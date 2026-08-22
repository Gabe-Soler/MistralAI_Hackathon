"""The web channel: a real browser driven in natural language. PLAN.md sections 13 and 11c.

`browser-use` is an OPTIONAL dependency (`pip install baduser[web]`). This module always
imports; the error only fires when you actually try to act without it installed, so the api
and chat channels keep working on a machine with no browser.

Two things this adapter does that the PLAN sketch does not:

  * `history.final_result()` is an LLM SUMMARY and drops canaries by design (PLAN 14,
    "Summarization and truncation"). The oracle scans `Result.raw`, so raw gets the DOM /
    extracted page text FIRST and the summary appended; the summary is kept separately in
    `Result.extracted["summary"]` for the dashboard.
  * The password never enters `step.action` (PLAN 11b rule 4). It is looked up from the
    manifest and handed to Browser Use as `sensitive_data`, so the task text the LLM sees
    contains only the placeholder `x_secret`.
"""

from __future__ import annotations

import base64
import contextlib
import uuid
from pathlib import Path
from typing import Any

from ..models import Manifest, Persona, Result, Step
from ..prompts import IDENTITY, SAFETY, probe_task
from . import cap

try:  # optional dependency -- guarded so the module always imports
    from browser_use import Agent, ChatMistral  # type: ignore

    _IMPORT_ERROR: str | None = None
except Exception as e:  # noqa: BLE001  # pragma: no cover - any import failure means "unavailable"
    Agent = ChatMistral = None  # type: ignore
    _IMPORT_ERROR = repr(e)

MISSING = (
    "browser-use is not installed, so the web channel cannot act. "
    "Install it with `uv sync --extra web` (or drop Channel.web from SessionConfig.channels). "
    "Original import error: {}"
)


def _call(obj: Any, name: str, default: Any = None) -> Any:
    """Browser Use's history API has moved between versions; never crash on a missing bit."""
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        return fn() if callable(fn) else fn
    except Exception:  # noqa: BLE001 - a missing history field is not a step failure
        return default


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(v) for v in value if v)
    return str(value)


