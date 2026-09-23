# Observability — self-hosted Langfuse

**Status (2026-09-16): verified working locally only. Not deployed
anywhere.** It has never run against the deployed agent on either host. The
deployed compose file (`agent/deploy/docker-compose.agent.yml`) passes no
`LANGFUSE_*` variables to the agent container, so export is off in
production. Production requires a second host, planned as a second
DigitalOcean droplet (see the provider note), that has **not been
provisioned**. The step-by-step checklist for that is in
[§9](#9-production-deployment-gap--provisioning-checklist).

**Provider note.** This plan and §9 were written while the application host
was a DigitalOcean droplet, with F-41's BAA analysis behind it. The
deployment has since moved to Vultr, and no BAA covers the Vultr host
(`docs/audit/findings.md` F-61). §9's `doctl` and same-VPC steps therefore
do not apply to the current deployment as written. A production deployment
has to redo §9 on a BAA-covered substrate, with the same decisions:
self-hosted, a separate host, private transit.

This closes the observability-backend half of `docs/ARCHITECTURE.md` §6's
open risk ("Agent compute / logging backend placement") at the design level.
It does not close it in production until §9 is done. Langfuse is not the
metrics path for this submission. `scripts/compute_metrics.py` computes
`docs/KEY_METRICS.md`'s metrics from the agent's stdout log alone (§6,
"Without Langfuse"; `docs/ARCHITECTURE.md` §1). That is where production
metrics come from.

---

## 1. Decision

**Self-hosted Langfuse** (OSS, server 4.37.0), on its **own host**
separate from the OpenEMR + agent host.

- **Self-hosted, never Langfuse Cloud.** Every event carries
  `patient_session_key` (a `(user, patient, session)` triple — ARCHITECTURE.md
  §4) and FHIR `source_id`s. That is PHI-adjacent data. It must stay on
  infrastructure under this project's own BAA analysis (AUDIT.md F-41),
  which is the same reasoning that kept MySQL off DigitalOcean Managed
  Databases.
- **Separate droplet** (the project's original two-droplet plan):
  - Langfuse's own Docker Compose guidance is "at least 4 cores and 16 GiB
    of memory" and "e.g. 100GiB" of storage. The audited droplet is 4 vCPU,
    ~12 GB RAM, 48 G disk (F-44), before OpenEMR runs at all.
  - Co-locating ClickHouse, Postgres, Redis and MinIO would also contaminate
    the load-test baseline measured on droplet 1 later.
  - ARCHITECTURE.md §1 and §6 once said the observability backend would
    stay on the same droplet. They now match this plan: a separate host,
    deferred to v2, with `compute_metrics.py` over stdout as the live path.

## 2. Which compose file, and why

`agent/observability/docker-compose.langfuse.yml` is derived from Langfuse's
own `docker-compose.yml` at tag `v4.37.0`. Langfuse ships **no lighter
"run Langfuse" variant**. Checked, not assumed:

| Upstream file | What it is | Usable here? |
|---|---|---|
| `docker-compose.yml` | The documented self-hosting path (Langfuse docs: `git clone` → `docker compose up`) | **Yes — basis for ours** |
| `docker-compose.dev.yml` | Backing stores only (ClickHouse, MinIO, Redis, Postgres, an AWS emulator) for people developing Langfuse itself. No web or worker. | No |
| `docker-compose.build.yml` | Builds web/worker from source; publishes every port on all interfaces | No |

All six services are required by Langfuse v4:

- `langfuse-web` — UI and ingestion API
- `langfuse-worker` — async processing
- `postgres`
- `clickhouse`
- `redis`
- `minio` — S3-compatible; every event is persisted there first

So "trimming" meant trimming exposure and defaults, not services. The
compose file's header lists every deviation from upstream. The ones that
matter:

- **One published port instead of eight** (see §3).
- **No weak default secrets.** Every secret is `${VAR:?}`, so the stack
  refuses to start until `generate-env.sh` writes random values to a
  gitignored `.env` (mode 0600).
- `TELEMETRY_ENABLED=false` (upstream default is `true`).
- `AUTH_DISABLE_SIGNUP=true`, with headless init creating the only user,
  org, project and API keys.
- Images pinned to `4.37.0`.
- Real healthchecks, so `up --wait` means "serving". Both Langfuse servers
  bind to the container's bridge IP, not loopback, so the check probes
  `$HOSTNAME`.

## 3. Port bindings — loopback only

| Service | Upstream `ports:` | Ours |
|---|---|---|
| langfuse-web | `3000:3000` (**all interfaces**) | `127.0.0.1:3000:3000` — the only published port; address hardcoded, not overridable from `.env` |
| minio | `9090:9000` (**all interfaces**), `127.0.0.1:9091:9001` | none — only needed for media uploads / batch exports, which this agent never uses |
| langfuse-worker | `127.0.0.1:3030:3030` | none |
| clickhouse | `127.0.0.1:8123:8123`, `127.0.0.1:9000:9000` | none |
| redis | `127.0.0.1:6379:6379` | none |
| postgres | `127.0.0.1:5432:5432` | none |

Upstream relies on "a security group or firewall" for the two
all-interfaces ports. That is exactly the pattern F-54 showed failing on
this project: Docker's NAT rules run ahead of UFW.

**Verified from actual listeners, not config** (the ARCHITECTURE.md §1
standard), 2026-09-16:

1. **Resolved config.** `docker compose ... config --format json` shows one
   `ports` entry in the whole file: `host_ip: 127.0.0.1, published: 3000`.
2. **Linux VM where Docker actually runs** (Colima). `sudo ss -tlnp` shows
   `127.0.0.1:3000` (docker-proxy) as the only Langfuse listener, and
   nothing on 3030/5432/6379/8123/9000/9090/9091.
3. **macOS host.** `lsof -iTCP:3000 -sTCP:LISTEN` shows `127.0.0.1:3000`
   only. The other six ports have no listeners.
4. **Non-loopback vantage.** `curl http://10.0.0.23:3000/...` (the laptop's
   LAN IP) got "Couldn't connect to server". `http://127.0.0.1:3000/api/public/health`
   returned `{"status":"OK","version":"4.37.0"}`.

That is local evidence only. On droplet 2 the same checks must be re-run
from a genuinely external network (§9, step 7).

## 4. How the agent exports

`app/middleware/logging.py` behaves as before and additionally calls
`app/middleware/langfuse_export.py`:

- `configure_logging()` calls `init_exporter()`.
- `log_event()` writes the stdout line (unchanged), then calls
  `export_event()`.

Both public signatures are **unchanged**. `tests/test_langfuse_export.py`
asserts them by string, because the two parallel worktrees call these
functions.

### Configuration

| Env var | Meaning |
|---|---|
| `LANGFUSE_BASE_URL` | e.g. `http://localhost:3000` |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | project key pair (`generate-env.sh` writes both into `agent-langfuse.env`) |
| `LANGFUSE_TRACING_ENVIRONMENT` | read by the SDK; `local-dev` locally |

What happens with each configuration state:

- **None set:** stdout-only, exactly the old behaviour. One
  `observability_export_disabled` line is written at startup.
- **Some set:** an error line naming the missing vars. Nothing is guessed.
- **All set:** `observability_export_enabled`, then a background
  `observability_export_auth_check ok=true|false`.

### Mapping of one `log_event` call

| Langfuse concept | Populated from |
|---|---|
| Trace | one per `correlation_id` (deterministic trace id) |
| Trace name | `use_case` (`pre_visit_brief` / `change_detection` / `follow_up_drilling`) |
| Session | `patient_session_key` — so the Sessions page *is* the `(user, patient, session)` rollup |
| Tags | `use_case:<value>`; plus `retrieved:<ResourceType>` on `retrieval_event` |
| Observation | EVENT named `event_type`, metadata = `event_data` + envelope fields; `llm_usage_event` becomes a GENERATION |
| Scores | derived per event type, each parsed through the existing `app/models/metrics_events.py` model (§6) |

### PHI boundary — structural, not just a convention

- **Input/output are never set.** Verified in the UI: every observation
  shows Input `null` / Output `undefined`.
- **Only exporter-created spans leave the process.** The client gets a
  private OpenTelemetry `TracerProvider` (never registered globally) and
  `should_export_span=is_langfuse_span`. The SDK's defaults would register
  globally and also export any GenAI/LLM auto-instrumentation spans. Those
  carry full prompts and completions (chart content), so adding such
  instrumentation elsewhere cannot silently start shipping PHI to Langfuse.
- **Payload contract.** `event_data` must stay what the metrics_events
  models allow: identifiers, enums, booleans, timestamps, scope strings,
  token counts. Never claim values, display text, or FHIR JSON.

### Failure isolation

- Export runs after the stdout line and can never raise into the request:
  failures log `observability_export_failed`.
- Delivery is batched on a background thread, so a down Langfuse costs a
  request nothing.
- `check_export_health()` answers "is export actually working" without
  blocking, and backs `/ready`'s `observability_backend` check
  (`app/health.py`). It reports a background auth check refreshed every
  15 s; a result older than 60 s is never reported as healthy. Details are
  `Langfuse unreachable` (connection failure), `Langfuse rejected the
  configured API keys` (401/403), `Langfuse reachability check failed`,
  `Langfuse export partially configured`, or `Langfuse reachability not yet
  confirmed` — no URLs, paths or exception text (F-37). With no `LANGFUSE_*`
  set, `/ready` does not call it: the check reports `healthy` with
  `backend: "stdout"`, because the JSON event log is then the production
  record (D55; before D55 this read `disabled`). With export configured it
  carries `backend: "langfuse"`.
- Exported spans carry the OpenTelemetry resource `service.name =
  clinical-copilot-agent` (`langfuse_export.service_resource()`;
  `OTEL_SERVICE_NAME` overrides it). Before, the private `TracerProvider` had
  no resource, and the SDK labelled every span `unknown_service`.
- **Verified live (2026-09-16):**
  - Langfuse up → `"observability_backend": {"status": "healthy"}`.
  - Containers stopped → the first probe served the 33 s-old cached result
    and triggered a recheck; 3 s later `{"status": "unhealthy", "detail":
    "Langfuse unreachable"}`, with no endpoint error.
  - Restarted → `healthy` again within 3 s.
  - `/health` stayed 200 throughout. `/ready` stays 503 overall while the
    FHIR and LLM provider checks are still `not_implemented`.

### Langfuse v4 behaviours found during verification

These shaped the code, and each was confirmed against server 4.37.0.

1. **v1 read APIs are disabled.** v4 runs in "events_only mode":
   `/api/public/traces` and `/api/public/v2/scores` return "not available".
   Read back through **Observations API v2** (`/api/public/v2/observations`)
   and **Metrics API v2** (`/api/public/v2/metrics`, the engine behind the
   dashboards).
2. **Scores need a true root span to group by use case.** Score queries
   resolve Trace Name and Tags only when the trace contains a true root span
   (no parent). The SDK's `trace_context=` mechanism hangs events off a
   synthetic parent, and with it every score grouped by Trace Name came back
   empty. Fix: each event is created as a parentless span, with its trace id
   pinned to the correlation_id by the private provider's id generator.
3. **Trace attributes are stored per observation row.** An event logged
   without `use_case` (e.g. `authorization_check_event` — its model has no
   such field) shows Trace Name = its own event name in the *Observations*
   view. Score views still resolve the trace's real name.
   **Recommendation to call sites:** pass `use_case=` to `log_event` for
   authorization checks whenever the request belongs to a use case.
4. **The trace detail page title uses the first observation's name** (e.g.
   "retrieval_event: 427b…"), not the trace name. Cosmetic.
5. **Session ids are validated by the SDK:** US-ASCII, ≤200 characters.
   Anything else is silently not attached, so the `patient_session_key`
   format must satisfy that.

## 5. Running it locally

```bash
# from the repository root
agent/observability/generate-env.sh      # once; refuses to overwrite
docker compose -f agent/observability/docker-compose.langfuse.yml up -d --wait   # ~2-3 min on first pull

cd agent
pip install -r requirements.txt
set -a; . observability/agent-langfuse.env; set +a
python scripts/send_langfuse_test_event.py   # exits non-zero unless the data actually arrived
python -m unittest discover -s tests          # 35 tests, no network

# UI: http://localhost:3000
#   user: admin@clinical-copilot.test
#   password: LANGFUSE_INIT_USER_PASSWORD in agent/observability/.env
```

Stop with `... stop`. `... down -v` wipes the data; rerun `generate-env.sh
--force` only after a `down -v`, because the database passwords are baked
into the volumes.

## 6. KEY_METRICS.md → Langfuse

Numbers shown are the **synthetic local data** (3 identical Pre-Visit Brief
interactions sent by `send_langfuse_test_event.py`: two from the host venv,
one from the built agent image). They prove each query returns the right
shape and value; they are not real measurements.

### How to use this section

**Dashboard recipes** refer to *Dashboards → New dashboard → Add widget* (or
*Dashboards → Widgets → New widget*). The builder fields are:

- **View:** Observations, Scores Numeric, Scores Categorical, or Scores Boolean
- **Metric** plus an aggregation: Sum / Avg / Count / Max / Min / P 50 / P 75 / P 90 / P 95 / P 99 / Uniq
- **Filters**
- **Breakdown Dimension**
- **Chart Type**

**API** calls are `GET /api/public/v2/metrics?query=<json>` with HTTP Basic
auth (public key : secret key).

**Event type names** are the snake_case of the model class
(`ClaimVerifiedEvent` → `claim_verified_event`), matching the
`retrieval_event` name already emitted by `app/fhir/client.py`. A payload
that doesn't parse against its model is still exported, at level WARNING
with "no metric derived", so a naming or shape mismatch from another track
is visible in Tracing, not silent.

### Metric 1 — Verification Pass Rate

- **Emitted as:**
  `log_event("claim_verified_event", ClaimVerifiedEvent(...).model_dump(mode="json"), use_case=..., patient_session_key=...)`
- **In Langfuse:** EVENT `claim_verified_event`, with **BOOLEAN score
  `verification_passed`** (1 = passed) attached to it.
- **Dashboard:** View **Scores Boolean** · Metric **Value / Avg** · Filter
  **Name = verification_passed** · Breakdown **Trace Name** · Horizontal Bar
  Chart. Average of a boolean is the pass rate, per use case.
- **Blind-spot check:** KEY_METRICS warns that ~100% everywhere should
  prompt checking that verification runs at all. Add a second widget with
  the same view and Metric **Count** to see claim volume per use case.
- **Per interaction:** *Sessions* → a session row's Scores column. Or
  *Tracing* → a trace, where each `claim_verified_event` row shows
  `verification_passed: True/False`.
- **Verified locally:** avg **0.5**, n = 6, Trace Name `pre_visit_brief`.

### Metric 2 — Unverified-Claim Disclosure Rate

- **In Langfuse:** BOOLEAN score **`unverified_claim_disclosed`**, created
  **only for failed claims**. Its average is therefore exactly
  KEY_METRICS.md's definition, with failed claims as the denominator.
- **Dashboard:** View **Scores Boolean** · Metric **Value / Min** · Filter
  **Name = unverified_claim_disclosed** · Big Number. This is an absolute,
  not a target: **Min < 1** means at least one silent drop happened. Pair it
  with Value / Avg broken down by Trace Name for the rate.
- **Find the defect:** *Scores* page → filter Name
  `unverified_claim_disclosed`, sort Value ascending → the Trace and
  Observation links open the offending interaction.
- **Verified locally:** avg **1.0**, n = 3.

### Metric 3 — Authorization Boundary Integrity

- **Emitted as:**
  `log_event("authorization_check_event", AuthorizationCheckEvent(...).model_dump(mode="json"))`,
  plus `use_case=` when known.
- **In Langfuse:** for requests where `requested_scope ∉ held_scopes`, a
  BOOLEAN score **`out_of_scope_request_rejected`** is created (1 = correctly
  rejected, 0 = accepted).
  - An **accepted** out-of-scope request also sets the event to **level
    ERROR**, with the status message "authorization regression".
  - In-scope requests get no score.
  - An unrecognised `outcome` gets no score and level WARNING.
- **Dashboard:** View **Scores Boolean** · Metric **Value / Min** · Filter
  **Name = out_of_scope_request_rejected** · Big Number. It must read **1**;
  anything else is a same-day page, per KEY_METRICS.
- **Every violation:** *Tracing* → filters **Name =
  authorization_check_event**, **Level = ERROR**. The metadata holds the
  `correlation_id`, requested scope and held scopes.
- **Verified locally:** min **1**, n = 3. Observations view grouped by Level
  for `authorization_check_event` → only `DEFAULT`.
- **Nothing in the agent emits this event.** The n = 3 above are the
  synthetic test events from `send_langfuse_test_event.py`. The agent-side
  scope record the event depends on is not built (`docs/ARCHITECTURE.md`
  §2), so with real traffic this widget stays empty.

### Metric 4 — Response Latency p50 / p95 vs the 90-second budget

- **Emitted as:**
  `log_event("latency_event", LatencyEvent(...).model_dump(mode="json"), use_case=..., patient_session_key=...)`
- **In Langfuse:** NUMERIC score **`response_latency_seconds`** =
  `rendered_ts − trigger_ts`. A negative interval gets no score and level
  WARNING.
  - Do **not** use Langfuse's own "Latency" column for this. Events are
    instantaneous, so it reads 0.00 s.
- **Dashboard:** two widgets, since one aggregation per widget. Both: View
  **Scores Numeric** · Metric **Value / P 50** (second widget: **P 95**) ·
  Filter **Name = response_latency_seconds** · Breakdown **Trace Name** ·
  Horizontal Bar Chart. Separate bars per use case, never blended, as
  KEY_METRICS requires. Compare against 90.
- **Verified locally:** p50 = p95 = **41.7 s**, `pre_visit_brief`.

### Metric 5 — Retrieval Coverage per Query (proxy)

- **Emitted as:** `retrieval_event` from `app/fhir/client.py`, already
  wired. It happens whenever the caller passes `use_case`.
- **In Langfuse:** each retrieval observation is tagged
  `retrieved:<ResourceType>` and `use_case:<value>`. `retrieval_event_skipped`
  (no use case) is exported at level WARNING.
- **Dashboard** (per use case; shown for Pre-Visit Brief):
  - **Numerator per type:** View **Observations** · Metric **Trace Id / Uniq**
    · Filters **Name = retrieval_event** and **Trace Name =
    pre_visit_brief** · Breakdown **Tags** · Vertical Bar Chart →
    interactions that queried each resource type.
  - **Denominator:** View **Observations** · Metric **Trace Id / Uniq** ·
    Filter **Trace Name = pre_visit_brief** · Big Number.
  - Coverage of type X = bar(X) ÷ denominator. Required sets (ARCHITECTURE.md
    §3):
    - Pre-Visit Brief: `Condition`, `MedicationRequest`, `AllergyIntolerance`
    - Change Detection: `Encounter`, `Condition`, `MedicationRequest`, `Observation`
    - Follow-Up Drilling: no fixed set by design, so not computable against
      a fixed requirement — same as KEY_METRICS.md.
- **Unattributable retrievals:** View **Observations** · Metric **Count** ·
  Filter **Name = retrieval_event_skipped**.
- **Limitation:** Langfuse can show per-type coverage, and the most
  actionable form: *which* type is missed. The **mean per-interaction
  fraction** isn't expressible as one widget. It needs the Metrics API plus
  a few lines of arithmetic, or a per-interaction coverage event from the
  orchestration layer (a v2 option). Still a proxy, as KEY_METRICS.md
  states: type-level, not row-level.
- **Verified locally:** each of the 3 required types → 3 unique traces;
  denominator 3; coverage 100% per type.

### Metric 6 — Cost per Interaction

- **Emitted as** (for the claim-drafting track — token counts only, never
  content):

  ```python
  log_event(
      "llm_usage_event",
      {
          "model": response.model,
          **response.usage.model_dump(
              include={
                  "input_tokens",
                  "output_tokens",
                  "cache_creation_input_tokens",
                  "cache_read_input_tokens",
              }
          ),
      },
      use_case=...,
      patient_session_key=...,
  )
  ```

  There is no model for this in `metrics_events.py` yet. The expected shape
  lives in `langfuse_export._LlmUsage` and should move to `metrics_events.py`
  as `LlmUsageEvent` once the tracks merge.
- **Why not Langfuse's Anthropic auto-instrumentation:** it records prompts
  and completions (chart content). That is also why the exporter filters
  spans (§4).
- **In Langfuse:** a GENERATION with `model` and usage `input` / `output` /
  `input_cache_read` / `input_cache_creation`. Langfuse prices it from its
  built-in model table, which in 4.37.0 includes `claude-sonnet-5`,
  `claude-opus-5`, `claude-haiku-4-5-20251001`, and others, keyed on exactly
  these usage names.
- **Per interaction:** the *Sessions* page, whose **Total Cost** column is
  cost per `(user, patient, session)`. For Follow-Up Drilling that correctly
  sums every turn.
- **Dashboard average:** View **Observations** · Metric **Total Cost / Sum**
  · Filter **Type = GENERATION** · Breakdown **Trace Name**, divided by a
  second widget: Metric **Unique Session Ids** with the same filter and
  breakdown.
- **Verified locally:** per session **$0.00866** (2,400 in + 1,800 cache-read
  → 350 out; the UI shows "4,200 prompt → 350 completion"). The sum across
  sessions ÷ unique sessions gives the same.
- **Before using these numbers for the AI Cost Analysis deliverable:** check
  Langfuse's price table against Anthropic's current published prices.
  Custom prices go in *Settings → Models*.

### Without Langfuse: `scripts/compute_metrics.py`

The same six metrics can be computed from the agent's stdout alone. Every
`log_event()` writes its JSON line to stdout (`logging.py`) and then
exports it, so no dashboard, deployment or second droplet is needed.

- **Capture:** `configure_logging()` writes to stdout only, so redirect it:
  `uvicorn app.main:app … >> logs/agent-stdout.jsonl`. That path is the
  script's default `--log-file`; `-` reads stdin. `agent/logs/` is
  gitignored.
- **Output:** each metric prints the event type and fields it came from,
  and the log line numbers that contributed, so it can be re-derived by
  hand.
- **Insufficient data:** printed as `insufficient data — 0 events of type X
  found`, never a fabricated 0% or 100%.
- **Exit code:** 1 when an absolute is broken — Metric 2 below 100%, or
  Metric 3's invariant violated.
- **Cost:** USD is reported only with an explicit `--prices` file, because
  no pricing constant exists in this codebase.
- **Verified 2026-09-16:** run against a real captured run of
  `send_langfuse_test_event.py`:
  - Values: 50.0% / 100.0% / invariant holds / p50 = p95 = 41.7 s / 3 of 3
    types / 4,550 tokens.
  - With a price file built from local Langfuse's `claude-sonnet-5` table,
    the script computed $0.008660, which equals Langfuse's recorded
    `totalCost` of 0.00866 for the same trace.

## 7. Proof — the real test event, and what the UI shows

`scripts/send_langfuse_test_event.py` sends one synthetic Pre-Visit Brief
through the real `configure_logging()` / `log_event()` path, with every
payload built from the real pydantic models:

- 3 `retrieval_event`
- 1 `authorization_check_event` (out of scope, rejected)
- 1 `llm_usage_event`
- 2 `claim_verified_event` (one passes; one fails and is disclosed)
- 1 `latency_event`

It then reads everything back through the v4 APIs and exits 1 unless all of
it arrived. Last runs: host venv → `VERIFIED`; built agent image
(`docker build` of `agent/Dockerfile`, joined to the Langfuse network) →
18/18 unit tests OK and `VERIFIED`.

**What appeared in the UI.** Logged in headlessly with Playwright and
screenshotted; this describes those screenshots.

- **Trace detail**, at
  `http://localhost:3000/project/clinical-copilot-agent/traces/427b2f6bbac87268e8cc290bad51fdb4`:
  - Title "Trace · retrieval_event: 427b2f6b…" (see §4, v4 behaviour 4).
  - **Left tree, 8 rows:**
    - `retrieval_event` ×3
    - `llm_usage_event` — "4,200 → 350 (∑ 4,550) $0.00866"
    - `authorization_check_event` — chip `out_of_scope_request_rejected: True`
    - `claim_verified_event` — `verification_passed: True`
    - `claim_verified_event` — `unverified_claim_disclosed: True`, `verification_passed: False`
    - `latency_event` — `response_latency_seconds: 41.70`
  - **Header badges:** "Latency: 0.00s", "Session:
    user:synthetic-physician|patient:synthetic-0001|session:0bdab32d-…",
    "Env: local-dev", "$0.00866".
  - **Selected retrieval_event:** Tags `use_case:pre_visit_brief`, Input
    `null`, Output `undefined`. Metadata includes `event_type`, `use_case`,
    `correlation_id` (`langfuse-smoke-d8d90116-…`), `resource_type`
    (`Condition`), `patient_session_key`.
- **Tracing list:** rows with Name = event type, **Trace Name =
  `pre_visit_brief`**, Environment `local-dev`. The filter sidebar offers
  Name, Type, Environment, Trace Name, Trace Tags, Session ID and more.
- **Scores page:** `response_latency_seconds 41.70`,
  `unverified_claim_disclosed True`, `verification_passed False`,
  `verification_passed True`, `out_of_scope_request_rejected True`, each
  with its Trace and Observation id.
- **Sessions page:** one row per synthetic session, Environment `local-dev`,
  Traces 1, Total Cost `$0.00866`, Usage "4,200 → 350 (∑ 4,550)".
- **Widget builder:** configured as Scores Boolean · Value · Avg · Breakdown
  Name · Horizontal Bar Chart, the preview "Avg Value by Name (Scores
  Boolean)" showed:
  - `out_of_scope_request_rejected` **1**
  - `unverified_claim_disclosed` **1**
  - `verification_passed` **0.5**

