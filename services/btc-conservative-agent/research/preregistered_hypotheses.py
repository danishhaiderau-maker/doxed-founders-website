"""Frozen, pre-registered mechanism-first hypotheses for the event-study harness.

Human-readable registration: ``diagnostics/PREREGISTERED-HYPOTHESES-20261002.md``.
Each spec fixes, before any lockbox data exists, the event rule, the sign
rule, the metric, the primary horizon, the minimum lockbox sample, the kill
rule and the lockbox length. Editing a registered spec is not allowed: add a
new id (``..._v2``) with a new registration time instead, so the lockbox of
the original stays clean. ``test_event_study`` pins the spec hashes.

Lockbox protocol: events before ``registered_utc`` are *discovery*
(exploratory, reported, never confirm anything). Events in
[``registered_utc``, ``registered_utc + lockbox_days``) are *lockbox*: counted
while it is open, scored exactly once after it closes. All thresholds are
fixed numbers or causal trailing statistics (``trailing_regime``); no
full-sample quantile is used anywhere.
"""
from __future__ import annotations

import hashlib
import json

REGISTRY_SCHEMA = "preregistered_hypotheses_v1"
REGISTERED_UTC = "2026-10-02T00:00:00Z"
CONTROLS_PER_EVENT = 5
CONTROL_MATCH = ("hour_of_day_utc", "trailing_rv15_tercile")

HYPOTHESES = (
    {
        "id": "H1_XVL_LEAD_10S_8BP_60S",
        "aligned_with": "H5_XVENUE_LEAD_60S_20261002 (cross_venue_lead.LeadRule defaults, worker 4a9ff2bd)",
        "mechanism": "Bitfinex tBTCF0 is a satellite venue; price discovery happens on Binance/Bybit perps "
                     "and Bitfinex quotes catch up with a lag of seconds, so a large cross-venue gap closes "
                     "toward the leaders.",
        "event_rule": {"kind": "xvl_lead", "lookback_sec": 10, "lead_threshold_bps": 8.0,
                       "venues": ["binance", "bybit"], "max_spread_bps": 3.0, "debounce_sec": 60},
        "sign_rule": "sign(lead)",
        "metric": "taker_after_spread_bp",
        "entry_delay_sec": 1,
        "horizons_sec": [5, 10, 30, 60, 120, 300],
        "primary_horizon_sec": 60,
        "min_lockbox_events": 200,
        "kill_rule": {"kill_if_mean_le_bp": 0.0, "kill_if_t_lt": 2.0},
        "lockbox_days": 14,
        "streams": ["cross_venue_tape_1m.jsonl", "market_microstructure_1s.jsonl"],
    },
    {
        "id": "H2_LIQ_BURST_60S_1M_CONTINUATION",
        "mechanism": "A burst of forced liquidations is price-insensitive flow; it either exhausts liquidity "
                     "and continues (cascade) or overshoots and reverts. Two-sided by construction.",
        "event_rule": {"kind": "liquidation_burst", "window_sec": 60, "min_notional_usd": 1_000_000,
                       "venues": ["binance", "bybit", "okx"], "debounce_sec": 900},
        "sign_rule": "continuation: SHORT after LONG_LIQUIDATED burst, LONG after SHORT_LIQUIDATED burst",
        "metric": "car_mid_bp",
        "entry_delay_sec": 1,
        "horizons_sec": [10, 30, 60, 120, 300, 900, 1800],
        "primary_horizon_sec": 300,
        "two_sided": True,
        "min_lockbox_events": 40,
        "kill_rule": {"kill_if_abs_t_lt": 1.5},
        "lockbox_days": 30,
        "streams": ["liquidations.jsonl", "market_microstructure_1s.jsonl"],
    },
    {
        "id": "H3_FUNDING_WINDOW_DRIFT_30M",
        "mechanism": "The side paying funding reduces exposure before the 00/08/16 UTC settlement and "
                     "re-enters after it, so price drifts against the paying side into settlement.",
        "event_rule": {"kind": "funding_window", "minutes_before_settlement": 30,
                       "funding_venues": ["binance", "bybit"], "min_abs_predicted_funding": 0.00005},
        "sign_rule": "-sign(mean predicted funding of Binance and Bybit at the event minute)",
        "metric": "car_mid_bp",
        "entry_delay_sec": 1,
        "horizons_sec": [300, 900, 1800, 2700, 3600],
        "primary_horizon_sec": 1800,
        "min_lockbox_events": 60,
        "kill_rule": {"kill_if_t_lt": 1.0},
        "lockbox_days": 30,
        "streams": ["market_context_1m.jsonl", "market_microstructure_1s.jsonl"],
    },
    {
        "id": "H4_US_CASH_OPEN_VOL_EXPANSION",
        "mechanism": "The US equity cash open concentrates ETF creation/redemption hedging and macro "
                     "repricing, so absolute BTC moves in the first 30 minutes exceed same-hour, "
                     "same-volatility controls on other days.",
        "event_rule": {"kind": "us_cash_open", "weekdays_only": True,
                       "holiday_table": "none (US market holidays not excluded; noted as a known bias)"},
        "sign_rule": "unsigned (absolute move); directional secondary: -sign(Bitfinex return over the prior 6 h)",
        "metric": "abs_move_bp",
        "entry_delay_sec": 0,
        "horizons_sec": [300, 900, 1800, 3600],
        "primary_horizon_sec": 1800,
        "min_lockbox_events": 20,
        "kill_rule": {"kill_if_t_lt": 2.0},
        "lockbox_days": 30,
        "control_match_override": ("weekday_class", "trailing_rv15_tercile"),
        "streams": ["market_context_1m.jsonl", "market_microstructure_1s.jsonl"],
    },
    {
        "id": "H5_COINBASE_PREMIUM_LEAD_300S",
        "mechanism": "Coinbase BTC-USD reflects US spot demand; a sudden change in its premium over "
                     "Bitfinex is information Bitfinex has not priced yet.",
        "event_rule": {"kind": "coinbase_premium_jump", "avg_sec": 10, "change_lag_sec": 60,
                       "min_change_bp": 3.0, "min_valid_sec": 8, "debounce_sec": 300},
        "sign_rule": "sign(premium change)",
        "metric": "car_mid_bp",
        "entry_delay_sec": 1,
        "horizons_sec": [10, 30, 60, 120, 300, 900],
        "primary_horizon_sec": 300,
        "min_lockbox_events": 100,
        "kill_rule": {"kill_if_mean_le_bp": 0.0, "kill_if_t_lt": 2.0},
        "lockbox_days": 21,
        "streams": ["market_context_1m.jsonl", "market_microstructure_1s.jsonl"],
    },
)


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=list).encode("utf-8")).hexdigest()[:16]


def registry() -> dict:
    return {
        "schema": REGISTRY_SCHEMA,
        "registered_utc": REGISTERED_UTC,
        "controls_per_event": CONTROLS_PER_EVENT,
        "control_match": list(CONTROL_MATCH),
        "hypotheses": [{**h, "spec_hash": spec_hash(h)} for h in HYPOTHESES],
    }
