#!/usr/bin/env bash
# Samples CPU and memory every INTERVAL seconds until killed, as CSV:
#   ts, name, cpu_percent, mem
# for the host agent process (by PID) and the local stack's containers.
#   AGENT_PID=<uvicorn pid> agent/load-test/w2/sample_resources.sh > samples.csv
set -uo pipefail
INTERVAL="${INTERVAL:-2}"
CONTAINERS="${CONTAINERS:-development-easy-openemr-1 development-easy-mysql-1}"
echo "ts,name,cpu_percent,mem"
while true; do
  ts=$(date -u +%H:%M:%S)
  if [ -n "${AGENT_PID:-}" ]; then
    ps -o %cpu=,rss= -p "$AGENT_PID" | awk -v ts="$ts" '{printf "%s,agent,%s,%.1fMiB\n", ts, $1, $2/1024}'
  fi
  # shellcheck disable=SC2086
  docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' $CONTAINERS \
    | awk -v ts="$ts" -F, '{gsub(/%/,"",$2); split($3,m," / "); printf "%s,%s,%s,%s\n", ts, $1, $2, m[1]}'
  sleep "$INTERVAL"
done
