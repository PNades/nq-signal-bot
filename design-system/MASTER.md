# CreamFlow design system — MASTER

> Step 14a part 0 (owner 2026-11-14, SPEC §9b, §13 Q-step14a). **Draft for the owner's picks**: the font pairing,
> the 21st.dev components and the showpieces are chosen at the end of part 0; until then the values below are the
> proposal. Once approved, this file and `pages/*.md` are the source of truth for the Control Panel's look; a page
> file overrides this one only where it says so.
>
> Made with the ui-ux-pro-max skill (`search.py --design-system --persist -p "CreamFlow"`, then `--page` per page and
> domain searches for charts, typography, UX and colour). The skill's raw output is kept in `skill-raw/`; where this
> file departs from it, the reason is in **What we kept and rejected from the skill** at the end.

---

## 1. Principles (in priority order)

1. **Risk first.** The kill/flat control, account states and alerts are the most prominent things on every screen
   and never wait for, sit behind or move with an animation. Decorative motion steps back the moment something is
   at risk.
2. **Honest numbers.** Every number is in tabular figures, says what it is (estimated / assumed filled / backtest),
   and shows a dash or a stale marker when its data is missing or old. Nothing counts down from a stored value:
   timers are recomputed from the clock and the data each tick.
3. **Fast.** The panel never slows the engine. Effects have a measured budget (§8) and pause when unseen.
4. **Premium, not busy.** Near-black (Cocoa Amber), a few warm cream accents, one showpiece per screen at most.
   If it looks better, it is better, but readability wins every tie.

---

## 2. Brand

**Name:** **CreamFlow**: one word, capital C and capital F, in every title, the browser tab, the sidebar header,
the splash screen, alerts and docs. Never "Creamflow", "Cream Flow" or "Cream-Flow". In the monospace uppercase
eyebrow style only, it is written `CREAMFLOW` (e.g. `CREAMFLOW / PERFORMANCE`). Not the bot's internal name
`nqbot`, which stays for commands and code.

**Idea:** cream poured into black coffee: smooth, warm, layered, flowing. The flow is order flow and the price
trace; the cream is the calm, warm interface on top of a black market.

**Wordmark** (`brand/wordmark.svg`, draft): "Cream" in the display face's upright, "Flow" in its italic, in gold.
Letter-spacing −0.025 em at display size. Minimum width 96 px; below that use the logo mark.

**Logo mark** (`brand/logo-mark.svg`, `brand/favicon.svg`): a thick cream "C" open to the right like a tipped cup,
with a gold flow line leaving it like a price trace and ending in an ember dot. Clear space = half the mark's
height on every side. The favicon is the mark on a 14-px-radius black tile with a 1-px border.

**Brand colours:** Cream `#F3E3C3`, Gold `#E7BF72`, Caramel `#D0945A`, Ember `#FF9A3C` (the cursor tint on the
dot-matrix wordmark, celebration sparks; never the King, owner 2026-11-14), on the Cocoa Amber background `#111111` (owner 2026-11-14). The logo mark and favicon keep their cream "C", gold flow line and ember dot.

**Where the name appears:** `<title>` = `CreamFlow · <Page>`; favicon; sidebar header (mark + wordmark, mark only
when collapsed); splash/loading screen; Home's forming title; the dot-matrix footer wordmark; empty states.

---

## 3. Colour tokens

