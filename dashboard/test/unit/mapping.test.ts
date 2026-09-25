import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { entries, type AllergyIntolerance, type Bundle, type CareTeam, type Condition, type MedicationRequest, type Observation, type Patient } from '../../web/src/fhir/types';
import { mapPatientHeader, deceasedAgo } from '../../web/src/mapping/patient';
import { mapAllergies, mapMedications, mapPrescriptions, mapProblems } from '../../web/src/mapping/clinical';
import { careTeamReferences, mapCareTeams, practitionerName } from '../../web/src/mapping/careTeam';
import { mapLabs, mapTrends, rangeText, trendKey } from '../../web/src/mapping/labs';
import { ageDisplay, narrativeText, ymd, ymdHms } from '../../web/src/mapping/format';

interface Fixture {
  Patient: Patient;
  AllergyIntolerance: Bundle<AllergyIntolerance>;
  Condition: Bundle<Condition>;
  MedicationRequest: Bundle<MedicationRequest>;
  CareTeam: Bundle<CareTeam>;
  Observation: Bundle<Observation>;
}

const load = (name: string): Fixture => JSON.parse(readFileSync(new URL(`../fixtures/${name}.json`, import.meta.url), 'utf8')) as Fixture;
const pid20 = load('pid20-dee580');
const pid1 = load('pid1-phil-belford');
const pid16 = load('pid16-colby655');
const pid49 = load('pid49-tonja658-d54');
const TODAY = '2026-09-24';

// Expected values are what the PHP dashboard rendered for the same patient on
// the same day (docs/dashboard/inventory/, captured 2026-09-24).

describe('patient header', () => {
  it('matches the PHP patient bar for Dee580 Homenick806', () => {
    const h = mapPatientHeader(pid20.Patient, TODAY);
    expect(h).toMatchObject({ name: 'Dee580 Homenick806', mrn: '20', dob: '2004-08-24', age: '22', ageLabel: 'Age', sex: 'Male', active: true, deceased: null });
  });

  it('takes the MRN from the PT identifier, never the Social Security number', () => {
    const h = mapPatientHeader(pid1.Patient, TODAY);
    expect(pid1.Patient.identifier?.some((i) => i.value === '999-99-9999')).toBe(true);
    expect(h.mrn).toBe('1');
    expect(h.name).toBe('Phil Belford');
    expect(h.age).toBe('54');
  });

  it('prints fname lname without the middle name, as PHP does', () => {
    const p: Patient = { resourceType: 'Patient', name: [{ use: 'official', given: ['Susan', 'Ardmore'], family: 'Underwood' }] };
    expect(mapPatientHeader(p, TODAY).name).toBe('Susan Underwood');
  });

  it('reports no MRN rather than falling back to another identifier', () => {
    const p: Patient = { resourceType: 'Patient', identifier: [{ type: { coding: [{ code: 'SS' }] }, value: '999-99-9999' }] };
    expect(mapPatientHeader(p, TODAY).mrn).toBeNull();
  });

  it('shows deceased with the PHP wording and age at death', () => {
    const p: Patient = { resourceType: 'Patient', birthDate: '1950-03-10', deceasedDateTime: '2026-09-20T00:00:00+00:00', active: true };
    const h = mapPatientHeader(p, TODAY);
    expect(h.deceased).toEqual({ date: '2026-09-20', ago: '4 days ago' });
    expect(h.ageLabel).toBe('Age at death');
    expect(h.age).toBe('76');
  });

  it('ports deceasedDays() boundaries', () => {
    expect(deceasedAgo('2026-09-24', TODAY)).toBe('Today');
    expect(deceasedAgo('2026-09-23', TODAY)).toBe('1 day ago');
    expect(deceasedAgo('2026-06-27', TODAY)).toBe('89 days ago');
    expect(deceasedAgo('2026-06-26', TODAY)).toBe('~3 months ago');
    expect(deceasedAgo('2020-01-01', TODAY)).toBe('More than 7 years ago');
  });
});

