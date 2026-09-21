# Week 1 debt — triage for Week 2

Work order 04, branch `chore/w1-debt`, 2026-09-21. Each item carries a
**status** with the evidence it rests on, its **impact on Week 2**, and a
**decision**.

Status is what the code and the live host say today, not what an earlier
round reported. Where a stated debt item turned out to be already closed, or
to be a different defect than described, that is recorded as found rather
than smoothed over.

**Verification environment.** Live reads in this document were made against
the Vultr host `64.177.42.210` (`FHIR_BASE_URL`,
`https://64.177.42.210:9300/apis/default/fhir`) on 2026-09-21, with a
password-grant token for `physician`, GET only. 52 patients visible.

| # | Item | Status | Decision |
|---|---|---|---|
| 1 | Lab trim was silent to the model | **fixed this branch** | fix now — done |
| 2 | Abnormal-first ordering | **cannot be built — no data** | document |
| 3 | F-57 pattern beyond allergies | **fixed, and general** | document |
| 4 | No FHIR paging | **open, latent; `_count` is the sharp edge** | document + guard shipped |
| 5 | Refusal-fallback beta not HIPAA-listed | **open** | defer to pre-launch |
| 6 | Week 1 eval failures | **none open (43/43)** | document |
| 7 | PHP tier saturates ~10 users | **open, confirmed** | defer |
| 8 | CapabilityStatement over-reports | **open (upstream)** | document |
| 9 | `GET /api/procedure` 200 + HTML SQL error | **open (upstream)** | document |
| 10 | Document download 500s under bearer auth | **open (upstream)** | document — routed around |
| 11 | Week 1 OAuth client still enabled | **open — human action** | record only |

---

## 1. The lab trim was silent to the model — FIXED on this branch

**Status: fixed.** It was real, and it was not the trim itself — it was that
only the physician was told about it.

The trim lives in `agent/app/conversation/routing.py:narrow_observations`,
not in a serializer. Caps, all in that file:

| Constant | Value | What it cuts |
|---|---|---|
| `LAB_RESULTS_PER_CODE` | 8 | per LOINC code, for a question naming tests |
| `LAB_RESULTS_MAX` | 160 | total, for a question naming tests |
| `LAB_RESULTS_GENERAL` | 40 | total, for a general lab question |
| `ENCOUNTER_COUNT` | 10 | visit dates (applied as FHIR `_count`) |

Order of the cut: sort every retrieved lab result newest-first
(`_newest_first`, absent date sorts last); keep the 8 most recent per
requested code; truncate to 160; re-add any result an earlier turn verified,
so `"and the one before that?"` can still cite it. A general lab question
skips the per-code step and keeps the 40 most recent overall. Condition,
MedicationRequest and AllergyIntolerance are **never** trimmed — the brief's
serializer (`agent/app/reasoning/draft_claims.py:_serialize_retrieved`) has
no cap at all.

**Who exceeds it, on the current data** (52 patients, 14,848
`Observation?category=laboratory` entries, 10,852 of them modelled — i.e.
carrying both a `valueQuantity.value` and a LOINC code):

| Cap | Patients over it |
|---|---|
| `LAB_RESULTS_GENERAL` = 40 | **29 of 52** |
| `LAB_RESULTS_PER_CODE` = 8, on at least one code | **16 of 52** |
| `LAB_RESULTS_MAX` = 160 | **9 of 52** |

Worst cases, by modelled lab count: 2,203 (LOINC 2345-7 alone has 83
results, of which 8 are kept), 1,669 (33914-3: 97 results), 1,655, 1,014,
976. Dropped rows are **Observation only** — no Condition,
MedicationRequest or AllergyIntolerance row is ever cut by any cap.

**The defect.** The cut was disclosed to the physician in the answer's
closing line (`agent/app/graph/conversation.py:_lab_part`) and nowhere else.
The model's input held only the surviving records, so the prompt for a
patient with 8 creatinine results on file was byte-identical to the prompt
for a patient with 83 of which 8 were shown. The model sets `coverage`
(`full` / `partial` / `none`) and the `lab_results` gap from that input, so
it could — correctly, given what it was shown — draft `coverage: "full"` on a
question the turn could not possibly cover.

**Fixed by** adding a `<retrieval_scope>` block to the model input, built
from the same `LabScope` the physician-facing closing line is rendered from,
so the two cannot drift. What the model now receives:

```
Observation: 16 of 123 lab results on this patient's chart are included below, newest first.
107 further Observation records are omitted from this turn and you cannot see them.
LOINC 2160-0: the 8 most recent of 83 results are included; 75 older results are omitted.
LOINC 2345-7: the 8 most recent of 40 results are included; 32 older results are omitted.
Encounter: the 10 most recent visit dates are included. This patient has older visits that were not retrieved; how many is unknown.
```

