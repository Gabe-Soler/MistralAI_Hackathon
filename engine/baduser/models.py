"""All DTOs. See PLAN.md section 12.

Two rules that the rest of the codebase depends on:
  * `Verdict.error` exists so an adapter crash can never render as a passing app.
  * Credentials are SecretStr, and the dashboard is served PublicManifest -- never Manifest.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr


class ProductType(str, Enum):
    b2b = "b2b"
    b2c = "b2c"


class Channel(str, Enum):
    api = "api"
    chat = "chat"
    web = "web"
    voice = "voice"


class Verdict(str, Enum):
    benign = "benign"
    breach = "breach"
    error = "error"


# ---------- Ground truth (the rules) ----------


class Invariant(BaseModel):
    id: str
    name: str
    rule: str
    source: Literal["code", "dev"]
    cite: str | None = None  # "app.py:42" -- the "should" half of should-vs-did


class GroundTruth(BaseModel):
    product_name: str
    product_type: ProductType
    domain: str = ""
    roles: list[str] = []
    invariants: list[Invariant] = []
    endpoints: list[str] = []
    signup_hint: str = ""
    endpoint_bodies: dict[str, list[str]] = {}
    """"METHOD /path" -> request body field names, when discovery found them (OpenAPI).

    Kept because it tells you what a route is SCOPED to: an invite whose body is
    {"email", "invoice_id"} is scoped to an invoice, so the interesting attack is whether
    accepting it also grants documents.
    """

    def tenant_isolation(self) -> Invariant | None:
        """The one invariant the canary scan enforces. Findings cite it."""
        for inv in self.invariants:
            if inv.id == "tenant-isolation":
                return inv
        return self.invariants[0] if self.invariants else None


# ---------- Manifest (the world we created) ----------


class Credentials(BaseModel):
    username: str
    secret: SecretStr
    token: SecretStr | None = None
    cookies: dict[str, SecretStr] = {}
    """Session cookies, so a browser->http handoff survives save/reload and --replay.

    Without this the session lives only inside the adapter's client and a resumed run
    silently 401s on every step -- which the oracle scores benign, i.e. reports clean.
    """

    def reveal(self) -> dict[str, str]:
        """The ONLY way secrets leave this object. Used by store.save_manifest and adapters."""
        out = {"username": self.username, "secret": self.secret.get_secret_value()}
        if self.token is not None:
            out["token"] = self.token.get_secret_value()
        return out

    def reveal_cookies(self) -> dict[str, str]:
        return {k: v.get_secret_value() for k, v in self.cookies.items()}


class Tenant(BaseModel):
    id: str
    kind: Literal["company", "user"] = "company"
    name: str
    canary: str  # embedded in every string this tenant owns


class Persona(BaseModel):
    id: str
    tenant_id: str
    role: str
    name: str
    email: str
    credentials: Credentials
    ref: str | None = None  # the account id the TARGET assigned; needed to address a user
                            # by the app's own identifier. Ours is `id`; theirs is `ref`.
    control: bool = False  # reserved for the compound-chain control run; never used by plays


class Artifact(BaseModel):
    id: str
    tenant_id: str
    owner_persona_id: str
    kind: str = "document"
    ref: str = ""
    title: str
    body: str = ""
    amount: Decimal | None = None


class Manifest(BaseModel):
    tenants: list[Tenant] = []
    personas: list[Persona] = []
    artifacts: list[Artifact] = []
    canaries: dict[str, str] = {}  # canary -> tenant_id
    routes: list[str] = []  # "METHOD /path" seeding actually got a 2xx from -- empirical
                            # proof, unlike the repo-read list. The campaign attacks these
                            # first because they are known to exist (discover.py).

    def persona(self, pid: str) -> Persona | None:
        return next((p for p in self.personas if p.id == pid), None)

    def tenant(self, tid: str) -> Tenant | None:
        return next((t for t in self.tenants if t.id == tid), None)

    def secrets(self) -> list[str]:
        """Every secret string we generated -- the redaction scrubber is built from this."""
        out: list[str] = []
        for p in self.personas:
            out.append(p.credentials.secret.get_secret_value())
            if p.credentials.token is not None:
                out.append(p.credentials.token.get_secret_value())
            out.extend(p.credentials.reveal_cookies().values())
        return [s for s in out if s]


class PublicPersona(BaseModel):
    id: str
    tenant_id: str
    role: str
    name: str
    email: str
    ref: str | None = None  # not a secret; the dashboard may want the target's own id


class PublicManifest(BaseModel):
    """What GET /state returns. A DISTINCT model, not Manifest.model_dump(exclude=...),
    because exclusions get forgotten the moment someone adds a field."""

    tenants: list[Tenant] = []
    personas: list[PublicPersona] = []
    artifacts: list[Artifact] = []

    @classmethod
    def of(cls, m: Manifest) -> PublicManifest:
        return cls(
            tenants=m.tenants,
            personas=[PublicPersona(**p.model_dump(include=set(PublicPersona.model_fields)))
                      for p in m.personas],
            artifacts=m.artifacts,
        )


# ---------- Campaign ----------


class Step(BaseModel):
    id: str
    persona_id: str
    channel: Channel
    action: str
    target_ref: str | None = None
    # action/target_ref may contain {{s2.org_id}} templates resolved against Play.context.


class Play(BaseModel):
    id: str
    title: str
    steps: list[Step]
    context: dict = {}
    compound: bool = False
    """Whether the final step is only expected to breach BECAUSE of the earlier ones.

    Only these get a control run. Re-running the last step of a flat probe play proves
    nothing -- the control just breaches too, and the dashboard reads it as a failed
    compound claim.
    """


class Result(BaseModel):
    error: str | None = None  # None = the step ran; set = adapter/target failed
    status: int | None = None
    raw: str = ""
    screenshot: str | None = None
    extracted: dict = {}


class Finding(BaseModel):
    id: str
    play_id: str
    persona_id: str
    channel: Channel
    action: str
    verdict: Verdict
    invariant_id: str | None = None
    cite: str | None = None
    evidence: str = ""
    repro: list[Step] = []


# ---------- Session ----------


class SessionConfig(BaseModel):
    run_id: str
    repo: str = ""
    target: str = ""
    channels: list[Channel] = [Channel.api, Channel.chat]
    support_phone: str | None = None
    replay: str | None = None
    ci: bool = False
    rps: float = 5.0  # per-target rate cap. Parallel plays would otherwise load-test the
                      # app and look like an attack from your IP (PLAN 15).


# ---------- Dashboard events ----------


class QuestionEvent(BaseModel):
    type: Literal["question"] = "question"
    id: str
    text: str
    options: list[str] = ["y", "n"]


class SeedEvent(BaseModel):
    type: Literal["seed"] = "seed"
    tenant_id: str
    persona_id: str | None = None
    detail: str = ""
    artifact_id: str | None = None
    ok: bool = True  # a failed seed must be colourable without string-matching `detail`


class TruthUpdated(BaseModel):
    type: Literal["truth_updated"] = "truth_updated"
    invariant: Invariant


class PhaseEvent(BaseModel):
    type: Literal["phase"] = "phase"
    phase: str  # reading | seeding | attacking | done


class StepStarted(BaseModel):
    type: Literal["step_started"] = "step_started"
    play_id: str
    persona_id: str
    channel: Channel
    action: str


class StepFinished(BaseModel):
    type: Literal["step_finished"] = "step_finished"
    play_id: str
    persona_id: str
    channel: Channel
    action: str
    detail: str = ""
    verdict: Verdict
    invariant_id: str | None = None
    shot: str | None = None


class FindingEvent(BaseModel):
    type: Literal["finding"] = "finding"
    finding: Finding


class ChainEvent(BaseModel):
    type: Literal["chain"] = "chain"
    play_id: str
    title: str
    steps: list[StepFinished]
    verdict: Verdict
    compound: bool
    control_verdict: Verdict | None = None


Event = Annotated[
    QuestionEvent
    | SeedEvent
    | TruthUpdated
    | PhaseEvent
    | StepStarted
    | StepFinished
    | FindingEvent
    | ChainEvent,
    Field(discriminator="type"),
]
