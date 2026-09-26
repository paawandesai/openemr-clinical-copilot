/**
 * Clinical Co-Pilot: the document upload control.
 *
 * A file picker, a document type, and the status of the extraction that
 * starts when the file lands.
 *
 * D7 — EXTRACTION RUNS AT UPLOAD TIME, NOT AT QUESTION TIME
 *
 * So this control is where extraction is observed, and the status it polls
 * is a first-class state rather than a spinner. `ExtractionStatus`
 * (agent/app/models/w2/documents.py) has four members and this file has a
 * case for each: `pending`, `in_progress`, `complete`, `failed`. A question
 * asked against a document that is still extracting gets an explicit "still
 * processing" — never a wait that looks like an answer, and never a partial
 * extraction presented as a whole one.
 *
 * The two document types are the two schemas that exist: `lab_pdf` and
 * `intake_form`. The reader picks; nothing here guesses from the file name,
 * because a wrong guess routes the file to the wrong extractor and the
 * result is a valid-looking extraction of the wrong shape.
 *
 * `extraction_status`, NOT `status`
 *
 * The module wraps every co-pilot response in the envelope `brief.php`
 * established — `{"status": "ok"|"error", ...}` — so `status` is already
 * taken by the transport. The extraction state rides in
 * `extraction_status`, and the two are different vocabularies that would
 * otherwise collide on the one field that decides whether to keep polling.
 * The agent's own responses have no envelope and call it `status`; the
 * module translates (docs/w2/ui-integration.md).
 *
 * A DOCUMENT FOR ANOTHER PATIENT IS SAID SO (D60). When the extraction
 * finds a name or date of birth that contradicts this chart
 * (`identity_check: "mismatch"`), the module moves the chart copy to
 * quarantine and reports how that went (`quarantine`). The status line then
 * says the document was not used and where it is — never a plain "complete"
 * — and `isUsable` keeps it out of the documents a question names.
 *
 * THE CHART WRITE IS REPORTED, AND A CONFLICT IS SHOWN (work order 34). A
 * lab report's extraction ends by writing its verified rows to the chart
 * (D54). The status payload carries `write_back` once that has ended: the
 * counts of rows added, already in the chart (from any source, any file),
 * in conflict with a chart value, and not written. For a lab report the
 * control keeps polling past `complete` until it is set, and then lists
 * each conflict as "the chart has X, this document has Y" under the status
 * line. A conflict is never written and never resolved here: the line is
 * for the clinician to act on. `awaitsWriteBack` says when to keep asking.
 *
 * NOTHING LOGS, AND NOTHING KEEPS THE FILE. The chosen file is handed to
 * `FormData` and dropped; no name, size or content is written to the
 * console or to storage (work order requirement 6). Poll failures surface
 * as a status and a correlation id.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

/** `DocumentKind` (agent/app/models/w2/documents.py). */
export const DOCUMENT_KINDS = ['lab_pdf', 'intake_form'];

/** `ExtractionStatus`, in the order a document moves through them. */
export const EXTRACTION_STATUSES = ['pending', 'in_progress', 'complete', 'failed'];

/** Terminal states: polling stops here. */
export const TERMINAL_STATUSES = ['complete', 'failed'];

/**
 * Polling schedule, in milliseconds, then every `POLL_TAIL_MS` until
 * `POLL_LIMIT_MS`. Front-loaded because a digital PDF finishes in seconds
 * and a heavy scan does not, and a fixed 1s interval would either hammer
 * the first case or crawl through the second.
 */
const POLL_STEPS = [400, 800, 1500, 2500];
const POLL_TAIL_MS = 4000;
const POLL_LIMIT_MS = 5 * 60 * 1000;

/** 25 MB: past this the browser should say so before the upload starts. */
export const MAX_BYTES = 25 * 1024 * 1024;

