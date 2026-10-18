import os

import retire_obsolete_artifacts as ro

KEEP = [
    "runtime/data_epoch_boundary/ce-20261004-v31-freeze21b.v3.json",
    "runtime/data_epoch_boundary/ce-20261004-v31-freeze21b.research_events_v22.json",
    "runtime/data_epoch.json", "runtime/crash_dump.json", "runtime/live_copy_a.json", "live_copy_tile_eligibility.json",
    "runtime/data_epoch_boundary/ce-20261004-v31-clean.pre_epoch_archive.json",
    "x/archive_receipt.json", "research_archive/handoffs/other.jsonl.20261013T021532Z",
    "runtime/data_epoch_boundary/sub/ce-20261004-v31-clean.v3.json",
    "runtime/data_epoch_boundary/ce-20261004-v31-clean.v3.json.bak",
    "bitfinex_live_state.json", "runtime/bitfinex_live_switch_state.json", "config-7002.json", "config.json",
    "policy.json", "spread-gate.json", "keep_manifest.json",
]
GO = [
    "runtime/data_epoch_boundary/ce-20261004-v31-clean.v3.json",
    "runtime/data_epoch_boundary/ce-20261004-v31-final-d.research_events_v22.json",
    "archive_receipt.json",
    "research_archive/handoffs/fill_evidence_handoffs.jsonl.20261016T012824Z",
    "research_archive/handoffs/cancellation_evidence_handoffs.jsonl.20261013T021532Z",
]


def _mk(root, rels):
    for r in rels:
        p = root / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")


def test_only_allowlisted_paths_are_removable():
    assert all(ro.removable(x) for x in GO)
    assert not any(ro.removable(x) for x in KEEP)


def test_plan_and_execute_need_the_reviewed_hash_and_never_touch_protected(tmp_path):
    _mk(tmp_path, GO + KEEP)
    paths = ro.plan(str(tmp_path))
    assert sorted(paths) == sorted(GO)
    bad = ro.execute(str(tmp_path), "0" * 64)
    assert bad["ok"] is False and all((tmp_path / g).exists() for g in GO)
    done = ro.execute(str(tmp_path), ro.plan_sha256(paths))
    assert done["ok"] and sorted(done["deleted"]) == sorted(GO)
    assert not any((tmp_path / g).exists() for g in GO)
    assert all((tmp_path / k).exists() for k in KEEP)


def test_symlinks_are_skipped(tmp_path):
    (tmp_path / "real.json").write_text("{}")
    os.symlink(tmp_path / "real.json", tmp_path / "archive_receipt.json")
    assert ro.plan(str(tmp_path)) == []


def test_twelve_handoffs_are_planned_exactly(tmp_path):
    handoffs = [f"research_archive/handoffs/{k}_evidence_handoffs.jsonl.2026101{d}T0{n}1500Z"
                for k in ("cancellation", "fill") for d in (3, 4) for n in (1, 2, 3)]
    assert len(handoffs) == 12
    _mk(tmp_path, handoffs + KEEP)
    planned = [p for p in ro.plan(str(tmp_path)) if p.startswith("research_archive/handoffs/")]
    assert sorted(planned) == sorted(handoffs)


def test_execute_skips_a_path_swapped_for_a_symlink_after_planning(tmp_path, monkeypatch):
    (tmp_path / "real.json").write_text("{}")
    (tmp_path / "archive_receipt.json").write_text("{}")
    paths = ro.plan(str(tmp_path))
    sha = ro.plan_sha256(paths)
    real_plan = ro.plan
    def swap(root):
        out = real_plan(root)
        (tmp_path / "archive_receipt.json").unlink()
        os.symlink(tmp_path / "real.json", tmp_path / "archive_receipt.json")
        return out
    monkeypatch.setattr(ro, "plan", swap)
    res = ro.execute(str(tmp_path), sha)
    assert res["deleted"] == [] and (tmp_path / "real.json").exists()


