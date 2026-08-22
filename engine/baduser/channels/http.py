"""api + chat -- both httpx, ~10 lines of real logic each. PLAN.md section 13.

The only non-obvious thing here is the client cache: ONE `httpx.AsyncClient` per persona,
held for the whole session. Most generated apps authenticate with a cookie session, so a
fresh client per request throws the session away and every call after login 401s. The same
client is used while seeding and while attacking, so the cookie jar carries across phases.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..models import Manifest, Persona, Result, Step
from . import cap

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def parse_action(action: str) -> tuple[str, str, Any]:
    """`GET /api/invoices` | `/api/invoices` | `POST /api/x {"title": "..."}`.

    Deliberately dumb, and documented so nobody upgrades it by accident:
      * first whitespace-delimited token is the METHOD iff it is a known verb, else GET;
      * next token is the path (query string included -- we do not build one);
      * anything after that is the JSON body iff it starts with `{`.
    Malformed JSON raises -- `act()` turns that into Result.error rather than a silent GET.
    """
    s = action.strip()
    head, _, rest = s.partition(" ")
    if head.upper() in _METHODS:
        method, s = head.upper(), rest.strip()
    else:
        method = "GET"
    path, _, tail = s.partition(" ")
    tail = tail.strip()
    body = json.loads(tail) if tail.startswith("{") else None
    return method, path or "/", body


def fill_credentials(action: str, persona: Persona | None) -> str:
    """Resolve `{{secret}}`-style placeholders from the manifest (PLAN 11b rule 4).

    Secrets never appear in `Step.action` -- a step that needs one carries a placeholder and
    the adapter substitutes it here, so the literal password is never copied into an event,
    sent to Mistral, or spoken on a recorded call.
    """
    if persona is None or "{{" not in action:
        return action
    c = persona.credentials.reveal()
    for token, value in (
        ("{{username}}", c["username"]),
        ("{{secret}}", c["secret"]),
        ("{{token}}", c.get("token", "")),
        ("{{email}}", persona.email),
        ("{{name}}", persona.name),
    ):
        action = action.replace(token, value)
    return action


class _HttpBase:
    """Shared client cache + error discipline for the api and chat adapters."""

    def __init__(self, target: str, manifest: Manifest, *, transport=None, timeout: float = 10.0):
        self.target = (target or "").rstrip("/")
        self.manifest = manifest
        self._transport = transport
        self._timeout = timeout
        self._clients: dict[str, httpx.AsyncClient] = {}

    def client_for(self, persona_id: str | None) -> httpx.AsyncClient:
        """One persistent client per persona: its own cookie jar and auth header."""
        key = persona_id or ""
        c = self._clients.get(key)
        if c is None:
            c = httpx.AsyncClient(
                base_url=self.target,
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            )
            self._clients[key] = c
        persona = self.manifest.persona(key) if key else None
        # Token may only appear later (seeding exports one), so refresh rather than bake in.
        if persona and persona.credentials.token is not None:
            c.headers["Authorization"] = f"Bearer {persona.credentials.token.get_secret_value()}"
        return c

    async def _send(self, step: Step, method: str, path: str, body: Any) -> Result:
        client = self.client_for(step.persona_id)
        r = await client.request(method, path or "/", json=body)
        return Result(status=r.status_code, raw=cap(r.text))

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()


class ApiAdapter(_HttpBase):
    """`step.action` is a request line. See `parse_action`."""

    async def act(self, step: Step) -> Result:
        try:
            action = fill_credentials(step.action, self.manifest.persona(step.persona_id))
            method, path, body = parse_action(action)
            return await self._send(step, method, path, body)
        except Exception as e:  # noqa: BLE001 - never swallow: a crash must not read as benign
            return Result(error=repr(e))


class ChatAdapter(_HttpBase):
    """`step.action` is the message. Posts it to the product's support-chat endpoint."""

    def __init__(self, target: str, manifest: Manifest, *, path: str = "/api/chat", **kw):
        super().__init__(target, manifest, **kw)
        self.path = path

    async def act(self, step: Step) -> Result:
        try:
            action = fill_credentials(step.action, self.manifest.persona(step.persona_id))
            return await self._send(step, "POST", self.path, {"message": action})
        except Exception as e:  # noqa: BLE001 - see ApiAdapter.act
            return Result(error=repr(e))