const DEFAULT_STRINGS = {
    heading: 'Upload a document',
    note: 'Extraction starts when the file is uploaded, not when you ask a question. A question asked before it finishes is answered "still processing".',
    kindLabel: 'Document type',
    kindLabPdf: 'Lab report (PDF)',
    kindIntakeForm: 'Intake form',
    choose: 'Choose a file',
    send: 'Upload',
    noFile: 'No file chosen',
    statusPending: 'Queued for extraction',
    statusInProgress: 'Extracting…',
    statusComplete: 'Extraction complete',
    statusFailed: 'Extraction failed',
    statusUnknown: 'Unknown extraction status',
    uploading: 'Uploading…',
    tooLarge: 'That file is larger than 25 MB.',
    notPdf: 'Only PDF files can be extracted.',
    uploadFailed: 'The upload was not accepted.',
    pollFailed: 'The extraction status could not be read. It may still be running.',
    pollTimedOut: 'Still extracting after five minutes. It has not failed; this page stopped asking.',
    reference: 'Reference:',
    failureUnreadableFile: 'the file could not be read',
    failureUnsupportedMimeType: 'that file type is not supported',
    failureNoTextLayerAndOcrFailed: 'no text layer, and OCR could not read it',
    failureModelOutputInvalid: 'the extractor returned something the schema rejects',
    failurePageLimitExceeded: 'too many pages',
    failureTimedOut: 'extraction timed out',
    failureVisionCallFailed: 'the extraction model could not be reached',
    failureNoFieldsLocated: 'nothing read from the file could be found on its pages',
    failureInternalError: 'the extractor failed unexpectedly',
    failureInterrupted: 'the co-pilot restarted before it finished; upload the file again',
    identityMismatch: 'Not used: the name or date of birth printed on this document does not match this chart. Nothing from it is stated or written to the chart.',
    quarantineQuarantined: 'It was moved to the “Quarantine - Patient Mismatch” category of this chart\'s Documents, with a note; it was not deleted.',
    quarantineNotOnChart: 'No copy of it is filed on this chart.',
    quarantineFailed: 'It could NOT be moved to quarantine and is still filed as a normal document: move it by hand.',
    reconciledOne: '1 document on this chart was moved to the “Quarantine - Patient Mismatch” category: the name or date of birth printed on it does not match this chart. It was not deleted.',
    reconciledMany: '%count% documents on this chart were moved to the “Quarantine - Patient Mismatch” category: the name or date of birth printed on them does not match this chart. They were not deleted.',
    reconcileFailed: 'A document on this chart whose name or date of birth does not match it could NOT be moved to quarantine: check this chart\'s Documents.',
    writeBackPending: 'Checking the chart for these results…',
    writeBackTimedOut: 'The chart write is still running after five minutes; this page stopped asking. Reload the chart to see the result.',
    writeBackAdded: '%count% result(s) added to the chart',
    writeBackAlready: '%count% already in the chart',
    writeBackDuplicate: '%count% from an earlier upload of this file',
    writeBackConflicts: '%count% conflict(s) with the chart',
    writeBackSkipped: '%count% not written (not verified on the page)',
    writeBackNoneWritten: 'nothing written',
    writeBackNothingWritable: 'Chart: nothing written — no result was verified on the page.',
    writeBackIdentityNotConfirmed: 'Chart: nothing written — the patient on the document was not confirmed as this chart\'s.',
    writeBackTokenRefused: 'Chart: the write was refused — this client cannot write lab results.',
    writeBackRequestRefused: 'Chart: the write was refused by OpenEMR.',
    writeBackEndpointUnavailable: 'Chart: the write could not be completed. Upload the file again to retry.',
    writeBackExtractionNotComplete: 'Chart: nothing written — the extraction did not complete.',
    writeBackConflictLine: '%name% (LOINC %code%), collected %when%, page %page%: the chart has %chart%, this document has %document%. Not written.',
};

const STATUS_STRINGS = {
    pending: 'statusPending',
    in_progress: 'statusInProgress',
    complete: 'statusComplete',
    failed: 'statusFailed',
};

const FAILURE_STRINGS = {
    unreadable_file: 'failureUnreadableFile',
    unsupported_mime_type: 'failureUnsupportedMimeType',
    no_text_layer_and_ocr_failed: 'failureNoTextLayerAndOcrFailed',
    model_output_invalid: 'failureModelOutputInvalid',
    page_limit_exceeded: 'failurePageLimitExceeded',
    timed_out: 'failureTimedOut',
    vision_call_failed: 'failureVisionCallFailed',
    no_fields_located: 'failureNoFieldsLocated',
    internal_error: 'failureInternalError',
    interrupted: 'failureInterrupted',
};