class WebAdapter:
    """NL browser steps. Also the seeding channel for an app we have never seen (PLAN 11c)."""

    def __init__(
        self,
        target: str,
        manifest: Manifest,
        shots: str | Path = "shots",
        *,
        agent_factory=None,   # (task, sensitive_data) -> agent with `async run()`; tests inject
        model: str = "mistral-large-latest",
        headless: bool = True,  # headless still renders and still screenshots -- only
                                # the visible window goes away. --show-browser brings it
                                # back when you want the browser itself on camera.
        max_steps: int = 12,
    ):
        self.target = (target or "").rstrip("/")
        self.manifest = manifest
        self.shots = Path(shots)
        self.agent_factory = agent_factory
        self.model = model
        self.headless = headless
        self.max_steps = max_steps
        self._last_agent: Any = None

    # ---- task construction ----

    def compose(self, step: Step, persona: Persona | None) -> tuple[str, dict[str, str]]:
        """Task text + sensitive_data. The secret is in the second half only.

        A pre-composed task (from baduser.prompts) passes through untouched -- world.py
        builds signup/create tasks itself. A bare instruction gets wrapped as a probe.
        """
        task = step.action if SAFETY in step.action else probe_task(self.target, step.action)
        lines = [task]
        sensitive: dict[str, str] = {}
        if persona is not None:
            sensitive = {
                "x_email": persona.email,
                "x_secret": persona.credentials.reveal()["secret"],
            }
            # Identity is orthogonal to task type: an attack step may still need to log in.
            # signup_task already carries it, so only add it when it is absent.
            if "x_email" not in task:
                lines.append(IDENTITY)
        return "\n".join(lines), sensitive

    def build_agent(self, task: str, sensitive: dict[str, str]) -> Any:
        if self.agent_factory is not None:
            return self.agent_factory(task, sensitive)
        if Agent is None:
            raise RuntimeError(MISSING.format(_IMPORT_ERROR))
        return Agent(
            task=task,
            llm=ChatMistral(model=self.model),
            sensitive_data=sensitive or None,
            headless=self.headless,
        )

    # ---- destructive-action guard -------------------------------------------------
    # The prompt (prompts.SAFETY) is guidance; this is the control. Checked against every
    # URL the agent actually visited, so a model that ignores the instruction still cannot
    # complete a destructive navigation without it being recorded as an error.

    BANNED_URL = ("delete", "destroy", "remove", "purge", "wipe", "deactivate", "cancel")

    def check_urls(self, urls: list[str]) -> str | None:
        for u in urls:
            low = u.lower()
            for bad in self.BANNED_URL:
                if bad in low:
                    return f"destructive navigation blocked: {bad!r} in {u}"
        return None

    # ---- the interface ----

    async def act(self, step: Step) -> Result:
        try:
            persona = self.manifest.persona(step.persona_id)
            task, sensitive = self.compose(step, persona)
            agent = self.build_agent(task, sensitive)
            self._last_agent = agent
            history = await agent.run(max_steps=self.max_steps) if _takes_max_steps(agent) \
                else await agent.run()

            summary = _as_text(_call(history, "final_result"))
            # DOM/extracted text first: the summary is lossy and the oracle scans raw.
            page_text = _as_text(_call(history, "extracted_content"))
            raw = "\n".join(p for p in (page_text, summary) if p)
            urls = [str(u) for u in (_call(history, "urls") or []) if u]

            # The control, not the prompt. A destructive navigation is an ERROR, so it is
            # loud (Verdict.error) rather than a quiet green tile.
            violation = self.check_urls(urls)
            if violation is not None:
                return Result(error=violation, raw=cap(raw), extracted={"urls": urls})

            return Result(
                raw=cap(raw),
                screenshot=self._save_shot(history, step),
                extracted={"summary": summary, "urls": urls},
            )
        except Exception as e:  # noqa: BLE001 - a crashed browser is Verdict.error, not benign
            return Result(error=repr(e))

    # ---- extras used by world.py seeding (PLAN 11c: hand the session to the fast channels) ----

    async def export_cookies(self) -> dict[str, str]:
        """Best-effort cookie grab from the last run's browser session. {} when unavailable."""
        session = getattr(self._last_agent, "browser_session", None) or getattr(
            self._last_agent, "browser_context", None
        )
        getter = getattr(session, "get_cookies", None)
        if getter is None:
            return {}
        try:
            cookies = await getter()
        except Exception:  # noqa: BLE001 - cookie export is best-effort
            return {}
        return {
            c["name"]: c["value"]
            for c in (cookies or [])
            if isinstance(c, dict) and "name" in c
        }

    async def aclose(self) -> None:
        closer = getattr(self._last_agent, "close", None)
        if closer is not None:
            with contextlib.suppress(Exception):  # browser teardown is best-effort
                await closer()

    # ---- screenshots ----

    def _save_shot(self, history: Any, step: Step) -> str | None:
        """Write the last frame into the run's shots/ dir; StepFinished.shot points at it."""
        frames = _call(history, "screenshots") or _call(history, "screenshot_paths") or []
        if isinstance(frames, (str, bytes)):
            frames = [frames]
        frames = [f for f in frames if f]
        if not frames:
            return None
        last = frames[-1]
        try:
            if isinstance(last, str) and last.endswith(".png") and Path(last).exists():
                return last  # already a file on disk
            data = last if isinstance(last, bytes) else base64.b64decode(_strip_data_url(last))
            self.shots.mkdir(parents=True, exist_ok=True)
            path = self.shots / f"{step.id or uuid.uuid4().hex[:8]}.png"
            path.write_bytes(data)
            return str(path)
        except Exception:  # noqa: BLE001 - a missing screenshot must never fail the step
            return None


def _strip_data_url(s: str) -> str:
    return s.split(",", 1)[1] if s.startswith("data:") else s


def _takes_max_steps(agent: Any) -> bool:
    import inspect

    try:
        return "max_steps" in inspect.signature(agent.run).parameters
    except (TypeError, ValueError):
        return False
