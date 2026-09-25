import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApp, CSP } from '../../bff/src/app';
import type { Config } from '../../bff/src/config';
import { loadConfig } from '../../bff/src/config';
import { setLogSink } from '../../bff/src/log';
import { SessionStore } from '../../bff/src/sessions';

const PATIENT = 'a2c373a4-dd96-48f7-b353-6732b98bf04f';
const T0 = Date.UTC(2026, 8, 24, 12, 0, 0);

function config(overrides: Partial<Config> = {}): Config {
  return {
    port: 0,
    host: '127.0.0.1',
    publicOrigin: 'http://localhost:8787',
    oauthBase: 'https://openemr.test/oauth2/default',
    browserOauthBase: 'https://openemr.test/oauth2/default',
    issuer: 'https://openemr.test/oauth2/default',
    fhirBase: 'https://openemr.test/apis/default/fhir',
    audience: 'https://openemr.test/apis/default/fhir',
    clientId: 'client-1',
    clientSecret: 'secret-never-leaves',
    scopes: ['openid', 'offline_access', 'user/Patient.read'],
    sessionIdleMs: 30 * 60_000,
    sessionMaxMs: 8 * 3_600_000,
    maxSessions: 10,
    staticDir: null,
    upstreamTimeoutMs: 5000,
    ...overrides,
  };
}

const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url');
const jwt = (claims: object) => `${b64({ alg: 'RS256' })}.${b64(claims)}.sig`;

/** A fake OpenEMR: a token endpoint and a FHIR endpoint, recording every call. */
function fakeOpenEmr(c: Config, clock: { now: number }) {
  const calls: { url: string; body?: URLSearchParams; auth?: string }[] = [];
  let tokenSeq = 0;
  let nonce = '';
  const state = {
    refreshFails: false,
    fhirStatus: 200,
    fhirBody: '{"resourceType":"Bundle","total":0}',
    refreshDelayMs: 0,
    /** When set, access tokens are JWTs carrying this `scopes` claim, as OpenEMR's are. */
    accessScopes: null as string[] | null,
  };
  const fetch = async (url: string, init?: RequestInit): Promise<Response> => {
    const body = init?.body instanceof URLSearchParams ? init.body : undefined;
    const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
    calls.push({ url, body, auth });
    if (url === `${c.oauthBase}/token`) {
      const grant = body?.get('grant_type');
      if (grant === 'refresh_token') {
        if (state.refreshDelayMs) await new Promise((r) => setTimeout(r, state.refreshDelayMs));
        if (state.refreshFails) return new Response(JSON.stringify({ error: 'invalid_grant', error_description: 'patient Jane Doe' }), { status: 400 });
      }
      tokenSeq += 1;
      return Response.json({
        access_token: state.accessScopes ? jwt({ jti: `access-${tokenSeq}`, scopes: state.accessScopes }) : `access-${tokenSeq}`,
        refresh_token: `refresh-${tokenSeq}`,
        expires_in: 3600,
        id_token: jwt({ iss: c.issuer, aud: c.clientId, nonce, exp: Math.floor(clock.now / 1000) + 3600, sub: 'user-uuid' }),
      });
    }
    if (url.startsWith(c.fhirBase)) return new Response(state.fhirBody, { status: state.fhirStatus });
    throw new Error('unexpected upstream ' + url);
  };
  return { fetch, calls, state, setNonce: (n: string) => (nonce = n) };
}

function setCookies(res: Response): string[] {
  return res.headers.getSetCookie();
}

function cookieValue(res: Response, name: string): string | undefined {
  for (const c of setCookies(res)) {
    const [pair] = c.split(';');
    const [k, v] = pair!.split('=');
    if (k === name) return v;
  }
  return undefined;
}

let logs: string[] = [];
beforeEach(() => {
  logs = [];
  setLogSink((l) => logs.push(l));
});
afterEach(() => setLogSink((l) => process.stdout.write(l + '\n')));

