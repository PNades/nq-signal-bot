// Frame-by-frame horizontal position of the page content during a page switch (used by stage 2i; runnable alone).
import { chromium } from "playwright";
export async function measureSwitch(page, act, ms = 900) {
  const pending = page.evaluate((ms) => new Promise((res) => {
    const out = []; const t0 = performance.now();
    const ch = new MessageChannel(); const after = () => ch.port2.postMessage(0);
    ch.port1.onmessage = () => {
      const m = document.getElementById("cf-main"); const h = m.querySelector("h1");
      const hr = h?.getBoundingClientRect();
      const home = m.querySelector("[data-home]")?.getBoundingClientRect();
      out.push({ t: Math.round(performance.now() - t0), mainW: m.clientWidth, scroll: m.scrollHeight > m.clientHeight, h1: h ? h.textContent.slice(0, 18) : null, h1Left: hr ? +hr.left.toFixed(2) : null,
        home: home ? `${home.left.toFixed(2)}/${home.width.toFixed(2)}` : null });
      if (performance.now() - t0 < ms) requestAnimationFrame(after); else res(out);
    };
    requestAnimationFrame(after);
  }), ms);
  await act();
  const f = await pending;
  // the new page's heading: its left edge must never jump (any change of more than 0.5 px between frames counts)
  let jumps = 0, maxJump = 0;
  for (let i = 1; i < f.length; i++) if (f[i].h1Left != null && f[i - 1].h1Left != null && f[i].h1 === f[i - 1].h1) {
    const d = Math.abs(f[i].h1Left - f[i - 1].h1Left); if (d > 0.5) jumps++; maxJump = Math.max(maxJump, d);
  }
  // Home's scene: its left edge and width must not change while it is shown (on the way in or out)
  for (let i = 1; i < f.length; i++) if (f[i].home && f[i - 1].home && f[i].home !== f[i - 1].home) {
    const [l0, w0] = f[i - 1].home.split("/").map(Number), [l1, w1] = f[i].home.split("/").map(Number);
    const d = Math.max(Math.abs(l1 - l0), Math.abs(w1 - w0)); if (d > 0.5) jumps++; maxJump = Math.max(maxJump, d);
  }
  return { frames: f, homeBoxes: [...new Set(f.map((x) => x.home).filter(Boolean))], mainWidths: [...new Set(f.map((x) => x.mainW))], h1Lefts: [...new Set(f.filter((x) => x.h1Left != null).map((x) => `${x.h1}@${x.h1Left}`))], jumps, maxJumpPx: +maxJump.toFixed(2) };
}
if (process.argv[1]?.endsWith("page_probe.mjs")) {
  // real (classic, 17-px) scrollbars as on the owner's Windows Chrome: Playwright hides them by default
  const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"], ignoreDefaultArgs: ["--hide-scrollbars"] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const page = await ctx.newPage(); await page.goto("http://127.0.0.1:8733/"); await page.evaluate(() => sessionStorage.setItem("creamflow.splashShown", "1"));
  await page.reload(); await page.waitForTimeout(5000); await page.mouse.move(5, 300);
  const r = await measureSwitch(page, () => page.keyboard.press("3"));
  console.log(JSON.stringify({ mainWidths: r.mainWidths, h1Lefts: r.h1Lefts, jumps: r.jumps, maxJumpPx: r.maxJumpPx }));
  console.log(r.frames.slice(0, 30).map((x) => `${x.t} w${x.mainW} s${x.scroll ? 1 : 0} ${x.h1}@${x.h1Left}`).join("\n"));
  await browser.close();
}
