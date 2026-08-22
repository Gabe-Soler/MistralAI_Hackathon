# The target app

`target/app.py` is a small multi-tenant invoice tracker (FastAPI + SQLite). It stands in for
the app Mistral vibe-codes on stage (PLAN 22 step 0), and it doubles as the eval fixture:
we know exactly what is wrong with it, so `evals/run_eval.py` can score the oracle in both
directions — every planted bug found, nothing flagged on the routes that are correct.

It ships the properties seeding depends on (PLAN 11c): **open signup** (no CAPTCHA, no email
verification), **bearer-or-cookie auth**, and **free-text `title`/`body` fields** that canaries
get written into. Signup joins an existing company by name — that is how the seeder puts
several personas in one tenant, and it is not one of the graded bugs.

## Run it

```sh
make target                  # http://127.0.0.1:8000  (docs at /docs)
./target/reset.sh            # drop + recreate the DB; idempotent
make eval                    # boot it on a free port and score the oracle against it
```

The DB is `target/invoices.db`, overridable with `BADUSER_TARGET_DB`. `reset.sh` honours it,
which is how the eval keeps its own throwaway `target/eval.db`.

## Planted bugs

Each has a stable id in a comment next to the code that is wrong.

| id | route | what is wrong |
|---|---|---|
| **BUG-1** | `GET /api/invoices/{id}` | IDOR. The row is looked up by id and returned; ownership is never checked. |
| **BUG-2** | `GET /api/invoices` | Listing leak. Authenticated, then `SELECT * FROM invoices` with no `WHERE tenant_id`. |
| **BUG-3** | `POST /api/chat` | Chat leak. Retrieval has no tenant filter, so it answers out of every tenant's content — **in prose, with no row id in the reply**. This is the leak an id-matching detector structurally cannot see, and the reason canaries exist (PLAN 14). |
| **BUG-4** | `POST /api/invites/{code}/accept` | Compound. The invite is scoped to one invoice, but accepting it writes a membership row for the whole inviting tenant and says nothing about it. Every later tenant check then passes for the invitee. |

BUG-4 is the demo centrepiece because it is only visible as a chain. The final read is
`GET /api/documents/{id}` — a route that is *correct*:

* **without** the invite → `403` (`CTRL-BUG-4`, the control run)
* **with** the invite → `200` (`BUG-4`)

Same persona, same request, same document. Step four alone: 403. Step four after one to
three: 200.

## Deliberately correct (the false-positive test)

| id | expectation |
|---|---|
| **OK-1** | `GET /api/invoices/{id}` for an invoice the caller **does** own → `200`. |
| **OK-2** | `GET /api/documents/{id}` across tenants → `403`, **and the body quotes the requested id and title**. The refusal therefore contains the other tenant's canary. A naive detector calls this a leak; the oracle must not (`looks_like_denial`). This is the most important correctness case in the suite. |
| **OK-3** | `GET /api/me` → the caller's own row only. |
| **OK-4** | any protected route with no credentials → `401`. |

If you change this file, re-run `make eval`. Bug line numbers are cited in findings, so the
`BUG-*` markers are load-bearing: `run_eval.py` resolves the invariant's `file:line` by
finding the `BUG-1 (IDOR)` marker.
