/**
 * The doc-factory's clean-layout -> scanned-page mapping, reimplemented for
 * the alignment check.
 *
 * This is the same arithmetic as `scan_space_box()` in
 * `agent/scripts/build_ui_fixtures.py`, written separately and on purpose.
 * The builder uses its copy to put a `bbox` into a fixture; the check uses
 * this one to work out, from the committed ground truth, where that box
 * ought to be. If the two ever disagree the check fails, which is the point
 * — a checker that imported the builder's mapping could only ever confirm
 * that the builder agrees with itself.
 *
 * The factory documents the mapping in each scanned sample's
 * `transform.mapping`:
 *
 *     image_x = a*x + c*y + e ; image_y = b*x + d*y + f
 *     with pdf_points_to_image_px = [a, c, e, b, d, f]
 *     and (x, y) a point on the clean layout in PyMuPDF page space.
 *
 * That lands in image pixels. The scanned PDF draws the image across the
 * full page, so pixels become points by `page_width_pt / image_width_px`.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

/**
 * One clean-layout box -> the same ink on the rendered page, in PDF points.
 *
 * Four corners and the hull: the mapping carries skew, so a rectangle does
 * not stay a rectangle, and an axis-aligned `BBox` can only honestly be the
 * hull of where the ink went.
 *
 * @param {number[]} box `[x0, y0, x1, y1]` on the clean layout.
 * @param {number[]} mapping `pdf_points_to_image_px`.
 * @param {number} pageWidthPt Width of the rendered page, in points.
 * @param {number} imageWidthPx Width of the scan, in pixels.
 * @returns {number[]} `[x0, y0, x1, y1]` on the rendered page.
 */
export function scanSpaceBox(box, mapping, pageWidthPt, imageWidthPx) {
    const [a, c, e, b, d, f] = mapping;
    const perPixel = pageWidthPt / imageWidthPx;
    const corners = [
        [box[0], box[1]],
        [box[2], box[1]],
        [box[2], box[3]],
        [box[0], box[3]],
    ];
    const xs = corners.map(([x, y]) => (a * x + c * y + e) * perPixel);
    const ys = corners.map(([x, y]) => (b * x + d * y + f) * perPixel);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/**
 * Every ground-truth field's box, in the space of the document that will be
 * drawn, keyed by `page|field_path`.
 *
 * A no-op for the four clean scenarios: their `transform` is null and the
 * rendered file is the clean layout, so the recorded box is already right.
 *
 * @param {object} truth A parsed `<scenario>.ground_truth.json`.
 * @param {Map<number, {width: number}>} pageSizes Rendered page widths, in points.
 * @returns {Map<string, {page: number, field: string, box: number[], status: string, text: string}>}
 */
export function groundTruthBoxes(truth, pageSizes) {
    const transform = truth.transform;
    const byPage = transform ? new Map(transform.pages.map((page) => [page.page, page])) : null;
    const boxes = new Map();
    for (const field of truth.fields) {
        if (!field.bbox || !field.page) {
            continue;
        }
        let box = field.bbox;
        if (byPage) {
            const page = byPage.get(field.page);
            const width = pageSizes.get(field.page).width;
            box = scanSpaceBox(box, page.pdf_points_to_image_px, width, page.image_size_px[0]);
        }
        boxes.set(`${field.page}|${field.field_path}`, {
            page: field.page,
            field: field.field_path,
            box,
            status: field.status,
            text: field.rendered_text || '',
        });
    }
    return boxes;
}
