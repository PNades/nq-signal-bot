// The account switcher in the page-tab row (owner 2026-11-15): node shot-switcher.mjs -> docs/design/step4/switcher/
// (screenshots + checks.json). Centred in the row on Drawdown and Monte Carlo, clear of the page tabs and the chips;
// left/right keys move between accounts (wrapping); it stays (no re-animation) between Drawdown and Monte Carlo and
// leaves on any other tab; it follows the page's account filter and moves it; a compact dropdown when the row can't
// hold it as tabs. Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("../step4/switcher");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [], checks = {};
const VIEWS = '[role="tablist"][aria-label="Performance views"]';

async function open(w, h, query) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${query} ${w}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${query} ${w}: pageerror ${e.message}`));
  await page.mouse.move(w - 5, 120);
  await page.goto(`${BASE}?${query}`);
  await page.waitForTimeout(2400);
  const b = await page.locator(VIEWS).boundingBox();
  await page.evaluate((y) => { const m = document.querySelector("main"); m.scrollTop += y - 160; }, b.y);
  await page.waitForTimeout(600);
  return { ctx, page };
}

const state = (page) => page.evaluate((VIEWS) => {
  const tabs = document.querySelector(VIEWS);
  const row = tabs.closest(".grid");
  const sw = row.querySelector("[data-account-switcher]");
  const chips = row.lastElementChild;
  const r = (el) => { const b = el.getBoundingClientRect(); return { l: b.left, r: b.right, c: (b.left + b.right) / 2 }; };
  const rowR = r(row), tabsR = r(tabs), chipsR = r(chips), swR = sw ? r(sw) : null;
  const sel = sw?.querySelector('[role="tab"][aria-selected="true"]');
  return {
    present: !!sw, fit: sw?.dataset.fit ?? null,
    selected: sel ? sel.id.replace("perf-account-tabs-tab-", "") : sw?.querySelector("button[aria-haspopup]")?.getAttribute("aria-label") ?? null,
    shown: document.querySelector("main [data-account-header]")?.getAttribute("data-account-header") ?? null,
    filter: document.querySelector('button[aria-haspopup="listbox"]')?.textContent,
    offCentre: swR ? Math.round(swR.c - rowR.c) : null,
    offGapCentre: swR ? Math.round(swR.c - (tabsR.r + chipsR.l) / 2) : null,
    clearOfTabs: swR ? Math.round(swR.l - tabsR.r) : null,
    clearOfChips: swR ? Math.round(chipsR.l - swR.r) : null,
    need: row.dataset.need ?? null,
  };
}, VIEWS);

const expect = (name, got, want) => {
  checks[name] = got;
  for (const [k, v] of Object.entries(want)) {
    const ok = typeof v === "function" ? v(got[k]) : got[k] === v;
    if (!ok) problems.push(`${name}: ${k} = ${JSON.stringify(got[k])}`);
  }
};
const shot = (page, name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });
const near = (x) => x !== null && Math.abs(x) <= 2;
const clear = (x) => x !== null && x >= 12;
const viewTab = (page, name) => page.locator(`${VIEWS} [role="tab"]`, { hasText: name });

// 1440: tabs, centred, clear of both sides; keys; stays between Drawdown / Monte Carlo; leaves on Equity
{
  const { ctx, page } = await open(1440, 900, "view=performance&tab=drawdown");
  expect("d1440", await state(page), { present: true, fit: "tabs", selected: "mffur1", shown: "mffur1", offCentre: near, clearOfTabs: clear, clearOfChips: clear });
  await shot(page, "drawdown_1440");
  await page.locator("#perf-account-tabs-tab-mffur1").focus();
  await page.keyboard.press("ArrowLeft"); await page.waitForTimeout(500);
  expect("keys_left_wraps", await state(page), { selected: "lucid1", shown: "lucid1" });
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(500);
  expect("keys_right", await state(page), { selected: "mffub1", shown: "mffub1" });
  await shot(page, "drawdown_1440_mffub1");
  // to Monte Carlo: the same element, fully shown at once (no re-animation)
  await page.evaluate(() => { document.querySelector("[data-account-switcher]").__mark = 1; });
  await viewTab(page, "Monte Carlo").click();
  await page.waitForTimeout(40);
  const stays = await page.evaluate(() => {
    const sw = document.querySelector("[data-account-switcher]");
    return { same: sw?.__mark === 1, opacity: sw ? +getComputedStyle(sw).opacity : 0 };
  });
  checks.stays_between = stays;
  if (!stays.same || stays.opacity < 0.99) problems.push(`switcher re-animated between Drawdown and Monte Carlo: ${JSON.stringify(stays)}`);
  await page.waitForTimeout(600);
  expect("mc1440", await state(page), { present: true, selected: "mffub1", shown: "mffub1" });
  await shot(page, "montecarlo_1440");
  // to Equity: it fades out
  await viewTab(page, "Equity").click();
  await page.waitForTimeout(60);
  const midFade = await page.evaluate(() => { const sw = document.querySelector("[data-account-switcher]"); return sw ? +getComputedStyle(sw).opacity : 0; });
  await page.waitForTimeout(500);
  expect("equity_gone", { ...(await state(page)), midFade }, { present: false, midFade: (x) => x < 1 });
  await ctx.close();
}
// follows the page filter, and moving it moves the filter
{
  const { ctx, page } = await open(1440, 900, "view=performance&tab=drawdown&account=lucid1");
  expect("filter_start", await state(page), { selected: "lucid1", filter: "lucid1" });
  await page.locator("#perf-account-tabs-tab-mffub1").click(); await page.waitForTimeout(700);
  expect("filter_follows", await state(page), { selected: "mffub1", shown: "mffub1", filter: "mffub1" });
  await ctx.close();
}
// 960: the row can't hold the account tabs, so a compact dropdown in the same spot
{
  const { ctx, page } = await open(960, 1000, "view=performance&tab=drawdown");
  expect("d960", await state(page), { present: true, fit: "menu", offGapCentre: near, clearOfTabs: clear, clearOfChips: clear });
  await shot(page, "drawdown_960");
  const trig = page.locator('[data-account-switcher] button[aria-haspopup="listbox"]');
  await trig.focus(); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(500);
  expect("d960_key_right", await state(page), { shown: "mffub1" });
  await trig.click(); await page.waitForTimeout(400);
  await shot(page, "drawdown_960_menu_open");
  await page.keyboard.press("Escape");
  // back up to tabs when the window widens
  await page.setViewportSize({ width: 1440, height: 900 }); await page.waitForTimeout(1000);
  expect("d960_to_1440", await state(page), { fit: "tabs", offCentre: near });
  await ctx.close();
}
await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ checks, problems }, null, 2));
for (const [k, v] of Object.entries(checks)) console.log(k, JSON.stringify(v));
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings, all checks passed");
