/**
 * Clinical Co-Pilot: the source-grounded answer view.
 *
 * Renders an `AnswerView` (agent/app/models/w2/answer_view.py). Everything
 * is inserted as text, never as HTML — the same rule the Week 1 card holds
 * to, and it matters more here because some of this text came out of an
 * uploaded PDF.
 *
 * THE THREE RULES THIS FILE EXISTS TO KEEP
 *
 * 1. **Patient facts and guideline evidence are visibly separate**
 *    (invariant 3). Not a badge on a shared list: separate sections, with
 *    headings that say which is which, and the guideline heading says it is
 *    not about this patient. `PatientFactClaim` and `GuidelineClaim` arrive
 *    in separate lists and are never merged here.
 *
 *    Patient facts are split again by source type (D17) — the chart, and
 *    the uploaded document — because D2 and invariant 5 turn on a reader
 *    being able to see that an intake form's medication list is the *form's*
 *    list and has not been merged into the record.
 *
 * 2. **Unsupported values are never shown as facts** (invariant 4). They
 *    are rendered from `answer.unsupported`, in their own section, with the
 *    reason. `UnsupportedField` has no `value` field for this code to reach
 *    for, and what the model read — `unverified_value`, present only for
 *    `not_located_on_page` — is labelled as unverified where it appears.
 *
 * 3. **A blank flag column reports nothing** (D20). `reportedFlagLabel`
 *    has a case for every `ReportedFlag` member, `not_reported` included,
 *    and there is no default branch that could quietly turn an unfamiliar
 *    value into "Normal". The flag table always shows the printed flag and
 *    the computed one side by side with the cross-check outcome, so
 *    "in range, nothing printed" cannot read as "the laboratory called it
 *    normal".
 *
 * NO PHI LEAVES THIS FILE. Nothing here logs, and the one error path
 * reports a code and a correlation id. A chart value passed to
 * `console.log` is a chart value in a place no audit log knows about.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

/** The `AnswerView` shape this file knows how to draw. */
export const ANSWER_VIEW_SCHEMA_VERSION = 'w2-ui/answer-view/1.0.0';

/**
 * English fallbacks. OpenEMR passes translated strings in from the Twig
 * template; the fixture page uses these as they are.
 */
export const DEFAULT_STRINGS = {
    chartHeading: 'Patient record — read from this chart',
    chartEmpty: 'No chart facts in this answer.',
    documentHeading: 'From the uploaded document — not merged into the chart',
    documentNote:
        'These are what the document says. Medications and allergies on an intake form are never merged into the chart; where they differ from the record, both are shown for you to resolve.',
    documentEmpty: 'No document facts in this answer.',
    guidelineHeading: 'Guideline evidence — not about this patient',
    guidelineNote: 'General guidance from a named source. It states nothing about the patient in front of you.',
    guidelineEmpty: 'No guideline evidence in this answer.',
    unsupportedHeading: 'Not supported by the document',
    unsupportedNote:
        'Values that could not be located on their page. These are disclosed rather than dropped, and none of them is a fact.',
    flagsHeading: 'Printed flags, checked against the values',
    flagsNote:
        'A blank flag column reports nothing; it is never read as normal. The check runs only where the laboratory actually printed a comparable flag.',
    columnTest: 'Test',
    columnReported: 'Printed by the laboratory',
    columnComputed: 'Computed from value and range',
    columnOutcome: 'Check',
    comparableCount: '%comparable% of %rows% rows carried a comparable printed flag',
    reasonNotLocated: 'read by the model, not found on the page',
    reasonNotPresent: 'the document does not carry this field',
    reasonIllegible: 'present as ink, not readable',
    unverifiedValue: 'Unverified reading:',
    flagNormal: 'Normal (printed)',
    flagHigh: 'High (printed)',
    flagLow: 'Low (printed)',
    flagAbnormal: 'Abnormal (printed)',
    flagNotReported: 'Not reported',
    flagUnrecognised: 'Unrecognised glyph',
    computedNormal: 'normal',
    computedHigh: 'high',
    computedLow: 'low',
    computedAbnormal: 'abnormal',
    computedIndeterminate: 'indeterminate',
    outcomeAgree: 'agrees',
    outcomeDisagree: 'DISAGREES',
    outcomeNotReported: 'nothing printed to compare',
    outcomeNotComputable: 'nothing to compute from',
    page: 'p.',
    showSnippet: 'Show the passage',
    hideSnippet: 'Hide the passage',
    retrievedBy: 'Retrieved by',
    unsupportedSchema: 'This answer is in a format this page cannot draw. Nothing is shown rather than something in the wrong place.',
};

/**
 * Every `ReportedFlag` member, and no default.
 *
 * D20 in one object. `not_reported` and `unrecognised` are the two ways a
 * flag column says nothing comparable, and each gets a label that says so.
 * A member missing from here renders as the raw enum value, which is ugly
 * and honest; falling through to "Normal" is neither.
 */
