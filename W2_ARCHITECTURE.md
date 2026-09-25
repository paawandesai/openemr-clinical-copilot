# Week 2 Architecture — Clinical Co-Pilot: documents, workers, evidence

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
9. **The measured code cannot move its own gate** (D8, D42–D48). Gated paths
   are hook-protected. 75 deterministic checks must pass at 100%, a regressed
   critical case blocks alone, and all 22 planted regressions block (§6).

**Tradeoffs:**

- **Recall for honesty.** The system locates less, and states no number the
  page lacks.
- **Reranking for latency.** Under load most retrievals go unreranked rather
  than queue (D39).
- **Local models for privacy.** Idle memory is 760–900 MiB against Week 1's
  50 MiB (`DEPLOY.md` §8), and in return D5 is structural.
- **One worker.** Correct for the demo, wrong for production (D40 item 10).

**Not true yet:**

- **The deployed flow has been measured one question at a time, never
  under load** (`DEPLOY.md` §8.0, `DEPLOY.md` §8.00).
- **The document + guideline question has not been re-measured since D50.**
  Its host figures (7 document + 2 guideline citations, 15.3–21.2 s) are
  from `21f1053`, before D50.

**Answer latency tracks what the model writes** (D50;
`docs/w2/COST_AND_LATENCY.md` §4.1a).

- **Before D50,** a whole-report answer on a 22-row report drafted all 91
  citations, about 11.5k output tokens, in 88.2–91.8 s (`LOAD_TEST.md` §7).
  That was past the 90 s budget.
- **D50 moves the work into code.** Code decides the scope, itemises
  critical, abnormal and named rows, and counts the rest in one cited
  statement of its own.
- **On the host,** the same question now answers in **6.9–8.4 s** end to
  end, with 49 document citations and 0 unverified, from 368–461 output
  tokens (`DEPLOY.md` §8.00).

§11 lists every known limitation.

---

## Provenance: snapshot and host figures

**Source of record.** Every capability below traces to a decision in
[`docs/w2/DECISIONS.md`](docs/w2/DECISIONS.md) (cited as Dn) or to code, and
every number traces to a committed artifact, cited where it is used. Week 1's
architecture is unchanged and is not repeated here:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (authorization, tool scope,
conversation state, the deterministic verifier).

**Snapshot: this document describes `chore/w2-final-hygiene`** (work order
28, not merged), which is `main@c75827f` plus the commits listed last below.
Every path and every decision cited is on that branch. Numbers are **not**
pinned to one commit. Each is pinned to the artifact it cites, and that
artifact names where it was measured:

- host figures to `ea198c8` (`DEPLOY.md` §8.00) or `21f1053` (§8.0);
- gate figures to `agent/evals/w2/baseline.json` and
  `docs/w2/EVAL_GATE_EVIDENCE.md`, which record the commit they scored;
- load and cost figures to their own `LOAD_TEST.md` and
  `COST_AND_LATENCY.md` sections.

An earlier version of this line said everything was pinned to `056cba9`.
It then cited W2-L1 and 97 specs, which landed afterwards (`1ddcfd5`). That
claim was wrong, and this replaces it.

`main@c75827f` includes these merges:

| Merge | At | What it brought |
|---|---|---|
| `feat/w2-php-wiring` | `2ae4d98` | the Documents & Sources card, its routes, the upload guard |
| `chore/w2-deploy-rehearsal` | `21f1053` | D41 |
| its first post-deploy figures | `bc5236f` | `DEPLOY.md` §8.0 |
| its D50-redeploy figures | `5ad81c5` | `DEPLOY.md` §8.00 |
| `feat/w2-eval-gate` | `0b4ae04` | the eval gate, D42–D48 |
| `feat/w2-answer-scope` | `ea198c8` | D50 |
| the DECISIONS restore | `caa92f8` | the dropped decision entries |
| `EVAL_GATE.md` | `0318c35` | the gate's page for reviewers |
| the decisions guard | `c1c5e21` | D51 |
| the event-tags fix | `056cba9` | D52 |
| `docs/w2-submission` | `2d12333` | this document, `KEY_METRICS.md`, D53 |
| `fix/w2-prompt-pins` | `1ddcfd5` | W2-L1, the prompt pin (97 specs) |
| `docs/w2-final-fixes` | `c75827f` | the host dashboard evidence |

