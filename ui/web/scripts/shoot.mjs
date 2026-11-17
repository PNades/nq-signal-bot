// Screenshots and checks for a build stage of the Control Panel (step 14; docs/design/build_stage_<n>.md).
// Run against a running `uv run nqbot ui` (add --synthetic where the stage needs it):
//   node scripts/shoot.mjs <stage>          -> docs/design/build/stage<stage>/*.png + checks.json
// Every shot checks: no request leaves 127.0.0.1, no console error or warning (a CSP violation is one), the CSP header
// is present, FLAT ALL is one line and fully visible, and nothing in the top bar overflows.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const stage = process.argv[2] ?? "1";
const BASE = process.env.CF_BASE ?? "http://127.0.0.1:8733";
const OUT = path.resolve(here, "../../../docs/design/build", `stage${stage}`);
const DIST = path.resolve(here, "../dist");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [];
const checks = {};

async function open(name, w, h, url, { js = true, reducedMotion = "no-preference", fresh = true } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", javaScriptEnabled: js, reducedMotion });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) ? r.continue() : (problems.push(`${name}: blocked remote request ${r.request().url()}`), r.abort())));
  // shots that abort the flat request on purpose (to raise a critical banner) expect Chrome's load-failure line
  const aborts = ["banner_timing", "bell_960"].includes(name);
  // the raising-risk dialog comes from the server's 428 answer (the save is refused before it writes): expected
  const expected428 = name.startsWith("raise_risk");
  // the Performance shots ask for walk-forward months on purpose: the ledger guard answers 403
  const expected403 = name.startsWith("perf_");
  page.on("console", (m) => { if ((m.type() === "error" || m.type() === "warning") && !(aborts && m.text().includes("net::ERR_FAILED")) && !(expected428 && m.text().includes("428")) && !(expected403 && m.text().includes("403"))) problems.push(`${name}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
  const resp = await page.goto(BASE + url, { waitUntil: "load" });
  const csp = resp?.headers()["content-security-policy"] ?? "";
  if (!csp.startsWith("default-src 'self'")) problems.push(`${name}: CSP header missing or wrong: ${csp}`);
  if (fresh && js) await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
  return { ctx, page };
}

async function shellChecks(name, page) {
  const r = await page.evaluate(() => {
    const flat = document.querySelector("[data-flat-all]");
    const bar = document.querySelector("[data-topbar]");
    if (!flat || !bar) return null;
    const fr = flat.getBoundingClientRect(), br = bar.getBoundingClientRect();
    return { flatH: Math.round(fr.height), flatRight: Math.round(fr.right), flatFits: flat.scrollWidth <= flat.clientWidth + 1,
      barW: Math.round(br.width), barOverflow: bar.scrollWidth > bar.clientWidth + 1, title: document.title };
  });
  if (!r) return;
  checks[name] = { ...(checks[name] ?? {}), ...r };
  if (r.flatH > 40 || !r.flatFits || r.flatRight > r.barW) problems.push(`${name}: FLAT ALL not one line / not fully visible (${JSON.stringify(r)})`);
  if (r.barOverflow) problems.push(`${name}: the top bar overflows`);
}

async function shot(name, w, h, url, act, opts = {}) {
  const { ctx, page } = await open(name, w, h, url, opts);
  if (opts.reload !== false && opts.js !== false) { await page.reload({ waitUntil: "load" }); }
  await page.waitForTimeout(opts.wait ?? 1400);
  if (act) await act(page);
  if (opts.js !== false) await shellChecks(name, page);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  await ctx.close();
}

const S = {
  "1": async () => {
    for (const [w, h] of [[1440, 900], [1280, 800], [960, 800]]) {
      await shot(`accounts_${w}`, w, h, "/accounts");
      await shot(`performance_${w}`, w, h, "/performance");
    }
    await shot("home_placeholder_1440", 1440, 900, "/");
    await shot("palette_1440", 1440, 900, "/accounts", async (p) => { await p.keyboard.press("Control+k"); await p.waitForTimeout(400); await p.keyboard.type("fla"); await p.waitForTimeout(300); });
    await shot("shortcuts_1440", 1440, 900, "/accounts", async (p) => { await p.keyboard.press("?"); await p.waitForTimeout(450); });
    await shot("kill_confirm_1440", 1440, 900, "/accounts", async (p) => { await p.getByRole("button", { name: /^KILL$/ }).first().click(); await p.waitForTimeout(250); });
    for (const w of [1440, 960]) {
      await shot(`flat_result_${w}`, w, 800, "/accounts", async (p) => { await p.locator("[data-flat-all]").click(); await p.waitForTimeout(500); });
    }
    await shot("dock_minimized_960", 960, 800, "/settings", async (p) => { await p.getByRole("button", { name: "Minimize the page dock" }).click(); await p.waitForTimeout(600); });
    await shot("dock_hover_1440", 1440, 900, "/review", async (p) => { const b = await p.getByRole("button", { name: "Accounts" }).last().boundingBox(); await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await p.waitForTimeout(500); });
    await shot("calm_1440", 1440, 900, "/settings", null, { reducedMotion: "reduce" });
    // the splash on a session's first open (shot mid-way)
    {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
      const page = await ctx.newPage();
      page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`splash: console ${m.type()}: ${m.text()}`); });
      await page.goto(BASE + "/");
      await page.waitForTimeout(700);
      await page.screenshot({ path: path.join(OUT, "splash_1440.png") });
      checks.splash = { flatAllVisibleDuringSplash: await page.locator("[data-flat-all]").isVisible() };
      if (!checks.splash.flatAllVisibleDuringSplash) problems.push("splash: FLAT ALL hidden during the splash");
      await ctx.close();
    }
    // the no-JavaScript emergency page, and the app's own noscript notice
    for (const w of [1440, 960]) await shot(`emergency_${w}`, w, 900, "/emergency", null, { js: false, reload: false, wait: 200 });
    await shot("emergency_after_flat_1440", 1440, 900, "/emergency", async (p) => { await p.getByRole("button", { name: "FLATTEN ALL" }).click(); await p.waitForLoadState("load"); }, { js: false, reload: false, wait: 200 });
    await shot("noscript_1440", 1440, 900, "/accounts", null, { js: false, reload: false, wait: 200 });
    // a stale build: / serves the controls with the reason (the build record is moved away for this shot only)
    const info = path.join(DIST, ".creamflow_build.json");
    const moved = info + ".shoot";
    fs.renameSync(info, moved);
    try { await shot("fallback_missing_build_1440", 1440, 900, "/", null, { js: false, reload: false, wait: 200 }); }
    finally { fs.renameSync(moved, info); }
  },
};

// Stage 1 changes (owner 2026-11-15): the KILL dialog, banners (info fades after ~5 s, paused on hover; warn/crit
// stay), the bell's history, the date block, the command palette's position and focus
S["1b"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const overlap = (a, b) => a && b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  for (const w of [1440, 960]) {
    // KILL dialog: focus on KILL, Esc backs out, Enter confirms
    await shot(`kill_dialog_${w}`, w, w === 1440 ? 900 : 800, "/accounts", async (p) => {
      await p.getByRole("button", { name: /^KILL$/ }).first().click();
      await p.waitForTimeout(150);
      const focused = await p.evaluate(() => document.activeElement?.hasAttribute("data-confirm") ?? false);
      checks[`kill_dialog_${w}`] = { focusOnKill: focused };
      if (!focused) problems.push(`kill_dialog_${w}: focus is not on KILL`);
    });
    // an info banner (FLAT ALL with no engine): shot at once, then it must be gone after ~5 s
    await shot(`banner_info_${w}`, w, 800, "/accounts", async (p) => {
      await p.locator("[data-flat-all]").click();
      await p.waitForTimeout(400);
    });
    // the date block on a page scrolled to its end, and on Home
    await shot(`date_block_home_${w}`, w, w === 1440 ? 900 : 800, "/");
  }
  {
    const name = "kill_keys";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    await page.getByRole("button", { name: /^KILL$/ }).first().click(); await page.waitForTimeout(100);
    await page.keyboard.press("Escape"); await page.waitForTimeout(100);
    const closedByEsc = (await page.locator("[data-confirm-dialog]").count()) === 0;
    await page.getByRole("button", { name: /^KILL$/ }).first().click(); await page.waitForTimeout(100);
    await page.keyboard.press("Enter"); await page.waitForTimeout(500);
    const killBanner = await page.locator('[data-banner="info"]').filter({ hasText: "KILL" }).count();
    checks[name] = { closedByEsc, enterSentKill: killBanner > 0 };
    if (!closedByEsc || !killBanner) problems.push(`${name}: ${JSON.stringify(checks[name])}`);
    await ctx.close();
  }
  {
    const name = "banner_timing";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(400);
    const shownAt = await page.locator('[data-banner="info"]').count();
    await page.waitForTimeout(5600);
    const goneAfter = (await page.locator('[data-banner="info"]').count()) === 0;
    // hovered: it stays past 5 s
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(400);
    await page.locator('[data-banner="info"]').first().hover();
    await page.waitForTimeout(6500);
    const staysWhileHovered = (await page.locator('[data-banner="info"]').count()) > 0;
    await page.mouse.move(700, 600); await page.waitForTimeout(5600);
    const goneAfterLeave = (await page.locator('[data-banner="info"]').count()) === 0;
    // a critical banner (the flat request can't reach the server): stays
    await page.route("**/api/actions/flat", (r) => r.abort());
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, "banner_crit_1440.png") });
    await page.waitForTimeout(6500);
    const critStays = (await page.locator('[data-banner="crit"]').count()) > 0;
    // the bell: history with everything that was shown
    await page.locator("[data-bell]").click(); await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, "bell_open_1440.png") });
    const rows = await page.locator("[data-bell-panel] .divide-y > div").count();
    checks[name] = { shownAt, goneAfter, staysWhileHovered, goneAfterLeave, critStays, historyRows: rows };
    for (const [k, v] of Object.entries(checks[name])) if (v === false || v === 0) problems.push(`${name}: ${k} = ${v}`);
    await ctx.close();
  }
  {
    const { ctx, page } = await open("bell_960", 960, 800, "/settings");
    await page.reload(); await page.waitForTimeout(1200);
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(300);
    await page.route("**/api/actions/flat", (r) => r.abort());
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(300);
    await page.locator("[data-bell]").click(); await page.waitForTimeout(400);
    await shellChecks("bell_960", page);
    await page.screenshot({ path: path.join(OUT, "bell_open_960.png") });
    await ctx.close();
  }
  // palette: centred slightly above the middle, top fixed while typing, no cream box on the field
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `palette_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    await page.keyboard.press("Control+k"); await page.waitForTimeout(450);
    const before = await box(page, '[role="dialog"] .max-w-\\[560px\\]');
    await page.keyboard.type("fla"); await page.waitForTimeout(350);
    const after = await box(page, '[role="dialog"] .max-w-\\[560px\\]');
    const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
    await page.screenshot({ path: path.join(OUT, `${name}.png`) });
    const centre = before.y + before.height / 2;
    checks[name] = { top: Math.round(before.y), topAfterTyping: Math.round(after.y), heightBefore: Math.round(before.height), heightAfter: Math.round(after.height), centreShare: +(centre / h).toFixed(3), fieldOutline: outline };
    if (Math.abs(before.y - after.y) > 0.5) problems.push(`${name}: the palette moved while typing`);
    if (outline !== "none") problems.push(`${name}: the search field still draws an outline`);
    await ctx.close();
  }
  // date block: never on top of the dock or the minimized circle; hidden below 900 px
  for (const [w, h] of [[1440, 900], [1280, 800], [960, 800], [899, 800]]) {
    const name = `date_block_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.reload(); await page.waitForTimeout(1200);
    const d = await box(page, "[data-date-block]");
    const dock = await box(page, "[data-dock]");
    await page.getByRole("button", { name: "Minimize the page dock" }).click(); await page.waitForTimeout(600);
    const circle = await box(page, "[data-dock]");
    const visible = await page.locator("[data-date-block]").isVisible();
    checks[name] = { visible, date: d && { x: Math.round(d.x), y: Math.round(d.y), w: Math.round(d.width), h: Math.round(d.height) }, gapToDock: d && dock ? Math.round(dock.x - (d.x + d.width)) : null };
    if (w >= 900 && !visible) problems.push(`${name}: date block hidden`);
    if (w < 900 && visible) problems.push(`${name}: date block shown below 900 px`);
    if (visible && (overlap(d, dock) || overlap(d, circle))) problems.push(`${name}: date block overlaps the dock`);
    if (w === 960) await page.screenshot({ path: path.join(OUT, `date_block_page_${w}.png`) });
    await ctx.close();
  }
};

// Stage 1, second round of changes (owner 2026-11-15): the KILL/FLAT ALL group's gap, the KILL dialog's three
// variants, the palette closing on an outside click, banners folding away smoothly (+ a video)
S["1c"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `topbar_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    const bell = await box(page, "[data-bell]");
    const kill = await box(page, "[data-risk-controls]");
    const fx = await box(page, "[data-effects-button]");
    const gapBellKill = Math.round(kill.x - (bell.x + bell.width));
    const gapFxBell = Math.round(bell.x - (fx.x + fx.width));
    checks[name] = { gapBellToKill: gapBellKill, gapEffectsToBell: gapFxBell };
    await shellChecks(name, page);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), clip: { x: 0, y: 0, width: w, height: 80 } });
    await ctx.close();
  }
  for (const v of ["ledger", "band", "seal"]) for (const [w, h] of [[1440, 900], [960, 800]]) {
    await shot(`kill_${v}_${w}`, w, h, `/accounts?kill=${v}`, async (p) => {
      await p.getByRole("button", { name: /^KILL$/ }).first().click(); await p.waitForTimeout(200);
      const ok = await p.evaluate((v) => document.activeElement?.hasAttribute("data-confirm") && !!document.querySelector(`[data-variant="${v}"]`), v);
      checks[`kill_${v}_${w}`] = { focusOnKill: ok };
      if (!ok) problems.push(`kill_${v}_${w}: focus not on KILL or wrong variant`);
    });
  }
  // palette: a click outside closes it (backdrop, the empty band beside it, and below it)
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `palette_outside_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    const results = {};
    for (const [label, x, y] of [["left of it", 40, h / 2], ["right of it, same height", w - 30, h * 0.4], ["below it", w / 2, h - 40]]) {
      await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
      if (label === "left of it") await page.screenshot({ path: path.join(OUT, `palette_${w}.png`) });
      await page.mouse.click(x, y); await page.waitForTimeout(400);
      results[label] = (await page.locator('[role="dialog"][aria-label="Command palette"]').count()) === 0;
    }
    // a click inside keeps it open
    await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
    const b = await box(page, '[role="combobox"]');
    await page.mouse.click(b.x + 20, b.y + b.height / 2); await page.waitForTimeout(300);
    results["inside stays open"] = (await page.locator('[role="dialog"][aria-label="Command palette"]').count()) === 1;
    checks[name] = results;
    for (const [k, v] of Object.entries(results)) if (!v) problems.push(`${name}: ${k} failed`);
    await ctx.close();
  }
  // banners: the content below glides up when one leaves (sampled), instant in Calm; and a video
  {
    const name = "banner_collapse";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(500);
    const y0 = (await box(page, "main h1")).y;
    await page.getByRole("button", { name: "Got it" }).click();
    const samples = [];
    for (let i = 0; i < 14; i++) { samples.push(Math.round((await box(page, "main h1")).y)); await page.waitForTimeout(35); }
    await page.waitForTimeout(300);
    const yEnd = (await box(page, "main h1")).y;
    const distinct = new Set(samples).size;
    checks[name] = { headingYWithBanner: Math.round(y0), headingYAfter: Math.round(yEnd), samples, inBetweenPositions: distinct - 2 };
    if (distinct < 4) problems.push(`${name}: the page jumped instead of gliding (${samples.join(",")})`);
    await ctx.close();
  }
  {
    const name = "banner_collapse_calm";
    const { ctx, page } = await open(name, 1440, 900, "/accounts", { reducedMotion: "reduce" });
    await page.reload(); await page.waitForTimeout(1200);
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(400);
    await page.getByRole("button", { name: "Got it" }).click(); await page.waitForTimeout(60);
    checks[name] = { goneWithin60ms: (await page.locator('[data-banner="info"]').count()) === 0 };
    if (!checks[name].goneWithin60ms) problems.push(`${name}: not instant in Calm`);
    await ctx.close();
  }
  {
    const VID = path.resolve(here, "../../../docs/design/video/stage1c");
    fs.mkdirSync(VID, { recursive: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/accounts");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(1500);
    await page.mouse.move(700, 600);
    // two banners: the first one appears, a second stacks on top; both fade and fold away by themselves
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(1600);
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(500);
    await page.mouse.move(700, 600); await page.waitForTimeout(6500);
    // one more, closed with Got it
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(1500);
    await page.getByRole("button", { name: "Got it" }).click(); await page.waitForTimeout(1500);
    const video = page.video();
    await ctx.close();
    const src = await video.path();
    const dst = path.join(VID, "banners_appear_fade_1440.webm");
    fs.renameSync(src, dst);
    checks.video = { path: dst };
  }
};

// The picked KILL dialog (band) and its behaviour (owner 2026-11-15): just above the centre; Esc closes it wherever focus
// is (also after a click on its text); a click outside does nothing; no focus ring on the top-bar KILL after mouse use
S["1d"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const isOpen = async (p) => (await p.locator("[data-confirm-dialog]").count()) === 1;
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    await shot(`kill_dialog_${w}`, w, h, "/accounts", async (p) => {
      await p.getByRole("button", { name: /^KILL$/ }).first().click(); await p.waitForTimeout(200);
      const card = await box(p, '[role="alertdialog"]');
      checks[`kill_dialog_${w}`] = { cardCentreAboveMiddlePx: Math.round(h / 2 - (card.y + card.height / 2)) };
    });
  }
  const VID = path.resolve(here, "../../../docs/design/video/stage1d");
  fs.mkdirSync(VID, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`video: console ${m.type()}: ${m.text()}`); });
  await page.goto(BASE + "/accounts");
  await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
  await page.reload(); await page.waitForTimeout(1500);
  const topKill = page.getByRole("button", { name: /^KILL$/ }).first();
  const r = {};
  // 1. open, click on the card's text, Esc closes
  await topKill.click(); await page.waitForTimeout(700);
  const desc = await box(page, '[role="alertdialog"] p');
  await page.mouse.click(desc.x + 40, desc.y + desc.height / 2); await page.waitForTimeout(700);
  r.focusStaysOnKillAfterTextClick = await page.evaluate(() => document.activeElement?.hasAttribute("data-confirm") ?? false);
  await page.keyboard.press("Escape"); await page.waitForTimeout(700);
  r.escClosesAfterTextClick = !(await isOpen(page));
  // 2. open, click outside: nothing happens; then Esc
  await topKill.click(); await page.waitForTimeout(700);
  for (const [x, y] of [[120, 450], [1320, 200], [720, 860]]) { await page.mouse.click(x, y); await page.waitForTimeout(450); }
  r.outsideClicksKeepItOpen = await isOpen(page);
  r.focusStillOnKill = await page.evaluate(() => document.activeElement?.hasAttribute("data-confirm") ?? false);
  await page.keyboard.press("Escape"); await page.waitForTimeout(700);
  r.escClosesAfterOutsideClicks = !(await isOpen(page));
  // 3. open, Cancel with the mouse: no ring on the top-bar KILL
  await topKill.click(); await page.waitForTimeout(700);
  await page.locator("[data-cancel]").click(); await page.waitForTimeout(700);
  r.closedByCancel = !(await isOpen(page));
  const ring = await page.evaluate(() => { const a = document.activeElement; return { onTopKill: a?.textContent?.trim() === "KILL", outline: a ? getComputedStyle(a).outlineStyle : null }; });
  r.focusBackOnTopKill = ring.onTopKill;
  r.noRingAfterMouse = ring.outline === "none";
  await page.waitForTimeout(800);
  // 4. keyboard: Shift+Tab then Tab back shows the ring again (rings stay for keyboard navigation)
  await page.keyboard.press("Shift+Tab"); await page.keyboard.press("Tab"); await page.waitForTimeout(500);
  r.ringWithKeyboard = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle !== "none");
  await page.waitForTimeout(900);
  const video = page.video();
  await ctx.close();
  const dst = path.join(VID, "kill_dialog_behaviour_1440.webm");
  fs.renameSync(await video.path(), dst);
  checks.behaviour = r;
  checks.video = { path: dst };
  for (const [k, v] of Object.entries(r)) if (v !== true) problems.push(`behaviour: ${k} = ${v}`);
};

// Stage 2 (Home, Accounts, Settings). Nothing here writes to the owner's panel file: the raising-risk dialog comes from a
// save the server refuses (428) before writing, delete is never held, Effects is never clicked.
S["2"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const overlap = (a, b) => a && b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  for (const [w, h] of [[1440, 900], [1280, 800], [960, 800]]) {
    await shot(`home_${w}`, w, h, "/", async (p) => {
      await p.evaluate(() => sessionStorage.setItem("creamflow.homeIntroSeen", "1"));
      await p.reload(); await p.waitForTimeout(1500);
      const nodes = await p.$$eval('button[aria-label*="(key"]', (els) => els.map((e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; }));
      const date = await box(p, "[data-date-block]");
      checks[`home_${w}`] = { nodes: nodes.length, dateBlockClearOfNodes: !nodes.some((n) => overlap(n, date)) };
      if (nodes.length !== 6) problems.push(`home_${w}: ${nodes.length} nodes`);
      if (nodes.some((n) => overlap(n, date))) problems.push(`home_${w}: a node overlaps the date block`);
      await p.locator('button[aria-label^="Accounts (key"]').hover(); await p.waitForTimeout(450);
    });
  }
  // the opening sequence, caught mid-way on a session's first open
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
    const p = await ctx.newPage();
    p.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`home_intro: console ${m.type()}: ${m.text()}`); });
    await p.goto(BASE + "/"); await p.evaluate(() => sessionStorage.setItem("creamflow.splashShown", "1")); await p.reload();
    await p.waitForTimeout(1900); await p.screenshot({ path: path.join(OUT, "home_intro_1440.png") });
    await ctx.close();
  }
  for (const [w, h] of [[1440, 900], [1280, 800], [960, 800]]) await shot(`accounts_${w}`, w, h, "/accounts");
  await shot("accounts_card_hover_1440", 1440, 900, "/accounts", async (p) => { const b = await box(p, '[data-account-card="mffur1"]'); await p.mouse.move(b.x + b.width * 0.7, b.y + b.height * 0.3); await p.waitForTimeout(400); });
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    await shot(`account_form_${w}`, w, h, "/accounts/mffur1");
    await shot(`account_form_limits_${w}`, w, h, "/accounts/mffur1", async (p) => { await p.locator("#grp-limits").scrollIntoViewIfNeeded(); await p.waitForTimeout(300); });
  }
  await shot("account_form_funded_1440", 1440, 900, "/accounts/mffur1", async (p) => {
    await p.getByRole("radio", { name: "Funded" }).first().click(); await p.waitForTimeout(500);
    await p.locator("#grp-targets").scrollIntoViewIfNeeded(); await p.waitForTimeout(300);
  });
  await shot("preset_picker_1440", 1440, 900, "/accounts/mffub1", async (p) => { await p.locator("[data-trigger]").click(); await p.waitForTimeout(300); });
  await shot("save_as_preset_1440", 1440, 900, "/accounts/mffub1", async (p) => {
    await p.getByRole("button", { name: "Save as preset" }).click(); await p.waitForTimeout(300);
    await p.locator("#pr-label").fill("Lucid Flex 50k"); await p.waitForTimeout(200);
  });
  await shot("raise_risk_1440", 1440, 900, "/accounts/mffur1", async (p) => {
    await p.locator("#grp-sizing").scrollIntoViewIfNeeded();
    await p.getByRole("radio", { name: "1/2" }).click(); await p.waitForTimeout(700);
    await p.locator("[data-save]").click(); await p.waitForTimeout(800);
    await p.locator("#raise-typed").fill("mffur1 1/2"); await p.waitForTimeout(200);
    checks.raise_risk = { dialog: await p.getByRole("dialog").count() };
  });
  await shot("account_invalid_960", 960, 800, "/accounts/mffur1", async (p) => {
    await p.locator("#grp-sizing").scrollIntoViewIfNeeded();
    await p.locator("#fld-risk_usd_per_trade").fill("900"); await p.waitForTimeout(900);
  });
  await shot("account_delete_1440", 1440, 900, "/accounts/mffub1", async (p) => {
    await p.getByText("Delete this account").scrollIntoViewIfNeeded(); await p.waitForTimeout(200);
  });
  await shot("leave_guard_1440", 1440, 900, "/accounts/mffur1", async (p) => {
    await p.locator("#fld-copied_accounts").fill("1"); await p.waitForTimeout(400);
    await p.getByRole("button", { name: "Home" }).last().click(); await p.waitForTimeout(400);
  });
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    await shot(`settings_${w}`, w, h, "/settings");
    await shot(`settings_custom_${w}`, w, h, "/settings", async (p) => {
      await p.getByRole("radio", { name: /^Custom/ }).click(); await p.waitForTimeout(300);
      await p.locator("#set-thresholds").scrollIntoViewIfNeeded(); await p.waitForTimeout(400);
    });
    await shot(`settings_global_${w}`, w, h, "/settings", async (p) => { await p.locator("#set-windows").scrollIntoViewIfNeeded(); await p.waitForTimeout(400); });
  }
  await shot("settings_footer_1440", 1440, 900, "/settings", async (p) => { await p.locator("#cf-main").evaluate((m) => m.scrollTo({ top: m.scrollHeight })); await p.waitForTimeout(600); });
  // effects budget (MASTER §8): idle CPU of the page's main thread over 8 s, Home in Full and in Calm
  for (const [name, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", reducedMotion: reduced });
    const p = await ctx.newPage();
    await p.goto(BASE + "/"); await p.evaluate(() => { sessionStorage.setItem("creamflow.splashShown", "1"); sessionStorage.setItem("creamflow.homeIntroSeen", "1"); });
    await p.reload(); await p.waitForTimeout(2500);
    const cdp = await ctx.newCDPSession(p);
    await cdp.send("Performance.enable");
    const m0 = Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
    await p.waitForTimeout(8000);
    const m1 = Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
    const share = (m1.TaskDuration - m0.TaskDuration) / (m1.Timestamp - m0.Timestamp);
    checks[`budget_home_${name}`] = { mainThreadShare: +share.toFixed(3), limit: name === "full" ? 0.15 : 0.03 };
    if (share > (name === "full" ? 0.15 : 0.03)) problems.push(`budget_home_${name}: ${(share * 100).toFixed(1)}% of a core`);
    await ctx.close();
  }
};

// Stage 2 changes (owner 2026-11-15): the bar follows the dock; subtle magnification with labels above the bar; deleting
// an own preset with Undo (the preset endpoints are faked here, so the owner's real presets file is never written);
// Reset / Discard / Save. Nothing is saved.
S["2b"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const overlap = (a, b) => a && b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  const makeChange = async (p) => { await p.locator("#fld-copied_accounts").fill("1"); await p.waitForTimeout(500); };
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `bar_dock_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts/mffur1");
    await page.reload(); await page.waitForTimeout(1200);
    await makeChange(page);
    const barOpen = await box(page, "[data-unsaved]");
    const dockOpen = await box(page, '[data-dock="open"]');
    await page.screenshot({ path: path.join(OUT, `bar_dock_open_${w}.png`) });
    // hover the Accounts icon: its label must sit above the bar's layer and clear of the bar
    const icon = await page.getByRole("button", { name: "Accounts" }).last().boundingBox();
    await page.mouse.move(icon.x + icon.width / 2, icon.y + icon.height / 2); await page.waitForTimeout(500);
    const label = await page.evaluate(() => {
      const el = [...document.querySelectorAll("[data-dock] .whitespace-pre")].find((e) => e.textContent === "Accounts");
      if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height };
    });
    const iconHover = await page.getByRole("button", { name: "Accounts" }).last().boundingBox();
    await page.screenshot({ path: path.join(OUT, `dock_hover_bar_${w}.png`) });
    await page.mouse.move(w / 2, 200); await page.waitForTimeout(300);
    await page.getByRole("button", { name: "Minimize the page dock" }).click(); await page.waitForTimeout(700);
    const barLow = await box(page, "[data-unsaved]");
    const date = await box(page, "[data-date-block]");
    const circle = await box(page, '[data-dock="min"]');
    await page.screenshot({ path: path.join(OUT, `bar_dock_min_${w}.png`) });
    await page.getByRole("button", { name: "Show the page dock" }).click(); await page.waitForTimeout(700);
    const barBack = await box(page, "[data-unsaved]");
    checks[name] = {
      barBottomOpen: Math.round(h - (barOpen.y + barOpen.height)), dockTop: Math.round(h - dockOpen.y),
      barBottomMinimized: Math.round(h - (barLow.y + barLow.height)), barBottomBack: Math.round(h - (barBack.y + barBack.height)),
      magnification: +(iconHover.width / 40).toFixed(2), labelClearOfBar: label ? !overlap(label, barOpen) : null,
      barClearOfDateBlock: !overlap(barLow, date), barClearOfCircle: !overlap(barLow, circle),
    };
    const c = checks[name];
    if (c.barBottomOpen <= c.dockTop) problems.push(`${name}: the bar is not above the dock`);
    if (!(c.barBottomMinimized < c.barBottomOpen - 50)) problems.push(`${name}: the bar didn't come down with the dock minimized`);
    if (Math.abs(c.barBottomBack - c.barBottomOpen) > 1) problems.push(`${name}: the bar didn't go back up`);
    if (c.magnification > 1.25) problems.push(`${name}: magnification ${c.magnification}`);
    if (c.labelClearOfBar === false) problems.push(`${name}: the hover label overlaps the bar`);
    if (date && !c.barClearOfDateBlock) problems.push(`${name}: the low bar overlaps the date block`);
    if (!c.barClearOfCircle) problems.push(`${name}: the low bar overlaps the dock circle`);
    await ctx.close();
  }
  // the three buttons; Discard on a new account creates nothing
  await shot("bar_buttons_1440", 1440, 900, "/accounts/mffur1", async (p) => { await makeChange(p); });
  {
    const name = "discard_new";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1200);
    const before = await page.locator("[data-account-card]").count();
    await page.getByRole("button", { name: "Add account" }).click(); await page.waitForTimeout(600);
    await page.locator("#fld-name").fill("test9"); await page.waitForTimeout(400);
    await page.locator("[data-discard]").click(); await page.waitForTimeout(800);
    const after = await page.locator("[data-account-card]").count();
    checks[name] = { url: new URL(page.url()).pathname, cardsBefore: before, cardsAfter: after };
    if (checks[name].url !== "/accounts" || after !== before) problems.push(`${name}: ${JSON.stringify(checks[name])}`);
    // Reset stays in the editor
    await page.goto(BASE + "/accounts/mffur1"); await page.waitForTimeout(1200);
    await makeChange(page); await page.locator("[data-reset]").click(); await page.waitForTimeout(400);
    checks[name].resetStays = new URL(page.url()).pathname === "/accounts/mffur1" && (await page.locator("#fld-copied_accounts").inputValue()) === "0";
    if (!checks[name].resetStays) problems.push(`${name}: Reset didn't undo or didn't stay`);
    await ctx.close();
  }
  // deleting an own preset (faked endpoints; one own preset injected into the list)
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `preset_delete_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts/mffub1");
    let deleted = false, restored = false;
    await page.route("**/api/accounts", async (r) => {
      const res = await r.fetch(); const body = await res.json();
      const base = body.presets.find((p) => p.id === "lucid_flex_25k_evaluation");
      if (!deleted || restored) body.presets.push({ ...base, id: "own_lucid_flex_50k_evaluation", label: "Lucid Flex 50k", builtin: false, fields: { ...base.fields, start_balance_usd: 50000, drawdown_usd: 2500 } });
      await r.fulfill({ response: res, json: body });
    });
    await page.route("**/api/presets/delete", (r) => { deleted = true; return r.fulfill({ json: { ok: true, hash: "x" } }); });
    await page.route("**/api/presets/save", (r) => { restored = true; return r.fulfill({ json: { ok: true, hash: "x", id: "own_lucid_flex_50k_evaluation" } }); });
    await page.reload(); await page.waitForTimeout(1200);
    await page.locator("[data-trigger]").click(); await page.waitForTimeout(300);
    await page.locator('input[aria-label="Filter presets"]').fill("lucid"); await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(OUT, `preset_own_${w}.png`) });
    await page.locator("[data-preset-delete]").first().click(); await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(OUT, `preset_delete_confirm_${w}.png`) });
    await page.locator("[data-preset-confirm]").getByRole("button", { name: "Delete" }).click(); await page.waitForTimeout(700);
    const undo = await page.locator("[data-toast-action]").count();
    // the toast stays clear of the dock (and of the unsaved-changes bar when open)
    const toastBox = await page.locator('ol[aria-label="Notifications"] li').first().boundingBox();
    const dockBox = await box(page, "[data-dock]");
    checks[`${name}_toast`] = { toastClearOfDock: !overlap(toastBox, dockBox) };
    if (overlap(toastBox, dockBox)) problems.push(`${name}: the toast overlaps the dock`);
    await page.screenshot({ path: path.join(OUT, `preset_deleted_undo_${w}.png`) });
    await page.locator("[data-toast-action]").click(); await page.waitForTimeout(800);
    checks[name] = { deleteCalled: deleted, undoShown: undo === 1, undoRestored: restored };
    for (const [k, v] of Object.entries(checks[name])) if (!v) problems.push(`${name}: ${k} = ${v}`);
    await ctx.close();
  }
  // video: the bar sliding with the dock, then the dock hover
  {
    const VID = path.resolve(here, "../../../docs/design/video/stage2b");
    fs.mkdirSync(VID, { recursive: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/accounts/mffur1");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); sessionStorage.setItem("creamflow.dockMinimized", "0"); } catch {} });
    await page.reload(); await page.waitForTimeout(1500);
    await makeChange(page); await page.waitForTimeout(800);
    for (let i = 0; i < 2; i++) {
      await page.getByRole("button", { name: "Minimize the page dock" }).click(); await page.waitForTimeout(1200);
      await page.getByRole("button", { name: "Show the page dock" }).click(); await page.waitForTimeout(1200);
    }
    // sweep the pointer along the dock: the subtle magnification and the labels above the bar
    const dock = await box(page, '[data-dock="open"]');
    for (let x = dock.x + 10; x <= dock.x + dock.width - 10; x += 12) { await page.mouse.move(x, dock.y + dock.height / 2); await page.waitForTimeout(45); }
    await page.mouse.move(720, 300); await page.waitForTimeout(800);
    const video = page.video();
    await ctx.close();
    const dst = path.join(VID, "bar_follows_dock_and_hover_1440.webm");
    fs.renameSync(await video.path(), dst);
    checks.video = { path: dst };
  }
};

// The top-bar account switcher (owner 2026-11-15). Real accounts, plus one preview with 7 made-up accounts and live
// numbers injected into this browser's status stream only (to show scrolling and the amber/red room bars).
S["2c"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const isOpen = async (p) => (await p.locator("[data-switcher-panel]").count()) === 1;
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `switcher_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts");
    await page.reload(); await page.waitForTimeout(1300);
    const b = await box(page, "[data-account-switcher]");
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await page.waitForTimeout(80);
    const openAt80 = await isOpen(page);
    await page.waitForTimeout(250);
    const openAt330 = await isOpen(page);
    // move into the panel: it stays open
    const pb = await box(page, "[data-switcher-panel]");
    await page.mouse.move(b.x + b.width / 2, pb.y + 10, { steps: 6 }); await page.mouse.move(pb.x + pb.width / 2, pb.y + pb.height / 2, { steps: 6 }); await page.waitForTimeout(400);
    const stillOpen = await isOpen(page);
    await page.screenshot({ path: path.join(OUT, `switcher_open_${w}.png`) });
    // leave both: it closes
    await page.mouse.move(w - 40, h - 200, { steps: 4 }); await page.waitForTimeout(500);
    const closedAfterLeave = !(await isOpen(page));
    // keyboard: focus the trigger, ArrowDown opens, ArrowDown moves, Enter focuses that account, Esc on a reopened list closes
    await page.locator("[data-account-switcher]").focus(); await page.keyboard.press("ArrowDown"); await page.waitForTimeout(300);
    const keyOpen = await isOpen(page);
    // nothing is highlighted at open (owner 2026-11-15): two presses reach the second row
    await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowDown"); await page.waitForTimeout(150);
    await page.screenshot({ path: path.join(OUT, `switcher_keyboard_${w}.png`) });
    const second = await page.locator("[data-switcher-row]").nth(1).getAttribute("data-switcher-row");
    await page.keyboard.press("Enter"); await page.waitForTimeout(300);
    const triggerText = await page.locator("[data-account-switcher]").innerText();
    await page.locator("[data-account-switcher]").focus(); await page.keyboard.press("Enter"); await page.waitForTimeout(300);
    await page.keyboard.press("Escape"); await page.waitForTimeout(250);
    const escCloses = !(await isOpen(page));
    // click opens; Add account opens the new-account form
    await page.locator("[data-account-switcher]").click(); await page.waitForTimeout(300);
    const clickOpen = await isOpen(page);
    await page.getByRole("button", { name: "Add account" }).last().click(); await page.waitForTimeout(800);
    const addPath = new URL(page.url()).pathname;
    // put the focus back on the first account for the next run
    await page.evaluate(() => localStorage.removeItem("creamflow.focusAccount"));
    checks[name] = { openAt80, openAt330, stillOpenOverPanel: stillOpen, closedAfterLeave, keyOpen, focusMoved: triggerText.includes(second ?? "?"), escCloses, clickOpen, addPath };
    const c = checks[name];
    if (c.openAt80 || !c.openAt330 || !c.stillOpenOverPanel || !c.closedAfterLeave || !c.keyOpen || !c.focusMoved || !c.escCloses || !c.clickOpen || c.addPath !== "/accounts/new")
      problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // preview: 7 accounts with made-up live numbers (this browser only)
  {
    const name = "switcher_preview_live_1440";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    const fake = (n, kind, phase, dd, room, pnl, prog, target, lives, extra = {}) => ({ name: n, firm: kind === "Lucid Flex" ? "Lucid" : "MyFundedFutures", phase, draft: false, demo: false, active: true,
      preset: null, kind, drawdown_usd: dd, target: { kind: phase === "funded" ? "payout" : "profit_target", usd: target },
      live: { state: null, room_to_floor_usd: room, today_pnl_usd: pnl, progress_usd: prog, lives_left: lives }, ...extra });
    const accounts = [
      fake("mffur1", "Rapid", "evaluation", 1000, 880, 212.5, 640, 1500, 3),
      fake("mffub1", "Builder", "funded", 1000, 420, -166.4, 310, 1000, 2),
      fake("lucid1", "Lucid Flex", "funded", 1000, 190, -333, 0, 1000, 1),
      fake("pro1", "Pro", "evaluation", 2000, 2000, 0, 0, 3000, 3),
      { ...fake("mffur2", "Rapid", "evaluation", 1000, null, null, null, 1500, null), live: { state: "paused", room_to_floor_usd: 760, today_pnl_usd: 0, progress_usd: 120, lives_left: 3 } },
      { ...fake("mffur3", "Rapid", "evaluation", 1000, 0, -420, 0, 1500, 0), live: { state: "blown", room_to_floor_usd: 0, today_pnl_usd: -420, progress_usd: 0, lives_left: 0 } },
      { ...fake("lucid2", "Lucid Flex", "evaluation", 1000, null, null, null, 1250, null), draft: true, live: { state: null, room_to_floor_usd: null, today_pnl_usd: null, progress_usd: null, lives_left: null } },
    ];
    await page.route("**/api/status", async (r) => { const res = await r.fetch(); const body = await res.json(); body.accounts = accounts; await r.fulfill({ response: res, json: body }); });
    // the status stream never ends: serve one static frame built from the real status (fetched once, up front)
    const st0 = await (await page.request.get(BASE + "/api/status")).json();
    st0.accounts = accounts;
    await page.route("**/api/events", (r) => r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: `event: status\ndata: ${JSON.stringify(st0)}\n\n` }));
    await page.reload(); await page.waitForTimeout(1500);
    await page.locator("[data-account-switcher]").click(); await page.waitForTimeout(400);
    const rows = await page.locator("[data-switcher-row]").count();
    const scrollable = await page.evaluate(() => { const l = document.querySelector('[data-switcher-panel] [role="listbox"]'); return l ? l.scrollHeight > l.clientHeight + 4 : false; });
    await page.screenshot({ path: path.join(OUT, `${name}.png`) });
    checks[name] = { rows, scrollable };
    if (rows !== 7 || !scrollable) problems.push(`${name}: ${JSON.stringify(checks[name])}`);
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await ctx.close();
  }
};

