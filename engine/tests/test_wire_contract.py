"""The dashboard's wire format is a contract, and this is the only thing enforcing it.

`BadChat-FrontEnd/src/lib/api/events.ts` mirrors the Event union by hand. Nothing in
Python imports it and nothing in TypeScript imports Python, so the two can drift silently
-- a renamed field or a new event would simply stop rendering, with no error anywhere and
no failing test. That is the expensive kind of bug: it looks like a UI problem and it is
not.

So the schema is snapshotted here. Adding, renaming or retyping anything on the wire
fails this test, and the failure names the file to update. Regenerate deliberately:

    uv run python -m pytest tests/test_wire_contract.py --snapshot-update

(there is no such flag -- delete tests/wire_contract.json and re-run, which is the same
thing and makes the deletion visible in the diff.)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from baduser.models import Event

SNAPSHOT = Path(__file__).parent / "wire_contract.json"

TS_FILE = "BadChat-FrontEnd/src/lib/api/events.ts"


def _schema() -> dict:
    return TypeAdapter(Event).json_schema()


def test_the_wire_format_has_not_changed() -> None:
    got = _schema()

    if not SNAPSHOT.exists():  # pragma: no cover - only on a deliberate regenerate
        SNAPSHOT.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
        raise AssertionError(f"wrote a new snapshot; review it and check {TS_FILE}")

    want = json.loads(SNAPSHOT.read_text())
    assert got == want, (
        f"The dashboard wire format changed.\n"
        f"Update {TS_FILE} to match, then delete {SNAPSHOT.name} and re-run to re-snapshot."
    )


def test_every_event_is_tagged_and_the_union_is_discriminated() -> None:
    """One `es.onmessage` narrows on `type`, so every member needs a literal tag."""
    schema = _schema()

    assert schema["discriminator"]["propertyName"] == "type"
    for member in schema["oneOf"]:
        name = member["$ref"].split("/")[-1]
        tag = schema["$defs"][name]["properties"]["type"]
        assert tag.get("const"), f"{name} has no literal `type`"


def test_the_tags_match_the_typescript_union() -> None:
    """The eight strings the client switches on. Hard-coded so a rename fails here."""
    schema = _schema()
    tags = {
        schema["$defs"][m["$ref"].split("/")[-1]]["properties"]["type"]["const"]
        for m in schema["oneOf"]
    }

    assert tags == {
        "question",
        "seed",
        "truth_updated",
        "phase",
        "step_started",
        "step_finished",
        "finding",
        "chain",
    }


def test_phase_is_not_an_enum() -> None:
    """`phase` must stay a bare string.

    "failed" was added after the first clients existed. A Literal here would make the
    client's union authoritative, and any phase added later would throw mid-run in a
    `switch` that has no default. events.ts types it `string` on purpose; this keeps the
    Python side from tightening it.
    """
    phase = _schema()["$defs"]["PhaseEvent"]["properties"]["phase"]

    assert phase.get("type") == "string"
    assert "enum" not in phase and "const" not in phase


def test_no_field_is_omitted_from_the_wire() -> None:
    """events.ts types optional fields `T | null`, never `T | undefined`.

    That is only safe because nothing dumps with exclude_none/exclude_defaults, so a
    defaulted field is still serialized. If that ever changes, the client's non-optional
    fields become lies and this is where it should be caught.
    """
    from baduser.models import PhaseEvent, SeedEvent

    seed = json.loads(SeedEvent(tenant_id="t").model_dump_json())
    assert set(seed) == {"type", "tenant_id", "persona_id", "detail", "artifact_id", "ok"}
    assert seed["persona_id"] is None  # present and null, not absent

    phase = json.loads(PhaseEvent(phase="reading").model_dump_json())
    assert set(phase) == {"type", "phase", "detail"}
