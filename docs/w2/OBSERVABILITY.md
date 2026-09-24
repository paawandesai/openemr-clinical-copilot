# Week 2 observability: events, dashboard, alerts

Work order 14. Week 1's foundation is unchanged: one JSON line per event on
stdout (`app/middleware/logging.py`), the same event exported to self-hosted
Langfuse when `LANGFUSE_*` is set (`app/middleware/langfuse_export.py`,
`agent/docs/observability.md`), and LangSmith refused at every graph node
(`UntracedGraph` / `_untraced`, `app/graph/pre_visit_brief.py`). Week 2 adds
operational events for the steps Week 1 never had, a dashboard over them,
and alerts.

| Deliverable | Where |
|---|---|
| Event models | `agent/app/models/telemetry_events.py` |
| Emitters | `agent/app/middleware/telemetry.py`, `CorrelationIdMiddleware` |
| Langfuse scores | `agent/app/middleware/langfuse_export.py` (`_OPERATIONAL_SCORERS`) |
| Dashboard + alert evaluator (stdout) | `agent/scripts/w2_dashboard.py` |
| Langfuse dashboard (local) | `agent/scripts/provision_langfuse_dashboard.py` |
| Alerts: meaning, response, thresholds | [`ALERTS.md`](ALERTS.md) |
| Load and stress tests | [`LOAD_TEST.md`](LOAD_TEST.md) |
| API collection | `agent/api-collection/` (README, "Week 2 core flow") |
| Evidence | `docs/w2/evidence/` |

---

## 1. One event shape

Three new event types. Each goes through `log_event`, so it inherits the
correlation id, the stdout line and the Langfuse export. Every field is an
enum, a number, a boolean, a route template, an exception class name or an
id. **No field can hold text**: `counts` is typed `int | float | bool`, so a
value, a name or a question fails validation rather than reaching a log
(`tests/test_w2_telemetry.py::test_counts_cannot_carry_text`).

| Event | Emitted by | Fields |
|---|---|---|
| `step_event` | `telemetry.step()` / `record_step()` | `pipeline`, `step`, `outcome` (`ok`/`error`/`refused`/`pending`), `latency_ms`, `error_type` (class name only), `counts` |
| `tool_call_event` | `telemetry.tool_call()` | `tool`, `target` (resource type / model id), `outcome`, `latency_ms`, `attempts`, `retries`, `status_code`, `error_type` |
| `http_request_event` | `CorrelationIdMiddleware` | `method`, `route` (the **template**, never the raw path), `status_code`, `latency_ms` |

Where each is emitted:

| Pipeline . step | Source |
|---|---|
| `pre_visit_brief.{retrieve,draft,verify,render}` | `_timed(..., pipeline)` wrapper, ok **and** error |
| `conversation.routing` | around `route()`; `refused` when the rules refuse |
| `conversation.{retrieve,draft,verify,render}`, `follow_up.*` | the graphs' own `step_seconds` |
| `documents.{upload,extraction_status}` | `app/api/documents.py` (`pending` until complete) |
| `intake_extractor.{read_document,vision,locate,verify}` | `_timed(..., Pipeline.INTAKE_EXTRACTOR)` in `app/graph/intake_extractor.py` |
| `evidence_retriever.retrieval` | `app/retrieval/worker.py`, with hits, reranked, per-stage ms |
| `writeback.writeback` | `app/writeback/client.py`, with created / duplicate counts |

| Tool | Source | Retries |
|---|---|---|
| `fhir_read` | `fhir/client.py` `_get_bundle`, `ensure_patient_exists` | client never retries → 1 attempt |
| `llm` | `draft_claims.py`, `draft_follow_up.py` (and the conversation turn through it) | **real**: the Anthropic SDK retries silently (2× by default); `anthropic_http_client()` reads the `x-stainless-retry-count` header the SDK stamps on every attempt |
| `guideline_retriever` | retrieval worker | in-process |
| `lab_writeback` | write-back client | 1 per call; a caller's retry is a second call under the same idempotency key |
| `vision` | reserved; the extractor's vision call reports through its own `llm_usage_event` | — |