describe('allergies', () => {
  it('lists the seven active allergies in service order', () => {
    expect(mapAllergies(entries(pid20.AllergyIntolerance)).map((a) => a.title)).toEqual([
      'Allergic disposition (finding)',
      'Mold (organism)',
      'House dust mite (organism)',
      'Animal dander (substance)',
      'Tree pollen (substance)',
      'Eggs (edible) (substance)',
      'Shellfish (substance)',
    ]);
  });

  it('reads an uncoded allergy name from the narrative', () => {
    const [a] = mapAllergies(entries(pid1.AllergyIntolerance));
    expect(a?.title).toBe('penicillin');
    // OpenEMR sends no criticality for this row; PHP prints "()" (G1).
    expect(a?.criticality).toBeNull();
  });

  it('shows criticality, highlights high, and keeps the PHP tooltip shape', () => {
    const list: AllergyIntolerance[] = [
      { resourceType: 'AllergyIntolerance', id: 'h', criticality: 'high', code: { coding: [{ display: 'Peanut' }] }, reaction: [{ manifestation: [{ text: 'Hives' }] }] },
      { resourceType: 'AllergyIntolerance', id: 'l', criticality: 'low', code: { text: 'Latex' } },
    ];
    const [h, l] = mapAllergies(list);
    expect(h).toMatchObject({ criticality: 'High Risk', highlight: true, tooltip: 'Peanut Reaction: Hives - High Risk' });
    expect(l).toMatchObject({ title: 'Latex', criticality: 'Low Risk', highlight: false });
  });

  it('drops inactive and resolved allergies', () => {
    const status = (code: string): AllergyIntolerance => ({ resourceType: 'AllergyIntolerance', id: code, clinicalStatus: { coding: [{ code }] }, code: { text: code } });
    expect(mapAllergies([status('active'), status('inactive'), status('resolved')]).map((a) => a.id)).toEqual(['active']);
  });
});

describe('medical problems', () => {
  it('shows the same active problems as PHP, in begdate order', () => {
    expect(mapProblems(entries(pid20.Condition)).map((p) => p.title)).toEqual(['Educated to high school level (finding)', 'Part-time employment (finding)']);
    expect(mapProblems(entries(pid16.Condition)).map((p) => p.title)).toEqual([
      'Seasonal allergic rhinitis (disorder)',
      'Risk activity involvement (finding)',
      'Chronic pain (finding)',
      'Chronic low back pain (finding)',
      'Medication review due (situation)',
      'Received higher education (finding)',
      'Part-time employment (finding)',
    ]);
    expect(mapProblems(entries(pid1.Condition)).map((p) => p.title)).toEqual(['HTN', 'Chronic Renal Insuficiency']);
  });

  it('keeps recurrence, drops resolved, and sorts a missing onset first (MySQL NULL order)', () => {
    const c = (id: string, code: string, onset?: string): Condition => ({ resourceType: 'Condition', id, clinicalStatus: { coding: [{ code }] }, code: { text: id }, onsetDateTime: onset });
    expect(mapProblems([c('b', 'active', '2020-01-01'), c('r', 'resolved', '2019-01-01'), c('rec', 'recurrence', '2021-01-01'), c('none', 'active')]).map((p) => p.id)).toEqual([
      'none',
      'b',
      'rec',
    ]);
  });
});

