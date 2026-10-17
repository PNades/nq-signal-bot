"""Keep-awake and the powercfg warnings (presd_fixes issue 11, SPEC §11 Overnight jobs)."""

import pytest

from nqbot import power

GOOD = "    Current AC Power Setting Index: 0x00000000\n    Current DC Power Setting Index: 0x00000384\n"


def answers(**bad):
    """powercfg text per setting GUID: 0 everywhere, except the CHECKS named in `bad` (by index) -> value or None."""
    by_guid = {c[2]: bad.get(f"c{i}", 0) for i, c in enumerate(power.CHECKS)}

    def query(subgroup, setting):
        v = by_guid[setting]
        return None if v is None else (f"    Current AC Power Setting Index: 0x{v:08x}\n"
                                        "    Current DC Power Setting Index: 0x00000000\n")

    return query


def test_all_fine():
    assert power.power_warnings(answers()) == []


@pytest.mark.parametrize("i, value", [(0, 3600), (1, 10800), (2, 1), (3, 1)])
def test_each_bad_setting_warns(i, value):
    w = power.power_warnings(answers(**{f"c{i}": value}))
    assert len(w) == 1 and power.CHECKS[i][0] in w[0] and power.CHECKS[i][4] in w[0]


def test_unreadable_setting_says_check_by_hand():
    w = power.power_warnings(answers(c2=None))
    assert len(w) == 1 and "couldn't read lid close action" in w[0]


def test_not_windows_no_checks_no_calls(monkeypatch, no_power_calls):
    monkeypatch.setattr(power.os, "name", "posix")
    assert power.power_warnings() == []
    with power.keep_awake():
        pass
    assert no_power_calls == []


def test_keep_awake_cleared_after_an_error(monkeypatch, no_power_calls):
    monkeypatch.setattr(power.os, "name", "nt")
    with pytest.raises(RuntimeError):
        with power.keep_awake():
            raise RuntimeError("chunk failed")
    assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]


def test_non_english_powercfg_is_read_by_position():
    german = ("    Aktueller Wechselstrom-Energieeinstellungsindex: 0x00000e10\n"
              "    Aktueller Gleichstrom-Energieeinstellungsindex: 0x00000000\n")
    w = power.power_warnings(lambda sub, setting: german if setting == power.CHECKS[0][2] else GOOD)
    assert len(w) == 1 and "value 3600" in w[0]


def test_keep_awake_nests_and_every_command_holds_it(monkeypatch, no_power_calls):
    """Owner 2026-10-11: every nqbot command holds it (a screen-off Modern Standby dropped the SSD under step8b);
    `nqbot run` / step7 holding it again inside must not release it early."""
    from nqbot import cli

    monkeypatch.setattr(power.os, "name", "nt")
    with power.keep_awake():
        with power.keep_awake():
            pass
        assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]  # still held
    assert no_power_calls[-1] == power.ES_CONTINUOUS and len(no_power_calls) == 2
    seen = []
    monkeypatch.setattr(cli, "_main", lambda argv: seen.append(list(no_power_calls)) or 0)
    assert cli.main(["status"]) == 0 and seen[0][-1] == power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED


# --- owner 2026-10-17: recordings, jobs and (later) live trading hold ES_SYSTEM_REQUIRED, cleared however they end --

_REAL_API = r'''
import ctypes, json, threading
from nqbot import power

fn = ctypes.windll.kernel32.SetThreadExecutionState
fn.argtypes, fn.restype = [ctypes.c_uint], ctypes.c_uint


def peek():
    """This thread's state (SetThreadExecutionState returns the previous one; put it back)."""
    prev = fn(power.ES_CONTINUOUS)
    fn(prev if prev & power.ES_CONTINUOUS else power.ES_CONTINUOUS)
    return prev


seen = {"before": peek()}
with power.keep_awake():
    with power.keep_awake():
        seen["held"] = peek()
    seen["inner_ended"] = peek()
seen["after"] = peek()
for name, exc in (("crash", RuntimeError), ("ctrl_c", KeyboardInterrupt), ("exit", SystemExit)):
    try:
        with power.keep_awake():
            raise exc()
    except BaseException:
        seen[name] = peek()
with power.keep_awake():
    t = {}
    th = threading.Thread(target=lambda: t.update(other_thread=peek()))
    th.start(); th.join()
    seen.update(t)
print(json.dumps(seen))
'''


@pytest.mark.skipif(power.os.name != "nt", reason="the real Windows call")
def test_the_real_windows_state_is_held_and_cleared_however_the_block_ends():
    """The real SetThreadExecutionState, in a child process (the test process's own calls are blocked by conftest)."""
    import json
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-c", _REAL_API], capture_output=True, text=True, timeout=60, check=True)
    seen = json.loads(out.stdout)
    held, cleared = power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS
    assert seen["held"] == held and seen["inner_ended"] == held  # nested: still held until the outer block ends
    assert {k: seen[k] for k in ("before", "after", "crash", "ctrl_c", "exit")} == dict.fromkeys(
        ("before", "after", "crash", "ctrl_c", "exit"), cleared)
    assert seen["other_thread"] == cleared  # Windows keeps it per thread


def test_each_thread_holds_and_clears_its_own(monkeypatch, no_power_calls):
    import threading

    monkeypatch.setattr(power.os, "name", "nt")
    entered, main_done = threading.Event(), threading.Event()

    def worker():
        with power.keep_awake():
            entered.set()
            main_done.wait(10)

    th = threading.Thread(target=worker)
    with power.keep_awake():
        th.start()
        entered.wait(10)
    main_done.set()
    th.join()
    held = power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED
    # the main thread's block ended while the worker still held its own: each cleared its own thread's state
    assert no_power_calls == [held, held, power.ES_CONTINUOUS, power.ES_CONTINUOUS]


def test_a_recording_holds_it_while_it_runs_and_a_crash_clears_it(settings, drive, monkeypatch, no_power_calls):
    from nqbot import cli

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(power.os, "name", "nt")
    during = []

    def recording(args, *rest):
        assert args.check == "stream-session"
        during.append(list(no_power_calls))
        raise RuntimeError("the recorder crashed")

    monkeypatch.setattr(cli, "_step7_check", recording)
    with pytest.raises(RuntimeError, match="crashed"):
        cli.main(["step7", "stream-session", "--maps", "QQQ"])
    assert during == [[power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]]
    assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]


class _StopsOrCrashes:
    name = "dummy"

    def __init__(self, how):
        self.how = how

    def chunks(self, ctx):
        return ["chunk-000", "chunk-001"]

    def run_chunk(self, chunk_id, ctx):
        from nqbot.jobs.runner import stop_request_path

        if self.how == "crash":
            raise RuntimeError("chunk failed")
        stop = stop_request_path(ctx.paths, self.name)
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("")  # `nqbot stop dummy` from a second terminal


@pytest.mark.parametrize("how", ["stop", "crash"])
def test_a_job_stopped_or_crashed_clears_it(how, settings, paths, log, clock, monkeypatch, no_power_calls):
    from datetime import timedelta

    from nqbot.jobs.runner import run_job

    monkeypatch.setattr(power.os, "name", "nt")
    go = lambda: run_job(_StopsOrCrashes(how), settings, paths, until=clock.now() + timedelta(hours=1), log=log,
                         now=clock.now, sleep=clock.sleep)
    if how == "crash":
        with pytest.raises(RuntimeError):
            go()
    else:
        assert go().status == "stopped"
    assert no_power_calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]
