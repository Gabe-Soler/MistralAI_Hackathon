"""The one polymorphic seam: a channel turns a Step into a Result. See PLAN.md section 13.

    ChannelAdapter.act(step) -> Result

That is the whole interface. Ref extraction is deliberately NOT part of it: the oracle
canary-scans `Result.raw` directly (PLAN 14), which is exactly what lets one detector serve
API JSON, chat prose, page text and a voice transcript without four channel-specific parsers.

Two rules every adapter obeys:
  * On any exception, set `Result.error` (PLAN 12 / CONTRACT invariant 1). An adapter that
    swallows a crash into a benign-looking Result renders a broken app as a passing one.
  * Never read a secret out of `Step.action` (PLAN 11b rule 4). Adapters look credentials up
    from the manifest by `step.persona_id`.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Channel, Manifest, Result, SessionConfig, Step

MAX_RAW = 64 * 1024  # every adapter caps Result.raw at 64KB


@runtime_checkable
class ChannelAdapter(Protocol):
    async def act(self, step: Step) -> Result: ...


def cap(raw: str) -> str:
    """Shared 64KB clamp. Truncation is marked so a short body is never confused for one."""
    if len(raw) <= MAX_RAW:
        return raw
    return raw[:MAX_RAW] + f"...[truncated {len(raw) - MAX_RAW} chars]"


def shots_dir(run_id: str, runs_dir: str | Path | None = None) -> Path:
    """`.baduser/runs/<run_id>/shots` -- where the web channel keeps its frames (PLAN 10)."""
    base = Path(runs_dir) if runs_dir else Path(".baduser") / "runs"
    return base / run_id / "shots"


def build_adapters(
    cfg: SessionConfig,
    manifest: Manifest,
    *,
    runs_dir: str | Path | None = None,
    chat_path: str = "/api/chat",
    transport=None,
) -> dict[Channel, ChannelAdapter]:
    """One adapter per enabled channel, sharing the manifest for credential lookup.

    Adapters are long-lived for the whole session -- the http ones hold a persistent client
    per persona (cookie jar), and seeding uses the same instances as the attack (PLAN 5:
    "setup uses the same channels as the attack"), so a session established while seeding is
    still authenticated when a play reaches for it.

    voice is intentionally absent: cut from the build (PLAN 22).
    """
    from .http import ApiAdapter, ChatAdapter  # local: keeps import order flat

    out: dict[Channel, ChannelAdapter] = {}
    # api and chat hit the same host, so they share one per-persona client pool: a session
    # established while seeding over api must also authenticate the chat channel.
    pool: dict = {}
    if Channel.api in cfg.channels:
        out[Channel.api] = ApiAdapter(cfg.target, manifest, transport=transport, clients=pool)
    if Channel.chat in cfg.channels:
        out[Channel.chat] = ChatAdapter(
            cfg.target, manifest, path=chat_path, transport=transport, clients=pool
        )
    if Channel.web in cfg.channels:
        from .web import WebAdapter  # guarded import; browser-use is optional

        out[Channel.web] = WebAdapter(
            cfg.target, manifest, shots=shots_dir(cfg.run_id, runs_dir)
        )
    return out


async def close_adapters(adapters: dict[Channel, ChannelAdapter]) -> None:
    """Best-effort teardown of every adapter that owns sockets or a browser."""
    for a in adapters.values():
        closer = getattr(a, "aclose", None)
        if closer is not None:
            # teardown must never mask a run's real failure
            with contextlib.suppress(Exception):
                await closer()


__all__ = [
    "MAX_RAW",
    "ChannelAdapter",
    "Result",
    "Step",
    "build_adapters",
    "cap",
    "close_adapters",
    "shots_dir",
]