**To see it yourself:**

1. Open http://localhost:3000 and sign in.
2. **Tracing** (left nav) → click any `pre_visit_brief` row.
3. Or **Sessions** → click a `user:synthetic-physician|…` session.

## 8. Known gaps (local)

- **No `LlmUsageEvent` model** in `metrics_events.py` yet (§6, Metric 6).
- **~~Metric 6 event-name mismatch at merge~~ — resolved.** The
  claim-drafting branch once logged `llm_call` (`served_model` /
  `requested_model`) instead of `llm_usage_event` (`model`). Both drafting
  call sites (`app/reasoning/draft_claims.py`, `draft_follow_up.py`) now log
  `llm_usage_event` with `model`, which the exporter prices.
  `compute_metrics.py` still reads both names.
- **Metric 5's mean fractional coverage** needs API arithmetic (§6).
- **Dashboards are recipes, not provisioned objects.** Widgets live in each
  Langfuse instance's Postgres, so they must be created (or exported and
  imported, via the builder's *Import*) on the production instance.
- **The local dev keys and password are for this laptop only.** Droplet 2
  generates its own.

## 9. Production deployment gap — provisioning checklist

**Not done. No droplet or cloud resource was created by this task.** Written
for DigitalOcean, before the application host moved to Vultr; see the
provider note at the top. Topology per the original plan:

