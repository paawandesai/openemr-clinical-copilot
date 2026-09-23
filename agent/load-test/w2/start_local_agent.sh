#!/usr/bin/env bash
# Starts THIS checkout's agent on the host, against the LOCAL OpenEMR stack
# (docs/w2/LOCAL_ENV.md), with its stdout event log going to $LOG — the file
# the dashboard (agent/scripts/w2_dashboard.py) reads.
#
#   LOG=agent/logs/run.jsonl [PORT=8100] [STUB=false] [LANGFUSE=on] \
#   AGENT_ENV_FILE=<file with ANTHROPIC_API_KEY=...> \
#   LANGFUSE_ENV_FILE=<agent-langfuse.env from agent/observability/generate-env.sh> \
#     agent/load-test/w2/start_local_agent.sh
#
# Secrets are read from the two files and never echoed. Overrides, applied
# after the files are read (agent/load-test/w2/ready_failure_modes.sh uses
# them to break one dependency at a time):
#   ANTHROPIC_KEY_OVERRIDE, ANTHROPIC_BASE_URL, FHIR_BASE_URL_OVERRIDE,
#   LANGFUSE_SECRET_KEY_OVERRIDE, LANGFUSE=off
#
# Why the host and not the committed container: the local container runs
# whatever image was last built, which may not be this branch, and other
# worktrees share it. A host process on its own port is this branch and
# nobody else's.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$HERE/../.." && pwd)"
cd "$AGENT_DIR"

: "${LOG:?LOG=<path> is required}"
: "${AGENT_ENV_FILE:?AGENT_ENV_FILE=<file holding ANTHROPIC_API_KEY> is required}"

ANTHROPIC_API_KEY="$(grep '^ANTHROPIC_API_KEY=' "$AGENT_ENV_FILE" | cut -d= -f2-)"
export ANTHROPIC_API_KEY="${ANTHROPIC_KEY_OVERRIDE:-$ANTHROPIC_API_KEY}"
export FHIR_BASE_URL="${FHIR_BASE_URL_OVERRIDE:-https://localhost:9300/apis/default/fhir}"
export FHIR_VERIFY_TLS=false
export AGENT_STUB_LLM_DRAFTING="${STUB:-false}"
export AGENT_PRELOAD_RETRIEVAL="${PRELOAD_RETRIEVAL:-false}"

if [ "${LANGFUSE:-on}" = on ]; then
  : "${LANGFUSE_ENV_FILE:?LANGFUSE_ENV_FILE is required unless LANGFUSE=off}"
  set -a
  # shellcheck disable=SC1090
  . "$LANGFUSE_ENV_FILE"
  set +a
  export LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY_OVERRIDE:-$LANGFUSE_SECRET_KEY}"
fi

exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8100}" \
  --no-access-log >> "$LOG" 2>&1