// Owner's changes after the switcher (2026-11-15): Home bottom row, the banner glide on Home (FLIP), 60 fps idle, the
// node cards inside the scene, the accounts list <-> editor transition, the dock on transforms, the pinned account
// dropdown, no stale hover highlight.
S["2d"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const VID = path.resolve(here, "../../../docs/design/video/stage2d");
  fs.mkdirSync(VID, { recursive: true });
  const homeIdle = async (p) => { await p.reload(); await p.waitForTimeout(5500); await p.mouse.move(5, 300); await p.waitForTimeout(300); };
  // 1 + 4: Home with every node's card hovered: inside the scene; the hint on the date block's line
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `home_${w}`;
    const { ctx, page } = await open(name, w, h, "/");
    await homeIdle(page);
    const scene = await box(page, "[data-home]");
    const msgs = await box(page, "[data-home-messages]");
    const date = await box(page, "[data-date-block]");
    await page.screenshot({ path: path.join(OUT, `home_bottom_row_${w}.png`) });
    const cards = {};
    for (const n of ["Live", "Gameplan", "Review", "Accounts", "Settings", "Performance"]) {
      const b = page.locator(`[data-home] button[aria-label^="${n} (key"]`);
      await b.hover(); await page.waitForTimeout(550);
      const r = await b.boundingBox();
      cards[n] = r.y >= scene.y - 0.5 && r.y + r.height <= scene.y + scene.height + 0.5 && r.x >= scene.x - 0.5 && r.x + r.width <= scene.x + scene.width + 0.5;
      if (!cards[n]) problems.push(`${name}: the ${n} card leaves the scene (${JSON.stringify(r)} vs ${JSON.stringify(scene)})`);
      if (n === "Live") await page.screenshot({ path: path.join(OUT, `home_live_hover_${w}.png`) });
      await page.mouse.move(5, 300); await page.waitForTimeout(350);
    }
    const hintBottom = Math.round(h - (msgs.y + msgs.height));
    const dateBottom = date ? Math.round(h - (date.y + date.height)) : null;
    checks[name] = { sceneBottomGap: Math.round(h - (scene.y + scene.height)), hintBottom, dateBottom, cardsInside: cards };
    if (date && Math.abs(hintBottom - dateBottom) > 1) problems.push(`${name}: the hint and the date block are not on one line (${hintBottom} vs ${dateBottom})`);
    if (checks[name].sceneBottomGap > 1) problems.push(`${name}: Home keeps ${checks[name].sceneBottomGap} px of bottom room`);
    await ctx.close();
  }
  // 2: a banner on Home: the scene is laid out once per change (its height jumps, it doesn't fold frame by frame) and
  // the stage glides there by transform
  const sampleHome = (p, ms) => p.evaluate((ms) => new Promise((res) => {
    const heights = [], transforms = [], t0 = performance.now();
    const home = document.querySelector("[data-home]"), stage = document.querySelector("[data-home-stage]");
    const tick = () => { heights.push(home.clientHeight); transforms.push(getComputedStyle(stage).transform);
      if (performance.now() - t0 < ms) requestAnimationFrame(tick); else res({ heights: [...new Set(heights)], transforms: new Set(transforms).size, frames: heights.length }); };
    requestAnimationFrame(tick);
  }), ms);
  {
    const name = "home_banner_glide";
    const { ctx, page } = await open(name, 1440, 900, "/");
    await homeIdle(page);
    const enter = sampleHome(page, 700);
    await page.locator("[data-flat-all]").click();
    const e = await enter;
    await page.waitForTimeout(400);
    const leave = sampleHome(page, 800);
    await page.getByRole("button", { name: "Got it" }).click();
    const l = await leave;
    checks[name] = { enterHeights: e.heights, enterTransformSteps: e.transforms, leaveHeights: l.heights, leaveTransformSteps: l.transforms, frames: [e.frames, l.frames] };
    if (e.heights.length > 2 || l.heights.length > 2) problems.push(`${name}: Home re-laid out during the banner animation (${JSON.stringify(checks[name])})`);
    if (e.transforms < 5 || l.transforms < 5) problems.push(`${name}: the scene didn't glide (${JSON.stringify(checks[name])})`);
    await ctx.close();
  }
  {
    const name = "home_banner_calm";
    const { ctx, page } = await open(name, 1440, 900, "/", { reducedMotion: "reduce" });
    await homeIdle(page);
    const enter = sampleHome(page, 400);
    await page.locator("[data-flat-all]").click();
    const e = await enter;
    checks[name] = { transformSteps: e.transforms };
    if (e.transforms > 1) problems.push(`${name}: the scene moved in Calm`);
    await ctx.close();
  }
  // 5: the list <-> editor transition (in-between opacities sampled); Calm instant
  const sampleView = (p, ms) => p.evaluate((ms) => new Promise((res) => {
    const seen = new Set(), t0 = performance.now();
    const tick = () => { const v = document.querySelector("[data-accounts-view]"); if (v) seen.add(`${v.getAttribute("data-accounts-view")}:${(+getComputedStyle(v).opacity).toFixed(1)}`);
      if (performance.now() - t0 < ms) requestAnimationFrame(tick); else res([...seen]); };
    requestAnimationFrame(tick);
  }), ms);
  for (const calm of [false, true]) {
    const name = calm ? "accounts_transition_calm" : "accounts_transition";
    const { ctx, page } = await open(name, 1440, 900, "/accounts", calm ? { reducedMotion: "reduce" } : {});
    await page.reload(); await page.waitForTimeout(1300);
    const s1 = sampleView(page, 600);
    await page.locator('[data-account-card="mffur1"]').click();
    const opened = await s1;
    await page.waitForTimeout(300);
    const s2 = sampleView(page, 600);
    await page.getByRole("button", { name: "All accounts" }).click();
    const closed = await s2;
    const mid = (xs) => xs.filter((x) => !x.endsWith(":1.0") && !x.endsWith(":0.0")).length;
    checks[name] = { openInBetween: mid(opened), closeInBetween: mid(closed), url: new URL(page.url()).pathname };
    if (!calm && (mid(opened) < 2 || mid(closed) < 2)) problems.push(`${name}: no transition (${JSON.stringify(checks[name])})`);
    if (calm && (mid(opened) > 0 || mid(closed) > 0)) problems.push(`${name}: not instant in Calm (${JSON.stringify(checks[name])})`);
    await ctx.close();
  }
  // 6: the dock: slots never move while sweeping (transforms only), magnification at most 1.2
  {
    const name = "dock_sweep";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1300);
    const slots = () => page.evaluate(() => [...document.querySelectorAll('[data-dock="open"] nav > div')].map((d) => Math.round(d.getBoundingClientRect().left)));
    const before = await slots();
    const dock = await box(page, '[data-dock="open"]');
    let moved = false, maxScale = 1;
    for (let x = dock.x + 10; x <= dock.x + dock.width - 10; x += 16) {
      await page.mouse.move(x, dock.y + dock.height / 2); await page.waitForTimeout(30);
      const now = await slots(); if (now.join() !== before.join()) moved = true;
      const s = await page.evaluate(() => Math.max(...[...document.querySelectorAll('[data-dock="open"] nav > div > button')].map((b) => b.getBoundingClientRect().width / 40)));
      maxScale = Math.max(maxScale, s);
    }
    checks[name] = { slots: before.length, slotsMoved: moved, maxScale: +maxScale.toFixed(3) };
    if (before.length < 6) problems.push(`${name}: found ${before.length} slots`);
    if (moved) problems.push(`${name}: the icon slots moved during the sweep`);
    if (maxScale > 1.205 || maxScale < 1.1) problems.push(`${name}: magnification ${maxScale}`);
    await ctx.close();
  }
  // 7 + 8: the account dropdown pins on click; no stale highlight (dropdown and palette)
  {
    const name = "switcher_pin";
    const isOpen = async (p) => (await p.locator("[data-switcher-panel]").count()) === 1;
    const lit = (p, sel) => p.locator(`${sel} [data-lit]`).count();
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1300);
    const b = await box(page, "[data-account-switcher]");
    const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
    await page.mouse.move(cx, cy); await page.waitForTimeout(350);
    const hoverOpens = await isOpen(page);
    await page.mouse.click(cx, cy); await page.waitForTimeout(200);
    const clickWhileHoverOpenKeepsOpen = await isOpen(page);
    await page.mouse.move(1100, 700, { steps: 4 }); await page.waitForTimeout(700);
    const staysPinnedAway = await isOpen(page);
    await page.mouse.click(cx, cy); await page.waitForTimeout(250);
    const secondClickCloses = !(await isOpen(page));
    await page.mouse.move(1100, 700, { steps: 3 }); await page.waitForTimeout(500);
    await page.mouse.click(cx, cy); await page.waitForTimeout(300);
    const noneLitAtOpen = (await lit(page, "[data-switcher-panel]")) === 0;
    const row = await page.locator("[data-switcher-row]").nth(1).boundingBox();
    await page.mouse.move(row.x + 40, row.y + row.height / 2, { steps: 4 }); await page.waitForTimeout(150);
    const rowLitOnHover = (await lit(page, "[data-switcher-panel]")) === 1;
    await page.screenshot({ path: path.join(OUT, "switcher_pinned_row_hover_1440.png") });
    const foot = await page.getByRole("button", { name: "Manage accounts" }).boundingBox();
    await page.mouse.move(foot.x + foot.width + 60, foot.y + foot.height / 2, { steps: 4 }); await page.waitForTimeout(200);
    const clearedOnLeave = (await lit(page, "[data-switcher-panel]")) === 0;
    await page.screenshot({ path: path.join(OUT, "switcher_pinned_left_list_1440.png") });
    await page.locator("[data-switcher-panel]").focus(); await page.keyboard.press("ArrowDown"); await page.waitForTimeout(120);
    const keysHighlight = (await lit(page, "[data-switcher-panel]")) === 1;
    await page.mouse.click(1100, 500); await page.waitForTimeout(250);
    const outsideClickCloses = !(await isOpen(page));
    await page.mouse.click(cx, cy); await page.waitForTimeout(250); await page.mouse.move(1100, 700, { steps: 3 }); await page.waitForTimeout(400);
    await page.keyboard.press("Escape"); await page.waitForTimeout(250);
    const escCloses = !(await isOpen(page));
    // the palette
    const pal = '[role="dialog"] [role="listbox"]';
    const palLit = () => page.locator(`ul[role="listbox"] [role="option"][aria-selected="true"]`).count();
    await page.mouse.move(1300, 850);
    await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
    const paletteNoneAtOpen = (await palLit()) === 0;
    const item = await page.locator(`ul[role="listbox"] [role="option"]`).nth(2).boundingBox();
    await page.mouse.move(item.x + 30, item.y + item.height / 2, { steps: 3 }); await page.waitForTimeout(150);
    const paletteLitOnHover = (await palLit()) === 1;
    const lb = await page.locator(`ul[role="listbox"]`).first().boundingBox();
    await page.mouse.move(lb.x + lb.width + 80, lb.y + 20, { steps: 3 }); await page.waitForTimeout(200);
    const paletteClearedOnLeave = (await palLit()) === 0;
    await page.screenshot({ path: path.join(OUT, "palette_pointer_left_1440.png") });
    await page.keyboard.press("ArrowDown"); await page.waitForTimeout(120);
    const paletteKeysHighlight = (await palLit()) === 1;
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
    await page.keyboard.type("perf"); await page.waitForTimeout(250);
    await page.keyboard.press("Enter"); await page.waitForTimeout(800);
    const enterRunsTop = new URL(page.url()).pathname;
    checks[name] = { hoverOpens, clickWhileHoverOpenKeepsOpen, staysPinnedAway, secondClickCloses, noneLitAtOpen, rowLitOnHover, clearedOnLeave, keysHighlight,
      outsideClickCloses, escCloses, paletteNoneAtOpen, paletteLitOnHover, paletteClearedOnLeave, paletteKeysHighlight, enterRunsTop };
    for (const [k, v] of Object.entries(checks[name])) if (v === false) problems.push(`${name}: ${k}`);
    if (enterRunsTop !== "/performance") problems.push(`${name}: Enter with nothing highlighted went to ${enterRunsTop}`);
    await page.evaluate(() => localStorage.removeItem("creamflow.focusAccount"));
    void pal;
    await ctx.close();
  }
  // videos
  const record = async (file, url, act) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + url);
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); sessionStorage.setItem("creamflow.dockMinimized", "0"); } catch {} });
    await act(page);
    const video = page.video();
    await ctx.close();
    const dst = path.join(VID, file);
    fs.renameSync(await video.path(), dst);
    (checks.videos ??= []).push(dst);
  };
  await record("home_banner_appear_leave_1440.webm", "/", async (p) => {
    await homeIdle(p); await p.waitForTimeout(600);
    await p.locator("[data-flat-all]").click(); await p.mouse.move(700, 450); await p.waitForTimeout(7200);
    await p.locator("[data-flat-all]").click(); await p.mouse.move(700, 450); await p.waitForTimeout(1500);
    await p.getByRole("button", { name: "Got it" }).click(); await p.mouse.move(700, 450); await p.waitForTimeout(1500);
  });
  await record("accounts_list_editor_1440.webm", "/accounts", async (p) => {
    await p.reload(); await p.waitForTimeout(1500);
    await p.locator('[data-account-card="mffur1"]').click(); await p.waitForTimeout(1300);
    await p.getByRole("button", { name: "All accounts" }).click(); await p.waitForTimeout(1300);
    await p.getByRole("button", { name: "Add account" }).first().click(); await p.waitForTimeout(1000);
    await p.locator("#fld-name").fill("test9"); await p.waitForTimeout(500);
    await p.locator("[data-discard]").click(); await p.waitForTimeout(1300);
    await p.locator('[data-account-card="mffub1"]').click(); await p.waitForTimeout(1300);
    await p.getByRole("button", { name: "All accounts" }).click(); await p.waitForTimeout(1200);
  });
  await record("dock_sweep_1440.webm", "/accounts", async (p) => {
    await p.reload(); await p.waitForTimeout(1500);
    const d = await box(p, '[data-dock="open"]');
    const y = d.y + d.height / 2;
    for (const step of [6, 14, 24]) {
      for (let x = d.x + 8; x <= d.x + d.width - 8; x += step) { await p.mouse.move(x, y); await p.waitForTimeout(16); }
      for (let x = d.x + d.width - 8; x >= d.x + 8; x -= step) { await p.mouse.move(x, y); await p.waitForTimeout(16); }
    }
    await p.mouse.move(720, 300); await p.waitForTimeout(900);
  });
};

// Owner's fixes before stage 3 (2026-11-15): outside clicks only close (KILL / FLAT ALL act), the page behind an open
// dropdown or palette is inert, no native title tooltips, no horizontal scrollbar, and Home's nodes never move.
S["2e"] = async () => {
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const VID = path.resolve(here, "../../../docs/design/video/stage2e");
  fs.mkdirSync(VID, { recursive: true });
  const homeIdle = async (p) => { await p.reload(); await p.waitForTimeout(5500); await p.mouse.move(5, 300); await p.waitForTimeout(300); };
  const isOpen = async (p) => (await p.locator("[data-switcher-panel]").count()) === 1;
  const palOpen = async (p) => (await p.locator('ul[role="listbox"]').count()) === 1;
  const pinSwitcher = async (p) => { const b = await box(p, "[data-account-switcher]"); await p.mouse.click(b.x + b.width / 2, b.y + b.height / 2); await p.waitForTimeout(300); };
  const noTitles = (p) => p.evaluate(() => [...document.querySelectorAll("[title]")].map((e) => `${e.tagName}:${e.getAttribute("title")}`.slice(0, 80)));
  const hScroll = (p) => p.evaluate(() => { const m = document.getElementById("cf-main"), d = document.scrollingElement;
    return { doc: d.scrollWidth > d.clientWidth, main: m ? m.scrollWidth > m.clientWidth : false }; });

  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const c = (checks[`overlays_${w}`] = {});
    const { ctx, page } = await open(`overlays_${w}`, w, h, "/accounts");
    await page.reload(); await page.waitForTimeout(1300);
    // 1. the pinned dropdown: a click on an account card behind it only closes it
    await pinSwitcher(page);
    // a card in view and clear of the dropdown: the third at 1440, the first at 960 (the third is below the window)
    const card = await box(page, `[data-account-card="${w === 1440 ? "mffur2" : "mffur1"}"]`);
    // 2. hover behind is inert: the card under the pointer gets no hover
    // a point on the card clear of the dropdown and the palette (the card's right end)
    const px = card.x + card.width - 30, py = card.y + card.height - 30;
    await page.mouse.move(px, py, { steps: 4 }); await page.waitForTimeout(250);
    c.frameInert = await page.evaluate(() => getComputedStyle(document.querySelector("[data-app-frame]")).pointerEvents === "none");
    c.hitUnderPointerIsRoot = await page.evaluate(([x, y]) => { const e = document.elementFromPoint(x, y); return e === document.documentElement || e === document.body || e?.id === "root"; }, [px, py]);
    await page.screenshot({ path: path.join(OUT, `dropdown_pinned_inert_${w}.png`) });
    await page.mouse.click(px, py); await page.waitForTimeout(500);
    c.dropdownOutsideClickOnlyCloses = !(await isOpen(page)) && new URL(page.url()).pathname === "/accounts";
    // KILL acts on the first click while the dropdown is pinned
    await pinSwitcher(page);
    await page.getByRole("button", { name: /^KILL$/ }).first().click(); await page.waitForTimeout(400);
    c.killFirstClickWithDropdown = (await page.locator("[data-confirm-dialog]").count()) === 1;
    c.dropdownClosedByKill = !(await isOpen(page));
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    // the palette: a click on a card behind only closes it; FLAT ALL acts on the first click
    await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
    c.paletteFrameInert = await page.evaluate(() => getComputedStyle(document.querySelector("[data-app-frame]")).pointerEvents === "none");
    await page.screenshot({ path: path.join(OUT, `palette_topbar_live_${w}.png`) });
    await page.mouse.click(px, py); await page.waitForTimeout(500);
    c.paletteOutsideClickOnlyCloses = !(await palOpen(page)) && new URL(page.url()).pathname === "/accounts";
    // the top bar's other buttons (bell) only close it too
    await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
    await page.getByRole("button", { name: /^Notifications/ }).click({ force: true }); await page.waitForTimeout(400);
    c.paletteBellClickOnlyCloses = !(await palOpen(page)) && (await page.locator("[data-bell-panel]").count()) === 0;
    await page.keyboard.press("Control+k"); await page.waitForTimeout(400);
    const infoBefore = await page.locator('[data-banner="info"]').count();
    await page.locator("[data-flat-all]").click(); await page.waitForTimeout(600);
    c.flatFirstClickWithPalette = (await page.locator('[data-banner="info"]').count()) > infoBefore;
    c.paletteClosedByFlat = !(await palOpen(page));
    // 3. hints: FLAT ALL shows our styled hint; no element anywhere carries a native title
    await page.mouse.move(5, 500); await page.waitForTimeout(200);
    await page.locator("[data-flat-all]").hover(); await page.waitForTimeout(800);
    c.hintShown = (await page.locator("[data-hint-tip]").count()) === 1;
    await page.screenshot({ path: path.join(OUT, `hint_flat_all_${w}.png`) });
    const titles = [];
    for (const url of ["/", "/accounts", "/accounts/mffur1", "/accounts/new", "/settings", "/performance", "/review", "/gameplan"]) {
      await page.goto(BASE + url); await page.waitForTimeout(url === "/" ? 4500 : 1200);
      titles.push(...(await noTitles(page)).map((t) => `${url} ${t}`));
      const hs = await hScroll(page);
      if (hs.doc || hs.main) problems.push(`overlays_${w}: horizontal overflow on ${url} ${JSON.stringify(hs)}`);
    }
    c.nativeTitles = titles;
    for (const [k, v] of Object.entries(c)) if (v === false) problems.push(`overlays_${w}: ${k}`);
    if (titles.length) problems.push(`overlays_${w}: native title attributes: ${titles.join(" | ")}`);
    await ctx.close();
  }
  // 2 on Home: with the dropdown open, hovering a node opens no card; after closing, it does
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `home_inert_${w}`;
    const { ctx, page } = await open(name, w, h, "/");
    await homeIdle(page);
    await pinSwitcher(page);
    const node = await box(page, '[data-home] button[aria-label^="Gameplan (key"]');
    await page.mouse.move(node.x + node.width / 2, node.y + node.height / 2, { steps: 5 }); await page.waitForTimeout(500);
    const cardWhileOpen = await page.locator("[data-home-card]").count();
    await page.screenshot({ path: path.join(OUT, `home_dropdown_inert_${w}.png`) });
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    await page.mouse.move(node.x + node.width / 2 + 3, node.y + node.height / 2, { steps: 2 }); await page.waitForTimeout(500);
    const cardAfter = await page.locator("[data-home-card]").count();
    await page.screenshot({ path: path.join(OUT, `home_after_close_hover_${w}.png`) });
    checks[name] = { cardWhileOpen, cardAfter };
    if (cardWhileOpen !== 0 || cardAfter !== 1) problems.push(`${name}: ${JSON.stringify(checks[name])}`);
    await ctx.close();
  }
  // 4. the accounts list <-> editor: no horizontal overflow on any frame of the transition
  {
    const name = "accounts_transition_no_hscroll";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(1300);
    const watch = () => page.evaluate(() => new Promise((res) => {
      let bad = 0, frames = 0; const t0 = performance.now();
      const tick = () => { frames++; const m = document.getElementById("cf-main"), d = document.scrollingElement;
        if (d.scrollWidth > d.clientWidth || (m && m.scrollWidth > m.clientWidth) || window.innerHeight - document.documentElement.clientHeight > 0) bad++;
        if (performance.now() - t0 < 700) requestAnimationFrame(tick); else res({ bad, frames }); };
      requestAnimationFrame(tick);
    }));
    const r = [];
    for (const act of [
      () => page.locator('[data-account-card="mffur1"]').click(),
      () => page.getByRole("button", { name: "All accounts" }).click(),
      () => page.getByRole("button", { name: "Add account" }).first().click(),
      () => page.locator("[data-discard]").click(),
    ]) { const w = watch(); await act(); r.push(await w); await page.waitForTimeout(300); }
    checks[name] = r;
    if (r.some((x) => x.bad > 0)) problems.push(`${name}: ${JSON.stringify(r)}`);
    await ctx.close();
  }
  // 5. Home stands still: 30 s video, idle then hovering each node; every node header and line sampled on every frame
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") problems.push(`home_still: console ${m.text()}`); });
    await page.goto(BASE + "/");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await homeIdle(page);
    await page.evaluate(() => {
      const home = document.querySelector("[data-home]");
      const snap = () => {
        const nodes = [...home.querySelectorAll("button[aria-label]")].map((b) => { const r = b.getBoundingClientRect(); return `${(r.left + r.width / 2).toFixed(2)},${(r.top + r.height / 2).toFixed(2)}`; }).join(" ");
        const lines = [...home.querySelectorAll("svg path")].map((p) => p.getAttribute("d")).join("|");
        const stage = getComputedStyle(document.querySelector("[data-home-stage]")).transform;
        const size = `${home.clientWidth}x${home.clientHeight}`;
        return { nodes, lines, stage, size };
      };
      const first = snap(); const w = (window.__still = { frames: 0, changes: [] });
      const tick = () => { w.frames++; const s = snap();
        for (const k of Object.keys(s)) if (s[k] !== first[k] && w.changes.length < 20) w.changes.push(`${Math.round(performance.now())} ${k}: ${String(first[k]).slice(0, 120)} -> ${String(s[k]).slice(0, 120)}`);
        if (!w.stop) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    });
    await page.waitForTimeout(16000);                                   // idle
    for (const n of ["Live", "Gameplan", "Review", "Performance", "Settings", "Accounts"]) {
      const b = await box(page, `[data-home] button[aria-label^="${n} (key"]`);
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 }); await page.waitForTimeout(1500);
      await page.mouse.move(b.x + b.width / 2, b.y + b.height + 60, { steps: 6 }); await page.waitForTimeout(500);   // into its card when it opens below
    }
    await page.mouse.move(5, 450, { steps: 6 }); await page.waitForTimeout(1200);
    const still = await page.evaluate(() => { window.__still.stop = true; return window.__still; });
    checks.home_still = still;
    if (still.changes.length) problems.push(`home_still: Home moved: ${still.changes.slice(0, 4).join(" ; ")}`);
    const video = page.video();
    await ctx.close();
    const dst = path.join(VID, "home_still_idle_then_hover_1440.webm");
    fs.renameSync(await video.path(), dst);
    (checks.videos ??= []).push(dst);
  }
  // the accounts transition again on video (no scrollbar), and the inert page behind the dropdown
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/accounts");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(1500);
    await page.locator('[data-account-card="mffur1"]').click(); await page.waitForTimeout(1200);
    await page.getByRole("button", { name: "All accounts" }).click(); await page.waitForTimeout(1200);
    await page.getByRole("button", { name: "Add account" }).first().click(); await page.waitForTimeout(1000);
    await page.locator("[data-discard]").click(); await page.waitForTimeout(1200);
    await pinSwitcher(page);
    const card = await box(page, '[data-account-card="mffur2"]');
    await page.mouse.move(card.x + card.width / 2, card.y + card.height - 30, { steps: 12 }); await page.waitForTimeout(800);
    await page.mouse.click(card.x + card.width / 2, card.y + card.height - 30); await page.waitForTimeout(1200);
    const video = page.video();
    await ctx.close();
    const dst = path.join(VID, "accounts_transition_and_inert_1440.webm");
    fs.renameSync(await video.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// Home's node cards animated, and the banner glide back (only on a banner), measured after every painted frame.
S["2f"] = async () => {
  const { probe } = await import("./glide_probe.mjs");
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const VID = path.resolve(here, "../../../docs/design/video/stage2f");
  fs.mkdirSync(VID, { recursive: true });
  const homeIdle = async (p) => { await p.reload(); await p.waitForTimeout(5500); await p.mouse.move(5, 300); await p.waitForTimeout(300); };
  const MAX_STEP_PX = 6;
  // after-paint sampler for any expression
  const sampleAfterPaint = (p, ms, fnSrc) => p.evaluate(([ms, fnSrc]) => new Promise((res) => {
    const f = new Function(`return (${fnSrc})()`); const out = []; const t0 = performance.now();
    const ch = new MessageChannel(); const after = () => ch.port2.postMessage(0);
    ch.port1.onmessage = () => { out.push({ t: Math.round(performance.now() - t0), v: f() }); if (performance.now() - t0 < ms) requestAnimationFrame(after); else res(out); };
    requestAnimationFrame(after);
  }), [ms, fnSrc]);

  // 2. the banner glide: two banners in a row (the second reuses the cached layout), at both widths; and Calm
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `banner_glide_${w}`;
    const { ctx, page } = await open(name, w, h, "/");
    await homeIdle(page);
    const runs = [];
    for (let i = 0; i < 2; i++) { const r = await probe(page); runs.push({ arrive: r.arrive, leave: r.leave }); await page.waitForTimeout(500); }
    checks[name] = runs;
    for (const [i, r] of runs.entries()) for (const k of ["arrive", "leave"]) {
      if (r[k].maxStepPx > MAX_STEP_PX) problems.push(`${name} #${i + 1} ${k}: a frame jumped ${r[k].maxStepPx} px`);
      if (r[k].framesMoving < 8) problems.push(`${name} #${i + 1} ${k}: no glide (${r[k].framesMoving} moving frames)`);
    }
    await ctx.close();
  }
  {
    const name = "banner_glide_calm";
    const { ctx, page } = await open(name, 1440, 900, "/", { reducedMotion: "reduce" });
    await homeIdle(page);
    const r = await probe(page);
    checks[name] = { arrive: r.arrive, leave: r.leave };
    // Calm: no glide; the scene goes to its new place in one step
    if (r.arrive.framesMoving > 1 || r.leave.framesMoving > 1) problems.push(`${name}: more than one step in Calm ${JSON.stringify(checks[name])}`);
    await ctx.close();
  }
  // 1. the cards: fade + scale-up in ~180 ms, out in ~120 ms; their own hover (lift + glow); headers never move
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `card_anim_${w}`;
    const { ctx, page } = await open(name, w, h, "/");
    await homeIdle(page);
    const headers = () => page.evaluate(() => [...document.querySelectorAll("[data-home] button[aria-label]")].map((b) => { const r = b.getBoundingClientRect(); return `${r.left.toFixed(2)},${r.top.toFixed(2)}`; }).join(" "));
    const before = await headers();
    const res = {};
    for (const n of ["Live", "Performance"]) {
      const b = await box(page, `[data-home] button[aria-label^="${n} (key"]`);
      const inP = sampleAfterPaint(page, 320, `() => { const c = document.querySelector("[data-home-card]"); if (!c) return null; const s = getComputedStyle(c); return [(+s.opacity).toFixed(2), s.transform]; }`);
      await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
      const ins = (await inP).map((x) => x.v).filter(Boolean);
      const mid = ins.filter((v) => v[0] !== "1.00" && v[0] !== "0.00").length;
      const scaled = ins.some((v) => v[1] !== "none" && /matrix\(0\.9/.test(v[1]));
      // into the card: its own hover
      const card = await box(page, "[data-home-card] [data-home-card-body]");
      await page.mouse.move(card.x + card.width / 2, card.y + card.height / 2, { steps: 4 }); await page.waitForTimeout(350);
      const glow = await page.evaluate(() => +getComputedStyle(document.querySelector("[data-home-card-glow]")).opacity);
      const lift = await page.evaluate(() => getComputedStyle(document.querySelector("[data-home-card-body]")).translate);
      if (n === "Live") await page.screenshot({ path: path.join(OUT, `card_hover_${w}.png`) });
      const outP = sampleAfterPaint(page, 300, `() => !!document.querySelector("[data-home-card]")`);
      await page.mouse.move(5, 300);
      const outs = await outP;
      const goneAt = outs.find((x) => !x.v)?.t ?? null;
      res[n] = { inBetweenFrames: mid, scaledUp: scaled, glowOnCardHover: +glow.toFixed(2), liftOnCardHover: lift, goneAfterMs: goneAt };
      if (mid < 3 || !scaled) problems.push(`${name} ${n}: no fade/scale-in (${JSON.stringify(res[n])})`);
      if (glow < 0.9 || !String(lift).includes("-2")) problems.push(`${name} ${n}: no hover effect on the card (${JSON.stringify(res[n])})`);
      if (goneAt == null || goneAt > 200) problems.push(`${name} ${n}: slow to leave (${goneAt} ms)`);
      await page.waitForTimeout(300);
    }
    res.headersStill = (await headers()) === before;
    if (!res.headersStill) problems.push(`${name}: a node header moved`);
    checks[name] = res;
    await ctx.close();
  }
  {
    const name = "card_anim_calm";
    const { ctx, page } = await open(name, 1440, 900, "/", { reducedMotion: "reduce" });
    await homeIdle(page);
    const b = await box(page, '[data-home] button[aria-label^="Live (key"]');
    const inP = sampleAfterPaint(page, 200, `() => { const c = document.querySelector("[data-home-card]"); return c ? (+getComputedStyle(c).opacity).toFixed(2) : null; }`);
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    const vals = (await inP).map((x) => x.v).filter((v) => v !== null);
    checks[name] = { opacities: [...new Set(vals)] };
    if (vals.some((v) => v !== "1.00")) problems.push(`${name}: the card faded in Calm (${vals.join(",")})`);
    await ctx.close();
  }
  // 30 s still: idle, then each node hovered and its card hovered: no node header, line, stage or size change
  const still = async (page) => {
    await page.evaluate(() => {
      const home = document.querySelector("[data-home]");
      const snap = () => ({
        nodes: [...home.querySelectorAll("button[aria-label]")].map((b) => { const r = b.getBoundingClientRect(); return `${(r.left + r.width / 2).toFixed(2)},${(r.top + r.height / 2).toFixed(2)}`; }).join(" "),
        lines: [...home.querySelectorAll("svg path")].map((p) => p.getAttribute("d")).join("|"),
        stage: getComputedStyle(document.querySelector("[data-home-stage]")).transform,
        size: `${home.clientWidth}x${home.clientHeight}`,
      });
      const first = snap(); const w = (window.__still = { frames: 0, changes: [] });
      const ch = new MessageChannel(); const after = () => ch.port2.postMessage(0);
      ch.port1.onmessage = () => { w.frames++; const s = snap();
        for (const k of Object.keys(s)) if (s[k] !== first[k] && w.changes.length < 20) w.changes.push(`${Math.round(performance.now())} ${k}`);
        if (!w.stop) requestAnimationFrame(after); };
      requestAnimationFrame(after);
    });
  };
  const ctxV = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const pv = await ctxV.newPage();
  pv.on("console", (m) => { if (m.type() === "error") problems.push(`videos: console ${m.text()}`); });
  await pv.goto(BASE + "/");
  await pv.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
  await homeIdle(pv);
  // video part 1: 30 s still (16 s idle, then each node and its card hovered)
  await still(pv);
  await pv.waitForTimeout(16000);
  for (const n of ["Live", "Gameplan", "Review", "Performance", "Settings", "Accounts"]) {
    const b = await box(pv, `[data-home] button[aria-label^="${n} (key"]`);
    await pv.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 }); await pv.waitForTimeout(700);
    const c = await box(pv, "[data-home-card] [data-home-card-body]");
    if (c) { await pv.mouse.move(c.x + c.width / 2, c.y + c.height / 2, { steps: 6 }); await pv.waitForTimeout(800); }
    await pv.mouse.move(720, 450, { steps: 6 }); await pv.waitForTimeout(300);
  }
  const st = await pv.evaluate(() => { window.__still.stop = true; return window.__still; });
  checks.home_still_30s = st;
  if (st.changes.length) problems.push(`home_still_30s: Home moved: ${st.changes.slice(0, 4).join(" ; ")}`);
  // video part 2: banners on Home: one arrives and fades by itself, a second arrives and is closed with Got it
  await pv.waitForTimeout(800);
  await pv.locator("[data-flat-all]").click(); await pv.mouse.move(720, 470); await pv.waitForTimeout(7200);
  await pv.locator("[data-flat-all]").click(); await pv.mouse.move(720, 470); await pv.waitForTimeout(1600);
  await pv.getByRole("button", { name: "Got it" }).click(); await pv.mouse.move(720, 470); await pv.waitForTimeout(1500);
  const video = pv.video();
  await ctxV.close();
  const dst = path.join(VID, "home_still_cards_then_banners_1440.webm");
  fs.renameSync(await video.path(), dst);
  (checks.videos ??= []).push(dst);
  // a short card-only video at 960
  const ctx2 = await browser.newContext({ viewport: { width: 960, height: 800 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 960, height: 800 } } });
  const p2 = await ctx2.newPage();
  await p2.goto(BASE + "/");
  await p2.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
  await homeIdle(p2);
  for (const n of ["Live", "Review", "Performance", "Accounts"]) {
    const b = await box(p2, `[data-home] button[aria-label^="${n} (key"]`);
    await p2.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 10 }); await p2.waitForTimeout(600);
    const c = await box(p2, "[data-home-card] [data-home-card-body]");
    if (c) { await p2.mouse.move(c.x + c.width / 2, c.y + c.height / 2, { steps: 8 }); await p2.waitForTimeout(900); }
    await p2.mouse.move(480, 400, { steps: 8 }); await p2.waitForTimeout(400);
  }
  const v2 = p2.video();
  await ctx2.close();
  const dst2 = path.join(VID, "home_cards_960.webm");
  fs.renameSync(await v2.path(), dst2);
  checks.videos.push(dst2);
};