A **dependency failure** is a tool call with no response, a 5xx or a 429.
A 4xx is the caller's business (unknown patient, rejected token, a write the
ledger refused) and is counted as `other_errors`, not as the dependency
failing (`langfuse_export.is_dependency_failure`; the dashboard script
applies the same rule, and a test pins that they agree).

## 2. One trace per encounter

A Langfuse trace is one correlation id (`create_trace_id(seed=correlation_id)`),
and before this work the correlation id was per request. It still is by
default. Now **a caller that sends the same `X-Correlation-ID` on every
request of an encounter gets one id on every event of that encounter**:
upload, status polls, brief, conversation turns and write-back.

That makes the header a trace key written into every line, so it is held
to an identifier's alphabet: `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`.
Anything else is replaced with a fresh UUID rather than logged
(`accepted_correlation_id`). A header carrying a name never reaches a log
line (`test_a_name_in_the_header_never_reaches_a_log_line`). Decision: D36.

**Encounter ids are UUID4s.** The middleware accepts any identifier-shaped
id, but two contracts downstream require a UUID: the extractor's
`IntakeExtractorOutput.correlation_id` and the write-back request's
`correlation_id`. So the background extraction inherits the uploading
request's id **when it is a UUID**. Otherwise it gets a fresh UUID, and
`w2_extraction_correlation_replaced` is logged under the request's id, so
the two traces still join. An upload never fails over the header's shape
(`test_a_non_uuid_encounter_id_still_uploads_and_links_the_two_traces`).

**Verified end to end against the real routes** (2026-09-22, local, real
LLM, after `feat/w2-graph` merged). The API collection ran one encounter
under `07c0fd35-…`. That one id carries all of these:

- the upload;
- 26 status polls;
- the **background extraction**: its 4 `intake_extractor` steps and its
  completion summary;
- the brief and a conversation turn;
- 3 supervisor routing decisions: `answer/extraction_available`,
  `retrieve/guideline_question`, `answer/evidence_in_hand`;
- 6 `supervisor` node steps across the two `/w2/ask` turns;
- the guideline retrieval;
- 55 tool calls and 5 `llm_usage_event`s.

No extraction summary landed on a different id. In Langfuse it is one trace:
2 min 52 s end to end, 87,245 tokens, $0.82. The record of it is the
committed screenshot,
[`evidence/screenshots/langfuse-encounter-trace.png`](evidence/screenshots/langfuse-encounter-trace.png).
The Langfuse instance was ephemeral: a local development stack on one
developer's machine (`Env: local-dev` in the screenshot, from
`agent/observability/docker-compose.langfuse.yml`). It is not hosted, a
reviewer cannot reach it, and nothing preserves its data, so a trace id from
it proves nothing to anyone else. The screenshot is the evidence. An earlier run, `c8661931-…`, gave the same
shape.

The same screenshot shows `resourceAttributes.service.name:
"unknown_service"`: the exporter set no OpenTelemetry resource. Exported
spans now name the service, `clinical-copilot-agent` (D55).

### Where the chain still breaks

