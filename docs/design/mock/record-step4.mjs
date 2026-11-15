// Step-4 interaction videos (owner 2026-11-15): node record-step4.mjs [performance nav tile]
// Videos -> docs/design/video/ (gitignored), listed in docs/design/videos.md. Only 127.0.0.1 is reachable; console
// errors and warnings are reported. Headless video shows no cursor: hover effects show where the pointer is.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const VID = path.resolve("../video");
fs.mkdirSync(VID, { recursive: true });
const which = process.argv.slice(2).length ? process.argv.slice(2) : ["performance", "nav", "tile"];
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"] });
const problems = [];
let at = [720, 450];

async function open(tag, w = 1440, h = 900) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, recordVideo: { dir: VID, size: { width: w, height: h } }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => {
    const u = r.request().url();
    if (u.startsWith(BASE) || u.startsWith("data:") || u.startsWith("blob:")) return r.continue();
    problems.push(`${tag}: blocked ${u}`); return r.abort();
  });
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${tag}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${tag}: pageerror ${e.message}`));
  at = [w - 40, h - 40];
  await page.mouse.move(...at);
  return { ctx, page };
}

async function glide(page, to, steps = 30, pause = 14) {
  const from = at;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, e = t * t * (3 - 2 * t);
    await page.mouse.move(from[0] + (to[0] - from[0]) * e, from[1] + (to[1] - from[1]) * e);
    await page.waitForTimeout(pause);
  }
  at = to;
}
async function centre(loc) { const b = await loc.boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; }
async function goTo(page, loc, steps) { await loc.scrollIntoViewIfNeeded(); await glide(page, await centre(loc), steps); }
async function press(page, loc, wait = 900) { await goTo(page, loc); await page.mouse.down(); await page.mouse.up(); await page.waitForTimeout(wait); }

async function save(ctx, page, name) {
  const video = page.video();
  await ctx.close();
  const dest = path.join(VID, `${name}.webm`);
  fs.rmSync(dest, { force: true });
  fs.renameSync(await video.path(), dest);
  console.log("video", dest);
}

if (which.includes("performance")) {
  const { ctx, page } = await open("performance");
  await page.goto(`${BASE}?view=performance`);
  await page.waitForTimeout(2600);                                   // stagger in, KPI values roll, sparklines draw
  const tiles = page.locator('[data-slot="kpi-tile"]');
  for (let i = 0; i < 6; i++) await goTo(page, tiles.nth(i), 18);    // spotlight follows the pointer
  const spark = await tiles.nth(0).locator('svg[viewBox="0 0 100 40"]').boundingBox();     // sparkline scrub on Net P&L
  await glide(page, [spark.x + 4, spark.y + spark.height / 2], 16);
  await glide(page, [spark.x + spark.width - 4, spark.y + spark.height / 2], 50, 20);
  await page.waitForTimeout(500);
  const chart = page.locator("main").locator("canvas").first();      // equity crosshair
  const cb = await chart.boundingBox();
  await glide(page, [cb.x + 30, cb.y + cb.height * 0.6], 24);
  await glide(page, [cb.x + cb.width - 90, cb.y + cb.height * 0.3], 80, 16);
  await page.waitForTimeout(400);
  const tab = (name) => page.locator("main button", { hasText: new RegExp(`^${name}$`) }).first();
  await press(page, page.locator("main button", { hasText: /^R$/ }).first(), 1100);   // $ -> R
  await press(page, page.locator("main button", { hasText: /^\$$/ }).first(), 900);
  await press(page, tab("Drawdown"), 1500);
  await press(page, tab("Monte Carlo"), 1700);
  await press(page, tab("Calendar"), 1200);
  const day = page.locator("[data-cal] button:not([disabled])").filter({ hasText: "trade" });
  await goTo(page, day.nth(2)); await page.waitForTimeout(700);      // hover card
  await goTo(page, day.nth(5)); await page.waitForTimeout(700);
  await press(page, day.nth(5), 1500);                               // side panel
  await press(page, page.locator('[aria-label="Close"]'), 700);
  await press(page, page.locator('[aria-label="Previous month"]'), 1000);
  await press(page, page.locator('[aria-label="Previous month"]'), 1000);
  await press(page, tab("Trades"), 1000);
  const th = (name) => page.locator("thead button", { hasText: name }).first();
  await press(page, th("Net"), 900);
  await press(page, th("Net"), 900);
  await press(page, page.locator('[aria-label="Filter trades"]'), 300);
  await page.keyboard.type("short", { delay: 90 });
  await page.waitForTimeout(1100);
  await press(page, tab("Equity"), 600);
  await page.locator("main").evaluate((m) => m.scrollTo({ top: m.scrollHeight, behavior: "smooth" }));
  await page.waitForTimeout(1600);                                   // halftone footer
  await glide(page, [420, 820], 10);
  await glide(page, [1300, 760], 70, 18);
  await glide(page, [800, 850], 40, 18);
  await page.waitForTimeout(600);
  const side = (name) => page.locator('aside nav[aria-label="Pages"] button', { hasText: name });
  await press(page, side("Home"), 2600);                             // page transition both ways
  await press(page, side("Performance"), 1800);
  await save(ctx, page, "step4_performance");
}

if (which.includes("nav")) {
  const { ctx, page } = await open("nav");
  // 1) sidebar
  await page.goto(`${BASE}?view=performance&nav=sidebar`);
  await page.waitForTimeout(1800);
  const side = (name) => page.locator('aside nav[aria-label="Pages"] button', { hasText: name });
  for (const n of ["Home", "Gameplan", "Accounts", "Settings", "Review"]) { await goTo(page, side(n), 14); await page.waitForTimeout(160); }
  await press(page, side("Accounts"), 1300);
  await press(page, side("Performance"), 1300);
  // 2) Waterline (liquid glass)
  await page.goto(`${BASE}?view=performance&nav=waterline`);
  at = [720, 450];
  await page.waitForTimeout(1800);
  const water = page.locator('nav[aria-label="Main"] .nimbus-item');
  const nw = await water.count();
  for (let i = 0; i < nw; i++) { await goTo(page, water.nth(i), 16); await page.waitForTimeout(140); }
  await press(page, water.nth(2), 1500);
  await press(page, water.nth(nw - 1), 1500);
  await press(page, water.nth(0), 1800);
  await press(page, water.nth(nw - 1), 1500);
  // 3) floating dock
  await page.goto(`${BASE}?view=performance&nav=dock`);
  at = [720, 450];
  await page.waitForTimeout(1800);
  const dock = page.locator('nav[aria-label="Pages"] button');
  const nd = await dock.count();
  const first = await centre(dock.nth(0)), last = await centre(dock.nth(nd - 1));
  await glide(page, [first[0] - 30, first[1]], 20);
  await glide(page, [last[0] + 30, last[1]], 90, 18);                 // magnification sweep
  await glide(page, [first[0] + 60, first[1]], 60, 18);
  await press(page, dock.nth(3), 1300);
  await press(page, dock.nth(nd - 1), 1500);
  await save(ctx, page, "step4_nav_options");
}

if (which.includes("tile")) {
  const { ctx, page } = await open("tile");
  await page.goto(`${BASE}?view=tile`);
  await page.waitForTimeout(1800);
  const main = page.locator("main");
  const slider = page.locator('[aria-label="Risk per trade"]');
  await goTo(page, slider);
  const sb = await slider.boundingBox();
  await page.mouse.down();
  await glide(page, [sb.x + sb.width * 0.75, sb.y + sb.height / 2], 30, 16);
  await glide(page, [sb.x + sb.width * 0.4, sb.y + sb.height / 2], 30, 16);
  await page.mouse.up();
  await press(page, page.locator('button[role="switch"]'), 500);
  await press(page, page.locator('button[role="switch"]'), 500);
  const card = page.locator('[aria-label="Drawdown used 50%"]').locator("xpath=ancestor::*[contains(@style,'perspective') or contains(@class,'tilt')][1]");
  const tilt = (await card.count()) ? card : page.locator('[aria-label="Drawdown used 50%"]');
  await tilt.scrollIntoViewIfNeeded();
  const tb = await tilt.boundingBox();
  await glide(page, [tb.x + 10, tb.y + 10], 20);
  await glide(page, [tb.x + tb.width - 10, tb.y + tb.height - 10], 50, 18);   // tilt
  await glide(page, [tb.x + 10, tb.y + tb.height - 10], 40, 18);
  await glide(page, [tb.x + tb.width / 2, tb.y - 200], 20);
  const beam = page.locator("main").getByText("Net R, months 1-6", { exact: false }).first();
  if (await beam.count()) { await goTo(page, beam); await page.waitForTimeout(2200); }        // border beam + spotlight
  const ticker = page.locator("main").getByText("Est. P&L today", { exact: false }).first();
  if (await ticker.count()) { await goTo(page, ticker); await page.waitForTimeout(3200); }    // delta ticker updates
  const flip = page.locator("main").getByText("Flip-disk", { exact: false }).first();
  if (await flip.count()) { await goTo(page, flip); await page.waitForTimeout(2500); }
  await press(page, page.locator("main button", { hasText: "Request payout" }), 2200);        // burst
  await press(page, page.locator("main button", { hasText: "Request payout" }), 1800);
  await main.evaluate((m) => m.scrollTo({ top: m.scrollHeight, behavior: "smooth" }));
  await page.waitForTimeout(4200);                                    // dot matrix text cycles
  await save(ctx, page, "step4_tile");
}

await browser.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings");
