"""clean-epoch-wipe: hard KEEP guards, dry-run default and fail-closed execution gates."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

import clean_epoch_wipe as wipe
import data_epoch

EPOCH = "ce-20261004-v31-clean"
DAY = 86400


def _write(path: Path, text: str = "x\n", age_sec: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_sec:
        t = time.time() - age_sec
        os.utime(path, (t, t))
    return path


@pytest.mark.parametrize("rel,reason", [
    ("platform-relay-evidence/2026-10-01/lot.json", "RELAY_BITFINEX_EVIDENCE"),
    ("relay_exec_publisher.jsonl.3", "RELAY_BITFINEX_EVIDENCE"),
    ("bitfinex_live_orders.jsonl.2", "RELAY_BITFINEX_EVIDENCE"),
    ("market_microstructure_1s.jsonl", "MARKET_TAPE_1S"),
    ("market_microstructure_1s.jsonl.14", "MARKET_TAPE_1S"),
    ("tierA/bitfinex_l1_tape_1s/v1/date=2026-09-20/part-0.parquet", "MARKET_TAPE_1S"),
    ("cross_venue_tape_1m.jsonl.2", "MARKET_DATA_EPOCH_INDEPENDENT"),
    ("vault/home-bot.env", "SECRETS"),
    ("secrets/api_key.json", "SECRETS"),
    ("config-7002.json", "CONFIG"),
    ("scripts/run.ps1", "CODE"),
    ("bot.py", "CODE"),
    (".git/objects/ab/cdef", "CODE"),
])
def test_keep_guard_both_scopes(rel, reason):
    for scope in ("laptop", "fly"):
        assert wipe.keep_reason(rel, scope=scope) is not None, (rel, scope)
    assert wipe.keep_reason(rel, scope="fly") == reason or reason == "CODE"


@pytest.mark.parametrize("rel", [
    "v3/ledgers/order_intent.jsonl", "v3/receipts/emergency_record_idempotency_v1/lifecycle/abc",
    "v3/qualification_horizon_index.sqlite3", "research.db", "research_events_v22.index.sqlite3-wal",
    "paper_lifecycle_v1.json", "research_session.json", "lane_pnl_ledger.json", "tile2_counters.json",
    "recovery_receipts/x.json", "trade.lock", "data_epoch.json", "pathway_lane_specs.json", "crash_dump.json",
])
def test_fly_restart_recovery_state_is_always_kept(rel):
    assert wipe.keep_reason(rel, scope="fly")


def test_laptop_copies_of_fly_state_are_not_kept_but_chain_state_is():
    assert wipe.keep_reason("research.db", scope="laptop") is None
    assert wipe.keep_reason("paper_lifecycle_v1.json", scope="laptop") is None
    assert wipe.keep_reason("v3/ledgers/order_intent.jsonl", scope="laptop") is None
    for rel in (".fly-mirror-generation.lease", "acks/laptop/000000002360.json", "data_epoch.json", "x/state.json"):
        assert wipe.keep_reason(rel, scope="laptop") == "LAPTOP_CHAIN_STATE"


def _fly_volume(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    rt = root / "runtime"
    old = 3 * DAY
    _write(rt / "signal_replay.jsonl", age_sec=old)                       # live head: never deleted
    _write(rt / "signal_replay.jsonl.7", age_sec=old)                     # pre-epoch rotation: delete
    _write(rt / "order_multiverse_entry_grid.jsonl.3", age_sec=old)       # digest grid: epoch-age guarded
    for n in range(1, 16):                                                # 15 days of 1 s tape: all kept
        _write(rt / f"market_microstructure_1s.jsonl.{n}", age_sec=n * DAY)
    _write(rt / "relay_lots.jsonl.4", age_sec=old)
    _write(rt / "bitfinex_exec.jsonl.2", age_sec=old)
    _write(rt / "paper_lifecycle_v1.json", age_sec=old)
    _write(rt / "research.db", age_sec=old)
    _write(rt / "v3" / "ledgers" / "execution.jsonl.2", age_sec=old)
    _write(rt / "corrupt_evidence_quarantine" / "a" / "signal_replay.jsonl", age_sec=old)
    _write(rt / "config-7002.json", age_sec=old)
    return root


def _manifest(root: Path, started: float) -> dict:
    doc = data_epoch.new_manifest(EPOCH, started_at_ts=started)
    data_epoch.write_json_atomic(root / "runtime" / data_epoch.MANIFEST_NAME, doc)
    return doc


def test_fly_plan_deletes_only_pre_epoch_rotations_and_quarantine(tmp_path):
    root = _fly_volume(tmp_path)
    now = time.time()
    plan = wipe.Plan("fly", EPOCH, now - 3 * 3600, simulated=False)
    wipe.plan_fly(plan, root, now=now)
    doc = plan.doc()
    deleted = {Path(c["path"]).relative_to(root / "runtime").as_posix() for c in doc["candidates"]}
    assert deleted == {"signal_replay.jsonl.7", "corrupt_evidence_quarantine/a/signal_replay.jsonl"}
    assert doc["kept_by_reason"]["MARKET_TAPE_1S"]["files"] == 15
    assert "RELAY_BITFINEX_EVIDENCE" in doc["kept_by_reason"]
    assert "RESTART_STATE_V3_STORE" in doc["kept_by_reason"]
    # 6 h after the epoch the digest-referenced grid rotation becomes a candidate too
    plan = wipe.Plan("fly", EPOCH, now - 7 * 3600, simulated=False)
    wipe.plan_fly(plan, root, now=now)
    assert any(c["path"].endswith("order_multiverse_entry_grid.jsonl.3") for c in plan.doc()["candidates"])


def test_fly_rotation_written_after_epoch_start_is_kept(tmp_path):
    root = tmp_path / "data"
    _write(root / "runtime" / "post_exit_replay.jsonl.9")
    now = time.time()
    plan = wipe.Plan("fly", EPOCH, now - 3600, simulated=False)
    wipe.plan_fly(plan, root, now=now)
    assert plan.doc()["candidates"] == []


def test_archived_prefix_requires_laptop_ack(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "runtime").mkdir(parents=True)
    _write(root / "segment-store" / "v2" / "seg" / "000000000010.tar.gz", age_sec=DAY)
    _write(root / "segment-store" / "v3" / "seg" / "000000000001.tar.gz")
    state = root / "segment-shipper-v2"
    _write(state / "status.json", json.dumps({"shipped_seq": 10}), age_sec=DAY)
    monkeypatch.setenv("RESEARCH_SEGMENTS_PREFIX", "v3")
    monkeypatch.setenv("RESEARCH_SEGMENTS_ARCHIVE_PREFIXES", f"v2={state}")
    now = time.time()
    plan = wipe.Plan("fly", EPOCH, now - 3 * 3600, simulated=False)
    wipe.plan_fly(plan, root, now=now)
    assert not plan.doc()["candidates"]
    _write(root / "segment-store" / "v2" / "acks" / "laptop" / "000000000010.json", "{}", age_sec=DAY)
    plan = wipe.Plan("fly", EPOCH, now - 3 * 3600, simulated=False)
    wipe.plan_fly(plan, root, now=now)
    paths = {c["path"] for c in plan.doc()["candidates"]}
    assert any("segment-store" in p and "v2" in p for p in paths)
    assert not any(os.sep + "v3" + os.sep in p for p in paths)


def _run(argv):
    return wipe.main(argv)


def test_plan_is_dry_run_and_deletes_nothing(tmp_path, capsys):
    root = _fly_volume(tmp_path)
    before = sorted(p for p in root.rglob("*") if p.is_file())
    assert _run(["--scope", "fly", "--data-root", str(root), "--simulate-now"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["total_delete_files"] >= 1
    assert sorted(p for p in root.rglob("*") if p.is_file()) == before


def test_execute_gates_fail_closed_and_receipt_on_success(tmp_path, capsys):
    root = _fly_volume(tmp_path)
    _manifest(root, time.time() - 3 * 3600)
    base = ["--scope", "fly", "--data-root", str(root), "--epoch", EPOCH]
    assert _run(["execute", "--simulate-now", *base]) == 2                       # preview never executes
    capsys.readouterr()
    assert _run(["execute", *base]) == 5                                         # no confirmation
    capsys.readouterr()
    token = f"DELETE-PRE-EPOCH:{EPOCH}:deadbeef"
    assert _run(["execute", *base, "--confirm", token, "--expect-plan-sha256", "0" * 64]) == 6
    sha = json.loads(capsys.readouterr().out)["plan_sha256"]
    assert _run(["execute", *base, "--confirm", f"DELETE-PRE-EPOCH:ce-20261005-other:deadbeef",
                 "--expect-plan-sha256", sha]) == 5
    capsys.readouterr()
    assert _run(["execute", *base, "--confirm", token, "--expect-plan-sha256", sha]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["executed"] and receipt["deleted_files"] == 2 and receipt["certification_sha8"] == "deadbeef"
    rt = root / "runtime"
    assert not (rt / "signal_replay.jsonl.7").exists()
    for kept in ("signal_replay.jsonl", "relay_lots.jsonl.4", "bitfinex_exec.jsonl.2", "paper_lifecycle_v1.json",
                 "research.db", "v3/ledgers/execution.jsonl.2", "config-7002.json", "market_microstructure_1s.jsonl.15"):
        assert (rt / kept).exists(), kept
    receipts = list((rt / "clean-epoch-receipts" / EPOCH).glob("receipt-fly-*.json"))
    assert len(receipts) == 1 and json.loads(receipts[0].read_text())["plan_sha256"] == sha


def test_fly_execute_refuses_inside_certification_window(tmp_path, capsys):
    root = _fly_volume(tmp_path)
    _manifest(root, time.time() - 600)
    assert _run(["execute", "--scope", "fly", "--data-root", str(root), "--epoch", EPOCH,
                 "--confirm", f"DELETE-PRE-EPOCH:{EPOCH}:deadbeef", "--expect-plan-sha256", "x"]) == 3


def test_execute_refuses_a_file_changed_after_planning(tmp_path):
    root = _fly_volume(tmp_path)
    now = time.time()
    plan = wipe.Plan("fly", EPOCH, now - 3 * 3600, simulated=False)
    wipe.plan_fly(plan, root, now=now)
    doc = plan.doc()
    target = Path(next(c["path"] for c in doc["candidates"] if c["path"].endswith(".jsonl.7")))
    target.write_text("changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        wipe.execute(doc, receipts_dir=tmp_path / "r", scope="fly", cert_sha8="deadbeef")


def test_execute_rechecks_keep_guard_even_for_a_tampered_plan(tmp_path):
    root = _fly_volume(tmp_path)
    tape = root / "runtime" / "market_microstructure_1s.jsonl.3"
    st = tape.stat()
    doc = {"epoch_id": EPOCH, "plan_sha256": "x", "by_root": {}, "kept_by_reason": {},
           "candidates": [{"root": "fly_runtime", "path": str(tape), "rel": "market_microstructure_1s.jsonl.3",
                           "bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "reason": "PRE_EPOCH_SEALED_ROTATION:x"}]}
    receipt = wipe.execute(doc, receipts_dir=tmp_path / "r", scope="fly", cert_sha8="deadbeef")
    assert tape.exists() and receipt["deleted_files"] == 0 and receipt["skipped"][0]["why"] == "KEEP_GUARD_AT_EXECUTION"


def test_laptop_execute_requires_certification(tmp_path, capsys, monkeypatch):
    tree = tmp_path / "tree"
    _write(tree / "signal_replay.jsonl.3", age_sec=3 * DAY)
    _write(tree / "market_microstructure_1s.jsonl.2", age_sec=3 * DAY)
    monkeypatch.setattr(wipe, "LAPTOP_ROOTS", {"mirror_tree": {"path": str(tree), "kind": "mirror"}})
    monkeypatch.setattr(wipe, "ANALYZER_CYCLE_STATUS", str(tmp_path / "none.json"))
    manifest = tmp_path / "data_epoch.json"
    data_epoch.write_json_atomic(manifest, data_epoch.new_manifest(EPOCH, started_at_ts=time.time() - 3 * 3600))
    cert = tmp_path / "certification.json"
    base = ["--scope", "laptop", "--epoch", EPOCH, "--manifest", str(manifest), "--certification", str(cert),
            "--receipts-dir", str(tmp_path / "receipts")]
    assert _run(["execute", *base, "--confirm", "x"]) == 3
    capsys.readouterr()
    doc = data_epoch.certification_doc(data_epoch.load_manifest(manifest),
                                       checks=[{"id": "contracts", "severity": "GREEN"}], now=time.time())
    assert doc["status"] == "CERTIFIED"
    cert.write_text(json.dumps(doc), encoding="utf-8")
    sha8 = hashlib.sha256(cert.read_bytes()).hexdigest()[:8]
    assert _run(["plan", *base]) == 0
    sha = json.loads(capsys.readouterr().out)["plan_sha256"]
    assert _run(["execute", *base, "--confirm", f"DELETE-PRE-EPOCH:{EPOCH}:{sha8}", "--expect-plan-sha256", sha]) == 0
    assert not (tree / "signal_replay.jsonl.3").exists()
    assert (tree / "market_microstructure_1s.jsonl.2").exists()


def test_laptop_execute_refuses_while_analyzer_cycle_runs(tmp_path, capsys, monkeypatch):
    status = tmp_path / "cycle.json"
    status.write_text(json.dumps({"pid": os.getpid(), "finishedAt": None}), encoding="utf-8")
    assert wipe._analyzer_cycle_running(str(status))
    status.write_text(json.dumps({"pid": os.getpid(), "finishedAt": "2026-10-04T00:00:00Z"}), encoding="utf-8")
    assert not wipe._analyzer_cycle_running(str(status))


def test_rejected_or_pending_certification_never_unlocks(tmp_path):
    manifest = data_epoch.new_manifest(EPOCH, started_at_ts=time.time() - 600)
    pending = data_epoch.certification_doc(manifest, checks=[{"id": "a", "severity": "GREEN"}], now=time.time())
    assert pending["status"] == "PENDING" and not data_epoch.certified(pending, EPOCH)
    manifest = data_epoch.new_manifest(EPOCH, started_at_ts=time.time() - 3 * 3600)
    rejected = data_epoch.certification_doc(manifest, checks=[{"id": "a", "severity": "AMBER"}], now=time.time())
    assert rejected["status"] == "REJECTED" and not data_epoch.certified(rejected, EPOCH)
    empty = data_epoch.certification_doc(manifest, checks=[], now=time.time())
    assert empty["status"] == "REJECTED"
