"""Stage 1 strategy fixes - focused regression coverage.

Covers the two highest-impact gates added in Stage 1:

1. test_zero_score_reject: parse_ai_factor_block must flag a payload with
   long_score=0, short_score=0 (or any pair summing < 50) as zero_score_reject,
   and parse_ai_response_fields must honor that flag to force decision=REJECT
   BEFORE derive_candidate_direction / derive_research_decision_tier run.

   Root cause being pinned: DeepSeek was returning long_score=0, short_score=0
   on 100% of calls (1,562/1,562 records), making the
   continuous_shared_direction_gap_v1 gap policy inert and letting direction
   fall through to derive_candidate_direction() which defaults to LONG.

2. test_structure_gate_rejects_long_vs_bear: apply_structure_agreement_gate
   must hard-reject a LONG direction when market_structure_shift contains
   "BEAR" (e.g. BEAR_CONTINUATION), and must let the same LONG pass when
   structure is BULL_CONTINUATION. This blocks the empirically worst loss
   pattern (14 of 20 recent LONG calls were against BEAR_CONTINUATION).
"""

from __future__ import annotations

import os

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


# ---------------------------------------------------------------------------
# Fix #1: zero-score reject
# ---------------------------------------------------------------------------


def _zero_score_payload() -> str:
    """Build an AI response payload where long_score + short_score < 50."""
    return (
        '```json\n'
        '{"direction": "LONG", "long_score": 0, "short_score": 0, '
        '"reason": "inert response reproducing DeepSeek zero-score bug"}\n'
        '```'
    )


def test_zero_score_reject_factor_block_flag() -> None:
    """parse_ai_factor_block must set zero_score_reject=True on sum < 50."""
    factors = bot.parse_ai_factor_block(_zero_score_payload())
    assert factors["factor_parse_ok"] is True, (
        f"factor_parse_ok must be True for valid JSON payload; got factors={factors}"
    )
    assert factors.get("zero_score_reject") is True, (
        f"zero_score_reject must be True when long+short < 50; got factors={factors}"
    )


def test_zero_score_reject_in_parse_ai_response_fields() -> None:
    """parse_ai_response_fields must force decision=REJECT and skip
    derive_candidate_direction when zero_score_reject is set."""
    parsed = bot.parse_ai_response_fields(_zero_score_payload())
    assert parsed["decision"] == "REJECT", (
        f"decision must be REJECT on zero-score payload; got {parsed['decision']!r}"
    )
    assert parsed["direction"] == "NO_TRADE", (
        f"direction must be NO_TRADE (no candidate derived from inert zeros); "
        f"got {parsed['direction']!r}"
    )
    assert parsed.get("zero_score_reject") is True, (
        "zero_score_reject flag must propagate to parsed result"
    )
    comment = parsed.get("factors", {}).get("zero_score_reject")
    assert comment is True, (
        f"factors.zero_score_reject must be True; got {comment!r}"
    )


def test_zero_score_reject_does_not_fire_on_real_scores() -> None:
    """A meaningful directional call (sum >= 50) must NOT trip the gate."""
    payload = (
        '```json\n'
        '{"direction": "LONG", "long_score": 70, "short_score": 20, '
        '"reason": "structure breakout confirmed by order flow"}\n'
        '```'
    )
    factors = bot.parse_ai_factor_block(payload)
    assert factors.get("zero_score_reject") is False, (
        f"zero_score_reject must be False for sum=90; got factors={factors}"
    )
    parsed = bot.parse_ai_response_fields(payload)
    assert parsed["decision"] != "REJECT" or parsed["direction"] != "NO_TRADE", (
        "Real-score payload must not be hard-forced to REJECT/NO_TRADE"
    )


def test_zero_score_reject_threshold_boundary() -> None:
    """The threshold is sum < 50: sum=49 trips, sum=50 does not."""
    payload_below = (
        '```json\n'
        '{"direction": "LONG", "long_score": 25, "short_score": 24, '
        '"reason": "boundary below"}\n'
        '```'
    )
    payload_at = (
        '```json\n'
        '{"direction": "LONG", "long_score": 25, "short_score": 25, '
        '"reason": "boundary at"}\n'
        '```'
    )
    factors_below = bot.parse_ai_factor_block(payload_below)
    factors_at = bot.parse_ai_factor_block(payload_at)
    assert factors_below.get("zero_score_reject") is True, (
        f"sum=49 must trip zero_score_reject; got {factors_below.get('zero_score_reject')}"
    )
    assert factors_at.get("zero_score_reject") is False, (
        f"sum=50 must NOT trip zero_score_reject; got {factors_at.get('zero_score_reject')}"
    )


# ---------------------------------------------------------------------------
# Fix #3: structure-agreement gate
# ---------------------------------------------------------------------------


def _ai_result(direction: str, tier: str = "STRONG_APPROVE") -> dict:
    """Build an execute-tier ai_result that the structure gate will evaluate."""
    return {
        "decision": "APPROVE",
        "approved": True,
        "execution_tier": tier,
        "research_soft": tier,
        "direction": direction,
        "win_prob": 70,
        "ai_error": False,
    }


def _ctx_with_structure_shift(shift: str, structure_score: float = 0.0) -> dict:
    """Build a ctx whose ai_input_upgrade carries the given market_structure_shift."""
    return {
        "ai_input_upgrade": {
            "market_structure_shift": shift,
        },
        "market_context": {
            "market_structure": {
                "structure_score": structure_score,
            },
        },
    }


