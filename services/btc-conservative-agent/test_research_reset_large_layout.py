"""Reset inventory/execution over a layout larger than the old 200k scan budget.

Mirrors the live Fly volume: hundreds of thousands of retained emergency-receipt
files (scanned, never targeted) plus a small number of real targets.
"""
import os

import pytest

import research_reset_execution as execution
import research_reset_inventory as inventory
from research_exact_deletion import ResearchDeletionRejected
from test_research_reset_execution import put, settings

RETAINED = 410_000  # > 2x the old 200k budget, ~ the live 394k volume
TARGETS = 40


def _layout(tmp_path):
    args = settings(tmp_path)
    root = args["runtime_root"]
    base = root / "v3" / "receipts" / "emergency_record_idempotency_v1"
    for shard in range(200):
        (base / f"{shard:03d}").mkdir(parents=True)
    for index in range(RETAINED):
        with open(base / f"{index % 200:03d}" / f"{index}.json", "wb"):
            pass
    targets = [put(root, f"v3/market_segments/{n:02x}/{n:02x}{n:062x}.json") for n in range(TARGETS)]
    return args, root, base, targets


def test_limits_are_hard_caps_above_live_count():
    assert inventory.RESET_MAX_ENTRIES == 1_000_000 > 2 * 394_000
    with pytest.raises(ValueError):
        inventory.plan_research_reset("/nonexistent", max_entries=inventory.RESET_MAX_ENTRIES + 1)


def test_over_old_cap_layout_plans_executes_and_is_idempotent(tmp_path):
    args, root, base, targets = _layout(tmp_path)
    with pytest.raises(ResearchDeletionRejected, match="INCOMPLETE"):
        execution.execute_research_reset(**args, max_entries=200_000)
    with pytest.raises(ResearchDeletionRejected, match="INCOMPLETE"):
        execution.execute_research_reset(**args, max_entries=RETAINED)
    assert all(t.exists() for t in targets)
    pre = execution.execute_research_reset(**args, validate_only=True)
    assert pre["status"] == "VALIDATED" and pre["target_count"] == TARGETS
    result = execution.execute_research_reset(**args)
    assert result["status"] == "COMPLETE"
    assert not any(t.exists() for t in targets)
    assert result["retained_count"] >= RETAINED
    assert sum(1 for _ in os.scandir(base / "000")) == RETAINED // 200
    args2 = dict(args, receipt_path=args["receipt_path"].with_name("reset2.json"))
    again = execution.execute_research_reset(**args2, validate_only=True)
    assert again["target_count"] == 0
    assert sum(1 for _ in os.scandir(base / "199")) == RETAINED // 200


def test_mid_run_abort_leaves_retained_and_rerun_finishes(tmp_path, monkeypatch):
    args, root, base, targets = _layout(tmp_path)
    real_unlink = os.unlink
    calls = {"n": 0}

    def flaky(path, *a, **kw):
        if str(path).endswith(".json") and "market_segments" in str(path):
            calls["n"] += 1
            if calls["n"] == TARGETS // 2:
                raise OSError("simulated crash mid-delete")
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", flaky)
    with pytest.raises(Exception):
        execution.execute_research_reset(**args)
    monkeypatch.setattr(os, "unlink", real_unlink)
    remaining = [t for t in targets if t.exists()]
    assert 0 < len(remaining) < TARGETS
    assert sum(1 for _ in os.scandir(base / "007")) == RETAINED // 200
    args2 = dict(args, receipt_path=args["receipt_path"].with_name("reset-resume.json"))
    result = execution.execute_research_reset(**args2)
    assert result["status"] == "COMPLETE"
    assert not any(t.exists() for t in targets)
    assert sum(1 for _ in os.scandir(base / "007")) == RETAINED // 200


def test_max_files_is_a_hard_cap_above_old_limit():
    import research_exact_deletion as deletion
    assert deletion.MAX_RESET_FILES == 600_000 > 100_000
    assert execution.MAX_RESET_FILES == deletion.MAX_RESET_FILES
