"""Fan-out event bus + append-only log. See PLAN.md section 16 "Bus contract".

One asyncio.Queue PER subscriber -- never one shared queue. A shared queue delivers each
event to exactly ONE consumer, so two dashboard tabs (or React StrictMode's double-mount)
steal each other's events. The append-only `_log` powers replay (subscribe(since=...)) and
`--replay` (persisted to events.jsonl via the on_emit hook).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from .models import Event


class Bus:
    def __init__(self, on_emit: Callable[[Event], None] | None = None) -> None:
        self._log: list[tuple[int, Event]] = []  # append-only, full run history
        self._subs: set[asyncio.Queue] = set()
        self._seq = 0
        self._on_emit = on_emit  # e.g. store.append_event -> events.jsonl

    def emit(self, ev: Event) -> int:
        self._seq += 1
        seq = self._seq
        self._log.append((seq, ev))
        for q in self._subs:  # fan-out: one queue per client
            # a throttled tab must never backpressure the attack loop (PLAN 16)
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait((seq, ev))
        if self._on_emit is not None:
            self._on_emit(ev)
        return seq

    def subscribe(self, since: int = 0) -> asyncio.Queue:
        missed = [(seq, ev) for seq, ev in self._log if seq > since]
        # maxsize sized to fit replay so the pre-load below can't raise QueueFull.
        q: asyncio.Queue = asyncio.Queue(maxsize=max(1000, len(missed) + 256))
        for item in missed:
            q.put_nowait(item)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        # /stream MUST call this in a `finally` or dead tabs' queues fill and stay.
        self._subs.discard(q)

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def log(self) -> list[tuple[int, Event]]:
        return self._log

    @property
    def subscribers(self) -> int:
        return len(self._subs)
