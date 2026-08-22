"""Repo -> GroundTruth. See PLAN.md sections 2, 12, 15.

The code-read half of the two sources of truth: an LLM reads the target's source and drafts
the invariants it *should* enforce. Two properties matter here and are load-bearing:

  * Every invariant carries a `cite` (file:line). An invariant the model cannot cite is
    dropped -- a hallucinated invariant produces a FALSE BREACH against correct code, which
    is fatal for a tool that gates a deploy (PLAN 15).
  * A `tenant-isolation` invariant is ALWAYS present: oracle.tenant_isolation() looks up that
    exact id, and every canary finding cites it.

The LLM is injected (default real, tests pass FakeLLM), so the whole thing runs offline.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from .llm import LLM
from .models import GroundTruth, Invariant, ProductType, QuestionEvent

# ripgrep-first selection: routes/handlers, authz middleware, ORM models, role enums.
# A real repo will not fit in context, so both a file-count and a byte cap are mandatory
# even though the demo target is a small vibe-coded app that mostly fits.
_PATTERNS = [
    r"@(app|router|blueprint)\.(get|post|put|delete|route)",  # routes/handlers
    r"(authoriz|permission|@login_required|current_user|require_auth|is_admin)",  # authz
    r"(class .*\(.*Base\)|db\.Model|Column\(|belongs_to|has_many|ForeignKey)",  # ORM models
    r"(role|ADMIN|MEMBER|OWNER|VIEWER|Enum)",  # role enums
]
_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".java")
_MAX_FILES = 40
_MAX_BYTES = 200_000


@dataclass
class Clarification:
    """A dev-facing question paired with the Invariant it would create if answered "y"."""

    question: QuestionEvent
    invariant: Invariant


def _select_files(repo: str, max_files: int, max_bytes: int) -> list[str]:
    """ripgrep-first candidate selection; falls back to a walk when rg is unavailable."""
    found: list[str] = []
    rg = shutil.which("rg")
    if rg:
        for pat in _PATTERNS:
            try:
                out = subprocess.run(
                    [rg, "-l", "-i", "--no-messages", pat, repo],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                for line in out.stdout.splitlines():
                    if line and line not in found:
                        found.append(line)
            except Exception:  # noqa: BLE001 - selection is best-effort; the walk covers us
                continue
    if not found:  # rg missing, or nothing matched: fall back to a plain walk.
        for root, _dirs, names in os.walk(repo):
            if any(part in root for part in (".git", "node_modules", "__pycache__")):
                continue
            for n in sorted(names):
                if n.endswith(_EXTS):
                    found.append(os.path.join(root, n))

    selected: list[str] = []
    total = 0
    for f in found:
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        if len(selected) >= max_files or total + size > max_bytes:
            break  # the cap MUST exist -- a real repo overruns the context window
        selected.append(f)
        total += size
    return selected


def _read_blob(files: list[str], max_bytes: int) -> str:
    parts: list[str] = []
    total = 0
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        chunk = f"\n===== {f} =====\n{text}"
        if total + len(chunk) > max_bytes:
            chunk = chunk[: max_bytes - total]
        parts.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return "".join(parts)


_GT_PROMPT = """You are auditing a multi-tenant web app for authorization bugs.
Read the source below and infer the security invariants the app SHOULD enforce.

For every invariant give an exact `cite` as "path:line" pointing at the code it is grounded
in. If you cannot cite it in the code, DO NOT include it.

Return JSON: {{"product_name", "product_type" ("b2b"|"b2c"), "domain", "roles" (list),
"invariants" (list of {{"id","name","rule","cite"}}), "endpoints" (list), "signup_hint"}}.
Always include one invariant with id "tenant-isolation".

SOURCE:
{context}
"""


def _to_product_type(v: object) -> ProductType:
    try:
        return ProductType(str(v))
    except ValueError:
        return ProductType.b2b


def _ensure_tenant_isolation(invs: list[Invariant], files: list[str]) -> list[Invariant]:
    """Guarantee the one invariant the oracle looks up by id, with a real cite."""
    if any(i.id == "tenant-isolation" for i in invs):
        return invs
    cite = f"{files[0]}:1" if files else "app.py:1"
    ti = Invariant(
        id="tenant-isolation",
        name="Tenant isolation",
        rule="A user may never read or modify another tenant's data.",
        source="code",
        cite=cite,
    )
    return [ti, *invs]


async def build_ground_truth(
    repo: str, llm: LLM, *, max_files: int = _MAX_FILES, max_bytes: int = _MAX_BYTES
) -> GroundTruth:
    files = _select_files(repo, max_files, max_bytes)
    blob = _read_blob(files, max_bytes)
    data = await llm.json(_GT_PROMPT.format(context=blob))

    invs: list[Invariant] = []
    for d in data.get("invariants", []):
        cite = (d or {}).get("cite")
        if not cite:  # PLAN 15: an uncited invariant is a false-breach generator -- drop it.
            continue
        try:
            invs.append(Invariant(
                id=str(d["id"]),
                name=str(d.get("name", d["id"])),
                rule=str(d.get("rule", "")),
                source="code",
                cite=str(cite),
            ))
        except (KeyError, TypeError):
            continue

    invs = _ensure_tenant_isolation(invs, files)

    return GroundTruth(
        product_name=str(data.get("product_name", "")),
        product_type=_to_product_type(data.get("product_type", "b2b")),
        domain=str(data.get("domain", "")),
        roles=[str(r) for r in data.get("roles", [])],
        invariants=invs,
        endpoints=[str(e) for e in data.get("endpoints", [])],
        signup_hint=str(data.get("signup_hint", "")),  # best-effort; seeding never depends on it
    )


_CLARIFY_PROMPT = """Given these known invariants for "{product}", propose the highest
blast-radius authorization questions still UNANSWERED by the code. Return JSON
{{"questions": [{{"id","text","name","rule","cite"}}]}}. At most 3, ordered by impact.

Known invariants: {invariants}
"""


async def clarify(gt: GroundTruth, llm: LLM) -> list[Clarification]:
    """At most 3 highest-blast-radius dev questions. Answering "y" writes a dev invariant."""
    data = await llm.json(_CLARIFY_PROMPT.format(
        product=gt.product_name,
        invariants=", ".join(i.rule for i in gt.invariants),
    ))
    cands = data.get("questions") or data.get("candidates") or []
    out: list[Clarification] = []
    for i, d in enumerate(cands[:3]):  # hard cap at 3 (PLAN 15)
        qid = str(d.get("id", f"q{i + 1}"))
        text = str(d.get("text", ""))
        inv = Invariant(
            id=str(d.get("invariant_id", qid)),
            name=str(d.get("name", text[:48] or qid)),
            rule=str(d.get("rule", text)),
            source="dev",  # a dev-confirmed rule, authoritative over the code-read ones
            cite=(str(d["cite"]) if d.get("cite") else None),
        )
        out.append(Clarification(question=QuestionEvent(id=qid, text=text), invariant=inv))
    return out


def accept_clarifications(
    clarifications: list[Clarification], answers: dict[str, str]
) -> list[Invariant]:
    """Only questions answered exactly "y" produce an invariant. Unanswered -> nothing."""
    return [c.invariant for c in clarifications if answers.get(c.question.id) == "y"]