- **Droplet 1:** OpenEMR + agent.
- **Droplet 2:** Langfuse only.
- **Link:** the agent reaches Langfuse over the private VPC network only,
  so traces never cross the public internet.

Facts from DigitalOcean's docs (checked 2026-09-16) that the steps rely on:

- VPC networks are regional.
- VPC peering works "across all regions" (`doctl vpcs peerings create`).
- Droplets created before 2 October 2024 need a manual update to carry
  peering traffic.
- Cloud Firewalls "stop traffic at the network layer before it reaches the
  server", "affect both public and VPC network traffic", and deny unmatched
  traffic by default.
- The VPC docs make **no claim that VPC traffic is encrypted**, so treat it
  as plaintext on a private network.

### 0. Before touching DigitalOcean

- [ ] Confirm an **executed BAA** with DigitalOcean covers this account.
      AUDIT.md F-41 records this as *not confirmed*. Droplet 2 will store
      session ids containing patient identifiers.
- [ ] **Decide transit protection** between the droplets and record the
      decision in ARCHITECTURE.md:
  - **Option A:** bind Langfuse to droplet 2's VPC private IP, plus a Cloud
    Firewall. Plaintext on the private network. Acceptable only while all
    data is synthetic.
  - **Option B (recommended before any real PHI):** a WireGuard tunnel
    between the droplets; bind Langfuse to the tunnel IP.
