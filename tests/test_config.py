import pytest

from nqbot.config import CONFIG_DIR, ConfigError, load_accounts, load_features, load_settings, load_yaml


def test_settings_start_values():
    s = load_settings()
    assert s["session"]["flat_by"] == "15:59"
    assert s["min_minutes_before_flat"] == 14
    assert s["paths"]["root"] is None  # set at step 4b
    assert s["months"] is None  # set at step 4b
    assert s["blackout_before_min"] == 30 and s["blackout_after_min"] == 60
    assert s["ops"]["job_lock_window"] == "08:30-16:15"


def test_features_and_accounts_load():
    f = load_features()
    assert f["ib_fib_start_minute"] == 20 and f["ib_lock_minute"] == 30
    assert f["min_node_pct_of_king"] == 0.25
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
