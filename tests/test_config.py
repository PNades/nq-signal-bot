import pytest

from nqbot.config import CONFIG_DIR, ConfigError, load_accounts, load_features, load_settings, load_yaml


def test_settings_start_values():
    s = load_settings()
    assert s["session"]["flat_by"] == "15:59"
    assert s["min_minutes_before_flat"] == 14
    assert s["paths"]["root"] == "nqbot/"  # set at step 4b (2026-10-06)
    assert s["months"][1] == "2025-10" and len(s["months"]) == 12  # set at step 4b (2026-10-06)
    assert s["blackout_before_min"] == 30 and s["blackout_after_min"] == 60
    assert s["ops"]["job_lock_window"] == "08:30-16:15"
    # Step-9 gameplan parser (§3.4): model name and cost-estimate prices live in config, not code (rule 5).
    assert s["gameplan_model"] == "claude-opus-5-5" and s["gameplan_effort"] == "medium"
    assert s["gameplan_max_tokens"] == 16000
    assert s["gameplan_usd_per_mtok"] == {"input": 4.0, "output": 20.0}
    assert s["gameplan_max_usd_per_file"] == 1.00
    assert s["gameplan_ny_am_end"] == "12:00"  # F8 scenario set by time (owner 2026-10-07)
    # Skylit backup (§11 Storage, owner 2026-10-05): rclone remote and Drive folder.
    assert s["backup"] == {"rclone_remote": "gdrive", "drive_folder": "NQ bot backup", "rclone_exe": "rclone"}


def test_features_and_accounts_load():
    f = load_features()
    assert f["ib_fib_start_minute"] == 15 and f["ib_lock_minute"] == 30 and f["ib_redraw_minute"] == 60
    assert f["min_node_pct_of_king"] == 0.25
    # golden zone (§5.8, F10): upper fib 0.79 (owner 2026-10-14), the zone runs 0.5 .. the upper fib
    assert f["golden_fib_levels"] == [0.5, 0.618, 0.705, 0.79]
    assert f["golden_zone_upper_fib"] == 0.79 == max(f["golden_fib_levels"]) and min(f["golden_fib_levels"]) == 0.5
    assert f["features_version"] == 6
    # mapping-dislocation guard (F1.8, owner 2026-10-14): fixed from data quality, never tuned
    m = f["mapping"]
    assert (m["disl_guard_pct"], m["disl_guard_window_sec"], m["disl_guard_confirm_sec"], m["disl_guard_hold_sec"]) == (
        0.002, 120, 10, 60)
    assert m["disl_guard_maps"] == ["QQQ", "SPY", "SPX"]
    # expected-move (sigma) levels (F3.11, owner 2026-10-14): ±1σ and ±2σ only, a chart-line level type
    assert f["em_level_sigmas"] == [-2, -1, 1, 2]
    assert "em_level" in f["chart_level_half_width"] and "em_level" in f["flip_margin"]
    assert "em_level" in f["tune_grids_13"]["detectors"]["flip_margin"]["groups"]["chart_lines"]
    assert load_accounts() == []