// No pause before the banner glide (0 frames between the banner starting and the nodes starting, every time including
// the first), and the node labels lighting up as one on hover.
S["2g"] = async () => {
  const { measureEvent } = await import("./glide_probe.mjs");
  const box = (p, sel) => p.locator(sel).first().boundingBox();
  const VID = path.resolve(here, "../../../docs/design/video/stage2g");
  fs.mkdirSync(VID, { recursive: true });
  const MAX_STEP_PX = 6;
  // the page is "idle after loading" once the intro has played: the first banner comes right then (a fresh load each
  // width, so the very first banner is measured)
  const readyIdle = async (p) => { await p.reload(); await p.waitForTimeout(4200); await p.mouse.move(5, 300); await p.waitForTimeout(800); };
  const flat = (p) => () => p.locator("[data-flat-all]").click();
  const gotIt = (p) => () => p.getByRole("button", { name: "Got it" }).first().click();
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `banner_gap_${w}`;
    const { ctx, page } = await open(name, w, h, "/");
    await readyIdle(page);
    const ev = [];
    // the very first banner, then it leaves; then three stacked, one after another, then all three leave
    ev.push({ e: "first arrives", ...(await measureEvent(page, flat(page))) });
    await page.waitForTimeout(400);
    ev.push({ e: "first leaves", ...(await measureEvent(page, gotIt(page))) });
    await page.waitForTimeout(400);
    // (info banners fade by themselves after ~5 s: all three arrive and leave within that, 500-ms windows)
    for (const k of [1, 2, 3]) { ev.push({ e: `${k} of 3 arrives`, ...(await measureEvent(page, flat(page), 500)) }); await page.waitForTimeout(50); }
    for (const k of [3, 2, 1]) { ev.push({ e: `${k} of 3 leaves`, ...(await measureEvent(page, gotIt(page), 500)) }); await page.waitForTimeout(50); }
    const late = await page.locator("[data-home]").getAttribute("data-lines-solved-late");
    checks[name] = { events: ev, linesReadyAhead: await page.locator("[data-home]").getAttribute("data-lines-ready"), solvedLate: late };
    if (late) problems.push(`${name}: lines solved late for height ${late}`);
    for (const x of ev) {
      if (x.gapFrames !== 0) problems.push(`${name} ${x.e}: ${x.gapFrames} frames between the banner and the nodes`);
      if (x.maxStepPx > MAX_STEP_PX) problems.push(`${name} ${x.e}: a frame jumped ${x.maxStepPx} px`);
      if (x.longestFrameMs > 60) problems.push(`${name} ${x.e}: a ${x.longestFrameMs}-ms frame (a pause)`);
    }
    await ctx.close();
  }
  // the node labels: every part lights up in the same frame, with in-between frames, and fades back the same way
  const parts = `() => { const b = document.querySelector('[data-home] button[aria-label^="Gameplan (key"]'); const l = b.querySelector("[data-node-label]");
    const [circle, name, kbd] = l.children; const cs = (e) => getComputedStyle(e);
    return [cs(l).borderColor + "|" + cs(l).backgroundColor + "|" + cs(l).boxShadow, cs(circle).borderColor + "|" + cs(circle).backgroundColor + "|" + cs(circle).color,
      cs(name).color, cs(kbd).borderColor + "|" + cs(kbd).color]; }`;
  const sampleAfterPaint = (p, ms, fnSrc) => p.evaluate(([ms, fnSrc]) => new Promise((res) => {
    const f = new Function(`return (${fnSrc})()`); const out = []; const t0 = performance.now();
    const ch = new MessageChannel(); const after = () => ch.port2.postMessage(0);
    ch.port1.onmessage = () => { out.push(f()); if (performance.now() - t0 < ms) requestAnimationFrame(after); else res(out); };
    requestAnimationFrame(after);
  }), [ms, fnSrc]);
  const starts = (frames) => frames[0].map((_, k) => frames.findIndex((f) => f[k] !== frames[0][k]));
  const settles = (frames) => frames[0].map((_, k) => { const last = frames.at(-1)[k]; let i = frames.length - 1; while (i > 0 && frames[i - 1][k] === last) i--; return i; });
  for (const calm of [false, true]) {
    const name = calm ? "label_hover_calm" : "label_hover";
    const { ctx, page } = await open(name, 1440, 900, "/", calm ? { reducedMotion: "reduce" } : {});
    await readyIdle(page);
    const b = await box(page, '[data-home] button[aria-label^="Gameplan (key"]');
    const inP = sampleAfterPaint(page, 400, parts);
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    const fin = await inP;
    if (!calm) await page.screenshot({ path: path.join(OUT, "label_lit_1440.png") });
    const outP = sampleAfterPaint(page, 400, parts);
    await page.mouse.move(5, 300);
    const fout = await outP;
    const r = { inStarts: starts(fin), inSettles: settles(fin), outStarts: starts(fout), outSettles: settles(fout) };
    r.inSameFrame = new Set(r.inStarts).size === 1; r.outSameFrame = new Set(r.outStarts).size === 1;
    r.inFrames = Math.max(...r.inSettles) - Math.min(...r.inStarts); r.outFrames = Math.max(...r.outSettles) - Math.min(...r.outStarts);
    checks[name] = r;
    if (!r.inSameFrame || !r.outSameFrame) problems.push(`${name}: the parts don't start together ${JSON.stringify(r)}`);
    if (!calm && (r.inFrames < 6 || r.outFrames < 6)) problems.push(`${name}: no smooth transition ${JSON.stringify(r)}`);
    if (calm && (r.inFrames > 1 || r.outFrames > 1)) problems.push(`${name}: not instant in Calm ${JSON.stringify(r)}`);
    await ctx.close();
  }
  // videos: the first banner right after loading, then three stacked arriving and leaving (1440 and 960); label hovers
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: w, height: h } } });
    const p = await ctx.newPage();
    await p.goto(BASE + "/");
    await p.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await readyIdle(p);
    await p.mouse.move(w / 2, h - 120);
    await p.locator("[data-flat-all]").click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(1500);
    await p.getByRole("button", { name: "Got it" }).first().click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(1300);
    for (let k = 0; k < 3; k++) { await p.locator("[data-flat-all]").click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(800); }
    for (let k = 0; k < 3; k++) { await p.getByRole("button", { name: "Got it" }).first().click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(700); }
    await p.waitForTimeout(600);
    if (w === 1440) {
      for (const n of ["Gameplan", "Live", "Review", "Performance", "Settings", "Accounts"]) {
        const b = await box(p, `[data-home] button[aria-label^="${n} (key"]`);
        await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 10 }); await p.waitForTimeout(900);
        await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2 + (n === "Live" || n === "Gameplan" || n === "Review" ? -70 : 70), { steps: 6 }); await p.waitForTimeout(600);
      }
    }
    const v = p.video();
    await ctx.close();
    const dst = path.join(VID, w === 1440 ? "home_banners_and_labels_1440.webm" : "home_banners_960.webm");
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// The lines morph between their old and new shapes on the nodes' curve (owner 2026-11-15): measured per painted frame.
S["2h"] = async () => {
  const { measureEvent } = await import("./glide_probe.mjs");
  const VID = path.resolve(here, "../../../docs/design/video/stage2h");
  fs.mkdirSync(VID, { recursive: true });
  const MAX_STEP_PX = 6;
  const readyIdle = async (p) => { await p.reload(); await p.waitForTimeout(4200); await p.mouse.move(5, 300); await p.waitForTimeout(800); };
  const flat = (p) => () => p.locator("[data-flat-all]").click();
  const gotIt = (p) => () => p.getByRole("button", { name: "Got it" }).first().click();
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `line_morph_${w}`;
    const { ctx, page } = await open(name, w, h, "/");
    await readyIdle(page);
    const ev = [];
    ev.push({ e: "first arrives", ...(await measureEvent(page, flat(page))) });
    await page.waitForTimeout(400);
    ev.push({ e: "first leaves", ...(await measureEvent(page, gotIt(page))) });
    await page.waitForTimeout(400);
    for (const k of [1, 2, 3]) { ev.push({ e: `${k} of 3 arrives`, ...(await measureEvent(page, flat(page), 500)) }); await page.waitForTimeout(50); }
    for (const k of [3, 2, 1]) { ev.push({ e: `${k} of 3 leaves`, ...(await measureEvent(page, gotIt(page), 500)) }); await page.waitForTimeout(50); }
    checks[name] = ev;
    for (const x of ev) {
      if (x.gapFrames !== 0 || x.lineGapFrames !== 0) problems.push(`${name} ${x.e}: banner/node/line start frames ${x.bannerStartFrame}/${x.nodeStartFrame}/${x.lineStartFrame}`);
      if (x.maxStepPx > MAX_STEP_PX) problems.push(`${name} ${x.e}: a node jumped ${x.maxStepPx} px`);
      if (x.maxLineStepPx > MAX_STEP_PX) problems.push(`${name} ${x.e}: a line point jumped ${x.maxLineStepPx} px`);
      if (x.lineMovingFrames < 10) problems.push(`${name} ${x.e}: lines changed in only ${x.lineMovingFrames} frames`);
      if (x.maxNodeEndOffPx > 0.5) problems.push(`${name} ${x.e}: a line's node end left its node by ${x.maxNodeEndOffPx} px`);
      if (x.longestFrameMs > 60) problems.push(`${name} ${x.e}: a ${x.longestFrameMs}-ms frame`);
    }
    await ctx.close();
  }
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: w, height: h } } });
    const p = await ctx.newPage();
    await p.goto(BASE + "/");
    await p.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await readyIdle(p);
    await p.mouse.move(w / 2, h - 120);
    await p.locator("[data-flat-all]").click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(1300);
    await p.getByRole("button", { name: "Got it" }).first().click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(1200);
    for (let k = 0; k < 3; k++) { await p.locator("[data-flat-all]").click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(800); }
    for (let k = 0; k < 3; k++) { await p.getByRole("button", { name: "Got it" }).first().click(); await p.mouse.move(w / 2, h - 120); await p.waitForTimeout(700); }
    await p.waitForTimeout(700);
    const v = p.video();
    await ctx.close();
    const dst = path.join(VID, `home_line_morph_${w}.webm`);
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// Page switches never shift the content sideways (owner 2026-11-15): with real 15-px scrollbars as on Windows (Playwright
// hides them by default), the content's left edge is measured after every painted frame.
S["2i"] = async () => {
  const { measureSwitch } = await import("./page_probe.mjs");
  const real = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"], ignoreDefaultArgs: ["--hide-scrollbars"] });
  const VID = path.resolve(here, "../../../docs/design/video/stage2i");
  fs.mkdirSync(VID, { recursive: true });
  const run = async (page, name) => {
    const steps = [["Home → Accounts", "3"], ["Accounts → Home", "1"], ["Home → Settings", "4"], ["Settings → Home", "1"], ["Home → Review (short)", "5"], ["Review → Accounts", "3"]];
    const res = [];
    for (const [label, key] of steps) {
      await page.mouse.move(5, 400); await page.waitForTimeout(key === "1" ? 400 : 200);
      const r = await measureSwitch(page, () => page.keyboard.press(key), 900);
      res.push({ switch: label, mainWidths: r.mainWidths, h1Lefts: r.h1Lefts, homeBoxes: r.homeBoxes, jumps: r.jumps, maxJumpPx: r.maxJumpPx });
      if (r.jumps) problems.push(`${name} ${label}: the content jumped ${r.maxJumpPx} px sideways`);
      if (r.mainWidths.length > 1) problems.push(`${name} ${label}: the content area changed width ${r.mainWidths.join(" → ")}`);
      await page.waitForTimeout(key === "1" ? 4200 : 600);
    }
    return res;
  };
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const ctx = await real.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", ...(w === 1440 ? { recordVideo: { dir: VID, size: { width: w, height: h } } } : {}) });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`switch_${w}: console ${m.text()}`); });
    await page.goto(BASE + "/");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(5000);
    checks[`switch_${w}`] = await run(page, `switch_${w}`);
    // centred: Home's scene and a long page's container (with its scrollbar showing) are centred on the window
    // (a heading is left-aligned inside its centred container: the container is measured)
    const centre = async (url, sel) => { await page.goto(BASE + url); await page.waitForTimeout(url === "/" ? 4500 : 1200);
      return page.evaluate((sel) => { const el = document.querySelector(sel); const r = (el.closest(".mx-auto") ?? el).getBoundingClientRect(); const m = document.getElementById("cf-main");
        return { offCentrePx: +((r.left + r.width / 2) - window.innerWidth / 2).toFixed(2), scrollbar: m.scrollHeight > m.clientHeight }; }, sel); };
    const cen = { home: await centre("/", "[data-home]"), accounts: await centre("/accounts", "main h1"), settings: await centre("/settings", "main h1") };
    checks[`centred_${w}`] = cen;
    for (const [k, v] of Object.entries(cen)) if (Math.abs(v.offCentrePx) > 0.5) problems.push(`centred_${w}: ${k} is ${v.offCentrePx} px off the window's centre`);
    await page.keyboard.press("2"); await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, `gutter_gameplan_${w}.png`) });
    await page.keyboard.press("3"); await page.waitForTimeout(900);
    await page.screenshot({ path: path.join(OUT, `gutter_accounts_${w}.png`) });
    const v = page.video();
    await ctx.close();
    if (v) { const dst = path.join(VID, `page_switches_${w}.webm`); fs.renameSync(await v.path(), dst); (checks.videos ??= []).push(dst); }
  }
  await real.close();
};

// Stage 3: Performance (SPEC §9b item 6). Run against `nqbot ui --synthetic` (stage "3"), and against the plain
// `nqbot ui` for the empty state (stage "3e").
S["3"] = async () => {
  const tab = (p, name) => p.getByRole("tab", { name, exact: true }).first().click();
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `perf_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.removeItem("creamflow.focusAccount"));
    await page.reload(); await page.waitForTimeout(2200);
    const c = (checks[name] = {});
    c.syntheticChip = (await page.locator("[data-synthetic-chip]").count()) === 1;
    c.notices = await page.locator("[data-notice]").evaluateAll((els) => els.map((e) => e.getAttribute("data-notice")));
    c.kpis = await page.locator("[data-kpis] > *").count();
    await page.screenshot({ path: path.join(OUT, `performance_equity_${w}.png`) });
    await page.screenshot({ path: path.join(OUT, `performance_full_${w}.png`), fullPage: false });
    // live vs expected sits inside the Equity panel
    await page.getByRole("tab", { name: "Live vs expected", exact: true }).first().click(); await page.waitForTimeout(800);
    await page.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
    c.liveVsExpected = (await page.locator("[data-live-vs-expected]").count()) === 1;
    await page.screenshot({ path: path.join(OUT, `performance_live_${w}.png`) });
    await page.getByRole("tab", { name: "Equity curve", exact: true }).first().click(); await page.waitForTimeout(300);
    for (const [t, file] of [["Drawdown", "drawdown"], ["Monte Carlo", "montecarlo"], ["Calendar", "calendar"], ["Trades", "trades"]]) {
      await tab(page, t); await page.waitForTimeout(t === "Monte Carlo" ? 1800 : 1000);
      // scroll the panel into view
      await page.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
      if (t === "Monte Carlo") c.pBreach = await page.locator("[data-p-breach]").textContent();
      if (t === "Calendar") {
        c.calendarDays = await page.locator("[data-day][data-sign]:not([data-sign=none])").count();
        await page.screenshot({ path: path.join(OUT, `performance_${file}_${w}.png`) });
        const day = page.locator("[data-day][data-sign='1']").first();
        if (await day.count()) { await day.click(); await page.waitForTimeout(700); c.dayPanelRows = await page.locator("[data-day-panel] tbody tr").count(); await page.screenshot({ path: path.join(OUT, `performance_calendar_day_${w}.png`) }); await page.keyboard.press("Escape"); await page.getByRole("button", { name: "Close" }).click(); }
        // a walk-forward month says why it is empty
        const months = await page.locator("select[aria-label=Month] option").evaluateAll((o) => o.map((x) => x.value));
        c.calendarMonths = months;
        continue;
      }
      if (t === "Trades") c.tradeRows = await page.locator("[data-trades-table] tbody tr").count();
      await page.screenshot({ path: path.join(OUT, `performance_${file}_${w}.png`) });
    }
    // the guard: picking Walk-forward 7-9 is refused with the missing gate
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 }));
    await page.getByRole("button", { name: /^Phase:/ }).click(); await page.waitForTimeout(250);
    await page.getByRole("option", { name: /Walk-forward/ }).click(); await page.waitForTimeout(900);
    c.refused = (await page.locator("[data-refused]").count()) === 1;
    await page.screenshot({ path: path.join(OUT, `performance_refused_${w}.png`) });
    await page.getByRole("button", { name: "All phases" }).click(); await page.waitForTimeout(800);
    // CSV export downloads, and holds back walk-forward / sealed
    if (w === 1440) {
      const [dl] = await Promise.all([page.waitForEvent("download"), page.locator("[data-export-csv]").click()]);
      const file = path.join(OUT, "export_sample.csv");
      await dl.saveAs(file);
      const lines = fs.readFileSync(file, "utf8").trim().split("\n");
      c.csvRows = lines.length - 1;
      fs.unlinkSync(file);
    }
    for (const [k, v] of Object.entries(c)) if (v === false || v === 0) problems.push(`${name}: ${k} = ${JSON.stringify(v)}`);
    if (!c.notices.includes("walk_forward") || !c.notices.includes("sealed")) problems.push(`${name}: guard notices ${c.notices}`);
    await ctx.close();
  }
};
S["3e"] = async () => {
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `perf_empty_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.reload(); await page.waitForTimeout(2200);
    checks[name] = { empty: (await page.locator("[data-empty-state]").count()) === 1, synthetic: await page.locator("[data-synthetic-chip]").count() };
    await page.screenshot({ path: path.join(OUT, `performance_empty_${w}.png`) });
    if (!checks[name].empty || checks[name].synthetic) problems.push(`${name}: ${JSON.stringify(checks[name])}`);
    await ctx.close();
  }
};

