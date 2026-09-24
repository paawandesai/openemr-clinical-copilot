import type { Browser, BrowserContext, Locator, Page } from '@playwright/test';

/** The local synthetic patients the suite compares, by UUID (pids differ between instances; memory note). */
export const PATIENTS = [
  { pid: 20, uuid: 'a2c373a4-dd96-48f7-b353-6732b98bf04f', why: 'care team entered through the PHP form' },
  { pid: 49, uuid: 'a2c37430-0a29-4c57-8195-2c924c2b654a', why: 'lab report written back by D54' },
  { pid: 1, uuid: 'a2c36e54-af42-4c38-b86e-ade616ed5494', why: 'hand-entered demo patient, uncoded allergy, no prescriptions' },
] as const;

export const OPENEMR_WEB = 'http://localhost:8300';
export const USER = { username: 'physician', password: 'physician' };

export interface CardData {
  header: { name: string; mrn: string; dobLine: string };
  allergies: string[];
  problems: string[];
  medications: string[];
  prescriptions: string[][];
  careTeam: { name: string; status: string; rows: string[][] };
  labs: { latestDate: string; rows: number };
}

const text = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, ' ').trim();

/** Text of the first match, or '' when there is none. Never waits for an element that is not there. */
async function firstText(loc: Locator, pick?: (el: Element) => string): Promise<string> {
  if ((await loc.count()) === 0) return '';
  const first = loc.first();
  return text(pick ? await first.evaluate(pick) : await first.innerText());
}

