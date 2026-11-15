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
  page.on("console", (m) => { if ((m.type() === "error" || m.type() === "warning") && !(aborts && m.text().includes("net::ERR_FAILED"))) problems.push(`${name}: console ${m.type()}: ${m.text()}`); });
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

await (S[stage] ?? (() => { throw new Error(`no shots for stage ${stage}`); }))();
await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ stage, base: BASE, problems, checks }, null, 2));
console.log(problems.length ? `PROBLEMS (${problems.length}):\n` + problems.join("\n") : "all checks passed");
console.log(`screenshots: ${OUT}`);
