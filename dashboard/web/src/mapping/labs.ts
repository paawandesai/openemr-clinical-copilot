import type { Observation, Quantity } from '../fhir/types';
import { conceptText, ymd } from './format';

export type Flag = { label: string; abnormal: boolean } | null;

export interface LabRowView {
  id: string;
  date: string;
  code: string;
  test: string;
  value: string;
  unit: string;
  range: string;
  flag: Flag;
  status: string;
  /** The D54 write-back's extraction id (`w2x-…`) when this result came from an uploaded report. */
  extractionId: string | null;
}

export interface TrendPoint {
  date: string;
  value: number;
}

export interface LabTrend {
  code: string;
  test: string;
  unit: string;
  points: TrendPoint[];
  low: number | null;
  high: number | null;
}

/** HL7 v3 ObservationInterpretation. OpenEMR sends `N` with its own list title "No", so the label comes from the code. */
const INTERPRETATION: Record<string, { label: string; abnormal: boolean }> = {
  N: { label: 'Normal', abnormal: false },
  L: { label: 'Low', abnormal: true },
  H: { label: 'High', abnormal: true },
  LL: { label: 'Critical low', abnormal: true },
  HH: { label: 'Critical high', abnormal: true },
  A: { label: 'Abnormal', abnormal: true },
  AA: { label: 'Critical abnormal', abnormal: true },
};

function num(q: Quantity | undefined): number | null {
  return typeof q?.value === 'number' && Number.isFinite(q.value) ? q.value : null;
}

/**
 * The reference range as OpenEMR sends it. It emits one only when the stored
 * range parses as two non-zero numbers (inventory G5), so a one-sided
 * range is rare; it is shown as a bound if it ever arrives.
 */
export function rangeText(o: Observation): string {
  const r = o.referenceRange?.[0];
  if (!r) return '';
  if (r.text) return r.text;
  const low = num(r.low);
  const high = num(r.high);
  if (low !== null && high !== null) return `${low}-${high}`;
  if (low !== null) return `>= ${low}`;
  if (high !== null) return `<= ${high}`;
  return '';
}

export function flagOf(o: Observation): Flag {
  const i = o.interpretation?.[0];
  if (!i) return null;
  const code = i.coding?.find((c) => c.code)?.code;
  if (code && INTERPRETATION[code]) return INTERPRETATION[code]!;
  const text = conceptText(i);
  return text ? { label: text, abnormal: true } : null;
}

function valueOf(o: Observation): { value: string; unit: string } {
  if (o.valueQuantity) {
    return { value: o.valueQuantity.value !== undefined ? String(o.valueQuantity.value) : '', unit: o.valueQuantity.unit ?? o.valueQuantity.code ?? '' };
  }
  if (o.valueString !== undefined) return { value: o.valueString, unit: '' };
  if (o.valueCodeableConcept) return { value: conceptText(o.valueCodeableConcept), unit: '' };
  return { value: '', unit: '' };
}

function codeOf(o: Observation): string {
  return o.code?.coding?.find((c) => c.code)?.code ?? '';
}

/**
 * Laboratory results, newest first; within a date, in the order OpenEMR
 * returned them (panel order). Values are shown exactly as stored, never
 * rounded: Synthea's 15-digit values are what the chart holds.
 */
export function mapLabs(list: Observation[]): LabRowView[] {
  return list
    .map((o, index) => ({ o, index }))
    .sort((a, b) => {
      const x = a.o.effectiveDateTime ?? '';
      const y = b.o.effectiveDateTime ?? '';
      return x === y ? a.index - b.index : x > y ? -1 : 1;
    })
    .map(({ o }) => {
      const { value, unit } = valueOf(o);
      const note = o.note?.map((n) => n.text ?? '').find((t) => t.startsWith('w2x-')) ?? null;
      return {
        id: o.id ?? '',
        date: ymd(o.effectiveDateTime ?? o.issued),
        code: codeOf(o),
        test: conceptText(o.code) || 'Unnamed test',
        value,
        unit,
        range: rangeText(o),
        flag: flagOf(o),
        status: o.status ?? '',
        extractionId: note,
      };
    });
}

/** A trend's key: one series per LOINC code *and* unit string. */
export function trendKey(code: string, unit: string): string {
  return `${code}|${unit}`;
}

/**
 * Per-analyte series of numeric values, oldest first, for analytes measured
 * at least twice. A series never joins two unit strings: the same LOINC code
 * arrives as `10*3/uL` from Synthea and `K/uL` from an uploaded report, and
 * the dashboard does not convert units, so those are two series.
 */
export function mapTrends(list: Observation[]): Map<string, LabTrend> {
  const byCode = new Map<string, LabTrend>();
  for (const o of list) {
    const code = codeOf(o);
    const v = num(o.valueQuantity);
    const date = ymd(o.effectiveDateTime);
    if (!code || v === null || !date) continue;
    const unit = o.valueQuantity?.unit ?? o.valueQuantity?.code ?? '';
    const key = trendKey(code, unit);
    let t = byCode.get(key);
    if (!t) {
      t = { code, test: conceptText(o.code), unit, points: [], low: null, high: null };
      byCode.set(key, t);
    }
    t.points.push({ date, value: v });
    const r = o.referenceRange?.[0];
    if (r) {
      t.low = num(r.low) ?? t.low;
      t.high = num(r.high) ?? t.high;
    }
  }
  for (const [code, t] of byCode) {
    if (t.points.length < 2) byCode.delete(code);
    else t.points.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  }
  return byCode;
}
