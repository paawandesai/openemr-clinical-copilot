#!/usr/bin/env bash
# Breaks each /ready dependency on the LOCAL stack, one at a time, and records
# what /health and /ready answer while it is broken and after it is restored.
#
#   AGENT=http://127.0.0.1:8100 START_AGENT=<script that starts an agent> \
#     agent/load-test/w2/ready_failure_modes.sh
#
# DISRUPTIVE, LOCAL ONLY. It `docker pause`s the local OpenEMR container for
# ~25 s and the local Langfuse web container for up to ~90 s, and unpauses
# both on exit even if interrupted. Never point it at a shared host.
#
# The LLM-provider cases do not touch Anthropic: they start a second agent on
# PORT2 whose key is invalid, then one whose API base URL has no listener —
# the same two faults the agent sees when a key is revoked or the provider is
# unreachable. START_AGENT must honour PORT, LOG and ANTHROPIC_KEY_OVERRIDE /
# ANTHROPIC_BASE_URL (agent/load-test/w2/README.md shows the one used).
set -uo pipefail

AGENT="${AGENT:-http://127.0.0.1:8100}"
PORT2="${PORT2:-8101}"
OPENEMR_CONTAINER="${OPENEMR_CONTAINER:-development-easy-openemr-1}"
LANGFUSE_CONTAINER="${LANGFUSE_CONTAINER:-clinical-copilot-langfuse-langfuse-web-1}"
: "${START_AGENT:?START_AGENT=<path to a script that starts an agent> is required}"
# Any string the provider will reject. Supplied by the caller rather than
# written here, so no key-shaped literal sits in the repository.
: "${INVALID_ANTHROPIC_KEY:?set INVALID_ANTHROPIC_KEY to any string Anthropic will reject}"
: "${INVALID_LANGFUSE_SECRET:?set INVALID_LANGFUSE_SECRET to any string Langfuse will reject}"
LOGDIR="${LOGDIR:-$(mktemp -d)}"

cleanup() {
  docker unpause "$OPENEMR_CONTAINER" >/dev/null 2>&1 || true
  docker unpause "$LANGFUSE_CONTAINER" >/dev/null 2>&1 || true
  [ -n "${SECOND:-}" ] && kill "$SECOND" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

now() { date -u +%H:%M:%S; }

probe() { # probe <base> <label>
  local ready health
  health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1/health")
  ready=$(curl -s --max-time 20 -w ' HTTP=%{http_code}' "$1/ready")
  printf '%s  %-34s /health=%s  /ready=%s\n' "$(now)" "$2" "$health" "$ready"
}

# Poll /ready until its body matches a pattern, or give up after N seconds.
wait_for() { # wait_for <base> <grep pattern> <seconds> <label>
  local deadline=$((SECONDS + $3)) body
  while [ $SECONDS -lt $deadline ]; do
    body=$(curl -s --max-time 20 "$1/ready")
    if printf '%s' "$body" | grep -q "$2"; then probe "$1" "$4"; return 0; fi
    sleep 2
  done
  probe "$1" "$4 (TIMED OUT waiting)"
  return 1
}

start_second() { # start_second <label> [env assignments...]
  local label=$1; shift
  env PORT="$PORT2" LOG="$LOGDIR/agent-$PORT2-$label.jsonl" "$@" "$START_AGENT" &
  SECOND=$!
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT2/health" && return 0
    sleep 1
  done
  echo "second agent did not start" >&2
}

stop_second() { kill "$SECOND" >/dev/null 2>&1; wait "$SECOND" 2>/dev/null; SECOND=""; sleep 1; }

echo "== baseline"
probe "$AGENT" "all dependencies up"

echo
echo "== 1. OpenEMR: container paused (hangs, then times out)"
docker pause "$OPENEMR_CONTAINER" >/dev/null
wait_for "$AGENT" '"fhir":{"status":"unhealthy"' 40 "OpenEMR paused"
docker unpause "$OPENEMR_CONTAINER" >/dev/null
wait_for "$AGENT" '"status":"ready"' 60 "OpenEMR restored"

echo
echo "== 2a. LLM provider: API key rejected (second agent, invalid key)"
start_second badkey ANTHROPIC_KEY_OVERRIDE="$INVALID_ANTHROPIC_KEY" LANGFUSE=off
probe "http://127.0.0.1:$PORT2" "invalid Anthropic key"
stop_second

echo
echo "== 2b. LLM provider: unreachable (second agent, API base with no listener)"
start_second unreachable ANTHROPIC_BASE_URL=http://127.0.0.1:9 LANGFUSE=off
probe "http://127.0.0.1:$PORT2" "Anthropic API unreachable"
stop_second

echo
echo "== 3. Observability backend: Langfuse web paused"
docker pause "$LANGFUSE_CONTAINER" >/dev/null
wait_for "$AGENT" '"observability_backend":{"status":"unhealthy"' 100 "Langfuse paused"
docker unpause "$LANGFUSE_CONTAINER" >/dev/null
wait_for "$AGENT" '"observability_backend":{"status":"healthy"' 60 "Langfuse restored"

echo
echo "== 3b. Observability backend: keys rejected (second agent, wrong secret)"
start_second badlf LANGFUSE_SECRET_KEY_OVERRIDE="$INVALID_LANGFUSE_SECRET"
wait_for "http://127.0.0.1:$PORT2" '"observability_backend":{"status":"unhealthy"' 40 "Langfuse keys rejected"
stop_second

echo
echo "== restored"
probe "$AGENT" "all dependencies up"
echo "second-agent logs: $LOGDIR"
