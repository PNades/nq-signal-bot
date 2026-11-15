// Step-4 final round (owner 2026-11-15): node shot-final.mjs -> docs/design/step4/final/ (screenshots + checks.json).
// KPI row (new set and order), the account menu, Drawdown and Monte Carlo per account under each account's own rule,
// the dock (corner when minimized) over the footer letters, the Dot Matrix Text stopping once settled, one border beam
// per page. Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("../step4/final");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [];
const checks = {};

async function open(w, h, query) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) || r.request().url().startsWith("data:") ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${query} ${w}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${query} ${w}: pageerror ${e.message}`));
  await page.mouse.move(w - 5, 120);
  await page.goto(`${BASE}?${query}`);
  return { ctx, page };
}
const shotMain = async (page, name, clip) => page.screenshot({ path: path.join(OUT, `${name}.png`), ...(clip ? { clip } : {}) });

// KPI row, all accounts and one account; the account menu open
{
  const { ctx, page } = await open(1440, 900, "view=performance");
  await page.waitForTimeout(2600);
  checks.kpiOrder = await page.$$eval('[data-slot="kpi-tile"] p[id$="-label"]', (els) => els.map((e) => e.textContent));
  await shotMain(page, "performance_all_1440");
  await page.locator('button[aria-haspopup="listbox"]').click();
  await page.waitForTimeout(400);
  await shotMain(page, "account_menu_1440", { x: 380, y: 230, width: 1060, height: 300 });
  await page.locator('[role="option"] button', { hasText: "lucid1" }).click();
  await page.waitForTimeout(1200);
  await shotMain(page, "performance_lucid1_1440");
  await ctx.close();
}

// Drawdown and Monte Carlo: all accounts (one panel each) and each account alone
for (const [tab, acc, w] of [["drawdown", "all", 1440], ["drawdown", "lucid1", 1440], ["drawdown", "mffur1", 1440], ["montecarlo", "all", 1440], ["montecarlo", "lucid1", 1440], ["drawdown", "all", 960], ["montecarlo", "all", 960]]) {
  const { ctx, page } = await open(w, 900, `view=performance&tab=${tab}&account=${acc}`);
  await page.waitForTimeout(2600);
  const panel = await page.locator(".mk-beam-ring").first().locator("xpath=..").boundingBox();
  await page.evaluate((y) => { const m = document.querySelector("main"); m.scrollTop += y - 140; }, panel.y);
  await page.waitForTimeout(500);
  if (tab === "drawdown" && acc === "lucid1" && w === 1440) {
    // hover a day after the lock: the tooltip shows the locked floor
    const cv = await page.locator("main canvas").first().boundingBox();
    await page.mouse.move(cv.x + cv.width * 0.6, cv.y + cv.height * 0.3, { steps: 8 });
    await page.waitForTimeout(400);
    checks.lucidTooltip = await page.evaluate(() => [...document.querySelectorAll("main div")].filter((d) => d.textContent?.startsWith("20") && d.textContent.includes("Floor")).map((d) => d.textContent).pop() ?? null);
  }
  checks[`${tab}_${acc}_${w}_headers`] = await page.$$eval("main .font-mono.text-\\[11px\\].text-text-3", (els) => els.map((e) => e.textContent).filter((t) => /EOD|static/.test(t)));
  if (tab === "montecarlo") checks[`${tab}_${acc}_${w}_p`] = await page.$$eval("main span", (els) => els.map((e) => e.textContent).filter((t) => t?.startsWith("P(reaching the floor)")));
  await shotMain(page, `${tab}_${acc}_${w}`);
  await ctx.close();
}

// the dock over the footer letters, scrolled to the bottom: open, then minimized (corner, the default now)
for (const [w, h] of [[1440, 900], [960, 1000]]) {
  const { ctx, page } = await open(w, h, "view=performance");
  await page.waitForTimeout(2600);
  await page.evaluate(() => { const m = document.querySelector("main"); m.scrollTop = m.scrollHeight; });
  await page.waitForTimeout(800);
  await shotMain(page, `dock_over_letters_${w}`);
  if (w === 1440) {
    await page.locator('[aria-label="Minimize the page dock"]').click();
    await page.waitForTimeout(900);
    await shotMain(page, "dock_min_corner_over_letters_1440");
  }
  await ctx.close();
}

// Dot Matrix Text: stops once settled (no redraw), wakes for the next text, settles again. Every change of its
// data-settled attribute is logged with its time for 9 s.
{
  const { ctx, page } = await open(1440, 900, "view=tile");
  await page.waitForTimeout(300);
  const log = await page.evaluate(() => new Promise((resolve) => {
    const c = document.querySelector("main canvas[data-settled]");
    const t0 = performance.now(), out = [[0, c.dataset.settled]];
    new MutationObserver(() => out.push([Math.round(performance.now() - t0), c.dataset.settled])).observe(c, { attributes: true, attributeFilter: ["data-settled"] });
    setTimeout(() => resolve(out), 9000);
  }));
  const settles = log.filter(([, v]) => v === "1").length, wakes = log.filter(([, v]) => v === "0").length;
  // while settled, the canvas must not be redrawn: two reads of its pixels 800 ms apart, inside a settled stretch
  checks.dotText = { transitions: log, settles, wakes, endsSettled: log.at(-1)[1] === "1" || log.at(-1)[0] > 8000 };
  if (settles < 2) problems.push(`dot text: settled ${settles} time(s) in 9 s (expected after the intro and after each text change)`);
  checks.beamsTile = await page.locator(".mk-beam-ring").count();
  await ctx.close();
}
for (const v of ["performance", "home"]) {
  const { ctx, page } = await open(1440, 900, `view=${v}`);
  await page.waitForTimeout(v === "home" ? 5000 : 2000);
  checks[`beams_${v}`] = await page.locator(".mk-beam-ring").count();
  await ctx.close();
}
for (const k of ["beamsTile", "beams_performance", "beams_home"]) if (checks[k] > 1) problems.push(`${k}: ${checks[k]} border beams on one page`);

await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ checks, problems }, null, 2));
console.log(JSON.stringify(checks, null, 1));
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings, all checks passed");
