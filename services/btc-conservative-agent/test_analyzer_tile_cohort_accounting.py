"""Current analyzer cohort = registry tiles of the current epoch; one PnL function feeds every total."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

AGENT = Path(__file__).resolve().parent
CURRENT = "epoch-v22-current"
RETIRED = "FAMILY_ATR_TARGET_2_5"


@pytest.fixture(scope="module")
def engine():
    spec = importlib.util.spec_from_file_location("cohort_engine", AGENT / "analyzer_research_engine_v62.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trades(engine):
    lane = engine.CURRENT_RESEARCH_LANES[0]
    return pd.DataFrame([
        {"trade_id": "t-1", "research_lane": lane, "epoch_id": CURRENT, "net_pnl_usd": 0.20},
        {"trade_id": "t-2", "research_lane": lane, "epoch_id": CURRENT, "net_pnl_usd": -0.05},
        {"trade_id": "t-3", "research_lane": lane, "epoch_id": CURRENT, "net_pnl_usd": 0.22},
        {"trade_id": "t-3", "research_lane": lane, "epoch_id": CURRENT, "net_pnl_usd": 0.22},
        {"trade_id": "t-0", "research_lane": lane, "epoch_id": None, "net_pnl_usd": 0.0},
        # Legacy Continuous paper orders from before Continuous became analysis-only.
        {"trade_id": "cont-a", "research_lane": "CONTINUOUS", "epoch_id": "epoch-v22-old", "net_pnl_usd": -0.03},
        {"trade_id": "cont-b", "research_lane": "CONTINUOUS", "epoch_id": CURRENT, "net_pnl_usd": -0.05},
        # Retired-tile history is archive evidence, never the current cohort.
        {"trade_id": "fat-9", "research_lane": RETIRED, "epoch_id": CURRENT, "net_pnl_usd": 0.40},
        {"trade_id": "old-1", "research_lane": lane, "epoch_id": "epoch-v22-old", "net_pnl_usd": 1.00},
        {"trade_id": "x-1", "research_lane": None, "epoch_id": CURRENT, "net_pnl_usd": 0.5},
    ])


def test_non_registry_and_prior_epoch_rows_leave_the_current_cohort(engine):
    assert RETIRED not in engine.CURRENT_RESEARCH_LANES
    kept, quarantine = engine.split_current_tile_cohort(_trades(engine), {"collector_v22_epoch_id": CURRENT})
    assert sorted(set(kept["trade_id"])) == ["t-0", "t-1", "t-2", "t-3"]
    assert quarantine["rows"] == 5
    assert quarantine["by_reason"] == {"NON_REGISTRY_LANE": 4, "PRIOR_EPOCH": 1}
    assert quarantine["by_lane"]["CONTINUOUS"] == 2
    assert quarantine["by_lane"][RETIRED] == 1
    assert {r["trade_id"] for r in quarantine["rows_detail"]} == {"cont-a", "cont-b", "fat-9", "old-1", "x-1"}


def test_without_a_session_epoch_only_the_lane_filter_applies(engine):
    kept, quarantine = engine.split_current_tile_cohort(_trades(engine), {})
    assert "old-1" in set(kept["trade_id"])
    assert quarantine["by_reason"] == {"NON_REGISTRY_LANE": 4}


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
    assert RETIRED not in pnl["by_lane"]
    assert pnl["net_pnl_usd"] == pytest.approx(0.20 - 0.05 + 0.22 + 0.0 + 1.00)


def test_executive_summary_and_funnel_share_the_cohort_total(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kept, quarantine = engine.split_current_tile_cohort(_trades(engine), {"collector_v22_epoch_id": CURRENT})
    engine.write_trade_cohort_quarantine(quarantine)
    receipt = json.loads((tmp_path / engine.TRADE_COHORT_QUARANTINE_FILE).read_text(encoding="utf-8"))
    assert receipt["rows"] == 5 and receipt["schema"] == "analyzer_trade_cohort_quarantine_v1"
    scope = engine._session_trade_scope(kept, engine.CURRENT_RESEARCH_LANES)
    assert scope["non_tile_trade_rows"] == {}
    assert scope["quarantined_trade_rows"]["by_lane"]["CONTINUOUS"] == 2
    payload = engine.build_executive_summary_payload(
        session={"collector_v22_epoch_id": CURRENT}, trades=kept,
        analysis_df=kept.drop_duplicates(subset=["trade_id"]), data_scope="session",
    )
    assert payload["performance"]["trades"] == 4
    assert payload["performance"]["net_pnl_usd"] == round(engine.tile_cohort_pnl(kept)["net_pnl_usd"], 2)


def _outcomes(engine):
    prefix = engine.ACTIVE_TILE_REGISTRY[engine.CURRENT_RESEARCH_LANES[0]]["id_prefix"]
    return [
        {"trade_id": f"{prefix}-p1", "exit_reason": "PHANTOM_CANCEL_BY_RELAY", "ts": "2026-09-29T10:00:00Z"},
        {"trade_id": f"{prefix}-p2", "exit_reason": "PHANTOM_CANCEL_BY_RELAY", "ts": "2026-09-29T11:00:00Z"},
        {"trade_id": f"{prefix}-p3", "outcome_exit_reason": "PHANTOM_CANCEL_BY_RELAY", "ts": "2026-09-29T12:00:00Z"},
        {"trade_id": f"{prefix}-ok", "exit_reason": "TRAIL_STOP", "ts": "2026-09-29T12:30:00Z"},
        {"trade_id": "fat-p4", "exit_reason": "PHANTOM_CANCEL_BY_RELAY", "ts": "2026-09-29T12:40:00Z"},
        {"trade_id": "", "exit_reason": "PHANTOM_CANCEL_BY_RELAY"},
    ]


def test_phantom_cancel_on_paper_only_tiles_is_relay_interference(engine):
    found = engine.relay_interference_trade_ids(_outcomes(engine))
    lane = engine.CURRENT_RESEARCH_LANES[0]
    # Retired-tile prefixes no longer resolve to a registry lane.
    assert {v["research_lane"] for v in found.values()} == {lane, "UNLABELLED"}
    assert len(found) == 4
    assert all(v["reason"] == "RELAY_INTERFERENCE_PHANTOM_CANCEL" for v in found.values())


def test_phantom_cancel_on_relay_eligible_tile_is_not_contamination(engine, monkeypatch):
    lane = engine.CURRENT_RESEARCH_LANES[0]
    registry = {k: dict(v) for k, v in engine.ACTIVE_TILE_REGISTRY.items()}
    registry[lane].update({"paper_only": False, "platform_relay_eligible": True})
    monkeypatch.setattr(engine, "ACTIVE_TILE_REGISTRY", registry)
    found = engine.relay_interference_trade_ids(_outcomes(engine))
    assert set(found) == {"fat-p4"}


def test_relay_interference_is_quarantined_receipted_and_flagged(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    found = engine.relay_interference_trade_ids(_outcomes(engine))
    trades = _trades(engine)
    lane = engine.CURRENT_RESEARCH_LANES[0]
    contaminated_id = next(t for t, v in found.items() if v["research_lane"] == lane)
    trades = pd.concat([trades, pd.DataFrame([
        {"trade_id": contaminated_id, "research_lane": lane, "epoch_id": CURRENT, "net_pnl_usd": 0.0},
    ])], ignore_index=True)
    kept, quarantine = engine.split_current_tile_cohort(
        trades, {"collector_v22_epoch_id": CURRENT}, relay_interference=found,
    )
    assert contaminated_id not in set(kept["trade_id"])
    assert quarantine["by_reason"]["RELAY_INTERFERENCE_PHANTOM_CANCEL"] == 1
    relay = quarantine["relay_interference"]
    assert relay["rows"] == 4 and relay["in_trade_cohort"] == 1
    assert relay["by_lane"][lane] == 3
    engine.write_trade_cohort_quarantine(quarantine)
    receipt = json.loads((tmp_path / engine.TRADE_COHORT_QUARANTINE_FILE).read_text(encoding="utf-8"))
    assert receipt["relay_interference"]["rows"] == 4
    assert len(receipt["relay_interference"]["rows_detail"]) == 4
    scope = engine._session_trade_scope(kept, engine.CURRENT_RESEARCH_LANES)
    assert "rows_detail" not in scope["quarantined_trade_rows"]["relay_interference"]


def test_outcome_loader_excludes_relay_interference_without_editing_ledger(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / engine.TRADE_OUTCOME_FILE
    body = "".join(json.dumps(row) + "\n" for row in _outcomes(engine))
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(engine, "_agent_data_path", lambda p: str(tmp_path / p))
    loaded = engine._load_trade_outcomes_v2()
    contaminated = set(engine.relay_interference_trade_ids(_outcomes(engine)))
    assert contaminated and not contaminated & set(loaded["trade_id"])
    assert any(tid.endswith("-ok") for tid in loaded["trade_id"])
    assert path.read_text(encoding="utf-8") == body
