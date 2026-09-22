# Week 2 — Decisions

**Append-only.** Never edit or delete an existing entry. To change a decision,
append a new entry that supersedes it by ID and set the old entry's status to
`SUPERSEDED by Dn` on its status line only.

Statuses: `DECIDED` (settled; do not re-open, raise conflicts in your session
report) · `OPEN` (not settled; propose, do not assume) · `PENDING-SPIKE`
(blocked on a named spike) · `SUPERSEDED by Dn`.

---

## D1 — Write-back path for documents and lab values

**Status:** SUPERSEDED (see D1 superseding, :162)
**Date:** 2026-09-21

**Decision:** Deferred. How extracted documents and lab values are written back
into OpenEMR — FHIR `DocumentReference`/`Observation`, the legacy documents
table, or a side table the co-pilot owns — is not settled and must not be
guessed at by an implementation session.

**Rationale:** The three candidate paths have different authorization models,
different audit-trail behaviour and different failure modes on retry, and the
audit found OpenEMR's authorization layer has no concept of "this patient"
(`docs/audit/findings.md` F-50). Picking wrong is expensive to unwind once
documents exist in the chart. Resolve it with evidence, not preference.

**Blocked on:** `docs/w2/spikes/writeback.md` (work order:
`docs/w2/work-orders/00-writeback-spike.md`).

**Constraint that holds regardless of outcome:** every write carries an
idempotency key (CLAUDE.md invariant 6).

---

## D2 — Provenance on persisted values; intake data is never auto-merged

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** Lab values are persisted with provenance pointing at the source
document — document id, page, and the located region on that page. Intake-form
medications and allergies are stored as **document-derived facts**, in their
own space, and surfaced to the clinician as **discrepancies against the chart**.
They are never merged into the patient's medication or allergy list, not even
when they match, and not behind a confidence threshold.

**Rationale:** Silently overwriting a medication list from a front-desk form is
a patient-safety failure. A form is what the patient remembered in a waiting
room; the chart is what a clinician recorded. The co-pilot's job is to show the
clinician where the two disagree, not to pick a winner. Provenance on lab values
exists so that any number the co-pilot shows can be traced back to the pixels it
came from.

---

## D3 — Extraction: text layer + vision LLM + deterministic locate

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** Document extraction is three stages:

1. **Text layer with word boxes** — PyMuPDF for digital PDFs, OCR for scans.
   Produces per-word text plus bounding boxes.
2. **Vision LLM extraction into a strict schema** — the model reads the page
   image and emits schema-conformant fields. It does not emit coordinates.
3. **Deterministic locate step** — code matches each extracted value back to
   the word boxes from stage 1. A value that cannot be located on its source
   page is flagged `unsupported`.

**The locate step is the verifier, and it is the only source of `bbox`.** The
model never supplies a bounding box, and a value the locate step cannot find is
never presented as fact.

**Rationale:** This is the Week 1 verification pattern applied to documents: the
model drafts, deterministic code confirms against the source, and what cannot be
confirmed is disclosed rather than dropped (see
`agent/app/verification/checker.py`, `docs/ARCHITECTURE.md` §5). A model-supplied
bounding box is an assertion about the source, not evidence from it.

---

## D4 — Supervisor routing is mostly deterministic and always logged

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** Supervisor routing is deterministic — explicit rules over request
shape, available artifacts and prior state. **Every routing decision is logged
with its reason.** At most one small LLM intent-classification step is allowed,
and only where deterministic rules genuinely cannot separate intents.

**Rationale:** A routing layer that cannot explain why it chose a path is not
debuggable, and an LLM-routed supervisor turns every eval failure into an
unbounded search. Deterministic routing also keeps routing out of the PHI
surface: rules read shape, not chart content.

---

## D5 — Embeddings and reranker

**Status:** OPEN
**Date:** 2026-09-21

**Open question:** Local embeddings vs. a hosted embedding API, and Cohere
rerank vs. a local cross-encoder. Not settled — propose with evidence
(retrieval quality on the eval cases, latency, cost), do not assume.

**Hard constraint, regardless of choice:** **no patient identifiers in retrieval
queries sent to third parties.** A query leaving this system carries clinical
concepts, never a name, MRN, date of birth, or any other identifier. This
constraint also applies to any local-vs-hosted comparison run during evaluation.

---

## D6 — Eval gate composition and failure threshold

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** The eval gate is split by what can be checked mechanically:

- **Deterministic checks** (no model): `schema_valid`, `citation_present`,
  `no_phi_in_logs`.
- **LLM judge, pinned model at temperature 0**: `factually_consistent` and
  `safe_refusal` only.
- **Recorded fixtures, not live OpenEMR.** The gate does not depend on a
  reachable FHIR server or on the state of a demo patient population.
- **Failure condition:** the gate fails on a **>5% regression in any category
  against the committed baseline**, or on any category falling **below its
  floor**.

**Rationale:** A judge asked to check schema validity is a slower, less reliable
`json.loads`. Reserving the model for the two judgements that genuinely need
reading comprehension keeps the gate fast, cheap and reproducible. Recorded
fixtures are what make a CI gate a gate rather than a flaky dependency on the
Week 1 live suite (`agent/evals/README.md` documents how much of that suite
SKIPs without credentials and a live host).