def _write(tmp_path, local: str):
    (tmp_path / "settings.yaml").write_text((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "local.yaml").write_text(local, encoding="utf-8")


def test_local_yaml_overrides_machine_resources(tmp_path):
    _write(tmp_path, "max_workers: 3\nlow_memory: false\n")
    s = load_settings(tmp_path)
    assert s["max_workers"] == 3 and s["low_memory"] is False


def test_local_yaml_refuses_result_changing_keys(tmp_path):
    _write(tmp_path, "max_workers: 3\nstop_buffer: 2\n")
    with pytest.raises(ConfigError, match="stop_buffer"):
        load_settings(tmp_path)


def test_local_example_has_only_allowed_keys():
    assert set(load_yaml(CONFIG_DIR / "local.example.yaml")) == {"max_workers", "max_fold_mem_gb", "low_memory"}


def test_step6_approved_values_set():
    """Step 6 (2026-10-05): F15 start values and F16 grids are in config, each grid holds its start value."""
    s, f = load_settings(), load_features()
    assert f["zone_proximity"] == 5 and f["confluence_tol"] == 10 and f["lean_window"] == 1
    assert "roll_window_min" not in f
    assert len(f["daytype_inputs"]) <= f["daytype_max_features"]
    g = s["threshold_grid"]
    assert len(g["min_ev_r"]) <= 5 and len(g["min_target_prob"]) <= 5
    assert s["tuning_start"]["min_ev_r"] in g["min_ev_r"]
    assert s["tuning_start"]["min_target_prob"] in g["min_target_prob"]
    assert len(s["be_trigger"]) <= 4 and len(s["style_set_grid"]) <= 6
    assert s["recency_halflife_months"]["start"] in s["recency_halflife_months"]["grid"]
    assert len(s["ablation_groups"]) == 9
    for grids in (f["tune_grids_13"]["detectors"], f["tune_grids_19"]["detectors_a"], f["tune_grids_19"]["detectors_b"]):
        for key, values in grids.items():
            if key != "flip_margin":
                assert f[key] in values, key
    assert s["stop_buffer"] in f["tune_grids_13"]["execution"]["stop_buffer"]
    ex13, ex19 = f["tune_grids_13"]["execution"], f["tune_grids_19"]["execution"]
    for style in ("c", "d"):
        assert s["entry_ttl_sec"][style] in ex13["entry_ttl_sec"]
        assert s["entry_order_type"][style] in ex13["entry_order_type"]
    for style in ("a", "b"):
        assert s["entry_ttl_sec"][style] in ex19["entry_ttl_sec"]
        assert s["entry_order_type"][style] in ex19["entry_order_type"][style]
    assert f["abs_stop_buffer"] in ex19["abs_stop_buffer"]
    assert f["abs_stop_window_sec"] in ex19["abs_stop_window_sec"]


def test_flip_margin_and_width_keys_agree():
    f = load_features()
    groups = f["tune_grids_13"]["detectors"]["flip_margin"]["groups"]
    grouped = [t for members in groups.values() for t in members]
    assert sorted(grouped) == sorted(f["flip_margin"]) and len(grouped) == len(set(grouped))
    assert set(f["chart_level_half_width"]) == set(f["chart_level_half_width_cap"])
    assert set(f["chart_level_half_width"]) <= set(f["flip_margin"])
    assert all(v in f["tune_grids_13"]["detectors"]["flip_margin"]["values"] for v in f["flip_margin"].values())


def test_q39_calendar_rows_present():
    from nqbot import calendar
    rows = [(str(e["date"]), e["time_et"]) for e in calendar.load()["events"] if e["name"] == "Fed Chair"]
    assert ("2026-04-21", "10:00") in rows and ("2026-05-13", "14:40") in rows


def test_step7_skylit_keys():
    s, f = load_settings(), load_features()
    assert set(s["skylit_symbols"]) == {"QQQ", "NDXP", "SPY", "SPX"}
    assert s["skylit_symbols"]["SPX"] == "SPXW"  # the 0DTE S&P root (§3.1)
    assert s["skylit_metric"] == "gamma" and s["skylit_max_expirations"] == 1
    assert f["dp_tickers"] == ["QQQ", "SPY"] and f["dp_min_notional"] == 1_000_000
    # Session D (owner decisions 2 and 4, 2026-10-06): failures in a row before the backfill stops, and the credits
    # it leaves for the step-7 checks running alongside.
    assert s["skylit_max_failures_in_row"] == 5 and s["skylit_alongside_reserve_credits"] == 1000
    assert s["skylit_dropped_extra_tries"] == 3 and s["skylit_dropped_retry_credits_per_night"] == 500


def test_step8b_mapping_values():
    # Owner 2026-10-08 (step 8b, SPEC §5.1, F1.1/F1.7/F7); NDXP and SPY's live offset wait for the recordings.
    m, s = load_features()["mapping"], load_settings()
    assert m["ratio_window_sec"] == {"QQQ": 60, "SPY": 60, "SPX": 0, "NDXP": None}
    assert m["ratio_gap_reset_sec"] == 10
    assert str(m["skylit_stamp_change_date"]) == "2026-03-17"
    assert m["board_content_offset_ms"]["before_stamp_change"] == {"QQQ": 740, "SPY": 840, "SPX": -80, "NDXP": None}
    assert m["board_content_offset_ms"]["from_stamp_change"] == {"QQQ": -60, "SPY": -60, "SPX": -80, "NDXP": None}
    assert m["board_content_offset_ms"]["live"] == {"QQQ": -60, "SPY": -60, "SPX": -80, "NDXP": None}  # owner 2026-10-19
    assert (m["ndxp_r_method"], m["ndxp_k_sessions"]) == ("qqq_k", 20)  # owner 2026-10-19 (ndxp-select)
    assert s["stop_buffer_floor"] == {"QQQ": 4.25, "NDXP": 6.25, "chart": 0}  # frozen, owner 2026-10-19 (Q8b-frozen)
    assert load_features()["mapping"]["atlas_match_tol"] == {"NQ": 4.05, "ES": 0.80}  # frozen, owner 2026-10-19


def test_step7_measured_values_and_tempest_pre_open():
    # Owner 2026-10-09: map_latency_sec from the 10-07 recording (p99.9 0.585 s); tempest_lag_sessions decided 1 by the
    # 10-12 finality check (0 cells changed); a Tempest copy counts only from the pre-open (store constant = preopen_start).
    from datetime import time

    from nqbot.clients.skylit_store import TEMPEST_FINAL_FROM_ET

    s, f = load_settings(), load_features()
    assert s["map_latency_sec"] == 1.16  # owner 2026-10-19 (arrival - tau p99.9)
    assert f["tempest_lag_sessions"] == 1  # step 7 decided 2026-10-12
    assert f["dp_lag_sessions"] == 1  # start value until late-prints-compare
    assert time.fromisoformat(s["preopen_start"]) == TEMPEST_FINAL_FROM_ET
