"""REALISM_V2 PR2: order-type model (exchange-resident stop vs bot-sent exits, entry walk / maker rest / NO_FILL)."""
import paper_fill
import order_model as om
import tile_fill


def _book(s, bid=100.0, ask=100.1, size=1.0):
    return {"s": s, "b": [[bid, size], [bid - 0.1, size]], "a": [[ask, size], [ask + 0.1, size]]}


# ---------------------------------------------------------------- exits
def test_exit_order_class_only_exchange_stop_is_resident():
    assert om.exit_order_class("EXCHANGE_STOP_35BP") == om.EXCHANGE_RESIDENT
    for r in ("PHYSICAL_HARD_STOP_10PCT", "GS_ATR_TAKE_PROFIT", "GS_LADDER_TP1", "PATH_END_30M", "MANUAL_CLOSE",
              "GS_BREAKEVEN_LOCK", "TILE_RETIRED"):
        assert om.exit_order_class(r) == om.BOT_SENT


def test_exchange_stop_level_anchored_at_entry_fill_live_code(monkeypatch):
    import live_copy_control as lcc
    monkeypatch.setattr(lcc, "exchange_stop_plan", lambda spec: {"exchange_stop_bp": 35.0, "hard_stop_bp": 10.0,
                                                                 "cap_applied": False})
    lv = om.exchange_stop_level("LONG", 100000.0, {"x": 1})
    assert abs(lv["exchange_stop_px"] - 99650.0) < 1e-6
    assert lv["exchange_stop_param_source"] == "LIVE_CODE" and lv["exchange_stop_never_moved"] is True
    sv = om.exchange_stop_level("SHORT", 100000.0, {"x": 1})
    assert abs(sv["exchange_stop_px"] - 100350.0) < 1e-6
    assert "ASSUMED" not in str(lv)


def test_exchange_stop_vs_hard_stop_ordering():
    # resident stop triggers before the bot hard-stop order would fill -> resident wins
    assert om.race(100.5, 100.0, 1.5) == om.EXCHANGE_RESIDENT
    # tie at the bot fill instant -> resident (already on the exchange)
    assert om.race(101.5, 100.0, 1.5) == om.EXCHANGE_RESIDENT
    # resident triggers after the bot order filled -> bot wins
    assert om.race(102.0, 100.0, 1.5) == om.BOT_SENT
    assert om.race(None, 100.0, 1.5) == om.BOT_SENT
    assert om.race(99.0, None, 1.5) == om.EXCHANGE_RESIDENT


def test_resident_exit_fill_has_no_delay(monkeypatch):
    books = {100: _book(100, bid=99.0, ask=99.1, size=5.0), 101: _book(101, bid=90.0, ask=90.1, size=5.0)}
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: books.get(int(t) - 1))
    res = tile_fill.exit_resident_fill("LONG", 1.0, 101.0)
    assert res["delay_s"] == 0.0 and res["fill_ts"] == 101.0 and res["delay_source"] == "EXCHANGE_RESIDENT_NO_DELAY"
    assert res["price"] == 99.0 and res["leg"] == "exit_resident"
    bot = tile_fill.exit_fill("LONG", 1.0, 101.0, 1.0, "LIVE_POOLED")
    assert bot["delay_s"] == 1.0 and bot["fill_ts"] == 102.0 and bot["price"] == 90.0


# ---------------------------------------------------------------- entries
def test_entry_model_from_spec_and_cell():
    spec = {"entry_policy": {"mode": "TAKER_AT_SIGNAL", "taker_protection_bps": 5.0, "taker_ttl_sec": 3}}
    m = om.entry_model(spec)
    assert m["entry_order_model"] == om.ENTRY_WALK_THEN_REST and m["entry_protection_bp"] == 5.0
    assert m["entry_rest_ttl_s"] == 3.0 and m["entry_param_source"] == "CONFIG"
    mk = om.entry_model({"entry_policy": {"mode": "REGIME_ADAPTIVE"}}, {"action": "MAKER"},
                        {"kind": "PASSIVE", "ttl_sec": 1200, "reprice_sec": 60})
    assert mk["entry_order_model"] == om.ENTRY_MAKER_REST and mk["entry_queue_reset_on_reprice"] is True
    io = om.entry_model({"entry_policy": {"mode": "TAKER_MARKET_AT_REAL_TOUCH_SELF_SIMULATED"}})
    assert io["entry_order_model"] == om.ENTRY_IOC_MARKET