---

## D7 — Extraction runs at upload time

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** Document extraction runs **asynchronously at upload time**, not at
question time. A question asked against a document that has not finished
extracting gets an explicit "still processing" state — never a synchronous wait
and never a partial extraction presented as complete.

**Rationale:** OCR plus vision extraction is far outside the latency budget for
an interactive answer (`docs/KEY_METRICS.md`), and doing it per question repeats
the same cost for every question about the same page. Extracting once at upload
also means the locate step's output is a stable artifact that evals can be
recorded against.

---

## D1 (superseding) — Write-back path for documents and lab values

**Status:** DECIDED — supersedes the original D1 above, whose body is left
unedited for the record; only its status line was flipped to `SUPERSEDED`, as
the header of this file prescribes.
**Date:** 2026-09-21
**Evidence:** [`docs/w2/spikes/writeback.md`](spikes/writeback.md). The draft
work order that entry pointed at was not the one executed; it is marked
SUPERSEDED at `docs/w2/work-orders/00-writeback-spike.md`.

**Decision:**

- **Documents** are stored via `POST /api/patient/:pid/document` and read back
  via `GET /fhir/Binary/:id`. `GET /fhir/Binary/:id` is also the source for the
  PDF preview. The standard-API download endpoint 500s under bearer auth — do
  not use it.

- **Lab values have no API write path.** The spike established this directly:
  every FHIR write attempt 404s, and no Observation write scope exists to grant.
  So: **implement a custom endpoint in the OpenEMR fork**, protected by a new
  custom OAuth scope, that stores the document and writes the
  `procedure_order` → `procedure_order_code` → `procedure_report` →
  `procedure_result` chain in one transaction through OpenEMR's own DB layer,
  and **returns real ids**. Returning ids is part of the decision, not a nicety:
  it fixes the upload-returns-`true`, no-id race that the standard document
  endpoint forces.

- **Idempotency** is a ledger table in the fork with a UNIQUE key over
  (document sha3-512, test code, collection date). Document-level dedup uses
  OpenEMR's own stored hash, checked before insert.

- **Provenance:** `procedure_result.comments` carries an **extraction id only**
  — it surfaces as `Observation.note[0].text`. The ledger maps that extraction
  id to document id, page and bbox. `derivedFrom` is not emitted by OpenEMR, so
  it cannot carry the link.

- **Scope this week:** `Observation` only. `DiagnosticReport` is out — the spike
  could not get it to return results.

**Rejected, and why:**

- **Direct SQL from the agent sidecar.** Puts database write credentials in the
  agent, likely bypasses OpenEMR's audit logging, and is a bolt-on rather than
  an integration.
- **An agent-only store.** Fails the round-trip requirement. Kept as a fallback
  only.

**Required verification before anything is built on this**, by the implementing
branch: results written through the new endpoint **render correctly in
OpenEMR's Procedure Results UI** and **appear in the audit log**. Until both are
observed, treat the endpoint as unproven.

**Human step, not an agent step:** a **second OAuth client is required** —
scopes are immutable per client, so the existing read-only client cannot be
extended. The human creates it manually, after the custom scope exists.

---

## D8 — Week 2 eval tree lives under agent/evals/w2/, with a staging step

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** The Week 2 eval tree is `agent/evals/w2/`, beside the Week 1
suite rather than at the repository root:

```
agent/evals/w2/rubrics/        gated
agent/evals/w2/cases/          gated
agent/evals/w2/baseline.json   gated
agent/evals/w2/judge_config.*  gated
agent/evals/w2/staging/        NOT gated
agent/evals/w2/factory/        NOT gated
```

Generators and the case factory write to `staging/`. **Promotion from
`staging/` into `cases/` requires a `W2_GATE_SESSION=1` session**, i.e. a
human. The Week 1 suite stays exactly where it is, untouched.

**Rationale:** The evals import from `app/`, so they belong in the same Python
root as the code they measure; a repo-root `evals/` in an OpenEMR fork also
reads as OpenEMR's, which it is not. The staging split exists because an
implementation session must be able to generate cases freely — that is the
work — while still being unable to quietly add, weaken or delete the cases its
own branch is scored against. Without it, "gate files are read-only" would
block case generation outright, and the pressure to relax the rule would win.

**Enforcement:** `.claude/hooks/gate-guard.sh` (edit time) and
`scripts/gate-diff.sh` (pre-merge), which reads the same glob list through
`gate-guard.sh --check-path` rather than keeping a copy.

**Known migration:** `feat/doc-factory` writes to `agent/evals/factory/` — 40
files, verified with `git diff --name-only main feat/doc-factory`. (The
amendment that prompted this entry described it as a repo-root `evals/factory/`;
that directory does not exist on that branch. Corrected here as a statement of
fact; the decision above is unchanged.) `agent/evals/factory/` is gated, since
`agent/evals/*` is. It relocates to the ungated `agent/evals/w2/factory/` when
it merges.

---

## D9 — Week 2 contracts live in `agent/app/models/w2/`

**Status:** DECIDED
**Date:** 2026-09-21
**Work order:** `docs/w2/work-orders/03-w2-contracts.md`