const REPORTED_FLAG_LABELS = {
    normal: 'flagNormal',
    high: 'flagHigh',
    low: 'flagLow',
    abnormal: 'flagAbnormal',
    not_reported: 'flagNotReported',
    unrecognised: 'flagUnrecognised',
};

/** `ReportedFlag` members `cross_check_abnormal_flag` will compare (D20). */
const COMPARABLE_REPORTED_FLAGS = ['normal', 'high', 'low', 'abnormal'];

const COMPUTED_FLAG_LABELS = {
    normal: 'computedNormal',
    high: 'computedHigh',
    low: 'computedLow',
    abnormal: 'computedAbnormal',
    indeterminate: 'computedIndeterminate',
};

const OUTCOME_LABELS = {
    agree: 'outcomeAgree',
    disagree: 'outcomeDisagree',
    not_reported: 'outcomeNotReported',
    not_computable: 'outcomeNotComputable',
};

const UNSUPPORTED_REASON_LABELS = {
    not_located_on_page: 'reasonNotLocated',
    not_present_in_document: 'reasonNotPresent',
    illegible_on_page: 'reasonIllegible',
};

/**
 * What a printed flag is called on screen.
 *
 * @param {string} flag A `ReportedFlag` value.
 * @param {object} [strings]
 * @returns {string}
 */
export function reportedFlagLabel(flag, strings = DEFAULT_STRINGS) {
    const key = REPORTED_FLAG_LABELS[flag];
    return key ? strings[key] : String(flag);
}

/**
 * Is this printed flag one the cross-check will compare (D20)?
 *
 * Exported because the view uses it twice — to style a row and to count the
 * denominator — and because "comparable" having one definition is the point
 * of the decision.
 *
 * @param {string} flag A `ReportedFlag` value.
 * @returns {boolean}
 */
export function isComparableReportedFlag(flag) {
    return COMPARABLE_REPORTED_FLAGS.includes(flag);
}

/**
 * A claim's citations, grouped into the chips a reader clicks.
 *
 * One chip per (source type, source id, field or chunk) — so a field that
 * wrapped across lines, and therefore arrived as one located citation per
 * line box, is a single chip that highlights every one of its boxes. Order
 * is the order the citations arrived in, which is the order the extractor
 * or retriever put them in.
 *
 * @param {Array<object>} citations
 * @returns {Array<{key: string, sourceType: string, sourceId: string, field: string, citations: Array<object>}>}
 */
export function groupCitations(citations) {
    const groups = new Map();
    for (const citation of citations) {
        const key = [citation.source_type, citation.source_id, citation.field_or_chunk_id].join('\u0000');
        let group = groups.get(key);
        if (!group) {
            group = {
                key,
                sourceType: citation.source_type,
                sourceId: citation.source_id,
                field: citation.field_or_chunk_id,
                citations: [],
            };
            groups.set(key, group);
        }
        group.citations.push(citation);
    }
    return [...groups.values()];
}

// --- DOM helpers -------------------------------------------------------------

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

function section(className, heading, note) {
    const element = node('section', `ccw2-section ${className}`);
    element.appendChild(node('h4', 'ccw2-section-heading', heading));
    if (note) {
        element.appendChild(node('p', 'ccw2-section-note', note));
    }
    return element;
}

// --- chips -------------------------------------------------------------------

/**
 * The label on a chip: what the reader needs to know before clicking it.
 *
 * A document chip names the page and the field, because that is where the
 * click is going. A chart chip names the record — resource type, field and
 * the id the fact was read from (D14 puts the resource type in
 * `page_or_section`). A guideline chip names the guideline and the section,
 * never the patient.
 */
function chipLabel(group, view, strings) {
    if (group.sourceType === 'document') {
        const page = group.citations[0].bbox?.page ?? group.citations[0].page_or_section;
        const boxes = group.citations.length > 1 ? ` (${group.citations.length})` : '';
        return `${strings.page}${page} · ${group.field}${boxes}`;
    }
    if (group.sourceType === 'guideline') {
        const chunk = (view.evidence_chunks || []).find((c) => c.chunk_id === group.field);
        const title = chunk ? chunk.source.title : group.sourceId;
        return `${title} · ${group.citations[0].page_or_section}`;
    }
    return `${group.citations[0].page_or_section} · ${group.field} · ${group.sourceId}`;
}

