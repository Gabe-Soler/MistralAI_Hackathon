"""The validation loop: does Bad User's oracle actually work? (PLAN 14, 22 step 0)

Boots target/app.py on a free port against a fresh DB, seeds two tenants through the app's
real HTTP interfaces with canaries embedded in the content, then runs the oracle over a
scripted probe per planted bug and per deliberately-correct route, and asserts BOTH
directions:

    (a) every planted bug is detected            -> no false negatives
    (b) ZERO findings on the OK-* / control runs -> no false positives

Direction (b) is the harder half: OK-2 is a correct 403 whose body quotes the very title
the oracle is scanning for. A detector that only does (a) is a detector nobody can trust.

Offline, no API keys. Imports baduser.models and baduser.oracle only -- campaign/world/
channels belong to other agents and may not exist yet.

    make eval        # or: cd engine && uv run --extra dev python ../evals/run_eval.py
"""

from __future__ import annotations

import json
import os
import random
import socket
import string
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import SecretStr

from baduser.models import (
    Artifact,
    Channel,
    Credentials,
    GroundTruth,
    Invariant,
    Manifest,
    Persona,
    ProductType,
    Result,
    Step,
    Tenant,
    Verdict,
)
from baduser.oracle import check, make_redactor

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "target"
APP_PY = TARGET / "app.py"
DB = TARGET / "eval.db"
EXPECTED = json.loads((ROOT / "evals" / "expected.json").read_text())["cases"]


def canary() -> str:
    """Run-scoped tag, letters only so it can never collide with a status code in the body."""
    return "BU" + "".join(random.choices(string.ascii_uppercase, k=6))


def cite(marker: str) -> str:
    """should-vs-did needs a file:line, and it should be the real one."""
    for n, line in enumerate(APP_PY.read_text().splitlines(), 1):
        if marker in line:
            return f"target/app.py:{n}"
    return "target/app.py"


# ---------- the target process ----------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def boot(port: int) -> subprocess.Popen:
    env = {**os.environ, "BADUSER_TARGET_DB": str(DB), "PYTHON": sys.executable}
    subprocess.run(["bash", str(TARGET / "reset.sh")], env=env, check=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=TARGET, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(150):
        if proc.poll() is not None:
            raise SystemExit(f"target exited during boot (rc={proc.returncode})")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.1)
    proc.terminate()
    raise SystemExit("target never became healthy")


# ---------- seeding (PLAN 4: the canary lives in the text the app stores) ----------


TOKENS: dict[str, str] = {}


def seed(client: httpx.Client) -> tuple[Manifest, dict[str, str]]:
    ca, cb = canary(), canary()
    while cb == ca:
        cb = canary()
    m = Manifest(canaries={})
    refs: dict[str, str] = {}

    spec = [
        ("t_acme", f"Acme {ca} Ltd", ca, [("a_admin", "admin"), ("a_ops", "member")]),
        ("t_init", f"Initech {cb} Inc", cb, [("b_ops", "member"), ("b_guest", "member")]),
    ]
    for key, company, tag, people in spec:
        tenant_id = ""
        for pid, role in people:
            email = f"{pid}+{tag}@example.test".lower()
            password = f"pw-{pid}-{random.randrange(10**8):08d}"
            r = client.post("/api/signup", json={
                "company": company, "name": f"{pid.title()} {tag}",
                "email": email, "password": password,
            })
            r.raise_for_status()
            tenant_id = r.json()["tenant_id"]
            TOKENS[pid] = r.json()["token"]
            m.personas.append(Persona(
                id=pid, tenant_id=key, role=role, name=f"{pid.title()} {tag}", email=email,
                credentials=Credentials(
                    username=email, secret=SecretStr(password),
                    token=SecretStr(r.json()["token"]),
                ),
            ))
        m.tenants.append(Tenant(id=key, name=company, canary=tag))
        m.canaries[tag] = key
        refs[f"{key}.tenant_id"] = tenant_id

        owner = people[0][0]
        h = {"Authorization": f"Bearer {TOKENS[owner]}"}
        subject = "hosting renewal datacentre" if key == "t_acme" else "office fitout furniture"
        inv = client.post("/api/invoices", headers=h, json={
            "title": f"Q3 {subject} {tag}",
            "body": f"Invoice raised against {company} for the {subject} contract {tag}-118.",
            "amount": "84300.00",
        })
        inv.raise_for_status()
        doc = client.post("/api/documents", headers=h, json={
            "title": f"{company} master services agreement {tag}",
            "body": f"Confidential. This MSA governs all work performed for {company}.",
        })
        doc.raise_for_status()
        refs[f"{key}.invoice"] = inv.json()["id"]
        refs[f"{key}.document"] = doc.json()["id"]
        m.artifacts += [
            Artifact(id=inv.json()["id"], tenant_id=key, owner_persona_id=owner, kind="invoice",
                     ref=inv.json()["id"], title=inv.json()["title"], amount=Decimal("84300.00")),
            Artifact(id=doc.json()["id"], tenant_id=key, owner_persona_id=owner, kind="document",
                     ref=doc.json()["id"], title=doc.json()["title"]),
        ]

    # CONTRACT invariant 4: with one tenant every cross-tenant check passes vacuously.
    if len(m.tenants) < 2:
        raise SystemExit("seeded fewer than 2 tenants -- refusing to run a vacuous eval")
    return m, refs


