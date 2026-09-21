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

---

## D1 (superseding) — Write-back path for documents and lab values

**Status:** DECIDED — supersedes the D1 `PENDING-SPIKE` entry above, which is
left unedited for the record.
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
