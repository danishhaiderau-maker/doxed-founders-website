"""Frozen Live Indicator Edge specification (pure; shared by Fly engine and laptop scorer).

One source of truth for the 52-indicator grid of
``diagnostics/INDICATOR-EDGE-SPEC-20261004.md`` Appendix A: every feature id,
its settings, direction-score rule, inputs, role and family; the forward scoring
rules (windows, latencies, costs, labels, FDR, cluster bootstrap); the regime
labels; and the week-2 combination grid shape.

``feature_set_sha()`` hashes the canonical JSON of :func:`spec_document`. The
engine stamps it on every ``indicator_bars_v1`` row and the scorer only scores
rows whose sha matches a pre-registration frozen in the forward tracker before
the row's bar closed. Any change to a setting, rule or the scoring rules
changes the sha, i.e. it is a new frozen candidate with a fresh clock.

Observation only: nothing here (or in the engine/scorer) can place, change or
cancel an order, read toggles, touch the relay or create a tile.
"""
from __future__ import annotations

import functools
import hashlib
import json
from typing import Any

FEATURE_SET_VERSION = "indicator_edge_fs_v1_20261004"
BAR_SCHEMA = "indicator_bars_v1"
BAR_FILE = "indicator_bars_v1.jsonl"
LIVE_FILE = "indicator_engine_live.json"
LIVE_SCHEMA = "indicator_engine_live_v1"
HEALTH_SCHEMA = "indicator_engine_health_v1"
BAR_SEC = 180
PCT_LOOKBACK_BARS = 480          # 24 h of 3-minute bars
MIN_PCT_HISTORY_BARS = 160       # percentile-based rules WARMING_UP below 8 h of history
HISTORY_BARS = 1200              # 60 h computation window (EMA 288 residual seed weight < 0.1%)

# Feature row layout: ``f[feature_id] = [raw, score, pct, status]``.
STATUS_AVAILABLE = "A"
STATUS_WARMING_UP = "W"
STATUS_UNAVAILABLE = "U"
STATUS_NAMES = {STATUS_AVAILABLE: "AVAILABLE", STATUS_WARMING_UP: "WARMING_UP", STATUS_UNAVAILABLE: "UNAVAILABLE"}
FEATURE_ROW_LAYOUT = ("raw", "score", "pct", "status")

# Families (Appendix A sections) and roles used by the combination grid.
FAMILY_TREND = "A_TREND"
FAMILY_MOMENTUM = "B_MOMENTUM"
FAMILY_FLOW = "C_FLOW"
FAMILY_VOLATILITY = "D_VOLATILITY"
FAMILY_STRUCTURE = "E_STRUCTURE"
FAMILY_DERIVATIVES = "F_DERIVATIVES"
ROLE_FILTER = "FILTER"
ROLE_TRIGGER = "TRIGGER"
ROLE_CONFIRMATION = "CONFIRMATION"
ROLE_REGIME = "REGIME"
ROLE_LEVELS = "LEVELS"

INPUT_TAPE = "T"
INPUT_XVENUE = "X"
INPUT_DEPTH = "D"
INPUT_FUNDING = "F"
INPUT_OI = "OI"
INPUT_LIQ = "L"

TREND = "TREND"
REVERSION = "REVERSION"
DIVERGENCE = "DIVERGENCE"
BREAKOUT = "BREAKOUT"
STATE = "STATE"


def _ind(num: int, ind_id: str, family: str, role: str, inputs: str, settings: dict, rule: str,
         variants: tuple, scored: bool = True, note: str = "") -> dict:
    return {"num": num, "id": ind_id, "family": family, "role": role, "inputs": inputs.split(","),
            "settings": settings, "rule": rule, "variants": list(variants), "scored": scored, "note": note}


