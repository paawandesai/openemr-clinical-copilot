// WO38 verification on the live host: open pid 38's chart N times as physician
// and record each brief.php / reconcile.php response; then one more open with a
// lab upload sent while the brief is still loading.
import path from "node:path";
import { createRequire } from "node:module";
const require = createRequire(process.env.PUPPETEER_FROM + "/");
const puppeteer = require("puppeteer-core");

const base = "https://64.177.42.210:9300";
const N = Number(process.env.N || 20);
const PID = process.env.PID || "38";
const out = process.env.OUT;
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const t = () => new Date().toISOString().slice(11, 19);

const browser = await puppeteer.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true, acceptInsecureCerts: true,
  args: ["--no-first-run", "--ignore-certificate-errors"],
  defaultViewport: { width: 1500, height: 1300 },
});
const page = await browser.newPage();
page.on("dialog", (d) => d.accept());
let current = null;
page.on("response", (r) => {
  const m = r.url().match(/clinical-copilot\/public\/(brief|reconcile|upload|upload-status)\.php/);
  if (m && current) (current[m[1]] ||= []).push({ status: r.status(), at: Date.now() - current.t0 });
});

await page.goto(`${base}/interface/login/login.php?site=default`, { waitUntil: "networkidle2" });
await page.type("#authUser", "physician");
await page.type("#clearPass", "physician");
await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2" }).catch(() => {}), page.keyboard.press("Enter")]);

async function open(label, upload) {
  current = { label, t0: Date.now() };
  await page.goto(`${base}/interface/patient_file/summary/demographics.php?set_pid=${PID}`, { waitUntil: "domcontentloaded", timeout: 90000 });
  if (upload) {
    await page.waitForSelector(".ccw2-upload-file", { timeout: 60000 });
    await page.select(".ccw2-upload-kind", "lab_pdf");
    await (await page.$(".ccw2-upload-file")).uploadFile(upload);
    await page.click(".ccw2-upload-send");
    current.upload_sent_at = Date.now() - current.t0;
  }
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline && !(current.brief && current.brief.length)) await pause(250);
  if (upload) {
    while (Date.now() < deadline) {
      const s = await page.$eval(".ccw2-upload-status", (e) => e.textContent.trim()).catch(() => "");
      if (/complete|failed|not accepted/i.test(s)) { current.upload_text = s.replace(/\(Reference:.*\)/, "").slice(0, 140); break; }
      await pause(1000);
    }
    await page.screenshot({ path: path.join(out, "upload-during-brief.png") });
  }
  const r = current; current = null;
  const brief = (r.brief || []).map((x) => `${x.status}@${(x.at / 1000).toFixed(1)}s`).join(",") || "none";
  const recon = (r.reconcile || []).map((x) => `${x.status}@${(x.at / 1000).toFixed(1)}s`).join(",") || "none";
  const up = (r.upload || []).map((x) => `${x.status}@${(x.at / 1000).toFixed(1)}s`).join(",") || "-";
  console.log(`${t()} ${label.padEnd(8)} brief=${brief.padEnd(12)} reconcile=${recon.padEnd(12)} upload=${up}${r.upload_sent_at !== undefined ? ` (sent @${(r.upload_sent_at / 1000).toFixed(1)}s)` : ""}${r.upload_text ? ` | ${r.upload_text}` : ""}`);
  return r;
}

let ok = 0;
for (let i = 1; i <= N; i++) {
  const r = await open(`open ${i}`, null);
  if ((r.brief || []).some((x) => x.status === 200)) ok++;
}
console.log(`BRIEF OK ${ok}/${N}`);
if (process.env.PDF) {
  const r = await open("upload", process.env.PDF);
  console.log(`UPLOAD+BRIEF brief_ok=${(r.brief || []).some((x) => x.status === 200)} upload_ok=${(r.upload || []).some((x) => x.status === 200)}`);
}
await browser.close();
