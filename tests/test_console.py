"""Owner 2026-10-16: a run's console output survives a pulled SSD (the 10-15 recording died on the print right after
it switched to the internal drive, because its task log was on the SSD)."""

from __future__ import annotations

import errno
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nqbot import console


class _VanishingLog(io.StringIO):
    """A task log on the SSD: fine until `gone`, then every write and flush fails like a pulled drive."""

    def __init__(self):
        super().__init__()
        self.gone = False

    def write(self, s):
        if self.gone:
            raise OSError(errno.EBADF, "Bad file descriptor")
        return super().write(s)

    def flush(self):
        if self.gone:
            raise OSError(errno.EBADF, "Bad file descriptor")


def test_print_after_the_log_vanishes_goes_to_the_fallback_log(tmp_path, monkeypatch):
    monkeypatch.setattr("nqbot.storage.SSD_EVENT_LOG", tmp_path / "events.log")
    (tmp_path / "ssd_gone").write_text("x")  # the SSD's logs folder can't be opened: the pulled drive
    monkeypatch.setenv(console.TASK_LOG_ENV, str(tmp_path / "ssd_gone" / "stream_session_20261015.txt"))
    out, err = _VanishingLog(), _VanishingLog()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with console.resilient_console(log_dir=tmp_path / "fb"):
        print("before")
        out.gone = err.gone = True
        print("  SSD write failed: part 21 and the rest go to C:\\...")  # 10-15: this line killed the recording
        print("a log line", file=sys.stderr)
        sys.stdout.flush()
        print("session ended")
    assert sys.stdout is out and sys.stderr is err  # put back
    assert out.getvalue() == "before\n"
    fb = (tmp_path / "fb" / "stream_session_20261015.txt").read_text(encoding="utf-8").splitlines()
    assert fb[0].startswith("--- ") and "console stdout write failed" in fb[0]
    assert fb[1:] == ["  SSD write failed: part 21 and the rest go to C:\\...", "a log line", "session ended"]
    events = (tmp_path / "events.log").read_text(encoding="utf-8")
    assert events.count("console stdout write failed") == 1 and str(tmp_path / "fb") in events


def test_after_a_remount_the_output_continues_in_the_task_log_itself(tmp_path, monkeypatch):
    """A re-mount kills cmd.exe's handle but the drive is back: a fresh handle on the same task log keeps the log on
    the SSD (rule 13); from the fallback the task log is tried again every REOPEN_EVERY_SEC."""
    monkeypatch.setattr("nqbot.storage.SSD_EVENT_LOG", tmp_path / "events.log")
    task = tmp_path / "logs" / "stream_session_20261016.txt"
    task.parent.mkdir()
    task.write_text("TASK START\n", encoding="utf-8")
    monkeypatch.setenv(console.TASK_LOG_ENV, str(task))
    out = _VanishingLog()
    monkeypatch.setattr(sys, "stdout", out)
    with console.resilient_console(log_dir=tmp_path / "fb"):
        out.gone = True
        print("after the remount")
    lines = task.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "TASK START" and lines[1].startswith("--- ") and lines[2] == "after the remount"
    assert not (tmp_path / "fb").exists()

    # the drive still gone at the failure: fallback, then back on the task log once it opens again
    now = {"t": 0.0}
    task2 = tmp_path / "logs2" / "stream_session_20261016.txt"
    (tmp_path / "logs2").write_text("x")  # a file where the folder should be: can't be opened yet
    monkeypatch.setenv(console.TASK_LOG_ENV, str(task2))
    out = _VanishingLog()
    monkeypatch.setattr(sys, "stdout", out)
    with console.resilient_console(log_dir=tmp_path / "fb2", clock=lambda: now["t"]):
        out.gone = True
        print("during the drop")
        (tmp_path / "logs2").unlink()  # the drive is back
        now["t"] = console.REOPEN_EVERY_SEC - 1
        print("still on the fallback")
        now["t"] = console.REOPEN_EVERY_SEC
        print("back on the SSD")
    fb = (tmp_path / "fb2" / task2.name).read_text(encoding="utf-8").splitlines()
    assert fb[1:] == ["during the drop", "still on the fallback"]
    assert task2.read_text(encoding="utf-8").splitlines() == ["back on the SSD"]
    assert "task log writable again" in (tmp_path / "events.log").read_text(encoding="utf-8")


def test_the_daily_log_file_reopens_after_a_dead_handle(tmp_path):
    import logging

    from nqbot.log import setup_logging

    log = setup_logging(tmp_path)
    (fh,) = [h for h in log.handlers if isinstance(h, logging.FileHandler)]
    log.info("one")

    class Dead:
        def write(self, s):
            raise OSError(errno.EBADF, "Bad file descriptor")

        def flush(self):
            raise OSError(errno.EBADF, "Bad file descriptor")

        def close(self):
            pass

    fh.stream = Dead()  # what an SSD re-mount leaves behind
    log.info("two")
    fh.close()
    (f,) = tmp_path.glob("nqbot_*.log")
    text = f.read_text(encoding="utf-8")
    assert "one" in text and "two" in text
    setup_logging(None)


