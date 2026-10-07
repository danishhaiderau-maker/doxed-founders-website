"""Tests for paper-vs-Bitfinex twin matching and diff."""
from __future__ import annotations

from paper_bitfinex_match import (
    match_paper_to_live,
    match_report,
    diff_twin,
    MATCH_INTENT,
    MATCH_CLIENT_ORDER,
)


def test_match_by_intent_id():
    paper = [{"intent_id": "i-1", "side": "LONG", "entry_price": 100.0, "qty": 1.0}]
    live = [{"intent_id": "i-1", "side": "LONG", "entry_price": 100.0, "qty": 1.0}]
    m = match_paper_to_live(paper, live)
    assert m[0]["matched"] is True
    assert m[0]["match_key"] == MATCH_INTENT
    assert m[0]["diffs"] == []


def test_match_by_client_order_id_fallback():
    paper = [{"client_order_id": "c-9", "side": "LONG", "entry_price": 100.0}]
    live = [{"client_order_id": "c-9", "side": "LONG", "entry_price": 100.0}]
    m = match_paper_to_live(paper, live)
    assert m[0]["match_key"] == MATCH_CLIENT_ORDER


def test_unmatched_flagged():
    paper = [{"trade_id": "t-1"}]
    live = []
    m = match_paper_to_live(paper, live)
    assert m[0]["matched"] is False
    assert any(d["kind"] == "UNMATCHED" for d in m[0]["diffs"])


def test_price_divergence_flagged():
    paper = {"intent_id": "i-2", "entry_price": 100.0}
    live = {"intent_id": "i-2", "entry_price": 101.0}
    diffs = diff_twin(paper, live)
    assert any(d["field"] == "entry_price" and d["kind"] == "VALUE_DIVERGENCE" for d in diffs)


def test_size_divergence_flagged():
    paper = {"intent_id": "i-3", "qty": 1.0}
    live = {"intent_id": "i-3", "qty": 1.1}
    diffs = diff_twin(paper, live)
    assert any(d["field"] == "size" for d in diffs)


def test_side_mismatch_flagged():
    diffs = diff_twin({"side": "LONG"}, {"side": "SHORT"})
    assert any(d["field"] == "side" and d["kind"] == "SIDE_MISMATCH" for d in diffs)


def test_timing_divergence_flagged():
    diffs = diff_twin({"entry_ts": 1000.0}, {"entry_ts": 1010.0})
    assert any(d["field"] == "entry_ts" and d["kind"] == "TIMING_DIVERGENCE" for d in diffs)


def test_match_report_summary():
    paper = [
        {"intent_id": "i-1", "entry_price": 100.0},
        {"intent_id": "i-2", "entry_price": 200.0},
        {"trade_id": "t-3"},
    ]
    live = [
        {"intent_id": "i-1", "entry_price": 100.0},
        {"intent_id": "i-2", "entry_price": 205.0},
    ]
    rep = match_report(paper, live)
    assert rep["paper_count"] == 3
    assert rep["matched_count"] == 2
    assert rep["unmatched_count"] == 1
    assert rep["diverged_count"] == 1
