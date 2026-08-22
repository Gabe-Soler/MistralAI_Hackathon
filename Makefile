# Bad User -- one-command gate.
#
#   make check   lint + test + eval. Green here means the oracle detects every planted bug
#                in target/app.py and fires on none of the correct routes.
#
# Everything runs offline, no API keys.

.PHONY: setup install install-cli install-skill test lint target mock demo live eval check clean clean-all status web

UV := cd engine && uv run --extra dev

# One-command setup for a fresh clone: deps, the global `bad-user` command, and the
# /baduser skill for Mistral Vibe.
setup: install install-cli install-skill
	@echo
	@echo "  ready. try:  make target   (terminal 1)"
	@echo "               make live     (terminal 2)"
	@echo "  or from any directory:  vibe --trust  ->  /baduser against <url>"

install:
	cd engine && uv sync --extra dev

# Puts `bad-user` on PATH so it runs from any directory, not just this repo.
install-cli:
	uv tool install --force ./engine

# Copies the /baduser command into Vibe's global skills dir, so it is available in every
# vibe session -- including the scratch directory where you vibe-code the target app.
install-skill:
	mkdir -p ~/.vibe/skills/baduser
	cp .vibe/skills/baduser/SKILL.md ~/.vibe/skills/baduser/SKILL.md
	@echo "  /baduser installed globally (project copy stays in .vibe/skills/)"

test:
	$(UV) pytest -q

# Rule set pinned on the CLI, not left to the installed ruff's defaults: engine/pyproject.toml
# sets only line-length, and 0.16 turns on far more than E/F, so an unpinned gate goes red or
# green depending on which ruff a machine happens to resolve.
lint:
	$(UV) ruff check . ../target ../evals   # rule set lives in engine/pyproject.toml

# The vibe-coded target app on :8000 (PLAN 22 step 0). ./target/reset.sh wipes its DB.
target:
	$(UV) python -m uvicorn app:app --app-dir ../target --host 127.0.0.1 --port 8000 --reload

# The dashboard, driven by a scripted run: no target app, no API keys. Opens a browser.
#   make mock            realtime pacing
#   make mock SPEED=0.2  fast rehearsal
SPEED ?= 1.0

mock:
	$(UV) bad-user --mock --speed $(SPEED)

# A REAL run against the local target app, dashboard open. Start `make target` first.
live:
	$(UV) bad-user --target http://127.0.0.1:8000 --channels api,chat

demo:
	$(UV) bad-user --repo ../target --target http://127.0.0.1:8000 --channels api,chat

# The validation loop: boots the target on a free port, seeds two tenants, asserts every
# BUG-* is detected and every OK-* is not. Non-zero on any mismatch.
eval:
	$(UV) python ../evals/run_eval.py

# What is running right now -- run this first when a dashboard looks stale. A dashboard
# belongs to ONE run; a new run starts its own server on the next free port, so an old
# browser tab will never show new data.
status:
	@./scripts/baduser-status.sh

# Stop engine runs and clear run artifacts + target databases.
clean:
	@./scripts/baduser-clean.sh

# Also stop target apps this project started (uvicorn app:app). Restart them afterwards --
# they create their schema at startup, so a dropped database needs a fresh boot.
clean-all:
	@./scripts/baduser-clean.sh --all

# Build the dashboard into engine/baduser/web/, where the server picks it up. Without
# this the engine still runs -- / falls back to the self-contained dev dashboard.
web:
	cd BadChat-FrontEnd && npm run build

check: lint test eval
