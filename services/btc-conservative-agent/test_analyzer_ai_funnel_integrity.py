import pandas as pd

import analyzer_research_engine_v62 as analyzer


def _funnel_check(rows):
    report = analyzer.run_integrity_checks(decisions=pd.DataFrame(rows))
    return next(row for row in report["checks"] if row["check"] == "ai_decision_funnel")


def test_ai_funnel_counts_rows_that_carry_ai_outcome_evidence():
    check = _funnel_check([
        {"decision": "AI", "ai_decision_text": "APPROVE", "reason": "APPROVE"},
        {"decision": "COMPLETE", "ai_decision_text": "APPROVE", "reason": "COMPLETE"},
        {"decision": "PIPELINE_WARNING", "ai_decision_text": "APPROVE", "reason": "PIPELINE_WARNING"},
        {"decision": "BLOCKED", "ai_decision_text": "REJECT", "reason": "AI_REJECT"},
    ])

    assert check["passed"] is True
    assert "=3+1+0+0=4" in check["expected"]
    assert "ai_involved_rows=4" in check["found"]


def test_ai_funnel_excludes_pre_ai_block_but_keeps_post_ai_outcome():
    check = _funnel_check([
        {"decision": "BLOCKED", "ai_decision_text": None, "reason": "CTX_FAIL"},
        {"decision": "BLOCKED", "ai_decision_text": "APPROVE", "reason": "MAX_ACTIVE_SIGNALS"},
        {"decision": "AI", "ai_decision_text": "APPROVE", "reason": "APPROVE"},
    ])

    assert check["passed"] is True
    assert "=2+0+0+0=2" in check["expected"]
    assert "ai_involved_rows=2" in check["found"]


def test_ai_funnel_fails_closed_for_unclassified_primary_row():
    check = _funnel_check([
        {"decision": "AI", "ai_decision_text": "", "reason": "UNKNOWN"},
    ])

    assert check["passed"] is False
    assert "unclassified=1" in check["found"]
