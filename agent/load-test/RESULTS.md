# Load and baseline performance test results

Run 2026-09-20 against the live Vultr host (`64.177.42.210`). The deployed
agent's `app/` is AST-identical to `main` at `28681e5` — the two files that
differ (`app/fhir/exceptions.py`, `app/models/metrics_events.py`) differ only
in module docstrings, checked by parsing both and comparing with docstrings
removed. `AGENT_STUB_LLM_DRAFTING` was set for the duration of test (a) and
reverted immediately after (see "What was run"). Methodology and how to
reproduce: `README.md` in this directory.

This replaces the 2026-09-16 run against the DigitalOcean droplet
(`201.79.25.22`, 4 vCPU / 11.7 GiB), which is kept for comparison in
"Vultr vs. the droplet" below. Both runs used the same scripts, the same
concurrency levels and the same generator (the operator's laptop).

## Methodology note (why two separate tests)

Running 50 concurrent *real* requests means 50 real Anthropic API calls in
one wave — expensive, and the resulting percentiles would be dominated by
Anthropic's own latency/rate limits, not by this agent's own connection
handling or FHIR round-trips. So this is split in two, and the two numbers
answer different questions:

- **(a) Infra load test** (`AGENT_STUB_LLM_DRAFTING=true`, this report's
  main result) — the LLM call is replaced with a same-shape claim built
  directly from the already-retrieved FHIR data (verifies cleanly, costs
  nothing). Every other step is real: real bearer token, real Patient +
  Condition + MedicationRequest + AllergyIntolerance reads against
  OpenEMR's FHIR API, real verification, real structured logging. This
  isolates *this system's* behavior under concurrency from Anthropic's.
- **(b) Real end-to-end batch** — real LLM calls, sequential, a handful of
  requests. Answers "what does a physician actually wait, and what does it
  cost," not a concurrency question. **Run this time** (it was
  operator-pending in the droplet run); see below.

Host under test: **4 vCPU, 7.24 GiB RAM** (the droplet had 11.7 GiB — see
"Vultr vs. the droplet", where that difference matters).

Patient: Phil Belford on Vultr (`a2c36e54-af42-4c38-b86e-ade616ed5494`),
a 5-record chart.

## (a) Infra load test results

| Concurrency | Requests | Error rate | p50 | p95 | p99 | max | Throughput achieved |
|---|---|---|---|---|---|---|---|
| 10 VUs, 30s | 79 | **0.00%** (0/79) | 2.98 s | 4.03 s | 4.76 s | 4.96 s | 2.39 req/s |
| 50 VUs, 30s | 123 | **56.1%** (69/123) | 13.03 s | 21.59 s | 23.38 s | 24.78 s | 3.15 req/s |

(Blended across all requests, failed and successful. Min at 10 VUs was
1.73 s; at 50 VUs, 3.08 s.)

For the 54 requests that *did* succeed at 50 VUs, latency was higher than
the blended number — contention delayed the ones that eventually made it
through more than it delayed the ones that failed fast:
p50 = 16.52 s, p95 = 22.93 s, p99 = 24.07 s, max = 24.78 s.

