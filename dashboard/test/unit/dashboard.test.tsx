// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ApiError, type Fetcher } from '../../web/src/api';
import { Dashboard } from '../../web/src/components/Dashboard';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fixture = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/pid20-dee580.json'), 'utf8')) as Record<string, unknown>;
const empty = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/pid1-phil-belford.json'), 'utf8')) as Record<string, unknown>;
const PATIENT = 'a2c373a4-dd96-48f7-b353-6732b98bf04f';

type Plan = Partial<Record<string, 'fail403' | 'fail401' | 'hang' | 'network' | object>>;

/** A fetcher that answers from a fixture, except where the plan says otherwise; it records every path. */
function fakeFetcher(source: Record<string, unknown>, plan: Plan = {}) {
  const paths: string[] = [];
  const fetcher: Fetcher = async <T,>(path: string): Promise<T> => {
    paths.push(path);
    const type = path.slice(1).split(/[/?]/)[0]!;
    const p = plan[type];
    if (p === 'hang') return new Promise<T>(() => {});
    if (p === 'fail403') throw new ApiError(403, 'upstream_forbidden');
    if (p === 'fail401') throw new ApiError(401, 'upstream_unauthorized');
    if (p === 'network') throw new ApiError(0, 'network');
    if (p && typeof p === 'object') return p as T;
    if (type === 'Practitioner' || type === 'Organization') throw new ApiError(403, 'upstream_forbidden');
    return source[type] as T;
  };
  return { fetcher, paths };
}

let root: Root | null = null;
let host: HTMLElement;

async function render(fetcher: Fetcher) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(<Dashboard patientId={PATIENT} fetcher={fetcher} today="2026-09-24" />);
  });
  // Let every settled promise flush.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  host?.remove();
});

const card = (id: string) => host.querySelector(`[data-card="${id}"]`) as HTMLElement;
const CARDS = ['allergy_ps_expand', 'medical_problem_ps_expand', 'medication_ps_expand', 'prescriptions_ps_expand', 'careteam_ps_expand', 'labs_ps_expand'];

describe('Dashboard', () => {
  it('starts all six section reads in the first render, before any has answered', async () => {
    const { fetcher, paths } = fakeFetcher(fixture, { Patient: 'hang', AllergyIntolerance: 'hang', Condition: 'hang', MedicationRequest: 'hang', CareTeam: 'hang', Observation: 'hang' });
    await render(fetcher);
    expect(paths.map((p) => p.split(/[/?]/)[1]).sort()).toEqual(['AllergyIntolerance', 'CareTeam', 'Condition', 'MedicationRequest', 'Observation', 'Patient']);
    for (const id of CARDS) expect(card(id).querySelector('[data-state="loading"]')).not.toBeNull();
  });

  it('renders every card from live-shaped data', async () => {
    await render(fakeFetcher(fixture).fetcher);
    expect(host.querySelector('[data-field="name"]')?.textContent).toContain('Dee580 Homenick806');
    expect(host.querySelector('[data-field="mrn"]')?.textContent).toBe('20');
    expect(card('allergy_ps_expand').querySelectorAll('.list-group-item')).toHaveLength(7);
    expect(card('prescriptions_ps_expand').querySelectorAll('tbody tr')).toHaveLength(7);
    expect(card('careteam_ps_expand').textContent).toContain('Primary care team');
    expect(card('careteam_ps_expand').textContent).toContain('Not available to your role');
    expect(card('labs_ps_expand').querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  });

  it('contains a failed card: the error shows in that card and every other card still renders', async () => {
    await render(fakeFetcher(fixture, { AllergyIntolerance: 'fail403', Observation: 'network' }).fetcher);
    expect(card('allergy_ps_expand').querySelector('[data-state="error"]')?.textContent).toMatch(/role does not permit/);
    expect(card('labs_ps_expand').querySelector('[data-state="error"]')?.textContent).toMatch(/dashboard server could not be reached/);
    expect(card('medical_problem_ps_expand').textContent).toContain('Part-time employment (finding)');
    expect(card('prescriptions_ps_expand').querySelectorAll('tbody tr')).toHaveLength(7);
    expect(host.querySelector('[data-field="name"]')?.textContent).toContain('Dee580');
  });

  it('keeps the cards when the patient read itself fails', async () => {
    await render(fakeFetcher(fixture, { Patient: 'fail401' }).fetcher);
    expect(host.querySelector('[data-section="patient-header"] [data-state="error"]')?.textContent).toMatch(/refused/);
    expect(card('medication_ps_expand').textContent).toContain('Loratadine');
  });

  it('shows the explicit empty state of every card', async () => {
    const none = { resourceType: 'Bundle', total: 0 };
    await render(fakeFetcher(empty, { AllergyIntolerance: none, Condition: none, MedicationRequest: none, CareTeam: none, Observation: none }).fetcher);
    expect(card('allergy_ps_expand').querySelector('[data-state="empty"]')?.textContent).toBe('Nothing Recorded');
    expect(card('medical_problem_ps_expand').querySelector('[data-state="empty"]')?.textContent).toBe('Nothing Recorded');
    expect(card('medication_ps_expand').querySelector('[data-state="empty"]')?.textContent).toBe('Nothing Recorded');
    expect(card('prescriptions_ps_expand').querySelector('[data-state="empty"]')?.textContent).toBe('None');
    expect(card('careteam_ps_expand').querySelector('[data-state="empty"]')?.textContent).toContain('No care team recorded.');
    expect(card('labs_ps_expand').querySelector('[data-state="empty"]')?.textContent).toBe('No lab data documented.');
  });

  it('never renders the Social Security number', async () => {
    await render(fakeFetcher(empty).fetcher);
    expect(host.textContent).not.toContain('999-99-9999');
    expect(host.querySelector('[data-field="mrn"]')?.textContent).toBe('1');
  });

  it('collapses a card from its title, with the PHP icons', async () => {
    await render(fakeFetcher(fixture).fetcher);
    const a = card('allergy_ps_expand').querySelector('h6 a') as HTMLAnchorElement;
    expect(a.querySelector('i')?.className).toContain('fa-compress');
    await act(async () => a.click());
    expect(host.querySelector('#allergy_ps_expand')?.className).not.toContain('show');
    expect(a.querySelector('i')?.className).toContain('fa-expand');
  });
});
