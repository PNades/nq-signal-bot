"""Which rclone runs (owner 2026-10-30): status-push failed with "[WinError 2]" in a PowerShell opened before winget
installed rclone. Order: config/local.yaml rclone_path, else PATH, else the WinGet Links folder, else winget's package
folder; the path is printed; none found = a clear message with the fix."""

from __future__ import annotations

import pytest

from nqbot import keys_check, preflight, rclone_path, status_push
from nqbot.backup import BackupRefused, SkylitBackupJob
from nqbot.config import load_settings


@pytest.fixture
def env(monkeypatch, tmp_path):
    """No rclone on PATH and an empty LOCALAPPDATA unless a test puts one there."""
    monkeypatch.setattr(rclone_path.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return tmp_path / "localappdata" / "Microsoft" / "WinGet" / "Links"  # .parent / "Packages": winget's packages


def _exe(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rclone.exe").write_bytes(b"")
    return folder / "rclone.exe"


def test_local_yaml_rclone_path_wins(settings, env, tmp_path, monkeypatch):
    mine = _exe(tmp_path / "my rclone")
    _exe(env)
    monkeypatch.setattr(rclone_path.shutil, "which", lambda name: r"C:\on\path\rclone.exe")
    assert rclone_path.find_rclone({**settings, "rclone_path": str(mine)}) == (
        str(mine), "from config/local.yaml rclone_path")


def test_a_wrong_local_yaml_rclone_path_stops_clearly(settings, env, tmp_path):
    _exe(env)  # never silently used instead of the path the owner wrote
    with pytest.raises(rclone_path.RcloneNotFound, match="rclone_path .*config/local.yaml"):
        rclone_path.find_rclone({**settings, "rclone_path": str(tmp_path / "nope" / "rclone.exe")})


def test_path_is_used_next(settings, env, monkeypatch):
    _exe(env)
    seen = []
    monkeypatch.setattr(rclone_path.shutil, "which", lambda name: seen.append(name) or r"C:\on\path\rclone.exe")
    assert rclone_path.find_rclone(settings) == (r"C:\on\path\rclone.exe", "found on PATH")
    assert seen == ["rclone"]  # backup.rclone_exe


def test_winget_links_when_this_shells_path_is_stale(settings, env):
    exe = _exe(env)
    assert rclone_path.find_rclone(settings) == (str(exe), "WinGet Links folder; not on this shell's PATH")
    assert rclone_path.rclone_line(settings) == (True, f"rclone: {exe} (WinGet Links folder; not on this shell's PATH)")


def test_winget_package_folder_when_there_is_no_links_shim(settings, env):
    """The PC (2026-10-30): winget put rclone in Packages and on the user PATH, with no Links shim."""
    pkg = env.parent / "Packages" / "Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe"
    _exe(pkg / "rclone-v1.9.0-windows-amd64")
    newest = _exe(pkg / "rclone-v1.75.1-windows-amd64")  # 1.75 > 1.9 by version, not by name
    assert rclone_path.find_rclone(settings) == (str(newest), "winget package folder; not on this shell's PATH")
    links = _exe(env)  # a Links shim comes first
    assert rclone_path.find_rclone(settings)[0] == str(links)


def test_none_found_names_the_fix(settings, env):
    with pytest.raises(rclone_path.RcloneNotFound) as e:
        rclone_path.find_rclone(settings)
    msg = str(e.value)
    assert msg.startswith("rclone not found") and "winget install Rclone.Rclone" in msg and "rclone_path" in msg
    assert "WinError" not in msg


def test_local_yaml_accepts_rclone_path(tmp_path):
    from nqbot.config import CONFIG_DIR

    (tmp_path / "settings.yaml").write_text((CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "local.yaml").write_text("rclone_path: 'C:\\tools\\rclone.exe'\n", encoding="utf-8")
    assert load_settings(tmp_path)["rclone_path"] == "C:\\tools\\rclone.exe"


def test_status_push_without_rclone_says_so_and_keeps_the_local_copy(paths, settings, env):
    out: list[str] = []
    code = status_push.push(paths, settings, clock=lambda: ([], []), echo=out.append, host="PC1")
    assert code == 1 and (paths.log_dir / "status_PC1.txt").exists()
    assert out[-1].startswith("status-push: rclone not found") and "WinError" not in out[-1]


def test_status_push_prints_the_rclone_it_uses(paths, settings, env, monkeypatch):
    from nqbot import backup

    exe = _exe(env)
    used = []

    class Fake:
        def __init__(self, path):
            used.append(path)

        def quick(self, args):
            return 0, ""

    monkeypatch.setattr(backup, "SubprocessRclone", Fake)
    out: list[str] = []
    assert status_push.push(paths, settings, clock=lambda: ([], []), echo=out.append, host="PC1") == 0
    assert used == [str(exe)] and f"rclone: {exe} (WinGet Links folder; not on this shell's PATH)" in out


def test_backup_without_rclone_is_refused_clearly(paths, settings, env):
    from nqbot.jobs.runner import Ctx

    (paths.raw_dir / "skylit").mkdir(parents=True, exist_ok=True)
    (paths.raw_dir / "skylit" / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
    ctx = Ctx(paths=paths, settings=settings, out_dir=paths.data_dir / "jobs" / "skylit_backup", low_memory=True,
              max_workers=1, log=None, deadline=None, should_stop=lambda: None)
    with pytest.raises(BackupRefused, match="rclone not found"):
        SkylitBackupJob().chunks(ctx)


def test_preflight_lists_the_rclone_path_or_warns():
    base = dict(key_exists=lambda k: False, read=lambda k, n: "2099-01-01T00:00:00Z", on_ac=lambda: True,
                ssd_ok=lambda: (True, ""), drops_since=lambda since: 0, last_check=None, has_battery=lambda: False)
    from datetime import datetime, timezone

    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    rep = preflight.run_checks(now, **base, rclone=lambda: (True, r"rclone: C:\x\rclone.exe (found on PATH)"))
    assert r"rclone: C:\x\rclone.exe (found on PATH)" in rep["ok"] and rep["warnings"] == []
    rep = preflight.run_checks(now, **base, rclone=lambda: (False, "rclone not found: ..."))
    assert rep["warnings"] == ["rclone not found: ..."]


def test_keys_check_lists_the_rclone_path_and_a_missing_one_is_not_a_key_failure(settings, env):
    from nqbot.secrets import MissingSecret

    def no_env(name):
        raise MissingSecret(name)

    res = {r.name: r for r in keys_check.run_checks(settings, env=no_env, env_names=lambda: [])}
    assert res["rclone"].ok is None and res["rclone"].detail.startswith("rclone not found")
    exe = _exe(env)
    res = {r.name: r for r in keys_check.run_checks(settings, env=no_env, env_names=lambda: [])}
    assert res["rclone"].ok is True and res["rclone"].detail == f"rclone: {exe} (WinGet Links folder; not on this shell's PATH)"