# Horizons are explicit per indicator: "F" = Appendix A setting (~9 bars) and
# "S" = slow (~50 bars) where the indicator has a length parameter.
INDICATORS: tuple = (
    # ---------------- A. Trend (filters)
    _ind(1, "EMA_200", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"n": 200, "slope_bars": 10}},
         "+1 close>EMA and EMA rising over 10 bars; -1 mirror; else 0", (STATE,)),
    _ind(2, "EMA_50", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"n": 50, "slope_bars": 10}},
         "same as EMA_200", (STATE,)),
    _ind(3, "EMA_CROSS_9_21", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"fast": 9, "slow": 21}},
         "+1 EMA9>EMA21, -1 below", (STATE,)),
    _ind(4, "EMA_288", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"n": 288, "slope_bars": 10}},
         "same as EMA_200; WARMING_UP until 288 bars", (STATE,)),
    _ind(5, "ICHIMOKU_FAST", FAMILY_TREND, ROLE_FILTER, "T",
         {"F": {"tenkan": 7, "kijun": 22, "senkou_b": 44, "displacement": 22}},
         "+1 close above the already-printed cloud and Tenkan>Kijun; -1 mirror; else 0", (STATE,)),
    _ind(6, "SUPERTREND", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"atr": 10, "mult": 2.0}},
         "+1 uptrend state, -1 downtrend", (STATE,)),
    _ind(7, "PSAR", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"step": 0.02, "max": 0.2}},
         "+1 SAR below price, -1 above", (STATE,)),
    _ind(8, "HMA_SLOPE", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"n": 14, "slope_bars": 3}, "S": {"n": 50, "slope_bars": 3}},
         "sign of the 3-bar HMA slope", (STATE,)),
    _ind(9, "LINREG_SLOPE", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"n": 14, "t_min": 2.0}, "S": {"n": 50, "t_min": 2.0}},
         "sign of the OLS slope if |t|>2, else 0", (STATE,)),
    _ind(10, "TEMA", FAMILY_TREND, ROLE_FILTER, "T", {"F": {"fast": 9, "slow": 21}},
         "+1 TEMA9>TEMA21, -1 below", (STATE,)),
    # ---------------- B. Momentum (triggers): TREND and REVERSION scored separately
    _ind(11, "RSI", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"n": 9}, "S": {"n": 21}},
         "TREND +1>55 -1<45; REVERSION +1<25 -1>75", (TREND, REVERSION)),
    _ind(12, "STOCH", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"k": 5, "smooth": 3, "d": 3}, "S": {"k": 21, "smooth": 3, "d": 3}},
         "TREND +1 %K crosses up %D above 50 (-1 crosses down below 50); REVERSION +1 cross up below 20, -1 cross down above 80",
         (TREND, REVERSION)),
    _ind(13, "MACD_SCALP", FAMILY_MOMENTUM, ROLE_TRIGGER, "T",
         {"F": {"fast": 6, "slow": 13, "signal": 4}, "S": {"fast": 12, "slow": 26, "signal": 9}},
         "TREND sign of histogram when rising in that direction; REVERSION histogram turning back from its top/bottom 10% pct",
         (TREND, REVERSION)),
    _ind(14, "CCI", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"n": 14}, "S": {"n": 50}},
         "TREND +1>+100 -1<-100; REVERSION +1 back above -100, -1 back below +100", (TREND, REVERSION)),
    _ind(15, "WILLR", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"n": 10}, "S": {"n": 50}},
         "TREND +1>-20 -1<-80; REVERSION +1 up through -80, -1 down through -20", (TREND, REVERSION)),
    _ind(16, "AO", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"fast": 3, "slow": 15, "twin_peak_lookback": 30}},
         "TREND sign and rising; REVERSION zero-line twin-peak reversal", (TREND, REVERSION)),
    _ind(17, "KST", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"roc": [3, 6, 9, 12], "sma": [3, 3, 3, 3], "signal": 3}},
         "TREND +1 KST>signal, -1 below", (TREND,)),
    _ind(18, "CMO", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"n": 9}, "S": {"n": 50}},
         "TREND +1>+30 -1<-30; REVERSION +1<-50 -1>+50", (TREND, REVERSION)),
    _ind(19, "ROC", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"n": 6}, "S": {"n": 20}},
         "TREND sign if |ROC| > rolling 60th pct of |ROC|; REVERSION reversed if |ROC| > 95th pct", (TREND, REVERSION)),
    _ind(20, "DPO", FAMILY_MOMENTUM, ROLE_TRIGGER, "T", {"F": {"n": 12}, "S": {"n": 50}},
         "causal DPO = close - SMA(n) shifted n/2+1; TREND +1 crosses above 0 (-1 below); REVERSION +1 bottom 10% pct, -1 top 10%",
         (TREND, REVERSION)),
    # ---------------- C. Volume and order flow (confirmation)
    _ind(21, "VP_SESSION", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"anchor": "00:00 UTC", "value_area": 0.70, "bin_bp": 2.0, "min_bars": 10}},
         "TREND +1 two closes above VAH, -1 two below VAL; REVERSION +1 VAL rejection, -1 VAH rejection", (TREND, REVERSION)),
    _ind(22, "AVWAP_SESSION", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"anchor": "00:00 UTC", "band_std": 2.0, "min_bars": 10}},
         "TREND +1 close>VWAP -1 below; REVERSION fades the 2-std bands", (TREND, REVERSION)),
    _ind(23, "OBV_EMA", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"n": 13}, "S": {"n": 50}},
         "+1 OBV above its EMA, -1 below", (TREND,)),
    _ind(24, "AD_SLOPE", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"n": 14}, "S": {"n": 50}},
         "TREND sign of A/D slope; DIVERGENCE +1 price slope down while A/D up, -1 mirror", (TREND, DIVERGENCE)),
    _ind(25, "VWMA_20", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"n": 20}, "S": {"n": 50}},
         "+1 close>VWMA and VWMA>SMA; -1 mirror; else 0", (TREND,)),
    _ind(26, "MFI", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"n": 9}, "S": {"n": 50}},
         "REVERSION +1<20 -1>80; TREND +1>60 -1<40", (TREND, REVERSION)),
    _ind(27, "CMF", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"n": 12}, "S": {"n": 50}},
         "+1 > +0.05, -1 < -0.05", (TREND,)),
    _ind(28, "KLINGER", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"fast": 17, "slow": 34, "signal": 9}},
         "+1 KVO>signal, -1 below", (TREND,)),
    _ind(29, "EOM", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"n": 9}, "S": {"n": 50}},
         "sign of SMA(EMV)", (TREND,)),
    _ind(30, "NET_TAKER_VOL", FAMILY_FLOW, ROLE_CONFIRMATION, "T", {"F": {"bars": 1, "pct": 70}, "S": {"bars": 5, "pct": 70}},
         "sign of taker buy-sell (per bar / 5-bar sum) if |x| > rolling 70th pct", (TREND,)),
    _ind(44, "BOOK_IMBALANCE", FAMILY_FLOW, ROLE_CONFIRMATION, "T,D",
         {"BFX": {"source": "bitfinex_tob_bar_mean", "threshold": 0.3},
          "BN5": {"source": "binance_top5_bar_mean", "threshold": 0.3},
          "BN20": {"source": "binance_top20_bar_mean", "threshold": 0.3}},
         "+1 > +0.3, -1 < -0.3 (bar mean of (bid-ask)/(bid+ask))", (TREND,)),
    _ind(45, "CVD", FAMILY_FLOW, ROLE_CONFIRMATION, "T,X",
         {"BFX": {"source": "bitfinex", "slope_bars": 20}, "BN": {"source": "binance", "slope_bars": 20}},
         "TREND sign of the 20-bar CVD slope; DIVERGENCE fades price when price slope and CVD slope disagree", (TREND, DIVERGENCE)),
    _ind(50, "XVENUE_NET_FLOW", FAMILY_FLOW, ROLE_CONFIRMATION, "X", {"F": {"venues": ["binance", "bybit"], "pct": 70}},
         "sign of (Binance+Bybit taker net) - Bitfinex taker net if |x| > rolling 70th pct", (TREND,)),
    # ---------------- D. Volatility (regime labels; breakout direction where noted)
    _ind(31, "BB", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"n": 20, "k": 2.0, "squeeze_pct": 20, "squeeze_memory": 5},
                                                        "S": {"n": 50, "k": 2.0, "squeeze_pct": 20, "squeeze_memory": 5}},
         "squeeze = bandwidth < 20th pct; BREAKOUT +1/-1 close outside the band within 5 bars after a squeeze", (BREAKOUT,)),
    _ind(32, "ATR", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"n": 14}},
         "regime tercile of ATR% (label only)", (STATE,), scored=False),
    _ind(33, "KELTNER", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"ema": 20, "atr": 20, "atr_mult": 1.5}},
         "TREND +1 close above upper, -1 below lower; REVERSION +1 lower-band rejection, -1 upper-band rejection",
         (TREND, REVERSION)),
    _ind(34, "DONCHIAN", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"n": 20}, "S": {"n": 50}},
         "BREAKOUT +1 close above prior 20-bar high, -1 below prior low", (BREAKOUT,)),
    _ind(35, "CHAIKIN_VOL", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"ema": 10, "roc": 10, "expansion_pct": 80}},
         "expansion flag (> 80th pct, label only)", (STATE,), scored=False),
    _ind(36, "HV", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"n": 10, "trigger_pct": 95, "fade_bars": 3}},
         "REVERSION: when 10-bar realised vol > 95th pct, fade the last 3-bar move", (REVERSION,)),
    _ind(37, "STDDEV_Z", FAMILY_VOLATILITY, ROLE_REGIME, "T", {"F": {"n": 20, "z": 2.0}, "S": {"n": 50, "z": 2.0}},
         "REVERSION +1 z<-2, -1 z>+2", (REVERSION,)),
    # ---------------- E. Structure (previous-day / confirmed-swing values only)
    _ind(38, "FIB_4H", FAMILY_STRUCTURE, ROLE_TRIGGER, "T", {"F": {"bars": 80, "level": 0.618, "zone_upper": 0.5}},
         "+1 bounce at 0.618 in a 4h up-leg (low touches, close back above), -1 mirror; window excludes the current bar",
         (REVERSION,)),
    _ind(39, "FIB_EXT_4H", FAMILY_STRUCTURE, ROLE_LEVELS, "T", {"F": {"bars": 80, "levels": [1.272, 1.618]}},
         "take-profit levels only (not scored)", (STATE,), scored=False),
    _ind(40, "CPR_PIVOTS", FAMILY_STRUCTURE, ROLE_TRIGGER, "T", {"F": {"anchor": "previous UTC day", "min_coverage": 0.9}},
         "TREND +1 close>TC, -1 close<BC; REVERSION fades S1/R1", (TREND, REVERSION)),
    _ind(41, "CAMARILLA", FAMILY_STRUCTURE, ROLE_TRIGGER, "T", {"F": {"anchor": "previous UTC day", "min_coverage": 0.9}},
         "REVERSION fades H3/L3; BREAKOUT follows beyond H4/L4", (REVERSION, BREAKOUT)),
    _ind(42, "ZIGZAG_MSB", FAMILY_STRUCTURE, ROLE_TRIGGER, "T", {"F": {"reversal_pct": 0.3}},
         "BREAKOUT +1 close breaks the last confirmed swing high, -1 swing low (event bar)", (BREAKOUT,)),
    _ind(43, "PITCHFORK_12H", FAMILY_STRUCTURE, ROLE_FILTER, "T", {"F": {"bars": 240, "reversal_pct": 0.8}},
         "+1 rising fork and close above its median line, -1 falling fork and below; mostly a context label", (STATE,)),
    # ---------------- F. Derivatives
    _ind(46, "LIQ_BURST", FAMILY_DERIVATIVES, ROLE_TRIGGER, "L", {"F": {"burst_pct": 95}},
         "REVERSION fade a burst > 95th pct (long liquidations -> +1); TREND follows", (REVERSION, TREND)),
    _ind(47, "VOL_EXPECTED", FAMILY_DERIVATIVES, ROLE_REGIME, "T", {"F": {"proxy": "20-bar realised vol pct", "dvol": "UNAVAILABLE"}},
         "regime label only (DVOL not collected; realised-vol proxy)", (STATE,), scored=False),
    _ind(48, "FUNDING", FAMILY_DERIVATIVES, ROLE_TRIGGER, "F", {"F": {"venues": ["binance", "bybit", "okx", "bitfinex"], "hi_pct": 90, "lo_pct": 10}},
         "REVERSION -1 if predicted funding > 90th pct, +1 < 10th", (REVERSION,)),
    _ind(49, "OI_DELTA", FAMILY_DERIVATIVES, ROLE_TRIGGER, "OI", {"F": {"bars": 1}, "S": {"bars": 20}},
         "+1 OI up & price up, -1 OI up & price down, OI down = 0", (TREND,)),
    _ind(51, "BASIS_PREMIUM", FAMILY_DERIVATIVES, ROLE_TRIGGER, "X",
         {"F": {"evaluator": "cross_venue_premium.PremiumRule (generic)", "mean_window_sec": 3600}},
         "follow the sign of the generic premium trigger (deviation from 60m mean beyond its frozen thresholds)", (TREND,)),
    _ind(52, "XVENUE_LEAD", FAMILY_DERIVATIVES, ROLE_TRIGGER, "X",
         {"F": {"evaluator": "cross_venue_lead.LeadRule (generic)", "lookback_sec": 10, "threshold_bp": 8.0}},
         "follow the net sign of generic lead-trigger seconds inside the bar", (TREND,)),
)

