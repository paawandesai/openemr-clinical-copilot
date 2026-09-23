# Week 2 Architecture — Clinical Co-Pilot: documents, workers, evidence

**Source of record.** Every capability below traces to a decision in
[`docs/w2/DECISIONS.md`](docs/w2/DECISIONS.md) (cited as Dn) or to code, and
every number traces to a committed artifact, cited where it is used. Week 1's
architecture is unchanged and is not repeated here:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (authorization, tool scope,
conversation state, the deterministic verifier).

**Snapshot: `main@f8b16c7`.** Every path and number below is on that commit
unless it is marked PENDING. That commit is on `origin`.

**PENDING** marks a claim that rests on a branch not yet merged into `main`,
and names the branch:

| Marker | Branch | On `origin`? | What rests on it |
|---|---|---|---|
| **[PENDING `chore/w2-deploy-rehearsal`]** | `chore/w2-deploy-rehearsal` | **no** | `docs/w2/DEPLOY.md`, the live-host rehearsal and every live-host measurement; D41; the upload guard for the stub route |
| **[PENDING `feat/w2-php-wiring`]** | `feat/w2-php-wiring` | **no** | the upload guard on `main`'s real `/documents` route |
| **[PENDING `feat/w2-eval-gate`]** | `feat/w2-eval-gate` | yes, unmerged | the eval gate, its cases and evidence (`docs/w2/EVAL_GATE_EVIDENCE.md`); D42–D48 |

All three are due to merge before submission. At that point every citation
is repinned to the one resulting `main` commit and the markers come out.
Until then a PENDING claim cannot be checked from `main`.

---

## Summary

Week 2 teaches a co-pilot that already reads and verifies a chart to do three
more things: read uploaded lab PDFs and intake forms, route each question
through a supervisor to one of two workers, and cite published guidance
beside chart facts without the two blurring. Nine decisions carry the weight.

1. **The model never supplies a location** (D3). Extraction has three stages:
   text-layer or OCR word boxes, one vision call into a strict schema, then a
   deterministic locate step. Only the locate step produces a `bbox`. A value
   it cannot place is `unsupported`: disclosed, never stated (invariant 4).
2. **Numbers, units, dates and flags match exactly or not at all** (D24).
   Fuzzy matching is for free text only. `3.1` and `8.1` are one character
   apart and are a normal and a critical potassium. The price is recall on
   bad scans (§1.4).
3. **A blank flag column says nothing** (D20). The cross-check compares only
   flags a laboratory printed, and it reports its small denominator.
4. **Intake medications and allergies never enter the chart** (D2). They
   surface as discrepancies. The write-back type accepts lab PDFs only.
5. **Routing uses no model** (D4, D40). A closed table maps wording and
   request shape to four routes, each with a reason code. Every decision is
   logged, and bounded visits fail closed.
6. **Retrieval is all local** (D25): BM25 plus local embeddings, fused with
   RRF, then a local cross-encoder rerank. Queries come from a fixed concept
   lexicon, never from the question, so no identifier can reach one (D5).
7. **The verifier builds citations from sources, not drafts** (D40 item 7).
   A document value must equal the located text. A guideline excerpt must be
   verbatim, at least 4 words. The sentences the clinician reads are rendered
   by code.
8. **Every write to OpenEMR is idempotent** (D1, D15, D29, D31). A module
   endpoint writes the lab chain in one transaction behind a ledger keyed
   UNIQUE on document hash, test code and collection date. Results land
   unreviewed.
9. **The measured code cannot move its own gate** (D8, D42–D48) **[PENDING `feat/w2-eval-gate`]**. Gated paths
   are hook-protected. 72 deterministic checks must pass at 100%, a regressed
   critical case blocks alone, and all 13 planted regressions **[PENDING `feat/w2-eval-gate`]** block (§6).

**Tradeoffs:**

- **Recall for honesty.** The system locates less, and states no number the
  page lacks.
- **Reranking for latency.** Under load most retrievals go unreranked rather
  than queue (D39).
- **Local models for privacy.** Idle memory is 760–900 MiB against Week 1's
  50 MiB (`DEPLOY.md` §8 **[PENDING `chore/w2-deploy-rehearsal`]**), and in return D5 is structural.
- **One worker.** Correct for the demo, wrong for production (D40 item 10).

**Not true yet:**

- A question about an uploaded lab report takes **92–131 s** (n = 2). It is
  one drafting call over every extracted result, writing 11.5k tokens: past
  the 90 s budget, and about half the $0.82 encounter cost (`LOAD_TEST.md`
  §6).
- The live host **[PENDING `chore/w2-deploy-rehearsal`]** does not run the Week 2 ask path (`a78ba51` predates
  `feat/w2-graph`, `DEPLOY.md` §0 **[PENDING `chore/w2-deploy-rehearsal`]**).
- The documents card is not registered in the module.
- The eval gate has not merged **[PENDING `feat/w2-eval-gate`]**.

§11 lists every known limitation.

---

## 1. Document ingestion

### 1.1 The flow

