import live_vs_paper as lv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_long_pair_computes_slippage_latencies_outcome_and_missing_fields():
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 101.0, "exit_ts": 50.0, "entry_ts": 10.0, "funding_usd_raw": 0.001}
    ch = {"correlation_id": "t1", "fill_price": 100.02, "exit_price": 100.98, "fly_signal_at_ts": 9.0, "order_sent_at_ts": 9.4,
          "fill_at_ts": 10.2, "stop_confirmed_at_ts": 10.9, "closed": True}
    r = lv.pair_row("t1", ch, twin)
    assert r["entry_slippage_bp"] == 2.0 and r["signal_to_send_ms"] == 400.0 and r["send_to_fill_ms"] == 800.0
    assert r["fill_to_stop_ms"] == 700.0 and r["outcome_bp_paper"] == 100.0 and r["outcome_bp_live"] == 95.98
    assert r["exit_slippage_bp"] == 1.98 and "live_exit_ts" in r["missing"] and "funding_usd_live" in r["missing"]


def test_short_signs_are_flipped():
    r = lv.pair_row("s", {"fill_price": 99.98, "exit_price": 99.0}, {"side": "SHORT", "entry_price": 100.0, "exit_price": 99.0})
    assert r["entry_slippage_bp"] == 2.0 and r["outcome_bp_live"] == round(-(99.0 / 99.98 - 1) * 1e4, 2)


def test_paper_signal_live_did_not_trade_gets_a_reason_code():
    out = lv.build([{"correlation_id": "a", "fill_price": 100.0}], {"a": {"side": "LONG", "entry_price": 100.0}, "b": {"side": "LONG"}, "c": {}},
                   paper_signal_ids=["a", "b", "c"], skip_reasons={"b": "LIVE_BUSY_SKIPPED"})
    by = {r["trade_id"]: r for r in out["rows"]}
    assert by["b"]["live_not_traded_reason"] == "LIVE_BUSY_SKIPPED" and by["c"]["live_not_traded_reason"] == "NO_LIVE_CHAIN"
    assert out["n_live_traded"] == 1 and out["n_paper_only"] == 2 and out["paper_only_by_reason"]["LIVE_BUSY_SKIPPED"] == 1


def test_declined_chain_reason_comes_from_executor_decision():
    r = lv.pair_row("d", {"executor_decision": {"decision": "SKIPPED", "reason": "MARK_BAND"}}, {"side": "LONG"})
    assert not r["live_traded"] and r["live_not_traded_reason"] == "MARK_BAND"


def test_route_returns_pairs_including_paper_only_signal_with_lane_filter():
    from flask import Flask
    import live_copy_monitor_api as api
    ctx = {"approvals": [{"correlation_id": "g1", "research_lane": "FAMILY_GS01_XV", "signal_at_ts": 9.0, "event": "ORDER_PLACED"},
                         {"correlation_id": "g2", "research_lane": "FAMILY_GS01_XV", "signal_at_ts": 20.0, "event": "ORDER_PLACED"},
                         {"correlation_id": "x1", "research_lane": "FAMILY_OTHER", "signal_at_ts": 5.0, "event": "ORDER_PLACED"}],
           "reports": [{"correlation_id": "g1", "account": "A", "lane": "FAMILY_GS01_XV", "type": "ORDER_FILLED",
                        "fill": {"price": 100.0}, "timeline": {"order_sent_at_ts": 9.4, "fill_at_ts": 10.0}}],
           "paper_twins": {"g1": {"side": "LONG", "entry_price": 100.0}, "g2": {"side": "LONG"}},
           "live_skip_reasons": {"g2": "LIVE_BUSY_SKIPPED"}}
    api.wire(lambda: ctx, lambda: True)
    app = Flask(__name__)
    app.register_blueprint(api.blueprint)
    r = app.test_client().get("/api/monitor/live-vs-paper?lane=GS01")
    assert r.status_code == 200, r.data
    body = r.get_json()
    d = body.get("data", body)
    ids = {x["trade_id"]: x for x in d["rows"]}
    assert set(ids) == {"g1", "g2"} and ids["g1"]["live_traded"] and ids["g2"]["live_not_traded_reason"] == "LIVE_BUSY_SKIPPED"


def test_paper_signal_during_an_open_live_position_is_derived_as_live_busy_skipped():
    chains = [{"correlation_id": "a", "fill_price": 100.0, "fill_at_ts": 100.0, "closed": False}]
    intents = {"b": {"fly_signal_at_ts": 150.0}, "c": {"fly_signal_at_ts": 50.0}}
    out = lv.build(chains, {"a": {"side": "LONG"}}, intents, paper_signal_ids=["a", "b", "c"])
    by = {r["trade_id"]: r for r in out["rows"]}
    assert by["b"]["live_not_traded_reason"] == "LIVE_BUSY_SKIPPED" and by["c"]["live_not_traded_reason"] == "NO_LIVE_CHAIN"


