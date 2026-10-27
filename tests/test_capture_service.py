"""CaptureService with fake adapters (S8): single-flight, expiry, and cleanup on every exit path."""
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from portal_auto.services.capture import CaptureService
from portal_auto.services.errors import Invalid

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeHandle:
    def __init__(self, port: int):
        self.port = port
        self.terminated = 0
        self.running = True

    def terminate(self) -> None:
        self.terminated += 1

    def alive(self) -> bool:
        return self.running


class FakeLauncher:
    def __init__(self):
        self.launches: list[tuple[str, Path, int]] = []
        self.handles: list[FakeHandle] = []

    def launch(self, url, profile_dir):
        assert profile_dir.is_dir()
        handle = FakeHandle(port=40000 + len(self.handles))
        self.launches.append((url, profile_dir, handle.port))
        self.handles.append(handle)
        return handle


class FakeReader:
    def __init__(self, state: str = '{"cookies": [], "origins": []}', error: Exception | None = None):
        self.state, self.error, self.ports = state, error, []

    def read(self, port):
        self.ports.append(port)
        if self.error:
            raise self.error
        return self.state


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def launcher():
    return FakeLauncher()


@pytest.fixture
def clock():
    return Clock()


class FakeScheduler:
    """Records timers instead of sleeping; fire() runs a callback as the timer thread would."""

    def __init__(self):
        self.timers: list[FakeTimer] = []

    def __call__(self, delay, callback):
        self.timers.append(FakeTimer(delay, callback))
        return self.timers[-1]


class FakeTimer:
    def __init__(self, delay, callback):
        self.delay, self.callback, self.cancelled = delay, callback, False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback()


@pytest.fixture
def scheduler():
    return FakeScheduler()


def make(launcher, clock, tmp_path, reader=None, scheduler=None, **kwargs):
    return CaptureService(
        launcher, reader or FakeReader(), clock=clock, temp_root=tmp_path,
        scheduler=scheduler or FakeScheduler(), **kwargs,
    )


def test_start_launches_chrome_on_a_loopback_port_with_a_private_profile(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path)
    info = service.start(7, "https://bank.example")
    url, profile, port = launcher.launches[0]
    assert (url, port, info.port, info.credential_id) == ("https://bank.example", info.port, port, 7)
    assert profile.parent == tmp_path and profile.stat().st_mode & 0o777 == 0o700
    assert info.expires_at == NOW + timedelta(seconds=900)
    assert service.active() == info


def test_only_one_capture_at_a_time(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path)
    service.start(1, "https://a.example")
    with pytest.raises(Invalid, match="already open"):
        service.start(2, "https://b.example")
    assert len(launcher.launches) == 1


def test_expired_capture_is_cleaned_up_and_frees_the_slot(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, ttl_seconds=60)
    first = service.start(1, "https://a.example")
    clock.now += timedelta(seconds=61)
    assert service.active() is None
    assert launcher.handles[0].terminated == 1 and not launcher.launches[0][1].exists()
    assert service.start(2, "https://b.example").id != first.id


def test_start_cleans_up_an_expired_capture_before_launching(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, ttl_seconds=60)
    service.start(1, "https://a.example")
    clock.now += timedelta(seconds=60)
    service.start(2, "https://b.example")
    assert launcher.handles[0].terminated == 1 and not launcher.launches[0][1].exists()


def test_finish_returns_the_state_and_cleans_up(launcher, clock, tmp_path):
    reader = FakeReader('{"cookies": [1], "origins": []}')
    service = make(launcher, clock, tmp_path, reader)
    info = service.start(1, "https://a.example")
    assert service.finish(info.id) == reader.state
    assert reader.ports == [info.port]
    assert launcher.handles[0].terminated == 1 and not launcher.launches[0][1].exists()
    assert service.active() is None