Raw k6 JSON summaries: `results-10vu.json` / `results-50vu.json`
(gitignored — regenerate with README.md's commands).

### Root cause of the 50-VU tail and error rate: OpenEMR, not the agent

`docker stats` sampled continuously throughout both runs (25 samples each).
"Peak" is the highest value seen for that metric across the run's samples —
CPU and memory peaks did not always land on the same sample, so read each
column independently, not as one simultaneous snapshot:

| Container | At rest | 10 VUs (peak CPU / peak mem) | 50 VUs (peak CPU / peak mem) |
|---|---|---|---|
| **agent** (this service) | 0.19% CPU, 107 MiB | 17.0% CPU / 94 MiB, 7 PIDs | 20.0% CPU / 110 MiB, 7 PIDs |
| **openemr** (Apache/PHP) | 0.58% CPU, 813 MiB | **396% CPU** / 1.63 GiB, PIDs 39→78 | **369% CPU** / **4.94 GiB**, PIDs 39→293 |
| **mysql** | 0.02% CPU, 766 MiB | 61% CPU / 783 MiB | 69% CPU / 788 MiB, PIDs 81→166 |

Host memory went from 1.98 GiB used at rest to 2.85 GiB at 10 VUs and
**6.16 GiB at 50 VUs, leaving 1.08 GiB available**. No OOM kill occurred
(`dmesg` clean), but that is a thin margin — see the memory note below.

The agent process never rose above 20% of one core, 110 MiB, or 7 PIDs at
either level. It is not the bottleneck. OpenEMR's own Apache/PHP stack is:
it pinned **roughly all 4 cores at 10 VUs already** (396%) and grew to
4.94 GiB resident at 50 VUs (from a 813 MiB baseline) as it spawned worker
processes (39 → 293 PIDs) faster than it could retire them. OpenEMR's own
access log for the 50-VU window shows it erroring on its own side: 1,357
responses of HTTP 200, but **97 of HTTP 500**, 19 of 401, 9 of 400 and 4
of 404.

Agent-side, the 79 logged `pre_visit_brief_failed` events during the 50-VU
window break down as:

| Error | Count | What it means |
|---|---|---|
| `fhir_unavailable` | 70 | OpenEMR refused or errored on the FHIR call (its 500s) |
| `fhir_token_rejected` | 7 | OpenEMR returned 401 for a token that was valid |
| `patient_not_found` | 2 | OpenEMR returned an empty Patient read under load |

(k6 counted 69 HTTP-level failures; the agent logged 79 failed briefs, the
difference being requests still in flight as the 30-second window closed.)

The dominant mode is the same one the droplet run found. The last two rows
are new, and worth stating: **under saturation OpenEMR can reject a valid
token and can answer a real patient id with an empty result.** The agent
failed closed on both — a 401 surfaced as `fhir_token_rejected`, and the
empty Patient read hit the existing patient-exists guard (`5ba6c61`) and
returned 404 rather than rendering a brief for nobody. No request returned
a brief built on missing data.

The host recovered on its own after the run: within ~20 s OpenEMR was back
to 1.23 GiB and 72 PIDs, memory to 2.6 GiB used, all containers healthy.

**Reading this**: this host's OpenEMR/Apache/PHP capacity, not this agent's
code, is what caps concurrent throughput. The agent has substantial untapped
headroom (≤20% of one core, ≤110 MiB) at both levels tested. Scaling further
means giving OpenEMR more PHP workers, CPU and RAM — or moving it off the
same host as the agent — not optimizing the agent's own request handling.

### The memory margin is the new constraint

The droplet had 11.7 GiB and OpenEMR peaked at 4.49 GiB, leaving room to
spare. This host has 7.24 GiB and OpenEMR peaked at 4.94 GiB, leaving
1.08 GiB. The 50-VU run did not OOM, but it came within about a gigabyte of
the point where the kernel would start killing processes — and MySQL, at
788 MiB, is one of the candidates. **Do not raise the concurrency level on
this host without watching `free -m` live.** This is a property of the host,
not of the agent.

## Baseline (idle, before any load)

| Container | CPU % | Memory |
|---|---|---|
| agent | 0.19% | 106.9 MiB / 7.244 GiB (1.44%) |
| proxy (Caddy) | 0.06% | 12.2 MiB |
| openemr | 0.58% | 812.6 MiB (10.96%) |
| mysql | 0.02% | 766.2 MiB (10.33%) |
| phpmyadmin | 0.01% | 12.3 MiB |

Host: 4 vCPU, 7.24 GiB RAM, ~1.98 GiB used / 5.26 GiB available at rest.

## Vultr vs. the droplet (2026-09-16, `201.79.25.22`)

Same scripts, same VU levels, same generator. The droplet had 4 vCPU and
11.7 GiB; this host has 4 vCPU and 7.24 GiB.

| Metric | Droplet (2026-09-16) | Vultr (2026-09-20) |
|---|---|---|
| 10 VUs — requests | 158 | 79 |
| 10 VUs — error rate | 0.00% | 0.00% |
| 10 VUs — p50 / p95 / p99 | 888 ms / 1.20 s / 2.00 s | 2.98 s / 4.03 s / 4.76 s |
| 10 VUs — throughput | 5.04 req/s | 2.39 req/s |
| 50 VUs — error rate | 59.0% | 56.1% |
| 50 VUs — p50 / p95 / p99 (blended) | 6.54 s / 11.98 s / 12.75 s | 13.03 s / 21.59 s / 23.38 s |
| 50 VUs — throughput | 6.20 req/s | 3.15 req/s |
| Agent peak CPU / mem | 18.7% / 115 MiB | 20.0% / 110 MiB |
| OpenEMR peak CPU / mem | 360% / 4.49 GiB | 369% / 4.94 GiB |
| OpenEMR peak PIDs | 228 | 293 |
| Host RAM | 11.7 GiB | 7.24 GiB |

**The finding holds, and holds harder.** On both hosts the agent stays flat
and OpenEMR saturates — the conclusion is unchanged. Three differences are
worth recording:

1. **This host is roughly half as fast per request.** p50 at 10 VUs is 3.4×
   the droplet's, and sustained throughput is about half, at both levels.
   OpenEMR pinned ~4 cores at 10 VUs here versus 336% on the droplet, so
   saturation begins at a lower concurrency.
2. **The agent's own numbers are unchanged** — 20.0% CPU and 110 MiB peak
   here against 18.7% and 115 MiB on the droplet. Whatever is slower, it is
   not the agent, and that is the point of stubbing the LLM call.
3. **Memory is tighter.** See the note above.

Not comparable across the two runs: the generator's network path (the same
laptop, but four days apart), and the patient — the droplet run used the
DigitalOcean-era Phil Belford id, this run his Vultr id. Both are 5-record
charts, so retrieval volume is equivalent.

