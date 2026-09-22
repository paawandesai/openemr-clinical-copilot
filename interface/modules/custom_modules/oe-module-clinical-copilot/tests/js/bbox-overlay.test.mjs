/**
 * Unit tests for the overlay coordinate transform.
 *
 * Run from the OpenEMR root, no dependencies and no node_modules:
 *
 *   node --test interface/modules/custom_modules/oe-module-clinical-copilot/tests/js/
 *
 * The last block reads the committed doc-factory ground truth and checks the
 * transform against real recorded boxes, so the arithmetic is tied to the
 * data the extractor will actually produce and not only to numbers chosen
 * here. Those cases skip themselves if the samples are absent.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import assert from 'node:assert/strict';
import {existsSync, readFileSync, readdirSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {describe, it} from 'node:test';

import {
    applyTransform,
    overlayRect,
    pageSpaceToUserSpace,
    transformsAgree,
    unionRect,
    userSpaceToPageSpace,
    viewportSize,
    viewportTransform,
    visibleRect,
} from '../../public/assets/w2/bbox-overlay.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SAMPLES = join(HERE, '../../../../../../agent/evals/w2/factory/output/samples');

/** US Letter with the CropBox at the origin — every committed sample. */
const LETTER = [0, 0, 612, 792];
/** A CropBox that does not start at the origin, which most real scans do not. */
const OFFSET = [12, 20, 624, 812];

const close = (actual, expected, tolerance = 1e-9) =>
    assert.ok(
        Math.abs(actual - expected) <= tolerance,
        `${actual} is not within ${tolerance} of ${expected}`
    );

const closeRect = (actual, expected, tolerance = 1e-9) => {
    for (const key of ['left', 'top', 'width', 'height']) {
        close(actual[key], expected[key], tolerance);
    }
};

/** The whole page as a bbox, which must render as the whole viewport. */
const wholePage = (view) => ({x0: 0, y0: 0, x1: view[2] - view[0], y1: view[3] - view[1]});

describe('pageSpaceToUserSpace', () => {
    it('puts the page-space origin at the top-left of the CropBox', () => {
        assert.deepEqual(pageSpaceToUserSpace(LETTER, 0, 0), [0, 792]);
        assert.deepEqual(pageSpaceToUserSpace(OFFSET, 0, 0), [12, 812]);
    });

    it('measures y downward from the top, not upward from the bottom', () => {
        // 100pt below the top edge is 692pt above the bottom on a 792pt page.
        assert.deepEqual(pageSpaceToUserSpace(LETTER, 0, 100), [0, 692]);
    });

    it('round-trips through userSpaceToPageSpace', () => {
        for (const view of [LETTER, OFFSET]) {
            for (const [x, y] of [[0, 0], [54, 58.34], [326.115, 79.115], [612, 792]]) {
                const [ux, uy] = pageSpaceToUserSpace(view, x, y);
                const [px, py] = userSpaceToPageSpace(view, ux, uy);
                close(px, x);
                close(py, y);
            }
        }
    });
});

describe('applyTransform', () => {
    it('reads the matrix in PDF column-major order', () => {
        // The doc-factory writes its scan mapping in the same order:
        // image_x = a*x + c*y + e ; image_y = b*x + d*y + f.
        assert.deepEqual(applyTransform([2, 3, 5, 7, 11, 13], 1, 1), [2 + 5 + 11, 3 + 7 + 13]);
    });

    it('is the identity for [1, 0, 0, 1, 0, 0]', () => {
        assert.deepEqual(applyTransform([1, 0, 0, 1, 0, 0], 42, -7), [42, -7]);
    });
});

