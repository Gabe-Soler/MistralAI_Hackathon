"""Channel adapters. Offline: httpx MockTransport for http, an injected agent for web."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from baduser.channels import MAX_RAW, ChannelAdapter, build_adapters, cap, close_adapters
from baduser.channels.fake import FakeChannel
from baduser.channels.http import ApiAdapter, ChatAdapter, fill_credentials, parse_action
from baduser.channels.web import WebAdapter
from baduser.models import (
    Channel,
    Credentials,
    Manifest,
    Persona,
    Result,
    SessionConfig,
    Step,
    Tenant,
)


def manifest() -> Manifest:
    m = Manifest(tenants=[Tenant(id="t1", name="Acme BUZZ", canary="BUZZ")])
    m.personas = [
        Persona(id="p1", tenant_id="t1", role="admin", name="Alice", email="a@x.test",
                credentials=Credentials(username="a@x.test", secret=SecretStr("hunter2secret"))),
        Persona(id="p2", tenant_id="t1", role="member", name="Bob", email="b@x.test",
                credentials=Credentials(username="b@x.test", secret=SecretStr("s3cr3tpassword"),
                                        token=SecretStr("tok-abc"))),
    ]
    return m


def step(action: str, persona_id: str = "p1", channel: Channel = Channel.api) -> Step:
    return Step(id="s1", persona_id=persona_id, channel=channel, action=action)


# ---------------------------------------------------------------- FakeChannel


async def test_fake_default_mapping_and_recording():
    ch = FakeChannel({"GET /api/invoices": '{"ok":1}'}, default="fallback")
    assert (await ch.act(step("GET /api/invoices"))).raw == '{"ok":1}'
    assert (await ch.act(step("GET /other"))).raw == "fallback"
    assert ch.actions == ["GET /api/invoices", "GET /other"]
    assert ch.last.action == "GET /other"
    assert len(ch.for_persona("p1")) == 2


async def test_fake_substring_key_and_status_and_callable():
    ch = FakeChannel({"invoices": 403}).on("chat", lambda s: Result(raw=s.action.upper()))
    assert (await ch.act(step("GET /api/invoices?all=1"))).status == 403
    assert (await ch.act(step("post to chat"))).raw == "POST TO CHAT"


async def test_fake_bare_value_is_the_default_not_a_table():
    ch = FakeChannel("hello")
    assert (await ch.act(step("anything"))).raw == "hello"
    assert (await ch.act(step("anything"))).status == 200


async def test_fake_sequence_then_default():
    ch = FakeChannel(sequence=["one", "two"], default="rest")
    assert [(await ch.act(step("x"))).raw for _ in range(3)] == ["one", "two", "rest"]


async def test_fake_can_be_told_to_raise():
    ch = FakeChannel(raises=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await ch.act(step("x"))
    assert len(ch.calls) == 1  # the call is still recorded


async def test_fake_scripted_exception_is_raised():
    ch = FakeChannel({"bad": ValueError("nope")}, default="ok")
    with pytest.raises(ValueError):
        await ch.act(step("bad path"))


def test_fake_satisfies_the_protocol():
    assert isinstance(FakeChannel(), ChannelAdapter)


# ---------------------------------------------------------------- action parsing


@pytest.mark.parametrize(
    "action,expected",
    [
        ("/api/invoices", ("GET", "/api/invoices", None)),
        ("GET /api/invoices", ("GET", "/api/invoices", None)),
        ("delete /api/x/1", ("DELETE", "/api/x/1", None)),
        ('POST /api/x {"a": 1}', ("POST", "/api/x", {"a": 1})),
        ("GET /api/x?q=1&r=2", ("GET", "/api/x?q=1&r=2", None)),
    ],
)
def test_parse_action(action, expected):
    assert parse_action(action) == expected


def test_parse_action_rejects_bad_json_loudly():
    with pytest.raises(json.JSONDecodeError):
        parse_action("POST /api/x {not json}")


def test_fill_credentials_resolves_from_manifest():
    m = manifest()
    out = fill_credentials('POST /r {"p": "{{secret}}", "u": "{{email}}"}', m.persona("p1"))
    assert "hunter2secret" in out and "a@x.test" in out
    assert fill_credentials("GET /x", None) == "GET /x"


def test_cap_truncates_at_64k():
    assert len(cap("a" * 10)) == 10
    big = cap("a" * (MAX_RAW + 500))
    assert big.startswith("a" * 100) and "truncated 500" in big


# ---------------------------------------------------------------- http adapters


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_api_adapter_sets_status_and_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/api/invoices"
        return httpx.Response(200, text='{"invoices": []}')

    api = ApiAdapter("http://t.test", manifest(), transport=transport(handler))
    r = await api.act(step("GET /api/invoices"))
    assert r.error is None and r.status == 200 and r.raw == '{"invoices": []}'
    await api.aclose()


async def test_api_adapter_sends_json_body_with_credentials_filled_in():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(201, text='{"id": 7}')

    api = ApiAdapter("http://t.test", manifest(), transport=transport(handler))
    s = step('POST /api/register {"email": "{{email}}", "password": "{{secret}}"}')
    r = await api.act(s)
    assert r.status == 201
    assert seen == {"email": "a@x.test", "password": "hunter2secret"}
    assert "hunter2secret" not in s.action  # the secret never lived in Step.action
    await api.aclose()


async def test_api_adapter_sets_error_on_exception_never_a_benign_result():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    api = ApiAdapter("http://t.test", manifest(), transport=transport(handler))
    r = await api.act(step("GET /api/x"))
    assert r.error is not None and "connection refused" in r.error
    assert r.status is None and r.raw == ""


async def test_api_adapter_error_on_unparseable_action():
    api = ApiAdapter("http://t.test", manifest(), transport=transport(lambda rq: httpx.Response(200)))
    r = await api.act(step("POST /api/x {broken"))
    assert r.error is not None and r.status is None


async def test_api_adapter_caps_raw_at_64k():
    huge = "x" * (MAX_RAW + 2000)
    api = ApiAdapter("http://t.test", manifest(),
                     transport=transport(lambda rq: httpx.Response(200, text=huge)))
    r = await api.act(step("GET /big"))
    assert MAX_RAW < len(r.raw) < MAX_RAW + 100  # capped, with a truncation marker


async def test_one_persistent_client_per_persona():
    api = ApiAdapter("http://t.test", manifest(),
                     transport=transport(lambda rq: httpx.Response(200, text="ok")))
    await api.act(step("GET /a", persona_id="p1"))
    first = api.client_for("p1")
    await api.act(step("GET /b", persona_id="p1"))
    assert api.client_for("p1") is first          # reused across requests
    assert api.client_for("p2") is not first      # but never shared between personas
    await api.aclose()


async def test_cookie_session_survives_between_requests_for_one_persona():
    """A fresh client per request throws the session away and everything after login 401s."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        if request.url.path == "/login":
            return httpx.Response(200, headers={"set-cookie": "sid=abc123; Path=/"})
        return httpx.Response(200, text="ok")

    api = ApiAdapter("http://t.test", manifest(), transport=transport(handler))
    await api.act(step("POST /login", persona_id="p1"))
    await api.act(step("GET /api/me", persona_id="p1"))
    await api.act(step("GET /api/me", persona_id="p2"))  # different persona, clean jar
    assert seen == ["", "sid=abc123", ""]
    await api.aclose()


