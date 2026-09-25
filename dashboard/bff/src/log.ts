/**
 * The BFF's only logger. It takes a closed set of fields, so there is no
 * argument through which a token, a query string, a patient id or an
 * upstream response body could reach the log. Routes are logged as their
 * template (`/fhir/:type/:id`), never as the requested path.
 */
export interface RequestLine {
  method: string;
  route: string;
  status: number;
  ms: number;
}

/** Operational events. `code` must be one of our own short identifiers. */
export interface EventLine {
  event: 'login_started' | 'login_completed' | 'login_failed' | 'token_refreshed' | 'refresh_failed' | 'logout' | 'upstream_error' | 'started';
  code?: string;
}

export type LogSink = (line: string) => void;

let sink: LogSink = (line) => process.stdout.write(line + '\n');

export function setLogSink(next: LogSink): void {
  sink = next;
}

const CODE = /^[a-z0-9_]{1,48}$/;

export function logRequest(line: RequestLine): void {
  sink(JSON.stringify({ t: new Date().toISOString(), method: line.method, route: line.route, status: line.status, ms: Math.round(line.ms) }));
}

export function logEvent(line: EventLine): void {
  const code = line.code && CODE.test(line.code) ? line.code : line.code ? 'unrecognised' : undefined;
  sink(JSON.stringify({ t: new Date().toISOString(), event: line.event, ...(code ? { code } : {}) }));
}
