"""`nqbot setup-ssd` (step 4b; presd_fixes issue 6): drive choice, NTFS check, window, month-1 merge, settings.yaml."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from nqbot import setup_ssd as su
from nqbot.config import CONFIG_DIR, load_settings
from nqbot.jobs.runner import ET
from nqbot.months import LedgerMissing, validate
from nqbot.storage import MARKER

OCT_5 = datetime(2026, 10, 5, 12, 0, tzinfo=ET)  # 20 of October 2025's 23 sessions still in reach: no merge
OCT_21 = datetime(2026, 10, 21, 12, 0, tzinfo=ET)  # only 8 left: month 1 = October + November 2025


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(CONFIG_DIR / "settings.yaml", d / "settings.yaml")
    return d / "settings.yaml"


@pytest.fixture
def ssd(tmp_path, monkeypatch):
    d = tmp_path / "E"
    d.mkdir()
    monkeypatch.setattr(su, "drive_info", lambda root: su.Drive(root, "NTFS", 931.0, 900.0))
    return d


def run(cfg, ssd, now=OCT_5, drives=None, **kw):
    lines: list[str] = []
    settings = load_settings(cfg.parent)
    kw.setdefault("drive_arg", str(ssd))
    wrote = su.setup(settings, cfg, now=now, echo=lines.append, candidates=lambda: drives or [ssd],
                     power=lambda: [], **kw)
    return wrote, "\n".join(lines)


def test_window_and_merge():
    s = load_settings()
    assert su.window_first_month(OCT_5.date()) == "2025-10"
    assert su.window_first_month(datetime(2026, 1, 3).date()) == "2025-01"
    assert su.merge_needed("2025-10", OCT_5, s) == (False, 20, 23)
    assert su.merge_needed("2025-10", OCT_21, s) == (True, 8, 23)
    led = su.build_ledger("2025-10", True)
    assert led[1] == ["2025-10", "2025-11"] and led[2] == "2025-12" and led[12] == "2026-10"
    assert validate(led, full=True)[12] == ["2026-10"]
    assert su.build_ledger("2025-10", False)[12] == "2026-09"


def test_dry_run_writes_nothing(cfg, ssd):
    before = cfg.read_text(encoding="utf-8")
    wrote, out = run(cfg, ssd)
    assert not wrote and "nothing written" in out and "no merge" in out
    assert '+months: {1: "2025-10", 2: "2025-11"' in out and '+  root: "nqbot/"' in out
    assert cfg.read_text(encoding="utf-8") == before and not (ssd / MARKER).exists() and not (ssd / "nqbot").exists()


def test_yes_writes_marker_root_and_only_two_lines(cfg, ssd):
    before = cfg.read_text(encoding="utf-8").splitlines()
    wrote, out = run(cfg, ssd, yes=True)
    assert wrote and (ssd / MARKER).exists() and (ssd / "nqbot").is_dir()
    after = cfg.read_text(encoding="utf-8").splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after) and len(changed) == 2  # paths.root and months; every comment kept
    assert all("# set at step 4b" in b for _, b in changed)
    s = load_settings(cfg.parent)
    assert s["paths"]["root"] == "nqbot/" and s["months"][1] == "2025-10" and s["months"][12] == "2026-09"
    wrote, out = run(cfg, ssd, yes=True)  # a re-run finds the marker and changes nothing
    assert not wrote and "nothing to write" in out


def test_merge_written_and_gameplan_note(cfg, ssd):
    wrote, out = run(cfg, ssd, now=OCT_21, yes=True)
    assert wrote and "MERGED with 2025-11" in out and "held back until step 20" in out and "from 2026-08" in out
    assert load_settings(cfg.parent)["months"][1] == ["2025-10", "2025-11"]


def test_drive_choice(tmp_path, ssd):
    other = tmp_path / "F"
    other.mkdir()
    with pytest.raises(su.SetupRefused, match="--drive E:"):
        su.choose_drive(None, lambda: [ssd, other])
    with pytest.raises(su.SetupRefused, match="--drive E:"):  # even one drive: it may be an internal partition
        su.choose_drive(None, lambda: [ssd])
    assert su.choose_drive(str(other), lambda: [ssd, other]) == other
    with pytest.raises(su.SetupRefused, match="not a mounted external drive"):
        su.choose_drive(str(tmp_path / "C"), lambda: [ssd, other])
    (ssd / MARKER).write_text("")
    assert su.choose_drive(None, lambda: [ssd, other]) == ssd  # a re-run finds the marked drive
    with pytest.raises(su.SetupRefused, match="already carries"):
        su.choose_drive(str(other), lambda: [ssd, other])
    with pytest.raises(su.SetupRefused, match="no external drive"):
        su.choose_drive(None, lambda: [])
    assert su._norm("E") == Path("E:\\") and su._norm("e:") == Path("e:\\")


def test_not_ntfs_or_full_refused(cfg, ssd, monkeypatch):
    monkeypatch.setattr(su, "drive_info", lambda root: su.Drive(root, "exFAT", 931.0, 900.0))
    with pytest.raises(su.SetupRefused, match="not NTFS"):
        run(cfg, ssd, yes=True)
    monkeypatch.setattr(su, "drive_info", lambda root: su.Drive(root, "NTFS", 931.0, 10.0))
    with pytest.raises(su.SetupRefused, match="min_free_gb"):
        run(cfg, ssd, yes=True)
    assert not (ssd / MARKER).exists()


def test_existing_root_or_ledger_not_overwritten(cfg, ssd):
    run(cfg, ssd, yes=True)
    with pytest.raises(su.SetupRefused, match="orphan"):
        run(cfg, ssd, root="other/", yes=True)
    with pytest.raises(su.SetupRefused, match="--rewrite-ledger"):
        run(cfg, ssd, now=OCT_21, yes=True)  # the merge now applies, but only --rewrite-ledger changes the ledger


def test_bad_root_refused_before_anything_is_written(cfg, ssd):
    with pytest.raises(su.SetupRefused, match="relative"):
        run(cfg, ssd, root="../elsewhere", yes=True)
    assert not (ssd / MARKER).exists()


def test_rewrite_ledger_when_step_10_starts(cfg, ssd, monkeypatch):
    from nqbot.clients.skylit_backfill import plan_path
    from nqbot.clients.skylit_pull import SkylitPullJob
    from nqbot.storage import Paths

    run(cfg, ssd, yes=True)  # SSD day: no merge yet
    paths = Paths.under(ssd, "nqbot/")
    dates = SkylitPullJob.plan_path(paths, "skylit_sample_days")
    dates.parent.mkdir(parents=True)
    dates.write_text(json.dumps({"dates": ["2026-03-10"]}))
    wrote, out = run(cfg, ssd, now=OCT_21, rewrite_ledger=True)
    assert not wrote and "2026-03: 6 -> 5" in out and "2026-03-10 -> month 5" in out
    wrote, out = run(cfg, ssd, now=OCT_21, rewrite_ledger=True, yes=True)
    assert wrote and load_settings(cfg.parent)["months"][1] == ["2025-10", "2025-11"]
    assert load_settings(cfg.parent)["paths"]["root"] == "nqbot/"
    plan_path(paths).parent.mkdir(parents=True)
    plan_path(paths).write_text("{}")
    with pytest.raises(su.SetupRefused, match="ledger is fixed"):
        run(cfg, ssd, now=OCT_21, rewrite_ledger=True)


def test_first_plan_refused_while_the_merge_is_missing():
    s = {**load_settings(), "months": su.build_ledger("2025-10", False)}
    su.check_month1(s, OCT_5)
    with pytest.raises(su.LedgerStale, match="--rewrite-ledger") as e:
        su.check_month1(s, OCT_21)
    assert isinstance(e.value, LedgerMissing)  # refused cleanly wherever a missing ledger is
    su.check_month1({**s, "months": su.build_ledger("2025-10", True)}, OCT_21)  # merged once: never again


def test_skylit_and_databento_first_plans_check_the_merge(paths, settings, log):
    from nqbot.clients.databento_client import DatabentoClient
    from nqbot.clients.databento_pull import make_plan
    from nqbot.clients.databento_store import DatabentoStore
    from nqbot.config import load_features
    from tests.databento_fakes import FakeHistorical

    from nqbot.clients.skylit_backfill import plan_path

    settings["months"] = su.build_ledger("2025-10", False)
    store = DatabentoStore(paths)
    plan = lambda: make_plan(DatabentoClient(FakeHistorical(), on_call=store.log_call), store, settings,  # noqa: E731
                             load_features(), OCT_21.date(), now=OCT_21, echo=lambda s: None)
    with pytest.raises(su.LedgerStale):
        plan()
    # Once step 10 has a plan the ledger is fixed: step 8 is planned on it, never locked out (review finding 2).
    plan_path(paths).parent.mkdir(parents=True)
    plan_path(paths).write_text("{}")
    assert plan()["items"]


def test_cli_setup_ssd_before_paths_root(settings, monkeypatch, capsys, tmp_path):
    from nqbot import cli, storage

    settings["paths"]["root"] = None
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(storage, "candidate_drives", lambda: [])
    monkeypatch.setattr(su, "candidate_drives", lambda: [])
    assert cli.main(["setup-ssd"]) == cli.EXIT_REFUSED
    assert "no external drive" in capsys.readouterr().err


# --- second review (2026-10-05) ------------------------------------------------------------------------------

def test_windows_style_root_and_crlf_file(cfg, ssd):
    cfg.write_bytes(cfg.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))  # a CRLF working copy
    wrote, _ = run(cfg, ssd, root="nqbot\\", yes=True)  # `--root nqbot\` would be a YAML escape in double quotes
    assert wrote and load_settings(cfg.parent)["paths"]["root"] == "nqbot/" and (ssd / "nqbot").is_dir()
    raw = cfg.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")  # line endings kept


def test_a_databento_plan_alone_does_not_fix_the_ledger(cfg, ssd):
    """§12 step 4b: the ledger is rewritten when step 10 starts; a Databento plan made earlier is just re-run."""
    from nqbot.clients.databento_pull import plan_path
    from nqbot.storage import Paths

    run(cfg, ssd, yes=True)
    p = plan_path(Paths.under(ssd, "nqbot/"))
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    assert not su.ledger_fixed(Paths.under(ssd, "nqbot/"))
    wrote, out = run(cfg, ssd, now=OCT_21, rewrite_ledger=True, yes=True)
    assert wrote and "re-run `uv run nqbot databento plan`" in out and "sample days" not in out


def test_a_merged_month_1_still_short_is_refused(cfg, ssd):
    run(cfg, ssd, yes=True)
    with pytest.raises(su.SetupRefused, match="whole window must move later"):
        run(cfg, ssd, now=datetime(2026, 12, 1, 12, 0, tzinfo=ET), rewrite_ledger=True)


def test_drive_info_and_check_drive(monkeypatch, tmp_path):
    from collections import namedtuple

    part = namedtuple("part", "device mountpoint fstype opts")
    monkeypatch.setattr(su.psutil, "disk_partitions", lambda all=False: [part("X", str(tmp_path), "NTFS", "rw,fixed")])
    info = su.drive_info(tmp_path)
    assert info.fstype == "NTFS" and info.total_gb > 0
    su.check_drive(info, 0)
    with pytest.raises(su.SetupRefused, match="not NTFS"):
        su.check_drive(su.Drive(tmp_path, "FAT32", 931, 900), 0)
    monkeypatch.setattr(su.psutil, "disk_partitions", lambda all=False: [])
    assert su.drive_info(tmp_path).fstype == ""  # unknown: refused by check_drive
