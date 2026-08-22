"""Run-scoped routes, and the root routes that must keep behaving identically.

The SPA addresses runs by id (`/<run_id>` in the browser, `/api/<run_id>/...` on the wire),
but `/state`, `/stream` and `/answer` stay: test_server.py pins them and
static/dashboard.html calls them with relative URLs. Both sets run the same handlers, so
these tests exist to prove they cannot drift apart.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from baduser.bus import Bus
from baduser.models import Finding, SessionConfig, Verdict
from baduser.server import EngineState, Registry, _started_at, create_app


def _state(run_id: str = "20260822-120000", target: str = "http://t") -> EngineState:
    return EngineState(SessionConfig(run_id=run_id, target=target), Bus())


def _finding(verdict: Verdict) -> Finding:
    return Finding(id="f1", play_id="p1", persona_id="b1", channel="api",
                   action="GET /x", verdict=verdict)


# ---------- the two route sets agree ----------


def test_run_scoped_state_matches_the_root_route() -> None:
    st = _state()
    c = TestClient(create_app(st))

    assert c.get("/api/20260822-120000/state").json() == c.get("/state").json()


def test_unknown_run_is_404_not_500() -> None:
    c = TestClient(create_app(_state()))

    r = c.get("/api/nope/state")

    assert r.status_code == 404
    assert "unknown run" in r.json()["detail"]


def test_run_scoped_answer_keeps_the_404_and_409_contract() -> None:
    st = _state()
    c = TestClient(create_app(st))
    body = {"question_id": "q1", "answer": "y"}

    assert c.post("/api/20260822-120000/answer", json=body).status_code == 404
    assert c.post("/api/nope/answer", json=body).status_code == 404


def test_root_routes_404_when_no_run_is_registered() -> None:
    """create_app() with nothing registered must not 500 -- it has nothing to serve."""
    c = TestClient(create_app(registry=Registry()))

    assert c.get("/state").status_code == 404


# ---------- the registry ----------


def test_add_makes_a_run_current() -> None:
    r = Registry()
    a, b = _state("20260822-120000"), _state("20260822-130000")

    r.add(a)
    r.add(b)

    assert r.current == "20260822-130000"
    assert r.current_state() is b
    assert r.get("20260822-120000") is a


def test_summaries_count_breaches_and_errors_separately() -> None:
    """An errored step is not a finding about the product -- the picker must not conflate
    them, for the same reason --ci counts only breaches."""
    st = _state()
    st.findings = [_finding(Verdict.breach), _finding(Verdict.breach), _finding(Verdict.error)]
    c = TestClient(create_app(st))

    (row,) = c.get("/api/runs").json()

    assert (row["findings"], row["breaches"], row["errors"]) == (3, 2, 1)
    assert row["live"] is True


def test_a_run_named_runs_cannot_shadow_the_listing() -> None:
    c = TestClient(create_app(_state("runs")))

    assert isinstance(c.get("/api/runs").json(), list)  # the listing, not that run's state


# ---------- archived runs on disk ----------


def test_summaries_include_archived_runs_newest_first(tmp_path) -> None:
    for rid, phase in (("20260822-100000", "done"), ("20260822-110000", "failed")):
        d = tmp_path / "runs" / rid
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({"run_id": rid, "target": "http://old"}))
        (d / "findings.json").write_text(json.dumps([{"verdict": "breach"}]))
        (d / "events.jsonl").write_text(
            '{"type":"phase","phase":"seeding","detail":""}\n'
            f'{{"type":"phase","phase":"{phase}","detail":"why"}}\n'
        )

    rows = Registry(tmp_path).summaries()

    assert [r.run_id for r in rows] == ["20260822-110000", "20260822-100000"]
    assert [r.phase for r in rows] == ["failed", "done"]
    assert rows[0].phase_detail == "why"      # the reason survives to the picker
    assert rows[0].target == "http://old"
    assert all(r.live is False for r in rows)


def test_a_half_written_run_still_lists(tmp_path) -> None:
    """A run killed mid-seed has no findings.json. It must degrade, not break the picker."""
    d = tmp_path / "runs" / "20260822-100000"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{ this is not json")

    (row,) = Registry(tmp_path).summaries()

    assert row.run_id == "20260822-100000"
    assert (row.findings, row.target) == (0, "")


def test_a_live_run_shadows_its_own_archived_directory(tmp_path) -> None:
    """Same id in memory and on disk: the in-memory one is always fresher."""
    d = tmp_path / "runs" / "20260822-120000"
    d.mkdir(parents=True)
    (d / "findings.json").write_text("[]")
    r = Registry(tmp_path)
    st = _state()
    st.findings = [_finding(Verdict.breach)]
    r.add(st)

    (row,) = r.summaries()

    assert row.live is True
    assert row.breaches == 1


def test_missing_store_root_is_not_an_error(tmp_path) -> None:
    assert Registry(tmp_path / "nope").summaries() == []


# ---------- run_id is the timestamp ----------


@pytest.mark.parametrize(
    ("run_id", "expected"),
    [
        ("20260822-143012", "2026-08-22T14:30:12+00:00"),
        ("not-a-timestamp", ""),
        ("", ""),
    ],
)
def test_started_at_is_derived_from_the_run_id(run_id: str, expected: str) -> None:
    assert _started_at(run_id) == expected
