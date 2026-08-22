#!/usr/bin/env bash
# Stop everything Bad User started and clear the state it left behind.
#
# Written after killing the wrong process three times with ad-hoc `lsof | xargs kill`:
# match on the exact command, never on a bare port, and never touch a process this
# project did not start.
set -u

BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'
say() { printf '  %s\n' "$*"; }

hard=0; keep_runs=0
for a in "$@"; do
  case "$a" in
    --all)       hard=1 ;;
    --keep-runs) keep_runs=1 ;;
    -h|--help)
      echo "usage: baduser-clean [--all] [--keep-runs]"
      echo "  --all        also stop target apps this project started (uvicorn app:app)"
      echo "  --keep-runs  leave .baduser/runs/ alone"
      exit 0 ;;
  esac
done

printf '%s\n' "${BOLD}stopping engine runs${OFF}"
n=$(pgrep -f 'bad-user --' 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" -gt 0 ]; then pkill -f 'bad-user --' 2>/dev/null; sleep 1; say "stopped $n"; else say "${DIM}none running${OFF}"; fi

if [ "$hard" = 1 ]; then
  printf '%s\n' "${BOLD}stopping target apps${OFF}"
  # Only uvicorn processes serving app:app -- not your editor, not someone else's server.
  m=$(pgrep -f 'uvicorn app:app' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$m" -gt 0 ]; then pkill -f 'uvicorn app:app' 2>/dev/null; sleep 1; say "stopped $m"; else say "${DIM}none running${OFF}"; fi
fi

printf '%s\n' "${BOLD}dashboard ports${OFF}"
busy=""
for p in $(seq 8787 8797); do
  pid=$(lsof -tiTCP:$p -sTCP:LISTEN 2>/dev/null | head -1)
  [ -n "$pid" ] && busy="$busy $p(pid $pid)"
done
[ -n "$busy" ] && say "still held:$busy  ${DIM}-- not ours, left alone${OFF}" || say "${DIM}8787-8797 all free${OFF}"

if [ "$keep_runs" = 0 ]; then
  printf '%s\n' "${BOLD}run artifacts${OFF}"
  found=0
  # Runs land relative to wherever bad-user was invoked, so sweep the usual spots.
  for d in ./.baduser engine/.baduser "$HOME/Desktop/baduser-demo/.baduser"; do
    if [ -d "$d/runs" ]; then
      c=$(find "$d/runs" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
      rm -rf "${d:?}/runs"; say "removed $c run(s) from $d"; found=1
    fi
  done
  [ "$found" = 0 ] && say "${DIM}none found${OFF}"
fi

printf '%s\n' "${BOLD}target databases${OFF}"
db=$(find target "$HOME/Desktop/baduser-demo" -maxdepth 1 -name '*.db' 2>/dev/null)
if [ -n "$db" ]; then
  echo "$db" | while read -r f; do rm -f "$f"; say "removed $f"; done
  say "${DIM}restart any target app -- it creates its schema at startup${OFF}"
else
  say "${DIM}none found${OFF}"
fi

echo
say "clean."
