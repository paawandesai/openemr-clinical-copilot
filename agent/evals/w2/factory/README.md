# Synthetic document factory (W2)

Turns a YAML scenario spec into a realistic **lab PDF** or **patient intake
form** for an existing synthetic OpenEMR patient, together with exact ground
truth — value, unit, page and bounding box — for every field it renders.

Two things make it useful rather than just decorative:

* **The document matches the chart by default.** Demographics, medications,
  allergies and conditions are read from the local OpenEMR instance. A
  *perturbation* is the only way a document is allowed to disagree with the
  chart, and every perturbation is recorded with the chart value, the document
  value and a sentence describing the discrepancy.
* **Every rendered value is located.** The renderer computes each bounding box
  analytically from PyMuPDF font metrics as it draws, and `validate` re-extracts
  the text afterwards and checks the two agree. Nothing is ever silently
  dropped: a field a perturbation removed is recorded as explicitly `absent`,
  and a field it scribbled out is recorded as `illegible` — both keeping the
  chart truth in `value`.

## Quick start

```bash
cd agent

# render every committed sample scenario
python -m evals.w2.factory generate evals/w2/factory/specs --out evals/w2/factory/output/samples

# check the ground truth against the PDFs
python -m evals.w2.factory validate evals/w2/factory/output/samples
```

`generate` reads the chart from `evals/w2/factory/cache/` when it can, so neither
command needs OpenEMR running. Pass `--refresh` to re-read from the database.

## The six committed samples

| Scenario | Type | Quality | What it exercises |
|---|---|---|---|
| `lab-clean-001` | lab | clean | Baseline. CMP + lipid + A1c, everything agrees with the chart. |
| `lab-scanned-heavy-abnormal-002` | lab | scanned_heavy | Image-only PDF, no text layer. High glucose, high ALT, low potassium, all flagged. |
| `lab-multipage-003` | lab | clean | Six panels; results continue onto page 2 with the header repeated. Ferritin flagged low on the continuation page. |
| `intake-clean-004` | intake | clean | Baseline handwritten form; medications and allergies match the chart. |
| `intake-scanned-light-med-005` | intake | scanned_light | Light scan; one medication written at a changed dose. |
| `intake-missing-allergy-006` | intake | clean | Date of birth never filled in, city/state/ZIP scribbled out, one charted allergy omitted. |

Each lands in `output/samples/<scenario_id>/` as:

```
<scenario_id>.pdf                 the document under test
<scenario_id>.clean.pdf           scanned qualities only — the layout every bbox refers to
<scenario_id>.ground_truth.json
```

## Spec format

One YAML file per scenario, in `specs/`.

```yaml
scenario_id: lab-clean-001     # lowercase [a-z0-9._-], unique within a run
seed: 20260921                 # every random choice derives from this
patient_ref: "a2c373ec-…"      # the patient FHIR UUID (see "Which patient")
doc_type: lab_pdf              # lab_pdf | intake_form
quality: clean                 # clean | scanned_light | scanned_heavy
note: free text, carried into the ground truth for humans
perturbations:
  - kind: abnormal_value
    test: glucose
    direction: high
```

### Perturbations

| `kind` | Parameters | Applies to | Effect |
|---|---|---|---|
| `missing_field` | `field:` a ground-truth field path | both | The value is not drawn. Recorded with `status: absent`, `bbox: null`, and the chart value still in `value`. |
| `illegible_field` | `field:` | both | A scribble is drawn where the value goes — ink, but nothing in the text layer. Recorded with `status: illegible`. |
| `abnormal_value` | `test:` (`glucose`, or `cmp.glucose`), `direction: high\|low` | lab | The result is pushed clear of its reference interval and flagged `H`/`L`. |
| `med_discrepancy` | `mode: add\|omit\|dose_change` | intake | The form's medication list disagrees with the chart in exactly one place. |
| `allergy_discrepancy` | `mode: add\|omit` | intake | Same, for allergies. |
| `wrong_patient_name` | — | both | The document is headed with a different person's name. |
| `multi_page` | — | lab | Six panels are ordered instead of three, so results run past page 1. |

A perturbation that names a field the document does not render, or a lab test
in a panel the report does not order, fails the run with a message listing what
*is* available — rather than being silently ignored.

## Ground truth

**This is not the Phase 2 schema.** It is an interim shape carrying a
`schema_version` (`doc-factory-groundtruth/0.2.0`) so a later adapter can
convert it without guessing.

