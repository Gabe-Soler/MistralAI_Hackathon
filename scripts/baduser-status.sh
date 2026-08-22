#!/usr/bin/env bash
# What is running right now. Run this before wondering why the dashboard is stale.
set -u
BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'

printf '%s\n' "${BOLD}engine runs${OFF}"
if pgrep -f 'bad-user --' >/dev/null 2>&1; then
  pgrep -lf 'bad-user --' | sed 's/^/  /'
else
  echo "  ${DIM}none${OFF}"
fi

printf '\n%s\n' "${BOLD}dashboards${OFF}"
any=0
for p in $(seq 8787 8797); do
  s=$(curl -s --max-time 1 "http://127.0.0.1:$p/state" 2>/dev/null) || continue
  [ -z "$s" ] && continue
  printf '  :%s  %s\n' "$p" "$(printf '%s' "$s" | python3 -c '
import sys,json
try:
    s=json.load(sys.stdin); c=s.get("config") or {}
    print(f'"'"'{c.get("run_id","?")}  target={c.get("target","?")}  phase={s.get("phase","?")}  findings={len(s.get("findings",[]))}'"'"')
except Exception: print("(not a Bad User dashboard)")' 2>/dev/null)"
  any=1
done
[ "$any" = 0 ] && echo "  ${DIM}none${OFF}"

printf '\n%s\n' "${BOLD}target apps${OFF}"
any=0
for p in 3000 8000 8080 5000; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 "http://127.0.0.1:$p/openapi.json" 2>/dev/null)
  [ "$code" = "200" ] && { echo "  :$p  serving /openapi.json"; any=1; }
done
[ "$any" = 0 ] && echo "  ${DIM}none with an OpenAPI schema on 3000/8000/8080/5000${OFF}"

printf '\n%s\n' "${BOLD}reminder${OFF}"
echo "  ${DIM}a dashboard belongs to ONE run. A new run starts its own server (next free"
echo "  port if 8787 is taken) -- an old tab will never show a new run.${OFF}"
