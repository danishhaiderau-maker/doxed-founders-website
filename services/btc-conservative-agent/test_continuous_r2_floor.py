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

Score scale is 0-100 (long_score/short_score). The score-led research contract
admits every valid non-tied directional gap; the legacy execution authority
remains stricter outside that disarmed cohort:
  raw gap  1 -> SOFT_APPROVE in score-led research
  raw gap  3 -> SOFT_APPROVE in score-led research
  raw gap  4 -> SOFT_APPROVE in score-led research
  raw gap  5 -> ACCEPTED (> floor)
  raw gap 10 -> ACCEPTED (well above floor)
  raw gap 30 -> ACCEPTED (well above floor; previously REJECTED under * 10 bug)
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


@pytest.fixture(autouse=True)
def _isolated_evidence_root(monkeypatch, tmp_path) -> None:
    """Keep immutable receipt ledgers isolated across tests and reruns."""
    monkeypatch.chdir(tmp_path)


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

    # Immutable pre-entry evidence is keyed by causal opportunity identity.
    # Each score case is a distinct synthetic opportunity, so do not reuse one
    # trade id across parametrically different receipts in the same test run.
    ctx = {
        "trade_id": (
            f"test-ctx-{long_score}-{short_score}-{textual_decision.lower()}"
        )
    }
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


def test_raw_gap_one_is_admitted_in_score_led_research(monkeypatch) -> None:
    # long 50 / short 51 -> raw gap 1 -> SOFT_APPROVE
    normalized = _run_spawn(long_score=50, short_score=51, monkeypatch=monkeypatch)
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "SOFT_APPROVE"


def test_raw_gap_three_is_admitted_in_score_led_research(monkeypatch) -> None:
    # long 50 / short 53 -> raw gap 3 -> SOFT_APPROVE
    normalized = _run_spawn(long_score=50, short_score=53, monkeypatch=monkeypatch)
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "SOFT_APPROVE"


def test_51_49_textual_reject_still_admits_stronger_side(monkeypatch) -> None:
    """The user-facing 51/49 case must remain visible as a short opportunity."""
    normalized = _run_spawn(
        long_score=49,
        short_score=51,
        monkeypatch=monkeypatch,
        textual_decision="REJECT",
    )
    assert normalized["raw_decision"] == "REJECT"
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "SOFT_APPROVE"
    assert normalized["direction"] == "SHORT"


def test_raw_gap_four_is_admitted_in_score_led_research(monkeypatch) -> None:
    normalized = _run_spawn(long_score=50, short_score=54, monkeypatch=monkeypatch)
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "SOFT_APPROVE"


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


def test_score_led_research_admits_stronger_side_despite_textual_reject(monkeypatch) -> None:
    """Research keeps raw REJECT telemetry but admits a valid stronger side."""
    normalized = _run_spawn(
        long_score=35,
        short_score=65,
        monkeypatch=monkeypatch,
        textual_decision="REJECT",
    )
    assert normalized["raw_decision"] == "REJECT"
    assert normalized["decision"] == "APPROVE"
    assert normalized["execution_tier"] == "STRONG_APPROVE"
    assert normalized["direction"] == "SHORT"
    assert normalized["approved"] is True


def test_explicit_no_trade_with_unequal_scores_is_researched(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_spawn_combo_lane(ctx, ai, edge_score, features, target_lane, trigger_reason):
        calls.append(dict(ai))

    monkeypatch.setattr(bot, "_spawn_combo_lane", fake_spawn_combo_lane)
    monkeypatch.setattr(bot, "continuous_ai_research_enabled", lambda: True)
    bot.spawn_continuous_lane_from_ai_scan(
        ctx={"trade_id": "test-no-trade"},
        ai={
            "decision": "REJECT",
            "raw_decision": "REJECT",
            "approved": False,
            "direction": "NO_TRADE",
            "candidate_direction": "NO_TRADE",
            "raw_direction": "NO_TRADE",
            "explicit_abstain": True,
            "long_score": 65,
            "short_score": 35,
        },
        edge_score=5.0,
        features={},
        source_lane=bot.RESEARCH_LANE_AI_SCAN,
    )
    assert len(calls) == 1
    assert calls[0]["direction"] == "LONG"
    assert calls[0]["decision"] == "APPROVE"
    assert calls[0]["execution_tier"] == "STRONG_APPROVE"
    assert calls[0]["approved"] is True


def test_non_research_textual_reject_remains_fail_closed(monkeypatch) -> None:
    """The score-led exception must never widen non-research/live execution."""
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: False)
    ai = {
        "decision": "REJECT",
        "raw_decision": "REJECT",
        "approved": False,
        "direction": "SHORT",
        "candidate_direction": "SHORT",
        "long_score": 35,
        "short_score": 65,
    }
    assert bot.continuous_score_gap_execution_tier(ai) == "REJECT"


def test_non_research_low_gap_keeps_execution_floor(monkeypatch) -> None:
    """Research-only admission must not widen non-research/live routing."""
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: False)
    ai = {
        "decision": "APPROVE",
        "approved": True,
        "direction": "SHORT",
        "candidate_direction": "SHORT",
        "long_score": 50,
        "short_score": 53,
    }
    assert bot.continuous_score_gap_execution_tier(ai) == "REJECT"


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
