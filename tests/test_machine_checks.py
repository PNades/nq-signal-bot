"""Owner 2026-10-28 (PC migration): the clock check, preflight on a machine with no battery, `nqbot keys-check`,
`nqbot status-push` and the wall-time / peak-memory lines of `nqbot test`. No real network, rclone or registry."""

from __future__ import annotations

import io
import sys
from datetime import datetime, timezone

import httpx
import pytest

from nqbot import clock, keys_check, preflight, status_push, testrun
from nqbot.secrets import MissingSecret, Secret


def _reader(tz="Eastern Standard Time", sync="NTP"):
    return lambda path, name: {"TimeZoneKeyName": tz, "Type": sync}[name]


def test_clock_checks_all_good(settings):
    warn, ok = clock.clock_checks(settings, offset_ms=lambda: 40.0, read=_reader(), service=lambda: "running")
    assert warn == [] and len(ok) == 4 and "Windows Time service running" in ok


@pytest.mark.parametrize("state", ["stopped", None])
def test_clock_checks_warn_when_the_time_service_is_not_running(settings, state):
    """Owner 2026-10-31: on the PC w32time was stopped (manual start), so "sync on" was true and nothing synced."""
    warn, ok = clock.clock_checks(settings, offset_ms=lambda: 40.0, read=_reader(), service=lambda: state)
    assert len(warn) == 1 and f"w32time) is {state or 'unreadable'}" in warn[0] and "fix_time_sync.ps1" in warn[0]
    assert "automatic time sync on (NTP)" in ok


@pytest.mark.parametrize("tz,sync,off,needle", [("Pacific Standard Time", "NTP", 0.0, "not Eastern Standard Time"),
                                                 ("Eastern Standard Time", "NoSync", 0.0, "time sync is off"),
                                                 ("Eastern Standard Time", "NTP", 900.0, "+900 ms")])
def test_clock_checks_warn(settings, tz, sync, off, needle):
    warn, _ = clock.clock_checks(settings, offset_ms=lambda: off, read=_reader(tz, sync), service=lambda: "running")
    assert len(warn) == 1 and needle in warn[0]


def test_clock_checks_ntp_unreachable_is_a_warning(settings):
    def fail():
        raise OSError("timed out")

    warn, _ = clock.clock_checks(settings, offset_ms=fail, read=_reader(), service=lambda: "running")
    assert len(warn) == 1 and "couldn't reach" in warn[0]


def _checks(**kw):
    base = dict(key_exists=lambda k: False, read=lambda k, n: "2099-01-01T00:00:00Z", on_ac=lambda: True,
                ssd_ok=lambda: (True, ""), drops_since=lambda since: 0, last_check=None)
    return preflight.run_checks(datetime(2026, 10, 28, 12, tzinfo=timezone.utc), **{**base, **kw})


def test_preflight_skips_the_lid_check_without_a_battery_and_adds_the_clock():
    rep = _checks(has_battery=lambda: False, lid_warning=lambda: pytest.fail("no lid on a desktop"),
                  clock=lambda: (["clock offset +900 ms"], ["time zone Eastern Standard Time"]))
    assert "no battery (desktop): lid check skipped" in rep["ok"]
    assert rep["warnings"] == ["clock offset +900 ms"] and "time zone Eastern Standard Time" in rep["ok"]


def test_preflight_keeps_the_lid_check_on_the_laptop():
    rep = _checks(has_battery=lambda: True, lid_warning=lambda: "lid would sleep")
    assert "lid would sleep" in rep["warnings"]


def _env(values):
    def get(name):
        if name not in values:
            raise MissingSecret(name)
        return Secret(values[name])
    return get


FULL = {"SKYLIT_API_KEY": "s", "DATABENTO_API_KEY": "db-SECRET-key", "ANTHROPIC_API_KEY": "a",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/tok", "DISCORD_BOT_TOKEN": "b",
        "DISCORD_CHANNEL_ID": "42"}
OK = {**{k: (lambda *a, **kw: "ok") for k in ("skylit", "databento", "anthropic", "discord_webhook", "discord_bot")},
      "rclone": lambda: (True, "rclone: C:\\x\\rclone.exe (found on PATH)")}


def test_keys_check_all_ok_and_tp_webhooks_never_called(settings):
    res = keys_check.run_checks(settings, env=_env(FULL), env_names=lambda: ["TP_WEBHOOK_MFFU_25K"], checks=OK)
    assert all(r.ok for r in res if r.name != "TP_WEBHOOK_*")
    tp = [r for r in res if r.name == "TP_WEBHOOK_*"][0]
    assert tp.ok is None and "not called" in tp.detail and "TP_WEBHOOK_MFFU_25K" in tp.detail


