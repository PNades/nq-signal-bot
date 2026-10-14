"""Owner 2026-10-11 (the SSD kept dropping off the USB bus): atomic writes and ledger appends survive a short drop
(one retry, every drop logged), the integrity check finds damaged files, the recording fallback moves back intact,
and the scheduled-task wrapper still runs when the SSD's log folder can't be written."""

from __future__ import annotations

import errno
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nqbot import fallback, storage, verify


@pytest.fixture
def events(tmp_path, monkeypatch):
    log = tmp_path / "local" / "ssd_events.log"
    monkeypatch.setattr(storage, "SSD_EVENT_LOG", log)
    monkeypatch.setattr(storage, "_sleep", lambda s: None)
    return log


def _flaky_fsync(monkeypatch, fails: int, err: OSError):
    real = storage.os.fsync
    left = {"n": fails}

    def fsync(fd):
        if left["n"] > 0:
            left["n"] -= 1
            raise err
        return real(fd)

    monkeypatch.setattr(storage.os, "fsync", fsync)


def test_atomic_write_retries_once_after_a_drop_and_logs_it(tmp_path, monkeypatch, events):
    _flaky_fsync(monkeypatch, 1, OSError(errno.EBADF, "Bad file descriptor"))  # the 2026-03-02 dislocations failure
    target = tmp_path / "ssd" / "nqbot" / "reports" / "x.json"
    storage.atomic_write_bytes(target, b"{}")
    assert target.read_bytes() == b"{}"
    assert not list(target.parent.glob(".*.tmp"))  # the failed try's temp file is gone
    text = events.read_text(encoding="utf-8")
    assert "device dropped" in text and "drive back and stable after" in text and "retry succeeded" in text


def test_a_second_failure_or_another_error_is_not_retried(tmp_path, monkeypatch, events):
    target = tmp_path / "a.bin"
    monkeypatch.setattr(storage, "_probe", lambda p: True)  # the drive looks stable: the second failure is the retry's
    _flaky_fsync(monkeypatch, 2, OSError(errno.EIO, "I/O error"))
    with pytest.raises(OSError):
        storage.atomic_write_bytes(target, b"x")  # retried once only
    assert "retry failed" in events.read_text(encoding="utf-8")
    _flaky_fsync(monkeypatch, 1, OSError(errno.ENOSPC, "No space left"))
    with pytest.raises(OSError):
        storage.atomic_write_bytes(target, b"x")  # a full disk is not a drop
    assert storage.is_device_drop(OSError(errno.ENOSPC, "x"), target) is False
    assert storage.is_device_drop(PermissionError(errno.EACCES, "locked"), target) is False


def test_drive_gone_too_long_gives_up(tmp_path, monkeypatch, events):
    gone = tmp_path / "gone" / "x.bin"  # its folder never appears
    _flaky_fsync(monkeypatch, 1, OSError(errno.EBADF, "x"))
    monkeypatch.setattr(storage, "_probe", lambda p: False)
    with pytest.raises(OSError):
        storage.atomic_write_bytes(gone, b"x")
    assert "giving up" in events.read_text(encoding="utf-8")


def test_append_after_a_drop_is_never_written_twice(tmp_path, monkeypatch, events):
    ledger = tmp_path / "ledger.jsonl"
    storage.append_jsonl(ledger, {"a": 1})
    _flaky_fsync(monkeypatch, 1, OSError(errno.EBADF, "x"))  # the line lands, then the device drops at fsync
    storage.append_jsonl(ledger, {"b": 2})
    assert [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()] == [{"a": 1}, {"b": 2}]


# --- nqbot verify -------------------------------------------------------------------------------------------