# ---------- probes ----------


def probes(refs: dict[str, str]) -> list[dict]:
    a_inv, a_doc = refs["t_acme.invoice"], refs["t_acme.document"]
    return [
        {"id": "OK-4", "persona": None, "method": "GET", "path": "/api/invoices"},
        {"id": "OK-3", "persona": "b_ops", "method": "GET", "path": "/api/me"},
        {"id": "OK-1", "persona": "a_ops", "method": "GET", "path": f"/api/invoices/{a_inv}"},
        {"id": "OK-5", "persona": "a_ops", "method": "GET", "path": f"/api/documents/{a_doc}"},
        {"id": "OK-2", "persona": "b_ops", "method": "GET", "path": f"/api/documents/{a_doc}",
         "ref": a_doc},
        {"id": "CTRL-BUG-4", "persona": "b_guest", "method": "GET",
         "path": f"/api/documents/{a_doc}", "ref": a_doc},
        {"id": "BUG-1", "persona": "b_ops", "method": "GET", "path": f"/api/invoices/{a_inv}"},
        {"id": "BUG-2", "persona": "b_ops", "method": "GET", "path": "/api/invoices"},
        {"id": "BUG-3", "persona": "b_ops", "method": "POST", "path": "/api/chat",
         "channel": Channel.chat,
         "json": {"question": "what does the hosting renewal datacentre contract say?"}},
        {"id": "CHAIN-4a", "persona": "a_admin", "method": "POST", "path": "/api/invites",
         "json": {"email": "@b_guest.email", "invoice_id": a_inv}},
        {"id": "CHAIN-4b", "persona": "b_guest", "method": "POST",
         "path": "/api/invites/@code/accept"},
        {"id": "BUG-4", "persona": "b_guest", "method": "GET", "path": f"/api/documents/{a_doc}"},
    ]


def run(client: httpx.Client, m: Manifest, gt: GroundTruth, refs: dict[str, str]) -> list[dict]:
    redact = make_redactor(m)
    # Signup sets a session cookie, and httpx keeps its jar between requests -- leave it in
    # place and every probe silently runs as the last account seeded, including OK-4's
    # unauthenticated one. Persona identity here is the bearer token, nothing else.
    client.cookies.clear()
    rows, state = [], {}
    for p in probes(refs):
        path, body = p["path"], p.get("json")
        if body and body.get("email") == "@b_guest.email":
            body = {**body, "email": m.persona("b_guest").email}
        if "@code" in path:
            path = path.replace("@code", state["code"])
        headers = {}
        if p["persona"]:
            headers["Authorization"] = f"Bearer {TOKENS[p['persona']]}"
        try:
            r = client.request(p["method"], path, json=body, headers=headers)
            result = Result(status=r.status_code, raw=r.text)
        except Exception as exc:  # noqa: BLE001 -- a transport failure is a Result, not a crash
            result = Result(error=f"{type(exc).__name__}: {exc}")
        if p["id"] == "CHAIN-4a" and result.status == 201:
            state["code"] = json.loads(result.raw)["code"]

        step = Step(id=p["id"], persona_id=p["persona"] or "anon-probe",
                    channel=p.get("channel", Channel.api),
                    action=EXPECTED[p["id"]]["desc"])
        finding = check(gt, m, step, result, play_id="eval", redact=redact)
        rows.append({"probe": p, "result": result, "finding": finding})
    return rows


