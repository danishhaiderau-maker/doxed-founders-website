import trade_research_tags as t


def test_tags_and_path_are_record_only():
    t._HISTORY.clear(); t._last_sample = 0
    for i in range(400):
        t._HISTORY.append((1.79e9 + i * 10, 80000.0 + i * 0.1))
    now = 1.79e9 + 400 * 10
    pos = {"dir": "LONG", "fill_ts": now - 5, "signal_ts": now - 7}
    t.tick(80040.0, [pos], now=now)
    tags = pos["research_tags"]
    assert tags["gs07_with_1h_move"] is True and tags["snapback_same_dir"] is None
    assert tags["signal_to_fill_sec"] == 2.0
    assert pos["price_path_10s"] == [[5.0, 80040.0]]
    out = t.outcome_fields(pos)
    assert out["price_path_10s"] and out["research_tags"]["move_1h_bp"] > 3


def test_never_raises_on_garbage():
    t._last_sample = 0
    t.tick("x", [None, {"dir": object()}])
    t.tick(1.0, None, now=1e10)
