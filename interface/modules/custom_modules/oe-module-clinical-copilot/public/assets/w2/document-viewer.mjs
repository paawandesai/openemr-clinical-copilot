/**
 * Clinical Co-Pilot: the cited-page viewer.
 *
 * Renders one page of a source PDF with PDF.js and draws the citation boxes
 * on top of it. The geometry is all in `bbox-overlay.mjs`; this file is the
 * PDF.js and DOM half.
 *
 * WHERE THE BYTES COME FROM
 *
 * Never from the browser's own credentials against FHIR. The page asks a
 * `DocumentSource` for an ArrayBuffer and has no idea what is behind it. In
 * OpenEMR that is `GET <module>/document?document_id=<int>`, a module route
 * that checks the clinician's own session and the `patients`/`docs` ACL and
 * streams the file server-side, so no token, and no FHIR base URL, is ever
 * in this document. In fixture mode it is a file next to the page.
 *
 * (`GET /fhir/Binary/:id` was the original route and is not usable: for a
 * non-patient request `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php`
 * requires the `admin`/`users` ACL, which a physician does not have, so it
 * answers 403 for exactly the people this feature is for. See D28.)
 *
 * WHAT A LAYER OF BOXES IS FOR
 *
 * The overlay is a sibling of the canvas, not something painted into it.
 * Boxes are therefore real elements: focusable, hit-testable, announced by a
 * screen reader, and still there when the page is re-rendered at a new zoom
 * — which is also why the canvas is re-rendered rather than CSS-scaled, so a
 * box drawn at 150% is drawn against ink rasterised at 150%.
 *
 * NOTHING HERE LOGS. A failure surfaces as a state on the viewer and an
 * error code; the page content, the file name and the field values never
 * reach the console (work order requirement 6).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import {overlayRect, transformsAgree, unionRect, viewportTransform, visibleRect} from './bbox-overlay.mjs';

/** Zoom steps the control offers, as a multiple of 1 CSS px per PDF point. */
export const ZOOM_STEPS = [0.75, 1, 1.25, 1.5, 2, 3];

/** What `load()` reports when it could not get the bytes. */
export const DOCUMENT_ERRORS = {
    FORBIDDEN: 'document_forbidden',
    NOT_FOUND: 'document_not_found',
    UNAVAILABLE: 'document_unavailable',
    UNREADABLE: 'document_unreadable',
};

const DEFAULT_STRINGS = {
    noDocument: 'Select a citation to see the page it came from.',
    loading: 'Loading the page…',
    pageOf: 'Page %page% of %pages%',
    previous: 'Previous page',
    next: 'Next page',
    zoomOut: 'Zoom out',
    zoomIn: 'Zoom in',
    forbidden: 'You do not have access to this document.',
    notFound: 'That document is no longer available.',
    unavailable: 'The document could not be fetched.',
    unreadable: 'The file could not be read as a PDF.',
    boxLabel: 'Cited region: %field%',
    degenerate: 'This region has no width on the page — an empty column. It is marked, not widened.',
    rendererMismatch:
        'The page was drawn by a renderer this overlay does not recognise, so no boxes are shown.',
};

function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
        element.className = className;
    }
    if (text !== undefined && text !== null) {
        element.textContent = String(text);
    }
    return element;
}

function fill(template, values) {
    return String(template).replace(/%(\w+)%/g, (match, key) =>
        Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
    );
}

/**
 * A source of PDF bytes for one document id.
 *
 * @typedef {object} DocumentSource
 * @property {(documentId: string) => Promise<ArrayBuffer>} fetchBytes
 */

/**
 * The module route, as the OpenEMR card uses it.
 *
 * `document_id` is OpenEMR's own integer document id, not the
 * `SourceDocument.document_id` the contract carries — the caller maps
 * between them, because only it knows how an uploaded file was registered.
 *
 * @param {string} endpoint e.g. `/interface/modules/.../public/document.php`
 * @param {(id: string) => string} [toDocumentId] Contract id -> OpenEMR id.
 * @returns {DocumentSource}
 */
