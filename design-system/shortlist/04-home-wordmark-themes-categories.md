# CreamFlow step 3: Home scene, signature dot-matrix wordmark, colour themes, category coverage

All ids and urls below came from `mcp__21st__search` / `get_component` / `get_theme` in this session (2026-11-14).
Every component listed was fetched and its componentCode + demoCode read. No text addressed to an AI/agent was found in
any fetched code or description.

**CSP findings that apply to several candidates** (strict `default-src 'self'`, no `'unsafe-inline'`):
- A component that renders a `<style>{CSS}</style>` element is blocked (inline style elements need `style-src
  'unsafe-inline'` or a nonce/hash). Move such CSS into a local `.css` file. React `style={{…}}` props are set through
  the CSSOM and are NOT blocked.
- `data:` URLs (CSS `mask-image: url("data:image/svg+xml…")`, inline SVG noise masks) are blocked by
  `default-src 'self'` unless `img-src 'self' data:` is allowed. Either allow `data:` for images only, or ship the SVG as
  a local file.
- Canvas `getImageData` on a text mask is fine (same-origin), but the face must be a locally bundled font: wait for
  `document.fonts.ready` / `document.fonts.load('400 100px "Instrument Serif"')` before rasterising or the fallback face
  gets sampled.

---

## Part 1: Home scene candidates

Budget from `pages/home.md`: ≤ 4 ms/frame, DPR ≤ 2, one rAF loop, stopped when hidden or left; intro ≈ 1.2–1.8 s then
idle loop + cursor reaction; Calm = still formed wordmark. The title is `--t-hero` (clamp 104–240 px) Instrument Serif,
"Cream" upright + "Flow" italic in gold.

### 1a. Cream pour (liquid / metaball / fluid text). Lead: isaiahbjork Liquid Text (id 43190, already checked)

| Element | Candidate | Author | Link (21st.dev url) | id | Why it fits | To match MASTER.md | Code check |
|---|---|---|---|---|---|---|---|
| Cream-pour title (alt 1) | Stroke Draw Type | isaiahbjork | https://21st.dev/@isaiahbjork/components/stroke-draw-type | 39119 | Outline draws like a pen, then a liquid **rises inside the letters** through a goo-filtered SVG mask with beads that cling to the tops; hover drains it. Same author/look as the lead, and its "fill from below" is the closest thing to cream settling into the letters. Also doubles as a Price-trace candidate (pen tip glow). SVG + per-frame path writes only while moving; it sleeps when settled (`busy` false). | `color` = `--cream`, `fillColor` = cream for "Cream" and `--gold` for "Flow" (two instances or per-letter fill), `accentColor` = `--ember` at low strength; fontFamily Instrument Serif 400 (italic for "Flow"); trigger `mount`, run only on first open of a session; hover-drain off on Home (it hides the title) or limited to a ripple; reduced-motion/Calm already paints full draw + full fill. | No fetch/URLs; deps **framer-motion** (`useReducedMotion`). **Needs 5 helper files not returned by get_component** (`stroke-draw-type-utils/palette`, `tone`, `loop`, `motion`, `random`): same gap as the lead Liquid Text; small, rewrite locally (cubicBezier, mulberry32, a visibility-gated rAF hook). |
| Cream-pour title (alt 2) | Fluid Ink Morph | arihantcodes_1f7b8c4d | https://21st.dev/@arihantcodes_1f7b8c4d/components/ink | 18211 | Text dissolves in from fluid turbulence (`feTurbulence` + `feDisplacementMap`) and settles into crisp letters over `settleMs`; the rAF stops when settled (cost only during the ≈ 2 s intro). Pure DOM text, so it stays sharp and accessible. Good "pour then settle" intro; idle ripple would be our addition. | `colorStart` caramel → `colorEnd` cream/gold; `settleMs` 1500; font Instrument Serif via class; drop the scale 0.8 entrance (motion rule: no big scale-in); add a reduced-motion/Calm branch (render settled text, no filter); for idle, a slow low-amplitude `scale` on the displacement only in Full. | Clean, no remote calls; deps **framer-motion** (`motion`, `animate`). Filter applied via `style={{filter:url(#…)}}` (CSSOM, CSP-safe). Note: SVG filters on 200-px text re-rasterise each frame during the intro (GPU-light, CPU moderate); fine for 1.5 s. |
| Cream-pour title (alt 3, texture only) | Liquid Metal (gold variant) | educalvolpz | https://21st.dev/@educalvolpz/components/liquid-metal | 30389 | WebGL2 domain-warped "liquid chrome" with a **gold** palette, pointer-reactive flow, IntersectionObserver pause, reduced-motion CSS fallback, DPR cap 2, `powerPreference: low-power`. Usable as the moving cream surface clipped to the title (render the canvas behind a CSS mask of the wordmark). Its own `maskText` mode is just a cheap CSS gradient sweep (`background-clip:text`), a nice Calm-ish fallback. | Retune the gold palette toward cream (`lo` = espresso `#0A0A0B`, `hi` = `#F3E3C3`, `tint` = `#E7BF72`); clip to the wordmark with a local SVG mask file (not a `data:` URL); render the canvas only at the title's bounding box, not full screen; cap DPR at 1.5 if the frame time exceeds 4 ms. | Clean, no remote calls; deps **motion** (`useReducedMotion`). Per-frame cost: full-screen fragment shader with ~9 fbm calls × 4 octaves per pixel = heavy at 1280×800 @2×; at title-box size (~1100×260) acceptable on the PC, measure on the 16 GB laptop. Never stops while visible (no idle sleep). |
| (searched, dropped) | Liquid Text (glasscn) | glasscn | https://21st.dev/@glasscn/components/liquid-text | 19237 | Smoke shader masked to text. | — | **Dropped: remote asset.** `GemSmoke image="https://cdn.21st.dev/assets/mirror/…svg"` loads from 21st's CDN; deps `@paper-design/shaders-react`; mask is a `data:` SVG URL (CSP). |
| (searched, dropped) | Metaball Morph | daiwiikharihar | https://21st.dev/@daiwiikharihar/components/metaball-morph | 27828 | Mercury metaballs + cursor drop. | — | Clean, no deps, but not text-shaped (free blobs behind a mix-blend title), and it evaluates the field on a 5-px grid every frame forever (≈ 41k cells × 8 sources at 1280×800 ≈ 2–4 ms CPU) with no hidden-tab pause. Idea only: the "cursor is another drop" for beads. |

