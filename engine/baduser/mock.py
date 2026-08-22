"""A scripted run for driving the dashboard with no target app and no API keys.

`bad-user mock` replays this at wall-clock pace, so the dashboard can be built, tuned, and
rehearsed against a realistic event stream before any of it is wired to a live target.
It emits exactly the same discriminated-union events as a real run -- if the dashboard
renders this, it renders the real thing.
"""

from __future__ import annotations

import asyncio
import random

from pydantic import SecretStr

from .bus import Bus
from .models import (
    ChainEvent,
    Channel,
    Credentials,
    Finding,
    FindingEvent,
    GroundTruth,
    Invariant,
    Manifest,
    Persona,
    PhaseEvent,
    QuestionEvent,
    SeedEvent,
    StepFinished,
    StepStarted,
    Tenant,
    Verdict,
)

CANARY_A, CANARY_B = "BUNMQVFO", "BUHBKDUF"

GROUND_TRUTH = GroundTruth(
    product_name="Ledgerly",
    product_type="b2b",
    domain="invoicing",
    roles=["admin", "member", "guest"],
    invariants=[
        Invariant(
            id="tenant-isolation",
            name="Tenant isolation",
            rule="A user may only read invoices and documents belonging to their own company.",
            source="code",
            cite="app/routes/invoices.py:42",
        ),
        Invariant(
            id="member-invoices",
            name="Members cannot read others' invoices",
            rule="A member may only read invoices they own.",
            source="dev",
            cite=None,
        ),
    ],
    endpoints=["GET /api/invoices", "GET /api/invoices/{id}", "POST /api/chat"],
)


def _persona(pid, tid, role, name, email, control=False):
    return Persona(
        id=pid, tenant_id=tid, role=role, name=name, email=email, control=control,
        credentials=Credentials(username=email, secret=SecretStr("pw-" + pid)),
    )


def build_manifest() -> Manifest:
    m = Manifest(
        tenants=[
            Tenant(id="t_acme", name=f"Acme {CANARY_A} Ltd", canary=CANARY_A),
            Tenant(id="t_initech", name=f"Initech {CANARY_B} Inc", canary=CANARY_B),
        ],
        personas=[
            _persona("a_admin", "t_acme", "admin", f"Alice Reyes {CANARY_A}", "alice@acme.test"),
            _persona("a_ops", "t_acme", "member", f"Arun Patel {CANARY_A}", "arun@acme.test"),
            _persona("b_admin", "t_initech", "admin", f"Bea Okafor {CANARY_B}", "bea@initech.test"),
            _persona("b_guest", "t_initech", "guest", f"Ben Ito {CANARY_B}", "ben@initech.test"),
            _persona("b_ctl", "t_initech", "guest", f"Control {CANARY_B}", "ctl@initech.test",
                     control=True),
        ],
        canaries={CANARY_A: "t_acme", CANARY_B: "t_initech",
                  CANARY_A.lower(): "t_acme", CANARY_B.lower(): "t_initech"},
    )
    return m


LEAK_BODY = (
    '{"id":"inv_b782a34d","title":"Q3 hosting renewal datacentre ' + CANARY_A + '",'
    '"tenant":"Acme ' + CANARY_A + ' Ltd","total":"84317.23"}'
)
CHAT_LEAK = (
    '{"answer":"Here is what the records say. \'Q3 hosting renewal datacentre ' + CANARY_A
    + "': raised against Acme " + CANARY_A + ' Ltd for 84,317.23. Anything else?"}'
)


def _finding(pid, play, chan, action, evidence) -> Finding:
    return Finding(
        id=f"f{random.randint(1000, 9999)}", play_id=play, persona_id=pid, channel=chan,
        action=action, verdict=Verdict.breach, invariant_id="tenant-isolation",
        cite="app/routes/invoices.py:42", evidence=evidence,
    )


