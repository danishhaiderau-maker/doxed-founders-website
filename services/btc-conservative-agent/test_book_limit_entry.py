"""RK R3/R4: book_limit_entry decides on quote_trigger and prices on paper_fill (bfx_book_10l only)."""
from book_limit_entry import EVALUATOR_VERSION, ENTRY_PRICE_SOURCE_BOOK, R17_UNPROVEN_ENTRY, book_limit_entry
from book_walk_test_fixture import entry_tape
from research.quantity_execution import build_signed_quantity_constraints

SYMBOL = "tBTCF0:USTF0"
CONSTRAINTS = build_signed_quantity_constraints(
    symbol=SYMBOL, quantity_step="0.00000001", quantity_precision=8,
    min_lot="0.00000001", min_notional="0.000001",
    captured_at="2026-08-30T00:00:00Z", source_revision="test-revision", source="TEST_FIXTURE",
)

SCHEDULE = [{"bucket_id": "ev-1:chase:0", "start_ts": 1000.0, "end_ts": 1005.0, "limit_price": 100.0}]


def _run(*, crossed=True, visible_qty=2.0, qty=1.0, **kw):
    walk = entry_tape(visible_qty, crossed).window(990, 1010)
    return book_limit_entry("LONG", qty, SCHEDULE, walk.book_at, quantity_constraints=CONSTRAINTS,
                            symbol=SYMBOL, **kw)


def test_crossed_book_fills_on_book_and_is_unproven_without_verified_delay():
    r = _run()
    assert r["outcome"] == "FILL"
    assert r["entry_price_source"] == ENTRY_PRICE_SOURCE_BOOK
    assert r["evaluator_version"] == EVALUATOR_VERSION
    assert r["fill_model"] == "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"
    assert r["fill_ts"] == 1001.5 and r["trigger_bucket_ts"] == r["fill_ts"]
    assert r["fill_price"] <= 100.0
    assert r["r17_entry_status"] == R17_UNPROVEN_ENTRY


def test_verified_delay_is_not_unproven():
    r = _run(delay_s=0.5, delay_source="lane_max_n12")
    assert r["outcome"] == "FILL"
    assert r.get("r17_entry_status") != R17_UNPROVEN_ENTRY


def test_book_that_never_reaches_limit_is_no_fill():
    assert _run(crossed=False)["outcome"] == "NO_FILL"


def test_missing_book_is_unsupported():
    r = book_limit_entry("LONG", 1.0, SCHEDULE, None)
    assert r["outcome"] == "UNSUPPORTED"
    assert "BOOK_WALK_UNAVAILABLE" in r["negative_reasons"]


# ---- RK #848 conditions (a) / (b) / (c) -------------------------------------------------------------------
from book_walk_test_fixture import book_row as _book_row
from research_v3_book_walk import BookWalkTape as _Tape


def _tape(row_for):
    return _Tape.from_rows([row_for(s) for s in range(980, 1020)], [], receipt={"fixture": True}).window(980, 1020)


def _ladder(s, *, ask=99.90, size=0.4):
    """Asks 99.90, 99.95, 100.00 (inside a 100.00 buy limit) then 100.05.. (outside), ``size`` each."""
    row = _book_row(s, ask - 0.05, ask, size=size)
    return row


def test_a_full_size_walks_the_row_at_signal_plus_delay_within_the_limit_only():
    # Row 999 (latest complete at 1000 + 0.5) is the ladder; every other row is out of reach (ask 100.50).
    walk = _tape(lambda s: _ladder(s) if s == 999 else _book_row(s, 100.45, 100.5))
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, delay_s=0.5, delay_source="lane_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["signal_ts"] == 1000.0 and r["fill_ts"] == 1000.5 and r["walk_book_s"] == 999
    assert r["walk_row_rule"] == "LATEST_BOOK_ROW_AT_SIGNAL_PLUS_DELAY"
    assert r["entry_fill"]["qty"] == 1.0  # the FULL size was sent into the walk
    # only 99.90/99.95/100.00 (3 x 0.4 = 1.2 >= 1.0) are inside the cap: VWAP of 0.4@99.90 + 0.4@99.95 + 0.2@100.00
    assert abs(r["fill_price"] - (0.4 * 99.90 + 0.4 * 99.95 + 0.2 * 100.00)) < 1e-9
    assert r["outcome"] == "FILL" and r["r17_entry_status"] is None


