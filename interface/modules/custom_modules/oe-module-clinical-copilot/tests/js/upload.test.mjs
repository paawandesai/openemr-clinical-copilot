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
    describeStatus,
    isIdentityMismatch,
    isTerminal,
    isUsable,
    pollDelay,
    reconcileMessage,
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

describe('a document for another patient — D60', () => {
    const complete = {extraction_status: 'complete', document_id: 'd'.repeat(128), correlation_id: 'c-1'};

    it('is never usable in a question, and a confirmed or unknown one is', () => {
        assert.ok(isIdentityMismatch({...complete, identity_check: 'mismatch'}));
        assert.ok(!isUsable({...complete, identity_check: 'mismatch'}));
        assert.ok(isUsable({...complete, identity_check: 'confirmed'}));
        assert.ok(isUsable({...complete, identity_check: 'not_confirmed'}));
        // An older module that sends no verdict: as before D60.
        assert.ok(isUsable(complete));
        assert.ok(!isUsable({extraction_status: 'failed', failure_reason: 'timed_out'}));
    });

    it('says it was not used, and where it went', () => {
        const text = describeStatus({...complete, identity_check: 'mismatch', quarantine: 'quarantined'});
        assert.match(text, /^Extraction complete — Not used: the name or date of birth/);
        assert.match(text, /Quarantine - Patient Mismatch/);
        assert.match(text, /not deleted/);
    });

    it('never implies it was set aside when the move failed or was not reported', () => {
        for (const quarantine of ['failed', undefined, 'something-new']) {
            const text = describeStatus({...complete, identity_check: 'mismatch', quarantine});
            assert.match(text, /could NOT be moved to quarantine/, String(quarantine));
        }
    });

    it('says nothing about identity for a document that matched', () => {
        const text = describeStatus({...complete, identity_check: 'confirmed'});
        assert.equal(text, 'Extraction complete (Reference: c-1)');
    });
});

describe('an interrupted extraction — D60', () => {
    it('tells the reader to upload again', () => {
        const text = describeStatus({extraction_status: 'failed', failure_reason: 'interrupted'});
        assert.match(text, /restarted before it finished; upload the file again/);
    });
});

describe('reconciling on card load — D60', () => {
    it('says nothing when nothing was moved and nothing failed', () => {
        assert.equal(reconcileMessage({checked: 3, quarantined: 0, failed: 0}), null);
        assert.equal(reconcileMessage(null), null);
    });

    it('says how many documents were moved, and that none was deleted', () => {
        const one = reconcileMessage({quarantined: 1, failed: 0});
        assert.match(one.text, /^1 document on this chart was moved to the “Quarantine - Patient Mismatch” category/);
        assert.match(one.text, /It was not deleted\.$/);
        assert.equal(one.isError, false);
        const three = reconcileMessage({quarantined: 3, failed: 0});
        assert.match(three.text, /^3 documents on this chart were moved/);
    });

    it('says so, as an error, when a mismatched document could not be moved', () => {
        const message = reconcileMessage({quarantined: 0, failed: 1});
        assert.match(message.text, /could NOT be moved to quarantine/);
        assert.equal(message.isError, true);
    });
});
