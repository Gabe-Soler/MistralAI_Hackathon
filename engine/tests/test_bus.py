from __future__ import annotations

import asyncio

from baduser.bus import Bus
from baduser.models import PhaseEvent


def _phase(p: str) -> PhaseEvent:
    return PhaseEvent(phase=p)


async def test_two_subscribers_both_receive_every_event():
    bus = Bus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    for p in ("reading", "seeding", "attacking"):
        bus.emit(_phase(p))

    got1 = [q1.get_nowait() for _ in range(3)]
    got2 = [q2.get_nowait() for _ in range(3)]
    assert [seq for seq, _ in got1] == [1, 2, 3]
    assert [ev.phase for _, ev in got1] == ["reading", "seeding", "attacking"]
    # fan-out, not a shared queue: BOTH subscribers see all three.
    assert [ev.phase for _, ev in got2] == ["reading", "seeding", "attacking"]


async def test_emit_assigns_monotonic_seq_and_logs():
    bus = Bus()
    assert bus.emit(_phase("a")) == 1
    assert bus.emit(_phase("b")) == 2
    assert bus.seq == 2
    assert [seq for seq, _ in bus.log] == [1, 2]


async def test_replay_after_gap_via_since():
    bus = Bus()
    bus.emit(_phase("reading"))
    bus.emit(_phase("seeding"))
    bus.emit(_phase("attacking"))
    # A tab that saw up to seq=1 reconnects with Last-Event-ID: 1.
    q = bus.subscribe(since=1)
    replayed = [q.get_nowait() for _ in range(q.qsize())]
    assert [seq for seq, _ in replayed] == [2, 3]
    assert [ev.phase for _, ev in replayed] == ["seeding", "attacking"]


async def test_replay_maxsize_fits_missed():
    bus = Bus()
    for i in range(1500):
        bus.emit(_phase(str(i)))
    q = bus.subscribe(since=0)
    # maxsize = max(1000, missed+256); pre-load must not raise QueueFull.
    assert q.qsize() == 1500
    assert q.maxsize >= 1500


async def test_queuefull_on_full_subscriber_does_not_raise_into_emit():
    bus = Bus()
    q = bus.subscribe()
    # Force it full without going through the sized subscribe path.
    q._maxsize = 1  # small bound
    while not q.full():
        q.put_nowait((0, _phase("x")))
    # Emit must swallow QueueFull -- a throttled tab can't backpressure the loop.
    seq = bus.emit(_phase("y"))
    assert seq == 1  # emit still assigned a seq and appended to the log
    assert bus.log[-1][1].phase == "y"


async def test_unsubscribe_stops_delivery():
    bus = Bus()
    q = bus.subscribe()
    assert bus.subscribers == 1
    bus.unsubscribe(q)
    assert bus.subscribers == 0
    bus.emit(_phase("gone"))
    assert q.qsize() == 0


async def test_on_emit_hook_fires_per_event():
    seen: list[str] = []
    bus = Bus(on_emit=lambda ev: seen.append(ev.phase))
    bus.emit(_phase("reading"))
    bus.emit(_phase("done"))
    assert seen == ["reading", "done"]


async def test_subscribe_is_live_after_replay():
    bus = Bus()
    bus.emit(_phase("old"))
    q = bus.subscribe(since=0)
    bus.emit(_phase("new"))
    items = []
    for _ in range(2):
        items.append(await asyncio.wait_for(q.get(), timeout=1))
    assert [ev.phase for _, ev in items] == ["old", "new"]
