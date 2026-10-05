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
    monkeypatch.setattr(storage, "REPLACE_RETRY_SEC", 0)
    atomic_write_text(tmp_path / "f.txt", "x")
    assert (tmp_path / "f.txt").read_text() == "x" and len(calls) == 3


def test_replace_gives_up_after_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(13, "denied")))
    monkeypatch.setattr(storage, "REPLACE_RETRY_SEC", 0)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "f.txt", "x")
    assert list(tmp_path.iterdir()) == []


def test_real_drive_scan_skips_the_system_drive():
    import os

    system = os.environ.get("SystemDrive", "C:").upper()
    assert all(not str(d).upper().startswith(system) for d in storage.candidate_drives())