const QUARANTINE_STRINGS = {
    quarantined: 'quarantineQuarantined',
    not_on_chart: 'quarantineNotOnChart',
    failed: 'quarantineFailed',
};

/** `WriteBackDecision` values that ended without a write, and what to say. */
const WRITE_BACK_DECISION_STRINGS = {
    nothing_writable: 'writeBackNothingWritable',
    identity_not_confirmed: 'writeBackIdentityNotConfirmed',
    token_refused: 'writeBackTokenRefused',
    request_refused: 'writeBackRequestRefused',
    endpoint_unavailable: 'writeBackEndpointUnavailable',
    extraction_not_complete: 'writeBackExtractionNotComplete',
};

/** Decisions after which the rows are not known to be in the chart: shown as errors. */
const WRITE_BACK_FAILED_DECISIONS = ['token_refused', 'request_refused', 'endpoint_unavailable'];

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

/**
 * How long to wait before the nth poll.
 *
 * @param {number} attempt 0-based.
 * @returns {number} milliseconds
 */
export function pollDelay(attempt) {
    return attempt < POLL_STEPS.length ? POLL_STEPS[attempt] : POLL_TAIL_MS;
}

/**
 * Is this a status the control should stop polling on?
 *
 * @param {string} status An `ExtractionStatus` value.
 * @returns {boolean}
 */
export function isTerminal(status) {
    return TERMINAL_STATUSES.includes(status);
}

/**
 * Did the extraction find another patient's name or date of birth on it?
 *
 * @param {object} payload An upload or status payload.
 * @returns {boolean}
 */
export function isIdentityMismatch(payload) {
    return payload.extraction_status === 'complete' && payload.identity_check === 'mismatch';
}

/**
 * May a question name this document? Only a complete extraction of this
 * chart's patient's document (D60).
 *
 * @param {object} payload The terminal upload or status payload.
 * @returns {boolean}
 */
export function isUsable(payload) {
    return payload.extraction_status === 'complete' && !isIdentityMismatch(payload);
}

/**
 * Is the chart write for this document still to be reported? Only a lab
 * report has one, only once its extraction has ended, and only when the
 * module passed the field at all (an older agent never will, and the
 * control must not wait on it).
 *
 * @param {object} payload An upload or status payload.
 * @returns {boolean}
 */
export function awaitsWriteBack(payload) {
    return (
        payload.kind === 'lab_pdf' &&
        isTerminal(payload.extraction_status) &&
        Object.prototype.hasOwnProperty.call(payload, 'write_back') &&
        payload.write_back === null
    );
}

function count(template, n) {
    return template.replace('%count%', String(n));
}

/**
 * What the chart write did, for the status line and the list under it.
 *
 * @param {object|null|undefined} summary The payload's `write_back`.
 * @param {object} [given] Translated strings.
 * @returns {{text: string, conflicts: string[], isError: boolean}|null} Null
 *     when there is nothing to say (no summary, or not a lab report).
 */
