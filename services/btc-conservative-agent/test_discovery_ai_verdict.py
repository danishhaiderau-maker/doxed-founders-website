import pytest

from research.discovery_scorecard_publication import _base_row, _ai_verdict_projection, build_discovery_scorecard_publication
from test_discovery_scorecard_publication import GENERATION, evaluator_row, inputs


@pytest.mark.parametrize("raw,category", [("APPROVE", "APPROVE"), ("SOFT_APPROVE", "APPROVE"),
    ("STRONG_APPROVE", "APPROVE"), ("REJECT", "REJECT"), ("SOFT_REJECT", "REJECT"),
    ("AI_ERROR", "ERROR"), (None, "UNKNOWN")])
def test_raw_evaluator_alias_is_preserved_without_family_or_error_inference(raw, category):
    row = _base_row(evaluator_row(ai_decision=raw, ai_direction="SHORT"))
    assert row["raw_ai_decision"] == (raw or "UNKNOWN")
    assert row["raw_ai_direction"] == "SHORT"
    assert row["ai_verdict_class"] == category
    assert row["family_policy_decision"] == "UNKNOWN"
    assert row["ai_error_status"] == ("ERROR" if category == "ERROR" else "UNKNOWN")


def test_raw_approval_and_family_rejection_remain_distinct():
    row = _ai_verdict_projection({"ai_decision": "APPROVE", "policy_decision": "REJECT",
        "execution_disposition": "BLOCKED_BY_POLICY", "ai_error": False})
    assert row["raw_ai_decision"] == "APPROVE"
    assert row["family_policy_decision"] == "REJECT"
    assert row["execution_disposition"] == "BLOCKED_BY_POLICY"
    assert row["ai_error_status"] == "EXPLICIT_NO_ERROR"


def test_orders_and_fills_cannot_manufacture_an_ai_approval():
    row = _base_row(evaluator_row(classification="FULL_FILL", profitability_supported=True,
        terminal_outcome_status="REALIZED_COST_COMPLETE", net_pnl_usd=50, order_intent_expected=True))
    assert row["raw_ai_decision"] == row["ai_verdict_class"] == "UNKNOWN"


def test_conflicting_aliases_remain_unknown():
    row = _ai_verdict_projection({"raw_ai_decision": "APPROVE", "ai_decision": "REJECT",
                                 "raw_ai_direction": "LONG", "ai_direction": "SHORT"})
    assert row["raw_ai_decision"] == row["raw_ai_direction"] == "UNKNOWN"
    assert "RAW_AI_DECISION_ALIAS_CONFLICT" in row["ai_verdict_blockers"]
    assert "RAW_AI_DIRECTION_ALIAS_CONFLICT" in row["ai_verdict_blockers"]


def test_error_is_not_a_model_rejection():
    row = _ai_verdict_projection({"raw_ai_decision": "AI_ERROR", "policy_decision": "ERROR",
                                 "ai_error": True, "error_type": "NETWORK_TIMEOUT"})
    assert row["ai_verdict_class"] == "ERROR"
    assert row["family_policy_decision"] == "ERROR"
    assert row["ai_error_type"] == "NETWORK_TIMEOUT"


def test_verdict_plus_contradictory_error_fails_closed():
    row = _ai_verdict_projection({"raw_ai_decision": "REJECT", "ai_error": True})
    assert row["raw_ai_decision"] == "REJECT"
    assert row["ai_verdict_class"] == "UNKNOWN"
    assert row["ai_error_status"] == "ERROR"
    assert row["ai_verdict_blockers"] == ["AI_VERDICT_ERROR_CONFLICT"]


def test_verified_evaluator_rows_reach_publication_and_adapter_before_filtering(tmp_path, monkeypatch):
    from research import dynamic_cohort_adapter
    rows = [evaluator_row(episode_id=f"e{i}", opportunity_id=f"o{i}", ai_decision=decision,
                         ai_direction="LONG", policy_decision="REJECT" if i == 0 else "UNKNOWN")
            for i, decision in enumerate(("APPROVE", "REJECT", "AI_ERROR", None))]
    root, status, baseline = inputs(tmp_path, rows)
    consumed = []
    original = dynamic_cohort_adapter.adapt_dynamic_cohorts
    def inspect_then_adapt(values, **kwargs):
        values = list(values)
        consumed.extend(values)
        return original(values, **kwargs)
    monkeypatch.setattr(dynamic_cohort_adapter, "adapt_dynamic_cohorts", inspect_then_adapt)
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=status, baseline_report=baseline)
    assert [row["raw_ai_decision"] for row in consumed] == ["APPROVE", "REJECT", "AI_ERROR", "UNKNOWN"]
    assert consumed[0]["family_policy_decision"] == "REJECT"
    coverage = report["ai_verdict_coverage"]
    assert coverage["raw_verdict_row_counts"] == {"APPROVE": 1, "REJECT": 1, "ERROR": 1, "UNKNOWN": 1}
    assert coverage["comparison_status"] == "NOT_EVALUATED"
    assert coverage["profitability_supported"] is False


def test_wrong_generation_verdict_is_not_adopted(tmp_path):
    root, status, baseline = inputs(tmp_path, [evaluator_row(ai_decision="APPROVE", source_revision="foreign")])
    report = build_discovery_scorecard_publication(root, expected_generation=GENERATION,
        evaluator_status=status, baseline_report=baseline)
    assert report["ai_verdict_coverage"]["raw_verdict_row_counts"]["APPROVE"] == 0
    assert report["unjoinable_counts"]["evaluator_row:source_revision_mismatch"] == 1