UNAVAILABLE_INPUTS = {
    "exchange_inflow_outflow": "not collected (no on-chain/exchange flow feed)",
    "dvol": "not collected (Deribit DVOL); VOL_EXPECTED uses a realised-vol proxy",
}

REGIME_LABELS = {
    "vol_tercile": "ATR14% percentile over 480 bars: <33.3 LOW, <66.7 MID, else HIGH",
    "trend_state": "ADX14: <20 RANGE, 20-30 WEAK, >=30 TREND",
    "session": "UTC hour 0-8 ASIA, 8-16 EU, 16-24 US",
    "spread_bucket": "bar mean Bitfinex spread bp: <1, 1-2, 2-4, >=4",
    "ttf_bucket": "minutes to next funding: <30, 30-120, 120-240, >=240",
    "ai_class": "latest AI call before bar close: COMMITTED / COMMITTED_SCORE_CONFLICT / NO_TRADE_SCORE_LED / NO_SIDE",
    "ai_side": "LONG / SHORT / NONE of that call",
}
REGIME_SPLITS = ("vol_tercile", "trend_state", "session")

SCORING_RULES = {
    "id": "INDICATOR_EDGE_SCORING_V1",
    "decision_lag_sec": 15,
    "decision_rule": "decision_ts = bar_close_ts + 15 s (cross-venue/market-context minute rows close by +3..+12 s)",
    "latencies_sec": [2, 9],
    "primary_latency_sec": 2,
    "windows_min": [3, 15, 60, 120],
    "round_trip_cost_bp": 2.0,
    "price": "Bitfinex 1 s tape mid (strategy_lab dense tape); entry at decision+latency, exit window later",
    "executable_secondary": "ask/bid crossing reported as exec_bp alongside the mid-minus-2bp headline",
    "hole_censor_sec": 60,
    "eligible_rows": "health.ok rows whose feature_set_sha matches the frozen pre-registration and bar_close_ts > frozen_at",
    "signal": "direction score != 0 (score>0 long, <0 short)",
    "hit": "side * mid move > 0",
    "rank_ic": "Spearman of oriented pct (raw when no pct) vs forward mid return; REVERSION/DIVERGENCE orientation -1",
    "top_bottom": "oriented mean forward return when pct >= 80 minus when pct <= 20",
    "cluster_sec": 3600,
    "bootstrap_resamples": 2000,
    "bootstrap_seed": 20261004,
    "p_value": "one-sided 1h-cluster bootstrap P(mean net bp <= 0); p = 1 below min_bootstrap_clusters",
    "min_bootstrap_clusters": 10,
    "fdr": "Benjamini-Hochberg across every scored feature x window trial, q = 0.10",
    "fdr_q": 0.10,
    "stability": "per UTC day mean gross side*move sign",
    "correlation_cluster_abs_rho": 0.7,
    "labels": {
        "HINT": ">= 3 scored UTC days, sign held every day and mean net bp (2 s) > 0",
        "PROMISING": ">= 7 scored UTC days, BH-significant (bootstrap CI clears zero after correction), sign held on >= 5 of "
                     "the last 7 days, mean net bp > 0 in >= 2 sessions and at 9 s latency",
        "NOISE": "everything else (including too few days)",
    },
    "min_signals_per_day": 1,
    "min_days_hint": 3,
    "min_days_promising": 7,
    "promising_min_sign_days_of_7": 5,
    "promising_min_sessions": 2,
}

