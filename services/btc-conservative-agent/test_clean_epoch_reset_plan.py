"""Clean-epoch boundary reset: inventory keeps the 1 s tape, read-only plan/verify for /api/wipe_fly_only."""
from __future__ import annotations

import json
from pathlib import Path

import clean_epoch_reset_plan as crp
import data_epoch
from research_reset_inventory import plan_research_reset

EPOCH = "ce-20261004-v31-clean"
STARTED = 1_790_000_000.0


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _runtime(root: Path) -> Path:
    data_epoch.ensure_runtime_manifest(root, EPOCH, now=STARTED)
    _write(root / "market_microstructure_1s.jsonl", [{"ts": STARTED + 5}])
    _write(root / "market_microstructure_1s.jsonl.3", [{"ts": STARTED - 86_400}])
    _write(root / "research_events_v22.jsonl", [{"ts": STARTED - 60}])
    _write(root / "v3" / "ledgers" / "execution.jsonl", [{"ts": STARTED - 60}])
    _write(root / "v3" / "ledgers" / "decision.jsonl", [{"ts": STARTED - 60}])
    pending = [{"event_id": f"evt-{i:02d}", "trade_id": f"t{i}"} for i in range(22)]
    (root / "paper_lifecycle_v1.json").write_text(json.dumps(
        {"schema": "paper_lifecycle_v1", "relay_events": {"pending": pending, "acks": []}}), encoding="utf-8")
    return root


def test_inventory_retains_epoch_independent_tape_and_rotations(tmp_path):
    result = plan_research_reset(str(_runtime(tmp_path)))
    reasons = {row["path"]: row["reason"] for row in result["retained"]}
    assert reasons["market_microstructure_1s.jsonl"] == "RETAINED_EPOCH_INDEPENDENT_MARKET_DATA"
    assert reasons["market_microstructure_1s.jsonl.3"] == "RETAINED_EPOCH_INDEPENDENT_MARKET_DATA"
    assert reasons["paper_lifecycle_v1.json"] == "ESSENTIAL_ORDER_PAPER_OR_ACCOUNTING_STATE"
    assert reasons["v3/ledgers/execution.jsonl"] == crp.CANDIDATE_REASON
    assert not result["targets"]


def test_plan_is_read_only_and_lists_v3_and_research_candidates(tmp_path):
    root = _runtime(tmp_path)
    before = sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
    out = crp.plan(str(root))
    after = sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
    assert before == after
    assert out["ok"] is True and out["protected_candidates"] == []
    assert out["would_delete"]["RETIRED_EPOCH_V3_LEDGER"]["files"] == 2
    assert "market_microstructure_1s.jsonl" in out["protected_present"]
    assert "paper_lifecycle_v1.json" in out["protected_present"]
    assert out["v3_generation_pointers"] == []


def test_protected_classifier_covers_relay_recovery_tape_and_epoch_state():
    for rel in ("paper_lifecycle_v1.json", "relay_lifecycle_evidence_v1.json", "data_epoch.json",
                "market_microstructure_1s.jsonl.12", "v3/receipts/ledger_generations_v1/execution/ACTIVE.json",
                "data_epoch_boundary/ce-x.v3.json", "recovery/x.json"):
        assert crp.protected(rel), rel
    for rel in ("v3/ledgers/execution.jsonl", "research_events_v22.jsonl.4"):
        assert not crp.protected(rel), rel


def _simulate_reset(root: Path) -> None:
    for path in (root / "v3" / "ledgers").glob("*.jsonl*"):
        path.unlink()
    (root / "research_events_v22.jsonl").unlink()


def test_verify_passes_after_reset_with_current_epoch_rows(tmp_path):
    root = _runtime(tmp_path)
    expected = crp.plan(str(root))["protected_present"]
    _simulate_reset(root)
    _write(root / "v3" / "ledgers" / "decision.jsonl", [{"ts": STARTED + 600, "data_epoch_id": EPOCH}])
    out = crp.verify(str(root), EPOCH, expected)
    assert out["ok"] is True, out["failures"]
    assert out["v3_heads"]["execution"]["head_decision"] == "ABSENT"
    assert out["v3_heads"]["decision"]["head_decision"] == "ALREADY_CURRENT"


