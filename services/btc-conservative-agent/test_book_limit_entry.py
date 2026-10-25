"""RK R3/R4 + HM #848 C1-C3: book_limit_entry = marketable-at-placement walk (paper_fill) then a resting limit filled
only on real prints with queue (paper_fill.maker_rest_fills); countability from r17_exclusion's entry contract."""
import quote_trigger as _qt
from book_limit_entry import EVALUATOR_VERSION, ENTRY_PRICE_SOURCE_BOOK, R17_UNPROVEN_ENTRY, book_limit_entry
from book_walk_test_fixture import book_row as _book_row, entry_tape, quiet_boot_log
from research.quantity_execution import build_signed_quantity_constraints
from research_v3_book_walk import BookWalkTape as _Tape

SYMBOL = "tBTCF0:USTF0"
CONSTRAINTS = build_signed_quantity_constraints(
    symbol=SYMBOL, quantity_step="0.00000001", quantity_precision=8,
    min_lot="0.00000001", min_notional="0.000001",
    captured_at="2026-08-30T00:00:00Z", source_revision="test-revision", source="TEST_FIXTURE",
)

SCHEDULE = [{"bucket_id": "ev-1:chase:0", "start_ts": 1000.0, "end_ts": 1005.0, "limit_price": 100.0}]
MEASURED = {"delay_s": 0.5, "delay_source": "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n12"}


def _call(walk, qty=1.0, schedule=SCHEDULE, **kw):
    return book_limit_entry("LONG", qty, schedule, walk.book_at, trades_between=walk.trades_between,
                            gap_clause_at=walk.gap_clause_at, quantity_constraints=CONSTRAINTS, symbol=SYMBOL, **kw)


def _run(*, crossed=True, visible_qty=2.0, qty=1.0, **kw):
    return _call(entry_tape(visible_qty, crossed).window(990, 1010), qty, **kw)


