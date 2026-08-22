"""The run directory `.baduser/runs/<run_id>/`. See PLAN.md sections 11 and 11b.

Four files + a shots/ dir:
  ground-truth.json   the rules
  manifest.json       the world WITH creds -- chmod 0600, the only file secrets touch
  findings.json       rewritten atomically as findings land, so a crash keeps evidence
  events.jsonl        the bus log; powers --replay and post-mortems

Every write is atomic (tmp + os.replace) so a Ctrl-C mid-write cannot corrupt a file.
`save_manifest` is the SOLE opt-in for serialising secrets (PLAN 11b rule 1): Pydantic
renders SecretStr as `**********` on every other dump.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import TypeAdapter

from .models import Event, Finding, GroundTruth, Manifest, SessionConfig

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def _atomic_write(path: Path, data: str, *, mode: int | None = None) -> None:
    """Write to a tmp file (chmod'd BEFORE the swap so there's no readable window),
    then os.replace -- atomic on POSIX, survives Ctrl-C mid-write. PLAN 11."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _manifest_revealed_json(m: Manifest) -> str:
    """The ONE place plaintext secrets are serialised (PLAN 11b rule 1).

    model_dump(mode="json") masks every SecretStr as `**********`; we then overwrite each
    persona's credentials with Credentials.reveal() so a reload restores working logins.
    """
    d = m.model_dump(mode="json")
    for pd, p in zip(d["personas"], m.personas, strict=True):
        # reveal() is the flat map adapters want; cookies are a nested dict, so merge them
        # back explicitly. Assigning reveal() alone would DROP the session silently, and a
        # resumed run would then 401 on every step -- which the oracle scores benign.
        cred = p.credentials.reveal()
        if p.credentials.cookies:
            cred["cookies"] = p.credentials.reveal_cookies()
        pd["credentials"] = cred
    return json.dumps(d, indent=2)


class Store:
    def __init__(self, run_id: str, root: str | Path = ".baduser") -> None:
        self.run_id = run_id
        self.dir = Path(root) / "runs" / run_id
        self.shots = self.dir / "shots"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.shots.mkdir(parents=True, exist_ok=True)
        self._findings: list[Finding] = []

    # ---- paths ----
    @property
    def config_path(self) -> Path:
        return self.dir / "config.json"

    @property
    def ground_truth_path(self) -> Path:
        return self.dir / "ground-truth.json"

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def findings_path(self) -> Path:
        return self.dir / "findings.json"

    @property
    def events_path(self) -> Path:
        return self.dir / "events.jsonl"

    # ---- config ----
    def save_config(self, cfg: SessionConfig) -> None:
        _atomic_write(self.config_path, cfg.model_dump_json(indent=2))

    def load_config(self) -> SessionConfig:
        return SessionConfig.model_validate_json(self.config_path.read_text())

    # ---- ground truth ----
    def save_ground_truth(self, gt: GroundTruth) -> None:
        _atomic_write(self.ground_truth_path, gt.model_dump_json(indent=2))

    def load_ground_truth(self) -> GroundTruth:
        return GroundTruth.model_validate_json(self.ground_truth_path.read_text())

    # ---- manifest (secrets) ----
    def save_manifest(self, m: Manifest) -> None:
        # 0600: the file has real logins to a running app (PLAN 11b). Written
        # incrementally -- call it again each time an entity is created.
        _atomic_write(self.manifest_path, _manifest_revealed_json(m), mode=0o600)

    def load_manifest(self) -> Manifest:
        return Manifest.model_validate_json(self.manifest_path.read_text())

    # ---- findings ----
    def add_finding(self, f: Finding) -> None:
        self._findings.append(f)
        self.save_findings(self._findings)

    def save_findings(self, findings: list[Finding]) -> None:
        payload = [json.loads(f.model_dump_json()) for f in findings]
        _atomic_write(self.findings_path, json.dumps(payload, indent=2))

    def load_findings(self) -> list[Finding]:
        if not self.findings_path.exists():
            return []
        return [Finding.model_validate(x) for x in json.loads(self.findings_path.read_text())]

    # ---- events (append-only; --replay) ----
    def append_event(self, ev: Event) -> None:
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(ev.model_dump_json() + "\n")

    def load_events(self) -> list[Event]:
        if not self.events_path.exists():
            return []
        out: list[Event] = []
        for line in self.events_path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(_EVENT_ADAPTER.validate_json(line))
        return out
