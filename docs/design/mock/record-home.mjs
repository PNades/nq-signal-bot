// Records the three Home concepts: node record-home.mjs [pour flow trace]
// Videos -> docs/design/video/ (gitignored), screenshots -> docs/design/. Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const VID = path.resolve("../video");
const SHOTS = path.resolve("..");
fs.mkdirSync(VID, { recursive: true });
const concepts = process.argv.slice(2).length ? process.argv.slice(2) : ["pour", "flow", "trace"];
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"] });
const problems = [];

async function guard(page, tag) {
  await page.route("**/*", (r) => {
    const u = r.request().url();
    if (u.startsWith(BASE) || u.startsWith("data:")) return r.continue();
    problems.push(`${tag}: blocked ${u}`); return r.abort();
  });
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`${tag}: console ${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`${tag}: pageerror ${e.message}`));
}

async function glide(page, from, to, steps = 40, pause = 12) {
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    await page.mouse.move(from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t);
    await page.waitForTimeout(pause);
  }
}

for (const c of concepts) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: VID, size: { width: 1440, height: 900 } }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await guard(page, c);
  await page.mouse.move(1300, 860);
  await page.goto(`${BASE}?view=home&concept=${c}`);
  await page.waitForTimeout(4600);                       // intro: title forms, then the nodes
  await page.screenshot({ path: path.join(SHOTS, `home_${c}_1440.png`) });
  // main area spans x 248..1440, y 80..900; title around 44% of it
  const cy = 80 + 820 * 0.44;
  await glide(page, [430, cy + 40], [1250, cy - 20], 70, 14); // cursor across the title
  await glide(page, [1250, cy - 20], [700, cy + 10], 40, 14);
  await page.waitForTimeout(500);
  const perf = await page.locator('button[aria-label^="Performance (key"]').boundingBox();
  await glide(page, [700, cy + 10], [perf.x + perf.width / 2, perf.y + perf.height / 2], 30, 14);
  await page.waitForTimeout(1300);
  const acc = await page.locator('button[aria-label^="Accounts (key"]').boundingBox();
  await glide(page, [perf.x + 20, perf.y + 20], [acc.x + acc.width / 2, acc.y + acc.height / 2], 40, 12);
  await page.waitForTimeout(1100);
  await page.keyboard.press("6");
  await page.waitForTimeout(1300);
  const video = page.video();
  await ctx.close();
  const dest = path.join(VID, `home_${c}.webm`);
  fs.rmSync(dest, { force: true });
  fs.renameSync(await video.path(), dest);
  console.log("video", dest);

  // Calm (still) and 1024-px screenshots
  for (const [w, h, q, name] of [[1440, 900, "&calm=1", `home_${c}_calm_1440.png`], [1024, 768, "", `home_${c}_1024.png`]]) {
    const cx2 = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
    const p2 = await cx2.newPage();
    await guard(p2, `${c} ${name}`);
    await p2.goto(`${BASE}?view=home&concept=${c}${q}`);
    await p2.waitForTimeout(q ? 1200 : 4600);
    await p2.screenshot({ path: path.join(SHOTS, name) });
    await cx2.close();
  }
}
await browser.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors");