// Stage 3 changes (owner 2026-11-15): Monte Carlo and Drawdown by account type (backtest phases) or one account (real
// trades), the draw-out, Net $ per trade, and no footer flash. Against `nqbot ui --synthetic`.
S["3b"] = async () => {
  const VID = path.resolve(here, "../../../docs/design/video/stage3b");
  fs.mkdirSync(VID, { recursive: true });
  const tab = (p, name) => p.getByRole("tab", { name, exact: true }).first().click();
  const phase = async (p, label) => { await p.getByRole("button", { name: /^Phase:/ }).click(); await p.waitForTimeout(200); await p.getByRole("option", { name: label }).click(); await p.waitForTimeout(900); };
  const toPanel = async (p) => { await p.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded(); await p.waitForTimeout(250); };
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `perf_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2000);
    const c = (checks[`modes_${w}`] = {});
    // KPI label follows the unit
    c.kpiUsd = (await page.locator("[data-kpis]").innerText()).includes("Net $ per trade".toUpperCase()) || (await page.locator("[data-kpis]").innerText()).toLowerCase().includes("net $ per trade");
    await page.getByRole("tab", { name: "R", exact: true }).first().click(); await page.waitForTimeout(400);
    c.kpiR = (await page.locator("[data-kpis]").innerText()).toLowerCase().includes("net r per trade");
    await page.getByRole("tab", { name: "$", exact: true }).first().click(); await page.waitForTimeout(300);
    // the account filter: All accounts (the focus is mffur1)
    await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(200);
    await page.getByRole("option", { name: /All accounts/ }).click(); await page.waitForTimeout(900);
    // Monte Carlo, backtest phase: by account type; numbers at once while the fan draws
    await tab(page, "Monte Carlo"); await page.waitForTimeout(350);
    c.mcTypesMode = await page.locator("[data-risk-mode]").getAttribute("data-risk-mode");
    c.numbersWhileDrawing = await page.evaluate(() => ({ drawn: document.querySelector("[data-montecarlo]")?.getAttribute("data-drawn"), p: document.querySelector("[data-p-breach]")?.textContent }));
    c.stripRows = await page.locator("[data-type-row]").count();
    await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `montecarlo_types_drawing_${w}.png`) });
    await page.waitForTimeout(1500);
    c.drawnAfter = await page.locator("[data-montecarlo]").getAttribute("data-drawn");
    await page.screenshot({ path: path.join(OUT, `montecarlo_types_${w}.png`) });
    // switching type redraws; a key skips to the end
    await page.locator("[data-type-row='lucid_flex_25k_funded']").click(); await page.waitForTimeout(400);
    c.redrawOnSwitch = await page.locator("[data-montecarlo]").getAttribute("data-drawn");
    await page.keyboard.press("Shift"); await page.waitForTimeout(80);
    c.keySkips = await page.locator("[data-montecarlo]").getAttribute("data-drawn");
    await page.screenshot({ path: path.join(OUT, `montecarlo_types_lucid_${w}.png`) });
    // Drawdown, backtest phase: by account type
    await tab(page, "Drawdown"); await page.waitForTimeout(900); await toPanel(page);
    c.ddTypesMode = await page.locator("[data-risk-mode]").getAttribute("data-risk-mode");
    await page.screenshot({ path: path.join(OUT, `drawdown_types_${w}.png`) });
    // real trades: phase Prop -> one actual account (the focused one, with the note)
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 }));
    await phase(page, "Prop");
    await toPanel(page);
    c.ddAccountMode = await page.locator("[data-risk-mode]").getAttribute("data-risk-mode");
    c.ddNote = await page.locator("[data-account-note]").first().textContent().catch(() => null);
    await page.screenshot({ path: path.join(OUT, `drawdown_account_${w}.png`) });
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1700); await toPanel(page);
    c.mcAccountMode = await page.locator("[data-risk-mode]").getAttribute("data-risk-mode");
    c.mcStateLine = await page.locator("[data-state-line]").textContent().catch(() => null);
    c.mcNote = await page.locator("[data-account-note]").first().textContent().catch(() => null);
    await page.screenshot({ path: path.join(OUT, `montecarlo_account_${w}.png`) });
    const ok = c.kpiUsd && c.kpiR && c.mcTypesMode === "types" && c.numbersWhileDrawing.drawn === "0" && c.numbersWhileDrawing.p && c.stripRows >= 10 &&
      c.drawnAfter === "1" && c.redrawOnSwitch === "0" && c.keySkips === "1" && c.ddTypesMode === "types" && c.ddAccountMode === "account" &&
      c.mcAccountMode === "account" && /SYNTHETIC/.test(c.mcStateLine ?? "") && /showing mffur1/.test(c.mcNote ?? "");
    if (!ok) problems.push(`modes_${w}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // Calm: drawn at once
  {
    const name = "mc_calm";
    const { ctx, page } = await open(name, 1440, 900, "/performance", { reducedMotion: "reduce" });
    await page.reload(); await page.waitForTimeout(1800);
    await tab(page, "Monte Carlo"); await page.waitForTimeout(300);
    checks[name] = { drawn: await page.locator("[data-montecarlo]").getAttribute("data-drawn") };
    if (checks[name].drawn !== "1") problems.push(`${name}: not drawn at once in Calm`);
    await ctx.close();
  }
  // the footer never shows before the content: every painted frame of Home -> page (keys 6, 3, 4) and back
  const footerCheck = async (page, key, content, ms = 1600) => {
    const pending = page.evaluate(([content, ms]) => new Promise((res) => {
      const out = []; const t0 = performance.now();
      const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        if (r.top >= innerHeight || r.bottom <= 0 || r.height === 0) return false;
        let o = 1; for (let e = el; e; e = e.parentElement) o *= +getComputedStyle(e).opacity;
        return o > 0.02;
      };
      const ch = new MessageChannel(); const after = () => ch.port2.postMessage(0);
      ch.port1.onmessage = () => {
        const f = document.querySelector("[data-page-footer]") ?? document.querySelector(".hwf-root, [data-halftone-footer]");
        out.push({ t: Math.round(performance.now() - t0), footer: visible(f), content: !!document.querySelector(content) });
        if (performance.now() - t0 < ms) requestAnimationFrame(after); else res(out);
      };
      requestAnimationFrame(after);
    }), [content, ms]);
    await page.keyboard.press(key);
    const f = await pending;
    const bad = f.filter((x) => x.footer && !x.content);
    return { frames: f.length, footerBeforeContent: bad.length, firstContentMs: f.find((x) => x.content)?.t ?? null, firstFooterMs: f.find((x) => x.footer)?.t ?? null };
  };
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", ...(w === 1440 ? { recordVideo: { dir: VID, size: { width: w, height: h } } } : {}) });
    const page = await ctx.newPage();
    await page.goto(BASE + "/");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(4500); await page.mouse.move(5, 300);
    const r = {};
    for (const [label, key, content] of [["performance", "6", "[data-kpis]"], ["accounts", "3", "[data-account-card]"], ["settings", "4", "#set-profile"]]) {
      r[label] = await footerCheck(page, key, content);
      await page.waitForTimeout(400);
      await page.keyboard.press("1"); await page.waitForTimeout(2600); await page.mouse.move(5, 300);
    }
    checks[`footer_${w}`] = r;
    for (const [k, v] of Object.entries(r)) if (v.footerBeforeContent) problems.push(`footer_${w} ${k}: the footer showed in ${v.footerBeforeContent} frames before the content`);
    const v = page.video();
    await ctx.close();
    if (v) { const dst = path.join(VID, "footer_check_1440.webm"); fs.renameSync(await v.path(), dst); (checks.videos ??= []).push(dst); }
  }
  // the draw-out video: open the Monte Carlo, switch types twice, skip one with a key
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/performance");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(2200);
    await page.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded();
    await tab(page, "Monte Carlo"); await page.waitForTimeout(500);
    await page.locator("[data-type-strip]").scrollIntoViewIfNeeded(); await page.waitForTimeout(1800);
    await page.locator("[data-type-row='mffu_builder_25k_funded']").click(); await page.waitForTimeout(2000);
    await page.locator("[data-type-row='lucid_flex_25k_evaluation']").click(); await page.waitForTimeout(450);
    await page.keyboard.press("Shift"); await page.waitForTimeout(1500);
    const v = page.video();
    await ctx.close();
    const dst = path.join(VID, "montecarlo_drawout_1440.webm");
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// Plans and their lifecycle (owner 2026-11-15): the strategy view per plan, and my-account mode. `--synthetic`.
S["3c"] = async () => {
  const VID = path.resolve(here, "../../../docs/design/video/stage3c");
  fs.mkdirSync(VID, { recursive: true });
  const tab = (p, name) => p.getByRole("tab", { name, exact: true }).first().click();
  const toPanel = async (p) => { await p.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded(); await p.waitForTimeout(250); };
  const pickPlan = async (p, label) => {
    await p.getByRole("button", { name: /^Plan:/ }).click(); await p.waitForTimeout(250);
    await p.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${label}`) }).click(); await p.waitForTimeout(1300);
  };
  const plans = ["MFFU Rapid EOD 25k", "MFFU Builder 25k", "MFFU Pro 50k", "Lucid Flex 25k"];
  for (const [w, h, shown] of [[1440, 900, plans], [960, 800, ["MFFU Rapid EOD 25k", "MFFU Pro 50k"]]]) {
    const name = `plans_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2000);
    const c = (checks[name] = { plans: {} });
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1700); await toPanel(page);
    c.modeTitle = await page.locator("[data-mode-title]").innerText();
    c.strategyNote = await page.locator("[data-strategy-note]").count();
    c.compareByDefault = await page.locator("[data-compare]").count();
    // the plan menu: 5 at a time (only 4 plans now: no scrolling needed), opened for the shot
    await page.getByRole("button", { name: /^Plan:/ }).click(); await page.waitForTimeout(300);
    c.menuMaxHeight = await page.locator("[data-menu-list]").evaluate((e) => getComputedStyle(e).maxHeight);
    await page.screenshot({ path: path.join(OUT, `plan_menu_${w}.png`) });
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    for (const pl of shown) {
      const slug = pl.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      await tab(page, "Monte Carlo"); await page.waitForTimeout(400);
      await pickPlan(page, pl);
      await page.keyboard.press("Shift"); await page.waitForTimeout(150);       // skip the draw-out for the still
      await toPanel(page);
      const nums = await page.locator("[data-plan-numbers]").innerText();
      await page.screenshot({ path: path.join(OUT, `montecarlo_${slug}_${w}.png`) });
      await tab(page, "Drawdown"); await page.waitForTimeout(1300); await toPanel(page);
      const evals = await page.locator("[data-evaluations]").textContent();
      await page.screenshot({ path: path.join(OUT, `drawdown_${slug}_${w}.png`) });
      c.plans[pl] = { numbers: nums.replace(/\s+/g, " ").slice(0, 200), evaluations: evals };
    }
    // horizon switch and Compare plans
    await tab(page, "Monte Carlo"); await page.waitForTimeout(600);
    await page.getByRole("tab", { name: "120 days", exact: true }).click(); await page.waitForTimeout(1200);
    await page.keyboard.press("Shift"); await page.waitForTimeout(150);
    c.horizon120 = (await page.locator("[data-plan-numbers]").innerText()).includes("day 120");
    await page.locator("[data-compare-toggle]").click(); await page.waitForTimeout(1500);
    c.compareRows = await page.locator("[data-compare-row]").count();
    await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `montecarlo_compare_120_${w}.png`) });
    // my account (real trades): phase Prop
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 }));
    await page.getByRole("button", { name: /^Phase:/ }).click(); await page.waitForTimeout(200);
    await page.getByRole("option", { name: "Prop" }).click(); await page.waitForTimeout(1800);
    await toPanel(page);
    c.accountTitle = await page.locator("[data-mode-title]").innerText();
    await page.screenshot({ path: path.join(OUT, `montecarlo_my_account_${w}.png`) });
    const ok = /Strategy/.test(c.modeTitle) && c.strategyNote === 1 && c.compareByDefault === 0 && c.horizon120 && c.compareRows === 4 &&
      /My account/.test(c.accountTitle) && Object.values(c.plans).every((x) => /Pass rate/.test(x.numbers) && Number(x.evaluations) >= 1);
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // the draw-out video: open the Monte Carlo, switch plans, switch to 120 days, skip one with a key
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/performance");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(2200);
    await toPanel(page);
    await tab(page, "Monte Carlo"); await page.waitForTimeout(400);
    await page.locator("[data-mode-title]").scrollIntoViewIfNeeded(); await page.waitForTimeout(1800);
    await pickPlan(page, "Lucid Flex 25k"); await page.waitForTimeout(1200);
    await page.getByRole("tab", { name: "120 days", exact: true }).click(); await page.waitForTimeout(500);
    await page.keyboard.press("Shift"); await page.waitForTimeout(1200);
    await pickPlan(page, "MFFU Pro 50k"); await page.waitForTimeout(1600);
    const v = page.video();
    await ctx.close();
    const dst = path.join(VID, "plans_drawout_1440.webm");
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// The strategy view by plan with payouts (owner 2026-11-15, rework 3): the plan selector in backtest phases, every
// number by plan, the lifecycle with payouts, stage markers kept inside the chart, the design variants, the real-phase
// account sync. `--synthetic`.
S["3d"] = async () => {
  const VID = path.resolve(here, "../../../docs/design/video/stage3d");
  fs.mkdirSync(VID, { recursive: true });
  const tab = (p, name) => p.getByRole("tab", { name, exact: true }).first().click();
  const toPanel = async (p) => { await p.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded(); await p.waitForTimeout(250); };
  const top = (p) => p.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 }));
  const menuPick = async (p, label, option) => {
    await p.getByRole("button", { name: new RegExp(`^${label}:`) }).click(); await p.waitForTimeout(250);
    await p.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${option}`) }).click(); await p.waitForTimeout(1300);
  };
  // every marker inside the chart's plot area; a hovered marker's tooltip inside the chart
  const markerCheck = async (p) => p.evaluate(() => {
    const out = [];
    for (const ch of document.querySelectorAll("[data-lifecycle-chart]")) {
      const r = ch.getBoundingClientRect();
      for (const m of ch.querySelectorAll("[data-marker]")) {
        const b = m.getBoundingClientRect();
        if (b.left < r.left || b.right > r.right - 78 + 1 || b.top < r.top || b.bottom > r.bottom - 20 + 1) out.push({ kind: m.dataset.marker, b: [b.left, b.top, b.right, b.bottom], r: [r.left, r.top, r.right, r.bottom] });
      }
    }
    return out;
  });
  const tipInside = async (p, sel) => {
    const m = p.locator(`[data-lifecycle-chart] ${sel}`);
    if (!(await m.count())) return null;
    await m.hover(); await p.waitForTimeout(250);
    return p.evaluate(() => {
      const ch = document.querySelector("[data-lifecycle-chart]").getBoundingClientRect();
      const t = document.querySelector("[data-chart-tip]");
      if (!t) return false;
      const b = t.getBoundingClientRect();
      return b.left >= ch.left - 0.5 && b.right <= ch.right + 0.5 && b.top >= ch.top - 0.5 && b.bottom <= ch.bottom + 0.5;
    });
  };
  const plans = ["MFFU Rapid EOD 25k", "MFFU Builder 25k", "MFFU Pro 50k", "Lucid Flex 25k"];
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/_$/, "");
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `strategy_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2000);
    const c = (checks[name] = { plans: {} });
    await menuPick(page, "Phase", "Development");
    c.planMenu = await page.getByRole("button", { name: /^Plan:/ }).count();
    c.accountMenuInBacktest = await page.getByRole("button", { name: /^Account:/ }).count();
    // the plan menu, open
    await page.getByRole("button", { name: /^Plan:/ }).click(); await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, `plan_menu_${w}.png`) });
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    for (const pl of plans) {
      await top(page);
      await menuPick(page, "Plan", pl);
      await page.waitForTimeout(900);
      c.plans[pl] = { kpiNet: await page.locator("[data-kpis] article").first().locator(".sr-only").first().textContent() };
      if (w === 1440 && pl === plans[0]) await page.screenshot({ path: path.join(OUT, `kpis_stats_a_${w}.png`) });
      await tab(page, "Monte Carlo"); await page.waitForTimeout(1500);
      await page.keyboard.press("Shift"); await page.waitForTimeout(150);      // skip the draw-out for the still
      await toPanel(page);
      c.plans[pl].mc = (await page.locator("[data-plan-numbers]").innerText()).replace(/\s+/g, " ").slice(0, 260);
      c.plans[pl].mcMarkersOutside = await markerCheck(page);
      await page.screenshot({ path: path.join(OUT, `montecarlo_${slug(pl)}_${w}.png`) });
      await tab(page, "Drawdown"); await page.waitForTimeout(1300); await toPanel(page);
      c.plans[pl].replay = (await page.locator("[data-replay-summary]").innerText()).replace(/\s+/g, " ");
      c.plans[pl].ddMarkersOutside = await markerCheck(page);
      c.plans[pl].markers = await page.locator("[data-lifecycle-chart] [data-marker]").count();
      await page.screenshot({ path: path.join(OUT, `drawdown_${slug(pl)}_${w}.png`) });
      // the first and the last marker's tooltips stay inside the chart (the edges)
      c.plans[pl].tipFirst = await tipInside(page, "[data-marker] >> nth=0");
      if (pl === plans[0]) await page.screenshot({ path: path.join(OUT, `marker_tip_first_${w}.png`) });
      c.plans[pl].tipLast = await tipInside(page, "[data-marker] >> nth=-1");
      if (pl === plans[0]) await page.screenshot({ path: path.join(OUT, `marker_tip_last_${w}.png`) });
      await page.mouse.move(5, 5);
      await tab(page, "Equity"); await page.waitForTimeout(300);
    }
    // a payout marker's tooltip, and the crosshair tooltip
    await top(page); await menuPick(page, "Plan", "Lucid Flex 25k");
    await tab(page, "Drawdown"); await page.waitForTimeout(1300); await toPanel(page);
    c.tipPayout = await tipInside(page, "[data-marker=payout] >> nth=2");
    await page.screenshot({ path: path.join(OUT, `marker_tip_payout_${w}.png`) });
    // the calendar and the trades by plan
    await tab(page, "Calendar"); await page.waitForTimeout(1200); await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `calendar_lucid_${w}.png`) });
    // Monte Carlo at 120 days with Compare plans
    await tab(page, "Monte Carlo"); await page.waitForTimeout(800);
    await page.getByRole("tab", { name: "120 days", exact: true }).click(); await page.waitForTimeout(1200);
    await page.keyboard.press("Shift"); await page.waitForTimeout(150);
    await page.locator("[data-compare-toggle]").click(); await page.waitForTimeout(1800);
    c.compareRows = await page.locator("[data-compare-row]").count();
    await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `montecarlo_compare_120_${w}.png`) });
    // real trades: the page's account filter and the top-bar switcher are one selection, both ways
    await top(page);
    await menuPick(page, "Phase", "Prop");
    c.planMenuInReal = await page.getByRole("button", { name: /^Plan:/ }).count();
    c.accountStart = await page.getByRole("button", { name: /^Account:/ }).getAttribute("aria-label");
    c.allInReal = await (async () => { await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(250);
      const n = await page.locator("[data-menu-list]").getByRole("option", { name: /^All accounts/ }).count(); await page.keyboard.press("Escape"); return n; })();
    await menuPick(page, "Account", "mffub1");
    c.topbarAfterPage = await page.locator("[data-topbar] button[aria-label^='Account focus']").getAttribute("aria-label");
    await page.locator("[data-topbar] button[aria-label^='Account focus']").click(); await page.waitForTimeout(350);
    await page.locator("[data-switcher-row=mffur1]").click(); await page.waitForTimeout(1200);
    c.pageAfterTopbar = await page.getByRole("button", { name: /^Account:/ }).getAttribute("aria-label");
    await page.screenshot({ path: path.join(OUT, `real_sync_${w}.png`) });
    const plansOk = Object.values(c.plans).every((x) => /Evaluations used/i.test(x.mc) && x.mcMarkersOutside.length === 0 && x.ddMarkersOutside.length === 0 && x.tipFirst !== false && x.tipLast !== false && x.markers > 0);
    const differ = c.plans[plans[0]].kpiNet !== c.plans[plans[2]].kpiNet;
    const ok = c.planMenu === 1 && c.accountMenuInBacktest === 0 && plansOk && differ && c.compareRows === 4 && c.tipPayout !== false &&
      c.planMenuInReal === 0 && c.allInReal === 0 && /mffub1/.test(c.topbarAfterPage ?? "") && /mffur1/.test(c.pageAfterTopbar ?? "");
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // variant B of the Monte Carlo summary and the stats box
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `variant_b_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance?variant=b");
    await page.reload(); await page.waitForTimeout(2000);
    await menuPick(page, "Phase", "Development");
    checks[name] = { stats: await page.locator("[data-stats-panel]").getAttribute("data-variant") };
    await page.screenshot({ path: path.join(OUT, `kpis_stats_b_${w}.png`) });
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1500); await page.keyboard.press("Shift"); await page.waitForTimeout(150);
    await toPanel(page);
    checks[name].summary = await page.locator("[data-plan-numbers]").getAttribute("data-variant");
    await page.screenshot({ path: path.join(OUT, `montecarlo_summary_b_${w}.png`) });
    if (checks[name].stats !== "b" || checks[name].summary !== "b") problems.push(`${name}: variant B not shown`);
    await ctx.close();
  }
  // stats box A at 960
  await shot("kpis_stats_a_960", 960, 800, "/performance", async (p) => { await menuPick(p, "Phase", "Development"); });
  // the draw-out video: Monte Carlo, switch plans, 120 days, skip one with a key, the replay with marker tooltips
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/performance");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(2200);
    await menuPick(page, "Phase", "Development");
    await tab(page, "Monte Carlo"); await page.waitForTimeout(300);
    await toPanel(page); await page.waitForTimeout(1800);
    await top(page); await menuPick(page, "Plan", "Lucid Flex 25k"); await toPanel(page); await page.waitForTimeout(1800);
    await page.getByRole("tab", { name: "120 days", exact: true }).click(); await page.waitForTimeout(500);
    await page.keyboard.press("Shift"); await page.waitForTimeout(1200);
    await top(page); await menuPick(page, "Plan", "MFFU Pro 50k"); await toPanel(page); await page.waitForTimeout(1800);
    await tab(page, "Drawdown"); await page.waitForTimeout(1200); await toPanel(page);
    for (const sel of ["[data-marker] >> nth=0", "[data-marker=payout] >> nth=0", "[data-marker] >> nth=-1"]) {
      const m = page.locator(`[data-lifecycle-chart] ${sel}`);
      if (await m.count()) { await m.hover(); await page.waitForTimeout(900); }
    }
    const v = page.video();
    await ctx.close();
    const dst = path.join(VID, "strategy_drawout_1440.webm");
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// Rework 4 (owner 2026-11-15): one layout in every phase with a crossfade and gliding numbers, no "All phases", the
// account's own-stage Monte Carlo, the floor and backtest-order legend tooltips, horizon + zoom on every chart, KPI
// sparklines per trading day, profit factor ∞. `--synthetic` on 127.0.0.1:8733.
const helpers3f = {
  tab: (p, name) => p.getByRole("tab", { name, exact: true }).first().click(),
  toPanel: async (p) => { await p.locator("[data-page=performance] .mk-beam").first().scrollIntoViewIfNeeded(); await p.waitForTimeout(250); },
  top: (p) => p.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 })),
  menuPick: async (p, label, option) => {
    await p.getByRole("button", { name: new RegExp(`^${label}:`) }).click(); await p.waitForTimeout(250);
    await p.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${option}`) }).click(); await p.waitForTimeout(1400);
  },
  rects: (p) => p.evaluate(() => {
    const r = (sel) => { const e = document.querySelector(sel); if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top + document.querySelector("#cf-main").scrollTop), Math.round(b.width), Math.round(b.height)]; };
    return { kpis: r("[data-kpis]"), stats: r("[data-stats-panel]"), slot: r("[data-filter-slot]"), header: r("[data-chart-header]"), chart: r("[data-lifecycle-chart]"), legend: r("[data-legend]") };
  }),
  // markers and the floor label inside the plot area
  inside: (p) => p.evaluate(() => {
    const out = [];
    for (const ch of document.querySelectorAll("[data-lifecycle-chart]")) {
      const r = ch.getBoundingClientRect();
      for (const m of ch.querySelectorAll("[data-marker], [data-floor-label]")) {
        if (getComputedStyle(m).opacity === "0") continue;
        const b = m.getBoundingClientRect();
        if (b.left < r.left - 0.5 || b.right > r.right - 78 + 0.5 || b.top < r.top - 0.5 || b.bottom > r.bottom - 20 + 0.5) out.push(m.dataset.marker ?? "floor-label");
      }
    }
    return out;
  }),
  // per-frame samples during a switch: how many views the crossfade holds, the Net P&L tile's text
  startSampler: (p) => p.evaluate(() => {
    window.__s = [];
    const t0 = performance.now();
    const f = () => {
      const tile = document.querySelector("[data-kpi='Net P&L'] [data-slot=kpi-tile] p + div span");
      window.__s.push({ t: Math.round(performance.now() - t0), views: document.querySelectorAll("[data-risk-tab] > div").length,
        net: tile ? tile.textContent : null });
      if (performance.now() - t0 < 2500) requestAnimationFrame(f);
    };
    requestAnimationFrame(f);
  }),
};

S["3f"] = async () => {
  const { tab, toPanel, top, menuPick, rects, inside, startSampler } = helpers3f;
  const VID = path.resolve(here, "../../../docs/design/video/stage3f");
  fs.mkdirSync(VID, { recursive: true });
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `phases_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2200);
    const c = (checks[name] = {});
    c.phaseButton = await page.getByRole("button", { name: /^Phase:/ }).getAttribute("aria-label");
    await page.getByRole("button", { name: /^Phase:/ }).click(); await page.waitForTimeout(250);
    c.phaseOptions = await page.locator("[data-menu-list] [role=option]").allInnerTexts();
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    // the same layout in Development and Prop, on every tab
    c.layout = {};
    for (const t of ["Equity", "Drawdown", "Monte Carlo"]) {
      await top(page); await tab(page, t); await page.waitForTimeout(1500); await page.keyboard.press("Shift");
      const dev = await rects(page);
      await page.screenshot({ path: path.join(OUT, `dev_${t.toLowerCase().replace(" ", "")}_${w}.png`), fullPage: false });
      await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800); await page.keyboard.press("Shift");
      const prop = await rects(page);
      await toPanel(page);
      await page.screenshot({ path: path.join(OUT, `prop_${t.toLowerCase().replace(" ", "")}_${w}.png`) });
      const same = (a, b, keys) => keys.every((k) => (a[k] === null && b[k] === null) || (a[k] && b[k] && a[k].every((v, i) => Math.abs(v - b[k][i]) <= 1)));
      c.layout[t] = { same: same(dev, prop, ["kpis", "stats", "slot", "header"]) && (t === "Equity" || same(dev, prop, ["chart"])), dev, prop };
      await top(page); await menuPick(page, "Phase", "Development");
    }
    // the KPI row and the stats box in Prop (the account's own days; profit factor ∞ if it has no losing trade)
    await menuPick(page, "Phase", "Prop"); await top(page); await page.waitForTimeout(600);
    c.pfTile = (await page.locator("[data-kpi='Profit factor']").innerText()).replace(/\s+/g, " ");
    c.badge = (await page.locator("[data-kpi='Net P&L']").innerText()).replace(/\s+/g, " ");
    await page.screenshot({ path: path.join(OUT, `prop_kpis_${w}.png`) });
    // the account's own-stage Monte Carlo (mffur1, an evaluation) and Drawdown
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1700); await page.keyboard.press("Shift"); await toPanel(page);
    c.accountMc = (await page.locator("[data-account-numbers]").innerText()).replace(/\s+/g, " ");
    c.noOldHeadline = !(await page.locator("body").innerText()).includes("P(reaching the floor");
    c.horizonMcAccount = await page.getByRole("tab", { name: "120 days", exact: true }).count();
    await page.screenshot({ path: path.join(OUT, `account_mc_evaluation_${w}.png`) });
    await tab(page, "Drawdown"); await page.waitForTimeout(1300); await toPanel(page);
    c.horizonDdAccount = await page.getByRole("tab", { name: "120 days", exact: true }).count();
    await page.screenshot({ path: path.join(OUT, `account_drawdown_${w}.png`) });
    // back to Development: legend tooltips, horizon and zoom on the plan views
    await top(page); await menuPick(page, "Phase", "Development");
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1600); await page.keyboard.press("Shift"); await toPanel(page);
    c.horizonMcPlans = await page.getByRole("tab", { name: "120 days", exact: true }).count();
    for (const k of ["floor-base", "base"]) {
      await page.locator(`[data-legend-item=${k}]`).hover(); await page.waitForTimeout(900);
      c[`hint_${k}`] = await page.locator("[data-hint-tip]").innerText().catch(() => null);
      await page.screenshot({ path: path.join(OUT, `legend_hint_${k}_${w}.png`) });
      await page.mouse.move(2, 2); await page.waitForTimeout(300);
    }
    await tab(page, "Drawdown"); await page.waitForTimeout(1300); await toPanel(page);
    c.horizonDdPlans = await page.getByRole("tab", { name: "120 days", exact: true }).count();
    const chart = page.locator("[data-lifecycle-chart]");
    const b = await chart.boundingBox();
    await page.mouse.move(b.x + b.width * 0.35, b.y + b.height * 0.5);
    for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, -240); await page.waitForTimeout(60); }
    await page.waitForTimeout(300);
    c.zoomed = await chart.getAttribute("data-zoomed");
    c.zoomOutside = await inside(page);
    c.floorLabelZoomed = await page.locator("[data-floor-label]").count();
    await page.screenshot({ path: path.join(OUT, `zoomed_${w}.png`) });
    await page.mouse.move(b.x + b.width * 0.5, b.y + b.height * 0.5); await page.mouse.down();
    await page.mouse.move(b.x + b.width * 0.2, b.y + b.height * 0.5, { steps: 12 }); await page.mouse.up(); await page.waitForTimeout(300);
    c.panOutside = await inside(page);
    await page.screenshot({ path: path.join(OUT, `zoomed_panned_${w}.png`) });
    await chart.dblclick({ position: { x: b.width * 0.5, y: b.height * 0.5 } }); await page.waitForTimeout(300);
    c.reset = await chart.getAttribute("data-zoomed");
    const ok = !c.phaseOptions.some((o) => /All phases/.test(o)) && /Development/.test(c.phaseButton) &&
      Object.values(c.layout).every((x) => x.same) && /Pass before failing/i.test(c.accountMc) && c.noOldHeadline &&
      [c.horizonMcAccount, c.horizonDdAccount, c.horizonMcPlans, c.horizonDdPlans].every((n) => n >= 1) &&
      /own trailing floor/.test(c.hint_floor_base ?? c["hint_floor-base"] ?? "") && /original order/.test(c.hint_base ?? "") &&
      c.zoomed === "1" && c.zoomOutside.length === 0 && c.panOutside.length === 0 && c.floorLabelZoomed === 1 && c.reset === "0";
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // the switch itself, frame by frame: Full (crossfade, numbers glide) and Calm (instant)
  for (const [mode, rm] of [["full", "no-preference"], ["calm", "reduce"]]) {
    const name = `switch_${mode}`;
    const { ctx, page } = await open(name, 1440, 900, "/performance", { reducedMotion: rm });
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2200);
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1600); await page.keyboard.press("Shift");
    await page.getByRole("button", { name: /^Phase:/ }).click(); await page.waitForTimeout(250);
    await startSampler(page);
    await page.locator("[data-menu-list]").getByRole("option", { name: /^Prop/ }).click();
    await page.waitForTimeout(2700);
    const s = await page.evaluate(() => window.__s);
    const nets = [...new Set(s.map((x) => x.net).filter(Boolean))];
    const both = s.filter((x) => x.views > 1).length;
    checks[name] = { frames: s.length, distinctNet: nets.length, netsSample: nets.slice(0, 6), crossfadeFrames: both };
    if (mode === "full" && (nets.length < 4 || both < 3)) problems.push(`${name}: no glide or no crossfade ${JSON.stringify(checks[name])}`);
    if (mode === "calm" && (nets.length > 2 || both > 1)) problems.push(`${name}: Calm isn't instant ${JSON.stringify(checks[name])}`);
    await ctx.close();
  }
  // videos: a phase switch, and zooming
  const record = async (file, act) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/performance");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "mffur1"); } catch {} });
    await page.reload(); await page.waitForTimeout(2200);
    await act(page);
    const v = page.video();
    await ctx.close();
    const dst = path.join(VID, file);
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  };
  await record("phase_switch_1440.webm", async (page) => {
    await tab(page, "Monte Carlo"); await page.waitForTimeout(2000);
    for (const ph of ["Prop", "Development", "Prop"]) {
      await top(page); await page.waitForTimeout(300);
      await menuPick(page, "Phase", ph); await page.waitForTimeout(600);
      await page.locator("[data-page=performance] [data-risk-tab]").scrollIntoViewIfNeeded(); await page.waitForTimeout(1600);
    }
    await top(page); await tab(page, "Equity"); await page.waitForTimeout(800);
    await menuPick(page, "Phase", "Development"); await page.waitForTimeout(1500);
  });
  await record("zoom_1440.webm", async (page) => {
    await tab(page, "Drawdown"); await page.waitForTimeout(1200);
    await page.locator("[data-lifecycle-chart]").scrollIntoViewIfNeeded(); await page.waitForTimeout(600);
    const b = await page.locator("[data-lifecycle-chart]").boundingBox();
    await page.mouse.move(b.x + b.width * 0.3, b.y + b.height * 0.5);
    for (let i = 0; i < 10; i++) { await page.mouse.wheel(0, -200); await page.waitForTimeout(120); }
    await page.waitForTimeout(600);
    await page.mouse.down(); await page.mouse.move(b.x + b.width * 0.05, b.y + b.height * 0.5, { steps: 30 }); await page.mouse.up(); await page.waitForTimeout(500);
    const m = page.locator("[data-lifecycle-chart] [data-marker]").first();
    if (await m.count()) { await m.hover(); await page.waitForTimeout(1200); }
    await page.mouse.move(b.x + b.width * 0.6, b.y + b.height * 0.5);
    for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 240); await page.waitForTimeout(120); }
    await page.waitForTimeout(500);
    await page.mouse.dblclick(b.x + b.width * 0.5, b.y + b.height * 0.5); await page.waitForTimeout(1200);
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 }));
    await tab(page, "Monte Carlo"); await page.waitForTimeout(2200);
    await page.locator("[data-lifecycle-chart]").scrollIntoViewIfNeeded(); await page.waitForTimeout(400);
    const c = await page.locator("[data-lifecycle-chart]").boundingBox();
    await page.mouse.move(c.x + c.width * 0.5, c.y + c.height * 0.5);
    for (let i = 0; i < 10; i++) { await page.mouse.wheel(0, -200); await page.waitForTimeout(120); }
    await page.waitForTimeout(1500);
  });
};

