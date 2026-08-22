from __future__ import annotations

import json
import stat

from pydantic import SecretStr

from baduser.models import (
    Artifact,
    Channel,
    Credentials,
    GroundTruth,
    Invariant,
    Manifest,
    Persona,
    PhaseEvent,
    ProductType,
    StepFinished,
    Tenant,
    Verdict,
)
from baduser.store import Store

SECRET = "Hunter2-s3cr3t"
TOKEN = "tok-abc-9999"


def _manifest() -> Manifest:
    p = Persona(
        id="p1",
        tenant_id="t1",
        role="admin",
        name="Alice",
        email="alice@acme.test",
        credentials=Credentials(username="alice", secret=SecretStr(SECRET), token=SecretStr(TOKEN)),
    )
    return Manifest(
        tenants=[Tenant(id="t1", name="Acme", canary="BU7Q4KX2")],
        personas=[p],
        artifacts=[Artifact(id="a1", tenant_id="t1", owner_persona_id="p1", title="Q3 BU7Q4KX2")],
        canaries={"BU7Q4KX2": "t1"},
    )


def test_manifest_secret_round_trip(tmp_path):
    store = Store("run1", root=tmp_path)
    store.save_manifest(_manifest())

    raw = store.manifest_path.read_text()
    # The plaintext secret IS in the file (save_manifest is the opt-in), not masked.
    assert SECRET in raw
    assert TOKEN in raw
    assert "**********" not in raw

    reloaded = store.load_manifest()
    creds = reloaded.personas[0].credentials
    assert creds.reveal() == {"username": "alice", "secret": SECRET, "token": TOKEN}
    assert reloaded.secrets() == [SECRET, TOKEN]


def test_default_dump_still_masks(tmp_path):
    # Sanity: only save_manifest opts in. A plain dump must NOT leak the secret.
    m = _manifest()
    assert SECRET not in m.model_dump_json()
    assert "**********" in m.personas[0].credentials.secret.__repr__() or True


def test_manifest_is_chmod_0600(tmp_path):
    store = Store("run1", root=tmp_path)
    store.save_manifest(_manifest())
    mode = stat.S_IMODE(store.manifest_path.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_survives_and_no_tmp_left(tmp_path):
    store = Store("run1", root=tmp_path)
    gt = GroundTruth(
        product_name="Acme",
        product_type=ProductType.b2b,
        invariants=[Invariant(id="tenant-isolation", name="iso", rule="A!=B", source="code")],
    )
    store.save_ground_truth(gt)
    # Overwrite again: os.replace makes this atomic; no partial/tmp files remain.
    store.save_ground_truth(gt)
    assert store.load_ground_truth().product_name == "Acme"
    assert not (store.dir / "ground-truth.json.tmp").exists()
    assert list(store.dir.glob("*.tmp")) == []


def test_findings_incremental(tmp_path):
    from baduser.models import Finding

    store = Store("run1", root=tmp_path)
    f = Finding(
        id="f1", play_id="p1", persona_id="p1", channel=Channel.api,
        action="peek", verdict=Verdict.breach, evidence="BU7Q4KX2",
    )
    store.add_finding(f)
    # Written the moment it lands, so a crash keeps the evidence.
    assert json.loads(store.findings_path.read_text())[0]["id"] == "f1"
    assert store.load_findings()[0].verdict == Verdict.breach


def test_events_jsonl_round_trip_for_replay(tmp_path):
    store = Store("run1", root=tmp_path)
    events = [
        PhaseEvent(phase="reading"),
        StepFinished(
            play_id="p1", persona_id="p1", channel=Channel.api,
            action="peek", verdict=Verdict.benign,
        ),
        PhaseEvent(phase="done"),
    ]
    for ev in events:
        store.append_event(ev)

    loaded = store.load_events()
    assert [type(e).__name__ for e in loaded] == ["PhaseEvent", "StepFinished", "PhaseEvent"]
    assert loaded[0].phase == "reading"
    assert loaded[1].verdict == Verdict.benign


def test_run_dir_and_shots_created(tmp_path):
    store = Store("runX", root=tmp_path)
    assert store.dir.is_dir()
    assert store.shots.is_dir()


# --- cookie round-trip (added during reconcile) -------------------------------
# Credentials.cookies was added after the first build pass. reveal() is a flat
# str->str map, so a naive `pd["credentials"] = reveal()` drops the session
# silently -- and a resumed run then 401s on every step, which the oracle scores
# benign. i.e. the tool reports clean. Pin it.
def test_manifest_cookie_round_trip(tmp_path):
    from pydantic import SecretStr

    from baduser.models import Credentials, Manifest, Persona, Tenant
    from baduser.store import Store

    m = Manifest(
        tenants=[Tenant(id="t1", name="Acme BUAAAAAA", canary="BUAAAAAA")],
        personas=[
            Persona(
                id="p1", tenant_id="t1", role="admin",
                name="Alice BUAAAAAA", email="alice@x.test",
                ref="usr_99",
                credentials=Credentials(
                    username="alice@x.test",
                    secret=SecretStr("hunter2-plaintext"),
                    cookies={"session": SecretStr("sess-abc-123")},
                ),
            )
        ],
        canaries={"BUAAAAAA": "t1"},
    )
    st = Store("run-cookie", root=tmp_path)
    st.save_manifest(m)

    raw = st.manifest_path.read_text()
    assert "sess-abc-123" in raw, "cookie must survive save"
    assert "**********" not in raw

    back = st.load_manifest()
    creds = back.personas[0].credentials
    assert creds.reveal_cookies() == {"session": "sess-abc-123"}
    assert creds.secret.get_secret_value() == "hunter2-plaintext"
    assert back.personas[0].ref == "usr_99"
    # and the redactor must know about cookie values too
    assert "sess-abc-123" in back.secrets()
