"""Runner: A19 order, STOP receipts carry every A18 key, nonce marker, pins, CUDA
pre-start, exclusive root, and a synthetic smoke run (push 3). No real data."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from ext2_test_data import small_calendar, synth_trades
from research import ext2_runner as R
from research import ext2_supervisor as S
from research.ext2_lattice import NumpyBackend

HERE = Path(__file__).resolve().parent


def _roots(tmp_path):
    data = tmp_path / "external-data" / "bitfinex"
    data.mkdir(parents=True)
    return data, tmp_path / "runs" / "r1"


def _keys_present(receipt):
    missing = [k for k in S.RECEIPT_REQUIRED_KEYS if k not in receipt]
    assert not missing, missing


def test_real_mode_without_a_gpu_stops_and_still_writes_every_key(tmp_path):
    data, out = _roots(tmp_path)
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n1", cuda_check=lambda: False)
    assert r["status"] == "STOP" and r["stop_reason"] == "STOP_GPU_MISSING"
    _keys_present(r)
    on_disk = json.loads((out / "run_receipt.json").read_text())
    assert on_disk["stop_reason"] == "STOP_GPU_MISSING" and on_disk["label"] == R.rules.LABEL
    assert on_disk["p_min_attainable_computed"]["by_T"]["20"]["reachable"] is True
    assert on_disk["fee_bp"] == 0
    assert not (out / "COMPLETE").exists()


def test_cpu_fallback_flag_and_non_frozen_calendar_are_refused(tmp_path):
    data, out = _roots(tmp_path)
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", allow_cpu_fallback=True)
    assert r["stop_reason"] == "STOP_CPU_FALLBACK_FORBIDDEN"
    data, out = _roots(tmp_path / "b")
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", calendar=small_calendar(),
                   cuda_check=lambda: True)
    assert r["stop_reason"] == "STOP_CALENDAR"


def test_shipped_import_pins_match_and_a_mismatch_stops(tmp_path):
    S.verify_import_pins(R.PIN_PATH, R.SERVICE_ROOT)
    pinned = set(json.loads(R.PIN_PATH.read_text())["files"])
    assert pinned == {f"research/{name}" for name in R.SOURCE_FILES}
    bad = tmp_path / "pins.json"
    bad.write_text(json.dumps({"files": {"research/ext2_rules.py": "0" * 64}}))
    data, out = _roots(tmp_path)
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", pin_path=bad)
    assert r["stop_reason"] == "STOP_IMPORT_PIN"


def test_root_reuse_stale_complete_and_missing_nonce_stop(tmp_path, monkeypatch):
    data, out = _roots(tmp_path)
    out.mkdir(parents=True)
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n")
    assert r["stop_reason"] == "STOP_RUN_ROOT_EXISTS"
    assert not (out / "run_receipt.json").exists()
    (out / "COMPLETE").write_text("{}")
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", supervised=True)
    assert r["stop_reason"] == "STOP_STALE_COMPLETE"
    assert (out / "COMPLETE").read_text() == "{}"
    monkeypatch.delenv(S.NONCE_ENV, raising=False)
    data2, out2 = _roots(tmp_path / "c")
    r = R.run_ext2(data_root=data2, output_root=out2)
    assert r["stop_reason"] == "STOP_NO_RUN_NONCE"


def test_cuda_prestart_fails_closed(tmp_path):
    class Proc:
        stdout = "Quadro P1000, 4096 MiB, 550.00"
    pre = R.cuda_prestart(NumpyBackend(), run_cmd=lambda *a, **k: Proc())
    assert pre["ok"] is False and pre["fail"] == "nvidia_smi_not_t2000"
    data, out = _roots(tmp_path)
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", cuda_check=lambda: True,
                   backend=NumpyBackend(), prestart=lambda b: {"ok": False, "fail": "parity"})
    assert r["stop_reason"] == "STOP_CUDA_PRESTART"
    assert r["cuda_prestart"]["fail"] == "parity"


def test_real_mode_refuses_a_cpu_lattice_even_after_prestart(tmp_path, monkeypatch):
    seal = tmp_path / "h1.seal"
    seal.write_text("seal")
    monkeypatch.setattr(R, "H1_SEAL_SHA", hashlib.sha256(b"seal").hexdigest())
    data, out = _roots(tmp_path)
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", cuda_check=lambda: True,
                   backend=NumpyBackend(), prestart=lambda b: {"ok": True}, h1_seal_path=seal)
    assert r["stop_reason"] == "STOP_GPU_FALLBACK"
    data, out = _roots(tmp_path / "noseal")
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", cuda_check=lambda: True,
                   backend=NumpyBackend(), prestart=lambda b: {"ok": True})
    assert r["stop_reason"] == "STOP_H1_SEAL"


def test_lattice_parity_check_passes_on_numpy_and_torch_cpu():
    assert R.lattice_parity(NumpyBackend())["pass"] is True
    torch = pytest.importorskip("torch")
    from research.ext2_lattice import make_backend
    res = R.lattice_parity(make_backend("torch", "cpu"))
    assert res["pass"] is True and res["dtype"] == "float64" and torch is not None


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("smoke")
    cal = small_calendar()
    tr = synth_trades(cal.days(), seed=7)
    data, out = _roots(tmp)
    r = R.run_ext2(data_root=data, output_root=out, mode="smoke", calendar=cal, trades=tr, run_nonce="nonce-1")
    return r, out


def test_smoke_run_completes_with_every_output_and_key(smoke):
    r, out = smoke
    assert r["status"] == "COMPLETED", r["stop_reason"]
    _keys_present(r)
    assert r["label"] == R.SMOKE_LABEL and r["allowlist_sha"] == "SMOKE-synthetic"
    for name in ("cells.npz", "leads.json", "fixtures.json", "canary.json", "notes_not_tested.md",
                 "dry_check.json", "run_receipt.json", "COMPLETE"):
        assert (out / name).is_file(), name
    marker = json.loads((out / "COMPLETE").read_text())
    assert marker == {"run_nonce": "nonce-1", "card_sha12": S.CARD_SHA_V3[:12],
                      "finished_aedt": marker["finished_aedt"]}
    cells = np.load(out / "cells.npz")["cells"]
    assert cells.shape == (181442,)
    assert r["lattice"]["dtype"] == "float64"
    assert r["fixtures"] == {"C3b": True, "C3b_lattice": True, "C3c": True, "canary": True}
    # Push 4: CPU phases after the lattice are their own CPU units (600 s cap), not the last GPU chunk.
    names = [(u["name"], u["device"]) for u in r["units"]]
    last_chunk = max(i for i, (n, _) in enumerate(names) if n.startswith("lattice_chunk_"))
    assert names[last_chunk + 1:last_chunk + 4] == [("cell_chains", "cpu"), ("switching", "cpu"), ("gates", "cpu")]
    # RK D1 interim wording, D3 f6 disclosure, D4 frozen bindings, B13 thread attestation.
    assert r["gate6_disclosure"] == "gate 6 nominal-level; gate 7 White RC binding"
    assert r["gate6_method"] == "hac_t_student_T1" and r["gate6_nominal_only"] is True
    assert r["pair_thesis_rule"] == "lowest_id"
    f6 = r["f6_eligibility_disclosure"]
    assert f6["feature"] == "f6 large_print_L" and "untested, p = 1" in f6["rule"]
    assert len(f6["eligible_share_by_L_and_fold"]) == 4
    assert all(set(v) == {"F1", "F2", "F3", "F4"} for v in f6["eligible_share_by_L_and_fold"].values())
    bind = {b["name"]: b for b in r["frozen_bindings"]}
    assert bind["card_v3"]["sha256"] == S.CARD_SHA_V3
    assert bind["build_spec_addendum_v3"]["sha256"].startswith("7a18903b")
    assert bind["rk_ruling_pr996_gate6_h25_receipt"]["sha256"].startswith("9ac982877fbc")
    assert bind["push_card_3"]["sha256"].startswith("46f2868d42fd")
    assert bind["card_v4_launch"]["pending"] is True and bind["hm_r2_receipt_merged_head"]["pending"] is True
    assert "rulings_log" not in " ".join(bind)
    assert r["thread_limits"]["max_threads"] == 8
    assert r["dual_c2_projection_hours"] > 0 and r["projection"]["lattice_measured_fp64_on_device"] is False
    for name, digest in r["outputs"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest


def test_supervised_smoke_worker_end_to_end(tmp_path):
    data, out = _roots(tmp_path)
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from research.ext2_supervisor import install_network_audit_hook, HeartbeatThread\n"
        "install_network_audit_hook()\n"
        "import pathlib\n"
        "from ext2_test_data import small_calendar, synth_trades\n"
        "from research.ext2_runner import run_ext2\n"
        "cal = small_calendar()\n"
        "hb = HeartbeatThread(pathlib.Path.cwd() / 'heartbeat', 1.0); hb.start()\n"
        "r = run_ext2(data_root=pathlib.Path(%r), output_root=pathlib.Path.cwd(), mode='smoke', calendar=cal,\n"
        "             trades=synth_trades(cal.days(), seed=3), supervised=True)\n"
        "hb.stop()\n"
        "sys.exit(0 if r['status'] == 'COMPLETED' else 3)\n" % (str(HERE), str(data))
    )
    receipt = S.supervise([sys.executable, "-c", script], data_root=data, output_root=out,
                          disk_free=lambda: S.DISK_FLOOR_BYTES + 1, hard_stop_sec=600, heartbeat_stall_sec=120)
    assert receipt["status"] == "COMPLETED", (receipt["reason"], receipt.get("worker_stop_reason"))
    assert receipt["worker_run_nonce_ok"] is True
    assert receipt["completion_marker_ok"] is True
    names = [u["name"] for u in receipt["units"]]
    assert "features" in names and len(names) >= 3  # units observed on the parent's clock
    seqs = [u["seq"] for u in receipt["units"]]
    assert seqs == sorted(seqs)
    _keys_present(receipt)
    assert receipt["label"] == R.SMOKE_LABEL
    # B13: the parent set the thread env; the worker measured it (<= 8) and the child ran BelowNormal.
    assert receipt["worker_thread_limits"]["ok"] is True
    assert set(receipt["worker_thread_limits"]["thread_env"].values()) == {"8"}
    assert receipt["resource_limits"]["applied"] is True and receipt["resource_limits"]["priority"]["ok"] is True


class _FakeCudaBackend(NumpyBackend):
    device = "cuda:0"


def test_real_mode_needs_every_frozen_binding_before_touching_data(tmp_path, monkeypatch):  # RK D4
    seal = tmp_path / "h1.seal"
    seal.write_text("seal")
    monkeypatch.setattr(R, "H1_SEAL_SHA", hashlib.sha256(b"seal").hexdigest())
    kw = dict(run_nonce="n", cuda_check=lambda: True, backend=_FakeCudaBackend(),
              prestart=lambda b: {"ok": True}, h1_seal_path=seal)
    data, out = _roots(tmp_path / "a")
    r = R.run_ext2(data_root=data, output_root=out, **kw)
    assert r["stop_reason"] == "STOP_BINDING_MISSING"
    pending = sorted(b["name"] for b in r["frozen_bindings"] if b["pending"])
    assert pending == ["card_v4_launch", "hm_r2_receipt_merged_head", "push_card_4"]
    fill = {name: "ab" * 32 for name in pending}
    data, out = _roots(tmp_path / "b")
    r = R.run_ext2(data_root=data, output_root=out, binding_overrides=fill, **kw)
    assert r["stop_reason"] == "STOP_MANIFEST"  # bindings complete -> next gate
    data, out = _roots(tmp_path / "c")
    r = R.run_ext2(data_root=data, output_root=out, binding_overrides={"card_v4_launch": "short"}, **kw)
    assert r["stop_reason"] == "STOP_BINDING_SHA"


def test_unknown_pair_rule_or_gate6_method_stops(tmp_path):  # RK D1 / D2 config keys
    data, out = _roots(tmp_path / "a")
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", mode="smoke", pair_thesis_rule="first")
    assert r["stop_reason"] == "STOP_PAIR_THESIS_RULE"
    data, out = _roots(tmp_path / "b")
    r = R.run_ext2(data_root=data, output_root=out, run_nonce="n", mode="smoke", gate6_method="iid_t")
    assert r["stop_reason"] == "STOP_GATE6_METHOD"


def test_a_failed_c3b_lattice_fixture_stops_the_run(tmp_path, monkeypatch):  # HM R2 non-blocking: wired, enforced
    real = R.pipeline.fixture_c3b_lattice_on_run
    seen = []

    def failing(*args, **kwargs):
        res = dict(real(*args, **kwargs))
        seen.append(res["pass"])
        res["pass"] = False
        return res
    monkeypatch.setattr(R.pipeline, "fixture_c3b_lattice_on_run", failing)
    cal = small_calendar()
    data, out = _roots(tmp_path)
    r = R.run_ext2(data_root=data, output_root=out, mode="smoke", calendar=cal,
                   trades=synth_trades(cal.days(), seed=7), run_nonce="n")
    assert seen == [True]
    assert r["status"] == "STOP" and r["stop_reason"] == "STOP_FIXTURE_C3"
    assert r["fixtures"]["C3b_lattice"] is False and not (out / "COMPLETE").exists()


def test_unit_announcer_pauses_new_work_while_the_pause_file_exists(tmp_path):  # B13
    pause = tmp_path / S.PAUSE_NAME
    pause.write_text("{}")
    waits = []

    def fake_sleep(sec):
        waits.append(sec)
        if len(waits) == 3:
            pause.unlink()
    ann = R.UnitAnnouncer(tmp_path, sleep=fake_sleep)
    ann("load", "cpu")
    assert [(u["name"], u["device"]) for u in ann.log] == [("ram_pause", "pause"), ("load", "cpu")]
    assert len(waits) == 3 and ann.pauses == 1
    assert json.loads((tmp_path / S.UNIT_NAME).read_text())["name"] == "load"
    ann("features", "cpu")
    assert ann.pauses == 1 and ann.log[-1]["name"] == "features"
