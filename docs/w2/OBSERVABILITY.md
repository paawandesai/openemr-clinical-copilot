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

The background extraction now inherits the uploading request's id
(`ExtractionJobs.submit`), so it stays on the encounter's trace.

**Verified end to end** (2026-09-22, local, real LLM): the API collection
ran one encounter under `afb764fa-…`. 105 stdout events carried that id —
upload, 5 status reads, 12 tool calls, 15 step events, 2
`llm_usage_event`s, 44 `claim_verified_event`s, routing, latency. Langfuse
shows them as **one trace**, `c791ae067383e767d629d5e55d35f77d`, with Input
`null` and Output `undefined` on every observation:
`evidence/screenshots/langfuse-encounter-trace.png`.

### Where the chain still breaks

| # | Break | State |
|---|---|---|
| 1 | The chart card (OpenEMR module, `AgentClient.php`) never sends `X-Correlation-ID`, so from the UI each request is its own trace. It reads the agent's id back, but does not send one. | **Open.** Fix: mint one id per chart open and send it on every call. PHP, not changed here. |
| 2 | `POST /documents` on `main` is still the stub. The extractor is merged, but the route does not call `ExtractionJobs`, so no extraction runs at upload. The chain is upload → *(nothing)* → status. | **Open.** Wiring the route is the extractor's follow-up. Once it is wired, break 3's fix keeps extraction on the upload's trace. |
| 3 | `ExtractionJobs.submit` minted a fresh UUID for the background extraction, cutting it off the upload's trace. | **Fixed** (`worker.py`). |
| 4 | The extractor's nodes emitted only their own `intake_extraction_step`, not `step_event`. | **Fixed** (`intake_extractor.py` names its pipeline). |
| 5 | No supervisor and no Week 2 ask route. `run_evidence_retriever` is called by nothing that serves HTTP, so "routing → guideline retrieval" is not on any live path. Conversation routing (Week 1) is. | **Open** (supervisor not built). The retrieval events carry the request's id when called in one. |
| 6 | Write-back: no agent route posts it. The collection calls the module endpoint directly, so the OpenEMR side has no agent event, and the PHP endpoint receives no correlation id. `write_lab_results()` itself emits `writeback` step and tool events under the caller's id. | **Open.** |
| 7 | Week 1 failure lines (`pre_visit_brief_failed`, `conversation_turn_failed`) are plain structlog lines, not `log_event`. They carry the id on stdout but are not exported. The failure itself **is** exported, as an `error` step, tool call and 5xx request. | Left as is. |

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
| `screenshots/langfuse-dashboard.png` | The Langfuse dashboard over the demo mix below |
| `screenshots/langfuse-encounter-trace.png` | One encounter = one trace (§2) |
| `screenshots/dashboard-export.png`, `dashboard-demo.html` / `.json` / `.txt` | The stdout dashboard over the same mix, with alert states |
| `api-collection-run.txt` | Final newman run: 21 requests, 48 assertions, 0 failed |
| `ready-failure-modes.txt` | `/ready` with each dependency broken (§5) |
| `load/` | Raw k6 summaries, resource samples, bench JSON ([`LOAD_TEST.md`](LOAD_TEST.md)) |

**The demo mix is a demonstration, not a baseline.** Local agent, stubbed
LLM for the brief, Langfuse on:

- one collection run (a real conversation turn),
- 20 s of briefs at 10 VUs, then 20 s at 50 VUs (the second produces the
  errors),
- the extraction bench with vision replayed,
- the retrieval bench.

Its latencies include Langfuse competing with OpenEMR for the Docker VM, and
are not the numbers in `LOAD_TEST.md`. Two alerts fire on it, correctly: the
50-VU burst drove the 5xx rate to 23% and FHIR dependency failures to 8.3%.

Two caveats on the Langfuse screenshot, stated rather than hidden:

- The first extraction bench ran from the extractor branch's checkout, whose
  exporter predates these scores. Its 12 `intake_extraction_completed`
  events were **re-exported** from the stdout log through this branch's
  exporter.
- After the rebase onto `main`, 12 more extractions ran on this branch's
  own code and exported directly.

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

## 5. Running it

```bash
# agent from this checkout, stdout to a log the dashboard reads
LOG=agent/logs/run.jsonl AGENT_ENV_FILE=<file with ANTHROPIC_API_KEY> \
LANGFUSE_ENV_FILE=<agent-langfuse.env> agent/load-test/w2/start_local_agent.sh &

cd agent
python scripts/w2_dashboard.py --log-file logs/run.jsonl \
  --prices ../docs/w2/evidence/prices-used.json --html logs/dashboard.html --watch 10
python scripts/provision_langfuse_dashboard.py --apply   # local Langfuse only
```