## (b) Real end-to-end batch — run 2026-09-20

`N=5` sequential requests, real Anthropic calls, stub off. All 5 returned
HTTP 200 with 5 of 5 claims verified and 0 unverified.

| Request | total | retrieve | draft (LLM) | verify | render |
|---|---|---|---|---|---|
| 1 | 4.78 s | 0.77 s | 4.00 s | 0.5 ms | 0.1 ms |
| 2 | 4.92 s | 0.73 s | 4.18 s | 0.4 ms | 0.1 ms |
| 3 | 4.53 s | 0.75 s | 3.77 s | 0.6 ms | 7.5 ms |
| 4 | 4.44 s | 0.76 s | 3.67 s | 0.4 ms | 0.1 ms |
| 5 | 4.41 s | 0.65 s | 3.76 s | 0.3 ms | 0.1 ms |
| **mean** | **4.61 s** | **0.73 s** | **3.88 s** | **0.4 ms** | **1.6 ms** |

**Cost:** 1,574 input and 325 output tokens per brief, identical on all five
(the same chart and a stable prompt), **$0.016 per brief**, $0.08 for the
batch. That matches `docs/COST_ANALYSIS.md` §1.2's measured per-brief figure
exactly.

**What this says next to (a).** A real brief takes 4.61 s, of which the
model call is 3.88 s (84%) and the FHIR retrieval 0.73 s. Verification and
rendering are together under 2 ms — §5's deterministic checker costs
effectively nothing. Well inside the ~90 s between encounters (USERS.md
§1–2; KEY_METRICS.md Metric 4).

## What was run

All from the operator's laptop, over the internet, with SSH to the host:

1. Confirmed the deployed agent's `app/` is AST-identical to `main`
   (`28681e5`) apart from two module docstrings, `/ready` healthy, and
   recorded the at-rest `docker stats` baseline and `ss -tlnp` bindings.
2. Enabled `AGENT_STUB_LLM_DRAFTING=true` on the `agent` service only
   (`docker compose up -d --no-deps --wait agent`), confirmed
   `draft_claims._STUB_LLM_DRAFTING is True`, re-checked bindings against
   the pre-test baseline (unchanged — F-54: `0.0.0.0:8443` the only agent
   listener, `8000` not published), and verified one stubbed request end to
   end (`draft_seconds` = 0.0003, so no model call).
3. Ran the 10-VU and 50-VU infra tests (`k6-infra.js`, k6 v2.2.0, 30 s
   each), sampling `docker stats` and `free -m` on the host throughout.
4. Reverted the flag by restarting the same service without it, and
   confirmed `draft_claims._STUB_LLM_DRAFTING is False`, bindings unchanged,
   and all five containers healthy.
5. Ran the real end-to-end batch (b), which is also the post-test proof that
   the production path works: 5 real briefs, all 200, all claims verified.
6. Read the token counts for those five calls back out of the agent's own
   `llm_usage_event` log for the cost line above.

No agent code was changed in this session, on the host or in the repository.
The only host-side change was the stub environment variable, set and
reverted.

## Generator location

Run from the operator's laptop, not the host under test — the generator's
CPU and network never competed with the system being measured. **Limitation,
stated plainly**: the laptop's network path is not a controlled environment
the way a dedicated cloud VM's egress would be, so some fraction of the
measured latency (especially the low end, e.g. the 1.73 s minimum at
10 VUs) is this network hop, not host-side processing. The p95/p99 growth
between 10 and 50 VUs, the CPU/memory evidence and the root-cause finding
(OpenEMR saturation, not the agent) are host-side and unaffected. The same
limitation applied to the droplet run, so the comparison above inherits it
on both sides.
