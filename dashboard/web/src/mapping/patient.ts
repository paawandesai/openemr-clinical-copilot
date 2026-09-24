import type { Patient } from '../fhir/types';
import { ageDisplay, ymd } from './format';

export interface PatientHeaderView {
  id: string;
  /** `fname lname`, as the PHP patient bar prints it (no middle name). */
  name: string;
  /** OpenEMR's public patient id (pubpid): the identifier typed `PT`. Null when absent, never another identifier. */
  mrn: string | null;
  dob: string;
  /** "22", or "7 month" under two years; "Age at death" when deceased. */
  age: string;
  ageLabel: 'Age' | 'Age at death';
  /** From `Patient.gender` (`patient_data.sex`). */
  sex: string;
  /** Patient.active as sent. OpenEMR hardcodes it to true (inventory G2). */
  active: boolean | null;
  deceased: { date: string; ago: string } | { date: null; ago: null } | null;
}

const SEX: Record<string, string> = { male: 'Male', female: 'Female', other: 'Other', unknown: 'Unknown' };

function daysBetween(fromYmd: string, toYmd: string): number {
  return Math.round((Date.parse(toYmd + 'T00:00:00Z') - Date.parse(fromYmd + 'T00:00:00Z')) / 86_400_000);
}

/** demographics.php `deceasedDays()`: "Today", "1 day ago", "n days ago", "~n months ago", "More than n years ago". */
export function deceasedAgo(dateYmd: string, todayYmd: string): string {
  const days = daysBetween(dateYmd, todayYmd);
  if (days <= 0) return 'Today';
  if (days === 1) return '1 day ago';
  if (days < 90) return `${days} days ago`;
  if (days < 731) return `~${Math.round(days / 30)} months ago`;
  return `More than ${Math.round(days / 365)} years ago`;
}

export function mapPatientHeader(p: Patient, todayYmd: string): PatientHeaderView {
  const name = p.name?.find((n) => n.use === 'official') ?? p.name?.[0];
  const first = name?.given?.[0] ?? '';
  const display = [first, name?.family ?? ''].filter(Boolean).join(' ') || name?.text || '';
  const mrn = p.identifier?.find((i) => i.type?.coding?.some((c) => c.code === 'PT'))?.value ?? null;
  const dob = ymd(p.birthDate);
  const deathDate = ymd(p.deceasedDateTime);
  const deceased = deathDate
    ? { date: deathDate, ago: deceasedAgo(deathDate, todayYmd) }
    : p.deceasedBoolean === true
      ? { date: null, ago: null }
      : null;
  return {
    id: p.id ?? '',
    name: display,
    mrn,
    dob,
    age: dob ? ageDisplay(dob, deathDate || todayYmd) : '',
    ageLabel: deathDate ? 'Age at death' : 'Age',
    sex: p.gender ? (SEX[p.gender] ?? p.gender) : '',
    active: typeof p.active === 'boolean' ? p.active : null,
    deceased,
  };
}
