/**
 * Clinical Co-Pilot: the question box of the Documents & Sources card.
 *
 * One question at a time, about the open chart and — when the clinician has
 * uploaded any on this page — the documents whose extraction completed. The
 * answer is drawn by the caller (`onAnswer`); this control only asks, and
 * says what happened when there is nothing to draw.
 *
 * WHAT IS SHOWN IS THE AGENT'S. Status, refusal wording, UNVERIFIED lines and
 * limitations are all in the agent's `message`, which is also the answer
 * view's prose. Nothing here re-words or re-derives them; the status line
 * only names which of four outcomes this was.
 *
 * WHOSE ANSWER. Every answer is headed by the patient the module says it is
 * for, from the server's `patient` block — never from the page or the
 * question (docs/audit/findings.md F-62). An answer with no patient block is
 * not drawn.
 *
 * NOTHING LOGS AND NOTHING IS STORED. The question and the answer live in the
 * DOM only; no console output, no browser storage.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

/** `AskStatus` (agent/app/models/w2/ask.py). */
export const ANSWER_STATUSES = ['answered', 'partially_answered', 'not_in_record', 'refused'];

export const DEFAULT_STRINGS = {
    askHeading: 'Ask about this chart',
    askNote: 'Answers cite the chart, the uploaded documents that finished extracting, and guideline passages, each in its own section.',
    askPlaceholder: 'e.g. Is the potassium on the uploaded lab report abnormal, and what does guidance say about it?',
    askSend: 'Ask',
    asking: 'Asking...',
    askDocuments: 'Documents included: %count%',
    askNoDocuments: 'No uploaded documents included; the question is about the chart and guidance.',
    answerFor: 'Answer for',
    born: 'DOB',
    statusAnswered: 'Answered',
    statusPartiallyAnswered: 'Partly answered',
    statusNotInRecord: 'Not in the sources read',
    statusRefused: 'Not answered',
    askFailed: 'The question could not be answered.',
    contextChanged: 'A different patient is now open in this session. Reload this chart to ask about the patient shown.',
    reference: 'Reference:',
};

const STATUS_STRINGS = {
    answered: 'statusAnswered',
    partially_answered: 'statusPartiallyAnswered',
    not_in_record: 'statusNotInRecord',
    refused: 'statusRefused',
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

/**
 * A random conversation name, as the brief card makes one: the module derives
 * the agent's session id from it, the login session and the open patient.
 *
 * @returns {string}
 */
export function newConversationNonce() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * The module route.
 *
 * @param {object} options
 * @param {string} options.endpoint Module ask route (public/ask.php).
 * @param {() => Record<string, string>} options.fields pid, CSRF token, correlation id.
 * @returns {{ask: (question: string, conversation: string, documentIds: string[]) => Promise<object>}}
 */
export function moduleAskSource({endpoint, fields}) {
    return {
        async ask(question, conversation, documentIds) {
            const body = new URLSearchParams(fields());
            body.append('conversation', conversation);
            body.append('question', question);
            for (const id of documentIds) {
                body.append('document_ids[]', id);
            }
            let response;
            try {
                response = await fetch(endpoint, {method: 'POST', credentials: 'same-origin', body});
            } catch {
                throw new AskError('agent_unavailable', null);
            }
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload || payload.status !== 'ok') {
                throw new AskError(payload && typeof payload.error === 'string' ? payload.error : 'agent_unavailable', payload);
            }
            return payload;
        },
    };
}

/** A failure with the module's error code, its safe message and a correlation id. */
export class AskError extends Error {
    constructor(code, payload) {
        super(code);
        this.name = 'AskError';
        this.code = code;
        this.safeMessage = payload && typeof payload.message === 'string' ? payload.message : null;
        this.correlationId =
            payload && typeof payload.correlation_id === 'string' ? payload.correlation_id : null;
    }
}

/**
 * The one-line status of a turn: which outcome, never why in the agent's
 * place — the why is in its message.
 *
 * @param {string} status An `AskStatus` value.
 * @param {object} strings
 * @returns {string}
 */
export function statusLabel(status, strings = DEFAULT_STRINGS) {
    const key = STATUS_STRINGS[status];
    return key ? strings[key] : strings.statusRefused;
}

/**
 * The bar naming whose answer this is, or null if the payload does not say.
 *
 * @param {object} payload
 * @param {object} strings
 * @returns {HTMLElement|null}
 */
export function patientBar(payload, strings = DEFAULT_STRINGS) {
    const patient = payload && payload.patient;
    if (!patient || typeof patient.name !== 'string' || patient.name === '') {
        return null;
    }
    const bar = node('div', 'ccw2-patient-bar');
    bar.appendChild(node('strong', '', `${strings.answerFor} ${patient.name}`));
    if (typeof patient.date_of_birth === 'string' && patient.date_of_birth !== '') {
        bar.appendChild(node('span', '', ` · ${strings.born} ${patient.date_of_birth}`));
    }
    return bar;
}

/**
 * Build the control inside `container`.
 *
 * @param {HTMLElement} container
 * @param {object} options
 * @param {{ask: Function}} options.source
 * @param {() => string[]} options.documents The document ids to include now.
 * @param {(payload: object) => void} options.onAnswer Called with the module's payload.
 * @param {number} [options.maxLength]
 * @param {object} [options.strings]
 */
export function createAskControl(
    container,
    {source, documents, onAnswer, maxLength = 500, strings: given = {}}
) {
    const strings = {...DEFAULT_STRINGS, ...given};
    container.replaceChildren();
    let conversation = newConversationNonce();
    let locked = false;

    const form = node('form', 'ccw2-ask');
    form.appendChild(node('h4', 'ccw2-section-heading', strings.askHeading));
    form.appendChild(node('p', 'ccw2-section-note', strings.askNote));
    const question = node('textarea', 'ccw2-ask-question');
    question.rows = 2;
    question.maxLength = maxLength;
    question.placeholder = strings.askPlaceholder;
    question.setAttribute('aria-label', strings.askHeading);
    const send = node('button', 'ccw2-ask-send', strings.askSend);
    send.type = 'submit';
    const included = node('p', 'ccw2-ask-documents');
    const status = node('p', 'ccw2-ask-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    form.append(question, send, included, status);
    container.appendChild(form);

    function refreshDocuments() {
        const count = documents().length;
        included.textContent = count
            ? strings.askDocuments.replace('%count%', String(count))
            : strings.askNoDocuments;
    }
    refreshDocuments();

    function say(text, isError = false) {
        status.textContent = text;
        status.classList.toggle('ccw2-upload-status--error', isError);
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const text = question.value.trim();
        if (locked || text === '') {
            return;
        }
        send.disabled = true;
        question.disabled = true;
        say(strings.asking);
        try {
            const payload = await source.ask(text, conversation, documents());
            say('');
            onAnswer(payload);
        } catch (error) {
            const reference =
                error instanceof AskError && error.correlationId ? ` (${strings.reference} ${error.correlationId})` : '';
            if (error instanceof AskError && error.code === 'patient_context_changed') {
                // The chart this card was drawn for is no longer open: stop
                // asking, as the brief card does.
                locked = true;
                say(strings.contextChanged + reference, true);
                return;
            }
            const message = error instanceof AskError && error.safeMessage ? error.safeMessage : strings.askFailed;
            say(message + reference, true);
        } finally {
            if (!locked) {
                send.disabled = false;
                question.disabled = false;
            }
        }
    });

    return {
        refreshDocuments,
        /** Start a new conversation: the agent keeps no context from the old one. */
        reset() {
            conversation = newConversationNonce();
        },
        elements: {form, question, send, included, status},
    };
}
