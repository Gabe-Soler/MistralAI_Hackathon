"""World synthesis + seeding. Fully offline: FakeChannel everywhere, no browser, no network."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from baduser.channels.fake import FakeChannel
from baduser.models import Channel, GroundTruth, Invariant, Manifest, Result
from baduser.world import SeedError, parse_hint, seed, synth_world

GT = GroundTruth(
    product_name="Ledger",
    product_type="b2b",
    domain="invoicing for agencies",
    roles=["admin", "member"],
    endpoints=["GET /api/invoices", "POST /api/invoices"],
    signup_hint="POST /api/register",
    invariants=[Invariant(id="tenant-isolation", name="tenant isolation",
                          rule="a user only sees their org's data", source="code",
                          cite="app.py:42")],
)


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, ev):
        self.events.append(ev)


class Store:
    def __init__(self):
        self.saves = 0

    def save_manifest(self, m):
        self.saves += 1


def created(status=201, ref="abc"):
    return Result(status=status, raw=json.dumps({"id": ref}))


def api_ok(**over):
    """An api FakeChannel that accepts /api/register and /api/invoices, 404s anything else."""
    def handler(step):
        path = step.action.split()[1]
        if path in ("/api/register", "/api/invoices"):
            return created(ref=step.id)
        return Result(status=404, raw="not found")

    return FakeChannel(over.get("script"), default=over.get("default", handler))


# ---------------------------------------------------------------- synth_world


def test_every_string_a_tenant_owns_carries_its_canary():
    m = synth_world(GT, "run-1")
    assert len(m.tenants) == 2
    for t in m.tenants:
        assert t.canary in t.name
        for p in [x for x in m.personas if x.tenant_id == t.id]:
            assert t.canary in p.name, "person names must carry the canary"
            assert t.canary in p.email.split("@")[0], "email LOCAL PART must carry the canary"
        arts = [a for a in m.artifacts if a.tenant_id == t.id]
        assert arts
        for a in arts:
            assert t.canary in a.title and t.canary in a.body
            assert a.title.startswith(t.canary), "canary belongs in a short prominent field"


def test_canaries_table_maps_both_cases_to_the_owning_tenant():
    m = synth_world(GT, "run-1")
    for t in m.tenants:
        assert m.canaries[t.canary] == t.id
        assert m.canaries[t.canary.lower()] == t.id  # apps lowercase emails
    assert len(set(m.canaries.values())) == 2
    assert len({t.canary for t in m.tenants}) == 2


def test_the_oracle_can_actually_see_a_leak_built_from_this_world():
    from baduser.models import Step
    from baduser.oracle import check

    m = synth_world(GT, "run-1")
    a_doc = next(a for a in m.artifacts if a.tenant_id == "t1")
    intruder = next(p for p in m.personas if p.tenant_id == "t2")
    step = Step(id="s1", persona_id=intruder.id, channel=Channel.api, action="GET /api/invoices")

    leak = check(GT, m, step, Result(status=200, raw=json.dumps({"title": a_doc.title})))
    assert leak is not None and leak.verdict.value == "breach"
    assert leak.invariant_id == "tenant-isolation" and leak.cite == "app.py:42"

    own = next(a for a in m.artifacts if a.tenant_id == "t2")
    assert check(GT, m, step, Result(status=200, raw=own.title)) is None


def test_deterministic_for_a_run_id_and_different_across_run_ids():
    a, b = synth_world(GT, "run-1"), synth_world(GT, "run-1")
    assert a.model_dump() == b.model_dump()
    assert [p.credentials.reveal() for p in a.personas] == [p.credentials.reveal() for p in b.personas]
    c = synth_world(GT, "run-2")
    assert {t.canary for t in c.tenants}.isdisjoint({t.canary for t in a.tenants})


def test_two_control_personas_per_tenant():
    m = synth_world(GT, "run-1")
    for t in m.tenants:
        people = [p for p in m.personas if p.tenant_id == t.id]
        assert len([p for p in people if p.control]) == 2
        assert len([p for p in people if not p.control]) == 2
    # controls own nothing: they must have taken no part in the setup
    owners = {a.owner_persona_id for a in m.artifacts}
    assert not any(p.control for p in m.personas if p.id in owners)


def test_amounts_are_unique_non_round_and_banded_per_tenant():
    m = synth_world(GT, "run-1")
    amounts = [a.amount for a in m.artifacts]
    assert len(set(amounts)) == len(amounts)
    for a in m.artifacts:
        assert a.amount % Decimal(100) != 0, "a round amount is unrecognisable as evidence"
    bands = {t.id: [a.amount for a in m.artifacts if a.tenant_id == t.id] for t in m.tenants}
    assert max(bands["t1"]) < min(bands["t2"])


def test_secrets_are_distinct_and_canary_free():
    m = synth_world(GT, "run-1")
    secrets = m.secrets()
    assert len(set(secrets)) == len(secrets) == len(m.personas)
    canaries = {t.canary for t in m.tenants}
    assert all(c not in s for s in secrets for c in canaries)
    assert all(s.isalnum() and len(s) >= 12 for s in secrets)


def test_roles_and_kind_come_from_ground_truth():
    m = synth_world(GT, "run-1")
    assert {p.role for p in m.personas} == {"admin", "member"}
    assert all(a.kind == "invoice" for a in m.artifacts)  # inferred from the domain/endpoints
    plain = synth_world(GroundTruth(product_name="X", product_type="b2c"), "run-1")
    assert all(a.kind == "document" for a in plain.artifacts)
    assert {p.role for p in plain.personas} == {"admin", "member"}


def test_scales_to_more_tenants():
    m = synth_world(GT, "run-1", n_tenants=3)
    assert len(m.tenants) == 3 and len(m.personas) == 12


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("POST /api/register", ("POST", "/api/register")),
        ("/signup", ("POST", "/signup")),
        ("http://app.test/users/new", ("POST", "/users/new")),
        ("PUT /api/x", ("PUT", "/api/x")),
        ("", ("POST", "/fallback")),
        ("the form lives at /join, submit it", ("POST", "/join")),
    ],
)
def test_parse_hint_is_loose(hint, expected):
    assert parse_hint(hint, "/fallback") == expected


# ---------------------------------------------------------------- seed


async def test_seed_creates_everyone_through_the_api_and_records_refs():
    m = synth_world(GT, "run-1")
    api, bus, store = api_ok(), Bus(), Store()
    out = await seed(m, {Channel.api: api}, bus, store, gt=GT)

    assert out is m
    signups = [s for s in api.calls if "/api/register" in s.action]
    assert len(signups) == len(m.personas)  # controls get real accounts too
    assert all(a.ref for a in m.artifacts)
    assert store.saves >= 2
    seeded = [e for e in bus.events if e.type == "seed"]
    assert len(seeded) == len(m.personas) + len(m.artifacts)
    assert any(e.artifact_id for e in seeded)


async def test_seed_never_puts_a_secret_in_a_step_action():
    """CONTRACT invariant 2 / PLAN 11b.4 -- actions are copied into events and sent to Mistral."""
    m = synth_world(GT, "run-1")
    api, web = api_ok(), FakeChannel(Result(raw="signed up"))
    await seed(m, {Channel.api: api, Channel.web: web}, gt=GT)

    m2 = synth_world(GT, "run-2")  # api-only: signup goes through the http adapter instead
    api2 = api_ok()
    await seed(m2, {Channel.api: api2}, gt=GT)

    actions = " ".join(s.action for s in api.calls + web.calls + api2.calls)
    for secret in m.secrets() + m2.secrets():
        assert secret not in actions
    # the api signup carries a placeholder the adapter resolves from the manifest
    assert "{{secret}}" in " ".join(s.action for s in api2.calls)
    # the browser task names the persona and lets the adapter inject the password
    assert all(s.persona_id and s.persona_id in {p.id for p in m.personas} for s in web.calls)


async def test_seed_prefers_the_web_channel_for_signup_and_the_api_for_artifacts():
    """PLAN 11c: NL browser signup adapts to an unknown form; artifacts go the fast way."""
    m = synth_world(GT, "run-1")
    web, api = FakeChannel(Result(raw="account created")), api_ok()
    await seed(m, {Channel.api: api, Channel.web: web}, gt=GT)
    assert len(web.calls) == len(m.personas)
    assert all(s.channel is Channel.web for s in web.calls)
    assert not [s for s in api.calls if "/api/register" in s.action]
    # >= not ==: seeding EXPLORES collections (tries an untried path first) so an
    # artifact can cost more than one call. The intent here is that artifacts go via
    # api and not the browser, not that each costs exactly one request.
    assert len(api.calls) >= len(m.artifacts)


async def test_seed_falls_back_to_the_signup_hint_when_the_browser_fails_twice():
    m = synth_world(GT, "run-1")
    web, api = FakeChannel(Result(error="agent gave up")), api_ok()
    await seed(m, {Channel.api: api, Channel.web: web}, gt=GT)
    assert len(web.calls) == 2 * len(m.personas)  # two attempts each, then give up
    assert len([s for s in api.calls if "/api/register" in s.action]) == len(m.personas)


async def test_seed_probes_other_routes_when_the_hint_is_wrong():
    """The hint is a hint, never a contract -- a wrong one must not fail the run."""
    m = synth_world(GT, "run-1")
    api = api_ok()
    gt = GT.model_copy(update={"signup_hint": "POST /api/nope"})
    await seed(m, {Channel.api: api}, gt=gt)
    tried = [s.action.split()[1] for s in api.calls if s.id.startswith("seed-signup")]
    assert tried[0] == "/api/nope" and "/api/register" in tried
    assert tried.count("/api/nope") == 1  # the working route is remembered, not re-probed


async def test_seed_raises_when_only_one_tenant_comes_up():
    """CONTRACT invariant 4: one tenant makes every cross-tenant check pass vacuously."""
    m = synth_world(GT, "run-1")

    def only_t1(step):
        return created() if step.persona_id.startswith("t1") else Result(status=500, raw="boom")

    bus = Bus()
    with pytest.raises(SeedError, match="could not seed 2 tenants"):
        await seed(m, {Channel.api: FakeChannel(only_t1)}, bus, gt=GT)
    assert any("NOT seeded" in e.detail for e in bus.events)


async def test_seed_raises_for_a_single_tenant_world():
    m = synth_world(GT, "run-1", n_tenants=1)
    with pytest.raises(SeedError, match="false clean"):
        await seed(m, {Channel.api: api_ok()}, gt=GT)


async def test_seed_raises_when_a_partial_tenant_loses_a_working_persona():
    m = synth_world(GT, "run-1")

    def flaky(step):
        # t2's second working persona never signs up -> t2 is not a usable tenant
        return Result(status=500) if step.persona_id == "t2-p2" else created()

    with pytest.raises(SeedError):
        await seed(m, {Channel.api: FakeChannel(flaky)}, gt=GT)


async def test_a_failing_control_persona_does_not_sink_the_tenant():
    m = synth_world(GT, "run-1")
    controls = {p.id for p in m.personas if p.control}

    def handler(step):
        return Result(status=500) if step.persona_id in controls else created()

    await seed(m, {Channel.api: FakeChannel(handler)}, gt=GT)  # must not raise


async def test_an_adapter_that_raises_is_a_failed_persona_not_a_crashed_run():
    m = synth_world(GT, "run-1")
    bus = Bus()
    with pytest.raises(SeedError):
        await seed(m, {Channel.api: FakeChannel(raises=RuntimeError("socket gone"))}, bus, gt=GT)
    assert any("socket gone" in e.detail for e in bus.events)


async def test_artifact_failure_is_reported_but_not_fatal():
    m = synth_world(GT, "run-1")

    def signups_only(step):
        return created() if "register" in step.action else Result(status=404, raw="no such route")

    bus = Bus()
    await seed(m, {Channel.api: FakeChannel(signups_only)}, bus, gt=GT)  # tenants are fine
    assert any("could not create" in e.detail for e in bus.events)
    assert all(a.ref == "" for a in m.artifacts)


async def test_seed_sends_the_canary_bearing_strings_to_the_target():
    m = synth_world(GT, "run-1")
    api = api_ok()
    await seed(m, {Channel.api: api}, gt=GT)
    sent = " ".join(s.action for s in api.calls)
    for t in m.tenants:
        assert t.canary in sent  # the tag lands in the text the app STORES, not in metadata


async def test_seed_works_with_no_bus_or_store():
    m = synth_world(GT, "run-1")
    assert await seed(m, {Channel.api: api_ok()}, None, None, gt=GT) is m


async def test_seed_without_ground_truth_still_probes_default_routes():
    m = synth_world(GT, "run-1")
    await seed(m, {Channel.api: api_ok()})
    assert all(a.ref for a in m.artifacts)


async def test_seed_with_no_adapters_raises():
    with pytest.raises(SeedError):
        await seed(synth_world(GT, "run-1"), {}, gt=GT)


async def test_seed_of_an_empty_manifest_raises():
    with pytest.raises(SeedError):
        await seed(Manifest(), {Channel.api: api_ok()}, gt=GT)