function chip(group, view, strings, onCite) {
    const button = node('button', `ccw2-chip ccw2-chip--${group.sourceType}`);
    button.type = 'button';
    button.appendChild(node('span', 'ccw2-chip-kind', group.sourceType));
    button.appendChild(node('span', 'ccw2-chip-label', chipLabel(group, view, strings)));
    button.dataset.sourceType = group.sourceType;
    button.dataset.sourceId = group.sourceId;
    button.dataset.field = group.field;
    if (group.sourceType === 'document') {
        button.addEventListener('click', () => onCite(group));
    } else {
        // A chart or guideline chip has no page to scroll to. It toggles its
        // own detail instead, so every chip does something when clicked and
        // none of them pretends to open a document.
        button.addEventListener('click', () => {
            const detail = button.nextElementSibling;
            if (detail && detail.classList.contains('ccw2-chip-detail')) {
                const shown = detail.hidden;
                detail.hidden = !shown;
                button.setAttribute('aria-expanded', String(shown));
            }
        });
        button.setAttribute('aria-expanded', 'false');
    }
    return button;
}

/**
 * What a non-document chip reveals: the record reference for a chart fact,
 * and the snippet plus its source metadata for a guideline one.
 */
function chipDetail(group, view, strings) {
    const detail = node('div', 'ccw2-chip-detail');
    detail.hidden = true;
    if (group.sourceType === 'guideline') {
        const chunk = (view.evidence_chunks || []).find((c) => c.chunk_id === group.field);
        detail.appendChild(node('blockquote', 'ccw2-snippet', group.citations[0].quote_or_value));
        if (chunk) {
            const source = chunk.source;
            const meta = [source.title, source.section, source.publisher, source.version && `v${source.version}`]
                .filter(Boolean)
                .join(' · ');
            detail.appendChild(node('div', 'ccw2-chip-meta', meta));
            detail.appendChild(
                node('div', 'ccw2-chip-meta', `${strings.retrievedBy} ${chunk.method} · ${chunk.chunk_id}`)
            );
            if (source.url) {
                const link = node('a', 'ccw2-chip-meta', source.url);
                link.href = source.url;
                link.rel = 'noreferrer noopener';
                link.target = '_blank';
                detail.appendChild(link);
            }
        }
        return detail;
    }
    const citation = group.citations[0];
    detail.appendChild(
        node('div', 'ccw2-chip-meta', `${citation.page_or_section} · ${citation.field_or_chunk_id}`)
    );
    detail.appendChild(node('div', 'ccw2-chip-meta ccw2-mono', citation.source_id));
    if (citation.quote_or_value !== null && citation.quote_or_value !== undefined) {
        detail.appendChild(node('blockquote', 'ccw2-snippet', citation.quote_or_value));
    }
    return detail;
}

function claimItem(claim, view, strings, onCite) {
    const item = node('li', 'ccw2-claim');
    item.appendChild(node('p', 'ccw2-claim-statement', claim.statement));
    const chips = node('div', 'ccw2-chips');
    for (const group of groupCitations(claim.citations)) {
        chips.appendChild(chip(group, view, strings, onCite));
        if (group.sourceType !== 'document') {
            chips.appendChild(chipDetail(group, view, strings));
        }
    }
    item.appendChild(chips);
    return item;
}

function claimList(claims, view, strings, onCite, emptyText) {
    if (!claims.length) {
        return node('p', 'ccw2-empty', emptyText);
    }
    const list = node('ul', 'ccw2-claims');
    for (const claim of claims) {
        list.appendChild(claimItem(claim, view, strings, onCite));
    }
    return list;
}

// --- sections ----------------------------------------------------------------

function unsupportedSection(fields, strings, onCite) {
    const element = section('ccw2-section--unsupported', strings.unsupportedHeading, strings.unsupportedNote);
    const list = node('ul', 'ccw2-claims');
    for (const field of fields) {
        const item = node('li', 'ccw2-claim ccw2-claim--unsupported');
        const reasonKey = UNSUPPORTED_REASON_LABELS[field.reason];
        item.appendChild(node('p', 'ccw2-claim-statement ccw2-mono', field.field_path));
        item.appendChild(node('p', 'ccw2-unsupported-reason', reasonKey ? strings[reasonKey] : field.reason));
        // Shown, and shown as a failure: `unverified_value` is populated for
        // `not_located_on_page` alone, and never stands on its own line
        // without the word that says nobody confirmed it.
        if (field.unverified_value !== null && field.unverified_value !== undefined) {
            const line = node('p', 'ccw2-unverified');
            line.appendChild(node('span', 'ccw2-unverified-label', strings.unverifiedValue));
            line.appendChild(node('span', '', ` ${field.unverified_value}`));
            item.appendChild(line);
        }
        // An illegible field has a bbox: the ink is there, it could not be
        // read. That box is worth showing, and it is not a located value.
        if (field.citation && field.citation.bbox) {
            const chips = node('div', 'ccw2-chips');
            const group = groupCitations([field.citation])[0];
            chips.appendChild(chip(group, {evidence_chunks: []}, strings, onCite));
            item.appendChild(chips);
        }
        list.appendChild(item);
    }
    element.appendChild(list);
    return element;
}