**Decision:** The Week 2 contract models are a `w2/` subpackage under
`agent/app/models/`, beside Week 1's flat `claims.py`, `api.py`,
`conversation.py`, `fhir_resources.py`, `follow_up.py` and
`metrics_events.py`. Nothing in Week 1 moves. Every model inherits
`app/models/w2/base.py:W2Contract`, which is
`ConfigDict(extra="forbid", strict=True, frozen=True)` — Week 1's own
`_Contract` rule from `app/models/api.py`, with `frozen` now load-bearing
rather than merely tidy.

**Rationale:** "Put it where Week 1 contracts live" is `app/models/`; a
subpackage because there are eleven modules against Week 1's six, and
interleaving them would make it unclear which contract a Phase 3 branch is
supposed to build against. `frozen=True` matters more here than it did in
Week 1: a mutable `ExtractedField` could be moved from `unlocated` to
`located` after validation, without ever passing the validator that
requires a bbox, which would defeat invariant 4.

**Consequence:** contracts hold no I/O, no model calls and no FastAPI. The
only executable code in the package is deterministic and pure — the
abnormal-flag rule, the idempotency-key composition and the doc-factory
adapter — because each is a place where two branches would otherwise invent
their own version and disagree in production.

---

## D10 — A lab value is a tagged union, not `float | str`

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `LabValue` is a discriminated union of `NumericLabValue`
(a float plus an optional `Comparator`: `<`, `<=`, `>`, `>=`) and
`QualitativeLabValue` (text). The discriminator is `kind`.

**Rationale:** A result is a number, a censored number (`<5`), or a term
(`positive`). `float | str` holds all three and tells a consumer nothing:
`"<5"` arrives as a string and every consumer that wants to compare it to a
reference range re-parses it, each getting the censoring wrong differently.
Under `strict=True` a `float | str` union also silently accepts `"89"` as a
qualitative value when the extractor forgets to parse. FHIR draws the line
in the same place (`valueQuantity.comparator` versus `valueString`), which
matters because a written-back result lands in `procedure_result` and comes
back out as an Observation (D1).

---

## D11 — Censored lab values are compared as intervals and refuse to guess

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `compute_abnormal_flag` turns a value into an interval and
compares containment against the reference interval:

- value interval entirely inside `[low, high]` -> `normal`
- entirely above `high` -> `high`; entirely below `low` -> `low`
- straddling a bound -> `indeterminate`

`AbnormalFlag` therefore has five members, not four. Two adjustments keep
`indeterminate` from over-firing: an absent reference bound constrains
nothing (`89` against `< 200` is normal), and a reference range whose floor
is at or above zero describes a non-negative quantity, so `<0.01` against
`0.00 - 0.04` is `[0, 0.01)` and normal — while a range that admits
negatives (a base excess of `-2 - 2`) gets no such assumption.
`openemr_abnormal_code()` maps the four decidable flags onto OpenEMR's
`proc_res_abnormal` list (`no | yes | high | low`) and **raises on
`indeterminate`**.

**Rationale:** `<5` does not mean 5. A result of `<5` against a range of
`0 - 3` genuinely does not determine whether the patient is in range, and
the contract says so rather than picking the reassuring answer.
`procedure_result.abnormal` has no value meaning "we could not tell", and
writing `no` would put a false normal in a chart.

---

## D12 — A blank flag column means `normal`; an unknown glyph means nothing

**Status:** SUPERSEDED by D20
**Date:** 2026-09-21

**Decision:** `parse_reported_flag("")` is `AbnormalFlag.NORMAL`.
`parse_reported_flag("?")` is `None`. A `FlagCrossCheck` treats a computed
flag that disagrees with a reported one as a **verification failure**, and
treats `not_reported` (unknown glyph) and `not_computable` (indeterminate
computation) as neither agreement nor failure.

**Rationale:** Lab reports print a blank flag on in-range rows — the
doc-factory does exactly this — so a blank column is an assertion of
normality and must be cross-checked like any other. An unrecognised glyph
is a different failure: the document asserted something we cannot read, and
reading it as agreement would invent a confirmation. A check that cried
wolf on correct data (a censored value against an overlapping range) would
be switched off, which is why `not_computable` is not a failure.

---

## D13 — Locate-status vocabulary, and where "located requires bbox" is enforced

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `FieldStatus` is `located | unlocated | missing | illegible`.
Invariant 4's `unsupported` is the *outcome* for the latter three and is
carried by `UnsupportedReason` (`not_located_on_page`,
`not_present_in_document`, `illegible_on_page`). Only `located` is
presentable; the only route from an `ExtractedField` to a non-optional
value is `as_presented_fact()`, which returns `None` for everything else,
and `PresentedFact` re-validates the invariant so it cannot be forged by
hand.

"A document citation for a located value requires bbox" is enforced on
`ExtractedField`, **not** on `Citation`. `Citation` additionally offers
`located_document()`, a constructor with no bbox default.

`UnsupportedField` has no field called `value`; what the model read is
`unverified_value`, populated for `not_located_on_page` only.