```
 upload (POST /documents?kind=&patient_id=)          ── D7: extraction runs here, never at question time
   │  Patient read under the clinician's token first (D40 item 8); same bytes on another chart → 409
   ▼
 read_document   PDF check, SHA3-512 (D16), words + boxes per page:
   │             PyMuPDF text layer; Tesseract at 300 dpi where a page has none   (D3 stage 1)
   ▼
 vision          ONE Anthropic call per document into a strict schema,            (D3 stage 2)
   │             pages rendered at 200 dpi, ≤10 pages per call, >30 refused before spend;
   │             no coordinates, no confidence asked for
   ▼
 locate          every value located on the page the model named, with its        (D3 stage 3)
   │             DECLARED value class (D35.1); row fields only on their anchor's row (D35.2);
   │             exact → numeric → number_only tiers; fuzzy for free text only (D24)
   ▼
 verify          chart read (identity; current meds/allergies) → wrong-patient hard flag (D35.10)
                 → lab: abnormal-flag cross-check (D11, D20) → intake: discrepancies (D2, D35.9)
                 → LOINC coding from a committed table (D35.13)
```

Source: `agent/app/graph/intake_extractor.py` (module docstring and the four
nodes), `agent/app/workers/intake_extractor/vision.py` (`MAX_PAGES_PER_CALL =
10`, `MAX_PAGES = 30`, 200 dpi), `agent/app/extraction/words.py` (`dpi: int =
300`). Every node runs inside Week 1's `_untraced` guard, and the graph is
reachable only through `UntracedGraph` (§8).

### 1.2 What each stage owns

| Stage | Owns | Never does |
|---|---|---|
| Text layer / OCR | word text and word boxes | interpret anything |
| Vision | which value belongs to which schema field, on which page | supply a bbox, a confidence or a flag decision |
| Locate | whether that value is on that page, and where | near-match a number, unit, date or flag (D24) |
| Verify | identity, flag cross-check, discrepancies, LOINC codes | merge anything into the chart (D2) |

A field ends in one of four states (D13): `located` (has a bbox, and is the
only presentable state), `unlocated` (the model read it and code could not
place it), `missing`, or `illegible`. `as_presented_fact()` is the only way
from an `ExtractedField` to a value, and it returns `None` for everything
except `located`. The models are `frozen=True`, so a field cannot move to
`located` after validation (D9).

A **blank printed flag is `missing`**, and the cross-check reads it as
`not_reported`, never `normal` (D20, D35.3). A value that cannot be parsed
into its schema type is `illegible` and listed in `parse_failures` (D35.4).
Confidence is `reader_confidence × tier_weight × match_score`, with tier
weights exact 1.0, numeric 0.95, number-only 0.90 and fuzzy 0.80. The model
reports no confidence (D35.7).

### 1.3 The abnormal-flag cross-check and discrepancies

`compute_abnormal_flag` turns the value and the reference range into
intervals: inside, above, below, or `indeterminate` when a censored value
straddles a bound (D11). `cross_check_abnormal_flag` compares the result
against the printed flag **only** when the laboratory printed a comparable
flag (`normal | high | low | abnormal`). Otherwise the outcome is
`not_reported` (D20).

Intake discrepancies compare **located** intake values against the chart's
current records only (D35.9):

- A written drug name matches when every word of it appears in the chart's
  product name. A patient who writes "Fexofenadine hydrochloride" for
  "Fexofenadine hydrochloride 30 MG Oral Tablet" therefore produces no
  discrepancy.
- A dose counts as `changed` only when it matches none of the strengths in
  the product name.
- An unlocated intake row turns an omission it might explain into
  `unresolved`, never `omitted`.

A name or date-of-birth mismatch against the chart is a hard identity flag.
Nothing from that document is stated, and the discrepancy check does not run
(D35.10, D40 item 3).

### 1.4 Measured

These are two different measurements, and the table keeps them apart. **Real
vision** is three live model calls, run sequentially
(`docs/w2/evidence/load/extraction-live-vision.json`,
`LOAD_TEST.md` §4b). **Replay** is all six committed samples with the vision
step replaced by ground truth, so it measures everything after the model
(`docs/w2/evidence/extractor-measurement.txt`, produced by
`python -m tests.intake_extractor_measurement` in this session).

| Sample | Page kind | Located rate, real vision | Located rate, replay |
|---|---|---|---|
| `lab-clean-001` | text layer | 98% (102/104) | 99.0% (103/104) |
| `lab-multipage-003` | text layer | — | 99.5% (192/193) |
| `intake-clean-004` | text layer | 92% (45/49) | 91.8% (45/49) |
| `intake-missing-allergy-006` | text layer | — | 94.4% (51/54) |
| `intake-scanned-light-med-005` | light scan | — | 87.5% (49/56) |
| `lab-scanned-heavy-abnormal-002` | **heavy scan** | **34% (36/107)** | **33.6% (36/107)** |
| all six, replay | | | 84.5% (476/563) |

Located rate is `located / (located + unlocated)`. `missing` and `illegible`
describe the document, not the extractor, so they are excluded from both the
numerator and the denominator. Also measured, in replay:

- 100% (391/391) of text-layer locates sit over the right ink (IoU ≥ 0.5).
- 0 fuzzy locates on a number, unit, date or flag.
- Discrepancy precision 2/2 and recall 2/2, with 0 false discrepancies across
  25 `derived` fields.

