import os
import signal
import subprocess
import sys
import time

from portal_auto.runtime.processes import pid_alive, terminate_group
from process_helpers import is_gone, wait_for

SPAWNS_SLEEPER = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen(['sleep', '60'])\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(60)\n"
)
WORKER_HARNESS = (
    "import sys\n"
    "from portal_auto.runtime.executor import SubprocessScriptExecutor\n"
    "from portal_auto.runtime.processes import install_sigterm_exit\n"
    "install_sigterm_exit()\n"
    "code = {code!r}.replace('PIDFILE', sys.argv[1])\n"
    "SubprocessScriptExecutor().execute(code, {{}}, True, 120)\n"
)
GRANDCHILD_SCRIPT = (
    "import subprocess, time\n"
    "child = subprocess.Popen(['sleep', '60'])\n"
    "open('PIDFILE', 'w').write(str(child.pid))\n"
    "time.sleep(60)\n"
)


def start_in_own_group(*args: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    return subprocess.Popen([sys.executable, *args], start_new_session=True, env=env)


def test_terminate_group_kills_the_leader_and_its_descendants(tmp_path):
    pidfile = tmp_path / "pid"
    leader = start_in_own_group("-c", SPAWNS_SLEEPER, str(pidfile))
    grandchild = wait_for(pidfile)

    terminate_group(leader.pid, grace=2)

    leader.wait(timeout=5)
    assert is_gone(grandchild)


def test_terminate_group_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path):
    pidfile = tmp_path / "pid"
    stubborn = SPAWNS_SLEEPER.replace("import subprocess", "import signal, subprocess").replace(
        "child =", "signal.signal(signal.SIGTERM, signal.SIG_IGN)\nchild =", 1)
    leader = start_in_own_group("-c", stubborn, str(pidfile))
    wait_for(pidfile)

    terminate_group(leader.pid, grace=0.3)

    assert leader.wait(timeout=5) == -signal.SIGKILL


def test_terminate_group_ignores_a_group_that_is_already_gone():
    done = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    done.wait()
    terminate_group(done.pid, grace=0.1)


def test_terminating_a_worker_takes_the_scripts_descendants_with_it(tmp_path):
    pidfile = tmp_path / "pid"
    worker = start_in_own_group("-c", WORKER_HARNESS.format(code=GRANDCHILD_SCRIPT), str(pidfile))
    grandchild = wait_for(pidfile)

    terminate_group(worker.pid, grace=5)

    worker.wait(timeout=10)
    assert is_gone(grandchild)


def test_pid_alive_is_false_for_a_finished_unreaped_child():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 5
    while pid_alive(child.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(child.pid)


def test_pid_alive_is_true_for_a_running_process():
    assert pid_alive(os.getpid())
