# Load and baseline performance testing — Clinical Co-Pilot agent

Two separate tests, answering two different questions. Never blend their
numbers.

| | (a) Infra load test | (b) Real end-to-end batch |
|---|---|---|
| Script | `k6-infra.js` | `real_batch.sh` |
| LLM call | Stubbed (`AGENT_STUB_LLM_DRAFTING=true`) | Real (default) |
| Concurrency | 10, then 50 | 1 (sequential) |
| Volume | Sustained (30–60s per level) | A handful (default 5) |
| Cost | $0 — no Anthropic calls | Real, billed |
| Answers | "How does *this agent* — connection handling, FHIR round-trips, queue depth — behave under concurrency?" | "What does a physician actually wait, end to end, for one real brief, and what does it cost?" |

## Why they're separated

Running 50 concurrent *real* requests means 50 real Anthropic API calls in
one wave: expensive, and the resulting p50/p95/p99 would be dominated by
Anthropic's own latency and rate limits, not by anything this agent's code
or the droplet's FHIR round-trips are doing. That confound is the whole
reason (a) exists — `AGENT_STUB_LLM_DRAFTING=true`
(`app/reasoning/draft_claims.py`) skips the one Anthropic call in the
Pre-Visit Brief pipeline and replaces it with a claim built directly from
the already-retrieved FHIR records (same shape as a correct model
response, so it always verifies) — while every other step (Patient lookup,
Condition/MedicationRequest/AllergyIntolerance retrieval over the real
FHIR API, verification, rendering, structured logging) runs exactly as in
production. (a) tells you where *this system's* tail latency comes from at
concurrency; (b) tells you the honest, real-world number a physician would
actually see, including LLM latency and dollar cost — deliberately small
and sequential so it doesn't also have to answer the concurrency question.

The flag is off by default and must never be true on a deployment serving
real briefs — a stubbed claim is a verbatim copy of the source record, not
a model's output. It does not touch F-54's port bindings or the
authorization model (§2): every stubbed request still needs the same real
clinician bearer token and does the same real FHIR reads.

## Running the generator away from the droplet under test

Both scripts are meant to run from somewhere that is *not* the droplet
being measured, so the generator's own CPU never competes with the system
it's measuring. Any of:

- **This laptop / your own machine**, over the internet — what these
  results were produced with. Limitation: shares your network path's
  variability (and, in this run, whatever else was on the operator's
  network at the time) with the actual measurement; a dedicated cloud VM
  (Railway, or any small VM instance) removes that variable if you want a
  cleaner run later.
- **Railway** (or any small cloud VM): `railway run k6 run k6-infra.js` (or
  just start a shell in a Railway service and run k6 directly). No special
  wiring needed beyond network egress to `AGENT_BASE_URL`; nothing in these
  scripts assumes it's running on the droplet.

## (a) Infra load test

### One-time / per-run setup (operator or whoever has the credentials)

```bash
export FHIR_CLIENT_ID=...       # OAuth client (findings.md F-02 provisioning)
export FHIR_CLIENT_SECRET=...
# FHIR_TEST_USERNAME / FHIR_TEST_PASSWORD default to physician/physician
AGENT_TOKEN=$(./get_token.sh)   # mints a token; expires in 1h — re-run if a run outlasts it
```

### Enabling the stub flag on the droplet (operator step — reversible)

The flag is read once at process start (`os.environ.get(...)` at import
time in `app/reasoning/draft_claims.py`), so it needs the container
restarted with it set, and restarted again to turn it back off. No image
rebuild is needed if the code deployed already contains the
`AGENT_STUB_LLM_DRAFTING` support (check with
`docker exec clinical-copilot-agent-agent-1 python -c "from app.reasoning import draft_claims; print(draft_claims._STUB_LLM_DRAFTING)"`
— if this errors with an ImportError-shaped problem, the deployed image
predates this change and needs `deployment.md §4`'s deploy steps first).

```bash
# On the droplet:
cd /opt/clinical-copilot/agent/deploy
AGENT_STUB_LLM_DRAFTING=true docker compose --env-file /etc/clinical-copilot/agent.env \
  -f docker-compose.agent.yml up -d --no-deps --wait agent
# confirm:
docker exec clinical-copilot-agent-agent-1 python -c \
  "from app.reasoning import draft_claims; print(draft_claims._STUB_LLM_DRAFTING)"
# → True
```

**Revert immediately after the test finishes** (real briefs must never be
served with stubbed claims):

```bash
cd /opt/clinical-copilot/agent/deploy
docker compose --env-file /etc/clinical-copilot/agent.env \
  -f docker-compose.agent.yml up -d --no-deps --wait agent
# (no AGENT_STUB_LLM_DRAFTING in the shell env now, so it falls back to
# the compose file's default: false)
docker exec clinical-copilot-agent-agent-1 python -c \
  "from app.reasoning import draft_claims; print(draft_claims._STUB_LLM_DRAFTING)"
# → False
```

### Running the generator (from off-droplet)

```bash
brew install k6   # or see https://k6.io/docs/get-started/installation/

AGENT_BASE_URL=https://201.79.25.22:8443 \
AGENT_TOKEN="$AGENT_TOKEN" \
PATIENT_ID=a2c0e096-23d5-4cf9-ad5b-4895597363a7 \
VUS=10 DURATION=30s \
  k6 run --summary-export=results-10vu.json k6-infra.js

AGENT_TOKEN="$(./get_token.sh)"   # fresh token before the second run
AGENT_BASE_URL=https://201.79.25.22:8443 \
AGENT_TOKEN="$AGENT_TOKEN" \
PATIENT_ID=a2c0e096-23d5-4cf9-ad5b-4895597363a7 \
VUS=50 DURATION=30s \
  k6 run --summary-export=results-50vu.json k6-infra.js
```

`results-*.json` are gitignored (raw run output, not committed); see
`RESULTS.md` in this directory for the numbers from the run that produced
this document, plus the CPU/memory baseline captured with `docker stats`
during each level.

### Baseline CPU / memory / throughput

Sampled with `docker stats --no-stream` on the droplet (DO's own
monitoring dashboard is an equally valid source — this repo has no droplet
API token, so the local `docker stats` view is what was actually used):

```bash
# On the droplet, or via: ssh root@201.79.25.22 '...'
watch -n5 'docker stats --no-stream clinical-copilot-agent-agent-1 development-easy-openemr-1 development-easy-mysql-1'
```

k6's own summary (`http_reqs` rate in `results-*.json`) is the throughput
number — requests/second actually sustained at each VU level, which is not
the same as VUs (a slow backend can turn 50 VUs into well under 50 req/s).

## (b) Real end-to-end batch

```bash
AGENT_TOKEN="$(./get_token.sh)"
N=5 AGENT_TOKEN="$AGENT_TOKEN" ./real_batch.sh
```

Prints per-request timing (from the response body's `timing` field — no
server log access needed) as CSV to stdout. For the dollar cost, pull the
log and run the existing `scripts/compute_metrics.py` (Metric 6) — see
`real_batch.sh`'s own trailer for the exact command; this script doesn't
duplicate that logic.

## Files

- `k6-infra.js` — the (a) load generator.
- `get_token.sh` — mints a clinician OAuth token (password grant, same as
  `scripts/smoke_test_fhir.py`).
- `real_batch.sh` — the (b) small real batch.
- `RESULTS.md` — the numbers from the run that shipped with this change,
  and exactly what was run vs. operator-pending.
