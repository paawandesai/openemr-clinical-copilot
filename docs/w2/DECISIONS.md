# Week 2 — Decisions

**Append-only.** Never edit or delete an existing entry. To change a decision,
append a new entry that supersedes it by ID and set the old entry's status to
`SUPERSEDED by Dn` on its status line only.

Statuses: `DECIDED` (settled; do not re-open, raise conflicts in your session
report) · `OPEN` (not settled; propose, do not assume) · `PENDING-SPIKE`
(blocked on a named spike) · `SUPERSEDED by Dn`.

---

## D1 — Write-back path for documents and lab values

**Status:** PENDING-SPIKE
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
