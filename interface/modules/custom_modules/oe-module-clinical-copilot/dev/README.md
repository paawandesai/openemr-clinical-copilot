# Fixture mode, and the two scripted checks

Development only. Nothing here is served by the module, nothing here holds
credentials, and the "documents" are the six synthetic doc-factory samples,
which contain no patient data.

It exists so the source-grounded answer view and its PDF overlay can be
seen, clicked and **measured** before the Week 2 graph, the extractor or the
module's document route exist.

## Build the fixtures first

```bash
cd agent && python scripts/build_ui_fixtures.py
```

That reads the six committed samples under
`agent/evals/w2/factory/output/samples/` and writes, per scenario, into
`dev/fixtures/` (gitignored):

| file | what |
|---|---|
| `document.pdf` | the sample PDF, byte-for-byte |
| `ground-truth.json` | the sample ground truth, byte-for-byte |
| `answer-view.json` | an `AnswerView` built from them, validated by the real contracts |

For the two **scanned** scenarios the recorded boxes are mapped out of the
clean layout and into the scanned page's own point space before any
contract object is built, through the affine the factory records in
`transform.pages[n].pdf_points_to_image_px`. That matches what the locate
step (D3) will produce in production: a `bbox` is always in the space of
the page the viewer draws on, and the viewer never learns which files were
scanned.

## Look at it

```bash
node interface/modules/custom_modules/oe-module-clinical-copilot/dev/serve.mjs
# http://127.0.0.1:8760/dev/fixture.html
```

Run it from the repository root, exactly as above; it works from a checkout
whose path contains spaces. It always prints `serving <dir>` and the URL —
if it exits without printing, that is a bug, not a quiet success. An optional
port follows the path (`… serve.mjs 0` picks a free one).

The scenario picker switches samples; `?scenario=<id>` deep-links one.
Click any **document** chip to scroll the page to its region and highlight
it; chart and guideline chips open their own detail instead, because
neither has a page to point at. The upload control is wired to a canned
status source that walks `pending -> in_progress -> complete`.

## Check it

Both need an installed Chrome or Chromium; neither needs `node_modules`.
They drive the browser over the DevTools protocol with Node's built-in
`WebSocket` (`dev/browser.mjs`), in a throwaway profile under the system
temp directory.

```bash
node .../dev/check-overlay-alignment.mjs [--scale 1.5] [--chrome PATH]
node .../dev/check-answer-view.mjs [--chrome PATH]
```

### `check-overlay-alignment.mjs`

Renders every sample and asks two independent questions of every box.

**Is it where the ground truth says?** The expected rectangle is computed
in the checker, from the committed `*.ground_truth.json`, without touching
`bbox-overlay.mjs`: every sample page is upright US Letter with the CropBox
at the origin — asserted, not assumed — so the expectation is `box * scale`.
The scan mapping is reimplemented too (`ground-truth-scan.mjs`), so the
builder and the checker have to agree independently. Tolerance is 0.01 CSS
px.

**Is there ink under it?** Geometry can be self-consistently wrong. So the
checker reads the rendered canvas: each box over printed text must contain
dark pixels, and the boxes must hold markedly more ink than the leading
immediately above and below them.

Last run, all six green at scales 0.75, 1, 1.5 and 3:

```
ok    intake-clean-004                   21 boxes,   21 ink-checked, ink 24.2% vs leading 0.0%
ok    intake-missing-allergy-006         28 boxes,   27 ink-checked, ink 24.9% vs leading 0.0%
ok    intake-scanned-light-med-005       36 boxes,   36 ink-checked, ink 18.1% vs leading 0.0%
ok    lab-clean-001                      28 boxes,    6 ink-checked, ink 25.0% vs leading 5.4%
ok    lab-multipage-003                  55 boxes,   12 ink-checked, ink 23.8% vs leading 0.0%
ok    lab-scanned-heavy-abnormal-002     43 boxes,   24 ink-checked, ink 28.3% vs leading 11.4%
```

**The check bites.** Two mutations were run against it:

| mutation | result |
|---|---|
| `pageSpaceToUserSpace` reads y bottom-left | every scenario fails, boxes off by up to 134px |
| the builder ignores the scan transform | only the two scanned scenarios fail, by 0.01–0.6px |

The second is the one worth noticing: the light scan is so nearly the
identity that the **ink check still passed**. Only the cross-implementation
geometry comparison caught it. Neither control is redundant.

### `check-answer-view.mjs`

Reads the rendered DOM — not the view model — and asserts the things the
work order and the invariants require a reader to be able to see:

* patient facts and guideline evidence in separate sections, with no chip
  under the wrong heading (invariant 3);
* chart and document facts separated, and the document section saying its
  contents are not merged into the chart (D2, invariant 5);
* no `unsupported` field path appearing as a fact (invariant 4);
* no row with a blank or unrecognised flag column rendering as "normal" or
  as agreement, and the comparable count shown with its denominator (D20);
* one chip per `(source type, source id, field)` the claims cite.

## Unit tests, no browser

```bash
node --test 'interface/modules/custom_modules/oe-module-clinical-copilot/tests/js/**/*.test.mjs'
```

56 tests over the coordinate transform, the flag labels, the citation
grouping and the upload control's decisions. No dependencies.