/** Signs in through the dashboard's real OAuth flow and waits until every card has settled. */
export async function openDashboard(page: Page, uuid: string): Promise<{ loginMs: number; settleMs: number }> {
  await page.goto(`/patient/${uuid}`);
  const signIn = page.locator('[data-section=login] a.btn');
  const t0 = Date.now();
  if (await signIn.isVisible().catch(() => false)) {
    await signIn.click();
    await page.waitForURL(/\/oauth2\/default\/provider\/login/);
    await page.fill('input[name=username]', USER.username);
    await page.fill('input[name=password]', USER.password);
    await page.click('button[type=submit]');
    // First sign-in shows OpenEMR's consent page; later ones may skip it.
    await page.waitForURL(/scope-authorize-confirm|localhost:8787\/patient\//);
    if (page.url().includes('scope-authorize-confirm')) await page.click('#authorize-btn');
    await page.waitForURL(new RegExp(`localhost:8787/patient/${uuid}`));
  }
  const loginMs = Date.now() - t0;
  const t1 = Date.now();
  await waitForCards(page);
  return { loginMs, settleMs: Date.now() - t1 };
}

/** Polls with locators: the page's CSP forbids the eval that page.waitForFunction needs. */
export async function waitForCards(page: Page): Promise<void> {
  await page.locator('[data-field=name]').waitFor({ timeout: 20_000 });
  for (let i = 0; i < 200; i++) {
    const busy = await page.locator('[aria-busy=true]').count();
    const loadingNames = await page.locator('[data-card=careteam_ps_expand] .text-muted', { hasText: '…' }).count();
    if (busy === 0 && loadingNames === 0) return;
    await page.waitForTimeout(100);
  }
  throw new Error('cards did not settle');
}

export async function readSpa(page: Page): Promise<CardData> {
  const card = (id: string) => page.locator(`[data-card="${id}"]`);
  const rows = async (id: string, sel: string) => (await card(id).locator(sel).allInnerTexts()).map(text);
  const tableRows = async (id: string) =>
    Promise.all((await card(id).locator('tbody tr').all()).map(async (tr) => (await tr.locator('td').allInnerTexts()).map(text)));
  const labRows = await card('labs_ps_expand').locator('tbody tr').count();
  return {
    header: {
      name: text(await page.locator('[data-field=name] > span').first().innerText()),
      mrn: text(await page.locator('[data-field=mrn]').innerText()),
      dobLine: text(await page.locator('[data-field=dob]').innerText()),
    },
    allergies: await rows('allergy_ps_expand', '.list-group-item:not([data-state])'),
    problems: await rows('medical_problem_ps_expand', '.list-group-item:not([data-state])'),
    medications: await rows('medication_ps_expand', '.list-group-item:not([data-state]) span.font-weight-normal'),
    prescriptions: await tableRows('prescriptions_ps_expand'),
    careTeam: {
      name: await firstText(card('careteam_ps_expand').locator('h5'), (el) => el.childNodes[0]?.textContent ?? ''),
      status: await firstText(card('careteam_ps_expand').locator('h5 .badge')),
      rows: (await tableRows('careteam_ps_expand')).filter((r) => r.length > 1),
    },
    labs: {
      latestDate: labRows ? text(await card('labs_ps_expand').locator('tbody tr td').first().innerText()) : '',
      rows: labRows,
    },
  };
}

/** A separate browser context signed in to OpenEMR itself, for the PHP side of the comparison. */
export async function phpContext(browser: Browser): Promise<BrowserContext> {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  await page.goto(`${OPENEMR_WEB}/interface/login/login.php?site=default`);
  await page.fill('#authUser', USER.username);
  await page.fill('#clearPass', USER.password);
  await Promise.all([page.waitForNavigation(), page.click('#login-button')]);
  // Keep this page: it is the tabbed shell (main.php?token_main=…), which only a fresh login opens.
  shells.set(ctx, page);
  return ctx;
}

const shells = new WeakMap<BrowserContext, Page>();

/** Reads the PHP dashboard for one patient: the patient bar from the tabbed shell, the cards from demographics.php. */
export async function readPhp(ctx: BrowserContext, pid: number): Promise<CardData> {
  const shell = shells.get(ctx);
  if (!shell) throw new Error('phpContext() first');
  await shell.waitForFunction(() => typeof (window as unknown as { navigateTab?: unknown }).navigateTab === 'function');
  await shell.evaluate((p) => {
    const w = window as unknown as { navigateTab: (u: string, n: string, cb: () => void) => void; activateTabByName: (n: string, b: boolean) => void; webroot_url: string };
    w.navigateTab(`${w.webroot_url}/interface/patient_file/summary/demographics.php?set_pid=${p}`, 'pat', () => w.activateTabByName('pat', true));
  }, pid);
  await shell.locator('#attendantData .ptName').waitFor();
  await shell.waitForTimeout(1500);
  const ptName = await shell.locator('#attendantData .ptName').innerText();
  const m = /^(.*)\((\d+)\)\s*$/.exec(text(ptName));
  const dobLine = text(await shell.locator('#attendantData .mt-2 span').first().innerText());

  const page = await ctx.newPage();
  await page.goto(`${OPENEMR_WEB}/interface/patient_file/summary/demographics.php?set_pid=${pid}`);
  await page.locator('#prescriptions_ps_expand').waitFor({ state: 'attached' });
  await page.waitForTimeout(1500);
  const rows = async (sel: string) => (await page.locator(sel).allInnerTexts()).map(text);
  const prescriptions = await Promise.all(
    (await page.locator('#prescriptions_ps_expand tbody tr').all()).map(async (tr) => (await tr.locator('td').allInnerTexts()).map(text)),
  );
  const careRows = await Promise.all(
    (await page.locator('#care_team_table tbody tr').all()).map(async (tr) => (await tr.locator('td .viewOnly').allInnerTexts()).map(text)),
  );
  const labText = await firstText(page.locator('#labdata_ps_expand'));
  const labDate = /\((\d{4}-\d{2}-\d{2})/.exec(labText)?.[1] ?? '';
  const data: CardData = {
    header: { name: text(m?.[1]), mrn: m?.[2] ?? '', dobLine },
    allergies: await rows('#allergy_ps_expand .list-group-item'),
    problems: await rows('#medical_problem_ps_expand .list-group-item'),
    medications: await rows('#medication_ps_expand .list-group-item span.font-weight-normal'),
    prescriptions,
    careTeam: {
      name: await firstText(page.locator('#careteam_ps_expand h5.viewOnly'), (el) => el.childNodes[0]?.textContent ?? ''),
      status: await firstText(page.locator('#careteam_ps_expand h5.viewOnly .badge')),
      rows: careRows.filter((r) => r.length > 1),
    },
    labs: { latestDate: labDate, rows: -1 },
  };
  await page.close();
  return data;
}
