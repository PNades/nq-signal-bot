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