```jsonc
{
  "schema_version": "doc-factory-groundtruth/0.2.0",
  "scenario":    { "scenario_id": ..., "seed": ..., "perturbations": [...] },
  "chart_source":{ "system": "local-openemr", "patient_uuid": "a2c373ec-…", ... },
  "coordinates": { "space": "clean_pdf", "units": "pt", "origin": "top-left", "y_axis": "down" },
  "document":    { "path": ..., "sha256": ..., "page_count": ..., "has_text_layer": false },
  "clean_reference": { "path": "<id>.clean.pdf", "sha256": ..., "has_text_layer": true },
  "transform":   { "kind": "scan", "profile": {...}, "pages": [ { "skew_degrees": ..., "pdf_points_to_image_px": [a,c,e,b,d,f] } ] },
  "pages":       [ { "number": 1, "width": 612.0, "height": 792.0 } ],
  "perturbations": [
    { "kind": "abnormal_value", "params": {...}, "field_paths": [...],
      "chart_value": null, "document_value": "168 mg/dL",
      "expected_discrepancy": "Glucose is reported as 168 mg/dL, above ..." }
  ],
  "fields": [
    { "field_path": "results.cmp.glucose.value", "status": "present",
      "kind": "text", "origin": "perturbed", "value": 168.0, "unit": "mg/dL",
      "page": 1, "bbox": [230.0, 396.3, 275.6, 407.2], "rendered_text": "168",
      "rendering": "verbatim" }
  ]
}
```

### Coordinates

Every `bbox` is `[x0, y0, x1, y1]` in **PDF points on the original clean
layout** — on `<scenario_id>.clean.pdf`, never on the degraded scan — with the
origin at the **top-left** of the page and **y increasing downward**. That is
PyMuPDF's page space; for PDF-native bottom-left coordinates use
`y_pdf = page_height - y`.

For a scanned variant, `transform.pages[i].pdf_points_to_image_px` is
`[a, c, e, b, d, f]` such that

```
image_x = a*x + c*y + e
image_y = b*x + d*y + f
```

maps a clean-layout point to a pixel of that page's scan image. It is the
rotation used to skew the page composed with the `dpi/72` raster scale — the
same matrix the degradation pipeline inverted to produce the image, not an
estimate.

### How rendered_text relates to value

`rendering` says why the drawn characters differ from `value`, so a reader
never has to infer it from the strings:

| `rendering` | meaning |
|---|---|
| `verbatim` | drawn exactly as `value` reads |
| `derived` | deliberately re-expressed; `note` says how. A medication is the clearest case: the chart holds `"Fexofenadine hydrochloride 30 MG Oral Tablet"` and the form carries `"Fexofenadine hydrochloride"`, because that is what a patient writes. **Not a perturbation and not a truncation** — the document is what a real one would say, and no discrepancy is implied. The letterhead drawn in caps is the same category. |
| `wrapped` | drawn over more than one line. `rendered_text` is the lines joined with single spaces — the whole text, never just the first line — `bbox` is the union of the line boxes, and `line_bboxes` holds them individually. |
| `truncated` | cut to fit its column and ending in an ellipsis. `value` keeps the full text; an extractor cannot recover it from the document. |

Fitting happens inside `canvas.emit_field` (via `canvas.fit`), not at the call
sites, so a value that had to be cut is always recorded as `truncated` rather
than looking like a shorter value that was that length all along. `fit` breaks
at a word boundary where there is one, so a cut reads as a shortened phrase
rather than a word sliced in half.

A field's lines must all land on one page. `canvas.multiline_field` raises
`PageBreakInField` rather than recording the part that fits, because a field
split across a page break has no single bbox and silently keeping the first
half is exactly the failure this is here to prevent. No field in the six
samples wraps or is truncated today; `tests/test_doc_factory.py` exercises both
paths with charts built for the purpose.

### Field statuses and origins

`status` is `present`, `absent` (not drawn at all) or `illegible` (drawn as ink,
absent from the text layer). `absent` and `illegible` both keep the chart truth
in `value`, so a scorer can tell "the extractor missed it" from "the document
never said it".

