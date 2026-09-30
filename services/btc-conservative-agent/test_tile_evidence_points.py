"""Tile evidence points: fill worlds, did vs missed, AI usefulness, collection, quarantine, EV ranking."""
import importlib.util
from pathlib import Path

import pandas as pd

from research import decision_view as dv
from research import evidence_points_view as epv
from research import research_dashboard as dashboard
from research import tile_evidence_points as tep

AGENT = Path(__file__).resolve().parent
T0 = 1_790_000_000.0
EPOCH = "epoch-v22-test"
REGISTRY = {"FAMILY_A": {"label": "Tile A", "requested_margin_usd": 0.2},
            "FAMILY_B": {"label": "Tile B", "requested_margin_usd": 0.2}}
ORDER = ("FAMILY_A", "FAMILY_B")


def _tape(seconds=6000, drift=0.01):
    return [{"bucket_ts": T0 + i, "bid": 100_000 + i * drift - 0.5, "ask": 100_000 + i * drift + 0.5}
            for i in range(seconds)]


def _trade(i, lane="FAMILY_A", pnl=0.004, exit_reason="TRAIL_STOP", **extra):
    row = {"trade_id": f"{lane}-t{i}", "research_lane": lane, "epoch_id": EPOCH, "dir": "LONG",
           "ts": T0 + 60 + i, "close_ts": T0 + 600 + i, "shared_ai_call_ts": T0 + 50 + i,
           "shared_ai_call_id": f"scan-{i}", "leverage": 100, "margin_usdt": 0.2, "entry": 100_000.6,
           "net_pnl_usd": round(pnl, 2), "exit_reason": exit_reason, "policy_signature": f"sig-{lane}",
           "execution_cost_accounting": repr({"observed_net_pnl_usd": pnl, "reconciled": True}),
           "funding_fees": 0.0001, "taker_fees": 0.0, "maker_fees": 0.0}
    row.update(extra)
    return row


def _build(**overrides):
    kwargs = dict(registry=REGISTRY, tile_order=ORDER, trades=[], expired=[], opportunities=[],
                  lifecycles=[], ai_scans=[], intent_audit=[], tape_rows=_tape(), epoch_id=EPOCH,
                  v2_start_ts=T0, generated_at=T0 + 6000)
    kwargs.update(overrides)
    return tep.build_tile_evidence_points(**kwargs)


def test_exact_net_pnl_prefers_the_reconciled_receipt_over_cent_rounding():
    assert tep.exact_net_pnl(_trade(1, pnl=0.0042)) == (0.0042, "TERMINAL_COST_RECEIPT_EXACT")
    unreconciled = _trade(1, pnl=0.0042, execution_cost_accounting=repr({"observed_net_pnl_usd": 0.0042}))
    assert tep.exact_net_pnl(unreconciled) == (0.0, "RECORDED_CSV_VALUE")
    assert tep.exact_net_pnl({}) == (None, "NET_PNL_MISSING")


def test_contaminated_rows_are_quarantined_with_a_reason():
    rows = [_trade(1), _trade(1), _trade(2, lane="CONTINUOUS"), _trade(3, lane="CONTROL_V1"),
            _trade(4, epoch_id="epoch-old"), _trade(5, close_ts=T0 - 10),
            _trade(6, exit_reason="PHANTOM_CANCEL_BY_RELAY"), _trade(7)]
    current, quarantined = tep.classify_trade_rows(rows, tiles=set(ORDER), epoch_id=EPOCH, v2_start_ts=T0)
    assert [r["trade_id"] for r in current] == ["FAMILY_A-t1", "FAMILY_A-t7"]
    assert [reason for _, reason in quarantined] == [
        "DUPLICATE_TRADE_ID", "LEGACY_CONTINUOUS_LANE", "NON_REGISTRY_LANE", "PRIOR_EPOCH",
        "PRE_CUTOVER", "RELAY_INTERFERENCE_PHANTOM_CANCEL"]


