import { expect, test } from '@playwright/test';
import { openDashboard, PATIENTS, phpContext, readPhp, readSpa } from './helpers';

/**
 * Every card, against the PHP dashboard for the same patient, on the same
 * local stack at the same moment. Where FHIR forces a difference (the gaps
 * in docs/dashboard/PARITY_INVENTORY.md §3), the assertion says which gap
 * and checks the port's side of it, never skips it.
 */
for (const p of PATIENTS) {
  test(`pid ${p.pid} (${p.why}): every card matches the PHP dashboard`, async ({ page, browser }) => {
    const cspViolations: string[] = [];
    page.on('console', (m) => {
      if (/Content Security Policy/i.test(m.text())) cspViolations.push(m.text());
    });
    await openDashboard(page, p.uuid);
    const spa = await readSpa(page);
    const ctx = await phpContext(browser);
    const php = await readPhp(ctx, p.pid);
    await ctx.close();

    // Guard against a vacuous pass (both sides empty): what the PHP page showed on 2026-09-24.
    // [allergies, problems, medications, prescriptions, care-team members]
    const seen = { 20: [7, 2, 2, 7, 2], 49: [10, 2, 2, 7, 0], 1: [1, 2, 2, 0, 0] }[p.pid]!;
    expect([php.allergies.length, php.problems.length, php.medications.length, php.prescriptions.length, php.careTeam.rows.length]).toEqual(seen);
    expect(php.header.name).not.toBe('');
    if (p.pid !== 1) expect(php.labs.latestDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    // Header: name, MRN (pubpid), "DOB: … Age: …".
    expect(spa.header).toEqual(php.header);

    // Allergies: same items, same order. PHP appends "(<severity>)"; FHIR has
    // only criticality, absent for these rows (G1), so the port adds nothing.
    expect(spa.allergies).toEqual(php.allergies.map((a) => a.replace(/\s*\([^()]*\)$/, '')));

    expect(spa.problems).toEqual(php.problems);
    expect(spa.medications).toEqual(php.medications);

    // Prescriptions: Drug, Refills, Filled match exactly. Details and Qty: PHP
    // prints the numeric dosage "1.00", which OpenEMR's FHIR drops (G3).
    expect(spa.prescriptions.map((r) => [r[0], r[3], r[4]])).toEqual(php.prescriptions.map((r) => [r[0], r[3], r[4]]));
    for (const r of php.prescriptions) expect(r[1]).toMatch(/^(\d+(\.\d+)?)?$/);
    for (const r of spa.prescriptions) expect(r[1]).toBe('');

    // Care Team: team, status, and per member Type, Role, Since. Member and
    // facility names need Practitioner/Organization reads that OpenEMR
    // refuses a physician; member status and note are not in FHIR (G10).
    expect(spa.careTeam.name).toBe(php.careTeam.name);
    expect(spa.careTeam.status).toBe(php.careTeam.status);
    expect(spa.careTeam.rows.map((r) => [r[0], r[2], r[4]])).toEqual(php.careTeam.rows.map((r) => [r[0], r[2], r[4]]));
    for (const r of spa.careTeam.rows) {
      expect(r[1]).toBe('Not available to your role');
      expect(r[3]).toBe('Not available to your role');
    }

    // Labs: the PHP card names only the most recent procedure's date.
    expect(spa.labs.latestDate).toBe(php.labs.latestDate);

    expect(cspViolations).toEqual([]);
  });
}

test('D54 write-back rows render with value, unit, range, flag, date and their source', async ({ page }) => {
  await openDashboard(page, PATIENTS[1].uuid);
  const labs = page.locator('[data-card=labs_ps_expand]');
  await labs.getByRole('button', { name: /Show all/ }).click();
  await expect(labs.locator('.badge', { hasText: 'From document' })).toHaveCount(43);
  const ferritin = labs.locator('tbody tr[data-code="2276-4"]').first();
  await expect(ferritin.locator('td')).toHaveText(['2026-09-05', /Ferritin\s*From document/, '6', 'ng/mL', '15-150', 'Low', '']);
  await expect(ferritin.locator('td').nth(5)).toHaveClass(/text-danger/);
});

test('the browser holds no token: httpOnly cookie only, nothing in storage, CSP on the page', async ({ page, context }) => {
  await openDashboard(page, PATIENTS[0].uuid);
  const storage = await page.evaluate(() => ({ cookie: document.cookie, local: Object.keys(localStorage), session: Object.keys(sessionStorage) }));
  expect(storage.cookie).toBe('');
  expect(storage.local.every((k) => k.startsWith('dashboard.card.'))).toBe(true);
  expect(storage.session).toEqual([]);
  const sid = (await context.cookies('http://localhost:8787')).find((c) => c.name === 'dash_sid');
  expect(sid).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Strict' });
  const res = await page.request.get('/');
  expect(res.headers()['content-security-policy']).toContain("connect-src 'self'");
  // A type outside the allowlist is refused even with a valid session.
  expect((await page.request.get('/fhir/Encounter?patient=' + PATIENTS[0].uuid)).status()).toBe(403);
  expect((await page.request.post('/fhir/Patient', { data: {} })).status()).toBe(405);
});

test('sign-out ends the server session and OpenEMR’s, and returns to the sign-in page', async ({ page }) => {
  await openDashboard(page, PATIENTS[2].uuid);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.waitForURL('http://localhost:8787/**');
  await expect(page.locator('[data-section=login]')).toBeVisible();
  expect((await page.request.get('/fhir/Patient/' + PATIENTS[2].uuid)).status()).toBe(401);
  expect(await (await page.request.get('/auth/session')).json()).toEqual({ authenticated: false });
});
