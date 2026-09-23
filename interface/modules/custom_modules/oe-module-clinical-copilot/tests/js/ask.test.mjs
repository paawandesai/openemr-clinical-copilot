/**
 * Unit tests for the question box's decisions: the outcome labels, what the
 * module route is sent, and how a refusal from the module surfaces.
 *
 *   node --test .../tests/js/ask.test.mjs
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import assert from 'node:assert/strict';
import {afterEach, describe, it} from 'node:test';

import {
    ANSWER_STATUSES,
    AskError,
    DEFAULT_STRINGS,
    moduleAskSource,
    newConversationNonce,
    statusLabel,
} from '../../public/assets/w2/ask.mjs';

const realFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = realFetch;
});

function respondWith(status, payload, seen) {
    globalThis.fetch = async (url, init) => {
        seen.push({url, init});
        return {ok: status >= 200 && status < 300, status, json: async () => payload};
    };
}

describe('statusLabel', () => {
    it('knows every AskStatus', () => {
        assert.deepEqual(ANSWER_STATUSES, ['answered', 'partially_answered', 'not_in_record', 'refused']);
        const labels = ANSWER_STATUSES.map((status) => statusLabel(status));
        assert.equal(new Set(labels).size, 4);
    });

    it('never labels an unknown outcome as answered', () => {
        assert.equal(statusLabel('answered_probably'), DEFAULT_STRINGS.statusRefused);
    });
});

describe('newConversationNonce', () => {
    it('is what ConversationNonce accepts, and fresh each time', () => {
        const nonce = newConversationNonce();
        assert.match(nonce, /^[A-Za-z0-9-]{8,64}$/);
        assert.notEqual(nonce, newConversationNonce());
    });
});

describe('moduleAskSource', () => {
    const fields = () => ({pid: '49', csrf_token_form: 'tok', correlation_id: '3f2b9c1e-7d4a-4e8b-9f0c-1a2b3c4d5e6f'});

    it('posts the card fields, the conversation, the question and each document id', async () => {
        const seen = [];
        respondWith(200, {status: 'ok', answer_status: 'answered'}, seen);
        const payload = await moduleAskSource({endpoint: '/ask.php', fields}).ask('Is K flagged?', 'nonce-1234', ['a'.repeat(128), 'b'.repeat(128)]);

        assert.equal(payload.answer_status, 'answered');
        assert.equal(seen.length, 1);
        assert.equal(seen[0].url, '/ask.php');
        assert.equal(seen[0].init.method, 'POST');
        assert.equal(seen[0].init.credentials, 'same-origin');
        const body = seen[0].init.body;
        assert.equal(body.get('pid'), '49');
        assert.equal(body.get('csrf_token_form'), 'tok');
        assert.equal(body.get('correlation_id'), '3f2b9c1e-7d4a-4e8b-9f0c-1a2b3c4d5e6f');
        assert.equal(body.get('conversation'), 'nonce-1234');
        assert.equal(body.get('question'), 'Is K flagged?');
        assert.deepEqual(body.getAll('document_ids[]'), ['a'.repeat(128), 'b'.repeat(128)]);
    });

    it('surfaces the module error code, its safe message and the reference', async () => {
        respondWith(409, {
            status: 'error',
            error: 'patient_context_changed',
            message: 'A different patient is now open.',
            correlation_id: 'c-1',
        }, []);
        await assert.rejects(moduleAskSource({endpoint: '/ask.php', fields}).ask('q', 'nonce-1234', []), (error) => {
            assert.ok(error instanceof AskError);
            assert.equal(error.code, 'patient_context_changed');
            assert.equal(error.safeMessage, 'A different patient is now open.');
            assert.equal(error.correlationId, 'c-1');
            return true;
        });
    });

    it('treats a 200 without the ok envelope as a failure, not an answer', async () => {
        respondWith(200, {answer_status: 'answered'}, []);
        await assert.rejects(moduleAskSource({endpoint: '/ask.php', fields}).ask('q', 'nonce-1234', []), AskError);
    });

    it('treats a network failure as the agent being unavailable', async () => {
        globalThis.fetch = async () => {
            throw new TypeError('network');
        };
        await assert.rejects(moduleAskSource({endpoint: '/ask.php', fields}).ask('q', 'nonce-1234', []), (error) => {
            assert.equal(error.code, 'agent_unavailable');
            return true;
        });
    });
});
