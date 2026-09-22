WORK ORDER 07: text layer + locate engine (branch A, part 1).
Branch feat/w2-locate. Do not merge.

STEP 0: attempt a Write to agent/evals/w2/cases/probe.json. It must be
BLOCKED. If not, STOP.

READ FIRST: CLAUDE.md, docs/w2/DECISIONS.md (D3, D7), the factory
(agent/evals/w2/factory/, especially groundtruth.py and README.md; the
6 samples are in agent/evals/w2/factory/output/samples/; ground-truth
format doc-factory-groundtruth/0.2.0 with `rendering` and
`line_bboxes`), and the BBox convention on the contracts branch:
  git show feat/w2-contracts:agent/app/models/w2/citations.py
(PDF points, top-left origin).

GOAL
The deterministic half of D3: given a PDF, produce word boxes; given an
extracted value, find where it is on the page or report that it isn't.
The locate step is the verifier and the bbox source. No LLM calls.

IN SCOPE: a new module under agent/app/ (choose a location, e.g.
agent/app/extraction/), tests under agent/tests/.
OUT OF SCOPE: the vision call, the Pydantic contracts, write-back,
supervisor, anything under agent/evals/ except reading factory samples.

1. Text layer:
   - Digital pages: PyMuPDF word boxes.
   - Image-only pages (no text layer): OCR with Tesseract (pytesseract;
     tesseract is installed via brew), word boxes mapped into the same
     coordinate space. Record the choice (Tesseract vs docTR) and why.
   - One output shape for both: page number, words with text, bbox
     (PDF points, top-left origin), OCR confidence (1.0 for text layer).

2. Locate:
   - locate(value, page_words) -> located (bbox, matched text, score) |
     not_located | ambiguous (all candidate bboxes). Never silently pick
     one of several matches.
   - Normalisation: case, whitespace, hyphenation across line breaks,
     numeric forms (5 vs 5.0 vs 5.00), common unit spellings.
   - Multi-word and multi-line spans: union bbox of the matched words
     on that page (plus per-line boxes, matching the factory's
     line_bboxes).
   - Row-anchored locate for lab results: locate_in_row(label, value)
     finds the value on the same visual row as the test-name label, so
     a "5" is never matched from elsewhere on the page.
   - For fields the ground truth marks rendering=derived, locate the
     rendered_text (what is drawn), not the chart value.

3. Measure against factory ground truth (all 6 samples, including
   scanned ones via the recorded transform):
   - located rate per field type and per quality level
     (clean / scanned_light / scanned_heavy)
   - bbox IoU vs ground truth (report distribution; flag < 0.5)
   - false-locate rate: fields ground truth marks missing/illegible
     that locate() nonetheless "finds"
   - time per page (text layer vs OCR)
   Print these as a table in the report; they become the
   extraction-confidence metric.

4. Tests: unit tests for normalisation, multi-line union, ambiguity,
   row anchoring; a test running the measurement over the 6 samples
   with thresholds you propose (and justify) for clean pages.

ACCEPTANCE: tests pass; full unit suite still passes; ruff clean on
new files; the measurement table in the report.
/report. STOP. Do not merge.