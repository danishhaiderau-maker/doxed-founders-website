import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "research"))

from quantity_execution import (
    apply_quantity_constraints,
    build_signed_quantity_constraints,
    validate_signed_quantity_constraints,
)


def constraints(**overrides):
    values = {
        "symbol": "tBTCF0:USTF0",
        "quantity_step": "0.0001",
        "quantity_precision": 4,
        "min_lot": "0.0002",
        "min_notional": "5",
        "captured_at": "2026-08-30T00:00:00Z",
        "source_revision": "revision-a",
        "source": "BITFINEX_MARKET_METADATA",
    }
    values.update(overrides)
    return build_signed_quantity_constraints(**values)


def decide(raw, *, accumulated=0, price=80_000, signed=None):
    return apply_quantity_constraints(
        requested_qty="0.0010", raw_partial_qty=raw,
        accumulated_qty=accumulated, execution_price=price,
        constraints=constraints() if signed is None else signed,
        symbol="tBTCF0:USTF0",
    )


def test_missing_constraints_are_unsupported_not_no_fill():
    result = decide("0.0005", signed={})
    assert result["final_classification"] == "UNSUPPORTED"
    assert result["minimum_lot_decision"] == "UNKNOWN"
    assert "QUANTITY_CONSTRAINT_SCHEMA_INVALID" in result["reasons"]


def test_tampered_signed_constraints_fail_closed():
    signed = constraints()
    signed["min_notional"] = "0.01"
    normalized, reasons = validate_signed_quantity_constraints(
        signed, symbol="tBTCF0:USTF0",
    )
    assert normalized is None
    assert reasons == ["QUANTITY_CONSTRAINT_SIGNATURE_INVALID"]


def test_raw_partial_is_rounded_down_and_every_boundary_is_visible():
    result = decide("0.00039")
    assert result["raw_partial_quantity"] == 0.00039
    assert result["rounded_executable_quantity"] == 0.0003
    assert result["minimum_lot_decision"] == "PASS"
    assert result["minimum_notional_decision"] == "PASS"
    assert result["accumulated_quantity_before"] == 0
    assert result["accumulated_quantity_after"] == 0.0003
    assert result["final_classification"] == "PARTIAL_FILL"
    assert result["accepted"] is True


def test_positive_raw_quantity_below_step_is_visible_but_not_accepted():
    result = decide("0.00009")
    assert result["raw_partial_quantity"] == 0.00009
    assert result["rounded_executable_quantity"] == 0
    assert result["final_classification"] == "NO_FILL"
    assert result["reasons"] == ["RAW_PARTIAL_ROUNDED_TO_ZERO"]


def test_min_lot_and_min_notional_each_fail_explicitly():
    lot = decide("0.0001")
    assert lot["minimum_lot_decision"] == "FAIL"
    assert lot["reasons"] == ["MINIMUM_LOT_NOT_MET"]
    notional = decide("0.0002", price=10_000)
    assert notional["minimum_lot_decision"] == "PASS"
    assert notional["minimum_notional_decision"] == "FAIL"
    assert notional["reasons"] == ["MINIMUM_NOTIONAL_NOT_MET"]


def test_accepted_partial_accumulates_across_reprices_and_becomes_full():
    first = decide("0.0004")
    second = decide("0.0006", accumulated=first["accumulated_quantity_after"])
    assert first["final_classification"] == "PARTIAL_FILL"
    assert second["accumulated_quantity_before"] == 0.0004
    assert second["rounded_executable_quantity"] == 0.0006
    assert second["accumulated_quantity_after"] == 0.001
    assert second["final_classification"] == "FULL_FILL"


def test_symbol_mismatch_is_unknown_unsupported():
    result = apply_quantity_constraints(
        requested_qty="0.001", raw_partial_qty="0.001", execution_price=80_000,
        constraints=constraints(), symbol="BTCUSD",
    )
    assert result["final_classification"] == "UNSUPPORTED"
    assert "QUANTITY_CONSTRAINT_SYMBOL_MISMATCH" in result["reasons"]