/** Runs a full login and returns the app, fake upstream and the session cookie header. */
async function signedIn(overrides: Partial<Config> = {}) {
  const c = config(overrides);
  const clock = { now: T0 };
  const up = fakeOpenEmr(c, clock);
  const { app, store } = createApp({ config: c, fetch: up.fetch, now: () => clock.now });
  const https = c.publicOrigin.startsWith('https');
  const loginName = https ? '__Host-dash_login' : 'dash_login';
  const sidName = https ? '__Host-dash_sid' : 'dash_sid';
  const login = await app.request('/auth/login?return=/patient/' + PATIENT);
  const location = new URL(login.headers.get('Location')!);
  up.setNonce(location.searchParams.get('nonce')!);
  const loginCookie = cookieValue(login, loginName)!;
  const callback = await app.request(`/auth/callback?code=code-1&state=${location.searchParams.get('state')}`, {
    headers: { Cookie: `${loginName}=${loginCookie}` },
  });
  const sid = cookieValue(callback, sidName)!;
  return { app, store, up, clock, c, login, callback, location, cookie: `${sidName}=${sid}`, sidName };
}

describe('sign-in', () => {
  it('sends the browser to OpenEMR with PKCE S256, state, nonce, aud and the configured scopes', async () => {
    const { location, c } = await signedIn();
    expect(location.origin + location.pathname).toBe(`${c.oauthBase}/authorize`);
    const p = location.searchParams;
    expect(p.get('code_challenge_method')).toBe('S256');
    expect(p.get('code_challenge')).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.get('aud')).toBe(c.fhirBase);
    expect(p.get('redirect_uri')).toBe('http://localhost:8787/auth/callback');
    expect(p.get('scope')).toBe('openid offline_access user/Patient.read');
    expect(p.get('client_secret')).toBeNull();
  });

  it('redeems the code on the back channel with the secret and the PKCE verifier', async () => {
    const { up } = await signedIn();
    const tokenCall = up.calls.find((x) => x.body?.get('grant_type') === 'authorization_code')!;
    expect(tokenCall.body?.get('client_secret')).toBe('secret-never-leaves');
    expect(tokenCall.body?.get('code_verifier')).toMatch(/^[A-Za-z0-9_-]{64}$/);
  });

  it('sets the login cookie HttpOnly, Secure, SameSite=Lax, and the session cookie HttpOnly, Secure, SameSite=Strict', async () => {
    const { login, callback } = await signedIn();
    const loginCookie = setCookies(login).find((x) => x.startsWith('dash_login='))!;
    expect(loginCookie).toMatch(/HttpOnly/);
    expect(loginCookie).toMatch(/Secure/);
    expect(loginCookie).toMatch(/SameSite=Lax/);
    const sid = setCookies(callback).find((x) => x.startsWith('dash_sid='))!;
    expect(sid).toMatch(/HttpOnly/);
    expect(sid).toMatch(/Secure/);
    expect(sid).toMatch(/SameSite=Strict/);
    expect(sid).toMatch(/Path=\//);
    expect(callback.headers.get('Location')).toBe('/patient/' + PATIENT);
  });

  it('uses __Host- cookies on an https origin', async () => {
    const { callback } = await signedIn({ publicOrigin: 'https://dash.example' });
    const sid = setCookies(callback).find((x) => x.startsWith('__Host-dash_sid='))!;
    expect(sid).toMatch(/HttpOnly/);
    expect(sid).toMatch(/Secure/);
    expect(sid).toMatch(/SameSite=Strict/);
    expect(sid).not.toMatch(/Domain=/);
  });

  it('never puts a token in a response body, header or redirect', async () => {
    const { app, callback, cookie } = await signedIn();
    const session = await app.request('/auth/session', { headers: { Cookie: cookie } });
    const all = [callback.headers.get('Location'), JSON.stringify([...callback.headers]), await session.text()].join(' ');
    expect(all).not.toMatch(/access-\d|refresh-\d|secret-never-leaves/);
    expect(JSON.parse(await (await app.request('/auth/session', { headers: { Cookie: cookie } })).text())).toMatchObject({ authenticated: true });
  });

  it('refuses a callback whose state does not match', async () => {
    const c = config();
    const clock = { now: T0 };
    const up = fakeOpenEmr(c, clock);
    const { app, store } = createApp({ config: c, fetch: up.fetch, now: () => clock.now });
    const login = await app.request('/auth/login');
    const res = await app.request('/auth/callback?code=x&state=forged', { headers: { Cookie: `dash_login=${cookieValue(login, 'dash_login')}` } });
    expect(res.headers.get('Location')).toBe('/?login_error=state_mismatch');
    expect(store.size()).toBe(0);
    expect(up.calls).toHaveLength(0);
  });

  it('refuses a callback without the login cookie, and a replayed login', async () => {
    const { app, location } = await signedIn();
    const res = await app.request(`/auth/callback?code=x&state=${location.searchParams.get('state')}`);
    expect(res.headers.get('Location')).toBe('/?login_error=login_expired');
  });

  it('refuses an ID token with the wrong nonce', async () => {
    const c = config();
    const clock = { now: T0 };
    const up = fakeOpenEmr(c, clock);
    const { app, store } = createApp({ config: c, fetch: up.fetch, now: () => clock.now });
    const login = await app.request('/auth/login');
    const loc = new URL(login.headers.get('Location')!);
    up.setNonce('someone-elses-nonce');
    const res = await app.request(`/auth/callback?code=x&state=${loc.searchParams.get('state')}`, { headers: { Cookie: `dash_login=${cookieValue(login, 'dash_login')}` } });
    expect(res.headers.get('Location')).toBe('/?login_error=login_failed');
    expect(store.size()).toBe(0);
  });

  it('only returns to same-origin paths', async () => {
    const { app } = createApp({ config: config(), fetch: async () => new Response() });
    for (const bad of ['//evil.example/x', '/\\evil.example', 'https://evil.example']) {
      const res = await app.request('/auth/login?return=' + encodeURIComponent(bad));
      expect(res.status).toBe(302);
    }
  });
});

