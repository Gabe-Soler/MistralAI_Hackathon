"""FastAPI: GET /state, GET /stream (SSE), POST /answer. See PLAN.md sections 16 & 11b.

Runs on the SAME loop as the pipeline (PLAN 16 "Process model") -- the bus is in-memory,
so a two-process design would give /stream a different Bus and it would emit nothing forever.

Secrets never leave here: /state returns PublicManifest, never Manifest (PLAN 11b rule 2).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
)
from .store import Store


class AnswerBody(BaseModel):
    question_id: str
    answer: str


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


def create_app(state: EngineState) -> FastAPI:
    app = FastAPI()
    app.state.engine = state

    # dashboard->engine IS a CORS request (PLAN 16; §8's "no CORS" is engine->target only).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        """The dev dashboard: one self-contained file, no build step, served same-origin
        so fetch("state") and new EventSource("stream") need no configuration."""
        return (Path(__file__).parent / "static" / "dashboard.html").read_text()

    @app.get("/shots/{name}")
    def shot(name: str):
        """Serve a Browser Use frame. Screenshots are captured headless too -- headless
        only removes the visible window, the page is still rendered and still captured."""
        from fastapi.responses import FileResponse

        # No traversal: the name must be a bare filename inside this run's shots dir.
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(status_code=400, detail="bad name")
        if state.store is None:
            raise HTTPException(status_code=404, detail="no store")
        path = state.store.shots / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no such shot")
        return FileResponse(path)

    @app.get("/state")
    def get_state() -> dict:
        return {
            "config": state.config.model_dump(mode="json") if state.config else None,
            "ground_truth": (
                state.ground_truth.model_dump(mode="json") if state.ground_truth else None
            ),
            # NEVER Manifest -- PublicManifest drops every credential (PLAN 11b rule 2).
            "manifest": PublicManifest.of(state.manifest).model_dump(mode="json"),
            "findings": [json.loads(f.model_dump_json()) for f in state.findings],
            "phase": state.phase,
        }

    @app.get("/stream")
    async def stream(request: Request) -> EventSourceResponse:
        since = int(request.headers.get("last-event-id") or 0)
        q = state.bus.subscribe(since=since)

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

    @app.post("/answer")
    def post_answer(body: AnswerBody) -> dict:
        state.answer(body.question_id, body.answer)
        return {"ok": True}

    return app