def test_every_order_lands_in_exactly_one_fill_world_with_no_fill_counterfactuals():
    opportunities = [{"lane": "FAMILY_A", "event": "ORDER_SUBMITTED", "trade_id": f"o{i}", "ts": T0 + i}
                     for i in range(6)]
    trades = [_trade(0, trade_id="o0"), _trade(1, trade_id="o1", entry_partial_fill=True)]
    expired = [
        {"research_lane": "FAMILY_A", "trade_id": "o2", "reason": "SIGNAL_TTL_EXPIRED", "dir": "LONG",
         "created_ts": T0 + 2, "expired_ts": T0 + 1802, "touched_limit": False,
         "no_fill_ttl_outcome": "EXPIRED_NO_TOUCH"},
        {"research_lane": "FAMILY_A", "trade_id": "o3", "reason": "ADMIN_FORCE_FLAT", "dir": "LONG",
         "created_ts": T0 + 3, "expired_ts": T0 + 5990},
        {"research_lane": "FAMILY_A", "trade_id": "o4", "reason": "LIMIT_NOT_REACHED", "dir": "LONG",
         "created_ts": T0 + 4, "expired_ts": T0 + 100},
        {"research_lane": "AI_SCAN", "trade_id": "x", "reason": "SIGNAL_TTL_EXPIRED"},
    ]
    report = _build(trades=trades, expired=expired, opportunities=opportunities)
    tile = report["fill_worlds"]["FAMILY_A"]
    assert tile["orders_submitted"] == 6
    assert tile["worlds"] == {"FILLED": 1, "PARTIAL": 1, "NO_FILL": 1, "EXPIRED": 1,
                              "ADMIN_CANCELLED": 1, "UNRESOLVED_OR_RESTING": 1}
    assert sum(tile["worlds"].values()) == tile["orders_submitted"]
    cf = tile["unfilled_counterfactual"]
    assert cf["EXPIRED"]["market_at_signal"]["n"] == 1 and cf["EXPIRED"]["market_at_signal"]["mean_usd"] > 0
    assert cf["ADMIN_CANCELLED"]["market_at_expiry"]["status_counts"] == {"HORIZON_INCOMPLETE": 1}
    assert tile["collector_touched_limit"] == {"NOT_TOUCHED": 2}
    assert report["quarantine_receipt"]["no_fill_rows_quarantined"] == {"NON_TILE_LANE:AI_SCAN": 1}
    assert report["fill_worlds"]["FAMILY_B"]["worlds"]["NO_FILL"] == 0
    codes = {g["code"] for g in report["evidence_gaps"]}
    assert "V3_LIFECYCLE_MISSING_TILE_NO_FILL_TERMINALS" in codes


def test_counterfactual_never_extrapolates_past_the_tape():
    tape = tep.Tape(_tape(seconds=100))
    outcome = tep.market_entry_outcome(tape, direction="LONG", anchor_ts=T0 + 10, leverage=100, margin_usd=0.2)
    assert {h["status"] for h in outcome["horizons"].values()} == {"HORIZON_INCOMPLETE"}
    assert tep.market_entry_outcome(tape, direction="LONG", anchor_ts=T0 - 50, leverage=100,
                                    margin_usd=0.2)["horizons"]["3600"]["status"] == "TAPE_NOT_COVERED"
    gapped = tep.Tape([r for r in _tape() if not (3000 <= r["bucket_ts"] - T0 <= 3700)])
    assert tep.fixed_horizon_outcome(gapped, direction="LONG", entry_price=100_000, entry_ts=T0,
                                     leverage=100, margin_usd=0.2, hold_sec=3600)["status"] == "TAPE_GAP"


