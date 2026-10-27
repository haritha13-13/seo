"""A Saved Session's files: bounded, UTF-8 safe reads and a sweep for directories a killed worker left behind."""
import os
import time

from portal_auto.runtime.session_files import session_files, sweep_stale_session_dirs
from portal_auto.services.saved_sessions import MAX_SESSION_BYTES


def test_read_out_returns_none_when_missing_oversized_or_not_utf8():
    with session_files("{}") as files:
        assert files.read_out() is None
        files.out.write_bytes(b"\xff\xfe not utf8")
        assert files.read_out() is None
        files.out.write_bytes(b" " * (MAX_SESSION_BYTES + 1))
        assert files.read_out() is None
        files.out.write_text("{\"cookies\": []}", encoding="utf-8")
        assert files.read_out() == "{\"cookies\": []}"


def test_state_with_non_ascii_text_round_trips():
    with session_files('{"v": "café"}') as files:
        assert files.state.read_text(encoding="utf-8") == '{"v": "café"}'


def test_sweep_removes_only_old_session_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    old, fresh, other = (tmp_path / name for name in ("portal_session_old", "portal_session_fresh", "unrelated_old"))
    for directory in (old, fresh, other):
        directory.mkdir()
        (directory / "state.json").write_text("secret")
    ancient = time.time() - 10_000
    for directory in (old, other):
        os.utime(directory, (ancient, ancient))

    sweep_stale_session_dirs(max_age_seconds=3600)

    assert not old.exists() and fresh.exists() and other.exists()
