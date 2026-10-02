"""Shared AI input revision r2: tape returns, two-sided swing detector, closed-bar volume ratio, call flags."""
import pytest

import bot
from combo_pathway_config import AI_PROMPT_INPUT_REVISION, AI_PROMPT_INPUT_REVISION_HISTORY


def _candle(i, high, low, volume=10.0):
    mid = (high + low) / 2.0
    return [i * 900_000, mid, high, low, mid, volume]


def _swings(points):
    return [{"type": kind, "price": price, "idx": i} for i, (kind, price) in enumerate(points)]


def test_revision_is_registered_and_r2_is_current():
    assert AI_PROMPT_INPUT_REVISION == "shared_direction_inputs_r2_20261002"
    assert AI_PROMPT_INPUT_REVISION_HISTORY[-1][0] == AI_PROMPT_INPUT_REVISION
    assert len({rev for rev, _ in AI_PROMPT_INPUT_REVISION_HISTORY}) == len(AI_PROMPT_INPUT_REVISION_HISTORY)


def test_lower_high_is_detected_even_when_low_labels_end_the_sequence(monkeypatch):
    swings = _swings([("high", 105.0), ("low", 95.0), ("high", 103.0), ("low", 96.0), ("low", 97.0)])
    labels, _ = bot.label_swing_sequence(swings)
    assert "LH" not in labels[-2:]
    monkeypatch.setattr(bot, "extract_pivot_swings", lambda candles, *a, **k: swings)
    ms = bot.compute_market_structure([[0, 1, 1, 1, 1, 1]] * 20)
    assert ms["lower_high"] is True and ms["last_high_label"] == "LH"
    assert ms["higher_low"] is True and ms["last_low_label"] == "HL"
    assert ms["hh_hl_sequence_active"] is False and ms["lh_ll_sequence_active"] is False
    micro = bot.build_micro_sr_levels([[0, 1, 1, 1, 1, 1]] * 20)
    assert micro["lower_high"] is True and micro["higher_low"] is True


def test_sequence_flags_need_both_sides():
    up = bot.last_swing_pair_labels(_swings([("high", 104.0), ("low", 95.0), ("high", 106.0), ("low", 97.0)]))
    assert up["higher_high"] and up["higher_low"] and up["hh_hl_sequence_active"]
    down = bot.last_swing_pair_labels(_swings([("high", 106.0), ("low", 97.0), ("high", 104.0), ("low", 95.0)]))
    assert down["lower_high"] and down["lower_low"] and down["lh_ll_sequence_active"]
    one_sided = bot.last_swing_pair_labels(_swings([("low", 95.0), ("low", 97.0), ("high", 104.0)]))
    assert one_sided["last_high_label"] is None and one_sided["higher_low"] is True
    assert one_sided["hh_hl_sequence_active"] is False


def test_volume_ratio_uses_closed_bars_not_the_forming_bar_or_trade_sizes():
    closed = [_candle(i, 101, 99, volume=10.0) for i in range(21)]
    closed.append(_candle(21, 101, 99, volume=25.0))
    forming = _candle(22, 101, 99, volume=0.5)
    assert bot.compute_volume_ratio(closed + [forming]) == pytest.approx(2.5)
    assert bot.compute_volume_ratio(closed[:10]) == 0.0


def test_ai_context_returns_come_from_the_tape(monkeypatch):
    monkeypatch.setattr(bot, "_ai_shadow_tape_features",
                        lambda now=None: {"ret_1m": 0.0012, "ret_5m": -0.003, "ret_1m_bp": 12.0, "ret_5m_bp": -30.0})
    import inspect
    source = inspect.getsource(bot.build_pure_ai_context)
    assert 'tape.get("ret_1m")' in source and 'buffers.get("ret_1m"' not in source
    assert not hasattr(bot, "ret_1m_buffer")


@pytest.mark.parametrize("raw,long_s,short_s,committed,abstain,mismatch", [
    ("LONG", 70, 30, True, False, False),
    ("SHORT", 20, 60, True, False, False),
    ("LONG", 64, 35, False, False, False),
    ("NO_TRADE", 80, 10, False, True, False),
    ("SHORT", 70, 30, False, False, True),
    ("LONG", 50, 50, False, False, False),
])
def test_commit_flags_are_logged_on_every_call(raw, long_s, short_s, committed, abstain, mismatch):
    flags = bot.ai_commit_flags({"raw_direction": raw, "long_score": long_s, "short_score": short_s})
    assert flags["ai_committed"] is committed
    assert flags["explicit_abstain"] is abstain
    assert flags["score_direction_mismatch"] is mismatch
    assert flags["score_gap"] == abs(long_s - short_s)
    assert bot.ai_commit_flags({"raw_direction": "LONG", "long_score": 90, "short_score": 0,
                                "ai_error": True})["ai_committed"] is False


def test_ai_input_log_row_carries_prompt_revision_served_model_and_flags(monkeypatch):
    rows = []
    monkeypatch.setattr(bot, "_safe_append_jsonl", lambda path, row, **kw: rows.append(row) or True)
    ai = {"decision": "REJECT", "direction": "NO_TRADE", "raw_direction": "LONG", "long_score": 75,
          "short_score": 25, "deepseek_served_model": "deepseek-flash", "prompt_id": "p1"}
    bot.log_ai_input_full({"trade_id": "t1"}, ai, {}, 0.0)
    row = rows[0]
    assert row["prompt_input_revision"] == AI_PROMPT_INPUT_REVISION and row["prompt_id"] == "p1"
    assert row["ai"]["deepseek_served_model"] == "deepseek-flash"
    assert row["ai"]["ai_committed"] is True and row["ai"]["raw_direction"] == "LONG"
