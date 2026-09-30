"""Decision page: missing data is never zero, and every metric has one source."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
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


def _puller(age_min=1, ack_age_min=1, **overrides):
    status = {"schema": "research_segment_puller_status_v1", "prefix": "v2", "applied_seq": 136, "acked_seq": 136,
              "last_error": None, "updated_at": (NOW - timedelta(minutes=age_min)).isoformat(),
              "ack_receipt": {"ok": True, "result": "RECORDED", "through_seq": 136,
                              "received_at": (NOW - timedelta(minutes=ack_age_min)).isoformat()}}
    status.update(overrides)
    return status


def _fly_head(age_min=1, **overrides):
    head = {"schema": "fly_segment_head_snapshot_v1", "ok": True,
            "observedAt": (NOW - timedelta(minutes=age_min)).isoformat(), "segments_enabled": True,
            "shipped_seq": 139, "laptop_acked_seq": 136, "unshipped_bytes": 299477, "last_error": None}
    head.update(overrides)
    return head


def _parity(verdict="GREEN", age_min=5):
    return {"verdict": verdict, "seq": 136, "counts": {"missing": 0},
            "generated_at": (NOW - timedelta(minutes=age_min)).isoformat()}


def _alarms(**overrides):
    kwargs = dict(freshness={"current": True}, analyzer_run=None, monitor_state=None,
                  segment_status=_puller(), fly_segment_head=_fly_head(), segment_parity=_parity(),
                  local_disk=None, local_wal=None, now=NOW)
    kwargs.update(overrides)
    return dv.collect_alarms(**kwargs)


def test_healthy_v2_transfer_raises_no_alarms():
    assert _alarms() == []


def test_alarms_surface_v2_transfer_disk_wal_and_staleness():
    alarms = _alarms(
        freshness={"current": False, "reasons": ["mirror receipt failed"]},
        analyzer_run={"state": "FAILED", "detail": "boom", "finishedAt": NOW.isoformat()},
        segment_status=_puller(age_min=40, ack_age_min=40, last_error="HTTP 503"),
        fly_segment_head=_fly_head(last_error="PLAN_RACE", unshipped_bytes=dv.SEGMENT_UNSHIPPED_ALARM_BYTES + 1,
                                   shipped_seq=200, laptop_acked_seq=100),
        segment_parity=_parity("RED", age_min=200),
        local_disk={"used_pct": 91.0},
        local_wal=[{"name": "journal.sqlite", "bytes": dv.LOCAL_WAL_ALARM_BYTES}],
    )
    codes = {a["code"] for a in alarms}
    assert codes == {"ANALYZER_RUN_FAILED", "ANALYZER_GENERATION_STALE", "SEGMENT_PULLER_STALE",
                     "SEGMENT_PULLER_ERROR", "SEGMENT_ACK_STALE", "FLY_SEGMENT_SHIPPER_ERROR",
                     "SEGMENT_UNSHIPPED_BACKLOG", "SEGMENT_ACK_BEHIND", "SEGMENT_PARITY_NOT_GREEN",
                     "SEGMENT_PARITY_STALE", "LOCAL_DISK_PRESSURE", "LOCAL_SQLITE_WAL_LARGE"}
    assert [a["severity"] for a in alarms] == sorted(
        (a["severity"] for a in alarms), key={"critical": 0, "warning": 1, "info": 2}.get)


def test_missing_v2_inputs_are_explicit_alarms_not_silence():
    codes = {a["code"] for a in _alarms(segment_status=None, fly_segment_head=None, segment_parity=None)}
    assert codes == {"SEGMENT_PULLER_NO_DATA", "FLY_SEGMENT_HEAD_NO_DATA", "SEGMENT_PARITY_NO_DATA"}
    stale_head = {a["code"] for a in _alarms(fly_segment_head=_fly_head(age_min=60))}
    assert stale_head == {"FLY_SEGMENT_HEAD_NO_DATA"}
    failed_head = {a["code"] for a in _alarms(fly_segment_head={**_fly_head(), "ok": False})}
    assert failed_head == {"FLY_SEGMENT_HEAD_NO_DATA"}
    rejected = _alarms(segment_status=_puller(ack_receipt={"ok": False, "result": "SEQ_REGRESSION", "through_seq": 3,
                                                            "received_at": NOW.isoformat()}))
    assert [a["code"] for a in rejected] == ["SEGMENT_ACK_REJECTED"]


def test_retired_mirror_alarms_never_resurface_from_monitor_state():
    monitor = {"alerts": [{"code": code, "severity": "critical", "detail": "legacy"}
                          for code in sorted(dv.RETIRED_TRANSFER_ALARM_CODES)]
                         + [{"code": "ANALYZER_NO_COMPLETION", "severity": "critical", "detail": "x"}]}
    assert [a["code"] for a in _alarms(monitor_state=monitor)] == ["ANALYZER_NO_COMPLETION"]


def test_segment_freshness_rows_replace_legacy_mirror_rows():
    rows = dv.segment_freshness_rows(
        segment_status=_puller(), fly_segment_head=_fly_head(), segment_parity=_parity(),
        promotion={"segmentPrefix": "v2", "segmentAppliedSeq": 124, "syncedAt": "2026-09-30T03:50:42Z"}, now=NOW)
    text = {row["label"]: row["text"] for row in rows}
    assert list(text) == ["Last v2 segment applied on laptop", "Last v2 ACK accepted by Fly", "Fly v2 shipper",
                          "v2 checkpoint parity", "Analyzer store promoted from"]
    assert text["Last v2 segment applied on laptop"].startswith("seq 136 \u00b7 ")
    assert "published seq 139" in text["Fly v2 shipper"] and "unshipped 0.3 MB" in text["Fly v2 shipper"]
    assert text["v2 checkpoint parity"].startswith("GREEN at seq 136")
    assert text["Analyzer store promoted from"].startswith("v2 seq 124 promoted")
    empty = {row["label"]: row["text"] for row in dv.segment_freshness_rows(
        segment_status=None, fly_segment_head=None, segment_parity=None, promotion={"ok": True}, now=NOW)}
    assert all(value.startswith(dv.NO_DATA_TEXT) for value in empty.values())
    old = {row["label"]: row["text"] for row in dv.segment_freshness_rows(
        segment_status=_puller(age_min=60, ack_age_min=60), fly_segment_head=None, segment_parity=None,
        promotion=None, now=NOW)}
    assert "stale since" in old["Last v2 segment applied on laptop"]
    assert "stale since" in old["Last v2 ACK accepted by Fly"]


def test_decision_payload_has_no_legacy_transfer_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "LAPTOP_CHAIN_STATE_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "SEGMENT_PULLER_STATUS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(dashboard, "SEGMENT_PARITY_FILES", (tmp_path / "missing-parity.json",))
    monkeypatch.setattr(dashboard, "FLY_SEGMENT_HEAD_FILE", tmp_path / "missing-head.json")
    payload = dashboard._decision_payload()
    labels = [row["label"] for row in payload["freshness"]]
    for legacy in ("Last laptop ACK", "Last Fly to laptop sync attempt", "Mirror sync receipt"):
        assert legacy not in labels
    codes = {a["code"] for a in payload["alarms"]}
    assert not codes & dv.RETIRED_TRANSFER_ALARM_CODES
    assert "SEGMENT_PULLER_NO_DATA" in codes


def test_relay_snapshot_is_read_from_the_laptop_chain_state(monkeypatch, tmp_path):
    snapshot = tmp_path / "relay_status_snapshot_v1.json"
    snapshot.write_text(json.dumps({
        "schema": "relay_status_snapshot_v1", "observedAt": datetime.now(timezone.utc).isoformat(), "ok": True,
        "status": "PAUSED", "relayExecutionMode": "PAUSED", "relayArmedAt": None,
        "reconciliation": {"signedExchangePositionQty": 0, "signedLedgerOpenQty": 0, "updatedAt": "x"},
        "exchangeOrderAudit": {"known": True, "activeOrderCount": 0}}), encoding="utf-8")
    monkeypatch.setattr(dashboard, "RELAY_STATUS_SNAPSHOT_FILE", snapshot)
    relay = dashboard._decision_payload()["relay_state"]
    assert relay["armed"] == {"state": dv.VALUE, "value": "DISARMED (PAUSED)"}
    assert relay["exchange_position_btc"] == {"state": dv.VALUE, "value": 0}


def test_integrity_receipt_is_bound_to_the_published_generation(monkeypatch):
    receipts = {dashboard.ANALYZER_INTEGRITY_FILE: {"valid": True, "report_status": "VALID", "generated_at": "G1"},
                dashboard.REPORT_MANIFEST_FILE: {"generated_at": "G1"}}
    monkeypatch.setattr(dashboard, "_read_json", lambda name, default=None: receipts.get(name, default))
    assert dashboard._generation_bound_integrity_receipt()["report_status"] == "VALID"
    receipts[dashboard.REPORT_MANIFEST_FILE] = {"generated_at": "G2"}
    assert dashboard._generation_bound_integrity_receipt() == {}


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


def test_relay_state_is_no_data_without_a_snapshot_never_flat_or_disarmed():
    registry = {"FAMILY_HYBRID_RUNNER": {"platform_relay_eligible": False}}
    view = dv.relay_state_view(None, registry=registry, now=NOW)
    assert view["allowlist"] == []
    assert view["allowlist_text"].startswith("empty")
    for key in ("armed", "executor_heartbeat", "exchange_position_btc", "ledger_open_btc", "active_orders"):
        assert view[key]["state"] == dv.NO_DATA, key
    failed = dv.relay_state_view(
        {"schema": "relay_status_snapshot_v1", "observedAt": NOW.isoformat(), "ok": False,
         "error": "RELAY_STATUS_HTTP_FAILED"},
        registry=registry, now=NOW,
    )
    assert "RELAY_STATUS_HTTP_FAILED" in dv.render_metric(failed["armed"])
    assert failed["exchange_position_btc"]["state"] == dv.NO_DATA


def test_relay_state_renders_disarmed_flat_heartbeat_and_goes_stale():
    snapshot = {
        "schema": "relay_status_snapshot_v1", "observedAt": (NOW - timedelta(minutes=2)).isoformat(), "ok": True,
        "status": "PAUSED", "relayExecutionMode": "PAUSED", "relayArmedAt": None,
        "reconciliation": {"signedExchangePositionQty": 0, "signedLedgerOpenQty": 0,
                           "updatedAt": "2026-09-30T11:57:00Z"},
        "exchangeOrderAudit": {"known": True, "activeOrderCount": 0},
        "relayExecutor": {"status": "PAUSED_HEALTHY", "healthy": True, "observedAt": "2026-09-30T11:59:00Z"},
    }
    view = dv.relay_state_view(snapshot, registry={}, now=NOW)
    assert dv.render_metric(view["armed"]) == "DISARMED (PAUSED)"
    assert view["exchange_position_btc"] == {"state": dv.VALUE, "value": 0}
    assert view["active_orders"] == {"state": dv.VALUE, "value": 0}
    assert dv.render_metric(view["executor_heartbeat"]).startswith("PAUSED_HEALTHY (healthy)")
    stale = dv.relay_state_view(snapshot, registry={}, now=NOW + timedelta(hours=1))
    assert stale["exchange_position_btc"]["state"] == dv.STALE
    unknown_orders = dv.relay_state_view(
        {**snapshot, "exchangeOrderAudit": {"known": False, "activeOrderCount": 0}}, registry={}, now=NOW)
    assert unknown_orders["active_orders"]["state"] == dv.NO_DATA
    html = dv.render_decision_html(
        dv.build_decision_payload(tile_order=(), registry={}, funnel_report=None, ai_coverage=None,
                                  generation={}, alarms=[], freshness_rows=[], relay_state=view),
        nav_links=(),
    )
    assert 'id="decisionRelayState"' in html
    assert "DISARMED (PAUSED)" in html and "empty (no tile may copy to Bitfinex)" in html
