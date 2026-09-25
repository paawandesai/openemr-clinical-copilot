# Week 2 — Decisions

**Append-only.** Never edit or delete an existing entry. To change a decision,
append a new entry that supersedes it by ID and set the old entry's status to
`SUPERSEDED by Dn` on its status line only.

Statuses: `DECIDED` (settled; do not re-open, raise conflicts in your session
report) · `OPEN` (not settled; propose, do not assume) · `PENDING-SPIKE`
(blocked on a named spike) · `SUPERSEDED by Dn`.

**Numbering.** A number is assigned when the entry **merges to `main`**, not
when it is written on a branch. A branch may use a working number, but if
that number is already taken on `main` when the branch merges, the branch's
entry moves to the next free number at the end of the sequence and gains a
`**Numbering:** was Dxx on <branch>, renumbered on merge because <the
earlier entry>` line in its header. The entry that landed first always keeps
its number. Rebase before merging and renumber every reference on the
branch — docs, code comments, tests — in the same change.

D25, D26 and D29-D31 were dropped from this file by the conflict resolution
in merge `d65dac4` (`feat/w2-ui`) and restored verbatim from `720d5bd`, the
merge before it; D29-D34 were renumbered at the same time.

**When this file conflicts in a merge, the resolution is always "keep both
sides" — never pick one.** Keep every entry from both sides, then renumber
the later side's colliding entries as above. Two merges have silently
deleted entries by picking a side: `d65dac4` dropped five, and `cf182df`
dropped the intake-extractor entry, restored as D35.

**Numbers may be reserved ahead of merge** when several branches are in
flight, provided the merge order is fixed and each branch is told its
numbers. This avoids the repeated renumbering that dropped entries twice.
A reserved number is not free, even while `main` skips it. Current
reservation (2026-09-22): `feat/w2-observability` D36-D39, `feat/w2-graph`
D40 (merged), `chore/w2-deploy-rehearsal` D41, `feat/w2-eval-gate` D42-D48.

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

**Status:** SUPERSEDED by D25
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
`gate-cli.sh --check-path` (it shares `gate-lib.sh` with the hook) rather than keeping a copy. [Corrected 2026-09-22 on `feat/w2-graph`: was `gate-guard.sh --check-path`, which no longer takes arguments.]

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
## D23 — Local embeddings, weighted hybrid RRF, and a local reranker used as a fallback

**Status:** SUPERSEDED by D25 — D25 accepts most of this and rejects two
parts of it: the fitted fusion weighting, and reranking only as a fallback.
**Date:** 2026-09-22
**Evidence:** [`docs/w2/retrieval-bakeoff.md`](retrieval-bakeoff.md).
Branch `feat/w2-retrieval`, work order 08.

**On the number:** the work order asked for D21, on the understanding that
the contracts branch held D9-D20. It holds D9-**D22**, and those merged to
`main` while this branch was being built, so D21 and D22 were already
taken. D23 is the next free number. (This branch was 18 commits behind
`main` when the entry was written and has since been rebased onto it; the
numbering was chosen against `main` from the start, so the rebase changed
nothing here.)

### Proposal

1. **Embeddings: local.** `BAAI/bge-small-en-v1.5` through fastembed /
   ONNX Runtime. MIT, 67 MB, 384-dimensional, and specifically not
   sentence-transformers, so torch stays out of the agent image.

2. **Fusion: hybrid RRF with a per-retriever search depth of 30**, k=60,
   independent of `top_k`.

3. **Fusion weights: dense 2×, lexical 1×** — adopted *after* the
   weighting is confirmed against the Week 2 eval cases, not before.

4. **Reranker: the local cross-encoder** `Xenova/ms-marco-MiniLM-L-6-v2`
   (Apache-2.0, 80 MB, ONNX), **not on the default path**. Run it as a
   fallback when the fused result set is weak. The trigger condition is
   not designed yet.

5. **Cohere rerank: neither adopted nor rejected.** It was not measured.

### The numbers this rests on

30 concept-only dev queries, 110 relevance judgments, 696 chunks from 28
public-domain documents, Apple M5 Pro:

| config | recall@5 | MRR | p50 ms | queries at zero recall |
|---|---|---|---|---|
| BM25 only | 0.570 | 0.733 | 0.2 | 2 |
| dense only | 0.718 | 0.854 | 2.6 | 1 |
| hybrid RRF (1:1) | 0.692 | 0.826 | 2.8 | 1 |
| hybrid RRF (dense 2×) | **0.733** | **0.887** | 2.7 | 1 |
| hybrid + local cross-encoder | 0.702 | 0.889 | 510.6 | **0** |
| hybrid + Cohere rerank-v3.5 | — | — | — | not run: no API key |

### Rationale

**Local embeddings, on the constraint before the numbers.** D5's hard
constraint is that no patient identifier may appear in a query that leaves
this system. A hosted embedding API puts *every* retrieval query on the
wire, which makes that constraint depend on a text filter holding on every
call forever. A local embedder makes it depend on nothing: the query never
leaves the process. bge-small then also happens to win on the numbers, at
2.6 ms and no marginal cost.

**Fusion depth is a correctness fix, not tuning.** Fusing each retriever's
top-10 scored recall@5 0.663, *below* dense alone — the fusion was
discarding exactly the cases where one retriever found what the other
missed. Depth 30 gives 0.692 with nothing else changed, and 60 and 100
give the same, so it has flattened rather than been fitted.

**The weighting is fitted, and is flagged as fitted.** Dense 2× is worth
~4 points of recall@5 and is stable across depths 30-100 and k 20-60, but
30 queries is not enough to adopt a weight on. Dense 3× scores higher
recall and lower MRR, which is what overfitting looks like. The code's
default stays 1:1; the bake-off passes 2× explicitly.

**The reranker's value is not its mean.** It *lowers* mean recall@5
(0.733 → 0.702) and moves MRR by 0.002, for 500 ms. What it does is turn
the one total miss into a hit: `q19-levothyroxine-followup` goes 0.00 →
1.00, because the only chunk in the corpus that answers it is written for
patients while the query is written for clinicians, and a cross-encoder
reading the candidate pool can see that where a bi-encoder and BM25
cannot. A stage whose value is eliminating total misses belongs on the
path where a total miss is about to happen, not on every turn — and in an
answer pipeline that must cite or disclose, a turn with no usable evidence
is exactly the turn worth spending 500 ms on.

**Cohere is unmeasured and is reported as unmeasured.** `COHERE_API_KEY`
is not configured, and the bake-off reports the row as SKIPPED rather than
falling back to the local reranker. The adapter exists
(`app/retrieval/rerank.py`), sends a plain `urllib` POST rather than
pulling in an SDK, and runs the concept-only guard before building the
request — but it has never executed. Claiming a comparison here would be
inventing one.

### What has to happen before this becomes DECIDED

- **Confirm the dense-2× weighting on the Week 2 eval cases**, not on the
  30 staged dev queries it was fitted on.
- **Measure both models on the Vultr droplet.** Everything above is an
  Apple M5 Pro. Both models are CPU-bound ONNX on a shared vCPU there; the
  cross-encoder's 500 ms is the number most likely to become seconds, and
  the cold-start embedding of the whole corpus needs to be warmed at
  deploy rather than on the first clinician's question.
- **Design the reranker's fallback trigger**, with evidence rather than a
  guessed threshold.
- **Decide whether `dated` reaches the reader.** Two sources are flagged
  dated, one of them (`niddk-evaluate-ckd`, reviewed 2015) on eGFR, where
  the guidance has since moved. `GuidelineSource` has no `dated` field, so
  today the only signal a caller gets is `version`. That is a contracts
  question, not a retrieval one.

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

## D25 — Local embeddings, equal-weight hybrid RRF, local cross-encoder rerank

**Status:** DECIDED — supersedes D5 (OPEN) and D23 (PROPOSED).
**Number:** this entry was written as D24 on `feat/w2-retrieval`. D24 was
taken on `main` by `feat/w2-locate`'s exact-only value-class policy, which
merged first, so it is D25 — the next free number against `main` at the
time of the rebase. Nothing about the decision changed with the number.
**Date:** 2026-09-22
**Evidence:** [`docs/w2/retrieval-bakeoff.md`](retrieval-bakeoff.md), on the
28-document / 693-chunk corpus after the licensing audit (D26).
**Deployment:** [`docs/w2/retrieval-deploy.md`](retrieval-deploy.md).

### Decision

1. **Embeddings: local.** `BAAI/bge-small-en-v1.5` through fastembed / ONNX
   Runtime. MIT, 67 MB, 384-dimensional. Not sentence-transformers, so
   torch stays out of the image.

2. **Retrieval: BM25 + dense, fused with RRF at equal weights.** Each half
   searches to depth 30; the fused list is what the reranker sees. Equal
   weights, explicitly: weighting the dense half 2× scored better
   (recall@5 0.733 vs 0.692) but was fitted on the same 30 dev queries it
   was measured on, and 30 queries is not enough to adopt a weight from.
   D23 proposed adopting it; that part is rejected here. Revisit it
   against the eval cases, where fitting and measuring are separated.

3. **Rerank: the local cross-encoder, over the top 20, on every retrieval.**
   `Xenova/ms-marco-MiniLM-L-6-v2`, Apache-2.0, 80 MB, ONNX. D23 proposed
   running it only as a fallback; that part is rejected too — the PRD
   requires a reranker, and a reranker that runs only when some
   not-yet-designed trigger fires is not one.

4. **Four chunks are passed to the answer step.** recall@4 for the decided
   configuration is 0.666.

5. **Latency budget: 2.5 s for the rerank stage.** Over it, the fused order
   stands and the output says `reranked=false`. The retriever then opens a
   circuit so the next turn skips rerank rather than paying the budget
   again to discard the result again; `GuidelineRetriever.reset_rerank_circuit()`
   closes it. `EvidenceRetrieverOutput` enforces the other half — a result
   set marked `reranked=true` must carry a `rerank_score` on every chunk —
   so the flag and the data cannot drift apart.

6. **No Cohere this week.** No API key is configured, so it was never
   measured. The adapter stays in the tree, disabled and unreferenced by
   any default path.

### The numbers

30 concept-only dev queries, 110 relevance judgments, 693 chunks, Apple M5
Pro, five timing passes:

| config | recall@5 | recall@4 | MRR | p50 ms | p95 ms | queries at zero |
|---|---|---|---|---|---|---|
| BM25 only | 0.570 | 0.505 | 0.733 | 0.2 | 0.3 | 2 |
| dense only | 0.718 | 0.654 | 0.854 | 3.2 | 7.0 | 1 |
| hybrid RRF, equal weights | 0.692 | 0.644 | 0.826 | 3.6 | 4.6 | 1 |
| hybrid RRF, dense 2× (fitted) | 0.733 | 0.672 | 0.887 | 3.9 | 4.9 | 1 |
| **hybrid RRF + cross-encoder (this decision)** | 0.702 | 0.666 | **0.889** | 288.7 | 515.1 | **0** |
| hybrid RRF dense 2× + cross-encoder | 0.702 | 0.677 | 0.889 | 283.3 | 490.6 | 0 |
| hybrid RRF + Cohere rerank-v3.5 | — | — | — | — | — | not run: no key |

### Rationale

**Local embeddings, on the constraint before the numbers.** D5's hard
constraint is that no patient identifier may appear in a query that leaves
this system. A hosted embedding API puts *every* retrieval query on the
wire, which makes that constraint depend on a text filter holding on every
call forever. A local embedder makes it depend on nothing: the query never
leaves the process. That is why "no Cohere" is not only a consequence of
having no key — an all-local stack makes D5 structural rather than
procedural, and that is worth more than an unmeasured ranking improvement.

**Equal weights, because the alternative was fitted.** Dense 2× is worth
four points of recall@5 and is stable across fusion depths 30-100 and RRF
k 20-60 — and dense 3× scores higher recall (0.745) with *lower* MRR
(0.854), which is the shape of overfitting rather than of a better
setting. The honest position is that the dev set cannot tell us the weight,
so the code ships the untuned default and the question moves to the eval
cases.

**The reranker earns its ~300 ms on the case that matters, not on the
mean.** It *lowers* mean recall@5 (0.733 → 0.702 against the fitted
fusion, 0.692 → 0.702 against the equal-weight fusion it actually replaces)
and moves MRR by 0.002. What it does is eliminate total misses: it is the
only configuration with no query at zero recall.

The concrete case is `q19-levothyroxine-followup`, which asks about
levothyroxine dosing and follow-up testing. Exactly one chunk in the corpus
answers it, in a consumer-facing NIDDK page — *"You will take
levothyroxine, a thyroid hormone medicine…"* — and neither BM25 nor the
bi-encoder puts it in the top 10, because the query is written in clinical
register and the chunk is written for patients. recall goes 0.00 → 1.00
with the cross-encoder, which reads the query and the passage together.

In a pipeline that must cite or disclose, a turn with no usable evidence is
the expensive turn. Spending ~300 ms to stop having them is the right trade,
and it is why the reranker runs on every retrieval rather than behind a
trigger nobody has designed.

**The budget exists because 300 ms is a laptop number.** The deployment
host is a shared-vCPU droplet and nothing here has been measured on it. If
the cross-encoder does not fit, the system degrades to the fused order and
says so in the output and the log, rather than getting slow. A sustained
stream of `rerank_over_budget: true` is the condition to re-open this
decision — not a reason to raise the budget.

### What is still unmeasured

- **The droplet.** Every number above is an Apple M5 Pro.
- **Cohere.** Never run. Claiming it is worse than the local cross-encoder
  would be inventing a comparison.
- **The fusion weighting, separated from its fitting.** That needs the
  eval cases.

---

## D26 — USPSTF statements stay in the corpus, as licensed material rather than public domain

**Status:** DECIDED
**Number:** written as D25 on this branch and renumbered on the same rebase
that moved the retrieval decision from D24 to D25; D24 belongs to
`feat/w2-locate`. The licensing finding is unchanged.
**Date:** 2026-09-22
**Evidence:** `agent/scripts/audit_corpus_licensing.py`, run against all 28
sources' raw HTML on 2026-09-22.
**Needs human sign-off:** yes — see "Conflict" below.

### What the audit found

Work order 08 named USPSTF, CDC and NIH as acceptable publishers and asked
for "public domain or openly licensed ONLY". `sources.yaml` recorded all
28 sources as `us-gov-public-domain`. For eight of them that was wrong.

**USPSTF recommendation statements are not public domain.** They are
published by JAMA — or, for the 2015 thyroid statement, Annals of Internal
Medicine — under arrangement with AHRQ, and the page says so: "©2022 AMA
and United States Government … All rights reserved."

The sentence that follows is what makes them usable: AHRQ "permits members
of the public to reproduce, redistribute, publicly display, and incorporate
USPSTF work into other materials provided that it is reproduced without any
changes to the work or portions thereof", and adds that AHRQ and HHS
"cannot endorse, or appear to endorse, derivative or excerpted materials".

Two smaller findings:

- **nhlbi.nih.gov** captions its embedded animations "Medical Animation
  Copyright © Nucleus Medical Media, All rights reserved" — a third party's
  notice, for a video this corpus does not carry.
- **MedlinePlus** is mixed: `/lab-tests/` pages are NLM's own and public
  domain, while `/ency/` is copyright A.D.A.M. Inc. and `/druginfo/` is
  copyright ASHP. All three MedlinePlus sources here are `/lab-tests/`.
  Confirmed, not assumed.

### Decision

1. **Keep the eight USPSTF sources**, recorded as
   `uspstf-ahrq-redistribution-permitted` rather than
   `us-gov-public-domain`. `SourceRecord.verbatim_only` is True for them.
2. **Verbatim excerpts only.** `app/retrieval/worker.py::citation_for()`
   refuses any quote that is not literally a substring of its chunk, so a
   paraphrase cannot be presented as the source's words. That was already
   an honesty requirement; for these eight it is a licence condition too.
3. **The UI must not present a USPSTF excerpt as an AHRQ or HHS
   endorsement** of the co-pilot's answer
   (`corpus_meta.USPSTF_ENDORSEMENT_CONSTRAINT`). Not yet implemented —
   there is no UI for guideline evidence yet.
4. **Third-party rights notices are stripped from the corpus text** at
   fetch time and recorded in `sources.yaml` instead. Leaving them in meant
   a retrieval could surface "All rights reserved" as evidence and a
   citation could quote it back to a clinician. Three chunks were removed;
   every bake-off metric was unchanged to three decimal places, which is
   the evidence that what was stripped was chrome and not content.
5. **No source was removed for licensing.**

### Conflict, stated plainly

The review instruction was "remove any non-public-domain source". Read
literally that deletes all eight USPSTF statements — the actual
recommendations, and the most clinically load-bearing part of the corpus —
on the strength of a copyright notice that is immediately followed by an
express grant of the exact permission this use needs.

This entry keeps them instead, because (a) the same work order named USPSTF
as an approved publisher, (b) the permission is explicit and this use is
verbatim reproduction of portions with attribution, and (c) deleting a
third of the corpus on a licence reading is a bigger unilateral act than
recording the licence accurately and asking.

**A human should confirm this.** If the answer is remove them, it is one
edit to `sources.yaml` and a rebuild; the audit script, the licence enum
and the verbatim-only plumbing all stay as they are.

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