function flagsSection(flags, strings, onCite) {
    const element = section('ccw2-section--flags', strings.flagsHeading, strings.flagsNote);
    const table = node('table', 'ccw2-flags');
    const head = node('tr');
    for (const key of ['columnTest', 'columnReported', 'columnComputed', 'columnOutcome']) {
        head.appendChild(node('th', '', strings[key]));
    }
    table.appendChild(node('thead')).appendChild(head);
    const body = node('tbody');

    let comparable = 0;
    for (const flag of flags) {
        const check = flag.cross_check;
        const printed = isComparableReportedFlag(check.reported);
        if (printed) {
            comparable += 1;
        }
        const row = node('tr', printed ? 'ccw2-flag-row--printed' : 'ccw2-flag-row--silent');

        const test = node('td');
        if (flag.citation && flag.citation.bbox) {
            const button = node('button', 'ccw2-linklike', flag.test_name);
            button.type = 'button';
            button.addEventListener('click', () => onCite(groupCitations([flag.citation])[0]));
            test.appendChild(button);
        } else {
            test.appendChild(node('span', '', flag.test_name));
        }
        if (flag.panel) {
            test.appendChild(node('span', 'ccw2-panel', ` ${flag.panel}`));
        }
        row.appendChild(test);

        row.appendChild(
            node('td', `ccw2-reported ccw2-reported--${check.reported}`, reportedFlagLabel(check.reported, strings))
        );
        const computedKey = COMPUTED_FLAG_LABELS[check.computed];
        row.appendChild(node('td', 'ccw2-computed', computedKey ? strings[computedKey] : check.computed));
        const outcomeKey = OUTCOME_LABELS[check.outcome];
        row.appendChild(
            node('td', `ccw2-outcome ccw2-outcome--${check.outcome}`, outcomeKey ? strings[outcomeKey] : check.outcome)
        );
        body.appendChild(row);
    }
    table.appendChild(body);
    element.appendChild(table);
    // The denominator, next to the count — D20's "a category scoring 100% on
    // three rows is not the same claim as one scoring 100% on three hundred".
    element.appendChild(
        node('p', 'ccw2-section-note', fill(strings.comparableCount, {comparable, rows: flags.length}))
    );
    return element;
}

// --- entry point -------------------------------------------------------------

/**
 * Draw an `AnswerView` into `container`.
 *
 * @param {HTMLElement} container Emptied first.
 * @param {object} view An `AnswerView`, already parsed.
 * @param {object} options
 * @param {object} [options.strings] Translated labels; see DEFAULT_STRINGS.
 * @param {(group: object) => void} [options.onCite] Called with a document
 *     citation group when a chip asking to see the source is activated.
 * @returns {{sections: string[]}} What was drawn, for tests.
 */
export function renderAnswerView(container, view, {strings: given = {}, onCite = () => {}} = {}) {
    const strings = {...DEFAULT_STRINGS, ...given};
    container.replaceChildren();

    // A view this page does not understand is not drawn at all. Drawing the
    // parts that happen to parse would put boxes on a page using rules from
    // a different version of the contract.
    if (view.schema_version !== ANSWER_VIEW_SCHEMA_VERSION) {
        container.appendChild(node('p', 'ccw2-error', strings.unsupportedSchema));
        return {sections: []};
    }

    const answer = view.answer;
    const drawn = [];
    container.appendChild(node('p', 'ccw2-prose', answer.answer));

    const chartFacts = answer.patient_facts.filter((claim) => claim.citations[0].source_type === 'chart');
    const documentFacts = answer.patient_facts.filter((claim) => claim.citations[0].source_type === 'document');

    const chart = section('ccw2-section--chart', strings.chartHeading);
    chart.appendChild(claimList(chartFacts, view, strings, onCite, strings.chartEmpty));
    container.appendChild(chart);
    drawn.push('chart');

    const document_ = section('ccw2-section--document', strings.documentHeading, strings.documentNote);
    document_.appendChild(claimList(documentFacts, view, strings, onCite, strings.documentEmpty));
    container.appendChild(document_);
    drawn.push('document');

    const guideline = section('ccw2-section--guideline', strings.guidelineHeading, strings.guidelineNote);
    guideline.appendChild(
        claimList(answer.guideline_evidence, view, strings, onCite, strings.guidelineEmpty)
    );
    container.appendChild(guideline);
    drawn.push('guideline');

    if (answer.unsupported.length) {
        container.appendChild(unsupportedSection(answer.unsupported, strings, onCite));
        drawn.push('unsupported');
    }
    if ((view.lab_flags || []).length) {
        container.appendChild(flagsSection(view.lab_flags, strings, onCite));
        drawn.push('flags');
    }
    return {sections: drawn};
}
