# -*- coding: utf-8 -*-
"""
analyzer_research_engine_v62.py — Pipeline intelligence for Bitfinex 3-factor research bot.

Pairs with bybit_bot.py (same folder, same CSV filenames).
Bot contract: EXECUTION_FIX_VERSION / ANALYZER_SYNC_ID / exchange=bitfinex / WINDOW_SIZE=10 readiness.

v86: Golden Stack gates (dashboard toggle) — chop<=0.8, SHORT BEAR_ALIGNED, struct<=-5,
spread 3-7, EMA hybrid dist<=0.5%, ADX 25-30 block, ladder 8→5; edge/AI stay dashboard-flexible.
v85: MFE-protect fix (unreal not peak), thesis -20%, Type-A stall 8min/5% MFE, EMA fill high-water,
CSV utf-8, EMA hybrid skips await_confirm.
v84.1: EMA9/EMA21 hybrid entry (+$20 long / -$20 short vs hybrid base; fallback pullback); flexible AI threshold 0–100.
v83: Type-A stall exit, trend health (BULL_WEAKENING), spread≥5 penalty, tick-priced sim fills.
v82: Ladder exec — 10→6 first rung, WS stale-tick filter, 150ms armed monitor, tick exits;
thesis fast-cut -28%, MFE protect 8%. Sole-AI funnel unchanged (edge 0.0+, periodic AI 300s).
v81: Sole-AI research funnel — edge min 0.0+, periodic AI every 300s, post-AI gates log
WOULD_BLOCK_* (zero-block mode for full replay dataset). Session-only dashboard AI history.
v80: Edge histogram + funnel by bucket, monotonicity, spread/SR/AI cohort tables,
Type-A vs Type-B, first-3-candle report; pairs with research_buckets + edge_census.jsonl.
v72: Blocked APPROVE shadow PnL (shadow_outcome.jsonl), real-edge report, counterfactual v2.
v71: Live defaults synced to v70 sweet-spot (pullback 0.2%, ladder 10→6%, thesis cut -6%).
v70: Thesis fast-cut / ladder-first-rung sweet-spot sweeps, research data coverage audit,
signal_replay tick simulation when available; cfg_* exit columns on trades CSV.
v69: Optimum pullback % sweep (post-signal price path), favorable/adverse excursion,
trail-ladder forensics (peak → lock floor → booked PnL), profit-left-on-table vs ladder rungs.
v68: BITFINEX_ZERO trading fees + live funding_fees_usd / trading_fees_usd / total_cost_usd breakdown.
v67: pipeline_events + ai_errors CSVs, AI_ERROR vs model AI_REJECT funnel, legacy crash detection.
v66: Bitfinex sync metadata verification, AI_REJECT funnel counts, bot version checks.
v63: MFE/MAE, bull/bear spread, structure_score, MTF alignment, contradiction index,
exit forensics, TIME_EXIT deep dive, edge×structure×MTF combos, factor-gate funnel.
"""
import pandas as pd
import numpy as np
import time
from datetime import datetime
import os
import json
import glob
import sys
import traceback
import re

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TRADES_FILE = "trades_3factor.csv"
BLOCKED_FILE = "blocked_signals_3factor.csv"
DECISIONS_FILE = "decisions_3factor.csv"
AI_TRANCHE_FILE = "ai_tranche_log.csv"
PIPELINE_EVENTS_FILE = "pipeline_events_3factor.csv"
AI_ERRORS_FILE = "ai_errors_3factor.csv"
SETUP_LOG_FILE = "setup_log_3factor.csv"
CANDLES_FILE = "candles_3factor.csv"
SIGNAL_PERSIST_FILE = "signal_persist.log"
NEAR_EDGE_FILE = "near_edge.log"
MIN_TRADES = 1
MIN_TRADES_FOR_RULES = 10
# Must match bybit_bot.py EXECUTION_FIX_VERSION + ANALYZER_SYNC_ID when changing research CSV contract.
EXPECTED_BOT_VERSION = "v10.9.451-v95-research-telemetry"
EXPECTED_EXCHANGE = "bitfinex"
EXPECTED_SYMBOL = "tBTCF0:USTF0"
EXPECTED_FEE_PROFILE = "BITFINEX_ZERO"
ANALYZER_SYNC_ID = "v9.5-research-telemetry-2026-06-12"
BOT_VERSION = EXPECTED_BOT_VERSION
ANALYZER_VERSION = "v95-research-telemetry"
REVERSAL_STUDY_FILE = "reversal_study.jsonl"
RESEARCH_LANE_LABELS = {
    "CONTINUOUS": "Continuous AI Research",
    "STABILITY": "AI Stability Research",
    "GOLDEN_STACK": "Golden Stack",
}
AI_INPUT_LOG_FILE = "ai_input_log.jsonl"
RESEARCH_FREE_RUN_LIVE = True  # v78: bot disables post-AI MTF/chop — sweeps use strict reference thresholds
FLAT_MARGIN_LIVE_USD = 20.0
EDGE_CENSUS_FILE = "edge_census.jsonl"
MARGIN_SIZE_SWEEP_USD = [5.0, 10.0, 15.0, 20.0, 25.0]
RESEARCH_SESSION_FILE = "research_session.json"
PIPELINE_ENFORCEMENT_TAG = "[PIPELINE ENFORCEMENT]"
# Mirror bybit_bot.py TRAIL_LADDER — (peak_margin_pct_trigger, lock_floor_margin_pct)
TRAIL_LADDER = [
    (8, 5), (15, 10), (20, 15), (25, 18), (40, 30),
    (60, 50), (80, 60), (100, 80),
]
THESIS_MFE_PROTECT_DEFAULT = 8.0
PEAK_NEVER_LOSER_MIN_PEAK = 40.0
PEAK_NEVER_LOSER_FLOOR = 10.0
DEFAULT_PULLBACK_THRESHOLDS = [0.0, 0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.006]
THESIS_FAST_EXIT_DEFAULT = -20.0
DEFAULT_PULLBACK_PCT = 0.002
THESIS_EXIT_ABOVE_DEFAULT = 8.0
THESIS_FAST_CUT_CANDIDATES = [-6, -8, -10, -12, -14, -16, -18, -20, -25, -30, -40, -50, -60, -80, -100, -120, -150, -180]
STOP_THESIS_SWEEP_MAX_MARGIN_PCT = 180.0  # research sweep ceiling (margin %)


def _build_stop_thesis_sweep_levels(max_margin_pct=None):
    """Thesis stop candidates from -6% down to -max (margin %, not entry pullback)."""
    cap = float(max_margin_pct if max_margin_pct is not None else STOP_THESIS_SWEEP_MAX_MARGIN_PCT)
    levels = [-6]
    levels.extend(range(-10, -31, -2))
    levels.extend(range(-35, -101, -5))
    levels.extend(range(-110, int(cap) + 1, -10))
    if -int(cap) not in levels:
        levels.append(-int(cap))
    return sorted(set(levels))


STOP_THESIS_WIDE_SWEEP = _build_stop_thesis_sweep_levels()
THESIS_EXIT_ABOVE_CANDIDATES = [4, 6, 8, 10, 12, 15, 18]
LADDER_FIRST_RUNG_CANDIDATES = [(8, 5), (10, 6), (12, 8), (14, 10), (15, 12), (18, 14)]
STOP_LADDER_2D_GRID_STOPS = [-6, -10, -12, -14, -16, -18, -20, -25, -30, -40]
MFE_PROTECT_SWEEP = [0, 2, 4, 6, 8, 10]
STOP_LADDER_MFE_3D_STOPS = [-6, -10, -12, -20, -30]
STOP_LADDER_MFE_3D_LADDERS = [(8, 5), (10, 6), (12, 8)]
# Entry gate research sweeps (signal_snapshot.jsonl @ APPROVE + tick replay PnL)
LIVE_ADX_BLOCK_MIN = 15.0
LIVE_MOMENTUM_CHOP_MAX = 0.8  # v86 golden stack live; 0.5 kept for strict sweep reference
GOLDEN_STACK_CHOP_MAX = 0.8
LIVE_EDGE_THRESHOLD_DEFAULT = 0.5
EDGE_BUCKET_ORDER = ["0.5-1.0", "1.0-1.5", "1.5-2.0", "2.0-2.5", "2.5-3.0", "3.0-3.5", "3.5-4.0", "4.0+"]
SPREAD_BUCKET_ORDER = ["0-1", "2", "3", "4", "5+"]
SR_BUCKET_ORDER = ["NEAR_SUPPORT", "MID_RANGE", "NEAR_RESISTANCE"]
SESSION_BUCKET_ORDER = ["ASIA", "LONDON", "OVERLAP", "NEW_YORK"]
AI_PROB_BUCKET_ORDER = ["45-50", "50-55", "55-60", "60-65", "65+"]
MIN_APPROVES_FOR_EDGE_CONCLUSIONS = 50
LIVE_MIN_FACTOR_SPREAD = 1
ENTRY_ADX_MIN_SWEEP = [8, 9, 10, 11, 12, 13, 14, 15, 16, 18]
ENTRY_CHOP_MAX_SWEEP = [0.10, 0.25, 0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 1.00]
ENTRY_EDGE_MIN_SWEEP = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
ENTRY_SPREAD_MIN_SWEEP = [1, 2, 3, 4, 5]
ENTRY_ADX_GRID_SWEEP = [0, 15, 18, 20, 22, 25, 28, 30]
PULLBACK_REPLAY_SWEEP = [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006]  # 0% market .. 0.6%
REPLAY_FILL_TTL_SEC = 120 * 60
BULL_TF_LABELS = {"BULLISH", "LEAN_BULL"}
MTF_RULE_LABELS = [
    ("NONE", "No MTF gate"),
    ("ALLOW_MIXED", "Allow BULL_ALIGNED + MIXED"),
    ("LIVE_BULL_ALIGNED", "★ LIVE: agreement==BULL_ALIGNED"),
    ("15M_BULL", "15m bullish (BULLISH/LEAN_BULL)"),
    ("1H_BULL", "1h bullish"),
    ("4H_BULL", "4h bullish"),
    ("15M+1H_BULL", "15m AND 1h bullish"),
    ("1H+4H_BULL", "1h AND 4h bullish"),
    ("15M+1H+4H_BULL", "15m+1h+4h all bullish"),
    ("15M_OR_1H_BULL", "15m OR 1h bullish"),
]
SIGNAL_REPLAY_FILE = "signal_replay.jsonl"
TRADE_OUTCOME_FILE = "trade_outcome.jsonl"
SHADOW_OUTCOME_FILE = "shadow_outcome.jsonl"
COUNTERFACTUAL_FILE = "counterfactual.jsonl"
SIGNAL_SNAPSHOT_FILE = "signal_snapshot.jsonl"
APPROVED_BUT_REJECTED_FILE = "approved_but_rejected.jsonl"
NEAR_MISS_FILE = "near_miss.jsonl"
SOFT_REJECT_SHADOW_FILE = "soft_reject_shadow.jsonl"
EXPORT_ZIP_FILES = (
    TRADES_FILE, BLOCKED_FILE, DECISIONS_FILE, AI_TRANCHE_FILE, SETUP_LOG_FILE, CANDLES_FILE,
    PIPELINE_EVENTS_FILE, AI_ERRORS_FILE, SIGNAL_PERSIST_FILE, NEAR_EDGE_FILE,
    SIGNAL_REPLAY_FILE, TRADE_OUTCOME_FILE, SHADOW_OUTCOME_FILE, SIGNAL_SNAPSHOT_FILE, COUNTERFACTUAL_FILE,
    APPROVED_BUT_REJECTED_FILE, NEAR_MISS_FILE, SOFT_REJECT_SHADOW_FILE,
    EDGE_CENSUS_FILE,
)
RESEARCH_CSV_FILES = (
    TRADES_FILE,
    BLOCKED_FILE,
    DECISIONS_FILE,
    AI_TRANCHE_FILE,
    SETUP_LOG_FILE,
    CANDLES_FILE,
    SIGNAL_PERSIST_FILE,
    NEAR_EDGE_FILE,
)
OPTIONAL_RESEARCH_CSV_FILES = (
    PIPELINE_EVENTS_FILE,
    AI_ERRORS_FILE,
    APPROVED_BUT_REJECTED_FILE,
    NEAR_MISS_FILE,
    SOFT_REJECT_SHADOW_FILE,
)

def _truthy(val):
    if isinstance(val, bool):
        return val
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return False
    return str(val).strip().lower() in ("true", "1", "yes")

def infer_pipeline_stage(row):
    """Map decision/reason/skip_stage to v6.9+ pipeline stage (backward compatible with old CSVs)."""
    skip = str(row.get("skip_stage", "") or "").strip()
    reason = str(row.get("reason", "") or "").strip()
    decision = str(row.get("decision", "") or "").strip()
    ai_error = _truthy(row.get("ai_error"))
    if skip == "AI_ERROR" or reason.startswith("AI_ERROR"):
        return "AI_ERROR"
    if ai_error and decision == "BLOCKED" and skip in ("AI", "AI_ERROR", ""):
        if reason.startswith("AI_ERROR") or "CRASH" in reason.upper():
            return "AI_ERROR"
        try:
            es = float(row.get("edge_score", 0) or 0)
            if es <= 0 and reason in ("AI_REJECT", "AI_REJECTED", ""):
                return "AI_ERROR"
        except (TypeError, ValueError):
            return "AI_ERROR"
    if skip == "POST_AI" or (decision == "BLOCKED" and skip == "POST_AI"):
        return "POST_AI"
    if reason.startswith("AI_REJECT") or reason in ("AI_REJECT", "AI_REJECTED"):
        return "POST_AI"
    if reason.startswith("FACTOR_GATE") or reason.startswith("WEAK_SETUP"):
        return "POST_AI"
    if reason == "APPROVE" or (decision == "AI" and reason in ("APPROVE", "")):
        return "AI"
    if decision == "AI" and reason not in ("APPROVE", ""):
        return f"AI_{reason or 'UNKNOWN'}"
    if reason.startswith("PRE_AI_") or reason.startswith("EDGE_BELOW_") or skip == "PRE_AI" or skip.startswith("PRE_AI_"):
        return "PRE_AI"
    if "COOLDOWN" in reason.upper() or skip == "COOLDOWN":
        return "COOLDOWN"
    if reason == "EDGE_FAIL":
        return "PRE_PIPELINE"
    if decision == "COMPLETE":
        return "EXECUTED"
    if decision == "NO_SIGNAL":
        if reason.startswith("PRE_AI_") or reason.startswith("EDGE_BELOW_"):
            return "PRE_AI"
        return "NO_SIGNAL"
    if decision == "BLOCKED":
        if reason.startswith("AI_ERROR"):
            return "AI_ERROR"
        if skip == "AI_ERROR":
            return "AI_ERROR"
        return "POST_AI"
    if skip and skip not in ("NO_SIGNAL", "PRE_PIPELINE", "nan", "None", ""):
        if skip.startswith("PRE_AI_") or skip.startswith("EDGE_BELOW_"):
            return "PRE_AI"
        return skip
    return decision or "UNKNOWN"

def normalize_decision_stages(decisions):
    if decisions.empty:
        return decisions
    work = decisions.copy()
    if "skip_stage" not in work.columns:
        work["skip_stage"] = np.nan
    work["pipeline_stage"] = work.apply(infer_pipeline_stage, axis=1)
    return work

def count_ai_approves(decisions):
    if decisions.empty or "reason" not in decisions.columns:
        return 0
    return int((decisions["reason"] == "APPROVE").sum())


def count_ai_rejects(decisions):
    """Model rejections / post-AI blocks (excludes API AI_ERROR stage)."""
    if decisions.empty:
        return 0
    staged = normalize_decision_stages(decisions)
    return int((staged["pipeline_stage"] == "POST_AI").sum())


def count_ai_errors(decisions):
    """DeepSeek API/parse failures (AI_ERROR), not model REJECT."""
    if decisions.empty:
        return 0
    staged = normalize_decision_stages(decisions)
    return int((staged["pipeline_stage"] == "AI_ERROR").sum())


def count_legacy_crash_rejects(decisions):
    """Old bot rows: BLOCKED + AI_REJECT reason + edge_score 0 (mislabeled crashes)."""
    if decisions.empty or "reason" not in decisions.columns:
        return 0
    work = decisions.copy()
    work["edge_num"] = pd.to_numeric(work.get("edge_score", 0), errors="coerce").fillna(0)
    mask = (
        work.get("decision", pd.Series()).astype(str) == "BLOCKED"
    ) & work["reason"].isin(["AI_REJECT", "AI_REJECTED"]) & (work["edge_num"] <= 0)
    if "ai_error" in work.columns:
        mask = mask & (~work["ai_error"].apply(_truthy))
    return int(mask.sum())


def _file_time_span(path: str, ts_cols=(), json_ts_key="ts"):
    """Return (min_ts, max_ts, row_count, mtime) for a CSV or JSONL research file."""
    if not os.path.isfile(path):
        return None, None, 0, None
    mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
    rows = 0
    min_ts = max_ts = None
    try:
        if path.endswith(".jsonl"):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rows += 1
                    try:
                        obj = json.loads(line)
                        raw = obj.get(json_ts_key)
                        if raw:
                            ts = pd.to_datetime(raw, utc=True, errors="coerce")
                            if pd.notna(ts):
                                min_ts = ts if min_ts is None or ts < min_ts else min_ts
                                max_ts = ts if max_ts is None or ts > max_ts else max_ts
                    except Exception:
                        pass
        else:
            try:
                df = pd.read_csv(path, encoding="utf-8", on_bad_lines="skip", low_memory=False)
            except UnicodeDecodeError:
                df = pd.read_csv(path, encoding="latin1", on_bad_lines="skip", low_memory=False)
            rows = len(df)
            for col in ts_cols:
                if col in df.columns:
                    ser = pd.to_datetime(df[col], utc=True, errors="coerce").dropna()
                    if len(ser):
                        cmin, cmax = ser.min(), ser.max()
                        min_ts = cmin if min_ts is None or cmin < min_ts else min_ts
                        max_ts = cmax if max_ts is None or cmax > max_ts else max_ts
    except Exception:
        pass
    fmt = lambda t: t.strftime("%Y-%m-%d %H:%M:%S UTC") if t is not None and pd.notna(t) else "n/a"
    return fmt(min_ts), fmt(max_ts), rows, mtime


