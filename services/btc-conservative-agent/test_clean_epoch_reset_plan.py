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


def test_cli_exit_codes(tmp_path, capsys):
    root = _runtime(tmp_path)
    assert crp.main(["plan", "--runtime-root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "plan"
    assert crp.main(["verify", "--runtime-root", str(root), "--epoch", EPOCH]) == 2
