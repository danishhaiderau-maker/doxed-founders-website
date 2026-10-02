"""Repaired evidence streams: win_prob, counterfactual join keys, replay censoring,
touch-grid fill certainty and the cross-venue connection mask."""

import ast
import json
import os
import time
from pathlib import Path

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import pytest

import chase_offset_touch_grid as grid
import cross_venue_tape as cvt

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def _function_source(name):
    tree = ast.parse(SOURCE)
    node = next(row for row in tree.body
                if isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef)) and row.name == name)
    return ast.get_source_segment(SOURCE, node)


@pytest.fixture(scope="module")
def bot():
    import bot as module
    return module


# --------------------------------------------------------------------------
# win_prob
# --------------------------------------------------------------------------
def test_placeholder_win_prob_is_null_not_zero(bot):
    assert bot.evidence_win_prob({"win_prob": 0, "win_prob_status": "NOT_REQUESTED_BY_PROMPT"}) is None
    assert bot.evidence_win_prob({"win_prob": 0}) is None
    assert bot.evidence_win_prob(None) is None
    assert bot.evidence_win_prob({"win_prob": 63, "win_prob_status": "EMITTED"}) == 63


def test_shadow_win_prob_uses_compact_side_probability(bot):
    ai = {"win_prob": 0, "win_prob_status": "NOT_REQUESTED_BY_PROMPT"}
    ch = {"sides": {"llm_score_led": "SHORT"},
          "compact": {"parse_status": "OK", "p_long_success": 0.4, "p_short_success": 0.44}}
    out = bot._ai_shadow_win_prob(ai, ch)
    assert out["win_prob"] == 44.0
    assert out["win_prob_status"] == "COMPACT_SHADOW_P_SUCCESS_SCORE_LED_SIDE"
    assert out["main_ai_win_prob"] is None
    none = bot._ai_shadow_win_prob(ai, {"sides": {"llm_score_led": "NONE"}, "compact": ch["compact"]})
    assert none["win_prob"] is None and none["win_prob_status"] == "UNAVAILABLE"


def test_evidence_rows_route_win_prob_through_helper():
    assert SOURCE.count('"ai_win_prob": evidence_win_prob(') >= 2
    blocked = _function_source("log_blocked_signal")
    assert '"ai_win_prob": evidence_win_prob(ai)' in blocked
    assert '"ai_win_prob": ai.get("win_prob")' not in blocked


# --------------------------------------------------------------------------
# Counterfactual timestamps and join keys
# --------------------------------------------------------------------------
def test_counterfactual_join_fields_from_snapshot(bot):
    snap = {"approve_ts": 1_790_000_000.5, "shared_ai_call_id": "scan-abc", "epoch_id": "epoch-v22-x",
            "research_lane": "shadow"}
    out = bot.counterfactual_join_fields("t1", snap, {})
    assert out["signal_ts"] == 1_790_000_000.5
    assert out["shared_ai_call_id"] == "scan-abc" and out["epoch_id"] == "epoch-v22-x"
    assert out["join_keys_missing"] == []
    assert out["ts"] and out["written_ts"] > 0


def test_counterfactual_join_fields_fall_back_without_guessing(bot):
    replay = {"start_ts": "2026-10-01T00:00:00+00:00", "collection_epoch_id": "epoch-v22-y"}
    out = bot.counterfactual_join_fields("scan-777", {}, replay)
    assert out["signal_ts"] == pytest.approx(1790812800.0)
    assert out["shared_ai_call_id"] == "scan-777"
    assert out["epoch_id"] == "epoch-v22-y"
    missing = bot.counterfactual_join_fields("paper-1", {}, {})
    assert missing["shared_ai_call_id"] is None
    assert set(missing["join_keys_missing"]) == {"signal_ts", "shared_ai_call_id", "epoch_id"}


def test_counterfactual_builders_spread_join_fields():
    assert SOURCE.count("**counterfactual_join_fields(") == 2
    assert SOURCE.count("_counterfactual_snapshot_with_join_keys(") == 3


# --------------------------------------------------------------------------
# Signal replay completion
# --------------------------------------------------------------------------
def test_expiry_sweep_waits_for_post_exit_grace():
    assert "POST_EXIT_REPLAY_GRACE_SEC" in SOURCE
    i = SOURCE.index('_buf_float(buf.get("post_exit_deadline_ts"), 0) + POST_EXIT_REPLAY_GRACE_SEC')
    assert i > 0


def test_shutdown_dumps_are_labelled_censored():
    assert SOURCE.count('dump_replay(tid, terminal_reason="CENSORED_PROCESS_SHUTDOWN")') == 2
    dump = _function_source("dump_replay")
    for key in ('"dumped_ts"', '"dump_seq"', '"dump_reason"', '"censored"', '"natural_completion_reason"'):
        assert key in dump


