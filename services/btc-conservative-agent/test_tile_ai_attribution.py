"""Per-tile AI attribution joins tile orders to the shared AI scan by shared_ai_call_id."""

import json

import pandas as pd

import analyzer_research_engine_v62 as analyzer
from research import decision_view as dv


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _store(tmp_path):
    _write_jsonl(tmp_path / analyzer.AI_REASON_RESEARCH_FILE, [
        {"schema": "ai_reason_v2", "research_lane": "AI_SCAN", "trade_id": "scan-a",
         "ai_decision": "APPROVE", "direction": "SHORT"},
        {"schema": "ai_reason_v2", "research_lane": "AI_SCAN", "trade_id": "scan-b",
         "ai_decision": "REJECT", "direction": "SHORT"},
        {"schema": "ai_reason_outcome_v1", "trade_id": "far-1"},
    ])
    _write_jsonl(tmp_path / analyzer.DUPLICATE_INTENT_AUDIT_FILE, [
        {"schema": "duplicate_intent_audit_v1", "trade_id": "far-1", "shared_ai_call_id": "scan-a",
         "research_lane": "FAMILY_ADAPTIVE_REGIME", "direction": "SHORT"},
        {"schema": "duplicate_intent_audit_v1", "trade_id": "far-2", "shared_ai_call_id": "scan-b",
         "research_lane": "FAMILY_ADAPTIVE_REGIME", "direction": "SHORT"},
        {"schema": "duplicate_intent_audit_v1", "trade_id": "far-3", "shared_ai_call_id": "scan-a",
         "research_lane": "FAMILY_ADAPTIVE_REGIME", "direction": "SHORT"},
    ])
    _write_jsonl(tmp_path / analyzer.LANE_OPPORTUNITY_CAPTURE_FILE, [
        {"lane": "FAMILY_ADAPTIVE_REGIME", "event": "ORDER_SUBMITTED", "trade_id": trade_id}
        for trade_id in ("far-1", "far-2", "far-3", "far-9")
    ])


def test_ai_funnel_attributes_shared_scan_calls_to_tiles(tmp_path, monkeypatch):
    _store(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    trades = pd.DataFrame([
        {"trade_id": "far-1", "research_lane": "FAMILY_ADAPTIVE_REGIME", "dir": "SHORT",
         "shared_ai_call_id": "scan-a", "net_pnl_usd": 0.05},
        {"trade_id": "far-2", "research_lane": "FAMILY_ADAPTIVE_REGIME", "dir": "SHORT",
         "shared_ai_call_id": "scan-b", "net_pnl_usd": -0.04},
    ])
    report = analyzer.ai_funnel_report(trades=trades, session={})
    lane = json.loads((tmp_path / analyzer.AI_FUNNEL_REPORT_FILE).read_text(encoding="utf-8"))["lanes"]["FAMILY_ADAPTIVE_REGIME"]
    assert lane["ai_calls"] == 2
    assert lane["ai_linked_orders"] == 3 and lane["ai_unlinked_orders"] == 1
    assert lane["ai_approve_decisions"] == 2 and lane["ai_rejected_orders"] == 1
    split = lane["ai_vs_rules"]
    assert split["status"] == "DESCRIPTIVE_ONLY"
    assert split["matched_trades"] == 2
    assert split["approved_same_direction_trades"] == 1 and split["rejected_trades"] == 1
    assert split["rules_only_net_pnl_usd"] == 0.01
    assert split["ai_filtered_net_pnl_usd"] == 0.05
    assert split["incremental_net_pnl_usd"] == 0.04
    lanes = json.loads((tmp_path / analyzer.AI_FUNNEL_REPORT_FILE).read_text(encoding="utf-8"))["lanes"]
    assert "FAMILY_ATR_TRAIL" not in lanes and "FAMILY_CHANDELIER_3" not in lanes
    assert report is None or isinstance(report, dict)


def test_opposite_direction_approval_is_not_counted_as_ai_filtered():
    scans = {"scan-a": {"ai_decision": "APPROVE", "direction": "LONG"}}
    trades = pd.DataFrame([{"trade_id": "t1", "dir": "SHORT", "shared_ai_call_id": "scan-a", "net_pnl_usd": 0.2}])
    split = analyzer._tile_ai_attribution(["t1"], trades, scans, {"t1": "scan-a"})["ai_vs_rules"]
    assert split["approved_same_direction_trades"] == 0 and split["other_trades"] == 1
    assert split["incremental_net_pnl_usd"] == -0.2


def test_decision_panel_uses_tile_split_when_scorecard_has_no_group():
    lane = {"ai_calls": 59, "ai_vs_rules": {
        "status": "DESCRIPTIVE_ONLY", "matched_trades": 25, "approved_same_direction_trades": 18,
        "rejected_trades": 7, "incremental_net_pnl_usd": 0.02}}
    result = dv.ai_vs_rules({"raw_policy_id": "p1"}, lane, None)
    assert result["cell"]["state"] == dv.INSUFFICIENT and result["cell"]["value"] == 0.02
    assert "59 AI calls" in result["note"] and "18 AI-approved" in result["note"] and "7 AI-rejected" in result["note"]
    lane["ai_vs_rules"]["matched_trades"] = 40
    assert dv.ai_vs_rules({}, lane, None)["cell"]["value"] == 0.02
    assert "no AI calls" in dv.ai_vs_rules({}, {"ai_calls": 0}, None)["note"]
