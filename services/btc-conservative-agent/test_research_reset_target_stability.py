"""Under-barrier two-sample stability pre-flight for reset deletion targets."""
import hashlib

import pytest

import research_reset_target_stability as stability
from research_exact_deletion import ResearchDeletionRejected
from research_reset_failure_detail import reset_failure_fields


def plan_for(files):
    def plan(root, *, proof, allow_fly_runtime_aliases, scope_name):
        assert allow_fly_runtime_aliases is True and proof == {"proof": 1}
        rows = files.get(scope_name, [])
        return {"complete": True, "errors": [], "targets": [
            {"absolute_path": f"/rt/{scope_name or 'runtime'}/{name}", "path": name, "size_bytes": size,
             "mtime_ns": mtime, "inode": inode} for name, size, mtime, inode in rows]}
    return plan


def test_unchanged_targets_are_stable(monkeypatch):
    files = {None: [("indicator_bars_v1.jsonl", 10, 1, 7)], "research": [("a.jsonl", 3, 2, 8)]}
    monkeypatch.setattr(stability, "plan_research_reset", plan_for(files))
    first = stability.sample_targets("/rt", {"proof": 1}, [None, "research"])
    second = stability.sample_targets("/rt", {"proof": 1}, [None, "research"])
    assert stability.assert_targets_stable(first, second) == {"status": "STABLE", "target_count": 2}


@pytest.mark.parametrize("change", ["append", "rewrite_same_size", "rotate", "appear", "vanish"])
def test_any_target_change_aborts_with_bounded_detail(monkeypatch, change):
    before = {None: [("indicator_bars_v1.jsonl", 10, 1, 7)]}
    after = {None: [("indicator_bars_v1.jsonl", 10, 1, 7)]}
    if change == "append": after[None] = [("indicator_bars_v1.jsonl", 16, 2, 7)]
    if change == "rewrite_same_size": after[None] = [("indicator_bars_v1.jsonl", 10, 2, 7)]
    if change == "rotate": after[None] = [("indicator_bars_v1.jsonl", 0, 2, 9)]
    if change == "appear": after[None].append(("indicator_bars_v1.jsonl.1", 10, 1, 9))
    if change == "vanish": after[None] = []
    monkeypatch.setattr(stability, "plan_research_reset", plan_for(before))
    first = stability.sample_targets("/rt", {"proof": 1}, [None])
    monkeypatch.setattr(stability, "plan_research_reset", plan_for(after))
    second = stability.sample_targets("/rt", {"proof": 1}, [None])
    with pytest.raises(ResearchDeletionRejected, match="RESET_TARGETS_CHANGED_UNDER_BARRIERS") as caught:
        stability.assert_targets_stable(first, second)
    assert caught.value.unstable_targets[0]["path"].startswith("indicator_bars_v1.jsonl")
    fields = reset_failure_fields(caught.value)
    assert fields["rejection_code"] == "RESET_TARGETS_CHANGED_UNDER_BARRIERS"
    changed_path = caught.value.unstable_targets[0]["absolute_path"]
    assert fields["target_stability"] == {
        "unstable_target_count": 1,
        "unstable_target_path_sha256": [hashlib.sha256(changed_path.encode()).hexdigest()]}
    assert "/rt/" not in str(fields)


def test_incomplete_inventory_refuses(monkeypatch):
    monkeypatch.setattr(stability, "plan_research_reset",
                        lambda *a, **k: {"complete": False, "errors": ["x"], "targets": []})
    with pytest.raises(ResearchDeletionRejected, match="RESET_INVENTORY_INCOMPLETE"):
        stability.sample_targets("/rt", {"proof": 1}, [None])


def test_reset_samples_before_and_after_preflight_and_before_reset_pointer():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8-sig"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_perform_fresh_collection_reset_quiesced")
    source = ast.get_source_segment(Path(__file__).with_name("bot.py").read_text(encoding="utf-8-sig"), fn)
    first = source.index("stability_sample = sample_targets(")
    preflight = source.index("validate_only=True")
    second = source.index("assert_targets_stable(")
    pointer = source.index('"ACTIVE_RESET.json"')
    assert first < preflight < second < pointer
    retire = source.index("_fresh_research_reset_retire_reviewed_attempt()")
    assert retire < source.index("_fresh_research_reset_resume()")
