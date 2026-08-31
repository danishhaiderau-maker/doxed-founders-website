from research_entry_baselines import (
    CHASE_WINDOW_BUCKETS,
    ENTRY_BASELINE_REGISTRY,
    build_entry_baseline_registry,
    classify_baseline_evidence,
)
from chase_offset_touch_grid import (
    COMPRESSED_SHADOW_EXPIRY_SEC,
    COMPRESSED_SHADOW_POLICY_ID,
    COMPRESSED_SHADOW_STAGE_SECONDS,
)


def _by_id():
    return {row["baseline_id"]: row for row in ENTRY_BASELINE_REGISTRY["baselines"]}


def test_required_baselines_are_typed_signed_and_research_only():
    rows = _by_id()
    assert set(rows) == {
        "MARKET_ENTRY_AT_SIGNAL", "NO_CHASE_LIMIT", "CHASE_13_MIN_COMPRESSED",
        "CHASE_30_MIN_LEGACY", "FINAL_MARKET_AFTER_EXPIRY",
    } | {f"CHASE_WINDOW_{bucket}" for bucket in CHASE_WINDOW_BUCKETS}
    assert rows["MARKET_ENTRY_AT_SIGNAL"]["entry_type"] == "MARKET_ENTRY"
    assert rows["FINAL_MARKET_AFTER_EXPIRY"]["entry_type"] == "FINAL_MARKET_AFTER_EXPIRY"
    assert rows["NO_CHASE_LIMIT"]["chase_policy_id"] == "no_chase"
    assert rows["CHASE_13_MIN_COMPRESSED"]["terminal_expiry_sec"] == COMPRESSED_SHADOW_EXPIRY_SEC
    assert rows["CHASE_13_MIN_COMPRESSED"]["stage_seconds"] == COMPRESSED_SHADOW_STAGE_SECONDS
    assert rows["CHASE_13_MIN_COMPRESSED"]["source_policy_id"] == COMPRESSED_SHADOW_POLICY_ID
    assert rows["CHASE_30_MIN_LEGACY"]["terminal_expiry_sec"] == 1800
    assert rows["CHASE_30_MIN_LEGACY"]["source_entry_policy_id"] == (
        "OFFSET_0.10_CHASE_w234_s50_i180"
    )
    for bucket in CHASE_WINDOW_BUCKETS:
        row = rows[f"CHASE_WINDOW_{bucket}"]
        assert row["chase_window_bucket"] == bucket
        assert row["window_start_sec"] == bucket * 300
        assert row["window_end_sec"] == (bucket + 1) * 300
    for row in rows.values():
        assert row["execution_class"] == "RESEARCH_ONLY"
        assert row["places_order"] is False
        assert row["relay_eligible"] is False
        assert row["missing_evidence_outcome"] == "UNKNOWN"
        assert row["policy_signature"].startswith("entry-baseline-")


def test_registry_is_deterministic_and_missing_data_never_becomes_no_fill():
    assert build_entry_baseline_registry() == ENTRY_BASELINE_REGISTRY
    result = classify_baseline_evidence("MARKET_ENTRY_AT_SIGNAL", {})
    assert result["outcome_state"] == "UNKNOWN"
    assert result["supported"] is False
    assert "MISSING_SIGNAL_TIME_BBO" in result["rejection_codes"]


def test_complete_evidence_accepts_only_explicit_conservative_terminal_state():
    baseline = _by_id()["MARKET_ENTRY_AT_SIGNAL"]
    evidence = {name: True for name in baseline["required_evidence"]}
    assert classify_baseline_evidence("MARKET_ENTRY_AT_SIGNAL", evidence)["outcome_state"] == "UNKNOWN"
    evidence["terminal_outcome"] = "PARTIAL_FILL"
    result = classify_baseline_evidence("MARKET_ENTRY_AT_SIGNAL", evidence)
    assert result["supported"] is True
    assert result["outcome_state"] == "PARTIAL_FILL"


def test_manifest_exposes_registry_without_changing_existing_cartesian_grid():
    from policy_search_manifest import POLICY_SEARCH_MANIFEST, compact_search_receipt

    assert POLICY_SEARCH_MANIFEST["entry_baseline_registry"] == ENTRY_BASELINE_REGISTRY
    assert POLICY_SEARCH_MANIFEST["counts"]["entry_policy_cartesian"] == 2700
    receipt = compact_search_receipt()
    assert receipt["entry_baseline_count"] == 11
    assert receipt["chase_window_buckets"] == list(range(6))
    assert receipt["entry_baseline_registry_signature"] == ENTRY_BASELINE_REGISTRY["registry_signature"]
