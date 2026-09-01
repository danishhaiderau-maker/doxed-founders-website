from research.research_v3_report import build_three_tier_strategy_leaders


def _candidate(policy_id, ideal_pnl, execution_pnl=None, *, unknown=0, qualified=False):
    fills = 1 if execution_pnl is not None else 0
    return {
        "policy_id": policy_id,
        "policy_signature": f"signature-{policy_id}",
        "policy_family": "FIXED_TARGET",
        "oos_episodes": 10,
        "supported_conservative_episodes": fills,
        "full_fills": fills,
        "partial_fills": 0,
        "no_fills": 0,
        "unsupported_episodes": unknown,
        "unknown_reason_codes": ["UNKNOWN_TAPE_EVIDENCE"] if unknown else [],
        "ideal_touch_diagnostic": {
            "evidence_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
            "touches": 4,
            "oos_net_usd": ideal_pnl,
            "max_drawdown_usd": -0.1,
        },
        "validation": {"risk": {
            "net_pnl_usd": execution_pnl,
            "max_drawdown_usd": -0.2,
            "cvar95_usd": -0.05,
        }},
        "gates": {"sealed_holdout_pass": qualified},
        "ranking_blockers": [] if qualified else ["SEALED_HOLDOUT_MISSING"],
    }


def _build(candidates, number_one=None, blockers=None):
    return build_three_tier_strategy_leaders(
        candidates, {"number_one": number_one},
        generated_at="2026-09-02T00:00:00+00:00",
        source_revision="source-revision",
        analyzer_revision="analyzer-revision",
        epoch_id="epoch-1",
        tile_config_signature="config-signature",
        report_blockers=list(blockers or []),
    )


def test_integrity_failure_does_not_blank_descriptive_leader():
    descriptive = _candidate("ideal-leader", 3.0, unknown=2)
    report = _build([descriptive], blockers=["V3_DATA_INTEGRITY_FAILED"])

    tier = report["descriptive_ideal_touch"]
    assert tier["status"] == "AVAILABLE"
    assert tier["leader"]["policy_id"] == "ideal-leader"
    assert "DOES NOT SHOW THAT IT WORKS" in tier["claim_label"]
    assert tier["blockers"] == ["V3_DATA_INTEGRITY_FAILED"]
    assert report["unknown_evidence"] == {
        "episode_count": 2,
        "blocker_counts": {"UNKNOWN_TAPE_EVIDENCE": 1},
    }


def test_execution_and_qualified_leaders_are_separate_claims():
    ideal = _candidate("ideal-only", 10.0)
    execution = _candidate("execution-leader", 2.0, 1.5)
    qualified = _candidate("qualified-leader", 1.0, 1.0, qualified=True)
    report = _build([ideal, execution, qualified], number_one=qualified)

    assert report["descriptive_ideal_touch"]["leader"]["policy_id"] == "ideal-only"
    assert report["execution_supported"]["leader"]["policy_id"] == "execution-leader"
    assert report["fully_qualified"]["leader"]["policy_id"] == "qualified-leader"
    assert report["execution_supported"]["leader"]["execution_net_pnl_usd"] == 1.5


def test_currency_is_explicit_and_missing_upper_tiers_are_not_blank():
    report = _build([_candidate("diagnostic", 0.5)])

    assert report["currency"] == {
        "generated_at": "2026-09-02T00:00:00+00:00",
        "source_revision": "source-revision",
        "analyzer_revision": "analyzer-revision",
        "dataset_epoch_id": "epoch-1",
        "tile_config_signature": "config-signature",
    }
    assert report["execution_supported"]["status"] == "NO_EXECUTION_SUPPORTED_POLICY"
    assert "NO_SUPPORTED_CONSERVATIVE_FILL_POLICY" in report["execution_supported"]["blockers"]
    assert report["fully_qualified"]["status"] == "NO_FULLY_QUALIFIED_POLICY"
    assert report["fully_qualified"]["blockers"] == ["NO_SAFE_QUALIFIED_POLICY"]


def test_dashboard_contains_explicit_three_tier_labels():
    from pathlib import Path

    source = (Path(__file__).resolve().parent / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    assert "Three strategy truth tiers" in source
    assert "Best descriptive / ideal-touch" in source
    assert "Best execution-supported" in source
    assert "Best fully qualified" in source
    assert "UNKNOWN blockers" in source