- [ ] Record droplet 1's facts:
      `doctl compute droplet get <droplet-1-id> --format ID,Name,Region,VPCUUID,PublicIPv4,PrivateIPv4`

### 1. SSH key (dedicated to droplet 2)

- [ ] `ssh-keygen -t ed25519 -f ~/.ssh/clinical-copilot-langfuse -C clinical-copilot-langfuse`
- [ ] `doctl compute ssh-key import clinical-copilot-langfuse --public-key-file ~/.ssh/clinical-copilot-langfuse.pub`
- [ ] Note the fingerprint: `doctl compute ssh-key list`

### 2. Create droplet 2 in droplet 1's network

- [ ] Pick a size with **≥4 vCPU and ≥16 GiB RAM** (Langfuse's stated
      minimum) and **≥100 GiB disk**:
      `doctl compute size list --format Slug,VCPUs,Memory,Disk,PriceMonthly`.
      If the plan's disk is smaller, add a Volume
      (`doctl compute volume create langfuse-data --region <region> --size 100GiB`),
      attach it, and put Docker's data-root on it before step 6. Volumes are
      on DigitalOcean's HIPAA-eligible list (F-41).
- [ ] **Preferred — same region, same VPC** (no peering needed):
      `doctl compute droplet create clinical-copilot-langfuse --region <droplet-1-region> --vpc-uuid <droplet-1-VPCUUID> --size <slug> --image ubuntu-24-04-x64 --ssh-keys <fingerprint> --enable-monitoring --tag-names clinical-copilot,langfuse --wait`