describe('viewportTransform', () => {
    it('is the hand-derived matrix at rotation 0', () => {
        // Composing it with pageSpaceToUserSpace collapses to a pure scale,
        // which is the property the whole overlay rests on.
        for (const view of [LETTER, OFFSET]) {
            for (const scale of [1, 1.5, 3]) {
                const expected = [scale, 0, 0, -scale, -view[0] * scale, view[3] * scale];
                // Element-wise, not deepEqual: -0 and 0 are the same mapping
                // and deepStrictEqual says they are not.
                viewportTransform({view, scale}).forEach((value, index) => close(value, expected[index]));
            }
        }
    });

    it('maps the four page corners onto the four viewport corners, at every rotation', () => {
        for (const view of [LETTER, OFFSET]) {
            for (const rotation of [0, 90, 180, 270]) {
                const scale = 1.25;
                const transform = viewportTransform({view, scale, rotation});
                const size = viewportSize({view, scale, rotation});
                const rect = overlayRect(wholePage(view), {view, transform});
                closeRect(rect, {left: 0, top: 0, width: size.width, height: size.height}, 1e-9);
            }
        }
    });

    it('swaps width and height on a quarter turn, and not on a half turn', () => {
        const flat = viewportSize({view: LETTER, scale: 1});
        assert.deepEqual(flat, {width: 612, height: 792});
        assert.deepEqual(viewportSize({view: LETTER, scale: 1, rotation: 180}), flat);
        assert.deepEqual(viewportSize({view: LETTER, scale: 1, rotation: 90}), {width: 792, height: 612});
        assert.deepEqual(viewportSize({view: LETTER, scale: 1, rotation: 270}), {width: 792, height: 612});
    });

    it('normalises a negative or over-full rotation the way PDF.js does', () => {
        assert.deepEqual(
            viewportTransform({view: LETTER, scale: 2, rotation: -90}),
            viewportTransform({view: LETTER, scale: 2, rotation: 270})
        );
        assert.deepEqual(
            viewportTransform({view: LETTER, scale: 2, rotation: 450}),
            viewportTransform({view: LETTER, scale: 2, rotation: 90})
        );
    });

    it('refuses a rotation PDF.js would refuse', () => {
        assert.throws(() => viewportTransform({view: LETTER, scale: 1, rotation: 45}), RangeError);
    });
});

describe('transformsAgree', () => {
    const base = viewportTransform({view: LETTER, scale: 2});

    it('accepts a matrix that differs only by floating-point noise', () => {
        assert.ok(transformsAgree(base, base.map((v) => v + 1e-12)));
    });

    it('rejects a real difference, a wrong length, and a non-array', () => {
        assert.ok(!transformsAgree(base, base.map((v, i) => (i === 3 ? v + 0.5 : v))));
        assert.ok(!transformsAgree(base, base.slice(0, 5)));
        assert.ok(!transformsAgree(base, null));
    });
});

describe('overlayRect', () => {
    it('is the box scaled, when the page is upright and the CropBox at the origin', () => {
        const scale = 2;
        const transform = viewportTransform({view: LETTER, scale});
        const rect = overlayRect({x0: 54, y0: 58.34, x1: 326.115, y1: 79.115}, {view: LETTER, transform});
        closeRect(rect, {
            left: 54 * scale,
            top: 58.34 * scale,
            width: (326.115 - 54) * scale,
            height: (79.115 - 58.34) * scale,
        }, 1e-9);
    });

    it('subtracts the CropBox origin rather than ignoring it', () => {
        const transform = viewportTransform({view: OFFSET, scale: 1});
        const rect = overlayRect({x0: 10, y0: 20, x1: 30, y1: 40}, {view: OFFSET, transform});
        closeRect(rect, {left: 10, top: 20, width: 20, height: 20});
    });

    it('never returns a negative width or height, whatever the rotation', () => {
        const box = {x0: 100, y0: 150, x1: 260, y1: 172};
        for (const rotation of [0, 90, 180, 270]) {
            const transform = viewportTransform({view: LETTER, scale: 1.5, rotation});
            const rect = overlayRect(box, {view: LETTER, transform});
            assert.ok(rect.width > 0, `width ${rect.width} at rotation ${rotation}`);
            assert.ok(rect.height > 0, `height ${rect.height} at rotation ${rotation}`);
        }
    });

    it('turns a landscape box into a portrait one on a quarter turn', () => {
        const box = {x0: 100, y0: 150, x1: 260, y1: 172};
        const upright = overlayRect(box, {view: LETTER, transform: viewportTransform({view: LETTER, scale: 1})});
        const turned = overlayRect(box, {
            view: LETTER,
            transform: viewportTransform({view: LETTER, scale: 1, rotation: 90}),
        });
        close(turned.width, upright.height);
        close(turned.height, upright.width);
    });

    it('scales linearly: doubling the scale doubles every edge', () => {
        const box = {x0: 54, y0: 58.34, x1: 326.115, y1: 79.115};
        const one = overlayRect(box, {view: LETTER, transform: viewportTransform({view: LETTER, scale: 1})});
        const two = overlayRect(box, {view: LETTER, transform: viewportTransform({view: LETTER, scale: 2})});
        closeRect(two, {left: one.left * 2, top: one.top * 2, width: one.width * 2, height: one.height * 2}, 1e-9);
    });

    it('keeps a zero-width box at zero width', () => {
        // citations.py BBox: the factory emits [306.0, 256.398, 306.0, 268.24]
        // for the empty flag column of an unflagged lab row. Widening it here
        // would report a region the page does not have.
        const transform = viewportTransform({view: LETTER, scale: 1});
        const rect = overlayRect({x0: 306, y0: 256.398, x1: 306, y1: 268.24}, {view: LETTER, transform});
        close(rect.width, 0);
        close(rect.height, 268.24 - 256.398);
    });
});

