from microstructure_tape import build_bucket, validate_window, window_reference


def test_bucket_aggregates_side_volume_and_binds_hash():
    row = build_bucket(
        bucket_ts=100, bid=99, ask=101, bid_qty=0.5, ask_qty=0.4,
        last=100, source_ts=100.5,
        trades=[
            {"received_ts": 100.2, "p": 101, "v": .2, "S": "Buy"},
            {"received_ts": 100.7, "p": 99, "v": .1, "S": "Sell"},
            {"received_ts": 101.1, "p": 101, "v": 9, "S": "Buy"},
        ],
    )
    assert row["fresh"] is True
    assert row["trade_count"] == 2
    assert row["buy_qty"] == .2 and row["sell_qty"] == .1
    assert len(row["row_sha256"]) == 64


def test_invalid_or_stale_bbo_is_preserved_as_negative_evidence():
    crossed = build_bucket(bucket_ts=100, bid=102, ask=101, bid_qty=1, ask_qty=1, last=101, source_ts=100)
    stale = build_bucket(bucket_ts=100, bid=99, ask=101, bid_qty=1, ask_qty=1, last=100, source_ts=90)
    assert crossed["valid_bbo"] is False and crossed["fresh"] is False
    assert stale["valid_bbo"] is True and stale["fresh"] is False


def test_window_requires_every_unique_fresh_second():
    ref = window_reference(100, 103)
    rows = [
        build_bucket(bucket_ts=ts, bid=99, ask=101, bid_qty=1, ask_qty=1, last=100, source_ts=ts + .5)
        for ts in range(100, 103)
    ]
    assert validate_window(rows, ref)["eligible"] is True
    assert validate_window(rows[:-1], ref)["eligible"] is False
    assert validate_window(rows + [rows[0]], ref)["eligible"] is False