def test_verify_fails_closed_on_pre_epoch_rows_pointers_or_missing_protected(tmp_path):
    root = _runtime(tmp_path)
    expected = crp.plan(str(root))["protected_present"]
    out = crp.verify(str(root), EPOCH, expected)
    assert "V3_PRE_EPOCH_ROWS:execution" in out["failures"]
    _simulate_reset(root)
    pointer = root / crp.GENERATION_POINTERS / "execution" / "ACTIVE.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("{}", encoding="utf-8")
    (root / "market_microstructure_1s.jsonl").unlink()
    failures = crp.verify(str(root), EPOCH, expected)["failures"]
    assert "V3_GENERATION_POINTER_SURVIVED" in failures
    assert "PROTECTED_FILE_MISSING:market_microstructure_1s.jsonl" in failures
    assert crp.verify(str(root), "ce-other", expected)["ok"] is False


def test_relay_evidence_is_counted_and_verify_detects_change(tmp_path):
    root = _runtime(tmp_path)
    before = crp.plan(str(root))["relay_evidence"]
    assert before["pending"] == 22 and len(before["pending_event_ids"]) == 22
    _simulate_reset(root)
    assert crp.verify(str(root), EPOCH, None, before["pending_ids_sha256"])["ok"] is True
    (root / "paper_lifecycle_v1.json").write_text(json.dumps(
        {"schema": "paper_lifecycle_v1", "relay_events": {"pending": [], "acks": []}}), encoding="utf-8")
    out = crp.verify(str(root), EPOCH, None, before["pending_ids_sha256"])
    assert "RELAY_PENDING_EVIDENCE_CHANGED" in out["failures"]


def _proof(root: Path) -> dict:
    from research_reset_inventory import PROOF_SCHEMA
    return {"schema": PROOF_SCHEMA, "runtime_root": str(root), "retired_epoch_id": "old",
            "new_epoch_id": "new", "source_revision": "a" * 40, "recovery_receipt_sha256": "b" * 64,
            "writers_quiesced": True, "paper_only": True, "live_disarmed": True, "epoch_retired": True,
            "pending_paper_orders": 0, "open_paper_positions": 0, "pending_wal_records": 0,
            "pending_recovery_records": 0}


def _execute_preflight(root: Path):
    from research_reset_execution import execute_research_reset
    (root / "research_reset_receipts").mkdir(exist_ok=True)
    return execute_research_reset(runtime_root=root, proof=_proof(root), quiescent=True,
                                  recovery_states={"emergency_wal": "NOT_PRESENT"},
                                  receipt_path=root / "research_reset_receipts" / "x" / "deletion.json",
                                  validate_only=True)


def test_plan_execute_gates_match_the_executor_targets(tmp_path):
    root = _runtime(tmp_path)
    out = crp.plan(str(root))
    gates = out["execute_gates"]
    assert gates["ok"] is True, gates["failures"]
    scope = gates["scopes"][0]
    assert scope["status"] == "ADMITTED" and scope["scope"] == "runtime"
    executed = _execute_preflight(root)
    assert executed["status"] == "VALIDATED"
    assert scope["target_count"] == executed["target_count"] == out["would_delete_files"]
    assert scope["retained_count"] == executed["retained_count"]
    assert scope["receipt_context_bytes"] < 4096


def test_plan_fails_exactly_when_execute_would_refuse_the_receipt_context(tmp_path, monkeypatch):
    import research_exact_deletion
    from research_exact_deletion import ResearchDeletionRejected
    root = _runtime(tmp_path)
    monkeypatch.setattr(research_exact_deletion, "MAX_RETAINED_ROWS", 1)
    out = crp.plan(str(root))
    assert out["ok"] is False
    assert out["execute_gates"]["failures"] == ["runtime:INVALID_RETAINED_METADATA"]
    try:
        _execute_preflight(root)
    except ResearchDeletionRejected as exc:
        assert str(exc) == "INVALID_RETAINED_METADATA"
    else:
        raise AssertionError("execute admitted what the plan refused")