def test_g5_one_pair_row_per_live_signal_and_anchor_drift_measured():
    """HM G5/B2: duplicate chains collapse to one pair row per live signal; a live chain with no paper twin is
    NO_PAPER_TWIN; the stop-anchor drift is measured from the pair (paper anchor vs live fill), not NO_LIVE_TWIN."""
    chains = [{"correlation_id": "a", "lane": "GS-01", "fill_price": 100.1, "fill_at_ts": 10.0},
              {"correlation_id": "a", "lane": "GS-01", "fill_price": 100.1, "fill_at_ts": 10.0},
              {"correlation_id": "b", "lane": "GS-01", "fill_price": 99.0, "fill_at_ts": 20.0}]
    twins = {"a": {"side": "LONG", "entry_price": 100.0, "exchange_stop_anchor_px": 100.0},
             "c": {"side": "SHORT", "entry_price": 101.0}}
    out = lv.build(chains, twins, paper_signal_ids=["a", "c"], skip_reasons={"c": "ACCOUNT_NOT_ARMED"})
    by = {r["trade_id"]: r for r in out["rows"]}
    assert [r["trade_id"] for r in out["rows"]] == ["a", "b", "c"]
    assert out["n_live_signals"] == 2 and out["n_live_pair_rows"] == 2 and out["one_pair_row_per_live_signal"]
    assert out["n_no_paper_twin"] == 1
    assert by["a"]["twin_status"] == "PAIRED" and by["b"]["twin_status"] == "NO_PAPER_TWIN"
    assert by["c"]["twin_status"] == "PAPER_ONLY"
    assert by["a"]["stop_anchor_drift_status"] == "MEASURED" and abs(by["a"]["stop_anchor_drift_bp"] - 10.0) < 1e-6
    assert by["c"]["stop_anchor_drift_status"] == "LIVE_NOT_TRADED"
    assert by["b"]["stop_anchor_drift_status"] == "UNVERIFIED"  # live fill but no paper anchor


def test_k3_live_only_forced_close_marks_the_paper_twin_non_countable():
    """HM R2 K3 / RK C (R8-live both legs): a live-only USER_RELAY_STOP / ADMIN_FORCE_FLAT / maintenance close
    makes the pair AND its paper twin non-countable, even though the paper twin itself closed on TP."""
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 101.0, "exit_reason": "TAKE_PROFIT"}
    for live_reason in ("USER_RELAY_STOP", "ADMIN_FORCE_FLAT", "SOURCE_CONFIRMED_DEPLOY_FORCE_FLAT",
                        "ADMIN_PAUSE:DEPLOY_MAINTENANCE"):
        chain = {"correlation_id": "c1", "fill_price": 100.1, "fill_at_ts": 10.0, "exit_price": 100.5,
                 "exit_reason": live_reason, "closed": True}
        r = lv.pair_row("c1", chain, twin)
        assert r["non_countable"] is True and r["live_non_countable"] is True, live_reason
        assert r["paper_twin_non_countable"] is True
        assert r["paper_twin_non_countable_reason"].startswith("LIVE_TWIN_FORCED_CLOSE:"), r
        out = lv.build([chain], {"c1": twin})
        assert out["paper_twin_non_countable_ids"] == ["c1"] and out["n_non_countable_pairs"] == 1


def test_k3_pair_rows_always_carry_non_countable():
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 101.0, "exit_reason": "TAKE_PROFIT"}
    chain = {"correlation_id": "c2", "fill_price": 100.1, "fill_at_ts": 10.0, "exit_price": 100.9,
             "exit_reason": "EXCHANGE_STOP_35BP", "closed": True}
    r = lv.pair_row("c2", chain, twin)
    assert r["non_countable"] is False and r["non_countable_reasons"] is None
    assert r["paper_twin_non_countable"] is False and r["live_non_countable"] is False
    # a paper twin that is itself forced / non-countable makes the pair non-countable
    r = lv.pair_row("c2", chain, dict(twin, exit_reason="DEPLOY_FORCE_FLAT"))
    assert r["non_countable"] is True and r["paper_twin_non_countable"] is True
    r = lv.pair_row("c2", chain, dict(twin, non_countable=True, non_countable_reason="R17_UNPROVEN:LIQ_UNVERIFIED"))
    assert r["non_countable"] is True and r["non_countable_reasons"] == ["PAPER_NON_COUNTABLE:R17_UNPROVEN:LIQ_UNVERIFIED"]
    # paper-only row (no live chain): only the paper leg decides
    r = lv.pair_row("c3", None, twin)
    assert r["non_countable"] is False and "non_countable" in r


def test_k3_forced_set_mirrors_ts_and_paper_canon():
    import paper_pnl_canon
    assert paper_pnl_canon.FORCED_EXIT_REASONS <= lv.LIVE_FORCED_EXIT_REASONS
    ts = (ROOT.parents[1] / "apps/api/src/trading-agents/live-row-countability.ts").read_text()
    for r in lv.LIVE_FORCED_EXIT_REASONS:
        assert f"'{r}'" in ts, r
