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
  page.on("console", (m) => { if ((m.type() === "error" || m.type() === "warning") && !(aborts && m.text().includes("net::ERR_FAILED")) && !(expected428 && m.text().includes("428"))) problems.push(`${name}: console ${m.type()}: ${m.text()}`); });
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

await (S[stage] ?? (() => { throw new Error(`no shots for stage ${stage}`); }))();
await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ stage, base: BASE, problems, checks }, null, 2));
console.log(problems.length ? `PROBLEMS (${problems.length}):\n` + problems.join("\n") : "all checks passed");
console.log(`screenshots: ${OUT}`);
