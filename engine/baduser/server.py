"""FastAPI: the dashboard API. See PLAN.md sections 16 & 11b.

Runs on the SAME loop as the pipeline (PLAN 16 "Process model") -- the bus is in-memory,
so a two-process design would give /stream a different Bus and it would emit nothing forever.

Two route sets over the same handlers:

  /api/runs, /api/{run_id}/{state,stream,answer}   run-scoped; what the SPA uses
  /state, /stream, /answer                         the current run; kept because
                                                   test_server.py pins them and
                                                   static/dashboard.html calls them
                                                   with relative URLs

Secrets never leave here: /state returns PublicManifest, never Manifest (PLAN 11b rule 2).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from .bus import Bus
from .models import (
    Event,
    Finding,
    GroundTruth,
    Manifest,
    PublicManifest,
    QuestionEvent,
    SessionConfig,
    Verdict,
)
from .store import Store


class AnswerBody(BaseModel):
    question_id: str
    answer: str


class RunSummary(BaseModel):
    """One row in the run picker. Cheap enough to build for every archived directory."""

    run_id: str
    started_at: str = ""
    target: str = ""
    phase: str = "done"
    phase_detail: str = ""
    findings: int = 0
    breaches: int = 0
    errors: int = 0
    live: bool = False


def _since(request: Request) -> int:
    """Resume point for a reconnecting client, from the header the browser sends itself.

    Guarded: a bare int() here 500s on any non-numeric Last-Event-ID, and EventSource
    replays whatever it last saw -- so one malformed value would wedge a client into a
    reconnect loop that fails forever. Falling back to 0 replays the run instead, which is
    always correct because the bus log is complete.
    """
    raw = request.headers.get("last-event-id") or request.query_params.get("since") or "0"
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def sse_event(seq: int, ev: Event) -> ServerSentEvent:
    """The wire format (PLAN 16): `id:` is the bus seq, `event:` stays default `message`
    so the dashboard's es.onmessage fires, `data:` is the discriminated-union JSON."""
    return ServerSentEvent(id=str(seq), data=ev.model_dump_json())


class EngineState:
    """The single source of truth the server reads and the pipeline writes."""

    def __init__(self, config: SessionConfig, bus: Bus, store: Store | None = None) -> None:
        self.config = config
        self.bus = bus
        self.store = store
        self.ground_truth: GroundTruth | None = None
        self.manifest: Manifest = Manifest()
        self.findings: list[Finding] = []
        self.phase: str = "reading"
        self.phase_detail: str = ""
        self._pending: dict[str, asyncio.Future] = {}

    async def ask(
        self,
        question: QuestionEvent | str,
        timeout: float = 60,  # noqa: ASYNC109 - public API shape; wait_for is the impl
        default: str | None = None,
    ) -> str | None:
        """Emit a QuestionEvent and await POST /answer. On timeout return `default`
        (PLAN 7: an unanswered question must NOT invent an invariant)."""
        if isinstance(question, str):
            question = QuestionEvent(id=uuid4().hex[:8], text=question)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[question.id] = fut
        self.bus.emit(question)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            # Expire it so a late answer gets 404 rather than resolving a stale question.
            self._pending.pop(question.id, None)
            return default

    def answer(self, question_id: str, answer: str) -> None:
        fut = self._pending.get(question_id)
        if fut is None:
            raise HTTPException(status_code=404, detail="unknown question")
        if fut.done():
            # done() guard: a second answerer gets 409, not an InvalidStateError 500.
            raise HTTPException(status_code=409, detail="already answered")
        fut.set_result(answer)

    def summary(self) -> RunSummary:
        return RunSummary(
            run_id=self.config.run_id,
            started_at=_started_at(self.config.run_id),
            target=self.config.target,
            phase=self.phase,
            phase_detail=self.phase_detail,
            findings=len(self.findings),
            breaches=sum(1 for f in self.findings if f.verdict == Verdict.breach),
            errors=sum(1 for f in self.findings if f.verdict == Verdict.error),
            live=True,
        )


def _started_at(run_id: str) -> str:
    """`run_id` IS the timestamp (cli.py builds it with %Y%m%d-%H%M%S).

    Derived rather than stored so no model gains a datetime field -- nothing else on this
    wire has one, and the dashboard orders by the SSE seq.
    """
    with contextlib.suppress(ValueError):
        stamp = _dt.datetime.strptime(run_id, "%Y%m%d-%H%M%S").replace(tzinfo=_dt.UTC)
        return stamp.isoformat()
    return ""