COMBINATION_GRID = {
    "id": "INDICATOR_EDGE_COMBOS_V1",
    "activation": "week 2 onward, only PROMISING features; otherwise GATED",
    "shape": "[1 filter from A, D or G] + [1 trigger from B, C-momentum, E or F] + [optional 1 confirmation from C]",
    "max_features": 3,
    "max_settings_per_feature": 2,
    "max_variants_per_combination": 8,
    "max_variants_per_family": 64,
    "forward_days": 7,
    "filter_families": [FAMILY_TREND, FAMILY_VOLATILITY, "G_REGIME"],
    "trigger_families": [FAMILY_MOMENTUM, FAMILY_FLOW, FAMILY_STRUCTURE, FAMILY_DERIVATIVES],
    "confirmation_families": [FAMILY_FLOW],
    "tile_filters": ["AI committed fade only when the best trend feature agrees",
                     "AI NO_TRADE score-led follow only when the best trend feature agrees"],
}

TILE_PACKAGE_RULES = {
    "id": "INDICATOR_EDGE_TILE_PACKAGE_V1",
    "eligibility": "combination still PROMISING after its own 7-day forward window",
    "entry": "volatility-aware offset or confirm-then-chase",
    "exit": "composite: late break-even, ATR/chandelier trail armed after +1.5 ATR, time backstop; first trigger wins",
    "risk": "conditional early cut -10/-12 bp in the first 3-5 min only if MFE never > +2 bp; 40 bp hard stop",
    "sizing": "$0.25 margin at 100x, REALISTIC_V1, zero Bitfinex fees",
    "registration": "paper-only, relay-ineligible, default OFF, pre-registered promote/kill rules; Danish turns it on",
    "output": "draft proposal JSON only; never edits combo_pathway_config.py",
}


