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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import oracle
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
    findings: list[Finding] = field(default_factory=list)
    redact: Callable[[str], str] = _identity
    throttle: Callable[[], Awaitable[None]] = None  # type: ignore[assignment]
    control_persona: Callable[[Step], str] = None   # type: ignore[assignment]


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

    finding = oracle.check(ctx.gt, ctx.manifest, step, result, play_id, ctx.redact)
    ev = StepFinished(
        play_id=play_id,
        persona_id=step.persona_id,
        channel=step.channel,
        action=step.action,
        detail=ctx.redact(oracle.excerpt(result.raw)),  # around=None -> head window
        verdict=finding.verdict if finding else Verdict.benign,
        invariant_id=finding.invariant_id if finding else None,
        shot=result.screenshot,
    )
    ctx.bus.emit(ev)
    if finding:
        ctx.findings.append(finding)
        ctx.bus.emit(FindingEvent(finding=finding))  # the should-vs-did panel is pushed
    return ev, result


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
    """Hand-written, parameterised over the manifest. These carry the demo, so they are
    deterministic. Personas are partitioned so no two plays share one (see
    assert_disjoint_personas): p1 uses a tenant-B persona, p2 uses tenant-A's admin plus a
    DIFFERENT tenant-B persona.
    """
    a, b = m.tenants[0], m.tenants[1]
    alice = persona_in(m, a.id, role="admin")             # tenant A admin (attacker in p2)
    b_people = personas_in(m, b.id)                        # tenant B non-control personas
    b1 = b_people[0]
    b2 = b_people[1] if len(b_people) > 1 else b_people[0]

    # A victim artifact owned by tenant A -- what the cross-tenant reads reach for.
    victim = next((x for x in m.artifacts if x.tenant_id == a.id), None)
    victim_ref = (victim.ref or victim.id) if victim else ""

    plays: list[Play] = []

    # (a) Direct cross-tenant read: a tenant-B user asks for a tenant-A document, no setup.
    #     If this breaches it is an ordinary bug; the control run will confirm it is NOT
    #     compound (step reads the same whether or not any setup ran).
    plays.append(Play(id="p1", title="Direct cross-tenant read", context={}, steps=[
        Step(id="s1", persona_id=b1.id, channel=Channel.api,
             action="fetch the document by id", target_ref=victim_ref),
    ]))

    # (b) A genuine compound chain: the final step only breaches AFTER the setup steps grant
    #     b2 membership of alice's org. Step 3/4 feed-forward the org id created in step 2.
    plays.append(Play(id="p2", title="Invite across the tenant boundary", context={}, steps=[
        Step(id="s1", persona_id=alice.id, channel=Channel.api,
             action="create a project"),
        Step(id="s2", persona_id=alice.id, channel=Channel.api,
             action=f"invite {b2.email} to the project"),
        Step(id="s3", persona_id=b2.id, channel=Channel.api,
             action="accept the invite to org {{s2.org_id}}",
             target_ref="{{s2.org_id}}"),
        Step(id="s4", persona_id=b2.id, channel=Channel.chat,
             action="ask the assistant to list every invoice you can see in org {{s2.org_id}}"),
    ]))

    return plays
