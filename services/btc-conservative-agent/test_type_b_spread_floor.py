"""Regression tests for the MIN_SPREAD_FLOOR gate in type_b_hunter_v1.

Raising MIN_SPREAD_FLOOR from 2 to 4 (R2 backtest, 2026-08-04) tightens entry
so only signals with directional_spread >= 4 clear the spread gate. These
tests pin that behavior so a future accidental revert is caught immediately.
"""

from __future__ import annotations

import type_b_hunter_v1 as type_b


def _passing_long_features(spread: int) -> dict:
    """Build a LONG feature set that clears every gate EXCEPT possibly spread.

    Score breakdown with these inputs:
      ADX 32 (30-35)        = +1.5
      volume_ratio 0.5      = +1.0
      regime BEAR           = +0.5
      structure +3.0 (LONG) = +1.0
      delta +20.0 (LONG)    = +0.75
      spread 3-5            = +0.5  (when spread is 3,4,5)
      edge 4.0              = +0.5
      ema_slope up          = +0.25
    Best case total ~6.0, comfortably above MIN_SCORE_TO_ENTER (3.0), so the
    ONLY gate that can block entry is the spread floor.
    """
    return {
        "direction": "LONG",
        "adx": 32.0,
        "volume_ratio": 0.5,
        "conviction_spread": spread,
        "regime": "BEAR",
        "structure_score": 3.0,
        "delta": 20.0,
        "edge_score": 4.0,
        "ema_slope": "up",
    }


def _assert_accepted(spread: int) -> None:
    entered, detail = type_b.should_enter_type_b(ai_prob=0.0, features=_passing_long_features(spread))
    assert entered is True, (
        f"spread={spread} should be ACCEPTED (>=MIN_SPREAD_FLOOR={type_b.MIN_SPREAD_FLOOR}) "
        f"but was rejected: {detail.get('block_reason')}"
    )


def _assert_rejected(spread: int) -> None:
    entered, detail = type_b.should_enter_type_b(ai_prob=0.0, features=_passing_long_features(spread))
    assert entered is False, (
        f"spread={spread} should be REJECTED (<MIN_SPREAD_FLOOR={type_b.MIN_SPREAD_FLOOR}) "
        f"but was accepted"
    )
    reason = detail.get("block_reason") or ""
    assert reason.startswith("SPREAD_FLOOR"), (
        f"spread={spread} should be blocked by SPREAD_FLOOR, got reason={reason!r}"
    )


def test_min_spread_floor_constant_is_four() -> None:
    assert type_b.MIN_SPREAD_FLOOR == 4, (
        f"MIN_SPREAD_FLOOR must stay 4 (R2 backtest); found {type_b.MIN_SPREAD_FLOOR}"
    )


def test_spread_below_floor_two_is_rejected() -> None:
    # Used to be accepted when floor was 2; now must be rejected.
    _assert_rejected(2)


def test_spread_three_is_rejected() -> None:
    # The key cohort boundary: spread=3 sits in the leak band (<4) and must
    # be rejected after the R2 raise.
    _assert_rejected(3)


def test_spread_at_floor_four_is_accepted() -> None:
    _assert_accepted(4)


def test_spread_above_floor_five_is_accepted() -> None:
    _assert_accepted(5)


def main() -> None:
    test_min_spread_floor_constant_is_four()
    test_spread_below_floor_two_is_rejected()
    test_spread_three_is_rejected()
    test_spread_at_floor_four_is_accepted()
    test_spread_above_floor_five_is_accepted()
    print("Type-B MIN_SPREAD_FLOOR regression tests passed")


if __name__ == "__main__":
    main()
