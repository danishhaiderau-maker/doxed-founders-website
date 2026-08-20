from microstructure_tape import build_bucket, validate_window, window_reference
from pathlib import Path

BOT = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
COLLECTOR = (Path(__file__).resolve().parent / "collector_v22.py").read_text(encoding="utf-8")


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


def test_window_bucket_index_preserves_duplicate_and_stale_checks():
    ref = window_reference(100, 103)
    indexed = {
        ts: [build_bucket(bucket_ts=ts, bid=99, ask=101, bid_qty=1,
                          ask_qty=1, last=100, source_ts=ts + .5)]
        for ts in range(100, 103)
    }
    assert validate_window(indexed, ref)["eligible"] is True
    indexed[101].append(dict(indexed[101][0]))
    duplicate = validate_window(indexed, ref)
    assert duplicate["eligible"] is False
    assert duplicate["duplicate_buckets"] == [101]
    indexed[101] = [dict(indexed[101][0], fresh=False)]
    stale = validate_window(indexed, ref)
    assert stale["eligible"] is False
    assert stale["invalid_or_stale_buckets"] == [101]


def test_runtime_capture_is_research_only_and_started_once():
    start = BOT.index("def microstructure_capture_loop():")
    end = BOT.index("\ndef ", start + 5)
    body = BOT[start:end]
    for forbidden in ("create_order", "submit_order", "close_position", "process_pending_orders"):
        assert forbidden not in body
    assert BOT.count("target=safe_thread(microstructure_capture_loop)") == 1
    assert 'state["bid_qty"] = bid_qty' in BOT
    assert 'state["ask_qty"] = ask_qty' in BOT
    assert 'float(state.get("rest_price_ts") or state.get("rest_last_tick") or 0.0)' in BOT
    assert "MICROSTRUCTURE_TAPE_FILE" in BOT


def test_v22_event_references_the_shared_required_window_without_claiming_coverage():
    assert '"microstructure_window": microstructure_window_reference(' in COLLECTOR
    assert "signal_ts, required_end_ts" in COLLECTOR