**Rationale:** Locatedness is a property of the extracted field, not of the
citation — the same `Citation` shape describes the ink region of an
illegible field, which has a bbox and is emphatically not a located value.
Putting the rule on `Citation` would either reject that legitimate case or
be unenforceable. The status names the cause rather than the outcome
because the three non-fact statuses are presented the same way and debugged
completely differently.

---

## D14 — A chart citation's `page_or_section` is its FHIR resource type

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** For `source_type=chart`, `Citation.page_or_section` holds one
of the five tool-scope resource types (ARCHITECTURE.md §3), validated
against that closed set. `citation_from_claim` / `claim_from_citation`
round-trip a Week 1 `Claim` through `Citation` losslessly, including
`value=None` (findings.md F-57). `quote_or_value` is typed
`str | bool | int | float | None` and may be `None` only for chart
citations. `claim_from_citation` refuses a boolean, because `bool` is an
`int` subclass and would otherwise become 0/1 silently.

**Rationale:** Invariant 2 fixes the citation at five fields plus `bbox`
and `extraction_id`. A sixth field for `resource_type` would break that;
`page_or_section` is honestly "which part of the source this came from",
and for a chart that is the resource type. Week 1's `ClaimOutcome` maps one
way only: its `verified` / `unverified_reason` are verification state, and
a citation that could assert its own verification is exactly what
ARCHITECTURE.md §5 keeps in a separate step.

---

## D15 — The lab extraction id is a pure function of the idempotency key

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `lab_result_idempotency_key(document_sha3_512, test_code,
collection_date)` composes D1's UNIQUE key canonically as
`<digest lowercased>:<test code stripped and upper-cased>:<YYYY-MM-DD>`.
`lab_result_extraction_id(...)` is `w2x-` plus the first 128 bits of
`sha3-256` of that key string, and is what goes in
`procedure_result.comments`. `document_extraction_id(document_sha3_512,
extractor_version)` is the analogous id for an extraction *run*, and is
what a document citation's `extraction_id` carries.

**Rationale:** D1 gives the ledger a UNIQUE key over three things and gives
`procedure_result.comments` an extraction id. Left independent those are
two identifiers for one row and a class of bug where they disagree.
Deriving one from the other means the duplicate the ledger refuses is the
same duplicate already visible in `Observation.note[0].text` — which is the
only place a client can match it, since the Observation search has no
`note`, `_text` or `identifier` parameter (spike §7). The id is short and
opaque because spike §10.3 requires that field to be "parseable but not
alarming" in a chart; the raw 128-character digest is neither. The UNIQUE
constraint, not the id, is what enforces idempotency.

---

## D16 — SHA-256 and SHA3-512 are separate fields and are never compared

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `DocumentChecksums` carries `sha3_512` and `sha256` as
distinct, separately-patterned, individually-optional fields, with at least
one required. `document_idempotency_key()` reads only `sha3_512` and raises
on anything that is not 128 hex characters.

**Rationale:** OpenEMR hashes with SHA3-512 and that digest is D1's
document-level dedup key; the doc-factory records a SHA-256 in every ground
truth. One field holding "the hash" eventually means comparing a SHA-256 to
a SHA3-512 and concluding a document is new, which would write a duplicate
chain into a chart.

---

## D17 — A single claim draws on one source type

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `FinalAnswer` keeps `patient_facts` and `guideline_evidence`
in separate lists. `GuidelineClaim` accepts `guideline` citations only.
`PatientFactClaim` accepts `chart` and `document` citations — and all of a
single claim's citations must share one of those two. Unsupported values
ride along in a third list that nothing counts as a claim.

**Rationale:** The work order requires that a claim cannot mix patient
facts with guideline evidence (invariant 3). This goes one step further and
forbids mixing chart with document inside one claim, because a sentence
supported partly by the chart and partly by an uploaded PDF reads as one
fact with one provenance — and D2 is exactly about the intake list and the
chart list never blurring together. Two sources means two claims, or a
`Discrepancy` when the point is that they differ.

---

## D18 — The doc-factory adapter reads `rendered_text`, never `value`

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** `app/models/w2/factory_mapping.py` derives every
`ExtractedField.value` from a ground-truth record's `rendered_text`. The
record's `value` — which for `absent` and `illegible` fields is the chart
truth the document fails to convey — is returned separately as
`GroundTruthConversion.chart_truth`. Ground-truth `present` maps to
`located`, `absent` to `missing`, `illegible` to `illegible`; `unlocated`
has no ground-truth counterpart because it is a runtime conclusion of the
D3 locate step, not a property of a document.

The adapter reports anything it cannot place in `unmapped_field_paths`
rather than dropping it; for the six committed samples that list is empty.
The mapping table is exported to `docs/w2/factory-field-mapping.md` by
`agent/scripts/export_w2_schemas.py`, and both directions are listed.

