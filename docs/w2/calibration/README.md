# Judge calibration

The gate's two judge rubrics, `factually_consistent` and `safe_refusal`, are
graded by an LLM judge that every gate run prints as **UNCALIBRATED**: nobody
has measured how often it agrees with a person. This directory holds the
measurement. The method is D57 in [`../DECISIONS.md`](../DECISIONS.md). Until
this branch merges, its heading there reads "D-unassigned (work order 27,
reserved D57)".

**Phase 1 (this branch)** built the label set, the labelling page and the
judge's verdicts. **Phase 2** is your labels and the agreement number.

## What is here

| Path | What it is | Open it before labelling? |
|---|---|---|
| `label.html` | The labelling page. One file, works offline. | **Yes.** This is the only file you need. |
| `sealed/items.json` | The 60 items, each with its intended label, hard-case mode and rationale. | **No.** |
| `sealed/judge_verdicts.json` | The pinned judge's verdict, votes and reasons for every item, and the cost of the run. | **No.** |
| `../../../agent/scripts/judge_calibration.py` | `build`, `judge`, `compute`, `synthetic`. | Only after labelling. `compute` prints the judge's verdicts. |
| `../../../agent/scripts/judge_calibration_page.html` | The page template. `build` puts the items into it. | No need. |

Everything in `sealed/` would bias your labels. Opening an item's intended
label turns the result into agreement with the item's author, and opening a
verdict turns it into agreement with the judge. Neither is what this measures.

## How to label

1. **Open `docs/w2/calibration/label.html` in a browser.** Double-clicking it
   works. It makes no network request.
2. Type your name or initials into **Labeller**. It goes into the file you
   download.
3. For each item, read the text on the left and the rubric on the right, then
   choose one:
   - **PASS**: the item meets the rubric.
   - **FAIL**: it does not.
   - **UNSURE**: you cannot decide from what is shown. Use it when you mean
     it. UNSURE items are counted and listed, and they are left out of the
     agreement number.

   Add a note when you choose FAIL or UNSURE: one line on why is enough. Your
   notes are printed next to every disagreement in phase 2.
4. Keys: `P`, `F`, `U` to label, `←` `→` to move. Keys do nothing while you
   are typing a note. **Next unlabelled** jumps to the next gap. The grid at
   the top marks the items you have labelled with ✓. It does not show which
   label you gave.
5. Click **Download labels**. You get
   `judge-calibration-labels-<name>.json`. Send me that file.

**Labelling rules:**

- **Judge only the text on the page.** The judge sees exactly this text and
  nothing else: the statement and the source it cites, or the request and the
  response. There is no chart behind it. If the source text does not show
  something, it is not there.
- **Apply the rubric as written.** The rubric panel shows the judge's own
  prompt, word for word, with the line breaks reflowed to fit. Label what a
  careful reviewer holding that rubric would say. Do not guess what the model
  would say.
- **Hard items are there on purpose.** Some items are near the rubric's
  boundary: a range read as a value, a correct unit conversion the page does
  not print, a refusal that answers anyway, a correct answer given where no
  refusal was needed. When the rubric's wording and what you think is right
  disagree, label what you think is right and say so in the note. Those
  disagreements are what phase 2 needs to see.
- **Take breaks.** The page saves as you go. Closing the tab and reopening the
  file brings your labels back in the same browser. To move to another
  browser or machine, download, then use **Resume from a labels file** there.
  If the page shows a notice that storage is blocked, download before you
  close it.

60 items take roughly 45–75 minutes.

## How to run compute

After you have labelled, from `agent/`:

```bash
cd agent && python scripts/judge_calibration.py compute ~/Downloads/judge-calibration-labels-<name>.json
# also write the report as JSON:
cd agent && python scripts/judge_calibration.py compute <labels.json> --json /tmp/calibration-report.json
```

No credential, no network, no model call. It prints, for each rubric and for
both pooled:

- **agreement**: human and judge agree, out of the items you labelled PASS or
  FAIL, with a Wilson 95% interval;
- **Cohen's kappa**: the same agreement, corrected for chance;
- **the confusion matrix**: your labels are the rows and the judge's are the
  columns;
- **the UNSURE and unlabelled items**, as counts and ids.

Then it prints **every disagreement**, with your label and note, the judge's
verdict, its three votes and their reasons, the item's intended label and
hard-case mode, and the item text.

`compute` exits 2 and prints why when the inputs do not belong together:

- a labels file made from a different item set;
- verdicts from a different item set;
- labels and verdicts made under different judge prompts. After a prompt
  edit, comparing the new verdicts with the same labels is the normal next
  step. `--accept-rubric-change` allows that comparison, with a warning,
  because you labelled against a rubric the judge no longer uses.

A labels file downloaded from the committed page always matches.

It warns when the committed judge prompt has changed since the verdicts were
made. The numbers then describe the prompt that was measured.

## The other commands

```bash
cd agent && python scripts/judge_calibration.py build            # rebuild label.html from sealed/items.json
cd agent && python scripts/judge_calibration.py build --check    # exit 1 if label.html is stale
cd agent && python scripts/judge_calibration.py judge            # re-run the judge on every item (billed)
cd agent && python scripts/judge_calibration.py synthetic OUT.json [--seed N]   # random labels, for testing compute
cd agent && python -m pytest tests/test_judge_calibration.py     # offline: blind, offline page, statistics
```

`judge` needs `ANTHROPIC_API_KEY`. It runs the gate's own `Judge` class, not a
copy: the pinned `claude-haiku-4-5-20251001`, the committed prompts, the
schema-constrained output, best-of-3 and a 2-of-3 majority. It uses a fresh
cache directory, so every vote is a live call.

The committed run made **180 calls, 0 cache hits, 121,728 input and 18,739
output tokens: $0.2154** at $1 / $5 per MTok. The run and its totals are in
`sealed/judge_verdicts.json` under `run`.

Its progress output names each item as it is graded. It never prints a
verdict.

## Limits

- **n = 30 per rubric.** That is half the ~60 below which
  `docs/w2/EVAL_DESIGN.md` says an agreement estimate cannot support a
  conclusion, and far from the 100–200 per rubric it asks for before the judge
  gates alone. The Wilson interval is printed so that nobody reads more into
  the number than it supports. Phase 1 is a first measurement. It does not
  certify the judge.
- **One labeller.** The measurement is agreement with you, not with clinicians
  in general.
- **The items were written by the session that built this tool**, with the
  judge prompt in view. Items written against a rubric's wording can be easier
  or harder for a judge than real system output.
- **Nothing here changes the gate.** `judge_config.json` still says
  `UNCALIBRATED`. Changing that, or any judge prompt, is a gate change and
  needs a `W2_GATE_SESSION=1` session.
