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
