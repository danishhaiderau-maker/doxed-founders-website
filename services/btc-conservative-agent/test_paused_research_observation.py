"""Regression checks for AI/data collection while paper execution is paused.

The operator pause must remain a hard order/relay stop, but in explicit
force-paper research the shared DeepSeek observation is still valuable. These
tests guard both halves of that contract: a narrow observer gate and no child
execution enqueue from a paused observation.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


SOURCE = Path(bot.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_source(name: str) -> str:
    node = next(
        item for item in TREE.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(SOURCE, node) or ""


def test_paused_observer_requires_explicit_force_paper_and_warm_runtime(monkeypatch):
    monkeypatch.setattr(
        bot,
        "_recompute_system_readiness",
        lambda _now=None: {"system_ready": True, "readiness_reasons": []},
    )
    with bot.state_lock:
        old = {
            "strategy_mode": bot.state.get("strategy_mode"),
            "live_armed": bot.state.get("live_armed"),
            "bitfinex_live_enabled": bot.state.get("bitfinex_live_enabled"),
            "ai_enabled": bot.state.get("ai_enabled"),
        }
        bot.state.update({
            "strategy_mode": "RESEARCH",
            "live_armed": False,
            "bitfinex_live_enabled": False,
            "ai_enabled": True,
        })
    try:
        allowed, reason, runtime = bot.can_run_paused_research_observation()
        assert allowed is True
        assert reason == "ADMIN_MANUAL_RESEARCH_OBSERVATION_ONLY"
        assert runtime["system_ready"] is True

        monkeypatch.setenv("FORCE_PAPER_MODE", "0")
        allowed, reason, _ = bot.can_run_paused_research_observation()
        assert allowed is False
        assert reason == "PAPER_ONLY_REQUIRED"
    finally:
        monkeypatch.setenv("FORCE_PAPER_MODE", "1")
        with bot.state_lock:
            bot.state.update(old)


def test_paused_continuous_observation_never_enqueues_child(monkeypatch):
    lab_calls = []
    enqueue_calls = []

    monkeypatch.setattr(bot, "continuous_ai_research_enabled", lambda: True)
    monkeypatch.setattr(bot, "_stamp_shared_ai_lane_verdict", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision", lambda *a, **k: True)
    monkeypatch.setattr(
        bot,
        "_spawn_lab_combo_shadow",
        lambda *args, **kwargs: lab_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        bot,
        "_spawn_combo_lane",
        lambda *args, **kwargs: enqueue_calls.append((args, kwargs)),
    )

    bot.spawn_continuous_lane_from_ai_scan(
        ctx={"trade_id": "paused-observation-1"},
        ai={
            "decision": "APPROVE",
            "approved": True,
            "execution_tier": "STRONG_APPROVE",
            "direction": "SHORT",
            "candidate_direction": "SHORT",
            "long_score": 35,
            "short_score": 65,
            "shared_ai_call_id": "scan-paused-observation-1",
            "shared_ai_call_ts": 1700000000,
        },
        edge_score=5.0,
        features={},
        source_lane=bot.RESEARCH_LANE_AI_SCAN,
        paused_shadow_mode=True,
    )

    assert len(lab_calls) == 1
    assert lab_calls[0][1]["collection_mode"] == "ADMIN_PAUSED_SHADOW"
    assert lab_calls[0][1]["is_counterfactual"] is True
    assert enqueue_calls == []


def test_paused_enabled_lane_reaches_real_lab_helper(monkeypatch):
    """The explicit paused mode must pass the helper's no-order guard."""
    replay_calls = []
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: True)
    monkeypatch.setattr(bot, "lane_orders_allowed", lambda _lane: True)
    monkeypatch.setattr(bot, "invert_signal_active", lambda: False)
    monkeypatch.setattr(bot, "manual_admin_pause_active", lambda: True)
    monkeypatch.setattr(bot, "is_patient_chase_lane", lambda _lane: False)
    monkeypatch.setattr(bot, "get_exit_config_for_lane", lambda _lane: {})
    monkeypatch.setattr(bot, "_enrich_combo_lane_features", lambda features, _ctx: features or {})
    monkeypatch.setattr(bot, "_lane_sized_margin_usdt", lambda _lane, _features: (1.0, 1.0))
    monkeypatch.setattr(
        bot,
        "start_replay_buffer",
        lambda *args, **kwargs: replay_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(bot, "append_replay_tick", lambda *args, **kwargs: None)
    with bot.state_lock:
        old_price = bot.state.get("price")
        bot.state["price"] = 100.0
    try:
        bot._spawn_lab_combo_shadow(
            {"trade_id": "paused-helper-1"},
            {
                "direction": "SHORT",
                "shared_ai_call_id": "scan-paused-helper-1",
                "shared_ai_call_ts": 1700000000,
            },
            5.0,
            bot.RESEARCH_LANE_CONTINUOUS,
            {},
            collection_mode="ADMIN_PAUSED_SHADOW",
            is_counterfactual=True,
        )
    finally:
        with bot.state_lock:
            bot.state["price"] = old_price
    assert len(replay_calls) == 1
    assert replay_calls[0][1]["collection_mode"] == "ADMIN_PAUSED_SHADOW"
    assert replay_calls[0][1]["is_counterfactual"] is True


def test_paused_continuous_verdict_is_never_order_eligible(monkeypatch):
    ledger_calls = []
    monkeypatch.setattr(bot, "continuous_ai_research_enabled", lambda: True)
    monkeypatch.setattr(bot, "continuous_score_gap_execution_tier", lambda _ai: "STRONG_APPROVE")
    monkeypatch.setattr(bot, "allocate_lane_trade_id", lambda _lane: "paused-continuous-ledger-1")
    monkeypatch.setattr(bot, "_stamp_shared_ai_lane_verdict", lambda *a, **k: None)
    monkeypatch.setattr(
        bot,
        "_write_v3_shared_lane_decision",
        lambda *args, **kwargs: ledger_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(bot, "_spawn_lab_combo_shadow", lambda *args, **kwargs: None)

    bot.spawn_continuous_lane_from_ai_scan(
        {"trade_id": "paused-continuous-source"},
        {
            "decision": "APPROVE",
            "direction": "SHORT",
            "candidate_direction": "SHORT",
            "long_score": 20,
            "short_score": 80,
            "shared_ai_call_id": "scan-paused-continuous-1",
            "shared_ai_call_ts": 1700000000,
        },
        edge_score=5.0,
        features={},
        source_lane=bot.RESEARCH_LANE_AI_SCAN,
        paused_shadow_mode=True,
    )

    assert ledger_calls
    assert ledger_calls[0]["execution_disposition"] == "PAUSED_SHADOW_NO_ORDER"
    assert ledger_calls[0]["exact_reason"] == "ADMIN_MANUAL_PAUSED_SHADOW"


def test_scheduler_latches_pause_marker_and_child_fanout_guard():
    scheduler = _function_source("periodic_pipeline_loop")
    process = _function_source("process_signal")
    combo = _function_source("spawn_combo_lanes_from_ai_scan")
    continuous = _function_source("spawn_continuous_lane_from_ai_scan")

    # A fresh session is executable by default.  An explicit persisted pause
    # remains authoritative and is covered by the existing pause-persistence
    # suite; this assertion prevents an accidental fail-closed default.
    assert '"manual_admin_pause": False' in SOURCE
    assert "can_run_paused_research_observation" in scheduler
    assert 'event["paused_shadow_mode"] = True' in scheduler
    assert 'event["research_observation_only"] = True' in scheduler
    assert "paused_shadow_mode: bool = False" in combo
    assert "paused_shadow_mode: bool = False" in continuous
    assert "paused_shadow_mode=paused_shadow_mode" in process
    assert "can_run_paused_research_observation" in process
    assert 'collection_mode="ADMIN_PAUSED_SHADOW"' in combo
    assert 'collection_mode="ADMIN_PAUSED_SHADOW"' in continuous
    assert '"PAUSED_SHADOW_NO_ORDER"' in combo
    assert '"PAUSED_SHADOW_NO_ORDER"' in continuous
