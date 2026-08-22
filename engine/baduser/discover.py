"""What routes does the target actually have?

Three sources, deliberately layered, because each is weak where the others are strong:

  repo read (truth.py)  broad but unverified -- every route including ones we never call.
                        A misread path 404s, and a 404 scores benign, so breadth alone is
                        how a run reports clean while missing everything.
  OpenAPI probe         exact and free when present (FastAPI/Flask-smorest). Absent on
                        Express/Next, so it can never be the only source.
  seeding               empirical proof, but only for the handful of routes seeding needs
                        (signup, login, create). It never touches the invite/chat/list
                        routes -- which is exactly where the interesting bugs live.

Union them, mark what is confirmed, and let the campaign attack confirmed routes first.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

_ID_SEG = re.compile(r"^\{.+\}$|^:.+$|^<.+>$")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


class Endpoint(BaseModel):
    method: str
    path: str  # may contain a {param} segment
    source: str = "repo"  # repo | openapi | seed
    confirmed: bool = False  # seeding actually got a 2xx from it
    body_keys: list[str] = []

    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def has_param(self) -> bool:
        return any(_ID_SEG.match(s) for s in self.path.strip("/").split("/"))

    def fill(self, value: str) -> str:
        """Substitute the first {param} segment with a concrete id."""
        out = []
        done = False
        for seg in self.path.strip("/").split("/"):
            if not done and _ID_SEG.match(seg):
                out.append(value)
                done = True
            else:
                out.append(seg)
        return "/" + "/".join(out)

    @property
    def collection(self) -> str:
        """The path with any trailing {param} stripped: /api/docs/{id} -> /api/docs."""
        segs = [s for s in self.path.strip("/").split("/")]
        while segs and _ID_SEG.match(segs[-1]):
            segs.pop()
        return "/" + "/".join(segs)


def parse_line(line: str) -> Endpoint | None:
    """'GET /api/invoices/{id}' -> Endpoint. Tolerant: the LLM writes these by hand."""
    s = (line or "").strip().strip("`")
    if not s:
        return None
    parts = s.split()
    method, path = "GET", s
    if parts and parts[0].upper() in _METHODS:
        method, path = parts[0].upper(), (parts[1] if len(parts) > 1 else "/")
    elif len(parts) > 1 and parts[1].upper() in _METHODS:  # "/path GET"
        method, path = parts[1].upper(), parts[0]
    path = path.split("?")[0]
    if not path.startswith("/"):
        return None
    return Endpoint(method=method, path=path, source="repo")


def from_ground_truth(gt: Any) -> list[Endpoint]:
    out = []
    for line in getattr(gt, "endpoints", []) or []:
        ep = parse_line(str(line))
        if ep:
            out.append(ep)
    return out


def from_openapi(spec: dict) -> list[Endpoint]:
    """A generated FastAPI app serves this at /openapi.json -- exact paths and body shapes,
    no LLM and no repo required."""
    out: list[Endpoint] = []
    for path, ops in (spec.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.upper() not in _METHODS:
                continue
            keys: list[str] = []
            try:
                schema = op["requestBody"]["content"]["application/json"]["schema"]
                # FastAPI puts body models in components/schemas and $refs them, so
                # `properties` is almost never inline. Resolve one hop.
                ref = schema.get("$ref")
                if ref and ref.startswith("#/"):
                    node: Any = spec
                    for part in ref[2:].split("/"):
                        node = node[part]
                    schema = node
                keys = list((schema.get("properties") or {}).keys())
            except (KeyError, TypeError):
                pass
            out.append(Endpoint(method=method.upper(), path=path,
                                source="openapi", body_keys=keys))
    return out


async def probe_openapi(adapter: Any, persona_id: str) -> list[Endpoint]:
    """Best-effort. Returns [] on anything unexpected -- most targets will not have it."""
    import json as _json

    from .models import Channel, Step

    for path in ("/openapi.json", "/swagger.json", "/api/openapi.json"):
        try:
            r = await adapter.act(Step(id="discover", persona_id=persona_id,
                                       channel=Channel.api, action=f"GET {path}"))
            if r.error or (r.status or 0) >= 400 or not r.raw:
                continue
            spec = _json.loads(r.raw)
            eps = from_openapi(spec)
            if eps:
                return eps
        except Exception:  # noqa: BLE001 - discovery is best-effort by definition
            continue
    return []


def merge(*groups: list[Endpoint]) -> list[Endpoint]:
    """Union by (method, path). Later groups win, and `confirmed` is sticky."""
    by: dict[str, Endpoint] = {}
    for group in groups:
        for ep in group:
            prev = by.get(ep.key())
            if prev is None:
                by[ep.key()] = ep.model_copy()
            else:
                prev.confirmed = prev.confirmed or ep.confirmed
                prev.body_keys = prev.body_keys or ep.body_keys
                if ep.source == "openapi":
                    prev.source = "openapi"
    return list(by.values())


def reads(eps: list[Endpoint]) -> list[Endpoint]:
    """GET routes -- the ones worth replaying as the wrong tenant."""
    return [e for e in eps if e.method == "GET"]


def by_keyword(eps: list[Endpoint], *words: str) -> list[Endpoint]:
    return [e for e in eps if any(w in e.path.lower() for w in words)]
