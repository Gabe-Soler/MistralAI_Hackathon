"""Campaign loop tests. Offline: tiny local fakes, no network, no channels/fake.py import."""

from __future__ import annotations

import asyncio

import pytest

from baduser import campaign
from baduser.campaign import Ctx, assert_disjoint_personas, hero_chains, run_play
from baduser.models import (
    Channel,
    Credentials,
    GroundTruth,
    Invariant,
    Manifest,
    Persona,
    Play,
    Result,
    Step,
    Tenant,
    Verdict,
)

CANARY_A = "CANARYAAA111"
CANARY_B = "CANARYBBB222"


# ---------- local fakes ----------


class FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, ev):
        self.events.append(ev)

    def of(self, type_):
        return [e for e in self.events if isinstance(e, type_)]


class FakeAdapter:
    """Driven by responder(step) -> Result | raises | coroutine. Records call order."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[Step] = []

    async def act(self, step: Step) -> Result:
        self.calls.append(step)
        r = self.responder(step)
        if asyncio.iscoroutine(r):
            r = await r
        return r


def _cred(name):
    return Credentials(username=name, secret="pw-" + name + "-secret")


def make_manifest():
    ta = Tenant(id="ta", name="Acme", canary=CANARY_A)
    tb = Tenant(id="tb", name="Globex", canary=CANARY_B)
    personas = [
        Persona(id="alice", tenant_id="ta", role="admin", name="Alice",
                email="alice@acme.test", credentials=_cred("alice")),
        Persona(id="amember", tenant_id="ta", role="member", name="Amy",
                email="amy@acme.test", credentials=_cred("amy")),
        Persona(id="bob", tenant_id="tb", role="member", name="Bob",
                email="bob@globex.test", credentials=_cred("bob")),
        Persona(id="bmember", tenant_id="tb", role="member", name="Ben",
                email="ben@globex.test", credentials=_cred("ben")),
        # pre-seeded controls, one per tenant, never used by a play
        Persona(id="ctrl_a", tenant_id="ta", role="member", name="CtrlA",
                email="ctrla@acme.test", credentials=_cred("ctrla"), control=True),
        Persona(id="ctrl_b", tenant_id="tb", role="member", name="CtrlB",
                email="ctrlb@globex.test", credentials=_cred("ctrlb"), control=True),
    ]
    return Manifest(
        tenants=[ta, tb],
        personas=personas,
        canaries={CANARY_A: "ta", CANARY_B: "tb"},
    )


def make_gt():
    return GroundTruth(
        product_name="Test",
        product_type="b2b",
        invariants=[Invariant(id="tenant-isolation", name="Tenant isolation",
                              rule="no cross-tenant reads", source="code", cite="app.py:1")],
    )


def make_ctx(manifest, adapters, bus=None):
    async def noop_throttle():
        return None

    return Ctx(
        gt=make_gt(),
        manifest=manifest,
        bus=bus or FakeBus(),
        adapters=adapters,
        findings=[],
        redact=lambda s: s,
        throttle=noop_throttle,
        control_persona=campaign.make_control_persona(manifest),
    )


def breach_raw(canary):
    return f"Here is the record: name Acme Corp invoice total 84300 tag {canary}"


DENIAL = Result(status=403, raw="access denied: you are not authorized")


# ---------- tests ----------


async def test_compound_true_when_chain_breaches_but_isolated_step_is_benign():
    """chained-breach + isolated-benign => compound True."""
    m = make_manifest()

    def responder(step: Step) -> Result:
        # The final chat step: breaches for the attacker (bob), but the control persona
        # (ctrl_b, who ran no setup) is correctly denied.
        if step.channel is Channel.chat:
            if step.persona_id == "bob":
                return Result(status=200, raw=breach_raw(CANARY_A))
            return DENIAL  # control persona -> benign
        return Result(status=200, raw="ok, done")  # benign setup steps

    ctx = make_ctx(m, {Channel.api: FakeAdapter(responder),
                       Channel.chat: FakeAdapter(responder)})
    play = Play(id="p", title="compound", compound=True, context={}, steps=[
        Step(id="s1", persona_id="alice", channel=Channel.api, action="create project"),
        Step(id="s2", persona_id="alice", channel=Channel.api, action="invite bob"),
        Step(id="s3", persona_id="bob", channel=Channel.api, action="accept"),
        Step(id="s4", persona_id="bob", channel=Channel.chat, action="list invoices"),
    ])
    chain = await run_play(play, ctx)

    assert chain is not None
    assert chain.compound is True
    assert chain.verdict is Verdict.breach
    assert chain.control_verdict is Verdict.benign
    assert len(chain.steps) == 4  # the StepFinished it emitted, in order


async def test_compound_false_when_isolated_step_also_breaches():
    """both breach => compound False (ordinary bug with ceremony in front)."""
    m = make_manifest()

    def responder(step: Step) -> Result:
        # Final step leaks regardless of who asks -> control also breaches.
        if step.channel is Channel.chat:
            return Result(status=200, raw=breach_raw(CANARY_A))
        return Result(status=200, raw="ok")

    ctx = make_ctx(m, {Channel.api: FakeAdapter(responder),
                       Channel.chat: FakeAdapter(responder)})
    play = Play(id="p", title="not compound", compound=True, context={}, steps=[
        Step(id="s1", persona_id="alice", channel=Channel.api, action="create project"),
        Step(id="s4", persona_id="bob", channel=Channel.chat, action="list invoices"),
    ])
    chain = await run_play(play, ctx)

    assert chain is not None
    assert chain.verdict is Verdict.breach
    assert chain.compound is False
    assert chain.control_verdict is Verdict.breach


async def test_step_raising_becomes_error_not_benign():
    m = make_manifest()

    def responder(step: Step) -> Result:
        raise RuntimeError("adapter blew up")

    bus = FakeBus()
    ctx = make_ctx(m, {Channel.api: FakeAdapter(responder)}, bus=bus)
    play = Play(id="p", title="boom", compound=True, context={}, steps=[
        Step(id="s1", persona_id="bob", channel=Channel.api, action="fetch"),
    ])
    await run_play(play, ctx)

    from baduser.models import StepFinished
    finished = bus.of(StepFinished)
    assert finished[0].verdict is Verdict.error
    assert finished[0].verdict is not Verdict.benign
    assert ctx.findings and ctx.findings[0].verdict is Verdict.error


async def test_template_resolution_feeds_s2_value_into_s4():
    m = make_manifest()
    adapter = None

    def responder(step: Step) -> Result:
        if step.id == "s2":
            return Result(status=200, raw="created", extracted={"org_id": "ORG-XYZ-9"})
        return Result(status=200, raw="ok")

    adapter = FakeAdapter(responder)
    ctx = make_ctx(m, {Channel.api: adapter, Channel.chat: adapter})
    play = Play(id="p", title="feed", compound=True, context={}, steps=[
        Step(id="s1", persona_id="alice", channel=Channel.api, action="create"),
        Step(id="s2", persona_id="alice", channel=Channel.api, action="invite"),
        Step(id="s3", persona_id="bob", channel=Channel.api,
             action="accept org {{s2.org_id}}", target_ref="{{s2.org_id}}"),
        Step(id="s4", persona_id="bob", channel=Channel.chat,
             action="list invoices for {{s2.org_id}}"),
    ])
    await run_play(play, ctx)

    s3 = next(c for c in adapter.calls if c.id == "s3")
    s4 = next(c for c in adapter.calls if c.id == "s4")
    assert "ORG-XYZ-9" in s3.action
    assert s3.target_ref == "ORG-XYZ-9"
    assert "ORG-XYZ-9" in s4.action


async def test_plays_run_in_parallel_but_steps_within_a_play_stay_ordered():
    m = make_manifest()
    log: list[str] = []

    def make_responder(play_tag):
        async def responder(step: Step) -> Result:
            log.append(f"{play_tag}:{step.id}:start")
            await asyncio.sleep(0.02)
            log.append(f"{play_tag}:{step.id}:end")
            return Result(status=200, raw="ok")
        return responder

    # Distinct personas per play (scheduling rule). Two api adapters, one per play.
    adapter1 = FakeAdapter(make_responder("p1"))
    adapter2 = FakeAdapter(make_responder("p2"))

    ctx1 = make_ctx(m, {Channel.api: adapter1})
    ctx2 = Ctx(gt=ctx1.gt, manifest=m, bus=ctx1.bus, adapters={Channel.api: adapter2},
               findings=ctx1.findings, redact=lambda s: s, throttle=ctx1.throttle,
               control_persona=ctx1.control_persona)

    p1 = Play(id="p1", title="p1", compound=True, context={}, steps=[
        Step(id="s1", persona_id="alice", channel=Channel.api, action="a"),
        Step(id="s2", persona_id="alice", channel=Channel.api, action="b"),
    ])
    p2 = Play(id="p2", title="p2", compound=True, context={}, steps=[
        Step(id="s1", persona_id="bob", channel=Channel.api, action="a"),
        Step(id="s2", persona_id="bob", channel=Channel.api, action="b"),
    ])

    await asyncio.gather(run_play(p1, ctx1), run_play(p2, ctx2))

    # within-play ordering: s1 fully starts before s2 for each play
    assert log.index("p1:s1:start") < log.index("p1:s2:start")
    assert log.index("p2:s1:start") < log.index("p2:s2:start")
    # parallelism: both plays' first steps start before either's first step ends
    first_end = min(log.index("p1:s1:end"), log.index("p2:s1:end"))
    assert log.index("p1:s1:start") < first_end
    assert log.index("p2:s1:start") < first_end


async def test_timeout_produces_error(monkeypatch):
    m = make_manifest()
    monkeypatch.setitem(campaign.TIMEOUTS, Channel.api, 0.01)

    async def slow(step):
        await asyncio.sleep(1.0)
        return Result(status=200, raw="too late")

    bus = FakeBus()
    ctx = make_ctx(m, {Channel.api: FakeAdapter(slow)}, bus=bus)
    play = Play(id="p", title="slow", compound=True, context={}, steps=[
        Step(id="s1", persona_id="bob", channel=Channel.api, action="fetch"),
    ])
    await run_play(play, ctx)

    from baduser.models import StepFinished
    finished = bus.of(StepFinished)
    assert finished[0].verdict is Verdict.error


async def test_benign_run_emits_no_chain_and_no_control():
    m = make_manifest()

    def responder(step):
        return DENIAL  # everything correctly refused

    ctx = make_ctx(m, {Channel.api: FakeAdapter(responder),
                       Channel.chat: FakeAdapter(responder)})
    play = Play(id="p", title="clean", compound=True, context={}, steps=[
        Step(id="s1", persona_id="bob", channel=Channel.api, action="fetch"),
    ])
    chain = await run_play(play, ctx)
    assert chain is None
    assert ctx.findings == []


def _routed_world():
    """A manifest/gt with DISCOVERED routes -- hero_chains builds from these, never from
    guessed English. `routes` is what seeding proved; `endpoints` is the broader repo read."""
    from baduser.models import Artifact

    m = make_manifest()
    gt = make_gt()
    m.artifacts.append(Artifact(id="doc1", tenant_id="ta", owner_persona_id="alice",
                                title="Acme secret", ref="doc1"))
    m.routes = ["POST /api/documents", "GET /api/documents/{id}"]      # seeding proved
    gt.endpoints = ["GET /api/invoices", "POST /api/chat",             # repo read only
                    "POST /api/invites", "POST /api/invites/{code}/accept"]
    return gt, m


def test_hero_chains_are_deterministic_and_persona_disjoint():
    gt, m = _routed_world()
    plays = hero_chains(gt, m)
    assert [p.id for p in plays] == [p.id for p in hero_chains(gt, m)]  # deterministic
    assert len(plays) >= 2
    assert_disjoint_personas(plays)  # must not raise


def test_hero_chains_emit_real_request_lines_not_prose():
    """A prose action 404s, and a 404 scores benign -- that is how a run reports clean
    while missing every bug. Every api step must be a literal request line."""
    gt, m = _routed_world()
    for play in hero_chains(gt, m):
        for st in play.steps:
            if st.channel == Channel.api:
                verb = st.action.split()[0]
                assert verb in {"GET", "POST", "PUT", "PATCH", "DELETE"}, st.action
                assert st.action.split()[1].startswith("/"), st.action


def test_hero_chains_replays_a_seeding_confirmed_read_as_the_wrong_tenant():
    gt, m = _routed_world()
    plays = hero_chains(gt, m)
    reads = [s.action for p in plays for s in p.steps if s.action.startswith("GET ")]
    assert "GET /api/documents/doc1" in reads      # the route seeding proved, real ref
    assert "GET /api/invoices" in reads            # repo-only route still attacked
    # and the attacker is never the artifact's owner
    for p in plays:
        for s in p.steps:
            if s.action == "GET /api/documents/doc1":
                assert m.persona(s.persona_id).tenant_id != "ta"


def test_compound_chain_is_emitted_only_when_an_invite_flow_was_discovered():
    gt, m = _routed_world()
    assert any(p.id == "chain" for p in hero_chains(gt, m))
    gt.endpoints = ["GET /api/invoices"]           # no invite route anywhere
    assert not any(p.id == "chain" for p in hero_chains(gt, m))


def test_no_discovered_routes_means_no_plays():
    """Better to emit nothing than plays that cannot possibly land: a play that 404s is
    scored benign and looks exactly like a clean app."""
    m, gt = make_manifest(), make_gt()
    m.routes, gt.endpoints = [], []
    assert hero_chains(gt, m) == []


def test_assert_disjoint_personas_raises_on_shared_persona():
    p1 = Play(id="p1", title="1", compound=True, steps=[
        Step(id="s1", persona_id="bob", channel=Channel.api, action="x")])
    p2 = Play(id="p2", title="2", compound=True, steps=[
        Step(id="s1", persona_id="bob", channel=Channel.api, action="y")])
    with pytest.raises(AssertionError):
        assert_disjoint_personas([p1, p2])
