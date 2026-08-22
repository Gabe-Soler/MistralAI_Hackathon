"""`/baduser` entry (Typer). See PLAN.md sections 7, 16, 17.

Server + pipeline run in ONE process on ONE loop (PLAN 16 "Process model"):
`asyncio.gather(uvicorn.Server(...).serve(), pipeline(...))`. A two-process design gives
/stream a different Bus instance and it emits nothing, forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import importlib
from urllib.parse import urlparse

import typer
import uvicorn

from .bus import Bus
from .models import Channel, PhaseEvent, SessionConfig, Verdict
from .server import EngineState, create_app
from .store import Store

app = typer.Typer(add_completion=False, help="Bad User -- point it at a product; it attacks.")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def parse_channels(s: str) -> list[Channel]:
    return [Channel(c.strip()) for c in s.split(",") if c.strip()]


def is_loopback(target: str) -> bool:
    if not target:
        return True
    host = urlparse(target).hostname or target
    return host in _LOOPBACK


def guard_target(target: str, *, confirm=typer.confirm) -> None:
    """Default-deny (PLAN 24): an autonomous agent must not be pointable at prod by a paste.
    A non-loopback target requires explicit confirmation or the run aborts."""
    if is_loopback(target):
        return
    ok = confirm(
        f"Target {target!r} is NOT loopback. Bad User writes REAL data and attacks it. "
        "Continue?",
        default=False,
    )
    if not ok:
        raise typer.Abort()


async def pipeline(state: EngineState) -> None:
    """Thin spine (PLAN 17). truth/world/campaign are owned by other agents; import them
    lazily and tolerate their absence so cli.py is importable and testable before they land."""
    bus = state.bus
    for phase, mod in (("reading", "truth"), ("seeding", "world"), ("attacking", "campaign")):
        state.phase = phase
        bus.emit(PhaseEvent(phase=phase))
        # Module present -- real wiring belongs to that module's agent; spine stays thin.
        with contextlib.suppress(ImportError):
            importlib.import_module(f".{mod}", __package__)
    state.phase = "done"
    bus.emit(PhaseEvent(phase="done"))


async def _run(state: EngineState, host: str, port: int, ci: bool) -> int:
    config = uvicorn.Config(create_app(state), host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    if ci:
        # --ci: quit when the pipeline finishes; exit non-zero on any breach (PLAN 16).
        server_task = asyncio.create_task(server.serve())
        await pipeline(state)
        server.should_exit = True
        await server_task
        return 1 if any(f.verdict == Verdict.breach for f in state.findings) else 0
    # demo mode: server stays up after the pipeline finishes so the dashboard stays live.
    await asyncio.gather(server.serve(), pipeline(state))
    return 0


@app.command()
def main(
    repo: str = typer.Option("", "--repo", help="local source to read (intent)"),
    target: str = typer.Option("", "--target", help="deployment URL to test"),
    channels: str = typer.Option("api,chat", "--channels", help="channels this run"),
    support_phone: str | None = typer.Option(None, "--support-phone", help="voice line you own"),
    replay: str | None = typer.Option(None, "--replay", help="drive from a recorded events.jsonl"),
    ci: bool = typer.Option(False, "--ci", help="exit non-zero on breach and quit"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    guard_target(target)
    run_id = _dt.datetime.now(tz=_dt.UTC).strftime("%Y%m%d-%H%M%S")
    cfg = SessionConfig(
        run_id=run_id,
        repo=repo,
        target=target,
        channels=parse_channels(channels),
        support_phone=support_phone,
        replay=replay,
        ci=ci,
    )
    store = Store(run_id)
    store.save_config(cfg)
    bus = Bus(on_emit=store.append_event)  # persist every event to events.jsonl
    state = EngineState(cfg, bus, store)
    code = asyncio.run(_run(state, host, port, ci))
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