| # | Break | State |
|---|---|---|
| 1 | The chart card (OpenEMR module, `AgentClient.php`) never sends `X-Correlation-ID`, so from the UI each request is its own trace. It reads the agent's id back, but does not send one. | **Open, PHP.** The header it must send: `X-Correlation-ID: <UUID4>`, one per chart open, reused on upload, status, ask and write-back. |
| 2 | `POST /documents` was a stub. | **Closed** by `feat/w2-graph`. The real route runs the extractor. |
| 3 | `ExtractionJobs.submit` minted its own id, cutting extraction off the upload's trace. | **Fixed here**: inherits a UUID id, or links a non-UUID one. |
| 4 | The extractor's nodes did not emit `step_event`. | **Fixed here** (`Pipeline.INTAKE_EXTRACTOR`). |
| 5 | No supervisor, no Week 2 ask route. | **Closed** by `feat/w2-graph`. Its nodes now emit `step_event`s (`Pipeline.SUPERVISOR`), and its FHIR, LLM and retriever calls are tool calls. |
| 6 | Write-back: nothing in the agent calls `write_lab_results()`. The client is instrumented (a `writeback` step and a `lab_writeback` tool call under the caller's id), but the only caller is the OpenEMR module (or the collection) posting to the PHP endpoint directly, which receives no correlation id. | **Open.** It closes when an agent route posts the write-back, or when the module forwards the header of break 1. |
| 7 | Week 1 failure lines (`pre_visit_brief_failed`, `conversation_turn_failed`) are plain structlog lines, not exported. The failure itself **is** exported, as an `error` step, a tool call and a 5xx request. | Left as is. |

## 3. Dashboard

Two views of the same events. The stdout one is the record; Langfuse is the
interactive view. That is the same split Week 1 made
(`agent/docs/observability.md` §1: no BAA covers a hosted Langfuse, so
production metrics come from stdout).

**`scripts/w2_dashboard.py`** computes every metric the work order lists
from the stdout log. It can run once, `--watch N` (rewrites a self-refreshing
HTML page every N seconds, i.e. real time), `--json`, `--alerts-only` (cron).

| Metric | Computed from |
|---|---|
| request count, error count (5xx), error rate, throughput | `http_request_event`, probes (`/health`, `/ready`) excluded |
| p50 / p95 / p99 overall, per route, answer routes | `http_request_event.latency_ms` |
| p50 / p95 **by step**, errors and outcomes by step | `step_event` |
| tool-call counts, dependency failures, retries, calls with retries | `tool_call_event` |
| verification pass / fail rate, disclosure rate | `claim_verified_event`, `claim_disclosure_event` |
| extraction located rate (pooled, text-layer only, worst document), mean confidence | `intake_extraction_completed` |
| retrieval hits, zero-hit count, reranked-true rate | `evidence_retrieval_event` |
| tokens and cost per encounter | `llm_usage_event` grouped by correlation id, priced with `--prices` |

Located rate is `located / (located + unlocated)`: of the values the model
read, the share the deterministic locate step found on the page (D3).
`missing` and `illegible` describe the document, not the extractor, so they
are left out of both sides.

**Langfuse**: `scripts/provision_langfuse_dashboard.py --apply` writes a
16-widget dashboard, "Clinical Co-pilot: Week 2 operations", into the
**local** Langfuse. Langfuse 4.37 has no public dashboard API, so it inserts
the rows the UI would write. The shapes were copied from Langfuse's own
seeded widgets, and every query was first run through the public Metrics API
v2. Scores behind it, per event:

| Score | From |
|---|---|
| `request_latency_ms`, `request_server_error` | `http_request_event` (5xx → level ERROR) |
| `step_latency_ms:<pipeline>.<step>` | `step_event` (a breakdown by score name is the by-step view; error → level ERROR) |
| `tool_call_failed`, `tool_call_retries` | `tool_call_event` |
| `retrieval_hit_count`, `retrieval_reranked` | `evidence_retrieval_event` |
| `extraction_located_rate`, `extraction_mean_confidence` | `intake_extraction_completed` |
| `verification_passed`, cost (GENERATION) | Week 1, unchanged |

### Evidence (`docs/w2/evidence/`)

| File | What it is |
|---|---|
| `screenshots/langfuse-dashboard.png` | The Langfuse dashboard on a **fresh** instance after following §6 exactly: generated traffic only (no local state) |
| `screenshots/langfuse-encounter-trace.png` | One real encounter against the real routes = one trace (§2) |
| `screenshots/dashboard-export.png`, `dashboard-demo.html` / `.json` / `.txt` | The stdout dashboard over an earlier load demo, with alert states. Two alerts fire on it, correctly: a 50-VU burst drove 5xx to 23% and FHIR dependency failures to 8.3%. Its latencies include Langfuse competing with OpenEMR for the Docker VM, and are not baselines. |
| `api-collection-run.txt` | Final newman run against the real routes: 44 requests (status polls included), 102 assertions, 0 failed |
| `ready-failure-modes.txt` | `/ready` with each dependency broken (§4) |
| `load/` | Raw k6 summaries, resource samples, bench JSON ([`LOAD_TEST.md`](LOAD_TEST.md)) |

**Neither event log behind these is committed.** Those local logs were
written before D56 and carry `patient_session_key` raw, which holds
(synthetic) patient UUIDs. Since D56 the key is emitted as its HMAC. But
uvicorn's access lines still print `?patient_id=` for the document routes,
and a log with patient identifiers is the wrong thing to put in a
repository even when the patients are fake. What is committed is aggregates: the dashboard exports
contain no session key or patient id (`test_rendered_output_never_carries_the_session_key`).
To see the widgets populated, generate traffic (§6 step 5).

## 4. Readiness

`/health` stays liveness-only. `/ready` checks OpenEMR (`GET /fhir/metadata`),
the LLM provider (key and model through `models.retrieve`, plus the
agent's own recent-call failures) and the observability backend (a
background Langfuse auth check, never older than 60 s).

- **OpenEMR and the LLM provider are required**: 503 when either is down.
- **The observability backend is reported, not required** (D37). A down
  Langfuse shows `unhealthy` in the body, but `/ready` stays 200.

Every failure mode was verified by breaking the dependency (local stack,
`agent/load-test/w2/ready_failure_modes.sh`,
`evidence/ready-failure-modes.txt`):

| Broken | `/health` | `/ready` | detail |
|---|---|---|---|
| OpenEMR container paused | 200 | **503** | `OpenEMR FHIR API unreachable`; back to 200 within 17 s of unpause |
| Anthropic key invalid | 200 | **503** | `LLM provider rejected the configured API key` |
| Anthropic API unreachable | 200 | **503** | `LLM provider unreachable` |
| Langfuse web paused | 200 | 200 | `observability_backend: unhealthy, Langfuse unreachable`; healthy again on unpause |
| Langfuse keys wrong | 200 | 200 | `observability_backend: unhealthy, Langfuse rejected the configured API keys` |

## 5. The stdout dashboard

```bash
# agent from this checkout, stdout to a log the dashboard reads
LOG=$PWD/agent/logs/run.jsonl AGENT_ENV_FILE=<file with ANTHROPIC_API_KEY> \
LANGFUSE_ENV_FILE=$PWD/agent/observability/agent-langfuse.env \
  agent/load-test/w2/start_local_agent.sh &

cd agent
.venv/bin/python scripts/w2_dashboard.py --log-file logs/run.jsonl \
  --prices ../docs/w2/evidence/prices-used.json --html logs/dashboard.html --watch 10
# open agent/logs/dashboard.html; it refreshes itself every 10 s
```

## 6. Runbook: a populated Langfuse dashboard, starting from nothing

For a reader with a clone of this repository and nothing else. Every path is
relative to the repository root.

- **Checked 2026-09-22** by following these steps on an empty instance:
  - generated `.env`, empty volumes;
  - provisioned, generated traffic;
  - screenshot `evidence/screenshots/langfuse-dashboard.png`, all 16
    widgets populated.
- **The only differences** from what is written here: the stack ran under
  another compose project name (`-p w2-fresh`), so it could not touch an
  existing stack's volumes. `--container w2-fresh-postgres-1` followed from
  that.

**Needs:**

- Docker with the compose plugin (Langfuse wants about 4 cores and 16 GiB);
- Python 3.12;
- `openssl`.

Screenshots only (step 7) also need Node 18+ and Chrome. No OpenEMR, no
Anthropic key.

1. **Python environment**, once:

   ```bash
   cd agent && python3.12 -m venv .venv \
     && .venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt && cd ..
   ```

2. **Secrets for the Langfuse stack**, once:

   ```bash
   agent/observability/generate-env.sh
   ```

   This writes two gitignored, mode-0600 files next to the script:
   - **`.env`**: the stack's own database and queue passwords, and the UI
     login.
   - **`agent-langfuse.env`**: only the four variables a client needs.

   **The `.env` caveat:**
   - The script **refuses to overwrite** an existing `.env`.
   - Postgres, ClickHouse and MinIO bake their passwords into their volumes
     on first boot. So a regenerated `.env` against old volumes gives a
     stack that cannot start.
   - To start over, wipe both together:
     `docker compose -f agent/observability/docker-compose.langfuse.yml down -v`,
     then `agent/observability/generate-env.sh --force`.
   - Never `--force` against volumes you want to keep.

3. **Start Langfuse**. The first pull takes 2–3 minutes. `--wait` returns
   when all six services are healthy:

   ```bash
   docker compose -f agent/observability/docker-compose.langfuse.yml up -d --wait
   curl -s http://localhost:3000/api/public/health    # {"status":"OK","version":"4.37.0"}
   ```

   It listens on `127.0.0.1:3000` only. The project id
   (`clinical-copilot-agent`) and the admin user are created headlessly
   from `.env`; sign-up is disabled.

4. **Provision the dashboard**: 16 widgets, written into Langfuse's own
   Postgres, because 4.37 has no dashboard API. Local instances only.

   ```bash
   agent/.venv/bin/python agent/scripts/provision_langfuse_dashboard.py --apply
   # dashboard 'w2-operations-dashboard': 16 widgets in project 'clinical-copilot-agent'
   ```

5. **Generate traffic**. About 20 s; the first run downloads the embedding
   and reranker models (about 150 MB) into the local model cache.

   ```bash
   cd agent
   set -a; . observability/agent-langfuse.env; set +a
   .venv/bin/python scripts/generate_langfuse_traffic.py
   # synthesized 40 encounters / extracted 6 committed samples (vision replayed)
   # retrieved for 30 dev queries / done; Langfuse accepted the keys.
   cd ..
   ```

   What it sends is spelled out in the script's docstring:
   - **Real code:** the extractor on the six committed samples, and the
     retriever on the 30 dev queries.
   - **Synthesized:** HTTP requests, steps (supervisor turns included), FHIR
     and LLM tool calls, verification and token usage, validated through the
     same event models, with a fixed share of FHIR 500s so the error
     widgets have data.
   - Session keys are `synthetic-user:synthetic-patient-NNN:…`. Nothing is
     written to disk.

6. **Open the dashboard** at
   <http://localhost:3000/project/clinical-copilot-agent/dashboards/w2-operations-dashboard>.
   Sign in with the two values from step 2's `.env`:

   ```bash
   grep -E '^LANGFUSE_INIT_USER_(EMAIL|PASSWORD)=' agent/observability/.env
   # email is admin@clinical-copilot.test; the password is random per .env
   ```

   The default range, "Past 1 day", covers the traffic just sent. Every
   widget should have data. Several read small or round values, and are
   correct to:
   - **Reranked-true rate: 1.** The generator's retrievals run one at a
     time, so every one is reranked. Under concurrent load it drops (see
     LOAD_TEST.md §5b).
   - **Server errors, tool failures, retries.** These are on a fixed
     schedule (every twelfth answer meets a FHIR 500; every twentieth model
     call retries once), so a handful each.
   - **Extraction located rate and confidence: about 0.8**, displayed to
     one decimal. That is the mean over six samples, one of them a heavy
     scan at about 34%.

   One encounter's trace is under *Tracing*: filter Name =
   `http_request_event` and open any row.

7. **Screenshots**, optional:

   ```bash
   cd agent/observability/screenshots && npm install
   node shoot.mjs --env-file ../.env --out ../../../docs/w2/evidence/screenshots
   cd ../../..
   ```

   `shoot.mjs` reads the login from `.env`, never from the command line. It
   sizes the page to the dashboard's inner scroll area, so the capture
   includes every widget. `CHROME_PATH` overrides the default macOS Chrome
   location; `--trace <id>` also captures one trace.

8. **Real traffic instead of, or besides, synthetic**:
   - start this checkout's agent with export on (§5 above,
     `LANGFUSE_ENV_FILE=agent/observability/agent-langfuse.env`), then run
     the API collection (`agent/api-collection/README.md`). That needs the
     local OpenEMR stack (`docs/w2/LOCAL_ENV.md`) and an Anthropic key.
   - Each run is one encounter, one trace.

9. **Stop or remove**:
   - `docker compose -f agent/observability/docker-compose.langfuse.yml stop`
     keeps the data;
   - `down -v` deletes it; then see step 2's caveat.
