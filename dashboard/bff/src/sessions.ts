import { randomBytes } from 'node:crypto';

/** Tokens for one signed-in browser. Lives only in this process's memory. */
export interface Session {
  id: string;
  createdAt: number;
  lastSeenAt: number;
  accessToken: string;
  /** Epoch ms. */
  accessExpiresAt: number;
  refreshToken: string | null;
  idToken: string | null;
  /** The ID token's `sub`: the OpenEMR user's UUID. Never logged. */
  subject: string;
  /** What the user granted at consent; a refresh asks for exactly these. */
  scopes: string[];
}

/** An authorize request in flight: what the callback must match. */
export interface PendingLogin {
  id: string;
  state: string;
  nonce: string;
  codeVerifier: string;
  returnTo: string;
  createdAt: number;
}

export type Clock = () => number;

const LOGIN_TTL_MS = 10 * 60_000;

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString('base64url');
}

/**
 * The server-side half of the session. The browser holds only an opaque id
 * in an httpOnly cookie; the tokens never leave this map. A process restart
 * signs everyone out, which is the intended failure mode for a single small
 * container (no token store on disk to protect).
 */
export class SessionStore {
  private sessions = new Map<string, Session>();
  private logins = new Map<string, PendingLogin>();

  private readonly opts: { idleMs: number; maxMs: number; maxSessions: number };
  private readonly now: Clock;

  constructor(opts: { idleMs: number; maxMs: number; maxSessions: number }, now: Clock = Date.now) {
    this.opts = opts;
    this.now = now;
  }

  beginLogin(returnTo: string): PendingLogin {
    this.sweep();
    const login: PendingLogin = {
      id: randomToken(),
      state: randomToken(),
      nonce: randomToken(),
      codeVerifier: randomToken(48),
      returnTo,
      createdAt: this.now(),
    };
    this.logins.set(login.id, login);
    return login;
  }

  /** One use only: the login is removed whether or not the caller succeeds. */
  takeLogin(id: string | undefined): PendingLogin | null {
    if (!id) return null;
    const login = this.logins.get(id);
    this.logins.delete(id);
    if (!login || this.now() - login.createdAt > LOGIN_TTL_MS) return null;
    return login;
  }

  create(fields: Omit<Session, 'id' | 'createdAt' | 'lastSeenAt'>): Session {
    this.sweep();
    if (this.sessions.size >= this.opts.maxSessions) {
      // Evict the least recently used rather than refuse a sign-in.
      let oldest: Session | null = null;
      for (const s of this.sessions.values()) if (!oldest || s.lastSeenAt < oldest.lastSeenAt) oldest = s;
      if (oldest) this.sessions.delete(oldest.id);
    }
    const t = this.now();
    const session: Session = { ...fields, id: randomToken(), createdAt: t, lastSeenAt: t };
    this.sessions.set(session.id, session);
    return session;
  }

  /** Returns the live session and marks it used, or null if absent or expired (and then forgets it). */
  get(id: string | undefined): Session | null {
    if (!id) return null;
    const session = this.sessions.get(id);
    if (!session) return null;
    const t = this.now();
    if (t - session.lastSeenAt > this.opts.idleMs || t - session.createdAt > this.opts.maxMs) {
      this.sessions.delete(id);
      return null;
    }
    session.lastSeenAt = t;
    return session;
  }

  destroy(id: string | undefined): Session | null {
    if (!id) return null;
    const session = this.sessions.get(id) ?? null;
    this.sessions.delete(id);
    return session;
  }

  size(): number {
    return this.sessions.size;
  }

  private sweep(): void {
    const t = this.now();
    for (const [id, s] of this.sessions) {
      if (t - s.lastSeenAt > this.opts.idleMs || t - s.createdAt > this.opts.maxMs) this.sessions.delete(id);
    }
    for (const [id, l] of this.logins) if (t - l.createdAt > LOGIN_TTL_MS) this.logins.delete(id);
  }
}