describe('medications and prescriptions', () => {
  it('splits MedicationRequest by intent exactly as the PHP cards split lists and prescriptions', () => {
    const all = entries(pid20.MedicationRequest);
    expect(all).toHaveLength(14);
    expect(mapMedications(all).map((m) => m.title)).toEqual(['Loratadine 5 MG Chewable Tablet', 'NDA020800 0.3 ML Epinephrine 1 MG/ML Auto-Injector']);
    expect(mapPrescriptions(all).map((r) => [r.drug, r.refills, r.filled])).toEqual([
      ['Ibuprofen 200 MG Oral Tablet', '0', '2020-07-11 16:07:18'],
      ['sodium fluoride 0.0272 MG/MG Oral Gel', '0', '2016-10-11 21:48:30'],
      ['Penicillin V Potassium 250 MG Oral Tablet', '0', '2016-09-20 04:28:18'],
      ['120 ACTUAT fluticasone propionate 0.044 MG/ACTUAT Metered Dose Inhaler [Flovent]', '0', '2009-01-18 15:28:18'],
      ['albuterol 0.83 MG/ML Inhalation Solution', '0', '2009-01-18 15:28:18'],
      ['Loratadine 5 MG Chewable Tablet', '0', '2005-12-15 03:52:14'],
      ['NDA020800 0.3 ML Epinephrine 1 MG/ML Auto-Injector', '0', '2005-12-15 03:52:14'],
    ]);
  });

  it('shows the hand-entered medications and an empty prescription list for Phil Belford', () => {
    const all = entries(pid1.MedicationRequest);
    expect(mapMedications(all).map((m) => m.title)).toEqual(['Norvasc', 'Lisinopril']);
    expect(mapPrescriptions(all)).toEqual([]);
  });

  it("leaves Details empty where OpenEMR drops a numeric dosage (G3), and survives OpenEMR's [[]] dosage", () => {
    const [first] = mapPrescriptions(entries(pid20.MedicationRequest));
    expect(first?.details).toBe('');
    expect(first?.quantity).toBe('');
  });

  it('renders dose, text, route and timing when present; excludes stopped scripts and ended list meds', () => {
    const rx: MedicationRequest = {
      resourceType: 'MedicationRequest',
      id: 'rx',
      intent: 'order',
      status: 'active',
      medicationCodeableConcept: { coding: [{ display: 'Amoxicillin 500 MG' }] },
      dosageInstruction: [{ text: 'twice daily', route: { text: 'Oral' }, doseAndRate: [{ doseQuantity: { value: 1, unit: 'tablet' } }] }],
      dispenseRequest: { quantity: { value: 20 }, numberOfRepeatsAllowed: 1 },
      authoredOn: '2026-09-01T08:00:00+00:00',
    };
    const stopped: MedicationRequest = { ...rx, id: 'stopped', status: 'stopped' };
    const endedPlan: MedicationRequest = { resourceType: 'MedicationRequest', id: 'plan', intent: 'plan', status: 'completed', medicationCodeableConcept: { text: 'Old' } };
    expect(mapPrescriptions([rx, stopped, endedPlan])).toEqual([{ id: 'rx', drug: 'Amoxicillin 500 MG', details: '1 tablet twice daily Oral', quantity: '20', refills: '1', filled: '2026-09-01 08:00:00' }]);
    expect(mapMedications([rx, stopped, endedPlan])).toEqual([]);
  });

  it('files any intent other than plan under Prescriptions (a prescriptions row keeps its own request_intent)', () => {
    const m = (intent: string): MedicationRequest => ({ resourceType: 'MedicationRequest', id: intent, intent, status: 'active', medicationCodeableConcept: { text: intent, coding: [{ display: intent }] } });
    const all = [m('plan'), m('order'), m('original-order'), m('proposal')];
    expect(mapMedications(all).map((x) => x.id)).toEqual(['plan']);
    expect(mapPrescriptions(all).map((x) => x.id)).toEqual(['order', 'original-order', 'proposal']);
  });

  it('keeps a completed prescription, since PHP shows every active>0 row including ended ones', () => {
    const done: MedicationRequest = { resourceType: 'MedicationRequest', id: 'd', intent: 'order', status: 'completed', medicationCodeableConcept: { coding: [{ display: 'X' }] } };
    expect(mapPrescriptions([done])).toHaveLength(1);
  });
});

describe('care team', () => {
  it('maps the team entered through the PHP form, dropping the facility-only participant', () => {
    const [team] = mapCareTeams(entries(pid20.CareTeam));
    expect(team).toMatchObject({ name: 'Primary care team', status: 'active', statusLabel: 'Active', statusBadge: 'badge-success' });
    expect(team?.members.map((m) => [m.typeLabel, m.role, m.since])).toEqual([
      ['Provider', 'Physician', '2024-01-15'],
      ['Provider', 'Nurse', '2025-03-01'],
    ]);
    const refs = careTeamReferences([team!]);
    expect(refs.filter((r) => r.startsWith('Practitioner/'))).toHaveLength(2);
    expect(refs.filter((r) => r.startsWith('Organization/'))).toHaveLength(1);
  });

  it('is empty when OpenEMR has no team', () => {
    expect(mapCareTeams(entries(pid1.CareTeam))).toEqual([]);
  });

  it('names a practitioner "Family, Given" like the PHP card', () => {
    expect(practitionerName({ resourceType: 'Practitioner', name: [{ use: 'official', family: 'Smith', given: ['Billy'] }] })).toBe('Smith, Billy');
  });
});

