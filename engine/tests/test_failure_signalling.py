"""A run that proved nothing must never read as a clean one.

Both early returns in `pipeline()` used to emit `phase="done"` with an empty findings list.
Nothing downstream could tell that apart from a genuinely clean run: the dashboard showed
green, and `--ci` evaluated `any(verdict == breach)` over `[]` and exited 0. A deploy gate
that passes when the tool never reached the target is the exact false clean this codebase
argues against in oracle.py ("FAILS VISIBLY"), world.py ("false clean") and PLAN 14.

These tests pin the distinction so it cannot quietly come back.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from baduser.bus import Bus
from baduser.cli import _fail
from baduser.models import Event, PhaseEvent, SeedEvent, SessionConfig
from baduser.server import EngineState


def _state() -> EngineState:
    bus = Bus()
    return EngineState(SessionConfig(run_id="t1", target="http://127.0.0.1:1"), bus)


def test_fail_marks_the_run_failed_not_done() -> None:
    state = _state()

    _fail(state, state.bus, "seeding failed: could not seed 2 tenants")

    # `done` here would be the bug: it is what a successful run also reports.
    assert state.phase == "failed"
    assert state.phase_detail == "seeding failed: could not seed 2 tenants"


def test_fail_puts_the_reason_on_the_wire() -> None:
    """A dashboard attached mid-run learns WHY from the stream, not from CLI stdout."""
    state = _state()

    _fail(state, state.bus, "no step reached the target: 6 attempts, none got a 2xx")

    (_seq, ev), = state.bus.log
    assert isinstance(ev, PhaseEvent)
    assert ev.phase == "failed"
    assert "none got a 2xx" in ev.detail


def test_phase_event_detail_survives_the_event_union() -> None:
    """`detail` is new; if it were dropped from the union the UI would go silent again."""
    raw = PhaseEvent(phase="failed", detail="seeding failed").model_dump_json()

    back = TypeAdapter(Event).validate_json(raw)

    assert isinstance(back, PhaseEvent)
    assert back.phase == "failed"
    assert back.detail == "seeding failed"


def test_phase_detail_defaults_empty_so_healthy_phases_are_unchanged() -> None:
    assert PhaseEvent(phase="attacking").detail == ""
    assert '"detail":""' in PhaseEvent(phase="attacking").model_dump_json()


def test_seed_failure_is_colourable_without_string_matching() -> None:
    """SeedEvent.ok existed and promised this, but nothing ever passed False."""
    ok = SeedEvent(tenant_id="t", detail="signed up Alice", ok=True)
    bad = SeedEvent(tenant_id="t", detail="tenant NOT seeded: missing working personas",
                    ok=False)

    assert ok.ok is True
    assert bad.ok is False
