/**
 * The slice of FHIR R4 the dashboard reads, typed from the elements
 * OpenEMR actually emits (checked against live responses; see
 * docs/dashboard/PARITY_INVENTORY.md §2). Every element is optional because
 * OpenEMR omits what it does not have.
 */

export interface Coding {
  system?: string;
  code?: string;
  display?: string;
}

export interface CodeableConcept {
  coding?: Coding[];
  text?: string;
}

export interface Reference {
  reference?: string;
  type?: string;
  display?: string;
}

export interface Quantity {
  value?: number;
  unit?: string;
  code?: string;
  system?: string;
}

export interface Period {
  start?: string;
  end?: string;
}

export interface Narrative {
  status?: string;
  div?: string;
}

export interface Identifier {
  use?: string;
  type?: CodeableConcept;
  system?: string;
  value?: string;
}

export interface HumanName {
  use?: string;
  family?: string;
  given?: string[];
  prefix?: string[];
  suffix?: string[];
  text?: string;
}

export interface Extension {
  url: string;
  valueCode?: string;
  valueString?: string;
}

interface Resource<T extends string> {
  resourceType: T;
  id?: string;
  meta?: { lastUpdated?: string; versionId?: string };
  text?: Narrative;
}

export interface Patient extends Resource<'Patient'> {
  identifier?: Identifier[];
  active?: boolean;
  name?: HumanName[];
  gender?: 'male' | 'female' | 'other' | 'unknown';
  birthDate?: string;
  deceasedBoolean?: boolean;
  deceasedDateTime?: string;
  extension?: Extension[];
}

export interface AllergyIntolerance extends Resource<'AllergyIntolerance'> {
  clinicalStatus?: CodeableConcept;
  verificationStatus?: CodeableConcept;
  category?: string[];
  criticality?: 'low' | 'high' | 'unable-to-assess';
  code?: CodeableConcept;
  patient?: Reference;
  onsetDateTime?: string;
  recordedDate?: string;
  reaction?: { manifestation?: CodeableConcept[]; severity?: string; description?: string }[];
}

export interface Condition extends Resource<'Condition'> {
  clinicalStatus?: CodeableConcept;
  verificationStatus?: CodeableConcept;
  category?: CodeableConcept[];
  code?: CodeableConcept;
  subject?: Reference;
  onsetDateTime?: string;
  abatementDateTime?: string;
  recordedDate?: string;
}

export interface Dosage {
  text?: string;
  timing?: { code?: CodeableConcept };
  route?: CodeableConcept;
  doseAndRate?: { doseQuantity?: Quantity }[];
}

export interface MedicationRequest extends Resource<'MedicationRequest'> {
  status?: string;
  intent?: string;
  category?: CodeableConcept[];
  medicationCodeableConcept?: CodeableConcept;
  subject?: Reference;
  authoredOn?: string;
  requester?: Reference;
  // OpenEMR sends `[[]]` for a prescription with no dosage, so each entry may be an array.
  dosageInstruction?: (Dosage | unknown[])[] | null;
  dispenseRequest?: { numberOfRepeatsAllowed?: number; quantity?: Quantity };
  note?: { text?: string }[];
}

export interface CareTeamParticipant {
  role?: CodeableConcept[];
  member?: Reference;
  onBehalfOf?: Reference;
  period?: Period;
}

export interface CareTeam extends Resource<'CareTeam'> {
  status?: string;
  name?: string;
  subject?: Reference;
  participant?: CareTeamParticipant[];
}

export interface Practitioner extends Resource<'Practitioner'> {
  name?: HumanName[];
}

export interface Organization extends Resource<'Organization'> {
  name?: string;
}

export interface ObservationReferenceRange {
  low?: Quantity;
  high?: Quantity;
  text?: string;
}

export interface Observation extends Resource<'Observation'> {
  status?: string;
  category?: CodeableConcept[];
  code?: CodeableConcept;
  subject?: Reference;
  effectiveDateTime?: string;
  issued?: string;
  valueQuantity?: Quantity;
  valueString?: string;
  valueCodeableConcept?: CodeableConcept;
  interpretation?: CodeableConcept[];
  referenceRange?: ObservationReferenceRange[];
  note?: { text?: string }[];
}

export interface Bundle<T> {
  resourceType: 'Bundle';
  type?: string;
  total?: number;
  entry?: { fullUrl?: string; resource?: T }[];
  link?: { relation?: string; url?: string }[];
}

export function entries<T>(bundle: Bundle<T> | null | undefined): T[] {
  return (bundle?.entry ?? []).map((e) => e.resource).filter((r): r is T => r !== undefined);
}
