/**
 * Clinical Co-Pilot: the coordinate transform behind the PDF bounding-box
 * overlay. Pure geometry — no DOM, no PDF.js, no fetch — so it is unit
 * testable on its own (tests/js/bbox-overlay.test.mjs, `node --test`).
 *
 * THE TWO COORDINATE SPACES
 *
 * `BBox` (agent/app/models/w2/citations.py) is recorded in **page space**:
 * PDF points, origin at the top-left of the page, y increasing downward.
 * That is PyMuPDF page space, and it is the convention the doc-factory
 * records its ground truth in (`agent/evals/w2/factory/groundtruth.py`
 * COORDINATE_CONVENTION), so ground truth and extractor output are directly
 * comparable without a conversion on either side.
 *
 * PDF itself uses **user space**: origin at the bottom-left of the CropBox,
 * y increasing upward. PDF.js works in user space throughout: `page.view` is
 * the CropBox as `[x0, y0, x1, y1]` in user space, and
 * `viewport.transform` maps user space to rendered CSS pixels, already
 * carrying the page's `/Rotate`.
 *
 * So the overlay is two steps, and this module is both of them:
 *
 *     page space  --pageSpaceToUserSpace-->  user space  --applyTransform-->  CSS px
 *
 * Neither step is folded into the other, because the first is ours (it
 * encodes the convention the contract chose) and the second is PDF.js's (it
 * encodes how the page was actually drawn). `viewportTransform()` reproduces
 * the second for tests and for the alignment check; at runtime the viewer
 * passes PDF.js's own `viewport.transform` and cross-checks the two agree.
 *
 * A RECTANGLE IS FOUR CORNERS, NOT TWO
 *
 * Under a rotated page the two-corner shortcut gives a rectangle with
 * negative width. Every function here maps all four corners and takes the
 * axis-aligned hull, which is right for 0/90/180/270 and is also the honest
 * answer for a skewed source: the hull of the ink, not a claim about ink the
 * transform cannot make.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

/** Rotations PDF.js accepts, and therefore the only ones this module maps. */
export const SUPPORTED_ROTATIONS = [0, 90, 180, 270];

/**
 * One point of page space -> one point of PDF user space.
 *
 * x is measured from the same edge in both, so it only needs the CropBox's
 * left edge added. y is measured downward from the top in page space and
 * upward from the bottom in user space, so it is subtracted from the
 * CropBox's top edge.
 *
 * @param {number[]} view PDF.js `page.view`: [x0, y0, x1, y1] in user space.
 * @param {number} x Page-space x, PDF points from the page's left edge.
 * @param {number} y Page-space y, PDF points from the page's top edge.
 * @returns {[number, number]} The same point in user space.
 */
export function pageSpaceToUserSpace(view, x, y) {
    return [view[0] + x, view[3] - y];
}

/**
 * The inverse of {@link pageSpaceToUserSpace}. Present so that a round trip
 * is a property a test can assert rather than an arithmetic identity a
 * reader has to check by hand.
 *
 * @param {number[]} view PDF.js `page.view`.
 * @param {number} x User-space x.
 * @param {number} y User-space y.
 * @returns {[number, number]} The same point in page space.
 */
export function userSpaceToPageSpace(view, x, y) {
    return [x - view[0], view[3] - y];
}

/**
 * Apply a PDF-style affine matrix [a, b, c, d, e, f]:
 * x' = a*x + c*y + e, y' = b*x + d*y + f.
 *
 * Note the column-major order, which is PDF's and PDF.js's, not the
 * [a, b, c, d, e, f] of CSS `matrix()` — they happen to coincide, but the
 * doc-factory's `pdf_points_to_image_px` is written out in the same order
 * for the same reason, so keeping one convention everywhere is worth the
 * comment.
 *
 * @param {number[]} m Six-element affine matrix.
 * @param {number} x
 * @param {number} y
 * @returns {[number, number]}
 */
export function applyTransform(m, x, y) {
    return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
}

/**
 * Reproduces PDF.js's `PageViewport.transform` for a page rendered at
 * `scale` with no offset and no `dontFlip`.
 *
 * This exists so the transform can be exercised without loading a PDF, and
 * so the alignment check can compute what the overlay *should* be without
 * asking the code under test. The viewer does not use it to draw: it passes
 * PDF.js's own `viewport.transform` and calls {@link transformsAgree} to
 * confirm the two have not drifted apart across a PDF.js upgrade.
 *
 * @param {object} page
 * @param {number[]} page.view PDF.js `page.view`.
 * @param {number} page.scale CSS pixels per PDF point.
 * @param {number} [page.rotation] Page `/Rotate`, a multiple of 90.
 * @returns {number[]} Six-element affine matrix, user space -> CSS px.
 */