def test_finish_still_cleans_up_when_reading_fails(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, FakeReader(error=Invalid("no chrome")))
    info = service.start(1, "https://a.example")
    with pytest.raises(Invalid, match="no chrome"):
        service.finish(info.id)
    assert launcher.handles[0].terminated == 1 and not launcher.launches[0][1].exists()
    assert service.active() is None


def test_finish_refuses_unknown_or_expired_captures(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, ttl_seconds=60)
    info = service.start(1, "https://a.example")
    with pytest.raises(Invalid, match="no longer open"):
        service.finish("wrong")
    assert service.active() == info
    clock.now += timedelta(seconds=61)
    with pytest.raises(Invalid, match="no longer open"):
        service.finish(info.id)
    assert launcher.handles[0].terminated == 1


def test_cancel_terminates_and_deletes_the_profile(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path)
    info = service.start(1, "https://a.example")
    service.cancel(info.id)
    assert launcher.handles[0].terminated == 1 and not launcher.launches[0][1].exists()
    assert service.active() is None


def test_shutdown_terminates_and_deletes_everything_and_is_idempotent(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path)
    service.start(1, "https://a.example")
    service.shutdown()
    service.shutdown()
    assert launcher.handles[0].terminated == 1 and list(tmp_path.iterdir()) == []


def test_failed_launch_leaves_no_profile_and_no_open_capture(clock, tmp_path):
    class Missing:
        def launch(self, url, profile_dir):
            raise Invalid("Google Chrome was not found")

    service = make(Missing(), clock, tmp_path)
    with pytest.raises(Invalid, match="not found"):
        service.start(1, "https://a.example")
    assert list(tmp_path.iterdir()) == [] and service.active() is None


def test_timer_expires_an_idle_capture_without_anyone_asking(launcher, clock, tmp_path, scheduler):
    service = make(launcher, clock, tmp_path, scheduler=scheduler, ttl_seconds=60)
    service.start(1, "https://a.example")
    assert [timer.delay for timer in scheduler.timers] == [60]
    clock.now += timedelta(seconds=61)
    scheduler.timers[0].fire()
    assert launcher.handles[0].terminated == 1 and list(tmp_path.iterdir()) == []
    assert service.expired_for(1)


@pytest.mark.parametrize("ending", ["finish", "cancel", "shutdown"])
def test_timer_is_cancelled_when_the_capture_ends(launcher, clock, tmp_path, scheduler, ending):
    service = make(launcher, clock, tmp_path, scheduler=scheduler)
    info = service.start(1, "https://a.example")
    if ending == "shutdown":
        service.shutdown()
    else:
        getattr(service, ending)(info.id)
    assert scheduler.timers[0].cancelled


def test_stale_timer_cannot_close_a_newer_capture(launcher, clock, tmp_path, scheduler):
    service = make(launcher, clock, tmp_path, scheduler=scheduler)
    first = service.start(1, "https://a.example")
    service.cancel(first.id)
    service.start(2, "https://b.example")
    clock.now += timedelta(hours=1)
    scheduler.timers[0].fire()
    assert launcher.handles[1].terminated == 0


def test_default_scheduler_uses_a_daemon_timer(launcher, clock, tmp_path):
    service = CaptureService(launcher, FakeReader(), clock=clock, temp_root=tmp_path, ttl_seconds=3600)
    service.start(1, "https://a.example")
    timer = service._open.timer
    assert timer.daemon and timer.is_alive()
    service.shutdown()


def test_starting_again_forgets_the_expiry(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, ttl_seconds=60)
    service.start(1, "https://a.example")
    clock.now += timedelta(seconds=61)
    assert service.active() is None and service.expired_for(1) and not service.expired_for(2)
    service.start(1, "https://a.example")
    assert not service.expired_for(1)


def test_sweep_removes_only_stale_capture_profiles(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, ttl_seconds=900)
    stale, fresh, other = (tmp_path / name for name in ("portal-auto-capture-old", "portal-auto-capture-new", "keep-me"))
    for directory in (stale, fresh, other):
        directory.mkdir()
    old = (NOW - timedelta(seconds=901)).timestamp()
    for directory in (stale, other):
        os.utime(directory, (old, old))
    service.sweep_stale_profiles()
    assert not stale.exists() and fresh.exists() and other.exists()
    assert launcher.handles == []


