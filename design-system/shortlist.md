# CreamFlow 21st.dev shortlist: index and leading candidates

> Step 14a part 0, steps 3 and 3b (2026-11-14). The full tables, one per page or area with element, candidate,
> author, 21st.dev link, id, why it fits, what it needs to match MASTER.md and the code check, are in `shortlist/`.
> Every listed candidate's code (component + demo) was read with `get_component`; nothing is picked until the owner
> picks. `coverage.md` has one row per element and per 21st.dev category.

| Area | File |
|---|---|
| App shell, shared elements, navigation showpieces, the owner's three inspiration files | `shortlist/01-shell-shared-nav.md` |
| Accounts, Settings, Gameplan, surface showpieces (tilt, spotlight, beams, glass, grain, liquid glass/metal) | `shortlist/02-accounts-settings-gameplan-surfaces.md` |
| Performance, Review, main-chart chrome, numbers & data showpieces, moments | `shortlist/03-performance-review-chart-data-moments.md` |
| Home scene, signature dot-matrix wordmark, colour themes, the 70-category checklist | `shortlist/04-home-wordmark-themes-categories.md` |

## The owner's saved components (each one candidate beside the searched ones)

| Saved file | On 21st.dev | Code vs the paste | Needs |
|---|---|---|---|
| `liquid-text.txt` | Liquid Text, isaiahbjork, id 43190 | same | 4 helper files not returned (written locally in the mock); fonts awaited before rasterising |
| `halden-footer.txt` | Halftone Wordmark Footer, kedhareswer, id 37628 | same (line endings only) | inline `<style>` → CSS file; CreamFlow colours and Instrument Serif; dot batching if > 4 ms/frame |
| `dot-text.txt` | Dot Text, jahed, id 25484 | same | stop redrawing once settled and off-screen (it redraws every frame as written) |
| `flip-disk-matrix.txt` | Flip Disk Matrix, daiwiikharihar, id 24220 | same | lime → cream/gold; glyphs `/ + - . $ %`; no overshoot; recompute from the clock; snap in Calm |
| `liquid-glass-navbar.txt` | Liquid Glass Navbar "Waterline", gughigug, id 43059 | same | full blue → cream/gold retint (CSS variables, shader constants, iridescence); sound code and demo credit removed; ambient water loop must sleep; horizontal only |
| `tilt-card.txt` | Tilt Card, tom_ui, id 12244 | same | 15° / 1.05 → 6° / 1.02; off in Calm; never on danger states |

## Leading candidates by page (first-ranked in the area files; alternatives there)

| Page / area | Element | Leading candidate (id) |
|---|---|---|
| Home | title: cream pour / order flow / price trace | Liquid Text 43190 / Pixel Flow Field 30145 / Stroke Draw Type 39119 |
| Home | nodes + flowing lines | Radial Orbital Timeline 1820 (layout and expand card, no rotation) + Nodes 40282 (pulse idea) |
| Shell | sidebar · account switcher · status pill · FLAT ALL | wensity Sidebar 31454 · efferd Workspaces 4511 · diceui Status 25395 · plain destructive Button (originui 137) |
| Shell | navigation showpiece (≥ 3 distinct) | Waterline 43059 · Magnetic Dock 23415 / Floating Dock 18014 · sliding-pill tabs 24930 · spotlight nav 2499 |
| Shared | toasts · alert banner · confirm · command palette · kbd | sonner 27363 · appica Alert 34383 · shadcn Alert Dialog 702 + type-to-confirm 13191 · cmdk 33510 · Combo Kbd 28308 |
| Shared | splash / empty states / skeleton | Dot Text 25484 · Halftone footer 37628 · Skeleton Swap 23557 |
| Accounts | drawdown gauge · status badges · hold-to-confirm (delete only) · tilt | see `02` · Hold-to-confirm 28348 (1.2 s, keyboard, reduced motion) · Tilt Card 12244 |
| Settings | segmented · spotlight · unsaved-changes bar · number field | Segmented 23552 · Spotlight 35180 · Unsaved bar 9576 · Number Field 28180 |
| Gameplan | file drop · countdown · timeline | joyco Dropzone 19201 · Flip-Disk 24220 (Animate Digits 20071 alt) · see `02` |
| Performance | KPI tile · ticker · calendar · table · tabs · date range · export | KPI Tile 40086 · Delta Number Ticker 44850 · 36571 + 31350 + hover card 222 + sheet 35214 · shadcn Data Table 1050 · Animated Tabs 24930 · 478 · 23536 |
| Review | card · result card · replay controls | Swipe Deck 23568 (drag off) · 18251 · 30231 + 35685 |
| Main chart chrome | timeframe / NQ \| ES segmented · toolbar · legend | Segmented 44919 · see `03` |
| Numbers & data | scramble · sparkline · ticker tape · GEX radial | 16620 · 34511 · 35200 · built on canvas (nothing fit; 29837 ring reference) |
| Moments | confetti · magnetic · page transition | 33152 · 35179 · 40098 (crossfade path) |
| Surfaces | border beam · grain · liquid metal | BorderBeamPanel 23408 (pauses off-screen) · static self-hosted grain file (nothing fit) · MetalButton 3166 (pure CSS, gold) |