def test_maker_trade_through_fills_at_limit_capped_by_print():
    st = om.maker_rest_step("LONG", 100.0, 2.0, 0.0, [{"ts": 1, "px": 100.5, "amt": -3},
                                                      {"ts": 2, "px": 99.9, "amt": -0.5},
                                                      {"ts": 3, "px": 99.8, "amt": -5}])
    assert st["fills"] == [(2, 100.0, 0.5), (3, 100.0, 1.5)] and st["remaining"] == 0.0


def test_maker_queue_depletes_before_fill_and_only_aggressor_side():
    st = om.maker_rest_step("LONG", 100.0, 1.0, 2.0, [{"ts": 1, "px": 100.0, "amt": 1.5},   # buy aggressor: no
                                                      {"ts": 2, "px": 100.0, "amt": -1.5},  # queue 2 -> 0.5
                                                      {"ts": 3, "px": 100.0, "amt": -0.8}])  # 0.5 queue, 0.3 us
    assert st["queue_ahead"] == 0.0 and len(st["fills"]) == 1 and abs(st["fills"][0][2] - 0.3) < 1e-12
    assert abs(st["remaining"] - 0.7) < 1e-12
    sh = om.maker_rest_step("SHORT", 100.0, 1.0, 0.0, [{"ts": 1, "px": 100.0, "amt": -1}, {"ts": 2, "px": 100.2, "amt": 2}])
    assert sh["fills"] == [(2, 100.0, 1.0)]


def test_entry_partial_walk_then_rest_and_no_fill():
    model = {"entry_order_model": om.ENTRY_WALK_THEN_REST}
    walk = {"status": "PARTIAL", "price": 100.0, "filled_qty": 1.0, "fill_ts": 10.0, "cap_px": 100.05}
    out = om.combine_entry(walk, [(11.0, 100.05, 0.5)], 2.0, model=model, rest_end_ts=13.0, rest_end_reason="TTL_CANCEL")
    assert out["status"] == "PARTIAL" and abs(out["entry_unfilled_qty"] - 0.5) < 1e-12
    assert abs(out["price"] - (100.0 + 100.05 * 0.5) / 1.5) < 1e-9 and out["fill_ts"] == 11.0
    none = om.combine_entry({"status": "NO_FILL_IOC_CAP", "price": None, "filled_qty": 0.0, "fill_ts": 10.0}, [], 2.0,
                            model=model, rest_end_ts=13.0, rest_end_reason="TTL_CANCEL")
    assert none["status"] == om.NO_FILL and none["filled_qty"] == 0 and none["entry_unfilled_qty"] == 2.0
    assert none["price"] is None and none["no_fill_reason"] == "TTL_CANCEL"
    full = om.combine_entry({"status": "NO_FILL_IOC_CAP", "price": None, "filled_qty": 0.0, "fill_ts": 10.0},
                            [(12.0, 100.05, 2.0)], 2.0, model=model, rest_end_ts=13.0, rest_end_reason="TTL_CANCEL")
    assert full["status"] == "FILLED" and full["price"] == 100.05


def test_no_maker_take_profit_anywhere():
    import gs_regime_exit_stack as stack
    import combo_pathway_config as cfg
    import inspect
    assert "MAKER_LIMIT_AT_TARGET" not in inspect.getsource(cfg)
    assert '"maker": True' not in inspect.getsource(stack)