- [ ] **Only if a different region is unavoidable:** create droplet 2 in
      that region's VPC, then
      `doctl vpcs peerings create clinical-copilot-langfuse --vpc-ids <vpc-1-uuid>,<vpc-2-uuid>`.
      If droplet 1 predates 2 October 2024, apply DigitalOcean's manual
      peering update to it.
- [ ] Record droplet 2's `PublicIPv4` and `PrivateIPv4`.

### 3. Cloud Firewall on droplet 2 — before anything listens

- [ ] Create it:
      `doctl compute firewall create --name clinical-copilot-langfuse --droplet-ids <droplet-2-id> --inbound-rules "protocol:tcp,ports:22,address:<operator-public-ip>/32 protocol:tcp,ports:3000,address:<droplet-1-private-ip>/32" --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0 protocol:udp,ports:all,address:0.0.0.0/0"`
      For Option B, also allow inbound `protocol:udp,ports:51820,address:<droplet-1-private-ip>/32`.
- [ ] Remember this is defence in depth, filtering before Docker's iptables.
      It does **not** replace the compose bind address (step 6).

### 4. Harden the host

- [ ] SSH is key-only, matching F-38. Check
      `sudo sshd -T | grep -Ei 'passwordauthentication|permitrootlogin'` →
      `passwordauthentication no`.