describe('visibleRect', () => {
    it('pads a zero-width box outward from where it really is', () => {
        const padded = visibleRect({left: 306, top: 256.398, width: 0, height: 11.842}, 6);
        assert.ok(padded.degenerate);
        close(padded.width, 6);
        close(padded.height, 11.842);
        // Centred: the true x is still the middle of the drawn box.
        close(padded.left + padded.width / 2, 306);
        close(padded.top, 256.398);
    });

    it('leaves a box that is already big enough exactly where it is', () => {
        const rect = {left: 54, top: 58, width: 272, height: 21};
        const shown = visibleRect(rect, 6);
        assert.ok(!shown.degenerate);
        closeRect(shown, rect);
    });
});

describe('unionRect', () => {
    it('covers every line box of a wrapped field', () => {
        const union = unionRect([
            {left: 130, top: 200, width: 180, height: 12},
            {left: 130, top: 214, width: 96, height: 12},
        ]);
        closeRect(union, {left: 130, top: 200, width: 180, height: 26});
    });

    it('is null for no boxes, so a caller cannot scroll to nowhere', () => {
        assert.equal(unionRect([]), null);
    });
});

describe('against the committed doc-factory ground truth', () => {
    const scenarios = existsSync(SAMPLES)
        ? readdirSync(SAMPLES, {withFileTypes: true}).filter((e) => e.isDirectory()).map((e) => e.name).sort()
        : [];

    it('finds the six committed samples', {skip: scenarios.length === 0 && 'samples not present'}, () => {
        assert.equal(scenarios.length, 6);
    });

    for (const scenario of scenarios) {
        it(`${scenario}: every recorded box lands inside the rendered page`, () => {
            const truth = JSON.parse(
                readFileSync(join(SAMPLES, scenario, `${scenario}.ground_truth.json`), 'utf8')
            );
            // Every sample page is US Letter, upright — asserted here rather
            // than assumed, because the rest of this case depends on it.
            const scale = 1.5;
            let checked = 0;
            for (const page of truth.pages) {
                assert.equal(page.width, 612);
                assert.equal(page.height, 792);
                const view = [0, 0, page.width, page.height];
                const transform = viewportTransform({view, scale});
                const size = viewportSize({view, scale});
                for (const field of truth.fields) {
                    if (field.page !== page.number || !field.bbox) {
                        continue;
                    }
                    const [x0, y0, x1, y1] = field.bbox;
                    const rect = overlayRect({x0, y0, x1, y1}, {view, transform});
                    assert.ok(rect.left >= -0.001, `${field.field_path} left ${rect.left}`);
                    assert.ok(rect.top >= -0.001, `${field.field_path} top ${rect.top}`);
                    assert.ok(
                        rect.left + rect.width <= size.width + 0.001,
                        `${field.field_path} right ${rect.left + rect.width} > ${size.width}`
                    );
                    assert.ok(
                        rect.top + rect.height <= size.height + 0.001,
                        `${field.field_path} bottom ${rect.top + rect.height} > ${size.height}`
                    );
                    checked += 1;
                }
            }
            assert.ok(checked > 0, 'no located boxes were checked');
        });
    }

    it('places a known lab header box where the ground truth says it is', {
        skip: !scenarios.includes('lab-clean-001') && 'lab-clean-001 not present',
    }, () => {
        const truth = JSON.parse(
            readFileSync(join(SAMPLES, 'lab-clean-001', 'lab-clean-001.ground_truth.json'), 'utf8')
        );
        const field = truth.fields.find((f) => f.field_path === 'patient.name');
        const view = [0, 0, 612, 792];
        const rect = overlayRect(
            {x0: field.bbox[0], y0: field.bbox[1], x1: field.bbox[2], y1: field.bbox[3]},
            {view, transform: viewportTransform({view, scale: 1})}
        );
        // Top-left origin: the patient block sits about a fifth of the way
        // down a 792pt page. Under a bottom-left reading it would be at ~642.
        close(rect.top, 137.54, 1e-6);
        assert.ok(rect.top < 792 / 2, 'the patient header is in the top half of the page');
    });
});
