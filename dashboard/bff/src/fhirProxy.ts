/**
 * What the dashboard may read, and nothing more. This is the BFF's whole
 * authorization surface on top of OpenEMR's own scope and ACL checks.
 */

/** One entry per FHIR type the SPA draws. Adding a card means adding a line here and a scope in config.ts. */
export const ALLOWED_TYPES = new Set([
  'Patient',
  'AllergyIntolerance',
  'Condition',
  'MedicationRequest',
  'CareTeam',
  'Observation',
  'Practitioner',
  'Organization',
]);

/**
 * Search parameters the SPA sends. `_include`, `_revinclude`, `_has`,
 * `_contained` and chained parameters are refused by omission: each could
 * make a search return resource types outside the allowlist.
 */
export const ALLOWED_PARAMS = new Set(['patient', '_id', 'category', 'name', 'identifier', '_count']);

const ID = /^[A-Za-z0-9\-.]{1,64}$/;
const MAX_VALUE = 200;

export type ProxyCheck = { ok: true; upstreamPath: string } | { ok: false; status: 400 | 403; error: string };

/** Pure: decides whether a GET may be forwarded, and to which upstream path. */
export function checkFhirRequest(type: string, id: string | undefined, query: URLSearchParams): ProxyCheck {
  if (!ALLOWED_TYPES.has(type)) return { ok: false, status: 403, error: 'resource_type_not_allowed' };
  if (id !== undefined && !ID.test(id)) return { ok: false, status: 400, error: 'invalid_id' };
  const seen = new Set<string>();
  for (const [name, value] of query) {
    if (!ALLOWED_PARAMS.has(name)) return { ok: false, status: 400, error: 'parameter_not_allowed' };
    if (seen.has(name)) return { ok: false, status: 400, error: 'parameter_repeated' };
    if (value.length > MAX_VALUE) return { ok: false, status: 400, error: 'parameter_too_long' };
    seen.add(name);
  }
  if (id !== undefined && seen.size > 0) return { ok: false, status: 400, error: 'parameter_not_allowed' };
  const qs = query.toString();
  return { ok: true, upstreamPath: `/${type}${id !== undefined ? `/${id}` : ''}${qs ? `?${qs}` : ''}` };
}

/** How an upstream status reaches the SPA. Our own error codes; OpenEMR's message text is never passed on. */
export function mapUpstreamStatus(status: number): { status: number; error?: string } {
  if (status >= 200 && status < 300) return { status };
  if (status === 404) return { status: 404, error: 'not_found' };
  // OpenEMR answers 401 for both an expired token and a missing scope (audit F-08).
  if (status === 401) return { status: 401, error: 'upstream_unauthorized' };
  if (status === 403) return { status: 403, error: 'upstream_forbidden' };
  if (status === 400) return { status: 400, error: 'upstream_bad_request' };
  return { status: 502, error: 'upstream_error' };
}
