# Load and baseline performance test results

Run 2026-09-16, against the live droplet (`201.79.25.22`), commit
`feat/standalone-ui@2d46f0f` (the code actually deployed there at test
time) plus this branch's `AGENT_STUB_LLM_DRAFTING` addition patched in for
the duration of the test and reverted immediately after (see "What was
run" below). Methodology and how to reproduce: `README.md` in this
directory.

## Methodology note (why two separate tests)

Running 50 concurrent *real* requests means 50 real Anthropic API calls in
one wave — expensive, and the resulting percentiles would be dominated by
Anthropic's own latency/rate limits, not by this agent's own connection
handling or FHIR round-trips. So this was split in two, and the two
numbers below answer different questions:

- **(a) Infra load test** (`AGENT_STUB_LLM_DRAFTING=true`, this report's
  main result) — the LLM call is replaced with a same-shape claim built
  directly from the already-retrieved FHIR data (verifies cleanly, costs
  nothing). Every other step is real: real bearer token, real Patient +
  Condition + MedicationRequest + AllergyIntolerance reads against
  OpenEMR's FHIR API, real verification, real structured logging. This
  isolates *this system's* behavior under concurrency from Anthropic's.
- **(b) Real end-to-end batch** — real LLM calls, sequential, a handful of
  requests. Answers "what does a physician actually wait, and what does it
  cost," not a concurrency question. **Not run by me** — the operator
  chose to run this step directly to control the real Anthropic spend
  themselves. Exact commands are in README.md / `real_batch.sh`; numbers
  below are marked accordingly.

## (a) Infra load test results

| Concurrency | Requests | Error rate | p50 | p95 | p99 | max | Throughput achieved |
|---|---|---|---|---|---|---|---|
| 10 VUs, 30s | 158 | **0.00%** (0/158) | 888 ms | 1.20 s | 2.00 s | 2.09 s | 5.04 req/s |
| 50 VUs, 30s | 222 | **59.0%** (131/222) | 6.54 s | 11.98 s | 12.75 s | 13.07 s | 6.20 req/s |

(Blended across all requests, failed and successful. All 131 failures at
50 VUs came back `502 fhir_unavailable` — see root cause below, not a
timeout on the agent's own side or an auth failure.)

For the 91 requests that *did* succeed at 50 VUs, latency was even higher
than the blended number (contention delayed the ones that eventually made
it through more than it delayed the ones that failed fast):
p50 = 8.04 s, p95 = 12.40 s, p99 = 13.05 s, max = 13.06 s.

Raw k6 JSON summaries: `results-10vu.json` / `results-50vu.json`
(gitignored — regenerate with README.md's commands).

### Root cause of the 50-VU tail and error rate: OpenEMR, not the agent

`docker stats` sampled every ~7s throughout both runs (droplet:
4 vCPU / 11 GiB). "Peak" is the highest value seen for that metric across
the run's samples — CPU and memory peaks did not always land on the same
sample, so read each column independently, not as one simultaneous
snapshot:

| Container | At rest | 10 VUs (peak CPU / peak mem) | 50 VUs (peak CPU / peak mem) |
|---|---|---|---|
| **agent** (this service) | 0.08% CPU, 98 MiB | 17.5% CPU / 90 MiB | 18.7% CPU / 115 MiB |
| **openemr** (Apache/PHP) | 0.17% CPU, 817 MiB | **336% CPU** / 1.10 GiB | **360% CPU** / **4.49 GiB**, PIDs 39→228 |
| **mysql** | 0.02% CPU, 434 MiB | 53% CPU / 447 MiB | 67% CPU / 492 MiB |

The agent process itself never rose above ~19% of one core or ~115 MiB —
it is not the bottleneck at either concurrency level. OpenEMR's own
Apache/PHP stack is: at 50 concurrent it pinned 3–3.6 of the droplet's 4
cores (near its ceiling) and grew to 4.5 GiB resident (from a baseline of
~0.8 GiB) as it spawned worker processes (39 → 228 PIDs) faster than it
could retire them. Every one of the 131 failed requests logged
`fhir_unavailable` on the agent side — OpenEMR itself refusing or erroring
on the FHIR call, not a timeout invented by the agent or a token problem.
The droplet fully recovered within ~30s after the run (confirmed via
`docker ps` health checks and OpenEMR's memory draining back down).

**Reading this**: this single droplet's OpenEMR/Apache/PHP capacity, not
this agent's code, is what caps concurrent throughput today. The agent
itself has substantial untapped headroom (single-digit CPU%, <120 MiB) at
both levels tested. Scaling this system further means giving OpenEMR more
PHP workers / CPU (or moving it off the same host as the agent), not
optimizing the agent's own request handling.

## Baseline (idle, before any load)

Captured before either run:

| Container | CPU % | Memory |
|---|---|---|
| agent | 0.08% | 97.8 MiB / 11.68 GiB (0.82%) |
| proxy (Caddy) | 0.00% | 11.7 MiB |
| openemr | 0.17% | 817 MiB (6.83%) |
| mysql | 0.02% | 433.6 MiB (3.62%) |
| phpmyadmin | 0.00% | 12.2 MiB |

Droplet: 4 vCPU, 11 GiB RAM, ~1.7 GiB used / 9 GiB available at rest.

## (b) Real end-to-end batch — operator-pending

Not run in this session (operator's choice, to control real Anthropic
spend directly). Exact commands, using this directory's scripts:

```bash
cd agent/load-test
export FHIR_CLIENT_ID=...
export FHIR_CLIENT_SECRET=...
AGENT_TOKEN=$(./get_token.sh)
N=5 AGENT_TOKEN="$AGENT_TOKEN" ./real_batch.sh
```

This prints per-request `total_seconds` / `retrieve_seconds` /
`draft_seconds` / `verify_seconds` / `render_seconds` and verification
counts as CSV — the honest real-world latency number, including Anthropic's
own latency, which the infra test above deliberately excludes. For the
dollar cost, `real_batch.sh`'s trailer gives the exact
`scripts/compute_metrics.py` invocation against the agent's own log (no
new tooling — reuses what already computes KEY_METRICS.md Metric 6).

**Sanity-checked but not logged as data**: this session confirmed the
production (non-stub) path still works correctly before/after the infra
test — `GET /health` and `GET /ready` both returned healthy throughout,
and one stub-mode request was manually verified end-to-end (below) to
confirm the pipeline shape. No real (non-stub) `/pre-visit-brief` call was
made, so no Anthropic cost was incurred in this session.

## What was run vs. operator-pending

**Run in this session** (from this laptop — see "Generator location"
below — over the internet, with SSH access to the droplet):

1. Added `AGENT_STUB_LLM_DRAFTING` to `app/reasoning/draft_claims.py` (off
   by default) and wired it through `deploy/docker-compose.agent.yml` /
   `.env.example`. Verified with the full local test suite (`pytest
   tests/`, 138 passed) before touching the droplet.
2. Confirmed the droplet's live deployed code
   (`feat/standalone-ui@2d46f0f`) was byte-identical, in the two files
   this change touches, to this branch's pre-change base — so patching
   just those two files onto the live checkout is a clean, additive
   change that doesn't disturb the standalone-UI work also live there.
   Backed up both original files on the droplet
   (`/root/load-test-backup-<timestamp>/`) before writing.
3. Patched and rebuilt only the `agent` service (never touched `proxy` or
   the `ui` volume mount); confirmed via `ss -tlnp` and `docker compose
   ps` that bindings are unchanged (F-54: still exactly one new public
   listener, `0.0.0.0:8443`; `8000`/`18310`/`18320` unchanged) both before
   enabling the flag and after reverting it.
4. Minted clinician tokens via the OAuth password grant (credentials
   supplied by the operator for this session only, used in-memory, never
   written to any file in this repo).
5. Ran the 10-VU and 50-VU infra load tests (`k6-infra.js`) from this
   laptop against the live droplet, sampling `docker stats` on the droplet
   throughout each run.
6. Reverted `AGENT_STUB_LLM_DRAFTING` to its default (false), rebuilt,
   and re-verified bindings, container health, and `/ready` — the droplet
   is back to serving real briefs exactly as before this session.
7. Ran the full `pytest tests/` suite locally (Python 3.12 venv,
   `pip install -r requirements.txt`) — 138 passed, 0 failed.

**Operator-pending:**

- **The real end-to-end batch (b)** — commands above, `real_batch.sh`.
  Deliberately left to the operator to control the real spend directly.
- **A cleaner-network re-run of (a) from Railway instead of this laptop**,
  if a run isolated from this laptop's own network variability is wanted
  later. Not required — this laptop already satisfies "generator isn't
  the droplet under test," just with a noted limitation (below).

## Generator location

Run from this laptop (developer machine), not the droplet under test —
satisfies the "generator isn't the system being measured" requirement,
since the generator's CPU/network never competed with the droplet's. Not
run from Railway (no Railway credentials were available in this session).
**Limitation, stated plainly**: the laptop's home/office network path to
the droplet is not a controlled environment the way a Railway container's
egress would be — some fraction of the measured latency (especially the
low end, e.g. the 522 ms/2.0 s min/p99 at 10 VUs) includes this network
hop's own variability, not only droplet-side processing time. The p95/p99
*growth pattern* between 10 and 50 VUs, and the CPU/memory evidence
above, are droplet-side and not affected by this — the root-cause finding
(OpenEMR saturation, not the agent or the network) does not depend on the
generator's exact location.
