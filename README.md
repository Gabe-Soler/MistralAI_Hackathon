# MistralAI_Hackathon
MistralAI Hackathon, San Francisco August 2026.

---

## Bad User

A dev tool that emulates a badly-behaved customer using your product — signing up,
poking every surface you shipped — and reports what it saw that it shouldn't have.

The trick: **Bad User builds the test world itself.** It creates two fake companies
through the app's own signup and create flows, stamping a unique canary tag into every
string each company owns. So when one company's canary turns up in the other's response,
that isn't a judgement call — it's a substring match against a table we wrote.

That one decision is what makes it work across every channel at once. A leak in JSON, in
chat prose, on a rendered page, or in a phone transcript is the same check.

### Setup

Needs [uv](https://docs.astral.sh/uv/). One command:

```bash
make setup
```

That installs dependencies, puts `bad-user` on your PATH, and installs the `/baduser`
command into [Mistral Vibe](https://github.com/mistralai/vibe).

Set your key (either location works):

```bash
echo 'MISTRAL_API_KEY=...' >> ~/.vibe/.env    # Vibe uses this too
```

The key is only needed to **read a repo for rules**. Without one, Bad User still runs —
it discovers routes from `/openapi.json` and assumes tenant isolation.

### Try it

```bash
make target     # terminal 1 — a deliberately vulnerable app on :8000
make live       # terminal 2 — real run, dashboard opens at :8787
```

Or with no target at all, to see the dashboard on a scripted run:

```bash
make mock
```

### Use it from Mistral Vibe

```bash
vibe --trust
> /baduser against http://127.0.0.1:8000 with --repo ./target
```

`--trust` matters: Vibe ignores project config in an untrusted folder. After
`make setup` the command also works in **any** directory — including a scratch dir where
you just vibe-coded an app:

```bash
mkdir demo && cd demo && vibe --trust
> build me a multi-tenant invoice tracker with signup, invoices, documents and a
  support chat endpoint. FastAPI + SQLite on port 3000.
> /baduser against http://127.0.0.1:3000 with --repo .
```

### What it reports

| verdict | meaning |
|---|---|
| `breach` | another tenant's canary came back where it shouldn't have |
| `benign` | nothing of ours came back, or the app correctly refused |
| `error` | the step itself failed — **never** counted as the app passing |

A chain marked `compound=True` means the final step only breaches *because* of the
earlier ones. That's proven, not asserted: the final step is re-run alone as a persona
that took no part in the setup, and the chain only counts as compound if that run is
clean.

Two things it refuses to do quietly, because "reports clean" is the worst way for a
tool like this to fail:

- fewer than 2 tenants seeded → hard error, not a degraded run
- no step reaching the target → `NO STEP REACHED THE TARGET`, not 0 breaches

### Layout

```
engine/    the engine (Python, uv) + its tests
target/    a vulnerable multi-tenant app: 4 planted bugs, 4 deliberately correct routes
evals/     the validation loop — asserts every bug is found AND none of the correct
           routes are flagged
.vibe/     the /baduser command for Mistral Vibe
```

### Checking it still works

```bash
make check    # lint + tests + eval
```

The eval is the one that matters. It asserts **both** directions — every planted bug
detected, and zero findings on the routes that behave correctly. The second half is what
stops a green dashboard from being meaningless.