// the funded account's own-stage views, on the scratch panel (CF_BASE=http://127.0.0.1:8734; a funded Lucid demo
// account beside the two draft evaluations) and a single-day KPI row
S["3g"] = async () => {
  const { tab, toPanel, top, menuPick, inside } = helpers3f;
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `funded_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "lucid1"));
    await page.reload(); await page.waitForTimeout(2200);
    await menuPick(page, "Phase", "Prop");
    const c = (checks[name] = {});
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1800); await page.keyboard.press("Shift"); await toPanel(page);
    c.numbers = (await page.locator("[data-account-numbers]").innerText()).replace(/\s+/g, " ");
    c.outside = await inside(page);
    await page.screenshot({ path: path.join(OUT, `account_mc_funded_${w}.png`) });
    await tab(page, "Drawdown"); await page.waitForTimeout(1300); await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `account_drawdown_funded_${w}.png`) });
    // one trading day on mffur1: a single dot, no badge
    await top(page); await menuPick(page, "Account", "mffur1"); await page.waitForTimeout(800);
    c.singleDots = await page.locator("[data-kpis] [data-single-dot]").count();
    c.badges = await page.locator("[data-kpis] [data-direction]").count();
    await page.screenshot({ path: path.join(OUT, `kpis_one_day_${w}.png`) });
    if (!/Next payout before the floor/i.test(c.numbers) || c.outside.length || c.singleDots < 1 || c.badges !== 0) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// Stage 3 changes (owner 2026-11-16): the equity curve from $0 (one point per trade in real-trade phases), the KPI
// dashed line for < 2 points and more room under the number, chips sized to their text, the stats grid's hover, the
// Accounts editor's last payout date. `--synthetic` on 127.0.0.1:8733.
S["3h"] = async () => {
  const { tab, toPanel, top, menuPick } = helpers3f;
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `stage3_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    // the chips: each sized to its text (even padding: width - text width = 2 x 8 px padding + 2 px border)
    const chips = async () => page.evaluate(() => [...document.querySelectorAll("[data-scope-chips] > span")].map((s) => {
      const r = document.createRange(); r.selectNodeContents(s);
      return { text: s.textContent, extra: Math.round(s.getBoundingClientRect().width - r.getBoundingClientRect().width) };
    }));
    c.chipsDev = await chips();
    // the KPI number and its badge: the gap between them
    c.badgeGap = await page.evaluate(() => {
      const t = document.querySelector("[data-kpi='Net P&L'] [data-slot=kpi-tile]");
      const v = t.querySelector("p + div > span"), b = t.querySelector("[data-direction]");
      if (!v || !b) return null;
      const vr = v.getBoundingClientRect(), br = b.getBoundingClientRect();
      // below the number (narrow tiles) or beside it (wide tiles)
      return br.top >= vr.bottom - 1 ? Math.round(br.top - vr.bottom) : Math.round(br.left - vr.right);
    });
    await page.screenshot({ path: path.join(OUT, `dev_top_${w}.png`) });
    // the stats grid: a cell hovered lights up like the KPI tiles
    const cell = page.locator("[data-stats-panel] [data-stat-cell]").nth(2);
    await cell.scrollIntoViewIfNeeded(); await page.waitForTimeout(250);
    const cb = await cell.boundingBox();
    await page.mouse.move(cb.x + cb.width * 0.3, cb.y + cb.height * 0.5); await page.waitForTimeout(450);
    c.cellLit = await cell.evaluate((e) => getComputedStyle(e.querySelector("span[aria-hidden]")).opacity);
    await page.screenshot({ path: path.join(OUT, `stats_hover_${w}.png`), clip: { x: 0, y: Math.max(0, cb.y - 220), width: w, height: 360 } });
    await page.mouse.move(2, 2);
    // the equity curve from $0 (Development: one point per day)
    await tab(page, "Equity"); await page.waitForTimeout(1600); await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `equity_dev_${w}.png`) });
    // Prop: one trade so far: the curve from $0 to it, the KPI tiles' dashed line, profit factor ∞ (no note)
    await top(page); await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(900);
    c.chipsProp = await chips();
    c.pf = (await page.locator("[data-kpi='Profit factor']").innerText()).replace(/\s+/g, " ");
    c.dashed = await page.locator("[data-kpis] path[stroke-dasharray], [data-kpis] path.stroke-current").count();
    await page.screenshot({ path: path.join(OUT, `prop_top_${w}.png`) });
    await toPanel(page); await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, `equity_prop_${w}.png`) });
    // the R view too
    await top(page); await page.getByRole("tab", { name: "R", exact: true }).first().click(); await page.waitForTimeout(900); await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `equity_prop_r_${w}.png`) });
    const evenChips = (xs) => xs.length === 4 && xs.every((x) => x.extra >= 16 && x.extra <= 20);
    const ok = evenChips(c.chipsDev) && evenChips(c.chipsProp) && (c.badgeGap === null || c.badgeGap >= 8) && Number(c.cellLit) > 0.5 &&
      /—/.test(c.pf) && !/∞|no losing trades yet/.test(c.pf);   // owner 2026-11-16: "—" again
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// More stage 3 changes (owner 2026-11-16): profit factor "—" again, the calendar's month picker, the account's Drawdown
// per trade, the redesigned Monte Carlo summaries (variant A gauge, B bars). `--synthetic` on 127.0.0.1:8733.
S["3i"] = async () => {
  const { tab, toPanel, top, menuPick } = helpers3f;
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    for (const variant of ["a", "b"]) {
      const name = `summaries_${variant}_${w}`;
      const { ctx, page } = await open(name, w, h, `/performance${variant === "b" ? "?variant=b" : ""}`);
      await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
      await page.reload(); await page.waitForTimeout(2400);
      const c = (checks[name] = {});
      // frame the whole summary: the chart header at the top of the view
      const frame = (p) => p.locator("[data-risk-tab]").evaluate((e) => { e.scrollIntoView({ block: "start" }); document.querySelector("#cf-main").scrollBy({ top: -12 }); });
      await tab(page, "Monte Carlo"); await page.waitForTimeout(2600); await page.keyboard.press("Shift");
      await frame(page); await page.waitForTimeout(400);
      c.plan = await page.locator("[data-plan-numbers]").getAttribute("data-variant");
      await page.screenshot({ path: path.join(OUT, `mc_plan_${variant}_${w}.png`) });
      await top(page); await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(2600);
      await frame(page); await page.waitForTimeout(400);
      c.account = await page.locator("[data-account-numbers]").getAttribute("data-variant");
      c.position = await page.locator("[data-account-numbers] [data-position]").count();
      c.rangeBars = await page.locator("[data-account-numbers] [data-range-bar]").count();
      await page.screenshot({ path: path.join(OUT, `mc_account_${variant}_${w}.png`) });
      if (c.plan !== variant || c.account !== variant || c.position !== 1 || c.rangeBars < 2) problems.push(`${name}: ${JSON.stringify(c)}`);
      await ctx.close();
    }
    const name = `stage3i_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    // profit factor: a number in Development; "—" with no losing trade (Prop, mffur1), no note, no tooltip
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(900);
    c.pf = (await page.locator("[data-kpi='Profit factor']").innerText()).replace(/\s+/g, " ");
    c.pfHint = await page.locator("[data-kpi='Profit factor']").getAttribute("data-hint");
    // the account's Drawdown: one point per trade from the start
    await tab(page, "Drawdown"); await page.waitForTimeout(1500); await toPanel(page);
    c.ddKicker = await page.locator("[data-chart-header]").innerText();
    await page.screenshot({ path: path.join(OUT, `drawdown_prop_per_trade_${w}.png`) });
    // the calendar's month picker (Development: many months)
    await top(page); await menuPick(page, "Phase", "Development"); await page.waitForTimeout(600);
    await tab(page, "Calendar"); await page.waitForTimeout(1400); await toPanel(page);
    c.nativeSelect = await page.locator("[data-cal] select").count();
    const trigger = page.getByRole("button", { name: /^Month:/ });
    c.before = await trigger.getAttribute("aria-label");
    await trigger.click(); await page.waitForTimeout(300);
    c.months = await page.locator("[data-month-list] [role=option]").allInnerTexts();
    c.listHeight = await page.locator("[data-month-list]").evaluate((e) => e.clientHeight);
    await page.locator("[data-month-list] [role=option]").nth(2).hover(); await page.waitForTimeout(150);
    await page.screenshot({ path: path.join(OUT, `month_picker_open_${w}.png`) });
    // keys: ↓ then Enter picks the next older month
    await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter"); await page.waitForTimeout(900);
    c.afterKeys = await trigger.getAttribute("aria-label");
    await page.getByRole("button", { name: "Next month" }).click(); await page.waitForTimeout(900);
    c.afterNext = await trigger.getAttribute("aria-label");
    await page.screenshot({ path: path.join(OUT, `calendar_${w}.png`) });
    const newestFirst = c.months.length > 1 && new Date(c.months[0]) > new Date(c.months[1]);
    const ok = /—/.test(c.pf) && !/∞|no losing/.test(c.pf) && !c.pfHint && /one point per trade/i.test(c.ddKicker) &&
      c.nativeSelect === 0 && newestFirst && c.listHeight <= 6 * 34 + 10 && c.afterKeys === `Month: ${c.months[3]}` && c.afterNext === `Month: ${c.months[2]}` &&
      !c.months.some((m) => /2026/.test(m) && /(October|November)/.test(m));
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// Stage 3 polish (owner 2026-11-16): the tighter summary and calmer ring, the hover rule on every list, the Drawdown
// stats row's hover, the Settings section list, one footer gap on every scrolling page. `--synthetic`, 127.0.0.1:8733.
S["3j"] = async () => {
  const { tab, top, menuPick } = helpers3f;
  const frame = (p) => p.locator("[data-risk-tab]").evaluate((e) => { e.scrollIntoView({ block: "start" }); document.querySelector("#cf-main").scrollBy({ top: -12 }); });
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `polish_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    // the summaries (plan, then account) with the ring: its number sits inside the ring with clear space around it
    await tab(page, "Monte Carlo"); await page.waitForTimeout(2600); await page.keyboard.press("Shift");
    await frame(page); await page.waitForTimeout(500);
    const ringFit = () => page.evaluate(() => {
      const ring = document.querySelector("[data-ring]"), v = ring?.querySelector("[data-ring-value]");
      if (!ring || !v) return null;
      const rr = ring.getBoundingClientRect(), vr = v.getBoundingClientRect();
      const inner = rr.width - 2 * 7;                        // the stroke
      return { inner: Math.round(inner), value: Math.round(vr.width), clear: Math.round((inner - vr.width) / 2) };
    });
    c.ringPlan = await ringFit();
    c.kicker = await page.locator("[data-chart-header] .eyebrow").first().innerText();
    c.labelSpacing = await page.locator("[data-plan-numbers] .eyebrow").first().evaluate((e) => getComputedStyle(e).letterSpacing);
    c.legendGap = await page.locator("[data-plan-numbers] [data-legend-row]").first().evaluate((r) => {
      const [, lab, val] = r.children; return Math.round(val.getBoundingClientRect().left - lab.getBoundingClientRect().right);
    });
    await page.screenshot({ path: path.join(OUT, `mc_plan_${w}.png`) });
    await page.locator("[data-ring]").screenshot({ path: path.join(OUT, `ring_plan_${w}.png`) });
    // the Drawdown stats row lights up on hover
    await tab(page, "Drawdown"); await page.waitForTimeout(1400); await frame(page); await page.waitForTimeout(300);
    const cell = page.locator("[data-replay-summary] [data-stat-cell]").nth(1);
    const cb = await cell.boundingBox();
    await page.mouse.move(cb.x + cb.width * 0.4, cb.y + cb.height * 0.5); await page.waitForTimeout(450);
    c.ddCellLit = await cell.evaluate((e) => getComputedStyle(e.querySelector("span[aria-hidden]")).opacity);
    await page.screenshot({ path: path.join(OUT, `drawdown_stats_hover_${w}.png`) });
    await page.mouse.move(2, 2);
    // the account's summary
    await top(page); await menuPick(page, "Phase", "Prop"); await tab(page, "Monte Carlo"); await page.waitForTimeout(2600);
    await frame(page); await page.waitForTimeout(500);
    c.ringAccount = await ringFit();
    c.days = await page.locator("[data-account-numbers] [data-metric='Days to pass']").innerText();
    await page.screenshot({ path: path.join(OUT, `mc_account_${w}.png`) });
    await page.locator("[data-ring]").screenshot({ path: path.join(OUT, `ring_account_${w}.png`) });
    // the hover rule on the month picker: the pointer leaves the list -> no row stays highlighted; the keys still do
    await top(page); await menuPick(page, "Phase", "Development"); await tab(page, "Calendar"); await page.waitForTimeout(1400);
    await page.getByRole("button", { name: /^Month:/ }).click(); await page.waitForTimeout(300);
    const lit = () => page.locator("[data-month-list] [role=option].bg-white\\/\\[\\.06\\]").count();
    c.monthOnOpen = await lit();
    await page.locator("[data-month-list] [role=option]").nth(2).hover(); await page.waitForTimeout(150);
    c.monthOnHover = await lit();
    const lb = await page.locator("[data-month-list]").boundingBox();
    await page.mouse.move(lb.x - 60, lb.y + lb.height + 60); await page.waitForTimeout(200);
    c.monthAfterLeave = await lit();
    await page.screenshot({ path: path.join(OUT, `month_picker_after_leave_${w}.png`) });
    await page.keyboard.press("ArrowDown"); await page.waitForTimeout(150);
    c.monthAfterKey = await lit();
    await page.keyboard.press("Escape");
    // the same on the account switcher (top bar) and the command palette
    await page.locator("[data-account-switcher]").click(); await page.waitForTimeout(350);
    await page.locator("[data-switcher-row]").first().hover(); await page.waitForTimeout(150);
    const swb = await page.locator("[data-switcher-panel]").boundingBox();
    await page.mouse.move(swb.x + swb.width + 80, swb.y + swb.height + 80); await page.waitForTimeout(200);
    c.switcherAfterLeave = await page.locator("[data-switcher-row][data-lit]").count();
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    const ok = c.ringPlan && c.ringPlan.clear >= 14 && c.ringAccount && c.ringAccount.clear >= 14 && !/synthetic|backtest,/i.test(c.kicker) &&
      parseFloat(c.labelSpacing) <= 0.06 * 10.5 + 0.01 && c.legendGap <= 12 && /days/.test(c.days) && Number(c.ddCellLit) > 0.5 &&
      c.monthOnOpen === 0 && c.monthOnHover === 1 && c.monthAfterLeave === 0 && c.monthAfterKey === 1 && c.switcherAfterLeave === 0;
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // Settings: the section list (click -> active at once and while scrolling; at the bottom the last section on screen)
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `settings_toc_${w}`;
    const { ctx, page } = await open(name, w, h, "/settings");
    await page.reload(); await page.waitForTimeout(2000);
    const c = (checks[name] = {});
    const current = () => page.locator("[data-toc][aria-current=true]").getAttribute("data-toc");
    const toc = (await page.locator("[data-toc]").count()) && (await page.locator("[data-toc]").first().isVisible());
    if (toc) {
      const ids = await page.locator("[data-toc]").evaluateAll((els) => els.map((e) => e.getAttribute("data-toc")));
      const last = ids[ids.length - 1];
      await page.locator(`[data-toc='${last}']`).click();
      c.atClick = await current(); await page.waitForTimeout(250);
      c.whileScrolling = await current(); await page.waitForTimeout(1500);
      c.afterScroll = await current();
      await page.screenshot({ path: path.join(OUT, `settings_toc_danger_${w}.png`) });
      await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 })); await page.waitForTimeout(600);
      c.atTop = await current();
      await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: m.scrollHeight })); await page.waitForTimeout(600);
      c.atBottom = await current();
      c.last = last;
      if (!(c.atClick === last && c.whileScrolling === last && c.afterScroll === last && c.atTop === ids[0] && c.atBottom === last)) problems.push(`${name}: ${JSON.stringify(c)}`);
    } else c.toc = "not shown at this width";
    await ctx.close();
  }
  // one gap from the content to the footer's label on every scrolling page
  {
    const name = "footer_gaps";
    const { ctx, page } = await open(name, 1440, 900, "/performance");
    const gaps = {};
    for (const url of ["/performance", "/accounts", "/settings", "/accounts/mffur1"]) {
      await page.goto(BASE + url); await page.waitForTimeout(2600);
      await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: m.scrollHeight })); await page.waitForTimeout(900);
      gaps[url] = await page.evaluate(() => {
        const content = document.querySelector("[data-footer-page]").firstElementChild;
        let bottom = 0;
        for (const e of content.querySelectorAll("*")) {
          const cs = getComputedStyle(e), r = e.getBoundingClientRect();
          if (!r.height || !r.width || cs.visibility === "hidden" || cs.position === "fixed" || cs.position === "absolute") continue;
          const drawn = parseFloat(cs.borderBottomWidth) > 0 || cs.backgroundColor !== "rgba(0, 0, 0, 0)" || (e.childElementCount === 0 && (e.textContent || "").trim());
          if (drawn) bottom = Math.max(bottom, r.bottom);
        }
        const foot = document.querySelector("[data-page-footer]");
        const label = [...foot.querySelectorAll("*")].find((e) => e.childElementCount === 0 && /CreamFlow · NQ/i.test(e.textContent || ""));
        return Math.round(label.getBoundingClientRect().top - bottom);
      });
      if (url === "/performance") await page.screenshot({ path: path.join(OUT, "footer_gap_performance_1440.png") });
    }
    checks[name] = gaps;
    const v = Object.values(gaps);
    if (Math.max(...v) - Math.min(...v) > 1 || Math.abs(v[0] - 80) > 2) problems.push(`${name}: ${JSON.stringify(gaps)}`);
    await ctx.close();
  }
};

// Monte Carlo summary refinements (owner 2026-11-16): the pass card with its ring on the right, "Where it stands"
// centred, one structure for every stat card (values, visuals and footnotes level; each value centred on its visual),
// the pips and the risk meter, one inner padding for every card. `--synthetic` on 127.0.0.1:8733.
S["3k"] = async () => {
  const { tab, top, menuPick } = helpers3f;
  const frame = (p) => p.locator("[data-risk-tab]").evaluate((e) => { e.scrollIntoView({ block: "start" }); document.querySelector("#cf-main").scrollBy({ top: -12 }); });
  const measure = (p, root) => p.evaluate((root) => {
    const R = (e) => e.getBoundingClientRect();
    const box = document.querySelector(root);
    const head = box.querySelector("[data-head-card]"), main = box.querySelector("[data-main-card]");
    const label = head.querySelector(".eyebrow"), ring = head.querySelector("[data-ring]");
    const mainLabel = main.querySelector(".eyebrow"), body = main.querySelector("[data-main-body]");
    const cs = getComputedStyle(main);
    const below = R(main).bottom - parseFloat(cs.paddingBottom) - R(body.lastElementChild).bottom, above = R(body.firstElementChild).top - R(mainLabel).bottom;
    const cards = [...box.querySelectorAll("[data-metric]")].filter((c) => c.querySelector("[data-stat-row]"));
    const mid = (e) => (R(e).top + R(e).bottom) / 2;
    const visualMid = (c) => { const v = c.querySelector("[data-stat-visual]"); const inner = v.querySelector("[data-range-bar], [data-pips], [data-meter]") || v.firstElementChild; return mid(inner); };
    const rows = cards.map((c) => ({ value: Math.round(R(c.querySelector("[data-stat-value]")).top), visual: Math.round(visualMid(c)),
      foot: Math.round(R(c.querySelector("[data-stat-foot]")).top), offset: Math.round(Math.abs(mid(c.querySelector("[data-stat-value]")) - visualMid(c)) * 10) / 10 }));
    const pads = [...box.querySelectorAll("[data-summary-card]")].map((c) => { const s = getComputedStyle(c); return [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft].join(" "); });
    return { ringRight: Math.round(R(ring).left - R(label).right), heights: [Math.round(R(head).height), Math.round(R(main).height)],
      above: Math.round(above), below: Math.round(below), rows, pads: [...new Set(pads)], pips: box.querySelectorAll("[data-pips]").length, meter: box.querySelectorAll("[data-meter]").length };
  }, root);
  // within each row of cards (four in a row at 1440, two rows of two at 960)
  const level = (rows) => {
    const groups = {};
    for (const r of rows) (groups[Math.round(r.value / 40)] ??= []).push(r);
    return Object.values(groups).every((g) => ["value", "visual", "foot"].every((k) => Math.max(...g.map((r) => r[k])) - Math.min(...g.map((r) => r[k])) <= 1)) && rows.every((r) => r.offset <= 1.5);
  };
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `summary_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    await tab(page, "Monte Carlo"); await page.waitForTimeout(2600); await page.keyboard.press("Shift"); await frame(page); await page.waitForTimeout(600);
    c.plan = await measure(page, "[data-plan-numbers]");
    await page.screenshot({ path: path.join(OUT, `mc_plan_${w}.png`) });
    await page.locator("[data-plan-numbers]").screenshot({ path: path.join(OUT, `summary_plan_${w}.png`) });
    await top(page); await menuPick(page, "Phase", "Prop"); await tab(page, "Monte Carlo"); await page.waitForTimeout(2600); await frame(page); await page.waitForTimeout(600);
    c.account = await measure(page, "[data-account-numbers]");
    await page.screenshot({ path: path.join(OUT, `mc_account_${w}.png`) });
    await page.locator("[data-account-numbers]").screenshot({ path: path.join(OUT, `summary_account_${w}.png`) });
    const good = (m) => m.ringRight > 0 && m.heights[0] === m.heights[1] && Math.abs(m.above - m.below) <= 4 && level(m.rows) && m.pads.length === 1 && m.pads[0] === "16px 16px 16px 16px";
    const ok = good(c.plan) && good(c.account) && c.account.pips === 1 && c.account.meter === 1;
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// The real account's Drawdown summary row (owner 2026-11-16): every cell a value and a sub-line, lined up; the room
// out of the drawdown with its bar; "Floor locked" from the account's own plan rules. Your panel (8733: mffur1, an
// evaluation, no lock) and the scratch panel (CF_SCRATCH, 8734: funded accounts with a lock).
S["3l"] = async () => {
  const { tab, top, menuPick } = helpers3f;
  const SCRATCH = process.env.CF_SCRATCH ?? "http://127.0.0.1:8734";
  const cases = [
    { base: BASE, account: "mffur1", expect: /No lock/, slug: "evaluation_no_lock" },
    { base: SCRATCH, account: "lucid1", expect: /to go/, slug: "funded_lucid_to_go" },
    { base: SCRATCH, account: "mffurf1", expect: /Locked/, slug: "funded_rapid_locked" },
    { base: SCRATCH, account: "mffupf1", expect: /At 1st payout/, slug: "funded_pro_first_payout" },
  ];
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    for (const cs of cases) {
      const name = `dd_row_${cs.slug}_${w}`;
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
      const page = await ctx.newPage();
      page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
      await page.goto(cs.base + "/performance");
      await page.evaluate((a) => { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", a); }, cs.account);
      await page.reload(); await page.waitForTimeout(2400);
      await menuPick(page, "Phase", "Prop"); await tab(page, "Drawdown"); await page.waitForTimeout(1600);
      await page.locator("[data-risk-tab]").evaluate((e) => { e.scrollIntoView({ block: "start" }); document.querySelector("#cf-main").scrollBy({ top: -12 }); });
      await page.waitForTimeout(500);
      const c = (checks[name] = await page.evaluate(() => {
        const R = (e) => e.getBoundingClientRect();
        const cells = [...document.querySelectorAll("[data-account-numbers] [data-stat-cell]")];
        const rows = cells.map((x) => { const [, v, sub] = x.querySelectorAll(":scope > div"); return { key: x.dataset.metric, value: Math.round(R(v).top), sub: Math.round(R(sub).top),
          subText: sub.textContent.trim(), valueText: v.textContent.trim(), bar: !!x.querySelector("[data-cell-bar]") }; });
        return { rows };
      }));
      const groups = {};
      for (const r of c.rows) (groups[Math.round(r.value / 40)] ??= []).push(r);
      const level = Object.values(groups).every((g) => ["value", "sub"].every((k) => Math.max(...g.map((r) => r[k])) - Math.min(...g.map((r) => r[k])) <= 1));
      const room = c.rows.find((r) => r.key === "room"), lock = c.rows.find((r) => r.key === "locked");
      const ok = c.rows.length === 5 && c.rows.every((r) => r.subText.length > 1) && level && /% of the \$1,000 drawdown|% of the \$2,000 drawdown/.test(room.subText) && room.bar &&
        cs.expect.test(lock.valueText);
      if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
      await page.screenshot({ path: path.join(OUT, `drawdown_${cs.slug}_${w}.png`) });
      await page.locator("[data-account-numbers]").screenshot({ path: path.join(OUT, `row_${cs.slug}_${w}.png`) });
      await ctx.close();
    }
  }
};

// The tab area's height follows its content (owner 2026-11-16): switching Equity -> Trades -> Equity on mffur1 (Prop),
// the footer glides into place with a slight fade instead of snapping; Calm: at once. `--synthetic` on 8733.
S["3m"] = async () => {
  const { tab, menuPick } = helpers3f;
  const VID = path.resolve(here, "../../../docs/design/video/stage3m");
  fs.mkdirSync(VID, { recursive: true });
  // per-frame samples while switching: the footer's top and opacity, the tab area's height, the chart's own height
  const sampleSwitch = async (page, to) => {
    await page.evaluate(() => {
      window.__f = [];
      const t0 = performance.now();
      const f = () => {
        const foot = document.querySelector("[data-page-footer]"), area = document.querySelector("[data-auto-height]");
        const chart = document.querySelector("[data-equity-chart]");
        window.__f.push({ t: Math.round(performance.now() - t0), top: Math.round(foot.getBoundingClientRect().top), op: Number(getComputedStyle(foot).opacity).toFixed(2),
          area: Math.round(area.getBoundingClientRect().height), chart: chart ? Math.round(chart.getBoundingClientRect().height) : null });
        if (performance.now() - t0 < 900) requestAnimationFrame(f);
      };
      requestAnimationFrame(f);
    });
    await tab(page, to); await page.waitForTimeout(1100);
    const s = await page.evaluate(() => window.__f);
    const tops = s.map((x) => x.top);
    const moves = s.filter((x, i) => i && x.top !== s[i - 1].top);
    return { frames: s.length, from: tops[0], to: tops[tops.length - 1], movingFrames: moves.length,
      span: moves.length ? moves[moves.length - 1].t - moves[0].t : 0, minOpacity: Math.min(...s.map((x) => Number(x.op))),
      chartHeights: [...new Set(s.map((x) => x.chart).filter((x) => x !== null))] };
  };
  for (const [mode, rm] of [["full", "no-preference"], ["calm", "reduce"]]) {
    const name = `tab_height_${mode}`;
    const { ctx, page } = await open(name, 1440, 900, "/performance", { reducedMotion: rm });
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800);
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: m.scrollHeight })); await page.waitForTimeout(500);
    const c = (checks[name] = {});
    c.toTrades = await sampleSwitch(page, "Trades");
    await page.screenshot({ path: path.join(OUT, `trades_${mode}_1440.png`) });
    c.toEquity = await sampleSwitch(page, "Equity");
    const glides = (x) => x.movingFrames >= 6 && x.span >= 150 && x.span <= 420;
    if (mode === "full" && !(glides(c.toTrades) && glides(c.toEquity) && c.toTrades.minOpacity < 0.8 && c.toEquity.chartHeights.length <= 1))
      problems.push(`${name}: ${JSON.stringify(c)}`);
    if (mode === "calm" && (c.toTrades.movingFrames > 2 || c.toEquity.movingFrames > 2)) problems.push(`${name}: Calm isn't instant ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // the video: Equity -> Trades -> Equity on mffur1, scrolled to the footer
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
    const page = await ctx.newPage();
    await page.goto(BASE + "/performance");
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "mffur1"); } catch {} });
    await page.reload(); await page.waitForTimeout(2200);
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800);
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: m.scrollHeight, behavior: "smooth" })); await page.waitForTimeout(1200);
    for (const t of ["Trades", "Equity", "Trades", "Equity"]) { await tab(page, t); await page.waitForTimeout(1500); }
    const v = page.video();
    await ctx.close();
    const dst = path.join(VID, "tab_height_equity_trades_1440.webm");
    fs.renameSync(await v.path(), dst);
    (checks.videos ??= []).push(dst);
  }
};