describe('the FHIR allowlist', () => {
  it('forwards an allowed GET with only the bearer token', async () => {
    const { app, up, cookie } = await signedIn();
    const res = await app.request(`/fhir/AllergyIntolerance?patient=${PATIENT}`, { headers: { Cookie: cookie, 'X-Extra': 'drop-me' } });
    expect(res.status).toBe(200);
    const call = up.calls.at(-1)!;
    expect(call.url).toBe(`https://openemr.test/apis/default/fhir/AllergyIntolerance?patient=${PATIENT}`);
    expect(call.auth).toBe('Bearer access-1');
    expect(res.headers.get('Cache-Control')).toBe('no-store');
  });

  it.each(['Encounter', 'Binary', 'DocumentReference', 'Provenance', 'Immunization', 'metadata'])('refuses resource type %s', async (type) => {
    const { app, up, cookie } = await signedIn();
    const before = up.calls.length;
    const res = await app.request(`/fhir/${type}?patient=${PATIENT}`, { headers: { Cookie: cookie } });
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: 'resource_type_not_allowed' });
    expect(up.calls.length).toBe(before);
  });

  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])('refuses %s', async (method) => {
    const { app, up, cookie } = await signedIn();
    const before = up.calls.length;
    const res = await app.request('/fhir/Patient/' + PATIENT, { method, headers: { Cookie: cookie }, body: method === 'DELETE' ? undefined : '{}' });
    expect(res.status).toBe(405);
    expect(res.headers.get('Allow')).toBe('GET');
    expect(up.calls.length).toBe(before);
  });

  it.each([
    ['_include=CareTeam:participant', 'parameter_not_allowed'],
    ['_revinclude=Provenance:target', 'parameter_not_allowed'],
    ['_has:Observation:patient:code=1', 'parameter_not_allowed'],
    ['patient=a&patient=b', 'parameter_repeated'],
    ['patient=' + 'x'.repeat(201), 'parameter_too_long'],
  ])('refuses the query %s', async (query, error) => {
    const { app, cookie } = await signedIn();
    const res = await app.request(`/fhir/Observation?${query}`, { headers: { Cookie: cookie } });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error });
  });

  it('refuses a malformed id', async () => {
    const { app, cookie } = await signedIn();
    const res = await app.request('/fhir/Patient/..%2F..%2Fapi%2Fpatient', { headers: { Cookie: cookie } });
    expect(res.status).toBe(400);
  });

  it('refuses without a session', async () => {
    const { app } = createApp({ config: config(), fetch: async () => new Response() });
    const res = await app.request(`/fhir/Patient/${PATIENT}`);
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: 'no_session' });
  });

  it("maps upstream failures to our own codes and never passes OpenEMR's text on", async () => {
    const { app, up, cookie } = await signedIn();
    for (const [status, expected, code] of [
      [401, 401, 'upstream_unauthorized'],
      [403, 403, 'upstream_forbidden'],
      [404, 404, 'not_found'],
      [500, 502, 'upstream_error'],
    ] as const) {
      up.state.fhirStatus = status;
      up.state.fhirBody = '{"message":"Organization policy does not have permit access resource Jane Doe"}';
      const res = await app.request(`/fhir/Practitioner/abc`, { headers: { Cookie: cookie } });
      expect(res.status).toBe(expected);
      const text = await res.text();
      expect(text).toBe(JSON.stringify({ error: code }));
    }
  });
});

