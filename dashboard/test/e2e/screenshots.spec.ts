import { test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { openDashboard, OPENEMR_WEB, PATIENTS, phpContext } from './helpers';

/**
 * Regenerates the old-vs-new screenshots in docs/dashboard/screenshots/.
 * Not a check, so it runs only when asked: SCREENSHOTS=1 npm run test:e2e -- screenshots
 */
const OUT = resolve(process.cwd(), '../docs/dashboard/screenshots'); // run from dashboard/

test.skip(!process.env.SCREENSHOTS, 'set SCREENSHOTS=1 to regenerate the committed screenshots');

for (const p of PATIENTS.slice(0, 2)) {
  test(`screenshots for pid ${p.pid}`, async ({ page, browser }) => {
    mkdirSync(OUT, { recursive: true });

    await openDashboard(page, p.uuid);
    await page.screenshot({ path: `${OUT}/new-pid${p.pid}.png`, fullPage: true });

    const ctx = await phpContext(browser);
    const shell = ctx.pages()[0]!;
    await shell.evaluate((pid) => {
      const w = window as unknown as { navigateTab: (u: string, n: string, cb: () => void) => void; activateTabByName: (n: string, b: boolean) => void; webroot_url: string };
      w.navigateTab(`${w.webroot_url}/interface/patient_file/summary/demographics.php?set_pid=${pid}`, 'pat', () => w.activateTabByName('pat', true));
    }, p.pid);
    await shell.locator('#attendantData .ptName').waitFor();
    await shell.waitForTimeout(2000);
    const bar = await shell.locator('#attendantData').boundingBox();
    await shell.screenshot({ path: `${OUT}/php-pid${p.pid}-header.png`, clip: { x: 0, y: bar!.y, width: 1440, height: bar!.height } });

    const dash = await ctx.newPage();
    await dash.goto(`${OPENEMR_WEB}/interface/patient_file/summary/demographics.php?set_pid=${p.pid}`);
    await dash.waitForTimeout(3000);
    // Care Team and Labs start collapsed for this user in PHP; open them, as the port shows them open.
    await dash.evaluate(() => ['#careteam_ps_expand', '#labdata_ps_expand'].forEach((id) => document.querySelector(id)?.classList.add('show')));
    await dash.waitForTimeout(500);
    const careTeam = await dash.locator('#careteam_ps_expand').evaluate((el) => {
      const r = el.closest('section')!.getBoundingClientRect();
      return r.bottom + window.scrollY;
    });
    await dash.screenshot({ path: `${OUT}/php-pid${p.pid}-cards.png`, fullPage: true, clip: { x: 0, y: 0, width: 1440, height: Math.ceil(careTeam) + 8 } });
    await dash.locator('#labdata_ps_expand').evaluate((el) => el.closest('section')!.scrollIntoView());
    await dash.locator('#labdata_ps_expand').evaluate((el) => el.closest('section')!.setAttribute('data-shot', '1'));
    await dash.locator('section[data-shot]').screenshot({ path: `${OUT}/php-pid${p.pid}-labs.png` });
    await ctx.close();
  });
}