describe('lab results', () => {
  const rows = mapLabs(entries(pid49.Observation));

  it('includes every Observation, among them the 43 the D54 write-back filed', () => {
    expect(rows).toHaveLength(67);
    expect(rows.filter((r) => r.extractionId?.startsWith('w2x-'))).toHaveLength(43);
  });

  it('shows value, unit, range, flag and date for a written-back result', () => {
    const ferritin = rows.find((r) => r.code === '2276-4' && r.extractionId);
    expect(ferritin).toMatchObject({ value: '6', unit: 'ng/mL', range: '15-150', flag: { label: 'Low', abnormal: true }, date: '2026-09-05', status: 'final' });
    const glucose = rows.find((r) => r.code === '2345-7' && r.extractionId);
    // OpenEMR sends interpretation N with the display "No"; the label comes from the code.
    expect(glucose?.flag).toEqual({ label: 'Normal', abnormal: false });
  });

  it('orders newest first', () => {
    const dates = rows.map((r) => r.date);
    expect([...dates].sort().reverse()).toEqual(dates);
  });

  it('shows Synthea values unrounded and blank range and flag (F-19)', () => {
    const r = mapLabs(entries(pid16.Observation)).find((x) => x.code === '6690-2');
    expect(r?.value).toMatch(/^\d+\.\d{6,}$/);
    expect(r?.range).toBe('');
    expect(r?.flag).toBeNull();
  });

  it('builds a trend only for analytes measured at least twice, oldest first', () => {
    const trends = mapTrends(entries(pid16.Observation));
    for (const t of trends.values()) {
      expect(t.points.length).toBeGreaterThanOrEqual(2);
      expect(t.points.map((p) => p.date)).toEqual([...t.points.map((p) => p.date)].sort());
    }
    expect(mapTrends(entries(pid49.Observation)).get(trendKey('2276-4', 'ng/mL'))).toBeUndefined(); // one ferritin only
  });

  it('never joins two unit strings in one trend (Synthea 10*3/uL vs the uploaded report K/uL)', () => {
    const trends = mapTrends(entries(pid49.Observation));
    const synthea = trends.get(trendKey('6690-2', '10*3/uL'));
    expect(synthea?.points.length).toBeGreaterThanOrEqual(2);
    expect(trends.get(trendKey('6690-2', 'K/uL'))).toBeUndefined(); // one K/uL value: no series
    for (const t of trends.values()) expect(new Set([t.unit]).size).toBe(1);
  });

  it('prints one-sided and text ranges', () => {
    const o = (referenceRange: Observation['referenceRange']): Observation => ({ resourceType: 'Observation', referenceRange });
    expect(rangeText(o([{ low: { value: 60 } }]))).toBe('>= 60');
    expect(rangeText(o([{ high: { value: 200 } }]))).toBe('<= 200');
    expect(rangeText(o([{ text: 'negative' }]))).toBe('negative');
    expect(rangeText(o(undefined))).toBe('');
  });
});

describe('formatting', () => {
  it('ports getPatientAge(): months up to 24, then whole years', () => {
    expect(ageDisplay('2026-02-14', TODAY)).toBe('7 month');
    expect(ageDisplay('2024-09-25', TODAY)).toBe('23 month');
    expect(ageDisplay('2024-09-24', TODAY)).toBe('24 month');
    expect(ageDisplay('2024-08-24', TODAY)).toBe('2');
    expect(ageDisplay('2004-09-25', TODAY)).toBe('21');
    expect(ageDisplay('2004-09-24', TODAY)).toBe('22');
  });

  it('keeps the literal date and time, with no time-zone shift', () => {
    expect(ymd('2016-09-20T23:59:00-05:00')).toBe('2016-09-20');
    expect(ymdHms('2020-07-11T16:07:18+00:00')).toBe('2020-07-11 16:07:18');
    expect(ymdHms('2020-07-11')).toBe('2020-07-11');
  });

  it('extracts narrative text without executing or keeping markup', () => {
    expect(narrativeText({ div: "<div xmlns='http://www.w3.org/1999/xhtml'>A &amp; B <script>x</script></div>" })).toBe('A & B x');
  });
});
