"""Self-aware content contract for the genome grid: unique AI episodes, no mixed classes, REALISTIC_V1 axes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import contracts as c  # noqa: E402


def _clean():
    axis = {"headline_fill_world": "REALISTIC_V1", "values": [
        {"value": "0.1", "best_policy_id": "FADE|A", "best_fill_world": "REALISTIC_V1"},
        {"value": "0.2", "best_policy_id": "FADE|B", "best_fill_world": "REALISTIC_V1"}]}
    return {"status": "OK", "headline_fill_world": "REALISTIC_V1",
            "coverage": {"evaluated_by_class": {"AI_COMMITTED": 491, "AI_NO_TRADE_SCORE_LED": 585}},
            "episode_integrity": {"status": "PASS", "episodes": 1076, "unique_decision_ids": 1076,
                                  "duplicate_decision_ids": 0},
            "dimension_summary": {"entry_offset_pct": axis},
            "top_100_by_world": {"REALISTIC_V1": [{"by_episode_class": {"AI_COMMITTED": {}}, "cluster_1h": {"all": {}}}]},
            "walk_forward_by_utc_day": {"AI_DECISION": {}}}


def _kinds(obj):
    viol, _ = c._rec_genome_grid_content(obj, {})
    return {v["kind"]: v["severity"] for v in viol}


def test_clean_report_passes_and_is_registered():
    assert _kinds(_clean()) == {}
    reg = c.load_registry()
    spec = next(s for s in reg["contracts"] if s["id"] == "analyzer.genome_grid_content")
    assert spec["reconcile"] == "genome_grid_content" and "genome_grid_content" in c.RECONCILERS


def test_contaminated_report_is_red():
    legacy = _clean()
    legacy.pop("episode_integrity")
    legacy["coverage"] = {"evaluated_by_class": {"AI_COMMITTED": 10, "XVENUE_PREMIUM": 3}}
    same = {"best_policy_id": "FADE|A", "best_fill_world": None}
    legacy["dimension_summary"] = {"fill_world": {"values": [dict(same, value="OPTIMISTIC_TOUCH_SHADOW"),
                                                             dict(same, value="REALISTIC_V1")]}}
    kinds = _kinds(legacy)
    assert kinds["GENOME_EPISODES_UNDECLARED"] == c.RED
    assert kinds["GENOME_MIXED_EPISODE_CLASSES"] == c.RED
    assert kinds["GENOME_AXES_OPTIMISTIC"] == c.RED
    assert kinds["GENOME_AXES_IDENTICAL"] == c.RED


def test_duplicates_and_missing_inference():
    dup = _clean()
    dup["episode_integrity"] = {"status": "FAIL", "duplicate_decision_ids": 264, "violations": ["DUPLICATE_DECISION_ID x264"]}
    dup["top_100_by_world"]["REALISTIC_V1"] = [{}]
    kinds = _kinds(dup)
    assert kinds["GENOME_DUPLICATE_EPISODES"] == c.RED and kinds["GENOME_INFERENCE_MISSING"] == c.AMBER
    assert _kinds({"status": "UNAVAILABLE"}) == {"RECONCILE_SKIPPED": c.INFO}
