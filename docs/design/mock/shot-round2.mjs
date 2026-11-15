// Step-4 round 2 (owner 2026-11-15): node shot-round2.mjs -> docs/design/step4/round2/ (+ a dock video in the
// gitignored docs/design/video/). Performance scrolled to the bottom at 1920/1440/1280/1024/960 and after resizing both
// ways, with the footer measured (ink inside the side margins, the page ending inside the letters, the clear gap under
// the details); Home without the dock; the dock open and minimized in both variants. Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("../step4/round2");
const VID = path.resolve("../video");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [];
const report = [];

async function open(w, h, query, video) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", ...(video ? { recordVideo: { dir: VID, size: { width: w, height: h } } } : {}) });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) || r.request().url().startsWith("data:") ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${query} ${w}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${query} ${w}: pageerror ${e.message}`));
  await page.mouse.move(w - 5, 120);
  await page.goto(`${BASE}?${query}`);
  return { ctx, page };
}

// scroll to the bottom and measure the footer from its canvas pixels
async function measure(page) {
  await page.evaluate(() => { const m = document.querySelector("main"); m.scrollTop = m.scrollHeight; });
  await page.waitForTimeout(700);
  return page.evaluate(() => {
    const main = document.querySelector("main"), f = document.querySelector(".hwf-root"), cv = f.querySelector("canvas");
    const mr = main.getBoundingClientRect(), fr = f.getBoundingClientRect(), rule = f.querySelector(".hwf-rule").getBoundingClientRect();
    const top = f.querySelector(".hwf-top"), pad = parseFloat(getComputedStyle(top).paddingLeft);
    const ctx = cv.getContext("2d"), W = cv.width, H = cv.height, s = W / fr.width;
    const d = ctx.getImageData(0, 0, W, H).data;
    let minX = W, maxX = -1, minY = H, lastRowInk = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (d[i] > 150 && d[i + 1] > 120) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y === H - 1) lastRowInk++; }
    }
    const weight = (window.__hwfWeight ?? null);
    return {
      footerW: Math.round(fr.width), footerH: Math.round(fr.height), pad: Math.round(pad),
      inkLeft: Math.round(minX / s), inkRight: Math.round(fr.width - (maxX + 1) / s),
      gapUnderDetails: Math.round(minY / s - (rule.bottom - fr.top)),
      pageEndsAtFooterBottom: Math.abs(fr.bottom - mr.bottom) < 1.5, belowFooter: Math.round(mr.bottom - fr.bottom),
      bottomRowInkPx: Math.round(lastRowInk / s), weight,
    };
  });
}

const SIZES = [[1920, 1080], [1440, 900], [1280, 800], [1024, 768], [960, 1000]];
for (const [w, h] of SIZES) {
  const { ctx, page } = await open(w, h, "view=performance");
  await page.waitForTimeout(2600);
  const m = await measure(page);
  report.push({ shot: `footer_${w}`, ...m });
  await page.screenshot({ path: path.join(OUT, `footer_${w}.png`) });
  await ctx.close();
}
for (const [a, b] of [[1920, 960], [960, 1920]]) {
  const { ctx, page } = await open(a, 1000, "view=performance");
  await page.waitForTimeout(2600);
  await page.setViewportSize({ width: b, height: 1000 });
  await page.waitForTimeout(1200);
  const m = await measure(page);
  report.push({ shot: `footer_resize_${a}_to_${b}`, ...m });
  await page.screenshot({ path: path.join(OUT, `footer_resize_${a}_to_${b}.png`) });
  await ctx.close();
}

// Home: no dock
{
  const { ctx, page } = await open(1440, 900, "view=home");
  await page.waitForTimeout(5200);
  const docks = await page.locator("[data-dock]").count();
  if (docks) problems.push("home: the dock is shown");
  report.push({ shot: "home_no_dock_1440", docks });
  await page.screenshot({ path: path.join(OUT, "home_no_dock_1440.png") });
  await ctx.close();
}

// dock: open, minimized centre, minimized corner (top of the page, and scrolled to the bottom); fresh session each
for (const v of ["centre", "corner"]) {
  const { ctx, page } = await open(1440, 900, `view=performance&dockmin=${v}`);
  await page.waitForTimeout(2600);
  if (v === "centre") await page.screenshot({ path: path.join(OUT, "dock_open_1440.png") });
  await page.locator('[aria-label="Minimize the page dock"]').click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(OUT, `dock_min_${v}_1440.png`) });
  // remembered across pages and a reload in the same session
  await page.locator('[aria-label="Show the page dock"]').waitFor();
  await page.reload(); await page.waitForTimeout(1500);
  const still = await page.locator('[data-dock="min"]').count();
  if (!still) problems.push(`dock ${v}: minimized state not remembered after a reload`);
  await page.evaluate(() => { const m = document.querySelector("main"); m.scrollTop = m.scrollHeight; });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, `dock_min_${v}_bottom_1440.png`) });
  await ctx.close();
}

// a page without the footer, scrolled to the bottom: the last content clears the dock
{
  const { ctx, page } = await open(1440, 700, "view=tile");
  await page.waitForTimeout(2200);
  await page.evaluate(() => { const m = document.querySelector("main"); m.scrollTop = m.scrollHeight; });
  await page.waitForTimeout(600);
  const r = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("main section, main article, main [class*='rounded-md'][class*='border']")];
    const last = Math.max(...cards.map((c) => c.getBoundingClientRect().bottom));
    const dock = document.querySelector("[data-dock]").getBoundingClientRect();
    return { lastContentBottom: Math.round(last), dockTop: Math.round(dock.top) };
  });
  if (r.lastContentBottom > r.dockTop) problems.push(`tile: content behind the dock (${JSON.stringify(r)})`);
  report.push({ shot: "tile_bottom_dock_1440", ...r });
  await page.screenshot({ path: path.join(OUT, "tile_bottom_dock_1440.png") });
  await ctx.close();
}

// a short video: minimize and expand, centre then corner
{
  const { ctx, page } = await open(1440, 900, "view=performance&dockmin=centre", true);
  await page.waitForTimeout(2400);
  for (const v of ["centre", "corner"]) {
    if (v === "corner") { await page.goto(`${BASE}?view=performance&dockmin=corner`); await page.waitForTimeout(1800); }
    const btn = page.locator('[aria-label="Minimize the page dock"]');
    const b = await btn.boundingBox();
    await page.mouse.move(b.x + 200, b.y - 10, { steps: 12 });
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 20 });
    await page.waitForTimeout(300);
    await page.mouse.down(); await page.mouse.up();
    await page.waitForTimeout(1300);
    const c = await page.locator('[aria-label="Show the page dock"]').boundingBox();
    await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2, { steps: 20 });
    await page.waitForTimeout(300);
    await page.mouse.down(); await page.mouse.up();
    await page.waitForTimeout(1400);
  }
  const video = page.video();
  await ctx.close();
  const dest = path.join(VID, "step4_dock_minimize.webm");
  fs.rmSync(dest, { force: true });
  fs.renameSync(await video.path(), dest);
  console.log("video", dest);
}

await browser.close();
fs.writeFileSync(path.join(OUT, "measurements.json"), JSON.stringify(report, null, 2));
for (const r of report) console.log(JSON.stringify(r));
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings");