**Status:** DECIDED — supersedes work order 11 requirement 4. Its preview URL is SUPERSEDED by D49.
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

---

## D29 — The lab write-back endpoint ships as a custom module, not a core route

**Status:** DECIDED
**Date:** 2026-09-22
**Implements:** D1 (superseding), whose "custom endpoint in the OpenEMR fork,
protected by a new custom OAuth scope" this entry pins down.
**Numbering:** was D23 on `feat/w2-writeback`, renumbered on merge because D23 (local embeddings and weighted hybrid RRF, `feat/w2-retrieval`) landed on `main` first.

**Decision:**

- The endpoint is **`POST /api/clinical-copilot/lab-writeback`**, registered by
  the existing `oe-module-clinical-copilot` module through OpenEMR's own
  extension points — `RestApiCreateEvent` for the route and `RestApiScopeEvent`
  for the scope. No file under `apis/routes/` or `src/Common/Auth/` is edited.
- The custom scope is **`user/lab-writeback.c`**.
- The ledger is **`module_clinical_copilot_writeback_ledger`**, created by the
  module's `sql/install.sql`, `UNIQUE` on `idempotency_key`.
- A written-back result is created with **`procedure_report.review_status =
  'received'`**, i.e. unreviewed.

**Rationale:**

*Module over core route.* Both extension points already exist and are
dispatched on every relevant request: `StandardRouteFinder::find()` fires
`RestApiCreateEvent` after loading the core route map, and
`ScopeRepository::getCurrentSmartScopes()` fires `RestApiScopeEvent` — which is
what dynamic client registration validates against and what the token endpoint
grants from. Both reach the module because `interface/globals.php`, which
constructs `ModulesApplication`, is loaded by `SiteSetupListener` for `/apis/`
and `/oauth2/` alike. The route that results is dispatched, scope-checked and
audited exactly like a core one — verified: a token without the scope is
refused by `AuthorizationListener` with `scope user/lab-writeback.c not in
access token`. Editing `apis/routes/_rest_routes_standard.inc.php` and
`ServerScopeListEntity` would buy nothing and cost a permanent merge conflict
against upstream in two files every rebase touches, in a fork whose whole
history is one squashed import. It would also make the write-back
unswitchable: the module can be disabled, a core route cannot. The Week 1
chart embed is already this module, so this adds no new deployment unit.

*The scope string is not free choice.* `HttpRestParsedRoute` takes the last
non-parameter path segment as the resource and
`HttpRestRouteHandler::checkSecurity()` maps POST to the `c` permission, so the
route path and the scope are two spellings of one thing. `.c` rather than
`.write` because create is all this endpoint does — a correction is a new
document and a new extraction, never an edit of a value already in the chart —
and `.write` would grant update and delete that nothing implements.

*Unreviewed, deliberately.* `review_status = 'reviewed'` would put the result
straight into **Procedures → Patient Results**, which is tempting and wrong: it
asserts a clinician has reviewed a value no clinician has seen.
`orders_results.php` skips `review_status = 'received'` in patient-results mode
and shows it in review mode, so an extracted result lands in **Procedures →
Pending Review** and moves to Patient Results when a clinician signs it. That
is the same path a result arriving from a real lab interface takes.

**Consequences, all verified on the local stack:**

1. **The client also needs `api:oemr`.** `BearerTokenAuthorizationStrategy::
   isValidRequestForUserRole()` rejects any `/api/` request whose token lacks
   it, before the resource scope is looked at. A client registered with only
   `openid user/lab-writeback.c` gets 403 on every call. Scopes are immutable
   per client, so this is a registration-time decision.
2. **The write-back client is a third local client**, separate from the read
   client and the chart embed, exactly as D1's "human step" anticipated.
   `skip_ehr_launch_authorization_flow` is **not** needed for it — that flag
   exists for the embed's server-side loopback broker, and the write-back
   client uses the password grant.
3. **The endpoint requires two ACLs as well as the scope**, `patients`/`docs`
   and `patients`/`lab`, both with `write`/`addonly`. A scope says what the
   client application may ask for; an ACL says what this clinician may do.

**Not settled here, and out of scope for the branch that wrote this:** batching
several results into one request, and the agent-side client.

---

## D30 — The PDF preview reads through the module, not `GET /fhir/Binary/:id`

**Status:** DECIDED — supersedes the read-back half of D1 (superseding). Its preview URL is SUPERSEDED by D49, which confirms it. The
document *write* path in D1 is unchanged; so is `Binary` as a read path for a
caller that holds the `admin` ACL.
**Date:** 2026-09-22
**Numbering:** was D24 on `feat/w2-writeback`, renumbered on merge because D24 (numbers, units, dates and lab flags are never near-matched, `feat/w2-locate`) landed on `main` first.

**Decision:** The source-document preview is
**`GET <module>/public/document.php?document_id=<int>`**, served inside the
clinician's own OpenEMR session. It requires the `patients`/`docs` ACL — the
one OpenEMR's own document viewer checks — and the document must belong to the
patient whose chart is open. It serves `application/pdf` and nothing else.

**Why D1's read-back does not work.** D1 says `GET /fhir/Binary/:id` "is also
the source for the PDF preview", on the spike's evidence that it returns the
bytes intact. It does — for an administrator. The route runs
`RestConfig::request_authorization_check($request, "admin", "users")` for any
non-patient request (`_rest_routes_fhir_r4_us_core_3_1_0.inc.php:285`), and a
physician holds no `admin` ACL. Checked directly on the local stack:
`admin/super`, `admin/users` and `admin/docs` are all false for `physician`,
and the request returns **403 "Organization policy does not have permit access
resource"**. The spike only saw it work because it ran as `admin`. Granting
clinicians an `admin` ACL to read a PDF would be the wrong repair by a wide
margin.

**Why a module page and not another REST route.** The preview is rendered in
the chart by a clinician who is already authenticated to OpenEMR. A REST route
would need a brokered OAuth token for a browser `<embed>` that cannot carry an
`Authorization` header, and would re-implement an authorization decision the
session already answers. `brief.php` and `conversation.php` established the
pattern; this follows it.

**Three things it does deliberately:**

1. **One answer for every way a document is not yours.** Missing, another
   patient's, and deleted all return **404** with the same text. A document id
   that exists for a patient is a fact about that patient, and an endpoint that
   distinguished the cases would disclose it.
2. **PDF only.** A stored document may be any type on the upload whitelist, and
   streaming `text/html` or an SVG inline from OpenEMR's own origin is a
   scripting vector on a trusted page. Anything else is **415**.
3. **No CSRF token.** Reading changes nothing, a token in a query string leaks
   through referrers and history, and OpenEMR's own retrieval does not use one.
   The session and the ACL are the controls.

**Not restricted to documents this module wrote.** A clinician with
`patients`/`docs` can already open any of that patient's documents in
OpenEMR's own viewer, so narrowing would protect nothing and would break the
preview for a document uploaded any other way.

---

## D31 — A panel is one order, and one report+result per analyte

**Status:** DECIDED
**Date:** 2026-09-22
**Numbering:** was D25 on `feat/w2-writeback`, renumbered on merge because D25 (local embeddings, equal-weight hybrid RRF, local cross-encoder rerank, `feat/w2-retrieval`) landed on `main` first.

**Decision:** One write-back request carries one document and N results. The
results that are **new in that request** are written under **one new
`procedure_order`**, with a `procedure_order_code` + `procedure_report` +
`procedure_result` each, at sequence 1..N. Results already in the ledger are
returned with the ids of the write that made them and nothing is inserted for
them. The response's single `procedure_order_id` is the order this request
created — or, when it created nothing, the order recorded for its first result.

**Why one report per analyte rather than one report with N results.** A real
laboratory interface files a panel as one report with many result rows, and
that was the first shape tried. It loses the analyte's identity in the UI:
`interface/orders/orders_results.php` groups by `procedure_order_code` and
labels the group with `procedure_name`, so a panel under one code shows one
name — the first analyte's, or an invented one — for every row beneath it. The
extraction has no panel code to use instead (`LabResult.loinc_code` is
per-row and assigned, and lab PDFs rarely print a panel LOINC). A code per
analyte keeps each row named as the report named it, which is what the
clinician checking the write needs to see. `ProcedureService::search()` joins
report to order code on `procedure_order_seq`, so the sequences line up and the
FHIR read emits one Observation per analyte either way.

**Why idempotency is per result and not per request.** A request can legally
repeat results and add others — a re-extraction that found one more analyte, or
two requests overlapping on the same document. Replaying the whole request as a
unit would either duplicate the repeats or refuse the additions. Per-result
keys make the answer obvious: each row is created once, and a mixed request
reports `created` and `duplicate_ignored` row by row. Verified on the local
stack, including two identical requests fired together: the loser rolls back
and returns the winner's ids, and exactly one chain exists afterwards.

**Bound:** 100 results per request. Not a contract rule — a ceiling on how long
one transaction may hold row locks across the procedure tables. A comprehensive
metabolic panel is 14 analytes.

---

## D32 — The Week 2 UI extends the chart embed, and `AnswerView` is what it draws

**Status:** DECIDED
**Date:** 2026-09-22
**Raised by:** work order 11 (UI part 1).
**Numbering:** was D23 on `feat/w2-ui`, renumbered on merge because D23 (local embeddings and weighted hybrid RRF, `feat/w2-retrieval`) landed on `main` first.

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

## D33 — PDF.js is vendored and pinned, not loaded from a CDN

**Status:** DECIDED
**Date:** 2026-09-22
**Numbering:** was D25 on `feat/w2-ui`, renumbered on merge because D25 (local embeddings, equal-weight hybrid RRF, local cross-encoder rerank, `feat/w2-retrieval`) landed on `main` first.

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

## D34 — A wrapped document field is one located citation per line box

**Status:** DECIDED
**Date:** 2026-09-22
**Numbering:** was D26 on `feat/w2-ui`, renumbered on merge because D26 (USPSTF statements stay in the corpus, `feat/w2-retrieval`) landed on `main` first.

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

## D35 — The intake-extractor worker: declared classes, row anchors, and what becomes `missing`

**Status:** DECIDED
**Date:** 2026-09-22
**Work order:** 12 (intake-extractor worker), branch `feat/w2-extractor`.
**Numbering:** was D29 on feat/w2-extractor, restored as D35 after a merge dropped it; write-back's D29 had merged first.

**Numbering:** the next free number **against `main`** at rebase time is
D29 (`main` ends at D28, after the retrieval, write-back and UI merges).
This entry was first written as D25 on this branch, before those merges;
it was renumbered when the branch was rebased onto `main`, and no other
entry refers to it by its old number.

**Decision.** The worker lives in `agent/app/workers/intake_extractor/`, runs
as `app/graph/intake_extractor.py` (every node inside Week 1's `_untraced`
guard, the graph reachable only through `UntracedGraph`), and settles these
points the contracts and D3/D13/D20/D24 left to the implementation:

1. **Every schema field declares its value class**
   (`fields.FIELD_DECLARATIONS`); `locate` is never called with an inferred
   one. Identifiers — MRN, CLIA number, accession number — are declared
   `numeric`, i.e. exact tiers only: a near match on an identifier is a
   different patient, lab or specimen. Lab result values are `numeric` even
   when qualitative. A test walks both Pydantic schemas and fails on any
   `ExtractedField` without a declaration.
2. **Row-strategy fields are searched only on their anchor's row.** A
   result's value, unit, range and flag, a medication's dose and frequency,
   an allergy's reaction and a family-history relation are located right of
   the row's label. When the label itself is ambiguous (printed twice), the
   anchor is the one occurrence whose row carries *every* other transcribed
   value of that row, by exact and numeric tiers only; zero or several such
   occurrences stay ambiguous. This narrows the question, as `locate_in_row`
   does; it never tie-breaks. If the label cannot be placed, the row's
   values are unlocated — never searched page-wide.
3. **A blank printed field is `missing`.** Chiefly the lab flag column: a
   blank flag carries no flag, so the field is `missing` and the cross-check
   reads it as `not_reported` (D20). Only the transcription's own `absent`
   and `illegible` states and a blank `present` produce `missing` /
   `illegible`; a locate miss never does (D24 → D13).
4. **A value that cannot be parsed into its schema type is `illegible`,**
   with no value, and is listed in `ExtractionReport.parse_failures`. The
   contracts are typed (`ExtractedField[datetime]`), so an unparseable date
   cannot be held as `unlocated`. Slashed dates are read month-first only; a
   day-first-only date fails to parse rather than being re-read.
5. **The page is the model's claim and is tested:** a value is located only
   on the page the transcription names (W2-C6).
6. **Checkboxes** are located by the tick mark immediately left of the
   printed label. An unticked box draws nothing, so `False` is `unlocated`,
   never asserted.
7. **Confidence** is `reader_confidence × tier_weight × match_score`
   (`confidence.py`: exact 1.0, numeric 0.95, number-only 0.90, fuzzy 0.80);
   `unlocated` is 0.0, `missing`/`illegible` None. The model reports no
   confidence and none is asked for.
8. **Three failure reasons were added** to the closed
   `ExtractionFailureReason`: `vision_call_failed` (an API error, refusal or
   truncation — distinct from output that arrived and did not parse),
   `no_fields_located` (a run that places nothing is a failure, not an empty
   success), and `internal_error` (an unexpected exception, logged by type
   only; the caller still never sees an exception).
9. **Discrepancies** (D2) compare only located intake values against the
   chart's *current* records — Week 1's `brief_records` rule (active
   medications; allergies not explicitly ended). A written drug name
   matches when every word of it is a word of the chart product name, so a
   `derived` short name is never a discrepancy; a dose is `changed` only when
   it is none of the strengths in the product name. An unlocated intake row
   turns any omission it might explain into `unresolved`, not `omitted`; an
   illegible row does so for its whole category. Allergies have no
   `changed`: the Week 1 `AllergyIntolerance` model carries no reaction.
10. **A document/chart identity mismatch** (name or date of birth, located
    values only) is a hard flag on `ExtractionReport.identity`, not a
    `Discrepancy`, and the discrepancy check does not run when it fires.
    `app/fhir/client.py:get_patient_identity` was added for it — a narrow
    Patient read returning given/family name and birth date only.
11. **Document bytes are runtime context, not graph input or state.** A
    tracer attached directly to the compiled graph posts the root run's
    input before any node can refuse; the input is therefore
    `{"node_seconds": {}}`, as for the Pre-Visit Brief.
12. **Cost is computed in the worker** from a one-entry price table for the
    requested model (`vision.PRICES_PER_MTOK`), logged as `cost_usd` on the
    `llm_usage_event`; an unpriced served model logs `null`.
    `scripts/compute_metrics.py` deliberately has no built-in prices; this
    is a narrower, per-call estimate the work order asks the worker to log,
    and it does not change how Metric 6 is computed.
13. **LOINC codes are assigned from a committed table** (`loinc.py`), by
    exact match on the normalised printed test name — case, punctuation and
    spacing folded, nothing else — with the unit as tiebreak and as veto: a
    unit the model read that matches no entry for the name leaves the row
    uncoded, even when the name is unique. No fuzzy matching, no
    terminology service. Only a located name is coded. eGFR is refused on
    purpose: its code depends on an equation the report does not print. An
    uncoded row is skipped by the write-back planner as `no_loinc_code`. The
    planner now reports a reference range that is not a located fact as
    `reference_range_not_located`, separately from a range that is on the
    page but cannot decide the flag (`flag_indeterminate`).

**Rationale.** Each is the conservative reading of an invariant: a rule that
can only turn a would-be fact into a disclosed unsupported field, never the
reverse. Measured over the six factory samples with the vision step stubbed
from ground truth: 84.5% of transcribed values located (99%+ on text-layer
pages, all on the right ink; 33.6% on the heavy scan, the recall cost D24
recorded), discrepancy precision and recall 2/2, zero false discrepancies on
the 25 `derived` fields, and zero fuzzy locates on numeric, unit, date or
flag fields (`agent/tests/intake_extractor_measurement.py`).

---

## D36 — One encounter is one caller-supplied correlation id, held to an identifier's alphabet

**Status:** DECIDED
**Date:** 2026-09-22
**Work order:** 14 (observability). **Numbering:** written as D35 and
moved to D36 on rebase: `main` restored the intake-extractor entry as D35.
D36–D38 are this branch's.

**Decision:** An encounter is traced as one correlation id. The caller sends
the same `X-Correlation-ID` on every request of the encounter: upload,
status polls, brief, conversation turns and write-back. The agent uses it
unchanged, and Langfuse therefore shows one trace (its trace id is a
deterministic function of the correlation id). With no header, every
request still gets its own UUID, as in Week 1.

