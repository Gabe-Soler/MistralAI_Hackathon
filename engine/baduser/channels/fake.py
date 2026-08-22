"""FakeChannel -- the deterministic adapter the whole test suite runs on.

No network, no browser, no clock. Everything else in the engine (world seeding, the campaign
loop, the oracle end-to-end) is tested by handing it one of these.

    ch = FakeChannel("hello")                          # every step -> raw "hello"
    ch = FakeChannel({"GET /api/invoices": '{"x":1}'}) # scripted by action (or step id)
    ch = FakeChannel(lambda step: Result(raw=step.action))
    ch = FakeChannel(sequence=["first", "second"])     # one per call, then the default
    ch = FakeChannel(raises=RuntimeError("boom"))      # act() raises, for timeout/crash paths

    await ch.act(step)
    ch.calls          # [Step, ...] in order
    ch.actions        # ["GET /api/invoices", ...]
    ch.last           # the most recent Step
    ch.for_persona("p1")

A scripted value may be a Result, a str (-> Result.raw with status 200), an int (-> status),
an Exception (raised), or a callable taking the Step.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..models import Result, Step

Scripted = Any  # Result | str | int | Exception | Callable[[Step], Result]


def as_result(value: Scripted, step: Step) -> Result:
    """Coerce a scripted value into a Result. Exceptions are raised, not coerced."""
    if callable(value) and not isinstance(value, Result):
        value = value(step)
    if isinstance(value, Exception):
        raise value
    if isinstance(value, Result):
        return value
    if isinstance(value, int):
        return Result(status=value)
    if value is None:
        return Result(status=200)
    return Result(status=200, raw=str(value))


class FakeChannel:
    """A ChannelAdapter that answers from a script and records everything it was asked."""

    def __init__(
        self,
        script: Mapping[str, Scripted] | Callable[[Step], Result] | str | Result | None = None,
        *,
        default: Scripted = None,
        sequence: list[Scripted] | None = None,
        raises: Exception | None = None,
    ):
        # A bare str/Result/callable is the default answer, not a lookup table.
        if script is not None and not isinstance(script, Mapping):
            default, script = script, None
        self.script: dict[str, Scripted] = dict(script or {})
        self.default: Scripted = default
        self.sequence: list[Scripted] = list(sequence or [])
        self.raises = raises
        self.calls: list[Step] = []

    # ---- recording ----

    @property
    def actions(self) -> list[str]:
        return [s.action for s in self.calls]

    @property
    def last(self) -> Step | None:
        return self.calls[-1] if self.calls else None

    def for_persona(self, persona_id: str) -> list[Step]:
        return [s for s in self.calls if s.persona_id == persona_id]

    def reset(self) -> None:
        self.calls.clear()

    # ---- scripting ----

    def on(self, key: str, value: Scripted) -> FakeChannel:
        """Add one scripted answer. Chainable."""
        self.script[key] = value
        return self

    def lookup(self, step: Step) -> Scripted:
        """exact action -> step id -> first key that is a substring of the action -> default."""
        if step.action in self.script:
            return self.script[step.action]
        if step.id in self.script:
            return self.script[step.id]
        for key, value in self.script.items():
            if key and key in step.action:
                return value
        if self.sequence:
            return self.sequence.pop(0)
        return self.default

    # ---- the interface ----

    async def act(self, step: Step) -> Result:
        self.calls.append(step)
        if self.raises is not None:
            raise self.raises
        return as_result(self.lookup(step), step)

    async def aclose(self) -> None:
        return None