**Theme: Cocoa Amber** (21st.dev, serafimcloud, https://21st.dev/community/themes/cocoa-amber), picked by the owner
2026-11-14 as shown in the preview, including its own background `#111111` (near-black, not navy or slate; overrides
the reference images and the first draft's true black `#0A0A0B`, kept in the mock as `&theme=original`). The theme sets
surfaces, lines, text, accents and radii; state, P&L and the chart palette stay CreamFlow's (§3.4–§3.6). No light theme.
Contrast ratios are measured (WCAG 2.x) against `bg-0` / `bg-2`.

### 3.1 Surfaces and lines

| Token | Hex | Use |
|---|---|---|
| `--bg-0` | `#111111` | app background (theme `background`) |
| `--bg-1` | `#18181B` | sidebar, top bar, page panels (theme `sidebar`) |
| `--bg-2` | `#191919` | cards, inputs, table header (theme `card`) |
| `--bg-3` | `#2A2A2A` | popovers, menus, tooltips, dialogs, hover on `bg-2` (theme `accent`) |
| `--bg-inset` | `#0C0C0C` | chart canvases, code, wells |
| `--line` | `#201E18` | 1-px borders, dividers, grid lines in tables (theme `border`, warm) |
| `--line-strong` | `#484848` | input borders, focused panel outline (theme `input`) |
| `--line-chart` | `#1E1E1E` | chart grid |

**Known consequences of the theme (measured; for the step-4 mocks):** `bg-1` and `bg-2` are almost the same grey
(1.01:1), and the warm `--line` is nearly invisible on `bg-2` (1.06:1), so cards on a panel separate by `line-strong`
borders or spacing rather than by fill. `--crit` text on `bg-3` is 4.4:1 (under 4.5): critical text sits on `bg-0`–`bg-2`
or on the `crit-fill` banner.

Elevation is shown by surface lightness + border + a 1-px top highlight, not by shadow (shadows vanish on black).

### 3.2 Text

| Token | Hex | Contrast bg-0 / bg-2 | Use |
|---|---|---|---|
| `--text-1` | `#EEEEEE` | 16.3 / 15.2 | primary text, numbers |
| `--text-2` | `#B4B4B4` | 9.1 / 8.5 | secondary text, labels |
| `--text-3` | `#B4B4B4` | 9.1 / 8.5 | captions, axis text, placeholders (the theme has one muted grey, so `text-3` = `text-2`; hierarchy comes from size and weight) |
| `--text-on-accent` | `#081A1B` | 14.2 on cream | text on cream fills (theme `primary-foreground`) |

### 3.3 Accent (brand)

| Token | Hex | Contrast bg-0 | Use |
|---|---|---|---|
| `--cream` | `#FFE0C2` | 15.0 | wordmark, active nav, primary headings' accent line, focus ring (theme `primary`) |
| `--gold` | `#FFE0C2` | 15.0 | primary buttons, active tab underline, slider, links, selection (the theme's one primary, so `gold` = `cream`: "Flow" in the wordmark is the same cream as "Cream") |
| `--caramel` | `#393028` | fill only (text-1 on it 11.1) | cocoa: secondary buttons, chips, selected-row fill (theme `secondary`); never a text colour |
| `--ember` | `#FF9A3C` | 9.4 | glows only: the dot-matrix cursor tint, celebration sparks (never the King) |
| `--accent-wash` | cream at 10% | — | active nav background, selected row |

Accent colours **never** signal state (healthy, warning, danger). State has its own tokens.

### 3.4 State (always with a second cue: icon, sign, arrow or text)

| Token | Hex | Contrast bg-0 | Second cue | Use |
|---|---|---|---|---|
| `--ok` | `#5FD39B` | 10.6 | `CircleCheck` icon + word ("LIVE", "feed OK") | healthy status only |
| `--warn` | `#FFC93D` | 12.9 | `TriangleAlert` icon + word | warnings |
| `--crit` | `#FF4F4F` | 6.1 | `OctagonAlert` icon + word, 2-px left rule | breaches, critical alerts, KILLED |
| `--crit-fill` | `#D4232A` | white text 5.2 | the words KILL / FLAT ALL | the kill/flat control's fill |
| `--info` | `#8FB3FF` | 9.5 | `Info` icon | neutral notices |
| `--stale` | `#8C867D` + dashed underline | — | `Clock` icon + "stale" / "—" | missing or out-of-date data |

Warn (`#FFC93D`, saturated yellow) and gold (`#E7BF72`, tan) are close in hue (43° vs 39°): the rule above (accents
never mean state; warnings always carry the icon and the word) keeps them apart.

### 3.5 Profit and loss (colour-blind safe)

| Token | Hex | Always with |
|---|---|---|
| `--pnl-up` | `#5FD39B` | a leading `+` and `▲` (or `TrendingUp`), e.g. `▲ +$651.66`, `+1.8R` |
| `--pnl-down` | `#FF7B6E` | a true minus `−` and `▼` (or `TrendingDown`), e.g. `▼ −$152.40` |
| `--pnl-flat` | `--text-2` | `±0` / `$0.00`, no colour |
| `--pnl-up-wash` / `--pnl-down-wash` | 9% alpha of each | calendar day cells, table row tint |

Red/green alone never carries meaning; the sign and arrow are always there, so the panel reads the same in
grayscale and for red-green colour blindness.

### 3.6 Chart palette (chosen together: candles + GEX orbs + levels)

| Token | Hex | Meaning | Distinguished by |
|---|---|---|---|
| `--candle-up` | `#EFE6D2` | up candle (cream-white, filled body) | filled vs … |
| `--candle-down` | `#8494E6` | down candle (soft periwinkle, filled body, 1-px brighter outline) | … hue 230° |
| `--orb-pika` | `#F2B950` | Pika node (positive gamma), warm gold | round glowing dot; hue 41° |
| `--orb-barney` | `#DD9BE0` | Barney node (negative gamma), soft orchid-lavender | round glowing dot; hue 297° (67° from the down candle) |
| `--orb-king` | its own sign colour (Pika gold / Barney orchid) at full brightness + an extra glow ring (`#FFF1DC` at 70%, 2 px, soft outer halo) | the King (owner 2026-11-14): the brightest, largest orb with the extra ring; the same on the main chart and the radial; not orange | ring + size + label "King" |
| `--level-ceiling` | `#FF7B6E` @ 70% | ceiling role line / band | role chip "C" |
| `--level-floor` | `#5FD39B` @ 70% | floor role line / band | role chip "F" |
| `--level-neutral` | `#B3ADA3` @ 60% | VWAP, POC, value-area lines | label at the right edge |
| `--zone-fill` | role colour @ 10% | a zone = shaded band between its edges; hatched while provisional | hatch pattern |
| `--marker-held` / `--marker-broke` / `--marker-timeout` | ok / crit / text-2 | event outcome markers | shapes ● / ✕ / ◌ |
| `--marker-king-switch` | cream | King-switch marker | `Crown` glyph |

Up vs down candles differ by lightness (cream-white vs mid periwinkle) as well as hue; Pika vs Barney orbs differ by
hue (gold vs orchid) and every orb label says "Pika" or "Barney". Single levels are thin lines, zones are shaded
bands (owner 2026-11-14).

**GEX snapshot radial (owner 2026-11-14):** the chart's colours: Pika gold for positive, Barney orchid-lavender for
negative, with `+` / `−` in every label; the King is the brightest orb with the extra glow ring, as on the main chart
(not orange). Green/red stay reserved for P&L and status.

---

## 4. Typography

All fonts are free (SIL Open Font License), self-hosted woff2 from the `@fontsource` packages, bundled at build
time; nothing loads from a font CDN. Every number on screen uses tabular figures (`font-variant-numeric:
tabular-nums`; the monospace faces are tabular by construction).

### 4.1 Pairing: B, "Terminal luxe" (owner 2026-11-14)

| Role | Face | Package |
|---|---|---|
| Display (headings, wordmark, Home title) | **Instrument Serif** 400, upright + italic ("Flow" and the second title line in italic) | `@fontsource/instrument-serif` |
| UI | **Geist** variable | `@fontsource-variable/geist` |
| Numbers, clocks, prices, eyebrows | **Geist Mono** variable (tabular by construction) | `@fontsource-variable/geist-mono` |

All OFL. Instrument Serif has one weight, so hierarchy in headings comes from size and italic, never from bold.
The specimen with the two pairings not chosen (A Fraunces + Hanken Grotesk + JetBrains Mono, C Bricolage Grotesque +
Inter + Martian Mono) stays in `docs/design/type_specimen_1440.png` for the record.

### 4.2 Scale (dense dashboard; px / line-height / weight)

| Token | Size | LH | Face | Use |
|---|---|---|---|---|
| `--t-eyebrow` | 11 | 16 | mono 500, uppercase, +0.12 em | `CREAMFLOW / PERFORMANCE`, card captions |
| `--t-caption` | 12 | 16 | UI 400 | helper text, axis labels (mono) |
| `--t-body-sm` | 13 | 18 | UI 400 | tables, dense lists |
| `--t-body` | 14 | 20 | UI 400 | default text |
| `--t-lead` | 16 | 24 | UI 400 | page subtitle |
| `--t-h3` | 18 | 24 | UI 600 | card and panel titles |
| `--t-h2` | 26 | 30 | display 400 | section titles |
| `--t-h1` | 38 | 40 | display 400, −0.01 em | page title (two lines: the second in cream italic) |
| `--t-kpi` | 28 | 32 | mono 500, −0.01 em | KPI numbers |
| `--t-display` | 64 | 64 | display 400, −0.02 em | splash, empty-state wordmark |
| `--t-hero` | clamp(104, 15vw, 240) | 0.9 | display 400, −0.02 em | Home's forming title (the dot-matrix footer is Geist 600–900 sized to its width, `pages/performance.md`) |

Minimum text size 11 px (mono eyebrows only); body text never below 13 px.

---

## 5. Spacing, layout, radii

**Spacing (4-px grid, dense):** `--s-0_5` 2 · `--s-1` 4 · `--s-2` 8 · `--s-3` 12 · `--s-4` 16 · `--s-5` 20 ·
`--s-6` 24 · `--s-8` 32 · `--s-10` 40 · `--s-12` 48 · `--s-16` 64.
Card padding 16 (dense) / 20; gaps between cards 12; page gutter 24 (32 at ≥ 1440 px).

**Shell:** sidebar 248 px (64 px collapsed, icons only, tooltips on hover); top bar 52 px; ticker tape 28 px under the
top bar; content max width none (dashboards use the full width), forms max 880 px. Targets: 1280 × 800 and up;
nothing scrolls horizontally at 1024 px.

**Radii (Cocoa Amber, 0.5 rem):** `--r-xs` 4 (chips, tags) · `--r-sm` 6 (inputs, buttons) · `--r-md` 8 (cards) · `--r-lg` 8 (panels,
dialogs) · `--r-pill` 999 (status pill, nav pill, badges).

---

## 6. Elevation

| Level | Surface | Border | Extra |
|---|---|---|---|
| 0 page | `bg-0` | none | optional 2% grain (Full only) |
| 1 panel | `bg-1` | `line` | `inset 0 1px 0 rgba(255,255,255,.03)` |
| 2 card | `bg-2` | `line` | same highlight; hover → `line-strong` |
| 3 overlay (menu, popover, tooltip) | `bg-3` | `line-strong` | `0 12px 32px rgba(0,0,0,.55)` |
| 4 dialog | `bg-3` | `line-strong` | `0 24px 64px rgba(0,0,0,.7)` + backdrop `rgba(0,0,0,.6)` + 8-px blur |
| glass (Full only) | `rgba(24,24,27,.55)` + `backdrop-filter: blur(16px) saturate(140%)` | `rgba(255,255,255,.06)` | only on the nav pill / dock and the Home node cards; text on it ≥ 4.5:1 |

---

## 7. Motion

**Durations:** `--d-instant` 80 ms (press) · `--d-fast` 150 ms (hover, toggles) · `--d-base` 220 ms (menus, tabs,
tooltips) · `--d-slow` 320 ms (dialogs, page transition) · `--d-number` 400 ms (number tickers) · `--d-scene`
600–1,800 ms (Home intro, celebrations). Exits run at about 70% of the entry time.

**Easing:** standard `cubic-bezier(.2,0,0,1)`; enter `cubic-bezier(0,0,.2,1)`; exit `cubic-bezier(.4,0,1,1)`;
emphasized `cubic-bezier(.3,0,0,1)`; springs (Motion) `{stiffness: 420, damping: 34}` for the nav pill and dock,
`{stiffness: 260, damping: 22}` for the review result card's slight bounce. No overshoot on numbers or tables.

**Rules:** animate `transform` and `opacity` only (no width/height/top/left); at most one showpiece animating per
view; a **border beam on only one element per page** (owner 2026-11-15); a canvas showpiece **stops redrawing once
settled** and starts again only for a change, a resize or the pointer (owner 2026-11-15: Dot Matrix Text); numbers tween only between real values and never hide the final value for more than `--d-number`; nothing
risk-related animates in (kill/flat, account danger states, critical alerts appear at once, then may pulse once).

**`prefers-reduced-motion: reduce`** forces Calm (§8). In Calm every transition is ≤ 80 ms opacity or instant.

---

## 8. Effects setting: Full and Calm

| | Full | Calm |
|---|---|---|
| Home title | forms on the first open of a session, idles, reacts to the cursor | still, fully formed, nodes in place |
| Canvas/WebGL backgrounds, grain, spotlight, border beams, tilt | on | off |
| Number tickers | tween 400 ms | snap |
| Flip-disk matrix | flips (0.6 s) | snaps at once; values keep updating |
| Page transitions | 320 ms fade + 8-px rise, staggered children | instant |
| GEX radial | 5–10 updates/s, nodes glide | 1 update/s, no motion |
| Main chart | live updates (always) | live updates (always) |
| Celebrations | confetti / sparkles | a static badge |

**Calm switches on automatically** while the live engine runs on the same machine during market hours (from step 21),
and whenever the OS asks for reduced motion. **Pause rules:** every effect stops its animation loop when the tab is
hidden (`visibilitychange`) or the element is off-screen (`IntersectionObserver`), and stops redrawing once settled.
**Budget** (measured with Playwright/Chrome tracing at 1440 px, device pixel ratio capped at 2): the whole panel idle
in Full ≤ 15% of one CPU core and ≤ 1 active requestAnimationFrame loop per visible showpiece; each canvas/WebGL
effect ≤ 4 ms per frame; Calm idle ≤ 3%. An effect over budget is cut, not the data.

---

## 9. Icons

Lucide (`lucide-react`), stroke 1.5, sizes 16 (inline, tables) / 20 (nav, buttons) / 24 (empty states). Decorative
icons `aria-hidden`; icon-only buttons have an `aria-label` and a tooltip. No emoji anywhere. Nav: `House` Home,
`Activity` Live, `Wallet` Accounts, `Map` Gameplan, `ScanEye` Review, `ChartNoAxesCombined` Performance,
`SlidersHorizontal` Settings; `OctagonX` kill, `Square` flat, `Pause` / `Play`.

---

## 10. Components (baseline; the 21st.dev picks restyle these)

- **Buttons:** primary = gold fill, black text; secondary = `bg-2` + `line-strong`; ghost = text-2, `bg-3` on hover;
  destructive (non-urgent: delete account/preset) = crit outline + hold-to-confirm. Height 32 (dense) / 36; hover
  150 ms, press scale .98 (Full).
- **Kill / flat control:** top bar, right, always visible on every page and in full-screen charts: solid `crit-fill`,
  white bold text "FLAT ALL", `OctagonX` icon, 36 px tall, never glass, never tilted, never animated in, a confirm
  only for KILL (one click + Enter), never hold-to-confirm. Links to `/emergency` (SPEC §9b fallback).
- **Status pill:** pill radius, dot + word: `ENGINE LIVE` (ok), `PAUSED` (warn), `KILLED` (crit), `OFFLINE`
  (stale). The dot may breathe slowly in Full only when healthy; a bad state never blinks for attention more than once.
- **Cards:** `bg-2`, `line`, `r-md`, padding 16; KPI card = eyebrow caption, mono number, delta badge with sign +
  arrow, optional sparkline.
- **Inputs:** `bg-2`, `line-strong`, `r-sm`, 32 px; unit suffix in text-3 mono; labels always visible above (never
  placeholder-only); errors below the field in crit with an icon, linked by `aria-describedby`; validate on blur;
  error summary at the top after a failed save, focus moved to it.
- **Tables:** sticky header on `bg-2`, 32-px rows, right-aligned numbers in mono, zebra off (hover row `bg-3`),
  sortable headers with `ArrowUpDown`, filter chips that wrap.
- **Dialogs:** Radix Dialog, focus trapped, Esc closes (except while a risky action is in flight), primary action on
  the right.
- **Toasts:** non-critical only, 4 s, bottom right; on a page with the dock they sit above the dock's footprint
  (open or minimized, either variant), never under it (owner 2026-11-15); on Home in the bottom-right corner below its
  nodes (`pages/home.md`); **critical alerts** are banners under the top bar in crit with
  icon and words, they stay until acknowledged and are never toasts.
- **Tooltips:** `bg-3`, 12 px, 220 ms in / 80 ms out, never the only place information lives.
- **Focus:** 2-px cream ring with 2-px offset on every focusable element.

---

## 11. Charts

- **Library:** TradingView Lightweight Charts (npm, self-hosted; its attribution kept) for candles, equity,
  drawdown; small custom SVG for sparklines; canvas for the GEX radial and the Monte Carlo fan.
- **Common style:** `bg-inset` canvas, `line-chart` grid (horizontal only), axis text `text-3` mono 11, crosshair
  cream dashed at 40%, tooltip `bg-3` with mono values, legend = small dots with labels (from the reference).
- **Equity curve:** area with a gold line, 12%→0% gradient fill; phases as faint background bands with a vertical
  marker and label at each start (development, demo, each prop account); walk-forward months never drawn.
- **Drawdown:** underwater area in `pnl-down` at 18% with the account's limit as a dashed crit line and the closest
  approach marked and labelled. (The skill's chart data has no drawdown entry; this follows its time-series rules.)
- **Monte Carlo fan:** 5–95% band in gold at 12%, 25–75% at 20%, median solid cream, smallest/largest-drawdown paths
  dashed text-2, the drawdown limit as a crit dashed line, probability of reaching it stated in text beside it.
- **P&L calendar:** layout of `docs/design/pnl_calendar_reference.png`; day cells tinted with `pnl-up-wash` /
  `pnl-down-wash`, value with sign and arrow, trade count and W/L in text-2; week column on `bg-1`.
- **Main chart:** §3.6 palette; zones as shaded bands, single levels as thin lines, outcome and King-switch markers
  with distinct shapes, crosshair tooltip with price and time; orbs drawn through the plugin (primitives) API.
- **Accessibility fallback:** every chart has a "Table" toggle showing the same numbers, and series differ by line
  style as well as colour.

---

## 12. Number formatting

Money `$1,186.91` / `−$152.40` (true minus, sign always on deltas); R to two decimals `+1.85R`; percentages one
decimal; prices to the tick `21,437.25`; times ET 24-hour `09:30:00 ET`; dates `Tue 14 Nov`. Estimated values carry
"est." and inferred states their label ("assumed filled", "maybe filled"). Missing or stale → `—` with the stale cue.

---

## 13. Anti-patterns (do not)

From the skill: light mode default; slow rendering; emoji icons; removing focus rings; placeholder-only labels;
errors only at the top; colour as the only cue; low-contrast grey on grey; animating width/height; infinite
decorative animation without a pause; parallax or scroll-jacking; toasts that never disappear; one timing for every
transition; text under 12 px for body.

Ours: navy or slate backgrounds; more than one showpiece animating in a view; glass, tilt, beams or glow on the
kill/flat control, alerts or an account in danger; the flip-disk matrix (0.6-s flips) for any risk information;
counting down a stored value instead of recomputing from the clock; showing an old number without a stale cue;
any remote font, script or image at runtime; inline `<style>` elements or `data:` URLs that the strict CSP blocks
(move the CSS into a bundled stylesheet; React `style` props are fine; see `shortlist.md` rules); accent colours used to mean state; green/red P&L without a sign and
arrow; decorative motion that keeps running in a hidden tab; hold-to-confirm on kill/flat.

---

## 14. Departures from the reference images (and why)

| Reference | CreamFlow | Why |
|---|---|---|
| Near-black **navy** background, panels one shade lighter | **Cocoa Amber** near-black `#111111` with neutral very dark greys (owner 2026-11-14; first draft true black `#0A0A0B`) | owner's choice; makes the cream accents and glows read as warm light, and charts gain contrast |
| One muted **steel-blue** accent | **Cream + gold** accents, ember for glows only | the brand; blue now means only "info" |
| Plain sans headings | **Display serif** headings (Instrument Serif) + grotesk UI + mono numbers | a premium, signature look; numbers stay in mono as the reference did |
| Two-line title, second line light blue | Two-line title, second line **cream italic** | same rhythm, brand colour |
| Boxes with 1-px borders everywhere | Fewer borders: surface steps carry depth, borders where they separate data | calmer on black; borders on black read heavier than on navy |
| No start page | **Home**: a full-screen CreamFlow scene with the navigation built into it | owner's brief |
| Static sidebar highlight | Animated nav indicator (pill / liquid drop) | owner's brief; Calm makes it instant |
| KPI cards: number + caption + icon | Same, plus delta badge with sign/arrow and a sparkline | more information per card |
| P&L calendar: plain green/red cells | Same layout, tinted washes **plus** sign and arrow, hover cards, click-to-expand day | colour-blind safe; richer |
| Network graph as a style cue | Dropped as a chart; the idea survives in Home's nodes linked by flowing lines | it was a cue, not a requirement |

---

## 15. What we kept and rejected from the skill

- **Kept:** "Dark Mode (OLED)" style (deep black, minimal glow, visible focus, high readability); the dense spacing dial;
  the motion rules (reduced motion, 1–2 animated elements per view, exits faster, no back-out easing on data); the UX
  rules for forms (inline errors, error summary with focus), tables, toasts, confirmation of destructive actions,
  skeletons, keyboard navigation and colour-only cues; Lightweight Charts for candles; the chart a11y fallbacks.
- **Rejected:** the navy/slate palette with a green CTA (owner wants true black and cream); the "Enterprise Gateway"
  landing pattern (a sales site, not a trading panel); Cinzel + Josefin Sans ("real estate luxury") and the Google
  Fonts CDN imports (we self-host); the page files' generic advice (lazy-load images, cache headers, 1200-px max width)
  where it doesn't fit a local dashboard; GSAP (Motion covers it with less weight).
