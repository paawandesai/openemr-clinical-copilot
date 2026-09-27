# Answer-step thinking: before and after

Evidence for **D62 (answer-step thinking)** (`docs/w2/DECISIONS.md`).
The raw rows are in `answer-step-runs.json`, `brief-runs.json` and
`conversation-runs.json`. They hold counts, statuses and critical-value flags
only; no chart content.

**The harness:** `agent/scripts/measure_answer_thinking.py`.

- It ran live on the local stack, on 2026-09-26 03:5x–04:1x UTC: real
  OpenEMR FHIR, real `claude-opus-5`, and patient `a2c373ec-…`.
- Each document is extracted once, through `DocumentStore.accept`.
- Every run is a new session over that same extraction.
- The configurations are **interleaved round by round**, so a provider-side
  swing lands on all of them alike.

## What happened on the host (2026-09-25)

The chart card's document question, "What were the results on the uploaded
lab report?", failed 3/3 in the card. The agent answered each time (200), but
in 54–58 s, and the module's ask proxy gives up at about 40 s.

- The answer call wrote 6.2–7.0k output tokens. D50 measured 368–461 on the
  same host on 2026-09-23.
- Every response was the same 88,079 bytes, so the extra tokens were thinking.
- **It was not the deploy.** The same question and document, against both
  images on the host:

  | Image | Run 1 | Run 2 | Run 3 |
  |---|---|---|---|
  | `ea198c8` (before) | 8.9 s, 638 tokens | 53.1 s, 6,115 | 56.3 s, 6,429 |
  | `06f0d45` (after) | 56.7 s, 6,594 | 62.7 s, 7,047 | 57.8 s, 6,647 |

  Input tokens (15,118), effort (`medium`) and installed packages are
  identical between the two images, and the answer code differs only by
  refactors.
- **The stdout dashboard caught it.** `w2_dashboard.py` over the host's log
  showed `answer_p95_latency` at **57.6 s against its 45 s threshold**, and
  reported `INSUFFICIENT_DATA` at n = 6/10: four more answers and it fires.
  (`docs/w2/evidence/prod-langfuse/deploy-2026-09-25/stdout-dashboard.png`, on branch `docs/w2-prod-langfuse-deploy`.)
- **By 23:41Z it had swung back.** The same question answered in about 8 s
  with 441 output tokens, still with no code change.

## Answer step (`/w2/ask`): 3 rounds × 2 questions × 3 configurations

The two questions:

- **whole-report:** "What were the results on the uploaded lab report?" over
  `lab-critical-potassium-007` (live vision, once). It carries the critical
  potassium.
- **glucose-uspstf:** `w2_e2e_ask.py`'s default question over `lab-clean-001`.

| Question | Configuration | Turn (s) | Output tokens | Verified claims | Unverified | Potassium stated |
|---|---|---|---|---|---|---|
| whole-report | adaptive, medium (**before**) | 12.2 / 11.1 / 11.2 | 1,066 / 1,105 / 1,094 | 3 / 3 / 3 | 0 / 0 / 0 | 3 / 3 |
| whole-report | **disabled, medium (after)** | 12.0 / 9.9 / 9.9 | 1,002 / 1,002 / 1,002 | 3 / 3 / 3 | 0 / 0 / 0 | 3 / 3 |
| whole-report | adaptive, low | 10.5 / 9.8 / 9.9 | 1,003 / 1,003 / 1,003 | 3 / 3 / 3 | 0 / 0 / 0 | 3 / 3 |
| glucose-uspstf | adaptive, medium (**before**) | 12.9 / 13.1 / 13.9 | 1,253 / 1,327 / 1,449 | 5 / 5 / 9 | 0 / 0 / 0 | — |
| glucose-uspstf | **disabled, medium (after)** | 12.8 / 11.3 / 11.2 | 1,134 / 1,134 / 1,134 | 6 / 6 / 6 | 0 / 0 / 0 | — |
| glucose-uspstf | adaptive, low | 9.3 / 8.9 / 10.2 | 873 / 873 / 980 | 5 / 5 / 5 | 0 / 0 / 0 | — |

For whole-report, the 3 verified claims carry 50 document citations. The
cited rows are the report, itemised by D50's rule.

- **Unverified claims:** 0 in every run, before and after.
- **Critical potassium:** stated in 9/9 whole-report runs.
- **Citations:** after the change, glucose-uspstf keeps its 48 document
  citations, with 3 guideline citations against 2 before.
- **Output tokens:**
  - With thinking disabled they are fixed, at 1,002 and 1,134. That is the
    answer itself, the same size every run.
  - Adaptive thinking, at either effort, leaves the size to the provider.
  - Today that provider-decided part was small: about 100–300 tokens. On
    2026-09-25 it was about 6,000.

## Brief and conversation turn: same exposure, same fix

Both paths ran adaptive thinking at medium effort, the same setting as the
answer step (`draft_claims.py`, `draft_follow_up.stream_structured`).

| Path | Configuration | Wall (s) | Output tokens | Claims passed | Claims failed |
|---|---|---|---|---|---|
| Pre-Visit Brief | adaptive, medium (before) | 15.2 / 14.8 / 16.8 | 1,710 ×3 | 24 ×3 | 0 |
| Pre-Visit Brief | **disabled, medium (after)** | 15.0 / 14.6 / 14.5 | 1,709 ×3 | 24 ×3 | 0 |
| Conversation turn | adaptive, medium (before) | 61.9 / 64.9 / 57.1 | 7,737 / 8,220 / 7,209 | 104 / 108 / 104 | 0 |
| Conversation turn | **disabled, medium (after)** | 58.9 / 58.0 / 58.0 | 7,542 / 7,536 / 7,536 | 114 ×3 | 0 |

**What the conversation turn shows:** it is slow for a different reason. The
question was "What medications is the patient on, and what were her latest
labs?", and its ~110 claims *are* the 7.5k tokens: thinking off, it still
takes 58 s. That path has no output bound, unlike D50's bound on the answer
step. This change does not address that, and it is recorded as open in the
D-entry.