def load_research_session() -> dict:
    if not os.path.isfile(RESEARCH_SESSION_FILE):
        return {}
    try:
        with open(RESEARCH_SESSION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _session_start_ts(session: dict):
    if not session:
        return None
    raw = session.get("bot_start_time")
    if raw is None:
        return None
    try:
        return pd.Timestamp(float(raw), unit="s", tz="UTC")
    except Exception:
        return pd.to_datetime(session.get("bot_start_iso"), utc=True, errors="coerce")


def filter_df_since_session(df, session: dict, ts_cols=("ts", "timestamp")):
    """Keep only rows collected after the current bot session started."""
    if df is None or df.empty or not session:
        return df
    start = _session_start_ts(session)
    if start is None or pd.isna(start):
        return df
    for col in ts_cols:
        if col not in df.columns:
            continue
        ser = pd.to_datetime(df[col], utc=True, errors="coerce")
        mask = ser >= start
        out = df.loc[mask].copy()
        dropped = len(df) - len(out)
        if dropped:
            print(f"   Session filter ({col}): {len(df)} -> {len(out)} rows (dropped {dropped} pre-session) {PIPELINE_ENFORCEMENT_TAG}")
        return out
    return df


def apply_session_filters(session: dict, *frames):
    """Filter loaded research frames to current bot session only."""
    if not session:
        print(f"   No {RESEARCH_SESSION_FILE} — analyzing all file rows (run bot from Final Bots for session-only mode) {PIPELINE_ENFORCEMENT_TAG}")
        return frames
    filtered = []
    ts_map = {
        0: ("ts", "close_ts", "entry_ts", "open_ts"),
        1: ("ts", "timestamp"),
        2: ("ts", "timestamp"),
        3: ("ts", "timestamp"),
        4: ("ts", "timestamp"),
        5: ("ts", "timestamp"),
        6: ("ts", "timestamp"),
        7: ("ts", "timestamp"),
        8: ("ts", "timestamp"),
        9: ("ts", "timestamp"),
    }
    for i, df in enumerate(frames):
        filtered.append(filter_df_since_session(df, session, ts_cols=ts_map.get(i, ("ts", "timestamp"))))
    return tuple(filtered)


def print_data_provenance_banner(session: dict = None):
    """Top-of-run banner: where data is read from and what time span it covers."""
    cwd = os.path.abspath(os.getcwd())
    now_local = datetime.now()
    now_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
    launcher = os.path.join(cwd, "15minu_bot.py")
    bot_here = os.path.isfile(launcher)
    session = session or load_research_session()
    start = _session_start_ts(session)
    print("\n" + "=" * 72)
    if start is not None and pd.notna(start):
        hours = (pd.Timestamp.now(tz="UTC") - start).total_seconds() / 3600.0
        print(f">>> SESSION DATA ONLY: last {hours:.1f} hours since bot started <<<")
        print(f">>> Bot session start: {start.strftime('%Y-%m-%d %H:%M:%S UTC')} | version: {session.get('bot_version', 'n/a')} <<<")
    else:
        print(">>> WARNING: no research_session.json — NOT session-filtered (wrong folder or bot not started) <<<")
    print("DATA SOURCE — reads LOCAL files from cwd (not live socket to dashboard)")
    print(f"  Analyzer run time (local):  {now_str}")
    print(f"  Working directory (cwd):    {cwd}")
    if bot_here:
        print(f"  Bot launcher:               {launcher}")
        print("  USE THIS FOLDER — NOT C:\\Users\\user\\qwen_bot.py (wrong copy, stale data).")
    else:
        print("  WARNING: 15minu_bot.py not in cwd — run from Desktop\\Final Bots")
    print("  Analyzer script:            analyzer_research_engine_v62.py (not qwen_bot.py)")
    print("-" * 72)
    spans = [
        (TRADES_FILE, ("ts", "close_ts", "entry_ts", "open_ts")),
        (DECISIONS_FILE, ("ts", "timestamp")),
        (SHADOW_OUTCOME_FILE, ()),
        (SIGNAL_REPLAY_FILE, ()),
        (SIGNAL_SNAPSHOT_FILE, ()),
        (BLOCKED_FILE, ("ts", "timestamp")),
    ]
    for fname, cols in spans:
        tmin, tmax, n, mtime = _file_time_span(fname, ts_cols=cols)
        if n == 0 and not os.path.isfile(fname):
            print(f"  {fname}: MISSING")
        elif n == 0:
            print(f"  {fname}: present (mtime {mtime}) — row count unavailable")
        else:
            print(f"  {fname}: {n} rows | data {tmin} .. {tmax} | file mtime {mtime}")
    print("=" * 72 + "\n")


def _meta_from_frames(*frames):
    """Read exchange/bot_version/analyzer_sync_id from newest CSV rows (if bot logged them)."""
    meta = {}
    for df in frames:
        if df is None or df.empty:
            continue
        for col in ("exchange", "bot_version", "analyzer_sync_id", "data_symbol", "fee_profile"):
            if col in df.columns:
                vals = df[col].dropna().astype(str)
                if len(vals):
                    meta[col] = vals.iloc[-1]
    return meta


def verify_research_sync(trades, decisions, ai_log, blocked, near_edge):
    """Confirm analyzer expectations match bot research CSV metadata (when columns exist)."""
    print(f"\n=== RESEARCH SYNC CHECK — Analyzer {ANALYZER_VERSION} {PIPELINE_ENFORCEMENT_TAG} ===")
    print(f"  Expected bot_version: {EXPECTED_BOT_VERSION}")
    print(f"  Expected exchange: {EXPECTED_EXCHANGE} | symbol: {EXPECTED_SYMBOL}")
    print(f"  analyzer_sync_id: {ANALYZER_SYNC_ID}")
    cwd = os.getcwd()
    print(f"  Working directory: {cwd}")
    missing = [f for f in RESEARCH_CSV_FILES if not os.path.exists(f)]
    if missing:
        print(f"  ℹ️ Missing files (run bot first to create): {', '.join(missing)} {PIPELINE_ENFORCEMENT_TAG}")
    else:
        print(f"  ✅ All standard research files present {PIPELINE_ENFORCEMENT_TAG}")
    opt_missing = [f for f in OPTIONAL_RESEARCH_CSV_FILES if not os.path.exists(f)]
    opt_present = [f for f in OPTIONAL_RESEARCH_CSV_FILES if os.path.exists(f)]
    if opt_present:
        print(f"  ✅ Optional v67 logs present: {', '.join(opt_present)} {PIPELINE_ENFORCEMENT_TAG}")
    if opt_missing:
        print(f"  ℹ️ Optional v67 logs not yet created: {', '.join(opt_missing)} {PIPELINE_ENFORCEMENT_TAG}")

    meta = _meta_from_frames(decisions, trades, ai_log, blocked, near_edge)
    if not meta:
        print(f"  ℹ️ No exchange/bot_version columns in CSVs yet — re-run bot after sync update to stamp rows {PIPELINE_ENFORCEMENT_TAG}")
        print("=" * 60 + "\n")
        return True

    ok = True
    ex = meta.get("exchange", "").lower()
    if ex and ex != EXPECTED_EXCHANGE:
        print(f"  ⚠️ exchange={ex} (expected {EXPECTED_EXCHANGE}) {PIPELINE_ENFORCEMENT_TAG}")
        ok = False
    elif ex:
        print(f"  ✅ exchange={ex} {PIPELINE_ENFORCEMENT_TAG}")

    bv = meta.get("bot_version", "")
    if bv and bv != EXPECTED_BOT_VERSION:
        print(f"  ⚠️ bot_version={bv} (expected {EXPECTED_BOT_VERSION}) {PIPELINE_ENFORCEMENT_TAG}")
        ok = False
    elif bv:
        print(f"  ✅ bot_version={bv} {PIPELINE_ENFORCEMENT_TAG}")

    sid = meta.get("analyzer_sync_id", "")
    if sid and sid != ANALYZER_SYNC_ID:
        print(f"  ⚠️ analyzer_sync_id={sid} (expected {ANALYZER_SYNC_ID}) {PIPELINE_ENFORCEMENT_TAG}")
        ok = False
    elif sid:
        print(f"  ✅ analyzer_sync_id={sid} {PIPELINE_ENFORCEMENT_TAG}")

    sym = meta.get("data_symbol", "")
    if sym:
        fp = meta.get("fee_profile", "n/a")
        print(f"  data_symbol={sym} fee_profile={fp} {PIPELINE_ENFORCEMENT_TAG}")
        if fp and fp != EXPECTED_FEE_PROFILE and fp != "n/a":
            print(f"  ⚠️ fee_profile={fp} (expected {EXPECTED_FEE_PROFILE} for current Bitfinex sim) {PIPELINE_ENFORCEMENT_TAG}")

    if ok:
        print(f"  ✅ Bot ↔ analyzer sync OK {PIPELINE_ENFORCEMENT_TAG}")
    print("=" * 60 + "\n")
    return ok


def safe_float(x):
    try:
        return float(x)
    except:
        return np.nan

def robust_read_csv(filepath, name="file"):
    if not os.path.exists(filepath):
        print(f"⚠️ {name} not found - pipeline stage incomplete {PIPELINE_ENFORCEMENT_TAG}")
        return pd.DataFrame()
    try:
        df = pd.read_csv(filepath, low_memory=False, on_bad_lines="skip", encoding="utf-8")
        print(f"✅ Loaded {name} with utf-8: {len(df)} rows {PIPELINE_ENFORCEMENT_TAG}")
        return df
    except UnicodeDecodeError:
        print(f"⚠️ Unicode error in {name} - trying latin1 fallback {PIPELINE_ENFORCEMENT_TAG}")
        try:
            with open(filepath, "r", encoding="latin1", errors="replace") as f:
                df = pd.read_csv(f, low_memory=False, on_bad_lines="skip")
            print(f"✅ Loaded {name} with latin1 fallback: {len(df)} rows {PIPELINE_ENFORCEMENT_TAG}")
            return df
        except Exception as e:
            print(f"❌ Failed to load {name}: {e} {PIPELINE_ENFORCEMENT_TAG}")
            return pd.DataFrame()
    except Exception as e:
        print(f"❌ Failed to load {name}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return pd.DataFrame()

def dedupe_for_merge(df, key, name="", prefer_col=None, prefer_values=None):
    """Keep one row per key before joining onto trades."""
    if df.empty or key not in df.columns:
        return df
    work = df.copy()
    if prefer_col and prefer_col in work.columns and prefer_values:
        rank = {v: i for i, v in enumerate(prefer_values)}
        default = len(prefer_values)
        work["_merge_rank"] = work[prefer_col].map(lambda v: rank.get(v, default))
        work = work.sort_values("_merge_rank")
        work = work.drop(columns=["_merge_rank"])
    before = len(work)
    work = work.drop_duplicates(subset=[key], keep="last")
    if before != len(work):
        print(f"   Deduped {name}: {before} → {len(work)} rows (1 per {key}) {PIPELINE_ENFORCEMENT_TAG}")
    return work


def safe_merge(left, right, name, key=None, prefer_col=None, prefer_values=None):
    if right.empty:
        print(f"⚠️ {name} empty → skipping merge {PIPELINE_ENFORCEMENT_TAG}")
        return left
    if left.empty:
        return left
    if key is None:
        common_keys = set(left.columns).intersection(set(right.columns))
        if "trade_id" in common_keys:
            key = "trade_id"
        elif "signal_id" in common_keys:
            key = "signal_id"
        else:
            print(f"❌ HARD SKIP: {name} has no common merge key {PIPELINE_ENFORCEMENT_TAG}")
            print(f"   left cols: {list(left.columns)[:20]}...")
            print(f"   right cols: {list(right.columns)[:20]}...")
            return left
    if key not in right.columns or key not in left.columns:
        print(f"❌ HARD SKIP: {name} missing merge key {key} {PIPELINE_ENFORCEMENT_TAG}")
        return left
    right_deduped = dedupe_for_merge(right, key, name, prefer_col, prefer_values)
    overlap = set(left[key].dropna()).intersection(set(right_deduped[key].dropna()))
    if len(overlap) == 0:
        print(f"❌ HARD SKIP: {name} key {key} exists but no matching values {PIPELINE_ENFORCEMENT_TAG}")
        return left
    before = len(left)
    merged = left.merge(right_deduped, on=key, how="left", suffixes=("", f"_{name}"))
    if len(merged) != before:
        print(f"⚠️ MERGE WARNING: {name} changed rows {before} → {len(merged)} — forcing 1 per {key} {PIPELINE_ENFORCEMENT_TAG}")
        merged = merged.drop_duplicates(subset=[key], keep="first")
    print(f"   Merged {name} on {key} (overlap={len(overlap)}, rows={len(merged)}) {PIPELINE_ENFORCEMENT_TAG}")
    return merged

def validate_feature_variance(df):
    print(f"🔍 VALIDATING FEATURE VARIANCE FOR DATA INTEGRITY... {PIPELINE_ENFORCEMENT_TAG}")
    n = len(df)
    if n < 3:
        print(f"   Skipping strict variance checks — only {n} trade(s); need ≥3 for meaningful spread {PIPELINE_ENFORCEMENT_TAG}")
        return df
    critical_features = ["momentum", "volume_ratio", "ema_slope", "velocity", "delta", "edge_score", "features_velocity", "features_volume_ratio", "features_delta"]
    for f in critical_features:
        if f in df.columns:
            try:
                numeric = pd.to_numeric(df[f], errors='coerce')
                std_val = numeric.std()
                if pd.isna(std_val) or std_val < 1e-9:
                    print(f"🚨 CRITICAL: {f} has zero variance → upstream broken {PIPELINE_ENFORCEMENT_TAG}")
                else:
                    print(f"✅ {f} variance OK (std={std_val:.4f}) {PIPELINE_ENFORCEMENT_TAG}")
            except:
                print(f"⚠️ {f} variance check skipped (non-numeric) {PIPELINE_ENFORCEMENT_TAG}")
    return df

def validate_atomic_integrity(df):
    print(f"🔐 VALIDATING ATOMIC INTEGRITY... {PIPELINE_ENFORCEMENT_TAG}")
    if "trade_id" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["trade_id"], keep="last")
        print(f"   Removed {before - len(df)} duplicate trades {PIPELINE_ENFORCEMENT_TAG}")
    critical = ["edge_score", "ai_win_prob", "net_pnl_usd", "entry_delay", "momentum", "volume_ratio", "final_direction", "edge_score_at_entry", "features_velocity", "features_volume_ratio", "features_delta"]
    missing = []
    for col in critical:
        if col in df.columns and df[col].isna().any():
            missing.append(col)
    if missing:
        print(f"⚠️ Atomic warning: missing critical fields {missing} - continuing in partial mode {PIPELINE_ENFORCEMENT_TAG}")
    else:
        print(f"✅ Atomic integrity confirmed - no missing critical fields {PIPELINE_ENFORCEMENT_TAG}")
    return df

def load_data():
    print(f"\n[{datetime.now()}] 🚀 Loading ALL pipeline CSVs... {PIPELINE_ENFORCEMENT_TAG}")
    trades = robust_read_csv(TRADES_FILE, "Trades")
    blocked = robust_read_csv(BLOCKED_FILE, "Blocked")
    decisions = robust_read_csv(DECISIONS_FILE, "Decisions")
    ai_log = robust_read_csv(AI_TRANCHE_FILE, "AI Log")
    setups = robust_read_csv(SETUP_LOG_FILE, "Setups")
    candles = robust_read_csv(CANDLES_FILE, "Candles")
    signal_persist = robust_read_csv(SIGNAL_PERSIST_FILE, "Signal Persist")
    near_edge = robust_read_csv(NEAR_EDGE_FILE, "Near Edge")
    pipeline_events = robust_read_csv(PIPELINE_EVENTS_FILE, "Pipeline Events")
    ai_errors = robust_read_csv(AI_ERRORS_FILE, "AI Errors")

    required = [
        "net_pnl_usd", "conf", "ai_win_prob", "dir", "regime", "exit_reason",
        "r_multiple", "ai_threshold", "ai_approved", "entry_type", "tp_stage",
        "ai_band", "ai_source", "structure", "ai_decision", "dur_min",
        "entry_delay", "slippage", "momentum", "volatility",
        "price_at_signal", "distance_to_resistance", "distance_to_support",
        "edge_score", "edge_threshold", "reason", "decision", "ts", "signal_id", "trade_id",
        "final_direction", "inverted", "edge_score_at_entry", "features_velocity",
        "features_volume_ratio", "features_delta", "controls_edge_threshold"
    ]

    missing_cols = [col for col in required if col not in trades.columns]
    if missing_cols:
        trades = trades.assign(**{col: np.nan for col in missing_cols})
        print(f"   Dynamic schema: added missing columns {missing_cols} {PIPELINE_ENFORCEMENT_TAG}")

    numeric_cols = [
        "net_pnl_usd", "conf", "ai_win_prob", "r_multiple", "ai_threshold",
        "dur_min", "entry_delay", "slippage", "momentum", "volatility",
        "distance_to_resistance", "distance_to_support", "edge_score", "edge_threshold",
        "edge_score_at_entry", "features_velocity", "features_volume_ratio", "features_delta"
    ]
    for col in numeric_cols:
        if col in trades.columns:
            trades[col] = trades[col].apply(safe_float)

    trades = trades.dropna(subset=["net_pnl_usd"]) if not trades.empty else trades

    print(
        f"Loaded: Trades={len(trades)} | Blocked={len(blocked)} | Decisions={len(decisions)} | "
        f"AI={len(ai_log)} | PipelineEvents={len(pipeline_events)} | AIErrors={len(ai_errors)} | "
        f"Setups={len(setups)} | Candles={len(candles)} | SignalPersist={len(signal_persist)} | "
        f"NearEdge={len(near_edge)} {PIPELINE_ENFORCEMENT_TAG}"
    )
    return trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors

def build_master_dataset(trades, blocked, decisions, ai_log, signal_persist, near_edge):
    print("\n=== BUILDING MASTER DATASET (v62 DEDUPED) ===")
    if trades.empty:
        print(f"⚠️ No trades yet - using decisions for partial funnel only {PIPELINE_ENFORCEMENT_TAG}")
        return pd.DataFrame()

    df = trades.drop_duplicates(subset=["trade_id"], keep="last").copy()
    print(f"   Base trades (deduped): {len(df)} rows {PIPELINE_ENFORCEMENT_TAG}")

    if "ai_win_prob" not in df.columns or df["ai_win_prob"].isna().all():
        if "win_prob" in df.columns:
            df["ai_win_prob"] = df["win_prob"]
            print(f"⚠️ Using win_prob fallback for ai_win_prob {PIPELINE_ENFORCEMENT_TAG}")
    if "signal_id" not in df.columns and "trade_id" in df.columns:
        df["signal_id"] = df["trade_id"]
    if "final_direction" not in df.columns:
        df["final_direction"] = df.get("dir", "UNKNOWN")
        print(f"⚠️ final_direction missing - using dir fallback {PIPELINE_ENFORCEMENT_TAG}")

    dec_subset = decisions
    if not decisions.empty and "decision" in decisions.columns:
        dec_subset = decisions[decisions["decision"] != "NO_SIGNAL"].copy()
        print(f"   Decisions for merge: {len(dec_subset)} non-NO_SIGNAL rows {PIPELINE_ENFORCEMENT_TAG}")

    df = safe_merge(df, dec_subset, "decisions", prefer_col="reason", prefer_values=["COMPLETE", "APPROVE", "AI_REJECT"])
    df = safe_merge(df, ai_log, "ai_log", prefer_col="decision", prefer_values=["APPROVE", "REJECT"])

    if not signal_persist.empty:
        df = safe_merge(
            df, signal_persist, "signal_persist",
            prefer_col="stage" if "stage" in signal_persist.columns else None,
            prefer_values=["FILLED", "CLOSED", "AI_DECISION", "SETUP"] if "stage" in signal_persist.columns else None,
        )

    if not near_edge.empty:
        print(f"   Near-edge log: {len(near_edge)} rows — not merged (no trade_id) {PIPELINE_ENFORCEMENT_TAG}")

    if not blocked.empty:
        blocked_copy = blocked.copy()
        blocked_copy["blocked_flag"] = 1
        df = safe_merge(df, blocked_copy, "blocked")
    if "blocked_flag" not in df.columns:
        df["blocked_flag"] = 0
    else:
        df["blocked_flag"] = df["blocked_flag"].fillna(0)

    df = df.drop_duplicates(subset=["trade_id"], keep="first")
    df = validate_atomic_integrity(df)
    df = validate_feature_variance(df)
    df = enrich_v55_features(df)
    print(f"✅ Master dataset ready: {len(df)} rows (= unique trades) {PIPELINE_ENFORCEMENT_TAG}")
    return df


def _profit_factor(pnl_series):
    pnl = pd.to_numeric(pnl_series, errors="coerce").dropna()
    if pnl.empty:
        return np.nan
    wins = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    return wins / losses if losses > 0 else (np.inf if wins > 0 else np.nan)


def _expectancy(pnl_series):
    pnl = pd.to_numeric(pnl_series, errors="coerce").dropna()
    if pnl.empty:
        return np.nan
    wr = (pnl > 0).mean()
    avg_win = pnl[pnl > 0].mean() if (pnl > 0).any() else 0.0
    avg_loss = pnl[pnl < 0].mean() if (pnl < 0).any() else 0.0
    return wr * avg_win + (1 - wr) * avg_loss


def _text_cols(df):
    keys = ("comment", "reason", "full_comment", "ai_reason", "decision_ai_reason", "ai_input", "market_context")
    return [c for c in df.columns if any(k in c.lower() for k in keys)]


def _combine_row_text(row):
    parts = []
    cols = [c for c in row.index if any(k in str(c).lower() for k in ("comment", "reason", "full_comment", "ai_reason", "decision_ai", "ai_input", "market_context", "extra"))]
    for c in cols:
        v = row.get(c)
        if isinstance(v, str) and len(v) > 2:
            parts.append(v)
        elif v is not None and not (isinstance(v, float) and np.isnan(v)):
            parts.append(str(v))
    return "\n".join(parts)


def parse_v55_fields_from_text(text):
    """Extract Phase A/B/C fields from AI JSON blocks or log prose."""
    out = {}
    if not isinstance(text, str) or len(text) < 4:
        return out
    jmatch = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if jmatch:
        try:
            blob = json.loads(jmatch.group(1))
            if isinstance(blob, dict):
                out["bull_score"] = blob.get("bull_score")
                out["bear_score"] = blob.get("bear_score")
                if blob.get("direction"):
                    out["ai_json_direction"] = str(blob.get("direction")).upper()
        except Exception:
            pass
    for pat, key in (
        (r'"bull_score"\s*:\s*(\d+)', "bull_score"),
        (r'"bear_score"\s*:\s*(\d+)', "bear_score"),
        (r'"structure_score"\s*:\s*(-?\d+)', "structure_score"),
        (r'"agreement"\s*:\s*"([A-Z_]+)"', "mtf_agreement"),
        (r'"adx"\s*:\s*([\d.]+)', "adx"),
        (r'"trend_score"\s*:\s*(-?\d+)', "trend_score"),
        (r'"rate_pct_per_8h"\s*:\s*([\d.]+)', "funding_rate_pct_8h"),
        (r'Bull\s*score:\s*(\d+)', "bull_score"),
        (r'Bear\s*score:\s*(\d+)', "bear_score"),
        (r'structure_score\s*[=:]\s*(-?\d+)', "structure_score"),
        (r'(BEAR_ALIGNED|BULL_ALIGNED|CONFLICTED|MIXED)', "mtf_agreement"),
    ):
        if key in out and out.get(key) is not None:
            continue
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        val = m.group(1)
        if key in ("structure_score", "trend_score", "bull_score", "bear_score"):
            try:
                out[key] = int(val)
            except Exception:
                pass
        elif key in ("adx", "funding_rate_pct_8h"):
            try:
                out[key] = float(val)
            except Exception:
                pass
        elif key == "mtf_agreement":
            out[key] = str(val).upper()
        else:
            out[key] = val
    if "structure_bias" not in out:
        if re.search(r"BEARISH_STRUCTURE|LEAN_BEAR", text, re.I):
            out["structure_bias"] = "BEARISH"
        elif re.search(r"BULLISH_STRUCTURE|LEAN_BULL|HH.?HL", text, re.I):
            out["structure_bias"] = "BULLISH"
    return out


def enrich_v55_features(df):
    """Add v5.5 thesis columns from CSV fields + parsed AI comments."""
    if df.empty:
        return df
    work = df.copy()
    numeric_defaults = {
        "bull_score_at_entry": np.nan,
        "bear_score_at_entry": np.nan,
        "structure_score_at_entry": np.nan,
        "mtf_agreement_at_entry": None,
        "adx_at_entry": np.nan,
        "trend_score_at_entry": np.nan,
        "funding_rate_pct_8h_at_entry": np.nan,
        "mfe_margin_pct": np.nan,
        "mae_margin_pct": np.nan,
        "final_pnl_margin_pct": np.nan,
        "profit_left_on_table": np.nan,
        "factor_spread": np.nan,
        "contradiction_index": np.nan,
        "directional_factor_spread": np.nan,
        "exit_failure_flag": 0,
        "thesis_conflict_flag": 0,
    }
    for col, default in numeric_defaults.items():
        if col not in work.columns:
            work[col] = default

    if "max_profit" in work.columns:
        work["mfe_margin_pct"] = pd.to_numeric(work["max_profit"], errors="coerce")
    if "max_drawdown" in work.columns:
        work["mae_margin_pct"] = pd.to_numeric(work["max_drawdown"], errors="coerce")
    if "pnl" in work.columns:
        work["final_pnl_margin_pct"] = pd.to_numeric(work["pnl"], errors="coerce")
    elif "outcome_pnl_pct" in work.columns:
        work["final_pnl_margin_pct"] = pd.to_numeric(work["outcome_pnl_pct"], errors="coerce")

    parsed_rows = []
    for idx, row in work.iterrows():
        blob = parse_v55_fields_from_text(_combine_row_text(row))
        parsed_rows.append(blob)
    parsed_df = pd.DataFrame(parsed_rows, index=work.index)

    for src, dst in (
        ("bull_score", "bull_score_at_entry"),
        ("bear_score", "bear_score_at_entry"),
        ("structure_score", "structure_score_at_entry"),
        ("mtf_agreement", "mtf_agreement_at_entry"),
        ("adx", "adx_at_entry"),
        ("trend_score", "trend_score_at_entry"),
        ("funding_rate_pct_8h", "funding_rate_pct_8h_at_entry"),
    ):
        if src in parsed_df.columns:
            work[dst] = work[dst].fillna(parsed_df[src]) if dst in work.columns else parsed_df[src]

    bull = pd.to_numeric(work["bull_score_at_entry"], errors="coerce")
    bear = pd.to_numeric(work["bear_score_at_entry"], errors="coerce")
    work["factor_spread"] = bull - bear
    work["contradiction_index"] = bull + bear
    direction = work.get("final_direction", work.get("dir", pd.Series("UNKNOWN", index=work.index))).astype(str).str.upper()
    work["directional_factor_spread"] = np.where(
        direction == "LONG", bull - bear,
        np.where(direction == "SHORT", bear - bull, bull - bear),
    )
    struct = pd.to_numeric(work["structure_score_at_entry"], errors="coerce")
    work["thesis_conflict_flag"] = (
        ((direction == "LONG") & (struct < -2) & (bull > bear + 1))
        | ((direction == "SHORT") & (struct > 2) & (bear > bull + 1))
    ).astype(int)

    mfe = pd.to_numeric(work["mfe_margin_pct"], errors="coerce")
    final = pd.to_numeric(work["final_pnl_margin_pct"], errors="coerce")
    work["profit_left_on_table"] = mfe - final
    work["exit_failure_flag"] = (
        (mfe >= 12) & (final < mfe - 5) & (final < 10)
    ).astype(int)

    parsed_n = parsed_df["bull_score"].notna().sum() if "bull_score" in parsed_df.columns else 0
    print(f"   v5.5 enrich: parsed bull/bear from AI text for {parsed_n}/{len(work)} trades {PIPELINE_ENFORCEMENT_TAG}")
    return work


def _bucket_table(df, col, bins, labels, title):
    print(f"\n=== {title} ===")
    if col not in df.columns or df.empty:
        print(f"Missing {col}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    if len(labels) != len(bins) - 1:
        print(
            f"Bucket config error for {col}: need {len(bins) - 1} labels, got {len(labels)}. "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
        return
    work = df.copy()
    series = pd.to_numeric(work[col], errors="coerce")
    if series.notna().sum() == 0:
        print(f"No numeric values for {col}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    try:
        work["_bucket"] = pd.cut(series, bins=bins, labels=labels, right=True, duplicates="drop")
    except ValueError as exc:
        raw = series.dropna().tolist()
        print(f"Skipping buckets for {col} ({exc}). Raw values: {raw} {PIPELINE_ENFORCEMENT_TAG}")
        return
    rows = []
    for b, g in work.groupby("_bucket", observed=True):
        pnl = pd.to_numeric(g["net_pnl_usd"], errors="coerce")
        if len(g) == 0:
            continue
        rows.append({
            "bucket": str(b),
            "trades": len(g),
            "win_rate_pct": round((pnl > 0).mean() * 100, 1),
            "sum_pnl_usd": round(pnl.sum(), 2),
            "avg_pnl_usd": round(pnl.mean(), 2),
            "profit_factor": round(_profit_factor(pnl), 2) if len(g) > 1 else np.nan,
            "expectancy": round(_expectancy(pnl), 2),
        })
    if not rows:
        print(f"No buckets for {col}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(pd.DataFrame(rows).to_string(index=False))
    print(PIPELINE_ENFORCEMENT_TAG)


def ladder_lock_floor(peak_pct):
    """Highest lock floor reached for a given peak margin % (matches bybit_bot.get_profit_lock_floor)."""
    if peak_pct is None or (isinstance(peak_pct, float) and np.isnan(peak_pct)):
        return None, None
    try:
        peak = float(peak_pct)
    except (TypeError, ValueError):
        return None, None
    if peak < TRAIL_LADDER[0][0]:
        return None, None
    trigger_hit, lock_floor = None, None
    for trigger, lock in TRAIL_LADDER:
        if peak >= trigger:
            trigger_hit, lock_floor = trigger, lock
    if peak >= PEAK_NEVER_LOSER_MIN_PEAK:
        lock_floor = max(lock_floor or 0, PEAK_NEVER_LOSER_FLOOR)
    return trigger_hit, lock_floor


def _parse_pullback_threshold(row):
    for key in ("pullback_threshold", "controls_pullback_pct", "pullback_pct"):
        if key in row.index and pd.notna(row.get(key)):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                pass
    for key in ("controls", "controls_json"):
        raw = row.get(key)
        if isinstance(raw, str) and "pullback" in raw:
            m = re.search(r"pullback_threshold['\"]?\s*:\s*([\d.]+)", raw)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
    return DEFAULT_PULLBACK_PCT


def _achieved_pullback_pct(row):
    """Price pullback captured between signal and fill (fraction, not %)."""
    signal = pd.to_numeric(row.get("price_at_signal"), errors="coerce")
    entry = pd.to_numeric(row.get("entry"), errors="coerce")
    if pd.isna(signal) or pd.isna(entry) or signal <= 0 or entry <= 0:
        return np.nan
    direction = str(row.get("final_direction", row.get("dir", ""))).upper()
    if direction == "SHORT":
        return (entry - signal) / signal
    if direction == "LONG":
        return (signal - entry) / signal
    return np.nan


def post_signal_price_excursion(df):
    """Favorable vs adverse price movement during the trade (margin % from max_profit / max_drawdown)."""
    print("\n=== POST-SIGNAL PRICE EXCURSION (favorable vs adverse) ===")
    if df.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    mfe = pd.to_numeric(df.get("mfe_margin_pct", df.get("max_profit", 0)), errors="coerce")
    mae = pd.to_numeric(df.get("mae_margin_pct", df.get("max_drawdown", 0)), errors="coerce")
    final = pd.to_numeric(df.get("final_pnl_margin_pct", df.get("pnl", 0)), errors="coerce")
    net = pd.to_numeric(df.get("net_pnl_usd", 0), errors="coerce")
    print(f"Avg favorable excursion (MFE): {mfe.mean():.2f}% margin | Avg adverse (MAE): {mae.mean():.2f}% {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg booked final: {final.mean():.2f}% margin | Capture ratio (final/MFE): {(final / mfe.replace(0, np.nan)).mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    winners = df[net > 0]
    losers = df[net <= 0]
    if len(winners):
        print(f"Winners ({len(winners)}): MFE {pd.to_numeric(winners.get('mfe_margin_pct', winners.get('max_profit')), errors='coerce').mean():.1f}% → final {pd.to_numeric(winners.get('final_pnl_margin_pct', winners.get('pnl')), errors='coerce').mean():.1f}% {PIPELINE_ENFORCEMENT_TAG}")
    if len(losers):
        print(f"Losers ({len(losers)}): MAE {pd.to_numeric(losers.get('mae_margin_pct', losers.get('max_drawdown')), errors='coerce').mean():.1f}% | MFE {pd.to_numeric(losers.get('mfe_margin_pct', losers.get('max_profit')), errors='coerce').mean():.1f}% {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def trail_ladder_forensics(df):
    """Per-trade ladder rung: peak → lock floor → exit; explains PROFIT_LOCK_LADDER bookings."""
    print("\n=== TRAIL LADDER FORENSICS (peak → lock floor → booked) ===")
    print("Ladder rungs (peak trigger % → lock floor % margin):")
    for trig, lock in TRAIL_LADDER:
        print(f"  peak >= {trig:3d}%  →  lock floor {lock:3d}%")
    print(f"  peak >= {PEAK_NEVER_LOSER_MIN_PEAK:.0f}%  →  never-loser floor {PEAK_NEVER_LOSER_FLOOR:.0f}%")
    print(f"Exit fires when unrealized margin falls back to the lock floor (not at peak). {PIPELINE_ENFORCEMENT_TAG}")
    if df.empty:
        return
    rows = []
    for _, row in df.iterrows():
        peak = pd.to_numeric(row.get("mfe_margin_pct", row.get("max_profit")), errors="coerce")
        final_m = pd.to_numeric(row.get("final_pnl_margin_pct", row.get("pnl")), errors="coerce")
        net = pd.to_numeric(row.get("net_pnl_usd"), errors="coerce")
        trig, lock = ladder_lock_floor(peak)
        left = (peak - final_m) if pd.notna(peak) and pd.notna(final_m) else np.nan
        margin_usdt = pd.to_numeric(row.get("margin_usdt"), errors="coerce")
        roi_pct = (net / margin_usdt * 100) if pd.notna(net) and pd.notna(margin_usdt) and margin_usdt > 0 else np.nan
        rows.append({
            "trade_id": str(row.get("trade_id", ""))[:8],
            "exit": row.get("exit_reason", ""),
            "peak_mfe%": round(peak, 1) if pd.notna(peak) else None,
            "ladder_trig%": trig,
            "lock_floor%": lock,
            "booked_margin%": round(final_m, 1) if pd.notna(final_m) else None,
            "net_usd": round(net, 2) if pd.notna(net) else None,
            "roi_on_margin%": round(roi_pct, 1) if pd.notna(roi_pct) else None,
            "left_on_table%": round(left, 1) if pd.notna(left) else None,
        })
    view = pd.DataFrame(rows)
    print(view.to_string(index=False))
    ladder_exits = df[df.get("exit_reason", pd.Series(dtype=str)) == "PROFIT_LOCK_LADDER"] if "exit_reason" in df.columns else pd.DataFrame()
    if not ladder_exits.empty:
        peaks = pd.to_numeric(ladder_exits.get("mfe_margin_pct", ladder_exits.get("max_profit")), errors="coerce")
        finals = pd.to_numeric(ladder_exits.get("final_pnl_margin_pct", ladder_exits.get("pnl")), errors="coerce")
        print(f"\nPROFIT_LOCK_LADDER trades: {len(ladder_exits)} | avg peak {peaks.mean():.1f}% → avg booked {finals.mean():.1f}% | avg left {(peaks - finals).mean():.1f}% {PIPELINE_ENFORCEMENT_TAG}")
        print("Note: net_usd (e.g. $8.81) × 100 / margin_usdt = ROI% (e.g. $8.81 on $20 margin ≈ 44%). Ladder lock is in margin %, not USD. {PIPELINE_ENFORCEMENT_TAG}".replace("{PIPELINE_ENFORCEMENT_TAG}", PIPELINE_ENFORCEMENT_TAG))
    early = df[df.get("exit_reason", pd.Series(dtype=str)) == "THESIS_FAST_CUT"] if "exit_reason" in df.columns else pd.DataFrame()
    if not early.empty:
        ep = pd.to_numeric(early.get("mfe_margin_pct", early.get("max_profit")), errors="coerce")
        print(f"THESIS_FAST_CUT ({len(early)}): exited before ladder (peak < {TRAIL_LADDER[0][0]}% or thesis cut) — avg peak {ep.mean():.1f}% {PIPELINE_ENFORCEMENT_TAG}")
    for reason in ("TYPE_A_STALL", "TREND_WEAKENING"):
        sub = df[df.get("exit_reason", pd.Series(dtype=str)) == reason] if "exit_reason" in df.columns else pd.DataFrame()
        if not sub.empty:
            net = pd.to_numeric(sub.get("net_pnl_usd"), errors="coerce")
            print(f"{reason} ({len(sub)}): net ${net.sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def pullback_optimization_analysis(df, decisions=None):
    """Sweep pullback % vs post-signal price path to find optimum fill threshold."""
    print("\n=== OPTIMUM PULLBACK % ANALYSIS (post-signal price movement) ===")
    if df.empty:
        print(f"No executed trades for pullback sweep. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    work = df.copy()
    work["configured_pullback"] = work.apply(_parse_pullback_threshold, axis=1)
    work["achieved_pullback"] = work.apply(_achieved_pullback_pct, axis=1)
    work["entry_delay_min"] = pd.to_numeric(work.get("entry_delay"), errors="coerce")
    work["net_pnl"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    work["mfe"] = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce")

    achieved = work["achieved_pullback"].dropna()
    if len(achieved):
        print(f"Achieved pullback at fill (price move signal→entry): mean {achieved.mean()*100:.3f}% | median {achieved.median()*100:.3f}% | max {achieved.max()*100:.3f}% {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Configured pullback_threshold: {work['configured_pullback'].mode().iloc[0]*100:.2f}% (mode) {PIPELINE_ENFORCEMENT_TAG}")
        buckets = pd.cut(achieved * 100, bins=[-0.1, 0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 10], right=True)
        agg = work.groupby(buckets, observed=True).agg(
            n=("trade_id", "count"),
            avg_pnl=("net_pnl", "mean"),
            sum_pnl=("net_pnl", "sum"),
            win_rate=("net_pnl", lambda s: (s > 0).mean()),
            avg_mfe=("mfe", "mean"),
        ).round(2)
        print("\nPnL by achieved pullback bucket (% price move from signal to entry):")
        print(agg.to_string())
    else:
        print(f"price_at_signal/entry missing — cannot measure achieved pullback. {PIPELINE_ENFORCEMENT_TAG}")

    # Sweep: would this pullback threshold still have filled + what PnL?
    sweep_rows = []
    market_types = {"SIM_MARKET", "MARKET", "TAKER"}
    for pb in DEFAULT_PULLBACK_THRESHOLDS:
        filled_pnl = []
        filled_mfe = []
        missed = 0
        for _, row in work.iterrows():
            entry_type = str(row.get("entry_type", "")).upper()
            achieved_pb = row.get("achieved_pullback")
            net = row.get("net_pnl")
            mfe = row.get("mfe")
            if pd.isna(net):
                continue
            instant = entry_type in market_types or (pd.notna(row.get("entry_delay_min")) and row["entry_delay_min"] < 0.05)
            if instant:
                filled_pnl.append(net)
                if pd.notna(mfe):
                    filled_mfe.append(mfe)
                continue
            if pd.isna(achieved_pb):
                missed += 1
                continue
            if achieved_pb >= pb - 1e-9:
                filled_pnl.append(net)
                if pd.notna(mfe):
                    filled_mfe.append(mfe)
            else:
                missed += 1
        if not filled_pnl:
            continue
        pnl_s = pd.Series(filled_pnl)
        wr = (pnl_s > 0).mean()
        score = pnl_s.sum() * wr
        sweep_rows.append({
            "pullback_%": round(pb * 100, 2),
            "fills": len(filled_pnl),
            "missed": missed,
            "sum_pnl_usd": round(pnl_s.sum(), 2),
            "avg_pnl_usd": round(pnl_s.mean(), 2),
            "win_rate_pct": round(wr * 100, 1),
            "avg_mfe_margin%": round(pd.Series(filled_mfe).mean(), 1) if filled_mfe else None,
            "score": round(score, 2),
        })
    if sweep_rows:
        sweep_df = pd.DataFrame(sweep_rows).sort_values("score", ascending=False)
        print("\nPullback threshold sweep (higher pullback = better limit price, fewer fills):")
        print(sweep_df.to_string(index=False))
        best = sweep_df.iloc[0]
        print(f"\n🎯 Suggested optimum pullback: {best['pullback_%']:.2f}% — score={best['score']:.2f} fills={int(best['fills'])} sum_pnl=${best['sum_pnl_usd']:.2f} WR={best['win_rate_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}")
        cur = sweep_df[sweep_df["pullback_%"] == round(work["configured_pullback"].iloc[0] * 100, 2)]
        if not cur.empty:
            c = cur.iloc[0]
            print(f"   Current config {c['pullback_%']:.2f}%: sum_pnl=${c['sum_pnl_usd']:.2f} fills={int(c['fills'])} {PIPELINE_ENFORCEMENT_TAG}")
        return float(best["pullback_%"]) / 100.0
    print(f"Insufficient data for pullback sweep. {PIPELINE_ENFORCEMENT_TAG}")
    return None


def _margin_pct_to_usd(margin_pct, margin_usdt):
    try:
        return float(margin_pct) / 100.0 * float(margin_usdt)
    except (TypeError, ValueError):
        return np.nan


def _ladder_lock_for_peak_custom(peak_pct, ladder):
    if peak_pct is None or (isinstance(peak_pct, float) and np.isnan(peak_pct)):
        return None, None
    try:
        peak = float(peak_pct)
    except (TypeError, ValueError):
        return None, None
    if peak < ladder[0][0]:
        return None, None
    trigger_hit, lock_floor = None, None
    for trigger, lock in ladder:
        if peak >= trigger:
            trigger_hit, lock_floor = trigger, lock
    if peak >= PEAK_NEVER_LOSER_MIN_PEAK:
        lock_floor = max(lock_floor or 0, PEAK_NEVER_LOSER_FLOOR)
    return trigger_hit, lock_floor


def _load_jsonl_replays():
    if not os.path.exists(SIGNAL_REPLAY_FILE):
        return {}
    replays = {}
    try:
        with open(SIGNAL_REPLAY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                tid = row.get("trade_id")
                if tid:
                    replays[tid] = row
    except Exception as e:
        print(f"⚠️ signal_replay.jsonl read error: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return replays


def _load_jsonl_by_trade_id(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                tid = row.get("trade_id")
                if tid:
                    rows[tid] = row
    except Exception as e:
        print(f"⚠️ {path} read error: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return rows


def shadow_approve_pnl_analysis(decisions, trades, blocked):
    """Blocked APPROVE shadow PnL — what would have happened if gates had not blocked."""
    print("\n=== BLOCKED APPROVE SHADOW PnL (counterfactual execution) ===")
    shadow = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    counter = _load_jsonl_by_trade_id(COUNTERFACTUAL_FILE)
    if not shadow and not counter:
        print(
            f"No shadow_outcome.jsonl / counterfactual.jsonl yet — restart bot on "
            f"{EXPECTED_BOT_VERSION} and collect APPROVE signals. {PIPELINE_ENFORCEMENT_TAG}"
        )
        return None
    work = shadow if shadow else counter
    df = pd.DataFrame(list(work.values()))
    if df.empty:
        print(f"Shadow file empty. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    df["net_pnl_usd"] = pd.to_numeric(df.get("net_pnl_usd"), errors="coerce")
    df["filled"] = df.get("filled", True)
    df["ai_win_prob"] = pd.to_numeric(df.get("ai_win_prob"), errors="coerce")
    executed_ids = set(trades["trade_id"].dropna()) if not trades.empty and "trade_id" in trades.columns else set()
    df["outcome_lane"] = df["trade_id"].apply(lambda x: "EXECUTED" if x in executed_ids else "BLOCKED_SHADOW")
    blocked_shadow = df[df["outcome_lane"] == "BLOCKED_SHADOW"]
    print(f"Shadow rows: {len(df)} | blocked APPROVE shadows: {len(blocked_shadow)} {PIPELINE_ENFORCEMENT_TAG}")
    if blocked_shadow.empty:
        print(f"No blocked APPROVE shadows yet (all APPROVEs executed or no blocks). {PIPELINE_ENFORCEMENT_TAG}")
        return df
    filled = blocked_shadow[blocked_shadow["filled"] == True]
    no_fill = blocked_shadow[blocked_shadow["filled"] != True]
    print(f"  Would-have-filled: {len(filled)} | NO_FILL (pullback TTL): {len(no_fill)} {PIPELINE_ENFORCEMENT_TAG}")
    if "block_reason" in blocked_shadow.columns:
        print("\nShadow PnL by block reason:")
        grp = blocked_shadow.groupby("block_reason").agg(
            n=("trade_id", "count"),
            sum_pnl=("net_pnl_usd", "sum"),
            avg_pnl=("net_pnl_usd", "mean"),
            win_rate=("net_pnl_usd", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean()),
        ).round(2)
        print(grp.to_string())
        print(PIPELINE_ENFORCEMENT_TAG)
    pnl = pd.to_numeric(blocked_shadow["net_pnl_usd"], errors="coerce")
    missed_winners = blocked_shadow[pnl > 0]
    good_blocks = blocked_shadow[pnl <= 0]
    print(f"\nMissed winners (blocked but shadow +$): {len(missed_winners)} sum=${pnl[pnl > 0].sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Good blocks (blocked and shadow ≤$0): {len(good_blocks)} sum=${pnl[pnl <= 0].sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if len(missed_winners) > 0 and "block_reason" in missed_winners.columns:
        print(f"Top block reasons for missed winners:\n{missed_winners['block_reason'].value_counts().head(5).to_string()} {PIPELINE_ENFORCEMENT_TAG}")
    return df


def real_edge_report(trades, decisions, shadow_df=None):
    """True APPROVE funnel edge: executed PnL + blocked shadow PnL vs counterfactual all-in."""
    print("\n=== REAL EDGE REPORT (APPROVE funnel true EV) ===")
    if decisions.empty:
        print(f"No decisions. {PIPELINE_ENFORCEMENT_TAG}")
        return
    approves = decisions[decisions.get("reason", pd.Series()) == "APPROVE"]["trade_id"].dropna().unique()
    if len(approves) == 0:
        print(f"No APPROVE rows. {PIPELINE_ENFORCEMENT_TAG}")
        return
    executed_pnl = 0.0
    executed_n = 0
    if not trades.empty and "trade_id" in trades.columns:
        ex = trades[trades["trade_id"].isin(approves)]
        executed_n = len(ex)
        executed_pnl = pd.to_numeric(ex.get("net_pnl_usd"), errors="coerce").sum()
    shadow_pnl = 0.0
    shadow_n = 0
    if shadow_df is not None and not shadow_df.empty:
        bs = shadow_df[shadow_df.get("outcome_lane") == "BLOCKED_SHADOW"]
        shadow_n = len(bs)
        shadow_pnl = pd.to_numeric(bs.get("net_pnl_usd"), errors="coerce").sum()
    total_approve = len(approves)
    blocked_n = total_approve - executed_n
    counterfactual_all = executed_pnl + shadow_pnl
    actual_only = executed_pnl
    print(f"APPROVE attempts: {total_approve} | executed: {executed_n} | blocked: {blocked_n} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Executed net PnL: ${actual_only:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Blocked shadow net PnL (if all had traded): ${shadow_pnl:.2f} ({shadow_n} shadows) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Counterfactual all-APPROVE sum: ${counterfactual_all:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if total_approve > 0:
        print(f"Per-APPROVE EV (counterfactual): ${counterfactual_all / total_approve:.2f} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Per-APPROVE EV (executed only): ${actual_only / max(1, executed_n):.2f} on {executed_n} trades {PIPELINE_ENFORCEMENT_TAG}")
    if shadow_n > 0 and blocked_n > 0:
        block_quality = (pd.to_numeric(shadow_df[shadow_df.get("outcome_lane") == "BLOCKED_SHADOW"]["net_pnl_usd"], errors="coerce") <= 0).mean()
        print(f"Block quality (% blocked shadows ≤$0): {block_quality * 100:.1f}% {PIPELINE_ENFORCEMENT_TAG}")
    if counterfactual_all > actual_only:
        print(f"⚠️ Gates cost ~${counterfactual_all - actual_only:.2f} vs trading every APPROVE (shadow). Review block reasons. {PIPELINE_ENFORCEMENT_TAG}")
    elif counterfactual_all < actual_only:
        print(f"✅ Gates saved ~${actual_only - counterfactual_all:.2f} vs trading every APPROVE. {PIPELINE_ENFORCEMENT_TAG}")


def counterfactual_vs_actual_analysis(trades, shadow_df=None):
    """Side-by-side executed vs shadow for same trade_ids where both exist."""
    print("\n=== COUNTERFACTUAL vs ACTUAL (executed trades) ===")
    if trades.empty or shadow_df is None or shadow_df.empty:
        print(f"Need trades + shadow rows. {PIPELINE_ENFORCEMENT_TAG}")
        return
    if "trade_id" not in trades.columns:
        return
    ex = trades.copy()
    ex["actual_pnl"] = pd.to_numeric(ex.get("net_pnl_usd"), errors="coerce")
    sh = shadow_df.copy()
    sh["shadow_pnl"] = pd.to_numeric(sh.get("net_pnl_usd"), errors="coerce")
    merged = ex.merge(sh[["trade_id", "shadow_pnl", "exit_reason"]], on="trade_id", how="inner", suffixes=("", "_shadow"))
    if merged.empty:
        print(f"No overlapping trade_ids (executed use live path, not shadow). {PIPELINE_ENFORCEMENT_TAG}")
        return
    merged["delta"] = merged["actual_pnl"] - merged["shadow_pnl"]
    print(f"Overlapping rows: {len(merged)} | mean actual=${merged['actual_pnl'].mean():.2f} shadow=${merged['shadow_pnl'].mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")


def _simulate_ticks_fast_cut_ladder(
    ticks, entry, direction, leverage, margin_usdt, fast_cut_pct, ladder, thesis_exit_above,
    fill_t=None, mfe_protect_pct=None,
):
    """Tick walk: fast cut + profit ladder only (no thesis flip — needs live scores)."""
    if not ticks or not entry or entry <= 0:
        return None
    dir_factor = 1 if str(direction).upper() == "LONG" else -1
    peak = 0.0
    last_unreal = 0.0
    if fill_t is None:
        for tick in ticks:
            if tick.get("unreal_pct") is not None:
                fill_t = float(tick.get("t", 0))
                break
    for tick in sorted(ticks, key=lambda x: x.get("seq", 0)):
        price = tick.get("price")
        if price is None or price <= 0:
            continue
        t_rel = float(tick.get("t", 0))
        if fill_t is not None and t_rel < fill_t:
            continue
        unreal = ((price - entry) / entry) * dir_factor * leverage * 100
        peak = max(peak, unreal)
        if peak >= ladder[0][0]:
            _, lock = _ladder_lock_for_peak_custom(peak, ladder)
            if lock is not None and unreal <= lock:
                return _margin_pct_to_usd(lock, margin_usdt), "PROFIT_LOCK_LADDER", peak
        if unreal > thesis_exit_above:
            continue
        if peak >= ladder[0][0]:
            continue
        if unreal <= fast_cut_pct:
            if mfe_protect_pct is not None and mfe_protect_pct > 0 and peak >= mfe_protect_pct:
                last_unreal = unreal
                continue
            return _margin_pct_to_usd(fast_cut_pct, margin_usdt), "THESIS_FAST_CUT", peak
        last_unreal = unreal
    if ticks:
        return _margin_pct_to_usd(last_unreal, margin_usdt), "REPLAY_END", peak
    return None


def _replay_entry_price(replay: dict):
    """Entry for tick sim: virtual fill from replay buffer."""
    for key in ("virtual_entry", "entry", "fill_price"):
        v = replay.get(key)
        if v is not None and float(v) > 0:
            return float(v)
    ticks = replay.get("ticks") or []
    for tick in ticks:
        if tick.get("unreal_pct") is not None:
            return tick.get("price")
    return replay.get("start_price")


def stop_thesis_wide_sweep_all_replays(trades_df=None):
    """
    Sweep thesis fast-cut / stop placement on ALL signal_replay.jsonl paths
    (executed + blocked APPROVE shadows). Uses tick-accurate replay.
    """
    print("\n=== STOP / THESIS FAST-CUT WIDE SWEEP (all replays) ===")
    sweep_levels = _build_stop_thesis_sweep_levels()
    print(
        "This is the THESIS STOP (margin %), NOT entry pullback %. "
        f"Current bot default: {THESIS_FAST_EXIT_DEFAULT}% margin. "
        f"Hard SL margin cap: 30% at 100x. "
        f"Sweep: {len(sweep_levels)} levels from -6% to -{int(STOP_THESIS_SWEEP_MAX_MARGIN_PCT):.0f}% margin. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    replays = _load_jsonl_replays()
    if not replays:
        print(f"No {SIGNAL_REPLAY_FILE} — run bot longer to collect APPROVE replays. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    executed_ids = set()
    if trades_df is not None and not trades_df.empty and "trade_id" in trades_df.columns:
        executed_ids = set(trades_df["trade_id"].dropna())

    rows = []
    per_cut = {c: {"sum": 0.0, "n": 0, "wins": 0, "recoveries": 0, "ladder": 0, "thesis": 0, "replay_end": 0} for c in sweep_levels}
    baseline_cut = THESIS_FAST_EXIT_DEFAULT
    baseline_pnls = {}

    for tid, replay in replays.items():
        ticks = replay.get("ticks") or []
        entry = _replay_entry_price(replay)
        if not ticks or not entry or entry <= 0:
            continue
        margin = float(replay.get("margin_usdt") or 20.0)
        lev = int(replay.get("leverage") or 100)
        direction = replay.get("direction", "SHORT")
        lane = replay.get("lane", "unknown")
        mfe = max(
            (float(t.get("unreal_pct")) for t in ticks if t.get("unreal_pct") is not None),
            default=0.0,
        )
        mae = min(
            (float(t.get("unreal_pct")) for t in ticks if t.get("unreal_pct") is not None),
            default=0.0,
        )

        fill_t = replay.get("virtual_fill_t")
        for cut_pct in sweep_levels:
            sim_usd, exit_reason, peak = _simulate_ticks_fast_cut_ladder(
                ticks, entry, direction, lev, margin,
                cut_pct, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=fill_t,
            )
            if sim_usd is None:
                continue
            per_cut[cut_pct]["sum"] += sim_usd
            per_cut[cut_pct]["n"] += 1
            if sim_usd > 0:
                per_cut[cut_pct]["wins"] += 1
            if exit_reason == "PROFIT_LOCK_LADDER":
                per_cut[cut_pct]["ladder"] += 1
            elif exit_reason == "THESIS_FAST_CUT":
                per_cut[cut_pct]["thesis"] += 1
            elif exit_reason == "REPLAY_END":
                per_cut[cut_pct]["replay_end"] += 1
            if cut_pct == baseline_cut:
                baseline_pnls[tid] = sim_usd

        rows.append({
            "trade_id": tid[:8],
            "lane": lane,
            "executed": tid in executed_ids,
            "mfe%": round(mfe, 1),
            "mae%": round(mae, 1),
            "ticks": len(ticks),
        })

    sweep_rows = []
    for cut_pct in sweep_levels:
        d = per_cut[cut_pct]
        if not d["n"]:
            continue
        recoveries = 0
        if cut_pct != baseline_cut and baseline_pnls:
            for tid, replay in replays.items():
                if tid not in baseline_pnls:
                    continue
                entry = _replay_entry_price(replay)
                if not entry:
                    continue
                ft = replay.get("virtual_fill_t")
                b_usd, _, _ = _simulate_ticks_fast_cut_ladder(
                    replay.get("ticks"), entry, replay.get("direction"), int(replay.get("leverage") or 100),
                    float(replay.get("margin_usdt") or 20), baseline_cut, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=ft,
                ) or (0, None, 0)
                w_usd, _, _ = _simulate_ticks_fast_cut_ladder(
                    replay.get("ticks"), entry, replay.get("direction"), int(replay.get("leverage") or 100),
                    float(replay.get("margin_usdt") or 20), cut_pct, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=ft,
                ) or (0, None, 0)
                if b_usd <= 0 and w_usd > 0:
                    recoveries += 1
        sweep_rows.append({
            "stop_margin_%": cut_pct,
            "replays": d["n"],
            "sum_pnl_usd": round(d["sum"], 2),
            "avg_pnl_usd": round(d["sum"] / d["n"], 2),
            "win_rate_pct": round(100 * d["wins"] / d["n"], 1),
            "ladder_exits": d["ladder"],
            "thesis_exits": d["thesis"],
            "replay_end": d["replay_end"],
            "loss_to_win_recoveries": recoveries,
        })

    if not sweep_rows:
        print(f"No simulatable replays (need virtual_entry + ticks). {PIPELINE_ENFORCEMENT_TAG}")
        return None

    sweep = pd.DataFrame(sweep_rows).sort_values("stop_margin_%", ascending=True)
    sweep_by_pnl = sweep.sort_values("sum_pnl_usd", ascending=False)
    print(f"Replays simulated: {len(rows)} (executed + shadow APPROVE paths) {PIPELINE_ENFORCEMENT_TAG}")
    print("\nStop / thesis fast-cut sweep (full -6% .. -180% margin):")
    print(sweep.to_string(index=False))
    max_sum = sweep_by_pnl["sum_pnl_usd"].max()
    plateau = sweep_by_pnl[sweep_by_pnl["sum_pnl_usd"] == max_sum]
    plateau_cut = float(plateau["stop_margin_%"].max())
    print(
        f"\n📈 PnL plateau: sum ${max_sum:.2f} first reached at stop {plateau_cut:.0f}% margin "
        f"({len(plateau)} levels tie at max) {PIPELINE_ENFORCEMENT_TAG}"
    )
    best = sweep_by_pnl.iloc[0]
    cur = sweep[sweep["stop_margin_%"] == baseline_cut]
    print(
        f"\n🎯 Best wide sweep: {best['stop_margin_%']:.0f}% margin — "
        f"sum ${best['sum_pnl_usd']:.2f} WR {best['win_rate_pct']:.1f}% "
        f"({int(best['loss_to_win_recoveries'])} recoveries from -6% losers) {PIPELINE_ENFORCEMENT_TAG}"
    )
    if not cur.empty:
        c = cur.iloc[0]
        print(
            f"   Current -6%: sum ${c['sum_pnl_usd']:.2f} WR {c['win_rate_pct']:.1f}% "
            f"on {int(c['replays'])} replays {PIPELINE_ENFORCEMENT_TAG}"
        )
        if best["stop_margin_%"] != baseline_cut and best["sum_pnl_usd"] > c["sum_pnl_usd"]:
            print(
                f"   ⚠️ Wider stop {best['stop_margin_%']:.0f}% beats current by "
                f"${best['sum_pnl_usd'] - c['sum_pnl_usd']:.2f} on replay data — "
                f"trades may recover after -6% dip {PIPELINE_ENFORCEMENT_TAG}"
            )
        elif best["stop_margin_%"] == baseline_cut:
            print(f"   ✅ Current -6% stop is optimal on available replay sample. {PIPELINE_ENFORCEMENT_TAG}")

    # Per-replay detail: trades that peaked positive but exited negative at -6%
    print("\nRecovery candidates (MFE>0 but -6% stop lost money):")
    shown = 0
    for tid, replay in replays.items():
        entry = _replay_entry_price(replay)
        ticks = replay.get("ticks") or []
        if not entry or not ticks:
            continue
        mfe = max((float(t.get("unreal_pct")) for t in ticks if t.get("unreal_pct") is not None), default=0.0)
        ft = replay.get("virtual_fill_t")
        b_usd, b_exit, _ = _simulate_ticks_fast_cut_ladder(
            ticks, entry, replay.get("direction"), int(replay.get("leverage") or 100),
            float(replay.get("margin_usdt") or 20), -6, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=ft,
        ) or (0, None, 0)
        if mfe > 0 and b_usd <= 0:
            w20, _, _ = _simulate_ticks_fast_cut_ladder(
                ticks, entry, replay.get("direction"), int(replay.get("leverage") or 100),
                float(replay.get("margin_usdt") or 20), -20, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=ft,
            ) or (0, None, 0)
            w30, _, _ = _simulate_ticks_fast_cut_ladder(
                ticks, entry, replay.get("direction"), int(replay.get("leverage") or 100),
                float(replay.get("margin_usdt") or 20), -30, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=ft,
            ) or (0, None, 0)
            print(
                f"  {tid[:8]} {replay.get('lane','?')[:12]:12} MFE={mfe:+.1f}% "
                f"@ -6%=${b_usd:+.2f} @ -20%=${w20:+.2f} @ -30%=${w30:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )
            shown += 1
            if shown >= 8:
                break
    if shown == 0:
        print(f"  None in this sample. {PIPELINE_ENFORCEMENT_TAG}")
    return float(best["stop_margin_%"])


def _ladder_col_label(trig, lock):
    return f"{int(trig)}→{int(lock)}"


def _build_ladder_with_first_rung(trig, lock):
    return [(trig, lock)] + [(t, l) for t, l in TRAIL_LADDER if t > trig]


def stop_ladder_2d_grid_sweep_all_replays(trades_df=None, top_n=10):
    """
    2D grid: thesis stop margin % × ladder first rung (trigger→lock) on all replays.
    Tick-accurate sim via _simulate_ticks_fast_cut_ladder on every signal_replay.jsonl path.
    """
    print("\n=== STOP × LADDER FIRST-RUNG 2D GRID SWEEP (all replays) ===")
    stop_levels = STOP_LADDER_2D_GRID_STOPS
    ladder_candidates = LADDER_FIRST_RUNG_CANDIDATES
    live_stop = THESIS_FAST_EXIT_DEFAULT
    live_trig, live_lock = TRAIL_LADDER[0]
    live_label = _ladder_col_label(live_trig, live_lock)
    n_cells = len(stop_levels) * len(ladder_candidates)
    print(
        "Thesis stop (margin %) × ladder first rung on tick replay. "
        f"Live defaults: stop {live_stop}% | first rung {live_label}%. "
        f"Grid: {len(stop_levels)} stops × {len(ladder_candidates)} ladders = {n_cells} cells. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    replays = _load_jsonl_replays()
    if not replays:
        print(f"No {SIGNAL_REPLAY_FILE} — run bot longer to collect APPROVE replays. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    per_cell = {
        (stop, trig, lock): {"sum": 0.0, "n": 0, "wins": 0, "ladder": 0, "thesis": 0, "replay_end": 0}
        for stop in stop_levels
        for trig, lock in ladder_candidates
    }
    replay_count = 0

    for tid, replay in replays.items():
        ticks = replay.get("ticks") or []
        entry = _replay_entry_price(replay)
        if not ticks or not entry or entry <= 0:
            continue
        margin = float(replay.get("margin_usdt") or 20.0)
        lev = int(replay.get("leverage") or 100)
        direction = replay.get("direction", "SHORT")
        fill_t = replay.get("virtual_fill_t")
        replay_count += 1

        for stop in stop_levels:
            for trig, lock in ladder_candidates:
                ladder = _build_ladder_with_first_rung(trig, lock)
                sim_usd, exit_reason, _peak = _simulate_ticks_fast_cut_ladder(
                    ticks, entry, direction, lev, margin,
                    stop, ladder, THESIS_EXIT_ABOVE_DEFAULT, fill_t=fill_t,
                )
                if sim_usd is None:
                    continue
                d = per_cell[(stop, trig, lock)]
                d["sum"] += sim_usd
                d["n"] += 1
                if sim_usd > 0:
                    d["wins"] += 1
                if exit_reason == "PROFIT_LOCK_LADDER":
                    d["ladder"] += 1
                elif exit_reason == "THESIS_FAST_CUT":
                    d["thesis"] += 1
                elif exit_reason == "REPLAY_END":
                    d["replay_end"] += 1

    sweep_rows = []
    for (stop, trig, lock), d in per_cell.items():
        if not d["n"]:
            continue
        sweep_rows.append({
            "stop_margin_%": stop,
            "ladder_trigger_%": trig,
            "ladder_lock_%": lock,
            "ladder": _ladder_col_label(trig, lock),
            "replays": d["n"],
            "sum_pnl_usd": round(d["sum"], 2),
            "avg_pnl_usd": round(d["sum"] / d["n"], 2),
            "win_rate_pct": round(100 * d["wins"] / d["n"], 1),
            "ladder_exits": d["ladder"],
            "thesis_exits": d["thesis"],
            "replay_end": d["replay_end"],
            "is_live": stop == live_stop and trig == live_trig and lock == live_lock,
        })

    if not sweep_rows:
        print(f"No simulatable replays (need virtual_entry + ticks). {PIPELINE_ENFORCEMENT_TAG}")
        return None

    sweep = pd.DataFrame(sweep_rows)
    sweep_by_pnl = sweep.sort_values("sum_pnl_usd", ascending=False)
    ladder_cols = [_ladder_col_label(t, l) for t, l in ladder_candidates]

    print(f"Replays simulated: {replay_count} (executed + shadow APPROVE paths) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"\nTop {top_n} combos by sum PnL:")
    top = sweep_by_pnl.head(top_n).copy()
    top["mark"] = top["is_live"].map({True: "★ LIVE", False: ""})
    print(top[[
        "stop_margin_%", "ladder", "sum_pnl_usd", "avg_pnl_usd", "win_rate_pct",
        "ladder_exits", "thesis_exits", "replay_end", "mark",
    ]].to_string(index=False))

    print(f"\nHeatmap: sum PnL ($) — rows=stop margin %, cols=ladder first rung {PIPELINE_ENFORCEMENT_TAG}")
    print(f"★ LIVE cell: stop {live_stop}% × {live_label}")
    pnl_pivot = sweep.pivot_table(index="stop_margin_%", columns="ladder", values="sum_pnl_usd", aggfunc="first")
    pnl_pivot = pnl_pivot.reindex(columns=[c for c in ladder_cols if c in pnl_pivot.columns])
    pnl_pivot = pnl_pivot.sort_index(ascending=True)
    print(pnl_pivot.to_string(float_format=lambda x: f"{x:.2f}"))

    print(f"\nHeatmap: win rate (%) — same grid {PIPELINE_ENFORCEMENT_TAG}")
    wr_pivot = sweep.pivot_table(index="stop_margin_%", columns="ladder", values="win_rate_pct", aggfunc="first")
    wr_pivot = wr_pivot.reindex(columns=[c for c in ladder_cols if c in wr_pivot.columns])
    wr_pivot = wr_pivot.sort_index(ascending=True)
    print(wr_pivot.to_string(float_format=lambda x: f"{x:.1f}"))

    max_sum = sweep_by_pnl["sum_pnl_usd"].max()
    plateau = sweep_by_pnl[sweep_by_pnl["sum_pnl_usd"] == max_sum]
    print(
        f"\n📈 PnL plateau: sum ${max_sum:.2f} — {len(plateau)} combo(s) tie at max "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    for _, r in plateau.head(8).iterrows():
        mark = " ★ LIVE" if r["is_live"] else ""
        print(
            f"   stop {r['stop_margin_%']:.0f}% | {r['ladder']}{mark} — "
            f"sum ${r['sum_pnl_usd']:.2f} WR {r['win_rate_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
        )

    best = sweep_by_pnl.iloc[0]
    live = sweep[sweep["is_live"]]
    print(
        f"\n🎯 Best grid combo: stop {best['stop_margin_%']:.0f}% | {best['ladder']} — "
        f"sum ${best['sum_pnl_usd']:.2f} WR {best['win_rate_pct']:.1f}% "
        f"(ladder={int(best['ladder_exits'])}, thesis={int(best['thesis_exits'])}, "
        f"replay_end={int(best['replay_end'])}) {PIPELINE_ENFORCEMENT_TAG}"
    )
    if not live.empty:
        c = live.iloc[0]
        print(
            f"   ★ LIVE ({live_stop}%, {live_label}): sum ${c['sum_pnl_usd']:.2f} "
            f"WR {c['win_rate_pct']:.1f}% "
            f"(ladder={int(c['ladder_exits'])}, thesis={int(c['thesis_exits'])}, "
            f"replay_end={int(c['replay_end'])}) {PIPELINE_ENFORCEMENT_TAG}"
        )
        if not best["is_live"] and best["sum_pnl_usd"] > c["sum_pnl_usd"]:
            print(
                f"   ⚠️ Best grid beats LIVE by ${best['sum_pnl_usd'] - c['sum_pnl_usd']:.2f} "
                f"on replay sample {PIPELINE_ENFORCEMENT_TAG}"
            )
        elif bool(best["is_live"]):
            print(f"   ✅ LIVE combo is optimal on available replay sample. {PIPELINE_ENFORCEMENT_TAG}")

    return {
        "best_stop": float(best["stop_margin_%"]),
        "best_trigger": int(best["ladder_trigger_%"]),
        "best_lock": int(best["ladder_lock_%"]),
        "best_sum_pnl": float(best["sum_pnl_usd"]),
        "live_sum_pnl": float(live.iloc[0]["sum_pnl_usd"]) if not live.empty else None,
    }


def stop_ladder_mfe_3d_sweep_all_replays(trades_df=None, top_n=12):
    """
    3D grid: thesis stop × ladder first rung × MFE protect floor.
    mfe_protect=0 means always fast-cut at stop; 4 means skip cut if peak ever >= 4% margin.
    """
    print("\n=== STOP × LADDER × MFE-PROTECT 3D SWEEP (all replays) ===")
    stop_levels = STOP_LADDER_MFE_3D_STOPS
    ladder_candidates = STOP_LADDER_MFE_3D_LADDERS
    mfe_levels = MFE_PROTECT_SWEEP
    live_stop = THESIS_FAST_EXIT_DEFAULT
    live_trig, live_lock = TRAIL_LADDER[0]
    live_mfe = THESIS_MFE_PROTECT_DEFAULT
    live_label = _ladder_col_label(live_trig, live_lock)
    n_cells = len(stop_levels) * len(ladder_candidates) * len(mfe_levels)
    print(
        f"Conditional fast cut: skip thesis stop if peak MFE >= floor. "
        f"Live: stop {live_stop}% | ladder {live_label} | MFE protect {live_mfe}%. "
        f"Grid: {len(stop_levels)}×{len(ladder_candidates)}×{len(mfe_levels)} = {n_cells} cells. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    replays = _load_jsonl_replays()
    if not replays:
        print(f"No {SIGNAL_REPLAY_FILE}. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    rows = []
    for stop in stop_levels:
        for trig, lock in ladder_candidates:
            ladder = _build_ladder_with_first_rung(trig, lock)
            for mfe_floor in mfe_levels:
                total = wins = n = thesis_ex = lad_ex = 0
                for tid, replay in replays.items():
                    ticks = replay.get("ticks") or []
                    entry = _replay_entry_price(replay)
                    if not ticks or not entry or entry <= 0:
                        continue
                    margin = float(replay.get("margin_usdt") or 20.0)
                    lev = int(replay.get("leverage") or 100)
                    direction = replay.get("direction", "SHORT")
                    fill_t = replay.get("virtual_fill_t")
                    mfe_arg = None if mfe_floor <= 0 else mfe_floor
                    sim_usd, exit_reason, _peak = _simulate_ticks_fast_cut_ladder(
                        ticks, entry, direction, lev, margin, stop, ladder,
                        THESIS_EXIT_ABOVE_DEFAULT, fill_t=fill_t, mfe_protect_pct=mfe_arg,
                    )
                    if sim_usd is None:
                        continue
                    n += 1
                    total += sim_usd
                    if sim_usd > 0:
                        wins += 1
                    if exit_reason == "THESIS_FAST_CUT":
                        thesis_ex += 1
                    elif exit_reason == "PROFIT_LOCK_LADDER":
                        lad_ex += 1
                if not n:
                    continue
                rows.append({
                    "stop_%": stop,
                    "ladder": _ladder_col_label(trig, lock),
                    "mfe_protect_%": mfe_floor,
                    "replays": n,
                    "sum_pnl_usd": round(total, 2),
                    "avg_pnl_usd": round(total / n, 2),
                    "win_rate_pct": round(100 * wins / n, 1),
                    "thesis_exits": thesis_ex,
                    "ladder_exits": lad_ex,
                    "is_live": (
                        stop == live_stop and trig == live_trig and lock == live_lock
                        and abs(mfe_floor - live_mfe) < 0.01
                    ),
                })

    if not rows:
        print(f"No simulatable replays. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    sweep = pd.DataFrame(rows)
    by_pnl = sweep.sort_values("sum_pnl_usd", ascending=False)
    print(f"Replays simulated: {len(replays)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"\nTop {top_n} combos by sum PnL:")
    top = by_pnl.head(top_n).copy()
    top["mark"] = top["is_live"].map({True: "★ LIVE", False: ""})
    print(top[[
        "stop_%", "ladder", "mfe_protect_%", "sum_pnl_usd", "win_rate_pct",
        "thesis_exits", "ladder_exits", "mark",
    ]].to_string(index=False))

    live_row = sweep[sweep["is_live"]]
    best = by_pnl.iloc[0]
    if not live_row.empty:
        lr = live_row.iloc[0]
        print(
            f"\n★ LIVE ({live_stop}%, {live_label}, MFE>={live_mfe}% protect): "
            f"sum ${lr['sum_pnl_usd']:.2f} WR {lr['win_rate_pct']:.1f}% "
            f"thesis={int(lr['thesis_exits'])} ladder={int(lr['ladder_exits'])} {PIPELINE_ENFORCEMENT_TAG}"
        )
    print(
        f"🎯 Best 3D: stop {best['stop_%']:.0f}% | {best['ladder']} | MFE protect {best['mfe_protect_%']:.0f}% — "
        f"sum ${best['sum_pnl_usd']:.2f} WR {best['win_rate_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
    )
    if not live_row.empty and best["sum_pnl_usd"] > live_row.iloc[0]["sum_pnl_usd"]:
        print(
            f"   ⚠️ Best beats LIVE by ${best['sum_pnl_usd'] - live_row.iloc[0]['sum_pnl_usd']:.2f} "
            f"on replay sample {PIPELINE_ENFORCEMENT_TAG}"
        )

    print(f"\nSlice: MFE protect @ live ladder {live_label}, varying stop (mfe={live_mfe}%):")
    slice_df = sweep[(sweep["ladder"] == live_label) & (sweep["mfe_protect_%"] == live_mfe)]
    slice_df = slice_df.sort_values("stop_%")
    if not slice_df.empty:
        print(slice_df[["stop_%", "sum_pnl_usd", "win_rate_pct", "thesis_exits"]].to_string(index=False))

    print(f"\nSlice: stop @ live {live_stop}%, ladder {live_label}, varying MFE protect:")
    mfe_slice = sweep[(sweep["stop_%"] == live_stop) & (sweep["ladder"] == live_label)]
    mfe_slice = mfe_slice.sort_values("mfe_protect_%")
    if not mfe_slice.empty:
        print(mfe_slice[["mfe_protect_%", "sum_pnl_usd", "win_rate_pct", "thesis_exits"]].to_string(index=False))

    return float(best["sum_pnl_usd"])


def _load_signal_snapshots():
    """APPROVE-time snapshots keyed by trade_id."""
    return _load_jsonl_by_trade_id(SIGNAL_SNAPSHOT_FILE)


def _blocked_reason_by_trade_id(blocked_df=None):
    """Post-APPROVE block reason from blocked_signals CSV."""
    reasons = {}
    if blocked_df is not None and not blocked_df.empty and "trade_id" in blocked_df.columns:
        for tid, grp in blocked_df.groupby("trade_id"):
            r = grp["reason"].iloc[0] if "reason" in grp.columns else None
            if r and str(r).strip():
                reasons[str(tid)] = str(r).strip()
    return reasons


def _parse_momentum_metric_from_block(block_reason):
    if not block_reason:
        return None
    m = re.search(r"MOMENTUM_CHOP_([\d.]+)", str(block_reason))
    return float(m.group(1)) if m else None


def _tf_is_bull(label) -> bool:
    return str(label or "").upper() in BULL_TF_LABELS


def _cohort_mtf_from_row(row) -> dict:
    gates = row.get("entry_gates") or {}
    mtf = gates.get("multi_tf") or {}
    thesis = row.get("_thesis") or {}
    return {
        "mtf_15m": mtf.get("mtf_15m") or thesis.get("mtf_15m") or row.get("mtf_15m") or "",
        "mtf_1h": mtf.get("mtf_1h") or thesis.get("mtf_1h") or row.get("mtf_1h") or "",
        "mtf_4h": mtf.get("mtf_4h") or thesis.get("mtf_4h") or row.get("mtf_4h") or "",
        "agreement": mtf.get("agreement") or thesis.get("mtf_agreement") or row.get("mtf") or "",
    }


def _cohort_passes_mtf_long(row, rule: str = "LIVE_BULL_ALIGNED") -> bool:
    direction = str(row.get("direction") or "LONG").upper()
    if direction == "SHORT":
        return row.get("mtf") == "BEAR_ALIGNED"
    m = _cohort_mtf_from_row(row)
    t15, t1h, t4h, agree = m["mtf_15m"], m["mtf_1h"], m["mtf_4h"], m["agreement"]
    if rule == "NONE":
        return True
    if rule == "ALLOW_MIXED":
        return agree in ("BULL_ALIGNED", "MIXED")
    if rule == "LIVE_BULL_ALIGNED":
        return agree == "BULL_ALIGNED"
    if rule == "15M_BULL":
        return _tf_is_bull(t15)
    if rule == "1H_BULL":
        return _tf_is_bull(t1h)
    if rule == "4H_BULL":
        return _tf_is_bull(t4h)
    if rule == "15M+1H_BULL":
        return _tf_is_bull(t15) and _tf_is_bull(t1h)
    if rule == "1H+4H_BULL":
        return _tf_is_bull(t1h) and _tf_is_bull(t4h)
    if rule == "15M+1H+4H_BULL":
        return _tf_is_bull(t15) and _tf_is_bull(t1h) and _tf_is_bull(t4h)
    if rule == "15M_OR_1H_BULL":
        return _tf_is_bull(t15) or _tf_is_bull(t1h)
    return True


def _cohort_passes_live_stack(row, mtf_rule: str = "LIVE_BULL_ALIGNED", max_chop: float = None, min_adx: float = None) -> bool:
    chop_lim = LIVE_MOMENTUM_CHOP_MAX if max_chop is None else max_chop
    adx_lim = LIVE_ADX_BLOCK_MIN if min_adx is None else min_adx
    return (
        _cohort_passes_adx(row, adx_lim)
        and _cohort_passes_chop(row, chop_lim)
        and _cohort_passes_mtf_long(row, mtf_rule)
        and _cohort_passes_edge(row, LIVE_EDGE_THRESHOLD_DEFAULT)
        and _cohort_passes_spread(row, LIVE_MIN_FACTOR_SPREAD)
    )


def _load_executed_trade_ids() -> set:
    if not os.path.exists(TRADES_FILE):
        return set()
    try:
        t = pd.read_csv(TRADES_FILE, usecols=["trade_id"], encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        try:
            t = pd.read_csv(TRADES_FILE, usecols=["trade_id"], encoding="latin1")
        except Exception:
            return set()
    except Exception:
        return set()
    return set(t["trade_id"].dropna().astype(str))


def _load_mtf_from_pipeline_events() -> dict:
    path = PIPELINE_EVENTS_FILE
    if not os.path.exists(path):
        return {}
    try:
        try:
            pe = pd.read_csv(path, usecols=["trade_id", "stage", "outcome", "mtf_15m", "mtf_1h", "mtf_4h"], encoding="utf-8")
        except UnicodeDecodeError:
            pe = pd.read_csv(path, usecols=["trade_id", "stage", "outcome", "mtf_15m", "mtf_1h", "mtf_4h"], encoding="latin1")
        ai = pe[(pe["stage"] == "AI") & (pe["outcome"] == "APPROVE")].drop_duplicates("trade_id")
        return {
            str(r.trade_id): {"mtf_15m": str(r.mtf_15m or ""), "mtf_1h": str(r.mtf_1h or ""), "mtf_4h": str(r.mtf_4h or "")}
            for r in ai.itertuples(index=False)
        }
    except Exception:
        return {}


def _build_approve_cohort(blocked_df=None):
    """
    One row per AI APPROVE from signal_snapshot.jsonl + block reason + replay availability.
    Prefers entry_gates.mom_metric (v3 snapshot); falls back to block reason parse.
    """
    snapshots = _load_signal_snapshots()
    replays = _load_jsonl_replays()
    block_map = _blocked_reason_by_trade_id(blocked_df)
    pipe_mtf = _load_mtf_from_pipeline_events()
    executed_ids = _load_executed_trade_ids()
    rows = []
    for tid, snap in snapshots.items():
        thesis = snap.get("entry_thesis") or {}
        gates = snap.get("entry_gates") or {}
        ai = snap.get("ai") or {}
        direction = str(snap.get("direction") or ai.get("direction") or "SHORT").upper()
        block = block_map.get(tid) or snap.get("block_reason")
        if block is None and snap.get("executed"):
            block = "EXECUTED"
        adx = gates.get("adx") if gates.get("adx") is not None else thesis.get("adx")
        try:
            adx = float(adx) if adx is not None else None
        except (TypeError, ValueError):
            adx = None
        bull = int(ai.get("bull_score") or 0)
        bear = int(ai.get("bear_score") or 0)
        spread = gates.get("directional_spread")
        if spread is None:
            spread = bear - bull if direction == "SHORT" else bull - bear
        mom = gates.get("mom_metric")
        ef = snap.get("entry_features") or {}
        if mom is None and ef.get("mom_metric") is not None:
            mom = float(ef["mom_metric"])
        if mom is None:
            mom = _parse_momentum_metric_from_block(block)
        pm = pipe_mtf.get(tid, {})
        rows.append({
            "trade_id": tid,
            "direction": direction,
            "adx": adx,
            "mtf": (gates.get("multi_tf") or {}).get("agreement") or thesis.get("mtf_agreement"),
            "mtf_15m": pm.get("mtf_15m") or thesis.get("mtf_15m") or (gates.get("multi_tf") or {}).get("mtf_15m"),
            "mtf_1h": pm.get("mtf_1h") or thesis.get("mtf_1h") or (gates.get("multi_tf") or {}).get("mtf_1h"),
            "mtf_4h": pm.get("mtf_4h") or thesis.get("mtf_4h") or (gates.get("multi_tf") or {}).get("mtf_4h"),
            "structure_score": thesis.get("structure_score"),
            "edge_score": float(gates.get("edge_score") or snap.get("edge_score") or 0),
            "bull_score": bull,
            "bear_score": bear,
            "directional_spread": spread,
            "block_reason": block,
            "mom_metric": mom,
            "executed_live": bool(snap.get("executed")) or tid in executed_ids,
            "entry_gates": gates,
            "entry_regime": snap.get("entry_regime") or {},
            "gate_margins": (gates.get("margins") or {}),
            "gate_mode": gates.get("gate_mode"),
            "would_pass_strict": (gates.get("would_pass_strict") or {}),
            "post_block_research": snap.get("post_block_research") or {},
            "fill_dynamics": (snap.get("outcome") or {}).get("fill_dynamics"),
            "approve_index": snap.get("approve_index"),
            "_thesis": thesis,
            "has_replay": tid in replays,
            "replay": replays.get(tid),
        })
    return rows


def _sim_replay_pnl_usd(replay, stop_pct=None, ladder=None, mfe_protect_pct=None):
    """Tick sim PnL for one replay; defaults = live thesis stop + TRAIL_LADDER."""
    if not replay:
        return None
    ticks = replay.get("ticks") or []
    entry = _replay_entry_price(replay)
    if not ticks or not entry or entry <= 0:
        return None
    cut = THESIS_FAST_EXIT_DEFAULT if stop_pct is None else stop_pct
    lad = TRAIL_LADDER if ladder is None else ladder
    mfe = THESIS_MFE_PROTECT_DEFAULT if mfe_protect_pct is None else mfe_protect_pct
    margin = float(replay.get("margin_usdt") or 20.0)
    lev = int(replay.get("leverage") or 100)
    direction = replay.get("direction", "SHORT")
    fill_t = replay.get("virtual_fill_t")
    sim_usd, exit_reason, _peak = _simulate_ticks_fast_cut_ladder(
        ticks, entry, direction, lev, margin, cut, lad, THESIS_EXIT_ABOVE_DEFAULT,
        fill_t=fill_t, mfe_protect_pct=mfe,
    )
    if sim_usd is None:
        return None
    return float(sim_usd), exit_reason


def _cohort_passes_adx(row, min_adx):
    adx = row.get("adx")
    if adx is None:
        return False
    if min_adx <= 0:
        return True
    return adx >= min_adx


def _cohort_passes_chop(row, max_chop):
    mom = row.get("mom_metric")
    if mom is not None:
        return mom <= max_chop + 1e-9
    block = str(row.get("block_reason") or "")
    if "MOMENTUM_CHOP" in block:
        return False
    if "MOMENTUM_COUNTER" in block or "MOMENTUM_FLAT" in block or "MOMENTUM_BOUNCE" in block:
        return False
    return True


def _cohort_passes_mtf_short(row):
    if row.get("direction") != "SHORT":
        return True
    return row.get("mtf") == "BEAR_ALIGNED"


def _cohort_passes_edge(row, min_edge):
    return float(row.get("edge_score") or 0) >= min_edge - 1e-9


def _cohort_passes_spread(row, min_spread):
    return int(row.get("directional_spread") or 0) >= min_spread


def _aggregate_cohort_replay_pnl(cohort_rows, stop_pct=None):
    total = 0.0
    n = wins = 0
    block_mix = {}
    for row in cohort_rows:
        if not row.get("has_replay"):
            continue
        res = _sim_replay_pnl_usd(row.get("replay"), stop_pct=stop_pct)
        if res is None:
            continue
        pnl, _ex = res
        total += pnl
        n += 1
        if pnl > 0:
            wins += 1
    wr = round(100 * wins / n, 1) if n else 0.0
    return {"n": n, "sum_pnl_usd": round(total, 2), "avg_pnl_usd": round(total / n, 2) if n else 0.0, "win_rate_pct": wr}


def entry_gate_replay_sweeps(blocked_df=None):
    """
    Sweep entry gate parameters on frozen APPROVE snapshots + tick replay exit PnL.
    No bot changes — uses signal_snapshot.jsonl entry_thesis.adx, edge, MTF, block reasons.
    """
    print("\n=== ENTRY GATE REPLAY SWEEPS (APPROVE snapshots + tick sim) ===")
    if RESEARCH_FREE_RUN_LIVE:
        print(
            f"★ LIVE v78 FREE-RUN: post-AI MTF + chop gates DISABLED — AI APPROVE executes. "
            f"Sweeps below use strict reference thresholds (chop<={LIVE_MOMENTUM_CHOP_MAX}, BULL_ALIGNED) "
            f"from snapshot margins to find sweet spot. {PIPELINE_ENFORCEMENT_TAG}"
        )
    print(
        f"Uses {SIGNAL_SNAPSHOT_FILE} at APPROVE time + {SIGNAL_REPLAY_FILE} for PnL. "
        f"Reference gates: ADX>={LIVE_ADX_BLOCK_MIN}, chop<={LIVE_MOMENTUM_CHOP_MAX}, "
        f"edge>={LIVE_EDGE_THRESHOLD_DEFAULT}, LONG MTF=BULL_ALIGNED, SHORT MTF=BEAR_ALIGNED. "
        f"Exit sim: thesis {THESIS_FAST_EXIT_DEFAULT}% + ladder {TRAIL_LADDER[0]} "
        f"+ MFE protect {THESIS_MFE_PROTECT_DEFAULT}%. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    cohort = _build_approve_cohort(blocked_df)
    if not cohort:
        print(f"No {SIGNAL_SNAPSHOT_FILE} APPROVE rows — run bot longer. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    with_replay = [r for r in cohort if r["has_replay"]]
    print(f"APPROVE snapshots: {len(cohort)} | with tick replay: {len(with_replay)} {PIPELINE_ENFORCEMENT_TAG}")

    # --- ADX min sweep (only ADX gate; chop/MTF/edge not re-applied) ---
    print("\n--- ADX minimum sweep (adx >= X, all other gates ignored) ---")
    print(f"★ LIVE min ADX = {LIVE_ADX_BLOCK_MIN}")
    adx_rows = []
    for min_adx in ENTRY_ADX_MIN_SWEEP:
        passed = [r for r in cohort if _cohort_passes_adx(r, min_adx)]
        agg = _aggregate_cohort_replay_pnl(passed)
        adx_rows.append({
            "min_adx": min_adx,
            "would_pass": len(passed),
            "with_replay": agg["n"],
            "sum_pnl_usd": agg["sum_pnl_usd"],
            "avg_pnl_usd": agg["avg_pnl_usd"],
            "win_rate_pct": agg["win_rate_pct"],
            "is_live": min_adx == LIVE_ADX_BLOCK_MIN,
        })
    adx_df = pd.DataFrame(adx_rows)
    print(adx_df.to_string(index=False))
    live_adx = adx_df[adx_df["is_live"]]
    best_adx = adx_df.sort_values("sum_pnl_usd", ascending=False).iloc[0]
    if not live_adx.empty:
        la = live_adx.iloc[0]
        print(
            f"\n★ LIVE ADX>={LIVE_ADX_BLOCK_MIN}: {int(la['would_pass'])} pass, "
            f"sum ${la['sum_pnl_usd']:.2f} WR {la['win_rate_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
        )
    print(
        f"🎯 Best ADX-only sweep: min {best_adx['min_adx']:.0f} — "
        f"{int(best_adx['would_pass'])} pass, sum ${best_adx['sum_pnl_usd']:.2f} WR {best_adx['win_rate_pct']:.1f}% "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )

    # --- Momentum chop max sweep ---
    print("\n--- Momentum chop max sweep (mom <= X; mom from CHOP block reason, else passed live gate) ---")
    print(f"★ LIVE chop max = {LIVE_MOMENTUM_CHOP_MAX}")
    chop_rows = []
    for max_chop in ENTRY_CHOP_MAX_SWEEP:
        passed = [r for r in cohort if _cohort_passes_chop(r, max_chop)]
        agg = _aggregate_cohort_replay_pnl(passed)
        chop_rows.append({
            "max_chop": max_chop,
            "would_pass": len(passed),
            "with_replay": agg["n"],
            "sum_pnl_usd": agg["sum_pnl_usd"],
            "avg_pnl_usd": agg["avg_pnl_usd"],
            "win_rate_pct": agg["win_rate_pct"],
            "is_live": abs(max_chop - LIVE_MOMENTUM_CHOP_MAX) < 0.01,
        })
    chop_df = pd.DataFrame(chop_rows)
    print(chop_df.to_string(index=False))
    best_chop = chop_df.sort_values("sum_pnl_usd", ascending=False).iloc[0]

    # --- Edge threshold sweep ---
    print("\n--- Edge score minimum sweep (edge >= X) ---")
    edge_rows = []
    for min_edge in ENTRY_EDGE_MIN_SWEEP:
        passed = [r for r in cohort if _cohort_passes_edge(r, min_edge)]
        agg = _aggregate_cohort_replay_pnl(passed)
        edge_rows.append({
            "min_edge": min_edge,
            "would_pass": len(passed),
            "sum_pnl_usd": agg["sum_pnl_usd"],
            "win_rate_pct": agg["win_rate_pct"],
            "is_live": min_edge == LIVE_EDGE_THRESHOLD_DEFAULT,
        })
    print(pd.DataFrame(edge_rows).to_string(index=False))

    # --- Directional spread (conviction) sweep ---
    print("\n--- AI directional spread sweep (bear-bull for SHORT, min spread >= X) ---")
    spread_rows = []
    for min_sp in ENTRY_SPREAD_MIN_SWEEP:
        passed = [r for r in cohort if _cohort_passes_spread(r, min_sp)]
        agg = _aggregate_cohort_replay_pnl(passed)
        spread_rows.append({
            "min_spread": min_sp,
            "would_pass": len(passed),
            "sum_pnl_usd": agg["sum_pnl_usd"],
            "win_rate_pct": agg["win_rate_pct"],
        })
    print(pd.DataFrame(spread_rows).to_string(index=False))

    # --- Combined live stack vs relaxed ---
    print("\n--- Combined gate stack (replay PnL @ live exit) ---")
    def _pass_live_stack(r):
        return _cohort_passes_live_stack(r, "LIVE_BULL_ALIGNED")

    stacks = [
        ("ALL_APPROVE (no entry gates)", lambda r: True),
        ("★ LIVE stack (ADX+chop+BULL_ALIGNED+edge+spread)", _pass_live_stack),
        ("ALLOW_MIXED + chop<=0.5 + ADX>=15", lambda r: _cohort_passes_live_stack(r, "ALLOW_MIXED", 0.5, 15)),
        ("ALLOW_MIXED + chop<=0.7 + ADX>=15", lambda r: _cohort_passes_live_stack(r, "ALLOW_MIXED", 0.7, 15)),
        ("ALLOW_MIXED + chop<=1.0 + ADX>=15", lambda r: _cohort_passes_live_stack(r, "ALLOW_MIXED", 1.0, 15)),
        ("ALLOW_MIXED + chop<=1.0 + ADX>=25", lambda r: _cohort_passes_live_stack(r, "ALLOW_MIXED", 1.0, 25)),
        ("ADX>=15 only", lambda r: _cohort_passes_adx(r, 15)),
        ("chop<=0.5 only", lambda r: _cohort_passes_chop(r, 0.5)),
        ("chop<=0.7 only", lambda r: _cohort_passes_chop(r, 0.7)),
        ("15M+1H_BULL + chop<=0.7 + ADX>=15", lambda r: _cohort_passes_adx(r, 15) and _cohort_passes_chop(r, 0.7) and _cohort_passes_mtf_long(r, "15M+1H_BULL")),
    ]
    stack_rows = []
    for label, fn in stacks:
        passed = [r for r in cohort if fn(r)]
        agg = _aggregate_cohort_replay_pnl(passed)
        stack_rows.append({
            "stack": label,
            "pass": len(passed),
            "sum_pnl_usd": agg["sum_pnl_usd"],
            "win_rate_pct": agg["win_rate_pct"],
        })
    stack_df = pd.DataFrame(stack_rows)
    print(stack_df.to_string(index=False))

    # --- ADX x chop 2D (subset) ---
    print("\n--- ADX min × chop max heatmap (sum PnL $, ALLOW_MIXED + edge + spread) ---")
    adx_grid = [0, 15, 18, 20, 22, 25]
    chop_grid = [0.5, 0.6, 0.7, 0.8, 1.0]
    heat = []
    for min_adx in adx_grid:
        row = {"min_adx": min_adx}
        for max_chop in chop_grid:
            passed = [
                r for r in cohort
                if _cohort_passes_adx(r, min_adx) and _cohort_passes_chop(r, max_chop)
                and _cohort_passes_mtf_long(r, "ALLOW_MIXED")
                and _cohort_passes_edge(r, LIVE_EDGE_THRESHOLD_DEFAULT)
                and _cohort_passes_spread(r, LIVE_MIN_FACTOR_SPREAD)
            ]
            agg = _aggregate_cohort_replay_pnl(passed)
            row[f"chop<={max_chop}"] = agg["sum_pnl_usd"]
        heat.append(row)
    heat_df = pd.DataFrame(heat).set_index("min_adx")
    print(heat_df.to_string())

    # --- Per-APPROVE detail (critical misses) ---
    print("\n--- Per-APPROVE gate detail (sorted by replay PnL @ live exit) ---")
    detail = []
    for row in cohort:
        res = _sim_replay_pnl_usd(row.get("replay")) if row.get("has_replay") else None
        pnl = res[0] if res else None
        m = _cohort_mtf_from_row(row)
        detail.append({
            "id": row["trade_id"][:8],
            "adx": row.get("adx"),
            "mom": row.get("mom_metric"),
            "mtf": m.get("agreement"),
            "15m": (m.get("mtf_15m") or "")[:8],
            "1h": (m.get("mtf_1h") or "")[:8],
            "edge": row.get("edge_score"),
            "spread": row.get("directional_spread"),
            "block": (str(row.get("block_reason") or "EXECUTED?"))[:28],
            "replay_pnl": round(pnl, 2) if pnl is not None else None,
            "live_pass": _pass_live_stack(row),
            "exec": row.get("executed_live"),
        })
    detail_df = pd.DataFrame(detail).sort_values("replay_pnl", ascending=False, na_position="last")
    print(detail_df.to_string(index=False))

    missed = detail_df[(detail_df["replay_pnl"] > 0) & (~detail_df["live_pass"])]
    if not missed.empty:
        print(f"\n⚠️ Profitable replays blocked by LIVE stack: {len(missed)} sum ${missed['replay_pnl'].sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
        print(missed.to_string(index=False))
    else:
        print(f"\nNo profitable replays blocked solely by live stack on this sample. {PIPELINE_ENFORCEMENT_TAG}")

    print(
        f"\n📌 Entry sweep uses frozen APPROVE decisions — does not re-run AI. "
        f"Sample size {len(cohort)} — treat as directional until 30+ APPROVEs. {PIPELINE_ENFORCEMENT_TAG}"
    )
    return {
        "best_adx": float(best_adx["min_adx"]),
        "best_chop": float(best_chop["max_chop"]),
        "live_stack_pnl": float(stack_df[stack_df["stack"].str.contains("LIVE stack")]["sum_pnl_usd"].iloc[0]) if len(stack_df) else None,
    }


def _find_pullback_fill(replay: dict, pullback_pct: float, ttl_sec: float = None):
    """Simulate limit/market fill from replay ticks (counterfactual entry pullback)."""
    ticks = sorted(replay.get("ticks") or [], key=lambda x: x.get("seq", 0))
    if not ticks:
        return None
    start_price = replay.get("start_price") or replay.get("signal_price")
    if not start_price or float(start_price) <= 0:
        start_price = ticks[0].get("price")
    if not start_price:
        return None
    start_price = float(start_price)
    direction = str(replay.get("direction", "LONG")).upper()
    ttl = ttl_sec if ttl_sec is not None else REPLAY_FILL_TTL_SEC
    if pullback_pct <= 0.0:
        t0 = ticks[0]
        p = t0.get("price")
        if p and float(p) > 0:
            return float(p), float(t0.get("t", 0))
        return None
    for tick in ticks:
        price = tick.get("price")
        t = float(tick.get("t", 0))
        if price is None or float(price) <= 0 or t > ttl:
            continue
        price = float(price)
        if direction == "LONG" and price <= start_price * (1 - pullback_pct):
            return price, t
        if direction == "SHORT" and price >= start_price * (1 + pullback_pct):
            return price, t
    return None


def _sim_replay_pnl_at_fill(replay: dict, entry: float, fill_t: float, stop_pct=None):
    if not replay or not entry or entry <= 0:
        return None
    ticks = replay.get("ticks") or []
    cut = THESIS_FAST_EXIT_DEFAULT if stop_pct is None else stop_pct
    margin = float(replay.get("margin_usdt") or 20.0)
    lev = int(replay.get("leverage") or 100)
    direction = replay.get("direction", "SHORT")
    sim_usd, exit_reason, _peak = _simulate_ticks_fast_cut_ladder(
        ticks, entry, direction, lev, margin, cut, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT,
        fill_t=fill_t, mfe_protect_pct=THESIS_MFE_PROTECT_DEFAULT,
    )
    if sim_usd is None:
        return None
    return float(sim_usd), exit_reason


def pullback_replay_fill_sweep(blocked_df=None):
    """
    Counterfactual entry pullback on APPROVE tick replays:
    market (0%), 0.1%, 0.2%, 0.4%, etc. — fill count + replay exit PnL.
    """
    print("\n=== PULLBACK REPLAY FILL SWEEP (counterfactual limit depth) ===")
    print(
        f"Uses {SIGNAL_REPLAY_FILE} ticks + start_price. TTL={REPLAY_FILL_TTL_SEC // 60}min. "
        f"0% = market fill at first tick. Exit sim: thesis {THESIS_FAST_EXIT_DEFAULT}% + ladder {TRAIL_LADDER[0]}. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    cohort = _build_approve_cohort(blocked_df)
    if not cohort:
        print(f"No APPROVE snapshots. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    rows = []
    per_pb_detail = []
    for pb in PULLBACK_REPLAY_SWEEP:
        filled_n = 0
        total_pnl = 0.0
        wins = 0
        live_gate_filled = 0
        live_gate_pnl = 0.0
        for row in cohort:
            replay = row.get("replay")
            if not replay or not replay.get("ticks"):
                continue
            fill = _find_pullback_fill(replay, pb)
            if not fill:
                continue
            entry, fill_t = fill
            res = _sim_replay_pnl_at_fill(replay, entry, fill_t)
            if res is None:
                continue
            pnl, _ex = res
            filled_n += 1
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            if _cohort_passes_live_stack(row, "LIVE_BULL_ALIGNED"):
                live_gate_filled += 1
                live_gate_pnl += pnl
        wr = round(100 * wins / filled_n, 1) if filled_n else 0.0
        rows.append({
            "pullback_%": round(pb * 100, 2),
            "fills": filled_n,
            "sum_pnl_usd": round(total_pnl, 2),
            "avg_pnl_usd": round(total_pnl / filled_n, 2) if filled_n else 0.0,
            "win_rate_pct": wr,
            "live_gate_fills": live_gate_filled,
            "live_gate_pnl": round(live_gate_pnl, 2),
            "is_live_pb": abs(pb - 0.002) < 1e-6,
        })
    if not rows:
        print(f"No replay ticks to simulate. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    best = df.sort_values("sum_pnl_usd", ascending=False).iloc[0]
    live_row = df[df["is_live_pb"]]
    print(
        f"\n🎯 Best pullback (all APPROVE replays): {best['pullback_%']:.2f}% — "
        f"fills={int(best['fills'])} sum ${best['sum_pnl_usd']:.2f} WR {best['win_rate_pct']:.1f}% "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    if not live_row.empty:
        lr = live_row.iloc[0]
        print(
            f"★ LIVE pullback {lr['pullback_%']:.2f}%: fills={int(lr['fills'])} "
            f"sum ${lr['sum_pnl_usd']:.2f} (live-gate subset: {int(lr['live_gate_fills'])} fills ${lr['live_gate_pnl']:.2f}) "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
    # Per-trade sensitivity (top movers)
    print("\n--- Per-APPROVE pullback sensitivity (replay PnL by pullback %) ---")
    for row in sorted(cohort, key=lambda r: (_sim_replay_pnl_usd(r.get("replay")) or (0,))[0] if r.get("has_replay") else -999, reverse=True)[:15]:
        if not row.get("has_replay"):
            continue
        replay = row["replay"]
        pb_pnls = {}
        for pb in PULLBACK_REPLAY_SWEEP:
            fill = _find_pullback_fill(replay, pb)
            if not fill:
                pb_pnls[f"{pb*100:.1f}%"] = "NO_FILL"
                continue
            res = _sim_replay_pnl_at_fill(replay, fill[0], fill[1])
            pb_pnls[f"{pb*100:.1f}%"] = round(res[0], 2) if res else None
        base_pnl = (_sim_replay_pnl_usd(replay) or (None,))[0]
        per_pb_detail.append({
            "id": row["trade_id"][:8],
            "live_pb_pnl": round(base_pnl, 2) if base_pnl is not None else None,
            **{k: v for k, v in pb_pnls.items()},
        })
    if per_pb_detail:
        print(pd.DataFrame(per_pb_detail).to_string(index=False))
    return {"best_pullback_pct": float(best["pullback_%"]) / 100.0}


def entry_gate_mtf_chop_sweet_spot_sweep(blocked_df=None):
    """
    MTF rule × chop × ADX grid on frozen APPROVE cohort + tick replay PnL.
    Finds minimal relaxation that unlocks profitable replays while blocking bad live trades.
    """
    print("\n=== MTF × CHOP × ADX SWEET-SPOT SWEEP (gate research) ===")
    print(
        f"Bot MTF TFs: 15m + 1h + 4h only. Live: chop<={LIVE_MOMENTUM_CHOP_MAX}, "
        f"ADX>={LIVE_ADX_BLOCK_MIN}, LONG MTF=BULL_ALIGNED. {PIPELINE_ENFORCEMENT_TAG}"
    )
    cohort = _build_approve_cohort(blocked_df)
    if not cohort:
        print(f"No APPROVE snapshots. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    executed = {r["trade_id"] for r in cohort if r.get("executed_live")}
    bad_ids = []
    for r in cohort:
        if not r.get("executed_live"):
            continue
        res = _sim_replay_pnl_usd(r.get("replay"))
        if res and res[0] < 0:
            bad_ids.append(r["trade_id"])
    print(f"APPROVE cohort: {len(cohort)} | executed live: {len(executed)} | live losers (replay): {len(bad_ids)}")

    # MTF rule sweep @ live chop/adx
    print("\n--- MTF rule sweep (chop<=live, ADX>=live, edge+spread) ---")
    mtf_rows = []
    for rule_id, rule_label in MTF_RULE_LABELS:
        passed = [r for r in cohort if _cohort_passes_live_stack(r, rule_id)]
        agg = _aggregate_cohort_replay_pnl(passed)
        bad_in = sum(1 for r in passed if r["trade_id"] in bad_ids)
        prof_blocked = 0
        prof_sum = 0.0
        for r in cohort:
            if r in passed:
                continue
            res = _sim_replay_pnl_usd(r.get("replay"))
            if res and res[0] > 0:
                prof_blocked += 1
                prof_sum += res[0]
        mtf_rows.append({
            "mtf_rule": rule_id,
            "label": rule_label[:42],
            "pass_n": len(passed),
            "replay_n": agg["n"],
            "sum_pnl": agg["sum_pnl_usd"],
            "wr": agg["win_rate_pct"],
            "bad_live_in_pass": bad_in,
            "prof_blocked": prof_blocked,
            "prof_blocked_$": round(prof_sum, 2),
            "is_live": rule_id == "LIVE_BULL_ALIGNED",
        })
    mtf_df = pd.DataFrame(mtf_rows).sort_values("sum_pnl", ascending=False)
    print(mtf_df.to_string(index=False))

    # Chop × ADX grid with ALLOW_MIXED (primary unlock lever)
    print("\n--- Chop × ADX grid (ALLOW_MIXED + edge + spread) ---")
    grid_rows = []
    for max_chop in ENTRY_CHOP_MAX_SWEEP:
        for min_adx in ENTRY_ADX_GRID_SWEEP:
            passed = [r for r in cohort if _cohort_passes_live_stack(r, "ALLOW_MIXED", max_chop, min_adx)]
            agg = _aggregate_cohort_replay_pnl(passed)
            bad_in = sum(1 for r in passed if r["trade_id"] in bad_ids)
            grid_rows.append({
                "chop<=": max_chop,
                "adx>=": min_adx,
                "pass": len(passed),
                "replay_n": agg["n"],
                "sum_pnl": agg["sum_pnl_usd"],
                "wr": agg["win_rate_pct"],
                "bad_in": bad_in,
            })
    grid_df = pd.DataFrame(grid_rows)
    top = grid_df.sort_values("sum_pnl", ascending=False).head(12)
    print(top.to_string(index=False))

    # Sweet spots: block all bad live losers, maximize PnL
    print("\n--- Sweet spots blocking ALL live replay-losers ---")
    if bad_ids:
        safe = grid_df[grid_df["bad_in"] == 0].sort_values("sum_pnl", ascending=False)
        if not safe.empty:
            print(safe.head(8).to_string(index=False))
        else:
            print(f"No grid cell blocks all {len(bad_ids)} live losers — loosen ADX or accept collateral. {PIPELINE_ENFORCEMENT_TAG}")
    else:
        print(f"No live replay-losers in cohort yet. {PIPELINE_ENFORCEMENT_TAG}")

    # Full 3D: MTF × chop × ADX (compact top 15)
    print("\n--- Top combos: MTF × chop × ADX (edge+spread fixed) ---")
    combo_rows = []
    for rule_id, rule_label in MTF_RULE_LABELS[:6]:
        for max_chop in [0.5, 0.6, 0.7, 0.8, 1.0]:
            for min_adx in [15, 20, 25]:
                passed = [r for r in cohort if _cohort_passes_live_stack(r, rule_id, max_chop, min_adx)]
                agg = _aggregate_cohort_replay_pnl(passed)
                bad_in = sum(1 for r in passed if r["trade_id"] in bad_ids)
                combo_rows.append({
                    "mtf": rule_id,
                    "chop": max_chop,
                    "adx": min_adx,
                    "pass": len(passed),
                    "sum_pnl": agg["sum_pnl_usd"],
                    "wr": agg["win_rate_pct"],
                    "bad_in": bad_in,
                })
    combo_df = pd.DataFrame(combo_rows).sort_values("sum_pnl", ascending=False).head(15)
    print(combo_df.to_string(index=False))

    best = combo_df.iloc[0] if not combo_df.empty else None
    live_passed = [r for r in cohort if _cohort_passes_live_stack(r, "LIVE_BULL_ALIGNED")]
    live_agg = _aggregate_cohort_replay_pnl(live_passed)
    print(
        f"\n★ LIVE stack: {len(live_passed)} pass, replay sum ${live_agg['sum_pnl_usd']:.2f} WR {live_agg['win_rate_pct']:.1f}% "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    if best is not None:
        print(
            f"🎯 Top combo: MTF={best['mtf']} chop<={best['chop']} adx>={best['adx']} — "
            f"pass={int(best['pass'])} sum ${best['sum_pnl']:.2f} bad_live_in={int(best['bad_in'])} "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
    return {"live_stack_pnl": live_agg["sum_pnl_usd"], "best_combo": best.to_dict() if best is not None else None}


def gate_margin_sweet_spot_analysis(blocked_df=None):
    """
    Distance-to-pass margins (adx/chop/edge/spread) — find minimal relaxation per gate.
    Negative margin = failed that gate at APPROVE.
    """
    print("\n=== GATE MARGIN SWEET-SPOT (distance-to-pass at APPROVE) ===")
    cohort = _build_approve_cohort(blocked_df)
    if not cohort:
        print(f"No APPROVE snapshots. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    rows = []
    for row in cohort:
        m = row.get("gate_margins") or (row.get("entry_gates") or {}).get("margins") or {}
        if not m:
            continue
        res = _sim_replay_pnl_usd(row.get("replay")) if row.get("has_replay") else None
        pnl = res[0] if res else None
        rows.append({
            "id": row["trade_id"][:8],
            "adx_m": m.get("adx_margin"),
            "chop_m": m.get("chop_margin"),
            "edge_m": m.get("edge_margin"),
            "spread_m": m.get("spread_margin"),
            "stack_m": m.get("live_stack_margin"),
            "mtf_pass": m.get("mtf_pass"),
            "block": (str(row.get("block_reason") or ""))[:24],
            "replay_pnl": round(pnl, 2) if pnl is not None else None,
            "live_pass": _cohort_passes_live_stack(row, "LIVE_BULL_ALIGNED"),
        })
    if not rows:
        print(f"No gate margins yet — restart bot on {EXPECTED_BOT_VERSION} for v4 snapshots. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    df = pd.DataFrame(rows)
    print(f"Rows with margins: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
    print("\n--- Near-miss chop blocks (chop_margin in [-0.15, 0]) ---")
    near_chop = df[(df["chop_m"] >= -0.15) & (df["chop_m"] < 0)].sort_values("replay_pnl", ascending=False, na_position="last")
    if not near_chop.empty:
        print(near_chop.to_string(index=False))
        print(f"Near-miss chop sum replay PnL: ${near_chop['replay_pnl'].sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    else:
        print(f"No near-miss chop rows in sample. {PIPELINE_ENFORCEMENT_TAG}")
    print("\n--- Near-miss ADX blocks (adx_margin in [-5, 0]) ---")
    near_adx = df[(df["adx_m"] >= -5) & (df["adx_m"] < 0)].sort_values("replay_pnl", ascending=False, na_position="last")
    if not near_adx.empty:
        print(near_adx.head(12).to_string(index=False))
    print("\n--- Chop margin bucket × replay PnL ---")
    df["chop_bucket"] = pd.cut(
        pd.to_numeric(df["chop_m"], errors="coerce"),
        bins=[-999, -0.5, -0.2, -0.05, 0, 0.5, 999],
        labels=["fail>0.5", "fail0.2-0.5", "fail0.05-0.2", "pass0-0.05", "pass>0.05", "wide_pass"],
    )
    chop_grp = df.groupby("chop_bucket", observed=True).agg(
        n=("id", "count"),
        sum_pnl=("replay_pnl", "sum"),
        avg_pnl=("replay_pnl", "mean"),
        live_pass=("live_pass", "sum"),
    ).round(2)
    print(chop_grp.to_string())
    print("\n--- Minimal chop relaxation sweep (by margin threshold) ---")
    for chop_floor in [-0.5, -0.3, -0.2, -0.1, -0.05, 0]:
        sub = [
            r for r in cohort
            if float((r.get("gate_margins") or {}).get("chop_margin", -999)) >= chop_floor
        ]
        agg = _aggregate_cohort_replay_pnl(sub)
        print(
            f"  chop_margin>={chop_floor}: pass={len(sub)} replay_sum=${agg['sum_pnl_usd']:.2f} "
            f"WR={agg['win_rate_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
        )
    return df


def post_block_quality_analysis(blocked_df=None):
    """
    Was blocking correct? Uses shadow_outcome v2 post_block_research + counterfactual PnL.
    """
    print("\n=== POST-BLOCK QUALITY (gate block vs counterfactual path) ===")
    shadow = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    cohort = _build_approve_cohort(blocked_df)
    if not shadow and not any(r.get("post_block_research") for r in cohort):
        print(
            f"No post_block_research yet — blocked APPROVEs after {EXPECTED_BOT_VERSION} restart "
            f"collect shadow_outcome_v2. {PIPELINE_ENFORCEMENT_TAG}"
        )
        return None
    rows = []
    for tid, sh in shadow.items():
        pbr = sh.get("post_block_research") or {}
        if not pbr and not sh.get("block_reason"):
            continue
        rows.append({
            "id": tid[:8],
            "block": (str(sh.get("block_reason") or ""))[:28],
            "cf_pnl": pbr.get("counterfactual_pnl_usd", sh.get("net_pnl_usd")),
            "block_cost": pbr.get("block_cost_usd"),
            "block_saved": pbr.get("block_saved_usd"),
            "correct": pbr.get("block_was_correct"),
            "post_ticks": pbr.get("post_block_tick_count"),
            "post_fav%": pbr.get("post_block_max_favorable_pct"),
            "post_adv%": pbr.get("post_block_max_adverse_pct"),
            "exit": sh.get("exit_reason"),
        })
    if not rows:
        print(f"No shadow rows with post-block metrics. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    df = pd.DataFrame(rows)
    print(f"Blocked shadow paths: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
    correct = df[df["correct"] == True]
    costly = df[pd.to_numeric(df["block_cost"], errors="coerce") > 0]
    print(
        f"Good blocks (cf_pnl<0): {len(correct)} | Costly blocks (blocked winners): {len(costly)} "
        f"| total block cost ${pd.to_numeric(costly['block_cost'], errors='coerce').sum():.2f} "
        f"| total saved ${pd.to_numeric(df['block_saved'], errors='coerce').sum():.2f} "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    if not costly.empty:
        print("\nTop costly blocks (profitable counterfactuals we blocked):")
        print(costly.sort_values("block_cost", ascending=False).head(12).to_string(index=False))
    if "block" in df.columns:
        print("\nBlock quality by reason:")
        grp = df.groupby("block").agg(
            n=("id", "count"),
            good=("correct", "sum"),
            cost_sum=("block_cost", "sum"),
            saved_sum=("block_saved", "sum"),
            avg_cf=("cf_pnl", "mean"),
        ).round(2)
        print(grp.sort_values("cost_sum", ascending=False).head(15).to_string())
    return df


def regime_segment_analysis(blocked_df=None):
    """Segment APPROVE replay PnL by session, funding, volatility, 4h ranging."""
    print("\n=== REGIME SEGMENT ANALYSIS (session / funding / vol / 4h) ===")
    cohort = _build_approve_cohort(blocked_df)
    if not cohort:
        print(f"No APPROVE snapshots. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    has_regime = sum(1 for r in cohort if r.get("entry_regime"))
    if not has_regime:
        print(f"No entry_regime yet — restart bot on {EXPECTED_BOT_VERSION}. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    rows = []
    for row in cohort:
        reg = row.get("entry_regime") or {}
        res = _sim_replay_pnl_usd(row.get("replay")) if row.get("has_replay") else None
        pnl = res[0] if res else None
        rows.append({
            "id": row["trade_id"][:8],
            "session": reg.get("session_utc"),
            "funding_bucket": reg.get("funding_bucket"),
            "vol_bucket": reg.get("volatility_bucket"),
            "adx_bucket": reg.get("adx_bucket"),
            "sr_state": reg.get("sr_state"),
            "mtf_4h_ranging": reg.get("mtf_4h_ranging"),
            "candle_elapsed": reg.get("candle_15m_elapsed_pct"),
            "replay_pnl": pnl,
            "live_pass": _cohort_passes_live_stack(row, "LIVE_BULL_ALIGNED"),
            "block": (str(row.get("block_reason") or ""))[:20],
        })
    df = pd.DataFrame(rows)
    print(f"Rows with regime tags: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")

    def _seg_report(col):
        sub = df[df[col].notna() & df["replay_pnl"].notna()]
        if sub.empty:
            return
        print(f"\n--- By {col} ---")
        g = sub.groupby(col).agg(
            n=("id", "count"),
            sum_pnl=("replay_pnl", "sum"),
            avg_pnl=("replay_pnl", "mean"),
            wr=("replay_pnl", lambda s: (s > 0).mean()),
            live_pass=("live_pass", "sum"),
        ).round(2)
        print(g.sort_values("sum_pnl", ascending=False).to_string())

    for col in ("session", "funding_bucket", "vol_bucket", "adx_bucket", "sr_state"):
        _seg_report(col)
    print("\n--- 4h RANGING vs not (replay PnL) ---")
    rng = df.groupby("mtf_4h_ranging").agg(
        n=("id", "count"), sum_pnl=("replay_pnl", "sum"), avg_pnl=("replay_pnl", "mean"),
    ).round(2)
    print(rng.to_string())
    blocked_prof = df[(~df["live_pass"]) & (pd.to_numeric(df["replay_pnl"], errors="coerce") > 0)]
    if not blocked_prof.empty:
        print(f"\nProfitable blocked by segment (top session):")
        print(blocked_prof.groupby("session")["replay_pnl"].agg(["count", "sum"]).round(2).to_string())
    return df


def margin_size_sweep_analysis(df, blocked_df=None):
    """
    Did conviction/regime/ADX margin scaling help or hurt?
    Compares historical margin buckets + counterfactual flat sizes ($5–$25).
    Margin % path is identical; USD PnL scales linearly with margin_usdt.
    """
    print("\n=== MARGIN SIZE SWEEP (flat $20 live vs scaled $5/$10/$15) ===")
    print(
        f"Live bot v79: flat ${FLAT_MARGIN_LIVE_USD} per trade. "
        f"PnL USD at size S = (margin_% / 100) × S. Replay sim uses same rule. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    size_grid = MARGIN_SIZE_SWEEP_USD

    # --- 1) Executed trades: actual margin bucket performance ---
    if df is not None and not df.empty:
        work = df.copy()
        work["net_pnl_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
        work["margin_usdt"] = pd.to_numeric(work.get("margin_usdt"), errors="coerce")
        work["margin_pct"] = pd.to_numeric(work.get("pnl", work.get("final_pnl_margin_pct")), errors="coerce")
        if work["margin_pct"].isna().all():
            work["margin_pct"] = np.where(
                work["margin_usdt"] > 0,
                work["net_pnl_usd"] / work["margin_usdt"] * 100,
                np.nan,
            )
        work["margin_bucket"] = work["margin_usdt"].round(1)
        print(f"\n--- Historical executed trades by actual margin_usdt (n={len(work)}) ---")
        hist = work.groupby("margin_bucket").agg(
            trades=("trade_id", "count"),
            sum_pnl=("net_pnl_usd", "sum"),
            avg_pnl=("net_pnl_usd", "mean"),
            wr=("net_pnl_usd", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean()),
            avg_margin_pct=("margin_pct", "mean"),
        ).round(2)
        print(hist.sort_values("sum_pnl", ascending=False).to_string())
        if not hist.empty:
            best_bucket = hist["sum_pnl"].idxmax()
            print(
                f"★ Best historical bucket: ${best_bucket} margin — "
                f"sum ${hist.loc[best_bucket, 'sum_pnl']:.2f} "
                f"({int(hist.loc[best_bucket, 'trades'])} trades) {PIPELINE_ENFORCEMENT_TAG}"
            )

        # Counterfactual: same price paths, different flat sizes
        print("\n--- Counterfactual flat margin on executed trades (same margin % path) ---")
        cf_rows = []
        valid = work[work["margin_pct"].notna()]
        for size in size_grid:
            scaled_pnl = valid["margin_pct"] / 100.0 * size
            cf_rows.append({
                "margin_usd": size,
                "trades": len(valid),
                "sum_pnl_usd": round(scaled_pnl.sum(), 2),
                "avg_pnl_usd": round(scaled_pnl.mean(), 2),
                "win_rate_pct": round(100 * (scaled_pnl > 0).mean(), 1) if len(valid) else 0.0,
                "is_live": size == FLAT_MARGIN_LIVE_USD,
            })
        cf_df = pd.DataFrame(cf_rows).sort_values("sum_pnl_usd", ascending=False)
        print(cf_df.to_string(index=False))
        best_cf = cf_df.iloc[0]
        live_cf = cf_df[cf_df["is_live"]]
        print(
            f"🎯 Best flat size on executed history: ${best_cf['margin_usd']:.0f} — "
            f"sum ${best_cf['sum_pnl_usd']:.2f} WR {best_cf['win_rate_pct']:.1f}% "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
        if not live_cf.empty:
            lc = live_cf.iloc[0]
            delta = best_cf["sum_pnl_usd"] - lc["sum_pnl_usd"]
            print(
                f"   vs live flat ${FLAT_MARGIN_LIVE_USD}: ${lc['sum_pnl_usd']:.2f} "
                f"(best is ${delta:+.2f}) {PIPELINE_ENFORCEMENT_TAG}"
            )

        # Spread bucket: did scaling help low-conviction trades?
        if "conviction_spread" in work.columns:
            work["spread"] = pd.to_numeric(work["conviction_spread"], errors="coerce")
            print("\n--- By conviction spread: $20 flat vs what scaled sizing would have earned ---")
            for spread_lo, spread_hi, label in [(1, 2, "spread_1"), (3, 4, "spread_3-4"), (5, 99, "spread_5+")]:
                sub = work[(work["spread"] >= spread_lo) & (work["spread"] <= spread_hi)]
                if sub.empty:
                    continue
                pct = sub["margin_pct"]
                actual = sub["net_pnl_usd"].sum()
                flat20 = (pct / 100.0 * 20).sum()
                scaled = (pct / 100.0 * sub["margin_usdt"]).sum()
                print(
                    f"  {label}: n={len(sub)} actual_sum=${actual:.2f} "
                    f"scaled_hist=${scaled:.2f} counterfactual_$20=${flat20:.2f} "
                    f"($20 vs scaled ${flat20 - scaled:+.2f}) {PIPELINE_ENFORCEMENT_TAG}"
                )
    else:
        print(f"No executed trades yet. {PIPELINE_ENFORCEMENT_TAG}")

    # --- 2) APPROVE replay cohort: counterfactual margin sizes ---
    cohort = _build_approve_cohort(blocked_df)
    replay_rows = []
    for size in size_grid:
        total = 0.0
        n = wins = 0
        for row in cohort:
            if not row.get("has_replay"):
                continue
            replay = row.get("replay")
            if not replay:
                continue
            orig_margin = float(replay.get("margin_usdt") or 20.0)
            res = _sim_replay_pnl_usd(replay)
            if res is None:
                continue
            pnl_orig, _ex = res
            margin_pct = (pnl_orig / orig_margin * 100) if orig_margin > 0 else 0
            pnl_at_size = margin_pct / 100.0 * size
            total += pnl_at_size
            n += 1
            if pnl_at_size > 0:
                wins += 1
        wr = round(100 * wins / n, 1) if n else 0.0
        replay_rows.append({
            "margin_usd": size,
            "replays": n,
            "sum_pnl_usd": round(total, 2),
            "avg_pnl_usd": round(total / n, 2) if n else 0.0,
            "win_rate_pct": wr,
            "is_live": size == FLAT_MARGIN_LIVE_USD,
        })
    if replay_rows:
        rdf = pd.DataFrame(replay_rows).sort_values("sum_pnl_usd", ascending=False)
        print(f"\n--- APPROVE replay counterfactual margin (n cohort={len(cohort)}) ---")
        print(rdf.to_string(index=False))
        best_r = rdf.iloc[0]
        print(
            f"🎯 Best replay flat margin: ${best_r['margin_usd']:.0f} — "
            f"sum ${best_r['sum_pnl_usd']:.2f} WR {best_r['win_rate_pct']:.1f}% "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )

    # --- 3) Reference scaled margin from snapshots (what v78 would have used) ---
    snaps = _load_signal_snapshots()
    if snaps:
        ref_rows = []
        for tid, snap in snaps.items():
            sizing = snap.get("entry_sizing") or {}
            ref_m = sizing.get("reference_scaled_margin_usdt")
            if ref_m is None:
                continue
            row = next((r for r in (cohort or []) if r["trade_id"] == tid), None)
            res = _sim_replay_pnl_usd(row.get("replay")) if row and row.get("has_replay") else None
            if not res:
                continue
            pnl_orig, _ = res
            orig_m = float((row.get("replay") or {}).get("margin_usdt") or ref_m or 20)
            margin_pct = pnl_orig / orig_m * 100 if orig_m > 0 else 0
            ref_rows.append({
                "id": tid[:8],
                "ref_scaled": ref_m,
                "pnl_at_ref": round(margin_pct / 100 * ref_m, 2),
                "pnl_at_20": round(margin_pct / 100 * 20, 2),
                "delta_20_vs_ref": round(margin_pct / 100 * (20 - ref_m), 2),
            })
        if ref_rows:
            rdf2 = pd.DataFrame(ref_rows)
            print(f"\n--- Snapshot reference scaled margin vs flat $20 (replay, n={len(rdf2)}) ---")
            print(
                f"  sum at reference scaled: ${rdf2['pnl_at_ref'].sum():.2f} | "
                f"sum at flat $20: ${rdf2['pnl_at_20'].sum():.2f} | "
                f"delta (20 - ref): ${rdf2['delta_20_vs_ref'].sum():+.2f} "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )
            worse = (rdf2["delta_20_vs_ref"] < 0).sum()
            better = (rdf2["delta_20_vs_ref"] > 0).sum()
            print(
                f"  Paths where $20 beats scaling: {better} | where scaling beats $20: {worse} "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )
    return {"best_flat_margin_usd": float(cf_df.iloc[0]["margin_usd"]) if df is not None and not df.empty else None}


def thesis_fast_cut_optimization(df):
    """Sweep THESIS_FAST_EXIT_UNREAL_PCT to find sweet spot (max sum PnL / win rate)."""
    print("\n=== THESIS FAST-CUT SWEET SPOT (THESIS_FAST_EXIT_UNREAL_PCT) ===")
    print(f"Current default: {THESIS_FAST_EXIT_DEFAULT}% margin | Ladder arms at peak >= {TRAIL_LADDER[0][0]}% {PIPELINE_ENFORCEMENT_TAG}")
    if df.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return None
    work = df.copy()
    work["margin"] = pd.to_numeric(work.get("margin_usdt"), errors="coerce")
    work["mfe"] = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce")
    work["mae"] = pd.to_numeric(work.get("mae_margin_pct", work.get("max_drawdown")), errors="coerce")
    work["final_m"] = pd.to_numeric(work.get("final_pnl_margin_pct", work.get("pnl")), errors="coerce")
    work["final_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    replays = _load_jsonl_replays()
    if replays:
        print(f"Tick replay available: {len(replays)} trades in {SIGNAL_REPLAY_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    else:
        print(f"No {SIGNAL_REPLAY_FILE} yet — using MFE/MAE heuristic (enable replay by running bot after update) {PIPELINE_ENFORCEMENT_TAG}")

    tfc = work[work.get("exit_reason", pd.Series(dtype=str)) == "THESIS_FAST_CUT"] if "exit_reason" in work.columns else pd.DataFrame()
    if not tfc.empty:
        print(f"THESIS_FAST_CUT trades: {len(tfc)} | avg peak {tfc['mfe'].mean():.1f}% | avg MAE {tfc['mae'].mean():.1f}% | avg final {tfc['final_m'].mean():.1f}% {PIPELINE_ENFORCEMENT_TAG}")
        missed_ladder = tfc[(tfc["mfe"] >= 8) & (tfc["mfe"] < TRAIL_LADDER[0][0])]
        if len(missed_ladder):
            print(f"  {len(missed_ladder)} fast-cuts peaked 8–{TRAIL_LADDER[0][0]-1}% — lower ladder first rung may have locked profit before cut {PIPELINE_ENFORCEMENT_TAG}")

    rows = []
    for cut_pct in THESIS_FAST_CUT_CANDIDATES:
        total_usd = 0.0
        n_sim = 0
        for _, row in work.iterrows():
            margin = row.get("margin")
            if pd.isna(margin) or margin <= 0:
                continue
            tid = row.get("trade_id")
            replay = replays.get(tid)
            sim_usd = None
            if replay and replay.get("ticks"):
                entry = pd.to_numeric(row.get("entry"), errors="coerce")
                lev = pd.to_numeric(row.get("leverage"), errors="coerce") or 100
                sim_usd, _, _ = _simulate_ticks_fast_cut_ladder(
                    replay["ticks"], entry, row.get("dir"), lev, margin,
                    cut_pct, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT,
                )
            if sim_usd is None:
                mae = row.get("mae")
                mfe = row.get("mfe")
                final_m = row.get("final_m")
                final_usd = row.get("final_usd")
                if pd.notna(mfe) and mfe >= TRAIL_LADDER[0][0]:
                    _, lock = ladder_lock_floor(mfe)
                    if pd.notna(final_m) and lock is not None:
                        total_usd += _margin_pct_to_usd(max(lock, final_m) if final_m > 0 else final_m, margin)
                    elif pd.notna(final_usd):
                        total_usd += final_usd
                    n_sim += 1
                    continue
                if pd.notna(mae) and mae <= cut_pct:
                    total_usd += _margin_pct_to_usd(cut_pct, margin)
                elif pd.notna(final_usd):
                    total_usd += final_usd
            else:
                total_usd += sim_usd
            n_sim += 1
        if n_sim:
            rows.append({
                "fast_cut_%": cut_pct,
                "sim_sum_pnl_usd": round(total_usd, 2),
                "sim_avg_pnl_usd": round(total_usd / n_sim, 2),
                "trades": n_sim,
            })
    if rows:
        sweep = pd.DataFrame(rows).sort_values("sim_sum_pnl_usd", ascending=False)
        print(sweep.to_string(index=False))
        best = sweep.iloc[0]
        print(f"\n🎯 Suggested THESIS_FAST_EXIT: {best['fast_cut_%']:.0f}% margin — sim sum PnL ${best['sim_sum_pnl_usd']:.2f} (current {THESIS_FAST_EXIT_DEFAULT}%) {PIPELINE_ENFORCEMENT_TAG}")
        return float(best["fast_cut_%"])
    print(f"Insufficient data for thesis fast-cut sweep. {PIPELINE_ENFORCEMENT_TAG}")
    return None


def ladder_first_rung_optimization(df):
    """Sweep ladder first trigger (default 12%) / lock (default 8%) for max captured profit."""
    print("\n=== LADDER FIRST-RUNG SWEET SPOT (peak trigger → lock floor) ===")
    print(f"Current first rung: peak >= {TRAIL_LADDER[0][0]}% → lock {TRAIL_LADDER[0][1]}% {PIPELINE_ENFORCEMENT_TAG}")
    if df.empty:
        return None
    work = df.copy()
    work["margin"] = pd.to_numeric(work.get("margin_usdt"), errors="coerce")
    work["mfe"] = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce")
    work["final_m"] = pd.to_numeric(work.get("final_pnl_margin_pct", work.get("pnl")), errors="coerce")
    work["final_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")

    rows = []
    for trig, lock in LADDER_FIRST_RUNG_CANDIDATES:
        ladder = [(trig, lock)] + [(t, l) for t, l in TRAIL_LADDER if t > trig]
        total = 0.0
        n = 0
        armed = 0
        for _, row in work.iterrows():
            margin = row.get("margin")
            mfe = row.get("mfe")
            final_m = row.get("final_m")
            final_usd = row.get("final_usd")
            if pd.isna(margin) or margin <= 0:
                continue
            if pd.notna(mfe) and mfe >= trig:
                armed += 1
                _, lock_floor = _ladder_lock_for_peak_custom(mfe, ladder)
                if lock_floor is not None:
                    sim_m = max(lock_floor, final_m) if pd.notna(final_m) and final_m > 0 else lock_floor
                    total += _margin_pct_to_usd(sim_m, margin)
                elif pd.notna(final_usd):
                    total += final_usd
            elif pd.notna(final_usd):
                total += final_usd
            n += 1
        if n:
            rows.append({
                "trigger_%": trig,
                "lock_%": lock,
                "would_arm": armed,
                "sim_sum_pnl_usd": round(total, 2),
                "sim_avg_usd": round(total / n, 2),
            })
    if rows:
        sweep = pd.DataFrame(rows).sort_values("sim_sum_pnl_usd", ascending=False)
        print(sweep.to_string(index=False))
        best = sweep.iloc[0]
        print(f"\n🎯 Suggested first rung: peak >= {int(best['trigger_%'])}% → lock {int(best['lock_%'])}% — sim sum ${best['sim_sum_pnl_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}")
        return int(best["trigger_%"]), int(best["lock_%"])
    return None


def thesis_exit_above_optimization(df):
    """Sweep THESIS_EXIT_IF_ABOVE_UNREAL_PCT — profit level that pauses thesis checks."""
    print("\n=== THESIS EXIT-ABOVE SWEET SPOT (pause thesis when unreal > X%) ===")
    print(f"Current: thesis checks skipped while unreal > {THESIS_EXIT_ABOVE_DEFAULT}% {PIPELINE_ENFORCEMENT_TAG}")
    if df.empty:
        return None
    work = df.copy()
    work["mfe"] = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce")
    work["final_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    tfc = work[work.get("exit_reason", pd.Series(dtype=str)) == "THESIS_FAST_CUT"] if "exit_reason" in work.columns else pd.DataFrame()
    for above in THESIS_EXIT_ABOVE_CANDIDATES:
        if tfc.empty:
            break
        had_profit = tfc[tfc["mfe"] >= above]
        if len(had_profit):
            print(f"  above={above}%: {len(had_profit)} fast-cuts had peaked >= {above}% (ladder/rung may be better) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Lower first ladder rung often beats raising exit-above alone — see ladder sweep. {PIPELINE_ENFORCEMENT_TAG}")
    return None


def research_data_coverage_audit(trades, decisions, blocked, pipeline_events=None):
    """Report what bot exports vs what analyzer needs for thesis/ladder/pullback sweeps."""
    print("\n=== RESEARCH DATA COVERAGE AUDIT (CSV / JSONL / exports) ===")
    checks = [
        ("trades_3factor.csv", TRADES_FILE, "required", ["max_profit", "max_drawdown", "exit_reason", "pnl", "net_pnl_usd", "margin_usdt", "entry", "price_at_signal", "entry_delay"]),
        ("decisions_3factor.csv", DECISIONS_FILE, "required", ["trade_id", "decision", "reason", "edge_score"]),
        ("blocked_signals_3factor.csv", BLOCKED_FILE, "funnel", ["trade_id", "reason"]),
        ("ai_tranche_log.csv", AI_TRANCHE_FILE, "funnel", ["trade_id", "decision"]),
        ("pipeline_events_3factor.csv", PIPELINE_EVENTS_FILE, "optional", ["trade_id", "stage"]),
        ("signal_persist.log", SIGNAL_PERSIST_FILE, "optional", ["trade_id", "stage"]),
        ("near_edge.log", NEAR_EDGE_FILE, "optional", ["edge_score"]),
        ("candles_3factor.csv", CANDLES_FILE, "optional", ["ts", "close"]),
        (SIGNAL_REPLAY_FILE, SIGNAL_REPLAY_FILE, "counterfactual", ["trade_id", "ticks"]),
        (TRADE_OUTCOME_FILE, TRADE_OUTCOME_FILE, "counterfactual", ["trade_id", "exit_config", "max_profit_margin_pct"]),
        (SHADOW_OUTCOME_FILE, SHADOW_OUTCOME_FILE, "counterfactual", ["trade_id", "net_pnl_usd", "block_reason"]),
        (SIGNAL_SNAPSHOT_FILE, SIGNAL_SNAPSHOT_FILE, "counterfactual", ["trade_id", "direction", "ai", "entry_gates", "entry_thesis", "research_buckets"]),
        (COUNTERFACTUAL_FILE, COUNTERFACTUAL_FILE, "counterfactual", ["trade_id", "net_pnl_usd"]),
        (EDGE_CENSUS_FILE, EDGE_CENSUS_FILE, "counterfactual", ["edge_score", "edge_score_bucket", "reason"]),
    ]
    snap_cov = _load_signal_snapshots()
    if snap_cov:
        n = len(snap_cov)
        has_gates = sum(1 for s in snap_cov.values() if s.get("entry_gates"))
        has_mom = sum(1 for s in snap_cov.values() if (s.get("entry_gates") or {}).get("mom_metric") is not None or (s.get("entry_features") or {}).get("mom_metric") is not None)
        has_mtf = sum(1 for s in snap_cov.values() if (s.get("entry_thesis") or {}).get("mtf_15m") or ((s.get("entry_gates") or {}).get("multi_tf") or {}).get("mtf_15m"))
        v5 = sum(1 for s in snap_cov.values() if s.get("schema") == "signal_snapshot_v5")
        v4 = sum(1 for s in snap_cov.values() if s.get("schema") == "signal_snapshot_v4")
        v3 = sum(1 for s in snap_cov.values() if s.get("schema") == "signal_snapshot_v3")
        has_regime = sum(1 for s in snap_cov.values() if s.get("entry_regime"))
        has_margins = sum(1 for s in snap_cov.values() if (s.get("entry_gates") or {}).get("margins"))
        has_post_block = sum(1 for s in snap_cov.values() if s.get("post_block_research"))
        has_rb = sum(1 for s in snap_cov.values() if s.get("research_buckets"))
        has_full_feat = sum(1 for s in snap_cov.values() if s.get("features"))
        has_horizon = sum(
            1 for s in snap_cov.values()
            if ((s.get("outcome") or {}).get("horizon_outcomes") or (s.get("post_block_research") or {}).get("horizon_outcomes"))
        )
        print(
            f"\n  signal_snapshot coverage: n={n} v5={v5} v4={v4} v3={v3} entry_gates={has_gates} "
            f"margins={has_margins} regime={has_regime} post_block={has_post_block} "
            f"research_buckets={has_rb} full_features={has_full_feat} horizon_outcomes={has_horizon} "
            f"mom_metric={has_mom} mtf_per_tf={has_mtf} {PIPELINE_ENFORCEMENT_TAG}"
        )
        if v5 < n and v4 < n:
            print(f"      ⚠️ {n - max(v4, v5)} snapshots pre-v5 — restart bot on {EXPECTED_BOT_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
        if has_margins < n and v4 > 0:
            print(f"      ⚠️ gate margins missing on some v4 rows {PIPELINE_ENFORCEMENT_TAG}")
        if has_mom < n * 0.5:
            print(f"      ⚠️ mom_metric sparse ({has_mom}/{n}) — chop sweeps use block_reason fallback {PIPELINE_ENFORCEMENT_TAG}")
    shadow_cov = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    if shadow_cov:
        v2 = sum(1 for s in shadow_cov.values() if s.get("schema") == "shadow_outcome_v2")
        pbr = sum(1 for s in shadow_cov.values() if s.get("post_block_research"))
        print(f"  shadow_outcome: n={len(shadow_cov)} v2={v2} post_block_research={pbr} {PIPELINE_ENFORCEMENT_TAG}")
    for label, path, tier, cols in checks:
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        status = "✅" if exists and size > 0 else ("⚠️ missing" if not exists else "⚠️ empty")
        print(f"  {status} {label} ({tier}) size={size} {PIPELINE_ENFORCEMENT_TAG}")
        if exists and size > 0 and label == TRADES_FILE and not trades.empty:
            missing_cols = [c for c in cols if c not in trades.columns]
            new_cfg = [c for c in trades.columns if str(c).startswith("cfg_")]
            if missing_cols:
                print(f"      missing columns: {missing_cols} {PIPELINE_ENFORCEMENT_TAG}")
            else:
                print(f"      core trade columns OK {PIPELINE_ENFORCEMENT_TAG}")
            if new_cfg:
                print(f"      exit config columns: {new_cfg[:6]}... {PIPELINE_ENFORCEMENT_TAG}")
            elif "exit_config_json" not in trades.columns:
                print(f"      ⚠️ no cfg_* / exit_config_json — restart bot on latest bybit_bot.py {PIPELINE_ENFORCEMENT_TAG}")
    print("\nDashboard Download CSV Logs zip should include:")
    for f in EXPORT_ZIP_FILES:
        ok = "✅" if os.path.exists(f) and os.path.getsize(f) > 0 else "—"
        print(f"    {ok} {f}")
    print(f"\nFor best thesis/ladder optimization: need max_profit + max_drawdown on every trade (✅ logged now).")
    print(f"For tick-accurate sweeps: need signal_replay.jsonl (✅ from APPROVE, 1 tick/sec).")
    print(f"trade_outcome.jsonl: executed path + exit_config (✅ on close).")
    print(f"signal_snapshot v5: full features/context/funding + horizon MFE/MAE (✅ {EXPECTED_BOT_VERSION}).")
    abr_n = len(_load_jsonl_by_trade_id(APPROVED_BUT_REJECTED_FILE)) if os.path.exists(APPROVED_BUT_REJECTED_FILE) else 0
    nm_n = len(_load_jsonl_by_trade_id(NEAR_MISS_FILE)) if os.path.exists(NEAR_MISS_FILE) else 0
    sr_n = len(_load_jsonl_by_trade_id(SOFT_REJECT_SHADOW_FILE)) if os.path.exists(SOFT_REJECT_SHADOW_FILE) else 0
    print(f"approved_but_rejected.jsonl: n={abr_n} | near_miss.jsonl: n={nm_n} | soft_reject_shadow.jsonl: n={sr_n}")
    print(f"shadow_outcome v2: post-block continuation + block quality metrics (✅ after blocked APPROVEs).")
    print(
        f"Sweeps: gate margins + post-block + regime + margin size $5–$25 "
        f"(✅ {ANALYZER_VERSION}). {PIPELINE_ENFORCEMENT_TAG}"
    )


def mfe_mae_analysis(df):
    print("\n=== MFE / MAE ANALYSIS (margin %) ===")
    if df.empty or "mfe_margin_pct" not in df.columns:
        print("Missing MFE data (max_profit). {PIPELINE_ENFORCEMENT_TAG}")
        return
    mfe = pd.to_numeric(df["mfe_margin_pct"], errors="coerce")
    mae = pd.to_numeric(df.get("mae_margin_pct", 0), errors="coerce")
    final = pd.to_numeric(df.get("final_pnl_margin_pct", 0), errors="coerce")
    left = pd.to_numeric(df.get("profit_left_on_table", mfe - final), errors="coerce")
    print(f"Trades with MFE>0: {(mfe > 0).sum()} | MFE>=12%: {(mfe >= 12).sum()} | MFE>=20%: {(mfe >= 20).sum()} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg MFE: {mfe.mean():.2f}% | Avg MAE: {mae.mean():.2f}% | Avg final: {final.mean():.2f}% | Avg left on table: {left.mean():.2f}% {PIPELINE_ENFORCEMENT_TAG}")
    ef = df[df.get("exit_failure_flag", 0) == 1] if "exit_failure_flag" in df.columns else pd.DataFrame()
    print(f"Exit-failure flags (MFE>=12, gave back): {len(ef)} ({len(ef)/len(df)*100:.1f}% of trades) {PIPELINE_ENFORCEMENT_TAG}")
    if len(ef) > 0 and "exit_reason" in ef.columns:
        print(f"Exit-failure mix:\n{ef['exit_reason'].value_counts().to_string()} {PIPELINE_ENFORCEMENT_TAG}")
    if "exit_reason" in df.columns:
        print("\nMFE by exit_reason (mean MFE / mean final pnl %):")
        agg = df.groupby("exit_reason").agg(
            count=("trade_id", "count"),
            avg_mfe=("mfe_margin_pct", lambda x: pd.to_numeric(x, errors="coerce").mean()),
            avg_final=("final_pnl_margin_pct", lambda x: pd.to_numeric(x, errors="coerce").mean()),
            avg_left=("profit_left_on_table", lambda x: pd.to_numeric(x, errors="coerce").mean()),
        ).round(2)
        print(agg.to_string())
        print(PIPELINE_ENFORCEMENT_TAG)


def exit_forensics_report(df):
    print("\n=== EXIT FORENSICS (per trade) ===")
    if df.empty:
        return
    cols = [c for c in [
        "trade_id", "final_direction", "dir", "exit_reason", "mfe_margin_pct", "mae_margin_pct",
        "final_pnl_margin_pct", "profit_left_on_table", "net_pnl_usd", "dur_min",
        "bull_score_at_entry", "bear_score_at_entry", "structure_score_at_entry",
        "mtf_agreement_at_entry", "edge_score", "ai_win_prob", "thesis_conflict_flag",
    ] if c in df.columns]
    view = df[cols].copy()
    for c in ("mfe_margin_pct", "mae_margin_pct", "final_pnl_margin_pct", "profit_left_on_table", "net_pnl_usd"):
        if c in view.columns:
            view[c] = pd.to_numeric(view[c], errors="coerce").round(2)
    print(view.to_string(index=False))
    print(PIPELINE_ENFORCEMENT_TAG)


def time_exit_deep_analysis(df):
    print("\n=== TIME_EXIT DEEP DIVE ===")
    if "exit_reason" not in df.columns:
        return
    te = df[df["exit_reason"] == "TIME_EXIT"].copy()
    if te.empty:
        print("No TIME_EXIT trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    pnl = pd.to_numeric(te["net_pnl_usd"], errors="coerce")
    mfe = pd.to_numeric(te.get("mfe_margin_pct", 0), errors="coerce")
    final = pd.to_numeric(te.get("final_pnl_margin_pct", 0), errors="coerce")
    print(f"TIME_EXIT count: {len(te)} | sum PnL: ${pnl.sum():.2f} | avg: ${pnl.mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg peak (MFE): {mfe.mean():.2f}% | Avg final margin: {final.mean():.2f}% {PIPELINE_ENFORCEMENT_TAG}")
    gave_back = te[(mfe >= 12) & (final < 5)]
    print(f"Went +12% then closed <+5%: {len(gave_back)} trades (exit timing issue) {PIPELINE_ENFORCEMENT_TAG}")
    if len(gave_back) > 0:
        print(gave_back[[c for c in ["trade_id", "mfe_margin_pct", "final_pnl_margin_pct", "net_pnl_usd"] if c in gave_back.columns]].to_string(index=False))


def bull_bear_spread_analysis(df):
    _bucket_table(
        df, "directional_factor_spread",
        bins=[-10, -5, -3, -2, -1, 0, 1, 2, 3, 5, 10],
        labels=["<=-5", "-5:-3", "-3:-2", "-2:-1", "-1:0", "0:1", "1:2", "2:3", "3:5", ">5"],
        title="BULL-BEAR SPREAD (directional: + favors trade direction)",
    )


def structure_score_analysis(df):
    _bucket_table(
        df, "structure_score_at_entry",
        bins=[-10, -5, -2, 2, 5, 10],
        labels=["<=-5", "-5:-2", "-2:2", "2:5", ">5"],
        title="STRUCTURE SCORE AT ENTRY",
    )


def mtf_alignment_analysis(df):
    print("\n=== MTF ALIGNMENT AT ENTRY ===")
    col = "mtf_agreement_at_entry"
    if col not in df.columns or df["mtf_agreement_at_entry"].notna().sum() == 0:
        print("No MTF agreement parsed — check AI log JSON in comments. {PIPELINE_ENFORCEMENT_TAG}")
        return
    rows = []
    for tag, g in df.groupby(col, dropna=False):
        pnl = pd.to_numeric(g["net_pnl_usd"], errors="coerce")
        rows.append({
            "mtf": tag,
            "trades": len(g),
            "win_rate_pct": round((pnl > 0).mean() * 100, 1) if len(g) else 0,
            "sum_pnl": round(pnl.sum(), 2),
            "profit_factor": round(_profit_factor(pnl), 2) if len(g) > 1 else np.nan,
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print(PIPELINE_ENFORCEMENT_TAG)


def contradiction_analysis(df):
    print("\n=== CONTRADICTION INDEX (bull+bear) ===")
    if "contradiction_index" not in df.columns:
        return
    _bucket_table(
        df, "contradiction_index",
        bins=[0, 4, 6, 8, 10, 20],
        labels=["0-4", "4-6", "6-8", "8-10", "10+"],
        title="CONTRADICTION BUCKETS",
    )
    if "thesis_conflict_flag" in df.columns:
        tc = df[df["thesis_conflict_flag"] == 1]
        print(f"Thesis conflict flags: {len(tc)} / {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
        if len(tc) > 0:
            print(f"Conflict trades avg PnL: ${pd.to_numeric(tc['net_pnl_usd'], errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")


def funding_adx_analysis(df):
    _bucket_table(df, "adx_at_entry", bins=[0, 20, 25, 30, 35, 50], labels=["<20", "20-25", "25-30", "30-35", "35+"], title="ADX AT ENTRY")
    if "funding_rate_pct_8h_at_entry" in df.columns and df["funding_rate_pct_8h_at_entry"].notna().any():
        _bucket_table(
            df, "funding_rate_pct_8h_at_entry",
            bins=[-0.05, -0.01, 0, 0.01, 0.05, 0.10],
            labels=["neg_extreme", "neg", "neutral", "pos", "pos_extreme"],
            title="FUNDING RATE % PER 8H AT ENTRY",
        )


def edge_combo_analysis(df):
    print("\n=== EDGE × v5.5 COMBO MATRIX ===")
    if df.empty:
        return
    work = df.copy()
    work["edge_hi"] = pd.to_numeric(work.get("edge_score", work.get("edge_score_at_entry")), errors="coerce") >= 4.0
    work["spread_hi"] = pd.to_numeric(work.get("directional_factor_spread"), errors="coerce") >= 3
    work["struct_long"] = pd.to_numeric(work.get("structure_score_at_entry"), errors="coerce") >= 2
    work["struct_short"] = pd.to_numeric(work.get("structure_score_at_entry"), errors="coerce") <= -2
    combos = [
        ("edge>=4 & spread>=3", work["edge_hi"] & work["spread_hi"]),
        ("edge>=4 & MTF BULL_ALIGNED", work["edge_hi"] & (work.get("mtf_agreement_at_entry") == "BULL_ALIGNED")),
        ("edge>=4 & MTF BEAR_ALIGNED", work["edge_hi"] & (work.get("mtf_agreement_at_entry") == "BEAR_ALIGNED")),
        ("edge>=4 & structure>=2", work["edge_hi"] & work["struct_long"]),
        ("edge>=4 & structure<=-2", work["edge_hi"] & work["struct_short"]),
    ]
    rows = []
    for name, mask in combos:
        g = work[mask]
        if len(g) == 0:
            continue
        pnl = pd.to_numeric(g["net_pnl_usd"], errors="coerce")
        rows.append({
            "combo": name,
            "n": len(g),
            "win_rate_pct": round((pnl > 0).mean() * 100, 1),
            "sum_pnl": round(pnl.sum(), 2),
            "profit_factor": round(_profit_factor(pnl), 2),
        })
    if rows:
        print(pd.DataFrame(rows).sort_values("sum_pnl", ascending=False).to_string(index=False))
    else:
        print("No combo samples yet. {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def factor_gate_funnel(decisions, blocked, ai_log):
    print("\n=== FACTOR GATE / REJECT FUNNEL (non-trades) ===")
    texts = []
    if not ai_log.empty and "comment" in ai_log.columns:
        texts.extend(ai_log["comment"].astype(str).tolist())
    if not decisions.empty and "reason" in decisions.columns:
        print(decisions["reason"].value_counts().head(15).to_string())
        print(PIPELINE_ENFORCEMENT_TAG)
    if not blocked.empty and "reason" in blocked.columns:
        print("\nBlocked reasons:")
        print(blocked["reason"].value_counts().head(15).to_string())
        print(PIPELINE_ENFORCEMENT_TAG)
    gate_hits = sum(1 for t in texts if "FACTOR_GATE" in str(t))
    no_trade = sum(1 for t in texts if "NO_TRADE" in str(t) and "REJECT" in str(t))
    print(f"AI log mentions FACTOR_GATE: {gate_hits} | NO_TRADE rejects in comments: {no_trade} {PIPELINE_ENFORCEMENT_TAG}")


def _edge_score_bucket_val(edge):
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


def _spread_bucket_val(spread):
    s = int(float(spread or 0))
    if s <= 1:
        return "0-1"
    if s == 2:
        return "2"
    if s == 3:
        return "3"
    if s == 4:
        return "4"
    return "5+"


def _sr_bucket_val(sr_state):
    s = str(sr_state or "").upper()
    if "SUPPORT" in s:
        return "NEAR_SUPPORT"
    if "RESISTANCE" in s:
        return "NEAR_RESISTANCE"
    return "MID_RANGE"


def _ai_prob_bucket_val(prob):
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


def _load_edge_census():
    if not os.path.exists(EDGE_CENSUS_FILE):
        return pd.DataFrame()
    rows = []
    try:
        with open(EDGE_CENSUS_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        print(f"⚠️ edge_census read error: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return pd.DataFrame()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _load_trade_outcomes_v2():
    if not os.path.exists(TRADE_OUTCOME_FILE):
        return pd.DataFrame()
    rows = []
    try:
        with open(TRADE_OUTCOME_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        print(f"⚠️ trade_outcome read error: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return pd.DataFrame()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _bucket_perf_table(df, bucket_col, title, bucket_order=None):
    print(f"\n--- {title} ---")
    if df is None or df.empty or bucket_col not in df.columns:
        print(f"No data for {title}. {PIPELINE_ENFORCEMENT_TAG}")
        return pd.DataFrame()
    work = df.copy()
    work["net_pnl_usd"] = pd.to_numeric(work.get("net_pnl_usd", 0), errors="coerce").fillna(0)
    work["mfe"] = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit", work.get("max_profit_margin_pct", 0))), errors="coerce")
    work["mae"] = pd.to_numeric(work.get("mae_margin_pct", work.get("max_drawdown", work.get("max_drawdown_margin_pct", 0))), errors="coerce")
    work["win"] = work["net_pnl_usd"] > 0
    if bucket_order:
        work[bucket_col] = pd.Categorical(work[bucket_col], categories=bucket_order, ordered=True)
    g = work.groupby(bucket_col, observed=False)
    rows = []
    for bucket, sub in g:
        if pd.isna(bucket) or (isinstance(bucket, str) and not str(bucket).strip()):
            continue
        n = len(sub)
        if n == 0:
            continue
        wins = sub["win"].sum()
        gross_win = sub.loc[sub["net_pnl_usd"] > 0, "net_pnl_usd"].sum()
        gross_loss = abs(sub.loc[sub["net_pnl_usd"] <= 0, "net_pnl_usd"].sum())
        rr = (gross_win / gross_loss) if gross_loss > 0 else np.nan
        rows.append({
            bucket_col: bucket,
            "trades": n,
            "win_rate_pct": round(100.0 * wins / n, 1),
            "rr": round(rr, 2) if not np.isnan(rr) else np.nan,
            "expectancy": round(sub["net_pnl_usd"].mean(), 2),
            "sum_pnl": round(sub["net_pnl_usd"].sum(), 2),
            "avg_mfe": round(sub["mfe"].mean(), 2) if sub["mfe"].notna().any() else np.nan,
            "avg_mae": round(sub["mae"].mean(), 2) if sub["mae"].notna().any() else np.nan,
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        print(out.to_string(index=False))
    else:
        print(f"(empty) {PIPELINE_ENFORCEMENT_TAG}")
    return out


def _enrich_trades_with_buckets(df):
    if df is None or df.empty:
        return df
    work = df.copy()
    if "edge_score_bucket" not in work.columns:
        edge_src = work.get("edge_score_at_entry", work.get("edge_score", 0))
        work["edge_score_bucket"] = pd.to_numeric(edge_src, errors="coerce").apply(_edge_score_bucket_val)
    if "directional_spread_bucket" not in work.columns:
        bull = pd.to_numeric(work.get("bull_score_at_entry", 0), errors="coerce").fillna(0)
        bear = pd.to_numeric(work.get("bear_score_at_entry", 0), errors="coerce").fillna(0)
        direction = work.get("final_direction", work.get("dir", "LONG")).astype(str).str.upper()
        spread = np.where(direction == "LONG", bull - bear, bear - bull)
        work["directional_spread"] = spread
        work["directional_spread_bucket"] = pd.Series(spread).apply(_spread_bucket_val)
    if "support_resistance_bucket" not in work.columns:
        work["support_resistance_bucket"] = work.get("sr_state", "UNKNOWN").apply(_sr_bucket_val)
    if "ai_probability_bucket" not in work.columns:
        work["ai_probability_bucket"] = pd.to_numeric(work.get("ai_win_prob", 0), errors="coerce").apply(_ai_prob_bucket_val)
    if "session_bucket" not in work.columns:
        work["session_bucket"] = "UNKNOWN"
    if "mfe_margin_pct" not in work.columns and "max_profit" in work.columns:
        work["mfe_margin_pct"] = pd.to_numeric(work["max_profit"], errors="coerce")
    if "mae_margin_pct" not in work.columns and "max_drawdown" in work.columns:
        work["mae_margin_pct"] = pd.to_numeric(work["max_drawdown"], errors="coerce")
    return work


def edge_histogram_report(near_edge, edge_census, pipeline_events, decisions):
    print("\n=== V80 EDGE HISTOGRAM (full distribution) ===")
    frames = []
    if near_edge is not None and not near_edge.empty and "edge_score" in near_edge.columns:
        ne = near_edge.copy()
        ne["source"] = "near_edge"
        ne["edge_score_bucket"] = pd.to_numeric(ne["edge_score"], errors="coerce").apply(_edge_score_bucket_val)
        frames.append(ne[["edge_score", "edge_score_bucket", "source"]])
    if edge_census is not None and not edge_census.empty and "edge_score" in edge_census.columns:
        ec = edge_census.copy()
        ec["source"] = "edge_census"
        if "edge_score_bucket" not in ec.columns:
            ec["edge_score_bucket"] = pd.to_numeric(ec["edge_score"], errors="coerce").apply(_edge_score_bucket_val)
        frames.append(ec[["edge_score", "edge_score_bucket", "source"]])
    if pipeline_events is not None and not pipeline_events.empty:
        pe = pipeline_events.copy()
        if "edge_score" in pe.columns or "edge" in pe.columns:
            pe["edge_score"] = pd.to_numeric(pe.get("edge_score", pe.get("edge")), errors="coerce")
            pe = pe.dropna(subset=["edge_score"])
            pe["source"] = "pipeline_events"
            pe["edge_score_bucket"] = pe["edge_score"].apply(_edge_score_bucket_val)
            frames.append(pe[["edge_score", "edge_score_bucket", "source"]])
    if not frames:
        print(f"No edge distribution sources yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    all_edges = pd.concat(frames, ignore_index=True)
    print("\nEdge count by bucket (all sources combined):")
    hist = all_edges.groupby("edge_score_bucket", observed=False).size().reindex(EDGE_BUCKET_ORDER, fill_value=0)
    for bucket, count in hist.items():
        print(f"  {bucket}: {int(count)}")
    if not decisions.empty and "edge_score" in decisions.columns:
        dec = decisions.copy()
        dec["edge_score_bucket"] = pd.to_numeric(dec["edge_score"], errors="coerce").apply(_edge_score_bucket_val)
        print("\nDecision rows by edge bucket:")
        print(dec.groupby("edge_score_bucket", observed=False).size().reindex(EDGE_BUCKET_ORDER, fill_value=0).to_string())


def edge_funnel_by_bucket_report(decisions, ai_log, trades, near_edge):
    print("\n=== V80 EDGE FUNNEL BY BUCKET ===")
    approve_ids = set()
    if not ai_log.empty and "decision" in ai_log.columns:
        approve_ids = set(ai_log.loc[ai_log["decision"] == "APPROVE", "trade_id"].dropna().astype(str))
    exec_ids = set()
    if not trades.empty and "trade_id" in trades.columns:
        exec_ids = set(trades["trade_id"].dropna().astype(str))
    win_ids = set()
    if not trades.empty and "trade_id" in trades.columns:
        pnl = pd.to_numeric(trades.get("net_pnl_usd", 0), errors="coerce")
        win_ids = set(trades.loc[pnl > 0, "trade_id"].dropna().astype(str))

    signal_rows = []
    if near_edge is not None and not near_edge.empty:
        for _, row in near_edge.iterrows():
            tid = str(row.get("trade_id") or "")
            edge = float(row.get("edge_score") or 0)
            bucket = row.get("edge_score_bucket") or _edge_score_bucket_val(edge)
            signal_rows.append({"edge_score_bucket": bucket, "trade_id": tid})
    if not signal_rows and not decisions.empty and "edge_score" in decisions.columns:
        for _, row in decisions.iterrows():
            edge = float(row.get("edge_score") or 0)
            bucket = _edge_score_bucket_val(edge)
            tid = str(row.get("trade_id") or "")
            signal_rows.append({"edge_score_bucket": bucket, "trade_id": tid})

    if not signal_rows:
        print(f"No signal-stage edge rows. {PIPELINE_ENFORCEMENT_TAG}")
        return

    sig_df = pd.DataFrame(signal_rows)
    rows = []
    for bucket in EDGE_BUCKET_ORDER:
        sub = sig_df[sig_df["edge_score_bucket"] == bucket]
        n_sig = len(sub)
        if n_sig == 0:
            continue
        tids = set(sub["trade_id"].astype(str)) - {""}
        n_app = len(tids & approve_ids) if tids else 0
        n_exec = len(tids & exec_ids) if tids else 0
        n_win = len(tids & win_ids) if tids else 0
        exec_sub = trades[trades["trade_id"].astype(str).isin(tids)] if not trades.empty else pd.DataFrame()
        sum_pnl = pd.to_numeric(exec_sub.get("net_pnl_usd", 0), errors="coerce").sum() if not exec_sub.empty else 0
        wr = 100.0 * n_win / n_exec if n_exec else 0.0
        rows.append({
            "edge_bucket": bucket,
            "signals": n_sig,
            "approves": n_app,
            "executed": n_exec,
            "win_rate_pct": round(wr, 1),
            "sum_pnl": round(float(sum_pnl), 2),
            "approve_rate_pct": round(100.0 * n_app / n_sig, 1) if n_sig else 0,
            "exec_rate_pct": round(100.0 * n_exec / n_app, 1) if n_app else 0,
        })
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    n_approve = len(approve_ids)
    if n_approve < MIN_APPROVES_FOR_EDGE_CONCLUSIONS:
        print(
            f"\n⚠️ Only {n_approve} APPROVEs — need ≥{MIN_APPROVES_FOR_EDGE_CONCLUSIONS} before edge sweet-spot conclusions. "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )


def edge_monotonicity_report(df):
    print("\n=== V80 EDGE MONOTONICITY ===")
    if df is None or df.empty:
        print(f"No executed trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = _enrich_trades_with_buckets(df)
    edge = pd.to_numeric(work.get("edge_score_at_entry", work.get("edge_score")), errors="coerce")
    pnl = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    mfe = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce")
    win = (pnl > 0).astype(int)
    pairs = [
        ("edge vs net_pnl_usd", edge, pnl),
        ("edge vs MFE", edge, mfe),
        ("edge vs win_flag", edge, win),
    ]
    for label, x, y in pairs:
        mask = x.notna() & y.notna()
        if mask.sum() < 3:
            print(f"  {label}: insufficient data (n={mask.sum()}) {PIPELINE_ENFORCEMENT_TAG}")
            continue
        corr = x[mask].corr(y[mask])
        print(f"  Correlation({label}) = {corr:.3f} (n={mask.sum()}) {PIPELINE_ENFORCEMENT_TAG}")
    pos = pairs[0][1].corr(pairs[0][2]) if pairs[0][1].notna().sum() >= 3 else 0
    if abs(pos) < 0.15:
        verdict = "NOISY / non-monotonic"
    elif pos > 0:
        verdict = "positive predictor (weak)" if pos < 0.35 else "positive predictor"
    else:
        verdict = "NEGATIVE predictor — higher edge → worse outcomes in sample"
    print(f"  Verdict: {verdict} {PIPELINE_ENFORCEMENT_TAG}")


def type_a_vs_type_b_report(df, trade_outcomes):
    print("\n=== V80 TYPE-A vs TYPE-B CLASSIFICATION ===")
    print("Type A: MFE < 10% | Type B: MFE >= 15% | MIXED: between")
    work = _enrich_trades_with_buckets(df) if df is not None and not df.empty else pd.DataFrame()
    if trade_outcomes is not None and not trade_outcomes.empty:
        tout = trade_outcomes.copy()
        if "trade_mfe_type" in tout.columns:
            for _, row in tout.iterrows():
                tid = row.get("trade_id")
                if tid and work.empty:
                    continue
        if not work.empty and "trade_id" in tout.columns:
            tout = tout.set_index("trade_id", drop=False)
    if work.empty:
        print(f"No executed trades for Type A/B. {PIPELINE_ENFORCEMENT_TAG}")
        return
    mfe = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce").fillna(0)
    work["trade_mfe_type"] = np.where(mfe >= 15, "TYPE_B", np.where(mfe < 10, "TYPE_A", "MIXED"))
    feature_cols = [
        ("edge_score_at_entry", "edge_score"),
        ("directional_spread", "spread"),
        ("structure_score_at_entry", "structure"),
        ("support_resistance_bucket", "sr_bucket"),
        ("adx_at_entry", "adx"),
        ("features_volume_ratio", "volume_ratio"),
        ("features_imbalance", "imbalance"),
        ("features_delta", "delta"),
        ("features_velocity", "velocity"),
        ("ai_win_prob", "ai_prob"),
    ]
    for ttype in ("TYPE_A", "TYPE_B", "MIXED"):
        sub = work[work["trade_mfe_type"] == ttype]
        print(f"\n{ttype}: n={len(sub)} sum_pnl=${pd.to_numeric(sub.get('net_pnl_usd',0), errors='coerce').sum():.2f}")
        if sub.empty:
            continue
        if "edge_score_bucket" in sub.columns:
            print(f"  edge buckets: {sub['edge_score_bucket'].value_counts().to_dict()}")
        avgs = {}
        for col, label in feature_cols:
            src = col if col in sub.columns else None
            if src:
                avgs[label] = round(pd.to_numeric(sub[src], errors="coerce").mean(), 3)
        if avgs:
            print(f"  feature averages: {avgs}")
    if len(work) >= 3:
        a = work[work["trade_mfe_type"] == "TYPE_A"]
        b = work[work["trade_mfe_type"] == "TYPE_B"]
        if not a.empty and not b.empty:
            sep = {}
            for col, label in feature_cols:
                if col not in work.columns:
                    continue
                am = pd.to_numeric(a[col], errors="coerce").mean()
                bm = pd.to_numeric(b[col], errors="coerce").mean()
                sep[label] = round(abs(float(bm) - float(am)), 3)
            ranked = sorted(sep.items(), key=lambda x: x[1], reverse=True)
            print(f"\n  Top separators (|TYPE_B mean - TYPE_A mean|): {ranked[:5]} {PIPELINE_ENFORCEMENT_TAG}")


def _normalize_first_3_candles(raw):
    """Parse first_3_candles from JSONL/CSV — skip NaN floats from empty DataFrame cells."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def ladder_booking_slip_report(df):
    """
    Explain PROFIT_LOCK_LADDER USD vs lock-floor expectation.
    Lock floor % is the minimum margin % to protect — exit books at the tick when
    unrealized falls through the floor (can slip if price gaps between checks).
    """
    print("\n=== LADDER BOOKING vs LOCK FLOOR (USD on $margin) ===")
    if df is None or df.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    rows = []
    for _, row in df.iterrows():
        if str(row.get("exit_reason", "")) != "PROFIT_LOCK_LADDER":
            continue
        peak = pd.to_numeric(row.get("mfe_margin_pct", row.get("max_profit")), errors="coerce")
        booked = pd.to_numeric(row.get("final_pnl_margin_pct", row.get("pnl")), errors="coerce")
        margin = pd.to_numeric(row.get("margin_usdt"), errors="coerce")
        net = pd.to_numeric(row.get("net_pnl_usd"), errors="coerce")
        trig, lock = ladder_lock_floor(peak)
        if lock is None or pd.isna(margin) or margin <= 0:
            continue
        expected_usd = margin * float(lock) / 100.0
        actual_usd = float(net) if pd.notna(net) else margin * float(booked) / 100.0 if pd.notna(booked) else np.nan
        slip_usd = (expected_usd - actual_usd) if pd.notna(actual_usd) else np.nan
        rows.append({
            "trade_id": str(row.get("trade_id", ""))[:8],
            "bot_version": str(row.get("bot_version", ""))[-20:],
            "margin_$": round(margin, 2),
            "peak_mfe%": round(peak, 1) if pd.notna(peak) else None,
            "lock_floor%": lock,
            "expected_at_lock_$": round(expected_usd, 2),
            "booked_margin%": round(booked, 2) if pd.notna(booked) else None,
            "actual_net_$": round(actual_usd, 2) if pd.notna(actual_usd) else None,
            "slip_vs_lock_$": round(slip_usd, 2) if pd.notna(slip_usd) else None,
        })
    if not rows:
        print(f"No PROFIT_LOCK_LADDER trades in dataset. {PIPELINE_ENFORCEMENT_TAG}")
        return
    view = pd.DataFrame(rows)
    print(view.to_string(index=False))
    print(
        f"\n  lock_floor% × margin_usdt / 100 = USD if exit exactly at floor. "
        f"actual_net_$ is what booked (often lower when price gaps through floor between ticks). "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )


def ema_hybrid_entry_report(df, trade_outcomes):
    """v84.1: EMA hybrid vs pullback execution buckets."""
    print("\n=== V84 EMA HYBRID ENTRY (EMA_HYBRID_LIMIT vs PULLBACK_LIMIT) ===")
    work = pd.DataFrame()
    if trade_outcomes is not None and not trade_outcomes.empty:
        work = trade_outcomes.copy()
    elif df is not None and not df.empty:
        work = df.copy()
    if work.empty:
        print(f"No trades yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    if "entry_mode" not in work.columns:
        work["entry_mode"] = "PULLBACK_LIMIT"
    for mode in ("EMA_HYBRID_LIMIT", "MICRO_SR_LIMIT", "PULLBACK_LIMIT"):
        subset = work[work["entry_mode"].astype(str) == mode]
        if subset.empty:
            continue
        net = pd.to_numeric(subset.get("net_pnl_usd"), errors="coerce")
        dist = pd.to_numeric(
            subset.get("dist_to_ema_hybrid_pct", subset.get("dist_to_micro_sr_pct")),
            errors="coerce",
        )
        base = pd.to_numeric(subset.get("ema_hybrid_base"), errors="coerce")
        lim = pd.to_numeric(subset.get("ema_hybrid_limit"), errors="coerce")
        print(
            f"  {mode}: n={len(subset)} | net ${net.sum():.2f} | "
            f"avg dist {dist.mean():.3f}% | avg base {base.mean():.1f} limit {lim.mean():.1f} "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
    exec_modes = work["entry_mode"].astype(str).value_counts().to_dict()
    print(f"  entry_mode distribution: {exec_modes} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def entry_health_exit_report(df):
    """v83: TYPE_A_STALL and TREND_WEAKENING exit outcomes + spread-penalty trades."""
    print("\n=== V83 ENTRY-HEALTH EXITS (TYPE_A_STALL / TREND_WEAKENING) ===")
    if df is None or df.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    for reason, label in (
        ("TYPE_A_STALL", "Type-A stall (8min, MFE<5%, underwater or weak tape)"),
        ("TREND_WEAKENING", "Trend weakening scratch (BULL/BEAR_WEAKENING)"),
    ):
        subset = df[df.get("exit_reason", pd.Series(dtype=str)) == reason] if "exit_reason" in df.columns else pd.DataFrame()
        if subset.empty:
            print(f"  {label}: 0 trades {PIPELINE_ENFORCEMENT_TAG}")
            continue
        net = pd.to_numeric(subset.get("net_pnl_usd"), errors="coerce")
        peak = pd.to_numeric(subset.get("mfe_margin_pct", subset.get("max_profit")), errors="coerce")
        booked = pd.to_numeric(subset.get("final_pnl_margin_pct", subset.get("pnl")), errors="coerce")
        print(
            f"  {label}: n={len(subset)} | net ${net.sum():.2f} | avg peak {peak.mean():.1f}% "
            f"| avg booked {booked.mean():.1f}% {PIPELINE_ENFORCEMENT_TAG}"
        )
    if "spread_penalty_mult" in df.columns:
        sp = pd.to_numeric(df["spread_penalty_mult"], errors="coerce")
        penalized = df[sp < 0.99] if sp.notna().any() else pd.DataFrame()
        if not penalized.empty:
            net = pd.to_numeric(penalized.get("net_pnl_usd"), errors="coerce")
            print(
                f"  Spread penalty trades (mult<1): n={len(penalized)} | net ${net.sum():.2f} "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )
    print(PIPELINE_ENFORCEMENT_TAG)


def first_3_candle_report(trade_outcomes):
    print("\n=== V80 FIRST-3-CANDLE BEHAVIOR ===")
    if trade_outcomes is None or trade_outcomes.empty:
        print(f"No trade_outcome.jsonl yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    winners, losers = [], []
    for _, row in trade_outcomes.iterrows():
        f3 = _normalize_first_3_candles(row.get("first_3_candles"))
        net = float(row.get("net_pnl_usd") or 0)
        bucket = "winner" if net > 0 else "loser"
        for c in ("1", "2", "3"):
            snap = f3.get(c) or f3.get(int(c))
            if not snap:
                continue
            rec = {
                "candle": int(c),
                "mfe_pct": snap.get("mfe_pct"),
                "mae_pct": snap.get("mae_pct"),
                "unreal_pct": snap.get("unreal_pct"),
                "outcome": bucket,
            }
            (winners if net > 0 else losers).append(rec)
    for label, rows in (("Winners", winners), ("Losers", losers)):
        if not rows:
            print(f"  {label}: no first-3-candle snapshots yet {PIPELINE_ENFORCEMENT_TAG}")
            continue
        rdf = pd.DataFrame(rows)
        print(f"\n  {label} (n trades with snapshots: {len(rows)}):")
        print(rdf.groupby("candle")[["mfe_pct", "mae_pct", "unreal_pct"]].mean().round(2).to_string())


def sr_direction_bucket_report(df):
    print("\n=== V80 SUPPORT/RESISTANCE × DIRECTION ===")
    if df is None or df.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = _enrich_trades_with_buckets(df)
    direction = work.get("final_direction", work.get("dir", "LONG")).astype(str).str.upper()
    work["sr_dir_bucket"] = direction + "+" + work["support_resistance_bucket"].astype(str)
    _bucket_perf_table(work, "sr_dir_bucket", "SR × Direction")


def v81_sole_ai_funnel_note(pipeline_events):
    """Brief v86 golden-stack mode summary."""
    print("\n" + "=" * 60)
    print(f"📊 V86 GOLDEN STACK GATES — {ANALYZER_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
    print("  Golden Stack (toggle): chop<=0.8 | SHORT BEAR_ALIGNED | struct<=-5 | spread 3-7")
    print("  EMA_HYBRID required | dist<=0.5% | block ADX 25-30 | block HIGH_POS funding")
    print("  Ladder 8→5 | thesis -20% | Type-A stall 8min | edge/AI thresholds dashboard-flexible")
    print("  Golden Stack OFF → sole-AI WOULD_BLOCK_* log-only for legacy gates")
    if pipeline_events is not None and not pipeline_events.empty:
        pe = pipeline_events
        gs = pe[pe.get("reason", pd.Series(dtype=str)).astype(str).str.startswith("GOLDEN_STACK", na=False)]
        would = pe[pe.get("reason", pd.Series(dtype=str)).astype(str).str.startswith("WOULD_BLOCK", na=False)]
        if not gs.empty:
            print(f"  GOLDEN_STACK blocks: {len(gs)} {PIPELINE_ENFORCEMENT_TAG}")
        if not would.empty:
            print(f"  WOULD_BLOCK events (golden off): {len(would)} {PIPELINE_ENFORCEMENT_TAG}")
    print("=" * 60 + "\n")


def _load_ai_input_log() -> pd.DataFrame:
    rows = []
    if not os.path.exists(AI_INPUT_LOG_FILE):
        return pd.DataFrame()
    with open(AI_INPUT_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ai = row.get("ai") or {}
            rm = row.get("replay_model") or {}
            upgrade = row.get("ai_input_upgrade") or {}
            if not upgrade and isinstance(row.get("context"), dict):
                upgrade = row["context"].get("ai_input_upgrade") or {}
            qc = upgrade.get("quality_score_components") or row.get("quality_score_components") or {}
            rows.append({
                "trade_id": row.get("trade_id"),
                "ts": row.get("ts"),
                "schema": row.get("schema"),
                "research_lane": row.get("research_lane"),
                "research_model": row.get("research_model"),
                "shadow_only": row.get("shadow_only"),
                "candle_bucket": row.get("candle_bucket"),
                "candle_15m_elapsed_pct": row.get("candle_15m_elapsed_pct"),
                "temperature": row.get("temperature"),
                "trigger_reason": row.get("trigger_reason"),
                "context_fingerprint": row.get("context_fingerprint"),
                "trend_health_state": upgrade.get("trend_health_state") or row.get("trend_health_state"),
                "entry_stage": upgrade.get("entry_stage") or row.get("entry_stage"),
                "reversal_risk_score": upgrade.get("reversal_risk_score") or row.get("reversal_risk_score"),
                "liquidity_sweep_high": upgrade.get("liquidity_sweep_high"),
                "liquidity_sweep_low": upgrade.get("liquidity_sweep_low"),
                "regime_change_count_60m": upgrade.get("regime_change_count_60m") or row.get("regime_change_count_60m"),
                "weaken_signals": upgrade.get("weaken_signals"),
                "quality_score": qc.get("quality_score"),
                "structure_component": qc.get("structure_component"),
                "micro_sr_component": qc.get("micro_sr_component"),
                "trend_component": qc.get("trend_component"),
                "ai_decision": ai.get("decision"),
                "ai_approved": ai.get("approved"),
                "ai_win_prob": ai.get("win_prob"),
                "ai_direction": ai.get("direction"),
                "replay_score": rm.get("replay_score"),
                "replay_approve": rm.get("replay_approve"),
                "ai_agrees": rm.get("ai_agrees"),
                "model_version": rm.get("model_version"),
            })
    return pd.DataFrame(rows)


def _lane_pnl_summary(df: pd.DataFrame, net_col: str = "net") -> None:
    if df is None or df.empty or "research_lane" not in df.columns:
        return
    work = df.copy()
    if net_col not in work.columns:
        work[net_col] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    else:
        work[net_col] = pd.to_numeric(work[net_col], errors="coerce")
    g = (
        work.groupby("research_lane", observed=True)
        .agg(
            n=("trade_id", "count"),
            executed=(net_col, lambda x: x.notna().sum()),
            wr=(net_col, lambda x: (x > 0).mean() * 100 if x.notna().any() else 0),
            sum_net=(net_col, "sum"),
            avg_net=(net_col, "mean"),
        )
        .round(2)
    )
    print(g.to_string())
    for lane in g.index:
        label = RESEARCH_LANE_LABELS.get(lane, lane)
        row = g.loc[lane]
        print(
            f"    {label} ({lane}): signals={int(row['n'])} executed={int(row['executed'])} "
            f"WR={row['wr']:.1f}% sum=${row['sum_net']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )


def research_lane_execution_report(trades, decisions, ai_log, pipeline_events, snapshots: dict, setups=None):
    """v88: per-lane live execution — CONTINUOUS / STABILITY / GOLDEN_STACK tagged PnL."""
    print("\n=== V88 RESEARCH LANE EXECUTION REPORT ===")
    print(f"  Expected bot: {EXPECTED_BOT_VERSION} | analyzer: {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG}")

    if setups is None:
        setups = robust_read_csv(SETUP_LOG_FILE, "Setups")

    frames = []
    if setups is not None and not setups.empty and "research_lane" in setups.columns:
        s = setups.copy()
        s["_source"] = "setup_log"
        frames.append(s)
        print(f"\n  Setup log rows with research_lane: {s['research_lane'].notna().sum()}/{len(s)}")
        print(s["research_lane"].value_counts(dropna=False).to_string())
    if decisions is not None and not decisions.empty and "research_lane" in decisions.columns:
        d = decisions.copy()
        d["_source"] = "decisions"
        print(f"\n  Decisions with research_lane: {d['research_lane'].notna().sum()}/{len(d)}")
        print(d["research_lane"].value_counts(dropna=False).head(8).to_string())
    if ai_log is not None and not ai_log.empty and "research_lane" in ai_log.columns:
        print(f"\n  AI tranche with research_lane: {ai_log['research_lane'].notna().sum()}/{len(ai_log)}")
        print(ai_log["research_lane"].value_counts(dropna=False).to_string())

    if trades is not None and not trades.empty:
        t = trades.copy()
        t["net"] = pd.to_numeric(t.get("net_pnl_usd"), errors="coerce")
        if "research_lane" in t.columns:
            print(f"\n  --- Executed trades by research_lane ---")
            _lane_pnl_summary(t, "net")
        else:
            print(f"  ⚠️ trades_3factor.csv has no research_lane — restart bot on {EXPECTED_BOT_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
        if "research_model" in t.columns:
            print(f"\n  --- By research_model ---")
            gm = (
                t.groupby("research_model", observed=True)
                .agg(n=("trade_id", "count"), wr=("net", lambda x: (x > 0).mean() * 100), sum=("net", "sum"))
                .round(2)
                .sort_values("sum", ascending=False)
            )
            print(gm.to_string())
    else:
        print(f"  No executed trades yet. {PIPELINE_ENFORCEMENT_TAG}")

    if snapshots:
        snap_lanes = []
        for tid, snap in snapshots.items():
            lane = snap.get("research_lane")
            if not lane:
                continue
            snap_lanes.append({
                "trade_id": tid,
                "research_lane": lane,
                "research_model": snap.get("research_model"),
                "executed": snap.get("executed"),
                "golden_stack_pass": (snap.get("golden_stack_eval") or {}).get("golden_stack_pass"),
            })
        if snap_lanes:
            sdf = pd.DataFrame(snap_lanes)
            print(f"\n  Snapshots tagged research_lane: {len(sdf)}/{len(snapshots)}")
            print(sdf["research_lane"].value_counts().to_string())
            if trades is not None and not trades.empty and "trade_id" in trades.columns:
                t = trades.copy()
                t["net"] = pd.to_numeric(t.get("net_pnl_usd"), errors="coerce")
                merged = sdf.merge(t[["trade_id", "net"]], on="trade_id", how="left")
                print(f"\n  --- Snapshot lane vs realized PnL ---")
                _lane_pnl_summary(merged, "net")

    if pipeline_events is not None and not pipeline_events.empty and "extra" in pipeline_events.columns:
        pass  # lane in extra JSON when present

    ai_in = _load_ai_input_log()
    if not ai_in.empty and "research_lane" in ai_in.columns:
        live = ai_in[ai_in.get("shadow_only").astype(str).str.lower() != "true"] if "shadow_only" in ai_in.columns else ai_in
        print(f"\n  AI input log (live lanes): {len(live)}/{len(ai_in)} rows")
        print(live["research_lane"].value_counts().to_string())

    print(PIPELINE_ENFORCEMENT_TAG)


def ai_stability_research_report(trades, snapshots: dict):
    """v87/v88: DeepSeek vs replay scorecard vs executed PnL (STABILITY lane)."""
    print("\n=== V87/V88 AI INPUT + REPLAY REPORT ===")
    df = _load_ai_input_log()
    if df.empty:
        print(f"No {AI_INPUT_LOG_FILE} yet — enable AI Stability bundle and restart bot. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(f"  AI input log rows: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
    if "research_lane" in df.columns:
        print("  By research_lane:")
        print(df["research_lane"].value_counts().to_string())
        for lane in df["research_lane"].dropna().unique():
            sub = df[df["research_lane"] == lane]
            shadow = sub["shadow_only"].astype(str).str.lower().eq("true").sum() if "shadow_only" in sub.columns else 0
            print(f"    {lane}: n={len(sub)} shadow={shadow} {PIPELINE_ENFORCEMENT_TAG}")
    if "temperature" in df.columns:
        print(f"  Temperature values: {df['temperature'].dropna().unique().tolist()} {PIPELINE_ENFORCEMENT_TAG}")
    if "trigger_reason" in df.columns:
        print(df["trigger_reason"].value_counts().head(6).to_string())
    agree = df["ai_agrees"].astype(str).str.lower().eq("true").sum() if "ai_agrees" in df.columns else 0
    print(f"  Replay model agrees with AI: {agree}/{len(df)} {PIPELINE_ENFORCEMENT_TAG}")
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        t = trades.copy()
        t["net"] = pd.to_numeric(t.get("net_pnl_usd"), errors="coerce")
        merged = df.merge(t[["trade_id", "net"]], on="trade_id", how="left")
        for label, mask in (
            ("AI+Replay APPROVE", (merged["ai_approved"] == True) & (merged["replay_approve"] == True)),
            ("AI APPROVE / Replay REJECT", (merged["ai_approved"] == True) & (merged["replay_approve"] != True)),
            ("AI REJECT / Replay APPROVE", (merged["ai_approved"] != True) & (merged["replay_approve"] == True)),
        ):
            sub = merged[mask]
            if sub.empty:
                print(f"  {label}: 0 {PIPELINE_ENFORCEMENT_TAG}")
                continue
            ex = sub["net"].notna().sum()
            wr = (sub["net"] > 0).mean() * 100 if ex else 0
            print(f"  {label}: n={len(sub)} executed={ex} WR={wr:.1f}% sum=${sub['net'].sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if snapshots:
        snap_rm = sum(1 for s in snapshots.values() if s.get("replay_model_eval"))
        print(f"  signal_snapshot replay_model_eval: {snap_rm}/{len(snapshots)} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def ai_timing_bucket_report(trades):
    """v87: PnL by 15m candle elapsed % at AI call (timing sensitivity)."""
    print("\n=== V87 AI TIMING BUCKET REPORT ===")
    df = _load_ai_input_log()
    if df.empty or "candle_15m_elapsed_pct" not in df.columns:
        print(f"No timing data in {AI_INPUT_LOG_FILE}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = df.copy()
    work["elapsed_pct"] = pd.to_numeric(work["candle_15m_elapsed_pct"], errors="coerce")
    bins = [0, 0.05, 0.15, 0.33, 0.66, 1.0]
    labels = ["0-5%", "5-15%", "15-33%", "33-66%", "66-100%"]
    work["timing_bucket"] = pd.cut(work["elapsed_pct"], bins=bins, labels=labels, include_lowest=True)
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        t = trades.copy()
        t["net"] = pd.to_numeric(t.get("net_pnl_usd"), errors="coerce")
        work = work.merge(t[["trade_id", "net"]], on="trade_id", how="left")
    g = work.groupby("timing_bucket", observed=True).agg(
        n=("trade_id", "count"),
        ai_approve=("ai_approved", lambda x: (x == True).sum()),
        executed=("net", lambda x: x.notna().sum()),
        wr=("net", lambda x: (x > 0).mean() * 100 if x.notna().any() else 0),
        sum_net=("net", "sum"),
    )
    print(g.to_string())
    print(PIPELINE_ENFORCEMENT_TAG)


def golden_stack_gate_report(snapshots: dict, trades, blocked, pipeline_events):
    """v86: golden_stack_eval pass/fail vs executed PnL."""
    print("\n=== V86 GOLDEN STACK GATE REPORT ===")
    if not snapshots:
        print(f"No signal_snapshot.jsonl yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    rows = []
    for tid, snap in snapshots.items():
        gs = snap.get("golden_stack_eval") or {}
        if not gs:
            continue
        rows.append({
            "trade_id": tid,
            "golden_stack_enabled": snap.get("golden_stack_enabled"),
            "golden_stack_pass": gs.get("golden_stack_pass"),
            "chop_pass": gs.get("chop_pass"),
            "mtf_pass": gs.get("mtf_pass"),
            "struct_pass": gs.get("struct_pass"),
            "spread_pass": gs.get("spread_pass"),
            "ema_pass": gs.get("ema_hybrid_required") and gs.get("ema_dist_pass"),
            "funding_pass": gs.get("funding_pass"),
            "executed": snap.get("executed"),
            "block_reason": snap.get("block_reason"),
            "session_utc": gs.get("session_utc"),
            "vol_bucket": gs.get("vol_bucket"),
            "sr_state": gs.get("sr_state"),
        })
    if not rows:
        print(f"No golden_stack_eval in snapshots — restart on {EXPECTED_BOT_VERSION}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    gdf = pd.DataFrame(rows)
    print(f"  Snapshots with golden_stack_eval: {len(gdf)} {PIPELINE_ENFORCEMENT_TAG}")
    if "golden_stack_pass" in gdf.columns:
        for label, subset in (
            ("PASS", gdf[gdf["golden_stack_pass"] == True]),
            ("FAIL", gdf[gdf["golden_stack_pass"] == False]),
        ):
            if subset.empty:
                print(f"  {label}: 0 {PIPELINE_ENFORCEMENT_TAG}")
                continue
            ex = subset["executed"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()
            print(f"  {label}: n={len(subset)} executed={ex} {PIPELINE_ENFORCEMENT_TAG}")
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        t = trades.copy()
        t["net"] = pd.to_numeric(t.get("net_pnl_usd"), errors="coerce")
        merged = gdf.merge(t[["trade_id", "net"]], on="trade_id", how="left")
        passed = merged[merged["golden_stack_pass"] == True]
        if not passed.empty and passed["net"].notna().any():
            wr = (passed["net"] > 0).mean() * 100
            print(f"  Golden PASS executed: n={passed['net'].notna().sum()} WR={wr:.1f}% sum=${passed['net'].sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if pipeline_events is not None and not pipeline_events.empty:
        gs_blocks = pipeline_events[
            pipeline_events.get("reason", pd.Series(dtype=str)).astype(str).str.startswith("GOLDEN_STACK", na=False)
        ]
        if not gs_blocks.empty:
            print(f"  Pipeline GOLDEN_STACK blocks: {len(gs_blocks)} {PIPELINE_ENFORCEMENT_TAG}")
            print(gs_blocks["reason"].value_counts().head(8).to_string())
    print(PIPELINE_ENFORCEMENT_TAG)


def profitable_ranges_report(trades):
    """Session profitability buckets — tracks what worked (edge/AI excluded from gates)."""
    print("\n=== V86 PROFITABLE RANGES (executed trades) ===")
    if trades is None or trades.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = _enrich_trades_with_buckets(trades.copy())
    work["net"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    work = work.dropna(subset=["net"])
    if work.empty:
        print(f"No net PnL rows. {PIPELINE_ENFORCEMENT_TAG}")
        return

    def _top_bucket(col, label, order=None):
        if col not in work.columns or work[col].isna().all():
            return
        g = (
            work.groupby(col, observed=True)
            .agg(n=("net", "count"), wr=("net", lambda x: (x > 0).mean() * 100), sum=("net", "sum"), avg=("net", "mean"))
            .round(2)
            .sort_values("sum", ascending=False)
        )
        print(f"\n  --- {label} ---")
        print(g.head(8).to_string())
        if not g.empty:
            best = g.index[0]
            print(f"  ★ Best {label}: {best} — sum ${g.iloc[0]['sum']:.2f} WR {g.iloc[0]['wr']:.1f}% {PIPELINE_ENFORCEMENT_TAG}")

    _top_bucket("edge_score_bucket", "Edge bucket", EDGE_BUCKET_ORDER)
    _top_bucket("ai_probability_bucket", "AI prob bucket")
    _top_bucket("directional_spread_bucket", "Spread bucket", SPREAD_BUCKET_ORDER)
    _top_bucket("support_resistance_bucket", "SR bucket")
    if "entry_mode" in work.columns:
        _top_bucket("entry_mode", "Entry mode")
    if "exit_reason" in work.columns:
        _top_bucket("exit_reason", "Exit reason")
    if "mfe_margin_pct" in work.columns:
        work["mfe_type"] = work["mfe_margin_pct"].apply(
            lambda m: "TYPE_A" if float(m or 0) < 10 else ("TYPE_B" if float(m or 0) >= 15 else "MIXED")
        )
        _top_bucket("mfe_type", "MFE type (TYPE_A/B)")
    print(PIPELINE_ENFORCEMENT_TAG)


def v80_research_intelligence_report(df, decisions, ai_log, trades, near_edge, pipeline_events):
    v81_sole_ai_funnel_note(pipeline_events)
    print("\n" + "=" * 60)
    print(f"📊 V80 RESEARCH INTELLIGENCE — {ANALYZER_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"MFE protect live default: {THESIS_MFE_PROTECT_DEFAULT}% | Edge collection min: {LIVE_EDGE_THRESHOLD_DEFAULT}")
    print("=" * 60)
    edge_census = _load_edge_census()
    trade_outcomes = _load_trade_outcomes_v2()
    snapshots = _load_signal_snapshots()
    n_approve = 0
    if snapshots:
        n_approve = sum(1 for s in snapshots.values() if (s.get("ai") or {}).get("decision") == "APPROVE")
    if n_approve < MIN_APPROVES_FOR_EDGE_CONCLUSIONS:
        print(
            f"⚠️ Dataset: {n_approve} APPROVE snapshots — conclusions are DIRECTIONAL until ≥{MIN_APPROVES_FOR_EDGE_CONCLUSIONS} "
            f"(edge min should be 0.5, max OFF). {PIPELINE_ENFORCEMENT_TAG}"
        )

    edge_histogram_report(near_edge, edge_census, pipeline_events, decisions)
    edge_funnel_by_bucket_report(decisions, ai_log, trades, near_edge)

    work = _enrich_trades_with_buckets(df) if df is not None and not df.empty else pd.DataFrame()
    if not work.empty:
        edge_monotonicity_report(work)
        _bucket_perf_table(work, "edge_score_bucket", "Executed trades by EDGE bucket", EDGE_BUCKET_ORDER)
        _bucket_perf_table(work, "directional_spread_bucket", "Executed trades by SPREAD bucket", SPREAD_BUCKET_ORDER)
        _bucket_perf_table(work, "support_resistance_bucket", "Executed trades by SR bucket", SR_BUCKET_ORDER)
        _bucket_perf_table(work, "ai_probability_bucket", "Executed trades by AI PROB bucket", AI_PROB_BUCKET_ORDER)
        sr_direction_bucket_report(work)
    else:
        print(f"\nNo executed trades — bucket performance tables skipped. {PIPELINE_ENFORCEMENT_TAG}")

    type_a_vs_type_b_report(work, trade_outcomes)
    ladder_booking_slip_report(work if not work.empty else df)
    entry_health_exit_report(work if not work.empty else df)
    ema_hybrid_entry_report(work if not work.empty else df, trade_outcomes)
    golden_stack_gate_report(snapshots, trades, None, pipeline_events)
    research_lane_execution_report(trades, decisions, ai_log, pipeline_events, snapshots)
    ai_stability_research_report(trades, snapshots)
    ai_timing_bucket_report(trades)
    profitable_ranges_report(trades if trades is not None and not trades.empty else work)
    first_3_candle_report(trade_outcomes)

    if snapshots:
        has_rb = sum(1 for s in snapshots.values() if s.get("research_buckets"))
        print(f"\n  signal_snapshot research_buckets coverage: {has_rb}/{len(snapshots)} {PIPELINE_ENFORCEMENT_TAG}")
    if not edge_census.empty:
        print(f"  edge_census rows: {len(edge_census)} (blocked-edge distribution) {PIPELINE_ENFORCEMENT_TAG}")
    v2_out = sum(1 for _, r in trade_outcomes.iterrows() if r.get("schema") == "trade_outcome_v2") if not trade_outcomes.empty else 0
    print(f"  trade_outcome_v2 rows: {v2_out}/{len(trade_outcomes)} {PIPELINE_ENFORCEMENT_TAG}")
    print("=" * 60 + "\n")


def run_v55_analysis(df, decisions, blocked, ai_log):
    print("\n" + "=" * 60)
    print(f"📈 PHASE A/B/C ANALYTICS — Analyzer {ANALYZER_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
    if RESEARCH_FREE_RUN_LIVE:
        print(
            "★ Bot FREE-RUN: MTF/chop/momentum-align post-AI gates off — expect higher APPROVE→execute conversion. "
            f"Flat ${FLAT_MARGIN_LIVE_USD} margin per trade (v79). "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
    print("=" * 60)
    stop_thesis_wide_sweep_all_replays(df if not df.empty else None)
    stop_ladder_2d_grid_sweep_all_replays(df if not df.empty else None)
    stop_ladder_mfe_3d_sweep_all_replays(df if not df.empty else None)
    entry_gate_replay_sweeps(blocked)
    entry_gate_mtf_chop_sweet_spot_sweep(blocked)
    pullback_replay_fill_sweep(blocked)
    gate_margin_sweet_spot_analysis(blocked)
    post_block_quality_analysis(blocked)
    regime_segment_analysis(blocked)
    margin_size_sweep_analysis(df if not df.empty else None, blocked)
    if df.empty:
        print(f"No trades for v5.5 analytics. {PIPELINE_ENFORCEMENT_TAG}")
        factor_gate_funnel(decisions, blocked, ai_log)
        return
    mfe_mae_analysis(df)
    post_signal_price_excursion(df)
    trail_ladder_forensics(df)
    pullback_optimization_analysis(df, decisions)
    thesis_fast_cut_optimization(df)
    ladder_first_rung_optimization(df)
    thesis_exit_above_optimization(df)
    exit_forensics_report(df)
    time_exit_deep_analysis(df)
    bull_bear_spread_analysis(df)
    structure_score_analysis(df)
    mtf_alignment_analysis(df)
    contradiction_analysis(df)
    funding_adx_analysis(df)
    edge_combo_analysis(df)
    factor_gate_funnel(decisions, blocked, ai_log)
    print("=" * 60 + "\n")


def build_signal_dataset(setups, decisions, ai_log, blocked, trades_len):
    print("\n=== BUILDING SIGNAL DATASET (FUNNEL MODE) ===")
    if setups.empty:
        print(f"⚠️ Setups missing — using decisions for funnel {PIPELINE_ENFORCEMENT_TAG}")
        df = decisions.copy() if not decisions.empty else pd.DataFrame()
    else:
        df = setups.copy()
    if df.empty:
        print(f"⚠️ No signal/decision rows available {PIPELINE_ENFORCEMENT_TAG}")
        return df
    if "signal_id" not in df.columns and "trade_id" in df.columns:
        df["signal_id"] = df["trade_id"]
    if "trade_id" not in df.columns and "signal_id" in df.columns:
        df["trade_id"] = df["signal_id"]
    df = safe_merge(df, ai_log, "ai_log", prefer_col="decision", prefer_values=["APPROVE", "REJECT"])
    if not blocked.empty:
        blocked_copy = blocked.copy()
        blocked_copy["blocked_flag"] = 1
        df = safe_merge(df, blocked_copy, "blocked")
    if "blocked_flag" not in df.columns:
        df["blocked_flag"] = 0
    print(f"✅ Signal funnel dataset: {len(df)} rows (not deduped — for funnel counts) {PIPELINE_ENFORCEMENT_TAG}")
    return df

def _trade_cost_series(df):
    """Trading fees, funding, and total cost (backward compatible with fees_usd-only CSVs)."""
    trading = pd.to_numeric(
        df.get("trading_fees_usd", df.get("fees_usd", pd.Series(0, index=df.index))),
        errors="coerce",
    ).fillna(0)
    funding = pd.to_numeric(
        df.get("funding_fees_usd", df.get("funding_fees", pd.Series(0, index=df.index))),
        errors="coerce",
    ).fillna(0)
    if "total_cost_usd" in df.columns:
        total = pd.to_numeric(df["total_cost_usd"], errors="coerce").fillna(0)
    else:
        total = trading + funding
    return trading, funding, total

def safe_bool_filter(df, col, value):
    """Safe boolean indexing with index alignment"""
    if col not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    series = df[col]
    if isinstance(series, pd.Series):
        mask = (series == value)
        if len(mask) != len(df):
            mask = pd.Series([False] * len(df), index=df.index)
        return mask
    return pd.Series([False] * len(df), index=df.index)

def executive_summary(trades, analysis_df, decisions, ai_log, blocked, near_edge, signal_persist, pipeline_events=None, ai_errors=None):
    verify_research_sync(trades, decisions, ai_log, blocked, near_edge)
    print("\n" + "=" * 60)
    print(f"📊 EXECUTIVE SUMMARY — Analyzer {ANALYZER_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
    print("=" * 60)
    n_trades = len(trades.drop_duplicates(subset=["trade_id"])) if not trades.empty and "trade_id" in trades.columns else len(trades)
    n_analysis = len(analysis_df)
    print(f"Unique executed trades: {n_trades} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Analysis rows (must equal trades): {n_analysis} {PIPELINE_ENFORCEMENT_TAG}")
    if n_trades != n_analysis and n_analysis > 0:
        print(f"🚨 ROW COUNT MISMATCH — analysis may still be inflated {PIPELINE_ENFORCEMENT_TAG}")

    if analysis_df.empty:
        print(f"⏳ No closed trades to score yet {PIPELINE_ENFORCEMENT_TAG}")
    else:
        net = pd.to_numeric(analysis_df.get("net_pnl_usd", 0), errors="coerce").sum()
        gross = pd.to_numeric(analysis_df.get("gross_pnl_usd", 0), errors="coerce").sum()
        trading_fees, funding_fees, total_cost = _trade_cost_series(analysis_df)
        wr = (pd.to_numeric(analysis_df.get("net_pnl_usd", 0), errors="coerce") > 0).mean() * 100
        print(f"Win rate (net): {wr:.1f}% {PIPELINE_ENFORCEMENT_TAG}")
        print(
            f"Gross PnL: ${gross:.2f} | Trading fees: ${trading_fees.sum():.2f} | "
            f"Funding: ${funding_fees.sum():.2f} | Total cost: ${total_cost.sum():.2f} | Net PnL: ${net:.2f} "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )
        if gross > 0 and net < 0:
            print(f"⚠️ COSTS EXCEED GROSS EDGE — check trading fees + funding vs gross {PIPELINE_ENFORCEMENT_TAG}")
        if trading_fees.sum() > 0.01 and "fee_profile" in analysis_df.columns:
            prof = analysis_df["fee_profile"].dropna().astype(str).unique().tolist()
            if prof and all(p == EXPECTED_FEE_PROFILE for p in prof):
                print(
                    f"⚠️ Non-zero trading fees with fee_profile={EXPECTED_FEE_PROFILE} — "
                    f"likely pre-Bitfinex-zero trades in CSV {PIPELINE_ENFORCEMENT_TAG}"
                )
        if "exit_reason" in analysis_df.columns:
            print(f"Exit mix:\n{analysis_df['exit_reason'].value_counts().to_string()} {PIPELINE_ENFORCEMENT_TAG}")

    if not decisions.empty and "reason" in decisions.columns:
        staged = normalize_decision_stages(decisions)
        edge_fail = (decisions["reason"] == "EDGE_FAIL").sum()
        approve = count_ai_approves(decisions)
        ai_reject = count_ai_rejects(decisions)
        ai_error = count_ai_errors(decisions)
        legacy_crash = count_legacy_crash_rejects(decisions)
        pre_ai = int((staged["pipeline_stage"] == "PRE_AI").sum())
        cooldown = int((staged["pipeline_stage"] == "COOLDOWN").sum())
        post_ai = int((staged["pipeline_stage"] == "POST_AI").sum())
        ai_err_stage = int((staged["pipeline_stage"] == "AI_ERROR").sum())
        eff_col = pd.to_numeric(decisions.get("effective_threshold", pd.Series()), errors="coerce")
        base_col = pd.to_numeric(decisions.get("edge_threshold", pd.Series()), errors="coerce")
        if eff_col.notna().any():
            print(f"  Effective threshold (logged): min={eff_col.min():.1f} max={eff_col.max():.1f} avg={eff_col.mean():.1f} {PIPELINE_ENFORCEMENT_TAG}")
        if base_col.notna().any() and eff_col.notna().any():
            lifted = int((eff_col > base_col).sum())
            if lifted:
                print(f"  Rows where effective > base edge threshold: {lifted} (flat-momentum penalty) {PIPELINE_ENFORCEMENT_TAG}")
        print(f"\nPipeline funnel: {PIPELINE_ENFORCEMENT_TAG}")
        print(f"  Decision rows: {len(decisions)}")
        print(f"  EDGE_FAIL (pre-AI light scan): {edge_fail} ({edge_fail/len(decisions)*100:.1f}%)")
        print(f"  PRE_AI blocks: {pre_ai} | COOLDOWN skips: {cooldown} | POST_AI blocks: {post_ai}")
        print(f"  AI model reject / post-AI (POST_AI stage): {ai_reject} | AI API errors (AI_ERROR): {ai_error}")
        if legacy_crash:
            print(f"  ⚠️ Legacy mislabeled crashes (AI_REJECT + edge=0): {legacy_crash} — ignore as model rejects {PIPELINE_ENFORCEMENT_TAG}")
        if ai_err_stage != ai_error:
            print(f"  (AI_ERROR stage rows: {ai_err_stage}) {PIPELINE_ENFORCEMENT_TAG}")
        print(f"  AI APPROVE: {approve}")
        print(f"  Executed trades: {n_trades}")
        if approve > 0:
            print(f"  APPROVE → trade conversion: {n_trades/approve*100:.1f}%")

    if not ai_log.empty:
        n_err_log = 0
        if "decision" in ai_log.columns:
            n_err_log = int((ai_log["decision"].astype(str) == "AI_ERROR").sum())
        elif "ai_error" in ai_log.columns:
            n_err_log = int(ai_log["ai_error"].apply(_truthy).sum())
        print(
            f"  AI log rows: {len(ai_log)} (unique trade_id: {ai_log['trade_id'].nunique() if 'trade_id' in ai_log.columns else 'N/A'}"
            f"{f', AI_ERROR rows: {n_err_log}' if n_err_log else ''})"
        )
    if pipeline_events is not None and not pipeline_events.empty:
        print(f"  Pipeline event rows: {len(pipeline_events)} {PIPELINE_ENFORCEMENT_TAG}")
    if ai_errors is not None and not ai_errors.empty:
        print(f"  AI error log rows: {len(ai_errors)} {PIPELINE_ENFORCEMENT_TAG}")

    zero_var = []
    for col in ["momentum", "features_velocity", "features_volume_ratio", "features_delta"]:
        if col in analysis_df.columns:
            s = pd.to_numeric(analysis_df[col], errors="coerce")
            if s.notna().any() and (s.nunique() <= 1 or s.std() < 1e-9):
                zero_var.append(col)
    if zero_var and n_trades >= 3:
        print(f"\n🚨 ZERO-VARIANCE FEATURES (bot logging issue): {zero_var} {PIPELINE_ENFORCEMENT_TAG}")
    elif zero_var:
        print(f"\nℹ️ Zero-variance on {zero_var} — expected with only {n_trades} trade(s); not a bot fault yet {PIPELINE_ENFORCEMENT_TAG}")

    if not near_edge.empty:
        print(f"\nNear-edge events logged: {len(near_edge)} (separate from trades — no trade_id) {PIPELINE_ENFORCEMENT_TAG}")
    if not signal_persist.empty:
        print(f"Signal persist rows: {len(signal_persist)} — use stage=FILL for entry snapshot {PIPELINE_ENFORCEMENT_TAG}")

    pipeline_events_analysis(pipeline_events)
    ai_errors_analysis(ai_errors, decisions, ai_log)
    print("=" * 60 + "\n")


def pipeline_events_analysis(pipeline_events):
    print("\n=== PIPELINE EVENTS (v67 gate trace) ===")
    if pipeline_events is None or pipeline_events.empty:
        print(f"No {PIPELINE_EVENTS_FILE} yet — run bot with updated logging. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = pipeline_events.copy()
    print(f"Total events: {len(work)} {PIPELINE_ENFORCEMENT_TAG}")
    if "stage" in work.columns and "outcome" in work.columns:
        print("\nBy stage → outcome (top 20):")
        print(work.groupby(["stage", "outcome"]).size().sort_values(ascending=False).head(20).to_string())
    if "reason" in work.columns:
        print("\nBy reason (top 12):")
        print(work["reason"].astype(str).value_counts().head(12).to_string())
    if "edge_score" in work.columns:
        edge = pd.to_numeric(work["edge_score"], errors="coerce")
        if edge.notna().any():
            print(f"\nEdge on events: min={edge.min():.1f} max={edge.max():.1f} avg={edge.mean():.1f} {PIPELINE_ENFORCEMENT_TAG}")
    ai_calls = work[work.get("stage", pd.Series()) == "AI"] if "stage" in work.columns else pd.DataFrame()
    if not ai_calls.empty:
        print(f"\nAI-stage events: {len(ai_calls)} {PIPELINE_ENFORCEMENT_TAG}")
        if "outcome" in ai_calls.columns:
            print(ai_calls["outcome"].value_counts().to_string())
    print(PIPELINE_ENFORCEMENT_TAG)


def ai_errors_analysis(ai_errors, decisions, ai_log):
    print("\n=== AI ERRORS (API / parse — not model reject) ===")
    err_df = pd.DataFrame()
    if ai_errors is not None and not ai_errors.empty:
        err_df = ai_errors.copy()
        print(f"From {AI_ERRORS_FILE}: {len(err_df)} rows {PIPELINE_ENFORCEMENT_TAG}")
    elif not decisions.empty:
        staged = normalize_decision_stages(decisions)
        err_df = staged[staged["pipeline_stage"] == "AI_ERROR"]
        if not err_df.empty:
            print(f"From decisions (AI_ERROR stage): {len(err_df)} rows {PIPELINE_ENFORCEMENT_TAG}")
    if err_df.empty:
        legacy = count_legacy_crash_rejects(decisions) if decisions is not None else 0
        if legacy:
            print(f"  ⚠️ {legacy} legacy crash rows in decisions (AI_REJECT + edge 0) {PIPELINE_ENFORCEMENT_TAG}")
        else:
            print(f"No AI_ERROR rows detected. {PIPELINE_ENFORCEMENT_TAG}")
        return
    if "error_type" in err_df.columns:
        print("\nBy error_type:")
        print(err_df["error_type"].astype(str).value_counts().head(10).to_string())
    if "http_status" in err_df.columns and err_df["http_status"].notna().any():
        print("\nHTTP status codes:")
        print(err_df["http_status"].value_counts().to_string())
    if "error_detail" in err_df.columns:
        sample = err_df["error_detail"].dropna().astype(str).head(3).tolist()
        for i, s in enumerate(sample, 1):
            print(f"  sample[{i}]: {s[:120]}...")
    if ai_log is not None and not ai_log.empty and "decision" in ai_log.columns:
        n = int((ai_log["decision"].astype(str) == "AI_ERROR").sum())
        if n:
            print(f"\nAI tranche log also has {n} AI_ERROR decision row(s) {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def core_metrics(df):
    print("\n=== CORE PERFORMANCE (UNIQUE TRADES) ===")
    if len(df) == 0:
        print(f"No trades yet. {PIPELINE_ENFORCEMENT_TAG}")
        return 0, 0
    pnl_series = pd.to_numeric(df.get("net_pnl_usd", pd.Series([0]*len(df))), errors='coerce')
    gross_series = pd.to_numeric(df.get("gross_pnl_usd", pnl_series), errors='coerce')
    trading_fees, funding_fees, total_cost = _trade_cost_series(df)
    fees_series = total_cost
    wins_mask = pnl_series > 0
    losses_mask = pnl_series < 0
    wins = df[wins_mask]
    losses = df[losses_mask]
    wr = len(wins) / len(df) * 100 if len(df) > 0 else 0
    avg_win = pd.to_numeric(wins.get("net_pnl_usd", pd.Series(0)), errors='coerce').mean() if len(wins) else 0
    avg_loss = pd.to_numeric(losses.get("net_pnl_usd", pd.Series(0)), errors='coerce').mean() if len(losses) else 0
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    expectancy = wr / 100 * avg_win + (1 - wr / 100) * avg_loss
    print(f"Trades: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Win Rate: {wr:.2f}% {PIPELINE_ENFORCEMENT_TAG}")
    print(f"RR: {rr:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Expectancy (net): {expectancy:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(
        f"Gross PnL total: {gross_series.sum():.2f} | Trading fees: {trading_fees.sum():.2f} | "
        f"Funding: {funding_fees.sum():.2f} | Total cost: {fees_series.sum():.2f} | Net total: {pnl_series.sum():.2f} "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    return wr, rr

def exit_analysis(df):
    print("\n=== EXIT DAMAGE ===")
    if "exit_reason" not in df.columns or "net_pnl_usd" not in df.columns:
        print("No exit_reason or net_pnl_usd column found. {PIPELINE_ENFORCEMENT_TAG}")
        return "N/A"
    stats = df.groupby("exit_reason")["net_pnl_usd"].agg(["count", "mean", "sum"]).round(2)
    print(stats)
    worst = stats["sum"].idxmin() if not stats.empty else "N/A"
    best = stats["sum"].idxmax() if not stats.empty else "N/A"
    print(f"Worst Exit: {worst} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Best Exit: {best} {PIPELINE_ENFORCEMENT_TAG}")
    return worst

def regime_matrix(df):
    print("\n=== REGIME x DIR MATRIX ===")
    if "regime" not in df.columns or "final_direction" not in df.columns or "net_pnl_usd" not in df.columns:
        print("Missing regime, final_direction or net_pnl_usd. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(df.groupby(["regime", "final_direction"])["net_pnl_usd"].agg(["mean", "count", "sum"]).round(2))

def ai_calibration(df):
    print("\n=== AI CALIBRATION ===")
    if "ai_win_prob" not in df.columns or "net_pnl_usd" not in df.columns:
        print("Missing ai_win_prob or net_pnl_usd. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["ai_bucket"] = pd.cut(pd.to_numeric(df["ai_win_prob"], errors='coerce'), bins=[0, 50, 55, 60, 65, 70, 100])
    stats = df.groupby("ai_bucket").agg(
        win_rate=("net_pnl_usd", lambda x: (pd.to_numeric(x, errors='coerce') > 0).mean() * 100 if len(x)>0 else 0),
        avg_pnl=("net_pnl_usd", lambda x: pd.to_numeric(x, errors='coerce').mean()),
        count=("net_pnl_usd", "count")
    )
    print(stats.round(2))

def edge_discovery(df):
    print("\n=== AUTO EDGE DISCOVERY ===")
    results = []
    for c in range(50, 71, 2):
        for a in range(50, 71, 2):
            s = df[(pd.to_numeric(df.get("conf", pd.Series(50)), errors='coerce') >= c) & (pd.to_numeric(df.get("ai_win_prob", pd.Series(50)), errors='coerce') >= a)]
            if len(s) < 1: continue
            pnl = pd.to_numeric(s.get("net_pnl_usd", pd.Series(0)), errors='coerce').sum()
            wr = (pd.to_numeric(s.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean() * 100 if len(s)>0 else 0
            score = pnl * (wr / 100)
            results.append((c, a, pnl, wr, len(s), score))
    if results:
        results = sorted(results, key=lambda x: x[5], reverse=True)
        print("\nTop 6 Edges: {PIPELINE_ENFORCEMENT_TAG}")
        for r in results[:6]:
            print(f"CONF≥{r[0]} AI≥{r[1]} → PnL={r[2]:.1f} WR={r[3]:.1f}% N={r[4]} Score={r[5]:.1f} {PIPELINE_ENFORCEMENT_TAG}")

def duration_analysis(df):
    print("\n=== DURATION ANALYSIS ===")
    if "dur_min" not in df.columns:
        print("No duration column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    stats = df.groupby("exit_reason")["dur_min"].mean().round(2)
    print(stats)

def outcome_distribution(df):
    print("\n=== OUTCOME DISTRIBUTION ===")
    total = len(df)
    if total == 0:
        print("No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    tp_hits = len(df[safe_bool_filter(df, "exit_reason", "TAKE_PROFIT")])
    time_exits = len(df[safe_bool_filter(df, "exit_reason", "TIME_EXIT")])
    stop_hits = len(df[safe_bool_filter(df, "exit_reason", "STOP_LOSS")])
    drawdown_hits = len(df[safe_bool_filter(df, "exit_reason", "DRAWDOWN_KILL")])
    print(f"TP Hit Rate     : {tp_hits/total*100:.2f}% ({tp_hits} trades) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"TIME_EXIT Rate  : {time_exits/total*100:.2f}% ({time_exits} trades) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"STOP_LOSS Rate  : {stop_hits/total*100:.2f}% ({stop_hits} trades) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"DRAWDOWN_KILL   : {drawdown_hits/total*100:.2f}% ({drawdown_hits} trades) {PIPELINE_ENFORCEMENT_TAG}")

def r_distribution(df):
    print("\n=== R-MULTIPLE DISTRIBUTION ===")
    if "r_multiple" not in df.columns:
        print("No r_multiple column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(df["r_multiple"].describe().round(3))

def ai_vs_duration(df):
    print("\n=== AI vs DURATION ===")
    if "ai_win_prob" not in df.columns or "dur_min" not in df.columns:
        print("Missing ai_win_prob or dur_min. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["ai_bucket"] = pd.cut(pd.to_numeric(df["ai_win_prob"], errors='coerce'), bins=[50, 60, 70, 80, 100])
    stats = df.groupby("ai_bucket")["dur_min"].mean().round(2)
    print(stats)

def entry_quality(df):
    print("\n=== ENTRY QUALITY TEST ===")
    if "dur_min" not in df.columns:
        return
    early = df[df["dur_min"] < 10].get("net_pnl_usd", pd.Series(0)).mean()
    late = df[df["dur_min"] > 60].get("net_pnl_usd", pd.Series(0)).mean()
    print(f"Early exits (<10 min) avg PnL : {early:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Late exits (>60 min) avg PnL  : {late:.2f} {PIPELINE_ENFORCEMENT_TAG}")

def entry_timing_analysis(df):
    print("\n=== ENTRY TIMING ANALYSIS (CRITICAL) ===")
    if "entry_delay" not in df.columns:
        print("No entry_delay column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["delay_bucket"] = pd.cut(df["entry_delay"], bins=[0, 5, 10, 15, 30, 60, 120])
    stats = df.groupby("delay_bucket")["net_pnl_usd"].agg(["mean", "count", "sum"]).round(2)
    print(stats)

def signal_execution_gap(df):
    print("\n=== SIGNAL → EXECUTION GAP ===")
    if "entry_delay" not in df.columns:
        return
    fast = df[df["entry_delay"] < 10].get("net_pnl_usd", pd.Series(0)).mean()
    slow = df[df["entry_delay"] > 30].get("net_pnl_usd", pd.Series(0)).mean()
    print(f"Fast entries (<10min) avg PnL : {fast:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Slow entries (>30min) avg PnL : {slow:.2f} {PIPELINE_ENFORCEMENT_TAG}")

def slippage_analysis(df):
    print("\n=== EXECUTION QUALITY (SLIPPAGE) ===")
    if "slippage" not in df.columns:
        print("No slippage column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(df["slippage"].describe().round(5))

def momentum_edge(df):
    print("\n=== MOMENTUM EDGE ===")
    if "momentum" not in df.columns:
        return
    df = df.copy()
    df["mom_bucket"] = pd.cut(pd.to_numeric(df["momentum"], errors='coerce'), bins=[-2, -1, 0, 1, 2, 5])
    stats = df.groupby("mom_bucket")["net_pnl_usd"].mean().round(2)
    print(stats)

def optimal_entry_window(df):
    print("\n=== OPTIMAL ENTRY WINDOW DETECTOR ===")
    if "entry_delay" not in df.columns:
        return
    results = []
    for d in range(0, 61, 5):
        subset = df[(df["entry_delay"] >= d) & (df["entry_delay"] < d + 5)]
        if len(subset) < 1:
            continue
        pnl = subset.get("net_pnl_usd", pd.Series(0)).mean()
        wr = (pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean()
        score = pnl * wr
        results.append((d, d + 5, pnl, wr, len(subset), score))
    if results:
        results = sorted(results, key=lambda x: x[5], reverse=True)
        for r in results[:6]:
            print(f"{r[0]:2d}–{r[1]:2d} min → PnL={r[2]:.2f} WR={r[3]*100:.1f}% N={r[4]} Score={r[5]:.2f} {PIPELINE_ENFORCEMENT_TAG}")

def blocked_analysis(blocked):
    print("\n=== BLOCKED SIGNALS ===")
    if blocked.empty:
        print("No blocked signals. {PIPELINE_ENFORCEMENT_TAG}")
        return
    if "reason" in blocked.columns:
        print(blocked["reason"].value_counts().head(10))

def multi_factor_edge(df):
    print("\n=== MULTI-FACTOR EDGE MATRIX (HIGHEST EDGE) ===")
    results = []
    for ai in [55, 60, 65]:
        for mom in [0, 0.5, 1.0]:
            for delay in [5, 10, 20]:
                subset = df[
                    (pd.to_numeric(df.get("ai_win_prob", pd.Series(50)), errors='coerce') >= ai) &
                    (pd.to_numeric(df.get("momentum", pd.Series(0)), errors='coerce') >= mom) &
                    (pd.to_numeric(df.get("entry_delay", pd.Series(999)), errors='coerce') <= delay)
                ]
                if len(subset) < 1:
                    continue
                pnl = pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce').sum()
                wr = (pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean() * 100 if len(subset)>0 else 0
                score = pnl * (wr / 100)
                results.append((ai, mom, delay, pnl, wr, len(subset), score))
    if results:
        results = sorted(results, key=lambda x: x[6], reverse=True)
        print("Top 5 Multi-Factor Combos: {PIPELINE_ENFORCEMENT_TAG}")
        for r in results[:5]:
            print(f"AI≥{r[0]} MOM≥{r[1]} DELAY≤{r[2]} → PnL={r[3]:.2f} WR={r[4]:.1f}% N={r[5]} Score={r[6]:.1f} {PIPELINE_ENFORCEMENT_TAG}")

def time_exit_diagnosis(df):
    print("\n=== TIME EXIT ROOT CAUSE ===")
    te = df[safe_bool_filter(df, "exit_reason", "TIME_EXIT")]
    if len(te) == 0:
        print("No TIME_EXIT trades yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(f"Avg momentum     : {pd.to_numeric(te.get('momentum', pd.Series(0)), errors='coerce').mean():.3f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg volatility   : {pd.to_numeric(te.get('volatility', pd.Series(0)), errors='coerce').mean():.3f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg entry delay  : {pd.to_numeric(te.get('entry_delay', pd.Series(0)), errors='coerce').mean():.1f} min {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg dist to SR   : {pd.to_numeric(te.get('distance_to_support', pd.Series(0)), errors='coerce').mean():.4f} {PIPELINE_ENFORCEMENT_TAG}")

def regime_sr_edge(df):
    print("\n=== REGIME + SR ZONE EDGE ===")
    if "distance_to_support" not in df.columns or "distance_to_resistance" not in df.columns:
        print("SR distance columns missing. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["zone"] = np.where(
        pd.to_numeric(df["distance_to_support"], errors='coerce') < 0.01, "SUPPORT",
        np.where(pd.to_numeric(df["distance_to_resistance"], errors='coerce') < 0.01, "RESISTANCE", "MID")
    )
    stats = df.groupby(["regime", "zone"])["net_pnl_usd"].agg(["mean", "count", "sum"]).round(2)
    print(stats)

def ai_mispricing(df):
    print("\n=== AI MISPRICING EDGE ===")
    if "net_pnl_usd" not in df.columns or "ai_win_prob" not in df.columns:
        print("Missing net_pnl_usd or ai_win_prob. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["actual_win"] = (pd.to_numeric(df.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).astype(int)
    df["ai_bucket"] = pd.cut(pd.to_numeric(df.get("ai_win_prob", pd.Series(50)), errors='coerce'), bins=[50, 55, 60, 65, 70, 100])
    stats = df.groupby("ai_bucket").agg(
        predicted=("ai_win_prob", lambda x: pd.to_numeric(x, errors='coerce').mean()),
        actual=("actual_win", "mean")
    )
    stats["edge"] = stats["actual"] - stats["predicted"] / 100
    print(stats.round(3))

def latency_decay(df):
    print("\n=== LATENCY DECAY CURVE ===")
    if "entry_delay" not in df.columns:
        return
    df = df.copy()
    df["delay_bucket"] = pd.cut(df["entry_delay"], bins=[0, 5, 10, 20, 40, 60])
    stats = df.groupby("delay_bucket")["net_pnl_usd"].agg(["mean", "count"]).round(2)
    print(stats)

def edge_score_analysis(df):
    print("\n=== EDGE SCORE ANALYSIS ===")
    if "edge_score" not in df.columns or "net_pnl_usd" not in df.columns:
        print("No edge_score or net_pnl_usd column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["edge_bucket"] = pd.cut(pd.to_numeric(df["edge_score"], errors='coerce'), bins=[0, 1, 1.5, 2, 2.5, 3, 5])
    stats = df.groupby("edge_bucket")["net_pnl_usd"].agg(["mean", "count", "sum"]).round(2)
    print(stats)

def blocked_vs_taken(df):
    print("\n=== BLOCKED vs TAKEN EDGE ===")
    df = df.copy()
    if "blocked_flag" not in df.columns:
        print("   Forced blocked_flag column for safety {PIPELINE_ENFORCEMENT_TAG}")
        df["blocked_flag"] = 0
    mask = df["blocked_flag"] == 1
    taken = df[~mask].copy()
    blocked = df[mask].copy()
    print(f"Taken Trades : {len(taken)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Blocked Signals : {len(blocked)} {PIPELINE_ENFORCEMENT_TAG}")
    if len(taken) > 0 and "net_pnl_usd" in taken.columns:
        print(f"Taken Avg PnL : {pd.to_numeric(taken['net_pnl_usd'], errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if len(blocked) > 0 and "reason" in blocked.columns:
        print("Top Block Reasons: {PIPELINE_ENFORCEMENT_TAG}")
        print(blocked["reason"].value_counts().head(10))

def ai_decision_quality(df):
    print("\n=== AI DECISION QUALITY ===")
    if "ai_decision" not in df.columns:
        print("No ai_decision column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    approved = df[safe_bool_filter(df, "ai_decision", "APPROVE")]
    rejected = df[safe_bool_filter(df, "ai_decision", "REJECT")]
    if len(approved) > 0 and "net_pnl_usd" in approved.columns:
        print(f"Approved Avg PnL : {pd.to_numeric(approved['net_pnl_usd'], errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if len(rejected) > 0 and "net_pnl_usd" in rejected.columns:
        print(f"Rejected Avg PnL (missed edge) : {pd.to_numeric(rejected['net_pnl_usd'], errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")

def pre_ai_gate_analysis(decisions):
    print("\n=== PRE-AI GATE BREAKDOWN ===")
    if decisions.empty or "reason" not in decisions.columns:
        print(f"No decision reasons logged. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = normalize_decision_stages(decisions)
    pre_ai = work[work["pipeline_stage"] == "PRE_AI"]
    if pre_ai.empty:
        print(f"No PRE_AI stage rows yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    reasons = pre_ai["reason"].astype(str)
    grouped = reasons.str.replace(r"PRE_AI_EDGE_LOW_\d+\.?\d*", "PRE_AI_EDGE_LOW", regex=True)
    grouped = grouped.str.replace(r"PRE_AI_LOW_ADX_[\d.]+", "PRE_AI_LOW_ADX", regex=True)
    print(grouped.value_counts().head(12).to_string())
    edge = pd.to_numeric(pre_ai.get("edge_score", pd.Series()), errors="coerce")
    eff = pd.to_numeric(pre_ai.get("effective_threshold", pd.Series()), errors="coerce")
    base = pd.to_numeric(pre_ai.get("edge_threshold", pd.Series()), errors="coerce")
    near = pre_ai[(edge.notna()) & (eff.notna()) & (edge >= eff - 0.5) & (edge < eff)]
    if not near.empty:
        print(f"\nNear-miss PRE_AI (edge within 0.5 of effective threshold): {len(near)}/{len(pre_ai)} {PIPELINE_ENFORCEMENT_TAG}")
    if base.notna().any() and eff.notna().any():
        penalty = pre_ai[(eff > base)]
        if not penalty.empty:
            print(f"PRE_AI rows with flat-momentum lift (eff > base): {len(penalty)}/{len(pre_ai)} {PIPELINE_ENFORCEMENT_TAG}")


def pipeline_funnel_staged(decisions, blocked, trades):
    print("\n=== PIPELINE FUNNEL BY STAGE (v6.9+) ===")
    if decisions.empty:
        print(f"No decisions logged. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = normalize_decision_stages(decisions)
    if "effective_threshold" not in work.columns:
        work["effective_threshold"] = np.nan
    print(f"Total decision rows: {len(work)} {PIPELINE_ENFORCEMENT_TAG}")
    print("\nBy pipeline_stage (normalized):")
    print(work["pipeline_stage"].value_counts().head(20).to_string())
    if "reason" in work.columns:
        print("\nBy reason (top 15):")
        print(work["reason"].value_counts().head(15).to_string())
    n_approve = count_ai_approves(work)
    blocked_after = work[work["pipeline_stage"] == "POST_AI"]
    api_errors = work[work["pipeline_stage"] == "AI_ERROR"]
    n_trades = len(trades.drop_duplicates(subset=["trade_id"])) if not trades.empty and "trade_id" in trades.columns else len(trades)
    print(f"\nAI APPROVE rows (reason=APPROVE): {n_approve} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Post-AI BLOCKED (model/gates): {len(blocked_after)} | AI_ERROR (API): {len(api_errors)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Executed trades: {n_trades} {PIPELINE_ENFORCEMENT_TAG}")
    if n_approve > 0:
        print(f"APPROVE → trade conversion: {n_trades / n_approve * 100:.1f}% {PIPELINE_ENFORCEMENT_TAG}")
    eff = pd.to_numeric(work.get("effective_threshold", pd.Series()), errors="coerce")
    edge = pd.to_numeric(work.get("edge_score", pd.Series()), errors="coerce")
    edge_fail = work[work.get("reason", pd.Series()) == "EDGE_FAIL"] if "reason" in work.columns else pd.DataFrame()
    if not edge_fail.empty and eff.notna().any():
        ef = edge_fail.copy()
        ef["edge_num"] = pd.to_numeric(ef["edge_score"], errors="coerce")
        ef["eff_num"] = pd.to_numeric(ef.get("effective_threshold", np.nan), errors="coerce")
        below_eff = ef[ef["edge_num"] < ef["eff_num"]]
        print(f"\nEDGE_FAIL with edge < effective_threshold: {len(below_eff)}/{len(ef)} {PIPELINE_ENFORCEMENT_TAG}")
        if len(below_eff) > 0:
            print(f"  Avg edge on fail: {below_eff['edge_num'].mean():.2f} | Avg eff: {below_eff['eff_num'].mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if not blocked.empty and "reason" in blocked.columns:
        print("\nBlocked signals (post-AI, from blocked_signals CSV):")
        print(blocked["reason"].value_counts().head(10).to_string())
    pre_ai_gate_analysis(decisions)
    print(PIPELINE_ENFORCEMENT_TAG)


def approve_outcome_analysis(decisions, blocked, trades):
    print("\n=== APPROVE OUTCOME ANALYSIS ===")
    if decisions.empty:
        return
    ai_approves = decisions[decisions.get("reason", pd.Series()) == "APPROVE"]
    if ai_approves.empty:
        print(f"No AI APPROVE rows in decisions CSV yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    staged = normalize_decision_stages(decisions)
    trade_ids = set(trades["trade_id"].dropna()) if not trades.empty and "trade_id" in trades.columns else set()
    rows = []
    for tid in ai_approves["trade_id"].dropna().unique():
        sub = staged[staged["trade_id"] == tid]
        blocked_row = sub[sub["pipeline_stage"] == "POST_AI"]
        if tid in trade_ids:
            outcome = "EXECUTED"
            reason = "TRADE_LOGGED"
        elif not blocked_row.empty:
            outcome = "BLOCKED"
            reason = str(blocked_row.iloc[-1].get("reason", "?"))
        else:
            outcome = "UNKNOWN"
            reason = "-"
        edge = pd.to_numeric(sub.get("edge_score", pd.Series()), errors="coerce").max()
        eff = pd.to_numeric(sub.get("effective_threshold", pd.Series()), errors="coerce").max()
        rows.append({"trade_id": tid, "outcome": outcome, "reason": reason, "edge": edge, "effective_threshold": eff})
    if not rows:
        return
    df = pd.DataFrame(rows)
    print(df.groupby(["outcome", "reason"]).size().reset_index(name="count").to_string(index=False))
    print(f"\nSummary: {len(df[df.outcome=='EXECUTED'])} executed / {len(df[df.outcome=='BLOCKED'])} blocked / {len(df)} APPROVE attempts {PIPELINE_ENFORCEMENT_TAG}")


def pipeline_funnel(setups, decisions, trades):
    print("\n=== PIPELINE FUNNEL ===")
    print(f"Setups detected     : {len(setups)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"AI Decisions logged : {len(decisions)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Executed Trades     : {len(trades)} {PIPELINE_ENFORCEMENT_TAG}")

def pipeline_leak_analysis(df):
    print("\n=== PIPELINE LEAK ANALYSIS ===")
    if "reason" not in df.columns:
        print("No reason column for leaks. {PIPELINE_ENFORCEMENT_TAG}")
        return
    leaks = df[df.get("blocked_flag", pd.Series(0)) == 1]
    print("Top Pipeline Leak Reasons: {PIPELINE_ENFORCEMENT_TAG}")
    print(leaks["reason"].value_counts().head(10))

def fee_impact(df):
    print("\n=== FEE & FUNDING IMPACT (Bitfinex research sim) ===")
    if not any(c in df.columns for c in ("fees_usd", "trading_fees_usd", "funding_fees_usd", "funding_fees")):
        print(f"No fee columns found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    trading, funding, total = _trade_cost_series(df)
    print(f"Total trading fees : ${trading.sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Total funding cost : ${funding.sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Total all-in cost  : ${total.sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if len(df) > 0:
        print(f"Avg trading fee/trade : ${trading.mean():.4f} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Avg funding/trade     : ${funding.mean():.4f} {PIPELINE_ENFORCEMENT_TAG}")
    if "fee_profile" in df.columns:
        print(f"fee_profile mix:\n{df['fee_profile'].value_counts().to_string()} {PIPELINE_ENFORCEMENT_TAG}")
    if "funding_rate_pct_8h_at_entry" in df.columns:
        fr = pd.to_numeric(df["funding_rate_pct_8h_at_entry"], errors="coerce")
        if fr.notna().any():
            print(
                f"Avg funding rate at entry (%/8h): {fr.mean():.5f} | "
                f"median: {fr.median():.5f} {PIPELINE_ENFORCEMENT_TAG}"
            )

def data_validation(df, raw_trade_count=None):
    print("\n=== DATA VALIDATION ===")
    missing_ids = df["trade_id"].isna().sum() if "trade_id" in df.columns else 0
    duplicates = df["trade_id"].duplicated().sum() if "trade_id" in df.columns else 0
    print(f"Missing trade_id : {missing_ids} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Duplicate trade_id : {duplicates} {PIPELINE_ENFORCEMENT_TAG}")
    if raw_trade_count is not None:
        print(f"Raw trades CSV rows: {raw_trade_count} | Analysis rows: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
        if duplicates > 0:
            print(f"🚨 FIX REQUIRED: duplicates detected — v61 would inflate stats by ~{len(df)/max(1,raw_trade_count):.1f}x {PIPELINE_ENFORCEMENT_TAG}")
        elif len(df) == raw_trade_count:
            print(f"✅ Row count matches unique trades {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Master dataset shape : {df.shape} {PIPELINE_ENFORCEMENT_TAG}")

def feature_impact_analysis(df):
    print("\n=== FEATURE IMPACT ANALYSIS ===")
    features = [
        "delta", "delta_change", "imbalance",
        "velocity", "volume", "volume_ratio",
        "ema_slope", "edge_score", "final_direction"
    ]
    for f in features:
        if f not in df.columns:
            continue
        df_copy = df.copy()
        numeric_col = pd.to_numeric(df_copy[f], errors='coerce')
        df_copy[f + "_bucket"] = pd.qcut(numeric_col, q=5, duplicates="drop")
        stats = df_copy.groupby(f + "_bucket")["net_pnl_usd"].agg(["mean", "count", "sum"])
        stats["win_rate"] = df_copy.groupby(f + "_bucket")["net_pnl_usd"].apply(lambda x: (pd.to_numeric(x, errors='coerce')>0).mean()*100 if len(x)>0 else 0)
        print(f"\n{f} impact: {PIPELINE_ENFORCEMENT_TAG}")
        print(stats.round(2))

def winner_vs_loser_profile(df):
    print("\n=== WINNER vs LOSER PROFILE ===")
    df_copy = df.copy()
    wins = df_copy[pd.to_numeric(df_copy.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0]
    losses = df_copy[pd.to_numeric(df_copy.get("net_pnl_usd", pd.Series(0)), errors='coerce') < 0]
    cols = ["momentum", "volume", "velocity", "delta", "ema_slope", "edge_score"]
    for c in cols:
        if c not in df_copy.columns:
            continue
        print(f"\n{c}: {PIPELINE_ENFORCEMENT_TAG}")
        print(f"  Winners: {pd.to_numeric(wins[c], errors='coerce').mean():.4f} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"  Losers : {pd.to_numeric(losses[c], errors='coerce').mean():.4f} {PIPELINE_ENFORCEMENT_TAG}")

def edge_threshold_effectiveness(df):
    print("\n=== EDGE THRESHOLD EFFECTIVENESS ===")
    thr_col = "effective_threshold" if "effective_threshold" in df.columns and df["effective_threshold"].notna().any() else "edge_threshold"
    if thr_col not in df.columns:
        print(f"No edge_threshold / effective_threshold column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    stats = df.groupby(thr_col)["net_pnl_usd"].agg(["mean","count","sum"])
    print(f"Grouped by {thr_col}:")
    print(stats.round(2))

def ai_cooldown_effect(df):
    print("\n=== AI COOLDOWN EFFECT ===")
    if "time_since_last_ai" not in df.columns:
        print("No time_since_last_ai column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df_copy = df.copy()
    df_copy["cooldown_bucket"] = pd.cut(df_copy["time_since_last_ai"], bins=[0,300,600,1800])
    stats = df_copy.groupby("cooldown_bucket")["net_pnl_usd"].mean()
    print(stats.round(2))

def conversion_rate(setups, trades):
    print("\n=== SIGNAL → TRADE CONVERSION ===")
    total_signals = len(setups)
    total_trades = len(trades)
    if total_signals == 0:
        print("No setups data. {PIPELINE_ENFORCEMENT_TAG}")
        return
    rate = total_trades / total_signals * 100
    print(f"Conversion Rate: {rate:.2f}% {PIPELINE_ENFORCEMENT_TAG}")

def tp_sl_efficiency(df):
    print("\n=== TP / SL EFFICIENCY ===")
    tp = df[df.get("exit_reason", pd.Series([])) == "TAKE_PROFIT"].get("net_pnl_usd", pd.Series(0)).mean()
    sl = df[df.get("exit_reason", pd.Series([])) == "STOP_LOSS"].get("net_pnl_usd", pd.Series(0)).mean()
    print(f"Avg TP PnL: {tp:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg SL PnL: {sl:.2f} {PIPELINE_ENFORCEMENT_TAG}")

def sequence_analysis(df):
    print("\n=== TRADE SEQUENCE ANALYSIS ===")
    if "ts" not in df.columns:
        print("No timestamp column for sequence analysis. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df_copy = df.copy()
    df_copy["ts"] = pd.to_datetime(df_copy["ts"], errors="coerce")
    df_copy = df_copy.sort_values("ts")
    df_copy["win"] = (pd.to_numeric(df_copy.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).astype(int)
    streak = 0
    max_win = 0
    for w in df_copy["win"]:
        if w == 1:
            streak += 1
            max_win = max(max_win, streak)
        else:
            streak = 0
    print(f"Max win streak: {max_win} {PIPELINE_ENFORCEMENT_TAG}")

def pipeline_consistency(signal_df, trades):
    print("\n=== PIPELINE CONSISTENCY ===")
    if signal_df.empty:
        return
    missing_ai = signal_df.get("ai_decision", pd.Series([])).isna().sum()
    print(f"Missing AI decisions: {missing_ai} {PIPELINE_ENFORCEMENT_TAG}")
    if "signal_id" in trades.columns:
        missing_signal = trades["signal_id"].isna().sum()
        print(f"Trades without signal_id: {missing_signal} {PIPELINE_ENFORCEMENT_TAG}")

def feature_interaction(df):
    print("\n=== FEATURE INTERACTION EDGE ===")
    df_copy = df.copy()
    for mom in [0.3, 0.5, 1.0]:
        for vol in [1.2, 1.5, 2.0]:
            try:
                if "momentum" not in df_copy.columns or "volume_ratio" not in df_copy.columns:
                    continue
                subset = df_copy[
                    (pd.to_numeric(df_copy["momentum"], errors='coerce') > mom) &
                    (pd.to_numeric(df_copy["volume_ratio"], errors='coerce') > vol)
                ]
                if len(subset) < 1:
                    continue
                wr = (pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean()*100 if "net_pnl_usd" in subset.columns else 0
                pnl = pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce').mean()
                print(f"MOM>{mom} & VOL>{vol} → WR={wr:.2f}% | PnL={pnl:.2f} (n={len(subset)}) {PIPELINE_ENFORCEMENT_TAG}")
            except Exception as e:
                print(f"Interaction skipped for MOM>{mom} VOL>{vol}: {e} {PIPELINE_ENFORCEMENT_TAG}")

def signal_outcome_analysis(signal_df):
    print("\n=== SIGNAL OUTCOME ANALYSIS (FUNNEL) ===")
    if signal_df.empty:
        return
    total = len(signal_df)
    if "blocked_flag" not in signal_df.columns:
        signal_df = signal_df.copy()
        signal_df["blocked_flag"] = 0
    blocked = int((signal_df["blocked_flag"] == 1).sum())
    approved = 0
    if "reason" in signal_df.columns:
        approved = int((signal_df["reason"] == "APPROVE").sum())
    elif "ai_decision" in signal_df.columns:
        approved = int(safe_bool_filter(signal_df, "ai_decision", "APPROVE").sum())
    edge_fail = int((signal_df.get("reason", pd.Series()) == "EDGE_FAIL").sum()) if "reason" in signal_df.columns else 0
    print(f"Decision rows   : {total} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"EDGE_FAIL       : {edge_fail} ({edge_fail/total*100:.2f}%) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Blocked         : {blocked} ({blocked/total*100:.2f}%) {PIPELINE_ENFORCEMENT_TAG}")
    print(f"AI APPROVE rows : {approved} ({approved/total*100:.2f}%) {PIPELINE_ENFORCEMENT_TAG}")

def feature_distribution(df):
    print("\n=== FEATURE DISTRIBUTION ===")
    cols = ["ema_slope", "momentum", "volume_ratio", "edge_score", "final_direction"]
    for c in cols:
        if c in df.columns:
            print(f"\n{c}: {PIPELINE_ENFORCEMENT_TAG}")
            if pd.api.types.is_numeric_dtype(df[c]):
                print(df[c].describe().round(4))
            else:
                print(df[c].value_counts().head(10))

def missed_opportunity_analysis(signal_df, shadow_df=None):
    print("\n=== MISSED OPPORTUNITY ANALYSIS ===")
    if shadow_df is not None and not shadow_df.empty and "outcome_lane" in shadow_df.columns:
        bs = shadow_df[shadow_df["outcome_lane"] == "BLOCKED_SHADOW"].copy()
        bs["net_pnl_usd"] = pd.to_numeric(bs.get("net_pnl_usd"), errors="coerce")
        missed = bs[bs["net_pnl_usd"] > 0]
        if len(missed) == 0:
            print(f"No missed profitable blocked APPROVEs in shadow data yet. {PIPELINE_ENFORCEMENT_TAG}")
            return
        print(f"Missed Winners (shadow): {len(missed)} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Avg Missed Shadow PnL: ${missed['net_pnl_usd'].mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
        if "block_reason" in missed.columns:
            print(f"Top Block Reasons:\n{missed['block_reason'].value_counts().head(5).to_string()} {PIPELINE_ENFORCEMENT_TAG}")
        return
    if signal_df.empty or "net_pnl_usd" not in signal_df.columns:
        print(f"No signal-level PnL data — use shadow_outcome.jsonl (v72 bot). {PIPELINE_ENFORCEMENT_TAG}")
        return
    if "blocked_flag" not in signal_df.columns:
        signal_df["blocked_flag"] = 0
    missed = signal_df[
        (signal_df["blocked_flag"] == 1) &
        (pd.to_numeric(signal_df["net_pnl_usd"], errors='coerce') > 0)
    ]
    if len(missed) == 0:
        print(f"No missed profitable signals yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(f"Missed Winners: {len(missed)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg Missed PnL: {pd.to_numeric(missed['net_pnl_usd'], errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Top Block Reasons: {PIPELINE_ENFORCEMENT_TAG}")
    print(missed["reason"].value_counts().head(5))

def feature_completeness_check(df):
    print("\n=== FEATURE COMPLETENESS ===")
    features = ["momentum", "volume_ratio", "delta", "ema_slope", "edge_score", "final_direction"]
    for f in features:
        if f in df.columns:
            missing = df[f].isna().sum()
            print(f"{f}: missing {missing} ({missing/len(df)*100:.2f}%) {PIPELINE_ENFORCEMENT_TAG}")

def conditional_edge(df, feature, threshold):
    print(f"\n=== CONDITIONAL EDGE: {feature} > {threshold} ===")
    if feature not in df.columns:
        print(f"Feature {feature} not found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    subset = df[pd.to_numeric(df[feature], errors='coerce') > threshold]
    if len(subset) < 1:
        print("Insufficient samples for conditional edge. {PIPELINE_ENFORCEMENT_TAG}")
        return
    wr = (pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean()
    pnl = pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce').mean()
    print(f"{feature}>{threshold} → WR={wr:.2f} | PnL={pnl:.2f} (n={len(subset)}) {PIPELINE_ENFORCEMENT_TAG}")

def interaction_matrix(df):
    print("\n=== FEATURE INTERACTION MATRIX ===")
    features = ["momentum", "volume_ratio", "delta", "imbalance", "edge_score"]
    for f1 in features:
        for f2 in features:
            if f1 == f2: continue
            if f1 not in df.columns or f2 not in df.columns:
                continue
            subset = df[(pd.to_numeric(df[f1], errors='coerce') > pd.to_numeric(df[f1], errors='coerce').median()) & (pd.to_numeric(df[f2], errors='coerce') > pd.to_numeric(df[f2], errors='coerce').median())]
            if len(subset) < 1: continue
            wr = (pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean()
            print(f"{f1}+{f2} → WR={wr:.2f} N={len(subset)} {PIPELINE_ENFORCEMENT_TAG}")

def latency_curve(df):
    print("\n=== LATENCY CURVE ===")
    if "entry_delay" not in df.columns:
        print("No entry_delay column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["lat_bin"] = pd.cut(df["entry_delay"], bins=[0,5,10,20,40,60])
    stats = df.groupby("lat_bin")["net_pnl_usd"].mean().round(2)
    print(stats)

def signal_loss(df):
    print("\n=== SIGNAL LOSS ANALYSIS ===")
    total = len(df)
    if total == 0:
        return
    valid = df["ai_decision"].notna().sum()
    print(f"AI coverage: {valid}/{total} ({valid/total*100:.2f}%) {PIPELINE_ENFORCEMENT_TAG}")

def time_edge(df):
    print("\n=== TIME-BASED EDGE ===")
    if "ts" not in df.columns:
        print("No ts column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df["hour"] = df["ts"].dt.hour
    df["day"] = df["ts"].dt.dayofweek
    print("Hourly edge: {PIPELINE_ENFORCEMENT_TAG}")
    print(df.groupby("hour")["net_pnl_usd"].mean().round(2))
    print("Day-of-week edge: {PIPELINE_ENFORCEMENT_TAG}")
    print(df.groupby("day")["net_pnl_usd"].mean().round(2))

def distribution_shape(df):
    print("\n=== DISTRIBUTION SHAPE ===")
    if "net_pnl_usd" not in df.columns:
        return
    print(f"Skew: {df['net_pnl_usd'].skew():.3f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Kurtosis: {df['net_pnl_usd'].kurt():.3f} {PIPELINE_ENFORCEMENT_TAG}")

def markov_edge(df):
    print("\n=== MARKOV EDGE (WIN CLUSTERING) ===")
    if len(df) < 2:
        return
    df = df.copy()
    df = df.sort_values("ts")
    df["prev_win"] = (pd.to_numeric(df.get("net_pnl_usd", pd.Series(0)), errors='coerce').shift(1) > 0)
    grouped = df.groupby("prev_win")["net_pnl_usd"].mean().round(2)
    print(grouped)

def rolling_edge(df):
    print("\n=== ROLLING EDGE STABILITY ===")
    if "ts" not in df.columns or len(df) < 5:
        print("Insufficient data for rolling analysis. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df = df.sort_values("ts")
    df["rolling_wr"] = pd.to_numeric(df.get("net_pnl_usd", pd.Series(0)), errors='coerce').rolling(5).apply(lambda x: (x>0).mean(), raw=True)
    print("Recent rolling win rates (last 5): {PIPELINE_ENFORCEMENT_TAG}")
    print(df["rolling_wr"].tail(5).round(3))

def validate_edge(subset):
    print("\n=== EDGE VALIDATION ===")
    n = len(subset)
    if n < 5:
        print("UNRELIABLE (n < 5) {PIPELINE_ENFORCEMENT_TAG}")
        return
    print(f"OK (n={n}) {PIPELINE_ENFORCEMENT_TAG}")

def invert_signal_impact(df):
    print("\n=== INVERT SIGNAL IMPACT ANALYSIS ===")
    if "inverted" not in df.columns and "invert_signal" not in df.columns:
        print("No invert column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = df.copy()
    df["inverted_flag"] = df.get("inverted", df.get("invert_signal", False))
    inverted = df[df["inverted_flag"] == True]
    normal = df[df["inverted_flag"] == False]
    print(f"Inverted trades: {len(inverted)} Avg PnL: {pd.to_numeric(inverted.get('net_pnl_usd', pd.Series(0)), errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Normal trades: {len(normal)} Avg PnL: {pd.to_numeric(normal.get('net_pnl_usd', pd.Series(0)), errors='coerce').mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")

def direction_consistency(df):
    print("\n=== DIRECTION CONSISTENCY CHECK ===")
    if "final_direction" not in df.columns or "ai_direction_raw" not in df.columns:
        print("Direction columns missing. {PIPELINE_ENFORCEMENT_TAG}")
        return
    consistent = (df["final_direction"] == df["ai_direction_raw"]).mean() * 100
    print(f"Direction consistency after inversion: {consistent:.2f}% {PIPELINE_ENFORCEMENT_TAG}")

def pipeline_health_dashboard(df):
    print("\n=== PIPELINE HEALTH DASHBOARD SNAPSHOT ===")
    print(f"Total Master Rows: {len(df)} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Data Quality Score: {100 - (df.isna().sum().sum() / (df.shape[0]*df.shape[1]) * 100 if df.shape[0]>0 else 0):.2f}% {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Edge Threshold Distribution: {df.get('edge_threshold', pd.Series()).value_counts().to_dict()} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"AI Threshold Distribution: {df.get('ai_threshold', pd.Series()).value_counts().to_dict()} {PIPELINE_ENFORCEMENT_TAG}")

def generate_candidate_rules(df):
    print(f"🔧 GENERATING CANDIDATE RULES FOR AUTO EXTRACTOR... {PIPELINE_ENFORCEMENT_TAG}")
    rules = []
    for edge in [1.8, 2.0, 2.2, 2.5, 2.8, 3.0]:
        for ai in [55, 58, 60, 62, 65]:
            for delay in [5, 8, 10, 12, 15, 20]:
                for mom in [0.2, 0.3, 0.4, 0.45, 0.5, 0.6]:
                    rules.append({
                        "edge": edge,
                        "ai": ai,
                        "delay": delay,
                        "momentum": mom
                    })
    print(f"Generated {len(rules)} candidate rules {PIPELINE_ENFORCEMENT_TAG}")
    return rules

def evaluate_rule(df, rule):
    subset = df[
        (pd.to_numeric(df.get("edge_score", pd.Series(0)), errors='coerce') >= rule["edge"]) &
        (pd.to_numeric(df.get("ai_win_prob", pd.Series(50)), errors='coerce') >= rule["ai"]) &
        (pd.to_numeric(df.get("entry_delay", pd.Series(999)), errors='coerce') <= rule["delay"]) &
        (pd.to_numeric(df.get("momentum", pd.Series(0)), errors='coerce') >= rule["momentum"])
    ]
    if len(subset) < MIN_TRADES_FOR_RULES:
        return None
    pnl = pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce').sum()
    wr = (pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce') > 0).mean()
    avg = pd.to_numeric(subset.get("net_pnl_usd", pd.Series(0)), errors='coerce').mean()
    if wr < 0.55:
        return None
    score = pnl * wr
    return {
        "rule": rule,
        "pnl": pnl,
        "wr": wr,
        "avg": avg,
        "count": len(subset),
        "score": score
    }

def find_best_rules(df):
    print(f"🔍 RANKING RULES (min {MIN_TRADES_FOR_RULES} trades per rule)... {PIPELINE_ENFORCEMENT_TAG}")
    rules = generate_candidate_rules(df)
    results = []
    for r in rules:
        res = evaluate_rule(df, r)
        if res:
            results.append(res)
    results = sorted(results, key=lambda x: x["score"], reverse=True)
    print(f"Found {len(results)} valid rules after filtering {PIPELINE_ENFORCEMENT_TAG}")
    return results[:10]

def build_strategy(best_rules):
    print(f"🏗️ BUILDING FINAL STRATEGY CONFIG... {PIPELINE_ENFORCEMENT_TAG}")
    if not best_rules:
        print(f"⚠️ No valid rules found (need ≥{MIN_TRADES_FOR_RULES} trades + WR≥55%) {PIPELINE_ENFORCEMENT_TAG}")
        return None
    top = best_rules[0]["rule"]
    strategy = {
        "edge_threshold": top["edge"],
        "ai_threshold": top["ai"],
        "entry_delay_max": top["delay"],
        "momentum_min": top["momentum"],
        "expected_wr": best_rules[0]["wr"],
        "expected_pnl": best_rules[0]["pnl"],
        "trade_count": best_rules[0]["count"]
    }
    return strategy

def auto_strategy_extractor(df):
    print(f"\n🔥 AUTO STRATEGY EXTRACTION ENGINE 🔥 {PIPELINE_ENFORCEMENT_TAG}")
    best = find_best_rules(df)
    print(f"\nTop 5 Strategies (need ≥{MIN_TRADES_FOR_RULES} trades each): {PIPELINE_ENFORCEMENT_TAG}")
    for b in best[:5]:
        r = b["rule"]
        print(f"EDGE≥{r['edge']} AI≥{r['ai']} DELAY≤{r['delay']} MOM≥{r['momentum']} → Score={b['score']:.2f} PnL={b['pnl']:.2f} WR={b['wr']*100:.1f}% N={b['count']} {PIPELINE_ENFORCEMENT_TAG}")
    strategy = build_strategy(best)
    if strategy:
        print(f"\n🎯 FINAL STRATEGY CONFIG: {PIPELINE_ENFORCEMENT_TAG}")
        print(json.dumps(strategy, indent=2))
    return strategy

def run():
    iteration = 0
    while True:
        iteration += 1
        print(f"\n=== ANALYZER {ANALYZER_VERSION} ITERATION {iteration} START {PIPELINE_ENFORCEMENT_TAG} ===")
        trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors = load_data()
        raw_trade_count = len(trades.drop_duplicates(subset=["trade_id"])) if not trades.empty and "trade_id" in trades.columns else len(trades)

        master = build_master_dataset(trades, blocked, decisions, ai_log, signal_persist, near_edge)
        signal_master = build_signal_dataset(setups, decisions, ai_log, blocked, raw_trade_count)

        analysis_df = master if not master.empty else pd.DataFrame()
        executive_summary(trades, analysis_df, decisions, ai_log, blocked, near_edge, signal_persist, pipeline_events, ai_errors)
        research_data_coverage_audit(trades, decisions, blocked, pipeline_events)
        v80_research_intelligence_report(analysis_df, decisions, ai_log, trades, near_edge, pipeline_events)

        if analysis_df.empty:
            print(f"⏳ No executed trades — funnel-only mode {PIPELINE_ENFORCEMENT_TAG}")
            pipeline_funnel(setups, decisions, trades)
            pipeline_funnel_staged(decisions, blocked, trades)
            approve_outcome_analysis(decisions, blocked, trades)
            shadow_df = shadow_approve_pnl_analysis(decisions, trades, blocked)
            real_edge_report(trades, decisions, shadow_df)
            missed_opportunity_analysis(signal_master, shadow_df)
            signal_outcome_analysis(signal_master)
            print(f"\n⏳ Next run in 30 minutes... Iteration {iteration} complete {PIPELINE_ENFORCEMENT_TAG}\n")
            time.sleep(1800)
            continue

        if len(analysis_df) < MIN_TRADES:
            print(f"⏳ Partial data ({len(analysis_df)} trades) — running analysis {PIPELINE_ENFORCEMENT_TAG}")
        else:
            print(f"✅ Analyzing {len(analysis_df)} unique trades {PIPELINE_ENFORCEMENT_TAG}")

        core_metrics(analysis_df)
        exit_analysis(analysis_df)
        run_v55_analysis(analysis_df, decisions, blocked, ai_log)
        regime_matrix(analysis_df)
        ai_calibration(analysis_df)
        edge_discovery(analysis_df)
        duration_analysis(analysis_df)
        outcome_distribution(analysis_df)
        r_distribution(analysis_df)
        ai_vs_duration(analysis_df)
        entry_quality(analysis_df)
        entry_timing_analysis(analysis_df)
        signal_execution_gap(analysis_df)
        slippage_analysis(analysis_df)
        momentum_edge(analysis_df)
        optimal_entry_window(analysis_df)
        blocked_analysis(blocked)

        multi_factor_edge(analysis_df)
        time_exit_diagnosis(analysis_df)
        regime_sr_edge(analysis_df)
        ai_mispricing(analysis_df)
        latency_decay(analysis_df)

        edge_score_analysis(analysis_df)
        blocked_vs_taken(analysis_df)
        ai_decision_quality(analysis_df)
        pipeline_funnel(setups, decisions, trades)
        pipeline_funnel_staged(decisions, blocked, trades)
        approve_outcome_analysis(decisions, blocked, trades)
        shadow_df = shadow_approve_pnl_analysis(decisions, trades, blocked)
        real_edge_report(trades, decisions, shadow_df)
        counterfactual_vs_actual_analysis(trades, shadow_df)
        pipeline_leak_analysis(analysis_df)
        fee_impact(analysis_df)
        data_validation(analysis_df, raw_trade_count=raw_trade_count)

        feature_impact_analysis(analysis_df)
        winner_vs_loser_profile(analysis_df)
        edge_threshold_effectiveness(analysis_df)
        ai_cooldown_effect(analysis_df)
        conversion_rate(setups, trades)
        tp_sl_efficiency(analysis_df)
        sequence_analysis(analysis_df)
        pipeline_consistency(signal_master, analysis_df)
        feature_interaction(analysis_df)
        signal_outcome_analysis(signal_master)
        feature_distribution(analysis_df)
        missed_opportunity_analysis(signal_master, shadow_df)
        feature_completeness_check(analysis_df)
        feature_completeness_check(signal_master)

        conditional_edge(analysis_df, "momentum", 0.3)
        conditional_edge(analysis_df, "volume_ratio", 1.2)
        interaction_matrix(analysis_df)
        latency_curve(analysis_df)
        signal_loss(analysis_df)
        time_edge(analysis_df)
        distribution_shape(analysis_df)
        markov_edge(analysis_df)
        rolling_edge(analysis_df)
        validate_edge(analysis_df)

        invert_signal_impact(analysis_df)
        direction_consistency(analysis_df)
        pipeline_health_dashboard(analysis_df)

        auto_strategy_extractor(analysis_df)

        print(f"\n🔥 STRATEGY ADVICE 🔥 {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Review EXECUTIVE SUMMARY first. Rules need ≥{MIN_TRADES_FOR_RULES} trades. Fix zero-variance features in bot before trusting edge buckets. {PIPELINE_ENFORCEMENT_TAG}")
        print(f"\n⏳ Next run in 30 minutes... Iteration {iteration} complete {PIPELINE_ENFORCEMENT_TAG}\n")
        time.sleep(1800)

if __name__ == "__main__":
    _once_mode = len(sys.argv) > 1 and str(sys.argv[1]).startswith("--once")
    if _once_mode:
        iteration = 0
        iteration += 1
        print(f"\n=== ANALYZER {ANALYZER_VERSION} SINGLE RUN {PIPELINE_ENFORCEMENT_TAG} ===")
        session = load_research_session()
        print_data_provenance_banner(session)
        trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors = load_data()
        trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors = apply_session_filters(
            session, trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors
        )
        raw_trade_count = len(trades.drop_duplicates(subset=["trade_id"])) if not trades.empty and "trade_id" in trades.columns else len(trades)
        master = build_master_dataset(trades, blocked, decisions, ai_log, signal_persist, near_edge)
        signal_master = build_signal_dataset(setups, decisions, ai_log, blocked, raw_trade_count)
        analysis_df = master if not master.empty else pd.DataFrame()
        executive_summary(trades, analysis_df, decisions, ai_log, blocked, near_edge, signal_persist, pipeline_events, ai_errors)
        research_data_coverage_audit(trades, decisions, blocked, pipeline_events)
        v80_research_intelligence_report(analysis_df, decisions, ai_log, trades, near_edge, pipeline_events)
        if not analysis_df.empty:
            core_metrics(analysis_df)
            exit_analysis(analysis_df)
            run_v55_analysis(analysis_df, decisions, blocked, ai_log)
            outcome_distribution(analysis_df)
            fee_impact(analysis_df)
            data_validation(analysis_df, raw_trade_count=raw_trade_count)
            pipeline_funnel(setups, decisions, trades)
            pipeline_funnel_staged(decisions, blocked, trades)
            approve_outcome_analysis(decisions, blocked, trades)
            shadow_df = shadow_approve_pnl_analysis(decisions, trades, blocked)
            real_edge_report(trades, decisions, shadow_df)
            counterfactual_vs_actual_analysis(trades, shadow_df)
            missed_opportunity_analysis(signal_master, shadow_df)
            signal_outcome_analysis(signal_master)
            edge_score_analysis(analysis_df)
            auto_strategy_extractor(analysis_df)
        else:
            pipeline_funnel(setups, decisions, trades)
            pipeline_funnel_staged(decisions, blocked, trades)
            approve_outcome_analysis(decisions, blocked, trades)
            shadow_df = shadow_approve_pnl_analysis(decisions, trades, blocked)
            real_edge_report(trades, decisions, shadow_df)
            missed_opportunity_analysis(signal_master, shadow_df)
            signal_outcome_analysis(signal_master)
        print(f"\n✅ Single run complete {PIPELINE_ENFORCEMENT_TAG}\n")
    else:
        run()