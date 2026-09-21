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
patient_ref: "18"              # OpenEMR pid, pubpid, or uuid hex
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
`schema_version` (`doc-factory-groundtruth/0.1.0`) so a later adapter can
convert it without guessing.

```jsonc
{
  "schema_version": "doc-factory-groundtruth/0.1.0",
  "scenario":    { "scenario_id": ..., "seed": ..., "perturbations": [...] },
  "chart_source":{ "system": "local-openemr", "origin": "cache" | "openemr", ... },
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
      "page": 1, "bbox": [230.0, 396.3, 275.6, 407.2], "rendered_text": "168" }
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

* PDF output is byte-stable because `canvas.canonical_pdf_bytes` rewrites the
  random `/ID` MuPDF stamps into the trailer. It raises rather than returning
  un-canonicalised bytes, because a silently-random id is a reproducibility bug
  that only shows up one save in fifty.
* The **scanned** PDFs additionally depend on Pillow's resampling and JPEG
  encoder, so their bytes are stable within one environment but may differ
  across Pillow versions. The ground truth and the `.clean.pdf` are stable
  everywhere, and the tests check those two separately.

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