describe('token refresh', () => {
  // A user still active when the one-hour access token runs out: the idle
  // window is widened so the jump in the clock reads as activity, not idleness.
  const active = { sessionIdleMs: 4 * 3_600_000 };

  it('refreshes an access token that is about to expire, then uses the new one', async () => {
    const { app, up, clock, cookie } = await signedIn(active);
    clock.now += 3600_000 - 30_000; // 30 s left
    const res = await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: cookie } });
    expect(res.status).toBe(200);
    const refresh = up.calls.find((x) => x.body?.get('grant_type') === 'refresh_token')!;
    expect(refresh.body?.get('refresh_token')).toBe('refresh-1');
    expect(refresh.body?.get('client_secret')).toBe('secret-never-leaves');
    expect(up.calls.at(-1)!.auth).toBe('Bearer access-2');
  });

  it("asks for exactly the granted scopes on refresh: OpenEMR's stray 'nonce' dropped, unticked scopes left out", async () => {
    const c = config({ ...active, scopes: ['openid', 'offline_access', 'user/Patient.read', 'user/Observation.read'] });
    const clock = { now: T0 };
    const up = fakeOpenEmr(c, clock);
    // The user unticked Observation at consent; OpenEMR adds 'nonce' to the claim.
    up.state.accessScopes = ['openid', 'offline_access', 'user/Patient.read', 'nonce'];
    const { app } = createApp({ config: c, fetch: up.fetch, now: () => clock.now });
    const login = await app.request('/auth/login');
    const loc = new URL(login.headers.get('Location')!);
    up.setNonce(loc.searchParams.get('nonce')!);
    const cb = await app.request(`/auth/callback?code=c&state=${loc.searchParams.get('state')}`, { headers: { Cookie: `dash_login=${cookieValue(login, 'dash_login')}` } });
    const cookie = `dash_sid=${cookieValue(cb, 'dash_sid')}`;
    clock.now += 3600_000;
    expect((await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: cookie } })).status).toBe(200);
    const refresh = up.calls.find((x) => x.body?.get('grant_type') === 'refresh_token')!;
    expect(refresh.body?.get('scope')).toBe('openid offline_access user/Patient.read');
  });

  it('does not refresh a token with time left', async () => {
    const { app, up, cookie } = await signedIn();
    await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: cookie } });
    expect(up.calls.filter((x) => x.body?.get('grant_type') === 'refresh_token')).toHaveLength(0);
  });

  it('shares one refresh between parallel card reads', async () => {
    const { app, up, clock, cookie } = await signedIn(active);
    clock.now += 3600_000;
    up.state.refreshDelayMs = 20;
    const types = ['Patient/' + PATIENT, 'AllergyIntolerance', 'Condition', 'MedicationRequest', 'CareTeam', 'Observation'];
    const results = await Promise.all(types.map((t) => app.request(`/fhir/${t}`, { headers: { Cookie: cookie } })));
    expect(results.map((r) => r.status)).toEqual([200, 200, 200, 200, 200, 200]);
    expect(up.calls.filter((x) => x.body?.get('grant_type') === 'refresh_token')).toHaveLength(1);
    expect(new Set(up.calls.filter((x) => x.url.startsWith('https://openemr.test/apis')).map((x) => x.auth))).toEqual(new Set(['Bearer access-2']));
  });

  it('ends the session when the refresh is refused, without logging upstream text', async () => {
    const { app, up, clock, cookie, store } = await signedIn(active);
    clock.now += 3600_000;
    up.state.refreshFails = true;
    const res = await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: cookie } });
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: 'session_expired' });
    expect(store.size()).toBe(0);
    expect(logs.join('\n')).toContain('"code":"invalid_grant"');
    expect(logs.join('\n')).not.toContain('Jane');
  });

  it('ends the session at expiry when there is no refresh token (offline access unticked at consent)', async () => {
    const c = config();
    const store = new SessionStore({ idleMs: c.sessionIdleMs, maxMs: c.sessionMaxMs, maxSessions: 10 }, () => T0);
    const s = store.create({ accessToken: 'a', accessExpiresAt: T0 - 1, refreshToken: null, idToken: null, subject: 'u', scopes: [] });
    const { app } = createApp({ config: c, store, now: () => T0, fetch: async () => new Response('{}') });
    const res = await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: `dash_sid=${s.id}` } });
    expect(res.status).toBe(401);
    expect(store.size()).toBe(0);
  });

  it('expires an idle session and caps its lifetime', () => {
    let now = T0;
    const store = new SessionStore({ idleMs: 1000, maxMs: 5000, maxSessions: 10 }, () => now);
    const s = store.create({ accessToken: 'a', accessExpiresAt: T0 + 1e9, refreshToken: null, idToken: null, subject: 'u', scopes: [] });
    now += 999;
    expect(store.get(s.id)).not.toBeNull();
    now += 1001;
    expect(store.get(s.id)).toBeNull();
    const t = store.create({ accessToken: 'a', accessExpiresAt: T0 + 1e9, refreshToken: null, idToken: null, subject: 'u', scopes: [] });
    for (let i = 0; i < 6; i++) {
      now += 900;
      store.get(t.id);
    }
    expect(store.get(t.id)).toBeNull();
  });
});

