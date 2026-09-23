# Week 2 load, stress and readiness tests (local stack)

Results and what they mean: [`docs/w2/LOAD_TEST.md`](../../../docs/w2/LOAD_TEST.md).
Readiness failure modes: [`docs/w2/OBSERVABILITY.md`](../../../docs/w2/OBSERVABILITY.md) §4.
Local stack setup: [`docs/w2/LOCAL_ENV.md`](../../../docs/w2/LOCAL_ENV.md).

| File | What it does |
|---|---|
| `start_local_agent.sh` | Starts **this checkout's** agent on the host (default port 8100) against local OpenEMR, with stdout going to `$LOG`. Flags: `STUB`, `LANGFUSE`. Overrides: `ANTHROPIC_KEY_OVERRIDE`, `ANTHROPIC_BASE_URL`, `LANGFUSE_SECRET_KEY_OVERRIDE`, `FHIR_BASE_URL_OVERRIDE`. |
| `k6-ask.js` | The ask path. Default route is a conversation turn; `ROUTE=brief` is the Pre-Visit Brief. Records server-side step timings as k6 trends. |
| `k6-upload.js` | The extraction path over HTTP: upload, then poll status until final. |
| `run_load.sh` | One level: resource sampler + k6, outputs side by side in `$OUT`. |
| `sample_resources.sh` | CSV of agent (by PID) and container CPU / memory. |
| `extraction_bench.py` | The intake-extractor in process: vision replayed at concurrency, or `--live-vision N` real calls in sequence. |
| `retrieval_bench.py` | The evidence retriever in process at concurrency, with or without rerank. |
| `ready_failure_modes.sh` | Breaks each `/ready` dependency in turn and records `/health` and `/ready`. **Pauses local containers.** |

## Before anything

- **`AGENT_STUB_LLM_DRAFTING` covers `/pre-visit-brief` only.** A
  conversation turn or a follow-up makes a **real model call** whatever the
  flag says (about $0.03 per turn on a 5-record chart). Load the conversation
  route only on purpose.
- Keep `LANGFUSE=off` for baselines. Langfuse's containers share the Docker
  VM with OpenEMR.
- Tokens: a password-grant client (`agent/api-collection/README.md`, "One-time
  setup"). Tokens last an hour; mint one per level.

## Commands

```bash
# 1. agent, LLM stubbed, export off
LOG=$PWD/agent/logs/load.jsonl STUB=true LANGFUSE=off \
AGENT_ENV_FILE=<file with ANTHROPIC_API_KEY> \
  agent/load-test/w2/start_local_agent.sh &
AGENT_PID=$(lsof -tiTCP:8100 -sTCP:LISTEN)

# 2. each level (repeat with VUS=50)
AGENT_TOKEN=<clinician token> AGENT_PID=$AGENT_PID OUT=$PWD/docs/w2/evidence/load \
ROUTE=brief NAME=brief-stub-10vu SCRIPT=k6-ask.js VUS=10 DURATION=60s \
  agent/load-test/w2/run_load.sh

AGENT_TOKEN=<clinician token> AGENT_PID=$AGENT_PID OUT=$PWD/docs/w2/evidence/load \
PDF=$PWD/agent/api-collection/fixtures/lab-clean-001.pdf \
NAME=upload-stub-10vu SCRIPT=k6-upload.js VUS=10 DURATION=60s \
  agent/load-test/w2/run_load.sh

# 3. extraction and retrieval, in process (from agent/)
cd agent
python load-test/w2/extraction_bench.py --levels 1 10 50 --per-worker 2 \
  --events logs/extraction.jsonl > ../docs/w2/evidence/load/extraction-bench.json
ANTHROPIC_API_KEY=... python load-test/w2/extraction_bench.py --live-vision 3 \
  --samples lab-clean-001 intake-clean-004 lab-scanned-heavy-abnormal-002 \
  --events logs/extraction.jsonl > ../docs/w2/evidence/load/extraction-live-vision.json  # ~$0.35
python load-test/w2/retrieval_bench.py --levels 1 10 50 --per-worker 4 \
  --events logs/retrieval.jsonl > ../docs/w2/evidence/load/retrieval-bench-rerank.json
python load-test/w2/retrieval_bench.py --levels 1 10 50 --per-worker 4 --no-rerank \
  --events logs/retrieval.jsonl > ../docs/w2/evidence/load/retrieval-bench-no-rerank.json
cd ..

# 4. readiness failure modes (agent on 8100 with LANGFUSE=on)
START_AGENT=$PWD/agent/load-test/w2/start_local_agent.sh \
AGENT_ENV_FILE=... LANGFUSE_ENV_FILE=... \
INVALID_ANTHROPIC_KEY=<any string Anthropic rejects> \
INVALID_LANGFUSE_SECRET=<any string Langfuse rejects> \
  agent/load-test/w2/ready_failure_modes.sh | tee docs/w2/evidence/ready-failure-modes.txt

# 5. the dashboard over whatever ran
cd agent && python scripts/w2_dashboard.py --log-file logs/load.jsonl \
  --prices ../docs/w2/evidence/prices-used.json
```

The 50-user rerank level holds the CPU for about six minutes (see
LOAD_TEST.md §5). That is the finding, not a hang.