// "All accounts" in real-trades phases (owner 2026-11-16): the combined view with the per-account breakdown, the top-bar
// option, Drawdown / Monte Carlo per account with the note; the Accounts page's archived accounts (scratch panel 8734).
S["3n"] = async () => {
  const { tab, toPanel, top, menuPick } = helpers3f;
  const SCRATCH = process.env.CF_SCRATCH ?? "http://127.0.0.1:8734";
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `all_accounts_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    await menuPick(page, "Phase", "Prop");
    await menuPick(page, "Account", "All accounts"); await page.waitForTimeout(800);
    c.topbar = await page.locator("[data-account-switcher]").getAttribute("aria-label");
    c.rows = await page.locator("[data-breakdown-row]").count();
    c.totals = (await page.locator("[data-breakdown-totals]").innerText()).replace(/\s+/g, " ");
    await page.locator("[data-breakdown]").scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
    await page.locator("#cf-main").evaluate((m) => m.scrollTo({ top: 0 })); await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, `all_accounts_top_${w}.png`) });
    await page.locator("[data-breakdown]").screenshot({ path: path.join(OUT, `breakdown_${w}.png`) });
    // sort by net cash: a number column sorts largest first on its first click
    await page.locator("[data-breakdown] th button", { hasText: "Net cash" }).click(); await page.waitForTimeout(200);
    c.firstByCash = await page.locator("[data-breakdown-row]").first().getAttribute("data-breakdown-row");
    await page.locator("[data-breakdown]").screenshot({ path: path.join(OUT, `breakdown_sorted_net_cash_${w}.png`) });
    // the combined equity curve, trades with the account column, calendar; Drawdown with the note
    await tab(page, "Equity"); await page.waitForTimeout(1400); await toPanel(page);
    await page.screenshot({ path: path.join(OUT, `all_equity_${w}.png`) });
    await tab(page, "Trades"); await page.waitForTimeout(1400); await toPanel(page);
    c.tradeAccounts = await page.evaluate(() => new Set([...document.querySelectorAll("tbody tr")].map((r) => r.children[2]?.textContent)).size);
    await page.screenshot({ path: path.join(OUT, `all_trades_${w}.png`) });
    await tab(page, "Drawdown"); await page.waitForTimeout(1500); await toPanel(page);
    c.note = await page.locator("[data-account-note]").innerText().catch(() => null);
    await page.screenshot({ path: path.join(OUT, `all_drawdown_note_${w}.png`) });
    // a row click focuses that account (the page filter and the top bar)
    await top(page); await page.locator("[data-breakdown-row='lucidf91']").click(); await page.waitForTimeout(1200);
    c.afterRow = await page.getByRole("button", { name: /^Account:/ }).getAttribute("aria-label");
    // the top-bar list: "All accounts" first, archived accounts not offered
    await page.locator("[data-account-switcher]").click(); await page.waitForTimeout(350);
    c.switcherRows = await page.locator("[data-switcher-row]").evaluateAll((els) => els.map((e) => e.getAttribute("data-switcher-row")));
    await page.screenshot({ path: path.join(OUT, `topbar_all_accounts_${w}.png`) });
    await page.keyboard.press("Escape");
    const ok = /All accounts/.test(c.topbar ?? "") && c.rows >= 7 && /totals/i.test(c.totals) && c.firstByCash === "mffurf92" &&
      c.tradeAccounts >= 5 && /showing/.test(c.note ?? "") && /lucidf91/.test(c.afterRow ?? "") && c.switcherRows[0] === "all";
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // the Accounts page on the scratch panel: archived accounts hidden, then shown; an archived account's editor
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `archived_${w}`;
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
    await page.goto(SCRATCH + "/accounts");
    await page.evaluate(() => sessionStorage.setItem("creamflow.splashShown", "1"));
    await page.reload(); await page.waitForTimeout(2200);
    const c = (checks[name] = {});
    c.hidden = await page.locator("[data-account-card]").count();
    await page.screenshot({ path: path.join(OUT, `accounts_default_${w}.png`) });
    await page.locator("[data-show-archived] [role=switch]").click(); await page.waitForTimeout(600);
    c.shown = await page.locator("[data-account-card]").count();
    await page.screenshot({ path: path.join(OUT, `accounts_show_archived_${w}.png`), fullPage: false });
    await page.locator("[data-account-card='mffur91']").click(); await page.waitForTimeout(1500);
    c.archiveNote = await page.locator("[data-archive-instead]").count();
    c.deleteBox = await page.getByText("Delete this account").count();
    await page.locator("[data-archive-instead]").scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, `account_with_trades_editor_${w}.png`) });
    if (!(c.shown > c.hidden && c.archiveNote === 1 && c.deleteBox === 0)) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// owner 2026-11-16 decisions: passed evaluations archived; saving an evaluation as passed asks for its funded account;
// under "All accounts" Drawdown / Monte Carlo show the last single account selected, else the most recently active one.
S["3o"] = async () => {
  const { tab, toPanel, top, menuPick } = helpers3f;
  const SCRATCH = process.env.CF_SCRATCH ?? "http://127.0.0.1:8734";
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `last_account_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => { localStorage.removeItem("creamflow.lastAccount"); localStorage.setItem("creamflow.focusAccount", "all"); });
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(600);
    await tab(page, "Drawdown"); await page.waitForTimeout(1400);
    c.fallback = await page.locator("[data-account-note]").innerText().catch(() => null);
    await toPanel(page); await page.screenshot({ path: path.join(OUT, `all_fallback_most_recent_${w}.png`) });
    await top(page); await menuPick(page, "Account", "mffub1"); await page.waitForTimeout(900);
    await menuPick(page, "Account", "All accounts"); await page.waitForTimeout(900);
    await tab(page, "Monte Carlo"); await page.waitForTimeout(1500);
    c.last = await page.locator("[data-account-note]").innerText().catch(() => null);
    await toPanel(page); await page.screenshot({ path: path.join(OUT, `all_last_selected_mc_${w}.png`) });
    // a breakdown row click counts as a selection too
    await top(page); await page.locator("[data-breakdown-row='lucidf91']").click(); await page.waitForTimeout(900);
    await menuPick(page, "Account", "All accounts"); await page.waitForTimeout(900);
    await tab(page, "Drawdown"); await page.waitForTimeout(1400);
    c.afterRow = await page.locator("[data-account-note]").innerText().catch(() => null);
    // it survives a reload
    await page.reload(); await page.waitForTimeout(2400);
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(600);
    await tab(page, "Drawdown"); await page.waitForTimeout(1400);
    c.afterReload = await page.locator("[data-account-note]").innerText().catch(() => null);
    const ok = /showing mffub1 \(the most recently active account\)/.test(c.fallback ?? "") && /showing mffub1 \(the last account you selected\)/.test(c.last ?? "") &&
      /showing lucidf91 \(the last account you selected\)/.test(c.afterRow ?? "") && /showing lucidf91/.test(c.afterReload ?? "");
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `passed_${w}`;
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
    await page.goto(SCRATCH + "/accounts");
    await page.evaluate(() => sessionStorage.setItem("creamflow.splashShown", "1"));
    await page.reload(); await page.waitForTimeout(2200);
    const c = (checks[name] = {});
    c.cards = await page.locator("[data-account-card]").evaluateAll((els) => els.map((e) => e.getAttribute("data-account-card")));
    await page.screenshot({ path: path.join(OUT, `accounts_passed_archived_${w}.png`) });
    if (w === 1440) {
      // mark an evaluation passed and save: the funded-account question, then the new account from the funded preset
      await page.locator("[data-account-card='mffur1']").click(); await page.waitForTimeout(1500);
      await page.getByRole("radiogroup", { name: "Status" }).getByRole("radio", { name: "Passed" }).click(); await page.waitForTimeout(500);
      await page.locator("[data-add-funded]").scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(OUT, `editor_marked_passed_${w}.png`) });
      await page.getByRole("button", { name: /^Save as checked$|^Save$/ }).first().click(); await page.waitForTimeout(1500);
      c.dialog = await page.getByRole("dialog").innerText().catch(() => null);
      await page.screenshot({ path: path.join(OUT, `ask_funded_dialog_${w}.png`) });
      await page.locator("[data-ask-funded-add]").click(); await page.waitForTimeout(1500);
      c.url = new URL(page.url()).pathname;
      c.preset = await page.getByText(/MFFU Rapid EOD 25k.*funded|funded/i).count();
      await page.screenshot({ path: path.join(OUT, `new_funded_account_${w}.png`) });
    }
    const ok = !c.cards.some((n) => ["mffur92", "lucid91"].includes(n)) && (w !== 1440 || (/add its funded account/i.test(c.dialog ?? "") && c.url === "/accounts/new" && c.preset > 0));
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// owner 2026-11-16: the top bar's "All accounts" row with its summary; both account lists capped at 10 accounts with
// "Archived" last and a search field past 10 (scratch panel 8734 has 12); the account switch crossfades the page body
// with the height glide (Calm: instant), and a video of the switch both ways.
S["3p"] = async () => {
  const { menuPick } = helpers3f;
  const SCRATCH = process.env.CF_SCRATCH ?? "http://127.0.0.1:8734";
  const scratchPage = async (name, w, h, url, opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", ...opts });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error") problems.push(`${name}: console ${m.text()}`); });
    await page.goto(SCRATCH + url);
    await page.evaluate(() => { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "mffur1"); });
    await page.reload(); await page.waitForTimeout(2200);
    return { ctx, page };
  };
  const switcherFacts = (page) => page.evaluate(() => {
    const rows = [...document.querySelectorAll("[data-switcher-row]")];
    const all = document.querySelector("[data-switcher-row='all']");
    const desc = all ? all.querySelector("p") : null;
    const one = rows.find((r) => r.getAttribute("data-switcher-row") !== "all");
    const list = document.querySelector("[data-switcher-list]");
    const heading = document.querySelector("[data-switcher-heading]");
    const pad = (el) => (el ? getComputedStyle(el).padding : null);
    return {
      order: rows.map((r) => r.getAttribute("data-switcher-row")),
      allH: all ? all.getBoundingClientRect().height : 0, rowH: one ? one.getBoundingClientRect().height : 0,
      allPad: pad(all), rowPad: pad(one),
      descWhole: desc ? desc.scrollWidth <= desc.clientWidth + 1 && getComputedStyle(desc).textOverflow !== "ellipsis" : false,
      descLines: desc ? Math.round(desc.getBoundingClientRect().height / parseFloat(getComputedStyle(desc).lineHeight)) : 0,
      counts: document.querySelector("[data-all-counts]")?.textContent, cash: document.querySelector("[data-all-cash]")?.textContent,
      heading: !!heading,
      scrolls: list ? list.scrollHeight > list.clientHeight + 1 : false, search: !!document.querySelector("[data-switcher-search]"),
    };
  });
  // 1. the owner's panel: the top bar and the page's account menu, no search (10 accounts or fewer)
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `lists_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
    await page.reload(); await page.waitForTimeout(2400);
    const c = (checks[name] = {});
    await page.locator("[data-account-switcher]").click(); await page.waitForTimeout(400);
    c.top = await switcherFacts(page);
    await page.screenshot({ path: path.join(OUT, `topbar_all_row_${w}.png`) });
    await page.locator("[data-switcher-list]").evaluate((l) => l.scrollTo({ top: l.scrollHeight })); await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(OUT, `topbar_archived_${w}.png`) });
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    await menuPick(page, "Phase", "Prop");
    await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(350);
    c.menuGroups = await page.locator("[data-menu-group]").count();
    c.menuSearch = await page.locator("[data-menu-search]").count();
    c.allSub = await page.locator("[data-menu-list] [role=option]").first().innerText();
    await page.screenshot({ path: path.join(OUT, `page_account_menu_${w}.png`) });
    await page.keyboard.press("Escape");
    const t = c.top;
    const ok = t.order[0] === "all" && t.allH >= t.rowH - 1 && t.allPad === t.rowPad && t.descWhole && /\d+ active · \d+ archived/.test(t.counts || "") &&
      /\$/.test(t.cash || "") && t.heading && !t.search && c.menuGroups === 1 && c.menuSearch === 0 && /active · \d+ archived/.test(c.allSub);
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // 2. the scratch panel's 12 accounts: both lists scroll after 10 and get a search field
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `search_${w}`;
    const { ctx, page } = await scratchPage(name, w, h, "/performance");
    const c = (checks[name] = {});
    await page.locator("[data-account-switcher]").click(); await page.waitForTimeout(450);
    c.top = await switcherFacts(page);
    c.focusInSearch = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    await page.screenshot({ path: path.join(OUT, `topbar_12_accounts_${w}.png`) });
    await page.keyboard.type("lucid"); await page.waitForTimeout(300);
    c.topFiltered = await page.locator("[data-switcher-row]").evaluateAll((els) => els.map((e) => e.getAttribute("data-switcher-row")));
    await page.screenshot({ path: path.join(OUT, `topbar_search_lucid_${w}.png`) });
    await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter"); await page.waitForTimeout(500);
    c.pickedBySearch = await page.locator("[data-account-switcher]").getAttribute("aria-label");
    await menuPick(page, "Phase", "Prop");
    await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(400);
    c.menuSearch = await page.locator("[data-menu-search]").count();
    c.menuScrolls = await page.locator("[data-menu-list]").evaluate((l) => l.scrollHeight > l.clientHeight + 1);
    c.menuVisibleRows = await page.locator("[data-menu-list]").evaluate((l) => [...l.querySelectorAll("[role=option]")].filter((o) => { const r = o.getBoundingClientRect(), b = l.getBoundingClientRect(); return r.top >= b.top - 1 && r.bottom <= b.bottom + 1; }).length);
    await page.screenshot({ path: path.join(OUT, `page_menu_12_accounts_${w}.png`) });
    await page.keyboard.type("91"); await page.waitForTimeout(250);
    c.menuFiltered = await page.locator("[data-menu-list] [role=option]").allInnerTexts();
    await page.screenshot({ path: path.join(OUT, `page_menu_search_91_${w}.png`) });
    await page.keyboard.press("Escape");
    const t = c.top;
    const ok = t.search && t.scrolls && c.focusInSearch === "Search accounts" && c.topFiltered.length >= 2 && c.topFiltered.every((n) => /lucid/.test(n)) &&
      /lucid/.test(c.pickedBySearch || "") && c.menuSearch === 1 && c.menuScrolls && c.menuVisibleRows <= 11 && c.menuFiltered.length === 4 && /\+\$/.test(t.cash || "") && !/\+\+/.test(t.cash || "");
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // 3. the account switch: the breakdown arrives with the body, in one crossfade; the height glides (Calm: instant)
  const sample = (page) => page.evaluate(() => new Promise((done) => {
    const out = []; const t0 = performance.now();
    const step = () => {
      const bodies = [...document.querySelectorAll("[data-page-body]")].map((b) => ({ key: b.getAttribute("data-page-body"), breakdown: !!b.querySelector("[data-breakdown]") }));
      const first = document.querySelector("[data-page-body]");
      const ah = first ? first.closest("[data-auto-height]") : null;
      out.push({ t: Math.round(performance.now() - t0), bodies, h: ah ? Math.round(ah.getBoundingClientRect().height) : null });
      if (performance.now() - t0 < 1500) requestAnimationFrame(step); else done(out);
    };
    step();
  }));
  for (const [label, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    for (const [w, h] of [[1440, 900], [960, 800]]) {
      const name = `switch_${label}_${w}`;
      const { ctx, page } = await open(name, w, h, "/performance", { reducedMotion: reduced });
      await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "mffur1"));
      await page.reload(); await page.waitForTimeout(2400);
      await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(500);
      const c = (checks[name] = {});
      for (const to of ["All accounts", "mffur1"]) {
        await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(250);
        const run = sample(page);
        await page.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${to}`) }).click();
        const s = await run;
        const late = s.some((f) => f.bodies.some((b) => b.key === "acct:all" && !b.breakdown) || f.bodies.some((b) => b.key !== "acct:all" && b.breakdown));
        const hs = [...new Set(s.map((f) => f.h))];
        const final = s[s.length - 1];
        c[to] = { late, heights: hs.length, framesWithBoth: s.filter((f) => f.bodies.length > 1).length, finalKey: final.bodies.map((b) => b.key), finalH: final.h };
        if (to === "All accounts") await page.screenshot({ path: path.join(OUT, `switch_${label}_to_all_${w}.png`) });
        await page.waitForTimeout(400);
      }
      const a = c["All accounts"], b = c["mffur1"];
      const ok = !a.late && !b.late && a.finalKey.join() === "acct:all" && b.finalKey.join() === "acct:mffur1" &&
        (label === "full" ? a.heights > 3 && b.heights > 3 && a.framesWithBoth > 2 : a.heights <= 2 && b.heights <= 2 && a.framesWithBoth <= 1 && b.framesWithBoth <= 1);
      if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
      await ctx.close();
    }
  }
  // the video: the switch both ways, twice, at 1440
  const VID = path.resolve(here, "../../../docs/design/video/stage3p");
  fs.mkdirSync(VID, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const page = await ctx.newPage();
  await page.goto(BASE + "/performance");
  await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "mffur1"); } catch {} });
  await page.reload(); await page.waitForTimeout(2000);
  await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800);
  await page.mouse.move(1200, 700);
  for (let i = 0; i < 2; i++) {
    for (const to of ["All accounts", "mffur1"]) {
      await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(600);
      await page.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${to}`) }).click();
      await page.mouse.move(1200, 700); await page.waitForTimeout(1800);
    }
  }
  const video = page.video();
  await ctx.close();
  const src = await video.path();
  fs.renameSync(src, path.join(VID, "account_switch_both_ways.webm"));
};

// owner 2026-11-16: an account switch on Performance never jumps: the current page stays until the new account's data
// (the page and the open tab's own data) is ready, then one crossfade with the height glide; numbers roll; a subtle
// progress hint only past ~300 ms; Calm: instant. Frame by frame on every tab, three accounts and "All accounts".
S["3q"] = async () => {
  const { menuPick } = helpers3f;
  const ROUTE = ["mffub1", "mffurf92", "All accounts", "mffur1", "All accounts", "mffub1"];
  const TABS3 = ["Equity", "Drawdown", "Monte Carlo", "Calendar", "Trades"];
  const sample = (page, ms) => page.evaluate((ms) => new Promise((done) => {
    const out = []; const t0 = performance.now();
    const y = (sel) => { const el = document.querySelector(sel); return el ? Math.round(el.getBoundingClientRect().top + window.scrollY) : null; };
    const step = () => {
      const pulse = [...document.querySelectorAll("[data-page='performance'] .animate-pulse")].filter((e) => { const r = e.getBoundingClientRect(); return r.height > 20 && getComputedStyle(e).opacity !== "0"; }).length;
      const hint = document.querySelector("[data-perf-loading][data-busy='true']");
      out.push({ t: Math.round(performance.now() - t0), foot: y("[data-page-footer]"), tabs: y("[role=tablist][aria-label='Performance views']"),
        kpis: y("[data-kpis]"), pulse, hint: !!hint && getComputedStyle(hint).opacity !== "0",
        panels: document.querySelectorAll("[data-panel-content]").length, kpiSame: document.querySelector("[data-kpi]")?.__cfTag === "kept",
        kpiText: document.querySelector("[data-kpi='Net P&L']")?.textContent ?? "",
        body: document.querySelector("[data-page-body]")?.getAttribute("data-page-body") ?? "",
        panelText: (document.querySelector("[data-panel-content] [data-account-note]")?.textContent ?? "") + "|" + ([...document.querySelectorAll("[data-panel-content]")].pop()?.getAttribute("class") ?? "") + "|" + (document.querySelector("[data-panel-content] h3, [data-panel-content] [data-risk-mode]")?.textContent ?? "").slice(0, 80) });
      if (performance.now() - t0 < ms) requestAnimationFrame(step); else done(out);
    };
    step();
  }), ms);
  // a jump: a skeleton on screen, a position going back the way it came, or one frame carrying most of a large move
  const judge = (s, calm) => {
    const bad = [];
    if (s.some((f) => f.pulse)) bad.push("placeholder shown");
    // one step: until the page body takes the new account, nothing in the tab panel changes either
    const before = s.filter((f) => f.body === s[0].body);
    if (before.some((f) => f.panelText !== s[0].panelText)) bad.push("the tab panel changed before the rest of the page");
    for (const k of ["foot", "tabs"]) {
      const v = s.map((f) => f[k]).filter((x) => x !== null);
      const steps = v.slice(1).map((x, i) => x - v[i]).filter((d) => Math.abs(d) > 1);
      const total = v[v.length - 1] - v[0];
      if (steps.some((d) => Math.sign(d) !== Math.sign(steps[0]))) bad.push(`${k} reverses (${steps.join(",")})`);
      const big = Math.max(0, ...steps.map(Math.abs));
      if (calm) { if (steps.length > 1) bad.push(`${k} moves over ${steps.length} frames in Calm`); }
      else if (Math.abs(total) > 40 && big > Math.max(24, 0.45 * Math.abs(total))) bad.push(`${k} jumps ${big}px in one frame of ${total}`);
    }
    return bad;
  };
  for (const [label, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    for (const [w, h] of [[1440, 900], [960, 800]]) {
      const name = `jump_${label}_${w}`;
      const { ctx, page } = await open(name, w, h, "/performance", { reducedMotion: reduced });
      await page.evaluate(() => { localStorage.setItem("creamflow.focusAccount", "mffur1"); localStorage.removeItem("creamflow.lastAccount"); });
      await page.reload(); await page.waitForTimeout(2400);
      await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(600);
      const c = (checks[name] = {});
      for (const t of TABS3) {
        await page.getByRole("tab", { name: t, exact: true }).first().click(); await page.waitForTimeout(1400);
        c[t] = [];
        for (const to of ROUTE) {
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(250);
          await page.evaluate(() => { const k = document.querySelector("[data-kpi]"); if (k) k.__cfTag = "kept"; });
          const run = sample(page, 1300);
          await page.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${to}`) }).click();
          const s = await run;
          const bad = judge(s, label === "calm");
          const moves = s.map((f) => f.foot).filter((v, i, a) => i === 0 || v !== a[i - 1]);
          // the KPI tiles stay (their numbers roll to the new values), never drawn again from nothing
          if (!s.every((f) => f.kpiSame)) bad.push("KPI tiles redrawn");
          const texts = [...new Set(s.map((f) => f.kpiText))];
          c[t].push({ to, bad, frames: s.length, footMoves: moves.length - 1, footFrom: moves[0], footTo: moves[moves.length - 1], hint: s.some((f) => f.hint), kpiTexts: texts.length });
          if (bad.length) problems.push(`${name} ${t} → ${to}: ${bad.join("; ")}`);
          if (t === "Calendar" && to === "All accounts" && w === 1440) await page.screenshot({ path: path.join(OUT, `${label}_calendar_all_${w}.png`) });
          await page.waitForTimeout(500);
        }
      }
      // a slow answer (held 900 ms on purpose): the old page stays, the thin progress line shows after ~300 ms, then the
      // switch happens in one step; a quick one never shows the line
      if (w === 1440) {
        await page.getByRole("tab", { name: "Calendar", exact: true }).first().click(); await page.waitForTimeout(1200);
        await page.route(/\/api\/performance(\/calendar)?\?/, async (r) => { await new Promise((ok) => setTimeout(ok, 900)); await r.continue(); });
        const slow = [];
        for (const to of ["All accounts", "mffub1"]) {
          await page.getByRole("button", { name: /^Account:/ }).click(); await page.waitForTimeout(250);
          await page.evaluate(() => { const k = document.querySelector("[data-kpi]"); if (k) k.__cfTag = "kept"; });
          const run = sample(page, 3200);
          await page.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${to}`) }).click();
          if (to === "All accounts") { await page.waitForTimeout(600); await page.screenshot({ path: path.join(OUT, `${label}_slow_load_hint_${w}.png`), clip: { x: 0, y: 0, width: w, height: 420 } }); }
          const s = await run;
          const first = s.find((f) => f.hint);
          const bad = judge(s, label === "calm");
          const moved = s.find((f, i) => i && f.foot !== s[0].foot);
          slow.push({ to, bad, hintAt: first ? first.t : null, movedAt: moved ? moved.t : null });
          await page.waitForTimeout(400);
          if (bad.length || !first || first.t < 250 || !moved || moved.t < first.t) problems.push(`${name} slow → ${to}: ${JSON.stringify(slow[slow.length - 1])}`);
        }
        await page.unroute(/\/api\/performance(\/calendar)?\?/);
        c.slow = slow;
      }
      const quick = TABS3.flatMap((t) => c[t]).filter((x) => x.hint).length;
      c.quickWithHint = quick;
      await ctx.close();
    }
  }
  // the video: three accounts and All accounts, both ways, on Equity then Calendar, at 1440 (Full)
  const VID = path.resolve(here, "../../../docs/design/video/stage3q");
  fs.mkdirSync(VID, { recursive: true });
  const vctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const vp = await vctx.newPage();
  await vp.goto(BASE + "/performance");
  await vp.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "mffur1"); } catch {} });
  await vp.reload(); await vp.waitForTimeout(2000);
  await menuPick(vp, "Phase", "Prop"); await vp.waitForTimeout(800);
  for (const t of ["Equity", "Calendar"]) {
    await vp.getByRole("tab", { name: t, exact: true }).first().click(); await vp.waitForTimeout(1200);
    for (const to of ["mffub1", "mffurf92", "All accounts", "mffur1"]) {
      await vp.getByRole("button", { name: /^Account:/ }).click(); await vp.waitForTimeout(500);
      await vp.locator("[data-menu-list]").getByRole("option", { name: new RegExp(`^${to}`) }).click();
      await vp.mouse.move(1300, 860); await vp.waitForTimeout(1500);
    }
  }
  const video = vp.video();
  await vctx.close();
  fs.renameSync(await video.path(), path.join(VID, "account_switch_no_jump.webm"));
};

// owner 2026-11-16: the Accounts page lists active accounts only (no "Show archived" toggle, no gap: the cards right
// under the title block, Add account to its right); past accounts open from Performance → All accounts → a row's Edit
// (archived ones too), where the status can be set back; the editor's back button returns to Performance.
S["3r"] = async () => {
  const { menuPick } = helpers3f;
  const SCRATCH = process.env.CF_SCRATCH ?? "http://127.0.0.1:8734";
  const layout = (page) => page.evaluate(() => {
    const head = document.querySelector("header:not([data-topbar])");
    const add = [...document.querySelectorAll("button")].find((b) => /Add account/.test(b.textContent ?? ""));
    const card = document.querySelector("[data-account-card]");
    const r = (el) => (el ? el.getBoundingClientRect() : null);
    const h = r(head), a = r(add), c = r(card);
    return { toggle: !!document.querySelector("[data-show-archived]"), cards: [...document.querySelectorAll("[data-account-card]")].map((e) => e.getAttribute("data-account-card")),
      gap: h && c ? Math.round(c.top - h.bottom) : null, addInHeader: !!(h && a && a.top >= h.top - 1 && a.bottom <= h.bottom + 1),
      addRight: h && a ? Math.round(h.right - a.right) : null, count: document.querySelector("header:not([data-topbar]) p")?.textContent ?? "" };
  });
  // 1. the owner's panel: the Accounts page and the breakdown's Edit
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `accounts_${w}`;
    const { ctx, page } = await open(name, w, h, "/accounts");
    await page.reload(); await page.waitForTimeout(1800);
    const c = (checks[name] = {});
    c.layout = await layout(page);
    await page.screenshot({ path: path.join(OUT, `accounts_active_only_${w}.png`) });
    const L = c.layout;
    if (L.toggle || L.gap === null || L.gap > 26 || !L.addInHeader || L.addRight > 1) problems.push(`${name}: ${JSON.stringify(L)}`);
    await ctx.close();
  }
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    const name = `breakdown_edit_${w}`;
    const { ctx, page } = await open(name, w, h, "/performance");
    await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "all"));
    await page.reload(); await page.waitForTimeout(2200);
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800);
    const c = (checks[name] = {});
    await page.locator("[data-breakdown]").scrollIntoViewIfNeeded(); await page.waitForTimeout(400);
    c.edit = await page.locator("[data-breakdown-edit]").evaluateAll((els) => els.map((e) => e.getAttribute("data-breakdown-edit")));
    c.noEdit = await page.locator("[data-breakdown-noedit]").evaluateAll((els) => els.map((e) => e.getAttribute("data-breakdown-noedit")));
    c.overflow = await page.locator("[data-breakdown] .overflow-x-auto").evaluate((el) => el.scrollWidth > el.clientWidth + 1);
    // the Edit column stays in view when the table scrolls sideways
    c.editVisible = await page.evaluate(() => { const b = document.querySelector("[data-breakdown-edit='mffur1']"), box = document.querySelector("[data-breakdown]"); if (!b || !box) return false; const r = b.getBoundingClientRect(), o = box.getBoundingClientRect(); return r.left >= o.left && r.right <= o.right + 1; });
    await page.locator("[data-breakdown]").screenshot({ path: path.join(OUT, `breakdown_edit_column_${w}.png`) });
    if (w === 1440) {
      await page.locator("[data-breakdown-edit='mffur1']").click(); await page.waitForTimeout(1600);
      c.url = new URL(page.url()).pathname;
      c.back = await page.getByRole("button", { name: /Performance/ }).first().innerText().catch(() => null);
      await page.screenshot({ path: path.join(OUT, `editor_from_performance_${w}.png`) });
      await page.getByRole("button", { name: /Performance/ }).first().click(); await page.waitForTimeout(1600);
      c.backUrl = new URL(page.url()).pathname;
    }
    const ok = c.edit.includes("mffur1") && c.editVisible && c.noEdit.length > 0 && (w !== 1440 || (c.url === "/accounts/mffur1" && /Performance/.test(c.back ?? "") && c.backUrl === "/performance"));
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // 2. the scratch panel (its accounts file holds the finished accounts): Edit an archived account, set it back to
  // active, save: it returns to Performance and the Accounts page lists it again
  {
    const name = "unarchive_1440";
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error") problems.push(`${name}: console ${m.text()}`); });
    await page.goto(SCRATCH + "/performance");
    await page.evaluate(() => { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "all"); });
    await page.reload(); await page.waitForTimeout(2200);
    await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800);
    const c = (checks[name] = {});
    c.edit = await page.locator("[data-breakdown-edit]").evaluateAll((els) => els.map((e) => e.getAttribute("data-breakdown-edit")));
    await page.locator("[data-breakdown-edit='mffur91']").scrollIntoViewIfNeeded();
    await page.locator("[data-breakdown]").screenshot({ path: path.join(OUT, `breakdown_archived_editable_1440.png`) });
    await page.locator("[data-breakdown-edit='mffur91']").click(); await page.waitForTimeout(1600);
    c.url = new URL(page.url()).pathname;
    await page.getByRole("radiogroup", { name: "Status" }).scrollIntoViewIfNeeded();
    c.statusBefore = await page.getByRole("radiogroup", { name: "Status" }).locator("[aria-checked=true]").innerText();
    await page.screenshot({ path: path.join(OUT, `editor_archived_account_1440.png`) });
    await page.getByRole("radiogroup", { name: "Status" }).getByRole("radio", { name: "Active" }).click(); await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, `editor_status_back_to_active_1440.png`) });
    await page.getByRole("button", { name: /^Save as checked$|^Save$/ }).first().click(); await page.waitForTimeout(1800);
    c.afterSave = new URL(page.url()).pathname;
    await page.goto(SCRATCH + "/accounts"); await page.waitForTimeout(1800);
    c.layout = await layout(page);
    await page.screenshot({ path: path.join(OUT, `accounts_after_unarchive_1440.png`) });
    const ok = c.edit.includes("mffur91") && c.url === "/accounts/mffur91" && /Failed/.test(c.statusBefore) && c.afterSave === "/performance" && c.layout.cards.includes("mffur91") && !c.layout.toggle;
    if (!ok) problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
};

// owner 2026-11-16: every page change keeps the current page until the next page's data is loaded, then crossfades to
// it filled, the shared footer gliding with the height; Edit in the breakdown goes straight to the filled editor; a thin
// line only past ~300 ms; Calm: an instant swap. Frame by frame, every page switch, 1440 and 960, Full and Calm.
S["3s"] = async () => {
  const { menuPick } = helpers3f;
  // owner 2026-11-16 (second round): the leaving page never moves (it fades out where it is, its scroll untouched), the
  // next page opens at its own top; each page's entrance plays when it is revealed (Performance's numbers roll in, its
  // cards stagger), never on a switch inside the page; Calm: no entrance
  const sample = (page, ms) => page.evaluate((ms) => new Promise((done) => {
    const out = []; const t0 = performance.now();
    // what is seen: the opacities down the tree, 0 for anything hidden
    const op = (el) => { if (getComputedStyle(el).visibility === "hidden") return 0; let o = 1; for (let e = el; e && e !== document.body; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity || "1"); return Math.round(o * 100) / 100; };
    const main = document.getElementById("cf-main");
    const step = () => {
      const mainTop = main.getBoundingClientRect().top;
      const views = [...document.querySelectorAll("[data-view]")].map((v) => {
        const sh = v.querySelector(":scope > [data-shift]") ?? v;
        return { id: v.getAttribute("data-view"), o: op(v), top: Math.round((sh.getBoundingClientRect().top - mainTop) * 10) / 10 };
      });
      const holes = [...document.querySelectorAll(".cf-skeleton, .animate-pulse")].filter((e) => { const r = e.getBoundingClientRect(); return r.height > 20 && r.bottom > 0 && r.top < innerHeight && op(e) > 0.05; }).length;
      const cards = {};
      for (const el of document.querySelectorAll("[data-view] [data-kpi], [data-view] [data-account-card], [data-view] [data-breakdown], [data-view] section[id^='set-'], [data-view] form, [data-view] [data-field]")) {
        const v = el.closest("[data-view]")?.getAttribute("data-view") ?? "home";
        const key = `${v}|${el.getAttribute("data-kpi") ?? el.getAttribute("data-account-card") ?? el.getAttribute("data-field") ?? el.id ?? el.tagName}`;
        const vals = [...el.querySelectorAll("input")].map((i) => i.value).join("|");
        cards[key] = (el.textContent ?? "").replace(/\s+/g, " ").slice(0, 300) + "#" + vals;
      }
      const foot = document.querySelector("[data-page-footer]");
      const kpi = document.querySelector("[data-view='performance'] [data-kpi='Net P&L']");
      out.push({ t: Math.round(performance.now() - t0), views, holes, cards, path: location.pathname, scroll: Math.round(main.scrollTop),
        foot: foot ? { y: Math.round(foot.getBoundingClientRect().top), o: op(foot), kept: foot.__cfTag === "kept" } : null,
        kpi: kpi ? { text: (kpi.textContent ?? "").replace(/\s+/g, " ").slice(0, 60), o: op(kpi) } : null,
        list: !!document.querySelector("[data-view] [data-account-card]"), hint: !!document.querySelector("[data-nav-loading][data-busy='true']") });
      if (performance.now() - t0 < ms) requestAnimationFrame(step); else done(out);
    };
    step();
  }), ms);
  // a card's text with its digits (and thousands commas) taken out: a number rolling in keeps it, a card going from
  // empty or "—" to its content does not
  const shape = (t) => t.replace(/[0-9,.+\-−]/g, "");
  const judge = (s, from, to, calm) => {
    const bad = [];
    if (s.some((f) => f.holes)) bad.push(`placeholder on screen (${s.filter((f) => f.holes).length} frames)`);
    // no blank frame: the old page and the new one together are always fully there
    const low = s.filter((f) => f.views.reduce((a, v) => a + v.o, 0) < 0.6);
    if (low.length) bad.push(`blank frames ${low.length}`);
    // every card of the arriving page is filled from its first frame (numbers may roll in)
    const fin = s[s.length - 1].cards;
    const late = new Set();
    for (const f of s) for (const [k, v] of Object.entries(f.cards)) if (k.startsWith(`${to}|`) && fin[k] !== undefined && shape(v) !== shape(fin[k])) late.add(k.split("|")[1]);
    if (late.size) bad.push(`cards filling in: ${[...late].slice(0, 4).join(", ")}`);
    // the leaving page never moves on screen while it is there
    const leaving = s.map((f) => f.views.find((v) => v.id === from)).filter((v) => v && v.o > 0.02);
    const moved = leaving.filter((v) => Math.abs(v.top - leaving[0].top) > 1);
    if (moved.length) bad.push(`the leaving page moved (${leaving[0].top} → ${moved[0].top})`);
    // the next page opens at its own top (only its 8-px rise in Full)
    const coming = s.map((f) => f.views.find((v) => v.id === to)).filter(Boolean);
    const off = coming.filter((v) => v.top < -1 || v.top > (calm ? 1 : 9));
    if (off.length) bad.push(`the next page not at its top (${off[0].top})`);
    // the footer as seen: between scrolling pages the same one glides (never back, never most of a move at once)
    const fs = s.map((f) => f.foot);
    if (from !== "home" && to !== "home") {
      if (fs.some((f) => !f || !f.kept || f.o < 0.95)) bad.push("footer replaced or faded");
      const ys = fs.filter(Boolean).map((f) => f.y);
      const steps = ys.slice(1).map((y, i) => y - ys[i]).filter((d) => Math.abs(d) > 1);
      const total = ys[ys.length - 1] - ys[0];
      if (steps.some((d) => Math.sign(d) !== Math.sign(steps[0]))) bad.push(`footer goes back (${steps.join(",")})`);
      if (!calm && Math.abs(total) > 40 && Math.max(...steps.map(Math.abs)) > Math.max(24, 0.45 * Math.abs(total))) bad.push(`footer jumps ${Math.max(...steps.map(Math.abs))} of ${total}`);
      if (calm && steps.length > 1) bad.push(`footer moves over ${steps.length} frames in Calm`);
    } else if (!calm) {
      const os = fs.map((f) => (f ? f.o : 0));
      const mid = os.filter((o) => o > 0.08 && o < 0.92).length;
      if (os[0] !== os[os.length - 1] && mid < 2) bad.push(`footer pops (${mid} in-between frames)`);
    }
    // the entrance: Performance's numbers roll in and its cards fade in after the reveal (Full); nothing in Calm
    if (to === "performance") {
      const ks = s.map((f) => f.kpi).filter(Boolean);
      const texts = new Set(ks.map((k) => k.text));
      const faded = ks.some((k) => k.o < 0.5);
      if (!calm && (texts.size < 3 || !faded)) bad.push(`no entrance (${texts.size} values, faded ${faded})`);
      if (calm && (texts.size > 1 || ks.some((k) => k.o < 0.99))) bad.push(`an entrance in Calm (${texts.size} values)`);
    }
    return bad;
  };
  const viewOf = (s) => { const f = s[s.length - 1]; return f.views.filter((v) => v.o > 0.9).map((v) => v.id).join(); };
  const clickTo = async (page, from, to) => {
    if (from === "home") return page.locator(`[data-home-node='${to}'] button`).first().click();
    return page.locator(`[data-dock-item='${to}']`).first().click();
  };
  const ROUTE = [["home", "accounts"], ["accounts", "settings"], ["settings", "performance"], ["performance", "accounts"], ["accounts", "performance"],
    ["performance", "settings"], ["settings", "home"], ["home", "performance"], ["performance", "home"], ["home", "settings"], ["settings", "accounts"], ["accounts", "home"]];
  for (const [label, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    for (const [w, h] of [[1440, 900], [960, 800]]) {
      const name = `pages_${label}_${w}`;
      const { ctx, page } = await open(name, w, h, "/", { reducedMotion: reduced });
      await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "all"));
      // the session's first-open intro (Full): skipped with a key, so the nodes are there to click
      await page.reload(); await page.waitForTimeout(900); await page.keyboard.press("x"); await page.waitForTimeout(800);
      const c = (checks[name] = { switches: [] });
      for (const [from, to] of ROUTE) {
        await page.mouse.move(5, 300); await page.waitForTimeout(150);
        // the leaving page is scrolled down first (where it can be): it must fade out exactly there
        await page.evaluate(() => { const m = document.getElementById("cf-main"); m.scrollTop = Math.min(420, m.scrollHeight - m.clientHeight); });
        await page.waitForTimeout(200);
        await page.evaluate(() => { const f = document.querySelector("[data-page-footer]"); if (f) f.__cfTag = "kept"; });
        const run = sample(page, 2000);
        const t0 = Date.now();
        await clickTo(page, from, to);
        const s = await run;
        const arrived = s.find((f) => f.views.some((v) => v.id === to));
        const bad = judge(s, from, to, label === "calm");
        if (viewOf(s) !== to) bad.push(`ended on ${viewOf(s)}`);
        c.switches.push({ from, to, bad, arriveMs: arrived ? arrived.t : null, hint: s.some((f) => f.hint) });
        if (bad.length) problems.push(`${name} ${from} → ${to}: ${bad.join("; ")}`);
        if (w === 1440 && label === "full" && ["accounts", "performance", "settings"].includes(to) && from !== "home") await page.screenshot({ path: path.join(OUT, `${label}_${from}_to_${to}_${w}.png`) });
        void t0;
        await page.waitForTimeout(450);
      }
      // Edit in the breakdown: straight to the filled editor (never the accounts list), and back (the route ends on Home)
      await page.locator("[data-home-node='performance'] button").first().click(); await page.waitForTimeout(1500);
      await menuPick(page, "Phase", "Prop"); await page.waitForTimeout(800);
      await page.locator("[data-breakdown-edit='mffur1']").scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
      await page.evaluate(() => { const f = document.querySelector("[data-page-footer]"); if (f) f.__cfTag = "kept"; });
      const run = sample(page, 2000);
      await page.locator("[data-breakdown-edit='mffur1']").click();
      const s = await run;
      const bad = judge(s, "performance", "accounts", label === "calm");
      if (s.some((f) => f.list)) bad.push("the accounts list showed");
      const firstEd = s.find((f) => f.views.some((v) => v.id === "accounts"));
      const nameVal = firstEd ? Object.entries(firstEd.cards).find(([k]) => k.startsWith("accounts|fld-name"))?.[1] ?? "" : "";
      if (!/mffur1/.test(nameVal)) bad.push(`editor not filled on its first frame (${nameVal.slice(0, 40)})`);
      if (s[s.length - 1].path !== "/accounts/mffur1") bad.push(`path ${s[s.length - 1].path}`);
      c.edit = { bad, arriveMs: firstEd ? firstEd.t : null };
      if (bad.length) problems.push(`${name} Edit → editor: ${bad.join("; ")}`);
      if (w === 1440 && label === "full") await page.screenshot({ path: path.join(OUT, `${label}_edit_to_editor_${w}.png`) });
      await page.waitForTimeout(400);
      await page.evaluate(() => { const f = document.querySelector("[data-page-footer]"); if (f) f.__cfTag = "kept"; });
      const run2 = sample(page, 2000);
      await page.getByRole("button", { name: /^Performance$/ }).first().click();
      const s2 = await run2;
      const bad2 = judge(s2, "accounts", "performance", label === "calm");
      if (viewOf(s2) !== "performance") bad2.push(`ended on ${viewOf(s2)}`);
      const phaseShown = await page.getByRole("button", { name: /^Phase:/ }).innerText();
      if (!/Prop/.test(phaseShown)) bad2.push(`came back to ${phaseShown}`);
      c.editBack = { bad: bad2 };
      if (bad2.length) problems.push(`${name} editor → Performance: ${bad2.join("; ")}`);
      await ctx.close();
    }
  }
  // a slow page (its answer held 900 ms on purpose): the current page stays, the line shows after ~300 ms, then one step
  {
    const name = "slow_settings_1440";
    const { ctx, page } = await open(name, 1440, 900, "/accounts");
    await page.reload(); await page.waitForTimeout(2200);
    await page.route(/\/api\/settings$/, async (r) => { await new Promise((ok) => setTimeout(ok, 900)); await r.continue(); });
    await page.evaluate(() => { const f = document.querySelector("[data-page-footer]"); if (f) f.__cfTag = "kept"; });
    const run = sample(page, 2200);
    await page.locator("[data-dock-item='settings']").first().click();
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, `slow_page_hint_1440.png`), clip: { x: 0, y: 0, width: 1440, height: 360 } });
    const s = await run;
    const hintAt = s.find((f) => f.hint)?.t ?? null;
    const arrived = s.find((f) => f.views.some((v) => v.id === "settings"))?.t ?? null;
    const bad = judge(s, "accounts", "settings", false);
    const c = (checks[name] = { hintAt, arrived, bad });
    if (bad.length || hintAt === null || hintAt < 250 || arrived === null || arrived < 850 || s.filter((f) => f.t < arrived).some((f) => !f.views.some((v) => v.id === "accounts" && v.o > 0.95)))
      problems.push(`${name}: ${JSON.stringify(c)}`);
    await ctx.close();
  }
  // the video: every page, then Edit and back, at 1440 (Full)
  const VID = path.resolve(here, "../../../docs/design/video/stage3s");
  fs.mkdirSync(VID, { recursive: true });
  const vctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const vp = await vctx.newPage();
  await vp.goto(BASE + "/");
  await vp.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "all"); } catch {} });
  await vp.reload(); await vp.waitForTimeout(900); await vp.keyboard.press("x"); await vp.waitForTimeout(900);
  // hover first (as a person does): the page's data is loading while the pointer rests on the icon, so the switch is quick
  const hovered = [];
  for (const [from, to] of ROUTE.slice(0, 9)) {
    await (from === "home" ? vp.locator(`[data-home-node='${to}'] button`).first() : vp.locator(`[data-dock-item='${to}']`).first()).hover(); await vp.waitForTimeout(250);
    // in the page: the click, the address change (= the data was there and the switch began) and the next page's first frame
    const watch = vp.evaluate((v) => new Promise((done) => {
      let t0 = null, tUrl = null; const p0 = location.pathname;
      addEventListener("pointerdown", () => { t0 = performance.now(); }, { once: true, capture: true });
      const step = () => {
        if (t0 !== null && tUrl === null && location.pathname !== p0) tUrl = performance.now();
        if (t0 !== null && document.querySelector(`[data-view='${v}']`)) { done({ dataWait: Math.round((tUrl ?? performance.now()) - t0), total: Math.round(performance.now() - t0) }); return; }
        requestAnimationFrame(step);
      };
      step();
    }), to);
    await clickTo(vp, from, to);
    hovered.push({ from, to, ...(await watch) });
    await vp.waitForTimeout(1300);
  }
  checks.hover_first_1440 = hovered;
  await vp.locator("[data-home-node='performance'] button").first().click(); await vp.waitForTimeout(1500);
  await menuPick(vp, "Phase", "Prop"); await vp.waitForTimeout(800);
  await vp.locator("[data-breakdown-edit='mffur1']").scrollIntoViewIfNeeded(); await vp.waitForTimeout(500);
  await vp.locator("[data-breakdown-edit='mffur1']").hover(); await vp.waitForTimeout(300);
  await vp.locator("[data-breakdown-edit='mffur1']").click(); await vp.waitForTimeout(1500);
  await vp.getByRole("button", { name: /^Performance$/ }).first().click(); await vp.waitForTimeout(1500);
  const video = vp.video();
  await vctx.close();
  fs.renameSync(await video.path(), path.join(VID, "page_changes.webm"));
};

// owner 2026-11-16: returning to Home (after the session's first-open intro) plays a short return entrance (~450 ms): the
// wordmark fades in from 0.98 scale, the nodes fade in 40 ms apart, each line draws out as its node arrives; the leaving
// page fades out where it is; a click or a key finishes it at once; Calm: instant.
S["3t"] = async () => {
  const ORDER = ["live", "review", "performance", "settings", "accounts", "gameplan"];
  const sample = (page, ms) => page.evaluate((ms) => new Promise((done) => {
    const out = []; const t0 = performance.now();
    window.__sampleT0 = t0; window.__skipAt = null;
    const mark = () => { if (window.__skipAt === null) window.__skipAt = performance.now(); };
    window.__markSkip = mark;
    const op = (el) => { if (!el || getComputedStyle(el).visibility === "hidden") return 0; let o = 1; for (let e = el; e && e !== document.body; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity || "1"); return Math.round(o * 1000) / 1000; };
    const main = document.getElementById("cf-main");
    const step = () => {
      const mainTop = main.getBoundingClientRect().top;
      const views = [...document.querySelectorAll("[data-view]")].map((v) => {
        const sh = v.querySelector(":scope > [data-shift]") ?? v;
        return { id: v.getAttribute("data-view"), o: op(v), top: Math.round((sh.getBoundingClientRect().top - mainTop) * 10) / 10 };
      });
      const word = document.querySelector("[data-home-word]");
      const wordScale = word ? new DOMMatrix(getComputedStyle(word).transform === "none" ? undefined : getComputedStyle(word).transform).a : null;
      const nodes = {}, lines = {};
      for (const n of document.querySelectorAll("[data-home-node]")) { const b = n.querySelector("button"); nodes[n.getAttribute("data-home-node")] = b ? Math.round(parseFloat(getComputedStyle(b).opacity) * 100) / 100 : 0; }
      for (const g of document.querySelectorAll("[data-line]")) {
        const pth = g.querySelector("path");
        const dash = pth ? (pth.getAttribute("stroke-dasharray") ?? getComputedStyle(pth).strokeDasharray ?? "") : "";
        const drawn = /^\s*([\d.]+)/.exec(dash.replace(/px/g, ""));
        lines[g.getAttribute("data-line")] = pth && parseFloat(getComputedStyle(pth).opacity) > 0.01 ? (drawn ? Math.round(parseFloat(drawn[1]) * 100) / 100 : 1) : 0;
      }
      out.push({ t: Math.round(performance.now() - t0), views, word: word ? { o: Math.round(parseFloat(getComputedStyle(word).opacity) * 1000) / 1000, s: Math.round(wordScale * 1000) / 1000 } : null, nodes, lines });
      if (performance.now() - t0 < ms) requestAnimationFrame(step); else done(out);
    };
    step();
  }), ms);
  // the return entrance from the frame Home is first there
  const judge = (s, from, calm, skipAt = null) => {
    const bad = [];
    const low = s.filter((f) => f.views.reduce((a, v) => a + v.o, 0) < 0.6);
    if (low.length) bad.push(`blank frames ${low.length}`);
    const leaving = s.map((f) => f.views.find((v) => v.id === from)).filter((v) => v && v.o > 0.02);
    if (leaving.some((v) => Math.abs(v.top - leaving[0].top) > 1)) bad.push("the leaving page moved");
    const i0 = s.findIndex((f) => f.views.some((v) => v.id === "home") && f.word);
    if (i0 < 0) return [...bad, "Home never came"];
    const hs = s.slice(i0), t0 = hs[0].t;
    if (hs.some((f) => Math.abs(f.views.find((v) => v.id === "home")?.top ?? 0) > 1)) bad.push("Home not at its top");
    const done = (f) => f.word.o >= 0.99 && f.word.s >= 0.995 && ORDER.every((n) => f.nodes[n] >= 0.99 && f.lines[n] >= 0.99);
    const di = hs.findIndex(done);
    const res = { doneMs: di < 0 ? null : hs[di].t - t0 };
    if (calm) {
      if (di !== 0) {
        const f = hs[0];
        const miss = [f.word.o < 0.99 || f.word.s < 0.995 ? "wordmark" : null, ...ORDER.filter((n) => f.nodes[n] < 0.99).map((n) => `node ${n}`), ...ORDER.filter((n) => f.lines[n] < 0.99).map((n) => `line ${n}`)].filter(Boolean);
        bad.push(`Calm not instant (done at frame ${di}; first frame missing ${miss.join(", ")})`);
      }
      return { bad, res };
    }
    if (skipAt !== null) {
      const after = hs.filter((f) => f.t >= skipAt);
      const k = after.findIndex(done);
      res.afterSkipFrames = k;
      if (k < 0 || k > 2) bad.push(`a click or key didn't finish it (${k} frames)`);
      return { bad, res };
    }
    if (di < 0) bad.push("never finished");
    else if (res.doneMs > 650) bad.push(`took ${res.doneMs} ms`);
    // the wordmark: faint and slightly small at first, growing to 1 without going back
    const w0 = hs[0].word;
    if (!(w0.o < 0.5 && w0.s < 0.995 && w0.s >= 0.975)) bad.push(`wordmark start ${JSON.stringify(w0)}`);
    const ws = hs.map((f) => f.word.s), wo = hs.map((f) => f.word.o);
    if (ws.some((v, i) => i && v < ws[i - 1] - 0.0005) || wo.some((v, i) => i && v < wo[i - 1] - 0.005)) bad.push("wordmark goes back");
    // the nodes in order, ~40 ms apart; each line starts drawing only once its node shows
    const at = Object.fromEntries(ORDER.map((n) => [n, hs.find((f) => f.nodes[n] > 0.05)?.t ?? null]));
    const lineAt = Object.fromEntries(ORDER.map((n) => [n, hs.find((f) => f.lines[n] > 0.05)?.t ?? null]));
    res.nodeAt = Object.fromEntries(ORDER.map((n) => [n, at[n] === null ? null : at[n] - t0]));
    res.lineAt = Object.fromEntries(ORDER.map((n) => [n, lineAt[n] === null ? null : lineAt[n] - t0]));
    if (ORDER.some((n, i) => at[n] === null || (i && at[n] < at[ORDER[i - 1]]))) bad.push(`nodes out of order ${JSON.stringify(res.nodeAt)}`);
    const spread = at[ORDER[5]] - at[ORDER[0]];
    if (!(spread >= 120 && spread <= 420)) bad.push(`stagger spread ${spread} ms`);
    if (ORDER.some((n) => lineAt[n] === null || lineAt[n] < at[n] - 20)) bad.push(`a line before its node ${JSON.stringify(res.lineAt)}`);
    return { bad, res };
  };
  for (const [label, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    for (const [w, h] of [[1440, 900], [960, 800]]) {
      const name = `home_return_${label}_${w}`;
      const { ctx, page } = await open(name, w, h, "/", { reducedMotion: reduced });
      await page.evaluate(() => localStorage.setItem("creamflow.focusAccount", "all"));
      // the session's first-open intro (Full): skipped with a key, so the nodes are there to click
      await page.reload(); await page.waitForTimeout(900); await page.keyboard.press("x"); await page.waitForTimeout(700);
      const c = (checks[name] = {});
      for (const [from, how] of [["accounts", "plain"], ["settings", "plain"], ["performance", "plain"], ["accounts", "click"], ["settings", "key"]]) {
        await page.locator(`[data-home-node='${from}'] button`).first().click(); await page.waitForTimeout(1600);
        await page.mouse.move(5, 300);
        await page.evaluate(() => { const m = document.getElementById("cf-main"); m.scrollTop = Math.min(420, m.scrollHeight - m.clientHeight); });
        await page.waitForTimeout(250);
        const run = sample(page, 1100);
        const tClick = await page.evaluate(() => performance.now());
        await page.locator("[data-dock-item='home']").first().click();
        if (how !== "plain" && label === "full") {
          await page.waitForFunction(() => !!document.querySelector("[data-home-word]"));
          await page.waitForTimeout(90);
          // the moment the click or key reaches the page (capture phase, before the panel's own handlers)
          await page.evaluate(() => { addEventListener("pointerdown", () => window.__markSkip(), { capture: true, once: true }); addEventListener("keydown", () => window.__markSkip(), { capture: true, once: true }); });
          if (how === "click") await page.mouse.click(Math.round(w * 0.3), Math.round(h * 0.55));
          else await page.keyboard.press("x");
        }
        const s = await run;
        const skipRel = await page.evaluate(() => (window.__skipAt === null ? null : window.__skipAt - window.__sampleT0));
        void tClick;
        const { bad, res } = judge(s, from, label === "calm", how !== "plain" && label === "full" ? skipRel : null);
        c[`${from}_${how}`] = { bad, ...res };
        if (bad.length) problems.push(`${name} ${from} → home (${how}): ${bad.join("; ")}`);
        if (w === 1440 && label === "full" && how === "plain" && from === "performance") {
          // mid-entrance and settled frames for the record
          await page.locator(`[data-home-node='settings'] button`).first().click(); await page.waitForTimeout(1600);
          await page.locator("[data-dock-item='home']").first().click();
          await page.waitForFunction(() => !!document.querySelector("[data-home-word]")); await page.waitForTimeout(150);
          await page.screenshot({ path: path.join(OUT, `home_return_mid_${w}.png`) });
          await page.waitForTimeout(900);
          await page.screenshot({ path: path.join(OUT, `home_return_settled_${w}.png`) });
        }
        await page.waitForTimeout(500);
      }
      await ctx.close();
    }
  }
  // the video: back to Home from three pages (each scrolled), at 1440 (Full)
  const VID = path.resolve(here, "../../../docs/design/video/stage3t");
  fs.mkdirSync(VID, { recursive: true });
  const vctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const vp = await vctx.newPage();
  await vp.goto(BASE + "/");
  await vp.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); localStorage.setItem("creamflow.focusAccount", "all"); } catch {} });
  await vp.reload(); await vp.waitForTimeout(3400);
  for (const from of ["accounts", "settings", "performance"]) {
    await vp.locator(`[data-home-node='${from}'] button`).first().click(); await vp.waitForTimeout(1400);
    await vp.evaluate(() => { const m = document.getElementById("cf-main"); m.scrollTo({ top: Math.min(420, m.scrollHeight - m.clientHeight), behavior: "smooth" }); });
    await vp.waitForTimeout(900);
    await vp.locator("[data-dock-item='home']").first().hover(); await vp.waitForTimeout(300);
    await vp.locator("[data-dock-item='home']").first().click(); await vp.mouse.move(1300, 880); await vp.waitForTimeout(1500);
  }
  const video = vp.video();
  await vctx.close();
  fs.renameSync(await video.path(), path.join(VID, "home_return.webm"));
};