### 1b. Order flow (particle text formation, streaming in and locking into letters, flow field at rest)

| Element | Candidate | Author | Link (21st.dev url) | id | Why it fits | To match MASTER.md | Code check |
|---|---|---|---|---|---|---|---|
| Order-flow title (best) | Pixel Flow Field | educalvolpz | https://21st.dev/@educalvolpz/components/pixel-flow-field | 30145 | Grid of cells sampled from the word; on first show they **scatter and re-form into the letters with a left-to-right stagger** (`REFORM_MS` 1750: exactly the intro length), then a **noise flow field moves through them at rest**, and the pointer leaves a decaying wake. Built for a frame budget: `MAX_CELLS` 14,000 cap (grid coarsens instead), flow lattice sampled per 4×4 block, **7 brightness tiers = 7 `fill()` calls per frame**, DPR cap 2, pauses on IntersectionObserver + `visibilitychange`, reduced-motion still frame. | Colours: field stop = `--line` at low alpha, accent = `--pnl-up` / `--pnl-down` at low alpha (needs a small change: assign each cell a side by hash and draw green and red tiers separately → 14 fills), ink = `--cream`; `shape="circle"` or square ticks; `weight` 400 with Instrument Serif (thin serifs need `cellSize` ≈ 4–5 to read; check legibility); make the scatter origin the left/right edges (a "tape") instead of random positions; `scatter` prop replays the intro; Calm = `paused` + still frame. | Clean: no fetch/URLs (`src` image prop unused; if used it must be local); deps **motion** (`useReducedMotion`), `@/lib/utils` `cn`. Fallback dot grid uses a `radial-gradient` style prop only (CSP-safe). |
| Order-flow title (alt) | Canvas Text | wensity | https://21st.dev/@wensity/components/canvas-text | 27535 | Particles sampled from the glyphs gather into the word (loading ring → morph), optional idle drift and pointer magnetism; **the loop stops itself once settled** (`idleLocked`) and wakes on pointer; one path + one `fill()` per frame (squares, not arcs); `MAX_PARTICLES` 2,800; reads only the glyph bbox with `getImageData`. Cheapest of the three. | Replace the loading-ring start with edge "tape" lanes (start x at ±width, y by row) so ticks stream in; colour by side (two fills, `--pnl-up`/`--pnl-down` at low alpha, settling to cream); `drift` ≈ 0.6 for a mild idle loop (it then never sleeps: fine, it's one cheap fill); fontFamily Instrument Serif; reduced motion already paints the static glyph. | Clean, no deps beyond React and `cn`; a MutationObserver on `<html>` class only re-reads colour. |
| Order-flow title (alt 2, strip first) | Particle Text | luv-jeri_c92deb60 (Cojeev 000h, MIT) | https://21st.dev/@luv-jeri_c92deb60/components/particle-text | 37510 | Glyph-sampled particles gather over 1.8 s and repel the pointer. | Would need its whole settings store removed. | **Issues:** its injected CSS has `@font-face` **src `https://cdn.21st.dev/assets/mirror/…woff2`** (remote fonts, Bricolage Grotesque / DM Sans), a global settings store that reads/writes `localStorage` and sets `data-flow` + CSS variables on `<html>`, and a `<style>` element (CSP). One `arc()+fill()` per particle per frame, never sleeps. Only the ~40-line draw loop is worth keeping. Rank last. |
| (searched, nothing better) | queries "particle text formation", "flow field text", "particle text cursor repel" also returned ParticleTextCanvas (8084), Cursor Driven Particles Typography (10097), Text Particle (1885), Flow Field Background (9962), Fluid Flow Grid (23961) | — | — | — | Same technique as the three above; not fetched (the three cover formation + flow field + repel). | — | — |

### 1c. Price trace (text path draw / stroke draw / glow)

| Element | Candidate | Author | Link (21st.dev url) | id | Why it fits | To match MASTER.md | Code check |
|---|---|---|---|---|---|---|---|
| Price-trace title (best) | Stroke Draw Type | isaiahbjork | https://21st.dev/@isaiahbjork/components/stroke-draw-type | 39119 | Per-letter outline draw with a **glowing accent pen tip riding the leading end of each stroke** (feGaussianBlur glow) = the "gold line drawing the letterforms like a price trace"; stagger per letter; then an optional fill. Sleeps when done. | Stroke `--gold`, tip `--ember`→`--gold` glow, fill off or a faint cream fill after the draw; stagger ≈ 0.11 s over 9 letters ≈ 1.5 s; the GEX-style heat glow behind is a separate cheap layer (a radial gradient in `--orb-pika` / caramel at ≤ 12% alpha, pulsing opacity via CSS, or the Liquid Metal shader at a low resolution). Dash lengths are estimated (`2.4·w + FS·1.05`, text has no `getTotalLength`): check the draw ends cleanly on Instrument Serif's thin strokes, or convert the wordmark to `<path>`s offline (opentype.js at build time) for exact lengths. | Same as 1a: deps **framer-motion**; **5 helper files missing** from get_component (rewrite locally). No remote calls. |
| Price-trace title (alt, simplest) | SVG Path Drawing Text Animation | minhxthanh | https://21st.dev/@minhxthanh/components/svg-path-drawing-text-animation | 2786 | One `<text>` with `stroke-dasharray`/`dashoffset` animated by SMIL `<animate>`: zero JS, zero per-frame script cost (compositor/SVG only). Proves the technique; good fallback for the laptop. | Replace `repeatCount="indefinite"` + 8 s with a single 1.5 s run (`fill="freeze"`); gradient stops → caramel → gold; Arial → Instrument Serif; add a glow copy (blurred duplicate) and the heat glow layer; Calm: no animate element, stroke fully drawn. | Clean, no deps; hard-coded "PATH DRAWING" text and size (it is a demo, not a reusable component: rewrite as ~30 lines). |
| Price-trace title (alt 2) | SVG Text Draw | animbits | https://21st.dev/@animbits/components/text-svg-text-draw | 19315 | Real per-letter `motion.path` `pathLength` 0→1 with stagger (exact lengths, unlike `<text>` strokes). | Its glyphs are hand-coded single-stroke lowercase paths, not Instrument Serif: keep the mechanism, feed it the wordmark's own paths (`brand/wordmark.svg` outlines, or centre-line paths drawn by hand for a monoline trace). | Clean, deps **motion**. |

