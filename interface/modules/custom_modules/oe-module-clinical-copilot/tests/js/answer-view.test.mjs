/**
 * Unit tests for the parts of the answer view that are decisions rather
 * than DOM: how a printed flag is labelled (D20), which flags the
 * cross-check compares, and how a claim's citations become chips.
 *
 * Rendering itself is checked in a real browser against all six fixtures by
 * `dev/check-answer-view.mjs`, because what is being asserted there —
 * "these two kinds of claim are in visibly separate sections" — is a
 * property of the page, not of a function.
 *
 *   node --test .../tests/js/answer-view.test.mjs
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import assert from 'node:assert/strict';
import {describe, it} from 'node:test';

import {
    DEFAULT_STRINGS,
    groupCitations,
    isComparableReportedFlag,
    reportedFlagLabel,
} from '../../public/assets/w2/answer-view.mjs';

/** Every `ReportedFlag` member (agent/app/models/w2/lab_values.py). */
const REPORTED_FLAGS = ['normal', 'high', 'low', 'abnormal', 'not_reported', 'unrecognised'];

const documentCitation = (field, page, box, id = 'doc-1') => ({
    source_type: 'document',
    source_id: id,
    page_or_section: String(page),
    field_or_chunk_id: field,
    quote_or_value: 'x',
    bbox: {page, x0: box[0], y0: box[1], x1: box[2], y1: box[3]},
    extraction_id: 'w2x-00000000000000000000000000000000',
});

describe('reportedFlagLabel — D20', () => {
    it('labels every ReportedFlag member', () => {
        for (const flag of REPORTED_FLAGS) {
            const label = reportedFlagLabel(flag);
            assert.ok(label && label !== flag, `${flag} has no label of its own`);
        }
    });

    it('never calls a blank flag column normal', () => {
        // D12 said a blank column asserts normality; D20 supersedes it. An
        // extractor that loses the flag column yields blanks on every row,
        // and under D12 every one of those scored as agreement.
        assert.equal(reportedFlagLabel('not_reported'), DEFAULT_STRINGS.flagNotReported);
        assert.doesNotMatch(reportedFlagLabel('not_reported'), /normal/i);
        assert.doesNotMatch(reportedFlagLabel('unrecognised'), /normal/i);
    });

    it('says "printed" only for the flags a laboratory actually printed', () => {
        for (const flag of ['normal', 'high', 'low', 'abnormal']) {
            assert.match(reportedFlagLabel(flag), /printed/i, `${flag} should say it was printed`);
        }
        for (const flag of ['not_reported', 'unrecognised']) {
            assert.doesNotMatch(reportedFlagLabel(flag), /printed/i);
        }
    });

    it('falls back to the raw value rather than to a label that means something', () => {
        // An enum member this file has not been taught about must look
        // wrong, not look normal.
        assert.equal(reportedFlagLabel('a_flag_from_the_future'), 'a_flag_from_the_future');
    });
});

describe('isComparableReportedFlag — D20', () => {
    it('compares the four flags a laboratory can print, and nothing else', () => {
        assert.deepEqual(
            REPORTED_FLAGS.filter(isComparableReportedFlag),
            ['normal', 'high', 'low', 'abnormal']
        );
    });

    it('excludes both ways of saying nothing', () => {
        assert.ok(!isComparableReportedFlag('not_reported'));
        assert.ok(!isComparableReportedFlag('unrecognised'));
    });
});

describe('groupCitations', () => {
    it('gives one chip per field, in citation order', () => {
        const groups = groupCitations([
            documentCitation('results.cmp.glucose.value', 1, [10, 20, 40, 32]),
            documentCitation('results.cmp.glucose.unit', 1, [50, 20, 80, 32]),
            documentCitation('results.cmp.glucose.flag', 1, [306, 20, 306, 32]),
        ]);
        assert.deepEqual(groups.map((g) => g.field), [
            'results.cmp.glucose.value',
            'results.cmp.glucose.unit',
            'results.cmp.glucose.flag',
        ]);
        assert.ok(groups.every((g) => g.citations.length === 1));
    });

    it('keeps a wrapped field as one chip holding every line box', () => {
        // A field that wraps arrives as one located citation per line box —
        // `Citation.bbox` is a single rectangle, so two lines cannot be one
        // citation without claiming the whitespace between them.
        const groups = groupCitations([
            documentCitation('patient.address', 1, [130, 200, 310, 212]),
            documentCitation('patient.address', 1, [130, 214, 226, 226]),
        ]);
        assert.equal(groups.length, 1);
        assert.equal(groups[0].citations.length, 2);
        assert.deepEqual(groups[0].citations.map((c) => c.bbox.y0), [200, 214]);
    });

    it('does not merge the same field across two documents', () => {
        const groups = groupCitations([
            documentCitation('patient.name', 1, [0, 0, 10, 10], 'doc-1'),
            documentCitation('patient.name', 1, [0, 0, 10, 10], 'doc-2'),
        ]);
        assert.equal(groups.length, 2);
        assert.deepEqual(groups.map((g) => g.sourceId), ['doc-1', 'doc-2']);
    });

    it('does not merge a chart citation with a document citation', () => {
        // Invariant 3 and D17: the same field name on two source types is
        // two different claims about where a value came from.
        const groups = groupCitations([
            {
                source_type: 'chart',
                source_id: 'fixture-chart:x:AllergyIntolerance:0',
                page_or_section: 'AllergyIntolerance',
                field_or_chunk_id: 'code',
                quote_or_value: 'Fish (substance)',
            },
            documentCitation('code', 1, [0, 0, 10, 10]),
        ]);
        assert.equal(groups.length, 2);
        assert.deepEqual(groups.map((g) => g.sourceType).sort(), ['chart', 'document']);
    });

    it('is empty for no citations', () => {
        assert.deepEqual(groupCitations([]), []);
    });
});
