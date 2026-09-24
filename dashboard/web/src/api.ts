/**
 * The SPA's only network access: same-origin calls to the BFF. The browser
 * never holds an OAuth token; the session is an httpOnly cookie the BFF set,
 * which the browser attaches on its own.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

export const SESSION_ENDED = 'dashboard:session-ended';

export type Fetcher = <T>(path: string, signal?: AbortSignal) => Promise<T>;

export const fhirGet: Fetcher = async <T>(path: string, signal?: AbortSignal): Promise<T> => {
  let res: Response;
  try {
    res = await fetch(`/fhir${path}`, { credentials: 'same-origin', headers: { Accept: 'application/fhir+json' }, signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'network');
  }
  if (!res.ok) {
    let code = 'http_' + res.status;
    try {
      const body = (await res.json()) as { error?: string };
      if (body.error) code = body.error;
    } catch {
      /* not JSON */
    }
    if (res.status === 401 && (code === 'no_session' || code === 'session_expired')) {
      window.dispatchEvent(new Event(SESSION_ENDED));
    }
    throw new ApiError(res.status, code);
  }
  return (await res.json()) as T;
};

export interface SessionInfo {
  authenticated: boolean;
  sessionExpiresAt?: number;
}

export async function getSession(): Promise<SessionInfo> {
  const res = await fetch('/auth/session', { credentials: 'same-origin' });
  if (!res.ok) throw new ApiError(res.status, 'session_check_failed');
  return (await res.json()) as SessionInfo;
}

export async function logout(): Promise<string | null> {
  const res = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-Dashboard-Request': '1' } });
  const body = (await res.json()) as { endSessionUrl?: string | null };
  return body.endSessionUrl ?? null;
}

/** What a card says when its read fails. Never OpenEMR's own error text. */
export function errorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return 'Could not load this section.';
  switch (e.code) {
    case 'upstream_unauthorized':
      // OpenEMR returns 401 for both a missing scope and an expired token (audit F-08).
      return 'OpenEMR refused this request: your sign-in does not cover this section, or it has expired.';
    case 'upstream_forbidden':
      return 'Your OpenEMR role does not permit reading this section.';
    case 'not_found':
      return 'OpenEMR has no record here.';
    case 'upstream_timeout':
      return 'OpenEMR did not answer in time.';
    case 'upstream_unreachable':
    case 'upstream_error':
      return 'OpenEMR could not be reached.';
    case 'network':
      return 'The dashboard server could not be reached.';
    case 'no_session':
    case 'session_expired':
      return 'Your session has ended. Sign in again.';
    default:
      return 'Could not load this section.';
  }
}
