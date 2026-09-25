# Production Langfuse on the live host — redeploy of `main@06f0d45`, 2026-09-25

Work order 30, phase B (D58). The agent host `64.177.42.210` went from
`main@ea198c8` to `main@06f0d45`, which carries D56 (HMAC of the session
key), D58 (access log off, `source_id` HMAC, `/ready` degraded, the
compose pass-through) and the module's D54 upload scopes. It now exports to
the self-hosted Langfuse at <https://64-177-51-119.sslip.io>. Procedure:
`docs/w2/DEPLOY.md` §1–§3; runbook: `docs/w2/OBSERVABILITY.md` §7.

**Not deployed:** the WO31 patient dashboard (`dashboard/`, D59). It is on
`main`, but DEPLOY.md does not cover it, and it is a separate service with
its own compose file.

## What was done, in order

| Step | Result |
|---|---|
| Snapshot (§1): `/root/w2-deploy-20260925/` | DB dump completed 14:38:31Z; module and agent trees; `agent.env.pre`; Caddyfile; globals; `ss`, `docker stats`, container list; the Caddy CA fingerprint `C5:54:80:…:81:89`. The running image is also tagged `clinical-copilot-agent:pre-langfuse-ea198c8`. |
| Build `clinical-copilot-agent:w2-06f0d45` from `git archive` | 169 s. The image's `CMD` has `--no-access-log`. |
| Offline cold start (`--network none`) | `retrieval_preload_event`, startup complete. With no `AGENT_TELEMETRY_HMAC_SECRET` the image refuses to start (`startup_refused`, `TelemetrySecretError`). DEPLOY.md's cold-start command predates D56: it needs `-e AGENT_TELEMETRY_HMAC_SECRET=<throwaway>` now. |
| Upload guard on the new image, before the proxy served it | See the table below. It ran in a throwaway container with a temporary password-grant client, both deleted afterwards (2 tokens, 2 refresh tokens, 1 client). |
| Merge `agent.env.langfuse-staged` into `agent.env` | 7 keys, no duplicates, mode 600 |
| Swap (`up -d --no-deps --no-build --wait agent`) | healthy in 12.2 s; 13 non-200 health polls at 0.5 s (≈6.5 s of 502s, the same as the rehearsal). Proxy not recreated: its Caddyfile is byte-identical to `a78ba51`'s. |
| Module files (§3) | 72 PHP files lint clean. Every archived file matches on the host. |
| Listening sockets | identical to before the deploy (`diff` of `ss -tlnp`); 8000 never published |

**Upload guard on `w2-06f0d45`** (DEPLOY.md §2.2). Non-PDF bodies were used
throughout, so no extraction, model call or write-back could start:

| Request | Result |
|---|---|
| no bearer / made-up bearer | 401 `missing_bearer_token` / 401 `malformed_bearer_token` |
| real token with `openid user/Patient.read user/Condition.read` only | 403 `insufficient_scope` |
| unknown patient | 404 `patient_not_found` |
| real token with the three scopes, non-PDF body | 415: the guard admitted it; the body check refused it |
| status on another chart / with a made-up bearer | 404 / 401 |
| 4 simultaneous 24 MiB uploads, throttled, 2 slots | 2 × 415, **2 × 503** `Retry-After: 10` |
| 11th upload for one patient in the window | **429**, `Retry-After: 591` |

## Verified on the host

**`/ready`:** 200, `status: "ready"`.

- `fhir`: healthy;
- `llm_provider`: healthy;
- `observability_backend`: healthy, `backend: "langfuse"`.

The startup log has `observability_export_enabled`, and no
`telemetry_hmac_secret_dev_default`.

**A real upload through the chart card**, driven in Chrome as `physician` on
pid 38 (patient `a2c373ec-…`, sample `lab-clean-001`), chart-open
correlation id `ed58060d-…`:

- **The upload:** extraction complete in 42 s.
  - Vision: 33.2 s, $0.126.
  - Located rate 98.1%.
- **Langfuse:** one trace, `705fe3db6159493e792b2898ee1000a0`, 162 spans,
  environment `production`. It holds:
  - the upload and 9 status polls;
  - 4 `intake_extractor` steps;
  - 1 `lab_writeback` tool call;
  - the ask turn.

  The trace carries one `psk_…` session.
  ([`langfuse-encounter-trace.png`](langfuse-encounter-trace.png), taken
  signed in as the read-only grader.)
- **Write-back (D54):** order 8199 on pid 38 with 21 results,
  `review_status = received`. It is listed under **Procedures → Pending
  Review** ([`procedures-pending-review.png`](procedures-pending-review.png)).
  The ledger has 21 rows, 1 order and 1 document. The throwaway containers
  wrote nothing: their tokens had no write-back scope.
- **Every chart open also makes a second, 40-span trace:** the Week 1 brief
  card mints its own correlation id.

**Zero patient identifiers in the new traces.** `scan-uuids.sh production`
covers every ClickHouse table, plus every raw MinIO blob:

