from live_copy_lifecycle import relay_copy_managed_ids

def _size(p): return p["amount"]

def test_stale_unclosed_copy_does_not_orphan_the_live_position():
    by = {
        "gs1-old": [{"type": "ORDER_FILLED", "order": {"exchange_order_id": "1", "qty": 0.00029, "side": "SHORT"},
                     "fill": {"qty": 0.00029, "side": "SHORT"}}],
        "gs1-446291153ae7": [{"type": "ORDER_FILLED", "order": {"exchange_order_id": "246297801048", "qty": 0.00029, "side": "LONG"},
                              "fill": {"qty": 0.00029, "side": "LONG"}},
                             {"type": "STOP_PLACED", "stop": {"exchange_order_id": "246284262544", "qty": 0.00029}}],
    }
    o, p, links = relay_copy_managed_ids(by, [{"id": "194465029", "amount": 0.00029}], _size)
    assert p == {"194465029"} and "246284262544" in o
    assert links["gs1-446291153ae7"]["position_id"] == "194465029"

def test_unmatched_size_stays_orphan():
    by = {"a": [{"type": "ORDER_FILLED", "order": {"exchange_order_id": "1", "qty": 0.001, "side": "LONG"}, "fill": {"qty": 0.001, "side": "LONG"}}]}
    assert relay_copy_managed_ids(by, [{"id": "9", "amount": 0.00029}], _size)[1] == set()
