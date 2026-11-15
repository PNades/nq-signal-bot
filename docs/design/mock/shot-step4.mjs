// Step-4 screenshots (owner 2026-11-15): style tile and Performance at 1440, 1280, 1024, 960 px (whole page: the
// scrolling content area is expanded before the shot), the other Performance tabs at 1440, and the three nav options
// at 1440 and 960. Top-bar no-wrap check on every shot. Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("../step4");
const SIZES = [[1440, 900], [1280, 800], [1024, 768], [960, 1000]];
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [];
import fs from "node:fs";
fs.mkdirSync(OUT, { recursive: true });

async function shot(query, w, h, name, { expand = true, wait = 3200, before } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) ? r.continue() : (problems.push(`${name}: blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`${name}: console ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${name}: pageerror ${e.message}`));
  await page.mouse.move(w - 3, h - 3);
  await page.goto(`${BASE}?${query}`);
  await page.waitForTimeout(wait);
  if (before) await before(page);
  const bar = await page.evaluate(() => {
    const header = document.querySelector("header"), flat = [...header.querySelectorAll("button")].find((b) => b.textContent.includes("FLAT ALL"));
    const f = flat.getBoundingClientRect(), hr = header.getBoundingClientRect();
    return { ok: f.height <= 40 && f.right <= hr.right && header.scrollWidth <= header.clientWidth };
  });
  if (!bar.ok) problems.push(`${name}: top bar wraps or overflows`);
  if (expand) {
    await page.evaluate(() => {
      for (const el of [document.documentElement, document.body, document.getElementById("root"), document.querySelector("#root > div")]) if (el) el.style.height = "auto";
      const main = document.querySelector("main"); main.style.overflow = "visible"; main.style.flex = "none";
    });
    // make the viewport as tall as the page, so canvas effects that draw only while on screen (the dot-matrix footer)
    // are on screen for the shot
    const full = await page.evaluate(() => Math.ceil(document.documentElement.scrollHeight));
    await page.setViewportSize({ width: w, height: Math.max(h, full) });
    await page.waitForTimeout(900);
  }
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  await ctx.close();
  console.log("wrote", name);
}

const ONLY = process.env.ONLY; // e.g. ONLY=tile re-shoots one group
// the tile is shot before the dot-matrix text's first change (3.5 s), so it shows settled text, not the scramble
for (const [w, h] of SIZES) {
  if (!ONLY || ONLY === "tile") await shot("view=tile", w, h, `tile_${w}`, { wait: 1400 });
  if (!ONLY || ONLY === "performance") await shot("view=performance", w, h, `performance_${w}`);
}
if (ONLY) { await browser.close(); console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors, top bar never wraps"); process.exit(0); }
for (const tab of ["drawdown", "montecarlo", "calendar", "trades"]) await shot(`view=performance&tab=${tab}`, 1440, 900, `performance_${tab}_1440`);
await shot("view=performance&tab=calendar", 1440, 900, "performance_calendar_day_1440", {
  before: async (p) => { const day = p.locator("[data-cal] button:not([disabled])").filter({ hasText: "trade" }).first(); await day.click(); await p.waitForTimeout(500); },
});
for (const nav of ["sidebar", "waterline", "dock"]) for (const [w, h] of [[1440, 900], [960, 1000]]) {
  await shot(`view=performance&nav=${nav}`, w, h, `nav_${nav}_${w}`, { expand: false });
}
await browser.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors, top bar never wraps");