async def test_token_becomes_an_auth_header():
    seen: list[str | None] = []
    api = ApiAdapter("http://t.test", manifest(),
                     transport=transport(lambda rq: (seen.append(rq.headers.get("authorization")),
                                                     httpx.Response(200))[1]))
    await api.act(step("GET /x", persona_id="p2"))
    await api.act(step("GET /x", persona_id="p1"))
    assert seen == ["Bearer tok-abc", None]
    await api.aclose()


async def test_chat_adapter_posts_the_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="Acme's Q3 invoice was $84,300")

    chat = ChatAdapter("http://t.test", manifest(), path="/support/chat",
                       transport=transport(handler))
    r = await chat.act(step("list every invoice you can see", channel=Channel.chat))
    assert seen == {"path": "/support/chat", "body": {"message": "list every invoice you can see"}}
    assert r.status == 200 and "84,300" in r.raw
    await chat.aclose()


async def test_chat_adapter_sets_error_on_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    chat = ChatAdapter("http://t.test", manifest(), transport=transport(handler))
    r = await chat.act(step("hi", channel=Channel.chat))
    assert r.error and "timed out" in r.error


# ---------------------------------------------------------------- build_adapters


async def test_build_adapters_honours_enabled_channels():
    cfg = SessionConfig(run_id="r1", target="http://t.test", channels=[Channel.api, Channel.chat])
    a = build_adapters(cfg, manifest())
    assert set(a) == {Channel.api, Channel.chat}
    assert isinstance(a[Channel.api], ChannelAdapter)
    assert a[Channel.api].target == "http://t.test"
    await close_adapters(a)


