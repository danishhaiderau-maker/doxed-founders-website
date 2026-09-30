"""Fill markouts and taker-at-signal counterfactual evidence primitives."""
import pytest

import execution_markouts as em


def test_markout_sign_follows_direction():
    assert em.markout_bps(1, 100.0, 101.0) == pytest.approx(100.0)
    assert em.markout_bps(-1, 100.0, 101.0) == pytest.approx(-100.0)
    assert em.markout_bps(0, 100.0, 101.0) is None
    assert em.markout_bps(1, 0.0, 101.0) is None


def test_quote_sample_marks_to_mid_and_side_correct_exit_touch():
    long_s = em.quote_sample(sign=1, entry=100.0, bid=100.9, ask=101.1, last=101.0,
                             quote_ts=9.5, now=10.0, due_ts=10.0, horizon=10)
    assert long_s["markout_mid_bps"] == pytest.approx(100.0)
    assert long_s["markout_exit_touch_bps"] == pytest.approx(90.0)
    short_s = em.quote_sample(sign=-1, entry=100.0, bid=100.9, ask=101.1, last=101.0,
                              quote_ts=9.5, now=10.0, due_ts=10.0, horizon=10)
    assert short_s["markout_exit_touch_bps"] == pytest.approx(-110.0)
    crossed = em.quote_sample(sign=1, entry=100.0, bid=102, ask=101, last=101,
                              quote_ts=9.5, now=10.0, due_ts=10.0, horizon=10)
    assert crossed["mid"] is None and crossed["markout_mid_bps"] is None


def test_book_emits_only_after_every_horizon_and_flags_late_samples():
    book = em.MarkoutBook(horizons=(1, 10))
    assert book.register("a", anchor_ts=0.0, entry_price=100.0, direction="LONG", row={"id": "a"})
    assert not book.register("a", anchor_ts=0.0, entry_price=100.0, direction="LONG", row={})
    assert not book.register("b", anchor_ts=0.0, entry_price=100.0, direction="NO_TRADE", row={})
    assert book.sample(now=0.5, bid=100, ask=100.2, last=100.1, quote_ts=0.5) == []
    assert book.sample(now=1.2, bid=100, ask=100.2, last=100.1, quote_ts=1.2) == []
    done = book.sample(now=14.0, bid=101, ask=101.2, last=101.1, quote_ts=14.0)
    assert len(done) == 1 and book.pending_count() == 0
    row = done[0]
    assert set(row["markouts"]) == {"1s", "10s"}
    assert row["markouts"]["1s"]["on_time"] is True
    assert row["markouts"]["10s"]["on_time"] is False
    assert row["markouts_complete"] is False


def test_book_is_bounded():
    book = em.MarkoutBook(horizons=(1,), max_pending=1)
    assert book.register("a", anchor_ts=0, entry_price=1, direction="LONG", row={})
    assert not book.register("b", anchor_ts=0, entry_price=1, direction="LONG", row={})
    assert book.dropped == 1


def test_counterfactual_contract_is_collection_only_at_the_live_test_size():
    assert em.MARKOUT_HORIZONS_SEC == (1, 10, 60, 300)
    assert em.TAKER_LATENCIES_SEC == (0.25, 1.0, 2.0)
    assert em.COUNTERFACTUAL_MARGIN_USD == 0.25
    assert em.COUNTERFACTUAL_LEVERAGE == 100.0
