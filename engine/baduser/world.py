"""Synthesize a world, then create it in the target through the product's own interfaces.

PLAN.md sections 4 and 11c. Two halves:

  synth_world(gt, run_id)  -- pure, deterministic, no I/O. Faker + a run-seeded RNG produce
                              tenants, personas and artifacts. EVERY string a tenant owns
                              embeds that tenant's canary (PLAN 4 step 2b): company name,
                              person names, email local parts, document titles and bodies.
                              The canary in the *identity* strings is what makes derived data
                              partially detectable (PLAN 14 limitations).

  seed(manifest, adapters, bus, store) -- signs up, logs in and uploads through the app's own
                              UI/API. Never a DB insert: that the world was created the way a
                              user creates one is the entire differentiator (PLAN 4 step 1).

Two hard rules live here:
  * Fewer than 2 tenants seeded is a HARD ERROR, never a degraded run (CONTRACT invariant 4).
    With one tenant every cross-tenant check passes vacuously and the tool reports a false
    clean -- the worst possible failure for a deploy gate.
  * No secret ever enters `Step.action` (PLAN 11b rule 4). Steps carry `{{secret}}`
    placeholders that the adapter resolves from the manifest.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import random
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from faker import Faker
from pydantic import SecretStr

from .models import (
    Artifact,
    Channel,
    Credentials,
    GroundTruth,
    Manifest,
    Persona,
    Result,
    SeedEvent,
    Step,
    Tenant,
)

# Unambiguous alphabet: no 0/O/1/I/S5/2Z, because canaries get read off a projector.
_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679"
_PW_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_PERSONAS_PER_TENANT = 2  # budget: browser signup is ~20-30s each (PLAN 11c)
_CONTROLS_PER_TENANT = 2  # never used by a play; the compound-chain control run needs them
_ARTIFACTS_PER_PERSONA = 2

_SIGNUP_PATHS = ["/api/register", "/api/signup", "/api/auth/register", "/register", "/signup"]
_ARTIFACT_PATHS = ["/api/documents", "/api/items", "/api/invoices", "/documents", "/items"]


class SeedError(RuntimeError):
    """Raised when the world could not be created. Never downgraded to a warning."""


# ---------------------------------------------------------------- synth (pure)


def _seed_int(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:16], 16)


def _canary(rng: random.Random) -> str:
    return "BU" + "".join(rng.choice(_ALPHABET) for _ in range(6))


def _password(rng: random.Random) -> str:
    # Alnum only: a password with no quotes or backslashes survives being substituted into a
    # JSON body by the api adapter without escaping games.
    return "".join(rng.choice(_PW_ALPHABET) for _ in range(16))


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:24] or "co"


def _kind(gt: GroundTruth) -> str:
    blob = " ".join([gt.domain, *gt.endpoints]).lower()
    for word in ("invoice", "project", "ticket", "order", "note"):
        if word in blob:
            return word
    return "document"


def synth_world(gt: GroundTruth, run_id: str, n_tenants: int = 2) -> Manifest:
    """Deterministic given `run_id`: same run id, byte-identical world (PLAN 22 replay).

    `n_tenants < 2` is allowed here so the seed-time hard error can be tested, but no real
    run should ever produce one -- see `seed`.
    """
    base = _seed_int(run_id)
    rng = random.Random(base)
    fake = Faker()
    fake.seed_instance(base)

    roles = list(gt.roles) or ["admin", "member"]
    kind = _kind(gt)
    m = Manifest()
    used_canaries: set[str] = set()
    used_amounts: set[Decimal] = set()

    for i in range(n_tenants):
        canary = _canary(rng)
        while canary in used_canaries:
            canary = _canary(rng)
        used_canaries.add(canary)

        tid = f"t{i + 1}"
        company = f"{fake.company()} {canary}"
        m.tenants.append(Tenant(id=tid, kind="company", name=company, canary=canary))
        # Both cases registered: apps routinely lowercase emails, and the oracle's scan is a
        # case-sensitive substring match, so the folded form must be a canary in its own right.
        m.canaries[canary] = tid
        m.canaries[canary.lower()] = tid

        domain = f"{_slug(fake.last_name())}-{canary.lower()}.test"
        n_people = _PERSONAS_PER_TENANT + _CONTROLS_PER_TENANT
        for j in range(n_people):
            control = j >= _PERSONAS_PER_TENANT
            first, last = fake.first_name(), fake.last_name()
            pid = f"{tid}-c{j - _PERSONAS_PER_TENANT + 1}" if control else f"{tid}-p{j + 1}"
            local = f"{first.lower()}.{canary}"  # canary in the email LOCAL PART (PLAN 4 2b)
            email = f"{local}@{domain}"
            m.personas.append(
                Persona(
                    id=pid,
                    tenant_id=tid,
                    role=roles[j % len(roles)],
                    name=f"{first} {last} {canary}",
                    email=email,
                    credentials=Credentials(username=email, secret=SecretStr(_password(rng))),
                    control=control,
                )
            )

        # Artifacts belong to the working personas only; controls took no part in the setup.
        owners = [p for p in m.personas if p.tenant_id == tid and not p.control]
        low = 10_000 * (i + 1)  # per-tenant band, so a leaked amount also names its tenant
        for owner in owners:
            for k in range(_ARTIFACTS_PER_PERSONA):
                amount = _amount(rng, low, used_amounts)
                aid = f"{owner.id}-a{k + 1}"
                title = f"{canary} {fake.bs().title()} {kind.title()}"
                body = (
                    f"{canary} :: {kind} for {company}. Owner {owner.name} <{owner.email}>. "
                    f"Amount {amount}. {fake.sentence(nb_words=10)} Ref {canary}."
                )
                m.artifacts.append(
                    Artifact(
                        id=aid,
                        tenant_id=tid,
                        owner_persona_id=owner.id,
                        kind=kind,
                        title=title,
                        body=body,
                        amount=amount,
                    )
                )
    return m


def _amount(rng: random.Random, low: int, used: set[Decimal]) -> Decimal:
    """Unique, non-round, inside this tenant's band -- a round 5000.00 is unrecognisable."""
    while True:
        cents = rng.randrange(1, 100)
        dollars = low + rng.randrange(1, 9000)
        if dollars % 100 == 0:
            continue
        amount = Decimal(f"{dollars}.{cents:02d}")
        if amount not in used:
            used.add(amount)
            return amount