- 1,015 distinct UUID-shaped values;
- **0** are in the production OpenEMR's `uuid_registry` (30,507 UUIDs,
  including today's 21 written-back result rows, which are the positive
  control);
- **0** are patient UUIDs (52).

All 1,015 have a named source:

- 52 encounter correlation ids (UUID4, D36);
- Langfuse's own event and file ids;
- the OTel instance id;
- the project public key;
- 6 values only in MinIO, from the 2026-09-24 `local-proof` run. The MinIO
  scan has no environment filter; they are that run's 5 correlation ids and
  its instance id.

A literal "zero UUID-shaped values" cannot hold while D36 makes encounter ids
UUID4s (D58).

**The host's stdout since the swap** (555 lines):

- 0 access-log lines;
- 0 `patient_id=`;
- 0 raw `source_id` UUIDs, and 333 `rid_…` HMACs;
- 0 `psk_dev_`;
- 0 UUIDs from OpenEMR's registry.

**The stdout dashboard still works:** `scripts/w2_dashboard.py` over that
log ([`stdout-dashboard.png`](stdout-dashboard.png)).

- 18 requests, 0 5xx;
- 324/324 claims verified;
- the extraction and write-back steps present;
- `tool_failure_rate`, `verification_failure_spike` and
  `text_layer_located_rate_collapse` all `ok`.

The Langfuse dashboard has every widget populated
([`langfuse-dashboard.png`](langfuse-dashboard.png)). Its Env filter
includes the 2026-09-24 `local-proof` run.

## Finding: the document question times out in the chart card, on both builds

The card's question "What were the results on the uploaded lab report?"
showed *"The co-pilot service is unavailable right now"* 3 times out of 3
([`card-answer.png`](card-answer.png)).

- **The agent answered each time:** `/w2/ask` returned 200 and the turn
  completed, but in **54–58 s**.
- **The module's ask proxy gave up at about 40 s** and returned 502 to the
  browser.
- **The time is output tokens:** 6.2–7.0k per answer (≈$0.24), against
  368–461 in D50's measurement on this host (DEPLOY.md §8.00, 2026-09-23).
- **The response is the same 88,079 bytes every time,** so the extra tokens
  are the model's adaptive thinking, not a longer answer.

**It is not caused by this deploy.** The same question and document, run
directly against a throwaway container of each image on the host:

| Image | Ask 1 | Ask 2 | Ask 3 |
|---|---|---|---|
| `ea198c8` (previously deployed) | 8.9 s, 638 output tokens | 53.1 s, 6,115 | 56.3 s, 6,429 |
| `06f0d45` (now deployed) | 56.7 s, 6,594 | 62.7 s, 7,047 | 57.8 s, 6,647 |

- **Same inputs:** input tokens are identical (15,118), and so are effort
  (`medium`) and the installed packages (`pip freeze` identical,
  `anthropic==1.8.0`).
- **Same answer code:** the only code changes on the answer path between
  the two commits are refactors.
- **So rolling back would not fix it.** Options, not taken here:
  - lower or disable thinking on the answer step;
  - raise the module's ask timeout above the agent's worst case.

The stdout dashboard shows it too: `answer_p95_latency` is 57.6 s against a
45 s threshold, `INSUFFICIENT_DATA` at n = 6/10.

## Rollback

**Rollback of the export alone** keeps the new image. Remove the four
`LANGFUSE_*` lines from `/etc/clinical-copilot/agent.env`, then:

```bash
cd /opt/clinical-copilot/agent/deploy
docker compose --env-file /etc/clinical-copilot/agent.env \
  -f docker-compose.agent.yml up -d --no-deps --no-build --wait agent
```

**Rollback of the agent to `ea198c8`.** Restore `agent.env.pre` **with** the
image. An `ea198c8` agent given `LANGFUSE_*` would export raw session keys,
patient uuids included, because it predates D56:

```bash
B=/root/w2-deploy-20260925
docker tag clinical-copilot-agent:pre-langfuse-ea198c8 clinical-copilot-agent:local
tar -C /opt/clinical-copilot -xzf $B/agent-tree-pre.tgz
cp -p $B/agent.env.pre /etc/clinical-copilot/agent.env
cd /opt/clinical-copilot/agent/deploy && docker compose --env-file /etc/clinical-copilot/agent.env \
  -f docker-compose.agent.yml up -d --no-deps --no-build --wait agent
```

`agent.env.pre` still holds `AGENT_TELEMETRY_HMAC_SECRET`, which `ea198c8`
ignores. It holds no `LANGFUSE_*`.

**Module:** replace the module directory with `$B/module-pre.tgz`, then
`chown -R 1001:1001`.

**The test write-back:** order 8199 and its 21 results. Delete it by DEPLOY.md
§7.4, keyed on the ledger.

**Last resort:** `$B/openemr-pre.sql.gz` (DEPLOY.md §6.4).

## Cost of this run

About **$2.8** of model spend:

- the card's extraction and 3 asks;
- 3 Pre-Visit Briefs from chart opens;
- 4 extractions and 6 asks in the A/B.

The A/B extractions are estimated at $0.126 each; they ran in throwaway
containers whose logs were removed.
