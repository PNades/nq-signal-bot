# Live status (from step 21; SPEC §9b item 3)

> Designed now so the shared components fit it; built at step 21.

## Layout override
- Full-width, 12-column grid, high density. KPI-card row per account (est. P&L vs daily cap, drawdown left, lives,
  distance to payout) with tabs per account; open position with stop/target; last alerts.
- Main chart (shared component, live data source) and the GEX snapshot radial side by side at ≥ 1440 px, stacked below.
- Level ladder (role chip, state, since-cross facts, P(held)) beside the chart.
- Action row: KILL, FLATTEN, PAUSE / RESUME, Resolve, Confirm state, Payout taken, New evaluation; balance and floor entry.

## Rules specific to this page
- **Calm is forced** whenever the live engine runs on this machine during market hours; the chart still updates live.
- Inferred values carry their label: "assumed filled", "maybe filled", "est. P&L since the morning balance", "exit sent".
- Account danger states (unknown, maybe filled, flat-unconfirmed, near the DLL or drawdown) use the crit/warn tokens with
  icon + words, appear at once and never animate in; no tilt, beams or glass on them.
- Status updates use one polite live region per account summary, not one per badge (skill rule: never make every badge
  a competing live region); critical changes use one assertive region.
- No flip-disk matrix on this page for anything risk-related.
