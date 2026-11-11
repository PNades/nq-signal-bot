"""Event builder, labeler and path store (step 13b; SPEC §12 step 13, §13 Q-step13b).

Two stages (owner 2026-11-10, SPEC §13 Q-step13b (1)):
- **engine** (inside the feature-build key, pass 2 of `feature_build`): `triggers` (style (c) firings, F6.1 / F6.2),
  `snapshot` (each decision point's frozen view at its `decision_ts`), `build` (events/, decisions/, decision_rows/,
  level_events/ per session), `zone_compare` (the 13d zone-book count);
- **labeler** (its own key: the build key + every execution and cost setting it reads + its code): the `nqbot.labeler`
  package (pairs/, fills/, paths/ per session).
"""
