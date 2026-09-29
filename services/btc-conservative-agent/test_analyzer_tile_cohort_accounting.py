"""Current analyzer cohort = registry tiles of the current epoch; one PnL function feeds every total."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

AGENT = Path(__file__).resolve().parent
CURRENT = "epoch-v22-current"


@pytest.fixture(scope="module")
def engine():
    spec = importlib.util.spec_from_file_location("cohort_engine", AGENT / "analyzer_research_engine_v62.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trades(engine):
    fc3, fat = engine.CURRENT_RESEARCH_LANES[0], engine.CURRENT_RESEARCH_LANES[1]
    return pd.DataFrame([
        {"trade_id": "fc3-1", "research_lane": fc3, "epoch_id": CURRENT, "net_pnl_usd": 0.20},
        {"trade_id": "fc3-2", "research_lane": fc3, "epoch_id": CURRENT, "net_pnl_usd": -0.05},
        {"trade_id": "fat-1", "research_lane": fat, "epoch_id": CURRENT, "net_pnl_usd": 0.22},
        {"trade_id": "fat-1", "research_lane": fat, "epoch_id": CURRENT, "net_pnl_usd": 0.22},
        {"trade_id": "fat-0", "research_lane": fat, "epoch_id": None, "net_pnl_usd": 0.0},
        # Legacy Continuous paper orders from before Continuous became analysis-only.
        {"trade_id": "cont-a", "research_lane": "CONTINUOUS", "epoch_id": "epoch-v22-old", "net_pnl_usd": -0.03},
        {"trade_id": "cont-b", "research_lane": "CONTINUOUS", "epoch_id": CURRENT, "net_pnl_usd": -0.05},
        {"trade_id": "old-1", "research_lane": fc3, "epoch_id": "epoch-v22-old", "net_pnl_usd": 1.00},
        {"trade_id": "x-1", "research_lane": None, "epoch_id": CURRENT, "net_pnl_usd": 0.5},
    ])


def test_non_registry_and_prior_epoch_rows_leave_the_current_cohort(engine):
    kept, quarantine = engine.split_current_tile_cohort(_trades(engine), {"collector_v22_epoch_id": CURRENT})
    assert sorted(set(kept["trade_id"])) == ["fat-0", "fat-1", "fc3-1", "fc3-2"]
    assert quarantine["rows"] == 4
    assert quarantine["by_reason"] == {"NON_REGISTRY_LANE": 3, "PRIOR_EPOCH": 1}
    assert quarantine["by_lane"]["CONTINUOUS"] == 2
    assert {r["trade_id"] for r in quarantine["rows_detail"]} == {"cont-a", "cont-b", "old-1", "x-1"}


def test_without_a_session_epoch_only_the_lane_filter_applies(engine):
    kept, quarantine = engine.split_current_tile_cohort(_trades(engine), {})
    assert "old-1" in set(kept["trade_id"])
    assert quarantine["by_reason"] == {"NON_REGISTRY_LANE": 3}


def test_lane_sum_equals_total_from_one_function(engine):
    kept, _ = engine.split_current_tile_cohort(_trades(engine), {"collector_v22_epoch_id": CURRENT})
    pnl = engine.tile_cohort_pnl(kept)
    assert pnl["n"] == 4
    assert pnl["net_pnl_usd"] == pytest.approx(0.37)
    assert sum(v["net_pnl_usd"] for v in pnl["by_lane"].values()) == pytest.approx(pnl["net_pnl_usd"])
    assert pnl["wins"] == 2


def test_tile_pnl_ignores_non_registry_rows_even_if_unfiltered(engine):
    pnl = engine.tile_cohort_pnl(_trades(engine))
    assert "CONTINUOUS" not in pnl["by_lane"]
    assert pnl["net_pnl_usd"] == pytest.approx(0.20 - 0.05 + 0.22 + 0.0 + 1.00)


def test_executive_summary_and_funnel_share_the_cohort_total(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kept, quarantine = engine.split_current_tile_cohort(_trades(engine), {"collector_v22_epoch_id": CURRENT})
    engine.write_trade_cohort_quarantine(quarantine)
    receipt = json.loads((tmp_path / engine.TRADE_COHORT_QUARANTINE_FILE).read_text(encoding="utf-8"))
    assert receipt["rows"] == 4 and receipt["schema"] == "analyzer_trade_cohort_quarantine_v1"
    scope = engine._session_trade_scope(kept, engine.CURRENT_RESEARCH_LANES)
    assert scope["non_tile_trade_rows"] == {}
    assert scope["quarantined_trade_rows"]["by_lane"]["CONTINUOUS"] == 2
    payload = engine.build_executive_summary_payload(
        session={"collector_v22_epoch_id": CURRENT}, trades=kept,
        analysis_df=kept.drop_duplicates(subset=["trade_id"]), data_scope="session",
    )
    assert payload["performance"]["trades"] == 4
    assert payload["performance"]["net_pnl_usd"] == round(engine.tile_cohort_pnl(kept)["net_pnl_usd"], 2)
