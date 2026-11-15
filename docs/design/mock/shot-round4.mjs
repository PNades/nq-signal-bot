// Step-4 round 4 (owner 2026-11-15): node shot-round4.mjs -> docs/design/step4/round4/ (screenshots + checks.json).
// Home: the toast and the key hint in the bottom-right corner, clear of every node (hover card open too); the
// Performance node and key 6 open the Performance page. Dock pages: the toast never under the dock (open, minimized
// corner, minimized centre). Footer: 2/7 of the letters hidden (hidden : visible = 2 : 5) by the bottom edge at 1920/1440/1280/1024/960.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("../step4/round4");
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [], checks = {};
const hit = (a, b) => a && b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

async function open(w, h, query) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) || r.request().url().startsWith("data:") ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${query} ${w}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${query} ${w}: pageerror ${e.message}`));
  await page.mouse.move(w / 2, 100);
  await page.goto(`${BASE}?${query}`);
  return { ctx, page };
}
const nodes = (page) => page.$$eval('button[aria-label*="(key"]', (els) => els.map((e) => { const r = e.getBoundingClientRect(); return { name: e.getAttribute("aria-label"), x: r.x, y: r.y, width: r.width, height: r.height }; }));
const box = (page, sel) => page.locator(sel).first().boundingBox();

// Home: toast + hint vs every node, at three widths; also with the Performance card open
for (const [w, h] of [[1920, 1080], [1440, 900], [1280, 800], [1024, 768], [960, 1000], [1440, 760]]) {
  const { ctx, page } = await open(w, h, "view=home&intro=1");
  await page.waitForTimeout(5200);
  // hover Performance (its card opens), then press 3 (Accounts): the toast appears while the card is open
  await page.locator('button[aria-label^="Performance (key"]').hover();
  await page.waitForTimeout(400);
  await page.keyboard.press("3");
  await page.waitForTimeout(450);
  const msgs = await box(page, "[data-home-messages]");
  const ns = await nodes(page);
  const over = ns.filter((n) => hit(n, msgs)).map((n) => n.name);
  checks[`home_${w}x${h}`] = { messages: msgs && { x: Math.round(msgs.x), y: Math.round(msgs.y), w: Math.round(msgs.width), h: Math.round(msgs.height) }, overlaps: over };
  if (over.length) problems.push(`home ${w}x${h}: messages overlap ${over.join(", ")}`);
  await page.screenshot({ path: path.join(OUT, `home_toast_${w}x${h}.png`) });
  // and with the pointer on the Performance node while another node's key is pressed: its card closes
  checks[`home_${w}x${h}`].performanceCardClosed = (await page.locator('button[aria-label^="Performance (key"]').boundingBox()).height < 60;
  await ctx.close();
}
// Home: the Performance node and key 6 open the Performance page; another node still shows its message
{
  const { ctx, page } = await open(1440, 900, "view=home");
  await page.waitForTimeout(5200);
  await page.locator('button[aria-label^="Gameplan (key"]').click();
  await page.waitForTimeout(300);
  const msg = await page.locator('[data-home-messages] [role="status"]').textContent().catch(() => null);
  await page.locator('button[aria-label^="Performance (key"]').click();
  await page.waitForTimeout(1200);
  const t1 = await page.title();
  await page.goto(`${BASE}?view=home`);
  await page.waitForTimeout(1500);
  await page.keyboard.press("6");
  await page.waitForTimeout(1200);
  const t2 = await page.title();
  checks.homeOpen = { gameplanMessage: msg, clickPerformance: t1, key6: t2 };
  if (t1 !== "CreamFlow · Performance" || t2 !== "CreamFlow · Performance") problems.push(`home: Performance didn't open (${t1} / ${t2})`);
  await ctx.close();
}
// dock pages: the Export CSV toast vs the dock, open / minimized corner / minimized centre, at 1440 and 960
for (const [w, h] of [[1440, 900], [960, 1000]]) for (const v of ["open", "corner", "centre"]) {
  const { ctx, page } = await open(w, h, `view=performance${v === "centre" ? "&dockmin=centre" : ""}`);
  await page.waitForTimeout(2400);
  if (v !== "open") { await page.locator('[aria-label="Minimize the page dock"]').click(); await page.waitForTimeout(900); }
  await page.locator("main button", { hasText: "Export CSV" }).click();
  await page.waitForTimeout(300);
  const t = await box(page, '[role="status"]'), d = await box(page, "[data-dock]");
  checks[`toast_${v}_${w}`] = { toast: t && [Math.round(t.x), Math.round(t.y), Math.round(t.width), Math.round(t.height)], dock: d && [Math.round(d.x), Math.round(d.y), Math.round(d.width), Math.round(d.height)], gap: t && d ? Math.round(d.y - (t.y + t.height)) : null };
  if (hit(t, d)) problems.push(`toast under the dock: ${v} ${w}`);
  await page.screenshot({ path: path.join(OUT, `toast_${v}_${w}.png`) });
  await ctx.close();
}
// footer: a third hidden
for (const [w, h] of [[1920, 1080], [1440, 900], [1280, 800], [1024, 768], [960, 1000]]) {
  const { ctx, page } = await open(w, h, "view=performance");
  await page.waitForTimeout(2600);
  await page.evaluate(() => { const m = document.querySelector("main"); m.scrollTop = m.scrollHeight; });
  await page.waitForTimeout(800);
  const m = await page.evaluate(() => {
    const main = document.querySelector("main"), f = document.querySelector(".hwf-root"), cv = f.querySelector("canvas");
    const fr = f.getBoundingClientRect(), mr = main.getBoundingClientRect(), s = cv.width / fr.width;
    const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
    let minY = cv.height;
    for (let y = 0; y < cv.height && minY === cv.height; y++) for (let x = 0; x < cv.width; x++) { const i = (y * cv.width + x) * 4; if (d[i] > 150 && d[i + 1] > 120) { minY = y; break; } }
    const cap = +f.dataset.hwfCap, visible = fr.height - minY / s;
    return { cap: Math.round(cap), visible: Math.round(visible), hiddenShare: +(1 - visible / cap).toFixed(3), crop: +f.dataset.hwfCrop, belowFooter: Math.round(mr.bottom - fr.bottom) };
  });
  checks[`footer_${w}`] = m;
  if (Math.abs(m.hiddenShare - 2 / 7) > 0.03 || m.belowFooter !== 0) problems.push(`footer ${w}: ${JSON.stringify(m)}`);
  await page.screenshot({ path: path.join(OUT, `footer_${w}.png`) });
  await ctx.close();
}
await browser.close();
fs.writeFileSync(path.join(OUT, "checks.json"), JSON.stringify({ checks, problems }, null, 2));
for (const [k, v] of Object.entries(checks)) console.log(k, JSON.stringify(v));
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings, all checks passed");