### 1d. Navigation in the scene (nodes on an ellipse around the title, thin flowing lines)

| Element | Candidate | Author | Link (21st.dev url) | id | Why it fits | To match MASTER.md | Code check |
|---|---|---|---|---|---|---|---|
| Page nodes on an orbit | Radial Orbital Timeline | jatin-yadav05 | https://21st.dev/@jatin-yadav05/components/radial-orbital-timeline | 1820 | Nodes (Lucide icon + label) placed on a circle around a centre; click expands a small card (status badge, a metric bar, related links) = our hover/focus card with the live number. | Use the **layout and expand-card only**: remove auto-rotation (moving click targets break the ≥ 44-px / keys `1`–`6` rule and it re-renders React every 50 ms via `setInterval`); place on an ellipse (rx ≠ ry); nodes = `<a>`/`<button>` with focus ring cream, glass chip label `text-1`; open card on hover **and** focus; recolour purple/teal centre gradient → none (the title is the centre); 700-ms `transition-all` → `--d-base` 220 ms on opacity/transform only. | Clean; deps **lucide-react**, shadcn `badge`/`button`/`card` (registry, `@radix-ui/react-slot`, `class-variance-authority`). Performance issue as noted (state-driven rotation). |
| Flowing lines to nodes | Nodes | abhishek-mallick | https://21st.dev/@abhishek-mallick/components/nodes | 40282 | Hub + 8 nodes; a **pulse travels the shortest route to the node nearest the pointer** and lights it; at rest it visits nodes in turn; reduced motion lights the route without the pulse. Exactly the "thin flowing lines" idea (pulse = a gold dot running hub → hovered node). | Idea only: flat (not isometric), hub = title, 6 nodes, line `--line-strong`, lit route `--gold`, pulse `--ember` 2 px; one shared rAF with the title scene. | Clean, no npm deps, but **imports 4 helper modules not returned** (`nodes-utils/figure`, `iso`, `motion`, `stage`); reimplement (≈ 60 lines SVG). |
| (searched) | queries "node graph navigation", "orbit menu radial navigation around center", "constellation nodes connected lines" also gave Radial Menu (9518, Base UI, keyboard support), Kokonut Marking Menu (39956), Radial Command Ring (43203, isaiahbjork), Constellation Field (25569) | — | — | — | Radial *menus* open on press/long-press (hidden until invoked): wrong for always-visible page nodes. Constellation Field is a background, not navigation. Not fetched. | — | — |

**Recommendation for the three videos:** (1) Cream pour = the lead Liquid Text (43190) with Fluid Ink Morph's settle
intro; Stroke Draw Type's rising fill is the backup. (2) Order flow = Pixel Flow Field (30145); Canvas Text (27535) if the
laptop can't hold 4 ms. (3) Price trace = Stroke Draw Type (39119) pen-tip glow + a CSS heat-glow layer; the SMIL
version (2786) as the cheap fallback. Navigation: Radial Orbital Timeline's layout/expand card without rotation, plus a
Nodes-style pulse along the lines. Both isaiahbjork components need their small helper files rewritten locally.

---

## Part 2: Signature dot-matrix wordmark (footer of Performance, Accounts, Settings)

Spec (`pages/shell.md` Dot-matrix footer): full-width halftone CreamFlow wordmark at the page bottom, lower quarter cut
off even when scrolled to the end; dots near the cursor part and tint ember; Calm = still dot render; never in the
sidebar.

