/**
 * A dependency-free driver for an installed Chrome, over the DevTools
 * protocol, for the two fixture checks in this directory.
 *
 * Node has had a global `WebSocket` since 22, and Chrome speaks CDP over
 * one, so the whole of a headless-browser harness is the ~80 lines below.
 * The alternative was adding Puppeteer to a repository whose front end has
 * no test runner installed at all — a browser download and a dependency
 * tree, to send four commands.
 *
 * Chrome is launched with a throwaway `--user-data-dir` under the system
 * temp directory, so it never touches the profile of whoever is running the
 * check, and the directory is removed on close.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {listen} from './serve.mjs';

export const CHROME_CANDIDATES = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
];

/** The first Chrome-like binary that exists, or null. */
export function findChrome(override = null) {
    return override || CHROME_CANDIDATES.find((path) => existsSync(path)) || null;
}

class Devtools {
    constructor(socket) {
        this.socket = socket;
        this.id = 0;
        this.pending = new Map();
        socket.addEventListener('message', (event) => {
            const message = JSON.parse(event.data);
            const waiting = this.pending.get(message.id);
            if (waiting) {
                this.pending.delete(message.id);
                if (message.error) {
                    waiting.reject(new Error(message.error.message));
                } else {
                    waiting.resolve(message.result);
                }
            }
        });
    }

    static async connect(url) {
        const socket = new WebSocket(url);
        await new Promise((resolve, reject) => {
            socket.addEventListener('open', resolve, {once: true});
            socket.addEventListener('error', () => reject(new Error(`cannot reach ${url}`)), {once: true});
        });
        return new Devtools(socket);
    }

    send(method, params = {}, sessionId) {
        const id = ++this.id;
        this.socket.send(JSON.stringify({id, method, params, sessionId}));
        return new Promise((resolve, reject) => this.pending.set(id, {resolve, reject}));
    }

    close() {
        this.socket.close();
    }
}

async function launchChrome(binary) {
    const profile = await mkdtemp(join(tmpdir(), 'ccw2-chrome-'));
    const child = spawn(
        binary,
        [
            '--headless=new',
            '--disable-gpu',
            '--hide-scrollbars',
            '--remote-debugging-port=0',
            `--user-data-dir=${profile}`,
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            '--disable-background-networking',
            'about:blank',
        ],
        {stdio: ['ignore', 'ignore', 'pipe']}
    );

    const endpoint = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Chrome did not report a DevTools endpoint')), 30000);
        let buffered = '';
        child.stderr.on('data', (chunk) => {
            buffered += chunk.toString();
            const found = buffered.match(/DevTools listening on (ws:\/\/\S+)/);
            if (found) {
                clearTimeout(timer);
                resolve(found[1]);
            }
        });
        child.once('exit', (code) => {
            clearTimeout(timer);
            reject(new Error(`Chrome exited with ${code}`));
        });
    });

    return {child, endpoint, profile};
}

/**
 * Serve the module, open the fixture page in headless Chrome, and hand back
 * an `evaluate()` bound to it plus a `close()` that tears everything down.
 *
 * @param {string|null} chromePath
 * @returns {Promise<{evaluate: (expression: string) => Promise<any>, close: () => Promise<void>}>}
 */
export async function openFixturePage(chromePath = null) {
    const chrome = findChrome(chromePath);
    if (!chrome) {
        throw new Error('No Chrome or Chromium found. Pass --chrome <path>.');
    }
    const server = await listen(0);
    const {port} = server.address();
    const browser = await launchChrome(chrome);
    const cdp = await Devtools.connect(browser.endpoint);

    const close = async () => {
        cdp.close();
        browser.child.kill();
        await rm(browser.profile, {recursive: true, force: true}).catch(() => {});
        server.close();
    };

    try {
        const {targetId} = await cdp.send('Target.createTarget', {url: 'about:blank'});
        const {sessionId} = await cdp.send('Target.attachToTarget', {targetId, flatten: true});
        await cdp.send('Page.enable', {}, sessionId);
        await cdp.send('Runtime.enable', {}, sessionId);
        await cdp.send('Page.navigate', {url: `http://127.0.0.1:${port}/dev/fixture.html`}, sessionId);

        const evaluate = async (expression) => {
            const result = await cdp.send(
                'Runtime.evaluate',
                {expression, awaitPromise: true, returnByValue: true},
                sessionId
            );
            if (result.exceptionDetails) {
                throw new Error(result.exceptionDetails.exception?.description || 'evaluation failed');
            }
            return result.result.value;
        };

        // The page is a module and builds itself asynchronously; there is no
        // load event that means "the fixture is mounted".
        await evaluate(`new Promise((resolve, reject) => {
            const deadline = Date.now() + 30000;
            (function wait() {
                if (window.ccw2Fixture) { resolve(true); return; }
                if (Date.now() > deadline) { reject(new Error('fixture page never became ready')); return; }
                setTimeout(wait, 50);
            })();
        })`);

        return {evaluate, close};
    } catch (error) {
        await close();
        throw error;
    }
}