export function describeWriteBack(summary, given = {}) {
    if (!summary || typeof summary !== 'object' || summary.decision === 'not_lab_report') {
        return null;
    }
    const strings = {...DEFAULT_STRINGS, ...given};
    const decisionKey = WRITE_BACK_DECISION_STRINGS[summary.decision];
    if (decisionKey) {
        return {
            text: strings[decisionKey],
            conflicts: [],
            isError: WRITE_BACK_FAILED_DECISIONS.includes(summary.decision),
        };
    }
    const n = (field) => Number(summary[field]) || 0;
    const parts = [];
    if (n('created') > 0) {
        parts.push(count(strings.writeBackAdded, n('created')));
    }
    if (n('already_in_chart') > 0) {
        parts.push(count(strings.writeBackAlready, n('already_in_chart')));
    }
    if (n('duplicate_ignored') > 0) {
        parts.push(count(strings.writeBackDuplicate, n('duplicate_ignored')));
    }
    if (n('conflict') > 0) {
        parts.push(count(strings.writeBackConflicts, n('conflict')));
    }
    if (n('skipped') > 0) {
        parts.push(count(strings.writeBackSkipped, n('skipped')));
    }
    if (parts.length === 0) {
        parts.push(strings.writeBackNoneWritten);
    }
    const conflicts = (Array.isArray(summary.conflicts) ? summary.conflicts : []).map((conflict) =>
        strings.writeBackConflictLine
            .replace('%name%', String(conflict.test_name))
            .replace('%code%', String(conflict.test_code))
            .replace('%when%', String(conflict.collected_at || conflict.collection_date).replace('T', ' '))
            .replace('%page%', String(conflict.page))
            .replace('%chart%', [conflict.chart_value, conflict.chart_unit].filter(Boolean).join(' '))
            .replace('%document%', [conflict.document_value, conflict.document_unit].filter(Boolean).join(' '))
    );
    return {text: `Chart: ${parts.join(', ')}.`, conflicts, isError: conflicts.length > 0};
}

/**
 * The status line for an upload or status payload.
 *
 * @param {object} payload
 * @param {object} [given] Translated strings; defaults for the rest.
 * @returns {string}
 */
export function describeStatus(payload, given = {}) {
    const strings = {...DEFAULT_STRINGS, ...given};
    const key = STATUS_STRINGS[payload.extraction_status];
    let text = key ? strings[key] : strings.statusUnknown;
    if (payload.extraction_status === 'failed') {
        const reason = FAILURE_STRINGS[payload.failure_reason];
        text += reason ? ` — ${strings[reason]}` : '';
    }
    if (isIdentityMismatch(payload)) {
        text += ` — ${strings.identityMismatch}`;
        // No outcome (an older module) is described as a failure to move:
        // never implied to be safely set aside when it may not be.
        text += ` ${strings[QUARANTINE_STRINGS[payload.quarantine] || 'quarantineFailed']}`;
    }
    const writeBack = describeWriteBack(payload.write_back, strings);
    if (writeBack) {
        text += ` — ${writeBack.text}`;
    } else if (awaitsWriteBack(payload)) {
        text += ` — ${strings.writeBackPending}`;
    }
    if (typeof payload.correlation_id === 'string') {
        text += ` (${strings.reference} ${payload.correlation_id})`;
    }
    return text;
}

/**
 * Should the status line be styled as an error for this payload?
 *
 * @param {object} payload
 * @returns {boolean}
 */
export function isErrorStatus(payload) {
    const writeBack = describeWriteBack(payload.write_back);
    return (
        payload.extraction_status === 'failed' ||
        isIdentityMismatch(payload) ||
        Boolean(writeBack && writeBack.isError)
    );
}

/**
 * Reject a file the server would only reject later, and say why.
 *
 * Deliberately not a guess at the document type: a `.pdf` check is about
 * whether the pipeline can read the bytes at all, which is a different
 * question from which of the two schemas applies.
 *
 * @param {File} file
 * @returns {string|null} A `DEFAULT_STRINGS` key, or null if it is fine.
 */
export function rejectFile(file) {
    if (file.size > MAX_BYTES) {
        return 'tooLarge';
    }
    const looksPdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
    return looksPdf ? null : 'notPdf';
}

/**
 * What the card says after reconciling on load (D60), or null for nothing.
 * Counts only: which documents is in the chart's Documents, with a note.
 *
 * @param {{quarantined?: number, failed?: number}} result The route's payload.
 * @param {object} [given] Translated strings.
 * @returns {{text: string, isError: boolean}|null}
 */
export function reconcileMessage(result, given = {}) {
    const strings = {...DEFAULT_STRINGS, ...given};
    const moved = Number(result && result.quarantined) || 0;
    const failed = Number(result && result.failed) || 0;
    const parts = [];
    if (moved === 1) {
        parts.push(strings.reconciledOne);
    } else if (moved > 1) {
        parts.push(strings.reconciledMany.replace('%count%', String(moved)));
    }
    if (failed > 0) {
        parts.push(strings.reconcileFailed);
    }
    return parts.length ? {text: parts.join(' '), isError: failed > 0} : null;
}

