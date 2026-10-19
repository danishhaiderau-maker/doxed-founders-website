import exchange_stops_view as v

STOP = {"id": "11", "symbol": "tBTCF0:USTF0", "type": "STOP", "amount": -0.001, "price": 79000.0, "status": "ACTIVE", "reduce_only": True}


def mirror(**k):
    base = {"ok": True, "read_age_s": 2.0, "positions": [], "open_orders": [], "stops": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}
    base.update(k)
    return base


def test_long_with_covering_reduce_only_stop_is_protected():
    pos = {"symbol": "tBTCF0:USTF0", "side": "LONG", "amount": 0.001, "base_price": 80000.0}
    r = v.build(mirror(positions=[pos], stops=[STOP], open_orders=[STOP]))
    assert r["verdict"] == "PROTECTED" and r["positions"][0]["stop_ids"] == ["11"] and r["fresh"]


def test_missing_wrong_side_non_reduce_only_or_partial_stop_is_unprotected():
    pos = {"symbol": "tBTCF0:USTF0", "side": "LONG", "amount": 0.001}
    assert v.build(mirror(positions=[pos]))["verdict"] == "UNPROTECTED_POSITION"
    assert v.build(mirror(positions=[pos], stops=[dict(STOP, amount=0.001)]))["verdict"] == "UNPROTECTED_POSITION"
    assert v.build(mirror(positions=[pos], stops=[dict(STOP, reduce_only=False)]))["verdict"] == "UNPROTECTED_POSITION"
    assert v.build(mirror(positions=[pos], stops=[dict(STOP, amount=-0.0004)]))["verdict"] == "UNPROTECTED_POSITION"


def test_short_needs_a_buy_stop_and_flat_states_and_unknown_mirror():
    pos = {"symbol": "tBTCF0:USTF0", "side": "SHORT", "amount": -0.001}
    assert v.build(mirror(positions=[pos], stops=[dict(STOP, amount=0.001)]))["verdict"] == "PROTECTED"
    assert v.build(mirror())["verdict"] == "FLAT_NO_STOPS"
    assert v.build(mirror(stops=[STOP], orphans=["11"]))["verdict"] == "FLAT_WITH_STOP_ORPHANS"
    assert v.build({"ok": False, "error": "credentials-missing"})["verdict"] == "UNKNOWN_MIRROR_NOT_OK"
    assert v.build(mirror(read_age_s=99.0))["fresh"] is False


def test_stale_read_never_gives_protected_and_shows_covered_vs_position_and_age():
    pos = {"symbol": "tBTCF0:USTF0", "side": "LONG", "amount": 0.001}
    r = v.build(mirror(positions=[pos], stops=[STOP], read_age_s=45.0))
    assert r["verdict"] == "STALE_READ_NOT_PROTECTED" and r["fresh"] is False and r["read_age_s"] == 45.0
    assert r["positions"][0]["protected"] is True and r["positions"][0]["covered_vs_position"] == "0.001 / 0.001"
    assert v.build(mirror(positions=[pos], stops=[STOP]))["verdict"] == "PROTECTED"
    assert v.build(mirror(read_age_s=45.0))["verdict"] == "STALE_READ"
    part = v.build(mirror(positions=[pos], stops=[dict(STOP, amount=-0.0004)]))
    assert part["positions"][0]["covered_vs_position"] == "0.0004 / 0.001" and part["verdict"] == "UNPROTECTED_POSITION"
    assert v.build(mirror(), relay_cap={"fly_max_active_signals": 20})["relay_cap"]["fly_max_active_signals"] == 20


def test_only_active_stop_orders_count_cancelled_executed_or_status_less_stops_do_not():
    pos = {"symbol": "tBTCF0:USTF0", "side": "LONG", "amount": 0.001}
    for st in ("CANCELED", "EXECUTED @ 80000(-0.001)", "INSUFFICIENT MARGIN", None, "POSTONLY CANCELED"):
        r = v.build(mirror(positions=[pos], stops=[dict(STOP, status=st)]))
        assert r["verdict"] == "UNPROTECTED_POSITION" and r["inactive_stops"] and r["positions"][0]["stop_amount_covered"] == 0, st
    for st in ("ACTIVE", "PARTIALLY FILLED @ 80000(-0.0005)"):
        assert v.build(mirror(positions=[pos], stops=[dict(STOP, status=st)]))["verdict"] == "PROTECTED", st
    r = v.build(mirror(), relay_cap={"HARDCODED_EXPECTATION_NOT_A_READING": {"x": 1}, "effective_value": None})
    assert r["relay_cap"]["effective_value"] is None