def _trade(ms, px, amt):
    return {"s": int(ms // 1000), "tr": [[int(ms), int(ms), amt, px]]}


def _tape(row_for, trades=(), *, dense=True):
    """Book rows 980..1019 from ``row_for``; ``dense`` adds a buy print at 100.50 every second (no fill for a 100.00
    buy limit) so print gaps are short and a quiet boot log makes the C2 detector clean."""
    prints = [_trade(s * 1000 + 500, 100.5, 0.01) for s in range(980, 1020)] if dense else []
    rows = sorted(prints + list(trades), key=lambda r: r["tr"][0][1])
    return _Tape.from_rows([row_for(s) for s in range(980, 1020)], rows, receipt={"fixture": True},
                           boot_log=quiet_boot_log(900, 1100)).window(980, 1020)


def _far(s):
    return _book_row(s, 100.45, 100.5)


def test_crossed_book_fills_at_placement_and_is_unproven_without_measured_delay():
    r = _run()
    assert r["outcome"] == "FILL" and r["fill_basis"] == "MARKETABLE_AT_PLACEMENT_WALK"
    assert r["entry_price_source"] == ENTRY_PRICE_SOURCE_BOOK and r["trigger_price_source"] == "book"
    assert r["evaluator_version"] == EVALUATOR_VERSION
    assert r["fill_model"] == "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"
    assert r["fill_ts"] == 1001.5 and r["trigger_bucket_ts"] == r["fill_ts"]
    assert r["fill_price"] <= 100.0
    assert r["delay_source_kind"] == "default" and r["delay_unverified"] is True
    assert r["r17_entry_status"] == R17_UNPROVEN_ENTRY and "DELAY_NOT_MEASURED" in r["r17_entry_reason"]


def test_measured_delay_full_fill_is_countable_with_contract_fields_at_top_level():
    r = _run(**MEASURED)
    assert r["outcome"] == "FILL" and r["evidence_status"] == "COMPLETE"
    assert r["delay_source"] == "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n12" and r["delay_source_kind"] == "live_ack"
    assert r["entry_book_age_ms"] is not None and r["entry_book_age_ms"] >= 0
    assert r["r17_entry_status"] is None and r["r17_entry_failures"] == []


def test_p3_no_fill_on_default_delay_is_unproven_but_measured_quiet_no_fill_counts():
    r = _run(crossed=False)
    assert r["outcome"] == "NO_FILL" and r["r17_entry_status"] == R17_UNPROVEN_ENTRY  # HM #848 C2
    assert "DELAY_NOT_MEASURED" in r["r17_entry_reason"]
    walk = _tape(_far)
    q = _call(walk, **MEASURED)
    assert q["outcome"] == "NO_FILL" and q["evidence_status"] == "COMPLETE" and q["r17_entry_status"] is None


def test_missing_book_or_prints_is_unsupported():
    r = book_limit_entry("LONG", 1.0, SCHEDULE, None)
    assert r["outcome"] == "UNSUPPORTED" and "BOOK_WALK_UNAVAILABLE" in r["negative_reasons"]
    walk = _tape(_far)
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["outcome"] == "UNSUPPORTED" and "ENTRY_TRADES_UNAVAILABLE" in r["negative_reasons"]


def test_a_full_size_walks_the_row_at_signal_plus_delay_within_the_limit_only():
    walk = _tape(lambda s: _book_row(s, 99.85, 99.90, size=0.4) if s == 999 else _far(s))
    r = _call(walk, **MEASURED)
    assert r["signal_ts"] == 1000.0 and r["fill_ts"] == 1000.5 and r["walk_book_s"] == 999
    assert r["walk_row_rule"] == "LATEST_BOOK_ROW_AT_SIGNAL_PLUS_DELAY"
    assert r["entry_fill"]["qty"] == 1.0
    assert abs(r["fill_price"] - (0.4 * 99.90 + 0.4 * 99.95 + 0.2 * 100.00)) < 1e-9
    assert r["outcome"] == "FILL" and r["r17_entry_status"] is None


def test_a_rows_before_signal_plus_delay_are_not_walked():
    walk = _tape(lambda s: _book_row(s, 99.85, 99.90, size=0.4) if s == 999 else _far(s))
    r = _call(walk, delay_s=1.5, delay_source="POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n12")
    assert r["outcome"] == "NO_FILL" and r["attempts"][0]["walk_book_s"] == 1000


def test_p1_book_dips_through_the_limit_with_no_print_is_never_a_fill():
    # HM P1: ask 99.00 at row 1002 (after placement), L = 100, no print at/through 100 -> no fill, no improvement.
    walk = _tape(lambda s: _book_row(s, 98.95, 99.0) if s == 1002 else _far(s))
    r = _call(walk, **MEASURED)
    assert r["outcome"] == "NO_FILL" and r["filled_qty"] == 0.0
    assert r["attempts"][0]["rest_touch_count"] == 1 and r["r17_entry_status"] is None  # prints dense: observable
    assert "price_improvement_vs_limit" not in r


def test_p1_touch_inside_a_flagged_print_gap_is_unproven():
    walk = _tape(lambda s: _book_row(s, 98.95, 99.0) if s == 1002 else _far(s), dense=False)
    r = _call(walk, **MEASURED)
    assert r["outcome"] == "NO_FILL" and r["r17_entry_status"] == R17_UNPROVEN_ENTRY
    assert "REST_TOUCH_IN_TRADE_GAP:C2:GAP_OVER_60S" in r["r17_entry_reason"]


def test_p4_at_limit_prints_fill_only_after_the_queue_ahead_and_at_the_limit():
    # RK R0 (25 Oct): our bid joins 0.3 already resting at 100.00.  A print AT 100.00 never fills us; through
    # prints at 99.95: 0.2 (queue 0.3 -> 0.1), then 0.5 (0.1 more queue, 0.4 to us).  No print -> no fill.
    rows = lambda s: _book_row(s, 100.0, 100.05, size=0.3)  # noqa: E731
    walk = _tape(rows, [_trade(1002_000, 100.0, -5.0), _trade(1002_200, 99.95, -0.2), _trade(1003_300, 99.95, -0.5)])
    r = _call(walk, **MEASURED)
    assert r["fill_basis"] == "RESTING_AT_LIMIT_TRADES_QUEUE" and r["outcome"] == "PARTIAL_FILL"
    assert abs(r["filled_qty"] - 0.4) < 1e-9 and r["fill_price"] == 100.0 and r["fill_ts"] == 1003.3
    assert r["attempts"][0]["rest_queue_ahead_start"] == 0.3
    assert r["evidence_status"] == "INCOMPLETE" and r["r17_entry_status"] == R17_UNPROVEN_ENTRY  # partial never counts
    assert _call(_tape(rows), **MEASURED)["outcome"] == "NO_FILL"


def test_through_print_fills_the_rest_at_the_limit_not_the_print():
    walk = _tape(lambda s: _book_row(s, 100.0, 100.05, size=0.3), [_trade(1002_200, 99.9, -2.0)])
    r = _call(walk, **MEASURED)
    assert r["outcome"] == "FILL" and r["fill_price"] == 100.0 and r["r17_entry_status"] is None


def test_partial_walk_then_rest_on_prints():
    # 0.5 inside the limit at placement, the rest fills on a through print later: VWAP of walk + 0.5 @ L.
    walk = _tape(lambda s: _book_row(s, 99.90, 99.95, size=0.25) if s == 999 else _book_row(s, 100.0, 100.5),
                 [_trade(1003_000, 99.95, -1.0)])
    r = _call(walk, **MEASURED)
    assert r["fill_basis"] == "PLACEMENT_WALK_THEN_REST" and r["outcome"] == "FILL"
    assert abs(r["fill_price"] - (0.25 * 99.95 + 0.25 * 100.0 + 0.5 * 100.0)) < 1e-9


def test_p2_p50_delay_partial_is_excluded():
    walk = _tape(lambda s: _book_row(s, 99.90, 99.95, size=0.25) if s == 999 else _far(s))
    r = _call(walk, delay_s=0.5, delay_source="lane_p50_n12")
    assert r["outcome"] == "PARTIAL_FILL" and r["r17_entry_status"] == R17_UNPROVEN_ENTRY
    assert {"DELAY_NOT_MEASURED", "ENTRY_EVIDENCE_NOT_COMPLETE"} <= set(r["r17_entry_failures"])


def test_one_missing_book_row_in_the_schedule_is_unsupported():
    walk = _Tape.from_rows([_far(s) for s in range(980, 1020) if s != 1002], [], receipt={},
                           boot_log=quiet_boot_log(900, 1100)).window(980, 1020)
    r = _call(walk, **MEASURED)
    assert r["outcome"] == "UNSUPPORTED" and r["negative_reasons"] == ["ENTRY_BOOK_GAP"]


def test_arrival_after_interval_end_reads_no_later_row():
    seen = []
    walk = _tape(lambda s: _book_row(s, 99.85, 99.90) if s >= 1001 else _far(s))
    book_at = lambda t: seen.append(t) or walk.book_at(t)  # noqa: E731
    sched = [{"bucket_id": "b0", "start_ts": 1000.0, "end_ts": 1001.0, "limit_price": 100.0}]
    r = book_limit_entry("LONG", 1.0, sched, book_at, trades_between=walk.trades_between,
                         gap_clause_at=walk.gap_clause_at, delay_s=1.5, delay_source="POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["attempts"][0]["status"] == "ARRIVAL_AFTER_INTERVAL_END" and r["outcome"] == "NO_FILL"
    assert max(seen) <= 1001.0


def test_window_truncated_marker_is_unsupported():
    walk = _tape(_far)
    sched = [{"bucket_id": "b0", "start_ts": 1000.0, "end_ts": 1000.0 + _qt.MAX_WINDOW_S + 10, "limit_price": 100.0}]
    r = _call(walk, schedule=sched, **MEASURED)
    assert r["outcome"] == "UNSUPPORTED" and r["negative_reasons"] == ["ENTRY_BOOK_WINDOW_TRUNCATED"]


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _keys(v)


def test_c_no_fee_term_in_the_entry_price_or_receipt():
    walk = _tape(lambda s: _book_row(s, 99.85, 99.90, size=2.0) if s == 999 else _far(s))
    r = _call(walk, **MEASURED)
    assert r["fill_price"] == 99.90
    assert not [k for k in _keys(r) if "fee" in k.lower()]
    import io, pathlib, tokenize
    src = pathlib.Path(__file__).with_name("book_limit_entry.py").read_text()
    names = {t.string.lower() for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.NAME}
    assert not [n for n in names if "fee" in n]


def test_fail_open_fill_and_no_fill_without_measured_receipt_are_never_complete():
    # RK 24 Oct FAIL-OPEN: measured pooled delay but rows with no WS_FRAME receive_ts. Before the fix both the FILL
    # and the NO_FILL receipt were forced COMPLETE and countable; now neither is.
    def bare(row_for):
        return lambda s: {**row_for(s), "receive_ts": None, "receive_mono": None, "receive_ts_source": "NONE"}
    crossed = _tape(bare(lambda s: _book_row(s, 99.85, 99.90, size=2.0) if s == 999 else _far(s)))
    f = _call(crossed, **MEASURED)
    assert f["outcome"] == "FILL" and f["evidence_status"] != "COMPLETE"
    assert f["entry_book_receive_ts_source"] != "WS_FRAME"
    assert f["r17_entry_status"] == R17_UNPROVEN_ENTRY and "BOOK_RECEIVE_TS_MISSING" in f["r17_entry_failures"]
    n = _call(_tape(bare(_far)), **MEASURED)
    assert n["outcome"] == "NO_FILL" and n["evidence_status"] != "COMPLETE"
    assert n["r17_entry_status"] == R17_UNPROVEN_ENTRY and "BOOK_RECEIVE_TS_MISSING" in n["r17_entry_failures"]
    # The same tape WITH measured receipts stays countable (the fix does not over-reject).
    ok = _call(_tape(_far), **MEASURED)
    assert ok["evidence_status"] == "COMPLETE" and ok["entry_book_receive_ts_source"] == "WS_FRAME"
    assert ok["r17_entry_status"] is None


def test_delay_stamp_uses_the_one_r17_label_set():
    # RK 24 Oct delay_stamp: the retired sampler label is never live_ack; one pattern shared with r17 + the pool.
    import live_ack_pool
    import paper_fill
    import r17_exclusion
    import r17_fields
    assert live_ack_pool.MEASURED_LABEL_PATTERN == r17_fields.MEASURED_DELAY_PATTERN
    retired = paper_fill.delay_stamp("lane_max_n12")
    assert retired["delay_source_kind"] == "default" and retired["delay_unverified"] is True
    pooled = paper_fill.delay_stamp("POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n12")
    assert pooled["delay_source_kind"] == "live_ack" and pooled["pool_n"] == 12 and pooled["delay_unverified"] is False
    small = paper_fill.delay_stamp("POOLED_LIVE_ACK_CROSS_LANE:EXIT_RO_MKT_q90_n9")
    assert small["delay_source_kind"] == "live_ack" and small["delay_unverified"] is True
    dflt = paper_fill.delay_stamp("POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_default_n3")
    assert dflt["delay_source_kind"] == "default" and dflt["delay_unverified"] is True
    stop = paper_fill.delay_stamp(r17_fields.STOP_RULE_NO_POOL)
    assert stop["delay_source_kind"] == "stop_rule" and stop["delay_unverified"] is False
    for src in ("lane_max_n12", "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n12",
                "POOLED_LIVE_ACK_CROSS_LANE:EXIT_RO_MKT_q90_n9", "POOLED_LIVE_ACK_CROSS_LANE:MAKER_REST_default_n40"):
        s = paper_fill.delay_stamp(src)
        assert (not s["delay_unverified"]) == r17_exclusion._delay_measured(src), src