def test_keys_check_failure_never_prints_the_key(settings):
    def boom(key):
        raise RuntimeError(f"bad key {key.reveal()}")

    res = keys_check.run_checks(settings, env=_env(FULL), env_names=lambda: [], checks={**OK, "databento": boom})
    bad = [r for r in res if r.name == "DATABENTO_API_KEY"][0]
    assert bad.ok is False and "SECRET" not in bad.detail


def test_keys_check_missing_required_key_fails_optional_is_listed(settings):
    env = {k: v for k, v in FULL.items() if k not in ("ANTHROPIC_API_KEY", "DISCORD_BOT_TOKEN")}
    res = {r.name: r for r in keys_check.run_checks(settings, env=_env(env), env_names=lambda: [], checks=OK)}
    assert res["ANTHROPIC_API_KEY"].ok is False and res["DISCORD_BOT_TOKEN"].ok is None


def test_discord_checks_use_get_only():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "1"})

    t = httpx.MockTransport(handler)
    assert keys_check.check_discord_webhook(Secret("https://discord.com/api/webhooks/1/tok"), t).startswith("ok")
    assert keys_check.check_discord_bot(Secret("b"), "42", t).startswith("ok")
    assert {m for m, _ in seen} == {"GET"}


def test_discord_bad_token_reports_the_status_only():
    t = httpx.MockTransport(lambda r: httpx.Response(401))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        keys_check.check_discord_bot(Secret("secret-token"), None, t)


def test_skylit_check_reads_the_free_account_endpoint(settings):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": {"creditsBalance": 123, "limits": {"requestsPerMinute": 60}}})

    assert keys_check.check_skylit(Secret("s"), settings, httpx.MockTransport(handler)) == "ok (balance 123 credits)"
    assert seen == ["/v1/account"]


class FakeRclone:
    def __init__(self, code=0):
        self.code, self.calls = code, []

    def quick(self, args):
        self.calls.append(args)
        return self.code, "" if self.code == 0 else "remote not found"


def test_status_push_writes_and_copies_one_file(paths, settings):
    rc = FakeRclone()
    out = []
    code = status_push.push(paths, settings, clock=lambda: ([], ["clock offset +5 ms"]), rclone=rc, echo=out.append,
                            host="PC1")
    local = paths.log_dir / "status_PC1.txt"
    text = local.read_text(encoding="utf-8")
    assert code == 0 and "nqbot status on PC1" in text and "queue: empty" in text and "clock ok   clock offset" in text
    assert rc.calls == [["copyto", str(local), "gdrive:NQ bot backup/status/status_PC1.txt"]]


def test_status_push_rclone_failure_keeps_the_local_copy(paths, settings):
    code = status_push.push(paths, settings, clock=lambda: ([], []), rclone=FakeRclone(1), echo=lambda s: None,
                            host="PC1")
    assert code == 1 and (paths.log_dir / "status_PC1.txt").exists()


def test_status_push_shows_the_latest_preflight_warnings(paths, settings):
    from nqbot.storage import atomic_write_json

    atomic_write_json(paths.reports_dir / "preflight" / "preflight_20261028T120000Z.json",
                      {"checked_at": "2026-10-28T12:00:00+00:00", "warnings": ["a restart is pending"], "ok": []})
    text = status_push.snapshot(paths, settings, clock=lambda: ([], []), host="PC1")
    assert "1 warning(s)" in text and "WARN a restart is pending" in text


def test_nqbot_test_reports_wall_time_and_peak_memory_above_the_exit_line(tmp_path):
    script = tmp_path / "stand_in.py"
    script.write_text("import time\nx = bytearray(50_000_000)\ntime.sleep(1.2)\nprint('1 passed')\n", encoding="utf-8")
    got, path = testrun.run_tests(tmp_path / "logs", cmd=[sys.executable, str(script)],
                                  now=datetime(2026, 10, 28, 9, 0, 0), out=io.BytesIO())
    lines = path.read_text(encoding="utf-8").rstrip().splitlines()
    assert got == 0 and lines[-1] == "PYTEST EXIT CODE: 0"
    assert lines[-3].startswith("PYTEST WALL TIME: ") and lines[-2].startswith("PYTEST PEAK MEMORY: ")
    assert float(lines[-2].split(": ")[1].split()[0]) >= 0.04  # the stand-in held 50 MB