def _archived_summary(run_dir: Path) -> RunSummary | None:
    """Build a row from a finished run's files, without loading its whole event log.

    Every read is individually guarded: a run killed mid-seed has a config and an events
    log but no findings.json, and it should still list rather than break the picker.
    """
    if not run_dir.is_dir():
        return None
    s = RunSummary(run_id=run_dir.name, started_at=_started_at(run_dir.name))

    with contextlib.suppress(OSError, ValueError):
        s.target = json.loads((run_dir / "config.json").read_text()).get("target", "")

    with contextlib.suppress(OSError, ValueError):
        found = json.loads((run_dir / "findings.json").read_text())
        s.findings = len(found)
        s.breaches = sum(1 for f in found if f.get("verdict") == "breach")
        s.errors = sum(1 for f in found if f.get("verdict") == "error")

    # Last phase event, from the tail only -- reading a whole run per row would make the
    # picker O(total events) for something shown before anything is clicked.
    with contextlib.suppress(OSError), (run_dir / "events.jsonl").open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 8192))
        for line in reversed(fh.read().decode("utf-8", "replace").splitlines()):
            if '"type":"phase"' not in line:
                continue
            with contextlib.suppress(ValueError):
                ev = json.loads(line)
                s.phase = ev.get("phase", s.phase)
                s.phase_detail = ev.get("detail", "")
            break
    return s


class Registry:
    """Every run this process can serve, keyed by run_id.

    `root` is resolved ONCE at construction. `.baduser/` is cwd-relative (store.py) while
    `bad-user` is installed globally and told to run from anywhere, so resolving lazily
    would let a later cwd change silently point the picker at a different history.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or ".baduser").resolve()
        self.runs: dict[str, EngineState] = {}
        self.current: str | None = None

    def add(self, state: EngineState) -> None:
        self.runs[state.config.run_id] = state
        self.current = state.config.run_id

    def get(self, run_id: str) -> EngineState:
        state = self.runs.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        return state

    def current_state(self) -> EngineState:
        if self.current is None:
            raise HTTPException(status_code=404, detail="no run in this process")
        return self.runs[self.current]

    def summaries(self) -> list[RunSummary]:
        """Live runs first, then archived directories, newest first, deduped by run_id."""
        out = {rid: st.summary() for rid, st in self.runs.items()}
        runs_dir = self.root / "runs"
        if runs_dir.is_dir():
            with contextlib.suppress(OSError):
                for d in runs_dir.iterdir():
                    if d.name in out:
                        continue  # a live run's in-memory state is always fresher
                    if (s := _archived_summary(d)) is not None:
                        out[d.name] = s
        return sorted(out.values(), key=lambda s: s.run_id, reverse=True)


def _state_payload(state: EngineState) -> dict:
    return {
        "config": state.config.model_dump(mode="json") if state.config else None,
        "ground_truth": (
            state.ground_truth.model_dump(mode="json") if state.ground_truth else None
        ),
        # NEVER Manifest -- PublicManifest drops every credential (PLAN 11b rule 2).
        "manifest": PublicManifest.of(state.manifest).model_dump(mode="json"),
        "findings": [json.loads(f.model_dump_json()) for f in state.findings],
        "phase": state.phase,
        # Why, for a client that connected AFTER the run ended: the reason is otherwise
        # only in the event stream, so a late page load sees "failed" with no cause.
        "phase_detail": state.phase_detail,
    }


def _stream_response(state: EngineState, request: Request) -> EventSourceResponse:
    q = state.bus.subscribe(since=_since(request))

    async def gen():
        try:
            while True:
                seq, ev = await q.get()
                yield sse_event(seq, ev)
        finally:
            state.bus.unsubscribe(q)  # PLAN 16: unsubscribe in finally

    return EventSourceResponse(
        gen(),
        ping=15,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _shot_response(state: EngineState, name: str) -> FileResponse:
    """Serve a Browser Use frame. Screenshots are captured headless too -- headless only
    removes the visible window, the page is still rendered and still captured.

    Two layers of traversal defence. The string check rejects the obvious shapes and is
    what returns 400; resolve() + is_relative_to() then neutralises anything that survives
    it -- symlinks, absolute paths, encodings the first check did not anticipate.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="bad name")
    if state.store is None:
        raise HTTPException(status_code=404, detail="no store")
    root = state.store.shots.resolve()
    path = (root / name).resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail="no such shot")
    return FileResponse(path)


