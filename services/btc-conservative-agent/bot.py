# -*- coding: utf-8 -*-
"""
3-Factor Bybit 15m Bot - FINAL RESEARCH-GRADE VERSION v10.9.425
ENFORCED: WINDOW=10 + SINGLE AGGREGATED SOURCE + SOFT FEATURE VALIDATION + EDGE→AI ALIGN + FEATURE VALIDITY LOG-ONLY + PIPELINE LOCK + AVG_VOLUME FIXED + FULL TRACE + RESEARCH DATA COLLECTION + DIRECTION CONSISTENCY (final_direction SINGLE SOURCE OF TRUTH + IMMEDIATE INVERSION) + SINGLE FEATURE SNAPSHOT ENFORCEMENT + HARD AI BLOCK ON INCOMPLETE DATA + DELTA_CHANGE PERSISTENT + STRICT BUFFER GATE + ATOMIC SNAPSHOT + NO ZERO FALLBACKS IN AI
"""
from __future__ import annotations
import os
import time
import math
import threading
import logging
import csv
import zipfile
import io
import json
import uuid
import requests
import glob
import re
import copy
import shutil
import sys
import traceback
from datetime import datetime, timezone
from typing import List, Dict, Any
from flask import Flask, jsonify, render_template_string, request, send_file
import ccxt
import websocket
import signal
import ssl
import hashlib
from queue import Queue, Empty, Full
from collections import deque
import numpy as np
import pytz

# #region agent log
_AGENT_DEBUG_LOG = r"C:\Users\user\Desktop\Final Bots\debug-43f630.log"
_AGENT_DEBUG_LOG_ALT = r"C:\Users\user\Desktop\BOT\debug-43f630.log"
_last_ws_trade_fp = None
_last_ws_trade_fp_ts = 0.0

def _agent_dbg(hypothesis_id, location, message, data=None, run_id="post-fix"):
    try:
        payload = {"sessionId": "43f630", "runId": run_id, "hypothesisId": hypothesis_id, "location": location, "message": message, "data": data or {}, "timestamp": int(time.time() * 1000)}
        line = json.dumps(payload) + "\n"
        for path in (_AGENT_DEBUG_LOG, _AGENT_DEBUG_LOG_ALT):
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
                break
            except Exception:
                continue
    except Exception:
        pass
# #endregion

def nz(x, default=None):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        logger.debug(f"[NZ] value was None/inf/nan - propagating None [PIPELINE ENFORCEMENT]")
        return None
    return float(x)

def safe_float(x, default=None):
    try:
        if x in ("", None, "None", "nan", "NaN"):
            logger.debug(f"[SAFE FLOAT] input was empty/None - propagating None [PIPELINE ENFORCEMENT]")
            return None
        return float(x)
    except:
        logger.debug(f"[SAFE FLOAT] conversion failed - propagating None [PIPELINE ENFORCEMENT]")
        return None

def safe_num(x):
    if x is None:
        logger.debug("[SAFE NUM] input None - returning None [PIPELINE ENFORCEMENT]")
        return None
    try:
        return round(float(x), 2)
    except:
        logger.debug("[SAFE NUM] conversion failed - returning None [PIPELINE ENFORCEMENT]")
        return None

def safe_csv_row(row: dict) -> dict:
    clean = {}
    for k, v in row.items():
        if isinstance(v, float):
            clean[k] = round(v, 6) if not math.isnan(v) else ""
        elif v is None:
            clean[k] = ""
        else:
            clean[k] = v
    return clean

def fmt(x, digits=2):
    try:
        if x is None:
            return "None"
        return f"{float(x):.{digits}f}"
    except:
        return "None"

def compute_r(entry, sl, exit_price):
    if entry <= 0 or sl <= 0 or exit_price <= 0:
        return 0.0
    risk = abs(entry - sl)
    reward = abs(exit_price - entry)
    return reward / risk if risk > 0 else 0.0

def compute_tp(entry, side, target_pct, leverage):
    move = (target_pct / 100.0) / leverage
    if side == "LONG":
        return entry * (1 + move)
    else:
        return entry * (1 - move)

def _feature_bundle(pos: dict, master: dict = None):
    master = master or {}
    features = copy.deepcopy(master.get("features") or pos.get("features") or {})
    context = copy.deepcopy(master.get("context") or pos.get("context") or {})
    return features, context

def _compute_momentum_metric(features: dict) -> float:
    ret_1m = abs(float(features.get("ret_1m") or 0))
    ret_5m = abs(float(features.get("ret_5m") or 0))
    velocity = abs(float(features.get("velocity") or 0))
    return round(min(max(ret_1m * 5000, ret_5m * 2000, velocity * 8000), 1.0), 6)

def _compute_volatility_metric(features: dict) -> float:
    return round(abs(float(features.get("candle_range") or features.get("volatility") or 0)), 6)

def _compute_entry_delay_sec(pos: dict, master: dict = None) -> float:
    if pos.get("entry_delay_sec") is not None:
        return round(float(pos.get("entry_delay_sec")), 3)
    fill_ts = pos.get("entry_ts")
    signal_ts = None
    if master:
        signal_ts = master.get("timing", {}).get("signal_ts") or master.get("created_ts_ts")
    if signal_ts and fill_ts:
        return round(max(0.0, float(fill_ts) - float(signal_ts)), 3)
    return 0.0

def _build_open_position(order: dict, signal: dict, ai: dict = None) -> dict:
    ai = ai or {}
    entry = order.get("limit_price") or order.get("entry") or signal.get("signal_price") or state.get("price")
    direction = order.get("signal_dir") or signal.get("final_direction")
    signal_price = order.get("signal_price") or signal.get("signal_price") or entry
    fill_ts = time.time()
    order_created = signal.get("order_created_ts") or order.get("created_ts") or signal.get("created_ts_ts")
    entry_delay_sec = max(0.0, fill_ts - float(order_created)) if order_created else 0.0
    features, context = _feature_bundle(signal, signal)
    return {
        "trade_id": order.get("trade_id") or signal.get("trade_id"),
        "dir": direction,
        "entry": entry,
        "qty": order.get("qty") or signal.get("qty"),
        "leverage": state.get("leverage", 20),
        "entry_ts": fill_ts,
        "sl": entry * (1 - sl_price_pct(state.get("leverage", 20))) if direction == "LONG" else entry * (1 + sl_price_pct(state.get("leverage", 20))),
        "tp": compute_tp(entry, direction, TP_TARGET_PCT, state.get("leverage", 20)),
        "regime_birth": signal.get("regime", "UNKNOWN"),
        "strategy_birth": "SR",
        "conf": signal.get("ai_win_prob", ai.get("win_prob", 0)),
        "max_pnl_pct": 0.0,
        "tp_stage": 0,
        "entry_type": order.get("entry_type", "LIMIT"),
        "ai_win_prob": signal.get("ai_win_prob", ai.get("win_prob")),
        "ai_approved": (ai.get("decision") == "APPROVE" or signal.get("ai_decision") == "APPROVE"),
        "ai_source": signal.get("ai_source", ai.get("source", "UNKNOWN")),
        "signal_conf": signal.get("ai_win_prob", ai.get("win_prob", 0)),
        "signal_regime": signal.get("regime", "UNKNOWN"),
        "signal_price": signal_price,
        "be_activated": False,
        "protection_active": False,
        "tp_placed": False,
        "status": "OPEN",
        "maker_fees": 0.0,
        "taker_fees": 0.0,
        "funding_fees": 0.0,
        "entry_fee_type": "MAKER" if order.get("fee_type") == "MAKER" else "TAKER",
        "exit_fee_type": "UNKNOWN",
        "features": features,
        "context": context,
        "controls": copy.deepcopy(signal.get("controls") or {}),
        "decision": copy.deepcopy(signal.get("decision") or {}),
        "edge_score_at_entry": signal.get("edge_score_at_entry"),
        "entry_delay_sec": round(entry_delay_sec, 3),
        "entry_slippage": round(abs(float(entry) - float(signal_price)), 6),
        "entry_sr_state": context.get("sr_state", "UNKNOWN"),
        "entry_dist_to_resistance": context.get("dist_to_resistance", 0.0),
        "entry_dist_to_support": context.get("dist_to_support", 0.0),
        "last_funding_accrual_ts": fill_ts,
        "bull_score_at_entry": signal.get("bull_score_at_entry") or (signal.get("ai_factors") or {}).get("bull_score", 0),
        "bear_score_at_entry": signal.get("bear_score_at_entry") or (signal.get("ai_factors") or {}).get("bear_score", 0),
        "ai_factors": copy.deepcopy(signal.get("ai_factors", {})),
        "entry_thesis": capture_entry_thesis(signal),
        "margin_usdt": float(signal.get("margin_usdt") or FIXED_MARGIN_USDT),
        "conviction_spread": signal.get("conviction_spread"),
    }

def compute_live_factor_scores(mc: dict):
    ms = mc.get("market_structure", {}) or {}
    ts = mc.get("trend_strength", {}) or {}
    mtf = mc.get("multi_tf", {}) or {}
    ema = mc.get("ema_alignment", {}) or {}
    struct = float(ms.get("structure_score") or 0)
    bull = bear = 3
    if struct > 0:
        bull += min(4, int(struct))
    elif struct < 0:
        bear += min(4, int(-struct))
    if float(ts.get("adx") or 0) >= 25:
        if struct > 0:
            bull += 1
        elif struct < 0:
            bear += 1
    agree = mtf.get("agreement", "")
    if agree == "BULL_ALIGNED":
        bull += 2
    elif agree == "BEAR_ALIGNED":
        bear += 2
    vwap_dist = float(ts.get("vwap_distance_pct") or 0)
    if vwap_dist > 0:
        bull += 1
    elif vwap_dist < 0:
        bear += 1
    ema_slope = float(ema.get("ema_fast_slope_pct") or 0)
    if ema_slope > 0:
        bull += 1
    elif ema_slope < 0:
        bear += 1
    return int(bull), int(bear)

def capture_entry_thesis(signal: dict) -> dict:
    mc = (
        signal.get("context", {}).get("market_context")
        or signal.get("market_context")
        or signal.get("market_structure")
        or {}
    )
    if not isinstance(mc, dict):
        mc = {}
    mtf = mc.get("multi_tf", {}) if isinstance(mc.get("multi_tf"), dict) else {}
    ms = mc.get("market_structure", {}) if isinstance(mc.get("market_structure"), dict) else {}
    ts = mc.get("trend_strength", {}) if isinstance(mc.get("trend_strength"), dict) else {}
    return {
        "bull_score": int(signal.get("bull_score_at_entry") or 0),
        "bear_score": int(signal.get("bear_score_at_entry") or 0),
        "structure_score": ms.get("structure_score"),
        "mtf_agreement": mtf.get("agreement"),
        "structure_bias": ms.get("structure_bias"),
        "adx": ts.get("adx"),
        "captured_ts": time.time(),
    }

def get_profit_lock_floor(peak_pct: float):
    if peak_pct is None or peak_pct < TRAIL_LADDER[0][0]:
        return None
    floor = None
    for trigger, lock in TRAIL_LADDER:
        if peak_pct >= trigger:
            floor = lock
    if peak_pct >= PEAK_NEVER_LOSER_MIN_PEAK:
        floor = max(floor or 0, PEAK_NEVER_LOSER_FLOOR)
    return floor

def unrealized_margin_pct(pos: dict, price: float) -> float:
    entry = pos.get("entry", 0)
    if entry <= 0 or price <= 0:
        return 0.0
    dir_factor = 1 if pos.get("dir") == "LONG" else -1
    price_move = ((price - entry) / entry) * dir_factor
    return price_move * pos.get("leverage", 20) * 100

def check_thesis_invalidation(pos: dict, price: float) -> bool:
    if not THESIS_INVALIDATION_ENABLED:
        return False
    thesis = pos.get("entry_thesis") or {}
    if not thesis:
        return False
    unreal_pct = unrealized_margin_pct(pos, price)
    age_sec = time.time() - pos.get("entry_ts", 0)
    peak = pos.get("max_pnl_pct", 0.0)
    lock_floor = get_profit_lock_floor(peak)
    if lock_floor is not None and unreal_pct > lock_floor:
        return False
    if unreal_pct > THESIS_EXIT_IF_ABOVE_UNREAL_PCT:
        return False
    fast_cut = unreal_pct <= THESIS_FAST_EXIT_UNREAL_PCT
    if fast_cut:
        logger.info(
            f"[THESIS_FAST_CUT] trade_id={pos.get('trade_id')} dir={pos.get('dir')} "
            f"unreal={unreal_pct:.1f}% age={age_sec/60:.1f}m [PIPELINE ENFORCEMENT]"
        )
        close_position(pos, "THESIS_FAST_CUT")
        return True
    if age_sec < THESIS_MIN_AGE_SEC:
        return False
    update_market_context()
    with state_lock:
        mc = copy.deepcopy(state.get("market_context", {}))
    ms = mc.get("market_structure", {}) or {}
    mtf = mc.get("multi_tf", {}) or {}
    cur_bull, cur_bear = compute_live_factor_scores(mc)
    entry_bull = int(thesis.get("bull_score", 0))
    entry_bear = int(thesis.get("bear_score", 0))
    entry_mtf = thesis.get("mtf_agreement")
    cur_mtf = mtf.get("agreement")
    entry_struct = thesis.get("structure_score")
    cur_struct = ms.get("structure_score")
    direction = pos.get("dir")
    flip = False
    m = THESIS_SCORE_FLIP_MARGIN
    decay = THESIS_EARLY_DECAY_DELTA
    entry_mtf_mixed = entry_mtf in ("MIXED", None, "")
    if direction == "LONG":
        if cur_bear >= cur_bull + m:
            flip = True
        elif cur_bull <= entry_bull - decay and cur_bear >= entry_bear + decay:
            flip = True
        elif cur_bull <= entry_bull - m and cur_bear >= entry_bear + m:
            flip = True
        if not entry_mtf_mixed and entry_mtf == "BULL_ALIGNED" and cur_mtf in ("BEAR_ALIGNED", "CONFLICTED"):
            flip = True
        if entry_struct is not None and cur_struct is not None and cur_struct <= -2 and entry_struct >= 2:
            flip = True
    elif direction == "SHORT":
        if cur_bull >= cur_bear + m:
            flip = True
        elif cur_bear <= entry_bear - decay and cur_bull >= entry_bull + decay:
            flip = True
        elif cur_bear <= entry_bear - m and cur_bull >= entry_bull + m:
            flip = True
        if not entry_mtf_mixed and entry_mtf == "BEAR_ALIGNED" and cur_mtf in ("BULL_ALIGNED", "CONFLICTED"):
            flip = True
        if entry_struct is not None and cur_struct is not None and cur_struct >= 2 and entry_struct <= -2:
            flip = True
    if flip:
        logger.info(
            f"[THESIS_INVALIDATED] trade_id={pos.get('trade_id')} dir={direction} "
            f"unreal={unreal_pct:.1f}% age={age_sec/60:.1f}m fast={fast_cut} "
            f"entry bull/bear={entry_bull}/{entry_bear} live={cur_bull}/{cur_bear} "
            f"mtf {entry_mtf}->{cur_mtf} struct {entry_struct}->{cur_struct} "
            f"[PIPELINE ENFORCEMENT]"
        )
        close_position(pos, "THESIS_INVALIDATED")
        return True
    return False

def count_directional_exposure(direction: str) -> int:
    direction = direction.upper()
    with trade_lock:
        open_n = sum(1 for p in open_positions if p.get("dir") == direction)
        pending_n = sum(
            1 for o in pending_orders
            if o.get("status") == "PENDING"
            and (o.get("side") or o.get("signal_dir", "")).upper() == direction
        )
    return open_n + pending_n

def is_clustered_entry(direction: str, price: float) -> bool:
    if price is None or price <= 0:
        return False
    with trade_lock:
        for p in open_positions:
            if p.get("dir") == direction:
                entry = p.get("entry", 0)
                if entry > 0 and abs(price - entry) / entry < CLUSTER_MIN_DIST_PCT:
                    return True
        for o in pending_orders:
            if o.get("status") != "PENDING":
                continue
            side = (o.get("side") or o.get("signal_dir", "")).upper()
            if side != direction:
                continue
            ref = o.get("limit_price") or o.get("signal_price") or 0
            if ref > 0 and abs(price - ref) / ref < CLUSTER_MIN_DIST_PCT:
                return True
    return False

def evaluate_entry_location_filter(direction: str, ctx: dict, ai: dict):
    dist_sup = nz(ctx.get("dist_to_support"))
    if dist_sup <= 0:
        with state_lock:
            dist_sup = nz(state.get("support_resistance", {}).get("dist_to_support", 1))
    mc = ctx.get("market_context") or {}
    ms = mc.get("market_structure", {}) or {}
    struct = float(ms.get("structure_score") or 0)
    mtf = (mc.get("multi_tf") or {}).get("agreement")
    bull = int(ai.get("bull_score", 0) or 0)
    bear = int(ai.get("bear_score", 0) or 0)

    if direction == "LONG":
        if struct <= -3:
            return True, "LONG_BLOCKED_BEAR_STRUCTURE"
        if dist_sup < LONG_NEAR_SUPPORT_MAX_DIST:
            if bull >= bear + LONG_NEAR_SUPPORT_MIN_BULL_SPREAD and mtf == "BULL_ALIGNED":
                return False, None
            return True, "LONG_BLOCKED_NEAR_SUPPORT_WEAK"

    if direction == "SHORT" and BLOCK_SHORT_NEAR_SUPPORT and dist_sup < SHORT_NEAR_SUPPORT_MAX_DIST:
        if (
            bear >= bull + SHORT_NEAR_SUPPORT_MIN_BEAR_SPREAD
            and mtf == "BEAR_ALIGNED"
            and struct <= SHORT_NEAR_SUPPORT_MIN_STRUCT
        ):
            return False, None
        return True, "SHORT_BLOCKED_NEAR_SUPPORT_WEAK"

    return False, None

def compute_directional_spread(direction: str, ai: dict) -> int:
    bull = int(ai.get("bull_score", 0) or 0)
    bear = int(ai.get("bear_score", 0) or 0)
    if direction == "LONG":
        return bull - bear
    return bear - bull

def conviction_size_multiplier(spread: int) -> float:
    if spread >= CONVICTION_SPREAD_FULL:
        return 1.0
    if spread >= CONVICTION_SPREAD_HALF:
        return 0.5
    if spread >= CONVICTION_SPREAD_QUARTER:
        return 0.25
    return 0.0

def get_regime_risk_profile() -> dict:
    with state_lock:
        regime = str(state.get("regime", "RANGE")).upper()
        sr_state = str((state.get("support_resistance") or {}).get("sr_state", "")).upper()
        mc = state.get("market_context") or {}
    adx = float((mc.get("trend_strength") or {}).get("adx") or 0)
    if regime == "RANGE" or "COMPRESSION" in sr_state:
        return {"max_active": 2, "max_long": 2, "max_short": 2, "size_mult": 0.5, "label": "RANGE"}
    if adx >= 25 and regime in ("BULL", "BEAR"):
        return {"max_active": 4, "max_long": 3, "max_short": 3, "size_mult": 1.0, "label": "TREND_STRONG"}
    return {"max_active": 4, "max_long": 3, "max_short": 3, "size_mult": 1.0, "label": "TREND"}

def resolve_entry_margin_usdt(direction: str, ai: dict, ctx: dict):
    spread = compute_directional_spread(direction, ai)
    conv = conviction_size_multiplier(spread)
    if conv <= 0:
        return None, f"CONVICTION_SPREAD_LOW_{spread}"
    mc = ctx.get("market_context") or {}
    adx = float((mc.get("trend_strength") or {}).get("adx") or 0)
    if adx < ADX_BLOCK_NEW_ENTRY:
        return None, f"ADX_NO_ENTRY_{adx:.1f}"
    adx_mult = ADX_HALF_SIZE_MULT if adx < ADX_HALF_SIZE_BELOW else 1.0
    regime_prof = get_regime_risk_profile()
    margin = round(FIXED_MARGIN_USDT * conv * adx_mult * regime_prof["size_mult"], 4)
    margin = max(FIXED_MARGIN_USDT * 0.1, min(margin, FIXED_MARGIN_USDT))
    return margin, None

def evaluate_entry_quality_filter(direction: str, ctx: dict, ai: dict):
    mc = ctx.get("market_context") or {}
    mtf = (mc.get("multi_tf", {}) or {}).get("agreement", "")
    if direction == "LONG" and mtf != "BULL_ALIGNED":
        return True, f"LONG_REQUIRES_BULL_MTF_{mtf or 'UNKNOWN'}"
    if direction == "SHORT":
        if mtf == "BULL_ALIGNED":
            return True, "SHORT_BLOCKED_BULL_MTF"
        if mtf not in ("BEAR_ALIGNED",):
            return True, f"SHORT_REQUIRES_BEAR_MTF_{mtf or 'UNKNOWN'}"
    return False, None

def risk_trading_allowed() -> bool:
    now = time.time()
    with state_lock:
        pause_until = state.get("loss_pause_until", 0)
        if pause_until > now:
            return False
        if pause_until > 0 and pause_until <= now:
            state["loss_pause_until"] = 0
            if state.get("execution_reason") == "LOSS_STREAK":
                set_execution_paused("")
        if state.get("daily_pnl_usd", 0) <= -DAILY_DRAWDOWN_PAUSE_USD:
            if not state.get("execution_paused") or state.get("execution_reason") != "DAILY_DRAWDOWN":
                set_execution_paused("DAILY_DRAWDOWN")
            return False
        if state.get("consecutive_losses", 0) >= CONSECUTIVE_LOSS_PAUSE:
            if state.get("loss_pause_until", 0) <= now:
                state["loss_pause_until"] = now + LOSS_PAUSE_SEC
                set_execution_paused("LOSS_STREAK")
            return False
    if state.get("execution_paused") and state.get("execution_reason") in ("DAILY_DRAWDOWN", "LOSS_STREAK"):
        return False
    return True

def ensure_directional_capacity(direction: str) -> bool:
    direction = direction.upper()
    prof = get_regime_risk_profile()
    cap = prof["max_long"] if direction == "LONG" else prof["max_short"]
    cap = min(cap, MAX_LONGS if direction == "LONG" else MAX_SHORTS)
    return count_directional_exposure(direction) < cap

def get_effective_max_active_signals() -> int:
    prof = get_regime_risk_profile()
    with state_lock:
        user_max = state.get("max_active_signals") or MAX_CONCURRENT_POSITIONS_DEFAULT
    return min(int(user_max), int(prof["max_active"]))

def _apply_position_exits(pos: dict, price: float, now: float = None):
    if now is None:
        now = time.time()
    unreal_pct = unrealized_margin_pct(pos, price)
    pos["max_pnl_pct"] = max(pos.get("max_pnl_pct", 0.0), unreal_pct)
    if pos.get("max_drawdown", 0) is None or unreal_pct < pos.get("max_drawdown", 0):
        pos["max_drawdown"] = min(pos.get("max_drawdown", 0.0), unreal_pct)

    if state.get("early_fail_enabled", True) and unreal_pct <= EARLY_FAIL_PCT_THRESHOLD:
        logger.info(f"[EXIT TRIGGER] EARLY_FAIL trade_id={pos.get('trade_id')} pnl={fmt(unreal_pct)} [PIPELINE ENFORCEMENT]")
        close_position(pos, "EARLY_FAIL")
        return True

    if (pos.get("dir") == "LONG" and price <= pos.get("sl", 0)) or (pos.get("dir") == "SHORT" and price >= pos.get("sl", 0)):
        logger.info(f"[EXIT TRIGGER] STOP_LOSS trade_id={pos.get('trade_id')} [PIPELINE ENFORCEMENT]")
        close_position(pos, "STOP_LOSS")
        return True

    if check_thesis_invalidation(pos, price):
        return True

    peak = pos.get("max_pnl_pct", 0.0)
    lock_floor = get_profit_lock_floor(peak)
    if lock_floor is not None and peak >= TRAIL_LADDER[0][0] and unreal_pct <= lock_floor:
        logger.info(
            f"[EXIT TRIGGER] PROFIT_LOCK_LADDER trade_id={pos.get('trade_id')} peak={peak:.1f}% "
            f"lock={lock_floor:.1f}% now={unreal_pct:.1f}% [PIPELINE ENFORCEMENT]"
        )
        close_position(pos, "PROFIT_LOCK_LADDER")
        return True

    tp_price = pos.get("tp", 0)
    if tp_price > 0:
        if (pos.get("dir") == "LONG" and price >= tp_price) or (pos.get("dir") == "SHORT" and price <= tp_price):
            logger.info(f"[EXIT TRIGGER] TAKE_PROFIT_EMERGENCY trade_id={pos.get('trade_id')} [PIPELINE ENFORCEMENT]")
            close_position(pos, "TAKE_PROFIT")
            return True

    if FIXED_TIME_EXIT_ENABLED:
        time_in_trade = now - pos.get("entry_ts", 0)
        if time_in_trade > MAX_POSITION_AGE_SEC:
            logger.info(f"[EXIT TRIGGER] TIME_EXIT trade_id={pos.get('trade_id')} duration={(time_in_trade/60):.1f}min [PIPELINE ENFORCEMENT]")
            close_position(pos, "TIME_EXIT")
            return True
    else:
        time_in_trade = now - pos.get("entry_ts", 0)
        if time_in_trade > EMERGENCY_MAX_HOLD_SEC:
            logger.warning(
                f"[EXIT TRIGGER] EMERGENCY_MAX_HOLD trade_id={pos.get('trade_id')} "
                f"duration={(time_in_trade/3600):.1f}h [PIPELINE ENFORCEMENT]"
            )
            close_position(pos, "EMERGENCY_MAX_HOLD")
            return True
    return False

def set_execution_paused(reason: str):
    global last_console_update
    with state_lock:
        if reason == "":
            state["execution_paused"] = False
            state["execution_reason"] = ""
            state["_pause_priority"] = 0
            state["ws_stale_count"] = 0
            logger.info("[RECOVERY] Soft recovery - state preserved [PIPELINE ENFORCEMENT]")
            return
        priority = PAUSE_PRIORITIES.get(reason, 0)
        current = state.get("_pause_priority", 0)
        if priority >= current:
            state["execution_paused"] = True
            state["execution_reason"] = reason
            state["_pause_priority"] = priority
            logger.warning(f"[EXECUTION] paused: {reason} [PIPELINE ENFORCEMENT]")
            return

def get_execution_status() -> str:
    system_health_check()
    with state_lock:
        system_ready = is_system_ready()
        paused = state.get("execution_paused", False)
        live_armed = state.get("live_armed", False)
        mode = state.get("strategy_mode", "RESEARCH")
        if mode == "RESEARCH":
            return "RESEARCH_ALLOW" if not paused else "BLOCKED"
        return "ACTIVE" if (system_ready and not paused and live_armed) else "BLOCKED"

def get_display_balance():
    with state_lock:
        if not state.get("live_armed", False):
            return STARTING_BALANCE
        return state.get("account_balance", STARTING_BALANCE)

def _pending_trade_ids():
    with trade_lock:
        return {o.get("trade_id") for o in pending_orders if o.get("trade_id") and o.get("status") == "PENDING"}

def is_terminal_signal(sig: dict) -> bool:
    if not isinstance(sig, dict):
        return False
    st = sig.get("status")
    outcome = sig.get("outcome") or sig.get("exit_reason")
    return st in TERMINAL_SIGNAL_STATUSES or outcome in TERMINAL_SIGNAL_OUTCOMES

def _open_trade_ids():
    with trade_lock:
        return {p.get("trade_id") for p in open_positions if p.get("trade_id")}

def expire_signal_for_order(order: dict, reason: str = "TTL_EXPIRED"):
    tid = order.get("trade_id")
    if not tid:
        return False
    with trade_lock:
        master = trades_map.get(tid, {}).get("signal_ref")
        if not master:
            _agent_dbg("H1", "expire_signal_for_order", "no_signal_ref", {"trade_id": tid, "reason": reason})
            return False
        prev_status = master.get("status")
        master["status"] = "EXPIRED"
        master["outcome"] = reason
        master["exit_reason"] = reason
    with state_lock:
        if state.get("pending_trade_id") == tid:
            state["pending_trade_id"] = None
    _agent_dbg("H1", "expire_signal_for_order", "signal_expired", {"trade_id": tid, "prev_status": prev_status, "reason": reason})
    pipeline_state_sync()
    return True

def get_active_signal_count():
    purge_dead_pending_orders()
    reconcile_stale_signals()
    with trade_lock:
        pending_count = len([o for o in pending_orders if o.get("status") == "PENDING"])
        open_count = len(open_positions)
        list_len = len(pending_orders)
        active = pending_count + open_count
        exposure = [{"trade_id": o.get("trade_id"), "kind": "pending", "status": o.get("status")} for o in pending_orders if o.get("status") == "PENDING"]
        exposure += [{"trade_id": p.get("trade_id"), "kind": "open"} for p in open_positions]
        stale_map = len([s for s in trades_map.values() if (s.get("signal_ref") or {}).get("status") in ("ORDERED", "ACTIVE", "PENDING") and (s.get("signal_ref") or {}).get("trade_id") not in {e.get("trade_id") for e in exposure}])
    _agent_dbg("H1", "get_active_signal_count", "counted", {"active": active, "pending_pending_status": pending_count, "pending_list_len": list_len, "open_positions": open_count, "stale_map_orphans": stale_map, "exposure": exposure})
    if active == 0 and (pending_count + open_count) == 0:
        logger.debug(f"[EXECUTION FIX {EXECUTION_FIX_VERSION}] exposure_count=0 pending=0 positions=0")
    return active

def sync_cooldown_debug_state():
    now = time.time()
    ai_rem = max(0, AI_COOLDOWN_SECONDS - (now - state.get("last_ai_call_ts", 0)))
    sig_rem = max(0, GLOBAL_SIGNAL_COOLDOWN - (now - state.get("last_signal_create_ts", 0)))
    with state_lock:
        state["debug_state"]["ai_cooldown_active"] = ai_rem > 0
        state["debug_state"]["cooldown_remaining_ai"] = int(ai_rem)
        state["debug_state"]["signal_cooldown_active"] = sig_rem > 0
        state["debug_state"]["cooldown_remaining_signal"] = int(sig_rem)
        if ai_rem <= 0 and sig_rem <= 0:
            sr = state["debug_state"].get("skip_reason")
            if sr in ("AI_COOLDOWN_ACTIVE", "GLOBAL_COOLDOWN", None):
                state["debug_state"]["skip_reason"] = None
    _agent_dbg("H4", "sync_cooldown_debug_state", "synced", {"ai_rem": int(ai_rem), "sig_rem": int(sig_rem), "skip_reason": state.get("debug_state", {}).get("skip_reason")})

def purge_dead_pending_orders():
    removed = 0
    with trade_lock:
        dead = [o for o in pending_orders if not isinstance(o, dict) or o.get("status") != "PENDING"]
        for o in dead:
            if o in pending_orders:
                pending_orders.remove(o)
                removed += 1
    if removed:
        logger.info(f"[EXECUTION FIX {EXECUTION_FIX_VERSION}] purged {removed} dead pending_orders (non-PENDING)")
        _agent_dbg("H3", "purge_dead_pending_orders", "purged", {"removed": removed, "remaining": len(pending_orders)})
    return removed

def execution_allowed() -> bool:
    if not risk_trading_allowed():
        with state_lock:
            state["execution_reason"] = state.get("execution_reason") or "RISK_PAUSE"
        logger.warning(f"[EXECUTION BLOCK] risk pause reason={state.get('execution_reason')} [PIPELINE ENFORCEMENT]")
        return False
    with trade_lock:
        active = get_active_signal_count()
    with state_lock:
        max_pos = state.get("max_active_signals") or MAX_CONCURRENT_POSITIONS_DEFAULT
    if active >= max_pos:
        state["execution_reason"] = "MAX_ACTIVE_SIGNALS"
        logger.warning(f"[LIMIT] Max active signals reached: {active}/{max_pos} [PIPELINE ENFORCEMENT]")
        return False
    status = get_execution_status()
    if status not in ["ACTIVE", "RESEARCH_ALLOW"]:
        state["execution_reason"] = status
        logger.warning(f"[EXECUTION BLOCK] status={status} [PIPELINE ENFORCEMENT]")
        return False
    state["execution_reason"] = "ALLOWED"
    return True

def clear_pending_trade():
    with state_lock:
        state["pending_trade_id"] = None

def safe_clear_pending():
    with state_lock:
        if state.get("pending_trade_id"):
            logger.warning("[FORCE CLEAR] pending_trade_id stuck - cleared [PIPELINE ENFORCEMENT]")
            state["pending_trade_id"] = None

def map_signal_to_exchange_side(final_direction: str) -> str:
    if final_direction == "LONG":
        return "buy"
    elif final_direction == "SHORT":
        return "sell"
    else:
        raise ValueError(f"Invalid final_direction: {final_direction}")

melbourne_tz = pytz.timezone("Australia/Melbourne")

def to_melbourne_time(utc_iso_str):
    try:
        dt = datetime.fromisoformat(utc_iso_str.replace("Z", "+00:00"))
        return dt.astimezone(melbourne_tz).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return utc_iso_str

DEBUG_LOG_BUFFER = deque(maxlen=1000)

def store_debug_snapshot(stage: str, data: dict):
    entry = {"ts": utc_iso(), "stage": stage, "data": data}
    DEBUG_LOG_BUFFER.append(entry)
    logger.info(f"[DEBUG SNAPSHOT STORED] {stage} [PIPELINE ENFORCEMENT]")

orderflow = {"buy_volume": 0.0, "sell_volume": 0.0, "delta": 0.0, "imbalance": 0.0, "last_update": 0.0, "prev_delta": 0.0}
volume_buffer = deque(maxlen=200)
price_buffer = deque(maxlen=200)
recent_trades = deque(maxlen=50)
ret_1m_buffer = deque(maxlen=20)
ret_5m_buffer = deque(maxlen=100)
velocity_buffer = deque(maxlen=200)
delta_buffer = deque(maxlen=200)
delta_change_buffer = deque(maxlen=200)
imbalance_buffer = deque(maxlen=200)
candle_range_buffer = deque(maxlen=200)
wick_ratio_buffer = deque(maxlen=200)
body_ratio_buffer = deque(maxlen=200)

LAST_AI_PAYLOAD = {}
LAST_AI_TIMESTAMP = None

WINDOW_SIZE = 10

def is_buffer_ready():
    ready = (
        len(price_buffer) >= WINDOW_SIZE and
        len(volume_buffer) >= WINDOW_SIZE and
        len(delta_buffer) >= WINDOW_SIZE and
        len(imbalance_buffer) >= WINDOW_SIZE and
        len(candle_range_buffer) >= WINDOW_SIZE and
        len(wick_ratio_buffer) >= WINDOW_SIZE and
        len(body_ratio_buffer) >= WINDOW_SIZE
    )
    if not ready:
        logger.warning(f"[BUFFER GATE] NOT READY: price={len(price_buffer)} vol={len(volume_buffer)} delta={len(delta_buffer)} imb={len(imbalance_buffer)} candle_range={len(candle_range_buffer)} wick={len(wick_ratio_buffer)} body={len(body_ratio_buffer)} [PIPELINE ENFORCEMENT]")
    else:
        logger.info(f"[BUFFER GATE] READY: all buffers >= {WINDOW_SIZE} [PIPELINE ENFORCEMENT]")
    return ready

def get_aggregated(arr, default=None):
    if len(arr) < WINDOW_SIZE:
        logger.debug(f"[AGGREGATION] buffer too short ({len(arr)} < {WINDOW_SIZE}) - returning None [PIPELINE ENFORCEMENT]")
        return None
    val = np.mean(list(arr)[-WINDOW_SIZE:])
    logger.debug(f"[AGGREGATION] computed mean over last {WINDOW_SIZE} = {val} [PIPELINE ENFORCEMENT]")
    return val

def get_sum(arr):
    if len(arr) < WINDOW_SIZE:
        logger.debug(f"[AGGREGATION] buffer too short ({len(arr)} < {WINDOW_SIZE}) - returning None [PIPELINE ENFORCEMENT]")
        return None
    val = np.sum(list(arr)[-WINDOW_SIZE:])
    logger.debug(f"[AGGREGATION] computed sum over last {WINDOW_SIZE} = {val} [PIPELINE ENFORCEMENT]")
    return val

