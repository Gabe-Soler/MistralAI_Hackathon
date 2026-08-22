from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from baduser.bus import Bus
from baduser.models import (
    Credentials,
    Manifest,
    Persona,
    PhaseEvent,
    PublicManifest,
    QuestionEvent,
    SessionConfig,
    Tenant,
)
from baduser.server import EngineState, create_app, sse_event

SECRET = "SuperSecret-do-not-leak-123"


def _cfg() -> SessionConfig:
    return SessionConfig(run_id="testrun", target="http://localhost:3000")


def _state_with_secret() -> EngineState:
    state = EngineState(_cfg(), Bus())
    state.manifest = Manifest(
        tenants=[Tenant(id="t1", name="Acme", canary="BU7Q4KX2")],
        personas=[
            Persona(
                id="p1", tenant_id="t1", role="admin", name="Alice", email="alice@acme.test",
                credentials=Credentials(username="alice", secret=SecretStr(SECRET)),
            )
        ],
    )
    return state


def test_state_never_contains_password():
    state = _state_with_secret()
    client = TestClient(create_app(state))
    r = client.get("/state")
    assert r.status_code == 200
    # The literal secret must be absent from the whole response body (PLAN 11b rule 2).
    assert SECRET not in r.text
    body = r.json()
    assert body["phase"] == "reading"
    assert body["manifest"]["personas"][0]["name"] == "Alice"
    # It's a PublicManifest -- no credentials key at all.
    assert "credentials" not in body["manifest"]["personas"][0]
    assert set(body["manifest"]["personas"][0]) == set(PublicManifest.of(state.manifest)
                                                        .personas[0].model_dump())


def test_sse_event_sets_id_to_seq():
    ev = PhaseEvent(phase="attacking")
    sse = sse_event(42, ev)
    assert sse.id == "42"
    assert '"phase":"attacking"' in sse.data


async def _collect_stream(app, last_event_id=None, want=1, timeout=5):  # noqa: ASYNC109
    """Drive /stream as raw ASGI and disconnect after `want` body chunks.

    TestClient and httpx's ASGITransport both buffer the WHOLE response before returning,
    which never completes on an endless SSE stream -- so we drive the ASGI app directly and
    send an http.disconnect once we've captured what we need.
    """
    headers = [(b"accept", b"text/event-stream")]
    if last_event_id is not None:
        headers.append((b"last-event-id", str(last_event_id).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/stream", "raw_path": b"/stream", "query_string": b"",
        "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    bodies: list[bytes] = []
    start: dict = {}
    disconnect = asyncio.Event()
    done = asyncio.Event()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            start["status"] = msg["status"]
            start["headers"] = {k.decode(): v.decode() for k, v in msg["headers"]}
        elif msg["type"] == "http.response.body" and msg.get("body"):
            bodies.append(msg["body"])
            if len(bodies) >= want:
                disconnect.set()

    async def run():
        try:
            await app(scope, receive, send)
        finally:
            done.set()

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    finally:
        disconnect.set()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
    return start, b"".join(bodies).decode()


async def test_stream_emits_ids():
    state = EngineState(_cfg(), Bus())
    state.bus.emit(PhaseEvent(phase="reading"))  # logged, so a fresh subscribe replays it
    start, text = await _collect_stream(create_app(state), want=1)
    assert start["status"] == 200
    assert "text/event-stream" in start["headers"]["content-type"]
    # `id:` is the bus seq; `data:` is the event JSON; default `message` event name.
    assert "id: 1" in text
    assert '"phase":"reading"' in text
    assert "event:" not in text  # stays the default `message` (PLAN 16 wire contract)


async def test_stream_uses_last_event_id_header():
    state = EngineState(_cfg(), Bus())
    state.bus.emit(PhaseEvent(phase="reading"))
    state.bus.emit(PhaseEvent(phase="seeding"))
    # since=1 -> first replayed event is seq 2, not 1.
    _, text = await _collect_stream(create_app(state), last_event_id=1, want=1)
    assert "id: 2" in text
    assert '"phase":"seeding"' in text
    assert "id: 1" not in text


def test_answer_unknown_question_404():
    state = EngineState(_cfg(), Bus())
    client = TestClient(create_app(state))
    r = client.post("/answer", json={"question_id": "nope", "answer": "y"})
    assert r.status_code == 404


async def test_ask_resolves_via_answer():
    state = EngineState(_cfg(), Bus())
    task = asyncio.create_task(state.ask(QuestionEvent(id="q1", text="ok?"), timeout=5))
    await asyncio.sleep(0)  # let ask register the future + emit the QuestionEvent
    state.answer("q1", "y")
    assert await task == "y"


async def test_ask_double_answer_is_409_not_500():
    state = EngineState(_cfg(), Bus())
    task = asyncio.create_task(state.ask(QuestionEvent(id="q1", text="ok?"), timeout=5))
    await asyncio.sleep(0)
    state.answer("q1", "y")  # first answerer wins
    with pytest.raises(HTTPException) as e:
        state.answer("q1", "n")  # done() guard -> 409, never InvalidStateError 500
    assert e.value.status_code == 409
    assert await task == "y"


async def test_ask_timeout_returns_safe_default():
    state = EngineState(_cfg(), Bus())
    # Unanswered => default (PLAN 7: an unanswered question must not invent an invariant).
    result = await state.ask(QuestionEvent(id="q1", text="ok?"), timeout=0.05, default=None)
    assert result is None
    # And the question expired: a late answer now 404s, not 409/500.
    with pytest.raises(HTTPException) as e:
        state.answer("q1", "y")
    assert e.value.status_code == 404
