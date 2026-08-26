from __future__ import annotations

import os
import time

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


def _ctx(*, adx=15, stack_bull=False, stack_bear=False, score=0, agreement="CONFLICTED", sr_bias="UNKNOWN", micro=False):
    return {
        "trade_id": "scan-test",
        "price": 79000,
        "adx": adx,
        "sr_bias": sr_bias,
        "historically_profitable_patterns": {"must_not": "reach prompt"},
        "market_context": {
            "trend_strength": {"adx": adx},
            "ema_alignment": {"stack_bull": stack_bull, "stack_bear": stack_bear},
            "market_structure": {"structure_score": score},
            "multi_tf": {"agreement": agreement},
        },
        "ai_input_upgrade": {
            "micro_structure_confirmed": micro,
            "higher_low_detected": stack_bull,
            "lower_high_detected": stack_bear,
            "market_structure_shift": "MIXED",
        },
        "exhaustion_3m": {"closed_3m_ts": 123, "rsi14_3m": 55, "atr14_pct_3m": 0.2},
    }


def test_explicit_no_trade_is_not_forced_to_a_side():
    parsed = bot.parse_ai_response_fields(
        '{"direction":"NO_TRADE","long_score":55,"short_score":45,"reason":"conflicted"}'
    )
    assert parsed["direction"] == "NO_TRADE"
    assert parsed["decision"] == "REJECT"
    assert parsed["explicit_abstain"] is True


def test_prompt_contract_permits_and_requires_abstention():
    assert "Choose LONG, SHORT, or NO_TRADE" in bot.AI_PROMPT_TEMPLATE
    assert '"direction": "LONG or SHORT or NO_TRADE"' in bot.AI_PROMPT_TEMPLATE
    assert "Never return NO_TRADE" not in bot.AI_PROMPT_TEMPLATE


def test_tied_scores_abstain_without_long_default():
    assert bot.derive_candidate_direction(50, 50, "NO_TRADE") == "NO_TRADE"
    parsed = bot.parse_ai_response_fields('{"direction":"LONG","long_score":50,"short_score":50}')
    assert parsed["direction"] == "NO_TRADE"
    assert parsed["decision"] == "REJECT"


def test_malformed_and_direction_mismatch_fail_closed():
    malformed = bot.parse_ai_response_fields('{"direction":"LONG","long_score":"bad","short_score":45}')
    assert malformed["direction"] == "NO_TRADE"
    assert malformed["decision"] == "REJECT"
    mismatch = bot.parse_ai_response_fields('{"direction":"LONG","long_score":40,"short_score":60}')
    assert mismatch["direction"] == "NO_TRADE"
    assert mismatch["score_direction_mismatch"] is True


def test_low_adx_countertrend_short_is_blocked():
    ctx = _ctx(adx=15, stack_bull=True, score=2, sr_bias="LONG_PREFERRED")
    assert bot.weak_countertrend_conflict(ctx, "SHORT").startswith("WEAK_COUNTERTREND_SHORT")
    ai = {"direction": "SHORT", "decision": "APPROVE", "approved": True, "execution_tier": "APPROVE"}
    assert bot.apply_weak_countertrend_gate(ctx, ai)["decision"] == "REJECT"


def test_low_adx_aligned_side_is_not_blanket_blocked():
    ctx = _ctx(adx=15, stack_bull=True, score=2, agreement="BULL_ALIGNED", micro=True)
    assert bot.weak_countertrend_conflict(ctx, "LONG") == ""


def test_compact_prompt_separates_raw_and_derived_and_drops_noise():
    compact = bot.build_shared_direction_prompt_context(_ctx())
    assert compact["schema"] == "shared_direction_prompt_v4"
    assert "raw" in compact and "derived" in compact
    assert "ai_input_upgrade" not in compact
    assert "historically_profitable_patterns" not in compact
    assert compact["raw"]["closed_3m_ts"] == 123


def test_aged_order_cancels_when_newer_ai_reverses():
    now = time.time()
    order = {"side": "sell", "created_ts": now - 300, "signal_created_ts": now - 300}
    signal = {"created_ts_ts": now - 300, "timing": {"signal_ts": now - 300}}
    reason = bot.stale_fill_direction_conflict(
        order,
        signal,
        now=now,
        latest_ai={"direction": "LONG", "decision": "APPROVE"},
        latest_ai_ts=now - 5,
    )
    assert reason == "FILL_REVALIDATION_REVERSED_SHORT_TO_LONG"


def test_fresh_order_does_not_revalidate_early():
    now = time.time()
    order = {"side": "sell", "created_ts": now - 30, "signal_created_ts": now - 30}
    signal = {"created_ts_ts": now - 30}
    assert bot.stale_fill_direction_conflict(
        order,
        signal,
        now=now,
        latest_ai={"direction": "LONG", "decision": "APPROVE"},
        latest_ai_ts=now - 5,
    ) == ""


def test_aged_order_cancels_on_current_structure_conflict_without_new_ai():
    now = time.time()
    order = {"side": "sell", "created_ts": now - 300, "signal_created_ts": now - 300}
    signal = {"created_ts_ts": now - 300}
    reason = bot.stale_fill_direction_conflict(
        order,
        signal,
        now=now,
        latest_ai={},
        latest_ai_ts=0,
        current_context=_ctx(adx=15, stack_bull=True, score=3, sr_bias="LONG_PREFERRED"),
    )
    assert reason.startswith("FILL_REVALIDATION_WEAK_COUNTERTREND_SHORT")
