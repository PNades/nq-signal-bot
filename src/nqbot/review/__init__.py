"""The setup-card review loop (SPEC §8, §9b item 5; step 14 build stage 4, owner's plan 2026-11-15).

- `roles`: a level's role over the day (ceiling / floor, provisional after a cross, settled at a flip, back at a
  reclaim) from the build's stored level events.
- `sampler`: the §8 step-14 sample of decision points (a spread across stack counts, level types, entry styles and
  times of day, plus setups that lost and a few the owner would never take).
- `cards`: one card's chart, maps and facts from the stored build, cut at its decision time (`pre`) and the rest of
  the path with the outcome (`post`, shown only after the owner answers).
- `store`: review rounds on the SSD (`<data_dir>/reviews/rounds/<round>/`) and the answers
  (`<data_dir>/reviews/answers_<round>.parquet`, §8); development months only (§7 rule 2b).
- `synthetic`: hand-built synthetic days and cards for the tests and `nqbot ui --synthetic` (§13 Q-dryrun13 (12):
  tests never read real data).
"""
