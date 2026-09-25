import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { authorizeUrl, checkIdToken, exchangeCode, grantedScopes, refreshTokens } from '../../bff/src/oauth';
import { DEFAULT_SCOPES, type Config } from '../../bff/src/config';
import { SessionStore } from '../../bff/src/sessions';
import { USER } from './helpers';

/**
 * The BFF's own OAuth functions against the real OpenEMR token endpoint:
 * code + PKCE + secret, then a refresh-token grant. Unit tests cover the
 * BFF's handling with a fake; this proves OpenEMR accepts the requests.
 * The browser completes the login; the code is read off the callback
 * request and redeemed here, not by the running BFF.
 */
test('OpenEMR redeems the code and honours a refresh for the BFF client', async ({ page }) => {
  const clientId = process.env.E2E_CLIENT_ID;
  const secretFile = process.env.E2E_CLIENT_SECRET_FILE;
  expect(clientId, 'run through scripts/e2e-local.sh').toBeTruthy();
  const config: Config = {
    port: 0,
    host: '127.0.0.1',
    publicOrigin: 'http://localhost:8787',
    oauthBase: 'https://localhost:9300/oauth2/default',
    browserOauthBase: 'https://localhost:9300/oauth2/default',
    issuer: 'https://localhost:9300/oauth2/default',
    fhirBase: 'https://localhost:9300/apis/default/fhir',
    audience: 'https://localhost:9300/apis/default/fhir',
    clientId: clientId!,
    clientSecret: readFileSync(secretFile!, 'utf8').trim(),
    scopes: DEFAULT_SCOPES,
    sessionIdleMs: 60_000,
    sessionMaxMs: 60_000,
    maxSessions: 1,
    staticDir: null,
    upstreamTimeoutMs: 15_000,
  };
  const login = new SessionStore({ idleMs: 60_000, maxMs: 60_000, maxSessions: 1 }).beginLogin('/');
  let code: string | null = null;
  let state: string | null = null;
  // A redirect hop cannot be routed, so the code is read off the request. The
  // running BFF then receives a callback it has no login for and refuses it
  // (login_expired), so the code is redeemed only here.
  page.on('request', (req) => {
    if (!req.url().startsWith('http://localhost:8787/auth/callback')) return;
    const u = new URL(req.url());
    state = u.searchParams.get('state');
    code = u.searchParams.get('code');
  });
  await page.goto(authorizeUrl(config, login));
  await page.fill('input[name=username]', USER.username);
  await page.fill('input[name=password]', USER.password);
  await page.click('button[type=submit]');
  await page.waitForURL(/scope-authorize-confirm|auth\/callback/);
  if (page.url().includes('scope-authorize-confirm')) await page.click('#authorize-btn');
  await expect.poll(() => code).not.toBeNull();
  expect(state).toBe(login.state);

  const first = await exchangeCode(config, fetch, code!, login, Date.now());
  expect(checkIdToken(config, first.idToken!, login.nonce, Date.now()).sub).toMatch(/^[0-9a-f-]{36}$/);
  expect(first.refreshToken, 'offline_access granted at consent').toBeTruthy();

  const scopes = grantedScopes(config, first.accessToken);
  expect(scopes).toEqual(DEFAULT_SCOPES); // everything ticked at consent; 'nonce' dropped
  const second = await refreshTokens(config, fetch, first.refreshToken!, scopes, Date.now());
  expect(second.accessToken).not.toBe(first.accessToken);
  const res = await fetch(`${config.fhirBase}/Patient?_count=1`, { headers: { Authorization: `Bearer ${second.accessToken}`, Accept: 'application/fhir+json' } });
  expect(res.status).toBe(200);
  // The rotated refresh token works too; the old one is spent.
  if (second.refreshToken && second.refreshToken !== first.refreshToken) {
    await expect(refreshTokens(config, fetch, first.refreshToken!, scopes, Date.now())).rejects.toThrow();
  }
});