**Rationale:** Copying `value` across would present chart truth as document
content — invariant 4 broken by the adapter, silently, on precisely the
cases the factory was built to catch (`intake-missing-allergy-006`'s own
expected discrepancy is "An extractor must report it as absent, not infer
it"). Three `present` fields also legitimately draw less than the chart
holds (`lab.name` upper-cased, `lab.director` with its label,
`medications[i].name` as the common name); those are the page being the
page, and the difference is recorded in `chart_truth`.

---

## D19 — Week 2 contract files are ruff-clean, including `StrEnum` and PEP 695

**Status:** DECIDED
**Date:** 2026-09-21

**Decision:** New Week 2 files pass `ruff format --check` and `ruff check`
with no findings, which required `enum.StrEnum` instead of `(str, Enum)`
(UP042) and PEP 695 type parameters instead of `Generic[T]` (UP046). Week 1
files are untouched and keep their existing baseline findings.

**Rationale:** Work order 03 makes "ruff clean on new files" an acceptance
criterion, and CLAUDE.md is explicit that reformatting Week 1 is its own
commit. Both migrations were verified to be behaviour-preserving under
`strict=True`: a plain string is still rejected where an enum member is
required, and a parametrised generic still enforces its type argument.
Every enum interpolation in Week 2 code already uses `.value`, so
`StrEnum`'s different `__str__` changes no message.

**Consequence:** Week 2 and Week 1 differ in enum idiom until the Week 1
reformatting commit lands. That is visible and intentional, not drift.

---

## D20 — A blank flag column reports nothing; it never means `normal`

**Status:** DECIDED — supersedes D12, whose body is left unedited for the
record; only its status line was flipped, as the header of this file
prescribes.
**Date:** 2026-09-21
**Raised by:** review of work order 03.

**Decision:** `parse_reported_flag` returns a `ReportedFlag`, a closed set
that includes the two ways a flag column says nothing comparable:

- `not_reported` — the column is blank. **This is what D12 got wrong.**
- `unrecognised` — the column carries a glyph not in the table.
- `normal | high | low | abnormal` — a flag the laboratory actually printed.
  `N` and `WNL` are `normal`: those are printed assertions.

`cross_check_abnormal_flag` compares **only** when the reported flag is one
of the four comparable members. Otherwise the computed flag stands alone and
the outcome is `not_reported` — never `agree`. `FlagCrossCheck.reported` is
a `ReportedFlag` and is no longer nullable, so "nothing was reported" is a
value rather than the absence of one, and the model validator rejects an
`agree` outcome over a non-comparable reported flag. The cause stays
distinguishable in a log line: same outcome, different `reported`.

`LabResult.reported_flag` is `ExtractedField[ReportedFlag]`, and
`LabResult.cross_check_flag()` now reads it through `as_presented_fact()`
rather than off `.value`: a flag the locate step could not place is not
something the document reported (invariant 4), and letting it into a
verification comparison would allow an unlocated value to confirm a fact.

**Rationale, and why D12 was wrong.** D12 reasoned that a lab report prints
a blank flag on in-range rows, so a blank column asserts normality. The
premise is true and the conclusion does not follow: the *report* leaves it
blank, but what this contract observes is an extractor's reading of a
column, and a blank reading has two causes that D12 could not tell apart —
the row is normal, or the flag was not found.

That matters in the direction a safety check must not fail. An extractor
that loses the flag column entirely — an unfamiliar layout, a scan where
the column is illegible — yields blanks on every row. Under D12 every one
of those became a printed `normal` and scored as agreement with the
computed flag. The check would have been loudest, and most reassuring,
exactly when the misread was total. It would also have recorded agreement
with a flag no laboratory ever printed, which is manufactured evidence in a
contract whose whole purpose is that claims trace to a source.

The cost is that the cross-check now compares far less often — across all
six doc-factory samples it compares on three rows, the three deliberately
abnormal ones, and reports `not_reported` on the rest. That is the honest
count. A check that ran on every row by inventing an observation for it was
not doing more work; it was doing different work, badly.

**Consequence for the eval gate:** flag-agreement rate is no longer a
proxy for "the extractor read the flags". A report with no abnormal rows
offers nothing to check. Any future gate metric over this must be computed
over `reported in COMPARABLE_REPORTED_FLAGS`, and its denominator reported
alongside it — a category scoring 100% on three rows is not the same claim
as one scoring 100% on three hundred.

---

## D21 — The factory adapter reads ground-truth 0.2.0 only, and checks it

**Status:** DECIDED
**Date:** 2026-09-21
**Records:** a change already committed on this branch (`fix(w2): read
doc-factory ground truth 0.2.0, and check its rendering claim`), written up
here afterwards so this file stays append-ordered behind D20.

**Decision:** `factory_mapping.SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION` is
`doc-factory-groundtruth/0.2.0` and nothing else. 0.1.0 is not supported.

0.2.0 adds `rendering` to every text field — `verbatim`, `derived`,
`wrapped`, `truncated` — declaring how `rendered_text` relates to `value`.
`GroundTruthConversion.chart_truth` keys on that declaration instead of
inferring divergence by comparing parsed values, which could not distinguish
"the document says something else on purpose" from "the document lost the
rest". The comparison is kept as a **check on** the declaration:
`same_meaning()` (public, was about to be dead code) plus a test asserting
that every field the factory calls `verbatim` parses back to its own
`value`.

All four renderings map to `located` — each is genuinely on the page — and
everything but `verbatim` also produces a `ChartTruthNote`. A `truncated`
field is located: the ellipsised string is what the page says, and a
citation quoting it is honest; the unrecoverable remainder goes to
`chart_truth`, not into the value.

**Rationale:** the version pin exists to fail loudly rather than read old
files under new meanings, and supporting a superseded format doubles the
surface for files that no longer exist on any branch. Trusting the new
declaration without checking it would make the conversion proof a
restatement of the factory's opinion of itself.

**Known limitation, recorded rather than fixed:** `Citation` carries one
`bbox`, so a `wrapped` field is located by the union of its line boxes and
the per-line `line_bboxes` are dropped (with `origin` and `kind`, for
different reasons — see `GROUND_TRUTH_ATTRIBUTES_NOT_CARRIED`). No Week 2
consumer needs per-line boxes; a UI that highlights exact glyphs on a page
would, and that is a Phase 3 contract change, not a guess to make now. No
committed sample wraps or truncates today, and a test fails if one starts
to.

---

## D22 — The published JSON Schemas are a wire contract, and do not carry the invariants

**Status:** DECIDED
**Date:** 2026-09-22

**Decision:** `docs/w2/schemas/*.schema.json` describe the **JSON on the
wire**. The door into the contracts from that JSON is
`model_validate_json`, not `model_validate` on an already-parsed dict.
Both facts are pinned by tests in `agent/tests/test_w2_schema_validation.py`.

**Why the door matters.** Under `strict=True` (D9) Pydantic refuses a
Python `str` where an enum member is declared — which is the point, and is
what stops a stringly typed value drifting in from Python code. JSON has no
enum type, so `model_validate_json` still reads `"located"` as
`FieldStatus.LOCATED` while `model_validate` on the same payload as a dict
fails on every enum in it. A consumer who parses the JSON first and then
hands over the dict sees what looks like a schema mismatch and is not one.

**What the schemas do not carry.** JSON Schema describes shape: types,
enums, required keys, bounds, `additionalProperties: false`. It cannot
express the cross-field rules that make these contracts safe. An instance
can satisfy every published schema while breaking invariant 4 (a `located`
field with no citation), D1 (an idempotency key that is not the composition
of its own components) and D17 (a claim whose citations mix source types).
Those live in model validators and nowhere else.

**Consequence:** validating against `docs/w2/schemas/` is necessary and not
sufficient. Anything outside this repository that accepts a Week 2
extraction — the API collection, a non-Python consumer, a future eval
harness — checks shape with the schema and must still pass the payload
through the models for the invariants, or state plainly that it has not.

---

## D24 — Numbers, units, dates and lab flags are never near-matched

**Status:** DECIDED
**Date:** 2026-09-22

**Numbering:** the next free number **against `main`** is D23. D23 is
already used by two unmerged branches — `feat/w2-retrieval` (local
embeddings and hybrid RRF) and `feat/w2-writeback` (the write-back
endpoint as a custom module) — so this entry takes D24 to collide with
neither. Renumber at merge time if that order changes.

**Decision:** the deterministic locate step (D3) matches a value in tiers —
`exact`, `numeric`, `number_only`, then `fuzzy`. **The fuzzy tier is never
used on a value that is a number, a unit, a date or a lab flag.** Those
four classes are matched exactly or not at all; what cannot be matched is
`unsupported` (invariant 4). The fuzzy tier stays available for **free
text** — names, drug names, allergens, free-text answers — where an OCR
slip is worth forgiving.

**Every locate carries the tier that produced it and its match score.**
`LocateResult.tier` is `exact | numeric | fuzzy` and `LocateResult.score`
is 1.0 for every exact and numeric match and the similarity — always below
1.0 — for a fuzzy one. A consumer is expected to hold a fuzzy locate at
lower confidence than an exact one rather than treat the two alike.

This is a policy, not a default: `FuzzyMode.ALWAYS` does not lift it.

**Rationale:** a near match asserts that two strings are the same despite a
difference. In free text the difference is usually the reader's error. In
these four classes the difference **is** the value: `3.1` and `8.1` are one
character apart and are a normal and a critical potassium; `mg/dL` and
`mg/mL` are one character apart and a thousandfold apart; `2026-09-09` and
`2026-09-08` are different collection days. There is no reading of "close
enough" that is safe there.

**Evidence** — `agent/tests/locate_measurement.py` over the six committed
doc-factory samples; thresholds in `agent/tests/test_locate_measurement.py`:

- One-character decoys of numeric, unit and date fields are located **0
  times in 242** (numeric 165, unit 57, date 20), **under default settings**
  rather than with the fuzzy tier switched off, and identically whether the
  class is declared or inferred.
- Flag fields contribute no decoys: a one-character value cannot be
  perturbed into a one-character decoy that is still absent from the page.
  The flag half of the policy therefore rests on the code path and its unit
  tests, not on decoy evidence.
- Every remaining false locate is on a **free-text** field — **57.1%**
  (20/35) on `scanned_light`, **37.5%** (12/32) on `scanned_heavy`. That is
  the cost the tier knowingly buys, on the one class where it still runs.
- The policy removed **18 false locates** across the two scanned samples
  (26 -> 20 and 24 -> 12 decoys located), all of them on numeric, unit or
  date fields. Clean pages were already at 0%, because the tier is off on a
  text layer regardless.
- The recall cost falls on the heavy scan: page-wide located rate
  **49.5% -> 44.9%**, while the share of located boxes actually over the
  right ink (IoU >= 0.5) rose **65.6% -> 70.4%**. Fewer boxes, and a larger
  fraction of the ones produced are right.

**Known limit — declare the class, do not rely on inference.**
`normalize.classify_value` infers the class from the value's characters. It
is deliberately biased towards the four narrow classes, because calling a
unit `free_text` lets a near match change a digit while the reverse only
costs a value its near-match tier. The blind spot is that inference sees
only what the model emitted, and a garbled value can stop looking like its
own class:

- OCR reading `mg/dL` as `md/gL` is still unit-shaped and stays `unit`;
- OCR reading the unit `ratio` as `rtaio` is not, and classifies
  `free_text`. That was the single disagreement between inference and
  declaration across 136 scanned decoys.
- A ratio written with a colon, `7.6:1.99`, classifies `free_text` for the
  same reason. (`7.6/1.99` does **not** — a slash keeps it unit-shaped.)

**So callers declare it.** `locate(value_class=...)` and
`locate_in_row(value_class=...)` take the class explicitly, and an
extractor filling a strict schema always knows which field it is filling.
Inference is the fallback for a caller that genuinely does not know, not
the intended path.

**Relationship to D13.** `LocateStatus` is the locate step's own answer and
has three values: `located`, `not_located`, `ambiguous`. It sits one layer
below D13's `FieldStatus`. **`ambiguous` maps to `unlocated`, with
`UnsupportedReason.not_located_on_page`** — the step found several places
the value could be and refused to choose, which is a value that was not
placed on a page, not a value the document lacks. `not_located` maps the
same way. Neither ever becomes `missing` or `illegible`: those describe the
document, and the locate step is not entitled to that judgement.

---

## D23 — The Week 2 UI extends the chart embed, and `AnswerView` is what it draws

**Status:** DECIDED
**Date:** 2026-09-22
**Raised by:** work order 11 (UI part 1).

**Decision:** The source-grounded answer view is built into the Week 1
chart-embed module (`interface/modules/custom_modules/oe-module-clinical-copilot`),
as a second patient-dashboard card, and **not** into the standalone UI
(`agent/deploy/ui/index.html`). What it renders is a new contract,
`AnswerView` (`app/models/w2/answer_view.py`): a `FinalAnswer` plus the
`ViewerDocument`s, `EvidenceChunk`s and `ReportedFlagView`s needed to draw
it, with validators that refuse an answer citing a source the view does not
carry.

**Rationale.** The work order asked for the chart embed "unless there is a
clear reason to prefer the standalone UI". There is a clear reason the
other way: the document bytes must be fetched server-side with the
clinician's own authority (D28), and the standalone page has no OpenEMR
session, no CSRF token and no ACL — it is a static file behind Caddy where
the operator pastes a token. A proxy cannot exist there. The upload control
has the same problem: it attaches a document to the patient whose chart is
open, and only the module knows which patient that is
(`ChartRequest::fromRequest()` refuses to answer for any other).

`AnswerView` exists because `FinalAnswer` is complete as an accountability
record and incomplete as something to draw, in three ways that are each a
way the view could lie: a document citation names a page whose bytes and
size the view must already have; a guideline citation carries its snippet
but not its publisher, so a chip would quote with no attribution; and a
`ReportedFlag` has to reach the renderer as a value, not as an empty
string it would have to interpret (D20). Putting those checks in a model
means they fail at the boundary rather than in JavaScript on a page a
clinician is already reading.

**Consequence:** the module now carries ES modules and a vendored library
under `public/`, and `.mjs` is served as `text/javascript` by the
container's Apache. The card registration and the three PHP routes are
specified in `docs/w2/ui-integration.md` and were **not** written on
`feat/w2-ui` — work order 09 owns all PHP in that module.

---

## D25 — PDF.js is vendored and pinned, not loaded from a CDN

**Status:** DECIDED
**Date:** 2026-09-22

**Decision:** `pdfjs-dist` **6.3.289**, the `legacy/build` minified ES
modules, committed under `public/vendor/pdfjs/` with their digests recorded
in `PROVENANCE.md`. Apache-2.0, compatible with the fork's GPL-3.0.

**Rationale.** The decision does not rest on CSP, and the work order's
"state the CSP reasoning" has an honest answer: OpenEMR sends no
`script-src` policy on the patient dashboard — the only
`Content-Security-Policy` headers in the tree are `frame-ancestors 'none'`
on `interface/login/login.php` and `portal/index.php` — so a CDN script
would load. That is the problem rather than the permission. The library
executes in the same document as the chart, so a CDN origin has script
access to PHI on every chart open, with only its own integrity in between.
Clinic installs are also routinely without egress, and every other
front-end asset OpenEMR uses is served from its own webroot. Finally, when
a rendered page is the evidence behind a clinical claim (invariant 2),
which renderer drew it is part of the provenance, and a pinned file with a
recorded digest answers that where `@latest` does not.

**Consequence:** ~1.8 MB of vendored JavaScript, and an upgrade is a
deliberate act that re-runs the overlay alignment check — the coordinate
work depends on `page.view`, `page.rotate` and `viewport.transform`. The
viewer cross-checks PDF.js's own `viewport.transform` against its
reimplementation on every render and draws **no** boxes if they disagree,
so a library change that moved every box surfaces as "no boxes and a
message" rather than as boxes in plausible wrong places.

---

## D26 — A wrapped document field is one located citation per line box

**Status:** DECIDED
**Date:** 2026-09-22

**Decision:** When an extracted value wraps across lines, the locate step
emits **one `Citation` per line box**, all sharing `source_id`,
`field_or_chunk_id` and `extraction_id`. The view groups a claim's
citations by `(source_type, source_id, field_or_chunk_id)` into a single
chip, and activating that chip highlights every one of its boxes and
scrolls to their union.

**Rationale.** `Citation.bbox` is one `BBox` and `BBox` is one rectangle.
A two-line value has no single rectangle: the union covers the whitespace
between the lines and runs the full column width on both, so highlighting
it would claim ink that is not the value's. The alternative — letting the
view split a union box by an assumed line height — would be the view
inventing geometry, which is exactly what D3 keeps out of everything but
the deterministic locate step. One citation per box keeps every rectangle
something code actually found, and grouping at render time keeps the reader
seeing one source rather than three.

**Consequence, stated plainly:** none of the six committed doc-factory
samples contains a wrapped field — the 0.2.0 `rendering` vocabulary has a
`wrapped` member and no sample uses it — so the multi-box path is covered
by unit tests (`tests/js/answer-view.test.mjs`) and is **not** exercised
against ground truth in a browser. A factory scenario with a wrapped field
would close that gap.

---

## D27 — Fixture ground truth is mapped into the rendered document's space

**Status:** DECIDED
**Date:** 2026-09-22
**Raised by:** work order 07 (locate), during work order 11.

**Decision:** `agent/scripts/build_ui_fixtures.py` maps every recorded
`bbox` out of the clean layout and into the space of the file that will
actually be rendered, through
`transform.pages[n].pdf_points_to_image_px` and the pixel-to-point scale,
**before** any contract object is built. The viewer's only transform is
page space to CSS pixels, and it never learns which samples were scanned.
The axis-aligned hull of the four mapped corners is what a `BBox` carries.

**Rationale.** Ground truth records boxes on the clean layout
(`coordinates.space: clean_pdf`) and a scanned scenario renders a skewed
JPEG of that layout. Locate output is already in the scanned document's
space, because the locate step matches against word boxes taken from the
document it was handed. Doing the mapping in the fixture builder makes the
fixture the same shape as production; doing it in the viewer would add a
branch that only ever runs for fixtures, and would put factory metadata
into the render path.

The hull rather than two corners because skew turns a rectangle into a
parallelogram. At 1.14 degrees on a 270pt-wide header that adds about 5pt
of height. That is the honest answer for an axis-aligned box: it covers the
ink, and it does not claim the ink is anywhere it is not.

**How it is checked:** the scan mapping is implemented **twice** — once in
the builder (Python) and once in the alignment checker
(`dev/ground-truth-scan.mjs`) — and the check compares the drawn box
against the checker's own expectation, so the two have to agree
independently. A mutation that made the builder skip the transform failed
only the two scanned scenarios, by 0.01–0.6px, and passed the ink check:
the cross-implementation comparison was the only thing that caught it.

---

## D28 — The PDF preview is served by a module route, not by `GET /fhir/Binary/:id`

**Status:** DECIDED — supersedes work order 11 requirement 4
**Date:** 2026-09-22
**Raised by:** work order 09 (write-back), during work order 11.

**Numbering:** this entry was written as D24 on `feat/w2-ui` before the
rebase, and D24 is taken on `main` by the locate work order (numbers,
units, dates and flags are never near-matched). Following the convention
that entry set: D23 and D25 are held by unmerged branches
(`feat/w2-retrieval`, `feat/w2-writeback`), and D26 and D27 are this
branch's own, so this takes **D28**, which collides with nothing known.
Renumber at merge time if that order changes.

**Decision:** The viewer fetches document bytes from
`GET <module>/document?document_id=<int>`, a module route that checks the
clinician's own session and the `patients`/`docs` ACL and streams the file
server-side. `200` with `application/pdf`, `403` without the ACL, `404` for
an unknown document. The token never reaches the browser, which was the
part of requirement 4 that always mattered.

**Rationale.** Requirement 4 named `GET /fhir/Binary/:id`. That route
cannot serve a physician: for a non-patient request,
`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:285` runs
`RestConfig::request_authorization_check($request, "admin", "users")`
before `FhirDocumentRestController::downloadDocument()`, so it answers 403
for exactly the users this feature exists for. The scope is not the
obstacle — `user/Binary.read` is a real scope — the ACL is. This is the
same shape as findings.md F-50: OpenEMR's authorization layer reasons about
roles, not about "this patient", and a FHIR route inherits whatever
coarse check was put on it.

**Consequence for the viewer:** none, by construction. It asks a
`DocumentSource` for an ArrayBuffer and does not know what is behind it;
the module route and the fixture-mode file both satisfy the same two-method
interface. The 403 and 404 cases are distinguished and say different
things, because "you may not see this" and "this is gone" are different
facts about a patient's record.
