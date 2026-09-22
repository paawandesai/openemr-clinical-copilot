/**
 * Clinical Co-Pilot: the source-grounded answer card.
 *
 * Wires the three pieces together — the answer view, the cited-page viewer
 * and the upload control — and is the only file that knows about endpoints.
 * Everything below it takes an adapter, which is what lets the same code
 * run inside the chart and inside the fixture page (`dev/fixture.html`)
 * with no branch on "are we in OpenEMR".
 *
 * Mounting it is one call. The chart card passes the module's own routes;
 * the fixture page passes local files.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import {renderAnswerView} from './answer-view.mjs';
import {createDocumentViewer, moduleDocumentSource} from './document-viewer.mjs';
import {createUploadControl, moduleUploadSource} from './upload.mjs';

/**
 * Mount the card.
 *
 * @param {HTMLElement} root The card's root element. Must contain
 *     `.ccw2-upload-slot`, `.ccw2-answer-slot` and `.ccw2-viewer-slot`.
 * @param {object} options
 * @param {object} options.pdfjs The imported PDF.js namespace.
 * @param {string} options.workerSrc URL of the vendored worker.
 * @param {object} options.documentSource A `DocumentSource`.
 * @param {object} [options.uploadSource] An upload adapter; omit to hide the control.
 * @param {object} [options.strings] Translated labels.
 * @param {number} [options.scale]
 * @returns {{show: (view: object) => void, viewer: object, upload: object|null}}
 */
export function mountSourceCard(
    root,
    {pdfjs, workerSrc, documentSource, uploadSource = null, strings = {}, scale = 1.25}
) {
    root.classList.add('ccw2-root');
    const answerSlot = root.querySelector('.ccw2-answer-slot');
    const viewerSlot = root.querySelector('.ccw2-viewer-slot');
    const uploadSlot = root.querySelector('.ccw2-upload-slot');

    const viewer = createDocumentViewer(viewerSlot, {
        pdfjs,
        workerSrc,
        source: documentSource,
        strings,
        scale,
    });

    const upload =
        uploadSource && uploadSlot
            ? createUploadControl(uploadSlot, {
                  source: uploadSource,
                  strings,
                  // D7: extraction finished at upload time, so the answer the
                  // card shows next is asked for afterwards, by the caller —
                  // this control does not itself ask a question.
                  onComplete: () => {},
              })
            : null;

    let current = null;

    function show(view) {
        current = view;
        renderAnswerView(answerSlot, view, {
            strings,
            onCite: (group) => {
                // A rejected fetch has already put its reason on the viewer;
                // nothing is logged, because the only things worth logging
                // here are the document id and the field name.
                viewer.show(group).catch(() => {});
            },
        });
    }

    return {show, viewer, upload, get view() { return current; }};
}

/**
 * Mount from `data-` attributes, for the chart card.
 *
 * Reads:
 *   data-document-endpoint   GET ...?document_id=<int> -> application/pdf
 *   data-upload-endpoint     POST multipart
 *   data-status-endpoint     GET ...?document_id=...
 *   data-pid                 the open patient
 *   data-csrf-token          OpenEMR's per-session token
 *   data-worker-src          the vendored PDF.js worker
 *
 * @param {HTMLElement} root
 * @param {object} pdfjs
 * @param {object} [strings]
 */
export function mountFromDataset(root, pdfjs, strings = {}) {
    const data = root.dataset;
    const fields = () => ({pid: data.pid, csrf_token_form: data.csrfToken});
    return mountSourceCard(root, {
        pdfjs,
        workerSrc: data.workerSrc,
        documentSource: moduleDocumentSource(data.documentEndpoint),
        uploadSource: data.uploadEndpoint
            ? moduleUploadSource({
                  endpoint: data.uploadEndpoint,
                  statusEndpoint: data.statusEndpoint,
                  fields,
              })
            : null,
        strings,
    });
}
