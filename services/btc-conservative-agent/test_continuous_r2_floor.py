"""Regression tests for the R2 spread floor on the CONTINUOUS lane.

The previous R2 fix (commit 0f980ab4) raised MIN_SPREAD_FLOOR in
type_b_hunter_v1, but live cont-* trades fire from the CONTINUOUS lane spawn
path (bot.spawn_continuous_lane_from_ai_scan) which had NO spread floor of its
own. This test pins the real fix: AI signals with a raw score gap < 40
(bucket < 4 on the 0-100 -> 0-10 dashboard scale) must NOT reach the chase
lifecycle. Backtest on 21 realized trades (commit b22acacd):
  spread<4 cohort:  28.6% win rate, -$10.61 PnL  (the leak)
  spread>=4 cohort: 71.4% win rate, -$0.15 PnL   (clean edge)

Score scale is 0-100 (long_score/short_score); bucket = raw/10. So:
  raw gap 30 -> bucket 3 -> REJECTED
  raw gap 40 -> bucket 4 -> ACCEPTED (== floor)
  raw gap 50 -> bucket 5 -> ACCEPTED
  raw gap 10 -> bucket 1 -> REJECTED
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


def _run_spawn(long_score: int, short_score: int, monkeypatch) -> bool:
    """Return True if the floor let the signal through to the chase lifecycle."""
    calls: list[tuple] = []

    def fake_spawn_combo_lane(ctx, ai, edge_score, features, target_lane, trigger_reason):
        calls.append((target_lane, ai.get("long_score"), ai.get("short_score")))
        return None

    monkeypatch.setattr(bot, "_spawn_combo_lane", fake_spawn_combo_lane)
    monkeypatch.setattr(bot, "continuous_ai_research_enabled", lambda: True)

    ctx = {"trade_id": "test-ctx"}
    ai = _approved_ai(long_score, short_score)
    bot.spawn_continuous_lane_from_ai_scan(
        ctx=ctx,
        ai=ai,
        edge_score=5.0,
        features={},
        source_lane=bot.RESEARCH_LANE_AI_SCAN,
    )
    return len(calls) == 1


def test_floor_constant_is_four() -> None:
    assert bot.CONTINUOUS_MIN_SPREAD_FLOOR == 4, (
        f"CONTINUOUS_MIN_SPREAD_FLOOR must stay 4 (R2 backtest); "
        f"found {bot.CONTINUOUS_MIN_SPREAD_FLOOR}"
    )


def test_raw_gap_30_bucket3_is_rejected(monkeypatch) -> None:
    # long 20 / short 50 -> raw gap 30 -> bucket 3 -> REJECTED (< floor 4)
    passed = _run_spawn(long_score=20, short_score=50, monkeypatch=monkeypatch)
    assert passed is False, "raw gap 30 (bucket 3) must be REJECTED by R2 floor"


def test_raw_gap_40_bucket4_is_accepted(monkeypatch) -> None:
    # long 10 / short 50 -> raw gap 40 -> bucket 4 -> ACCEPTED (== floor 4)
    passed = _run_spawn(long_score=10, short_score=50, monkeypatch=monkeypatch)
    assert passed is True, "raw gap 40 (bucket 4) must be ACCEPTED (== floor)"


def test_raw_gap_50_bucket5_is_accepted(monkeypatch) -> None:
    # long 0 / short 50 -> raw gap 50 -> bucket 5 -> ACCEPTED (> floor 4)
    passed = _run_spawn(long_score=0, short_score=50, monkeypatch=monkeypatch)
    assert passed is True, "raw gap 50 (bucket 5) must be ACCEPTED (> floor)"


def test_raw_gap_10_bucket1_is_rejected(monkeypatch) -> None:
    # long 45 / short 55 -> raw gap 10 -> bucket 1 -> REJECTED (well below floor)
    passed = _run_spawn(long_score=45, short_score=55, monkeypatch=monkeypatch)
    assert passed is False, "raw gap 10 (bucket 1) must be REJECTED by R2 floor"


def test_rejected_ai_does_not_reach_floor(monkeypatch) -> None:
    """When AI itself rejects, the floor must not even fire (shadow path)."""
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
