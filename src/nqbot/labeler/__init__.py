"""The step-13b labeler (SPEC §12 step 13, §5.5b, §6.1-§6.6; feature_spec F7, F11.1-F11.3; owner 2026-11-10, SPEC §13
Q-step13b): pairs, fills and path summaries for entry styles (c) and (d) from the engine stage's decision points
(`events/`) and the session's NQ trades and `mbp-1` top of book. Its own key: the feature-build key + every execution
and cost setting it reads (`params.py`) + this package's code, so its outputs never collide (Q-step13b (1))."""
