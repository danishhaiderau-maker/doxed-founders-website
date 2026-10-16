"""Lanes 1-5 migration (Danish 16 Oct 17:01): every tile prices through paper_fill; CI guards."""
import ast
import pathlib

import combo_pathway_config as cpc
import paper_fill as pf
import tile_fill as tf

ROOT = pathlib.Path(__file__).parent
BOOK = {"s": 100, "a": [[100.0, 1.0], [101.0, 5.0]], "b": [[99.0, 1.0], [98.0, 5.0]]}


def _use_book(monkeypatch, book=BOOK):
    monkeypatch.setattr(pf, "book10l_at", lambda t: {**book, "s": int(t) - 1} if book else None)


def test_roster_completeness_and_empty_pending():
    registry = set(cpc.ACTIVE_TILE_REGISTRY)
    assert registry <= set(pf.TILE_FILL_SITES), registry - set(pf.TILE_FILL_SITES)
    assert not pf.PENDING_MIGRATION
    assert tf.MIGRATED_LANES <= registry
    for lane in tf.MIGRATED_LANES:
        assert pf.TILE_FILL_SITES[lane] == (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"))


def test_every_fill_site_in_roster_passes_checker_and_book_is_positional():
    for lane, sites in pf.TILE_FILL_SITES.items():
        for module, func in sites:
            src = (ROOT / module).read_text(encoding="utf-8")
            assert pf.fill_site_violations(src, func) == [], (lane, func)
            fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == func)
            call = fn.body[-1].value
            assert len(call.args) == 5 and "book_at" not in {k.arg for k in call.keywords}
            assert {k.arg for k in call.keywords} <= pf.ALLOWED_FILL_KWARGS


def test_tile_fill_module_scan_clean_and_no_legacy_models():
    src = (ROOT / "tile_fill.py").read_text(encoding="utf-8")
    assert pf.tile_module_violations(src, "tile_fill.py", ("price_entry", "price_exit")) == []
    for bad in ("REALISTIC_V1", "TRADE_THROUGH", "QUEUE_CONSUMED", "state[", "book_tape_1s", "ticker"):
        if bad != "ticker":
            assert bad not in src
    assert pf.ALLOWED_FILL_MODELS == {pf.FILL_MODEL}


def test_checker_rejects_planted_kwargs_and_book_keyword():
    planted = ("import paper_fill\ndef price_entry(o):\n"
               "    return paper_fill.paper_fill(o['side'], o['qty'], o['signal_ts'], o['delay_s'], "
               "book_at=paper_fill.book10l_at, leg='entry')\n")
    v = pf.fill_site_violations(planted, "price_entry")
    assert any("book_at" in x for x in v)


def test_entry_is_ioc_at_signal_plus_delay_with_provenance(monkeypatch):
    _use_book(monkeypatch)
    r = tf.entry_fill("LONG", 0.5, 99.0, 1.5, "default_until_n10")
    assert r["status"] == "FILLED" and r["price"] == 100.0 and r["fill_ts"] == 100.5
    assert r["fill_model"] == pf.FILL_MODEL and r["fill_model_version"] == 1
    assert r["delay_source"] == "default_until_n10" and r["depth_snapshot_fill"]["a"][0] == [100.0, 1.0]
    p = tf.provenance(r)
    assert p["fee_model"] == tf.FEE_MODEL and p["depth_snapshot_fill"]


def test_entry_cap_blocks_deep_levels(monkeypatch):
    _use_book(monkeypatch)
    r = tf.entry_fill("LONG", 3.0, 99.0, 1.5, "x")  # cap = 100.05: only the 100.0 level is eligible
    assert r["status"] == "PARTIAL" and r["filled_qty"] == 1.0
    s = tf.entry_fill("SHORT", 0.5, 99.0, 1.5, "x")
    assert s["status"] == "FILLED" and s["price"] == 99.0


def test_no_book_means_no_fill_never_ticker(monkeypatch):
    _use_book(monkeypatch, book=None)
    for r in (tf.entry_fill("LONG", 1, 99.0, 1.5, "x"), tf.exit_fill("LONG", 1, 99.0, 1.5, "x")):
        assert r["status"] == "REJECT_NO_BOOK" and r["price"] is None


