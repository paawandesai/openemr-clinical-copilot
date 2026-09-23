// Screenshots of the local Langfuse operations dashboard, and optionally
// one encounter's trace and the stdout dashboard's HTML export.
// Runbook: docs/w2/OBSERVABILITY.md §6.
//
//   cd agent/observability/screenshots && npm install
//   node shoot.mjs --env-file ../.env --out ../../../docs/w2/evidence/screenshots \
//     [--trace <trace id>] [--html <path to dashboard-demo.html>]
//
// The login is read from the Langfuse .env that generate-env.sh wrote
// (LANGFUSE_INIT_USER_EMAIL / LANGFUSE_INIT_USER_PASSWORD), never from the
// command line, where `ps` would show it. Chrome: CHROME_PATH, or the
// default macOS install. Base URL: LANGFUSE_URL (default
// http://localhost:3000). Project and dashboard ids are the fixed ones
// docker-compose.langfuse.yml and provision_langfuse_dashboard.py create.
import fs from "node:fs";
import path from "node:path";
import puppeteer from "puppeteer-core";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, arg, i, all) => {
    if (arg.startsWith("--")) pairs.push([arg.slice(2), all[i + 1]]);
    return pairs;
  }, []),
);
if (!args["env-file"] || !args.out) {
  console.error("usage: node shoot.mjs --env-file <langfuse .env> --out <dir> [--trace id] [--html file]");
  process.exit(2);
}

const env = Object.fromEntries(
  fs.readFileSync(args["env-file"], "utf8").split("\n")
    .filter((line) => /^[A-Z_]+=/.test(line))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);
const email = env.LANGFUSE_INIT_USER_EMAIL;
const password = env.LANGFUSE_INIT_USER_PASSWORD;
if (!email || !password) {
  console.error(`${args["env-file"]} has no LANGFUSE_INIT_USER_EMAIL / LANGFUSE_INIT_USER_PASSWORD`);
  process.exit(2);
}
const base = process.env.LANGFUSE_URL || "http://localhost:3000";
const project = "clinical-copilot-agent";
const out = path.resolve(args.out);
fs.mkdirSync(out, { recursive: true });

const browser = await puppeteer.launch({
  executablePath:
    process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  args: ["--no-first-run"],
  defaultViewport: { width: 1920, height: 1200 },
});
const page = await browser.newPage();
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

await page.goto(`${base}/auth/sign-in`, { waitUntil: "networkidle2" });
await page.type('input[name="email"]', email);
await page.type('input[name="password"]', password);
await Promise.all([
  page.waitForNavigation({ waitUntil: "networkidle2" }).catch(() => {}),
  page.keyboard.press("Enter"),
]);
await pause(2000);
if (page.url().includes("/auth/sign-in")) {
  console.error("sign-in failed: check the .env against the running stack");
  await browser.close();
  process.exit(1);
}

// Langfuse scrolls inside its own container, so `fullPage` stops at the
// window. Grow the viewport to the tallest scrollable element instead.
async function shootWhole(name) {
  const height = await page.evaluate(() => {
    let tallest = document.documentElement.scrollHeight;
    for (const element of document.querySelectorAll("*")) {
      const style = getComputedStyle(element);
      if (/(auto|scroll)/.test(style.overflowY) && element.scrollHeight > element.clientHeight) {
        tallest = Math.max(tallest, element.scrollHeight + element.getBoundingClientRect().top);
      }
    }
    return Math.ceil(tallest);
  });
  await page.setViewport({ width: 1920, height: Math.min(Math.max(height + 40, 1200), 6000) });
  await pause(4000); // widgets re-render at the new size
  await page.screenshot({ path: path.join(out, name) });
  console.log(`wrote ${name} (${height}px tall)`);
}

await page.goto(`${base}/project/${project}/dashboards/w2-operations-dashboard`, {
  waitUntil: "networkidle2",
  timeout: 60000,
});
await pause(8000); // every widget runs its own query
await shootWhole("langfuse-dashboard.png");

if (args.trace) {
  // A fixed viewport, not shootWhole: growing the page scrolls the trace
  // tree away from its header and leaves the detail pane empty. What the
  // shot has to show is the header (one trace id, its cost and latency),
  // the top of the observation tree, and one observation's metadata.
  await page.setViewport({ width: 1920, height: 1600 });
  await page.goto(`${base}/project/${project}/traces/${args.trace}`, {
    waitUntil: "networkidle2",
    timeout: 60000,
  });
  await pause(6000);
  await page.screenshot({ path: path.join(out, "langfuse-encounter-trace.png") });
  console.log("wrote langfuse-encounter-trace.png");
}

if (args.html) {
  await page.setViewport({ width: 1600, height: 1200 });
  await page.goto(`file://${path.resolve(args.html)}`, { waitUntil: "load" });
  await page.screenshot({ path: path.join(out, "dashboard-export.png"), fullPage: true });
  console.log("wrote dashboard-export.png");
}
await browser.close();
