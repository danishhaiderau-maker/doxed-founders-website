# -*- coding: utf-8 -*-
"""
3-Factor Bitfinex 15m Bot - FINAL RESEARCH-GRADE VERSION v10.9.428
ENFORCED: WINDOW=10 + SINGLE AGGREGATED SOURCE + SOFT FEATURE VALIDATION + EDGE→AI ALIGN + FEATURE VALIDITY LOG-ONLY + PIPELINE LOCK + AVG_VOLUME FIXED + FULL TRACE + RESEARCH DATA COLLECTION + DIRECTION CONSISTENCY (final_direction SINGLE SOURCE OF TRUTH + IMMEDIATE INVERSION) + SINGLE FEATURE SNAPSHOT ENFORCEMENT + HARD AI BLOCK ON INCOMPLETE DATA + DELTA_CHANGE PERSISTENT + STRICT BUFFER GATE + ATOMIC SNAPSHOT + NO ZERO FALLBACKS IN AI
"""
from __future__ import annotations
import os
import time
import math
import threading
import logging
from logging.handlers import RotatingFileHandler
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
import subprocess
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
_AGENT_DEBUG_LOG = os.path.join(os.getenv("AGENT_DEBUG_LOG_DIR", "/tmp"), "agent-debug.log")
_AGENT_DEBUG_LOG_ALT = os.path.join(os.getenv("AGENT_DEBUG_LOG_DIR", "/tmp"), "agent-debug-alt.log")
AGENT_DEBUG_LOG_MAX_BYTES = int(os.getenv("AGENT_DEBUG_LOG_MAX_BYTES", str(20 * 1024 * 1024)))
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(50 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

class SafeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('delay', True)
        super().__init__(*args, **kwargs)
        self._rollover_failed = False

    def shouldRollover(self, record):
        if self._rollover_failed:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        if self._rollover_failed:
            return
        try:
            super().doRollover()
        except (PermissionError, OSError):
            self._rollover_failed = True

NEAR_EDGE_LOG_MAX_BYTES = int(os.getenv("NEAR_EDGE_LOG_MAX_BYTES", str(100 * 1024 * 1024)))
WATCHDOG_HEARTBEAT_STALE_SEC = float(os.getenv("WATCHDOG_HEARTBEAT_STALE_SEC", "45"))
WATCHDOG_WS_STALE_SEC = float(os.getenv("WATCHDOG_WS_STALE_SEC", "15"))
MOMENTUM_ALIGN_EPS = 1e-6
MOMENTUM_FLAT_MAX = 0.01
FLAT_MOMENTUM_EDGE_FLOOR = 4.8
FLAT_MOMENTUM_FLOOR_LOW_EDGE = 2.0
FLAT_MOMENTUM_FLOOR_HIGH_EDGE = 4.0
def get_weak_setup_min_edge() -> float:
    """Post-AI WEAK_SETUP floor; defaults to dashboard edge threshold (keeps UI and execution aligned)."""
    raw = (os.getenv("WEAK_SETUP_MIN_EDGE") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return get_edge_threshold()
_last_ws_trade_fp = None
_last_ws_trade_fp_ts = 0.0

def _agent_dbg(hypothesis_id, location, message, data=None, run_id="post-fix"):
    if os.getenv("DISABLE_AGENT_DEBUG_LOG", "").lower() in ("1", "true", "yes"):
        return
    try:
        payload = {"sessionId": "43f630", "runId": run_id, "hypothesisId": hypothesis_id, "location": location, "message": message, "data": data or {}, "timestamp": int(time.time() * 1000)}
        line = json.dumps(payload) + "\n"
        for path in (_AGENT_DEBUG_LOG, _AGENT_DEBUG_LOG_ALT):
            try:
                if os.path.exists(path) and os.path.getsize(path) > AGENT_DEBUG_LOG_MAX_BYTES:
                    continue
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

def _ascii_fold_csv_text(s: str) -> str:
    """Normalize common unicode in AI comments so Windows CSV writes stay safe."""
    repl = {
        "\u2192": "->", "\u2190": "<-", "\u2265": ">=", "\u2264": "<=",
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
    }
    out = s
    for src, dst in repl.items():
        out = out.replace(src, dst)
    return out


def safe_csv_row(row: dict) -> dict:
    clean = {}
    for k, v in row.items():
        if isinstance(v, float):
            clean[k] = round(v, 6) if not math.isnan(v) else ""
        elif v is None:
            clean[k] = ""
        elif isinstance(v, str):
            clean[k] = _ascii_fold_csv_text(v)
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

def _signed_momentum_components(features: dict) -> tuple:
    features = features or {}
    return (
        float(features.get("ret_1m") or 0),
        float(features.get("ret_5m") or 0),
        float(features.get("velocity") or 0),
    )

def _tape_bounce_against(direction: str, features: dict) -> bool:
    if not features or direction not in ("LONG", "SHORT"):
        return False
    r1, r5, vel = _signed_momentum_components(features)
    eps = MOMENTUM_ALIGN_EPS
    if direction == "SHORT":
        return r1 > eps and r5 >= -eps and vel >= -eps
    if direction == "LONG":
        return r1 < -eps and r5 <= eps and vel <= eps
    return False

def evaluate_momentum_alignment(direction: str, features: dict) -> tuple:
    if not features:
        return False, None
    if _tape_bounce_against(direction, features):
        return True, "MOMENTUM_BOUNCE_BLOCK"
    mom = _compute_momentum_metric(features)
    r1, r5, vel = _signed_momentum_components(features)
    eps = MOMENTUM_ALIGN_EPS
    if direction == "SHORT":
        if mom < MOMENTUM_FLAT_MAX and r1 > eps:
            return True, "MOMENTUM_FLAT_BOUNCE_SHORT"
        if r5 > eps and vel > eps:
            return True, "MOMENTUM_COUNTER_TREND_SHORT"
    elif direction == "LONG":
        if mom < MOMENTUM_FLAT_MAX and r1 < -eps:
            return True, "MOMENTUM_FLAT_BOUNCE_LONG"
        if r5 < -eps and vel < -eps:
            return True, "MOMENTUM_COUNTER_TREND_LONG"
    return False, None

def _trim_oversized_log(path: str, max_bytes: int):
    try:
        if max_bytes > 0 and os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.remove(path)
    except Exception:
        pass

def prune_aux_logs_on_startup():
    _trim_oversized_log("near_edge.log", NEAR_EDGE_LOG_MAX_BYTES)
    for path in (_AGENT_DEBUG_LOG, _AGENT_DEBUG_LOG_ALT):
        _trim_oversized_log(path, AGENT_DEBUG_LOG_MAX_BYTES)

RESEARCH_SESSION_FILE = "research_session.json"

def _wipe_csv_on_startup() -> bool:
    return os.getenv("WIPE_CSV_ON_STARTUP", "").strip().lower() in ("1", "true", "yes")

def _preserve_research_data() -> bool:
    return os.getenv("PRESERVE_RESEARCH_DATA", "").strip().lower() in ("1", "true", "yes")

def _load_research_session_meta() -> dict:
    try:
        if os.path.isfile(RESEARCH_SESSION_FILE):
            with open(RESEARCH_SESSION_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _write_research_session(start_ts: float):
    payload = {
        "bot_version": EXECUTION_FIX_VERSION,
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "bot_start_time": start_ts,
        "bot_start_iso": datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "cwd": os.getcwd(),
        "launcher": "15minu_bot.py",
    }
    with open(RESEARCH_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

def _should_wipe_research_on_startup() -> bool:
    if _preserve_research_data():
        return False
    if _wipe_csv_on_startup():
        return True
    prev = _load_research_session_meta()
    if prev.get("bot_version") and prev.get("bot_version") != EXECUTION_FIX_VERSION:
        return True
    return True  # fresh CSV/JSONL every bot start — set PRESERVE_RESEARCH_DATA=1 to keep history

def _wipe_research_on_startup_if_needed():
    if not _should_wipe_research_on_startup():
        logger.info("[STARTUP] PRESERVE_RESEARCH_DATA=1 — keeping existing research files [PIPELINE ENFORCEMENT]")
        return
    prev = _load_research_session_meta()
    reason = "version change" if prev.get("bot_version") and prev.get("bot_version") != EXECUTION_FIX_VERSION else "fresh session"
    logger.warning(f"[STARTUP] Wiping research files ({reason}) — only post-start data will be collected [PIPELINE ENFORCEMENT]")
    reset_all_research_files()

def _log_credential_sources():
    """Log where API keys come from — Railway must use Admin Control, not synced research defaults."""
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
    admin = os.getenv("CREDENTIALS_FROM", "").strip().lower() == "admin_control"
    ds_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    bx_ok = _private_api_keys_ok()
    ds_ok = bool(ds_key)
    ds_tail = ds_key[-4:] if len(ds_key) >= 4 else "none"
    bx_key = (os.getenv("BITFINEX_API_KEY") or "").strip()
    bx_tail = bx_key[-4:] if len(bx_key) >= 4 else "none"
    logger.info(
        f"[CREDENTIALS] railway={on_railway} admin_control={admin} "
        f"deepseek={'ok' if ds_ok else 'MISSING'}(…{ds_tail}) "
        f"bitfinex={'ok' if bx_ok else 'MISSING'}(…{bx_tail}) "
        f"[PIPELINE ENFORCEMENT]"
    )
    if on_railway and not ds_ok:
        logger.error(
            "[CREDENTIALS] Railway DEEPSEEK missing — save key at /admin/control and Push to Runtime "
            "(npm run push:showcase-bot) [PIPELINE ENFORCEMENT]"
        )
    if on_railway and not bx_ok:
        logger.error(
            "[CREDENTIALS] Railway Bitfinex keys missing — save at /admin/control and Push to Runtime "
            "[PIPELINE ENFORCEMENT]"
        )

def _private_api_keys_ok() -> bool:
    k = os.getenv("BITFINEX_API_KEY", "").strip()
    s = os.getenv("BITFINEX_API_SECRET", "").strip()
    return bool(k and len(k) >= 10 and s and len(s) >= 10)

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
    entry = order.get("fill_price") or order.get("limit_price") or order.get("entry") or signal.get("signal_price") or state.get("price")
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
        "funding_rate_at_entry": (state.get("funding") or {}).get("rate"),
        "funding_rate_pct_8h_at_entry": (state.get("funding") or {}).get("rate_pct_per_8h"),
        "funding_source_at_entry": (state.get("funding") or {}).get("source"),
        "bull_score_at_entry": signal.get("bull_score_at_entry") or (signal.get("ai_factors") or {}).get("bull_score", 0),
        "bear_score_at_entry": signal.get("bear_score_at_entry") or (signal.get("ai_factors") or {}).get("bear_score", 0),
        "ai_factors": copy.deepcopy(signal.get("ai_factors", {})),
        "entry_thesis": capture_entry_thesis(signal),
        "margin_usdt": float(signal.get("margin_usdt") or FIXED_MARGIN_USDT),
        "conviction_spread": signal.get("conviction_spread"),
        "spread_penalty_mult": signal.get("spread_penalty_mult", 1.0),
        "planned_limit_price": order.get("planned_limit_price"),
        "fill_tick_price": order.get("fill_price"),
        "trend_health_at_entry": copy.deepcopy(signal.get("trend_health_at_entry") or {}),
        "entry_mode": signal.get("entry_mode", ENTRY_MODE_PULLBACK),
        "ema_hybrid_base": signal.get("ema_hybrid_base"),
        "ema_hybrid_limit": signal.get("ema_hybrid_limit"),
        "ema_hybrid_offset_usd": signal.get("ema_hybrid_offset_usd"),
        "ema9_at_entry": signal.get("ema9_at_entry"),
        "ema21_at_entry": signal.get("ema21_at_entry"),
        "dist_to_ema_hybrid_pct": signal.get("dist_to_ema_hybrid_pct"),
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

def _utc_session_label(ts: float = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), timezone.utc)
    h = dt.hour
    if h < 8:
        return "ASIA"
    if h < 16:
        return "EU"
    return "US"


def _candle_15m_elapsed_pct(ts: float = None) -> float:
    now = float(ts or time.time())
    elapsed = now % CANDLE_INTERVAL_SEC
    return round(elapsed / CANDLE_INTERVAL_SEC, 4)


def _funding_bucket(funding: dict) -> str:
    rate = float((funding or {}).get("rate_pct_per_8h") or (funding or {}).get("rate") or 0)
    if rate > 0.01:
        return "HIGH_POS"
    if rate < -0.01:
        return "HIGH_NEG"
    if rate > 0.001:
        return "LOW_POS"
    if rate < -0.001:
        return "LOW_NEG"
    return "NEUTRAL"


def _volatility_bucket(vol_metric: float) -> str:
    v = abs(float(vol_metric or 0))
    if v >= 0.015:
        return "HIGH"
    if v >= 0.008:
        return "MED"
    if v >= 0.003:
        return "LOW"
    return "FLAT"


def _adx_bucket(adx: float) -> str:
    a = float(adx or 0)
    if a >= 30:
        return "STRONG"
    if a >= 25:
        return "TREND"
    if a >= 18:
        return "MODERATE"
    if a >= 12:
        return "WEAK"
    return "LOW"


def _research_session_bucket(ts: float = None) -> str:
    """ASIA / LONDON / OVERLAP / NEW_YORK for v80 segmented research."""
    h = datetime.fromtimestamp(ts or time.time(), timezone.utc).hour
    if h < 8:
        return "ASIA"
    if h < 13:
        return "LONDON"
    if h < 16:
        return "OVERLAP"
    if h < 22:
        return "NEW_YORK"
    return "ASIA"


def _edge_score_bucket(edge: float) -> str:
    e = float(edge or 0)
    if e < 1.0:
        return "0.5-1.0"
    if e < 1.5:
        return "1.0-1.5"
    if e < 2.0:
        return "1.5-2.0"
    if e < 2.5:
        return "2.0-2.5"
    if e < 3.0:
        return "2.5-3.0"
    if e < 3.5:
        return "3.0-3.5"
    if e < 4.0:
        return "3.5-4.0"
    return "4.0+"


def _spread_bucket(spread: int) -> str:
    s = int(spread or 0)
    if s <= 1:
        return "0-1"
    if s == 2:
        return "2"
    if s == 3:
        return "3"
    if s == 4:
        return "4"
    return "5+"


def _sr_location_bucket(sr_state: str) -> str:
    s = str(sr_state or "").upper()
    if "SUPPORT" in s:
        return "NEAR_SUPPORT"
    if "RESISTANCE" in s:
        return "NEAR_RESISTANCE"
    return "MID_RANGE"


def _ai_probability_bucket(prob: float) -> str:
    p = float(prob or 0)
    if p < 50:
        return "45-50"
    if p < 55:
        return "50-55"
    if p < 60:
        return "55-60"
    if p < 65:
        return "60-65"
    return "65+"


def _trade_mfe_type_label(mfe_pct: float) -> str:
    m = float(mfe_pct or 0)
    if m < 10:
        return "TYPE_A"
    if m >= 15:
        return "TYPE_B"
    return "MIXED"


def _compute_quality_components(
    edge: float, spread: int, structure_score, sr_state: str, adx: float, ai_prob: float,
) -> dict:
    """Research-only quality decomposition — not used for gating in v80."""
    edge_comp = min(1.0, max(0.0, float(edge or 0) / 4.0))
    spread_comp = min(1.0, max(0.0, float(spread or 0) / 5.0))
    struct = float(structure_score or 0)
    structure_comp = max(0.0, min(1.0, (struct + 2.0) / 4.0))
    loc = _sr_location_bucket(sr_state)
    location_comp = {"NEAR_SUPPORT": 0.85, "MID_RANGE": 0.55, "NEAR_RESISTANCE": 0.25}.get(loc, 0.5)
    adx_comp = min(1.0, max(0.0, float(adx or 0) / 30.0))
    ai_comp = min(1.0, max(0.0, float(ai_prob or 0) / 100.0))
    quality_score = round(
        0.20 * edge_comp + 0.30 * spread_comp + 0.20 * structure_comp
        + 0.15 * location_comp + 0.10 * adx_comp + 0.05 * ai_comp,
        4,
    )
    return {
        "edge_component": round(edge_comp, 4),
        "spread_component": round(spread_comp, 4),
        "structure_component": round(structure_comp, 4),
        "location_component": round(location_comp, 4),
        "adx_component": round(adx_comp, 4),
        "ai_component": round(ai_comp, 4),
        "quality_score": quality_score,
    }


def capture_research_buckets(signal: dict, ai: dict, edge_score: float, features: dict = None) -> dict:
    """Frozen bucket labels at APPROVE for v80 analyzer cohort reports."""
    features = features or signal.get("features") or {}
    direction = str(signal.get("final_direction") or ai.get("direction") or "LONG").upper()
    bull = int(ai.get("bull_score") or signal.get("bull_score_at_entry") or 0)
    bear = int(ai.get("bear_score") or signal.get("bear_score_at_entry") or 0)
    spread = bull - bear if direction == "LONG" else bear - bull
    sr_state = (
        features.get("sr_state")
        or (state.get("support_resistance") or {}).get("sr_state")
        or "UNKNOWN"
    )
    mc = signal.get("context", {}).get("market_context") or signal.get("market_context") or {}
    if not isinstance(mc, dict):
        mc = {}
    ms = mc.get("market_structure", {}) if isinstance(mc.get("market_structure"), dict) else {}
    ts_mc = mc.get("trend_strength", {}) if isinstance(mc.get("trend_strength"), dict) else {}
    adx = float(ts_mc.get("adx") or 0)
    ai_prob = float(ai.get("win_prob") or 0)
    edge_score = round(float(edge_score or 0), 2)
    now_ts = time.time()
    return {
        "edge_score_bucket": _edge_score_bucket(edge_score),
        "directional_spread_bucket": _spread_bucket(spread),
        "support_resistance_bucket": _sr_location_bucket(sr_state),
        "session_bucket": _research_session_bucket(now_ts),
        "ai_probability_bucket": _ai_probability_bucket(ai_prob),
        "directional_spread": spread,
        "edge_score": edge_score,
        "sr_state": sr_state,
        "structure_score": ms.get("structure_score"),
        "adx": adx,
        "ai_win_prob": ai_prob,
        "quality_components": _compute_quality_components(
            edge_score, spread, ms.get("structure_score"), sr_state, adx, ai_prob,
        ),
    }


def _research_session_context() -> dict:
    rc = state.setdefault("research_counters", {})
    return {
        "approve_index": int(rc.get("approve_index", 0)),
        "trades_since_last_loss": int(rc.get("trades_since_last_loss", 0)),
        "last_exit_reason": rc.get("last_exit_reason"),
        "last_trade_pnl_usd": rc.get("last_trade_pnl_usd"),
    }


def _bump_research_approve_index() -> int:
    rc = state.setdefault("research_counters", {})
    rc["approve_index"] = int(rc.get("approve_index", 0)) + 1
    return rc["approve_index"]


def _record_research_trade_close(net_pnl: float, exit_reason: str):
    rc = state.setdefault("research_counters", {})
    rc["last_exit_reason"] = exit_reason
    rc["last_trade_pnl_usd"] = round(float(net_pnl), 4)
    if net_pnl < 0:
        rc["trades_since_last_loss"] = 0
    else:
        rc["trades_since_last_loss"] = int(rc.get("trades_since_last_loss", 0)) + 1


def capture_entry_regime_snapshot(signal: dict, features: dict = None) -> dict:
    """Frozen regime/session context at APPROVE for segmented analyzer research."""
    features = features or signal.get("features") or {}
    ctx = signal.get("context") or {}
    mc = ctx.get("market_context") or signal.get("market_context") or state.get("market_context") or {}
    if not isinstance(mc, dict):
        mc = {}
    ts_mc = mc.get("trend_strength", {}) if isinstance(mc.get("trend_strength"), dict) else {}
    mtf = mc.get("multi_tf", {}) if isinstance(mc.get("multi_tf"), dict) else {}
    trends = mtf.get("trends") or {}
    sr = mc.get("sr_context", {}) if isinstance(mc.get("sr_context"), dict) else {}
    if not sr:
        sr_ctx = state.get("support_resistance") or {}
        sr = {
            "sr_state": sr_ctx.get("sr_state", "UNKNOWN"),
            "dist_to_resistance_pct": round(nz(sr_ctx.get("dist_to_resistance", 0)) * 100, 3),
            "dist_to_support_pct": round(nz(sr_ctx.get("dist_to_support", 0)) * 100, 3),
        }
    funding = copy.deepcopy(state.get("funding") or {})
    vol_metric = _compute_volatility_metric(features)
    adx = float(ts_mc.get("adx") or 0)
    now_ts = time.time()
    mtf_4h = str(trends.get("4h") or "").upper()
    return {
        "captured_ts": now_ts,
        "session_utc": _utc_session_label(now_ts),
        "candle_15m_elapsed_pct": _candle_15m_elapsed_pct(now_ts),
        "regime_label": signal.get("regime") or mc.get("regime_label") or state.get("regime", "UNKNOWN"),
        "strategy": signal.get("strategy") or state.get("strategy"),
        "sr_state": sr.get("sr_state", "UNKNOWN"),
        "dist_to_resistance_pct": sr.get("dist_to_resistance_pct"),
        "dist_to_support_pct": sr.get("dist_to_support_pct"),
        "adx": adx,
        "adx_bucket": _adx_bucket(adx),
        "volatility_metric": vol_metric,
        "volatility_bucket": _volatility_bucket(vol_metric),
        "mtf_4h": trends.get("4h"),
        "mtf_4h_ranging": mtf_4h in ("RANGING", "NEUTRAL", "MIXED", ""),
        "funding_rate": funding.get("rate"),
        "funding_rate_pct_8h": funding.get("rate_pct_per_8h"),
        "funding_source": funding.get("source"),
        "funding_bucket": _funding_bucket(funding),
        "session_context": _research_session_context(),
    }


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
    trends = mtf.get("trends") or {}
    return {
        "bull_score": int(signal.get("bull_score_at_entry") or 0),
        "bear_score": int(signal.get("bear_score_at_entry") or 0),
        "structure_score": ms.get("structure_score"),
        "mtf_agreement": mtf.get("agreement"),
        "mtf_15m": trends.get("15m"),
        "mtf_1h": trends.get("1h"),
        "mtf_4h": trends.get("4h"),
        "mtf_bull_tf_count": mtf.get("bull_tf_count"),
        "mtf_bear_tf_count": mtf.get("bear_tf_count"),
        "structure_bias": ms.get("structure_bias"),
        "adx": ts.get("adx"),
        "captured_ts": time.time(),
        "trend_health": copy.deepcopy(signal.get("trend_health_at_entry") or state.get("trend_health") or {}),
    }


def capture_entry_gate_snapshot(signal: dict, ai: dict, edge_score: float, features: dict = None) -> dict:
    """Frozen gate metrics at APPROVE for analyzer MTF/chop sweet-spot sweeps."""
    direction = str(signal.get("final_direction") or ai.get("direction") or "LONG").upper()
    ctx = signal.get("context") or {}
    features = features or signal.get("features") or {}
    mc = ctx.get("market_context") or signal.get("market_context") or {}
    if not isinstance(mc, dict):
        mc = {}
    mtf = mc.get("multi_tf", {}) if isinstance(mc.get("multi_tf"), dict) else {}
    trends = mtf.get("trends") or {}
    ts = mc.get("trend_strength", {}) if isinstance(mc.get("trend_strength"), dict) else {}
    adx = float(ts.get("adx") or 0)
    mom = round(_compute_momentum_metric(features), 4)
    agreement = mtf.get("agreement") or ""
    bull = int(ai.get("bull_score") or signal.get("bull_score_at_entry") or 0)
    bear = int(ai.get("bear_score") or signal.get("bear_score_at_entry") or 0)
    spread = bull - bear if direction == "LONG" else bear - bull
    edge_score = round(float(edge_score or 0), 2)
    adx_pass = adx >= ADX_BLOCK_NEW_ENTRY
    edge_pass = edge_score >= get_edge_threshold() - 1e-9
    spread_pass = spread >= 1
    edge_min = get_edge_threshold()
    chop_max = MOMENTUM_CHOP_BLOCK_ABOVE
    adx_min = ADX_BLOCK_NEW_ENTRY
    spread_min = 1
    if direction == "LONG":
        mtf_pass_strict = agreement == "BULL_ALIGNED"
    elif direction == "SHORT":
        mtf_pass_strict = agreement == "BEAR_ALIGNED"
    else:
        mtf_pass_strict = True
    mtf_pass = True if RESEARCH_FREE_RUN_DISABLE_MTF_GATE else mtf_pass_strict
    chop_pass_strict = mom <= chop_max + 1e-9
    chop_pass = True if RESEARCH_FREE_RUN_DISABLE_CHOP_GATE else chop_pass_strict
    return {
        "direction": direction,
        "gate_mode": "FREE_RUN" if (
            RESEARCH_FREE_RUN_DISABLE_MTF_GATE or RESEARCH_FREE_RUN_DISABLE_CHOP_GATE
        ) else "STRICT",
        "free_run": {
            "mtf_disabled": RESEARCH_FREE_RUN_DISABLE_MTF_GATE,
            "chop_disabled": RESEARCH_FREE_RUN_DISABLE_CHOP_GATE,
            "momentum_align_disabled": RESEARCH_FREE_RUN_DISABLE_MOMENTUM_ALIGN,
        },
        "would_pass_strict": {
            "mtf": mtf_pass_strict,
            "chop": chop_pass_strict,
            "adx": adx_pass,
            "edge": edge_pass,
            "spread": spread_pass,
            "live_stack": adx_pass and chop_pass_strict and mtf_pass_strict and edge_pass and spread_pass,
        },
        "mom_metric": mom,
        "adx": adx,
        "edge_score": edge_score,
        "directional_spread": spread,
        "margins": {
            "adx_margin": round(adx - adx_min, 4),
            "chop_margin": round(chop_max - mom, 4),
            "edge_margin": round(edge_score - edge_min, 4),
            "spread_margin": int(spread - spread_min),
            "mtf_pass": bool(mtf_pass),
            "mtf_agreement": agreement,
            "live_stack_margin": round(
                min(adx - adx_min, chop_max - mom, edge_score - edge_min, spread - spread_min), 4
            ),
        },
        "chop_components": {
            "ret_1m": features.get("ret_1m"),
            "ret_5m": features.get("ret_5m"),
            "velocity": features.get("velocity"),
            "volume_ratio": features.get("volume_ratio"),
            "imbalance": features.get("imbalance"),
        },
        "multi_tf": {
            "agreement": agreement,
            "trends": trends,
            "mtf_15m": trends.get("15m"),
            "mtf_1h": trends.get("1h"),
            "mtf_4h": trends.get("4h"),
            "bull_tf_count": mtf.get("bull_tf_count"),
            "bear_tf_count": mtf.get("bear_tf_count"),
            "interpretation_note": mtf.get("interpretation_note"),
        },
        "thresholds": {
            "adx_min": adx_min,
            "chop_max": chop_max,
            "edge_min": edge_min,
            "spread_min": spread_min,
            "long_mtf_required": "BULL_ALIGNED",
            "short_mtf_required": "BEAR_ALIGNED",
        },
        "would_pass": {
            "adx": adx_pass,
            "chop": chop_pass,
            "mtf": mtf_pass,
            "edge": edge_pass,
            "spread": spread_pass,
            "live_stack": adx_pass and chop_pass and mtf_pass and edge_pass and spread_pass,
            "strict_stack": adx_pass and chop_pass_strict and mtf_pass_strict and edge_pass and spread_pass,
        },
    }

def get_exit_config_snapshot() -> dict:
    """Active exit/thesis/ladder params — logged per trade for analyzer sweeps."""
    return {
        "trail_ladder": TRAIL_LADDER,
        "ladder_first_trigger_pct": TRAIL_LADDER[0][0],
        "ladder_first_lock_pct": TRAIL_LADDER[0][1],
        "peak_never_loser_min_peak": PEAK_NEVER_LOSER_MIN_PEAK,
        "peak_never_loser_floor": PEAK_NEVER_LOSER_FLOOR,
        "thesis_fast_exit_unreal_pct": THESIS_FAST_EXIT_UNREAL_PCT,
        "thesis_mfe_protect_pct": THESIS_MFE_PROTECT_PCT,
        "thesis_exit_if_above_unreal_pct": THESIS_EXIT_IF_ABOVE_UNREAL_PCT,
        "thesis_min_age_sec": THESIS_MIN_AGE_SEC,
        "thesis_score_flip_margin": THESIS_SCORE_FLIP_MARGIN,
        "thesis_early_decay_delta": THESIS_EARLY_DECAY_DELTA,
        "early_fail_pct_threshold": EARLY_FAIL_PCT_THRESHOLD,
        "type_a_stall_enabled": TYPE_A_STALL_ENABLED,
        "type_a_stall_min_age_sec": TYPE_A_STALL_MIN_AGE_SEC,
        "type_a_stall_min_candles": TYPE_A_STALL_MIN_CANDLES,
        "type_a_stall_max_mfe_pct": TYPE_A_STALL_MAX_MFE_PCT,
        "trend_weakening_enabled": TREND_WEAKENING_ENABLED,
        "spread_penalty_threshold": SPREAD_PENALTY_THRESHOLD,
        "spread_penalty_margin_mult": SPREAD_PENALTY_MARGIN_MULT,
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

def compute_trend_health(direction_hint: str = None) -> dict:
    """v83: BULL / BULL_WEAKENING / BEAR / BEAR_WEAKENING / MIXED from live scores + tape."""
    with state_lock:
        mc = copy.deepcopy(state.get("market_context") or {})
        fs = copy.deepcopy(state.get("feature_snapshot") or {})
        hist = list(state.get("trend_health_history") or [])
    bull, bear = compute_live_factor_scores(mc)
    ms = mc.get("market_structure", {}) or {}
    mtf = mc.get("multi_tf", {}) or {}
    struct_score = float(ms.get("structure_score") or 0)
    agreement = str(mtf.get("agreement") or "")
    vel = float(fs.get("velocity") or 0)
    vol_ratio = float(fs.get("volume_ratio") or 0)
    delta = float(fs.get("delta") or 0)
    direction_hint = str(direction_hint or "").upper()
    if agreement == "BULL_ALIGNED" or struct_score >= 2:
        base = "BULL"
    elif agreement == "BEAR_ALIGNED" or struct_score <= -2:
        base = "BEAR"
    else:
        base = "MIXED"
    if direction_hint == "LONG" and base == "MIXED":
        base = "BULL"
    elif direction_hint == "SHORT" and base == "MIXED":
        base = "BEAR"
    weaken = 0
    if base == "BULL":
        if vel < -MOMENTUM_ALIGN_EPS:
            weaken += 1
        if delta < TYPE_A_STALL_WEAK_DELTA:
            weaken += 1
        if vol_ratio < TYPE_A_STALL_WEAK_VOLUME_RATIO:
            weaken += 1
        if len(hist) >= 1:
            prev = hist[-1]
            if bull < int(prev.get("bull_score", bull)) - 1:
                weaken += 1
            if bear > int(prev.get("bear_score", bear)) + 1:
                weaken += 1
    elif base == "BEAR":
        if vel > MOMENTUM_ALIGN_EPS:
            weaken += 1
        if delta > -TYPE_A_STALL_WEAK_DELTA:
            weaken += 1
        if vol_ratio < TYPE_A_STALL_WEAK_VOLUME_RATIO:
            weaken += 1
        if len(hist) >= 1:
            prev = hist[-1]
            if bear < int(prev.get("bear_score", bear)) - 1:
                weaken += 1
            if bull > int(prev.get("bull_score", bull)) + 1:
                weaken += 1
    trend_state = base
    if TREND_WEAKENING_ENABLED and weaken >= TREND_WEAKENING_MIN_SIGNALS:
        if base == "BULL":
            trend_state = "BULL_WEAKENING"
        elif base == "BEAR":
            trend_state = "BEAR_WEAKENING"
    result = {
        "trend_state": trend_state,
        "base_state": base,
        "bull_score": bull,
        "bear_score": bear,
        "weaken_signals": weaken,
        "structure_score": struct_score,
        "mtf_agreement": agreement,
        "velocity": vel,
        "volume_ratio": vol_ratio,
        "delta": delta,
        "ts": time.time(),
    }
    with state_lock:
        state["trend_health"] = result
        hist.append(result)
        state["trend_health_history"] = hist[-TREND_HEALTH_HISTORY_MAX:]
    return result

def _effective_profit_lock_floor(pos: dict, peak_pct: float):
    floor = get_profit_lock_floor(peak_pct)
    if floor is None:
        return None
    tighten = 0.0
    spread = int(pos.get("conviction_spread") or 0)
    if spread_penalty_active(spread):
        tighten += SPREAD_PENALTY_LOCK_TIGHTEN_PCT
    health = state.get("trend_health") or {}
    ts = str(health.get("trend_state") or "")
    direction = str(pos.get("dir") or "").upper()
    if TREND_WEAKENING_ENABLED:
        if direction == "LONG" and ts == "BULL_WEAKENING":
            tighten += TREND_WEAKENING_LOCK_TIGHTEN_PCT
        elif direction == "SHORT" and ts == "BEAR_WEAKENING":
            tighten += TREND_WEAKENING_LOCK_TIGHTEN_PCT
    if tighten > 0:
        floor = min(float(peak_pct) - 0.5, float(floor) + tighten)
    return floor

def _type_a_stall_weak_tape(direction: str, fs: dict) -> bool:
    vol_ratio = float(fs.get("volume_ratio") or 0)
    delta = float(fs.get("delta") or 0)
    vel = float(fs.get("velocity") or 0)
    if vol_ratio >= TYPE_A_STALL_WEAK_VOLUME_RATIO:
        return False
    if direction == "LONG":
        return delta <= TYPE_A_STALL_WEAK_DELTA or vel < 0
    if direction == "SHORT":
        return delta >= -TYPE_A_STALL_WEAK_DELTA or vel > 0
    return False

def check_type_a_stall_exit(pos: dict, unreal_pct: float, now: float) -> bool:
    if not TYPE_A_STALL_ENABLED:
        return False
    peak = float(pos.get("max_pnl_pct") or 0)
    if peak >= TRAIL_LADDER[0][0]:
        return False
    entry_ts = float(pos.get("entry_ts") or 0)
    if entry_ts <= 0:
        return False
    age_sec = now - entry_ts
    if age_sec < TYPE_A_STALL_MIN_AGE_SEC:
        return False
    if peak >= TYPE_A_STALL_MAX_MFE_PCT:
        return False
    direction = str(pos.get("dir") or "").upper()
    fs = state.get("feature_snapshot") or {}
    if unreal_pct < 0:
        stall_reason = "unreal_negative"
    elif _type_a_stall_weak_tape(direction, fs):
        stall_reason = "weak_tape"
    else:
        return False
    cur_candle = int(age_sec // CANDLE_INTERVAL_SEC) + 1
    logger.info(
        f"[EXIT TRIGGER] TYPE_A_STALL trade_id={pos.get('trade_id')} reason={stall_reason} "
        f"age_min={age_sec/60:.1f} candle={cur_candle} peak={peak:.1f}% unreal={unreal_pct:.1f}% "
        f"vol_ratio={fs.get('volume_ratio')} delta={fs.get('delta')} vel={fs.get('velocity')} "
        f"[PIPELINE ENFORCEMENT]"
    )
    close_position(pos, "TYPE_A_STALL")
    return True

def check_trend_weakening_exit(pos: dict, unreal_pct: float, now: float) -> bool:
    if not TREND_WEAKENING_ENABLED:
        return False
    peak = float(pos.get("max_pnl_pct") or 0)
    if peak >= TRAIL_LADDER[0][0]:
        return False
    health = state.get("trend_health") or {}
    ts = str(health.get("trend_state") or "")
    direction = str(pos.get("dir") or "").upper()
    if direction == "LONG" and ts != "BULL_WEAKENING":
        return False
    if direction == "SHORT" and ts != "BEAR_WEAKENING":
        return False
    entry_ts = float(pos.get("entry_ts") or 0)
    cur_candle = int((now - entry_ts) // CANDLE_INTERVAL_SEC) + 1 if entry_ts > 0 else 0
    if cur_candle < 2:
        return False
    if unreal_pct > TREND_WEAKENING_SCRATCH_UNREAL_PCT:
        return False
    logger.info(
        f"[EXIT TRIGGER] TREND_WEAKENING trade_id={pos.get('trade_id')} state={ts} "
        f"peak={peak:.1f}% unreal={unreal_pct:.1f}% weaken={health.get('weaken_signals')} "
        f"[PIPELINE ENFORCEMENT]"
    )
    close_position(pos, "TREND_WEAKENING")
    return True

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
    if peak >= TRAIL_LADDER[0][0]:
        return False
    fast_cut = unreal_pct <= THESIS_FAST_EXIT_UNREAL_PCT
    if fast_cut:
        if THESIS_MFE_PROTECT_PCT > 0 and unreal_pct >= THESIS_MFE_PROTECT_PCT:
            logger.info(
                f"[THESIS_MFE_PROTECT] trade_id={pos.get('trade_id')} dir={pos.get('dir')} "
                f"skip fast cut unreal={unreal_pct:.1f}% peak={peak:.1f}% "
                f"floor={THESIS_MFE_PROTECT_PCT:.1f}% [PIPELINE ENFORCEMENT]"
            )
        else:
            logger.info(
                f"[THESIS_FAST_CUT] trade_id={pos.get('trade_id')} dir={pos.get('dir')} "
                f"unreal={unreal_pct:.1f}% peak={peak:.1f}% age={age_sec/60:.1f}m [PIPELINE ENFORCEMENT]"
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

def spread_penalty_margin_mult(spread: int) -> float:
    """v83: soft penalty for wide conviction spread (≥5) — margin scale, not hard block."""
    if not SPREAD_PENALTY_ENABLED:
        return 1.0
    if int(spread or 0) >= SPREAD_PENALTY_THRESHOLD:
        return SPREAD_PENALTY_MARGIN_MULT
    return 1.0

def spread_penalty_active(spread: int) -> bool:
    return SPREAD_PENALTY_ENABLED and int(spread or 0) >= SPREAD_PENALTY_THRESHOLD

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

def compute_reference_scaled_margin(direction: str, ai: dict, ctx: dict) -> dict:
    """Legacy risk-sized margin (pre-v79) — logged for analyzer size sweep, not used live when FLAT_MARGIN_EVERY_TRADE."""
    spread = compute_directional_spread(direction, ai)
    conv = conviction_size_multiplier(spread)
    mc = ctx.get("market_context") or {}
    adx = float((mc.get("trend_strength") or {}).get("adx") or 0)
    adx_mult = ADX_HALF_SIZE_MULT if adx < ADX_HALF_SIZE_BELOW else 1.0
    regime_prof = get_regime_risk_profile()
    scaled = round(FIXED_MARGIN_USDT * conv * adx_mult * regime_prof["size_mult"], 4)
    scaled = max(FIXED_MARGIN_USDT * 0.1, min(scaled, FIXED_MARGIN_USDT))
    return {
        "directional_spread": spread,
        "conviction_mult": conv,
        "adx_mult": adx_mult,
        "regime_mult": regime_prof.get("size_mult"),
        "regime_label": regime_prof.get("label"),
        "adx": adx,
        "reference_scaled_margin_usdt": scaled,
        "live_flat_margin_usdt": FIXED_MARGIN_USDT if FLAT_MARGIN_EVERY_TRADE else scaled,
    }


def resolve_entry_margin_usdt(direction: str, ai: dict, ctx: dict):
    spread = compute_directional_spread(direction, ai)
    conv = conviction_size_multiplier(spread)
    if conv <= 0:
        return None, f"CONVICTION_SPREAD_LOW_{spread}"
    mc = ctx.get("market_context") or {}
    adx = float((mc.get("trend_strength") or {}).get("adx") or 0)
    if golden_stack_enabled():
        if GOLDEN_STACK_ADX_BLOCK_LOW <= adx < GOLDEN_STACK_ADX_BLOCK_HIGH:
            return None, f"ADX_BAND_{adx:.1f}"
    elif adx < ADX_BLOCK_NEW_ENTRY:
        return None, f"ADX_NO_ENTRY_{adx:.1f}"
    penalty_mult = 1.0 if golden_stack_enabled() else spread_penalty_margin_mult(spread)
    if FLAT_MARGIN_EVERY_TRADE:
        margin = round(float(FIXED_MARGIN_USDT) * penalty_mult, 4)
        if spread_penalty_active(spread):
            return margin, None
        return float(FIXED_MARGIN_USDT), None
    adx_mult = ADX_HALF_SIZE_MULT if adx < ADX_HALF_SIZE_BELOW else 1.0
    regime_prof = get_regime_risk_profile()
    margin = round(FIXED_MARGIN_USDT * conv * adx_mult * regime_prof["size_mult"], 4)
    margin = max(FIXED_MARGIN_USDT * 0.1, min(margin, FIXED_MARGIN_USDT))
    return margin, None

def evaluate_entry_quality_filter(direction: str, ctx: dict, ai: dict, features: dict = None):
    mc = ctx.get("market_context") or {}
    mtf = (mc.get("multi_tf", {}) or {}).get("agreement", "")
    if not RESEARCH_FREE_RUN_DISABLE_MTF_GATE:
        if direction == "LONG" and mtf != "BULL_ALIGNED":
            return True, f"LONG_REQUIRES_BULL_MTF_{mtf or 'UNKNOWN'}"
        if direction == "SHORT":
            if mtf == "BULL_ALIGNED":
                return True, "SHORT_BLOCKED_BULL_MTF"
            if mtf not in ("BEAR_ALIGNED",):
                return True, f"SHORT_REQUIRES_BEAR_MTF_{mtf or 'UNKNOWN'}"
    features = features or ctx.get("features") or {}
    if not RESEARCH_FREE_RUN_DISABLE_MOMENTUM_ALIGN:
        blocked, reason = evaluate_momentum_alignment(direction, features)
        if blocked:
            return True, reason
    return False, None

def evaluate_evidence_entry_filter(
    direction: str, ctx: dict, ai: dict, features: dict, edge_score: float
) -> tuple:
    """v63 analyzer gates: momentum chop, SR zone, edge dead zone (both directions)."""
    sr_state = str(
        ctx.get("sr_state")
        or (ctx.get("support_resistance") or {}).get("sr_state")
        or state.get("support_resistance", {}).get("sr_state")
        or ""
    ).upper()
    if (
        direction in ("LONG", "SHORT")
        and state.get("block_free_range_entries", BLOCK_FREE_RANGE_ENTRIES)
        and sr_state == "FREE_RANGE"
    ):
        return True, f"{direction}_BLOCKED_FREE_RANGE"

    edge_score = round(float(edge_score or 0), 1)
    if not is_research_data_collection():
        if EDGE_DEAD_ZONE_LOW < edge_score <= EDGE_DEAD_ZONE_HIGH:
            return True, f"EDGE_DEAD_ZONE_{edge_score:.1f}"

    features = features or ctx.get("features") or {}
    if not RESEARCH_FREE_RUN_DISABLE_CHOP_GATE:
        mom = _compute_momentum_metric(features)
        if direction in ("LONG", "SHORT") and mom > MOMENTUM_CHOP_BLOCK_ABOVE:
            return True, f"{direction}_MOMENTUM_CHOP_{mom:.2f}"
    return False, None

def golden_stack_enabled() -> bool:
    with state_lock:
        return bool(state.get("golden_stack_enabled", GOLDEN_STACK_DEFAULT_ENABLED))

def capture_golden_stack_eval(signal: dict, ai: dict, edge_score: float, features: dict = None) -> dict:
    """Frozen v86 golden-stack check breakdown for analyzer replay."""
    direction = str(signal.get("final_direction") or ai.get("direction") or "").upper()
    ctx = signal.get("context") or {}
    features = features or signal.get("features") or {}
    mc = ctx.get("market_context") or state.get("market_context") or {}
    mtf = (mc.get("multi_tf") or {}) if isinstance(mc.get("multi_tf"), dict) else {}
    ts = (mc.get("trend_strength") or {}) if isinstance(mc.get("trend_strength"), dict) else {}
    ms = (mc.get("market_structure") or {}) if isinstance(mc.get("market_structure"), dict) else {}
    adx = float(ts.get("adx") or 0)
    mom = round(_compute_momentum_metric(features), 4)
    spread = int(compute_directional_spread(direction, ai))
    struct = float(ms.get("structure_score") or 0)
    agreement = str(mtf.get("agreement") or "")
    entry_mode = str(signal.get("entry_mode") or "")
    dist_pct = signal.get("dist_to_ema_hybrid_pct")
    regime = capture_entry_regime_snapshot(signal, features)
    funding_bucket = regime.get("funding_bucket") or _funding_bucket(state.get("funding") or {})
    checks = {
        "enabled": golden_stack_enabled(),
        "chop_max": GOLDEN_STACK_CHOP_MAX,
        "mom": mom,
        "chop_pass": mom <= GOLDEN_STACK_CHOP_MAX + 1e-9,
        "adx": adx,
        "adx_block_band": GOLDEN_STACK_ADX_BLOCK_LOW <= adx < GOLDEN_STACK_ADX_BLOCK_HIGH,
        "adx_pass": not (GOLDEN_STACK_ADX_BLOCK_LOW <= adx < GOLDEN_STACK_ADX_BLOCK_HIGH),
        "mtf_agreement": agreement,
        "mtf_pass": (direction != "SHORT") or agreement == "BEAR_ALIGNED",
        "structure_score": struct,
        "struct_pass": (direction != "SHORT") or struct <= GOLDEN_STACK_SHORT_STRUCT_MAX,
        "spread": spread,
        "spread_pass": GOLDEN_STACK_SPREAD_MIN <= spread <= GOLDEN_STACK_SPREAD_MAX,
        "entry_mode": entry_mode,
        "ema_hybrid_required": entry_mode == ENTRY_MODE_EMA_HYBRID,
        "dist_to_ema_hybrid_pct": dist_pct,
        "ema_dist_pass": dist_pct is None or float(dist_pct) <= GOLDEN_STACK_EMA_DIST_MAX_PCT,
        "ema_dist_ideal": dist_pct is not None and float(dist_pct) <= GOLDEN_STACK_EMA_DIST_IDEAL_MAX_PCT,
        "funding_bucket": funding_bucket,
        "funding_pass": funding_bucket in ("NEUTRAL", "LOW_NEG", "LOW_POS") or not GOLDEN_STACK_BLOCK_HIGH_POS_FUNDING,
        "session_utc": regime.get("session_utc"),
        "vol_bucket": regime.get("volatility_bucket"),
        "sr_state": regime.get("sr_state"),
    }
    checks["golden_stack_pass"] = all(
        checks[k] for k in (
            "chop_pass", "adx_pass", "mtf_pass", "struct_pass", "spread_pass",
            "ema_hybrid_required", "ema_dist_pass", "funding_pass",
        )
    )
    return checks

def evaluate_golden_stack_filter(
    direction: str, ctx: dict, ai: dict, features: dict, edge_score: float, signal: dict
) -> tuple:
    """v86 combined gate stack. Edge/AI thresholds intentionally excluded (dashboard)."""
    if not direction:
        return False, None
    features = features or {}
    mc = ctx.get("market_context") or state.get("market_context") or {}
    mtf = (mc.get("multi_tf") or {}) if isinstance(mc.get("multi_tf"), dict) else {}
    ts = (mc.get("trend_strength") or {}) if isinstance(mc.get("trend_strength"), dict) else {}
    ms = (mc.get("market_structure") or {}) if isinstance(mc.get("market_structure"), dict) else {}
    adx = float(ts.get("adx") or 0)
    mom = _compute_momentum_metric(features)
    if mom > GOLDEN_STACK_CHOP_MAX:
        return True, f"MOMENTUM_CHOP_{mom:.2f}"
    if GOLDEN_STACK_ADX_BLOCK_LOW <= adx < GOLDEN_STACK_ADX_BLOCK_HIGH:
        return True, f"ADX_BAND_{adx:.1f}"
    agreement = str(mtf.get("agreement") or "")
    if direction == "SHORT" and agreement != "BEAR_ALIGNED":
        return True, f"SHORT_REQUIRES_BEAR_MTF_{agreement or 'UNKNOWN'}"
    struct = float(ms.get("structure_score") or 0)
    if direction == "SHORT" and struct > GOLDEN_STACK_SHORT_STRUCT_MAX:
        return True, f"STRUCTURE_{struct:.1f}"
    spread = int(compute_directional_spread(direction, ai))
    if spread < GOLDEN_STACK_SPREAD_MIN:
        return True, f"SPREAD_LOW_{spread}"
    if spread > GOLDEN_STACK_SPREAD_MAX:
        return True, f"SPREAD_HIGH_{spread}"
    entry_mode = str(signal.get("entry_mode") or "")
    if entry_mode != ENTRY_MODE_EMA_HYBRID:
        return True, f"ENTRY_MODE_{entry_mode or 'UNKNOWN'}"
    dist_pct = signal.get("dist_to_ema_hybrid_pct")
    if dist_pct is not None and float(dist_pct) > GOLDEN_STACK_EMA_DIST_MAX_PCT:
        return True, f"EMA_DIST_{float(dist_pct):.3f}"
    if GOLDEN_STACK_BLOCK_HIGH_POS_FUNDING:
        funding_bucket = _funding_bucket(state.get("funding") or {})
        if funding_bucket == "HIGH_POS":
            return True, f"FUNDING_{funding_bucket}"
    return False, None

def _golden_stack_gate_exit(signal, ai, reason: str, edge_score: float) -> bool:
    """Return True if process_signal should return after a golden-stack gate hit."""
    trade_id = signal.get("trade_id")
    tag = f"GOLDEN_STACK_{reason}"
    if golden_stack_enabled():
        logger.info(f"[GOLDEN_STACK] BLOCK {reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
        log_blocked_signal(signal, ai, tag)
        log_pipeline_event("POST_AI", "BLOCKED", tag, trade_id, edge_score, force=True)
        exit_pipeline(signal, ai, tag)
        with state_lock:
            state["debug_state"]["last_block_reason"] = tag
            state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
            state["debug_state"]["skip_reason"] = tag
        update_debug_state_always(tag, {"edge": edge_score, "golden_stack": True})
        return True
    if _sole_ai_research_mode():
        _research_log_would_block(signal, ai, tag, edge_score)
        return False
    logger.info(f"[GOLDEN_STACK] BLOCK {reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
    log_blocked_signal(signal, ai, tag)
    exit_pipeline(signal, ai, tag)
    with state_lock:
        state["debug_state"]["last_block_reason"] = tag
        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
        state["debug_state"]["skip_reason"] = tag
    update_debug_state_always(tag, {"edge": edge_score, "golden_stack": True})
    return True

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

def _ws_trade_timestamp_sec(trade: dict) -> float:
    trade_ts_raw = trade.get("T")
    if not trade_ts_raw:
        return time.time()
    return float(trade_ts_raw) / 1000 if float(trade_ts_raw) > 1e12 else float(trade_ts_raw)

def _is_stale_ws_trade(trade: dict) -> bool:
    """Reject aged or pre-reconnect replay ticks that inflate MFE / false fills."""
    now = time.time()
    trade_ts = _ws_trade_timestamp_sec(trade)
    age = max(0.0, now - trade_ts)
    if age > WS_MAX_STALE_TRADE_SEC:
        return True
    ws_conn = float(state.get("ws_connected_ts") or 0)
    if ws_conn > 0 and (now - ws_conn) < WS_RECONNECT_STALE_GRACE_SEC and trade_ts < (ws_conn - 0.5):
        return True
    return False

def _position_monitor_interval_sec() -> float:
    trigger = TRAIL_LADDER[0][0]
    health = state.get("trend_health") or {}
    ts = str(health.get("trend_state") or "")
    with trade_lock:
        for pos in open_positions:
            if isinstance(pos, dict) and pos.get("status") == "OPEN":
                if float(pos.get("max_pnl_pct") or 0) >= trigger:
                    return LADDER_ARMED_MONITOR_INTERVAL_SEC
                direction = str(pos.get("dir") or "").upper()
                if direction == "LONG" and ts == "BULL_WEAKENING":
                    return LADDER_ARMED_MONITOR_INTERVAL_SEC
                if direction == "SHORT" and ts == "BEAR_WEAKENING":
                    return LADDER_ARMED_MONITOR_INTERVAL_SEC
    return POSITION_MONITOR_INTERVAL_SEC

def _log_ladder_exit_audit(pos: dict, price: float, unreal_pct: float, peak: float, lock_floor: float):
    if peak < TRAIL_LADDER[0][0] or lock_floor is None:
        return
    now = time.time()
    last_log = float(pos.get("_ladder_audit_ts") or 0)
    near_floor = unreal_pct <= (lock_floor + 3.0)
    crossed = pos.get("_prev_unreal_pct") is not None and float(pos["_prev_unreal_pct"]) > lock_floor >= unreal_pct
    if not near_floor and not crossed:
        return
    if not crossed and (now - last_log) < LADDER_AUDIT_LOG_INTERVAL_SEC:
        return
    pos["_ladder_audit_ts"] = now
    entry = float(pos.get("entry") or 0)
    logger.info(
        f"[LADDER AUDIT] trade_id={pos.get('trade_id')} entry={fmt(entry)} price={fmt(price)} "
        f"source={state.get('price_source', 'WS')} peak={peak:.2f}% lock={lock_floor:.2f}% "
        f"unreal={unreal_pct:.2f}% crossed={crossed} [PIPELINE ENFORCEMENT]"
    )

def _apply_position_exits(pos: dict, price: float, now: float = None):
    if now is None:
        now = time.time()
    unreal_pct = unrealized_margin_pct(pos, price)
    append_replay_tick(pos.get("trade_id"), price, unreal_pct)
    pos["max_pnl_pct"] = max(pos.get("max_pnl_pct", 0.0), unreal_pct)
    if pos.get("max_drawdown", 0) is None or unreal_pct < pos.get("max_drawdown", 0):
        pos["max_drawdown"] = min(pos.get("max_drawdown", 0.0), unreal_pct)
    entry_ts = float(pos.get("entry_ts") or 0)
    if entry_ts > 0:
        cur_candle = int((now - entry_ts) // CANDLE_INTERVAL_SEC) + 1
        prev_candle = int(pos.get("_candle_track_idx") or 0)
        if cur_candle > prev_candle and cur_candle <= 3:
            pos.setdefault("first_3_candles", {})[str(cur_candle)] = {
                "mfe_pct": round(float(pos.get("max_pnl_pct") or 0), 2),
                "mae_pct": round(float(pos.get("max_drawdown") or 0), 2),
                "unreal_pct": round(float(unreal_pct), 2),
            }
            pos["_candle_track_idx"] = cur_candle

    if state.get("early_fail_enabled", True) and unreal_pct <= EARLY_FAIL_PCT_THRESHOLD:
        logger.info(f"[EXIT TRIGGER] EARLY_FAIL trade_id={pos.get('trade_id')} pnl={fmt(unreal_pct)} [PIPELINE ENFORCEMENT]")
        close_position(pos, "EARLY_FAIL")
        return True

    if (pos.get("dir") == "LONG" and price <= pos.get("sl", 0)) or (pos.get("dir") == "SHORT" and price >= pos.get("sl", 0)):
        logger.info(f"[EXIT TRIGGER] STOP_LOSS trade_id={pos.get('trade_id')} [PIPELINE ENFORCEMENT]")
        close_position(pos, "STOP_LOSS")
        return True

    if check_type_a_stall_exit(pos, unreal_pct, now):
        return True

    if check_trend_weakening_exit(pos, unreal_pct, now):
        return True

    peak = pos.get("max_pnl_pct", 0.0)
    lock_floor = _effective_profit_lock_floor(pos, peak)
    _log_ladder_exit_audit(pos, price, unreal_pct, peak, lock_floor)
    if lock_floor is not None and peak >= TRAIL_LADDER[0][0] and unreal_pct <= lock_floor:
        entry = float(pos.get("entry") or 0)
        logger.info(
            f"[EXIT TRIGGER] PROFIT_LOCK_LADDER trade_id={pos.get('trade_id')} peak={peak:.1f}% "
            f"lock={lock_floor:.1f}% now={unreal_pct:.1f}% entry={fmt(entry)} exit={fmt(price)} "
            f"source={state.get('price_source', 'WS')} [PIPELINE ENFORCEMENT]"
        )
        close_position(pos, "PROFIT_LOCK_LADDER")
        return True
    pos["_prev_unreal_pct"] = unreal_pct

    if check_thesis_invalidation(pos, price):
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
        # RESEARCH balance showcase [PIPELINE ENFORCEMENT]
        if state.get("strategy_mode") == "RESEARCH":
            return round(float(state.get("account_balance", STARTING_BALANCE)), 4)
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
        ag = state["debug_state"].get("ai_gate") or {}
        ai_was_called = bool(ag.get("called"))
        if ai_rem <= 0 and sig_rem <= 0:
            sr = state["debug_state"].get("skip_reason")
            if sr in ("AI_COOLDOWN_ACTIVE", "GLOBAL_COOLDOWN", None):
                state["debug_state"]["skip_reason"] = None
            elif sr and not ai_was_called and _is_misleading_ai_skip_label(sr):
                state["debug_state"]["skip_reason"] = None
                state["debug_state"]["last_block_reason"] = None
            elif sr and str(sr).startswith("AI_") and state.get("last_pipeline_stage") == "IDLE":
                state["debug_state"]["skip_reason"] = None
                state["debug_state"]["last_block_reason"] = None
            if not ai_was_called and state.get("last_pipeline_stage") == "IDLE":
                if state.get("final_decision") == "AI_REJECTED":
                    state["final_decision"] = None
                if state.get("ai_decision") == "REJECTED":
                    state["ai_decision"] = None
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

DEBUG_LOG_BUFFER = deque(maxlen=5000)

def store_debug_snapshot(stage: str, data: dict):
    entry = {"ts": utc_iso(), "stage": stage, "data": data}
    if isinstance(data, dict):
        ai = data.get("ai") or {}
        if ai.get("ai_error") or ai.get("decision") == "AI_ERROR":
            entry["ai_error"] = True
            entry["error_type"] = ai.get("error_type")
            entry["error_detail"] = (ai.get("error_detail") or ai.get("comment") or "")[:500]
        if data.get("edge"):
            entry["edge_score"] = (data.get("edge") or {}).get("score")
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
# v79: always deploy full $20 margin — conviction/regime/ADX scaling disabled (logged for analyzer)
FLAT_MARGIN_EVERY_TRADE = True
MAX_SL_MARGIN_PCT = 30.0

def sl_price_pct(leverage: int = None) -> float:
    lev = max(int(leverage or state.get("leverage", 20) or 20), 1)
    return MAX_SL_MARGIN_PCT / (lev * 100.0)

SL_PCT = sl_price_pct(20)
# Fee profile: BITFINEX_ZERO = 0% maker/taker (Bitfinex default since Dec 2025 on spot/margin/derivatives).
# BYBIT_DEFAULT = legacy research comparison only (~0.02% maker / 0.06% taker on notional).
_EXCHANGE_FEE_RAW = os.getenv("EXCHANGE_FEE_PROFILE", "BITFINEX_ZERO").strip().upper()
EXCHANGE_FEE_PROFILE = _EXCHANGE_FEE_RAW if _EXCHANGE_FEE_RAW in ("BITFINEX_ZERO", "BYBIT_DEFAULT") else "BITFINEX_ZERO"
_BYBIT_MAKER_FEE_PCT = 0.0002
_BYBIT_TAKER_FEE_PCT = 0.0006

def get_trading_fee_rates():
    if EXCHANGE_FEE_PROFILE == "BITFINEX_ZERO":
        return 0.0, 0.0
    return _BYBIT_MAKER_FEE_PCT, _BYBIT_TAKER_FEE_PCT

MAKER_FEE_PCT, TAKER_FEE_PCT = get_trading_fee_rates()
# Bitfinex BTC USDt perpetual — market data + sim; live funding from /v2/status/deriv.
FUNDING_SIMULATION_ENABLED = True
# Bitfinex deriv status row indices (https://docs.bitfinex.com/reference/rest-public-derivatives-status)
_DERIV_STATUS_DERIV_PRICE = 3
_DERIV_STATUS_SPOT_PRICE = 4
_DERIV_STATUS_NEXT_FUNDING_MTS = 8
_DERIV_STATUS_NEXT_FUNDING_ACCRUED = 9
_DERIV_STATUS_NEXT_FUNDING_STEP = 10
_DERIV_STATUS_CURRENT_FUNDING = 12
_DERIV_STATUS_MARK_PRICE = 15
_DERIV_STATUS_OPEN_INTEREST = 18
_DERIV_STATUS_CLAMP_MIN = 22
_DERIV_STATUS_CLAMP_MAX = 23
BITFINEX_REST_BASE = "https://api-pub.bitfinex.com/v2"
BITFINEX_WS_URL = "wss://api-pub.bitfinex.com/ws/2"
BITFINEX_WS_SYMBOL = "tBTCF0:USTF0"
SYMBOL = BITFINEX_WS_SYMBOL
BOT_EXCHANGE = "bitfinex"
# Shared with analyzer_research_engine_v62.py — bump both when bot/analyzer contract changes.
ANALYZER_SYNC_ID = "v8.6-golden-stack-gates-2026-06-10"
SYMBOL_CCXT = "BTC/USDT:USDT"
FUNDING_INTERVAL_HOURS = 8
FUNDING_REFRESH_SEC = 60
FUNDING_RATE_CAP_PER_8H = 0.001
_last_funding_refresh_ts = 0.0
bitfinex_public = None
bitfinex_private = None

def get_bitfinex_public():
    if bitfinex_public is None:
        raise RuntimeError("Bitfinex public client not initialized")
    return bitfinex_public

def fetch_bitfinex_ohlcv(timeframe: str = "15m", limit: int = 250) -> list:
    """Bitfinex REST candles (ccxt OHLCV for perps can return stale rows)."""
    key = f"trade:{timeframe}:{BITFINEX_WS_SYMBOL}"
    url = f"{BITFINEX_REST_BASE}/candles/{key}/hist"
    resp = requests.get(url, params={"limit": limit}, timeout=30)
    resp.raise_for_status()
    rows = resp.json() or []
    candles = []
    for row in reversed(rows):
        ts, o, c, h, l, v = row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        candles.append([ts, o, h, l, c, v])
    return candles

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

def _effective_funding_rate_8h(current_funding: float, next_accrued: float) -> float:
    """Bitfinex: CURRENT_FUNDING is the live 8h rate; if zero, use next-period accrual."""
    cur = float(current_funding or 0.0)
    if abs(cur) >= 1e-12:
        return max(-FUNDING_RATE_CAP_PER_8H, min(FUNDING_RATE_CAP_PER_8H, cur))
    acc = float(next_accrued or 0.0)
    if abs(acc) >= 1e-12:
        return max(-FUNDING_RATE_CAP_PER_8H, min(FUNDING_RATE_CAP_PER_8H, acc))
    return 0.0

def fetch_bitfinex_deriv_funding_rest(symbol: str = BITFINEX_WS_SYMBOL) -> dict:
    """Live perp funding from Bitfinex GET /v2/status/deriv?keys=... (public, no API key)."""
    url = f"{BITFINEX_REST_BASE}/status/deriv"
    resp = requests.get(url, params={"keys": symbol}, timeout=12)
    resp.raise_for_status()
    payload = resp.json()
    if not payload or not isinstance(payload, list):
        raise ValueError("empty deriv status response")
    row = payload[0]
    if not isinstance(row, (list, tuple)) or len(row) < 13:
        raise ValueError(f"unexpected deriv status row len={len(row) if isinstance(row, (list, tuple)) else 'n/a'}")
    current = row[_DERIV_STATUS_CURRENT_FUNDING]
    next_accrued = row[_DERIV_STATUS_NEXT_FUNDING_ACCRUED]
    rate = _effective_funding_rate_8h(current, next_accrued)
    next_ms = row[_DERIV_STATUS_NEXT_FUNDING_MTS]
    next_ts = float(next_ms) / 1000.0 if next_ms else None
    mark = row[_DERIV_STATUS_MARK_PRICE] if len(row) > _DERIV_STATUS_MARK_PRICE else None
    spot = row[_DERIV_STATUS_SPOT_PRICE] if len(row) > _DERIV_STATUS_SPOT_PRICE else None
    oi = row[_DERIV_STATUS_OPEN_INTEREST] if len(row) > _DERIV_STATUS_OPEN_INTEREST else None
    return {
        "rate": rate,
        "current_funding": float(current or 0.0),
        "next_funding_accrued": float(next_accrued or 0.0),
        "next_funding_step": int(row[_DERIV_STATUS_NEXT_FUNDING_STEP] or 0),
        "next_time": next_ts,
        "mark_price": float(mark) if mark is not None else None,
        "index_price": float(spot) if spot is not None else None,
        "open_interest": float(oi) if oi is not None else None,
        "clamp_min": float(row[_DERIV_STATUS_CLAMP_MIN]) if len(row) > _DERIV_STATUS_CLAMP_MIN and row[_DERIV_STATUS_CLAMP_MIN] is not None else None,
        "clamp_max": float(row[_DERIV_STATUS_CLAMP_MAX]) if len(row) > _DERIV_STATUS_CLAMP_MAX and row[_DERIV_STATUS_CLAMP_MAX] is not None else None,
        "source": "BITFINEX_DERIV_STATUS",
    }

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
    meta = {}
    try:
        meta = fetch_bitfinex_deriv_funding_rest(BITFINEX_WS_SYMBOL)
        rate = meta.get("rate")
        next_ts = meta.get("next_time")
        mark = meta.get("mark_price")
        index = meta.get("index_price")
        source = meta.get("source", "BITFINEX_DERIV_STATUS")
    except Exception as e:
        logger.warning(f"[FUNDING] Bitfinex deriv status failed: {e} — trying ccxt [PIPELINE ENFORCEMENT]")
        try:
            fr = get_bitfinex_public().fetch_funding_rate(SYMBOL_CCXT)
            rate = fr.get("fundingRate")
            if rate is None:
                rate = fr.get("nextFundingRate")
            next_ms = fr.get("nextFundingTimestamp") or fr.get("fundingTimestamp")
            if next_ms:
                next_ts = float(next_ms) / 1000.0
            mark = fr.get("markPrice")
            index = fr.get("indexPrice")
            source = "BITFINEX_CCXT"
        except Exception as e2:
            logger.warning(f"[FUNDING] ccxt funding failed: {e2} — using synthetic [PIPELINE ENFORCEMENT]")
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
            "next_time_iso": datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat(),
            "mark_price": mark,
            "index_price": index,
            "interval_hours": FUNDING_INTERVAL_HOURS,
            "source": source,
            "longs_pay": rate > 0,
            "shorts_receive_when_positive": rate > 0,
            "updated_ts": now,
            "current_funding": meta.get("current_funding"),
            "next_funding_accrued": meta.get("next_funding_accrued"),
            "next_funding_step": meta.get("next_funding_step"),
            "open_interest": meta.get("open_interest"),
            "clamp_min": meta.get("clamp_min"),
            "clamp_max": meta.get("clamp_max"),
            "symbol": BITFINEX_WS_SYMBOL,
        }
    _last_funding_refresh_ts = now
    logger.info(
        f"[FUNDING] {source} rate_8h={rate*100:.5f}% current={meta.get('current_funding')} "
        f"next_accrued={meta.get('next_funding_accrued')} next_settlement="
        f"{datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat()} longs_pay={rate > 0} "
        f"[PIPELINE ENFORCEMENT]"
    )
    if rate > 0:
        with trade_lock:
            short_n = sum(
                1 for p in open_positions
                if p.get("dir") == "SHORT" and p.get("status") == "OPEN"
            )
        if short_n:
            logger.info(
                f"[FUNDING MONITOR] positive rate with {short_n} open SHORT(s) — favorable carry (monitor only) "
                f"[PIPELINE ENFORCEMENT]"
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

def funding_side_label(rate_8h: float, direction: str) -> str:
    if abs(float(rate_8h or 0)) < 1e-12:
        return "NEUTRAL"
    if direction == "LONG":
        return "LONG_PAYS" if rate_8h > 0 else "LONG_RECEIVES"
    return "SHORT_RECEIVES" if rate_8h > 0 else "SHORT_PAYS"

def projected_funding_to_next_settlement(pos: dict, funding: dict = None) -> float:
    """USD funding until next Bitfinex settlement (uses live rate from state)."""
    if funding is None:
        with state_lock:
            funding = copy.deepcopy(state.get("funding") or {})
    next_ts = float(funding.get("next_time") or 0)
    if next_ts <= 0:
        return 0.0
    hours = max(0.0, (next_ts - time.time()) / 3600.0)
    return funding_cost_for_position(pos, float(funding.get("rate") or 0.0), hours)

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

def _entry_ema9_ema21() -> tuple:
    with state_lock:
        es = state.get("ema_status") or {}
    return float(es.get("ema9") or 0), float(es.get("ema21") or 0)

def compute_ema_hybrid_base(direction: str, price: float, ema9: float, ema21: float):
    """LONG: max(ema9, ema21) below price. SHORT: min(ema9, ema21) above price."""
    if not price or price <= 0:
        return None
    direction = str(direction or "").upper()
    if direction == "LONG":
        cands = [e for e in (ema9, ema21) if e > 0 and e < price]
        return max(cands) if cands else None
    if direction == "SHORT":
        cands = [e for e in (ema9, ema21) if e > 0 and e > price]
        return min(cands) if cands else None
    return None

def apply_ema_hybrid_entry_offset(direction: str, hybrid_base: float) -> float:
    """LONG: +$offset (higher bid, closer fill). SHORT: -$offset (lower offer, closer fill)."""
    off = float(EMA_HYBRID_ENTRY_OFFSET_USD)
    direction = str(direction or "").upper()
    if direction == "LONG":
        return float(hybrid_base) + off
    if direction == "SHORT":
        return float(hybrid_base) - off
    return float(hybrid_base)

def compute_ema_hybrid_entry(signal: dict) -> dict:
    """v84.1: EMA9/EMA21 hybrid limit with fixed USD offset (no pivot micro-SR)."""
    direction = str(signal.get("final_direction") or "").upper()
    price = float(signal.get("signal_price") or state.get("price") or 0)
    ema9, ema21 = _entry_ema9_ema21()
    hybrid_base = compute_ema_hybrid_base(direction, price, ema9, ema21)
    limit_price = None
    if hybrid_base is not None:
        limit_price = apply_ema_hybrid_entry_offset(direction, hybrid_base)
        if direction == "SHORT" and limit_price <= price:
            hybrid_base = None
            limit_price = None
        elif direction == "LONG" and limit_price >= price:
            hybrid_base = None
            limit_price = None
    entry_mode = ENTRY_MODE_EMA_HYBRID if hybrid_base is not None else ENTRY_MODE_PULLBACK
    dist_pct = (
        round(abs(price - limit_price) / price * 100, 4)
        if limit_price is not None and price > 0
        else None
    )
    signal["entry_mode"] = entry_mode
    signal["ema_hybrid_base"] = hybrid_base
    signal["ema_hybrid_limit"] = limit_price
    signal["ema_hybrid_offset_usd"] = EMA_HYBRID_ENTRY_OFFSET_USD
    signal["ema9_at_entry"] = ema9
    signal["ema21_at_entry"] = ema21
    signal["dist_to_ema_hybrid_pct"] = dist_pct
    trade_id = signal.get("trade_id")
    if entry_mode == ENTRY_MODE_EMA_HYBRID:
        logger.info(
            f"[EMA HYBRID] trade_id={trade_id} dir={direction} signal={fmt(price)} "
            f"ema9={fmt(ema9)} ema21={fmt(ema21)} base={fmt(hybrid_base)} "
            f"offset_usd={EMA_HYBRID_ENTRY_OFFSET_USD} limit={fmt(limit_price)} "
            f"dist={dist_pct}% [PIPELINE ENFORCEMENT]"
        )
    else:
        logger.info(
            f"[EMA HYBRID] trade_id={trade_id} dir={direction} no_valid_ema_level "
            f"ema9={fmt(ema9)} ema21={fmt(ema21)} fallback={ENTRY_MODE_PULLBACK} "
            f"[PIPELINE ENFORCEMENT]"
        )
    return {
        "entry_mode": entry_mode,
        "ema_hybrid_base": hybrid_base,
        "ema_hybrid_limit": limit_price,
        "dist_to_ema_hybrid_pct": dist_pct,
    }

compute_micro_sr_entry = compute_ema_hybrid_entry
compute_shadow_micro_sr_entry = compute_ema_hybrid_entry

def resolve_entry_limit_price(signal: dict) -> tuple:
    """Return (limit_price, entry_mode) — EMA hybrid + USD offset when available, else pullback %."""
    direction = str(signal.get("final_direction") or "").upper()
    signal_price = float(signal.get("signal_price") or state.get("price") or 0)
    pullback_pct = float(signal.get("pullback_pct", state.get("pullback_threshold", 0.002)))
    if direction == "LONG":
        pullback_limit = signal_price * (1 - pullback_pct)
    elif direction == "SHORT":
        pullback_limit = signal_price * (1 + pullback_pct)
    else:
        pullback_limit = signal_price
    signal["planned_pullback_limit"] = pullback_limit
    ema_limit = signal.get("ema_hybrid_limit")
    if signal.get("entry_mode") == ENTRY_MODE_EMA_HYBRID and ema_limit is not None:
        return float(ema_limit), ENTRY_MODE_EMA_HYBRID
    return float(pullback_limit), ENTRY_MODE_PULLBACK

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
        candles = fetch_bitfinex_ohlcv(timeframe, limit=limit)
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
    health = compute_trend_health()
    logger.info(
        f"[MARKET_CTX] structure_score={structure.get('structure_score')} bias={structure.get('structure_bias')} "
        f"mtf={mtf.get('trends')} adx={trend_strength.get('adx')} trend_score={trend_strength.get('trend_score')} "
        f"trend_health={health.get('trend_state')} [PIPELINE ENFORCEMENT]"
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
# v86: earlier first rung 8→5 (replay-backed); was v82 10→6
TRAIL_LADDER = [
    (8, 5), (15, 10), (20, 15), (25, 18), (40, 30),
    (60, 50), (80, 60), (100, 80),
]
POSITION_MONITOR_INTERVAL_SEC = 1.0
LADDER_ARMED_MONITOR_INTERVAL_SEC = 0.15
WS_MAX_STALE_TRADE_SEC = 3.0
WS_RECONNECT_STALE_GRACE_SEC = 8.0
LADDER_AUDIT_LOG_INTERVAL_SEC = 0.25
# v84.1: EMA9/EMA21 hybrid entry (+$offset long / -$offset short); fallback pullback
ENTRY_MODE_PULLBACK = "PULLBACK_LIMIT"
ENTRY_MODE_EMA_HYBRID = "EMA_HYBRID_LIMIT"
EMA_HYBRID_ENTRY_OFFSET_USD = 20.0
# v85 P0: time-based Type-A stall (v83 candle gate was too slow for sim session)
TYPE_A_STALL_ENABLED = True
TYPE_A_STALL_MIN_AGE_SEC = 8 * 60
TYPE_A_STALL_MIN_CANDLES = 3  # legacy reference / analyzer label
TYPE_A_STALL_MAX_MFE_PCT = 5.0
TYPE_A_STALL_WEAK_VOLUME_RATIO = 0.5
TYPE_A_STALL_WEAK_DELTA = 0.0
TREND_WEAKENING_ENABLED = True
TREND_WEAKENING_LOCK_TIGHTEN_PCT = 2.0
TREND_WEAKENING_MIN_SIGNALS = 2
TREND_WEAKENING_SCRATCH_UNREAL_PCT = 2.0
TREND_HEALTH_HISTORY_MAX = 12
SPREAD_PENALTY_ENABLED = True
SPREAD_PENALTY_THRESHOLD = 5
SPREAD_PENALTY_MARGIN_MULT = 0.75
SPREAD_PENALTY_LOCK_TIGHTEN_PCT = 1.0
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
THESIS_FAST_EXIT_UNREAL_PCT = -20.0  # v85: replay plateau -18..-20%; was -28%
THESIS_MFE_PROTECT_PCT = 8.0  # v85: skip thesis fast-cut only while unreal still >= 8% margin
ADX_BLOCK_NEW_ENTRY = 15.0
ADX_HALF_SIZE_BELOW = 18.0
ADX_HALF_SIZE_MULT = 0.5
RESEARCH_AI_THRESHOLD_DEFAULT = 50
LIVE_AI_THRESHOLD_FLOOR = 70  # suggested live default only — dashboard override is not clamped to this
AI_THRESHOLD_MIN = 0
AI_THRESHOLD_MAX = 100
RESEARCH_EDGE_THRESHOLD_DEFAULT = 2.0
MOMENTUM_CHOP_BLOCK_ABOVE = 0.5  # strict reference for analyzer sweeps; golden stack uses GOLDEN_STACK_CHOP_MAX
# v78 free-run: let AI APPROVE execute — still log gate metrics/margins for analyzer sweet-spot
RESEARCH_FREE_RUN_DISABLE_MTF_GATE = True
RESEARCH_FREE_RUN_DISABLE_CHOP_GATE = True
RESEARCH_FREE_RUN_DISABLE_MOMENTUM_ALIGN = True
RESEARCH_AI_SOLE_AUTHORITY = True  # v81: AI decides all; gates log-only unless golden_stack_enabled
# v86 Golden Stack — dashboard toggle; edge/AI thresholds remain dashboard-controlled
GOLDEN_STACK_DEFAULT_ENABLED = True
GOLDEN_STACK_CHOP_MAX = 0.8
GOLDEN_STACK_ADX_MIN = 15.0
GOLDEN_STACK_ADX_ALT_MAX = 20.0
GOLDEN_STACK_ADX_BLOCK_LOW = 25.0
GOLDEN_STACK_ADX_BLOCK_HIGH = 30.0
GOLDEN_STACK_SPREAD_MIN = 3
GOLDEN_STACK_SPREAD_MAX = 7
GOLDEN_STACK_SHORT_STRUCT_MAX = -5.0
GOLDEN_STACK_EMA_DIST_MAX_PCT = 0.5
GOLDEN_STACK_EMA_DIST_IDEAL_MAX_PCT = 0.3
GOLDEN_STACK_BLOCK_HIGH_POS_FUNDING = True
GOLDEN_STACK_PREFER_NEUTRAL_FUNDING = True
RESEARCH_PERIODIC_AI_INTERVAL_SEC = 300  # align with AI_COOLDOWN_SECONDS
BLOCK_FREE_RANGE_ENTRIES = True
EDGE_DEAD_ZONE_LOW = 4.92
EDGE_DEAD_ZONE_HIGH = 5.1
DASHBOARD_AUTO_REFRESH_MS = 60000
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT", "7800"))
DASHBOARD_BIND_HOST = os.getenv("DASHBOARD_BIND_HOST", "0.0.0.0")
DASHBOARD_PUBLIC_HOST = os.getenv("DASHBOARD_PUBLIC_HOST", "127.0.0.1")

def dashboard_public_url() -> str:
    custom = (os.getenv("DASHBOARD_PUBLIC_URL") or "").strip()
    if custom:
        return custom if custom.endswith("/") else custom + "/"
    return f"http://{DASHBOARD_PUBLIC_HOST}:{DASHBOARD_PORT}/"
DAILY_DRAWDOWN_PAUSE_USD = 20.0
CONSECUTIVE_LOSS_PAUSE = 4
DEFAULT_RESEARCH_LEVERAGE = 100
MAX_RESEARCH_LEVERAGE = 100
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
CSV_PIPELINE_EVENTS = "pipeline_events_3factor.csv"
CSV_AI_ERRORS = "ai_errors_3factor.csv"
PIPELINE_EVENT_DEDUPE_SEC = 2.0
EMA_FAST = 9
EMA_SLOW = 21
EMA_LONG = 200
LIVE_TRADING_ENABLED = False
MAX_PENDING_ORDERS = 2
MAX_EXPIRED_ORDERS = 20

def _load_local_dotenv():
    """Load .env into os.environ (does not override existing vars).
    On Railway, Admin Control → Neon → push-showcase-bot is authoritative."""
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
        return
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    secret_keys = frozenset({
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "OPENROUTER_API_KEY", "BITFINEX_API_KEY", "BITFINEX_API_SECRET",
        "BYBIT_API_KEY", "BYBIT_SECRET", "BINANCE_API_KEY", "BINANCE_API_SECRET",
        "OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE",
        "HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_PRIVATE_KEY",
    })
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if not key or key in os.environ:
                    continue
                if key in secret_keys and (os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("CREDENTIALS_FROM")):
                    continue
                os.environ[key] = val
    except Exception:
        pass

_load_local_dotenv()
DEEPSEEK_API_KEY = (os.getenv("DEEPSEEK_API_KEY") or "").strip() or None
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
FAST_MONITOR_INTERVAL_SEC = 2.0
STARTING_BALANCE = 500.0
MAX_CONCURRENT_POSITIONS_DEFAULT = 6
AI_TIMEOUT_SEC = 60
HEDGE_MODE = False
SIGNAL_TTL_SEC = 2700
REPLAY_TTL_SEC = 120 * 60
MAX_EVENT_QUEUE = 10000
LIMIT_ORDER_MAX_AGE_SEC = 1800
SHADOW_REPLAY_TTL_SEC = 2 * 3600  # 2h blocked-APPROVE counterfactual replay (long-position what-if)
POST_BLOCK_CONTINUATION_SEC = 3600  # min post-block tick window for block-quality research
GLOBAL_SIGNAL_COOLDOWN = 300
HEARTBEAT_INTERVAL = 300.0
ANALYTICS_INTERVAL_SEC = 600
MIN_ANALYTICS_TRADES = 20
OHLCV_FETCH_INTERVAL = 60
STARTUP_GRACE_PERIOD = 30
CANDLE_STALE_SEC = 180
MAX_ACTIVE_SIGNALS = 6
MAX_SIGNAL_RETENTION_SEC = 7200
BUFFER_MIN = 150  # Legacy depth target for process_signal warmup flag; readiness gate uses WINDOW_SIZE (10).
MIN_CANDLES = 200
READY_STABLE_SEC = 5.0
STALE_SOFT_SEC = 20
STALE_HARD_SEC = 180
SR_ZONE_PCT = 0.0162
PRICE_CHANGE_THRESHOLD = 0.0002
MAX_WS_AGE = 3
PIPELINE_INTERVAL = 10.0
AI_COOLDOWN_SECONDS = int(os.getenv("AI_COOLDOWN_SECONDS", "300"))  # mandatory 5 min between DeepSeek calls
MIN_PIPELINE_INTERVAL = 30
EDGE_INTERVAL_SEC = 30
EDGE_SCORE_MAX = 6.0
EDGE_HYSTERESIS_DROP = 0.5
EDGE_MIN_SPIKE_DELTA = 0.4
EDGE_CROSS_ONLY_TRIGGER = True
EDGE_CANDLE_REARM_RESEARCH = True
EDGE_RANGE_THRESHOLD_BUMP = 0.5
EDGE_CHOP_CONFLICTED_PENALTY = 0.5
EDGE_CHOP_COMPRESSION_PENALTY = 0.35
EDGE_PRE_AI_MIN_SCORE = 3.0
PRE_AI_BLOCK_CONFLICTED_BELOW = 4.0
PRE_AI_BLOCK_COMPRESSION_BELOW = 3.2
PRE_AI_BLOCK_LOW_ADX_BELOW = 3.5
PRE_AI_MIN_ADX = 12.0
DOUBLE_CONFIRM_AI = False
MIN_DATA_QUALITY_FOR_EDGE = 0.7
EXECUTION_FIX_VERSION = "v10.9.439-v86-golden-stack-gates"


def csv_research_meta() -> dict:
    """Columns written to research CSVs for analyzer version/exchange verification."""
    return {
        "exchange": BOT_EXCHANGE,
        "data_symbol": SYMBOL,
        "bot_version": EXECUTION_FIX_VERSION,
        "fee_profile": EXCHANGE_FEE_PROFILE,
        "analyzer_sync_id": ANALYZER_SYNC_ID,
    }
ORDER_PLACEMENT_GRACE_SEC = 30
# Instant fill only when dashboard pullback is 0.0% (see execute_simulated_order / execute_order).
RESEARCH_INSTANT_FILL = False
TERMINAL_SIGNAL_STATUSES = frozenset({"EXPIRED", "BLOCKED", "REJECTED", "COMPLETE", "CANCELLED", "CLOSED", "FILLED"})
TERMINAL_SIGNAL_OUTCOMES = frozenset({"STALE_NO_EXPOSURE", "SIGNAL_TTL_EXPIRED", "TTL_EXPIRED", "CAPACITY_REPLACED", "WIN", "LOSS"})
last_signal_hash = None
last_event_trigger = 0.0
last_pipeline_run = 0.0
last_edge_compute = 0.0

EDGE_OPTIONS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
EDGE_RANGE_PRESETS = [
    {"id": "min_only", "min": 0.0, "max": None, "label": "Any > 0 (no upper cap) — v81 sole-AI funnel"},
    {"id": "2.0_2.5", "min": 2.0, "max": 2.5, "label": "2.0 – 2.5 (sweet spot experiment)"},
    {"id": "2.0_3.0", "min": 2.0, "max": 3.0, "label": "2.0 – 3.0"},
    {"id": "2.5_3.5", "min": 2.5, "max": 3.5, "label": "2.5 – 3.5"},
    {"id": "3.0_4.0", "min": 3.0, "max": 4.0, "label": "3.0 – 4.0"},
    {"id": "custom", "min": None, "max": None, "label": "Custom min / max"},
]
DEFAULT_EDGE_RANGE_PRESET = "min_only"

candidate_signal = {"active": False, "direction": None, "confidence": 0.0, "ts": 0.0}

state = {
    "strategy_mode": "RESEARCH",
    "allow_compression": True,
    "live_armed": False,
    "early_fail_enabled": True,
    "block_free_range_entries": True,
    "ai_enabled": True,
    "invert_signal": False,
    "telegram_enabled": False,
    "pullback_threshold": 0.001,
    "leverage": DEFAULT_RESEARCH_LEVERAGE,
    "max_active_signals": MAX_CONCURRENT_POSITIONS_DEFAULT,
    "ai_threshold": 50,
    "consecutive_losses": 0,
    "loss_pause_until": 0.0,
    "edge_threshold": 0.0,
    "edge_threshold_max": None,
    "edge_range_preset": DEFAULT_EDGE_RANGE_PRESET,
    "min_confidence": 0,
    "force_ai_every_signal": False,
    "debug_enabled": False,
    "fresh_collection_mode": False,
    "golden_stack_enabled": GOLDEN_STACK_DEFAULT_ENABLED,
    "last_fresh_reset_ts": 0.0,
    "last_fresh_reset_summary": "",
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
    "last_approve_outcome": {"status": None, "reason": None, "effective_threshold": None, "edge_at_approve": None, "trade_id": None, "ts": None},
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
    "trend_health": {},
    "trend_health_history": [],
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
        "edge_reason": "UNKNOWN",
        "edge_trigger_reason": None,
        "pipeline_event_trigger": None,
        "edge_above_threshold": False,
    },
    "last_edge": 0.0,
    "edge_prev": 0.0,
    "edge_trigger_armed": True,
    "last_edge_trigger_candle_bucket": -1,
    "edge_threshold": 2.0,
    "last_pipeline_stage": "IDLE",
    "warmup_mode": True
}
shutdown_event = threading.Event()
PAUSE_PRIORITIES = {"STALE_DATA_HARD_STOP": 50, "THREAD_CRASH": 1, "QUEUE_OVERFLOW": 60, "": 0, "CSV_FAILURE": 100, "PRELOAD_FAILED": 100, "ADMIN_MANUAL": 200}

def get_edge_threshold():
    with state_lock:
        return round(float(state["edge_threshold"]), 1)

def get_edge_threshold_max():
    """Optional upper cap; None = no max (min-only gate)."""
    with state_lock:
        raw = state.get("edge_threshold_max")
    if raw is None or raw == "":
        return None
    try:
        return round(float(raw), 1)
    except (TypeError, ValueError):
        return None

def _edge_range_label() -> str:
    lo = get_edge_threshold()
    hi = get_edge_threshold_max()
    if hi is None:
        return f">={lo:.1f}"
    return f"{lo:.1f}–{hi:.1f}"

def edge_range_allows(edge_score: float) -> tuple:
    """Return (allowed, block_reason). Uses effective min + optional dashboard max."""
    edge_score = round(float(edge_score), 1)
    eff_thr = get_effective_edge_threshold()
    if eff_thr <= 0.0:
        if edge_score <= 0.0:
            return False, "EDGE_BELOW_THRESHOLD"
    elif edge_score < eff_thr - 1e-9:
        return False, "EDGE_BELOW_THRESHOLD"
    edge_max = get_edge_threshold_max()
    if edge_max is not None and edge_score > edge_max + 1e-9:
        return False, "EDGE_ABOVE_MAX"
    return True, "EDGE_IN_RANGE"

def get_pre_ai_min_score() -> float:
    """Pre-AI gate follows dashboard edge threshold (research tuning knob)."""
    return get_edge_threshold()

def get_dynamic_flat_momentum_floor(base: float = None) -> float:
    """
    Flat-momentum extra bar scales with dashboard edge:
    edge 2.0 → no flat penalty (experiment mode); edge 4.0+ → full FLAT_MOMENTUM_EDGE_FLOOR.
    """
    base = round(float(base if base is not None else get_edge_threshold()), 1)
    if base <= FLAT_MOMENTUM_FLOOR_LOW_EDGE:
        return base
    if base >= FLAT_MOMENTUM_FLOOR_HIGH_EDGE:
        return round(FLAT_MOMENTUM_EDGE_FLOOR, 1)
    span = FLAT_MOMENTUM_FLOOR_HIGH_EDGE - FLAT_MOMENTUM_FLOOR_LOW_EDGE
    t = (base - FLAT_MOMENTUM_FLOOR_LOW_EDGE) / span
    return round(FLAT_MOMENTUM_FLOOR_LOW_EDGE + t * (FLAT_MOMENTUM_EDGE_FLOOR - FLAT_MOMENTUM_FLOOR_LOW_EDGE), 1)

def record_approve_outcome(status: str, reason: str = None, eff_thr: float = None, trade_id: str = None, edge: float = None, ai: dict = None):
    with state_lock:
        state["last_approve_outcome"] = {
            "status": status,
            "reason": reason,
            "effective_threshold": eff_thr,
            "edge_at_approve": edge,
            "trade_id": trade_id,
            "ts": utc_iso(),
            "win_prob": (ai or {}).get("win_prob") or state.get("last_ai", {}).get("win_prob"),
            "direction": (ai or {}).get("direction") or state.get("last_ai", {}).get("direction"),
        }

def enforce_edge_threshold_options():
    with state_lock:
        current = round(state["edge_threshold"], 1)
        research_collect = state.get("strategy_mode") == "RESEARCH" and not state.get("live_armed")
        if research_collect and RESEARCH_AI_SOLE_AUTHORITY:
            fallback = 0.0
        else:
            fallback = RESEARCH_EDGE_THRESHOLD_DEFAULT if research_collect else 3.0
        if current not in [round(x, 1) for x in EDGE_OPTIONS]:
            logger.warning(
                f"[EDGE ENFORCEMENT] Invalid threshold {current} - resetting to {fallback} "
                f"[PIPELINE ENFORCEMENT]"
            )
            state["edge_threshold"] = fallback
            state["debug_state"]["edge_reason"] = "RESET_TO_DEFAULT"
    logger.info(f"[EDGE ENFORCEMENT] threshold validated at {get_edge_threshold()} [PIPELINE ENFORCEMENT]")

def get_effective_edge_threshold() -> float:
    """Regime-aware edge bar in live mode; dashboard-only in research data collection."""
    base = get_edge_threshold()
    if is_research_data_collection():
        with state_lock:
            comps = state.get("debug_state", {}).get("edge_components") or {}
            comps["base_threshold"] = base
            comps["effective_threshold"] = round(base, 1)
            comps["research_mode"] = True
            state["debug_state"]["edge_components"] = comps
        return round(base, 1)
    with state_lock:
        regime = str(state.get("regime", "RANGE")).upper()
        sr_state = str((state.get("support_resistance") or {}).get("sr_state", "")).upper()
        fs = state.get("feature_snapshot") or {}
    eff = round(base + EDGE_RANGE_THRESHOLD_BUMP, 1) if (regime == "RANGE" or "COMPRESSION" in sr_state) else base
    flat_floor = get_dynamic_flat_momentum_floor(base)
    momentum_flat = _compute_momentum_metric(fs) < MOMENTUM_FLAT_MAX
    if momentum_flat:
        eff = max(eff, flat_floor)
    with state_lock:
        comps = state.get("debug_state", {}).get("edge_components") or {}
        comps["base_threshold"] = base
        comps["flat_momentum_floor"] = flat_floor
        comps["momentum_flat"] = momentum_flat
        comps["effective_threshold"] = round(eff, 1)
        state["debug_state"]["edge_components"] = comps
    return round(eff, 1)

def _sync_edge_arm_state(edge_score: float):
    """Hysteresis: re-arm only after edge falls meaningfully below threshold."""
    edge_score = round(float(edge_score), 1)
    eff_thr = get_effective_edge_threshold()
    rearm_below = round(max(0.5, eff_thr - EDGE_HYSTERESIS_DROP), 1)
    with state_lock:
        prev = round(float(state.get("edge_prev", state.get("last_edge", 0)) or 0), 1)
        state["edge_prev"] = edge_score
        if edge_score < rearm_below:
            state["edge_trigger_armed"] = True
        armed = state.get("edge_trigger_armed", True)
        range_hi = get_edge_threshold_max()
        range_cap = f"{range_hi:.1f}" if range_hi is not None else f"{EDGE_SCORE_MAX:.1f}+"
        state["debug_state"]["edge_progress"] = (
            f"{edge_score:.1f}/{eff_thr:.1f}–{range_cap} armed={armed}"
        )
        state["debug_state"]["last_edge_score"] = edge_score
    return prev, armed, eff_thr

def is_edge_valid(edge_score: float) -> bool:
    edge_score_rounded = round(edge_score, 1)
    eff_thr = get_effective_edge_threshold()
    valid, _reason = edge_range_allows(edge_score_rounded)
    _sync_edge_arm_state(edge_score_rounded)
    with state_lock:
        state["debug_state"]["edge_above_threshold"] = valid
    logger.info(
        f"[EDGE GATE] edge={edge_score_rounded:.1f} valid={valid} "
        f"range={_edge_range_label()} effective_min={eff_thr} [PIPELINE ENFORCEMENT]"
    )
    return valid

def _edge_candle_bucket() -> int:
    return int(time.time() // CANDLE_INTERVAL_SEC)

def should_trigger_edge_event(edge_score: float) -> tuple:
    """
    Decide if this cycle should open the pipeline (and eventually AI).
    Cross-only mode avoids calling AI on every tick while edge stays high.
    In RESEARCH mode, re-arm once per closed 15m candle bucket when edge stays elevated.
    """
    edge_score = round(float(edge_score), 1)
    prev, armed, eff_thr = _sync_edge_arm_state(edge_score)
    allowed, block_reason = edge_range_allows(edge_score)
    if not allowed:
        return False, block_reason
    candle_bucket = _edge_candle_bucket()
    with state_lock:
        last_candle_bucket = int(state.get("last_edge_trigger_candle_bucket", -1))
        research_candle_rearm = (
            EDGE_CANDLE_REARM_RESEARCH
            and state.get("strategy_mode") == "RESEARCH"
            and candle_bucket > last_candle_bucket
        )
    spike = (edge_score - prev) >= EDGE_MIN_SPIKE_DELTA
    if EDGE_CROSS_ONLY_TRIGGER:
        if armed or research_candle_rearm:
            with state_lock:
                state["edge_trigger_armed"] = False
                state["last_edge_trigger_candle_bucket"] = candle_bucket
            if prev < eff_thr:
                reason = "EDGE_CROSS"
            elif spike:
                reason = "EDGE_SPIKE"
            elif research_candle_rearm and not armed:
                reason = "CANDLE_REARM"
            else:
                reason = "EDGE_ARMED"
            logger.info(
                f"[EDGE TRIGGER] {reason} edge={edge_score} prev={prev} thr={eff_thr} "
                f"candle_bucket={candle_bucket} [PIPELINE ENFORCEMENT]"
            )
            return True, reason
        return False, "EDGE_SUSTAINED_NO_REARM"
    if spike or prev < eff_thr:
        return True, "EDGE_PASS"
    return True, "EDGE_PASS"

def _sole_ai_research_mode() -> bool:
    return RESEARCH_AI_SOLE_AUTHORITY and is_research_data_collection()

def evaluate_pre_ai_gate(edge_score: float, features: dict = None) -> tuple:
    """Cheap structural gate — blocks AI without an API call."""
    if _sole_ai_research_mode():
        return False, None
    edge_score = round(float(edge_score), 1)
    allowed, block_reason = edge_range_allows(edge_score)
    if not allowed:
        if block_reason == "EDGE_ABOVE_MAX":
            return True, f"PRE_AI_EDGE_ABOVE_MAX_{edge_score}"
        return True, f"PRE_AI_EDGE_LOW_{edge_score}"
    pre_ai_min = get_pre_ai_min_score()
    if edge_score < pre_ai_min:
        return True, f"PRE_AI_EDGE_LOW_{edge_score}"
    if is_research_data_collection():
        return False, None
    update_market_context()
    with state_lock:
        mc = state.get("market_context") or {}
    mtf = (mc.get("multi_tf") or {})
    ts = (mc.get("trend_strength") or {})
    agreement = str(mtf.get("agreement", ""))
    adx = float(ts.get("adx") or 0)
    sr_state = ""
    if features:
        sr_state = str(features.get("sr_state", "")).upper()
    if not sr_state:
        sr_state = str((state.get("support_resistance") or {}).get("sr_state", "")).upper()
    if agreement == "CONFLICTED" and edge_score < PRE_AI_BLOCK_CONFLICTED_BELOW:
        return True, "PRE_AI_MTF_CONFLICTED"
    if "COMPRESSION" in sr_state and edge_score < PRE_AI_BLOCK_COMPRESSION_BELOW:
        return True, "PRE_AI_RANGE_COMPRESSION"
    if adx < PRE_AI_MIN_ADX and edge_score < PRE_AI_BLOCK_LOW_ADX_BELOW:
        return True, f"PRE_AI_LOW_ADX_{adx:.1f}"
    return False, None

def ai_cooldown_remaining_sec() -> int:
    return max(0, int(AI_COOLDOWN_SECONDS - (time.time() - state.get("last_ai_call_ts", 0))))

def reserve_ai_cooldown_slot() -> tuple:
    """Atomically claim the 5-minute DeepSeek slot immediately before any API call."""
    with state_lock:
        now = time.time()
        last = float(state.get("last_ai_call_ts") or 0)
        if now - last < AI_COOLDOWN_SECONDS:
            rem = int(AI_COOLDOWN_SECONDS - (now - last))
            return False, f"AI_COOLDOWN_{rem}s"
        state["last_ai_call_ts"] = now
        state["debug_state"]["ai_cooldown_active"] = True
        state["debug_state"]["cooldown_remaining_ai"] = AI_COOLDOWN_SECONDS
    return True, "RESERVED"

def should_invoke_ai(ctx: dict, edge_score: float, event_trigger: bool) -> tuple:
    """Final gate before DeepSeek — edge event + pre-AI blocks; cooldown enforced via reserve_ai_cooldown_slot()."""
    if state.get("force_ai_every_signal"):
        return True, "FORCE_AI"
    if _sole_ai_research_mode():
        if ai_cooldown_remaining_sec() > 0:
            return False, f"AI_COOLDOWN_{ai_cooldown_remaining_sec()}s"
        if round(float(edge_score), 1) <= 0.0:
            return False, "EDGE_ZERO"
        return True, "PERIODIC_RESEARCH_AI"
    if ai_cooldown_remaining_sec() > 0:
        return False, f"AI_COOLDOWN_{ai_cooldown_remaining_sec()}s"
    if not event_trigger:
        return False, "NO_EDGE_TRIGGER"
    blocked, reason = evaluate_pre_ai_gate(edge_score, ctx)
    if blocked:
        return False, reason
    return True, "EDGE_EVENT"

def compute_ret_1m():
    if len(price_buffer) < 2:
        return 0.0
    return (price_buffer[-1] - price_buffer[-2]) / price_buffer[-2] if price_buffer[-2] != 0 else 0.0

def compute_ret_5m():
    if len(price_buffer) < 20:
        return 0.0
    return (price_buffer[-1] - price_buffer[-20]) / price_buffer[-20] if price_buffer[-20] != 0 else 0.0

def populate_candle_buffers_from_candles(candles, *, force=False):
    if not force and len(candle_range_buffer) >= WINDOW_SIZE:
        return 0
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
        except Exception as e:
            logger.error(f"[CANDLE BUFFER PRELOAD ERROR] {e} [PIPELINE ENFORCEMENT]")
    logger.info(f"[CANDLE BUFFER PRELOAD] populated {populated} candles into buffers [PIPELINE ENFORCEMENT]")
    return populated

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
        ret_1m_raw, ret_5m_raw, velocity_raw = _signed_momentum_components(features)
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
        agreement = str(mtf.get("agreement", ""))
        struct_score_raw = float(ms.get("structure_score") or 0)
        pref_dir = None
        if agreement == "BEAR_ALIGNED" or struct_score_raw <= -3:
            pref_dir = "SHORT"
        elif agreement == "BULL_ALIGNED" or struct_score_raw >= 3:
            pref_dir = "LONG"

        def _aligned_momentum(val, scale):
            if pref_dir == "SHORT":
                return max(0.0, -val) * scale
            if pref_dir == "LONG":
                return max(0.0, val) * scale
            return abs(val) * scale

        momentum_raw = max(
            _aligned_momentum(ret_1m_raw, 5000),
            _aligned_momentum(ret_5m_raw, 2000),
            _aligned_momentum(velocity_raw, 8000),
        )
        momentum_component = min(momentum_raw, 1.0) * 1.0
        momentum_misalign_penalty = 0.0
        if pref_dir == "SHORT" and _tape_bounce_against("SHORT", features):
            momentum_misalign_penalty = min(0.6, ret_1m_raw * 8000 + max(0.0, ret_5m_raw) * 3000)
        elif pref_dir == "LONG" and _tape_bounce_against("LONG", features):
            momentum_misalign_penalty = min(0.6, abs(ret_1m_raw) * 8000 + max(0.0, -ret_5m_raw) * 3000)
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
        chop_penalty = 0.0
        if mtf.get("agreement") == "CONFLICTED":
            chop_penalty += EDGE_CHOP_CONFLICTED_PENALTY
        sr_state = str((state.get("support_resistance") or {}).get("sr_state", "")).upper()
        if "COMPRESSION" in sr_state:
            chop_penalty += EDGE_CHOP_COMPRESSION_PENALTY
        if adx < PRE_AI_MIN_ADX:
            chop_penalty += 0.25
        edge_score = edge_score - chop_penalty - momentum_misalign_penalty
        edge_score = max(0.1, min(edge_score, EDGE_SCORE_MAX))
        edge_score = round(edge_score, 1)
        eff_thr = get_effective_edge_threshold()
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
                "chop_penalty": round(chop_penalty, 2),
                "momentum_misalign_penalty": round(momentum_misalign_penalty, 2),
                "effective_threshold": eff_thr,
            }
            state["debug_state"]["edge_progress"] = (
                f"{edge_score:.1f}/{eff_thr:.1f} (max {EDGE_SCORE_MAX:.1f})"
            )
            state["debug_state"]["edge_reason"] = "OK" if edge_score > 0.5 else "LOW"
        logger.info(f"[EDGE] compute_edge_score completed edge={edge_score:.1f} reason={state['debug_state']['edge_reason']} [PIPELINE ENFORCEMENT]")
        enforce_edge_threshold_options()
        update_debug_state_always("EDGE_DONE", {"edge": edge_score})
        return edge_score
    except Exception as e:
        logger.error(f"[EDGE SCORE ERROR] {e} [PIPELINE ENFORCEMENT]")
        return 0.1

def _fresh_debug_state():
    return {
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
        "edge_reason": "UNKNOWN",
        "edge_trigger_reason": None,
        "pipeline_event_trigger": None,
        "edge_above_threshold": False,
    }

def _ai_gate_was_called(debug_state: dict) -> bool:
    return bool(((debug_state or {}).get("ai_gate") or {}).get("called"))

def _is_misleading_ai_skip_label(label) -> bool:
    """Labels that imply DeepSeek ran when ai_gate.called is false."""
    if label is None:
        return False
    s = str(label).strip().upper()
    if not s or s in ("-", "OK", "UNKNOWN"):
        return False
    if s.startswith("EDGE_") or s.startswith("PRE_AI_"):
        return False
    if s in (
        "EDGE_FAIL", "DUPLICATE", "DUPLICATE_KEY", "CTX_FAIL", "NO_PRICE",
        "CLUSTER_ENTRY", "MAX_ACTIVE_SIGNALS", "GLOBAL_COOLDOWN",
    ):
        return False
    if s.startswith("AI_"):
        return True
    return s in {"REJECT", "AI_REJECT", "AI_REJECTED", "BELOW_THRESHOLD", "AI_FAIL"}

def _dashboard_idle_gate_reason(debug_state: dict) -> str:
    dbg = debug_state or {}
    reason = dbg.get("edge_trigger_reason") or dbg.get("edge_reason")
    if reason and str(reason) not in ("OK", "UNKNOWN", ""):
        return str(reason)
    edge = dbg.get("last_edge_score")
    try:
        eff = (dbg.get("edge_components") or {}).get("effective_threshold")
        if eff is None:
            eff = get_effective_edge_threshold()
    except Exception:
        eff = get_edge_threshold()
    if edge is not None and eff is not None:
        return f"EDGE_WAIT ({float(edge):.1f} / {float(eff):.1f})"
    return "WAITING_FOR_EDGE"

def _dashboard_skip_block(debug_state: dict) -> tuple:
    """Map skip/block to the real gate when DeepSeek was not invoked this cycle."""
    dbg = debug_state or {}
    skip = dbg.get("skip_reason")
    block = dbg.get("last_block_reason")
    if _ai_gate_was_called(dbg):
        return skip or "-", block or "-"
    if skip and not _is_misleading_ai_skip_label(skip):
        return skip, block or skip
    if block and not _is_misleading_ai_skip_label(block):
        return skip or block, block
    idle = _dashboard_idle_gate_reason(dbg)
    return idle, idle

def build_dashboard_display(snapshot: dict) -> dict:
    """Server-side labels for dashboard (avoids stale AI_REJECT / Bybit-era copy)."""
    dbg = snapshot.get("debug_state") or {}
    ag = dbg.get("ai_gate") or {}
    la = snapshot.get("last_ai") or {}
    sk, bl = _dashboard_skip_block(dbg)
    symbol = BITFINEX_WS_SYMBOL
    if _ai_gate_was_called(dbg):
        dec = la.get("decision") or snapshot.get("ai_outcome") or "UNKNOWN"
        if dec == "AI_ERROR":
            ai_status = "AI_ERROR"
            err_note = (la.get("error_detail") or la.get("reason") or la.get("comment") or "DeepSeek API/parse failure")[:400]
            if "MISSING_API_KEY" in err_note or "MISSING_API_KEY" in str(la.get("error_type") or ""):
                err_note = "MISSING_API_KEY — add DEEPSEEK_API_KEY to Final Bots\\.env and restart"
            good = snapshot.get("last_ai_best") or _pick_dashboard_last_ai({"last_ai": {}}, snapshot.get("ai_history") or [])
            if good.get("decision") not in (None, "", "AI_ERROR", "UNKNOWN"):
                ai_note = f"{err_note} | Last OK: {good.get('decision')} {good.get('win_prob')}% ({good.get('direction')})"
            else:
                ai_note = err_note
        else:
            ai_status = dec
            ai_note = "DeepSeek evaluated this pipeline cycle"
        ai_prob = la.get("win_prob") if dec != "AI_ERROR" else None
    else:
        ai_status = "NO_AI_CALL"
        ai_note = f"No DeepSeek call — {sk}"
        ai_prob = None
        if la.get("decision") and la.get("source") in ("CSV", "FRESH", "AI"):
            ai_note += f" (table below may show prior call: {la.get('decision')})"
    return {
        "display_skip_block": {"skip": sk, "block": bl},
        "display_ai": {
            "status": ai_status,
            "win_prob": ai_prob,
            "note": ai_note,
            "gate_called": bool(ag.get("called")),
        },
        "exchange_label": "Bitfinex",
        "market_symbol": symbol,
        "data_banner_ws": f"REAL BITFINEX MARKET DATA (WS) · {symbol}",
        "data_banner_rest": f"BITFINEX REST FALLBACK · {symbol}",
        "data_banner_boot": f"BITFINEX CONNECTING · {symbol}",
    }

def sync_dashboard_branding():
    """Keep exchange/banner fields on state so /api/state is never Bybit-stale."""
    try:
        with state_lock:
            snap = {
                "debug_state": copy.deepcopy(state.get("debug_state") or {}),
                "last_ai": copy.deepcopy(state.get("last_ai") or {}),
                "ai_outcome": state.get("ai_outcome"),
            }
        branding = build_dashboard_display(snap)
        with state_lock:
            state.update(branding)
    except Exception as e:
        logger.debug(f"[DASHBOARD BRANDING] {e}")

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
    files = [
        CSV_DECISIONS, CSV_TRADES, CSV_EXPIRED, CSV_BLOCKS, CSV_AI_TRANCHE, CSV_SETUP_LOG, CSV_CANDLES,
        CSV_PIPELINE_EVENTS, CSV_AI_ERRORS,
    ]
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
                logger.info(f"[RESET] Deleted {f} [PIPELINE ENFORCEMENT]")
        except Exception as e:
            logger.error(f"[RESET ERROR] Failed to delete {f}: {e} [PIPELINE ENFORCEMENT]")

def update_logger_level():
    try:
        console_level = logging.DEBUG if state.get("debug_enabled") else logging.INFO
        logger.setLevel(console_level)
        for h in logger.handlers:
            if isinstance(h, RotatingFileHandler):
                h.setLevel(logging.INFO)
            else:
                h.setLevel(console_level)
        logger.info(
            f"[LOGGER] console={logging.getLevelName(console_level)} "
            f"file=INFO rotate={LOG_MAX_BYTES // (1024*1024)}MB x{LOG_BACKUP_COUNT} [PIPELINE ENFORCEMENT]"
        )
    except Exception as e:
        logger.error(f"[LOGGER ERROR] {e} [PIPELINE ENFORCEMENT]")

def dynamic_csv_writer(filename, row):
    try:
        file_exists = os.path.exists(filename)
        if not file_exists:
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                writer.writeheader()
                writer.writerow(safe_csv_row(row))
            return
        with open(filename, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            existing = reader.fieldnames or []
        new_fields = list(set(existing) | set(row.keys()))
        if set(new_fields) != set(existing):
            with open(filename, "r", encoding="utf-8", errors="replace") as f:
                old_rows = list(csv.DictReader(f))
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=new_fields)
                writer.writeheader()
                for old in old_rows:
                    writer.writerow(old)
                writer.writerow(safe_csv_row(row))
        else:
            with open(filename, "a", newline="", encoding="utf-8") as f:
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

def enforce_log(signal: dict, stage: str, extra: str = None, skip_stage: str = None):
    if not signal or not isinstance(signal, dict) or not signal.get("trade_id"):
        logger.error(f"[LOG ENFORCEMENT FAIL] Missing trade_id for stage={stage} [PIPELINE ENFORCEMENT]")
        return
    logged_key = f"_logged_{stage}"
    if signal.get(logged_key):
        return
    signal[logged_key] = True
    signal["_logged"] = True
    log_decision(signal, stage, extra or stage, skip_stage=skip_stage or stage, ai_extra=extra)
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
    if ai.get("decision") == "APPROVE":
        record_approve_outcome(
            "BLOCKED",
            reason,
            signal.get("effective_threshold_at_entry") or get_effective_edge_threshold(),
            signal.get("trade_id"),
            signal.get("edge_score_at_entry"),
            ai,
        )
        defer_shadow_research(signal.get("trade_id"), reason)
        if not signal.get("_logged_BLOCKED"):
            enforce_log(signal, "BLOCKED", reason, skip_stage="POST_AI")
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
        dbg = state.get("debug_state") or {}
        snapshot = {
            "time": utc_iso(),
            "stage": stage,
            "price": state.get("price"),
            "regime": state.get("regime"),
            "ai": {
                "direction": ai.get("direction") if ai else None,
                "decision": ai.get("decision") if ai else None,
                "win_prob": ai.get("win_prob") if ai else None,
                "source": ai.get("source") if ai else None,
                "ai_error": ai.get("ai_error") if ai else None,
                "error_type": ai.get("error_type") if ai else None,
                "error_detail": (ai.get("error_detail") or (ai.get("comment") if ai else None) or "")[:500] if ai else None,
                "latency_ms": ai.get("latency_ms") if ai else None,
                "http_status": ai.get("http_status") if ai else None,
            },
            "edge": {
                "score": dbg.get("last_edge_score"),
                "threshold": get_edge_threshold(),
                "effective": get_effective_edge_threshold(),
                "trigger_reason": dbg.get("edge_trigger_reason"),
                "trigger": dbg.get("pipeline_event_trigger"),
                "components": dbg.get("edge_components"),
            },
            "ai_gate": dbg.get("ai_gate"),
            "threshold": state.get("ai_threshold"),
            "max_pos": state.get("max_active_signals"),
            "active_signals": get_active_signal_count(),
            "execution": {"allowed": execution_allowed(),"reason": state.get("execution_reason")},
            "signal": {"id": signal.get("trade_id") if signal else None,"status": signal.get("status") if signal else None,"direction": signal.get("final_direction") if signal else None},
            "orderflow": {"delta": orderflow["delta"], "imbalance": orderflow["imbalance"]},
            "volume_spike": (len(volume_buffer) > 5 and volume_buffer[-1] > np.mean(volume_buffer) * 1.5),
            "exposure": compute_exposure(),
            "pipeline_outcome": state.get("pipeline_outcome"),
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

def _structure_allows_bear_continuation(market_context: dict) -> bool:
    mc = market_context or {}
    ms = mc.get("market_structure", {})
    mtf = mc.get("multi_tf", {})
    struct_score = float(ms.get("structure_score") or 0)
    if ms.get("lh_ll_sequence_active"):
        return True
    if struct_score <= -2:
        return True
    if mtf.get("agreement") == "BEAR_ALIGNED":
        return True
    return False


def _structure_allows_bull_continuation(market_context: dict) -> bool:
    mc = market_context or {}
    ms = mc.get("market_structure", {})
    mtf = mc.get("multi_tf", {})
    struct_score = float(ms.get("structure_score") or 0)
    if ms.get("hh_hl_sequence_active"):
        return True
    if struct_score >= 2:
        return True
    if mtf.get("agreement") == "BULL_ALIGNED":
        return True
    return False


def _ai_confirms_sr_continuation(direction: str, ai: dict, market_context: dict) -> bool:
    """AI already receives SR context; APPROVE + factor scores can confirm continuation at S/R."""
    if not ai or str(ai.get("decision", "")).upper() != "APPROVE":
        return False
    bull = int(ai.get("bull_score", 0) or 0)
    bear = int(ai.get("bear_score", 0) or 0)
    mc = market_context or {}
    ms = mc.get("market_structure", {})
    struct_score = float(ms.get("structure_score") or 0)
    mtf_agree = (mc.get("multi_tf") or {}).get("agreement", "MIXED")
    if direction == "SHORT":
        return (
            bear >= bull + MIN_FACTOR_SCORE_MARGIN
            and (mtf_agree == "BEAR_ALIGNED" or struct_score <= SHORT_NEAR_SUPPORT_MIN_STRUCT)
        )
    if direction == "LONG":
        return (
            bull >= bear + MIN_FACTOR_SCORE_MARGIN
            and (mtf_agree == "BULL_ALIGNED" or struct_score >= 2)
        )
    return False


def evaluate_sr_direction_filter(direction: str, sr_state: str, market_context: dict = None, ai: dict = None):
    """
    Phase B soft S/R filter. SOFT mode blocks fade trades at S/R only when structure + MTF oppose;
    strong continuation (and AI factor confirmation after SR-aware APPROVE) is allowed.
    Returns (blocked: bool, reason: str|None).
    """
    if SR_FILTER_MODE == "OFF":
        return False, None
    if direction not in ("LONG", "SHORT"):
        return False, None
    mc = market_context or {}
    ms = mc.get("market_structure", {})
    mtf = mc.get("multi_tf", {})
    struct_score = float(ms.get("structure_score") or 0)
    mtf_agree = mtf.get("agreement", "MIXED")

    if direction == "LONG" and sr_state == "NEAR_RESISTANCE":
        if SR_FILTER_MODE == "HARD" or (BLOCK_LONG_NEAR_RESISTANCE and SR_FILTER_MODE != "SOFT"):
            return True, "LONG_BLOCKED_NEAR_RESISTANCE"
        if _structure_allows_bull_continuation(mc) or _ai_confirms_sr_continuation("LONG", ai, mc):
            return False, "SR_SOFT_ALLOW_BULL_CONTINUATION"
        if struct_score <= -2 and mtf_agree == "BEAR_ALIGNED":
            return True, "LONG_BLOCKED_NEAR_RESISTANCE"
        if BLOCK_LONG_NEAR_RESISTANCE:
            return True, "LONG_BLOCKED_NEAR_RESISTANCE"
        return False, None

    if direction == "SHORT" and sr_state == "NEAR_SUPPORT":
        if SR_FILTER_MODE == "HARD":
            return True, "SHORT_BLOCKED_NEAR_SUPPORT"
        if _structure_allows_bear_continuation(mc):
            return False, "SR_SOFT_ALLOW_BEAR_CONTINUATION"
        if _ai_confirms_sr_continuation("SHORT", ai, mc):
            return False, "SR_SOFT_ALLOW_AI_FACTOR_CONTINUATION"
        if struct_score >= 2 and mtf_agree == "BULL_ALIGNED":
            return True, "SHORT_BLOCKED_NEAR_SUPPORT"
        if BLOCK_SHORT_NEAR_SUPPORT:
            return True, "SHORT_BLOCKED_NEAR_SUPPORT_WEAK"
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
        text, _latency = call_deepseek_api([{"role": "user", "content": confirm_prompt}], temperature=0.3)
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

def _format_melbourne_hm(ts_str: str) -> str:
    if not ts_str or ts_str == "-":
        return "-"
    try:
        ts = ts_str.replace("Z", "+00:00")
        if "+" not in ts and ts.count(":") >= 2:
            ts = ts + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mel = dt.astimezone(ZoneInfo("Australia/Melbourne"))
        return mel.strftime("%Y-%m-%d %H:%M Melbourne")
    except Exception:
        return ts_str

def _session_ai_history(in_memory: list, limit: int = 50) -> list:
    """Return in-memory AI history rows from current bot session only."""
    session_start = bot_start_time or 0.0
    filtered = []
    for row in in_memory or []:
        ts = parse_ts(row.get("time") or row.get("ts") or "")
        if ts >= session_start - 1.0:
            filtered.append(row)
    return filtered[-limit:]

def _research_log_would_block(signal, ai, reason, edge_score=None):
    """Log gate that would have blocked in v80; continue in v81 sole-AI mode."""
    tag = f"WOULD_BLOCK_{reason}"
    log_blocked_signal(signal, ai, tag)
    log_pipeline_event(
        "POST_AI", "WOULD_BLOCK", tag,
        signal.get("trade_id") if signal else None,
        edge_score if edge_score is not None else (signal or {}).get("edge_score_at_entry", 0),
        force=True,
    )

def _append_ai_history_row(ai_result: dict) -> None:
    """In-memory session AI history for dashboard AI History table."""
    now_iso = utc_iso()
    with state_lock:
        state["ai_history"].append({
            "time": now_iso,
            "melbourne_time": _format_melbourne_hm(now_iso),
            "session_ts": bot_start_time,
            "trade_id": ai_result.get("trade_id"),
            "ai_direction_raw": ai_result.get("direction"),
            "final_direction": ai_result.get("direction"),
            "inverted": False,
            "decision": ai_result.get("decision"),
            "win_prob": ai_result.get("win_prob"),
            "edge_score": state.get("last_edge", 0.0),
            "edge_threshold": get_edge_threshold(),
            "source": ai_result.get("source", "AI"),
            "comment": (ai_result.get("comment") or "")[:2000],
            "ai_error": ai_result.get("ai_error", False),
            "error_type": ai_result.get("error_type"),
            "error_detail": (ai_result.get("error_detail") or "")[:500],
            "final_outcome": state.get("execution_outcome", "PENDING"),
            "bull_score": ai_result.get("bull_score", 0),
            "bear_score": ai_result.get("bear_score", 0),
        })
        hist_limit = 50 if _sole_ai_research_mode() else 5
        state["ai_history"] = state["ai_history"][-hist_limit:]
        state["ai_history_updated"] = time.time()

def _sync_ai_dashboard_debug(ai_result: dict, trade_id: str = None) -> None:
    """Align top AI card + debug panel after every DeepSeek evaluation (approve or reject)."""
    tid = trade_id or ai_result.get("trade_id")
    with state_lock:
        state["last_ai_ts"] = time.time()
        state["debug_state"]["last_ai_call"] = utc_iso()
        state["debug_state"]["last_ai_score"] = ai_result.get("win_prob")
        state["last_ai"]["win_prob"] = ai_result.get("win_prob")
        state["last_ai"]["direction"] = ai_result.get("direction")
        state["last_ai"]["decision"] = ai_result.get("decision")
        state["last_ai"]["trade_id"] = tid
        state["last_ai"]["comment"] = (ai_result.get("comment") or "")[:500]
        state["last_ai"]["source"] = ai_result.get("source", "FRESH")
        state["last_ai"]["ai_error"] = ai_result.get("ai_error", False)
        state["last_ai"]["error_type"] = ai_result.get("error_type")
        state["last_ai"]["error_detail"] = (ai_result.get("error_detail") or "")[:500]
        if ai_result.get("decision") == "AI_ERROR":
            state["last_ai"]["reason"] = f"AI_ERROR:{ai_result.get('error_type', 'unknown')}"
        else:
            state["last_ai"]["reason"] = (ai_result.get("comment") or "")[:500]
        state["ai_outcome"] = ai_result.get("decision")
        state["ai_decision"] = ai_result.get("decision")

def _deepseek_api_key():
    """Read key each call so .env / env changes apply without full process restart."""
    _load_local_dotenv()
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip() or None

def _csv_row_to_ai_history(row: dict) -> dict:
    try:
        wp = float(row.get("win_prob") or 0)
    except (TypeError, ValueError):
        wp = 0
    dec = (row.get("decision") or "").strip().upper()
    if not dec and str(row.get("approved", "")).lower() in ("true", "1", "yes"):
        dec = "APPROVE"
    if not dec and str(row.get("approved", "")).lower() in ("false", "0", "no"):
        dec = "REJECT"
    comment = (row.get("comment") or row.get("full_comment") or "")
    if not dec or dec == "AI_ERROR":
        m = re.search(r"Decision:\s*(APPROVE|REJECT)", comment, re.IGNORECASE)
        if m:
            dec = m.group(1).upper()
    return {
        "time": row.get("ts") or row.get("time") or "-",
        "trade_id": row.get("trade_id"),
        "ai_direction_raw": row.get("ai_direction_raw") or row.get("dir"),
        "final_direction": row.get("final_direction") or row.get("dir"),
        "inverted": str(row.get("inverted", "")).lower() in ("true", "1", "yes"),
        "decision": dec or "UNKNOWN",
        "win_prob": wp,
        "comment": comment[:2000],
        "source": row.get("source", "CSV"),
        "ai_error": str(row.get("ai_error", "")).lower() in ("true", "1", "yes") or dec == "AI_ERROR",
    }

def _load_recent_ai_history_from_csv(limit: int = 5) -> list:
    """Hydrate dashboard from Final Bots + legacy %USERPROFILE% logs."""
    bot_root = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(bot_root, CSV_AI_TRANCHE),
        os.path.join(os.path.expanduser("~"), CSV_AI_TRANCHE),
        CSV_AI_TRANCHE,
    ]
    seen_paths = set()
    rows = []
    for path in paths:
        if path in seen_paths or not os.path.isfile(path):
            continue
        seen_paths.add(path)
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                with open(path, newline="", encoding=enc) as f:
                    rows.extend(list(csv.DictReader(f)))
                break
            except UnicodeDecodeError:
                continue
            except Exception as e:
                logger.debug(f"[AI HISTORY CSV] read failed {path}: {e}")
                break
    if not rows:
        return []
    rows.sort(key=lambda r: r.get("ts") or r.get("time") or "")
    out = [_csv_row_to_ai_history(r) for r in rows[-max(limit * 3, limit):]]
    return out[-limit:]

def _merged_ai_history(in_memory: list, limit: int = 5) -> list:
    if RESEARCH_AI_SOLE_AUTHORITY or is_research_data_collection():
        return _session_ai_history(in_memory, limit)
    csv_rows = _load_recent_ai_history_from_csv(limit * 2)
    combined = (in_memory or []) + csv_rows
    by_trade = {}
    for row in combined:
        tid = row.get("trade_id") or ""
        prev = by_trade.get(tid)
        if not prev or (row.get("time") or "") >= (prev.get("time") or ""):
            by_trade[tid] = row
    deduped = sorted(by_trade.values(), key=lambda r: r.get("time") or "")
    return deduped[-limit:]

def _pick_dashboard_last_ai(snapshot: dict, ai_history: list) -> dict:
    """Prefer latest non-error AI row (fixes dashboard stuck on MISSING_API_KEY from an old cycle)."""
    la = copy.deepcopy(snapshot.get("last_ai") or {})
    if la.get("decision") and la.get("decision") != "AI_ERROR" and not la.get("ai_error"):
        return la
    for row in reversed(ai_history or []):
        dec = row.get("decision")
        if dec and dec != "AI_ERROR" and not row.get("ai_error"):
            return {
                "win_prob": row.get("win_prob"),
                "direction": row.get("ai_direction_raw") or row.get("dir"),
                "final_direction": row.get("final_direction"),
                "decision": dec,
                "trade_id": row.get("trade_id"),
                "comment": row.get("comment"),
                "source": row.get("source", "CSV"),
                "inverted": row.get("inverted"),
                "ai_error": False,
                "from_csv_history": True,
            }
    return la

_last_pipeline_event_log = {"key": None, "ts": 0.0}

def call_deepseek_api(messages, temperature=0.4):
    """HTTP + JSON guard for DeepSeek; raises RuntimeError with a short code prefix."""
    api_key = _deepseek_api_key()
    if not api_key:
        raise RuntimeError("MISSING_API_KEY")
    t0 = time.time()
    try:
        res = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": messages, "temperature": temperature},
            timeout=AI_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP_ERROR:{e}") from e
    latency_ms = int((time.time() - t0) * 1000)
    if res.status_code >= 400:
        body = (res.text or "")[:500]
        err = RuntimeError(f"HTTP_{res.status_code}:{body}")
        err.http_status = res.status_code  # type: ignore[attr-defined]
        err.latency_ms = latency_ms  # type: ignore[attr-defined]
        raise err
    try:
        payload = res.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON_DECODE:{e}:{(res.text or '')[:200]}") from e
    if payload.get("error"):
        err_obj = payload["error"]
        msg = err_obj.get("message", err_obj) if isinstance(err_obj, dict) else str(err_obj)
        raise RuntimeError(f"API_ERROR:{msg}")
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"NO_CHOICES:{str(payload)[:300]}")
    text = (choices[0].get("message") or {}).get("content")
    if not text:
        raise RuntimeError("EMPTY_CONTENT")
    return text, latency_ms

def build_ai_error_result(exc, trade_id=None, latency_ms=None, http_status=None):
    err_type = type(exc).__name__
    detail = str(exc)[:1500]
    if getattr(exc, "http_status", None):
        http_status = exc.http_status
    if latency_ms is None and getattr(exc, "latency_ms", None):
        latency_ms = exc.latency_ms
    return {
        "win_prob": 0,
        "direction": "NO_TRADE",
        "decision": "AI_ERROR",
        "override": False,
        "comment": f"AI_CRASH_FAILSAFE: {detail}",
        "ai_error": True,
        "error_type": err_type,
        "error_detail": detail,
        "latency_ms": latency_ms,
        "http_status": http_status,
        "factors": {},
        "source": "ERROR",
        "approved": False,
        "trade_id": trade_id,
    }

def log_pipeline_event(stage, outcome, reason="", trade_id=None, edge=None, extra=None, force=False):
    """Structured gate trace for edge/AI/post-AI diagnosis (deduped per 2s by default)."""
    global _last_pipeline_event_log
    try:
        key = f"{stage}|{outcome}|{reason}|{round(float(edge if edge is not None else state.get('last_edge', 0) or 0), 1)}"
        now = time.time()
        if not force and _last_pipeline_event_log.get("key") == key and now - _last_pipeline_event_log.get("ts", 0) < PIPELINE_EVENT_DEDUPE_SEC:
            return
        _last_pipeline_event_log = {"key": key, "ts": now}
        dbg = state.get("debug_state") or {}
        mc = state.get("market_context") or {}
        mtf = (mc.get("multi_tf") or {}).get("trends") or {}
        ec = dbg.get("edge_components") or {}
        row = {
            "ts": utc_iso(),
            "stage": stage,
            "outcome": outcome,
            "reason": reason,
            "trade_id": trade_id or "",
            "edge_score": edge if edge is not None else dbg.get("last_edge_score"),
            "edge_threshold": get_edge_threshold(),
            "effective_threshold": get_effective_edge_threshold(),
            "edge_trigger": dbg.get("pipeline_event_trigger"),
            "edge_trigger_reason": dbg.get("edge_trigger_reason"),
            "price": state.get("price"),
            "regime": state.get("regime"),
            "mtf_15m": mtf.get("15m"),
            "mtf_1h": mtf.get("1h"),
            "mtf_4h": mtf.get("4h"),
            "structure_bias": (mc.get("market_structure") or {}).get("structure_bias"),
            "adx": (mc.get("trend_strength") or {}).get("adx"),
            "ai_gate_called": (dbg.get("ai_gate") or {}).get("called"),
            "pipeline_outcome": state.get("pipeline_outcome"),
            "momentum_pts": ec.get("momentum"),
            "orderflow_pts": ec.get("orderflow"),
            **csv_research_meta(),
        }
        if extra:
            for k, v in extra.items():
                row[f"x_{k}"] = v
        with csv_lock:
            dynamic_csv_writer(CSV_PIPELINE_EVENTS, row)
    except Exception as e:
        logger.debug(f"[PIPELINE EVENT] skip log: {e}")

def log_ai_error_row(ai_result, ctx=None):
    try:
        with csv_lock:
            row = {
                "ts": utc_iso(),
                "trade_id": ai_result.get("trade_id") or "",
                "error_type": ai_result.get("error_type"),
                "error_detail": (ai_result.get("error_detail") or ai_result.get("comment") or "")[:2000],
                "http_status": ai_result.get("http_status"),
                "latency_ms": ai_result.get("latency_ms"),
                "edge_score": state.get("last_edge", 0.0),
                "edge_threshold": get_edge_threshold(),
                "effective_threshold": get_effective_edge_threshold(),
                "price": state.get("price"),
                "ctx_trade_id": (ctx or {}).get("trade_id"),
                **csv_research_meta(),
            }
            dynamic_csv_writer(CSV_AI_ERRORS, row)
    except Exception as e:
        logger.error(f"[AI ERROR CSV] {e}")

def log_ai_tranche_outcome(ai_result, event="AI_DECISION"):
    try:
        with csv_lock:
            row = {
                "ts": utc_iso(),
                "trade_id": ai_result.get("trade_id") or "",
                "ai_direction_raw": ai_result.get("direction"),
                "decision": ai_result.get("decision"),
                "approved": ai_result.get("approved", False),
                "win_prob": ai_result.get("win_prob"),
                "comment": (ai_result.get("comment") or "")[:2000],
                "source": ai_result.get("source"),
                "event": event,
                "ai_error": ai_result.get("ai_error", False),
                "error_type": ai_result.get("error_type"),
                "error_detail": (ai_result.get("error_detail") or "")[:2000],
                "latency_ms": ai_result.get("latency_ms"),
                "http_status": ai_result.get("http_status"),
                "edge_score": state.get("last_edge", 0.0),
                "factor_gate": ai_result.get("factor_gate"),
                "bull_score": ai_result.get("bull_score", 0),
                "bear_score": ai_result.get("bear_score", 0),
                **csv_research_meta(),
            }
            dynamic_csv_writer(CSV_AI_TRANCHE, row)
    except Exception as e:
        logger.error(f"[AI TRANCHE] {e}")

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
        text, latency_ms = call_deepseek_api([{"role": "user", "content": prompt}], temperature=0.4)
        log_pipeline_event("AI", "API_OK", "DEEPSEEK_RESPONSE", ctx.get("trade_id"), state.get("last_edge"), {"latency_ms": latency_ms}, force=True)
        logger.info(f"[AI RAW RESPONSE] {text} [PIPELINE ENFORCEMENT]")
        dir_match = re.search(r"Direction:\s*(LONG|SHORT|NO_TRADE)", text, re.IGNORECASE)
        direction = dir_match.group(1).upper() if dir_match else "NO_TRADE"
        match = re.search(r"Win probability:\s*(\d+)", text)
        win_prob = int(match.group(1)) if match else 0
        factors = parse_ai_factor_block(text)
        if win_prob <= 0:
            conf_json = re.search(r'"confidence"\s*:\s*(\d+)', text)
            conf_line = re.search(r"Confidence:\s*(\d+)", text, re.IGNORECASE)
            fallback = int(conf_json.group(1)) if conf_json else (int(conf_line.group(1)) if conf_line else 0)
            if 0 < fallback <= 100:
                win_prob = fallback
        decision_match = re.search(r"Decision:\s*(APPROVE|REJECT)", text, re.IGNORECASE)
        decision = decision_match.group(1).upper() if decision_match else "REJECT"
        override_match = re.search(r"Override:\s*(YES|NO)", text, re.IGNORECASE)
        override = override_match.group(1).upper() == "YES" if override_match else False
        if win_prob is None or win_prob <= 0:
            win_prob = 0
        if direction not in ["LONG", "SHORT", "NO_TRADE"]:
            direction = "NO_TRADE"
            win_prob = 0
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
            "trade_id": ctx.get("trade_id"),
            "latency_ms": latency_ms,
        }
        ai_result = apply_phase_c_factor_gate(ai_result)
        if DOUBLE_CONFIRM_AI:
            ai_result = double_confirm_ai(ai_result, ctx)
            ai_result = apply_phase_c_factor_gate(ai_result)
        with state_lock:
            state["pipeline_outcome"] = "AI_EVALUATED"
        ai_result["_tranche_logged"] = True
        log_ai_tranche_outcome(ai_result)
        _append_ai_history_row(ai_result)
        _sync_ai_dashboard_debug(ai_result)
        with state_lock:
            eff = get_effective_edge_threshold()
            state["debug_state"]["ai_gate"] = {
                "called": True,
                "reason": ai_result.get("decision", "AI_DONE"),
                "edge": state.get("last_edge", 0.0),
                "threshold": eff,
            }
        log_pipeline_event(
            "AI", ai_result.get("decision", "UNKNOWN"), ai_result.get("factor_gate") or "MODEL",
            ctx.get("trade_id"), state.get("last_edge"),
            {"win_prob": win_prob, "latency_ms": latency_ms}, force=True,
        )
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
        ai_result = build_ai_error_result(e, raw_context.get("trade_id"))
        full_pipeline_trace("[AI]", "EVALUATE_CRASH", raw_context.get("trade_id"))
        trace("AI", "EVALUATE_CRASH", raw_context.get("trade_id"))
        debug_snapshot(None, ai_result, "AI_CRASH")
        log_ai_error_row(ai_result, raw_context)
        log_ai_tranche_outcome(ai_result, event="AI_ERROR")
        _append_ai_history_row(ai_result)
        log_pipeline_event(
            "AI", "AI_ERROR", ai_result.get("error_type", "UNKNOWN"),
            raw_context.get("trade_id"), state.get("last_edge"),
            {"detail": (ai_result.get("error_detail") or "")[:200]}, force=True,
        )
        with state_lock:
            state["pipeline_outcome"] = "AI_CRASH"
            eff = get_effective_edge_threshold()
            state["debug_state"]["ai_gate"] = {
                "called": True,
                "reason": f"AI_ERROR:{ai_result.get('error_type', 'unknown')}",
                "edge": state.get("last_edge", 0.0),
                "threshold": eff,
            }
            state["debug_state"]["skip_reason"] = f"AI_ERROR:{ai_result.get('error_type', 'unknown')}"
        _append_ai_history_row(ai_result)
        _sync_ai_dashboard_debug(ai_result)
        with state_lock:
            state["ai_call_count"] = state.get("ai_call_count", 0) + 1
        logger.info(f"[AI RESULT] decision={ai_result['decision']} prob={ai_result['win_prob']} err={ai_result.get('error_type')} [PIPELINE ENFORCEMENT]")
        return ai_result

def is_research_data_collection() -> bool:
    with state_lock:
        return state.get("strategy_mode") == "RESEARCH" and not state.get("live_armed")

def _clamp_ai_threshold(value: float) -> float:
    return max(AI_THRESHOLD_MIN, min(AI_THRESHOLD_MAX, float(value)))

def get_ai_threshold():
    with state_lock:
        t = state.get("ai_threshold")
        research = state.get("strategy_mode") == "RESEARCH"
        default = RESEARCH_AI_THRESHOLD_DEFAULT if research else LIVE_AI_THRESHOLD_FLOOR
        raw = default if t is None else float(t)
        if research or state.get("_threshold_locked"):
            return _clamp_ai_threshold(raw)
        return _clamp_ai_threshold(max(raw, LIVE_AI_THRESHOLD_FLOOR))

def set_ai_threshold(value):
    with state_lock:
        research = state.get("strategy_mode") == "RESEARCH"
        default = RESEARCH_AI_THRESHOLD_DEFAULT if research else LIVE_AI_THRESHOLD_FLOOR
        raw = float(value) if value is not None else default
        state["ai_threshold"] = _clamp_ai_threshold(raw)
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
        edge_max = state.get("edge_threshold_max")
        if edge_max is not None and value > float(edge_max) + 1e-9:
            state["edge_threshold_max"] = value
        state["edge_range_preset"] = "custom"
        save_persistent_config()
        logger.info(
            f"[SET] EDGE min={state['edge_threshold']} max={state.get('edge_threshold_max')} "
            f"[PIPELINE ENFORCEMENT]"
        )
        enforce_edge_threshold_options()

def set_edge_threshold_max(value):
    if value is None or value == "" or (isinstance(value, str) and value.lower() in ("none", "null", "no_cap")):
        with state_lock:
            state["edge_threshold_max"] = None
            state["edge_range_preset"] = "custom"
            save_persistent_config()
        logger.info("[SET] EDGE max cleared (min-only gate) [PIPELINE ENFORCEMENT]")
        return
    value = round(float(value), 1)
    if value not in [round(x, 1) for x in EDGE_OPTIONS]:
        logger.warning(f"[EDGE SET] Invalid max {value} - rejected [PIPELINE ENFORCEMENT]")
        return
    with state_lock:
        if float(state["edge_threshold"]) > value + 1e-9:
            state["edge_threshold"] = value
        state["edge_threshold_max"] = value
        state["edge_range_preset"] = "custom"
        save_persistent_config()
    logger.info(f"[SET] EDGE max={value} min={get_edge_threshold()} [PIPELINE ENFORCEMENT]")
    enforce_edge_threshold_options()

def apply_edge_range_preset(preset_id: str):
    preset = next((p for p in EDGE_RANGE_PRESETS if p["id"] == preset_id), None)
    if not preset:
        logger.warning(f"[EDGE SET] Unknown preset {preset_id} [PIPELINE ENFORCEMENT]")
        return
    if preset_id == "custom":
        with state_lock:
            state["edge_range_preset"] = "custom"
            save_persistent_config()
        return
    with state_lock:
        state["edge_threshold"] = round(float(preset["min"]), 1)
        state["edge_threshold_max"] = None if preset["max"] is None else round(float(preset["max"]), 1)
        state["edge_range_preset"] = preset_id
        save_persistent_config()
    enforce_edge_threshold_options()
    logger.info(
        f"[SET] EDGE preset={preset_id} range={_edge_range_label()} [PIPELINE ENFORCEMENT]"
    )

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
    """Legacy alias — use should_invoke_ai for edge-gated AI."""
    return should_invoke_ai(ctx, state.get("last_edge", 0), event_trigger)

def log_setup(signal):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_setup"
        with csv_lock:
            row = {"ts": utc_iso(),"trade_id": signal.get("trade_id"),"price": signal.get("signal_price"),"ai_win_prob": signal.get("ai_win_prob", 0),"regime": signal.get("regime"),"direction": signal.get("final_direction"),"event": "BUILD","edge_score": signal.get("edge_score_at_entry"), **csv_research_meta()}
            dynamic_csv_writer(CSV_SETUP_LOG, row)
        logger.info(f"[LOG SETUP] trade_id={signal.get('trade_id')} ai_win_prob={signal.get('ai_win_prob',0)} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"SETUP LOG FAIL: {e} [PIPELINE ENFORCEMENT]")

def log_ai(signal, ai):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_ai"
        if ai.get("_tranche_logged"):
            return
        signal["_ai_logged"] = True
        with csv_lock:
            row = {"ts": utc_iso(),"trade_id": signal.get("trade_id"),"ai_direction_raw": ai.get("direction"),"final_direction": signal.get("final_direction"),"inverted": signal.get("inverted", False),"approved": ai.get("approved", False),"win_prob": ai.get("win_prob"),"comment": ai.get("comment"),"source": ai.get("source"),"event": "AI_DECISION","decision": ai.get("decision"),"override": ai.get("override", False),"full_comment": ai.get("comment"),"edge_score": signal.get("edge_score_at_entry"),"bull_score": ai.get("bull_score", 0),"bear_score": ai.get("bear_score", 0),"ai_error": ai.get("ai_error", False),"error_type": ai.get("error_type"),"error_detail": (ai.get("error_detail") or "")[:2000],"latency_ms": ai.get("latency_ms"), **csv_research_meta()}
            dynamic_csv_writer(CSV_AI_TRANCHE, row)
        logger.info(f"[LOG AI] trade_id={signal.get('trade_id')} prob={ai.get('win_prob')} source={ai.get('source')} decision={ai.get('decision')} override={ai.get('override')} final_direction={signal.get('final_direction')} inverted={signal.get('inverted')} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"AI LOG FAIL: {e} [PIPELINE ENFORCEMENT]")

def log_decision(signal, decision, reason, skip_stage=None, ai_extra=None):
    try:
        assert signal.get("trade_id"), "[CRITICAL] trade_id missing in log_decision"
        with csv_lock:
            row = {
                "ts": utc_iso(),
                "trade_id": signal.get("trade_id"),
                "decision": decision,
                "reason": reason,
                "skip_stage": skip_stage or decision,
                "ai_win_prob": signal.get("ai_win_prob"),
                "ai_threshold": get_ai_threshold(),
                "ai_decision_text": signal.get("ai_decision"),
                "edge_score": signal.get("edge_score_at_entry", 0.0),
                "edge_threshold": get_edge_threshold(),
                "effective_threshold": signal.get("effective_threshold_at_entry") or get_effective_edge_threshold(),
                "invert_signal": state.get("invert_signal", False),
                "early_fail_enabled": state.get("early_fail_enabled", True),
                "experiment_tag": f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}",
                "features_price": signal.get("features",{}).get("price"),
                "features_ema9": signal.get("features",{}).get("ema9"),
                "features_delta": signal.get("features",{}).get("delta"),
                "controls_edge": signal.get("controls",{}).get("edge_threshold"),
                "final_direction": signal.get("final_direction"),
                "ai_error": signal.get("ai_error")
                or (str(ai_extra or reason or skip_stage or "").startswith("AI_ERROR")),
                "ai_source": signal.get("ai_source"),
                "edge_trigger_reason": state.get("debug_state", {}).get("edge_trigger_reason"),
                **csv_research_meta(),
            }
            if ai_extra and str(ai_extra).startswith("AI_ERROR"):
                row["ai_error_detail"] = str(ai_extra)[:500]
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
            row["effective_threshold"] = signal.get("effective_threshold_at_entry") or get_effective_edge_threshold()
            row["experiment_tag"] = f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}"
            row.update(csv_research_meta())
            dynamic_csv_writer(CSV_BLOCKS, row)
        logger.info(f"[CSV] Blocked signal logged reason={reason} trade_id={signal.get('trade_id')} structure={sr.get('sr_state')} ai_decision={ai.get('decision')} experiment={row.get('experiment_tag')} final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]")
        patch_signal_snapshot_outcome(signal.get("trade_id"), executed=False, block_reason=reason)
    except Exception as e:
        logger.error(f"CSV BLOCKED WRITE FAILED: {e} [PIPELINE ENFORCEMENT]")

def log_no_signal_with_context(signal=None, reason="NO_SETUP_DETECTED", skip_stage="PRE_PIPELINE"):
    trade_id = str(uuid.uuid4()) if not signal or not signal.get("trade_id") else signal.get("trade_id")
    edge_score = state.get("last_edge", state.get("debug_state", {}).get("last_edge_score", 0.0))
    eff_thr = get_effective_edge_threshold()
    stub = {
        "trade_id": trade_id,
        "edge_score_at_entry": round(float(edge_score or 0.0), 1),
        "effective_threshold_at_entry": eff_thr,
        "features": state.get("debug_state", {}).get("last_features", {}),
    }
    enforce_log(stub, "NO_SIGNAL", reason, skip_stage=skip_stage)
    log_pipeline_event("PIPELINE", "NO_SIGNAL", reason, trade_id, edge_score, force=True)
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
        buffer_ready = (
            len(volume_buffer) >= WINDOW_SIZE
            and len(price_buffer) >= WINDOW_SIZE
            and len(delta_buffer) >= WINDOW_SIZE
        )
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
    if state.get("manual_admin_pause") or (
        state.get("execution_paused") and state.get("execution_reason") == "ADMIN_MANUAL"
    ):
        return False
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

        is_edge_valid(edge_score)
        if _sole_ai_research_mode() and ai_cooldown_remaining_sec() == 0 and round(edge_score, 1) > 0.0:
            event_trigger, trigger_reason = True, "PERIODIC_RESEARCH_AI"
        else:
            event_trigger, trigger_reason = should_trigger_edge_event(edge_score)

        last_event_trigger = now

        logger.info(
            f"[EVENT LIGHT V2] edge={edge_score:.1f} trigger={event_trigger} "
            f"reason={trigger_reason} effective_thr={get_effective_edge_threshold():.1f} "
            f"[PIPELINE ENFORCEMENT]"
        )

        eff_thr = get_effective_edge_threshold()
        with state_lock:
            ds = state["debug_state"]
            ds["last_edge_score"] = edge_score
            ds["pipeline_event_trigger"] = event_trigger
            ds["edge_trigger_reason"] = trigger_reason
            ds["edge_above_threshold"] = edge_score >= eff_thr
            ag = dict(ds.get("ai_gate") or {"called": False, "reason": "", "edge": 0.0, "threshold": 0.0})
            if not ag.get("called"):
                ag["reason"] = trigger_reason if not event_trigger else "EDGE_EVENT_ARMED"
                ag["edge"] = edge_score
                ag["threshold"] = eff_thr
                ds["ai_gate"] = ag
            if not event_trigger:
                ds["skip_reason"] = trigger_reason
                ds["last_block_reason"] = trigger_reason
            elif _is_misleading_ai_skip_label(ds.get("skip_reason")):
                ds["skip_reason"] = None
                ds["last_block_reason"] = None

        update_debug_state_always(
            "EVENT_DETECTED",
            {"edge": edge_score, "pipeline_event_trigger": event_trigger, "edge_trigger_reason": trigger_reason},
        )
        log_pipeline_event(
            "EDGE", "TRIGGER" if event_trigger else "WAIT",
            trigger_reason, None, edge_score,
            {"effective_threshold": eff_thr, "edge_threshold_max": get_edge_threshold_max()},
        )
        if not event_trigger and trigger_reason in ("EDGE_BELOW_THRESHOLD", "EDGE_ABOVE_MAX"):
            log_edge_census(edge_score, "EDGE_EVENT", trigger_reason, features)

        return {
            "event_trigger": event_trigger,
            "edge_trigger_reason": trigger_reason,
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
    pullback_pct = signal.get("pullback_pct", state.get("pullback_threshold", 0.002))
    signal_price = signal.get("signal_price", price)
    limit_price, entry_mode = resolve_entry_limit_price(signal)
    signal["entry_mode"] = entry_mode
    margin_usdt = float(signal.get("margin_usdt") or FIXED_MARGIN_USDT)
    lev = state.get("leverage", DEFAULT_RESEARCH_LEVERAGE)
    qty = margin_usdt * lev / price
    order = {
        "trade_id": signal["trade_id"],
        "side": map_signal_to_exchange_side(signal["final_direction"]),
        "signal_dir": signal["final_direction"],
        "limit_price": limit_price,
        "planned_limit_price": limit_price,
        "entry_mode": entry_mode,
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
    logger.info(
        f"[SIM] ORDER CREATED trade_id={signal.get('trade_id')} signal_price={fmt(signal_price)} "
        f"limit_price={fmt(limit_price)} entry_mode={entry_mode} pullback={pullback_pct*100}% "
        f"final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]"
    )
    defer_instant_fill = False
    features = signal.get("features") or state.get("feature_snapshot") or {}
    direction = signal.get("final_direction")
    if _tape_bounce_against(direction, features):
        defer_instant_fill = True
        logger.info(f"[SIM] Bounce vs {direction} — using pullback limit instead of instant fill [PIPELINE ENFORCEMENT]")
    setup_type = signal.get("setup_type") or classify_setup(features)
    if setup_type == "WEAK_SETUP":
        defer_instant_fill = True
        logger.info("[SIM] WEAK_SETUP — using pullback limit instead of instant fill [PIPELINE ENFORCEMENT]")
    if pullback_pct <= 0.0 and not defer_instant_fill and entry_mode != ENTRY_MODE_EMA_HYBRID:
        order["limit_price"] = price
        order["entry_type"] = "SIM_MARKET"
        order["fee_type"] = "TAKER"
        fill_order(order)
        logger.info(f"[SIM] Instant fill (pullback=0%) at {fmt(price)} trade_id={signal.get('trade_id')} [PIPELINE ENFORCEMENT]")
    elif defer_instant_fill and entry_mode != ENTRY_MODE_EMA_HYBRID:
        order["await_confirm"] = True
        logger.info(f"[SIM] Pullback limit pending confirm trade_id={signal.get('trade_id')} limit={fmt(limit_price)} [PIPELINE ENFORCEMENT]")
    elif defer_instant_fill and entry_mode == ENTRY_MODE_EMA_HYBRID:
        logger.info(
            f"[SIM] EMA_HYBRID_LIMIT — skip await_confirm trade_id={signal.get('trade_id')} "
            f"limit={fmt(limit_price)} [PIPELINE ENFORCEMENT]"
        )
    order["max_price_since_order"] = float(price)
    order["min_price_since_order"] = float(price)
    pipeline_state_sync()
    return True

def _update_pending_order_price_extremes(price: float):
    if price is None or price <= 0:
        return
    with trade_lock:
        for order in pending_orders:
            if order.get("status") != "PENDING":
                continue
            mx = order.get("max_price_since_order")
            if mx is None:
                order["max_price_since_order"] = float(price)
                order["min_price_since_order"] = float(price)
            else:
                order["max_price_since_order"] = max(float(mx), float(price))
                order["min_price_since_order"] = min(float(order.get("min_price_since_order", price)), float(price))

def _pending_limit_touched(order: dict, price: float) -> bool:
    limit = float(order["limit_price"])
    max_p = float(order.get("max_price_since_order", price) or price)
    min_p = float(order.get("min_price_since_order", price) or price)
    if order["side"] == "buy":
        return min_p <= limit or price <= limit
    if order["side"] == "sell":
        return max_p >= limit or price >= limit
    return False

def process_pending_orders():
    price = state.get("price")
    if price is None or price <= 0:
        return
    _update_pending_order_price_extremes(price)
    with trade_lock:
        for order in list(pending_orders):
            if order.get("status") != "PENDING":
                continue
            if order.get("await_confirm"):
                direction = order.get("signal_dir")
                fs = state.get("feature_snapshot") or {}
                r1, _, vel = _signed_momentum_components(fs)
                if direction == "SHORT" and vel > MOMENTUM_ALIGN_EPS and r1 > MOMENTUM_ALIGN_EPS:
                    continue
                if direction == "LONG" and vel < -MOMENTUM_ALIGN_EPS and r1 < -MOMENTUM_ALIGN_EPS:
                    continue
                order.pop("await_confirm", None)
            if not _pending_limit_touched(order, price):
                continue
            fill_px = float(order["limit_price"])
            order["fill_price"] = fill_px
            order["limit_price"] = fill_px
            order["status"] = "FILLED"
            fill_order(order)

def fill_order(order):
    planned = order.get("planned_limit_price") or order.get("signal_price")
    tick = order.get("fill_price") or order.get("limit_price")
    logger.info(
        f"[ORDER] FILLED trade_id={order['trade_id']} final_direction={order.get('signal_dir')} "
        f"tick={fmt(tick)} planned_limit={fmt(planned)} [PIPELINE ENFORCEMENT]"
    )
    with trade_lock:
        if order in pending_orders:
            pending_orders.remove(order)
    meta = trades_map.get(order["trade_id"], {})
    signal = meta.get("signal_ref", {})
    ai = meta.get("ai", {}) or signal.get("ai", {})
    if order["signal_dir"] not in ["LONG", "SHORT"]:
        raise Exception("Invalid signal direction")
    with trade_lock:
        pos = _build_open_position(order, signal, ai)
        open_positions.append(pos)
    fill_px = order.get("fill_price") or order.get("limit_price") or pos.get("entry")
    mark_approve_research_executed(pos.get("trade_id"), fill_px)
    master = trades_map.get(order["trade_id"], {}).get("signal_ref")
    if master:
        master.update({
            "status": "FILLED",
            "filled_ts": time.time(),
            "fill_price": fill_px,
            "planned_limit_price": order.get("planned_limit_price"),
            "outcome": "OPEN",
        })
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

def atomic_freeze_signal(signal, edge_score, pipeline_eff_thr=None):
    eff = float(pipeline_eff_thr if pipeline_eff_thr is not None else get_effective_edge_threshold())
    signal["features"] = copy.deepcopy(state.get("feature_snapshot", {}))
    signal["edge_score_at_entry"] = float(round(edge_score, 1))
    signal["edge_threshold_at_entry"] = float(get_edge_threshold())
    signal["effective_threshold_at_entry"] = eff
    signal["edge_passed"] = round(float(edge_score), 1) >= eff
    signal["controls"] = copy.deepcopy({
        "early_fail_enabled": state.get("early_fail_enabled"),
        "invert_signal": state.get("invert_signal"),
        "ai_enabled": state.get("ai_enabled"),
        "edge_threshold": get_edge_threshold(),
        "edge_threshold_max": get_edge_threshold_max(),
        "edge_range_preset": state.get("edge_range_preset"),
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

def log_edge_census(edge_score: float, stage: str, reason: str, features: dict = None, trade_id: str = None):
    """Log edge observations blocked before AI — full distribution for uncensored analyzer sweeps."""
    try:
        features = features or {}
        edge_score = round(float(edge_score or 0), 2)
        row = {
            "schema": "edge_census_v1",
            "ts": utc_iso(),
            "ts_epoch": time.time(),
            "stage": stage,
            "reason": reason,
            "trade_id": trade_id,
            "edge_score": edge_score,
            "edge_score_bucket": _edge_score_bucket(edge_score),
            "edge_threshold_min": get_edge_threshold(),
            "edge_threshold_max": get_edge_threshold_max(),
            "edge_range_preset": state.get("edge_range_preset"),
            "effective_threshold": get_effective_edge_threshold(),
            "in_range": edge_range_allows(edge_score)[0],
            "price": nz(state.get("price")),
            "regime": state.get("regime"),
            "sr_state": (state.get("support_resistance") or {}).get("sr_state"),
            "support_resistance_bucket": _sr_location_bucket(
                (state.get("support_resistance") or {}).get("sr_state")
            ),
            "session_bucket": _research_session_bucket(),
            "volume_ratio": nz(features.get("volume_ratio")),
            "delta": nz(features.get("delta")),
            "velocity": nz(features.get("velocity")),
            "bot_version": EXECUTION_FIX_VERSION,
            "analyzer_sync_id": ANALYZER_SYNC_ID,
        }
        rotate_log(EDGE_CENSUS_FILE)
        with open(EDGE_CENSUS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        logger.error(f"[EDGE_CENSUS] log failed: {e} [PIPELINE ENFORCEMENT]")


def log_near_edge(candidate, edge_score):
    try:
        edge_score = round(float(edge_score or 0), 2)
        with csv_lock:
            row = {
                "ts": utc_iso(),
                "edge_score": edge_score,
                "edge_score_bucket": _edge_score_bucket(edge_score),
                "threshold": get_edge_threshold(),
                "edge_threshold_max": get_edge_threshold_max(),
                "effective_threshold": get_effective_edge_threshold(),
                "in_range": edge_range_allows(edge_score)[0],
                "flat_momentum_floor": get_dynamic_flat_momentum_floor(),
                "price": nz(state.get("price")),
                "reason": "NEAR_EDGE",
                "delta": nz(candidate.get("delta")),
                "volume_ratio": nz(candidate.get("volume_ratio")),
                "experiment_tag": f"INV_{state.get('invert_signal',False)}_EF_{state.get('early_fail_enabled',True)}",
                **csv_research_meta(),
            }
            dynamic_csv_writer("near_edge.log", row)
        logger.info(f"[NEAR_EDGE] edge={edge_score:.1f} logged for analysis [PIPELINE ENFORCEMENT]")
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

            if edge_score >= 0.5:
                log_near_edge(features, edge_score)
            allowed_edge, edge_block = edge_range_allows(edge_score)
            if not allowed_edge:
                log_edge_census(edge_score, "PIPELINE", edge_block, features)

            event_obj = event if event else detect_event_light()
            if not event_obj:
                event_obj = {"event_trigger": False, "edge_score": edge_score, "price": nz(state.get("price")), "timestamp": utc_iso(), "features": features}

            eff_thr = get_effective_edge_threshold()
            pipeline_eff_thr = eff_thr
            sole = _sole_ai_research_mode()
            if sole:
                if round(float(edge_score), 1) <= 0.0:
                    logger.info(
                        f"[EDGE GATE] edge_score={edge_score:.1f} <= 0 — NO_SIGNAL (v81 sole-AI) "
                        f"[PIPELINE ENFORCEMENT]"
                    )
                    log_no_signal_with_context(reason="EDGE_FAIL")
                    full_pipeline_trace("BLOCKED", "EDGE_FAIL", None)
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = "EDGE_FAIL"
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = "EDGE_FAIL"
                    update_debug_state_always("EDGE_FAIL", {"edge": edge_score})
                    state["last_pipeline_stage"] = "IDLE"
                    return
            elif edge_score < eff_thr:
                logger.info(
                    f"[EDGE GATE] edge_score={edge_score:.1f} < effective_threshold={eff_thr} — NO_SIGNAL "
                    f"[PIPELINE ENFORCEMENT]"
                )
                log_no_signal_with_context(reason="EDGE_FAIL")
                full_pipeline_trace("BLOCKED", "EDGE_FAIL", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "EDGE_FAIL"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "EDGE_FAIL"
                update_debug_state_always("EDGE_FAIL", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if sole:
                trigger_ok = True
                trigger_reason = event_obj.get("edge_trigger_reason", "PERIODIC_RESEARCH_AI")
            elif event_obj.get("event_trigger") and event:
                trigger_ok = True
                trigger_reason = event_obj.get("edge_trigger_reason", "EDGE_EVENT_PASSTHROUGH")
            else:
                trigger_ok, trigger_reason = should_trigger_edge_event(edge_score)
            if not trigger_ok:
                logger.info(
                    f"[EDGE GATE] edge={edge_score:.1f} >= {eff_thr} but no trigger ({trigger_reason}) — skip AI "
                    f"[PIPELINE ENFORCEMENT]"
                )
                log_no_signal_with_context(reason=trigger_reason)
                full_pipeline_trace("BLOCKED", trigger_reason, None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = trigger_reason
                    state["debug_state"]["skip_reason"] = trigger_reason
                update_debug_state_always(trigger_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            logger.info(
                f"[PIPELINE] EDGE TRIGGER {trigger_reason} → candidate stage (AI only if pre-gates pass) "
                f"[PIPELINE ENFORCEMENT]"
            )

            safe_clear_pending()
            now = time.time()
            last_pipeline_run = now
            full_pipeline_trace("[PIPELINE]", "ENTER_process_signal", None)

            with state_lock:
                max_active = state.get("max_active_signals") or MAX_CONCURRENT_POSITIONS_DEFAULT
            if not sole and not ensure_signal_capacity():
                active = get_active_signal_count()
                logger.info(f"[MAX ACTIVE SIGNALS] Hard block at entry - {active}/{max_active} [PIPELINE ENFORCEMENT]")
                _agent_dbg("H1", "process_signal", "max_active_block", {"active": active, "max_active": max_active, "pending_list": len(pending_orders), "positions": len(open_positions), "fix_version": EXECUTION_FIX_VERSION})
                full_pipeline_trace("BLOCKED", "MAX_ACTIVE_SIGNALS", None)
                update_debug_state_always("MAX_ACTIVE_SIGNALS", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if not sole and time.time() - state.get("last_signal_create_ts", 0) < GLOBAL_SIGNAL_COOLDOWN:
                logger.info("[GLOBAL COOLDOWN] 5min block active after any prior signal attempt [PIPELINE ENFORCEMENT]")
                log_no_signal_with_context(reason="GLOBAL_COOLDOWN", skip_stage="COOLDOWN")
                full_pipeline_trace("BLOCKED", "GLOBAL_COOLDOWN", None)
                update_debug_state_always("GLOBAL_COOLDOWN", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if not sole:
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
                log_no_signal_with_context(reason="AI_COOLDOWN_ACTIVE", skip_stage="COOLDOWN")
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

            if round(float(edge_score), 1) < round(float(pipeline_eff_thr), 1):
                logger.info(
                    f"[EDGE GATE] edge={edge_score:.1f} < frozen effective={pipeline_eff_thr:.1f} "
                    f"— skip before AI [PIPELINE ENFORCEMENT]"
                )
                log_no_signal_with_context(reason=f"EDGE_BELOW_{pipeline_eff_thr}", skip_stage="PRE_AI")
                full_pipeline_trace("BLOCKED", "EDGE_BELOW_THRESHOLD", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "EDGE_BELOW_THRESHOLD"
                    state["debug_state"]["skip_reason"] = f"EDGE_BELOW_{pipeline_eff_thr}"
                update_debug_state_always("EDGE_BELOW_THRESHOLD", {"edge": edge_score, "effective_threshold": pipeline_eff_thr})
                state["last_pipeline_stage"] = "IDLE"
                return

            invoke_ai, ai_gate_reason = should_invoke_ai(ctx, edge_score, True)
            if not invoke_ai:
                logger.info(
                    f"[AI GATE] Skipped DeepSeek — {ai_gate_reason} edge={edge_score:.1f} "
                    f"[PIPELINE ENFORCEMENT]"
                )
                log_no_signal_with_context(reason=ai_gate_reason, skip_stage="PRE_AI")
                full_pipeline_trace("BLOCKED", ai_gate_reason, None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = ai_gate_reason
                    state["debug_state"]["skip_reason"] = ai_gate_reason
                    state["debug_state"]["ai_gate"] = {
                        "called": False,
                        "reason": ai_gate_reason,
                        "edge": edge_score,
                        "threshold": eff_thr,
                    }
                update_debug_state_always(ai_gate_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            ctx["trade_id"] = str(uuid.uuid4())
            with state_lock:
                state["debug_state"]["ai_gate"] = {
                    "called": False,
                    "reason": "AI_CALL_PENDING",
                    "edge": edge_score,
                    "threshold": pipeline_eff_thr,
                }
            reserved, reserve_reason = reserve_ai_cooldown_slot()
            if not reserved:
                logger.info(f"[AI] COOLDOWN RESERVE BLOCKED — {reserve_reason} [PIPELINE ENFORCEMENT]")
                log_no_signal_with_context(reason=reserve_reason, skip_stage="COOLDOWN")
                full_pipeline_trace("BLOCKED", reserve_reason, None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = reserve_reason
                    state["debug_state"]["skip_reason"] = reserve_reason
                    state["debug_state"]["ai_gate"] = {
                        "called": False,
                        "reason": reserve_reason,
                        "edge": edge_score,
                        "threshold": pipeline_eff_thr,
                    }
                update_debug_state_always(reserve_reason, {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            ai = evaluate_signal_with_ai(ctx)
            if not ai:
                enforce_log({"trade_id": ctx["trade_id"]}, "BLOCKED", "AI_FAIL")
                log_pipeline_event("AI", "BLOCKED", "AI_FAIL", ctx["trade_id"], edge_score, force=True)
                full_pipeline_trace("BLOCKED", "AI_FAIL", None)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = "AI_FAIL"
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = "AI_FAIL"
                update_debug_state_always("AI_FAIL", {"edge": edge_score})
                state["last_pipeline_stage"] = "IDLE"
                return

            if ai.get("decision") != "APPROVE":
                trade_id = ai.get("trade_id") or ctx["trade_id"]
                ai["trade_id"] = trade_id
                if ai.get("decision") == "AI_ERROR":
                    block_tag = f"AI_ERROR:{ai.get('error_type', 'UNKNOWN')}"
                    skip_stage = "AI_ERROR"
                else:
                    block_tag = ai.get("factor_gate") or f"AI_{ai.get('decision')}"
                    skip_stage = "AI"
                _sync_ai_dashboard_debug(ai, trade_id)
                reject_stub = {
                    "trade_id": trade_id,
                    "edge_score_at_entry": round(float(edge_score), 1),
                    "effective_threshold_at_entry": pipeline_eff_thr,
                    "ai_win_prob": ai.get("win_prob"),
                    "ai_decision": ai.get("decision"),
                    "ai_error": ai.get("ai_error"),
                    "ai_source": ai.get("source"),
                    "error_type": ai.get("error_type"),
                    "error_detail": ai.get("error_detail"),
                    "features": state.get("debug_state", {}).get("last_features", {}),
                }
                enforce_log(reject_stub, "BLOCKED", block_tag, skip_stage=skip_stage)
                log_pipeline_event("AI", "BLOCKED", block_tag, trade_id, edge_score, {"win_prob": ai.get("win_prob")}, force=True)
                full_pipeline_trace("BLOCKED", block_tag, trade_id)
                with state_lock:
                    state["debug_state"]["last_block_reason"] = block_tag
                    state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                    state["debug_state"]["skip_reason"] = block_tag
                update_debug_state_always(block_tag, {"edge": edge_score, "bull": ai.get("bull_score"), "bear": ai.get("bear_score")})
                state["last_pipeline_stage"] = "IDLE"
                return

            trade_id = ctx.get("trade_id") or str(uuid.uuid4())
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
            atomic_freeze_signal(signal, edge_score, pipeline_eff_thr)
            signal["ai_decision"] = ai.get("decision")
            signal["ai_win_prob"] = ai.get("win_prob")
            enforce_immutable(signal)
            ensure_signal_registered(signal)
            log_setup(signal)
            enforce_log(signal, "AI", extra=f"{ai.get('decision')}", skip_stage="AI")
            log_ai(signal, ai)
            record_approve_outcome("PENDING", None, pipeline_eff_thr, trade_id, edge_score, ai)
            full_pipeline_trace("[PIPELINE]", f"AI_{ai.get('decision')}", trade_id)
            with state_lock:
                state["debug_state"]["last_pipeline_stage"] = "AI"
                state["debug_state"]["last_ai_call"] = utc_iso()
                state["debug_state"]["ai_gate"] = {"called": True, "reason": "AI_CALLED", "edge": edge_score, "threshold": pipeline_eff_thr}
            full_pipeline_trace("[PIPELINE]", "START", trade_id)
            logger.info("[PIPELINE] EDGE+AI GATES PASSED (frozen eff_thr) → EXECUTION PATH [PIPELINE ENFORCEMENT]")
            logger.info(f"[PIPELINE START] timestamp={utc_iso()} event_trigger={event_obj.get('event_trigger', False)} edge={edge_score:.1f} eff={pipeline_eff_thr:.1f} [PIPELINE ENFORCEMENT]")

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
                "ai_reason": ai.get("comment", ""),
                "effective_threshold": pipeline_eff_thr,
            }

            if state["data_quality"] < 0.5:
                if sole:
                    _research_log_would_block(signal, ai, "LOW_DATA_QUALITY", edge_score)
                else:
                    logger.warning("[EXECUTION BLOCK] Data quality too low [PIPELINE ENFORCEMENT]")
                    enforce_log(signal, "BLOCKED", "LOW_DATA_QUALITY")
                    full_pipeline_trace("BLOCKED", "LOW_DATA_QUALITY", trade_id)
                    update_debug_state_always("LOW_DATA_QUALITY", {"edge": edge_score})
                    state["last_pipeline_stage"] = "IDLE"
                    return

            if state["data_quality"] < 0.7:
                logger.warning("[DATA QUALITY] Suboptimal (<0.7) [PIPELINE ENFORCEMENT]")

            if not (
                len(volume_buffer) >= WINDOW_SIZE
                and len(price_buffer) >= WINDOW_SIZE
                and len(delta_buffer) >= WINDOW_SIZE
            ):
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
            signal["pullback_pct"] = state.get("pullback_threshold", 0.002)
            compute_ema_hybrid_entry(signal)
            signal["golden_stack_enabled_at_entry"] = golden_stack_enabled()
            signal["golden_stack_eval"] = capture_golden_stack_eval(
                signal, ai, edge_score, signal.get("features")
            )
            begin_approve_research(signal, ai, pipeline_eff_thr)
            gs_blocked, gs_reason = evaluate_golden_stack_filter(
                final_direction,
                signal.get("context", {}) or ctx,
                ai,
                signal.get("features"),
                edge_score,
                signal,
            )
            if gs_blocked and _golden_stack_gate_exit(signal, ai, gs_reason, edge_score):
                patch_signal_snapshot_outcome(
                    signal.get("trade_id"),
                    executed=False,
                    block_reason=f"GOLDEN_STACK_{gs_reason}",
                )
                state["last_pipeline_stage"] = "IDLE"
                return

            if not signal.get("final_direction"):
                raise RuntimeError("PIPELINE BREAK: AI returned no direction")

            sr_state = signal.get("context", {}).get("sr_state") or state.get("support_resistance", {}).get("sr_state", "UNKNOWN")
            mc = signal.get("context", {}).get("market_context") or state.get("market_context", {})
            sr_blocked, sr_block_reason = evaluate_sr_direction_filter(final_direction, sr_state, mc, ai)
            if sr_blocked:
                if sole:
                    _research_log_would_block(signal, ai, sr_block_reason, edge_score)
                else:
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
                if sole:
                    _research_log_would_block(signal, ai, loc_reason, edge_score)
                else:
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
                final_direction, signal.get("context", {}) or ctx, ai, signal.get("features")
            )
            if qual_blocked:
                if sole:
                    _research_log_would_block(signal, ai, qual_reason, edge_score)
                else:
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

            ev_blocked, ev_reason = evaluate_evidence_entry_filter(
                final_direction,
                signal.get("context", {}) or ctx,
                ai,
                signal.get("features"),
                edge_score,
            )
            if ev_blocked:
                if sole:
                    _research_log_would_block(signal, ai, ev_reason, edge_score)
                else:
                    logger.info(f"[EVIDENCE GATE] {ev_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                    log_blocked_signal(signal, ai, ev_reason)
                    exit_pipeline(signal, ai, ev_reason)
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = ev_reason
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = ev_reason
                    update_debug_state_always(ev_reason, {"edge": edge_score})
                    state["last_pipeline_stage"] = "IDLE"
                    return

            setup_type = signal.get("setup_type") or classify_setup(signal.get("features") or {})
            if not is_research_data_collection():
                weak_min_edge = get_weak_setup_min_edge()
                if setup_type == "WEAK_SETUP" and edge_score < weak_min_edge:
                    weak_reason = f"WEAK_SETUP_LOW_EDGE_{edge_score:.1f}_LT_{weak_min_edge}"
                    logger.info(f"[SETUP GATE] {weak_reason} trade_id={trade_id} [PIPELINE ENFORCEMENT]")
                    log_pipeline_event("POST_AI", "BLOCKED", weak_reason, trade_id, edge_score, {"setup_type": setup_type, "min_edge": weak_min_edge}, force=True)
                    log_blocked_signal(signal, ai, weak_reason)
                    exit_pipeline(signal, ai, weak_reason)
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = weak_reason
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = weak_reason
                    update_debug_state_always(weak_reason, {"edge": edge_score})
                    state["last_pipeline_stage"] = "IDLE"
                    return

            if not sole and not ensure_directional_capacity(final_direction):
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
            if not sole:
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
                if sole:
                    _research_log_would_block(signal, ai, margin_reason, edge_score)
                    margin_usdt = margin_usdt or FIXED_MARGIN_USDT
                else:
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
            spread = compute_directional_spread(final_direction, ai)
            signal["margin_usdt"] = margin_usdt
            signal["conviction_spread"] = spread
            signal["spread_penalty_mult"] = spread_penalty_margin_mult(spread)
            health = compute_trend_health(final_direction)
            signal["trend_health_at_entry"] = health
            if final_direction == "LONG" and health.get("trend_state") == "BULL_WEAKENING":
                if sole:
                    _research_log_would_block(signal, ai, "TREND_WEAKENING", edge_score)
                else:
                    exit_pipeline(signal, ai, "TREND_WEAKENING")
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = "TREND_WEAKENING"
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = "TREND_WEAKENING"
                    update_debug_state_always("TREND_WEAKENING", {"edge": edge_score, "health": health})
                    state["last_pipeline_stage"] = "IDLE"
                    return
            if final_direction == "SHORT" and health.get("trend_state") == "BEAR_WEAKENING":
                if sole:
                    _research_log_would_block(signal, ai, "TREND_WEAKENING", edge_score)
                else:
                    exit_pipeline(signal, ai, "TREND_WEAKENING")
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = "TREND_WEAKENING"
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = "TREND_WEAKENING"
                    update_debug_state_always("TREND_WEAKENING", {"edge": edge_score, "health": health})
                    state["last_pipeline_stage"] = "IDLE"
                    return
            if spread_penalty_active(spread):
                logger.info(
                    f"[SPREAD PENALTY] spread={spread} margin_mult={signal['spread_penalty_mult']:.2f} "
                    f"margin=${margin_usdt} trade_id={trade_id} [PIPELINE ENFORCEMENT]"
                )
            prof = get_regime_risk_profile()
            sizing_ref = compute_reference_scaled_margin(
                final_direction, ai, signal.get("context", {}) or ctx
            )
            signal["sizing_reference"] = sizing_ref
            logger.info(
                f"[RISK SIZE] margin=${margin_usdt} (flat={FLAT_MARGIN_EVERY_TRADE}) "
                f"ref_scaled=${sizing_ref.get('reference_scaled_margin_usdt')} "
                f"spread={signal['conviction_spread']} regime={prof.get('label')} "
                f"trade_id={trade_id} [PIPELINE ENFORCEMENT]"
            )

            if ai.get("win_prob", 0) < get_ai_threshold():
                if sole:
                    _research_log_would_block(signal, ai, "BELOW_THRESHOLD", edge_score)
                else:
                    exit_pipeline(signal, ai, "BELOW_THRESHOLD")
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = "BELOW_THRESHOLD"
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = "BELOW_THRESHOLD"
                    update_debug_state_always("BELOW_THRESHOLD", {"edge": edge_score})
                    state["last_pipeline_stage"] = "IDLE"
                    return

            if is_clustered_entry(final_direction, price):
                if sole:
                    _research_log_would_block(signal, ai, "CLUSTER_ENTRY", edge_score)
                else:
                    exit_pipeline(signal, ai, "CLUSTER_ENTRY")
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = "CLUSTER_ENTRY"
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = "CLUSTER_ENTRY"
                    update_debug_state_always("CLUSTER_ENTRY", {"edge": edge_score, "price": price})
                    state["last_pipeline_stage"] = "IDLE"
                    return

            if not sole and not ensure_signal_capacity():
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
                exec_reason = state.get("execution_reason") or "EXECUTION_BLOCK"
                hard_exec_blocks = frozenset({"STALE_DATA_HARD_STOP", "NO_PRICE", "AI_FAIL"})
                if sole and exec_reason not in hard_exec_blocks:
                    _research_log_would_block(signal, ai, exec_reason, edge_score)
                else:
                    exit_pipeline(signal, ai, exec_reason)
                    with state_lock:
                        state["debug_state"]["last_block_reason"] = exec_reason
                        state["debug_state"]["last_pipeline_stage"] = "BLOCKED"
                        state["debug_state"]["skip_reason"] = exec_reason
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
            record_approve_outcome("EXECUTED", "ORDER_PLACED", pipeline_eff_thr, trade_id, edge_score, ai)
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

def _normalize_bitfinex_trade(mts, amount, price):
    px = float(price)
    amt = float(amount)
    if px <= 0:
        return None
    return {
        "p": px,
        "v": abs(amt),
        "S": "Buy" if amt > 0 else "Sell",
        "T": int(mts),
    }

def _bitfinex_ws_trades_from_message(data) -> list:
    """Parse Bitfinex WS v2 trades channel messages into internal trade dicts."""
    if not isinstance(data, list) or len(data) < 2:
        return []
    tag = data[1]
    out = []
    if isinstance(tag, str) and tag in ("te", "tu") and len(data) >= 3:
        row = data[2]
        if isinstance(row, list) and len(row) >= 4:
            t = _normalize_bitfinex_trade(row[1], row[2], row[3])
            if t:
                out.append(t)
    elif isinstance(tag, list):
        for row in tag:
            if isinstance(row, list) and len(row) >= 4:
                t = _normalize_bitfinex_trade(row[1], row[2], row[3])
                if t:
                    out.append(t)
    return out

def _process_ws_trade_tick(trade: dict):
    global _last_ws_trade_fp, _last_ws_trade_fp_ts, prev_price, prev_delta, avg_volume, recent_high, recent_low, rejection_strength
    trade_fp = (trade.get("T"), trade.get("p"), trade.get("v"), trade.get("S"))
    if trade_fp == _last_ws_trade_fp and (time.time() - _last_ws_trade_fp_ts) < 0.05:
        _agent_dbg("H2", "safe_ws_handler", "dedupe_skip", {"fp": str(trade_fp)[:80]})
        return
    price = float(trade.get("p", 0))
    size = float(trade.get("v", 0))
    if price <= 0:
        return
    if _is_stale_ws_trade(trade):
        trade_ts = _ws_trade_timestamp_sec(trade)
        logger.debug(
            f"[WS STALE SKIP] price={fmt(price)} trade_age={time.time() - trade_ts:.2f}s "
            f"fp={str(trade_fp)[:40]} [PIPELINE ENFORCEMENT]"
        )
        return
    _last_ws_trade_fp = trade_fp
    _last_ws_trade_fp_ts = time.time()
    prev_price = nz(state.get("price"))
    state["price"] = price
    state["price_ts"] = time.time()
    state["ws_last_tick"] = time.time()
    state["last_data_ts"] = time.time()
    price_buffer.append(price)
    volume_buffer.append(size)
    update_orderflow(trade)
    delta_buffer.append(orderflow["delta"])
    delta_change = orderflow["delta"] - orderflow.get("prev_delta", 0)
    delta_change_buffer.append(delta_change)
    imbalance_buffer.append(orderflow["imbalance"])
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
    trade_ts_raw = trade.get("T")
    trade_ts = trade_ts_raw / 1000 if trade_ts_raw and trade_ts_raw > 1e12 else (trade_ts_raw or time.time())
    latency = max(0, (time.time() - trade_ts) * 1000)
    with state_lock:
        state["diag"].update({"ws_latency_ms": round(latency, 1)})
        state["price_source"] = "WS"
    if not state.get("ws_ready"):
        state["ws_ready"] = True
        logger.info(f"[WS] FIRST TICK RECEIVED - Price: {price} | ws_ready=True [PIPELINE ENFORCEMENT]")
    _update_pending_order_price_extremes(price)
    with trade_lock:
        has_open = any(
            isinstance(p, dict) and p.get("status") == "OPEN" for p in open_positions
        )
        has_pending = any(
            isinstance(o, dict) and o.get("status") == "PENDING" for o in pending_orders
        )
    if has_pending:
        process_pending_orders()
    if has_open:
        process_positions()

def safe_ws_handler(message):
    try:
        logger.debug(f"[WS RAW] Received message length: {len(message)}")
        data = json.loads(message)
        global last_ws_message_time
        last_ws_message_time = time.time()
        if isinstance(data, dict):
            ev = data.get("event")
            if ev in ("info", "subscribed", "pong"):
                if ev == "subscribed" and data.get("channel") == "trades":
                    with state_lock:
                        state["ws_connected_ts"] = time.time()
                    logger.info(f"[WS] Subscribed trades chanId={data.get('chanId')} symbol={data.get('symbol')}")
                return
            if ev == "error":
                logger.error(f"[WS] Bitfinex error: {data}")
                return
            return
        trades = _bitfinex_ws_trades_from_message(data)
        if not trades:
            return
        _agent_dbg("H2", "safe_ws_handler", "batch", {"trades_in_msg": len(trades), "msg_len": len(message)})
        for trade in trades:
            _process_ws_trade_tick(trade)
    except IndexError as ie:
        logger.error(f"[WS FIX] deque underflow prevented: {ie}")
        return
    except Exception as e:
        logger.critical(f"[WS FATAL] {e}")
        set_execution_paused("THREAD_CRASH")

def on_message(ws, message):
    try:
        safe_ws_handler(message)
    except Exception as e:
        logger.error(f"[WS FATAL] on_message: {e}")

def on_open(ws):
    global ws_alive, ws_app
    logger.info(f"WS: Connected — subscribing Bitfinex trades {BITFINEX_WS_SYMBOL}")
    ws.send(json.dumps({"event": "subscribe", "channel": "trades", "symbol": BITFINEX_WS_SYMBOL}))
    with state_lock:
        state["ws_connected_ts"] = time.time()
    threading.Thread(target=ping_ws, args=(ws,), daemon=True).start()

def on_error(ws, error):
    logger.error(f"WS error: {error}")

def on_close(ws, code, reason):
    logger.warning(f"WS closed: code={code}, reason={reason}")
    ws_sock = None
    global ws_alive
    ws_alive = False
    # Log only — do not pause execution on brief disconnects (watchdog handles stale/reconnect)

def start_websocket():
    global ws_app, ws_alive
    ws_url = BITFINEX_WS_URL
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
            ws.send(json.dumps({"event": "ping"}))
        except Exception:
            break

def ws_watchdog():
    global ws_app, ws_reconnecting, last_ws_reconnect, ws_alive, ws_stale_count, ws_retry
    try:
        while not shutdown_event.is_set():
            time.sleep(3)
            price_ts = state.get("price_ts")
            if price_ts is None:
                continue
            age = time.time() - price_ts
            if age > WATCHDOG_WS_STALE_SEC:
                if not ws_reconnecting:
                    ws_reconnecting = True
                    ws_stale_count = ws_stale_count + 1
                    logger.warning(f"[WS] STALE DETECTED age={fmt(age)}s (count={ws_stale_count}) — nudging reconnect")
                    with ws_lock:
                        if ws_app:
                            try:
                                ws_app.keep_running = False
                                if ws_app.sock:
                                    ws_app.sock.close()
                                ws_app.close()
                            except Exception:
                                pass
                    ws_retry = min(ws_retry + 1, 8)
                    time.sleep(min(ws_retry * 2, 30))
            elif ws_reconnecting:
                ws_reconnecting = False
                ws_retry = 1
            else:
                if state.get("execution_reason") == "STALE_DATA_HARD_STOP":
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
                state["last_heartbeat"] = time.time()
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
                    state["data_source"] = "ws_ready_waiting_bitfinex"
                elif state["ohlcv_ready"]:
                    state["data_source"] = "ws_ready_waiting_bitfinex"
                else:
                    state["data_source"] = "booting_bitfinex"
                ws_tick = state.get("ws_last_tick")
                ws_age = (time.time() - ws_tick) if ws_tick else 0
                state["diag"]["ws_status"] = "OK" if (ws_tick and ws_age < STALE_HARD_SEC) else ("BOOTING" if not ws_tick else "STALE")
                engine_age = time.time() - last_ohlcv_fetch
                state["diag"]["engine_status"] = "OK" if engine_age < 300 else "STALE"
                state["diag"]["ai_status"] = "OK" if state["ai_enabled"] else "OFF"
                if ws_age > STALE_HARD_SEC:
                    state["ws_stale_count"] = state.get("ws_stale_count", 0) + 1
                else:
                    state["ws_stale_count"] = 0
                if state["ws_stale_count"] > 3:
                    logger.error(
                        "[PIPELINE] WS DEAD -> THREAD_CRASH (monitor keeps running; WS watchdog will reconnect) "
                        "[PIPELINE ENFORCEMENT]"
                    )
                    set_execution_paused("THREAD_CRASH")
                    state["ws_stale_count"] = 0
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
                    age_from_start = time.time() - buf.get("start_ts", 0)
                    is_deferred_shadow = buf.get("lane") in ("shadow", "shadow_deferred") and buf.get("block_reason")
                    if len(buf.get("ticks", [])) >= 2000:
                        expired_ids.append(tid)
                    elif is_deferred_shadow and age_from_start > SHADOW_REPLAY_TTL_SEC:
                        expired_ids.append(tid)
                    elif not is_deferred_shadow and time.time() - buf.get("last_update", buf.get("start_ts", 0)) > REPLAY_TTL_SEC:
                        expired_ids.append(tid)
                if len(replay_buffers) > MAX_REPLAY_BUFFERS:
                    sorted_ids = sorted(replay_buffers, key=lambda k: replay_buffers[k].get("start_ts", 0))
                    excess = len(replay_buffers) - MAX_REPLAY_BUFFERS
                    for tid in sorted_ids[:excess]:
                        dump_replay(tid)
                        replay_buffers.pop(tid, None)
            for tid in expired_ids:
                with replay_lock:
                    buf = replay_buffers.get(tid)
                    if buf and buf.get("lane") not in ("executed",) and buf.get("lane") != "shadow_blocked":
                        finalize_shadow_research(tid, buf.get("block_reason") or "SHADOW_TTL")
                    else:
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
                elif _sole_ai_research_mode() and ai_cooldown_remaining_sec() == 0:
                    features = build_full_feature_snapshot()
                    if features:
                        edge_score = compute_edge_score(features)
                        if round(edge_score, 1) > 0.0:
                            process_signal({
                                "event_trigger": True,
                                "edge_trigger_reason": "PERIODIC_RESEARCH_AI",
                                "edge_score": round(edge_score, 1),
                                "price": nz(state.get("price")),
                                "timestamp": utc_iso(),
                                "features": features,
                            })
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
        pullback_pct = float(state.get("pullback_threshold", 0.002))
        use_instant = pullback_pct <= 0.0
        if use_instant or not state.get("allow_compression", True):
            execute_market_order(signal)
            with trade_lock:
                filled = any(p.get("trade_id") == signal.get("trade_id") for p in open_positions)
            if not filled:
                logger.error(f"[EXECUTION FAIL HARD] Market fill missing position trade_id={signal.get('trade_id')}")
                exit_pipeline(signal, ai, "ORDER_NOT_RECORDED")
                return False
        else:
            order = create_limit_order(signal)
            if order is None:
                logger.error("[EXECUTION] create_limit_order failed")
                exit_pipeline(signal, ai, "ORDER_CREATION_FAILED")
                return False
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
        pos = _build_open_position(order, signal, signal.get("ai", {}))
        open_positions.append(pos)
    mark_approve_research_executed(pos.get("trade_id"), price)
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
    pullback_pct = signal.get("pullback_pct", state.get("pullback_threshold", 0.002))
    signal_price = signal.get("signal_price", price)
    limit_price, entry_mode = resolve_entry_limit_price(signal)
    signal["entry_mode"] = entry_mode
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
        "entry_mode": entry_mode,
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
    logger.info(
        f"[ORDER CREATED] trade_id={signal.get('trade_id')} signal_price={fmt(signal_price)} "
        f"limit_price={fmt(limit_price)} entry_mode={entry_mode} pullback={pullback_pct*100}% "
        f"final_direction={signal.get('final_direction')} [PIPELINE ENFORCEMENT]"
    )
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
            tick_all_replay_buffers(price)

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
            time.sleep(_position_monitor_interval_sec())
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
        exit_is_maker = (
            pos.get("exit_fee_type") == "MAKER"
            or exit_reason in ("TAKE_PROFIT", "PROFIT_LOCK_LADDER", "TP_HIT")
            or ("PROFIT" in exit_reason and "FAST" not in exit_reason)
            or "POSTONLY" in exit_reason
        )
        maker_fee, taker_fee = get_trading_fee_rates()
        entry_fee = position_value_entry * (maker_fee if entry_is_maker else taker_fee)
        exit_fee = position_value_exit * (maker_fee if exit_is_maker else taker_fee)
        trading_fees = entry_fee + exit_fee
        accrue_position_funding(pos, time.time())
        funding_total = round(float(pos.get("funding_fees", 0.0) or 0.0), 4)
        total_fees = trading_fees
        net_pnl = gross_pnl - trading_fees - funding_total

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
            "trading_fees_usd": round(trading_fees, 2),
            "fees_usd": round(trading_fees, 2),
            "funding_fees_usd": round(funding_total, 2),
            "total_cost_usd": round(trading_fees + funding_total, 2),
            "exit_reason": exit_reason,
            "leverage": pos.get("leverage", 20),
            "r_multiple": round(r_multiple, 2),
            "ai_win_prob": pos.get("ai_win_prob") or master.get("ai_win_prob"),
            "ai_threshold": get_ai_threshold(),
            "ai_approved": pos.get("ai_approved", master.get("ai_decision") == "APPROVE"),
            "entry_type": pos.get("entry_type", "UNKNOWN"),
            "entry_mode": pos.get("entry_mode", master.get("entry_mode", ENTRY_MODE_PULLBACK)),
            "ema_hybrid_base": pos.get("ema_hybrid_base", master.get("ema_hybrid_base")),
            "ema_hybrid_limit": pos.get("ema_hybrid_limit", master.get("ema_hybrid_limit")),
            "ema_hybrid_offset_usd": pos.get("ema_hybrid_offset_usd", master.get("ema_hybrid_offset_usd")),
            "dist_to_ema_hybrid_pct": pos.get("dist_to_ema_hybrid_pct", master.get("dist_to_ema_hybrid_pct")),
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
            **csv_research_meta(),
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
            "outcome_trading_fees_usd": round(trading_fees, 2),
            "outcome_fees_usd": round(trading_fees, 2),
            "outcome_funding_fees_usd": round(funding_total, 2),
            "outcome_total_cost_usd": round(trading_fees + funding_total, 2),
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
            "final_direction": pos.get("dir"),
            **{f"cfg_{k}": v for k, v in get_exit_config_snapshot().items() if not isinstance(v, (list, tuple))},
            "cfg_trail_ladder_json": json.dumps(TRAIL_LADDER),
            "exit_config_json": json.dumps(get_exit_config_snapshot()),
        }
        open_positions.remove(pos)
        close_replay_buffer(trade_id)
        log_trade_outcome_jsonl(trade_row, pos)
        trades.append(trade_row)
        validate_state()
        try:
            log_trade(trade_row)
            logger.info(
                f"[CSV] Trade logged reason={exit_reason} trade_id={trade_id} net_pnl={fmt(net_pnl)} "
                f"gross={fmt(gross_pnl)} trading_fees={fmt(trading_fees)} funding={fmt(funding_total)} "
                f"profile={EXCHANGE_FEE_PROFILE} final_direction={pos.get('dir')} [PIPELINE ENFORCEMENT]"
            )
            persist_signal_close(trade_id, "CLOSED")
            state["account_balance"] += net_pnl
            recent_trades.append({"pnl": net_pnl,"win": net_pnl > 0,"regime": trade_row.get("regime", "UNKNOWN"),"setup": trade_row.get("strategy", "SR")})
        except Exception as e:
            logger.error(f"[CSV ERROR] {e}")
        apply_trade_pnl(trade_row)
        _record_research_trade_close(net_pnl, exit_reason)

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
api_key = os.getenv("BITFINEX_API_KEY", "").strip()
api_secret = os.getenv("BITFINEX_API_SECRET", "").strip()
bitfinex_public = ccxt.bitfinex({"enableRateLimit": True})
MARKETS = bitfinex_public.load_markets()
bitfinex_private = ccxt.bitfinex({
    "apiKey": api_key,
    "secret": api_secret,
    "enableRateLimit": True,
})
tick_prices = deque(maxlen=300)
price_seq = 0
logger = logging.getLogger("3factor-bot")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s'))
LOG_FILE = os.getenv("BOT_LOG_FILE", "bot_runtime.log")
file_handler = SafeRotatingFileHandler(
    LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s'))
file_handler.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(stream_handler)
logger.addHandler(file_handler)

FRESH_COLLECTION_MAINTAIN_INTERVAL_SEC = 3600
_last_fresh_maintain_ts = 0.0

def research_wipe_file_paths():
    """Research artifacts that can grow without bound between analyzer runs."""
    paths = [
        CSV_DECISIONS, CSV_TRADES, CSV_EXPIRED, CSV_BLOCKS, CSV_AI_TRANCHE, CSV_SETUP_LOG, CSV_CANDLES,
        CSV_PIPELINE_EVENTS, CSV_AI_ERRORS,
        "signal_snapshot.jsonl", "signal_replay.jsonl", "trade_outcome.jsonl", "shadow_outcome.jsonl", "counterfactual.jsonl",
        EDGE_CENSUS_FILE,
        "near_edge.log", "signal_persist.log", "crash_dump.json", POSITIONS_FILE,
        CONFIG_FILE, POLICY_FILE, RESEARCH_SESSION_FILE,
        _AGENT_DEBUG_LOG, _AGENT_DEBUG_LOG_ALT,
    ]
    return paths

def _delete_paths(paths) -> tuple:
    deleted = []
    errors = []
    seen = set()
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            if os.path.exists(path):
                os.remove(path)
                deleted.append(path)
        except Exception as e:
            errors.append(f"{path}: {e}")
    return deleted, errors

def _reset_runtime_log_handlers():
    """Truncate the rotating runtime log without restarting the process."""
    for h in logger.handlers:
        if not isinstance(h, RotatingFileHandler):
            continue
        try:
            with h.lock:
                if h.stream:
                    h.stream.close()
                    h.stream = None
                for i in range(LOG_BACKUP_COUNT + 5, -1, -1):
                    path = h.baseFilename if i == 0 else f"{h.baseFilename}.{i}"
                    if os.path.exists(path):
                        os.remove(path)
                h.stream = h._open()
        except Exception as e:
            logger.error(f"[FRESH COLLECTION] Log handler reset failed: {e} [PIPELINE ENFORCEMENT]")

def reset_all_research_files() -> tuple:
    deleted, errors = _delete_paths(research_wipe_file_paths())
    return deleted, errors

def maintain_fresh_collection_files():
    """Periodic trim when fresh-collection mode is on (prevents silent re-accumulation)."""
    prune_aux_logs_on_startup()
    log_path = os.getenv("BOT_LOG_FILE", "bot_runtime.log")
    if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_MAX_BYTES:
        _reset_runtime_log_handlers()

def perform_fresh_collection_reset() -> dict:
    """Dashboard-triggered wipe: delete research files, reset session memory, keep bot running."""
    global bot_start_time, _last_fresh_maintain_ts
    logger.warning("[FRESH COLLECTION] Reset requested — wiping research files and session state")
    with replay_lock:
        replay_buffers.clear()
    with trade_lock:
        pending_orders.clear()
        expired_orders.clear()
        open_positions.clear()
        trades_map.clear()
        trades.clear()
        recent_trades.clear()
    deleted, errors = reset_all_research_files()
    _reset_runtime_log_handlers()
    reset_runtime_state()
    reset_session_risk_state()
    bot_start_time = time.time()
    _last_fresh_maintain_ts = time.time()
    summary = f"deleted {len(deleted)} file(s)" + (f", {len(errors)} error(s)" if errors else "")
    with state_lock:
        state["fresh_collection_mode"] = True
        state["last_fresh_reset_ts"] = time.time()
        state["last_fresh_reset_summary"] = summary
        state["execution_paused"] = False
        state["execution_reason"] = ""
        state["_pause_priority"] = 0
    save_persistent_config()
    logger.info(f"[FRESH COLLECTION] Reset complete — {summary} [PIPELINE ENFORCEMENT]")
    return {"deleted": deleted, "errors": errors, "summary": summary, "ts": utc_iso()}

replay_buffers: Dict[str, Dict] = {}
MAX_REPLAY_BUFFERS = 100
SIGNAL_SNAPSHOT_FILE = "signal_snapshot.jsonl"
EDGE_CENSUS_FILE = "edge_census.jsonl"
SIGNAL_REPLAY_FILE = "signal_replay.jsonl"
TRADE_OUTCOME_FILE = "trade_outcome.jsonl"
SHADOW_OUTCOME_FILE = "shadow_outcome.jsonl"
COUNTERFACTUAL_FILE = "counterfactual.jsonl"
_sim_processed_trade_ids: set = set()
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
        with state_lock:
            hb_ts = state.get("last_heartbeat") or last_heartbeat
            ws_ts = state.get("ws_last_tick") or 0
        hb_age = time.time() - hb_ts
        ws_age = time.time() - ws_ts
        if hb_age > WATCHDOG_HEARTBEAT_STALE_SEC and ws_age > WATCHDOG_WS_STALE_SEC:
            logger.critical(
                f"[WATCHDOG] Pipeline stale {hb_age:.0f}s (WS {ws_age:.0f}s) — dumping state [PIPELINE ENFORCEMENT]"
            )
            dump_system_state()
            dump_threads()
        time.sleep(5)

def dump_threads():
    for thread in threading.enumerate():
        logger.debug(f"[THREAD] {thread.name} alive={thread.is_alive()}")

def pipeline_heartbeat():
    global last_pipeline_run, last_heartbeat
    now = time.time()
    with state_lock:
        state["last_heartbeat"] = now
    last_heartbeat = now
    logger.debug("[HEARTBEAT] Pipeline alive")

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
    <title>3-Factor Research Bot — Bitfinex · __BOT_VERSION__</title>
    <script src="/static/dashboard.js?v=__BOT_VERSION__"></script>
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

<h1>3-Factor Research Bot — Bitfinex <span style="color:#3fb950;font-size:0.55em;vertical-align:middle;">__BOT_VERSION__</span></h1>
<p style="color:#8b949e;margin-top:0;">Bot build: <strong id="botVersionBanner">__BOT_VERSION__</strong> · Exchange: <strong>Bitfinex</strong> perp · Symbol: <span id="marketSymbol">tBTCF0:USTF0</span> · Research / sim mode · <a href="__DASHBOARD_URL__" style="color:#58a6ff;">__DASHBOARD_URL__</a></p>
<p id="serverBanner" style="background:#1f2937;border:1px solid #374151;padding:8px 12px;border-radius:6px;color:#8b949e;font-size:0.9em;">
  Server: checking…
</p>
<p>
    <button type="button" onclick="refresh()">Refresh now</button>
    <label style="margin-left:12px;"><input type="checkbox" id="autoRefreshToggle"> Auto-refresh every 60s (optional)</label>
    <span id="refreshStatus" style="margin-left:8px;color:#8b949e;">Manual refresh by default — click Refresh now or enable auto</span>
</p>

<div id="dashboardToggles" style="margin:12px 0;padding:10px 12px;background:#161b22;border:1px solid #30363d;border-radius:6px;">
    <strong style="color:#58a6ff;">Quick toggles</strong>
    <button onclick="toggleLive()">LIVE ARM: <span id="liveArmBtn">OFF</span></button>
    <button onclick="toggleEarlyFail()">Early Fail: <span id="earlyFailBtn">OFF</span></button>
    <button onclick="toggleInvert()">Invert Signal: <span id="invertBtn">OFF</span></button>
    <button onclick="toggleBlockFreeRange()">Block FREE_RANGE entries: <span id="blockFreeRangeBtn">ON</span></button>
    <button onclick="toggleGoldenStack()">Golden Stack gates: <span id="goldenStackBtn">ON</span></button>
    <button onclick="toggleDebug()">Debug Mode: <span id="debugToggle">OFF</span></button>
    <button id="freshCollectionBtn" onclick="toggleFreshCollection()" title="Wipe research CSVs/logs and reset session counters">Fresh Collection: <span id="freshCollectionLabel">OFF</span></button>
    <button onclick="downloadDebug()">Download Debug Logs</button>
    <button onclick="window.location.href='/api/export_csv'">Download CSV Logs</button>
</div>

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

<h3>AI Decision (current cycle)</h3>
<p><strong>AI Status:</strong> <span id="aiDecision">-</span> <span id="aiStatusNote" style="color:#8b949e;font-size:0.9em;"></span></p>
<p><strong>AI Win Prob:</strong> <span id="aiProb">-</span></p>
<p><strong>AI Direction (raw):</strong> <span id="aiDirRaw">-</span></p>
<p><strong>Final Direction (after invert):</strong> <span id="finalDir">-</span></p>
<p><strong>Inverted:</strong> <span id="inverted">-</span></p>
<p><strong>AI Threshold:</strong> <span id="aiThresholdDisplay">-</span>%</p>
<p><strong>Edge range (gate):</strong> <span id="edgeThresholdDisplay">-</span></p>
<p><strong>Last APPROVE Outcome:</strong> <span id="approveOutcome">-</span></p>
<p><strong>AI Reason:</strong> <span id="aiReason">-</span></p>

<h3>Exchange &amp; Cost Model (Research Sim)</h3>
<p><strong>Exchange:</strong> <span id="exchangeLabel">Bitfinex</span> · <strong>Fee profile:</strong> <span id="feeProfile">-</span> · <strong>Trading fees:</strong> <span id="tradingFeeRates">-</span></p>
<p><strong>Funding (live):</strong> <span id="fundingRateLive">-</span> · <strong>Source:</strong> <span id="fundingSource">-</span> · <strong>Next settlement (UTC):</strong> <span id="fundingNextSettle">-</span></p>
<p><strong>Funding meaning:</strong> <span id="fundingMeaning">-</span> · <strong>Open-interest:</strong> <span id="fundingOpenInterest">-</span></p>

<h3>System Status</h3>
<p id="why"></p>
<p><strong>Bot sync:</strong> <span id="botInstance">-</span></p>
<p>Last Fetch: <span id="lastFetch"></span></p>
<p>WS Age: <span id="ws_age"></span></p>

<div class="debug-panel">
    <h3>🔍 DEBUG STATE</h3>
    <p><strong>Last Check:</strong> <span id="lastCheckTime">-</span></p>
    <p><strong>Last Event:</strong> <span id="lastEventTime">-</span></p>
    <p><strong>Edge Score:</strong> <span id="edgeScore">-</span></p>
    <p><strong>Edge Progress (score / min required, max 6):</strong> <span id="edgeProgress">-</span></p>
    <p><strong>Flags:</strong> <span id="flags">-</span></p>
    <p><strong>Pipeline trigger (edge event):</strong> <span id="trigger">-</span></p>
    <p><strong>AI Gate (DeepSeek):</strong> <span id="aiGateStatus">-</span></p>
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

<p id="freshCollectionHint" style="color:#8b949e;font-size:0.85em;margin-top:8px;">
  <strong>Fresh Collection:</strong> turn ON to delete research CSVs, jsonl logs, debug/log files, and reset in-memory trades/session counters. The bot keeps running and starts collecting clean data. While ON, oversized aux logs are trimmed hourly.
</p>
<p id="freshCollectionStatus" style="color:#58a6ff;font-size:0.85em;margin-top:4px;"></p>

<h2>Controls</h2>
<p style="color:#8b949e;font-size:0.9em;">Changes apply live and save to config.json. Leverage / max positions update when you leave the field or press Enter.</p>
<label>Leverage (1–100x):</label><input id="leverage" type="number" min="1" max="100" value="100"><br>
<label>Pullback %:</label>
<select id="pullbackThresh">
  <option value="0.0">0.0% (instant)</option>
  <option value="0.1" selected>0.1%</option>
  <option value="0.2">0.2%</option>
  <option value="0.3">0.3%</option>
  <option value="0.4">0.4%</option>
  <option value="0.5">0.5%</option>
  <option value="0.6">0.6%</option>
</select><br>
<label>Max concurrent signals:</label><input id="maxConcurrentPositions" type="number" min="1" max="20" value="3"><br>
<label>Min AI win % to execute (not the AI’s score):</label><input id="aiThreshold" type="number" min="0" max="100" value="68" onchange="updateThreshold(this.value)"><br>
<label>Edge range preset:</label>
<select id="edgeRangePreset">
  <option value="min_only" selected>Any ≥ min (no upper cap) — v80 collection</option>
  <option value="2.0_2.5">2.0 – 2.5 (sweet spot experiment)</option>
  <option value="2.0_3.0">2.0 – 3.0</option>
  <option value="2.5_3.5">2.5 – 3.5</option>
  <option value="3.0_4.0">3.0 – 4.0</option>
  <option value="custom">Custom min / max</option>
</select><br>
<div id="edgeCustomRange" style="display:none;margin:6px 0;padding:8px;border:1px solid #30363d;border-radius:6px;">
<label>Min edge:</label>
<select id="edgeThreshold">
  <option value="0.5" selected>0.5</option>
  <option value="1.0">1.0</option>
  <option value="1.5">1.5</option>
  <option value="2.0">2.0</option>
  <option value="2.5">2.5</option>
  <option value="3.0">3.0</option>
  <option value="3.5">3.5</option>
  <option value="4.0">4.0</option>
  <option value="4.5">4.5</option>
  <option value="5.0">5.0</option>
  <option value="5.5">5.5</option>
  <option value="6.0">6.0</option>
</select>
<label style="margin-left:12px;">Max edge:</label>
<select id="edgeThresholdMax">
  <option value="no_cap" selected>No cap</option>
  <option value="2.0">2.0</option>
  <option value="2.5">2.5</option>
  <option value="3.0">3.0</option>
  <option value="3.5">3.5</option>
  <option value="4.0">4.0</option>
  <option value="4.5">4.5</option>
  <option value="5.0">5.0</option>
  <option value="5.5">5.5</option>
  <option value="6.0">6.0</option>
</select>
</div>
<p style="color:#8b949e;font-size:0.85em;margin:4px 0;">Only signals with edge inside the range trigger AI. High edge (e.g. 3.5+) is blocked when max is set.</p>
<p id="executionGateHint" style="margin:8px 0;color:#8b949e;">—</p>

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
    <thead><tr><th>Time</th><th>ID</th><th>Dir (final)</th><th>Entry</th><th>Exit</th><th>Duration min</th><th>PnL %</th><th>Net USD</th><th>Gross USD</th><th>Trade Fees</th><th>Funding</th><th>AI Band</th></tr></thead>
    <tbody id="tradesTable"></tbody>
</table>

<h2>AI History (Session)</h2>
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
    function formatMelbourneDateTime(ts) {
      if (!ts || ts === '-') return '-';
      try {
        const d = new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z');
        if (isNaN(d.getTime())) return ts;
        const parts = new Intl.DateTimeFormat('en-AU', {
          timeZone: 'Australia/Melbourne',
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', hour12: false
        }).formatToParts(d);
        const get = (t) => (parts.find(p => p.type === t) || {}).value || '';
        return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')} Melbourne`;
      } catch (e) { return ts; }
    }
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
    function normalizeEdgeOptionValue(value) {
      const n = parseFloat(value);
      if (Number.isNaN(n)) return null;
      return n.toFixed(1);
    }
    function syncEdgeThresholdSelect(threshold) {
      const edgeSel = document.getElementById('edgeThreshold');
      if (!edgeSel || threshold == null || threshold === '') return;
      const normalized = normalizeEdgeOptionValue(threshold);
      if (!normalized) return;
      edgeSel.value = normalized;
      if (!edgeSel.value) {
        const opt = document.createElement('option');
        opt.value = normalized;
        opt.textContent = normalized;
        edgeSel.appendChild(opt);
        edgeSel.value = normalized;
      }
    }
    function syncEdgeThresholdMaxSelect(maxVal) {
      const maxSel = document.getElementById('edgeThresholdMax');
      if (!maxSel) return;
      if (maxVal == null || maxVal === '' || maxVal === undefined) {
        maxSel.value = 'no_cap';
        return;
      }
      const normalized = normalizeEdgeOptionValue(maxVal);
      if (!normalized) return;
      maxSel.value = normalized;
      if (!maxSel.value) maxSel.value = 'no_cap';
    }
    function toggleEdgeCustomPanel(presetId) {
      const panel = document.getElementById('edgeCustomRange');
      if (panel) panel.style.display = presetId === 'custom' ? 'block' : 'none';
    }
    function syncEdgeRangePreset(presetId) {
      const presetSel = document.getElementById('edgeRangePreset');
      if (!presetSel || !presetId) return;
      presetSel.value = presetId;
      if (!presetSel.value) {
        const opt = document.createElement('option');
        opt.value = presetId;
        opt.textContent = presetId;
        presetSel.appendChild(opt);
        presetSel.value = presetId;
      }
      toggleEdgeCustomPanel(presetId);
    }
    async function updateEdgeRangePreset(presetId) {
      await post('/api/set_edge_range', {preset: presetId});
      syncEdgeRangePreset(presetId);
      refresh();
    }
    async function updateEdgeRangeCustom() {
      const minSel = document.getElementById('edgeThreshold');
      const maxSel = document.getElementById('edgeThresholdMax');
      const minVal = minSel ? parseFloat(normalizeEdgeOptionValue(minSel.value)) : null;
      const maxRaw = maxSel ? maxSel.value : 'no_cap';
      const maxVal = maxRaw === 'no_cap' ? null : parseFloat(normalizeEdgeOptionValue(maxRaw));
      await post('/api/set_edge_range', {preset: 'custom', min: minVal, max: maxVal});
      syncEdgeRangePreset('custom');
      refresh();
    }
    async function updateEdge(value) {
      const normalized = normalizeEdgeOptionValue(value);
      if (!normalized) return;
      await post('/api/set_edge_threshold', {value: parseFloat(normalized)});
      syncEdgeThresholdSelect(normalized);
      syncEdgeRangePreset('custom');
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
    async function toggleBlockFreeRange() {
      await post('/api/toggle_block_free_range_entries');
      refresh();
    }
    async function toggleGoldenStack() {
      await post('/api/toggle_golden_stack');
      refresh();
    }
    async function toggleDebug() {
      const cur = document.getElementById('debugToggle').innerText.includes('OFF');
      await post('/api/toggle_debug', {enabled: cur});
      refresh();
    }
    async function toggleFreshCollection() {
      const turningOn = document.getElementById('freshCollectionLabel').innerText.includes('OFF');
      if (turningOn) {
        const ok = confirm(
          'Fresh Collection will DELETE all research CSVs, jsonl logs, debug/log files, and clear open/pending trades in memory.\\n\\nThe bot keeps running and starts collecting from zero.\\n\\nContinue?'
        );
        if (!ok) return;
      }
      const res = await post('/api/toggle_fresh_collection', {enabled: turningOn});
      try {
        const body = await res.json();
        if (body.error) {
          alert('Fresh Collection blocked: ' + body.error);
        } else if (body.reset && body.reset.summary) {
          alert('Fresh collection reset complete: ' + body.reset.summary);
        }
      } catch (e) {}
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
        if (rs) rs.innerText = 'Last updated ' + new Date().toLocaleTimeString() + ' (live)';
        const sb = document.getElementById('serverBanner');
        if (sb) {
          const pid = d.bot_pid;
          const cd = d.ai_cooldown_remaining_sec;
          let txt = pid
            ? 'LIVE Python bot PID ' + pid + ' · cwd ' + (d.bot_cwd || '-') + ' · DeepSeek ' + (d.deepseek_key_present ? 'OK' : 'MISSING')
            : 'LIVE (PID unknown — restart bot)';
          if (d.server_ts) txt += ' · server ' + d.server_ts;
          if (cd != null && cd > 0) txt += ' · AI cooldown ' + cd + 's';
          if (d.dashboard_url) txt += ' · URL ' + d.dashboard_url;
          txt += ' · Stop: run stop_bot.ps1 in Final Bots (closing CMD alone may not stop a background bot)';
          sb.style.borderColor = '#238636';
          sb.style.color = '#c9d1d9';
          sb.innerText = txt;
        }
        const src = document.getElementById('dataSource');
        const banner = document.getElementById('dataBanner');
        if (src && banner) {
          if (d.execution_paused) {
            src.innerHTML = 'PAUSED - ' + (d.execution_reason || 'Unknown reason');
            src.className = 'text-red-500 font-bold animate-pulse';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-green-600';
          } else if (d.price_source === 'WS') {
            src.innerHTML = d.data_banner_ws || ('REAL BITFINEX MARKET DATA (WS) · ' + (d.market_symbol || 'tBTCF0:USTF0'));
            src.className = 'text-green-400 font-bold';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-green-600';
          } else if (d.price_source === 'REST') {
            src.innerHTML = d.data_banner_rest || ('BITFINEX REST FALLBACK · ' + (d.market_symbol || 'tBTCF0:USTF0'));
            src.className = 'text-orange-400 font-bold';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-orange-600';
          } else {
            src.innerHTML = d.data_banner_boot || ('BITFINEX CONNECTING · ' + (d.market_symbol || 'tBTCF0:USTF0'));
            src.className = 'text-yellow-400 font-bold';
            banner.className = 'bg-gray-800 p-6 rounded-lg shadow mb-8 border-4 border-yellow-600';
          }
        }
        const inst = document.getElementById('botInstance');
        if (inst) {
          const pid = d.bot_pid;
          const keyOk = d.deepseek_key_present;
          const weakMin = d.weak_setup_min_edge;
          let syncTxt = keyOk ? 'DeepSeek key OK' : 'DeepSeek key MISSING (.env)';
          if (pid) syncTxt = 'PID ' + pid + ' | ' + syncTxt;
          else syncTxt = 'PID unknown — restart bot to sync dashboard | ' + syncTxt;
          if (weakMin != null) syncTxt += ' | WEAK_SETUP min edge ' + weakMin;
          const cd = d.ai_cooldown_remaining_sec;
          if (cd != null && cd > 0) syncTxt += ' | AI cooldown ' + cd + 's / ' + (d.ai_cooldown_sec || 300) + 's';
          if (d.bot_cwd) syncTxt += ' | ' + d.bot_cwd;
          if (d.fee_profile) syncTxt += ' | fees=' + d.fee_profile;
          if (d.bot_version) syncTxt += ' | ' + d.bot_version;
          if (d.analyzer_sync_id) syncTxt += ' | ' + d.analyzer_sync_id;
          if (d.block_free_range_entries === false) syncTxt += ' | FREE_RANGE block OFF';
          if (d.golden_stack_enabled === false) syncTxt += ' | Golden Stack OFF';
          else syncTxt += ' | Golden Stack ON';
          inst.innerText = syncTxt;
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
        safeText('exchangeLabel', d.exchange_label || 'Bitfinex');
        const fp = d.fee_profile || 'BITFINEX_ZERO';
        safeText('feeProfile', fp);
        const mfr = d.maker_fee_pct != null ? (d.maker_fee_pct * 100).toFixed(4) : '0';
        const tfr = d.taker_fee_pct != null ? (d.taker_fee_pct * 100).toFixed(4) : '0';
        safeText('tradingFeeRates', mfr + '% maker / ' + tfr + '% taker (sim)');
        const fund = d.funding || {};
        const frPct = fund.rate_pct_per_8h != null ? fund.rate_pct_per_8h : (fund.rate != null ? (fund.rate * 100).toFixed(5) : '-');
        safeText('fundingRateLive', frPct !== '-' ? frPct + '% per 8h' : '-');
        safeText('fundingSource', fund.source || '-');
        safeText('fundingNextSettle', fund.next_time_iso || (fund.next_time ? new Date(fund.next_time * 1000).toISOString() : '-'));
        let fMean = 'neutral';
        if (fund.rate > 0) fMean = 'positive rate → longs pay, shorts receive';
        else if (fund.rate < 0) fMean = 'negative rate → shorts pay, longs receive';
        safeText('fundingMeaning', fMean);
        safeText('fundingOpenInterest', fund.open_interest != null ? fund.open_interest.toLocaleString() : '-');

        const sr = d.support_resistance || {};
        safeText('swingHigh', sr.swing_high != null ? sr.swing_high.toLocaleString() : '-');
        safeText('swingLow', sr.swing_low != null ? sr.swing_low.toLocaleString() : '-');
        safeText('distRes', sr.dist_to_resistance != null ? (sr.dist_to_resistance*100).toFixed(2) : '-');
        safeText('distSup', sr.dist_to_support != null ? (sr.dist_to_support*100).toFixed(2) : '-');
        safeText('srZone', sr.sr_zone_pct != null ? (sr.sr_zone_pct*100).toFixed(2) : '-');
        safeText('srState', sr.sr_state || '-');
        safeText('srBias', sr.sr_bias || '-');

        const dai = d.display_ai || {};
        const aiCalled = !!dai.gate_called;
        let aiStatusTxt = 'NO AI CALL (this cycle)';
        if (aiCalled) {
          aiStatusTxt = dai.status || d.last_ai?.decision || d.ai_outcome || 'UNKNOWN';
          if (aiStatusTxt === 'AI_ERROR') {
            aiStatusTxt = 'AI ERROR (API)';
          }
        } else if (dai.status && dai.status !== 'NO_AI_CALL') {
          aiStatusTxt = dai.status;
        }
        safeText('aiDecision', aiStatusTxt);
        safeText('aiStatusNote', dai.note || '');
        const aiProbEl = document.getElementById('aiProb');
        if (aiProbEl) {
          aiProbEl.innerText = aiCalled && dai.win_prob != null
            ? dai.win_prob + '%'
            : (aiCalled && d.last_ai?.win_prob != null ? d.last_ai.win_prob + '%' : '-');
        }
        const msym = document.getElementById('marketSymbol');
        if (msym && d.market_symbol) msym.innerText = d.market_symbol;
        safeText('aiDirRaw', d.last_ai?.direction || '-');
        safeText('finalDir', d.last_ai?.final_direction || '-');
        safeText('inverted', d.last_ai?.inverted ? 'YES' : 'NO');
        safeText('aiThresholdDisplay', d.ai_threshold != null ? d.ai_threshold : 'WAITING');
        const edgeBase = d.edge_threshold != null ? normalizeEdgeOptionValue(d.edge_threshold) : '2.0';
        const edgeMax = d.edge_threshold_max;
        const edgeEff = d.debug_state?.edge_components?.effective_threshold;
        const flatFloor = d.debug_state?.edge_components?.flat_momentum_floor;
        let edgeDisp = edgeMax != null && edgeMax !== ''
          ? edgeBase + ' – ' + normalizeEdgeOptionValue(edgeMax)
          : edgeBase + '+';
        if (d.edge_range_preset) edgeDisp += ' (' + d.edge_range_preset + ')';
        if (edgeEff != null) {
          edgeDisp += ' | eff min ' + normalizeEdgeOptionValue(edgeEff);
          if (flatFloor != null && flatFloor > parseFloat(edgeBase)) edgeDisp += ', flat floor ' + normalizeEdgeOptionValue(flatFloor);
        }
        safeText('edgeThresholdDisplay', edgeDisp);
        const lao = d.last_approve_outcome || {};
        const approveEl = document.getElementById('approveOutcome');
        if (approveEl) {
          if (lao.status === 'EXECUTED') {
            approveEl.innerHTML = '<span class="green">EXECUTED — trade placed' + (lao.trade_id ? ' (' + lao.trade_id.slice(0,8) + '…)' : '') + '</span>';
          } else if (lao.status === 'BLOCKED') {
            approveEl.innerHTML = '<span class="red">BLOCKED (' + (lao.reason || '?') + ', eff=' + (lao.effective_threshold != null ? lao.effective_threshold : '-') + ', edge=' + (lao.edge_at_approve != null ? lao.edge_at_approve : '-') + ')</span>';
          } else if (lao.status === 'PENDING') {
            approveEl.innerHTML = '<span style="color:#fbbf24">PENDING execution checks…</span>';
          } else {
            approveEl.innerText = '-';
          }
        }
        const gate = document.getElementById('executionGateHint');
        if (gate) {
          const prob = d.last_ai?.win_prob;
          const thr = d.ai_threshold;
          const dec = aiCalled ? (dai.status || d.last_ai?.decision) : null;
          if (dec === 'APPROVE') {
            if (lao.status === 'EXECUTED') {
              gate.innerHTML = `<span class="green">Last APPROVE ${prob}% → EXECUTED</span>`;
            } else if (lao.status === 'BLOCKED') {
              gate.innerHTML = `<span class="red">Last APPROVE ${prob}% → BLOCKED: ${lao.reason || '?'} (eff=${lao.effective_threshold ?? '-'}, edge=${lao.edge_at_approve ?? '-'})</span>`;
            } else if (prob != null && thr != null) {
              const ok = prob >= thr;
              gate.innerHTML = ok
                ? `<span class="green">Last APPROVE: AI ${prob}% ≥ min ${thr}% → passing execution gates…</span>`
                : `<span class="red">Last APPROVE: AI ${prob}% &lt; min ${thr}% → blocked (BELOW_THRESHOLD)</span>`;
            }
          } else if (!aiCalled) {
            gate.innerHTML = `<span style="color:#8b949e">DeepSeek not called this cycle — ${dai.note || skipBlk.skip || 'waiting for edge ≥ threshold'}</span>`;
          } else {
            gate.innerHTML = `<span style="color:#8b949e">Min win % to execute: ${thr != null ? thr : '-'} | Last AI: ${dec || '-'} ${prob != null ? prob + '%' : ''}</span>`;
          }
        }
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
        const blockFrBtn = document.getElementById('blockFreeRangeBtn');
        if (blockFrBtn) {
          const bfr = d.block_free_range_entries !== false;
          blockFrBtn.innerText = `Block FREE_RANGE entries ${bfr ? 'ON' : 'OFF'}`;
          blockFrBtn.style.backgroundColor = bfr ? '#10b981' : '#ef4444';
        }
        const gsBtn = document.getElementById('goldenStackBtn');
        if (gsBtn) {
          const gs = d.golden_stack_enabled !== false;
          gsBtn.innerText = gs ? 'ON' : 'OFF';
          gsBtn.style.backgroundColor = gs ? '#10b981' : '#ef4444';
        }
        const debugBtn = document.getElementById('debugToggle');
        if (debugBtn) {
          debugBtn.innerText = `Debug Mode (Console) ${d.debug_enabled ? 'ON' : 'OFF'}`;
          debugBtn.style.backgroundColor = d.debug_enabled ? '#10b981' : '#ef4444';
        }
        const freshBtn = document.getElementById('freshCollectionBtn');
        const freshLabel = document.getElementById('freshCollectionLabel');
        if (freshLabel) {
          freshLabel.innerText = d.fresh_collection_mode ? 'ON' : 'OFF';
        }
        if (freshBtn) {
          freshBtn.style.backgroundColor = d.fresh_collection_mode ? '#2563eb' : '#374151';
        }
        const freshStatus = document.getElementById('freshCollectionStatus');
        if (freshStatus) {
          if (d.last_fresh_reset_summary) {
            freshStatus.innerText = 'Last reset: ' + (d.last_fresh_reset_summary || '-') +
              (d.last_fresh_reset_ts ? ' @ ' + new Date(d.last_fresh_reset_ts * 1000).toLocaleString() : '');
          } else {
            freshStatus.innerText = d.fresh_collection_mode
              ? 'Auto-trim active — aux logs checked hourly'
              : '';
          }
        }
        if (d.leverage) {
          const lev = document.getElementById('leverage');
          if (lev) lev.value = d.leverage;
        }
        if (d.max_active_signals) {
          const mcp = document.getElementById('maxConcurrentPositions');
          if (mcp) mcp.value = d.max_active_signals;
        }
        if (d.ai_threshold != null && d.ai_threshold !== '') {
          const aiThresh = document.getElementById('aiThreshold');
          if (aiThresh) aiThresh.value = d.ai_threshold;
        }
        if (d.edge_range_preset) {
          syncEdgeRangePreset(d.edge_range_preset);
        }
        if (d.edge_threshold != null && d.edge_threshold !== '') {
          syncEdgeThresholdSelect(d.edge_threshold);
        }
        if (d.edge_threshold_max !== undefined) {
          syncEdgeThresholdMaxSelect(d.edge_threshold_max);
        }
        if (d.pullback_threshold != null && d.pullback_threshold !== '') {
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
            <td>$${(t.trading_fees_usd != null ? t.trading_fees_usd : t.fees_usd)?.toFixed(2)||'-'}</td>
            <td>$${(t.funding_fees_usd != null ? t.funding_fees_usd : t.funding_fees)?.toFixed(2)||'-'}</td>
            <td>${t.ai_band || '-'}</td>
          </tr>
        `).join(''));
        const aiHist = d.ai_history || [];
        safeHTML('aiHistoryTable', aiHist.length ? aiHist.map(a => {
          const prob = a.win_prob != null && a.win_prob !== '' ? Number(a.win_prob) : null;
          const c = a.comment || '';
          const cShort = c.length > 80 ? c.substring(0, 80) + '...' : (c || '-');
          return `
          <tr>
            <td>${a.melbourne_time || formatMelbourneDateTime(a.time || a.ts)}</td>
            <td>${a.trade_id || '-'}</td>
            <td>${a.ai_direction_raw || a.dir || '-'}</td>
            <td>${a.final_direction || a.dir || '-'}</td>
            <td>${a.inverted ? 'YES' : 'NO'}</td>
            <td>${a.decision || '-'}</td>
            <td>${prob != null && !Number.isNaN(prob) ? prob.toFixed(0) + '%' : '-'}</td>
            <td title="${c.replace(/"/g, '&quot;')}">${cShort}</td>
          </tr>`;
        }).join('') : '<tr><td colspan="8" style="color:#8b949e">No AI evaluations yet this session</td></tr>');
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
        const pipeTrig = dbg.pipeline_event_trigger;
        safeText('trigger', pipeTrig === true ? 'YES' : (pipeTrig === false ? 'NO' : (dbg.edge_above_threshold ? 'edge≥thr only' : '-')));
        const skipBlk = d.display_skip_block || {};
        const ag = dbg.ai_gate || {};
        const agTxt = ag.called
          ? ('CALLED — ' + (ag.reason || 'ok'))
          : ('NOT CALLED — ' + (dai.note || ag.reason || skipBlk.skip || 'waiting for edge'));
        safeText('aiGateStatus', agTxt);
        safeText('skipReason', skipBlk.skip != null ? skipBlk.skip : (dbg.skip_reason || '-'));
        safeText('signalAttempt', dbg.last_signal_attempt ? dbg.last_signal_attempt.time : '-');
        safeText('blockReason', skipBlk.block != null ? skipBlk.block : (dbg.last_block_reason || '-'));
        safeText('lastAICall', dbg.last_ai_call || '-');
        safeText('aiScore', dbg.last_ai_score || '-');
        safeText('signalCooldown', dbg.signal_cooldown_active ? 'ACTIVE (' + dbg.cooldown_remaining_signal + 's)' : 'READY');
        safeText('aiCooldown', dbg.ai_cooldown_active ? 'ACTIVE (' + dbg.cooldown_remaining_ai + 's)' : 'READY');
        safeText('lastPipeline', dbg.last_pipeline_stage || '-');
        const hbAge = d.heartbeat ? Math.round(Date.now()/1000 - d.heartbeat) : null;
        if (hbAge != null) {
          const hbStale = hbAge > 45;
          safeText('heartbeat', (hbStale ? 'STALE' : 'Alive') + ' (' + hbAge + 's ago)');
        } else {
          safeText('heartbeat', '-');
        }
        const dashPort = d.dashboard_port || __DASHBOARD_PORT__;
        const onWrongPort = (window.location.port && String(window.location.port) !== String(dashPort));
        safeText('aiInput', JSON.stringify(d.ai_input || {}) + (d.ai_input_time ? ' @ ' + d.ai_input_time : '') + ' (snapshot at last AI call)');
        safeText('features', JSON.stringify(d.feature_snapshot || {}) + ' (live — may differ from AI Input until next call)');
        if (onWrongPort) {
          const sb = document.getElementById('serverBanner');
          if (sb) {
            sb.style.borderColor = '#f85149';
            sb.style.color = '#f85149';
            sb.innerHTML = 'Wrong port :' + window.location.port + ' — use <strong>__DASHBOARD_URL__</strong> (bot listens on ' + dashPort + ')';
          }
        }
        safeText('dataQuality', (d.data_quality * 100).toFixed(1) + '%');
      } catch(e) {
        console.error("Refresh failed:", e);
        const rs = document.getElementById('refreshStatus');
        if (rs) rs.innerText = 'OFFLINE — no bot on this address (' + new Date().toLocaleTimeString() + ')';
        const sb = document.getElementById('serverBanner');
        if (sb) {
          sb.style.borderColor = '#f85149';
          sb.style.color = '#f85149';
          sb.innerHTML = 'BOT OFFLINE — nothing listening or network error. Use <strong>__DASHBOARD_URL__</strong> (not :5000 or old IPs). Start: <code>start_bot.ps1</code> · Stop: <code>stop_bot.ps1</code>';
        }
        const src = document.getElementById('dataSource');
        if (src) {
          src.innerHTML = 'OFFLINE — start bot or fix URL';
          src.className = 'text-red-500 font-bold';
        }
      } finally {
        refreshInFlight = false;
      }
    }
    document.addEventListener('DOMContentLoaded', () => {
      const dropdowns = {
        'leverage': '/api/set_leverage',
        'pullbackThresh': '/api/set_pullback_threshold',
        'maxConcurrentPositions': '/api/set_max_active_signals',
        'edgeThreshold': '/api/set_edge_range',
        'edgeThresholdMax': '/api/set_edge_range',
        'edgeRangePreset': '/api/set_edge_range'
      };
      function debounce(fn, ms) {
        let t;
        return function (...args) {
          clearTimeout(t);
          t = setTimeout(() => fn.apply(this, args), ms);
        };
      }
      Object.keys(dropdowns).forEach(id => {
        const el = document.getElementById(id);
        if (el) {
          const save = debounce(function() {
            post(dropdowns[id], {value: this.value});
            refresh();
          }, 400);
          el.addEventListener('change', function() {
            if (id === 'edgeRangePreset') {
              updateEdgeRangePreset(this.value);
              return;
            }
            if (id === 'edgeThreshold' || id === 'edgeThresholdMax') {
              updateEdgeRangeCustom();
              return;
            }
            const val = this.value;
            post(dropdowns[id], {value: val});
            refresh();
          });
          if (el.type === 'number') {
            el.addEventListener('input', save);
          }
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
    window.toggleBlockFreeRange = toggleBlockFreeRange;
    window.toggleDebug = toggleDebug;
    window.toggleFreshCollection = toggleFreshCollection;
    window.downloadDebug = downloadDebug;
    window.updateThreshold = updateThreshold;
    window.updateEdge = updateEdge;
  } catch (e) {
    console.error("DASHBOARD BOOT FAILURE", e);
  }
  console.info("dashboard.js loaded: true");
})();"""

@app.route('/static/dashboard.js')
def dashboard_js():
    js = (
        DASHBOARD_JS.replace("__DASHBOARD_PORT__", str(DASHBOARD_PORT))
        .replace("__DASHBOARD_URL__", dashboard_public_url())
    )
    return js, 200, {'Content-Type': 'application/javascript'}

@app.route('/')
def dashboard():
    page = (
        HTML.replace("__DASHBOARD_URL__", dashboard_public_url())
        .replace("__DASHBOARD_PORT__", str(DASHBOARD_PORT))
        .replace("__BOT_VERSION__", EXECUTION_FIX_VERSION)
    )
    return render_template_string(page)

@app.route('/api/state')
def api_state():
    try:
        refresh_funding_state()
        now_ts = time.time()
        with trade_lock:
            for pos in open_positions:
                if pos.get("status") == "OPEN":
                    accrue_position_funding(pos, now_ts)
        with state_lock:
            snapshot = copy.deepcopy(state)
            positions_copy = copy.deepcopy(open_positions)
            pending_orders_copy = copy.deepcopy(pending_orders)
            trades_copy = copy.deepcopy(trades)
            expired_orders_copy = copy.deepcopy(expired_orders)
            ai_history_copy = copy.deepcopy(state["ai_history"])
            trades_map_copy = copy.deepcopy(trades_map)
        ai_history_copy = _session_ai_history(ai_history_copy, 50)
        snapshot["ai_history"] = ai_history_copy
        snapshot["last_ai_best"] = _pick_dashboard_last_ai(snapshot, ai_history_copy)
        snapshot["deepseek_key_present"] = bool(_deepseek_api_key())
        snapshot["bot_pid"] = os.getpid()
        snapshot["bot_cwd"] = os.getcwd()
        snapshot["bot_script"] = os.path.abspath(__file__)
        snapshot["weak_setup_min_edge"] = get_weak_setup_min_edge()
        snapshot["ai_cooldown_sec"] = AI_COOLDOWN_SECONDS
        snapshot["ai_cooldown_remaining_sec"] = ai_cooldown_remaining_sec()
        snapshot["dashboard_port"] = DASHBOARD_PORT
        snapshot["dashboard_url"] = dashboard_public_url()
        snapshot["positions"] = []
        total_unreal = 0.0
        funding_snap = snapshot.get("funding") or {}
        for pos in positions_copy:
            pos_copy = copy.deepcopy(pos)
            pos_copy["current_price"] = snapshot["price"]
            dir_factor = 1 if pos["dir"] == "LONG" else -1
            move_pct = ((snapshot["price"] - pos["entry"]) / pos["entry"] * 100 * dir_factor) if snapshot["price"] and pos["entry"] else 0
            pnl_usd = (move_pct / 100) * FIXED_MARGIN_USDT * pos.get("leverage", 20)
            pnl_pct_margin = move_pct * pos.get("leverage", 20)
            funding_acc = round(float(pos.get("funding_fees", 0.0) or 0.0), 4)
            pos_copy["pnl_pct_margin"] = pnl_pct_margin
            pos_copy["unreal_usd_gross"] = pnl_usd
            pos_copy["funding_fees_accrued"] = funding_acc
            pos_copy["unreal_usd"] = round(pnl_usd - funding_acc, 4)
            pos_copy["funding_projected_to_settlement"] = round(
                projected_funding_to_next_settlement(pos, funding_snap), 4
            )
            pos_copy["funding_side"] = funding_side_label(
                float(funding_snap.get("rate") or 0.0), pos.get("dir", "LONG")
            )
            total_unreal += pos_copy["unreal_usd"]
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
        branding = build_dashboard_display(snapshot)
        snapshot.update(branding)
        with state_lock:
            state.update(branding)
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
        snapshot["equity"] = snapshot["account_balance"] + total_unreal
        session_trades = _session_trades_only(trades_copy)
        snapshot["trades"] = session_trades
        snapshot["trade_count_session"] = len(session_trades)
        snapshot["bot_start_time"] = bot_start_time
        snapshot["fresh_collection_mode"] = bool(state.get("fresh_collection_mode", False))
        snapshot["ai_input"] = LAST_AI_PAYLOAD if LAST_AI_PAYLOAD else state.get("feature_snapshot", {"status": "NO_AI_CALL_YET"})
        snapshot["ai_input_time"] = LAST_AI_TIMESTAMP
        snapshot["feature_snapshot"] = state.get("feature_snapshot", {})
        snapshot["data_quality"] = state.get("data_quality", 0.0)
        snapshot["edge_threshold"] = get_edge_threshold()
        snapshot["edge_threshold_max"] = get_edge_threshold_max()
        snapshot["edge_range_preset"] = state.get("edge_range_preset", DEFAULT_EDGE_RANGE_PRESET)
        snapshot["edge_threshold_display"] = _edge_range_label()
        snapshot["effective_threshold"] = get_effective_edge_threshold()
        snapshot["research_data_collection"] = is_research_data_collection()
        snapshot["edge_options"] = EDGE_OPTIONS
        snapshot["edge_range_presets"] = EDGE_RANGE_PRESETS
        snapshot["max_active_signals"] = state.get("max_active_signals", MAX_CONCURRENT_POSITIONS_DEFAULT)
        m_fee, t_fee = get_trading_fee_rates()
        snapshot["fee_profile"] = EXCHANGE_FEE_PROFILE
        snapshot["maker_fee_pct"] = m_fee
        snapshot["taker_fee_pct"] = t_fee
        snapshot["funding_simulation_enabled"] = FUNDING_SIMULATION_ENABLED
        snapshot["bot_version"] = EXECUTION_FIX_VERSION
        snapshot["analyzer_sync_id"] = ANALYZER_SYNC_ID
        with state_lock:
            snapshot["funding"] = copy.deepcopy(state.get("funding") or {})
        logger.info(f"[API STATE] edge_threshold synced to UI: {snapshot['edge_threshold']} [PIPELINE ENFORCEMENT]")
        return jsonify(snapshot)
    except Exception as e:
        logger.error(f"/api/state error: {str(e)}")
        return jsonify({})

@app.route('/health')
@app.route('/api/status')
@app.route('/status')
def health():
    with state_lock:
        hb = state.get("last_heartbeat", last_heartbeat)
        paused = bool(state.get("execution_paused", False))
        reason = state.get("execution_reason", "")
        manual = bool(state.get("manual_admin_pause", False))
    status = "paused" if paused else "alive"
    return jsonify({
        "status": status,
        "last_heartbeat": hb,
        "time_since_heartbeat": time.time() - hb,
        "execution_paused": paused,
        "execution_reason": reason,
        "manual_admin_pause": manual,
    })

@app.route('/debug_state')
def get_debug_state():
    with state_lock:
        return jsonify(state.get("debug_state", {}))

@app.route('/api/pause', methods=['POST'])
def api_pause():
    with state_lock:
        state["manual_admin_pause"] = True
        state["live_armed"] = False
        save_persistent_config()
    set_execution_paused("ADMIN_MANUAL")
    logger.warning("[ADMIN] Manual pause via /api/pause [PIPELINE ENFORCEMENT]")
    return jsonify({"status": "paused", "execution_paused": True, "execution_reason": "ADMIN_MANUAL"})

@app.route('/api/resume', methods=['POST'])
def api_resume():
    with state_lock:
        state["manual_admin_pause"] = False
        save_persistent_config()
    set_execution_paused("")
    logger.info("[ADMIN] Manual resume via /api/resume [PIPELINE ENFORCEMENT]")
    return jsonify({"status": "resumed", "execution_paused": False})

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

@app.route('/api/toggle_block_free_range_entries', methods=['POST'])
def toggle_block_free_range_entries():
    with state_lock:
        state["block_free_range_entries"] = not state.get("block_free_range_entries", BLOCK_FREE_RANGE_ENTRIES)
        save_persistent_config()
    return jsonify({"block_free_range_entries": state["block_free_range_entries"]})

@app.route('/api/toggle_golden_stack', methods=['POST'])
def toggle_golden_stack():
    with state_lock:
        state["golden_stack_enabled"] = not bool(
            state.get("golden_stack_enabled", GOLDEN_STACK_DEFAULT_ENABLED)
        )
        save_persistent_config()
        logger.info(
            f"[GOLDEN_STACK] toggled {'ON' if state['golden_stack_enabled'] else 'OFF'} "
            f"[PIPELINE ENFORCEMENT]"
        )
    return jsonify({"golden_stack_enabled": state["golden_stack_enabled"]})

@app.route('/api/toggle_debug', methods=['POST'])
def toggle_debug():
    data = request.get_json() or {}
    with state_lock:
        state["debug_enabled"] = data.get("enabled", not state["debug_enabled"])
        save_persistent_config()
        update_logger_level()
    return jsonify({"debug_enabled": state["debug_enabled"]})

@app.route('/api/reset', methods=['POST'])
def api_reset_showcase():
    """Admin/platform: wipe all research artifacts and restart session at $500."""
    result = perform_fresh_collection_reset()
    enforce_clean_research_session()
    return jsonify({"ok": True, "reset": result, "account_balance": STARTING_BALANCE})

@app.route('/api/toggle_fresh_collection', methods=['POST'])
def toggle_fresh_collection():
    data = request.get_json() or {}
    want_on = data.get("enabled")
    if want_on is None:
        want_on = not state.get("fresh_collection_mode", False)
    if want_on:
        with state_lock:
            if state.get("live_armed"):
                return jsonify({"error": "Disable LIVE ARM before fresh collection reset"}), 400
        result = perform_fresh_collection_reset()
        return jsonify({"fresh_collection_mode": True, "reset": result})
    with state_lock:
        state["fresh_collection_mode"] = False
        save_persistent_config()
    logger.info("[FRESH COLLECTION] Mode turned OFF — no file wipe [PIPELINE ENFORCEMENT]")
    return jsonify({"fresh_collection_mode": False})

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
        logger.warning(f"[LEVERAGE] Capped request to {MAX_RESEARCH_LEVERAGE}x (max allowed) [PIPELINE ENFORCEMENT]")
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
    val = max(1, min(20, int(data.get("value", 3))))
    with state_lock:
        state["max_active_signals"] = val
        save_persistent_config()
    return jsonify({"max_active_signals": val})

@app.route('/api/set_threshold', methods=['POST'])
def set_threshold():
    data = request.get_json() or {}
    value = data.get("value")
    if value is None:
        return jsonify({"status": "error", "msg": "missing value"}), 400
    try:
        val = float(value)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "msg": "invalid threshold"}), 400
    if val < AI_THRESHOLD_MIN or val > AI_THRESHOLD_MAX:
        return jsonify({"status": "error", "msg": f"threshold must be {AI_THRESHOLD_MIN}–{AI_THRESHOLD_MAX}"}), 400
    set_ai_threshold(val)
    return jsonify({"status": "ok", "ai_threshold": get_ai_threshold()})

@app.route('/api/set_edge_threshold', methods=['POST'])
def api_set_edge_threshold():
    data = request.get_json() or {}
    value = round(float(data.get("value", 3.0)), 1)
    if value not in [round(x, 1) for x in EDGE_OPTIONS]:
        logger.warning(f"[EDGE SET API] Invalid value {value} rejected [PIPELINE ENFORCEMENT]")
        return jsonify({"status": "error", "msg": "invalid threshold"}), 400
    set_edge_threshold(value)
    return jsonify({
        "status": "ok",
        "new_value": value,
        "edge_threshold": get_edge_threshold(),
        "edge_threshold_max": get_edge_threshold_max(),
        "edge_threshold_display": _edge_range_label(),
    })

@app.route('/api/set_edge_range', methods=['POST'])
def api_set_edge_range():
    data = request.get_json() or {}
    preset = data.get("preset")
    if preset and preset != "custom":
        apply_edge_range_preset(str(preset))
    else:
        if "min" in data and data.get("min") is not None:
            set_edge_threshold(round(float(data["min"]), 1))
        if "max" in data:
            set_edge_threshold_max(data.get("max"))
        elif preset == "custom":
            with state_lock:
                state["edge_range_preset"] = "custom"
                save_persistent_config()
    with state_lock:
        preset_out = state.get("edge_range_preset")
    return jsonify({
        "status": "ok",
        "edge_threshold": get_edge_threshold(),
        "edge_threshold_max": get_edge_threshold_max(),
        "edge_range_preset": preset_out,
        "edge_threshold_display": _edge_range_label(),
    })

@app.route('/api/download_debug_config')
def download_debug_config():
    with state_lock:
        config = {k: v for k, v in state.items() if not k.startswith("_")}
    return jsonify(config), 200, {'Content-Disposition': 'attachment; filename=debug_config.json'}

def _read_log_tail(path, max_lines=400):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except Exception:
        return ""

@app.route('/api/export_debug')
def export_debug():
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            debug_data = list(DEBUG_LOG_BUFFER)
            z.writestr("debug.json", json.dumps(debug_data, indent=2, default=str))
            errors_only = [e for e in debug_data if e.get("ai_error") or (e.get("data") or {}).get("ai", {}).get("ai_error")]
            z.writestr("debug_ai_errors.json", json.dumps(errors_only, indent=2, default=str))
            with state_lock:
                snap = {
                    "debug_state": copy.deepcopy(state.get("debug_state", {})),
                    "last_edge": state.get("last_edge"),
                    "pipeline_outcome": state.get("pipeline_outcome"),
                    "ai_call_count": state.get("ai_call_count"),
                    "last_ai": copy.deepcopy(state.get("last_ai", {})),
                }
            z.writestr("debug_state_snapshot.json", json.dumps(snap, indent=2, default=str))
            log_tail = _read_log_tail(os.getenv("BOT_LOG_FILE", "bot_runtime.log"))
            if log_tail:
                z.writestr("bot_runtime_tail.log", log_tail)
            for csv_name in [CSV_PIPELINE_EVENTS, CSV_AI_ERRORS, CSV_DECISIONS, CSV_AI_TRANCHE]:
                if os.path.exists(csv_name):
                    z.write(csv_name, arcname=csv_name)
        buf.seek(0)
        return send_file(buf, mimetype='application/zip', as_attachment=True, download_name='debug_export.zip')
    except Exception as e:
        logger.error(f"[EXPORT ERROR] {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/export_csv')
def export_csv():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in [
            CSV_DECISIONS, CSV_TRADES, CSV_EXPIRED, CSV_BLOCKS, CSV_AI_TRANCHE, CSV_SETUP_LOG, CSV_CANDLES,
            CSV_PIPELINE_EVENTS, CSV_AI_ERRORS,
            SIGNAL_SNAPSHOT_FILE, SIGNAL_REPLAY_FILE, TRADE_OUTCOME_FILE, SHADOW_OUTCOME_FILE, COUNTERFACTUAL_FILE,
            EDGE_CENSUS_FILE, "signal_persist.log", "near_edge.log",
        ]:
            if os.path.exists(file):
                zip_file.write(file)
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='3factor_logs.zip')

def calc_position_qty(price, leverage, margin_usdt=None):
    try:
        if price is None or price <= 0 or leverage is None:
            logger.error(f"[QTY CALC FAIL] price={price} leverage={leverage}")
            return 0.0001
        market = bitfinex_public.market(SYMBOL_CCXT)
        min_qty = market.get("limits", {}).get("amount", {}).get("min", 0.001)
        min_notional = market.get("limits", {}).get("cost", {}).get("min", 5.0)
        fee_buffer = 1 - (MAKER_FEE_PCT + TAKER_FEE_PCT)
        notional = float(margin_usdt or FIXED_MARGIN_USDT) * (leverage or 20) * fee_buffer
        if notional < min_notional:
            notional = min_notional
        qty = notional / price
        qty = max(min_qty, qty)
        qty = float(bitfinex_public.amount_to_precision(SYMBOL_CCXT, qty))
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
            candles = fetch_bitfinex_ohlcv("15m", limit=250)
            if candles and len(candles) >= MIN_CANDLES:
                with state_lock:
                    latest_candles = candles[-250:]
                    last_candle_ts = candles[-1][0] / 1000 if candles else time.time()
                    state["last_data_ts"] = time.time()
                    state["ohlcv_ready"] = True
                    state["data_error"] = None
                populate_candle_buffers_from_candles(latest_candles)
                last_ohlcv_fetch = time.time()
                update_ema()
                trend_info()
                update_market_context(force=True)
                with state_lock:
                    state.update({"last_fetch_success": utc_iso()})
                    state.update({"data_source": "bitfinex_rest"})
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
            sync_dashboard_branding()
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

def reset_session_risk_state():
    """Research: each process start forgets prior session PnL and loss-streak pauses."""
    with state_lock:
        state["daily_pnl_usd"] = 0.0
        state["consecutive_losses"] = 0
        state["loss_pause_until"] = 0.0
        state["execution_paused"] = False
        state["execution_reason"] = ""
        state["_pause_priority"] = 0
        state["current_trading_day"] = datetime.now(timezone.utc).date()
    logger.info("[STARTUP] Session risk reset — daily PnL and loss streak cleared [PIPELINE ENFORCEMENT]")

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
            "consecutive_losses": 0,
            "loss_pause_until": 0.0,
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
            "bootstrap_done": False,
            "edge_prev": 0.0,
            "edge_trigger_armed": True,
            "last_edge_trigger_candle_bucket": -1,
            "debug_state": _fresh_debug_state(),
            "last_pipeline_stage": "IDLE",
            "ai_call_count": 0,
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
        now_iso = utc_iso()
        state["ai_history"].append({
            "ts": now_iso,
            "time": now_iso,
            "melbourne_time": _format_melbourne_hm(now_iso),
            "session_ts": bot_start_time,
            "trade_id": trade_id,
            "dir": dir_,
            "approved": approved,
            "win_prob": win_prob,
            "comment": comment,
            "event_id": event_id,
        })
        hist_limit = 50 if _sole_ai_research_mode() else 5
        if len(state["ai_history"]) > hist_limit:
            state["ai_history"] = state["ai_history"][-hist_limit:]
        state["ai_decision"] = "APPROVED" if approved else "REJECTED"
        state["final_decision"] = "APPROVED" if approved else "AI_REJECTED"
        state["last_ai"].update({"win_prob": win_prob,"direction": dir_,"trade_id": trade_id,"comment": comment,"ai_error": None})
        state["last_engine_error"] = "" if approved else "AI rejected"

REPLAY_TICK_MIN_INTERVAL_SEC = 1.0


def _profit_lock_floor_for_ladder(peak_pct: float, ladder):
    if peak_pct is None or peak_pct < ladder[0][0]:
        return None
    floor = None
    for trigger, lock in ladder:
        if peak_pct >= trigger:
            floor = lock
    if peak_pct >= PEAK_NEVER_LOSER_MIN_PEAK:
        floor = max(floor or 0, PEAK_NEVER_LOSER_FLOOR)
    return floor


def _margin_pct_to_usd(margin_pct: float, margin_usdt: float) -> float:
    try:
        return float(margin_pct) / 100.0 * float(margin_usdt)
    except (TypeError, ValueError):
        return 0.0


def patch_signal_snapshot_outcome(
    trade_id: str,
    executed: bool = None,
    block_reason: str = None,
    fill_price: float = None,
    post_block_research: dict = None,
    fill_dynamics: dict = None,
):
    """Merge execution outcome into signal_snapshot.jsonl for analyzer cohort joins."""
    if not trade_id or not os.path.exists(SIGNAL_SNAPSHOT_FILE):
        return
    try:
        lines = []
        updated = False
        with open(SIGNAL_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                row = json.loads(raw)
                if row.get("trade_id") == trade_id:
                    if executed is not None:
                        row["executed"] = bool(executed)
                    if block_reason is not None:
                        row["block_reason"] = block_reason
                    outcome = row.get("outcome") or {}
                    if fill_price is not None:
                        outcome["fill_price"] = float(fill_price)
                    if fill_dynamics:
                        outcome["fill_dynamics"] = fill_dynamics
                    if post_block_research:
                        row["post_block_research"] = post_block_research
                    outcome["patched_ts"] = time.time()
                    row["outcome"] = outcome
                    updated = True
                lines.append(json.dumps(row))
        if updated:
            with open(SIGNAL_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.error(f"[SIGNAL_SNAPSHOT] patch failed trade_id={trade_id}: {e}")


def log_signal_snapshot(signal: dict, ai: dict, pipeline_eff_thr: float):
    """Persist APPROVE-time config for counterfactual / shadow research."""
    try:
        trade_id = signal.get("trade_id")
        if not trade_id or ai.get("decision") != "APPROVE":
            return
        price = float(signal.get("signal_price") or state.get("price") or 0)
        if price <= 0:
            return
        lev = int(state.get("leverage", DEFAULT_RESEARCH_LEVERAGE))
        margin_usdt = float(signal.get("margin_usdt") or FIXED_MARGIN_USDT)
        edge_score = float(signal.get("edge_score_at_entry") or state.get("last_edge") or 0)
        feat = signal.get("features") or {}
        approve_idx = _bump_research_approve_index()
        snapshot = {
            "schema": "signal_snapshot_v4",
            "trade_id": trade_id,
            "ts": utc_iso(),
            "approve_ts": time.time(),
            "approve_index": approve_idx,
            "direction": signal.get("final_direction"),
            "price": price,
            "edge_score": signal.get("edge_score_at_entry"),
            "effective_threshold": pipeline_eff_thr,
            "executed": False,
            "block_reason": None,
            "ai": {
                "approved": True,
                "decision": "APPROVE",
                "win_prob": ai.get("win_prob"),
                "direction": ai.get("direction"),
                "bull_score": ai.get("bull_score"),
                "bear_score": ai.get("bear_score"),
                "source": ai.get("source"),
            },
            "config": {
                "pullback_threshold": float(state.get("pullback_threshold", 0.002)),
                "leverage": lev,
                "sl_pct": sl_price_pct(lev),
                "margin_usdt": margin_usdt,
                "trail_ladder": TRAIL_LADDER,
                "free_run_mtf": RESEARCH_FREE_RUN_DISABLE_MTF_GATE,
                "free_run_chop": RESEARCH_FREE_RUN_DISABLE_CHOP_GATE,
                "free_run_momentum_align": RESEARCH_FREE_RUN_DISABLE_MOMENTUM_ALIGN,
                **get_exit_config_snapshot(),
            },
            "policy_effective": {
                "early_fail": bool(state.get("early_fail_enabled", True)),
            },
            "market": {
                "regime": signal.get("regime") or state.get("regime"),
                "strategy": signal.get("strategy") or state.get("strategy"),
            },
            "entry_thesis": capture_entry_thesis(signal),
            "entry_mode": signal.get("entry_mode", ENTRY_MODE_PULLBACK),
            "ema_hybrid_base": signal.get("ema_hybrid_base"),
            "ema_hybrid_limit": signal.get("ema_hybrid_limit"),
            "ema_hybrid_offset_usd": signal.get("ema_hybrid_offset_usd"),
            "ema9_at_entry": signal.get("ema9_at_entry"),
            "ema21_at_entry": signal.get("ema21_at_entry"),
            "dist_to_ema_hybrid_pct": signal.get("dist_to_ema_hybrid_pct"),
            "entry_gates": capture_entry_gate_snapshot(signal, ai, edge_score, feat),
            "entry_regime": capture_entry_regime_snapshot(signal, feat),
            "research_buckets": capture_research_buckets(signal, ai, edge_score, feat),
            "entry_sizing": signal.get("sizing_reference") or compute_reference_scaled_margin(
                str(signal.get("final_direction") or ai.get("direction") or "LONG").upper(),
                ai,
                signal.get("context") or {},
            ),
            "setup_type": signal.get("setup_type") or classify_setup(feat),
            "bot_version": EXECUTION_FIX_VERSION,
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "golden_stack_enabled": signal.get("golden_stack_enabled_at_entry", golden_stack_enabled()),
            "golden_stack_eval": signal.get("golden_stack_eval"),
        }
        if feat:
            snapshot["entry_features"] = {
                "mom_metric": round(_compute_momentum_metric(feat), 4),
                "ret_1m": feat.get("ret_1m"),
                "ret_5m": feat.get("ret_5m"),
                "velocity": feat.get("velocity"),
                "volume_ratio": feat.get("volume_ratio"),
                "imbalance": feat.get("imbalance"),
            }
        rotate_log(SIGNAL_SNAPSHOT_FILE)
        with open(SIGNAL_SNAPSHOT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot) + "\n")
        logger.info(f"[SIGNAL_SNAPSHOT] trade_id={trade_id} dir={snapshot['direction']} price={fmt(price)} [PIPELINE ENFORCEMENT]")
    except Exception as e:
        logger.error(f"[SIGNAL_SNAPSHOT] failed: {e}")


def begin_approve_research(signal: dict, ai: dict, pipeline_eff_thr: float):
    """Start replay + snapshot at AI APPROVE (before post-AI execution gates)."""
    if ai.get("decision") != "APPROVE":
        return
    trade_id = signal.get("trade_id")
    price = float(signal.get("signal_price") or state.get("price") or 0)
    if not trade_id or price <= 0:
        return
    log_signal_snapshot(signal, ai, pipeline_eff_thr)
    lev = int(state.get("leverage", DEFAULT_RESEARCH_LEVERAGE))
    margin_usdt = float(signal.get("margin_usdt") or FIXED_MARGIN_USDT)
    start_replay_buffer(
        trade_id,
        price,
        lane="shadow",
        direction=signal.get("final_direction"),
        leverage=lev,
        margin_usdt=margin_usdt,
        pullback_pct=float(signal.get("pullback_pct", state.get("pullback_threshold", 0.002))),
        early_fail_enabled=bool(state.get("early_fail_enabled", True)),
        exit_config=get_exit_config_snapshot(),
        ai_win_prob=ai.get("win_prob"),
        edge_score=signal.get("edge_score_at_entry"),
    )
    append_replay_tick(trade_id, price, None)


def mark_approve_research_executed(trade_id: str, fill_price: float):
    if not trade_id or not fill_price or fill_price <= 0:
        return
    fill_dynamics = None
    with replay_lock:
        buf = replay_buffers.get(trade_id)
        if not buf:
            start_replay_buffer(trade_id, fill_price, lane="executed")
            buf = replay_buffers.get(trade_id)
        if buf:
            buf["lane"] = "executed"
            buf["block_reason"] = None
            buf["virtual_entry"] = float(fill_price)
            fill_t = time.time() - buf.get("start_ts", time.time())
            buf["virtual_fill_t"] = fill_t
            start_price = float(buf.get("start_price") or fill_price)
            direction = str(buf.get("direction", "LONG")).upper()
            dir_factor = 1 if direction == "LONG" else -1
            leverage = int(buf.get("leverage", state.get("leverage", 20)))
            pre_fill_mae = 0.0
            for tick in sorted(buf.get("ticks", []), key=lambda x: x.get("seq", 0)):
                t = float(tick.get("t", 0))
                if t >= fill_t:
                    break
                p = float(tick.get("price") or 0)
                if p > 0 and start_price > 0:
                    unreal = ((p - start_price) / start_price) * dir_factor * leverage * 100
                    pre_fill_mae = min(pre_fill_mae, unreal)
            fill_dynamics = {
                "fill_delay_sec": round(fill_t, 3),
                "pre_fill_mae_margin_pct": round(pre_fill_mae, 4),
                "achieved_pullback_pct": round(abs(fill_price - start_price) / start_price, 6) if start_price > 0 else None,
            }
    patch_signal_snapshot_outcome(
        trade_id, executed=True, block_reason=None, fill_price=fill_price, fill_dynamics=fill_dynamics,
    )


def _shadow_unreal_pct(buf: dict, price: float) -> float:
    entry = buf.get("virtual_entry")
    if not entry or entry <= 0 or price <= 0:
        return 0.0
    direction = buf.get("direction", "LONG")
    leverage = int(buf.get("leverage", state.get("leverage", 20)))
    dir_factor = 1 if direction == "LONG" else -1
    return ((price - entry) / entry) * dir_factor * leverage * 100


def _try_shadow_limit_fill(buf: dict, price: float, t_rel: float):
    if buf.get("virtual_entry"):
        return
    start_price = buf.get("start_price")
    direction = buf.get("direction", "LONG")
    pullback_pct = float(buf.get("pullback_pct", 0))
    if not start_price or start_price <= 0:
        return
    if pullback_pct <= 0.0:
        buf["virtual_entry"] = float(price)
        buf["virtual_fill_t"] = t_rel
        return
    if direction == "LONG" and price <= start_price * (1 - pullback_pct):
        buf["virtual_entry"] = float(price)
        buf["virtual_fill_t"] = t_rel
    elif direction == "SHORT" and price >= start_price * (1 + pullback_pct):
        buf["virtual_entry"] = float(price)
        buf["virtual_fill_t"] = t_rel


def tick_all_replay_buffers(price: float):
    if price is None or price <= 0:
        return
    now = time.time()
    with replay_lock:
        items = list(replay_buffers.items())
    for trade_id, buf in items:
        if buf.get("closed") or buf.get("lane") == "executed":
            continue
        t_rel = now - buf.get("start_ts", now)
        _try_shadow_limit_fill(buf, price, t_rel)
        unreal = _shadow_unreal_pct(buf, price) if buf.get("virtual_entry") else None
        append_replay_tick(trade_id, price, unreal)


def simulate_replay_outcome(buf: dict) -> dict:
    """Walk replay ticks with bot exit rules — used for blocked APPROVE shadow PnL."""
    ticks = sorted(buf.get("ticks", []), key=lambda x: x.get("seq", 0))
    direction = str(buf.get("direction", "LONG")).upper()
    leverage = int(buf.get("leverage", state.get("leverage", 20)))
    margin_usdt = float(buf.get("margin_usdt", FIXED_MARGIN_USDT))
    pullback_pct = float(buf.get("pullback_pct", 0))
    start_price = float(buf.get("start_price") or 0)
    exit_config = buf.get("exit_config") or get_exit_config_snapshot()
    trail_ladder = exit_config.get("trail_ladder", TRAIL_LADDER)
    fast_cut = float(exit_config.get("thesis_fast_exit_unreal_pct", THESIS_FAST_EXIT_UNREAL_PCT))
    thesis_above = float(exit_config.get("thesis_exit_if_above_unreal_pct", THESIS_EXIT_IF_ABOVE_UNREAL_PCT))
    early_fail = bool(buf.get("early_fail_enabled", True))
    sl_pct = sl_price_pct(leverage)
    dir_factor = 1 if direction == "LONG" else -1

    entry = buf.get("virtual_entry")
    fill_t = buf.get("virtual_fill_t")
    if entry is None:
        for tick in ticks:
            price = tick.get("price")
            t = float(tick.get("t", 0))
            if price is None or price <= 0 or t > REPLAY_TTL_SEC:
                continue
            if pullback_pct <= 0.0:
                entry = float(price)
                fill_t = t
                break
            if direction == "LONG" and price <= start_price * (1 - pullback_pct):
                entry = float(price)
                fill_t = t
                break
            if direction == "SHORT" and price >= start_price * (1 + pullback_pct):
                entry = float(price)
                fill_t = t
                break
    if entry is None or fill_t is None:
        return {
            "filled": False,
            "exit_reason": "NO_FILL",
            "net_pnl_usd": 0.0,
            "gross_pnl_margin_pct": 0.0,
            "max_profit_margin_pct": 0.0,
            "max_drawdown_margin_pct": 0.0,
            "fill_delay_sec": None,
        }

    sl = entry * (1 - dir_factor * sl_pct)
    peak = 0.0
    mae = 0.0
    exit_reason = "MARK_TO_MARKET"
    exit_margin_pct = 0.0
    for tick in ticks:
        t = float(tick.get("t", 0))
        if t < fill_t:
            continue
        price = tick.get("price")
        if price is None or price <= 0:
            continue
        unreal = tick.get("unreal_pct")
        if unreal is None:
            unreal = ((price - entry) / entry) * dir_factor * leverage * 100
        else:
            unreal = float(unreal)
        peak = max(peak, unreal)
        mae = min(mae, unreal)
        age_min = (t - fill_t) / 60.0
        if early_fail and unreal <= EARLY_FAIL_PCT_THRESHOLD and age_min < EARLY_FAIL_MINUTES:
            exit_reason = "EARLY_FAIL"
            exit_margin_pct = unreal
            break
        if (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl):
            exit_reason = "STOP_LOSS"
            exit_margin_pct = -MAX_SL_MARGIN_PCT
            break
        lock_floor = _profit_lock_floor_for_ladder(peak, trail_ladder)
        if lock_floor is not None and peak >= trail_ladder[0][0] and unreal <= lock_floor:
            exit_reason = "PROFIT_LOCK_LADDER"
            exit_margin_pct = lock_floor
            break
        mfe_protect = float(exit_config.get("thesis_mfe_protect_pct", THESIS_MFE_PROTECT_PCT))
        if peak < trail_ladder[0][0] and unreal <= fast_cut:
            if not (mfe_protect > 0 and unreal >= mfe_protect):
                exit_reason = "THESIS_FAST_CUT"
                exit_margin_pct = fast_cut
                break
        if unreal >= TP_EMERGENCY_MARGIN_PCT:
            exit_reason = "TAKE_PROFIT"
            exit_margin_pct = TP_EMERGENCY_MARGIN_PCT
            break
        exit_margin_pct = unreal
    else:
        exit_margin_pct = float(unreal) if ticks else 0.0

    net_usd = _margin_pct_to_usd(exit_margin_pct, margin_usdt)
    return {
        "filled": True,
        "exit_reason": exit_reason,
        "net_pnl_usd": round(net_usd, 4),
        "gross_pnl_margin_pct": round(exit_margin_pct, 4),
        "max_profit_margin_pct": round(peak, 4),
        "max_drawdown_margin_pct": round(mae, 4),
        "fill_delay_sec": round(float(fill_t), 3),
        "entry": entry,
        "fill_price": entry,
    }


def compute_post_block_research(buf: dict, gate_outcome: dict) -> dict:
    """Post-block price path + block quality metrics for analyzer."""
    block_t = float(buf.get("block_t_rel") or 0)
    ticks = sorted(buf.get("ticks", []), key=lambda x: x.get("seq", 0))
    post_ticks = [t for t in ticks if float(t.get("t", 0)) >= block_t]
    start_price = float(buf.get("start_price") or 0)
    direction = str(buf.get("direction", "LONG")).upper()
    dir_factor = 1 if direction == "LONG" else -1
    max_fav = 0.0
    max_adv = 0.0
    for tick in post_ticks:
        p = float(tick.get("price") or 0)
        if p <= 0 or start_price <= 0:
            continue
        move_pct = ((p - start_price) / start_price) * dir_factor * 100
        max_fav = max(max_fav, move_pct)
        max_adv = min(max_adv, move_pct)
    cf_pnl = gate_outcome.get("net_pnl_usd")
    filled = bool(gate_outcome.get("filled"))
    post_end = max((float(t.get("t", 0)) for t in post_ticks), default=block_t)
    return {
        "block_t_rel_sec": round(block_t, 3),
        "post_block_tick_count": len(post_ticks),
        "post_block_duration_sec": round(max(0.0, post_end - block_t), 3),
        "post_block_max_favorable_pct": round(max_fav, 4),
        "post_block_max_adverse_pct": round(max_adv, 4),
        "counterfactual_pnl_usd": round(float(cf_pnl), 4) if filled and cf_pnl is not None else None,
        "block_was_correct": bool(filled and cf_pnl is not None and cf_pnl < 0),
        "block_cost_usd": round(float(cf_pnl), 4) if filled and cf_pnl and cf_pnl > 0 else 0.0,
        "block_saved_usd": round(-float(cf_pnl), 4) if filled and cf_pnl is not None and cf_pnl < 0 else 0.0,
        "continuation_target_sec": POST_BLOCK_CONTINUATION_SEC,
    }


def log_shadow_outcome_jsonl(
    trade_id: str, block_reason: str, outcome: dict, buf: dict,
    signal: dict = None, ai: dict = None, post_block_research: dict = None,
):
    try:
        row = {
            "schema": "shadow_outcome_v2",
            "ts": utc_iso(),
            "trade_id": trade_id,
            "executed": False,
            "block_reason": block_reason,
            "direction": buf.get("direction"),
            "ai_win_prob": buf.get("ai_win_prob") or (ai or {}).get("win_prob"),
            "edge_score": buf.get("edge_score"),
            "pullback_pct": buf.get("pullback_pct"),
            "leverage": buf.get("leverage"),
            "margin_usdt": buf.get("margin_usdt"),
            "start_price": buf.get("start_price"),
            "tick_count": len(buf.get("ticks", [])),
            "block_t_rel_sec": buf.get("block_t_rel"),
            "post_block_tick_count": (post_block_research or {}).get("post_block_tick_count"),
            "exit_config": buf.get("exit_config") or get_exit_config_snapshot(),
            "post_block_research": post_block_research or {},
            **outcome,
        }
        rotate_log(SHADOW_OUTCOME_FILE)
        with open(SHADOW_OUTCOME_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        logger.info(
            f"[SHADOW_OUTCOME] trade_id={trade_id} reason={block_reason} filled={outcome.get('filled')} "
            f"pnl=${outcome.get('net_pnl_usd')} exit={outcome.get('exit_reason')} [PIPELINE ENFORCEMENT]"
        )
    except Exception as e:
        logger.error(f"[SHADOW_OUTCOME] log failed: {e}")


def defer_shadow_research(trade_id: str, block_reason: str):
    """Keep blocked APPROVE replay alive until SHADOW_REPLAY_TTL_SEC for counterfactual ticks."""
    if not trade_id:
        return
    now = time.time()
    block_t_rel = 0.0
    with replay_lock:
        buf = replay_buffers.get(trade_id)
        if not buf or buf.get("lane") == "executed":
            return
        buf["block_reason"] = block_reason
        buf["block_ts"] = now
        block_t_rel = round(now - buf.get("start_ts", now), 3)
        buf["block_t_rel"] = block_t_rel
        buf["lane"] = "shadow_deferred"
    logger.info(
        f"[SHADOW_DEFER] trade_id={trade_id} reason={block_reason} "
        f"block_t={block_t_rel}s ttl={SHADOW_REPLAY_TTL_SEC}s post_block>={POST_BLOCK_CONTINUATION_SEC}s "
        f"[PIPELINE ENFORCEMENT]"
    )


def finalize_shadow_research(trade_id: str, block_reason: str, signal: dict = None, ai: dict = None):
    """Simulate blocked APPROVE path and persist shadow PnL."""
    if not trade_id:
        return
    with replay_lock:
        buf = replay_buffers.get(trade_id)
        if not buf:
            return
        if buf.get("lane") == "executed":
            return
        buf["block_reason"] = block_reason
        buf["lane"] = "shadow_blocked"
        buf_copy = {
            **buf,
            "ticks": list(buf.get("ticks", [])),
        }
    outcome = simulate_replay_outcome(buf_copy)
    post_block = compute_post_block_research(buf_copy, outcome)
    log_shadow_outcome_jsonl(
        trade_id, block_reason, outcome, buf_copy, signal=signal, ai=ai, post_block_research=post_block,
    )
    patch_signal_snapshot_outcome(trade_id, executed=False, block_reason=block_reason, post_block_research=post_block)
    close_replay_buffer(trade_id)


def start_replay_buffer(trade_id: str, start_price: float, **meta):
    if not trade_id or not start_price:
        return
    with replay_lock:
        if trade_id in replay_buffers and not replay_buffers[trade_id].get("closed"):
            return
        replay_buffers[trade_id] = {
            "start_ts": time.time(),
            "start_price": float(start_price),
            "ticks": [],
            "last_update": time.time(),
            "last_tick_ts": 0.0,
            "closed": False,
            "seq": 0,
            "lane": meta.get("lane", "executed"),
            "direction": meta.get("direction"),
            "leverage": meta.get("leverage"),
            "margin_usdt": meta.get("margin_usdt"),
            "pullback_pct": meta.get("pullback_pct"),
            "early_fail_enabled": meta.get("early_fail_enabled", True),
            "exit_config": meta.get("exit_config"),
            "virtual_entry": meta.get("virtual_entry"),
            "virtual_fill_t": meta.get("virtual_fill_t"),
            "block_reason": meta.get("block_reason"),
            "ai_win_prob": meta.get("ai_win_prob"),
            "edge_score": meta.get("edge_score"),
        }


def append_replay_tick(trade_id: str, price: float, unreal_pct: float = None):
    if not trade_id or price is None or price <= 0:
        return
    now = time.time()
    with replay_lock:
        buf = replay_buffers.get(trade_id)
        if not buf or buf.get("closed"):
            return
        if now - buf.get("last_tick_ts", 0) < REPLAY_TICK_MIN_INTERVAL_SEC:
            return
        buf["seq"] = int(buf.get("seq", 0)) + 1
        t_rel = round(now - buf["start_ts"], 3)
        phase = "post_block" if buf.get("block_ts") and now >= float(buf["block_ts"]) else "pre_block"
        buf["ticks"].append({
            "seq": buf["seq"],
            "t": t_rel,
            "price": float(price),
            "unreal_pct": round(float(unreal_pct), 4) if unreal_pct is not None else None,
            "phase": phase,
        })
        buf["last_update"] = now
        buf["last_tick_ts"] = now


def log_trade_outcome_jsonl(trade_row: dict, pos: dict):
    """Per-trade path summary for analyzer thesis/ladder counterfactuals."""
    try:
        mfe = float(trade_row.get("max_profit") or pos.get("max_pnl_pct") or 0)
        direction = str(trade_row.get("dir") or pos.get("dir") or "LONG").upper()
        bull = int(trade_row.get("bull_score_at_entry") or pos.get("bull_score_at_entry") or 0)
        bear = int(trade_row.get("bear_score_at_entry") or pos.get("bear_score_at_entry") or 0)
        spread = bull - bear if direction == "LONG" else bear - bull
        edge = float(trade_row.get("decision_edge_score") or pos.get("edge_score_at_entry") or 0)
        sr_state = trade_row.get("sr_state") or pos.get("entry_sr_state") or "UNKNOWN"
        ai_prob = float(trade_row.get("ai_win_prob") or pos.get("ai_win_prob") or 0)
        structure = trade_row.get("structure_score_at_entry")
        adx = trade_row.get("adx_at_entry")
        buckets = capture_research_buckets(
            {
                "final_direction": direction,
                "bull_score_at_entry": bull,
                "bear_score_at_entry": bear,
                "features": {"sr_state": sr_state},
            },
            {"bull_score": bull, "bear_score": bear, "win_prob": ai_prob},
            edge,
            {"sr_state": sr_state},
        )
        outcome = {
            "schema": "trade_outcome_v2",
            "ts": trade_row.get("ts"),
            "trade_id": trade_row.get("trade_id"),
            "dir": trade_row.get("dir"),
            "exit_reason": trade_row.get("exit_reason"),
            "entry": trade_row.get("entry"),
            "exit": trade_row.get("exit"),
            "margin_usdt": trade_row.get("margin_usdt"),
            "leverage": trade_row.get("leverage"),
            "max_profit_margin_pct": trade_row.get("max_profit"),
            "max_drawdown_margin_pct": trade_row.get("max_drawdown"),
            "final_pnl_margin_pct": trade_row.get("pnl"),
            "net_pnl_usd": trade_row.get("net_pnl_usd"),
            "dur_min": trade_row.get("dur_min"),
            "entry_type": trade_row.get("entry_type"),
            "trade_mfe_type": _trade_mfe_type_label(mfe),
            "first_3_candles": pos.get("first_3_candles") or {},
            "research_buckets": buckets,
            "entry_features": {
                "edge_score": edge,
                "directional_spread": spread,
                "structure_score": structure,
                "sr_state": sr_state,
                "dist_to_support": trade_row.get("distance_to_support"),
                "dist_to_resistance": trade_row.get("distance_to_resistance"),
                "adx": adx,
                "volume_ratio": trade_row.get("features_volume_ratio"),
                "imbalance": trade_row.get("features_imbalance"),
                "delta": trade_row.get("features_delta"),
                "velocity": trade_row.get("features_velocity"),
                "ret_1m": None,
                "ret_5m": None,
                "ai_win_prob": ai_prob,
            },
            "exit_config": get_exit_config_snapshot(),
            "entry_thesis": pos.get("entry_thesis") or {},
            "entry_mode": pos.get("entry_mode", trade_row.get("entry_mode", ENTRY_MODE_PULLBACK)),
            "ema_hybrid_base": pos.get("ema_hybrid_base", trade_row.get("ema_hybrid_base")),
            "ema_hybrid_limit": pos.get("ema_hybrid_limit", trade_row.get("ema_hybrid_limit")),
            "ema_hybrid_offset_usd": pos.get("ema_hybrid_offset_usd", trade_row.get("ema_hybrid_offset_usd")),
            "dist_to_ema_hybrid_pct": pos.get("dist_to_ema_hybrid_pct", trade_row.get("dist_to_ema_hybrid_pct")),
            "bot_version": EXECUTION_FIX_VERSION,
            "analyzer_sync_id": ANALYZER_SYNC_ID,
        }
        rotate_log(TRADE_OUTCOME_FILE)
        with open(TRADE_OUTCOME_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(outcome) + "\n")
    except Exception as e:
        logger.error(f"[TRADE_OUTCOME] log failed: {e}")


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
            post_ticks = [t for t in buf.get("ticks", []) if t.get("phase") == "post_block"]
            replay = {
                "schema": "signal_replay_v3",
                "trade_id": trade_id,
                "start_ts": utc_iso(datetime.fromtimestamp(buf["start_ts"], timezone.utc)),
                "start_price": buf["start_price"],
                "direction": buf.get("direction"),
                "lane": buf.get("lane"),
                "block_reason": buf.get("block_reason"),
                "block_ts": buf.get("block_ts"),
                "block_t_rel": buf.get("block_t_rel"),
                "post_block_tick_count": len(post_ticks),
                "virtual_entry": buf.get("virtual_entry"),
                "virtual_fill_t": buf.get("virtual_fill_t"),
                "pullback_pct": buf.get("pullback_pct"),
                "leverage": buf.get("leverage"),
                "margin_usdt": buf.get("margin_usdt"),
                "ticks": list(buf["ticks"]),
            }
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

def run_offline_research_sim():
    """Backfill counterfactual.jsonl from snapshots + replays (deduped by trade_id)."""
    global _sim_processed_trade_ids
    try:
        if os.path.exists(COUNTERFACTUAL_FILE):
            with open(COUNTERFACTUAL_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        tid = row.get("trade_id")
                        if tid:
                            _sim_processed_trade_ids.add(tid)
                    except Exception:
                        pass
        if os.path.exists(SHADOW_OUTCOME_FILE):
            with open(SHADOW_OUTCOME_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        tid = row.get("trade_id")
                        if tid:
                            _sim_processed_trade_ids.add(tid)
                    except Exception:
                        pass
        offline_simulator()
    except Exception as e:
        logger.error(f"[RESEARCH_SIM] {e}")


def analytics_loop():
    try:
        while not shutdown_event.is_set():
            try:
                compute_analytics()
                run_offline_research_sim()
            except Exception as e:
                logger.error(f"Analytics error: {e}")
            time.sleep(ANALYTICS_INTERVAL_SEC)
    except Exception as e:
        logger.exception("[CRITICAL] Analytics loop crash")
        set_execution_paused("THREAD_CRASH")

def offline_simulator(signal_snapshot_file=SIGNAL_SNAPSHOT_FILE, signal_replay_file=SIGNAL_REPLAY_FILE, output_file=COUNTERFACTUAL_FILE):
    global write_counter, _sim_processed_trade_ids
    if not os.path.exists(signal_snapshot_file) or not os.path.exists(signal_replay_file):
        return
    snapshots = {}
    with open(signal_snapshot_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                tid = row.get("trade_id")
                if tid:
                    snapshots[tid] = row
            except Exception:
                pass
    replays = {}
    with open(signal_replay_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                tid = row.get("trade_id")
                if tid:
                    replays[tid] = row
            except Exception:
                pass
    written = 0
    for trade_id, snapshot in snapshots.items():
        if trade_id in _sim_processed_trade_ids:
            continue
        if not snapshot.get("ai", {}).get("approved", False):
            continue
        replay = replays.get(trade_id)
        if not replay:
            continue
        cfg = snapshot.get("config", {})
        buf = {
            "start_price": replay.get("start_price"),
            "ticks": replay.get("ticks", []),
            "direction": snapshot.get("direction") or replay.get("direction"),
            "leverage": cfg.get("leverage", state.get("leverage", 20)),
            "margin_usdt": cfg.get("margin_usdt", FIXED_MARGIN_USDT),
            "pullback_pct": cfg.get("pullback_threshold", state.get("pullback_threshold", 0.002)),
            "early_fail_enabled": snapshot.get("policy_effective", {}).get("early_fail", True),
            "exit_config": {**get_exit_config_snapshot(), **{k: v for k, v in cfg.items() if k in (
                "trail_ladder", "thesis_fast_exit_unreal_pct", "thesis_exit_if_above_unreal_pct",
            )}},
            "virtual_entry": replay.get("virtual_entry"),
            "virtual_fill_t": replay.get("virtual_fill_t"),
        }
        outcome = simulate_replay_outcome(buf)
        counterfactual = {
            "schema": "counterfactual_v2",
            "trade_id": trade_id,
            "scenario": buf["direction"],
            "executed": bool(snapshot.get("executed")),
            "block_reason": snapshot.get("block_reason") or replay.get("block_reason"),
            "lane": replay.get("lane"),
            "ai_win_prob": snapshot.get("ai", {}).get("win_prob"),
            "edge_score": snapshot.get("edge_score"),
            "fill_price": outcome.get("fill_price"),
            "fill_delay_sec": outcome.get("fill_delay_sec"),
            "filled": outcome.get("filled"),
            "exit_reason": outcome.get("exit_reason"),
            "net_pnl_usd": outcome.get("net_pnl_usd"),
            "gross_pnl_margin_pct": outcome.get("gross_pnl_margin_pct"),
            "max_profit_margin_pct": outcome.get("max_profit_margin_pct"),
            "max_drawdown_margin_pct": outcome.get("max_drawdown_margin_pct"),
        }
        try:
            rotate_log(output_file)
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(counterfactual) + "\n")
                f.flush()
                write_counter += 1
                if write_counter % 10 == 0:
                    os.fsync(f.fileno())
            _sim_processed_trade_ids.add(trade_id)
            written += 1
        except Exception as e:
            logger.error(f"Counterfactual log failed: {e}")
    if written:
        logger.info(f"[RESEARCH_SIM] counterfactual rows written={written} [PIPELINE ENFORCEMENT]")

def preload_candles():
    global latest_candles, last_candle_ts
    preload_failed = True
    for attempt in range(5):
        try:
            candles = fetch_bitfinex_ohlcv("15m", limit=250)
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
            populate_candle_buffers_from_candles(latest_candles, force=True)
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

def _persistent_config_keys():
    keys = [
        "pullback_threshold", "leverage", "max_active_signals", "ai_enabled", "early_fail_enabled",
        "block_free_range_entries", "invert_signal", "debug_enabled", "live_armed",
        "min_confidence",
        "force_ai_every_signal", "ai_threshold", "edge_threshold", "edge_threshold_max",
        "edge_range_preset", "fresh_collection_mode", "golden_stack_enabled",
    ]
    if state.get("strategy_mode") != "RESEARCH":
        keys.append("daily_pnl_usd")
    return keys

def enforce_clean_research_session():
    """Research sim always starts at STARTING_BALANCE with no carry-over trades."""
    global bot_start_time
    with trade_lock:
        trades.clear()
        pending_orders.clear()
        expired_orders.clear()
        open_positions.clear()
        trades_map.clear()
        recent_trades.clear()
    with replay_lock:
        replay_buffers.clear()
    with state_lock:
        state["account_balance"] = STARTING_BALANCE
        state["daily_pnl_usd"] = 0.0
        state["consecutive_losses"] = 0
        state["loss_pause_until"] = 0.0
        state["fresh_collection_mode"] = True
        if not state.get("live_armed", False):
            state["fresh_collection_mode"] = True
    bot_start_time = time.time()
    with state_lock:
        state["bot_start_time"] = bot_start_time
    logger.warning(
        f"[STARTUP] Clean research session — balance={STARTING_BALANCE} fresh_collection=ON "
        f"version={EXECUTION_FIX_VERSION} [PIPELINE ENFORCEMENT]"
    )

def _session_trades_only(trades_list):
    """Only expose trades opened after this process started."""
    start = bot_start_time or 0.0
    if start <= 0:
        return list(trades_list or [])
    kept = []
    for t in trades_list or []:
        if not isinstance(t, dict):
            continue
        ts = t.get("created_ts_ts") or t.get("entry_ts") or 0.0
        if not ts:
            raw_ts = t.get("ts")
            if isinstance(raw_ts, str):
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0.0
            elif isinstance(raw_ts, (int, float)):
                ts = float(raw_ts)
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
        if float(ts or 0) >= start - 1.0:
            kept.append(t)
    return kept

def load_persistent_config():
    if os.path.exists(CONFIG_FILE):
        allowed_keys = _persistent_config_keys()
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            with state_lock:
                for key in allowed_keys:
                    if key in config:
                        state[key] = config[key]
                if "_threshold_locked" in config:
                    state["_threshold_locked"] = config["_threshold_locked"]
        with state_lock:
            lev = int(state.get("leverage", DEFAULT_RESEARCH_LEVERAGE) or DEFAULT_RESEARCH_LEVERAGE)
            if lev > MAX_RESEARCH_LEVERAGE:
                state["leverage"] = MAX_RESEARCH_LEVERAGE
                logger.warning(
                    f"[CONFIG] Capped leverage {lev} -> {MAX_RESEARCH_LEVERAGE}x [PIPELINE ENFORCEMENT]"
                )
            if state.get("ai_threshold") is not None:
                state["ai_threshold"] = _clamp_ai_threshold(state["ai_threshold"])
            elif state.get("strategy_mode") == "RESEARCH":
                state["ai_threshold"] = RESEARCH_AI_THRESHOLD_DEFAULT
            elif not state.get("_threshold_locked"):
                state["ai_threshold"] = LIVE_AI_THRESHOLD_FLOOR
        logger.info("Loaded persistent config from config.json - ai_threshold restored")

def save_persistent_config():
    config = {k: state[k] for k in _persistent_config_keys() + ["_threshold_locked", "bootstrap_done"]}
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

def _port_is_open(host: str, port: int) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False

def _ensure_flask_port_available(port: int = None):
    if port is None:
        port = DASHBOARD_PORT
    """Fail fast if another process already serves the dashboard (prevents stale dual-bot state)."""
    if not _port_is_open("127.0.0.1", port):
        return
    import sys
    if sys.platform != "win32":
        logger.warning(f"[PORT] {port} appears in use on Linux — continuing (Railway) [PIPELINE ENFORCEMENT]")
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="replace",
            timeout=10,
        )
    except Exception as e:
        raise SystemExit(
            f"Port {port} is in use and listener PID could not be resolved ({e}). "
            f"Stop the old bot, then restart from Final Bots."
        ) from e
    me = os.getpid()
    pids = set()
    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line or needle not in line:
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pids.add(int(parts[-1]))
    others = sorted(p for p in pids if p != me)
    if others:
        raise SystemExit(
            f"Port {port} already in use by PID(s) {others}. "
            f"Run: taskkill /PID {others[0]} /F  then start one bot from Final Bots."
        )

def run_flask():
    _ensure_flask_port_available(DASHBOARD_PORT)
    app.run(host=DASHBOARD_BIND_HOST, port=DASHBOARD_PORT, use_reloader=False)

def is_ai_active():
    return state.get("ai_enabled", False) and bool(_deepseek_api_key())

def ttl_monitor():
    global _last_fresh_maintain_ts
    logger.info("[TTL] Independent monitor started")
    while not shutdown_event.is_set():
        try:
            prune_signals()
            cleanup_expired_orders()
            if state.get("fresh_collection_mode"):
                now = time.time()
                if now - _last_fresh_maintain_ts >= FRESH_COLLECTION_MAINTAIN_INTERVAL_SEC:
                    _last_fresh_maintain_ts = now
                    maintain_fresh_collection_files()
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
                if state.get("execution_paused") and not state.get("manual_admin_pause"):
                    set_execution_paused("")
        else:
            state["last_ready_ts"] = 0
            state["system_ready"] = False
            ws_tick = state.get("ws_last_tick")
            if ws_tick is not None and not ws_ok:
                ws_age = now - ws_tick
                if ws_age >= STALE_HARD_SEC:
                    state["execution_paused"] = True
                    state["execution_reason"] = "STALE_DATA_HARD_STOP"
                    logger.error(f"[HARD STOP] WS STALE age={fmt(ws_age)}s — execution paused")
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
        research = state.get("strategy_mode") == "RESEARCH"
        if "ai_threshold" in state:
            state["ai_threshold"] = _clamp_ai_threshold(state["ai_threshold"])
            return
        default = RESEARCH_AI_THRESHOLD_DEFAULT if research else LIVE_AI_THRESHOLD_FLOOR
        state["ai_threshold"] = default
        save_persistent_config()
        logger.info(
            f"[INIT] AI threshold default {default} "
            f"({'research flexible 0–100' if research else 'live suggested floor'}) "
            f"[PIPELINE ENFORCEMENT]"
        )

def startup_log_research_sync():
    """Confirm dashboard thresholds match runtime after config load (research vs live)."""
    enforce_edge_threshold_options()
    if not is_research_data_collection():
        logger.info(
            f"[INIT] Live mode — AI={get_ai_threshold()} (suggested floor {LIVE_AI_THRESHOLD_FLOOR}) | "
            f"edge={get_edge_threshold()} effective={get_effective_edge_threshold()} "
            f"[PIPELINE ENFORCEMENT]"
        )
        return
    logger.info(
        f"[INIT] RESEARCH DATA COLLECTION — flexible thresholds | "
        f"AI={get_ai_threshold()} (flexible {AI_THRESHOLD_MIN}–{AI_THRESHOLD_MAX}) | "
        f"edge={get_edge_threshold()} effective={get_effective_edge_threshold()} (dashboard-only) | "
        f"live_armed={'ON' if state.get('live_armed') else 'OFF'} [PIPELINE ENFORCEMENT]"
    )

def recover_from_crash():
    if state.get("execution_reason") == "THREAD_CRASH":
        logger.warning("[RECOVERY] Attempting recovery from thread crash")
        set_execution_paused("")

def heartbeat_loop():
    global last_heartbeat
    while not shutdown_event.is_set():
        now = time.time()
        with state_lock:
            state["heartbeat"] = now
            state["last_heartbeat"] = now
        last_heartbeat = now
        time.sleep(HEARTBEAT_INTERVAL)
        if not (state.get("ws_ready") and len(latest_candles) >= MIN_CANDLES):
            continue
        now = time.time()
        if now - state.get("last_ai_call_ts", 0) < AI_COOLDOWN_SECONDS:
            logger.debug(f"[HEARTBEAT] Skipped - AI cooldown active ({AI_COOLDOWN_SECONDS - (now - state.get('last_ai_call_ts', 0)):.0f}s left) [PIPELINE ENFORCEMENT]")
            continue
        if _sole_ai_research_mode():
            logger.info("[HEARTBEAT] v83 periodic AI check [PIPELINE ENFORCEMENT]")
            event = detect_event_light()
            if event and round(event.get("edge_score", 0), 1) > 0:
                process_signal(event)
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
    global bot_start_time, last_signal_create_global, last_console_update, last_ai_call_ts, last_signal_process_ts, last_context_hash, last_signal_create_ts, test_signal_fired, prev_price, prev_delta, avg_volume, recent_high, recent_low, rejection_strength, last_signal_hash, last_ws_message_time, last_pipeline_run, last_heartbeat, last_edge_compute
    prune_aux_logs_on_startup()
    global DEEPSEEK_API_KEY
    _load_local_dotenv()
    DEEPSEEK_API_KEY = _deepseek_api_key()
    logger.info(f"[AI INIT] KEY PRESENT: {bool(DEEPSEEK_API_KEY)} cwd={os.getcwd()} [PIPELINE ENFORCEMENT]")
    if not DEEPSEEK_API_KEY:
        logger.warning(
            "[AI INIT] DEEPSEEK_API_KEY missing — copy .env.example to .env in this folder "
            "or set env vars before starting. AI will return MISSING_API_KEY until fixed."
        )
    _wipe_research_on_startup_if_needed()
    reset_runtime_state()
    update_logger_level()
    validate_startup()
    load_persistent_config()
    startup_hard_fix_ai_threshold()
    startup_log_research_sync()
    if state.get("strategy_mode") == "RESEARCH":
        reset_session_risk_state()
    bot_start_time = time.time()
    with state_lock:
        state["ai_history"] = []
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
    _write_research_session(bot_start_time)
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1)
    logger.info(f"[RAILWAY] Early health server on :{DASHBOARD_PORT}/health [PIPELINE ENFORCEMENT]")
    research_mode = state.get("strategy_mode") == "RESEARCH"
    keys_ok = _private_api_keys_ok()
    if not keys_ok:
        if research_mode and not state.get("live_armed"):
            logger.warning(
                "[STARTUP] BITFINEX private API keys missing — RESEARCH public-data mode "
                f"(balance={STARTING_BALANCE}) [PIPELINE ENFORCEMENT]"
            )
            state["account_balance"] = STARTING_BALANCE
        else:
            raise RuntimeError("BITFINEX_API_KEY or BITFINEX_API_SECRET invalid or missing")
    else:
        try:
            balance = bitfinex_private.fetch_balance()
            logger.info(f"Bitfinex API keys validated - Balance: {balance}")
            if state.get("live_armed"):
                usdt = STARTING_BALANCE
                if isinstance(balance, dict):
                    usdt = balance.get("total", {}).get("USDt", balance.get("total", {}).get("USDT", STARTING_BALANCE))
                state["account_balance"] = usdt
            else:
                state["account_balance"] = STARTING_BALANCE
        except Exception as e:
            logger.error(f"Bitfinex API key test failed: {e}")
            state["account_balance"] = STARTING_BALANCE
    if not _deepseek_api_key():
        logger.warning("AI disabled: DEEPSEEK_API_KEY missing (see Final Bots\\.env)")
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
    logger.info(
        f"[V75 EXIT] pullback_default={state.get('pullback_threshold', 0.002)*100:.2f}% "
        f"ladder_1st={TRAIL_LADDER[0][0]}%→lock{TRAIL_LADDER[0][1]}% "
        f"thesis_fast_cut={THESIS_FAST_EXIT_UNREAL_PCT}% mfe_protect={THESIS_MFE_PROTECT_PCT}% "
        f"(0.0% pullback toggle=instant fill) [PIPELINE ENFORCEMENT]"
    )
    logger.info(
        f"[V72 SHADOW RESEARCH] snapshot+replay at APPROVE | shadow_outcome.jsonl on blocked APPROVE | "
        f"counterfactual auto-sim in analytics loop [PIPELINE ENFORCEMENT]"
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
    if golden_stack_enabled():
        logger.warning(
            f"[V86 GOLDEN_STACK] ENFORCED — chop<={GOLDEN_STACK_CHOP_MAX} | SHORT BEAR_ALIGNED | "
            f"struct<={GOLDEN_STACK_SHORT_STRUCT_MAX} | spread {GOLDEN_STACK_SPREAD_MIN}-{GOLDEN_STACK_SPREAD_MAX} | "
            f"EMA_HYBRID dist<={GOLDEN_STACK_EMA_DIST_MAX_PCT}% | block ADX {GOLDEN_STACK_ADX_BLOCK_LOW}-"
            f"{GOLDEN_STACK_ADX_BLOCK_HIGH} | ladder {TRAIL_LADDER[0][0]}→{TRAIL_LADDER[0][1]}% "
            f"[PIPELINE ENFORCEMENT]"
        )
    elif RESEARCH_AI_SOLE_AUTHORITY:
        logger.warning(
            f"[V81 SOLE-AI] Full research funnel — edge min 0.0+ | periodic AI every "
            f"{RESEARCH_PERIODIC_AI_INTERVAL_SEC}s | golden stack OFF — post-AI gates log WOULD_BLOCK_* "
            f"[PIPELINE ENFORCEMENT]"
        )
    if RESEARCH_FREE_RUN_DISABLE_MTF_GATE or RESEARCH_FREE_RUN_DISABLE_CHOP_GATE:
        logger.warning(
            f"[FREE_RUN] Post-AI gates OFF — mtf={RESEARCH_FREE_RUN_DISABLE_MTF_GATE} "
            f"chop={RESEARCH_FREE_RUN_DISABLE_CHOP_GATE} "
            f"mom_align={RESEARCH_FREE_RUN_DISABLE_MOMENTUM_ALIGN} "
            f"(strict v75 thresholds still logged in snapshots) [PIPELINE ENFORCEMENT]"
        )
    if FLAT_MARGIN_EVERY_TRADE:
        logger.warning(
            f"[FLAT_MARGIN] Every trade uses ${FIXED_MARGIN_USDT} — conviction/regime/ADX size scaling OFF "
            f"(reference scaled margin logged in snapshots) [PIPELINE ENFORCEMENT]"
        )
    _agent_dbg("H1", "main.startup", "boot_complete", {"version": EXECUTION_FIX_VERSION, "exposure": boot_exposure, "pending": len(pending_orders), "positions": len(open_positions)})
    logger.info(f"Bot start time locked at {bot_start_time} - old trades blocked")
    fetch_ohlcv()
    logger.info(
        f"[STARTUP] Exchange=Bitfinex symbol={BITFINEX_WS_SYMBOL} data=WS+REST sim_fees={EXCHANGE_FEE_PROFILE} "
        f"pid={os.getpid()} cwd={os.getcwd()} dashboard={dashboard_public_url()} [PIPELINE ENFORCEMENT]"
    )
    sync_dashboard_branding()
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
    main()