G16 = "runtime/data_epoch_boundary/ce-20261004-v31-"
G16_GO = [
    G16 + "final-e.v3.json", G16 + "final-e.research_events_v22.json", G16 + "final.v3.json",
    G16 + "final.research_events_v22.json", G16 + "freeze21.v3.json", G16 + "freeze21.research_events_v22.json",
    G16 + "freeze21.epoch_aliases.json", G16 + "freeze21.pre_epoch_archive.json",
    "runtime/data_epoch_boundary/orphan_tmp_sweep.20261004T054612Z.json",
    "runtime/data_epoch_boundary/orphan_tmp_sweep.20261004T065855Z.json",
    "runtime/data_epoch_boundary/orphan_tmp_sweep.20261004T111516Z.json",
    "runtime/disloc_trades.jsonl.validation.json", "runtime/disloc_foi_trades.jsonl.validation.json",
    "runtime/lane_pnl_ledger_epoch_receipts.jsonl", "runtime/pre_entry_evidence_handoffs.jsonl.1",
    "runtime/relay_outbox_quarantine.jsonl", "runtime/relay_outbox_retired.jsonl",
    "runtime/recovery_receipts/pre-restart-34276394781/crash_dump.json",
    "runtime/recovery_receipts/pre-restart-34276394781/paper_lifecycle_v1.json",
]
G16_KEEP = [
    G16 + "freeze21b.pre_epoch_archive.json", G16 + "freeze21b.epoch_aliases.json", G16 + "freeze21b.v3.json",
    G16 + "freeze21.v3.json.bak", G16 + "freeze21c.v3.json",
    "runtime/crash_dump.json", "crash_dump.json",  # top-level crash dump stays PROTECTED
    "runtime/recovery_receipts/pre-restart-34276394781/config-7002.json",
    "runtime/recovery_receipts/pre-restart-OTHER/crash_dump.json",
    "runtime/recovery_receipts/pre-restart-OTHER/paper_lifecycle_v1.json",
    "runtime/recovery_receipts/pre-restart-34276394781/sub/crash_dump.json",
    "x/runtime/recovery_receipts/pre-restart-34276394781/crash_dump.json",
    "runtime/decision_feature_snapshots.jsonl.validation.json", "runtime/trade_outcome.jsonl.validation.json",
    "runtime/relay_outbox.jsonl", "runtime/lane_pnl_ledger.jsonl", "runtime/pre_entry_evidence_handoffs.jsonl",
    "runtime/pre_entry_evidence_handoffs.jsonl.2", "runtime/data_epoch_boundary/orphan_tmp_sweep.json",
    "runtime/data_epoch_boundary/sub/orphan_tmp_sweep.20261004T054612Z.json",
    "runtime/data_epoch_boundary/ce-20261004-v31-clean.pre_epoch_archive.json",
]


def test_g16_named_list_is_removable_and_everything_else_is_kept():
    assert all(ro.removable(x) for x in G16_GO), [x for x in G16_GO if not ro.removable(x)]
    assert not any(ro.removable(x) for x in G16_KEEP), [x for x in G16_KEEP if ro.removable(x)]


def test_top_level_crash_dump_stays_protected_and_only_one_exact_exception_exists():
    assert ro.PROTECTED_EXCEPTIONS == {"runtime/recovery_receipts/pre-restart-34276394781/crash_dump.json"}
    assert not ro.removable("runtime/crash_dump.json") and not ro.removable("crash_dump.json")
    assert not ro.removable("runtime/recovery_receipts/pre-restart-OTHER/crash_dump.json")
    assert ro.removable("runtime/recovery_receipts/pre-restart-34276394781/crash_dump.json")


def test_g16_plan_on_a_tree_deletes_exactly_the_list_and_keeps_top_level_crash_dump(tmp_path):
    _mk(tmp_path, G16_GO + G16_KEEP)
    paths = ro.plan(str(tmp_path))
    assert sorted(paths) == sorted(G16_GO)
    res = ro.execute(str(tmp_path), ro.plan_sha256(paths))
    assert res["ok"] and sorted(res["deleted"]) == sorted(G16_GO)
    assert (tmp_path / "runtime" / "crash_dump.json").exists() and (tmp_path / "crash_dump.json").exists()
    assert (tmp_path / (G16 + "freeze21b.pre_epoch_archive.json")).exists()
