"""Shadow exit tables contain only current-stack terminals; archives are opaque counts."""
import pandas as pd

import analyzer_research_engine_v62 as analyzer

CURRENT = analyzer.EXPECTED_BOT_VERSION
LEGACY = "v31-five-family-score-led-non-tie-paper-v2"


def _row(trade_id, version, **extra):
    row = {
        "trade_id": trade_id, "research_lane": "AI_SCAN", "exit_reason": "PROFIT_LOCK_LADDER",
        "net_pnl_usd": 0.01, "max_drawdown_margin_pct": -6.0,
        "exit_config": {"policy_version": version, "hard_stop_margin_pct": -30.0},
    }
    row.update(extra)
    return row


def test_prior_stack_and_unstamped_shadow_rows_are_archived_not_analyzed():
    rows = [
        _row("scan-1", LEGACY), _row("scan-2", LEGACY),
        _row("scan-3", CURRENT),
        {"trade_id": "scan-4", "exit_reason": "STOP_LOSS"},
    ]
    kept, archive = analyzer._scope_shadow_exit_rows(rows, CURRENT)
    assert [row["trade_id"] for row in kept] == ["scan-3"]
    assert archive["current_stack_version"] == CURRENT
    assert archive["archived_rows_excluded"] == 3
    assert archive["archived_rows_by_version"] == {LEGACY: 2, "UNSTAMPED": 1}
    assert archive["archive_semantics"] == "OPAQUE_COUNT_ONLY_NOT_ANALYZED"


def test_analyzer_sync_id_stamp_wins_over_policy_version():
    row = _row("scan-5", "ATR_TRAIL_POLICY_ID")
    row["exit_config"]["analyzer_sync_id"] = CURRENT
    kept, archive = analyzer._scope_shadow_exit_rows([row], CURRENT)
    assert len(kept) == 1 and archive["archived_rows_excluded"] == 0


def test_stop_matrix_reads_hard_stop_and_mae_from_the_frozen_snapshot():
    frame = pd.DataFrame([_row("scan-3", CURRENT, shared_ai_call_id="call-1")])
    stops = analyzer._exit_family_and_stop_summaries(frame, "SHADOW_LAB_DESCRIPTIVE")["stop_effectiveness_matrix"]
    assert len(stops) == 1
    assert stops[0]["stop_type"] == "PHYSICAL_HARD_STOP"
    assert stops[0]["hard_stop_margin_pct"] == 30.0
    assert stops[0]["avg_mae_margin_pct"] == -6.0
    assert stops[0]["missing_mae_rows"] == 0
    assert stops[0]["missing_identity_rows"] == 0
