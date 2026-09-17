#!/usr/bin/env bash
# The small REAL end-to-end batch (b) in load-test/README.md: a handful of
# full, sequential requests against the live agent with AGENT_STUB_LLM_DRAFTING
# left at its default (false/unset) — a real Anthropic call on every request.
# NOT concurrent, and NOT a substitute for k6-infra.js: this is the honest
# real-world latency/cost number, deliberately small because every request
# here is billed. Run this yourself; it spends your Anthropic API key's
# quota (already configured on the droplet, ARCHITECTURE.md §1).
#
# Required env: AGENT_TOKEN (a fresh clinician bearer token — see get_token.sh)
# Optional env: AGENT_BASE_URL (default https://64.177.42.210:8443)
#               PATIENT_ID     (default the seeded demo patient)
#               N              (default 5 — keep this small; each call is billed)
set -euo pipefail

: "${AGENT_TOKEN:?set AGENT_TOKEN — see get_token.sh}"
BASE_URL="${AGENT_BASE_URL:-https://64.177.42.210:8443}"
PATIENT_ID="${PATIENT_ID:-a2c0e096-23d5-4cf9-ad5b-4895597363a7}"
N="${N:-5}"

echo "Real end-to-end batch: $N sequential requests against $BASE_URL, real LLM calls, real cost." >&2
echo "session_id,http_status,total_seconds,retrieve_seconds,draft_seconds,verify_seconds,render_seconds,claims,verified,unverified"

for i in $(seq 1 "$N"); do
  SESSION_ID="real-batch-$(date +%s)-$i"
  RESPONSE=$(curl -sk -w '\n%{http_code}' "$BASE_URL/pre-visit-brief" \
    -H "Authorization: Bearer $AGENT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"patient_id\": \"$PATIENT_ID\", \"session_id\": \"$SESSION_ID\"}")
  STATUS=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')
  echo "$BODY" | python3 -c "
import json, sys
status = sys.argv[1]
session_id = sys.argv[2]
try:
    d = json.loads(sys.stdin.read())
    t = d.get('timing', {})
    v = d.get('verification', {})
    print(f\"{session_id},{status},{t.get('total_seconds','')},{t.get('retrieve_seconds','')},{t.get('draft_seconds','')},{t.get('verify_seconds','')},{t.get('render_seconds','')},{v.get('claims','')},{v.get('verified','')},{v.get('unverified','')}\")
except json.JSONDecodeError:
    print(f\"{session_id},{status},,,,,,,,\", file=sys.stdout)
    print(sys.argv[3][:500], file=sys.stderr)
" "$STATUS" "$SESSION_ID" "$BODY"
done

echo "" >&2
echo "For token cost: pull the agent's stdout log and run scripts/compute_metrics.py Metric 6" >&2
echo "  (agent/docs/deployment.md 'Metrics' section) — e.g.:" >&2
echo "  ssh root@64.177.42.210 'docker logs clinical-copilot-agent-agent-1 --since 10m 2>&1' | python scripts/compute_metrics.py --log-file -" >&2
