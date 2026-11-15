// Home screenshots (owner 2026-11-14): widths 1440, 1280, 960; a hover shot; one shot per theme; a top-bar
// no-wrap check at every width. Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("..");
const SIZES = [[1920, 1080], [1440, 900], [1280, 800], [960, 1000]];
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [];

async function open(w, h, query) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  await page.mouse.move(w - 5, h - 5);
  await page.goto(`${BASE}?view=home&concept=flow${query}`);
  await page.waitForTimeout(4200);
  return { ctx, page };
}

async function topBarCheck(page, w) {
  const r = await page.evaluate(() => {
    const header = document.querySelector("header");
    const flat = [...header.querySelectorAll("button")].find((b) => b.textContent.includes("FLAT ALL"));
    const fr = flat.getBoundingClientRect(), hr = header.getBoundingClientRect();
    const lines = Math.round(fr.height / parseFloat(getComputedStyle(flat).lineHeight || "20"));
    const tall = [...header.querySelectorAll("*")].filter((el) => el.getBoundingClientRect().height > hr.height).map((el) => el.textContent.trim().slice(0, 30));
    return { flatH: fr.height, flatRight: fr.right, headerRight: hr.right, overflow: header.scrollWidth > header.clientWidth, tall };
  });
  const ok = r.flatH <= 40 && r.flatRight <= r.headerRight && !r.overflow && r.tall.length === 0;
  console.log(`top bar @${w}: ${ok ? "OK" : "PROBLEM"} (FLAT ALL height ${r.flatH}px, right ${Math.round(r.flatRight)}/${Math.round(r.headerRight)}, overflow ${r.overflow})`);
  if (!ok) problems.push(`top bar @${w}: ${JSON.stringify(r)}`);
}

for (const [w, h] of SIZES) {
  const { ctx, page } = await open(w, h, "");
  await topBarCheck(page, w);
  await page.screenshot({ path: path.join(OUT, `home_flow_${w}.png`) });
  if (w === 1440 || w === 960) {
    const acc = await page.locator('button[aria-label^="Accounts (key"]').boundingBox();
    await page.mouse.move(acc.x + acc.width / 2, acc.y + acc.height / 2);
    await page.waitForTimeout(700);
    await page.screenshot({ path: path.join(OUT, `home_flow_hover_${w}.png`) });
  }
  await ctx.close();
}
for (const t of []) { // theme picked (Cocoa Amber, owner 2026-11-14): no per-theme shots
  const { ctx, page } = await open(1440, 900, `&theme=${t}`);
  await page.screenshot({ path: path.join(OUT, `home_flow_theme_${t}_1440.png`) });
  await ctx.close();
}
await browser.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors, top bar never wraps");