`origin` says where the value came from: `chart` (verbatim), `chart_derived`
(e.g. a drug's common name pulled out of an RxNorm product string),
`synthetic` (accession numbers, ordering provider, family history — nothing in
the chart contradicts them), or `perturbed`.

## Determinism

Same spec + same seed produces byte-identical PDFs and identical ground truth.
Every random draw comes from a stream keyed on `(seed, purpose)` — never the
global `random` module, never the clock. Document dates are offsets from a
fixed anchor (`content.ANCHOR_DATE`) for the same reason.

Two caveats worth knowing:

* Nothing that depends on *how a run went* may enter the ground truth. This is
  easy to get wrong: `chart_source` briefly recorded whether the chart came
  from the cache or from OpenEMR, which made `generate --refresh` and a cached
  `generate` emit different bytes for the same spec.
* PDF output is byte-stable because `canvas.canonical_pdf_bytes` rewrites the
  random `/ID` MuPDF stamps into the trailer. It raises rather than returning
  un-canonicalised bytes, because a silently-random id is a reproducibility bug
  that only shows up one save in fifty.
* The **scanned** PDFs additionally depend on Pillow's resampling and JPEG
  encoder, so their bytes are stable within one environment but may differ
  across Pillow versions. The ground truth and the `.clean.pdf` are stable
  everywhere, and the tests check those two separately.

## Which patient a spec names

`patient_ref` is the patient's **FHIR UUID**, in the dashed form OpenEMR
returns as `Patient.id`. `pid` and `pubpid` are also accepted, but do not use
them in a committed spec: OpenEMR assigns both per instance at import time, so
a spec keyed on a pid means a different person on a different deployment — or
nobody at all. A first pass at this keyed on pids, and every patient in it was
absent from the deployed instance.

The cached chart records the UUID too (`record.uuid`), and every ground-truth
file repeats it at `chart_source.patient_uuid`, so a generated document can be
checked against a live FHIR server without going back to the database.

The three patients the samples use were picked from the deployed population by
chart richness — each has medications, allergies, conditions and labs — with
two deliberate exclusions: the Week 1 F-57 patient, whose penicillin allergy
carries a data-absent code and exists to exercise that defect, and one other
patient carrying a penicillin allergy. Those belong to eval cases that target
the defect, not to general-purpose document scenarios.

| uuid | meds | allergies | conditions | labs | used by |
|---|---|---|---|---|---|
| `a2c373ec-…663d` | 22 | 3 | 36 | 183 | `lab-clean-001`, `intake-scanned-light-med-005` |
| `a2c373d1-…db1c7` | 8 | 9 | 32 | 215 | `lab-scanned-heavy-abnormal-002`, `intake-clean-004` |
| `a2c37430-…b2654a` | 14 | 10 | 21 | 39 | `lab-multipage-003`, `intake-missing-allergy-006` |

(Counts are FHIR resource counts. The cache holds fewer medications, because
OpenEMR emits one `MedicationRequest` per `lists` row *and* one per
`prescriptions` row while the chart holds each drug once.)

## Chart source

`chart.py` is the only code that talks to OpenEMR. It issues `SELECT`s inside a
`SET SESSION TRANSACTION READ ONLY` session and writes what it reads to
`cache/patient-<ref>.json`; every later run prefers the cache. Connection
settings come from the environment, then `agent/.env`, then the published
defaults of `docker/development-easy`:

| Variable | Default |
|---|---|
| `FACTORY_OPENEMR_DB_HOST` | `127.0.0.1` |
| `FACTORY_OPENEMR_DB_PORT` | `18320` |
| `FACTORY_OPENEMR_DB_NAME` | `openemr` |
| `FACTORY_OPENEMR_DB_USER` | `root` |
| `FACTORY_OPENEMR_DB_PASSWORD` | `root` |

Credentials are never logged, and CLI output names no patient: scenario ids,
counts and file paths only. The generated documents and the ground truth do of
course contain the synthetic patient's details — that is what they are for.

Chart names are used **verbatim**. The local instance is seeded with Synthea
data, whose names carry numeric suffixes (`Collier206`). Prettifying them in the
document would create a chart/document disagreement that nothing in the ground
truth records, which is exactly the property this factory exists to protect.

## Fonts

Everything is drawn in the PDF base-14 fonts, so no font file has to ship and
output stays byte-stable across machines. The intake form's handwriting variant
is Times-Italic drawn one glyph at a time with a seeded baseline, advance and
size wobble (`canvas.DocumentCanvas._draw_hand`). Because the glyphs are
separate text runs, PyMuPDF splits a written answer into more "words" than were
drawn, so `validate` compares whitespace-insensitively.

## Layout

```
evals/w2/factory/
  spec.py         YAML -> typed ScenarioSpec, with all the validation
  chart.py        read-only OpenEMR pull + the local cache
  content.py      (spec + chart) -> what to draw, perturbations applied & recorded
  panels.py       lab panel catalog and reference intervals
  canvas.py       PyMuPDF drawing layer; records a FieldRecord per value drawn
  render_lab.py   lab report layout
  render_intake.py intake form layout
  degrade.py      rasterise + degrade + re-wrap as an image-only PDF
  groundtruth.py  the interim ground-truth dataclasses
  generate.py     the pipeline
  validate.py     the checks
  cli.py          python -m evals.w2.factory
  specs/          the six committed sample scenarios
  cache/          committed charts for those scenarios
  output/         generated documents (gitignored except output/samples/)
```

Tests: `agent/tests/test_doc_factory.py`, run with `python -m pytest tests`
from `agent/`. They need no database and no network.

## When the Phase 2 schemas land

The Pydantic `lab_pdf` / `intake_form` models are owned by another session.
When they exist, expect to write an adapter from this file format to those
models and to revisit:

* **field paths** — `results.<panel>.<test>.value`, `medications[0].dose` and
  friends are this module's invention and will need mapping onto the schema's
  names;
* **`value` typing** — numeric results are JSON numbers and everything else is a
  string; the schema may want stricter types, units as enums, or dates as
  `date` rather than `YYYY-MM-DD` text;
* **`perturbations`** — the per-scenario discrepancy list is shaped for a human
  or a judge to read, not for a scorer to consume field by field;
* **`schema_version`** — bump it in `groundtruth.py` if this format changes
  before the adapter exists.
