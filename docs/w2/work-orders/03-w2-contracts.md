<!-- Saved verbatim from the session's WORK-ORDER.md, matching the convention
     of 00-02. Referenced by docs/w2/DECISIONS.md D9-D19. -->

WORK ORDER 03: Week 2 contracts. Branch feat/w2-contracts. Do not merge.

STEP 0: prove the gate is live. Attempt a Write to
agent/evals/w2/cases/probe.json. It must be BLOCKED. If it is not,
STOP and tell me: this session was started outside the worktree.

READ FIRST: CLAUDE.md, docs/w2/DECISIONS.md (D1-D8), and the factory's
ground-truth format via
  git show feat/doc-factory:agent/evals/w2/factory/groundtruth.py
plus one sample ground truth JSON from that branch. Also locate the
existing Week 1 claim/citation models; you extend them, you do not
break them.

GOAL
Strict Pydantic contracts that every Phase 3 branch builds against.
Contracts are the source of truth, not the implementation.

IN SCOPE: a contracts module under agent/app/ (put it where Week 1
contracts live; say where), tests under agent/tests/, DECISIONS
entries for decisions you make.
OUT OF SCOPE: extraction, retrieval, supervisor, endpoints, UI,
anything under agent/evals/.

ALL MODELS: Pydantic v2, extra="forbid", strict where types matter.

1. Citation contract (extends Week 1):
   {source_type, source_id, page_or_section, field_or_chunk_id,
    quote_or_value} + optional bbox (page, x0, y0, x1, y1, PDF points)
   + extraction_id for document sources.
   source_type enum distinguishes chart (FHIR), document, guideline.
   Validator: document citations for a located value require bbox.
   Week 1 citations must still validate or map losslessly; show it.

2. Extracted-field wrapper, generic over value type: value, status
   (located | unlocated | missing | illegible), citation, confidence.
   Validators: located requires citation with bbox; unlocated,
   missing and illegible must never be presentable as fact. Encode that
   in the type (e.g. a method that only yields presentable facts for
   located fields). The type system is the first verifier.

3. lab_pdf schema: document metadata + results, each with test name,
   value (numeric OR qualitative text such as "<5" or "positive";
   decide and record), unit, reference range (low/high and/or text),
   collection date, abnormal flag, citation. Plus a pure function
   computing the abnormal flag from value vs range, and a cross-check
   result type (VLM flag vs computed flag; disagreement =
   verification failure). Deterministic, fully unit-tested.

4. intake_form schema: demographics, chief concern, current
   medications (name, dose, frequency, route), allergies (substance,
   reaction), family history (relation, condition), citations.
   Plus a Discrepancy type: document value vs chart value, kind
   (added / omitted / changed), both citations. Intake data is never
   merged into the chart (D2); discrepancies are how it surfaces.

5. Worker and supervisor contracts:
   - IntakeExtractorInput/Output, EvidenceRetrieverInput/Output,
     EvidenceChunk (text, source metadata, retrieval and rerank scores).
   - Retrieval input carries clinical concepts only: NO patient
     identifier fields exist on the type (D5 constraint).
   - RoutingDecision: decision (extract | retrieve | answer | refuse),
     reason_code enum, correlation_id; no free-text field that could
     carry PHI into logs.

6. Write-back contracts (D1): request and response for the custom
   endpoint (document + lab results in, real ids out), the idempotency
   key composition (sha3-512 + test code + collection date) as a
   function with tests, the ledger row, the extraction-id format that
   goes into procedure_result.comments.

7. Final answer contract: claims each with >=1 citation; patient facts
   and guideline evidence in separate lists; a claim can't mix them.

8. Factory compatibility: map every factory ground-truth field_path to
   a schema field. Adopt the factory's paths where sensible; otherwise
   write an explicit mapping table. Prove it: a test that loads all 6
   sample ground truths (read from the factory branch) and converts
   them into valid schema instances. Unmappable fields are listed.

9. Export JSON Schema for the lab_pdf and intake_form models to docs/w2/
   (graded deliverable; also feeds the API collection).

ACCEPTANCE: new contract tests pass; the full existing unit suite
still passes (368+); factory conversion test green; ruff clean on
new files.

/report, including a list of every decision you made and its
DECISIONS.md entry. STOP. Do not merge.