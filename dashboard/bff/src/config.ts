import { readFileSync } from 'node:fs';

/**
 * Everything the BFF needs from its environment. Read once at start; a
 * missing required value is a start-up failure, never a runtime surprise.
 */
export interface Config {
  port: number;
  host: string;
  /** The origin the browser sees, e.g. https://host/dashboard's origin. The redirect URI hangs off it. */
  publicOrigin: string;
  /** OpenEMR's OAuth2 base as the BFF reaches it (token endpoint, back channel). */
  oauthBase: string;
  /**
   * The same base as the browser reaches it (authorize and sign-out
   * redirects). Differs from `oauthBase` when the BFF runs in a container and
   * reaches OpenEMR through the Docker host gateway.
   */
  browserOauthBase: string;
  /**
   * The `iss` the ID token must carry. Configured, not discovered: on the
   * deployed host discovery advertises https://localhost:9300 (audit F-49).
   */
  issuer: string;
  /** OpenEMR's FHIR base as the BFF reaches it. */
  fhirBase: string;
  /**
   * The `aud` sent at authorize. OpenEMR checks it against its own advertised
   * FHIR URL (site_addr_oath + /apis/default/fhir), not the address the BFF dials.
   */
  audience: string;
  clientId: string;
  clientSecret: string;
  scopes: string[];
  sessionIdleMs: number;
  sessionMaxMs: number;
  maxSessions: number;
  /** Built SPA. Absent in unit tests and in `vite` dev, where Vite serves it. */
  staticDir: string | null;
  upstreamTimeoutMs: number;
}

/**
 * Least privilege: `openid` for the ID token (nonce check, sign-out hint),
 * `offline_access` because OpenEMR issues a refresh token only with it, and
 * one read scope per FHIR type the dashboard draws. Nothing else. Practitioner
 * and Organization are read only to name care-team members, and OpenEMR
 * refuses them to a clinician without the admin/users ACL (the card says so).
 */
export const DEFAULT_SCOPES = [
  'openid',
  'offline_access',
  'user/Patient.read',
  'user/AllergyIntolerance.read',
  'user/Condition.read',
  'user/MedicationRequest.read',
  'user/CareTeam.read',
  'user/Observation.read',
  'user/Practitioner.read',
  'user/Organization.read',
];

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`missing required environment variable ${name}`);
  return value;
}

function secret(env: NodeJS.ProcessEnv): string {
  // A file (a Docker secret, mode 0400) is preferred so the secret never
  // appears in `docker inspect` or a process listing.
  const file = env.OAUTH_CLIENT_SECRET_FILE?.trim();
  if (file) {
    const value = readFileSync(file, 'utf8').trim();
    if (!value) throw new Error('OAUTH_CLIENT_SECRET_FILE is empty');
    return value;
  }
  return required(env, 'OAUTH_CLIENT_SECRET');
}

function stripSlash(url: string): string {
  return url.replace(/\/+$/, '');
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const oauthBase = stripSlash(required(env, 'OPENEMR_OAUTH_BASE'));
  const publicOrigin = stripSlash(required(env, 'PUBLIC_ORIGIN'));
  if (new URL(publicOrigin).pathname !== '/') {
    throw new Error('PUBLIC_ORIGIN must be an origin (scheme://host[:port]) with no path');
  }
  return {
    port: Number(env.PORT ?? 8787),
    host: env.HOST ?? '127.0.0.1',
    publicOrigin,
    oauthBase,
    browserOauthBase: stripSlash(env.OPENEMR_BROWSER_OAUTH_BASE ?? oauthBase),
    issuer: stripSlash(env.OPENEMR_ISSUER ?? oauthBase),
    fhirBase: stripSlash(required(env, 'OPENEMR_FHIR_BASE')),
    audience: stripSlash(env.OPENEMR_FHIR_AUDIENCE ?? required(env, 'OPENEMR_FHIR_BASE')),
    clientId: required(env, 'OAUTH_CLIENT_ID'),
    clientSecret: secret(env),
    scopes: env.OAUTH_SCOPES ? env.OAUTH_SCOPES.split(/\s+/).filter(Boolean) : DEFAULT_SCOPES,
    sessionIdleMs: Number(env.SESSION_IDLE_MINUTES ?? 30) * 60_000,
    sessionMaxMs: Number(env.SESSION_MAX_HOURS ?? 8) * 3_600_000,
    maxSessions: Number(env.MAX_SESSIONS ?? 500),
    staticDir: env.STATIC_DIR ?? null,
    upstreamTimeoutMs: Number(env.UPSTREAM_TIMEOUT_MS ?? 15_000),
  };
}
