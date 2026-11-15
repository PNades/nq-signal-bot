# Accounts (SPEC §9b item 1)

> Overrides MASTER.md for this page.

## Layout override
- Top: account cards in a responsive grid (3 across at 1440 px, 2 at 1024 px). Each card: name (mono), firm, status
  badge (EVALUATION / FUNDED / PAUSED / BLOWN, each with icon), drawdown gauge (ring or bar: distance to the limit in $
  and as lives left), risk per trade, today's est. P&L (sign + arrow), DEMO tag when `demo: true`.
- Card hover in Full: 3D tilt 5–8°, scale 1.02, soft cursor spotlight. **No tilt, spotlight or beam on an account in
  danger** (near its limit, unknown, maybe filled): that card is static with a crit outline and its words.
- Edit form: a page (not a modal) with the §9b groups as bordered panels (Account, Targets & caps, Drawdown & losses,
  Firm rules, Position limits, Sizing & trade rules); max width 880 px; a sticky side summary showing the live-as-you-
  type values (daily profit limit, risk per trade, lives, smallest stop with full risk, largest stop for 1 micro).
- Preset picker (searchable select grouped by firm) fills every field; "Save as preset" opens a small dialog (name,
  firm, stage) with validation.

## Forms (from the skill's UX rules)
- Labels always visible; units as suffixes (`$`, `days`, `%`, `contracts`); number inputs with `inputmode="decimal"`;
  segmented controls for small closed choices (static / EOD trailing; off / pause / protect); toggles for yes/no.
- Validate on blur; inline errors under the field with an icon, linked by `aria-describedby`; on a failed save, an error
  summary at the top with links to each field and focus moved to it; saving blocked until fixed; warnings (a bot daily
  loss cap above the firm's DLL) shown in warn, not blocking.
- **Raising risk** dialog (§9b): states new risk in $, lives left, losses that fail the account, the caps' effect;
  saves only after typing `<account> 1/N`. Lowering risk needs no confirmation.
- Unsaved-changes bar fixed at the bottom of the form (Save / Discard, count of changed fields); leaving with unsaved
  changes asks first.
- Delete account / delete preset: hold-to-confirm (1.2 s ring fill) + the account name; never used for kill/flat.

## Effects
Dot-matrix footer at the bottom of the page (scrolling page). Tilt off in Calm.
