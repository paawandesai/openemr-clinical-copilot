import { test } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { openDashboard, OPENEMR_WEB, PATIENTS, phpContext, waitForCards } from './helpers';

/**
 * Load-time comparison, PHP vs port, same machine, same data, same moment.
 * Not a check; run with PERF=1 npm run test:e2e -- perf. Writes
 * docs/dashboard/perf.json. Each run is a fresh page in an already
 * signed-in context, after two warm-up loads, so both sides are measured
 * warm (PHP opcache, browser cache for the SPA's hashed assets).
 */
test.skip(!process.env.PERF, 'set PERF=1 to measure');

const RUNS = 7;
const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]!;

interface Sample {
  serverMs: number;
  cardsVisibleMs: number;
  bytes: number;
  requests: number;
}

test('PHP dashboard vs React port: warm load', async ({ browser, page }) => {
  test.setTimeout(600_000);
  const results: Record<string, { php: Sample[]; port: Sample[] }> = {};
  await openDashboard(page, PATIENTS[0].uuid); // sign the port's context in
  const php = await phpContext(browser);

  for (const p of PATIENTS.slice(0, 2)) {
    results[p.pid] = { php: [], port: [] };
    for (let i = 0; i < RUNS + 2; i++) {
      // PHP: the clinical cards are in the server-rendered HTML, so they are
      // visible at DOMContentLoaded. The Co-Pilot cards' own AJAX is not counted.
      const pg = await php.newPage();
      await pg.goto(`${OPENEMR_WEB}/interface/patient_file/summary/demographics.php?set_pid=${p.pid}`, { waitUntil: 'load' });
      const ps = await pg.evaluate(() => {
        const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
        const res = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
        return {
          serverMs: nav.responseEnd - nav.requestStart,
          cardsVisibleMs: nav.domContentLoadedEventEnd - nav.startTime,
          bytes: nav.transferSize + res.reduce((a, r) => a + r.transferSize, 0),
          requests: 1 + res.length,
        };
      });
      await pg.close();

      // Port: from navigation start until every card has settled.
      const sp = await page.context().newPage();
      const t0 = Date.now();
      await sp.goto(`/patient/${p.uuid}`);
      await waitForCards(sp);
      const settled = Date.now() - t0;
      const ss = await sp.evaluate(() => {
        const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
        const res = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
        const api = res.filter((r) => new URL(r.name).pathname.startsWith('/fhir/'));
        return {
          serverMs: api.length ? Math.max(...api.map((r) => r.responseEnd - r.requestStart)) : 0,
          bytes: nav.transferSize + res.reduce((a, r) => a + r.transferSize, 0),
          requests: 1 + res.length,
        };
      });
      await sp.close();
      if (i >= 2) {
        results[p.pid]!.php.push(ps);
        results[p.pid]!.port.push({ ...ss, cardsVisibleMs: settled });
      }
    }
  }
  await php.close();

  const summary = Object.fromEntries(
    Object.entries(results).map(([pid, r]) => [
      pid,
      Object.fromEntries(
        (['php', 'port'] as const).map((side) => [
          side,
          {
            medianServerMs: Math.round(median(r[side].map((s) => s.serverMs))),
            medianCardsVisibleMs: Math.round(median(r[side].map((s) => s.cardsVisibleMs))),
            minCardsVisibleMs: Math.round(Math.min(...r[side].map((s) => s.cardsVisibleMs))),
            maxCardsVisibleMs: Math.round(Math.max(...r[side].map((s) => s.cardsVisibleMs))),
            medianTransferBytes: median(r[side].map((s) => s.bytes)),
            medianRequests: median(r[side].map((s) => s.requests)),
          },
        ]),
      ),
    ]),
  );
  const out = {
    measuredAt: new Date().toISOString(),
    runs: RUNS,
    warmups: 2,
    environment: 'MacBook, local development-easy stack (Colima), headless Chrome, warm caches',
    definitions: {
      medianServerMs: 'PHP: demographics.php request start to response end. Port: the slowest of the parallel /fhir reads (BFF + OpenEMR FHIR).',
      medianCardsVisibleMs: 'PHP: navigation start to DOMContentLoaded (cards are server-rendered). Port: navigation start to every card settled (polled every 100 ms).',
      medianTransferBytes: 'Bytes over the wire for the page and everything it loaded (0 for a cache hit).',
    },
    summary,
    samples: results,
  };
  writeFileSync(resolve(process.cwd(), '../docs/dashboard/perf.json'), JSON.stringify(out, null, 1) + '\n');
  console.log(JSON.stringify(summary, null, 1));
});
