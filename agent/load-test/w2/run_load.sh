#!/usr/bin/env bash
# One load level: resource sampler + k6, outputs side by side.
#   OUT=<dir> NAME=ask-10vu SCRIPT=k6-ask.js VUS=10 DURATION=60s AGENT_PID=<pid> \
#   AGENT_TOKEN=... agent/load-test/w2/run_load.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${OUT:?}" "${NAME:?}" "${SCRIPT:?}" "${AGENT_TOKEN:?}"
mkdir -p "$OUT"
"$HERE/sample_resources.sh" > "$OUT/$NAME-resources.csv" &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT
sleep 2   # an at-rest sample before load starts
k6 run --quiet --summary-export="$OUT/$NAME.json" "$HERE/$SCRIPT" > "$OUT/$NAME.txt" 2>&1 || true
sleep 4   # and one while it drains
kill $SAMPLER 2>/dev/null || true
tail -40 "$OUT/$NAME.txt"
