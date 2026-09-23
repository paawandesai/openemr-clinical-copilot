/**
 * Clinical Co-Pilot: the source-grounded answer card.
 *
 * Wires the four pieces together — the answer view, the cited-page viewer,
 * the upload control and the question box — and is the only file that knows
 * about endpoints.
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
import {createAskControl, moduleAskSource, patientBar, statusLabel} from './ask.mjs';
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
 * @param {object} [options.askSource] A question adapter; omit to hide the question box.
 * @param {number} [options.questionMaxLength]
 * @param {number} [options.maxDocuments] How many documents one question may name.
 * @param {object} [options.strings] Translated labels.
 * @param {number} [options.scale]
 * @returns {{show: (view: object) => void, showAnswer: (payload: object) => void, viewer: object, upload: object|null, ask: object|null}}
 */
export function mountSourceCard(
    root,
    {
        pdfjs,
        workerSrc,
        documentSource,
        uploadSource = null,
        askSource = null,
        questionMaxLength = 500,
        maxDocuments = 4,
        strings = {},
        scale = 1.25,
    }
) {
    root.classList.add('ccw2-root');
    const answerSlot = root.querySelector('.ccw2-answer-slot');
    const viewerSlot = root.querySelector('.ccw2-viewer-slot');
    const uploadSlot = root.querySelector('.ccw2-upload-slot');
    const askSlot = root.querySelector('.ccw2-ask-slot');

    // Documents uploaded on this page whose extraction completed, newest
    // last. A question names at most `maxDocuments` of them (the agent's
    // MAX_DOCUMENTS_PER_TURN); a failed extraction is never included.
    const ready = [];
    let ask = null;

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
                  // card shows next is asked for afterwards, through the
                  // question box — this control does not itself ask one.
                  onComplete: (payload) => {
                      if (payload.extraction_status !== 'complete' || typeof payload.document_id !== 'string') {
                          return;
                      }
                      const at = ready.indexOf(payload.document_id);
                      if (at !== -1) {
                          ready.splice(at, 1);
                      }
                      ready.push(payload.document_id);
                      if (ask) {
                          ask.refreshDocuments();
                      }
                  },
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

    /**
     * One question's outcome: whose answer, which outcome, then the answer
     * view when the turn reached the answer step, or the agent's message
     * when it did not (a refusal, a document still processing). The view's
     * prose is that same message, so nothing is shown twice or re-worded.
     */
    function showAnswer(payload) {
        const bar = patientBar(payload, strings);
        if (!bar) {
            // F-62: an answer that does not say whose it is is not drawn.
            answerSlot.replaceChildren(node('p', 'ccw2-error', strings.askFailed || 'The question could not be answered.'));
            return;
        }
        const outcome = node('p', 'ccw2-answer-status', statusLabel(payload.answer_status, strings));
        outcome.dataset.answerStatus = String(payload.answer_status);
        if (typeof payload.correlation_id === 'string') {
            outcome.appendChild(node('span', 'ccw2-chip-meta', ` ${strings.reference || 'Reference:'} ${payload.correlation_id}`));
        }
        const head = node('div', 'ccw2-answer-head');
        head.append(bar, outcome);
        const body = node('div', 'ccw2-answer-body');
        answerSlot.replaceChildren(head, body);
        if (payload.answer_view) {
            current = payload.answer_view;
            renderAnswerView(body, payload.answer_view, {
                strings,
                onCite: (group) => {
                    viewer.show(group).catch(() => {});
                },
            });
        } else {
            body.appendChild(node('p', 'ccw2-prose', payload.message));
        }
    }

    if (askSource && askSlot) {
        ask = createAskControl(askSlot, {
            source: askSource,
            documents: () => ready.slice(-maxDocuments),
            onAnswer: showAnswer,
            maxLength: questionMaxLength,
            strings,
        });
    }

    return {show, showAnswer, viewer, upload, ask, get view() { return current; }};
}

function node(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined && text !== null) {
        element.textContent = String(text);
    }
    return element;
}

/**
 * Mount from `data-` attributes, for the chart card.
 *
 * Reads:
 *   data-document-endpoint   GET ...?document_id=<id> -> application/pdf
 *   data-upload-endpoint     POST multipart
 *   data-status-endpoint     GET ...?document_id=...
 *   data-ask-endpoint        POST form (question, conversation, document_ids[])
 *   data-pid                 the open patient
 *   data-csrf-token          OpenEMR's per-session token
 *   data-correlation-id      this chart open's id; every route forwards it
 *   data-question-max-length, data-max-documents
 *   data-worker-src          the vendored PDF.js worker
 *
 * @param {HTMLElement} root
 * @param {object} pdfjs
 * @param {object} [strings]
 */
export function mountFromDataset(root, pdfjs, strings = {}) {
    const data = root.dataset;
    const fields = () => ({pid: data.pid, csrf_token_form: data.csrfToken, correlation_id: data.correlationId});
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
        askSource: data.askEndpoint ? moduleAskSource({endpoint: data.askEndpoint, fields}) : null,
        questionMaxLength: Number(data.questionMaxLength) || 500,
        maxDocuments: Number(data.maxDocuments) || 4,
        strings,
    });
}
