"""Decision page: missing data is never zero, and every metric has one source."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pandas as pd

from research import decision_view as dv
from research import research_dashboard as dashboard

AGENT = Path(__file__).resolve().parent
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def test_missing_zero_and_stale_render_differently():
    assert dv.render_metric(dv.metric(None)) == "no data yet (not collected yet)"
    assert dv.render_metric(None) == "no data yet"
    assert dv.render_metric(dv.metric(0)) == "0"
    assert dv.render_metric(dv.metric(0, stale_since="2026-09-26 19:35")) == "0 · stale since 2026-09-26 19:35"
    assert dv.render_metric(dv.metric(float("nan"))).startswith("no data yet")


def test_tile_absent_from_funnel_is_no_data_not_zero():
    funnel = dv.execution_funnel_metrics(None)
    assert all(cell["state"] == dv.NO_DATA for cell in funnel.values())
    assert "0" not in dv.render_metric(funnel["fill_rate_pct"])


def test_zero_submitted_orders_is_no_data_rate_but_real_zero_counts():
    funnel = dv.execution_funnel_metrics({"approve": 4, "order_submitted": 0, "filled": 0, "closed": 0})
    assert funnel["filled"] == {"state": dv.VALUE, "value": 0}
    assert funnel["fill_rate_pct"]["state"] == dv.NO_DATA
    assert dv.render_metric(funnel["fill_rate_pct"]) == "no data yet (no orders submitted yet)"


def test_fill_rate_has_one_definition():
    lane = {"approve": 40, "order_submitted": 26, "filled": 6, "closed": 3}
    funnel = dv.execution_funnel_metrics(lane)
    assert funnel["fill_rate_pct"]["value"] == round(100 * 6 / 26, 1)
    assert funnel["approve_to_fill_pct"]["value"] == round(100 * 6 / 40, 1)
    assert funnel["not_filled"]["value"] == 20
    payload = dv.build_decision_payload(
        tile_order=("A",), registry={"A": {"label": "Tile A"}},
        funnel_report={"lanes": {"A": lane}}, ai_coverage=None,
        generation={"generated_at": NOW.isoformat(), "current": True}, alarms=[], freshness_rows=[],
    )
    assert payload["tiles"][0]["funnel"]["fill_rate_pct"] == funnel["fill_rate_pct"]


def test_small_sample_is_not_enough_data_and_never_ranked():
    ev = dv.after_cost_ev({"closed": 3, "net_pnl_usd": 0.9})
    assert ev["cell"]["state"] == dv.INSUFFICIENT
    assert dv.render_metric(ev["cell"]) == "NOT ENOUGH DATA (n<30)"
    verdict = dv.tile_verdict(has_generation=True, ev=ev, stale_since=None)
    assert verdict["code"] == "NOT_ENOUGH_DATA"
    for n in (0, None):
        assert dv.lane_evidence_status(n) == "NO DATA YET"
    assert dv.lane_evidence_status(29) == "NOT ENOUGH DATA (n<30)"
    assert dv.lane_evidence_status(30) is None


def test_ev_confidence_interval_and_verdicts():
    positive = dv.after_cost_ev({"closed_trade_stats": {"n": 100, "mean_net_pnl_usd": 0.5, "stdev_net_pnl_usd": 1.0}})
    assert positive["ci95_usd"][0] > 0 and "above zero" in positive["note"]
    assert dv.tile_verdict(has_generation=True, ev=positive, stale_since=None)["code"] == "POSITIVE"
    spans = dv.after_cost_ev({"closed_trade_stats": {"n": 40, "mean_net_pnl_usd": 0.01, "stdev_net_pnl_usd": 2.0}})
    stale = dv.tile_verdict(has_generation=True, ev=spans, stale_since="2026-09-26")
    assert stale["code"] == "INCONCLUSIVE" and stale["text"].endswith("(stale since 2026-09-26)")
    assert dv.tile_verdict(has_generation=False, ev=spans, stale_since=None)["code"] == "NO_DATA"


def test_mae_mfe_missing_is_explained():
    missing = dv.mae_mfe_metrics({"closed": 5})
    assert missing["mae"]["state"] == dv.NO_DATA and "next analyzer generation" in missing["mae"]["reason"]
    present = dv.mae_mfe_metrics({"closed_trade_stats": {
        "mae_mfe_rows": 4, "median_mae_margin_pct": -12.5, "median_mfe_margin_pct": 30.0}})
    assert present["mae"]["value"] == -12.5 and present["mfe"]["value"] == 30.0


def test_ai_vs_rules_reports_reason_instead_of_zero():
    result = dv.ai_vs_rules({"raw_policy_id": "p1"}, {"ai_calls": 0}, None)
    assert result["cell"]["state"] == dv.NO_DATA
    assert "no AI calls" in result["note"]


def test_alarms_surface_transfer_disk_wal_and_staleness():
    alarms = dv.collect_alarms(
        freshness={"current": False, "reasons": ["mirror receipt failed"]},
        analyzer_run={"state": "FAILED", "detail": "boom", "finishedAt": NOW.isoformat()},
        ack_watcher={"lastAckAt": (NOW - timedelta(hours=5)).isoformat(), "consecutiveSyncFailures": 3,
                     "lastSyncResult": {"detail": "timeout", "at": NOW.isoformat()}},
        monitor_state={"alerts": [{"code": "SYNC_FAIL_REPEATED", "severity": "critical", "detail": "x"}]},
        segment_status=None,
        local_disk={"used_pct": 91.0},
        local_wal=[{"name": "journal.sqlite", "bytes": dv.LOCAL_WAL_ALARM_BYTES}],
        now=NOW,
    )
    codes = {a["code"] for a in alarms}
    assert {"ANALYZER_RUN_FAILED", "ANALYZER_GENERATION_STALE", "TRANSFER_ACK_LAG", "TRANSFER_SYNC_FAILING",
            "SYNC_FAIL_REPEATED", "SEGMENT_PULLER_NO_DATA", "LOCAL_DISK_PRESSURE",
            "LOCAL_SQLITE_WAL_LARGE"} <= codes
    assert alarms[-1]["severity"] == "info"
    missing = dv.collect_alarms(freshness=None, analyzer_run=None, ack_watcher=None, monitor_state=None,
                                segment_status={"updated_at": NOW.isoformat()}, local_disk=None,
                                local_wal=None, now=NOW)
    assert [a["code"] for a in missing] == ["TRANSFER_STATUS_UNAVAILABLE"]


def test_freshness_text_distinguishes_missing_fresh_and_stale():
    assert dv.freshness_text(None, max_age_sec=60, now=NOW) == "no data yet"
    assert dv.freshness_text(NOW.isoformat(), max_age_sec=60, now=NOW) == NOW.isoformat()
    old = (NOW - timedelta(hours=3)).isoformat()
    assert dv.freshness_text(old, max_age_sec=60, now=NOW) == f"stale since {old}"


def test_decision_page_renders_registry_tiles_and_explicit_empty_states():
    payload = dv.build_decision_payload(
        tile_order=tuple(dashboard.DASHBOARD_PRIMARY_LANES), registry=dashboard.ACTIVE_TILE_REGISTRY,
        funnel_report=None, ai_coverage=None,
        generation={"generated_at": None, "current": False}, alarms=[], freshness_rows=[],
    )
    page = dv.render_decision_html(payload, nav_links=[("Details", "/details")])
    assert [t["lane"] for t in payload["tiles"]] == list(dashboard.DASHBOARD_PRIMARY_LANES)
    assert "NO DATA YET: the analyzer has not published a generation" in page
    assert 'id="decisionTiles"' in page and 'href="/details"' in page
    assert ">0<" not in page and "$+0.000" not in page


def test_root_serves_decision_and_details_keeps_full_report():
    client = dashboard.app.test_client()
    root = client.get("/")
    assert root.status_code == 200
    text = root.get_data(as_text=True)
    assert "Research Dashboard · Decision" in text and 'id="decisionAlarms"' in text
    details = client.get("/details").get_data(as_text=True)
    assert "Research Dashboard · Details" in details and 'id="decision-link"' in details
    api = client.get("/api/decision").get_json()
    assert api["schema"] == "analyzer_decision_view_v1"
    assert [t["lane"] for t in api["tiles"]] == list(dashboard.DASHBOARD_PRIMARY_LANES)


def test_registry_failure_is_visible_on_decision_page(monkeypatch):
    monkeypatch.setattr(dashboard, "REGISTRY_IMPORT_ERROR", "ImportError: boom")
    text = dashboard.app.test_client().get("/").get_data(as_text=True)
    assert "REGISTRY UNAVAILABLE" in text and "ImportError: boom" in text


def test_analyzer_publishes_closed_trade_stats_per_tile():
    spec = importlib.util.spec_from_file_location("decision_engine", AGENT / "analyzer_research_engine_v62.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    trades = pd.DataFrame({
        "outcome_net_pnl_usd": [0.2, -0.1, 0.4],
        "max_drawdown_margin_pct": [-10.0, -30.0, None],
        "max_profit_margin_pct": [25.0, 5.0, 40.0],
    })
    stats = engine._lane_closed_trade_stats(trades)
    assert stats["n"] == 3 and abs(stats["mean_net_pnl_usd"] - 0.166667) < 1e-6
    assert stats["mae_mfe_rows"] == 2 and stats["median_mae_margin_pct"] == -20.0
    empty = engine._lane_closed_trade_stats(trades.iloc[0:0])
    assert empty["n"] == 0 and empty["mean_net_pnl_usd"] is None

def test_trade_counts_reconcile_details_summary_with_tile_rows():
    funnel = {"trade_scope": {"session_trade_rows": 57, "tile_trade_rows": 53,
                              "non_tile_trade_rows": {"CONTINUOUS": 4}}}
    ok = dv.trade_count_reconciliation(funnel, 57, 53)
    assert ok["consistent"] is True
    assert ok["text"] == "The Details summary trade count (57) = 53 tile trades + 4 non-tile trades (CONTINUOUS 4)."
    bad = dv.trade_count_reconciliation(funnel, 60, 53)
    assert bad["consistent"] is False and "MISMATCH: the executive summary reports 60" in bad["text"]


def test_trade_counts_without_scope_explain_both_scopes():
    pending = dv.trade_count_reconciliation({}, 57, 53)
    assert pending["available"] is False
    assert "(57) counts every session trade row, including non-tile lanes" in pending["text"]
    assert "per-tile total here is 53 (CLOSED lifecycle events)" in pending["text"]
    assert dv.trade_count_reconciliation(None, None, None)["text"] == dv.NO_DATA_TEXT


def test_sample_names_its_source():
    assert dv.after_cost_ev({"closed_trade_stats": {"n": 5, "mean_net_pnl_usd": 0.1}})["n_source"] == "trade log rows"
    assert dv.after_cost_ev({"closed": 3, "net_pnl_usd": 0.3})["n_source"] == "CLOSED lifecycle events"
    assert dv.after_cost_ev(None)["n_source"] is None


def test_analyzer_trade_scope_uses_the_session_trade_frame():
    spec = importlib.util.spec_from_file_location("decision_engine_scope", AGENT / "analyzer_research_engine_v62.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    trades = pd.DataFrame({"trade_id": ["a", "b", "b", "c", "d"],
                           "research_lane": ["FAMILY_X", "FAMILY_X", "FAMILY_X", "CONTINUOUS", None]})
    scope = engine._session_trade_scope(trades, ["FAMILY_X"])
    assert scope["session_trade_rows"] == 4 and scope["tile_trade_rows"] == 2
    assert scope["non_tile_trade_rows"] == {"CONTINUOUS": 1, "UNLABELLED": 1}
    assert engine._session_trade_scope(pd.DataFrame({"x": [1]}), ["FAMILY_X"]) is None