def create_app(state: EngineState | None = None, *, registry: Registry | None = None) -> FastAPI:
    """Serve one run or many.

    The positional `state` form is what cli.py and test_server.py use: it wraps the single
    run in a one-entry registry so both route sets work without either caller changing.
    """
    if registry is None:
        registry = Registry(state.store.dir.parent.parent if state and state.store else None)
    if state is not None:
        registry.add(state)

    app = FastAPI()
    app.state.engine = registry.runs.get(registry.current) if registry.current else None
    app.state.registry = registry

    # dashboard->engine IS a CORS request (PLAN 16; §8's "no CORS" is engine->target only).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    api = APIRouter(prefix="/api")

    # Declared before /{run_id}/... so a run literally named "runs" can never shadow it.
    @api.get("/runs")
    def list_runs() -> list[RunSummary]:
        return registry.summaries()

    @api.get("/{run_id}/state")
    def run_state(run_id: str) -> dict:
        return _state_payload(registry.get(run_id))

    @api.get("/{run_id}/stream")
    async def run_stream(run_id: str, request: Request) -> EventSourceResponse:
        return _stream_response(registry.get(run_id), request)

    @api.get("/{run_id}/shots/{name}")
    def run_shot(run_id: str, name: str) -> FileResponse:
        return _shot_response(registry.get(run_id), name)

    @api.post("/{run_id}/answer")
    def run_answer(run_id: str, body: AnswerBody) -> dict:
        registry.get(run_id).answer(body.question_id, body.answer)
        return {"ok": True}

    app.include_router(api)

    @app.get("/dev", response_class=HTMLResponse)
    def dashboard() -> str:
        """The dev dashboard: one self-contained file, no build step, served same-origin
        so fetch("state") and new EventSource("stream") need no configuration.

        Kept after the SPA landed. It needs no npm, so it is the fallback when the bundle
        is missing or broken -- and its relative fetch("state") still resolves to /state
        from here, because /dev has no trailing slash.
        """
        return (Path(__file__).parent / "static" / "dashboard.html").read_text()

    # --- the current run, unprefixed: pinned by test_server.py, used by dashboard.html ---

    @app.get("/state")
    def get_state() -> dict:
        return _state_payload(registry.current_state())

    @app.get("/stream")
    async def stream(request: Request) -> EventSourceResponse:
        return _stream_response(registry.current_state(), request)

    @app.get("/shots/{name}")
    def shot(name: str) -> FileResponse:
        return _shot_response(registry.current_state(), name)

    @app.post("/answer")
    def post_answer(body: AnswerBody) -> dict:
        registry.current_state().answer(body.question_id, body.answer)
        return {"ok": True}

    _mount_spa(app)
    return app


# Paths the SPA catch-all must never answer for. Registration order already protects them
# -- starlette matches in order -- but the guard is what makes /api/typo return JSON
# rather than index.html. A fetch that receives HTML fails at JSON.parse with a message
# naming neither the route nor the cause, which is an hour of debugging for one line here.
_RESERVED = ("api", "openapi.json", "docs", "redoc", "dev", "state", "stream", "answer",
             "shots")


def _mount_spa(app: FastAPI) -> None:
    """Serve the built SPA, if `make web` has produced one.

    When it has not, / falls back to the dev dashboard rather than 404ing, so `bad-user`
    is useful with no npm involvement at all.
    """
    web = Path(__file__).parent / "web"
    index = web / "index.html"

    if not index.is_file():
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def _no_spa() -> str:
            return (Path(__file__).parent / "static" / "dashboard.html").read_text()

        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        head = full_path.split("/", 1)[0]
        if head in _RESERVED:
            raise HTTPException(status_code=404, detail=f"no such route: /{full_path}")
        # A real file (assets/, favicon, the cat gifs) is served as itself; anything else
        # is a client route like /<run_id> and gets index.html.
        candidate = (web / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(web.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