Per document, the vision call takes 15.3–32.9 s, 93–99% of the extraction
time, and costs $0.0955–$0.2276 (`LOAD_TEST.md` §4b; the `cost_usd` field
of the six recordings in `agent/evals/w2/staging/transcriptions/`).
OCR on the deployment host **[PENDING `chore/w2-deploy-rehearsal`]** is p50 5.7 s per page (`DEPLOY.md` §8).

---

## 2. The worker graph

### 2.1 Shape

```
                     ┌──────────────────────────────┐
  POST /w2/ask  ──►  │  supervisor  (≤4 visits)     │ ◄─────────────┐
                     └──┬─────────┬─────────┬───────┘               │
                 extract│ retrieve│   answer│  refuse               │
                        ▼         ▼         ▼        ▼              │
            intake_extractor  evidence_retriever  answer ──(fail)──►│   refuse → END
               (≤1 visit)        (≤1 visit)      (≤1)               │
                    └──────────────┴────────────────────────────────┘
```

`SUPERVISOR_GRAPH` is in `agent/app/graph/supervisor.py`. Workers return to
the supervisor. `answer` returns only when it fails. The Week 1 conversation
route is untouched (D40 item 1). Both paths share the same process-wide
`ConversationStore`, so a patient switch on either path discards the other's
context. Contract: `agent/app/models/w2/ask.py`. `routing` and `handoffs` are
returned in `AskResponse` as an inspectable trace, and every decision is
logged as a routing event (`log_event(ROUTING_EVENT, ...)` in `supervisor.py`).

### 2.2 Step 1: `classify(question)`, wording only, once per turn

The rule order is the policy (`agent/app/supervisor/routing.py`, `classify`):

| Order | If the question… | Intent | Rule id |
|---|---|---|---|
| 1 | names another patient | `refuse_out_of_scope` | `refuse:other_patients` |
| 2 | asks for a dose, diagnosis, trend or change over time | `refuse_unverifiable` | `refuse:dosing` / `:diagnosis` / `:trend` / `:change_detection` |
| 3a | uses recommendation wording ("should she", "eligible for"…) | `recommendation` | `retrieve:recommendation_wording` |
| 3b | uses guideline wording ("guideline", "USPSTF", "screening"…) | `guideline` | `retrieve:guideline_wording` |
| 4 | hits a Week 1 chart-scope or judgment refusal | `refuse_out_of_scope` / `refuse_unverifiable` | `refuse:<category>` |
| 5 | anything else | `record_lookup` (never retrieves, G2) | `answer:record_lookup` |

Rule 3 comes before rule 4 on purpose. "At what age should screening start" is
a question about guidance, not a read of this patient's age (D40 item 2).

### 2.3 Step 2: `decide(view)`, shape only, on every supervisor visit

`SupervisorView` carries only shape: the intent, a per-document state
(`not_found | pending | failed | identity_mismatch | ready`), whether
retrieval ran and how many hits it returned, a failure code, and visit
counts. It never carries chart content (`routing.py`, `_choose`, `decide`).
The first matching row wins.

| Condition, in order | Route | `RoutingReasonCode` |
|---|---|---|
| supervisor visits > 4, or the target node is at its bound | refuse | `visit_limit_exceeded` |
| a worker reported a failure | refuse | `retrieval_unavailable` / `chart_unavailable` / `model_unavailable` |
| intent is a refusal | refuse | `out_of_scope_resource` / `unverifiable_judgment` |
| a named document is unknown or another patient's | refuse | `document_not_found` (one code for both) |
| a named document failed extraction | refuse | `extraction_failed` |
| a document is pending and the extractor has not been visited | **extract** | `document_not_yet_extracted` |
| a document is still pending after one extractor visit | refuse | `document_still_processing` (D7: never a wait) |
| a document's identity flag fired | refuse | `document_identity_mismatch` |
| guideline or recommendation intent, retrieval not yet run | **retrieve** | `guideline_question` / `evidence_needed_for_recommendation` |
| …retrieval ran and found nothing | refuse | `no_guideline_evidence` |
| …retrieval found evidence | **answer** | `evidence_in_hand` |
| documents named, all ready | **answer** | `extraction_available` |
| otherwise | **answer** | `chart_only_question` |

`RoutingReasonCode` is a closed enum with no free-text companion, and
`ALLOWED_REASONS` rejects any route/reason pair not in this table when the
decision is constructed (`agent/app/models/w2/workers.py`). One member,
`document_reextract_requested`, exists in the enum and is produced by no
rule. Every clinical outcome is HTTP 200 with a typed status. An unexpected
exception is HTTP 500 `internal_error` with fixed wording (D40 item 3).

**Cross-patient safety is structural, not a routing rule.**

- `run_ask_turn` takes the patient uuid as an argument beside the question.
  The question never selects the patient, so no phrasing can reach another
  chart.
- Rule 1 is a second layer: it refuses explicit cross-patient *wording*
  before anything is looked up.
- It keys on the phrase "another patient", not on a person's name, so "Show
  me Jane Doe's recent labs" is classified as an ordinary record lookup, for
  the open chart only.