- [ ] Create a non-root sudo user. Run `apt update && apt full-upgrade`.
      Enable `unattended-upgrades`.
- [ ] Install Docker Engine and the compose plugin from Docker's apt
      repository.

### 5. Option B only — WireGuard between the droplets

- [ ] `apt install wireguard` on both. Generate a key pair on each
      (`wg genkey | tee private | wg pubkey > public`).
- [ ] **Droplet 2** `/etc/wireguard/wg0.conf`: `Address = 10.99.0.2/24`,
      `ListenPort = 51820`, `[Peer]` with droplet 1's public key and
      `AllowedIPs = 10.99.0.1/32`.
- [ ] **Droplet 1**: `Address = 10.99.0.1/24`, `[Peer]` with droplet 2's
      key, `Endpoint = <droplet-2-private-ip>:51820`,
      `AllowedIPs = 10.99.0.2/32`.
- [ ] `systemctl enable --now wg-quick@wg0` on both. From droplet 1, `ping
      10.99.0.2` must succeed.
- [ ] On droplet 2, make Docker start after the tunnel, otherwise the port
      bind to 10.99.0.2 fails at boot: a `docker.service` drop-in with
      `After=wg-quick@wg0.service` and `Requires=wg-quick@wg0.service`.

### 6. Deploy Langfuse on droplet 2