def feature_ids() -> list:
    """Every stored feature id, in registry order: ``<ID>@<horizon>:<variant>``."""
    out = []
    for ind in INDICATORS:
        for horizon in ind["settings"]:
            for variant in ind["variants"]:
                out.append(f"{ind['id']}@{horizon}:{variant}")
    return out


def indicator_of(feature_id: str) -> dict:
    ind_id = str(feature_id).split("@", 1)[0]
    for ind in INDICATORS:
        if ind["id"] == ind_id:
            return ind
    raise KeyError(feature_id)


def scored_feature_ids() -> list:
    return [fid for fid in feature_ids() if indicator_of(fid)["scored"]]


def orientation(feature_id: str) -> int:
    variant = str(feature_id).rsplit(":", 1)[-1]
    return -1 if variant in (REVERSION, DIVERGENCE) else 1


def trial_count() -> int:
    return len(scored_feature_ids()) * len(SCORING_RULES["windows_min"])


def spec_document() -> dict:
    return {
        "feature_set_version": FEATURE_SET_VERSION,
        "bar_schema": BAR_SCHEMA,
        "bar_sec": BAR_SEC,
        "pct_lookback_bars": PCT_LOOKBACK_BARS,
        "min_pct_history_bars": MIN_PCT_HISTORY_BARS,
        "history_bars": HISTORY_BARS,
        "row_layout": list(FEATURE_ROW_LAYOUT),
        "indicators": [dict(i) for i in INDICATORS],
        "feature_ids": feature_ids(),
        "scored_feature_ids": scored_feature_ids(),
        "unavailable_inputs": UNAVAILABLE_INPUTS,
        "regime_labels": REGIME_LABELS,
        "scoring_rules": SCORING_RULES,
        "trial_count": trial_count(),
        "combination_grid": COMBINATION_GRID,
        "tile_package_rules": TILE_PACKAGE_RULES,
    }


