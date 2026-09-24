import type { AllergyIntolerance, Condition, Dosage, MedicationRequest } from '../fhir/types';
import { codingDisplayFirst, conceptText, narrativeText, statusCode, ymdHms } from './format';

// ---- Allergies ---------------------------------------------------------------

export interface AllergyView {
  id: string;
  title: string;
  /** FHIR `criticality` label. OpenEMR folds its seven severities into these three (inventory G1). */
  criticality: string | null;
  /** The PHP card highlights severe / life-threatening / fatal; FHIR can only say `high`. */
  highlight: boolean;
  reaction: string;
  /** Mirrors the PHP hover text: "<title> Reaction: <reaction> - <severity>". */
  tooltip: string;
}

const CRITICALITY: Record<string, string> = { low: 'Low Risk', high: 'High Risk', 'unable-to-assess': 'Unable to Assess Risk' };

/** An allergy's name. For an uncoded allergy OpenEMR puts it only in the narrative (Phil Belford's "penicillin"). */
export function allergyTitle(a: AllergyIntolerance): string {
  return codingDisplayFirst(a.code) || narrativeText(a.text) || 'Unnamed allergy';
}

/**
 * Active allergies, in the order OpenEMR returns them (the PHP card uses the
 * same service order). OpenEMR's FHIR status is `active` only while the
 * record has no end date (FhirAllergyIntoleranceService.php:117).
 */
export function mapAllergies(list: AllergyIntolerance[]): AllergyView[] {
  return list
    .filter((a) => {
      const s = statusCode(a.clinicalStatus);
      return s !== 'inactive' && s !== 'resolved';
    })
    .map((a) => {
      const title = allergyTitle(a);
      const criticality = a.criticality ? (CRITICALITY[a.criticality] ?? a.criticality) : null;
      const reaction = (a.reaction ?? [])
        .flatMap((r) => r.manifestation ?? [])
        .map((m) => conceptText(m))
        .filter(Boolean)
        .join(', ');
      return {
        id: a.id ?? title,
        title,
        criticality,
        highlight: a.criticality === 'high',
        reaction,
        tooltip: `${title} Reaction: ${reaction} - ${criticality ?? ''}`,
      };
    });
}

// ---- Medical problems -------------------------------------------------------

export interface ProblemView {
  id: string;
  title: string;
}

const ACTIVE_CONDITION = new Set(['active', 'recurrence', 'relapse']);

/**
 * Active problem-list items ordered by onset ascending, the PHP card's
 * `ORDER BY lists.begdate` (MySQL sorts a missing date first, and so do we).
 */
export function mapProblems(list: Condition[]): ProblemView[] {
  return list
    .filter((c) => (c.category ?? []).length === 0 || c.category!.some((cat) => cat.coding?.some((x) => x.code === 'problem-list-item')))
    .filter((c) => ACTIVE_CONDITION.has(statusCode(c.clinicalStatus) ?? 'active'))
    .map((c, index) => ({ c, index }))
    .sort((a, b) => {
      const x = a.c.onsetDateTime ?? '';
      const y = b.c.onsetDateTime ?? '';
      return x === y ? a.index - b.index : x < y ? -1 : 1;
    })
    .map(({ c }) => ({ id: c.id ?? '', title: conceptText(c.code) || narrativeText(c.text) || 'Unnamed problem' }));
}

// ---- Medications and prescriptions --------------------------------------------

/**
 * OpenEMR emits one MedicationRequest per `lists` medication row
 * (intent `plan`) and one per `prescriptions` row (intent `order`, or the
 * row's own `request_intent`). The table a row came from is not in the
 * resource, so intent is the only discriminator (inventory §2).
 */
export function isListMedication(m: MedicationRequest): boolean {
  return m.intent === 'plan';
}

function dosages(m: MedicationRequest): Dosage[] {
  return (m.dosageInstruction ?? []).filter((d): d is Dosage => !!d && !Array.isArray(d) && typeof d === 'object');
}

export interface MedicationView {
  id: string;
  title: string;
  instructions: string;
}

/** The Medications card: `lists` medications that are active (no end date). */
export function mapMedications(list: MedicationRequest[]): MedicationView[] {
  return list
    .filter((m) => isListMedication(m) && m.status === 'active')
    .map((m) => ({
      id: m.id ?? '',
      title: conceptText(m.medicationCodeableConcept) || narrativeText(m.text) || 'Unnamed medication',
      instructions: dosages(m).map((d) => d.text).find((t) => !!t?.trim()) ?? '',
    }));
}

export interface PrescriptionView {
  id: string;
  drug: string;
  details: string;
  quantity: string;
  refills: string;
  filled: string;
}

function dosageDetails(ds: Dosage[]): string {
  const parts: string[] = [];
  for (const d of ds) {
    const q = d.doseAndRate?.[0]?.doseQuantity;
    if (q?.value !== undefined) parts.push(`${q.value}${q.unit ? ' ' + q.unit : ''}`);
    if (d.text) parts.push(d.text);
    const route = conceptText(d.route);
    if (route) parts.push(route);
    const timing = conceptText(d.timing?.code);
    if (timing) parts.push(timing);
  }
  return parts.join(' ');
}

/**
 * The Prescriptions card: prescriptions the PHP page counts as active
 * (`active > 0`), which in FHIR is status `active` or, once an end date is
 * set, `completed`. Newest first by `authoredOn` (= `date_added`); PHP sorts
 * by `date_modified` first, which FHIR does not carry (inventory G4).
 */
export function mapPrescriptions(list: MedicationRequest[]): PrescriptionView[] {
  return list
    .filter((m) => !isListMedication(m) && (m.status === 'active' || m.status === 'completed'))
    .map((m, index) => ({ m, index }))
    .sort((a, b) => {
      const x = a.m.authoredOn ?? '';
      const y = b.m.authoredOn ?? '';
      return x === y ? a.index - b.index : x > y ? -1 : 1;
    })
    .map(({ m }) => ({
      id: m.id ?? '',
      drug: codingDisplayFirst(m.medicationCodeableConcept) || narrativeText(m.text) || 'Unnamed drug',
      details: dosageDetails(dosages(m)),
      quantity: m.dispenseRequest?.quantity?.value !== undefined ? String(m.dispenseRequest.quantity.value) : '',
      refills: m.dispenseRequest?.numberOfRepeatsAllowed !== undefined ? String(m.dispenseRequest.numberOfRepeatsAllowed) : '',
      filled: ymdHms(m.authoredOn),
    }));
}