def test_did_vs_missed_and_forced_exits_stay_out_of_ev():
    trades = [_trade(i, pnl=0.004) for i in range(3)] + [_trade(9, pnl=-0.5, exit_reason="ADMIN_MANUAL_CLOSE")]
    report = _build(trades=trades)
    executed = report["did_vs_missed"]["FAMILY_A"]["executed"]
    assert executed["actual_after_cost"]["n"] == 4
    assert executed["actual_strategy_exits"]["n"] == 3 and executed["actual_forced_exits"]["n"] == 1
    assert executed["market_at_signal"]["n"] == 4
    row = next(r for r in report["ev_ranking"]["rows"] if r["lane"] == "FAMILY_A")
    assert row["n"] == 3 and row["mean_usd"] == 0.004 and row["forced_exits_excluded"] == 1
    assert row["status"] == "NOT_ENOUGH_DATA" and row["closes_needed"] == 27 and row["rank"] is None
    assert row["all_closes_descriptive"]["n"] == 4
    assert report["quarantine_receipt"]["ev_ranking_exclusions"]["forced_exits_by_tile"]["FAMILY_A"] == 1
    assert report["quarantine_receipt"]["net_pnl_precision"]["csv_rounding_zero_rows"] == 3


def test_ev_ranking_publishes_once_a_tile_crosses_thirty_strategy_exits():
    trades = ([_trade(i, pnl=0.01 + (i % 3) * 0.001) for i in range(30)]
              + [_trade(i, lane="FAMILY_B", pnl=0.02) for i in range(29)])
    rows = {r["lane"]: r for r in _build(trades=trades)["ev_ranking"]["rows"]}
    assert rows["FAMILY_A"]["status"] == "RANKED" and rows["FAMILY_A"]["rank"] == 1
    assert rows["FAMILY_A"]["rank_confidence"] == "PROVISIONAL_CI_EXCLUDES_ZERO"
    low, high = rows["FAMILY_A"]["ci95_usd"]
    assert low < rows["FAMILY_A"]["mean_usd"] < high
    assert rows["FAMILY_A"]["cost_components_usd"]["funding_fees"] > 0
    assert rows["FAMILY_B"]["status"] == "NOT_ENOUGH_DATA" and rows["FAMILY_B"]["closes_needed"] == 1


def test_ai_usefulness_is_gated_on_the_smaller_arm():
    trades = [_trade(i, pnl=0.01) for i in range(40)] + [_trade(100 + i, pnl=-0.01) for i in range(5)]
    scans = ([{"trade_id": f"scan-{i}", "ai_decision": "APPROVE", "direction": "LONG"} for i in range(40)]
             + [{"trade_id": f"scan-{100 + i}", "ai_decision": "REJECT", "direction": "LONG"} for i in range(5)])
    ai = _build(trades=trades, ai_scans=scans)["ai_usefulness"]["FAMILY_A"]
    assert ai["ai_approved_same_direction"]["n"] == 40 and ai["ai_approved_same_direction"]["gate"] == "OK"
    assert ai["ai_rejected"]["n"] == 5 and ai["gate"].startswith("NOT_ENOUGH_DATA")
    assert ai["ai_filtered_minus_rules_only_usd"] == 0.05 and ai["unlinked_trades"] == 0


def test_fresh_collection_rate_identity_and_orphans():
    trades = [_trade(i) for i in range(4)] + [_trade(9, policy_signature="sig-other")]
    opportunities = [{"lane": "FAMILY_A", "event": "ORDER_SUBMITTED", "trade_id": "stale", "ts": T0},
                     {"lane": "FAMILY_A", "event": "ORDER_SUBMITTED", "trade_id": "fresh", "ts": T0 + 5900}]
    report = _build(trades=trades, opportunities=opportunities)
    tile = report["fresh_collection"]["tiles"]["FAMILY_A"]
    assert tile["closed_current_epoch"] == 5 and tile["identity_drift"] is True
    assert tile["unresolved_orders"] == 2 and tile["orphan_order_ids"] == ["stale"]
    assert tile["closes_per_hour_since_v2_start"] > 0
    codes = {g["code"] for g in report["evidence_gaps"]}
    assert {"IDENTITY_DRIFT", "ORPHAN_ORDERS_PAST_TTL"} <= codes