# ---------------------------------------------------------------- seed (I/O)


def _emit(bus: Any, ev: SeedEvent) -> None:
    if bus is not None and hasattr(bus, "emit"):
        bus.emit(ev)


def _save(store: Any, manifest: Manifest) -> None:
    """Incremental save: a crash mid-seed still leaves the credentials we already created."""
    if store is not None and hasattr(store, "save_manifest"):
        store.save_manifest(manifest)


def _confirm(state: dict, method: str, path: str) -> None:
    """Record a route we have empirically proven works. The campaign attacks these first:
    a route seeding got a 2xx from is one worth replaying as the wrong tenant, no guessing."""
    state.setdefault("routes", [])
    key = f"{method.upper()} {path}"
    if key not in state["routes"]:
        state["routes"].append(key)


def _ok(r: Result) -> bool:
    """A step landed. `error` set is never ok -- an adapter crash is not a seeded tenant."""
    if r.error:
        return False
    return r.status is None or 200 <= r.status < 400


def parse_hint(hint: str, default_path: str) -> tuple[str, str]:
    """`GroundTruth.signup_hint` is a HINT, never a contract (PLAN 11c) -- so parse it loosely.

    Accepts "POST /api/register", "/signup", "http://host/signup", or a sentence containing
    one of those. Anything unrecognised falls back to `default_path`.
    """
    method, path = "POST", default_path
    for tok in (hint or "").replace(",", " ").split():
        upper = tok.upper()
        if upper in _METHODS:
            method = upper
        elif tok.startswith("http"):
            path = urlsplit(tok).path or path
        elif tok.startswith("/") and path == default_path:
            path = tok
    return method, path


def _signup_payload(persona: Persona, tenant: Tenant) -> dict[str, str]:
    """A superset body: unknown field names are ignored by most frameworks, and we do not
    know this app's schema (it was vibe-coded minutes ago). Secrets stay as placeholders."""
    return {
        "email": "{{email}}",
        "username": "{{username}}",
        "password": "{{secret}}",
        "name": persona.name,
        "full_name": persona.name,
        "company": tenant.name,
        "organization": tenant.name,
        "tenant": tenant.name,
        "role": persona.role,
    }