def canonical_json(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)


@functools.lru_cache(maxsize=1)
def feature_set_sha() -> str:
    return hashlib.sha256(canonical_json(spec_document()).encode("utf-8")).hexdigest()


ENGINE_DOWN_SEC = 60.0
BAR_STALL_SEC = 2 * BAR_SEC + 60


def health_from_live(live: Any, now: float, *, enabled: bool = True) -> dict:
    """Engine health from ``indicator_engine_live.json`` (status only; never an order input).

    ``STALLED`` means the process is alive but no bar closed for ``BAR_STALL_SEC``
    (bars must advance every 3 minutes); ``DEGRADED`` means the last row's
    feature-health flag is false (tape hole or missing input).
    """
    out = {"schema": HEALTH_SCHEMA, "enabled": bool(enabled), "status": "DISABLED", "age_sec": None,
           "last_bar_ts": None, "last_bar_close_age_sec": None, "affects_orders": False}
    if not enabled:
        return out
    if not isinstance(live, dict) or live.get("schema") != LIVE_SCHEMA:
        out["status"] = "ENGINE_DOWN"
        return out
    age = now - float(live.get("written_ts") or 0.0)
    stats = live.get("stats") or {}
    health = live.get("last_health") or {}
    out.update({
        "age_sec": round(age, 1),
        "boot_id": live.get("boot_id"),
        "feature_set_version": live.get("feature_set_version"),
        "feature_set_sha": live.get("feature_set_sha"),
        "feature_set_matches": live.get("feature_set_sha") == feature_set_sha(),
        "last_bar_ts": live.get("last_bar_ts"),
        "rows_written": stats.get("rows_written"),
        "late_rows_written": stats.get("late_rows_written"),
        "write_failures": stats.get("write_failures"),
        "compute_failures": stats.get("compute_failures"),
        "compute_ms_last": stats.get("compute_ms_last"),
        "cpu_pct_1m": stats.get("cpu_pct_1m"),
        "rss_mb": stats.get("rss_mb"),
        "bytes_today": stats.get("bytes_today"),
        "history_bars": live.get("history_bars"),
        "status_counts": live.get("last_status_counts"),
        "row_health_ok": health.get("ok"),
        "row_health_reasons": health.get("reasons"),
    })
    if age > ENGINE_DOWN_SEC:
        out["status"] = "ENGINE_DOWN"
        return out
    last_bar = live.get("last_bar_ts")
    close_age = None if last_bar is None else now - (float(last_bar) + BAR_SEC)
    out["last_bar_close_age_sec"] = None if close_age is None else round(close_age, 1)
    if close_age is None or close_age > BAR_STALL_SEC:
        out["status"] = "STALLED"
    elif not out["feature_set_matches"] or stats.get("write_failures") or stats.get("compute_failures"):
        out["status"] = "DEGRADED"
    elif health.get("ok") is False:
        out["status"] = "DEGRADED"
    else:
        out["status"] = "OK"
    return out


def validate_spec() -> list:
    """Problems with the registry itself (empty list = valid)."""
    problems = []
    nums = [i["num"] for i in INDICATORS]
    if sorted(nums) != list(range(1, 53)):
        problems.append(f"indicator numbers must be exactly 1..52, got {sorted(nums)}")
    ids = [i["id"] for i in INDICATORS]
    if len(set(ids)) != len(ids):
        problems.append("duplicate indicator ids")
    fids = feature_ids()
    if len(set(fids)) != len(fids):
        problems.append("duplicate feature ids")
    for ind in INDICATORS:
        if not ind["settings"] or not ind["variants"]:
            problems.append(f"{ind['id']}: settings and variants are required")
        if ind["role"] not in (ROLE_FILTER, ROLE_TRIGGER, ROLE_CONFIRMATION, ROLE_REGIME, ROLE_LEVELS):
            problems.append(f"{ind['id']}: unknown role {ind['role']}")
    return problems