def _engine():
    spec = importlib.util.spec_from_file_location("evidence_engine", AGENT / "analyzer_research_engine_v62.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine


def test_analyzer_loader_uses_exact_pnl_and_ev_excludes_forced_exits():
    engine = _engine()
    frame = pd.DataFrame([_trade(1, pnl=0.0042), _trade(2, pnl=-0.0031, exit_reason="ADMIN_MANUAL_CLOSE"),
                          _trade(3, pnl=0.0, execution_cost_accounting="")])
    exact = engine.apply_exact_terminal_net_pnl(frame)
    assert exact["net_pnl_usd"].tolist() == [0.0042, -0.0031, 0.0]
    assert exact["net_pnl_usd_csv_cents"].tolist() == [0.0, -0.0, 0.0]
    assert exact["net_pnl_basis"].tolist()[-1] == "RECORDED_CSV_VALUE"
    assert frame["net_pnl_usd"].tolist() == [0.0, -0.0, 0.0]
    stats = engine._lane_closed_trade_stats(exact)
    assert stats["n"] == 2 and stats["n_all_closes"] == 3 and stats["forced_exits_excluded"] == 1
    assert abs(stats["mean_net_pnl_usd"] - 0.0021) < 1e-9
    assert engine.TILE_EVIDENCE_POINTS_REPORT_FILE in engine.ANALYZER_JSON_REPORT_FILES
    catalog = {fname for _, fname, _ in engine.DEEP_DIVE_REPORT_CATALOG}
    assert {engine.TILE_EVIDENCE_POINTS_REPORT_FILE, engine.TRADE_COHORT_QUARANTINE_FILE} <= catalog


def test_decision_page_names_forced_exit_exclusion_and_gates_ai_on_smaller_arm():
    ev = dv.after_cost_ev({"closed_trade_stats": {"n": 27, "n_all_closes": 35, "forced_exits_excluded": 8,
                                                  "mean_net_pnl_usd": -0.0007, "stdev_net_pnl_usd": 0.03}})
    assert "8 deploy/operator forced exits excluded" in ev["n_source"]
    lane = {"ai_calls": 80, "ai_vs_rules": {"status": "DESCRIPTIVE_ONLY", "matched_trades": 45,
                                             "approved_same_direction_trades": 40, "rejected_trades": 5,
                                             "incremental_net_pnl_usd": 0.05}}
    assert dv.ai_vs_rules({}, lane, None)["cell"]["state"] == dv.INSUFFICIENT
    lane["ai_vs_rules"].update(rejected_trades=30, approved_same_direction_trades=30, matched_trades=60)
    assert dv.ai_vs_rules({}, lane, None)["cell"]["state"] == dv.VALUE


def test_evidence_points_page_fails_closed_and_renders_current_report(monkeypatch):
    client = dashboard.app.test_client()
    monkeypatch.setattr(dashboard, "_current_lane_artifact", lambda name: ({}, {
        "status": "UNAVAILABLE_CURRENT_GENERATION", "blockers": ["REPORT_NOT_IN_CURRENT_GENERATION"]}))
    missing = client.get("/evidence-points").get_data(as_text=True)
    assert "NO CURRENT DATA" in missing and "REPORT_NOT_IN_CURRENT_GENERATION" in missing
    assert "evidenceFillWorlds" not in missing
    report = _build(trades=[_trade(i) for i in range(3)])
    monkeypatch.setattr(dashboard, "_current_lane_artifact", lambda name: (report, {
        "status": "CURRENT_GENERATION", "blockers": []}))
    page = client.get("/evidence-points").get_data(as_text=True)
    for table in ("evidenceFillWorlds", "evidenceDidVsMissed", "evidenceAiUsefulness",
                  "evidenceFreshCollection", "evidenceQuarantine", "evidenceEvRanking", "evidenceGaps"):
        assert table in page
    assert "NOT ENOUGH DATA" in page and "Tile A" in page
    assert client.get("/api/evidence-points").get_json()["report"]["schema"] == tep.REPORT_SCHEMA
    assert ("Evidence points", "/evidence-points") in dashboard.DECISION_NAV_LINKS
    assert epv.REPORT_FILE == tep.REPORT_FILE

def test_loader_reads_rotated_ledgers_and_flags_missing_tape_history(tmp_path):
    import json
    head = tmp_path / tep.TAPE_FILE
    older = tmp_path / (tep.TAPE_FILE + ".1")
    rows = _tape(seconds=4000)
    older.write_text("".join(json.dumps(r) + "\n" for r in rows[:3900]), encoding="utf-8")
    head.write_text("".join(json.dumps(r) + "\n" for r in rows[3900:]), encoding="utf-8")
    (tmp_path / (tep.TAPE_FILE + ".validation.json")).write_text('{"bucket_ts": 1}', encoding="utf-8")
    assert [p.name for p in tep.rotation_family(str(head))] == [older.name, head.name]
    inputs = tep.load_evidence_inputs(lambda name: str(tmp_path / name))
    assert len(inputs["tape_rows"]) == 4000 and inputs["tape_rows"][0]["bucket_ts"] == T0
    covered = _build(trades=[_trade(1)], tape_rows=inputs["tape_rows"])
    assert "TAPE_COVERAGE_STARTS_AFTER_V2" not in {g["code"] for g in covered["evidence_gaps"]}
    assert covered["did_vs_missed"]["FAMILY_A"]["executed"]["market_at_signal"]["n"] == 1
    head_only = _build(trades=[_trade(1)], tape_rows=rows[3900:])
    assert "TAPE_COVERAGE_STARTS_AFTER_V2" in {g["code"] for g in head_only["evidence_gaps"]}


def test_lifecycle_no_fill_terminals_feed_fill_worlds_and_reconcile_with_expired_ledger():
    opportunities = [{"lane": "FAMILY_A", "event": "ORDER_SUBMITTED", "trade_id": f"o{i}", "ts": T0 + i}
                     for i in range(3)]
    expired = [{"research_lane": "FAMILY_A", "trade_id": "o0", "reason": "SIGNAL_TTL_EXPIRED", "dir": "LONG",
                "created_ts": T0, "expired_ts": T0 + 1800}]
    def terminal(event_id, reason, ts):
        return {"research_lane": "FAMILY_A", "epoch_id": EPOCH, "terminal": True, "terminal_no_fill": True,
                "outcome_state": "NO_FILL", "record_id": f"lifecycle:{event_id}:paper-no-fill-terminal",
                "event_id": event_id, "terminal_reason": reason, "terminal_ts": ts,
                "executed_direction": "LONG", "submitted_ts": T0 + 1}
    lifecycles = [terminal("o0", "SIGNAL_TTL_EXPIRED", T0 + 1800), terminal("o1", "LIMIT_NOT_REACHED", T0 + 900),
                  terminal("o2", "CIRCUIT_BREAKER_ADMIN_MANUAL", T0 + 5990)]
    report = _build(trades=[_trade(9)], expired=expired, opportunities=opportunities, lifecycles=lifecycles)
    tile = report["fill_worlds"]["FAMILY_A"]
    assert tile["worlds"]["EXPIRED"] == 1 and tile["worlds"]["NO_FILL"] == 1 and tile["worlds"]["ADMIN_CANCELLED"] == 1
    assert tile["worlds"]["UNRESOLVED_OR_RESTING"] == 0
    assert tile["unfilled_counterfactual"]["NO_FILL"]["market_at_signal"]["n"] == 1
    assert tile["unfilled_counterfactual"]["NO_FILL"]["market_at_expiry"]["n"] == 1
    assert tile["no_fill_source_reconciliation"] == {
        "lifecycle_terminals_by_world": {"ADMIN_CANCELLED": 1, "EXPIRED": 1, "NO_FILL": 1},
        "in_both_ledgers": 1, "expired_orders_ledger_only": 0, "lifecycle_ledger_only": 2,
        "world_disagreements": 0}
    assert "V3_LIFECYCLE_MISSING_TILE_NO_FILL_TERMINALS" not in {g["code"] for g in report["evidence_gaps"]}