def _artifact_payload(a: Artifact) -> dict[str, Any]:
    return {
        "title": a.title,
        "name": a.title,
        "body": a.body,
        "content": a.body,
        "description": a.body,
        "kind": a.kind,
        "amount": str(a.amount) if a.amount is not None else None,
    }


def _artifact_candidates(gt: GroundTruth, kind: str) -> list[str]:
    """Prefer collection paths the code-reader actually saw (PLAN 12 GroundTruth.endpoints)."""
    found = []
    for e in gt.endpoints:
        _, path = parse_hint(e, "")
        if path and path.count("/") <= 3 and "{" not in path and not path.endswith("login"):
            found.append(path)
    plural = f"/api/{kind}s"
    return list(dict.fromkeys([*found, plural, *_ARTIFACT_PATHS]))


def _ref_of(raw: str) -> str:
    """Pull the created object's id out of the response, if it said one."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        for key in ("id", "uuid", "ref", "_id", "slug"):
            if inner.get(key) not in (None, ""):
                return str(inner[key])
    return ""


async def _signup(persona, tenant, gt, adapters, state) -> bool:
    """Fallbacks in order (PLAN 11c): NL browser signup, twice; then signup_hint as a POST."""
    web, api = adapters.get(Channel.web), adapters.get(Channel.api)

    if web is not None:
        task = (
            f"Sign up for a new account. Company: {tenant.name}. Name: {persona.name}. "
            f"Email: {persona.email}. Use the password you were given for this account."
        )  # no secret in the action -- the adapter injects it as sensitive_data (PLAN 11b.4)
        for attempt in range(2):
            step = Step(id=f"seed-signup-{persona.id}-{attempt}", persona_id=persona.id,
                        channel=Channel.web, action=task)
            if _ok(await web.act(step)):
                await _handoff_session(web, adapters, persona.id)
                return True

    if api is not None:
        if state.get("signup_path"):  # a route already worked this run; don't re-probe
            method, paths = state.get("signup_method", "POST"), [state["signup_path"]]
        else:
            method, hinted = parse_hint(gt.signup_hint, _SIGNUP_PATHS[0])
            # The hint goes first but is not a contract -- keep probing when it is wrong.
            paths = [hinted, *(p for p in _SIGNUP_PATHS if p != hinted)]
        body = json.dumps(_signup_payload(persona, tenant))
        for path in paths:
            step = Step(id=f"seed-signup-{persona.id}", persona_id=persona.id,
                        channel=Channel.api, action=f"{method} {path} {body}")
            if _ok(await api.act(step)):
                state["signup_path"], state["signup_method"] = path, method
                _confirm(state, method, path)
                return True
    return False


async def _handoff_session(web, adapters, persona_id: str) -> None:
    """Cookies out of the browser into the fast channels, so only seeding pays for the
    browser (PLAN 11c "then hand off to fast channels")."""
    exporter = getattr(web, "export_cookies", None)
    if exporter is None:
        return
    try:
        cookies = await exporter()
    except Exception:  # noqa: BLE001 - the handoff is an optimisation, not a requirement
        return
    for ch in (Channel.api, Channel.chat):
        adapter = adapters.get(ch)
        client_for = getattr(adapter, "client_for", None)
        if client_for and cookies:
            with contextlib.suppress(Exception):  # a stale cookie jar is not fatal
                client_for(persona_id).cookies.update(cookies)


async def _create_artifact(a: Artifact, gt: GroundTruth, adapters, state) -> bool:
    """Artifacts go through the api channel once a session exists -- browser cost is for
    signup only (PLAN 11c budget). Falls back to the web channel when there is no api."""
    api = adapters.get(Channel.api)
    if api is not None:
        body = json.dumps(_artifact_payload(a))
        # Spread artifacts across EVERY collection that works, rather than caching the
        # first one. An app with /api/documents and /api/invoices must end up with data in
        # both: seed only documents and `GET /api/invoices` returns [], which reads as a
        # clean app when it is really an untested one.
        working = state.setdefault("artifact_paths", [])
        tried = state.setdefault("artifact_tried", set())
        candidates = _artifact_candidates(gt, a.kind)
        n = state["artifact_n"] = state.get("artifact_n", 0) + 1

        # EXPLORE first, then round-robin. Rotating only among paths already known to work
        # means the first collection that succeeds is the only one ever tried -- the app
        # ends up with invoices and no documents, every /api/documents route returns
        # nothing, and the routes that read documents are silently never tested.
        untried = [c for c in candidates if c not in tried]
        if untried:
            paths = [untried[0], *working, *untried[1:]]
        elif working:
            paths = [working[n % len(working)], *working]
        else:
            paths = candidates
        for path in paths:
            step = Step(id=f"seed-artifact-{a.id}", persona_id=a.owner_persona_id,
                        channel=Channel.api, action=f"POST {path} {body}")
            tried.add(path)
            r = await api.act(step)
            if _ok(r):
                if path not in working:
                    working.append(path)
                state["artifact_path"] = path
                a.ref = _ref_of(r.raw) or a.ref or path
                a.kind = path.rstrip("/").rsplit("/", 1)[-1].rstrip("s") or a.kind
                _confirm(state, "POST", path)
                # The read route follows by convention from the create route, and the
                # oracle will tell us if the guess is wrong.
                _confirm(state, "GET", path.rstrip("/") + "/{id}")
                return True
    web = adapters.get(Channel.web)
    if web is not None:
        step = Step(id=f"seed-artifact-{a.id}", persona_id=a.owner_persona_id,
                    channel=Channel.web,
                    action=f"Create a {a.kind} titled '{a.title}' with body '{a.body}'.")
        if _ok(await web.act(step)):
            a.ref = a.ref or a.id
            return True
    return False


async def seed(manifest: Manifest, adapters, bus=None, store=None, *, gt=None) -> Manifest:
    """Create the synthesized world inside the target, through its own interfaces.

    Returns the same Manifest, with `Artifact.ref` filled in for everything that landed.
    Raises SeedError if fewer than two tenants come up -- CONTRACT invariant 4.
    """
    gt = gt or GroundTruth(product_name="", product_type="b2b")
    state: dict = {}  # routes that worked, reused for the rest of the run
    seeded: list[str] = []

    for tenant in manifest.tenants:
        people = [p for p in manifest.personas if p.tenant_id == tenant.id]
        working = [p for p in people if not p.control]
        live: set[str] = set()

        for persona in people:
            try:
                ok = await _signup(persona, tenant, gt, adapters, state)
                detail = ("signed up" if ok else "signup FAILED") + (
                    " (control)" if persona.control else ""
                )
            except Exception as e:  # noqa: BLE001 - a raising adapter is a failed persona, not a crashed run
                ok, detail = False, f"signup errored: {e!r}"
            if ok:
                live.add(persona.id)
            _emit(bus, SeedEvent(tenant_id=tenant.id, persona_id=persona.id, detail=detail))

        for a in [x for x in manifest.artifacts if x.tenant_id == tenant.id]:
            if a.owner_persona_id not in live:
                continue
            try:
                ok = await _create_artifact(a, gt, adapters, state)
                detail = f"created {a.kind} '{a.title}'" if ok else f"could not create '{a.title}'"
            except Exception as e:  # noqa: BLE001 - one artifact must not sink the tenant
                ok, detail = False, f"artifact errored: {e!r}"
            _emit(bus, SeedEvent(tenant_id=tenant.id, persona_id=a.owner_persona_id,
                                 artifact_id=a.id, detail=detail))

        # A tenant counts only when every non-control persona can actually log in: the hero
        # chains address personas by id, and a missing one turns a play into a silent no-op.
        if working and all(p.id in live for p in working):
            seeded.append(tenant.id)
        else:
            _emit(bus, SeedEvent(tenant_id=tenant.id,
                                 detail="tenant NOT seeded: missing working personas"))
        _save(store, manifest)

    manifest.routes = list(state.get("routes", []))  # hand the proven routes to the campaign

    if len(seeded) < 2:
        raise SeedError(
            f"could not seed 2 tenants (seeded {len(seeded)}: {seeded or 'none'}). "
            "Refusing to continue: with one tenant every cross-tenant check passes "
            "vacuously and the run would report a false clean."
        )
    _save(store, manifest)
    return manifest
