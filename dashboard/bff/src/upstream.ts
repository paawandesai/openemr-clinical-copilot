import { request } from 'node:https';
import { readFileSync } from 'node:fs';
import type { FetchLike } from './oauth.js';

/**
 * The fetch the BFF uses to reach OpenEMR.
 *
 * By default, Node's own fetch: the upstream certificate is verified against
 * the system trust store plus NODE_EXTRA_CA_CERTS.
 *
 * With OPENEMR_TLS_SERVERNAME set, requests go to the configured host but the
 * certificate is verified for that name, against OPENEMR_CA_FILE only. It
 * exists for one deployment shape: OpenEMR's self-signed certificate says
 * CN=localhost (audit F-36), and from inside a container OpenEMR is reached
 * as host.docker.internal. The agent handles that by switching verification
 * off; this keeps it on, pinned to the one certificate. The lasting fix is a
 * real certificate on OpenEMR, after which neither variable is needed.
 */
export function upstreamFetch(env: NodeJS.ProcessEnv = process.env): FetchLike {
  const servername = env.OPENEMR_TLS_SERVERNAME?.trim();
  if (!servername) return (input, init) => fetch(input, init);
  const caFile = env.OPENEMR_CA_FILE?.trim();
  if (!caFile) throw new Error('OPENEMR_TLS_SERVERNAME needs OPENEMR_CA_FILE (the certificate to pin)');
  const ca = readFileSync(caFile);

  return (input, init = {}) =>
    new Promise<Response>((resolve, reject) => {
      const url = new URL(input);
      if (url.protocol !== 'https:') return reject(new Error('upstream must be https'));
      const headers = new Headers(init.headers);
      const body = init.body === undefined || init.body === null ? undefined : String(init.body);
      if (body !== undefined && init.body instanceof URLSearchParams && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/x-www-form-urlencoded');
      }
      const req = request(
        {
          protocol: 'https:',
          hostname: url.hostname,
          port: url.port || 443,
          path: url.pathname + url.search,
          method: init.method ?? 'GET',
          headers: Object.fromEntries(headers),
          servername,
          ca,
          rejectUnauthorized: true,
          signal: init.signal ?? undefined,
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on('data', (c: Buffer) => chunks.push(c));
          res.on('end', () => {
            const h = new Headers();
            for (const [k, v] of Object.entries(res.headers)) if (typeof v === 'string') h.set(k, v);
            resolve(new Response(Buffer.concat(chunks), { status: res.statusCode ?? 502, headers: h }));
          });
          res.on('error', reject);
        },
      );
      // An aborted request rejects with the signal's reason, so a timeout stays a TimeoutError (-> 504).
      req.on('error', (e) => reject(init.signal?.aborted ? init.signal.reason : e));
      if (body !== undefined) req.write(body);
      req.end();
    });
}
