"""#722 follow-up: /tiles/trades rows carry a separate identity_quarantined flag; no fresh counter counts them."""
import monitor_fill_provenance as mfp
import monitor_reconcile as mrc
import monitor_tiles as mt
import fills_by_model as fm


def _row(trade_id, fresh=True):
    return {"trade_id": trade_id, "status": "CLOSED", "lane": "FAMILY_GSB1_CVD_DIV_REGIME",
            "identity_quarantined": trade_id in fm.IDENTITY_QUARANTINED_TRADE_IDS,
            "pricing": {"fresh_eligible": fresh, "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1}}


def test_tile_trade_row_flags_quarantined_trade_without_touching_fresh_eligible():
    raw = {"trade_id": "gb1-746896eb658e", "research_lane": "FAMILY_GSB1_CVD_DIV_REGIME"}
    row = mt.tile_trade_row(raw, status="closed", short_names={}, epoch_id="e", forced_reasons=[])
    assert row["identity_quarantined"] is True
    ok = mt.tile_trade_row({"trade_id": "gb1-clean", "research_lane": "FAMILY_GSB1_CVD_DIV_REGIME"},
                           status="closed", short_names={}, epoch_id="e", forced_reasons=[])
    assert ok["identity_quarantined"] is False
    assert mt.pricing_block({"trade_id": "gb1-746896eb658e"}).get("fresh_eligible") is not None  # untouched key


def test_fresh_only_filter_and_reconcile_never_count_a_quarantined_trade():
    bad, good = _row("gb1-746896eb658e"), _row("gb1-clean")
    assert mt.row_is_fresh(good) is True and mt.row_is_fresh(bad) is False
    out = mrc.build({"FAMILY_GSB1_CVD_DIV_REGIME": [bad, good]}, {}, None, {})
    assert out["per_tile"][0]["fly_n"] == 2 and out["per_tile"][0]["fly_fresh_n"] == 1