def reset_orderflow():
    global orderflow
    orderflow = {"buy_volume": 0.0, "sell_volume": 0.0, "delta": 0.0, "imbalance": 0.0, "last_update": 0.0, "prev_delta": 0.0}

def compute_exposure():
    total = 0.0
    for pos in open_positions:
        if isinstance(pos, dict):
            total += pos.get("qty", 0) * pos.get("entry", 0)
    return total

FIXED_MARGIN_USDT = 20.0
MAX_SL_MARGIN_PCT = 30.0

def sl_price_pct(leverage: int = None) -> float:
    lev = max(int(leverage or state.get("leverage", 20) or 20), 1)
    return MAX_SL_MARGIN_PCT / (lev * 100.0)

SL_PCT = sl_price_pct(20)
# Fee profile: BITFINEX_ZERO = 0% maker/taker (Bitfinex default since Dec 2025 on spot/margin/derivatives).
# BYBIT_DEFAULT = prior Bybit-style sim (~0.02% maker / 0.06% taker on notional). Funding not modeled in either.
EXCHANGE_FEE_PROFILE = "BITFINEX_ZERO"
_BYBIT_MAKER_FEE_PCT = 0.0002
_BYBIT_TAKER_FEE_PCT = 0.0006

def get_trading_fee_rates():
    if EXCHANGE_FEE_PROFILE == "BITFINEX_ZERO":
        return 0.0, 0.0
    return _BYBIT_MAKER_FEE_PCT, _BYBIT_TAKER_FEE_PCT

MAKER_FEE_PCT, TAKER_FEE_PCT = get_trading_fee_rates()
# Bitfinex perp funding (BTC USDt perpetual via ccxt). Trading fees = 0; funding still applies.
FUNDING_SIMULATION_ENABLED = True
BITFINEX_PERP_SYMBOL = "BTC/USDT:USDT"
FUNDING_INTERVAL_HOURS = 8
FUNDING_REFRESH_SEC = 60
FUNDING_RATE_CAP_PER_8H = 0.001
_last_funding_refresh_ts = 0.0
bitfinex_public = None

def get_bitfinex_public():
    global bitfinex_public
    if bitfinex_public is None:
        bitfinex_public = ccxt.bitfinex({"enableRateLimit": True})
        bitfinex_public.load_markets()
    return bitfinex_public

def synthetic_funding_rate_8h():
    """Fallback 8h rate from premium (price vs EMA200) + orderflow when API unavailable."""
    with state_lock:
        price = nz(state.get("price"))
        ema200 = nz(state.get("ema_status", {}).get("ema200"))
        imbalance = nz(orderflow.get("imbalance", 0))
        delta = nz(orderflow.get("delta", 0))
    if price <= 0 or ema200 <= 0:
        return 0.0
    premium = (price - ema200) / ema200
    rate = (premium * 0.15) + (imbalance * 0.00005) + (0.00002 if delta > 0 else -0.00002 if delta < 0 else 0)
    return max(-FUNDING_RATE_CAP_PER_8H, min(FUNDING_RATE_CAP_PER_8H, rate))

def refresh_funding_state(force: bool = False):
    """Pull live Bitfinex perp funding; update state['funding'] for sim + AI."""
    global _last_funding_refresh_ts
    if not FUNDING_SIMULATION_ENABLED:
        return
    now = time.time()
    if not force and now - _last_funding_refresh_ts < FUNDING_REFRESH_SEC:
        return
    rate = None
    next_ts = None
    mark = None
    index = None
    source = "SYNTHETIC"
    try:
        fr = get_bitfinex_public().fetch_funding_rate(BITFINEX_PERP_SYMBOL)
        rate = fr.get("fundingRate")
        if rate is None:
            rate = fr.get("nextFundingRate")
        next_ms = fr.get("nextFundingTimestamp") or fr.get("fundingTimestamp")
        if next_ms:
            next_ts = float(next_ms) / 1000.0
        mark = fr.get("markPrice")
        index = fr.get("indexPrice")
        source = "BITFINEX_LIVE"
    except Exception as e:
        logger.warning(f"[FUNDING] Bitfinex fetch failed: {e} — using synthetic [PIPELINE ENFORCEMENT]")
    if rate is None:
        rate = synthetic_funding_rate_8h()
        source = "SYNTHETIC"
    else:
        rate = float(rate)
    if next_ts is None or next_ts < now:
        next_ts = now + FUNDING_INTERVAL_HOURS * 3600
    with state_lock:
        state["funding"] = {
            "rate": rate,
            "rate_pct_per_8h": round(rate * 100, 5),
            "next_time": next_ts,
            "mark_price": mark,
            "index_price": index,
            "interval_hours": FUNDING_INTERVAL_HOURS,
            "source": source,
            "longs_pay": rate > 0,
            "updated_ts": now,
        }
    _last_funding_refresh_ts = now
    logger.info(
        f"[FUNDING] {source} rate_8h={rate*100:.5f}% next_settlement={datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat()} "
        f"longs_pay={rate > 0} [PIPELINE ENFORCEMENT]"
    )

def funding_cost_for_position(pos: dict, rate_8h: float, hours: float) -> float:
    """
    USD funding cashflow for holding `hours` at `rate_8h` (per 8h period).
    Positive = cost to trader; negative = rebate.
    LONG pays when rate > 0; SHORT receives when rate > 0.
    """
    notional = nz(pos.get("entry")) * nz(pos.get("qty"))
    if notional <= 0 or hours <= 0:
        return 0.0
    dir_sign = 1.0 if pos.get("dir") == "LONG" else -1.0
    return dir_sign * notional * rate_8h * (hours / FUNDING_INTERVAL_HOURS)

def accrue_position_funding(pos: dict, now: float = None):
    if not FUNDING_SIMULATION_ENABLED:
        return
    if now is None:
        now = time.time()
    refresh_funding_state()
    with state_lock:
        rate = state.get("funding", {}).get("rate", 0.0)
    last = pos.get("last_funding_accrual_ts") or pos.get("entry_ts") or now
    dt_hours = max(0.0, (now - last) / 3600.0)
    if dt_hours <= 0:
        return
    payment = funding_cost_for_position(pos, rate, dt_hours)
    pos["funding_fees"] = pos.get("funding_fees", 0.0) + payment
    pos["last_funding_accrual_ts"] = now

def process_funding_accrual():
    if not FUNDING_SIMULATION_ENABLED:
        return
    now = time.time()
    refresh_funding_state()
    with trade_lock:
        for pos in list(open_positions):
            if pos.get("status") != "OPEN":
                continue
            accrue_position_funding(pos, now)

def get_funding_snapshot_for_ai():
    refresh_funding_state()
    with state_lock:
        f = copy.deepcopy(state.get("funding", {}))
    rate = f.get("rate", 0.0)
    f["interpretation"] = (
        "LONGS_PAY_SHORTS" if rate > 0 else "SHORTS_PAY_LONGS" if rate < 0 else "NEUTRAL"
    )
    f["favors_short_when_positive"] = rate > 0
    f["favors_long_when_negative"] = rate < 0
    return f

# --- Phase A: balanced market context (structure, MTF, EMA facts, trend strength) ---
MTF_REFRESH_SEC = 300
STRUCTURE_PIVOT_BARS = 2
_mtf_cache = {"1h": {"ts": 0.0, "candles": []}, "4h": {"ts": 0.0, "candles": []}}
_last_market_context_ts = 0.0

def _pct_diff(a, b):
    if a is None or b is None or b == 0:
        return 0.0
    return round((a - b) / b * 100, 4)

def extract_pivot_swings(candles, left=STRUCTURE_PIVOT_BARS, right=STRUCTURE_PIVOT_BARS):
    if not candles or len(candles) < left + right + 3:
        return []
    swings = []
    for i in range(left, len(candles) - right):
        window = candles[i - left : i + right + 1]
        hi = candles[i][2]
        lo = candles[i][3]
        if hi >= max(c[2] for c in window):
            swings.append({"type": "high", "price": hi, "idx": i})
        if lo <= min(c[3] for c in window):
            swings.append({"type": "low", "price": lo, "idx": i})
    swings.sort(key=lambda x: x["idx"])
    deduped = []
    for s in swings:
        if deduped and deduped[-1]["idx"] == s["idx"]:
            continue
        deduped.append(s)
    return deduped

def label_swing_sequence(swings, max_labels=6):
    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]
    labels = []
    for seq, tag_up, tag_down, tag_eq in (
        (highs, "HH", "LH", "EH"),
        (lows, "HL", "LL", "EL"),
    ):
        for i in range(1, len(seq)):
            prev, curr = seq[i - 1], seq[i]
            if curr["price"] > prev["price"]:
                labels.append(tag_up)
            elif curr["price"] < prev["price"]:
                labels.append(tag_down)
            else:
                labels.append(tag_eq)
    labels = labels[-max_labels:]
    score_map = {"HH": 2, "HL": 1, "LH": -1, "LL": -2, "EH": 0, "EL": 0}
    score = sum(score_map.get(l, 0) for l in labels)
    return labels, score

def compute_market_structure(candles):
    swings = extract_pivot_swings(candles[-96:] if len(candles) > 96 else candles)
    labels, score = label_swing_sequence(swings, max_labels=6)
    if score >= 3:
        bias = "BULLISH_STRUCTURE"
    elif score <= -3:
        bias = "BEARISH_STRUCTURE"
    elif score > 0:
        bias = "LEAN_BULL"
    elif score < 0:
        bias = "LEAN_BEAR"
    else:
        bias = "MIXED"
    last_highs = [s["price"] for s in swings if s["type"] == "high"][-2:]
    last_lows = [s["price"] for s in swings if s["type"] == "low"][-2:]
    hh_hl_active = len(labels) >= 2 and all(x in ("HH", "HL") for x in labels[-2:])
    lh_ll_active = len(labels) >= 2 and all(x in ("LH", "LL") for x in labels[-2:])
    return {
        "swing_labels_last": labels,
        "structure_score": score,
        "structure_bias": bias,
        "hh_hl_sequence_active": hh_hl_active,
        "lh_ll_sequence_active": lh_ll_active,
        "last_swing_high": last_highs[-1] if last_highs else None,
        "last_swing_low": last_lows[-1] if last_lows else None,
        "pivot_count": len(swings),
    }

def tf_trend_from_candles(candles):
    if not candles or len(candles) < 30:
        return "UNKNOWN"
    closes = [c[4] for c in candles]
    ema9 = ema(closes, EMA_FAST)
    ema21 = ema(closes, EMA_SLOW)
    close = closes[-1]
    if ema9 is None or ema21 is None:
        return "UNKNOWN"
    if close > ema9 > ema21:
        return "BULLISH"
    if close < ema9 < ema21:
        return "BEARISH"
    if close > ema9 and ema9 > ema21:
        return "LEAN_BULL"
    if close < ema9 and ema9 < ema21:
        return "LEAN_BEAR"
    return "RANGING"

def fetch_mtf_candles(timeframe: str, limit: int = 120):
    global _mtf_cache
    now = time.time()
    bucket = _mtf_cache.get(timeframe, {"ts": 0.0, "candles": []})
    if bucket["candles"] and now - bucket["ts"] < MTF_REFRESH_SEC:
        return bucket["candles"]
    try:
        candles = bybit_public.fetch_ohlcv(SYMBOL, timeframe, limit=limit)
        if candles:
            _mtf_cache[timeframe] = {"ts": now, "candles": candles}
            return candles
    except Exception as e:
        logger.warning(f"[MTF] fetch {timeframe} failed: {e} [PIPELINE ENFORCEMENT]")
    return bucket.get("candles") or []

def compute_multi_tf_trend(candles_15m):
    m15 = tf_trend_from_candles(candles_15m)
    m1h = tf_trend_from_candles(fetch_mtf_candles("1h"))
    m4h = tf_trend_from_candles(fetch_mtf_candles("4h"))
    trends = {"15m": m15, "1h": m1h, "4h": m4h}
    bull = sum(1 for t in trends.values() if t in ("BULLISH", "LEAN_BULL"))
    bear = sum(1 for t in trends.values() if t in ("BEARISH", "LEAN_BEAR"))
    if bull >= 2 and bear == 0:
        agreement = "BULL_ALIGNED"
    elif bear >= 2 and bull == 0:
        agreement = "BEAR_ALIGNED"
    elif bull >= 1 and bear >= 1:
        agreement = "CONFLICTED"
    else:
        agreement = "MIXED"
    note = ""
    if m15 in ("BEARISH", "LEAN_BEAR") and m1h in ("BULLISH", "LEAN_BULL") and m4h in ("BULLISH", "LEAN_BULL"):
        note = "15m pullback inside higher-TF bull trend"
    elif m15 in ("BULLISH", "LEAN_BULL") and m1h in ("BEARISH", "LEAN_BEAR") and m4h in ("BEARISH", "LEAN_BEAR"):
        note = "15m bounce inside higher-TF bear trend"
    return {"trends": trends, "bull_tf_count": bull, "bear_tf_count": bear, "agreement": agreement, "interpretation_note": note}

def compute_vwap_distance_pct(candles, price):
    if not candles or price is None or price <= 0:
        return 0.0
    slice_c = candles[-48:]
    vol_sum = 0.0
    pv_sum = 0.0
    for c in slice_c:
        v = float(c[5]) if len(c) > 5 else 0.0
        if v <= 0:
            continue
        tp = (c[2] + c[3] + c[4]) / 3.0
        pv_sum += tp * v
        vol_sum += v
    if vol_sum <= 0:
        return 0.0
    vwap = pv_sum / vol_sum
    return round((price - vwap) / vwap * 100, 4)

def compute_adx(candles, period=14):
    if len(candles) < period + 2:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        h, l, pc = candles[i][2], candles[i][3], candles[i - 1][4]
        ph, pl = candles[i - 1][2], candles[i - 1][3]
        up_move = h - ph
        down_move = pl - l
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None

    def wilder_smooth(vals):
        s = sum(vals[:period])
        out = [s]
        for v in vals[period:]:
            s = s - (s / period) + v
            out.append(s)
        return out

    atr_s = wilder_smooth(trs)
    pdm_s = wilder_smooth(plus_dm)
    mdm_s = wilder_smooth(minus_dm)
    dx_vals = []
    for atr, pdm, mdm in zip(atr_s, pdm_s, mdm_s):
        if atr <= 0:
            dx_vals.append(0.0)
            continue
        pdi = 100 * pdm / atr
        mdi = 100 * mdm / atr
        denom = pdi + mdi
        dx_vals.append(abs(pdi - mdi) / denom * 100 if denom > 0 else 0.0)
    if len(dx_vals) < period:
        return None
    adx_seed = sum(dx_vals[:period]) / period
    adx = adx_seed
    for dx in dx_vals[period:]:
        adx = (adx * (period - 1) + dx) / period
    return round(adx, 2)

def compute_ema_alignment_facts(price, ema_status, candles):
    ema9 = nz(ema_status.get("ema9"))
    ema21 = nz(ema_status.get("ema21"))
    ema200 = nz(ema_status.get("ema200"))
    closes = [c[4] for c in candles] if candles else []
    ema21_prev = ema(closes[:-3], EMA_SLOW) if len(closes) > EMA_SLOW + 3 else None
    ema9_prev = ema(closes[:-3], EMA_FAST) if len(closes) > EMA_FAST + 3 else None
    slope21 = _pct_diff(ema21, ema21_prev) if ema21_prev else nz(ema_status.get("ema21_slope_pct"), 0)
    slope9 = _pct_diff(ema9, ema9_prev) if ema9_prev else nz(ema_status.get("ema9_slope_pct"), 0)
    return {
        "price_vs_ema9_pct": _pct_diff(price, ema9),
        "price_vs_ema21_pct": _pct_diff(price, ema21),
        "price_vs_ema200_pct": _pct_diff(price, ema200),
        "ema9_above_ema21": bool(ema9 and ema21 and ema9 > ema21),
        "ema21_above_ema200": bool(ema21 and ema200 and ema21 > ema200),
        "stack_bull": bool(ema9 and ema21 and ema200 and ema9 > ema21 > ema200),
        "stack_bear": bool(ema9 and ema21 and ema200 and ema9 < ema21 < ema200),
        "ema9_slope_pct": round(slope9, 4),
        "ema21_slope_pct": round(slope21, 4),
        "ema_spread_pct": round(nz(ema_status.get("ema_spread", 0)) * 100, 4),
    }

def compute_trend_strength(candles, price, structure_score, mtf):
    adx = compute_adx(candles)
    vwap_dist = compute_vwap_distance_pct(candles, price)
    mtf_bonus = 0
    if mtf.get("agreement") == "BULL_ALIGNED":
        mtf_bonus = 2
    elif mtf.get("agreement") == "BEAR_ALIGNED":
        mtf_bonus = -2
    trend_score = int(max(-10, min(10, structure_score + mtf_bonus + (1 if (adx or 0) >= 25 else 0))))
    trending = (adx or 0) >= 25
    return {
        "adx": adx,
        "trend_score": trend_score,
        "vwap_distance_pct": vwap_dist,
        "trending_market_adx_25_plus": trending,
        "mean_reversion_risk": not trending and abs(vwap_dist) > 0.5,
    }

def update_market_context(force: bool = False):
    global _last_market_context_ts
    now = time.time()
    if not force and now - _last_market_context_ts < 15:
        return
    with state_lock:
        candles = copy.deepcopy(latest_candles)
        price = state.get("price")
        ema_status = copy.deepcopy(state.get("ema_status", {}))
        regime = state.get("regime", "UNKNOWN")
        sr = copy.deepcopy(state.get("support_resistance", {}))
    if len(candles) < 30 or not price or price <= 0:
        return
    structure = compute_market_structure(candles)
    mtf = compute_multi_tf_trend(candles)
    ema_alignment = compute_ema_alignment_facts(price, ema_status, candles)
    trend_strength = compute_trend_strength(candles, price, structure.get("structure_score", 0), mtf)
    ctx = {
        "updated_ts": now,
        "market_structure": structure,
        "multi_tf": mtf,
        "ema_alignment": ema_alignment,
        "trend_strength": trend_strength,
        "regime_label": regime,
        "sr_context": {
            "sr_state": sr.get("sr_state", "UNKNOWN"),
            "dist_to_resistance_pct": round(nz(sr.get("dist_to_resistance", 0)) * 100, 3),
            "dist_to_support_pct": round(nz(sr.get("dist_to_support", 0)) * 100, 3),
            "role": "LOCATION_CONTEXT_ONLY",
        },
    }
    with state_lock:
        state["market_context"] = ctx
    _last_market_context_ts = now
    logger.info(
        f"[MARKET_CTX] structure_score={structure.get('structure_score')} bias={structure.get('structure_bias')} "
        f"mtf={mtf.get('trends')} adx={trend_strength.get('adx')} trend_score={trend_strength.get('trend_score')} [PIPELINE ENFORCEMENT]"
    )

def get_market_context_for_ai():
    update_market_context()
    with state_lock:
        return copy.deepcopy(state.get("market_context", {}))

FILL_BUFFER = 0.0005
SLIPPAGE_BPS = 0.0002
EARLY_FAIL_PCT_THRESHOLD = -32.0
CANDLE_INTERVAL_SEC = 15 * 60
# --- v6 exit / risk (evidence-driven: MFE positive, TIME_EXIT losses) ---
FIXED_TIME_EXIT_ENABLED = False
EMERGENCY_MAX_HOLD_SEC = 24 * 3600
TRAIL_LADDER = [
    (12, 9), (20, 15), (30, 25), (40, 30), (50, 40),
    (60, 50), (80, 60), (100, 80), (120, 100), (150, 140),
]
PEAK_NEVER_LOSER_MIN_PEAK = 40.0
PEAK_NEVER_LOSER_FLOOR = 10.0
TP_EMERGENCY_MARGIN_PCT = 150.0
MAX_LONGS = 3
MAX_SHORTS = 3
CLUSTER_MIN_DIST_PCT = 0.0025
LONG_NEAR_SUPPORT_MAX_DIST = 0.004
LONG_NEAR_SUPPORT_MIN_BULL_SPREAD = 4
THESIS_INVALIDATION_ENABLED = True
THESIS_SCORE_FLIP_MARGIN = 1
THESIS_EARLY_DECAY_DELTA = 2
THESIS_MIN_AGE_SEC = 5 * 60
THESIS_EXIT_IF_ABOVE_UNREAL_PCT = 8.0
THESIS_FAST_EXIT_UNREAL_PCT = -12.0
ADX_BLOCK_NEW_ENTRY = 15.0
ADX_HALF_SIZE_BELOW = 18.0
ADX_HALF_SIZE_MULT = 0.5
RESEARCH_AI_THRESHOLD_FLOOR = 68
DASHBOARD_AUTO_REFRESH_MS = 60000
DAILY_DRAWDOWN_PAUSE_USD = 20.0
CONSECUTIVE_LOSS_PAUSE = 4
DEFAULT_RESEARCH_LEVERAGE = 50
MAX_RESEARCH_LEVERAGE = 50
CONVICTION_SPREAD_FULL = 5
CONVICTION_SPREAD_HALF = 3
CONVICTION_SPREAD_QUARTER = 1
LOSS_PAUSE_SEC = 4 * 3600
MAX_POSITION_AGE_SEC = EMERGENCY_MAX_HOLD_SEC
TP_TARGET_PCT = TP_EMERGENCY_MARGIN_PCT
TP_ARM_UNREAL_PCT = 999.0
PROTECTION_ARM_UNREAL_PCT = 999.0
TRAIL_LOCK_UNREAL_PCT = 0.0
# Phase B: SOFT = block fade-only trades at S/R when structure+MTF disagree; OFF = no S/R direction blocks
SR_FILTER_MODE = "SOFT"
BLOCK_LONG_NEAR_RESISTANCE = False
BLOCK_SHORT_NEAR_SUPPORT = True
SHORT_NEAR_SUPPORT_MAX_DIST = 0.01
SHORT_NEAR_SUPPORT_MIN_BEAR_SPREAD = 4
SHORT_NEAR_SUPPORT_MIN_STRUCT = -5
# Phase C: require bull/bear factor scores to align with direction before APPROVE stands
PHASE_C_FACTOR_GATE_ENABLED = True
MIN_FACTOR_SCORE_MARGIN = 1
PERSIST_SIGNAL_STAGES = frozenset({"SETUP", "AI", "FILLED", "CLOSED", "COMPLETE", "BLOCKED"})
EARLY_FAIL_MINUTES = 40
CSV_DECISIONS = "decisions_3factor.csv"
CSV_TRADES = "trades_3factor.csv"
CSV_EXPIRED = "expired_orders_3factor.csv"
CSV_BLOCKS = "blocked_signals_3factor.csv"
CSV_AI_TRANCHE = "ai_tranche_log.csv"
CSV_SETUP_LOG = "setup_log_3factor.csv"
CSV_CANDLES = "candles_3factor.csv"
EMA_FAST = 9
EMA_SLOW = 21
EMA_LONG = 200
SYMBOL = "BTCUSDT"
LIVE_TRADING_ENABLED = False
MAX_PENDING_ORDERS = 2
MAX_EXPIRED_ORDERS = 20
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
FAST_MONITOR_INTERVAL_SEC = 2.0
STARTING_BALANCE = 500.0
MAX_CONCURRENT_POSITIONS_DEFAULT = 6
AI_TIMEOUT_SEC = 60
HEDGE_MODE = True
SIGNAL_TTL_SEC = 2700
REPLAY_TTL_SEC = 120 * 60
MAX_EVENT_QUEUE = 10000
LIMIT_ORDER_MAX_AGE_SEC = 1800
GLOBAL_SIGNAL_COOLDOWN = 300
HEARTBEAT_INTERVAL = 300.0
ANALYTICS_INTERVAL_SEC = 600
MIN_ANALYTICS_TRADES = 20
OHLCV_FETCH_INTERVAL = 60
STARTUP_GRACE_PERIOD = 30
CANDLE_STALE_SEC = 180
MAX_ACTIVE_SIGNALS = 6
MAX_SIGNAL_RETENTION_SEC = 7200
BUFFER_MIN = 150
MIN_CANDLES = 200
READY_STABLE_SEC = 5.0
STALE_SOFT_SEC = 20
STALE_HARD_SEC = 180
SR_ZONE_PCT = 0.0162
PRICE_CHANGE_THRESHOLD = 0.0002
MAX_WS_AGE = 3
PIPELINE_INTERVAL = 10.0
AI_COOLDOWN_SECONDS = 300
MIN_PIPELINE_INTERVAL = 30
EDGE_INTERVAL_SEC = 30
EDGE_SCORE_MAX = 6.0
DOUBLE_CONFIRM_AI = False
MIN_DATA_QUALITY_FOR_EDGE = 0.7
EXECUTION_FIX_VERSION = "v6.3-loss-asymmetry-risk"
ORDER_PLACEMENT_GRACE_SEC = 30
RESEARCH_INSTANT_FILL = True
TERMINAL_SIGNAL_STATUSES = frozenset({"EXPIRED", "BLOCKED", "REJECTED", "COMPLETE", "CANCELLED", "CLOSED", "FILLED"})
TERMINAL_SIGNAL_OUTCOMES = frozenset({"STALE_NO_EXPOSURE", "SIGNAL_TTL_EXPIRED", "TTL_EXPIRED", "CAPACITY_REPLACED", "WIN", "LOSS"})
last_signal_hash = None
last_event_trigger = 0.0
last_pipeline_run = 0.0
last_edge_compute = 0.0

EDGE_OPTIONS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]

candidate_signal = {"active": False, "direction": None, "confidence": 0.0, "ts": 0.0}

state = {
    "strategy_mode": "RESEARCH",
    "allow_compression": True,
    "live_armed": False,
    "early_fail_enabled": True,
    "ai_enabled": True,
    "invert_signal": False,
    "telegram_enabled": False,
    "pullback_threshold": 0.001,
    "leverage": DEFAULT_RESEARCH_LEVERAGE,
    "max_active_signals": MAX_CONCURRENT_POSITIONS_DEFAULT,
    "ai_threshold": 68,
    "consecutive_losses": 0,
    "loss_pause_until": 0.0,
    "edge_threshold": 3.0,
    "min_confidence": 0,
    "force_ai_every_signal": False,
    "debug_enabled": True,
    "daily_pnl_usd": 0.0,
    "account_balance": STARTING_BALANCE,
    "current_trading_day": datetime.now(timezone.utc).date(),
    "heartbeat": 0,
    "last_data_ts": time.time(),
    "last_fetch_success": "never",
    "data_source": "booting",
    "ai_verdict": "AI reviewer ON | Threshold initializing",
    "regime": "UNKNOWN",
    "strategy": "SR",
    "direction": "FLAT",
    "signal_direction": "FLAT",
    "signal_info": {"active": False, "count": 0, "signals": []},
    "orders": [],
    "support_resistance": {"pivot": None, "s1": None, "s2": None, "r1": None, "r2": None, "swing_high": None, "swing_low": None, "ts": None, "window": "96x15m (~24h rolling)", "dist_to_resistance": 0.0, "dist_to_support": 0.0, "sr_zone_pct": SR_ZONE_PCT, "sr_state": "UNKNOWN", "sr_bias": "BOTH_ALLOWED"},
    "ema_status": {},
    "ws_last_tick": None,
    "last_engine_error": "None",
    "drawdown_kill_until": None,
    "last_ai": {"win_prob": None, "direction": None, "trade_id": None, "comment": None, "ai_error": None, "factors": {}, "source": "NONE", "decision": None},
    "last_ai_ts": 0.0,
    "last_ai_fp": "",
    "ai_history": [],
    "engine_reason": "",
    "ai_reason": "",
    "price": None,
    "price_ts": None,
    "price_source": "NONE",
    "execution_paused": False,
    "execution_reason": "",
    "ws_ready": False,
    "ohlcv_ready": False,
    "data_error": None,
    "system_ready": False,
    "allow_rest_price": False,
    "early_fail_cooldown_until": 0,
    "_pause_priority": 0,
    "candle_pressure_snapshot": 0.0,
    "setup_detected": False,
    "pivot_structural": None,
    "pivot_active": None,
    "ai_outcome": "NO_SIGNAL",
    "execution_outcome": "UNKNOWN",
    "last_ai_fingerprint_time": 0.0,
    "price_seq": 0,
    "_policy": {},
    "last_signal_time": 0.0,
    "last_signal_direction": "FLAT",
    "signal_cooldown_candles": 1,
    "last_signal_candle": 0,
    "pending_trade_id": None,
    "last_signal_create_ts": 0.0,
    "last_block_time": 0.0,
    "last_setup_time": 0.0,
    "diag": {"ws_latency_ms": 0, "engine_loop_ms": 0, "ai_latency_ms": 0, "order_latency_ms": 0, "signals_last_hour": 0, "ws_status": "UNKNOWN", "engine_status": "UNKNOWN", "ai_status": "UNKNOWN"},
    "last_cross_candle": -1,
    "analytics": {"ai_bands": {}},
    "analytics_ts": None,
    "last_fail_dir": None,
    "last_fail_price": None,
    "debug_conf_components": {},
    "last_setup_key": "",
    "last_signal_key": "",
    "last_ai_signal_key": "",
    "execution_status": "BLOCKED",
    "last_ready_ts": 0.0,
    "last_ai_candle": -1,
    "last_ai_logged_key": "",
    "last_ai_candle_logged": -1,
    "no_signal_count": 0,
    "ws_stale_count": 0,
    "ai_decision": None,
    "execution_decision": None,
    "final_decision": None,
    "order_decision": None,
    "order_fail_reason": None,
    "system_ready_logged": False,
    "ai_skipped_signals": 0,
    "ai_call_count": 0,
    "signals_last_hour": 0,
    "last_ai_confidence": 0.0,
    "ready_since": 0.0,
    "last_processed_candle_ts": 0.0,
    "last_ai_signal_time": 0.0,
    "ai_processed": False,
    "last_no_signal_candle": -1,
    "last_block_log": 0.0,
    "_threshold_locked": True,
    "last_event_ts": 0.0,
    "ws_retry": 1,
    "last_ai_call_ts": 0.0,
    "bootstrap_done": False,
    "prev_ai_ctx": {},
    "ai_history_updated": 0.0,
    "feature_snapshot": {},
    "data_quality": 0.0,
    "funding": {"rate": 0.0, "next_time": 0},
    "market_context": {},
    "debug_state": {
        "last_event_time": None,
        "last_edge_score": 0,
        "last_flags": {},
        "last_trigger": False,
        "last_signal_attempt": None,
        "last_block_reason": None,
        "last_ai_call": None,
        "last_ai_decision": None,
        "last_ai_score": None,
        "signal_cooldown_active": False,
        "ai_cooldown_active": False,
        "cooldown_remaining_signal": 0,
        "cooldown_remaining_ai": 0,
        "last_pipeline_stage": None,
        "system_last_tick": None,
        "engine_last_run": None,
        "last_check_time": None,
        "skip_reason": None,
        "edge_progress": None,
        "ai_gate": {"called": False, "reason": "", "edge": 0.0, "threshold": 0.0},
        "edge_reason": "UNKNOWN"
    },
    "last_edge": 0.0,
    "edge_threshold": 3.0,
    "last_pipeline_stage": "IDLE",
    "warmup_mode": True
}
shutdown_event = threading.Event()
PAUSE_PRIORITIES = {"STALE_DATA_HARD_STOP": 50, "THREAD_CRASH": 1, "QUEUE_OVERFLOW": 60, "": 0, "CSV_FAILURE": 100, "PRELOAD_FAILED": 100}

def get_edge_threshold():
    with state_lock:
        return round(float(state["edge_threshold"]), 1)

def enforce_edge_threshold_options():
    with state_lock:
        current = round(state["edge_threshold"], 1)
        if current not in [round(x, 1) for x in EDGE_OPTIONS]:
            logger.warning(f"[EDGE ENFORCEMENT] Invalid threshold {current} - resetting to 3.0 [PIPELINE ENFORCEMENT]")
            state["edge_threshold"] = 3.0
            state["debug_state"]["edge_reason"] = "RESET_TO_DEFAULT"
    logger.info(f"[EDGE ENFORCEMENT] threshold validated at {get_edge_threshold()} [PIPELINE ENFORCEMENT]")

def is_edge_valid(edge_score: float) -> bool:
    threshold = get_edge_threshold()
    edge_score_rounded = round(edge_score, 1)
    valid = edge_score_rounded >= threshold
    with state_lock:
        state["debug_state"]["edge_progress"] = f"{edge_score_rounded:.1f}/{threshold:.1f} (max {EDGE_SCORE_MAX:.1f})"
        state["debug_state"]["last_trigger"] = valid
        state["debug_state"]["last_edge_score"] = edge_score_rounded
    logger.info(f"[EDGE GATE SINGLE SOURCE] edge={edge_score_rounded:.1f} valid={valid} threshold={threshold} [PIPELINE ENFORCEMENT]")
    return valid

def compute_ret_1m():
    if len(price_buffer) < 2:
        return 0.0
    return (price_buffer[-1] - price_buffer[-2]) / price_buffer[-2] if price_buffer[-2] != 0 else 0.0

def compute_ret_5m():
    if len(price_buffer) < 20:
        return 0.0
    return (price_buffer[-1] - price_buffer[-20]) / price_buffer[-20] if price_buffer[-20] != 0 else 0.0

def populate_candle_buffers_from_candles(candles):
    populated = 0
    for candle in candles:
        try:
            o = candle[1]
            h = candle[2]
            l = candle[3]
            c = candle[4]
            candle_range = h - l
            if candle_range <= 0:
                continue
            body = abs(c - o)
            wick = candle_range - body
            body_ratio = body / candle_range
            wick_ratio = wick / candle_range
            candle_range_buffer.append(candle_range)
            body_ratio_buffer.append(body_ratio)
            wick_ratio_buffer.append(wick_ratio)
            populated += 1
            logger.debug(f"[CANDLE BUFFER PRELOAD] range={candle_range:.4f} body_ratio={body_ratio:.4f} wick_ratio={wick_ratio:.4f} [PIPELINE ENFORCEMENT]")
        except Exception as e:
            logger.error(f"[CANDLE BUFFER PRELOAD ERROR] {e} [PIPELINE ENFORCEMENT]")
    logger.info(f"[CANDLE BUFFER PRELOAD] populated {populated} candles into buffers [PIPELINE ENFORCEMENT]")

def compute_volume_ratio():
    if len(volume_buffer) < 1:
        return 0.0
    vol_mean = np.mean(list(volume_buffer)[-50:]) if len(volume_buffer) > 50 else np.mean(list(volume_buffer))
    if vol_mean <= 0:
        return 0.0
    current = volume_buffer[-1]
    return float(current / vol_mean)

