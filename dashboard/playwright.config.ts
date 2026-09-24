import { defineConfig } from '@playwright/test';

/**
 * End-to-end against the LOCAL OpenEMR dev stack (docker/development-easy):
 * real OAuth, real FHIR, real PHP dashboard. Needs the stack up, the BFF
 * client registered (README "Run locally"), and `npm run build` first.
 * Uses the installed Chrome, so no browser download.
 */
export default defineConfig({
  testDir: 'test/e2e',
  timeout: 120_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:8787',
    channel: 'chrome',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    ignoreHTTPSErrors: true, // OpenEMR's local self-signed certificate, in the browser only
  },
  webServer: {
    command: 'scripts/run-local.sh',
    url: 'http://localhost:8787/healthz',
    reuseExistingServer: true,
    timeout: 20_000,
  },
});
