import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_detached_exec as fde


class FakeFly:
    """Runs guest programs locally against a temp job dir; injects 408s."""

    def __init__(self, tmp_path, *, fail_first=0, job_rc=0, job_output='{"mode": "plan", "ok": true}\n'):
        self.tmp_path = tmp_path
        self.fail_first = fail_first
        self.calls = 0
        self.job_rc = job_rc
        self.job_output = job_output
        self.launched_argv = None
        self.polls = 0

    def __call__(self, cmd, capture_output, text):
        self.calls += 1
        assert cmd[:3] == ["flyctl", "machine", "exec"]
        if self.fail_first > 0:
            self.fail_first -= 1
            return subprocess.CompletedProcess(cmd, 1, "", "Error: request returned non-2xx status: 408")
        command = cmd[-1]
        match = re.match(r"python -c 'import base64; exec\(base64.b64decode\(\"([A-Za-z0-9+/=]+)\"\)\)' (.*)$", command)
        assert match, command
        program = base64.b64decode(match.group(1)).decode()
        args = re.findall(r"'((?:[^']|'\"'\"')*)'", match.group(2))
        if "Popen" in program:
            self.launched_argv = json.loads(args[2])
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"schema": "fly_detached_exec_v1", "started": True, "label": args[1], "pid": 1}) + "\n", "")
        self.polls += 1
        if self.polls < 2:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"schema": "fly_detached_exec_v1", "done": False}) + "\n", "")
        row = {"schema": "fly_detached_exec_v1", "done": True, "rc": self.job_rc, "output": self.job_output}
        return subprocess.CompletedProcess(cmd, 0, "Connecting...\n" + json.dumps(row) + "\n", "")


def _clock():
    now = [0.0]

    def monotonic():
        return now[0]

    def sleep(sec):
        now[0] += sec

    return monotonic, sleep


def test_detached_exec_survives_408_and_replays_output(tmp_path):
    fake = FakeFly(tmp_path, fail_first=2)
    monotonic, sleep = _clock()
    argv = ["python", "/app/clean_epoch_reset_plan.py", "plan", "--runtime-root", "/app/data/runtime"]
    rc, out = fde.run_detached("app", "m1", "reset-plan", argv, deadline_sec=600, run=fake, sleep=sleep, monotonic=monotonic)
    assert rc == 0
    assert json.loads(out.strip())["ok"] is True
    assert fake.launched_argv == argv


def test_detached_exec_propagates_job_failure(tmp_path):
    fake = FakeFly(tmp_path, job_rc=3, job_output="boom\n")
    monotonic, sleep = _clock()
    rc, out = fde.run_detached("app", "m1", "verify", ["true"], deadline_sec=600, run=fake, sleep=sleep, monotonic=monotonic)
    assert rc == 3 and out == "boom\n"


def test_detached_exec_deadline_fails_closed(tmp_path):
    class NeverDone(FakeFly):
        def __call__(self, cmd, capture_output, text):
            result = super().__call__(cmd, capture_output, text)
            if '"done": true' in result.stdout:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"schema": "fly_detached_exec_v1", "done": False}) + "\n", "")
            return result

    monotonic, sleep = _clock()
    with pytest.raises(RuntimeError, match="did not finish"):
        fde.run_detached("app", "m1", "plan", ["true"], deadline_sec=60, run=NeverDone(tmp_path), sleep=sleep, monotonic=monotonic)


def test_launch_gives_up_after_bounded_attempts(tmp_path):
    monotonic, sleep = _clock()
    with pytest.raises(RuntimeError, match="launch failed"):
        fde.run_detached("app", "m1", "plan", ["true"], deadline_sec=60, run=FakeFly(tmp_path, fail_first=99), sleep=sleep, monotonic=monotonic)


def test_guest_programs_run_locally(tmp_path):
    job_dir = str(tmp_path / "jobs")
    launch = subprocess.run([sys.executable, "-c", fde._LAUNCHER, job_dir, "t1", json.dumps([sys.executable, "-c", "print('hello')"])],
                            capture_output=True, text=True, check=True)
    assert fde.parse_guest_json(launch.stdout)["started"] is True
    import time
    for _ in range(100):
        poll = subprocess.run([sys.executable, "-c", fde._POLLER, job_dir, "t1"], capture_output=True, text=True, check=True)
        row = fde.parse_guest_json(poll.stdout)
        if row["done"]:
            break
        time.sleep(0.1)
    assert row["done"] is True and row["rc"] == 0 and row["output"].strip() == "hello"


def test_label_is_validated():
    with pytest.raises(ValueError):
        fde.run_detached("app", "m1", "../x", ["true"], deadline_sec=1)
