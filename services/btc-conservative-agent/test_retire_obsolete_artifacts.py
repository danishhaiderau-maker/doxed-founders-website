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
