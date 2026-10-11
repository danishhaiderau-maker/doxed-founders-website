"""R14/R15/R16/R21/R22/R23 live monitor routes: token-gated, manifest-complete, alerts fire."""
import time

import flask

import live_copy_monitor_api as api
import live_copy_monitor_extra as lcx

NEW = ("events", "fill-quality", "daily", "slo", "drills", "funding", "routes")


def _client(authorized=True, ctx=None):
    app = flask.Flask(__name__)
    app.register_blueprint(api.blueprint)
    api.wire(lambda: dict(ctx or {}), authorize=lambda: authorized)
    return app.test_client()


def test_new_routes_need_monitor_auth():
    c = _client(authorized=False)
    for name in NEW:
        assert c.get(f"/api/monitor/live/{name}").status_code == 401


def test_new_routes_200_with_empty_context_and_report_no_evidence():
    c = _client(ctx={})
    for name in NEW:
        r = c.get(f"/api/monitor/live/{name}")
        assert r.status_code == 200, name
    assert c.get("/api/monitor/live/funding").get_json()["evidence"] == "NONE"
    assert c.get("/api/monitor/live/drills").get_json()["verdict"] == "AMBER"


def test_manifest_lists_every_blueprint_route():
    app = flask.Flask(__name__)
    app.register_blueprint(api.blueprint)
    rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/monitor/")}
    assert rules == set(lcx.LIVE_ROUTE_MANIFEST)


def test_event_sequence_gap_is_red():
    out = lcx.events([{"trade_id": "t1", "event_seq": 1, "created_at_ts": 1}, {"trade_id": "t1", "event_seq": 3,
                       "created_at_ts": 2}], [], [])
    assert out["verdict"] == "RED" and out["sequence_gaps"][0]["missing_seq"] == [2]


def test_markout_worse_than_twin_is_amber():
    now = time.time()
    chains = {"accounts": {"k": {"correlation_id": "c", "account": "a", "lane": "L", "fill_price": 100.5,
                                 "fill_at_ts": now - 100}}}
    out = lcx.fill_quality(chains, {"c": {"entry_price": 100.0, "side": "LONG"}}, lambda ts: 100.2, now)
    assert out["verdict"] == "AMBER" and out["alerts"][0]["code"] == "LIVE_MARKOUT_WORSE_THAN_PAPER"


def test_daily_ledger_mismatch_is_red():
    rep = [{"type": "POSITION_CLOSED", "account": "a", "sent_at_ts": 1_760_000_000, "realized_pnl_usd": 0.5}]
    day = lcx._day(1_760_000_000)
    assert lcx.daily(rep, {day: {"a": 0.4}})["verdict"] == "RED"
    assert lcx.daily(rep, {day: {"a": 0.5}})["verdict"] == "GREEN"
