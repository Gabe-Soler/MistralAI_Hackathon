# Bad User — Technical Plan

> **What it is:** a dev tool that emulates a human using your product across every
> channel — clicking buttons, filling fields, messaging the chat, calling the support
> line — and checks each result against what the product is *supposed* to do.
>
> **Keystone:** Bad User authors the test world, so it *owns* the ground truth.
> Cross-tenant exposure of a seeded object is a **lookup, not a guess** — we planted the
> data, so recognizing it is a substring match. Everything beyond that is labeled inference.

---

# PART I — CONCEPT

## 1. Core idea

Point it at a product. It:
1. reads the **source code** to learn what the app should do,
2. asks the **developer** to clarify anything unclear,
3. **creates a fake world** in the running app (accounts, orgs, documents),
4. attacks that world across every channel, and
5. reports where the app did something it shouldn't.

Data leaks are one kind of finding. It also catches broken paths and wrong behavior.
The prize find is the **compound chain**: steps that each look fine, but together break in.

## 2. Two sources of truth

| | From | Example |
|---|---|---|
| **The rules** (allowed / expected) | reading the **source code** + dev clarification | "members can't see other members' invoices" |
| **The world** (accounts, orgs, docs) | **synthesized** and **persisted** into the deployment | Acme Corp, 3 employees, 12 contracts |

Read the **code** for intent; test the **deployment** for reality. The gap is a bug.

**Scope of the claim.** The *world* half is a genuine lookup: we created every tenant,
persona, and document, so we can recognize them with certainty. The *rules* half is LLM
inference over the repo and is only as good as the reader. Claim the first precisely;
label the second as inferred.

## 3. The world adapts to the product

- **B2B** → a fake **company** (or two): org chart, employees, roles, matters.
- **B2C** → several independent **users**, each with their own docs and inputs.

**Always build at least two tenants.** You need a second one to test "A never sees B's stuff."

## 4. Persistence = how it owns the truth

1. **Seed first** — sign up, create orgs/users, upload docs via the product's own interfaces.
   This lands in the deployment's real DB and stays there.
2. **Keep a manifest** — record every persona, credential, and doc, and who owns what.
   The manifest is the oracle's lookup table.