/**
 * The reconcile route (D60): called once when the card loads.
 *
 * @param {object} options
 * @param {string} options.endpoint
 * @param {() => Record<string, string>} options.fields pid, csrf, correlation id.
 * @returns {{reconcile: () => Promise<object>}}
 */
export function moduleReconcileSource({endpoint, fields}) {
    return {
        async reconcile() {
            const body = new URLSearchParams(fields());
            const response = await fetch(endpoint, {method: 'POST', credentials: 'same-origin', body});
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload) {
                throw new UploadError('reconcile_unavailable', payload);
            }
            return payload;
        },
    };
}

/**
 * The module route, as the OpenEMR card uses it.
 *
 * @param {object} options
 * @param {string} options.endpoint Module upload route.
 * @param {string} options.statusEndpoint Module extraction-status route.
 * @param {() => Record<string, string>} options.fields Per-request fields
 *     the module needs (pid, csrf token). A function, because a CSRF token
 *     can be rotated between requests.
 * @returns {{upload: Function, status: Function}}
 */
export function moduleUploadSource({endpoint, statusEndpoint, fields}) {
    return {
        async upload(file, kind) {
            const body = new FormData();
            for (const [key, value] of Object.entries(fields())) {
                body.append(key, value);
            }
            body.append('document_kind', kind);
            body.append('document', file);
            const response = await fetch(endpoint, {method: 'POST', credentials: 'same-origin', body});
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload) {
                throw new UploadError('upload_rejected', payload);
            }
            return payload;
        },
        async status(documentId) {
            const query = new URLSearchParams({...fields(), document_id: documentId});
            const response = await fetch(`${statusEndpoint}?${query}`, {credentials: 'same-origin'});
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload) {
                throw new UploadError('status_unavailable', payload);
            }
            return payload;
        },
    };
}

/**
 * A failure with a code; the payload is kept only for a correlation id and
 * the module's `message`, which the module guarantees is safe to show (it is
 * fixed text chosen by the error code, never exception text or chart content).
 */
export class UploadError extends Error {
    constructor(code, payload) {
        super(code);
        this.name = 'UploadError';
        this.code = code;
        this.correlationId =
            payload && typeof payload.correlation_id === 'string' ? payload.correlation_id : null;
        this.safeMessage = payload && typeof payload.message === 'string' ? payload.message : null;
    }
}

/**
 * Build the control inside `container`.
 *
 * @param {HTMLElement} container
 * @param {object} options
 * @param {{upload: Function, status: Function}} options.source
 * @param {object} [options.strings]
 * @param {(payload: object) => void} [options.onComplete] Called once, with
 *     the terminal status payload, when extraction finishes.
 * @param {(ms: number) => Promise<void>} [options.sleep] Injected so a test
 *     does not wait in real time.
 */
