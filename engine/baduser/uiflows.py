"""Does the UI actually work?

Every other check in this codebase asks one question: did data appear where it should not.
A page can be completely broken -- signup 500s, the create form never submits, the list
never renders -- and score `benign` on all of them, because nothing leaked. That is a real
blind spot, and it is the one a human notices first.

So these flows assert the opposite property: that a person can get through the app at all.
They drive the same browser the attack uses, but the verdict comes from whether the flow
COMPLETED, not from what came back in the body.

Written to be app-agnostic. Nothing here names a route, a selector or a field -- the agent
is told the goal and finds its own way, which is the only approach that survives an app the
tool has never seen (PLAN 22: the target is vibe-coded minutes before the run).

The contract with the agent is a single token. It must end its report with `RESULT: PASS`
or `RESULT: FAIL <reason>`, and anything else is treated as a failure to complete -- an
agent that wanders off and never answers is exactly the case where a UI is unusable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .models import Channel, Finding, Manifest, Step, Verdict
from .prompts import SAFETY

_RESULT = re.compile(r"RESULT:\s*(PASS|FAIL)\b[ \t]*(.*)", re.IGNORECASE)

_CONTRACT = """
End your report with exactly one line:
  RESULT: PASS
or
  RESULT: FAIL <one short sentence naming what stopped you>
Judge only whether you could COMPLETE the goal. Ugly, slow or oddly laid out is a PASS.
A page that errors, a control that does nothing, a form that will not submit, or a result
that never appears is a FAIL."""


@dataclass(frozen=True)
class Flow:
    id: str
    title: str
    goal: str
    #: True when the flow needs an account, so it is skipped if seeding produced none.
    needs_persona: bool = True


# Ordered: later flows assume earlier ones worked, which also makes the first failure the
# informative one rather than a cascade of them.
FLOWS: tuple[Flow, ...] = (
    Flow("ui-signup", "Sign up through the interface",
         "Create a brand new account. Find the sign up or register link and complete "
         "whatever the form asks for. Use the email and password given below.",
         needs_persona=True),
    Flow("ui-login", "Log in through the interface",
         "Log in with the email and password below. If you are already signed in, sign "
         "out first, then log back in.",
         needs_persona=True),
    Flow("ui-create", "Create a record through the interface",
         "You are signed in. Create one new record of whatever kind this app manages -- "
         "an invoice, a document, a project, whatever its main object is. Fill in every "
         "required field with plausible values and save it.",
         needs_persona=True),
    Flow("ui-list", "The new record is visible afterwards",
         "You are signed in. Find the list or dashboard showing the records you own, and "
         "confirm at least one record is displayed there with its details readable.",
         needs_persona=True),
    Flow("ui-auth-gate", "Signed-out visitors are kept out",
         "Sign out if you are signed in. Then try to reach the main dashboard or records "
         "page directly without logging in. PASS only if the app keeps you out -- a login "
         "screen, a redirect, or an access-denied message. FAIL if you can see any "
         "records or account data while signed out.",
         needs_persona=False),
)


def build_task(target: str, flow: Flow) -> str:
    """A pre-composed task. WebAdapter.compose passes anything containing SAFETY through
    untouched, which is what keeps probe_task's 'look only' rule from blocking a flow that
    is supposed to submit a form."""
    return "\n\n".join([
        f"You are using the web app at {target}.",
        flow.goal,
        _CONTRACT,
        SAFETY,
    ])


def read_result(raw: str) -> tuple[bool, str]:
    """(passed, reason). No verdict line means the agent never reached a conclusion."""
    m = _RESULT.search(raw or "")
    if m is None:
        return False, "the agent never reported a result; the flow did not complete"
    ok = m.group(1).upper() == "PASS"
    return ok, (m.group(2) or "").strip()[:200]


async def run_flows(
    adapter: Any,
    manifest: Manifest,
    target: str,
    *,
    emit: Any = None,
    redact: Any = None,
    flows: tuple[Flow, ...] = FLOWS,
) -> list[Finding]:
    """Run each flow once. Returns a Finding per BROKEN flow; passes produce nothing."""
    redact = redact or (lambda t: t)
    people = [p for p in manifest.personas if not p.control]
    out: list[Finding] = []

    for flow in flows:
        if flow.needs_persona and not people:
            continue
        # A fresh persona per flow so one flow's session cannot mask another's failure.
        persona = people[len(out) % len(people)] if people else None
        pid = persona.id if (persona and flow.needs_persona) else "anonymous"

        step = Step(id=f"ui-{flow.id}", persona_id=pid, channel=Channel.web,
                    action=build_task(target, flow))
        if emit is not None:
            emit(flow, "started", "")

        try:
            result = await adapter.act(step)
        except Exception as e:  # noqa: BLE001 - one broken flow must not end the suite
            result = None
            reason, ok = f"the browser could not run this flow: {e!r}"[:200], False
        else:
            if result.error:
                ok, reason = False, f"the browser could not run this flow: {result.error}"
            else:
                ok, reason = read_result(result.raw)

        if emit is not None:
            emit(flow, "passed" if ok else "broken", reason)
        if ok:
            continue

        out.append(Finding(
            id=str(uuid4())[:8],
            play_id="ui-flows",
            persona_id=pid,
            channel=Channel.web,
            action=flow.title,
            verdict=Verdict.broken,
            invariant_id=flow.id,
            evidence=redact((result.raw if result else "")[:400]),
            rationale=reason,
        ))
    return out