2b. **Tag every tenant with a canary** — a short run-scoped random string (`BU7Q4KX2`)
   embedded in *every* string that tenant owns: company name, person names, email local
   parts, document titles and bodies. The canary must live in the text the app stores,
   not in metadata about it. This is the oracle's entire detection primitive.
   Two rules learned in the build: canaries must be **fixed width** (if one tenant's tag is
   a substring of another's, a response containing only your *own* data scores as a breach),
   and both the upper- and lower-case forms must be registered in `Manifest.canaries` --
   apps routinely lowercase emails on store, and a case-sensitive scan would then silently
   stop matching, i.e. report clean.
3. **Then attack** — data survives the whole session, so compound chains work
   (create as A now, try to reach it as B later).

Bad User writes real data, so the target must be disposable. For the demo it is vibe-coded
seconds before the run ( 22 step 0); teardown is that app's DB reset script.

## 5. Channels — one interface, many hands

```
Channel.act(step) -> result
   api     backbone — always works  (httpx)
   chat    backbone                 (httpx)
   web     the wow — real browser   (Browser Use, Mistral-driven)
   voice   the finale — real call   (Retell)
```

- Engine and oracle don't care which channel.
- Turn channels on/off **per session** to control time/compute.
- **Setup uses the same channels as the attack** — seeding is just benign use.

## 6. The pipeline

```
1. Read code   -> rules            (Mistral reads the repo)
2. Ask dev     -> clarify          (CLI Q&A, written into ground truth)
3. Seed        -> create world     (into the deployment; save manifest)
4. Attack      -> across channels  (long-running)
5. Check       -> oracle           (rules + manifest)
6. Report      -> stream to dashboard
```

## 7. Entry: `/baduser` from the Mistral Vibe CLI

```
/baduser --repo ./app --target http://localhost:3000

  reading source... drafting ground truth (Mistral)
  2 questions for you:
    1) Can members see other members' invoices? [y/n]
    2) Is /admin meant to be public?            [y/n]
  ground truth saved -> ground-truth.json
  seeding world (2 companies, 6 users, 14 docs)...
  testing deployment... 1 BREACH found
  dashboard live -> http://localhost:3002   [opens browser]
```

- `/baduser` runs the `bad-user` engine and opens the dashboard.
- **The prompt must not block the event loop.** uvicorn and the pipeline share one loop
  ( 16), so a bare `input()` freezes the SSE stream mid-demo. Race
  `asyncio.to_thread(input)` against the dashboard's `POST /answer` with
  `FIRST_COMPLETED`, and against a **60s timeout whose default is "do not create the
  invariant"** -- an unanswered question must never invent a rule. Whichever side answers
  first wins; emit `question_resolved` so the other UI dismisses it.
- A "y" answer writes `Invariant(source="dev", cite=None)`. Cap the reader at the **3
  highest-blast-radius questions**; a real repo would otherwise produce dozens.
- **VERIFY EARLY:** does the Vibe CLI support custom slash commands? If not, fall back to
  the Vibe agent shelling out to the `bad-user` command — same result.

### CLI flags
```
--repo <path>            local source to read (intent)
--target <url>           the deployment to test (localhost or hosted)
--channels api,chat,web  which channels this run (default: api,chat)
--support-phone <num>    phone line to test (voice only, must be yours)
--replay <events.jsonl>  drive the dashboard from a recorded run (demo insurance)
--ci                     exit non-zero on breach and quit (otherwise the server stays up)

# CUT: --find (the chain finder was never specified,  23 cuts narrating it, and the
# authored hero chains carry the demo). --scope (no semantics anywhere), --signup
# (pulls in Mailslurp, which  24
# already rules out), --staging/--cleanup (two names for one need; the target is a
# disposable app with a reset script). None of the four appear in the demo script.
```

## 8. How we reach the target

The target is **just a URL**. api/chat/web hit it identically whether local or hosted;
only voice is special (needs a real phone number you own).

- **Demo:** the target is **vibe-coded live with Mistral** and run as a local dev server
  (`localhost:3000`). Disposable by construction -- it did not exist a minute ago -- so it
  is already a sandbox, and it is simultaneously the "unseen app" ( 22 step 0).
- **Account creation:** open signup only. Mailslurp and email-verification flows are cut
  ( 7) -- a vibe-coded target ships open signup, which is one of the reasons to use one.
- **Voice:** only if the product has a support line **you own**; otherwise skip the channel.
- No CORS issues — the engine is a server-side HTTP client + a real browser, not a page fetch.

## 9. Sandboxing (keep it simple)

- **Sandbox the target, not the engine.** For the demo, a disposable localhost app IS the
  sandbox; cleanup = restart with a fresh DB. (Productized later: Docker + ephemeral DB,
  destroy to clean up.)
- **Engine needs no container** — the LLM only proposes attack steps against the target;
  it has no host shell. One precaution: the web channel uses a **fresh browser profile**
  (Browser Use default), never your logged-in Chrome.

---

# PART II — ENGINEERING

## 10. Repo layout

```
bad-user/
  engine/                     # Python (uv)
    baduser/
      cli.py                  # /baduser entry (Typer) -> SessionConfig
      models.py               # all DTOs (Pydantic)
      llm.py                  # Mistral wrapper (text / json / vision)
      store.py                # load/save the run dir; sole opt-in for dumping secrets
      bus.py                  # fan-out event bus + append-only log -> SSE
      oracle.py               # allowed() -> verdict
      server.py               # FastAPI: /state /stream /answer
      truth.py                # read repo -> GroundTruth; ask dev the open questions
      world.py                # Faker + Mistral -> plan; seed into deployment -> Manifest
      campaign.py             # plays + template resolver + control run + finder + runner
      channels/               # the one real polymorphic seam -- keep as a package
        __init__.py           # Channel protocol: act(step) -> Result
        http.py               # api + chat (both httpx; ~10 lines each)
        web.py  voice.py

# CUT: campaign/invariants.py -- a second, competing source of invariants alongside
# GroundTruth, and it undercuts the " you planted the bug" rebuttal. Invariants live in
# GroundTruth only. 4 sub-packages -> 1; ~20 files -> ~9.
    pyproject.toml
  dashboard/                  # React + Vite (JSX) + React Flow + Tailwind
  .gitignore                  # .baduser/ .env  -- WRITE THIS HOUR ONE
  .env.example                # MISTRAL_API_KEY= RETELL_API_KEY=
  .baduser/                   # ALL runtime output, gitignored as one directory
    runs/<run_id>/
      ground-truth.json
      manifest.json           # has creds -- chmod 0600
      findings.json
      events.jsonl            # the bus log; powers --replay and post-mortems
      shots/                  # Browser Use frames, referenced by StepFinished.shot
```

## 11. Storage ("database")

No real DB. Two JSON files + memory.

Four files per run under `.baduser/runs/<run_id>/`. Not one `session.json`: the manifest
needs its own `chmod 0600` ( 11b) and the event log needs to be append-only for `--replay`.

| Data | Where | How |
|---|---|---|
| The rules | `ground-truth.json` | Pydantic model_dump_json / model_validate_json |
| The world we made (incl. creds) | `manifest.json` (0600) | same; `store.save_manifest()` is the only place secrets are dumped |
| Findings | `findings.json` | appended as emitted, so a crash keeps the evidence |
| Every event | `events.jsonl` | the bus log; powers `--replay` and post-mortems |
| The seeded accounts/docs | the target app's own DB | not ours; we hold refs |
| Findings + live events | in memory | list + asyncio.Queue for SSE |

One writer, atomic replace (`tmp.write; os.replace`) so a Ctrl-C mid-write cannot corrupt
them. Written incrementally as each entity is created, not once at the end.

## 11b. Secrets -- four rules

Seeded credentials are real logins to a running app, and this tool is demoed on a projector
and recorded. Four cheap rules close every path they currently take to a screen:

1. **`SecretStr`, never `dict`.** Pydantic renders it `**********` on every dump unless
   explicitly opted in. `store.save_manifest()` is the *only* place that opts in.
2. **A separate `PublicManifest` DTO for `GET /state`.** A naive read endpoint returns the
   whole `Manifest` -- i.e. every persona's password -- to a browser tab, and dashboard
   visual #1 is built from it. Use a distinct model, **not** `exclude={"credentials"}`:
   exclusions get forgotten the moment someone adds a field.
3. **`redact()` before every `bus.emit`.** Build the scrubber *from the manifest itself* --
   you generated every secret, so it is a dict lookup, not pattern matching. Run it over
   `detail`, `evidence`, and `action`. (Regex tiers for `eyJ|sk-|AKIA` are deferred until
   something actually leaks that the manifest does not know about.) Without it:
   `Finding.evidence = result.raw` is the raw body of a login response, and
   `detail = result.raw[:200]` is literally `{"token":"eyJ...`.
4. **Never put a secret in `Step.action`.** It is a natural-language string, so a web or
   voice step would read *"log in as alice@acme.test with Hunter2"* -- which is then copied
   into events, sent to Mistral, and **spoken aloud on a recorded phone call**. Pass
   `persona_id`; let the adapter look creds up.

Plus: `.gitignore` containing `.baduser/` and `.env` in **hour one** (the current plan only
*comments* that the files are gitignored -- nothing creates it), `chmod 0600` on the
manifest, and `.env.example` rather than real keys anywhere in the repo.

## 11c. Seeding an app we have never seen (world.py)

The target is vibe-coded seconds earlier ( 22 step 0), so its routes, auth shape, and field
names are unknown at build time. This is the riskiest piece in the plan and it gets a design.

**Seed through the browser, in natural language.** Browser Use is already a dependency and
already drives a real browser; a generated CRUD app has an ordinary HTML signup form.

```python
async def seed_tenant(web, tenant, personas):
    for p in personas:
        await web.act(Step(action=f"Sign up for a new account. "
                           f"Company: {tenant.name}. Name: {p.name}. "
                           f"Email: {p.email}. Password: {p.credentials.secret}."))
        session = await web.export_cookies()          # reuse on the api/chat channels
        p.credentials.token = session
    for a in artifacts_for(tenant):
        await web.act(Step(action=f"Create a document titled '{a.title}' "
                           f"with body '{a.body}'."))   # both carry the canary
```

**Why NL rather than route extraction:** a discovered `POST /register {email,password}`
breaks the moment the generated app names the field `emailAddress` or adds a company step.
An agent reading the rendered form adapts. `GroundTruth.signup_hint` ( 12) is passed to the
agent as a *hint*, never as a contract -- seeding must not fail when the hint is wrong.

**Consequence for the build order:** seeding depends on the web channel, so `web.py` moves
from step 5 to step 2 ( 22). It is no longer only a showpiece -- it is infrastructure.

**Then hand off to fast channels.** Cookies exported from the browser session drive the
api and chat adapters, so only seeding pays the browser's ~30s-per-action cost; the attack
phase stays fast. Attacks that *need* the visible browser still use it.

**Fallbacks, in order:** (1) NL browser signup; (2) if the form defeats the agent twice,
`signup_hint` as a direct httpx POST; (3) if both fail, abort the run with "could not seed
2 tenants" -- **never continue with one tenant**, because every cross-tenant check then
passes vacuously and the tool reports a false clean.

**Budget it.** Six personas through a browser at ~20-30s each is 2-3 minutes -- longer than
the entire demo. Seed **two personas per tenant**, create artifacts via the api channel once
the first session exists, and pre-seed before the demo starts if the clock demands it.

## 12. DTOs (Pydantic -- models.py)

```python
from enum import Enum
from typing import Annotated, Literal
from pydantic import BaseModel, Field, SecretStr

class ProductType(str, Enum): b2b="b2b"; b2c="b2c"
class Channel(str, Enum):     api="api"; chat="chat"; web="web"; voice="voice"
class Verdict(str, Enum):     benign="benign"; breach="breach"; error="error"

# ---------- Ground truth (the rules) ----------
class Invariant(BaseModel):
    id: str; name: str; rule: str
    source: Literal["code", "dev"]
    cite: str | None = None           # "app/routes/invoices.py:42" -- the should-vs-did
                                      # visual and the auto-fix story both need this.
                                      # An invariant the reader cannot cite is dropped.

class GroundTruth(BaseModel):
    product_name: str
    product_type: ProductType
    domain: str
    roles: list[str]                  # plain labels; nothing consumed the Role model
    invariants: list[Invariant]
    endpoints: list[str] = []         # "POST /api/invoices" -- what the api channel aims at
    signup_hint: str = ""             # NL, e.g. "POST /register {email,password,company}";
                                      # best-effort. Seeding does NOT depend on it --  10.

# ---------- Manifest (the world we created) ----------
class Tenant(BaseModel):
    id: str; kind: str                # "company" | "user"
    name: str
    canary: str                       # "BU7Q4KX2" -- embedded in every string it owns

class Credentials(BaseModel):
    username: str
    secret: SecretStr                 # SecretStr dumps as "**********" unless opted in
    token:  SecretStr | None = None

class Persona(BaseModel):
    id: str; tenant_id: str; role: str
    name: str; email: str
    credentials: Credentials          # never a bare dict -- see  11b

class Artifact(BaseModel):
    id: str; tenant_id: str; owner_persona_id: str
    kind: str                         # "document" | "invoice" | ...
    ref: str                          # its id/url INSIDE the deployment
    title: str

class Manifest(BaseModel):
    tenants:   list[Tenant]   = []
    personas:  list[Persona]  = []
    artifacts: list[Artifact] = []
    canaries:  dict[str, str] = {}    # canary string -> owning tenant_id

class PublicPersona(BaseModel):       # everything the dashboard needs, no credentials
    id: str; tenant_id: str; role: str; name: str; email: str

class PublicManifest(BaseModel):      # what GET /state returns -- a DISTINCT model, never
    tenants:   list[Tenant]   = []    # Manifest.model_dump(exclude=...), which gets
    personas:  list[PublicPersona] = []   # forgotten the moment someone adds a field
    artifacts: list[Artifact] = []

    @classmethod
    def of(cls, m: Manifest) -> "PublicManifest": ...

# ---------- Campaign ----------
class Step(BaseModel):
    id: str                           # "s2" -- later steps refer to it
    persona_id: str
    channel: Channel
    action: str                       # NL instruction or structured request
    target_ref: str | None = None     # what it's reaching for
    # action and target_ref may contain {{s2.org_id}} templates, resolved at run time
    # against the play's context. Without this a chain cannot compound --  15.

class Play(BaseModel):
    id: str; title: str
    steps: list[Step]
    context: dict = {}                # accumulates each step's result; steps read from it

class Result(BaseModel):
    error: str | None = None          # None = the step ran; set = adapter/target failed
    status: int | None = None
    raw: str                          # response body / page text (capped 64KB)
    screenshot: str | None = None     # Browser Use already captures frames -- keep them
    extracted: dict = {}              # ids/tokens parsed out, merged into play.context
    # CUT: returned_refs (the deleted extract_refs fed it) and ok (error is the real check).

class Finding(BaseModel):
    id: str
    play_id: str
    persona_id: str
    channel: Channel
    action: str
    verdict: Verdict
    invariant_id: str | None = None   # set by the oracle from the canary's tenant rule
    cite: str | None = None           # copied off the Invariant -- the "should" half
    evidence: str                     # redacted excerpt around the match -- the "did" half
    repro: list[Step] = []            # the play's steps up to and including this one

# ---------- Session ----------
class SessionConfig(BaseModel):
    run_id: str                       # stamps every event, finding, canary, artifact dir
    repo: str
    target: str
    channels: list[Channel] = [Channel.api, Channel.chat]
    support_phone: str | None = None
    replay: str | None = None         # drive the dashboard from a recorded events.jsonl
    ci: bool = False                  # exit non-zero on breach and quit; without it the
                                      # server stays up so the dashboard stays live ( 16)

# ---------- Dashboard events (SSE payloads) ----------
# Every event carries a Literal discriminator, so the union validates on the way out and
# narrows on the way in. `type: str` would accept "banana" and the client could not narrow.
class QuestionEvent(BaseModel):
    type: Literal["question"] = "question"
    id: str; text: str; options: list[str]

class SeedEvent(BaseModel):
    type: Literal["seed"] = "seed"
    tenant_id: str                    # visual 2 builds the org chart from the stream
    persona_id: str | None = None; detail: str; artifact_id: str | None = None

class TruthUpdated(BaseModel):                    # visual 3 needs this; it did not exist
    type: Literal["truth_updated"] = "truth_updated"
    invariant: Invariant

class PhaseEvent(BaseModel):
    type: Literal["phase"] = "phase"
    phase: str                                    # reading | seeding | attacking | done

# Two events per step, so the dashboard can show work IN FLIGHT, not just completed.
class StepStarted(BaseModel):
    type: Literal["step_started"] = "step_started"
    play_id: str; persona_id: str; channel: Channel; action: str

class StepFinished(BaseModel):
    type: Literal["step_finished"] = "step_finished"
    play_id: str; persona_id: str; channel: Channel
    action: str; detail: str                      # redacted --  11b
    verdict: Verdict; invariant_id: str | None = None
    shot: str | None = None                       # Browser Use frame, relative path

class FindingEvent(BaseModel):        # visual 6 (should-vs-did) is pushed, not polled
    type: Literal["finding"] = "finding"
    finding: Finding

class ChainEvent(BaseModel):
    type: Literal["chain"] = "chain"
    play_id: str; title: str; steps: list[StepFinished]; verdict: Verdict
    compound: bool                    # proven by the control run,  15 -- not assumed
    control_verdict: Verdict | None = None   # final step run in isolation

Event = Annotated[
    QuestionEvent | SeedEvent | TruthUpdated | PhaseEvent
    | StepStarted | StepFinished | FindingEvent | ChainEvent,
    Field(discriminator="type"),
]
```

**Wire contract (freeze this, not just the field list).** The discriminator lives in the
JSON body, and the SSE `event:` name stays the default `message` -- if the server sets
`event: step_finished`, the dashboard's `es.onmessage` never fires and named listeners are
required instead. Classic silent day-one bug. The Bus assigns `seq`, emitted as the SSE
`id:` ( 16).

The dashboard is JSX ( 20), so there is **no TypeScript contract to generate** -- the
committed `contract/mock-session.ndjson` ( 22 step 1) is the contract the designer builds
against, and it is testable from both sides.

## 13. Channel interface (channels/__init__.py)

```python
from typing import Protocol
class ChannelAdapter(Protocol):
    async def act(self, step: Step) -> Result: ...
```

| Adapter | Uses | Notes |
|---|---|---|
| http.py: api | httpx | fast; endpoints from the repo |
| http.py: chat | httpx | support-chat endpoint. Both are ~10 lines; one file. |
| web.py | Browser Use (ChatMistral) | visible browser; NL tasks; keeps its frames |
| voice.py | Retell SDK | dials support_phone -- **cut from the build**,  22 |

Ref extraction is **not** part of this interface. The oracle canary-scans `Result.raw`
directly ( 14), which is what lets one detector serve all four channels.

### web.py sketch
```python
from browser_use import Agent, ChatMistral

async def act(self, step: Step) -> Result:
    agent = Agent(task=step.action,
                  llm=ChatMistral(model="mistral-large-latest"),
                  headless=False)                 # visible = demo-recordable
    h = await agent.run()
    # DOM text FIRST -- final_result() is an LLM summary and drops canaries by design.
    # No ref extraction: the oracle canary-scans raw.
    return Result(raw=cap(h.extracted_content() + "\n" + h.final_result()),
                  screenshot=save_shot(h), extracted={"urls": h.urls()})
```

## 13b. Prompts and the destructive-action guard (prompts.py)

Every browser-agent prompt lives in `prompts.py`, not inline in the adapter -- prompt tuning
is what you do most during rehearsals. Three builders with different success criteria:
`signup_task` (adapt to an unseen form), `create_task` (store content verbatim), and
`probe_task` (look, report verbatim, change nothing). All three append one shared `SAFETY`
block, and each states its own output contract.

Two details that are load-bearing:

- **Secrets never enter task text.** Tasks carry `x_email` / `x_secret`; the real values go
  to Browser Use as `sensitive_data`, so the model never sees them ( 11b rule 4). Identity
  is orthogonal to task type -- an attack step may still need to log in -- so the adapter
  appends it whenever a persona exists and the task does not already carry it.
- **"Copy EXACTLY, character for character."** Canaries only work if the agent types them
  verbatim; an agent that tidies up a name defeats the detector silently.

**The guard is two-layered, because a prompt is guidance and not a control.** `SAFETY`
forbids deleting, cancelling, and changing other people's accounts; `WebAdapter.check_urls`
then hard-blocks any URL containing delete/destroy/remove/purge/wipe/deactivate/cancel
against the URLs the agent actually visited. A blocked navigation returns `Verdict.error`
-- loud -- rather than a quiet green tile.

## 14. Oracle (oracle.py) -- the whole violation detector

Detection is a **canary scan**: every tenant's strings carry a unique tag (§4), so any
response that exposes one tenant's data to another necessarily contains that tag.

```python
def check(gt, m, step: Step, result: Result) -> Finding | None:
    if result.error:                                  # adapter/target failure
        return finding(step, Verdict.error, evidence=result.error)

    actor = next((p for p in m.personas if p.id == step.persona_id), None)
    mine  = actor.tenant_id if actor else None        # None = anonymous persona

    for canary, owner_tenant in m.canaries.items():
        if canary in result.raw and owner_tenant != mine:
            inv = tenant_isolation_invariant(gt)      # the one rule this check enforces;
            return finding(step, Verdict.breach,      # carries its id and file:line cite
                           invariant_id=inv.id if inv else None,
                           cite=inv.cite if inv else None,
                           evidence=redact(excerpt(result.raw, around=canary)))
    return None
```

### Why this design

- **One check, four channels.** A substring scan behaves identically on API JSON, page
  text, chat prose, and a voice transcript. The previous design matched on returned object
  *ids*, which only survive the API channel: a chat bot that says "Acme's Q3 invoice was
  $84,300" leaks the data without ever printing an id, and Browser Use returns an LLM
  *summary* that drops ids by design. Three of four channels were structurally blind,
  including both showpieces.
- **Deletes two undefined functions.** `extract_refs()` (four channel-specific parsers,
  the main source of false positives) and `violates_role_rule()` (undefined, and it could
  not express the plan's own flagship rule, which is per-owner not per-role) are both gone.
  The oracle is now shorter than the version it replaces.
- **It fails visibly, not silently.** The old `if artifact is None: return True` meant every
  extraction bug, normalization mismatch, crashed adapter, and expired credential rendered
  as a green tile. The tool's failure mode was "reports clean" — the worst possible failure
  for something pitched as a deploy gate. `Verdict.error` makes infrastructure failure
  distinguishable from a passing app.
- **It makes the keystone claim literally true.** Recognizing a leak in prose no longer needs
  an LLM to judge it. It is a substring match against a table we wrote.

### Known limitations (state these; don't be surprised by them)

- **Derived data.** Data the app *generates from* seeded data carries no canary: an invoice
  computed from a seeded contract, a dashboard aggregate, a PDF export. Mitigated by putting
  the canary in the tenant's **identity** strings (company name, person names, emails), so
  anything that *names* the entity inherits it. Not total.
- **Summarization and truncation.** Any channel that compresses output can drop the tag —
  Browser Use's `final_result()` most of all. Keep canaries in short, prominent fields
  (names, titles), not buried at the end of long bodies. Verify empirically when the web
  channel is first wired up.
- **Metadata-only responses.** A listing endpoint returning `{id, owner, modified}` with no
  content has nothing to canary. A 3-line cross-tenant `step.target_ref` + 2xx check closes
  this if it comes up.
- **Non-seeded data is invisible.** A leak of *real* pre-existing user data is not detected,
  because we can only recognize what we planted. The cheapest future fix is a pattern scan
  (§14b) rather than more canaries.
- **Bugs of omission.** If an authz check is missing from the handler, the code-reader infers
  intent from the same code containing the bug, so it never drafts the violated invariant.
  Canaries do not help here; differential replay (§14b) would.

### 14b. Deferred detection tiers (not built now)

Both are deterministic, need no ground truth, and bolt on without touching the schema.
Prefer either over the voice channel if spare hours appear.

| Tier | What it does | Cost |
|---|---|---|
| **Pattern scan** | regex `result.raw` for stack traces, secrets (`sk-`, `eyJ`, `AKIA`), SQL in errors, and any email/phone matching *neither* tenant — which by definition is real user data | standalone function, ~1h |
| **Differential replay** | seeding already produces a corpus of known-good authenticated requests; replay each with no auth (→ missing authentication) and as the other tenant (→ broken access control). The app contradicting itself is the oracle; no invariant needed | ~30 lines + request recording |

## 15. Campaign loop (campaign.py) -- parallel

Plays run **concurrently**, not one after another: the dashboard's whole appeal is many
lanes working at once, with attempts visibly in flight before their verdicts land.

```python
SEM = asyncio.Semaphore(8)               # global cap; also protects Mistral rate limits
TIMEOUTS = {"api": 10, "chat": 30, "web": 120, "voice": 180}

async def run_step(step, ctx, play_id) -> tuple[StepFinished, Result]:
    step = resolve(step, ctx.play.context)             # {{s2.org_id}} -> the real value
    ctx.bus.emit(StepStarted(play_id=play_id, persona_id=step.persona_id,
                             channel=step.channel, action=step.action))
    async with SEM:
        await ctx.throttle()                           # per-target min-interval
        try:
            result = await asyncio.wait_for(
                ctx.adapters[step.channel].act(step), timeout=TIMEOUTS[step.channel])
        except Exception as e:
            result = Result(error=repr(e), raw="")

    finding = oracle.check(ctx.gt, ctx.manifest, step, result)
    ev = StepFinished(play_id=play_id, persona_id=step.persona_id, channel=step.channel,
                      action=step.action, detail=ctx.redact(excerpt(result.raw)),  # same fn,
                      #                                     `around` defaults to None -> head
                      verdict=finding.verdict if finding else Verdict.benign,
                      invariant_id=finding.invariant_id if finding else None,
                      shot=result.screenshot)
    ctx.bus.emit(ev)
    if finding:
        ctx.findings.append(finding)
        ctx.bus.emit(FindingEvent(finding=finding))    # visual 6 needs the push
    return ev, result                                  # <- returning ev is what makes
                                                       #    ChainEvent.steps possible

async def run_play(play, ctx):
    events: list[StepFinished] = []
    for step in play.steps:                            # steps within a play stay ordered
        ev, result = await run_step(step, ctx, play.id)
        events.append(ev)
        play.context[step.id] = result.extracted       # feed forward to later steps
        if result.error: break                         # precondition gone; rest is noise

    if not any(e.verdict is Verdict.breach for e in events):
        return

    # CONTROL: does the last step break on its own, or only after the setup?
    last = play.steps[len(events) - 1]
    control_step = last.model_copy(update={"persona_id": ctx.control_persona(last)})
    control_ev, _ = await run_step(control_step, ctx, play.id)
    compound = control_ev.verdict is not Verdict.breach

    ctx.bus.emit(ChainEvent(play_id=play.id, title=play.title, steps=events,
                            verdict=Verdict.breach, compound=compound,
                            control_verdict=control_ev.verdict))

async def run(cfg, ctx):
    plays = hero_chains(ctx.gt, ctx.manifest)          # authored --  15 "Authoring plays"
    await asyncio.gather(*(run_play(p, ctx) for p in plays))
```

`ctx` carries `gt`, `manifest`, `bus`, `adapters`, `findings`, `redact`, `throttle`, and
`control_persona` -- one object rather than eight parameters threaded through every call.

### Authoring plays (`hero_chains`)

Hand-written Python in `campaign.py`, parameterised over the manifest, because
`{{s2.org_id}}` templates imply a human decided what step 4 reaches for. Not YAML, not
LLM-generated -- the demo depends on these and they must be deterministic.

```python
def hero_chains(gt, m) -> list[Play]:
    a, b = m.tenants[0], m.tenants[1]
    alice = persona_in(m, a, role="admin"); bob = persona_in(m, b, role="member")
    return [Play(id="p1", title="Invite across the tenant boundary", steps=[
        Step(id="s1", persona_id=alice.id, channel="api", action="create a project"),
        Step(id="s2", persona_id=alice.id, channel="api",
             action=f"invite {bob.email} to the project"),
        Step(id="s3", persona_id=bob.id,   channel="api", action="accept the invite"),
        Step(id="s4", persona_id=bob.id,   channel="chat",
             action="ask the assistant to list every invoice you can see"),
    ])]
```



### Compound chains: feed-forward + a control run

The prize find ( 1) is *"steps that each look fine, but together break in."* Two things are
needed to make that real, and neither existed in earlier drafts.

**1. Steps must pass values forward.** `Step` objects used to be fully constructed before the
play started, with `target_ref` hardcoded -- so step 4 could never use an org id created in
step 2, which is the definition of compounding. Now each step resolves `{{s2.org_id}}`
templates against `play.context`, and each `Result.extracted` merges back in.

**2. The chain verdict must be earned, not assumed.** The old rule was
`any(step == breach)` -- which only says *some step broke*, not that the setup mattered. A
judge asks "would step 4 have failed without steps 1-3?" and there was no answer; if the
answer is yes, it was an ordinary bug with three steps of ceremony in front of it.

So: **re-run the final step alone**, as a persona that took no part in the setup.
`compound = True` only when the isolated run is benign and the chained run breaches.

Use **control personas pre-seeded during step 3** -- two per tenant, never touched by any
play. Do *not* sign up a fresh persona mid-attack: signup is the least reliable code in the
system and the control run is on the demo's critical path. ~15 lines, and it turns the
headline claim into something provable on stage:

> *"Step four on its own: 403. Step four after steps one through three: 200 -- and here is
> Acme's invoice."*

Chains that fail the control are still reported, just as ordinary findings.

**Why parallel costs more than it looks:**
- **No two concurrent plays may share a persona.** Two steps using one persona's cookie jar
  or bearer token will corrupt each other's auth state and produce phantom findings. Solve
  this by **scheduling, not locking**: we seed the personas, so give each play its own. A
  lock would be a mechanism guarding a problem we can simply not create.
- **Rate limiting becomes mandatory.** Sequential execution was self-throttling. Concurrent
  runs will hit Mistral 429s (add backoff + jitter in `llm.py`) and will hammer the target
  hard enough to look like an attack. Global semaphore + a per-target min-interval.
- **Steps inside a play stay ordered.** Only the *plays* fan out. A compound chain is
  sequential by definition -- step N+1 depends on step N.
- Ordering across lanes is nondeterministic, so the dashboard must key off `play_id` +
  `seq`, never arrival order.

## 16. APIs

### Engine -> Dashboard (FastAPI, server.py)
```
GET  /state      -> { config, ground_truth, manifest: PublicManifest, findings, phase }
GET  /stream     -> text/event-stream: Event...   # live feed; see Bus contract below
POST /answer     -> { question_id: str, answer: str }                    # dev clarifies (CLI too)

# CUT: /truth, /findings, /session were three reads of the same file -> one /state.
# PublicManifest carries NO credentials --  11b.
```

### Process model (decide once, up front)

**Everything runs in one process on one event loop.** The bus is in-memory, so the FastAPI
server and the campaign runner must share it — a two-process design would give the server a
*different* `Bus` instance and `/stream` would connect successfully and then emit nothing,
forever, with no error. That silent failure is the single most likely day-one time sink.

```python
# cli.py
await asyncio.gather(
    uvicorn.Server(cfg).serve(),
    pipeline(session_cfg, bus, store),
)
```

When the pipeline finishes the server **stays up** so the dashboard remains live -- that is
the demo mode. `--ci` is the other mode: quit on completion and exit non-zero if any finding
has `verdict == breach`. One flag, and the two modes stop contradicting each other ( 23).

### Bus contract (bus.py)

```python
class Bus:
    def __init__(self):
        self._log: list[tuple[int, Event]] = []      # append-only, full run history
        self._subs: set[asyncio.Queue] = set()
        self._seq = 0

    def emit(self, ev) -> None:
        self._seq += 1
        self._log.append((self._seq, ev))
        for q in self._subs:                          # fan-out: one queue per client
            try: q.put_nowait((self._seq, ev))
            except asyncio.QueueFull: pass            # never block the attack loop

    def subscribe(self, since: int = 0) -> asyncio.Queue:
        missed = [(seq, ev) for seq, ev in self._log if seq > since]
        q = asyncio.Queue(maxsize=max(1000, len(missed) + 256))   # replay can exceed 1000
        for item in missed: q.put_nowait(item)
        self._subs.add(q); return q

    def unsubscribe(self, q) -> None:                 # /stream must call this in `finally`,
        self._subs.discard(q)                         # or dead tabs' queues fill and stay
```

`/stream` reads `request.headers.get("last-event-id")` -> `since`, and emits
`ServerSentEvent(id=str(seq), data=ev.model_dump_json())`.

**Why fan-out and replay, not one shared queue:**
- A single `asyncio.Queue` delivers each event to **exactly one** consumer. Two dashboard
  tabs split the stream 50/50 — and React **StrictMode double-mounts effects in dev**, so a
  single page opens two EventSources and silently drops half its own events. This presents
  as "SSE is flaky" and costs hours to diagnose.
- EventSource **auto-reconnects** on any drop (laptop sleep, Vite HMR, proxy timeout). With
  no `id:` and no buffer, everything sent during the gap is lost. `/findings` does not cover
  it: benign steps, seed events, and the compound chain exist *only* in the stream, so a
  reload mid-run leaves a half-empty grid and no chain.
- `put_nowait` + a bounded queue means a backgrounded or throttled tab can never apply
  backpressure to the campaign loop. Dropped frames for that client are recovered by replay.
- The `_log` is free `--replay` (drive the dashboard from a recorded run, no engine needed)
  and free run artifacts for debugging. Persist it as JSONL and both fall out.

Also required and easy to forget: `ping=15` heartbeat (sse-starlette), `Cache-Control:
no-cache`, `X-Accel-Buffering: no`, gzip off, and Vite's dev proxy configured not to buffer.
CORSMiddleware is needed for dashboard->engine — §8's "no CORS issues" is about
engine->target and does not apply here.

### Engine -> Deployment
No fixed contract. Adapters translate a `Step` into the target's **own** interfaces
(endpoints from the repo, the chat widget, login/upload flows, the phone line).

## 17. Pipeline mapping (what /baduser runs)

```
cli.py -> SessionConfig (run_id)
  1  truth.py      read repo (Mistral)       -> GroundTruth draft
  2  truth.py      ask dev                   -> GroundTruth final -> save
  3  world.py      Faker + Mistral           -> planned world (canary per tenant,  4)
     world.py      create via channels       -> Manifest -> save incrementally
  4  campaign.py   attack -- plays in PARALLEL,  15
  5  oracle.py     canary scan per result
  6  server + bus  stream to dashboard
  (server and pipeline run concurrently in ONE process -- see  16)
  (teardown = the target's DB reset script, not --cleanup)
```

## 18. Mistral usage (llm.py)

One wrapper: chat(), json(schema), vision(image).

| Job | Model |
|---|---|
| read repo -> ground truth | Large |
| synthesize world / docs | Large |
| seed an unknown signup form ( 11c) | Large (via Browser Use) |
| web channel (Browser Use) | Large |
| voice brain (Retell) -- cut from the build | Custom LLM -> Mistral |

---

# PART III — DELIVERY

## 19. Reuse vs build

| | |
|---|---|
| Reuse — web | Browser Use (Python, native Mistral). Don't build browser control. |
| Reuse — voice | Retell (team knows it; Mistral via Custom LLM). |
| Build — our edge | ground-truth builder, world synth + seeding, manifest/oracle, campaign loop. |
| Skip | Clawdbot (unsafe, wrong design). Heavy orchestrators (LangGraph/AutoGen). |
| Optional later | Promptfoo for more canned attacks; the LLM chain finder ( 25). |

## 20. Tech stack

- **Engine:** Python, uv, Pydantic, FastAPI + uvicorn + sse-starlette, Typer, httpx,
  asyncio (our own loop), Browser Use, Retell SDK, Faker, Mistral SDK.
- **Dashboard:** React + Vite (JSX), React Flow, Tailwind, EventSource.
  CUT Framer Motion (CSS transitions cover every visual) and TypeScript (the designer would
  spend the sprint type-fighting a contract that changes on day 2). KEEP React Flow -- it
  looks like the heaviest dep but visuals 1 and 5 *are* a graph, and hand-rolling layout
  plus edge routing costs more than the library.
- **Storage:** four JSON/JSONL files per run under `.baduser/runs/<run_id>/` ( 11).
- **Entry:** /baduser in the Mistral Vibe CLI.

## 21. Team split (3 people, agent-driven)

- **Pair:** engine — ground-truth builder, world synth + seeding, channels, oracle,
  campaign loop, /baduser entry.
- **Designer:** dashboard + all visuals, built against the mock feed from day one.

The engine->dashboard contract is frozen first, so the designer never waits.

### Dashboard visuals (the show)

The centrepiece is a **live parallel view**: many lanes attacking at once, attempts visibly
*in flight* before their verdicts land. This is what the two-event contract
(`step_started` / `step_finished`,  12) exists to support -- a pending row that resolves is
far more alive than a row that simply appears green.

1. **Parallel attack lanes** -- one lane per play, each showing the step in flight
   (persona, channel, what it is trying) resolving to a verdict. The main visual.
2. World assembling (org chart / users + who-can-see-what) -- background, <=8s, not a beat.
3. Dev-clarification (a question pops, gets answered, truth updates).
4. Compound chain (3 green, 1 red).
5. Breach edge (red line across a tenant boundary on the graph).
6. **Should-vs-did** on the hero finding -- the invariant with its `file:line` citation
   beside what the app actually returned, plus the Browser Use frame. Nothing else in the
   dashboard shows *what leaked*; give this the longest screen time.

Run history is nearly free: `run_id` is already on `SessionConfig` and stamped into every
event and artifact directory, so comparing runs is a read over `.baduser/runs/`.

## 22. Build order

**0. The target is vibe-coded live with Mistral.** *(nothing in earlier drafts created a
target at all, and it is on the critical path for steps 2-6)*

The demo generates the app on stage with the Mistral Vibe CLI, then immediately points
`/baduser` at it. **Mistral writes the app; Bad User breaks it.**

Why this is the right target:
- **It ends the "you planted the bug" objection.** The app is sixty seconds old and the room
  watched it get written. No other answer is this complete.
- It removes the reader's context problem -- a freshly generated app is a few hundred lines,
  so no chunking, no file selection heuristics.
- It removes the seeding blockers -- generated apps ship open signup, no CAPTCHA, no email
  verification, plain-text fields we can write canaries into.
- The pitch follows from it: *AI can write an app in sixty seconds. Nobody can review one in
  sixty seconds. This is how you trust it.*

What it costs, and the mitigations:
- **`truth.py` loses its fallback.** It must read code that did not exist when we built it,
  live. Promote it from step 3 to a step-2 concern and test it against ~10 freshly generated
  apps, not one.
- **The target is nondeterministic** -- different routes, auth shape, and schema each
  generation. Seeding must discover the signup/login flow rather than be tuned to one app.
  This is the main engineering consequence of the choice.
- **The app might not be vulnerable.** Low risk (LLM-generated multi-tenant CRUD misses
  authz constantly) but it is the entire demo. Generate the same prompt ~20 times during the
  build and measure the hit rate. If it is not near-certain, pre-generate on stage from a
  vetted seed instead of live.
- **The vibe prompt must be visibly innocent.** "A multi-tenant invoice tracker where
  companies manage their invoices" -- nothing resembling "with a vulnerability," or the bug
  is planted again, in front of the judges. Freeze the exact prompt and rehearse it.
- **Keep a pre-generated app + a recorded run as backup.** Live codegen is one more cloud
  dependency on venue wifi.
- Needs a one-command DB reset. This replaces `--cleanup` entirely.

1. models.py + server.py + Bus + mock feed -> **designer unblocked** (commit
   `contract/mock-session.ndjson`, a recorded ~60-event run the dashboard replays with no
   engine; that file, not the DTO table, is what actually unblocks them).
2. **web.py (Browser Use) + world.py seeding** + channels/http.py + oracle.py -> real
   manifest, one breach e2e. Seeding drives an unknown signup form in natural language, so
   the web channel is infrastructure, not a showpiece ( 11c) -- it cannot wait until step 5.
3. truth.py (read repo + clarify) -> real ground truth.
4. chat + plays -> the hero compound chain.
5. the visible-browser attack beat + should-vs-did panel. **Verify canaries survive
   `final_result()` in the first hour of step 2** ( 14 limitations); use DOM text if not.
6. control personas + the compound-chain control run ( 15).
7. **Freeze 3h early. Record a clean full run as backup video *before* rehearsing.** Rehearse
   from the frozen build until two consecutive clean runs.

**Why seeding moved ahead of truth-reading:** seeding is the most unproven piece (signup
forms, session tokens, upload flows against an app we do not control) *and* it produces the
manifest the oracle entirely depends on. Truth-reading has a cheap fallback -- hand-write
the ground truth -- and only has to run live for the unseen-app beat.

**Voice is out of the build order.** It appears nowhere in the  23 script, costs Retell
setup plus a Custom-LLM bridge plus a phone line, and is the most expensive seconds per
point scored. `Channel.voice` stays in the enum; if the beat is wanted, record a call
offline and cut it into the backup video.

## 23. Demo script (~3 min)

| Time | Beat |
|---|---|
| 0:00 | **Vibe CLI:** "build a multi-tenant invoice tracker where companies manage their invoices." App generates. *"This is how a lot of code gets written now. It runs. Would you ship it?"* |
| 0:35 | `/baduser --repo ./app --target localhost:3000` |
| 0:45 | Reads the code, asks one clarifying question -> answered live |
| 1:00 | World assembles (compressed) + **the keystone line** |
| 1:20 | Parallel lanes light up; a **BREACH** lands -> **should-vs-did** panel + browser frame |
| 1:55 | Compound chain: 3 benign steps, 1 breach -- **plus the control**: *"step four alone: 403. step four after one to three: 200."* |
| 2:20 | The visible browser doing it |
| 2:40 | Close: exits non-zero on a breach -- it can block a deploy |

**The keystone line must be said out loud.** It is the entire differentiator and it appeared
nowhere in earlier drafts of this script: *"Nothing here judged that. We built this world --
that invoice belongs to Acme, that persona works at Initech. It is a lookup, not a guess."*
Eight seconds, over the manifest, immediately after the first breach.

**The unseen-app beat is gone as a separate step** -- it is now step one. The app is sixty
seconds old and the room watched Mistral write it.

**Budget the run against the clock.** The script gives read + seed + attack about 100
seconds (0:35 -> 2:20) while a single web step is allowed 120s ( 15). Seeding through a
browser ( 11c) does not fit live. Either pre-seed before the demo starts and open on the
attack, or cut to two personas per tenant and seed artifacts over the api channel. **Time a
full run end to end on day one** -- this is the constraint most likely to break the script.

**Cuts, ~45s reclaimed:** org-chart assembly as a focal beat (background it), the attack grid
narrated as its own moment (the parallel lanes carry it), any architecture diagram. Voice is cut from the build entirely ( 22).

## 24. Choke points

| Risk | Fix |
|---|---|
| LLM channels flaky/slow | api + chat are the reliable core; web + voice are showpieces on top. |
| Chat bot leaks inconsistently | keep a scripted hero chain as default; tune the prompt. |
| Reader must work on never-seen code | no hand-written fallback exists once the target is generated live -- test truth.py against ~10 fresh apps, not one. |
| "You planted the bug" | the target is vibe-coded on stage seconds earlier; the prompt is visibly innocent and the room watched it. |
| Vibe-coded app happens to be correct | measure the hit rate over ~20 generations during the build; if not near-certain, pre-generate from a vetted seed. |
| Live codegen fails on venue wifi | pre-generated app + recorded run as backup. |
| Seeding pollutes a real app | the target is generated seconds earlier and reset by script; refuse to seed a non-loopback host without explicit confirmation. |
| Designer blocked on engine | frozen contract + mock feed from hour one. |
| Vibe has no custom slash commands | fall back to shelling out to `bad-user`; verify early. |
| CAPTCHA / 2FA blocks the browser | moot -- generated apps ship open signup. |
| Seeding tuned to one app shape | seeding must *discover* the signup/login flow; this is the main cost of a nondeterministic target. |

## 25. Out of scope (post-hackathon)

General app-scanning beyond the given repo, our own database, real multi-tenant
orchestration at scale, exhaustive chain search, more than the four channels.

**Auto-fix (next, not now).** Every input already exists: the finding, the repro, the
`file:line` citation, and the source. A Mistral call proposes a patch and Vibe applies it.
With a vibe-coded target this closes the full loop on stage -- *generate -> break -> patch ->
re-test -> green* -- which is a stronger ending than any static report. Deferred only
because the detection half has to be trustworthy first: auto-fixing a false positive is
worse than reporting nothing.

**Deferred detection tiers** ( 14b): pattern scan and differential replay. Both are
deterministic, need no ground truth, and are worth more than the voice channel.

**LLM chain finder** (`--find`). Cut from the build: it was never specified,  23 does not
narrate it, and the authored hero chains ( 15) carry the demo deterministically. It is the
natural next thing once the authored chains prove the shape works.
