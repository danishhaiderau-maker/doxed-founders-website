import live_copy_lifecycle as lcl


def _size(p):
    return p.get("size")


def test_relay_copy_links_position_and_stop():
    j = {"gs1-x": [
        {"type": "ORDER_PLACED", "order": {"exchange_order_id": 111, "side": "SHORT"}},
        {"type": "ORDER_FILLED", "fill": {"qty": 0.00029}, "order": {"side": "SHORT"}},
        {"type": "STOP_CONFIRMED", "stop": {"exchange_order_id": 222}},
    ], "gs1-closed": [
        {"type": "ORDER_FILLED", "fill": {"qty": 0.00029}},
        {"type": "STOP_CONFIRMED", "stop": {"exchange_order_id": 333}},
        {"type": "POSITION_CLOSED"},
    ]}
    o, p, links = lcl.relay_copy_managed_ids(j, [{"id": 194459792, "size": -0.00029}], _size)
    assert o == {"111", "222"} and p == {"194459792"}
    assert links["gs1-x"]["position_id"] == "194459792"


def test_relay_copy_no_link_on_qty_or_side_mismatch():
    j = {"a": [{"type": "ORDER_FILLED", "fill": {"qty": 0.00029}, "order": {"side": "SHORT"}}]}
    assert lcl.relay_copy_managed_ids(j, [{"id": 1, "size": 0.00029}], _size)[1] == set()
    assert lcl.relay_copy_managed_ids(j, [{"id": 1, "size": -0.001}], _size)[1] == set()