// owner 2026-11-16: a banner arriving off Home unfolds its height on the 270-ms curve (like Home and like leaving), so
// the page below glides down in step, stacked banners included; frame by frame on Accounts, Settings and Performance,
// arriving and leaving, one and three: no frame where the page jumps. Calm: one step.
S["3u"] = async () => {
  // where the page's content is on screen (the content area's top edge moves with the banners above it)
  const sample = (page, ms) => page.evaluate((ms) => new Promise((done) => {
    const out = []; const t0 = performance.now();
    const step = () => {
      const m = document.getElementById("cf-main");
      out.push({ t: Math.round(performance.now() - t0), y: Math.round(m.getBoundingClientRect().top * 10) / 10, n: document.querySelectorAll("[data-banner]").length });
      if (performance.now() - t0 < ms) requestAnimationFrame(step); else done(out);
    };
    step();
  }), ms);
  // Calm: each banner in one step (three banners clicked apart are three steps)
  const judge = (s, calm, n = 1) => {
    const ys = s.map((f) => f.y);
    const steps = ys.slice(1).map((y, i) => y - ys[i]).filter((d) => Math.abs(d) > 0.5);
    const total = ys[ys.length - 1] - ys[0];
    const big = Math.max(0, ...steps.map(Math.abs));
    const bad = [];
    if (Math.abs(total) < 20) bad.push(`the page didn't move (${total})`);
    if (steps.some((d) => Math.sign(d) !== Math.sign(total))) bad.push(`goes back (${steps.join(",")})`);
    if (calm) { if (steps.length > n) bad.push(`Calm over ${steps.length} frames for ${n} banner(s)`); }
    else if (big > Math.max(24, 0.45 * Math.abs(total))) bad.push(`jumps ${big} of ${total}`);
    return { bad, total: Math.round(total), frames: steps.length, biggest: Math.round(big * 10) / 10 };
  };
  const gotIt = (page) => page.locator("[data-banner] button", { hasText: /^Got it$/ });
  for (const [label, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    for (const [w, h] of [[1440, 900], [960, 800]]) {
      for (const pg of ["accounts", "settings", "performance"]) {
        const name = `banners_${label}_${pg}_${w}`;
        const { ctx, page } = await open(name, w, h, `/${pg}`, { reducedMotion: reduced });
        await page.reload(); await page.waitForTimeout(2200);
        const c = (checks[name] = {});
        for (const n of [1, 3]) {
          // arriving: n banners (FLAT ALL with no engine: "nothing was sent"), the next while the last still unfolds
          const run = sample(page, n === 1 ? 700 : 1100);
          for (let i = 0; i < n; i++) { await page.locator("[data-flat-all]").click(); if (i < n - 1) await page.waitForTimeout(110); }
          const a = judge(await run, label === "calm", n);
          a.banners = await page.locator("[data-banner]").count();
          if (a.banners < n) a.bad.push(`only ${a.banners} banners`);
          c[`arrive_${n}`] = a;
          if (a.bad.length) problems.push(`${name} ${n} arriving: ${a.bad.join("; ")}`);
          if (w === 1440 && label === "full" && n === 3) await page.screenshot({ path: path.join(OUT, `${pg}_three_banners_${w}.png`), clip: { x: 0, y: 0, width: w, height: 420 } });
          await page.waitForTimeout(300);
          // leaving: Got it on each, the next while the last still folds
          // each banner by its id (a leaving banner stays in place while it folds, so "first" would be the same one)
          const ids = (await page.locator("[data-banner-id]").evaluateAll((els) => els.map((e) => e.getAttribute("data-banner-id")))).slice(0, n);
          const run2 = sample(page, n === 1 ? 900 : 1300);
          for (let i = 0; i < n; i++) { await page.locator(`[data-banner-id="${ids[i]}"] button`, { hasText: /^Got it$/ }).click(); if (i < n - 1) await page.waitForTimeout(110); }
          const l = judge(await run2, label === "calm", n);
          c[`leave_${n}`] = l;
          if (l.bad.length) problems.push(`${name} ${n} leaving: ${l.bad.join("; ")}`);
          await page.waitForTimeout(400);
        }
        await ctx.close();
      }
    }
  }
  // the video: Performance, one banner then three, arriving and leaving, at 1440 (Full)
  const VID = path.resolve(here, "../../../docs/design/video/stage3u");
  fs.mkdirSync(VID, { recursive: true });
  const vctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const vp = await vctx.newPage();
  await vp.route("**/*", (r) => r.continue());
  await vp.goto(BASE + "/performance");
  await vp.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
  await vp.reload(); await vp.waitForTimeout(2200);
  for (const n of [1, 3]) {
    for (let i = 0; i < n; i++) { await vp.locator("[data-flat-all]").click(); await vp.waitForTimeout(n === 1 ? 0 : 140); }
    await vp.mouse.move(700, 600); await vp.waitForTimeout(1200);
    const vids = (await vp.locator("[data-banner-id]").evaluateAll((els) => els.map((e) => e.getAttribute("data-banner-id")))).slice(0, n);
    for (const id of vids) { await vp.locator(`[data-banner-id="${id}"] button`, { hasText: /^Got it$/ }).click(); await vp.waitForTimeout(n === 1 ? 0 : 160); }
    await vp.mouse.move(700, 600); await vp.waitForTimeout(1200);
  }
  const video = vp.video();
  await vctx.close();
  fs.renameSync(await video.path(), path.join(VID, "banners_unfold.webm"));
};

// owner 2026-11-16: a critical banner off Home is fully visible from its first frame (over the content), then the page
// glides down beneath it on the 270-ms curve so nothing stays covered; frame by frame on Accounts, Settings and
// Performance, alone and on top of two info banners, then leaving (Acknowledge). Calm: the banner and one step.
S["3v"] = async () => {
  const sample = (page, ms) => page.evaluate((ms) => new Promise((done) => {
    const out = []; const t0 = performance.now();
    const op = (el) => { let o = 1; for (let e = el; e && e !== document.body; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity || "1"); return Math.round(o * 1000) / 1000; };
    const step = () => {
      const m = document.getElementById("cf-main");
      const b = document.querySelector('[data-banner="crit"] [data-kind="crit"]');
      let crit = null;
      if (b) {
        const r = b.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        crit = { top: Math.round(r.top * 10) / 10, h: Math.round(r.height * 10) / 10, bottom: Math.round(r.bottom * 10) / 10, o: op(b), onTop: !!hit && b.contains(hit) };
      }
      const strip = document.querySelector("[data-synthetic]");
      out.push({ t: Math.round(performance.now() - t0), y: Math.round(m.getBoundingClientRect().top * 10) / 10, below: strip ? Math.round(strip.getBoundingClientRect().top * 10) / 10 : null, crit });
      if (performance.now() - t0 < ms) requestAnimationFrame(step); else done(out);
    };
    step();
  }), ms);
  const judge = (s, calm, arriving) => {
    const bad = [];
    const ys = s.map((f) => f.y);
    const steps = ys.slice(1).map((y, i) => y - ys[i]).filter((d) => Math.abs(d) > 0.5);
    const total = ys[ys.length - 1] - ys[0];
    const big = Math.max(0, ...steps.map(Math.abs));
    if (Math.abs(total) < 20) bad.push(`the page didn't move (${total})`);
    if (steps.some((d) => Math.sign(d) !== Math.sign(total))) bad.push(`goes back (${steps.join(",")})`);
    if (calm) { if (steps.length > 1) bad.push(`Calm over ${steps.length} frames`); }
    else if (big > Math.max(24, 0.45 * Math.abs(total))) bad.push(`jumps ${big} of ${total}`);
    const res = { total: Math.round(total), frames: steps.length, biggest: Math.round(big * 10) / 10 };
    if (arriving) {
      const cs = s.map((f) => f.crit).filter(Boolean);
      const fin = cs[cs.length - 1];
      const first = cs[0];
      res.firstFrame = first;
      // fully readable in its first frame: opaque, at its full height, on top of whatever it overlaps
      if (!first || first.o < 0.999 || Math.abs(first.h - fin.h) > 0.5 || !first.onTop) bad.push(`first frame not fully readable ${JSON.stringify(first)}`);
      if (cs.some((c) => c.o < 0.999 || !c.onTop || Math.abs(c.h - fin.h) > 0.5)) bad.push("not fully readable in some frame");
      // nothing covered once the glide ends: what follows the banners starts at or below the banner's bottom
      const last = s[s.length - 1];
      if (last.below !== null && last.below < fin.bottom - 0.5) bad.push(`covers the content at the end (${last.below} < ${fin.bottom})`);
    }
    return { bad, res };
  };
  const newCtx = async (name, w, h, reduced) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark", reducedMotion: reduced });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => problems.push(`${name}: page error ${e.message}`));
    return { ctx, page };
  };
  for (const [label, reduced] of [["full", "no-preference"], ["calm", "reduce"]]) {
    for (const [w, h] of [[1440, 900], [960, 800]]) {
      for (const pg of ["accounts", "settings", "performance"]) {
        const name = `crit_${label}_${pg}_${w}`;
        const { ctx, page } = await newCtx(name, w, h, reduced);
        await page.goto(`${BASE}/${pg}`);
        await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
        await page.reload(); await page.waitForTimeout(2200);
        const c = (checks[name] = {});
        for (const withInfo of [false, true]) {
          const key = withInfo ? "over_two_info" : "alone";
          if (withInfo) {
            for (let i = 0; i < 2; i++) { await page.locator("[data-flat-all]").click(); await page.waitForTimeout(150); }
            await page.waitForTimeout(500);
          }
          // a critical banner: the flat request can't reach the server
          await page.route("**/api/actions/flat", (r) => r.abort());
          const run = sample(page, 800);
          await page.locator("[data-flat-all]").click();
          const a = judge(await run, label === "calm", true);
          await page.unroute("**/api/actions/flat");
          c[`${key}_arrive`] = { bad: a.bad, ...a.res };
          if (a.bad.length) problems.push(`${name} ${key} arriving: ${a.bad.join("; ")}`);
          await page.waitForTimeout(300);
          if (w === 1440 && label === "full" && pg === "performance") await page.screenshot({ path: path.join(OUT, `crit_${key}_settled_${w}.png`), clip: { x: 0, y: 0, width: w, height: 400 } });
          // leaving: Acknowledge (then the info banners, if any, by id)
          const run2 = sample(page, 900);
          await page.locator('[data-banner="crit"] button', { hasText: /^Acknowledge$/ }).click();
          const l = judge(await run2, label === "calm", false);
          c[`${key}_leave`] = { bad: l.bad, ...l.res };
          if (l.bad.length) problems.push(`${name} ${key} leaving: ${l.bad.join("; ")}`);
          await page.waitForTimeout(300);
          for (const id of await page.locator("[data-banner-id]").evaluateAll((els) => els.map((e) => e.getAttribute("data-banner-id")))) {
            await page.locator(`[data-banner-id="${id}"] button`).last().click().catch(() => {});
          }
          await page.waitForTimeout(600);
        }
        await ctx.close();
      }
    }
  }
  // a frame from the very start of the glide (the banner over the content), Performance at 1440
  {
    const { ctx, page } = await newCtx("crit_first_frame", 1440, 900, "no-preference");
    await page.goto(`${BASE}/performance`);
    await page.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
    await page.reload(); await page.waitForTimeout(2200);
    await page.route("**/api/actions/flat", (r) => r.abort());
    await page.locator("[data-flat-all]").click();
    await page.waitForFunction(() => !!document.querySelector('[data-banner="crit"]'));
    await page.waitForTimeout(40);
    await page.screenshot({ path: path.join(OUT, "crit_first_frames_over_content_1440.png"), clip: { x: 0, y: 0, width: 1440, height: 400 } });
    await ctx.close();
  }
  // the video: Performance, a critical banner alone and over two info banners, arriving and leaving (Full, 1440)
  const VID = path.resolve(here, "../../../docs/design/video/stage3v");
  fs.mkdirSync(VID, { recursive: true });
  const vctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", recordVideo: { dir: VID, size: { width: 1440, height: 900 } } });
  const vp = await vctx.newPage();
  await vp.goto(BASE + "/performance");
  await vp.evaluate(() => { try { sessionStorage.setItem("creamflow.splashShown", "1"); } catch {} });
  await vp.reload(); await vp.waitForTimeout(2200);
  for (const withInfo of [false, true]) {
    if (withInfo) { for (let i = 0; i < 2; i++) { await vp.locator("[data-flat-all]").click(); await vp.waitForTimeout(500); } await vp.waitForTimeout(600); }
    await vp.route("**/api/actions/flat", (r) => r.abort());
    await vp.locator("[data-flat-all]").click(); await vp.mouse.move(700, 650); await vp.waitForTimeout(1500);
    await vp.unroute("**/api/actions/flat");
    await vp.locator('[data-banner="crit"] button', { hasText: /^Acknowledge$/ }).click(); await vp.mouse.move(700, 650); await vp.waitForTimeout(1200);
    for (const id of await vp.locator("[data-banner-id]").evaluateAll((els) => els.map((e) => e.getAttribute("data-banner-id")))) {
      await vp.locator(`[data-banner-id="${id}"] button`).last().click().catch(() => {}); await vp.waitForTimeout(250);
    }
    await vp.mouse.move(700, 650); await vp.waitForTimeout(1000);
  }
  const video = vp.video();
  await vctx.close();
  fs.renameSync(await video.path(), path.join(VID, "critical_banner.webm"));
};

