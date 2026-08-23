"""A broken UI must not read as a healthy app.

Every other check answers "did data leak". A page that 500s, a form that never submits, a
list that never renders -- all of those leak nothing and score benign. These flows exist
to catch exactly that, so the thing to pin is that a flow which does NOT complete is
reported, including when the agent fails in an unhelpful way.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from baduser.models import Channel, Credentials, Manifest, Persona, Result, Tenant, Verdict
from baduser.prompts import SAFETY
from baduser.uiflows import (
    FLOWS,
    Flow,
    UiUnavailable,
    build_task,
    read_result,
    run_flows,
)


def _manifest() -> Manifest:
    return Manifest(
        tenants=[Tenant(id="t1", name="A", canary="BUAAAAAA")],
        personas=[Persona(id="p1", tenant_id="t1", role="member", name="N",
                          email="p1@x.test",
                          credentials=Credentials(username="p1@x.test",
                                                  secret=SecretStr("pw123456")))],
    )


class FakeWeb:
    """Stands in for the browser. `replies` is consumed one per flow."""

    def __init__(self, *replies: str | Exception) -> None:
        self._replies = list(replies)
        self.tasks: list[str] = []

    async def act(self, step):
        self.tasks.append(step.action)
        r = self._replies.pop(0) if self._replies else "RESULT: PASS"
        if isinstance(r, Exception):
            raise r
        if r.startswith("ERROR:"):
            return Result(error=r[6:], raw="")
        return Result(status=200, raw=r)


# ---------- reading the agent's verdict ----------


def test_pass_and_fail_are_read_from_the_contract_line() -> None:
    assert read_result("blah\nRESULT: PASS")[0] is True
    ok, why = read_result("RESULT: FAIL the save button did nothing")
    assert ok is False
    assert why == "the save button did nothing"


def test_no_verdict_line_is_a_failure_not_a_pass() -> None:
    """An agent that wanders off and never concludes IS the unusable-UI case. Defaulting
    to pass here would turn the one signal this suite has into a rubber stamp."""
    ok, why = read_result("I looked at the page and it was quite nice really")

    assert ok is False
    assert "never reported" in why


def test_a_pass_mentioning_the_word_fail_is_still_read_correctly() -> None:
    assert read_result("the form showed 'do not fail' text\nRESULT: PASS")[0] is True


# ---------- the task ----------


def test_the_task_carries_safety_so_it_is_not_rewrapped_as_a_probe() -> None:
    """WebAdapter.compose passes a task containing SAFETY through untouched. Without it the
    task becomes a probe_task, whose 'do not submit anything' rule would block every flow
    that has to submit a form."""
    task = build_task("http://x", FLOWS[0])

    assert SAFETY in task
    assert "RESULT: PASS" in task


# ---------- the suite ----------


async def test_a_passing_suite_produces_no_findings() -> None:
    web = FakeWeb(*["RESULT: PASS"] * len(FLOWS))

    assert await run_flows(web, _manifest(), "http://x") == []


async def test_a_broken_flow_is_reported_with_its_reason() -> None:
    flows = (Flow("ui-create", "Create a record", "make one"),)
    web = FakeWeb("RESULT: FAIL the save button did nothing")

    (f,) = await run_flows(web, _manifest(), "http://x", flows=flows)

    assert f.verdict is Verdict.broken
    assert f.invariant_id == "ui-create"
    assert f.channel is Channel.web
    assert "save button" in f.rationale


async def test_a_browser_crash_is_a_broken_flow_not_a_lost_one() -> None:
    flows = (Flow("ui-create", "Create a record", "make one"),)
    web = FakeWeb(RuntimeError("chrome died"))

    (f,) = await run_flows(web, _manifest(), "http://x", flows=flows)

    assert f.verdict is Verdict.broken
    assert "could not run" in f.rationale


async def test_one_broken_flow_does_not_end_the_suite() -> None:
    flows = (Flow("a", "A", "x"), Flow("b", "B", "y"), Flow("c", "C", "z"))
    web = FakeWeb("RESULT: FAIL nope", RuntimeError("boom"), "RESULT: PASS")

    found = await run_flows(web, _manifest(), "http://x", flows=flows)

    assert [f.invariant_id for f in found] == ["a", "b"]
    assert len(web.tasks) == 3  # every flow was attempted


async def test_a_hung_flow_is_bounded_and_reported() -> None:
    """campaign.TIMEOUTS only bounds steps run through run_step, and these call the adapter
    directly -- so without this the suite had no ceiling and a wedged browser hung the run
    forever, which is worse than any timeout value."""
    import asyncio

    class Hangs:
        async def act(self, step):
            await asyncio.sleep(60)

    flows = (Flow("ui-create", "Create a record", "make one"),)

    (f,) = await run_flows(Hangs(), _manifest(), "http://x", flows=flows, timeout=0.05)

    assert f.verdict is Verdict.broken
    assert "did not finish" in f.rationale


async def test_a_broken_flow_carries_its_frame() -> None:
    """A screenshot of the page it broke on IS the evidence for a usability failure."""
    class WithShot:
        async def act(self, step):
            return Result(status=200, raw="RESULT: FAIL nothing happened",
                          screenshot="/abs/path/.baduser/runs/r1/shots/ui-create.png")

    flows = (Flow("ui-create", "Create a record", "make one"),)

    (f,) = await run_flows(WithShot(), _manifest(), "http://x", flows=flows)

    assert f.shot == "ui-create.png"  # basename: a server path is useless to a browser


async def test_the_signed_out_flow_runs_without_an_account() -> None:
    flows = (Flow("ui-auth-gate", "Gate", "stay out", needs_persona=False),)

    (f,) = await run_flows(FakeWeb("RESULT: FAIL saw the dashboard"),
                           Manifest(), "http://x", flows=flows)

    assert f.persona_id == "anonymous"


async def test_a_missing_browser_dependency_is_not_the_app_s_fault() -> None:
    """The false-clean failure in reverse. A run once reported five BROKEN flows -- a
    verdict about the TARGET -- when the truth was that browser-use could not import and
    no page was ever opened. 'We could not look' and 'the app failed a user' are opposite
    claims, and only one of them is about the product."""
    class NoBrowser:
        async def act(self, step):
            raise ModuleNotFoundError("No module named 'pydantic_settings'")

    with pytest.raises(UiUnavailable):
        await run_flows(NoBrowser(), _manifest(), "http://x")


async def test_a_lazy_import_failure_is_caught_too() -> None:
    """browser-use imports several of its own dependencies lazily, so the failure arrives
    mid-run as a plain exception rather than at construction."""
    class LazyBoom:
        async def act(self, step):
            raise RuntimeError("worker failed: No module named 'playwright'")

    with pytest.raises(UiUnavailable):
        await run_flows(LazyBoom(), _manifest(), "http://x")


async def test_a_real_browser_crash_is_still_the_app_s_problem() -> None:
    """Only a MISSING DEPENDENCY is ours. A browser that starts and then dies is a flow a
    user could not complete, and must stay reportable."""
    flows = (Flow("ui-create", "Create a record", "make one"),)

    (f,) = await run_flows(FakeWeb(RuntimeError("chrome crashed")), _manifest(),
                           "http://x", flows=flows)

    assert f.verdict is Verdict.broken
