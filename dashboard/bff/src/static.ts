import { readFile, stat } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import type { Context } from 'hono';

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf',
  '.json': 'application/json',
};

/**
 * Serves the built SPA. Files under assets/ carry a content hash in their
 * name and are cached for a year; everything else, index.html included, is
 * revalidated so a deploy is picked up on the next load. Unknown paths
 * without an extension get index.html (client-side routes).
 */
export function staticHandler(root: string) {
  const base = resolve(root);
  return async (c: Context) => {
    const path = decodeURIComponent(new URL(c.req.url).pathname);
    let file = resolve(base, '.' + path);
    if (file !== base && !file.startsWith(base + sep)) return c.text('Not found', 404);
    let isFile = false;
    try {
      isFile = (await stat(file)).isFile();
    } catch {
      /* missing */
    }
    if (!isFile) {
      if (extname(path)) return c.text('Not found', 404);
      file = resolve(base, 'index.html');
    }
    const body = await readFile(file);
    const hashed = file.startsWith(resolve(base, 'assets') + sep);
    return c.body(body, 200, {
      'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream',
      'Cache-Control': hashed ? 'public, max-age=31536000, immutable' : 'no-cache',
    });
  };
}