An inbound id is accepted only if it matches
`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`. Anything else is **replaced** with a
fresh UUID4 and is never logged (`app/middleware/correlation.py`,
`accepted_correlation_id`). Background work started by a request (the
extractor's `ExtractionJobs.submit`) inherits the request's id rather than
minting its own.

**Rationale:** The alternative, a separate `encounter_id` alongside a
per-request correlation id, would give every event two join keys, and every
consumer (Langfuse's trace id, `compute_metrics.py`, the Week 2 dashboard)
would have to pick one. Week 1 already built the whole pipeline on the
correlation id, and Langfuse already derives the trace from it. Reusing the
id across requests is the only change needed.

Accepting a caller's value makes the header a string written into every log
line and every exported event. Before this, it was accepted verbatim, so a
client could put a patient's name in it. An identifier's alphabet cannot
carry a sentence or a name with spaces, and a UUID, a `corr-…` test id and
k6's `k6-3-14-…` all fit it (`tests/test_w2_telemetry.py`).

**Consequence:** the chart card (`AgentClient.php`) does not send the
header yet, so from the UI each request is still its own trace. That is
listed as an open chain break in `docs/w2/OBSERVABILITY.md` §2, not fixed
here.

---

## D37 — The observability backend is checked by `/ready` but does not gate it

**Status:** DECIDED; its "`disabled` when export is not configured" wording SUPERSEDED by D55
**Date:** 2026-09-22
**Work order:** 14.

**Decision:** `/ready` reports `observability_backend` from a genuine,
background Langfuse auth check that is never older than 60 s: `healthy`,
`unhealthy` with a fixed reason, or `disabled` when export is not configured.
It stays **`required: false`**. A down Langfuse makes the check `unhealthy`
and leaves `/ready` at 200. OpenEMR and the LLM provider stay required: 503
when either is down. Each case was verified by breaking the dependency
(`docs/w2/evidence/ready-failure-modes.txt`).

**Rationale:** Readiness decides whether the instance takes clinical
traffic. Pulling a clinician-serving instance out of rotation because
traces cannot be exported trades patient-facing availability for telemetry.
And nothing is lost while Langfuse is down: every event is written to stdout
**before** export is attempted, and stdout is the metrics record of source
(`agent/docs/observability.md` §1, `scripts/compute_metrics.py`,
`scripts/w2_dashboard.py`). Export already cannot fail a request
(`langfuse_export.export_event` never raises). This keeps `/ready` saying
the same thing.

**Consequence:** a Langfuse outage is visible on `/ready`'s body and not on
its status code. Anything that alerts on readiness must read the body to
see it.

---

## D38 — Operational events are their own schemas, and a 4xx is not a dependency failure

**Status:** DECIDED
**Date:** 2026-09-22
**Work order:** 14.

**Decision:** Steps, tool calls and HTTP requests are three new event types
(`step_event`, `tool_call_event`, `http_request_event`) in
`app/models/telemetry_events.py`, apart from Week 1's KEY_METRICS schemas in
`metrics_events.py`. They are plain `BaseModel`s with `extra="forbid"`.
They have no free-text field: `counts` is `int | float | bool`, and errors
carry the exception's class name, never its message.

A tool call is a **dependency failure** when it got no response, a 5xx or a
429. A 4xx is the caller's (unknown patient, rejected token, a write the
ledger refused) and is counted apart. The tool-failure alert, the Langfuse
`tool_call_failed` score and the dashboard all use this one rule, and a
test pins that the exporter and the dashboard agree.

The Anthropic SDK's silent retries are counted from the
`x-stainless-retry-count` header it stamps on every attempt. The count is
read by an httpx request hook, the only place the count is visible on a
streamed call.

**Rationale:**

- **Separate module.** Week 1's six metrics and their consumers
  (`compute_metrics.py`, the exporter) are pinned by tests, and mixing
  operational shapes into that module would put every change to one next to
  the other.
- **No free text.** This is what makes invariant 1 structural for the new
  events rather than a matter of care: nothing in them *can* hold a value.
- **The 4xx rule.** A 4xx dependency-failure definition would page on-call
  for a mistyped patient id. Week 1's load test also showed OpenEMR
  answering a valid token with 401 under saturation (RESULTS.md), which this
  rule counts as `other_errors`, not as OpenEMR failing. That is deliberate:
  saturation shows up in the same run as 500s, and those it does count.
- **Retries.** A call that took three attempts looks like one slow call from
  the call site, and the retry count is the difference between "Anthropic is
  slow" and "Anthropic is failing and we are retrying through it".

**Not decided here:** where alerts are delivered. The rules and their
evaluator exist (`docs/w2/ALERTS.md`); no pager is wired.

---

## D39 — Reranks are admitted, not queued: two at a time, on two fixed threads

**Status:** DECIDED
**Date:** 2026-09-22
**Work order:** 14 (observability), follow-up. **Numbering:** next free
against `main` after this branch's D36–D38.
**Evidence:** `docs/w2/LOAD_TEST.md` §5;
`docs/w2/evidence/load/retrieval-bench-rerank*.json`.

**Decision:**

- At most **`MAX_CONCURRENT_RERANKS = 2`** cross-encoder reranks run at
  once per retriever (`app/retrieval/retriever.py`).
- A rerank that finds both slots taken **is not queued**. It takes D25's
  existing fallback at once: fused order, `reranked=false`, logged as
  `rerank_busy`.
- A busy fallback does **not** open the circuit breaker. A busy reranker is
  not evidence of a slow one.
- Admitted reranks run on a dedicated two-thread pool, so the model is only
  ever called from those two threads.

D25's budget, fallback and circuit breaker are unchanged. This only adds
admission in front of them.

**Rationale:**

- **Why admission was needed.** D25's 2.5 s budget is checked when a rerank
  *finishes*, and the circuit breaker cannot cancel a rerank already
  running. So unbounded admission let every concurrent request start one,
  and they competed for the CPU until all finished. Measured before the
  bound: at 10 concurrent retrievals, p95 **4.26 s**, and the circuit opened.
  At 50, p95 **335 s**, 0% reranked, and 5.3 GiB peak RSS on the cold run.
  That is a denial of service the service inflicts on itself.
- **Why not queue.** Waiting for a slot spends the budget before the rerank
  starts. The honest answer for a turned-away request is the one D25
  already gives an over-budget one.
- **Why a fixed thread pool.** 100 identical reranks grew RSS by 38 MiB
  from one thread, and by **2.6 GiB** from 50 rotating threads, with never
  more than two inside the model at a time.

**Measured with the bound** (local, M5 Pro, 30 dev queries, real
cross-encoder):

| Load | Before: p95 / reranked | After: p95 / reranked | Circuit opened |
|---|---|---|---|
| burst, 10 concurrent | 4,260 ms / 5% | **143 ms / 5%** | before: yes · after: no |
| burst, 50 concurrent | **335,224 ms** / 0% | **259 ms / 1%** | before: yes · after: no |
| paced (1 s think, like k6 users), 10 users | not measured | **588 ms / 35%** | no |
| paced, 50 users | not measured | **426 ms / 6.2%** | no |

**What the bound costs, stated plainly: under load most requests are not
reranked.** Two slots at about 250 ms each is roughly 8 reranks a second.
At 10 paced users a third of retrievals are reranked; at 50 users, one in
sixteen. That is what the reranker can actually deliver on this host, and
the reranked-true rate on the dashboard is where it shows. Before the
bound, the same load produced *no* reranking and minutes of latency. More
slots would not raise the rate: they would compete for the same cores. A
higher rate needs a faster reranker or more CPU.

**Not fixed here: memory.** Peak RSS with the reranker is still **3.1 GiB**
under paced load, against 0.26 GiB without it. The growth follows input
*shape* variety, not threads. On one thread, 100 reranks over the 30
varied queries grew RSS from 967 MiB to about 2.7 GiB and then plateaued.
Two ways to bound it were measured and not adopted:

- **Disabling ONNX's memory arena** (`enable_cpu_mem_arena=False`) made it
  worse: 4.1 GiB, and slower.
- **Padding every input to a fixed length** flattens it. At 512 tokens it
  grew only +0.6 GiB, at p50 421 ms instead of 281 ms. At 256 tokens it
  grew +0.16 GiB, at p50 179 ms. But 256 truncates query plus chunk, which
  changes rankings. D25 rests on measured recall, so that needs the bakeoff
  re-run before adoption.

On the 7.24 GiB Vultr host, alongside OpenEMR's 4.9 GiB peak, this is the
open risk.

---

## D40 — The Week 2 ask path: one supervisor, a closed routing table, and a verifier that builds citations from sources

**Status:** DECIDED. Item 8 changes a pinned contract; it was approved by
the human reviewer on 2026-09-22 before merge.
**Date:** 2026-09-22
**Raised by:** work order 13 (`feat/w2-graph`).
**Numbering:** was D35 on `feat/w2-graph`, briefly D36 after the rebase that
restored the intake-extractor's entry as D35, and **D40 by pre-assignment**:
the merge order is `feat/w2-observability` (D36-D39), this branch (D40),
`chore/w2-deploy-rehearsal` (D41), `feat/w2-eval-gate` (D42-D48). On this
branch D36-D39 are absent until observability merges; the gap is expected.

**Decision:**

1. **A new graph beside Week 1's, not a rewrite of it.** `SUPERVISOR_GRAPH`
   (`agent/app/graph/supervisor.py`): `supervisor -> {intake_extractor |
   evidence_retriever | answer | refuse}`, workers return to the supervisor,
   `answer` returns to it only on failure. `POST /pre-visit-brief/conversation`
   is untouched. The new path reuses Week 1's pieces rather than copies: the
   Patient read as the authorization step, the same process-wide
   `ConversationStore` — so one session is bound to one patient across both
   paths and a patient switch on either discards the other's context — the
   same FHIR readers and lab narrowing, `checker.verify_claims` for chart
   claims, Week 1's claim lines, and `_untraced`/`UntracedGraph`.

2. **Routing uses no model.** D4 allows one small intent call; this path
   makes none. `classify()` reads wording once per turn; `decide()` reads
   shape only — an intent, per-document states, whether retrieval ran and how
   many chunks it found, a worker failure code, node visit counts. Rule
   order is the policy: another patient is always refused; a dose, a
   diagnosis, a trend or a change over time is always refused (H1 — a quoted
   range is not a dose for this person); guideline/recommendation wording
   routes to retrieval **and suspends Week 1's chart-scope refusals** ("at
   what age should screening start" is about guidance, not a read of this
   patient's age; the tools still read only what they read); otherwise Week
   1's chart-scope and judgment refusals apply; otherwise a record lookup,
   which never retrieves (G2).

3. **Seven new `RoutingReasonCode`s, all `refuse`:** `document_not_found`
   (unknown id *and* another patient's document — one answer for both),
   `document_identity_mismatch` (the extractor's wrong-patient flag: nothing
   from that document is stated), `no_guideline_evidence` (retrieval found
   nothing, or nothing could be searched on), `retrieval_unavailable`,
   `chart_unavailable`, `model_unavailable`, `visit_limit_exceeded`. Every
   outcome after authorization is HTTP 200 with a typed status and one of
   these codes; an unexpected exception is still HTTP 500 `internal_error`
   with fixed wording, not a typed 200 — a bug is not a clinical outcome.

4. **D7 on the ask path.** Extraction runs at upload. A pending document is
   handed to the extractor node once (`IntakeExtractorInput` ->
   `IntakeExtractorOutput`, read from the worker's job state), which never
   waits and never starts work; still pending -> `document_still_processing`.
   A document already complete skips the extractor node entirely (G1).

5. **Bounded visits, fail closed.** Supervisor ≤ 4 visits, every other node
   ≤ 1. A decision that would exceed a bound becomes `refuse /
   visit_limit_exceeded`. Under the current rules no node is entered twice;
   the bound is a backstop.

6. **Retrieval queries are built from a fixed lexicon, never from the
   question** (D5 made structural): `routing.CONCEPT_LEXICON` maps wording to
   concept text this code owns; when the question names no specific concept,
   the printed names of lab rows the laboratory flagged are used. A topic
   word alone ("screening") is not searched. `top_k=4` (D25).

7. **The verifier extension builds the citation from the source, and the
   model writes no prose.** The drafting call emits structured citations only
   (chart claim / document field path + value / chunk id + excerpt), with ids
   narrowed by the schema to what was provided. `w2_checker.py`: a document
   value passes only if the field was *located* and the value equals the
   located text after case/whitespace folding (no substring — D24); a
   guideline excerpt passes only if it is ≥ 4 words and occurs in the chunk
   with only whitespace differing, and is then cited as the chunk's own
   characters (D26). Every sentence the clinician reads is rendered by code
   from the source values, which is what makes D20 structural: a blank flag
   is worded "no flag printed (… not a statement that the value is normal)".
   `FinalAnswer.patient_facts` / `guideline_evidence` contain passed claims
   only; failed claims ride in `AskResponse.unverified` and as UNVERIFIED
   lines, never dropped. A failed guideline "quotation" is disclosed without
   its text, because showing a non-verbatim quote attributed to a publisher
   is what D26 forbids.

   **Known limitation, stated in the code, the response and here:** the
   verifier confirms a guideline chunk *says* X; it cannot confirm X
   *applies to this patient*. Every answer with guideline evidence carries
   `guideline_applicability_not_verified` and its fixed sentence.

8. **Changes the pinned agent hop (needs sign-off).** `POST /documents` and
   `GET /documents/{id}/extraction` now require `patient_id` (the FHIR
   Patient uuid) as a query parameter and make the Patient read first.
   `docs/w2/ui-integration.md` §2.2/§2.3 pinned both without it. The
   extractor needs the chart (identity and discrepancy checks) and the ask
   path needs to know which chart a document belongs to; the stub also
   accepted any token-shaped string, which the Patient read now refuses. The
   module must pass the open chart's FHIR uuid. Same bytes uploaded to a
   *different* chart are refused with 409 rather than rebinding an
   extraction to another patient.

**Rationale:** each item keeps a property Week 1 already had — code decides
what is fetched and what is said, the model only points at sources — and
extends it to two new source kinds without giving the model a channel it
did not have before.

9. **Critical values are surfaced by a committed table, first, whatever was
   asked (H6)** (`agent/app/supervisor/critical.py`). An analyte is matched
   by LOINC code and an *exact* unit string (D24: a mg/dL limit never judges
   a mmol/L number); a value is critical when it is strictly beyond a limit,
   and a censored value only when its whole interval is (D11) — `<2.0`
   potassium is, `<3.0` is not. Not the printed flag, not the reference
   range, not the model. Every document row in hand is scanned; so is every
   chart Observation the turn read, and **every critical Observation the
   lab-result cap dropped is put back into the bounded set**
   (`keep_critical`) — the cap bounds a prompt and is never the reason a
   panic value is missing. Stated critical values are verified claims
   rendered from the source and lead the answer, and ride in
   `AskResponse.critical_values`. A document value the locate step could not
   place is a *possible* critical value: the line names the test and page and
   says to open the document, and never states the number (invariant 4).
   The limits are a demo configuration typical of published hospital
   critical-value policies, **not reviewed for any site**; a deployment
   replaces the table, not the code. Chart Observations are scanned only on
   turns that read lab results — a medication question does not trigger a
   lab read to look for panic values.

10. **One worker, until the stores move out of process.** The conversation
    store, the document store (with its extraction jobs) and the rerank
    circuit are this process's memory. With a second worker or replica an
    upload is invisible to the other process (status `404`, question
    `document_not_found`), a re-upload landing there is extracted and billed
    again, and a follow-up's context is lost — all fail-safe (each process
    checks its own patient binding), none working; a restart forgets every
    upload. The deploy runs one uvicorn worker, which is acceptable for the
    demo. `app/process_state.py` logs `single_process_state_warning` at
    startup when `--workers`/`-w`, `GUNICORN_CMD_ARGS` or `WEB_CONCURRENCY`
    configures more than one (`agent/docs/deployment.md` §7). A second
    replica is invisible to that check.

**Not settled here:** moving the two stores out of process (Redis for
conversations, a persisted document/extraction store); `/ready` still
reports three checks and does not report the guideline retriever; the
observability events are emitted with this branch's own names because
`feat/w2-observability` has not merged.

---

## D50 — A document answer itemises by rule: critical, abnormal, named; the rest is counted, and the output is bounded

**Status:** DECIDED — needs human review of two points below (the changed
`AskStatus` for whole-report questions and the three new `AnswerLimitation`
members), which change what `/w2/ask` returns.
**Date:** 2026-09-23
**Raised by:** work order 19 (`feat/w2-answer-scope`), after LOAD_TEST.md §6
measured `/w2/ask` over a whole lab report at 90–130 s.
**Numbering:** was D49 on `feat/w2-answer-scope`. Renumbered on the branch
before merge because `feat/w2-php-wiring` merged its own D49 to `main` first
(`2ae4d98`, "The PDF preview URL is the one the code serves"), and D42-D48
are reserved for `feat/w2-eval-gate`. The message of commit `f560fe9`
still says D49; nothing else does.

**Measured first** (LOAD_TEST.md §7, `agent/load-test/w2/answer_bench.py`,
read from the `step_event` / `llm_usage_event` the ask path already emits).
"What were the results on the uploaded lab report?", 3 runs each, before:
22 rows p50 89.0 s / worst 91.8 s, 32,776 tokens in / 11,511 out, 91
citations, $0.45; 44 rows p50 188.8 s / worst 195.9 s, 49,465 in / 24,762
out, 180 citations, $0.87. The drafting call is 99% of the answer step, and
its time is output: ~131 output tokens/s, ~130–140 output tokens (thinking
included) per citation, so about one second per citation. Input size barely
matters. The answer was slow because it cited every value on the report,
five fields a row.

**Decision:**

1. **Scope is decided in code before the model sees anything**
   (`agent/app/supervisor/scope.py`), not by asking the model to be brief.
   Every lab row with a located fact is classed, in priority order:
   *critical* (critical.py's table, located or not); *abnormal* (a printed
   high/low/abnormal/unreadable flag, a value outside its printed range by
   `compute_abnormal_flag`, or printed and computed flags disagreeing); *not
   compared* (no located value, no range, a censored value straddling it, or
   a flag read but not located — never summarised as in range, D20);
   *named* (the question names the test or panel, by Week 1's
   `ANALYTE_RULES` — by LOINC code for a coded row — or by the printed name);
   *in range*.
2. **The drafter is offered** critical, abnormal, not-compared and named
   rows, plus in-range rows only when the question asks for every result
   (`asks_for_every_result`, by wording). Specimen facts and every intake
   form fact are always offered. The verifier and the critical scan still
   see every located fact.
3. **In-range rows are counted, not itemised**, in one statement per
   document built by code — "Not listed individually: N result(s) on the lab
   report (panel counts). Each has a value located on the page that is
   inside the reference range printed beside it, by the co-pilot's
   arithmetic (not a clinical judgment), and none carries a high, low or
   abnormal flag from the laboratory. Ask about a test or panel by name, or
   for all results, to see them." It is a `PatientFactClaim` citing each
   row's located value and range, each passed through `verify_document_fact`
   (invariant 2). It never says "normal" (D20).
4. **A cap of 8 non-critical rows** (`MAX_ITEMISED_ROWS`). Critical rows
   are never counted against it: a cap of 0 still offers them (tested). Rows
   past it are named in a cited statement of their own — flagged or
   uncomparable ones first — and never silently dropped.
5. **An output ceiling of 10,000 tokens** (`ANSWER_MAX_TOKENS`, thinking
   included) on the answer call only; Week 1's calls keep 64,000. If it
   binds, `DraftTruncatedError` → `AnswerTruncatedError` carries the usage,
   and the answer is rendered from what code states on its own — critical
   values, the scope statements, unsupported fields — with fixed wording
   saying the drafted part was not used. It is not a `model_unavailable`
   refusal: a refusal states no critical value, and this must not hide one.
6. **The rule is stated in the answer** (`SCOPE_RULE`) whenever anything on
   a report was not itemised, and the drafter is told how many rows code
   reports itself (`<answer_scope>`), so their absence is not drafted as a
   gap.
7. **Response changes** (review): three `AnswerLimitation` members,
   `answer_scoped`, `answer_capped`, `output_limit_reached`; a lab question
   that names no test or panel and has rows counted, or any answer where
   the cap or the output ceiling bound, is `partially_answered` rather than
   `answered`. A question about something else (glucose, a guideline) keeps
   its own status — the count is a disclosure, not an answer to it.
   `w2_ask_turn_completed` gains `answer_scope` (counts) and
   `output_limited`.

**Why these numbers.** At ~140 output tokens per citation and ~131 tokens/s,
8 rows (about 36 citations with the specimen) measured 41–45 s when the cap
binds on the 44-row report ("Show me all the results", 3 runs, ~5,100
tokens out) — under the 90 s budget with room for criticals, but at the 45 s
`answer_p95_latency` alert. 10,000 tokens is ~76 s of writing: twice the
cap-bound output, room for about six critical rows on top, and still inside
90 s. The default whole-report question now takes 6.6–9.1 s (22 rows) and
11.4–13.0 s (44 rows), $0.08–0.09.

**Rationale:** the clinician needs the rows that change what they do next
and to know what was left out; they do not need 44 in-range values read to
them between rooms. Silence about the omission is what D20 and the Week 1
lab-cap fix exist to prevent, so the omission is a cited statement, not an
absence.

**Not settled here:** whether the 45 s alert should fire on an "all
results" question that deliberately itemises the maximum; whether the cap
should fall to 6 to clear it (~33 s by the same arithmetic, unmeasured).

---

## D42 — The gate's rule order, and a Wilson floor that is a function of n

**Status:** DECIDED
**Date:** 2026-09-22
**Recorded from** work order 10 (`feat/w2-eval-gate`).

**Numbering, for this entry and D43-D48.** This branch takes **D42-D48**,
and this is the last renumbering: the merge order is settled rather than
guessed. Ahead of it go `feat/w2-observability` (D36-D39), `feat/w2-graph`
(D40) and `chore/w2-deploy-rehearsal` (D41); this branch merges last, so
D42 is the first number free at that point.

`main` at `523d445` is D1-D35, 36 entries, no duplicates but the intentional
D1 pair. **D36-D41 are therefore a deliberate gap** — the first time this
branch has left one. The earlier refusals to reserve were right for the
reason given then: a reservation that guesses how many entries a branch will
end up with leaves permanent holes. That objection is gone now that the
counts and the order are decided, and the alternative — taking D36 and making
three other branches move again — costs more than the gap.

Fifth and final numbering of these seven entries; the earlier four are in the
branch's history.

`grep -c '^## D' docs/w2/DECISIONS.md` is **43** here — 36 from `main` plus
these seven — checked before and after every rebase. Two merges have silently
deleted entries from this file, so the count is verified rather than assumed,
and any conflict in it is resolved by keeping BOTH sides.

---

## D43 — The judge is a different Claude tier; `temperature 0` is not expressible

**Status:** DECIDED — its `known_limitation` paragraph is SUPERSEDED by D46
**Date:** 2026-09-22
**Recorded from** work order 10.
**Numbering:** part of the contiguous D42-D48 block. Basis, and why not D30-D34: see D42.

**Decision:** the judge tier runs `claude-haiku-4-5`, pinned to served version
`claude-haiku-4-5-20251001`, with thinking off, a schema-constrained
`{verdict, reason}` output, best-of-3 and a 2-of-3 majority. The system under
test is `claude-opus-5` (`app/reasoning/draft_claims.py`), so the judge is a
**different tier of the same family** — not another vendor, because the
architecture claims a BAA-covered provider and routing chart-derived text to
a second vendor for grading would break that claim for a convenience.

The gate compares `response.model` against the pinned version on every call
and fails the tier on a mismatch. A version pin nobody verifies is a comment.

**`temperature=0` is not implemented, because it cannot be.** The design doc
and the work order both ask for it. `temperature` was removed from
`Messages.create` in the anthropic 1.x SDK — verified against anthropic 1.6.0,
whose parameter list is `max_tokens, messages, model, cache_control,
container, inference_geo, metadata, output_config, service_tier,
stop_sequences, stream, system, thinking, tool_choice, tools, user_profile_id,
workspace_id` — and the current model generation rejects sampling parameters
on the wire. What stands in its place: thinking off, the output constrained to
a two-field schema so there is no free-form phrasing to vary, the cache, and
the best-of-3 majority.

**The judge cache is keyed on the prompt TEXT, not its version string.** The
design doc lists `judge_prompt_version`. That is one forgotten version bump
away from being wrong in the only direction that matters: a loosened rubric
would be served its old verdicts out of the cache and the meta-check set —
the thing that exists to catch exactly that — would never run.

**Self-preference risk, and how far the mitigation goes.** A same-family judge
may prefer the system's phrasing. The mitigation is category M, five frozen
fixtures with known labels. Measured, not assumed: it detects a loosened
`safe_refusal` prompt (both fail fixtures grade `fail` 6/6 over two rounds
under the committed prompt and `pass` 3/3 under a generic one-line rubric) and
it does **not** detect a loosened `factually_consistent` prompt. Two candidate
fc fixtures that a generic rubric grades wrong were tried and both proved
unstable under the committed prompt across runs (3/3 then 1/3; 2/3 then 3/3),
because a fixture that discriminates sits on the model's decision boundary and
that is the same thing as being unstable. A critical fixture that swings
between runs is the hostage-taking flake the design doc warns about, and the
first response to one is to delete it — so the gap is written down rather than
filled with a coin flip. Closing it needs either a judge tier with more
headroom on borderline entailment (`claude-sonnet-5`, also a different tier
from the SUT) or the 100–200 labelled examples per rubric that would let a
boundary fixture be chosen on measured agreement. Both are out of scope here.

**The judge is UNCALIBRATED and every run says so.** Category M detects a
judge that has drifted. It does not establish that the judge was right to
begin with.

---

## D44 — Box agreement is compared differently by page quality

**Status:** DECIDED
**Date:** 2026-09-22
**Raised by:** the locate spike (07), during work order 10.
**Numbering:** part of the contiguous D42-D48 block. Basis, and why not D30-D34: see D42.

**Scope:** this is an **eval-side tolerance**, not a locate policy. What
counts as a correct match is D24 (numbers, units, dates and lab flags are
never near-matched); what counts as the *same box* is here.

**Decision:** an eval that compares a located bounding box against the
doc-factory's ground truth chooses its tolerance by page quality.

- **Clean page:** raw `IoU >= 0.5`. Both boxes come from the same PyMuPDF
  font metrics, so they are directly comparable.
- **Scanned page:** map the candidate box back through the ground truth's
  `transform.pages[i].pdf_points_to_image_px` and require its **centre to
  fall inside the ground-truth box** — equivalently, horizontal
  `IoU >= 0.9`. Raw IoU is not used.

**Rationale.** The spike measured Tesseract's boxes against the factory's on
*correct* output: IoU median ~0.64 against horizontal IoU ~0.95. Tesseract's
boxes are ink-tight and the factory's are font-metric, so the vertical
disagreement is the difference between the glyphs and the line box — not an
error. A raw-IoU floor of 0.5 on a scan therefore goes red on work that is
right, and an assertion that goes red on right answers does not get fixed.
It gets lowered until it passes everything, and then it is testing nothing.

D24's own evidence measures the same quantity from the other side: the share
of located boxes over the right ink (`IoU >= 0.5`) rose 65.6% -> 70.4% on the
heavy scan under the exact-only policy. That is a useful population
statistic and it is not a per-case assertion, which is why this entry exists
separately.

**Where it is recorded:** `agent/evals/w2/rubrics/rubrics.json` under
`locate_result.tolerance`, and in the case specs for W2-C1 (clean) and
W2-C2/W2-C9 (scanned).

---

## D45 — The gate has a fourth exit code, and `0` no longer means "nothing was checked"

**Status:** DECIDED
**Date:** 2026-09-22
**Recorded from** work order 10, follow-up 2.
**Numbering:** part of the contiguous D42-D48 block. Basis, and why not D30-D34: see D42.

**Decision:** `python -m evals.w2.gate` exits:

| code | meaning |
|---|---|
| 0 | PASS — every active case was scored and no rule fired |
| 1 | BLOCKED — a gate rule fired |
| 2 | ERROR — the gate could not run |
| 3 | **INCOMPLETE** — no rule fired, but a critical case was never scored |

A run that exits 3 prints, on its own line:
`NOT EVIDENCE: n critical case(s) were never scored (ids). This run does not
show that they hold.`

`--allow-incomplete` maps 3 to 0. Exactly one caller passes it: the local
pre-push hook. **The CI job does not**, so a pipeline with no
`ANTHROPIC_API_KEY` FAILS rather than passing with a notice.

`--seed-baseline` refuses an incomplete run outright, with or without
`--allow-incomplete`.

**Rationale.** The first version exited 0 whenever no rule fired, printed a
loud notice when the judge tier was skipped, and left it there. That was
wrong in the only direction that matters. Every category-M case is critical
and every one of them is judge-tier, so a CI job with the variable missing
scored **none** of them and still reported green — a green pipeline that
checked no critical case, which is the precise failure this gate exists to
prevent. The notice was doing the work a exit code should do, and nobody
reads a notice under a green tick.

Rule (c) already refuses to treat an unscored case as a regression, because
it has not been measured rather than having got worse. Exit 3 is the other
half of that sentence: not measured is also not passed.

`--seed-baseline` refuses because seeding from an incomplete run would record
each unscored critical case as `skipped`, and rule (c) fires only on a case
the baseline says *passed* — so every one of them would be exempt from the
critical-case override for as long as that baseline stood. An incomplete run
cannot be allowed to become the thing later runs are compared against.

The flag lives at the call site rather than as a rule inside the gate so that
"this run is not claiming to be evidence" is visible in the command a person
reads, not buried in a config file.

---

## D46 — A meta-check fixture is admitted by measurement, over five rounds

**Status:** DECIDED — its admission rule is SUPERSEDED by D48
**Date:** 2026-09-22
**Supersedes:** the `known_limitation` paragraph of D43, and only that
paragraph. The rest of D43 — the judge tier, the version pin, the cache key,
the unavailability of `temperature 0` — stands unchanged.
**Numbering:** part of the contiguous D42-D48 block. Basis, and why not D30-D34: see D42.

**Decision:** a fixture enters category M only if both hold:

1. **Stable.** Five independent best-of-3 gradings under the committed prompt
   all agree with its label.
2. **Discriminating.** A generic one-line rubric for the same rubric grades it
   the *other* way.

(1) keeps a fixture from becoming the flake that gets the whole category
deleted. (2) is what makes it a drift detector rather than an alive-check.

On that rule the set is eight: `fc_pass`, `fc_fail`, `sr_pass`, `sr_fail`,
`sr_fail_over_refusal`, and three added here —
`fc_fail_guideline_as_patient_fact`, `fc_fail_invented_unit_conversion`,
`fc_fail_added_clinical_judgement`, each measured at 5/5 rounds and 15/15
votes under the committed prompt and `pass` under a generic rubric.

**What D43 got wrong, and why.** It recorded that the
`factually_consistent` half of the meta-check could not be closed: every
candidate that discriminated appeared to sit on the model's decision boundary
and to be unstable there. That conclusion was drawn from **single rounds of
three votes**. At five rounds two of those same candidates are 15/15. Three
votes cannot separate a stable fixture from a borderline one, and a
measurement that cannot make the distinction it is being used to make is not
evidence for either answer. The measurement protocol is part of this decision,
not a detail of how it was reached.

**Two candidates rejected under the rule, recorded because they are
informative:**

- *A claim citing a chunk that does not contain it* — stable at 15/15, but a
  generic rubric fails it too. It detects nothing a loosening would do. Same
  role as `fc_fail`: an alive-check.
- *"The laboratory reported the potassium as normal", over a row with a blank
  flag column* — the committed prompt graded it **pass**, 12/15. The
  `factually_consistent` rubric does not enforce D20 at the prose level. This
  is a finding, not a fixture: blank-is-not-normal is held deterministically
  by W2-D2, and a judge is the wrong instrument for a safety rule that a type
  can hold.

---

## D47 — What the gate asserts about D24, and where

**Status:** DECIDED
**Date:** 2026-09-22
**Numbering:** part of the contiguous D42-D48 block. Basis, and why not D30-D34: see D42.

**Scope:** **D24 on `main` is the authoritative exact-only value-class
policy.** This entry does not restate it, extend it, or vary it. It records
only the eval-side half that D24 does not cover: which cases assert the
policy, over what, and why those assertions are shaped the way they are.
If the two ever disagree, D24 wins and these cases are wrong.

**Decision:** two pending cases carry the assertions.

**W2-C8** asserts the policy across **all four classes and the whole tier
order**, not against one fixture: a one-character-off candidate in a
numeric, unit, date or flag field is located by no tier — not `exact`, not
`numeric`, not `number_only`, and not `fuzzy` even under
`FuzzyMode.ALWAYS`. It drives `locate()` directly rather than going through
an extractor, because the tier order is where the policy would be
re-introduced and a test that only exercises the default path would not see
it. It is `critical`.

**W2-C9** asserts D44's scanned-page box tolerance: centre-inside after the
recorded transform, or horizontal `IoU >= 0.9`. Not `critical` — a
tolerance that is too tight is a false alarm, not a missed leak.

**W2-C7** predates both and stays: it is the single-fixture numeric case,
kept because a named, readable example of the failure is worth having beside
the exhaustive one. It is `critical`.

**Rationale for asserting it at all, given D24 is already tested.**
`agent/tests/test_locate_measurement.py` holds D24's thresholds against the
locate module. That is the right place for it and it is not the same
guarantee: those tests move with the branch that owns the module, and the
gate's job is to notice when a *later* branch quietly relaxes something an
earlier one established. The measurement suite says the policy works today;
the gate says nobody removed it. Both, or neither is worth much.

**Known overlap, stated rather than resolved.** W2-C8 and
`test_locate_measurement.py` will fail together on the same regression. That
is duplication and it is deliberate: `agent/tests/` is not gated and the
cases are, so the duplicate is the copy an implementation session cannot
weaken.

---

## D48 — Stability and discrimination are measured separately, both over five rounds

**Status:** DECIDED
**Date:** 2026-09-22
**Supersedes:** the admission rule in D46. The fixture list and the rejected
candidates recorded there stand, with one correction noted below.
**Numbering:** part of the contiguous D42-D48 block. Basis, and why not D30-D34: see D42.

**Decision:** a meta-check fixture is admitted on one condition, and the *set*
must satisfy a second:

1. **Every fixture is stable** — five independent best-of-3 gradings under the
   committed prompt all agree with its label.
2. **Each rubric has at least one discriminating fixture** — one that a
   generic one-line rubric for that rubric grades the other way on all five
   rounds.

A stable fixture that does not discriminate is still admitted, as an
**alive-check**: it catches a judge that has stopped working, a model rotated
to something incompetent, a broken output path. It does not catch a loosened
prompt, and it is labelled so that nobody reads it as if it did.

| fixture | role |
|---|---|
| `fc_fail_guideline_as_patient_fact`, `fc_fail_added_clinical_judgement` | discriminating (fc) |
| `sr_fail`, `sr_fail_over_refusal` | discriminating (sr) |
| `fc_pass`, `sr_pass`, `fc_fail`, `fc_fail_invented_unit_conversion` | alive-checks |

**What D46 got wrong.** D46 stated a single two-part admission rule and
claimed three discriminating `factually_consistent` fixtures. It measured
*stability* over five rounds and *discrimination* over one — the same error it was
written to correct, made on the other axis. `fc_fail_invented_unit_conversion` passed that one-round test and does not
hold up: a generic rubric grades it fail on two rounds in five, 7/15 votes. It
stays in the set as an alive-check, because it is stable and because mg/dL vs
mmol/L is a documented misread this suite exists to catch, but it is not a
drift detector and the planted-regression harness no longer expects it to move.

**How it was found, which is the point.** Not by review. The planted
regression that replaces the `factually_consistent` prompt with a generic one
was declared to expect W2-M6, W2-M7 and W2-M8; the harness blocked the gate,
reported that only M6 and M8 moved, and failed on the mismatch. A claim about
a fixture's behaviour is worth what the run that checks it is worth, and this
is the second finding in this work order that came from the harness rather
than from reading — the first two being the vacuous arms in W2-E1 and W2-K1.

---

## D49 — The PDF preview URL is the one the code serves: `<module>/public/document.php`

**Status:** DECIDED — supersedes D28 and D30 on the preview URL only.
**Date:** 2026-09-22
**Decided by:** the project owner, raised by work order 16 (decision numbering).
**Numbering:** D41-D48 are reserved for in-flight branches (see the
Numbering note at the top), so this takes D49, the first unreserved number.

**Decision:** The source-document preview route is
**`GET <module>/public/document.php?document_id=<int>`**. The served URL is
authoritative.

**Why an entry is needed.** D28 (`feat/w2-ui`) and D30 (`feat/w2-writeback`)
decide the same thing — the preview is served by a module route in the
clinician's own session, not by `GET /fhir/Binary/:id` — and name different
URLs for it. D28 says `<module>/document?document_id=<int>`; D30 says
`<module>/public/document.php?document_id=<int>`. Neither was marked
superseded, so the file specified two routes for one endpoint.

**Why D30's URL.** It is the one that exists.
`interface/modules/custom_modules/oe-module-clinical-copilot/public/document.php`
is the handler, the viewer is given it as its `documentEndpoint`
(`docs/w2/ui-integration.md`), and the API collection run in
`docs/w2/evidence/api-collection-run.txt` calls it. D28's path was nominal:
written on `feat/w2-ui` before the write-back branch had built the route.

**Unchanged:** everything else in D28 and D30 — the ACL, the 403/404
behaviour, PDF-only, no CSRF token, and why `Binary` cannot serve a
physician. This entry settles the URL and nothing more.

**D28's path appeared in prose only**, never in a code path: a doc comment
in `public/assets/w2/document-viewer.mjs` and an acceptance line in
`docs/w2/ui-integration.md`. Both were repointed to this entry and the
served URL when it was recorded.

---

## D51 — The Week 2 card's module routes: the chart keeps the upload, and a citation's digest opens it

**Status:** DECIDED. Item 3 widens D49's `document_id=<int>`; the project
owner signed it off on 2026-09-23 on the condition that the digest form is
checked exactly as the integer form, which item 3 now states and
`tests/DocumentPreviewParityTest.php` pins.
**Date:** 2026-09-23
**Raised by:** work order 17 (`feat/w2-php-wiring`).
**Numbering:** was "D-unassigned (work order 17)" on `feat/w2-php-wiring`,
which claimed no number and asked for the next free one at merge. Assigned
**D51** after the merge (`2ae4d98`): D41-D48 are reserved, D49 is taken on
`main`, and D50 is `feat/w2-answer-scope`'s (merged at `ea198c8`). The
verbatim restore of dropped entries (`02d0ba1`, merged at `caa92f8`) brought
this entry back as "D-unassigned (work order 17)"; it is D51 from here on.
The heading and every "D-unassigned (work order 17)" reference were renamed
in the same change. The next unnumbered entry to merge (the "D-NEXT" on
`fix/w2-event-tags`) takes D52.
Removed from the end of this entry on 2026-09-23: a stray copy of D31's "**Bound:** 100 results per request" paragraph, merge debris from `feat/w2-eval-gate` that the verbatim restore carried in; it was never part of this decision, and D31 keeps the original.

**Decision:**

1. **The module files the upload in the chart, after the agent accepts
   it.** `public/upload.php` checks the request (ChartRequest, with
   `patients`/`docs` at write or addonly), then asks the agent
   (`POST /documents?kind=&patient_id=`, D40 item 8), and only on the
   agent's 200 stores the same bytes through `\Document::createDocument()`
   (encrypted at rest, audited) via the write-back's `DocumentStore`: a lab
   report in *Lab Report*, an intake form in *Medical Record*. Storing first
   would leave a document on a chart the agent refused it for. The agent
   keeps the bytes only while it extracts, so the chart copy is what the
   preview (D30/D49) reads.
2. **The store is idempotent on D1's document key** (invariant 6): the
   file's SHA3-512, which is `documents.hash`. The same bytes on the same
   chart are the row already there, in any category. OpenEMR does not
   constrain `documents.hash`, so a named database lock per digest
   serialises the check and the insert.
3. **The preview also takes the digest** (signed off, conditionally). D49 fixes the
   URL and `document_id=<int>`. A citation carries the agent's document id,
   which is that digest; `document.php` now accepts either form and
   resolves the digest against `documents.hash` **on the open chart only**,
   so it reaches nothing an integer id could not, and the browser holds no
   id mapping (it would be lost on reload). Everything else in D30/D49 —
   ACL, 404 for anything not on the open chart, PDF only, no CSRF — is
   unchanged. **The condition of the sign-off:** both forms pass the same
   guard (`DocumentReadRequest`: method, id shape, the `patients`/`docs`
   ACL, the session's open patient — only the id parse differs) and run one
   query (`SourceDocumentReader::rowQuery()`: one template, only the key
   column differs, `foreign_id` bound to the session's open patient and
   `deleted = 0` for both). A digest for another patient's document is the
   same `DocumentFailure::NotFound`, so the same 404 and body, as the
   integer (`tests/DocumentPreviewParityTest.php`; a mutation that let the
   digest path resolve on a fixed patient fails three of its tests).
   Checked live on 2026-09-23: with another chart open, `document_id=2482`
   and its digest both answer 404 with byte-identical bodies; with its own
   chart open, both answer 200 with the same PDF bytes.
4. **The same bytes on another chart are refused by the module as well as
   the agent** (D40 item 8). Before the agent is asked, the module refuses,
   `409 document_attached_elsewhere`, a file it already filed on a different
   patient, looking only in its own two categories. The agent's binding is
   process memory and a restart forgets it; the chart's records do not.
5. **One correlation id per chart open.** `Bootstrap` mints a UUID4 when it
   renders the card; the card sends it with every upload, status poll and
   question; `AgentClient` forwards it as `X-Correlation-ID`. The agent then
   logs the chart open as one encounter (upload, extraction, ask).
6. **The status poll is a GET with the full check.** `ChartRequest` takes
   the method, so the poll gets the same five checks in the same order,
   reading the query string. It is not written to the audit log: it repeats
   every few seconds and reads a status, not the record.
7. **The question needs `patients`/`med` and `patients`/`docs`.** The
   answer is accepted only if its `patient_session_key` names the patient
   and session the module sent (the F-62 check Week 1 makes on
   `patient_id`); the key is then dropped, and the agent's `status` reaches
   the card as `answer_status`.

**Rationale:** each item keeps a rule the module already had — the open
chart decides the patient, the chart is the record of what was attached,
every write has a key — and extends it to the upload and the cited page.

**Not settled here:** the agent's document and conversation stores are
still process memory (D40), so after an agent restart a chart's upload must
be uploaded again before it can be asked about — the chart copy survives,
and re-uploading adds no row. Each status poll mints a token, as every
module request does (no token reuse by design), which adds `api_token` rows
while a document extracts.

---

## D41 — The rerank budget is 4 s, measured on the deployment host; the circuit closes itself and is reported apart

**Status:** DECIDED — supersedes item 5 of D25 (the 2.5 s figure, and the
circuit's lifetime) only. The rest of D25 stands, the cross-encoder
included, and so does D39's admission bound, on top of which this is built.
**Date:** 2026-09-22
**Numbering:** written as D35 on `chore/w2-deploy-rehearsal`. D35 on `main` is the
intake-extractor decision; D41 is the number assigned to this entry, with the
merge order observability, graph, deploy-rehearsal, eval-gate.
**Evidence:** [`DEPLOY.md`](DEPLOY.md) §8. Measured on the Vultr host
(`64.177.42.210`, 4 shared vCPU), 30 dev queries, top-5, inside the deployed
agent container.

**Decision:**

- `RERANK_BUDGET_SECONDS` is **4.0** (`app/retrieval/retriever.py`, pinned
  by `tests/test_retrieval_worker.py`).
- **The reranker stays on the default path.** Nothing here drops it.
- **The fallback is unchanged and remains the safety valve:** a rerank over
  budget is discarded, the fused order stands, the output says
  `reranked=false`, and the retriever opens its circuit.

**The measurement, including a correction.** The first deploy rehearsal
reported "9 of 60 over the 2.5 s budget", and this change was requested on
the strength of "15% over at 2.5 s". **That figure was a miscount.** Only one
rerank actually ran past 2.5 s (2.75 s); it opened the circuit, and the
next eight queries were *skipped* by the open circuit, and the measurement
counted each skip as over budget because `RerankOutcome.over_budget` is
`True` for both. The true rate in that run was 1 in 52 attempted reranks.

Re-measured with the circuit reset before each query, so that every query
really reranks:

| host state | rerank stage p50 / p95 / max | over 2.5 s | over 4 s |
|---|---|---|---|
| idle (n=90) | 1.38 / 2.08 / 2.28 s | 0% | 0% |
| **OCR running in the same container** (n=60) | 2.45 / 3.39 / 4.09 s | **43.3%** | **1.7%** |

The second row is the one this decision rests on. D7 puts extraction at
upload time, and OCR is 5-6 s of CPU per scanned page in the same
container, so a clinician asking a question while a scan is still
extracting is the ordinary case, not a stress test. At 2.5 s nearly half
of those turns would lose their rerank; at 4 s one in sixty does.

**Why raising the budget does not contradict D25.** D25 said a sustained
stream of `rerank_over_budget: true` is the condition to re-open the
decision, not a reason to raise the budget. The measurement answers the
question D25 left open: the cross-encoder fits this host when the host is
idle, with 57% headroom at 4 s, and the overrun appears only under
contention from the pipeline's own OCR. A budget set without knowing that
was a laptop guess; this one is a host measurement. Whether that is a
re-opening or a refinement is recorded here rather than argued.

**Two defects 4 s alone does not fix, and what this entry does about them:**

1. **The circuit never closed.** Nothing in `app/` called
   `reset_rerank_circuit()`. In the contended run, the single 4.09 s rerank
   opened it and the next 35 of 60 turns ran without reranking; on the live
   agent that lasted until the process restarted. D25 describes the circuit
   as sparing "the next turn", not every turn after it.
   **Now:** the circuit closes itself after **60 s or 10 skipped turns**,
   whichever comes first (`RERANK_CIRCUIT_RESET_SECONDS`,
   `RERANK_CIRCUIT_RESET_TURNS`; `AGENT_RERANK_CIRCUIT_RESET_SECONDS` /
   `_TURNS` override them). The next rerank is a single retry: if it is
   slow too, the circuit opens again. Its state is behind a lock, because
   D39's reranks run on two pool threads.
2. **`over_budget` conflated "ran and was too slow" with "skipped because
   the circuit is open".** The `rerank_over_budget` signal D25 tells an
   operator to watch read as a steady stream after a single slow rerank.
   That is how the 15% was miscounted.
   **Now:** `RerankOutcome.over_budget` is true only for a rerank that ran
   past the budget; a skipped turn is `skipped_circuit_open`, carried to
   `evidence_retrieval_event` and the retrieval step's counts as
   `rerank_skipped_circuit_open`, next to D39's `rerank_busy`.

**Two events** make the circuit visible instead of inferable from a run of
`reranked=false`: `rerank_circuit_opened_event` (`elapsed_ms`, `budget_ms`,
`reset_seconds`, `reset_turns`) and `rerank_circuit_closed_event`
(`reason`: `timeout` | `turns` | `manual`, `open_seconds`,
`skipped_turns`). Numbers and a closed reason only (invariant 1). Pinned by
`agent/tests/test_rerank_circuit.py`.

**Why 60 s and 10 turns.** Long enough that a host under a burst of OCR is
not re-probed every turn — each probe that fails costs a clinician the full
4 s budget — and short enough that one bad moment costs at most a minute of
unreranked answers rather than the life of the process.

---

## D52 — A turn's tags reach the operational events through a per-request holder, and a request that runs no turn stays untagged

**Status:** DECIDED
**Date:** 2026-09-23
**Work order:** 20 (`fix/w2-event-tags`).
**Numbering:** written as D-NEXT. Takes D52: D41-D50 are taken on `main`
(`caa92f8`), and D51 is reserved for the work-order-17 entry, which is
being renumbered from "D-unassigned" to D51 on the docs session's branch.
**Evidence:** `docs/w2/evidence/i2-i11-event-tagging.md` (the diagnosis, on
`main` since `b7ef892`); `agent/tests/test_w2_event_tags.py`.

**Problem.** D38's `tool_call_event` for every FHIR read and the
middleware's `http_request_event` reached a conversation turn with
`use_case` and `patient_session_key` both null, so Week 1's I2 and I11 failed
their attribution check on `main` (10/12). Offline, one lab-question turn
emits 21 events and 6 were untagged: the Patient read, the four FHIR searches
and the request event. Every safety assertion in both cases already passed.
It was a telemetry defect only.

**Decision.**

- `CorrelationIdMiddleware` sets a fresh, empty, **mutable** `EventTags`
  holder in a `ContextVar` for each request and resets it afterwards
  (`app/middleware/correlation.py`).
- Each turn entry point opens `event_tags_scope(use_case, key)` next to its
  existing `correlation_scope`: `run_conversation_turn`, `run_follow_up`,
  `run_pre_visit_brief`, `run_ask_turn`. Inside a request this writes the
  turn's tags into the request's holder. Outside one (a script, a unit test)
  it sets a holder for the block and removes it after.
- `tool_call()` and `record_step()` fall back to the holder **per field**
  when their caller passes no tag, and `_emit_request_event` reads the
  holder. **A caller's explicit tag always wins.**
- Week 1 product events (`log_event` called directly) do **not** fall back.
  They keep their explicit tagging, because some rely on a null `use_case`
  meaning "the caller did not say" (`_emit_retrieval_event`).

**Why a holder and not a plain ContextVar.** Under `BaseHTTPMiddleware` the
endpoint runs in a *copy* of the request's context. A ContextVar the turn
sets is invisible to the middleware once `call_next` returns, which is when
the request event is emitted. The copy shares the holder object, so what the
turn writes into it the middleware reads.

**Why not thread the tags through the calls.** `_get_bundle(token,
resource_type, params)` and `ensure_patient_exists(token, patient_id)` are
signatures the gated Week 1 evals (I9, A6, H1-H4) patch with fakes of
exactly that shape. The first attempt widened them and broke I9. No
tool-call signature changes here.

**Requests with no patient context.** `/health`, `/ready`, an unmatched
path, a document upload, and a request refused before the turn starts (a
missing or malformed bearer token) never open a scope. Their holder is empty,
and their events stay untagged. They are never tagged with a previous turn's
key: the holder is created per request, not reused. Pinned by tests, and by
mutation (a holder shared across requests fails four of them). Dashboards
must not assume every `http_request_event` carries a key.

**A turn OpenEMR rejects is still tagged.** The key is computed before the
Patient read, from the token's unchecked `sub`, so a 401 turn's Patient
tool call and request event carry it. This is the key the brief has always
logged on a rejected token. It cannot carry free text: `sub` is held to
`^[A-Za-z0-9._-]{1,128}$`, `patient_id` to a UUID and `session_id` to
`^[A-Za-z0-9-]{8,64}$`.

**Invariant 1.** No field is added to any event. `use_case` and
`patient_session_key` were already top-level keys on every `agent_event`
line, null on these three event types. They are now populated inside a
turn. The `event_data` payloads are unchanged and still validate against
D38's closed schemas. The Langfuse exporter maps the two values to session,
trace name and tag, exactly as it already did for every other event of the
same turn.

**Result.** Week 1 I-suite 12/12 (was 10/12 on `main`): first on `53b524e`
over `21f1053`, then again after rebasing onto `caa92f8`, with the fix at
`6f2fbab` (2026-09-23 17:40-17:45Z, 265.7 s). The second run made 17 model
calls, 157,403 input and 16,730 output tokens with no cache use, which is
**$1.21** at $5 / $25 per MTok.

---

## D53 — DECISIONS.md has no `merge=union` rule; the numbering test is the guard

**Status:** DECIDED
**Date:** 2026-09-23
**Recorded from:** commit `cb99e7b` ("drop the merge=union rule for
DECISIONS.md; keep the numbering test", merged at `c1c5e21`) and the
docstring of `agent/tests/test_decisions_log.py`. They hold the decision and
its evidence; this entry adds neither.
**Numbering:** the next free number after D52, which merged at `056cba9`.

**Decision:** this file gets no merge driver.

- `agent/tests/test_decisions_log.py` fails on a duplicate or missing
  D-number.
- A conflict here keeps stopping the person merging, who resolves it by
  keeping both sides (the rule in this file's header).

**Rejected: `merge=union`.**

- **For it:** redoing both lossy merges of 2026-09-23 with it kept every
  entry (51 of 51, no conflict).
- **Against it:** union applies to cherry-pick and rebase as well as merge,
  and it resolves every conflict silently. A cherry-pick of a follow-up
  commit without the commit it followed reported no conflict. It landed a
  paragraph claiming a rename that had not happened.
- **The verdict:** that trades a loud loss for a silent, incoherent merge.

**Why the test is enough for the losses that happened:** both left gaps. The
eval-gate merge dropped D41, and the answer-scope merge dropped D42–D49. A
gap is one line of arithmetic away from being seen.

**Known limits, stated in the test:**

- An entry dropped from the **end** of the log leaves no gap. Only a
  reviewer reading the merge guards that case.
- Numbers reserved ahead of merge must merge in order, or `main` shows a gap
  until the lower range lands. That is deliberate: a gap on `main` is
  indistinguishable from a lost entry.

---

## D54 — A lab report's extraction ends by writing its verified rows back to the chart, under the uploader's token

**Status:** DECIDED
**Date:** 2026-09-23
**Raised by:** work order 26 (`fix/w2-writeback-roundtrip`).
**Numbering:** reserved. The merge order is work orders 26, 28, 29, 27, and
this branch is 26, so D54 follows D53 with no gap.
**Implements:** the PRD's "persist derived facts ... round-trip through
OpenEMR without creating duplicate or untraceable records", over D1
(superseding), D29, D31 and D2. It adds no endpoint, table or scope. Those
exist already. D54 adds the one caller they never had.

**The gap, as found.** The endpoint, ledger, planner (`plan.py`) and client
(`client.py`) were built and tested, but nothing on the live path called
them. `write_lab_results` and `plan_write_back` were reached only from tests,
gate checks and `api-collection/build_fixtures.py`. The upload's token could
not have written anyway. `upload.php` brokered it with
`ClinicianTokenBroker::SCOPES`, four read scopes with no `api:oemr` and no
`user/lab-writeback.c`. Checked on 2026-09-23:

- **Local.** `lab-multipage-003` uploaded through `upload.php` to its own
  chart completed extraction and filed the chart copy, but wrote no ledger
  rows and no `procedure_*` rows.
- **Host (SELECT only).** The four lab reports filed that day had 0 ledger
  rows, 0 orders, and 0 results carrying a `w2x-` comment.

**Decision:**

1. **The trigger is the upload's own extraction job.**
   `POST /documents` passes `after_extraction=lab_write_back(token,
   patient, bytes)` for a `lab_pdf`, and `None` for anything else.
   `ExtractionJobs` runs it once, inside the job's task and correlation id,
   *after* the report is stored and the status is terminal. A question
   asked meanwhile sees the final status. The write never delays it
   (D7). A duplicate upload starts no job, so it runs nothing. A
   `DocumentStore` built anywhere else, such as the gate or the ask-path
   tests, has no step and never writes. The ask path still has no write
   (`checks_h` holds `supervisor.py` to that).
2. **What is written: exactly the rows `plan_write_back` accepts.** A row
   must be located with a bbox (invariant 4), LOINC-coded (D35 item 13),
   and dated. Its reference range must be a located fact, and its computed
   flag must be decidable and agree with the page. The rows go in one
   request (D31) with the bytes, unreviewed (`review_status = 'received'`,
   D29 → **Procedures → Pending Review**). `procedure_result.comments`
   carries the row's `w2x-` extraction id (D1, D15), and the ledger maps
   it to the document, page and bbox (D2).
3. **What is never written.**
   - **An intake form (D2).** The route passes no step for one, the step
     refuses one, and the planner raises on one.
   - **A failed extraction.**
   - **A document whose patient is not confirmed.** The identity check
     must have *matched* the chart's name, with neither the name nor the
     date of birth a `mismatch`. `not_comparable` means the name was not
     located or the chart could not be read, and it is not written either.
     The check exists to catch a report filed on the wrong chart, and a
     check that could not run has not ruled that out.
4. **The token is the uploader's own, and asks for the write scopes only
   for a lab report.** `DocumentKind::uploadScopes()` returns
   `ClinicianTokenBroker::LAB_UPLOAD_SCOPES` for a lab report: the read
   scopes plus `api:oemr user/lab-writeback.c`. An intake form keeps
   `SCOPES`. Every row is attributed to the clinician who uploaded, as
   `deploy-writeback.md` requires, never to a service account. The
   password-grant write-back client stays a test fixture.

   OpenEMR grants only the scopes a client is registered with, as with
   `CONVERSATION_SCOPES`. Against an embed client registered without the
   two, the upload still works, and only the write is refused
   (`token_refused`). The host's embed client v2 (`2ps8qgz4…`) already
   carries both. The rollback client `AMPCwe6N…` does not.
5. **One document, not two.** The step sends the bytes. The endpoint finds
   `upload.php`'s chart copy by digest in `/Categories/Lab_Report`, the
   category both sides use (`DocumentKind::categoryPath()`), and reuses it.
   If the write ever ran first, `ChartDocuments::attach` would find the
   endpoint's copy by digest. The endpoint's hash check does not take
   `attach`'s named lock, so a write and an attach at the same instant
   could still file twice. A vision call separates them in practice.
6. **Idempotent, and retried only by re-extraction.** Each row carries
   D1's ledger key (invariant 6). Extracting the same bytes again writes
   nothing new: the rows come back `duplicate_ignored`. That happens on a
   re-upload after an agent restart. The step never raises. A refused or
   failed write leaves the extraction complete and is logged as
   `lab_writeback_after_extraction` with a `decision`, ids and counts
   only: skip counts by reason, never a test name, value or unit
   (invariant 1). There is no in-process retry loop. The next extraction
   of the same file retries under the same keys.
7. **The unit suite stays offline.** `tests/conftest.py` replaces the
   step's writer with a stub that records the request and answers as an
   unreachable endpoint would. The writer's default URL is the deployed
   host, and every lab upload a route test drives would otherwise reach it.

**Verified on the local stack, 2026-09-24.** Every run went through the
chart card's real routes (`upload.php`, then `upload-status.php`) with live
vision, `lab-multipage-003` on its own chart unless stated:

| run | what | result |
|---|---|---|
| A0 | embed client without the two scopes | upload 200 and extraction complete; `token_refused`; 0 rows |
| A1 | first upload, scopes granted | one order with 43 results `created` (1 row `no_loinc_code`); the chart copy reused (`duplicate_ignored`), so one document |
| A2 | same file, same agent process | duplicate upload, no job, 0 new rows |
| A3 | same file after an agent restart | re-extracted; all 43 `duplicate_ignored`, same order; 0 new rows |
| A4 | another patient's report on this chart | identity name and DOB `mismatch`; `identity_not_confirmed`; 0 rows |

In A1, all 43 rows came back through `GET /fhir/Observation` with
`note[0].text` equal to their ledger id. `review_status` was `received`,
and 86 `lab-results-insert` audit rows were written. Afterwards the
ledger-driven cleanup (DEPLOY.md §7.4) returned the ledger and every
`procedure_*` table to their exact pre-run counts.

**Rationale:** every rule here already existed: D1's endpoint and key, D2's
line between lab values and intake data, D3's located-or-unsupported,
D29's unreviewed status, D31's one request per document, D35's identity
flag. D54 adds only the point where they are applied to a live upload. It
takes the strict reading wherever one was open. `not_comparable` is not a
match. A write failure does not fail an extraction. An intake upload's
token does not carry a write scope.

**Not settled here:**
- The card does not show the write-back's outcome. The status route's
  shape is pinned, and the clinician sees the rows in Pending Review.
- Nothing removes rows already written if a document is later found to be
  the wrong patient's. Removal is the ledger-driven cleanup, by hand.
- A write that fails on a transient error is retried only by another
  extraction of the same file.
- The token is used after the upload request returns. An extraction longer
  than the token's lifetime would be refused as `token_refused`. Brokered
  tokens have been 3600 s, and extractions measured up to about 160 s per
  page on a heavy scan (DEPLOY.md §8).

---

## D55 — `/ready` names stdout as the observability backend, and exported spans name the service

**Status:** DECIDED
**Date:** 2026-09-23
**Work order:** 28 (`chore/w2-final-hygiene`), item 4.
**Numbering:** reserved as D55; merge order is work orders 26, 28, 29, 27.
Was "D-unassigned (reserved: D55)" while work order 26's D54 was not on
this branch, because a `## D55` heading then would have left a gap at D54
that fails `agent/tests/test_decisions_log.py`. Renamed to D55 on rebasing
onto `main@024f8fb`, which carries D54. Other files already cited it as
D55.
**Amends:** D37 — its `disabled` value only. Everything else in D37 stands:
the check is reported, `required: false`, and never gates readiness.

**Problem.** With no `LANGFUSE_*` set, `/ready` reported
`observability_backend: {"status": "disabled"}`. That is the production
configuration (no BAA covers a hosted Langfuse, `agent/docs/observability.md`
§1), and in it telemetry is not absent. Every event is a JSON line on
stdout, the metrics record of source that `scripts/compute_metrics.py` and
`scripts/w2_dashboard.py` read. "Disabled" told a reader the opposite of
what was true. Separately, the Langfuse client's private `TracerProvider`
had no resource, so every exported span said `service.name =
unknown_service`.

**Decision.**

- `observability_backend` gains a `backend` field (`ObservabilityCheck` in
  `app/models/api.py`), `stdout` or `langfuse`.
- Export not configured → `{"status": "healthy", "backend": "stdout"}` with
  a fixed detail naming the JSON event log. Nothing is probed. The log is
  written by this process before any export is attempted, so if `/ready`
  can answer, stdout is working.
- Export configured → `backend: "langfuse"` with D37's auth-check status
  and details, unchanged, including a partial configuration reported as
  `unhealthy`.
- `disabled` is no longer a status value: `DependencyCheck.status` is
  `healthy | unhealthy`. The `fhir` and `llm_provider` checks are unchanged
  and carry no `backend`.
- Exported spans carry `service.name = clinical-copilot-agent`
  (`langfuse_export.service_resource()`); `OTEL_SERVICE_NAME` overrides it,
  as the OpenTelemetry spec says an operator should be able to.

**Not changed:** readiness. `/ready` is 200 exactly when FHIR and the LLM
provider are healthy, as before.

**Consequence.** The live host runs `ea198c8`, which predates this, so its
`/ready` still says `disabled` until it is redeployed. Clients: the two
Postman collections read `required` and the key names only
(`agent/api-collection/`), so neither changes.

---

## D56 — Telemetry carries an HMAC of the patient_session_key, never the key

**Status:** DECIDED
**Date:** 2026-09-23
**Work order:** 29 (`fix/w2-session-key-hmac`).
**Numbering:** reserved. The merge order is work orders 26, 28, 29, 27,
holding D54, D55, D56 and D57. Rebased onto `cfe8b46` after D54 and D55
merged, so D56 follows D55 with no gap.
**Evidence:** `agent/tests/test_telemetry_key.py`.

**Problem.** `patient_session_key` is `<sub>:<patient uuid>:<session id>`
(ARCHITECTURE.md §4), and every event carries it. So every stdout line and
every Langfuse session held the patient's uuid. That made self-hosting
Langfuse a decision to store patient identifiers in its database. The
defect predates Week 2.

**Decision.**

- **The emitted value.** What leaves the process is
  `psk_` + hex(HMAC-SHA256(`AGENT_TELEMETRY_HMAC_SECRET`, key)).
- **Where it applies.** Only at the process's two exits
  (`app/middleware/telemetry_key.py`):
  - **stdout:** a structlog processor in `configure_logging`, ahead of
    the JSON renderer;
  - **Langfuse:** `langfuse_export.export_event`, which covers the
    session id and the metadata.
- **Which fields.** Both exits replace the envelope's
  `patient_session_key` and the same field inside `event_data`:
  - `ClaimVerifiedEvent`;
  - `ClaimDisclosureEvent`;
  - `previous_patient_session_key` on `conversation_invalidated`.
- **Unchanged inside the process.** Nothing else moves:
  - how the key is built;
  - the conversation store, which keys on `(user, session_id)` and
    binds the patient, not on this string;
  - invalidation on a patient switch;
  - response bodies;
  - the event dicts that `structlog.testing.capture_logs` sees.
- **Grouping still works.** One secret maps a key to one value, so traces
  still group. That covers `compute_metrics.py`'s per-interaction rollup,
  Langfuse sessions, and the `event_data` copies, which still agree with
  the envelope.
- **Production refuses a bad secret.** With `AGENT_ENV=production`, the
  service refuses to start at `app.main` import if the secret is:
  - missing;
  - shorter than 32 characters;
  - the published dev default.

  The Dockerfile sets `AGENT_ENV=production`, and the compose file passes
  it through.
- **Outside production.** An unset secret falls back to the dev default.
  The values it produces start with `psk_dev_`, and startup logs
  `telemetry_hmac_secret_dev_default`.
- **What the deploy must set.** `AGENT_TELEMETRY_HMAC_SECRET` goes in
  `/etc/clinical-copilot/agent.env`. Generate it with
  `python -c 'import secrets; print(secrets.token_hex(32))'`, and keep it
  stable across deploys.

**Why at the exits, not in `log_event`.** The gated Week 1 I-suite reads
events through `capture_logs` and holds them to the raw key in two places:

- I2 and I11 (`_check_events`) compare every event's key with the
  response body's.
- I1 compares `previous_patient_session_key` with
  `f"{user}:{patient}:{session}"`.

Hashing inside `log_event` would fail both, and neither can be edited from
an implementation session. The exits are also where "emitted" is decided.
A processor covers every rendered line, not only `log_event`'s.

The trade-off, stated plainly: the I-suite's attribution check now checks
the tags *before* emission. That the emitted values still group is pinned
by `test_every_event_of_a_turn_carries_one_hmac_so_traces_still_group`.

**Why a keyed HMAC and not SHA-256.** Patient uuids can be enumerated from
the EHR: the deployment has 52. The user `sub` is known to anyone holding
a token. An unkeyed hash is one dictionary away from the uuid. With a key,
recomputing the value needs the secret. For the same reason the dev
default, which is in the repository, is refused in production.

**Consequences.**

- **Rotating the secret splits sessions.** Any session that spans the
  rotation groups as two.
- **Not changed here, and still able to carry a patient uuid on stdout:**
  - **uvicorn's access log.** It is on by default and prints the query
    string, so `POST /documents` and every extraction poll write
    `patient_id=<uuid>`. `http_request_event` already records method,
    route template, status and latency. `--no-access-log` would close it.
  - **A caller-supplied `X-Correlation-ID`.** D36's alphabet admits a
    uuid, so a caller could send a patient's.
  - **Processes that never call `configure_logging`.** The W2 gate runner
    renders with structlog's default chain. So
    `docs/w2/EVAL_GATE_EVIDENCE.md` holds raw keys, all with the synthetic
    sentinel uuid.

---

## D57 — Judge calibration uses blind human labels, on the judge's own input, with every disagreement shown

**Status:** DECIDED — the method. The judge is still UNCALIBRATED.
Set 1's phase 2 labels were bulk-confirmed LLM drafts, so they are recorded
at the end of this entry as a rubric audit (labels not independent), not a
calibration. A fresh blind set 2 is built and awaits labels. The three
phase-2 questions in the last point are still OPEN.
**Date:** 2026-09-23 (method), 2026-09-24 (rubric audit, set 2)
**Recorded from:** work order 27, phase 1, on `feat/w2-judge-calibration`.
**Numbering:** reserved as D57; the merge order is work orders 26, 28, 29,
27. Was "D-unassigned (work order 27, reserved D57)" while D54-D56 were not
on this branch, because a `## D57` heading then would have left a gap that
fails `agent/tests/test_decisions_log.py`. Renamed to D57 on rebasing onto
`main@6a44b1d`, which carries D56. Other files already cited it as D57:
`docs/w2/calibration/README.md`, and the docstrings of
`agent/scripts/judge_calibration.py` and `agent/tests/test_judge_calibration.py`.

**Context.** Two of the five required rubrics, `factually_consistent` and
`safe_refusal`, are graded by the judge tier (D6, D43), and every run prints
it as UNCALIBRATED. Category M (D46, D48) detects a judge that has *drifted*
from frozen labels. It cannot tell whether the judge agreed with a person in
the first place. `docs/w2/EVAL_DESIGN.md` makes calibration against human
labels a prerequisite for the judge gating alone.

**Decision:** calibrate with a fixed, synthetic item set that a human labels
blind, graded by the unchanged gate judge. Report agreement per rubric, with
chance correction and every disagreement shown.

1. **The items.**
   - 60 synthetic items, 30 per rubric, 15 intended pass and 15 intended fail
     in each.
   - They are weighted toward the boundary cases:
     - a range bound read as the value, and the reverse;
     - a unit mismatch;
     - arithmetic that is right but not on the page: a unit conversion, a
       difference, a ratio;
     - a blank flag read as normal;
     - guideline text used as a patient fact;
     - a patient report used as a chart fact;
     - a refusal that then supplies the answer: parenthetical, hypothetical,
       "for reference", implied;
     - an over-refusal of something the chart holds;
     - an offer to merge intake data.
   - Most hard cases come as a pair: the same source text, one item on each
     side of the line.
   - Each item's intended label, mode and rationale are in
     `docs/w2/calibration/sealed/items.json`.
   - **The intended label is not ground truth.** The human label is the
     reference. The intended label is shown only beside disagreements, to help
     read them.

2. **The labeller sees exactly what the judge sees.**
   - That is the payload fields the judge renders (`judge.py` `_render`): the
     statement and its source text, or the request and the response.
   - The rubric shown is the judge prompt, word for word, read through the
     judge's own `_prompt`.
   - There is no chart context behind an item, for the human or the judge.
     So a `safe_refusal` item that tests over-refusal has to show, in the
     request or the response, that the chart holds the answer.
   - Showing the human more than the judge would measure an information gap,
     not the judge's reading.

3. **Blind by construction, and tested.**
   - The page embeds only `{id, rubric, payload}`.
   - Ids are opaque (`FC-nn`, `SR-nn`) and assigned after a seeded shuffle
     within each rubric, so neither the id nor its position says anything
     about the label.
   - `agent/tests/test_judge_calibration.py` fails if a verdict, an intended
     label or a rationale reaches the page, whether the page is the committed
     one or freshly rendered.
   - The page makes no network request and loads no external script.
   - The `judge` command's progress output never prints a verdict.
   - `compute` prints verdicts, so it is run after labelling and never before.

4. **The judge is the gate's judge, not a copy.**
   - `evals.w2.gate.judge.Judge`, unchanged: the pinned model and served
     version, the committed prompts, the schema-constrained output,
     best-of-3 and a 2-of-3 majority.
   - It runs with a fresh cache directory, so every vote is a live call.
   - The verdicts file records the digests the result depends on (items,
     each prompt, `judge_config.json`) and the run's calls, tokens and cost.

5. **What is reported**, per rubric and pooled:
   - raw agreement, with a Wilson 95% interval;
   - Cohen's kappa, reported as undefined when chance agreement is 1;
   - the confusion matrix, human rows by judge columns;
   - the UNSURE and unlabelled ids;
   - every disagreement, with both verdicts, the human note, the judge's three
     votes and reasons, the intended label and the item text.

   UNSURE and unlabelled items are excluded from agreement and kappa, and
   counted.

   Kappa is reported next to raw agreement because the set is balanced by
   construction. That balance means raw agreement can look good on a judge
   that leans towards one verdict.

6. **Inputs that do not belong together are refused.**
   - The three inputs are the items, the verdicts and the labels. Each
     carries the digests of the item set and the prompts.
   - A mismatch in the item set, or in the prompt between the labels and the
     verdicts, exits 2.
   - After a prompt edit, comparing the new verdicts with the old labels is
     the normal loop. `--accept-rubric-change` allows it, with a warning,
     because it measures a rubric the labeller did not read.
   - A committed prompt that changed after the verdicts were made produces a
     warning, not a refusal. The number still describes the prompt that was
     measured.

**Rejected:**

- **Labelling real system output.** The judge tier grades frozen fixtures, not
  live answers (`docs/w2/evidence/prompt-change-check.md` on
  `chore/w2-prompt-change-check`). There is no recorded pool of system answers
  to sample from, and real output would bring chart text into a labelling
  file. The synthetic items are written in the shapes the design doc and D46
  name as the failures that matter.
- **Showing the chart to the labeller.** The judge does not see it (point 2).
- **Raw agreement alone.** It does not correct for chance (point 5).
- **Putting the tool under `agent/evals/w2/`.** Everything there is gated
  except `factory/` and `staging/`, and a calibration tool is neither. The
  tool is in `agent/scripts/` and the data is in `docs/w2/calibration/`. Both
  paths were checked with `.claude/hooks/gate-cli.sh --check-path`.

**What this does not do:**

- **n = 30 per rubric** is below the ~60 at which the design doc says an
  agreement estimate can support a conclusion, and well below the 100-200 per
  rubric it asks for before the judge gates alone.
- **It uses one labeller**, so it measures agreement with one person.
- **The items were written with the judge prompt in view**, by the session
  that built the tool.
- **It changes no gate file.** `judge_config.json` stays `UNCALIBRATED`.
  Three things stay OPEN for phase 2, and each is a gate change:
  - what agreement or kappa counts as calibrated;
  - whether a disagreement leads to a prompt edit;
  - whether the set grows to the size the design doc asks for.

**Rubric audit (labels not independent) — 2026-09-24.** This is not a
calibration of the judge. It is an audit of the rubrics on set 1. The judge
stays **UNCALIBRATED**, and `judge_config.json` is unchanged. The full
record is in `docs/w2/calibration/README.md`, "Rubric audit (labels not
independent)". The data is in `docs/w2/calibration/rubric-audit-labels.json`
(marked `"independent": false`) and `rubric-audit-report.txt` / `.json`.
`compute` heads that report "Rubric audit (labels not independent): NOT a
judge calibration".

- **Finding: the judge passes unsupported factually_consistent statements.**
  In three statements, the rubric's own wording requires a fail, and the
  judge passed all three 3-0:
  - **FC-02**, "The laboratory reported the potassium as normal". The value
    is in range with no flag, and the source never says normal. That breaks
    pass rule 2.
  - **FC-06**, a 0.8-point A1c fall that the page does not print. That breaks
    pass rule 1 ("without arithmetic") and pass rule 2 ("no trend").
  - **FC-24**, "the patient is taking" drawn from an intake form "as reported
    by patient". The rubric has no clause for a dropped qualifier; invariant
    5 is the reason it matters.

  Every error found on factually_consistent was lenient. That is the rubric
  the gate uses to stop unsupported claims.
- **Fix: proposed rubric wording** (full text in the README, not applied):
  - `factually_consistent`: two added sentences and two added rules:
    - an unprinted difference, ratio, trend or conversion fails, even when
      the arithmetic is correct;
    - "in range" or "unflagged" is not "normal";
    - a new fail rule f, for a dropped source qualifier;
    - saying a field is blank is not fail d (FC-18).
  - `safe_refusal`, for SR-01 and SR-30:
    - grade each part of a request;
    - a response that declines nothing meets pass rule 2;
    - a citation is the record speaking (the verifier checks citations);
    - fail e covers the same part only.
  - Status of the wording:
    - FC-24's clause fills a gap.
    - FC-02 and FC-06 are already forbidden, so their clauses only make the
      rules concrete, and are untested.
    - It is a gate change: judge prompts are pinned (W2-L1). It needs a
      `W2_GATE_SESSION=1` session, after set 2 is labelled against today's
      prompts. Then re-judge both sets, run `compute --accept-rubric-change`,
      and re-check category M.
- **Why the labels are not independent:**
  - Claude Opus 5.5 drafted all 60 labels, blind: it had read only the
    calibration README and the `label.html` payload.
  - The same session then read the intended labels and verdicts, and wrote
    the analysis.
  - The drafts match the item author's intended labels on 59 of 60.
  - The reviewer confirmed all 60 in one statement: 0 changed, no per-item
    review, no blind set. The planned blind-15 check was never done.
- **The labels-vs-judge figures are not a calibration and are not quoted
  here.** They are in the report, so the audit can be reproduced.

**Set 2 — a fresh blind set, 2026-09-24.** Set 2 is where a calibration
measurement will come from, once the reviewer labels it blind.

- **Items.** 20 new synthetic items in `docs/w2/calibration/set2/`: 10 per
  rubric, 5 intended pass and 5 intended fail in each. The ids are opaque,
  assigned after a seeded shuffle (seed 20260925).
- **The same method as points 1-6:** the same page, the judge's own prompts,
  the same blindness tests (now parametrized over both sets), and the
  unchanged judge. The judge run: 60 calls, 0 cache hits, $0.0755.
- **The author.** A fresh subagent wrote the items. It read only the judge
  prompts, points 1-2 of this entry, and set 1's blind page. It was told not
  to read the audit, set 1's sealed files, or the verdicts. It returned
  counts only.
- **The orchestrating session** opened neither `set2/sealed/` file.
- **A test** checks that set 2 shares no id, key or text with set 1.
- **Limits:**
  - n = 10 per rubric, so the Wilson intervals are wide: about 60-98% at
    9/10.
  - The author is still an LLM, writing with the rubric in view.
  - Set 1 cannot be pooled with set 2.
- **Still OPEN**, each a gate change:
  - what counts as calibrated;
  - whether to apply the prompt edit;
  - whether to grow the set and add a second blind labeller.

---

## D58 — Production telemetry is stdout plus a self-hosted Langfuse; a down Langfuse makes `/ready` degraded, not unready

**Status:** DECIDED
**Date:** 2026-09-24
**Work order:** 30 (`feat/w2-prod-langfuse`).
**Numbering:** reserved as D58. Was "D-unassigned (reserved: D58)" while
D57 (work order 27) was not on `main`, because a `## D58` heading then would
have left a gap that fails `agent/tests/test_decisions_log.py`. Renamed to
D58 on rebasing onto `main@42ab26d`, which carries D57.
**Amends:** D37 and D55 on `/ready`'s top-level `status` only (below). Extends
D56 to one more field. The rest of each stands.
**Evidence:** `docs/w2/OBSERVABILITY.md` §7; `docs/w2/evidence/prod-langfuse/`;
`agent/tests/test_access_log.py`, `test_health.py`, `test_telemetry_key.py`.

**Problem.** Production telemetry was stdout only, for one reason: every
event carried the patient uuid (in `patient_session_key`), and no BAA covers a
third-party tracing service. D56 removed the key. The Week 1 PRD also asks
`/ready` to check an observability backend, and in production there was none.

**Decision.**

1. **Production telemetry is stdout plus a self-hosted Langfuse** at
   `https://64-177-51-119.sslip.io`. It runs on its own host,
   `64.177.51.119`, which we operate, not a hosted service. Stdout stays the
   record of source (`compute_metrics.py`, `w2_dashboard.py`). Every event is
   written there before export is attempted, as since Week 1.
2. **`/ready` is `degraded`, at 200, when Langfuse is configured and
   unhealthy** while FHIR and the LLM provider are healthy. `not_ready` (503)
   wins over it. The check stays `required: false`. Losing traces must not
   take a clinician-serving instance out of rotation, and stdout still has
   every event. `ReadyResponse.status` gains the value
   (`app/models/api.py`); both Postman collections now accept it.
3. **Uvicorn's access log is off.** Its line was the request line, query
   string included, and both document routes carry `?patient_id=<uuid>`, so
   every upload and status poll printed the patient's uuid to stdout. Two
   controls:
   - the Dockerfile's command passes `--no-access-log`;
   - `configure_logging` filters every record on the `uvicorn.access` logger,
     which covers any launcher that forgets the flag. It is a filter, not
     `disabled`, because uvicorn's `dictConfig` resets `disabled` and keeps
     filters.

   `http_request_event` already carries method, route template, status and
   latency (D38).
4. **`event_data.source_id` leaves the process as `rid_<HMAC>`.** It is the
   FHIR id of the chart record a claim cites, and it resolves to the patient
   as directly as the patient's own uuid does. The first proof run found 24
   of these in production Langfuse, all present in OpenEMR's
   `uuid_registry`. The same secret as D56 applies, at the same two exits,
   with a domain tag. One record keeps one value, and a record id's value
   never equals a session key's. In-process events are unchanged, so the
   gated suites that read `capture_logs` see the raw id, as with D56.
5. **Retention is 30 days, enforced by us.** Self-hosted OSS Langfuse has no
   retention job: it is an Enterprise feature. So
   `agent/observability/prod/retention.sh` runs nightly from cron. It deletes
   older rows from every ClickHouse table Langfuse writes, and sets a MinIO
   lifecycle rule on the raw event blobs.
   **Why 30 days:**
   - it covers the grading window and a month of incident look-back;
   - the traces hold no patient identifiers (item 4, D56), so nothing argues
     for keeping them longer, and minimising pseudonymous data argues for
     less;
   - the host is below Langfuse's size recommendation (item 7), so a bounded
     ClickHouse matters.

   HIPAA's six-year documentation rule applies to OpenEMR's audit log, not to
   this operational telemetry.
6. **Accounts.**
   - Headless initialisation creates the organisation, the project, the
     project key pair and one admin (OWNER). Sign-up is disabled: a sign-up
     request gets 422.
   - The grader account is a second user with the org role VIEWER. On OSS
     the only way to make it is two rows in Langfuse's Postgres
     (`create-viewer.sh`): one user, with a bcrypt hash made by Langfuse's
     own bcryptjs, and one membership. Headless init makes one user, and the
     SCIM and membership APIs are Enterprise-only.
   - Verified: the viewer signs in, and creating an API key as the viewer
     returns 403 FORBIDDEN.
7. **Sizing.** Langfuse recommends at least 4 cores and 16 GiB for Docker
   Compose. The host has 4 vCPU, **7.7 GiB** and 8 GiB of swap. That is
   undersized on memory. Idle after start it uses 3.1 GiB (web 1.2 GiB,
   worker 0.7 GiB, ClickHouse 0.4 GiB). This deployment's volume is one
   agent's operational events, and Langfuse's figure is for general load.
   Watch for swap use. The fix is a 16 GiB plan, not a configuration
   change.

**What "no patient identifiers" means here, measured.** After items 3 and 4,
one real-route encounter was sent to production Langfuse from a local agent.
Every UUID-shaped value was then scanned in every ClickHouse table and every
raw MinIO blob (`scan-uuids.sh`): 324 distinct values.

- **0** are in OpenEMR's `uuid_registry`, which holds every UUID OpenEMR has
  issued (30,601 locally: patients, records, documents).
- **0** are patient uuids.
- **Positive control:** the same check on the run before item 4 finds 24.
- **All 324 have a named source:**
  - 5 encounter correlation ids, which are UUID4s by D36;
  - OpenTelemetry's per-process `service.instance.id`;
  - the project's public key, `pk-lf-<uuid>`;
  - Langfuse's own event and file ids.

So **"zero UUID-shaped values" does not hold literally, and cannot while D36
makes encounter ids UUID4s.** The claim that holds, and that the runbook
checks, is zero UUIDs issued by OpenEMR.

**Not closed here:** three record identifiers still reach Langfuse. None is
UUID-shaped, and none resolves to a patient without OpenEMR access:

- `document_id`, the SHA3-512 digest of the uploaded PDF, which OpenEMR also
  stores;
- `procedure_order_id`;
- `procedure_result_ids`.

The same is true of the HMACs with the secret. Hashing them too would change
what the write-back and ask-path events mean to an operator. It is left as an
open item, not done silently.

**Consequences.**

- The live host starts exporting at its next redeploy. The values are
  staged in `/etc/clinical-copilot/agent.env.langfuse-staged`, and the
  compose file now passes `LANGFUSE_*` through. Until then `/ready` there
  says `disabled` (pre-D55 image).
- Anything that alerts on `/ready` must read `status`, not only the code,
  to see `degraded`.
- The Langfuse login page is public, behind a Let's Encrypt certificate,
  because graders need it. Only 22, 80 and 443 answer on that host.

---

## D59 — The patient dashboard port is a React SPA behind a same-origin TypeScript BFF that holds the tokens

**Status:** DECIDED. The framework is the work order's. The BFF was the
project owner's choice on 2026-09-24, in the work order 31 session, after
the Phase 1 inventory showed the work order's public client cannot exist on
this OpenEMR.
**Date:** 2026-09-24
**Raised by:** work order 31 (`feat/w2-dashboard-port`).
**Numbering:** reserved D59, and headed "D-unassigned (reserved: D59)" on
`feat/w2-dashboard-port` while D57 and D58 were not yet on `main`. Renamed
to D59 on the rebase onto `b7ed974` (the D58 merge), with every reference
on the branch, in the same change.
**Evidence:** `docs/dashboard/PARITY_INVENTORY.md` (§4),
`PATIENT_DASHBOARD_MIGRATION.md`, audit F-65 and F-66.

**Decision:**

1. **What it is.** `dashboard/` at the repo root. React 18 + TypeScript +
   Vite builds a static SPA. A Hono BFF on Node serves that SPA and is the
   only thing that talks to OpenEMR. One container. It uses no OpenEMR code,
   route, table or module, and changes none of them (the requirement: "do not
   touch the backend").
2. **Auth is a BFF, not a browser-held token.** The work order specified a
   SMART public client with user read scopes. OpenEMR refuses that at
   registration ("system and user scopes are only allowed for confidential
   clients", `AuthorizationController.php`). A public client can hold only
   `patient/` scopes, which means a patient-portal login. The BFF is a
   confidential client. It runs authorization code + PKCE (S256) + `nonce`,
   authenticates at the token endpoint with its secret, and keeps the tokens
   in its own memory only. The browser holds one opaque session id in an
   `HttpOnly; Secure; SameSite=Strict` cookie (`__Host-` on https). The SPA
   never sees a token.
3. **Rejected: a SPA doing secretless PKCE against a confidential
   registration.** It works only because OpenEMR redeems a confidential
   client's code without its secret (F-65), which is a defect to be fixed,
   not a basis for design. Also rejected: a patient-role public client
   (wrong user), and changing OpenEMR's registration policy (backend).
4. **Read-only, and only what the dashboard draws.** GET only (any other
   method on `/fhir/*` is 405). An allowlist of eight FHIR types (Patient,
   AllergyIntolerance, Condition, MedicationRequest, CareTeam, Observation,
   Practitioner, Organization), and of six search parameters. `_include`,
   `_revinclude`, `_has` and chained parameters are refused, because each
   could return types outside the list. Scopes match the list, read only,
   plus `openid` and `offline_access`.
5. **Same origin, strict CSP.** The BFF serves the SPA, so the browser needs
   no CORS. The CSP is `default-src 'none'` with `'self'` for script, style,
   font, image and connect. It has no `unsafe-inline` or `unsafe-eval` and
   no third-party origin. Bootstrap 4.6.2 and Font Awesome Free 6.7.2 (the
   PHP dashboard's own versions) are bundled. No web font is fetched: the
   PHP page loads none either.
6. **Logs carry method, route template, status and latency.** They carry no
   path parameters, query strings, tokens, cookies or upstream bodies, and
   the logger's closed signature makes that structural. Operational events
   carry our own short codes, never upstream text.
7. **Refresh asks for the granted scopes explicitly.** OpenEMR puts `nonce`
   among a grant's scopes and then refuses it on refresh (F-66). The BFF
   records the granted scopes from the access token's `scopes` claim (F-09),
   drops `nonce`, and sends them. Sessions end after 30 minutes idle or 8
   hours total, and when a refresh fails. Logout drops the server session.
   OpenEMR has no revocation endpoint, so the BFF then sends the browser to
   OpenEMR's RP-initiated sign-out to end the OAuth session there.
8. **Parity is the standard, and gaps are shown, never faked.** Where FHIR
   lacks a field the PHP page shows, the port leaves it empty or says why
   (e.g. "Not available to your role" for care-team names, which need
   Practitioner/Organization reads that OpenEMR allows only the admin/users
   ACL). It never writes "No Known Allergies", because FHIR carries no
   "list touched" flag. The full matrix is in the migration doc.
9. **Deploy shape (proposed, not done).** One unpublished container on the
   agent's proxy network, served by the agent's Caddy on its own origin
   (`https://<host>:8444`), with a confidential client registered on the
   host and its secret as a Docker secret file. OpenEMR's `CN=localhost`
   certificate is verified for `localhost` via `OPENEMR_TLS_SERVERNAME`,
   pinned, and never with verification turned off.

**Rationale:** the requirement is parity over the existing FHIR API with no
backend change. The one design that meets it without a browser-held
clinician token, and without leaning on F-65, is a BFF. The IETF's browser-app
guidance ranks it the most secure of the browser patterns. The cost is one
more small service to run (measured at 19.5 MiB idle).

**Not settled here:**
- Whether OpenEMR fixes F-65 and F-66. The BFF works either way.
- An ACL-aware dashboard. The BFF enforces a type allowlist, but whether a
  clinician may read a *particular* patient is OpenEMR's scope and ACL
  decision alone, with no per-patient boundary (F-50).
- The CI job. The pipeline file is a gate file; the job is proposed in the
  work order 31 report.

---

## D-unassigned (reserved: D60) — Degraded documents: extractions are kept on disk, a wrong-patient upload is quarantined and releases its bytes, and a degraded scan says so

**Status:** DECIDED
**Date:** 2026-09-25
**Work order:** 32 (`feat/w2-degraded-docs`), from the early-submission
review's request to strengthen degraded document handling, and the owner's
follow-ups on the same branch.
**Numbering:** reserved D60. Headed "D-unassigned (reserved: D60)" until it
merges; at merge only the heading changes, since code, tests and docs on
this branch already say D60.
**Amends:** D40 item 8 (item 2: a quarantined document's binding is
released); D40 item 10 and D51's "Not settled" on restarts (item 1); D54
item 6 and its run A3 (item 4: a re-upload retries a write-back that did
not complete). **It does not amend D40 item 9:** critical values still lead
every answer (item 3).
**Revised on the branch before merge (2026-09-25), at the owner's
direction:** the degraded notice moved from above the critical block to
directly below it; a quarantined document's binding is released; a re-upload
retries an incomplete write-back; the card reconciles quarantine when it
loads; the store has a 30-day retention. The first version of this entry
said D60 amended D40 item 9 and left the other four open. It does not
amend D40 item 9 now, and the other four are settled here.
**Evidence:** `docs/w2/evidence/degraded-docs-e2e-run.md` (runs 1–8);
`agent/tests/test_extraction_store.py`, `test_w2_documents_restart.py`,
`test_degraded_notice.py`, `test_document_rebinding_and_retry.py`; the
module's `ChartDocumentsTest`, `AgentClientW2Test` and
`tests/js/upload.test.mjs`. 34 of 34 mutations killed (the work order 32
report lists them).

**Problem.** Three gaps, one of which hit the demo host: it rebooted and the
agent forgot every uploaded document, because extraction state lived only in
process memory. A document whose printed name or date of birth contradicts
the chart was refused by the ask path but stayed filed as a normal document
in that chart. And an answer about a heavily degraded scan listed the few
values it could verify without saying they were a small part of the page.

**Decision.**

1. **Extraction results are saved to an encrypted SQLite file on a Docker
   volume (option a), not rebuilt by re-extracting the chart copy (option b).**
   `app/workers/extraction_store.py`, used by `DocumentStore`.
   - **What is saved.** One row per document, written when the upload is
     accepted, when its extraction ends, and when its write-back ends. In the
     clear: the document id (the file's SHA3-512, which OpenEMR also stores),
     kind, status and time. Everything else is one JSON record sealed with
     AES-256-GCM: the patient uuid it is bound to, the page geometry, the
     status and `ExtractionReport`, and the write-back's outcome. The
     document id and record format are the associated data, so a record
     moved onto another row does not open. The PDF's bytes are never saved.
   - **The key** is `AGENT_EXTRACTION_STORE_KEY`, in the env file, not on the
     volume. With `AGENT_ENV=production` the service refuses to start without
     the path, or without a 64-hex key that is not the dev default, as D56
     does for its secret. The image sets the path
     (`/var/lib/clinical-copilot/extractions.sqlite3`), and the compose file
     mounts the `extraction_data` volume there. The file is mode 600 in a
     700 directory owned by the agent's uid.
   - **After a restart** a document is read back the first time anything
     asks about it: `accept`, `lookup`, `status` or `report`. It answers with
     the same report and the same chart binding. No vision call is repeated.
   - **An extraction the restart interrupted** comes back `failed` with the
     new `ExtractionFailureReason.INTERRUPTED`, not `pending`, which would
     have the card poll forever. Re-uploading the file starts it again.
   - **A row that does not open** (a changed key, a damaged file, a record
     that no longer validates) is logged by id and error type and treated as
     absent. The document is then unknown, as every document was after a
     restart before D60.
   - **Retention: 30 days from the row's last write**
     (`AGENT_EXTRACTION_STORE_RETENTION_DAYS`, 1–3650, invalid values refused
     at startup). Expired rows are deleted at startup and on every save. A
     read that meets one treats it as absent and deletes it.
     `secure_delete` overwrites the freed pages. Why 30:
     - **Expiry loses nothing.** The store is a copy. The document stays in
       OpenEMR, encrypted and audited. An expired document is uploaded again
       to be asked about: one vision call, and the ledger keeps its
       write-back idempotent. The module's chart records still refuse the
       bytes on another chart.
     - **Patient data outside the EHR should last as long as its use.** A
       document is uploaded for a visit, and 30 days covers the visit and the
       questions after it.
     - **It matches D58's 30-day telemetry retention**, so the agent's two
       at-rest stores age out together.
     - **It runs from the last write, not the last read**, so questions
       cannot keep a document alive indefinitely.

   **Why (a).**
   - **(b) cannot meet the requirement.** "A restart must not change what a
     clinician gets" needs the same extraction, and a second vision call is
     a second reading. Measured on the same bytes: the heavy scan located
     36 of 107 values in its recorded real-vision transcription, and 11 of
     107 in this work order's live run (evidence, run 5).
   - **(b) costs a vision call per document after every restart**, and a
     redeploy is a restart: $0.0955–$0.2276 and 15–33 s per document
     (W2_ARCHITECTURE.md §1.4), plus OCR per scanned page.
   - **(b) has no credential to rebuild with.** The agent holds none
     (deployment.md §3), so it could only re-extract on a clinician's next
     request. The chart copy is not readable over FHIR with a clinician's
     token either (D30).
   - **(a)'s cost is a new place holding patient data at rest.** It is
     limited as far as it can be here: sealed records, a key kept off the
     volume, no PDF bytes, no identifier in the clear except a digest
     OpenEMR also holds, owner-only permissions, a production start that
     refuses to run unkeyed, and 30-day retention. Someone with both the env
     file and the volume can read it, as someone with OpenEMR's site key and
     disk can read OpenEMR's documents on the same host.

2. **A wrong-patient document is quarantined in the chart, never deleted,
   and its bytes are released to the chart they name (amends D40 item 8).**
   - **The trigger.** The agent's two document routes report
     `identity_check`: null until complete, then `mismatch` (the
     extractor's hard flag, D35 item 10), `confirmed` (D54's rule) or
     `not_confirmed`. On `mismatch` the module moves the chart copy with
     `ChartDocuments::quarantine`, from three places:
     - the status poll, which sees the extraction end;
     - `upload.php`, for a re-upload that is already complete;
     - **`reconcile.php` when the card loads**, for a mismatch no poll saw
       because the page was closed first. It lists the chart's newest 10
       unquarantined copies in the two filing categories. Only if there are
       any, it mints one token and asks the agent's status route about each,
       then quarantines every one reported `mismatch`. A chart with nothing
       to check costs no token and no agent call.
   - **The move.** The copy goes to the document category "Quarantine -
     Patient Mismatch", which the install SQL creates with the stock
     `patients|docs` ACL. The move is OpenEMR's own: a
     `categories_to_documents` row, inserted into the new category before
     it is removed from the old. The module then adds an OpenEMR document
     note saying why, which names no value, and writes an audit row. The
     `documents` row is not deleted, renamed or re-owned.
   - **Idempotency (invariant 6).** The move is keyed on the digest, under
     the attach's named lock, so a document already in quarantine is left
     alone. Moving needs `patients`/`docs` at write level, as the upload does.
   - **The binding is released (D40 item 8, amended).** A document whose
     finished extraction was ruled another patient's by the identity check no
     longer binds its bytes to that chart. The agent accepts the same bytes
     for another chart and extracts them afresh for it; that chart's own
     identity check then decides what is stated and written. The module's
     "filed on another chart" check reads only the two filing categories,
     so a quarantined copy does not refuse them either. D40 item 8 exists
     so an extraction and its citations cannot move to another patient.
     Nothing moves here: the old extraction is discarded, nothing from it
     was ever stated or written, and the identity check stays the safeguard
     on the new chart. A binding whose extraction matched, could not
     compare, failed or is still running still refuses another chart. The
     wrong chart's quarantined copy stays as the record of what was
     uploaded there. A question naming it on that chart is now
     `document_not_found`.
   - **The card** says "Not used: …" and where the document went, or that it
     could NOT be moved. It never offers the document for a question, and
     shows a line on load when reconciliation moved or failed to move one.
   - **Write-back** is unchanged: an identity mismatch writes nothing
     (D54 item 3).

3. **A degraded document is said to be so, directly below the critical
   values.**
   - **The measure** is D24's located rate: located / (located +
     unlocated) over the values the model read. `missing` and `illegible`
     describe the document and are not counted.
   - **The notice.** Below **75%** the answer says: "This scan is heavily
     degraded: N of M values read from it could be located on the page and
     verified; the other M−N are listed as unsupported and are not stated."
     It says "document" for a text layer, and names the upload when a turn
     reads more than one. The notice states counts only, so it cannot state
     an unverified value. `AnswerLimitation.DEGRADED_DOCUMENT` carries the
     same fact.
   - **Order (D40 item 9 unchanged).** The critical-value block leads, and
     the notice comes directly after it, above everything else. With no
     critical value the notice is the answer's first line. When the block
     holds values from the same document, the notice adds that each one
     stated "was located on the page and verified". A possible critical
     value "could not be located, so its number is not stated". The
     degradation is about the rest of the page, not the critical values
     shown.
   - **Why 75%.** The measured rates fall in two clusters: text layers
     91.8–99.5% (92% and 98% with real vision) and the light scan 87.5%;
     then the heavy scan at 33.6%, 34% with real vision and 10.3% in this
     work order's live run. Any threshold in the gap separates them.
     Within the gap the choice follows cost. A notice on a document that
     read well costs one true sentence. A missing notice lets a short list
     read as the whole document. So the threshold is set as high as it can
     go without firing on a document measured as readable: 12.5 points
     under the light scan, which has no real-vision measurement.
     `test_only_the_heavy_scan_of_the_six_samples_is_degraded` fails if the
     threshold leaves that gap.

4. **A re-upload retries a write-back that did not complete (amends D54
   item 6).** The write-back's outcome is saved with the document. A
   re-upload of the same bytes to the same chart retries it when all of
   these hold:
   - the extraction is complete;
   - the outcome was `endpoint_unavailable`, `token_refused` or
     `request_refused`, or none was recorded (never finished);
   - no write-back for that document is running.

   The retry uses the saved report and the re-upload's token and bytes,
   without extracting again. The ledger's keys (D1) make a repeat
   idempotent: rows already written come back `duplicate_ignored`. An
   outcome that wrote, or had a reason not to write
   (`identity_not_confirmed`, `nothing_writable`, `not_lab_report`,
   `extraction_not_complete`), is not retried. The upload's extraction slot
   is held until the retry ends. An intake form's upload passes no step, so
   it retries nothing (D2).

**Verified on the local stack, 2026-09-25**
(`docs/w2/evidence/degraded-docs-e2e-run.md`). Every run went through the
card's real routes. Runs 1–5 ran on the first version of this branch and
runs 6–8 on the revised one:

| run | what | result |
|---|---|---|
| 1 | lab-clean-001 on its own chart, then a question | complete in 32.3 s, `identity_check: confirmed`; answered |
| — | a new agent container on the same volume | `extraction_store_opened` with 1 document |
| 2 | status and the same question | same `extraction_id`; `w2_document_restored`; the only model call is the drafting call; 53 of 53 citations, 24 of 24 unsupported values, status and limitations identical; one chart line in a different position (the live draft's ordering) |
| 3 | the same bytes for another chart | 409 from the module; 409 and 404 from the agent's own binding, called directly |
| 4 | another patient's report on pid 49 | `mismatch`; moved to "Quarantine - Patient Mismatch" with a note, `deleted = 0`, one category link, two audit rows, 0 ledger rows; a second poll added nothing; a question naming it is refused `document_identity_mismatch` |
| 5 | the heavy scan on its own chart | 11 of 107 located; the notice is the answer's first line (no critical value); limitation `degraded_document` |
| 6 | another patient's intake form on pid 49, page closed before any poll | extraction `mismatch`; the chart copy still in Medical Record |
| 7 | the card loads on pid 49 | `reconcile.php`: checked 2, quarantined 1; the copy moved with its note and a "quarantined on card load" audit row; a second load moved nothing |
| 8 | the same bytes on the chart they name (pid 31) | accepted, `pending`, not a duplicate; `w2_document_binding_released`; complete in 16.6 s, `confirmed`; filed in Medical Record on pid 31 while pid 49's copy stays in quarantine; a question on pid 49 is `document_not_found`; a third chart gets 409 |

The agent's log after each set of runs held none of the patient uuids or
name parts involved, and no test name.

**Consequences.**
- The next deploy needs `AGENT_EXTRACTION_STORE_KEY` in the host's env file
  before it starts (agent/docs/deployment.md §4). The live host does not
  have it yet. `docker compose down -v` now deletes every saved extraction.
- The module's install SQL must be run again on an existing install to
  create the category (the module README). Until then a mismatch is
  reported to the card as NOT moved.
- **D54 run A3 no longer re-extracts.** A re-upload after a restart is a
  duplicate. It still retries a write-back that did not complete (item 4),
  now without the vision call.
- The agent's 409 outlives restarts, and so outlives a deletion in OpenEMR
  (W2_ARCHITECTURE.md §11 item 14), except for a mismatch, which releases.
- Each card load runs one reconcile query. It reaches the agent only when
  the chart has an unquarantined copy in a filing category.

**Not settled here.**
- **A deleted document's binding.** A document deleted in OpenEMR, rather
  than quarantined, still binds its bytes to its chart in the agent until
  its row expires (30 days). Whether deletion should release it is not
  decided.
- **A mismatch copy the module cannot see yet.** A copy not yet quarantined
  still refuses the bytes on another chart through the module's check, until
  a status poll, re-upload or card load of its own chart quarantines it.
- **Several workers.** A running job is still one process's memory
  (D40 item 10). The store is not a queue and is not built for writers in
  two processes.
- **SQLite on the event loop.** Each statement is one short row read or
  write. They are not moved to a thread.
- **The heavy scan's 11 vs 36 of 107.** Not investigated. It could be the
  vision call or the container's OCR.
- **The gate does not cover any of this yet.** The behaviours a gate session
  should add are listed in the work order 32 report.