def update_candle_features():
    global last_processed_candle_ts
    try:
        if len(latest_candles) < 1:
            logger.debug("[CANDLE] No candles yet - skipping buffer fill [PIPELINE ENFORCEMENT]")
            return
        candle = latest_candles[-1]
        candle_ts = candle[0]
        if candle_ts == last_processed_candle_ts:
            _agent_dbg("H5", "update_candle_features", "dedupe_skip", {"candle_ts": candle_ts})
            return
        last_processed_candle_ts = candle_ts
        high = candle[2]
        low = candle[3]
        open_p = candle[1]
        close = candle[4]
        candle_range = high - low
        body = abs(close - open_p)
        wick = candle_range - body
        body_ratio = body / candle_range if candle_range > 0 else 0.0
        wick_ratio = wick / candle_range if candle_range > 0 else 0.0
        candle_range_buffer.append(candle_range)
        body_ratio_buffer.append(body_ratio)
        wick_ratio_buffer.append(wick_ratio)
        logger.info(f"[CANDLE BUFFER LIVE UPDATE] range={candle_range:.4f} body_ratio={body_ratio:.4f} wick_ratio={wick_ratio:.4f} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"[CANDLE BUFFER ERROR] {e} [PIPELINE ENFORCEMENT]")

def build_full_feature_snapshot():
    if not is_buffer_ready():
        logger.warning("[BUFFER] Not enough data for stable features - skipping [PIPELINE ENFORCEMENT]")
        return None
    try:
        logger.info("[FEATURE BUILD] Starting aggregated feature construction with WINDOW=10 [PIPELINE ENFORCEMENT]")
        update_candle_features()
        price = nz(state.get("price"))
        if price <= 0:
            logger.warning("[FEATURE BUILD] price <=0 [PIPELINE ENFORCEMENT]")
            return None
        ema_status = state.get("ema_status", {})
        sr = state.get("support_resistance", {})
        volume_ratio = compute_volume_ratio()
        velocity = (price_buffer[-1] - price_buffer[-10]) / price_buffer[-10] if len(price_buffer) > 10 else 0.0
        delta = get_sum(delta_buffer)
        imbalance = get_aggregated(imbalance_buffer)
        candle = latest_candles[-1] if len(latest_candles) > 0 else None
        if candle:
            candle_range = candle[2] - candle[3]
            body_ratio = abs(candle[4] - candle[1]) / candle_range if candle_range > 0 else 0.0
            wick_ratio = (candle[2] - max(candle[1], candle[4])) / candle_range if candle_range > 0 else 0.0
        else:
            candle_range = 0.0
            body_ratio = 0.0
            wick_ratio = 0.0
        features = {
            "price": price,
            "ret_1m": compute_ret_1m(),
            "ret_5m": compute_ret_5m(),
            "ema9": nz(ema_status.get("ema9")),
            "ema21": nz(ema_status.get("ema21")),
            "ema200": nz(ema_status.get("ema200")),
            "dist_to_support": nz(sr.get("dist_to_support")),
            "dist_to_resistance": nz(sr.get("dist_to_resistance")),
            "volume": get_aggregated(volume_buffer),
            "volume_ratio": volume_ratio,
            "delta": get_aggregated(delta_buffer),
            "velocity": get_aggregated(velocity_buffer),
            "imbalance": get_aggregated(imbalance_buffer),
            "candle_range": get_aggregated(candle_range_buffer),
            "body_ratio": get_aggregated(body_ratio_buffer),
            "wick_ratio": get_aggregated(wick_ratio_buffer)
        }
        with state_lock:
            state["feature_snapshot"] = features
        logger.info(f"[FEATURE BUILD] complete - keys: {list(features.keys())} price={price:.2f} volume={features['volume']:.4f} velocity={features['velocity']:.6f} volume_ratio={features['volume_ratio']:.4f} candle_range={features['candle_range']:.4f} body_ratio={features['body_ratio']:.4f} wick_ratio={features['wick_ratio']:.4f} [PIPELINE ENFORCEMENT]")
        return features
    except Exception as e:
        logger.error(f"[FEATURE BUILD ERROR] {e} [PIPELINE ENFORCEMENT]")
        return None

def validate_feature_completeness(features):
    if features is None:
        return False
    required = ["ret_1m", "ema9", "dist_to_support"]
    missing = [k for k in required if features.get(k) is None]
    if missing:
        logger.info(f"[EDGE BLOCK SOFT] incomplete features: {missing} - continuing with partial [PIPELINE ENFORCEMENT]")
        return False
    return True

def is_valid_feature_set(features):
    if features is None:
        logger.info("[FEATURE VALIDATION] BAD_FEATURE_QUALITY features=None [PIPELINE ENFORCEMENT]")
        return False
    if any(v is None for v in features.values()):
        logger.info("[FEATURE VALIDATION] BAD_FEATURE_QUALITY None values present [PIPELINE ENFORCEMENT]")
        return False
    if features.get("volume", 0) < 0.01:
        logger.warning("[FEATURE VALIDATION] LOW_VOLUME_ENV - continuing for research [PIPELINE ENFORCEMENT]")
        return True
    if abs(features.get("velocity", 0)) < 1e-5:
        logger.warning("[FEATURE VALIDATION] LOW_VELOCITY_ENV - continuing for research [PIPELINE ENFORCEMENT]")
        return True
    return True

def compute_edge_score(features: dict = None) -> float:
    try:
        global last_edge_compute
        now = time.time()
        if now - last_edge_compute < EDGE_INTERVAL_SEC:
            logger.debug(f"[EDGE CLOCK] skipped compute - {now - last_edge_compute:.1f}s since last - reusing last_edge [PIPELINE ENFORCEMENT]")
            return round(state.get("last_edge", 0.1), 1)
        last_edge_compute = now
        logger.info("[EDGE] compute_edge_score started with explicit features (30s enforced) [PIPELINE ENFORCEMENT]")
        if features is None:
            features = build_full_feature_snapshot()
        if features is None or not validate_feature_completeness(features):
            logger.info("[FEATURE VALIDATION] partial - proceeding with penalties [PIPELINE ENFORCEMENT]")
            return 0.1
        with state_lock:
            dq = state.get("data_quality", 0.0)
        if dq < MIN_DATA_QUALITY_FOR_EDGE:
            logger.info(f"[EDGE] data_quality={dq:.2f} < {MIN_DATA_QUALITY_FOR_EDGE} - no edge score [PIPELINE ENFORCEMENT]")
            return 0.1
        price = nz(state.get("price"))
        if price <= 0:
            logger.warning("[EDGE] price <=0 - returning floor 0.1 [PIPELINE ENFORCEMENT]")
            return 0.1
        dist_to_support = nz(features.get("dist_to_support", 1))
        dist_to_resistance = nz(features.get("dist_to_resistance", 1))
        ema9 = nz(features.get("ema9", 0))
        ema21 = nz(features.get("ema21", 0))
        ema200 = nz(features.get("ema200", 0))
        ret_1m = abs(nz(features.get("ret_1m", 0)))
        ret_5m = abs(nz(features.get("ret_5m", 0)))
        velocity = abs(nz(features.get("velocity", 0)))
        volume = nz(features.get("volume", 0))
        volume_ratio = nz(features.get("volume_ratio", 0))
        delta = abs(nz(features.get("delta", 0)))
        if volume < 0.01:
            logger.info("[EDGE] low volume - soft penalty [PIPELINE ENFORCEMENT]")
            volume_ratio *= 0.3
        update_market_context()
        with state_lock:
            mc = copy.deepcopy(state.get("market_context", {}))
        ms = mc.get("market_structure", {})
        mtf = mc.get("multi_tf", {})
        ts = mc.get("trend_strength", {})
        struct_score = abs(ms.get("structure_score", 0))
        structure_component = min(1.0, struct_score / 6.0) * 1.5
        mtf_aligned = max(mtf.get("bull_tf_count", 0), mtf.get("bear_tf_count", 0))
        mtf_component = (mtf_aligned / 3.0) * 1.2
        if mtf.get("agreement") == "CONFLICTED":
            mtf_component *= 0.35
        adx = ts.get("adx") or 0
        if ema9 > ema21 > ema200 or ema9 < ema21 < ema200:
            ema_trend_component = 1.0
        else:
            ema_trend_component = 0.35
        trend_component = ema_trend_component + (0.25 if adx >= 25 else 0.0)
        momentum_raw = max(ret_1m * 5000, ret_5m * 2000, velocity * 8000)
        momentum_component = min(momentum_raw, 1.0) * 1.0
        flow_component = min(volume_ratio, 1.0) * min(delta / 50, 1.0) * 1.0
        sr_near = dist_to_support < 0.02 or dist_to_resistance < 0.02
        sr_component = 0.6 if sr_near else 0.15
        edge_score = (
            structure_component +
            mtf_component +
            trend_component +
            momentum_component +
            flow_component +
            sr_component
        )
        edge_score = max(0.1, min(edge_score, EDGE_SCORE_MAX))
        edge_score = round(edge_score, 1)
        with state_lock:
            state["last_edge"] = edge_score
            state["debug_state"]["last_edge_score"] = edge_score
            state["debug_state"]["edge_components"] = {
                "structure": round(structure_component, 2),
                "mtf": round(mtf_component, 2),
                "trend": round(trend_component, 2),
                "momentum": round(momentum_component, 2),
                "flow": round(flow_component, 2),
                "sr_context": round(sr_component, 2),
            }
            state["debug_state"]["edge_progress"] = f"{edge_score:.1f}/{get_edge_threshold():.1f} (max {EDGE_SCORE_MAX:.1f})"
            state["debug_state"]["edge_reason"] = "OK" if edge_score > 0.5 else "LOW"
        logger.info(f"[EDGE] compute_edge_score completed edge={edge_score:.1f} reason={state['debug_state']['edge_reason']} [PIPELINE ENFORCEMENT]")
        enforce_edge_threshold_options()
        update_debug_state_always("EDGE_DONE", {"edge": edge_score})
        return edge_score
    except Exception as e:
        logger.error(f"[EDGE SCORE ERROR] {e} [PIPELINE ENFORCEMENT]")
        return 0.1

def update_debug_state_always(stage=None, extra=None, *args, **kwargs):
    try:
        with state_lock:
            if stage:
                state["debug_state"]["last_pipeline_stage"] = stage
            state["debug_state"]["last_event_time"] = utc_iso()
            if extra:
                state["debug_state"].update(extra)
            if args or kwargs:
                logger.debug(f"[DEBUG STATE] extra args absorbed: args={len(args)} kwargs={len(kwargs)} [PIPELINE ENFORCEMENT]")
            logger.info(f"[DEBUG STATE UPDATE] stage={stage} edge={state['debug_state'].get('last_edge_score',0)} pipeline={state['last_pipeline_stage']} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"[DEBUG STATE ERROR] {e} [PIPELINE ENFORCEMENT]")

def reset_all_csv():
    logger.warning("[RESET] Deleting all CSV files for fresh session [PIPELINE ENFORCEMENT]")
    files = [CSV_DECISIONS, CSV_TRADES, CSV_EXPIRED, CSV_BLOCKS, CSV_AI_TRANCHE, CSV_SETUP_LOG, CSV_CANDLES]
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
                logger.info(f"[RESET] Deleted {f} [PIPELINE ENFORCEMENT]")
        except Exception as e:
            logger.error(f"[RESET ERROR] Failed to delete {f}: {e} [PIPELINE ENFORCEMENT]")

def update_logger_level():
    try:
        level = logging.DEBUG if state.get("debug_enabled") else logging.INFO
        logger.setLevel(level)
        for h in logger.handlers:
            h.setLevel(level)
        logger.info(f"[LOGGER] Level set to {logging.getLevelName(level)} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"[LOGGER ERROR] {e} [PIPELINE ENFORCEMENT]")

def dynamic_csv_writer(filename, row):
    try:
        file_exists = os.path.exists(filename)
        if not file_exists:
            with open(filename, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                writer.writeheader()
                writer.writerow(safe_csv_row(row))
            return
        with open(filename, "r") as f:
            reader = csv.DictReader(f)
            existing = reader.fieldnames or []
        new_fields = list(set(existing) | set(row.keys()))
        if set(new_fields) != set(existing):
            with open(filename, "r") as f:
                old_rows = list(csv.DictReader(f))
            with open(filename, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=new_fields)
                writer.writeheader()
                for old in old_rows:
                    writer.writerow(old)
                writer.writerow(safe_csv_row(row))
        else:
            with open(filename, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=existing)
                writer.writerow(safe_csv_row(row))
        logger.info(f"[CSV WRITE] {filename} row added - edge={row.get('edge_score', 'N/A')} experiment={row.get('experiment_tag','NONE')} stage={row.get('stage','N/A')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.critical(f"[FATAL CSV FAILURE] {e} - HALTING ENGINE [PIPELINE ENFORCEMENT]")
        set_execution_paused("CSV_FAILURE")
        raise

def prune_signals():
    now = time.time()
    removed = 0
    with trade_lock:
        for t in list(trades_map.keys()):
            signal = trades_map[t].get("signal_ref", {})
            ts = signal.get("created_ts_ts", 0)
            if ts and now - ts > MAX_SIGNAL_RETENTION_SEC:
                trades_map.pop(t, None)
                removed += 1
    if removed > 0:
        logger.info(f"[PRUNE] Removed {removed} old signals [PIPELINE ENFORCEMENT]")

def _remove_pending_for_trade(trade_id: str, reason: str = "ORPHAN_CLEANUP"):
    if not trade_id:
        return False
    removed = False
    with trade_lock:
        for order in list(pending_orders):
            if order.get("trade_id") == trade_id and order.get("status") == "PENDING":
                pending_orders.remove(order)
                removed = True
                logger.info(f"[ORDER CLEANUP] Removed pending order trade_id={trade_id} reason={reason} [PIPELINE ENFORCEMENT]")
    return removed

def reconcile_stale_signals():
    pending_ids = _pending_trade_ids()
    open_ids = _open_trade_ids()
    fixed = 0
    orphans_removed = 0
    now = time.time()
    with trade_lock:
        for s in trades_map.values():
            sig = s.get("signal_ref", {}) or {}
            tid = sig.get("trade_id")
            if not tid:
                continue
            st = sig.get("status")
            outcome = sig.get("outcome") or sig.get("exit_reason")
            if outcome in TERMINAL_SIGNAL_OUTCOMES and st not in TERMINAL_SIGNAL_STATUSES:
                sig["status"] = "EXPIRED"
                fixed += 1
                if _remove_pending_for_trade(tid, "OUTCOME_TERMINAL_SYNC"):
                    orphans_removed += 1
                continue
            created_ts = sig.get("created_ts_ts", 0)
            if created_ts and now - created_ts < ORDER_PLACEMENT_GRACE_SEC:
                continue
            expires_ts = sig.get("expires_ts") or (sig.get("created_ts_ts", 0) + SIGNAL_TTL_SEC)
            ttl_expired = expires_ts and now > expires_ts
            missing_exposure = tid not in pending_ids and tid not in open_ids
            if st in ("ORDERED", "ACTIVE", "PENDING") and (missing_exposure or ttl_expired):
                sig["status"] = "EXPIRED"
                sig["outcome"] = "STALE_NO_EXPOSURE" if not ttl_expired else "SIGNAL_TTL_EXPIRED"
                sig["exit_reason"] = sig["outcome"]
                fixed += 1
                if _remove_pending_for_trade(tid, sig["outcome"]):
                    orphans_removed += 1
        for order in list(pending_orders):
            tid = order.get("trade_id")
            if order.get("status") != "PENDING" or not tid:
                continue
            master = trades_map.get(tid, {}).get("signal_ref", {}) or {}
            if is_terminal_signal(master) or (master.get("created_ts_ts", 0) and now - master.get("created_ts_ts", 0) > SIGNAL_TTL_SEC):
                pending_orders.remove(order)
                orphans_removed += 1
                logger.info(f"[ORDER CLEANUP] Dropped orphan pending trade_id={tid} signal_status={master.get('status')} outcome={master.get('outcome')} [PIPELINE ENFORCEMENT]")
    if fixed or orphans_removed:
        logger.info(f"[EXECUTION FIX {EXECUTION_FIX_VERSION}] reconciled {fixed} stale signals, removed {orphans_removed} orphan pending orders")
        _agent_dbg("H1", "reconcile_stale_signals", "reconciled", {"count": fixed, "orphans_removed": orphans_removed, "pending_ids": len(_pending_trade_ids()), "open_ids": len(_open_trade_ids())})
    return fixed

def sync_signal_info_registry():
    pending_ids = _pending_trade_ids()
    open_ids = _open_trade_ids()
    live_ids = pending_ids | open_ids
    live_signals = []
    with trade_lock:
        for entry in trades_map.values():
            sig = entry.get("signal_ref")
            if not isinstance(sig, dict):
                continue
            tid = sig.get("trade_id")
            if not tid or tid not in live_ids or is_terminal_signal(sig):
                continue
            live_signals.append(sig)
        exposure = len([o for o in pending_orders if o.get("status") == "PENDING"]) + len(open_positions)
    with state_lock:
        state["signal_info"]["signals"] = live_signals[-MAX_ACTIVE_SIGNALS:]
        state["signal_info"]["count"] = exposure
        state["signal_info"]["active"] = exposure > 0

def pipeline_state_sync():
    reconcile_stale_signals()
    sync_signal_info_registry()
    sync_cooldown_debug_state()
    logger.debug("[STATE SYNC] Pipeline state synchronized [PIPELINE ENFORCEMENT]")

def persist_signal(signal, stage="UNKNOWN"):
    try:
        if stage not in PERSIST_SIGNAL_STAGES:
            return
        if not signal or not signal.get("trade_id"):
            return
        row = {
            "ts": utc_iso(),
            "trade_id": signal.get("trade_id"),
            "stage": stage,
            "direction": signal.get("final_direction"),
            "price": signal.get("signal_price"),
            "status": signal.get("status"),
            "ai_win_prob": signal.get("ai_win_prob"),
            "ai_decision": signal.get("ai_decision"),
            "edge_score": signal.get("edge_score_at_entry"),
            "setup_type": signal.get("setup_type")
        }
        logger.info(f"[PERSIST] {stage} for trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")
        dynamic_csv_writer("signal_persist.log", row)
    except Exception as e:
        logger.error(f"[PERSIST ERROR] {e} [PIPELINE ENFORCEMENT]")

def persist_signal_close(trade_id, status):
    logger.info(f"[PERSIST CLOSE] trade_id={trade_id} status={status} [PIPELINE ENFORCEMENT]")

def validate_startup():
    required = ["reset_all_csv","update_logger_level","prune_signals","process_signal","evaluate_signal_with_ai","execute_order","pipeline_state_sync","persist_signal","compute_edge_score","update_debug_state_always"]
    for fn in required:
        if fn not in globals():
            raise Exception(f"[STARTUP ERROR] Missing function: {fn}")
    logger.info("[STARTUP] All critical functions verified [PIPELINE ENFORCEMENT]")

def system_health_snapshot():
    try:
        snapshot = {
            "time": utc_iso(),
            "price": state.get("price"),
            "price_age": time.time() - state.get("price_ts", 0) if state.get("price_ts") else None,
            "ws_ready": state.get("ws_ready"),
            "candles": len(latest_candles),
            "data_stale": is_data_stale(),
            "execution_status": state.get("execution_status"),
            "paused": state.get("execution_paused"),
            "pending_orders": len(pending_orders),
            "open_positions": len(open_positions),
            "active_signals": get_active_signal_count(),
            "ai_last_run": time.time() - state.get("last_ai_ts", 0),
            "ai_calls": state.get("ai_call_count"),
            "no_signal_count": state.get("no_signal_count", 0)
        }
        logger.info("\n" + "#"*60)
        logger.info("[SYSTEM HEALTH]")
        logger.info(json.dumps(snapshot, indent=2, default=str))
        logger.info("#"*60 + "\n")
    except Exception as e:
        logger.error(f"[HEALTH ERROR] {e} [PIPELINE ENFORCEMENT]")

def track_event(trade_id, stage):
    if trade_id in trades_map:
        if "timeline" not in trades_map[trade_id]:
            trades_map[trade_id]["timeline"] = []
        trades_map[trade_id]["timeline"].append({"ts": utc_iso(), "stage": stage})
        logger.info(f"[LIFECYCLE] trade_id={trade_id} stage={stage} [PIPELINE ENFORCEMENT]")

def sanitize_ai_inputs(ctx: dict) -> dict:
    if not isinstance(ctx, dict):
        return {}
    clean = {}
    for k, v in ctx.items():
        if isinstance(v, (int, float)):
            if math.isnan(v) or math.isinf(v):
                clean[k] = 0
            else:
                clean[k] = float(v)
        elif v is None:
            clean[k] = 0
        else:
            clean[k] = v
    return clean

def enforce_log(signal: dict, stage: str, extra: str = None):
    if not signal or not isinstance(signal, dict) or not signal.get("trade_id"):
        logger.error(f"[LOG ENFORCEMENT FAIL] Missing trade_id for stage={stage} [PIPELINE ENFORCEMENT]")
        return
    logged_key = f"_logged_{stage}"
    if signal.get(logged_key):
        return
    signal[logged_key] = True
    signal["_logged"] = True
    log_decision(signal, stage, extra or stage)
    persist_signal(signal, stage)
    logger.info(f"[ENFORCE_LOG] {stage} trade_id={signal.get('trade_id')} extra={extra} [PIPELINE ENFORCEMENT]")

def enforce_immutable(signal: dict):
    IMMUTABLE_FIELDS = ["features", "controls", "indicators", "edge_score_at_entry", "edge_threshold_at_entry", "counterfactuals"]
    for f in IMMUTABLE_FIELDS:
        if f in signal and isinstance(signal[f], (dict, list)):
            signal[f] = copy.deepcopy(signal[f])
    signal["_frozen"] = True
    logger.debug(f"[IMMUTABILITY GUARD] Applied to trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")

def assert_not_mutated(signal: dict, field: str, new_value=None):
    if signal.get("_frozen") and field in ["features", "controls", "indicators", "edge_score_at_entry", "edge_threshold_at_entry", "counterfactuals"]:
        logger.critical(f"IMMUTATION VIOLATION: {field} on frozen trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")
        raise RuntimeError(f"IMMUTATION VIOLATION: {field} on frozen trade_id={signal.get('trade_id')}")

def finalize_signal(signal: dict, ai: dict = None, status: str = None):
    if signal.get("_finalized"):
        current = signal.get("status")
        if status and current != status:
            if current in TERMINAL_SIGNAL_STATUSES or is_terminal_signal(signal):
                logger.debug(f"[FINALIZE SKIP] trade_id={signal.get('trade_id')} terminal status={current} — not downgrading to {status} [PIPELINE ENFORCEMENT]")
            else:
                signal["status"] = status
                logger.info(f"[FINALIZE UPDATE] trade_id={signal.get('trade_id')} status={status} [PIPELINE ENFORCEMENT]")
                pipeline_state_sync()
        else:
            logger.debug(f"[FINALIZE SKIP] trade_id={signal.get('trade_id')} already finalized status={signal.get('status')} [PIPELINE ENFORCEMENT]")
        return
    if ai is None:
        raise RuntimeError("FINALIZE WITHOUT AI — PIPELINE VIOLATION")
    if status:
        signal["status"] = status
    if ai:
        signal["ai"] = ai
        signal["ai_win_prob"] = ai.get("win_prob")
        signal["ai_decision"] = ai.get("decision")
        signal["ai_source"] = ai.get("source")
        ai_direction = ai.get("direction")
        signal["ai_direction_raw"] = ai_direction
        final_direction = ai_direction
        inverted = False
        if state.get("invert_signal", False):
            if ai_direction == "LONG":
                final_direction = "SHORT"
            elif ai_direction == "SHORT":
                final_direction = "LONG"
            inverted = True
            logger.info(f"[DIRECTION CONSISTENCY] INVERSION APPLIED raw_ai={ai_direction} → final_direction={final_direction} inverted={inverted} trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")
        signal["final_direction"] = final_direction
        signal["direction"] = final_direction
        signal["inverted"] = inverted
    if ai and not signal.get("_ai_logged"):
        log_ai(signal, ai)
    signal["_completed"] = True
    signal["_finalized"] = True
    trades_map[signal.get("trade_id")] = {"signal_ref": signal, "ai": ai}
    pipeline_state_sync()
    enforce_log(signal, "COMPLETE")
    logger.info(f"[FINALIZE] trade_id={signal.get('trade_id')} status={signal.get('status')} ai_source={ai.get('source') if ai else 'NONE'} final_direction={signal.get('final_direction')} raw_ai={signal.get('ai_direction_raw')} inverted={signal.get('inverted')} [PIPELINE ENFORCEMENT]")
    assert signal.get("_logged"), "[CRITICAL] Signal not logged"
    assert signal.get("trade_id"), "[CRITICAL] Missing trade_id"
    validate_pipeline_completion(signal)

def exit_pipeline(signal: dict, ai: dict = None, reason: str = "UNKNOWN"):
    signal["status"] = "BLOCKED"
    signal["block_reason"] = reason
    if ai is None:
        ai = {"win_prob":0,"direction":"NO_TRADE","decision":"REJECT","source":"SYSTEM","approved":False}
    log_blocked_signal(signal, ai, reason)
    finalize_signal(signal, ai, "BLOCKED")
    full_pipeline_trace("BLOCKED", reason, signal.get("trade_id"))
    clear_pending_trade()
    return

def full_pipeline_trace(stage: str, reason: str = "", trade_id=None):
    msg = f"[PIPELINE] {stage} | REASON={reason}"
    if trade_id:
        msg += f" | trade_id={trade_id}"
    logger.info(msg + " [PIPELINE ENFORCEMENT]")

def trace(stage: str, msg: str, trade_id: str = None):
    ts = utc_iso()
    tid = f" | trade_id={trade_id}" if trade_id else ""
    logger.info(f"[{ts}] [{stage}] {msg}{tid} [PIPELINE ENFORCEMENT]")

def debug_snapshot(signal=None, ai=None, stage="UNKNOWN"):
    try:
        snapshot = {
            "time": utc_iso(),
            "stage": stage,
            "price": state.get("price"),
            "regime": state.get("regime"),
            "ai": {"direction": ai.get("direction") if ai else None,"decision": ai.get("decision") if ai else None,"win_prob": ai.get("win_prob") if ai else None},
            "threshold": state.get("ai_threshold"),
            "max_pos": state.get("max_active_signals"),
            "active_signals": get_active_signal_count(),
            "execution": {"allowed": execution_allowed(),"reason": state.get("execution_reason")},
            "signal": {"id": signal.get("trade_id") if signal else None,"status": signal.get("status") if signal else None,"direction": signal.get("final_direction") if signal else None},
            "orderflow": {"delta": orderflow["delta"], "imbalance": orderflow["imbalance"]},
            "volume_spike": (len(volume_buffer) > 5 and volume_buffer[-1] > np.mean(volume_buffer) * 1.5),
            "exposure": compute_exposure()
        }
        store_debug_snapshot(stage, snapshot)
        logger.info("\n" + "="*80)
        logger.info(f"[DEBUG SNAPSHOT] {stage}")
        logger.info(json.dumps(snapshot, indent=2, default=str))
        logger.info("="*80 + "\n")
    except Exception as e:
        logger.error(f"[DEBUG SNAPSHOT ERROR] {e} [PIPELINE ENFORCEMENT]")

def ensure_signal_registered(signal: dict):
    if not signal or not signal.get("trade_id"):
        return
    with trade_lock:
        trades_map[signal["trade_id"]] = {"signal_ref": signal}
        logger.info(f"[SIGNAL REGISTERED] trade_id={signal['trade_id']} [PIPELINE ENFORCEMENT]")
    with state_lock:
        if "signals" not in state["signal_info"]:
            state["signal_info"]["signals"] = []
        state["signal_info"]["signals"].append(signal)
        state["signal_info"]["active"] = True
        state["signal_info"]["count"] += 1

def validate_ai_features(ctx):
    required = ["velocity", "volume_ratio", "delta", "imbalance"]
    for k in required:
        if k not in ctx:
            return False, f"MISSING_{k}"
        if ctx[k] is None:
            return False, f"NONE_{k}"
    if all(abs(ctx[k]) < 1e-5 for k in required):
        return False, "ALL_ZERO"
    return True, "OK"

def update_data_quality(features: dict) -> float:
    try:
        valid = 0
        total = 6
        if abs(features.get("delta", 0)) > 0: valid += 1
        if abs(features.get("delta_change", 0)) > 0: valid += 1
        if abs(features.get("imbalance", 0)) > 0: valid += 1
        if abs(features.get("velocity", 0)) > 0: valid += 1
        if features.get("volume", 0) > 0: valid += 1
        if features.get("volume_ratio", 0) > 0: valid += 1
        score = valid / total
        if abs(features.get("delta", 0)) > 10 or features.get("volume_ratio", 0) > 1:
            score += 0.1
        return min(score, 1.0)
    except Exception as e:
        logger.error(f"[DATA QUALITY ERROR] {e} [PIPELINE ENFORCEMENT]")
        return 0.0

def update_feature_snapshot():
    try:
        if len(delta_buffer) >= 2:
            delta_change = delta_buffer[-1] - delta_buffer[-2]
        else:
            delta_change = 0.0
        if delta_change == 0 and len(delta_buffer) > 3:
            delta_change = delta_buffer[-1] - delta_buffer[-3]
        if len(price_buffer) >= 2:
            prev = price_buffer[-2]
            velocity = (price_buffer[-1] - prev) / prev if prev != 0 else 0.0
        else:
            velocity = 0.0
        if abs(velocity) < 1e-5 and len(price_buffer) > 3:
            prev = price_buffer[-3]
            velocity = (price_buffer[-1] - prev) / prev if prev != 0 else 0.0
        vol_mean = np.mean(volume_buffer) if len(volume_buffer) > 0 else 0
        state["feature_snapshot"] = {
            "velocity": float(velocity),
            "volume": float(volume_buffer[-1]) if len(volume_buffer) > 0 else 0,
            "volume_ratio": compute_volume_ratio(),
            "delta": float(delta_buffer[-1]) if len(delta_buffer) > 0 else 0,
            "imbalance": float(imbalance_buffer[-1]) if len(imbalance_buffer) > 0 else 0,
            "delta_change": float(delta_change)
        }
        data_quality = update_data_quality(state["feature_snapshot"])
        with state_lock:
            state["data_quality"] = data_quality
        if state.get("debug_enabled"):
            logger.debug(f"[FEATURES] {state['feature_snapshot']}")
            logger.debug(f"[DATA QUALITY] {data_quality:.3f}")
    except Exception as e:
        logger.error(f"[FEATURE SNAPSHOT ERROR] {e} [PIPELINE ENFORCEMENT]")

def sanitize_features(f):
    for k in f:
        if f[k] is None:
            f[k] = 0.0
    if f.get("delta") == 0 and len(delta_buffer) > 0:
        f["delta"] = delta_buffer[-1]
    if f.get("volume") == 0 and len(volume_buffer) > 0:
        f["volume"] = volume_buffer[-1]
    return f

def update_orderflow(trade):
    try:
        size = float(trade.get('v', trade.get('q', 0)))
        side = trade.get('S', trade.get('side', '')).lower()
        if side == "buy":
            orderflow["buy_volume"] += size
        elif side == "sell":
            orderflow["sell_volume"] += size
        orderflow["delta"] = orderflow["buy_volume"] - orderflow["sell_volume"]
        total = orderflow["buy_volume"] + orderflow["sell_volume"]
        orderflow["imbalance"] = abs(orderflow["delta"]) / total if total > 0 else 0
        orderflow["last_update"] = time.time()
        orderflow["prev_delta"] = orderflow.get("delta", 0.0)
    except Exception as e:
        logger.error(f"[ORDERFLOW ERROR] {e} [PIPELINE ENFORCEMENT]")

def build_pure_ai_context(state_snapshot, buffers):
    ctx = {
        "price": nz(state_snapshot.get("price")),
        "recent_high": nz(state_snapshot.get("support_resistance", {}).get("swing_high")),
        "recent_low": nz(state_snapshot.get("support_resistance", {}).get("swing_low")),
        "dist_to_resistance": nz(state_snapshot.get("support_resistance", {}).get("dist_to_resistance")),
        "dist_to_support": nz(state_snapshot.get("support_resistance", {}).get("dist_to_support")),
        "ema9": nz(state_snapshot.get("ema_status", {}).get("ema9")),
        "ema21": nz(state_snapshot.get("ema_status", {}).get("ema21")),
        "ema200": nz(state_snapshot.get("ema_status", {}).get("ema200")),
        "ema_slope": (nz(state_snapshot.get("ema_status", {}).get("ema9")) - nz(state_snapshot.get("ema_status", {}).get("ema21"))) / nz(state_snapshot.get("ema_status", {}).get("ema21")) if nz(state_snapshot.get("ema_status", {}).get("ema21")) != 0 else 0.0,
        "ret_1m": nz(buffers.get("ret_1m", [0])[-1] if len(buffers.get("ret_1m", [])) > 0 else 0),
        "ret_5m": nz(buffers.get("ret_5m", [0])[-1] if len(buffers.get("ret_5m", [])) > 0 else 0),
        "velocity": get_aggregated(velocity_buffer),
        "volume": get_aggregated(volume_buffer),
        "avg_volume": get_aggregated(volume_buffer),
        "volume_ratio": compute_volume_ratio(),
        "delta": get_aggregated(delta_buffer),
        "delta_change": get_aggregated(delta_change_buffer),
        "imbalance": get_aggregated(imbalance_buffer),
        "candle_range": get_aggregated(candle_range_buffer),
        "wick_ratio": get_aggregated(wick_ratio_buffer),
        "body_ratio": get_aggregated(body_ratio_buffer),
        "edge_score": state.get("last_edge", 0.0),
        "edge_threshold": get_edge_threshold(),
        "regime": state_snapshot.get("regime", "UNKNOWN"),
        "sr_state": state_snapshot.get("support_resistance", {}).get("sr_state", "UNKNOWN"),
        "sr_bias": state_snapshot.get("support_resistance", {}).get("sr_bias", "UNKNOWN"),
        "data_quality": state_snapshot.get("data_quality", 0.0),
        "funding": get_funding_snapshot_for_ai(),
        "market_context": get_market_context_for_ai(),
    }
    ctx = sanitize_features(ctx)
    if ctx["recent_high"] == 0 or ctx["recent_low"] == 0:
        logger.warning("[SR VALIDATION] Invalid SR data - skipping AI [PIPELINE ENFORCEMENT]")
        return None
    return sanitize_ai_inputs(ctx)

def evaluate_sr_direction_filter(direction: str, sr_state: str, market_context: dict = None):
    """
    Phase B soft S/R filter. Blocks fade trades at S/R only when structure + MTF oppose the direction.
    Returns (blocked: bool, reason: str|None).
    """
    if SR_FILTER_MODE == "OFF":
        return False, None
    if direction not in ("LONG", "SHORT"):
        return False, None
    mc = market_context or {}
    ms = mc.get("market_structure", {})
    mtf = mc.get("multi_tf", {})
    struct_score = ms.get("structure_score", 0)
    hh_hl = ms.get("hh_hl_sequence_active", False)
    lh_ll = ms.get("lh_ll_sequence_active", False)
    mtf_agree = mtf.get("agreement", "MIXED")

    if direction == "LONG" and sr_state == "NEAR_RESISTANCE":
        if SR_FILTER_MODE == "HARD" or BLOCK_LONG_NEAR_RESISTANCE:
            return True, "LONG_BLOCKED_NEAR_RESISTANCE"
        if hh_hl or struct_score >= 2 or mtf_agree == "BULL_ALIGNED":
            return False, "SR_SOFT_ALLOW_BULL_CONTINUATION"
        if struct_score <= -2 and mtf_agree == "BEAR_ALIGNED":
            return True, "LONG_BLOCKED_NEAR_RESISTANCE"
        return False, None

    if direction == "SHORT" and sr_state == "NEAR_SUPPORT":
        if SR_FILTER_MODE == "HARD" or BLOCK_SHORT_NEAR_SUPPORT:
            return True, "SHORT_BLOCKED_NEAR_SUPPORT"
        if lh_ll or struct_score <= -2 or mtf_agree == "BEAR_ALIGNED":
            return False, "SR_SOFT_ALLOW_BEAR_CONTINUATION"
        if struct_score >= 2 and mtf_agree == "BULL_ALIGNED":
            return True, "SHORT_BLOCKED_NEAR_SUPPORT"
        return False, None

    return False, None

def parse_ai_factor_block(text: str) -> dict:
    """Extract Phase C bull/bear scores and reason lists from AI response."""
    factors = {
        "reasons_for": [],
        "reasons_against": [],
        "bull_score": 0,
        "bear_score": 0,
        "factor_parse_ok": False,
    }
    if not text:
        return factors
    json_blob = None
    jstart = text.find("```json")
    if jstart >= 0:
        brace = text.find("{", jstart)
        jend = text.find("```", brace + 1)
        chunk = text[brace:jend if jend > brace else len(text)]
        try:
            json_blob = json.loads(chunk.strip().rstrip("`"))
        except Exception:
            json_blob = None
    if json_blob is None:
        brace = text.find("{")
        while brace >= 0:
            try:
                candidate = json.loads(text[brace:text.find("}", brace) + 1])
                if isinstance(candidate, dict) and ("bull_score" in candidate or "reasons_for_trade" in candidate):
                    json_blob = candidate
                    break
            except Exception:
                pass
            brace = text.find("{", brace + 1)
    if isinstance(json_blob, dict):
        factors["reasons_for"] = json_blob.get("reasons_for_trade") or json_blob.get("reasons_for") or []
        factors["reasons_against"] = json_blob.get("reasons_against_trade") or json_blob.get("reasons_against") or []
        factors["bull_score"] = int(json_blob.get("bull_score", 0) or 0)
        factors["bear_score"] = int(json_blob.get("bear_score", 0) or 0)
        factors["factor_parse_ok"] = True
        return factors
    bull_m = re.search(r"Bull\s*score:\s*(\d+)", text, re.IGNORECASE)
    bear_m = re.search(r"Bear\s*score:\s*(\d+)", text, re.IGNORECASE)
    if bull_m:
        factors["bull_score"] = int(bull_m.group(1))
    if bear_m:
        factors["bear_score"] = int(bear_m.group(1))
    if re.search(r"reasons?_for", text, re.IGNORECASE):
        block = re.search(r"reasons?_for(?:_trade)?[:\s]+(.*?)(?:reasons?_against|Bull\s*score|$)", text, re.IGNORECASE | re.DOTALL)
        if block:
            factors["reasons_for"] = [ln.strip("- ").strip() for ln in block.group(1).splitlines() if ln.strip() and not ln.strip().startswith("reasons")]
    if re.search(r"reasons?_against", text, re.IGNORECASE):
        block = re.search(r"reasons?_against(?:_trade)?[:\s]+(.*?)(?:Bull\s*score|Direction:|$)", text, re.IGNORECASE | re.DOTALL)
        if block:
            factors["reasons_against"] = [ln.strip("- ").strip() for ln in block.group(1).splitlines() if ln.strip() and not ln.strip().lower().startswith("bull")]
    factors["factor_parse_ok"] = (factors["bull_score"] > 0 or factors["bear_score"] > 0 or len(factors["reasons_for"]) > 0)
    return factors

def apply_phase_c_factor_gate(ai_result: dict) -> dict:
    """Reject APPROVE when declared direction conflicts with bull/bear scores."""
    if not PHASE_C_FACTOR_GATE_ENABLED:
        return ai_result
    direction = ai_result.get("direction")
    factors = ai_result.get("factors") or {}
    bull = int(factors.get("bull_score", 0) or 0)
    bear = int(factors.get("bear_score", 0) or 0)
    margin = MIN_FACTOR_SCORE_MARGIN
    if ai_result.get("decision") != "APPROVE" or direction in (None, "NO_TRADE"):
        return ai_result
    gate_reason = None
    if direction == "LONG" and bull < bear + margin:
        gate_reason = f"FACTOR_GATE_LONG bull={bull} bear={bear}"
    elif direction == "SHORT" and bear < bull + margin:
        gate_reason = f"FACTOR_GATE_SHORT bull={bull} bear={bear}"
    if gate_reason:
        logger.warning(f"[PHASE-C] {gate_reason} — overriding APPROVE to REJECT [PIPELINE ENFORCEMENT]")
        ai_result["decision"] = "REJECT"
        ai_result["approved"] = False
        ai_result["factor_gate"] = gate_reason
        ai_result["pre_gate_decision"] = "APPROVE"
    return ai_result

def double_confirm_ai(original_ai, ctx):
    try:
        logger.info("[DOUBLE AI] Starting confirmation [PIPELINE ENFORCEMENT]")
        confirm_prompt = f"""Original decision: Direction={original_ai.get('direction')} WinProb={original_ai.get('win_prob')} Decision={original_ai.get('decision')}
Context: {json.dumps(ctx, indent=2)}
Verify if still correct. Return same format."""
        res = requests.post(DEEPSEEK_URL, headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, json={"model": "deepseek-chat","messages": [{"role": "user", "content": confirm_prompt}],"temperature": 0.3}, timeout=AI_TIMEOUT_SEC)
        text = res.json()["choices"][0]["message"]["content"]
        dir_match = re.search(r"Direction:\s*(LONG|SHORT|NO_TRADE)", text, re.IGNORECASE)
        direction = dir_match.group(1).upper() if dir_match else original_ai.get("direction")
        match = re.search(r"Win probability:\s*(\d+)", text)
        win_prob = int(match.group(1)) if match else original_ai.get("win_prob")
        decision_match = re.search(r"Decision:\s*(APPROVE|REJECT)", text, re.IGNORECASE)
        decision = decision_match.group(1).upper() if decision_match else original_ai.get("decision")
        if decision != original_ai.get("decision"):
            logger.warning(f"[AI CONFLICT] Original {original_ai.get('decision')} vs Confirm {decision} — safer REJECT [PIPELINE ENFORCEMENT]")
            return {"win_prob": min(original_ai.get("win_prob",0), win_prob), "direction": "NO_TRADE", "decision": "REJECT", "override": False, "comment": text, "ai_error": False, "factors": {}, "source": "DOUBLE_CONFIRM", "approved": False}
        return original_ai
    except Exception as e:
        logger.error(f"[DOUBLE AI] Failed: {e} — using original [PIPELINE ENFORCEMENT]")
        return original_ai

def evaluate_signal_with_ai(raw_context: dict):
    try:
        logger.info(f"[AI] START evaluate_signal_with_ai [PIPELINE ENFORCEMENT]")
        full_pipeline_trace("[AI]", "EVALUATE_START", raw_context.get("trade_id"))
        trace("AI", "EVALUATE_START", raw_context.get("trade_id"))
        debug_snapshot(None, None, "PRE_AI_EVAL")
        with state_lock:
            price = state.get("price")
        if price is None or price <= 0:
            logger.error("[AI CRITICAL] price missing - HARD FAIL [PIPELINE ENFORCEMENT]")
            full_pipeline_trace("[AI]", "HARD_FAIL_NO_PRICE", raw_context.get("trade_id"))
            trace("AI", "HARD_FAIL_NO_PRICE", raw_context.get("trade_id"))
            debug_snapshot(None, None, "AI_NO_PRICE")
            raise RuntimeError("AI CALLED WITHOUT VALID PRICE - PIPELINE VIOLATION")
        ctx = copy.deepcopy(raw_context)
        if not ctx:
            logger.warning("[AI] Context build failed - using fallback reject [PIPELINE ENFORCEMENT]")
            return {"win_prob": 0, "direction": "NO_TRADE", "decision": "REJECT", "override": False, "comment": "CONTEXT_FALLBACK", "ai_error": True, "factors": {}, "source": "FALLBACK", "approved": False, "trade_id": raw_context.get("trade_id")}
        ctx = sanitize_ai_inputs(ctx)
        ok, reason = validate_ai_features(ctx)
        if not ok:
            logger.warning(f"[AI] Feature validation failed: {reason} - reject without API call [PIPELINE ENFORCEMENT]")
            return {"win_prob": 0, "direction": "NO_TRADE", "decision": "REJECT", "override": False, "comment": f"FEATURE_VALIDATION:{reason}", "ai_error": True, "factors": {}, "source": "VALIDATION", "approved": False, "trade_id": raw_context.get("trade_id")}
        global LAST_AI_PAYLOAD, LAST_AI_TIMESTAMP
        LAST_AI_PAYLOAD = copy.deepcopy(ctx)
        LAST_AI_TIMESTAMP = utc_iso()
        logger.info(f"[AI PAYLOAD SNAPSHOT] {ctx} [PIPELINE ENFORCEMENT]")
        prompt = AI_PROMPT_TEMPLATE.format(context=json.dumps(ctx, indent=2))
        res = requests.post(DEEPSEEK_URL, headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, json={"model": "deepseek-chat","messages": [{"role": "user", "content": prompt}],"temperature": 0.4}, timeout=AI_TIMEOUT_SEC)
        text = res.json()["choices"][0]["message"]["content"]
        logger.info(f"[AI RAW RESPONSE] {text} [PIPELINE ENFORCEMENT]")
        dir_match = re.search(r"Direction:\s*(LONG|SHORT|NO_TRADE)", text, re.IGNORECASE)
        direction = dir_match.group(1).upper() if dir_match else "NO_TRADE"
        match = re.search(r"Win probability:\s*(\d+)", text)
        win_prob = int(match.group(1)) if match else 0
        decision_match = re.search(r"Decision:\s*(APPROVE|REJECT)", text, re.IGNORECASE)
        decision = decision_match.group(1).upper() if decision_match else "REJECT"
        override_match = re.search(r"Override:\s*(YES|NO)", text, re.IGNORECASE)
        override = override_match.group(1).upper() == "YES" if override_match else False
        if win_prob is None or win_prob <= 0:
            win_prob = 0
        if direction not in ["LONG", "SHORT", "NO_TRADE"]:
            direction = "NO_TRADE"
            win_prob = 0
        factors = parse_ai_factor_block(text)
        ai_result = {
            "win_prob": win_prob,
            "direction": direction,
            "decision": decision,
            "override": override,
            "comment": text,
            "ai_error": False,
            "factors": factors,
            "bull_score": factors.get("bull_score", 0),
            "bear_score": factors.get("bear_score", 0),
            "source": "FRESH",
            "approved": decision == "APPROVE",
            "trade_id": ctx.get("trade_id")
        }
        ai_result = apply_phase_c_factor_gate(ai_result)
        if DOUBLE_CONFIRM_AI:
            ai_result = double_confirm_ai(ai_result, ctx)
            ai_result = apply_phase_c_factor_gate(ai_result)
        with state_lock:
            state["last_ai_ts"] = time.time()
            state["last_ai_call_ts"] = time.time()
            state["ai_history"].append({
                "time": utc_iso(),
                "trade_id": ai_result.get("trade_id"),
                "ai_direction_raw": ai_result.get("direction"),
                "final_direction": ai_result.get("direction"),
                "inverted": False,
                "decision": ai_result.get("decision"),
                "win_prob": ai_result.get("win_prob"),
                "edge_score": state.get("last_edge", 0.0),
                "edge_threshold": get_edge_threshold(),
                "source": "AI",
                "comment": ai_result.get("comment"),
                "final_outcome": state.get("execution_outcome", "PENDING")
            })
            state["ai_history"] = state["ai_history"][-5:]
            state["ai_history_updated"] = time.time()
            state["ai_outcome"] = ai_result.get("decision")
            state["ai_decision"] = ai_result.get("decision")
            state["pipeline_outcome"] = "AI_EVALUATED"
            state["last_ai"]["win_prob"] = ai_result.get("win_prob")
            state["last_ai"]["direction"] = ai_result.get("direction")
            state["last_ai"]["decision"] = ai_result.get("decision")
            state["last_ai"]["reason"] = ai_result.get("comment")
        logger.info(
            f"[AI CALL] FRESH dir={direction} prob={win_prob} decision={ai_result.get('decision')} "
            f"bull={factors.get('bull_score')} bear={factors.get('bear_score')} "
            f"gate={ai_result.get('factor_gate', 'none')} approved={ai_result['approved']} [PIPELINE ENFORCEMENT]"
        )
        full_pipeline_trace("[AI]", "EVALUATE_COMPLETE", raw_context.get("trade_id"))
        trace("AI", "EVALUATE_COMPLETE", raw_context.get("trade_id"))
        debug_snapshot(None, ai_result, "POST_AI_EVAL")
        if not ai_result.get("ai_error"):
            with state_lock:
                state["ai_call_count"] = state.get("ai_call_count", 0) + 1
        logger.info(f"[AI RESULT] decision={ai_result['decision']} prob={ai_result['win_prob']} [PIPELINE ENFORCEMENT]")
        update_debug_state_always("AI_COMPLETE", {"ai_decision": ai_result.get("decision")})
        return ai_result
    except Exception as e:
        logger.error(f"[AI CRASH] {e} [PIPELINE ENFORCEMENT]")
        ai_result = {"win_prob": 0, "direction": "NO_TRADE", "decision": "REJECT", "override": False, "comment": f"AI_CRASH_FAILSAFE: {e}", "ai_error": True, "factors": {}, "source": "ERROR", "approved": False, "trade_id": raw_context.get("trade_id")}
        full_pipeline_trace("[AI]", "EVALUATE_CRASH", raw_context.get("trade_id"))
        trace("AI", "EVALUATE_CRASH", raw_context.get("trade_id"))
        debug_snapshot(None, ai_result, "AI_CRASH")
        if not ai_result.get("ai_error"):
            with state_lock:
                state["ai_call_count"] = state.get("ai_call_count", 0) + 1
            state["ai_history"].append({
                "time": utc_iso(),
                "trade_id": ai_result.get("trade_id"),
                "ai_direction_raw": ai_result.get("direction"),
                "final_direction": ai_result.get("direction"),
                "inverted": False,
                "decision": ai_result.get("decision"),
                "win_prob": ai_result.get("win_prob"),
                "edge_score": state.get("last_edge", 0.0),
                "edge_threshold": get_edge_threshold(),
                "source": "ERROR",
                "comment": ai_result.get("comment"),
                "final_outcome": "CRASH"
            })
            state["ai_history"] = state["ai_history"][-5:]
            state["ai_history_updated"] = time.time()
            state["ai_outcome"] = ai_result.get("decision")
            state["ai_decision"] = ai_result.get("decision")
            state["pipeline_outcome"] = "AI_CRASH"
            state["last_ai"]["win_prob"] = ai_result.get("win_prob")
            state["last_ai"]["direction"] = ai_result.get("direction")
            state["last_ai"]["decision"] = ai_result.get("decision")
            state["last_ai"]["reason"] = ai_result.get("comment")
        logger.info(f"[AI RESULT] decision={ai_result['decision']} prob={ai_result['win_prob']} [PIPELINE ENFORCEMENT]")
        return ai_result

def get_ai_threshold():
    with state_lock:
        t = state.get("ai_threshold")
        return 60 if t is None else float(t)

def set_ai_threshold(value):
    with state_lock:
        state["ai_threshold"] = float(value) if value is not None else 60
        state["_threshold_locked"] = True
        state["last_ai_ts"] = 0
        state["last_ai_signal_key"] = None
        state["last_ai_confidence"] = 0
        state["ai_verdict"] = f"AI reviewer {'ON' if state['ai_enabled'] else 'OFF'} | Threshold {state['ai_threshold']}"
        save_persistent_config()
        logger.info(f"[SET] AI threshold locked to {state['ai_threshold']} [PIPELINE ENFORCEMENT]")

def set_edge_threshold(value):
    value = round(float(value), 1)
    if value not in [round(x, 1) for x in EDGE_OPTIONS]:
        logger.warning(f"[EDGE SET] Invalid value {value} - rejected [PIPELINE ENFORCEMENT]")
        return
    with state_lock:
        state["edge_threshold"] = value
        save_persistent_config()
        logger.info(f"[SET] EDGE threshold set to {state['edge_threshold']} [PIPELINE ENFORCEMENT]")
        enforce_edge_threshold_options()

def hash_context(ctx):
    try:
        return hash((
            round(safe_float(ctx.get("price")), 2),
            safe_float(ctx.get("volume_ratio")),
            safe_float(ctx.get("delta")),
            safe_float(ctx.get("delta_change"))
        ))
    except:
        return hash(str(ctx))

def compute_ai_score(ai, ctx):
    score = 0.0
    score += nz(ai.get("win_prob")) * 0.5
    score += nz(ai.get("confidence", 50)) * 0.2
    if ctx.get("volume_ratio", 0) > 1.3:
        score += 15
    if abs(ctx.get("delta_change", 0)) > 0.08:
        score += 10
    if ctx.get("wick_ratio", 0) > 0.55:
        score += 8
    return max(0, min(100, score))

def decision_from_score(score):
    if score >= 80:
        return "STRONG_TRADE"
    elif score >= 65:
        return "TRADE"
    elif score >= 55:
        return "WEAK_TRADE"
    return "NO_TRADE"

def should_call_ai(ctx, event_trigger):
    now = time.time()
    if now - state.get("last_ai_call_ts", 0) < AI_COOLDOWN_SECONDS:
        logger.info(f"[AI] COOLDOWN SKIP remaining={(AI_COOLDOWN_SECONDS - (now - state.get('last_ai_call_ts', 0))):.0f}s [PIPELINE ENFORCEMENT]")
        return False, "COOLDOWN"
    if event_trigger:
        return True, "EVENT"
    if now - state.get("last_ai_call_ts", 0) > HEARTBEAT_INTERVAL and safe_float(ctx.get("volume_ratio", 0)) > 1.1:
        return True, "HEARTBEAT"
    return False, "NO_TRIGGER"

def log_setup(signal):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_setup"
        with csv_lock:
            row = {"ts": utc_iso(),"trade_id": signal.get("trade_id"),"price": signal.get("signal_price"),"ai_win_prob": signal.get("ai_win_prob", 0),"regime": signal.get("regime"),"direction": signal.get("final_direction"),"event": "BUILD","edge_score": signal.get("edge_score_at_entry")}
            dynamic_csv_writer(CSV_SETUP_LOG, row)
        logger.info(f"[LOG SETUP] trade_id={signal.get('trade_id')} ai_win_prob={signal.get('ai_win_prob',0)} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"SETUP LOG FAIL: {e} [PIPELINE ENFORCEMENT]")

def log_ai(signal, ai):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_ai"
        signal["_ai_logged"] = True
        with csv_lock:
            row = {"ts": utc_iso(),"trade_id": signal.get("trade_id"),"ai_direction_raw": ai.get("direction"),"final_direction": signal.get("final_direction"),"inverted": signal.get("inverted", False),"approved": ai.get("approved", False),"win_prob": ai.get("win_prob"),"comment": ai.get("comment"),"source": ai.get("source"),"event": "AI_DECISION","decision": ai.get("decision"),"override": ai.get("override", False),"full_comment": ai.get("comment"),"edge_score": signal.get("edge_score_at_entry")}
            dynamic_csv_writer(CSV_AI_TRANCHE, row)
        logger.info(f"[LOG AI] trade_id={signal.get('trade_id')} prob={ai.get('win_prob')} source={ai.get('source')} decision={ai.get('decision')} override={ai.get('override')} final_direction={signal.get('final_direction')} inverted={signal.get('inverted')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"AI LOG FAIL: {e} [PIPELINE ENFORCEMENT]")

def log_decision(signal, decision, reason):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_decision"
        with csv_lock:
            row = {
                "ts": utc_iso(),
                "trade_id": signal.get("trade_id"),
                "decision": decision,
                "reason": reason,
                "ai_win_prob": signal.get("ai_win_prob"),
                "ai_threshold": get_ai_threshold(),
                "ai_decision_text": signal.get("ai_decision"),
                "edge_score": signal.get("edge_score_at_entry", 0.0),
                "invert_signal": state.get("invert_signal", False),
                "early_fail_enabled": state.get("early_fail_enabled", True),
                "edge_threshold": get_edge_threshold(),
                "experiment_tag": f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}",
                "features_price": signal.get("features",{}).get("price"),
                "features_ema9": signal.get("features",{}).get("ema9"),
                "features_delta": signal.get("features",{}).get("delta"),
                "controls_edge": signal.get("controls",{}).get("edge_threshold"),
                "final_direction": signal.get("final_direction")
            }
            dynamic_csv_writer(CSV_DECISIONS, row)
        logger.info(f"[LOG DECISION] trade_id={signal.get('trade_id')} decision={decision} reason={reason} ai_decision_text={signal.get('ai_decision')} experiment={row.get('experiment_tag')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"DECISION LOG FAIL: {e} [PIPELINE ENFORCEMENT]")

def log_trade(trade):
    try:
        assert trade.get("trade_id"), "[CRITICAL] trade_id missing in log_trade"
        required = ["trade_id", "entry", "exit", "net_pnl_usd", "exit_reason"]
        for f in required:
            if trade.get(f) is None:
                logger.error(f"[CSV BLOCK] Missing required field {f} [PIPELINE ENFORCEMENT]")
                return
        if "conf" in trade:
            trade.pop("conf", None)
        with csv_lock:
            dynamic_csv_writer(CSV_TRADES, trade)
        logger.info(f"[CSV] Trade logged trade_id={trade.get('trade_id')} exit={trade.get('exit_reason')} ai_source={trade.get('ai_source')} final_direction={trade.get('final_direction')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"CSV TRADE WRITE FAILED: {e} [PIPELINE ENFORCEMENT]")

def log_blocked_signal(signal, ai, reason):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_blocked_signal"
        sr = state.get("support_resistance", {})
        with csv_lock:
            row = {"ts": utc_iso(), "trade_id": signal.get("trade_id", "duplicate"), "dir": signal.get("final_direction", signal.get("dir", "UNKNOWN")), "ai_win_prob": ai.get("win_prob"), "ai_threshold": get_ai_threshold(), "ai_approved": ai.get("approved", False), "reason": reason, "ai_source": ai.get("source","UNKNOWN"),"structure": sr.get("sr_state", "UNKNOWN"),"participation": state.get("ema_status", {}).get("ema_spread", 0.0),"context": state.get("regime", "UNKNOWN"),"ai_decision_text": ai.get("decision"),"price": signal.get("price"),"edge_score": signal.get("edge_score_at_entry"),"final_direction": signal.get("final_direction")}
            if "features" in signal:
                row.update({f"features_{k}": v for k,v in signal["features"].items()})
            if "context" in signal:
                row.update({f"context_{k}": v for k,v in signal["context"].items()})
            if "controls" in signal:
                row.update({f"controls_{k}": v for k,v in signal["controls"].items()})
            if "decision" in signal:
                row.update({f"decision_{k}": v for k,v in signal["decision"].items()})
            row["invert_signal"] = state.get("invert_signal", False)
            row["early_fail_enabled"] = state.get("early_fail_enabled", True)
            row["edge_threshold"] = get_edge_threshold()
            row["experiment_tag"] = f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}"
            dynamic_csv_writer(CSV_BLOCKS, row)
        logger.info(f"[CSV] Blocked signal logged reason={reason} trade_id={signal.get('trade_id')} structure={sr.get('sr_state')} ai_decision={ai.get('decision')} experiment={row.get('experiment_tag')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"CSV BLOCKED WRITE FAILED: {e} [PIPELINE ENFORCEMENT]")

def log_no_signal_with_context(signal=None, reason="NO_SETUP_DETECTED"):
    trade_id = str(uuid.uuid4()) if not signal or not signal.get("trade_id") else signal.get("trade_id")
    edge_score = state.get("last_edge", state.get("debug_state", {}).get("last_edge_score", 0.0))
    stub = {
        "trade_id": trade_id,
        "edge_score_at_entry": round(float(edge_score or 0.0), 1),
        "features": state.get("debug_state", {}).get("last_features", {}),
    }
    enforce_log(stub, "NO_SIGNAL", reason)
    logger.info(f"[PIPELINE] No signal - skipping cycle | trade_id={trade_id} reason={reason} [PIPELINE ENFORCEMENT]")
    full_pipeline_trace("BLOCKED", reason, trade_id)

def compute_structural_sr(candles):
    if len(candles) < 96:
        return None, None
    highs = [c[2] for c in candles[-96:]]
    lows = [c[3] for c in candles[-96:]]
    return max(highs), min(lows)

def sr_context(price, swing_high, swing_low):
    if not price or not swing_high or not swing_low:
        return 0.0, 0.0, False, False
    dist_high = abs((nz(swing_high) - nz(price)) / nz(price))
    dist_low = abs((nz(price) - nz(swing_low)) / nz(price))
    near_res = dist_high <= SR_ZONE_PCT
    near_sup = dist_low <= SR_ZONE_PCT
    return dist_high, dist_low, near_res, near_sup

def classify_sr_state(dist_high, dist_low):
    if dist_high <= SR_ZONE_PCT and dist_low <= SR_ZONE_PCT:
        return "RANGE_COMPRESSION"
    if dist_high <= SR_ZONE_PCT:
        return "NEAR_RESISTANCE"
    if dist_low <= SR_ZONE_PCT:
        return "NEAR_SUPPORT"
    return "FREE_RANGE"

def sr_bias(sr_state, regime):
    if regime == "BULL":
        return "LONG_PREFERRED"
    if regime == "BEAR":
        return "SHORT_PREFERRED"
    if sr_state == "NEAR_RESISTANCE":
        return "SHORT_PREFERRED"
    if sr_state == "NEAR_SUPPORT":
        return "LONG_PREFERRED"
    if sr_state == "RANGE_COMPRESSION":
        return "NO_TRADE"
    return "BOTH_ALLOWED"

def validate_pipeline_completion(signal: dict):
    if not signal or not signal.get("trade_id"):
        logger.error("[PIPELINE VALIDATION] CRITICAL - Missing trade_id at completion [PIPELINE ENFORCEMENT]")
        return False
    VALID_FINAL_STATES = ["ACTIVE", "ORDERED", "REJECTED", "BLOCKED", "EXPIRED", "COMPLETE"]
    if signal.get("status") not in VALID_FINAL_STATES:
        logger.warning(f"[PIPELINE WARNING] Non-terminal state: {signal.get('status')} trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")
        enforce_log(signal, "PIPELINE_WARNING")
        full_pipeline_trace("BLOCKED", "PIPELINE_WARNING", signal.get('trade_id'))
        return False
    logger.info(f"[PIPELINE VALIDATION] COMPLETE trade_id={signal.get('trade_id')} status={signal.get('status')} [PIPELINE ENFORCEMENT]")
    return True

def is_system_ready():
    with state_lock:
        price_ok = state.get("price") is not None and state.get("price") > 0
        ws_ok = state.get("ws_ready", False) and (time.time() - state.get("price_ts", 0) < STALE_HARD_SEC)
        buffer_ready = len(volume_buffer) >= BUFFER_MIN and len(price_buffer) >= BUFFER_MIN and len(delta_buffer) >= BUFFER_MIN
        return price_ok and ws_ok and buffer_ready

def pipeline_guard(stage: str, signal: dict) -> bool:
    if not signal or not signal.get("trade_id"):
        logger.error(f"[PIPELINE GUARD] Missing signal at stage {stage} [PIPELINE ENFORCEMENT]")
        full_pipeline_trace("[GUARD]", f"FAIL_{stage}", None)
        return False
    if not signal.get("trade_id"):
        logger.error(f"[PIPELINE GUARD] INVALID SIGNAL - no id at stage {stage} [PIPELINE ENFORCEMENT]")
        return False
    return True

def update_signal_pull_metrics(price):
    return

def is_data_stale():
    return (time.time() - state.get("ws_last_tick", 0)) > STALE_HARD_SEC

def is_data_consistent():
    return (len(latest_candles) >= MIN_CANDLES and state.get("price") is not None and not is_data_stale())

def is_engine_halted():
    return state.get("execution_paused", False) and state.get("execution_reason") == "STALE_DATA_HARD_STOP"

def should_run_pipeline() -> bool:
    if len(latest_candles) < MIN_CANDLES:
        logger.warning("[WARMUP BLOCK] Not enough candles [PIPELINE ENFORCEMENT]")
        return False
    if not is_system_ready():
        logger.warning("[SYSTEM BLOCK] Not ready [PIPELINE ENFORCEMENT]")
        return False
    if state.get("data_quality", 0.0) < 0.6:
        logger.warning("[DATA BLOCK] Quality below threshold [PIPELINE ENFORCEMENT]")
        return False
    if is_data_stale():
        logger.critical("[STALE BLOCK] Hard stop [PIPELINE ENFORCEMENT]")
        set_execution_paused("STALE_DATA_HARD_STOP")
        return False
    now = time.time()
    if now - last_pipeline_run < MIN_PIPELINE_INTERVAL:
        return False
    return True

def detect_event_light():
    try:
        features = build_full_feature_snapshot()
        if features is None:
            return {"event_trigger": False, "edge_score": 0.1, "price": 0, "timestamp": utc_iso(), "features": {}}
        edge_score = compute_edge_score(features)
        price = nz(state.get("price"))
        if price <= 0:
            return {"event_trigger": False, "edge_score": 0.1, "price": 0, "timestamp": utc_iso(), "features": features}

        global last_event_trigger
        now = time.time()

        if now - last_event_trigger < 0.5:
            return {"event_trigger": False, "edge_score": edge_score, "price": price, "timestamp": utc_iso(), "features": features}

        event_trigger = is_edge_valid(edge_score)

        last_event_trigger = now

        logger.info(
            f"[EVENT LIGHT V2] edge={edge_score:.1f} trigger={event_trigger} threshold={get_edge_threshold():.1f} [PIPELINE ENFORCEMENT]"
        )

        update_debug_state_always("EVENT_DETECTED", {"edge": edge_score, "trigger": event_trigger})

        return {
            "event_trigger": event_trigger,
            "edge_score": round(edge_score, 1),
            "components": {
                "momentum": 0,
                "orderflow": 0,
                "volume": 0,
                "breakout": 0,
                "liquidity": 0
            },
            "price": price,
            "timestamp": utc_iso(),
            "features": features
        }

    except Exception as e:
        logger.error(f"[EVENT ERROR V2] {e} [PIPELINE ENFORCEMENT]")
        return {"event_trigger": False, "edge_score": 0.1, "price": 0, "timestamp": utc_iso(), "features": {}}

def monitor_positions():
    pass

def _evict_oldest_pending_if_at_capacity(max_slots: int) -> bool:
    cleanup_expired_orders()
    reconcile_stale_signals()
    with state_lock:
        max_active = max_slots or state.get("max_active_signals") or MAX_CONCURRENT_POSITIONS_DEFAULT
    evicted = False
    while get_active_signal_count() >= max_active:
        with trade_lock:
            pending = [o for o in pending_orders if o.get("status") == "PENDING"]
            if not pending:
                break
            oldest = min(pending, key=lambda o: o.get("created_ts", time.time()))
            pending_orders.remove(oldest)
        expire_signal_for_order(oldest, "CAPACITY_REPLACED")
        evicted = True
        logger.info(f"[CAPACITY] Evicted oldest pending trade_id={oldest.get('trade_id')} to make room ({max_active} max) [PIPELINE ENFORCEMENT]")
    if evicted:
        pipeline_state_sync()
    return evicted

def ensure_signal_capacity() -> bool:
    """Evict stale pending orders if needed, then return True if a new signal slot is available."""
    max_active = get_effective_max_active_signals()
    _evict_oldest_pending_if_at_capacity(max_active)
    return get_active_signal_count() < max_active

def execute_simulated_order(signal):
    max_active = get_effective_max_active_signals()
    _evict_oldest_pending_if_at_capacity(max_active)
    logger.info(f"[SIM] Simulated order created trade_id={signal.get('trade_id')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    full_pipeline_trace("[EXECUTION]", "SIM_ORDER_CREATED", signal.get("trade_id"))
    price = state.get("price")
    pullback_pct = signal.get("pullback_pct", 0.001)
    signal_price = signal.get("signal_price", price)
    if signal["final_direction"] == "LONG":
        limit_price = signal_price * (1 - pullback_pct)
    else:
        limit_price = signal_price * (1 + pullback_pct)
    margin_usdt = float(signal.get("margin_usdt") or FIXED_MARGIN_USDT)
    lev = state.get("leverage", DEFAULT_RESEARCH_LEVERAGE)
    qty = margin_usdt * lev / price
    order = {
        "trade_id": signal["trade_id"],
        "side": map_signal_to_exchange_side(signal["final_direction"]),
        "signal_dir": signal["final_direction"],
        "limit_price": limit_price,
        "qty": qty,
        "status": "PENDING",
        "created_ts": time.time(),
        "entry_type": "SIM_LIMIT",
        "signal_price": signal.get("signal_price"),
        "fee_type": "MAKER"
    }
    signal["limit_price"] = limit_price
    signal["qty"] = qty
    signal["order_created_ts"] = time.time()
    signal["status"] = "ORDERED"
    signal["order_placed"] = True
    with trade_lock:
        pending_orders.append(order)
    logger.info(f"[SIM] ORDER CREATED trade_id={signal.get('trade_id')} signal_price={fmt(signal_price)} limit_price={fmt(limit_price)} pullback={pullback_pct*100}% final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    if state.get("strategy_mode") == "RESEARCH" and RESEARCH_INSTANT_FILL:
        order["limit_price"] = price
        order["entry_type"] = "SIM_MARKET"
        order["fee_type"] = "TAKER"
        fill_order(order)
        logger.info(f"[SIM] RESEARCH instant fill at {fmt(price)} trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")
    pipeline_state_sync()
    return True

def process_pending_orders():
    now = time.time()
    price = state.get("price")
    if price is None or price <= 0:
        return
    with trade_lock:
        for order in list(pending_orders):
            if order.get("status") != "PENDING":
                continue
            if order["side"] == "buy" and price <= order["limit_price"]:
                order["status"] = "FILLED"
                fill_order(order)
            elif order["side"] == "sell" and price >= order["limit_price"]:
                order["status"] = "FILLED"
                fill_order(order)

def fill_order(order):
    logger.info(f"[ORDER] FILLED trade_id={order['trade_id']} final_direction={order.get('signal_dir')} [PIPELINE ENFORCEMENT]")
    with trade_lock:
        if order in pending_orders:
            pending_orders.remove(order)
    meta = trades_map.get(order["trade_id"], {})
    signal = meta.get("signal_ref", {})
    ai = meta.get("ai", {}) or signal.get("ai", {})
    if order["signal_dir"] not in ["LONG", "SHORT"]:
        raise Exception("Invalid signal direction")
    with trade_lock:
        open_positions.append(_build_open_position(order, signal, ai))
    master = trades_map.get(order["trade_id"], {}).get("signal_ref")
    if master:
        master.update({"status": "FILLED", "filled_ts": time.time(), "fill_price": order["limit_price"], "outcome": "OPEN"})
    persist_signal(master or signal, "FILLED")
    logger.info(f"[ORDER] POSITION OPENED from LIMIT {order['signal_dir']} qty={order['qty']} [PIPELINE ENFORCEMENT]")
    pipeline_state_sync()

def process_positions():
    price = state.get("price")
    if price is None or price <= 0:
        return
    now = time.time()
    process_funding_accrual()
    with trade_lock:
        for pos in list(open_positions):
            if not isinstance(pos, dict) or pos.get("status") != "OPEN":
                continue
            _apply_position_exits(pos, price, now)

def place_postonly_tp(pos, target_pct):
    entry = pos.get("entry", 0)
    lev = pos.get("leverage", 20)
    direction = pos.get("dir")
    tp_price = compute_tp(entry, direction, target_pct, lev)
    order = {
        "trade_id": pos["trade_id"],
        "side": "sell" if direction == "LONG" else "buy",
        "limit_price": tp_price,
        "qty": pos.get("qty", 0),
        "status": "PENDING",
        "created_ts": time.time(),
        "entry_type": "POSTONLY_TP",
        "signal_price": pos.get("signal_price"),
        "fee_type": "MAKER"
    }
    with trade_lock:
        pending_orders.append(order)
    logger.info(f"[POSTONLY TP] Placed at {fmt(tp_price)} for {target_pct}% trade_id={pos.get('trade_id')} [PIPELINE ENFORCEMENT]")

def classify_setup(features):
    if features.get("velocity", 0) > 0 and features.get("delta", 0) > 0:
        return "MOMENTUM_LONG"
    elif features.get("velocity", 0) < 0 and features.get("delta", 0) < 0:
        return "MOMENTUM_SHORT"
    elif abs(features.get("delta", 0)) > 10 and features.get("volume_ratio", 0) > 1.5:
        return "ORDERFLOW_SPIKE"
    else:
        return "WEAK_SETUP"

def atomic_freeze_signal(signal, edge_score):
    signal["features"] = copy.deepcopy(state.get("feature_snapshot", {}))
    signal["edge_score_at_entry"] = float(round(edge_score, 1))
    signal["edge_threshold_at_entry"] = float(get_edge_threshold())
    signal["edge_passed"] = edge_score >= get_edge_threshold()
    signal["controls"] = copy.deepcopy({
        "early_fail_enabled": state.get("early_fail_enabled"),
        "invert_signal": state.get("invert_signal"),
        "ai_enabled": state.get("ai_enabled"),
        "edge_threshold": get_edge_threshold(),
        "ai_threshold": get_ai_threshold(),
        "leverage": state.get("leverage"),
        "pullback_threshold": state.get("pullback_threshold")
    })
    signal["indicators"] = copy.deepcopy(state.get("ema_status", {}))
    signal["counterfactuals"] = {
        "edge_2_0": edge_score >= 2.0,
        "edge_2_5": edge_score >= 2.5,
        "edge_3_0": edge_score >= 3.0,
        "ai_50": signal.get("ai_win_prob", 0) >= 50,
        "ai_60": signal.get("ai_win_prob", 0) >= 60,
        "ai_70": signal.get("ai_win_prob", 0) >= 70
    }
    signal["setup_type"] = classify_setup(signal["features"])
    signal["_frozen"] = True
    logger.info(f"[ATOMIC FREEZE SUCCESS] trade_id={signal.get('trade_id')} edge={round(edge_score,1):.1f} frozen=True [PIPELINE ENFORCEMENT]")

def log_near_edge(candidate, edge_score):
    try:
        with csv_lock:
            row = {
                "ts": utc_iso(),
                "edge_score": round(edge_score, 1),
                "threshold": get_edge_threshold(),
                "price": nz(state.get("price")),
                "reason": "NEAR_EDGE",
                "delta": nz(candidate.get("delta")),
                "volume_ratio": nz(candidate.get("volume_ratio")),
                "experiment_tag": f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}"
            }
            dynamic_csv_writer("near_edge.log", row)
        logger.info(f"[NEAR_EDGE] edge={round(edge_score,1):.1f} logged for analysis [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"[NEAR_EDGE LOG FAIL] {e} [PIPELINE ENFORCEMENT]")

def process_signal(event: dict):
    global last_signal_create_ts, last_ai_call_ts, last_signal_key, last_ai_signal_key, last_processed_candle_ts, last_ai_call_ts, last_price_for_debounce, last_signal_process_ts, last_signal_create_ts, test_signal_fired, prev_price, prev_delta, avg_volume, recent_high, recent_low, rejection_strength, last_signal_hash, last_pipeline_run, last_edge_compute
    with process_lock:
        try:
            with state_lock:
                if state.get("last_pipeline_stage") == "RUNNING":
                    logger.warning("[PIPELINE] RE-ENTRY BLOCKED - logging as no_signal [PIPELINE ENFORCEMENT]")
                    log_no_signal_with_context(reason="REENTRY_GUARD")
                    return
                state["last_pipeline_stage"] = "RUNNING"

            logger.info("[PIPELINE] → ENTER process_signal - full pipeline enforced [PIPELINE ENFORCEMENT]")
            update_debug_state_always("PIPELINE_ENTER")
            if not is_buffer_ready():
                logger.warning("[BUFFER] Not enough data for stable features - skipping [PIPELINE ENFORCEMENT]")
                log_no_signal_with_context(reason="BUFFER_NOT_READY")
                state["last_pipeline_stage"] = "IDLE"
                return
            features = build_full_feature_snapshot()
            if features is None or not is_valid_feature_set(features):
                logger.warning("[PIPELINE] LOW_QUALITY_ENV - continuing for research [PIPELINE ENFORCEMENT]")
            edge_score = compute_edge_score(features)
            logger.info(f"[PIPELINE] edge computed = {edge_score:.1f} [PIPELINE ENFORCEMENT]")

            if edge_score > 1.5:
                log_near_edge(features, edge_score)

            event_obj = event if event else detect_event_light()
            if not event_obj:
                event_obj = {"event_trigger": False, "edge_score": edge_score, "price": nz(state.get("price")), "timestamp": utc_iso(), "features": features}

            if edge_score < get_edge_threshold():
                logger.info(f"[EDGE GATE] edge_score={edge_score:.1f} < threshold — NO_SIGNAL [PIPELINE ENFORCEMENT]")
                log_no_signal_with_context(reason="EDGE_FAIL")
                full_pipeline_trace("BLOCKED", "EDGE_FAIL", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "EDGE_FAIL"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "EDGE_FAIL"
                update_debug_state_always("EDGE_FAIL", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            logger.info(f"[PIPELINE] EDGE PASSED → proceeding to candidate stage (event flag only metadata) [PIPELINE ENFORCEMENT]")

            safe_clear_pending()
            now = time.time()
            last_pipeline_run = now
            full_pipeline_trace("[PIPELINE]", "ENTER_process_signal", None)

            with state_lock:
                max_active = state.get("max_active_signals") or MAX_CONCURRENT_POSITIONS_DEFAULT
            if not ensure_signal_capacity():
                active = get_active_signal_count()
                logger.info(f"[MAX ACTIVE SIGNALS] Hard block at entry - {active}/{max_active} [PIPELINE ENFORCEMENT]")
                _agent_dbg("H1", "process_signal", "max_active_block", {"active": active, "max_active": max_active, "pending_list": len(pending_orders), "positions": len(open_positions), "fix_version": EXECUTION_FIX_VERSION})
                full_pipeline_trace("BLOCKED", "MAX_ACTIVE_SIGNALS", None)
                update_debug_state_always("MAX_ACTIVE_SIGNALS", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if time.time() - state.get("last_signal_create_ts", 0) < GLOBAL_SIGNAL_COOLDOWN:
                logger.info("[GLOBAL COOLDOWN] 5min block active after any prior signal attempt [PIPELINE ENFORCEMENT]")
                full_pipeline_trace("BLOCKED", "GLOBAL_COOLDOWN", None)
                update_debug_state_always("GLOBAL_COOLDOWN", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            current_bucket = int(time.time() / 5)
            signal_key = f"{round(state.get('price',0),2)}_{round(edge_score,1)}_{current_bucket}"
            if signal_key == state.get("last_signal_key"):
                logger.info("[SIGNAL BLOCKED] DUPLICATE CONTEXT - LOGGED [PIPELINE ENFORCEMENT]")
                full_pipeline_trace("BLOCKED", "DUPLICATE", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "DUPLICATE"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "DUPLICATE"
                update_debug_state_always("DUPLICATE", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return
            state["last_signal_key"] = signal_key

            if now - state.get("last_ai_call_ts", 0) < AI_COOLDOWN_SECONDS:
                logger.info(f"[AI] COOLDOWN ACTIVE - BLOCK BEFORE SIGNAL CREATION [PIPELINE ENFORCEMENT]")
                full_pipeline_trace("BLOCKED", "AI_COOLDOWN_ACTIVE", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "AI_COOLDOWN_ACTIVE"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "AI_COOLDOWN_ACTIVE"
                update_debug_state_always("AI_COOLDOWN_ACTIVE", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            buffers = {
                "ret_1m": ret_1m_buffer,
                "ret_5m": ret_5m_buffer,
                "velocity": velocity_buffer,
                "volume": volume_buffer,
                "avg_volume": get_aggregated(volume_buffer),
                "delta": delta_buffer,
                "delta_change": delta_change_buffer,
                "imbalance": imbalance_buffer,
                "range": candle_range_buffer,
                "wick_ratio": wick_ratio_buffer,
                "body_ratio": body_ratio_buffer
            }
            ctx = build_pure_ai_context(state, buffers)
            if not ctx:
                enforce_log({"trade_id": str(uuid.uuid4())}, "BLOCKED", "CTX_FAIL")
                full_pipeline_trace("BLOCKED", "CTX_FAIL", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "CTX_FAIL"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "CTX_FAIL"
                update_debug_state_always("CTX_FAIL", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            ai = evaluate_signal_with_ai(ctx)
            last_ai_call_ts = time.time()
            if not ai:
                enforce_log({"trade_id": str(uuid.uuid4())}, "BLOCKED", "AI_FAIL")
                full_pipeline_trace("BLOCKED", "AI_FAIL", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "AI_FAIL"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "AI_FAIL"
                update_debug_state_always("AI_FAIL", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if ai.get("decision") != "APPROVE":
                trade_id = str(uuid.uuid4())
                block_tag = ai.get("factor_gate") or f"AI_{ai.get('decision')}"
                enforce_log({"trade_id": trade_id}, "BLOCKED", block_tag)
                full_pipeline_trace("BLOCKED", block_tag, trade_id)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = block_tag
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = block_tag
                update_debug_state_always(block_tag, {"edge": edge_score, "bull": ai.get("bull_score"), "bear": ai.get("bear_score")})
                state["last_pipeline_stage"] = "IDLE"
                return

            trade_id = str(uuid.uuid4())
            signal = {
                "trade_id": trade_id,
                "status": "INIT",
                "created_ts": utc_iso(),
                "created_ts_ts": now,
                "snapshot_ts": time.time(),
                "features_at_signal": copy.deepcopy(state.get("feature_snapshot", {})),
                "ai_input": copy.deepcopy(ctx),
                "ai_output": copy.deepcopy(ai),
                "timing": {"signal_ts": now, "order_ts": None, "fill_ts": None},
                "near_miss": False,
                "time_features": {},
                "feature_derivatives": {},
                "market_structure": copy.deepcopy(ctx.get("market_context", {})),
                "exit_context": None,
                "_frozen": False
            }
            atomic_freeze_signal(signal, edge_score)
            enforce_immutable(signal)
            ensure_signal_registered(signal)
            log_setup(signal)
            full_pipeline_trace("[PIPELINE]", "START", trade_id)
            logger.info("[PIPELINE] SINGLE ENTRY - EDGE GATE FIRST - AI ONLY AFTER EDGE - ATOMIC FREEZE ENFORCED [PIPELINE ENFORCEMENT]")
            logger.info(f"[PIPELINE START] timestamp={utc_iso()} event_trigger={event_obj.get('event_trigger', False)} edge={edge_score:.1f} [PIPELINE ENFORCEMENT]")

            with state_lock:
                state["debug_state"]["last_pipeline_stage"] = "START"
                state["debug_state"]["last_signal_attempt"] = {"time": utc_iso(), "status": "STARTED"}
                state["debug_state"]["last_check_time"] = utc_iso()

            signal["context"] = {
                "price": nz(state.get("price")),
                "ema9": nz(state.get("ema_status", {}).get("ema9")),
                "ema21": nz(state.get("ema_status", {}).get("ema21")),
                "ema200": nz(state.get("ema_status", {}).get("ema200")),
                "ema_slope": (nz(state.get("ema_status", {}).get("ema9")) - nz(state.get("ema_status", {}).get("ema21"))) / nz(state.get("ema_status", {}).get("ema21")) if nz(state.get("ema_status", {}).get("ema21")) != 0 else 0.0,
                "sr_state": state.get("support_resistance", {}).get("sr_state", "UNKNOWN"),
                "sr_bias": state.get("support_resistance", {}).get("sr_bias", "UNKNOWN"),
                "dist_to_resistance": nz(state.get("support_resistance", {}).get("dist_to_resistance")),
                "dist_to_support": nz(state.get("support_resistance", {}).get("dist_to_support")),
                "regime": state.get("regime", "UNKNOWN"),
                "market_context": copy.deepcopy(ctx.get("market_context", {})),
            }
            signal["decision"] = {
                "edge_score": event_obj.get("edge_score", 0.0),
                "trigger": event_obj.get("event_trigger", False),
                "ai_called": True,
                "ai_decision": ai.get("decision"),
                "ai_win_prob": ai.get("win_prob"),
                "ai_reason": ai.get("comment", "")
            }

            if not is_edge_valid(edge_score):
                logger.info(f"[EDGE GATE] edge_score={edge_score:.1f} < threshold — NO AI CALL [PIPELINE ENFORCEMENT]")
                enforce_log(signal, "BLOCKED", "EDGE_BELOW_THRESHOLD")
                log_event_rejection({"edge": edge_score, "threshold": get_edge_threshold()})
                log_no_signal_with_context(reason="EDGE_BELOW_THRESHOLD")
                full_pipeline_trace("BLOCKED", "EDGE_BELOW_THRESHOLD", trade_id)
                with state_lock:
                    state["debug_state"]["skip_reason"] = f"EDGE_BELOW_{get_edge_threshold()}"
                    state["debug_state"]["last_pipeline_stage"] = "EDGE_ONLY"
                update_debug_state_always("EDGE_BELOW_THRESHOLD", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            logger.info("[PIPELINE] → EDGE PASSED — AI COOLDOWN ALREADY PASSED [PIPELINE ENFORCEMENT]")
            signal["decision"]["ai_called"] = True
            enforce_log(signal, "AI", extra=f"{ai.get('decision')}")
            full_pipeline_trace("[PIPELINE]", f"AI_{ai.get('decision')}", trade_id)
            with state_lock:
                state["debug_state"]["last_pipeline_stage"] = "AI"
                state["debug_state"]["last_ai_call"] = utc_iso()
                state["debug_state"]["ai_gate"]["called"] = True
                state["debug_state"]["ai_gate"]["reason"] = "AI_CALLED"

            if state["data_quality"] < 0.5:
                logger.warning("[EXECUTION BLOCK] Data quality too low [PIPELINE ENFORCEMENT]")
                enforce_log(signal, "BLOCKED", "LOW_DATA_QUALITY")
                full_pipeline_trace("BLOCKED", "LOW_DATA_QUALITY", trade_id)
                update_debug_state_always("LOW_DATA_QUALITY", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if state["data_quality"] < 0.7:
                logger.warning("[DATA QUALITY] Suboptimal (<0.7) [PIPELINE ENFORCEMENT]")

            if len(volume_buffer) < BUFFER_MIN or len(price_buffer) < BUFFER_MIN or len(delta_buffer) < BUFFER_MIN:
                logger.warning("[WARMUP] Partial mode - buffers not full [PIPELINE ENFORCEMENT]")
                state["system_ready"] = False
            else:
                state["system_ready"] = True

            ctx["trade_id"] = trade_id
            signal["event"] = event_obj
            signal["signal_price"] = state.get("price")
            ai_direction_raw = ai.get("direction")
            final_direction = ai_direction_raw
            inverted = False
            if state.get("invert_signal", False):
                if ai_direction_raw == "LONG":
                    final_direction = "SHORT"
                elif ai_direction_raw == "SHORT":
                    final_direction = "LONG"
                inverted = True
                logger.info(f"[DIRECTION CONSISTENCY] INVERSION APPLIED immediately after AI - raw_ai={ai_direction_raw} → final_direction={final_direction} inverted={inverted} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
            signal["ai_direction_raw"] = ai_direction_raw
            signal["final_direction"] = final_direction
            signal["direction"] = final_direction
            signal["inverted"] = inverted
            signal["ai_decision"] = ai.get("decision")
            signal["ai_win_prob"] = ai.get("win_prob")
            signal["bull_score_at_entry"] = ai.get("bull_score", 0)
            signal["bear_score_at_entry"] = ai.get("bear_score", 0)
            signal["ai_factors"] = copy.deepcopy(ai.get("factors", {}))
            signal["regime"] = state.get("regime")
            signal["strategy"] = state.get("strategy")
            signal["_logged"] = False
            signal["_finalized"] = False
            signal["pullback_pct"] = state.get("pullback_threshold", 0.001)

            if not signal.get("final_direction"):
                raise RuntimeError("PIPELINE BREAK: AI returned no direction")

            sr_state = signal.get("context", {}).get("sr_state") or state.get("support_resistance", {}).get("sr_state", "UNKNOWN")
            mc = signal.get("context", {}).get("market_context") or state.get("market_context", {})
            sr_blocked, sr_block_reason = evaluate_sr_direction_filter(final_direction, sr_state, mc)
            if sr_blocked:
                logger.info(f"[SR FILTER] {sr_block_reason} mode={SR_FILTER_MODE} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                log_blocked_signal(signal, ai, sr_block_reason)
                exit_pipeline(signal, ai, sr_block_reason)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = sr_block_reason
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = sr_block_reason
                update_debug_state_always(sr_block_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return
            elif sr_block_reason and str(sr_block_reason).startswith("SR_SOFT_ALLOW"):
                logger.info(f"[SR SOFT] Allowed {final_direction} at {sr_state}: {sr_block_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")

            loc_blocked, loc_reason = evaluate_entry_location_filter(
                final_direction, signal.get("context", {}) or ctx, ai
            )
            if loc_blocked:
                logger.info(f"[ENTRY FILTER] {loc_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                log_blocked_signal(signal, ai, loc_reason)
                exit_pipeline(signal, ai, loc_reason)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = loc_reason
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = loc_reason
                update_debug_state_always(loc_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            qual_blocked, qual_reason = evaluate_entry_quality_filter(
                final_direction, signal.get("context", {}) or ctx, ai
            )
            if qual_blocked:
                logger.info(f"[ENTRY QUALITY] {qual_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                log_blocked_signal(signal, ai, qual_reason)
                exit_pipeline(signal, ai, qual_reason)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = qual_reason
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = qual_reason
                update_debug_state_always(qual_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if not ensure_directional_capacity(final_direction):
                block_reason = f"MAX_{final_direction}S"
                logger.info(f"[DIRECTION CAP] {block_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                log_blocked_signal(signal, ai, block_reason)
                exit_pipeline(signal, ai, block_reason)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = block_reason
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = block_reason
                update_debug_state_always(block_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            direction = final_direction
            price = state.get("price")
            signal_key = f"{direction}_{round(price, 1)}" if price else "UNKNOWN"
            if signal_key == state.get("last_signal_key"):
                logger.info("[DUPLICATE] Signal blocked by key match [PIPELINE ENFORCEMENT]")
                enforce_log(signal, "BLOCKED", "DUPLICATE_KEY")
                full_pipeline_trace("BLOCKED", "DUPLICATE_KEY_BLOCK", trade_id)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "DUPLICATE_KEY"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "DUPLICATE_KEY"
                update_debug_state_always("DUPLICATE_KEY", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return
            state["last_signal_key"] = signal_key

            margin_usdt, margin_reason = resolve_entry_margin_usdt(
                final_direction, ai, signal.get("context", {}) or ctx
            )
            if margin_reason:
                logger.info(f"[RISK SIZE] {margin_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                log_blocked_signal(signal, ai, margin_reason)
                exit_pipeline(signal, ai, margin_reason)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = margin_reason
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = margin_reason
                update_debug_state_always(margin_reason, {"edge": edge_score, "spread": compute_directional_spread(final_direction, ai)})
                state["last_pipeline_stage"] = "IDLE"
                return
            signal["margin_usdt"] = margin_usdt
            signal["conviction_spread"] = compute_directional_spread(final_direction, ai)
            prof = get_regime_risk_profile()
            logger.info(
                f"[RISK SIZE] margin=${margin_usdt} spread={signal['conviction_spread']} "
                f"regime={prof.get('label')} trade_id={trade_id} [PIPELINE ENFORCEMENT]"
            )

            if ai.get("win_prob", 0) < get_ai_threshold():
                exit_pipeline(signal, ai, "BELOW_THRESHOLD")
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "BELOW_THRESHOLD"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "BELOW_THRESHOLD"
                update_debug_state_always("BELOW_THRESHOLD", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if is_clustered_entry(final_direction, price):
                exit_pipeline(signal, ai, "CLUSTER_ENTRY")
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "CLUSTER_ENTRY"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "CLUSTER_ENTRY"
                update_debug_state_always("CLUSTER_ENTRY", {"edge": edge_score, "price": price})
                state["last_pipeline_stage"] = "IDLE"
                return

            if not ensure_signal_capacity():
                logger.warning("[MAX ACTIVE SIGNALS] Hard block before finalize [PIPELINE ENFORCEMENT]")
                exit_pipeline(signal, ai, "MAX_ACTIVE_SIGNALS")
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "MAX_ACTIVE_SIGNALS"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "MAX_ACTIVE_SIGNALS"
                update_debug_state_always("MAX_ACTIVE_SIGNALS", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            price = ctx.get("price")
            if price is None or price <= 0:
                exit_pipeline(signal, ai, "NO_PRICE")
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "NO_PRICE"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "NO_PRICE"
                update_debug_state_always("NO_PRICE", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if abs(price - last_price_for_debounce) < PRICE_CHANGE_THRESHOLD:
                logger.info("[DEBOUNCE] continuing [PIPELINE ENFORCEMENT]")
            last_price_for_debounce = price

            logger.info("[PIPELINE] → AI APPROVED → EXECUTION STAGE REACHED [PIPELINE ENFORCEMENT]")
            if not execution_allowed():
                exit_pipeline(signal, ai, state.get("execution_reason"))
                with state_lock:
                    state["debug_state"]["last_block_reason"] = state.get("execution_reason")
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = state.get("execution_reason")
                update_debug_state_always("EXECUTION_BLOCK", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return
            full_pipeline_trace("[PIPELINE]", "EXECUTION_ALLOWED", trade_id)
            with state_lock:
                state["debug_state"]["last_pipeline_stage"] = "EXECUTION_ALLOWED"
            logger.info("[PIPELINE] → EXECUTION STAGE → ORDER PLACEMENT [PIPELINE ENFORCEMENT]")
            success = execute_simulated_order(signal)
            if not success:
                exit_pipeline(signal, ai, "ORDER_FAILED")
                state["last_pipeline_stage"] = "IDLE"
                return
            final_status = signal.get("status") if signal.get("status") in ("FILLED", "OPEN") else "ORDERED"
            finalize_signal(signal, ai, final_status)
            logger.info("[PIPELINE] → ORDER STAGE COMPLETE [PIPELINE ENFORCEMENT]")
            state["last_pipeline_stage"] = "IDLE"
            return

        except Exception as e:
            logger.error(f"[PIPELINE FATAL] {e} [PIPELINE ENFORCEMENT]")
            full_pipeline_trace("[PIPELINE]", f"CRASH_{str(e)}", trade_id if 'trade_id' in locals() else None)
            if 'signal' in locals():
                exit_pipeline(signal, reason=f"PIPELINE_ERROR: {e}")
            else:
                logger.error("[PIPELINE] No signal object for crash recovery [PIPELINE ENFORCEMENT]")
            update_debug_state_always("PIPELINE_ERROR", {"error": str(e)})
            state["last_pipeline_stage"] = "IDLE"

def parse_ts(ts_str):
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except:
        return 0

def safe_ws_handler(message):
    global _last_ws_trade_fp, _last_ws_trade_fp_ts
    try:
        logger.debug(f"[WS RAW] Received message length: {len(message)}")
        data = json.loads(message)
        global last_ws_message_time
        last_ws_message_time = time.time()
        if 'topic' in data and data['topic'] == 'publicTrade.BTCUSDT':
            trade_data = data.get('data')
            if not isinstance(trade_data, list) or not trade_data:
                logger.warning("[WS] Invalid data format - skipping")
                return
            _agent_dbg("H2", "safe_ws_handler", "batch", {"trades_in_msg": len(trade_data), "msg_len": len(message)})
            for trade in trade_data:
                trade_fp = (trade.get("i"), trade.get("T"), trade.get("p"), trade.get("v"), trade.get("S"))
                if trade_fp == _last_ws_trade_fp and (time.time() - _last_ws_trade_fp_ts) < 0.05:
                    _agent_dbg("H2", "safe_ws_handler", "dedupe_skip", {"fp": str(trade_fp)[:80]})
                    continue
                _last_ws_trade_fp = trade_fp
                _last_ws_trade_fp_ts = time.time()
                price = float(trade.get('p', 0))
                size = float(trade.get('v', 0))
                side = trade.get('S', 'Buy')
                if price <= 0:
                    continue
                global prev_price, prev_delta, avg_volume, recent_high, recent_low, rejection_strength
                prev_price = nz(state.get("price"))
                state["price"] = price
                state["price_ts"] = time.time()
                state["ws_last_tick"] = time.time()
                state["last_data_ts"] = time.time()

                price_buffer.append(price)
                volume = size
                volume_buffer.append(volume)

                update_orderflow(trade)

                delta_buffer.append(orderflow["delta"])
                delta_change = orderflow["delta"] - orderflow.get("prev_delta", 0)
                delta_change_buffer.append(delta_change)

                imbalance = orderflow["imbalance"]
                imbalance_buffer.append(imbalance)

                if len(price_buffer) >= 2:
                    velocity = (price_buffer[-1] - price_buffer[-2]) / price_buffer[-2]
                    velocity_buffer.append(velocity)

                update_feature_snapshot()
                try:
                    data_quality = update_data_quality(state["feature_snapshot"])
                except Exception as e:
                    logger.error(f"[DATA QUALITY FAILSAFE] {e}")
                    data_quality = 0.0
                with state_lock:
                    state["data_quality"] = data_quality
                if state.get("debug_enabled"):
                    logger.debug(f"[FEATURES] {state['feature_snapshot']}")
                    logger.debug(f"[DATA QUALITY] {data_quality:.3f}")

                trade_ts_raw = trade.get('T')
                trade_ts = trade_ts_raw / 1000 if trade_ts_raw else time.time()
                latency = max(0, (time.time() - trade_ts) * 1000)
                with state_lock:
                    state["diag"].update({"ws_latency_ms": round(latency,1)})
                if not state.get("ws_ready"):
                    state["ws_ready"] = True
                    logger.info(f"[WS] FIRST TICK RECEIVED - Price: {price} | ws_ready=True [PIPELINE ENFORCEMENT]")
        else:
            logger.debug(f"[WS] Non-trade message received: {data.get('topic','unknown')}")
    except IndexError as ie:
        logger.error(f"[WS FIX] deque underflow prevented: {ie}")
        return
    except Exception as e:
        logger.critical(f"[WS FATAL] {e}")
        set_execution_paused("THREAD_CRASH")

def on_message(ws, message):
    safe_ws_handler(message)

def on_open(ws):
    logger.info("WS: Connected and subscribed to BTCUSDT trades")
    ws.send(json.dumps({"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}))
    threading.Thread(target=ping_ws, args=(ws,), daemon=True).start()

def on_error(ws, error):
    logger.error(f"WS error: {error}")

def on_close(ws, code, reason):
    logger.warning(f"WS closed: code={code}, reason={reason}")
    ws.sock = None
    global ws_alive
    ws_alive = False
    set_execution_paused("STALE_DATA_HARD_STOP")

def start_websocket():
    global ws_app, ws_alive
    ws_url = "wss://stream.bybit.com/v5/public/linear"
    while not shutdown_event.is_set():
        with ws_lock:
            if ws_app:
                try:
                    ws_app.keep_running = False
                    if ws_app.sock:
                        ws_app.sock.close()
                    ws_app.close()
                except:
                    pass
        try:
            logger.info("Starting websocket connection")
            ws_app = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open, on_error=on_error, on_close=on_close)
            ws_alive = True
            ws_app.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}, ping_interval=20, ping_timeout=10)
        except Exception as e:
            logger.error(f"WS failure: {e}")
        ws_alive = False
        logger.warning("Websocket restarting in 5s")
        time.sleep(5)

def ping_ws(ws):
    global ws_alive
    while ws_alive and not shutdown_event.is_set():
        sock = getattr(ws, "sock", None)
        if not sock or not sock.connected:
            break
        time.sleep(15)
        try:
            ws.send(json.dumps({"op": "ping"}))
        except Exception:
            break

def ws_watchdog():
    global ws_app, ws_reconnecting, last_ws_reconnect, ws_alive, ws_stale_count, ws_retry
    try:
        while not shutdown_event.is_set():
            time.sleep(3)
            price_ts = state.get("price_ts")
            if price_ts is None:
                logger.info("[WS WATCHDOG] Startup phase - no price yet, skipping stale check")
                continue
            age = time.time() - price_ts
            if age > 10:
                if not ws_reconnecting or (time.time() - last_ws_reconnect > 5):
                    ws_reconnecting = True
                    last_ws_reconnect = time.time()
                    reconnect_delay = min(ws_retry * 2, 30)
                    logger.warning(f"[WS] STALE DETECTED age={fmt(age)}s - backoff {reconnect_delay}s")
                    with ws_lock:
                        if ws_app:
                            try:
                                ws_app.keep_running = False
                                if ws_app.sock:
                                    ws_app.sock.close()
                                ws_app.close()
                            except:
                                pass
                    time.sleep(reconnect_delay)
                    start_websocket()
                    ws_reconnecting = False
                    ws_retry = 1
            if age > STALE_HARD_SEC and price_ts is not None:
                set_execution_paused("STALE_DATA_HARD_STOP")
            else:
                if state.get("execution_reason") == "STALE_DATA_HARD_STOP":
                    logger.info("[RECOVERY FIX] Clearing stale hard stop")
                    set_execution_paused("")
    except Exception as e:
        logger.exception("[CRITICAL] WS watchdog crash")
        set_execution_paused("THREAD_CRASH")

def state_monitor_loop():
    try:
        last_stale_time = 0
        while not shutdown_event.is_set():
            time.sleep(1)
            with state_lock:
                state["heartbeat"] = int(time.time())
                validate_market_data()
                data_age = time.time() - state.get("last_data_ts", 0)
                if data_age <= 5:
                    if state.get("execution_reason") == "STALE_DATA_HARD_STOP":
                        logger.info("[RECOVERY] DATA FLOW RESTORED - ENGINE RESUMED")
                        with state_lock:
                            state["execution_paused"] = False
                            state["execution_reason"] = ""
                if state["ws_ready"] and state["ohlcv_ready"]:
                    if state.get("last_ready_ts", 0) == 0:
                        state["last_ready_ts"] = time.time()
                    elif time.time() - state["last_ready_ts"] >= READY_STABLE_SEC:
                        if not state.get("system_ready"):
                            logger.info(f"[SYSTEM READY] STABLE for {READY_STABLE_SEC}s -> system_ready=True")
                        state["system_ready"] = True
                        if state.get("execution_reason") == "THREAD_CRASH":
                            set_execution_paused("")
                            logger.info("[RECOVERY][STATE_SYNC] Execution resumed after WS/OHLCV recovery")
                elif state["ws_ready"]:
                    state["data_source"] = "ws_ready_waiting_ohlcv"
                elif state["ohlcv_ready"]:
                    state["data_source"] = "ohlcv_ready_waiting_ws"
                else:
                    state["data_source"] = "booting"
                ws_age = time.time() - (state.get("ws_last_tick") or 0)
                state["diag"]["ws_status"] = "OK" if ws_age < STALE_HARD_SEC else "STALE"
                engine_age = time.time() - last_ohlcv_fetch
                state["diag"]["engine_status"] = "OK" if engine_age < 300 else "STALE"
                state["diag"]["ai_status"] = "OK" if state["ai_enabled"] else "OFF"
                if ws_age > STALE_HARD_SEC:
                    state["ws_stale_count"] = state.get("ws_stale_count", 0) + 1
                else:
                    state["ws_stale_count"] = 0
                if state["ws_stale_count"] > 3:
                    logger.error("[PIPELINE] WS DEAD -> THREAD_CRASH")
                    set_execution_paused("THREAD_CRASH")
                    return
            fetch_ohlcv()
            update_ema()
            trend_info()
            update_support_resistance()
            update_market_context()
            price_ts = state.get("price_ts")
            if price_ts is None:
                continue
            stale_age = time.time() - price_ts
            if stale_age > 300:
                logger.warning(f"WS stale for {stale_age:.0f}s")
                if time.time() - last_stale_time > 120:
                    last_stale_time = time.time()
            else:
                with state_lock:
                    if state["execution_paused"] and state["execution_reason"] in ["WS_STALE", "PRICE_STALE_OR_MISSING"]:
                        set_execution_paused("")
                        logger.info("[RECOVERY][STATE_SYNC] WS no longer stale — execution resumed")
            expired_ids = []
            with replay_lock:
                for tid, buf in list(replay_buffers.items()):
                    if buf.get("closed"):
                        dump_replay(tid)
                        replay_buffers.pop(tid, None)
                        continue
                    if time.time() - buf.get("last_update", buf.get("start_ts", 0)) > REPLAY_TTL_SEC or len(buf.get("ticks", [])) >= 2000:
                        expired_ids.append(tid)
                if len(replay_buffers) > MAX_REPLAY_BUFFERS:
                    sorted_ids = sorted(replay_buffers, key=lambda k: replay_buffers[k].get("start_ts", 0))
                    excess = len(replay_buffers) - MAX_REPLAY_BUFFERS
                    for tid in sorted_ids[:excess]:
                        dump_replay(tid)
                        replay_buffers.pop(tid, None)
            for tid in expired_ids:
                close_replay_buffer(tid)
            with replay_lock:
                oldest_id = None
                if len(replay_buffers) > MAX_REPLAY_BUFFERS:
                    oldest_id = min(replay_buffers, key=lambda k: replay_buffers[k].get("start_ts", 0))
                    dump_replay(oldest_id)
                    replay_buffers.pop(oldest_id, None)
            if oldest_id:
                dump_replay(oldest_id)
            pipeline_state_sync()
            process_pending_orders()
            process_positions()
            pipeline_heartbeat()
            if time.time() - last_pipeline_run >= MIN_PIPELINE_INTERVAL:
                logger.info("[PERIODIC PIPELINE] forcing detect_event_light for analyzer data [PIPELINE ENFORCEMENT]")
                event = detect_event_light()
                if event and event.get("event_trigger"):
                    process_signal(event)
    except Exception as e:
        logger.exception("[CRITICAL] State monitor loop crash")
        set_execution_paused("THREAD_CRASH")

def build_signal(signal: dict, context: dict, ai: dict) -> dict:
    signal.update(context)
    signal["final_direction"] = ai.get("direction")
    signal["direction"] = ai.get("direction")
    signal["status"] = "CREATED"
    signal["ai"] = ai
    signal["ai_win_prob"] = ai.get("win_prob")
    signal["ai_decision"] = ai.get("decision")
    signal["ai_source"] = ai.get("source")
    if "features" not in signal:
        signal["features"] = {}
    if "context" not in signal:
        signal["context"] = {}
    if "controls" not in signal:
        signal["controls"] = {}
    if "decision" not in signal:
        signal["decision"] = {}
    return signal

def execute_order(signal, ai=None):
    if not state.get("live_armed", False) and state.get("strategy_mode") != "RESEARCH":
        logger.warning("[LIVE ARM BLOCK] execute_order skipped — live_armed=False")
        full_pipeline_trace("[EXECUTION]", "LIVE_ARM_BLOCKED", signal.get("trade_id"))
        return False
    try:
        logger.info(f"[EXECUTION] Routing order trade_id={signal.get('trade_id')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
        full_pipeline_trace("[EXECUTION]", "ROUTING_START", signal.get("trade_id"))
        track_event(signal.get("trade_id"), "EXECUTION_ROUTED")
        use_limit = state.get("allow_compression", True)
        if use_limit:
            order = create_limit_order(signal)
            if order is None:
                logger.error("[EXECUTION] create_limit_order failed")
                exit_pipeline(signal, ai, "ORDER_CREATION_FAILED")
                return False
        else:
            execute_market_order(signal)
        created = any(o.get("trade_id") == signal.get("trade_id") for o in pending_orders)
        if not created:
            logger.error(f"[EXECUTION FAIL HARD] Order not in pending_orders trade_id={signal.get('trade_id')}")
            exit_pipeline(signal, ai, "ORDER_NOT_RECORDED")
            return False
        assert any(o.get("trade_id") == signal.get("trade_id") for o in pending_orders), "[CRITICAL] Order not recorded in pending_orders"
        full_pipeline_trace("[ORDER]", "PLACED", signal.get("trade_id"))
        return True
    except Exception as e:
        logger.error(f"[EXECUTION ERROR] {e}")
        exit_pipeline(signal, ai, "EXECUTION_EXCEPTION")
        return False

def execute_market_order(signal):
    logger.info(f"[ORDER] MARKET EXEC trade_id={signal.get('trade_id')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    full_pipeline_trace("[ORDER]", "MARKET_EXEC_START", signal.get("trade_id"))
    price = state.get("price")
    if state.get("account_balance", 0) < 10 or price is None or price <= 0:
        signal["status"] = "BLOCKED"
        enforce_log(signal, "BLOCKED_INSUFFICIENT_CAPITAL")
        exit_pipeline(signal, None, "BLOCKED_INSUFFICIENT_CAPITAL")
        return
    qty = calc_position_qty(price, state.get("leverage", 20), signal.get("margin_usdt"))
    assert qty > 0, "INVALID QTY"
    order = {
        "trade_id": signal["trade_id"],
        "signal_dir": signal["final_direction"],
        "limit_price": price,
        "qty": qty,
        "entry_type": "MARKET",
        "signal_price": signal.get("signal_price"),
        "created_ts": signal.get("order_created_ts") or time.time(),
        "fee_type": "TAKER",
    }
    with trade_lock:
        open_positions.append(_build_open_position(order, signal, signal.get("ai", {})))
    master = trades_map.get(signal["trade_id"], {}).get("signal_ref")
    if master:
        master.update({"status": "FILLED","filled_ts": time.time(),"fill_price": price,"outcome": "OPEN"})
    persist_signal(master or signal, "FILLED")
    logger.info(f"[ORDER] POSITION OPENED {signal['final_direction']} qty={qty} [PIPELINE ENFORCEMENT]")
    pipeline_state_sync()

def create_limit_order(signal):
    logger.info(f"[ORDER] LIMIT EXEC trade_id={signal.get('trade_id')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    full_pipeline_trace("[ORDER]", "LIMIT_EXEC_START", signal.get("trade_id"))
    price = state.get("price")
    if state.get("account_balance", 0) < 10 or price is None or price <= 0:
        signal["status"] = "BLOCKED"
        enforce_log(signal, "BLOCKED_INSUFFICIENT_CAPITAL")
        exit_pipeline(signal, None, "BLOCKED_INSUFFICIENT_CAPITAL")
        return None
    pullback_pct = signal.get("pullback_pct", 0.001)
    signal_price = signal.get("signal_price", price)
    if signal["final_direction"] == "LONG":
        limit_price = signal_price * (1 - pullback_pct)
    else:
        limit_price = signal_price * (1 + pullback_pct)
    if limit_price <= 0:
        logger.error(f"[ORDER BLOCK] Invalid limit price {limit_price}")
        return None
    qty = calc_position_qty(price, state.get("leverage", 20))
    assert qty > 0, "INVALID QTY"
    assert limit_price > 0, "INVALID LIMIT PRICE"
    order = {
        "trade_id": signal["trade_id"],
        "side": map_signal_to_exchange_side(signal["final_direction"]),
        "signal_dir": signal["final_direction"],
        "limit_price": limit_price,
        "qty": qty,
        "status": "PENDING",
        "created_ts": time.time(),
        "entry_type": "LIMIT",
        "signal_price": signal.get("signal_price"),
        "signal_ts": signal.get("created_ts"),
        "fee_type": "MAKER"
    }
    signal["limit_price"] = limit_price
    signal["order_created_ts"] = time.time()
    signal["status"] = "ORDERED"
    signal["order_placed"] = True
    with trade_lock:
        pending_orders.append(order)
    logger.info(f"[ORDER CREATED] trade_id={signal.get('trade_id')} signal_price={fmt(signal_price)} limit_price={fmt(limit_price)} pullback={pullback_pct*100}% final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    pipeline_state_sync()
    assert any(o.get("trade_id") == signal.get("trade_id") for o in pending_orders), "Order append failed"
    return order

def cleanup_expired_orders():
    now = time.time()
    expired_n = 0
    with trade_lock:
        for order in list(pending_orders):
            created = order.get("created_ts")
            if not created or created <= 0:
                _agent_dbg("H2", "cleanup_expired_orders", "bad_created_ts", {"trade_id": order.get("trade_id"), "created_ts": created})
                continue
            age = now - created
            if age > LIMIT_ORDER_MAX_AGE_SEC:
                if order in pending_orders:
                    pending_orders.remove(order)
                expired_orders.append({
                    "trade_id": order.get("trade_id"),
                    "dir": order.get("side"),
                    "limit_price": order.get("limit_price"),
                    "signal_price": order.get("signal_price"),
                    "created_ts": order.get("created_ts"),
                    "expired_ts": now,
                    "age_min": int(age / 60),
                    "conf": order.get("ai_win_prob", 0),
                    "mode": state.get("strategy_mode"),
                    "reason": "TTL_EXPIRED"
                })
                if len(expired_orders) > MAX_EXPIRED_ORDERS:
                    expired_orders.pop(0)
                sig_ok = expire_signal_for_order(order, "TTL_EXPIRED")
                expired_n += 1
                _agent_dbg("H2", "cleanup_expired_orders", "expired", {"trade_id": order.get("trade_id"), "age_sec": int(age), "signal_expired": sig_ok, "pending_left": len(pending_orders)})
                logger.info(f"[ORDER][{order['trade_id']}] EXPIRED [PIPELINE ENFORCEMENT]")
    if expired_n:
        pipeline_state_sync()

def update_orders_state():
    logger.debug("[ENGINE] update_orders_state called")
    cleanup_expired_orders()

def manage_open_positions():
    logger.debug("[ENGINE] manage_open_positions called")
    monitor_positions()

def position_manager():
    try:
        while not shutdown_event.is_set():
            if is_engine_halted():
                time.sleep(5)
                continue
            price = state.get("price")
            if not price or price <= 0:
                time.sleep(1)
                continue
            now = time.time()
            prune_signals()
            update_signal_pull_metrics(price)
            cleanup_expired_orders()
            process_pending_orders()
            process_positions()

            with trade_lock:
                for order in list(pending_orders):
                    if not isinstance(order, dict):
                        continue
                    age = now - order.get("created_ts", now)
                    if age > LIMIT_ORDER_MAX_AGE_SEC:
                        logger.warning(f"[TTL] ORDER EXPIRED {order.get('trade_id')} age={age:.1f}s [PIPELINE ENFORCEMENT]")
                        if order in pending_orders:
                            pending_orders.remove(order)
                        expired = {"trade_id": order.get("trade_id"),"dir": order.get("side"),"limit_price": order.get("limit_price"),"signal_price": order.get("signal_price"),"created_ts": order.get("created_ts"),"expired_ts": now,"age_min": int(age / 60),"conf": order.get("ai_win_prob", 0),"mode": state.get("strategy_mode"),"reason": "TTL_EXPIRED"}
                        expired_orders.append(expired)
                        expire_signal_for_order(order, "TTL_EXPIRED")
                        if len(expired_orders) > MAX_EXPIRED_ORDERS:
                            expired_orders.pop(0)
                        logger.info(f"[ORDER][{order['trade_id']}] EXPIRED [PIPELINE ENFORCEMENT]")

            pipeline_state_sync()
            print_console_dashboard()
            time.sleep(1)
    except Exception as e:
        logger.exception(f"Position manager crash: {e}")
        set_execution_paused("THREAD_CRASH")

def close_position(pos: dict, exit_reason: str):
    if not validate_state():
        return
    with state_lock:
        if pos not in open_positions:
            return
        trade_id = pos.get("trade_id")
        if not trade_id:
            return
        price = state.get("price", pos.get("entry", 0))
        entry = pos.get("entry", 0)
        qty = pos.get("qty", 0)
        assert entry > 0, f"[EXIT VALIDATION FAIL] entry={entry} <=0"
        assert price > 0, f"[EXIT VALIDATION FAIL] exit_price={price} <=0"
        assert pos.get("sl", 0) > 0, f"[EXIT VALIDATION FAIL] sl={pos.get('sl')} <=0"
        dir_factor = 1 if pos.get("dir") == "LONG" else -1
        price_move = ((price - entry) / entry) * dir_factor if entry > 0 else 0
        margin_usdt = float(pos.get("margin_usdt") or FIXED_MARGIN_USDT)
        gross_pnl = price_move * pos.get("leverage", 20) * margin_usdt

        position_value_entry = entry * qty
        position_value_exit = price * qty
        entry_is_maker = pos.get("entry_fee_type") == "MAKER"
        exit_is_maker = pos.get("exit_fee_type") == "MAKER" or "TP" in exit_reason or "POSTONLY" in exit_reason
        maker_fee, taker_fee = get_trading_fee_rates()
        entry_fee = position_value_entry * (maker_fee if entry_is_maker else taker_fee)
        exit_fee = position_value_exit * (maker_fee if exit_is_maker else taker_fee)
        total_fees = entry_fee + exit_fee
        accrue_position_funding(pos, time.time())
        funding_total = pos.get("funding_fees", 0.0)
        net_pnl = gross_pnl - total_fees - funding_total

        if net_pnl <= 0 and ("TP" in exit_reason or "PROFIT" in exit_reason):
            logger.warning(f"[FEE FILTER] Skipping unprofitable exit trade_id={trade_id} net={fmt(net_pnl)}")
            return

        r_multiple = compute_r(entry, pos.get("sl", 0), price)
        ai_prob = pos.get("ai_win_prob", 0) or 0
        ai_band = "0-50" if ai_prob < 50 else "50-60" if ai_prob < 60 else "60-70" if ai_prob < 70 else "70+"
        band_key = f"{ai_prob//5*5}-{ai_prob//5*5+5}"
        master = trades_map.get(trade_id, {}).get("signal_ref", {})
        features, context = _feature_bundle(pos, master)
        entry_delay = _compute_entry_delay_sec(pos, master)
        slippage = pos.get("entry_slippage")
        if slippage is None:
            slippage = abs(float(entry) - float(pos.get("signal_price", entry)))
        momentum_val = _compute_momentum_metric(features)
        volatility_val = _compute_volatility_metric(features)
        entry_sr_state = pos.get("entry_sr_state") or context.get("sr_state", "UNKNOWN")
        dist_res = pos.get("entry_dist_to_resistance", context.get("dist_to_resistance", 0.0))
        dist_sup = pos.get("entry_dist_to_support", context.get("dist_to_support", 0.0))
        edge_at_entry = pos.get("edge_score_at_entry") or master.get("edge_score_at_entry", 0.0)
        mc = pos.get("context", {}).get("market_context", {}) or master.get("market_structure", {}) or master.get("ai_input", {}).get("market_context", {})
        if not isinstance(mc, dict):
            mc = {}
        ms = mc.get("market_structure", {}) if isinstance(mc.get("market_structure"), dict) else {}
        mtf = mc.get("multi_tf", {}) if isinstance(mc.get("multi_tf"), dict) else {}
        ts_entry = mc.get("trend_strength", {}) if isinstance(mc.get("trend_strength"), dict) else {}
        fund = mc.get("funding", {}) if isinstance(mc.get("funding"), dict) else state.get("funding", {})
        ai_factors = master.get("ai_factors", {}) or pos.get("ai_factors", {})
        if not isinstance(ai_factors, dict):
            ai_factors = {}
        trade_row = {
            "ts": utc_iso(),
            "trade_id": trade_id,
            "dir": pos.get("dir"),
            "entry": entry,
            "exit": price,
            "dur_min": (time.time() - pos.get("entry_ts", 0)) / 60,
            "pnl": round(net_pnl / margin_usdt * 100, 2),
            "margin_usdt": margin_usdt,
            "conviction_spread": pos.get("conviction_spread"),
            "net_pnl_usd": round(net_pnl, 2),
            "gross_pnl_usd": round(gross_pnl, 2),
            "fees_usd": round(total_fees, 2),
            "exit_reason": exit_reason,
            "leverage": pos.get("leverage", 20),
            "r_multiple": round(r_multiple, 2),
            "ai_win_prob": pos.get("ai_win_prob") or master.get("ai_win_prob"),
            "ai_threshold": get_ai_threshold(),
            "ai_approved": pos.get("ai_approved", master.get("ai_decision") == "APPROVE"),
            "entry_type": pos.get("entry_type", "UNKNOWN"),
            "tp_stage": pos.get("tp_stage", 0),
            "regime": pos.get("signal_regime", state.get("regime", "UNKNOWN")),
            "strategy": pos.get("strategy_birth", "SR"),
            "ai_band": ai_band,
            "ai_source": pos.get("ai_source", state.get("last_ai", {}).get("source", "UNKNOWN")),
            "structure": ms.get("structure_score", 0.0),
            "structure_score_at_entry": ms.get("structure_score"),
            "structure_bias_at_entry": ms.get("structure_bias"),
            "mtf_agreement_at_entry": mtf.get("agreement"),
            "adx_at_entry": ts_entry.get("adx"),
            "trend_score_at_entry": ts_entry.get("trend_score"),
            "bull_score_at_entry": pos.get("bull_score_at_entry") or ai_factors.get("bull_score") or master.get("bull_score_at_entry"),
            "bear_score_at_entry": pos.get("bear_score_at_entry") or ai_factors.get("bear_score") or master.get("bear_score_at_entry"),
            "funding_rate_pct_8h_at_entry": fund.get("rate_pct_per_8h"),
            "bot_version": EXECUTION_FIX_VERSION,
            "ai_decision": master.get("ai_decision", state.get("ai_decision", "UNKNOWN")),
            "price_at_signal": pos.get("signal_price"),
            "distance_to_resistance": dist_res,
            "distance_to_support": dist_sup,
            "sr_state": entry_sr_state,
            "ema_spread": state.get("ema_status", {}).get("ema_spread", 0.0),
            "momentum": momentum_val,
            "volatility": volatility_val,
            "duration_min": (time.time() - pos.get("entry_ts", 0)) / 60,
            "max_drawdown": pos.get("max_drawdown", 0.0),
            "max_profit": pos.get("max_pnl_pct", 0.0),
            "entry_delay": entry_delay,
            "slippage": round(float(slippage), 6),
            "maker_fees": pos.get("maker_fees", 0.0),
            "taker_fees": pos.get("taker_fees", 0.0),
            "funding_fees": pos.get("funding_fees", 0.0),
            "features_velocity": features.get("velocity", 0.0),
            "features_volume": features.get("volume", 0.0),
            "features_volume_ratio": features.get("volume_ratio", 0.0),
            "features_delta": features.get("delta", 0.0),
            "features_delta_change": features.get("delta_change", 0.0),
            "features_imbalance": features.get("imbalance", 0.0),
            "context_price": pos.get("context", {}).get("price", 0.0),
            "context_ema9": pos.get("context", {}).get("ema9", 0.0),
            "context_ema21": pos.get("context", {}).get("ema21", 0.0),
            "context_ema200": pos.get("context", {}).get("ema200", 0.0),
            "context_ema_slope": pos.get("context", {}).get("ema_slope", 0.0),
            "context_sr_state": pos.get("context", {}).get("sr_state", "UNKNOWN"),
            "context_sr_bias": pos.get("context", {}).get("sr_bias", "UNKNOWN"),
            "context_dist_to_resistance": pos.get("context", {}).get("dist_to_resistance", 0.0),
            "context_dist_to_support": pos.get("context", {}).get("dist_to_support", 0.0),
            "context_regime": pos.get("context", {}).get("regime", "UNKNOWN"),
            "controls_edge_threshold": pos.get("controls", {}).get("edge_threshold", 0.0),
            "controls_ai_threshold": pos.get("controls", {}).get("ai_threshold", 0.0),
            "controls_pullback_pct": pos.get("controls", {}).get("pullback_pct", 0.0),
            "controls_leverage": pos.get("controls", {}).get("leverage", 0.0),
            "decision_edge_score": pos.get("decision", {}).get("edge_score", 0.0),
            "decision_trigger": pos.get("decision", {}).get("trigger", False),
            "decision_ai_called": pos.get("decision", {}).get("ai_called", False),
            "decision_ai_decision": pos.get("decision", {}).get("ai_decision", "UNKNOWN"),
            "decision_ai_win_prob": pos.get("decision", {}).get("ai_win_prob", 0.0),
            "decision_ai_reason": pos.get("decision", {}).get("ai_reason", ""),
            "execution_entry_price": entry,
            "execution_exit_price": price,
            "execution_entry_type": "MAKER" if entry_is_maker else "TAKER",
            "execution_exit_type": "MAKER" if exit_is_maker else "TAKER",
            "execution_qty": qty,
            "execution_slippage": round(float(slippage), 6),
            "execution_fill_delay_sec": entry_delay,
            "outcome_net_pnl_usd": round(net_pnl, 2),
            "outcome_gross_pnl_usd": round(gross_pnl, 2),
            "outcome_fees_usd": round(total_fees, 2),
            "outcome_pnl_pct": round(net_pnl / margin_usdt * 100, 2),
            "outcome_duration_sec": time.time() - pos.get("entry_ts", 0),
            "outcome_exit_reason": exit_reason,
            "feature_delta": pos.get("features", {}).get("delta", 0.0),
            "feature_volume_ratio": pos.get("features", {}).get("volume_ratio", 0.0),
            "feature_velocity": pos.get("features", {}).get("velocity", 0.0),
            "feature_imbalance": pos.get("features", {}).get("imbalance", 0.0),
            "early_fail_triggered": (exit_reason == "EARLY_FAIL"),
            "early_fail_enabled": pos.get("controls", {}).get("early_fail_enabled", False),
            "entry_features": master.get("features", {}) if master else pos.get("features", {}),
            "entry_edge": master.get("edge_score_at_entry", 0.0) if master else 0.0,
            "entry_controls": master.get("controls", {}) if master else {},
            "entry_indicators": master.get("indicators", {}) if master else {},
            "setup_type": master.get("setup_type", "UNKNOWN") if master else "UNKNOWN",
            "edge_score": edge_at_entry,
            "edge_score_at_entry": edge_at_entry,
            "invert_signal": state.get("invert_signal", False),
            "early_fail_enabled_global": state.get("early_fail_enabled", True),
            "experiment_tag": f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}",
            "final_direction": pos.get("dir")
        }
        open_positions.remove(pos)
        trades.append(trade_row)
        validate_state()
        try:
            log_trade(trade_row)
            logger.info(f"[CSV] Trade logged reason={exit_reason} trade_id={trade_id} net_pnl={fmt(net_pnl)} gross={fmt(gross_pnl)} maker={fmt(pos.get('maker_fees',0))} taker={fmt(pos.get('taker_fees',0))} funding={fmt(pos.get('funding_fees',0))} final_direction={pos.get('dir')} [PIPELINE ENFORCEMENT]")
            persist_signal_close(trade_id, "CLOSED")
            state["account_balance"] += net_pnl
            recent_trades.append({"pnl": net_pnl,"win": net_pnl > 0,"regime": trade_row.get("regime", "UNKNOWN"),"setup": trade_row.get("strategy", "SR")})
        except Exception as e:
            logger.error(f"[CSV ERROR] {e}")
        apply_trade_pnl(trade_row)

        with state_lock:
            a = state.setdefault("analytics", {})
            bands = a.setdefault("ai_bands", {})
            if band_key not in bands:
                bands[band_key] = {"trades": 0, "wins": 0, "pnl": 0.0}
            bands[band_key]["trades"] += 1
            bands[band_key]["pnl"] += net_pnl
            if net_pnl > 0:
                bands[band_key]["wins"] += 1
            state["analytics"] = a

        master = trades_map.get(trade_id, {}).get("signal_ref")
        if master:
            master.update({"status": "CLOSED","exit_reason": exit_reason,"outcome": "WIN" if net_pnl > 0 else "LOSS","closed_ts": time.time()})
            master["expires_ts"] = time.time() - 1

        persist_signal_close(trade_id, "CLOSED")

        save_positions()
        save_persistent_config()
        if not validate_state():
            logger.error("State corrupted after closing position")
            set_execution_paused("ENGINE_FAILURE")
    logger.info(f"[CLOSE][{trade_id}] reason={exit_reason} net_pnl={fmt(net_pnl)} ai_source={state.get('last_ai',{}).get('source')} final_direction={pos.get('dir')} [PIPELINE ENFORCEMENT]")
    candidate_signal["active"] = False
    clear_pending_trade()
    pipeline_state_sync()

AI_PROMPT_TEMPLATE = """
You are a probabilistic trading decision engine.

Given the following market data:

{context}

Tasks:
1) Direction: LONG / SHORT / NO_TRADE
2) Win probability (0–100)
3) Explicit bull vs bear factor scoring (required)
4) Decision: APPROVE only if your direction matches the stronger factor side

Decision priority (highest weight first):
1) market_context.market_structure — HH/HL/LH/LL swing labels and structure_score (-10 to +10)
2) market_context.multi_tf — 15m/1h/4h trend agreement; note interpretation_note for pullbacks vs reversals
3) market_context.trend_strength — ADX, trend_score, vwap_distance_pct (ADX>=25 = trending; favor continuation not fading)
4) market_context.ema_alignment — price vs EMA distances and slopes (facts only; no rigid EMA-only rules)
5) orderflow — delta, imbalance, volume_ratio, velocity
6) funding — positioning pressure (rate per 8h; longs pay when rate > 0)
7) sr_state / dist_to_resistance / dist_to_support — LOCATION CONTEXT ONLY (never sole reason to fade)

Rules:
- Do NOT assume any predefined strategy or invert signals
- Do NOT infer hidden variables
- If multi_tf.agreement is CONFLICTED, prefer NO_TRADE or lower win probability unless orderflow strongly confirms one side
- If structure shows hh_hl_sequence_active and higher TFs are BULLISH, do NOT SHORT solely because price is near resistance
- If structure shows lh_ll_sequence_active and higher TFs are BEARISH, do NOT LONG solely because price is near support
- edge_score (0-6) is gate strength only, not direction
- Prefer NO_TRADE when data_quality is low or orderflow fields are near zero
- APPROVE LONG only if bull_score >= bear_score + 1; APPROVE SHORT only if bear_score >= bull_score + 1
- List at least 2 reasons_for and 2 reasons_against before deciding

Return EXACTLY (include this JSON block before the Direction line):

```json
{{
  "direction": "LONG or SHORT or NO_TRADE",
  "confidence": 0,
  "reasons_for_trade": ["...", "..."],
  "reasons_against_trade": ["...", "..."],
  "bull_score": 0,
  "bear_score": 0
}}
```

Direction: LONG / SHORT / NO_TRADE
Win probability: 0-100
Bull score: 0-10
Bear score: 0-10
Decision: APPROVE / REJECT
Reason: ...
"""

signal_queue = Queue(maxsize=MAX_EVENT_QUEUE)
event_queue = Queue(maxsize=MAX_EVENT_QUEUE)
latest_candles: List[List] = []
last_candle_ts = 0.0
candle_index = 0
trades: List[Dict] = []
pending_orders: List[Dict] = []
expired_orders: List[Dict] = []
open_positions: List[Dict] = []
trades_map: Dict[str, Dict] = {}
app = Flask("3factor_bot")
state_lock = threading.RLock()
trade_lock = threading.RLock()
csv_lock = threading.Lock()
replay_lock = threading.RLock()
ws_lock = threading.RLock()
console_lock = threading.Lock()
pipeline_lock = threading.Lock()
process_lock = threading.RLock()
last_ohlcv_fetch = time.time() - 60
last_processed_candle_ts = 0.0
last_ai_evaluation_ts = 0.0
last_ai_reject_ts = 0.0
last_logged_candle_ts = 0
rest_failure_count = 0
MAX_REST_FAILURES = 5
POSITIONS_FILE = "open_positions.json"
bybit_public = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'swap', 'unifiedMargin': True}})
try:
    MARKETS = bybit_public.load_markets()
except Exception as e:
    MARKETS = {}
    print(f"[WARN] Bybit load_markets failed at boot (will retry in engine): {e}", flush=True)
api_key = os.getenv("BYBIT_API_KEY", "").strip()
secret = os.getenv("BYBIT_SECRET", "").strip()
bybit_private = ccxt.bybit({
    'apiKey': api_key,
    'secret': secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap', 'unifiedMargin': True, 'recvWindow': 15000}
})
tick_prices = deque(maxlen=300)
price_seq = 0
logger = logging.getLogger("3factor-bot")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s'))
file_handler = logging.FileHandler("bot_runtime.log", encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s'))
logger.handlers.clear()
logger.addHandler(stream_handler)
logger.addHandler(file_handler)
replay_buffers: Dict[str, Dict] = {}
MAX_REPLAY_BUFFERS = 100
SIGNAL_SNAPSHOT_FILE = "signal_snapshot.jsonl"
SIGNAL_REPLAY_FILE = "signal_replay.jsonl"
TRADE_OUTCOME_FILE = "trade_outcome.jsonl"
COUNTERFACTUAL_FILE = "counterfactual.jsonl"
POLICY_FILE = "policy.json"
CONFIG_FILE = "config.json"
write_counter = 0
bot_start_time = 0.0
last_engine_run = 0.0
last_signal_create_global = 0.0
last_setup_key = ""
last_signal_direction = "FLAT"
last_ai_confidence = 0.0
last_signal_time = 0.0
last_console_update = 0.0
last_signal_key = ""
last_ai_signal_key = ""
last_ai_candle = -1
last_ai_logged_key = ""
last_ai_candle_logged = -1
no_signal_count = 0
ws_stale_count = 0
ws_reconnecting = False
last_ws_reconnect = 0.0
ws_app = None
ws_alive = True
last_no_signal_candle = -1
last_block_log = 0.0
first_ai_done = False
last_event_trigger = 0.0
ws_retry = 1
last_ai_call_ts = 0.0
last_signal_process_ts = 0.0
bootstrap_done = False
last_price_for_debounce = 0.0
last_ai_reason = "NONE"
last_trigger_ai_call = 0.0
last_context_hash = None
last_signal_create_ts = 0.0
test_signal_fired = False
prev_price = 0.0
prev_delta = 0.0
avg_volume = 0.0
recent_high = 0.0
recent_low = 0.0
rejection_strength = 0.0
last_ws_message_time = 0.0
last_heartbeat = time.time()

def global_exception_handler(exc_type, exc_value, exc_traceback):
    logger.critical("=== GLOBAL CRASH DETECTED ===")
    logger.critical("Type: %s", exc_type)
    logger.critical("Value: %s", exc_value)
    logger.critical("Traceback:\n%s", "".join(traceback.format_tb(exc_traceback)))
    dump_system_state()

sys.excepthook = global_exception_handler

def safe_thread(fn):
    def wrapper(*args, **kwargs):
        while not shutdown_event.is_set():
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.exception(f"[THREAD CRASH] {fn.__name__}: {e}")
                dump_system_state()
                set_execution_paused("THREAD_CRASH")
                time.sleep(2)
    return wrapper

def dump_system_state():
    try:
        price_ts = state.get("price_ts")
        snapshot = {
            "time": utc_iso(),
            "edge_score": state.get("last_edge", 0.0),
            "edge_threshold": get_edge_threshold(),
            "last_pipeline_stage": state.get("last_pipeline_stage"),
            "active_signals": get_active_signal_count(),
            "open_positions": len(open_positions),
            "pending_orders": len(pending_orders),
            "last_ai_call_ts": state.get("last_ai_call_ts", 0),
            "data_quality": state.get("data_quality", 0.0),
            "ws_last_tick": state.get("ws_last_tick", 0),
            "price": state.get("price"),
            "execution_paused": state.get("execution_paused", False),
            "execution_reason": state.get("execution_reason", ""),
            "last_event_ts": state.get("last_event_ts", 0),
            "ws_age": (time.time() - price_ts) if price_ts else None,
            "candles": len(latest_candles)
        }
        with open("crash_dump.json", "a") as f:
            f.write(json.dumps(snapshot) + "\n")
        logger.critical("[CRASH DUMP] Written to crash_dump.json")
    except Exception as e:
        logger.error(f"[CRASH DUMP FAILED] {e}")

def watchdog_loop():
    while not shutdown_event.is_set():
        if time.time() - last_heartbeat > 10:
            if time.time() - state.get("ws_last_tick", 0) > 5:
                logger.critical("[WATCHDOG] SYSTEM FREEZE DETECTED - using WS only")
                dump_system_state()
                dump_threads()
        time.sleep(5)

def dump_threads():
    for thread in threading.enumerate():
        logger.debug(f"[THREAD] {thread.name} alive={thread.is_alive()}")

def pipeline_heartbeat():
    global last_pipeline_run
    with state_lock:
        state["last_heartbeat"] = time.time()
    logger.debug("[HEARTBEAT] System alive - WS active")

def log_event_rejection(event):
    logger.info(f"[PIPELINE BLOCKED] edge={event.get('edge',0)} < threshold={event.get('threshold',2.9)} [PIPELINE ENFORCEMENT]")
    try:
        with csv_lock:
            row = {"ts": utc_iso(), "type": "REJECTED_SIGNAL", "edge": event.get("edge",0), "threshold": event.get("threshold",2.9), "reason": "EDGE_BELOW_THRESHOLD"}
            dynamic_csv_writer(CSV_BLOCKS, row)
    except:
        pass

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>3-Factor Research Bot Dashboard</title>
    <script src="/static/dashboard.js"></script>
    <style>
        body { background:#0d1117; color:#c9d1d9; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; margin:20px; }
        h1, h2, h3 { color:#58a6ff; }
        table { border-collapse: collapse; width:100%; margin:20px 0; }
        th, td { padding:10px; border:1px solid #30363d; text-align:left; }
        th { background:#161b22; }
        .green { color:#3fb950; } .red { color:#f85149; }
        button { margin:5px; padding:8px 16px; background:#238636; color:white; border:none; border-radius:6px; cursor:pointer; }
        button:hover { background:#2ea043; }
        #dataBanner { padding:15px; border-radius:6px; margin-bottom:20px; font-weight:bold; }
        input { margin:5px; width:120px; }
        select { margin:5px; width:140px; }
        .debug-panel { background:#161b22; padding:15px; margin:15px 0; border-radius:6px; border:1px solid #30363d; }
    </style>
</head>
<body>

<h1>3-Factor Research Bot Dashboard</h1>
<p>
    <button type="button" onclick="refresh()">Refresh now</button>
    <label style="margin-left:12px;"><input type="checkbox" id="autoRefreshToggle"> Auto-refresh every 60s (optional)</label>
    <span id="refreshStatus" style="margin-left:8px;color:#8b949e;">Manual refresh by default — click Refresh now or enable auto</span>
</p>

<div id="dataBanner">
    Data Source: <span id="dataSource">Loading...</span>
</div>

<p><strong>Price:</strong> <span id="price">-</span></p>
<p><strong>Account Balance:</strong> <span id="accountBalance">-</span></p>
<p><strong>Daily PnL:</strong> <span id="dailyPnl">-</span></p>
<p><strong>Equity:</strong> <span id="equity">-</span></p>
<p><strong>Regime:</strong> <span id="regime">-</span></p>

<h3>Support / Resistance (24h Structural)</h3>
<p><strong>Swing High:</strong> <span id="swingHigh">-</span></p>
<p><strong>Swing Low:</strong> <span id="swingLow">-</span></p>
<p><strong>Dist to Resistance:</strong> <span id="distRes">-</span>%</p>
<p><strong>Dist to Support:</strong> <span id="distSup">-</span>%</p>
<p><strong>SR Zone:</strong> <span id="srZone">-</span>%</p>
<p><strong>SR State:</strong> <span id="srState">-</span></p>
<p><strong>SR Bias:</strong> <span id="srBias">-</span></p>

<h3>AI Decision (Last Signal)</h3>
<p><strong>AI Status:</strong> <span id="aiDecision">-</span></p>
<p><strong>AI Win Prob:</strong> <span id="aiProb">-</span></p>
<p><strong>AI Direction (raw):</strong> <span id="aiDirRaw">-</span></p>
<p><strong>Final Direction (after invert):</strong> <span id="finalDir">-</span></p>
<p><strong>Inverted:</strong> <span id="inverted">-</span></p>
<p><strong>AI Threshold:</strong> <span id="aiThresholdDisplay">-</span>%</p>
<p><strong>Edge Threshold (min to trigger):</strong> <span id="edgeThresholdDisplay">-</span></p>
<p><strong>AI Reason:</strong> <span id="aiReason">-</span></p>

<h3>System Status</h3>
<p id="why"></p>
<p>Last Fetch: <span id="lastFetch"></span></p>
<p>WS Age: <span id="ws_age"></span></p>

<div class="debug-panel">
    <h3>🔍 DEBUG STATE</h3>
    <p><strong>Last Check:</strong> <span id="lastCheckTime">-</span></p>
    <p><strong>Last Event:</strong> <span id="lastEventTime">-</span></p>
    <p><strong>Edge Score:</strong> <span id="edgeScore">-</span></p>
    <p><strong>Edge Progress (score / min required, max 6):</strong> <span id="edgeProgress">-</span></p>
    <p><strong>Flags:</strong> <span id="flags">-</span></p>
    <p><strong>Trigger:</strong> <span id="trigger">-</span></p>
    <p><strong>Skip Reason:</strong> <span id="skipReason">-</span></p>
    <p><strong>Signal Attempt:</strong> <span id="signalAttempt">-</span></p>
    <p><strong>Block Reason:</strong> <span id="blockReason">-</span></p>
    <p><strong>Last AI Call:</strong> <span id="lastAICall">-</span></p>
    <p><strong>AI Score:</strong> <span id="aiScore">-</span></p>
    <p><strong>Signal Cooldown:</strong> <span id="signalCooldown">-</span></p>
    <p><strong>AI Cooldown:</strong> <span id="aiCooldown">-</span></p>
    <p><strong>Last Pipeline:</strong> <span id="lastPipeline">-</span></p>
    <p><strong>Heartbeat:</strong> <span id="heartbeat">-</span></p>
    <p><strong>AI Input:</strong> <span id="aiInput">-</span></p>
    <p><strong>Features:</strong> <span id="features">-</span></p>
    <p><strong>Data Quality:</strong> <span id="dataQuality">-</span></p>
</div>

<div>
    <button onclick="toggleLive()">LIVE ARM: <span id="liveArmBtn">OFF</span></button>
    <button onclick="toggleEarlyFail()">Early Fail: <span id="earlyFailBtn">OFF</span></button>
    <button onclick="toggleInvert()">Invert Signal: <span id="invertBtn">OFF</span></button>
    <button onclick="toggleDebug()">Debug Mode: <span id="debugToggle">OFF</span></button>
    <button onclick="downloadDebug()">Download Debug Logs</button>
    <button onclick="window.location.href='/api/export_csv'">Download CSV Logs</button>
</div>

<h2>Controls</h2>
<label>Leverage:</label><input id="leverage" type="number" min="1" max="50" value="50"><br>
<label>Pullback %:</label>
<select id="pullbackThresh">
  <option value="0.1">0.1%</option>
  <option value="0.2">0.2%</option>
  <option value="0.3">0.3%</option>
  <option value="0.4">0.4%</option>
  <option value="0.5">0.5%</option>
  <option value="0.6">0.6%</option>
</select><br>
<label>Max Positions:</label><input id="maxConcurrentPositions" type="number" min="1" value="3"><br>
<label>AI Threshold:</label><input id="aiThreshold" type="number" min="0" max="100" value="68" onchange="updateThreshold(this.value)"><br>
<label>Edge Threshold:</label>
<select id="edgeThreshold" onchange="updateEdge(this.value)">
  <option value="0.5">0.5</option>
  <option value="1.0">1.0</option>
  <option value="1.5">1.5</option>
  <option value="2.0">2.0</option>
  <option value="2.5">2.5</option>
  <option value="3.0" selected>3.0</option>
  <option value="3.5">3.5</option>
  <option value="4.0">4.0</option>
  <option value="4.5">4.5</option>
  <option value="5.0">5.0</option>
  <option value="5.5">5.5</option>
  <option value="6.0">6.0</option>
</select><br>

<h2>Active Signals</h2>
<table>
    <thead><tr><th>Time</th><th>Dir (final)</th><th>Conf</th><th>Regime</th><th>Strategy</th><th>Trigger</th><th>Pull Req</th><th>Signal Price</th><th>Max Pull</th><th>Outcome</th><th>Fill Price</th><th>Exit Reason</th></tr></thead>
    <tbody id="signalsTable"></tbody>
</table>

<h2>Positions</h2>
<table>
    <thead><tr><th>Leg</th><th>Side</th><th>Qty</th><th>Entry</th><th>Current</th><th>SL</th><th>TP</th><th>PnL</th></tr></thead>
    <tbody id="positionsTable"></tbody>
</table>

<h2>Pending Orders</h2>
<table>
    <thead><tr><th>Age min</th><th>Side</th><th>Status</th><th>Qty</th><th>Limit Price</th><th>Signal Price</th></tr></thead>
    <tbody id="ordersTable"></tbody>
</table>

<h2>Expired Orders</h2>
<table>
    <thead><tr><th>Time</th><th>Dir</th><th>Limit Price</th><th>Age min</th><th>Reason</th><th>Conf</th><th>Mode</th></tr></thead>
    <tbody id="expiredOrdersTable"></tbody>
</table>

<h2>Trades</h2>
<table>
    <thead><tr><th>Time</th><th>ID</th><th>Dir (final)</th><th>Entry</th><th>Exit</th><th>Duration min</th><th>PnL %</th><th>Net USD</th><th>Gross USD</th><th>Fees USD</th><th>AI Band</th></tr></thead>
    <tbody id="tradesTable"></tbody>
</table>

<h2>AI History (Last 5)</h2>
<table>
    <thead><tr><th>Time</th><th>Trade ID</th><th>AI Dir (raw)</th><th>Final Dir</th><th>Inverted</th><th>Decision</th><th>Win Prob</th><th>Comment</th></tr></thead>
    <tbody id="aiHistoryTable"></tbody>
</table>

<h2>Analytics</h2>
<h3>AI Bands</h3><table id="aiBandsAnalytics"></table>
<h3>Exit Reasons</h3><table id="exitReasonsAnalytics"></table>

</body>
</html>
"""

DASHBOARD_JS = """(function () {
  try {
    function safeText(id, value) {
      const el = document.getElementById(id);
      if (el) el.innerText = value ?? "-";
    }
    function safeHTML(id, html) {
      const el = document.getElementById(id);
      if (el) el.innerHTML = html ?? "";
    }
    async function post(url, obj={}) {
      try {
        const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(obj)});
        if (!res.ok) {
          const err = await res.json();
          if (err.error) alert("Toggle blocked: " + err.error);
        }
        return res;
      } catch(e){}
    }
    async function updateThreshold(value) {
      await post('/api/set_threshold', {value: parseFloat(value)});
      refresh();
    }
    async function updateEdge(value) {
      await post('/api/set_edge_threshold', {value: parseFloat(value)});
      refresh();
    }
    async function toggleLive() {
      const cur = document.getElementById('liveArmBtn').innerText.includes('OFF');
      await post('/api/live_arm', {armed: cur});
      refresh();
    }
    async function toggleEarlyFail() {
      await post('/api/toggle_early_fail');
      refresh();
    }
    async function toggleInvert() {
      await post('/api/toggle_invert_signal');
      refresh();
    }
    async function toggleDebug() {
      const cur = document.getElementById('debugToggle').innerText.includes('OFF');
      await post('/api/toggle_debug', {enabled: cur});
      refresh();
    }
    function downloadDebug() {
      window.open('/api/export_debug', '_blank');
    }
    let refreshInFlight = false;
    async function refresh() {
      if (refreshInFlight) return;
      refreshInFlight = true;
      try {
        const r = await fetch('/api/state');
        if (r.status === 204) {
          return;
        }
        const d = await r.json();
        const rs = document.getElementById('refreshStatus');
        if (rs) rs.innerText = 'Last updated ' + new Date().toLocaleTimeString();
        const src = document.getElementById('dataSource');
        const banner = document.getElementById('dataBanner');
        if (src && banner) {
          if (d.execution_paused) {
            src.innerHTML = 'PAUSED - ' + (d.execution_reason || 'Unknown reason');
            src.className = 'text-red-500 font-bold animate-pulse';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-red-700 animate-pulse';
          } else if (d.price_source === 'WS') {
            src.innerHTML = 'REAL BYBIT MARKET DATA (WS)';
            src.className = 'text-green-400 font-bold';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-green-600';
          } else if (d.price_source === 'REST') {
            src.innerHTML = 'REST FALLBACK (DEGRADED)';
            src.className = 'text-orange-400 font-bold';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-orange-600';
          } else {
            src.innerHTML = 'CHECKING...';
            src.className = 'text-yellow-400 font-bold';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-yellow-600';
          }
        }
        safeText('lastFetch', d.last_fetch_success || 'never');
        let wsAgeText = '-';
        if (d.ws_last_tick) {
          const age = Math.round((Date.now()/1000 - d.ws_last_tick));
          wsAgeText = age + ' s';
          if (age > 10) wsAgeText += ' (STALE!)';
        }
        safeText('ws_age', wsAgeText);
        safeText('regime', d.regime || '-');
        safeText('price', d.price != null ? d.price.toLocaleString() : '-');
        safeText('accountBalance', '$' + (d.account_balance != null ? d.account_balance.toFixed(2) : '500.00'));
        safeText('dailyPnl', '$' + (d.daily_pnl_usd != null ? d.daily_pnl_usd.toFixed(2) : '0.00') + ' net (UTC day)');
        safeText('equity', '$' + (d.equity != null ? d.equity.toFixed(2) : '500.00'));

        const sr = d.support_resistance || {};
        safeText('swingHigh', sr.swing_high != null ? sr.swing_high.toLocaleString() : '-');
        safeText('swingLow', sr.swing_low != null ? sr.swing_low.toLocaleString() : '-');
        safeText('distRes', sr.dist_to_resistance != null ? (sr.dist_to_resistance*100).toFixed(2) : '-');
        safeText('distSup', sr.dist_to_support != null ? (sr.dist_to_support*100).toFixed(2) : '-');
        safeText('srZone', sr.sr_zone_pct != null ? (sr.sr_zone_pct*100).toFixed(2) : '-');
        safeText('srState', sr.sr_state || '-');
        safeText('srBias', sr.sr_bias || '-');

        safeText('aiDecision', d.last_ai?.decision || d.ai_outcome || 'NO_SIGNAL');
        safeText('aiProb', d.last_ai?.win_prob != null ? d.last_ai.win_prob + '%' : '-');
        safeText('aiDirRaw', d.last_ai?.direction || '-');
        safeText('finalDir', d.last_ai?.final_direction || '-');
        safeText('inverted', d.last_ai?.inverted ? 'YES' : 'NO');
        safeText('aiThresholdDisplay', d.ai_threshold != null ? d.ai_threshold : 'WAITING');
        safeText('edgeThresholdDisplay', d.edge_threshold != null ? d.edge_threshold : '3.0');
        safeText('aiReason', d.last_ai?.reason || d.ai_reason || '-');
        const why = document.getElementById('why');
        if (why) {
          let whyHtml = '';
          if (d.engine_reason) whyHtml += `<p>ENGINE: ${d.engine_reason}</p>`;
          if (d.ai_reason) whyHtml += `<p>AI: ${d.ai_reason}</p>`;
          if (d.reasons && d.reasons.length) whyHtml += (d.reasons||[]).map(r=>`<li>${r}</li>`).join('');
          why.innerHTML = whyHtml || 'No rejection reason this candle';
        }
        safeText('liveArmBtn', `LIVE ARM: ${d.live_armed ? 'ON' : 'OFF'}`);
        const earlyBtn = document.getElementById('earlyFailBtn');
        if (earlyBtn) {
          earlyBtn.innerText = `Early Fail ${d.early_fail_enabled ? 'ON' : 'OFF'}`;
          earlyBtn.style.backgroundColor = d.early_fail_enabled ? '#10b981' : '#ef4444';
        }
        const invertBtn = document.getElementById('invertBtn');
        if (invertBtn) {
          invertBtn.innerText = `Invert Signal ${d.invert_signal ? 'ON' : 'OFF'}`;
          invertBtn.style.backgroundColor = d.invert_signal ? '#f97316' : '#374151';
        }
        const debugBtn = document.getElementById('debugToggle');
        if (debugBtn) {
          debugBtn.innerText = `Debug Mode (Console) ${d.debug_enabled ? 'ON' : 'OFF'}`;
          debugBtn.style.backgroundColor = d.debug_enabled ? '#10b981' : '#ef4444';
        }
        if (d.leverage) {
          const lev = document.getElementById('leverage');
          if (lev) lev.value = d.leverage;
        }
        if (d.max_active_signals) {
          const mcp = document.getElementById('maxConcurrentPositions');
          if (mcp) mcp.value = d.max_active_signals;
        }
        if (d.ai_threshold) {
          const aiThresh = document.getElementById('aiThreshold');
          if (aiThresh) aiThresh.value = d.ai_threshold;
        }
        if (d.edge_threshold) {
          const edgeSel = document.getElementById('edgeThreshold');
          if (edgeSel) edgeSel.value = d.edge_threshold;
        }
        if (d.pullback_threshold) {
          const pb = document.getElementById('pullbackThresh');
          if (pb) pb.value = (d.pullback_threshold * 100).toFixed(1);
        }
        safeHTML('signalsTable', (d.signal_info?.signals || []).filter(s => !s.terminal && (s.status === "ACTIVE" || s.status === "ORDERED")).map(s => `
          <tr>
            <td>${s.created_ts || '-'}</td>
            <td>${s.final_direction || s.dir || '-'}</td>
            <td>${s.conf || '-'}</td>
            <td>${s.regime || '-'}</td>
            <td>${s.strategy || '-'}</td>
            <td>${s.trigger || '-'}</td>
            <td>${s.pull_req != null ? s.pull_req.toFixed(2) : '-' }%</td>
            <td>${s.signal_price !== undefined ? s.signal_price.toFixed(2) : '-'}</td>
            <td>${s.max_pull != null ? s.max_pull.toFixed(2) : '-' }%</td>
            <td>${s.outcome || '-'}</td>
            <td>${s.fill_price != null ? s.fill_price.toFixed(2) : '-'}</td>
            <td>${s.exit_reason || '-'}</td>
          </tr>
        `).join(''));
        safeHTML('positionsTable', (d.positions||[]).map(l => `
          <tr>
            <td>${l.leg || '-'}</td>
            <td>${l.side || '-'}</td>
            <td>${l.qty || '-'}</td>
            <td>${l.entry != null ? l.entry.toFixed(2) : '-'}</td>
            <td>${l.current_price != null ? l.current_price.toFixed(2) : '-'}</td>
            <td>${l.sl != null ? l.sl.toFixed(2) : '-'}</td>
            <td>${l.tp || '-'}</td>
            <td>${l.pnl_pct_margin?.toFixed(2)||'-'}% $${l.unreal_usd?.toFixed(2)||'-'}</td>
          </tr>
        `).join(''));
        safeHTML('ordersTable', (d.orders||[]).map(o => `
          <tr>
            <td>${o.age_min?.toFixed(1)||'-'}</td>
            <td>${o.side || '-'}</td>
            <td>${o.status || '-'}</td>
            <td>${o.qty || '-'}</td>
            <td>${o.limit_price?.toFixed(2)||'-'}</td>
            <td>${o.signal_price?.toFixed(2)||'-'}</td>
          </tr>
        `).join(''));
        safeHTML('expiredOrdersTable', (d.expired_orders || []).map(e => `
          <tr>
            <td>${e.time || '-'}</td>
            <td>${e.dir || '-'}</td>
            <td>${e.limit_price?.toFixed(2)||'-'}</td>
            <td>${e.age_min?.toFixed(1)||'-'}</td>
            <td>${e.reason||'-'}</td>
            <td>${e.conf||'-'}</td>
            <td>${e.mode||'-'}</td>
          </tr>
        `).join(''));
        safeHTML('tradesTable', (d.trades||[]).map(t => `
          <tr>
            <td>${t.ts || '-'}</td>
            <td>${t.trade_id || '-'}</td>
            <td>${t.final_direction || t.dir || '-'}</td>
            <td>${t.entry != null ? t.entry.toFixed(2) : '-'}</td>
            <td>${t.exit != null ? t.exit.toFixed(2) : '-'}</td>
            <td>${t.dur_min != null ? t.dur_min.toFixed(1) : '-'}</td>
            <td>${t.pnl != null ? t.pnl.toFixed(2) : '-' }%</td>
            <td>$${t.net_pnl_usd?.toFixed(2)||'-'}</td>
            <td>$${t.gross_pnl_usd?.toFixed(2)||'-'}</td>
            <td>$${t.fees_usd?.toFixed(2)||'-'}</td>
            <td>${t.ai_band || '-'}</td>
          </tr>
        `).join(''));
        safeHTML('aiHistoryTable', (d.ai_history || []).map(a => `
          <tr>
            <td>${a.time || '-'}</td>
            <td>${a.trade_id || '-'}</td>
            <td>${a.ai_direction_raw || '-'}</td>
            <td>${a.final_direction || '-'}</td>
            <td>${a.inverted ? 'YES' : 'NO'}</td>
            <td>${a.decision || '-'}</td>
            <td>${a.win_prob != null ? a.win_prob.toFixed(0) + '%' : '-'}</td>
            <td title="${a.comment || '-'}">${(a.comment || '').substring(0, 80)}...</td>
          </tr>
        `).join(''));
        safeHTML('aiBandsAnalytics', Object.entries(d.analytics?.ai_bands || {}).map(([k,v])=>`
          <tr>
            <td>${k}%</td>
            <td>${v.trades}</td>
            <td>${v.wins}</td>
            <td>${v.pnl.toFixed(2)}</td>
          </tr>
        `).join(''));
        safeHTML('exitReasonsAnalytics', Object.entries(d.analytics?.exit_reasons || {}).map(([k,v])=>`
          <tr>
            <td>${k}</td>
            <td>${v}</td>
          </tr>
        `).join(''));
        safeText('wsLatency', d.diag?.ws_latency_ms ?? '-');
        safeText('engineLoop', d.diag?.engine_loop_ms ?? '-');
        safeText('aiLatency', d.diag?.ai_latency_ms ?? '-');
        safeText('orderLatency', d.diag?.order_latency_ms ?? '-');
        safeText('signalsLastHour', d.diag?.signals_last_hour ?? '-');
        safeText('wsStatus', d.diag?.ws_status ?? '-');
        safeText('engineStatus', d.diag?.engine_status ?? '-');
        safeText('aiStatus', d.diag?.ai_status ?? '-');

        const dbg = d.debug_state || {};
        safeText('lastCheckTime', dbg.last_check_time || '-');
        safeText('lastEventTime', dbg.last_event_time || '-');
        safeText('edgeScore', dbg.last_edge_score || '0');
        safeText('edgeProgress', dbg.edge_progress || '-');
        safeText('flags', JSON.stringify(dbg.last_flags || {}));
        safeText('trigger', dbg.last_trigger ? 'YES' : 'NO');
        safeText('skipReason', dbg.skip_reason || '-');
        safeText('signalAttempt', dbg.last_signal_attempt ? dbg.last_signal_attempt.time : '-');
        safeText('blockReason', dbg.last_block_reason || '-');
        safeText('lastAICall', dbg.last_ai_call || '-');
        safeText('aiScore', dbg.last_ai_score || '-');
        safeText('signalCooldown', dbg.signal_cooldown_active ? 'ACTIVE (' + dbg.cooldown_remaining_signal + 's)' : 'READY');
        safeText('aiCooldown', dbg.ai_cooldown_active ? 'ACTIVE (' + dbg.cooldown_remaining_ai + 's)' : 'READY');
        safeText('lastPipeline', dbg.last_pipeline_stage || '-');
        safeText('heartbeat', d.heartbeat ? 'Alive (' + Math.round(Date.now()/1000 - d.heartbeat) + 's ago)' : '-');
        safeText('aiInput', JSON.stringify(d.ai_input || {}));
        safeText('features', JSON.stringify(d.feature_snapshot || {}));
        safeText('dataQuality', (d.data_quality * 100).toFixed(1) + '%');
      } catch(e) {
        console.error("Refresh failed:", e);
      } finally {
        refreshInFlight = false;
      }
    }
    document.addEventListener('DOMContentLoaded', () => {
      const dropdowns = {
        'leverage': '/api/set_leverage',
        'pullbackThresh': '/api/set_pullback_threshold',
        'maxConcurrentPositions': '/api/set_max_active_signals'
      };
      Object.keys(dropdowns).forEach(id => {
        const el = document.getElementById(id);
        if (el) {
          el.addEventListener('change', function() {
            post(dropdowns[id], {value: this.value});
            refresh();
          });
        }
      });
      const AUTO_REFRESH_MS = 60000;
      let refreshTimer = null;
      function scheduleAutoRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = null;
        const toggle = document.getElementById('autoRefreshToggle');
        if (toggle && toggle.checked) {
          refreshTimer = setInterval(refresh, AUTO_REFRESH_MS);
        }
      }
      const autoToggle = document.getElementById('autoRefreshToggle');
      if (autoToggle) {
        autoToggle.checked = false;
        autoToggle.addEventListener('change', scheduleAutoRefresh);
      }
      scheduleAutoRefresh();
      refresh();
    });
    window.refresh = refresh;
    window.toggleLive = toggleLive;
    window.toggleEarlyFail = toggleEarlyFail;
    window.toggleInvert = toggleInvert;
    window.toggleDebug = toggleDebug;
    window.downloadDebug = downloadDebug;
    window.updateThreshold = updateThreshold;
    window.updateEdge = updateEdge;
  } catch (e) {
    console.error("DASHBOARD BOOT FAILURE", e);
  }
  console.info("dashboard.js loaded: true");
})();"""

BOT_CONTROL_SECRET = os.getenv("BOT_CONTROL_SECRET", "").strip()
_BOT_PUBLIC_GET_PATHS = frozenset({"/", "/health", "/api/state", "/static/dashboard.js"})

def _bot_control_secret_ok():
    if not BOT_CONTROL_SECRET:
        if os.getenv("NODE_ENV") == "production" or os.getenv("RAILWAY_ENVIRONMENT"):
            return False
        return True
    header = (request.headers.get("X-Bot-Control-Secret") or "").strip()
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        header = header or auth[7:].strip()
    return header == BOT_CONTROL_SECRET

@app.before_request
def _enforce_bot_control_auth():
    path = request.path.rstrip("/") or "/"
    if request.method == "GET" and path in _BOT_PUBLIC_GET_PATHS:
        return None
    if not _bot_control_secret_ok():
        return jsonify({"error": "unauthorized"}), 401
    return None

@app.route('/static/dashboard.js')
def dashboard_js():
    return DASHBOARD_JS, 200, {'Content-Type': 'application/javascript'}

@app.route('/')
def dashboard():
    return render_template_string(HTML)

@app.route('/api/state')
def api_state():
    try:
        with state_lock:
            snapshot = copy.deepcopy(state)
            positions_copy = copy.deepcopy(open_positions)
            pending_orders_copy = copy.deepcopy(pending_orders)
            trades_copy = copy.deepcopy(trades)
            expired_orders_copy = copy.deepcopy(expired_orders)
            ai_history_copy = copy.deepcopy(state["ai_history"])
            trades_map_copy = copy.deepcopy(trades_map)
        snapshot["positions"] = []
        total_unreal = 0.0
        for pos in positions_copy:
            pos_copy = copy.deepcopy(pos)
            pos_copy["current_price"] = snapshot["price"]
            dir_factor = 1 if pos["dir"] == "LONG" else -1
            move_pct = ((snapshot["price"] - pos["entry"]) / pos["entry"] * 100 * dir_factor) if snapshot["price"] and pos["entry"] else 0
            pnl_usd = (move_pct / 100) * FIXED_MARGIN_USDT * pos.get("leverage", 20)
            pnl_pct_margin = move_pct * pos.get("leverage", 20)
            pos_copy["pnl_pct_margin"] = pnl_pct_margin
            pos_copy["unreal_usd"] = pnl_usd
            total_unreal += pnl_usd
            pos_copy["side"] = "LONG" if pos["dir"] == "LONG" else "SHORT"
            pos_copy["leg"] = "Main"
            snapshot["positions"].append(pos_copy)
        snapshot["equity"] = snapshot["account_balance"] + total_unreal
        orders = []
        for o in pending_orders_copy:
            age = (time.time() - o["created_ts"]) / 60 if o["created_ts"] else 0
            oc = copy.deepcopy(o)
            oc["age_min"] = age
            orders.append(oc)
        snapshot["orders"] = orders
        snapshot["trades"] = trades_copy
        snapshot["expired_orders"] = expired_orders_copy
        snapshot["ai_history"] = ai_history_copy
        snapshot["trades_map"] = trades_map_copy
        snapshot["ai_verdict"] = f"AI reviewer {'ON' if snapshot['ai_enabled'] else 'OFF'} | Threshold {snapshot.get('ai_threshold','WAITING')}%"
        snapshot.setdefault("regime", "UNKNOWN")
        snapshot.setdefault("strategy", "SR")
        snapshot.setdefault("direction", "FLAT")
        snapshot.setdefault("ema_status", {})
        snapshot.setdefault("support_resistance", {"pivot": None, "s1": None, "s2": None, "r1": None, "r2": None, "swing_high": None, "swing_low": None, "ts": None, "window": "96x15m (~24h rolling)", "dist_to_resistance": 0.0, "dist_to_support": 0.0, "sr_zone_pct": SR_ZONE_PCT, "sr_state": "UNKNOWN", "sr_bias": "BOTH_ALLOWED"})
        snapshot.setdefault("signal_info", {"active": False, "count": 0, "signals": []})
        snapshot.setdefault("heartbeat", 0)
        snapshot.setdefault("price_ts", 0)
        snapshot["server_ts"] = utc_iso()
        if snapshot.get("price") is None:
            snapshot["price"] = None
        reconcile_stale_signals()
        sync_cooldown_debug_state()
        active_list = []
        pending_ids = {o.get("trade_id") for o in pending_orders_copy if o.get("trade_id")}
        open_ids = {p.get("trade_id") for p in positions_copy if p.get("trade_id")}
        live_ids = pending_ids | open_ids
        for t in trades_map_copy.values():
            s = t.get("signal_ref")
            if not isinstance(s, dict):
                continue
            tid = s.get("trade_id")
            st = s.get("status")
            if tid not in live_ids or is_terminal_signal(s):
                continue
            created_ts = s.get("created_ts_ts") or 0
            expires_ts = s.get("expires_ts", 0)
            active_list.append({
                "trade_id": s.get("trade_id"),
                "dir": s.get("final_direction") or s.get("dir"),
                "conf": s.get("ai_win_prob", 0),
                "regime": s.get("regime"),
                "created_ts": s.get("created_ts"),
                "expires_ts": expires_ts,
                "ttl_remaining": (expires_ts - time.time()),
                "age": time.time() - created_ts,
                "pullback_pct": s.get("pull_req", 0),
                "regime_birth": s.get("regime_birth", snapshot.get("regime", "-")),
                "strategy_birth": s.get("strategy_birth", snapshot.get("strategy", "-")),
                "status": s.get("status", "UNKNOWN"),
                "outcome": s.get("outcome", "PENDING"),
                "terminal": is_terminal_signal(s),
                "fill_price": s.get("fill_price"),
                "exit_reason": s.get("exit_reason"),
                "trigger": s.get("trigger", "BASE"),
                "signal_price": s.get("signal_price")
            })
        exposure_count = get_active_signal_count()
        snapshot["signal_info"] = {"active": len(active_list) > 0,"count": exposure_count,"signals": active_list}
        snapshot["diag"]["signals_last_hour"] = 0
        snapshot["account_balance"] = get_display_balance()
        snapshot["ai_input"] = LAST_AI_PAYLOAD if LAST_AI_PAYLOAD else state.get("feature_snapshot", {"status": "NO_AI_CALL_YET"})
        snapshot["ai_input_time"] = LAST_AI_TIMESTAMP
        snapshot["feature_snapshot"] = state.get("feature_snapshot", {})
        snapshot["data_quality"] = state.get("data_quality", 0.0)
        snapshot["edge_threshold"] = get_edge_threshold()
        snapshot["edge_options"] = EDGE_OPTIONS
        snapshot["max_active_signals"] = state.get("max_active_signals", MAX_CONCURRENT_POSITIONS_DEFAULT)
        logger.info(f"[API STATE] edge_threshold synced to UI: {snapshot['edge_threshold']} [PIPELINE ENFORCEMENT]")
        return jsonify(snapshot)
    except Exception as e:
        logger.error(f"/api/state error: {str(e)}")
        return jsonify({})

_startup_complete = False

@app.route('/health')
def health():
    """Railway healthcheck — must respond before long Bybit/candle bootstrap finishes."""
    return jsonify({
        "status": "alive" if _startup_complete else "starting",
        "ready": _startup_complete,
        "last_heartbeat": last_heartbeat,
        "time_since_heartbeat": time.time() - last_heartbeat,
        "execution_paused": state.get("execution_paused", False),
        "execution_reason": state.get("execution_reason", ""),
    }), 200

@app.route('/debug_state')
def get_debug_state():
    with state_lock:
        return jsonify(state.get("debug_state", {}))

@app.route('/api/resume', methods=['POST'])
def api_resume():
    set_execution_paused("")
    return jsonify({"status": "resumed"})

@app.route('/api/pause', methods=['POST'])
def api_pause():
    data = request.get_json() or {}
    reason = data.get("reason", "ADMIN_PAUSE")
    set_execution_paused(reason)
    return jsonify({"status": "paused", "reason": reason})

@app.route('/api/toggle_early_fail', methods=['POST'])
def toggle_early_fail():
    with state_lock:
        state["early_fail_enabled"] = not state["early_fail_enabled"]
        save_persistent_config()
    return jsonify({"early_fail_enabled": state["early_fail_enabled"]})

@app.route('/api/toggle_invert_signal', methods=['POST'])
def toggle_invert_signal():
    with state_lock:
        state["invert_signal"] = not state.get("invert_signal", False)
        save_persistent_config()
    return jsonify({"invert_signal": state["invert_signal"]})

@app.route('/api/toggle_debug', methods=['POST'])
def toggle_debug():
    data = request.get_json() or {}
    with state_lock:
        state["debug_enabled"] = data.get("enabled", not state["debug_enabled"])
        save_persistent_config()
        update_logger_level()
    return jsonify({"debug_enabled": state["debug_enabled"]})

@app.route('/api/live_arm', methods=['POST'])
def live_arm():
    data = request.get_json() or {}
    with state_lock:
        state["live_armed"] = data.get("armed", False)
        save_persistent_config()
    return jsonify({"live_armed": state["live_armed"]})

@app.route('/api/set_leverage', methods=['POST'])
def set_leverage():
    data = request.get_json() or {}
    val = int(data.get("value", DEFAULT_RESEARCH_LEVERAGE))
    val = max(1, min(val, MAX_RESEARCH_LEVERAGE))
    with state_lock:
        state["leverage"] = val
        save_persistent_config()
    if int(data.get("value", val)) > MAX_RESEARCH_LEVERAGE:
        logger.warning(f"[LEVERAGE] Capped request to {MAX_RESEARCH_LEVERAGE}x (research max) [PIPELINE ENFORCEMENT]")
    return jsonify({"leverage": val})

@app.route('/api/set_pullback_threshold', methods=['POST'])
def set_pullback_threshold():
    data = request.get_json() or {}
    val = float(data.get("value", 0.0)) / 100.0
    with state_lock:
        state["pullback_threshold"] = max(0.0, min(val, 0.006))
        save_persistent_config()
    logger.info(f"[DASHBOARD CONTROL] pullback_threshold set to {state['pullback_threshold']} [PIPELINE ENFORCEMENT]")
    return jsonify({"pullback_threshold": state["pullback_threshold"]})

@app.route('/api/set_max_active_signals', methods=['POST'])
def set_max_active_signals():
    data = request.get_json() or {}
    val = int(data.get("value", 3))
    with state_lock:
        state["max_active_signals"] = val
        save_persistent_config()
    return jsonify({"max_active_signals": val})

@app.route('/api/set_threshold', methods=['POST'])
def set_threshold():
    data = request.get_json() or {}
    value = data.get("value")
    set_ai_threshold(value)
    return jsonify({"status": "ok", "ai_threshold": value})

@app.route('/api/set_edge_threshold', methods=['POST'])
def set_edge_threshold():
    data = request.get_json() or {}
    value = round(float(data.get("value", 3.0)), 1)
    if value not in [round(x, 1) for x in EDGE_OPTIONS]:
        logger.warning(f"[EDGE SET API] Invalid value {value} rejected [PIPELINE ENFORCEMENT]")
        return jsonify({"status": "error", "msg": "invalid threshold"}), 400
    with state_lock:
        state["edge_threshold"] = value
        save_persistent_config()
    logger.info(f"[EDGE SET API] threshold updated to {value} and locked [PIPELINE ENFORCEMENT]")
    enforce_edge_threshold_options()
    return jsonify({"status": "ok", "new_value": value})

@app.route('/api/download_debug_config')
def download_debug_config():
    with state_lock:
        config = {k: v for k, v in state.items() if not k.startswith("_")}
    return jsonify(config), 200, {'Content-Disposition': 'attachment; filename=debug_config.json'}

@app.route('/api/export_debug')
def export_debug():
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            debug_data = list(DEBUG_LOG_BUFFER)
            z.writestr("debug.json", json.dumps(debug_data, indent=2, default=str))
        buf.seek(0)
        return send_file(buf, mimetype='application/zip', as_attachment=True, download_name='debug_export.zip')
    except Exception as e:
        logger.error(f"[EXPORT ERROR] {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/export_csv')
def export_csv():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in [CSV_DECISIONS, CSV_TRADES, CSV_EXPIRED, CSV_BLOCKS, CSV_AI_TRANCHE, CSV_SETUP_LOG, CSV_CANDLES, SIGNAL_SNAPSHOT_FILE, SIGNAL_REPLAY_FILE, TRADE_OUTCOME_FILE, COUNTERFACTUAL_FILE]:
            if os.path.exists(file):
                zip_file.write(file)
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='3factor_logs.zip')

def calc_position_qty(price, leverage, margin_usdt=None):
    try:
        if price is None or price <= 0 or leverage is None:
            logger.error(f"[QTY CALC FAIL] price={price} leverage={leverage}")
            return 0.0001
        market = bybit_public.market(SYMBOL)
        min_qty = market.get("limits", {}).get("amount", {}).get("min", 0.001)
        min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
        fee_buffer = 1 - (MAKER_FEE_PCT + TAKER_FEE_PCT)
        notional = float(margin_usdt or FIXED_MARGIN_USDT) * (leverage or 20) * fee_buffer
        if notional < min_notional:
            notional = min_notional
        qty = notional / price
        qty = max(min_qty, qty)
        qty = float(bybit_public.amount_to_precision(SYMBOL, qty))
        if qty < min_qty:
            qty = min_qty
        return qty
    except Exception as e:
        logger.error(f"Qty calc failed: {e}")
        return 0.0001

def utc_iso(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.isoformat()

def shutdown_handler(signum, frame):
    if shutdown_event.is_set():
        return
    logger.warning(f"[SHUTDOWN] Signal received: {signum}")
    shutdown_event.set()
    logger.warning("[SHUTDOWN] Controlled shutdown initiated")

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

def ema(values, period):
    if len(values) < period:
        return None
    if any(math.isnan(v) for v in values):
        return None
    sma = sum(values[:period]) / period
    k = 2 / (period + 1)
    ema_val = sma
    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val

def fetch_ohlcv():
    global latest_candles, last_candle_ts, last_ohlcv_fetch
    if time.time() - last_ohlcv_fetch < OHLCV_FETCH_INTERVAL:
        return True
    for attempt in range(5):
        try:
            candles = bybit_public.fetch_ohlcv(SYMBOL, '15m', limit=250)
            if candles and len(candles) >= MIN_CANDLES:
                with state_lock:
                    latest_candles = candles[-250:]
                    last_candle_ts = candles[-1][0] / 1000 if candles else time.time()
                    state["last_data_ts"] = time.time()
                    state["ohlcv_ready"] = True
                    state["data_error"] = None
                populate_candle_buffers_from_candles(candles)
                last_ohlcv_fetch = time.time()
                update_ema()
                trend_info()
                update_market_context(force=True)
                with state_lock:
                    state.update({"last_fetch_success": utc_iso()})
                    state.update({"data_source": "rest_only"})
                return True
        except Exception as e:
            logger.warning(f"[OHLCV RETRY {attempt+1}/5] {e}")
            time.sleep(2)
    with state_lock:
        state["ohlcv_ready"] = False
        state["data_error"] = "OHLCV_FETCH_FAILED"
    logger.critical("[STARTUP] PRELOAD FAILED — SYSTEM HALTED")
    set_execution_paused("PRELOAD_FAILED")
    raise RuntimeError("No candles available — cannot continue")

def update_ema():
    with state_lock:
        closes = [c[4] for c in latest_candles]
    if len(closes) < EMA_LONG:
        return
    ema9_val = ema(closes, EMA_FAST)
    ema21_val = ema(closes, EMA_SLOW)
    ema200_val = ema(closes, EMA_LONG)
    if ema9_val is None or ema21_val is None or ema200_val is None:
        return
    ema21_prev = ema(closes[:-3], EMA_SLOW) if len(closes) > EMA_SLOW + 3 else ema21_val
    ema9_prev = ema(closes[:-3], EMA_FAST) if len(closes) > EMA_FAST + 3 else ema9_val
    spread = (ema9_val - ema21_val) / ema21_val if ema21_val else 0.0
    with state_lock:
        state["ema_status"] = {
            "ema9": ema9_val,
            "ema21": ema21_val,
            "ema200": ema200_val,
            "ema_spread": round(spread, 6),
            "ema9_slope_pct": _pct_diff(ema9_val, ema9_prev),
            "ema21_slope_pct": _pct_diff(ema21_val, ema21_prev),
            "ema9_above_ema21": ema9_val > ema21_val,
            "ema21_above_ema200": ema21_val > ema200_val,
        }

def update_support_resistance():
    with state_lock:
        candles_snapshot = copy.deepcopy(latest_candles)
    if not candles_snapshot:
        return
    with state_lock:
        price = state.get("price")
    if price is None or price <= 0:
        return
    highs96 = [c[2] for c in candles_snapshot[-96:]]
    lows96 = [c[3] for c in candles_snapshot[-96:]]
    high_struct = max(highs96)
    low_struct = min(lows96)
    close_struct = candles_snapshot[-2][4] if len(candles_snapshot) >= 2 else 0
    pivot_struct = (high_struct + low_struct + close_struct) / 3
    r1_struct = pivot_struct * 2 - low_struct
    s1_struct = pivot_struct * 2 - high_struct
    highs32 = [c[2] for c in candles_snapshot[-32:]]
    lows32 = [c[3] for c in candles_snapshot[-32:]]
    high_active = max(highs32 + [price]) if price > 0 else max(highs32)
    low_active = min(lows32 + [price]) if price > 0 else min(lows32)
    pivot_active = (high_active + low_active + close_struct) / 3

    swing_high, swing_low = compute_structural_sr(candles_snapshot)
    dist_high, dist_low, near_res, near_sup = sr_context(price, swing_high, swing_low)
    sr_state = classify_sr_state(dist_high, dist_low)
    sr_bias_val = sr_bias(sr_state, state.get("regime", "UNKNOWN"))

    with state_lock:
        state["support_resistance"].update({
            "pivot": pivot_struct,
            "r1": r1_struct,
            "s1": s1_struct,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "dist_to_resistance": dist_high,
            "dist_to_support": dist_low,
            "sr_zone_pct": SR_ZONE_PCT,
            "sr_state": sr_state,
            "sr_bias": sr_bias_val,
            "ts": utc_iso()
        })
        state.update({"pivot_structural": pivot_struct})
        state.update({"pivot_active": pivot_active})

def compute_regime_pressure():
    if len(tick_prices) < 10:
        return 0.0
    diffs = [tick_prices[i+1] - tick_prices[i] for i in range(len(tick_prices)-1)]
    up = sum(1 for d in diffs if d > 0)
    down = sum(1 for d in diffs if d < 0)
    persistence = (up - down) / len(diffs) if len(diffs) else 0
    velocity = (tick_prices[-1] - tick_prices[0]) / tick_prices[0] if tick_prices[0] else 0
    return max(-1.0, min(1.0, 0.7 * persistence + 0.3 * velocity))

def update_price(price: float):
    if price <= 0:
        return
    now = time.time()
    with state_lock:
        state["price"] = price
        state["price_ts"] = now
        state["ws_last_tick"] = now
        state["price_seq"] += 1
        state["price_source"] = "WS"
        state["last_data_ts"] = now
        if not state["ws_ready"]:
            state["ws_ready"] = True
            state["data_source"] = "ws_ready"
            logger.info(f"[WS] FIRST TICK RECEIVED - Price: {price} | ws_ready=True [PIPELINE ENFORCEMENT]")
            if not is_system_ready():
                state["system_ready"] = True
                state["ready_since"] = now
                logger.info("[SYSTEM] READY STATE INITIALIZED FROM FIRST TICK - execution unpaused [PIPELINE ENFORCEMENT]")
            if state.get("execution_paused") and state.get("execution_reason") in ["WS_STALE", "THREAD_CRASH", "STALE_DATA_HARD_STOP"]:
                set_execution_paused("")
                logger.info("[WS RECOVERY][STATE_SYNC] Execution auto-resumed after first tick [PIPELINE ENFORCEMENT]")
    tick_prices.append(price)
    full_pipeline_trace("[WS]", f"PRICE_UPDATED price={price}", None)
    if not state.get("ws_ready") or len(latest_candles) < MIN_CANDLES:
        logger.info("[SYSTEM] Not ready for context build")
        return
    event = detect_event_light()
    if event and event.get("event_trigger"):
        process_signal(event)
    elif not event:
        logger.info("[EVENT GATE] No meaningful event - skipping pipeline")

def validate_market_data():
    with state_lock:
        now = time.time()
        price_ok = state.get("price") is not None and state["price"] > 0
        ws_ok = state.get("ws_last_tick") is not None and (now - state["ws_last_tick"] < STALE_HARD_SEC)
        ohlcv_ok = state.get("ohlcv_ready", False)
        ema_ok = all([state["ema_status"].get("ema9") is not None,state["ema_status"].get("ema21") is not None,state["ema_status"].get("ema200") is not None])
        candle_ok = (now - last_candle_ts < CANDLE_STALE_SEC) or (len(latest_candles) >= MIN_CANDLES)
        market_ready = price_ok and ws_ok
        indicator_ready = ohlcv_ok and ema_ok and candle_ok
        system_ready = market_ready and indicator_ready
        if not system_ready:
            reason = []
            if not price_ok: reason.append("no_price")
            if not ws_ok: reason.append("ws_stale")
            if not ohlcv_ok: reason.append("ohlcv_not_ready")
            if not ema_ok: reason.append("ema_not_ready")
            if not candle_ok: reason.append("candle_stale")
            state["last_engine_error"] = f"Market data invalid: {', '.join(reason)}"
            state["system_ready"] = False
            if not ws_ok:
                state["allow_rest_price"] = True
            logger.warning(f"[SYSTEM] not ready: {state['last_engine_error']} [PIPELINE ENFORCEMENT]")
            return False
        if state.get("last_ready_ts", 0) == 0:
            state["last_ready_ts"] = now
        elif now - state["last_ready_ts"] >= READY_STABLE_SEC:
            if not state.get("system_ready"):
                logger.info(f"[SYSTEM READY] STABLE for {READY_STABLE_SEC}s -> system_ready=True")
            state["system_ready"] = True
        state["price_source"] = "WS"
        state["allow_rest_price"] = False
        state["last_engine_error"] = ""
        state["engine_reason"] = ""
        return True

def trend_info():
    with state_lock:
        ema9 = state["ema_status"].get("ema9")
        ema21 = state["ema_status"].get("ema21")
        ema200 = state["ema_status"].get("ema200")
    if ema9 is None or ema21 is None or ema200 is None:
        return "UNKNOWN"
    if ema9 > ema200 and ema21 > ema200:
        regime = "BULL"
    elif ema9 < ema200 and ema21 < ema200:
        regime = "BEAR"
    else:
        regime = "RANGE"
    with state_lock:
        state.update({"regime": regime})
    return regime

def reset_runtime_state():
    global bot_start_time, trades, pending_orders, expired_orders, open_positions, trades_map
    logger.warning("[RESET] HARD RESET START - clearing all in-memory state for true clean slate")
    bot_start_time = time.time()
    trades.clear()
    pending_orders.clear()
    expired_orders.clear()
    open_positions.clear()
    trades_map.clear()
    with state_lock:
        state.update({
            "account_balance": STARTING_BALANCE,
            "daily_pnl_usd": 0.0,
            "last_ai": {"win_prob": 0, "direction": None, "trade_id": None, "comment": "NO_SIGNAL", "ai_error": False, "factors": {}, "source": "NONE", "decision": None},
            "last_ai_ts": 0.0,
            "last_ai_fp": "",
            "ai_history": [],
            "regime": "UNKNOWN",
            "strategy": "SR",
            "direction": "FLAT",
            "signal_direction": "FLAT",
            "signal_info": {"active": False, "count": 0, "signals": []},
            "execution_status": "BLOCKED",
            "last_block_time": 0.0,
            "last_setup_time": 0.0,
            "last_engine_error": "None",
            "execution_paused": False,
            "execution_reason": "",
            "_pause_priority": 0,
            "last_event_ts": 0.0,
            "bootstrap_done": False
        })
    logger.warning("[RESET] HARD RESET COMPLETE - true clean slate achieved")

def load_positions():
    if state.get("strategy_mode") != "RESEARCH" and os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, 'r') as f:
            with state_lock:
                open_positions.extend(json.load(f))

def save_positions():
    with state_lock:
        snapshot = copy.deepcopy(open_positions)
    tmp = POSITIONS_FILE + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(snapshot, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, POSITIONS_FILE)

def validate_state():
    now = time.time()
    with state_lock:
        prune_signals()
        pending_orders[:] = [o for o in pending_orders if isinstance(o, dict) and o.get("created_ts", 0) > 0]
        expired_orders[:] = [o for o in expired_orders if isinstance(o, dict) and o.get("created_ts", 0) > 0]
        open_positions[:] = [p for p in open_positions if isinstance(p, dict) and p.get("entry", 0) > 0]
    return True

def record_ai_decision(trade_id, approved, win_prob, comment, dir_, conf, regime, event_id):
    with state_lock:
        state["ai_history"] = [x for x in state["ai_history"] if parse_ts(x.get("time")) >= bot_start_time]
        state["ai_history"].append({"ts": utc_iso(),"trade_id": trade_id,"dir": dir_,"approved": approved,"win_prob": win_prob,"comment": comment,"event_id": event_id})
        if len(state["ai_history"]) > 5:
            state["ai_history"] = state["ai_history"][-5:]
        state["ai_decision"] = "APPROVED" if approved else "REJECTED"
        state["final_decision"] = "APPROVED" if approved else "AI_REJECTED"
        state["last_ai"].update({"win_prob": win_prob,"direction": dir_,"trade_id": trade_id,"comment": comment,"ai_error": None})
        state["last_engine_error"] = "" if approved else "AI rejected"

def close_replay_buffer(trade_id):
    with replay_lock:
        buf = replay_buffers.get(trade_id)
        if buf:
            buf["closed"] = True
            dump_replay(trade_id)
            replay_buffers.pop(trade_id, None)

def dump_replay(trade_id: str):
    global write_counter
    with replay_lock:
        buf = replay_buffers.get(trade_id)
        if not buf:
            return
        try:
            replay = {"schema": "signal_replay_v1","trade_id": trade_id,"start_ts": utc_iso(datetime.fromtimestamp(buf["start_ts"], timezone.utc)),"start_price": buf["start_price"],"ticks": list(buf["ticks"])}
            rotate_log(SIGNAL_REPLAY_FILE)
            with open(SIGNAL_REPLAY_FILE, 'a') as f:
                f.write(json.dumps(replay) + "\n")
                f.flush()
                write_counter += 1
                if write_counter % 10 == 0:
                    os.fsync(f.fileno())
        except Exception as e:
            logger.error(f"Replay dump failed for {trade_id}: {e}")

def load_policy():
    if not os.path.exists(POLICY_FILE):
        logger.info("No policy.json found - using defaults")
        return
    try:
        with open(POLICY_FILE, 'r') as f:
            policy = json.load(f)
        policy_hash = hashlib.sha256(json.dumps(policy.get("rules", {}), sort_keys=True).encode()).hexdigest()
        logger.info(f"Policy loaded: hash {policy_hash}")
        with state_lock:
            state["_policy"] = policy
    except Exception as e:
        logger.error(f"Policy load failed: {e} - using defaults")

def policy_allows_invert(regime: str) -> bool:
    with state_lock:
        p = state.get("_policy", {})
        rules = p.get("rules", {})
        if "invert_signal_only_in" in rules:
            return regime in rules["invert_signal_only_in"]
        return state.get("invert_signal", False)

def policy_allows_early_fail(strategy: str) -> bool:
    with state_lock:
        p = state.get("_policy", {})
        rules = p.get("rules", {})
        if "disable_early_fail_in" in rules:
            return strategy not in rules["disable_early_fail_in"]
        return state.get("early_fail_enabled", True)

def safe_event_put(event):
    try:
        if event_queue.qsize() > MAX_EVENT_QUEUE * 0.7:
            logger.warning("Queue pressure high - throttling signals")
            return
        try:
            event_queue.put(event, timeout=0.01)
        except Full:
            logger.error("[QUEUE] Overflow — dropping safely")
            try:
                event_queue.get_nowait()
                event_queue.put(event)
            except:
                pass
    except Exception as e:
        logger.error(f"safe_event_put failed: {e}")

def drain_stale_events():
    now = time.time()
    drained = 0
    temp = []
    while not event_queue.empty():
        ev = event_queue.get_nowait()
        if now - ev.get("ts", 0) <= 5:
            temp.append(ev)
        else:
            drained += 1
    for ev in temp:
        event_queue.put_nowait(ev)
    if drained > 0:
        logger.info(f"Drained {drained} stale events")

def reconcile_state():
    with state_lock:
        pending_orders[:] = [o for o in pending_orders if isinstance(o, dict) and o.get("created_ts", 0) > 0]
        expired_orders[:] = [o for o in expired_orders if isinstance(o, dict) and o.get("created_ts", 0) > 0]
        open_positions[:] = [p for p in open_positions if isinstance(p, dict) and p.get("entry", 0) > 0]
    return True

def update_analytics_summary():
    if len(trades) < MIN_ANALYTICS_TRADES:
        return
    wins = [t for t in trades if safe_float(t.get("net_pnl_usd", 0)) > 0]
    losses = [t for t in trades if safe_float(t.get("net_pnl_usd", 0)) < 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    with state_lock:
        state["analytics"] = {"win_rate": round(win_rate, 2),"total_trades": len(trades),"by_strategy": {},"by_regime": {},"exit_reasons": {}}
        state["analytics_ts"] = utc_iso()

def compute_analytics():
    with state_lock:
        trades_snapshot = copy.deepcopy(trades)
    if len(trades_snapshot) < MIN_ANALYTICS_TRADES:
        return
    analytics = {"total_trades": len(trades_snapshot),"wins": 0,"losses": 0,"by_strategy": {},"by_regime": {},"exit_reasons": {},"ai_bands": {}}
    for t in trades_snapshot:
        pnl = safe_float(t.get("net_pnl_usd", 0))
        if pnl > 0:
            analytics["wins"] += 1
        else:
            analytics["losses"] += 1
        reg = t.get("regime", "UNKNOWN")
        strat = t.get("strategy", "SR")
        exit_r = t.get("exit_reason", "UNKNOWN")
        analytics["by_regime"][reg] = analytics["by_regime"].get(reg, 0) + 1
        analytics["by_strategy"][strat] = analytics["by_strategy"].get(strat, 0) + 1
        analytics["exit_reasons"][exit_r] = analytics["exit_reasons"].get(exit_r, 0) + 1
        band = str(t.get("ai_band", "0-5"))
        if band not in analytics["ai_bands"]:
            analytics["ai_bands"][band] = {"trades": 0, "wins": 0, "pnl": 0.0}
        analytics["ai_bands"][band]["trades"] += 1
        analytics["ai_bands"][band]["pnl"] += pnl
        if pnl > 0:
            analytics["ai_bands"][band]["wins"] += 1
    with state_lock:
        state["analytics"] = analytics
        state["analytics_ts"] = utc_iso()

def analytics_loop():
    try:
        while not shutdown_event.is_set():
            try:
                compute_analytics()
            except Exception as e:
                logger.error(f"Analytics error: {e}")
            time.sleep(ANALYTICS_INTERVAL_SEC)
    except Exception as e:
        logger.exception("[CRITICAL] Analytics loop crash")
        set_execution_paused("THREAD_CRASH")

def offline_simulator(signal_snapshot_file=SIGNAL_SNAPSHOT_FILE, signal_replay_file=SIGNAL_REPLAY_FILE, output_file=COUNTERFACTUAL_FILE):
    global write_counter
    if not os.path.exists(signal_snapshot_file):
        return
    if not os.path.exists(signal_replay_file):
        return
    snapshots = []
    with open(signal_snapshot_file, 'r') as f:
        for line in f:
            snapshots.append(json.loads(line))
    replays = []
    with open(signal_replay_file, 'r') as f:
        for line in f:
            replays.append(json.loads(line))
    simulated_open = 0
    for snapshot in snapshots:
        trade_id = snapshot["trade_id"]
        if not snapshot.get("ai", {}).get("approved", False):
            continue
        replay = next((r for r in replays if r["trade_id"] == trade_id), None)
        if not replay:
            continue
        start_price = replay["start_price"]
        ticks = sorted(replay["ticks"], key=lambda x: x["seq"])
        sl_pct = snapshot["config"].get("sl_pct", SL_PCT)
        trail_ladder = snapshot["config"].get("trail_ladder", TRAIL_LADDER)
        leverage = snapshot["config"].get("leverage", 20)
        ttl_sec = REPLAY_TTL_SEC
        pullback_pct = snapshot["config"].get("pullback_threshold", 0.0)
        scenario = snapshot["direction"]
        entry = None
        fill_t = None
        for tick in ticks:
            price = tick["price"]
            t = tick["t"]
            if t > ttl_sec:
                break
            if scenario == "LONG":
                if price <= start_price * (1 - pullback_pct):
                    entry = price
                    fill_t = t
                    break
            else:
                if price >= start_price * (1 + pullback_pct):
                    entry = price
                    fill_t = t
                    break
        if entry is None or simulated_open >= state.get("max_active_signals", 3):
            continue
        simulated_open += 1
        dir_factor = 1 if scenario == "LONG" else -1
        sl = entry * (1 - dir_factor * sl_pct)
        current_sl = sl
        max_favorable = 0.0
        max_adverse = 0.0
        best_exit_price = entry
        best_pnl_pct = 0.0
        time_to_sl = None
        time_to_best = None
        effective_early_fail = snapshot["policy_effective"]["early_fail"]
        for tick in ticks:
            price = tick["price"]
            t = tick["t"]
            if t < fill_t:
                continue
            if t > ttl_sec:
                break
            unreal_pct = ((price - entry) / entry * 100) * dir_factor * leverage
            max_favorable = max(max_favorable, unreal_pct)
            max_adverse = min(max_adverse, unreal_pct)
            age_min = (t - fill_t) / 60
            if effective_early_fail and unreal_pct < EARLY_FAIL_PCT_THRESHOLD and age_min < EARLY_FAIL_MINUTES:
                best_exit_price = price
                best_pnl_pct = unreal_pct
                break
            best_lock = None
            for trigger_pct, lock_pct in trail_ladder:
                if unreal_pct >= trigger_pct:
                    best_lock = lock_pct
            if best_lock is not None:
                price_move_pct = best_lock / leverage
                if scenario == "LONG":
                    candidate = price * (1 - price_move_pct / 100)
                    current_sl = max(current_sl, candidate)
                else:
                    candidate = price * (1 + price_move_pct / 100)
                    current_sl = min(current_sl, candidate)
            if (scenario == "LONG" and price <= current_sl) or (scenario == "SHORT" and price >= current_sl):
                if time_to_sl is None:
                    time_to_sl = t
                break
            if unreal_pct > best_pnl_pct:
                best_pnl_pct = unreal_pct
                best_exit_price = price
                time_to_best = t
        simulated_open -= 1
        counterfactual = {"schema": "counterfactual_v1","trade_id": trade_id,"scenario": scenario,"fill_price": entry,"fill_time_sec": fill_t,"best_exit_price": best_exit_price,"best_pnl_pct": round(best_pnl_pct, 2),"max_adverse_pct": round(max_adverse, 2),"time_to_sl_sec": time_to_sl,"time_to_best_sec": time_to_best}
        try:
            rotate_log(COUNTERFACTUAL_FILE)
            with open(output_file, 'a') as f:
                f.write(json.dumps(counterfactual) + "\n")
                f.flush()
                global write_counter
                write_counter += 1
                if write_counter % 10 == 0:
                    os.fsync(f.fileno())
        except Exception as e:
            logger.error(f"Counterfactual log failed: {e}")
    logger.info("Offline simulation complete")

def preload_candles():
    global latest_candles, last_candle_ts
    preload_failed = True
    for attempt in range(5):
        try:
            candles = bybit_public.fetch_ohlcv(SYMBOL, '15m', limit=250)
            with state_lock:
                latest_candles[:] = candles[-250:]
                last_candle_ts = candles[-1][0] / 1000 if candles else time.time()
                state["ohlcv_ready"] = True
                state["last_data_ts"] = time.time()
            logger.info(f"Preloaded {len(latest_candles)} candles at startup")
            update_ema()
            update_support_resistance()
            trend_info()
            update_market_context(force=True)
            populate_candle_buffers_from_candles(latest_candles)
            logger.info(f"[PRELOAD INDICATORS + CANDLE BUFFERS] completed [PIPELINE ENFORCEMENT]")
            preload_failed = False
            break
        except Exception as e:
            logger.warning(f"[PRELOAD ATTEMPT {attempt+1}/5] {e}")
            time.sleep(2)
    if preload_failed:
        logger.critical("[STARTUP] PRELOAD FAILED — SYSTEM HALTED")
        set_execution_paused("PRELOAD_FAILED")
        raise RuntimeError("No candles available — cannot continue")

def rebuild_state_from_snapshots():
    if not os.path.exists(SIGNAL_SNAPSHOT_FILE):
        return
    restored = 0
    with open(SIGNAL_SNAPSHOT_FILE, "r") as f:
        lines = deque(f, maxlen=500)
    for line in reversed(lines):
        try:
            snap = json.loads(line)
            trade_id = snap["trade_id"]
            created_ts = datetime.fromisoformat(snap["ts"]).timestamp()
            if created_ts + SIGNAL_TTL_SEC < time.time():
                continue
            payload = {"trade_id": trade_id,"dir": snap["direction"],"regime": snap["market"]["regime"],"strategy": snap["market"]["strategy"],"created_ts": snap["ts"],"created_ts_ts": created_ts,"expires_ts": created_ts + SIGNAL_TTL_SEC,"signal_price": snap["price"],"pull_req": 0,"max_pull": 0}
            process_signal(payload)
            restored += 1
        except:
            continue
    logger.info(f"Recovered {restored} active signals from snapshot log")

def load_persistent_config():
    if os.path.exists(CONFIG_FILE):
        allowed_keys = ["pullback_threshold", "leverage","max_active_signals", "ai_enabled","early_fail_enabled", "invert_signal", "debug_enabled", "live_armed","account_balance", "daily_pnl_usd","min_confidence", "force_ai_every_signal","ai_threshold", "edge_threshold"]
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            with state_lock:
                for key in allowed_keys:
                    if key in config:
                        state[key] = config[key]
                if "_threshold_locked" in config:
                    state["_threshold_locked"] = config["_threshold_locked"]
        with state_lock:
            if float(state.get("ai_threshold", 60) or 60) < RESEARCH_AI_THRESHOLD_FLOOR:
                state["ai_threshold"] = RESEARCH_AI_THRESHOLD_FLOOR
                logger.info(
                    f"[CONFIG] Raised ai_threshold to research floor {RESEARCH_AI_THRESHOLD_FLOOR} "
                    f"[PIPELINE ENFORCEMENT]"
                )
            lev = int(state.get("leverage", DEFAULT_RESEARCH_LEVERAGE) or DEFAULT_RESEARCH_LEVERAGE)
            if lev > MAX_RESEARCH_LEVERAGE:
                state["leverage"] = MAX_RESEARCH_LEVERAGE
                logger.warning(
                    f"[CONFIG] Capped leverage {lev} -> {MAX_RESEARCH_LEVERAGE}x [PIPELINE ENFORCEMENT]"
                )
        logger.info("Loaded persistent config from config.json - ai_threshold restored")

def save_persistent_config():
    config = {k: state[k] for k in ["pullback_threshold", "leverage","max_active_signals", "ai_enabled","early_fail_enabled", "invert_signal", "debug_enabled", "live_armed","account_balance", "daily_pnl_usd","min_confidence", "force_ai_every_signal","ai_threshold","_threshold_locked","bootstrap_done", "edge_threshold"]}
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(config, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_FILE)
    logger.debug("Saved persistent config")

def rotate_log(file):
    if os.path.exists(file) and os.path.getsize(file) > 20 * 1024 * 1024:
        existing = sorted(glob.glob(file + ".*"))
        next_index = len(existing) + 1
        os.rename(file, f"{file}.{next_index}")

def run_flask():
    port = int(os.environ.get("PORT", "5000"))
    logger.info(f"[HTTP] Listening on 0.0.0.0:{port} (health=/health)")
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)

def is_ai_active():
    return state.get("ai_enabled", False) and bool(DEEPSEEK_API_KEY)

def ttl_monitor():
    logger.info("[TTL] Independent monitor started")
    while not shutdown_event.is_set():
        try:
            prune_signals()
            cleanup_expired_orders()
            time.sleep(30)
        except Exception as e:
            logger.error(f"[TTL] ERROR: {e}")
            time.sleep(5)

def system_health_check():
    with state_lock:
        now = time.time()
        price_ok = state.get("price") is not None and state["price"] > 0
        ws_ok = state.get("ws_last_tick") is not None and (now - state["ws_last_tick"] < STALE_HARD_SEC)
        ohlcv_ok = state.get("ohlcv_ready", False)
        ema_ok = all([state["ema_status"].get("ema9") is not None,state["ema_status"].get("ema21") is not None,state["ema_status"].get("ema200") is not None])
        candle_ok = (now - last_candle_ts < CANDLE_STALE_SEC) or (len(latest_candles) >= MIN_CANDLES)
        healthy = price_ok and ws_ok and ohlcv_ok and ema_ok and candle_ok
        if healthy:
            if state.get("last_ready_ts", 0) == 0:
                state["last_ready_ts"] = now
            elif now - state["last_ready_ts"] >= READY_STABLE_SEC:
                if not state.get("system_ready"):
                    logger.info(f"[SYSTEM READY] STABLE for {READY_STABLE_SEC}s -> system_ready=True")
                state["system_ready"] = True
                if state.get("execution_paused"):
                    set_execution_paused("")
        else:
            state["last_ready_ts"] = 0
            state["system_ready"] = False
            if not ws_ok and state.get("price_ts") is not None:
                state["execution_paused"] = True
                state["execution_reason"] = "STALE_DATA_HARD_STOP"
                logger.error("[HARD STOP] WS STALE - execution paused")
    if not healthy:
        logger.warning(f"[HEALTH] NOT READY ws={ws_ok} price={price_ok} candle={candle_ok}")
    else:
        logger.info("[HEALTH] OK")
    return healthy

def auto_recovery_check():
    if system_health_check():
        with state_lock:
            if state.get("execution_reason") == "THREAD_CRASH":
                logger.info("[RECOVERY][STATE_SYNC] SYSTEM HEALTH RESTORED -> RESUMING")
                set_execution_paused("")

def startup_hard_fix_ai_threshold():
    with state_lock:
        if "ai_threshold" not in state or not state.get("_threshold_locked"):
            logger.info("[INIT] AI threshold restored from config (no hard default)")

def recover_from_crash():
    if state.get("execution_reason") == "THREAD_CRASH":
        logger.warning("[RECOVERY] Attempting recovery from thread crash")
        set_execution_paused("")

def heartbeat_loop():
    global last_heartbeat
    while not shutdown_event.is_set():
        with state_lock:
            state["heartbeat"] = time.time()
        last_heartbeat = time.time()
        time.sleep(HEARTBEAT_INTERVAL)
        if not (state.get("ws_ready") and len(latest_candles) >= MIN_CANDLES):
            continue
        now = time.time()
        if now - state.get("last_ai_call_ts", 0) < AI_COOLDOWN_SECONDS:
            logger.debug(f"[HEARTBEAT] Skipped - AI cooldown active ({AI_COOLDOWN_SECONDS - (now - state.get('last_ai_call_ts', 0)):.0f}s left) [PIPELINE ENFORCEMENT]")
            continue
        logger.info("[HEARTBEAT] Periodic pipeline check (no direct AI call) [PIPELINE ENFORCEMENT]")
        event = detect_event_light()
        if event and event.get("event_trigger"):
            process_signal(event)

def ai_loop():
    pass

def engine_loop():
    global last_engine_run
    try:
        while not shutdown_event.is_set():
            try:
                start = time.time()
                if time.time() - bot_start_time < STARTUP_GRACE_PERIOD:
                    time.sleep(1)
                    continue
                if not should_run_pipeline():
                    time.sleep(1)
                    continue
                if not validate_market_data():
                    time.sleep(1)
                    continue
                if state.get("allow_rest_price"):
                    time.sleep(1)
                    continue
                if time.time() - last_engine_run < PIPELINE_INTERVAL:
                    time.sleep(0.5)
                    continue
                last_engine_run = time.time()
                validate_state()
                update_orders_state()
                monitor_positions()
                cleanup_expired_orders()
                prune_signals()
                update_analytics_summary()
                print_console_dashboard()
                try:
                    item = signal_queue.get_nowait()
                    process_signal(item["context"], item["ai"])
                except Empty:
                    pass
                while not event_queue.empty():
                    try:
                        ctx = event_queue.get_nowait()
                        process_signal(ctx)
                    except Empty:
                        break
                if time.time() - last_pipeline_run >= MIN_PIPELINE_INTERVAL:
                    logger.info("[ENGINE LOOP] PERIODIC PIPELINE TRIGGER [PIPELINE ENFORCEMENT]")
                    event = detect_event_light()
                    if event and event.get("event_trigger"):
                        process_signal(event)
            except Exception as e:
                logger.error(f"[ENGINE LOOP CRASH] {e}")
                state["last_engine_error"] = str(e)
                time.sleep(1)
            finally:
                with state_lock:
                    state["diag"]["engine_loop_ms"] = int((time.time() - start) * 1000)
                    state["debug_state"]["engine_last_run"] = utc_iso()
    except Exception as e:
        logger.exception("[CRITICAL] Engine loop fatal crash")
        set_execution_paused("THREAD_CRASH")

def tick_execution_engine():
    logger.info("[ENGINE] Tick execution engine started")
    while not shutdown_event.is_set():
        try:
            update_orders_state()
            manage_open_positions()
            cleanup_expired_orders()
            pipeline_state_sync()
            time.sleep(FAST_MONITOR_INTERVAL_SEC)
        except Exception as e:
            logger.error(f"[ENGINE ERROR] {e}")
            time.sleep(2)

def run_pipeline():
    logger.info("[PIPELINE STAGE][RAW CONTEXT] - DISABLED IN DUAL LOOP MODE")
    return

def run_with_restart(target, name):
    while not shutdown_event.is_set():
        try:
            target()
        except Exception as e:
            logger.error(f"[{name}] CRASH - restarting: {e}")
            set_execution_paused("THREAD_CRASH")
            time.sleep(2)

def print_console_dashboard():
    global last_console_update
    if time.time() - last_console_update < 10:
        return
    last_console_update = time.time()
    exposure = get_active_signal_count()
    with trade_lock:
        pending_pending = len([o for o in pending_orders if o.get("status") == "PENDING"])
        pending_all = len(pending_orders)
        pos_n = len(open_positions)
    _agent_dbg("H5", "print_console_dashboard", "snapshot", {"exposure": exposure, "pending_pending": pending_pending, "pending_all": pending_all, "positions": pos_n, "fix_version": EXECUTION_FIX_VERSION})
    logger.info(f"[DASHBOARD] Price={fmt(state.get('price'))} | Signals={exposure} | Pending={pending_all} | Positions={pos_n} | AI Calls={state.get('ai_call_count')} | Exec={get_execution_status()} | Edge={get_edge_threshold()} | Fix={EXECUTION_FIX_VERSION} [PIPELINE ENFORCEMENT]")

def apply_trade_pnl(trade_row):
    try:
        net = float(trade_row.get("net_pnl_usd") or trade_row.get("pnl_usd") or 0)
    except (TypeError, ValueError):
        net = 0.0
    with state_lock:
        state["daily_pnl_usd"] = round(state.get("daily_pnl_usd", 0.0) + net, 4)
        if net < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
        else:
            state["consecutive_losses"] = 0
        if state["consecutive_losses"] >= CONSECUTIVE_LOSS_PAUSE:
            state["loss_pause_until"] = time.time() + LOSS_PAUSE_SEC
            set_execution_paused("LOSS_STREAK")
            logger.warning(
                f"[RISK] {state['consecutive_losses']} consecutive losses — pausing {LOSS_PAUSE_SEC/3600:.0f}h "
                f"[PIPELINE ENFORCEMENT]"
            )
        if state.get("daily_pnl_usd", 0) <= -DAILY_DRAWDOWN_PAUSE_USD:
            set_execution_paused("DAILY_DRAWDOWN")
            logger.warning(
                f"[RISK] Daily PnL {state['daily_pnl_usd']:.2f} <= -{DAILY_DRAWDOWN_PAUSE_USD} — new trades paused "
                f"[PIPELINE ENFORCEMENT]"
            )

def main():
    global bot_start_time, last_signal_create_global, last_console_update, last_ai_call_ts, last_signal_process_ts, last_context_hash, last_signal_create_ts, test_signal_fired, prev_price, prev_delta, avg_volume, recent_high, recent_low, rejection_strength, last_signal_hash, last_ws_message_time, last_pipeline_run, last_heartbeat, last_edge_compute, _startup_complete
    logger.info(f"[AI INIT] KEY PRESENT: {bool(os.getenv('DEEPSEEK_API_KEY'))}")
    validate_startup()
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(0.8)
    logger.info("[STARTUP] HTTP server up — Railway /health available during bootstrap")
    logger.info("[STARTUP] WIPING OLD DATA FOR FRESH RUN")
    reset_all_csv()
    reset_runtime_state()
    update_logger_level()
    startup_hard_fix_ai_threshold()
    load_persistent_config()
    bot_start_time = time.time()
    last_signal_create_global = time.time() - 31
    state["last_ai_signal_time"] = 0
    last_ai_call_ts = 0.0
    last_signal_process_ts = 0.0
    last_trigger_ai_call = 0.0
    last_context_hash = None
    last_signal_create_ts = 0.0
    test_signal_fired = False
    prev_price = 0.0
    prev_delta = 0.0
    avg_volume = 0.0
    recent_high = 0.0
    recent_low = 0.0
    rejection_strength = 0.0
    last_signal_hash = None
    last_ws_message_time = time.time()
    last_pipeline_run = 0.0
    last_heartbeat = time.time()
    last_edge_compute = 0.0
    logger.info(f"[STARTUP] bot_start_time locked at {bot_start_time} - old data blocked")
    if not api_key or len(api_key) < 10 or not secret or len(secret) < 10:
        logger.error(
            "[STARTUP] BYBIT_API_KEY/BYBIT_SECRET missing or invalid — staying up for /health; trading paused"
        )
        set_execution_paused("MISSING_BYBIT_KEYS")
        state["account_balance"] = STARTING_BALANCE
    else:
        try:
            balance = bybit_private.fetch_balance()
            logger.info(f"API keys validated - Balance: {balance}")
            if state.get("live_armed"):
                state["account_balance"] = (
                    balance.get('total', {}).get('USDT', STARTING_BALANCE)
                    if isinstance(balance, dict)
                    else STARTING_BALANCE
                )
            else:
                state["account_balance"] = STARTING_BALANCE
        except Exception as e:
            logger.error(f"API key test failed: {e}")
            set_execution_paused("BYBIT_API_ERROR")
            state["account_balance"] = STARTING_BALANCE
    if not DEEPSEEK_API_KEY:
        logger.warning("AI disabled: DEEPSEEK_API_KEY missing")
    logger.info(f"[STARTUP] BALANCE SET TO {state['account_balance']}")
    load_policy()
    preload_candles()
    for _ in range(3):
        fetch_ohlcv()
        time.sleep(1)
    update_ema()
    update_support_resistance()
    trend_info()
    update_market_context(force=True)
    with state_lock:
        if len(latest_candles) >= MIN_CANDLES:
            logger.info("[BOOTSTRAP] Initial indicator computation completed after preload")
    with state_lock:
        closes = [c[4] for c in latest_candles]
        if len(closes) >= EMA_LONG:
            state["ema_status"] = {"ema9": ema(closes, EMA_FAST),"ema21": ema(closes, EMA_SLOW),"ema200": ema(closes, EMA_LONG),"prev_ema9": ema(closes, EMA_FAST),"prev_ema21": ema(closes, EMA_SLOW)}
            state["ohlcv_ready"] = True
    if LIVE_TRADING_ENABLED:
        set_execution_paused("SIMULATION_ONLY")
    load_positions()
    rebuild_state_from_snapshots()
    reconcile_stale_signals()
    boot_exposure = get_active_signal_count()
    m_fee, t_fee = get_trading_fee_rates()
    refresh_funding_state(force=True)
    with state_lock:
        f = state.get("funding", {})
    logger.info(
        f"[FEES] profile={EXCHANGE_FEE_PROFILE} maker={m_fee*100:.4f}% taker={t_fee*100:.4f}% | "
        f"funding_sim={FUNDING_SIMULATION_ENABLED} rate_8h={f.get('rate_pct_per_8h', 0)}% source={f.get('source')} [PIPELINE ENFORCEMENT]"
    )
    with state_lock:
        mc = state.get("market_context", {})
    logger.info(
        f"[PHASE-A] market_context loaded structure_score={mc.get('market_structure', {}).get('structure_score')} "
        f"mtf={mc.get('multi_tf', {}).get('trends')} adx={mc.get('trend_strength', {}).get('adx')} [PIPELINE ENFORCEMENT]"
    )
    logger.info(
        f"[PHASE-B] SR_FILTER_MODE={SR_FILTER_MODE} edge=rebalanced (structure/mtf primary, sr_context<=0.6) [PIPELINE ENFORCEMENT]"
    )
    logger.info(
        f"[PHASE-C] factor_gate={PHASE_C_FACTOR_GATE_ENABLED} min_margin={MIN_FACTOR_SCORE_MARGIN} [PIPELINE ENFORCEMENT]"
    )
    logger.info(f"[EXECUTION FIX {EXECUTION_FIX_VERSION}] startup exposure={boot_exposure} pending={len(pending_orders)} positions={len(open_positions)}")
    _agent_dbg("H1", "main.startup", "boot_complete", {"version": EXECUTION_FIX_VERSION, "exposure": boot_exposure, "pending": len(pending_orders), "positions": len(open_positions)})
    logger.info(f"Bot start time locked at {bot_start_time} - old trades blocked")
    _startup_complete = True
    fetch_ohlcv()
    if HEDGE_MODE:
        try:
            bybit_private.set_position_mode(True, SYMBOL)
            logger.info("Position mode already hedge or UTA handled")
        except ccxt.BaseError as e:
            if "110025" in str(e) or "not modified" in str(e).lower() or "unified account" in str(e).lower():
                logger.info("Position mode already hedge or UTA handled")
            else:
                logger.error(f"Hedge mode set failed: {e}")
    threading.Thread(target=safe_thread(start_websocket), daemon=True).start()
    threading.Thread(target=safe_thread(state_monitor_loop), daemon=True).start()
    threading.Thread(target=safe_thread(engine_loop), daemon=True).start()
    threading.Thread(target=safe_thread(tick_execution_engine), daemon=True).start()
    threading.Thread(target=safe_thread(ws_watchdog), daemon=True).start()
    threading.Thread(target=safe_thread(analytics_loop), daemon=True).start()
    threading.Thread(target=safe_thread(position_manager), daemon=True).start()
    threading.Thread(target=safe_thread(ttl_monitor), daemon=True).start()
    threading.Thread(target=safe_thread(heartbeat_loop), daemon=True).start()
    threading.Thread(target=safe_thread(watchdog_loop), daemon=True).start()
    update_logger_level()
    while True:
        try:
            recover_from_crash()
            safe_clear_pending()
            print_console_dashboard()
            if shutdown_event.wait(60):
                logger.info("Shutting down...")
                save_positions()
                save_persistent_config()
                with replay_lock:
                    for tid in list(replay_buffers.keys()):
                        dump_replay(tid)
                break
        except Exception as e:
            logger.critical(f"[FATAL] Restarting after crash: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"[FATAL STARTUP] Process staying alive for health/debug: {e}")
        logger.critical(traceback.format_exc())
        while not shutdown_event.is_set():
            time.sleep(60)