def test_sweep_never_touches_the_open_capture(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, ttl_seconds=900)
    service.start(1, "https://a.example")
    profile = launcher.launches[0][1]
    old = (NOW - timedelta(hours=2)).timestamp()
    os.utime(profile, (old, old))
    service.sweep_stale_profiles()
    assert profile.exists()


def test_finish_when_chrome_was_closed_does_not_read_and_cleans_up(launcher, clock, tmp_path):
    reader = FakeReader()
    service = make(launcher, clock, tmp_path, reader)
    info = service.start(1, "https://a.example")
    launcher.handles[0].running = False
    with pytest.raises(Invalid, match="Chrome was closed before the session was saved; start the capture again"):
        service.finish(info.id)
    assert reader.ports == [] and launcher.handles[0].terminated == 1
    assert not launcher.launches[0][1].exists() and service.active() is None


def test_concurrent_start_launches_exactly_once(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path)
    outcomes: list[str] = []

    def attempt(credential_id):
        try:
            service.start(credential_id, "https://a.example")
            outcomes.append("started")
        except Invalid:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["refused"] * 7 + ["started"] and len(launcher.launches) == 1


def profile_of(tmp_path, credential_id: int):
    return tmp_path / "profiles" / f"credential-{credential_id}"


def test_each_credential_gets_its_own_private_profile_which_is_kept(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, profiles_dir=tmp_path / "profiles")

    first = service.start(1, "https://x.test")
    service.cancel(first.id)
    second = service.start(2, "https://x.test")
    service.cancel(second.id)

    assert launcher.launches[0][1] == profile_of(tmp_path, 1)
    assert launcher.launches[1][1] == profile_of(tmp_path, 2)
    for credential_id in (1, 2):
        directory = profile_of(tmp_path, credential_id)
        assert directory.is_dir() and (directory.stat().st_mode & 0o777) == 0o700
    assert ((tmp_path / "profiles").stat().st_mode & 0o777) == 0o700


def test_capturing_the_same_credential_again_reuses_its_profile(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path, profiles_dir=tmp_path / "profiles")

    service.cancel(service.start(1, "https://x.test").id)
    (profile_of(tmp_path, 1) / "marker").write_text("still signed in")
    service.cancel(service.start(1, "https://x.test").id)

    assert launcher.launches[0][1] == launcher.launches[1][1]
    assert (profile_of(tmp_path, 1) / "marker").read_text() == "still signed in"


@pytest.mark.parametrize("ending", ["finish", "expire", "shutdown"])
def test_a_credentials_profile_survives_every_way_a_capture_ends(launcher, clock, tmp_path, ending):
    service = make(launcher, clock, tmp_path, profiles_dir=tmp_path / "profiles")
    info = service.start(1, "https://x.test")

    if ending == "finish":
        service.finish(info.id)
    elif ending == "expire":
        clock.now += timedelta(hours=1)
        service.active()
    else:
        service.shutdown()

    assert profile_of(tmp_path, 1).is_dir() and launcher.handles[0].terminated == 1


def test_a_failed_launch_keeps_the_credentials_profile(clock, tmp_path):
    class Failing:
        def launch(self, url, profile_dir):
            raise Invalid("Chrome was not found")

    with pytest.raises(Invalid):
        make(Failing(), clock, tmp_path, profiles_dir=tmp_path / "profiles").start(1, "https://x.test")
    assert profile_of(tmp_path, 1).is_dir()


def test_without_a_profiles_folder_each_capture_gets_a_throwaway_profile(launcher, clock, tmp_path):
    service = make(launcher, clock, tmp_path)

    info = service.start(1, "https://x.test")
    profile = launcher.launches[0][1]
    service.cancel(info.id)

    assert not profile.exists()