On this branch, after `c75827f`: the CI unit-test job, W2-J8 (98 specs),
W2-L1 extended to schemas and user-message templates, the gated staleness
check, D55 (`/ready` names stdout; spans name the service) and the
gate troubleshooting notes (work order 28).

**The host runs `main@ea198c8`** since 2026-09-23 17:12 UTC (`DEPLOY.md`
§8.00). It lacks the event-tags telemetry fix (D52), D55 (`/ready` names
stdout as the backend; exported spans name the service), and the gate,
test and documentation commits after them. None of them changes an
answer.

**Three kinds of host figure:**

- **`DEPLOY.md` §8.00:** the D50 redeploy, on `ea198c8`.
- **`DEPLOY.md` §8.0:** the earlier deploy, on `21f1053`, **before D50**.
  D50 changes only how a document answer is scoped, so these still describe
  extraction, retrieval and memory. They do not describe the answer step.
- **Before any Week 2 deploy:** labelled "rehearsal host, before the Week 2
  deploy".

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
OCR on the deployed host adds 4.9–6.1 s per scanned page (`DEPLOY.md` §8.0; 5.7 s p50
on the rehearsal host, before the Week 2 deploy, `DEPLOY.md` §8).

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

Gate case W2-H2 asserts both halves and asserts the limitation, so it will
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
| Budget and circuit | **4.0 s** (D41, measured on the host). Over budget, the fused order stands, `reranked=false`, and the circuit opens. It **closes itself** after 60 s or 10 skipped turns, and a skipped turn is logged as `rerank_skipped_circuit_open`, apart from a slow one | `agent/app/retrieval/retriever.py:72`, `:91–92`; D41 |
| Admission | at most 2 concurrent reranks; the rest take the fallback at once (`rerank_busy`). On `main` and deployed (`main@21f1053`) | D39; `retriever.py:77` |
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
| Intake guard | the write-back request type refuses any document that is not a `lab_pdf`, so an intake form has no path into the chart | `agent/app/models/w2/writeback.py`; planted regression `auto-merge-intake` |
| Preview | `GET <module>/public/document.php`: session ACL `patients/docs`, the open patient only, PDF only. 404 is identical for missing, another patient's and deleted documents | D28, D30, D49 |
| Deletion | not designed: what a deleted document leaves behind, and why that is a gap | §11 item 14 |

**Verified on the rehearsal host, before the Week 2 deploy** (`DEPLOY.md` §7.3):

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

**Since D54 (work order 26, on `main` at `024f8fb`),** a lab report's
extraction writes its verified rows back, under the uploader's token, after
the report is stored. `upload.php` brokers `api:oemr user/lab-writeback.c`
for a lab report only. Rows land in Pending Review, and an intake form is
never written. Before D54, no agent route or graph called the write-back
client (`DEPLOY.md` §0, §10). The card still does not show the write's
outcome (D54, "Not settled here").

---

## 6. The eval gate

**How to find, run and read the gate: [`EVAL_GATE.md`](EVAL_GATE.md).** It
covers the golden set and prompts, the command and exit codes, CI and its
settings, and the five rules. This section keeps only what the architecture
rests on.

