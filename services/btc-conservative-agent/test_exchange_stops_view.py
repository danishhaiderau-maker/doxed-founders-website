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