// ── stage 4: the main chart, the GEX radial and the Review page (docs/design/build_stage_4.md) ─────────────────
// "4": against a panel on the real preview round (read-only: nothing is answered); "4s": against a --synthetic panel
// (answers stay in memory), for the answer bar, the result card and the reveal.
async function reviewChecks(name, page) {
  const r = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const box = (s) => { const e = q(s); if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }; };
    return { chart: box("[data-plot] canvas"), radial: box("[data-radial] canvas"), facts: box("[data-facts]"), bar: box("[data-answer-bar]") ?? box("[data-result]"),
      clock: q("[data-clock]")?.textContent ?? null, hOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      titles: document.querySelectorAll("[title]").length, titled: [...document.querySelectorAll("[title]")].map((e) => e.tagName + ":" + e.getAttribute("title")).slice(0, 3) };
  });
  checks[name] = { ...(checks[name] ?? {}), ...r };
  if (!r.chart || r.chart.w < 300 || r.chart.h < 250) problems.push(`${name}: the main chart is missing or too small (${JSON.stringify(r.chart)})`);
  if (!r.radial) problems.push(`${name}: the GEX radial is missing`);
  if (r.hOverflow) problems.push(`${name}: the page scrolls sideways`);
  if (r.titles) problems.push(`${name}: ${r.titles} native title attribute(s): ${r.titled.join(", ")}`);
  // the replay bar (and the chart card) stay clear of the open dock at load
  const clash = await page.evaluate(() => { const d = document.querySelector("[data-dock]") ?? document.querySelector("nav[aria-label*=ock]"); const rb = document.querySelector("[data-replay]");
    if (!d || !rb) return null; const a = d.getBoundingClientRect(), b = rb.getBoundingClientRect(); return a.top < b.bottom && b.top < a.bottom && a.left < b.right && b.left < a.right ? { dockTop: Math.round(a.top), replayBottom: Math.round(b.bottom) } : false; });
  checks[name].replay_vs_dock = clash;
  // owner 2026-11-16, round 3: the replay bar is back at the chart's bottom and may sit under the dock (recorded only)
}
async function rshot(name, w, h, url, act, opts = {}) {
  return shot(name, w, h, url, async (p) => { await p.waitForSelector("[data-plot] canvas", { timeout: 15000 }); await p.waitForTimeout(700); if (act) await act(p); await reviewChecks(name, p); }, opts);
}
S["4"] = async () => {
  for (const [w, h] of [[1440, 900], [1280, 800], [960, 800]]) await rshot(`review_${w}`, w, h, "/review");
};
S["4s"] = async () => {
  for (const [w, h] of [[1440, 900], [960, 800]]) await rshot(`review_synthetic_${w}`, w, h, "/review");
  // §8: the card as sent before an answer holds nothing after the decision time and no outcome
  {
    const { ctx, page } = await open("cut_check", 1440, 900, "/review");
    for (const id of ["c01", "c02", "c03", "c04", "c05"]) {
      const c = await (await page.request.get(BASE + `/api/review/card?round=synthetic&card=${id}`)).json();
      const late = [...c.pre.bars.NQ, ...c.pre.bars.ES].filter((b) => b[0] > c.t.decision).length
        + c.pre.levels.NQ.flatMap((l) => l.segs).filter((g) => g[0] > c.t.decision).length
        + Object.values(c.pre.maps).flat().filter((m) => m.t > c.t.decision).length;
      const own = c.pre.events.filter((e) => e.own && e.label != null).length;
      checks[`cut_${id}`] = { post: "post" in c, outcome: "outcome" in c, stratum: "stratum" in c, late, own_label: own };
      if ("post" in c || "outcome" in c || "stratum" in c || late || own) problems.push(`cut_${id}: the card before its answer shows later data (${JSON.stringify(checks[`cut_${id}`])})`);
    }
    await ctx.close();
  }
  // replay: Restart (Home), three minutes on (→), the radial following the clock
  await rshot("replay_restart_1440", 1440, 900, "/review", async (p) => {
    await p.locator("[data-plot]").click({ position: { x: 5, y: 5 } }).catch(() => {});
    await p.keyboard.press("Home"); await p.waitForTimeout(500);
    for (let i = 0; i < 3; i++) { await p.keyboard.press("ArrowRight"); await p.waitForTimeout(120); }
    await p.waitForTimeout(700);
    checks.replay_restart_1440 = { clock: await p.locator("[data-clock]").textContent() };
  });
  // (the level and orb hover cards are checked by "4b" / "4br": fix6)
  await rshot("radial_tooltip_1440", 1440, 900, "/review", async (p) => {
    await p.locator("[data-radial]").scrollIntoViewIfNeeded(); await p.waitForTimeout(500);
    const cv = await p.locator("[data-radial] canvas").boundingBox();
    // sweep the canvas for an orb
    for (let y = 30; y < cv.height - 30 && !(await p.locator("[data-radial-tip]").count()); y += 9)
      for (let x = 30; x < cv.width - 30; x += 9) { await p.mouse.move(cv.x + x, cv.y + y); if (await p.locator("[data-radial-tip]").count()) break; }
    checks.radial_tooltip_1440 = { tip: await p.locator("[data-radial-tip]").textContent().catch(() => null) };
  });
  await rshot("es_1440", 1440, 900, "/review", async (p) => { await p.getByRole("radio", { name: "ES" }).click(); await p.waitForTimeout(500); });
  await rshot("table_1440", 1440, 900, "/review", async (p) => { await p.locator("[data-view=table]").click(); await p.waitForTimeout(300); });
  await rshot("all_levels_1440", 1440, 900, "/review", async (p) => { await p.keyboard.press("a"); await p.waitForTimeout(300); });
  // answering: Take (T), the reason, the optional fields; then Ctrl+Enter, the reveal and the result card
  for (const [w, h, calm] of [[1440, 900, false], [960, 800, false], [1440, 900, true]]) {
    const tag = `${w}${calm ? "_calm" : ""}`;
    await rshot(`answer_filled_${tag}`, w, h, "/review?x=" + tag, async (p) => {
      await p.keyboard.press("t"); await p.waitForTimeout(150);
      await p.locator("[data-reason]").click();
      await p.keyboard.type("Second test of the ON high with the King right above; sellers held it at 10:05.");
      const focused = await p.evaluate(() => document.activeElement?.hasAttribute("data-reason"));
      checks[`answer_keys_${tag}`] = { verdict_take: await p.getByRole("radio", { name: /^Take/ }).getAttribute("aria-checked"), reason_focused: focused };
      await p.getByRole("button", { name: /Optional/ }).click(); await p.waitForTimeout(300);
      await p.getByRole("button", { name: "Audit" }).click();
      await p.getByRole("radiogroup", { name: "Confidence 1 to 5" }).getByRole("radio", { name: "4", exact: true }).click();
      await p.locator("[data-answer]").scrollIntoViewIfNeeded();
    }, { reducedMotion: calm ? "reduce" : "no-preference" });
  }
  for (const [w, h, calm] of [[1440, 900, false], [960, 800, false], [1440, 900, true]]) {
    const tag = `${w}${calm ? "_calm" : ""}`;
    await rshot(`result_${tag}`, w, h, "/review", async (p) => {
      await p.locator("[data-card]").getAttribute("data-card");
      // a card not answered yet in this panel's memory
      for (let k = 0; k < 5 && await p.locator("[data-result]").count(); k++) { await p.keyboard.press("n"); await p.waitForTimeout(600); }
      await p.keyboard.press("s"); await p.waitForTimeout(150); await p.locator("[data-reason]").click();
      await p.keyboard.type("Lunch chop under VWAP; I'd wait for the reclaim.");
      const t0 = Date.now();
      await p.keyboard.press("Control+Enter");
      await p.waitForSelector("[data-result]"); const shown = Date.now() - t0;
      await p.waitForTimeout(calm ? 300 : 4500);
      checks[`result_${tag}`] = { shown_ms: shown, clock: await p.locator("[data-clock]").textContent(), net: await p.locator("[data-net-r]").textContent() };
      await p.locator("[data-result]").scrollIntoViewIfNeeded();
    }, { reducedMotion: calm ? "reduce" : "no-preference" });
  }
};

// ── stage 4, review fixes (owner 2026-11-16): "4b" on a --synthetic panel, "4br" on the real preview round ─────────
const plotBox = (p) => p.locator("[data-plot]").boundingBox();
const ov = (p, fn, arg) => p.evaluate(fn, arg);

async function cutCheckAll(page, round, ids) {
  // §8: before its answer, nothing in a card is after its decision time (bars, levels, zones, touches, maps, Kings)
  const bad = [];
  const late18 = [];
  for (const id of ids) {
    const c = await (await page.request.get(BASE + `/api/review/card?round=${round}&card=${id}`)).json();
    if (c.answer) continue;
    const T = c.t.decision, M = c.t.minute;
    const n = {
      hidden: ["post", "outcome", "stratum", "why"].filter((k) => k in c).length,
      bars: [...c.pre.bars.NQ, ...c.pre.bars.ES].filter((b) => b[0] > M).length,
      levels: [...c.pre.levels.NQ, ...c.pre.levels.ES].flatMap((l) => [...l.segs.map((s) => s[0]), ...l.roles.map((r) => r[0]), ...l.marks.map((m) => m[0])]).filter((t) => t > T).length,
      zones: c.pre.zones.flatMap((z) => z.pts.map((p) => p[0])).filter((t) => t > T).length,
      touches: c.pre.events.filter((e) => e.t > T || (e.own && e.label != null)).length,
      maps: Object.values(c.pre.maps).flat().filter((m) => m.t > M).length,
      kings: Object.values(c.pre.kings).flat().filter((k) => k[0] > T).length,
    };
    if (Object.values(n).some((v) => v)) bad.push(`${id}: ${JSON.stringify(n)}`);
    // the stacked list adds up to the scorer's count (its items, the ones not found, and "other")
    if (c.stack && c.stack.items.length + c.stack.other !== c.stack.count) bad.push(`${id}: stacked ${c.stack.items.length} + other ${c.stack.other} != ${c.stack.count}`);
    if (!c.stack) bad.push(`${id}: no stacked list`);
    const first = c.pre.bars.NQ[0]?.[0];
    if (first == null || first > c.t.chart_start + 5 * 60) late18.push(`${id} ${c.day}: first bar ${first == null ? "none" : new Date((first + c.et_offset_sec) * 1000).toISOString().slice(11, 16)} ET`);
  }
  checks[`cut_all_${round}`] = { cards: ids.length, bad, not_from_1800: late18 };
  if (bad.length) problems.push(`cut_all_${round}: ${bad.length} card(s) show later data: ${bad.slice(0, 5).join("; ")}`);
}

async function fixShots(tag, w, h, opts = {}) {
  // (1) the price axis after a symbol switch: stretch ES, switch to NQ, the candles must be on screen
  await rshot(`fix1_axis_switch_${tag}`, w, h, "/review", async (p) => {
    await p.locator("[data-inst=ES]").click(); await p.waitForTimeout(400);
    const b = await plotBox(p);
    const ax = b.x + b.width - 30, ay = b.y + b.height / 2;
    await p.mouse.move(ax, ay); await p.mouse.down(); await p.mouse.move(ax, ay - 160, { steps: 8 }); await p.mouse.up(); await p.waitForTimeout(200);
    const esAuto = await ov(p, () => window.__cfOverlay.series.priceScale().options().autoScale);
    await p.locator("[data-inst=NQ]").click(); await p.waitForTimeout(500);
    const r = await ov(p, () => { const o = window.__cfOverlay, bt = o.s.barTimes; const d = o.series.data(); const last = d[d.length - 1];
      const ys = d.slice(-60).flatMap((x) => [o.yOf(x.high), o.yOf(x.low)]); const H = o.chart.paneSize().height;
      return { autoScale: o.series.priceScale().options().autoScale, inView: ys.filter((y) => y != null && y >= 0 && y <= H).length, of: ys.length, lastClose: last?.close, bars: bt.length }; });
    checks[`fix1_${tag}`] = { esAutoScaleAfterDrag: esAuto, ...r };
    if (esAuto !== false) problems.push(`fix1_${tag}: dragging the ES axis didn't take it off autoscale (the test didn't stretch it)`);
    if (!r.autoScale || r.inView < r.of) problems.push(`fix1_${tag}: NQ after the switch is not fitted to its candles (${JSON.stringify(r)})`);
  }, opts);
  // (2) one switch: exactly one of Focus / Table, the chart when a card opens
  await rshot(`fix2_view_switch_${tag}`, w, h, "/review", async (p) => {
    const read = () => p.locator("[data-view-switch] [role=radio]").evaluateAll((els) => els.map((e) => `${e.getAttribute("data-view")}:${e.getAttribute("aria-checked")}`));
    const at0 = await read(); await p.locator("[data-view=table]").click(); await p.waitForTimeout(200);
    const at1 = await read(); await p.locator("[data-view=focus]").click(); await p.waitForTimeout(200);
    const at2 = await read();
    checks[`fix2_${tag}`] = { open: at0, table: at1, back: at2, oldToggles: await p.locator("[data-focus-toggle]").count() };
    const one = (a) => a.filter((x) => x.endsWith(":true")).length === 1;
    if (!(one(at0) && one(at1) && one(at2) && at0[0] === "focus:true" && at1[1] === "table:true")) problems.push(`fix2_${tag}: the switch isn't one-of-two (${JSON.stringify(checks[`fix2_${tag}`])})`);
    await p.locator("[data-view=table]").click(); await p.waitForTimeout(200);
  }, opts);
  // (3) the orbs across the chart: none after the clock or the decision; then 4× replay frame times
  await rshot(`fix3_orbs_${tag}`, w, h, "/review", async (p) => {
    const r = await ov(p, () => { const o = window.__cfOverlay; const keys = o.orbShapes.map((s) => s.hit().key.split(":")); const ts = keys.map((k) => Number(k[3]));
      return { orbs: ts.length, latest: Math.max(...ts), clock: o.s.clock, limit: o.s.limit, nodeLines: 0 }; });
    checks[`fix3_${tag}`] = r;
    if (!r.orbs) problems.push(`fix3_${tag}: no orbs drawn`);
    if (r.latest > r.clock || r.latest + 60 > r.limit + 60) problems.push(`fix3_${tag}: an orb after the clock / decision (${JSON.stringify(r)})`);
    // round 3: every orb a vertical oval; the weakest nodes nearly fill their minute (rows read as near-continuous lines)
    const sh = await ov(p, () => { const o = window.__cfOverlay, bt = o.s.barTimes; const sp = o.xOf(bt[bt.length - 1] + 60) - o.xOf(bt[bt.length - 1]);
      const s = o.orbShapes; const weak = s.filter((x) => x.ry <= Math.min(...s.map((y) => y.ry)) + 0.01);
      return { n: s.length, notTall: s.filter((x) => !(x.ry > x.rx)).length, spacing: +sp.toFixed(2), weakWidth: weak.length ? +(2 * weak[0].rx).toFixed(2) : null,
        biggest: Math.max(...s.map((x) => x.ry)), smallest: Math.min(...s.map((x) => x.ry)) }; });
    checks[`fix3_${tag}`].ovals = sh;
    if (sh.notTall) problems.push(`fix3_${tag}: ${sh.notTall} orb(s) not taller than wide`);
    if (sh.weakWidth != null && sh.weakWidth < 0.5 * sh.spacing) problems.push(`fix3_${tag}: the weakest orbs leave wide gaps (${sh.weakWidth} px wide at ${sh.spacing} px a bar)`);
  }, opts);
  await rshot(`fix3_replay4x_${tag}`, w, h, "/review", async (p) => {
    await p.keyboard.press("Home"); await p.waitForTimeout(300);
    await p.getByRole("radio", { name: "4×" }).click();
    const before = await ov(p, () => window.__cfOverlay.orbRedraws);
    await p.evaluate(() => { window.__frames = []; let last = performance.now(); const tick = (t) => { window.__frames.push(t - last); last = t; if (window.__frames.length < 600) requestAnimationFrame(tick); }; requestAnimationFrame(tick); });
    await p.keyboard.press(" ");
    // move the pointer over the chart while it plays (hover redraws too)
    const b = await plotBox(p);
    for (let i = 0; i < 40; i++) { await p.mouse.move(b.x + 40 + i * 12, b.y + b.height * (0.3 + 0.01 * i)); await p.waitForTimeout(60); }
    await p.waitForTimeout(1500);
    const f = await p.evaluate(() => { const a = window.__frames.slice(5).sort((x, y) => x - y); const n = a.length; return { frames: n, median: a[Math.floor(n / 2)], p95: a[Math.floor(n * 0.95)], over20ms: a.filter((x) => x > 20).length, max: a[n - 1] }; });
    const after = await ov(p, () => window.__cfOverlay.orbRedraws);
    checks[`fix3_fps_${tag}`] = { ...f, orbRedrawsWhilePlaying: after - before };
    if (f.p95 > 20) problems.push(`fix3_fps_${tag}: 95th-percentile frame ${f.p95.toFixed(1)} ms at 4× (60 fps = 16.7 ms)`);
  }, opts);
  // (5) the chart fills down to the next row; the replay bar never under the dock
  await rshot(`fix5_fill_${tag}`, w, h, "/review", async (p) => {
    const r = await p.evaluate(() => { const b = (s) => document.querySelector(s)?.getBoundingClientRect(); const c = b("[data-chart-card]"), a = b("aside[aria-label='At this point']"), rad = document.querySelector("[data-radial]")?.closest("section")?.getBoundingClientRect();
      return { chartBottom: Math.round(c.bottom), asideBottom: Math.round(a.bottom), nextRowTop: rad ? Math.round(rad.top) : null, sideBySide: Math.abs(c.top - a.top) < 2 }; });
    checks[`fix5_${tag}`] = r;
    if (r.sideBySide && Math.abs(r.chartBottom - r.asideBottom) > 2) problems.push(`fix5_${tag}: a gap under the chart (${JSON.stringify(r)})`);
  }, opts);
  // (6) hover: the objects under the pointer in one card; nothing right of the last bar; never over the axis; no OHLC
  await rshot(`fix6_hover_${tag}`, w, h, "/review", async (p) => {
    const b = await plotBox(p);
    const target = await ov(p, () => { const o = window.__cfOverlay; const right = o.lastBarRight();
      const H = o.chart.paneSize().height;
      const kindOf = (sh) => (typeof sh.hit === "function" ? sh.hit() : sh.hit).kind;
      for (const sh of [...(o.shapes ?? [])].reverse()) if (sh.shape.kind === "h" && kindOf(sh) === "level") {
        const x = Math.min(sh.shape.x1, right) - 2; if (x > Math.max(10, sh.shape.x0) && sh.shape.y > 10 && sh.shape.y < H - 10) return { x, y: sh.shape.y }; }
      const s0 = o.orbShapes[o.orbShapes.length - 1]; return s0 ? { x: s0.x, y: s0.y } : null; });
    if (!target) { problems.push(`fix6_${tag}: nothing to hover`); return; }
    await p.mouse.move(b.x + target.x, b.y + target.y); await p.waitForTimeout(300);
    const tip = await p.evaluate(() => { const t = document.querySelector("[data-tip]"); if (!t) return null; const r = t.getBoundingClientRect();
      return { items: [...t.querySelectorAll("[data-tip-item]")].map((e) => e.getAttribute("data-tip-item")), text: t.textContent, right: Math.round(r.right) }; });
    const axisLeft = await ov(p, () => { const o = window.__cfOverlay; return o.chart.paneSize().width; });
    checks[`fix6_${tag}`] = { tip, axisLeft: Math.round(b.x + axisLeft) };
    if (!tip) problems.push(`fix6_${tag}: no card over a drawn object`);
    else {
      if (/O H L C/.test(tip.text)) problems.push(`fix6_${tag}: the OHLC box is back`);
      if (tip.items.some((k) => !["level", "orb", "touch"].includes(k))) problems.push(`fix6_${tag}: the card shows ${tip.items.join(", ")} (levels, orbs and touches only)`);
      if (tip.right > b.x + axisLeft) problems.push(`fix6_${tag}: the hover card covers the price axis (${tip.right} > ${b.x + axisLeft})`);
    }
  }, opts);
  await rshot(`fix6_gap_${tag}`, w, h, "/review", async (p) => {
    const b = await plotBox(p);
    const g = await ov(p, () => { const o = window.__cfOverlay; return { right: o.lastBarRight(), pane: o.chart.paneSize().width }; });
    const x = (g.right + g.pane) / 2;
    for (const fy of [0.2, 0.4, 0.5, 0.6, 0.8]) { await p.mouse.move(b.x + x, b.y + b.height * fy); await p.waitForTimeout(120);
      if (await p.locator("[data-tip]").count()) { problems.push(`fix6_gap_${tag}: a card in the empty room right of the last bar`); break; } }
    checks[`fix6_gap_${tag}`] = { pointerX: Math.round(x), lastBarRight: Math.round(g.right), pane: g.pane };
  }, opts);
  // (8) End: to the decision before the answer
  await rshot(`fix8_end_${tag}`, w, h, "/review", async (p) => {
    const c0 = await p.locator("[data-clock]").textContent();
    await p.keyboard.press("Home"); await p.waitForTimeout(200); const c1 = await p.locator("[data-clock]").textContent();
    await p.keyboard.press("End"); await p.waitForTimeout(300); const c2 = await p.locator("[data-clock]").textContent();
    await p.keyboard.press("Home"); await p.waitForTimeout(200); await p.getByRole("button", { name: "To the decision" }).click(); await p.waitForTimeout(300);
    const c3 = await p.locator("[data-clock]").textContent();
    checks[`fix8_${tag}`] = { open: c0, home: c1, endKey: c2, endButton: c3 };
    if (c2 !== c0 || c3 !== c0 || c1 === c0) problems.push(`fix8_${tag}: End doesn't go to the decision (${JSON.stringify(checks[`fix8_${tag}`])})`);
  }, opts);
  // (7) history from 18:00: the slider's start and the first candle
  await rshot(`fix7_history_${tag}`, w, h, "/review", async (p) => {
    const s = await p.locator("[data-scrub]").evaluate((e) => ({ min: Number(e.min), max: Number(e.max) }));
    await p.locator("[data-scrub]").evaluate((e) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; set.call(e, e.min); e.dispatchEvent(new Event("input", { bubbles: true })); });
    await p.waitForTimeout(400);
    checks[`fix7_${tag}`] = { sliderMin: s.min, clockAtMin: await p.locator("[data-clock]").textContent(), bars: await ov(p, () => window.__cfOverlay.s.barTimes.length) };
  }, opts);
}

S["4b"] = async () => {
  for (const [w, h] of [[1440, 900], [960, 800]]) await fixShots(`syn_${w}`, w, h);
  // (8) after the answer: End goes to the end of the revealed path
  await rshot("fix8_after_answer_1440", 1440, 900, "/review", async (p) => {
    for (let k = 0; k < 5 && await p.locator("[data-result]").count(); k++) { await p.keyboard.press("n"); await p.waitForTimeout(500); }
    await p.keyboard.press("t"); await p.locator("[data-reason]").click(); await p.keyboard.type("Testing the reveal.");
    await p.keyboard.press("Control+Enter"); await p.waitForSelector("[data-result]"); await p.waitForTimeout(600);
    await p.locator("[data-replay]").click({ position: { x: 4, y: 4 } }).catch(() => {});
    await p.keyboard.press("Home"); await p.waitForTimeout(200);
    await p.getByRole("button", { name: "To the end of the revealed path" }).click(); await p.waitForTimeout(300);
    const c = await p.locator("[data-clock]").textContent();
    const lim = await ov(p, () => { const o = window.__cfOverlay; return { clock: o.s.clock, limit: o.s.limit }; });
    checks.fix8_after_answer = { clock: c, ...lim };
    if (lim.clock + 60 < lim.limit - 60) problems.push(`fix8_after_answer: End stops before the revealed end (${JSON.stringify(lim)})`);
  });
  const { ctx, page } = await open("cut_all", 1440, 900, "/review");
  await cutCheckAll(page, "synthetic", ["c01", "c02", "c03", "c04", "c05"]);
  await ctx.close();
};

S["4br"] = async () => {
  for (const [w, h] of [[1440, 900], [1280, 800], [960, 800]]) await fixShots(`real_${w}`, w, h);
  // (4) the card strip: arrows, faded edges, the current card in view, nothing pushed off screen
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    await rshot(`fix4_strip_${w}`, w, h, "/review", async (p) => {
      const st0 = await p.evaluate(() => { const r = document.querySelector("[data-strip-row]"); return { scroll: r.scrollWidth > r.clientWidth, mask: getComputedStyle(r).maskImage || getComputedStyle(r).webkitMaskImage }; });
      await p.locator("[data-strip-right]").click(); await p.waitForTimeout(500);
      await p.locator("[data-strip-right]").click(); await p.waitForTimeout(500);
      await p.locator("[role=listitem]", { hasText: /^50/ }).click(); await p.waitForTimeout(900);
      const r = await p.evaluate(() => { const row = document.querySelector("[data-strip-row]").getBoundingClientRect(); const cur = document.querySelector("[data-strip-row] [aria-current=true]").getBoundingClientRect();
        const nav = [...document.querySelectorAll("[data-strip] button")].map((b) => b.getBoundingClientRect().right);
        return { current: document.querySelector("[data-strip-row] [aria-current=true]").textContent, inView: cur.left >= row.left && cur.right <= row.right,
          navRight: Math.round(Math.max(...nav)), vw: window.innerWidth, pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 }; });
      checks[`fix4_${w}`] = { ...st0, ...r };
      if (!r.inView || r.navRight > r.vw || r.pageOverflow) problems.push(`fix4_${w}: the strip (${JSON.stringify(r)})`);
      await p.locator("[data-strip-row]").scrollIntoViewIfNeeded();
    });
  }
  const { ctx, page } = await open("cut_all", 1440, 900, "/review");
  const idx = await (await page.request.get(BASE + "/api/review")).json();
  await cutCheckAll(page, idx.round.id, idx.round.cards.map((c) => c.id));
  await ctx.close();
};

// ── stage 4, review round 3 (owner 2026-11-16): the stacked list, the replay bar back at the bottom, orb hover ──────
S["4r3"] = async () => {
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    await rshot(`r3_replay_bottom_${w}`, w, h, "/review", async (p) => {
      const r = await p.evaluate(() => { const plot = document.querySelector("[data-plot]").getBoundingClientRect(), rb = document.querySelector("[data-replay]").getBoundingClientRect();
        return { replayTop: Math.round(rb.top), plotBottom: Math.round(plot.bottom) }; });
      checks[`r3_replay_${w}`] = r;
      if (r.replayTop < r.plotBottom - 1) problems.push(`r3_replay_${w}: the replay bar isn't under the chart (${JSON.stringify(r)})`);
    });
    await rshot(`r3_stack_${w}`, w, h, "/review", async (p) => {
      await p.locator("[data-stack]").scrollIntoViewIfNeeded(); await p.waitForTimeout(300);
      const r = await p.evaluate(() => ({ rows: [...document.querySelectorAll("[data-stack-item]")].map((e) => e.textContent), head: document.querySelector("[data-stack]").textContent.slice(0, 60) }));
      checks[`r3_stack_${w}`] = r;
      if (!r.rows.length) problems.push(`r3_stack_${w}: no stacked confluences listed`);
    });
    await rshot(`r3_orb_hover_${w}`, w, h, "/review", async (p) => {
      const b = await p.locator("[data-plot]").boundingBox();
      const o = await p.evaluate(() => { const s = window.__cfOverlay.orbShapes; const k = s.filter((x) => x.hit().title.includes("King")); const t = (k.length ? k : s)[Math.floor((k.length ? k : s).length * 0.6)]; return t ? { x: t.x, y: t.y } : null; });
      if (!o) { problems.push(`r3_orb_hover_${w}: no orb`); return; }
      await p.mouse.move(b.x + o.x, b.y + o.y); await p.waitForTimeout(300);
      const kinds = await p.locator("[data-tip-item]").evaluateAll((e) => e.map((x) => x.getAttribute("data-tip-item")));
      checks[`r3_orb_hover_${w}`] = { kinds };
      if (!kinds.includes("orb") || kinds.some((k) => !["level", "orb", "touch"].includes(k))) problems.push(`r3_orb_hover_${w}: ${kinds.join(", ")}`);
    });
  }
  await rshot("r3_shortcuts_1440", 1440, 900, "/review", async (p) => { await p.keyboard.press("?"); await p.waitForTimeout(500); await p.getByPlaceholder("Search shortcuts").fill("review"); await p.waitForTimeout(300); });
};

// ── stage 4, review round 4 (owner 2026-11-16) ──────────────────────────────────────────────────────────────────────
S["4r4"] = async () => {
  for (const [w, h] of [[1440, 900], [960, 800]]) {
    // the replay bar: buttons, speed, track, thumb and the clock on one centre line
    await rshot(`r4_replay_${w}`, w, h, "/review", async (p) => {
      await p.locator("[data-replay]").scrollIntoViewIfNeeded(); await p.waitForTimeout(200);
      const r = await p.evaluate(() => { const c = (el) => { const b = el.getBoundingClientRect(); return +(b.top + b.height / 2).toFixed(1); };
        const bar = document.querySelector("[data-replay]");
        return { buttons: [...bar.querySelectorAll("button")].map(c), thumb: c(bar.querySelector("[data-thumb]")), track: c(bar.querySelector("[data-scrub-wrap] > div")),
          clock: c(bar.querySelector("[data-clock-block]")), bar: c(bar) }; });
      const all = [...r.buttons, r.thumb, r.track, r.clock];
      const spread = Math.max(...all) - Math.min(...all);
      checks[`r4_replay_${w}`] = { ...r, spread: +spread.toFixed(1) };
      if (spread > 1.5) problems.push(`r4_replay_${w}: the replay bar's parts are ${spread.toFixed(1)} px off one centre line`);
    });
    // hover cards stay inside the pane near each corner (a long role timeline is shortened, "+n earlier")
    await rshot(`r4_hover_corners_${w}`, w, h, "/review", async (p) => {
      const b = await p.locator("[data-plot]").boundingBox();
      const pane = await p.evaluate(() => window.__cfOverlay.chart.paneSize());
      const res = [];
      for (const [fx, fy] of [[0.1, 0.1], [0.85, 0.1], [0.1, 0.9], [0.85, 0.9], [0.5, 0.5]]) {
        const t = await p.evaluate(([fx, fy]) => { const o = window.__cfOverlay, P = o.chart.paneSize(), right = o.lastBarRight();
          let best = null, bd = 1e9;
          for (const sh of o.shapes) { if (sh.shape.kind !== "h") continue; const x = Math.max(sh.shape.x0 + 1, Math.min(sh.shape.x1 - 1, P.width * fx)); if (x > right - 2 || x < 4) continue;
            const d = Math.hypot(x - P.width * fx, sh.shape.y - P.height * fy); if (d < bd && sh.shape.y > 4 && sh.shape.y < P.height - 4) { bd = d; best = { x, y: sh.shape.y }; } }
          return best; }, [fx, fy]);
        if (!t) continue;
        await p.mouse.move(b.x + t.x, b.y + t.y); await p.waitForTimeout(250);
        const tip = await p.evaluate(() => { const e = document.querySelector("[data-tip]"); if (!e) return null; const r = e.getBoundingClientRect(), pr = document.querySelector("[data-plot]").getBoundingClientRect();
          return { l: r.left - pr.left, t: r.top - pr.top, r: r.right - pr.left, b: r.bottom - pr.top, earlier: !!e.querySelector("[data-earlier]") }; });
        res.push({ at: [fx, fy], tip });
        if (tip && (tip.l < 0 || tip.t < 0 || tip.r > pane.width + 0.5 || tip.b > pane.height + 0.5)) problems.push(`r4_hover_corners_${w}: a card leaves the pane at ${fx},${fy} (${JSON.stringify(tip)})`);
      }
      checks[`r4_hover_corners_${w}`] = { pane, res };
    });
    // at most 5 orbs per ticker per minute, still vertical ovals; no crowns (they are gone from the code)
    await rshot(`r4_orbs_${w}`, w, h, "/review", async (p) => {
      const r = await p.evaluate(() => { const per = {}; for (const s of window.__cfOverlay.orbShapes) { const k = s.hit().key.split(":"); const id = `${k[1]}:${k[3]}`; per[id] = (per[id] ?? 0) + 1; }
        const v = Object.values(per); return { minutes: v.length, most: Math.max(...v), notTall: window.__cfOverlay.orbShapes.filter((x) => !(x.ry > x.rx)).length }; });
      checks[`r4_orbs_${w}`] = r;
      if (r.most > 5 || r.notTall) problems.push(`r4_orbs_${w}: ${JSON.stringify(r)}`);
    });
    await rshot(`r4_stacked_${w}`, w, h, "/review", async (p) => {
      await p.locator("[data-stack]").scrollIntoViewIfNeeded(); await p.waitForTimeout(250);
      checks[`r4_stacked_${w}`] = await p.evaluate(() => ({ rows: [...document.querySelectorAll("[data-stack-item]")].map((e) => e.textContent), head: document.querySelector("[data-stack]").firstElementChild.textContent }));
    });
  }
  await rshot("r4_table_timeline_1440", 1440, 900, "/review", async (p) => { await p.locator("[data-view=table]").click(); await p.waitForTimeout(300); });
  // a long role timeline in a short pane: the card is shortened ("+n earlier") and stays whole inside the pane
  await rshot("r4_hover_long_960", 960, 640, "/review", async (p) => {
    await p.locator("[role=listitem]", { hasText: /^01/ }).click(); await p.waitForTimeout(1500);
    // an answered card focuses its Next card button (which scrolls the page): bring the chart back into view
    await p.evaluate(() => { document.activeElement?.blur?.(); document.querySelector("[data-chart-card]").scrollIntoView({ block: "start" }); });
    await p.waitForTimeout(400);
    const b = await p.locator("[data-plot]").boundingBox();
    const pane = await p.evaluate(() => window.__cfOverlay.chart.paneSize());
    const targets = await p.evaluate(() => { const o = window.__cfOverlay, right = o.lastBarRight(), P = o.chart.paneSize(), seen = new Set(), out = [];
      for (const sh of o.shapes) { if (sh.shape.kind !== "h") continue; const h = typeof sh.hit === "function" ? sh.hit() : sh.hit; if (h.kind !== "level" || seen.has(h.key)) continue;
        const x = Math.min(sh.shape.x1 - 1, right - 3); if (x < sh.shape.x0 || sh.shape.y < 6 || sh.shape.y > P.height - 6) continue; seen.add(h.key); out.push({ x, y: sh.shape.y, key: h.key }); }
      return out; });
    let best = null;
    for (const t of targets) {
      await p.mouse.move(b.x + t.x, b.y + t.y); await p.waitForTimeout(150);
      const tip = await p.evaluate(() => { const e = document.querySelector("[data-tip]"); if (!e) return null; const r = e.getBoundingClientRect(), pr = document.querySelector("[data-plot]").getBoundingClientRect();
        return { l: r.left - pr.left, t: r.top - pr.top, r: r.right - pr.left, b: r.bottom - pr.top, earlier: e.querySelector("[data-earlier]")?.textContent ?? null,
          story: Math.max(0, ...[...e.querySelectorAll("[data-tip-item]")].map((x) => Number(x.getAttribute("data-story")))) }; });
      if (!tip) continue;
      if (tip.l < 0 || tip.t < 0 || tip.r > pane.width + 0.5 || tip.b > pane.height + 0.5) problems.push(`r4_hover_long_960: a card leaves the pane (${JSON.stringify(tip)})`);
      if (!best || tip.story > best.story || (tip.earlier && !best.earlier)) best = { ...tip, at: t };
    }
    checks.r4_hover_long_960 = { pane, hovered: targets.length, longest: best };
    if (best) await p.mouse.move(b.x + best.at.x, b.y + best.at.y); await p.waitForTimeout(300);
  });
};

await (S[stage] ?? (() => { throw new Error(`no shots for stage ${stage}`); }))();
await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ stage, base: BASE, problems, checks }, null, 2));
console.log(problems.length ? `PROBLEMS (${problems.length}):\n` + problems.join("\n") : "all checks passed");
console.log(`screenshots: ${OUT}`);
