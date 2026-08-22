# Bad User -- one-command gate.
#
#   make check   lint + test + eval. Green here means the oracle detects every planted bug
#                in target/app.py and fires on none of the correct routes.
#
# Everything runs offline, no API keys.

.PHONY: install test lint target eval check

UV := cd engine && uv run --extra dev

install:
	cd engine && uv sync --extra dev

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

# The validation loop: boots the target on a free port, seeds two tenants, asserts every
# BUG-* is detected and every OK-* is not. Non-zero on any mismatch.
eval:
	$(UV) python ../evals/run_eval.py

check: lint test eval
