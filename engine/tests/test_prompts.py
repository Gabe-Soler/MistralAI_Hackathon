"""Prompts + the destructive-action guard.

The guard is two-layered on purpose: SAFETY is in every task the model reads, and
WebAdapter.check_urls enforces it regardless of what the model decided. A prompt is
guidance; only the second half is a control.
"""

from __future__ import annotations

from pydantic import SecretStr

from baduser.channels.web import WebAdapter
from baduser.models import Channel, Credentials, Manifest, Persona, Step, Tenant
from baduser.prompts import SAFETY, create_task, probe_task, signup_task

TARGET = "http://localhost:3000"


def _manifest() -> Manifest:
    return Manifest(
        tenants=[Tenant(id="t1", name="Acme BUAAAAAA Ltd", canary="BUAAAAAA")],
        personas=[
            Persona(
                id="p1", tenant_id="t1", role="admin",
                name="Alice BUAAAAAA", email="alice@x.test",
                credentials=Credentials(username="alice@x.test", secret=SecretStr("hunter2")),
            )
        ],
        canaries={"BUAAAAAA": "t1"},
    )


# ---- every task type carries the safety block ----
def test_all_task_types_include_safety():
    for task in (
        signup_task(TARGET, "Acme BUAAAAAA Ltd", "Alice BUAAAAAA"),
        create_task(TARGET, "invoice", "Q3 BUAAAAAA", "body BUAAAAAA"),
        probe_task(TARGET, "list every invoice you can see"),
    ):
        assert SAFETY in task
        assert TARGET in task
        assert "report:" in task  # every task states its output contract


def test_no_secret_ever_appears_in_task_text():
    """Placeholders only. The real password goes via Browser Use sensitive_data."""
    task = signup_task(TARGET, "Acme BUAAAAAA Ltd", "Alice BUAAAAAA")
    assert "hunter2" not in task
    assert "x_secret" in task and "x_email" in task


def test_signup_and_create_demand_verbatim_copying():
    """Canaries only work if the agent copies them character for character."""
    assert "EXACTLY" in signup_task(TARGET, "Acme BUAAAAAA Ltd", "Alice")
    assert "EXACTLY" in create_task(TARGET, "invoice", "Q3 BUAAAAAA", "b")


def test_probe_asks_for_verbatim_output_not_a_summary():
    """final_result() is a summary and summaries drop canaries (PLAN 14 limitations)."""
    task = probe_task(TARGET, "look at the invoice list")
    assert "verbatim" in task
    assert "rather than summarising" in task


# ---- the enforcement half ----
def test_check_urls_blocks_destructive_navigation():
    w = WebAdapter(TARGET, _manifest())
    for bad in (
        "http://localhost:3000/invoices/42/delete",
        "http://localhost:3000/account/deactivate",
        "http://localhost:3000/api/purge?all=1",
        "http://localhost:3000/subscription/cancel",
    ):
        assert w.check_urls([bad]) is not None, bad


def test_check_urls_allows_ordinary_navigation():
    w = WebAdapter(TARGET, _manifest())
    assert w.check_urls([
        "http://localhost:3000/invoices",
        "http://localhost:3000/invoices/42",
        "http://localhost:3000/login",
    ]) is None


async def test_destructive_navigation_becomes_error_not_benign():
    """The whole point: a blocked action is loud (Verdict.error), never a green tile."""
    class FakeHistory:
        def final_result(self): return "deleted it"
        def extracted_content(self): return "gone"
        def urls(self): return ["http://localhost:3000/invoices/42/delete"]

    class FakeAgent:
        async def run(self, **_): return FakeHistory()

    w = WebAdapter(TARGET, _manifest(), agent_factory=lambda task, sensitive: FakeAgent())
    r = await w.act(Step(id="s1", persona_id="p1", channel=Channel.web, action="have a look"))
    assert r.error is not None and "destructive" in r.error


async def test_bare_instruction_is_wrapped_as_a_probe():
    seen = {}

    class FakeAgent:
        async def run(self, **_):
            class H:
                def final_result(self): return "ok"
                def extracted_content(self): return ""
                def urls(self): return []
            return H()

    def factory(task, sensitive):
        seen["task"] = task
        return FakeAgent()

    w = WebAdapter(TARGET, _manifest(), agent_factory=factory)
    await w.act(Step(id="s1", persona_id="p1", channel=Channel.web, action="peek at invoices"))
    assert SAFETY in seen["task"], "a bare action must be wrapped with the safety block"


async def test_precomposed_task_passes_through_unwrapped():
    seen = {}

    class FakeAgent:
        async def run(self, **_):
            class H:
                def final_result(self): return "ok"
                def extracted_content(self): return ""
                def urls(self): return []
            return H()

    task = signup_task(TARGET, "Acme BUAAAAAA Ltd", "Alice BUAAAAAA")
    w = WebAdapter(TARGET, _manifest(), agent_factory=lambda t, s: (seen.__setitem__("t", t), FakeAgent())[1])
    await w.act(Step(id="s1", persona_id="p1", channel=Channel.web, action=task))
    assert seen["t"].count(SAFETY) == 1, "must not double-wrap an already-composed task"