def test_structure_gate_rejects_long_vs_bear() -> None:
    """LONG vs BEAR_CONTINUATION must be hard-rejected."""
    ctx = _ctx_with_structure_shift("BEAR_CONTINUATION", structure_score=-3.0)
    ai_result = _ai_result("LONG")
    out = bot.apply_structure_agreement_gate(ctx, ai_result)
    assert out["decision"] == "REJECT", (
        f"LONG vs BEAR_CONTINUATION must be REJECT; got {out['decision']!r}"
    )
    assert out["approved"] is False, "approved must be False after gate fires"
    assert out["execution_tier"] == "REJECT", (
        f"execution_tier must be REJECT; got {out['execution_tier']!r}"
    )
    gate = out.get("structure_agreement_gate") or ""
    assert "AI_LONG_VS_BEAR_STRUCTURE" in gate, (
        f"gate reason must mention AI_LONG_VS_BEAR_STRUCTURE; got {gate!r}"
    )


def test_structure_gate_rejects_short_vs_bull() -> None:
    """SHORT vs BULL_CONTINUATION must be hard-rejected (mirror case)."""
    ctx = _ctx_with_structure_shift("BULL_CONTINUATION", structure_score=3.0)
    ai_result = _ai_result("SHORT")
    out = bot.apply_structure_agreement_gate(ctx, ai_result)
    assert out["decision"] == "REJECT", (
        f"SHORT vs BULL_CONTINUATION must be REJECT; got {out['decision']!r}"
    )
    gate = out.get("structure_agreement_gate") or ""
    assert "AI_SHORT_VS_BULL_STRUCTURE" in gate, (
        f"gate reason must mention AI_SHORT_VS_BULL_STRUCTURE; got {gate!r}"
    )


def test_structure_gate_passes_long_vs_bull() -> None:
    """LONG vs BULL_CONTINUATION must pass (inverse of the reject case)."""
    ctx = _ctx_with_structure_shift("BULL_CONTINUATION", structure_score=3.0)
    ai_result = _ai_result("LONG")
    out = bot.apply_structure_agreement_gate(ctx, ai_result)
    assert out["decision"] == "APPROVE", (
        f"LONG vs BULL_CONTINUATION must pass; got decision={out['decision']!r}"
    )
    assert out.get("structure_agreement_gate") is None, (
        "gate must not fire when direction agrees with structure"
    )


def test_structure_gate_passes_short_vs_bear() -> None:
    """SHORT vs BEAR_CONTINUATION must pass (inverse of the mirror case)."""
    ctx = _ctx_with_structure_shift("BEAR_CONTINUATION", structure_score=-3.0)
    ai_result = _ai_result("SHORT")
    out = bot.apply_structure_agreement_gate(ctx, ai_result)
    assert out["decision"] == "APPROVE", (
        f"SHORT vs BEAR_CONTINUATION must pass; got decision={out['decision']!r}"
    )
    assert out.get("structure_agreement_gate") is None


def test_structure_gate_uses_score_threshold_when_shift_absent() -> None:
    """Gate must also fire on structure_score alone when shift string is empty.

    Brief contract: LONG with structure_score <= -2 rejects; SHORT with
    structure_score >= 2 rejects. This catches cases where the shift label is
    missing but the numeric structure score is still informative.
    """
    ctx = {
        "ai_input_upgrade": {"market_structure_shift": ""},
        "market_context": {"market_structure": {"structure_score": -2.5}},
    }
    out = bot.apply_structure_agreement_gate(ctx, _ai_result("LONG"))
    assert out["decision"] == "REJECT", (
        f"LONG with structure_score=-2.5 must REJECT; got {out['decision']!r}"
    )


def test_structure_gate_skips_non_execute_tier() -> None:
    """A SOFT_REJECT input must pass through unchanged (gate only fires on execute tiers)."""
    ctx = _ctx_with_structure_shift("BEAR_CONTINUATION", structure_score=-3.0)
    ai_result = _ai_result("LONG", tier="SOFT_REJECT")
    ai_result["decision"] = "SOFT_REJECT"
    ai_result["approved"] = False
    ai_result["execution_tier"] = "SOFT_REJECT"
    out = bot.apply_structure_agreement_gate(ctx, ai_result)
    assert out["decision"] == "SOFT_REJECT", (
        "Non-execute-tier inputs must pass through unchanged"
    )
    assert out.get("structure_agreement_gate") is None


# ---------------------------------------------------------------------------
# Smoke runner for non-pytest invocations
# ---------------------------------------------------------------------------


def main() -> None:
    test_zero_score_reject_factor_block_flag()
    test_zero_score_reject_in_parse_ai_response_fields()
    test_zero_score_reject_does_not_fire_on_real_scores()
    test_zero_score_reject_threshold_boundary()
    test_structure_gate_rejects_long_vs_bear()
    test_structure_gate_rejects_short_vs_bull()
    test_structure_gate_passes_long_vs_bull()
    test_structure_gate_passes_short_vs_bear()
    test_structure_gate_uses_score_threshold_when_shift_absent()
    test_structure_gate_skips_non_execute_tier()
    print("Stage 1 strategy-fix regression tests passed")


if __name__ == "__main__":
    main()
