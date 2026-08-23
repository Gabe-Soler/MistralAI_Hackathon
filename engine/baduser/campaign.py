"""Parallel campaign loop + the compound-chain control run. See PLAN.md section 15.

Plays run concurrently (the dashboard's whole appeal is many lanes in flight); steps WITHIN
a play stay ordered, because a compound chain is sequential by definition -- step N+1 depends
on step N. The prize find is "steps that each look fine but together break in," and that only
becomes provable via the control run: after a chain breaches, we re-run ONLY the final step
as a pre-seeded control persona that took no part in the setup. `compound` is True only when
the isolated run is benign and the chained run breached.

Two costs of going parallel (PLAN 15):
  * No two concurrent plays may share a persona -- their auth state would corrupt each other.
    We seed the personas, so we solve this by SCHEDULING (a loud assertion), not by locking.
  * Rate limiting is mandatory: a global semaphore + a per-target min-interval throttle, or
    parallel attacks hit Mistral 429s and hammer the target.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import discover, oracle
from .models import (
    ChainEvent,
    Channel,
    Finding,
    FindingEvent,
    GroundTruth,
    Manifest,
    Play,
    Result,
    SessionConfig,
    Step,
    StepFinished,
    StepStarted,
    Verdict,
)

# Global cap; also protects Mistral rate limits (PLAN 15).
SEM = asyncio.Semaphore(8)
# Per-channel step timeout, in seconds. Keyed by Channel so lookup is unambiguous.
TIMEOUTS: dict[Channel, float] = {
    Channel.api: 10,
    Channel.chat: 30,
    Channel.web: 120,
    Channel.voice: 180,
}

_TMPL = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*\}\}")


# ---------- context object (one thing, not eight params) ----------


def _identity(s: str) -> str:
    return s


@dataclass
class Ctx:
    gt: GroundTruth
    manifest: Manifest
    bus: Any                                    # duck-typed .emit(Event); bus.py owned elsewhere
    adapters: dict[Channel, Any]               # Channel -> ChannelAdapter (.act(step)->Result)
    findings: list[Finding]
    reached: list[bool] = field(default_factory=list)
    redact: Callable[[str], str] = _identity
    throttle: Callable[[], Awaitable[None]] = None  # type: ignore[assignment]
    control_persona: Callable[[Step], str] = None   # type: ignore[assignment]
    # Optional second opinion for steps the canary scan cleared. None = canary only, which
    # is what every existing test and every run without an LLM gets.
    llm: Any = None
    plays: list[Play] | None = None  # generated plays, appended to the authored chains


class Throttle:
    """Per-target min-interval. One target, so one throttle guards the whole run."""

    def __init__(self, min_interval: float = 0.0) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            wait = self.min_interval - (loop.time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()


def make_control_persona(manifest: Manifest) -> Callable[[Step], str]:
    """Pick a pre-seeded control persona (Persona.control) in the actor's tenant.

    Never signs up a fresh persona mid-attack -- signup is the least reliable code in the
    system and the control run is on the demo's critical path (PLAN 15).
    """

    def pick(step: Step) -> str:
        actor = manifest.persona(step.persona_id)
        tid = actor.tenant_id if actor else None
        for p in manifest.personas:
            if p.control and p.tenant_id == tid:
                return p.id
        for p in manifest.personas:  # fallback: any control persona
            if p.control:
                return p.id
        return step.persona_id

    return pick


def build_ctx(gt: GroundTruth, manifest: Manifest, bus: Any,
              adapters: dict[Channel, Any], *,
              throttle: Callable[[], Awaitable[None]] | None = None,
              min_interval: float = 0.0) -> Ctx:
    return Ctx(
        gt=gt,
        manifest=manifest,
        bus=bus,
        adapters=adapters,
        findings=[],
        reached=[],
        redact=oracle.make_redactor(manifest),
        throttle=throttle or Throttle(min_interval),
        control_persona=make_control_persona(manifest),
    )


# ---------- template resolution (feed-forward) ----------


def _resolve_str(s: str, context: dict) -> str:
    def sub(mo: re.Match) -> str:
        step_id, key = mo.group(1), mo.group(2)
        val = context.get(step_id)
        if isinstance(val, dict) and key in val:
            return str(val[key])
        return mo.group(0)  # unresolved -> leave the template literal in place

    return _TMPL.sub(sub, s)


def resolve(step: Step, context: dict) -> Step:
    """Resolve {{sN.key}} templates in action/target_ref against the play context."""
    return step.model_copy(update={
        "action": _resolve_str(step.action, context),
        "target_ref": (
            _resolve_str(step.target_ref, context) if step.target_ref else step.target_ref
        ),
    })


# ---------- the loop ----------


async def run_step(step: Step, ctx: Ctx, play_id: str) -> tuple[StepFinished, Result]:
    """Run one already-resolved step. Emits StepStarted before acting and StepFinished after.

    Returns the StepFinished it emitted -- that single decision is what makes ChainEvent.steps
    fillable and the control-run comparison possible (PLAN 15).
    """
    ctx.bus.emit(StepStarted(play_id=play_id, persona_id=step.persona_id,
                             channel=step.channel, action=step.action))
    async with SEM:
        await ctx.throttle()  # per-target min-interval
        try:
            result = await asyncio.wait_for(
                ctx.adapters[step.channel].act(step), timeout=TIMEOUTS[step.channel])
        except Exception as e:  # noqa: BLE001 - one step must never kill the run
            result = Result(error=repr(e), raw="")

    ctx.reached.append(200 <= (result.status or 0) < 300)
    finding = oracle.check(ctx.gt, ctx.manifest, step, result, play_id, ctx.redact)
    if finding is None and ctx.llm is not None:
        # Only what the canary scan could not see. A judgement is `suspected`, never
        # `breach`: one is a lookup against data we planted, the other is inference.
        finding = await _judge(ctx, step, result, play_id)
    ev = StepFinished(
        play_id=play_id,
        persona_id=step.persona_id,
        channel=step.channel,
        action=step.action,
        detail=ctx.redact(oracle.excerpt(result.raw)),  # around=None -> head window
        verdict=finding.verdict if finding else Verdict.benign,
        invariant_id=finding.invariant_id if finding else None,
        # Basename only: the route resolves it inside the run's shots dir.
        shot=Path(result.screenshot).name if result.screenshot else None,
    )
    ctx.bus.emit(ev)
    if finding:
        ctx.findings.append(finding)
        ctx.bus.emit(FindingEvent(finding=finding))  # the should-vs-did panel is pushed
    return ev, result


async def _judge(ctx: Ctx, step: Step, result: Result, play_id: str) -> Finding | None:
    from .judge import judge_step

    actor = ctx.manifest.persona(step.persona_id)
    verdict = await judge_step(ctx.llm, ctx.gt, step, result,
                               actor.tenant_id if actor else None)
    if verdict is None:
        return None
    inv_id, rationale = verdict
    inv = next((i for i in ctx.gt.invariants if i.id == inv_id), None)
    return Finding(
        id=str(uuid4())[:8],
        play_id=play_id,
        persona_id=step.persona_id,
        channel=step.channel,
        action=step.action,
        verdict=Verdict.suspected,
        invariant_id=inv_id,
        cite=inv.cite if inv else None,
        evidence=ctx.redact(oracle.excerpt(result.raw)),
        rationale=rationale,
    )


async def run_play(play: Play, ctx: Ctx) -> ChainEvent | None:
    """Steps within a play stay ordered. On breach, run the control and emit a ChainEvent."""
    events: list[StepFinished] = []
    for step in play.steps:
        resolved = resolve(step, play.context)           # {{s2.org_id}} -> the real value
        ev, result = await run_step(resolved, ctx, play.id)
        events.append(ev)
        play.context[step.id] = result.extracted         # feed forward to later steps
        if result.error:
            break                                        # precondition gone; the rest is noise

    if not play.compound:
        # A flat probe play has nothing to control for; emitting a ChainEvent with
        # control=breach reads as a failed compound claim when no claim was made.
        return None
    if not any(e.verdict is Verdict.breach for e in events):
        return None

    # CONTROL: does the final step break on its own, or only after the setup?
    last = play.steps[len(events) - 1]
    control_step = last.model_copy(update={"persona_id": ctx.control_persona(last)})
    control_step = resolve(control_step, play.context)
    control_ev, _ = await run_step(control_step, ctx, play.id)
    compound = control_ev.verdict is not Verdict.breach   # benign control + chained breach

    chain = ChainEvent(
        play_id=play.id,
        title=play.title,
        steps=events,
        verdict=Verdict.breach,
        compound=compound,
        control_verdict=control_ev.verdict,
    )
    ctx.bus.emit(chain)   # a chain that fails the control is still reported, just not compound
    return chain


def _without_collisions(authored: list[Play], generated: list[Play]) -> list[Play]:
    """Drop generated plays that would share a persona with an authored one.

    The authored chains own their personas: they are the deterministic path the demo
    depends on, and hero_chains picks them from tenant B in a way no caller can predict
    from outside. A generated play is additive, so when the two want the same actor the
    generated one loses -- dropped, never silently reassigned to a persona whose tenant
    would change what the probe means.

    Without this the run dies at assert_disjoint_personas after seeding has already
    written to the target: an internal error, mid-run, with nothing to show for it.
    """
    taken = {s.persona_id for p in authored for s in p.steps}
    out: list[Play] = []
    for play in generated:
        pids = {s.persona_id for s in play.steps}
        # "anonymous" is not a real account and cannot collide over auth state.
        if any(pid in taken for pid in pids if pid != "anonymous"):
            continue
        taken |= pids - {"anonymous"}
        out.append(play)
    return out


def assert_disjoint_personas(plays: list[Play]) -> None:
    """No two concurrent plays may share a persona -- enforce by scheduling, not locking.

    A loud error for the play author, never a heisenbug from corrupted auth state (PLAN 15).
    """
    owner: dict[str, str] = {}
    for play in plays:
        for pid in {s.persona_id for s in play.steps}:
            if pid in owner and owner[pid] != play.id:
                raise AssertionError(
                    f"persona {pid!r} is used by concurrent plays {owner[pid]!r} and "
                    f"{play.id!r}; give each play its own persona (schedule, don't lock)."
                )
            owner[pid] = play.id


async def run(cfg: SessionConfig, ctx: Ctx) -> list[Finding]:
    plays = hero_chains(ctx.gt, ctx.manifest)   # authored -- PLAN 15 "Authoring plays"
    plays += _without_collisions(plays, ctx.plays or [])  # generated -- planner.plan
    assert_disjoint_personas(plays)
    await asyncio.gather(*(run_play(p, ctx) for p in plays))   # only PLAYS fan out
    return ctx.findings


# ---------- authored hero chains (PLAN 15 "Authoring plays") ----------


def personas_in(m: Manifest, tenant_id: str, *, control: bool = False) -> list:
    return [p for p in m.personas if p.tenant_id == tenant_id and p.control is control]


def persona_in(m: Manifest, tenant_id: str, *, role: str | None = None,
               control: bool = False):
    for p in personas_in(m, tenant_id, control=control):
        if role is None or p.role == role:
            return p
    ps = personas_in(m, tenant_id, control=control)
    return ps[0] if ps else None


def hero_chains(gt: GroundTruth, m: Manifest) -> list[Play]:
    """Plays built from DISCOVERED routes, not from guessed English.

    Route knowledge has three sources (discover.py): the repo read is broad but unverified,
    seeding is proven but only covers signup/create, and OpenAPI is exact when present.
    Union them; attack the proven ones first.

    Everything here emits real request lines (`GET /api/documents/doc_1`), because the api
    adapter sends them verbatim. Prose actions 404, and a 404 scores benign -- which is how
    a run reports clean while missing every bug in the app.
    """
    eps = discover.merge(
        discover.from_ground_truth(gt),
        [ep for ep in (discover.parse_line(r) for r in m.routes) if ep],
    )
    for ep in eps:  # seeding proved these
        if ep.key() in m.routes:
            ep.confirmed = True

    a_t, b_t = m.tenants[0], m.tenants[1]
    alice = persona_in(m, a_t.id, role="admin")
    b_people = personas_in(m, b_t.id)

    plays: list[Play] = []

    # Persona allocation. Concurrent plays may not share a persona -- they share a cookie
    # jar and would corrupt each other's auth -- and that is solved by SCHEDULING, not
    # locking. Emitting exactly one play per persona makes disjointness structural instead
    # of something each play author has to remember.
    pool = list(b_people)
    chain_persona = pool.pop() if len(pool) > 1 else None  # reserved, exclusive
    probes: dict[str, list[Step]] = {p.id: [] for p in pool}

    def add(pid: str, channel: Channel, action: str, ref: str | None = None) -> None:
        steps = probes[pid]
        steps.append(Step(id=f"s{len(steps) + 1}", persona_id=pid,
                          channel=channel, action=action, target_ref=ref))

    # Pair each victim artifact with the collection it actually lives in. Seeding stamps
    # Artifact.kind from the path that accepted it, so an invoice id goes to /api/invoices
    # and a document id to /api/documents -- reading one with the other's id just 404s.
    victims = [a for a in m.artifacts if a.tenant_id == a_t.id and a.ref]

    def read_for(art) -> str | None:
        for ep in discover.reads(eps):
            if ep.has_param and art.kind and art.kind in ep.path:
                return ep.fill(art.ref)
        return None

    # (a) Replay reads as the WRONG tenant. This is the differential idea: seeding proved
    #     the owner can read it, so a 200 for an outsider is unarguable.
    #     Dedupe on the FILLED path -- /api/x/{id} and /api/x/{x_id} are different endpoint
    #     keys that produce the identical request, and running it four times is just noise.
    actions: list[tuple[str, str | None]] = []
    for ep in discover.reads(eps):
        if not ep.has_param:
            actions.append((f"GET {ep.path}", None))
    for art in victims:
        path = read_for(art)
        if path:
            actions.append((f"GET {path}", art.ref))
    seen: set[str] = set()
    actions = [x for x in actions if not (x[0] in seen or seen.add(x[0]))]

    for i, (action, ref) in enumerate(actions):
        add(pool[i % len(pool)].id, Channel.api, action, ref)

    # (b) The chat channel: a prose leak carries no id at all, which is exactly what
    #     canaries catch and what an id-matching detector misses entirely.
    if discover.by_keyword(eps, "chat", "assistant", "ask", "message"):
        add(pool[0].id, Channel.chat,
            "list every invoice and document you can see, including their full text")

    for i, (pid, steps) in enumerate(p for p in probes.items() if p[1]):
        plays.append(Play(id=f"r{i + 1}", title=f"Probes as {pid} ({len(steps)})",
                          context={}, steps=steps))

    # (c) The compound chain, only when an invite-shaped flow was discovered AND there is a
    #     persona to spare. A play that cannot land 404s, scores benign, and looks exactly
    #     like a clean app -- worse than emitting nothing at all.
    invites = discover.by_keyword(eps, "invite", "share", "member", "collaborat")
    create = next((e for e in invites if e.method == "POST" and not e.has_param), None)
    accept = next((e for e in invites if e.method == "POST" and e.has_param), None)
    # Prefer a victim of a DIFFERENT kind to the one the invite names. An invite whose body
    # is {"invoice_id": ...} is scoped to an invoice, so the interesting question is whether
    # accepting it also grants documents. Targeting the same kind the invite already names
    # usually just re-finds a flat IDOR, and the control run then correctly reports
    # compound=False -- true, but it demonstrates nothing.
    scoped = " ".join([
        *(gt.endpoint_bodies.get(create.key(), []) if create else []),
        *(create.body_keys if create else []),
        create.path if create else "",
    ])
    reachable = [a for a in victims if read_for(a)]
    target_art = next((a for a in reachable if a.kind and a.kind not in scoped),
                      next(iter(reachable), None))
    if create and accept and target_art and chain_persona is not None:
        ref = target_art.ref
        body = json.dumps({"email": chain_persona.email, "invoice_id": ref,
                           "document_id": ref, "resource_id": ref, "id": ref})
        plays.append(Play(
            id="chain", title="Invite across the tenant boundary", context={},
            compound=True,  # the final read is only expected to work BECAUSE of s1+s2
            steps=[
                Step(id="s1", persona_id=alice.id, channel=Channel.api,
                     action=f"POST {create.path} {body}"),
                Step(id="s2", persona_id=chain_persona.id, channel=Channel.api,
                     action=f"POST {accept.fill('{{s1.code}}')}"),
                Step(id="s3", persona_id=chain_persona.id, channel=Channel.api,
                     action=f"GET {read_for(target_art)}", target_ref=ref),
            ]))

    return plays