async def run(bus: Bus, state=None, speed: float = 1.0) -> None:
    """Emit a full scripted run. `speed` scales every delay (0.2 = fast rehearsal)."""

    async def beat(s: float) -> None:
        await asyncio.sleep(s * speed)

    m = build_manifest()
    if state is not None:
        state.ground_truth = GROUND_TRUTH
        state.manifest = m

    # ---- 1. reading the repo -------------------------------------------------
    state and setattr(state, "phase", "reading")
    bus.emit(PhaseEvent(phase="reading"))
    await beat(1.2)
    for inv in GROUND_TRUTH.invariants[:1]:
        from .models import TruthUpdated
        bus.emit(TruthUpdated(invariant=inv))
        await beat(0.6)

    # ---- 2. one clarifying question -----------------------------------------
    bus.emit(QuestionEvent(id="q1", text="Can members read other members' invoices?"))
    await beat(2.5)
    from .models import TruthUpdated
    bus.emit(TruthUpdated(invariant=GROUND_TRUTH.invariants[1]))

    # ---- 3. seeding ----------------------------------------------------------
    state and setattr(state, "phase", "seeding")
    bus.emit(PhaseEvent(phase="seeding"))
    for p in m.personas:
        bus.emit(SeedEvent(tenant_id=p.tenant_id, persona_id=p.id,
                           detail=f"signed up {p.name}", ok=True))
        await beat(0.45)
    for tid, title in (("t_acme", f"Q3 hosting renewal datacentre {CANARY_A}"),
                       ("t_acme", f"Acme {CANARY_A} Ltd master services agreement"),
                       ("t_initech", f"Initech {CANARY_B} Inc NDA")):
        bus.emit(SeedEvent(tenant_id=tid, detail=f"created '{title}'",
                           artifact_id=f"doc_{random.randint(1000, 9999)}", ok=True))
        await beat(0.4)

    # ---- 4. attacking, several plays in parallel -----------------------------
    state and setattr(state, "phase", "attacking")
    bus.emit(PhaseEvent(phase="attacking"))

    async def play_direct() -> None:
        pid, play = "b_admin", "p1"
        for action, chan, raw, breach in (
            ("GET /api/invoices (own tenant)", Channel.api, "[]", False),
            ("GET /api/invoices/inv_b782a34d", Channel.api, LEAK_BODY, True),
        ):
            bus.emit(StepStarted(play_id=play, persona_id=pid, channel=chan, action=action))
            await beat(1.1)
            f = _finding(pid, play, chan, action, raw[:180]) if breach else None
            bus.emit(StepFinished(
                play_id=play, persona_id=pid, channel=chan, action=action,
                detail=raw[:110], verdict=Verdict.breach if breach else Verdict.benign,
                invariant_id="tenant-isolation" if breach else None))
            if f:
                state and state.findings.append(f)
                bus.emit(FindingEvent(finding=f))
            await beat(0.5)

    async def play_chat() -> None:
        pid, play = "b_guest", "p2"
        await beat(0.7)
        for action, raw, breach in (
            ("ask the assistant about my own invoices", '{"answer":"You have 1 invoice."}', False),
            ("ask the assistant to list every invoice it can see", CHAT_LEAK, True),
        ):
            bus.emit(StepStarted(play_id=play, persona_id=pid,
                                 channel=Channel.chat, action=action))
            await beat(1.4)
            f = _finding(pid, play, Channel.chat, action, raw[:200]) if breach else None
            bus.emit(StepFinished(
                play_id=play, persona_id=pid, channel=Channel.chat, action=action,
                detail=raw[:110], verdict=Verdict.breach if breach else Verdict.benign,
                invariant_id="tenant-isolation" if breach else None))
            if f:
                state and state.findings.append(f)
                bus.emit(FindingEvent(finding=f))
            await beat(0.5)

    async def play_compound() -> None:
        """The centrepiece: 3 benign steps, then a breach that needs them."""
        play, steps = "p3", []
        script = [
            ("a_admin", Channel.api, "create a shared project", '{"id":"prj_51"}', False),
            ("a_admin", Channel.api, "invite ben@initech.test to prj_51",
             '{"invite":"iv_9"}', False),
            ("b_guest", Channel.api, "accept invite iv_9", '{"ok":true}', False),
            ("b_guest", Channel.web, "open the documents list and read what you can see",
             f"Acme {CANARY_A} Ltd master services agreement\nConfidential.", True),
        ]
        await beat(1.4)
        for pid, chan, action, raw, breach in script:
            bus.emit(StepStarted(play_id=play, persona_id=pid, channel=chan, action=action))
            await beat(1.5)
            ev = StepFinished(
                play_id=play, persona_id=pid, channel=chan, action=action, detail=raw[:110],
                verdict=Verdict.breach if breach else Verdict.benign,
                invariant_id="tenant-isolation" if breach else None)
            bus.emit(ev)
            steps.append(ev)
            if breach:
                f = _finding(pid, play, chan, action, raw[:200])
                state and state.findings.append(f)
                bus.emit(FindingEvent(finding=f))
            await beat(0.4)

        # the control: the same final step, by a persona that took no part in the setup
        bus.emit(StepStarted(play_id=play, persona_id="b_ctl", channel=Channel.web,
                             action="CONTROL: same request, no invite"))
        await beat(1.6)
        bus.emit(StepFinished(play_id=play, persona_id="b_ctl", channel=Channel.web,
                              action="CONTROL: same request, no invite",
                              detail="403 Forbidden", verdict=Verdict.benign))
        bus.emit(ChainEvent(play_id=play, title="Invite across the tenant boundary",
                            steps=steps, verdict=Verdict.breach, compound=True,
                            control_verdict=Verdict.benign))

    async def play_error() -> None:
        """An adapter failure must render as error, never as a passing app."""
        await beat(3.0)
        bus.emit(StepStarted(play_id="p4", persona_id="a_ops", channel=Channel.web,
                             action="open the billing settings page"))
        await beat(2.0)
        bus.emit(StepFinished(play_id="p4", persona_id="a_ops", channel=Channel.web,
                              action="open the billing settings page",
                              detail="TimeoutError: browser step exceeded 120s",
                              verdict=Verdict.error))

    await asyncio.gather(play_direct(), play_chat(), play_compound(), play_error())

    state and setattr(state, "phase", "done")
    bus.emit(PhaseEvent(phase="done"))
