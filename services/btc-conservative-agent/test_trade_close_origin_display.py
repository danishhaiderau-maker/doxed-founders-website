"""Trades table: close origin, paper-fill truth, and analyzer exclusion labels."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

from research import close_origin as co
from research import decision_view as dv
from research.dual_execution_truth import split_execution_truth


def _closed(trade_id, exit_reason, **extra):
    row = {"trade_id": trade_id, "entry": 86081.0, "exit": 86078.0, "exit_reason": exit_reason,
           "research_lane": "FAMILY_TREND_FADE_60", "pnl_accounting_schema": "terminal_single_count_v1"}
    row.update(extra)
    return row


def test_pause_owner_maps_to_close_origin():
    assert co.close_origin_for_pause_owner("DEPLOY_MAINTENANCE") == "DEPLOY_MAINTENANCE"
    assert co.close_origin_for_pause_owner("SAFETY") == "SAFETY"
    for owner in ("OPERATOR", "UNATTRIBUTED_MANUAL", None, ""):
        assert co.close_origin_for_pause_owner(owner) == "OPERATOR"


def test_deploy_flatten_is_filled_paper_only_and_excluded():
    row = _closed("ftf-new-1", "ADMIN_MANUAL_CLOSE", close_origin="DEPLOY_MAINTENANCE")
    disp = co.trade_display(row, split_execution_truth(row))
    assert disp["paper_fill"] == {"code": "FILLED_CLOSED", "label": "Filled, then closed"}
    assert disp["bitfinex_copy"]["code"] == "NOT_COPIED"
    assert disp["bitfinex_copy"]["label"] == "Paper only, not copied to Bitfinex"
    assert disp["bitfinex_copy"]["relationship"] == "SHOWCASE_ONLY"
    assert disp["close"]["code"] == "DEPLOY_BOUNDARY"
    assert disp["close"]["label"] == "Deploy boundary (paper force-flat)"
    assert "evidence_url" not in disp["close"]
    assert disp["analyzer"] == {"included": False, "code": "EXCLUDED_FORCED_EXIT",
                                "label": "Excluded: deploy force-flat"}


def test_legacy_rows_use_the_deploy_run_attestation():
    row = _closed("ftl-94b81db91b0a", "ADMIN_MANUAL_CLOSE")
    close = co.trade_display(row, split_execution_truth(row))["close"]
    assert close["code"] == "DEPLOY_BOUNDARY"
    assert close["evidence_url"].endswith("/actions/runs/36974300808")
    assert set(co.LEGACY_DEPLOY_FLATTEN_ATTESTATIONS) >= {
        "ftf-928c9bcd7bd5", "ftl-94b81db91b0a", "ftf-a721a6929cfb",
        "ftl-93598246300f", "ftl-d3f2eb57a2f6",
    }


def test_operator_and_unattributed_forced_closes_stay_distinct():
    operator = _closed("ftf-op", "ADMIN_MANUAL_CLOSE", close_origin="OPERATOR")
    unknown = _closed("ftf-unknown", "ADMIN_MANUAL_CLOSE")
    op = co.trade_display(operator, split_execution_truth(operator))
    un = co.trade_display(unknown, split_execution_truth(unknown))
    assert op["close"]["label"] == "Manual close (operator)"
    assert op["analyzer"]["label"] == "Excluded: manual close"
    assert un["close"]["code"] == "FORCED_UNATTRIBUTED"
    assert un["analyzer"]["included"] is False


def test_strategy_exit_is_included_and_keeps_its_own_label():
    row = _closed("ftf-path", "PATH_END_60M")
    disp = co.trade_display(row, split_execution_truth(row))
    assert disp["close"] == {"code": "STRATEGY_EXIT", "label": None, "origin": None}
    assert disp["analyzer"] == {"included": True, "code": "INCLUDED", "label": "Included"}


def test_close_origin_never_reclassifies_strategy_exits():
    row = _closed("ftf-path", "PATH_END_60M", close_origin="DEPLOY_MAINTENANCE")
    assert co.resolve_close_origin(row) is None
    assert co.trade_display(row)["analyzer"]["included"] is True


def test_copied_trade_reports_the_bitfinex_copy():
    row = _closed("ftf-copy", "PATH_END_60M",
                  copy_fill_observed={"classification": "FILLED", "fill_ids": [1]})
    disp = co.trade_display(row, split_execution_truth(row))
    assert disp["bitfinex_copy"]["code"] == "COPIED"
    assert disp["bitfinex_copy"]["label"] == "Copied to Bitfinex (FILLED)"


def test_forced_exit_origin_counts():
    rows = [
        _closed("ftf-928c9bcd7bd5", "ADMIN_MANUAL_CLOSE"),
        _closed("a", "ADMIN_MANUAL_CLOSE", close_origin="DEPLOY_MAINTENANCE"),
        _closed("b", "ADMIN_MANUAL_CLOSE", close_origin="OPERATOR"),
        _closed("c", "ADMIN_MANUAL_CLOSE"),
        _closed("d", "PATH_END_60M"),
    ]
    assert co.forced_exit_origin_counts(rows) == {
        "DEPLOY_MAINTENANCE": 2, "OPERATOR": 1, "SAFETY": 0, "UNRECORDED": 1,
    }


def test_analyzer_excludes_forced_exits_and_splits_them_by_origin():
    import analyzer_research_engine_v62 as engine

    frame = pd.DataFrame([
        _closed("s1", "PATH_END_60M", net_pnl_usd=0.02),
        _closed("s2", "PHYSICAL_HARD_STOP_40PCT", net_pnl_usd=-0.10),
        _closed("ftf-928c9bcd7bd5", "ADMIN_MANUAL_CLOSE", net_pnl_usd=0.001),
        _closed("n1", "ADMIN_MANUAL_CLOSE", net_pnl_usd=-0.03, close_origin="DEPLOY_MAINTENANCE"),
        _closed("n2", "ADMIN_MANUAL_CLOSE", net_pnl_usd=-0.01, close_origin="OPERATOR"),
    ])
    stats = engine._lane_closed_trade_stats(frame)
    assert stats["n"] == 2 and stats["n_all_closes"] == 5
    assert stats["forced_exits_excluded"] == 3
    assert stats["forced_exits_by_origin"] == {"DEPLOY_MAINTENANCE": 2, "OPERATOR": 1}
    ev = dv.after_cost_ev({"closed_trade_stats": stats})
    assert ev["n_source"] == ("strategy exits (3 forced exits excluded from EV: "
                              "2 deploy-boundary force-flats, 1 operator close)")


def test_bot_stamps_close_origin_and_publishes_public_safe_display():
    import bot

    saved = {k: bot.state.get(k) for k in ("manual_admin_pause", "pause_intent", "execution_paused")}
    try:
        bot.state.update(manual_admin_pause=True, pause_intent="DEPLOY_MAINTENANCE", execution_paused=True)
        assert bot._forced_close_origin("ADMIN_MANUAL_CLOSE") == "DEPLOY_MAINTENANCE"
        assert bot._forced_close_origin("PATH_END_60M") is None
        bot.state.update(pause_intent="OPERATOR")
        assert bot._forced_close_origin("ADMIN_MANUAL_CLOSE") == "OPERATOR"
    finally:
        bot.state.update(saved)

    raw = [_closed("ftf-928c9bcd7bd5", "ADMIN_MANUAL_CLOSE", pnl=0.35, ts_melbourne="2026-10-02 16:42:01 AEST")]
    enriched = bot._enrich_dashboard_trade_rows(raw, split_execution_truth, {})
    assert enriched[0]["trade_display"]["close"]["code"] == "DEPLOY_BOUNDARY"
    public = bot._sanitize_public_state({"trades": enriched})["trades"][0]
    assert "exit_reason" not in public and "research_lane" not in public
    assert public["trade_display"]["paper_fill"]["code"] == "FILLED_CLOSED"
    assert public["trade_display"]["analyzer"]["included"] is False


def test_close_endpoint_and_trade_row_carry_close_origin():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py"), encoding="utf-8").read()
    close_src = src[src.index("def close_position(pos: dict, exit_reason: str):"):]
    close_src = close_src[:close_src.index("\ndef ", 1)]
    assert '"exit_reason": exit_reason,' in close_src
    assert '"close_origin": _forced_close_origin(exit_reason),' in close_src
    endpoint = src[src.index("def api_close_showcase_position"):src.index("@app.route('/api/toggle_early_fail")]
    assert 'close_position(matches[0], "ADMIN_MANUAL_CLOSE")' in endpoint
    assert '"close_origin": close_origin,' in endpoint


def test_trades_table_headers_explain_fill_copy_and_stats():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py"), encoding="utf-8").read()
    for header in (">Paper fill</th>", ">Bitfinex copy</th>", ">Analyzer stats</th>"):
        assert header in src
    assert "<th>Relationship</th>" not in src
    assert 'id="tradesTableLegend"' in src
    assert "orders that never filled are listed under Expired Orders" in src
    assert "t.trade_display" in src