export function viewportTransform({view, scale, rotation = 0}) {
    let turn = rotation % 360;
    if (turn < 0) {
        turn += 360;
    }
    if (!SUPPORTED_ROTATIONS.includes(turn)) {
        throw new RangeError(`page rotation must be a multiple of 90, got ${rotation}`);
    }
    const centerX = (view[2] + view[0]) / 2;
    const centerY = (view[3] + view[1]) / 2;
    // The flip to y-down lives in rotateD = -1 at rotation 0; the other three
    // cases are that flip composed with the quarter turn.
    const [rotateA, rotateB, rotateC, rotateD] = {
        0: [1, 0, 0, -1],
        90: [0, 1, 1, 0],
        180: [-1, 0, 0, 1],
        270: [0, -1, -1, 0],
    }[turn];
    const offsetCanvasX = Math.abs(rotateA === 0 ? centerY - view[1] : centerX - view[0]) * scale;
    const offsetCanvasY = Math.abs(rotateA === 0 ? centerX - view[0] : centerY - view[1]) * scale;
    return [
        rotateA * scale,
        rotateB * scale,
        rotateC * scale,
        rotateD * scale,
        offsetCanvasX - rotateA * scale * centerX - rotateC * scale * centerY,
        offsetCanvasY - rotateB * scale * centerX - rotateD * scale * centerY,
    ];
}

/**
 * The rendered size of a page, in CSS pixels, for the same inputs as
 * {@link viewportTransform}. A quarter turn swaps the two.
 *
 * @param {object} page
 * @param {number[]} page.view
 * @param {number} page.scale
 * @param {number} [page.rotation]
 * @returns {{width: number, height: number}}
 */
export function viewportSize({view, scale, rotation = 0}) {
    let turn = rotation % 360;
    if (turn < 0) {
        turn += 360;
    }
    const across = (view[2] - view[0]) * scale;
    const down = (view[3] - view[1]) * scale;
    return turn === 90 || turn === 270 ? {width: down, height: across} : {width: across, height: down};
}

/**
 * Do two affine matrices describe the same mapping, to within `tolerance`?
 *
 * The viewer calls this on PDF.js's `viewport.transform` against
 * {@link viewportTransform}'s idea of it. A mismatch means PDF.js changed
 * how it builds a viewport, which would move every overlay on the page —
 * exactly the failure that is invisible until someone compares a box to the
 * ink under it.
 *
 * @param {number[]} a
 * @param {number[]} b
 * @param {number} [tolerance] In the matrices' own units.
 * @returns {boolean}
 */
export function transformsAgree(a, b, tolerance = 1e-6) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== 6 || b.length !== 6) {
        return false;
    }
    return a.every((value, index) => Math.abs(value - b[index]) <= tolerance);
}

/**
 * One `BBox` -> one CSS-pixel rectangle on the rendered page.
 *
 * The returned rectangle is positioned relative to the page's top-left
 * corner, which is what an absolutely-positioned overlay layer sitting on
 * top of the canvas needs.
 *
 * @param {object} bbox A citation bbox: {x0, y0, x1, y1} in page space.
 * @param {object} rendered
 * @param {number[]} rendered.view PDF.js `page.view`.
 * @param {number[]} rendered.transform PDF.js `viewport.transform`.
 * @returns {{left: number, top: number, width: number, height: number}}
 */
export function overlayRect(bbox, {view, transform}) {
    const corners = [
        [bbox.x0, bbox.y0],
        [bbox.x1, bbox.y0],
        [bbox.x1, bbox.y1],
        [bbox.x0, bbox.y1],
    ];
    const xs = [];
    const ys = [];
    for (const [x, y] of corners) {
        const [ux, uy] = pageSpaceToUserSpace(view, x, y);
        const [vx, vy] = applyTransform(transform, ux, uy);
        xs.push(vx);
        ys.push(vy);
    }
    const left = Math.min(...xs);
    const top = Math.min(...ys);
    return {left, top, width: Math.max(...xs) - left, height: Math.max(...ys) - top};
}

/**
 * The smallest CSS rectangle that shows a box a reader can see.
 *
 * A zero-width box is real data, not a defect: `BBox` allows `x1 === x0`
 * because the doc-factory emits exactly that for the empty flag column of an
 * unflagged lab row, and rejecting it would make the contract unable to
 * describe a normal result (citations.py, BBox). Drawn at its true size it
 * is invisible, and an invisible highlight reads as "there is nothing to
 * see here" — which for a flag column is the opposite of what D20 says a
 * blank means. So a degenerate box is padded outward to a minimum, centred
 * on where it really is, and the viewer marks it as a point rather than a
 * region.
 *
 * @param {{left: number, top: number, width: number, height: number}} rect
 * @param {number} [minimum] CSS px.
 * @returns {{left: number, top: number, width: number, height: number, degenerate: boolean}}
 */
export function visibleRect(rect, minimum = 6) {
    const degenerate = rect.width < minimum || rect.height < minimum;
    const width = Math.max(rect.width, minimum);
    const height = Math.max(rect.height, minimum);
    return {
        left: rect.left - (width - rect.width) / 2,
        top: rect.top - (height - rect.height) / 2,
        width,
        height,
        degenerate,
    };
}

/**
 * The union of several rectangles — what a chip has to scroll into view when
 * its field wrapped across lines and therefore has one located citation per
 * line box.
 *
 * @param {Array<{left: number, top: number, width: number, height: number}>} rects
 * @returns {{left: number, top: number, width: number, height: number}|null}
 */
export function unionRect(rects) {
    if (!rects.length) {
        return null;
    }
    const left = Math.min(...rects.map((r) => r.left));
    const top = Math.min(...rects.map((r) => r.top));
    const right = Math.max(...rects.map((r) => r.left + r.width));
    const bottom = Math.max(...rects.map((r) => r.top + r.height));
    return {left, top, width: right - left, height: bottom - top};
}
