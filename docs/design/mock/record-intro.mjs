// Home's opening sequence (owner 2026-11-15): node record-intro.mjs -> docs/design/video/home_intro.webm (gitignored).
// 1) first open: the wordmark forms alone, then the six nodes arrive clockwise from Live, each line drawing out to the
// word; 2) Performance and back: the formed scene with a short fade; 3) &intro=1 replay skipped by a click.
// Only 127.0.0.1 is reachable; console errors and warnings are reported.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const VID = path.resolve("../video");
fs.mkdirSync(VID, { recursive: true });
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"] });
const problems = [];
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: VID, size: { width: 1440, height: 900 } }, colorScheme: "dark" });
const page = await ctx.newPage();
await page.route("**/*", (r) => { const u = r.request().url(); if (u.startsWith(BASE) || u.startsWith("data:")) return r.continue(); problems.push(`blocked ${u}`); return r.abort(); });
page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") problems.push(`console ${m.type()}: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror ${e.message}`));
await page.mouse.move(1430, 890);
await page.goto(`${BASE}?view=home`);
await page.waitForTimeout(6200);                                   // wordmark, then the nodes and lines, then idle
await page.locator("aside button", { hasText: "Performance" }).click();
await page.waitForTimeout(1600);
await page.locator("aside button", { hasText: "Home" }).click();   // later visit: formed scene, short fade
await page.waitForTimeout(2000);
await page.goto(`${BASE}?view=home&intro=1`);                       // replay, skipped by a click
await page.waitForTimeout(1300);
await page.mouse.click(1300, 820);
await page.waitForTimeout(1800);
const video = page.video();
await ctx.close();
const dest = path.join(VID, "home_intro.webm");
fs.rmSync(dest, { force: true });
fs.renameSync(await video.path(), dest);
console.log("video", dest);
await browser.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no blocked requests, no console errors or warnings");