export function moduleDocumentSource(endpoint, toDocumentId = (id) => id) {
    return {
        async fetchBytes(documentId) {
            const url = `${endpoint}${endpoint.includes('?') ? '&' : '?'}document_id=${encodeURIComponent(
                toDocumentId(documentId)
            )}`;
            let response;
            try {
                response = await fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/pdf'}});
            } catch {
                throw new DocumentError(DOCUMENT_ERRORS.UNAVAILABLE);
            }
            if (response.status === 403) {
                throw new DocumentError(DOCUMENT_ERRORS.FORBIDDEN);
            }
            if (response.status === 404) {
                throw new DocumentError(DOCUMENT_ERRORS.NOT_FOUND);
            }
            if (!response.ok) {
                throw new DocumentError(DOCUMENT_ERRORS.UNAVAILABLE);
            }
            return response.arrayBuffer();
        },
    };
}

/**
 * Fixture mode: the sample PDF sitting next to the fixture JSON.
 *
 * @param {(documentId: string) => string} toUrl
 * @returns {DocumentSource}
 */
export function staticDocumentSource(toUrl) {
    return {
        async fetchBytes(documentId) {
            let response;
            try {
                response = await fetch(toUrl(documentId));
            } catch {
                throw new DocumentError(DOCUMENT_ERRORS.UNAVAILABLE);
            }
            if (response.status === 404) {
                throw new DocumentError(DOCUMENT_ERRORS.NOT_FOUND);
            }
            if (!response.ok) {
                throw new DocumentError(DOCUMENT_ERRORS.UNAVAILABLE);
            }
            return response.arrayBuffer();
        },
    };
}

/** A failure with a code and no payload — nothing here can carry PHI. */
export class DocumentError extends Error {
    constructor(code) {
        super(code);
        this.name = 'DocumentError';
        this.code = code;
    }
}

const ERROR_STRINGS = {
    [DOCUMENT_ERRORS.FORBIDDEN]: 'forbidden',
    [DOCUMENT_ERRORS.NOT_FOUND]: 'notFound',
    [DOCUMENT_ERRORS.UNAVAILABLE]: 'unavailable',
    [DOCUMENT_ERRORS.UNREADABLE]: 'unreadable',
};

/**
 * Build a viewer inside `container`.
 *
 * @param {HTMLElement} container
 * @param {object} options
 * @param {object} options.pdfjs The imported PDF.js module namespace.
 * @param {string} options.workerSrc URL of the vendored worker.
 * @param {DocumentSource} options.source
 * @param {object} [options.strings]
 * @param {number} [options.scale] Initial CSS px per PDF point.
 */