def test_output_is_dropped_but_never_raises_when_the_fallback_fails_too(tmp_path, monkeypatch):
    monkeypatch.setattr("nqbot.storage.SSD_EVENT_LOG", tmp_path / "events.log")
    monkeypatch.delenv(console.TASK_LOG_ENV, raising=False)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    out = _VanishingLog()
    monkeypatch.setattr(sys, "stdout", out)
    with console.resilient_console(log_dir=blocker / "logs"):
        out.gone = True
        print("one")
        print("two")
    assert "nowhere" in (tmp_path / "events.log").read_text(encoding="utf-8")


def test_without_a_task_log_the_fallback_is_named_console_by_date(tmp_path, monkeypatch):
    monkeypatch.setattr("nqbot.storage.SSD_EVENT_LOG", tmp_path / "events.log")
    monkeypatch.delenv(console.TASK_LOG_ENV, raising=False)
    out = _VanishingLog()
    monkeypatch.setattr(sys, "stdout", out)
    with console.resilient_console(log_dir=tmp_path / "fb"):
        out.gone = True
        print("x")
    (f,) = (tmp_path / "fb").glob("console_*.txt")
    assert f.read_text(encoding="utf-8").splitlines()[-1] == "x"


def test_task_start_waits_for_a_briefly_missing_ssd(monkeypatch, tmp_path):
    """Owner 2026-10-16: the 10-15 08:40 task started inside a 4-s drop and refused; under a task a missing SSD is
    waited for up to SSD_DROP_WAIT_SEC. From a terminal it still refuses at once."""
    from nqbot import cli, storage

    monkeypatch.setattr(storage, "SSD_EVENT_LOG", tmp_path / "events.log")
    calls = {"n": 0}
    sentinel = object()

    def resolve(settings):
        calls["n"] += 1
        if calls["n"] <= 8:  # back after 4 s (8 polls of 0.5 s)
            raise storage.SSDNotFound("marker not found")
        return type("P", (), {"root": tmp_path, "it": sentinel})()

    monkeypatch.setattr(cli, "resolve_paths", resolve)
    monkeypatch.setattr(storage, "_sleep", lambda s: None)
    settings = {"paths": {"root": "nqbot/"}}
    monkeypatch.delenv(console.TASK_LOG_ENV, raising=False)
    with pytest.raises(storage.SSDNotFound):
        cli._resolve_paths(settings)
    calls["n"] = 0
    monkeypatch.setenv(console.TASK_LOG_ENV, str(tmp_path / "late_prints_morning_20261015.txt"))
    assert cli._resolve_paths(settings).it is sentinel
    assert "SSD found after 4.0 s" in (tmp_path / "events.log").read_text(encoding="utf-8")
    calls["n"] = -10**6  # never back: refuses after the wait
    with pytest.raises(storage.SSDNotFound):
        cli._resolve_paths(settings)
    assert "still not found after 30 s" in (tmp_path / "events.log").read_text(encoding="utf-8")


WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "nqbot_task.ps1"


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")
def test_task_wrapper_passes_the_log_path_and_keeps_the_exit_line_when_the_log_is_gone(tmp_path):
    """The program sees NQBOT_TASK_LOG; if the log can't be written at the end, the exit-code line goes to a file of
    the same name in the fallback log folder and the task still returns the program's code."""
    logs, fb = tmp_path / "logs", tmp_path / "fb"
    script = tmp_path / "stand_in.py"
    script.write_text("import os, stat, sys\nlog = os.environ['NQBOT_TASK_LOG']\nprint('log=' + log)\n"
                      "sys.stdout.flush()\nos.chmod(log, stat.S_IREAD)\nsys.exit(4)\n", encoding="utf-8")
    exe = f"{sys.executable} {script}" if " " not in sys.executable + str(script) else f'"{sys.executable}" "{script}"'
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
                        "-LogDir", str(logs), "-LogName", "stream_session", "-NqbotArgs", "x", "-Exe", exe,
                        "-FallbackLogDir", str(fb)], capture_output=True, timeout=120)
    assert r.returncode == 4, r.stderr.decode(errors="replace")
    (log,) = logs.glob("stream_session_*.txt")
    text = log.read_text(encoding="utf-8")
    assert f"log={log}" in text and "TASK EXIT CODE" not in text
    alt = (fb / log.name).read_text(encoding="utf-8").splitlines()
    assert alt[-1] == "TASK EXIT CODE: 4" and "not writable at the end" in alt[0]
    log.chmod(0o666)