## Colour themes (21st.dev)

**Picked by the owner 2026-11-14: Cocoa Amber, as shown in the preview** (its own `#111111` background included; MASTER.md §3).
The three proposed:

1. **THURAY Gold Void** (suliin, `18560fd9-ba95-427f-937f-db5f6a833460`): true black, warm 7%-alpha hairlines, one gold voltage, names Instrument Serif. Best match for the mood.
2. **Amber Dark** (vanweerenbern, `06da29bc-ea79-4af3-9174-48ebc2ad155c`): its surface ladder is almost exactly MASTER's; amber ring.
3. **Cocoa Amber** (serafimcloud, `ccae16a1-8cbb-423e-bcaf-de12c7f0e94b`): cream as the primary, cocoa `#393028` secondary (a candidate chip/secondary-button tone).

Mappings and the four rejected themes are in `shortlist/04`.

## Rules found while reading the code (apply to every adopted component)

1. **CSP:** about a dozen components put CSS in an inline `<style>` element (in JSX or added to `document.head`); under
   `default-src 'self'` that is blocked, so their CSS moves into a bundled stylesheet. React `style={{…}}` props are fine.
   `data:` mask URLs and `blob:` (Liquid Glass 40333's displacement map, dropzone previews) need an explicit
   `img-src` allowance or a local file; a blob worker (Magic UI confetti 843) needs `useWorker: false`.
2. **Loops that never stop** (Waterline's ambient water, Dot Text, Flip-Disk polling, Magnetic Dock's idle wave,
   KineticGrid 18254, MetallicButton 29293): each gets a sleep when settled, off-screen or hidden (MASTER §8).
3. **Missing helper files:** several picks import `*-utils` files that `get_component` doesn't return; they are fetched
   from the registry (or rewritten) and read before use.
4. **Dropped after reading:** remote images or fonts inside the component (glasscn Liquid Text 19237, Cojeev Particle
   Text 37510, Select with Avatar 25130's avatars, Motion Tilt 24971, Video Player 36555), unsafe HTML injection
   (45363), an unauditable one-person npm wrapper (44686), a global `html, body { overflow: hidden }` (Spotlight
   Button). No fetched code contained text addressed to an AI; two search descriptions did (Bench Matrix 29454,
   Scribble Clock 46283) and were not used.
5. **Bugs to patch if picked:** Rolling Digits 19158 rounds before formatting (loses quarter points); Shimmer Button 833
   `dark:text-black` on black; marquee 27529 reduced-motion class on the wrong element; 30343 calls Radix with the
   Base UI API; Hold-to-confirm 23527 shows no progress in Calm.
6. **Hold-to-confirm** only for deleting an account or preset; never for KILL / FLAT ALL.
