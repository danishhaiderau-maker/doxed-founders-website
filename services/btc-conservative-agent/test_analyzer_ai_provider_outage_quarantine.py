"""2026-10-01 DeepSeek outage interval is quarantined from current-cohort stats."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

AGENT = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def engine():
    spec = importlib.util.spec_from_file_location("outage_engine", AGENT / "analyzer_research_engine_v62.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_outage_interval_bounds_keep_last_success_and_normal_recovery(engine):
    ai_log = pd.DataFrame([
        {"ts": "2026-10-01T18:56:00.128048+00:00", "decision": "APPROVE"},   # last success: kept
        {"ts": "2026-10-01T19:34:23.933411+00:00", "decision": "AI_ERROR"},
        {"ts": "2026-10-01T21:24:46.014210+00:00", "decision": "AI_ERROR"},
        {"ts": "2026-10-01T21:30:47.978007+00:00", "decision": "REJECT"},    # 354 s stale verdict
        {"ts": "2026-10-01T21:30:55.349315+00:00", "decision": "REJECT"},    # normal: kept
    ])
    kept, receipt = engine.quarantine_ai_provider_outage_frames({"ai_log": ai_log, "decisions": None})
    assert list(kept["ai_log"]["ts"].str[11:19]) == ["18:56:00", "21:30:55"]
    assert kept["decisions"] is None
    assert receipt["rows"] == 3 and receipt["rows_by_frame"] == {"ai_log": 3}
    assert receipt["intervals"][0]["reason"] == "AI_PROVIDER_TIMEOUT_OUTAGE"


def test_trades_entered_inside_outage_leave_current_cohort_with_reason(engine):
    lane = engine.CURRENT_RESEARCH_LANES[0]
    trades = pd.DataFrame([
        {"trade_id": "a", "research_lane": lane, "entry_ts": "2026-10-01T18:40:00Z",
         "ts": "2026-10-01T19:40:00Z", "net_pnl_usd": 0.1},
        {"trade_id": "b", "research_lane": lane, "entry_ts": "2026-10-01T20:00:00Z",
         "ts": "2026-10-01T21:00:00Z", "net_pnl_usd": -0.2},
        {"trade_id": "c", "research_lane": lane, "entry_ts": "2026-10-01T21:34:06Z",
         "ts": "2026-10-01T22:34:06Z", "net_pnl_usd": 0.3},
    ])
    current, summary = engine.split_current_tile_cohort(trades, session={})
    assert list(current["trade_id"]) == ["a", "c"]
    assert summary["by_reason"] == {"AI_PROVIDER_TIMEOUT_OUTAGE": 1}
    assert summary["rows_detail"][0]["trade_id"] == "b"


def test_outage_quarantine_is_wired_before_the_receipt_is_published(engine):
    source = (AGENT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    run = source[source.index("trades, cohort_quarantine = split_current_tile_cohort("):]
    run = run[: run.index("write_trade_cohort_quarantine(cohort_quarantine)")]
    assert "quarantine_ai_provider_outage_frames(" in run
    assert 'cohort_quarantine["ai_provider_outage"] = ai_outage_receipt' in run