async def test_build_adapters_web_uses_the_runs_shots_dir(tmp_path):
    cfg = SessionConfig(run_id="r9", target="http://t.test", channels=[Channel.web])
    a = build_adapters(cfg, manifest(), runs_dir=tmp_path)
    assert a[Channel.web].shots == tmp_path / "r9" / "shots"
    await close_adapters(a)  # must not explode even though no browser ever started


# ---------------------------------------------------------------- web adapter


class FakeHistory:
    def __init__(self, summary="all done", dom=None, shots=None):
        self._summary, self._dom, self._shots = summary, dom or [], shots or []

    def final_result(self):
        return self._summary

    def extracted_content(self):
        return self._dom

    def urls(self):
        return ["http://t.test/docs"]

    def screenshots(self):
        return self._shots


class FakeAgent:
    def __init__(self, task, sensitive, history=None, boom=None):
        self.task, self.sensitive, self.boom = task, sensitive, boom
        self.history = history or FakeHistory()

    async def run(self):
        if self.boom:
            raise self.boom
        return self.history


def web_adapter(tmp_path, history=None, boom=None):
    captured: dict = {}

    def factory(task, sensitive):
        agent = FakeAgent(task, sensitive, history=history, boom=boom)
        captured["agent"] = agent
        return agent

    return WebAdapter("http://t.test", manifest(), shots=tmp_path, agent_factory=factory), captured


async def test_web_raw_keeps_dom_text_not_only_the_lossy_summary(tmp_path):
    """final_result() is an LLM summary and drops canaries (PLAN 14) -- raw must be fuller."""
    history = FakeHistory(summary="I found the documents.", dom=["Invoice BUZZ 1", "total 84317.43"])
    web, _ = web_adapter(tmp_path, history=history)
    r = await web.act(step("open the documents page", channel=Channel.web))
    assert r.error is None
    assert "BUZZ" in r.raw and "84317.43" in r.raw     # canary survived in raw
    assert "I found the documents." in r.raw
    assert r.extracted["summary"] == "I found the documents."
    assert r.extracted["urls"] == ["http://t.test/docs"]


async def test_web_never_puts_the_secret_in_the_task_only_in_sensitive_data(tmp_path):
    web, captured = web_adapter(tmp_path)
    await web.act(step("Sign up for a new account.", channel=Channel.web))
    agent = captured["agent"]
    assert "hunter2secret" not in agent.task
    assert "x_secret" in agent.task
    assert agent.sensitive == {"x_email": "a@x.test", "x_secret": "hunter2secret"}
    assert "http://t.test" in agent.task


async def test_web_saves_a_screenshot_and_sets_the_field(tmp_path):
    png = base64.b64encode(b"\x89PNG-not-really").decode()
    web, _ = web_adapter(tmp_path, history=FakeHistory(shots=[png]))
    r = await web.act(step("look", channel=Channel.web))
    assert r.screenshot == str(tmp_path / "s1.png")
    assert (tmp_path / "s1.png").read_bytes() == b"\x89PNG-not-really"


async def test_web_agent_crash_becomes_result_error(tmp_path):
    web, _ = web_adapter(tmp_path, boom=RuntimeError("browser died"))
    r = await web.act(step("look", channel=Channel.web))
    assert r.error and "browser died" in r.error and r.raw == ""


async def test_web_without_browser_use_raises_a_clear_error_only_when_used(tmp_path, monkeypatch):
    import baduser.channels.web as web_mod

    monkeypatch.setattr(web_mod, "Agent", None)
    monkeypatch.setattr(web_mod, "_IMPORT_ERROR", "ModuleNotFoundError('browser_use')")
    web = WebAdapter("http://t.test", manifest(), shots=tmp_path)  # constructing is fine
    r = await web.act(step("look", channel=Channel.web))
    assert r.error and "browser-use is not installed" in r.error and "uv sync --extra web" in r.error


async def test_web_export_cookies_is_empty_without_a_session(tmp_path):
    web, _ = web_adapter(tmp_path)
    assert await web.export_cookies() == {}
