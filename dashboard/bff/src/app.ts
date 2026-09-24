import { Hono, type Context } from 'hono';
import { getCookie, setCookie, deleteCookie } from 'hono/cookie';
import type { Config } from './config.js';
import { SessionStore, type Clock, type Session } from './sessions.js';
import { authorizeUrl, checkIdToken, endSessionUrl, exchangeCode, grantedScopes, OAuthError, refreshTokens, type FetchLike } from './oauth.js';
import { checkFhirRequest, mapUpstreamStatus } from './fhirProxy.js';
import { logEvent, logRequest } from './log.js';
import { staticHandler } from './static.js';

type Env = { Variables: { route: string } };

export interface AppDeps {
  config: Config;
  fetch?: FetchLike;
  now?: Clock;
  store?: SessionStore;
}

/** Refresh when the access token has less than this left, so a proxied read never races expiry. */
const REFRESH_MARGIN_MS = 60_000;
const LOGIN_ERRORS = new Set(['access_denied', 'invalid_scope', 'server_error', 'temporarily_unavailable', 'login_expired', 'state_mismatch', 'login_failed']);

/**
 * The CSP is the reason the BFF is same-origin: the SPA talks only to
 * 'self', loads nothing from anywhere else, and runs no inline script or
 * style. Everything it needs is bundled.
 */
export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "manifest-src 'self'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