def test_a_rows_before_signal_plus_delay_are_not_walked():
    # Crossed only at 999 (complete at 1000) but the order arrives at 1001.5 on row 1000, which is out of reach.
    walk = _tape(lambda s: _ladder(s) if s == 999 else _book_row(s, 100.45, 100.5))
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, delay_s=1.5, delay_source="lane_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["outcome"] == "NO_FILL" and r["attempts"][0]["walk_book_s"] == 1000


def test_b_size_outside_the_limit_is_not_filled_and_a_quiet_remainder_is_proven_not_filled():
    # 2 x 0.25 inside the limit at arrival; later rows never reach the limit -> remainder NOT_FILLED, countable.
    walk = _tape(lambda s: _book_row(s, 99.90, 99.95, size=0.25) if s == 999 else _book_row(s, 100.45, 100.5))
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, delay_s=0.5, delay_source="lane_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["outcome"] == "PARTIAL_FILL" and abs(r["filled_qty"] - 0.5) < 1e-9 and r["fill_price"] <= 100.0
    assert r["resting_remainder"]["status"] == "NOT_FILLED" and abs(r["remaining_qty"] - 0.5) < 1e-9
    assert r["r17_entry_status"] is None


def test_b_resting_remainder_reached_without_trade_queue_proof_is_r17_unproven():
    # Same partial, but a later row reaches the limit while the remainder rests: no trade+queue proof -> unproven.
    walk = _tape(lambda s: _book_row(s, 99.90, 99.95, size=0.25) if s in (999, 1002) else _book_row(s, 100.45, 100.5))
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, delay_s=0.5, delay_source="lane_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert abs(r["filled_qty"] - 0.5) < 1e-9  # the remainder is never added to the filled size
    assert r["resting_remainder"]["status"] == R17_UNPROVEN_ENTRY
    assert r["resting_remainder"]["queue_position_proof"] is None
    assert r["r17_entry_status"] == R17_UNPROVEN_ENTRY
    assert r["r17_entry_reason"] == "RESTING_FILL_NO_TRADE_QUEUE_PROOF"


def test_b_nothing_at_arrival_then_level_reached_is_an_unproven_resting_claim_not_a_countable_fill():
    walk = _tape(lambda s: _book_row(s, 99.90, 99.95) if s == 1002 else _book_row(s, 100.45, 100.5))
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, delay_s=0.5, delay_source="lane_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["fill_basis"] == "RESTING_AT_LIMIT_UNPROVEN" and r["fill_price"] == 100.0
    assert r["r17_entry_status"] == R17_UNPROVEN_ENTRY and r["entry_fill"] is None


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _keys(v)


def test_c_no_fee_term_in_the_entry_price_or_receipt():
    walk = _tape(lambda s: _ladder(s, size=2.0) if s == 999 else _book_row(s, 100.45, 100.5))
    r = book_limit_entry("LONG", 1.0, SCHEDULE, walk.book_at, delay_s=0.5, delay_source="lane_max_n12",
                         quantity_constraints=CONSTRAINTS, symbol=SYMBOL)
    assert r["fill_price"] == 99.90  # exactly the book level: no fee/penalty added to the price
    assert not [k for k in _keys(r) if "fee" in k.lower()]
    import io, pathlib, tokenize
    src = pathlib.Path(__file__).with_name("book_limit_entry.py").read_text()
    names = {t.string.lower() for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.NAME}
    assert not [n for n in names if "fee" in n]  # no fee identifier anywhere in the entry pricer's code
