"""Supervisor: exclusive run root, nonce marker, per-unit caps, 4 h stop / 2 h flag,
tree kill, scrub, guards and receipts. Synthetic processes only (push 3)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from research import ext2_supervisor as S
from research.ext2_costs import Ext2Stop

FREE = S.DISK_FLOOR_BYTES + 1

# A tiny worker: optional unit announcements, heartbeat, marker modes, exit code.
WORKER = r'''
import json, os, pathlib, sys, time
root = pathlib.Path.cwd()
mode, code = sys.argv[1], int(sys.argv[2])
units = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
def unit(seq, device):
    tmp = root / "unit.json.tmp"
    tmp.write_text(json.dumps({"seq": seq, "device": device, "name": "u%d" % seq}))
    os.replace(tmp, root / "unit.json")
(root / "heartbeat").write_text("alive")
for seq, (device, sleep) in enumerate(units, 1):
    unit(seq, device)
    end = time.time() + sleep
    while time.time() < end:
        (root / "heartbeat").write_text("alive")
        time.sleep(0.05)
nonce = os.environ.get("EXT2_RUN_NONCE")
card = os.environ.get("EXT2_CARD_SHA12")
(root / "env_keys.json").write_text(json.dumps(sorted(os.environ)))
if mode == "good":
    (root / "COMPLETE").write_text(json.dumps({"run_nonce": nonce, "card_sha12": card, "finished_aedt": "x"}))
elif mode == "wrong_nonce":
    (root / "COMPLETE").write_text(json.dumps({"run_nonce": "0" * 32, "card_sha12": card, "finished_aedt": "x"}))
elif mode == "leak":
    (root / "run_receipt.json").write_text(json.dumps({"note": os.environ.get("LEAKME", "")}))
    (root / "COMPLETE").write_text(json.dumps({"run_nonce": nonce, "card_sha12": card, "finished_aedt": "x"}))
sys.exit(code)
'''


def _roots(tmp_path: Path):
    data = tmp_path / "external-data" / "bitfinex"
    data.mkdir(parents=True)
    return data, tmp_path / "runs" / "run1"


def _run(tmp_path, mode="good", code=0, units=(), **kw):
    data, out = _roots(tmp_path)
    cmd = [sys.executable, "-c", WORKER, mode, str(code), json.dumps(list(units))]
    kw.setdefault("hard_stop_sec", 30)
    kw.setdefault("heartbeat_stall_sec", 30)
    kw.setdefault("disk_free", lambda: FREE)
    return S.supervise(cmd, data_root=data, output_root=out, **kw), out


def test_caps_and_disk_floor_are_the_card_limits():
    assert S.HARD_STOP_SEC == 4 * 3600 and S.FLAG_SEC == 2 * 3600
    assert S.CPU_UNIT_SEC == 600 and S.GPU_UNIT_SEC == 120
    assert S.DISK_FLOOR_BYTES == 100 * 1024 ** 3 and S.DISK_RUN_FLOOR_BYTES == 80 * 1024 ** 3
    for kw, reason in (({"hard_stop_sec": S.HARD_STOP_SEC + 1}, "STOP_HARD_STOP_EXTENDED"),
                       ({"flag_sec": S.FLAG_SEC + 1}, "STOP_FLAG_EXTENDED"),
                       ({"cpu_unit_sec": 601}, "STOP_CPU_CAP_EXTENDED"),
                       ({"gpu_unit_sec": 121}, "STOP_GPU_CAP_EXTENDED")):
        data = Path(os.devnull).parent
        receipt = S.supervise([sys.executable, "-c", "pass"], data_root=data / "x", output_root=data / "y",
                              disk_free=lambda: FREE, **kw)
        assert receipt["reason"] == reason and receipt["child_started"] is False


def test_completed_needs_exit_zero_and_this_runs_nonce(tmp_path):  # RK2 / A18
    receipt, out = _run(tmp_path)
    assert receipt["status"] == "COMPLETED", receipt["reason"]
    assert receipt["child_exit_code"] == 0 and receipt["completion_marker_ok"] is True
    assert len(receipt["run_nonce"]) == 32
    marker = json.loads((out / "COMPLETE").read_text())
    assert marker["run_nonce"] == receipt["run_nonce"] and marker["card_sha12"] == S.CARD_SHA_V3[:12]
    assert (out / "receipt.json").is_file() and (out / "SHA256SUMS.txt").is_file()
    sums = (out / "SHA256SUMS.txt").read_text()
    assert "receipt.json" in sums and "COMPLETE" in sums
    for key in S.RECEIPT_REQUIRED_KEYS:
        assert key in receipt


def test_exit_zero_with_a_foreign_nonce_or_no_marker_stops(tmp_path):
    receipt, _ = _run(tmp_path / "a", mode="wrong_nonce")
    assert receipt["reason"] == "STOP_NO_COMPLETION_MARKER" and receipt["completion_marker_ok"] is False
    receipt, _ = _run(tmp_path / "b", mode="none")
    assert receipt["reason"] == "STOP_NO_COMPLETION_MARKER"
    assert receipt["stop_reason"] == "STOP_NO_COMPLETION_MARKER"


def test_non_zero_child_exit_is_recorded_not_zeroed(tmp_path):  # M01 / N1
    receipt, out = _run(tmp_path, mode="good", code=3)
    assert receipt["status"] == "STOP" and receipt["reason"] == "STOP_CHILD_EXIT"
    assert receipt["child_exit_code"] == 3 and receipt["exit_code"] == 3
    on_disk = json.loads((out / "receipt.json").read_text())
    assert on_disk["child_exit_code"] == 3


def test_run_root_must_be_new_and_nothing_is_overwritten(tmp_path):  # RK1 / B7
    data, out = _roots(tmp_path)
    out.mkdir(parents=True)
    (out / "old.txt").write_text("keep")
    receipt = S.supervise([sys.executable, "-c", "pass"], data_root=data, output_root=out, disk_free=lambda: FREE)
    assert receipt["reason"] == "STOP_RUN_ROOT_EXISTS" and receipt["child_started"] is False
    assert receipt["receipt_written"] is False
    assert sorted(p.name for p in out.iterdir()) == ["old.txt"]
    target = out / "old.txt"
    with pytest.raises(Ext2Stop, match="STOP_OVERWRITE"):
        S.guarded_write(target, "new", data_root=data, output_root=out)
    assert target.read_text() == "keep"
    S.guarded_write(out / "fresh.bin", b"\x00\x01", data_root=data, output_root=out)
    with pytest.raises(Ext2Stop, match="STOP_OVERWRITE"):
        S.guarded_write(out / "fresh.bin", b"\x02", data_root=data, output_root=out)
    with pytest.raises(Ext2Stop, match="STOP_RUN_ROOT_EXISTS"):
        S.create_run_root(out)


def test_gpu_unit_cap_is_per_unit_on_the_parents_clock(tmp_path):  # B5
    receipt, _ = _run(tmp_path / "gpu", units=[("gpu", 5.0)], gpu_unit_sec=0.5)
    assert receipt["reason"] == "STOP_GPU_UNIT_CAP" and receipt["tree_kill"] is True
    receipt, _ = _run(tmp_path / "cpu", units=[("cpu", 5.0)], cpu_unit_sec=0.5)
    assert receipt["reason"] == "STOP_UNIT_CAP"
    # Many short units, each under the cap, complete (the cap is per unit, not cumulative).
    receipt, _ = _run(tmp_path / "many", units=[("gpu", 0.3)] * 5, gpu_unit_sec=0.8)
    assert receipt["status"] == "COMPLETED", receipt["reason"]
    assert [u["seq"] for u in receipt["units"]] == [1, 2, 3, 4, 5]


def test_run_level_hard_stop_and_two_hour_flag(tmp_path):  # B5
    receipt, _ = _run(tmp_path / "hard", units=[("cpu", 0.3)] * 20, hard_stop_sec=1.0, cpu_unit_sec=0.8)
    assert receipt["reason"] == "STOP_HARD_STOP"
    receipt, _ = _run(tmp_path / "flag", units=[("cpu", 0.8)], flag_sec=0.3)
    assert receipt["status"] == "COMPLETED" and receipt["wall_flag_2h"] is True
    receipt, _ = _run(tmp_path / "noflag", units=[("cpu", 0.1)])
    assert receipt["wall_flag_2h"] is False


def test_missing_heartbeat_stops(tmp_path):
    data, out = _roots(tmp_path)
    receipt = S.supervise([sys.executable, "-c", "import time; time.sleep(10)"], data_root=data, output_root=out,
                          disk_free=lambda: FREE, heartbeat_stall_sec=0.5, hard_stop_sec=30)
    assert receipt["reason"] == "STOP_HEARTBEAT"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group")
def test_final_tree_kill_reaps_a_grandchild_and_job_is_measured(tmp_path):
    data, out = _roots(tmp_path)
    script = (
        "import json, os, pathlib, subprocess, sys\n"
        "root = pathlib.Path.cwd()\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "(root / 'grandchild').write_text(str(child.pid))\n"
        "(root / 'heartbeat').write_text('a')\n"
        "(root / 'COMPLETE').write_text(json.dumps({'run_nonce': os.environ['EXT2_RUN_NONCE'],"
        " 'card_sha12': os.environ['EXT2_CARD_SHA12'], 'finished_aedt': 'x'}))\n"
    )
    receipt = S.supervise([sys.executable, "-c", script], data_root=data, output_root=out, disk_free=lambda: FREE,
                          hard_stop_sec=10)
    assert receipt["status"] == "COMPLETED" and receipt["tree_kill"] is True
    assert receipt["job"]["kind"] == "posix_process_group" and receipt["job"]["child_pgid_differs"] is True
    grandchild = int((out / "grandchild").read_text())
    deadline = time.time() + 3
    alive = True
    while time.time() < deadline:
        try:
            os.kill(grandchild, 0)
            with open(f"/proc/{grandchild}/stat") as fh:
                if fh.read().split(")")[-1].split()[0] == "Z":
                    alive = False
                    break
        except OSError:
            alive = False
            break
        time.sleep(0.05)
    assert alive is False


def test_supervise_starts_the_child_through_start_worker(tmp_path, monkeypatch):  # B6
    calls = []
    real = S.start_worker

    def spy(command, *, env, cwd):
        calls.append(cwd)
        return real(command, env=env, cwd=cwd)
    monkeypatch.setattr(S, "start_worker", spy)
    receipt, out = _run(tmp_path)
    assert receipt["status"] == "COMPLETED" and calls == [str(out)]


def test_secret_env_is_scrubbed_and_its_value_cannot_reach_the_receipt(tmp_path):  # N2
    env = dict(os.environ)
    # Names are assembled so secret scanners do not flag these fake test values.
    fake = {"GH_" + "TOKEN": "fake-value-1", "MY_SESSION_ID": "fake-value-2", "X_AUTH": "fake-value-3",
            "DB_PASS": "fake-value-4", "SOME_COOKIE": "fake-value-5"}
    env.update(fake)
    env["LEAKME"] = "harmless"
    receipt, out = _run(tmp_path / "scrub", env=env)
    keys = json.loads((out / "env_keys.json").read_text())
    for name in fake:
        assert name not in keys
    assert "PATH" in keys and "EXT2_RUN_NONCE" in keys
    assert str(out / "tmp") in json.dumps(keys) or "TMPDIR" in keys
    text = (out / "receipt.json").read_text()
    for value in fake.values():
        assert value not in text
    env["SECRET_LEAK"] = "leaky-value-123"
    env["LEAKME"] = "leaky-value-123"
    receipt, _ = _run(tmp_path / "leak", mode="leak", env=env)
    assert receipt["reason"] == "STOP_RECEIPT_SECRET" and receipt["receipt_written"] is False
    with pytest.raises(Ext2Stop, match="STOP_RECEIPT_SECRET"):
        S.seal_receipt({"env": {"A": "b"}})


def test_guard_is_anchored_on_external_data(tmp_path):  # N4 (old HM N4)
    data = tmp_path / "x" / "external-data" / "bitfinex" / "tBTCF0USTF0"
    data.mkdir(parents=True)
    sibling = tmp_path / "x" / "external-data" / "runs" / "r1"
    with pytest.raises(Ext2Stop, match="STOP_OUTPUT_INSIDE_DATA_ROOT"):
        S.assert_roots(data, sibling)
    assert S.external_data_anchor(data) == (tmp_path / "x" / "external-data").resolve()
    with pytest.raises(Ext2Stop, match="STOP_OUTPUT_CONTAINS_DATA_ROOT"):
        S.assert_roots(data, tmp_path / "x")
    S.assert_roots(data, tmp_path / "runs" / "r1")


def test_static_import_audit_flags_network_and_dynamic_imports():
    assert S.import_violations("import socket\nimport importlib\nfrom urllib import request\n") == [
        "importlib", "socket", "urllib"]
    assert S.import_violations("import numpy\nfrom research import ext2_rules\n") == []


def test_runtime_network_hook_blocks_a_connect_in_the_worker(tmp_path):  # N3 (old HM)
    here = Path(__file__).resolve().parent
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from research.ext2_supervisor import install_network_audit_hook\n"
        "install_network_audit_hook()\n"
        "import socket\n"
        "try:\n"
        "    socket.getaddrinfo('localhost', 80)\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__, getattr(exc, 'reason', ''))\n" % str(here)
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert "Ext2Stop STOP_NETWORK" in out.stdout


def test_require_gpu_stops_before_the_run_root_is_created(tmp_path):
    data, out = _roots(tmp_path)
    receipt = S.supervise([sys.executable, "-c", "pass"], data_root=data, output_root=out, disk_free=lambda: FREE,
                          require_gpu=True, cuda_check=lambda: False)
    assert receipt["reason"] == "STOP_GPU_MISSING" and not out.exists()
    for key in S.RECEIPT_REQUIRED_KEYS:
        assert key in receipt


def test_low_disk_does_not_start_the_child(tmp_path):
    data, out = _roots(tmp_path)
    receipt = S.supervise([sys.executable, "-c", "pass"], data_root=data, output_root=out, disk_free=lambda: 1)
    assert receipt["reason"] == "STOP_DISK_FLOOR" and receipt["child_started"] is False


def test_heartbeat_thread_does_not_enforce_anything(tmp_path):
    beat = S.HeartbeatThread(tmp_path / "hb", interval_sec=0.01)
    beat.start()
    time.sleep(0.05)
    beat.stop()
    assert (tmp_path / "hb").read_text() == "alive"


def test_job_struct_sizes_match_the_windows_x64_abi():  # B6
    sizes = S.job_struct_sizes()
    if sizes["pointer_bits"] == 64:
        assert sizes == {"basic": 64, "io": 48, "extended": 144, "threadentry32": 28, "pointer_bits": 64}



# ---- B13: card v3 section 6 resource limits and section 10 STOP 7 (push 4) ----
GB = 1024 ** 3


class FakeProbe:
    """psutil-like fake: free RAM and tree RSS are scripted; priority is recorded."""

    def __init__(self, free=(64 * GB,), rss=(1 * GB,), priority_ok=True):
        self.free = list(free)
        self.rss = list(rss)
        self.priority_ok = priority_ok
        self.calls = 0

    def available_bytes(self):
        value = self.free[min(self.calls, len(self.free) - 1)]
        self.calls += 1
        return value

    def tree_rss_bytes(self, pid, pgid, job=None):
        return self.rss[min(self.calls - 1, len(self.rss) - 1)]

    def set_below_normal(self, proc):
        return {"kind": "fake", "ok": self.priority_ok}


def test_resource_constants_are_the_card_values():
    assert S.MAX_THREADS == 8 and S.RAM_CAP_BYTES == 24 * GB and S.RAM_PAUSE_FREE_BYTES == 8 * GB
    assert S.RAM_STOP_SEC == 600 and S.BELOW_NORMAL_PRIORITY_CLASS == 0x4000 and S.POSIX_NICE == 10
    env = S.thread_limit_env()
    assert set(env) >= {"OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"} and set(env.values()) == {"8"}
    with pytest.raises(Ext2Stop, match="STOP_THREADS_EXTENDED"):
        S.thread_limit_env(9)


def test_guard_pauses_below_8gb_and_stops_after_10_min_on_the_injected_clock():  # STOP 7
    probe = FakeProbe(free=[16 * GB, 7 * GB, 7 * GB, 7 * GB, 9 * GB, 7 * GB, 7 * GB])
    g = S.ResourceGuard(probe)
    assert g.check(0.0) == (False, None)
    assert g.check(10.0) == (True, None)          # low: pause new work
    assert g.check(610.0) == (True, None)         # exactly 600 s low: not yet (> 10 min)
    assert g.check(610.5) == (True, "STOP_RAM_FLOOR")
    g2 = S.ResourceGuard(FakeProbe(free=[7 * GB, 9 * GB, 7 * GB, 7 * GB]))
    assert g2.check(0.0) == (True, None)
    assert g2.check(500.0) == (False, None)       # recovered: clock resets, pause lifted
    assert g2.check(501.0) == (True, None)
    assert g2.check(1000.0) == (True, None)       # 499 s since the reset, not 1000
    assert g2.pause_events == 2 and g2.min_free == 7 * GB


def test_guard_stops_on_the_24gb_cap_and_when_ram_cannot_be_measured():
    g = S.ResourceGuard(FakeProbe(free=[32 * GB], rss=[25 * GB]))
    assert g.check(0.0, pid=1, pgid=1) == (False, "STOP_RAM_CAP")
    assert S.ResourceGuard(FakeProbe(free=[None])).check(0.0)[1] == "STOP_RAM_PROBE"
    ok = S.ResourceGuard(FakeProbe(free=[32 * GB], rss=[24 * GB]))
    assert ok.check(0.0, pid=1, pgid=1) == (False, None) and ok.max_rss == 24 * GB


def test_ram_limits_cannot_be_loosened():
    data = Path(os.devnull).parent
    for kw, reason in (({"ram_stop_sec": 601}, "STOP_RAM_STOP_EXTENDED"),
                       ({"ram_cap_bytes": 25 * GB}, "STOP_RAM_CAP_EXTENDED")):
        receipt = S.supervise([sys.executable, "-c", "pass"], data_root=data / "x", output_root=data / "y",
                              disk_free=lambda: FREE, probe=FakeProbe(), **kw)
        assert receipt["reason"] == reason and receipt["child_started"] is False


def test_supervisor_writes_pause_and_stops_on_low_ram_with_a_receipt_reason(tmp_path):  # B13 wired
    probe = FakeProbe(free=[64 * GB] + [4 * GB] * 10_000)
    receipt, out = _run(tmp_path, units=[("cpu", 30)], probe=probe, ram_stop_sec=1.0)
    assert receipt["reason"] == "STOP_RAM_FLOOR" and receipt["stop_reason"] == "STOP_RAM_FLOOR"
    assert receipt["tree_kill"] is True
    limits = receipt["resource_limits"]
    assert limits["pause_events"] == 1 and limits["min_free_bytes"] == 4 * GB and limits["applied"] is True
    on_disk = json.loads((out / "receipt.json").read_text())
    assert on_disk["stop_reason"] == "STOP_RAM_FLOOR"
    assert (out / S.PAUSE_NAME).exists()   # pause was signalled to the worker


def test_supervisor_lifts_the_pause_when_ram_recovers(tmp_path):
    probe = FakeProbe(free=[64 * GB, 64 * GB, 4 * GB, 4 * GB] + [64 * GB] * 10_000)
    receipt, out = _run(tmp_path, units=[("cpu", 4.5)], probe=probe)
    assert receipt["status"] == "COMPLETED", receipt["reason"]
    assert receipt["resource_limits"]["pause_events"] == 1 and not (out / S.PAUSE_NAME).exists()


def test_supervisor_stops_on_the_ram_cap_and_on_failed_priority(tmp_path):
    receipt, _ = _run(tmp_path / "a", units=[("cpu", 30)], probe=FakeProbe(rss=[30 * GB]))
    assert receipt["reason"] == "STOP_RAM_CAP"
    receipt, _ = _run(tmp_path / "b", units=[("cpu", 30)], probe=FakeProbe(priority_ok=False))
    assert receipt["reason"] == "STOP_PRIORITY" and receipt["tree_kill"] is True


def test_child_gets_thread_caps_and_real_below_normal_priority(tmp_path):
    receipt, out = _run(tmp_path)
    assert receipt["status"] == "COMPLETED", receipt["reason"]
    keys = json.loads((out / "env_keys.json").read_text())
    assert set(S.THREAD_ENV_KEYS) <= set(keys)
    pri = receipt["resource_limits"]["priority"]
    assert pri["ok"] is True
    if os.name != "nt":
        assert pri["kind"] == "posix_nice" and pri["measured"] >= 10


def test_pause_unit_has_its_own_cap(tmp_path):
    receipt, _ = _run(tmp_path, units=[("pause", 30)], probe=FakeProbe(), ram_stop_sec=0.5)
    assert receipt["reason"] == "STOP_PAUSE_CAP"


def test_job_limits_set_below_normal_and_24gb_job_memory():  # Windows Job fields, any OS
    _, _, extended, _ = S._job_structs()
    info = S.fill_job_limits(extended())
    flags = info.BasicLimitInformation.LimitFlags
    assert flags & S.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and flags & S.JOB_OBJECT_LIMIT_PRIORITY_CLASS
    assert flags & S.JOB_OBJECT_LIMIT_JOB_MEMORY
    assert info.BasicLimitInformation.PriorityClass == S.BELOW_NORMAL_PRIORITY_CLASS
    assert info.JobMemoryLimit == 24 * GB


def test_worker_thread_attestation_caps_torch():
    class FakeTorch:
        n = 12

        def set_num_threads(self, n):
            self.n = n

        def get_num_threads(self):
            return self.n
    env = S.thread_limit_env()
    out = S.apply_worker_thread_limits(env, FakeTorch())
    assert out["ok"] is True and out["torch_threads"] == 8
    bad = S.apply_worker_thread_limits({**env, "OMP_NUM_THREADS": "12"}, FakeTorch())
    assert bad["ok"] is False
    assert S.apply_worker_thread_limits({}, FakeTorch())["ok"] is False


def test_in_run_disk_floor_stops_the_child(tmp_path):  # H26
    calls = {"n": 0}

    def disk():
        calls["n"] += 1
        return FREE if calls["n"] == 1 else S.DISK_RUN_FLOOR_BYTES - 1
    receipt, _ = _run(tmp_path, units=[("cpu", 30)], disk_free=disk, probe=FakeProbe())
    assert receipt["reason"] == "STOP_DISK_RUN" and receipt["tree_kill"] is True


def test_receipt_binds_frozen_files_and_carries_the_gate6_wording(tmp_path):  # RK D1 / D4
    receipt, _ = _run(tmp_path, probe=FakeProbe())
    names = [b["name"] for b in receipt["frozen_bindings"]]
    assert names == ["card_v4_launch", "card_v3", "build_spec_addendum_v3", "rk_ruling_pr996_gate6_h25_receipt",
                     "hm_r2_receipt_merged_head", "push_card_3", "push_card_4"]
    assert receipt["gate6_disclosure"] == "gate 6 nominal-level; gate 7 White RC binding"
    from research import ext2_rules
    assert ext2_rules.GATE6_DISCLOSURE == S.GATE6_DISCLOSURE
    with pytest.raises(Ext2Stop, match="STOP_BINDING_UNKNOWN"):
        S.frozen_bindings({"rulings_log": "ab" * 32})
