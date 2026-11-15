# Settings (SPEC §9b item 2, Strategy settings)

> Overrides MASTER.md for this page.

## Layout override
- Left: a sticky section list (per account: Risk profile, Thresholds, Funded protection, Lives; Global: Gates, Session
  & windows (read-only), Effects & display). Right: grouped panels, max width 880 px.
- Risk profile selector: segmented control or cards (Aggressive / Normal / Conservative / Protect / Custom), each
  showing backtested trades per week, win rate, average R, losing-day odds ("n/a" until step 15).
- Risk $ slider capped at drawdown ÷ N with the cap marked on the track and the exact value in a mono number input
  beside it; threshold sliders appear only for Custom (progressive disclosure).
- Read-only owner rules (session and setup windows, news blackout 30 / 60) shown as locked rows with a `Lock` icon and
  "changed through Claude Code" helper text.
- **Danger zone** card at the end (crit outline): reset an account's sweep (Re-run sweep), clear local UI settings;
  each with its consequence in one line and a confirm dialog.
- Unsaved-changes bar as on Accounts. Changes apply between trades only: the bar says so while a trade is open.

## Effects & display group
Effects: Full / Calm / Auto (Auto = Calm while the live engine runs here in market hours, and when the OS asks for
reduced motion; shows which is active and why). Sound: off (no sounds in the panel).

## Effects
Dot-matrix footer at the bottom (scrolling page). Sliders' thumbs grow 1.15× on drag in Full; instant in Calm.