An untrimmed turn says so explicitly rather than staying silent
(`Observation: 3 of 3 …`), because silence is ambiguous in exactly the
direction that caused the defect. `SYSTEM_PROMPT` now binds the block to
coverage: an answer needing omitted results is `partial` with the
`lab_results` gap, while `"the most recent"` stays `full` because the set is
newest-first. Counts only — no value, unit, date or `source_id` of an
omitted record enters the prompt (invariant 1), asserted by
`test_the_scope_block_carries_counts_only_and_no_chart_content`.

The **answer path already did not claim completeness**: `render_turn` opens
with `"From the records retrieved for this turn:"` and closes with
`"Answered only from what was retrieved for this turn: … Nothing else from
the chart was retrieved."` That wording was left alone; a test now pins the
physician's line and the model's block to the same numbers.

**Encounters are the honest exception.** `get_encounters` passes FHIR
`_count=10`, so the server never returns the true total — see item 4. The
block therefore says older visits exist but not how many, rather than
guessing.

**Impact on Week 2:** the supervisor and evidence-retriever will cut
candidate sets the same way. This establishes the rule they inherit: a set
handed to a model states what it leaves out, in counts, in the prompt.

---

## 2. Abnormal-first ordering — CANNOT BE BUILT, no data exists

**Status: not implementable, and not for want of code.** The work order asks
that "abnormal and most-recent results be kept first". Most-recent-first is
implemented and always was. **Abnormal-first has nothing to sort on.**

- F-19 (`docs/audit/findings.md:1694`): of 7,259 local `procedure_result`
  rows, **7,259 have a blank reference range and 7,259 a blank abnormal
  flag** — zero of either, though the schema supports both. 29,701 rows on
  the droplet, same result.
- Re-verified live for this triage, 2026-09-21, against Vultr: of **14,848**
  `Observation?category=laboratory` resources, **0 carry `interpretation`**
  and **0 carry `referenceRange`**.
- `Observation` (`agent/app/models/fhir_resources.py:202-217`) deliberately
  has no normal/abnormal field, with that reasoning recorded at the model.

So a clinically extreme value is ordered by date like any other and, if old
enough, falls outside the cap. What item 1 guarantees instead is that it is
never dropped *silently*: the count is stated, so neither the model nor the
physician is told the visible results are all of them. That is the available
guarantee, and it is weaker than the one asked for.
`test_an_outlying_value_beyond_the_cap_is_cut_but_the_cut_is_disclosed`
pins the behaviour and records why.

**Impact on Week 2:** ranking clinical salience needs a reference range, and
the only place one will exist is a range **the co-pilot itself extracts from
a lab PDF**. The D1 write-back chain stores one (`procedure_result.range` —
the spike's inserted row carries `ref 70–99`,
`docs/w2/spikes/writeback.md:257`). Abnormal-aware ordering becomes possible
for document-derived values, and stays impossible for the Synthea chart.

**Decision: document.** Do not add an abnormal flag to `Observation` to
satisfy the ordering requirement — a field the data cannot fill would imply
a characterization the system cannot support, which is the failure F-19 and
invariant 4 both guard against.

---

## 3. The F-57 fix is general, not allergy-specific — FIXED

**Status: fixed and general.** Checked in code, not inferred.
`_record_name(resource, codeable_concept)`
(`agent/app/fhir/client.py:291`) tries the coded display, then the resource
narrative, then `None`. It is called from **all three** brief types:

- `agent/app/fhir/client.py:421` — Condition
- `agent/app/fhir/client.py:452` — MedicationRequest
- `agent/app/fhir/client.py:490` — AllergyIntolerance

`unnamed_record_claims` (`agent/app/reasoning/draft_claims.py`) likewise
iterates `_BRIEF_RESOURCE_MODELS`, so a nameless record of any of the three
gets a `display: None` claim that renders UNVERIFIED rather than vanishing.

**The pattern is latent on the current data, not live.** Live counts,
2026-09-21, across all 52 patients:

| Type | Records | With a data-absent code | Name only in the narrative |
|---|---|---|---|
| Condition | 1,816 | 0 | 0 |
| MedicationRequest | 592 | 0 | 0 |
| AllergyIntolerance | 36 | 1 | 1 |

Only allergies currently exercise it — one record, Phil Belford's penicillin
allergy. The code would handle a Condition or MedicationRequest with the
same shape; no such record exists to prove it end to end.

**Impact on Week 2:** intake-form extraction produces exactly this shape —
a medication or allergy the document names in free text with no code. The
general path is the one D2's discrepancy surface will lean on.

