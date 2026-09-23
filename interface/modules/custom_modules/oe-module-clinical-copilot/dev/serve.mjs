/**
 * A static server for the fixture page, on loopback only.
 *
 * The fixture page imports the module's real assets (`../public/assets/w2/`
 * and the vendored PDF.js) and fetches the built fixtures, so all three
 * have to be on one origin: ES modules and the PDF.js worker are both
 * subject to it. Serving the module directory is the whole job.
 *
 * No dependencies, no node_modules: `node:http` and `node:fs` are enough,
 * and adding a dev server to package.json for this would put a supply chain
 * behind a file that never ships.
 *
 *   node interface/modules/custom_modules/oe-module-clinical-copilot/dev/serve.mjs
 *   # -> http://127.0.0.1:8760/dev/fixture.html
 *
 * Bound to 127.0.0.1. The fixtures are synthetic, but a directory server on
 * 0.0.0.0 is a habit worth not having in a repository that also holds a
 * chart embed.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Paawan Desai <paawankdesai@gmail.com>
 * @copyright Copyright (c) 2026 Paawan Desai <paawankdesai@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

import {createReadStream, realpathSync} from 'node:fs';
import {stat} from 'node:fs/promises';
import {createServer} from 'node:http';
import {dirname, extname, join, normalize, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const MODULE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.pdf': 'application/pdf',
    '.map': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
};

/**
 * Resolve a request path inside the module directory, or null.
 *
 * `normalize` collapses `..` before the prefix check, so `/../../etc/passwd`
 * cannot climb out — the same reasoning the gate hook applies to its own
 * paths, for the same reason.
 *
 * @param {string} urlPath
 * @returns {string|null}
 */
export function resolveWithin(urlPath) {
    const decoded = decodeURIComponent(urlPath.split('?')[0]);
    const candidate = resolve(join(MODULE_ROOT, normalize(decoded)));
    return candidate === MODULE_ROOT || candidate.startsWith(MODULE_ROOT + sep) ? candidate : null;
}

export function createFixtureServer() {
    return createServer(async (request, response) => {
        const path = resolveWithin(request.url === '/' ? '/dev/fixture.html' : request.url);
        if (path === null) {
            response.writeHead(403).end('forbidden');
            return;
        }
        try {
            const info = await stat(path);
            if (!info.isFile()) {
                response.writeHead(404).end('not found');
                return;
            }
            response.writeHead(200, {
                'Content-Type': TYPES[extname(path)] || 'application/octet-stream',
                'Content-Length': info.size,
                'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff',
            });
            createReadStream(path).pipe(response);
        } catch {
            response.writeHead(404).end('not found');
        }
    });
}

/**
 * Start on `port`, or on a free port when 0. Resolves with the server.
 *
 * @param {number} [port]
 * @returns {Promise<import('node:http').Server>}
 */
export function listen(port = 8760) {
    const server = createFixtureServer();
    return new Promise((resolvePromise, reject) => {
        server.once('error', reject);
        server.listen(port, '127.0.0.1', () => resolvePromise(server));
    });
}

/**
 * Whether this file is the script node was asked to run, as opposed to an
 * import (`browser.mjs` imports `listen`).
 *
 * Compared as resolved filesystem paths, not as `file://${argv[1]}` against
 * `import.meta.url`: the URL percent-encodes a space and argv does not, so
 * that string comparison is false in any checkout whose path has a space in
 * it, and the server then exits 0 without printing anything. `realpathSync`
 * also folds a relative argv or a symlinked checkout into the path node
 * resolved `import.meta.url` to.
 *
 * @returns {boolean}
 */
function isEntryPoint() {
    if (!process.argv[1]) {
        return false;
    }
    try {
        return fileURLToPath(import.meta.url) === realpathSync(process.argv[1]);
    } catch {
        return false;
    }
}

if (isEntryPoint()) {
    const port = Number(process.argv[2] || 8760);
    const server = await listen(port);
    const {port: bound} = server.address();
    process.stdout.write(`serving ${MODULE_ROOT}\n  http://127.0.0.1:${bound}/dev/fixture.html\n`);
}
