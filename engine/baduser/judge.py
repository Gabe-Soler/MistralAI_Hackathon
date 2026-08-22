"""Judging a response against the rules, for everything the canary scan cannot see.

The canary scan answers one question with certainty: did another tenant's planted data
come back? That is a lookup, and it is why `breach` can be trusted. But it is blind to
every other way an app breaks -- an endpoint that ignores auth and returns an empty list,
a role check that never runs, an invite that grants more than it promised, a write that
succeeds where it should have been refused. None of those contain a foreign canary.

So this asks a model instead, and the honesty of the whole tool depends on keeping the two
apart. A judgement is `Verdict.suspected`, never `breach`; it is not counted in the breach
total, not used for the --ci exit code, and rendered differently. PLAN 15's warning is the
reason: a hallucinated invariant produces a FALSE BREACH against correct code, which is
fatal for something that gates a deploy.

Three guards keep it from inventing findings:
  * it may only cite an invariant id that already exists -- no invented rules;
  * it is never asked about a step the app refused (looks_like_denial runs first) or one
    that already produced a canary breach;
  * a low-confidence answer is dropped rather than downgraded.
"""

from __future__ import annotations

from typing import Any

from .models import GroundTruth, Result, Step

_MAX_BODY = 2000
_MIN_CONFIDENCE = 0.7

_PROMPT = """You are auditing one HTTP interaction against an app's security rules.

RULES (cite only these ids, never invent one):
{rules}

WHO MADE THE REQUEST: persona {persona} of tenant {tenant}, over the {channel} channel.
REQUEST: {action}
STATUS: {status}
RESPONSE BODY (truncated):
{body}

Did this response BREAK one of the rules above?

Judge only what you can see. Be strict about the difference between a response that is
merely uninteresting and one that is actually wrong:
- An empty list, a 4xx, or a refusal is CORRECT behaviour. Not a violation.
- Data belonging to the requester is CORRECT. Not a violation.
- A violation means the app did something a rule forbids: returned another party's data,
  served a protected route without valid credentials, allowed an action the requester's
  role should not permit, or granted access wider than a rule allows.

Return JSON: {{"violated": bool, "invariant_id": str or null, "confidence": 0.0-1.0,
"rationale": "one sentence naming what the app did wrong"}}.
If nothing is clearly wrong, return violated=false. Saying "no" is the common answer and
is always safer than guessing."""


async def judge_step(
    llm: Any,
    gt: GroundTruth,
    step: Step,
    result: Result,
    tenant_id: str | None,
) -> tuple[str, str] | None:
    """Return (invariant_id, rationale) when a rule was clearly broken, else None."""
    if llm is None:
        return None
    rules = [i for i in gt.invariants if i.id != "tenant-isolation"] or list(gt.invariants)
    if not rules:
        return None

    prompt = _PROMPT.format(
        rules="\n".join(f"- {i.id}: {i.rule}" for i in rules),
        persona=step.persona_id,
        tenant=tenant_id or "anonymous",
        channel=step.channel.value,
        action=step.action[:400],
        status=result.status,
        body=(result.raw or "")[:_MAX_BODY] or "(empty)",
    )

    try:
        data = await llm.json(prompt)
    except Exception:  # noqa: BLE001 - a judge that fails must not fail the step
        return None
    if not isinstance(data, dict) or not data.get("violated"):
        return None

    inv_id = str(data.get("invariant_id") or "")
    # Only a rule that actually exists. A model naming something not in the list is
    # exactly the hallucinated-invariant failure this design is built to avoid.
    if not any(i.id == inv_id for i in rules):
        return None

    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return None

    rationale = str(data.get("rationale") or "").strip()[:300]
    return (inv_id, rationale or "judged to violate this rule")
