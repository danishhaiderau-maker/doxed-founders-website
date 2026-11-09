"""Supervisor caps, kill, scrub, and receipt rules. Synthetic processes only."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from research import ext2_supervisor as supervisor
from research.ext2_costs import Ext2Stop


def _roots(tmp_path: Path):
    data = tmp_path / "data"
    output = tmp_path / "out"
    data.mkdir()
    output.mkdir()
    return data, output


def _disk(value: int):
    return lambda: value


def test_caps_and_disk_floor_are_the_card_limits():
    assert supervisor.HARD_STOP_SEC == 4 * 60 * 60
    assert supervisor.CPU_UNIT_SEC == 600
    assert supervisor.GPU_UNIT_SEC == 120
    assert supervisor.DISK_FLOOR_BYTES == 100 * 1024 ** 3
    assert supervisor.DISK_RUN_FLOOR_BYTES == 80 * 1024 ** 3
    assert supervisor.HEARTBEAT_STALL_SEC == 300


def test_low_disk_does_not_start_the_child(tmp_path):
    data, output = _roots(tmp_path)
    marker = output / "started"
    receipt = supervisor.supervise(
        [sys.executable, "-c", f"open(r'{marker}', 'w').write('started')"],
        data_root=data, output_root=output, disk_free=_disk(1),
        cpu_unit_sec=30, gpu_unit_sec=30, hard_stop_sec=30, heartbeat_stall_sec=30,
    )
    assert receipt["status"] == "STOP"
    assert receipt["reason"] == "STOP_DISK_FLOOR"
    assert receipt["child_started"] is False
    assert not marker.exists()


def test_unit_cap_kills_a_live_heartbeat(tmp_path):
    data, output = _roots(tmp_path)
    script = (
        "import time, pathlib, sys\n"
        "beat = pathlib.Path(sys.argv[1])\n"
        "while True:\n"
        "    beat.write_text('alive', encoding='utf-8')\n"
        "    time.sleep(0.05)\n"
    )
    receipt = supervisor.supervise(
        [sys.executable, "-c", script, str(output / "heartbeat")],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        cpu_unit_sec=0.4, gpu_unit_sec=0.4, hard_stop_sec=5, heartbeat_stall_sec=5,
        unit_device="cpu",
    )
    assert receipt["reason"] == "STOP_UNIT_CAP"
    assert receipt["child_started"] is True
    assert receipt["exit_code"] is not None
    assert receipt["tree_kill"] is True
    assert (output / "heartbeat").exists()


def test_gpu_unit_cap_is_separate_and_cannot_be_raised(tmp_path):
    data, output = _roots(tmp_path)
    receipt = supervisor.supervise(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        cpu_unit_sec=30, gpu_unit_sec=0.3, hard_stop_sec=5, heartbeat_stall_sec=5,
        unit_device="gpu",
    )
    assert receipt["reason"] == "STOP_UNIT_CAP"
    raised = supervisor.supervise(
        [sys.executable, "-c", "print(1)"],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        cpu_unit_sec=601,
    )
    assert raised["reason"] == "STOP_CPU_CAP_EXTENDED"
    assert raised["child_started"] is False


def test_missing_heartbeat_stops_when_the_unit_cap_has_not_fired(tmp_path):
    data, output = _roots(tmp_path)
    receipt = supervisor.supervise(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        cpu_unit_sec=30, hard_stop_sec=10, heartbeat_stall_sec=0.3,
    )
    assert receipt["reason"] == "STOP_HEARTBEAT"
    assert receipt["exit_code"] is not None


def test_exit_zero_without_a_completion_marker_stops_and_unknown_is_classified():
    assert supervisor.classify_exit(None, True) == "STOP_UNKNOWN_EXIT"
    assert supervisor.classify_exit(0, False) == "STOP_NO_COMPLETION_MARKER"
    assert supervisor.classify_exit(0, True) == "COMPLETED"
    assert supervisor.classify_exit(3, True) == "STOP_CHILD_EXIT"


def test_child_exit_zero_without_marker_is_recorded(tmp_path):
    data, output = _roots(tmp_path)
    receipt = supervisor.supervise(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        cpu_unit_sec=30, hard_stop_sec=10, heartbeat_stall_sec=10,
    )
    assert receipt["exit_code"] == 0
    assert receipt["reason"] == "STOP_NO_COMPLETION_MARKER"


def test_final_tree_kill_reaps_a_grandchild(tmp_path):
    data, output = _roots(tmp_path)
    script = (
        "import pathlib, subprocess, sys, time\n"
        "out, marker = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "out.write_text(str(child.pid), encoding='utf-8')\n"
        "marker.write_text('complete', encoding='utf-8')\n"
    )
    receipt = supervisor.supervise(
        [sys.executable, "-c", script, str(output / "grandchild"), str(output / "COMPLETE")],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        cpu_unit_sec=30, hard_stop_sec=10, heartbeat_stall_sec=10,
    )
    assert receipt["status"] == "COMPLETED"
    assert receipt["exit_code"] == 0
    assert receipt["tree_kill"] is True
    grandchild = int((output / "grandchild").read_text(encoding="utf-8"))
    deadline = time.time() + 2
    alive = True
    while time.time() < deadline:
        try:
            os.kill(grandchild, 0)
        except OSError:
            alive = False
            break
        time.sleep(0.05)
    assert alive is False
    job = supervisor.windows_job_members(os.getpid(), receipt["child_pid"])
    assert receipt["child_pid"] in job["members"]
    assert os.getpid() in job["excludes"]
    assert job["supervisor_outside_job"] is True


def test_key_like_env_is_scrubbed_and_absent_from_the_receipt(tmp_path):
    data, output = _roots(tmp_path)
    secret = "super-secret-value"
    script = (
        "import os, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('\\n'.join(sorted(os.environ)), encoding='utf-8')\n"
        "pathlib.Path(sys.argv[2]).write_text('complete', encoding='utf-8')\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "BITFINEX_API_KEY": secret, "MY_SECRET": secret}
    receipt = supervisor.supervise(
        [sys.executable, "-c", script, str(output / "keys.txt"), str(output / "COMPLETE")],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        env=env, cpu_unit_sec=30, hard_stop_sec=10, heartbeat_stall_sec=10,
        forbidden_substrings=(secret,),
    )
    keys = (output / "keys.txt").read_text(encoding="utf-8")
    assert "BITFINEX_API_KEY" not in keys
    assert "MY_SECRET" not in keys
    assert secret not in keys
    assert secret not in json.dumps(receipt)
    assert "environ" not in receipt


def test_write_under_the_data_root_stops_and_network_imports_are_flagged(tmp_path):
    data, output = _roots(tmp_path)
    with pytest.raises(Ext2Stop, match="STOP_WRITE_DATA_ROOT"):
        supervisor.guarded_write(data / "leak.txt", "nope", data_root=data, output_root=output)
    with pytest.raises(Ext2Stop, match="STOP_OUTPUT_INSIDE_DATA_ROOT"):
        supervisor.assert_roots(data, data / "nested-out")
    assert supervisor.import_violations("import socket\nimport numpy\n") == ["socket"]
    assert supervisor.classify_connect("203.0.113.5:443") == "STOP_NETWORK"
    assert supervisor.classify_connect("ok", allowlist=("ok",)) == "ALLOWED"
    files = list((Path(supervisor.__file__).resolve().parent).glob("ext2_*.py"))
    assert supervisor.audit_files(files) == []


def test_heartbeat_thread_does_not_enforce_the_unit_cap(tmp_path):
    beat = tmp_path / "heartbeat"
    thread = supervisor.HeartbeatThread(beat, 0.05)
    thread.start()
    time.sleep(0.2)
    thread.stop()
    assert beat.read_text(encoding="utf-8") == "alive"
    source = Path(supervisor.__file__).read_text(encoding="utf-8").split("class HeartbeatThread", 1)[1].split("def scrub_env", 1)[0]
    assert "CPU_UNIT_SEC" not in source
    assert "killpg" not in source
    assert supervisor.real_run_device_stop([{"stage": "exit_lattice", "device": "cpu", "fallback": True}]) == "STOP_GPU_FALLBACK"


def test_receipt_rejects_an_env_dump():
    with pytest.raises(Ext2Stop, match="STOP_RECEIPT_SECRET"):
        supervisor.seal_receipt({"env": {"BITFINEX_API_KEY": "x"}})
    with pytest.raises(Ext2Stop, match="STOP_RECEIPT_SECRET"):
        supervisor.seal_receipt({"note": "super-secret-value"}, forbidden_substrings=("super-secret-value",))


def test_require_gpu_stops_before_the_worker_starts(tmp_path):
    data, output = _roots(tmp_path)
    receipt = supervisor.supervise(
        [sys.executable, "-c", "print('should-not-run')"],
        data_root=data, output_root=output, disk_free=_disk(supervisor.DISK_FLOOR_BYTES + 1),
        require_gpu=True, cpu_unit_sec=30, hard_stop_sec=30, heartbeat_stall_sec=30,
    )
    assert receipt["reason"] == "STOP_GPU_MISSING"
    assert receipt["child_started"] is False
    assert receipt["devices"][-1]["stage"] == "exit_lattice"
    assert receipt["devices"][-1]["fallback"] is False
    assert receipt["devices"][-1]["device"] is None