- [ ] Put `agent/observability/` on droplet 2 (git checkout of the merged
      branch, or `scp -r`).
- [ ] Run `agent/observability/generate-env.sh` **on droplet 2**. Never copy
      the laptop's `.env`. The only secrets that ever leave droplet 2 are
      the project public/secret key pair.
- [ ] Edit the single `ports:` line in `docker-compose.langfuse.yml`
      **in the file itself**, in a reviewed commit — **not** an override
      file, since Compose appends override ports rather than replacing them
      (F-54):
  - Option A: `"<droplet-2-private-ip>:3000:3000"`
  - Option B: `"10.99.0.2:3000:3000"`

  Never `0.0.0.0:3000:3000`, never bare `3000:3000`. This is the only
  non-loopback bind in the project, and its stated reason is that droplet
  1's agent must reach it.
- [ ] `docker compose -f agent/observability/docker-compose.langfuse.yml up -d --wait`.
      All six services report `healthy`.

### 7. Verify the binding — before sending a single trace (ARCHITECTURE.md §1)

- [ ] **Droplet 2:**
      `sudo ss -tlnp | grep -E ':(3000|3030|5432|6379|8123|9000|9090|9091)\b'`
      → exactly one listener, `<bind-ip>:3000` (docker-proxy). Nothing on
      `0.0.0.0` or `[::]`.
