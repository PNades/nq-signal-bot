"""Owner 2026-10-14: the pre-session check warns before a restart or an unpaused Windows Update can cost a session
(10-14: a 04:50 update restart left Windows at the sign-in screen and the recording never ran)."""

from __future__ import annotations

from datetime import datetime, timezone

from nqbot import preflight as pf

NOW = datetime(2026, 10, 15, 12, 20, tzinfo=timezone.utc)  # 08:20 ET


def _run(keys=(), pause=None, ac=True, ssd=(True, "writable"), drops=0):
    values = {(pf.PAUSE_KEY, n): pause for n in pf.PAUSE_VALUES} if pause else {}
    return pf.run_checks(NOW, key_exists=lambda k: k in keys, read=lambda k, n: values.get((k, n)),
                         on_ac=lambda: ac, ssd_ok=lambda: ssd, drops_since=lambda since: drops, last_check=None)


def test_all_clear():
    rep = _run(pause="2026-10-20T17:47:56Z")
    assert rep["warnings"] == [] and any("paused until 2026-10-20 13:47 ET" in x for x in rep["ok"])


def test_restart_pending_and_not_paused_warn():
    rep = _run(keys=(pf.REBOOT_KEYS[0],))
    assert any("restart is pending" in w for w in rep["warnings"])
    assert any("not paused" in w for w in rep["warnings"])


def _win(build, now=NOW):
    return pf.run_checks(now, key_exists=lambda k: False, read=lambda k, n: None, on_ac=lambda: True,
                         ssd_ok=lambda: (True, ""), drops_since=lambda since: 0, last_check=None,
                         windows_build=lambda: build)


def test_windows_10_past_end_of_support_is_info_not_a_warning():
    """Owner 2026-10-31: the home PC runs Windows 10 past end of support (can't upgrade, can't pause): no updates and
    no update restarts come, so no 'not paused' warning and no exit 5; Windows 11 (the laptop) keeps the check."""
    after = datetime(2026, 10, 31, 11, 40, tzinfo=timezone.utc)
    rep = _win(19045, after)
    assert rep["warnings"] == [] and len(rep["info"]) == 1
    assert "Windows 10 (build 19045) is past end of support" in rep["info"][0] and "no pause is needed" in rep["info"][0]
    for build in (22631, 26100, None):  # Windows 11 (the laptop), or the version can't be read
        rep = _win(build, after)
        assert rep["info"] == [] and any("not paused" in w for w in rep["warnings"])
    # Windows 10 on its last update day (the consumer ESU year's end, 2026-10-13 ET) still warns
    rep = _win(19045, datetime(2026, 10, 14, 3, 59, tzinfo=timezone.utc))  # 23:59 ET on 10-13
    assert any("not paused" in w for w in rep["warnings"])
    assert _win(19045, datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc))["warnings"] == []  # 00:00 ET on 10-14


def test_windows_10_still_warns_on_a_pending_restart():
    rep = pf.run_checks(datetime(2026, 10, 31, 11, 40, tzinfo=timezone.utc), key_exists=lambda k: k == pf.REBOOT_KEYS[0],
                        read=lambda k, n: None, on_ac=lambda: True, ssd_ok=lambda: (True, ""),
                        drops_since=lambda since: 0, last_check=None, windows_build=lambda: 19045)
    assert len(rep["warnings"]) == 1 and "restart is pending" in rep["warnings"][0]


def test_the_info_line_is_printed(settings, tmp_path, monkeypatch):
    from nqbot import clock, power

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(pf, "_hklm_key_exists", lambda k: False)
    monkeypatch.setattr(pf, "_hklm_value", lambda k, n: None)
    monkeypatch.setattr(pf, "_on_ac", lambda: True)
    monkeypatch.setattr(pf, "_has_battery", lambda: False)
    monkeypatch.setattr(pf, "_windows_build", lambda: 19045)
    monkeypatch.setattr(pf, "datetime", type("D", (datetime,), {"now": staticmethod(
        lambda tz=None: datetime(2026, 10, 31, 11, 40, tzinfo=timezone.utc))}))
    monkeypatch.setattr(power, "lid_on_battery_warning", lambda: None)
    monkeypatch.setattr(clock, "clock_checks", lambda s: ([], ["clock ok"]))
    out: list[str] = []
    rep = pf.preflight(settings, echo=out.append)
    assert not any("not paused" in w for w in rep["warnings"])
    assert any(line.startswith("info Windows 10 (build 19045) is past end of support") for line in out)