export function createApp(deps: AppDeps): { app: Hono<Env>; store: SessionStore } {
  const { config } = deps;
  const fetchImpl: FetchLike = deps.fetch ?? ((input, init) => fetch(input, init));
  const now: Clock = deps.now ?? Date.now;
  const store =
    deps.store ?? new SessionStore({ idleMs: config.sessionIdleMs, maxMs: config.sessionMaxMs, maxSessions: config.maxSessions }, now);
  const https = config.publicOrigin.startsWith('https://');
  // __Host- pins the cookie to this exact origin (Secure, Path=/, no Domain).
  // Browsers honour the prefix only on https, so plain-http localhost dev uses bare names.
  const SID = https ? '__Host-dash_sid' : 'dash_sid';
  const LOGIN = https ? '__Host-dash_login' : 'dash_login';
  const refreshing = new Map<string, Promise<Session | null>>();

  const app = new Hono<Env>();

  app.use('*', async (c, next) => {
    const started = performance.now();
    c.set('route', 'unmatched');
    await next();
    const h = c.res.headers;
    h.set('Content-Security-Policy', CSP);
    h.set('X-Content-Type-Options', 'nosniff');
    h.set('Referrer-Policy', 'no-referrer');
    h.set('X-Frame-Options', 'DENY');
    h.set('Cross-Origin-Opener-Policy', 'same-origin');
    h.set('Cross-Origin-Resource-Policy', 'same-origin');
    h.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()');
    if (https) h.set('Strict-Transport-Security', 'max-age=31536000');
    logRequest({ method: c.req.method, route: c.get('route'), status: c.res.status, ms: performance.now() - started });
  });

  const noStore = (c: Context<Env>) => c.header('Cache-Control', 'no-store');

  app.get('/healthz', (c) => {
    c.set('route', '/healthz');
    noStore(c);
    return c.json({ ok: true });
  });

  // ---- Sign-in ------------------------------------------------------------

  app.get('/auth/login', (c) => {
    c.set('route', '/auth/login');
    noStore(c);
    const requested = c.req.query('return') ?? '/';
    // Only same-origin paths; `//host` and `/\host` would be protocol-relative.
    const returnTo = /^\/(?![/\\])[\w\-./]*$/.test(requested) ? requested : '/';
    const login = store.beginLogin(returnTo);
    // Lax, not Strict: this cookie must come back on the top-level redirect
    // from OpenEMR's login page, which a browser treats as cross-site when
    // the two are on different sites. It holds only a one-use lookup key.
    setCookie(c, LOGIN, login.id, { httpOnly: true, secure: true, sameSite: 'Lax', path: '/', maxAge: 600 });
    logEvent({ event: 'login_started' });
    return c.redirect(authorizeUrl(config, login), 302);
  });

  app.get('/auth/callback', async (c) => {
    c.set('route', '/auth/callback');
    noStore(c);
    const login = store.takeLogin(getCookie(c, LOGIN));
    deleteCookie(c, LOGIN, { path: '/', secure: true });
    const fail = (code: string) => {
      logEvent({ event: 'login_failed', code });
      return c.redirect(`/?login_error=${LOGIN_ERRORS.has(code) ? code : 'login_failed'}`, 302);
    };
    if (!login) return fail('login_expired');
    const error = c.req.query('error');
    if (error) return fail(error);
    if (c.req.query('state') !== login.state) return fail('state_mismatch');
    const code = c.req.query('code');
    if (!code) return fail('login_failed');
    try {
      const tokens = await exchangeCode(config, fetchImpl, code, login, now());
      if (!tokens.idToken) throw new OAuthError('id_token_missing');
      const { sub } = checkIdToken(config, tokens.idToken, login.nonce, now());
      const session = store.create({ ...tokens, subject: sub, scopes: grantedScopes(config, tokens.accessToken) });
      setCookie(c, SID, session.id, {
        httpOnly: true,
        secure: true,
        sameSite: 'Strict',
        path: '/',
        maxAge: Math.floor(config.sessionMaxMs / 1000),
      });
      logEvent({ event: 'login_completed' });
      return c.redirect(login.returnTo, 302);
    } catch (e) {
      return fail(e instanceof OAuthError ? e.code : 'login_failed');
    }
  });

  app.get('/auth/session', (c) => {
    c.set('route', '/auth/session');
    noStore(c);
    const session = store.get(getCookie(c, SID));
    if (!session) return c.json({ authenticated: false });
    return c.json({ authenticated: true, sessionExpiresAt: session.createdAt + config.sessionMaxMs });
  });

  app.post('/auth/logout', (c) => {
    c.set('route', '/auth/logout');
    noStore(c);
    // SameSite=Strict already keeps the cookie off cross-site requests; the
    // custom header (which a cross-site form cannot set without a CORS
    // preflight we never answer) and the Origin check are belt and braces.
    const origin = c.req.header('Origin');
    if (c.req.header('X-Dashboard-Request') !== '1' || (origin && origin !== config.publicOrigin)) {
      return c.json({ error: 'csrf_check_failed' }, 403);
    }
    const session = store.destroy(getCookie(c, SID));
    deleteCookie(c, SID, { path: '/', secure: true });
    logEvent({ event: 'logout' });
    // OpenEMR has no token revocation endpoint. Dropping the session here
    // discards the tokens; its own sign-out ends the OAuth session there.
    return c.json({ signedOut: true, endSessionUrl: session?.idToken ? endSessionUrl(config, session.idToken) : null });
  });

  // ---- FHIR, read-only ----------------------------------------------------

  async function freshSession(session: Session): Promise<Session | null> {
    if (session.accessExpiresAt - now() > REFRESH_MARGIN_MS) return session;
    if (!session.refreshToken) {
      store.destroy(session.id);
      return null;
    }
    // One refresh per session at a time: parallel card fetches share it.
    let pending = refreshing.get(session.id);
    if (!pending) {
      pending = (async () => {
        try {
          const tokens = await refreshTokens(config, fetchImpl, session.refreshToken!, session.scopes, now());
          session.accessToken = tokens.accessToken;
          session.accessExpiresAt = tokens.accessExpiresAt;
          // OpenEMR rotates the refresh token; keep the old one only if it did not send one.
          session.refreshToken = tokens.refreshToken ?? session.refreshToken;
          session.idToken = tokens.idToken ?? session.idToken;
          logEvent({ event: 'token_refreshed' });
          return session;
        } catch (e) {
          logEvent({ event: 'refresh_failed', code: e instanceof OAuthError ? e.code : 'refresh_failed' });
          store.destroy(session.id);
          return null;
        } finally {
          refreshing.delete(session.id);
        }
      })();
      refreshing.set(session.id, pending);
    }
    return pending;
  }

  const fhir = async (c: Context<Env>, route: string) => {
    c.set('route', route);
    noStore(c);
    const check = checkFhirRequest(c.req.param('type')!, c.req.param('id'), new URL(c.req.url).searchParams);
    if (!check.ok) return c.json({ error: check.error }, check.status);
    const found = store.get(getCookie(c, SID));
    if (!found) return c.json({ error: 'no_session' }, 401);
    const session = await freshSession(found);
    if (!session) {
      deleteCookie(c, SID, { path: '/', secure: true });
      return c.json({ error: 'session_expired' }, 401);
    }
    let upstream: Response;
    try {
      upstream = await fetchImpl(config.fhirBase + check.upstreamPath, {
        method: 'GET',
        // Only the bearer token and Accept go upstream: never the browser's cookies or headers.
        headers: { Authorization: `Bearer ${session.accessToken}`, Accept: 'application/fhir+json' },
        signal: AbortSignal.timeout(config.upstreamTimeoutMs),
      });
    } catch (e) {
      const timeout = e instanceof Error && e.name === 'TimeoutError';
      logEvent({ event: 'upstream_error', code: timeout ? 'timeout' : 'unreachable' });
      return c.json({ error: timeout ? 'upstream_timeout' : 'upstream_unreachable' }, timeout ? 504 : 502);
    }
    const mapped = mapUpstreamStatus(upstream.status);
    if (mapped.error) {
      await upstream.body?.cancel();
      if (mapped.status === 502) logEvent({ event: 'upstream_error', code: `status_${upstream.status}` });
      return c.json({ error: mapped.error }, mapped.status as 400);
    }
    return new Response(await upstream.text(), {
      status: 200,
      headers: { 'Content-Type': 'application/fhir+json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  };

  app.get('/fhir/:type/:id', (c) => fhir(c, '/fhir/:type/:id'));
  app.get('/fhir/:type', (c) => fhir(c, '/fhir/:type'));
  app.all('/fhir/*', (c) => {
    c.set('route', '/fhir/*');
    c.header('Allow', 'GET');
    return c.json({ error: 'method_not_allowed' }, 405);
  });
  app.all('/auth/*', (c) => {
    c.set('route', '/auth/*');
    return c.json({ error: 'not_found' }, 404);
  });

  // ---- The SPA --------------------------------------------------------------

  if (config.staticDir) {
    const serve = staticHandler(config.staticDir);
    app.get('*', async (c) => {
      c.set('route', 'static');
      return serve(c);
    });
  }

  return { app, store };
}
