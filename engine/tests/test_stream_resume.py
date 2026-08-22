"""The resume point must never be able to 500.

`EventSource` reconnects on its own and replays whatever Last-Event-ID it last saw. A bare
int() on that header turns one malformed value into a permanent reconnect loop: the browser
retries, the server 500s, the browser retries. Falling back to 0 replays the whole run
instead, which is always correct because `Bus._log` is complete and append-only.

Verified against a live server before this was written: `Last-Event-ID: abc` returned 500.
"""

from __future__ import annotations

from starlette.datastructures import Headers, QueryParams

from baduser.server import _since


class _Req:
    """Just the two attributes `_since` reads."""

    def __init__(self, header: str | None = None, query: str = "") -> None:
        self.headers = Headers({"last-event-id": header} if header is not None else {})
        self.query_params = QueryParams(query)


def test_valid_header_is_the_resume_point() -> None:
    assert _since(_Req("12")) == 12


def test_missing_header_replays_from_the_start() -> None:
    assert _since(_Req()) == 0


def test_malformed_header_replays_instead_of_500ing() -> None:
    for bad in ("abc", "", "1.5", "12,13", "9e9", " "):
        assert _since(_Req(bad)) == 0, bad


def test_negative_is_clamped() -> None:
    """A negative `since` would still work in Bus.subscribe, but 0 is the honest floor."""
    assert _since(_Req("-5")) == 0


def test_query_param_is_the_manual_resume_path() -> None:
    """No header is sent by curl or a fetch-based client; ?since= gives them a way in."""
    assert _since(_Req(None, "since=7")) == 7


def test_header_wins_over_query_param() -> None:
    """The browser's own header is authoritative; a stale URL must not override it."""
    assert _since(_Req("20", "since=7")) == 20
