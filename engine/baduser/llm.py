"""Thin Mistral wrapper. See PLAN.md section 18.

One wrapper exposing chat() and json(schema). `mistralai` is an OPTIONAL dependency
(the `llm` extra), so its import is guarded -- importing this module must never fail on a
machine without it. Everything downstream depends on the `LLM` Protocol, not on Mistral, so
the whole pipeline is testable with `FakeLLM` and zero network.

Concurrency + rate limits: a global semaphore caps in-flight calls and exponential backoff
with jitter absorbs the 429s that parallel plays (PLAN 15) would otherwise trigger.
"""

from __future__ import annotations

import asyncio
import json as _jsonlib
import os
import random
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

# mistralai is optional (pyproject `llm` extra). Guard it: absence must not break imports.
try:  # pragma: no cover - exercised only when the extra is installed
    from mistralai import Mistral  # type: ignore

    _HAVE_MISTRAL = True
except Exception:  # noqa: BLE001  # pragma: no cover - any import failure means "unavailable"
    Mistral = None  # type: ignore
    _HAVE_MISTRAL = False


DEFAULT_MODEL = "mistral-large-latest"
_MAX_CONCURRENCY = 4
_MAX_RETRIES = 5
_BASE_DELAY = 0.5


@runtime_checkable
class LLM(Protocol):
    """The only surface the rest of the codebase depends on."""

    async def chat(self, prompt: str, *, system: str | None = None,
                   model: str | None = None) -> str: ...

    async def json(self, prompt: str, schema: dict | None = None, *,
                   system: str | None = None, model: str | None = None) -> dict: ...


def _looks_like_rate_limit(exc: Exception) -> bool:
    text = repr(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429 or "429" in text or "rate" in text or "too many requests" in text


async def _with_backoff(factory: Callable[[], Awaitable[Any]]) -> Any:
    """Run an awaitable factory, retrying rate limits with exponential backoff + jitter."""
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await factory()
        except Exception as e:
            last = e
            if not _looks_like_rate_limit(e) or attempt == _MAX_RETRIES - 1:
                raise
            delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, _BASE_DELAY)
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def _pick(source: Any, prompt: str, default: Any) -> Any:
    """Resolve a scripted FakeLLM response: deque (FIFO), callable, or a plain value."""
    if source is None:
        return default
    if isinstance(source, deque):
        return source.popleft() if source else default
    if callable(source):
        return source(prompt)
    return source


class FakeLLM:
    """Scripted, offline LLM for tests. No network, ever.

    `chat` / `json` each accept a single value (returned every call), a list (consumed FIFO),
    or a callable `(prompt) -> response`.
    """

    def __init__(self, *, chat: Any = None, json: Any = None) -> None:
        self._chat = deque(chat) if isinstance(chat, list) else chat
        self._json = deque(json) if isinstance(json, list) else json
        self.chat_prompts: list[str] = []
        self.json_prompts: list[str] = []

    async def chat(self, prompt: str, *, system: str | None = None,
                   model: str | None = None) -> str:
        self.chat_prompts.append(prompt)
        return str(_pick(self._chat, prompt, ""))

    async def json(self, prompt: str, schema: dict | None = None, *,
                   system: str | None = None, model: str | None = None) -> dict:
        self.json_prompts.append(prompt)
        out = _pick(self._json, prompt, {})
        if isinstance(out, str):
            out = _jsonlib.loads(out)
        return dict(out)


class MistralLLM:
    """Real wrapper. Only constructed when the `llm` extra is installed and a key exists."""

    def __init__(self, api_key: str | None = None, *, model: str = DEFAULT_MODEL,
                 max_concurrency: int = _MAX_CONCURRENCY) -> None:
        if not _HAVE_MISTRAL:  # pragma: no cover
            raise RuntimeError("mistralai not installed; `pip install baduser[llm]`")
        key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not key:  # pragma: no cover
            raise RuntimeError("MISTRAL_API_KEY not set")
        self._client = Mistral(api_key=key)
        self._model = model
        self._sem = asyncio.Semaphore(max_concurrency)

    async def _complete(self, messages: list[dict], model: str | None,
                        json_mode: bool) -> str:  # pragma: no cover - needs network
        kwargs: dict[str, Any] = {"model": model or self._model, "messages": messages}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        async def factory() -> Any:
            # mistralai's sync client is robust across versions; run it off the event loop.
            return await asyncio.to_thread(self._client.chat.complete, **kwargs)

        async with self._sem:
            resp = await _with_backoff(factory)
        return resp.choices[0].message.content

    async def chat(self, prompt: str, *, system: str | None = None,
                   model: str | None = None) -> str:  # pragma: no cover - needs network
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        return await self._complete(messages, model, json_mode=False)

    async def json(self, prompt: str, schema: dict | None = None, *,
                   system: str | None = None,
                   model: str | None = None) -> dict:  # pragma: no cover - needs network
        sys = system or "Respond with a single valid JSON object and nothing else."
        if schema:
            sys += f" It must conform to this JSON schema: {_jsonlib.dumps(schema)}"
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": prompt}]
        raw = await self._complete(messages, model, json_mode=True)
        return _jsonlib.loads(raw)