export function createUploadControl(
    container,
    {source, strings: given = {}, onComplete = () => {}, sleep = (ms) => new Promise((r) => setTimeout(r, ms))}
) {
    const strings = {...DEFAULT_STRINGS, ...given};
    container.replaceChildren();

    const form = node('form', 'ccw2-upload');
    form.appendChild(node('h4', 'ccw2-section-heading', strings.heading));
    form.appendChild(node('p', 'ccw2-section-note', strings.note));

    const row = node('div', 'ccw2-upload-row');
    const kindLabel = node('label', 'ccw2-upload-field');
    kindLabel.appendChild(node('span', '', strings.kindLabel));
    const kind = node('select', 'ccw2-upload-kind');
    for (const value of DOCUMENT_KINDS) {
        const option = node('option', '', value === 'lab_pdf' ? strings.kindLabPdf : strings.kindIntakeForm);
        option.value = value;
        kind.appendChild(option);
    }
    kindLabel.appendChild(kind);

    const fileLabel = node('label', 'ccw2-upload-field');
    fileLabel.appendChild(node('span', '', strings.choose));
    const file = node('input', 'ccw2-upload-file');
    file.type = 'file';
    file.accept = 'application/pdf,.pdf';
    fileLabel.appendChild(file);

    const send = node('button', 'ccw2-upload-send', strings.send);
    send.type = 'submit';
    send.disabled = true;

    row.append(kindLabel, fileLabel, send);
    form.appendChild(row);

    const status = node('p', 'ccw2-upload-status', strings.noFile);
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    form.appendChild(status);
    // One line per result the chart holds with a different value.
    const conflicts = node('ul', 'ccw2-writeback-conflicts');
    conflicts.hidden = true;
    form.appendChild(conflicts);
    container.appendChild(form);

    let polling = false;

    function say(text, isError = false) {
        status.textContent = text;
        status.classList.toggle('ccw2-upload-status--error', isError);
    }

    function showConflicts(lines) {
        conflicts.replaceChildren(...lines.map((line) => node('li', 'ccw2-writeback-conflict', line)));
        conflicts.hidden = lines.length === 0;
    }

    /** The status line and the conflict list, for one payload. */
    function render(payload) {
        say(describeStatus(payload, strings), isErrorStatus(payload));
        const writeBack = describeWriteBack(payload.write_back, strings);
        showConflicts(writeBack ? writeBack.conflicts : []);
    }

    /**
     * Ask until the answer is terminal — and, for a lab report, until its
     * chart write has been reported — or the budget runs out, or the caller
     * navigates away. A poll that errors does not end the run: the
     * extraction is a server-side job and a dropped request says nothing
     * about it, so the control reports the read failure and keeps asking.
     * `onComplete` fires once, when the extraction ends: the document is
     * usable for a question from then on, whatever the chart write says.
     */
    async function poll(documentId, alreadyCompleted = false) {
        polling = true;
        const started = Date.now();
        let completed = alreadyCompleted;
        for (let attempt = 0; polling; attempt += 1) {
            await sleep(pollDelay(attempt));
            if (!polling) {
                return;
            }
            let payload;
            try {
                payload = await source.status(documentId);
            } catch {
                say(strings.pollFailed, true);
                if (Date.now() - started > POLL_LIMIT_MS) {
                    polling = false;
                    say(strings.pollTimedOut, true);
                    return;
                }
                continue;
            }
            render(payload);
            if (isTerminal(payload.extraction_status)) {
                if (!completed) {
                    completed = true;
                    onComplete(payload);
                }
                if (!awaitsWriteBack(payload)) {
                    polling = false;
                    return;
                }
            }
            if (Date.now() - started > POLL_LIMIT_MS) {
                polling = false;
                say(completed ? strings.writeBackTimedOut : strings.pollTimedOut, true);
                return;
            }
        }
    }

    file.addEventListener('change', () => {
        const chosen = file.files && file.files[0];
        if (!chosen) {
            send.disabled = true;
            say(strings.noFile);
            return;
        }
        const problem = rejectFile(chosen);
        send.disabled = problem !== null;
        say(problem ? strings[problem] : chosen.name, problem !== null);
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const chosen = file.files && file.files[0];
        if (!chosen || rejectFile(chosen) !== null) {
            return;
        }
        send.disabled = true;
        file.disabled = true;
        kind.disabled = true;
        say(strings.uploading);
        let accepted;
        try {
            accepted = await source.upload(chosen, kind.value);
        } catch (error) {
            const reference =
                error instanceof UploadError && error.correlationId
                    ? ` (${strings.reference} ${error.correlationId})`
                    : '';
            // The module's own wording says why (for instance, D40's 409: the
            // file is already on a different chart); otherwise the generic line.
            const why = error instanceof UploadError && error.safeMessage ? ` ${error.safeMessage}` : '';
            say(strings.uploadFailed + why + reference, true);
            file.disabled = false;
            kind.disabled = false;
            send.disabled = false;
            return;
        }
        render(accepted);
        const terminal = isTerminal(accepted.extraction_status);
        if (terminal) {
            onComplete(accepted);
        }
        if (!terminal || awaitsWriteBack(accepted)) {
            await poll(accepted.document_id, terminal);
        }
        file.value = '';
        file.disabled = false;
        kind.disabled = false;
        send.disabled = true;
    });

    return {
        /** Stop polling — the card is going away. */
        stop() {
            polling = false;
        },
        elements: {form, kind, file, send, status, conflicts},
    };
}
