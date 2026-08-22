import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from collector_v22 import build_research_event
from conservative_fill_cohort import build_conservative_fill_cohort


def tape_row(ts, *, ask=101, bid=99, ask_qty=2, bid_qty=2,
             sell_qty=0, buy_qty=0, sell_vwap=None, buy_vwap=None,
             fresh=True, symbol="BTC"):
    return {
        "schema": "market_microstructure_1s_v1", "symbol": symbol,
        "bucket_ts": ts, "fresh": fresh, "valid_bbo": True,
        "ask": ask, "bid": bid, "ask_qty": ask_qty, "bid_qty": bid_qty,
        "sell_qty": sell_qty, "buy_qty": buy_qty,
        "sell_vwap": sell_vwap, "buy_vwap": buy_vwap,
        "trade_count": int(sell_qty > 0) + int(buy_qty > 0),
    }


def event(qty=1, *, schema="research_event_v2.2"):
    return {
        "schema": schema, "event_id": "evt-1", "direction": "LONG", "symbol": "BTC",
        "research_execution_basis": {
            "requested_qty": qty, "requested_qty_provenance": "SOURCE_TICKET_QTY",
            "exchange_qty_claim": True,
        },
        "research_chase_schedule": {
            "authoritative": True,
            "intervals": [{"bucket_id": "chase_3", "start_ts": 100, "end_ts": 103,
                           "limit_price": 100, "generation": 3}],
        },
    }


def test_new_event_records_exact_source_qty_without_rewriting_legacy():
    new = build_research_event(
        trade_id="new", epoch_id="epoch", signal_ts=100, signal_price=100,
        requested_qty=.25, market_microstructure_symbol="tBTCF0:USTF0", candles_1m=[],
    )
    basis = new["research_execution_basis"]
    assert basis["requested_qty"] == .25
    assert basis["requested_qty_provenance"] == "SOURCE_TICKET_QTY"
    assert basis["exchange_qty_claim"] is True
    assert basis["market_microstructure_symbol"] == "tBTCF0:USTF0"
    legacy = {"schema": "research_event_v2.1", "event_id": "old"}
    assert "research_execution_basis" not in legacy


def test_standardized_basis_is_explicitly_not_exchange_qty():
    new = build_research_event(
        trade_id="new", epoch_id="epoch", signal_ts=100, signal_price=100,
        requested_qty=None, research_notional_usd=2000, candles_1m=[],
    )
    basis = new["research_execution_basis"]
    assert basis["requested_qty"] == 20
    assert basis["requested_qty_provenance"] == "STANDARDIZED_RESEARCH_NOTIONAL"
    assert basis["exchange_qty_claim"] is False


def test_missing_qty_is_unsupported():
    e = event(None)
    result = build_conservative_fill_cohort([e], [tape_row(i) for i in range(100, 103)])
    assert result["receipts"][0]["outcome"] == "UNSUPPORTED"
    assert result["receipts"][0]["negative_reasons"] == ["MISSING_REQUESTED_QTY"]


def test_exact_qty_fill_partial_and_no_fill_receipts():
    fill_tape = [tape_row(100), tape_row(101), tape_row(102, ask=100, sell_qty=1, sell_vwap=100)]
    partial_tape = [tape_row(100), tape_row(101), tape_row(102, ask=100, ask_qty=.2, sell_qty=1, sell_vwap=100)]
    no_fill_tape = [tape_row(100), tape_row(101), tape_row(102)]
    assert build_conservative_fill_cohort([event()], fill_tape)["receipts"][0]["outcome"] == "FILL"
    partial = build_conservative_fill_cohort([event()], partial_tape)["receipts"][0]
    assert partial["outcome"] == "PARTIAL_FILL" and partial["filled_qty"] == .2
    assert build_conservative_fill_cohort([event()], no_fill_tape)["receipts"][0]["outcome"] == "NO_FILL"


def test_legacy_is_unsupported_and_cohort_cannot_promote_qualification():
    result = build_conservative_fill_cohort(
        [event(schema="research_event_v2.1")], [tape_row(i) for i in range(100, 103)],
    )
    assert result["receipts"][0]["negative_reasons"] == ["LEGACY_EVENT_UNSUPPORTED"]
    assert result["qualification"] == "DESCRIPTIVE_ONLY"
    assert result["qualification_promotion_allowed"] is False
    assert result["conservative_execution_gate_changed"] is False


def test_incomplete_microstructure_cannot_claim_no_fill():
    result = build_conservative_fill_cohort([event()], [tape_row(100), tape_row(102)])
    receipt = result["receipts"][0]
    assert receipt["outcome"] == "UNSUPPORTED"
    assert receipt["negative_reasons"][0] == "MICROSTRUCTURE_WINDOW_INCOMPLETE"
    assert "EVIDENCE_GAP" in receipt["negative_reasons"]


def test_fresh_trigger_can_prove_fill_despite_unrelated_stale_second():
    e = event()
    e["research_chase_schedule"]["intervals"][0]["end_ts"] = 104
    rows = [
        tape_row(100, fresh=False),
        tape_row(101),
        tape_row(102),
        tape_row(103, ask=100, sell_qty=1, sell_vwap=100),
    ]
    receipt = build_conservative_fill_cohort([e], rows)["receipts"][0]
    assert receipt["outcome"] == "FILL"
    assert receipt["supported"] is True
    assert receipt["trigger_bucket_ts"] == 103
    assert receipt["microstructure_completeness"]["eligible"] is False
    assert receipt["fill_time_semantics"] == "LATEST_PROVEN_TRIGGER_BUCKET_NOT_EARLIEST_FILL"
    assert receipt["window_integrity_scope"] == "TRIGGER_PROOF_ONLY"


def test_strategy_symbol_alias_maps_to_exact_bitfinex_tape_instrument():
    e = event()
    e.pop("symbol")
    e["event_episode"] = {"symbol": "BTCUSD"}
    rows = [
        tape_row(100, symbol="tBTCF0:USTF0"),
        tape_row(101, symbol="tBTCF0:USTF0"),
        tape_row(102, ask=100, sell_qty=1, sell_vwap=100, symbol="tBTCF0:USTF0"),
    ]
    receipt = build_conservative_fill_cohort([e], rows)["receipts"][0]
    assert receipt["outcome"] == "FILL"
    assert receipt["market_microstructure_symbol"] == "tBTCF0:USTF0"