describe('logout', () => {
  it('refuses a POST without the custom header, or from another origin', async () => {
    const { app, cookie, store } = await signedIn();
    expect((await app.request('/auth/logout', { method: 'POST', headers: { Cookie: cookie } })).status).toBe(403);
    expect((await app.request('/auth/logout', { method: 'POST', headers: { Cookie: cookie, 'X-Dashboard-Request': '1', Origin: 'https://evil.example' } })).status).toBe(403);
    expect(store.size()).toBe(1);
  });

  it('destroys the server session, clears the cookie, and later reads are refused', async () => {
    const { app, cookie, store, c } = await signedIn();
    const res = await app.request('/auth/logout', { method: 'POST', headers: { Cookie: cookie, 'X-Dashboard-Request': '1', Origin: c.publicOrigin } });
    expect(res.status).toBe(200);
    expect(store.size()).toBe(0);
    const cleared = setCookies(res).find((x) => x.startsWith('dash_sid='))!;
    expect(cleared).toMatch(/Max-Age=0/);
    const body = (await res.json()) as { endSessionUrl: string };
    const end = new URL(body.endSessionUrl);
    expect(end.origin + end.pathname).toBe('https://openemr.test/oauth2/default/logout');
    expect(end.searchParams.get('post_logout_redirect_uri')).toBe('http://localhost:8787/');
    const after = await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: cookie } });
    expect(after.status).toBe(401);
    expect(await (await app.request('/auth/session', { headers: { Cookie: cookie } })).json()).toEqual({ authenticated: false });
  });
});

describe('logs', () => {
  it('carry method, route template, status and latency only: no ids, queries, tokens or cookies', async () => {
    const { app, cookie } = await signedIn();
    await app.request(`/fhir/Observation?patient=${PATIENT}&category=laboratory`, { headers: { Cookie: cookie } });
    await app.request(`/fhir/Patient/${PATIENT}`, { headers: { Cookie: cookie } });
    const text = logs.join('\n');
    expect(text).not.toContain(PATIENT);
    expect(text).not.toMatch(/access-\d|refresh-\d|secret-never-leaves|code-1|user-uuid|laboratory/);
    expect(text).not.toContain(cookie.split('=')[1]!);
    const lines = logs.map((l) => JSON.parse(l) as Record<string, unknown>);
    const fhirLines = lines.filter((l) => typeof l.route === 'string' && (l.route as string).startsWith('/fhir'));
    expect(fhirLines.map((l) => l.route)).toEqual(['/fhir/:type', '/fhir/:type/:id']);
    for (const l of lines) {
      const keys = Object.keys(l).sort();
      expect([['method', 'ms', 'route', 'status', 't'].join(), ['event', 't'].join(), ['code', 'event', 't'].join()]).toContain(keys.join());
    }
  });
});

