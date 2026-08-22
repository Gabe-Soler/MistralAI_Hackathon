"""The write path and the read path must be the same directory.

test_server.py's shots test writes a frame directly into `store.shots` and then asks the
route for it, so it passes whatever the web channel actually does. That is how this
survived: cli.py passed `store.dir` (already `.baduser/runs/<id>`) into `shots_dir()`,
which appends `run_id` again -- frames landed in `.baduser/runs/<id>/<id>/shots` while the
route read `.baduser/runs/<id>/shots`. Every frame 404'd.

These tests pin the seam between the two rather than either side of it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from baduser.bus import Bus
from baduser.channels import shots_dir
from baduser.models import SessionConfig
from baduser.server import EngineState, create_app
from baduser.store import Store


def test_frames_land_exactly_where_the_route_reads_them(tmp_path: Path) -> None:
    store = Store("20260822-120000", root=tmp_path)

    # What cli.py hands build_adapters(), and what build_adapters() does with it.
    runs_root = str(store.dir.parent)
    written_to = shots_dir("20260822-120000", runs_root)

    assert written_to == store.shots


def test_passing_the_run_dir_double_nests(tmp_path: Path) -> None:
    """The old behaviour, pinned so the regression is recognisable if it returns."""
    store = Store("20260822-120000", root=tmp_path)

    wrong = shots_dir("20260822-120000", str(store.dir))

    assert wrong != store.shots
    assert wrong == store.shots.parent / "20260822-120000" / "shots"


def test_a_frame_written_by_the_channel_is_servable(tmp_path: Path) -> None:
    """End to end across the seam: write via shots_dir, read via the HTTP route."""
    store = Store("20260822-120000", root=tmp_path)
    frames = shots_dir("20260822-120000", str(store.dir.parent))
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "s1.png").write_bytes(b"\x89PNG\r\n\x1a\n-fake")

    st = EngineState(SessionConfig(run_id="20260822-120000"), Bus(), store)
    c = TestClient(create_app(st))

    for url in ("/shots/s1.png", "/api/20260822-120000/shots/s1.png"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert r.content.startswith(b"\x89PNG"), url


def test_shots_are_run_scoped(tmp_path: Path) -> None:
    """A frame from one run must not be reachable under another run's id."""
    a = Store("20260822-120000", root=tmp_path)
    b = Store("20260822-130000", root=tmp_path)
    (a.shots / "s1.png").write_bytes(b"\x89PNG\r\n\x1a\n-a")

    st = EngineState(SessionConfig(run_id="20260822-120000"), Bus(), a)
    app = create_app(st)
    st_b = EngineState(SessionConfig(run_id="20260822-130000"), Bus(), b)
    app.state.registry.add(st_b)
    c = TestClient(app)

    assert c.get("/api/20260822-120000/shots/s1.png").status_code == 200
    assert c.get("/api/20260822-130000/shots/s1.png").status_code == 404


def test_traversal_never_serves_a_file_outside_the_shots_dir(tmp_path: Path) -> None:
    """Assert on the bytes, not the status code.

    A bare `../secret.png` never reaches the handler at all -- the HTTP client normalises
    it to `/secret.png` before sending, and the SPA catch-all then answers it with
    index.html, which is correct behaviour for an unknown client route. Checking for 4xx
    would therefore pass or fail for reasons unrelated to traversal. What must hold is
    that the secret's bytes are never returned by any of these.
    """
    store = Store("20260822-120000", root=tmp_path)
    secret = b"SECRET-OUTSIDE-SHOTS"
    (tmp_path / "secret.png").write_bytes(secret)
    (store.shots.parent / "manifest.json").write_bytes(secret)
    c = TestClient(create_app(EngineState(SessionConfig(run_id="20260822-120000"),
                                          Bus(), store)))

    for bad in ("../secret.png", "..%2f..%2fsecret.png", "%2e%2e%2fmanifest.json",
                ".hidden", "a/b.png", "....//secret.png"):
        for url in (f"/shots/{bad}", f"/api/20260822-120000/shots/{bad}"):
            assert secret not in c.get(url).content, url


def test_the_shots_handler_itself_refuses_the_obvious_shapes(tmp_path: Path) -> None:
    """Reaching the handler directly, the string guard is what returns 400."""
    store = Store("20260822-120000", root=tmp_path)
    c = TestClient(create_app(EngineState(SessionConfig(run_id="20260822-120000"),
                                          Bus(), store)))

    for bad in ("..%2f..%2fetc%2fpasswd", ".hidden"):
        assert c.get(f"/shots/{bad}").status_code in (400, 404), bad
        assert c.get(f"/api/20260822-120000/shots/{bad}").status_code in (400, 404), bad
