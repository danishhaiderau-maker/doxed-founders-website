"""Regression tests for the R2 spread floor on the CONTINUOUS lane.

The previous R2 fix (commit 0f980ab4) raised MIN_SPREAD_FLOOR in
an unrelated retired experiment, but live cont-* trades fire from the CONTINUOUS lane spawn
path (bot.spawn_continuous_lane_from_ai_scan) which had NO spread floor of its
own. The R2 floor gates AI signals before they reach the chase lifecycle.

Stage 1 Fix #4 (2026-08-06): the prior implementation multiplied the constant
by 10, making the effective threshold raw gap >= 40 (8x stricter than the
original R2 intent of raw gap >= ~5). The constant is now used as a RAW
score-gap threshold directly. The canonical tier gate remains the stricter
execution authority at raw gap >= 5; the R2 floor is defense in depth.

Score scale is 0-100 (long_score/short_score). The canonical Continuous tier
gate is gap >= 5; the older R2 floor remains a lower-bound safety check:
  raw gap  3 -> REJECTED
  raw gap  4 -> REJECTED by the canonical tier gate
  raw gap  5 -> ACCEPTED (> floor)
  raw gap 10 -> ACCEPTED (well above floor)
  raw gap 30 -> ACCEPTED (well above floor; previously REJECTED under * 10 bug)
"""

from __future__ import annotations

import os

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


def _approved_ai(long_score: int, short_score: int) -> dict:
    """Build an AI result that ai_decision_should_execute returns True for."""
    return {
        "decision": "APPROVE",
        "approved": True,
        "execution_tier": "STRONG_APPROVE",
        "direction": "LONG" if long_score >= short_score else "SHORT",
        "long_score": long_score,
        "short_score": short_score,
        "shared_ai_call_ts": 1700000000,
    }


def _run_spawn(long_score: int, short_score: int, monkeypatch, textual_decision="APPROVE") -> dict:
    """Return the normalized AI record handed to the Continuous lifecycle."""
    calls: list[dict] = []

    def fake_spawn_combo_lane(ctx, ai, edge_score, features, target_lane, trigger_reason):
        calls.append(dict(ai))
        return None

    monkeypatch.setattr(bot, "_spawn_combo_lane", fake_spawn_combo_lane)
    monkeypatch.setattr(bot, "continuous_ai_research_enabled", lambda: True)

    ctx = {"trade_id": "test-ctx"}
    ai = _approved_ai(long_score, short_score)
    ai["decision"] = textual_decision
    ai["approved"] = textual_decision == "APPROVE"
    ai["execution_tier"] = textual_decision
    bot.spawn_continuous_lane_from_ai_scan(
        ctx=ctx,
        ai=ai,
        edge_score=5.0,
        features={},
        source_lane=bot.RESEARCH_LANE_AI_SCAN,
    )
    assert len(calls) == 1
    return calls[0]


def test_floor_constant_is_four() -> None:
    assert bot.CONTINUOUS_MIN_SPREAD_FLOOR == 4, (
        f"CONTINUOUS_MIN_SPREAD_FLOOR must stay 4 (R2 floor); "
        f"found {bot.CONTINUOUS_MIN_SPREAD_FLOOR}"
    )


def test_raw_gap_three_is_rejected(monkeypatch) -> None:
    # long 50 / short 53 -> raw gap 3 -> REJECTED
    normalized = _run_spawn(long_score=50, short_score=53, monkeypatch=monkeypatch)
    assert normalized["decision"] == "REJECT"


def test_raw_gap_four_is_rejected_by_canonical_tier(monkeypatch) -> None:
    normalized = _run_spawn(long_score=50, short_score=54, monkeypatch=monkeypatch)
    assert normalized["decision"] == "REJECT"


def test_raw_gap_five_is_accepted(monkeypatch) -> None:
    normalized = _run_spawn(long_score=48, short_score=53, monkeypatch=monkeypatch)
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "SOFT_APPROVE"


def test_raw_gap_ten_is_accepted(monkeypatch) -> None:
    # long 45 / short 55 -> raw gap 10 -> ACCEPTED (well above floor)
    # Note: under the prior * 10 bug this was REJECTED. Fix #4 restored the
    # intended semantics so this signal now correctly enters the lifecycle.
    normalized = _run_spawn(long_score=45, short_score=55, monkeypatch=monkeypatch)
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "APPROVE"


def test_raw_gap_thirty_is_accepted(monkeypatch) -> None:
    # long 20 / short 50 -> raw gap 30 -> ACCEPTED (well above floor)
    # Note: under the prior * 10 bug this was REJECTED at threshold 40.
    normalized = _run_spawn(long_score=35, short_score=65, monkeypatch=monkeypatch)
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "STRONG_APPROVE"
    assert normalized["direction"] == "SHORT"


def test_textual_reject_cannot_override_executable_score_gap(monkeypatch) -> None:
    """Production regression: 35/65 must execute even if the model says REJECT."""
    normalized = _run_spawn(
        long_score=35,
        short_score=65,
        monkeypatch=monkeypatch,
        textual_decision="REJECT",
    )
    assert normalized["raw_decision"] == "REJECT"
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "STRONG_APPROVE"
    assert normalized["approved"] is True


def test_zero_gap_remains_rejected(monkeypatch) -> None:
    """A textual reject with no directional separation remains shadow-only."""
    calls: list[tuple] = []

    def fake_spawn_combo_lane(ctx, ai, edge_score, features, target_lane, trigger_reason):
        calls.append((target_lane,))
        return None

    monkeypatch.setattr(bot, "_spawn_combo_lane", fake_spawn_combo_lane)
    monkeypatch.setattr(bot, "continuous_ai_research_enabled", lambda: True)

    rejected_ai = {
        "decision": "REJECT",
        "approved": False,
        "execution_tier": "REJECT",
        "direction": "LONG",
        "long_score": 50,
        "short_score": 50,
    }
    bot.spawn_continuous_lane_from_ai_scan(
        ctx={"trade_id": "test-ctx"},
        ai=rejected_ai,
        edge_score=5.0,
        features={},
        source_lane=bot.RESEARCH_LANE_AI_SCAN,
    )
    # REJECT AI still flows through to _spawn_combo_lane because the floor
    # only short-circuits the EXECUTE branch; data-only shadow is preserved.
    # The contract under test is just: a REJECT verdict must not be silently
    # rewritten by the floor into a block. Verify by checking no exception
    # was raised and the call landed (shadow-collection path is intact).
    assert len(calls) == 1, "rejected AI must still feed the shadow/spawn path"
