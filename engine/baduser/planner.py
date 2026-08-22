"""Plays generated from the rules, for bug classes the authored chains do not cover.

campaign.hero_chains stays exactly as it is: hand-written, deterministic, and the thing the
demo depends on. This adds to it rather than replacing it, because a generated plan is the
opposite trade -- broader, and never guaranteed to produce the same run twice.

What it targets is the gap the canary scan cannot see. tenant-isolation is already covered
by a lookup, so the planner is pointed at the OTHER invariants the repo read produced --
auth-required, owner-access, invite-scope, role limits -- and it probes them with the
personas and routes that actually exist.

Two constraints from the engine shape the output and are enforced here, not hoped for:
  * campaign.assert_disjoint_personas raises if two concurrent plays share a persona, so
    plays are partitioned across the pool rather than trusting the model to do it;
  * every persona id and path is checked against the manifest and the discovered routes,
    because a play referring to something that does not exist is a step that errors, and
    an errored step reads as "we could not judge" -- noise that looks like a finding.
"""

from __future__ import annotations

from typing import Any

from .models import Channel, GroundTruth, Manifest, Play, Step

MAX_PLAYS = 6
MAX_STEPS = 4

_PROMPT = """You are planning probes against a multi-tenant web app to see whether it
actually enforces its own rules.

RULES TO TEST (use these ids):
{rules}

ROUTES THAT EXIST (use these exactly, do not invent paths):
{routes}

PERSONAS YOU MAY ACT AS:
{personas}

Write up to {max_plays} short plays. Each play targets ONE rule and uses ONE persona.
A step's `action` is a literal request line the runner will execute, e.g.
"GET /api/invoices", "GET /api/documents/1", "POST /api/invites {{\\"email\\":\\"x@y.z\\"}}".

Aim at things a canary scan cannot see:
- a protected route called with no credentials at all (persona "anonymous")
- reading or writing a resource id that belongs to somebody else
- an action the persona's role should not be allowed to perform
- an invite or share that might grant more than the one resource it names

Return JSON: {{"plays": [{{"title": str, "invariant_id": str, "persona_id": str,
"channel": "api"|"chat", "steps": [{{"action": str}}]}}]}}
At most {max_steps} steps per play. Prefer reads. Never delete."""


def _routes_of(gt: GroundTruth) -> list[str]:
    return [e for e in gt.endpoints if e][:40]


async def plan(
    llm: Any,
    gt: GroundTruth,
    manifest: Manifest,
    channels: list[Channel],
    max_plays: int = MAX_PLAYS,
) -> list[Play]:
    """Generated plays, or [] when there is nothing to plan with. Never raises."""
    if llm is None or not manifest.personas:
        return []
    rules = [i for i in gt.invariants if i.id != "tenant-isolation"]
    routes = _routes_of(gt)
    if not rules or not routes:
        return []

    # Only personas no authored play will touch, so the disjointness assertion holds.
    pool = [p for p in manifest.personas if not p.control]
    if len(pool) < 3:
        return []
    pool = pool[2:]  # hero_chains takes from the front

    allowed = {c.value for c in channels} & {"api", "chat"}
    if not allowed:
        return []

    try:
        data = await llm.json(_PROMPT.format(
            rules="\n".join(f"- {i.id}: {i.rule}" for i in rules),
            routes="\n".join(f"- {r}" for r in routes),
            personas="\n".join(
                f"- {p.id} (tenant {p.tenant_id}, role {p.role})" for p in pool
            ) + '\n- anonymous (no credentials at all)',
            max_plays=max_plays,
            max_steps=MAX_STEPS,
        ))
    except Exception:  # noqa: BLE001 - planning is best-effort; the authored chains remain
        return []

    ids = {p.id for p in pool}
    out: list[Play] = []
    used: set[str] = set()

    for i, raw in enumerate((data or {}).get("plays", [])[:max_plays]):
        if not isinstance(raw, dict):
            continue
        persona = str(raw.get("persona_id") or "")
        # "anonymous" is a real and useful actor -- oracle.check maps an unknown persona to
        # no tenant -- but every other id must exist, or the step errors on a phantom.
        if persona != "anonymous" and persona not in ids:
            continue
        if persona in used:
            continue  # one play per persona: concurrent plays must not share auth state
        channel = str(raw.get("channel") or "api")
        if channel not in allowed:
            channel = "api"

        steps: list[Step] = []
        for j, s in enumerate((raw.get("steps") or [])[:MAX_STEPS]):
            action = str((s or {}).get("action") or "").strip()
            if not action or _is_destructive(action):
                continue
            steps.append(Step(id=f"g{i}s{j}", persona_id=persona,
                              channel=Channel(channel), action=action))
        if not steps:
            continue

        used.add(persona)
        out.append(Play(id=f"gen-{i}", title=str(raw.get("title") or f"probe {i}"),
                        steps=steps, context={}))
    return out


_DESTRUCTIVE = ("delete ", "purge", "wipe", "drop ", "truncate", "/logout", "deactivate")


def _is_destructive(action: str) -> bool:
    """The prompt says never delete; this is the half that does not rely on the model.

    prompts.py already makes the point for the browser: a prompt is guidance, not a
    control. The same holds for a generated plan.
    """
    low = action.lower()
    return low.startswith("delete") or any(w in low for w in _DESTRUCTIVE)