| Element | Candidate | Author | Link (21st.dev url) | id | Why it fits | To match MASTER.md | Code check |
|---|---|---|---|---|---|---|---|
| Dot-matrix wordmark (lead) | Halftone Wordmark Footer | kedhareswer | https://21st.dev/@kedhareswer/components/halftone-wordmark-footer | 37628 | **This is the Halden Studio footer.** The fetched componentCode is the same as the local `docs/design/inspiration/halden-footer.txt` (only the line endings differ, CRLF on 21st vs LF locally; defaults `wordmark="halden"`, `label="Halden Studio"`, preset `night` identical). Word fitted to the width and **cropped by the bottom edge (`crop` 0–0.6 of cap height, default 0.2)**, area-true halftone dots sampled 3×3 per cell, dots **part around the pointer on springs and warm toward a `warm` colour as they move** (= ember tint), click shock ring, print grain; DPR ≤ 2; IntersectionObserver pause; **the loop sleeps once every dot is home**; reduced-motion = still dots; real `<a>` links, sr-only wordmark. | `palette={{background:"#0A0A0B", ink:"#F3E3C3", warm:"#FF9A3C"}}` (ember is exactly its intended use); `wordmark="CreamFlow"` set in Instrument Serif (`fontFamily`, `fontWeight` 400; check the thin serifs survive at `dotGap` 6: may need 4–5, or set "Flow" italic in gold by drawing two words/two inks); `crop` 0.25 (lower quarter cut); `columns=[]`, `label`/`meta` → `CREAMFLOW / <PAGE>` eyebrow in Geist Mono or hide the top rows; `backToTop` keep (it scrolls the page, harmless); `grain` 0.3; click ring off in Calm and probably always (a click on the footer should do nothing surprising); Calm = `interactive={false}` + one still draw. | Clean: no fetch/URLs/fonts, no deps. **CSP: it renders `<style>{HWF_CSS}</style>`, move HWF_CSS to a local .css file.** Per-frame cost: one `beginPath`+`arc`+`fill` per dot per frame while awake; at 1440-px width, 6-px gap and a 560-px root the letter area holds roughly 8–15k dots → likely 3–8 ms while the cursor moves (fine at rest: it sleeps). Fix if measured over 4 ms: batch dots into 8 warmth buckets (one path + one fill per bucket), or `fillRect` squares, and `createPattern(grainTile)` once instead of every frame. Fonts: `document.fonts.ready` relayout already present. |
| Dot-matrix wordmark (alt 1, Calm still render / cheapest) | Dot Text | jahed | https://21st.dev/@jahed/components/dot-text | 25484 | Canvas dot-matrix typography (LED grid), fade/scramble transitions, optional unlit background dots; **single path + one fill for all lit dots** (cheap). Good for the Calm still render and as a lower-cost Full variant. | Remove the text cycling (one word); `activeColor` cream, `inactiveColor` `rgba(243,227,195,.04)`; no cursor reaction exists, so add the Halden spring/warmth step only if used for Full; cap DPR at 2 (it uses raw `devicePixelRatio`); stop the rAF after the fade (it loops forever, even when idle). | Clean, no deps. Never sleeps (rAF every frame forever); no reduced-motion branch. |
| Dot-matrix wordmark (alt 2, CSS-only) | Dithered Footer | otatechie | https://21st.dev/@otatechie/components/dithered-footer | 33290 | Brand name cut out of a drifting **dithered dot field** that brightens under the cursor; pure CSS masks, no canvas, no rAF except to set two CSS variables; drift is a 40-s CSS animation; respects reduced motion. Cheapest and zero main-thread cost. | Accent = cream dots, background `#0A0A0B`; the word is the *gap* in the dots (negative), a different look from the lead's positive halftone; no spring parting (spotlight only), so it misses "dots part"; remove newsletter form and socials. | No fetch; demo social links point to x.com/github.com/linkedin.com (plain anchors, remove). **CSP: its dot field is a `url('data:image/svg+xml…')` mask (blocked by `default-src 'self'` unless `img-src data:`) and it uses a `<style>` element.** No deps. |
| (searched) | "interactive dot matrix text", "particle text cursor repel", "halftone text effect" also gave Dot-Matrix Cascade Typography (45461), LED Dot Matrix Display (24868), Halftone Dots / CMYK shaders (17740/17741, paper-design), Halftone (44758), Halftone Shader Hero (44656, kedhareswer) | — | — | — | Shaders are background fills, not a wordmark; LED matrix is a small-digit display (could suit a status digit, not the footer). Not fetched. | — | — |

**Verdict:** keep the Halden footer (37628). It already does everything the spec asks (crop, part, ember warmth, sleep,
reduced motion, no remote calls); the only required changes are the CSS file (CSP), the CreamFlow palette and face, and
a dot-batching pass if the measured frame time exceeds 4 ms. Dot Text is the fallback for the Calm still render.

---

## Part 3: Colour themes (21st.dev themes, `get_theme`, all free)

Searches: type "theme" with queries "dark warm gold", "espresso cream", "black luxury", `color: amber` + "dark",
"espresso", "coffee" (all empty); "gold" (9), "cream" (1), "noir" (7), "amber" (12), "theme" sorted popular (16).
Fetched 7: THURAY Gold Void, Black Forest Gold, Honey Glow Gold, Cocoa Amber, Amber Dark, Amber Glow, Accessible Gold.
Rule kept in every mapping: a theme never replaces the true-black `--bg-0` or the chart/state palettes (MASTER §3.4–3.5);
it only fills the shadcn semantic variables that Radix/shadcn components read.

### Proposed (ranked)

**1. THURAY Gold Void** (suliin) · id `18560fd9-ba95-427f-937f-db5f6a833460` ·
https://21st.dev/community/themes/thuray-gold-void-1788909549433
- Dark tokens (HSL triplets, `.dark` = `:root`): background `0 0% 2%` (#050505), card / popover `0 0% 4%` (#0A0A0A),
  primary `45 68% 47%` (#C9A227 gold) with primary-foreground #050505, secondary #1A1A1A, muted-foreground #8B8B8B,
  accent = gold at 12% alpha, border / input = warm white `45 40% 97%` at **7% alpha** (hairline), ring = gold,
  destructive `0 84% 60%`; extras `--thread` bronze #8A6D1F, `--star`; radius 0; font stack names **Instrument Serif**
  for English display.
- Why: the only true-black + single-gold-voltage theme found; warm alpha hairlines read well on black; gold as ring and
  selection matches MASTER (cream focus ring is ours, see mapping).
- Map onto MASTER: `--background` → `--bg-0` #0A0A0B (keep ours, not #050505); `--card` → `--bg-2` #18181B (theirs
  #0A0A0A is too close to the page: cards would vanish); `--popover` → `--bg-3`; `--primary` → `--gold` #E7BF72 (theirs is
  darker/more saturated, 47% L); `--primary-foreground` → `--text-on-accent`; `--accent` → `--accent-wash`
  rgba(231,191,114,.10) (same idea as theirs); `--border` → `--line` #2A2A2F (or adopt their warm 7%-alpha hairline
  as `--line` on `bg-2`: test contrast), `--input` → `--line-strong`; `--ring` → `--cream`; `--destructive` →
  `--crit`; radius 0 → ignore (MASTER radii 4/6/10/14). Use `--thread` as the Home node-line colour (bronze, quieter than
  gold).

