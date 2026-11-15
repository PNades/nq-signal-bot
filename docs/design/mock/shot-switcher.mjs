// Drawdown / Monte Carlo one account at a time (owner 2026-11-15): node shot-switcher.mjs -> docs/design/step4/switcher/
// (screenshots + checks.json). One panel at a time; ← → step through the accounts and wrap; the segmented control picks
// one; with one account in the page filter the switcher follows it and moving the switcher moves the filter.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("../step4/switcher");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [], checks = {};
async function open(w, h, query) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${query} ${w}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${query} ${w}: pageerror ${e.message}`));
  await page.mouse.move(w - 5, 120);
  await page.goto(`${BASE}?${query}`);
  await page.waitForTimeout(2400);
  return { ctx, page };
}
const state = (page) => page.evaluate(() => ({
  checked: document.querySelector('[data-account-switcher] [aria-checked="true"]')?.textContent,
  panels: document.querySelectorAll("main [data-account-header]").length,
  shown: document.querySelector("main [data-account-header]")?.getAttribute("data-account-header"),
  filter: document.querySelector('button[aria-haspopup="listbox"]')?.textContent,
}));
async function toPanel(page) {
  const b = await page.locator("[data-account-switcher]").boundingBox();
  await page.evaluate((y) => { const m = document.querySelector("main"); m.scrollTop += y - 130; }, b.y);
  await page.waitForTimeout(500);
}
const expect = (name, got, want) => { checks[name] = got; for (const [k, v] of Object.entries(want)) if (got[k] !== v) problems.push(`${name}: ${k} = ${got[k]}, want ${v}`); };

// All accounts: one panel, arrows step and wrap, the segmented control picks
{
  const { ctx, page } = await open(1440, 900, "view=performance&tab=drawdown");
  await toPanel(page);
  expect("all_start", await state(page), { panels: 1, shown: "mffur1", filter: "All accounts" });
  await page.locator('[aria-label="Next account"]').click(); await page.waitForTimeout(500);
  expect("all_next", await state(page), { shown: "mffub1", filter: "All accounts" });
  await page.screenshot({ path: path.join(OUT, "drawdown_mffub1_1440.png") });
  await page.locator('[aria-label="Previous account"]').click(); await page.locator('[aria-label="Previous account"]').click(); await page.waitForTimeout(500);
  expect("all_prev_wraps", await state(page), { shown: "lucid1" });
  await page.locator('[data-account-switcher] [role="radio"]', { hasText: "mffub1" }).click(); await page.waitForTimeout(400);
  await page.keyboard.press("ArrowRight"); await page.waitForTimeout(500);
  expect("all_keyboard_right", await state(page), { shown: "lucid1" });
  await ctx.close();
}
// Monte Carlo with one account in the filter: the switcher follows it, and moving it moves the filter
{
  const { ctx, page } = await open(1440, 900, "view=performance&tab=montecarlo&account=lucid1");
  await toPanel(page);
  expect("one_start", await state(page), { panels: 1, shown: "lucid1", checked: "lucid1Lucid Flex · funded", filter: "lucid1" });
  await page.locator('[aria-label="Next account"]').click(); await page.waitForTimeout(700);
  expect("one_next_moves_filter", await state(page), { shown: "mffur1", filter: "mffur1" });
  await page.locator('[aria-label="Next account"]').click(); await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, "montecarlo_mffub1_1440.png") });
  await ctx.close();
}
// 960 px
{
  const { ctx, page } = await open(960, 1000, "view=performance&tab=drawdown&account=mffub1");
  await toPanel(page);
  expect("w960", await state(page), { panels: 1, shown: "mffub1" });
  await page.screenshot({ path: path.join(OUT, "drawdown_mffub1_960.png") });
  await ctx.close();
}
await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ checks, problems }, null, 2));
for (const [k, v] of Object.entries(checks)) console.log(k, JSON.stringify(v));
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings, all checks passed");
