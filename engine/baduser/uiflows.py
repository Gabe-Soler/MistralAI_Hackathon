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

import asyncio
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from .models import Channel, Finding, Manifest, Step, Verdict
from .prompts import SAFETY

_RESULT = re.compile(r"RESULT:\s*(PASS|FAIL)\b[ \t]*(.*)", re.IGNORECASE)

# Ceiling per flow. The agent already caps itself at WebAdapter.max_steps (12) browser
# actions, and each is an LLM call plus a page interaction, so a healthy flow lands well
# inside this. The timeout is for the unhealthy case: a login loop or a modal it cannot
# dismiss, where the agent burns its whole budget going nowhere.
FLOW_TIMEOUT = 180.0

_CONTRACT = """
End your report with exactly one line:
  RESULT: PASS
or
  RESULT: FAIL <one short sentence naming what stopped you>
Judge only whether you could COMPLETE the goal. Ugly, slow or oddly laid out is a PASS.
A page that errors, a control that does nothing, a form that will not submit, or a result
that never appears is a FAIL."""


class UiUnavailable(RuntimeError):
    """The browser could not start at all, so no flow was actually tested.

    Kept distinct from a broken flow because they are opposite claims. A broken flow says
    THE APP failed a person trying to use it. This says WE could not look. Reporting the
    second as the first is the false-clean failure in reverse: five BROKEN findings against
    an app whose UI was never opened, because a dependency of ours was missing.
    """


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
    timeout: float = FLOW_TIMEOUT,  # noqa: ASYNC109 - wait_for is the impl; same shape as EngineState.ask
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
            result = await asyncio.wait_for(adapter.act(step), timeout=timeout)
        except TimeoutError:
            result = None
            ok, reason = False, (f"the flow did not finish within {timeout:.0f}s -- the "
                                 "agent could not get through it")
        except ImportError as e:
            # Our dependency, not their bug. Abort the suite rather than emit a finding.
            raise UiUnavailable(str(e)) from e
        except Exception as e:
            if _is_missing_dependency(e):
                raise UiUnavailable(str(e)) from e
            result = None
            reason, ok = f"the browser could not run this flow: {e!r}"[:200], False
        else:
            if result.error:
                ok, reason = False, f"the browser could not run this flow: {result.error}"
            else:
                ok, reason = read_result(result.raw)

        shot = _shot_name(result)
        if emit is not None:
            emit(flow, "passed" if ok else "broken", reason, shot)
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
            shot=shot,
        ))
    return out


def _is_missing_dependency(e: BaseException) -> bool:
    """browser-use imports several of its own dependencies lazily, so a missing one
    surfaces mid-run as a plain exception rather than at construction."""
    text = f"{type(e).__name__}: {e}"
    return "No module named" in text or "ModuleNotFoundError" in text


def _shot_name(result: Any) -> str | None:
    """Basename only -- the route resolves it inside the run's shots dir, and a server
    filesystem path is useless to a browser."""
    path = getattr(result, "screenshot", None)
    return PurePath(path).name if path else None
