/**
 * Scripted check: are the overlay boxes the fixture page draws actually on
 * the ink the doc-factory says they cover?
 *
 * Work order 11, acceptance: "fixture mode renders all 6 samples with
 * overlays aligned to ground-truth boxes (include a scripted check
 * comparing drawn boxes to ground truth)".
 *
 * It renders every sample in a real browser through the real viewer and
 * asks two different questions about every box:
 *
 * 1. **Is it where the ground truth says?** The expected rectangle is
 *    computed here, from the committed `*.ground_truth.json`, without
 *    touching `bbox-overlay.mjs`. Every sample page is US Letter, upright,
 *    with the CropBox at the origin — asserted, not assumed — so the
 *    expectation is `box * scale`, which no shared code can bias. For the
 *    two scanned samples the recorded box is mapped into scan space first
 *    (`ground-truth-scan.mjs`), by a second implementation of the factory's
 *    affine, so builder and checker have to agree independently.
 *
 * 2. **Is there ink under it, and does the ink stop where the box does?**
 *    Geometry can be self-consistently wrong — a y-flip, a CropBox ignored,
 *    a scale applied twice — and still pass (1) if ground truth itself is
 *    in a different space from the rendered file. That is a live risk for
 *    the two scanned samples, whose boxes are recorded on the clean layout
 *    and drawn on a skewed JPEG of it. So the check reads the rendered
 *    canvas: every box over printed text must contain dark pixels, and
 *    across the page the boxes must contain markedly more ink than the
 *    leading immediately above and below them. A box that had slipped off
 *    its line would sit in whitespace (failing the first) or straddle two
 *    lines with its bands as inked as itself (failing the second).
 *
 *    A y-mirror control was tried first and dropped: on a dense two-column
 *    intake form a mirrored box lands on other form text, so it cannot
 *    discriminate. Measured medians on the six samples were 24.2/5.0,
 *    24.9/18.6, 18.1/11.5, 25.0/0.0, 23.8/14.8 and 28.3/0.1 per cent — a
 *    control that answers "maybe" on half the corpus is not a control. The
 *    leading band is local and reads 24.2/0.0, 24.9/0.0, 18.1/0.0,
 *    25.0/5.4, 23.8/0.0 and 28.3/11.4, the last two being lab tables whose
 *    rows and rules sit close together.
 *
 * NO DEPENDENCIES. It drives an installed Chrome over the DevTools
 * protocol using Node's built-in WebSocket, so it runs without
 * node_modules and adds nothing to package.json.
 *
 *   node .../dev/check-overlay-alignment.mjs [--scale 1.5] [--chrome PATH]
 *
 * Exit status is 0 only if every box passed. It prints a per-scenario table
 * and the tolerances it used.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import {existsSync, readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

import {openFixturePage} from './browser.mjs';
import {groundTruthBoxes} from './ground-truth-scan.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, 'fixtures');

/** Where a box may sit relative to where ground truth puts it, in CSS px. */
const POSITION_TOLERANCE = 0.01;

/** Minimum dark-pixel fraction inside a box that covers printed text. */
const MIN_INK = 0.02;

/** A box only gets an ink check if it is this big and has text behind it. */
const MIN_INK_AREA = 20;

/**
 * How much more ink a box must hold than the leading around it.
 *
 * The tightest observed margin is `lab-scanned-heavy-abnormal-002` at
 * 28.3% against 11.4%, i.e. 2.5x — a lab table where the rows are close
 * together and the scan profile adds speckle everywhere. 1.8 leaves that
 * case room to get a little noisier without turning the check into a
 * coin toss. The other five are at or near infinity.
 */
const BAND_RATIO = 1.8;

/** Band height, as a fraction of the box's own height. */
const BAND_FRACTION = 0.3;

/** The minimum-size rule `visibleRect()` applies, restated here. */
const MIN_DRAWN = 6;

// --- the check ---------------------------------------------------------------