**Decision: document.** No code change. Note under UNVERIFIED that the
Condition/MedicationRequest branch is untested against real data.

---

## 4. No FHIR paging — OPEN, and `_count` is the sharper edge

**Status: open, currently harmless, and the real hazard is not what the item
says.** `_get_bundle` (`agent/app/fhir/client.py:91`) reads
`bundle["entry"]` and never looks at `bundle["link"]`. There is no
`next`-following anywhere in the client.

Measured live, 2026-09-21, on the largest chart
(`a2c3740d-…`, 3,226 lab entries):

```
entries returned : 3226
bundle.total     : 3226
link relations   : ['self']          <- no 'next'
with _count=50   : entries=50  total=50  links=['self']
```

Two facts, and the second matters more:

1. **OpenEMR does not paginate on its own.** It returned all 3,226 entries
   in one bundle with only a `self` link. So today nothing is silently
   omitted by the absence of paging.
2. **`_count` truncates and then lies about it.** With `_count=50` the
   server reports `total: 50` — not 3,226 — and still emits **no `next`
   link**. A client cannot detect the truncation or recover the rest.

`get_encounters` passes `_count=10`. So the encounter set is genuinely
truncated for any patient with more than 10 visits (most of this
population — the load-test outlier has 234), and the client **cannot learn
how many were left out**. That is why item 1's `<retrieval_scope>` says
"how many is unknown" for Encounter instead of a number: the honest
statement is the one the API can support.

The latent risk: if any future OpenEMR build or deployment sets a default
page size, every read in this client silently truncates, `total` agrees with
the truncation, and no `next` link exists to follow. Nothing would surface
it.

**Impact on Week 2:** hybrid RAG over guideline evidence will read larger
result sets than the brief does. Any retriever built on `_get_bundle`
inherits the blind spot.

**Decision: document; partial guard already shipped.** The `<retrieval_scope>`
block makes Encounter truncation visible to the model this week. Real paging
— follow `link[relation=next]`, and treat a bundle whose `entry` count
equals a requested `_count` as "possibly truncated" — is a Week 2 change to
the FHIR client, not a Week 1 debt fix. Do not add `_count` to any new call
site until it lands.

---

## 5. Refusal-fallback beta is not HIPAA-listed — OPEN

**Status: open, and broader than "the fallback model is not eligible".**
`draft_claims.py:88` sets `_FALLBACK_BETA = "server-side-fallback-2026-07-01"`
and both drafting paths pass `betas=[_FALLBACK_BETA], fallbacks="default"`
(`draft_claims.py:434-435`, `draft_follow_up.py:267-268`).

`docs/COST_ANALYSIS.md:819` records the finding precisely: the beta is **not
listed** as HIPAA-eligible, and "features in beta are generally not covered
under the BAA unless explicitly listed." The consequence is not a degraded
fallback — **a HIPAA organization returns 400 on a non-eligible feature,
which would fail every brief and every conversation turn**, not just the
ones that trigger a refusal.

`MODEL = "claude-opus-5"` itself is not the problem; the beta header is.

**Impact on Week 2:** none this week (synthetic data, non-HIPAA org). It is
a launch blocker, and it blocks the whole drafting path rather than an edge
case.

**Decision: defer, with the scope corrected.** Confirm the beta's BAA status
inside the HIPAA organization before launch, per
`docs/COST_ANALYSIS.md:890`. Do not remove the beta as a precaution — a
mid-stream refusal with no fallback is a hard failure on the golden path,
and `_final_text` already handles the fallback block correctly.

---

## 6. Week 1 eval failures — NONE OPEN

**Status: closed.** Both suites, run 2026-09-21 at code `43fa8db`:

- `agent/evals/RESULTS.md`: **PASS 31, FAIL 0, SKIP 0, ERROR 0 of 31**
- `agent/evals/RESULTS_CONVERSATION.md`: **PASS 12, FAIL 0, SKIP 0, ERROR 0
  of 12**

43 of 43, matching `c1900cf test(agent): full suite re-run on Vultr — 43 of
43 pass`. `43fa8db` is an ancestor of this branch's HEAD, and every commit
since touches only gate hooks, docs and tooling — no agent code — so the
result still described the shipped code before this branch.

This branch does change agent code, so the 12 I cases were re-run (see the
session report): **PASS 12, FAIL 0, SKIP 0, ERROR 0**, no regression.

One caveat worth carrying: **D6 reports SKIP, never PASS**, because OpenEMR
has no revocation endpoint and an expired token cannot be made on demand
(`agent/evals/README.md`). The 43/43 figure does not include a live
expired-token check.

**Impact on Week 2:** the Week 1 suite is a working regression net for the
paths W2 builds on. It is live and costs model calls, so it is not the CI
gate — D6 in `DECISIONS.md` already settles that the W2 gate runs on
recorded fixtures.