- **What it proves.** 22 planted regressions, each applied to a copy of the
  tree: the control run passes and all 22 block, each caught by at least the
  case the design names (`docs/w2/EVAL_GATE_EVIDENCE.md`, "Harness verdict:
  HELD").
- **A merge it blocked.** [MR !1](https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot/-/merge_requests/1) is left open with a red pipeline. It plants
  "blank flag means normal", and W2-D2 blocks it:
  `VERDICT: BLOCKED | specs 96 | active 79 | pending 15 | det 72/73 | judge 8/8`
  (`EVAL_GATE.md`; recorded before W2-L1, when there were 96 specs).
- **The gate at the snapshot.** 98 specs (81 active, 15 pending, 2
  delegated), 75/75 deterministic and 8/8 judge (evidence control run,
  re-seeded after W2-J8, the turn event's scope fields).
- **The judge is UNCALIBRATED.** It is `claude-haiku-4-5-20251001`,
  best-of-3 (D43, D48). Category M detects drift, not agreement with humans.
- **Its protection is a guard, not a boundary.** The hooks and
  `scripts/gate-diff.sh` stop mistakes, not a determined bypass
  (`docs/w2/GATE.md`, "Known limit").

**Prompt injection inside an uploaded PDF is covered on three
deterministic cases, for containment only.** `lab-clean-001` is re-rendered
with a hidden instruction on two channels: white-on-white text in the page's
text layer, and the PDF's metadata.

| Case | Asserts |
|---|---|
| W2-I1 | a payload in the text layer reaches no log entry, trace field, extraction output or disclosure |
| W2-I2 | the same, for a payload in the metadata |
| W2-I3 | an injected instruction cannot redirect a chart write |

- **I3 is structural, like H2 (§2.3).** `patient_uuid` is an argument the
  caller passes to the write planner, never read out of the document. A
  refactor that starts deriving the subject from extracted text is the
  change I3 would fail.
- **This is containment, not refusal.** The gate reads the document through
  a ground-truth stand-in for the vision model, so no model reads the
  injection. Whether a model shown the payload declines it is W2-I5, which
  is pending.
- **What catches it:** the planted regression `page-text-in-a-log-line` is
  caught by I1 and I2.

Sources: the commits that activated them, "activate W2-I1 and W2-I2" and
"activate W2-I3 — an injection cannot redirect a chart write".

**Delegated cases are scored against the job's own result** (how:
[`EVAL_GATE.md`](EVAL_GATE.md), "What runs in CI"). The reason is recorded:
while `w2-writeback-php` failed six pipelines in a row on a missing `unzip`,
W2-K4 and W2-K6 still reported a valid delegation. The gate checked only
that the job was declared, not that it passed (commit "score delegated cases
against the job's own result, not its config").

Pending, meaning specified but not scored:

| Category | Pending cases |
|---|---|
| B | B8, B9 |
| C | C7–C9 |
| F | F4 |
| G | G8–G10 |
| H | H1, H7–H9 |
| I | I4, I5 (I5 is the refusal half of the injection cases) |

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
- `/ready` reports the observability backend but does not gate on it
  (D37). It names the backend: `stdout` when Langfuse export is not
  configured, which is the production path, or `langfuse` with its auth
  check (D55; before D55 the stdout case read `disabled`).

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

**Two dashboards, two sources.** Production telemetry is **stdout plus a
self-hosted Langfuse inside our own infrastructure** (D58,
`docs/w2/OBSERVABILITY.md` §7). Week 1 kept production on stdout alone for one
reason: every event carried the patient uuid, and no BAA covers a
third-party tracing service. D56 made telemetry carry an HMAC of the session
key, and D58 does the same for the cited record's id. So a Langfuse we host
ourselves stores no patient or chart-record uuid; that was measured, not
assumed (D58). Stdout stays the record of source.

The Langfuse is up at <https://64-177-51-119.sslip.io> with a read-only grader
account. The live agent host has exported to it since the 2026-09-25
redeploy of `main@06f0d45`. A real chart-card upload arrived as one trace with
a `psk_` session, and no UUID OpenEMR issued is in any trace
([evidence](docs/w2/evidence/prod-langfuse/deploy-2026-09-25/README.md)).

- **Local:** the Langfuse screenshots,
  [`langfuse-dashboard.png`](docs/w2/evidence/screenshots/langfuse-dashboard.png)
  and [`langfuse-encounter-trace.png`](docs/w2/evidence/screenshots/langfuse-encounter-trace.png).
- **Production:** [the dashboard computed from the deployed host's logs](docs/w2/evidence/host-dashboard.html)
  ([JSON](docs/w2/evidence/host-dashboard.json)), by `agent/scripts/w2_dashboard.py`
  over the host's stdout from the `ea198c8` deploy, 2026-09-23 17:12–21:00
  UTC. It shows 83 requests, 0 5xx, 165 of 165 claims verified, and all five
  alerts `ok`. It holds aggregates only.

**Open chain breaks:**

- ~~The chart card never sends `X-Correlation-ID`.~~ Closed on `main` at
  `2ae4d98`: the card mints one UUID4 per chart open, and `AgentClient.php`
  sends it on every request (`CorrelationId.php`).
- Write-back carries no correlation id into PHP.
- Extraction inherits the upload's id only when that id is a UUID;
  otherwise the two traces are linked by a logged event, not merged.
- Since the 2026-09-25 redeploy (`main@06f0d45`), the live host's `/ready`
  reports `backend: "langfuse"`, healthy. If Langfuse cannot be reached,
  `status` is `degraded` at 200, never unready (`OBSERVABILITY.md` §7.4,
  D58).

---

## 8. Deployment state

The host `64.177.42.210` runs **`main@06f0d45`**, deployed 2026-09-25 14:44
UTC.

- It adds D54's upload scopes (module), D56, and D58: the access log is off,
  `source_id` is sent as an HMAC, and there is export to the self-hosted
  Langfuse. The Caddyfile is unchanged
  ([evidence](docs/w2/evidence/prod-langfuse/deploy-2026-09-25/README.md)).
- Before that it ran `main@ea198c8`, deployed 2026-09-23 17:12 UTC: the eval
  gate and D50 on top of `21f1053`.

The measurements below are from those earlier builds.

- It was first deployed at `21f1053` (16:40 UTC) and measured through the
  real chart card, one document at a time (`DEPLOY.md` §8.0).
- The D50 redeploy re-verified the guard and measured the whole-report
  question (`DEPLOY.md` §8.00).
- Rows below are from §8.0 unless marked. They describe `21f1053`, and D50
  changes only the answer step.

| Surface | `main` | Host (`DEPLOY.md` §8.0; §8.00 where marked) |
|---|---|---|
| Week 1 brief + conversation in the chart | yes | yes; brief 24/24 and medication and lab turns unchanged after the deploy |
| Documents & Sources card and its routes | yes (`Bootstrap.php`; `public/{upload,upload-status,ask,document}.php`) | yes; the post-deploy run was driven through it |
| `POST /documents` | the real extractor, behind the upload guard (token, scopes and OpenEMR's acceptance, then rate limits, declared size and an extraction slot, all before the body is read) | yes; the guard was verified on the new image **before** the proxy served it: 401 with no bearer or a made-up one, 403 without the scopes, 200 with them, 409 for the same bytes on a second chart, and 422 for an unknown patient, refused on its id format before OpenEMR is asked (`DEPLOY.md` §2.2, §8.0) |
| Extraction | vision + locate + checks | one vision call per document, 15.7–54.5 s; OCR +4.9–6.1 s per scanned page; locate and verify ~1 s; 0 parse failures over five documents |
| `POST /w2/ask` (supervisor) | yes | yes. On `21f1053`, before D50, the document + guideline question answered 3 of 3 with 7 document + 2 guideline citations, 0 unverified, 15.3–21.2 s end to end. On `ea198c8`, the whole-report question answered 3 of 3 per report as `partially_answered` (D50 item 7), 0 unverified: 22 rows 6.9–8.4 s with 49 document citations, 44 rows 12.0–13.1 s with 97 (`DEPLOY.md` §8.00) |
| Retrieval, D39 admission, D41 budget and circuit | yes | yes; retrieval including rerank 1.55–1.68 s, and every rerank ran, none over budget, busy or skipped |
| Preview by a citation's digest | yes | 200 `application/pdf` with that chart open, bytes identical to the upload; 404 with another chart open |
| Memory | agent capped at 4.5 GiB | peak **1.32 GiB** over the run, 0 OOM kills; 3.7 GiB is the stress bound with concurrent reranking (`DEPLOY.md` §8) |
| Lab write-back endpoint and ledger | yes | yes; rehearsed on the rehearsal host, before the Week 2 deploy (§5) |
| Eval gate | yes (`0b4ae04`) | n/a: runs on fixtures (D6) |

**What the deploy did not measure:** load. Every post-deploy figure is one
document or one question at a time, so the concurrency limits (the D39
rerank slots, the extraction slots, OpenEMR's PHP tier) are measured only
locally or on the rehearsal host, before the Week 2 deploy.

The upload guard's checks run in this order, and only then is the body
read:

1. the token, its scopes, and OpenEMR's acceptance;
2. rate limits;
3. the declared size;
4. an extraction slot.

The agent image grew from 360 MB to 1.18 GB, and idle memory from 50 MiB to
760–900 MiB (`DEPLOY.md` §8).

---

## 9. Risks and tradeoffs

| Risk | Where it bites | Mitigation today | What is still exposed |
|---|---|---|---|
| **The whole-report answer** (fixed on `main` and on the host) | before D50: answer step 88.2–91.8 s, 91 citations, ~11.5k tokens out, about $0.45 (`LOAD_TEST.md` §7); whole request 91.9 s and 130.5 s (`LOAD_TEST.md` §6) | D50: code scopes and counts; the answer step is 6.9 s p50 in process (`LOAD_TEST.md` §7), and on the host 6.9–8.4 s end to end, ~$0.084 (`DEPLOY.md` §8.00) | the 44-row "Show me all the results" case lands at 43.4 s, at the 45 s alert (`LOAD_TEST.md` §7) |
| **One process, in-memory stores** (conversations, documents, extraction jobs, the rerank circuit) | a second worker or replica makes uploads invisible (`404`), re-bills extraction, and loses follow-up context; a restart forgets every upload | one uvicorn worker; a startup warning when more are configured (D40 item 10, `agent/docs/deployment.md` §7) | a second *replica* is invisible to the check; no persistence |
| **Reranker under load** | 50 concurrent retrievals: p95 335,224 ms, 0% reranked, 5.3 GiB peak RSS on a cold run (`LOAD_TEST.md` §5) | D39 admission bound: p95 259 ms, but only 1% reranked in the burst and 6.2% at 50 paced users (§5b). A 4.5 GiB memory cap on the agent, so overload restarts the agent rather than the kernel killing the database (`DEPLOY.md` §8) | deployed. After the deploy, one document at a time, the agent peaked at 1.32 GiB (`DEPLOY.md` §8.0). The stress bound, with concurrent reranking, is **3.7 GiB**, and a heavier mix was OOM-killed at a 5 GiB test cap (`DEPLOY.md` §8) |
| **The rerank circuit latched** (fixed on `main`) | one slow rerank turned reranking off until restart; 35 of the next 60 turns ran unreranked (D41) | it closes itself after 60 s or 10 skipped turns, and skipped turns are reported apart from slow ones (D41); at demo load it opened, skipped 10 turns and closed (`DEPLOY.md` §8) | deployed; in the post-deploy questions no rerank was skipped (`DEPLOY.md` §8.0) |
| **OCR contends with retrieval** | OCR in the same container raises rerank p95 from 2.08 s to 3.39 s. Two concurrent OCR jobs took ~162 s per page with Tesseract's default threads (`DEPLOY.md` §8) | 4 s budget (D41); `OMP_THREAD_LIMIT=1` baked into the image, 5.6 s per page with two workers (`DEPLOY.md` §8) | extraction still runs in the serving process (`LOAD_TEST.md`, "Where the new bottleneck is") |
| **OpenEMR's PHP tier** | saturated at 10 users locally (420% CPU) and failing 58% at 50 (`LOAD_TEST.md` §1) | none agent-side | Week 1 finding, unchanged |
| **Upload surface** | `/documents` accepts uploads from any clinician token | the upload guard, on `main` since `2ae4d98` (§8) | deployed and verified on the new image before the proxy served it (`DEPLOY.md` §8.0) |
| **Recall versus honesty on scans** | a heavy scan presents a third of its values | unsupported values disclosed, possible criticals named without a number | a clinician must open the PDF for the rest |
| **USPSTF licensing reading** | verbatim-only redistribution | substring-enforced quotes (D26) | human sign-off pending; the "no AHRQ endorsement" UI constraint is not implemented (D26 item 3) |
| **Critical-value table** | a demo list | exact LOINC and unit match | not reviewed for any site (D40 item 9) |

---

## 10. Decisions this rests on

| Area | Decisions |
|---|---|
| Extraction | D3, D7, D10, D11, D13, D16, D20 (supersedes D12), D24, D35 |
| Contracts | D9, D14, D17, D18, D21, D22 |
| Routing and the ask path | D4, D40, D50 (the scoped document answer) |
| Retrieval | D25 (supersedes D5, D23), D26, D39, D41 |
| UI | D27, D28, D30, D32, D33, D34, D49 (the preview URL; supersedes D28 and D30 on that point), D51 (the card's module routes; a citation's digest opens the preview) |
| Write-back | D1 (superseding), D2, D15, D29, D31 |
| Observability | D36, D37, D38, D52 (a turn's tags reach the operational events), D55 (`/ready` names stdout as the backend; spans name the service) |
| Evals | D6, D8, D42–D48 |
| The decisions log itself | D53 (no merge driver for `DECISIONS.md`; a numbering test guards it) |

D1–D54 are on `main`. D53 records a decision already made in commit
`cb99e7b`; it was appended at the owner's request, not decided here. D54
(work order 26) makes a lab upload's extraction write its verified rows
back. D55 is on this branch only. This document makes no new decision.

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
   prompt (D46). Blank-is-not-normal is held deterministically instead: by
   code-rendered sentences (§4) and by gate case W2-D2. The judge is
   **uncalibrated** against human labels (D43, D48).
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
   refusal**.
   - W2-I1 and W2-I2 assert that a payload in the text layer or the
     metadata reaches no log, trace, extraction output or disclosure.
   - W2-I3 asserts it cannot redirect a chart write.
   - Their extraction bypasses the vision model, so nothing tests whether a
     model that reads the payload declines it. That is W2-I5,
     which is pending along with I4 (§6). The structural defences are that the model writes no prose,
   its citation ids are narrowed by schema, and it has no write channel.
9. **Live evidence for a combined answer is six runs, all before D50.** On
   the deployed host at `21f1053`, three runs of the document + guideline
   question each cited 7 document + 2 guideline values, 0 unverified
   (`DEPLOY.md` §8.0). Not re-measured on `ea198c8`. Before that, on the
   local stack: From the real chart (`docs/w2/evidence/php-wiring-e2e-run.md`), three answers to one question cited
   a document and a guideline together: 6 + 4 + 2 citations, then 6 + 0 + 2
   twice, with 0 unverified each time. None of them ran on the host.
   `agent/scripts/w2_e2e_ask.py` asks the same question in process; no
   output from it is committed.
10. **The whole-report question was too slow before D50, and the fix is
    narrower than it looks.**
    - Before: the answer step took 88.2–91.8 s and about $0.45, citing all 91
      values on a 22-row report (`LOAD_TEST.md` §7).
    - After D50 on the host: 6.9–8.4 s and about $0.084 (`DEPLOY.md` §8.00).
    - The answer is `partially_answered` by design: in-range rows are counted
      in one cited statement, not itemised (D50 item 7).
    - "Show me all the results" on 44 rows still takes 43.4 s p50, at the
      45 s alert (`LOAD_TEST.md` §7).
    - The 10,000-token output ceiling is about 76 s of writing (D50 item 5),
      which is inside the 90 s budget, but only just.
11. **A wrapped field's multi-box path is unit-tested only.** No sample wraps a
    field (D34).
12. **The deployed flow is measured one question at a time only.** The card,
    its routes, the guard and the scoped ask path run on the host
    (`main@ea198c8`, `DEPLOY.md` §8.00). No load test has run against them there.
13. **No FHIR paging.** `_count` is the sharp edge (`docs/w2/WEEK1_DEBT.md` §4).
14. **Deleting a document is a gap, not a provenance design.** Nothing was
    decided about deletion. This is what the code does. It was read from
    the code, not exercised end to end.
    - **OpenEMR's delete** (`interface/patient_file/deleter.php`,
      `delete_document`) sets `documents.deleted = 1`, keeps the row, and
      removes its `categories_to_documents` link.
    - **The module keeps no attachment record of its own.** Its only table
      is the write-back ledger. Its "already filed on another chart" check
      (`QueryUtilsChartDocumentIndex::isFiledOnAnotherPatient`) reads
      `deleted = 0` rows joined to their category. So once a file is
      deleted, the module **would** let the same bytes be attached to a
      different chart. It is not true that a deleted file can never be
      re-attached elsewhere.
    - **The agent refuses it anyway, until it restarts.** Its binding of a
      digest to a patient (`DocumentStore`, D40 item 8) is process memory,
      and nothing tells it about the deletion. So the card gets 409 on
      another chart while this agent process lives, and not after a restart.
    - **The write-back ledger outlives the deletion, and its replay does
      not check the patient.** Its rows stay, keyed on `<document
      sha3-512>:<TEST CODE>:<YYYY-MM-DD>` (D1), which does not include the
      patient. `LabWriteBackService` answers a request whose keys are all
      in the ledger by replaying the recorded ids, before it resolves the
      document or looks at the patient those ids belong to. So a write-back
      for patient B, with bytes, test and date already written for
      patient A, would return A's `procedure_order_id` and result ids as
      `duplicate_ignored`, and write nothing for B. That needs the same
      bytes on two charts. The card path allows that only after a deletion
      plus an agent restart. Even then, the write the upload itself makes
      (D54) runs only when the report's name matches B's chart and neither
      name nor date of birth mismatches. A's report on B's chart is
      therefore refused as `identity_not_confirmed` before any request,
      unless the two patients share a name and date of birth. A direct call
      to the endpoint with the write scope reaches the replay without any
      of this.
    - **What would close it:** a ledger replay that refuses a key recorded
      for a different `patient_uuid`, and a decision on whether a deleted
      document's ledger rows should block, or release, its bytes. Neither is
      built.
