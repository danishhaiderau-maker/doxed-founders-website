# -*- coding: utf-8 -*-
"""
analyzer_research_engine_v62.py — Pipeline intelligence for Bitfinex 3-factor research bot.

Pairs with bybit_bot.py (same folder, same CSV filenames).
Bot contract: EXECUTION_FIX_VERSION / ANALYZER_SYNC_ID / exchange=bitfinex / WINDOW_SIZE=10 readiness.

v111: Pre-test analytics — AI decision fingerprint clusters, confidence×direction matrix,
benchmark-relative scorecard vs CONTINUOUS, missed-opportunity reason heatmap.
v112: Chase attribution report — per-trade chase_count, first/last chase sec, saved_fill heuristic.
v110: AI Calibration Report — confidence buckets, expected vs actual, feature attribution,
decision fingerprints, confidence histogram, confidence×edge cross-ref, override opportunities;
writes ai_calibration_report.json for dashboard "AI Calibration" section.
v89: Realism era split (legacy vs BBO vs depth), book_slippage audit, fill_quality_report.json,
execution type cohorts, replay tick note for executable marks.
v88: Book depth VWAP fills, executable depth marks, stale-book refresh, partial fills on thin liquidity.
v87: BBO sim — buy fills at ask, sell fills at bid; LONG unreal/exit marks bid, SHORT marks ask;
dashboard shows live spread; PnL no longer uses last-trade-only.
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
import threading
from collections import defaultdict
from datetime import datetime, timezone
import os
import json
import glob
import shutil
import sys
import traceback
import re

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ANALYZER_RUN_LOG_FILE = "analyzer_run.log"
RESEARCH_COMPACT_SUMMARY_FILE = "research_compact_summary.json"
EXECUTIVE_SUMMARY_FILE = "executive_summary.txt"
RESEARCH_HIGHLIGHTS_FILE = "research_highlights.txt"
RESEARCH_FINDINGS_FILE = "research_findings.txt"
RESEARCH_COVERAGE_FILE = "research_coverage.txt"
DEEP_DIVE_INDEX_FILE = "research_deep_dive_index.txt"
REPORT_MANIFEST_FILE = "report_manifest.json"
SESSION_ARCHIVE_DIR = "research_session_archives"
SESSION_ARCHIVE_INDEX_FILE = "research_session_index.json"
REAL_EDGE_SUMMARY_FILE = "real_edge_summary.json"
HORIZON_PROFITABILITY_REPORT_FILE = "horizon_profitability_report.json"
HORIZON_PROFIT_HORIZONS = {"5m": 300, "10m": 600, "15m": 900, "30m": 1800, "60m": 3600, "120m": 7200}
TOP_LEAKAGE_REPORT_FILE = "top_leakage_report.json"
LANE_RETIREMENT_REPORT_FILE = "lane_retirement_report.json"
FEATURE_IMPORTANCE_REPORT_FILE = "feature_importance_report.json"
CHASE_PROFIT_REPORT_FILE = "chase_profit_report.json"
CONFIDENCE_BAND_CROSS_REPORT_FILE = "confidence_band_cross_report.json"
EDGE_VALIDATION_REPORT_FILE = "edge_validation_report.json"
BENCHMARK_CONTRIBUTION_REPORT_FILE = "benchmark_contribution_report.json"
LANE_OVERLAP_REPORT_FILE = "lane_overlap_report.json"
FAST_CUT_SWEEP_REPORT_FILE = "fast_cut_sweep_report.json"
LANE_DEFINITION_REPORT_FILE = "lane_definition_report.json"
URGENT_CHASE_REPORT_FILE = "urgent_chase_report.json"
LANE_CHASE_ISOLATION_REPORT_FILE = "lane_chase_isolation_report.json"
TOP_COMBINATIONS_REPORT_FILE = "top_combinations_report.json"
CHASE_EFFICIENCY_MATRIX_REPORT_FILE = "chase_efficiency_matrix_report.json"
TYPE_B_PREDICTOR_REPORT_FILE = "type_b_predictor_report.json"
CHASE_THRESHOLD_REPORT_FILE = "chase_threshold_report.json"
CHASE_DELAY_REPORT_FILE = "chase_delay_report.json"
EXIT_COMBINATIONS_REPORT_FILE = "exit_combinations_report.json"
EXIT_LEAKAGE_BY_REASON_REPORT_FILE = "exit_leakage_by_reason_report.json"
EXIT_LADDER_SIMULATOR_REPORT_FILE = "exit_ladder_simulator_report.json"
ANALYZER_INTEGRITY_REPORT_FILE = "analyzer_integrity_report.json"
REGIME_LEADERBOARD_REPORT_FILE = "regime_leaderboard.json"
ROSTER_POLICY_FILE = "roster_policy.json"
REPORTS_DIR = "reports"
ANALYSIS_DASHBOARD_HTML = "analysis_dashboard.html"
REPORTS_HISTORY_DIR = os.path.join("reports", "history")
HORIZON_MIN_COVERAGE_PCT = 80
CONFIDENCE_BANDS_STANDARD = ("50-55", "55-60", "60-65", "65+")
MIN_LANE_FILLS_FOR_RETIREMENT = 15
MIN_LANE_APPROVES_FOR_RETIREMENT = 10
_CONSOLE_STDOUT = sys.stdout
ANALYZER_CONSOLE_VERBOSE = False
_ANALYZER_REPORT_SUBDIR = None
ALL_DATA_REPORTS_SUBDIR = os.path.join(REPORTS_DIR, "all_data")
ALL_DATA_SESSION_SCOPE = {"data_scope": "all", "fresh_collection_mode": False}


def analyzer_report_path(filename: str) -> str:
    if _ANALYZER_REPORT_SUBDIR:
        return os.path.join(_ANALYZER_REPORT_SUBDIR, filename)
    return filename


def _set_analyzer_report_subdir(subdir: str | None):
    global _ANALYZER_REPORT_SUBDIR
    _ANALYZER_REPORT_SUBDIR = subdir


class _AnalyzerTee:
    """Mirror stdout to analyzer_run.log; optional console suppression for --compact."""

    def __init__(self, console_stream, log_stream):
        self.console = console_stream
        self.log = log_stream
        self.console_enabled = True

    def write(self, data):
        if self.log is not None:
            try:
                self.log.write(data)
                self.log.flush()
            except Exception:
                pass
        if self.console_enabled and self.console is not None:
            self.console.write(data)

    def flush(self):
        if self.log is not None:
            try:
                self.log.flush()
            except Exception:
                pass
        if self.console is not None:
            try:
                self.console.flush()
            except Exception:
                pass

    def isatty(self):
        if self.console is not None and hasattr(self.console, "isatty"):
            return self.console.isatty()
        return False

    @property
    def encoding(self):
        if self.console is not None and hasattr(self.console, "encoding"):
            return self.console.encoding
        return "utf-8"


ANALYZER_LOOP_INTERVAL_MINUTES = max(1, int(os.getenv("ANALYZER_INTERVAL_MINUTES", "30")))


def resolve_analyzer_session_scope() -> tuple:
    """Always analyze the current session — from last fresh collection wipe onward."""
    session = load_research_session()
    if session.get("fresh_collection_mode") or session.get("fresh_collection_start_time"):
        iso = session.get("fresh_collection_start_iso") or "n/a"
        return True, f"SESSION (fresh collection from {iso})"
    if session.get("bot_start_time"):
        iso = session.get("bot_start_iso") or "n/a"
        return True, f"SESSION (bot session from {iso})"
    return True, "SESSION (no research_session.json — analyzing all loaded rows)"


def start_research_dashboard_server() -> threading.Thread | None:
    """Embedded read-only research dashboard (:9001) — same process as analyzer."""
    try:
        from research_dashboard import (
            app,
            BIND_HOST,
            BIND_PORT,
            PUBLIC_URL,
            RESEARCH_DASHBOARD_VERSION,
        )
    except ImportError as exc:
        print(f"  ⚠️ Research dashboard unavailable: {exc} {PIPELINE_ENFORCEMENT_TAG}")
        return None

    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((BIND_HOST, int(BIND_PORT)))
    except OSError:
        print(
            f"  ℹ️ Dashboard port {BIND_PORT} already in use — "
            f"using existing server at {PUBLIC_URL} {PIPELINE_ENFORCEMENT_TAG}"
        )
        return None
    finally:
        try:
            probe.close()
        except Exception:
            pass

    def _serve():
        print(
            f"  Research Dashboard {RESEARCH_DASHBOARD_VERSION} listening on "
            f"http://{BIND_HOST}:{BIND_PORT}/ (LAN: {PUBLIC_URL}) {PIPELINE_ENFORCEMENT_TAG}"
        )
        app.run(host=BIND_HOST, port=BIND_PORT, debug=False, threaded=True, use_reloader=False)

    thread = threading.Thread(target=_serve, name="research_dashboard", daemon=True)
    thread.start()
    return thread


def _setup_analyzer_output(verbose_console=False, enable_log=True):
    """Tee all prints to analyzer_run.log; default hides verbose console (executive mode)."""
    log_handle = None
    if enable_log:
        try:
            log_handle = open(ANALYZER_RUN_LOG_FILE, "w", encoding="utf-8", buffering=1)
            header = (
                f"# analyzer run {datetime.now().isoformat()} | "
                f"sync={ANALYZER_SYNC_ID} | verbose_console={verbose_console}\n"
            )
            log_handle.write(header)
            log_handle.flush()
        except Exception as exc:
            _CONSOLE_STDOUT.write(f"⚠️ Could not open {ANALYZER_RUN_LOG_FILE}: {exc}\n")
    tee = _AnalyzerTee(_CONSOLE_STDOUT, log_handle)
    tee.console_enabled = verbose_console
    sys.stdout = tee
    return tee, log_handle


def _restore_analyzer_output(tee, log_handle):
    sys.stdout = _CONSOLE_STDOUT
    if log_handle is not None:
        try:
            log_handle.close()
        except Exception:
            pass
    return tee


def _load_json_report(path, default=None):
    candidates = [path]
    if not os.path.dirname(str(path)):
        alt = analyzer_report_path(path)
        if alt != path:
            candidates.append(alt)
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return default if default is not None else {}


def _fmt_pct(val, digits=1):
    if val is None:
        return "n/a"
    try:
        return f"{float(val):.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_usd(val, digits=2):
    if val is None:
        return "n/a"
    try:
        return f"${float(val):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


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
# Single source: combo_pathway_config (bot + dashboard import the same contract).
try:
    from combo_pathway_config import (
        ANALYZER_SYNC_ID,
        BENCHMARK_LANE,
        COMPARISON_BENCHMARK_LANE,
        CONTINUOUS_PROXY_LANES,
        COMBO_CHASE_DELAY_LANES,
        COMBO_CHASE_DIRECT_REFERENCE,
        ACTIVE_CHASE_ISOLATION_LANES,
        ACTIVE_CHASE_ISOLATION_PAIRS,
        COMBO_CHASE_ISOLATION_PAIRS,
        COMBO_LANE_SPECS,
        COMBO_EXECUTION_LANES,
        COMBO_LANE_LABELS as _COMBO_LANE_LABELS,
        EXPECTED_BOT_VERSION,
        EXPECTED_EXCHANGE,
        PRIMARY_PRODUCTION_LANE,
        RESEARCH_STACK_VERSION,
        RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2,
    )
    from scenario_c_config import SCENARIO_C_LADDER_LABEL, TRAIL_LADDER_SCENARIO_C
    from legacy_pathway_config import (
        PATHWAY_STATUS_SHADOW_COLLECTING,
        SHADOW_COLLECTING_LANES,
    )
    from experimental_pathway_config import (
        EXPERIMENTAL_EXECUTION_LANES,
        EXPERIMENTAL_LANE_LABELS,
        RESEARCH_LANE_AI_DISAGREEMENT_ALPHA,
        RESEARCH_LANE_AI_DISAGREEMENT_REPLAY,
        RESEARCH_LANE_RECOVERY_MONSTER_V1,
        RESEARCH_LANE_TYPE_B_PREDICTOR_V1,
    )
    ACTIVE_PATHWAY_LANES = tuple(COMBO_EXECUTION_LANES) + tuple(EXPERIMENTAL_EXECUTION_LANES)
    from pathway_lane_roster import ANALYZER_COMPARE_LANES, RETIRED_PATHWAY_LANES as _ROSTER_RETIRED
    RETIRED_PATHWAY_LANES = _ROSTER_RETIRED
except ImportError:
    EXPECTED_BOT_VERSION = "v10.2-ai-chase-bands-desk-2026-06-23"
    EXPECTED_EXCHANGE = "bitfinex"
    ANALYZER_SYNC_ID = "v10.2-ai-chase-bands-desk-2026-06-23"
    RESEARCH_STACK_VERSION = ANALYZER_SYNC_ID
    SCENARIO_C_LADDER_LABEL = "12→8, 15→10, 25→18, 40→28, 60→45, 80→60, 100→75, 150→120"
    TRAIL_LADDER_SCENARIO_C = [
        (12, 8), (15, 10), (25, 18), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120),
    ]
    PATHWAY_STATUS_SHADOW_COLLECTING = "SHADOW_COLLECTING"
    SHADOW_COLLECTING_LANES = ()
    BENCHMARK_LANE = "CONTINUOUS"
    COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
    CONTINUOUS_PROXY_LANES = (
        "COMBO_65_SP5_CHASE_3PLUS",
        "COMBO_604_SP4_CHASE_3PLUS",
    )
    PRIMARY_PRODUCTION_LANE = "COMBO_65_SP5_CHASE_3PLUS"
    COMBO_CHASE_DELAY_LANES = (
        "COMBO_65_SP5_CHASE_3PLUS",
        "COMBO_604_SP4_CHASE_3PLUS",
    )
    COMBO_CHASE_ISOLATION_PAIRS = (
        ("COMBO_604_SP4_DIRECT", "COMBO_604_SP4_CHASE_3PLUS"),
        ("COMBO_65_SP5_DIRECT", "COMBO_65_SP5_CHASE_3PLUS"),
    )
    ACTIVE_CHASE_ISOLATION_PAIRS = (
        ("CONTINUOUS", "AI60_SP3_VIRTUAL_CHASE"),
    )
    ACTIVE_CHASE_ISOLATION_LANES = (
        "CONTINUOUS",
        "AI60_SP3_VIRTUAL_CHASE",
        "A160_CONTEXT_CHASE_EXIT_V2",
    )
    COMBO_LANE_SPECS = {}
    COMBO_CHASE_DIRECT_REFERENCE = "COMBO_604_SP4_DIRECT"
    ACTIVE_PATHWAY_LANES = (
        "COMBO_65_SP5_CHASE_3PLUS",
        "COMBO_604_SP4_CHASE_3PLUS",
        "AI_DISAGREEMENT_REPLAY",
    )
    _COMBO_LANE_LABELS = {}
    ANALYZER_COMPARE_LANES = ACTIVE_PATHWAY_LANES + (
        "CONTINUOUS",
        "COMBO_65_SP5_DIRECT",
        "COMBO_604_SP4_DIRECT",
        "RECOVERY_MONSTER_V1",
        "TYPE_B_PREDICTOR_V1",
        "AI_DISAGREEMENT_ALPHA",
        "EXTREME_EDGE",
        "EDGE_PLUS_STACK",
        "AI_SCAN",
        "HIGH_EDGE_RUNNER",
        "SHADOW_RUNNER",
        "EDGE_ALPHA_4",
        "TYPE_B_HUNTER",
        "SHORT_BEAR_ALPHA",
        "AI_60_65_ALPHA",
        "URGENT_CHASE_ALPHA",
        "CHASE_3PLUS_ALPHA",
    )
    RETIRED_PATHWAY_LANES = frozenset({
        "EXTREME_EDGE", "EDGE_PLUS_STACK",
        "COMBO_65_SP5_DIRECT", "COMBO_604_SP4_DIRECT",
        "RECOVERY_MONSTER_V1", "TYPE_B_PREDICTOR_V1", "AI_DISAGREEMENT_ALPHA",
    })
EXPECTED_SYMBOL = "tBTCF0:USTF0"
EXPECTED_FEE_PROFILE = "BITFINEX_ZERO"
BOT_VERSION = EXPECTED_BOT_VERSION
ANALYZER_VERSION = RESEARCH_STACK_VERSION
REVERSAL_STUDY_FILE = "reversal_study.jsonl"
AI_REASON_RESEARCH_FILE = "ai_reason_research.jsonl"
AI_CONFIDENCE_CALIBRATION_FILE = "ai_confidence_calibration.jsonl"
TRADE_LIFECYCLE_FILE = "trade_lifecycle.jsonl"
EXECUTION_FUNNEL_FILE = "execution_funnel.jsonl"
LANE_OPPORTUNITY_CAPTURE_FILE = "lane_opportunity_capture.jsonl"
LANE_OPPORTUNITY_REPORT_FILE = "lane_opportunity_capture.json"
AI_FUNNEL_REPORT_FILE = "ai_funnel_report.json"
AI_CONFIDENCE_EXPECTANCY_FILE = "ai_confidence_expectancy.json"
AI_DECISION_FINGERPRINT_REPORT_FILE = "ai_decision_fingerprint_report.json"
APPROVE_OUTCOME_CONF_DIRECTION_FILE = "approve_outcome_confidence_direction.json"
BENCHMARK_RELATIVE_SCORECARD_FILE = "benchmark_relative_scorecard.json"
MISSED_OPPORTUNITY_HEATMAP_FILE = "missed_opportunity_heatmap.json"
CHASE_ATTRIBUTION_REPORT_FILE = "chase_attribution_report.json"
CHASE_EFFECTIVENESS_REPORT_FILE = "chase_effectiveness_report.json"
SCENARIO_C_LEAKAGE_REPORT_FILE = "scenario_c_leakage_report.json"
FIRST_15M_OUTCOME_REPORT_FILE = "first_15m_outcome_report.json"
PATHWAY_SURVIVAL_REPORT_FILE = "pathway_survival_report.json"
AI_DIRECTION_BIAS_REPORT_FILE = "ai_direction_bias_report.json"
EDGE_PREDICTIVENESS_REPORT_FILE = "edge_predictiveness_report.json"
EDGE_SCORE_DECILE_REPORT_FILE = "edge_score_decile_report.json"
EDGE_INCREMENTAL_VALUE_REPORT_FILE = "edge_incremental_value_report.json"
SCENARIO_C_CAPTURE_RATIO_REPORT_FILE = "scenario_c_capture_ratio.json"
FAST_CUT_SURVIVOR_REPORT_FILE = "fast_cut_survivor_report.json"
EDGE_DECILE_ORDER = ["0-1", "1-2", "2-3", "3-4", "4+"]
EDGE_INCREMENTAL_THRESHOLDS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
AI_CALIB_BAND_MIDPOINTS = {"50-55": 52.5, "55-60": 57.5, "60-65": 62.5, "65+": 70.0}
POST_CUT_GREEN_HORIZONS = {"5m": 300, "10m": 600, "15m": 900, "30m": 1800}
POST_CUT_MFE_HORIZONS = {"5m": 300, "15m": 900, "30m": 1800, "60m": 3600}
FAST_CUT_NO_EXIT_PCT = -999.0
EXECUTION_FUNNEL_SUMMARY_FILE = "execution_funnel_summary.json"
FILL_QUALITY_REPORT_FILE = "fill_quality_report.json"
PATHWAY_TRADE_COLUMNS = [
    "research_lane", "entry_path", "entry_mode", "fill_model",
    "signal_age_sec", "entry_delay_sec", "signal_age_bucket",
]
REALISM_TRADE_COLUMNS = [
    "book_slippage_usd_entry", "book_slippage_usd_exit", "book_slippage_usd_total",
    "entry_partial_fill", "execution_entry_type", "execution_exit_type",
    "execution_entry_price", "execution_exit_price", "execution_slippage",
]
REALISM_ERA_LABELS = {
    "LEGACY_LAST_PRICE": "Pre v1.1.16 — last-trade sim (optimistic PnL)",
    "BBO_ONLY": "v1.1.16–17 — bid/ask marks, no depth walk",
    "DEPTH_REALISM": "v1.1.18+ — BBO + order-book VWAP fills",
    "UNKNOWN": "Unknown sim era",
}
RESEARCH_LANE_LABELS = {
    "CONTINUOUS": "Continuous AI Research",
    "HIGH_EDGE_RUNNER": "High Edge Runner",
    "EXTREME_EDGE": "Extreme Edge",
    "EDGE_PLUS_STACK": "Edge Plus Stack",
    "SHADOW_RUNNER": "Shadow Runner",
    "EDGE_ALPHA_4": "Edge Alpha 4",
    "TYPE_B_HUNTER": "Type B Hunter",
    "SHORT_BEAR_ALPHA": "Short Bear Alpha",
    "AI_60_65_ALPHA": "AI 60-65 Alpha",
    "URGENT_CHASE_ALPHA": "Urgent Chase Alpha",
    "CHASE_3PLUS_ALPHA": "Chase 3+ Alpha",
    "COMBO_65_SP5_CHASE_3PLUS": "AI65+ · Spread5+ · Chase 3+",
    "COMBO_65_SP5_DIRECT": "AI65+ · Spread5+ · Direct",
    "COMBO_604_SP4_CHASE_3PLUS": "AI60-65 · Spread4 · Chase 3+",
    "COMBO_604_SP4_DIRECT": "AI60-65 · Spread4 · Direct",
    # Legacy — historical CSV/JSON only; excluded from active scorecards
    "EDGE_ACCELERATION": "Edge Acceleration (legacy)",
    "PROFIT_GATES": "Profit Gates (legacy)",
    "STABILITY": "Stability (legacy)",
}
if _COMBO_LANE_LABELS:
    RESEARCH_LANE_LABELS.update(_COMBO_LANE_LABELS)
try:
    RESEARCH_LANE_LABELS.update(EXPERIMENTAL_LANE_LABELS)
except NameError:
    pass
EXPIRED_ORDERS_FILE = "expired_orders_3factor.csv"
FILL_QUALITY_JSONL_FILE = "fill_quality.jsonl"
SHADOW_VS_LIVE_ENTRY_FILE = "shadow_vs_live_entry.jsonl"
SHADOW_VS_LIVE_ENTRY_REPORT_FILE = "shadow_vs_live_entry_report.json"
SHADOW_FILL_OUTCOME_REPORT_FILE = "shadow_fill_outcome_report.json"
BENCHMARK_VS_LANES_REPORT_FILE = "benchmark_vs_lanes_report.json"
PATHWAY_LANE_SPECS_FILE = "pathway_lane_specs.json"
HORIZON_COUNTERFACTUAL_REPORT_FILE = "horizon_counterfactual_report.json"
AI_CALIBRATION_REPORT_FILE = "ai_calibration_report.json"
DIRECTION_REPORT_FILE = "direction_report.json"
CONFIDENCE_BAND_REPORT_FILE = "confidence_band_report.json"
APPROVE_CONF_DIRECTION_BUCKETS = ["50-55", "55-60", "60-65", "65+"]
AI_CALIB_REPORT_BUCKET_ORDER = ["50-55", "55-60", "60-65", "65+"]
CONFIDENCE_BAND_BUCKET_ORDER = ["0-45", "45-50", "50-55", "55-60", "60-65", "65+"]
AI_MATRIX_CONF_BUCKETS = ["50-55", "55-60"]
AI_MATRIX_EDGE_BUCKETS = ["2-3", "3-4", "4+"]
HORIZON_30M_SEC = 1800
EXPERIMENT_LANES = (
    "COMBO_65_SP5_DIRECT",
    "COMBO_604_SP4_CHASE_3PLUS",
    "COMBO_604_SP4_DIRECT",
    "HIGH_EDGE_RUNNER",
    "SHADOW_RUNNER",
    "EDGE_ALPHA_4",
    "TYPE_B_HUNTER",
    "SHORT_BEAR_ALPHA",
    "AI_60_65_ALPHA",
    "URGENT_CHASE_ALPHA",
    "CHASE_3PLUS_ALPHA",
    "TYPE_B_PREDICTOR_V1",
    "RECOVERY_MONSTER_V1",
    "AI_DISAGREEMENT_ALPHA",
    "AI_DISAGREEMENT_REPLAY",
    "CONTINUOUS",
)
PATHWAY_LANE_STATUS = {
    "COMBO_65_SP5_CHASE_3PLUS": "PRIMARY_PRODUCTION",
    "COMBO_65_SP5_DIRECT": "RETIRED",
    "COMBO_604_SP4_CHASE_3PLUS": "ACTIVE",
    "COMBO_604_SP4_DIRECT": "RETIRED",
    "CONTINUOUS": "BENCHMARK",
    "TYPE_B_PREDICTOR_V1": "RETIRED",
    "RECOVERY_MONSTER_V1": "RETIRED",
    "AI_DISAGREEMENT_ALPHA": "RETIRED",
    "AI_DISAGREEMENT_REPLAY": "ACTIVE",
    "HIGH_EDGE_RUNNER": "SHADOW_COLLECTING",
    "EXTREME_EDGE": "RETIRED",
    "EDGE_PLUS_STACK": "RETIRED",
    "SHADOW_RUNNER": "SHADOW_COLLECTING",
    "EDGE_ALPHA_4": "SHADOW_COLLECTING",
    "TYPE_B_HUNTER": "SHADOW_COLLECTING",
    "SHORT_BEAR_ALPHA": "SHADOW_COLLECTING",
    "AI_60_65_ALPHA": "SHADOW_COLLECTING",
    "URGENT_CHASE_ALPHA": "SHADOW_COLLECTING",
    "CHASE_3PLUS_ALPHA": "SHADOW_COLLECTING",
    "AI_SCAN": "ACTIVE",
}
BENCHMARK_LANES = ANALYZER_COMPARE_LANES
LEGACY_LANES = frozenset({"EDGE_ACCELERATION", "PROFIT_GATES", "STABILITY", "EXEC_5M"})
FAST_CUT_SWEEP_LEVELS = (-6, -8, -10, -12)
ANALYZER_JSON_REPORT_FILES = (
    AI_CALIBRATION_REPORT_FILE,
    AI_FUNNEL_REPORT_FILE,
    AI_DECISION_FINGERPRINT_REPORT_FILE,
    APPROVE_OUTCOME_CONF_DIRECTION_FILE,
    BENCHMARK_RELATIVE_SCORECARD_FILE,
    BENCHMARK_VS_LANES_REPORT_FILE,
    CHASE_ATTRIBUTION_REPORT_FILE,
    CHASE_EFFECTIVENESS_REPORT_FILE,
    CONFIDENCE_BAND_REPORT_FILE,
    DIRECTION_REPORT_FILE,
    EDGE_INCREMENTAL_VALUE_REPORT_FILE,
    EDGE_PREDICTIVENESS_REPORT_FILE,
    EDGE_SCORE_DECILE_REPORT_FILE,
    FAST_CUT_SURVIVOR_REPORT_FILE,
    FILL_QUALITY_REPORT_FILE,
    FIRST_15M_OUTCOME_REPORT_FILE,
    HORIZON_COUNTERFACTUAL_REPORT_FILE,
    HORIZON_PROFITABILITY_REPORT_FILE,
    LANE_OPPORTUNITY_REPORT_FILE,
    MISSED_OPPORTUNITY_HEATMAP_FILE,
    PATHWAY_SURVIVAL_REPORT_FILE,
    REAL_EDGE_SUMMARY_FILE,
    SCENARIO_C_CAPTURE_RATIO_REPORT_FILE,
    SCENARIO_C_LEAKAGE_REPORT_FILE,
    AI_DIRECTION_BIAS_REPORT_FILE,
    RESEARCH_COMPACT_SUMMARY_FILE,
    TOP_LEAKAGE_REPORT_FILE,
    EXIT_LEAKAGE_BY_REASON_REPORT_FILE,
    EXIT_LADDER_SIMULATOR_REPORT_FILE,
    LANE_RETIREMENT_REPORT_FILE,
    FEATURE_IMPORTANCE_REPORT_FILE,
    CHASE_PROFIT_REPORT_FILE,
    CONFIDENCE_BAND_CROSS_REPORT_FILE,
    EDGE_VALIDATION_REPORT_FILE,
    BENCHMARK_CONTRIBUTION_REPORT_FILE,
    LANE_OVERLAP_REPORT_FILE,
    FAST_CUT_SWEEP_REPORT_FILE,
    LANE_DEFINITION_REPORT_FILE,
    URGENT_CHASE_REPORT_FILE,
    LANE_CHASE_ISOLATION_REPORT_FILE,
    TOP_COMBINATIONS_REPORT_FILE,
    CHASE_EFFICIENCY_MATRIX_REPORT_FILE,
    TYPE_B_PREDICTOR_REPORT_FILE,
    CHASE_THRESHOLD_REPORT_FILE,
    CHASE_DELAY_REPORT_FILE,
    EXIT_COMBINATIONS_REPORT_FILE,
    REGIME_LEADERBOARD_REPORT_FILE,
    ROSTER_POLICY_FILE,
)
DEEP_DIVE_REPORT_CATALOG = (
    ("AI Calibration", AI_CALIBRATION_REPORT_FILE, "Confidence buckets, expected vs actual WR, calibration error"),
    ("AI Funnel", AI_FUNNEL_REPORT_FILE, "AI decision funnel stages and drop-offs"),
    ("AI Decision Fingerprints", AI_DECISION_FINGERPRINT_REPORT_FILE, "Cluster fingerprints driving APPROVE/REJECT"),
    ("Approve Outcome × Conf × Direction", APPROVE_OUTCOME_CONF_DIRECTION_FILE, "Outcome matrix by confidence and direction"),
    ("Benchmark Scorecard", BENCHMARK_RELATIVE_SCORECARD_FILE, "Experiment lanes vs PRIMARY_PRODUCTION benchmark"),
    ("Lane Analysis", BENCHMARK_VS_LANES_REPORT_FILE, "Per-lane approves, fills, PnL, EV/approve"),
    ("Chase Attribution", CHASE_ATTRIBUTION_REPORT_FILE, "Per-trade chase counts and saved-fill heuristic"),
    ("Chase Effectiveness", CHASE_EFFECTIVENESS_REPORT_FILE, "PnL by chase-count bucket"),
    ("Chase Threshold", CHASE_THRESHOLD_REPORT_FILE, "Cumulative EV/WR/PnL at chase_count N+ thresholds"),
    ("Chase Delay Lanes", CHASE_DELAY_REPORT_FILE, "COMBO Direct vs Chase 3+ within each AI/spread tier"),
    ("Confidence Bands", CONFIDENCE_BAND_REPORT_FILE, "Executed trades by AI confidence band"),
    ("Direction Bias", DIRECTION_REPORT_FILE, "LONG vs SHORT performance split"),
    ("Edge Incremental Value", EDGE_INCREMENTAL_VALUE_REPORT_FILE, "AI-only vs AI+edge filter uplift"),
    ("Edge Predictiveness", EDGE_PREDICTIVENESS_REPORT_FILE, "Does edge score rank outcomes?"),
    ("Edge Score Deciles", EDGE_SCORE_DECILE_REPORT_FILE, "PnL/WR by edge decile bucket"),
    ("Fast-Cut Survivors", FAST_CUT_SURVIVOR_REPORT_FILE, "Thesis fast-cut trades — saved vs missed ladder"),
    ("Fill Quality", FILL_QUALITY_REPORT_FILE, "Slippage, distance, fill realism metrics"),
    ("First 15m Outcomes", FIRST_15M_OUTCOME_REPORT_FILE, "Early post-fill trajectory"),
    ("Horizon Counterfactual", HORIZON_COUNTERFACTUAL_REPORT_FILE, "What-if hold horizons on exits"),
    ("Horizon Recovery", HORIZON_PROFITABILITY_REPORT_FILE, "Would losers have turned green later?"),
    ("Lane Opportunity", LANE_OPPORTUNITY_REPORT_FILE, "Missed lane capture vs shadow fills"),
    ("Missed Opportunities", MISSED_OPPORTUNITY_HEATMAP_FILE, "Blocked signals by reason and $ left"),
    ("Pathway Survival", PATHWAY_SURVIVAL_REPORT_FILE, "Pathway stage survival and drop rates"),
    ("Real Edge / Gate Damage", REAL_EDGE_SUMMARY_FILE, "APPROVE funnel, executed vs blocked shadow PnL"),
    ("Scenario C Capture", SCENARIO_C_CAPTURE_RATIO_REPORT_FILE, "MFE capture % distribution"),
    ("Scenario C Leakage", SCENARIO_C_LEAKAGE_REPORT_FILE, "Peak vs booked profit left on table"),
    ("AI Direction Bias", AI_DIRECTION_BIAS_REPORT_FILE, "AI directional skew vs outcomes"),
    ("Compact Summary", RESEARCH_COMPACT_SUMMARY_FILE, "Machine-readable rollup of all KPIs"),
    ("Top Leakage Trades", TOP_LEAKAGE_REPORT_FILE, "Top 50 trades — peak vs booked, money left on table"),
    ("Lane Retirement", LANE_RETIREMENT_REPORT_FILE, "KEEP / RETIRE / COLLECT MORE per pathway lane"),
    ("Feature Importance", FEATURE_IMPORTANCE_REPORT_FILE, "Which trading features correlate with PnL"),
    ("Chase Profit", CHASE_PROFIT_REPORT_FILE, "Incremental PnL from chase-assisted vs static fills"),
    ("Confidence × Lane", CONFIDENCE_BAND_CROSS_REPORT_FILE, "Performance by AI band per lane"),
    ("Edge Validation", EDGE_VALIDATION_REPORT_FILE, "ACTIVE / WATCHLIST / DEPRECATED status for edge filter"),
    ("Benchmark Contribution", BENCHMARK_CONTRIBUTION_REPORT_FILE, "% of session PnL from each lane"),
    ("Lane Overlap", LANE_OVERLAP_REPORT_FILE, "Overlap vs CONTINUOUS — unique alpha signals"),
    ("Fast Cut Sweep", FAST_CUT_SWEEP_REPORT_FILE, "Replay sweep at -6/-8/-10/-12 vs booked PnL"),
    ("Lane Definitions", LANE_DEFINITION_REPORT_FILE, "Entry conditions, dependencies, and status per pathway lane"),
    ("Urgent Chase Alpha", URGENT_CHASE_REPORT_FILE, "Legacy URGENT_CHASE_ALPHA vs CONTINUOUS (historical)"),
    ("Lane Chase Isolation", LANE_CHASE_ISOLATION_REPORT_FILE, "COMBO Direct vs Chase 3+ fill_model and chase policy"),
    ("Top Combinations", TOP_COMBINATIONS_REPORT_FILE, "AI × spread × type × lane ranked cohorts"),
    ("Exit Combinations", EXIT_COMBINATIONS_REPORT_FILE, "Exit reason × entry combo — leakage and best exit paths"),
    ("Exit Leakage by Reason", EXIT_LEAKAGE_BY_REASON_REPORT_FILE, "Which exit reasons destroy the most value"),
    ("Exit Ladder Simulator", EXIT_LADDER_SIMULATOR_REPORT_FILE, "Replay tick sim — alternate ladder rungs vs live"),
    ("AI Scan Independence", "ai_scan_independence_report.json", "AI pipeline vs production tile ON/OFF"),
    ("Lane Memory", "lane_memory_validation.json", "Retired lane exposure + bucket bounds"),
    ("Bot↔Analyzer Sync", "bot_analyzer_sync.json", "SYSTEM_NOT_READY gate at startup"),
    ("TYPE_B Execution Audit", "type_b_execution_audit.json", "Proof TYPE_B is not an entry gate"),
    ("Exit Reports Validation", "exit_reports_validation.json", "Analyzer gate — exit reports populated"),
    ("Chase Efficiency Matrix", CHASE_EFFICIENCY_MATRIX_REPORT_FILE, "Chase count × AI × spread × lane EV matrix"),
    ("Type B Predictor", TYPE_B_PREDICTOR_REPORT_FILE, "Pre-entry feature separators for Type B runners"),
)
AI_INPUT_LOG_FILE = "ai_input_log.jsonl"
RESEARCH_FREE_RUN_LIVE = True  # v78: bot disables post-AI MTF/chop — sweeps use strict reference thresholds
FLAT_MARGIN_LIVE_USD = 20.0
EDGE_CENSUS_FILE = "edge_census.jsonl"
MARGIN_SIZE_SWEEP_USD = [5.0, 10.0, 15.0, 20.0, 25.0]
RESEARCH_SESSION_FILE = "research_session.json"
PIPELINE_ENFORCEMENT_TAG = "[PIPELINE ENFORCEMENT]"
# Mirror scenario_c_config — (peak_margin_pct_trigger, lock_floor_margin_pct)
TRAIL_LADDER = list(TRAIL_LADDER_SCENARIO_C)
THESIS_MFE_PROTECT_DEFAULT = 2.0
PEAK_NEVER_LOSER_MIN_PEAK = 40.0
PEAK_NEVER_LOSER_FLOOR = 10.0
DEFAULT_PULLBACK_THRESHOLDS = [0.0, 0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.006]
THESIS_FAST_EXIT_DEFAULT = -12.0
DEFAULT_PULLBACK_PCT = 0.001
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
SHADOW_LANE_OUTCOME_FILE = "shadow_lane_outcome.jsonl"
V2_SHADOW_OUTCOME_FILE = "v2_shadow_outcome.jsonl"
V2_CHECKER_LOG_FILE = "v2_checker_log.jsonl"

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
    raw = session.get("fresh_collection_start_time") or session.get("bot_start_time")
    if raw is None:
        return None
    try:
        return pd.Timestamp(float(raw), unit="s", tz="UTC")
    except Exception:
        iso = session.get("fresh_collection_start_iso") or session.get("bot_start_iso")
        return pd.to_datetime(iso, utc=True, errors="coerce")


def _session_hours(session: dict = None) -> float:
    session = session or load_research_session()
    start = _session_start_ts(session)
    if start is None or pd.isna(start):
        return 0.0
    return (pd.Timestamp.now(tz="UTC") - start).total_seconds() / 3600.0


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
    session_bv = (load_research_session() or {}).get("bot_version")
    if session_bv:
        print(f"  Session bot_version (research_session.json): {session_bv}")
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

def _agent_data_path(filename: str) -> str:
    """Resolve CSV/JSONL under agent root when cwd is research/."""
    if os.path.isfile(filename):
        return filename
    parent = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    alt = os.path.normpath(os.path.join(parent, filename))
    if os.path.isfile(alt):
        return alt
    env_root = os.getenv("BTC_AGENT_DATA_DIR")
    if env_root:
        env_alt = os.path.join(env_root, filename)
        if os.path.isfile(env_alt):
            return env_alt
    return filename


def robust_read_csv(filepath, name="file"):
    filepath = _agent_data_path(filepath)
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
    critical = [
        "edge_score", "ai_win_prob", "net_pnl_usd", "momentum", "volume_ratio",
        "final_direction", "edge_score_at_entry", "features_velocity",
        "features_volume_ratio", "features_delta",
    ]
    delay_cols = ["entry_delay_sec", "signal_age_sec", "entry_delay"]
    has_delay = any(c in df.columns and df[c].notna().any() for c in delay_cols)
    missing = []
    if not has_delay:
        missing.append("entry_delay_sec")
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
    pathway_missing = [col for col in PATHWAY_TRADE_COLUMNS if col not in trades.columns]
    realism_missing = [col for col in REALISM_TRADE_COLUMNS if col not in trades.columns]
    if missing_cols:
        trades = pd.concat([trades, pd.DataFrame({col: np.nan for col in missing_cols}, index=trades.index)], axis=1)
        print(f"   Dynamic schema: added missing columns {missing_cols} {PIPELINE_ENFORCEMENT_TAG}")
    if pathway_missing:
        trades = pd.concat([trades, pd.DataFrame({col: np.nan for col in pathway_missing}, index=trades.index)], axis=1)
        print(f"   Pathway schema: added {pathway_missing} (populate after bot restart on {EXPECTED_BOT_VERSION}) {PIPELINE_ENFORCEMENT_TAG}")
    if realism_missing:
        trades = pd.concat([trades, pd.DataFrame({col: np.nan for col in realism_missing}, index=trades.index)], axis=1)
        print(f"   Realism schema: added {realism_missing} (stamped after {EXPECTED_BOT_VERSION}) {PIPELINE_ENFORCEMENT_TAG}")

    numeric_cols = [
        "net_pnl_usd", "conf", "ai_win_prob", "r_multiple", "ai_threshold",
        "dur_min", "entry_delay", "slippage", "momentum", "volatility",
        "distance_to_resistance", "distance_to_support", "edge_score", "edge_threshold",
        "edge_score_at_entry", "features_velocity", "features_volume_ratio", "features_delta",
        "book_slippage_usd_entry", "book_slippage_usd_exit", "book_slippage_usd_total",
        "execution_entry_price", "execution_exit_price", "execution_slippage",
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
    work["entry_delay_min"] = _normalize_entry_delay_min(work)
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


def _signal_replay_paths():
    paths = [_agent_data_path(SIGNAL_REPLAY_FILE)]
    for i in range(1, 6):
        alt = _agent_data_path(f"{SIGNAL_REPLAY_FILE}.{i}")
        if os.path.isfile(alt):
            paths.append(alt)
    return paths


def _load_jsonl_replays():
    replays = {}
    try:
        for path in _signal_replay_paths():
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    tid = row.get("trade_id")
                    if tid:
                        replays[tid] = row
    except Exception as e:
        print(f"⚠️ signal_replay read error: {e} {PIPELINE_ENFORCEMENT_TAG}")
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


def _load_jsonl_rows(path):
    """Load all rows from a JSONL research dataset."""
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ {path} read error: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return rows


def load_reversal_study():
    """Load reversal_study.jsonl (risk score vs horizon outcomes)."""
    return _load_jsonl_rows(REVERSAL_STUDY_FILE)


def load_ai_reason_research():
    """Load ai_reason_research.jsonl (reasons_for/against + outcomes)."""
    return _load_jsonl_rows(AI_REASON_RESEARCH_FILE)


def load_ai_confidence_calibration():
    """Load ai_confidence_calibration.jsonl (prob bucket vs actual)."""
    return _load_jsonl_rows(AI_CONFIDENCE_CALIBRATION_FILE)


def load_trade_lifecycle():
    """Load trade_lifecycle.jsonl (entry stage, regime, trend health)."""
    return _load_jsonl_rows(TRADE_LIFECYCLE_FILE)


def load_research_jsonl_datasets():
    return {
        "reversal_study": load_reversal_study(),
        "ai_reason_research": load_ai_reason_research(),
        "ai_confidence_calibration": load_ai_confidence_calibration(),
        "trade_lifecycle": load_trade_lifecycle(),
    }


def research_jsonl_summary(datasets=None):
    """Summarize v96+ JSONL research datasets for offline validation."""
    print("\n=== RESEARCH JSONL DATASETS (reversal / AI reason / calibration / lifecycle) ===")
    if datasets is None:
        datasets = load_research_jsonl_datasets()
    path_map = {
        "reversal_study": REVERSAL_STUDY_FILE,
        "ai_reason_research": AI_REASON_RESEARCH_FILE,
        "ai_confidence_calibration": AI_CONFIDENCE_CALIBRATION_FILE,
        "trade_lifecycle": TRADE_LIFECYCLE_FILE,
    }
    for label, rows in datasets.items():
        path = path_map.get(label, "")
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        print(f"  {label}: n={len(rows)} size={size} {PIPELINE_ENFORCEMENT_TAG}")

    reversal = datasets.get("reversal_study") or []
    outcomes = [r for r in reversal if r.get("phase") != "start"]
    if outcomes:
        wins = sum(1 for r in outcomes if str(r.get("result", "")).upper() == "WIN")
        print(f"  reversal_study outcomes: n={len(outcomes)} win_rate={wins / len(outcomes) * 100:.1f}% {PIPELINE_ENFORCEMENT_TAG}")

    cal = datasets.get("ai_confidence_calibration") or []
    if cal:
        buckets = {}
        for r in cal:
            if r.get("schema") != "ai_calibration_v1":
                continue
            b = r.get("prob_bucket", "?")
            buckets.setdefault(b, []).append(r.get("actual"))
        print(f"  ai_confidence_calibration buckets: {len(buckets)} {PIPELINE_ENFORCEMENT_TAG}")
        for b, vals in sorted(buckets.items(), key=lambda x: str(x[0])):
            if not vals:
                continue
            hit = sum(1 for v in vals if v in ("WIN", "win", True) or (isinstance(v, (int, float)) and v > 0))
            print(f"      {b}: n={len(vals)} win_rate={hit / len(vals) * 100:.1f}%")

    lifecycle = datasets.get("trade_lifecycle") or []
    if lifecycle:
        stages = {}
        for r in lifecycle:
            if r.get("schema") != "trade_lifecycle_v1":
                continue
            stage = r.get("entry_stage") or "UNKNOWN"
            pnl = float(r.get("net_pnl_usd") or 0)
            stages.setdefault(stage, []).append(pnl)
        print(f"  trade_lifecycle entry stages: {len(stages)} {PIPELINE_ENFORCEMENT_TAG}")
        for stage, pnls in sorted(stages.items()):
            wins = sum(1 for p in pnls if p > 0)
            avg = sum(pnls) / len(pnls) if pnls else 0
            print(f"      {stage}: n={len(pnls)} win_rate={wins / len(pnls) * 100:.1f}% avg_pnl=${avg:.2f}")

    reasons = datasets.get("ai_reason_research") or []
    if reasons:
        unique_ids = {r.get("trade_id") for r in reasons if r.get("trade_id")}
        decisions = defaultdict(int)
        for r in reasons:
            if r.get("schema") == "ai_reason_v1":
                decisions[r.get("ai_decision") or "?"] += 1
        print(
            f"  ai_reason_research: n={len(reasons)} unique_trade_ids={len(unique_ids)} "
            f"decisions={dict(decisions)} {PIPELINE_ENFORCEMENT_TAG}"
        )
        outcomes = [r for r in reasons if r.get("schema") == "ai_reason_outcome_v1"]
        if outcomes:
            wins = sum(1 for r in outcomes if str(r.get("outcome", "")).upper() == "WIN")
            print(f"  ai_reason outcomes: n={len(outcomes)} win_rate={wins / len(outcomes) * 100:.1f}% {PIPELINE_ENFORCEMENT_TAG}")

    lifecycle = datasets.get("trade_lifecycle") or []
    if lifecycle:
        regimes = {r.get("market_regime") for r in lifecycle if r.get("schema") == "trade_lifecycle_v1"}
        trends = {r.get("trend_health_state") for r in lifecycle if r.get("schema") == "trade_lifecycle_v1"}
        print(f"  trade_lifecycle regimes={sorted(x for x in regimes if x)} trend_health={sorted(x for x in trends if x)} {PIPELINE_ENFORCEMENT_TAG}")


def _load_shadow_outcome_df(session: dict = None):
    """Load shadow_outcome.jsonl (fallback counterfactual.jsonl) with optional session filter."""
    shadow = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    counter = _load_jsonl_by_trade_id(COUNTERFACTUAL_FILE)
    if not shadow and not counter:
        return None
    work = shadow if shadow else counter
    df = pd.DataFrame(list(work.values()))
    if df.empty:
        return df
    if session and _session_start_ts(session) is not None:
        before = len(df)
        df = filter_df_since_session(df, session, ts_cols=("ts", "timestamp"))
        if len(df) == 0 and before > 0:
            print(
                f"   No shadow rows in current session (file has {before} all-time rows). "
                f"Run bot longer or disable session filter for all-time shadow review. {PIPELINE_ENFORCEMENT_TAG}"
            )
    return df


def _load_v2_shadow_outcome_df(session: dict = None):
    """A160 V2 tile-OFF simulated fills/exits (v2_shadow_outcome.jsonl)."""
    rows = _load_jsonl_rows(V2_SHADOW_OUTCOME_FILE)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if session and _session_start_ts(session) is not None:
        df = filter_df_since_session(df, session, ts_cols=("ts", "timestamp"))
    return df


def _load_v2_checker_approves_df(session: dict = None):
    """V2 checker accepts — independent of AI_SCAN signal_snapshot approves."""
    rows = _load_jsonl_rows(V2_CHECKER_LOG_FILE)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "accepted" in df.columns:
        df = df[df["accepted"].apply(_truthy)]
    if session and _session_start_ts(session) is not None:
        df = filter_df_since_session(df, session, ts_cols=("ts", "timestamp"))
    return df


def _v2_outcome_is_reject_counterfactual(row) -> bool:
    """Reject-path counterfactual sims must not count as checker-pass paper fills."""
    if hasattr(row, "get"):
        mode = str(row.get("collection_mode") or "").upper()
        checker_accepted = row.get("checker_accepted", True)
    else:
        mode = str((row or {}).get("collection_mode") or "").upper()
        checker_accepted = (row or {}).get("checker_accepted", True)
    if mode == "V2_CHECKER_REJECT":
        return True
    if mode == "V2_SHADOW":
        return False
    return not _truthy(checker_accepted)


def _v2_lane_metrics_from_logs(session: dict = None) -> dict:
    """Aggregate V2 checker approves + shadow outcomes for benchmark_vs_lanes."""
    checker = _load_v2_checker_approves_df(session)
    outcomes = _load_v2_shadow_outcome_df(session)
    approves = len(checker) if checker is not None and not checker.empty else 0
    checker_pass_sims = 0
    checker_pass_pnl = 0.0
    wins = 0
    reject_counterfactual_sims = 0
    reject_counterfactual_pnl = 0.0
    if outcomes is not None and not outcomes.empty:
        work = outcomes.copy()
        if "filled" in work.columns:
            work["filled"] = work["filled"].apply(_truthy)
        else:
            work["filled"] = True
        reject_mask = work.apply(_v2_outcome_is_reject_counterfactual, axis=1)
        reject_work = work[reject_mask]
        work = work[~reject_mask]
        filled = work[work["filled"]]
        checker_pass_sims = len(filled)
        checker_pass_pnl = round(
            float(pd.to_numeric(filled.get("net_pnl_usd"), errors="coerce").fillna(0).sum()), 2
        )
        pnl_series = pd.to_numeric(filled.get("net_pnl_usd"), errors="coerce").fillna(0)
        wins = int((pnl_series >= 0).sum())
        reject_filled = reject_work[reject_work["filled"]] if not reject_work.empty else reject_work
        reject_counterfactual_sims = len(reject_filled)
        reject_counterfactual_pnl = round(
            float(pd.to_numeric(reject_filled.get("net_pnl_usd"), errors="coerce").fillna(0).sum()), 2
        ) if reject_counterfactual_sims else 0.0
    per_ev = round(checker_pass_pnl / approves, 2) if approves else 0.0
    win_rate = round(100.0 * wins / checker_pass_sims, 1) if checker_pass_sims else 0.0
    return {
        "approves": approves,
        "checker_pass_sims": checker_pass_sims,
        "checker_pass_pnl": checker_pass_pnl,
        "sim_fills": checker_pass_sims,
        "sim_pnl": checker_pass_pnl,
        "per_approve_ev": per_ev,
        "win_rate_pct": win_rate,
        "reject_counterfactual_sims": reject_counterfactual_sims,
        "reject_counterfactual_pnl": reject_counterfactual_pnl,
        "reject_sim_fills": reject_counterfactual_sims,
        "reject_sim_pnl": reject_counterfactual_pnl,
    }


def _load_shadow_lane_outcome_df(session: dict = None):
    """Off-dashboard shadow-collecting lane sim outcomes (no live orders)."""
    rows = _load_jsonl_rows(SHADOW_LANE_OUTCOME_FILE)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if session and _session_start_ts(session) is not None:
        df = filter_df_since_session(df, session, ts_cols=("ts", "timestamp"))
    return df


def _shadow_scope_label(session: dict = None) -> str:
    if session and _session_start_ts(session) is not None:
        if session.get("fresh_collection_mode"):
            return "FRESH-COLLECTION"
        return "SESSION"
    return "ALL-TIME"


def _shadow_block_prefix(br) -> str:
    """Classify block_reason prefix for REAL EDGE / missed-opportunity subtotals."""
    s = str(br or "")
    if s.startswith("WOULD_BLOCK"):
        return "WOULD_BLOCK"
    if s.startswith("WOULD_FAIL"):
        return "WOULD_FAIL"
    if s.startswith("PROFIT_GATE"):
        return "PROFIT_GATE"
    if s.startswith("CLUSTER"):
        return "CLUSTER"
    if s.startswith("SOFT_REJECT"):
        return "SOFT_REJECT"
    return "other"


def _is_blocked_approve_lane(br) -> bool:
    """Hard APPROVE gates included in REAL EDGE blocked shadow sum."""
    s = str(br or "")
    return (
        s.startswith("WOULD_BLOCK")
        or s.startswith("WOULD_FAIL")
        or s.startswith("PROFIT_GATE")
        or s.startswith("CLUSTER")
    )


def _assign_shadow_lane(block_reason_series: pd.Series) -> pd.Series:
    br = block_reason_series.astype(str)
    return np.where(
        br.apply(_is_blocked_approve_lane),
        "blocked_approve",
        np.where(br.str.startswith("SOFT_REJECT"), "soft_reject", "other"),
    )


def _print_block_prefix_subtotals(df: pd.DataFrame, pnl_col: str = "net_pnl_usd", label: str = "Blocked APPROVE"):
    """Subtotals by WOULD_BLOCK / WOULD_FAIL / CLUSTER / PROFIT_GATE for REAL EDGE reporting."""
    if df is None or df.empty or "block_reason" not in df.columns:
        return
    work = df.copy()
    work["_prefix"] = work["block_reason"].map(_shadow_block_prefix)
    work[pnl_col] = pd.to_numeric(work.get(pnl_col), errors="coerce")
    rows = []
    for prefix in ("WOULD_BLOCK", "WOULD_FAIL", "CLUSTER", "PROFIT_GATE", "other"):
        sub = work[work["_prefix"] == prefix]
        if sub.empty:
            continue
        rows.append({
            "prefix": prefix,
            "n": len(sub),
            "sum_pnl": round(sub[pnl_col].sum(), 2),
            "avg_pnl": round(sub[pnl_col].mean(), 2),
        })
    if not rows:
        return
    print(f"\n{label} subtotals by block prefix:")
    print(pd.DataFrame(rows).to_string(index=False))
    print(PIPELINE_ENFORCEMENT_TAG)


def _warn_if_low_replay_n(replay_n: int, label: str = "exit sweep"):
    if replay_n < 15:
        print(
            f"  ⚠️ Low replay sample (n={replay_n} < 15) — {label} results may be unstable. "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )


def _infer_fill_missed_by_usd(row: dict):
    """Derive missed_by_usd from closest/limit or min/max excursion when null."""
    missed = row.get("missed_by_usd")
    if missed is not None and pd.notna(missed):
        return float(missed)
    direction = str(row.get("direction") or row.get("dir") or "").upper()
    limit_price = row.get("limit_price")
    if limit_price is not None and pd.notna(limit_price):
        limit_price = float(limit_price)
    else:
        limit_price = None
    closest = row.get("closest_price")
    if closest is not None and pd.notna(closest) and limit_price is not None:
        closest = float(closest)
        if direction == "LONG":
            return round(max(0.0, limit_price - closest), 2)
        if direction == "SHORT":
            return round(max(0.0, closest - limit_price), 2)
    min_p = row.get("min_price_since_order")
    max_p = row.get("max_price_since_order")
    if limit_price is not None and min_p is not None and max_p is not None:
        min_p = float(min_p)
        max_p = float(max_p)
        if direction == "LONG":
            return round(max(0.0, limit_price - min_p), 2)
        if direction == "SHORT":
            return round(max(0.0, max_p - limit_price), 2)
    return None


def _print_shadow_pnl_summary(blocked_shadow: pd.DataFrame, label: str):
    """Print fill stats + missed-winner totals for one shadow subset."""
    if blocked_shadow is None or blocked_shadow.empty:
        print(f"  {label}: no rows {PIPELINE_ENFORCEMENT_TAG}")
        return
    filled = blocked_shadow[blocked_shadow["filled"] == True]
    no_fill = blocked_shadow[blocked_shadow["filled"] != True]
    print(f"  {label}: n={len(blocked_shadow)} | filled={len(filled)} | NO_FILL={len(no_fill)} {PIPELINE_ENFORCEMENT_TAG}")
    filled_pnl = pd.to_numeric(filled.get("net_pnl_usd"), errors="coerce")
    missed_winners = filled[filled_pnl > 0]
    good_blocks = filled[filled_pnl <= 0]
    print(
        f"    Missed winners (filled, shadow +$): {len(missed_winners)} sum=${filled_pnl[filled_pnl > 0].sum():.2f} | "
        f"Good blocks (filled, shadow ≤$0): {len(good_blocks)} sum=${filled_pnl[filled_pnl <= 0].sum():.2f} | "
        f"Net filled shadow PnL: ${filled_pnl.sum():.2f} {PIPELINE_ENFORCEMENT_TAG}"
    )
    if len(no_fill) > 0:
        print(f"    NO_FILL (excluded from missed-$): {len(no_fill)} — check mfe/post_block separately {PIPELINE_ENFORCEMENT_TAG}")
    if len(missed_winners) > 0 and "block_reason" in missed_winners.columns:
        print(f"    Top missed-winner reasons:\n{missed_winners['block_reason'].value_counts().head(5).to_string()} {PIPELINE_ENFORCEMENT_TAG}")


def shadow_approve_pnl_analysis(decisions, trades, blocked, session: dict = None):
    """Shadow counterfactual PnL — blocked APPROVE gates vs soft-reject lanes."""
    scope = _shadow_scope_label(session)
    print(f"\n=== SHADOW PnL ({scope}) — counterfactual execution ===")
    df = _load_shadow_outcome_df(session)
    if df is None:
        print(
            f"No shadow_outcome.jsonl / counterfactual.jsonl yet — restart bot on "
            f"{EXPECTED_BOT_VERSION} and collect APPROVE signals. {PIPELINE_ENFORCEMENT_TAG}"
        )
        return None
    if df.empty:
        print(f"Shadow file empty for {scope.lower()} scope. {PIPELINE_ENFORCEMENT_TAG}")
        return df
    df["net_pnl_usd"] = pd.to_numeric(df.get("net_pnl_usd"), errors="coerce")
    df["filled"] = df.get("filled", True)
    df["ai_win_prob"] = pd.to_numeric(df.get("ai_win_prob"), errors="coerce")
    if "block_reason" in df.columns:
        df["shadow_lane"] = _assign_shadow_lane(df["block_reason"])
    else:
        df["shadow_lane"] = "other"
    executed_ids = set(trades["trade_id"].dropna()) if not trades.empty and "trade_id" in trades.columns else set()
    df["outcome_lane"] = df["trade_id"].apply(lambda x: "EXECUTED" if x in executed_ids else "BLOCKED_SHADOW")
    blocked_shadow = df[df["outcome_lane"] == "BLOCKED_SHADOW"]
    print(f"Shadow rows ({scope.lower()}): {len(df)} | non-executed shadows: {len(blocked_shadow)} {PIPELINE_ENFORCEMENT_TAG}")
    if blocked_shadow.empty:
        print(f"No shadow rows to analyze. {PIPELINE_ENFORCEMENT_TAG}")
        return df
    gate_shadow = blocked_shadow[blocked_shadow["shadow_lane"] == "blocked_approve"]
    soft_shadow = blocked_shadow[blocked_shadow["shadow_lane"] == "soft_reject"]
    other_shadow = blocked_shadow[blocked_shadow["shadow_lane"] == "other"]
    print(
        f"  Split: blocked APPROVE gates (WOULD_BLOCK/WOULD_FAIL/CLUSTER/PROFIT_GATE): {len(gate_shadow)} | "
        f"soft rejects (SOFT_REJECT:*): {len(soft_shadow)} | other: {len(other_shadow)} {PIPELINE_ENFORCEMENT_TAG}"
    )
    if len(soft_shadow) > 0:
        print(
            f"  ℹ️ SOFT_REJECT shadows simulate AI-rejected signals — not blocked APPROVEs. "
            f"Use WOULD_BLOCK_* rows for gate-cost analysis. {PIPELINE_ENFORCEMENT_TAG}"
        )
    _print_shadow_pnl_summary(gate_shadow, "Blocked APPROVE gates (WOULD_BLOCK/WOULD_FAIL/CLUSTER/PROFIT_GATE)")
    if not soft_shadow.empty:
        _print_shadow_pnl_summary(soft_shadow, "Soft rejects (SOFT_REJECT:*)")
    if not other_shadow.empty:
        _print_shadow_pnl_summary(other_shadow, "Other shadow lanes")
    if "block_reason" in blocked_shadow.columns and not gate_shadow.empty:
        print("\nShadow PnL by block reason (blocked APPROVE lanes only):")
        grp = gate_shadow.groupby("block_reason").agg(
            n=("trade_id", "count"),
            sum_pnl=("net_pnl_usd", "sum"),
            avg_pnl=("net_pnl_usd", "mean"),
            win_rate=("net_pnl_usd", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean()),
        ).round(2)
        print(grp.to_string())
        _print_block_prefix_subtotals(gate_shadow, label="Shadow PnL")
        print(PIPELINE_ENFORCEMENT_TAG)
    return df


def real_edge_report(trades, decisions, shadow_df=None):
    """True APPROVE funnel edge: executed PnL + blocked shadow PnL vs counterfactual all-in."""
    print("\n=== REAL EDGE REPORT (APPROVE funnel true EV) ===")
    if decisions.empty:
        print(f"No decisions. {PIPELINE_ENFORCEMENT_TAG}")
        return {}
    approves = decisions[decisions.get("reason", pd.Series()) == "APPROVE"]["trade_id"].dropna().unique()
    if len(approves) == 0:
        print(f"No APPROVE rows. {PIPELINE_ENFORCEMENT_TAG}")
        return {}
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
        if "shadow_lane" in bs.columns:
            bs = bs[bs["shadow_lane"] == "blocked_approve"]
        shadow_n = len(bs)
        shadow_pnl = pd.to_numeric(bs.get("net_pnl_usd"), errors="coerce").sum()
    total_approve = len(approves)
    blocked_n = total_approve - executed_n
    counterfactual_all = executed_pnl + shadow_pnl
    actual_only = executed_pnl
    print(f"APPROVE attempts: {total_approve} | executed: {executed_n} | blocked: {blocked_n} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Executed net PnL: ${actual_only:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Blocked shadow net PnL (if all had traded): ${shadow_pnl:.2f} ({shadow_n} shadows) {PIPELINE_ENFORCEMENT_TAG}")
    if shadow_n > 0 and "block_reason" in bs.columns:
        _print_block_prefix_subtotals(bs, label="REAL EDGE blocked shadow")
    print(f"Counterfactual all-APPROVE sum: ${counterfactual_all:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if total_approve > 0:
        print(f"Per-APPROVE EV (counterfactual): ${counterfactual_all / total_approve:.2f} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Per-APPROVE EV (executed only): ${actual_only / max(1, executed_n):.2f} on {executed_n} trades {PIPELINE_ENFORCEMENT_TAG}")
    if shadow_n > 0 and blocked_n > 0:
        filled_bs = bs[bs.get("filled") == True] if "filled" in bs.columns else bs
        block_quality = (pd.to_numeric(filled_bs.get("net_pnl_usd"), errors="coerce") <= 0).mean()
        print(
            f"Block quality (% filled blocked-APPROVE shadows ≤$0): {block_quality * 100:.1f}% "
            f"({len(filled_bs)} filled) {PIPELINE_ENFORCEMENT_TAG}"
        )
    if counterfactual_all > actual_only:
        print(f"⚠️ Gates cost ~${counterfactual_all - actual_only:.2f} vs trading every APPROVE (shadow). Review block reasons. {PIPELINE_ENFORCEMENT_TAG}")
    elif counterfactual_all < actual_only:
        print(f"✅ Gates saved ~${actual_only - counterfactual_all:.2f} vs trading every APPROVE. {PIPELINE_ENFORCEMENT_TAG}")

    summary = {
        "schema": "real_edge_summary_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "approve_attempts": int(total_approve),
        "executed": int(executed_n),
        "blocked": int(blocked_n),
        "executed_pnl_usd": round(float(actual_only), 2),
        "blocked_shadow_pnl_usd": round(float(shadow_pnl), 2),
        "counterfactual_all_approve_usd": round(float(counterfactual_all), 2),
        "gate_damage_usd": round(float(counterfactual_all - actual_only), 2),
        "per_approve_ev_executed": round(actual_only / max(1, executed_n), 2),
        "per_approve_ev_counterfactual": round(counterfactual_all / max(1, total_approve), 2),
    }
    try:
        with open(REAL_EDGE_SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
    except Exception:
        pass
    return summary


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
    """Tick walk: fast cut + profit ladder only (no thesis flip — needs live scores).
    Replay ticks from bot v1.1.18+ use executable depth marks; older ticks used last trade."""
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
        unreal = tick.get("unreal_pct")
        if unreal is None:
            unreal = ((price - entry) / entry) * dir_factor * leverage * 100
        else:
            unreal = float(unreal)
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

    _warn_if_low_replay_n(len(rows), "thesis stop wide sweep")

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
    _warn_if_low_replay_n(replay_count, "stop × ladder 2D sweep")
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
    replay_n = int(sweep["replays"].iloc[0]) if not sweep.empty and "replays" in sweep.columns else len(replays)
    print(f"Replays simulated: {replay_n} {PIPELINE_ENFORCEMENT_TAG}")
    _warn_if_low_replay_n(replay_n, "stop × ladder × MFE 3D sweep")
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
        ("trades_3factor.csv", TRADES_FILE, "required", [
            "max_profit", "max_drawdown", "exit_reason", "pnl", "net_pnl_usd", "margin_usdt",
            "entry", "price_at_signal", "entry_delay", "entry_delay_sec", "signal_age_sec",
            "research_lane", "entry_path", "fill_model", "signal_age_bucket",
        ]),
        ("decisions_3factor.csv", DECISIONS_FILE, "required", ["trade_id", "decision", "reason", "edge_score"]),
        ("blocked_signals_3factor.csv", BLOCKED_FILE, "funnel", ["trade_id", "reason"]),
        ("ai_tranche_log.csv", AI_TRANCHE_FILE, "funnel", ["trade_id", "decision"]),
        (EXECUTION_FUNNEL_FILE, EXECUTION_FUNNEL_FILE, "funnel", ["trade_id", "stage", "research_lane", "entry_path"]),
        (EXECUTION_FUNNEL_SUMMARY_FILE, EXECUTION_FUNNEL_SUMMARY_FILE, "funnel", ["approve_count", "filled_count"]),
        (FILL_QUALITY_REPORT_FILE, FILL_QUALITY_REPORT_FILE, "funnel", ["buckets"]),
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
        (REVERSAL_STUDY_FILE, REVERSAL_STUDY_FILE, "research_jsonl", ["trade_id", "reversal_risk", "phase"]),
        (AI_REASON_RESEARCH_FILE, AI_REASON_RESEARCH_FILE, "research_jsonl", ["trade_id", "reasons_for", "reasons_against"]),
        (AI_CONFIDENCE_CALIBRATION_FILE, AI_CONFIDENCE_CALIBRATION_FILE, "research_jsonl", ["prob_bucket", "actual"]),
        (TRADE_LIFECYCLE_FILE, TRADE_LIFECYCLE_FILE, "research_jsonl", ["trade_id", "entry_stage", "net_pnl_usd"]),
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


def _edge_decile_bucket(edge):
    """Whole-point deciles for edge validation (0-1, 1-2, …, 4+)."""
    e = float(edge or 0)
    if e < 1.0:
        return "0-1"
    if e < 2.0:
        return "1-2"
    if e < 3.0:
        return "2-3"
    if e < 4.0:
        return "3-4"
    return "4+"


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


def _peak_mfe_bucket_val(mfe):
    try:
        v = float(mfe)
    except (TypeError, ValueError):
        return "unknown"
    if np.isnan(v):
        return "unknown"
    if v < 15:
        return "0-14"
    if v < 25:
        return "15-24"
    if v < 35:
        return "25-34"
    if v < 50:
        return "35-49"
    if v < 75:
        return "50-74"
    return "75+"


def _time_in_trade_bucket_val(sec):
    try:
        v = float(sec)
    except (TypeError, ValueError):
        return "unknown"
    if np.isnan(v):
        return "unknown"
    mins = v / 60.0
    if mins < 10:
        return "0-10m"
    if mins < 20:
        return "10-20m"
    if mins < 40:
        return "20-40m"
    if mins < 60:
        return "40-60m"
    return "60m+"


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
    work["entry_delay_min"] = _normalize_entry_delay_min(work)
    if "peak_mfe_bucket" not in work.columns:
        mfe_src = work.get("mfe_margin_pct", work.get("max_profit"))
        work["peak_mfe_bucket"] = pd.to_numeric(mfe_src, errors="coerce").apply(_peak_mfe_bucket_val)
    if "time_in_trade_bucket" not in work.columns:
        dur = pd.to_numeric(work.get("outcome_duration_sec"), errors="coerce")
        work["time_in_trade_bucket"] = dur.apply(_time_in_trade_bucket_val)
    if "entry_mode_bucket" not in work.columns and "research_lane" in work.columns:
        work["entry_mode_bucket"] = work["research_lane"].apply(
            lambda ln: "CONTINUOUS"
            if str(ln).upper().endswith("_DIRECT") or str(ln).upper().endswith("DIRECT")
            else ("CHASE_3PLUS" if "CHASE" in str(ln).upper() else "OTHER")
        )
    return work


def _aggregate_lane_metric_blocks(blocks: list) -> dict:
    """Sum lane metric dicts (approves, fills, pnl) for CONTINUOUS proxy."""
    if not blocks:
        return {}
    approves = sum(int(b.get("approves") or 0) for b in blocks)
    real_fills = sum(int(b.get("real_fills") or 0) for b in blocks)
    shadow_filled = sum(int(b.get("shadow_filled") or 0) for b in blocks)
    net_pnl_real = round(sum(float(b.get("net_pnl_real") or 0) for b in blocks), 2)
    net_pnl_shadow_blocked = round(sum(float(b.get("net_pnl_shadow_blocked") or 0) for b in blocks), 2)
    costly = round(sum(float(b.get("costly_blocks_usd") or 0) for b in blocks), 2)
    good_saved = round(sum(float(b.get("good_blocks_saved_usd") or 0) for b in blocks), 2)
    approve_to_fill_pct = round(100.0 * real_fills / approves, 1) if approves else 0.0
    shadow_fill_pct = round(100.0 * shadow_filled / approves, 1) if approves else 0.0
    per_approve_ev = round((net_pnl_real + net_pnl_shadow_blocked) / approves, 2) if approves else 0.0
    return {
        "approves": approves,
        "real_fills": real_fills,
        "approve_to_fill_pct": approve_to_fill_pct,
        "shadow_filled": shadow_filled,
        "shadow_fill_pct": shadow_fill_pct,
        "net_pnl_real": net_pnl_real,
        "net_pnl_shadow_blocked": net_pnl_shadow_blocked,
        "per_approve_ev": per_approve_ev,
        "costly_blocks_usd": costly,
        "good_blocks_saved_usd": good_saved,
    }


def _inject_continuous_benchmark_lane(lane_metrics: dict, lanes_ordered: list) -> None:
    """CONTINUOUS yardstick = aggregate of immediate-entry (Direct) COMBO lanes."""
    proxy_lanes = tuple(CONTINUOUS_PROXY_LANES)
    bench_lane = COMPARISON_BENCHMARK_LANE or BENCHMARK_LANE
    existing = lane_metrics.get(bench_lane) or {}
    if int(existing.get("real_fills") or 0) > 0 or int(existing.get("approves") or 0) > 0:
        return
    parts = [lane_metrics.get(ln) for ln in proxy_lanes if lane_metrics.get(ln)]
    agg = _aggregate_lane_metric_blocks(parts)
    if not agg.get("approves") and not agg.get("real_fills"):
        return
    lane_metrics[bench_lane] = agg
    if bench_lane not in lanes_ordered:
        lanes_ordered.insert(0, bench_lane)


def _normalize_entry_delay_min(df: pd.DataFrame) -> pd.Series:
    """Prefer entry_delay_sec / signal_age_sec; detect legacy entry_delay seconds vs minutes."""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if "entry_delay_sec" in df.columns:
        sec = pd.to_numeric(df["entry_delay_sec"], errors="coerce")
        if sec.notna().any():
            return sec / 60.0
    if "signal_age_sec" in df.columns:
        sec = pd.to_numeric(df["signal_age_sec"], errors="coerce")
        if sec.notna().any():
            return sec / 60.0
    if "execution_fill_delay_sec" in df.columns:
        sec = pd.to_numeric(df["execution_fill_delay_sec"], errors="coerce")
        if sec.notna().any():
            mean_v = sec.mean()
            if pd.notna(mean_v) and mean_v < 200:
                return sec / 60.0
            return sec
    if "entry_delay" not in df.columns:
        return pd.Series([np.nan] * len(df), index=df.index)
    ed = pd.to_numeric(df["entry_delay"], errors="coerce")
    if ed.notna().any():
        mean_v = ed.mean()
        if pd.notna(mean_v) and mean_v < 200:
            return ed / 60.0
    return ed


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
    """v88: per-lane live execution — CONTINUOUS + spawn lanes tagged PnL."""
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


def pathway_matrix_report(trades, snapshots: dict = None):
    """Compare research_lane × entry_path (or entry_mode) for pathway edge."""
    print("\n=== PATHWAY MATRIX (research_lane × entry_path) ===")
    print(f"  Expected bot: {EXPECTED_BOT_VERSION} | analyzer: {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG}")
    if trades is None or trades.empty:
        print(f"  No executed trades yet. {PIPELINE_ENFORCEMENT_TAG}")
    else:
        work = trades.copy()
        work["net"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
        work["entry_delay_min"] = _normalize_entry_delay_min(work)
        if "signal_age_sec" not in work.columns and "entry_delay_sec" in work.columns:
            work["signal_age_sec"] = pd.to_numeric(work["entry_delay_sec"], errors="coerce")
        elif "signal_age_sec" not in work.columns:
            work["signal_age_sec"] = work["entry_delay_min"] * 60.0
        path_col = "entry_path" if "entry_path" in work.columns else "entry_mode"
        if path_col not in work.columns:
            print(f"  ⚠️ trades missing entry_path/entry_mode — restart bot on {EXPECTED_BOT_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
        elif "research_lane" not in work.columns:
            print(f"  ⚠️ trades missing research_lane — restart bot on {EXPECTED_BOT_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
        else:
            g = (
                work.groupby(["research_lane", path_col], observed=True)
                .agg(
                    n=("trade_id", "count"),
                    wr=("net", lambda x: (x > 0).mean() * 100 if x.notna().any() else 0),
                    sum_net=("net", "sum"),
                    avg_net=("net", "mean"),
                    avg_signal_age_sec=("signal_age_sec", "mean"),
                )
                .round(2)
            )
            print(g.to_string())
            if "fill_model" in work.columns:
                print("\n  --- By fill_model ---")
                fm = (
                    work.groupby("fill_model", observed=True)
                    .agg(n=("trade_id", "count"), wr=("net", lambda x: (x > 0).mean() * 100), sum=("net", "sum"))
                    .round(2)
                )
                print(fm.to_string())
            if "exit_reason" in work.columns and path_col in work.columns and "research_lane" in work.columns:
                print("\n  --- Lane × entry_path × exit_reason (top exits) ---")
                cube = (
                    work.groupby(["research_lane", path_col, "exit_reason"], observed=True)
                    .agg(n=("trade_id", "count"), sum_net=("net", "sum"), wr=("net", lambda x: (x > 0).mean() * 100))
                    .round(2)
                    .sort_values("n", ascending=False)
                )
                print(cube.head(24).to_string())
        if "signal_age_bucket" in work.columns:
            print("\n  --- By signal_age_bucket ---")
            sb = (
                work.groupby("signal_age_bucket", observed=True)
                .agg(n=("trade_id", "count"), wr=("net", lambda x: (x > 0).mean() * 100), sum=("net", "sum"))
                .round(2)
            )
            print(sb.to_string())
    if snapshots:
        exec5m = [
            s for s in snapshots.values()
            if s.get("research_lane") == "EXEC_5M"
        ]
        if exec5m:
            has_5m = sum(
                1 for s in exec5m
                if s.get("5m_higher_low") is not None or s.get("5m_lower_high") is not None
            )
            print(f"\n  EXEC_5M snapshots: {len(exec5m)} | with 5m HL/LH fields: {has_5m}/{len(exec5m)} {PIPELINE_ENFORCEMENT_TAG}")
            if has_5m < len(exec5m):
                print(f"  ⚠️ Some EXEC_5M snapshots pre-v1.1.1 — restart bot on {EXPECTED_BOT_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
        else:
            print(f"\n  No spawn-lane snapshots yet — lanes spawn on CONTINUOUS APPROVE. {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def _pathway_column_coverage(trades: pd.DataFrame) -> None:
    """Report pathway telemetry columns populated on executed trades."""
    print("\n=== PATHWAY COLUMN COVERAGE (trades_3factor.csv) ===")
    if trades is None or trades.empty:
        print(f"  No trades yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    n = len(trades)
    for col in PATHWAY_TRADE_COLUMNS:
        if col not in trades.columns:
            print(f"  ⚠️ missing column: {col} — restart bot on {EXPECTED_BOT_VERSION} {PIPELINE_ENFORCEMENT_TAG}")
            continue
        filled = trades[col].notna().sum()
        if col in ("research_lane", "entry_path", "fill_model"):
            uniq = trades[col].dropna().astype(str).nunique()
            print(f"  {col}: {filled}/{n} populated | unique={uniq} {PIPELINE_ENFORCEMENT_TAG}")
        else:
            print(f"  {col}: {filled}/{n} populated {PIPELINE_ENFORCEMENT_TAG}")


def execution_funnel_report(trades=None):
    """APPROVE→ORDER→FILL funnel with SIGNAL_EXPIRED vs ORDER_EXPIRED by lane."""
    print("\n=== EXECUTION FUNNEL (APPROVE → ORDER → FILL) ===")
    print(f"  Expected bot: {EXPECTED_BOT_VERSION} | analyzer: {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG}")
    summary_path = EXECUTION_FUNNEL_SUMMARY_FILE
    funnel_path = EXECUTION_FUNNEL_FILE
    summary = {}
    if os.path.isfile(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                summary = json.load(f)
        except Exception as e:
            print(f"  ⚠️ could not read {summary_path}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    if summary:
        print(
            f"  approves={summary.get('approve_count')} orders={summary.get('order_submitted_count')} "
            f"touches={summary.get('price_touch_count')} fills={summary.get('filled_count')} "
            f"closed={summary.get('closed_count')} {PIPELINE_ENFORCEMENT_TAG}"
        )
        print(
            f"  approve→order={summary.get('approval_to_order_rate_pct')}% "
            f"order→fill={summary.get('order_to_fill_rate_pct')}% "
            f"approve→fill={summary.get('approve_to_fill_rate_pct')}% {PIPELINE_ENFORCEMENT_TAG}"
        )
        print(
            f"  signal_expired={summary.get('signal_expired_count', 0)} "
            f"order_expired={summary.get('order_expired_count', 0)} {PIPELINE_ENFORCEMENT_TAG}"
        )
        if summary.get("terminal_reasons"):
            print(f"  terminal_reasons: {summary.get('terminal_reasons')} {PIPELINE_ENFORCEMENT_TAG}")
        if summary.get("by_research_lane"):
            print(f"  by_research_lane: {summary.get('by_research_lane')} {PIPELINE_ENFORCEMENT_TAG}")
    elif not os.path.isfile(funnel_path):
        print(f"  No {funnel_path} yet — restart bot on {EXPECTED_BOT_VERSION}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    rows = []
    if os.path.isfile(funnel_path):
        with open(funnel_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    if not rows:
        print(f"  {funnel_path} empty. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = pd.DataFrame(rows)
    if "stage" not in df.columns:
        return
    print("\n  --- Funnel stages ---")
    print(df["stage"].value_counts().to_string())
    for stage in ("SIGNAL_EXPIRED", "ORDER_EXPIRED", "CAPACITY_REJECTED"):
        sub = df[df["stage"] == stage]
        if sub.empty:
            continue
        print(f"\n  --- {stage} ---")
        if "research_lane" in sub.columns:
            print(sub["research_lane"].value_counts(dropna=False).to_string())
        if "entry_path" in sub.columns:
            print(sub["entry_path"].value_counts(dropna=False).head(8).to_string())
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        filled_ids = set(df[df["stage"] == "FILLED"]["trade_id"].astype(str))
        trade_ids = set(trades["trade_id"].astype(str))
        orphan_fills = filled_ids - trade_ids
        if orphan_fills:
            print(f"\n  ⚠️ {len(orphan_fills)} funnel FILLED ids missing from trades CSV {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)


def ai_stability_research_report(trades, snapshots: dict):
    """v87/v88: DeepSeek vs replay scorecard vs executed PnL (all research lanes)."""
    print("\n=== V87/V88 AI INPUT + REPLAY REPORT ===")
    df = _load_ai_input_log()
    if df.empty:
        print(f"No {AI_INPUT_LOG_FILE} yet — restart bot on {EXPECTED_BOT_VERSION}. {PIPELINE_ENFORCEMENT_TAG}")
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
    else:
        work["net"] = np.nan
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
    _pathway_column_coverage(trades)
    pathway_matrix_report(trades, snapshots)
    execution_funnel_report(trades)
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
        if "bot_version" in analysis_df.columns or "book_slippage_usd_total" in analysis_df.columns:
            era_df = _realism_era_summary(_attach_realism_era(analysis_df))
            if not era_df.empty:
                depth = era_df[era_df["sim_era"] == "DEPTH_REALISM"]
                print(f"\nSim realism eras:")
                for _, row in era_df.iterrows():
                    print(f"  {row['label']}: {int(row['trades'])} trades WR={row['win_rate_pct']}% net=${row['sum_net_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}")
                if depth.empty:
                    print(f"  ⚠️ No depth-realism trades — headline WR/PnL may be inflated vs live. {PIPELINE_ENFORCEMENT_TAG}")

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
    if df is None or df.empty:
        return
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    if df["entry_delay_min"].notna().sum() == 0:
        print(f"No entry_delay / entry_delay_sec column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df["delay_bucket"] = pd.cut(df["entry_delay_min"], bins=[0, 5, 10, 15, 30, 60, 120])
    stats = df.groupby("delay_bucket", observed=True)["net_pnl_usd"].agg(["mean", "count", "sum"]).round(2)
    print(stats)

def signal_execution_gap(df):
    print("\n=== SIGNAL → EXECUTION GAP ===")
    if df is None or df.empty:
        return
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    if df["entry_delay_min"].notna().sum() == 0:
        return
    fast = df[df["entry_delay_min"] < 10].get("net_pnl_usd", pd.Series(0)).mean()
    slow = df[df["entry_delay_min"] > 30].get("net_pnl_usd", pd.Series(0)).mean()
    print(f"Fast entries (<10min) avg PnL : {fast:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Slow entries (>30min) avg PnL : {slow:.2f} {PIPELINE_ENFORCEMENT_TAG}")

def _bot_version_era(bot_version) -> str:
    """Classify trade sim realism from bot_version string."""
    bv = str(bot_version or "").strip().lower()
    if not bv:
        return "LEGACY_LAST_PRICE"
    if "data-collection" in bv or "v1.1.20" in bv:
        return "DEPTH_REALISM"
    if "profit-gates" in bv or "v1.1.19" in bv:
        return "DEPTH_REALISM"
    if "realism-complete" in bv or "v1.1.18" in bv:
        return "DEPTH_REALISM"
    if "book-depth" in bv or "v1.1.17" in bv:
        return "BBO_ONLY"
    if "bbo" in bv or "v1.1.16" in bv:
        return "BBO_ONLY"
    m = re.search(r"v1\.1\.(\d+)", bv)
    if m:
        minor = int(m.group(1))
        if minor >= 18:
            return "DEPTH_REALISM"
        if minor >= 16:
            return "BBO_ONLY"
    return "LEGACY_LAST_PRICE"


def _attach_realism_era(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    if "bot_version" in out.columns:
        out["sim_era"] = out["bot_version"].apply(_bot_version_era)
    else:
        out["sim_era"] = "LEGACY_LAST_PRICE"
    return out


def _realism_era_summary(df: pd.DataFrame, pnl_col: str = "net_pnl_usd") -> pd.DataFrame:
    if df is None or df.empty or "sim_era" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["net"] = pd.to_numeric(work.get(pnl_col), errors="coerce")
    rows = []
    for era, sub in work.groupby("sim_era", observed=True):
        n = len(sub)
        if n == 0:
            continue
        net = sub["net"]
        rows.append({
            "sim_era": era,
            "label": REALISM_ERA_LABELS.get(era, era),
            "trades": n,
            "win_rate_pct": round((net > 0).mean() * 100, 1),
            "sum_net_usd": round(net.sum(), 2),
            "avg_net_usd": round(net.mean(), 2),
        })
    return pd.DataFrame(rows).sort_values("sim_era")


def realism_sim_audit(trades, analysis_df):
    """
    v89: Audit BBO/depth realism — split PnL by sim era, book slippage, execution types.
    Writes fill_quality_report.json for dashboard / downstream tools.
    """
    print(f"\n=== REALISM SIM AUDIT (BBO + book depth) — {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    print(
        "  Trades before v1.1.16 used last-trade fills (optimistic). "
        f"v1.1.18+ uses bid/ask + order-book VWAP. Prefer {REALISM_ERA_LABELS['DEPTH_REALISM']} cohort for live readiness. "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    base = analysis_df if analysis_df is not None and not analysis_df.empty else trades
    if base is None or base.empty:
        print(f"  No trades to audit. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    work = _attach_realism_era(base)
    era_df = _realism_era_summary(work)
    if not era_df.empty:
        print("\n  --- PnL by simulation era ---")
        print(era_df.to_string(index=False))
        depth_n = int((work["sim_era"] == "DEPTH_REALISM").sum())
        legacy_n = int((work["sim_era"] == "LEGACY_LAST_PRICE").sum())
        if legacy_n and depth_n:
            print(
                f"\n  ⚠️ Mixed eras: {legacy_n} legacy + {depth_n} depth-realism trades — "
                f"do not blend win-rate/PnL across eras. {PIPELINE_ENFORCEMENT_TAG}"
            )
        elif depth_n == 0:
            print(f"\n  ⚠️ No v1.1.18+ trades yet — stats may be optimistic until bot restarts. {PIPELINE_ENFORCEMENT_TAG}")

    book_entry = pd.to_numeric(work.get("book_slippage_usd_entry"), errors="coerce")
    book_exit = pd.to_numeric(work.get("book_slippage_usd_exit"), errors="coerce")
    book_total = pd.to_numeric(work.get("book_slippage_usd_total"), errors="coerce")
    has_book = book_total.notna().any() and book_total.fillna(0).abs().sum() >= 0

    report = {
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_count": int(len(work)),
        "era_breakdown": era_df.to_dict(orient="records") if not era_df.empty else [],
        "book_slippage": {},
        "execution_types": {},
        "partial_fills": 0,
        "replay_note": (
            "signal_replay ticks from v1.1.18+ use executable depth marks; "
            "legacy replay used last-trade unreal — counterfactual sweeps may differ."
        ),
    }

    if has_book or "book_slippage_usd_total" in work.columns:
        depth_work = work[work["sim_era"] == "DEPTH_REALISM"] if "sim_era" in work.columns else work
        if depth_work.empty:
            depth_work = work[book_total.notna()]
        be = pd.to_numeric(depth_work.get("book_slippage_usd_entry"), errors="coerce").fillna(0)
        bx = pd.to_numeric(depth_work.get("book_slippage_usd_exit"), errors="coerce").fillna(0)
        bt = pd.to_numeric(depth_work.get("book_slippage_usd_total"), errors="coerce").fillna(0)
        print("\n  --- Order-book slippage (USD) ---")
        print(f"  Entry avg: ${be.mean():.4f} | Exit avg: ${bx.mean():.4f} | Total avg: ${bt.mean():.4f} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"  Total book slippage (all trades): ${bt.sum():.2f} {PIPELINE_ENFORCEMENT_TAG}")
        buckets = []
        for lo, hi, label in [(0, 0.01, "0"), (0.01, 0.5, "0.01-0.50"), (0.5, 2, "0.50-2"), (2, 999, "2+")]:
            sub = depth_work[(bt >= lo) & (bt < hi)]
            if sub.empty:
                continue
            pnl = pd.to_numeric(sub.get("net_pnl_usd"), errors="coerce")
            buckets.append({
                "bucket": label,
                "trades": len(sub),
                "win_rate_pct": round((pnl > 0).mean() * 100, 1),
                "sum_net_usd": round(pnl.sum(), 2),
                "avg_book_slip_usd": round(bt.loc[sub.index].mean(), 4),
            })
        if buckets:
            print("\n  --- PnL by book_slippage_usd_total bucket ---")
            print(pd.DataFrame(buckets).to_string(index=False))
        report["book_slippage"] = {
            "entry_avg_usd": round(float(be.mean()), 4),
            "exit_avg_usd": round(float(bx.mean()), 4),
            "total_avg_usd": round(float(bt.mean()), 4),
            "total_sum_usd": round(float(bt.sum()), 2),
            "buckets": buckets,
        }
    else:
        print(f"  ℹ️ No book_slippage_* columns yet — close trades on {EXPECTED_BOT_VERSION} to populate. {PIPELINE_ENFORCEMENT_TAG}")

    if "execution_entry_type" in work.columns:
        ent = work.groupby("execution_entry_type", observed=True).agg(
            n=("trade_id", "count"),
            wr=("net_pnl_usd", lambda x: (pd.to_numeric(x, errors="coerce") > 0).mean() * 100),
            sum_net=("net_pnl_usd", lambda x: pd.to_numeric(x, errors="coerce").sum()),
        ).round(2)
        print("\n  --- By execution_entry_type ---")
        print(ent.to_string())
        report["execution_types"]["entry"] = ent.reset_index().to_dict(orient="records")
    if "execution_exit_type" in work.columns:
        ex = work.groupby("execution_exit_type", observed=True).agg(
            n=("trade_id", "count"),
            wr=("net_pnl_usd", lambda x: (pd.to_numeric(x, errors="coerce") > 0).mean() * 100),
            sum_net=("net_pnl_usd", lambda x: pd.to_numeric(x, errors="coerce").sum()),
        ).round(2)
        print("\n  --- By execution_exit_type ---")
        print(ex.to_string())
        report["execution_types"]["exit"] = ex.reset_index().to_dict(orient="records")

    if "entry_partial_fill" in work.columns:
        pf = work["entry_partial_fill"].astype(str).str.lower().isin(("true", "1", "yes"))
        report["partial_fills"] = int(pf.sum())
        if report["partial_fills"]:
            print(f"\n  ⚠️ Partial entry fills: {report['partial_fills']} {PIPELINE_ENFORCEMENT_TAG}")

    if "exit_reason" in work.columns:
        ladder = work[work["exit_reason"] == "PROFIT_LOCK_LADDER"].copy()
        if not ladder.empty:
            net = pd.to_numeric(ladder.get("net_pnl_usd"), errors="coerce")
            mfe = pd.to_numeric(ladder.get("max_profit", ladder.get("mfe_margin_pct")), errors="coerce")
            suspicious = ladder[(net >= 25) & (mfe < 20)]
            report["ladder_exit_audit"] = {
                "ladder_trades": int(len(ladder)),
                "suspicious_tp_booking": int(len(suspicious)),
                "note": "net≥25 with peak MFE<20% implies pre-v1.1.19 maker/TP booking bug",
            }
            if len(suspicious):
                print(
                    f"\n  ⚠️ LADDER BUG SUSPECTS: {len(suspicious)} trades booked ~${net[suspicious.index].mean():.0f} "
                    f"but peak MFE only {mfe[suspicious.index].mean():.1f}% — exclude from edge stats "
                    f"(fixed in {EXPECTED_BOT_VERSION}). {PIPELINE_ENFORCEMENT_TAG}"
                )
            else:
                print(
                    f"\n  Ladder exits: {len(ladder)} | no suspicious TP-booking rows "
                    f"(v1.1.19+ uses taker book walk). {PIPELINE_ENFORCEMENT_TAG}"
                )

    signal_slip = pd.to_numeric(work.get("slippage"), errors="coerce")
    exec_slip = pd.to_numeric(work.get("execution_slippage"), errors="coerce")
    if signal_slip.notna().any():
        print(f"\n  Signal-vs-limit slippage (price units): mean={signal_slip.mean():.4f} max={signal_slip.max():.4f} {PIPELINE_ENFORCEMENT_TAG}")
        report["signal_slippage_mean"] = round(float(signal_slip.mean()), 6)
    if exec_slip.notna().any():
        report["execution_slippage_mean"] = round(float(exec_slip.mean()), 6)

    try:
        with open(FILL_QUALITY_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n  ✅ Wrote {FILL_QUALITY_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"\n  ⚠️ Could not write {FILL_QUALITY_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")

    print(PIPELINE_ENFORCEMENT_TAG)
    return report


def slippage_analysis(df):
    print("\n=== EXECUTION QUALITY (SLIPPAGE) ===")
    if df is None or df.empty:
        print(f"No trades. {PIPELINE_ENFORCEMENT_TAG}")
        return
    work = _attach_realism_era(df)
    if "slippage" in work.columns:
        print("Signal vs limit slippage (price units):")
        print(work["slippage"].describe().round(5))
    for col, label in (
        ("book_slippage_usd_entry", "Book slippage entry (USD)"),
        ("book_slippage_usd_exit", "Book slippage exit (USD)"),
        ("book_slippage_usd_total", "Book slippage total (USD)"),
    ):
        if col in work.columns:
            s = pd.to_numeric(work[col], errors="coerce")
            if s.notna().any():
                print(f"\n{label}:")
                print(s.describe().round(4))
    if "sim_era" in work.columns:
        era_df = _realism_era_summary(work)
        if not era_df.empty:
            print(f"\nSlippage context by sim era (see REALISM SIM AUDIT for detail):")
            print(era_df[["sim_era", "trades", "win_rate_pct", "sum_net_usd"]].to_string(index=False))
    if "slippage" not in work.columns and "book_slippage_usd_total" not in work.columns:
        print(f"No slippage columns found. {PIPELINE_ENFORCEMENT_TAG}")

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
    if df is None or df.empty:
        return
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    if df["entry_delay_min"].notna().sum() == 0:
        return
    results = []
    for d in range(0, 61, 5):
        subset = df[(df["entry_delay_min"] >= d) & (df["entry_delay_min"] < d + 5)]
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
    if df is None or df.empty:
        return
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    results = []
    for ai in [55, 60, 65]:
        for mom in [0, 0.5, 1.0]:
            for delay in [5, 10, 20]:
                subset = df[
                    (pd.to_numeric(df.get("ai_win_prob", pd.Series(50)), errors='coerce') >= ai) &
                    (pd.to_numeric(df.get("momentum", pd.Series(0)), errors='coerce') >= mom) &
                    (pd.to_numeric(df.get("entry_delay_min", pd.Series(999)), errors='coerce') <= delay)
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
    te = df[safe_bool_filter(df, "exit_reason", "TIME_EXIT")].copy()
    if len(te) == 0:
        print("No TIME_EXIT trades yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    te["entry_delay_min"] = _normalize_entry_delay_min(te)
    print(f"Avg momentum     : {pd.to_numeric(te.get('momentum', pd.Series(0)), errors='coerce').mean():.3f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg volatility   : {pd.to_numeric(te.get('volatility', pd.Series(0)), errors='coerce').mean():.3f} {PIPELINE_ENFORCEMENT_TAG}")
    print(f"Avg entry delay  : {pd.to_numeric(te['entry_delay_min'], errors='coerce').mean():.1f} min {PIPELINE_ENFORCEMENT_TAG}")
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
    if df is None or df.empty:
        return
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    if df["entry_delay_min"].notna().sum() == 0:
        return
    df["delay_bucket"] = pd.cut(df["entry_delay_min"], bins=[0, 5, 10, 20, 40, 60])
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
        if "shadow_lane" in bs.columns:
            bs = bs[bs["shadow_lane"] == "blocked_approve"]
        if "filled" in bs.columns:
            bs = bs[bs["filled"] == True]
        bs["net_pnl_usd"] = pd.to_numeric(bs.get("net_pnl_usd"), errors="coerce")
        missed = bs[bs["net_pnl_usd"] > 0]
        if len(missed) == 0:
            print(f"No missed profitable blocked APPROVE gates in shadow data yet. {PIPELINE_ENFORCEMENT_TAG}")
            return
        print(f"Missed Winners (blocked APPROVE gates): {len(missed)} {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Avg Missed Shadow PnL: ${missed['net_pnl_usd'].mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
        if "block_reason" in missed.columns:
            print(f"Top Block Reasons:\n{missed['block_reason'].value_counts().head(5).to_string()} {PIPELINE_ENFORCEMENT_TAG}")
            _print_block_prefix_subtotals(missed, label="Missed opportunity")
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
    if df is None or df.empty:
        return
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    if df["entry_delay_min"].notna().sum() == 0:
        print(f"No entry_delay / entry_delay_sec column found. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df["lat_bin"] = pd.cut(df["entry_delay_min"], bins=[0,5,10,20,40,60])
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
    df = df.copy()
    df["entry_delay_min"] = _normalize_entry_delay_min(df)
    subset = df[
        (pd.to_numeric(df.get("edge_score", pd.Series(0)), errors='coerce') >= rule["edge"]) &
        (pd.to_numeric(df.get("ai_win_prob", pd.Series(50)), errors='coerce') >= rule["ai"]) &
        (pd.to_numeric(df.get("entry_delay_min", pd.Series(999)), errors='coerce') <= rule["delay"]) &
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

def _refresh_pathway_scorecard_safe(session: dict):
    try:
        from pathway_scorecard_engine import refresh_pathway_scorecard
        refresh_pathway_scorecard(
            os.getcwd(),
            bot_version=EXPECTED_BOT_VERSION,
            fresh_collection_mode=bool((session or {}).get("fresh_collection_mode")),
        )
        print(f"  pathway_scorecard.json refreshed {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as pe:
        print(f"  ⚠️ pathway_scorecard refresh failed: {pe} {PIPELINE_ENFORCEMENT_TAG}")


def _write_analyzer_crash_log(iteration: int, tb: str):
    crash_path = os.path.join(os.getcwd(), "analyzer_crash.log")
    try:
        with open(crash_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat()} iteration {iteration} ---\n")
            f.write(tb)
            if not tb.endswith("\n"):
                f.write("\n")
    except Exception:
        pass


def run(interval_min=30, session_only=True, max_iterations=None):
    iteration = 0
    sleep_sec = max(60, int(interval_min) * 60)
    while True:
        iteration += 1
        crashed = False
        print(f"\n=== ANALYZER {ANALYZER_VERSION} ITERATION {iteration} START {PIPELINE_ENFORCEMENT_TAG} ===")
        try:
            _run_analyzer_iteration(iteration, interval_min, session_only)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            crashed = True
            tb = traceback.format_exc()
            print(
                f"\n❌ ANALYZER ITERATION {iteration} FAILED: {exc} {PIPELINE_ENFORCEMENT_TAG}\n{tb}"
            )
            _write_analyzer_crash_log(iteration, tb)
        crash_note = " (recovered from error)" if crashed else ""
        print(
            f"\n⏳ Next run in {interval_min} minutes... "
            f"Iteration {iteration} complete{crash_note} {PIPELINE_ENFORCEMENT_TAG}\n"
        )
        if max_iterations is not None and iteration >= max_iterations:
            break
        time.sleep(sleep_sec)


def _run_analyzer_iteration(iteration, interval_min, session_only):
        session = load_research_session()
        print_data_provenance_banner(session)
        trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors = load_data()
        all_trades_unfiltered = trades.copy()
        dataset_counts = {
            "csv_trades": len(trades),
            "csv_blocked": len(blocked),
            "csv_decisions": len(decisions),
            "csv_ai_log": len(ai_log),
            "csv_setups": len(setups),
        }
        data_scope = "session" if session_only else "all"
        if session_only:
            trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors = apply_session_filters(
                session, trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors
            )
        raw_trade_count = len(trades.drop_duplicates(subset=["trade_id"])) if not trades.empty and "trade_id" in trades.columns else len(trades)

        master = build_master_dataset(trades, blocked, decisions, ai_log, signal_persist, near_edge)
        signal_master = build_signal_dataset(setups, decisions, ai_log, blocked, raw_trade_count)

        analysis_df = master if not master.empty else pd.DataFrame()
        executive_summary(trades, analysis_df, decisions, ai_log, blocked, near_edge, signal_persist, pipeline_events, ai_errors)
        research_data_coverage_audit(trades, decisions, blocked, pipeline_events)
        research_jsonl_summary()
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
            fill_distance_report()
            shadow_report = shadow_fill_outcome_matrix(trades, session=session, blocked=blocked)
            benchmark_report = benchmark_vs_lanes_report(
                trades, session=session, blocked=blocked, shadow_report=shadow_report,
                all_trades=all_trades_unfiltered, decisions=decisions, ai_log=ai_log,
            )
            lane_opportunity_capture_report(trades=trades, shadow_report=shadow_report)
            ai_funnel_report(trades=trades, session=session)
            pre_test_analytics_reports(
                trades=trades,
                decisions=decisions,
                session=session,
                benchmark_report=benchmark_report,
                shadow_report=shadow_report,
                blocked=blocked,
            )
            direction_attribution_report(trades=trades, decisions=decisions, session=session)
            confidence_band_report(trades=trades, decisions=decisions, session=session)
            pathway_lane_specs_report(trades, session=session, benchmark_report=benchmark_report, shadow_report=shadow_report)
            horizon_counterfactual_report(trades=trades, session=session, shadow_report=shadow_report, blocked=blocked)
            shadow_vs_live_fill_audit(blocked)
            shadow_vs_live_entry_report()
            ai_calibration_report(trades, session=session)
            _refresh_pathway_scorecard_safe(session)
            finalize_analyzer_outputs(
                session=session,
                trades=trades,
                analysis_df=analysis_df,
                decisions=decisions,
                blocked=blocked,
                dataset_counts=dataset_counts,
                data_scope=data_scope,
            )
            return

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
        realism_sim_audit(trades, analysis_df)
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

        fill_distance_report()
        shadow_report = shadow_fill_outcome_matrix(trades, session=session, blocked=blocked)
        benchmark_report = benchmark_vs_lanes_report(
            trades, session=session, blocked=blocked, shadow_report=shadow_report,
            all_trades=all_trades_unfiltered,
        )
        lane_opportunity_capture_report(trades=trades, shadow_report=shadow_report)
        ai_funnel_report(trades=trades, session=session)
        pre_test_analytics_reports(
            trades=trades,
            decisions=decisions,
            session=session,
            benchmark_report=benchmark_report,
            shadow_report=shadow_report,
            blocked=blocked,
        )
        pathway_lane_specs_report(trades, session=session, shadow_report=shadow_report)
        horizon_counterfactual_report(trades=trades, session=session, shadow_report=shadow_report, blocked=blocked)
        ai_calibration_report(trades, session=session)
        direction_attribution_report(trades, decisions=decisions, session=session)
        confidence_band_report(trades, decisions=decisions, session=session)
        shadow_vs_live_fill_audit(blocked)
        shadow_vs_live_entry_report()
        _refresh_pathway_scorecard_safe(session)

        print(f"\n🔥 STRATEGY ADVICE 🔥 {PIPELINE_ENFORCEMENT_TAG}")
        print(f"Review EXECUTIVE SUMMARY first. Rules need ≥{MIN_TRADES_FOR_RULES} trades. Fix zero-variance features in bot before trusting edge buckets. {PIPELINE_ENFORCEMENT_TAG}")
        finalize_analyzer_outputs(
            session=session,
            trades=trades,
            analysis_df=analysis_df,
            decisions=decisions,
            blocked=blocked,
            dataset_counts=dataset_counts,
            data_scope=data_scope,
        )


def _classify_fill_block_reason(reason: str) -> str:
    r = str(reason or "").upper()
    if not r or r == "EXECUTED":
        return "executed"
    if "DUPLICATE" in r:
        return "duplicate"
    if "CAPACITY" in r or "MAX_ACTIVE" in r or "MAX_LONG" in r or "MAX_SHORT" in r:
        return "capacity"
    if "CHOP" in r or "MOMENTUM" in r or "GOLDEN_STACK" in r:
        return "chop"
    if "TTL" in r or "EXPIRED" in r or "STALE" in r:
        return "TTL"
    if "NEVER" in r or "AWAIT" in r or "NOT_SUBMIT" in r:
        return "never_submitted"
    return "other"


SHADOW_FILL_OUTCOME_LABELS = [
    "Shadow fill + real fill",
    "Shadow fill + TTL expired",
    "Shadow fill + missed by <$5",
    "Shadow fill + missed by <$10",
    "Shadow fill + missed by <$20",
    "Shadow fill + missed by >=$20",
    "Shadow fill + blocked (capacity)",
    "Shadow fill + blocked (gates)",
    "Shadow fill + other",
    "Shadow NO_FILL",
]


def _build_funnel_trade_index():
    """Per trade_id: funnel stages, last stage, ORDER_SUBMITTED / FILLED flags."""
    index = {}
    if not os.path.exists(EXECUTION_FUNNEL_FILE):
        return index
    for row in _load_jsonl_rows(EXECUTION_FUNNEL_FILE):
        tid = str(row.get("trade_id") or "").strip()
        if not tid:
            continue
        stage = str(row.get("stage") or "").upper()
        ts_raw = row.get("ts")
        try:
            ts_val = pd.Timestamp(ts_raw).value if ts_raw else 0
        except Exception:
            ts_val = 0
        rec = index.setdefault(
            tid,
            {
                "stages": set(),
                "last_stage": "",
                "last_ts": 0,
                "has_order_submitted": False,
                "has_filled": False,
                "fill_reason": None,
                "research_lane": None,
            },
        )
        rec["stages"].add(stage)
        if ts_val >= rec["last_ts"]:
            rec["last_ts"] = ts_val
            rec["last_stage"] = stage
        if stage == "ORDER_SUBMITTED":
            rec["has_order_submitted"] = True
        if stage == "FILLED":
            rec["has_filled"] = True
        if row.get("research_lane"):
            rec["research_lane"] = row.get("research_lane")
        fill_reason = row.get("fill_reason") or row.get("reason")
        if fill_reason:
            rec["fill_reason"] = fill_reason
    return index


def _build_missed_by_usd_index():
    """Merge fill_quality.jsonl + expired_orders CSV into trade_id → missed_by_usd."""
    rows = []
    if os.path.exists(FILL_QUALITY_JSONL_FILE):
        rows.extend(_load_jsonl_rows(FILL_QUALITY_JSONL_FILE))
    if os.path.exists(EXPIRED_ORDERS_FILE):
        try:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="utf-8")
        except UnicodeDecodeError:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="latin1")
        if not exp.empty:
            seen_ids = {str(r.get("trade_id")) for r in rows if r.get("trade_id")}
            for _, r in exp.iterrows():
                tid = r.get("trade_id")
                if tid is None or pd.isna(tid):
                    continue
                tid = str(tid)
                if tid in seen_ids:
                    continue
                rows.append(r.to_dict())
    index = {}
    for row in rows:
        tid = str(row.get("trade_id") or "").strip()
        if not tid:
            continue
        inferred = _infer_fill_missed_by_usd(row)
        if inferred is not None:
            index[tid] = float(inferred)
    return index


def _shadow_fill_ttl_hit(funnel_rec, block_reason, expired_reason=None) -> bool:
    ttl_tokens = ("TTL", "EXPIRED", "STALE")
    for text in (block_reason, expired_reason, (funnel_rec or {}).get("fill_reason")):
        u = str(text or "").upper()
        if any(tok in u for tok in ttl_tokens):
            return True
    if not funnel_rec:
        return False
    stages = funnel_rec.get("stages") or set()
    if stages & {"SIGNAL_EXPIRED", "ORDER_EXPIRED", "STALE"}:
        return True
    last = str(funnel_rec.get("last_stage") or "").upper()
    return last in ("SIGNAL_EXPIRED", "ORDER_EXPIRED", "STALE")


def _shadow_fill_capacity_hit(funnel_rec, block_reason) -> bool:
    cap_tokens = ("MAX_ACTIVE", "CLUSTER", "DUPLICATE", "CAPACITY")
    u = str(block_reason or "").upper()
    if any(tok in u for tok in cap_tokens):
        return True
    if not funnel_rec:
        return False
    stages = funnel_rec.get("stages") or set()
    if "CAPACITY_REJECTED" in stages:
        return True
    return str(funnel_rec.get("last_stage") or "").upper() == "CAPACITY_REJECTED"


def _shadow_fill_gate_hit(block_reason) -> bool:
    br = str(block_reason or "")
    if br.startswith(("WOULD_FAIL", "WOULD_BLOCK", "PROFIT_GATE", "LONG_BLOCKED")):
        return True
    u = br.upper()
    return any(tok in u for tok in ("CHOP", "MOMENTUM", "SPREAD", "GOLDEN_STACK", "ADX", "STRUCTURE"))


def _shadow_fill_missed_label(missed_usd):
    if missed_usd is None or (isinstance(missed_usd, float) and np.isnan(missed_usd)):
        return None
    m = float(missed_usd)
    if m <= 5:
        return "Shadow fill + missed by <$5"
    if m <= 10:
        return "Shadow fill + missed by <$10"
    if m <= 20:
        return "Shadow fill + missed by <$20"
    return "Shadow fill + missed by >=$20"


def _classify_shadow_fill_real_outcome(
    trade_id,
    executed_ids,
    funnel_index,
    missed_index,
    block_reason,
    expired_reason=None,
):
    tid = str(trade_id)
    if tid in executed_ids:
        return "Shadow fill + real fill"
    funnel_rec = funnel_index.get(tid)
    if _shadow_fill_ttl_hit(funnel_rec, block_reason, expired_reason):
        return "Shadow fill + TTL expired"
    if _shadow_fill_capacity_hit(funnel_rec, block_reason):
        return "Shadow fill + blocked (capacity)"
    if _shadow_fill_gate_hit(block_reason):
        return "Shadow fill + blocked (gates)"
    if funnel_rec and funnel_rec.get("has_order_submitted") and not funnel_rec.get("has_filled"):
        missed_label = _shadow_fill_missed_label(missed_index.get(tid))
        if missed_label:
            return missed_label
        return "Shadow fill + missed by >=$20"
    return "Shadow fill + other"


def _resolve_shadow_research_lane(row, funnel_index, snapshots, expired_lanes):
    tid = str(row.get("trade_id") or "")
    if row.get("research_lane"):
        return str(row.get("research_lane"))
    funnel_rec = funnel_index.get(tid) or {}
    if funnel_rec.get("research_lane"):
        return str(funnel_rec.get("research_lane"))
    snap = snapshots.get(tid) or {}
    if snap.get("research_lane"):
        return str(snap.get("research_lane"))
    if tid in expired_lanes:
        return str(expired_lanes[tid])
    return "UNKNOWN"


def shadow_fill_outcome_matrix(trades=None, session=None, blocked=None):
    """Classify shadow-filled APPROVE paths by what happened in the real pipeline."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== SHADOW FILL OUTCOME MATRIX — {ANALYZER_SYNC_ID} ({scope}) {PIPELINE_ENFORCEMENT_TAG} ===")
    shadow_df = _load_shadow_outcome_df(session)
    if shadow_df is None or shadow_df.empty:
        print(f"  No shadow_outcome rows for {scope.lower()} scope. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        executed_ids = set(trades["trade_id"].dropna().astype(str))
    else:
        executed_ids = _load_executed_trade_ids()

    funnel_index = _build_funnel_trade_index()
    missed_index = _build_missed_by_usd_index()
    block_map = _blocked_reason_by_trade_id(blocked)
    snapshots = _load_signal_snapshots()
    expired_lanes = {}
    expired_reasons = {}
    if os.path.exists(EXPIRED_ORDERS_FILE):
        try:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="utf-8", usecols=["trade_id", "reason", "research_lane"])
        except (UnicodeDecodeError, ValueError):
            try:
                exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="latin1", usecols=["trade_id", "reason", "research_lane"])
            except Exception:
                exp = pd.DataFrame()
        except Exception:
            exp = pd.DataFrame()
        for _, r in exp.iterrows():
            tid = str(r.get("trade_id") or "").strip()
            if not tid:
                continue
            if pd.notna(r.get("research_lane")):
                expired_lanes[tid] = r.get("research_lane")
            if pd.notna(r.get("reason")):
                expired_reasons[tid] = r.get("reason")
            if tid not in missed_index:
                inferred = _infer_fill_missed_by_usd(r.to_dict())
                if inferred is not None:
                    missed_index[tid] = inferred

    work = shadow_df.copy()
    if "filled" in work.columns:
        work["filled"] = work["filled"].apply(_truthy)
    else:
        work["filled"] = True
    work["net_pnl_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    work["research_lane"] = work.apply(
        lambda r: _resolve_shadow_research_lane(r, funnel_index, snapshots, expired_lanes),
        axis=1,
    )

    shadow_cohort = len(work)
    filled_rows = work[work["filled"]]
    shadow_filled = len(filled_rows)
    print(f"Shadow cohort: {shadow_cohort} (all shadow_outcome rows in session)")
    print(f"Shadow filled: {shadow_filled}")

    counts = {label: 0 for label in SHADOW_FILL_OUTCOME_LABELS}
    classified_rows = []

    for _, row in work.iterrows():
        tid = str(row.get("trade_id") or "")
        if not _truthy(row.get("filled")):
            outcome = "Shadow NO_FILL"
        else:
            br = block_map.get(tid) or row.get("block_reason") or ""
            outcome = _classify_shadow_fill_real_outcome(
                tid,
                executed_ids,
                funnel_index,
                missed_index,
                br,
                expired_reason=expired_reasons.get(tid),
            )
        counts[outcome] = counts.get(outcome, 0) + 1
        classified_rows.append({
            "trade_id": tid,
            "outcome": outcome,
            "research_lane": row.get("research_lane"),
            "net_pnl_usd": row.get("net_pnl_usd"),
            "filled": _truthy(row.get("filled")),
            "executed": tid in executed_ids,
        })

    print("\nOutcome                              Count")
    for label in SHADOW_FILL_OUTCOME_LABELS:
        print(f"{label:<37} {counts.get(label, 0):>5}")

    class_df = pd.DataFrame(classified_rows)
    non_exec_filled = class_df[(class_df["filled"]) & (~class_df["executed"])]
    pnl = pd.to_numeric(non_exec_filled.get("net_pnl_usd"), errors="coerce")
    missed_winner_usd = round(float(pnl[pnl > 0].sum()), 2) if not pnl.empty else 0.0
    good_block_saved = round(float(-pnl[pnl <= 0].sum()), 2) if not pnl.empty else 0.0
    if shadow_filled:
        print(
            f"\n  Shadow-filled non-executed: n={len(non_exec_filled)} | "
            f"missed_winner_usd=${missed_winner_usd:.2f} | good_block_saved=${good_block_saved:.2f} "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )

    by_lane = {}
    for lane, sub in class_df.groupby("research_lane", observed=True):
        lane_counts = {label: 0 for label in SHADOW_FILL_OUTCOME_LABELS}
        for oc in sub["outcome"]:
            lane_counts[oc] = lane_counts.get(oc, 0) + 1
        lane_filled = sub[sub["filled"]]
        lane_non_exec = lane_filled[~lane_filled["executed"]]
        lane_pnl = pd.to_numeric(lane_non_exec.get("net_pnl_usd"), errors="coerce")
        lane_n = len(sub)
        lane_entry = {
            "shadow_cohort": lane_n,
            "shadow_filled": len(lane_filled),
            "counts": lane_counts,
            "counts_pct": {
                label: round(100 * lane_counts.get(label, 0) / lane_n, 1) if lane_n else 0.0
                for label in SHADOW_FILL_OUTCOME_LABELS
            },
            "missed_winner_usd": round(float(lane_pnl[lane_pnl > 0].sum()), 2) if not lane_pnl.empty else 0.0,
            "good_block_saved": round(float(-lane_pnl[lane_pnl <= 0].sum()), 2) if not lane_pnl.empty else 0.0,
        }
        by_lane[str(lane)] = lane_entry

    counts_pct = {
        label: round(100 * counts.get(label, 0) / shadow_cohort, 1) if shadow_cohort else 0.0
        for label in SHADOW_FILL_OUTCOME_LABELS
    }
    report = {
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shadow_cohort": shadow_cohort,
        "shadow_filled": shadow_filled,
        "counts": counts,
        "counts_pct": counts_pct,
        "missed_winner_usd": missed_winner_usd,
        "good_block_saved": good_block_saved,
        "by_lane": by_lane,
    }
    try:
        with open(SHADOW_FILL_OUTCOME_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n  ✅ Wrote {SHADOW_FILL_OUTCOME_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"\n  ⚠️ Could not write {SHADOW_FILL_OUTCOME_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)
    return report


def _filter_snapshots_by_session(snapshots: dict, session: dict = None) -> dict:
    """Keep APPROVE snapshots collected in the current research session."""
    if not snapshots:
        return {}
    if not session or _session_start_ts(session) is None:
        return snapshots
    df = pd.DataFrame([{"trade_id": tid, **snap} for tid, snap in snapshots.items()])
    if df.empty:
        return {}
    filtered = filter_df_since_session(df, session, ts_cols=("ts", "timestamp"))
    if filtered.empty:
        return {}
    return {str(r["trade_id"]): snapshots[str(r["trade_id"])] for _, r in filtered.iterrows()}


def _empty_lane_benchmark_metrics():
    return {
        "approves": 0,
        "real_fills": 0,
        "approve_to_fill_pct": 0.0,
        "shadow_filled": 0,
        "shadow_fill_pct": 0.0,
        "net_pnl_real": 0.0,
        "net_pnl_shadow_blocked": 0.0,
        "per_approve_ev": 0.0,
        "costly_blocks_usd": 0.0,
        "good_blocks_saved_usd": 0.0,
    }


def _lane_fills_pnl_from_trades(trade_df, lane: str) -> tuple[int, float]:
    """Real fills and booked PnL for one research_lane from a trades frame."""
    if trade_df is None or trade_df.empty or not lane:
        return 0, 0.0
    if "research_lane" not in trade_df.columns:
        return 0, 0.0
    sub = trade_df[trade_df["research_lane"].astype(str).str.upper() == str(lane).upper()]
    if sub.empty:
        return 0, 0.0
    if "trade_id" in sub.columns:
        sub = sub.drop_duplicates(subset=["trade_id"], keep="last")
    fills = len(sub)
    pnl = round(float(pd.to_numeric(sub.get("net_pnl_usd"), errors="coerce").fillna(0).sum()), 2)
    return fills, pnl


def _all_time_lane_metrics(all_trades, lane: str) -> dict:
    """Full CSV history stats — shown when session has no activity on a paused lane."""
    fills, pnl = _lane_fills_pnl_from_trades(all_trades, lane)
    ev = round(pnl / fills, 2) if fills else 0.0
    return {
        "real_fills": fills,
        "net_pnl_real": pnl,
        "ev_usd": ev,
    }


def _ordered_lane_catalog(lanes_with_approves, trade_df=None) -> list:
    """Every lane in ANALYZER_COMPARE_LANES plus any lane seen in data."""
    lanes_from_trades = set()
    if trade_df is not None and not trade_df.empty and "research_lane" in trade_df.columns:
        lanes_from_trades = {
            str(x).strip()
            for x in trade_df["research_lane"].dropna().unique()
            if str(x).strip() and str(x).strip() not in ("EXEC_5M", "UNKNOWN", "nan")
        }
    catalog = []
    seen = set()
    for ln in list(BENCHMARK_LANES) + sorted(lanes_with_approves or []) + sorted(lanes_from_trades):
        if not ln or ln in seen or ln == "EXEC_5M":
            continue
        seen.add(ln)
        catalog.append(ln)
    return catalog


def _benchmark_lane_verdict(lane: str, delta: dict, bench: dict) -> str:
    if lane == BENCHMARK_LANE:
        return "benchmark baseline"
    d_fill = delta.get("delta_approve_to_fill_pct", 0.0)
    d_ev = delta.get("delta_per_approve_ev", 0.0)
    d_costly = delta.get("delta_costly_blocks_usd", 0.0)
    if d_ev > 0.05 and d_fill >= -5:
        return "beats benchmark on EV"
    if d_fill < -15 and abs(d_fill) > abs(d_ev * 100):
        return "TTL dominant"
    if d_costly > 5 and d_ev <= 0:
        return "costly blocks vs benchmark"
    if d_ev < -0.05:
        return "underperforms on EV"
    if d_fill < -10:
        return "underperforms on fill rate"
    return "mixed vs benchmark"


def benchmark_vs_lanes_report(trades=None, session=None, blocked=None, shadow_report=None, all_trades=None, decisions=None, ai_log=None):
    """Compare research lanes vs CONTINUOUS benchmark within the same session."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== BENCHMARK vs LANES — {scope.lower()} ===")
    print(f"Benchmark: {BENCHMARK_LANE} (Scenario C exits frozen) {PIPELINE_ENFORCEMENT_TAG}")

    snapshots_all = _load_signal_snapshots()
    snapshots = _filter_snapshots_by_session(snapshots_all, session)
    if not snapshots:
        print(f"  No APPROVE snapshots for {scope.lower()} scope. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    approve_rows = []
    for tid, snap in snapshots.items():
        lane = str(snap.get("research_lane") or "UNKNOWN")
        if lane == "EXEC_5M":
            continue
        approve_rows.append({"trade_id": str(tid), "research_lane": lane})
    approve_df = pd.DataFrame(approve_rows)
    if approve_df.empty:
        print(f"  No lane-tagged APPROVE snapshots. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        executed_ids = set(trades["trade_id"].dropna().astype(str))
        trade_df = trades.copy()
    else:
        executed_ids = _load_executed_trade_ids()
        trade_df = pd.DataFrame()
        if os.path.exists(TRADES_FILE):
            try:
                trade_df = pd.read_csv(TRADES_FILE, encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                try:
                    trade_df = pd.read_csv(TRADES_FILE, encoding="latin1")
                except Exception:
                    trade_df = pd.DataFrame()
            except Exception:
                trade_df = pd.DataFrame()
            if session and _session_start_ts(session) is not None and not trade_df.empty:
                trade_df = filter_df_since_session(
                    trade_df, session, ts_cols=("ts", "close_ts", "entry_ts", "open_ts")
                )
            executed_ids = set(trade_df["trade_id"].dropna().astype(str)) if not trade_df.empty and "trade_id" in trade_df.columns else executed_ids

    shadow_df = _load_shadow_outcome_df(session)
    funnel_index = _build_funnel_trade_index()
    expired_lanes = {}
    if os.path.exists(EXPIRED_ORDERS_FILE):
        try:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="utf-8", usecols=["trade_id", "research_lane"])
        except (UnicodeDecodeError, ValueError):
            try:
                exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="latin1", usecols=["trade_id", "research_lane"])
            except Exception:
                exp = pd.DataFrame()
        except Exception:
            exp = pd.DataFrame()
        for _, r in exp.iterrows():
            tid = str(r.get("trade_id") or "").strip()
            if tid and pd.notna(r.get("research_lane")):
                expired_lanes[tid] = r.get("research_lane")

    shadow_by_lane = {}
    if shadow_report and isinstance(shadow_report.get("by_lane"), dict):
        shadow_by_lane = shadow_report["by_lane"]

    shadow_lane_df = _load_shadow_lane_outcome_df(session)
    v2_log_metrics = _v2_lane_metrics_from_logs(session)
    if v2_log_metrics.get("approves"):
        lanes_with_approves_seed = set(approve_df["research_lane"].unique()) - {"EXEC_5M"}
        lanes_with_approves_seed.add(RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2)
    else:
        lanes_with_approves_seed = set(approve_df["research_lane"].unique()) - {"EXEC_5M"}

    lane_metrics = {}
    lanes_with_approves = lanes_with_approves_seed
    all_trade_df = all_trades if all_trades is not None else trade_df
    lanes_ordered = _ordered_lane_catalog(lanes_with_approves, all_trade_df)

    for lane in lanes_ordered:
        if lane == "EXEC_5M":
            continue
        lane_approves = approve_df[approve_df["research_lane"] == lane]
        approve_ids = set(lane_approves["trade_id"])
        approves_n = len(lane_approves)

        if not trade_df.empty and "research_lane" in trade_df.columns:
            lane_trades = trade_df[trade_df["research_lane"].astype(str) == lane]
        elif not trade_df.empty:
            lane_trades = trade_df[trade_df["trade_id"].astype(str).isin(approve_ids)]
        else:
            lane_trades = pd.DataFrame()
        real_fills = len(lane_trades)
        net_pnl_real = round(float(pd.to_numeric(lane_trades.get("net_pnl_usd"), errors="coerce").sum()), 2) if not lane_trades.empty else 0.0

        v2_lane_extra = {}
        if lane == RESEARCH_LANE_A160_CONTEXT_CHASE_EXIT_V2 and v2_log_metrics:
            v2_approves = int(v2_log_metrics.get("approves") or 0)
            if v2_approves:
                approves_n = max(approves_n, v2_approves)
            v2_lane_extra = {
                "v2_checker_approves": v2_approves,
                "v2_checker_pass_sims": int(v2_log_metrics.get("checker_pass_sims") or v2_log_metrics.get("sim_fills") or 0),
                "v2_reject_counterfactual_sims": int(
                    v2_log_metrics.get("reject_counterfactual_sims") or v2_log_metrics.get("reject_sim_fills") or 0
                ),
                "v2_checker_pass_pnl": float(v2_log_metrics.get("checker_pass_pnl") or v2_log_metrics.get("sim_pnl") or 0.0),
                "v2_reject_counterfactual_pnl": float(
                    v2_log_metrics.get("reject_counterfactual_pnl") or v2_log_metrics.get("reject_sim_pnl") or 0.0
                ),
                "v2_metrics_note": (
                    "Tile-OFF lane: Approves=checker pass; Checker-pass sims=paper shadow fills; "
                    "Reject sims=checker-reject counterfactuals (not session fills)."
                ),
            }
            if real_fills == 0 and v2_lane_extra["v2_checker_pass_sims"] > 0:
                net_pnl_real = float(v2_lane_extra["v2_checker_pass_pnl"])
        elif lane == "AI_SCAN":
            coord = _ai_scan_coordinator_stats(decisions, ai_log)
            v2_lane_extra = {
                "coordinator_note": "Coordinator — 0 fills by design",
                "ai_scan_coordinator": coord,
                "coordinator_rejects": coord.get("rejects", 0),
                "coordinator_skipped": coord.get("skipped", 0),
                "coordinator_timeouts": coord.get("timeouts", 0),
            }

        if _pathway_lane_status(lane) == PATHWAY_STATUS_SHADOW_COLLECTING:
            if shadow_lane_df is not None and not shadow_lane_df.empty and "research_lane" in shadow_lane_df.columns:
                lane_sl = shadow_lane_df[shadow_lane_df["research_lane"].astype(str) == lane]
                sim_n = len(lane_sl)
                if sim_n:
                    real_fills = sim_n
                    net_pnl_real = round(
                        float(pd.to_numeric(lane_sl.get("net_pnl_usd"), errors="coerce").fillna(0).sum()), 2
                    )
                    approves_n = max(approves_n, sim_n)

        shadow_filled = 0
        net_pnl_shadow_blocked = 0.0
        costly_blocks_usd = 0.0
        good_blocks_saved_usd = 0.0
        if shadow_df is not None and not shadow_df.empty:
            work = shadow_df.copy()
            if "filled" in work.columns:
                work["filled"] = work["filled"].apply(_truthy)
            else:
                work["filled"] = True
            work["net_pnl_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
            work["research_lane"] = work.apply(
                lambda r: _resolve_shadow_research_lane(r, funnel_index, snapshots_all, expired_lanes),
                axis=1,
            )
            lane_shadow = work[work["research_lane"].astype(str) == lane]
            shadow_filled = int(lane_shadow["filled"].sum()) if not lane_shadow.empty else 0
            blocked_shadow = lane_shadow[~lane_shadow["trade_id"].astype(str).isin(executed_ids)]
            blocked_pnl = pd.to_numeric(blocked_shadow.get("net_pnl_usd"), errors="coerce")
            net_pnl_shadow_blocked = round(float(blocked_pnl.sum()), 2) if not blocked_pnl.empty else 0.0
            costly_blocks_usd = round(float(blocked_pnl[blocked_pnl > 0].sum()), 2) if not blocked_pnl.empty else 0.0
            good_blocks_saved_usd = round(float(-blocked_pnl[blocked_pnl <= 0].sum()), 2) if not blocked_pnl.empty else 0.0
        elif lane in shadow_by_lane:
            shadow_filled = int(shadow_by_lane[lane].get("shadow_filled") or 0)
            costly_blocks_usd = float(shadow_by_lane[lane].get("missed_winner_usd") or 0.0)
            good_blocks_saved_usd = float(shadow_by_lane[lane].get("good_block_saved") or 0.0)
            net_pnl_shadow_blocked = round(costly_blocks_usd - good_blocks_saved_usd, 2)

        approve_to_fill_pct = round(100.0 * real_fills / approves_n, 1) if approves_n else 0.0
        shadow_fill_pct = round(100.0 * shadow_filled / approves_n, 1) if approves_n else 0.0
        per_approve_ev = round((net_pnl_real + net_pnl_shadow_blocked) / approves_n, 2) if approves_n else 0.0

        lane_metrics[lane] = {
            "approves": approves_n,
            "real_fills": real_fills,
            "approve_to_fill_pct": approve_to_fill_pct,
            "shadow_filled": shadow_filled,
            "shadow_fill_pct": shadow_fill_pct,
            "net_pnl_real": net_pnl_real,
            "net_pnl_shadow_blocked": net_pnl_shadow_blocked,
            "per_approve_ev": per_approve_ev,
            "costly_blocks_usd": costly_blocks_usd,
            "good_blocks_saved_usd": good_blocks_saved_usd,
            **v2_lane_extra,
        }
        if all_trade_df is not None:
            lane_metrics[lane]["all_time"] = _all_time_lane_metrics(all_trade_df, lane)

    _inject_continuous_benchmark_lane(lane_metrics, lanes_ordered)

    bench = lane_metrics.get(BENCHMARK_LANE) or _empty_lane_benchmark_metrics()
    for lane, metrics in lane_metrics.items():
        if lane == BENCHMARK_LANE:
            metrics["delta_approve_to_fill_pct"] = 0.0
            metrics["delta_net_pnl_real"] = 0.0
            metrics["delta_per_approve_ev"] = 0.0
            metrics["delta_costly_blocks_usd"] = 0.0
            metrics["verdict"] = _benchmark_lane_verdict(lane, {}, bench)
            continue
        if metrics["approves"] == 0:
            metrics["delta_approve_to_fill_pct"] = None
            metrics["delta_net_pnl_real"] = None
            metrics["delta_per_approve_ev"] = None
            metrics["delta_costly_blocks_usd"] = None
            metrics["verdict"] = "no session approves"
            continue
        metrics["delta_approve_to_fill_pct"] = round(metrics["approve_to_fill_pct"] - bench["approve_to_fill_pct"], 1)
        metrics["delta_net_pnl_real"] = round(metrics["net_pnl_real"] - bench["net_pnl_real"], 2)
        metrics["delta_per_approve_ev"] = round(metrics["per_approve_ev"] - bench["per_approve_ev"], 2)
        metrics["delta_costly_blocks_usd"] = round(metrics["costly_blocks_usd"] - bench["costly_blocks_usd"], 2)
        metrics["verdict"] = _benchmark_lane_verdict(
            lane,
            {
                "delta_approve_to_fill_pct": metrics["delta_approve_to_fill_pct"],
                "delta_per_approve_ev": metrics["delta_per_approve_ev"],
                "delta_costly_blocks_usd": metrics["delta_costly_blocks_usd"],
            },
            bench,
        )

    hdr = f"{'Lane':<16}{'Approves':>9}{'Fills':>7}{'Fill%':>7}{'RealPnL':>9}{'ShadowBlk':>11}{'PerApprEV':>11}{'Δ vs CONT':>14}"
    print(f"\n{hdr}")
    for lane in lanes_ordered:
        if lane not in lane_metrics:
            continue
        m = lane_metrics[lane]
        label = RESEARCH_LANE_LABELS.get(lane, lane)[:16]
        if lane == BENCHMARK_LANE:
            delta_str = "—"
        elif m["approves"] == 0 or m.get("delta_approve_to_fill_pct") is None:
            delta_str = "n/a"
        else:
            delta_str = f"Δ fill {m['delta_approve_to_fill_pct']:+.0f}%  Δ PnL ${m['delta_net_pnl_real']:+.2f}"
        print(
            f"{label:<16}{m['approves']:>9}{m['real_fills']:>7}{m['approve_to_fill_pct']:>6.1f}%"
            f"{m['net_pnl_real']:>9.2f}{m['net_pnl_shadow_blocked']:>11.2f}{m['per_approve_ev']:>11.2f}"
            f"{delta_str:>14}"
        )

    print(f"\n--- vs {BENCHMARK_LANE} benchmark ---")
    for lane in lanes_ordered:
        if lane == BENCHMARK_LANE or lane not in lane_metrics:
            continue
        m = lane_metrics[lane]
        if m["approves"] == 0 or m.get("delta_approve_to_fill_pct") is None:
            print(f"{lane}: no session approves {PIPELINE_ENFORCEMENT_TAG}")
            continue
        print(
            f"{lane}: Δ fill {m['delta_approve_to_fill_pct']:+.0f}%  "
            f"Δ PnL ${m['delta_net_pnl_real']:+.2f}  ({m['verdict']}) {PIPELINE_ENFORCEMENT_TAG}"
        )

    report = {
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "benchmark_lane": BENCHMARK_LANE,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lanes": lane_metrics,
    }
    try:
        with open(analyzer_report_path(BENCHMARK_VS_LANES_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n  ✅ Wrote {BENCHMARK_VS_LANES_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"\n  ⚠️ Could not write {BENCHMARK_VS_LANES_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)
    return report


def _static_pathway_lane_specs():
    """Frozen lane definitions — entry/exit params and explicit diff vs CONTINUOUS benchmark."""
    scenario_c_exit = {
        "profile": "Scenario C",
        "ladder": SCENARIO_C_LADDER_LABEL,
        "thesis_stop_margin_pct": THESIS_FAST_EXIT_DEFAULT,
        "mfe_protect_margin_pct": THESIS_MFE_PROTECT_DEFAULT,
        "thesis_pause_above_margin_pct": THESIS_EXIT_ABOVE_DEFAULT,
        "type_a_stall": "OFF",
        "fixed_time_exit": "2h global (7200s)",
    }
    runner_exit = {
        "profile": "Scenario C Runner Variant",
        "ladder": "18→14, 25→18, 40→28, 55→38",
        "thesis_stop_margin_pct": THESIS_FAST_EXIT_DEFAULT,
        "mfe_protect_margin_pct": THESIS_MFE_PROTECT_DEFAULT,
        "thesis_pause_above_margin_pct": THESIS_EXIT_ABOVE_DEFAULT,
        "type_a_stall": "OFF",
        "fixed_time_exit": "2h global (7200s)",
    }
    ai_direct = {
        "entry_path": "AI_DIRECT",
        "fill_path": "AI_DIRECT_CHASE",
        "ai_path": "same as CONTINUOUS benchmark",
        "execution": "fills-first, chase 0s",
        "post_ai_gates": "log-only telemetry",
        "margin_usd": FLAT_MARGIN_LIVE_USD,
    }
    promote = "Per-trade EV > CONTINUOUS; session PnL > CONTINUOUS"
    kill = "EV <= CONTINUOUS benchmark over rolling window"
    return {
        BENCHMARK_LANE: {
            "lane": BENCHMARK_LANE,
            "label": RESEARCH_LANE_LABELS.get(BENCHMARK_LANE, BENCHMARK_LANE),
            "subtitle": "FROZEN SCENARIO C BENCHMARK",
            "role": "yardstick — all experiments compared to this",
            "is_benchmark": True,
            "badge": "★ BENCHMARK",
            "toggle_key": "continuous_ai_research_enabled",
            "hypothesis": "Periodic AI + AI_DIRECT is the minimum viable research baseline.",
            "research_question": "What is baseline approve→fill→PnL under frozen Scenario C?",
            "entry": {"trigger": "~180s AI when edge > 0", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": "N/A — benchmark",
            "kill_criteria": "N/A — benchmark",
            "expected_advantage": "Reference yardstick",
            "expected_risk": "Baseline drawdown profile",
            "benchmark_comparison": "Self",
            "diff_vs_benchmark": [],
        },
        "HIGH_EDGE_RUNNER": {
            "lane": "HIGH_EDGE_RUNNER",
            "label": RESEARCH_LANE_LABELS["HIGH_EDGE_RUNNER"],
            "subtitle": "EDGE≥3.5 · VOL≥1.5 · RUNNER EXIT PROFILE",
            "role": "high-edge + volume continuation with wider runner exits",
            "parent_lane": BENCHMARK_LANE,
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Strong edge + elevated volume deserves wider profit ladder rungs.",
            "research_question": "Does runner exit capture more tail on high-edge/high-volume approves?",
            "entry": {"trigger": "spawn on CONTINUOUS APPROVE when edge≥3.5 & vol_ratio≥1.5", **ai_direct},
            "exit": runner_exit,
            "exit_path": "Scenario C Runner Variant (18→14 first rung)",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Higher MFE capture on runners",
            "expected_risk": "Wider ladder gives back more peak profit",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: edge≥3.5 & vol_ratio≥1.5", "Exit: runner ladder 18→14 vs 12→8"],
        },
        "EXTREME_EDGE": {
            "lane": "EXTREME_EDGE",
            "label": RESEARCH_LANE_LABELS["EXTREME_EDGE"],
            "subtitle": "EDGE≥4.5 ONLY · RETIRED",
            "role": "retired — edge has no predictive value; historical analytics only",
            "parent_lane": BENCHMARK_LANE,
            "status": "RETIRED",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Tail-edge approves outperform average edge band.",
            "research_question": "Is edge≥4.5 sufficient alone for superior EV?",
            "entry": {"trigger": "spawn on CONTINUOUS APPROVE when edge≥4.5", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": "N/A — retired",
            "kill_criteria": "RETIRED — edge hypothesis failed validation",
            "expected_advantage": "N/A",
            "expected_risk": "N/A",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: edge≥4.5 only", "Exit: frozen Scenario C"],
        },
        "SHORT_BEAR_ALPHA": {
            "lane": "SHORT_BEAR_ALPHA",
            "label": RESEARCH_LANE_LABELS["SHORT_BEAR_ALPHA"],
            "subtitle": "SHORT · struct≤-3 · bear>bull · spread≥3 · AI≥55",
            "role": "bearish regime asymmetry — highest session edge cohort",
            "parent_lane": BENCHMARK_LANE,
            "status": "LIVE TEST",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Short + bear structure + wide spread beats broad CONTINUOUS.",
            "research_question": "Can directional/regime filter beat benchmark EV?",
            "entry": {
                "trigger": "spawn SHORT when structure≤-3, bear>bull, spread≥3, AI≥55%",
                **ai_direct,
            },
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Session data: SHORT 71% WR vs LONG 56%",
            "expected_risk": "Direction filter reduces spawn rate",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: SHORT bear-alpha fingerprint", "Exit: frozen Scenario C"],
        },
        "AI_60_65_ALPHA": {
            "lane": "AI_60_65_ALPHA",
            "label": RESEARCH_LANE_LABELS["AI_60_65_ALPHA"],
            "subtitle": "AI 60-65 · spread≥3 · edge≥3",
            "role": "strongest AI confidence band (82% WR in session)",
            "parent_lane": BENCHMARK_LANE,
            "status": "LIVE TEST",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "AI 60-65 band outperforms 65+ and 55-60 bands.",
            "research_question": "Does mid-high confidence alone beat benchmark?",
            "entry": {"trigger": "spawn when 60≤AI<65, spread≥3, edge≥3", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Session 60-65 band: 82% WR, +$122",
            "expected_risk": "Narrow AI band — sparse spawns",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: AI 60-65 + spread≥3 + edge≥3", "Exit: frozen Scenario C"],
        },
        "EDGE_PLUS_STACK": {
            "lane": "EDGE_PLUS_STACK",
            "label": RESEARCH_LANE_LABELS["EDGE_PLUS_STACK"],
            "subtitle": "EDGE≥3.5 · GS PASS · RETIRED",
            "role": "retired — edge + extra filters; historical analytics only",
            "parent_lane": BENCHMARK_LANE,
            "status": "RETIRED",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Golden-stack pass filters noise without blocking benchmark.",
            "research_question": "Does GS-pass subset beat raw edge≥3.5?",
            "entry": {"trigger": "spawn when edge≥3.5 AND golden_stack_eval pass", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": "N/A — retired",
            "kill_criteria": "RETIRED — edge stack adds complexity without alpha",
            "expected_advantage": "N/A",
            "expected_risk": "N/A",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: edge≥3.5 + GS eval pass", "Exit: frozen Scenario C"],
        },
        "SHADOW_RUNNER": {
            "lane": "SHADOW_RUNNER",
            "label": RESEARCH_LANE_LABELS["SHADOW_RUNNER"],
            "subtitle": "POST-EXIT HORIZON STUDY · PROBATION",
            "role": "shadow-only — retire if no unique EV contribution",
            "parent_lane": BENCHMARK_LANE,
            "status": "PROBATION",
            "live_trading": False,
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Post-approve price paths reveal missed runner opportunity.",
            "research_question": "What is +15/+30/+60/+90m outcome after APPROVE at edge≥3.5?",
            "entry": {"trigger": "shadow log on CONTINUOUS APPROVE edge≥3.5", "entry_path": "SHADOW", "fill_path": "NONE"},
            "exit": {"profile": "horizon study", "horizons_min": "15, 30, 60, 90"},
            "exit_path": "Horizon counterfactual (+15/+30/+60/+90m)",
            "promotion_criteria": "Inform runner exit design — not promoted to live",
            "kill_criteria": "N/A — observational",
            "expected_advantage": "Counterfactual insight",
            "expected_risk": "None — shadow only",
            "benchmark_comparison": "vs CONTINUOUS post-approve paths",
            "diff_vs_benchmark": ["Live trading OFF", "Measures post-approve horizons"],
        },
        "EDGE_ALPHA_4": {
            "lane": "EDGE_ALPHA_4",
            "label": RESEARCH_LANE_LABELS["EDGE_ALPHA_4"],
            "subtitle": "EDGE >= 4.0 · NEAR_SUPPORT · Scenario C",
            "role": "high-edge concentration near support",
            "parent_lane": BENCHMARK_LANE,
            "status": "LIVE TEST",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Edge 4+ near support outperforms the broad CONTINUOUS cohort.",
            "research_question": "Can Edge 4+ outperform benchmark?",
            "entry": {"trigger": "spawn on CONTINUOUS APPROVE when edge>=4.0 & NEAR_SUPPORT", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Elite edge band (session WR ~74% on edge 4+)",
            "expected_risk": "Lower spawn rate vs CONTINUOUS",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: edge>=4.0 & NEAR_SUPPORT", "Exit: frozen Scenario C"],
        },
        "TYPE_B_HUNTER": {
            "lane": "TYPE_B_HUNTER",
            "label": RESEARCH_LANE_LABELS["TYPE_B_HUNTER"],
            "subtitle": "Edge>=3.5 · Vol>1.2 · NEAR_SUPPORT · AI 50-55",
            "role": "pre-Type-B fingerprint before entry",
            "parent_lane": BENCHMARK_LANE,
            "status": "LIVE TEST",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Type-B winners share edge, volume, SR, and AI-prob fingerprints.",
            "research_question": "Can we predict Type B before entry?",
            "entry": {"trigger": "spawn when edge>=3.5, vol_ratio>1.2, NEAR_SUPPORT, AI 50-55%", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Capture Type-B runner profile early",
            "expected_risk": "Strict filter — sparse samples",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": [
                "Activation: edge>=3.5, vol>1.2, NEAR_SUPPORT, AI 50-55",
                "Exit: frozen Scenario C",
            ],
        },
        "URGENT_CHASE_ALPHA": {
            "lane": "URGENT_CHASE_ALPHA",
            "label": RESEARCH_LANE_LABELS["URGENT_CHASE_ALPHA"],
            "subtitle": "VELOCITY-AWARE CHASE · SAME ENTRY/EXIT AS BENCHMARK",
            "role": "execution experiment — velocity-aware chase vs benchmark 25% step",
            "parent_lane": BENCHMARK_LANE,
            "status": "ACTIVE",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Velocity-aware chase captures more profitable fills than fixed 25% step.",
            "research_question": "Does market-velocity chase beat CONTINUOUS on EV or net PnL?",
            "entry": {
                "trigger": "spawn on every CONTINUOUS APPROVE — same AI, same limit plan",
                "fill_path": "URGENT_VELOCITY_CHASE",
                **{k: v for k, v in ai_direct.items() if k != "fill_path"},
                "execution": "normal 25% · medium 50% · high 75% · extreme marketable",
            },
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": "Retire if EV and net PnL do not beat CONTINUOUS after adequate sample",
            "expected_advantage": "Better fills in fast markets without changing AI or exits",
            "expected_risk": "Over-chasing in chop",
            "benchmark_comparison": "vs CONTINUOUS chase (25% fixed step)",
            "diff_vs_benchmark": [
                "Chase only: normal 25% / medium 50% / high 75% / extreme marketable",
                "Entry, AI, Scenario C exits, TTL: frozen same as CONTINUOUS",
            ],
        },
        "CHASE_3PLUS_ALPHA": {
            "lane": "CHASE_3PLUS_ALPHA",
            "label": RESEARCH_LANE_LABELS["CHASE_3PLUS_ALPHA"],
            "subtitle": "DELAYED ENTRY · VIRTUAL CHASE ≥3 OR 180s",
            "role": "late-entry experiment — observe persistence before first limit",
            "parent_lane": BENCHMARK_LANE,
            "status": "ACTIVE",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Trades that would reach chase #3+ are stronger; delayed entry avoids weak early fills.",
            "research_question": "Does waiting for virtual chase persistence beat immediate CONTINUOUS entry?",
            "entry": {
                "trigger": "spawn on every CONTINUOUS APPROVE — observe, do not submit immediately",
                "entry_path": "AI_DIRECT",
                "fill_path": "AI_DIRECT_CHASE",
                "ai_path": "same as CONTINUOUS benchmark",
                "execution": "activate when virtual_chase≥3 OR age≥180s OR signal distance threshold; then normal chase",
                "post_ai_gates": "log-only telemetry",
                "margin_usd": FLAT_MARGIN_LIVE_USD,
            },
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": "Retire if EV and net PnL do not beat CONTINUOUS after adequate sample",
            "expected_advantage": "Skip 0–2 chase noise cohort; enter demonstrated trends",
            "expected_risk": "Miss early fills; correlation≠causation on chase buckets",
            "benchmark_comparison": "vs CONTINUOUS immediate entry",
            "diff_vs_benchmark": [
                "Entry delay: virtual chase observation before first limit",
                "After activation: same AI limit, 25% chase, Scenario C, TTL",
            ],
        },
        "TYPE_B_PREDICTOR_V1": {
            "lane": "TYPE_B_PREDICTOR_V1",
            "label": RESEARCH_LANE_LABELS.get("TYPE_B_PREDICTOR_V1", "Type B Predictor v1"),
            "subtitle": "AI≥60 · spread≥4 · ADX≥20 · vol≥1.8 · struct≤-3",
            "role": "pre-entry Type-B fingerprint test",
            "parent_lane": BENCHMARK_LANE,
            "status": "ACTIVE",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Entry features matching Type-B averages predict outsized MFE at entry.",
            "research_question": "Can we identify Type-B runners before peak MFE is known?",
            "entry": {
                "trigger": "spawn on AI_SCAN APPROVE when predictor filters pass",
                **ai_direct,
            },
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "TYPE_B cohort historically +$56 vs TYPE_A −$69 on session sample",
            "expected_risk": "Filter stack may over-constrain spawn rate",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Entry: Type-B predictor fingerprint", "Exit: frozen Scenario C"],
        },
        "RECOVERY_MONSTER_V1": {
            "lane": "RECOVERY_MONSTER_V1",
            "label": RESEARCH_LANE_LABELS.get("RECOVERY_MONSTER_V1", "Recovery Monster v1"),
            "subtitle": "Benchmark entry · thesis −40% · ladder 18→14",
            "role": "exit-only experiment — wide thesis + runner ladder",
            "parent_lane": BENCHMARK_LANE,
            "status": "ACTIVE",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "−12% thesis fast-cut kills recoverable trades; replay sweep favors −40%.",
            "research_question": "Do exits (not entries) explain benchmark underperformance?",
            "entry": {"trigger": "spawn on every AI_SCAN APPROVE (benchmark entry)", **ai_direct},
            "exit": {
                "profile": "Recovery Monster v1",
                "ladder": "18→14, 25→18, 40→28, 55→38",
                "thesis_stop_margin_pct": -40.0,
                "mfe_protect_margin_pct": THESIS_MFE_PROTECT_DEFAULT,
                "thesis_pause_above_margin_pct": THESIS_EXIT_ABOVE_DEFAULT,
                "type_a_stall": "OFF",
                "fixed_time_exit": "2h global (7200s)",
            },
            "exit_path": "Thesis −40% · ladder 18→14 · MFE protect 2%",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Replay grid: +$79 vs live −12% on sample",
            "expected_risk": "Wider stop increases tail loss on true failures",
            "benchmark_comparison": "vs CONTINUOUS Scenario C entry",
            "diff_vs_benchmark": ["Entry: same as benchmark", "Exit: −40% thesis + runner ladder"],
        },
        "AI_DISAGREEMENT_ALPHA": {
            "lane": "AI_DISAGREEMENT_ALPHA",
            "label": RESEARCH_LANE_LABELS.get("AI_DISAGREEMENT_ALPHA", "AI Disagreement · AI Wins"),
            "subtitle": "AI APPROVE + replay REJECT",
            "role": "disagreement cohort — AI approves, replay scorecard rejects",
            "parent_lane": BENCHMARK_LANE,
            "status": "ACTIVE",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "AI may outperform deterministic replay on disagreement.",
            "research_question": "Does AI APPROVE + replay REJECT still carry edge?",
            "entry": {"trigger": "spawn when AI APPROVE and replay_approve=False", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "Tests LLM vs replay where they diverge",
            "expected_risk": "Replay may be right — AI false positives",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: AI/replay disagreement (AI side)"],
        },
        "AI_DISAGREEMENT_REPLAY": {
            "lane": "AI_DISAGREEMENT_REPLAY",
            "label": RESEARCH_LANE_LABELS.get("AI_DISAGREEMENT_REPLAY", "AI Disagreement · Replay Wins"),
            "subtitle": "AI REJECT + replay APPROVE",
            "role": "disagreement cohort — replay approves, AI rejected",
            "parent_lane": BENCHMARK_LANE,
            "status": "ACTIVE",
            "toggle_key": "research_lane_enabled",
            "hypothesis": "Replay-approved signals that AI skipped are hidden alpha.",
            "research_question": "Does replay find winners the LLM rejects?",
            "entry": {"trigger": "spawn when AI REJECT and replay_approve=True", **ai_direct},
            "exit": scenario_c_exit,
            "exit_path": "Scenario C frozen",
            "promotion_criteria": promote,
            "kill_criteria": kill,
            "expected_advantage": "201/242 AI calls disagreed with replay in session",
            "expected_risk": "Replay model may be overfit to features",
            "benchmark_comparison": "vs CONTINUOUS Scenario C",
            "diff_vs_benchmark": ["Activation: AI/replay disagreement (replay side)"],
        },
    }


def _horizon_outcome_30m_pct(trade_id, snapshots, reversal_index, shadow_row=None, replay=None):
    """Resolve +30m outcome % from snapshot, reversal study, shadow, or replay ticks."""
    tid = str(trade_id)
    snap = snapshots.get(tid) or {}
    pbr = snap.get("post_block_research") or {}
    if pbr.get("outcome_30m_pct") is not None:
        return float(pbr["outcome_30m_pct"])
    outcome = snap.get("outcome") or {}
    ho = outcome.get("horizon_outcomes") or {}
    if ho.get("outcome_30m_pct") is not None:
        return float(ho["outcome_30m_pct"])
    if outcome.get("outcome_30m_pct") is not None:
        return float(outcome["outcome_30m_pct"])
    rev = reversal_index.get(tid) or {}
    if rev.get("outcome_30m_pct") is not None:
        return float(rev["outcome_30m_pct"])
    horizons = (rev.get("horizons") or {})
    if horizons.get("outcome_30m_pct") is not None:
        return float(horizons["outcome_30m_pct"])
    if shadow_row:
        spbr = shadow_row.get("post_block_research") or {}
        if spbr.get("outcome_30m_pct") is not None:
            return float(spbr["outcome_30m_pct"])
        if shadow_row.get("outcome_30m_pct") is not None:
            return float(shadow_row["outcome_30m_pct"])
    if replay:
        ticks = sorted(replay.get("ticks") or [], key=lambda x: float(x.get("t") or 0))
        start_price = _replay_entry_price(replay) or float(replay.get("start_price") or 0)
        direction = str(replay.get("direction") or "LONG").upper()
        leverage = int(replay.get("leverage") or 100)
        if ticks and start_price > 0:
            dir_factor = 1 if direction == "LONG" else -1
            last_unreal = None
            for tick in ticks:
                t = float(tick.get("t") or 0)
                p = float(tick.get("price") or 0)
                if p <= 0:
                    continue
                unreal = tick.get("unreal_pct")
                if unreal is None:
                    unreal = ((p - start_price) / start_price) * dir_factor * leverage * 100
                else:
                    unreal = float(unreal)
                last_unreal = unreal
                if t >= HORIZON_30M_SEC:
                    return round(unreal, 4)
            if last_unreal is not None:
                return round(last_unreal, 4)
    return None


def _horizon_pnl_usd_from_pct(outcome_30m_pct, margin_usdt=None):
    if outcome_30m_pct is None:
        return None
    margin = float(margin_usdt if margin_usdt is not None else FLAT_MARGIN_LIVE_USD)
    return round(float(outcome_30m_pct) / 100.0 * margin, 2)


def _build_reversal_outcome_index():
    index = {}
    for row in load_reversal_study():
        if str(row.get("phase") or "") != "outcome":
            continue
        tid = str(row.get("trade_id") or row.get("study_id") or "").replace("rev-", "")
        if tid:
            index[tid] = row
    return index


def _classify_horizon_counterfactual(row):
    """Classify blocked shadow APPROVE by +30m horizon vs shadow fill outcome."""
    filled = _truthy(row.get("shadow_filled"))
    outcome_30m = row.get("outcome_30m_pct")
    shadow_net = row.get("shadow_net_usd")
    exit_t = row.get("shadow_exit_t_sec")
    if not filled:
        return "FILL_FAILURE"
    if outcome_30m is not None and float(outcome_30m) > 0 and shadow_net is not None and float(shadow_net) > 0:
        return "HORIZON_WINNER"
    if (
        outcome_30m is not None
        and float(outcome_30m) > 0
        and shadow_net is not None
        and float(shadow_net) <= 0
        and exit_t is not None
        and float(exit_t) < HORIZON_30M_SEC
    ):
        return "EARLY_EXIT_MISS"
    if outcome_30m is not None and float(outcome_30m) <= 0:
        return "GOOD_BLOCK"
    if shadow_net is not None and float(shadow_net) <= 0:
        return "GOOD_BLOCK"
    if outcome_30m is not None and float(outcome_30m) > 0:
        return "HORIZON_WINNER"
    return "GOOD_BLOCK"


def horizon_counterfactual_report(trades=None, session=None, shadow_report=None, blocked=None):
    """
    If we waited 30 more minutes, what would blocked APPROVEs have made?
    Uses shadow_outcome, signal_snapshot horizons, reversal_study, signal_replay.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== HORIZON COUNTERFACTUAL — {scope.lower()} {PIPELINE_ENFORCEMENT_TAG} ===")

    shadow_df = _load_shadow_outcome_df(session)
    if shadow_df is None or shadow_df.empty:
        print(f"  No shadow_outcome rows for {scope.lower()} scope. {PIPELINE_ENFORCEMENT_TAG}")
        return None

    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        executed_ids = set(trades["trade_id"].dropna().astype(str))
    else:
        executed_ids = _load_executed_trade_ids()

    snapshots_all = _load_signal_snapshots()
    snapshots = _filter_snapshots_by_session(snapshots_all, session)
    replays = _load_jsonl_replays()
    reversal_index = _build_reversal_outcome_index()
    funnel_index = _build_funnel_trade_index()
    block_map = _blocked_reason_by_trade_id(blocked)
    expired_lanes = {}
    if os.path.exists(EXPIRED_ORDERS_FILE):
        try:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="utf-8", usecols=["trade_id", "research_lane"])
        except Exception:
            exp = pd.DataFrame()
        for _, r in exp.iterrows():
            tid = str(r.get("trade_id") or "").strip()
            if tid and pd.notna(r.get("research_lane")):
                expired_lanes[tid] = r.get("research_lane")

    work = shadow_df.copy()
    if "filled" in work.columns:
        work["filled"] = work["filled"].apply(_truthy)
    else:
        work["filled"] = True
    work["net_pnl_usd"] = pd.to_numeric(work.get("net_pnl_usd"), errors="coerce")
    work["research_lane"] = work.apply(
        lambda r: _resolve_shadow_research_lane(r, funnel_index, snapshots_all, expired_lanes),
        axis=1,
    )

    blocked_rows = []
    for _, row in work.iterrows():
        tid = str(row.get("trade_id") or "")
        if not tid or tid in executed_ids:
            continue
        br = block_map.get(tid) or row.get("block_reason") or ""
        if not _is_blocked_approve_lane(br) and not str(br).startswith("WOULD_"):
            snap = snapshots.get(tid) or {}
            if snap.get("executed"):
                continue
            if not br and not snap.get("block_reason"):
                continue
        replay = replays.get(tid)
        shadow_dict = row.to_dict()
        outcome_30m = _horizon_outcome_30m_pct(tid, snapshots, reversal_index, shadow_dict, replay)
        margin = float(
            row.get("margin_usdt")
            or (snapshots.get(tid) or {}).get("config", {}).get("margin_usdt")
            or FLAT_MARGIN_LIVE_USD
        )
        shadow_exit_t = None
        if replay:
            ticks = replay.get("ticks") or []
            if ticks:
                shadow_exit_t = max(float(t.get("t") or 0) for t in ticks)
        elif shadow_dict.get("post_block_research"):
            shadow_exit_t = shadow_dict["post_block_research"].get("post_block_duration_sec")
        classified = {
            "trade_id": tid,
            "research_lane": row.get("research_lane"),
            "block_reason": br,
            "shadow_filled": _truthy(row.get("filled")),
            "shadow_net_usd": row.get("net_pnl_usd"),
            "outcome_30m_pct": outcome_30m,
            "horizon_pnl_usd": _horizon_pnl_usd_from_pct(outcome_30m, margin),
            "shadow_exit_t_sec": shadow_exit_t,
        }
        classified["classification"] = _classify_horizon_counterfactual(classified)
        blocked_rows.append(classified)

    cohort_n = len(blocked_rows)
    print(f"Blocked APPROVE cohort: {cohort_n}")

    if not blocked_rows:
        print(f"  No blocked APPROVE shadow paths in scope. {PIPELINE_ENFORCEMENT_TAG}")
        report = {
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "analyzer_version": ANALYZER_VERSION,
            "session_scope": scope,
            "blocked_approve_cohort": 0,
            "summary": {},
            "by_lane": {},
            "vs_continuous": {},
        }
        try:
            with open(HORIZON_COUNTERFACTUAL_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Could not write {HORIZON_COUNTERFACTUAL_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return report

    df = pd.DataFrame(blocked_rows)
    has_30m = df[df["outcome_30m_pct"].notna()]
    winners = df[df["classification"] == "HORIZON_WINNER"]
    fill_fail = df[df["classification"] == "FILL_FAILURE"]
    early_miss = df[df["classification"] == "EARLY_EXIT_MISS"]
    good_block = df[df["classification"] == "GOOD_BLOCK"]

    def _avg_pct(sub):
        vals = pd.to_numeric(sub.get("outcome_30m_pct"), errors="coerce").dropna()
        return round(float(vals.mean()), 2) if len(vals) else None

    def _sum_horizon_usd(sub):
        vals = pd.to_numeric(sub.get("horizon_pnl_usd"), errors="coerce").dropna()
        return round(float(vals.sum()), 2) if len(vals) else 0.0

    print(f"\n{'Horizon (post-APPROVE)':<34} {'Count':>6}  {'Avg +30m%':>10}  {'Shadow+$ at 30m':>16}")
    print(f"{'Had +30m data':<34} {len(has_30m):>6}  {(_avg_pct(has_30m) or 0):>9}%  ${_sum_horizon_usd(has_30m):>14.2f}")
    print(
        f"{'Would have won at +30m':<34} {len(winners):>6}  "
        f"{(_avg_pct(winners) or 0):>9}%  ${_sum_horizon_usd(winners):>14.2f}"
    )
    print(f"{'Missed: never filled':<34} {len(fill_fail):>6}")
    print(f"{'Missed: exited early (had +30m)':<34} {len(early_miss):>6}")
    print(f"{'Missed: block was good (+30m ≤0)':<34} {len(good_block):>6}")

    by_lane = {}
    for lane, sub in df.groupby("research_lane"):
        by_lane[str(lane)] = {
            "blocked_approve_cohort": int(len(sub)),
            "had_30m_data": int(sub["outcome_30m_pct"].notna().sum()),
            "avg_outcome_30m_pct": _avg_pct(sub),
            "horizon_pnl_usd_sum": _sum_horizon_usd(sub),
            "horizon_winners": int((sub["classification"] == "HORIZON_WINNER").sum()),
            "fill_failures": int((sub["classification"] == "FILL_FAILURE").sum()),
            "early_exit_miss": int((sub["classification"] == "EARLY_EXIT_MISS").sum()),
            "good_blocks": int((sub["classification"] == "GOOD_BLOCK").sum()),
            "shadow_net_usd_sum": round(float(pd.to_numeric(sub["shadow_net_usd"], errors="coerce").sum()), 2),
        }

    bench = by_lane.get(BENCHMARK_LANE) or {}
    vs_continuous = {}
    print(f"\n--- vs {BENCHMARK_LANE} benchmark ---")
    for lane in sorted(by_lane.keys()):
        if lane == BENCHMARK_LANE:
            continue
        lm = by_lane[lane]
        delta_winners = lm.get("horizon_winners", 0) - bench.get("horizon_winners", 0)
        delta_horizon = round(
            float(lm.get("horizon_pnl_usd_sum") or 0) - float(bench.get("horizon_pnl_usd_sum") or 0),
            2,
        )
        vs_continuous[lane] = {
            "delta_horizon_winners": delta_winners,
            "delta_horizon_pnl_usd": delta_horizon,
            "delta_good_blocks": lm.get("good_blocks", 0) - bench.get("good_blocks", 0),
        }
        print(
            f"{lane}: winners {lm.get('horizon_winners', 0)} (Δ{delta_winners:+d}) | "
            f"+30m $ {lm.get('horizon_pnl_usd_sum', 0):.2f} (Δ${delta_horizon:+.2f}) | "
            f"good blocks {lm.get('good_blocks', 0)}"
        )

    report = {
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "blocked_approve_cohort": cohort_n,
        "summary": {
            "had_30m_data": len(has_30m),
            "avg_outcome_30m_pct": _avg_pct(has_30m),
            "horizon_pnl_usd_sum": _sum_horizon_usd(has_30m),
            "horizon_winners": len(winners),
            "fill_failures": len(fill_fail),
            "early_exit_miss": len(early_miss),
            "good_blocks": len(good_block),
        },
        "by_lane": by_lane,
        "vs_continuous": vs_continuous,
        "rows": blocked_rows,
    }
    try:
        with open(HORIZON_COUNTERFACTUAL_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"  ✅ Wrote {HORIZON_COUNTERFACTUAL_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {HORIZON_COUNTERFACTUAL_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)
    return report


def _filter_jsonl_rows_by_session(rows, session: dict = None):
    """Filter JSONL dict rows to current research session."""
    if not rows:
        return []
    if not session or _session_start_ts(session) is None:
        return rows
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    filtered = filter_df_since_session(df, session, ts_cols=("ts", "timestamp"))
    return filtered.to_dict("records") if not filtered.empty else []


def _ai_calib_report_bucket(prob):
    """Confidence bucket for AI calibration report (50-55, 55-60, 60-65, 65+)."""
    p = float(prob or 0)
    if p < 55:
        return "50-55"
    if p < 60:
        return "55-60"
    if p < 65:
        return "60-65"
    return "65+"


def _ai_matrix_conf_bucket(prob):
    """Coarse AI bucket for confidence×edge matrix (50-55, 55-60 only)."""
    p = float(prob or 0)
    if 50 <= p < 55:
        return "50-55"
    if 55 <= p < 60:
        return "55-60"
    return None


def _ai_matrix_edge_bucket(edge):
    """Coarse edge bucket for confidence×edge matrix (2-3, 3-4, 4+)."""
    e = float(edge or 0)
    if 2.0 <= e < 3.0:
        return "2-3"
    if 3.0 <= e < 4.0:
        return "3-4"
    if e >= 4.0:
        return "4+"
    return None


def _structure_attribution_bucket(structure_score):
    try:
        s = float(structure_score)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if s <= 1:
        return "WEAK"
    if s <= 2:
        return "MODERATE"
    return "STRONG"


def _participation_attribution_bucket(volume_ratio):
    try:
        v = float(volume_ratio)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if v < 0.5:
        return "LOW"
    if v < 1.0:
        return "MID"
    return "HIGH"


def _context_attribution_bucket(sr_state, session_bucket):
    sr = str(sr_state or "").upper()
    if "COMPRESSION" in sr:
        return "COMPRESSION"
    if "SUPPORT" in sr:
        return "NEAR_SUPPORT"
    if "RESISTANCE" in sr:
        return "NEAR_RESISTANCE"
    sess = str(session_bucket or "").upper()
    if sess in SESSION_BUCKET_ORDER:
        return sess
    if sess in ("EU", "LONDON"):
        return "LONDON"
    if sess in ("US", "NEW_YORK", "NY"):
        return "NEW_YORK"
    if sess in ("ASIA", "APAC"):
        return "ASIA"
    return "MID_RANGE"


def _regime_attribution_bucket(regime_label):
    r = str(regime_label or "UNKNOWN").upper()
    if r in ("", "NAN", "NONE"):
        return "UNKNOWN"
    return r


def _ai_calib_cohort_stats(sub):
    """Return trades, WR, avg PnL, EV for a cohort sub-frame."""
    if sub is None or sub.empty:
        return {"trades": 0, "win_rate_pct": 0.0, "avg_pnl_usd": 0.0, "ev_usd": 0.0, "sum_pnl_usd": 0.0}
    pnl = pd.to_numeric(sub.get("net_pnl_usd"), errors="coerce")
    valid = pnl.notna()
    n = int(valid.sum())
    if n == 0:
        return {"trades": 0, "win_rate_pct": 0.0, "avg_pnl_usd": 0.0, "ev_usd": 0.0, "sum_pnl_usd": 0.0}
    wins = int((pnl[valid] > 0).sum())
    avg = float(pnl[valid].mean())
    total = float(pnl[valid].sum())
    return {
        "trades": n,
        "win_rate_pct": round(100.0 * wins / n, 1),
        "avg_pnl_usd": round(avg, 2),
        "ev_usd": round(avg, 2),
        "sum_pnl_usd": round(total, 2),
    }


def _lane_trade_stats(trades, lane):
    """Win rate + PnL for a single research lane from executed trades."""
    if trades is None or trades.empty:
        return _ai_calib_cohort_stats(pd.DataFrame())
    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    if "research_lane" in work.columns:
        work = work[work["research_lane"].astype(str).str.upper() == str(lane).upper()]
    if "net_pnl_usd" not in work.columns and "outcome_net_pnl_usd" in work.columns:
        work = work.copy()
        work["net_pnl_usd"] = work["outcome_net_pnl_usd"]
    return _ai_calib_cohort_stats(work)


def _score_fingerprint_int(val):
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def _mtf_fingerprint_label(val):
    if val is None:
        return "UNKNOWN"
    s = str(val).upper().strip()
    if not s or s in ("NAN", "NONE", "UNKNOWN", "NULL"):
        return "UNKNOWN"
    if "BULL" in s and ("ALIGN" in s or "ALIGNED" in s):
        return "BULL_ALIGNED"
    if "BEAR" in s and ("ALIGN" in s or "ALIGNED" in s):
        return "BEAR_ALIGNED"
    if "MIX" in s or "DISAGREE" in s or "CONFLICT" in s or "NEUTRAL" in s:
        return "MIXED"
    if s in ("TRUE", "1", "AGREE", "ALIGNED"):
        return "ALIGNED"
    if s in ("FALSE", "0"):
        return "MIXED"
    return s[:32]


def _ai_reason_fingerprint(row):
    return {
        "bull_score": _score_fingerprint_int(row.get("bull_score")),
        "bear_score": _score_fingerprint_int(row.get("bear_score")),
        "structure": _score_fingerprint_int(row.get("structure_score")),
        "mtf": _mtf_fingerprint_label(row.get("mtf_agreement")),
    }


def _fingerprint_key(fp):
    return json.dumps(fp, sort_keys=True)


def _resolve_row_pnl_usd(trade=None, outcome=None, shadow=None, reason=None):
    for src in (trade, outcome, shadow, reason):
        if not src:
            continue
        pnl = src.get("net_pnl_usd")
        if pnl is None:
            pnl = src.get("shadow_pnl")
        if pnl is None:
            continue
        try:
            val = float(pnl)
            if not np.isnan(val):
                return val
        except (TypeError, ValueError):
            continue
    return None


def _build_ai_reason_outcome_rows(session=None, trades=None):
    """Merge ai_reason_v2 rows with trade / shadow / outcome PnL."""
    reasons = _filter_jsonl_rows_by_session(load_ai_reason_research(), session)
    outcome_by_id = {}
    for r in reasons:
        if r.get("schema") == "ai_reason_outcome_v1":
            tid = str(r.get("trade_id") or "")
            if tid:
                outcome_by_id[tid] = r

    trade_by_id = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        for _, row in trades.iterrows():
            tid = str(row.get("trade_id") or "")
            if tid:
                trade_by_id[tid] = row.to_dict()

    shadow_by_id = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    if session and _session_start_ts(session) is not None:
        shadow_df = _load_shadow_outcome_df(session)
        if shadow_df is not None and not shadow_df.empty:
            shadow_by_id = {
                str(r.get("trade_id")): r.to_dict()
                for _, r in shadow_df.iterrows()
                if r.get("trade_id")
            }

    rows = []
    for r in reasons:
        if r.get("schema") != "ai_reason_v2":
            continue
        tid = str(r.get("trade_id") or "")
        trade = trade_by_id.get(tid) or {}
        outcome = outcome_by_id.get(tid) or {}
        shadow = shadow_by_id.get(tid) or {}
        pnl = _resolve_row_pnl_usd(trade, outcome, shadow, r)
        ai_prob = r.get("ai_prob")
        try:
            ai_prob = float(ai_prob) if ai_prob is not None else None
        except (TypeError, ValueError):
            ai_prob = None
        direction = str(r.get("direction") or trade.get("final_direction") or trade.get("dir") or "").upper()
        if direction not in ("LONG", "SHORT"):
            direction = None
        rows.append({
            "trade_id": tid,
            "research_lane": str(r.get("research_lane") or trade.get("research_lane") or "UNKNOWN").upper(),
            "direction": direction,
            "ai_prob": ai_prob,
            "ai_confidence_bucket": _ai_calib_report_bucket(ai_prob) if ai_prob is not None else None,
            "fingerprint": _ai_reason_fingerprint(r),
            "net_pnl_usd": pnl,
            "win": float(pnl) > 0 if pnl is not None else None,
            "ai_decision": str(r.get("ai_decision") or "").upper(),
        })
    return rows


def _classify_missed_opportunity_reason(reason: str) -> str:
    """Normalize block reasons into heatmap buckets."""
    r = str(reason or "").upper()
    if not r:
        return "UNKNOWN"
    if "COOLDOWN" in r or "AI_COOLDOWN" in r or "REARM" in r:
        return "COOLDOWN"
    if "CAPACITY" in r or "MAX_LONG" in r or "MAX_SHORT" in r or "MAX_ACTIVE" in r or "SLOT" in r:
        return "CAPACITY"
    if "TTL" in r or "EXPIRED" in r or "TIMEOUT" in r or "STALE" in r or "ORDER_TIMEOUT" in r:
        return "ORDER_TIMEOUT"
    if "DUPLICATE" in r:
        return "DUPLICATE"
    if "ORDER_FAILED" in r or "EXECUTION" in r or "FILL_FAIL" in r:
        return "EXECUTION"
    if "WOULD_BLOCK" in r or "CHOP" in r or "GOLDEN" in r or "GATE" in r or "CTX" in r or "MTF" in r:
        return "POST_AI_GATE"
    if "EDGE" in r:
        return "EDGE_GATE"
    return "OTHER"


def _confidence_band_label(conf) -> str:
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return None
    if c < 45:
        return "0-45"
    if c < 50:
        return "45-50"
    if c < 55:
        return "50-55"
    if c < 60:
        return "55-60"
    if c < 65:
        return "60-65"
    return "65+"


def _direction_cohort_stats(sub):
    base = _ai_calib_cohort_stats(sub)
    if sub is None or sub.empty:
        base.update({"avg_edge": 0.0, "avg_ai_confidence": 0.0})
        return base
    edge = pd.to_numeric(sub.get("edge_score_at_entry", sub.get("edge_score")), errors="coerce")
    ai = pd.to_numeric(sub.get("ai_win_prob", sub.get("conf")), errors="coerce")
    base["avg_edge"] = round(float(edge.mean()), 2) if edge.notna().any() else 0.0
    base["avg_ai_confidence"] = round(float(ai.mean()), 1) if ai.notna().any() else 0.0
    return base


def direction_attribution_report(trades=None, decisions=None, session=None):
    """
    Direction Attribution Report - LONG vs SHORT performance overall and by regime.
    Writes direction_report.json.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== DIRECTION_ATTRIBUTION_REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    if trades is None or trades.empty:
        ai_call_stats = {"long_calls": 0, "short_calls": 0, "no_trade_calls": 0, "total_calls": 0}
        for row in _load_jsonl_rows(AI_REASON_RESEARCH_FILE):
            if row.get("schema") != "ai_reason_v2":
                continue
            ai_call_stats["total_calls"] += 1
            d = str(row.get("direction") or "").upper()
            if d == "LONG":
                ai_call_stats["long_calls"] += 1
            elif d == "SHORT":
                ai_call_stats["short_calls"] += 1
            else:
                ai_call_stats["no_trade_calls"] += 1
        report = {
            "schema": "direction_report_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "long": _direction_cohort_stats(pd.DataFrame()),
            "short": _direction_cohort_stats(pd.DataFrame()),
            "long_calls": ai_call_stats["long_calls"],
            "short_calls": ai_call_stats["short_calls"],
            "no_trade_calls": ai_call_stats["no_trade_calls"],
            "total_ai_calls": ai_call_stats["total_calls"],
            "long_wr": 0,
            "short_wr": 0,
            "long_pnl": 0,
            "short_pnl": 0,
            "by_regime_direction": [],
            "ai_recommendations": {"long": {}, "short": {}},
            "ai_call_stats": ai_call_stats,
        }
        try:
            with open(DIRECTION_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Could not write {DIRECTION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return report

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    if "final_direction" not in work.columns:
        work["final_direction"] = work.get("dir", "UNKNOWN")
    work["final_direction"] = work["final_direction"].astype(str).str.upper()
    if "regime" not in work.columns:
        work["regime"] = "UNKNOWN"
    work["regime"] = work["regime"].astype(str).str.upper()

    long_df = work[work["final_direction"] == "LONG"]
    short_df = work[work["final_direction"] == "SHORT"]
    long_stats = _direction_cohort_stats(long_df)
    short_stats = _direction_cohort_stats(short_df)

    ai_recs = {"long": {"approved": 0, "filled": int(len(long_df))}, "short": {"approved": 0, "filled": int(len(short_df))}}
    ai_call_stats = {"long_calls": 0, "short_calls": 0, "no_trade_calls": 0, "total_calls": 0}
    ai_reason_rows = _load_jsonl_rows(AI_REASON_RESEARCH_FILE)
    for row in ai_reason_rows:
        if row.get("schema") != "ai_reason_v2":
            continue
        ai_call_stats["total_calls"] += 1
        d = str(row.get("direction") or "").upper()
        if d == "LONG":
            ai_call_stats["long_calls"] += 1
        elif d == "SHORT":
            ai_call_stats["short_calls"] += 1
        else:
            ai_call_stats["no_trade_calls"] += 1

    print(
        f"  LONG: n={long_stats['trades']} WR={long_stats['win_rate_pct']:.1f}% "
        f"net=${long_stats['sum_pnl_usd']:.2f} avg_edge={long_stats['avg_edge']} "
        f"avg_ai={long_stats['avg_ai_confidence']} {PIPELINE_ENFORCEMENT_TAG}"
    )
    print(
        f"  SHORT: n={short_stats['trades']} WR={short_stats['win_rate_pct']:.1f}% "
        f"net=${short_stats['sum_pnl_usd']:.2f} avg_edge={short_stats['avg_edge']} "
        f"avg_ai={short_stats['avg_ai_confidence']} {PIPELINE_ENFORCEMENT_TAG}"
    )
    print(
        f"  AI calls: LONG={ai_call_stats['long_calls']} SHORT={ai_call_stats['short_calls']} "
        f"NO_TRADE={ai_call_stats['no_trade_calls']} total={ai_call_stats['total_calls']} "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )

    by_regime = []
    for regime in sorted(work["regime"].dropna().unique()):
        for direction in ("LONG", "SHORT"):
            sub = work[(work["regime"] == regime) & (work["final_direction"] == direction)]
            stats = _direction_cohort_stats(sub)
            if stats["trades"] > 0:
                row = {"regime": regime, "direction": direction, **stats}
                by_regime.append(row)
                print(
                    f"  {direction} in {regime}: n={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                    f"net=${stats['sum_pnl_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
                )

    if decisions is not None and not decisions.empty:
        dec = decisions.copy()
        if "final_direction" in dec.columns:
            dec["final_direction"] = dec["final_direction"].astype(str).str.upper()
            dec_col = "decision" if "decision" in dec.columns else ("ai_decision" if "ai_decision" in dec.columns else None)
            if dec_col:
                tiers = dec[dec_col].astype(str).str.upper()
                for direction in ("LONG", "SHORT"):
                    dsub = dec[dec["final_direction"] == direction]
                    ai_recs[direction.lower()]["approved"] = int(
                        tiers.loc[dsub.index].isin(["APPROVE", "STRONG_APPROVE", "SOFT_APPROVE"]).sum()
                    )

    report = {
        "schema": "direction_report_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "long": long_stats,
        "short": short_stats,
        "long_calls": ai_call_stats["long_calls"],
        "short_calls": ai_call_stats["short_calls"],
        "no_trade_calls": ai_call_stats["no_trade_calls"],
        "total_ai_calls": ai_call_stats["total_calls"],
        "long_wr": long_stats.get("win_rate_pct", 0),
        "short_wr": short_stats.get("win_rate_pct", 0),
        "long_pnl": long_stats.get("sum_pnl_usd", 0),
        "short_pnl": short_stats.get("sum_pnl_usd", 0),
        "by_regime_direction": by_regime,
        "ai_recommendations": ai_recs,
        "ai_call_stats": ai_call_stats,
    }
    try:
        with open(DIRECTION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"  ✅ Wrote {DIRECTION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {DIRECTION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return report


def confidence_band_report(trades=None, decisions=None, session=None):
    """
    Confidence band histogram with WR and PnL per bucket.
    Writes confidence_band_report.json (fills + AI approve cohort).
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CONFIDENCE_BAND_REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    fill_buckets = []
    if trades is not None and not trades.empty:
        work = trades.copy()
        if "trade_id" in work.columns:
            work = work.drop_duplicates(subset=["trade_id"], keep="last")
        conf = pd.to_numeric(work.get("ai_win_prob", work.get("conf")), errors="coerce")
        work["confidence_band"] = conf.apply(_confidence_band_label)
        for bucket in CONFIDENCE_BAND_BUCKET_ORDER:
            sub = work[work["confidence_band"] == bucket]
            stats = _direction_cohort_stats(sub)
            fill_buckets.append({"bucket": bucket, "source": "filled_trades", **stats})
            if stats["trades"]:
                print(
                    f"  fills {bucket}: n={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                    f"avg_pnl=${stats['avg_pnl_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
                )

    approve_buckets = []
    if decisions is not None and not decisions.empty:
        dec = decisions.copy()
        conf = pd.to_numeric(dec.get("ai_win_prob", dec.get("conf")), errors="coerce")
        dec["confidence_band"] = conf.apply(_confidence_band_label)
        for bucket in CONFIDENCE_BAND_BUCKET_ORDER:
            sub = dec[dec["confidence_band"] == bucket]
            approve_buckets.append({
                "bucket": bucket,
                "source": "ai_decisions",
                "count": int(len(sub)),
            })
            if len(sub):
                print(f"  AI decisions {bucket}: n={len(sub)} {PIPELINE_ENFORCEMENT_TAG}")

    report = {
        "schema": "confidence_band_report_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filled_trades_by_band": fill_buckets,
        "ai_decisions_by_band": approve_buckets,
    }
    expectancy_buckets = ["45-50", "50-55", "55-60", "60-65", "65+"]
    expectancy_rows = []
    for bucket in expectancy_buckets:
        match = next((b for b in fill_buckets if b.get("bucket") == bucket), None)
        if match:
            expectancy_rows.append({
                "bucket": bucket,
                "trades": match.get("trades", 0),
                "win_rate_pct": match.get("win_rate_pct", 0),
                "sum_pnl_usd": match.get("sum_pnl_usd", 0),
                "avg_pnl_usd": match.get("avg_pnl_usd", 0),
                "ev_usd": match.get("ev_usd", 0),
            })
        else:
            expectancy_rows.append({
                "bucket": bucket,
                "trades": 0,
                "win_rate_pct": 0,
                "sum_pnl_usd": 0,
                "avg_pnl_usd": 0,
                "ev_usd": 0,
            })
    expectancy_report = {
        "schema": "ai_confidence_expectancy_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Filled-trade expectancy by AI confidence band — discover profitable confidence zones.",
        "buckets": expectancy_rows,
    }
    try:
        with open(AI_CONFIDENCE_EXPECTANCY_FILE, "w", encoding="utf-8") as f:
            json.dump(expectancy_report, f, indent=2)
        print(f"  ✅ Wrote {AI_CONFIDENCE_EXPECTANCY_FILE} {PIPELINE_ENFORCEMENT_TAG}")
        for row in expectancy_rows:
            if row["trades"]:
                print(
                    f"  expectancy {row['bucket']}: n={row['trades']} WR={row['win_rate_pct']:.1f}% "
                    f"net=${row['sum_pnl_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
                )
    except Exception as e:
        print(f"  ⚠️ Could not write {AI_CONFIDENCE_EXPECTANCY_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    try:
        with open(CONFIDENCE_BAND_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"  ✅ Wrote {CONFIDENCE_BAND_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CONFIDENCE_BAND_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return report


def _build_confidence_edge_matrix(with_outcome):
    """2D matrix: AI buckets (50-55, 55-60) × edge buckets (2-3, 3-4, 4+) with WR and n."""
    empty_cell = {"trades": 0, "win_rate_pct": 0.0}
    matrix = {
        edge_b: {conf_b: dict(empty_cell) for conf_b in AI_MATRIX_CONF_BUCKETS}
        for edge_b in AI_MATRIX_EDGE_BUCKETS
    }
    cells = []
    if with_outcome is None or with_outcome.empty:
        return {
            "ai_buckets": AI_MATRIX_CONF_BUCKETS,
            "edge_buckets": AI_MATRIX_EDGE_BUCKETS,
            "matrix": matrix,
            "cells": cells,
        }
    work = with_outcome.copy()
    work["matrix_conf_bucket"] = pd.to_numeric(work["ai_confidence"], errors="coerce").apply(_ai_matrix_conf_bucket)
    work["matrix_edge_bucket"] = pd.to_numeric(work.get("edge_score"), errors="coerce").apply(_ai_matrix_edge_bucket)
    filtered = work[work["matrix_conf_bucket"].notna() & work["matrix_edge_bucket"].notna()]
    for (conf_b, edge_b), sub in filtered.groupby(["matrix_conf_bucket", "matrix_edge_bucket"], observed=True):
        stats = _ai_calib_cohort_stats(sub)
        cell = {
            "ai_bucket": conf_b,
            "edge_bucket": edge_b,
            "trades": stats["trades"],
            "win_rate_pct": stats["win_rate_pct"],
        }
        cells.append(cell)
        matrix[edge_b][conf_b] = {"trades": stats["trades"], "win_rate_pct": stats["win_rate_pct"]}
    return {
        "ai_buckets": AI_MATRIX_CONF_BUCKETS,
        "edge_buckets": AI_MATRIX_EDGE_BUCKETS,
        "matrix": matrix,
        "cells": cells,
    }


def _print_confidence_edge_matrix(matrix_payload):
    print("\n--- 8. AI Confidence × Edge Matrix ---")
    edge_label = 'edge \\\\ ai'
    header = f"{edge_label:<10}" + "".join(f"{cb:>16}" for cb in AI_MATRIX_CONF_BUCKETS)
    print(header)
    for edge_b in AI_MATRIX_EDGE_BUCKETS:
        row = f"{edge_b:<10}"
        for conf_b in AI_MATRIX_CONF_BUCKETS:
            cell = matrix_payload["matrix"][edge_b][conf_b]
            n = cell["trades"]
            if n:
                row += f" WR {cell['win_rate_pct']}% n={n}".rjust(16)
            else:
                row += f"{'—':>16}"
        print(row + f" {PIPELINE_ENFORCEMENT_TAG}")


def _build_ai_calibration_cohort(trades=None, session=None):
    """
    Merge APPROVE snapshots + ai_reason_research + trades + ai_confidence_calibration
    into one row per trade_id with confidence, features, and outcomes.
    """
    if session is None:
        session = load_research_session()
    snapshots_all = _load_signal_snapshots()
    snapshots = _filter_snapshots_by_session(snapshots_all, session)
    reasons_raw = _filter_jsonl_rows_by_session(load_ai_reason_research(), session)
    cal_raw = _filter_jsonl_rows_by_session(load_ai_confidence_calibration(), session)

    reason_by_id = {}
    for r in reasons_raw:
        if r.get("schema") not in ("ai_reason_v1", "ai_reason_v2"):
            continue
        tid = str(r.get("trade_id") or "")
        if tid:
            reason_by_id[tid] = r

    cal_by_id = {}
    for r in cal_raw:
        if r.get("schema") != "ai_calibration_v1":
            continue
        tid = str(r.get("trade_id") or "")
        if tid:
            cal_by_id[tid] = r

    trade_by_id = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        for _, row in trades.iterrows():
            tid = str(row.get("trade_id") or "")
            if tid:
                trade_by_id[tid] = row.to_dict()

    shadow_by_id = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    if session and _session_start_ts(session) is not None:
        shadow_df = _load_shadow_outcome_df(session)
        if shadow_df is not None and not shadow_df.empty:
            shadow_by_id = {
                str(r.get("trade_id")): r.to_dict()
                for _, r in shadow_df.iterrows()
                if r.get("trade_id")
            }

    rows = []
    approve_ids = set()
    for tid, snap in snapshots.items():
        ai = snap.get("ai") or {}
        if str(ai.get("decision") or "").upper() != "APPROVE":
            continue
        approve_ids.add(tid)

    if not approve_ids and reason_by_id:
        approve_ids = set(reason_by_id.keys())

    for tid in approve_ids:
        snap = snapshots.get(tid) or {}
        reason = reason_by_id.get(tid) or {}
        cal = cal_by_id.get(tid) or {}
        trade = trade_by_id.get(tid) or {}
        shadow = shadow_by_id.get(tid) or {}

        ai = snap.get("ai") or {}
        rb = snap.get("research_buckets") or {}
        ef = snap.get("entry_features") or {}
        er = snap.get("entry_regime") or {}
        ctx = snap.get("context") or {}
        mc = (ctx.get("market_context") or {}) if isinstance(ctx, dict) else {}

        ai_conf = (
            ai.get("win_prob")
            or reason.get("ai_prob")
            or cal.get("ai_prob")
            or trade.get("ai_win_prob")
            or rb.get("ai_win_prob")
        )
        try:
            ai_conf = float(ai_conf) if ai_conf is not None else None
        except (TypeError, ValueError):
            ai_conf = None

        net_pnl = trade.get("net_pnl_usd")
        if net_pnl is None or (isinstance(net_pnl, float) and np.isnan(net_pnl)):
            net_pnl = cal.get("net_pnl_usd")
        if net_pnl is None or (isinstance(net_pnl, float) and np.isnan(net_pnl)):
            net_pnl = shadow.get("net_pnl_usd")
        if net_pnl is None or (isinstance(net_pnl, float) and np.isnan(net_pnl)):
            net_pnl = reason.get("shadow_pnl")

        edge = snap.get("edge_score") or rb.get("edge_score") or trade.get("edge_score") or trade.get("edge_score_at_entry")
        try:
            edge = float(edge) if edge is not None else None
        except (TypeError, ValueError):
            edge = None

        structure_score = (
            rb.get("structure_score")
            or reason.get("structure_score")
            or (snap.get("market_structure") or {}).get("structure_score")
            or mc.get("market_structure", {}).get("structure_score")
        )
        vol_ratio = ef.get("volume_ratio")
        if vol_ratio is None and isinstance(snap.get("features"), dict):
            vol_ratio = snap["features"].get("volume_ratio")

        regime = (
            reason.get("market_regime")
            or er.get("regime_label")
            or mc.get("regime_label")
            or reason.get("regime")
            or snap.get("entry_regime", {}).get("regime_label")
        )
        sr_state = rb.get("sr_state") or reason.get("sr_state") or er.get("sr_state") or "UNKNOWN"
        session_bucket = rb.get("session_bucket") or er.get("session_utc") or "UNKNOWN"
        direction = str(
            reason.get("direction")
            or ai.get("direction")
            or trade.get("final_direction")
            or trade.get("dir")
            or ""
        ).upper()
        if direction not in ("LONG", "SHORT"):
            direction = None

        win = None
        if net_pnl is not None and not (isinstance(net_pnl, float) and np.isnan(net_pnl)):
            win = float(net_pnl) > 0
        elif cal.get("actual") is not None:
            win = str(cal.get("actual")).upper() == "WIN"
        elif reason.get("outcome") is not None:
            win = str(reason.get("outcome")).upper() == "WIN"

        rows.append({
            "trade_id": tid,
            "ai_confidence": ai_conf,
            "ai_confidence_bucket": _ai_calib_report_bucket(ai_conf) if ai_conf is not None else None,
            "edge_score": edge,
            "edge_score_bucket": _edge_score_bucket_val(edge) if edge is not None else None,
            "net_pnl_usd": net_pnl,
            "win": win,
            "executed": bool(trade) or _truthy(snap.get("executed")),
            "structure_bucket": _structure_attribution_bucket(structure_score),
            "participation_bucket": _participation_attribution_bucket(vol_ratio),
            "context_bucket": _context_attribution_bucket(sr_state, session_bucket),
            "regime_bucket": _regime_attribution_bucket(regime),
            "research_lane": snap.get("research_lane") or reason.get("research_lane") or cal.get("research_lane"),
            "direction": direction,
            "structure_score": structure_score,
            "volume_ratio": vol_ratio,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def ai_calibration_report(trades=None, session=None):
    """
    AI Calibration Report — confidence buckets, expected vs actual, feature attribution,
    decision fingerprints, histogram, confidence×edge cross-ref, override opportunities.
    Writes ai_calibration_report.json for dashboard section "AI Calibration".
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== AI_CALIBRATION_REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    cohort = _build_ai_calibration_cohort(trades, session)
    if cohort.empty:
        print(f"  No APPROVE cohort rows (need signal_snapshot + ai_reason_research). {PIPELINE_ENFORCEMENT_TAG}")
        report = {
            "schema": "ai_calibration_report_v1",
            "dashboard_section": "AI Calibration",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "analyzer_version": ANALYZER_VERSION,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sample_size": {"approve_rows": 0, "with_outcome": 0},
            "confidence_buckets": [],
            "calibration_bands": {},
            "expected_vs_actual": {},
            "usage_note": (
                "Analyzer-only — review every 48-72h. Do not auto-adjust bot AI thresholds from this file."
            ),
            "feature_attribution": {},
            "decision_fingerprints": [],
            "confidence_histogram": [],
            "confidence_vs_edge": [],
            "confidence_edge_matrix": _build_confidence_edge_matrix(pd.DataFrame()),
            "override_opportunities": {"under_scored_winners": [], "over_scored_losers": []},
        }
        try:
            with open(AI_CALIBRATION_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"  ✅ Wrote {AI_CALIBRATION_REPORT_FILE} (empty cohort) {PIPELINE_ENFORCEMENT_TAG}")
        except Exception as e:
            print(f"  ⚠️ Could not write {AI_CALIBRATION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return report

    with_outcome = cohort[cohort["net_pnl_usd"].notna() | cohort["win"].notna()].copy()
    with_conf = cohort[cohort["ai_confidence"].notna()].copy()
    print(
        f"  APPROVE cohort: {len(cohort)} | with AI confidence: {len(with_conf)} | "
        f"with outcome: {len(with_outcome)} {PIPELINE_ENFORCEMENT_TAG}"
    )

    # --- 1. Confidence Buckets ---
    print("\n--- 1. Confidence Buckets ---")
    bucket_rows = []
    for bucket in AI_CALIB_REPORT_BUCKET_ORDER:
        sub = with_outcome[with_outcome["ai_confidence_bucket"] == bucket]
        stats = _ai_calib_cohort_stats(sub)
        row = {"bucket": bucket, **stats}
        bucket_rows.append(row)
        if stats["trades"]:
            print(
                f"  {bucket}: trades={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                f"avg_pnl=${stats['avg_pnl_usd']:.2f} EV=${stats['ev_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )
        else:
            print(f"  {bucket}: (no trades) {PIPELINE_ENFORCEMENT_TAG}")

    # --- 2. Expected vs Actual calibration ---
    print("\n--- 2. Expected vs Actual Calibration ---")
    cal_detail = []
    abs_errors = []
    for bucket in AI_CALIB_REPORT_BUCKET_ORDER:
        sub = with_outcome[with_outcome["ai_confidence_bucket"] == bucket]
        if sub.empty:
            continue
        conf = pd.to_numeric(sub["ai_confidence"], errors="coerce").dropna()
        wins = sub["win"].fillna(sub["net_pnl_usd"].apply(lambda x: float(x) > 0 if pd.notna(x) else np.nan))
        wins = wins.dropna()
        if conf.empty or wins.empty:
            continue
        expected_wr = conf.mean() / 100.0
        actual_wr = wins.astype(bool).mean()
        err = abs(expected_wr - actual_wr)
        n = len(wins)
        abs_errors.extend([err] * n)
        cal_detail.append({
            "bucket": bucket,
            "trades": n,
            "expected_wr_pct": round(expected_wr * 100, 1),
            "actual_wr_pct": round(actual_wr * 100, 1),
            "calibration_error": round(err, 3),
        })
        print(
            f"  {bucket}: expected={expected_wr * 100:.1f}% actual={actual_wr * 100:.1f}% "
            f"error={err:.3f} n={n} {PIPELINE_ENFORCEMENT_TAG}"
        )

    overall_conf = pd.to_numeric(with_outcome["ai_confidence"], errors="coerce").dropna()
    overall_wins = with_outcome.loc[overall_conf.index, "win"].fillna(
        with_outcome.loc[overall_conf.index, "net_pnl_usd"].apply(lambda x: float(x) > 0 if pd.notna(x) else np.nan)
    ).dropna()
    mae = round(float(np.mean(abs_errors)), 3) if abs_errors else None
    brier = None
    if len(overall_conf) >= 3 and len(overall_wins) >= 3:
        aligned = overall_wins.astype(bool).astype(float)
        brier = round(float(((overall_conf / 100.0 - aligned) ** 2).mean()), 4)
    expected_vs_actual = {
        "per_bucket": cal_detail,
        "mean_absolute_calibration_error": mae,
        "brier_score": brier,
        "overall_expected_wr_pct": round(overall_conf.mean(), 1) if len(overall_conf) else None,
        "overall_actual_wr_pct": round(overall_wins.mean() * 100, 1) if len(overall_wins) else None,
        "overall_calibration_error": round(abs(overall_conf.mean() / 100 - overall_wins.mean()), 3)
        if len(overall_conf) and len(overall_wins) else None,
    }
    calibration_bands = {}
    for row in cal_detail:
        bucket = row["bucket"]
        key = bucket.replace("-", "_")
        calibration_bands[key] = {
            "expected": AI_CALIB_BAND_MIDPOINTS.get(bucket, row.get("expected_wr_pct")),
            "actual": round(float(row["actual_wr_pct"]), 1),
            "trades": int(row["trades"]),
            "calibration_error": round(float(row["calibration_error"]), 3),
        }
    for bucket in AI_CALIB_REPORT_BUCKET_ORDER:
        key = bucket.replace("-", "_")
        if key not in calibration_bands:
            calibration_bands[key] = {
                "expected": AI_CALIB_BAND_MIDPOINTS.get(bucket),
                "actual": None,
                "trades": 0,
                "calibration_error": None,
            }
    if mae is not None:
        print(f"  Mean absolute calibration error: {mae} | Brier: {brier} {PIPELINE_ENFORCEMENT_TAG}")

    # --- 3. AI Feature Attribution ---
    print("\n--- 3. AI Feature Attribution ---")
    feature_attribution = {}
    for dim, col in (
        ("structure", "structure_bucket"),
        ("participation", "participation_bucket"),
        ("context", "context_bucket"),
        ("regime", "regime_bucket"),
    ):
        dim_rows = []
        if col not in with_outcome.columns:
            feature_attribution[dim] = dim_rows
            continue
        for val, sub in with_outcome.groupby(col, observed=True):
            if str(val) in ("UNKNOWN", "nan", ""):
                continue
            stats = _ai_calib_cohort_stats(sub)
            if stats["trades"] == 0:
                continue
            avg_conf = round(float(pd.to_numeric(sub["ai_confidence"], errors="coerce").mean()), 1)
            dim_rows.append({"value": str(val), "avg_ai_confidence": avg_conf, **stats})
        dim_rows.sort(key=lambda x: x.get("ev_usd", 0), reverse=True)
        feature_attribution[dim] = dim_rows
        print(f"  {dim}:")
        for dr in dim_rows[:6]:
            print(
                f"    {dr['value']}: n={dr['trades']} WR={dr['win_rate_pct']:.1f}% "
                f"avg_pnl=${dr['avg_pnl_usd']:.2f} avg_conf={dr['avg_ai_confidence']} {PIPELINE_ENFORCEMENT_TAG}"
            )

    # --- 4. Decision Fingerprints ---
    print("\n--- 4. Decision Fingerprints ---")
    fp_cols = ["regime_bucket", "structure_bucket", "participation_bucket", "context_bucket"]
    fp_rows = []
    if all(c in with_outcome.columns for c in fp_cols):
        for keys, sub in with_outcome.groupby(fp_cols, observed=True):
            stats = _ai_calib_cohort_stats(sub)
            if stats["trades"] == 0:
                continue
            avg_conf = round(float(pd.to_numeric(sub["ai_confidence"], errors="coerce").mean()), 1)
            fp_rows.append({
                "regime": keys[0],
                "structure": keys[1],
                "participation": keys[2],
                "context": keys[3],
                "avg_ai_confidence": avg_conf,
                **stats,
            })
        fp_rows.sort(key=lambda x: x.get("ev_usd", 0), reverse=True)
        for fp in fp_rows[:8]:
            print(
                f"  {fp['regime']}|{fp['structure']}|{fp['participation']}|{fp['context']}: "
                f"n={fp['trades']} WR={fp['win_rate_pct']:.1f}% EV=${fp['ev_usd']:.2f} "
                f"conf={fp['avg_ai_confidence']} {PIPELINE_ENFORCEMENT_TAG}"
            )

    # --- 5. Confidence Distribution histogram ---
    print("\n--- 5. Confidence Distribution ---")
    hist_rows = []
    conf_ints = pd.to_numeric(with_conf["ai_confidence"], errors="coerce").dropna().astype(int)
    if not conf_ints.empty:
        vc = conf_ints.value_counts().sort_index()
        for conf_val, count in vc.items():
            if conf_val < 50:
                continue
            hist_rows.append({"confidence": int(conf_val), "count": int(count)})
        preview = hist_rows[:12]
        print(f"  histogram bins: {len(hist_rows)} (showing first {len(preview)}) {PIPELINE_ENFORCEMENT_TAG}")
        for h in preview:
            print(f"    conf={h['confidence']}: n={h['count']}")

    # --- 6. Confidence vs Edge cross-reference ---
    print("\n--- 6. Confidence vs Edge Cross-Reference ---")
    cross_rows = []
    cross_df = with_outcome[with_outcome["ai_confidence_bucket"].notna() & with_outcome["edge_score_bucket"].notna()]
    if not cross_df.empty:
        for (conf_b, edge_b), sub in cross_df.groupby(["ai_confidence_bucket", "edge_score_bucket"], observed=True):
            stats = _ai_calib_cohort_stats(sub)
            cross_rows.append({
                "confidence_bucket": conf_b,
                "edge_bucket": edge_b,
                **stats,
            })
        cross_rows.sort(key=lambda x: (
            AI_CALIB_REPORT_BUCKET_ORDER.index(x["confidence_bucket"])
            if x["confidence_bucket"] in AI_CALIB_REPORT_BUCKET_ORDER else 99,
            x.get("edge_bucket", ""),
        ))
        cross_tbl = pd.DataFrame(cross_rows)
        if not cross_tbl.empty:
            print(cross_tbl.to_string(index=False))

    # --- 7. Override Opportunities ---
    print("\n--- 7. AI Override Opportunities ---")
    under_scored = []
    over_scored = []
    min_fp_trades = 2
    for fp in fp_rows:
        if fp["trades"] < min_fp_trades:
            continue
        if fp["ev_usd"] >= 1.0 and fp.get("avg_ai_confidence", 100) < 58:
            under_scored.append({**fp, "signal": "under_scored_winner"})
        if fp["ev_usd"] <= -1.0 and fp.get("avg_ai_confidence", 0) > 62:
            over_scored.append({**fp, "signal": "over_scored_loser"})
    under_scored.sort(key=lambda x: x["ev_usd"], reverse=True)
    over_scored.sort(key=lambda x: x["ev_usd"])
    print(f"  Under-scored winners (high EV, low conf): {len(under_scored)} {PIPELINE_ENFORCEMENT_TAG}")
    for u in under_scored[:5]:
        print(
            f"    {u['regime']}|{u['structure']}|{u['participation']}|{u['context']}: "
            f"EV=${u['ev_usd']:.2f} conf={u['avg_ai_confidence']} n={u['trades']} {PIPELINE_ENFORCEMENT_TAG}"
        )
    print(f"  Over-scored losers (neg EV, high conf): {len(over_scored)} {PIPELINE_ENFORCEMENT_TAG}")
    for o in over_scored[:5]:
        print(
            f"    {o['regime']}|{o['structure']}|{o['participation']}|{o['context']}: "
            f"EV=${o['ev_usd']:.2f} conf={o['avg_ai_confidence']} n={o['trades']} {PIPELINE_ENFORCEMENT_TAG}"
        )

    confidence_edge_matrix = _build_confidence_edge_matrix(with_outcome)
    _print_confidence_edge_matrix(confidence_edge_matrix)

    report = {
        "schema": "ai_calibration_report_v1",
        "dashboard_section": "AI Calibration",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": {
            "approve_rows": len(cohort),
            "with_ai_confidence": len(with_conf),
            "with_outcome": len(with_outcome),
            "executed": int(cohort["executed"].sum()) if "executed" in cohort.columns else 0,
        },
        "confidence_buckets": bucket_rows,
        "calibration_bands": calibration_bands,
        "expected_vs_actual": expected_vs_actual,
        "confidence_distribution": cal_detail,
        "underconfidence_note": (
            "AI under-confident"
            if (
                expected_vs_actual.get("overall_actual_wr_pct") is not None
                and expected_vs_actual.get("overall_actual_wr_pct")
                > (expected_vs_actual.get("overall_expected_wr_pct") or 0) + 10
            )
            else None
        ),
        "usage_note": (
            "Analyzer-only — review every 48-72h. Do not auto-adjust bot AI thresholds from this file."
        ),
        "feature_attribution": feature_attribution,
        "decision_fingerprints": fp_rows,
        "confidence_histogram": hist_rows,
        "confidence_vs_edge": cross_rows,
        "confidence_edge_matrix": confidence_edge_matrix,
        "override_opportunities": {
            "under_scored_winners": under_scored,
            "over_scored_losers": over_scored,
        },
    }
    try:
        with open(AI_CALIBRATION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n  ✅ Wrote {AI_CALIBRATION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"\n  ⚠️ Could not write {AI_CALIBRATION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    print(PIPELINE_ENFORCEMENT_TAG)
    return report


def lane_opportunity_capture_report(trades=None, shadow_report=None):
    """Lane Opportunity Capture — APPROVE → FILL % and APPROVE NOT TRADED shadow PnL."""
    print(f"\n=== LANE OPPORTUNITY CAPTURE {PIPELINE_ENFORCEMENT_TAG} ===")
    lanes_out = {}
    rows = []
    if os.path.isfile(LANE_OPPORTUNITY_CAPTURE_FILE):
        try:
            with open(LANE_OPPORTUNITY_CAPTURE_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"  ⚠️ Could not read {LANE_OPPORTUNITY_CAPTURE_FILE}: {e}")

    shadow_by_lane = (shadow_report or {}).get("by_lane") or {}
    approve_not_traded_shadow = {}
    abr_path = "approved_but_rejected.jsonl"
    if os.path.isfile(abr_path):
        try:
            with open(abr_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("would_block_only"):
                        lane = str(o.get("research_lane") or "UNKNOWN").upper()
                        approve_not_traded_shadow[lane] = approve_not_traded_shadow.get(lane, 0) + 1
        except Exception:
            pass

    for lane_key in BENCHMARK_LANES:
        lane_rows = [r for r in rows if str(r.get("lane", "")).upper() == lane_key]
        approves = sum(1 for r in lane_rows if r.get("event") == "APPROVE")
        orders = sum(1 for r in lane_rows if r.get("event") == "ORDER_SUBMITTED")
        fills = sum(1 for r in lane_rows if r.get("event") == "FILLED")
        closes = sum(1 for r in lane_rows if r.get("event") == "CLOSED")
        not_traded = sum(1 for r in lane_rows if r.get("event") in ("APPROVE_NOT_TRADED", "WOULD_BLOCK", "EXECUTION_BLOCK"))
        approve_to_fill_pct = round(100.0 * fills / approves, 1) if approves else 0.0
        fill_to_profit_pct = None
        net_pnl = 0.0
        if trades is not None and not trades.empty and "research_lane" in trades.columns:
            lt = trades[trades["research_lane"].astype(str).str.upper() == lane_key]
            if not lt.empty and "outcome_net_pnl_usd" in lt.columns:
                net_pnl = float(lt["outcome_net_pnl_usd"].sum())
                wins = int((lt["outcome_net_pnl_usd"] > 0).sum())
                fill_to_profit_pct = round(100.0 * wins / len(lt), 1) if len(lt) else 0.0
        shadow_missed = float((shadow_by_lane.get(lane_key) or {}).get("sum_pnl_usd") or 0)
        lanes_out[lane_key] = {
            "lane": lane_key,
            "label": RESEARCH_LANE_LABELS.get(lane_key, lane_key),
            "approves": approves,
            "orders": orders,
            "fills": fills,
            "closes": closes,
            "approve_not_traded": not_traded,
            "approve_to_fill_pct": approve_to_fill_pct,
            "fill_to_profit_pct": fill_to_profit_pct,
            "net_pnl_usd": round(net_pnl, 2),
            "ev_per_approve_usd": round(net_pnl / approves, 2) if approves else 0.0,
            "shadow_would_block_logged": approve_not_traded_shadow.get(lane_key, 0),
            "shadow_profit_missed_usd": round(shadow_missed, 2),
        }
        print(
            f"  {lane_key}: approves={approves} fills={fills} ({approve_to_fill_pct}%) "
            f"missed={not_traded} net=${net_pnl:.2f} shadow_missed=${shadow_missed:.2f}"
        )

    payload = {
        "schema": "lane_opportunity_capture_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "bot_version": EXPECTED_BOT_VERSION,
        "lanes": lanes_out,
        "totals": {
            "approves": sum(v.get("approves", 0) for v in lanes_out.values()),
            "fills": sum(v.get("fills", 0) for v in lanes_out.values()),
            "approve_not_traded": sum(v.get("approve_not_traded", 0) for v in lanes_out.values()),
        },
    }
    try:
        with open(LANE_OPPORTUNITY_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {LANE_OPPORTUNITY_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {LANE_OPPORTUNITY_REPORT_FILE}: {e}")
    return payload


def ai_funnel_report(trades=None, session=None):
    """
    Per-lane AI approval funnel: ai_calls → approve → order_submitted → filled → closed.
    Writes ai_funnel_report.json — surfaces hidden blockers between AI APPROVE and execution.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== AI FUNNEL REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    opp_rows = _load_jsonl_rows(LANE_OPPORTUNITY_CAPTURE_FILE)
    ai_rows = [
        r for r in _load_jsonl_rows(AI_REASON_RESEARCH_FILE)
        if r.get("schema") == "ai_reason_v2"
    ]

    lanes_out = {}
    for lane_key in BENCHMARK_LANES:
        lane_ai = [r for r in ai_rows if str(r.get("research_lane") or "").upper() == lane_key]
        lane_opp = [r for r in opp_rows if str(r.get("lane") or "").upper() == lane_key]
        ai_calls = len(lane_ai)
        ai_approve_decisions = sum(
            1 for r in lane_ai
            if str(r.get("ai_decision") or "").upper() in ("APPROVE", "STRONG_APPROVE", "SOFT_APPROVE")
        )
        approve = sum(1 for r in lane_opp if r.get("event") == "APPROVE")
        order_submitted = sum(1 for r in lane_opp if r.get("event") == "ORDER_SUBMITTED")
        filled = sum(1 for r in lane_opp if r.get("event") == "FILLED")
        closed = sum(1 for r in lane_opp if r.get("event") == "CLOSED")
        would_block = sum(1 for r in lane_opp if r.get("event") in ("WOULD_BLOCK", "EXECUTION_BLOCK", "APPROVE_NOT_TRADED"))
        net_pnl = 0.0
        if trades is not None and not trades.empty and "research_lane" in trades.columns:
            lt = trades[trades["research_lane"].astype(str).str.upper() == lane_key]
            if not lt.empty and "outcome_net_pnl_usd" in lt.columns:
                net_pnl = float(lt["outcome_net_pnl_usd"].sum())
        funnel = {
            "lane": lane_key,
            "label": RESEARCH_LANE_LABELS.get(lane_key, lane_key),
            "ai_calls": ai_calls,
            "ai_approve_decisions": ai_approve_decisions,
            "approve": approve,
            "order_submitted": order_submitted,
            "filled": filled,
            "closed": closed,
            "would_block_or_not_traded": would_block,
            "approve_to_order_gap": max(0, approve - order_submitted),
            "order_to_fill_gap": max(0, order_submitted - filled),
            "approve_to_fill_pct": round(100.0 * filled / approve, 1) if approve else 0.0,
            "net_pnl_usd": round(net_pnl, 2),
        }
        lanes_out[lane_key] = funnel
        print(
            f"  {lane_key}: ai_calls={ai_calls} approve={approve} orders={order_submitted} "
            f"fills={filled} closed={closed} blocked={would_block} "
            f"gap(A→O)={funnel['approve_to_order_gap']} gap(O→F)={funnel['order_to_fill_gap']} "
            f"{PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "ai_funnel_report_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lanes": lanes_out,
        "totals": {
            "ai_calls": sum(v.get("ai_calls", 0) for v in lanes_out.values()),
            "approve": sum(v.get("approve", 0) for v in lanes_out.values()),
            "order_submitted": sum(v.get("order_submitted", 0) for v in lanes_out.values()),
            "filled": sum(v.get("filled", 0) for v in lanes_out.values()),
            "closed": sum(v.get("closed", 0) for v in lanes_out.values()),
            "would_block_or_not_traded": sum(v.get("would_block_or_not_traded", 0) for v in lanes_out.values()),
        },
    }
    try:
        with open(AI_FUNNEL_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {AI_FUNNEL_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {AI_FUNNEL_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def ai_decision_fingerprint_report(trades=None, session=None):
    """
    Cluster AI calls by bull/bear/structure/MTF fingerprint with WR and PnL.
    Answers: why does AI keep producing 58-62% longs?
    Writes ai_decision_fingerprint_report.json.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== AI DECISION FINGERPRINT REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    rows = _build_ai_reason_outcome_rows(session=session, trades=trades)
    if not rows:
        payload = {
            "schema": "ai_decision_fingerprint_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "clusters": [],
            "top_losing_fingerprints": [],
            "top_winning_fingerprints": [],
        }
        try:
            with open(AI_DECISION_FINGERPRINT_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            print(f"  ✅ Wrote {AI_DECISION_FINGERPRINT_REPORT_FILE} (empty) {PIPELINE_ENFORCEMENT_TAG}")
        except Exception as e:
            print(f"  ⚠️ Could not write {AI_DECISION_FINGERPRINT_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return payload

    df = pd.DataFrame(rows)
    df["fp_key"] = df["fingerprint"].apply(_fingerprint_key)
    clusters = []
    for fp_key, sub in df.groupby("fp_key", observed=True):
        fp = sub.iloc[0]["fingerprint"]
        with_outcome = sub[sub["net_pnl_usd"].notna()]
        stats = _ai_calib_cohort_stats(with_outcome)
        long_n = int((sub["direction"] == "LONG").sum())
        short_n = int((sub["direction"] == "SHORT").sum())
        approve_n = int(sub["ai_decision"].isin(["APPROVE", "STRONG_APPROVE", "SOFT_APPROVE"]).sum())
        avg_conf = round(float(pd.to_numeric(sub["ai_prob"], errors="coerce").mean()), 1) if sub["ai_prob"].notna().any() else 0.0
        cluster = {
            "fingerprint": fp,
            "ai_calls": int(len(sub)),
            "approve_calls": approve_n,
            "long_calls": long_n,
            "short_calls": short_n,
            "long_pct": round(100.0 * long_n / len(sub), 1) if len(sub) else 0.0,
            "avg_ai_confidence": avg_conf,
            "with_outcome": stats["trades"],
            "win_rate_pct": stats["win_rate_pct"],
            "sum_pnl_usd": stats["sum_pnl_usd"],
            "ev_usd": stats["ev_usd"],
        }
        clusters.append(cluster)
        if stats["trades"]:
            print(
                f"  {fp}: n={len(sub)} outcomes={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                f"pnl=${stats['sum_pnl_usd']:.2f} long={long_n} short={short_n} conf={avg_conf} "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )

    clusters.sort(key=lambda x: x.get("ai_calls", 0), reverse=True)
    with_outcome_clusters = [c for c in clusters if c.get("with_outcome", 0) > 0]
    top_losing = sorted(with_outcome_clusters, key=lambda x: x.get("sum_pnl_usd", 0))[:8]
    top_winning = sorted(with_outcome_clusters, key=lambda x: x.get("sum_pnl_usd", 0), reverse=True)[:8]

    payload = {
        "schema": "ai_decision_fingerprint_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_ai_calls": int(len(df)),
        "clusters": clusters,
        "top_losing_fingerprints": top_losing,
        "top_winning_fingerprints": top_winning,
    }
    try:
        with open(AI_DECISION_FINGERPRINT_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {AI_DECISION_FINGERPRINT_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {AI_DECISION_FINGERPRINT_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def approve_outcome_confidence_direction_report(trades=None, session=None):
    """
    Confidence × direction matrix — e.g. Long 60-65 vs Short 60-65 WR/PnL.
    Writes approve_outcome_confidence_direction.json.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== APPROVE OUTCOME CONF×DIRECTION — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    cohort = _build_ai_calibration_cohort(trades, session)
    matrix = {}
    if cohort.empty:
        for bucket in APPROVE_CONF_DIRECTION_BUCKETS:
            matrix[bucket] = {
                "long": _ai_calib_cohort_stats(pd.DataFrame()),
                "short": _ai_calib_cohort_stats(pd.DataFrame()),
            }
    else:
        with_outcome = cohort[cohort["net_pnl_usd"].notna() | cohort["win"].notna()].copy()
        for bucket in APPROVE_CONF_DIRECTION_BUCKETS:
            bucket_rows = with_outcome[with_outcome["ai_confidence_bucket"] == bucket]
            long_stats = _ai_calib_cohort_stats(bucket_rows[bucket_rows["direction"] == "LONG"])
            short_stats = _ai_calib_cohort_stats(bucket_rows[bucket_rows["direction"] == "SHORT"])
            matrix[bucket] = {"long": long_stats, "short": short_stats}
            if long_stats["trades"] or short_stats["trades"]:
                print(
                    f"  {bucket}: LONG n={long_stats['trades']} WR={long_stats['win_rate_pct']:.1f}% "
                    f"${long_stats['sum_pnl_usd']:.2f} | SHORT n={short_stats['trades']} "
                    f"WR={short_stats['win_rate_pct']:.1f}% ${short_stats['sum_pnl_usd']:.2f} "
                    f"{PIPELINE_ENFORCEMENT_TAG}"
                )

    payload = {
        "schema": "approve_outcome_confidence_direction_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": APPROVE_CONF_DIRECTION_BUCKETS,
        "matrix": matrix,
    }
    try:
        with open(APPROVE_OUTCOME_CONF_DIRECTION_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {APPROVE_OUTCOME_CONF_DIRECTION_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {APPROVE_OUTCOME_CONF_DIRECTION_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def benchmark_relative_scorecard_report(
    trades=None,
    session=None,
    benchmark_report=None,
    blocked=None,
    shadow_report=None,
):
    """
    First-class lane vs CONTINUOUS scorecard — did the experiment beat the benchmark?
    Writes benchmark_relative_scorecard.json.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== BENCHMARK RELATIVE SCORECARD — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    print(f"Control group: {BENCHMARK_LANE} (Scenario C / 180s cadence frozen) {PIPELINE_ENFORCEMENT_TAG}")

    if benchmark_report is None:
        benchmark_report = benchmark_vs_lanes_report(
            trades=trades,
            session=session,
            blocked=blocked,
            shadow_report=shadow_report,
        )
    if not benchmark_report:
        payload = {
            "schema": "benchmark_relative_scorecard_v1",
            "benchmark_lane": BENCHMARK_LANE,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lanes": [],
        }
        try:
            with open(BENCHMARK_RELATIVE_SCORECARD_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return payload

    lane_metrics = benchmark_report.get("lanes") or {}
    bench_m = lane_metrics.get(BENCHMARK_LANE) or {}
    bench_trade = _lane_trade_stats(trades, BENCHMARK_LANE)
    bench_wr = bench_trade["win_rate_pct"]
    bench_pnl = float(bench_m.get("net_pnl_real") or bench_trade["sum_pnl_usd"] or 0)
    bench_ev = float(bench_m.get("per_approve_ev") or 0)

    lanes_out = []
    for lane in BENCHMARK_LANES:
        m = lane_metrics.get(lane) or {}
        lane_trade = _lane_trade_stats(trades, lane)
        wr = lane_trade["win_rate_pct"]
        pnl = float(m.get("net_pnl_real") or lane_trade["sum_pnl_usd"] or 0)
        ev = float(m.get("per_approve_ev") or 0)
        entry = {
            "lane": lane,
            "label": RESEARCH_LANE_LABELS.get(lane, lane),
            "is_benchmark": lane == BENCHMARK_LANE,
            "approves": m.get("approves", 0),
            "fills": m.get("real_fills", lane_trade["trades"]),
            "win_rate_pct": wr,
            "pnl_usd": round(pnl, 2),
            "ev_per_approve_usd": round(ev, 2),
        }
        if lane == BENCHMARK_LANE:
            entry["vs_benchmark"] = {
                "pnl_delta": 0.0,
                "wr_delta": 0.0,
                "ev_delta": 0.0,
                "fill_pct_delta": 0.0,
                "beats_benchmark": None,
            }
        else:
            vs = {
                "pnl_delta": round(pnl - bench_pnl, 2),
                "wr_delta": round(wr - bench_wr, 1),
                "ev_delta": round(ev - bench_ev, 2),
                "fill_pct_delta": m.get("delta_approve_to_fill_pct"),
                "beats_benchmark": bool(pnl > bench_pnl or ev > bench_ev),
            }
            entry["vs_benchmark"] = vs
            if m.get("approves", 0):
                print(
                    f"  {lane}: PnL ${pnl:.2f} (Δ ${vs['pnl_delta']:+.2f}) "
                    f"WR {wr:.1f}% (Δ {vs['wr_delta']:+.1f}pp) "
                    f"EV ${ev:.2f}/appr (Δ {vs['ev_delta']:+.2f}) "
                    f"{'BEATS' if vs['beats_benchmark'] else 'TRAILS'} benchmark {PIPELINE_ENFORCEMENT_TAG}"
                )
        lanes_out.append(entry)

    payload = {
        "schema": "benchmark_relative_scorecard_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "benchmark_lane": BENCHMARK_LANE,
        "benchmark_frozen": {
            "scenario_c_exits": True,
            "ai_cadence_sec": 180,
            "note": "CONTINUOUS is the scientific control — do not mutate during research collection.",
        },
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_summary": {
            "pnl_usd": round(bench_pnl, 2),
            "win_rate_pct": bench_wr,
            "ev_per_approve_usd": round(bench_ev, 2),
            "approves": bench_m.get("approves", 0),
        },
        "lanes": lanes_out,
    }
    try:
        with open(BENCHMARK_RELATIVE_SCORECARD_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {BENCHMARK_RELATIVE_SCORECARD_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {BENCHMARK_RELATIVE_SCORECARD_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def missed_opportunity_heatmap_report(trades=None, session=None):
    """
    APPROVE-not-traded / WOULD_BLOCK reasons × missed shadow profit.
    Separates AI problems from execution problems.
    Writes missed_opportunity_heatmap.json.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== MISSED OPPORTUNITY HEATMAP — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    shadow_by_id = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    if session and _session_start_ts(session) is not None:
        shadow_df = _load_shadow_outcome_df(session)
        if shadow_df is not None and not shadow_df.empty:
            shadow_by_id = {
                str(r.get("trade_id")): r.to_dict()
                for _, r in shadow_df.iterrows()
                if r.get("trade_id")
            }

    heat = {}
    seen_trade_reason = set()

    def _accumulate(reason_bucket, pnl, event_type, raw_reason=None):
        rec = heat.setdefault(reason_bucket, {
            "reason": reason_bucket,
            "count": 0,
            "missed_profit_usd": 0.0,
            "shadow_pnl_total_usd": 0.0,
            "missed_winner_usd": 0.0,
            "saved_loss_usd": 0.0,
            "sample_raw_reasons": [],
        })
        rec["count"] += 1
        if pnl is not None:
            pnl = float(pnl)
            rec["shadow_pnl_total_usd"] = round(rec["shadow_pnl_total_usd"] + pnl, 2)
            if pnl > 0:
                rec["missed_profit_usd"] = round(rec["missed_profit_usd"] + pnl, 2)
                rec["missed_winner_usd"] = round(rec["missed_winner_usd"] + pnl, 2)
            else:
                rec["saved_loss_usd"] = round(rec["saved_loss_usd"] + abs(pnl), 2)
        if raw_reason and len(rec["sample_raw_reasons"]) < 5 and raw_reason not in rec["sample_raw_reasons"]:
            rec["sample_raw_reasons"].append(raw_reason)

    opp_rows = _filter_jsonl_rows_by_session(_load_jsonl_rows(LANE_OPPORTUNITY_CAPTURE_FILE), session)
    for row in opp_rows:
        event = str(row.get("event") or "").upper()
        if event not in ("APPROVE_NOT_TRADED", "WOULD_BLOCK", "EXECUTION_BLOCK"):
            continue
        raw = row.get("block_reason") or event
        bucket = _classify_missed_opportunity_reason(raw)
        tid = str(row.get("trade_id") or "")
        dedupe_key = (tid, bucket, event)
        if dedupe_key in seen_trade_reason:
            continue
        seen_trade_reason.add(dedupe_key)
        pnl = row.get("shadow_pnl_usd")
        if pnl is None and tid in shadow_by_id:
            pnl = shadow_by_id[tid].get("net_pnl_usd")
        _accumulate(bucket, pnl, event, str(raw))

    abr_rows = _filter_jsonl_rows_by_session(_load_jsonl_rows(APPROVED_BUT_REJECTED_FILE), session)
    for row in abr_rows:
        if row.get("schema") != "approved_but_rejected_v1":
            continue
        raw = row.get("block_reason") or "UNKNOWN"
        bucket = _classify_missed_opportunity_reason(raw)
        tid = str(row.get("trade_id") or row.get("signal_id") or "")
        dedupe_key = (tid, bucket, "abr")
        if dedupe_key in seen_trade_reason:
            continue
        seen_trade_reason.add(dedupe_key)
        pnl = shadow_by_id.get(tid, {}).get("net_pnl_usd") if tid else None
        _accumulate(bucket, pnl, "approved_but_rejected", str(raw))

    rows_out = sorted(heat.values(), key=lambda x: x.get("missed_profit_usd", 0), reverse=True)
    for rec in rows_out:
        print(
            f"  {rec['reason']}: count={rec['count']} missed_profit=${rec['missed_profit_usd']:.2f} "
            f"shadow_total=${rec['shadow_pnl_total_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "missed_opportunity_heatmap_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "missed_profit_usd = sum of positive shadow PnL on blocked APPROVEs (money left on table).",
        "heatmap": rows_out,
        "totals": {
            "events": sum(r.get("count", 0) for r in rows_out),
            "missed_profit_usd": round(sum(r.get("missed_profit_usd", 0) for r in rows_out), 2),
            "shadow_pnl_total_usd": round(sum(r.get("shadow_pnl_total_usd", 0) for r in rows_out), 2),
        },
    }
    try:
        with open(MISSED_OPPORTUNITY_HEATMAP_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {MISSED_OPPORTUNITY_HEATMAP_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {MISSED_OPPORTUNITY_HEATMAP_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _funnel_row_epoch(row: dict):
    ts = row.get("ts")
    if ts is None:
        return None
    try:
        return float(pd.Timestamp(ts).timestamp())
    except Exception:
        return None



def _normalize_lane_label(lane) -> str:
    """Fix pandas NaN / empty funnel lane values."""
    if lane is None:
        return "UNKNOWN"
    s = str(lane).strip().upper()
    if not s or s in {"NAN", "NONE", "NULL", "UNKNOWN", "<NA>"}:
        return "UNKNOWN"
    return s


def _resolve_chase_count(tid: str, funnel_count, trade_chase: dict) -> tuple[int, str]:
    """Prefer trades_3factor.limit_chase_count when funnel under-reports chases."""
    csv_count = trade_chase.get(tid)
    try:
        funnel_n = int(funnel_count or 0)
    except (TypeError, ValueError):
        funnel_n = 0
    if csv_count is not None:
        try:
            csv_n = int(csv_count or 0)
        except (TypeError, ValueError):
            csv_n = 0
        if csv_n > funnel_n:
            return csv_n, "trades_3factor.limit_chase_count"
        return max(funnel_n, csv_n), (
            "execution_funnel.limit_chase_count" if funnel_n else "trades_3factor.limit_chase_count"
        )
    return funnel_n, "execution_funnel.limit_chase_count"


def _ai_scan_coordinator_stats(decisions=None, ai_log=None) -> dict:
    """AI_SCAN funnel — approvals/rejects/skipped/timeout from decisions + ai log."""
    out = {"approvals": 0, "rejects": 0, "skipped": 0, "timeouts": 0, "total": 0}
    if decisions is not None and not decisions.empty:
        work = decisions.copy()
        if "ai_decision_text" in work.columns:
            txt = work["ai_decision_text"].fillna("").astype(str).str.upper()
            out["approvals"] = int((txt == "APPROVE").sum())
            out["rejects"] = int((txt == "REJECT").sum())
            out["timeouts"] = int(txt.str.contains("ERROR|TIMEOUT", regex=True).sum())
        if "skip_stage" in work.columns:
            out["skipped"] = int(work["skip_stage"].fillna("").astype(str).str.upper().eq("COOLDOWN").sum())
        elif "reason" in work.columns:
            out["skipped"] = int(work["reason"].fillna("").astype(str).str.contains("AI_COOLDOWN", regex=False).sum())
        out["total"] = int(len(work))
    funnel = out["approvals"] + out["rejects"] + out["skipped"] + out["timeouts"]
    out["funnel_sum"] = funnel
    return out



def run_integrity_checks(
    trades=None,
    decisions=None,
    session=None,
    chase_payload=None,
    benchmark_report=None,
):
    """
    Validate → reconcile → display. Reports are INVALID when checks fail.
    Writes analyzer_integrity_report.json (dashboard reads before render).
    """
    checks = []
    valid = True

    def _add(name, passed, expected, found, detail=""):
        nonlocal valid
        if not passed:
            valid = False
        checks.append({
            "check": name,
            "passed": passed,
            "expected": expected,
            "found": found,
            "detail": detail,
        })

    # Trade W/L reconciliation
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        pnl = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0)
        wins = int((pnl > 0).sum())
        losses = int((pnl < 0).sum())
        breakeven = int((pnl == 0).sum())
        total = int(len(work))
        _add(
            "trades_wins_losses",
            total == wins + losses + breakeven,
            f"wins+losses+be={wins}+{losses}+{breakeven}={wins + losses + breakeven}",
            f"total_trades={total}",
        )

    # AI funnel: approvals + rejects + skipped + timeout reconcile on AI-involved rows
    if decisions is not None and not decisions.empty:
        d = decisions.copy()
        ai_txt = d["ai_decision_text"].fillna("").astype(str).str.upper() if "ai_decision_text" in d.columns else pd.Series([""] * len(d))
        dec = d["decision"].fillna("").astype(str).str.upper() if "decision" in d.columns else pd.Series([""] * len(d))
        skip_st = d["skip_stage"].fillna("").astype(str).str.upper() if "skip_stage" in d.columns else pd.Series([""] * len(d))
        ai_involved = d[dec.isin(["AI", "BLOCKED"]) | ai_txt.isin(["APPROVE", "REJECT"]) | skip_st.eq("COOLDOWN")]
        sub_txt = ai_involved["ai_decision_text"].fillna("").astype(str).str.upper()
        appr = int((sub_txt == "APPROVE").sum())
        rej = int((sub_txt == "REJECT").sum())
        timeout = int(sub_txt.str.contains("ERROR|TIMEOUT", regex=True).sum())
        skipped = int(ai_involved["skip_stage"].fillna("").astype(str).str.upper().eq("COOLDOWN").sum()) if "skip_stage" in ai_involved.columns else 0
        funnel = appr + rej + skipped + timeout
        n_ai = int(len(ai_involved))
        _add(
            "ai_decision_funnel",
            abs(funnel - n_ai) <= max(10, int(0.05 * n_ai)),
            f"approvals+rejects+skipped+timeout={appr}+{rej}+{skipped}+{timeout}={funnel}",
            f"ai_involved_rows={n_ai} (total decisions={len(d)})",
            "decisions_3factor: AI/BLOCKED + ai_decision_text APPROVE/REJECT + COOLDOWN skip_stage",
        )

    # Chase buckets: CSV limit_chase_count vs chase_effectiveness report
    csv_buckets = {}
    if trades is not None and not trades.empty and "limit_chase_count" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        cc = pd.to_numeric(work["limit_chase_count"], errors="coerce").fillna(0).astype(int)
        for n in cc:
            key = _chase_count_bucket(n)
            csv_buckets[key] = csv_buckets.get(key, 0) + 1
        eff = {}
        if chase_payload:
            attr = (chase_payload.get("trades") or [])
            for row in attr:
                key = _chase_count_bucket(row.get("chase_count"))
                if row.get("net_pnl_usd") is not None or row.get("win") is not None:
                    eff[key] = eff.get(key, 0) + 1
        else:
            eff_path = analyzer_report_path("chase_effectiveness_report.json")
            if os.path.isfile(eff_path):
                try:
                    with open(eff_path, encoding="utf-8") as f:
                        rep = json.load(f)
                    for k, b in (rep.get("buckets") or {}).items():
                        eff[k] = int((b or {}).get("trades") or 0)
                except Exception:
                    eff = {}
        mismatch = []
        for k in set(list(csv_buckets.keys()) + list(eff.keys())):
            if csv_buckets.get(k, 0) != eff.get(k, 0):
                mismatch.append(f"{k}: csv={csv_buckets.get(k, 0)} report={eff.get(k, 0)}")
        _add(
            "chase_count_buckets",
            not mismatch,
            str(csv_buckets),
            str(eff),
            "; ".join(mismatch[:6]) if mismatch else "trades_3factor.limit_chase_count matches report buckets",
        )

    # Lane totals vs CONTINUOUS
    if benchmark_report and trades is not None and not trades.empty and "research_lane" in trades.columns:
        lanes = (benchmark_report or {}).get("lanes") or {}
        cont = int((lanes.get("CONTINUOUS") or {}).get("real_fills") or (lanes.get("CONTINUOUS") or {}).get("fills") or 0)
        lane_sum = 0
        for ln, m in lanes.items():
            if ln in ("AI_SCAN", "EXEC_5M"):
                continue
            lane_sum += int(m.get("real_fills") or m.get("fills") or 0)
        work = trades.drop_duplicates(subset=["trade_id"])
        csv_total = int(len(work))
        _add(
            "lane_fill_reconcile",
            lane_sum >= cont and csv_total >= cont,
            f"lane_fills_sum≥CONTINUOUS({cont}), csv_trades={csv_total}",
            f"lane_fills_sum={lane_sum}",
        )

    # Genome vs completed trades (best-effort)
    genome_path = _agent_data_path(os.path.join("research", "genome", "genome_events.jsonl"))
    if not os.path.isfile(genome_path):
        genome_path = _agent_data_path("genome_events.jsonl")
    trade_ids = set()
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        trade_ids = set(trades["trade_id"].dropna().astype(str))
    genome_trade_events = 0
    if os.path.isfile(genome_path) and trade_ids:
        try:
            for row in _load_jsonl_rows(genome_path):
                if row.get("event_name") in ("TRADE_COMPLETE", "TRADE_CLOSED") and str(row.get("trade_id") or "") in trade_ids:
                    genome_trade_events += 1
        except Exception:
            pass
        if genome_trade_events:
            _add(
                "genome_vs_trades",
                genome_trade_events <= len(trade_ids) * 1.5,
                f"genome_trade_events≤{len(trade_ids) * 1.5}",
                f"genome_events={genome_trade_events}, csv_trades={len(trade_ids)}",
            )

    # signal_id linkage spot check
    if trades is not None and not trades.empty and decisions is not None and not decisions.empty:
        if "signal_id" in trades.columns and "signal_id" in decisions.columns:
            sample = trades.drop_duplicates(subset=["trade_id"]).head(20)
            linked = 0
            dec_ids = set(decisions["signal_id"].dropna().astype(str))
            for _, row in sample.iterrows():
                sid = str(row.get("signal_id") or "")
                tid = str(row.get("trade_id") or "")
                if sid and sid in dec_ids:
                    linked += 1
                elif tid and tid in set(decisions.get("trade_id", pd.Series()).dropna().astype(str)):
                    linked += 1
            _add(
                "signal_id_linkage_spot",
                linked >= min(5, len(sample) // 2),
                f"≥{min(5, len(sample) // 2)} of {len(sample)} sample trades linked",
                f"linked={linked}",
            )

    scope = _shadow_scope_label(session) if session else "SESSION"
    payload = {
        "schema": "analyzer_integrity_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_scope": scope,
        "valid": valid,
        "report_status": "VALID" if valid else "INVALID",
        "banner": None if valid else "⚠ REPORT INVALID — reconcile before trusting chase/exit tables",
        "checks": checks,
        "failed_checks": [c for c in checks if not c.get("passed")],
    }
    try:
        with open(analyzer_report_path(ANALYZER_INTEGRITY_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        status = payload["report_status"]
        print(f"  Integrity: {status} ({len(checks)} checks, {len(payload['failed_checks'])} failed) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as exc:
        print(f"  ⚠️ Could not write {ANALYZER_INTEGRITY_REPORT_FILE}: {exc} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def chase_attribution_report(trades=None, session=None):
    """
    Per-trade chase attribution from execution_funnel.jsonl.
    Answers: how many fills happened only because chase moved the limit?
    Writes chase_attribution_report.json
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CHASE ATTRIBUTION REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    rows = _filter_jsonl_rows_by_session(_load_jsonl_rows(EXECUTION_FUNNEL_FILE), session)
    trade_pnl = {}
    trade_wr = {}
    trade_chase = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        work = trades.copy()
        if "trade_id" in work.columns:
            work = work.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        if pnl_col in work.columns:
            work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce")
            for _, t in work.iterrows():
                tid = str(t.get("trade_id") or "")
                if not tid:
                    continue
                pnl = t.get(pnl_col)
                if pd.notna(pnl):
                    trade_pnl[tid] = float(pnl)
                    trade_wr[tid] = float(pnl) > 0
        trade_lane = {}
        trade_hold = {}
        if "limit_chase_count" in work.columns:
            for _, t in work.iterrows():
                tid = str(t.get("trade_id") or "")
                if tid:
                    trade_chase[tid] = int(pd.to_numeric(t.get("limit_chase_count"), errors="coerce") or 0)
                    if "research_lane" in work.columns:
                        trade_lane[tid] = _normalize_lane_label(t.get("research_lane"))
                    hold = t.get("dur_min") if "dur_min" in work.columns else t.get("duration_min")
                    if hold is not None and pd.notna(hold):
                        trade_hold[tid] = round(float(hold), 2)

    by_tid = {}
    for row in rows:
        tid = str(row.get("trade_id") or "")
        if tid:
            by_tid.setdefault(tid, []).append(row)

    attributions = []
    chase_events_total = 0
    for tid, events in by_tid.items():
        events = sorted(events, key=lambda r: _funnel_row_epoch(r) or 0)
        order_row = next((e for e in events if e.get("stage") == "ORDER_SUBMITTED"), None)
        chase_rows = [e for e in events if e.get("stage") == "LIMIT_CHASE"]
        fill_row = next((e for e in events if e.get("stage") == "FILLED"), None)
        expire_row = next((e for e in events if e.get("stage") == "ORDER_EXPIRED"), None)
        if not order_row and not chase_rows and not fill_row and not expire_row:
            continue

        chase_events_total += len(chase_rows)
        order_epoch = _funnel_row_epoch(order_row) if order_row else None
        first_chase_sec = None
        last_chase_sec = None
        if order_epoch and chase_rows:
            first_ts = _funnel_row_epoch(chase_rows[0])
            last_ts = _funnel_row_epoch(chase_rows[-1])
            if first_ts is not None:
                first_chase_sec = round(first_ts - order_epoch, 1)
            if last_ts is not None:
                last_chase_sec = round(last_ts - order_epoch, 1)

        funnel_cc = 0
        if fill_row and fill_row.get("limit_chase_count") is not None:
            funnel_cc = int(fill_row.get("limit_chase_count") or 0)
        elif chase_rows:
            funnel_cc = int(chase_rows[-1].get("limit_chase_count") or len(chase_rows))
        elif expire_row and expire_row.get("limit_chase_count") is not None:
            funnel_cc = int(expire_row.get("limit_chase_count") or 0)
        chase_count, chase_count_source = _resolve_chase_count(tid, funnel_cc, trade_chase)

        lane = _normalize_lane_label(
            (order_row or {}).get("research_lane")
            or (fill_row or {}).get("research_lane")
            or (chase_rows[0].get("research_lane") if chase_rows else None)
            or (expire_row or {}).get("research_lane")
            or trade_lane.get(tid)
            or "UNKNOWN"
        )
        original_limit = (
            (order_row or {}).get("original_limit_price")
            or (fill_row or {}).get("original_limit_price")
            or (chase_rows[0].get("original_limit_price") if chase_rows else None)
        )
        try:
            original_limit = float(original_limit) if original_limit is not None else None
        except (TypeError, ValueError):
            original_limit = None

        filled = fill_row is not None
        fill_price = None
        if fill_row:
            try:
                fill_price = float(fill_row.get("fill_price") or 0)
            except (TypeError, ValueError):
                fill_price = None

        if filled:
            fill_reason = "LIMIT_CHASE" if chase_count > 0 else "STATIC_LIMIT"
        elif expire_row:
            fill_reason = "TTL_EXPIRED"
        else:
            fill_reason = "UNFILLED"

        filled_after_chase = bool(filled and chase_count > 0)
        saved_fill = False
        if filled_after_chase and original_limit and fill_price:
            saved_fill = abs(fill_price - original_limit) > 1.0 or len(chase_rows) > 0

        final_limit = None
        if chase_rows:
            try:
                final_limit = float(chase_rows[-1].get("new_limit") or chase_rows[-1].get("limit_price"))
            except (TypeError, ValueError):
                final_limit = None
        if final_limit is None and order_row:
            try:
                final_limit = float(order_row.get("limit_price") or 0) or None
            except (TypeError, ValueError):
                final_limit = None

        attr = {
            "trade_id": tid,
            "lane": str(lane).upper(),
            "label": RESEARCH_LANE_LABELS.get(str(lane).upper(), lane),
            "chase_count": chase_count,
            "chase_count_source": chase_count_source,
            "avg_hold_min": trade_hold.get(tid),
            "chase_events_logged": len(chase_rows),
            "first_chase_sec": first_chase_sec,
            "last_chase_sec": last_chase_sec,
            "filled_after_chase": filled_after_chase,
            "fill_reason": fill_reason,
            "saved_fill": saved_fill,
            "original_limit_price": original_limit,
            "final_limit_price": final_limit,
            "fill_price": fill_price,
            "net_pnl_usd": trade_pnl.get(tid),
            "win": trade_wr.get(tid),
            "ttl_expired": expire_row is not None and not filled,
        }
        attributions.append(attr)
        if chase_count or filled or expire_row:
            print(
                f"  {tid[:12]}… lane={attr['lane']} chase={chase_count} "
                f"first={first_chase_sec}s filled={filled} saved={saved_fill} "
                f"reason={fill_reason} {PIPELINE_ENFORCEMENT_TAG}"
            )

    seen_tids = {a.get("trade_id") for a in attributions}
    for tid, pnl in trade_pnl.items():
        if tid in seen_tids:
            continue
        chase_count = int(trade_chase.get(tid) or 0)
        attributions.append({
            "trade_id": tid,
            "lane": "UNKNOWN",
            "label": "UNKNOWN",
            "chase_count": chase_count,
            "chase_events_logged": 0,
            "first_chase_sec": None,
            "last_chase_sec": None,
            "filled_after_chase": chase_count > 0,
            "fill_reason": "STATIC_LIMIT" if chase_count <= 0 else "LIMIT_CHASE",
            "saved_fill": False,
            "original_limit_price": None,
            "final_limit_price": None,
            "fill_price": None,
            "net_pnl_usd": pnl,
            "win": trade_wr.get(tid),
            "ttl_expired": False,
            "chase_count_source": "trades_3factor.limit_chase_count",
        })

    approve_count = sum(1 for r in rows if r.get("stage") == "APPROVE")
    orders_created = sum(1 for r in rows if r.get("stage") == "ORDER_SUBMITTED")
    fills_total = sum(1 for r in rows if r.get("stage") == "FILLED")
    ttl_expired = sum(1 for r in rows if r.get("stage") == "ORDER_EXPIRED")
    chase_assisted_fills = sum(1 for a in attributions if a.get("filled_after_chase"))
    saved_fills = sum(1 for a in attributions if a.get("saved_fill"))
    static_fills = sum(1 for a in attributions if a.get("fill_reason") == "STATIC_LIMIT")

    by_lane = {}
    for a in attributions:
        ln = a.get("lane") or "UNKNOWN"
        bucket = by_lane.setdefault(ln, {
            "orders": 0,
            "chase_events": 0,
            "chase_assisted_fills": 0,
            "saved_fills": 0,
            "static_fills": 0,
            "ttl_expired": 0,
            "sum_pnl_usd": 0.0,
        })
        bucket["orders"] += 1
        bucket["chase_events"] += int(a.get("chase_events_logged") or 0)
        if a.get("filled_after_chase"):
            bucket["chase_assisted_fills"] += 1
        if a.get("saved_fill"):
            bucket["saved_fills"] += 1
        if a.get("fill_reason") == "STATIC_LIMIT":
            bucket["static_fills"] += 1
        if a.get("ttl_expired"):
            bucket["ttl_expired"] += 1
        pnl = a.get("net_pnl_usd")
        if pnl is not None:
            bucket["sum_pnl_usd"] = round(bucket["sum_pnl_usd"] + float(pnl), 2)

    print(
        f"  Totals: approves={approve_count} orders={orders_created} "
        f"chase_events={chase_events_total} chase_fills={chase_assisted_fills} "
        f"saved_fills={saved_fills} static_fills={static_fills} ttl_expired={ttl_expired} "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )

    payload = {
        "schema": "chase_attribution_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "saved_fill=true when filled after chase moved limit away from original (> $1 or chase events logged).",
        "overnight_watch": {
            "approve": approve_count,
            "orders_created": orders_created,
            "chase_events": chase_events_total,
            "chase_assisted_fills": chase_assisted_fills,
            "saved_fills_heuristic": saved_fills,
            "static_limit_fills": static_fills,
            "ttl_expired": ttl_expired,
            "total_fills": fills_total,
        },
        "totals": {
            "trades_with_orders": len([a for a in attributions if a.get("chase_count") or a.get("fill_reason") != "UNFILLED"]),
            "chase_events": chase_events_total,
            "chase_assisted_fills": chase_assisted_fills,
            "saved_fills_heuristic": saved_fills,
            "ttl_expired": ttl_expired,
        },
        "by_lane": by_lane,
        "trades": attributions,
    }
    try:
        with open(analyzer_report_path(CHASE_ATTRIBUTION_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CHASE_ATTRIBUTION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CHASE_ATTRIBUTION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _chase_count_bucket(chase_count) -> str:
    try:
        n = int(chase_count or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 5:
        return "5+"
    return str(n)


def _chase_bucket_stats(attributions):
    order = ["0", "1", "2", "3", "4", "5+"]
    buckets = {
        k: {
            "trades": 0, "wins": 0, "sum_pnl_usd": 0.0, "win_rate_pct": 0.0, "ev_usd": 0.0,
            "avg_hold_min": 0.0, "_hold_n": 0,
        }
        for k in order
    }
    for row in attributions or []:
        if row.get("net_pnl_usd") is None and row.get("win") is None:
            continue
        key = _chase_count_bucket(row.get("chase_count"))
        b = buckets[key]
        b["trades"] += 1
        pnl = float(row.get("net_pnl_usd") or 0)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"] + pnl, 2)
        if row.get("win") or pnl > 0:
            b["wins"] += 1
        hold = row.get("avg_hold_min")
        if hold is not None:
            try:
                b["avg_hold_min"] = round(b["avg_hold_min"] + float(hold), 2)
                b["_hold_n"] += 1
            except (TypeError, ValueError):
                pass
    for key, b in buckets.items():
        n = b["trades"]
        hold_n = b.pop("_hold_n", 0)
        if hold_n:
            b["avg_hold_min"] = round(b["avg_hold_min"] / hold_n, 2)
        else:
            b["avg_hold_min"] = None
        if n:
            b["win_rate_pct"] = round(100.0 * b["wins"] / n, 1)
            b["ev_usd"] = round(b["sum_pnl_usd"] / n, 2)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"], 2)
    return buckets


def chase_effectiveness_report(trades=None, session=None, chase_payload=None):
    """Chase count buckets — are heavily chased fills rescue fills or profitable?"""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CHASE EFFECTIVENESS REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if chase_payload is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        try:
            with open(CHASE_ATTRIBUTION_REPORT_FILE, encoding="utf-8") as f:
                chase_payload = json.load(f)
        except Exception:
            chase_payload = None
    if chase_payload is None:
        chase_payload = chase_attribution_report(trades=trades, session=session)
    attributions = (chase_payload or {}).get("trades") or []
    buckets = _chase_bucket_stats(attributions)
    for key, b in buckets.items():
        if b["trades"]:
            print(
                f"  {key}: n={b['trades']} WR={b['win_rate_pct']:.1f}% "
                f"sum=${b['sum_pnl_usd']:.2f} EV=${b['ev_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )
    payload = {
        "schema": "chase_effectiveness_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Are heavily chased trades profitable or rescue fills?",
        "buckets": buckets,
    }
    try:
        with open(analyzer_report_path(CHASE_EFFECTIVENESS_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CHASE_EFFECTIVENESS_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CHASE_EFFECTIVENESS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _cumulative_chase_threshold_stats(attributions, min_chase: int) -> dict:
    rows = []
    for row in attributions or []:
        if row.get("net_pnl_usd") is None and row.get("win") is None:
            continue
        try:
            cc = int(row.get("chase_count") or row.get("limit_chase_count") or 0)
        except (TypeError, ValueError):
            cc = 0
        if cc >= min_chase:
            rows.append(row)
    n = len(rows)
    if not n:
        return {"trades": 0, "wins": 0, "wr": 0.0, "ev": 0.0, "pnl": 0.0}
    pnl = sum(float(r.get("net_pnl_usd") or 0) for r in rows)
    wins = sum(1 for r in rows if r.get("win") or float(r.get("net_pnl_usd") or 0) > 0)
    return {
        "trades": n,
        "wins": wins,
        "wr": round(100.0 * wins / n, 1),
        "ev": round(pnl / n, 2),
        "pnl": round(pnl, 2),
    }


def _exact_chase_bucket_stats(attributions):
    """Exact limit_chase_count buckets: 0, 1, 2, 3, 4, 5+."""
    return _chase_bucket_stats(attributions)


def chase_threshold_report(trades=None, session=None, chase_payload=None):
    """Exact chase-count buckets — EV/WR/PnL at each limit_chase_count (0, 1, 2, 3, 4, 5+)."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CHASE THRESHOLD REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if chase_payload is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        try:
            with open(CHASE_ATTRIBUTION_REPORT_FILE, encoding="utf-8") as f:
                chase_payload = json.load(f)
        except Exception:
            chase_payload = None
    if chase_payload is None:
        chase_payload = chase_attribution_report(trades=trades, session=session)
    attributions = (chase_payload or {}).get("trades") or []
    thresholds = _exact_chase_bucket_stats(attributions)
    for key, block in thresholds.items():
        if block["trades"]:
            print(
                f"  chase={key}: n={block['trades']} WR={block['win_rate_pct']:.1f}% "
                f"PnL=${block['sum_pnl_usd']:.2f} EV=${block['ev_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )
    payload = {
        "schema": "chase_threshold_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Per exact limit_chase_count bucket (0, 1, 2, 3, 4, 5+) — EV/WR/PnL.",
        "chase_count_source": "trades_3factor.limit_chase_count when execution_funnel lacks LIMIT_CHASE rows",
        "thresholds": thresholds,
    }
    try:
        with open(analyzer_report_path(CHASE_THRESHOLD_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CHASE_THRESHOLD_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CHASE_THRESHOLD_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _avg_entry_distance_pct(sub: pd.DataFrame):
    if sub is None or sub.empty:
        return None
    if "signal_price" in sub.columns and ("entry" in sub.columns or "fill_price" in sub.columns):
        sig = pd.to_numeric(sub["signal_price"], errors="coerce")
        entry_col = "entry" if "entry" in sub.columns else "fill_price"
        entry = pd.to_numeric(sub[entry_col], errors="coerce")
        mask = sig.notna() & entry.notna() & (sig > 0)
        if mask.any():
            dist = (entry[mask] - sig[mask]).abs() / sig[mask] * 100.0
            return round(float(dist.mean()), 4)
    for col in ("entry_slippage", "slippage"):
        if col in sub.columns:
            slip = pd.to_numeric(sub[col], errors="coerce")
            if slip.notna().any():
                return round(float(slip.mean()), 4)
    return None


def chase_delay_report(trades=None, session=None, benchmark_report=None, chase_payload=None):
    """Compare COMBO Direct vs Chase 3+ lanes within each AI/spread tier."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CHASE DELAY REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if benchmark_report is None and os.path.isfile(BENCHMARK_VS_LANES_REPORT_FILE):
        benchmark_report = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    lanes_meta = (benchmark_report or {}).get("lanes") or {}
    compare_lanes = tuple(COMBO_CHASE_DELAY_LANES)

    def _lane_summary(lane_key: str) -> dict:
        m = lanes_meta.get(lane_key) or {}
        fills = int(m.get("real_fills") or m.get("fills") or 0)
        approves = int(m.get("approves") or 0)
        pnl = float(m.get("net_pnl_real") or m.get("net_pnl_usd") or 0)
        fill_pct = float(m.get("approve_to_fill_pct") or (100.0 * fills / approves if approves else 0))
        ev_approve = float(m.get("per_approve_ev") or 0)
        block = {
            "approves": approves,
            "fills": fills,
            "fill_pct": round(fill_pct, 1),
            "pnl_usd": round(pnl, 2),
            "ev_per_approve": round(ev_approve, 2),
            "wr_pct": None,
            "ev_usd": None,
            "avg_signal_age_sec": None,
            "avg_entry_distance_pct": None,
            "label": RESEARCH_LANE_LABELS.get(lane_key, lane_key),
        }
        if trades is not None and not trades.empty and "research_lane" in trades.columns:
            sub = trades[trades["research_lane"].astype(str).str.upper() == lane_key]
            if not sub.empty:
                stats = _combo_stats_from_df(sub)
                block["wr_pct"] = stats["wr_pct"]
                block["ev_usd"] = stats["ev_usd"]
                sa = pd.to_numeric(sub.get("signal_age_sec", sub.get("execution_fill_delay_sec")), errors="coerce")
                if sa.notna().any():
                    block["avg_signal_age_sec"] = round(float(sa.mean()), 1)
                block["avg_entry_distance_pct"] = _avg_entry_distance_pct(sub)
        return block

    lane_blocks = {ln: _lane_summary(ln) for ln in compare_lanes}
    direct_ref = COMBO_CHASE_DIRECT_REFERENCE
    chase_ref = BENCHMARK_LANE
    bench = lane_blocks.get(direct_ref) or {}
    chase_primary = lane_blocks.get(chase_ref) or {}
    delta = {
        "ev_per_approve": round(chase_primary.get("ev_per_approve", 0) - bench.get("ev_per_approve", 0), 2),
        "pnl_usd": round(chase_primary.get("pnl_usd", 0) - bench.get("pnl_usd", 0), 2),
        "fill_pct": round(chase_primary.get("fill_pct", 0) - bench.get("fill_pct", 0), 1),
        "avg_signal_age_sec": (
            round(chase_primary.get("avg_signal_age_sec", 0) - bench.get("avg_signal_age_sec", 0), 1)
            if chase_primary.get("avg_signal_age_sec") is not None and bench.get("avg_signal_age_sec") is not None
            else None
        ),
    }
    adequate = chase_primary.get("fills", 0) >= MIN_LANE_FILLS_FOR_RETIREMENT
    beats_bench = (
        chase_primary.get("ev_per_approve", 0) > bench.get("ev_per_approve", 0)
        or chase_primary.get("pnl_usd", 0) > bench.get("pnl_usd", 0)
    )
    if chase_primary.get("fills", 0) == 0:
        verdict = "COLLECTING"
    elif adequate and beats_bench:
        verdict = "PROMISING"
    elif adequate and not beats_bench:
        verdict = "RETIRE_CANDIDATE"
    else:
        verdict = "INSUFFICIENT_SAMPLE"

    for ln, b in lane_blocks.items():
        if b.get("fills") or b.get("approves"):
            print(
                f"  {ln}: approves={b['approves']} fills={b['fills']} fill%={b['fill_pct']:.1f} "
                f"WR={b.get('wr_pct')}% EV=${b.get('ev_usd')} PnL=${b['pnl_usd']:.2f} "
                f"age={b.get('avg_signal_age_sec')}s dist={b.get('avg_entry_distance_pct')}% "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )

    payload = {
        "schema": "chase_delay_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Does COMBO Chase 3+ beat COMBO Direct on EV per approve within each AI/spread tier?",
        "benchmark_lane": chase_ref,
        "direct_reference_lane": direct_ref,
        "lane_order": list(compare_lanes),
        "lanes": lane_blocks,
        "delta_chase_vs_direct_primary": delta,
        "delta_chase_3plus_vs_continuous": delta,
        "verdict": verdict,
        "success_criteria": f"Beat {direct_ref} on EV per approve or total PnL after adequate sample",
        "min_fills_for_decision": MIN_LANE_FILLS_FOR_RETIREMENT,
    }
    try:
        with open(CHASE_DELAY_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CHASE_DELAY_REPORT_FILE} verdict={verdict} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CHASE_DELAY_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _replay_unreal_at_sec(replay: dict, offset_sec: float):
    """Nearest tick unreal_pct at offset_sec after virtual fill."""
    ticks = sorted(replay.get("ticks") or [], key=lambda t: float(t.get("t") or 0))
    fill_t = replay.get("virtual_fill_t")
    if fill_t is None:
        for tick in ticks:
            if tick.get("unreal_pct") is not None:
                fill_t = float(tick.get("t") or 0)
                break
    if fill_t is None:
        fill_t = 0.0
    target = float(fill_t) + float(offset_sec)
    best = None
    best_dist = None
    for tick in ticks:
        if tick.get("unreal_pct") is None:
            continue
        t = float(tick.get("t") or 0)
        if t < float(fill_t):
            continue
        dist = abs(t - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = tick
    if best is None or best_dist is None or best_dist > 120:
        return None
    return float(best.get("unreal_pct"))


def first_15m_outcome_report(trades=None, session=None):
    """+5m / +15m / +30m unreal% after entry — immediate right vs wrong vs recovery."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== FIRST 15M OUTCOME REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    replays = _filter_jsonl_rows_by_session(_load_jsonl_rows(SIGNAL_REPLAY_FILE), session)
    replay_by_id = {}
    for row in replays:
        tid = str(row.get("trade_id") or "")
        if tid and row.get("virtual_entry"):
            replay_by_id[tid] = row

    trade_pnl = {}
    trade_exit = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        for _, t in work.iterrows():
            tid = str(t.get("trade_id") or "")
            if not tid:
                continue
            trade_pnl[tid] = float(t.get(pnl_col) or 0)
            trade_exit[tid] = str(t.get("exit_reason") or "")

    horizons = {"5m": 300, "15m": 900, "30m": 1800}
    rows_out = []
    for tid, replay in replay_by_id.items():
        if tid not in trade_pnl:
            continue
        h = {label: _replay_unreal_at_sec(replay, sec) for label, sec in horizons.items()}
        final_pnl = trade_pnl[tid]
        row = {
            "trade_id": tid,
            "lane": replay.get("lane") or replay.get("research_lane"),
            "direction": replay.get("direction"),
            "exit_reason": trade_exit.get(tid),
            "net_pnl_usd": round(final_pnl, 2),
            "win": final_pnl > 0,
            "unreal_5m_pct": round(h["5m"], 2) if h["5m"] is not None else None,
            "unreal_15m_pct": round(h["15m"], 2) if h["15m"] is not None else None,
            "unreal_30m_pct": round(h["30m"], 2) if h["30m"] is not None else None,
        }
        u5 = h["5m"]
        if u5 is not None:
            if u5 >= 2:
                row["early_tag"] = "immediately_right"
            elif u5 <= -6:
                row["early_tag"] = "immediately_wrong"
            elif final_pnl > 0 and u5 < 0:
                row["early_tag"] = "recovered"
            else:
                row["early_tag"] = "mixed"
        else:
            row["early_tag"] = "no_tick_data"
        rows_out.append(row)

    tags = {}
    for row in rows_out:
        tag = row.get("early_tag") or "unknown"
        bucket = tags.setdefault(tag, {"n": 0, "wins": 0, "sum_pnl_usd": 0.0})
        bucket["n"] += 1
        bucket["sum_pnl_usd"] = round(bucket["sum_pnl_usd"] + float(row.get("net_pnl_usd") or 0), 2)
        if row.get("win"):
            bucket["wins"] += 1

    fast_cut = [r for r in rows_out if r.get("exit_reason") == "THESIS_FAST_CUT"]
    fc_tags = {}
    for row in fast_cut:
        tag = row.get("early_tag") or "unknown"
        fc_tags[tag] = fc_tags.get(tag, 0) + 1

    for tag, b in sorted(tags.items()):
        wr = round(100.0 * b["wins"] / b["n"], 1) if b["n"] else 0.0
        print(f"  {tag}: n={b['n']} WR={wr}% sum=${b['sum_pnl_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if fast_cut:
        print(f"  THESIS_FAST_CUT early tags: {fc_tags} {PIPELINE_ENFORCEMENT_TAG}")

    payload = {
        "schema": "first_15m_outcome_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_replays_with_trades": len(rows_out),
        "early_tags": tags,
        "fast_cut_early_tags": fc_tags,
        "trades": rows_out[:200],
    }
    try:
        with open(FIRST_15M_OUTCOME_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {FIRST_15M_OUTCOME_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {FIRST_15M_OUTCOME_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def scenario_c_leakage_report(trades=None, session=None):
    """Peak MFE vs booked profit — Scenario C capture ratio."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== SCENARIO C LEAKAGE REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if trades is None or trades.empty:
        print(f"  No trades {PIPELINE_ENFORCEMENT_TAG}")
        return {}
    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
    work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce")
    mfe = pd.to_numeric(work.get("max_profit", work.get("mfe_margin_pct")), errors="coerce")
    final_margin = pd.to_numeric(work.get("pnl", work.get("final_pnl_margin_pct")), errors="coerce")
    margin_usd = pd.to_numeric(work.get("margin_usdt", FLAT_MARGIN_LIVE_USD), errors="coerce").fillna(FLAT_MARGIN_LIVE_USD)
    peak_usd = (mfe / 100.0) * margin_usd
    booked_usd = work[pnl_col].fillna((final_margin / 100.0) * margin_usd)
    left_usd = (peak_usd - (final_margin / 100.0) * margin_usd).clip(lower=0)
    capture = (booked_usd / peak_usd.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    overall = {
        "trades": int(len(work)),
        "peak_profit_usd": round(float(peak_usd.sum()), 2),
        "booked_profit_usd": round(float(booked_usd.sum()), 2),
        "left_on_table_usd": round(float(left_usd.sum()), 2),
        "capture_ratio_pct": round(float(capture.mean(skipna=True) * 100), 1) if capture.notna().any() else 0.0,
        "avg_mfe_margin_pct": round(float(mfe.mean()), 2) if mfe.notna().any() else 0.0,
    }
    by_exit = {}
    if "exit_reason" in work.columns:
        work = work.assign(_peak_usd=peak_usd, _booked_usd=booked_usd, _capture=capture)
        for reason, sub in work.groupby("exit_reason", observed=True):
            if sub.empty:
                continue
            by_exit[str(reason)] = {
                "trades": int(len(sub)),
                "peak_profit_usd": round(float(sub["_peak_usd"].sum()), 2),
                "booked_profit_usd": round(float(sub["_booked_usd"].sum()), 2),
                "capture_ratio_pct": round(float(sub["_capture"].mean(skipna=True) * 100), 1)
                if sub["_capture"].notna().any() else 0.0,
            }

    print(
        f"  Overall: peak=${overall['peak_profit_usd']:.2f} booked=${overall['booked_profit_usd']:.2f} "
        f"left=${overall['left_on_table_usd']:.2f} capture={overall['capture_ratio_pct']:.1f}% "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    for reason, b in sorted(by_exit.items(), key=lambda x: -x[1].get("booked_profit_usd", 0))[:6]:
        print(
            f"  {reason}: n={b['trades']} booked=${b['booked_profit_usd']:.2f} "
            f"capture={b['capture_ratio_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "scenario_c_leakage_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "by_exit_reason": by_exit,
    }
    try:
        with open(SCENARIO_C_LEAKAGE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {SCENARIO_C_LEAKAGE_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {SCENARIO_C_LEAKAGE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _numeric_series(df, *col_names, default=np.nan):
    """Return a numeric Series from the first present column (never a scalar)."""
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return pd.Series(dtype=float)
    if isinstance(df, pd.Series):
        return pd.to_numeric(df, errors="coerce")
    for name in col_names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(default, index=df.index, dtype=float)


def _direction_bias_trade_stats(sub):
    """Executed-trade cohort stats for ai_direction_bias_report.json."""
    base = _direction_cohort_stats(sub)
    empty_feats = {
        "count": 0,
        "avg_bull_score": 0.0,
        "avg_bear_score": 0.0,
        "avg_structure": 0.0,
        "avg_spread": 0.0,
        "wr": 0.0,
        "pnl": 0.0,
    }
    if sub is None or sub.empty:
        base.update(empty_feats)
        return base
    bull = _numeric_series(sub, "bull_score_at_entry")
    bear = _numeric_series(sub, "bear_score_at_entry")
    struct = _numeric_series(sub, "structure_score_at_entry")
    if "directional_spread" in sub.columns:
        spread = pd.to_numeric(sub["directional_spread"], errors="coerce")
    else:
        spread = bull - bear
    base.update(
        {
            "count": base["trades"],
            "avg_bull_score": round(float(bull.mean()), 2) if bull.notna().any() else 0.0,
            "avg_bear_score": round(float(bear.mean()), 2) if bear.notna().any() else 0.0,
            "avg_structure": round(float(struct.mean()), 2) if struct.notna().any() else 0.0,
            "avg_spread": round(float(spread.mean()), 2) if spread.notna().any() else 0.0,
            "wr": base["win_rate_pct"],
            "pnl": base["sum_pnl_usd"],
        }
    )
    return base


def _ai_decision_bias_stats(rows, direction):
    """Aggregate AI APPROVE fingerprint for one direction from ai_reason_v2 rows."""
    direction = str(direction).upper()
    cohort = [
        r
        for r in rows
        if str(r.get("direction") or "").upper() == direction
        and str(r.get("ai_decision") or "").upper() in ("APPROVE", "EXECUTE")
    ]
    if not cohort:
        return {
            "count": 0,
            "avg_bull_score": 0.0,
            "avg_bear_score": 0.0,
            "avg_structure": 0.0,
            "avg_spread": 0.0,
            "pct_bull_mtf": 0.0,
            "pct_bear_mtf": 0.0,
        }
    bull = pd.to_numeric(pd.Series([r.get("bull_score") for r in cohort]), errors="coerce")
    bear = pd.to_numeric(pd.Series([r.get("bear_score") for r in cohort]), errors="coerce")
    struct = pd.to_numeric(pd.Series([r.get("structure_score") for r in cohort]), errors="coerce")
    spread = pd.to_numeric(pd.Series([r.get("spread") for r in cohort]), errors="coerce")
    mtf = [str(r.get("mtf_agreement") or "").upper() for r in cohort]
    n = len(cohort)
    return {
        "count": n,
        "avg_bull_score": round(float(bull.mean()), 2) if bull.notna().any() else 0.0,
        "avg_bear_score": round(float(bear.mean()), 2) if bear.notna().any() else 0.0,
        "avg_structure": round(float(struct.mean()), 2) if struct.notna().any() else 0.0,
        "avg_spread": round(float(spread.mean()), 2) if spread.notna().any() else 0.0,
        "pct_bull_mtf": round(100.0 * sum(1 for m in mtf if "BULL" in m) / n, 1),
        "pct_bear_mtf": round(100.0 * sum(1 for m in mtf if "BEAR" in m) / n, 1),
    }


def _std_mean_diff(long_vals, short_vals):
    if len(long_vals) < 2 or len(short_vals) < 2:
        return 0.0
    l_arr = np.asarray(long_vals, dtype=float)
    s_arr = np.asarray(short_vals, dtype=float)
    l_m, s_m = float(l_arr.mean()), float(s_arr.mean())
    l_s, s_s = float(l_arr.std(ddof=1)), float(s_arr.std(ddof=1))
    pooled = float(np.sqrt((l_s ** 2 + s_s ** 2) / 2.0))
    if pooled < 1e-9:
        return abs(l_m - s_m)
    return abs(l_m - s_m) / pooled


def _direction_feature_importance(ai_rows):
    """Which features best separate AI LONG vs SHORT APPROVE decisions (normalized weights)."""
    approve = [
        r
        for r in ai_rows
        if str(r.get("ai_decision") or "").upper() in ("APPROVE", "EXECUTE")
        and str(r.get("direction") or "").upper() in ("LONG", "SHORT")
    ]
    long_rows = [r for r in approve if str(r.get("direction")).upper() == "LONG"]
    short_rows = [r for r in approve if str(r.get("direction")).upper() == "SHORT"]

    def nums(rows, key):
        return [float(r[key]) for r in rows if r.get(key) is not None and pd.notna(r.get(key))]

    bull_l, bull_s = nums(long_rows, "bull_score"), nums(short_rows, "bull_score")
    bear_l, bear_s = nums(long_rows, "bear_score"), nums(short_rows, "bear_score")
    struct_l, struct_s = nums(long_rows, "structure_score"), nums(short_rows, "structure_score")

    def mtf_bull_rate(rows):
        if not rows:
            return 0.0
        hits = sum(1 for r in rows if "BULL" in str(r.get("mtf_agreement") or "").upper())
        return hits / len(rows)

    mtf_diff = abs(mtf_bull_rate(long_rows) - mtf_bull_rate(short_rows))

    raw = {
        "bull_score_importance": _std_mean_diff(bull_l, bull_s),
        "bear_score_importance": _std_mean_diff(bear_l, bear_s),
        "structure_importance": _std_mean_diff(struct_l, struct_s),
        "mtf_importance": mtf_diff * 2.0,
    }
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 3) for k, v in raw.items()}


def ai_direction_bias_report(trades=None, decisions=None, session=None):
    """Why AI prefers LONG — executed outcomes vs AI decision fingerprints."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== AI DIRECTION BIAS REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    # Full ai_reason cohort (not session-filtered) — matches direction_attribution_report.
    ai_rows = [r for r in _load_jsonl_rows(AI_REASON_RESEARCH_FILE) if r.get("schema") == "ai_reason_v2"]

    long_exec = pd.DataFrame()
    short_exec = pd.DataFrame()
    if trades is not None and not trades.empty:
        work = trades.copy()
        if "trade_id" in work.columns:
            work = work.drop_duplicates(subset=["trade_id"], keep="last")
        if "final_direction" not in work.columns:
            work["final_direction"] = work.get("dir", "UNKNOWN")
        work["final_direction"] = work["final_direction"].astype(str).str.upper()
        long_exec = work[work["final_direction"] == "LONG"]
        short_exec = work[work["final_direction"] == "SHORT"]

    long_stats = _direction_bias_trade_stats(long_exec)
    short_stats = _direction_bias_trade_stats(short_exec)
    ai_long = _ai_decision_bias_stats(ai_rows, "LONG")
    ai_short = _ai_decision_bias_stats(ai_rows, "SHORT")
    importance = _direction_feature_importance(ai_rows)

    long_calls = ai_long["count"]
    short_calls = ai_short["count"]
    bias_ratio = round(long_calls / max(short_calls, 1), 2)

    print(
        f"  Executed LONG: n={long_stats['count']} WR={long_stats['wr']:.1f}% pnl=${long_stats['pnl']:.2f} "
        f"bull={long_stats['avg_bull_score']} bear={long_stats['avg_bear_score']} {PIPELINE_ENFORCEMENT_TAG}"
    )
    print(
        f"  Executed SHORT: n={short_stats['count']} WR={short_stats['wr']:.1f}% pnl=${short_stats['pnl']:.2f} "
        f"bull={short_stats['avg_bull_score']} bear={short_stats['avg_bear_score']} {PIPELINE_ENFORCEMENT_TAG}"
    )
    print(
        f"  AI APPROVE calls: LONG={long_calls} SHORT={short_calls} ratio={bias_ratio:.2f} "
        f"importance={importance} {PIPELINE_ENFORCEMENT_TAG}"
    )

    payload = {
        "schema": "ai_direction_bias_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executed_trades": {
            "LONG": {
                "count": long_stats["count"],
                "avg_bull_score": long_stats["avg_bull_score"],
                "avg_bear_score": long_stats["avg_bear_score"],
                "avg_structure": long_stats["avg_structure"],
                "avg_spread": long_stats["avg_spread"],
                "wr": long_stats["wr"],
                "pnl": long_stats["pnl"],
            },
            "SHORT": {
                "count": short_stats["count"],
                "avg_bull_score": short_stats["avg_bull_score"],
                "avg_bear_score": short_stats["avg_bear_score"],
                "avg_structure": short_stats["avg_structure"],
                "avg_spread": short_stats["avg_spread"],
                "wr": short_stats["wr"],
                "pnl": short_stats["pnl"],
            },
        },
        "ai_decisions": {
            "LONG": ai_long,
            "SHORT": ai_short,
            "long_short_call_ratio": bias_ratio,
        },
        "feature_importance": importance,
        "interpretation": {
            "executed_edge_favors": "SHORT" if short_stats["pnl"] > long_stats["pnl"] else "LONG",
            "ai_call_bias_favors": "LONG" if long_calls > short_calls else "SHORT",
            "top_driver": max(importance, key=importance.get),
        },
    }
    try:
        with open(AI_DIRECTION_BIAS_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {AI_DIRECTION_BIAS_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {AI_DIRECTION_BIAS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def edge_predictiveness_report(trades=None, session=None):
    """Edge bucket WR/PnL/EV/MFE — tests whether edge score predicts outcomes."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EDGE PREDICTIVENESS REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    if trades is None or trades.empty:
        payload = {
            "schema": "edge_predictiveness_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "buckets": [],
            "overall": {},
        }
        try:
            with open(EDGE_PREDICTIVENESS_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Could not write {EDGE_PREDICTIVENESS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    edge = _numeric_series(work, "edge_score")
    work = work.assign(edge_score=edge)
    work["edge_bucket"] = work["edge_score"].apply(_edge_score_bucket_val)

    buckets = []
    for bucket in EDGE_BUCKET_ORDER:
        sub = work[work["edge_bucket"] == bucket]
        stats = _ai_calib_cohort_stats(sub)
        mfe = _numeric_series(sub, "max_profit", "mfe_margin_pct")
        row = {
            "edge_bucket": bucket,
            "trades": stats["trades"],
            "win_rate_pct": stats["win_rate_pct"],
            "sum_pnl": stats["sum_pnl_usd"],
            "ev_usd": stats["ev_usd"],
            "avg_mfe_margin_pct": round(float(mfe.mean()), 2) if mfe.notna().any() else 0.0,
        }
        buckets.append(row)
        if stats["trades"] > 0:
            print(
                f"  {bucket}: n={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                f"EV=${stats['ev_usd']:.2f} MFE={row['avg_mfe_margin_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
            )

    pnl = _numeric_series(work, "net_pnl_usd", "outcome_net_pnl_usd")
    win = _numeric_series(work, "win_flag")
    mfe_all = _numeric_series(work, "max_profit", "mfe_margin_pct")
    corr_pnl = float(edge.corr(pnl)) if edge.notna().sum() > 2 and pnl.notna().sum() > 2 else 0.0
    corr_mfe = float(edge.corr(mfe_all)) if edge.notna().sum() > 2 and mfe_all.notna().sum() > 2 else 0.0
    corr_wr = float(edge.corr(win)) if edge.notna().sum() > 2 and win.notna().sum() > 2 else 0.0

    ev_series = [b["ev_usd"] for b in buckets if b["trades"] >= 10]
    monotonic = "INSUFFICIENT_DATA"
    if len(ev_series) >= 3:
        increases = sum(1 for i in range(1, len(ev_series)) if ev_series[i] >= ev_series[i - 1])
        decreases = len(ev_series) - 1 - increases
        if increases > decreases * 1.5:
            monotonic = "MONOTONIC_UP"
        elif decreases > increases * 1.5:
            monotonic = "MONOTONIC_DOWN"
        else:
            monotonic = "NOISY"

    best_bucket = max((b for b in buckets if b["trades"] >= 5), key=lambda x: x["sum_pnl"], default=None)
    overall = {
        "trades": int(len(work)),
        "correlation_edge_vs_pnl": round(corr_pnl, 4),
        "correlation_edge_vs_mfe": round(corr_mfe, 4),
        "correlation_edge_vs_win": round(corr_wr, 4),
        "monotonicity_verdict": monotonic,
        "best_pnl_bucket": best_bucket["edge_bucket"] if best_bucket else None,
        "edge_predictive": abs(corr_pnl) >= 0.15 and monotonic != "NOISY",
    }
    print(
        f"  Correlation(edge vs PnL)={overall['correlation_edge_vs_pnl']:.3f} "
        f"verdict={monotonic} {PIPELINE_ENFORCEMENT_TAG}"
    )

    payload = {
        "schema": "edge_predictiveness_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": buckets,
        "overall": overall,
    }
    try:
        with open(EDGE_PREDICTIVENESS_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EDGE_PREDICTIVENESS_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EDGE_PREDICTIVENESS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _edge_incremental_filter_metrics(cohort, min_edge, exit_by_id=None):
    """Metrics for APPROVE cohort filtered by min edge (0 = AI only)."""
    if cohort is None or cohort.empty:
        return {
            "min_edge": min_edge,
            "approves": 0,
            "fills": 0,
            "fill_pct": 0.0,
            "trades_with_outcome": 0,
            "win_rate_pct": 0.0,
            "sum_pnl_usd": 0.0,
            "ev_usd": 0.0,
            "ladder_hit_pct": 0.0,
        }
    work = cohort.copy()
    if min_edge and float(min_edge) > 0:
        edge = pd.to_numeric(work["edge_score"], errors="coerce")
        work = work[edge >= float(min_edge) - 1e-9]
    approves = int(len(work))
    executed = work[work["executed"].astype(bool)] if "executed" in work.columns else work.iloc[0:0]
    fills = int(len(executed))
    fill_pct = round(100.0 * fills / approves, 1) if approves else 0.0
    stats = _ai_calib_cohort_stats(executed)
    ladder_hits = 0
    ladder_known = 0
    if exit_by_id and fills:
        for tid in executed["trade_id"].astype(str):
            ex = exit_by_id.get(tid)
            if not ex:
                continue
            ladder_known += 1
            if str(ex).upper() == "PROFIT_LOCK_LADDER":
                ladder_hits += 1
    ladder_hit_pct = round(100.0 * ladder_hits / ladder_known, 1) if ladder_known else 0.0
    return {
        "min_edge": float(min_edge),
        "approves": approves,
        "fills": fills,
        "fill_pct": fill_pct,
        "trades_with_outcome": stats["trades"],
        "win_rate_pct": stats["win_rate_pct"],
        "sum_pnl_usd": stats["sum_pnl_usd"],
        "ev_usd": stats["ev_usd"],
        "ladder_hit_pct": ladder_hit_pct,
        "ladder_known_fills": ladder_known,
    }


def edge_score_decile_report(trades=None, session=None):
    """
    Whole-point edge deciles — flat WR/PnL across buckets means edge is non-predictive.
    Separate from AI calibration; do not tune edge from this until predictive value is proven.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EDGE SCORE DECILE REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    if trades is None or trades.empty:
        payload = {
            "schema": "edge_score_decile_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "deciles": [],
            "overall": {},
        }
        try:
            with open(EDGE_SCORE_DECILE_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Could not write {EDGE_SCORE_DECILE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    edge = _numeric_series(work, "edge_score", "edge_score_at_entry", "decision_edge_score")
    work = work.assign(edge_score=edge)
    work["edge_decile"] = work["edge_score"].apply(_edge_decile_bucket)

    deciles = []
    wr_vals = []
    ev_vals = []
    for bucket in EDGE_DECILE_ORDER:
        sub = work[work["edge_decile"] == bucket]
        stats = _ai_calib_cohort_stats(sub)
        row = {
            "edge_bucket": bucket,
            "trades": stats["trades"],
            "win_rate_pct": stats["win_rate_pct"],
            "sum_pnl_usd": stats["sum_pnl_usd"],
            "ev_usd": stats["ev_usd"],
        }
        deciles.append(row)
        if stats["trades"] >= 5:
            wr_vals.append(stats["win_rate_pct"])
            ev_vals.append(stats["ev_usd"])
        if stats["trades"] > 0:
            print(
                f"  {bucket}: n={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                f"sum=${stats['sum_pnl_usd']:.2f} EV=${stats['ev_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )

    pnl = _numeric_series(work, "net_pnl_usd", "outcome_net_pnl_usd")
    corr_pnl = float(edge.corr(pnl)) if edge.notna().sum() > 2 and pnl.notna().sum() > 2 else 0.0
    wr_spread = (max(wr_vals) - min(wr_vals)) if len(wr_vals) >= 2 else 0.0
    ev_spread = (max(ev_vals) - min(ev_vals)) if len(ev_vals) >= 2 else 0.0
    corr_abs = abs(corr_pnl)
    if corr_abs < 0.10:
        verdict = "edge_non_predictive"
        recommendation = (
            "Treat edge as informational only — correlation to PnL is ~0 (|r| < 0.10)."
        )
    elif corr_abs >= 0.15:
        verdict = "edge_may_predict"
        recommendation = (
            "Edge shows measurable correlation — validate with edge_incremental_value_report before gating."
        )
    else:
        verdict = "edge_weak_signal"
        recommendation = (
            "Edge correlation is weak (0.10–0.15) — decile table may look uneven from sample mix, not edge rank."
        )
    overall = {
        "trades": int(len(work)),
        "correlation_edge_vs_pnl": round(corr_pnl, 4),
        "wr_spread_across_deciles_pct": round(wr_spread, 1),
        "ev_spread_across_deciles_usd": round(ev_spread, 2),
        "edge_predictive": corr_abs >= 0.15,
        "verdict": verdict,
        "recommendation": recommendation,
    }
    print(
        f"  corr(edge,PnL)={overall['correlation_edge_vs_pnl']:.3f} WR spread={wr_spread:.1f}% "
        f"verdict={overall['verdict']} {PIPELINE_ENFORCEMENT_TAG}"
    )

    payload = {
        "schema": "edge_score_decile_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Does edge score predict outcomes, or is it flat noise?",
        "usage_note": "Validation only — do not calibrate edge thresholds from this report.",
        "deciles": deciles,
        "overall": overall,
    }
    try:
        with open(EDGE_SCORE_DECILE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EDGE_SCORE_DECILE_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EDGE_SCORE_DECILE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def edge_incremental_value_report(trades=None, session=None):
    """
    AI-only vs AI+edge filter — does edge improve WR, PnL, EV, ladder hit %, fill %?
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EDGE INCREMENTAL VALUE REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    cohort = _build_ai_calibration_cohort(trades, session)
    exit_by_id = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns and "exit_reason" in trades.columns:
        for _, t in trades.drop_duplicates(subset=["trade_id"], keep="last").iterrows():
            tid = str(t.get("trade_id") or "")
            if tid:
                exit_by_id[tid] = t.get("exit_reason")

    if cohort.empty:
        payload = {
            "schema": "edge_incremental_value_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filters": [],
            "baseline": {},
            "verdict": "insufficient_data",
        }
        try:
            with open(EDGE_INCREMENTAL_VALUE_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Could not write {EDGE_INCREMENTAL_VALUE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return payload

    filters = []
    for min_edge in EDGE_INCREMENTAL_THRESHOLDS:
        filters.append(_edge_incremental_filter_metrics(cohort, min_edge, exit_by_id=exit_by_id))

    baseline = filters[0] if filters else {}
    best_ev = baseline
    for row in filters[1:]:
        if row["approves"] >= 10 and row.get("ev_usd", 0) > best_ev.get("ev_usd", -999):
            best_ev = row

    improves_wr = any(
        f["min_edge"] > 0 and f["approves"] >= 10 and f["win_rate_pct"] > baseline.get("win_rate_pct", 0) + 2
        for f in filters
    )
    improves_ev = best_ev.get("min_edge", 0) > 0 and best_ev.get("ev_usd", 0) > baseline.get("ev_usd", 0) + 0.15
    improves_ladder = any(
        f["min_edge"] > 0 and f["ladder_known_fills"] >= 5
        and f["ladder_hit_pct"] > baseline.get("ladder_hit_pct", 0) + 3
        for f in filters
    )

    if not improves_wr and not improves_ev and not improves_ladder:
        verdict = "edge_no_incremental_value"
        recommendation = "Keep edge informational only — AI-only baseline is not improved by edge gates."
    elif improves_ev or improves_wr:
        verdict = "edge_may_add_value"
        recommendation = (
            f"Edge >= {best_ev.get('min_edge')} shows best EV in sample — validate on next 48-72h window before gating."
        )
    else:
        verdict = "mixed"
        recommendation = "Edge moves ladder hit rate but not EV/WR — review manually."

    for row in filters:
        b_ev = baseline.get("ev_usd") or 0
        b_wr = baseline.get("win_rate_pct") or 0
        row["delta_ev_vs_ai_only"] = round(row["ev_usd"] - b_ev, 2) if row["min_edge"] > 0 else 0.0
        row["delta_wr_vs_ai_only"] = round(row["win_rate_pct"] - b_wr, 1) if row["min_edge"] > 0 else 0.0
        row["label"] = "AI_only" if row["min_edge"] == 0 else f"AI_and_edge_ge_{row['min_edge']}"
        if row["approves"] >= 5:
            print(
                f"  {row['label']}: approves={row['approves']} fill={row['fill_pct']:.1f}% "
                f"WR={row['win_rate_pct']:.1f}% EV=${row['ev_usd']:.2f} "
                f"ladder={row['ladder_hit_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
            )

    print(f"  Verdict: {verdict} | live edge min={LIVE_EDGE_THRESHOLD_DEFAULT} {PIPELINE_ENFORCEMENT_TAG}")

    payload = {
        "schema": "edge_incremental_value_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Does filtering APPROVEs by edge improve outcomes vs AI-only?",
        "usage_note": "Validation only — do not auto-change bot edge gates from this file.",
        "cohort_approves": int(len(cohort)),
        "live_edge_threshold": LIVE_EDGE_THRESHOLD_DEFAULT,
        "baseline_ai_only": baseline,
        "filters": filters,
        "best_ev_filter": best_ev,
        "verdict": verdict,
        "recommendation": recommendation,
    }
    try:
        with open(EDGE_INCREMENTAL_VALUE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EDGE_INCREMENTAL_VALUE_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EDGE_INCREMENTAL_VALUE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def scenario_c_capture_ratio_report(trades=None, session=None):
    """Per-trade MFE capture ratio — cleaner benchmark than sum-based leakage %."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== SCENARIO C CAPTURE RATIO — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    if trades is None or trades.empty:
        print(f"  No trades {PIPELINE_ENFORCEMENT_TAG}")
        return {}

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    booked_usd = _numeric_series(work, "net_pnl_usd", "outcome_net_pnl_usd").fillna(0.0)
    mfe = _numeric_series(work, "max_profit", "mfe_margin_pct")
    margin_usd = _numeric_series(work, "margin_usdt").fillna(FLAT_MARGIN_LIVE_USD)
    peak_usd = (mfe / 100.0) * margin_usd
    capture_ratio = (booked_usd / peak_usd.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    capture_pct = capture_ratio * 100.0

    def _agg_stats(mask):
        sub_peak = peak_usd[mask]
        sub_book = booked_usd[mask]
        sub_cap = capture_pct[mask]
        valid_cap = sub_cap[sub_peak > 0].dropna()
        n = int(mask.sum())
        if n == 0:
            return {"trades": 0}
        out = {
            "trades": n,
            "available_mfe_usd": round(float(sub_peak.sum()), 2),
            "captured_usd": round(float(sub_book.sum()), 2),
            "mean_capture_pct": round(float(valid_cap.mean()), 1) if len(valid_cap) else 0.0,
            "median_capture_pct": round(float(valid_cap.median()), 1) if len(valid_cap) else 0.0,
        }
        if sub_peak.sum() > 0:
            out["aggregate_capture_pct"] = round(100.0 * sub_book.sum() / sub_peak.sum(), 1)
        return out

    overall_mask = peak_usd > 0
    winners_mask = overall_mask & (booked_usd > 0)
    exit_reason = (
        work["exit_reason"].astype(str)
        if "exit_reason" in work.columns
        else pd.Series("", index=work.index)
    )
    ladder_mask = overall_mask & (exit_reason == "PROFIT_LOCK_LADDER")

    capture_buckets = []
    for label, lo, hi in (
        ("0-30%", 0, 30),
        ("30-50%", 30, 50),
        ("50-70%", 50, 70),
        ("70-90%", 70, 90),
        ("90%+", 90, 1000),
    ):
        bmask = overall_mask & capture_pct.notna() & (capture_pct >= lo) & (capture_pct < hi)
        capture_buckets.append({"bucket": label, "trades": int(bmask.sum())})

    by_exit = {}
    if "exit_reason" in work.columns:
        for reason, sub in work.assign(_cap=capture_pct, _peak=peak_usd, _book=booked_usd).groupby(
            "exit_reason", observed=True
        ):
            valid = sub["_peak"] > 0
            caps = sub.loc[valid, "_cap"].dropna()
            by_exit[str(reason)] = {
                "trades": int(len(sub)),
                "available_mfe_usd": round(float(sub["_peak"].sum()), 2),
                "captured_usd": round(float(sub["_book"].sum()), 2),
                "mean_capture_pct": round(float(caps.mean()), 1) if len(caps) else 0.0,
                "median_capture_pct": round(float(caps.median()), 1) if len(caps) else 0.0,
            }

    overall = _agg_stats(overall_mask)
    winners = _agg_stats(winners_mask)
    ladder = _agg_stats(ladder_mask)

    print(
        f"  Overall (MFE>0): mean_capture={overall.get('mean_capture_pct', 0):.1f}% "
        f"median={overall.get('median_capture_pct', 0):.1f}% "
        f"avail=${overall.get('available_mfe_usd', 0):.2f} captured=${overall.get('captured_usd', 0):.2f} "
        f"{PIPELINE_ENFORCEMENT_TAG}"
    )
    if ladder.get("trades"):
        print(
            f"  Ladder only: mean_capture={ladder.get('mean_capture_pct', 0):.1f}% "
            f"n={ladder['trades']} {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "scenario_c_capture_ratio_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "capture_pct = net_pnl_usd / (max_profit_margin_pct * margin_usdt / 100) per trade",
        "overall_mfe_positive": overall,
        "winners_only": winners,
        "ladder_exits_only": ladder,
        "capture_distribution": capture_buckets,
        "by_exit_reason": by_exit,
        "note": (
            "Use mean/median capture_pct — not sum(booked)/sum(peak) across losers "
            "(scenario_c_leakage aggregate can go negative when fast-cut/stop booked USD is negative)."
        ),
    }
    try:
        with open(SCENARIO_C_CAPTURE_RATIO_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {SCENARIO_C_CAPTURE_RATIO_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {SCENARIO_C_CAPTURE_RATIO_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _replay_fill_t(replay):
    """Seconds from replay start to virtual fill (or first in-position tick)."""
    if not replay:
        return 0.0
    fill_t = replay.get("virtual_fill_t")
    if fill_t is not None:
        return float(fill_t)
    for tick in sorted(replay.get("ticks") or [], key=lambda x: float(x.get("t") or 0)):
        if tick.get("unreal_pct") is not None:
            return float(tick.get("t") or 0)
    return 0.0


def _tick_unreal_pct(tick, entry, direction, leverage):
    """Margin % unrealized PnL for one replay tick."""
    if not tick:
        return None
    unreal = tick.get("unreal_pct")
    if unreal is not None:
        return float(unreal)
    price = tick.get("price")
    if price is None or entry is None or float(entry) <= 0:
        return None
    dir_factor = 1 if str(direction).upper() == "LONG" else -1
    lev = float(leverage or 100)
    return ((float(price) - float(entry)) / float(entry)) * dir_factor * lev * 100.0


def _locate_fast_cut_exit(
    ticks,
    entry,
    direction,
    leverage,
    fast_cut_pct,
    ladder,
    thesis_exit_above,
    fill_t=None,
    mfe_protect_pct=None,
):
    """Walk ticks until THESIS_FAST_CUT would fire; return exit metadata or None."""
    if not ticks or not entry or float(entry) <= 0:
        return None
    dir_factor = 1 if str(direction).upper() == "LONG" else -1
    lev = float(leverage or 100)
    peak = 0.0
    if fill_t is None:
        for tick in ticks:
            if tick.get("unreal_pct") is not None:
                fill_t = float(tick.get("t") or 0)
                break
    fill_t = float(fill_t or 0)
    for tick in sorted(ticks, key=lambda x: x.get("seq", 0)):
        price = tick.get("price")
        if price is None or float(price) <= 0:
            continue
        t_rel = float(tick.get("t") or 0)
        if t_rel < fill_t:
            continue
        unreal = _tick_unreal_pct(tick, entry, direction, lev)
        if unreal is None:
            unreal = ((float(price) - float(entry)) / float(entry)) * dir_factor * lev * 100.0
        peak = max(peak, unreal)
        if peak >= ladder[0][0]:
            continue
        if unreal > thesis_exit_above:
            continue
        if unreal <= fast_cut_pct:
            if (
                mfe_protect_pct is not None
                and mfe_protect_pct > 0
                and peak >= mfe_protect_pct
            ):
                continue
            return {
                "exit_t": t_rel,
                "exit_unreal_pct": round(unreal, 2),
                "peak_at_cut_pct": round(peak, 2),
            }
    return None


def _hold_through_post_cut_metrics(ticks, entry, direction, leverage, exit_t, green_horizons, mfe_horizons):
    """
    Counterfactual: position stays open at fast-cut moment.
    green_after_Xm = unreal > 0 at exit_t + horizon; future_mfe_Xm = max unreal in [exit_t, exit_t + horizon].
    """
    if exit_t is None or not ticks:
        return {}, {}
    sorted_ticks = sorted(ticks, key=lambda x: float(x.get("t") or 0))
    max_t = max(float(t.get("t") or 0) for t in sorted_ticks) if sorted_ticks else exit_t
    green = {}
    future_mfe = {}
    for label, sec in green_horizons.items():
        target = float(exit_t) + float(sec)
        if max_t + 180 < target:
            green[f"green_after_{label}"] = None
            continue
        best = None
        best_dist = None
        for tick in sorted_ticks:
            t = float(tick.get("t") or 0)
            if t < exit_t:
                continue
            u = _tick_unreal_pct(tick, entry, direction, leverage)
            if u is None:
                continue
            dist = abs(t - target)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = u
        if best is None or best_dist is None or best_dist > 180:
            green[f"green_after_{label}"] = None
        else:
            green[f"green_after_{label}"] = bool(best > 0)
    for label, sec in mfe_horizons.items():
        target = float(exit_t) + float(sec)
        if max_t < exit_t:
            future_mfe[f"future_mfe_{label}"] = None
            continue
        peak = None
        for tick in sorted_ticks:
            t = float(tick.get("t") or 0)
            if t < exit_t or t > target:
                continue
            u = _tick_unreal_pct(tick, entry, direction, leverage)
            if u is None:
                continue
            peak = u if peak is None else max(peak, u)
        future_mfe[f"future_mfe_{label}"] = round(peak, 2) if peak is not None else None
    future_vals = [v for v in future_mfe.values() if v is not None]
    future_mfe["future_max_mfe"] = round(max(future_vals), 2) if future_vals else None
    return green, future_mfe


def _path_peak_after_fill(ticks, entry, direction, leverage, fill_t):
    """Max margin-% unreal on replay ticks from fill onward."""
    peak = None
    fill_t = float(fill_t or 0)
    for tick in sorted(ticks or [], key=lambda x: x.get("seq", 0)):
        if float(tick.get("t") or 0) < fill_t:
            continue
        u = _tick_unreal_pct(tick, entry, direction, leverage)
        if u is None:
            continue
        peak = u if peak is None else max(peak, u)
    return peak


def _ladder_rung_hits(peak_pct):
    """Which Scenario C ladder rungs a peak margin % would have reached."""
    peak = float(peak_pct or 0)
    hits = {}
    for i, (trigger, _lock) in enumerate(TRAIL_LADDER[:3]):
        hits[f"would_hit_ladder_{i + 1}"] = peak >= trigger
    return hits


def _fast_cut_trade_replay(trade_row, replays):
    """Resolve signal_replay row for an executed trade (direct id or rev- prefix)."""
    tid = str(trade_row.get("trade_id") or "")
    if not tid:
        return None
    if tid in replays:
        return replays[tid]
    rev_key = f"rev-{tid}"
    if rev_key in replays:
        return replays[rev_key]
    return None


def _replay_keys_for_trade_id(trade_id: str) -> tuple:
    """Candidate replay keys for an executed trade_id (direct + rev- alias)."""
    tid = str(trade_id or "").strip()
    if not tid:
        return ()
    keys = [tid]
    if not tid.startswith("rev-"):
        keys.append(f"rev-{tid}")
    return tuple(keys)


def _trade_has_replay(trade_id: str, replays: dict) -> bool:
    return any(k in replays for k in _replay_keys_for_trade_id(trade_id))


def _resolve_trade_exit_t(replay, trade_row, exit_reason=None):
    """Best-effort exit timestamp (seconds from replay start) for horizon checks."""
    if replay and replay.get("exit_t_rel") is not None:
        try:
            return float(replay.get("exit_t_rel"))
        except (TypeError, ValueError):
            pass
    fill_t = _replay_fill_t(replay) if replay else 0.0
    dur = pd.to_numeric(trade_row.get("outcome_duration_sec"), errors="coerce")
    if pd.notna(dur) and float(dur) >= 0:
        return float(fill_t) + float(dur)
    if replay and replay.get("ticks"):
        ticks = replay.get("ticks") or []
        return max(float(t.get("t") or 0) for t in ticks)
    return None


def horizon_profitability_report(trades=None, session=None):
    """
    For losing trades: would the position have been profitable at +5/10/15/30m after exit?
    Answers whether early exits (fast cut, stop) are killing future winners.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    if ANALYZER_CONSOLE_VERBOSE:
        print(f"\n=== HORIZON PROFITABILITY REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    replays = _load_jsonl_replays()
    empty_horizons = {
        label: {"profitable": 0, "still_loss": 0, "unknown": 0, "coverage_pct": 0.0}
        for label in HORIZON_PROFIT_HORIZONS
    }
    if trades is None or trades.empty:
        payload = {
            "schema": "horizon_profitability_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "question": "Would losing trades have become profitable N minutes after exit?",
            "losing_trades": 0,
            "horizons": empty_horizons,
            "by_exit_reason": {},
            "trades": [],
        }
        try:
            with open(HORIZON_PROFITABILITY_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
    pnl = pd.to_numeric(work.get(pnl_col), errors="coerce").fillna(0.0)
    losers = work[pnl <= 0].copy()
    n_losers = int(len(losers))

    horizon_totals = {
        label: {"profitable": 0, "still_loss": 0, "unknown": 0}
        for label in HORIZON_PROFIT_HORIZONS
    }
    by_exit = {}
    trade_rows = []

    for _, row in losers.iterrows():
        tid = str(row.get("trade_id") or "")
        exit_reason = str(row.get("exit_reason") or "UNKNOWN")
        lev = int(row.get("leverage") or 100)
        direction = str(row.get("final_direction") or row.get("dir") or "SHORT").upper()
        exit_pnl = round(float(row.get(pnl_col) or 0), 2)
        replay = _fast_cut_trade_replay(row, replays)
        entry = pd.to_numeric(row.get("entry"), errors="coerce")
        ticks = (replay or {}).get("ticks") or []
        if replay and ticks and (pd.isna(entry) or float(entry) <= 0):
            entry = _replay_entry_price(replay)
        exit_t = _resolve_trade_exit_t(replay, row, exit_reason)
        if replay and ticks and exit_reason == "THESIS_FAST_CUT":
            fast_cut_pct = THESIS_FAST_EXIT_DEFAULT
            cut = _locate_fast_cut_exit(
                ticks, float(entry), direction, lev, fast_cut_pct,
                TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=_replay_fill_t(replay),
            )
            if cut:
                exit_t = cut["exit_t"]

        green = {}
        if ticks and exit_t is not None and pd.notna(entry) and float(entry) > 0:
            green, _mfe = _hold_through_post_cut_metrics(
                ticks, float(entry), direction, lev, exit_t, HORIZON_PROFIT_HORIZONS, {},
            )

        per_trade = {"trade_id": tid, "exit_reason": exit_reason, "exit_pnl_usd": exit_pnl}
        exit_rec = by_exit.setdefault(
            exit_reason,
            {k: {"profitable": 0, "still_loss": 0, "unknown": 0} for k in HORIZON_PROFIT_HORIZONS},
        )

        for label in HORIZON_PROFIT_HORIZONS:
            key = f"green_after_{label}"
            val = green.get(key)
            per_trade[key] = val
            if val is True:
                horizon_totals[label]["profitable"] += 1
                exit_rec[label]["profitable"] += 1
            elif val is False:
                horizon_totals[label]["still_loss"] += 1
                exit_rec[label]["still_loss"] += 1
            else:
                horizon_totals[label]["unknown"] += 1
                exit_rec[label]["unknown"] += 1
        trade_rows.append(per_trade)

    horizons_out = {}
    for label, counts in horizon_totals.items():
        known = counts["profitable"] + counts["still_loss"]
        horizons_out[label] = {
            **counts,
            "profitable_pct": round(100.0 * counts["profitable"] / known, 1) if known else None,
            "coverage_pct": round(100.0 * known / n_losers, 1) if n_losers else 0.0,
        }

    if ANALYZER_CONSOLE_VERBOSE and n_losers:
        for label, h in horizons_out.items():
            print(
                f"  {label} later: profitable={h['profitable']} still_loss={h['still_loss']} "
                f"unknown={h['unknown']} coverage={h.get('coverage_pct')}% "
                f"({h.get('profitable_pct', 'n/a')}% of known) {PIPELINE_ENFORCEMENT_TAG}"
            )

    def _recovery_block(exit_counts):
        out = {}
        for label in HORIZON_PROFIT_HORIZONS:
            counts = (exit_counts or {}).get(label) or {}
            known = int(counts.get("profitable", 0)) + int(counts.get("still_loss", 0))
            total = known + int(counts.get("unknown", 0))
            cov = round(100.0 * known / total, 1) if total else 0.0
            rate = round(100.0 * counts.get("profitable", 0) / known, 1) if known else None
            out[label] = {
                **counts,
                "coverage_pct": cov,
                "recovery_rate_pct": rate if cov >= HORIZON_MIN_COVERAGE_PCT else None,
                "conclusion_allowed": cov >= HORIZON_MIN_COVERAGE_PCT,
            }
        return out

    max_coverage = max((h.get("coverage_pct") or 0) for h in horizons_out.values()) if horizons_out else 0
    fast_cut_block = _recovery_block(by_exit.get("THESIS_FAST_CUT", {}))
    stop_block = _recovery_block(by_exit.get("STOP_LOSS", {}))

    payload = {
        "schema": "horizon_profitability_v3",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Would losing trades have become profitable N minutes after exit?",
        "method": "Tick replay at exit_t + horizon; profitable = unreal > 0 at horizon.",
        "min_coverage_pct_for_conclusions": HORIZON_MIN_COVERAGE_PCT,
        "max_horizon_coverage_pct": max_coverage,
        "conclusions_allowed": max_coverage >= HORIZON_MIN_COVERAGE_PCT,
        "losing_trades": n_losers,
        "horizons": horizons_out,
        "recovery_summary": [
            {
                "horizon": label,
                "recovery_rate_pct": (
                    h.get("profitable_pct")
                    if (h.get("coverage_pct") or 0) >= HORIZON_MIN_COVERAGE_PCT
                    else None
                ),
                "profitable": h.get("profitable", 0),
                "still_loss": h.get("still_loss", 0),
                "unknown": h.get("unknown", 0),
                "coverage_pct": h.get("coverage_pct"),
                "conclusion_allowed": (h.get("coverage_pct") or 0) >= HORIZON_MIN_COVERAGE_PCT,
            }
            for label, h in horizons_out.items()
        ],
        "fast_cut_recovery": fast_cut_block,
        "fast_cut_recovery_summary": [
            {"horizon": label, **fast_cut_block.get(label, {})}
            for label in HORIZON_PROFIT_HORIZONS
        ],
        "stop_loss_recovery": stop_block,
        "by_exit_reason": by_exit,
        "trades": trade_rows[:100],
        "note": (
            "Recovery rates hidden until coverage >= 80%. "
            "Bot v1.1.41+ collects 120m post-exit replay ticks on executed trade_ids — "
            "re-run after fresh session fills accumulate post-exit data."
            if max_coverage < HORIZON_MIN_COVERAGE_PCT
            else "Coverage sufficient for recovery conclusions."
        ),
        "coverage_reason": (
            f"0/{n_losers} losing trades have post-exit replay ticks linked to session trade_ids. "
            "Prior-session replays (scan-*, rev-*) do not overlap fresh cont-*/vc603-* fills."
            if max_coverage < HORIZON_MIN_COVERAGE_PCT and n_losers
            else None
        ),
    }
    try:
        with open(HORIZON_PROFITABILITY_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        if ANALYZER_CONSOLE_VERBOSE:
            print(f"  ✅ Wrote {HORIZON_PROFITABILITY_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        if ANALYZER_CONSOLE_VERBOSE:
            print(f"  ⚠️ Could not write {HORIZON_PROFITABILITY_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def fast_cut_survivor_report(trades=None, session=None):
    """
    THESIS_FAST_CUT survivor analysis — would trades recover / hit ladder if Scenario C stayed alive?
    Uses signal_replay tick paths (tick-accurate) with MFE fallback when replay missing.
    """
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== FAST CUT SURVIVOR REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    replays = _load_jsonl_replays()
    if trades is None or trades.empty:
        payload = {
            "schema": "fast_cut_survivor_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "expected_bot_version": EXPECTED_BOT_VERSION,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fast_cut_trades": 0,
            "summary": {},
            "data_coverage": {},
            "trades": [],
        }
        try:
            with open(FAST_CUT_SURVIVOR_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Could not write {FAST_CUT_SURVIVOR_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    if "exit_reason" not in work.columns:
        print(f"  No exit_reason column {PIPELINE_ENFORCEMENT_TAG}")
        return {}
    fc = work[work["exit_reason"].astype(str) == "THESIS_FAST_CUT"]
    n_fc = int(len(fc))
    print(f"  THESIS_FAST_CUT cohort: n={n_fc} replays={len(replays)} {PIPELINE_ENFORCEMENT_TAG}")

    green_totals = {f"green_after_{k}": 0 for k in POST_CUT_GREEN_HORIZONS}
    green_known = {f"green_after_{k}": 0 for k in POST_CUT_GREEN_HORIZONS}
    ladder_totals = {f"would_hit_ladder_{i}": 0 for i in range(1, 4)}
    ladder_cf_ladder_exit = 0
    missed_ladder_profit = 0.0
    tick_cohort = 0
    mfe_fallback = 0
    trade_rows = []

    for _, row in fc.iterrows():
        tid = str(row.get("trade_id") or "")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in row.index else "outcome_net_pnl_usd"
        exit_pnl = round(float(row.get(pnl_col) or 0), 2)
        margin = float(row.get("margin_usdt") or FLAT_MARGIN_LIVE_USD)
        lev = int(row.get("leverage") or 100)
        direction = str(row.get("final_direction") or row.get("dir") or "SHORT").upper()
        mfe_csv = pd.to_numeric(row.get("max_profit", row.get("mfe_margin_pct")), errors="coerce")
        replay = _fast_cut_trade_replay(row, replays)
        fast_cut_pct = THESIS_FAST_EXIT_DEFAULT
        for cfg_key in ("cfg_thesis_fast_exit_unreal_pct", "cfg_thesis_fast_exit_pct"):
            if cfg_key in row.index and pd.notna(row.get(cfg_key)):
                try:
                    fast_cut_pct = float(row.get(cfg_key))
                except (TypeError, ValueError):
                    pass
                break

        entry = pd.to_numeric(row.get("entry"), errors="coerce")
        fill_t = None
        ticks = []
        analysis_mode = "mfe_fallback"
        exit_t = pd.to_numeric(row.get("outcome_duration_sec"), errors="coerce")
        cut = None
        cf_reason = None
        cf_pnl = None
        if replay and replay.get("ticks"):
            ticks = replay.get("ticks") or []
            entry = _replay_entry_price(replay) if pd.isna(entry) or float(entry) <= 0 else float(entry)
            fill_t = _replay_fill_t(replay)
            analysis_mode = "tick_replay"
            tick_cohort += 1
            cut = _locate_fast_cut_exit(
                ticks,
                entry,
                direction,
                lev,
                fast_cut_pct,
                TRAIL_LADDER,
                THESIS_EXIT_ABOVE_DEFAULT,
                fill_t=fill_t,
            )
            if cut:
                exit_t = cut["exit_t"]
            cf_pnl, cf_reason, cf_peak = _simulate_ticks_fast_cut_ladder(
                ticks,
                entry,
                direction,
                lev,
                margin,
                FAST_CUT_NO_EXIT_PCT,
                TRAIL_LADDER,
                THESIS_EXIT_ABOVE_DEFAULT,
                fill_t=fill_t,
            )
            path_peak = _path_peak_after_fill(ticks, entry, direction, lev, fill_t)
            peak_for_ladder = max(
                float(cf_peak or 0),
                float(path_peak or 0),
                float(cut["peak_at_cut_pct"] if cut else 0),
            )
            green, future_mfe = _hold_through_post_cut_metrics(
                ticks,
                entry,
                direction,
                lev,
                exit_t,
                POST_CUT_GREEN_HORIZONS,
                POST_CUT_MFE_HORIZONS,
            )
            ladder_hits = _ladder_rung_hits(peak_for_ladder)
            if cf_reason == "PROFIT_LOCK_LADDER" and cf_pnl is not None and cf_pnl > exit_pnl:
                missed_ladder_profit += float(cf_pnl) - exit_pnl
                ladder_cf_ladder_exit += 1
        else:
            mfe_fallback += 1
            peak_for_ladder = float(mfe_csv) if pd.notna(mfe_csv) else 0.0
            ladder_hits = _ladder_rung_hits(peak_for_ladder)
            green = {f"green_after_{k}": None for k in POST_CUT_GREEN_HORIZONS}
            future_mfe = {f"future_mfe_{k}": None for k in POST_CUT_MFE_HORIZONS}
            future_mfe["future_max_mfe"] = round(peak_for_ladder, 2) if peak_for_ladder else None

        for k, v in green.items():
            if v is True:
                green_totals[k] += 1
            if v is not None:
                green_known[k] += 1
        for i in range(1, 4):
            key = f"would_hit_ladder_{i}"
            if ladder_hits.get(key):
                ladder_totals[key] += 1

        trade_rows.append(
            {
                "trade_id": tid,
                "analysis_mode": analysis_mode,
                "exit_pnl_usd": exit_pnl,
                "exit_unreal_pct": cut["exit_unreal_pct"] if cut else None,
                "counterfactual_exit": cf_reason,
                "counterfactual_pnl_usd": round(float(cf_pnl), 2) if cf_pnl is not None else None,
                **green,
                **future_mfe,
                **ladder_hits,
            }
        )

    summary = {
        "fast_cut_trades": n_fc,
        **{k: green_totals[k] for k in green_totals},
        **{f"{k}_coverage": green_known[k] for k in green_known},
        **ladder_totals,
        "counterfactual_ladder_exits": ladder_cf_ladder_exit,
        "missed_ladder_profit_usd": round(missed_ladder_profit, 2),
        "pct_would_hit_ladder_1": round(100.0 * ladder_totals["would_hit_ladder_1"] / n_fc, 1) if n_fc else 0.0,
        "interpretation": (
            "fast_cut_protecting"
            if n_fc and ladder_totals["would_hit_ladder_1"] / n_fc < 0.15
            else "fast_cut_destroying_winners"
            if n_fc and ladder_totals["would_hit_ladder_1"] / n_fc >= 0.40
            else "mixed"
        ),
    }

    print(
        f"  Tick replay: {tick_cohort}/{n_fc} | ladder_1 hits: {ladder_totals['would_hit_ladder_1']} "
        f"({summary['pct_would_hit_ladder_1']:.1f}%) missed_ladder=${missed_ladder_profit:.2f} "
        f"verdict={summary['interpretation']} {PIPELINE_ENFORCEMENT_TAG}"
    )
    for label in POST_CUT_GREEN_HORIZONS:
        key = f"green_after_{label}"
        cov = green_known[key]
        if cov:
            print(
                f"  {key}: {green_totals[key]}/{cov} "
                f"({round(100.0 * green_totals[key] / cov, 1)}% of tick-covered) {PIPELINE_ENFORCEMENT_TAG}"
            )

    payload = {
        "schema": "fast_cut_survivor_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Is THESIS_FAST_CUT protecting capital or destroying ladder winners?",
        "method": (
            "Tick replay: counterfactual no-cut sim + hold-through post-cut horizons on signal_replay.jsonl. "
            "MFE fallback when replay missing (ladder rungs only; post-cut green requires ticks)."
        ),
        "summary": summary,
        "data_coverage": {
            "tick_replay_trades": tick_cohort,
            "mfe_fallback_trades": mfe_fallback,
            "replay_file": SIGNAL_REPLAY_FILE,
            "note": (
                "Executed trades close replay at exit — post-cut green_after_* only resolves when replay ticks "
                "extend past exit_t + horizon (common on shadow paths; rare on executed until bot continues replay)."
            ),
        },
        "trades": trade_rows[:200],
    }
    try:
        with open(FAST_CUT_SURVIVOR_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {FAST_CUT_SURVIVOR_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {FAST_CUT_SURVIVOR_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _normalize_research_lane(val):
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    lane = str(val).strip().upper()
    if lane in ("", "NAN", "NONE", "NAT"):
        return ""
    return lane


def pathway_survival_report(trades=None, session=None):
    """Full funnel per pathway: approve → order → fill → closed → wins → PnL."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== PATHWAY SURVIVAL REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    all_rows = _load_jsonl_rows(EXECUTION_FUNNEL_FILE)
    trade_lane = {}
    for row in all_rows:
        tid = str(row.get("trade_id") or "")
        lane = _normalize_research_lane(row.get("research_lane"))
        if tid and lane:
            trade_lane[tid] = lane
    if trades is not None and not trades.empty and "research_lane" in trades.columns:
        for _, t in trades.drop_duplicates(subset=["trade_id"], keep="last").iterrows():
            tid = str(t.get("trade_id") or "")
            lane = _normalize_research_lane(t.get("research_lane"))
            if tid and lane:
                trade_lane[tid] = lane
    rows = _filter_jsonl_rows_by_session(all_rows, session)
    lanes = list(BENCHMARK_LANES)
    survival = {}
    for lane in lanes:
        survival[lane] = {
            "lane": lane,
            "label": RESEARCH_LANE_LABELS.get(lane, lane),
            "approves": 0,
            "orders": 0,
            "fills": 0,
            "closed": 0,
            "signal_expired": 0,
            "order_expired": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl_usd": 0.0,
            "win_rate_pct": 0.0,
            "approve_to_fill_pct": 0.0,
            "ev_per_fill_usd": 0.0,
        }

    for row in rows:
        tid = str(row.get("trade_id") or "")
        lane = _normalize_research_lane(row.get("research_lane")) or trade_lane.get(tid) or ""
        if not lane:
            continue
        if lane not in survival:
            survival[lane] = {
                "lane": lane,
                "label": RESEARCH_LANE_LABELS.get(lane, lane),
                "approves": 0, "orders": 0, "fills": 0, "closed": 0,
                "signal_expired": 0, "order_expired": 0,
                "wins": 0, "losses": 0, "net_pnl_usd": 0.0,
                "win_rate_pct": 0.0, "approve_to_fill_pct": 0.0, "ev_per_fill_usd": 0.0,
            }
        stage = str(row.get("stage") or "").upper()
        bucket = survival[lane]
        if stage == "APPROVE":
            bucket["approves"] += 1
        elif stage == "ORDER_SUBMITTED":
            bucket["orders"] += 1
        elif stage == "FILLED":
            bucket["fills"] += 1
        elif stage == "CLOSED":
            bucket["closed"] += 1
        elif stage == "SIGNAL_EXPIRED":
            bucket["signal_expired"] += 1
        elif stage == "ORDER_EXPIRED":
            bucket["order_expired"] += 1

    if trades is not None and not trades.empty and "research_lane" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce")
        for lane, sub in work.groupby(work["research_lane"].astype(str).str.upper(), observed=True):
            if lane not in survival:
                survival[lane] = {
                    "lane": lane,
                    "label": RESEARCH_LANE_LABELS.get(lane, lane),
                    "approves": 0, "orders": 0, "fills": 0, "closed": 0,
                    "signal_expired": 0, "order_expired": 0,
                    "wins": 0, "losses": 0, "net_pnl_usd": 0.0,
                    "win_rate_pct": 0.0, "approve_to_fill_pct": 0.0, "ev_per_fill_usd": 0.0,
                }
            survival[lane]["wins"] = int((sub[pnl_col] > 0).sum())
            survival[lane]["losses"] = int((sub[pnl_col] <= 0).sum())
            survival[lane]["net_pnl_usd"] = round(float(sub[pnl_col].sum()), 2)
            n = len(sub)
            if n:
                survival[lane]["win_rate_pct"] = round(100.0 * survival[lane]["wins"] / n, 1)

    bench_ev = None
    for lane, b in survival.items():
        if b["approves"]:
            b["approve_to_fill_pct"] = round(100.0 * b["fills"] / b["approves"], 1)
        if b["fills"]:
            b["ev_per_fill_usd"] = round(b["net_pnl_usd"] / b["fills"], 2)
        if lane == BENCHMARK_LANE and b["fills"]:
            bench_ev = b["ev_per_fill_usd"]
        if b["approves"] or b["fills"]:
            print(
                f"  {lane}: approve={b['approves']} order={b['orders']} fill={b['fills']} "
                f"closed={b['closed']} win={b['wins']} pnl=${b['net_pnl_usd']:.2f} "
                f"fill_rate={b['approve_to_fill_pct']:.1f}% {PIPELINE_ENFORCEMENT_TAG}"
            )

    payload = {
        "schema": "pathway_survival_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_lane": BENCHMARK_LANE,
        "benchmark_ev_per_fill_usd": bench_ev,
        "lanes": survival,
    }
    try:
        with open(PATHWAY_SURVIVAL_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {PATHWAY_SURVIVAL_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {PATHWAY_SURVIVAL_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


    return payload


def top_leakage_report(trades=None, session=None, top_n=50):
    """Rank trades by money left on table — WHERE Scenario C leakage comes from."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== TOP LEAKAGE REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if trades is None or trades.empty:
        payload = {
            "schema": "top_leakage_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "top_n": top_n,
            "overall_left_usd": 0.0,
            "trades": [],
        }
        try:
            with open(TOP_LEAKAGE_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
    work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0.0)
    mfe = pd.to_numeric(work.get("max_profit", work.get("mfe_margin_pct")), errors="coerce")
    final_margin = pd.to_numeric(work.get("pnl", work.get("final_pnl_margin_pct")), errors="coerce")
    margin_usd = pd.to_numeric(work.get("margin_usdt", FLAT_MARGIN_LIVE_USD), errors="coerce").fillna(FLAT_MARGIN_LIVE_USD)
    peak_usd = (mfe / 100.0) * margin_usd
    booked_usd = work[pnl_col]
    left_usd = (peak_usd - (final_margin / 100.0) * margin_usd).clip(lower=0)

    rows = []
    for _, row in work.iterrows():
        peak = float(peak_usd.loc[row.name]) if row.name in peak_usd.index else 0.0
        booked = float(booked_usd.loc[row.name])
        left = float(left_usd.loc[row.name]) if row.name in left_usd.index else 0.0
        mfe_pct = float(mfe.loc[row.name]) if pd.notna(mfe.loc[row.name]) else None
        final_pct = float(final_margin.loc[row.name]) if pd.notna(final_margin.loc[row.name]) else None
        leak_pct = round(mfe_pct - final_pct, 2) if mfe_pct is not None and final_pct is not None else None
        if left <= 0.01 and peak <= 0.01:
            continue
        rows.append({
            "trade_id": str(row.get("trade_id") or ""),
            "lane": row.get("research_lane") or row.get("lane"),
            "direction": row.get("final_direction") or row.get("dir"),
            "exit_reason": str(row.get("exit_reason") or ""),
            "realized_usd": round(booked, 2),
            "peak_profit_usd": round(peak, 2),
            "left_on_table_usd": round(left, 2),
            "capture_pct": round(100.0 * booked / peak, 1) if peak > 0 else None,
            "mfe_margin_pct": round(mfe_pct, 2) if mfe_pct is not None else None,
            "realized_margin_pct": round(final_pct, 2) if final_pct is not None else None,
            "leakage_margin_pct": leak_pct,
        })
    rows.sort(key=lambda x: -x["left_on_table_usd"])
    top_rows = rows[:top_n]
    by_exit = {}
    for r in rows:
        ex = r["exit_reason"] or "UNKNOWN"
        by_exit.setdefault(ex, {"trades": 0, "left_usd": 0.0})
        by_exit[ex]["trades"] += 1
        by_exit[ex]["left_usd"] = round(by_exit[ex]["left_usd"] + r["left_on_table_usd"], 2)

    overall_left = round(sum(r["left_on_table_usd"] for r in rows), 2)
    top_leak = top_rows[0]["left_on_table_usd"] if top_rows else 0.0
    print(f"  Overall left: ${overall_left:.2f} | top trade left: ${top_leak:.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if top_rows:
        t0 = top_rows[0]
        print(
            f"  Worst leak: {t0['trade_id'][:12]}… {t0['exit_reason']} "
            f"realized=${t0['realized_usd']:.2f} peak=${t0['peak_profit_usd']:.2f} "
            f"left=${t0['left_on_table_usd']:.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "top_leakage_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Which trades left the most profit on the table?",
        "top_n": top_n,
        "overall_left_usd": overall_left,
        "by_exit_reason": by_exit,
        "trades": top_rows,
    }
    try:
        with open(TOP_LEAKAGE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {TOP_LEAKAGE_REPORT_FILE} ({len(top_rows)} trades) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {TOP_LEAKAGE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _pathway_lane_status(lane_key: str) -> str:
    return PATHWAY_LANE_STATUS.get(str(lane_key or "").upper(), "ACTIVE")


def _lane_entry_conditions(spec: dict) -> list:
    entry = spec.get("entry") or {}
    conditions = []
    trigger = entry.get("trigger")
    if trigger:
        conditions.append(str(trigger))
    for line in spec.get("diff_vs_benchmark") or []:
        conditions.append(str(line))
    return conditions


def _lane_depends_on_edge(lane_key: str, spec: dict) -> bool:
    if lane_key in RETIRED_PATHWAY_LANES:
        return True
    if lane_key in ("URGENT_CHASE_ALPHA", "CHASE_3PLUS_ALPHA"):
        return False
    text = " ".join(_lane_entry_conditions(spec)).lower()
    return any(m in text for m in ("edge≥", "edge>=", "edge >=", "edge≥3", "edge>=3", "edge>=4"))


def _lane_depends_on_ai(lane_key: str, spec: dict) -> bool:
    if lane_key == "SHADOW_RUNNER":
        return False
    text = " ".join(_lane_entry_conditions(spec)).lower()
    return "ai" in text or lane_key in ("CONTINUOUS", "AI_60_65_ALPHA", "TYPE_B_HUNTER", "SHORT_BEAR_ALPHA")


def _lane_depends_on_chase(lane_key: str, spec: dict) -> bool:
    if lane_key in ("URGENT_CHASE_ALPHA", "CHASE_3PLUS_ALPHA"):
        return True
    entry = spec.get("entry") or {}
    blob = " ".join([
        str(entry.get("fill_path") or ""),
        str(entry.get("execution") or ""),
        " ".join(spec.get("diff_vs_benchmark") or []),
    ]).lower()
    return "chase" in blob or lane_key == "CONTINUOUS"


def lane_definition_report(trades=None, session=None, benchmark_report=None):
    """Structured inventory — what each lane actually tests (names may drift from logic)."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== LANE DEFINITION REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if benchmark_report is None and os.path.isfile(BENCHMARK_VS_LANES_REPORT_FILE):
        benchmark_report = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    lanes_metrics = (benchmark_report or {}).get("lanes") or {}
    static = _static_pathway_lane_specs()
    rows = []
    for lane_key in BENCHMARK_LANES:
        spec = static.get(lane_key) or {}
        metrics = lanes_metrics.get(lane_key) or {}
        all_time = metrics.get("all_time") or {}
        fills = int(metrics.get("real_fills") or metrics.get("fills") or 0)
        approves = int(metrics.get("approves") or 0)
        pnl = float(metrics.get("net_pnl_real") or metrics.get("net_pnl_usd") or 0)
        ev = float(metrics.get("per_approve_ev") or 0)
        at_fills = int(all_time.get("real_fills") or 0)
        at_pnl = float(all_time.get("net_pnl_real") or 0)
        rows.append({
            "lane": lane_key,
            "label": RESEARCH_LANE_LABELS.get(lane_key, lane_key),
            "pathway_status": _pathway_lane_status(lane_key),
            "entry_conditions": _lane_entry_conditions(spec),
            "depends_on_edge": _lane_depends_on_edge(lane_key, spec),
            "depends_on_ai": _lane_depends_on_ai(lane_key, spec),
            "depends_on_chase": _lane_depends_on_chase(lane_key, spec),
            "sample_size": fills,
            "approves": approves,
            "pnl_usd": round(pnl, 2),
            "ev_per_approve": round(ev, 2),
            "all_time_fills": at_fills,
            "all_time_pnl_usd": round(at_pnl, 2),
            "role": spec.get("role"),
            "research_question": spec.get("research_question"),
        })
        print(
            f"  {lane_key} [{_pathway_lane_status(lane_key)}]: edge={_lane_depends_on_edge(lane_key, spec)} "
            f"ai={_lane_depends_on_ai(lane_key, spec)} chase={_lane_depends_on_chase(lane_key, spec)} "
            f"n={fills} PnL=${pnl:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )
    payload = {
        "schema": "lane_definition_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_roster": list(ACTIVE_PATHWAY_LANES),
        "retired_lanes": sorted(RETIRED_PATHWAY_LANES),
        "probation_lanes": [ln for ln, st in PATHWAY_LANE_STATUS.items() if st == "PROBATION"],
        "lanes": rows,
    }
    try:
        with open(LANE_DEFINITION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {LANE_DEFINITION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {LANE_DEFINITION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def urgent_chase_report(trades=None, session=None, benchmark_report=None, chase_payload=None):
    """URGENT_CHASE_ALPHA vs CONTINUOUS — velocity-aware chase experiment metrics."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== URGENT CHASE REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if benchmark_report is None and os.path.isfile(BENCHMARK_VS_LANES_REPORT_FILE):
        benchmark_report = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    lanes = (benchmark_report or {}).get("lanes") or {}
    bench = lanes.get("CONTINUOUS") or {}
    urgent = lanes.get("URGENT_CHASE_ALPHA") or {}

    def _lane_block(m):
        fills = int(m.get("real_fills") or m.get("fills") or 0)
        approves = int(m.get("approves") or 0)
        pnl = float(m.get("net_pnl_real") or m.get("net_pnl_usd") or 0)
        ev = float(m.get("per_approve_ev") or 0)
        fill_pct = float(m.get("approve_to_fill_pct") or 0)
        return {
            "fills": fills,
            "approves": approves,
            "fill_pct": round(fill_pct, 1),
            "pnl_usd": round(pnl, 2),
            "ev_per_approve": round(ev, 2),
        }

    bench_block = _lane_block(bench)
    urgent_block = _lane_block(urgent)

    if chase_payload is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        chase_payload = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    attrs = (chase_payload or {}).get("attributions") or (chase_payload or {}).get("trades") or []
    chase_assisted = 0
    ttl_prevented = 0
    for a in attrs:
        if str(a.get("research_lane") or "").upper() != "URGENT_CHASE_ALPHA":
            continue
        cc = int(a.get("chase_count") or a.get("limit_chase_count") or 0)
        if cc > 0 or a.get("filled_after_chase"):
            chase_assisted += 1
        if a.get("ttl_saved") or a.get("chase_prevented_ttl"):
            ttl_prevented += 1

    delta_ev = round(urgent_block["ev_per_approve"] - bench_block["ev_per_approve"], 2)
    delta_pnl = round(urgent_block["pnl_usd"] - bench_block["pnl_usd"], 2)
    delta_fill = round(urgent_block["fill_pct"] - bench_block["fill_pct"], 1)
    adequate = urgent_block["fills"] >= MIN_LANE_FILLS_FOR_RETIREMENT
    beats_bench = (
        urgent_block["ev_per_approve"] > bench_block["ev_per_approve"]
        or urgent_block["pnl_usd"] > bench_block["pnl_usd"]
    )

    forensics = {}
    if trades is not None and not trades.empty and "research_lane" in trades.columns:
        work = _enrich_trades_with_buckets(trades.copy())
        chase_by_tid = _chase_attr_by_trade_id(chase_payload)
        for lane_key, label in (("CONTINUOUS", "continuous"), ("URGENT_CHASE_ALPHA", "urgent")):
            sub = work[work["research_lane"].astype(str).str.upper() == lane_key]
            if sub.empty:
                continue
            stats = _combo_stats_from_df(sub)
            mfe = pd.to_numeric(sub.get("max_profit", sub.get("mfe_margin_pct")), errors="coerce")
            mae = pd.to_numeric(sub.get("max_drawdown", sub.get("mae_margin_pct")), errors="coerce")
            sa = pd.to_numeric(sub.get("signal_age_sec", sub.get("execution_fill_delay_sec")), errors="coerce")
            slip = pd.to_numeric(sub.get("slippage"), errors="coerce")
            chase_n = []
            for _, row in sub.iterrows():
                tid = str(row.get("trade_id") or "")
                cc = chase_by_tid.get(tid, {}).get("chase_count")
                if cc is None and "limit_chase_count" in row.index:
                    cc = row.get("limit_chase_count")
                try:
                    chase_n.append(int(cc or 0))
                except (TypeError, ValueError):
                    chase_n.append(0)
            forensics[label] = {
                **stats,
                "avg_mfe_margin_pct": round(float(mfe.mean()), 2) if mfe.notna().any() else None,
                "avg_mae_margin_pct": round(float(mae.mean()), 2) if mae.notna().any() else None,
                "avg_signal_age_sec": round(float(sa.mean()), 1) if sa.notna().any() else None,
                "avg_entry_slippage_usd": round(float(slip.mean()), 2) if slip.notna().any() else None,
                "avg_chase_count": round(float(np.mean(chase_n)), 2) if chase_n else 0.0,
            }
        if forensics.get("continuous") and forensics.get("urgent"):
            fc, fu = forensics["continuous"], forensics["urgent"]
            forensics["delta"] = {
                "ev_usd": round(fu.get("ev_usd", 0) - fc.get("ev_usd", 0), 2),
                "wr_pct": round(fu.get("wr_pct", 0) - fc.get("wr_pct", 0), 1),
                "avg_signal_age_sec": (
                    round(fu.get("avg_signal_age_sec", 0) - fc.get("avg_signal_age_sec", 0), 1)
                    if fu.get("avg_signal_age_sec") is not None and fc.get("avg_signal_age_sec") is not None
                    else None
                ),
                "avg_chase_count": round(fu.get("avg_chase_count", 0) - fc.get("avg_chase_count", 0), 2),
                "avg_mfe_margin_pct": (
                    round(fu.get("avg_mfe_margin_pct", 0) - fc.get("avg_mfe_margin_pct", 0), 2)
                    if fu.get("avg_mfe_margin_pct") is not None and fc.get("avg_mfe_margin_pct") is not None
                    else None
                ),
            }
            forensics["question"] = "Did faster URGENT fill improve MFE? Compare avg_mfe_margin_pct."
    if urgent_block["fills"] == 0:
        verdict = "COLLECTING"
    elif adequate and beats_bench:
        verdict = "PROMISING"
    elif adequate and not beats_bench:
        verdict = "RETIRE_CANDIDATE"
    else:
        verdict = "INSUFFICIENT_SAMPLE"

    payload = {
        "schema": "urgent_chase_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_lane": "URGENT_CHASE_ALPHA",
        "benchmark_lane": "CONTINUOUS",
        "purpose": "Test whether velocity-aware chase improves benchmark performance",
        "chase_tiers": {
            "normal": "25% gap closure",
            "medium": "50% gap closure",
            "high": "75% gap closure",
            "extreme": "immediate marketable limit",
        },
        "urgent_chase_alpha": {
            **urgent_block,
            "chase_assisted_fills": chase_assisted,
            "ttl_prevented": ttl_prevented,
        },
        "continuous_benchmark": bench_block,
        "benchmark_delta": {
            "delta_ev_per_approve": delta_ev,
            "delta_pnl_usd": delta_pnl,
            "delta_fill_pct": delta_fill,
        },
        "forensics": forensics,
        "success_criteria": "Must outperform CONTINUOUS on EV or net PnL after adequate sample",
        "verdict": verdict,
        "min_fills_for_decision": MIN_LANE_FILLS_FOR_RETIREMENT,
    }
    print(
        f"  URGENT: fills={urgent_block['fills']} EV=${urgent_block['ev_per_approve']:+.2f} "
        f"PnL=${urgent_block['pnl_usd']:+.2f} | BENCH: EV=${bench_block['ev_per_approve']:+.2f} "
        f"ΔEV=${delta_ev:+.2f} verdict={verdict} {PIPELINE_ENFORCEMENT_TAG}"
    )
    try:
        with open(URGENT_CHASE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {URGENT_CHASE_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {URGENT_CHASE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _combo_stats_from_df(sub: pd.DataFrame) -> dict:
    if sub is None or sub.empty:
        return {"trades": 0, "wins": 0, "wr_pct": 0.0, "pnl_usd": 0.0, "ev_usd": 0.0}
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in sub.columns else "outcome_net_pnl_usd"
    pnl = pd.to_numeric(sub.get(pnl_col, 0), errors="coerce").fillna(0)
    wins = int((pnl > 0).sum())
    n = len(sub)
    total = round(float(pnl.sum()), 2)
    return {
        "trades": n,
        "wins": wins,
        "wr_pct": round(100.0 * wins / n, 1) if n else 0.0,
        "pnl_usd": total,
        "ev_usd": round(total / n, 2) if n else 0.0,
    }


def _trade_mfe_type_series(work: pd.DataFrame) -> pd.Series:
    mfe = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce").fillna(0)
    return pd.Series(
        np.where(mfe >= 15, "TYPE_B", np.where(mfe < 10, "TYPE_A", "MIXED")),
        index=work.index,
    )


def _chase_attr_by_trade_id(chase_payload) -> dict:
    out = {}
    for row in (chase_payload or {}).get("trades") or []:
        tid = str(row.get("trade_id") or "")
        if tid:
            out[tid] = row
    return out


def lane_chase_isolation_report(trades=None, session=None, chase_payload=None):
    """COMBO Direct vs Chase 3+ — fill_model and chase policy per combo tile pair."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== LANE CHASE ISOLATION — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if chase_payload is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        chase_payload = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    if chase_payload is None:
        chase_payload = chase_attribution_report(trades=trades, session=session)

    work = _enrich_trades_with_buckets(trades.copy()) if trades is not None and not trades.empty else pd.DataFrame()
    chase_by_tid = _chase_attr_by_trade_id(chase_payload)

    def _lane_audit(lane_key: str) -> dict:
        sub = work[work["research_lane"].astype(str).str.upper() == lane_key] if not work.empty and "research_lane" in work.columns else pd.DataFrame()
        stats = _combo_stats_from_df(sub)
        fill_models = sub["fill_model"].value_counts().to_dict() if not sub.empty and "fill_model" in sub.columns else {}
        entry_paths = sub["entry_path"].value_counts().to_dict() if not sub.empty and "entry_path" in sub.columns else {}
        chase_counts = []
        for _, row in sub.iterrows():
            tid = str(row.get("trade_id") or "")
            cc = chase_by_tid.get(tid, {}).get("chase_count")
            if cc is None and "limit_chase_count" in row.index:
                cc = row.get("limit_chase_count")
            try:
                chase_counts.append(int(cc or 0))
            except (TypeError, ValueError):
                chase_counts.append(0)
        avg_chase = round(float(np.mean(chase_counts)), 2) if chase_counts else 0.0
        avg_signal_age = None
        if not sub.empty and "signal_age_sec" in sub.columns:
            sa = pd.to_numeric(sub["signal_age_sec"], errors="coerce").dropna()
            if len(sa):
                avg_signal_age = round(float(sa.mean()), 1)
        avg_slip = None
        if not sub.empty and "slippage" in sub.columns:
            sl = pd.to_numeric(sub["slippage"], errors="coerce").dropna()
            if len(sl):
                avg_slip = round(float(sl.mean()), 2)
        chase_assisted = sum(1 for c in chase_counts if c > 0)
        static_fills = len(chase_counts) - chase_assisted
        return {
            **stats,
            "fill_model": fill_models,
            "entry_path": entry_paths,
            "chase_assisted_fills": chase_assisted,
            "static_limit_fills": static_fills,
            "avg_chase_count": avg_chase,
            "avg_signal_age_sec": avg_signal_age,
            "avg_entry_slippage_usd": avg_slip,
        }

    global_fill = work["fill_model"].value_counts().to_dict() if not work.empty and "fill_model" in work.columns else {}
    pairs_out = []
    isolation_pairs = list(ACTIVE_CHASE_ISOLATION_PAIRS) + list(COMBO_CHASE_ISOLATION_PAIRS)

    for direct_lane, chase_lane in isolation_pairs:
        direct_a = _lane_audit(direct_lane)
        chase_a = _lane_audit(chase_lane)
        combo_retired = bool(COMBO_LANE_SPECS.get(direct_lane, {}).get("is_legacy"))
        session_inactive = (direct_a.get("trades") or 0) == 0 and (chase_a.get("trades") or 0) == 0
        pairs_out.append({
            "direct_lane": direct_lane,
            "chase_lane": chase_lane,
            "direct_label": RESEARCH_LANE_LABELS.get(direct_lane, direct_lane),
            "chase_label": RESEARCH_LANE_LABELS.get(chase_lane, chase_lane),
            "combo_retired": combo_retired,
            "session_inactive": session_inactive,
            "direct": {
                **direct_a,
                "chase_policy": "immediate limit (COMBO Direct)" if combo_retired else "CONTINUOUS immediate limit",
                "virtual_chase_gate": False,
            },
            "chase": {
                **chase_a,
                "chase_policy": (
                    "virtual chase 3+ gate before fill"
                    if combo_retired
                    else "virtual chase 3+ (AI60 SP3 tile)"
                ),
                "virtual_chase_gate": True,
            },
            "delta": {
                "ev_usd": round(chase_a.get("ev_usd", 0) - direct_a.get("ev_usd", 0), 2),
                "wr_pct": round(chase_a.get("wr_pct", 0) - direct_a.get("wr_pct", 0), 1),
                "avg_chase_count": round(chase_a.get("avg_chase_count", 0) - direct_a.get("avg_chase_count", 0), 2),
                "avg_signal_age_sec": (
                    round(chase_a.get("avg_signal_age_sec", 0) - direct_a.get("avg_signal_age_sec", 0), 1)
                    if chase_a.get("avg_signal_age_sec") is not None and direct_a.get("avg_signal_age_sec") is not None
                    else None
                ),
            },
        })
        print(
            f"  {direct_lane} vs {chase_lane}: direct_n={direct_a.get('trades')} chase_n={chase_a.get('trades')} "
            f"inactive={session_inactive} {PIPELINE_ENFORCEMENT_TAG}"
        )

    active_pairs = [p for p in pairs_out if not p.get("session_inactive")]
    primary = active_pairs[0] if active_pairs else (pairs_out[0] if pairs_out else {})
    direct_primary = primary.get("direct") or {}
    chase_primary = primary.get("chase") or {}
    isolated = True
    notes = []
    if primary.get("session_inactive"):
        notes.append(
            "COMBO tiles inactive this session (retired 2026-06-26) — compare CONTINUOUS vs AI60_SP3 instead."
        )
    else:
        notes.append(
            f"Primary pair: {primary.get('direct_label')} vs {primary.get('chase_label')} "
            f"(direct n={direct_primary.get('trades', 0)}, chase n={chase_primary.get('trades', 0)})."
        )
    notes.extend([
        "COMBO Direct lanes use immediate limit entry; COMBO Chase lanes require virtual chase 3+ (or age/dist) before fill.",
        "fill_model=AI_DIRECT_CHASE tags limit_chase_count>0 on AI_DIRECT path — expected on both when chase steps fire.",
        "Global fill_model counts all session fills — not limited to the primary isolation pair.",
    ])
    if direct_primary.get("fill_model") and chase_primary.get("fill_model"):
        if direct_primary["fill_model"].keys() == chase_primary["fill_model"].keys():
            notes.append("Primary pair fill_model keys match — parallel tagging, not cross-lane contamination.")

    payload = {
        "schema": "lane_chase_isolation_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "ISOLATED" if isolated else "CONTAMINATED",
        "isolation_notes": notes,
        "global_fill_model": global_fill,
        "active_lanes": list(ACTIVE_CHASE_ISOLATION_LANES),
        "pairs": pairs_out,
        "primary_pair": primary,
        "primary_inactive": bool(primary.get("session_inactive")),
        "benchmark_lane": BENCHMARK_LANE,
        "continuous_benchmark": direct_primary,
        "urgent_chase_alpha": chase_primary,
        "benchmark_delta": primary.get("delta") or {},
    }
    print(
        f"  Primary {primary.get('direct_lane')} vs {primary.get('chase_lane')} "
        f"verdict={payload['verdict']} {PIPELINE_ENFORCEMENT_TAG}"
    )
    try:
        with open(LANE_CHASE_ISOLATION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {LANE_CHASE_ISOLATION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {LANE_CHASE_ISOLATION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def top_combinations_report(trades=None, session=None, min_trades=3, top_n=100):
    """Rank AI × spread × type × lane cohorts — top and bottom performers."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== TOP COMBINATIONS — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    work = _enrich_trades_with_buckets(trades.copy()) if trades is not None and not trades.empty else pd.DataFrame()
    if work.empty:
        print(f"  No trades for combination heatmap. {PIPELINE_ENFORCEMENT_TAG}")
        payload = {"schema": "top_combinations_v1", "top": [], "bottom": [], "session_scope": scope}
        with open(analyzer_report_path(TOP_COMBINATIONS_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    combos = []
    dims = ["ai_probability_bucket", "directional_spread_bucket", "entry_mode_bucket", "research_lane"]
    for keys, sub in work.groupby(dims, observed=True, dropna=False):
        ai_b, spread_b, entry_b, lane = keys
        stats = _combo_stats_from_df(sub)
        if stats["trades"] < min_trades:
            continue
        combo_label = f"AI{ai_b}+SPREAD{spread_b}+{entry_b}+{lane}"
        combos.append({
            "combo": combo_label,
            "ai_bucket": ai_b,
            "spread_bucket": spread_b,
            "entry_mode": entry_b,
            "lane": str(lane).upper(),
            **stats,
        })
    combos.sort(key=lambda x: (x["ev_usd"], x["pnl_usd"]), reverse=True)
    top = combos[:top_n]
    bottom = list(reversed(combos[-top_n:])) if len(combos) > top_n else list(reversed(combos))
    for row in top[:8]:
        print(
            f"  TOP {row['combo']}: n={row['trades']} WR={row['wr_pct']:.1f}% "
            f"EV=${row['ev_usd']:+.2f} PnL=${row['pnl_usd']:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )
    payload = {
        "schema": "top_combinations_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_trades_per_combo": min_trades,
        "dimensions": ["ai_probability_bucket", "directional_spread_bucket", "entry_mode_bucket", "research_lane"],
        "total_combos": len(combos),
        "top": top,
        "bottom": bottom,
    }
    try:
        with open(analyzer_report_path(TOP_COMBINATIONS_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {TOP_COMBINATIONS_REPORT_FILE} ({len(combos)} combos) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {TOP_COMBINATIONS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def exit_combinations_report(trades=None, session=None, min_trades=3, top_n=80):
    """Exit × entry combo cohorts — find leakage (left on table) and best exit paths."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EXIT COMBINATIONS — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    work = _enrich_trades_with_buckets(trades.copy()) if trades is not None and not trades.empty else pd.DataFrame()
    if work.empty:
        payload = {"schema": "exit_combinations_v1", "top": [], "worst_leakage": [], "session_scope": scope}
        with open(analyzer_report_path(EXIT_COMBINATIONS_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    work["trade_mfe_type"] = _trade_mfe_type_series(work)
    if "exit_reason" not in work.columns:
        work["exit_reason"] = "UNKNOWN"
    work["exit_reason"] = work["exit_reason"].fillna("UNKNOWN").astype(str)
    lot_col = None
    for c in ("profit_left_on_table", "left_on_table_usd", "left_on_table"):
        if c in work.columns:
            lot_col = c
            break
    if lot_col:
        work["left_on_table_usd"] = pd.to_numeric(work[lot_col], errors="coerce").fillna(0)
    else:
        peak = pd.to_numeric(work.get("mfe_margin_pct", work.get("max_profit")), errors="coerce").fillna(0)
        booked = pd.to_numeric(work.get("net_pnl_usd", 0), errors="coerce").fillna(0)
        work["left_on_table_usd"] = (peak - booked).clip(lower=0)

    dims = [
        "exit_reason",
        "ai_probability_bucket",
        "directional_spread_bucket",
        "peak_mfe_bucket",
        "time_in_trade_bucket",
        "trade_mfe_type",
        "research_lane",
    ]
    combos = []
    for keys, sub in work.groupby(dims, observed=True, dropna=False):
        ex, ai_b, sp_b, mfe_b, time_b, ttype, lane = keys
        if str(ttype).upper() == "TYPE_B":
            continue
        stats = _combo_stats_from_df(sub)
        if stats["trades"] < min_trades:
            continue
        left = round(float(sub["left_on_table_usd"].sum()), 2)
        avg_left = round(float(sub["left_on_table_usd"].mean()), 2)
        combos.append({
            "combo": f"EXIT_{ex}+AI{ai_b}+SPREAD{sp_b}+MFE{mfe_b}+TIME{time_b}+{ttype}+{str(lane).upper()}",
            "exit_reason": ex,
            "ai_bucket": ai_b,
            "spread_bucket": sp_b,
            "peak_mfe_bucket": mfe_b,
            "time_in_trade_bucket": time_b,
            "type": ttype,
            "lane": str(lane).upper(),
            "left_on_table_usd": left,
            "avg_left_usd": avg_left,
            **stats,
        })
    by_ev = sorted(combos, key=lambda x: (x["ev_usd"], x["pnl_usd"]), reverse=True)
    by_leak = sorted(combos, key=lambda x: (x["left_on_table_usd"], -x["ev_usd"]), reverse=True)
    top = by_ev[:top_n]
    worst_leak = by_leak[:top_n]
    for row in top[:6]:
        print(
            f"  TOP {row['combo']}: n={row['trades']} EV=${row['ev_usd']:+.2f} "
            f"left=${row['left_on_table_usd']:+.0f} {PIPELINE_ENFORCEMENT_TAG}"
        )
    payload = {
        "schema": "exit_combinations_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_lane": BENCHMARK_LANE,
        "min_trades_per_combo": min_trades,
        "dimensions": dims,
        "total_combos": len(combos),
        "overall_left_on_table_usd": round(float(work["left_on_table_usd"].sum()), 2),
        "filter_note": "TYPE_B excluded — not predictable enough for exit combo optimization.",
        "top": top,
        "worst_leakage": worst_leak,
    }
    try:
        with open(analyzer_report_path(EXIT_COMBINATIONS_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EXIT_COMBINATIONS_REPORT_FILE} ({len(combos)} exit combos) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EXIT_COMBINATIONS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


EXIT_LEAK_ACTION_MAP = {
    "PROFIT_LOCK_LADDER": {
        "action": "Tighten ladder rungs — raise lock floors on early rungs (12→8 → 12→10) to capture more peak.",
        "script_hint": "Review cfg_trail_ladder_json / TRAIL_LADDER in combo_pathway_config.py",
        "priority": "high",
    },
    "STOP_LOSS": {
        "action": "Block or widen hard stop path on chase-assisted entries — largest leak bucket after ladder.",
        "script_hint": "Audit STOP_LOSS triggers in bot exit ladder; consider thesis_mfe_protect before hard SL.",
        "priority": "high",
    },
    "THESIS_FAST_CUT": {
        "action": "Raise thesis fast-cut threshold or enable MFE-protect on runners showing >10% peak.",
        "script_hint": "Tune cfg_thesis_fast_exit_unreal_pct / cfg_thesis_mfe_protect_pct per lane.",
        "priority": "medium",
    },
    "EARLY_FAIL": {
        "action": "Tighten early-fail gate — trades dying before ladder engagement.",
        "script_hint": "Review cfg_early_fail_pct_threshold and cfg_type_a_early_fail_enabled.",
        "priority": "medium",
    },
    "THESIS_INVALIDATED": {
        "action": "Review thesis-invalidation score flip margin — may be cutting recoverable runners.",
        "script_hint": "Tune cfg_thesis_score_flip_margin / cfg_thesis_min_age_sec.",
        "priority": "medium",
    },
    "TIME_EXIT": {
        "action": "Extend TTL or add late-stage ladder rung before TIME_EXIT fires.",
        "script_hint": "Review pending order TTL and time-based exit config.",
        "priority": "low",
    },
}


def _exit_leak_recommendations(reasons: list) -> list:
    """Finding → Recommendation → Expected gain per exit leak source."""
    recs = []
    for row in reasons or []:
        reason = str(row.get("exit_reason") or "")
        template = EXIT_LEAK_ACTION_MAP.get(reason)
        if not template:
            continue
        left = float(row.get("left_on_table_usd") or 0)
        n = int(row.get("trades") or 0)
        avg_left = float(row.get("avg_left_usd") or 0)
        capture = float(row.get("capture_ratio_pct") or 0)
        finding = (
            f"{reason} on {n} trades left ${left:.0f} on table "
            f"(avg ${avg_left:.2f}/trade, {capture:.0f}% capture)."
        )
        expected_gain = (
            f"Recover ~10–25% of leaked value (${left * 0.1:.0f}–${left * 0.25:.0f}) "
            f"if {reason} exits tighten by one ladder rung or delayed trigger."
        )
        recs.append({
            "exit_reason": reason,
            "trades": n,
            "left_on_table_usd": left,
            "priority": template["priority"],
            "finding": finding,
            "recommendation": template["action"],
            "expected_gain": expected_gain,
            "action": template["action"],
            "script_hint": template["script_hint"],
        })
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: (order.get(r.get("priority"), 9), -(r.get("left_on_table_usd") or 0)))
    return recs


def exit_leakage_by_reason_report(trades=None, session=None):
    """Aggregate money left on table by exit reason — which exit path leaks most."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EXIT LEAKAGE BY REASON — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if trades is None or trades.empty:
        payload = {
            "schema": "exit_leakage_by_reason_v1",
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall_left_usd": 0.0,
            "reasons": [],
        }
        with open(EXIT_LEAKAGE_BY_REASON_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
    work[pnl_col] = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0.0)
    mfe = pd.to_numeric(work.get("max_profit", work.get("mfe_margin_pct")), errors="coerce")
    final_margin = pd.to_numeric(work.get("pnl", work.get("final_pnl_margin_pct")), errors="coerce")
    margin_usd = pd.to_numeric(work.get("margin_usdt", FLAT_MARGIN_LIVE_USD), errors="coerce").fillna(FLAT_MARGIN_LIVE_USD)
    peak_usd = (mfe / 100.0) * margin_usd
    booked_usd = work[pnl_col]
    left_usd = (peak_usd - (final_margin / 100.0) * margin_usd).clip(lower=0)
    capture = (booked_usd / peak_usd.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    work = work.assign(_left=left_usd, _peak=peak_usd, _booked=booked_usd, _capture=capture)
    if "exit_reason" not in work.columns:
        work["exit_reason"] = "UNKNOWN"
    work["exit_reason"] = work["exit_reason"].fillna("UNKNOWN").astype(str)

    reasons = []
    for reason, sub in work.groupby("exit_reason", observed=True):
        if sub.empty:
            continue
        n = int(len(sub))
        left_sum = round(float(sub["_left"].sum()), 2)
        avg_left = round(float(sub["_left"].mean()), 2)
        avg_mfe = round(float(mfe.loc[sub.index].mean()), 2) if mfe.loc[sub.index].notna().any() else None
        avg_realized = round(float(final_margin.loc[sub.index].mean()), 2) if final_margin.loc[sub.index].notna().any() else None
        avg_leak_pct = round(float((mfe.loc[sub.index] - final_margin.loc[sub.index]).mean()), 2) if mfe.loc[sub.index].notna().any() else None
        reasons.append({
            "exit_reason": str(reason),
            "trades": n,
            "left_on_table_usd": left_sum,
            "avg_left_usd": avg_left,
            "avg_mfe_margin_pct": avg_mfe,
            "avg_realized_margin_pct": avg_realized,
            "avg_leakage_margin_pct": avg_leak_pct,
            "booked_profit_usd": round(float(sub["_booked"].sum()), 2),
            "peak_profit_usd": round(float(sub["_peak"].sum()), 2),
            "capture_ratio_pct": round(float(sub["_capture"].mean(skipna=True) * 100), 1)
            if sub["_capture"].notna().any() else 0.0,
        })
    reasons.sort(key=lambda x: (-x["left_on_table_usd"], -x["trades"]))
    overall_left = round(float(left_usd.sum()), 2)
    for row in reasons[:6]:
        print(
            f"  {row['exit_reason']}: n={row['trades']} left=${row['left_on_table_usd']:.2f} "
            f"avg_leak={row['avg_leakage_margin_pct']}% {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "exit_leakage_by_reason_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_left_usd": overall_left,
        "overall_booked_usd": round(float(booked_usd.sum()), 2),
        "overall_peak_usd": round(float(peak_usd.sum()), 2),
        "reasons": reasons,
        "recommendations": _exit_leak_recommendations(reasons),
    }
    try:
        with open(EXIT_LEAKAGE_BY_REASON_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EXIT_LEAKAGE_BY_REASON_REPORT_FILE} ({len(reasons)} reasons) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EXIT_LEAKAGE_BY_REASON_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


LADDER_SIM_PROFILES = {
    "current_live": {
        "label": f"Current Scenario C ({SCENARIO_C_LADDER_LABEL})",
        "ladder": list(TRAIL_LADDER),
    },
    "legacy_12_8": {
        "label": "Legacy Scenario C (12→8 · 15→10 · 25→18 · 40→28)",
        "ladder": [(12, 8), (15, 10), (25, 18), (40, 28)],
    },
    "extended_upper": {
        "label": "Legacy early + extended upper (12→8 … 150→120)",
        "ladder": [(12, 8), (15, 10), (25, 18), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120)],
    },
    "relaxed_early_extended": {
        "label": f"Relaxed early + extended ({SCENARIO_C_LADDER_LABEL})",
        "ladder": list(TRAIL_LADDER),
    },
    "profile_30": {
        "label": "30→20 · 40→30 · 50→40 · 60→50",
        "ladder": [(30, 20), (40, 30), (50, 40), (60, 50)],
    },
    "profile_40": {
        "label": "40→25 · 60→40 · 80→60",
        "ladder": [(40, 25), (60, 40), (80, 60)],
    },
    "profile_50": {
        "label": "50→35 · 75→50 · 100→70",
        "ladder": [(50, 35), (75, 50), (100, 70)],
    },
}


def exit_ladder_simulator_report(trades=None, session=None):
    """Replay tick paths with alternate ladder rungs — data-driven exit optimization."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EXIT LADDER SIMULATOR — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")

    replays = _load_jsonl_replays()
    executed_ids = set()
    actual_sum = 0.0
    actual_n = 0
    if trades is not None and not trades.empty:
        if "trade_id" in trades.columns:
            executed_ids = set(trades["trade_id"].dropna().astype(str))
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in trades.columns else "outcome_net_pnl_usd"
        actual_sum = round(float(pd.to_numeric(trades[pnl_col], errors="coerce").fillna(0).sum()), 2)
        actual_n = int(len(trades))

    profile_stats = {
        pid: {"sum_pnl_usd": 0.0, "n": 0, "wins": 0, "ladder_exits": 0, "thesis_cuts": 0, "replay_end": 0}
        for pid in LADDER_SIM_PROFILES
    }
    replays_considered = len(replays)
    replays_matched = (
        sum(1 for tid in executed_ids if _trade_has_replay(tid, replays))
        if executed_ids
        else 0
    )
    matched_replay_keys = set()
    if executed_ids:
        for tid in executed_ids:
            for key in _replay_keys_for_trade_id(tid):
                if key in replays:
                    matched_replay_keys.add(key)

    if replays and matched_replay_keys:
        for tid in matched_replay_keys:
            replay = replays.get(tid)
            if not replay:
                continue
            entry = _replay_entry_price(replay)
            ticks = replay.get("ticks") or []
            if not entry or not ticks:
                continue
            direction = replay.get("direction") or replay.get("final_direction") or "LONG"
            lev = float(replay.get("leverage") or 100)
            margin = float(replay.get("margin_usdt") or FLAT_MARGIN_LIVE_USD)
            fill_t = _replay_fill_t(replay)
            for pid, prof in LADDER_SIM_PROFILES.items():
                sim = _simulate_ticks_fast_cut_ladder(
                    ticks,
                    entry,
                    direction,
                    lev,
                    margin,
                    THESIS_FAST_EXIT_DEFAULT,
                    prof["ladder"],
                    THESIS_EXIT_ABOVE_DEFAULT,
                    fill_t=fill_t,
                    mfe_protect_pct=THESIS_MFE_PROTECT_DEFAULT,
                )
                if not sim:
                    continue
                pnl, reason, _peak = sim
                cell = profile_stats[pid]
                cell["sum_pnl_usd"] += float(pnl)
                cell["n"] += 1
                if float(pnl) > 0:
                    cell["wins"] += 1
                if reason == "PROFIT_LOCK_LADDER":
                    cell["ladder_exits"] += 1
                elif reason == "THESIS_FAST_CUT":
                    cell["thesis_cuts"] += 1
                elif reason == "REPLAY_END":
                    cell["replay_end"] += 1

    profiles_out = []
    for pid, prof in LADDER_SIM_PROFILES.items():
        cell = profile_stats[pid]
        n = cell["n"]
        sum_pnl = round(cell["sum_pnl_usd"], 2)
        delta = round(sum_pnl - actual_sum, 2) if actual_n and n else None
        unrealistic = bool(actual_sum > 0 and sum_pnl > actual_sum * 2)
        profiles_out.append({
            "profile_id": pid,
            "label": prof["label"],
            "ladder": prof["ladder"],
            "trades_simulated": n,
            "sum_pnl_usd": sum_pnl,
            "avg_pnl_usd": round(sum_pnl / n, 2) if n else 0.0,
            "wr_pct": round(100.0 * cell["wins"] / n, 1) if n else 0.0,
            "ladder_exit_pct": round(100.0 * cell["ladder_exits"] / n, 1) if n else 0.0,
            "thesis_cut_pct": round(100.0 * cell["thesis_cuts"] / n, 1) if n else 0.0,
            "delta_vs_actual_usd": delta,
            "unrealistic_vs_actual": unrealistic,
        })
    profiles_out.sort(key=lambda x: (-x["sum_pnl_usd"], -x["trades_simulated"]))
    best = profiles_out[0] if profiles_out else None
    disclaimer = (
        "HINDSIGHT COUNTERFACTUAL: tick replay on executed trade paths only (not perfect live fills). "
        "Perfect ladder fills at historical tick marks — optimistic vs live slippage/fees. "
        "Δ vs actual compares simulated cohort to session booked PnL; not a live deployment forecast."
    )
    if actual_n and replays_matched == 0:
        data_status = "NO_EXECUTED_REPLAY_OVERLAP"
        empty_reason = (
            f"No executed-trade replay overlap — {replays_considered} replays on disk are mostly "
            f"shadow/scan paths (scan-*, rev-*) from prior sessions, not linked to this session's "
            f"{actual_n} cont-*/vc603-* fills. Ladder sim requires bot v1.1.41+ post-exit tick "
            "collection on executed trade_ids after fresh start."
        )
        disclaimer += f" {empty_reason}"
    elif executed_ids and replays_matched < actual_n:
        data_status = "PARTIAL_OVERLAP"
        empty_reason = (
            f"Only {replays_matched}/{actual_n} executed trades have tick replays "
            f"({replays_considered} total replays on disk)."
        )
        disclaimer += f" Warning: {empty_reason}"
    elif replays_matched:
        data_status = "OK"
        empty_reason = None
    elif not replays:
        data_status = "NO_REPLAYS"
        empty_reason = f"No {SIGNAL_REPLAY_FILE} — run bot to collect tick replays."
        disclaimer += f" {empty_reason}"
    else:
        data_status = "OK"
        empty_reason = None
    if best and best.get("trades_simulated"):
        flag = " UNREALISTIC (>2× actual)" if best.get("unrealistic_vs_actual") else ""
        print(
            f"  Best profile: {best['profile_id']} sum=${best['sum_pnl_usd']:.2f} "
            f"(actual=${actual_sum:.2f}, Δ=${best.get('delta_vs_actual_usd')}){flag} {PIPELINE_ENFORCEMENT_TAG}"
        )
    elif not replays:
        print(f"  No {SIGNAL_REPLAY_FILE} — run bot to collect tick replays. {PIPELINE_ENFORCEMENT_TAG}")

    payload = {
        "schema": "exit_ladder_simulator_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thesis_fast_cut_pct": THESIS_FAST_EXIT_DEFAULT,
        "thesis_exit_above_pct": THESIS_EXIT_ABOVE_DEFAULT,
        "actual_realized_usd": actual_sum,
        "actual_trades": actual_n,
        "replays_available": replays_considered,
        "replays_considered": replays_considered,
        "replays_matched_executed": replays_matched,
        "data_status": data_status,
        "empty_reason": empty_reason,
        "disclaimer": disclaimer,
        "profiles": profiles_out,
        "best_profile_id": best["profile_id"] if best and best.get("trades_simulated") else None,
    }
    try:
        with open(EXIT_LADDER_SIMULATOR_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EXIT_LADDER_SIMULATOR_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EXIT_LADDER_SIMULATOR_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def chase_efficiency_matrix_report(trades=None, session=None, chase_payload=None):
    """Chase count buckets split by lane, AI bucket, and spread bucket."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CHASE EFFICIENCY MATRIX — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if chase_payload is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        chase_payload = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    if chase_payload is None:
        chase_payload = chase_attribution_report(trades=trades, session=session)

    work = _enrich_trades_with_buckets(trades.copy()) if trades is not None and not trades.empty else pd.DataFrame()
    chase_by_tid = _chase_attr_by_trade_id(chase_payload)
    trade_dims = {}
    if not work.empty and "trade_id" in work.columns:
        for _, row in work.iterrows():
            tid = str(row.get("trade_id") or "")
            if tid:
                trade_dims[tid] = {
                    "ai_bucket": row.get("ai_probability_bucket"),
                    "spread_bucket": row.get("directional_spread_bucket"),
                    "lane": str(row.get("research_lane") or "UNKNOWN").upper(),
                }

    matrix = defaultdict(lambda: {"trades": 0, "wins": 0, "sum_pnl_usd": 0.0})
    for attr in (chase_payload or {}).get("trades") or []:
        tid = str(attr.get("trade_id") or "")
        pnl = attr.get("net_pnl_usd")
        if pnl is None:
            continue
        dims = trade_dims.get(tid, {})
        chase_b = _chase_count_bucket(attr.get("chase_count"))
        lane = str(attr.get("lane") or dims.get("lane") or "UNKNOWN").upper()
        ai_b = dims.get("ai_bucket") or "unknown"
        spread_b = dims.get("spread_bucket") or "unknown"
        for key in (
            chase_b,
            f"{chase_b}|lane={lane}",
            f"{chase_b}|ai={ai_b}",
            f"{chase_b}|spread={spread_b}",
            f"{chase_b}|ai={ai_b}|spread={spread_b}|lane={lane}",
        ):
            cell = matrix[key]
            cell["trades"] += 1
            cell["sum_pnl_usd"] = round(cell["sum_pnl_usd"] + float(pnl), 2)
            if attr.get("win") or float(pnl) > 0:
                cell["wins"] += 1

    for key, cell in matrix.items():
        n = cell["trades"]
        cell["wr_pct"] = round(100.0 * cell["wins"] / n, 1) if n else 0.0
        cell["ev_usd"] = round(cell["sum_pnl_usd"] / n, 2) if n else 0.0

    overall = {k: v for k, v in matrix.items() if "|" not in k}
    by_lane = {k: v for k, v in matrix.items() if k.split("|")[0] in ("0", "1", "2", "3", "4", "5+") and "|lane=" in k and "|ai=" not in k}
    golden = sorted(
        [v for k, v in matrix.items() if "|ai=60-65|spread=4|lane=" in k],
        key=lambda x: x.get("ev_usd", 0),
        reverse=True,
    )[:10]

    payload = {
        "schema": "chase_efficiency_matrix_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_by_chase_count": overall,
        "by_lane_and_chase_count": by_lane,
        "golden_ai60_spread4": golden,
        "full_matrix": dict(matrix),
    }
    for key in ("0", "1", "2", "3", "4", "5+"):
        cell = overall.get(key)
        if cell and cell["trades"]:
            print(
                f"  {key}: n={cell['trades']} WR={cell['wr_pct']:.1f}% "
                f"EV=${cell['ev_usd']:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )
    try:
        with open(CHASE_EFFICIENCY_MATRIX_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CHASE_EFFICIENCY_MATRIX_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CHASE_EFFICIENCY_MATRIX_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _type_b_bucket(val, kind: str) -> str:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "unknown"
    if kind == "adx":
        if v < 20:
            return "adx<20"
        if v < 30:
            return "adx20-30"
        return "adx30+"
    if kind == "spread":
        if v <= 2:
            return "spread0-2"
        if v <= 4:
            return "spread3-4"
        return "spread5+"
    if kind == "conf":
        if v < 55:
            return "conf<55"
        if v < 65:
            return "conf55-65"
        return "conf65+"
    if kind == "vol":
        if v < 80:
            return "vol_low"
        if v < 150:
            return "vol_mid"
        return "vol_high"
    return "unknown"


def _type_b_probability_table(work: pd.DataFrame) -> list:
    """Historical P(TYPE_B | feature bucket) — discovery only, not an entry gate."""
    if work is None or work.empty:
        return []
    df = work.copy()
    df["trade_mfe_type"] = _trade_mfe_type_series(df)
    df["is_type_b"] = df["trade_mfe_type"].eq("TYPE_B")
    if "adx_at_entry" in df.columns:
        df["_adx_b"] = df["adx_at_entry"].map(lambda x: _type_b_bucket(x, "adx"))
    if "conviction_spread" in df.columns:
        df["_spread_b"] = df["conviction_spread"].map(lambda x: _type_b_bucket(x, "spread"))
    elif "directional_spread" in df.columns:
        df["_spread_b"] = df["directional_spread"].map(lambda x: _type_b_bucket(x, "spread"))
    if "ai_win_prob" in df.columns:
        df["_conf_b"] = df["ai_win_prob"].map(lambda x: _type_b_bucket(x, "conf"))
    if "volatility" in df.columns:
        df["_vol_b"] = df["volatility"].map(lambda x: _type_b_bucket(x, "vol"))
    if "context_ema_slope" in df.columns:
        df["_ema_b"] = pd.to_numeric(df["context_ema_slope"], errors="coerce").map(
            lambda x: "ema_up" if (x or 0) > 0 else ("ema_down" if (x or 0) < 0 else "ema_flat")
        )
    if "research_lane" in df.columns:
        df["_lane_b"] = df["research_lane"].fillna("").astype(str).str.upper()
    dim_cols = {
        "adx": "_adx_b", "spread": "_spread_b", "confidence": "_conf_b",
        "volatility": "_vol_b", "ema_slope": "_ema_b", "lane": "_lane_b",
    }
    rows = []
    for dim, col in dim_cols.items():
        if col not in df.columns:
            continue
        for bucket, sub in df.groupby(col, observed=True):
            if str(bucket) in ("unknown", "nan", ""):
                continue
            n = int(len(sub))
            if n < 3:
                continue
            b_n = int(sub["is_type_b"].sum())
            wr = round(100.0 * (sub["net_pnl_usd"].astype(float) > 0).mean(), 1) if "net_pnl_usd" in sub.columns else None
            rows.append({
                "dimension": dim,
                "bucket": str(bucket),
                "trades": n,
                "type_b_count": b_n,
                "type_b_probability_pct": round(100.0 * b_n / n, 1),
                "wr_pct": wr,
            })
    rows.sort(key=lambda r: (-r["type_b_probability_pct"], -r["trades"]))
    return rows[:40]


def type_b_predictor_report(trades=None, session=None):
    """Pre-entry feature separators — TYPE_A vs TYPE_B averages and ranked deltas."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== TYPE B PREDICTOR — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    work = _enrich_trades_with_buckets(trades.copy()) if trades is not None and not trades.empty else pd.DataFrame()
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
        ("conviction_spread", "conviction_spread"),
    ]
    if work.empty:
        payload = {"schema": "type_b_predictor_v1", "separators": [], "session_scope": scope}
        with open(TYPE_B_PREDICTOR_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    work["trade_mfe_type"] = _trade_mfe_type_series(work)
    cohort_stats = {}
    for ttype in ("TYPE_A", "TYPE_B", "MIXED"):
        sub = work[work["trade_mfe_type"] == ttype]
        stats = _combo_stats_from_df(sub)
        avgs = {}
        for col, label in feature_cols:
            if col not in sub.columns:
                continue
            val = pd.to_numeric(sub[col], errors="coerce").mean()
            if pd.notna(val):
                avgs[label] = round(float(val), 3)
        cohort_stats[ttype] = {**stats, "feature_averages": avgs}

    separators = []
    a = work[work["trade_mfe_type"] == "TYPE_A"]
    b = work[work["trade_mfe_type"] == "TYPE_B"]
    if not a.empty and not b.empty:
        for col, label in feature_cols:
            if col not in work.columns:
                continue
            am = pd.to_numeric(a[col], errors="coerce").mean()
            bm = pd.to_numeric(b[col], errors="coerce").mean()
            if pd.notna(am) and pd.notna(bm):
                separators.append({
                    "feature": label,
                    "type_a_mean": round(float(am), 3),
                    "type_b_mean": round(float(bm), 3),
                    "delta_abs": round(abs(float(bm) - float(am)), 3),
                    "direction": "higher_in_B" if bm > am else "lower_in_B",
                })
        separators.sort(key=lambda x: x["delta_abs"], reverse=True)

    prob_table = _type_b_probability_table(work)
    payload = {
        "schema": "type_b_predictor_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "TYPE_A: MFE<10% | TYPE_B: MFE>=15% | MIXED: between",
        "cohorts": cohort_stats,
        "separators_ranked": separators,
        "top_separators": separators[:10],
        "probability_table": prob_table,
        "hypothesis": "Moderate AI (60-65) + spread 4 + high participation → Type B sweet spot",
    }
    if separators:
        print(f"  Top separator: {separators[0]['feature']} Δ={separators[0]['delta_abs']} {PIPELINE_ENFORCEMENT_TAG}")
    try:
        with open(TYPE_B_PREDICTOR_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {TYPE_B_PREDICTOR_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {TYPE_B_PREDICTOR_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def lane_retirement_report(trades=None, session=None, benchmark_report=None):
    """Automatic KEEP / RETIRE / COLLECT MORE recommendations per pathway lane."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== LANE RETIREMENT REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if benchmark_report is None and os.path.isfile(BENCHMARK_VS_LANES_REPORT_FILE):
        benchmark_report = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    lanes = (benchmark_report or {}).get("lanes") or {}
    bench = lanes.get("CONTINUOUS") or {}
    bench_ev = float(bench.get("per_approve_ev") or 0)
    bench_pnl = float(bench.get("net_pnl_real") or 0)

    recommendations = []
    for lane_key in BENCHMARK_LANES:
        if lane_key == "CONTINUOUS":
            continue
        pathway_status = _pathway_lane_status(lane_key)
        m = lanes.get(lane_key) or {}
        all_time = m.get("all_time") or {}
        fills = int(m.get("real_fills") or m.get("fills") or 0)
        approves = int(m.get("approves") or 0)
        pnl = float(m.get("net_pnl_real") or m.get("net_pnl_usd") or 0)
        ev = float(m.get("per_approve_ev") or 0)
        at_fills = int(all_time.get("real_fills") or 0)
        at_pnl = float(all_time.get("net_pnl_real") or 0)
        hist_note = ""
        if at_fills and (fills != at_fills or abs(pnl - at_pnl) > 0.01):
            hist_note = f" · all-time: {at_fills} fills ${at_pnl:+.2f}"
        if pathway_status == "RETIRED":
            recommendations.append({
                "lane": lane_key,
                "trades": fills,
                "approves": approves,
                "pnl_usd": round(pnl, 2),
                "ev_per_approve": round(ev, 2),
                "benchmark_ev": round(bench_ev, 2),
                "pathway_status": pathway_status,
                "all_time_fills": at_fills,
                "all_time_pnl_usd": round(at_pnl, 2),
                "recommendation": "RETIRED",
                "reason": f"Paused — no new orders{hist_note}" if at_fills else "Frozen — no active research budget; historical analytics only",
            })
            continue
        if pathway_status == PATHWAY_STATUS_SHADOW_COLLECTING:
            recommendations.append({
                "lane": lane_key,
                "trades": fills,
                "approves": approves,
                "pnl_usd": round(pnl, 2),
                "ev_per_approve": round(ev, 2),
                "benchmark_ev": round(bench_ev, 2),
                "pathway_status": pathway_status,
                "all_time_fills": at_fills,
                "all_time_pnl_usd": round(at_pnl, 2),
                "recommendation": "COLLECTING",
                "reason": f"Shadow-only off-dashboard lane — simulated PnL, no live orders{hist_note}",
            })
            continue
        if fills == 0 and approves == 0 and at_fills == 0:
            recommendations.append({
                "lane": lane_key,
                "trades": 0,
                "approves": 0,
                "pnl_usd": 0.0,
                "ev_per_approve": 0.0,
                "benchmark_ev": round(bench_ev, 2),
                "pathway_status": pathway_status,
                "all_time_fills": 0,
                "all_time_pnl_usd": 0.0,
                "recommendation": "NO_DATA",
                "reason": "No session or historical fills in CSV for this lane",
            })
            continue
        if fills == 0 and approves == 0 and at_fills > 0:
            recommendations.append({
                "lane": lane_key,
                "trades": 0,
                "approves": 0,
                "pnl_usd": 0.0,
                "ev_per_approve": 0.0,
                "benchmark_ev": round(bench_ev, 2),
                "pathway_status": pathway_status,
                "all_time_fills": at_fills,
                "all_time_pnl_usd": round(at_pnl, 2),
                "recommendation": "HISTORICAL_ONLY",
                "reason": f"No session activity — all-time: {at_fills} fills ${at_pnl:+.2f}",
            })
            continue
        if pathway_status == "PROBATION":
            rec = "PROBATION"
            reason = "Collect unique trades/PnL/EV — retire if overlap with CONTINUOUS remains insignificant"
        elif fills < MIN_LANE_FILLS_FOR_RETIREMENT and approves < MIN_LANE_APPROVES_FOR_RETIREMENT:
            rec = "INSUFFICIENT_SAMPLE"
            reason = f"only {fills} fills / {approves} approves — need ≥{MIN_LANE_FILLS_FOR_RETIREMENT} fills"
        elif fills >= MIN_LANE_FILLS_FOR_RETIREMENT and (pnl <= -10 or (ev < 0 and fills >= 20)):
            rec = "RETIRE"
            reason = f"negative PnL ${pnl:+.2f} with {fills} fills or EV ${ev:+.2f}/approve"
        elif fills >= MIN_LANE_FILLS_FOR_RETIREMENT and bench_ev > 0 and ev < bench_ev * 0.45 and pnl < bench_pnl * 0.05:
            rec = "RETIRE"
            reason = f"dominated by CONTINUOUS — EV ${ev:+.2f} vs benchmark ${bench_ev:+.2f}"
        elif pnl > 0 and ev >= bench_ev * 0.85:
            rec = "KEEP"
            reason = f"beats benchmark EV (${ev:+.2f} vs ${bench_ev:+.2f})"
        elif pnl > 0 and (ev < bench_ev * 0.85 or fills < MIN_LANE_FILLS_FOR_RETIREMENT):
            rec = "WATCH"
            reason = f"profitable but below benchmark EV (${ev:+.2f} vs ${bench_ev:+.2f}) — collect more or demote"
        elif pnl <= 0 and fills >= MIN_LANE_FILLS_FOR_RETIREMENT:
            rec = "WATCH"
            reason = f"enough sample ({fills}) but weak PnL ${pnl:+.2f} — monitor"
        elif pnl > 0:
            rec = "WATCH"
            reason = "positive PnL but thin sample"
        else:
            rec = "WATCH"
            reason = "mixed signals — manual review"
        recommendations.append({
            "lane": lane_key,
            "trades": fills,
            "approves": approves,
            "pnl_usd": round(pnl, 2),
            "ev_per_approve": round(ev, 2),
            "benchmark_ev": round(bench_ev, 2),
            "pathway_status": pathway_status,
            "all_time_fills": at_fills,
            "all_time_pnl_usd": round(at_pnl, 2),
            "recommendation": rec,
            "reason": reason + hist_note if hist_note and hist_note not in reason else reason,
        })

    cont_fills = int(bench.get("real_fills") or bench.get("fills") or 0)
    recommendations.insert(0, {
        "lane": "CONTINUOUS",
        "trades": cont_fills,
        "approves": int(bench.get("approves") or 0),
        "pnl_usd": round(bench_pnl, 2),
        "ev_per_approve": round(bench_ev, 2),
        "benchmark_ev": round(bench_ev, 2),
        "recommendation": "KEEP (BENCHMARK)",
        "reason": "baseline lane — do not retire",
    })
    retire = [r for r in recommendations if r["recommendation"] == "RETIRE"]
    for r in recommendations:
        print(
            f"  {r['lane']}: {r['recommendation']} n={r['trades']} "
            f"PnL=${r['pnl_usd']:+.2f} EV=${r['ev_per_approve']:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "lane_retirement_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_lane": "CONTINUOUS",
        "active_roster": list(ACTIVE_PATHWAY_LANES),
        "retired_lanes": sorted(RETIRED_PATHWAY_LANES),
        "min_fills_for_decision": MIN_LANE_FILLS_FOR_RETIREMENT,
        "retire_candidates": [r["lane"] for r in retire],
        "lanes": recommendations,
    }
    try:
        with open(LANE_RETIREMENT_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {LANE_RETIREMENT_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {LANE_RETIREMENT_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _adx_bucket_val(v) -> str:
    try:
        from research_trade_accumulator import _adx_bucket as _ab
        return _ab(v)
    except ImportError:
        pass
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if x < 18:
        return "adx_low"
    if x < 30:
        return "adx_mid"
    return "adx_high"


def _trades_for_regime_analysis(trades=None, session=None):
    """Prefer fresh accumulator DB (v9.83+ epoch) over session CSV slice."""
    try:
        from research_trade_accumulator import load_accumulated_trades_df

        acc = load_accumulated_trades_df()
        if acc is not None and not acc.empty:
            return acc
    except Exception:
        pass
    return trades


def _regime_tags_from_row(row) -> dict:
    try:
        from research_trade_accumulator import compute_regime_tags

        if hasattr(row, "to_dict"):
            row = row.to_dict()
        return compute_regime_tags(row or {})
    except ImportError:
        pass
    ts = row.get("close_ts") or row.get("ts") or row.get("entry_ts") or ""
    weekend = "unknown"
    try:
        dt = pd.Timestamp(ts)
        if pd.notna(dt):
            weekend = "weekend" if dt.dayofweek >= 5 else "weekday"
    except Exception:
        pass
    adx = _adx_bucket_val(row.get("adx_at_entry") or row.get("adx"))
    spread = str(row.get("directional_spread_bucket") or "unk")
    key = f"{weekend}|{adx}|spread_{spread}"
    return {"regime_key": key, "day_type": weekend, "adx": adx, "spread_bucket": spread}


def _regime_key_from_row(row) -> str:
    return _regime_tags_from_row(row).get("regime_key", "unknown")


def regime_leaderboard_report(trades=None, session=None, min_trades=3):
    """Regime × lane leaderboard — which lane wins in which environment (Phase 1, no auto-switch)."""
    if session is None:
        session = load_research_session()
    trades = _trades_for_regime_analysis(trades=trades, session=session)
    scope = _shadow_scope_label(session)
    print(f"\n=== REGIME LEADERBOARD — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if trades is None or trades.empty:
        payload = {
            "schema": "regime_leaderboard_v1",
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cells": [],
            "regimes": [],
            "note": "No trades — collect data with 4 live tiles",
        }
        with open(REGIME_LEADERBOARD_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    work = _enrich_trades_with_buckets(trades.drop_duplicates(subset=["trade_id"], keep="last").copy())
    if "research_lane" not in work.columns:
        work["research_lane"] = "UNKNOWN"
    tag_rows = work.apply(_regime_tags_from_row, axis=1)
    work["regime_key"] = tag_rows.apply(lambda t: t.get("regime_key"))
    for dim in ("day_type", "session", "adx", "volatility", "funding", "liquidity"):
        work[dim] = tag_rows.apply(lambda t, d=dim: t.get(d))

    cells = []
    by_regime_lane = {}
    for (regime, lane), sub in work.groupby(["regime_key", work["research_lane"].astype(str).str.upper()]):
        stats = _combo_stats_from_df(sub)
        if stats["trades"] < 1:
            continue
        cell = {"regime": regime, "lane": lane, **stats}
        cells.append(cell)
        by_regime_lane.setdefault(regime, []).append(cell)

    regimes = []
    for regime, lane_rows in sorted(by_regime_lane.items()):
        eligible = [r for r in lane_rows if r["trades"] >= min_trades]
        ranked = sorted(eligible or lane_rows, key=lambda x: (x["ev_usd"], x["pnl_usd"]), reverse=True)
        best = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None
        regimes.append({
            "regime": regime,
            "total_trades": sum(r["trades"] for r in lane_rows),
            "lanes_observed": len(lane_rows),
            "best_lane": best["lane"] if best else None,
            "best_ev_usd": best["ev_usd"] if best else None,
            "best_pnl_usd": best["pnl_usd"] if best else None,
            "second_lane": second["lane"] if second else None,
            "conclusion_allowed": bool(best and best["trades"] >= min_trades),
            "lanes": sorted(lane_rows, key=lambda x: -x["ev_usd"]),
        })
        if best:
            print(
                f"  {regime}: best={best['lane']} EV=${best['ev_usd']:+.2f} "
                f"n={best['trades']} {PIPELINE_ENFORCEMENT_TAG}"
            )

    payload = {
        "schema": "regime_leaderboard_v2",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "data_source": "research_accumulator_db" if len(work) else "session_csv",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_trades_per_cell": min_trades,
        "target_trades_for_roster": 200,
        "total_trades": len(work),
        "regime_dimensions": ["day_type", "session", "adx", "volatility", "funding", "liquidity"],
        "cells": cells,
        "regimes": regimes,
        "usage_note": "Recommend-only — do not auto-switch lanes until ≥20 trades per regime cell (~200 total tagged)",
    }
    try:
        with open(REGIME_LEADERBOARD_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {REGIME_LEADERBOARD_REPORT_FILE} ({len(regimes)} regimes) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {REGIME_LEADERBOARD_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def roster_policy_report(trades=None, session=None, regime_payload=None, benchmark_report=None):
    """Recommend lane weights from regime leaderboard — human approval required before bot applies."""
    if session is None:
        session = load_research_session()
    trades = _trades_for_regime_analysis(trades=trades, session=session)
    if regime_payload is None:
        regime_payload = _load_json_report(REGIME_LEADERBOARD_REPORT_FILE) or {}
    regimes = regime_payload.get("regimes") or []
    min_n = int(regime_payload.get("min_trades_per_cell") or 3)

    # Detect current regime from latest trade timestamp
    current_regime = "unknown"
    if trades is not None and not trades.empty:
        sort_cols = [c for c in ("close_ts", "ts") if c in trades.columns]
        last = trades.sort_values(by=sort_cols[0] if sort_cols else trades.columns[0], ascending=False).iloc[0]
        current_regime = _regime_key_from_row(last)

    match = next((r for r in regimes if r.get("regime") == current_regime and r.get("conclusion_allowed")), None)
    if not match and regimes:
        match = max(
            (r for r in regimes if r.get("conclusion_allowed")),
            key=lambda r: r.get("total_trades") or 0,
            default=None,
        )

    weights = {ln: 0.25 for ln in BENCHMARK_LANES if ln in ACTIVE_PATHWAY_LANES or ln == BENCHMARK_LANE}
    weights[BENCHMARK_LANE] = 0.5
    action = "COLLECT_ONLY"
    reason = "Insufficient regime sample — keep all tiles collecting"
    confidence = "LOW"

    if match and match.get("best_lane"):
        best = match["best_lane"]
        for k in list(weights.keys()):
            weights[k] = 0.1
        weights[best] = 0.7
        if match.get("second_lane") in weights:
            weights[match["second_lane"]] = 0.2
        weights[BENCHMARK_LANE] = 0.0 if best != BENCHMARK_LANE else 0.5
        action = "RECOMMEND_WEIGHTS"
        reason = (
            f"Regime {match['regime']}: favor {best} "
            f"(EV ${match.get('best_ev_usd'):+.2f}, n={next((c['trades'] for c in match.get('lanes') or [] if c.get('lane')==best), 0)})"
        )
        confidence = "MODERATE" if (match.get("total_trades") or 0) >= min_n * 2 else "LOW"

    total_tagged = int(regime_payload.get("total_trades") or 0)
    target = int(regime_payload.get("target_trades_for_roster") or 200)
    progress_pct = round(100.0 * min(total_tagged, target) / target, 1) if target else 0.0

    payload = {
        "schema": "roster_policy_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_regime": current_regime,
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "tile_weights": weights,
        "auto_apply": False,
        "requires_human_approval": True,
        "collection_progress": {
            "accumulated_trades": total_tagged,
            "target_trades": target,
            "progress_pct": progress_pct,
            "ready_for_roster_decision": total_tagged >= target,
        },
        "phase": "COLLECT" if total_tagged < target else "RECOMMEND_REVIEW",
    }
    try:
        with open(ROSTER_POLICY_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {ROSTER_POLICY_FILE} action={action} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {ROSTER_POLICY_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def feature_importance_report(trades=None, session=None):
    """Rank trading features by |correlation| with net PnL — not ML, simple Pearson."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== FEATURE IMPORTANCE REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    candidates = [
        ("confidence", ("conf", "ai_win_prob")),
        ("edge", ("edge_score", "edge_score_at_entry")),
        ("structure", ("structure_score_at_entry", "structure_score")),
        ("spread", ("factor_spread",)),
        ("mtf_alignment", ("directional_factor_spread",)),
        ("adx", ("adx_at_entry", "adx")),
        ("momentum", ("momentum",)),
        ("volatility", ("volatility",)),
        ("participation", ("features_volume_ratio",)),
        ("velocity", ("features_velocity", "velocity")),
    ]
    if trades is None or trades.empty:
        payload = {
            "schema": "feature_importance_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "features": [],
        }
        try:
            with open(FEATURE_IMPORTANCE_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return payload

    work = trades.copy()
    if "trade_id" in work.columns:
        work = work.drop_duplicates(subset=["trade_id"], keep="last")
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
    pnl = pd.to_numeric(work.get(pnl_col), errors="coerce")
    ranked = []
    for label, cols in candidates:
        series = None
        used_col = None
        for col in cols:
            if col in work.columns:
                s = pd.to_numeric(work[col], errors="coerce")
                if s.notna().sum() >= 10:
                    series = s
                    used_col = col
                    break
        if series is None:
            continue
        aligned = pd.concat([series, pnl], axis=1).dropna()
        if len(aligned) < 10:
            continue
        corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
        if pd.isna(corr):
            continue
        ranked.append({
            "feature": label,
            "column": used_col,
            "correlation_with_pnl": round(corr, 4),
            "abs_correlation": round(abs(corr), 4),
            "n": int(len(aligned)),
        })
    ranked.sort(key=lambda x: -x["abs_correlation"])
    if ranked:
        top = ranked[0]
        weak = [r["feature"] for r in ranked if r["abs_correlation"] < 0.05]
        print(
            f"  Top predictor: {top['feature']} corr={top['correlation_with_pnl']:+.3f} "
            f"| weak (|r|<0.05): {', '.join(weak) or 'none'} {PIPELINE_ENFORCEMENT_TAG}"
        )
    payload = {
        "schema": "feature_importance_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Pearson correlation with net_pnl_usd — validation only, not for auto-tuning",
        "trades": int(pnl.notna().sum()),
        "features": ranked,
        "weak_signals": [r["feature"] for r in ranked if r["abs_correlation"] < 0.05],
    }
    try:
        with open(FEATURE_IMPORTANCE_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {FEATURE_IMPORTANCE_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {FEATURE_IMPORTANCE_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def chase_profit_report(trades=None, session=None, chase_payload=None):
    """Incremental PnL: chase-assisted fills vs static-limit fills."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CHASE PROFIT REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if chase_payload is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        chase_payload = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    attrs = (chase_payload or {}).get("attributions") or (chase_payload or {}).get("trades") or []
    trade_pnl = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        for _, row in work.iterrows():
            tid = str(row.get("trade_id") or "")
            if tid:
                trade_pnl[tid] = float(pd.to_numeric(row.get(pnl_col), errors="coerce") or 0)

    chase_fills, static_fills = [], []
    for a in attrs:
        filled = bool(
            a.get("filled_after_chase")
            or a.get("fill_reason") in ("LIMIT_CHASE", "STATIC_LIMIT")
            or a.get("filled")
        )
        if not filled:
            continue
        tid = str(a.get("trade_id") or "")
        pnl = a.get("net_pnl_usd")
        if pnl is None and tid in trade_pnl:
            pnl = trade_pnl[tid]
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            continue
        cc = int(a.get("chase_count") or a.get("limit_chase_count") or 0)
        row = {"trade_id": tid, "pnl_usd": round(pnl, 2), "chase_count": cc}
        if cc > 0 or a.get("filled_after_chase"):
            chase_fills.append(row)
        elif a.get("fill_reason") == "STATIC_LIMIT":
            static_fills.append(row)

    chase_n = len(chase_fills)
    static_n = len(static_fills)
    chase_pnl = round(sum(r["pnl_usd"] for r in chase_fills), 2)
    static_pnl = round(sum(r["pnl_usd"] for r in static_fills), 2)
    chase_ev = round(chase_pnl / chase_n, 2) if chase_n else 0.0
    static_ev = round(static_pnl / static_n, 2) if static_n else 0.0
    incremental = round(chase_pnl - (static_ev * chase_n if static_n else 0), 2)

    payload = {
        "schema": "chase_profit_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "How much profit does chase contribute vs static limits?",
        "chase_assisted": {
            "fills": chase_n,
            "sum_pnl_usd": chase_pnl,
            "ev_usd": chase_ev,
        },
        "static_limit": {
            "fills": static_n,
            "sum_pnl_usd": static_pnl,
            "ev_usd": static_ev,
        },
        "incremental_value_usd": incremental,
        "note": "Incremental = chase PnL minus (static EV × chase fill count). Validation only.",
    }
    print(
        f"  chase fills={chase_n} PnL=${chase_pnl:+.2f} | static fills={static_n} PnL=${static_pnl:+.2f} "
        f"| incremental≈${incremental:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
    )
    try:
        with open(CHASE_PROFIT_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CHASE_PROFIT_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CHASE_PROFIT_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def confidence_band_cross_report(trades=None, session=None, benchmark_report=None):
    """PnL/WR by confidence band — overall and per lane."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== CONFIDENCE BAND CROSS REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    overall = []
    by_lane = []
    if trades is not None and not trades.empty:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last").copy()
        conf = pd.to_numeric(work.get("ai_win_prob", work.get("conf")), errors="coerce")
        work["confidence_band"] = conf.apply(_confidence_band_label)
        for bucket in CONFIDENCE_BANDS_STANDARD:
            sub = work[work["confidence_band"] == bucket]
            stats = _direction_cohort_stats(sub)
            overall.append({"band": bucket, **stats})
            if stats["trades"]:
                print(
                    f"  ALL {bucket}: n={stats['trades']} WR={stats['win_rate_pct']:.1f}% "
                    f"PnL=${stats['sum_pnl_usd']:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
                )
        lane_col = "research_lane" if "research_lane" in work.columns else None
        lanes = BENCHMARK_LANES if lane_col else []
        for lane in lanes:
            lane_df = work[work[lane_col].astype(str) == lane] if lane_col else work.iloc[0:0]
            bands = []
            for bucket in CONFIDENCE_BANDS_STANDARD:
                sub = lane_df[lane_df["confidence_band"] == bucket]
                stats = _direction_cohort_stats(sub)
                bands.append({"band": bucket, **stats})
            if any(b.get("trades") for b in bands):
                by_lane.append({"lane": lane, "bands": bands})
    bench = (benchmark_report or {}).get("lanes") or {}
    benchmark_by_band = overall
    payload = {
        "schema": "confidence_band_cross_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bands": list(CONFIDENCE_BANDS_STANDARD),
        "overall_by_band": overall,
        "benchmark_continuous_by_band": benchmark_by_band,
        "lanes_by_band": by_lane,
        "continuous_benchmark_ev": float((bench.get("CONTINUOUS") or {}).get("per_approve_ev") or 0),
    }
    try:
        with open(CONFIDENCE_BAND_CROSS_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {CONFIDENCE_BAND_CROSS_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {CONFIDENCE_BAND_CROSS_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def edge_validation_report(trades=None, session=None):
    """Mark edge filter ACTIVE / WATCHLIST / DEPRECATED — validation only, no auto-tuning."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== EDGE VALIDATION REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    edge_corr = None
    incremental_verdict = None
    if os.path.isfile(EDGE_INCREMENTAL_VALUE_REPORT_FILE):
        inc = _load_json_report(EDGE_INCREMENTAL_VALUE_REPORT_FILE) or {}
        incremental_verdict = inc.get("verdict") or inc.get("summary")
    if trades is not None and not trades.empty:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        edge_col = next((c for c in ("edge_score_at_entry", "edge_score", "decision_edge_score") if c in work.columns), None)
        if edge_col:
            aligned = pd.concat([
                pd.to_numeric(work[edge_col], errors="coerce"),
                pd.to_numeric(work[pnl_col], errors="coerce"),
            ], axis=1).dropna()
            if len(aligned) >= 10:
                edge_corr = round(float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1])), 4)
    status = "DEPRECATED"
    reasons = []
    if incremental_verdict and "no_incremental" in str(incremental_verdict).lower():
        reasons.append(str(incremental_verdict))
    if edge_corr is not None and abs(edge_corr) < 0.05:
        reasons.append(f"corr(edge,pnl)={edge_corr}")
    if not reasons:
        status = "WATCHLIST"
        reasons.append("edge shows weak but non-zero signal — keep collecting")
    print(f"  EDGE status={status} {' | '.join(reasons)} {PIPELINE_ENFORCEMENT_TAG}")
    payload = {
        "schema": "edge_validation_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "edge_validation_status": status,
        "status_labels": ["ACTIVE", "WATCHLIST", "DEPRECATED"],
        "edge_correlation_with_pnl": edge_corr,
        "incremental_verdict": incremental_verdict,
        "reasons": reasons,
        "usage_note": "Validation only — do not auto-remove edge from bot based on this report alone.",
    }
    try:
        with open(EDGE_VALIDATION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {EDGE_VALIDATION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {EDGE_VALIDATION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _overlap_signal_key(snap: dict):
    """Match spawn lanes to CONTINUOUS approves by time bucket + direction + AI prob."""
    if not isinstance(snap, dict):
        return None
    ts_raw = snap.get("approve_ts")
    if ts_raw is None:
        try:
            ts_raw = pd.Timestamp(snap.get("ts")).timestamp()
        except Exception:
            ts_raw = 0
    try:
        ts_val = float(ts_raw)
    except (TypeError, ValueError):
        ts_val = 0
    ai = snap.get("ai") or {}
    try:
        prob = int(round(float(ai.get("win_prob") or snap.get("ai_win_prob") or 0)))
    except (TypeError, ValueError):
        prob = 0
    direction = str(snap.get("direction") or ai.get("direction") or "").upper()
    if not direction:
        return None
    return (int(ts_val // 30), direction, prob)


def benchmark_contribution_report(trades=None, session=None, benchmark_report=None):
    """Share of total session PnL contributed by each lane."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== BENCHMARK CONTRIBUTION REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    if benchmark_report is None and os.path.isfile(BENCHMARK_VS_LANES_REPORT_FILE):
        benchmark_report = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    lanes = (benchmark_report or {}).get("lanes") or {}
    rows = []
    total_pnl = 0.0
    for lane_key in BENCHMARK_LANES:
        m = lanes.get(lane_key) or {}
        pnl = float(m.get("net_pnl_real") or m.get("net_pnl_usd") or 0)
        fills = int(m.get("real_fills") or m.get("fills") or 0)
        if fills == 0 and pnl == 0 and lane_key != BENCHMARK_LANE:
            continue
        total_pnl += pnl
        rows.append({
            "lane": lane_key,
            "label": RESEARCH_LANE_LABELS.get(lane_key, lane_key),
            "fills": fills,
            "pnl_usd": round(pnl, 2),
        })
    for row in rows:
        row["pnl_pct_of_total"] = round(100.0 * row["pnl_usd"] / total_pnl, 1) if total_pnl else 0.0
    rows.sort(key=lambda x: -abs(x["pnl_usd"]))
    for row in rows:
        if row["pnl_usd"]:
            print(
                f"  {row['lane']}: ${row['pnl_usd']:+.2f} ({row['pnl_pct_of_total']:.1f}% of total) "
                f"n={row['fills']} {PIPELINE_ENFORCEMENT_TAG}"
            )
    payload = {
        "schema": "benchmark_contribution_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_pnl_usd": round(total_pnl, 2),
        "lanes": rows,
    }
    try:
        with open(BENCHMARK_CONTRIBUTION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {BENCHMARK_CONTRIBUTION_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {BENCHMARK_CONTRIBUTION_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def lane_overlap_report(trades=None, session=None, benchmark_report=None):
    """How much each experiment lane overlaps CONTINUOUS approves vs unique alpha."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== LANE OVERLAP REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    snapshots_all = _load_signal_snapshots()
    snapshots = _filter_snapshots_by_session(snapshots_all, session)
    trade_pnl = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        for _, row in work.iterrows():
            tid = str(row.get("trade_id") or "")
            if tid:
                trade_pnl[tid] = float(pd.to_numeric(row.get(pnl_col), errors="coerce") or 0)

    continuous_keys = set()
    for tid, snap in snapshots.items():
        if str(snap.get("research_lane") or "") == BENCHMARK_LANE:
            k = _overlap_signal_key(snap)
            if k:
                continuous_keys.add(k)

    lanes_out = []
    for lane_key in EXPERIMENT_LANES:
        if lane_key in LEGACY_LANES:
            continue
        lane_snaps = [(tid, s) for tid, s in snapshots.items() if str(s.get("research_lane") or "") == lane_key]
        if not lane_snaps:
            continue
        keys = []
        for tid, snap in lane_snaps:
            k = _overlap_signal_key(snap)
            if k:
                keys.append((tid, k))
        if not keys:
            continue
        overlap_n = sum(1 for _tid, k in keys if k in continuous_keys)
        unique_n = len(keys) - overlap_n
        overlap_pct = round(100.0 * overlap_n / len(keys), 1) if keys else 0.0
        unique_pct = round(100.0 * unique_n / len(keys), 1) if keys else 0.0
        overlap_pnl = round(sum(trade_pnl.get(tid, 0) for tid, k in keys if k in continuous_keys), 2)
        unique_pnl = round(sum(trade_pnl.get(tid, 0) for tid, k in keys if k not in continuous_keys), 2)
        rec = {
            "lane": lane_key,
            "label": RESEARCH_LANE_LABELS.get(lane_key, lane_key),
            "approves": len(keys),
            "overlap_with_continuous": overlap_n,
            "unique_signals": unique_n,
            "overlap_pct": overlap_pct,
            "unique_pct": unique_pct,
            "overlap_pnl_usd": overlap_pnl,
            "unique_pnl_usd": unique_pnl,
            "recommendation": (
                "LIKELY_DUPLICATE" if overlap_pct >= 75 and unique_pnl <= 0
                else "UNIQUE_ALPHA" if unique_pct >= 40 and unique_pnl > 0
                else "MIXED"
            ),
        }
        lanes_out.append(rec)
        print(
            f"  {lane_key}: overlap={overlap_pct:.0f}% unique={unique_pct:.0f}% "
            f"unique_pnl=${unique_pnl:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
        )

    payload = {
        "schema": "lane_overlap_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_lane": BENCHMARK_LANE,
        "match_method": "30s approve bucket + direction + AI win_prob",
        "continuous_approve_keys": len(continuous_keys),
        "lanes": lanes_out,
    }
    try:
        with open(LANE_OVERLAP_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {LANE_OVERLAP_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {LANE_OVERLAP_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def fast_cut_sweep_report(trades=None, session=None):
    """Replay sweep at -6/-8/-10/-12 vs current booked PnL (executed trades)."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== FAST CUT SWEEP REPORT — {scope.lower()} {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    replays = _load_jsonl_replays()
    sweep_levels = list(FAST_CUT_SWEEP_LEVELS) + [THESIS_FAST_EXIT_DEFAULT]
    sweep_levels = sorted(set(round(float(x), 1) for x in sweep_levels))
    current_label = round(float(THESIS_FAST_EXIT_DEFAULT), 1)

    per_level = {lv: {"trades": 0, "sum_pnl_usd": 0.0, "wins": 0} for lv in sweep_levels}
    booked_total = 0.0
    booked_n = 0
    replay_n = 0

    if trades is None or trades.empty:
        payload = {
            "schema": "fast_cut_sweep_v1",
            "analyzer_sync_id": ANALYZER_SYNC_ID,
            "session_scope": scope,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "current_cut_pct": current_label,
            "sweep_levels": [],
            "booked": {"trades": 0, "sum_pnl_usd": 0},
        }
        try:
            with open(FAST_CUT_SWEEP_REPORT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return payload

    work = trades.drop_duplicates(subset=["trade_id"], keep="last")
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"

    for _, row in work.iterrows():
        tid = str(row.get("trade_id") or "")
        booked = float(pd.to_numeric(row.get(pnl_col), errors="coerce") or 0)
        booked_total += booked
        booked_n += 1
        replay = _fast_cut_trade_replay(row, replays)
        if not replay or not replay.get("ticks"):
            continue
        ticks = replay.get("ticks") or []
        entry = _replay_entry_price(replay)
        if not entry or entry <= 0:
            continue
        direction = str(row.get("final_direction") or row.get("dir") or replay.get("direction") or "SHORT").upper()
        lev = int(row.get("leverage") or replay.get("leverage") or 100)
        margin = float(row.get("margin_usdt") or replay.get("margin_usdt") or FLAT_MARGIN_LIVE_USD)
        fill_t = _replay_fill_t(replay)
        replay_n += 1
        for cut_pct in sweep_levels:
            sim_usd, _reason, _peak = _simulate_ticks_fast_cut_ladder(
                ticks, entry, direction, lev, margin,
                cut_pct, TRAIL_LADDER, THESIS_EXIT_ABOVE_DEFAULT, fill_t=fill_t,
            )
            if sim_usd is None:
                continue
            bucket = per_level[cut_pct]
            bucket["trades"] += 1
            bucket["sum_pnl_usd"] = round(bucket["sum_pnl_usd"] + float(sim_usd), 2)
            if sim_usd > 0:
                bucket["wins"] += 1

    sweep_rows = []
    for cut_pct in sweep_levels:
        b = per_level[cut_pct]
        n = b["trades"]
        sweep_rows.append({
            "cut_pct": cut_pct,
            "is_current": abs(cut_pct - current_label) < 0.01,
            "trades": n,
            "sum_pnl_usd": b["sum_pnl_usd"],
            "avg_pnl_usd": round(b["sum_pnl_usd"] / n, 2) if n else 0.0,
            "win_rate_pct": round(100.0 * b["wins"] / n, 1) if n else 0.0,
            "delta_vs_booked_usd": round(b["sum_pnl_usd"] - booked_total, 2) if n else None,
        })
        if n:
            tag = " (current)" if abs(cut_pct - current_label) < 0.01 else ""
            print(
                f"  cut {cut_pct:+.0f}%{tag}: sim_pnl=${b['sum_pnl_usd']:+.2f} "
                f"n={n} Δvs_booked=${b['sum_pnl_usd'] - booked_total:+.2f} {PIPELINE_ENFORCEMENT_TAG}"
            )

    payload = {
        "schema": "fast_cut_sweep_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_cut_pct": current_label,
        "replay_coverage_pct": round(100.0 * replay_n / booked_n, 1) if booked_n else 0.0,
        "booked": {"trades": booked_n, "sum_pnl_usd": round(booked_total, 2)},
        "sweep_levels": sweep_rows,
        "note": "Counterfactual replay sim — not for auto-tuning until post-exit replay coverage is high.",
    }
    try:
        with open(FAST_CUT_SWEEP_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {FAST_CUT_SWEEP_REPORT_FILE} {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {FAST_CUT_SWEEP_REPORT_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def _save_rolling_snapshot():
    """Copy current reports to reports/history/TIMESTAMP for run-over-run comparison."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
        dest = os.path.join(REPORTS_HISTORY_DIR, stamp)
        os.makedirs(dest, exist_ok=True)
        copied = 0
        for fname in ANALYZER_JSON_REPORT_FILES:
            if os.path.isfile(fname):
                shutil.copy2(fname, os.path.join(dest, os.path.basename(fname)))
                copied += 1
        rep_dir = REPORTS_DIR
        if os.path.isdir(rep_dir):
            snap_rep = os.path.join(dest, "reports")
            os.makedirs(snap_rep, exist_ok=True)
            for p in glob.glob(os.path.join(rep_dir, "*.json")):
                shutil.copy2(p, os.path.join(snap_rep, os.path.basename(p)))
        for txt in (
            EXECUTIVE_SUMMARY_FILE, RESEARCH_FINDINGS_FILE, REPORT_MANIFEST_FILE,
        ):
            if os.path.isfile(txt):
                shutil.copy2(txt, os.path.join(dest, txt))
        meta = {"snapshot_at": datetime.now(timezone.utc).isoformat(), "files_copied": copied}
        with open(os.path.join(dest, "snapshot_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return dest
    except Exception:
        return None


def pre_test_analytics_reports(
    trades=None,
    decisions=None,
    session=None,
    benchmark_report=None,
    shadow_report=None,
    blocked=None,
):
    """Run all v9.53 pre-test analytics (no strategy changes)."""
    ai_calibration_report(trades=trades, session=session)
    ai_decision_fingerprint_report(trades=trades, session=session)
    approve_outcome_confidence_direction_report(trades=trades, session=session)
    benchmark_relative_scorecard_report(
        trades=trades,
        session=session,
        benchmark_report=benchmark_report,
        blocked=blocked,
        shadow_report=shadow_report,
    )
    missed_opportunity_heatmap_report(trades=trades, session=session)
    chase_payload = chase_attribution_report(trades=trades, session=session)
    chase_effectiveness_report(trades=trades, session=session, chase_payload=chase_payload)
    chase_threshold_report(trades=trades, session=session, chase_payload=chase_payload)
    chase_profit_report(trades=trades, session=session, chase_payload=chase_payload)
    urgent_chase_report(
        trades=trades, session=session, benchmark_report=benchmark_report, chase_payload=chase_payload,
    )
    chase_delay_report(
        trades=trades, session=session, benchmark_report=benchmark_report, chase_payload=chase_payload,
    )
    lane_chase_isolation_report(trades=trades, session=session, chase_payload=chase_payload)
    top_combinations_report(trades=trades, session=session)
    exit_combinations_report(trades=trades, session=session)
    exit_leakage_by_reason_report(trades=trades, session=session)
    exit_ladder_simulator_report(trades=trades, session=session)
    chase_efficiency_matrix_report(trades=trades, session=session, chase_payload=chase_payload)
    type_b_predictor_report(trades=trades, session=session)
    first_15m_outcome_report(trades=trades, session=session)
    scenario_c_leakage_report(trades=trades, session=session)
    ai_direction_bias_report(trades=trades, decisions=decisions, session=session)
    edge_predictiveness_report(trades=trades, session=session)
    edge_score_decile_report(trades=trades, session=session)
    edge_incremental_value_report(trades=trades, session=session)
    scenario_c_capture_ratio_report(trades=trades, session=session)
    horizon_profitability_report(trades=trades, session=session)
    fast_cut_survivor_report(trades=trades, session=session)
    pathway_survival_report(trades=trades, session=session)
    top_leakage_report(trades=trades, session=session)
    try:
        from pathway_lab_validation import (
            audit_type_b_not_in_execution,
            run_ai_scan_independence_self_test,
            run_ai_scan_role_validation,
            run_tile_independence_self_test,
            validate_exit_reports_populated,
            verify_repo_version_sync,
        )
        audit_type_b_not_in_execution()
        run_tile_independence_self_test(retired_status=dict(PATHWAY_LANE_STATUS))
        run_ai_scan_independence_self_test(retired_status=dict(PATHWAY_LANE_STATUS))
        run_ai_scan_role_validation()
        verify_repo_version_sync()
        trade_n = len(trades.drop_duplicates(subset=["trade_id"])) if trades is not None and not trades.empty and "trade_id" in trades.columns else len(trades or [])
        exit_val = validate_exit_reports_populated(trade_count=int(trade_n))
        if exit_val.get("verdict") == "INSUFFICIENT_DATA":
            print(
                f"  ⚠️ Exit report validation {exit_val.get('verdict')}: "
                f"{'; '.join(exit_val.get('errors') or [])} — reports still written; finalize continues "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )
    except SystemExit as exc:
        print(f"  ⚠️ Pathway validation halted: {exc} — continuing to finalize {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as exc:
        print(f"  ⚠️ Pathway validation skipped: {exc} {PIPELINE_ENFORCEMENT_TAG}")
    lane_definition_report(trades=trades, session=session, benchmark_report=benchmark_report)
    lane_retirement_report(trades=trades, session=session, benchmark_report=benchmark_report)
    regime_payload = regime_leaderboard_report(trades=trades, session=session)
    roster_policy_report(trades=trades, session=session, regime_payload=regime_payload, benchmark_report=benchmark_report)
    feature_importance_report(trades=trades, session=session)
    confidence_band_cross_report(trades=trades, session=session, benchmark_report=benchmark_report)
    edge_validation_report(trades=trades, session=session)
    benchmark_contribution_report(trades=trades, session=session, benchmark_report=benchmark_report)
    lane_overlap_report(trades=trades, session=session, benchmark_report=benchmark_report)
    fast_cut_sweep_report(trades=trades, session=session)
    chase_payload_final = chase_payload
    if chase_payload_final is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):
        chase_payload_final = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    run_integrity_checks(
        trades=trades,
        decisions=decisions,
        session=session,
        chase_payload=chase_payload_final,
        benchmark_report=benchmark_report,
    )


def pathway_lane_specs_report(trades=None, session=None, benchmark_report=None, shadow_report=None):
    """Write pathway_lane_specs.json — static lane params + session stats for Pathway Lab tiles."""
    if session is None:
        session = load_research_session()
    scope = _shadow_scope_label(session)
    print(f"\n=== PATHWAY LANE SPECS — {scope.lower()} {PIPELINE_ENFORCEMENT_TAG} ===")

    if benchmark_report is None and os.path.isfile(BENCHMARK_VS_LANES_REPORT_FILE):
        try:
            with open(BENCHMARK_VS_LANES_REPORT_FILE, encoding="utf-8") as f:
                benchmark_report = json.load(f)
        except Exception:
            benchmark_report = None
    if benchmark_report is None:
        benchmark_report = benchmark_vs_lanes_report(trades=trades, session=session, shadow_report=shadow_report)

    lane_metrics = (benchmark_report or {}).get("lanes") or {}
    shadow_by_lane = (shadow_report or {}).get("by_lane") or {}
    if not shadow_by_lane and os.path.isfile(SHADOW_FILL_OUTCOME_REPORT_FILE):
        try:
            with open(SHADOW_FILL_OUTCOME_REPORT_FILE, encoding="utf-8") as f:
                shadow_by_lane = json.load(f).get("by_lane") or {}
        except Exception:
            pass

    static = _static_pathway_lane_specs()
    tiles = []
    for lane_key in BENCHMARK_LANES:
        base = dict(static.get(lane_key) or {})
        if not base:
            continue
        metrics = lane_metrics.get(lane_key) or {}
        shadow = shadow_by_lane.get(lane_key) or {}
        ttl = int((shadow.get("counts") or {}).get("Shadow fill + TTL expired") or 0)
        gate_blocks = int((shadow.get("counts") or {}).get("Shadow fill + blocked (gates)") or 0)
        session_line = (
            f"n={metrics.get('approves', 0)} approves · "
            f"{metrics.get('real_fills', 0)} trades · "
            f"{metrics.get('approve_to_fill_pct', 0):.0f}% fill · "
            f"${metrics.get('net_pnl_real', 0):.2f} real · "
            f"EV ${metrics.get('per_approve_ev', 0):.2f}/approve"
        )
        if ttl:
            session_line += f" · {ttl} TTL expired"
        if gate_blocks:
            session_line += f" · {gate_blocks} gate blocks"
        base["session_stats"] = {
            "approves": metrics.get("approves", 0),
            "real_fills": metrics.get("real_fills", 0),
            "approve_to_fill_pct": metrics.get("approve_to_fill_pct", 0),
            "shadow_fill_pct": metrics.get("shadow_fill_pct", 0),
            "net_pnl_real": metrics.get("net_pnl_real", 0),
            "per_approve_ev": metrics.get("per_approve_ev", 0),
            "verdict": metrics.get("verdict"),
            "summary_line": session_line,
        }
        if lane_key != BENCHMARK_LANE:
            base["delta_vs_benchmark"] = {
                "delta_approve_to_fill_pct": metrics.get("delta_approve_to_fill_pct", 0),
                "delta_net_pnl_real": metrics.get("delta_net_pnl_real", 0),
                "delta_per_approve_ev": metrics.get("delta_per_approve_ev", 0),
                "verdict": metrics.get("verdict"),
            }
        tiles.append(base)

    payload = {
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "bot_version": EXPECTED_BOT_VERSION,
        "benchmark_lane": BENCHMARK_LANE,
        "benchmark_profile_id": "PRIMARY_PRODUCTION_v1",
        "session_scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lanes": tiles,
    }
    try:
        with open(PATHWAY_LANE_SPECS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  ✅ Wrote {PATHWAY_LANE_SPECS_FILE} ({len(tiles)} lane tiles) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as e:
        print(f"  ⚠️ Could not write {PATHWAY_LANE_SPECS_FILE}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


def fill_distance_report():
    """Histogram missed_by_usd from expired orders + fill_quality.jsonl."""
    print(f"\n=== FILL DISTANCE REPORT — {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    rows = []
    fq_path = FILL_QUALITY_JSONL_FILE
    if os.path.exists(fq_path):
        rows.extend(_load_jsonl_rows(fq_path))
        print(f"  fill_quality.jsonl rows: {len(rows)} {PIPELINE_ENFORCEMENT_TAG}")
    if os.path.exists(EXPIRED_ORDERS_FILE):
        try:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="utf-8")
        except UnicodeDecodeError:
            exp = pd.read_csv(EXPIRED_ORDERS_FILE, encoding="latin1")
        if not exp.empty:
            seen_ids = {r.get("trade_id") for r in rows}
            for _, r in exp.iterrows():
                tid = r.get("trade_id")
                if tid in seen_ids:
                    continue
                rows.append(r.to_dict())
            print(f"  expired_orders CSV rows: {len(exp)} {PIPELINE_ENFORCEMENT_TAG}")
    if not rows:
        print(f"  No fill distance data yet — restart bot on {EXPECTED_BOT_VERSION} to populate. {PIPELINE_ENFORCEMENT_TAG}")
        return
    for r in rows:
        inferred = _infer_fill_missed_by_usd(r)
        if inferred is not None:
            r["missed_by_usd"] = inferred
    missed = pd.to_numeric(pd.Series([r.get("missed_by_usd") for r in rows]), errors="coerce").dropna()
    touched = sum(1 for r in rows if _truthy(r.get("touched_limit")))
    if len(missed) == 0:
        print(f"  samples=0 — no inferrable missed_by_usd; bucketing by expiry reason {PIPELINE_ENFORCEMENT_TAG}")
        reason_counts = pd.Series([r.get("reason") or "unknown" for r in rows]).value_counts()
        print(reason_counts.to_string())
        return
    print(f"  samples={len(missed)} touched_limit={touched} avg_miss=${missed.mean():.2f} median=${missed.median():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    buckets = [
        ("0 (touched)", 0, 0.001),
        ("$0–1", 0.001, 1),
        ("$1–5", 1, 5),
        ("$5–10", 5, 10),
        ("$10–25", 10, 25),
        ("$25+", 25, 99999),
    ]
    hist_rows = []
    for label, lo, hi in buckets:
        sub = missed[(missed >= lo) & (missed < hi)]
        if label.startswith("0 (touched)"):
            sub = missed[missed <= 0]
        if sub.empty:
            continue
        hist_rows.append({"bucket": label, "n": len(sub), "pct": round(100 * len(sub) / len(missed), 1)})
    if hist_rows:
        print(pd.DataFrame(hist_rows).to_string(index=False))


def shadow_vs_live_fill_audit(blocked_df=None):
    """Compare signal_snapshot executed=false vs shadow_outcome; tabulate block reasons."""
    print(f"\n=== SHADOW vs LIVE FILL AUDIT — {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    snapshots = _load_jsonl_by_trade_id(SIGNAL_SNAPSHOT_FILE)
    if not snapshots:
        print(f"  No signal_snapshot.jsonl yet. {PIPELINE_ENFORCEMENT_TAG}")
        return
    shadow = _load_jsonl_by_trade_id(SHADOW_OUTCOME_FILE)
    block_map = _blocked_reason_by_trade_id(blocked_df)
    not_executed = []
    for tid, snap in snapshots.items():
        if snap.get("executed") is True:
            continue
        reason = block_map.get(tid) or snap.get("block_reason") or "never_submitted"
        not_executed.append({
            "trade_id": tid,
            "reason": reason,
            "category": _classify_fill_block_reason(reason),
            "has_shadow": tid in shadow,
            "shadow_pnl": (shadow.get(tid) or {}).get("net_pnl_usd"),
            "research_lane": snap.get("research_lane"),
        })
    if not not_executed:
        print(f"  All snapshots marked executed — nothing to audit. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = pd.DataFrame(not_executed)
    print(f"  non-executed APPROVE snapshots: {len(df)} | with shadow_outcome: {int(df['has_shadow'].sum())} {PIPELINE_ENFORCEMENT_TAG}")
    cat = df.groupby("category", observed=True).agg(
        n=("trade_id", "count"),
        with_shadow=("has_shadow", "sum"),
    ).reset_index()
    print("\n  --- Block reason categories ---")
    print(cat.to_string(index=False))
    top = df["reason"].value_counts().head(12)
    if not top.empty:
        print("\n  --- Top raw block reasons ---")
        print(top.to_string())
    shadowed = df[df["has_shadow"]]
    if not shadowed.empty:
        pnl = pd.to_numeric(shadowed["shadow_pnl"], errors="coerce").dropna()
        if not pnl.empty:
            print(f"\n  Shadow PnL on blocked paths: n={len(pnl)} sum=${pnl.sum():.2f} avg=${pnl.mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")


def shadow_vs_live_entry_report():
    """Summarize approve-time shadow price vs live limit from bot jsonl/report."""
    print(f"\n=== SHADOW vs LIVE ENTRY — {ANALYZER_SYNC_ID} {PIPELINE_ENFORCEMENT_TAG} ===")
    report_path = SHADOW_VS_LIVE_ENTRY_REPORT_FILE
    if os.path.isfile(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                rep = json.load(f)
            print(
                f"  report n={rep.get('sample_count')} avg_delta=${rep.get('avg_delta_usd')} "
                f"median=${rep.get('median_delta_usd')} signed_avg=${rep.get('avg_signed_delta_usd')} "
                f"{PIPELINE_ENFORCEMENT_TAG}"
            )
            by_lane = rep.get("by_lane") or {}
            if by_lane:
                lane_rows = [{"lane": k, **v} for k, v in by_lane.items()]
                print(pd.DataFrame(lane_rows).to_string(index=False))
            return
        except Exception as e:
            print(f"  ⚠️ Could not read {report_path}: {e} {PIPELINE_ENFORCEMENT_TAG}")
    rows = _load_jsonl_rows(SHADOW_VS_LIVE_ENTRY_FILE) if os.path.exists(SHADOW_VS_LIVE_ENTRY_FILE) else []
    if not rows:
        print(f"  No {SHADOW_VS_LIVE_ENTRY_FILE} yet — restart bot on {EXPECTED_BOT_VERSION}. {PIPELINE_ENFORCEMENT_TAG}")
        return
    df = pd.DataFrame(rows)
    df["delta_usd"] = pd.to_numeric(df.get("delta_usd"), errors="coerce")
    print(f"  jsonl rows: {len(df)} avg_delta=${df['delta_usd'].mean():.2f} {PIPELINE_ENFORCEMENT_TAG}")
    if "research_lane" in df.columns:
        print(df.groupby("research_lane", observed=True)["delta_usd"].agg(["count", "mean"]).to_string())


def _count_json_reports_written():
    return sum(1 for f in ANALYZER_JSON_REPORT_FILES if os.path.isfile(f))


def _best_worst_confidence_bands(ai_cal, conf_band):
    """Pick best/worst WR buckets with minimum sample."""
    candidates = []
    for src in (ai_cal.get("confidence_buckets") or [], conf_band.get("filled_trades_by_band") or conf_band.get("bands") or []):
        for b in src:
            bucket = b.get("bucket") or b.get("confidence_bucket")
            trades = int(b.get("trades") or 0)
            wr = b.get("win_rate_pct")
            pnl = b.get("sum_pnl_usd") or b.get("sum_pnl") or 0
            if bucket and trades >= 3 and wr is not None:
                candidates.append({"bucket": bucket, "trades": trades, "wr": float(wr), "pnl": float(pnl or 0)})
    if not candidates:
        return None, None
    best = max(candidates, key=lambda x: (x["wr"], x["pnl"]))
    worst = min(candidates, key=lambda x: (x["wr"], x["pnl"]))
    return best, worst


def _best_worst_lanes(bench):
    lanes = bench.get("lanes") or {}
    ranked = []
    for lane in BENCHMARK_LANES:
        m = lanes.get(lane) or {}
        pnl = m.get("net_pnl_real")
        if pnl is None:
            pnl = m.get("net_pnl_usd")
        if pnl is None:
            continue
        ranked.append({
            "lane": lane,
            "pnl": float(pnl),
            "fills": int(m.get("real_fills") or m.get("fills") or 0),
            "approves": int(m.get("approves") or 0),
        })
    if not ranked:
        return None, None
    return max(ranked, key=lambda x: x["pnl"]), min(ranked, key=lambda x: x["pnl"])


def _fmt_row(cells, widths):
    parts = []
    for cell, w in zip(cells, widths):
        s = str(cell) if cell is not None else ""
        if len(s) > w:
            s = s[: max(0, w - 1)] + "…"
        parts.append(s.ljust(w))
    return "  ".join(parts)


def _exit_mix_from_df(analysis_df):
    if analysis_df is None or analysis_df.empty or "exit_reason" not in analysis_df.columns:
        return []
    pnl = pd.to_numeric(analysis_df.get("net_pnl_usd", 0), errors="coerce")
    work = analysis_df.assign(_pnl=pnl)
    rows = []
    for reason, sub in work.groupby("exit_reason", observed=True):
        n = len(sub)
        s = float(sub["_pnl"].sum())
        wr = float((sub["_pnl"] > 0).mean() * 100) if n else 0.0
        rows.append((str(reason), n, round(wr, 1), round(s, 2)))
    rows.sort(key=lambda x: -x[1])
    return rows


def _lane_table_rows(bench):
    lanes = bench.get("lanes") or {}
    rows = []
    for lane in BENCHMARK_LANES:
        m = lanes.get(lane) or {}
        pnl = m.get("net_pnl_real", m.get("net_pnl_usd"))
        if pnl is None and not m.get("approves"):
            continue
        rows.append({
            "lane": lane,
            "approves": int(m.get("approves") or 0),
            "fills": int(m.get("real_fills") or m.get("fills") or 0),
            "fill_pct": round(float(m.get("approve_to_fill_pct") or 0), 0),
            "pnl": round(float(pnl or 0), 2),
            "ev": round(float(m.get("per_approve_ev") or 0), 2),
        })
    return rows


def _assess_research_confidence(n_trades, n_approves):
    """Return (status, note) for sample sufficiency."""
    if n_trades >= 200 and n_approves >= MIN_APPROVES_FOR_EDGE_CONCLUSIONS:
        return "GOOD", f"{n_trades} trades / {n_approves} approves — adequate for lane and edge conclusions."
    if n_trades >= MIN_APPROVES_FOR_EDGE_CONCLUSIONS:
        return "MODERATE", (
            f"{n_trades} trades — directional only; prefer ≥200 trades before strong edge/lane claims."
        )
    return "POOR", (
        f"Only {n_trades} trades / {n_approves} approves — treat calibration, edge, and lane verdicts as noisy."
    )


def _band_counts(rows, bucket_key="bucket", trade_key="trades"):
    out = {}
    for b in rows or []:
        if not isinstance(b, dict):
            continue
        key = b.get(bucket_key) or b.get("confidence_bucket")
        if key:
            out[str(key)] = int(b.get(trade_key) or 0)
    return out


def _edge_decile_counts(edge_val):
    out = {}
    for d in edge_val.get("deciles") or []:
        key = d.get("edge_bucket") or d.get("decile") or d.get("bucket")
        if key:
            out[str(key)] = int(d.get("trades") or d.get("n") or 0)
    return out


def _blocked_opportunity_usd(missed_list, real_edge):
    total = 0.0
    for row in missed_list or []:
        try:
            total += float(row.get("missed_profit_usd") or 0)
        except (TypeError, ValueError):
            pass
    if total:
        return round(total, 2)
    return round(float(real_edge.get("blocked_shadow_pnl_usd") or 0), 2)


def _best_exit_from_mix(exit_mix):
    if not exit_mix:
        return None
    return max(exit_mix, key=lambda x: float(x.get("pnl_usd") or 0))


def _worst_exit_from_mix(exit_mix):
    losers = [x for x in exit_mix if float(x.get("pnl_usd") or 0) < 0]
    if not losers:
        return None
    return min(losers, key=lambda x: float(x.get("pnl_usd") or 0))


def _generate_research_findings(payload):
    """Rule-based conclusions with explicit sample-size caveats."""
    p = payload.get("performance") or {}
    re = payload.get("real_edge") or {}
    bench = payload.get("benchmark") or {}
    bl = (bench.get("best_lane") or {})
    wl = (bench.get("worst_lane") or {})
    bc = payload.get("best_confidence") or {}
    wc = payload.get("worst_confidence") or {}
    sc = payload.get("scenario_c") or {}
    exit_mix = payload.get("exit_mix") or []
    edge_val = payload.get("edge_validation") or {}
    cov = payload.get("coverage") or {}
    conf_status = cov.get("confidence_status", "POOR")
    net = float(p.get("net_pnl_usd") or 0)
    findings = []

    if bl.get("lane") and net:
        share = 100.0 * float(bl.get("pnl") or 0) / net if net else 0
        findings.append(
            f"{bl['lane']} lane accounts for {share:.0f}% of net profit (${float(bl.get('pnl', 0)):+.2f})."
        )
    if wl.get("lane"):
        findings.append(
            f"Lowest lane: {wl['lane']} at ${float(wl.get('pnl', 0)):+.2f} "
            f"({int(wl.get('fills', 0))} fills) — review if sample grows."
        )
    if bc.get("bucket"):
        findings.append(
            f"Best confidence band {bc['bucket']}: {bc.get('wr', 0):.1f}% WR over {bc.get('trades', 0)} trades."
        )
    if wc.get("bucket") and wc.get("bucket") != bc.get("bucket"):
        findings.append(
            f"Weakest confidence band {wc['bucket']}: {wc.get('wr', 0):.1f}% WR over {wc.get('trades', 0)} trades."
        )
    ai_v = (payload.get("ai_calibration") or {}).get("verdict")
    if ai_v:
        findings.append(f"AI calibration: {ai_v}.")

    fc_n = int(sc.get("fast_cut_trades") or 0)
    fc_sum = sc.get("fast_cut_summary") or {}
    fc_loss = sum(float(x.get("pnl_usd") or 0) for x in exit_mix if "FAST_CUT" in str(x.get("reason", "")))
    if fc_n:
        findings.append(
            f"Fast-cut exits: {fc_n} trades, ${fc_loss:+.2f} booked"
            f" — {'not a major leak' if fc_loss > -10 else 'review thesis cut threshold'}."
        )
    else:
        findings.append("No fast-cut exits in sample.")

    best_exit = _best_exit_from_mix(exit_mix)
    if best_exit:
        findings.append(
            f"Dominant exit: {best_exit.get('reason')} "
            f"({best_exit.get('n')} trades, ${float(best_exit.get('pnl_usd', 0)):+.2f})."
        )

    gate = float(re.get("gate_damage_usd") or 0)
    if abs(gate) < 5:
        findings.append(f"Gate damage ${gate:+.2f} — post-AI gates are not the main PnL leak.")
    else:
        findings.append(f"Gate damage ${gate:+.2f} — investigate blocked APPROVEs.")

    left = sc.get("leakage_left_usd")
    if left is not None:
        findings.append(
            f"Scenario C left ${float(left):.1f} on table vs peak — exit timing is the bigger lever than entry gates."
        )

    edge_verdict = payload.get("edge_verdict")
    corr = (edge_val.get("overall") or {}).get("correlation_edge_vs_pnl")
    if edge_verdict:
        caveat = ""
        if conf_status != "GOOD":
            caveat = f" (sample {conf_status} — {cov.get('confidence_note', '')})"
        findings.append(f"Edge filter: {edge_verdict}{caveat}.")
    if corr is not None:
        findings.append(f"Edge score correlation with PnL: {corr:+.3f}.")

    ch = payload.get("chase") or {}
    if ch.get("total_fills"):
        findings.append(
            f"Chase assisted {ch.get('assisted_fills', 0)}/{ch.get('total_fills', 0)} fills — "
            "3-5 chase bucket historically strongest EV in this run."
        )

    blocked_usd = payload.get("blocked_opportunity_usd")
    if blocked_usd is not None:
        findings.append(f"Blocked shadow opportunity (heuristic): ${float(blocked_usd):+.2f}.")

    top_leak = payload.get("top_leakage") or {}
    if top_leak.get("overall_left_usd"):
        findings.append(
            f"Top leakage: ${float(top_leak['overall_left_usd']):.0f} left on table across "
            f"{len(top_leak.get('trades') or [])} ranked trades — see top_leakage_report.json."
        )
        by_ex = top_leak.get("by_exit_reason") or {}
        if by_ex:
            worst_ex = max(by_ex.items(), key=lambda x: x[1].get("left_usd", 0))
            findings.append(
                f"Biggest leak by exit type: {worst_ex[0]} (${worst_ex[1].get('left_usd', 0):.0f} left)."
            )

    recovery = payload.get("recovery_summary") or []
    for h in recovery:
        if h.get("horizon") == "30m" and h.get("recovery_rate_pct") is not None:
            findings.append(
                f"Loser recovery @30m: {h['recovery_rate_pct']}% would have been green "
                f"(fast-cut too aggressive if high)."
            )
            break

    lane_ret = payload.get("lane_retirement") or {}
    retire = lane_ret.get("retire_candidates") or []
    if retire:
        findings.append(f"Lane retirement candidates: {', '.join(retire)}.")
    else:
        keep_testing = [
            r["lane"] for r in (lane_ret.get("lanes") or [])
            if r.get("recommendation") in ("KEEP TESTING", "COLLECT MORE")
        ]
        if keep_testing:
            findings.append(f"Lanes needing more data: {', '.join(keep_testing[:3])}.")

    feat = payload.get("feature_importance") or {}
    weak = feat.get("weak_signals") or []
    top_feat = (feat.get("features") or [{}])[0] if feat.get("features") else {}
    if top_feat:
        findings.append(
            f"Strongest PnL predictor: {top_feat.get('feature')} "
            f"(r={top_feat.get('correlation_with_pnl'):+.3f})."
        )
    if weak:
        findings.append(f"Weak / dead signals (|r|<0.05): {', '.join(weak)}.")

    if conf_status == "POOR":
        findings.append(
            "⚠ Statistical confidence POOR — do not change bot thresholds from this run alone."
        )
    elif conf_status == "MODERATE":
        findings.append(
            "⚠ Statistical confidence MODERATE — use for direction, confirm over ≥200 trades."
        )

    return findings[:12]


def _mirror_reports_to_dir():
    """Copy JSON reports into reports/ for one-stop deep dive."""
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        for _title, fname, _desc in DEEP_DIVE_REPORT_CATALOG:
            if os.path.isfile(fname):
                shutil.copy2(fname, os.path.join(REPORTS_DIR, os.path.basename(fname)))
        return True
    except Exception:
        return False


def write_report_manifest(payload=None):
    """Manifest for research dashboard — no hardcoded report list in UI."""
    reports = []
    for title, fname, desc in DEEP_DIVE_REPORT_CATALOG:
        if os.path.isfile(fname):
            reports.append({
                "title": title,
                "file": fname,
                "category": _manifest_category(title),
                "description": desc,
                "size_bytes": os.path.getsize(fname),
                "modified_at": datetime.fromtimestamp(
                    os.path.getmtime(fname), tz=timezone.utc
                ).isoformat(),
            })
    manifest = {
        "schema": "report_manifest_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "data_scope": (payload or {}).get("data_scope"),
        "session_scope": (payload or {}).get("session_scope"),
        "performance": (payload or {}).get("performance"),
        "report_count": len(reports),
        "reports": reports,
        "text_artifacts": [
            EXECUTIVE_SUMMARY_FILE,
            RESEARCH_HIGHLIGHTS_FILE,
            RESEARCH_FINDINGS_FILE,
            RESEARCH_COVERAGE_FILE,
            DEEP_DIVE_INDEX_FILE,
            ANALYSIS_DASHBOARD_HTML,
            ANALYZER_RUN_LOG_FILE,
            RESEARCH_COMPACT_SUMMARY_FILE,
        ],
        "research_dashboard_url": os.getenv(
            "RESEARCH_DASHBOARD_PUBLIC_URL", "http://10.0.0.102:9001/"
        ),
    }
    try:
        with open(REPORT_MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception as exc:
        print(f"  ⚠️ Could not write {REPORT_MANIFEST_FILE}: {exc} {PIPELINE_ENFORCEMENT_TAG}")
    return manifest


def _manifest_category(title: str) -> str:
    t = title.lower()
    if "ai" in t or "confidence" in t or "fingerprint" in t:
        return "AI"
    if "lane" in t or "benchmark" in t or "pathway" in t:
        return "Pathways"
    if "chase" in t:
        return "Chase"
    if "scenario" in t or "fast" in t or "horizon" in t:
        return "Exits"
    if "edge" in t:
        return "Edge"
    if "missed" in t or "funnel" in t or "direction" in t:
        return "Funnels"
    return "Reports"


def archive_research_session(payload):
    """Store snapshot of this analyzer run for session comparison."""
    try:
        os.makedirs(SESSION_ARCHIVE_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        seq = len(glob.glob(os.path.join(SESSION_ARCHIVE_DIR, "session_*"))) + 1
        folder = os.path.join(SESSION_ARCHIVE_DIR, f"session_{seq:03d}_{stamp}")
        os.makedirs(folder, exist_ok=True)
        p = payload.get("performance") or {}
        meta = {
            "schema": "research_session_archive_v1",
            "session_id": os.path.basename(folder),
            "generated_at": payload.get("generated_at"),
            "analyzer_sync_id": payload.get("analyzer_sync_id"),
            "data_scope": payload.get("data_scope"),
            "trades": p.get("trades"),
            "net_pnl_usd": p.get("net_pnl_usd"),
            "win_rate_pct": p.get("win_rate_pct"),
        }
        with open(os.path.join(folder, "session_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        for name in (
            EXECUTIVE_SUMMARY_FILE,
            RESEARCH_HIGHLIGHTS_FILE,
            RESEARCH_FINDINGS_FILE,
            RESEARCH_COVERAGE_FILE,
            RESEARCH_COMPACT_SUMMARY_FILE,
            REPORT_MANIFEST_FILE,
        ):
            if os.path.isfile(name):
                shutil.copy2(name, os.path.join(folder, name))
        if os.path.isdir(REPORTS_DIR):
            shutil.copytree(REPORTS_DIR, os.path.join(folder, "reports"), dirs_exist_ok=True)
        index = {"sessions": []}
        if os.path.isfile(SESSION_ARCHIVE_INDEX_FILE):
            try:
                with open(SESSION_ARCHIVE_INDEX_FILE, encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                pass
        index.setdefault("sessions", []).insert(0, meta)
        index["sessions"] = index["sessions"][:50]
        with open(SESSION_ARCHIVE_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
        return folder
    except Exception as exc:
        print(f"  ⚠️ Session archive failed: {exc} {PIPELINE_ENFORCEMENT_TAG}")
        return None


def build_executive_summary_payload(
    session=None,
    trades=None,
    analysis_df=None,
    decisions=None,
    blocked=None,
    dataset_counts=None,
    data_scope="all",
):
    """Aggregate KPIs from analysis_df + JSON reports (written during same run)."""
    session = session or load_research_session()
    scope = "ALL-DATA" if data_scope == "all" else _shadow_scope_label(session)
    hours = _session_hours(session) if data_scope == "session" else None

    n_trades = 0
    wr = net = ev = None
    exit_mix = []
    if analysis_df is not None and not analysis_df.empty:
        n_trades = len(analysis_df)
        pnl_s = pd.to_numeric(analysis_df.get("net_pnl_usd", 0), errors="coerce")
        wr = float((pnl_s > 0).mean() * 100)
        net = float(pnl_s.sum())
        ev = float(net / n_trades) if n_trades else 0.0
        exit_mix = _exit_mix_from_df(analysis_df)

    real_edge = _load_json_report(REAL_EDGE_SUMMARY_FILE)
    bench = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    ai_cal = _load_json_report(AI_CALIBRATION_REPORT_FILE)
    conf_band = _load_json_report(CONFIDENCE_BAND_REPORT_FILE)
    chase = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    leakage = _load_json_report(SCENARIO_C_LEAKAGE_REPORT_FILE)
    capture = _load_json_report(SCENARIO_C_CAPTURE_RATIO_REPORT_FILE)
    missed = _load_json_report(MISSED_OPPORTUNITY_HEATMAP_FILE)
    horizon = _load_json_report(HORIZON_PROFITABILITY_REPORT_FILE)
    fast_cut = _load_json_report(FAST_CUT_SURVIVOR_REPORT_FILE)
    edge_inc = _load_json_report(EDGE_INCREMENTAL_VALUE_REPORT_FILE)
    edge_val = _load_json_report(EDGE_SCORE_DECILE_REPORT_FILE)
    dir_rep = _load_json_report(DIRECTION_REPORT_FILE)
    chase_buckets = _load_json_report(CHASE_EFFECTIVENESS_REPORT_FILE)
    top_leak = _load_json_report(TOP_LEAKAGE_REPORT_FILE)
    lane_ret = _load_json_report(LANE_RETIREMENT_REPORT_FILE)
    feat_imp = _load_json_report(FEATURE_IMPORTANCE_REPORT_FILE)

    best_lane, worst_lane = _best_worst_lanes(bench)
    lane_rows = _lane_table_rows(bench)
    best_conf, worst_conf = _best_worst_confidence_bands(ai_cal, conf_band)

    chase_tot = chase.get("overnight_watch") or chase.get("totals") or {}
    chase_fills = int(chase_tot.get("chase_assisted_fills") or 0)
    total_fills = int(chase_tot.get("total_fills") or chase_tot.get("trades_with_orders") or 0)

    leak_overall = leakage.get("overall") or {}
    left_usd = leak_overall.get("left_on_table_usd") or leak_overall.get("left_usd")
    if left_usd is None:
        peak = leak_overall.get("peak_profit_usd") or leak_overall.get("peak_usd")
        booked = leak_overall.get("booked_profit_usd") or leak_overall.get("booked_usd")
        if peak is not None and booked is not None:
            left_usd = float(peak) - float(booked)

    missed_rows = missed.get("heatmap") or missed.get("rows") or missed.get("buckets") or []
    if isinstance(missed_rows, dict):
        missed_rows = list(missed_rows.values())
    top_missed_list = []
    for row in missed_rows[:5]:
        if isinstance(row, dict) and row.get("reason"):
            top_missed_list.append({
                "reason": row.get("reason"),
                "missed_profit_usd": row.get("missed_profit_usd"),
                "count": row.get("count"),
            })

    mfe_cap = (capture.get("overall_mfe_positive") or {}).get("aggregate_capture_pct")
    cap_dist = capture.get("capture_distribution") or {}
    ai_bands = ai_cal.get("confidence_buckets") or ai_cal.get("confidence_bands") or ai_cal.get("bands") or []
    conf_bands = (
        conf_band.get("filled_trades_by_band")
        or conf_band.get("bands")
        or conf_band.get("confidence_bands")
        or []
    )
    eva = ai_cal.get("expected_vs_actual") or {}
    exp_wr = eva.get("overall_expected_wr_pct")
    act_wr = eva.get("overall_actual_wr_pct")
    if exp_wr is not None and act_wr is not None:
        if act_wr > exp_wr + 5:
            ai_verdict = f"under-confident (actual {act_wr}% vs AI {exp_wr}% implied)"
        elif act_wr < exp_wr - 5:
            ai_verdict = f"over-confident (actual {act_wr}% vs AI {exp_wr}% implied)"
        else:
            ai_verdict = "well-calibrated"
    else:
        ai_verdict = None

    pathway_specs = _load_json_report(PATHWAY_LANE_SPECS_FILE) or {}
    bench_profile = (
        bench.get("benchmark_profile_id")
        or pathway_specs.get("benchmark_profile_id")
        or "CONTINUOUS_SCENARIO_C_v2"
    )

    chase_bucket_rows = []
    raw_buckets = chase_buckets.get("buckets") or {}
    if isinstance(raw_buckets, dict):
        for key, b in raw_buckets.items():
            if int((b or {}).get("trades") or 0):
                chase_bucket_rows.append({"bucket": key, **(b or {})})
    elif isinstance(raw_buckets, list):
        chase_bucket_rows = raw_buckets[:6]

    n_approves = int(real_edge.get("approve_attempts") or 0)
    n_blocked = int(real_edge.get("blocked") or 0)
    if not n_blocked and blocked is not None and not getattr(blocked, "empty", True):
        n_blocked = len(blocked)
    n_decisions = len(decisions) if decisions is not None and not getattr(decisions, "empty", True) else 0
    conf_status, conf_note = _assess_research_confidence(n_trades, n_approves)
    conf_band_counts = _band_counts(conf_bands)
    ai_band_counts = _band_counts(ai_bands)
    edge_bucket_counts = _edge_decile_counts(edge_val)
    blocked_opp = _blocked_opportunity_usd(top_missed_list, real_edge)
    exit_mix_dicts = [{"reason": r, "n": n, "wr_pct": w, "pnl_usd": p} for r, n, w, p in exit_mix]
    best_exit = _best_exit_from_mix(exit_mix_dicts)
    worst_exit = _worst_exit_from_mix(exit_mix_dicts)
    fc_sum = fast_cut.get("summary") or {}
    fast_cut_damage = round(
        sum(float(x.get("pnl_usd") or 0) for x in exit_mix_dicts if "FAST_CUT" in str(x.get("reason", ""))),
        2,
    )

    payload = {
        "schema": "research_hierarchy_v1",
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "analyzer_version": ANALYZER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_scope": data_scope,
        "session_scope": scope,
        "session_hours": round(float(hours), 2) if hours is not None else None,
        "dataset": dataset_counts or {},
        "performance": {
            "trades": n_trades,
            "win_rate_pct": round(wr, 1) if wr is not None else None,
            "net_pnl_usd": round(net, 2) if net is not None else None,
            "expectancy_usd": round(ev, 2) if ev is not None else None,
            "mfe_capture_pct": mfe_cap,
        },
        "real_edge": real_edge,
        "benchmark": {
            "profile_id": bench_profile,
            "lanes": lane_rows,
            "best_lane": best_lane,
            "worst_lane": worst_lane,
        },
        "exit_mix": exit_mix_dicts,
        "highlights": {
            "best_lane": best_lane,
            "worst_lane": worst_lane,
            "best_confidence": best_conf,
            "worst_confidence": worst_conf,
            "fast_cut_damage_usd": fast_cut_damage,
            "blocked_opportunity_usd": blocked_opp,
            "edge_correlation": (edge_val.get("overall") or {}).get("correlation_edge_vs_pnl"),
            "best_exit": best_exit,
            "worst_exit": worst_exit,
            "gate_damage_usd": round(float(real_edge.get("gate_damage_usd") or 0), 2),
            "leakage_left_usd": round(float(left_usd), 2) if left_usd is not None else None,
        },
        "coverage": {
            "trades": n_trades,
            "approves": n_approves,
            "blocked": n_blocked,
            "decisions": n_decisions,
            "confidence_bands": conf_band_counts,
            "ai_confidence_bands": ai_band_counts,
            "edge_buckets": edge_bucket_counts,
            "confidence_status": conf_status,
            "confidence_note": conf_note,
        },
        "ai_calibration": {
            "verdict": ai_verdict,
            "bands": ai_bands[:8],
            "expected_vs_actual": eva,
        },
        "confidence_bands": conf_bands[:8],
        "best_confidence": best_conf,
        "worst_confidence": worst_conf,
        "chase": {
            "assisted_fills": chase_fills,
            "total_fills": total_fills,
            "saved_fills": int(chase_tot.get("saved_fills_heuristic") or 0),
            "ttl_expired": int(chase_tot.get("ttl_expired") or 0),
            "buckets": chase_bucket_rows[:6],
        },
        "scenario_c": {
            "leakage_left_usd": round(float(left_usd), 2) if left_usd is not None else None,
            "capture_pct": mfe_cap,
            "capture_distribution": cap_dist,
            "fast_cut_trades": fc_sum.get("fast_cut_trades", 0),
            "fast_cut_summary": fc_sum,
        },
        "horizon_profitability": horizon.get("horizons") or {},
        "top_missed_opportunities": top_missed_list,
        "direction": dir_rep.get("summary") or dir_rep,
        "edge_verdict": edge_inc.get("verdict"),
        "edge_validation": edge_val,
        "top_leakage": top_leak,
        "lane_retirement": lane_ret,
        "feature_importance": feat_imp,
        "recovery_summary": horizon.get("recovery_summary") or [],
        "blocked_opportunity_usd": blocked_opp,
        "json_reports_written": _count_json_reports_written(),
        "artifacts": {
            "executive_summary_txt": os.path.abspath(EXECUTIVE_SUMMARY_FILE),
            "research_highlights_txt": os.path.abspath(RESEARCH_HIGHLIGHTS_FILE),
            "research_findings_txt": os.path.abspath(RESEARCH_FINDINGS_FILE),
            "research_coverage_txt": os.path.abspath(RESEARCH_COVERAGE_FILE),
            "deep_dive_index_txt": os.path.abspath(DEEP_DIVE_INDEX_FILE),
            "analysis_dashboard_html": os.path.abspath(ANALYSIS_DASHBOARD_HTML),
            "reports_dir": os.path.abspath(REPORTS_DIR),
            "full_log": os.path.abspath(ANALYZER_RUN_LOG_FILE),
        },
    }
    payload["key_findings"] = _generate_research_findings(payload)
    return payload


def _fmt_usd(val, default="n/a"):
    if val is None:
        return default
    try:
        return f"{float(val):+.2f}"
    except (TypeError, ValueError):
        return default


def format_executive_summary_short(payload):
    """Layer 1 — ~15 lines: KPIs + funnel + pointers to deeper artifacts."""
    p = payload.get("performance") or {}
    re = payload.get("real_edge") or {}
    cov = payload.get("coverage") or {}
    ds = payload.get("dataset") or {}
    scope = payload.get("session_scope", "ALL-DATA")
    hours = payload.get("session_hours")
    time_label = f"~{hours:.1f}h bot session" if hours else "full CSV history"
    csv_n = ds.get("csv_trades", p.get("trades", 0))
    analyzed = p.get("trades", 0)

    lines = [
        "=" * 72,
        f"=== EXECUTIVE SUMMARY — {scope} ({time_label}) | {payload.get('analyzer_sync_id', ANALYZER_SYNC_ID)} ===",
        "=" * 72,
        f"Data scope: {payload.get('data_scope', 'all').upper()} | CSV trades: {csv_n} | Analyzed fills: {analyzed}",
        f"Trades {analyzed} | WR {p.get('win_rate_pct', 'n/a')}% | PnL {_fmt_usd(p.get('net_pnl_usd'))} | "
        f"EV {p.get('expectancy_usd', 'n/a')}/trade | MFE capture {p.get('mfe_capture_pct', 'n/a')}%",
        f"APPROVE {re.get('approve_attempts', 'n/a')} → executed {re.get('executed', 'n/a')} | "
        f"Gate damage ${_fmt_usd(re.get('gate_damage_usd'))} | Edge: {payload.get('edge_verdict', 'n/a')}",
        f"Sample confidence: {cov.get('confidence_status', 'n/a')} — {cov.get('confidence_note', '')}",
        "",
        "Next layers (same run):",
        f"  • {RESEARCH_HIGHLIGHTS_FILE} — detailed tables",
        f"  • {RESEARCH_FINDINGS_FILE} — auto conclusions",
        f"  • {RESEARCH_COVERAGE_FILE} — bucket counts & statistical health",
        f"  • {DEEP_DIVE_INDEX_FILE} — all {payload.get('json_reports_written', 0)} JSON reports",
        f"  • {ANALYSIS_DASHBOARD_HTML} | {ANALYZER_RUN_LOG_FILE} (full verbose)",
        "=" * 72,
    ]
    return "\n".join(lines)


def format_research_highlights_text(payload):
    """Layer 2 — detailed tables: lanes, exits, calibration, chase, horizon."""
    W = 112
    p = payload.get("performance") or {}
    re = payload.get("real_edge") or {}
    bench = payload.get("benchmark") or {}
    bl = bench.get("best_lane") or payload.get("best_lane") or {}
    wl = bench.get("worst_lane") or payload.get("worst_lane") or {}
    bc = payload.get("best_confidence") or {}
    wc = payload.get("worst_confidence") or {}
    ch = payload.get("chase") or {}
    sc = payload.get("scenario_c") or {}
    hz = payload.get("horizon_profitability") or {}
    ai_cal = payload.get("ai_calibration") or {}
    conf_bands = payload.get("confidence_bands") or []
    exit_mix = payload.get("exit_mix") or []
    lane_rows = bench.get("lanes") or []
    missed_list = payload.get("top_missed_opportunities") or []
    if not missed_list and payload.get("top_missed_opportunity"):
        missed_list = [payload.get("top_missed_opportunity")]
    direction = payload.get("direction") or {}
    edge_val = payload.get("edge_validation") or {}

    lines = [
        "=" * W,
        f"=== RESEARCH HIGHLIGHTS — {payload.get('session_scope', 'ALL-DATA')} | "
        f"{payload.get('analyzer_sync_id', ANALYZER_SYNC_ID)} ===",
        "=" * W,
        "",
    ]
    hl = payload.get("highlights") or {}
    lines.extend([
        "=== RESEARCH HIGHLIGHTS (at-a-glance) ===",
        f"Top Lane:     {(hl.get('best_lane') or {}).get('lane', 'n/a')}  "
        f"${float((hl.get('best_lane') or {}).get('pnl', 0)):+.2f}",
        f"Worst Lane:   {(hl.get('worst_lane') or {}).get('lane', 'n/a')}  "
        f"${float((hl.get('worst_lane') or {}).get('pnl', 0)):+.2f}",
        f"Best Conf:    {(hl.get('best_confidence') or {}).get('bucket', 'n/a')}  "
        f"{(hl.get('best_confidence') or {}).get('wr', 0):.1f}% WR",
        f"Worst Conf:   {(hl.get('worst_confidence') or {}).get('bucket', 'n/a')}  "
        f"{(hl.get('worst_confidence') or {}).get('wr', 0):.1f}% WR",
        f"Fast-Cut PnL: ${float(hl.get('fast_cut_damage_usd', 0)):+.2f}",
        f"Blocked Opp:  ${float(hl.get('blocked_opportunity_usd', 0)):+.2f}",
        f"Edge corr:    {hl.get('edge_correlation', 'n/a')}",
        f"Best Exit:    {(hl.get('best_exit') or {}).get('reason', 'n/a')}  "
        f"${float((hl.get('best_exit') or {}).get('pnl_usd', 0)):+.2f}",
        "",
    ])
    if re:
        lines.extend([
            "--- APPROVE funnel ---",
            _fmt_row(["APPROVE", "Executed", "Exec PnL", "All-APPROVE CF", "Gate damage"], [10, 10, 12, 16, 14]),
            _fmt_row([
                re.get("approve_attempts", "n/a"),
                re.get("executed", "n/a"),
                f"{re.get('executed_pnl_usd', 0):+.2f}",
                f"{re.get('counterfactual_all_approve_usd', 0):+.2f}",
                f"{re.get('gate_damage_usd', 0):+.2f}",
            ], [10, 10, 12, 16, 14]),
            "",
        ])
    if lane_rows:
        lines.extend([
            f"--- Lanes (profile: {bench.get('profile_id', 'n/a')}) ---",
            _fmt_row(["Lane", "Appr", "Fill", "Fill%", "PnL", "EV/appr"], [22, 6, 6, 7, 10, 10]),
        ])
        for row in lane_rows:
            lines.append(_fmt_row([
                row.get("lane"),
                row.get("approves"),
                row.get("fills"),
                f"{row.get('fill_pct', 0)}%",
                f"{row.get('pnl', 0):+.2f}",
                f"{row.get('ev', 0):+.2f}",
            ], [22, 6, 6, 7, 10, 10]))
        if bl or wl:
            lines.append(
                f"  Best: {bl.get('lane', 'n/a')} ${bl.get('pnl', 0):+.2f} | "
                f"Worst: {wl.get('lane', 'n/a')} ${wl.get('pnl', 0):+.2f}"
            )
        lines.append("")

    if exit_mix:
        lines.extend([
            "--- Exit mix ---",
            _fmt_row(["Exit reason", "N", "WR%", "PnL"], [28, 6, 8, 12]),
        ])
        for row in exit_mix:
            lines.append(_fmt_row([
                row.get("reason"),
                row.get("n"),
                f"{row.get('wr_pct', 0)}%",
                f"{row.get('pnl_usd', 0):+.2f}",
            ], [28, 6, 8, 12]))
        lines.append("")

    ai_bands = ai_cal.get("bands") or []
    if not ai_bands:
        ai_bands = _load_json_report(AI_CALIBRATION_REPORT_FILE).get("confidence_buckets") or []
    if ai_bands:
        lines.extend([
            f"--- AI calibration ({ai_cal.get('verdict', 'see report')}) ---",
            _fmt_row(["Band", "N", "WR%", "PnL"], [10, 6, 8, 12]),
        ])
        for b in ai_bands[:8]:
            if int(b.get("trades") or 0) == 0:
                continue
            lines.append(_fmt_row([
                b.get("bucket"),
                b.get("trades"),
                f"{b.get('win_rate_pct', b.get('wr', 0))}%",
                f"{float(b.get('sum_pnl_usd', b.get('pnl', 0)) or 0):+.2f}",
            ], [10, 6, 8, 12]))
        if bc or wc:
            lines.append(
                f"  Best: {bc.get('bucket', 'n/a')} WR {bc.get('wr', 0):.1f}% | "
                f"Worst: {wc.get('bucket', 'n/a')} WR {wc.get('wr', 0):.1f}%"
            )
        lines.append("")

    if not conf_bands:
        conf_bands = _load_json_report(CONFIDENCE_BAND_REPORT_FILE).get("filled_trades_by_band") or []
    if conf_bands:
        lines.extend([
            "--- Confidence bands (executed) ---",
            _fmt_row(["Band", "N", "WR%", "PnL"], [10, 6, 8, 12]),
        ])
        for b in conf_bands[:8]:
            if int(b.get("trades") or 0) == 0:
                continue
            lines.append(_fmt_row([
                b.get("bucket"),
                b.get("trades"),
                f"{b.get('win_rate_pct', b.get('wr', 0))}%",
                f"{float(b.get('sum_pnl_usd', b.get('pnl', 0)) or 0):+.2f}",
            ], [10, 6, 8, 12]))
        lines.append("")

    lines.extend([
        "--- Chase ---",
        f"Assisted: {ch.get('assisted_fills', 0)}/{ch.get('total_fills', 0)} fills | "
        f"Saved: {ch.get('saved_fills', 0)} | TTL expired: {ch.get('ttl_expired', 0)}",
    ])
    chase_buckets = ch.get("buckets") or []
    if chase_buckets:
        lines.append(_fmt_row(["Chase bucket", "N", "WR%", "PnL", "EV"], [14, 6, 8, 12, 10]))
        for b in chase_buckets:
            lines.append(_fmt_row([
                b.get("bucket"),
                b.get("trades"),
                f"{b.get('win_rate_pct', 0)}%",
                f"{float(b.get('sum_pnl_usd', 0)):+.2f}",
                f"{b.get('ev_usd', 0):+.2f}",
            ], [14, 6, 8, 12, 10]))
    lines.append("")

    cap_dist = sc.get("capture_distribution") or {}
    lines.extend([
        "--- Scenario C / exits ---",
        f"Leakage left on table: ${sc.get('leakage_left_usd', 'n/a')} | "
        f"MFE capture: {sc.get('capture_pct', p.get('mfe_capture_pct', 'n/a'))}% | "
        f"Fast-cut trades: {sc.get('fast_cut_trades', 0)}",
    ])
    if cap_dist:
        if isinstance(cap_dist, dict):
            dist_parts = [f"{k}: {v}" for k, v in list(cap_dist.items())[:6]]
        else:
            dist_parts = [
                f"{b.get('bucket', 'n/a')}: n={b.get('trades', 0)}"
                for b in (cap_dist or [])[:6]
                if isinstance(b, dict)
            ]
        if dist_parts:
            lines.append(f"  Capture distribution: {', '.join(dist_parts)}")
    fc = sc.get("fast_cut_summary") or {}
    if fc.get("fast_cut_trades"):
        lines.append(
            f"  Fast-cut: n={fc.get('fast_cut_trades')} "
            f"saved=${float(fc.get('loss_avoided_usd') or 0):.2f} "
            f"missed=${float(fc.get('missed_ladder_usd') or 0):.2f}"
        )
    lines.extend(["", "--- Horizon (losers → green later?) ---"])
    for label in ("5m", "10m", "15m", "30m"):
        h = hz.get(label) or {}
        if h:
            lines.append(
                f"  {label}: green={h.get('profitable', 0)} still_red={h.get('still_loss', 0)} "
                f"unknown={h.get('unknown', 0)} ({h.get('profitable_pct') if h.get('profitable_pct') is not None else 'n/a'}% of known)"
            )

    if missed_list:
        lines.extend(["", "--- Top missed opportunities ---"])
        for tm in missed_list[:5]:
            lines.append(
                f"  {tm.get('reason', 'n/a')}  +${float(tm.get('missed_profit_usd') or 0):.2f}  (n={tm.get('count', 0)})"
            )

    if direction and isinstance(direction, dict):
        lines.extend(["", "--- Direction ---"])
        for side in ("LONG", "SHORT"):
            s = direction.get(side) or direction.get(side.lower())
            if isinstance(s, dict) and s.get("trades"):
                lines.append(
                    f"  {side}: n={s.get('trades')} WR={s.get('win_rate_pct', s.get('wr', 'n/a'))}% "
                    f"PnL=${float(s.get('sum_pnl_usd', s.get('pnl', 0)) or 0):+.2f}"
                )

    ev_overall = edge_val.get("overall") or {}
    lines.extend([
        "",
        "--- Edge validation ---",
        f"Incremental: {payload.get('edge_verdict', 'n/a')}",
    ])
    if ev_overall:
        lines.append(
            f"  Decile corr(edge,PnL)={ev_overall.get('correlation_edge_vs_pnl', 'n/a')} "
            f"verdict={ev_overall.get('verdict', 'n/a')}"
        )
    deciles = edge_val.get("deciles") or []
    if deciles:
        lines.append(_fmt_row(["Edge decile", "N", "WR%", "EV"], [12, 6, 8, 10]))
        for d in deciles[:6]:
            n_dec = int(d.get("trades", d.get("n")) or 0)
            if n_dec == 0:
                continue
            lines.append(_fmt_row([
                d.get("edge_bucket", d.get("decile", d.get("bucket"))),
                n_dec,
                f"{d.get('win_rate_pct', d.get('wr', 0))}%",
                f"{float(d.get('ev_usd', d.get('ev', 0)) or 0):+.2f}",
            ], [12, 6, 8, 10]))

    lines.extend(["=" * W])
    return "\n".join(lines)


def format_research_coverage_text(payload):
    """Layer 3 — sample sizes, bucket coverage, statistical confidence."""
    cov = payload.get("coverage") or {}
    ds = payload.get("dataset") or {}
    W = 72
    lines = [
        "=" * W,
        "=== RESEARCH COVERAGE & STATISTICAL CONFIDENCE ===",
        "=" * W,
        f"Scope: {payload.get('session_scope', 'ALL-DATA')} ({payload.get('data_scope', 'all').upper()})",
        f"CSV rows — trades: {ds.get('csv_trades', 'n/a')} | blocked: {ds.get('csv_blocked', 'n/a')} | "
        f"decisions: {ds.get('csv_decisions', 'n/a')}",
        f"Analyzed — trades: {cov.get('trades', 0)} | approves: {cov.get('approves', 0)} | "
        f"blocked: {cov.get('blocked', 0)} | decisions: {cov.get('decisions', 0)}",
        "",
        f"Coverage status: {cov.get('confidence_status', 'n/a')}",
        cov.get("confidence_note", ""),
        "",
        "Confidence bands (executed):",
    ]
    for band in CONFIDENCE_BAND_BUCKET_ORDER:
        n = (cov.get("confidence_bands") or {}).get(band, 0)
        lines.append(f"  {band}: n={n}")
    lines.extend(["", "AI calibration bands:"])
    for band in AI_CALIB_REPORT_BUCKET_ORDER:
        n = (cov.get("ai_confidence_bands") or {}).get(band, 0)
        lines.append(f"  {band}: n={n}")
    lines.extend(["", "Edge buckets:"])
    for bucket in EDGE_DECILE_ORDER:
        n = (cov.get("edge_buckets") or {}).get(bucket, 0)
        lines.append(f"  {bucket}: n={n}")
    lines.extend([
        "",
        f"Minimum for edge conclusions: ≥{MIN_APPROVES_FOR_EDGE_CONCLUSIONS} approves",
        "=" * W,
    ])
    return "\n".join(lines)


def format_research_findings_text(payload):
    """Layer 4 — auto-generated human-readable conclusions."""
    findings = payload.get("key_findings") or _generate_research_findings(payload)
    W = 72
    lines = ["=" * W, "=== KEY FINDINGS ===", "=" * W, ""]
    for i, ftxt in enumerate(findings, 1):
        lines.append(f"{i}. {ftxt}")
    lines.extend(["", "=" * W])
    return "\n".join(lines)


def format_deep_dive_index_text(payload):
    """Layer 5 — index of all JSON reports with paths."""
    W = 72
    art = payload.get("artifacts") or {}
    lines = [
        "=" * W,
        "=== DEEP DIVE INDEX — AVAILABLE REPORTS ===",
        "=" * W,
        f"Reports directory: {art.get('reports_dir', os.path.abspath(REPORTS_DIR))}",
        f"Full verbose log:  {art.get('full_log', ANALYZER_RUN_LOG_FILE)}",
        "",
    ]
    for i, (title, fname, desc) in enumerate(DEEP_DIVE_REPORT_CATALOG, 1):
        path = os.path.join(REPORTS_DIR, os.path.basename(fname)) if os.path.isfile(
            os.path.join(REPORTS_DIR, os.path.basename(fname))
        ) else fname
        exists = "✓" if os.path.isfile(fname) else " "
        lines.append(f"{i:2}. [{exists}] {title}")
        lines.append(f"     {os.path.abspath(path)}")
        lines.append(f"     {desc}")
    lines.extend([
        "",
        f"Written this run: {payload.get('json_reports_written', 0)} reports",
        "Run: python analyzer_research_engine_v62.py  (30-min loop + dashboard :9001, fresh-collection session only)",
        "=" * W,
    ])
    return "\n".join(lines)


def format_terminal_status(payload):
    """Terminal layer — bot/analyzer health + critical KPIs only (~40 lines max)."""
    exec_short = format_executive_summary_short(payload)
    findings = (payload.get("key_findings") or [])[:5]
    cov = payload.get("coverage") or {}
    art = payload.get("artifacts") or {}
    lines = [
        exec_short,
        "",
        "=== CRITICAL FINDINGS (top 5) ===",
    ]
    for i, ftxt in enumerate(findings, 1):
        lines.append(f"  {i}. {ftxt}")
    if not findings:
        lines.append("  (run analyzer to populate findings)")
    lines.extend([
        "",
        f"Research Dashboard: {os.getenv('RESEARCH_DASHBOARD_PUBLIC_URL', 'http://10.0.0.102:9001/')}",
        "  (bundled — starts automatically with analyzer_research_engine_v62.py)",
        "",
        "Full reports (not printed here):",
        f"  {RESEARCH_HIGHLIGHTS_FILE} | {RESEARCH_FINDINGS_FILE} | {RESEARCH_COVERAGE_FILE}",
        f"  {DEEP_DIVE_INDEX_FILE} | {REPORTS_DIR}/ | {ANALYZER_RUN_LOG_FILE}",
        f"  Sample confidence: {cov.get('confidence_status', 'n/a')}",
        "=" * 72,
    ])
    return "\n".join(lines)


def write_analysis_dashboard_html(payload):
    """Self-contained HTML dashboard — open in browser instead of scrolling terminal."""
    p = payload.get("performance") or {}
    re = payload.get("real_edge") or {}
    bench = _load_json_report(BENCHMARK_VS_LANES_REPORT_FILE)
    ai_cal = _load_json_report(AI_CALIBRATION_REPORT_FILE)
    chase = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)
    leakage = _load_json_report(SCENARIO_C_LEAKAGE_REPORT_FILE)
    horizon = _load_json_report(HORIZON_PROFITABILITY_REPORT_FILE)
    fast_cut = _load_json_report(FAST_CUT_SURVIVOR_REPORT_FILE)

    def esc(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lane_rows = ""
    for lane in BENCHMARK_LANES:
        m = (bench.get("lanes") or {}).get(lane) or {}
        pnl = m.get("net_pnl_real", m.get("net_pnl_usd", 0))
        lane_rows += (
            f"<tr><td>{esc(lane)}</td><td>{m.get('real_fills', m.get('fills', 0))}</td>"
            f"<td>{m.get('approves', 0)}</td><td>${float(pnl or 0):.2f}</td>"
            f"<td>${float(m.get('per_approve_ev') or 0):.2f}</td></tr>\n"
        )

    cal_rows = ""
    for b in ai_cal.get("confidence_buckets") or []:
        if int(b.get("trades") or 0) == 0:
            continue
        cal_rows += (
            f"<tr><td>{esc(b.get('bucket'))}</td><td>{b.get('trades')}</td>"
            f"<td>{b.get('win_rate_pct')}%</td><td>${float(b.get('sum_pnl_usd') or 0):.2f}</td></tr>\n"
        )

    ch = payload.get("chase") or {}
    hz = horizon.get("horizons") or {}
    hz_rows = ""
    for label in ("5m", "10m", "15m", "30m"):
        h = hz.get(label) or {}
        hz_rows += (
            f"<tr><td>{label}</td><td>{h.get('profitable', 0)}</td><td>{h.get('still_loss', 0)}</td>"
            f"<td>{h.get('unknown', 0)}</td><td>{h.get('profitable_pct', 'n/a')}%</td></tr>\n"
        )

    fc_sum = fast_cut.get("summary") or {}
    leak = leakage.get("overall") or {}
    cov = payload.get("coverage") or {}
    findings = payload.get("key_findings") or []
    findings_html = "".join(f"<li>{esc(f)}</li>" for f in findings[:10])
    report_links = ""
    for title, fname, _desc in DEEP_DIVE_REPORT_CATALOG[:12]:
        rp = os.path.join(REPORTS_DIR, os.path.basename(fname))
        href = rp if os.path.isfile(rp) else fname
        if os.path.isfile(href):
            report_links += f'<li><a href="{esc(href)}">{esc(title)}</a></li>\n'
    scope_meta = payload.get("session_scope", "ALL-DATA")
    if payload.get("session_hours"):
        scope_meta += f" · ~{payload.get('session_hours')}h"
    else:
        ds = payload.get("dataset") or {}
        scope_meta += f" · {ds.get('csv_trades', '?')} CSV trade rows"

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<title>Research Dashboard — {esc(payload.get('generated_at', '')[:19])}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; background: #0f1419; color: #e7ecf1; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .meta {{ color: #8b9aab; font-size: 0.85rem; margin-bottom: 24px; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 28px; }}
  .kpi {{ background: #1a2332; border: 1px solid #2a3544; border-radius: 8px; padding: 14px 20px; min-width: 120px; }}
  .kpi .val {{ font-size: 1.5rem; font-weight: 700; }}
  .kpi .lbl {{ font-size: 0.75rem; color: #8b9aab; text-transform: uppercase; }}
  section {{ margin-bottom: 28px; }}
  h2 {{ font-size: 1rem; border-bottom: 1px solid #2a3544; padding-bottom: 6px; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 900px; font-size: 0.9rem; }}
  th, td {{ border: 1px solid #2a3544; padding: 8px 10px; text-align: left; }}
  th {{ background: #1a2332; }}
  tr:nth-child(even) {{ background: #151c26; }}
  .note {{ color: #8b9aab; font-size: 0.8rem; margin-top: 8px; }}
  a {{ color: #6eb5ff; }}
</style></head><body>
<h1>Research Analysis Dashboard</h1>
<p class="meta">{esc(scope_meta)} · {esc(ANALYZER_SYNC_ID)} · confidence: {esc(cov.get('confidence_status', 'n/a'))}</p>
<div class="kpis">
  <div class="kpi"><div class="lbl">Trades</div><div class="val">{p.get('trades', 0)}</div></div>
  <div class="kpi"><div class="lbl">Win Rate</div><div class="val">{p.get('win_rate_pct', 'n/a')}%</div></div>
  <div class="kpi"><div class="lbl">Net PnL</div><div class="val">${float(p.get('net_pnl_usd') or 0):+.2f}</div></div>
  <div class="kpi"><div class="lbl">Expectancy</div><div class="val">${esc(p.get('expectancy_usd', 'n/a'))}</div></div>
  <div class="kpi"><div class="lbl">MFE Capture</div><div class="val">{esc(p.get('mfe_capture_pct', 'n/a'))}%</div></div>
  <div class="kpi"><div class="lbl">Gate Damage</div><div class="val">${float(re.get('gate_damage_usd') or 0):+.2f}</div></div>
</div>
<section><h2>Lanes</h2><table><tr><th>Lane</th><th>Fills</th><th>Approves</th><th>Real PnL</th><th>EV/Approve</th></tr>{lane_rows}</table></section>
<section><h2>AI Confidence</h2><table><tr><th>Band</th><th>Trades</th><th>WR</th><th>PnL</th></tr>{cal_rows or '<tr><td colspan="4">No data</td></tr>'}</table></section>
<section><h2>Chase</h2><p>Assisted fills: <b>{ch.get('assisted_fills', 0)}</b> / {ch.get('total_fills', 0)} · Saved: {ch.get('saved_fills', 0)} · TTL expired: {ch.get('ttl_expired', 0)}</p></section>
<section><h2>Scenario C — Leakage &amp; Exits</h2>
<p>Peak ${leak.get('peak_profit_usd', leak.get('peak_usd', 'n/a'))} → Booked ${leak.get('booked_profit_usd', leak.get('booked_usd', 'n/a'))} · Left ${payload.get('scenario_c', {}).get('leakage_left_usd', 'n/a')}</p>
<p>Fast-cut trades: {fc_sum.get('fast_cut_trades', 0)} · Missed ladder profit: ${float(fc_sum.get('missed_ladder_profit_usd') or 0):.2f}</p></section>
<section><h2>Horizon Profitability (losers)</h2>
<p class="note">Would losing trades have been green N minutes after exit? (tick replay)</p>
<table><tr><th>Horizon</th><th>Profitable</th><th>Still loss</th><th>Unknown</th><th>% profitable</th></tr>{hz_rows or '<tr><td colspan="5">No losing trades</td></tr>'}</table></section>
<section><h2>Key Findings</h2><ol>{findings_html or '<li>No findings</li>'}</ol></section>
<section><h2>Report Layers</h2><ul>
<li><a href="executive_summary.txt">executive_summary.txt</a> — 60-second overview</li>
<li><a href="research_highlights.txt">research_highlights.txt</a> — detailed tables</li>
<li><a href="research_findings.txt">research_findings.txt</a> — conclusions</li>
<li><a href="research_coverage.txt">research_coverage.txt</a> — sample health</li>
<li><a href="research_deep_dive_index.txt">research_deep_dive_index.txt</a> — all reports</li>
<li><a href="analyzer_run.log">analyzer_run.log</a> — full verbose</li>
</ul></section>
<section><h2>Deep Dive Reports (sample)</h2><ul>{report_links or '<li>Run analyzer to populate reports/</li>'}</ul>
<p class="note">Full index: research_deep_dive_index.txt · {payload.get('json_reports_written', 0)} JSON files mirrored to reports/</p></section>
</body></html>"""
    try:
        with open(ANALYSIS_DASHBOARD_HTML, "w", encoding="utf-8") as f:
            f.write(html)
        return True
    except Exception:
        return False


def generate_all_data_companion_reports(dataset_counts=None, session_trade_count=0):
    """Always write reports/all_data/ from full CSV — dashboard uses this for paused/retired lanes."""
    dataset_counts = dataset_counts or {}
    csv_n = int(dataset_counts.get("csv_trades") or 0)
    if csv_n <= 0:
        return
    sub = ALL_DATA_REPORTS_SUBDIR
    os.makedirs(sub, exist_ok=True)
    print(
        f"\n=== ALL-DATA COMPANION — full CSV ({csv_n} rows; session fills={session_trade_count}) "
        f"→ {sub}/ {PIPELINE_ENFORCEMENT_TAG} ==="
    )
    _set_analyzer_report_subdir(sub)
    try:
        trades, blocked, decisions, ai_log, setups, candles, signal_persist, near_edge, pipeline_events, ai_errors = load_data()
        no_filter_session = {}
        shadow_report = shadow_fill_outcome_matrix(trades, session=no_filter_session, blocked=blocked)
        benchmark_report = benchmark_vs_lanes_report(
            trades, session=no_filter_session, blocked=blocked, shadow_report=shadow_report, all_trades=trades,
        )
        chase_payload = chase_attribution_report(trades=trades, session=no_filter_session)
        chase_effectiveness_report(trades=trades, session=no_filter_session, chase_payload=chase_payload)
        chase_threshold_report(trades=trades, session=no_filter_session)
        top_combinations_report(trades=trades, session=no_filter_session)
        exit_combinations_report(trades=trades, session=no_filter_session)
        lane_definition_report(trades=trades, session=no_filter_session, benchmark_report=benchmark_report)
        lane_retirement_report(trades=trades, session=no_filter_session, benchmark_report=benchmark_report)
        print(f"  ✅ ALL-DATA companion reports → {sub}/ {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as exc:
        print(f"  ⚠️ ALL-DATA companion failed: {exc} {PIPELINE_ENFORCEMENT_TAG}")
    finally:
        _set_analyzer_report_subdir(None)


def finalize_analyzer_outputs(
    session=None,
    trades=None,
    analysis_df=None,
    decisions=None,
    blocked=None,
    dataset_counts=None,
    data_scope="all",
):
    """Write hierarchical text reports + dashboard; print all layers to console."""
    payload = build_executive_summary_payload(
        session,
        trades,
        analysis_df,
        decisions,
        blocked=blocked,
        dataset_counts=dataset_counts,
        data_scope=data_scope,
    )
    _mirror_reports_to_dir()
    files = {
        EXECUTIVE_SUMMARY_FILE: format_executive_summary_short(payload),
        RESEARCH_HIGHLIGHTS_FILE: format_research_highlights_text(payload),
        RESEARCH_COVERAGE_FILE: format_research_coverage_text(payload),
        RESEARCH_FINDINGS_FILE: format_research_findings_text(payload),
        DEEP_DIVE_INDEX_FILE: format_deep_dive_index_text(payload),
    }
    for path, content in files.items():
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as exc:
            print(f"⚠️ Could not write {path}: {exc}")
    write_analysis_dashboard_html(payload)
    try:
        with open(RESEARCH_COMPACT_SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
    write_report_manifest(payload)
    try:
        from research_trade_accumulator import sync_accumulator_from_analyzer_run

        acc = sync_accumulator_from_analyzer_run(session=session, trades=trades)
        print(
            f"  ✅ Trade accumulator: +{acc.get('new', 0)} new → {acc.get('total', 0)} total "
            f"(epoch {str(acc.get('epoch', ''))[:19]}) {PIPELINE_ENFORCEMENT_TAG}"
        )
    except Exception as exc:
        print(f"  ⚠️ Trade accumulator sync failed: {exc} {PIPELINE_ENFORCEMENT_TAG}")
    if str(data_scope).lower() == "session":
        stc = 0
        if trades is not None and not trades.empty:
            if "trade_id" in trades.columns:
                stc = len(trades.drop_duplicates(subset=["trade_id"]))
            else:
                stc = len(trades)
        generate_all_data_companion_reports(dataset_counts, session_trade_count=stc)
    snap = _save_rolling_snapshot()
    archive_research_session(payload)
    console_text = format_terminal_status(payload)
    print("\n" + console_text)
    art = payload.get("artifacts") or {}
    print(f"\n  ✅ Research UI:    {os.getenv('RESEARCH_DASHBOARD_PUBLIC_URL', 'http://10.0.0.102:9001/')}")
    print(f"  ✅ Dashboard:      embedded with analyzer (no separate script)")
    print(f"  ✅ Download ZIP:   http://10.0.0.102:9001/download/reports")
    if snap:
        print(f"  ✅ Snapshot:       {os.path.abspath(snap)}")
    print(f"  ✅ Manifest:       {os.path.abspath(REPORT_MANIFEST_FILE)}")
    print(f"  ✅ Reports dir:    {art.get('reports_dir', REPORTS_DIR)} ({payload.get('json_reports_written', 0)} JSON) {PIPELINE_ENFORCEMENT_TAG}\n")
    return payload


if __name__ == "__main__":
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    # Home-stack runs from agent/research/; pathway_lab_validation lives in agent root.
    if os.path.isfile(os.path.join(_script_dir, "pathway_lab_validation.py")):
        if _script_dir not in sys.path:
            sys.path.insert(0, _script_dir)
    else:
        _agent_root = os.path.dirname(_script_dir)
        if _agent_root and _agent_root not in sys.path:
            sys.path.insert(0, _agent_root)
    if os.path.abspath(os.getcwd()) != _script_dir:
        print(f"  ℹ️ Switching cwd → {_script_dir}")
        os.chdir(_script_dir)

    interval_min = ANALYZER_LOOP_INTERVAL_MINUTES
    session_only, scope_reason = resolve_analyzer_session_scope()
    dashboard_url = os.getenv("RESEARCH_DASHBOARD_PUBLIC_URL", "http://10.0.0.102:9001/")

    _once_mode = len(sys.argv) > 1 and str(sys.argv[1]).startswith("--once")

    ANALYZER_CONSOLE_VERBOSE = True
    _tee, _log_handle = _setup_analyzer_output(verbose_console=True, enable_log=True)

    if _once_mode:
        print(
            f"\n=== ANALYZER {ANALYZER_VERSION} — single run (no dashboard) ==="
            f" {PIPELINE_ENFORCEMENT_TAG}"
        )
        print(f"  Data scope: {scope_reason}")
        try:
            run(interval_min=interval_min, session_only=session_only, max_iterations=1)
        finally:
            _restore_analyzer_output(_tee, _log_handle)
        sys.exit(0)

    print(
        f"\n=== ANALYZER {ANALYZER_VERSION} — continuous {interval_min} min + embedded dashboard ==="
        f" {PIPELINE_ENFORCEMENT_TAG}"
    )
    print(f"  Data scope: {scope_reason}")
    print(f"  Full log:   {os.path.abspath(ANALYZER_RUN_LOG_FILE)}")
    print(f"  Dashboard:  {dashboard_url}")
    start_research_dashboard_server()
    try:
        run(interval_min=interval_min, session_only=session_only)
    finally:
        _restore_analyzer_output(_tee, _log_handle)