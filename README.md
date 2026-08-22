# MistralAI_Hackathon
MistralAI Hackathon, San Francisco August 2026.

---

# Bad User

**The worst customer your product will ever have — and it files the bug report.**

Point it at a running app. It signs up, creates real data, then tries to read one
customer's records as another — across the API and the support chat — and tells you what
it saw that it shouldn't have.

```
POST /api/invites                    benign    ← normal feature
POST /api/invites/{code}/accept      benign    ← normal feature
GET  /api/documents/doc_a5351f90     BREACH    ← 200. another tenant's document.
GET  /api/documents/doc_a5351f90     benign    ← CONTROL: same request, no setup → 403

compound = True
```

Three ordinary steps, one leak, and proof the leak *needed* those steps.

## The idea

Testing "did the app leak something?" normally requires a human to judge every response.
Bad User sidesteps that: **it builds the world it tests in.**

It creates two fake companies through the app's own signup flow, stamping a unique random
tag — a *canary* — into every string each one owns: company name, people's names, emails,
document titles and bodies.

```
Acme BU7Q4KX2 Ltd
alice.bu7q4kx2@example.test
"Q3 Services Agreement BU7Q4KX2"
```

Now detection is a substring match against a table we wrote ourselves. If Initech's
session ever contains `BU7Q4KX2`, something got out. No judgement, no LLM in the loop.

That single decision is what makes one detector work across every channel. A leak in JSON,
in chatbot prose, on a rendered page, or in a phone transcript is the *same check* —
which matters, because a chatbot saying *"Acme's Q3 invoice came to $84,300"* leaks the
data without ever printing an ID for a conventional scanner to match on.

## Quickstart

Needs [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Gabe-Soler/MistralAI_Hackathon.git
cd MistralAI_Hackathon
make setup
```

`make setup` installs dependencies, puts **`bad-user` on your PATH**, and installs the
**`/baduser` command** into [Mistral Vibe](https://github.com/mistralai/vibe).

Then a key — Bad User reads it from the environment, `./.env`, or `~/.vibe/.env`:

```bash
echo 'MISTRAL_API_KEY=...' >> ~/.vibe/.env
```

The key is only needed to **read a repo for rules**. Without one it still runs: routes come
from `/openapi.json` and it assumes tenant isolation.

### See it work

```bash
make target     # terminal 1 — a deliberately vulnerable app on :8000
make live       # terminal 2 — real run; dashboard opens at :8787
```

No target and no key? `make mock` drives the dashboard from a scripted run.

## Using it from Mistral Vibe

```bash
vibe --trust
> /baduser against http://127.0.0.1:8000 with --repo ./target
```

> **`--trust` is not optional.** Vibe ignores project and global skills in an untrusted
> folder — it prints one easily-missed warning and `/baduser` simply won't exist.

After `make setup` the command works in **any** directory. The full loop, two prompts:

```bash
mkdir demo && cd demo && vibe --trust

> build me a multi-tenant invoice tracker with company signup, invoices, documents
  and a support chat endpoint. FastAPI + SQLite, on port 3000.

> /baduser against http://127.0.0.1:3000 with --repo .
```

Mistral writes the app; Bad User breaks it. Nothing was planted — the app is a minute old.

## Reading the output

| verdict | meaning |
|---|---|
| `breach` | another tenant's canary came back where it shouldn't have |
| `benign` | nothing of ours came back, **or** the app correctly refused |
| `error` | the step itself failed — **never** counted as the app passing |

`compound=True` on a chain means the final step only breaches *because* of the earlier
ones. That's measured, not claimed: the final step is re-run alone as a persona that took
no part in the setup, and the chain only counts as compound if that run comes back clean.

### It refuses to report clean when it doesn't know

The worst way for a tool like this to fail is a green dashboard that means nothing. Three
guards, all deliberately loud:

- **fewer than 2 tenants seeded** → hard error. With one tenant every cross-tenant check
  passes vacuously.
- **no step reached the target** → `NO STEP REACHED THE TARGET`, never "0 breaches".
- **an adapter crash** → `error`, never `benign`.

## How a run works

```
1  read     truth.py      Mistral reads the repo → invariants, with file:line citations
2  discover discover.py   /openapi.json + the repo → candidate routes
3  seed     world.py      two companies created through the app's OWN signup and forms
4  attack   campaign.py   plays run in parallel; steps within a play stay ordered
5  check    oracle.py     canary scan on every response
6  report   bus.py        streamed live to the dashboard over SSE
```

Seeding goes through the product's real interfaces, never direct DB inserts. That's the
whole differentiator over a conventional scanner: the signup path gets tested for free,
and the data is reachable exactly the way real data is.

Route discovery layers three sources because each is weak where the others are strong —
the repo read is broad but unverified, OpenAPI is exact but not always present, and
seeding proves only the handful of routes it needs.

## Layout

```
engine/              the engine (Python, uv) + 137 tests
  baduser/
    oracle.py        the detector — ~100 lines, the actual product
    world.py         world synthesis + seeding
    campaign.py      plays, the parallel runner, the control run
    discover.py      route discovery
    channels/        api + chat (httpx), web (Browser Use), fake (tests)
    static/          the dev dashboard — one self-contained file, no build step
target/              a vulnerable app: 4 planted bugs, 4 deliberately correct routes
evals/               the validation loop
BadChat-FrontEnd/    the React landing page (Vite + Tailwind + shadcn)
.vibe/skills/        the /baduser command for Mistral Vibe
PLAN.md              the design doc, with the reasoning behind each decision
```

## Working on it

**Engine ↔ dashboard contract** (frozen, PLAN §16):

| endpoint | purpose |
|---|---|
| `GET /state` | snapshot: config, ground truth, public manifest, findings, phase |
| `GET /stream` | SSE; honours `Last-Event-ID` on reconnect |
| `POST /answer` | answer a clarifying question |

`engine/baduser/static/dashboard.html` is a working reference implementation of both —
easier to read than the spec. **`make mock` emits the exact event stream a real run does**,
with no target and no API key, so frontend work never blocks on the engine.

**Editing the slash command:** edit `.vibe/skills/baduser/SKILL.md` (version controlled),
then `make install-skill`. Editing the copy in `~/.vibe/skills/` only changes your laptop.

## Troubleshooting

| symptom | cause |
|---|---|
| `/baduser` doesn't exist in Vibe | not started with `--trust` |
| `port 8787 is already in use` | an earlier run is still up — stop it, or `--port 8788` |
| `seeding failed: could not seed 2 tenants` | signup isn't open (email verification, OAuth, or CAPTCHA). Bad User can't test an app it can't get accounts on. |
| `NO STEP REACHED THE TARGET` | the plays don't match this app's API. Check the URL, and that it serves `/openapi.json` or that `--repo` was passed. |
| the run never exits | by design — the server stays up so the dashboard stays live. Use `--ci` to quit on completion. |
| `no usable LLM` | no `MISTRAL_API_KEY` found. The run continues without the repo read. |

## Testing

```bash
make check     # lint + 137 tests + eval
```

The eval is the one that matters. It boots the vulnerable app, seeds it, and asserts
**both** directions: every planted bug detected, **and zero findings on the routes that
behave correctly.** That second half is what keeps a green dashboard meaningful.

```
detected        4/4 planted bugs
false positives 0
EVAL PASSED
```

## Limits

Bad User needs open signup, a multi-tenant model, and a JSON API. That describes almost
anything an AI code generator produces. It does **not** yet handle email verification,
OAuth-only signup, CAPTCHA, GraphQL, or server-rendered apps with no API — and in each of
those cases it fails loudly rather than reporting a clean run.
