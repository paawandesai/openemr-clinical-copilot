import { createHash } from 'node:crypto';
import type { Config } from './config.js';
import type { PendingLogin } from './sessions.js';

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface TokenSet {
  accessToken: string;
  accessExpiresAt: number;
  refreshToken: string | null;
  idToken: string | null;
}

/** A token-endpoint failure. `code` is OAuth's error code or our own; never upstream prose. */
export class OAuthError extends Error {
  readonly code: string;
  readonly status: number | undefined;
  constructor(code: string, status?: number) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

export function redirectUri(config: Config): string {
  return `${config.publicOrigin}/auth/callback`;
}

export function codeChallenge(verifier: string): string {
  return createHash('sha256').update(verifier).digest('base64url');
}

export function authorizeUrl(config: Config, login: PendingLogin): string {
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.clientId,
    redirect_uri: redirectUri(config),
    scope: config.scopes.join(' '),
    state: login.state,
    nonce: login.nonce,
    code_challenge: codeChallenge(login.codeVerifier),
    code_challenge_method: 'S256',
    aud: config.audience,
  });
  return `${config.browserOauthBase}/authorize?${params}`;
}

function decodeJwtPayload(jwt: string): Record<string, unknown> {
  const part = jwt.split('.')[1];
  if (!part) throw new OAuthError('id_token_malformed');
  try {
    return JSON.parse(Buffer.from(part, 'base64url').toString('utf8')) as Record<string, unknown>;
  } catch {
    throw new OAuthError('id_token_malformed');
  }
}

/**
 * Claim checks on the ID token. It arrives on the back channel, straight
 * from the token endpoint over TLS in answer to our own code redemption,
 * which OIDC Core 3.1.3.7 accepts in place of a signature check. What is
 * left to check is that it is ours: issuer (configured, per F-49), audience,
 * nonce (binds it to this login) and expiry.
 */
export function checkIdToken(config: Config, idToken: string, nonce: string, nowMs: number): { sub: string } {
  const claims = decodeJwtPayload(idToken);
  if (claims.iss !== config.issuer) throw new OAuthError('id_token_issuer');
  const aud = claims.aud;
  if (!(aud === config.clientId || (Array.isArray(aud) && aud.includes(config.clientId)))) {
    throw new OAuthError('id_token_audience');
  }
  if (claims.nonce !== nonce) throw new OAuthError('id_token_nonce');
  if (typeof claims.exp !== 'number' || claims.exp * 1000 < nowMs) throw new OAuthError('id_token_expired');
  if (typeof claims.sub !== 'string' || !claims.sub) throw new OAuthError('id_token_subject');
  return { sub: claims.sub };
}

async function tokenRequest(config: Config, fetchImpl: FetchLike, body: Record<string, string>, nowMs: number): Promise<TokenSet> {
  let res: Response;
  try {
    res = await fetchImpl(`${config.oauthBase}/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
      // client_secret_post: the confidential client authenticates here, and
      // only here. The secret is never sent anywhere else.
      body: new URLSearchParams({ ...body, client_id: config.clientId, client_secret: config.clientSecret }),
      signal: AbortSignal.timeout(config.upstreamTimeoutMs),
    });
  } catch {
    throw new OAuthError('token_endpoint_unreachable');
  }
  let json: Record<string, unknown> = {};
  try {
    json = (await res.json()) as Record<string, unknown>;
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok || typeof json.access_token !== 'string') {
    const code = typeof json.error === 'string' && /^[a-z_]{1,40}$/.test(json.error) ? json.error : 'token_request_failed';
    throw new OAuthError(code, res.status);
  }
  const expiresIn = typeof json.expires_in === 'number' ? json.expires_in : 3600;
  return {
    accessToken: json.access_token,
    accessExpiresAt: nowMs + expiresIn * 1000,
    refreshToken: typeof json.refresh_token === 'string' ? json.refresh_token : null,
    idToken: typeof json.id_token === 'string' ? json.id_token : null,
  };
}

export function exchangeCode(config: Config, fetchImpl: FetchLike, code: string, login: PendingLogin, nowMs: number): Promise<TokenSet> {
  return tokenRequest(
    config,
    fetchImpl,
    { grant_type: 'authorization_code', code, redirect_uri: redirectUri(config), code_verifier: login.codeVerifier },
    nowMs,
  );
}

/**
 * The scopes the user actually granted. OpenEMR's consent page lets them
 * untick resources, so this can be fewer than we asked for. Read from the
 * access token's own `scopes` claim, not the response's `scope` field, which
 * has been seen to disagree with it (audit F-09). OpenEMR also puts the
 * literal `nonce` in that claim; it is not a scope and is dropped.
 */
export function grantedScopes(config: Config, accessToken: string): string[] {
  try {
    const claims = decodeJwtPayload(accessToken);
    if (Array.isArray(claims.scopes)) {
      const granted = new Set(claims.scopes.filter((x): x is string => typeof x === 'string'));
      return config.scopes.filter((x) => granted.has(x));
    }
  } catch {
    /* not a JWT we can read */
  }
  return config.scopes;
}

/**
 * `scope` is sent explicitly. Without it the server reuses the refresh
 * token's stored scopes, which in OpenEMR include the authorize request's
 * `nonce`, and then rejects `nonce` as an unknown scope (`invalid_scope`;
 * found on the local stack 2026-09-24, every refresh failed). A refresh may
 * ask for any subset of the original grant (RFC 6749 §6), so it asks for
 * exactly what was granted.
 */
export function refreshTokens(config: Config, fetchImpl: FetchLike, refreshToken: string, scopes: string[], nowMs: number): Promise<TokenSet> {
  return tokenRequest(config, fetchImpl, { grant_type: 'refresh_token', refresh_token: refreshToken, scope: scopes.join(' ') }, nowMs);
}

/** OpenEMR's RP-initiated sign-out, which ends its own OAuth session for this user and client. */
export function endSessionUrl(config: Config, idToken: string): string {
  const params = new URLSearchParams({ id_token_hint: idToken, post_logout_redirect_uri: `${config.publicOrigin}/` });
  return `${config.browserOauthBase}/logout?${params}`;
}