def test_verify_finds_damaged_files_and_size_mismatches(tmp_path):
    import zstandard

    root = tmp_path / "nqbot"
    raw = root / "data" / "raw"
    good = raw / "skylit" / "range" / "good.parquet"
    good.parent.mkdir(parents=True)
    pq.write_table(pa.table({"x": list(range(1000))}), good)
    cut = good.with_name("cut.parquet")
    cut.write_bytes(good.read_bytes()[:-40])  # footer gone
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "bad.json").write_text('{"a": ', encoding="utf-8")
    dbn = raw / "databento" / "trades" / "NQZ6"
    dbn.mkdir(parents=True)
    blob = zstandard.ZstdCompressor().compress(b"x" * 100000)
    (dbn / "2026-10-07.dbn.zst").write_bytes(blob)
    (dbn / "2026-10-08.dbn.zst").write_bytes(blob[: len(blob) // 2])  # cut off mid-stream
    rows = [{"kind": "download", "ok": True, "bytes": len(blob) + 1, "day": "2026-10-07", "schema": "trades",
             "symbols": ["NQZ6"]}]
    (raw / "databento" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    res = verify.verify_tree(root, datetime(2020, 1, 1), raw, lambda: True, echo=lambda s: None)
    damaged = sorted(Path(d["path"]).name for d in res.damaged)
    assert damaged == ["2026-10-08.dbn.zst", "bad.json", "cut.parquet"]
    assert [Path(m["path"]).name for m in res.size_mismatch] == ["2026-10-07.dbn.zst"]
    assert res.unreadable == [] and res.checked == 6
    assert "damaged (can't be read back as their type): **3**" in verify.to_markdown(res, root, datetime(2020, 1, 1))


def test_verify_waits_out_a_drop_mid_read(tmp_path, monkeypatch):
    root = tmp_path / "nqbot"
    f = root / "reports" / "ok.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}", encoding="utf-8")
    calls = {"n": 0}
    real = verify.check_file

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EIO, "device gone")
        real(p)

    monkeypatch.setattr(verify, "check_file", flaky)
    res = verify.verify_tree(root, datetime(2020, 1, 1), root / "data" / "raw", lambda: True,
                             sleep=lambda s: None, echo=lambda s: None)
    assert res.damaged == [] and res.unreadable == [] and res.drops_seen == 1


# --- recording fallback ---------------------------------------------------------------------------------------

def test_fallback_move_copies_checks_and_merges(tmp_path):
    src, dst = fallback.fallback_paths("nqbot", tmp_path / "local"), fallback.fallback_paths("nqbot", tmp_path / "ssd")
    part = Path("data/raw/skylit/stream/2026-10-12/session_092700/part_005_boards.parquet")
    (src.root / part).parent.mkdir(parents=True)
    (src.root / part).write_bytes(b"part5")
    same = Path("data/raw/skylit/stream/2026-10-12/session_092700/part_000_boards.parquet")
    for base in (src.root, dst.root):
        (base / same).parent.mkdir(parents=True, exist_ok=True)
        (base / same).write_bytes(b"part0")
    led = Path("data/raw/skylit/ledger_stream_session.jsonl")
    (dst.root / led).write_text('{"a": 1}\n', encoding="utf-8")
    (src.root / led).write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    clash = Path("reports/step7/x.json")
    for base, body in ((src.root, b"1"), (dst.root, b"2")):
        (base / clash).parent.mkdir(parents=True, exist_ok=True)
        (base / clash).write_bytes(body)
    res = fallback.move_fallback(src, dst, echo=lambda s: None)
    assert (dst.root / part).read_bytes() == b"part5" and not (src.root / part).exists()
    assert res.already_there == [str(same)] and res.merged_lines == {str(led): 1}
    assert (dst.root / led).read_text(encoding="utf-8").splitlines() == ['{"a": 1}', '{"b": 2}']
    assert res.conflicts == [str(clash)] and (src.root / clash).exists()  # a differing file stays put


def test_ssd_writable_probe(tmp_path):
    p = storage.Paths.under(tmp_path / "ssd", "nqbot")
    ok, why = fallback.ssd_writable(p)
    assert not ok and "marker" in why
    (tmp_path / "ssd").mkdir(exist_ok=True)
    (tmp_path / "ssd" / storage.MARKER).write_text("")
    assert fallback.ssd_writable(p) == (True, "writable")


def test_stream_session_fallback_target_when_the_ssd_is_missing(tmp_path, monkeypatch, capsys):
    from nqbot import cli

    monkeypatch.setattr(fallback, "FALLBACK_DRIVE", tmp_path / "local")
    monkeypatch.setattr(storage, "SSD_EVENT_LOG", tmp_path / "events.log")
    monkeypatch.setattr(cli, "resolve_paths", lambda s: (_ for _ in ()).throw(storage.SSDNotFound("no marker")))
    assert cli.main(["step7", "stream-session", "--maps", "NDXP", "--fallback-local", "--check-only"]) == 0
    out = capsys.readouterr().out
    assert "recording target: the internal drive" in out and "writable: True" in out
    assert cli.main(["status"]) == cli.EXIT_REFUSED  # every other command still needs the SSD


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")
def test_task_wrapper_logs_locally_when_the_log_dir_is_unwritable(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")  # a file: its "subfolder" can never be created
    local = tmp_path / "local_logs"
    script = tmp_path / "ok.py"
    script.write_text("print('ran')\n", encoding="utf-8")
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "nqbot_task.ps1"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
                        "-LogDir", str(blocker / "logs"), "-LogName", "stream_session", "-NqbotArgs", "x",
                        "-Exe", f"{sys.executable} {script}", "-FallbackLogDir", str(local)],
                       capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    (log,) = local.glob("stream_session_*.txt")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "not writable: logging to" in lines[1] and "ran" in lines and lines[-1] == "TASK EXIT CODE: 0"


def test_verify_reads_utf16_task_logs(tmp_path):
    """The old PowerShell task action wrote UTF-16 logs (fixed 2026-10-10): readable, not damage."""
    log = tmp_path / "stream_session_20261009.txt"
    log.write_text("TASK START\n69760 boards\n", encoding="utf-16")
    verify.check_file(log)
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\x80\x81 not utf-8")
    with pytest.raises(UnicodeDecodeError):
        verify.check_file(bad)


def test_a_drop_lasting_seconds_waits_for_a_stable_drive_before_the_retry(tmp_path, monkeypatch, events):
    """Owner 2026-10-13 (10-12 03:51): the old volume still looked present, the retry ran 3 ms after the drop and
    failed again; the volume came back 4 s later. Now the retry waits for fresh-handle probes to succeed in a row."""
    clock = {"t": 0.0}
    drop_ends = 5.0  # the device is gone for 5 s: every fsync fails until then
    monkeypatch.setattr(storage, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    real = storage.os.fsync
    tries = []

    def fsync(fd):
        if clock["t"] < drop_ends:
            raise OSError(errno.EBADF, "Bad file descriptor")
        tries.append(clock["t"])
        return real(fd)

    monkeypatch.setattr(storage.os, "fsync", fsync)
    target = tmp_path / "ssd" / "nqbot" / "reports" / "day.json"
    storage.atomic_write_bytes(target, b'{"ok": 1}')
    assert target.read_bytes() == b'{"ok": 1}'
    # not retried until the drive had been back for SSD_STABLE_PROBES probes in a row
    stable_at = drop_ends + (storage.SSD_STABLE_PROBES - 1) * storage.SSD_DROP_POLL_SEC
    assert clock["t"] >= stable_at and len(tries) == storage.SSD_STABLE_PROBES + 1  # the probes, then the write
    text = events.read_text(encoding="utf-8")
    assert "drive back and stable after" in text and "retry succeeded" in text and "retry failed" not in text
    assert not list(target.parent.glob(".*.tmp")) and not list(target.parent.glob(storage.PROBE_PREFIX + "*"))
