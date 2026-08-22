#!/usr/bin/env bash
# Drop and recreate the target's SQLite DB. Idempotent -- safe to run any number of times,
# whether or not the DB exists. This is the teardown the plan calls for (PLAN 22 step 0);
# it replaces --cleanup entirely.
#
#   ./target/reset.sh
#   BADUSER_TARGET_DB=/tmp/eval.db ./target/reset.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="${BADUSER_TARGET_DB:-$DIR/invoices.db}"
PY="${PYTHON:-python3}"

rm -f "$DB" "$DB-wal" "$DB-shm"

if "$PY" -c "import fastapi" >/dev/null 2>&1; then
  BADUSER_TARGET_DB="$DB" "$PY" -c "
import sys
sys.path.insert(0, '$DIR')
import app
app.init_db()
print('reset: recreated', app.DB_PATH)
"
else
  echo "reset: removed $DB (no fastapi on $PY; the app recreates the schema at startup)"
fi
