// Screenshot a local design page with Playwright: node shoot.mjs <page.html> <out-prefix> [widths...]
// Local files only; any request that isn't file:// is blocked and reported, so a mock can't load remote code.
import { chromium } from "playwright";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [, , page = "specimen.html", out = "../type_specimen", ...w] = process.argv;
const widths = w.length ? w.map(Number) : [1440, 1024];
const browser = await chromium.launch();
const blocked = [];
for (const width of widths) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 1, colorScheme: "dark" });
  const p = await ctx.newPage();
  await p.route("**/*", (r) => (r.request().url().startsWith("file:") ? r.continue() : (blocked.push(r.request().url()), r.abort())));
  await p.goto(pathToFileURL(path.resolve(page)).href);
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(300);
  const file = `${out}_${width}.png`;
  await p.screenshot({ path: file, fullPage: true });
  console.log("wrote", file);
  await ctx.close();
}
await browser.close();
if (blocked.length) { console.log("BLOCKED remote requests:", blocked); process.exitCode = 2; }