/**
 * Where a recorded box should be drawn, in CSS px from the page's top-left.
 *
 * Deliberately not `overlayRect()`: for an upright page whose CropBox
 * starts at the origin — which every committed sample is, and which the
 * caller asserts — the mapping is a scale, and writing it out here means
 * the expectation shares no code with the thing it is checking.
 */
function expectedRect(box, scale) {
    const rect = {
        left: box[0] * scale,
        top: box[1] * scale,
        width: (box[2] - box[0]) * scale,
        height: (box[3] - box[1]) * scale,
    };
    if (rect.width >= MIN_DRAWN && rect.height >= MIN_DRAWN) {
        return {...rect, point: false};
    }
    const width = Math.max(rect.width, MIN_DRAWN);
    const height = Math.max(rect.height, MIN_DRAWN);
    return {
        left: rect.left - (width - rect.width) / 2,
        top: rect.top - (height - rect.height) / 2,
        width,
        height,
        point: true,
    };
}

function median(values) {
    if (!values.length) {
        return 0;
    }
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

async function checkScenario(evaluate, scenario, scale) {
    const truth = JSON.parse(readFileSync(join(FIXTURES, scenario, 'ground-truth.json'), 'utf8'));
    await evaluate(`window.ccw2Fixture.load(${JSON.stringify(scenario)})`);

    const view = await evaluate('window.ccw2Fixture.view.documents[0]');
    const pages = view.pages;
    // The expectation below is only a scale because of these three facts.
    // If a future sample is rotated or cropped, this check must grow a real
    // transform rather than quietly measure the wrong thing.
    for (const page of pages) {
        if (page.width_pt !== 612 || page.height_pt !== 792 || page.rotation !== 0) {
            throw new Error(
                `${scenario} page ${page.page} is ${page.width_pt}x${page.height_pt} rot ${page.rotation}; ` +
                    'this check assumes upright US Letter with the CropBox at the origin'
            );
        }
    }
    const pageSizes = new Map(pages.map((page) => [page.page, {width: page.width_pt}]));
    const recorded = groundTruthBoxes(truth, pageSizes);

    const failures = [];
    const trueInk = [];
    const bandInk = [];
    let boxes = 0;
    let inked = 0;

    for (const page of pages) {
        const drawn = await evaluate(`window.ccw2Fixture.drawPage(${page.page}, ${scale})`
        );
        if (!drawn.boxes.length) {
            continue;
        }
        if (drawn.boxes.length !== drawn.citations.length) {
            failures.push(
                `page ${page.page}: drew ${drawn.boxes.length} boxes for ${drawn.citations.length} citations`
            );
        }

        for (const [index, citation] of drawn.citations.entries()) {
            const box = drawn.boxes[index];
            boxes += 1;
            const key = `${citation.bbox.page}|${citation.field_or_chunk_id}`;
            const truthBox = recorded.get(key);
            if (!truthBox) {
                failures.push(`${key}: drawn, but the ground truth has no box for it`);
                continue;
            }
            const expected = expectedRect(truthBox.box, scale);
            for (const edge of ['left', 'top', 'width', 'height']) {
                const delta = Math.abs(box[edge] - expected[edge]);
                if (delta > POSITION_TOLERANCE) {
                    failures.push(
                        `${key}: ${edge} drawn ${box[edge].toFixed(4)}, expected ` +
                            `${expected[edge].toFixed(4)} (off by ${delta.toFixed(4)}px)`
                    );
                }
            }
            if (box.point !== expected.point) {
                failures.push(`${key}: drawn as ${box.point ? 'point' : 'region'}, expected the other`);
            }

            const area = expected.width * expected.height;
            const hasText = truthBox.status === 'present' && truthBox.text.trim() !== '';
            if (!hasText || area < MIN_INK_AREA) {
                continue;
            }
            const ink = await evaluate(`window.ccw2Fixture.ink(${JSON.stringify({
                    left: expected.left,
                    top: expected.top,
                    width: expected.width,
                    height: expected.height,
                })})`
            );
            if (ink === null) {
                continue;
            }
            inked += 1;
            trueInk.push(ink.ratio);
            if (ink.ratio < MIN_INK) {
                failures.push(
                    `${key}: only ${(ink.ratio * 100).toFixed(2)}% of the box is ink ` +
                        `(paper ${ink.paper.toFixed(0)}); the box may be over blank page`
                );
            }
            // The leading directly above and below the same box. Text has
            // space around it; a box that has slid off its line does not.
            const bandHeight = Math.max(2, expected.height * BAND_FRACTION);
            const above = await evaluate(`window.ccw2Fixture.ink(${JSON.stringify({
                left: expected.left, top: expected.top - bandHeight, width: expected.width, height: bandHeight,
            })})`);
            const below = await evaluate(`window.ccw2Fixture.ink(${JSON.stringify({
                left: expected.left, top: expected.top + expected.height, width: expected.width, height: bandHeight,
            })})`);
            const bands = [above, below].filter((b) => b !== null).map((b) => b.ratio);
            if (bands.length) {
                bandInk.push(Math.max(...bands));
            }
        }
    }

    const trueMedian = median(trueInk);
    const bandMedian = median(bandInk);
    if (inked > 0 && trueMedian < BAND_RATIO * bandMedian) {
        failures.push(
            `ink under the boxes (median ${(trueMedian * 100).toFixed(2)}%) is not ` +
                `${BAND_RATIO}x the ink in the leading around them ` +
                `(median ${(bandMedian * 100).toFixed(2)}%): the boxes may not be on their lines`
        );
    }

    return {scenario, boxes, inked, trueMedian, bandMedian, failures};
}

// --- main --------------------------------------------------------------------

async function main() {
    const args = process.argv.slice(2);
    const scale = Number(args[args.indexOf('--scale') + 1]) || 1.5;
    const chromeArg = args.indexOf('--chrome') === -1 ? null : args[args.indexOf('--chrome') + 1];

    if (!existsSync(join(FIXTURES, 'manifest.json'))) {
        process.stderr.write(
            'No fixtures. Build them first:\n  cd agent && python scripts/build_ui_fixtures.py\n'
        );
        return 1;
    }

    const manifest = JSON.parse(readFileSync(join(FIXTURES, 'manifest.json'), 'utf8'));
    let page;
    try {
        page = await openFixturePage(chromeArg);
    } catch (error) {
        process.stderr.write(`${error.message}\n`);
        return 1;
    }

    let failed = 0;
    try {
        process.stdout.write(
            `overlay alignment check \u2014 scale ${scale}, position tolerance ${POSITION_TOLERANCE}px, ` +
                `min ink ${(MIN_INK * 100).toFixed(0)}%, box:leading ratio ${BAND_RATIO}x\n\n`
        );
        for (const entry of manifest.scenarios) {
            const result = await checkScenario(page.evaluate, entry.scenario_id, scale);
            const verdict = result.failures.length ? 'FAIL' : 'ok';
            process.stdout.write(
                `${verdict.padEnd(5)} ${result.scenario.padEnd(32)} ` +
                    `${String(result.boxes).padStart(4)} boxes, ${String(result.inked).padStart(4)} ink-checked, ` +
                    `ink ${(result.trueMedian * 100).toFixed(1)}% vs leading ${(result.bandMedian * 100).toFixed(1)}%\n`
            );
            for (const failure of result.failures.slice(0, 12)) {
                process.stdout.write(`        ${failure}\n`);
            }
            if (result.failures.length > 12) {
                process.stdout.write(`        ... and ${result.failures.length - 12} more\n`);
            }
            failed += result.failures.length;
        }
        process.stdout.write(
            failed === 0
                ? `\nAll ${manifest.scenarios.length} scenarios aligned.\n`
                : `\n${failed} problem(s).\n`
        );
    } finally {
        await page.close();
    }
    return failed === 0 ? 0 : 1;
}

process.exitCode = await main();