def test_plan_fails_on_target_budget_and_v3_identity_conflict(tmp_path, monkeypatch):
    import research_reset_execution
    root = _runtime(tmp_path)
    original = research_reset_execution.admit_research_reset_plan
    monkeypatch.setattr(research_reset_execution, "admit_research_reset_plan",
                        lambda plan, **kw: original(plan, **{**kw, "max_files": 1}))
    assert "runtime:RESET_TARGET_BUDGET_EXCEEDED" in crp.plan(str(root))["execute_gates"]["failures"]
    monkeypatch.setattr(research_reset_execution, "admit_research_reset_plan", original)
    markers = root / "v3" / "receipts" / "emergency_record_idempotency_v1"
    for ledger, rev in (("decision", "1" * 40), ("execution", "2" * 40)):
        (markers / ledger).mkdir(parents=True)
        (markers / ledger / "complete.json").write_text(json.dumps(
            {"schema": "emergency_record_index_complete_v1", "ledger": ledger,
             "identity": {"epoch_id": "e", "source_revision": rev, "deployed_revision": rev,
                          "tile_config_signature": "a" * 64}}), "utf-8")
    failures = crp.plan(str(root))["execute_gates"]["failures"]
    assert any(f.startswith("V3_IDENTITY_OR_WAL:") and "identity conflict" in f for f in failures)


def test_cli_exit_codes(tmp_path, capsys):
    root = _runtime(tmp_path)
    assert crp.main(["plan", "--runtime-root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "plan"
    assert crp.main(["verify", "--runtime-root", str(root), "--epoch", EPOCH]) == 2


def _pointer(root: Path, reset_id: str, stage: str = "FAILED") -> dict:
    import hashlib
    receipts = root / "research_reset_receipts"
    (receipts / reset_id).mkdir(parents=True, exist_ok=True)
    files = {"active": receipts / "ACTIVE_RESET.json", "binding": receipts / reset_id / "binding.json",
             "operation": receipts / reset_id / "operation.json"}
    files["binding"].write_text(json.dumps({"reset_anchor": 1}))
    files["operation"].write_text(json.dumps({"stage": stage}))
    files["active"].write_text(json.dumps({"reset_id": reset_id}))
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in files.items()}


def test_active_reset_gate_predicts_execute_binding(tmp_path, monkeypatch):
    import research_reset_predeletion_abort as abort
    root = _runtime(tmp_path)
    assert crp.active_reset_gate(root)["status"] == "ABSENT"
    _pointer(root, "a" * 24, stage="COMPLETE")
    assert crp.active_reset_gate(root)["status"] == "COMPLETE"
    hashes = _pointer(root, "b" * 24)
    assert crp.active_reset_gate(root)["status"] == "UNREGISTERED_ACTIVE_POINTER"
    monkeypatch.setattr(abort, "ADDITIONAL_REVIEWED_ATTEMPTS", {"b" * 24: {
        "hashes": hashes, "restart_continuity": {}}})
    assert crp.active_reset_gate(root) == {"status": "RETIREABLE_REVIEWED_ATTEMPT", "reset_id": "b" * 24}
    (root / "research_reset_receipts" / ("b" * 24) / "deletion.json.progress.jsonl").write_text("")
    assert crp.active_reset_gate(root)["status"] == "REGISTERED_ATTEMPT_CHANGED"


def test_plan_fails_on_unretireable_active_pointer(tmp_path):
    root = _runtime(tmp_path)
    _pointer(root, "c" * 24)
    result = crp.plan(str(root))
    assert result["ok"] is False
    assert "RESET_ACTIVE_POINTER_NOT_RETIREABLE:UNREGISTERED_ACTIVE_POINTER" in result["execute_gates"]["failures"]


def test_plan_reports_target_churn_without_blocking(tmp_path, monkeypatch):
    root = _runtime(tmp_path)
    real = crp._candidate_sample
    def churn(path):
        (root / "indicator_bars_v1.jsonl").open("a").write('{"x":1}\n')
        return real(path)
    monkeypatch.setattr(crp, "_candidate_sample", churn)
    baseline = crp.plan(str(root))
    stability = baseline["target_stability"]
    assert stability["blocking"] is False
    assert any(row["path"] == "indicator_bars_v1.jsonl" for row in stability["changed"])
    monkeypatch.setattr(crp, "_candidate_sample", real)
    assert crp.plan(str(root))["target_stability"]["changed_count"] == 0
