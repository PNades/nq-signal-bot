import pytest

from nqbot import storage
from nqbot.storage import MARKER, SSDNotFound, atomic_write_json, atomic_write_text, resolve_paths


def test_paths_built_under_root(paths, drive):
    assert paths.root == drive / "nqbot"
    assert paths.data_dir == drive / "nqbot" / "data"
    assert paths.raw_dir == drive / "nqbot" / "data" / "raw"
    assert paths.live_dir == drive / "nqbot" / "data" / "live"
    assert paths.models_dir == drive / "nqbot" / "models"
    assert paths.reports_dir == drive / "nqbot" / "reports"
    assert paths.log_dir == drive / "nqbot" / "logs"
    assert paths.tmp_dir == drive / "nqbot" / "tmp"
    assert paths.mounted()


def test_no_root_set_refuses(settings, drive):
    settings["paths"]["root"] = None
    with pytest.raises(SSDNotFound, match="step 4b"):
        resolve_paths(settings)


def test_no_marker_refuses(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "candidate_drives", lambda: [tmp_path])
    with pytest.raises(SSDNotFound, match=MARKER):
        resolve_paths(settings)


def test_marker_found_on_any_drive(settings, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (b / MARKER).write_text("")
    monkeypatch.setattr(storage, "candidate_drives", lambda: [a, b])
    assert resolve_paths(settings).drive == b


def test_atomic_write_replaces(tmp_path):
    target = tmp_path / "x" / "f.json"
    atomic_write_json(target, {"a": 1})
    atomic_write_json(target, {"a": 2})
    assert target.read_text().count('"a": 2') == 1
    assert [p.name for p in target.parent.iterdir()] == ["f.json"]


def test_atomic_write_leaves_nothing_partial(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    atomic_write_text(target, "old")

    def boom(*a):
        raise OSError("disk gone")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


@pytest.mark.parametrize("bad", ["C:/nqbot", r"C:\nqbot", "/nqbot", r"\nqbot", "../nqbot", "D:nqbot"])
def test_root_must_be_relative_to_the_ssd(settings, drive, bad):
    settings["paths"]["root"] = bad
    with pytest.raises(SSDNotFound, match="relative"):
        resolve_paths(settings)


def test_several_markers_refused(settings, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / MARKER).write_text("")
    monkeypatch.setattr(storage, "candidate_drives", lambda: [a, b])
    with pytest.raises(SSDNotFound, match="several"):
        resolve_paths(settings)


def test_replace_retries_while_file_is_open(tmp_path, monkeypatch):
    # Windows: a reader holding the file open makes os.replace fail with PermissionError for a moment.
    calls = []
    real = storage.os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(storage.os, "replace", flaky)
    monkeypatch.setattr(storage, "_sleep", lambda s: None)
    atomic_write_text(tmp_path / "f.txt", "x")
    assert (tmp_path / "f.txt").read_text() == "x" and len(calls) == 3


def test_replace_gives_up_after_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(13, "denied")))
    monkeypatch.setattr(storage, "_sleep", lambda s: None)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "f.txt", "x")
    assert list(tmp_path.iterdir()) == []


def test_real_drive_scan_skips_the_system_drive():
    import os

    system = os.environ.get("SystemDrive", "C:").upper()
    assert all(not str(d).upper().startswith(system) for d in storage.candidate_drives())


def test_atomic_path_removes_a_temp_file_a_failed_writer_left_open(tmp_path):
    # databento's get_range opens the path ("x+b") and, if the stream fails, raises with the file still open through
    # its frame in the traceback; Windows refuses to delete an open file. Nothing may be left behind.
    import pytest

    from nqbot.storage import atomic_path, remove_stale_temps

    def writer(path):
        f = open(path, "x+b")  # noqa: SIM115 - deliberately left open, as the library does
        f.write(b"partial")
        raise ConnectionError("stream dropped")

    target = tmp_path / "x.dbn.zst"
    with pytest.raises(ConnectionError):
        with atomic_path(target) as tmp:
            writer(tmp)
    assert list(tmp_path.iterdir()) == []

    stale = tmp_path / ".x.dbn.zst.deadbeef.tmp"
    stale.write_bytes(b"old")
    remove_stale_temps(target)
    assert not stale.exists()


def test_replace_retries_for_about_30_seconds_with_backoff(tmp_path, monkeypatch):
    """presd_fixes D5: an antivirus scan may hold a fresh file for many seconds; the rename waits ~30 s, backing off."""
    waits = []
    monkeypatch.setattr(storage.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(13, "denied")))
    monkeypatch.setattr(storage, "_sleep", waits.append)
    with pytest.raises(PermissionError):
        storage.replace_retry(tmp_path / "a", tmp_path / "b")
    assert waits[0] == storage.REPLACE_RETRY_FIRST_SEC and max(waits) == storage.REPLACE_RETRY_MAX_SEC
    assert all(b >= a for a, b in zip(waits, waits[1:]))
    assert 30 <= sum(waits) < 30 + storage.REPLACE_RETRY_MAX_SEC
