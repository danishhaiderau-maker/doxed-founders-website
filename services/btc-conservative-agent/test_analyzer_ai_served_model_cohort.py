"""Served-model cohort annotation: split by the model that answered, never a new epoch."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

AGENT = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def engine():
    spec = importlib.util.spec_from_file_location("served_model_engine", AGENT / "analyzer_research_engine_v62.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_served_model_wins_and_timestamps_fill_the_rest(engine):
    df = pd.DataFrame([
        {"ts": "2026-10-01T18:56:00.128048+00:00"},                                   # last pre-switch
        {"ts": "2026-10-01T20:00:00+00:00"},                                          # inside window
        {"ts": "2026-10-01T21:30:47.978007+00:00"},                                   # first post-switch
        {"ts": "2026-10-02T03:00:00+00:00", "deepseek_served_model": "deepseek-flash-2"},
    ])
    served = list(engine.ai_served_model_cohort(df))
    assert served == ["deepseek-v4-flash", engine.SERVED_MODEL_SWITCHOVER_UNKNOWN, "deepseek-flash",
                      "deepseek-flash-2"]
    epochs = pd.DataFrame([{"entry_ts": 1_790_870_000.0}, {"entry_ts": 1_790_900_000.0}])
    assert list(engine.ai_served_model_cohort(epochs)) == ["deepseek-v4-flash", "deepseek-flash"]


def test_split_receipt_by_lane_and_model_keeps_one_cohort(engine):
    lane = engine.CURRENT_RESEARCH_LANES[0]
    trades = pd.DataFrame([
        {"trade_id": "a", "research_lane": lane, "entry_ts": "2026-10-01T17:00:00Z", "net_pnl_usd": 0.10},
        {"trade_id": "b", "research_lane": lane, "entry_ts": "2026-10-01T18:00:00Z", "net_pnl_usd": -0.05},
        {"trade_id": "c", "research_lane": lane, "entry_ts": "2026-10-01T22:00:00Z", "net_pnl_usd": 0.20},
    ])
    receipt = engine.ai_served_model_split(trades)
    assert receipt["switchover"]["to_model"] == "deepseek-flash"
    assert receipt["switchover"]["window_start"] == "2026-10-01T18:56:01Z"
    assert receipt["rows_by_model"] == {"deepseek-v4-flash": 2, "deepseek-flash": 1}
    by_model = receipt["by_lane"][lane]
    assert by_model["deepseek-v4-flash"]["trades"] == 2
    assert by_model["deepseek-v4-flash"]["net_pnl_usd"] == pytest.approx(0.05)
    assert by_model["deepseek-flash"]["win_rate"] == 1.0
    assert engine.ai_served_model_split(pd.DataFrame())["by_lane"] == {}


def test_served_model_receipt_is_published_with_the_cohort_quarantine(engine):
    source = (AGENT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    run = source[source.index("trades, cohort_quarantine = split_current_tile_cohort("):]
    run = run[: run.index("write_trade_cohort_quarantine(cohort_quarantine)")]
    assert 'cohort_quarantine["ai_served_model"] = ai_served_model_split(trades)' in run
    assert "new epoch" not in source[source.index("AI_SERVED_MODEL_SWITCHOVER = {"):][:400].lower()