def test_exit_priced_from_book_other_side(monkeypatch):
    _use_book(monkeypatch)
    assert tf.exit_fill("LONG", 0.5, 99.0, 1.5, "x")["price"] == 99.0
    assert tf.exit_fill("SHORT", 0.5, 99.0, 1.5, "x")["price"] == 100.0


def test_row_provenance_guard_and_legacy_tag():
    good = {"entry_provenance": {"fill_model": pf.FILL_MODEL, "fill_model_version": 1, "delay_source": "d",
                                 "depth_snapshot_fill": {"s": 1}},
            "exit_provenance": {"fill_model": pf.FILL_MODEL, "fill_model_version": 1, "delay_source": "d",
                                "depth_snapshot_fill": {"s": 2}}}
    assert tf.row_provenance_complete(good)
    bad = {**good, "exit_provenance": {"fill_model": pf.FILL_MODEL}}
    assert not tf.row_provenance_complete(bad)
    assert pf.row_fresh_eligible({"research_lane": "X", "fill_model_tag": pf.LEGACY_TAG}) is False
    assert pf.row_fresh_eligible({"research_lane": "X", "fresh_eligible": False}) is False
    assert pf.row_fresh_eligible({"research_lane": "X", "fresh_eligible": True}) is True


def test_bot_hooks_route_lanes_1_to_5_only():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    def seg(name):
        return ast.get_source_segment(src, next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name))
    assert "_paper_lane_entry_result" in seg("resolve_sim_fill_with_depth")
    assert "_paper_lane_exit" in seg("resolve_sim_exit_price")
    assert "_paper_lane_entry_result" in seg("_pending_limit_ready_for_fill")
    assert "paper_ioc_no_fill" in seg("process_pending_orders")
    assert "tile_fill" in seg("_is_paper_book_lane") and "MIGRATED_LANES" in seg("_is_paper_book_lane")


def test_epoch_filter_and_legacy_tagging(monkeypatch):
    monkeypatch.setattr(pf, "FRESH_EPOCH_TS", 1000.0)
    ok = {"research_lane": "X", "exit_reason": "TP", "fill_model_version": 1, "entry_ts": 1500.0}
    assert pf.row_fresh_eligible(ok) is True
    assert pf.row_fresh_eligible({**ok, "entry_ts": 900.0}) is False  # pre-epoch
    legacy = {"research_lane": "X", "exit_reason": "TP", "entry_ts": 1500.0}  # no fill_model_version
    t = pf.tag_legacy(legacy)
    assert t["legacy_pricing"] is True and t["fill_model_tag"] == pf.LEGACY_TAG
    assert pf.row_fresh_eligible(legacy) is False


def test_vwap_and_leg_hooks_in_bot():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    ns = {}
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_exit_price_vwap")
    exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    pos = {"partial_exit_receipts": [{"closed_qty": 1.0, "price": 100.0, "realized_gross_usd": 1.0},
                                     {"closed_qty": 9, "price": 1, "realized_gross_usd": None}]}
    assert ns["_exit_price_vwap"](pos, 110.0, 1.0) == 105.0
    assert "_paper_lane_partial_leg" in src and "leg_trigger_ts" in src and "leg_fill_ts" in src


def test_fee_model_and_funding_fields():
    assert tf.FEE_MODEL == "NO_TRADING_FEE, funding only"
    assert tf.provenance({})["fee_model"] == tf.FEE_MODEL
    f = tf.funding_fields(7 * 3600, 17 * 3600, 0.0001)  # crosses 08:00 and 16:00
    assert f == {"funding_rate": 0.0001, "hold_seconds": 36000.0, "funding_ts_crossed": [8 * 3600, 16 * 3600]}
    assert tf.funding_fields(100, 200, 0.0)["funding_ts_crossed"] == []
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "funding_fields(pos.get(\"entry_ts\")" in src and "BITFINEX_FEE_RATES" not in src.split("def close_position")[1][:20000]
