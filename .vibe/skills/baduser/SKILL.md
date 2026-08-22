---
name: baduser
description: Run Bad User against a running app to find cross-tenant data leaks, broken access control, and compound attack chains. Use when the user asks to test, attack, audit, or security-check an app they are running, or says /baduser. Seeds a fake world into the target through its own signup flow and reports what leaked, with a live dashboard.
---

# Bad User

Point it at a running app. It seeds two fake companies through the app's own signup and
create flows, then tries to read one company's data as the other, across the API and the
support chat. Because it built the world, detecting a leak is a substring match on a
planted canary, not a guess.

`bad-user` is installed globally -- run it from anywhere, no `cd` needed.

## IMPORTANT: how to invoke it without hanging

Without `--ci` the process **never exits** -- the server deliberately stays up so the
dashboard stays live. Running that in the foreground blocks you forever.

**Pick one. Both are a SINGLE bash call. Never poll across turns.**

1. **Just want the result** (default choice):
   ```bash
   bad-user --target <URL> --repo <REPO_DIR> --channels api,chat --ci --no-open
   ```

2. **User wants the dashboard left live** -- background it and block on the log in the
   same command, so it costs one turn:
   ```bash
   (bad-user --target <URL> --repo <REPO_DIR> --channels api,chat --no-open \
      > /tmp/baduser.log 2>&1 &) ; \
   for _ in $(seq 120); do \
     grep -qE "BREACH|NO STEP REACHED|seeding failed|Traceback" /tmp/baduser.log \
       && break; sleep 2; done; \
   cat /tmp/baduser.log
   ```
   The dashboard then stays on `http://127.0.0.1:8787`.

## Flags

| flag | meaning |
|---|---|
| `--target <url>` | the running app. **Required.** Non-loopback needs confirmation. |
| `--repo <path>` | source dir to read for rules, via Mistral. **Pass this whenever you know where the app's source is** -- usually `.` when you just built it. Without it the run assumes only tenant isolation. |
| `--channels api,chat` | which channels to use. |
| `--ci` | exit non-zero on a breach and quit. |
| `--no-open` | do not open a browser tab. |
| `--rps 5` | cap requests/sec against the target. |
| `--port 8787` | dashboard port; change it if 8787 is taken. |

`MISTRAL_API_KEY` is read from the environment, `./.env`, or `~/.vibe/.env`.

## What to do when asked to run it

1. **Make sure the app is actually running** and you know its URL and port. If you just
   built it, start it first (backgrounded) and confirm it responds before attacking it.
2. **Pass `--repo`** pointing at the app's source when you know it. That is what lets
   Mistral read the code and state the rules the app is supposed to follow.
3. **Run it** using one of the two recipes above.
4. **Report**: tenants seeded, routes discovered, breach count, and whether any compound
   chain was proven. Give the user the dashboard URL.

## Reading the result

- `breach` -- another tenant's canary appeared where it should not have.
- `benign` -- nothing of ours came back, or the app correctly refused.
- `error` -- the step itself failed. **Never** report this as the app passing.
- `compound=True` on a chain means the final step only breaches *because* of the earlier
  steps, proven by re-running it alone as an uninvolved persona.

## Failure modes -- say these plainly, never as a clean result

- **`NO STEP REACHED THE TARGET`** -- the plays did not match this app's API. Not a pass.
  Check the URL, and that the app serves `/openapi.json` or that `--repo` was passed.
- **`seeding failed: could not seed 2 tenants`** -- signup is not open (email
  verification, OAuth, or CAPTCHA). Bad User cannot test the app without accounts.
- **0 breaches with everything green** -- only credible if steps actually reached the app.
  Check the log before calling it clean.

## Do not

- Point it at production or anything the user does not own.
