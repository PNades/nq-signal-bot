# CreamFlow coverage: 21st.dev for every element

> Step 14a part 0, step 3 (owner 2026-11-14). One row per page and element: the 21st.dev component(s) used or drawn
> on (with links), or "searched, nothing fit" and why; then one row per 21st.dev category. The ranked candidates with
> their code checks are in `shortlist/`. The owner's picks (2026-11-15) are in `picks.md`; rows say **picked** where
> one is used. Rows added 2026-11-15 after a coverage check against every `pages/*.md` file and MASTER §10–11 are
> marked (2026-11-15); their candidates were found by search and their code is read only when one is built. Any element added later is searched on 21st.dev first and gets a row here before it is
> written from scratch (never `generate`).

## 1. Pages and elements

### App shell, shared elements, navigation

(detail: `shortlist/` file for this area)

- shell | collapsible sidebar (icons, grouped sections) | candidates: wensity Sidebar 31454 (https://21st.dev/@wensity/components/sidebar), starc007 Animated Sidebar 29334 (https://21st.dev/@starc007/components/animated-sidebar), unlumen sidebar-001 21517 (https://21st.dev/@unlumen/components/sidebar-001)
- shell | top-bar account switcher | candidates: efferd Workspaces 4511 (https://21st.dev/@efferd/components/workspaces), Select with Avatar 25130 (https://21st.dev/@felipemenezes098/components/select-08)
- shell | engine/connection status pill | candidates: diceui Status 25395 (https://21st.dev/@diceui/components/status), Status Dot 24882 (https://21st.dev/@edwinvakayil/components/status-dot)
- shell | FLAT ALL / KILL control | candidates: originui Button 137 (https://21st.dev/@originui/components/button), Interfaces Button 11859 (https://21st.dev/@jshguo/components/interfaces-button); animated destructive buttons searched and rejected (547, 31174, 28348, 24092: animate/hold-to-confirm)
- shared | toasts (sonner) | candidates: 27363 (https://21st.dev/@isaiahbjork/components/primitive-sonner), 19941 (https://21st.dev/@bundui/components/toast1)
- shared | alert banners + critical style | candidates: Project Banner 3905 (https://21st.dev/@shugar/components/project-banner), appica Alert 34383 (https://21st.dev/@appica-dev/components/alert)
- shared | confirm dialogs (alert dialog) | candidates: shadcn 702 (https://21st.dev/@shadcn/components/alert-dialog), type-to-confirm 13191 (https://21st.dev/@felipemenezes098/components/the-dialog), originui 1144 (https://21st.dev/@originui/components/alert-dialog)
- shared | splash / loading screen | candidates: Dot Matrix Text 25484 (owner's; https://21st.dev/@jahed/components/dot-text), Canvas Text particles 27535 (https://21st.dev/@wensity/components/canvas-text), Scramble Text 35193 (https://21st.dev/@kevingirelli/components/scramble-text), Text Shimmer 27522 (https://21st.dev/@wensity/components/text-shimmer)
- shared | empty states | **picked: Dot Matrix Text 25484** (stops redrawing once settled, owner 2026-11-15); Halftone Wordmark Footer 37628 (https://21st.dev/@kedhareswer/components/halftone-wordmark-footer; read from the local paste), cnippet Empty 19746 (https://21st.dev/@cnippet-dev/components/cnippet-empty)
- shared | skeleton loaders | candidates: Skeleton Swap 23557 (https://21st.dev/@ddoemonn/components/skeleton-swap), animbits shimmer 19999 (https://21st.dev/@animbits/components/loaders-skeleton), Shapes Skeleton 29863 (https://21st.dev/@ai2/components/skeleton-shapes)
- shared | tooltips | candidates: originui Tooltip 207 (https://21st.dev/@originui/components/tooltip), Flexnative Tooltip 13517 (https://21st.dev/@felipemenezes098/components/flexnative-tooltip)
- shared | command palette (Ctrl+K) | candidates: Layro Command Menu 33510 (https://21st.dev/@uvain/components/command-menu-palette), ddoemonn Command Palette 23522 (https://21st.dev/@ddoemonn/components/command-palette)
- shared | kbd shortcut hints | candidates: Combo Kbd 28308 (https://21st.dev/@ai2/components/kbd-combo), Keyboard Shortcuts sheet 26919 (https://21st.dev/@laziekiki/components/keyboard-shortcuts)
- shared | flip-disk matrix + split-flap alternatives | **picked: Flip Disk Matrix 24220** (owner's; https://21st.dev/@daiwiikharihar/components/flip-disk-matrix), Split Flap Display 19241 (https://21st.dev/@harshjadhavconnect/components/split-flap-display), Text Flipping Board 18574 (https://21st.dev/@manuarora700/components/text-flipping-board)
- nav | liquid drop (owner's Waterline) | candidates: Liquid Glass Navbar 43059 (https://21st.dev/@gughigug/components/liquid-glass-navbar)
- nav | floating dock with magnification | **picked 2026-11-15: Aceternity Floating Dock 18014** (https://21st.dev/@manuarora700/components/floating-dock), not on Home; other candidate Magnetic Dock 23415 (https://21st.dev/@rmahammad/components/magnetic-dock)
- nav | sliding pill / morphing indicator | candidates: Animated Tabs 24930 (https://21st.dev/@educalvolpz/components/animated-tabs), Glassmorphic Navbar with Morphing Pill 45733 (https://21st.dev/@themeocean/components/floating-glassmorphic-navbar-with-morphing-pill-indicator), Build UI Animated Tabs 1113 (https://21st.dev/@builduilabs/components/animated-tabs)
- nav | navbar reshaping on scroll | candidates: Morphing Scroll Navbar 27428 (https://21st.dev/@laziekiki/components/morphing-scroll-navbar), 45733 (scroll shadow)
- nav | spotlight hover on nav items | candidates: Spotlight Button 2499 (https://21st.dev/@waleedkibhen/components/spotlight-button), Fluid Hover Highlight 39507 (https://21st.dev/@educalvolpz/components/fluid-hover-highlight)
- nav | command palette with fuzzy search | candidates: 33510, 23522 (as shared)
- nav | dock minimize circle: the bar collapses into one circle in the bottom-right corner and back, remembered for the session (2026-11-15) | searched ("collapsible floating dock minimize to button"): 18014, 37505, 5004, 28 and 43047 are bars only, none collapses → our own morph on 18014 with a Motion layout animation (built in the mock)
- shell | top-bar logo mark + CreamFlow wordmark (2026-11-15) | our own asset (`brand/`), not a 21st.dev element
- shell | breadcrumb eyebrow `CREAMFLOW / <PAGE>` (2026-11-15) | candidates: Page Header with Breadcrumbs 18149 (https://21st.dev/@olewandowski1/components/page-header-2), Page Header Breadcrumb 18129; the mock uses a mono eyebrow with the scramble 16620 on load
- shell | page header: eyebrow, two-line title with the cream italic second line, subtitle (2026-11-15) | candidates: 18149, Sticky Header 23562 (https://21st.dev/@ddoemonn/components/sticky-header; condenses on scroll), Tagline 19232 (eyebrow)
- shell | ET clock (2026-11-15) | our own mono text recomputed from the system clock every second (MASTER: never counted down); no component needed
- shell | skip-to-content link (2026-11-15) | our own visually hidden link shown on focus; no component needed
- shared | base buttons: primary gold, secondary, ghost, press scale (2026-11-15) | candidates: originui Button 137, Interfaces Button 11859 (as the FLAT ALL row), on shadcn Button
- shared | error state: what failed, the time, Retry (2026-11-15) | candidates: Error Empty State 19376 (https://21st.dev/@olewandowski1/components/empty-states-4), Error Boundary UI 19362 (for React error boundaries)
- shared | stale cue: dimmed last value, Clock icon, "as of HH:MM:SS" (2026-11-15) | candidates: Inline Loading Status 19001 (state line), Status 25395; the dimming is our own token style
- shared | inline notice: Gameplan's after-09:30 upload notice, Performance's ledger-guard refusal (2026-11-15) | candidates: Callout 44862 (https://21st.dev/@alwkala/components/callout), shadcn Alert 1170
- shared | table filter chips (2026-11-15) | candidate: Chip Filter 44978 (section 2, Tags)
- shared | select with sub-labels: the Performance account filter and phase selector (2026-11-15) | candidates: Select With Description Items 25123 (https://21st.dev/@felipemenezes098/components/select-06), Select With Object 25122; the mock's account filter is our own listbox in the same pattern
- shared | main chart: candles, zone bands, level lines, outcome and King-switch markers, GEX orbs; live on Live, the replay clock on Review, pre-open on Gameplan (2026-11-15) | TradingView Lightweight Charts (MASTER §11) with our own series and primitives; reference Candlestick 43205 (section 2); the orbs from the GEX radial row
- shared | chart "Table" toggle, the accessible fallback on every chart (2026-11-15) | the switch from the layer-toggle candidates (29206, 44138); the table from shadcn data-table 1050

### Accounts, Settings, Gameplan, surfaces

(detail: `shortlist/` file for this area)

- accounts | drawdown gauge | candidates: Gauge https://21st.dev/@designali-in/components/gauge, Circular Progress https://21st.dev/@shadcnui-blocks/components/progress-10, Progress https://21st.dev/@edwinvakayil/components/r-progress
- accounts | status badges | candidates: Status https://21st.dev/@diceui/components/status, Badge https://21st.dev/@coss.com/components/coss-badge
- accounts | lives left (pips / segments) | candidates: Segmented Progress https://21st.dev/@jatin-yadav05/components/progress-bar, Meter https://21st.dev/@vijayksingh/components/meter
- accounts | edit form with validation | candidates: Form https://21st.dev/@shadcn/components/form, Form Field https://21st.dev/@kevingirelli/components/form
- accounts | preset picker (grouped combobox) | candidates: Combobox https://21st.dev/@coss.com/components/combobox, Combobox https://21st.dev/@isaiahbjork/components/primitive-combobox
- accounts | save-as-preset dialog | candidates: Dialog https://21st.dev/@originui/components/dialog (+ Form 1622)
- accounts | hold-to-confirm delete | candidates: https://21st.dev/@arihantcodes_1f7b8c4d/components/hold-to-confirm, https://21st.dev/@ddoemonn/components/hold-to-confirm, https://21st.dev/@kokonutd/components/hold-and-release-button
- accounts | tilt card (owner's saved) | **picked: Tilt Card id 12244** by tom_ui https://21st.dev/@tom_ui/components/tilt-card (= tilt-card.txt; cut to 6° / 1.02, off in Calm), Tilt https://21st.dev/@ibelick/components/tilt; Motion Tilt card 24971 rejected (remote image in component)
- settings | grouped layout (section list + panels) | candidates: Scroll Spy https://21st.dev/@ddoemonn/components/scroll-spy, Settings Sidebar Layout https://21st.dev/@felipemenezes098/components/settings-2
- settings | toggles / switches | candidates: https://21st.dev/@jshguo/components/interfaces-switch, https://21st.dev/@micka_design/components/switch
- settings | sliders with value label and cap marker | candidates: https://21st.dev/@originui/components/slider, https://21st.dev/@micka_design/components/slider (cap marker to add to either)
- settings | number input with units (stepper) | candidates: https://21st.dev/@coss.com/components/number-field, https://21st.dev/@sean0205/components/number-field-form; Unit Field https://21st.dev/@arikchakma/components/unit-field unreviewable (util files not returned)
- settings | segmented controls | candidates: https://21st.dev/@ddoemonn/components/segmented-control, https://21st.dev/@halaska-studio/components/segmented-control
- settings | danger-zone card | candidates: https://21st.dev/@cnippet-dev/components/v-form-12 (+ Dialog 373 for the confirm)
- settings | unsaved-changes bar | candidates: https://21st.dev/@stvenchg/components/unsaved-changes, https://21st.dev/@KarrixLee/components/unsave-popup
- accounts | sticky side summary with live-as-you-type values: daily profit limit, risk, lives, smallest / largest stop (2026-11-15) | candidate: Live Preview Form 37551 (https://21st.dev/@dhxwhy/components/live-preview-form; side preview, container-query layout, no animation library)
- accounts | unit number inputs, segmented controls, toggles, unsaved bar, DEMO tag (2026-11-15) | the same components as the Settings rows above and the status badges row (shared, one component each)
- settings | locked owner-rule rows: `Lock` icon, "changed through Claude Code" (2026-11-15) | candidate: Label Disabled 29340 (https://21st.dev/@uiable/components/label-disabled); Padlock 36617 rejected (an animated lock is decoration on a rule row)
- settings | risk-profile selector as cards with backtested stats (2026-11-15) | candidates: Radio Group in Card (section 2, Radio Groups) with the KPI tile's number style
- gameplan | header KPI tiles: status, flags left to review (2026-11-15) | **picked: KPI Tile 40086** (as Performance)
- gameplan | day timeline | candidates: https://21st.dev/@abui/components/timeline (strip DnD; ET now-line), https://21st.dev/@slide-cn/components/timeline (list fallback); a custom SVG band strip may be leaner
- gameplan | level list with tags | candidates: https://21st.dev/@bundui/components/badge10, https://21st.dev/@adobe/components/tailwind-taggroup
- gameplan | file upload dropzone | candidates: https://21st.dev/@haydenbleasel/components/dropzone, https://21st.dev/@joyco/components/file-dropzone
- gameplan | editable table | candidates: https://21st.dev/@ruixen.ui/components/table-edit, https://21st.dev/@ebnsina/components/inline-edit
- gameplan | countdown (alternatives to the Flip-Disk) | candidates: https://21st.dev/@unlumen/components/animate-digits; plain mono text recomputed per second as the Calm fallback
- surfaces | dot / grid background | candidates: https://21st.dev/@rmahammad/components/animated-grid (CSS; move `<style>` out for CSP), https://21st.dev/@satoriui/components/kinetic-grid (needs perf rework)
- surfaces | cursor spotlight | **picked: Spotlight 35180** https://21st.dev/@kevingirelli/components/spotlight; other https://21st.dev/@ibelick/components/spotlight
- surfaces | border beam | **picked: Border Beam Panel 23408** https://21st.dev/@rmahammad/components/border-beam-panel (pauses when hidden; CSP fix), **on only one element per page** (owner 2026-11-15); other https://21st.dev/@dillionverma/components/border-beam
- surfaces | glass panels | candidates: https://21st.dev/@educalvolpz/components/glass-card, Liquid Glass simple mode https://21st.dev/@manuarora700/components/liquid-glass
- surfaces | grain / noise overlay | searched ("noise grain texture overlay", "film grain overlay svg feTurbulence static"), nothing fit: only animated WebGL shaders or a card (1468) whose noise texture isn't in its code. Ship a static self-hosted grain file instead.
- surfaces | liquid glass | candidates: https://21st.dev/@manuarora700/components/liquid-glass, https://21st.dev/@designali-in/components/liquid-glass-button
- surfaces | liquid metal / chrome button | candidates: MetalButton (gold preset, CSS) in https://21st.dev/@designali-in/components/liquid-glass-button, https://21st.dev/@starc007/components/button-metallic; WebGL options (26531, 21562, 13533, 30389) seen in search, not read

### Performance, Review, main chart, numbers & data, moments

(detail: `shortlist/` file for this area)

- performance | KPI stat cards (Net P&L, Trades, Net R per trade, Win rate, Profit factor, Max drawdown) | **picked: 40086 kpi-tile**; others 45564 stat-card, 39900 tweenly-chart-kpi
- performance | number tickers | **picked: 44850 delta-number-ticker**; others 21513 number-ticker-02 (NumberFlow), 20071 animate-digits
- performance | equity chart tooltip/legend/card | candidates: 35057 line-chart, 43131 metric-switcher-trend-chart, 30538 balance-chart
- performance | drawdown chart | candidates: 39725 bjork-drawdown-chart, 29532 portfolio-chart (both draw a fixed dashed limit only)
- performance | drawdown per account under its own rule: the floor as a stepped dashed line, a "floor locked" badge, one account at a time (2026-11-15) | Lightweight Charts (MASTER §11): an area series plus a stepped line series; the badge from the status badges row (25395); built in the mock
- performance | Monte Carlo per account: bands, median, backtest-order path, the account's floor, P(reaching the floor) (2026-11-15) | the custom SVG of the fan row below, one account at a time; built in the mock
- performance | per-account switcher on Drawdown and Monte Carlo, in the page-tab row (2026-11-15) | **picked: Animated Tabs 24930** (the page tabs' own underline variant, with a small type badge per tab); the compact fallback is the select-with-sub-labels row (25123 pattern, our own listbox). The Waterline bar 43059 was tried here and dropped (too much empty space above the chart)
- performance | account filter and phase selector (2026-11-15) | see "shared | select with sub-labels"
- performance | "backtest" label on data (2026-11-15) | the status badges row (25395)
- performance | plan selector in backtest phases (2026-11-15, rework 3) | searched ("select dropdown scrollable list with badges"): 25167 v-select-13, 28315 select-multi, 23528 ddoemonn dropdown, 16830 HeroUI ListBox; **kept: our own listbox in the select-with-sub-labels pattern (25123)**, the same as the account and phase filters, 5 rows visible then it scrolls; ← → step through the plans
- performance | stage markers (passed, payout, failed) with hover tooltips, kept inside the chart (2026-11-15, rework 3) | searched ("chart marker tooltip annotation event pin"): **picked: MarkerTooltip 13982** (mapcn: a small marker with a hover card) and **Chart Tooltip 26600** (uiable: a label row and indicator rows) for the tooltip body; others 213 originui tooltip, 6036 tooltip-with-arrow; code read 2026-11-15, no network calls, nothing embedded. Markers are HTML over the SVG chart, 12 px inside the plot edges; the tooltip goes above the marker, else below, clamped inside the chart
- performance | chart headers and legends (Monte Carlo, Drawdown) (2026-11-15, rework 3) | searched ("chart card header legend dark"): **picked: Market Card 45580** for the header (eyebrow line + display title + controls on one row); 19605 cnippet chart legend for the legend row (swatch + short label, only the items present); 5881 SubframeApp area chart, 44670 peak-chart seen; the plan's rules moved into an info hint
- performance | Monte Carlo summary, two variants (2026-11-15, rework 3) | searched ("summary card key metrics with range median", "dark stats panel metric grid card"): **A: a hero cell after the KPI tile 40086** (net cash, or payouts to you while fees are missing) beside a **Stats Grid 29195** of five cells; **B: one Stats Grid 29195 of seven cells, each with the Leaderboard Table 29468 range whisker** (10-90% bar, median dot); others 39900 tweenly-chart-kpi, 45564 stat-card, 19070 advanced-stats, 44279 spark-table; code read 2026-11-15 (29468 inlines a <style> block: its CSS became classes; its description carries an advertisement, no instruction; no network calls)
- performance | stats box under the KPI tiles, two variants (2026-11-15, rework 3) | searched ("dark stats panel metric grid card"): **A: Stats Grid 29195** (ten cells, one bordered grid); **B: Insight Cards 29167** (five grouped cards, a pair of related numbers each, the wins / losses split bar); others 7461 card-10, 7841 stats-card-1, 8321 stats-card, 4245 / 4237 / 4435 statistics cards; code read 2026-11-15 (29167's keyframes are an inline <style>: not used)
- performance | Drawdown replay totals (2026-11-15, rework 3) | the same Stats Grid 29195 cells as summary A
- performance | horizon switch (30 / 60 / 120 trading days) on every Monte Carlo and Drawdown chart (2026-11-16, rework 4) | searched ("segmented control time range switch 30 60 120 days"): 34764 halaska segmented-control, 34935 balick, 44919 vamsiy78, 40165 adobe css-segmentedcontrol (day/week/month ranges), 24931 segmented-control-tabs, 35020 kuratlielia; **kept: Animated Tabs 24930** (segment variant), the page's own unit and view switches
- performance | chart zoom and pan along the time axis (2026-11-16, rework 4) | searched ("chart zoom pan brush time axis"): **picked as the pattern: Range Navigator 23954** (ssychui: drag the window to pan, pull to zoom) and **Brush Chart 35059** (kuratlielia: a zoomed window with markers read in place); 18775 canvas zoom toolbar, 2332 visx zoom seen; built into our lifecycle chart (wheel zoom around the pointer, drag to pan, double-click to reset, a "from – to" readout); no code copied
- performance | legend and KPI tooltips ("floor (backtest order)", "backtest order", profit factor) (2026-11-16, rework 4) | searched ("info tooltip legend item hover explanation"): 14335 HeroUI tooltip, 11817 interfaces-tooltip, 25221 floating-tooltip, 1277 shadcn tooltip, 28253 animated-tooltip, 3908 tooltip; **kept: our hint layer** (one delegated `data-hint` layer, MASTER: no native titles); dotted underline marks a legend item that explains itself
- performance | headline numbers as small stats (account "from today", Drawdown totals) (2026-11-16, rework 4) | searched ("probability stat card label value metric grid dark"): 7461 card-10, 45580 market-card, 8321 stats-card-1, 4245 / 4237 / 4435 statistics cards; **picked: Stats Grid 29195** (label above, value below, one bordered grid; the same cells as the Monte Carlo summary A) and summary A's hero cell for the account's own-stage chance, with a split bar (pass / open / fail)
- performance | stats grid cells light up on hover (2026-11-16) | **picked: Spotlight 35180** (the KPI tiles' own hover: a cream glow and ring that follow the pointer; off in Calm)
- performance | the calendar's month picker (2026-11-16) | searched ("month picker dropdown select with prev next arrows"): **picked: Calendar with Custom Select Dropdown 25124** (a month list in a custom select) and **Pagination with Arrows 28336** (prev / next), modelled on our top-bar account switcher (listbox, keys, themed states); others 25120 v-calendar-4, 11390 coss calendar, 468 originui calendar, 10449 date wheel picker
- performance | Monte Carlo summaries, account and plan, redesigned in two variants (2026-11-16) | searched ("radial progress ring gauge stat card animated", "progress bar with marker current value between min and max"): **picked: Circular Progress 22095** and **Progress (Vo2 Max card) 7636** for the ring (variant A), **Partition Bar 26545** for the split bar (variant B), **Reshaped Progress bar 17906** (a value between boundaries) for the position bar floor → target; others 2504 animated radial chart, 28963 radial chart, 19174 progress-with-value, 23549 progress-bar; code read 2026-11-16 (22095 pulls a Radix slider for its demo only; 26545 uses cva: neither copied); the stat cards with Spotlight 35180 and the range bars of Leaderboard Table 29468
- performance | "All accounts" per-account breakdown table, sortable, totals row (2026-11-16) | searched ("sortable data table dark with totals footer row status badges"): **picked as the pattern: Card Frame Table 22188** (status badges, a totals footer) and **Invoice History Table 22187** (a total footer); 28327 data-table (sorting), 25160 payments table, 34786 table seen; our own markup, the Trades table's sorting
- accounts | "Show archived" toggle (2026-11-16) | searched ("toggle switch show archived filter"): 37311, 11653, 21948, 29998; **kept: our Switch** (form-controls, 21st.dev 11886)
- performance | Compare plans table (2026-11-15, rework 3) | searched ("comparison table dark pricing plans"): **picked: Leaderboard Table 29468** (bars with range whiskers, numeric columns, the other rows dim on hover); others 29260 dark-matrix, 21218 comparison-3, 1541 pricing-table, 26827 comparison-02, 37513 two-plan-diff
- performance | the lifecycle chart (Monte Carlo fan and Drawdown replay) (2026-11-15, rework 3) | one SVG chart for both (the fan row's custom chart), so stages break the lines and each line shows only while it applies; the replay moved off Lightweight Charts for this (its lines can't break between two sessions)
- performance | Monte Carlo fan | candidate frame 35059 brush-chart; fan itself: searched (confidence band, monte carlo fan, percentile band), nothing fit (only a hard-coded price-target fan) → custom canvas
- performance | P&L calendar | candidates: 36571 activity-heatmap, 31350 wensity calendar, 23460 returns-calendar; day panel 35214 sheet
- performance | trades table | candidates: 1050 shadcn data-table, 33506 data-table-bulk-actions, 22175 table-15 (column chooser pattern)
- performance | tabs | **picked: 24930 animated-tabs**; other 24960 tabs-subtle
- performance | date-range picker | candidates: 478 originui calendar, 39093 starc007 date-range-picker, 35052 kuratlielia date-range-picker
- performance | $ / R segmented | candidates: 44919, 43309, 23552 segmented-control
- performance | export button | candidates: 23536 loading-button, 34553 download-button
- live | per-account KPI cards: est. P&L vs daily cap, drawdown left, lives, distance to payout (2026-11-15) | **picked: KPI Tile 40086**; other Market Card 45580 (https://21st.dev/@louisstudios/components/market-card)
- live | account tabs (2026-11-15) | **picked: Animated Tabs 24930**
- live | open position panel with stop and target (2026-11-15) | searched ("trading position card stop target"): 45534 ticker-card, 45580 market-card and 27122 swap-ticket don't fit; 27124 trade-journal-table (https://21st.dev/@ssychui/components/trade-journal-table) as the open-position row reference → our own card
- live | last-alerts list (2026-11-15) | candidates: Activity Feed 29394 (https://21st.dev/@felipemenezes098/components/item-19), Audit Log With Icon Tiles 28483, Notifications 27135 (section 2)
- live | level ladder: role chip, state, since-cross facts, P(held) (2026-11-15) | searched ("order book ladder price levels list"): only depth charts and pricing lists (29968, 28540) → our own table rows with the status-badge chips (25395)
- live | action row: PAUSE / RESUME, KILL / FLATTEN, Resolve, Confirm state, Payout taken, New evaluation (2026-11-15) | the base buttons and FLAT ALL rows, the confirm dialogs row (702, 13191), Burst 33152 on Payout taken
- live | balance and floor entry (2026-11-15) | the number-field row (coss number-field) inside the confirm-dialog pattern (1144)
- live | inferred-value labels and account danger states (2026-11-15) | the status badges (25395) and alert banners rows
- review | "at this point" facts side panel (2026-11-15) | our own definition list on the card surface; no candidate needed
- review | optional answers: entry choice, stop / target, confidence 1–5 (2026-11-15) | candidates: Rating Scale Group 8241 (https://21st.dev/@ruixen.ui/components/rating-scale-group; numbered boxes, cut to 1–5), Radio Group (section 2); star ratings (29301, 7096) rejected: a confidence score, not stars
- review | card + Take/Skip + shortcuts | candidates: 23568 swipe-deck, 23792 ai-approval, 35044 card-stack
- review | reason textarea | candidates: 29430 p-textarea-11, 23672 v-textarea-6
- review | batch progress | candidates: 23549 progress-bar, 9659 segmented progress-bar
- review | shortcut hints | candidate: 28308 kbd-combo
- review | result card bounce | candidates: 18251 presence-fade (around our own card), 23792 ai-approval (resolve morph); 44686 ding-toast dropped
- review | replay controls / scrubber | candidates: 30231 video-player (controls), 35685 matte-scrub-slider, 10324 micka slider
- chart chrome | toolbar | candidates: 19516 cnippet-toolbar (pattern on Radix Toolbar), 30343 toggle-group-animated-toolbar
- chart chrome | timeframe switcher / symbol switch | used: the $ / R segmented candidates (44919, 43309, 23552)
- chart chrome | legend | candidates: 35057 line-chart legend, 39296 chart-line
- chart chrome | tooltips | candidates: 20107 candle-chart (layout reference); 26600 chart-tooltip (reference only)
- chart chrome | layer toggles | candidates: 29206 toggle-group-multi, 44138 ebnsina toggle-group, 31280 wensity toggle
- chart chrome | full-screen button | candidate: 35286 expand-button + our own Fullscreen API code
- performance | calendar hover card | candidate: 222 shadcn hover-card
- showpieces | number tickers / flip counters | candidates: 19158 rolling-digits, 19241 split-flap-display, 18574 text-flipping-board
- showpieces | text scramble | candidates: 16620 dqnamo scramble-text, 26544 soralabs text-scramble, 35193 kevingirelli scramble-text
- showpieces | sparklines draw-in | candidates: 34511 appica sparkline, 35038 kuratlielia sparkline
- showpieces | ticker tape / marquee | candidates: 35200 kevingirelli marquee, 27529 diceui marquee, 19171 ticker
- showpieces | GEX radial base | candidate (reference only): 29837 radar-chart; otherwise searched (radar, radial, orbit, gamma exposure), nothing fit as code → custom canvas
- moments | confetti / sparkle burst | **picked: 33152 burst** (a static badge in Calm); other 843 magicui confetti
- moments | shimmer button | candidates: 38278 klyv-shimmer-button, 833 magicui shimmer-button
- moments | magnetic button | candidates: 35179 kevingirelli magnetic, 26725 magnetic-button
- moments | staggered page transitions | candidates: 40098 wipe-transition (crossfade path), 1079 blur-fade
- moments | shared-layout (layoutId) | searched (layout animation, shared layoutId), nothing fit as code (24523 demo, 25363 broken) → Motion layoutId directly
- moments | hold-to-confirm | candidates: 23527 ddoemonn hold-to-confirm, 28348 arihantcodes hold-to-confirm

### Home, dot-matrix wordmark

(detail: `shortlist/` file for this area)

- home | title, cream pour | candidates 43190 (lead, pre-checked), 39119, 18211, 30389 (texture); dropped 19237 (remote CDN asset), 27828 (not text, heavy)
- home | title, order flow | **picked concept (2026-11-14), build base 30145**; others 27535, 37510 (strip remote fonts/global store first)
- home | title, price trace | candidates 39119, 2786, 19315
- home | nav nodes on an ellipse | 1820 (layout + expand card, no rotation); line pulse idea 40282 (helpers missing). The layout is now wordmark-relative with "arcs to the centre" lines and hover cards with live numbers (`pages/home.md`), built on Motion directly
- home | opening sequence: nodes arrive clockwise from Live (200-ms stagger), each line drawing out to the word, a click or key skips (2026-11-15) | searched ("stagger entrance reveal sequence nodes connecting lines"): 18357 stagger-reveal-grid, 19729 text-reveal and 18555 words-stagger reveal grids or text, none sequences nodes with their lines → Motion directly (built in the mock)
- shell | dot-matrix footer | **picked: 37628** (= local halden-footer.txt), the word "cream" in Geist 900 → 600 by width, sized from its own box (owner 2026-11-15); alternatives 25484 (Calm still), 33290 (CSS-only, `data:` mask)
- theme | colour themes | proposed THURAY Gold Void 18560fd9-…, Amber Dark 06da29bc-…, Cocoa Amber ccae16a1-…; rejected Black Forest Gold, Honey Glow Gold, Accessible Gold, Amber Glow
- gameplan | file upload | used 19201 (helper hook missing)
- shell | ticker tape | used 18221 InfiniteSlider (text cells, demo logos remote)
- all 70 categories | see Part 4 table (marketing-only and no-place categories each with a one-line reason)

## 2. 21st.dev categories (checklist from its All components page)

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
| Docks | **used (picked 2026-11-15)** | ["dock magnification bar"] The page navigation is Aceternity Floating Dock 18014 (not on Home), with our own minimize circle (bottom-right corner). Dock 37505 (luv-jeri; same Cojeev library whose Particle Text injects remote fonts) stays rejected. The earlier objection (a magnifying dock moves targets under the pointer) is answered by Calm, which turns magnification off. |
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
| Borders | **used (picked 2026-11-15)** | ["animated border beam card outline"] Border Beam Panel 23408 on **only one element per page** (owner 2026-11-15), never on risk surfaces or alerts (MASTER). Animated Border Beam 28347 not used. |
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
| File Uploads | used (Gameplan) | ["file upload dropzone markdown document"] Morning gameplan `.md` file onto the Gameplan page (SPEC §3.4): File Dropzone 19201 (joyco) https://21st.dev/@joyco/components/file-dropzone. **Code read:** clean component (deps lucide-react, shadcn Button → `@radix-ui/react-slot`, `class-variance-authority`); demo only `console.log`s; **it imports `file-dropzone-utils/use-file-upload`, not returned by get_component** (write a small hook: drag state, accept `.md,.pdf,.docx` as SPEC §3.4 and §9b item 4 say (corrected 2026-11-15; it said `.md,.txt`), max size, one file). Image previews use object URLs (`blob:`): not needed for these files, so no CSP change. Illos Upload Dropzone 40107 ships an illustration under a separate licence (skip); UploadThing Dropzone 18847 is tied to an upload backend (skip). |
| FAQs | no place | ["faq section"] FAQ Section 1290. Help lives in SPEC/CLAUDE.md; tooltips explain fields in place. Borrow: the accordion pattern, already under Accordions. |

## 3. Built in the panel (step 14; each component's code read before use)

One row per element as it is built in `ui/web/`, with the 21st.dev component it came from and what was changed.
"Code read" = fetched with `get_component` and read for remote calls, remote assets and text addressed to an AI
(none found unless the row says so).

### Stage 1 (2026-11-15): shell, safety, /emergency
- shell | command palette (Ctrl K) | **used: Command Palette 23522** (ddoemonn), code read: Cocoa level-3/4 surfaces, `danger` rows for KILL / FLAT ALL (KILL still opens its confirm), Motion follows Effects, Windows keys only (`ui/web/src/components/ui/command-palette.tsx`)
- shell | engine status pill | **used: Status 25395** (diceui), code read: MASTER §3.4 state variants (ok / warn / crit / info / stale), the ping only on a healthy state, Radix Slot dropped (`status.tsx`)
- shell | KILL confirm dialog | **used: Alert Dialog 702** (shadcn on `@radix-ui/react-alert-dialog` 1.2.1), code read: level-4 surface, the critical z-layer (70), the tailwindcss-animate classes dropped (risk dialogs appear at once), Enter confirms (`alert-dialog.tsx`)
- shell | ticker tape | **used: InfiniteSlider from Logo Marquee 18221** (grootstudio), code read: react-use-measure replaced by a ResizeObserver, stops in Calm and in a hidden tab, the demo's CDN logos not used (`infinite-slider.tsx`); until step 22 the tape shows a static "levels appear once the live engine runs" row
- shell | toasts | **pattern of Sonner Toast 27363** (isaiahbjork), code read: the `sonner` package itself is **not** used, because it inserts a `<style>` element at run time that the panel's CSP blocks; a small toast host in its pattern instead (`toast.tsx`; 4 s, bottom right at `--toast-bottom`, non-critical only)
- shell | critical / warning banners | **layout of Alert 34383** (appica-dev), code read: its `alert-utils` helpers weren't returned, and its blur/scale exit is wrong for risk banners, so our own grid in the same layout with no motion, a 2-px left rule and the state icon (`banner.tsx`)
- shell | Ctrl K hint and keycaps | **used: Combo Kbd 28308** (ai2), code read: the ComboKbd variant only (`kbd.tsx`)
- shell | keyboard shortcut sheet (`?`) | **used: Keyboard Shortcuts 26919** (laziekiki), code read: Windows names only, the dialog z-level (50) so the critical layer stays above, an emergency-page link in its footer (`keyboard-shortcuts.tsx`)
- shell | floating dock + minimize circle | **used: Floating Dock 18014** (picked), moved from the mock unchanged (`floating-dock.tsx`)
- shell | splash, empty states | **used: Dot Matrix Text 25484** (picked), moved from the mock unchanged (`dot-text.tsx`); the splash sits below the top bar so KILL / FLAT ALL work from the first frame
- shell | KILL and FLAT ALL in the top bar, /emergency, the fallback page | our own (MASTER §10: no decorative component on risk controls; `/emergency` is plain HTML from the Python server, `src/nqbot/ui/templates/`)
- CSP note: Radix's scroll lock (react-style-singleton) and Motion's `popLayout` insert a `<style>` element at run time; both carry a per-launch style nonce (`get-nonce` 1.0.1, `MotionConfig nonce`), so the CSP stays `default-src 'self'` with no `unsafe-inline`

### Stage 1 changes (owner 2026-11-15)
- shell | KILL confirm dialog (replaces the Radix Alert Dialog 702) | searched ("dark destructive confirm alert dialog danger"): **used: Alert Dialog Layout 28277** (ai2), code read: its split layout, self-contained (no Radix), focus on the confirm button, Tab trap, no entrance animation, crit tokens (`confirm-dialog.tsx`); Astryx Alert Dialog 45177 rejected (a wrapper over `@astryxdesign/core` plus a CSS file not returned); Base Alert Dialog 26651 rejected earlier (3D blur entrance)
- shell | notification bell + history | searched ("notification bell dropdown history list unread badge"): **used: Notification Panel 27135** (uvain), code read: its demo's inline base64 portraits not used (the CSP would block them anyway); rows, tabs and surface adapted (`NotificationBell.tsx`); Illos Notifications Popover 44490 rejected (an illustration under a separate licence)
- shell | date block (bottom-left) | from the step-14a mock's sidebar foot (our own); no component needed
- shell | banner motion and auto-fade | our own on Motion (`shell/Banners.tsx`), the banner layout as before (34383)

### Stage 2 (2026-11-15): Home, Accounts, Settings
- home | the scene, nodes, lines, opening sequence | moved from the step-14a mock (order flow, Pixel Flow Field 30145 prototype); the flowing-dash keyframes moved from an inline `<style>` into styles.css; node numbers from /api/home ("—" until their data exists)
- accounts | tilt account card | **used: Tilt Card 12244** (picked), moved from the mock; a `disabled` switch added (Calm, an account in danger)
- accounts | hold-to-confirm delete | **used: Hold to Confirm 23527** (ddoemonn), code read: 1.2-s hold, crit sweep, Calm snaps, enabled only once the typed name matches (`hold-to-confirm.tsx`); 28348 not used
- accounts | unsaved-changes bar | **used: Unsaved changes 9576** (stvenchg), code read: its Button/cva replaced by our classes, change count, blocked reason, above the dock (`unsaved-changes.tsx`)
- accounts, settings | segmented controls | **used: Segmented Control 44919** (vamsiy78), code read: look and spring pill kept; its `radix-ui` RadioGroup and a missing direction helper replaced by a plain radiogroup with arrow keys (`form-controls.tsx`)
- accounts, settings | switches | **used: Switch 11886** (jshguo), code read: look kept on a native `role="switch"` button instead of @radix-ui/react-switch
- accounts | preset picker | **pattern of Combobox 34727** (halaska-studio), code read: its inline token palette and undefined keyframe replaced; options grouped by firm with a SAVED tag (`preset-picker.tsx`)
- accounts | drawdown gauge / meter | Meter 38114 (redrob-labs) code read but its `meter-utils` files weren't returned: not used; the card shows the rule in words and lives as pips until live balances exist (step 22)
- accounts | lives pips | **from Progress Bar 9659** (jatin-yadav05), code read: segments only, no hover wobble or demo slider (`LivesPips`)
- accounts | number inputs with units, text inputs, field + inline errors | our own (`form-controls.tsx`); searched: coss Number Field 28180 needs Base UI, Unit Field's helpers weren't returned
- accounts, settings | save-as-preset, raising-risk, leave-without-saving dialogs | the shadcn / originui Dialog pattern on @radix-ui/react-dialog (already in the tree; nonce-carrying scroll lock) (`dialog.tsx`)
- settings | sliders with labelled marks and a cap marker | **used: Marks Slider 29217** (ai2, LabelsSlider), code read: no packages; a cap marker and hatched track past the cap, thumb 1.15× while dragged in Full only (`slider.tsx`)
- settings | risk-profile cards | our own radio cards in the Radio Group in Card pattern (28339), the KPI tile's number style
- settings | section list (scroll spy) | our own (a scroll listener on the page; ddoemonn Scroll Spy's pattern)
- settings | locked owner-rule rows | our own, the Label Disabled 29340 pattern with a static Lock icon
- shell | the dot-matrix footer on Accounts and Settings | **used: Halftone Wordmark Footer 37628** (picked), moved from the mock; its scoped `<style>` moved into styles.css (CSP)
- shell | top-bar account switcher, a real dropdown (owner 2026-11-15) | **structure of Workspaces 4511** (efferd), code read: no network calls (its demo's CDN logos not used); trigger, titled list with a check on the current row, footer actions, on our own portal instead of Radix Popover / Avatar (`shell/AccountSwitcher.tsx`); Select with Avatar 25130 not used
- shared | hints (styled tooltips; no native `title` anywhere, owner 2026-11-15) | **look of originui Tooltip 207** (dark variant), code read 2026-11-15: Radix only, no network calls, no embedded instructions; built as one delegated layer over `data-hint` (`components/ui/hint-layer.tsx`) instead of wrapping each trigger in Radix providers
- performance | built in stage 3 (2026-11-15) | **used:** KPI Tile 40086, Animated Tabs 24930, Spotlight 35180, Border Beam Panel 23408 (its inline <style> moved into styles.css), the Halftone Wordmark Footer 37628, the P&L calendar after the reference layout with the Activity Heatmap Calendar 36571 / hover card 222 / sheet 35214 patterns, the trades table after shadcn Data Table 1050 with the aria-sort header of 33506 and a column chooser; Lightweight Charts (npm) for equity and drawdown, its attribution logo's one <style> allowed by hash