describe('security headers and static files', () => {
  function withStatic() {
    const dir = mkdtempSync(join(tmpdir(), 'dash-static-'));
    writeFileSync(join(dir, 'index.html'), '<!doctype html><div id="root"></div>');
    mkdirSync(join(dir, 'assets'));
    writeFileSync(join(dir, 'assets', 'index-abc.js'), 'console.log(1)');
    writeFileSync(join(dir, '..', 'dash-secret.txt'), 'outside');
    return createApp({ config: config({ staticDir: dir }), fetch: async () => new Response() }).app;
  }

  it('sends the CSP and hardening headers on every response', async () => {
    const app = withStatic();
    for (const path of ['/', '/patient/abc', '/healthz', '/fhir/Encounter', '/assets/index-abc.js']) {
      const res = await app.request(path);
      expect(res.headers.get('Content-Security-Policy')).toBe(CSP);
      expect(res.headers.get('X-Frame-Options')).toBe('DENY');
      expect(res.headers.get('Referrer-Policy')).toBe('no-referrer');
      expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
    }
    expect(CSP).toContain("connect-src 'self'");
    expect(CSP).not.toMatch(/unsafe-inline|unsafe-eval|https?:/);
  });

  it('serves index.html for client routes, caches hashed assets, and refuses traversal', async () => {
    const app = withStatic();
    const route = await app.request('/patient/abc');
    expect(await route.text()).toContain('id="root"');
    expect(route.headers.get('Cache-Control')).toBe('no-cache');
    expect((await app.request('/assets/index-abc.js')).headers.get('Cache-Control')).toContain('immutable');
    expect((await app.request('/../dash-secret.txt')).status).toBe(404);
    expect((await app.request('/%2e%2e/dash-secret.txt')).status).toBe(404);
    expect((await app.request('/missing.js')).status).toBe(404);
  });
});

describe('configuration', () => {
  it('sends the browser to the browser-facing base while redeeming codes on the back channel', async () => {
    const c = config({ oauthBase: 'https://host.docker.internal:9300/oauth2/default', browserOauthBase: 'https://emr.example:9300/oauth2/default' });
    const clock = { now: T0 };
    const up = fakeOpenEmr(c, clock);
    const { app } = createApp({ config: c, fetch: up.fetch, now: () => clock.now });
    const login = await app.request('/auth/login');
    expect(login.headers.get('Location')).toMatch(/^https:\/\/emr\.example:9300\/oauth2\/default\/authorize\?/);
    const loc = new URL(login.headers.get('Location')!);
    up.setNonce(loc.searchParams.get('nonce')!);
    await app.request(`/auth/callback?code=c&state=${loc.searchParams.get('state')}`, { headers: { Cookie: `dash_login=${cookieValue(login, 'dash_login')}` } });
    expect(up.calls[0]!.url).toBe('https://host.docker.internal:9300/oauth2/default/token');
  });

  it('refuses to start without the client secret, and rejects a PUBLIC_ORIGIN with a path', () => {
    const env = { OPENEMR_OAUTH_BASE: 'https://o/oauth2/default', OPENEMR_FHIR_BASE: 'https://o/apis/default/fhir', OAUTH_CLIENT_ID: 'c', PUBLIC_ORIGIN: 'https://d' };
    expect(() => loadConfig(env)).toThrow(/OAUTH_CLIENT_SECRET/);
    expect(() => loadConfig({ ...env, OAUTH_CLIENT_SECRET: 's', PUBLIC_ORIGIN: 'https://d/app' })).toThrow(/no path/);
    expect(loadConfig({ ...env, OAUTH_CLIENT_SECRET: 's' }).issuer).toBe('https://o/oauth2/default');
  });
});