Gate case W2-H2 **[PENDING `feat/w2-eval-gate`]** asserts both halves and asserts the limitation, so it will
fail if the router starts refusing on names (commit message,
"activate the supervisor batch").

### 2.4 Critical values (H6)

Critical values are decided by a committed table, not by the model, the
printed flag or the reference range (`agent/app/supervisor/critical.py`,
D40 item 9):

- An analyte is matched by LOINC code **and** an exact unit string.
- A value is critical when it lies strictly beyond a limit. A censored value
  is critical only when its whole interval does (`<2.0` potassium is
  critical; `<3.0` is not).
- Critical values lead the answer whatever was asked, and ride in
  `AskResponse.critical_values`.
- `keep_critical` puts back every critical Observation that the lab-result
  cap dropped.
- An unlocated document value beyond a limit is reported as a *possible*
  critical value: the finding names the test and the page, and never states
  the number (invariant 4).

The limits are a demo configuration, **not reviewed for any site**.
Potassium, for example, is critical below 2.5 and above 6.0 mmol/L.

---

## 3. Retrieval (hybrid RAG)

| Piece | Choice | Source |
|---|---|---|
| Corpus | 28 documents, 693 chunks, 602,041 characters. USPSTF (8), NIDDK (6), NHLBI (4), CDC (6), MedlinePlus (3), NIA (1). Topics match what the factory's lab PDFs contain | `docs/w2/retrieval-bakeoff.md` §1 |
| Licensing | 20 sources are US federal public domain. The 8 USPSTF statements are **jointly copyrighted with an express verbatim-redistribution grant**, so they are kept, verbatim only; the code refuses a non-substring quote. Third-party rights notices are stripped. **Flagged for human sign-off** | D26 |
| Dated sources | `uspstf-thyroid-screening` (2015) and `niddk-evaluate-ckd` (2015) are flagged `(dated)` in `GuidelineSource.version`, a workaround because the contract has no `dated` field | `retrieval-bakeoff.md` §1 |
| Chunking | section-aware; the heading path becomes `page_or_section`, and the body becomes the quotable text | `retrieval-bakeoff.md` §3 |
| Sparse + dense | BM25 plus `BAAI/bge-small-en-v1.5` (fastembed/ONNX, 384-d, local), each searched to depth 30, fused with RRF at **equal** weights. Dense 2× scored better but was fitted on the same 30 queries it was measured on | D25 |
| Rerank | local cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` over the top 20, on every retrieval; 4 chunks passed to the answer step | D25 |
| Budget | over budget, the fused order stands, `reranked=false`, and the circuit opens. **2.5 s on `main`**; 4.0 s on `chore/w2-deploy-rehearsal` (D41 **[PENDING `chore/w2-deploy-rehearsal`]**, measured on the host) | `agent/app/retrieval/retriever.py:67`; D41 |
| Admission | at most 2 concurrent reranks; the rest take the fallback at once (`rerank_busy`). On `main`; **not on the live host** **[PENDING `chore/w2-deploy-rehearsal`]** | D39; `retriever.py:77` |
| Cohere | adapter present, never run (no key) | D25 item 6 |

**The concept-only query rule.** `routing.CONCEPT_LEXICON` maps wording to
concept text that this code owns (for example `\ba1c\b` → "hemoglobin A1C").
When the question names no concept, the query uses the printed names of lab
rows the laboratory flagged. A topic word on its own ("screening") searches
nothing. No substring of the question is ever sent, so a patient name typed
into a question has no path into a query (D40 item 6, D5). Because the whole
stack is local, the query also never leaves the process (D25 rationale).

**Measured on 30 concept-only dev queries with 110 relevance judgments**
(Apple M5 Pro; `retrieval-bakeoff.md` §4):

- The decided configuration scores recall@4 0.666 and MRR 0.889.
- It is the **only configuration with no query at zero recall**. The query it
  rescues is `q19-levothyroxine-followup`: the only chunk that answers it is
  written for patients, while the query is written in clinical register.
- 30 queries cannot resolve a 1-point difference. D25 rests on the
  zero-recall count, not on the means.

---

## 4. Citation contract and verification

**The contract (invariant 2).** Every claim carries `{source_type, source_id,
page_or_section, field_or_chunk_id, quote_or_value}`, plus `bbox` and
`extraction_id` when the source is a document
(`agent/app/models/w2/citations.py`).

| `source_type` | `page_or_section` holds | `bbox` |
|---|---|---|
| `chart` | one of the five Week 1 FHIR resource types (D14) | none |
| `document` | the page | required for a located value (enforced on `ExtractedField`, D13) |
| `guideline` | the section path | none |

**Separation (invariant 3, D17).** `FinalAnswer` keeps `patient_facts` and
`guideline_evidence` in separate lists. A `GuidelineClaim` accepts guideline
citations only. A `PatientFactClaim` accepts chart or document citations, all
of one type. Unsupported values ride in a third list that nothing counts as a
claim.

**Verification (`agent/app/verification/w2_checker.py`; no model, no
network).** The drafting call (`app/reasoning/draft_answer.py`) emits
structured citations only, with ids narrowed by the schema to what this turn
provided. Each drafted claim cites exactly one source and is checked against
what this turn holds:

- **chart**: Week 1's `checker.verify_claims`, unchanged.
- **document**: the field was *located*, and the claimed value equals the
  located text after case and whitespace folding. No substring match ("16" is
  in "162"), no near match.
- **guideline**: the chunk was returned this turn, and the excerpt is at least
  `MIN_EXCERPT_WORDS = 4` words, found in the chunk with only whitespace
  differences.

**The citation a passed claim carries is rebuilt from the source**: the
locate step's citation with its bbox, or the chunk's own characters. The
model therefore has no channel through which to put a bbox or a quote on the
page (D40 item 7).

**Rendering.** Every sentence the clinician reads is rendered by code from the
source values. That makes D20 structural rather than a matter of wording: a
blank flag is always rendered as "no flag printed (… not a statement that the
value is normal)". Failed claims appear as UNVERIFIED lines and in
`AskResponse.unverified`, never dropped. A failed guideline "quotation" is
disclosed without its text, because showing a non-verbatim quote attributed
to a publisher is what D26 forbids.

**The view.** `AnswerView` (`agent/app/models/w2/answer_view.py`, D32) wraps
a `FinalAnswer` with the documents, chunks and reported-flag values needed
to draw it, and its validators refuse an answer that cites a source the view
does not carry. The chart card draws it with pinned, vendored PDF.js 6.3.289
(D33). A document chip highlights its bbox on the rendered page. Ground truth
is mapped into the rendered file's own space (D27), and the viewer
cross-checks PDF.js's `viewport.transform` against its own reimplementation,
drawing **no** boxes if they disagree (D33). Overlay alignment is checked at
scales 0.75, 1, 1.5 and 3 against all six samples
(`interface/modules/custom_modules/oe-module-clinical-copilot/dev/README.md`).

---

## 5. Write-back and idempotency

Documents and lab values are written through a custom module endpoint.
**Intake data is never written anywhere** (D2).

| Piece | Decision | Source |
|---|---|---|
| Endpoint | `POST /api/clinical-copilot/lab-writeback`, registered through `RestApiCreateEvent` and `RestApiScopeEvent`. No core route file is edited | D29 |
| Scope | `user/lab-writeback.c`, plus `api:oemr`; a new client registration is needed because scopes are immutable per client | D29, `docs/w2/deploy-writeback.md` |
| Shape | one request carries one document and N results: one new `procedure_order`, then one `procedure_order_code` + `procedure_report` + `procedure_result` per analyte, in one transaction. At most 100 results per request | D31 |
| Idempotency key | `<document sha3-512>:<TEST CODE>:<YYYY-MM-DD>`, UNIQUE in `module_clinical_copilot_writeback_ledger`; per result, not per request | D1, D15, D31 |
| Provenance | `procedure_result.comments` carries `w2x-` plus 128 bits of `sha3-256(key)`. It surfaces as `Observation.note[0].text`, and the ledger maps it to the document, page and bbox | D1, D15 |
| Review state | `review_status = 'received'`: the result lands in *Pending Review*, not *Patient Results*, until a clinician signs it | D29 |
| Intake guard | the write-back request type refuses any document that is not a `lab_pdf`, so an intake form has no path into the chart | `agent/app/models/w2/writeback.py`; planted regression **[PENDING `feat/w2-eval-gate`]** `auto-merge-intake` |
| Preview | `GET <module>/public/document.php`: session ACL `patients/docs`, the open patient only, PDF only. 404 is identical for missing, another patient's and deleted documents | D28, D30, D49 |

**Verified on the deployment host** (`DEPLOY.md` §7.3) **[PENDING `chore/w2-deploy-rehearsal`]**:

| Check | Result |
|---|---|
| Write of 2 results | 200 with real ids, 500 ms |
| Identical replay | the same ids, every row `duplicate_ignored`, 384 ms |
| FHIR read-back | the note carries the extraction id |
| Audit | 4 `openemr.log` rows plus the module's commit entry |
| Review state | `received` |
| Cleanup | proven to leave zero leftover rows (§7.4) |

Two identical requests fired together leave exactly one chain (D31, local
stack).

**Not built:** no agent route or graph calls the write-back client, and the
chart-embed broker never requests the write scope (`DEPLOY.md` §0, §10 **[PENDING `chore/w2-deploy-rehearsal`]**).
Write-back is a verified endpoint plus a library, not a user-facing feature.

---

## 6. The eval gate

**[PENDING `feat/w2-eval-gate`]**: **every row and number in this section** rests on
`feat/w2-eval-gate`, read at its `origin` tip.

| | |
|---|---|
| Command | `cd agent && python -m evals.w2.gate`; exit code 0 PASS, 1 BLOCKED, 2 ERROR, 3 INCOMPLETE (D45) |
| Cases | 96 specs: 78 active, 16 pending, 2 delegated to a PHP CI job (`docs/w2/EVAL_GATE_EVIDENCE.md`, control run) |
| Tiers | deterministic 72/72, judge 8/8. The judge is `claude-haiku-4-5-20251001`, best-of-3 with a 2-of-3 majority, a cache keyed on the prompt **text**, and marked **UNCALIBRATED** (D43, D48) |
| Rules, in order | per-category minimum n (over active plus pending) → deterministic at 100% → critical-case override → judge threshold plus a 95% Wilson floor → >5% regression or below threshold (D42; `agent/evals/w2/README.md`) |
| Fixtures | six recorded live `claude-opus-5` transcriptions, replayed offline and refused if the PDF, prompt or schema changed (`agent/evals/w2/README.md`) |
| CI | `.gitlab-ci.yml` jobs `w2-eval-gate` and `w2-writeback-php`. The gate job does **not** pass `--allow-incomplete`, so a pipeline with no key fails (D45). Both were "verified locally with the same images and the same commands" (commit message, "a PHP CI job enforces the write-back cases"). No committed evidence shows either has run on a GitLab runner |
| Proof | 13 planted regressions, each applied to a copy of the tree. The control run passes; all 13 block, each caught by at least the case the design names (`EVAL_GATE_EVIDENCE.md`, "Harness verdict: HELD") |
| Protection | `.claude/hooks/gate-guard.sh` (before a tool call) and `gate-effect-check.sh` (after it); `scripts/gate-diff.sh` before a merge (`docs/w2/GATE.md`, on `main`). These are a guard against mistakes, not a security boundary (GATE.md, "Known limit") |

**Prompt injection inside an uploaded PDF (W2-I1, W2-I2) is covered for
containment only.**

- `lab-clean-001` is re-rendered with a hidden instruction on two channels:
  white-on-white text in the page's text layer, and the PDF's metadata.
- The gate asserts the payload reaches no log entry, trace field,
  extraction output or disclosure.
- It does **not** assert that a model shown the payload declines it. The
  gate reads the document through a ground-truth stand-in for the vision
  model, so no model reads the injection. That half is W2-I5, which is
  pending.
- The 13th planted regression, `page-text-in-a-log-line`, is caught by I1
  and I2.

Source: the commit that activated them, "activate W2-I1 and W2-I2".

Pending, meaning specified but not scored:

| Category | Pending cases |
|---|---|
| B | B8, B9 |
| C | C7–C9 |
| F | F4 |
| G | G8–G10 |
| H | H1, H7–H9 |
| I | I3, I4, I5 (I5 is the refusal half of the injection cases) |

The evidence file's closing section, "Coverage-map rows not testable yet",
still says the supervisor does not exist, although G1, G2, G5–G7 and H2–H6
now run against it. That section is stale.

---

## 7. Observability and tracing

**Tracing containment (invariant 1).** Week 1's defence has two layers:
`UntracedGraph` forces tracing off, and `_untraced` wraps every node and
refuses to run with a LangSmith tracer attached (`agent/app/graph/pre_visit_brief.py`).
Week 2 found a gap in it: a tracer attached to the compiled graph posts the
root run's input *before* any node can refuse. The first extractor graph had
PDF bytes in its input, and LangSmith attempted the upload
(`docs/w2/root-run-trace-gap.md`).

The rule now: **a graph's input carries bookkeeping only** (`{"node_seconds":
{}}`), and everything per-run is runtime context.
`agent/tests/test_graph_inputs_carry_nothing.py` scans every graph's run
calls in `app/`. Its known limit is that it reads source, so it does not see
a run through `getattr` or from outside `app/`.

**On `main`:**

- Week 1's JSON-per-event stdout log, the correlation id, and the Langfuse
  export when `LANGFUSE_*` is set.
- The supervisor logs every routing decision and a `w2_ask_turn_completed`
  line.
- Failure events carry the exception's class name only (`supervisor.py`).

**Added by `feat/w2-observability`, now on `main`** (D36–D38,
`OBSERVABILITY.md`):

- Three operational event types that cannot hold text: `counts` is `int |
  float | bool`, and errors carry a class name.
- One trace per encounter, keyed on a caller-supplied `X-Correlation-ID`
  held to `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`.
- Dependency failure defined as no response, a 5xx or a 429; a 4xx is the
  caller's.
- A stdout dashboard, a 16-widget local Langfuse dashboard, and five alert
  rules (`ALERTS.md`).
- `/ready` reports the Langfuse check but does not gate on it (D37).

**Alerts:**

| Alert | Fires at |
|---|---|
| answer p95 latency | > 45 s |
| 5xx rate | > 5% |
| per-tool dependency-failure rate | > 5% |
| text-layer located rate | < 85% |
| verification failure | > 10% |

Nothing is scheduled and nothing pages anyone (`ALERTS.md`, "What is not
built").

**Verified on the real routes** (`OBSERVABILITY.md` §2). One
API-collection encounter, `07c0fd35-…`, is one Langfuse trace: 2 min 52 s,
87,245 tokens, $0.82. It carried:

- the upload, 26 status polls and the background extraction;
- the brief and a conversation turn;
- 3 supervisor routing decisions;
- 6 supervisor node steps;
- 55 tool calls.

**Open chain breaks:**

- The chart card never sends `X-Correlation-ID`, so each UI request is its
  own trace.
- Write-back carries no correlation id into PHP.
- Extraction inherits the upload's id only when that id is a UUID;
  otherwise the two traces are linked by a logged event, not merged.
- The hosted Langfuse has no BAA, so production metrics come from stdout
  (Week 1, `agent/docs/observability.md` §1). The live host **[PENDING `chore/w2-deploy-rehearsal`]** runs with
  Langfuse `disabled` (`DEPLOY.md` §7.2 **[PENDING `chore/w2-deploy-rehearsal`]**).

---

## 8. Deployment state

| Surface | `main` | Live host `64.177.42.210` (`a78ba51` **[PENDING `chore/w2-deploy-rehearsal`]**, per `DEPLOY.md`) |
|---|---|---|
| Week 1 brief + conversation in the chart | yes | yes, v2 OAuth client (`2ps8qgz4…`) |
| Retrieval (baked models, warmed at start, offline) | yes | yes, preload 2.63 s |
| OCR (Tesseract in the image) | yes | yes |
| `POST /documents` | the real extractor, **but it reads the body before checking the token**, with no scope check, rate limit or concurrency cap (`DEPLOY.md` §2.2 **[PENDING `chore/w2-deploy-rehearsal`]**) | the **stub** (hash and discard), behind the upload guard |
| `POST /w2/ask` (supervisor) | yes | **no**: forwarded by Caddy, but the deployed image predates it |
| Source-documents card and module upload routes | **no** (not registered in `Bootstrap.php`) | no |
| Lab write-back endpoint and ledger | yes | yes, rehearsed (§5) |
| Eval gate | no (branch) | n/a (runs on fixtures, D6) |

The upload guard exists on `chore/w2-deploy-rehearsal` for the stub and on
`feat/w2-php-wiring` **[PENDING `feat/w2-php-wiring`]** for the real route: token, scopes and OpenEMR's
acceptance are checked, then rate limits, then declared size, then an
extraction slot, and only then is the body read. **Deploying `main`'s
`/documents` as it stands would drop that guard**: the body is read before
OpenEMR is asked about the token, and there is no scope check, rate limit or
cap on concurrent extractions. The graph deploy precondition is in
`DEPLOY.md` §2.2 **[PENDING `chore/w2-deploy-rehearsal`]**.

The agent image grew from 360 MB to 1.18 GB, and idle memory from 50 MiB to
760–900 MiB (`DEPLOY.md` §8 **[PENDING `chore/w2-deploy-rehearsal`]**).

---

## 9. Risks and tradeoffs

| Risk | Where it bites | Mitigation today | What is still exposed |
|---|---|---|---|
| **The whole-report answer** | one `/w2/ask` about an uploaded report: 91.9 s and 130.5 s, one call at 34,948 in / 11,523 out, $0.46 (`LOAD_TEST.md` §6) | the 45 s `answer_p95_latency` alert covers `/w2/ask` (`ALERTS.md` §1) | past the 90 s budget with no load at all; the fix (bound the output, or answer only from the rows the question names) is not built |
| **One process, in-memory stores** (conversations, documents, extraction jobs, the rerank circuit) | a second worker or replica makes uploads invisible (`404`), re-bills extraction, and loses follow-up context; a restart forgets every upload | one uvicorn worker; a startup warning when more are configured (D40 item 10, `agent/docs/deployment.md` §7) | a second *replica* is invisible to the check; no persistence |
| **Reranker under load** | 50 concurrent retrievals: p95 335,224 ms, 0% reranked, 5.3 GiB peak RSS on a cold run (`LOAD_TEST.md` §5) | D39 admission bound: p95 259 ms, but only 1% reranked in the burst and 6.2% at 50 paced users (§5b) | on `main`, not on the live host **[PENDING `chore/w2-deploy-rehearsal`]**; memory still up to 3.1 GiB against a 7.24 GiB host that OpenEMR already takes 4.9 GiB of (D39) |
| **The rerank circuit never closes** | one slow rerank turns reranking off until restart; 35 of the next 60 turns ran unreranked (D41 **[PENDING `chore/w2-deploy-rehearsal`]**) | none | `rerank_over_budget` conflates "slow" with "skipped" (D41) |
| **OCR contends with retrieval** | OCR in the same container raises rerank p95 from 2.08 s to 3.39 s (`DEPLOY.md` §8 **[PENDING `chore/w2-deploy-rehearsal`]**) | 4 s budget (D41, branch) | extraction still runs in the serving process (`LOAD_TEST.md`, "Where the new bottleneck is") |
| **OpenEMR's PHP tier** | saturated at 10 users locally (420% CPU) and failing 58% at 50 (`LOAD_TEST.md` §1) | none agent-side | Week 1 finding, unchanged |
| **Upload surface** | `main`'s `/documents` reads the body first | guard on two branches (§8) | not merged |
| **Recall versus honesty on scans** | a heavy scan presents a third of its values | unsupported values disclosed, possible criticals named without a number | a clinician must open the PDF for the rest |
| **USPSTF licensing reading** | verbatim-only redistribution | substring-enforced quotes (D26) | human sign-off pending; the "no AHRQ endorsement" UI constraint is not implemented (D26 item 3) |
| **Critical-value table** | a demo list | exact LOINC and unit match | not reviewed for any site (D40 item 9) |

---

## 10. Decisions this rests on

| Area | Decisions |
|---|---|
| Extraction | D3, D7, D10, D11, D13, D16, D20 (supersedes D12), D24, D35 |
| Contracts | D9, D14, D17, D18, D21, D22 |
| Routing and the ask path | D4, D40 |
| Retrieval | D25 (supersedes D5, D23), D26, D39, D41 **[PENDING `chore/w2-deploy-rehearsal`]** |
| UI | D27, D28, D30, D32, D33, D34, D49 (the preview URL; supersedes D28 and D30 on that point) |
| Write-back | D1 (superseding), D2, D15, D29, D31 |
| Observability | D36, D37, D38 |
| Evals | D6, D8, D42–D48 **[PENDING `feat/w2-eval-gate`]** |

D41 and D42–D48 are on unmerged branches (marked PENDING above). This document
makes no new decision and takes no D-number.

---

## 11. Known limitations

1. **The verifier can confirm a guideline chunk says X. It cannot confirm X
   applies to this patient.** Nothing reads a recommendation's population
   criteria against the chart. Every answer with guideline evidence carries
   `guideline_applicability_not_verified` and a fixed sentence, and the
   guideline section is headed as not about this patient
   (`w2_checker.py` docstring, D40 item 7). A verified chart or document value
   is likewise what the source says, not proof the source is right.
2. **The judge cannot enforce D20 at the prose level.** "The laboratory
   reported the potassium as normal", over a blank flag column, was graded
   **pass** in 12 of 15 votes under the committed `factually_consistent`
   prompt (D46 **[PENDING `feat/w2-eval-gate`]**). Blank-is-not-normal is held deterministically instead: by
   code-rendered sentences (§4) and by gate case W2-D2 **[PENDING `feat/w2-eval-gate`]**. The judge is
   **uncalibrated** against human labels (D43 **[PENDING `feat/w2-eval-gate`]**, D48).
3. **The heavy-scan located rate is low by design: 34% (36/107)** with real
   vision, and 33.6% (36/107) in replay (§1.4). D24 chose exact-only matching
   for numbers, units, dates and flags over recall. On that scan the flag
   cross-check compares nothing: 0 comparable flags, 6 `not_reported`.
4. **Single worker, in-memory state.** See §9, row 2. A restart forgets every
   upload and conversation.
5. **The LangGraph root-run trace gap.** A tracer attached directly to a
   compiled graph posts the root input before any node guard runs. It is
   closed by keeping graph inputs to bookkeeping. The static guard reads
   source, so it misses runs through `getattr` or from outside `app/`
   (`root-run-trace-gap.md`).
6. **The LOINC table is model-sourced, not taken from a LOINC release.**
   `agent/app/workers/intake_extractor/loinc.py` was written in the
   implementation session. No LOINC release file or terminology service is in
   the repository, and nothing cites one. The only mechanical check is LOINC's
   mod-10 check digit (`agent/tests/test_intake_extractor_loinc.py`), which
   catches a typo and **not** a valid code for the wrong analyte. eGFR is
   deliberately uncoded (D35 item 13). A deployment should verify every row
   against a LOINC release before writing results to a chart.
7. **Flag cross-check agreement rests on one comparable flag.** Across all six
   samples in replay it is 100% (**1/1**). Every other flag was blank or not
   located (§1.4 evidence file). It is not evidence that the extractor reads
   flags in general (D20, "Consequence for the eval gate").
8. **Prompt injection inside an uploaded PDF is contained, not tested for
   refusal** **[PENDING `feat/w2-eval-gate`]**. W2-I1 and W2-I2 assert that a payload in the text layer or
   the metadata reaches no log, trace, extraction output or disclosure. Their
   extraction bypasses the vision model, so nothing tests whether a model that
   reads the payload declines it. That is W2-I5 **[PENDING `feat/w2-eval-gate`]**, which is pending, along with
   I3 and I4 (§6). The structural defences are that the model writes no prose,
   its citation ids are narrowed by schema, and it has no write channel.
9. **No committed live run shows one answer citing a document and a
   guideline together.** The real-route API collection asks two separate
   questions, one about the document and one about guidance, and asserts on
   each (`docs/w2/evidence/api-collection-run.txt`).
   `agent/scripts/w2_e2e_ask.py` asks the combined question and exits non-zero
   without both citation types, but no output from it is committed. The
   combined answer is covered by in-process tests and gate cases only.
10. **A question about a whole uploaded report is too slow and too
    expensive.** 91.9 s and 130.5 s (n = 2) and about $0.46 per call: one
    drafting call over every extracted result, writing 11.5k tokens
    (`LOAD_TEST.md` §6). It breaks the 90 s budget for a single user with no
    load. It is a product fix, not a scale fix, and it is not built.
11. **A wrapped field's multi-box path is unit-tested only.** No sample wraps a
    field (D34).
12. **The chart card, the module upload routes and the correlation header are
    not wired** (§8, `docs/w2/ui-integration.md`). The UI is demonstrable in
    fixture mode only.
13. **No FHIR paging.** `_count` is the sharp edge (`docs/w2/WEEK1_DEBT.md` §4).
