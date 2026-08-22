"""Inference must never be able to impersonate a proof.

The canary scan is a lookup: we planted the data, so finding it where it cannot legally be
is certain. The judge and the planner are the opposite -- they widen coverage to bug classes
the scan is blind to, at the cost of being able to be wrong. These tests pin the boundary
between the two, because collapsing it is the one failure that would discredit the tool.
"""

from __future__ import annotations

from pydantic import SecretStr

from baduser.judge import judge_step
from baduser.llm import FakeLLM
from baduser.models import (
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
)
from baduser.planner import plan

GT = GroundTruth(
    product_name="X", product_type=ProductType.b2b,
    endpoints=["GET /api/invoices", "GET /api/me"],
    invariants=[
        Invariant(id="tenant-isolation", name="T", rule="own tenant only", source="code"),
        Invariant(id="auth-required", name="Auth", rule="protected routes need creds",
                  source="code", cite="app.py:85"),
    ],
)
STEP = Step(id="s1", persona_id="p1", channel=Channel.api, action="GET /api/invoices")
OK = Result(status=200, raw='[{"id":1}]')


def _manifest(n: int = 6) -> Manifest:
    return Manifest(
        tenants=[Tenant(id="t1", name="A", canary="BUAAAAAA")],
        personas=[
            Persona(id=f"p{i}", tenant_id="t1", role="member", name=f"N{i}",
                    email=f"p{i}@x.test",
                    credentials=Credentials(username=f'p{i}@x.test',
                                           secret=SecretStr('pw123456')))
            for i in range(n)
        ],
    )


# ---------- the judge ----------


async def test_a_violation_is_reported_with_its_rule() -> None:
    llm = FakeLLM(json={"violated": True, "invariant_id": "auth-required",
                        "confidence": 0.9, "rationale": "served without credentials"})

    got = await judge_step(llm, GT, STEP, OK, "t1")

    assert got == ("auth-required", "served without credentials")


async def test_an_invented_rule_id_is_refused() -> None:
    """The hallucinated-invariant failure PLAN 15 calls fatal. It must not survive."""
    llm = FakeLLM(json={"violated": True, "invariant_id": "rule-i-made-up",
                        "confidence": 0.99, "rationale": "trust me"})

    assert await judge_step(llm, GT, STEP, OK, "t1") is None


async def test_low_confidence_is_dropped_not_downgraded() -> None:
    llm = FakeLLM(json={"violated": True, "invariant_id": "auth-required",
                        "confidence": 0.3, "rationale": "maybe"})

    assert await judge_step(llm, GT, STEP, OK, "t1") is None


async def test_no_llm_means_no_judgement() -> None:
    """Every run without a key stays canary-only, exactly as before."""
    assert await judge_step(None, GT, STEP, OK, "t1") is None


async def test_a_raising_llm_does_not_fail_the_step() -> None:
    class Boom:
        async def json(self, *a, **k):
            raise RuntimeError("429")

    assert await judge_step(Boom(), GT, STEP, OK, "t1") is None


# ---------- the planner ----------


async def test_generated_plays_use_only_real_personas() -> None:
    """A play naming a phantom persona is a step that errors, and an errored step reads as
    'we could not judge' -- noise shaped exactly like a finding."""
    llm = FakeLLM(json={"plays": [
        {"title": "ghost", "invariant_id": "auth-required", "persona_id": "nobody",
         "channel": "api", "steps": [{"action": "GET /api/invoices"}]},
        {"title": "real", "invariant_id": "auth-required", "persona_id": "p3",
         "channel": "api", "steps": [{"action": "GET /api/me"}]},
    ]})

    plays = await plan(llm, GT, _manifest(), [Channel.api])

    assert [p.title for p in plays] == ["real"]


async def test_anonymous_is_allowed_because_it_is_the_point() -> None:
    llm = FakeLLM(json={"plays": [
        {"title": "no creds", "invariant_id": "auth-required", "persona_id": "anonymous",
         "channel": "api", "steps": [{"action": "GET /api/invoices"}]},
    ]})

    (play,) = await plan(llm, GT, _manifest(), [Channel.api])

    assert play.steps[0].persona_id == "anonymous"


async def test_two_plays_never_share_a_persona() -> None:
    """campaign.assert_disjoint_personas raises on a shared persona -- concurrent plays
    would corrupt each other's auth state."""
    llm = FakeLLM(json={"plays": [
        {"title": "a", "persona_id": "p3", "channel": "api",
         "steps": [{"action": "GET /api/me"}]},
        {"title": "b", "persona_id": "p3", "channel": "api",
         "steps": [{"action": "GET /api/invoices"}]},
    ]})

    plays = await plan(llm, GT, _manifest(), [Channel.api])

    assert len(plays) == 1


async def test_destructive_actions_are_stripped() -> None:
    """The prompt says never delete; this is the half that does not trust the model."""
    llm = FakeLLM(json={"plays": [
        {"title": "wreck", "persona_id": "p3", "channel": "api", "steps": [
            {"action": "DELETE /api/invoices/1"},
            {"action": "GET /api/invoices"},
        ]},
    ]})

    (play,) = await plan(llm, GT, _manifest(), [Channel.api])

    assert [s.action for s in play.steps] == ["GET /api/invoices"]


async def test_a_disabled_channel_is_not_used() -> None:
    llm = FakeLLM(json={"plays": [
        {"title": "chatty", "persona_id": "p3", "channel": "chat",
         "steps": [{"action": "ask about invoices"}]},
    ]})

    (play,) = await plan(llm, GT, _manifest(), [Channel.api])

    assert play.steps[0].channel is Channel.api  # coerced, not silently dropped


async def test_no_llm_means_no_generated_plays() -> None:
    assert await plan(None, GT, _manifest(), [Channel.api]) == []