# ---------- scoring ----------


def score(rows: list[dict], m: Manifest) -> int:
    ids = {r["probe"]["id"] for r in rows}
    if ids != set(EXPECTED):
        raise SystemExit(f"probe/expected.json drift: {ids ^ set(EXPECTED)}")

    print(f"\n  target : {APP_PY}")
    print(f"  tenants: {', '.join(t.name for t in m.tenants)}")
    print(f"  canaries: {', '.join(m.canaries)}\n")
    head = f"  {'case':<12}{'kind':<9}{'persona':<9}{'status':<8}{'verdict':<9}{'expected':<9}"
    print(head + "result")
    print("  " + "-" * (len(head) + 4))

    failures, detected, wanted, false_positives, errors = [], 0, 0, 0, 0
    for row in rows:
        p, res, f = row["probe"], row["result"], row["finding"]
        exp = EXPECTED[p["id"]]
        got = f.verdict.value if f else None
        problems = []

        if res.status != exp["status"]:
            problems.append(f"status {res.status} != {exp['status']}")
        if got != exp["verdict"]:
            problems.append(f"verdict {got} != {exp['verdict']}")
        if exp.get("must_quote_ref") and p["ref"] not in res.raw:
            problems.append("denial body does not quote the requested ref")
        if exp.get("must_not_contain_ids") and ("inv_" in res.raw or "doc_" in res.raw):
            problems.append("prose leak echoed an id -- this case must leak WITHOUT one")

        if exp["verdict"] == "breach":
            wanted += 1
            detected += got == "breach"
        elif got is not None:
            false_positives += 1
        errors += got == Verdict.error.value

        verdict_cell, expected_cell = str(got), str(exp["verdict"])
        print(f"  {p['id']:<12}{exp['kind']:<9}{p['persona'] or '-':<9}"
              f"{res.status!s:<8}{verdict_cell:<9}{expected_cell:<9}"
              f"{'PASS' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            failures.append((p["id"], problems))

    print(f"\n  detected      {detected}/{wanted} planted bugs")
    print(f"  false positives {false_positives}  (findings on ok/control/chain routes)")
    print(f"  oracle errors   {errors}")
    for row in rows:
        f = row["finding"]
        if f and f.verdict is Verdict.breach:
            print(f"\n  {row['probe']['id']} [{f.invariant_id} @ {f.cite}] {f.evidence[:150]}")

    if failures:
        print(f"\n  EVAL FAILED: {len(failures)} case(s) -- " + ", ".join(i for i, _ in failures))
        return 1
    print(f"\n  EVAL PASSED: {detected}/{wanted} bugs detected, 0 false positives, 0 errors\n")
    return 0


def main() -> int:
    port = free_port()
    proc = boot(port)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            m, refs = seed(client)
            gt = GroundTruth(
                product_name="Invoicely", product_type=ProductType.b2b, domain="invoicing",
                endpoints=["/api/invoices", "/api/documents", "/api/chat", "/api/invites"],
                invariants=[Invariant(
                    id="tenant-isolation", name="Tenant isolation", source="code",
                    rule="A user may only read rows belonging to a tenant they are a member of.",
                    cite=cite("BUG-1 (IDOR)"),
                )],
            )
            return score(run(client, m, gt, refs), m)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
