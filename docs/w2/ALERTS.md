# Week 2 alerts

Five rules. The numbers are defined once, in `agent/scripts/w2_dashboard.py`
(`ALERTS`); this page gives what each one means, what on-call does, and why
the threshold sits where it does. Every basis below is a number measured in
this repository, cited to where it was measured.

**How they run.** Evaluate every 5 minutes over the last 10 minutes of the
agent's stdout log:

```bash
docker logs --since 10m clinical-copilot-agent-agent-1 2>&1 \
  | python scripts/w2_dashboard.py --log-file - --alerts-only
```

Exit code 3 means at least one alert is firing. Each line is one alert as
JSON: `state` is `ok`, `firing` or `insufficient_data`.

**What is not built.** Nothing is scheduled and nothing pages anyone. The
rules and their evaluator exist and are tested (`tests/test_w2_dashboard.py`),
and they fired correctly on the demo load (`evidence/dashboard-demo.txt`).
Wiring the exit code to a pager (cron + a webhook, or the host's monitoring)
is a deployment step. The local Langfuse OSS instance has no alerting of its
own.

Every rule has a **minimum sample**. Below it the state is
`insufficient_data`, never `ok` and never `firing`: two failures out of
three requests is not an error rate.

| Alert | Fires when | Min n | Severity |
|---|---|---|---|
| `answer_p95_latency` | p95 of answer routes > **45 s** | 10 requests | page |
| `request_error_rate` | 5xx share of non-probe requests > **5%** | 20 requests | page |
| `tool_failure_rate` | any one tool's dependency-failure rate > **5%** | 20 calls of that tool | page |
| `text_layer_located_rate_collapse` | located / (located + unlocated) over **text-layer** PDFs < **85%** | 50 fields read | ticket |
| `verification_failure_spike` | share of drafted claims failing verification > **10%** | 20 claims | ticket |

---

## 1. `answer_p95_latency` — p95 of answer-route latency > 45 s

**Meaning.** Clinicians waiting for a brief, a follow-up, a conversation
turn or a Week 2 answer are waiting long enough to miss the gap between
patients. Answer routes only: `/pre-visit-brief`,
`/pre-visit-brief/follow-up`, `/pre-visit-brief/conversation` and `/w2/ask`. A status poll takes 1 ms and would drag a
blended p95 down.

**Threshold basis.**

- The budget is **90 s** between encounters (KEY_METRICS.md Metric 4,
  USERS.md §1–2). 45 s is half of it, so this fires while there is still time
  to act before the budget is breached.
- Normal is well under it. Real-LLM p95 is **6.4 s** at 10 concurrent turns
  on a 5-record chart (`LOAD_TEST.md` §2). On the largest local chart
  (36 conditions), a real brief took **18.8 s** and a real turn **15–16 s**
  (`evidence/api-collection-run.txt`). 45 s is 2.4× that worst normal case,
  so a big chart does not page.
- It does catch the failure mode seen locally. OpenEMR saturation at 50 users
  put the FHIR step alone at **28 s p95** (stubbed LLM). Add a real 4–18 s
  draft and that is 32–46 s.
- A first draft of this rule used 20 s. The real-LLM runs showed that would
  page on healthy traffic for large charts, so it was raised.

**On-call.**

1. Open the dashboard's by-step table. Is it `*.retrieve` (FHIR) or
   `*.draft` (LLM) that moved?
2. If retrieve: check OpenEMR CPU and memory (`docker stats`). The known
   ceiling is OpenEMR's PHP tier, around 10 concurrent users on a 4-vCPU host
   (`agent/load-test/RESULTS.md`). Shed load or scale OpenEMR; the agent is
   not the constraint.
3. If `supervisor.answer`: a `/w2/ask` over a whole lab report takes 90–130 s
   with no load at all (LOAD_TEST.md §6). One model call writes 11k+ tokens.
   That is a product fix, not an incident.
4. If draft: check the `llm` tool row for retries. Rising retries mean
   Anthropic is overloaded or rate limiting. Check provider status; nothing
   local fixes it.

## 2. `request_error_rate` — 5xx share of requests > 5%

**Meaning.** Clinicians are getting errors instead of answers. 4xx is
excluded: a rejected token or an unknown patient is the request's problem,
not the service's. Probes are excluded, so a load balancer cannot dilute the
rate.

**Threshold basis.**

- The healthy baseline is **0%**: 0/280 briefs at 10 VUs, 0/100 real turns,
  0/750 uploads (`LOAD_TEST.md`).
- The known saturation mode is **58%** at 50 VUs locally, and 56% on the
  Vultr host (Week 1).
- 5% is one clinician in twenty seeing an error, which is past noise when
  normal is zero. With a minimum of 20 requests, a single transient error
  cannot fire it.

**On-call.**

1. Split the 5xx by route on the dashboard.
2. `502 fhir_unavailable` means OpenEMR; go to alert 3's FHIR row.
3. `502 brief_generation_failed` means the LLM; check `/ready`. It turns
   `llm_provider: unhealthy, Recent model calls are failing` after
   consecutive failures (F-60, exhausted credit).
4. `500 internal_error` is the agent's own bug. Take the `correlation_id`
   from the error response and read that trace.

## 3. `tool_failure_rate` — one tool's dependency failures > 5%

**Meaning.** A dependency is failing: OpenEMR's FHIR API, Anthropic, the
guideline retriever or the write-back endpoint. A dependency failure is no
response at all, a 5xx or a 429 (`is_dependency_failure`). It is evaluated
per tool, so a failing write-back endpoint is not hidden by thousands of
healthy FHIR reads.

**Threshold basis.**

- One brief makes 4–6 FHIR reads, and one failed read fails the brief. A 5%
  per-call failure rate therefore already means roughly 20–25% of briefs
  failing. That is as loud as this alert should get before it pages, and the
  error-rate alert would follow it.
- Measured at saturation: **7.1%** of FHIR reads returned 500 across the
  local load runs (157 of 2,225), and **8.3%** in the demo burst. Healthy
  runs had 0.
- This alert names the dependency, where the error-rate alert does not.

**On-call.**

- `fhir_read`: OpenEMR (see alert 1, step 2).
- `llm`: provider status, key, credit; `/ready`'s `llm_provider`.
- `lab_writeback`: OpenEMR's module endpoint. Check its PHP error log; the
  ledger makes a retry safe (invariant 6).
- `guideline_retriever`: the agent process itself; check memory (see
  LOAD_TEST.md §4).

## 4. `text_layer_located_rate_collapse` — located rate < 85% on text-layer PDFs (Week 2)

**Meaning.** For documents with a real text layer, the deterministic locate
step (D3) is failing to find on the page the values the vision model read.
Each value it cannot find is shown to the clinician as `unsupported`
(invariant 4). So a collapse means documents that should be fully supported
suddenly mostly are not.

**Why text-layer only.**

- A heavy scan legitimately locates about a third of what the model reads
  (**34%**, 36/107, measured live; D24 records the recall cost). Averaging
  scans in would make this alert either always firing or blind.
- On a clean text layer there is no OCR noise. A drop there can only come
  from the model's transcription changing shape, or from a regression in the
  locate step. Both are silent failures nothing else catches: requests still
  succeed and verification of chart claims is unaffected.

**Threshold basis.**

- Measured with the real vision model: **98%** on the clean lab (102/104)
  and **92%** on the clean intake form (45/49)
  (`evidence/load/extraction-live-vision.json`).
- The pooled text-layer rate in the demo mix was **98.2%** over 954 fields.
- 85% sits below the worst clean document measured (92%) with margin, and far
  above what a real regression produces. With 50 fields as the minimum, one
  odd form cannot fire it.

**On-call (ticket, next business day).**

1. Did the vision model, prompt or schema change? Check
   `llm_usage_event.model` / `served_model`; a server-side fallback shows as
   `fallback_ran`.
2. Did `app/extraction/locate.py` or `normalize.py` change?
3. Re-run `tests/test_locate_measurement.py` and the extractor measurement
   against the committed samples.

Nothing is unsafe while it fires: unlocated values are already shown as
unsupported. It is a quality regression, not a safety one, hence a ticket.

## 5. `verification_failure_spike` — > 10% of drafted claims fail verification

**Meaning.** The model is drafting claims the deterministic verifier cannot
match to a retrieved record. KEY_METRICS.md Metric 1: "a retrieval or
prompting problem, not a verification failure".

**Threshold basis.**

- Measured normal is **100% pass**: 1,119/1,119 claims in the demo mix, and
  44/44 in the real-LLM encounter. Week 1's real batch was 5/5.
- 10% is far outside that, and still low enough to notice a prompt or model
  regression within one busy hour.
- Metric 2 (disclosure below 100%) is a separate absolute. It is already a
  same-day defect in KEY_METRICS.md, and the dashboard shows it.

**On-call (ticket).**

1. Which `resource_type` and which `unverified_reason` dominate the failed
   claims? Read them from the `claim_verified_event` lines.
2. A model or prompt change? Compare `served_model`.
3. A FHIR shape change? The F-57 class of defect: a field OpenEMR stopped
   sending.
4. Every failed claim was still shown as UNVERIFIED, so the patient-safety
   property holds while this fires.
