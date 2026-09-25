import type { CodeableConcept, Narrative } from '../fhir/types';

/**
 * Dates are shown the way the PHP dashboard shows them with OpenEMR's
 * default date format (`date_display_format = 0`, Y-m-d): the literal
 * calendar date, no time-zone conversion. FHIR carries OpenEMR's wall-clock
 * value as UTC; converting it to the browser's zone would move dates across
 * midnight and disagree with the PHP page.
 */
export function ymd(value: string | undefined | null): string {
  if (!value) return '';
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return m ? m[1]! : '';
}

/** `2020-07-11T16:07:18+00:00` -> `2020-07-11 16:07:18`, as the PHP prescriptions table prints `date_added`. */
export function ymdHms(value: string | undefined | null): string {
  if (!value) return '';
  const m = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}:\d{2}))?/.exec(value);
  if (!m) return '';
  return m[2] ? `${m[1]} ${m[2]}` : m[1]!;
}

const ENTITIES: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&apos;': "'", '&nbsp;': ' ' };

/** Plain text from an XHTML narrative. The result is rendered as text, never as HTML. */
export function narrativeText(text: Narrative | undefined): string {
  if (!text?.div) return '';
  return text.div
    .replace(/<[^>]*>/g, ' ')
    .replace(/&[a-z#0-9]+;/gi, (e) => ENTITIES[e.toLowerCase()] ?? e)
    .replace(/\s+/g, ' ')
    .trim();
}

/** A code's human name: `text`, else the first coding with a display. */
export function conceptText(concept: CodeableConcept | undefined): string {
  if (!concept) return '';
  if (concept.text?.trim()) return concept.text.trim();
  return concept.coding?.find((c) => c.display?.trim())?.display?.trim() ?? '';
}

/** A coding display first (MedicationRequest and AllergyIntolerance put the name there). */
export function codingDisplayFirst(concept: CodeableConcept | undefined): string {
  if (!concept) return '';
  return concept.coding?.find((c) => c.display?.trim() && c.code !== 'unknown')?.display?.trim() ?? concept.text?.trim() ?? '';
}

export function statusCode(concept: CodeableConcept | undefined): string | undefined {
  return concept?.coding?.[0]?.code ?? undefined;
}

/**
 * OpenEMR's age display (`PatientService::getPatientAge`): whole months up
 * to 24 months ("7 month"), whole years after. `asOf` is Y-m-d.
 */
export function ageDisplay(dob: string, asOf: string): string {
  const d = /^(\d{4})-(\d{2})-(\d{2})/.exec(dob);
  const n = /^(\d{4})-(\d{2})-(\d{2})/.exec(asOf);
  if (!d || !n) return '';
  const [dy, dm, dd] = [Number(d[1]), Number(d[2]), Number(d[3])];
  const [ny, nm, nd] = [Number(n[1]), Number(n[2]), Number(n[3])];
  const dayDiff = nd - dd;
  const monthDiff = nm - dm;
  let months = ny * 12 + nm - (dy * 12 + dm);
  if (dayDiff < 0) months -= 1;
  if (months > 24) {
    let age = ny - dy;
    if ((monthDiff === 0 && dayDiff < 0) || monthDiff < 0) age -= 1;
    return String(age);
  }
  return `${months} month`;
}

export function todayYmd(now: Date = new Date()): string {
  const p = (x: number) => String(x).padStart(2, '0');
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}