- [ ] **Droplet 2:**
      `docker compose -f agent/observability/docker-compose.langfuse.yml config --format json | jq -c '.services[] | .ports // empty'`
      → one entry, `host_ip` = bind IP.
- [ ] **From the laptop** (external):
      `for p in 3000 3030 5432 6379 8123 9000 9090 9091; do nc -vz -w 5 <droplet-2-public-ip> $p; done`
      → every port times out or is refused.
- [ ] **An independent external port checker** (as F-54 used) → port 3000
      closed on the public IP.
- [ ] **From droplet 1:** `curl -sS http://<bind-ip>:3000/api/public/health`
      → `{"status":"OK","version":"4.37.0"}`.

### 8. Point droplet 1's agent at it

- [ ] Agent environment on droplet 1, from a 0600 env file, never committed:
  - `LANGFUSE_BASE_URL=http://<bind-ip>:3000`
  - `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` = droplet 2's
    `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `…_SECRET_KEY`
  - `LANGFUSE_TRACING_ENVIRONMENT=production` (or `demo` while data is
    synthetic)
- [ ] Agent logs show `observability_export_enabled`, then
      `observability_export_auth_check` with `"ok": true`.
- [ ] Inside droplet 1's agent container:
      `python scripts/send_langfuse_test_event.py` → prints `VERIFIED`, exit 0.
- [ ] `GET /ready` on droplet 1's agent → `"observability_backend":
      {"status": "healthy"}`.
- [ ] For load tests on droplet 1, record whether `LANGFUSE_*` export was
      enabled for each baseline run. The SDK's batching thread still runs on
      droplet 1.

### 9. Operator access to the UI — SSH tunnel only

- [ ] `ssh -i ~/.ssh/clinical-copilot-langfuse -L 3000:<bind-ip>:3000 <user>@<droplet-2-public-ip>`,
      then open http://localhost:3000. `NEXTAUTH_URL` stays
      `http://localhost:3000`.
- [ ] Sign in with droplet 2's `LANGFUSE_INIT_USER_EMAIL` /
      `LANGFUSE_INIT_USER_PASSWORD`. Sign-up is disabled.
- [ ] Recreate the §6 dashboard widgets, or *Import* them from the local
      instance.

### 10. Operate

- [ ] **Backups.** Langfuse's own docs say the compose setup "lacks …
      backup functionality". Choose DigitalOcean droplet backups/snapshots
      (confirm they are covered by the BAA) and/or scheduled `pg_dump` plus
      ClickHouse `BACKUP` to a Volume.
- [ ] **Disk alert.** DigitalOcean Monitoring alert at disk > 80% on
      droplet 2 (ClickHouse and MinIO grow with every event).
- [ ] **Doc update (outside `/agent`).** Update `docs/ARCHITECTURE.md` §1
      and §6 to the two-droplet topology and the transit decision from
      step 0.
