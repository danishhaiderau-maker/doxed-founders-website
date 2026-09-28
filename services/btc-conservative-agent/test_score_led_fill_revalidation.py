"""Fill-time recheck uses the admission rule that opened the order; tiles own capacity.

Live QA on f4db4ddb: every score-led tile order that became executable was
cancelled with FILL_REVALIDATION_NO_TRADE because the recheck read the raw AI
verdict (NO_TRADE) instead of the score-led rule (e.g. SHORT 42 > LONG 38).
"""

import ast
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot  # noqa: E402

TILE = bot.ACTIVE_TILE_ORDER[0]
OTHER_TILE = bot.ACTIVE_TILE_ORDER[1]


@pytest.fixture
def score_led(monkeypatch):
    monkeypatch.setattr(bot, "SCORE_LED_PAPER_RESEARCH_ENABLED", True)
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: True)
    monkeypatch.setattr(bot, "_force_paper_mode_active", lambda: True)
    for lane in bot.ACTIVE_TILE_ORDER:
        monkeypatch.setitem(bot.ACTIVE_TILE_REGISTRY[lane], "admission_treatment", bot.SCORE_LED_ADMISSION_POLICY_ID)
    with bot.state_lock:
        bot.state["live_armed"] = False
        bot.state["bitfinex_live_enabled"] = False


def _raw_ai(long_score, short_score):
    return {
        "direction": "NO_TRADE", "candidate_direction": "NO_TRADE",
        "decision": "NO_TRADE", "long_score": long_score, "short_score": short_score,
    }


def _recheck(lane, direction, ai):
    now = time.time()
    signal_ts = now - (bot.FILL_DIRECTION_REVALIDATE_AFTER_SEC + 60)
    order = {"research_lane": lane, "signal_dir": direction, "created_ts": signal_ts}
    signal = {"timing": {"signal_ts": signal_ts}, "created_ts_ts": signal_ts}
    views = bot._fill_revalidation_ai_views(ai)
    return bot.stale_fill_direction_conflict(
        order, signal, now=now,
        latest_ai=bot._fill_revalidation_ai_for_lane(views, lane),
        latest_ai_ts=now - 5, current_context={},
    )


def test_score_led_order_with_raw_no_trade_fills_when_stronger_side_unchanged(score_led):
    assert _recheck(TILE, "SHORT", _raw_ai(38, 42)) == ""


def test_true_tie_still_cancels(score_led):
    assert _recheck(TILE, "SHORT", _raw_ai(40, 40)) == "FILL_REVALIDATION_NO_TRADE"


def test_direction_reversal_still_cancels(score_led):
    assert _recheck(TILE, "SHORT", _raw_ai(45, 30)) == "FILL_REVALIDATION_REVERSED_SHORT_TO_LONG"


def test_invalid_scores_fail_closed(score_led):
    assert _recheck(TILE, "SHORT", _raw_ai(None, 42)) == "FILL_REVALIDATION_NO_TRADE"


def test_non_registry_lane_keeps_raw_ai_recheck(score_led):
    assert _recheck("CONTINUOUS", "SHORT", _raw_ai(38, 42)) == "FILL_REVALIDATION_NO_TRADE"


def test_shadow_recheck_uses_score_led_rule(score_led, monkeypatch):
    now = time.time()
    with bot.state_lock:
        saved = {k: bot.state.get(k) for k in ("last_ai", "last_ai_ts", "last_ai_context")}
        bot.state["last_ai"] = _raw_ai(38, 42)
        bot.state["last_ai_ts"] = now - 5
        bot.state["last_ai_context"] = {}
    try:
        shadow = {"signal_ts": now - 400, "direction": "SHORT"}
        assert bot._shadow_stage_direction_revalidation(shadow, now) == ("VALID", "FILL_REVALIDATION_SAME_SHORT")
        with bot.state_lock:
            bot.state["last_ai"] = _raw_ai(40, 40)
        assert bot._shadow_stage_direction_revalidation(shadow, now)[1] == "FILL_REVALIDATION_NO_TRADE"
    finally:
        with bot.state_lock:
            bot.state.update(saved)


class _ForbiddenLock:
    def __enter__(self):
        raise AssertionError("state_lock acquired while trade_lock is held")

    def __exit__(self, *exc):
        return False

    acquire = __enter__


def test_lane_selection_under_trade_lock_never_takes_state_lock(score_led, monkeypatch):
    views = bot._fill_revalidation_ai_views(_raw_ai(38, 42))
    monkeypatch.setattr(bot, "state_lock", _ForbiddenLock())
    with bot.trade_lock:
        chosen = bot._fill_revalidation_ai_for_lane(views, TILE)
    assert chosen["direction"] == "SHORT"


def test_score_led_view_is_built_outside_trade_lock():
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_pending_orders")
    forbidden = {"_fill_revalidation_ai_views", "_effective_score_led_family_ai"}
    built = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_fill_revalidation_ai_views":
            built = True
        if isinstance(node, ast.With) and any(
            getattr(item.context_expr, "id", "") == "trade_lock" for item in node.items
        ):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    assert getattr(inner.func, "id", "") not in forbidden
    assert built


def test_every_registry_tile_declares_its_own_capacity():
    from combo_pathway_config import validate_tile_registry

    assert validate_tile_registry() == ()
    for lane in bot.ACTIVE_TILE_ORDER:
        assert bot.tile_max_active_signals(lane) == bot.ACTIVE_TILE_REGISTRY[lane]["max_active_signals"] >= 1
        assert bot.get_lane_max_active_signals(lane) == bot.tile_max_active_signals(lane)
    assert bot.tile_max_active_signals("CONTINUOUS") is None


@pytest.fixture
def admission(monkeypatch):
    counts = {}
    monkeypatch.setattr(bot, "get_active_signal_count", lambda lane=None: counts.get(lane, 0))
    monkeypatch.setattr(bot, "_refresh_order_and_signal_ttl", lambda: None)
    monkeypatch.setattr(bot, "lane_orders_allowed", lambda lane: True)
    monkeypatch.setattr(bot, "risk_trading_allowed", lambda: True)
    monkeypatch.setattr(bot, "get_execution_status", lambda: "RESEARCH_ALLOW")
    monkeypatch.setattr(bot, "manual_admin_pause_active", lambda: False)
    with bot.state_lock:
        saved = {k: bot.state.get(k) for k in ("execution_paused", "manual_admin_pause", "max_active_signals")}
        bot.state.update(execution_paused=False, manual_admin_pause=False, max_active_signals=20)
    yield counts
    with bot.state_lock:
        bot.state.update(saved)


def test_full_tile_blocks_only_itself(admission):
    cap = bot.tile_max_active_signals(TILE)
    admission.update({TILE: cap, OTHER_TILE: 0, None: cap})
    assert bot.evaluate_execution_admission(TILE) == (False, "MAX_ACTIVE_SIGNALS")
    assert bot.evaluate_execution_admission(OTHER_TILE) == (True, "ALLOWED")
    assert bot.ensure_lane_signal_capacity(TILE) is False
    assert bot.ensure_lane_signal_capacity(OTHER_TILE) is True


def test_shared_pool_saturation_no_longer_refuses_tiles(admission):
    admission.update({None: 25, TILE: 3})
    assert bot.evaluate_execution_admission(TILE) == (True, "ALLOWED")
    assert bot.ensure_lane_signal_capacity(TILE) is True
