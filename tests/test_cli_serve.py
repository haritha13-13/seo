"""`portal-auto serve` start-up checks (fails fast on a bad vault key, notes unstable sessions)."""
import os
import signal
import tempfile
import threading
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from flask import Flask

from portal_auto import cli
from portal_auto.config import Settings


@pytest.fixture(autouse=True)
def private_temp_root(tmp_path, monkeypatch):
    root = tmp_path / "tmp-root"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def no_server(monkeypatch):
    started = []
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: started.append(kwargs))
    return started


def settings(tmp_path, vault_key):
    return Settings(database_url=f"sqlite:///{tmp_path / 'cli.db'}", vault_key=vault_key, root=tmp_path)


@pytest.mark.parametrize("key", [None, "not-a-fernet-key"])
def test_serve_refuses_to_start_without_a_valid_vault_key(tmp_path, no_server, key):
    with pytest.raises(SystemExit) as exit_info:
        cli.serve(settings(tmp_path, key))
    assert "PORTAL_AUTO_VAULT_KEY" in str(exit_info.value)
    assert no_server == []
    assert not (tmp_path / "cli.db").exists()


def test_serve_starts_and_notes_that_sessions_reset_without_a_secret_key(tmp_path, no_server, monkeypatch, capsys):
    monkeypatch.delenv("PORTAL_AUTO_SECRET_KEY", raising=False)
    cli.serve(settings(tmp_path, Fernet.generate_key().decode()), port=5999)
    assert no_server == [{"host": "127.0.0.1", "port": 5999}]
    assert "sessions reset on restart" in capsys.readouterr().out


def test_serve_is_quiet_about_sessions_when_a_secret_key_is_set(tmp_path, no_server, monkeypatch, capsys):
    monkeypatch.setenv("PORTAL_AUTO_SECRET_KEY", "stable")
    cli.serve(settings(tmp_path, Fernet.generate_key().decode()))
    assert "sessions reset" not in capsys.readouterr().out


def test_serve_sweeps_stale_capture_profiles_at_startup(tmp_path, no_server, private_temp_root):
    stale = private_temp_root / "portal-auto-capture-dead"
    stale.mkdir()
    old = os.stat(stale).st_mtime - timedelta(hours=1).total_seconds()
    os.utime(stale, (old, old))
    cli.serve(settings(tmp_path, Fernet.generate_key().decode()))
    assert not stale.exists()


def test_serve_shuts_the_capture_down_on_sigterm_and_restores_the_handler(tmp_path, monkeypatch):
    previous = signal.getsignal(signal.SIGTERM)
    seen = {}

    def run(self, **kwargs):
        service = self.extensions["capture_service"]
        monkeypatch.setattr(service, "shutdown", lambda: seen.setdefault("shutdown", True))
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(Flask, "run", run)
    with pytest.raises(SystemExit):
        cli.serve(settings(tmp_path, Fernet.generate_key().decode()))
    assert seen == {"shutdown": True}
    assert signal.getsignal(signal.SIGTERM) == previous


def test_serve_off_the_main_thread_skips_the_signal_handler(tmp_path, no_server):
    previous = signal.getsignal(signal.SIGTERM)
    worker = threading.Thread(target=cli.serve, args=(settings(tmp_path, Fernet.generate_key().decode()),))
    worker.start()
    worker.join()
    assert no_server and signal.getsignal(signal.SIGTERM) == previous