**Decision: document.** Nothing to fix.

---

## 7. OpenEMR PHP tier saturates around 10 concurrent users — OPEN

**Status: open, confirmed, and it is OpenEMR's tier, not the agent's.**
From `agent/load-test/RESULTS.md`:

| Concurrency | Requests | Error rate | p50 | p95 | Throughput |
|---|---|---|---|---|---|
| 10 VUs, 30s | 79 | **0.00%** | 2.98 s | 4.03 s | 2.39 req/s |
| 50 VUs, 30s | 123 | **56.1%** (69/123) | 13.03 s | 21.59 s | 3.15 req/s |

OpenEMR's PHP-FPM pinned **roughly all four cores at 10 VUs already**
(396% CPU) and grew to 4.94 GiB resident at 50 VUs. Host memory went from
1.98 GiB at rest to 6.16 GiB at 50 VUs with 1.08 GiB left. The agent has
substantial untapped headroom; the PHP tier is the ceiling. Under saturation
**OpenEMR can reject a valid token**, which is the failure mode that matters
— it surfaces as an auth error, not as a timeout.

**Impact on Week 2:** document ingestion adds OpenEMR write traffic through
the new D1 endpoint, on the same PHP tier that is already the bottleneck at
10 users, and extraction at upload time (D7) makes bursts likelier. The
write path will contend with reads.

**Decision: defer.** Capacity work is not in this branch's scope. Record two
constraints for the D1 implementer: the endpoint must not be load-tested at
50 VUs against this host, and a 401 under load may mean saturation rather
than a bad token, so retry logic must not treat it as terminal.

---

## 8. The CapabilityStatement advertises DocumentReference `create` — OPEN

**Status: open, upstream, confirmed by the spike.**
`GET /fhir/metadata` advertises `interactions=['search-type', 'create',
'read']` for DocumentReference, and `POST /fhir/DocumentReference` returns
**404 `{"message":"Route not found"}`** — the only POST route under that
path is the `$docref` read operation
(`docs/w2/spikes/writeback.md:83-111`).

**Impact on Week 2:** direct. It is the trap D1 was written to avoid; a
session that trusted the CapabilityStatement would have built document
write-back on a route that does not exist.

**Decision: document.** Already reflected in D1 — documents go through
`POST /api/patient/:pid/document`. Carry the general rule: on this build,
"supported" means observed, never advertised.

---

## 9. `GET /api/procedure` returns 200 with an HTML SQL error — OPEN

**Status: open, upstream.** `docs/w2/spikes/writeback.md:57` records it
returning **HTTP 200 with an HTML SQL-error page** — the worst shape for a
client, because status-code checking passes and JSON parsing fails on markup.

**Impact on Week 2:** the lab-value read-back path must not use it. D1 reads
results back as `Observation`, not via `/api/procedure`.

**Decision: document.** Any new client code must check content type, not
only status, against this build.

---

## 10. Document download 500s under bearer auth — OPEN, routed around

**Status: open, upstream, already designed around.**
`GET /api/patient/:pid/document/:did` returns **HTTP 500**
(`docs/w2/spikes/writeback.md:55, 171-190`), on what the spike attributes to
the CSRF/session path rather than the token.

**Impact on Week 2:** would have broken PDF preview.

**Decision: document.** D1 already settles it: read documents back via
`GET /fhir/Binary/:id`, which also serves the preview. `writeback.md:535`
states the rule as "never read documents back via
`/api/patient/:pid/document/:did`".

---

## 11. The Week 1 OAuth client is still enabled — OPEN, human action only

**Status: open.** The Week 1 read-only OAuth client remains registered and
enabled on the Vultr host, with the password grant enabled. This session
minted a working token against it with `physician` / `physician` — F-32's
username-as-password demo account (`docs/audit/findings.md:447`, severity
High, exposure ongoing), which is also still live.

Recorded, not touched: the work order puts OAuth client changes out of
scope, and D1 notes that creating the **second** client for the write scope
is a human step because scopes are immutable per client.

**Impact on Week 2:** the write-back client must be a separate registration.
The read-only client's continued existence is not a blocker for it.

**Decision: record only.** Two human actions, neither an agent action:
disable or rotate the Week 1 grading client when it is no longer needed, and
change the F-32 demo-account passwords. Both are outside any implementation
session.

---

## What this triage did not settle

- Whether the Condition/MedicationRequest narrative fallback (item 3) works
  against a real uncoded record — no such record exists on this host.
- The true encounter count for any patient (item 4) — the API cannot report
  it while `_count` is set.
- Whether real paging is needed at all, which depends on a deployment
  decision about page size rather than on this code.