def test_dump_replay_censored_row(bot, tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "SIGNAL_REPLAY_FILE", str(tmp_path / "signal_replay.jsonl"))
    tid = "shadow-test-censor"
    now = time.time()
    with bot.replay_lock:
        bot.replay_buffers[tid] = {"start_ts": now - 30, "start_price": 100000.0, "direction": "LONG",
                                   "lane": "shadow", "ticks": [{"t": 1.0, "price": 100000.0}],
                                   "closed": False}
    try:
        bot.dump_replay(tid, terminal_reason="CENSORED_PROCESS_SHUTDOWN")
        bot.dump_replay(tid, terminal_reason="CENSORED_PROCESS_SHUTDOWN")
    finally:
        with bot.replay_lock:
            bot.replay_buffers.pop(tid, None)
    rows = [json.loads(line) for line in open(tmp_path / "signal_replay.jsonl", encoding="utf-8")]
    assert [r["dump_seq"] for r in rows] == [1, 2]
    assert all(r["censored"] is True for r in rows)
    assert rows[0]["replay_completion_reason"] == "CENSORED_PROCESS_SHUTDOWN"
    assert rows[0]["natural_completion_reason"] == "INCOMPLETE_BUFFER"


# --------------------------------------------------------------------------
# Touch grid: quote cross vs trade print, queue ahead
# --------------------------------------------------------------------------
def test_classify_touch_certainty_levels():
    sell_limit = 100100.0
    cross = grid.classify_touch(side="sell", limit_price=sell_limit, last=100090.0, bid=100100.0, ask=100101.0)
    assert cross["quote_cross"] and cross["fill_certainty"] == grid.CERTAIN_QUOTE_CROSS
    through = grid.classify_touch(side="sell", limit_price=sell_limit, last=100105.0, bid=100090.0, ask=100091.0)
    assert through["trade_through"] and through["fill_certainty"] == grid.CERTAIN_TRADE_THROUGH
    at = grid.classify_touch(side="sell", limit_price=sell_limit, last=100100.0, bid=100095.0,
                             ask=100100.0, ask_qty=1.25)
    assert at["fill_certainty"] == grid.UNCERTAIN_AT_LIMIT_QUEUE
    assert at["queue_ahead_upper_btc"] == 1.25 and at["queue_ahead_status"] == "L1_SAME_SIDE_AT_LIMIT"
    behind = grid.classify_touch(side="sell", limit_price=sell_limit, last=100100.0, bid=100095.0, ask=100096.0)
    assert behind["queue_ahead_upper_btc"] is None and behind["queue_ahead_status"] == "UNKNOWN_NOT_AT_BEST"
    buy = grid.classify_touch(side="buy", limit_price=99900.0, last=99950.0, low=99890.0, bid=99940.0, ask=99941.0)
    assert buy["fill_certainty"] == grid.UNCERTAIN_RANGE_ONLY and buy["high_low_source"] == "RANGE"


def test_poll_grid_emits_certain_touch_after_uncertain_first_touch():
    state = {"trade_id": "t-grid", "direction": "SHORT", "expires_ts": 1e12,
             "offsets": {"0.30": {"limit_price": 100100.0, "touched": False, "touch_ts": None}}}
    first = grid.poll_grid_state(state, now_ts=10.0, last=100100.0, bid=100095.0, ask=100100.0,
                                 bid_qty=0.5, ask_qty=2.0)
    assert [r["event"] for r in first] == ["TOUCHED"]
    assert first[0]["fill_certainty"] == grid.UNCERTAIN_AT_LIMIT_QUEUE
    assert first[0]["queue_ahead_upper_btc"] == 2.0
    assert grid.poll_grid_state(state, now_ts=11.0, last=100099.0, bid=100095.0, ask=100099.5) == []
    later = grid.poll_grid_state(state, now_ts=14.0, last=100099.0, bid=100100.5, ask=100101.0)
    assert [r["event"] for r in later] == ["CERTAIN_TOUCH"]
    assert later[0]["sec_after_first_touch"] == 4.0
    assert grid.poll_grid_state(state, now_ts=15.0, last=100120.0, bid=100119.0, ask=100121.0) == []


def test_bot_passes_l1_quantities_to_touch_grid():
    poll = _function_source("_poll_chase_offset_touch_grid")
    assert "bid_qty=None if not bid_qty else float(bid_qty)" in poll
    assert "ask_qty=None if not ask_qty else float(ask_qty)" in poll
    assert "_poll_chase_offset_touch_grid(price, grid_bid, grid_ask, grid_bid_qty, grid_ask_qty)" in SOURCE


# --------------------------------------------------------------------------
# Cross-venue connection mask (taker-flow coverage)
# --------------------------------------------------------------------------
def test_cross_venue_up_mask_round_trips_and_legacy_is_unknown():
    samples = [{"sec": 1_800_000_000 + i, "mid": 100000.0, "last": None, "buy": 0.1, "sell": 0.0,
                "up": i % 2 == 0} for i in range(60)]
    venues = {v: samples for v in cvt.VENUES}
    V0 = next(iter(cvt.VENUES))
    row = cvt.encode_minute(1_800_000_000, venues, [100000.0] * 60)
    first = row["venues"][V0]
    assert first["up"] == "10" * 30
    cells = cvt.decode_minute(row)
    assert cells[V0][1_800_000_000]["up"] is True
    assert cells[V0][1_800_000_001]["up"] is False
    legacy = json.loads(json.dumps(row))
    for cell in legacy["venues"].values():
        cell.pop("up", None)
    assert cvt.decode_minute(legacy)[V0][1_800_000_000]["up"] is None
