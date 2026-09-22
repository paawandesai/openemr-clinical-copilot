/**
 * Unit tests for the upload control's decisions: which files it refuses
 * before sending, which extraction statuses end the poll, and how the poll
 * is paced.
 *
 *   node --test .../tests/js/upload.test.mjs
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
    DOCUMENT_KINDS,
    EXTRACTION_STATUSES,
    MAX_BYTES,
    isTerminal,
    pollDelay,
    rejectFile,
} from '../../public/assets/w2/upload.mjs';

const file = (name, type, size) => ({name, type, size});

describe('document kinds', () => {
    it('offers exactly the two schemas that exist', () => {
        // DocumentKind (agent/app/models/w2/documents.py). A third option
        // here would be an extractor nothing routes to.
        assert.deepEqual(DOCUMENT_KINDS, ['lab_pdf', 'intake_form']);
    });
});

describe('isTerminal — D7', () => {
    it('knows every ExtractionStatus', () => {
        assert.deepEqual(EXTRACTION_STATUSES, ['pending', 'in_progress', 'complete', 'failed']);
    });

    it('keeps polling while a document is still extracting', () => {
        // D7: a question asked against a document that has not finished
        // gets "still processing". Treating `pending` as terminal would
        // make the card claim an extraction that has not started.
        assert.ok(!isTerminal('pending'));
        assert.ok(!isTerminal('in_progress'));
    });

    it('stops on both endings, not only the happy one', () => {
        assert.ok(isTerminal('complete'));
        assert.ok(isTerminal('failed'));
    });

    it('does not treat an unknown status as an ending', () => {
        assert.ok(!isTerminal('nearly'));
    });
});

describe('pollDelay', () => {
    it('starts quickly, because a digital PDF finishes in seconds', () => {
        assert.ok(pollDelay(0) <= 500);
    });

    it('never gets faster as it goes on', () => {
        let previous = 0;
        for (let attempt = 0; attempt < 20; attempt += 1) {
            const delay = pollDelay(attempt);
            assert.ok(delay >= previous, `attempt ${attempt}: ${delay} < ${previous}`);
            previous = delay;
        }
    });

    it('settles at a fixed interval rather than growing without bound', () => {
        assert.equal(pollDelay(50), pollDelay(500));
    });

    it('asks no more than about thirty times in the first two minutes', () => {
        let elapsed = 0;
        let polls = 0;
        while (elapsed < 120000) {
            elapsed += pollDelay(polls);
            polls += 1;
        }
        assert.ok(polls <= 35, `${polls} polls in two minutes`);
    });
});

describe('rejectFile', () => {
    it('accepts a PDF under the size cap', () => {
        assert.equal(rejectFile(file('report.pdf', 'application/pdf', 1024)), null);
    });

    it('accepts a PDF whose type the browser did not fill in', () => {
        // Some browsers give an empty type for a file dragged from certain
        // sources; the extension is the fallback, not the only test.
        assert.equal(rejectFile(file('report.PDF', '', 1024)), null);
    });

    it('refuses something that is not a PDF', () => {
        assert.equal(rejectFile(file('scan.png', 'image/png', 1024)), 'notPdf');
        assert.equal(rejectFile(file('notes.txt', 'text/plain', 10)), 'notPdf');
    });

    it('refuses a file over the cap, and reports the size before the type', () => {
        assert.equal(rejectFile(file('huge.pdf', 'application/pdf', MAX_BYTES + 1)), 'tooLarge');
        assert.equal(rejectFile(file('huge.png', 'image/png', MAX_BYTES + 1)), 'tooLarge');
    });

    it('accepts a file exactly at the cap', () => {
        assert.equal(rejectFile(file('big.pdf', 'application/pdf', MAX_BYTES)), null);
    });

    it('does not guess the document type from the file name', () => {
        // A `.pdf` check is about whether the bytes can be read at all. A
        // name-based guess at lab_pdf vs intake_form would route the file to
        // the wrong extractor and produce a well-formed extraction of the
        // wrong shape, which is worse than refusing.
        assert.equal(rejectFile(file('intake-form.pdf', 'application/pdf', 10)), null);
        assert.equal(rejectFile(file('lab-results.pdf', 'application/pdf', 10)), null);
    });
});