def test_pause_ending_during_the_session_or_soon_warns():
    rep = _run(pause="2026-10-15T17:47:56Z")  # 13:47 ET today, mid-session
    assert any("before today's session ends" in w for w in rep["warnings"])
    rep = _run(pause="2026-10-16T17:47:56Z")  # tomorrow: within PAUSE_WARN_DAYS
    assert any("within 2 days" in w for w in rep["warnings"])


def test_battery_ssd_and_drops_warn():
    rep = _run(pause="2026-11-20T00:00:00Z", ac=False, ssd=(False, "marker not found"), drops=2)
    w = " | ".join(rep["warnings"])
    assert "not on AC power (battery)" in w and "SSD not usable: marker not found" in w and "since the last check: 2" in w


def test_drops_counted_from_the_event_log(tmp_path):
    log = tmp_path / "ssd_events.log"
    log.write_text("2026-10-12T03:51:13+00:00 pid 1 atomic write D:\\x: device dropped (OSError)\n"
                   "2026-10-12T03:51:13+00:00 pid 1 atomic write D:\\x: retry failed\n"
                   "2026-10-15T01:00:00+00:00 pid 2 append D:\\y: device dropped (OSError)\n", encoding="utf-8")
    assert pf.drops_since(log, datetime(2026, 10, 14, tzinfo=timezone.utc)) == 1
    assert pf.drops_since(log, datetime(2026, 10, 1, tzinfo=timezone.utc)) == 2


def test_lid_on_battery_must_be_do_nothing():
    """Owner 2026-10-19 (§13 Q-power): ES_DISPLAY_REQUIRED doesn't stop a lid close, so preflight checks it."""
    from nqbot import power

    def query(dc):
        return lambda sub, setting: (f"    Current AC Power Setting Index: 0x00000000\n"
                                     f"    Current DC Power Setting Index: 0x{dc:08x}\n")

    assert power.lid_on_battery_warning(query(0)) is None
    w = power.lid_on_battery_warning(query(1))
    assert "closing the lid on battery would put the laptop to sleep (value 1)" in w and "On battery = Do nothing" in w
    assert "couldn't read" in power.lid_on_battery_warning(lambda sub, setting: None)
    values = {}
    rep = pf.run_checks(NOW, key_exists=lambda k: False, read=lambda k, n: values.get((k, n)), on_ac=lambda: True,
                        ssd_ok=lambda: (True, ""), drops_since=lambda since: 0, last_check=None,
                        lid_warning=lambda: power.lid_on_battery_warning(query(1)))
    assert any("closing the lid on battery would" in x for x in rep["warnings"])
    rep = pf.run_checks(NOW, key_exists=lambda k: False, read=lambda k, n: values.get((k, n)), on_ac=lambda: True,
                        ssd_ok=lambda: (True, ""), drops_since=lambda since: 0, last_check=None,
                        lid_warning=lambda: None)
    assert "closing the lid on battery: Do nothing" in rep["ok"]


def test_preflight_with_the_ssd_missing_warns_instead_of_crashing(settings, tmp_path, monkeypatch):
    """Owner 2026-10-29: the SSD-missing branch built `lambda: (False, str(e))` inside the except block; Python unbinds
    `e` when the block ends, so calling it raised NameError. It must be a warning, and the report goes to the
    internal-drive fallback (temporary here, tests/conftest.py)."""
    from nqbot import clock, power, storage

    def missing(settings_):
        raise storage.SSDNotFound("the SSD marker .nqbot_drive was not found on any drive")

    monkeypatch.setattr(storage, "resolve_paths", missing)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(pf, "_hklm_key_exists", lambda k: False)
    monkeypatch.setattr(pf, "_hklm_value", lambda k, n: "2099-01-01T00:00:00Z")
    monkeypatch.setattr(pf, "_on_ac", lambda: True)
    monkeypatch.setattr(pf, "_has_battery", lambda: False)
    monkeypatch.setattr(power, "lid_on_battery_warning", lambda: None)
    monkeypatch.setattr(clock, "clock_checks", lambda s: ([], ["clock ok"]))
    out: list[str] = []
    rep = pf.preflight(settings, echo=out.append)
    assert any("SSD" in w and ".nqbot_drive was not found" in w for w in rep["warnings"])
    assert any(line.startswith("report: ") for line in out)