**2. Amber Dark** (vanweerenbern) · id `06da29bc-ea79-4af3-9174-48ebc2ad155c` ·
https://21st.dev/community/themes/amber-dark-1788951724951
- Dark tokens: background #09090B, card #101013, popover #17171B, secondary / muted #17171B, accent #1C1C21,
  muted-foreground #A1A1AA, border rgba(255,255,255,.08), input rgba(255,255,255,.14), ring **#E3AF35**, chart-1…5
  amber ramp #E3AF35 / #C2820A / #7A4E05 / #3A2100 / #1C1206, sidebar-primary #E3AF35; radius 0.375 rem in `.dark`.
  Note: `.dark --primary` is a grey **#7A7A7A** (likely a mistake; `:root` has #FFFFFF), so primary must be set.
- Why: its surface ladder (#09090B → #101013 → #17171B → #1C1C21) is almost exactly MASTER's (#0A0A0B / #111113 /
  #18181B / #202024): the closest structural match, neutral-cool greys with an amber ring.
- Map onto MASTER: surfaces 1:1 to `--bg-0…3`; `--primary` → `--gold` (fixing the grey); `--ring` → `--cream`;
  `--sidebar-primary` → `--gold`; borders → `--line` / `--line-strong` (theirs are white-alpha; ours are solid, so
  either works); **drop its `--chart-*` amber ramp** (charts keep MASTER §3.5 candle / orb / level colours).

**3. Cocoa Amber** (serafimcloud) · id `ccae16a1-8cbb-423e-bcaf-de12c7f0e94b` ·
https://21st.dev/community/themes/cocoa-amber
- Dark tokens: background #111111, card / popover #191919, **primary #FFE0C2 (cream)** with primary-foreground #081A1B,
  secondary **#393028 (cocoa)** / secondary-foreground #FFE0C2, muted #222222 / muted-foreground #B4B4B4, accent #2A2A2A,
  border #201E18 (warm), input #484848, ring #FFE0C2, chart cream/cocoa ramp. Its `.dark` sidebar block is off-palette
  (sidebar-primary **#1D4ED8 blue**, ring #D4D4D8).
- Why: the only theme that uses **cream as the primary** and a cocoa secondary: a direct "cream in coffee" reading; good
  source for a warm secondary-button / chip tone (#393028) that MASTER doesn't define yet.
- Map onto MASTER: `--background` → `--bg-0`; `--card` → `--bg-2`; `--primary` → `--gold` for buttons (keep cream for
  the focus ring and active nav, per MASTER §3.3) or try cream-primary as an owner option; `--secondary` #393028 →
  a new `--cocoa` chip/secondary-button fill (check text-1 on it ≥ 4.5:1: #F2ECE0 on #393028 ≈ 11:1); `--border`
  #201E18 → too dark on `bg-2`, use `--line`; replace the blue sidebar tokens with `--gold` / `--cream`.

### Fetched and rejected
- **Black Forest Gold** (dobotox, `e5d7a097-06aa-4bfb-be51-be25780c0508`): dark bg #0B0D0C, gold #CBA74F, but accent
  is forest green #1B3A2E, which would read as `--ok` / `--pnl-up` (accent never signals state).
- **Honey Glow Gold** (serafimcloud, `d20c0518-6f3d-4658-b66d-ad03e73da9d8`): dark bg #18181B (grey, not black),
  primary #FFC23D is almost MASTER's `--warn` #FFC93D (the gold/warn confusion MASTER §3.4 warns about); blue charts.
- **Accessible Gold** (dhxwhy, `d68b7a18-ade0-453a-80e1-16177681240f`): oklch-based and contrast-checked, Geist /
  Geist Mono fonts, but the dark background is olive-tinted (oklch 0.17 0.026 92) and primary oklch 0.78 0.151 92 is a
  yellow too close to `--warn`; purple/teal chart colours.
- **Amber Glow** (serafimcloud, `0dd4f377-57a5-4dea-af4f-05c1a38cce01`): primary is orange #FF6B35 (reads as ember /
  pnl-down), ring blue #0073FF.

---

## Part 4: Category coverage checklist (21st.dev categories)

One search (or more) per category, scoped to a CreamFlow use; query in brackets. "Detailed in per-page tables" = the
per-page agents own the full candidate list and code checks; the link given is one representative search result, **not
code-checked here** unless the row says "code read". Pages: Home, Live, Gameplan, Accounts, Settings, Review,
Performance, plus the shell (sidebar, top bar, ticker tape, alerts).

| Category | Verdict | Where / candidate / reason |
|---|---|---|
| Heroes | no place in the panel | ["dark hero section serif headline minimal"] Marketing sections (e.g. Hero Minimalism 6999 https://21st.dev/@lyanchouss/components/hero-minimalism). The Home scene (Part 1) replaces a hero. Borrow: nothing beyond the big-serif-title idea. |
| Features | no place | ["feature grid dark minimal icons"] Dark Grid 8712, Modern Feature Grid 6876 (glassmorphism spotlight). Marketing; the panel has no feature list. |
| Calls to action | no place | ["call to action banner dark"] Gradient Blinds CTA 40234 (WebGL), CTA Banner 19341. Nothing to sell; the one "act now" surface is the kill/flat control, which must never be a decorative banner. |
| Footers | used | Dot-matrix footer on Performance / Accounts / Settings: Halftone Wordmark Footer 37628 (Part 2, code read). |
| Navbars | used (shell) | ["app header top bar with status pill"] Top bar = page title + status pill + FLAT ALL; Page Header with Breadcrumbs 18149 https://21st.dev/@olewandowski1/components/page-header-2, Status pill 25393 (diceui). Detailed in per-page tables (shell). |
| Backgrounds | used, sparingly | ["subtle dark film grain noise background"] A static grain/noise over `bg-0` only (MASTER: one showpiece per screen). Card with Noise Pattern 1468 (SVG-filter noise, framer-motion entrance) https://21st.dev/@Ali-Hussein-dev/components/card-with-noise-patter; animated shaders (Grainy Light Leak 36552) are too busy for data pages. Not code-checked. Prefer the Halden footer's own 256-px grain tile reused as a static CSS background (local PNG, not `data:`). |
| Shaders | used on Home only | ["warm gold glow shader background dark"] Liquid Metal 30389 (Part 1, code read) for the cream/heat glow; shader-builder backgrounds (Background Shades 6543, Golden Rose 44725) are generic palettes. Never behind tables or charts. |
| Texts | used | Home title (Part 1: 43190, 39119, 18211, 30145, 27535, 2786, code read). Page titles stay static. |
| Buttons | used | ["hold to confirm destructive button"] Primary/secondary/ghost from shadcn. **Hold to Confirm (23527 ddoemonn, 28348 arihantcodes) must NOT be used for KILL / FLAT ALL** (MASTER risk-first: no delay); used only for deleting an account or preset (MASTER §10, pages/accounts.md); the raise-risk change keeps its typed confirmation. Detailed in per-page tables. |
| Cards | used | ["dark stat card with sparkline and delta"] Stat Card 45564 https://21st.dev/@aadixc0de/components/stat-card (delta colour by "good direction"), Market Card 45580 (blue glow, recolour). Detailed in per-page tables (Live, Accounts, Performance). |
| Inputs | used | ["number input with stepper buttons"] Settings risk, account sizes: Number Field 28180 (coss.com, Base UI, drag-to-scrub) https://21st.dev/@coss.com/components/number-field. Detailed in per-page tables (Settings, Accounts). |
| Tables | used | ["dense sortable data table dark"] Trades, events, accounts: Sortable Data Table 35036, Data Table 28327 https://21st.dev/@ephraimduncan/components/table-05, Trade Journal Table 27124 (ssych ui). Detailed in per-page tables. |
| Forms | used | ["settings form with validation sections"] Settings / account setup: Form Layout 4347 https://21st.dev/@ephraimduncan/components/form-layout. Detailed in per-page tables. |
| Dialogs | used | ["type to confirm destructive dialog"] Typed confirmation `<account> 1/N` (SPEC Q-riskfrac): The Dialog 13191 "type-to-confirm" variant https://21st.dev/@felipemenezes098/components/the-dialog, shadcn Alert Dialog 702. Base Alert Dialog 26651 has a 3D blur entrance (drop: delays). Detailed in per-page tables. |
| Tabs | used | ["animated sliding pill tabs indicator"] Animated Tabs 24930 (educalvolpz, underline/pill/segment, keyboard) https://21st.dev/@educalvolpz/components/animated-tabs; Pill Morph Tabs 7878. Detailed in per-page tables. |
| Badges | used | ["status badge with dot live"] Engine/feed status, LIVE / stale chips: Status Dot 24882 https://21st.dev/@edwinvakayil/components/status-dot, Status 25393. Pulse only on healthy LIVE; never pulse crit. Detailed in per-page tables. |
| Avatars | no place | ["avatar initials account"] Avatar 34164 (appica-dev). One owner, no people; accounts are identified by firm + name chips, not avatars. Borrow: the initials-in-circle shape could mark the prop firm (MFFU / Lucid) on account rows if the owner wants a glyph. |
| Toggles | used | ["switch toggle dark radix"] Full/Calm effects, per-account enable: Radix Switch 1765 https://21st.dev/@skyleen77/components/radix-switch, Switch 11886 (jshguo). Detailed in per-page tables (Settings). |
| Sliders | used | ["range slider with value label ticks"] Thresholds / risk fraction: Marks Slider 29217 https://21st.dev/@ai2/components/slider-marks (labelled ticks, snap stops), coss.com Slider 11664. Risk changes still need the typed confirmation; the slider only proposes. Detailed in per-page tables. |
| Selects | used | ["searchable select combobox"] Account picker, firm preset: Combobox 34727 (halaska-studio) https://21st.dev/@halaska-studio/components/combobox. Detailed in per-page tables. |
| Checkboxes | used | ["checkbox animated check"] Review card flags, filters: Checkbox Group 10365 (Radix + Framer Motion) https://21st.dev/@micka_design/components/checkbox-group. Detailed in per-page tables. |
| Radio Groups | used | ["radio group cards segmented choice"] Account preset (MFFU Rapid/Builder/Pro, Lucid Flex), Review verdict: Radio Group in Card 28339, originui segmented 753 https://21st.dev/@originui/components/radio-group. Detailed in per-page tables. |
| Date Pickers | used | ["date range picker dark"] Performance / Review range filter: Date Range Picker 35052 (kuratlielia, presets, keyboard) https://21st.dev/@kuratlielia/components/date-range-picker. Detailed in per-page tables. |
| Calendars | used | ["monthly calendar heatmap profit loss days"] Performance P&L calendar: Returns Calendar 23460 (ssych ui, monthly returns heatmap, compounding) https://21st.dev/@ssychui/components/returns-calendar; Activity Heatmap 35054. Detailed in per-page tables (Performance). |
| Text Areas | used | ["autosize textarea notes"] Review notes, gameplan override notes: Auto growing textarea 10143 https://21st.dev/@shadcnspace/components/auto-growing-textarea. Detailed in per-page tables. |
| Charts & Data Viz | used | ["candlestick chart", "equity curve area chart drawdown"] Live chart: Candlestick Chart 43205 (isaiahbjork, canvas, pan/zoom/keyboard) https://21st.dev/@isaiahbjork/components/candlestick-chart, Candle Chart 20107 (ssych); Performance: Portfolio Chart 29532 (drawdown-from-peak shading), BJORK Drawdown chart 39725, Balance Chart 30538. Detailed in per-page tables (Live, Performance). |
| Stats & KPIs | used | Same as Cards: Stat Card 45564; Card 7461 count-up (count-up must not count from a stored value, MASTER principle 2: show the live value). Detailed in per-page tables. |
| Dashboards | searched, nothing fit as a whole | ["trading dashboard dark"] Crypto Dashboard 5480 (reuno-ui) **calls the CoinGecko API** (remote fetch, drop); Trade Journal Table 27124 and Candle Chart 22250 are parts, already listed. The panel is built page by page from parts. |
| Progress | used | ["progress bar meter threshold segments"] Drawdown margin / daily loss used: Meter 38114 (escalating colour at 75% / 90%) https://21st.dev/@redrob-labs/components/meter, Partition Bar 26545. Recolour to ok/warn/crit with icon + word. Detailed in per-page tables (Accounts). |
| Numbers | used | ["animated number ticker tabular digits"] Live P&L, price: Animate Digits 20071 (unlumen, only changed digits blur-slide) https://21st.dev/@unlumen/components/animate-digits. Tabular figures; no count-up from 0. Detailed in per-page tables. |
| Toasts | used | ["toast notification stack sonner"] Non-critical confirmations ("settings saved"): Sonner Toast 27363 (isaiahbjork) https://21st.dev/@isaiahbjork/components/primitive-sonner. Critical alerts are never toasts (they persist). Detailed in per-page tables (shell). |
| Alerts | used | ["critical alert banner with icon"] Persistent crit/warn banners: Alert 34379 (appica-dev, icon + title + actions) https://21st.dev/@appica-dev/components/alert, Banner 21206 (diceui, priority queue, variants). No entrance animation on crit. Detailed in per-page tables (shell). |
| Notifications | used | ["notification center inbox list"] Alert history (seen vs dealt with): Notification Panel 27135 https://21st.dev/@uvain/components/notification-panel (its "seen vs handled" split fits risk alerts). Notification Stack 31789's pile/fan hides items (drop). Detailed in per-page tables (shell). |
| Spinner Loaders | used, minimal | ["minimal spinner loader"] Spinner 11657 (coss.com, Lucide Loader2) https://21st.dev/@coss.com/components/spinner. Data waits show skeletons or "—" + stale marker, not spinners, wherever possible. Metaball Loader 42977 (louisstudios) could be the splash loader (WebGL, not checked). |
| Empty States | used | ["empty state minimal dark"] No trades yet / no review cards: Empty State 1435 (serafimcloud) https://21st.dev/@serafimcloud/components/empty-state, Interactive Empty State 22301. MASTER: empty states carry the wordmark. Detailed in per-page tables. |
| Sidebars | used (shell) | ["collapsible sidebar navigation icons dark"] Sidebar 1075 (aceternity, expands on hover) https://21st.dev/@manuarora700/components/sidebar, Sidebar Light 19361. Detailed in per-page tables (shell). |
| Menus | used, minor | ["context menu right click dark"] Row actions on tables (copy order_id, open review card): Context Menu 23560 (ddoemonn, keyboard, portal) https://21st.dev/@ddoemonn/components/context-menu. Never the only path to an action. |
| Dropdowns | used | ["dropdown menu radix dark"] Account / view menus: Dropdown (Radix UI) 24858 https://21st.dev/@edwinvakayil/components/r-dropdown, Radix Dropdown Menu 1778. Detailed in per-page tables. |
| Paginations | used, minor | ["compact pagination"] Trade history pages if not virtualised: Joined Pagination 454 (originui) https://21st.dev/@originui/components/joined-pagination. Prefer virtual scroll for logs. |
| Docks | no place | ["dock magnification bar"] Dock 37505 (luv-jeri; same Cojeev library whose Particle Text injects remote fonts). The sidebar + keys `1`–`6` + Ctrl+K cover navigation; a magnifying dock moves targets under the pointer. |
| Grids & Bento | used, layout only | ["bento grid dashboard tiles dark"] Live / Accounts tile layout: Bento Grid 622 (kokonutd) https://21st.dev/@kokonutd/components/bento-grid. Use CSS grid directly; no hover-shadow tilt on risk tiles. |
| Lists | used | ["list rows with status and metadata"] Results were selects/spinners (Inline Loading Status 19001 is a good "checking / connected / reconnecting" line for feed state https://21st.dev/@cnippet-dev/components/v-spinner-7). Lists are plain shadcn rows. |
| Accordions | used, minor | ["accordion minimal dark"] Settings groups, gameplan sections: Ghost Accordion 29230 https://21st.dev/@arihantcodes_1f7b8c4d/components/accordion-ghost; shadcn Accordion. Detailed in per-page tables. |
| Carousels | used (Review) | ["swipeable card stack review"] Review cards one at a time with keyboard + undo: Swipe Deck 23568 (ddoemonn) https://21st.dev/@ddoemonn/components/swipe-deck, Card Stack 35044 (kuratlielia). Swipe-to-decide is fine for review verdicts only. Detailed in per-page tables (Review). |
| Scroll Areas | used | ["custom scroll area scrollbar radix"] Log panes, tables: shadcn Scroll Area 803 https://21st.dev/@shadcn/components/scroll-area. |
| File Trees | no place | ["file tree explorer"] File Tree 19150 (edwinvakayil). The panel never browses the SSD; reports open by link. Borrow: nothing. |
| Steppers | used | ["stepper wizard dark multi step form"] Account setup (firm preset → size → risk fraction → webhook → confirm): Wizard Steps 23576 (ddoemonn, clickable rail, keyboard) https://21st.dev/@ddoemonn/components/wizard-steps, Configuration Stepper 28551. Detailed in per-page tables (Accounts). |
| Timelines | used | ["vertical timeline event log"] Order / trade event log (entry sent → filled → breakeven → exit): Audit Log 25163 (corr, timestamped, actor/status tags) https://21st.dev/@corr/components/audit-log, Timeline 35051 (kuratlielia, day groups). Detailed in per-page tables (Live, Review). |
| Links | used, minor | ["animated underline link hover"] Underline Animation 44319 (wensity, respects reduced motion) https://21st.dev/@wensity/components/underline-animation; the Halden footer has the same underline. |
| Search Bars | used | ["command palette cmdk search"] Ctrl+K (Home spec: Ctrl+K opens a page): Command Palette 44074 (ebnsina) https://21st.dev/@ebnsina/components/command-palette, 23522 (ddoemonn). 46285 tilts its preview pane (drop the tilt). Detailed in per-page tables (shell). |
| Images | no place (minor) | ["image zoom lightbox"] Zoomable Image 20027. Review cards use live chart renders, not images; a zoom on a chart snapshot could reuse it later. Images load only from local files. |
| Icons | used | ["animated lucide icons"] Lucide (MASTER). Lucide Icon Drawer 1785 uses anime.js (extra dep, skip). Static Lucide icons; animated icons not needed. |
| Marquees | used (shell ticker tape) | ["stock ticker tape marquee prices"] Logo Marquee 18221 (grootstudio) https://21st.dev/@grootstudio/components/logo-marquee: **code read** — its `InfiniteSlider` is clean (deps **motion**, **react-use-measure**; one motion value, linear loop, hover slows it); the demo's logos load from `cdn.21st.dev` (demo-only, remove). For the ticker: replace logos with text cells (symbol, price, ▲/▼ change), tabular figures, pause on hover and in Calm, and never let the tape carry state (alerts live in the top bar). Ticker 19171 (haydenbleasel) is a single symbol cell (price + change) to put inside it. |
| Borders | no place (deliberately) | ["animated border beam card outline"] Animated Border Beam 28347, Border Beam Panel 23408. MASTER: no glow/animated border on risk surfaces, and one showpiece per screen; a beam would compete with alerts. Possible only on the Home node cards (hover), owner's call. |
| Cursors | no place | ["custom cursor follower"] Cursor Follower 2883, Motion multi-follow 24664. A custom cursor slows precise clicks on charts and tables; the Home and footer cursor reactions are inside their canvases. |
| Tooltips | used | ["tooltip dark radix with shortcut"] originui Tooltip 207 (dark variant) https://21st.dev/@originui/components/tooltip. Detailed in per-page tables. |
| Popovers | used | ["popover radix details panel"] Event / level details on the chart: Popover (Radix UI) 23630 https://21st.dev/@edwinvakayil/components/r-popover. Detailed in per-page tables. |
| Tags | used | ["tag chip removable filter"] Level-type and outcome filters (held / broke / timeout): Chip Filter 44978 https://21st.dev/@amankaushik481/components/chip-filter, Chip Group 35042. Detailed in per-page tables (Review, Performance). |
| Announcements | used, minor | ["announcement pill banner new"] "New model live" / "gameplan parsed" notices: Banner 21206 (diceui, priority queue) https://21st.dev/@diceui/components/banner. Never used for risk (that is Alerts). |
| Pricing | no place | ["pricing plan cards"] Pricing Plan Card 25362. Nothing priced. Borrow: the plan-card layout (feature checklist + highlighted ring) for the prop-firm preset picker in account setup (rules per preset: drawdown, consistency %, profit target). |
| Testimonials | no place | ["testimonials"] Testimonial 6286. Personal tool, no social proof. |
| Sign ins | no place | ["lock screen PIN code unlock"] PIN Keypad Unlock 22111, Retro Slide to Unlock 44519. Served on 127.0.0.1 to one owner; no login. If the owner later wants a lock on the panel, a PIN keypad must never gate FLAT ALL. |
| Sign ups | no place | ["sign up form"] Sign up Form 2464. No accounts to create on the panel itself (broker/prop accounts are entered in the Accounts stepper). |
| AI chats | no place | ["ai chat message thread input"] Message Thread 31661, Prompt Input 28187. The panel has no chat; Discord is the phone remote. Borrow: Message Thread's timestamped list for showing the Discord command log (`!flat`, `!resolve`) read-only, if wanted. |
| Globes | no place | ["globe 3d"] 3D Globe 26543, Globe 27476 (React Three Fiber). NQ only, no geography; three.js would be dead weight. |
| Maps | no place | ["world map dots"] World Map 999. No geographic data. ("Map" in CreamFlow means the Skylit GEX map: that is a chart, under Charts.) |
| Galleries | no place | ["screenshot gallery grid"] Gallery Grid with Captions 26909. No image collections; Review cards are a deck, not a gallery. |
| Videos | no place | ["video player minimal"] Video Player 36555. Videos are only for the owner's concept pick (Playwright recordings viewed outside the panel). |
| File Uploads | used (Gameplan) | ["file upload dropzone markdown document"] Morning gameplan `.md` file onto the Gameplan page (SPEC §3.4): File Dropzone 19201 (joyco) https://21st.dev/@joyco/components/file-dropzone. **Code read:** clean component (deps lucide-react, shadcn Button → `@radix-ui/react-slot`, `class-variance-authority`); demo only `console.log`s; **it imports `file-dropzone-utils/use-file-upload`, not returned by get_component** (write a small hook: drag state, accept `.md,.txt`, max size, one file). Image previews use object URLs (`blob:`): not needed for `.md`, so no CSP change. Illos Upload Dropzone 40107 ships an illustration under a separate licence (skip); UploadThing Dropzone 18847 is tied to an upload backend (skip). |
| FAQs | no place | ["faq section"] FAQ Section 1290. Help lives in SPEC/CLAUDE.md; tooltips explain fields in place. Borrow: the accordion pattern, already under Accordions. |

## Coverage rows

- home | title, cream pour | candidates 43190 (lead, pre-checked), 39119, 18211, 30389 (texture); dropped 19237 (remote CDN asset), 27828 (not text, heavy)
- home | title, order flow | candidates 30145, 27535, 37510 (strip remote fonts/global store first)
- home | title, price trace | candidates 39119, 2786, 19315
- home | nav nodes on an ellipse | 1820 (layout + expand card, no rotation); line pulse idea 40282 (helpers missing)
- shell | dot-matrix footer | used 37628 (= local halden-footer.txt); alternatives 25484 (Calm still), 33290 (CSS-only, `data:` mask)
- theme | colour themes | proposed THURAY Gold Void 18560fd9-…, Amber Dark 06da29bc-…, Cocoa Amber ccae16a1-…; rejected Black Forest Gold, Honey Glow Gold, Accessible Gold, Amber Glow
- gameplan | file upload | used 19201 (helper hook missing)
- shell | ticker tape | used 18221 InfiniteSlider (text cells, demo logos remote)
- all 70 categories | see Part 4 table (marketing-only and no-place categories each with a one-line reason)