export function createDocumentViewer(container, {pdfjs, workerSrc, source, strings: given = {}, scale = 1.25}) {
    const strings = {...DEFAULT_STRINGS, ...given};
    pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

    container.replaceChildren();
    const toolbar = node('div', 'ccw2-viewer-toolbar');
    const previous = node('button', 'ccw2-viewer-button', '‹');
    previous.type = 'button';
    previous.title = strings.previous;
    const pageLabel = node('span', 'ccw2-viewer-page');
    const next = node('button', 'ccw2-viewer-button', '›');
    next.type = 'button';
    next.title = strings.next;
    const zoomOut = node('button', 'ccw2-viewer-button', '−');
    zoomOut.type = 'button';
    zoomOut.title = strings.zoomOut;
    const zoomLabel = node('span', 'ccw2-viewer-zoom');
    const zoomIn = node('button', 'ccw2-viewer-button', '+');
    zoomIn.type = 'button';
    zoomIn.title = strings.zoomIn;
    toolbar.append(previous, pageLabel, next, zoomOut, zoomLabel, zoomIn);

    const scroll = node('div', 'ccw2-viewer-scroll');
    const stage = node('div', 'ccw2-viewer-stage');
    const canvas = node('canvas', 'ccw2-viewer-canvas');
    const layer = node('div', 'ccw2-viewer-overlay');
    stage.append(canvas, layer);
    scroll.appendChild(stage);
    const status = node('p', 'ccw2-viewer-status', strings.noDocument);
    container.append(toolbar, status, scroll);

    /** @type {{pdf: object|null, documentId: string|null, page: number, scale: number, task: object|null}} */
    const state = {pdf: null, documentId: null, page: 1, scale, task: null, wanted: []};
    let renderToken = 0;

    function setStatus(text, isError = false) {
        status.textContent = text;
        status.classList.toggle('ccw2-viewer-status--error', isError);
        status.hidden = text === '';
    }

    function setControls() {
        const pages = state.pdf ? state.pdf.numPages : 0;
        previous.disabled = !state.pdf || state.page <= 1;
        next.disabled = !state.pdf || state.page >= pages;
        zoomOut.disabled = !state.pdf || state.scale <= ZOOM_STEPS[0];
        zoomIn.disabled = !state.pdf || state.scale >= ZOOM_STEPS[ZOOM_STEPS.length - 1];
        pageLabel.textContent = state.pdf ? fill(strings.pageOf, {page: state.page, pages}) : '';
        zoomLabel.textContent = state.pdf ? `${Math.round(state.scale * 100)}%` : '';
    }

    /**
     * Draw one page, then its boxes.
     *
     * `renderToken` guards against two renders racing — a second page
     * request while the first is still rasterising would otherwise be able
     * to paint its boxes over the other page's ink, which is the one bug in
     * a source viewer that a reader cannot possibly catch.
     */
    async function draw() {
        if (!state.pdf) {
            return;
        }
        const token = ++renderToken;
        if (state.task) {
            state.task.cancel();
            state.task = null;
        }
        const page = await state.pdf.getPage(state.page);
        if (token !== renderToken) {
            return;
        }
        const viewport = page.getViewport({scale: state.scale});
        const ratio = window.devicePixelRatio || 1;

        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        stage.style.width = `${viewport.width}px`;
        stage.style.height = `${viewport.height}px`;
        layer.style.width = `${viewport.width}px`;
        layer.style.height = `${viewport.height}px`;

        state.task = page.render({
            canvas,
            viewport,
            transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
        });
        try {
            await state.task.promise;
        } catch (error) {
            if (error && error.name === 'RenderingCancelledException') {
                return;
            }
            throw error;
        }
        if (token !== renderToken) {
            return;
        }
        state.task = null;
        drawBoxes(page, viewport);
        setControls();
        setStatus('');
    }

    function drawBoxes(page, viewport) {
        layer.replaceChildren();
        const view = page.view;

        // PDF.js built this viewport; bbox-overlay.mjs can build the same
        // one from `page.view`, the scale and the rotation. If they disagree
        // the library has changed how a page maps to pixels, and every box
        // would be drawn somewhere plausible and wrong — so draw none.
        const ours = viewportTransform({view, scale: state.scale, rotation: page.rotate});
        if (!transformsAgree(viewport.transform, ours, 1e-6)) {
            setStatus(strings.rendererMismatch, true);
            return;
        }

        const rendered = {view, transform: viewport.transform};
        const drawn = [];
        for (const wanted of state.wanted) {
            for (const citation of wanted.citations) {
                if (!citation.bbox || citation.bbox.page !== state.page) {
                    continue;
                }
                const rect = visibleRect(overlayRect(citation.bbox, rendered));
                const box = node('div', 'ccw2-box');
                if (rect.degenerate) {
                    box.classList.add('ccw2-box--point');
                    box.title = strings.degenerate;
                }
                if (wanted.active) {
                    box.classList.add('ccw2-box--active');
                }
                box.style.left = `${rect.left}px`;
                box.style.top = `${rect.top}px`;
                box.style.width = `${rect.width}px`;
                box.style.height = `${rect.height}px`;
                box.dataset.field = citation.field_or_chunk_id;
                box.dataset.sourceId = citation.source_id;
                box.setAttribute('role', 'img');
                box.setAttribute('aria-label', fill(strings.boxLabel, {field: citation.field_or_chunk_id}));
                layer.appendChild(box);
                if (wanted.active) {
                    drawn.push(rect);
                }
            }
        }
        const focus = unionRect(drawn);
        if (focus) {
            scrollTo(focus);
        }
    }

    function scrollTo(rect) {
        const margin = 48;
        const top = stage.offsetTop + rect.top - margin;
        const left = stage.offsetLeft + rect.left - margin;
        scroll.scrollTo({
            top: Math.max(0, top),
            left: Math.max(0, left),
            behavior: 'smooth',
        });
    }

    async function load(documentId) {
        if (state.documentId === documentId && state.pdf) {
            return;
        }
        setStatus(strings.loading);
        layer.replaceChildren();
        let bytes;
        try {
            bytes = await source.fetchBytes(documentId);
        } catch (error) {
            const code = error instanceof DocumentError ? error.code : DOCUMENT_ERRORS.UNAVAILABLE;
            setStatus(strings[ERROR_STRINGS[code]], true);
            throw error;
        }
        try {
            // `data` takes ownership of the buffer; a copy keeps the caller's
            // ArrayBuffer usable if it wants to retry.
            state.pdf = await pdfjs.getDocument({data: new Uint8Array(bytes).slice()}).promise;
        } catch {
            state.pdf = null;
            setStatus(strings[ERROR_STRINGS[DOCUMENT_ERRORS.UNREADABLE]], true);
            throw new DocumentError(DOCUMENT_ERRORS.UNREADABLE);
        }
        state.documentId = documentId;
        state.page = 1;
        setControls();
    }

    previous.addEventListener('click', () => {
        if (state.page > 1) {
            state.page -= 1;
            draw();
        }
    });
    next.addEventListener('click', () => {
        if (state.pdf && state.page < state.pdf.numPages) {
            state.page += 1;
            draw();
        }
    });
    zoomOut.addEventListener('click', () => {
        const index = ZOOM_STEPS.findIndex((step) => step >= state.scale);
        state.scale = ZOOM_STEPS[Math.max(0, index - 1)];
        draw();
    });
    zoomIn.addEventListener('click', () => {
        const index = ZOOM_STEPS.findIndex((step) => step > state.scale);
        state.scale = ZOOM_STEPS[index === -1 ? ZOOM_STEPS.length - 1 : index];
        draw();
    });

    setControls();

    return {
        /**
         * Show a citation group's boxes, loading and paging as needed.
         *
         * @param {{sourceId: string, citations: Array<object>}} group
         */
        async show(group) {
            const located = group.citations.filter((citation) => citation.bbox);
            if (!located.length) {
                return;
            }
            await load(group.sourceId);
            state.wanted = [{...group, active: true}];
            state.page = located[0].bbox.page;
            await draw();
        },

        /** Everything currently drawn, for the alignment check. */
        boxes() {
            return [...layer.children].map((box) => ({
                field: box.dataset.field,
                sourceId: box.dataset.sourceId,
                left: parseFloat(box.style.left),
                top: parseFloat(box.style.top),
                width: parseFloat(box.style.width),
                height: parseFloat(box.style.height),
                point: box.classList.contains('ccw2-box--point'),
            }));
        },

        /** Draw every located citation on one page at once — fixture mode. */
        async showAll(documentId, citations, page, at = state.scale) {
            await load(documentId);
            state.wanted = citations.map((citation) => ({sourceId: documentId, citations: [citation], active: false}));
            state.page = page;
            state.scale = at;
            await draw();
        },

        state,
        canvas,
        layer,
    };
}
