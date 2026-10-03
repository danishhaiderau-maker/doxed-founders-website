"""Per-second cross-venue session-gated follow (XVS) evaluator.

Rule (pre-registered as H10_XVENUE_SESSION_FOLLOW_60M_20261004): every second
evaluate this tile's own copy of the cross-venue lead rule (Binance/Bybit 10 s
mid return leads Bitfinex by >= threshold) and the cross-venue premium rule
(Binance/Bybit premium over Bitfinex versus its own 60-minute mean beyond the
long/short thresholds). Either one triggering is a trigger in its direction;
both triggering in opposite directions is a ``CONFLICT`` and never trades. A
trigger outside the frozen ``allowed_sessions`` (UTC Asia 0-8, EU 8-16,
US 16-24) is ``SESSION_GATED``.

The thresholds are copied into this tile's registry entry, so the generic lead
and premium rules carry no tile identity of their own.
Shadow outcome: the same after-spread Bitfinex taker markout as
``cross_venue_lead`` at ``anchor + entry_delay + hold``. Nothing here places,
changes or cancels an order, or reads toggles, relay state or fee constants.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from cross_venue_lead import (
    STATUS_BELOW,
    STATUS_TRIGGER,
    LeadEvaluator,
    LeadRule,
    evaluate_second as lead_evaluate_second,
)
from cross_venue_premium import (
    PremiumRule,
    PremiumTracker,
    evaluate_second as premium_evaluate_second,
)

SHADOW_FILE = "xvs_shadow_signals.jsonl"
TRIGGER_SCHEMA = "xvs_shadow_trigger_v1"
OUTCOME_SCHEMA = "xvs_shadow_outcome_v1"
STATUS_CONFLICT = "CONFLICT"
STATUS_SESSION_GATED = "SESSION_GATED"
SESSIONS = ("ASIA", "EU", "US")
# 60-minute outcomes at up to one trigger per second.
MAX_PENDING = 4096


def utc_session(ts: float) -> str:
    hour = time.gmtime(float(ts)).tm_hour
    return "ASIA" if hour < 8 else "EU" if hour < 16 else "US"


@dataclass(frozen=True)
class SessionFollowRule:
    lead: LeadRule
    premium: PremiumRule
    allowed_sessions: tuple = SESSIONS
    hold_sec: int = 3600
    entry_delay_sec: int = 1

    @classmethod
    def from_policy(cls, entry: Mapping[str, Any], exit_policy: Mapping[str, Any]) -> "SessionFollowRule":
        hold = int(exit_policy["max_duration_sec"])
        delay = int(entry.get("shadow_entry_delay_sec", 1))
        venues = tuple(entry["leader_venues"])
        lead = LeadRule(
            lookback_sec=int(entry["lookback_sec"]),
            lead_threshold_bps=float(entry["lead_threshold_bps"]),
            venues=venues,
            max_venue_age_sec=float(entry["max_venue_age_sec"]),
            max_bfx_age_sec=float(entry["max_bbo_age_sec"]),
            max_spread_bps=float(entry["max_spread_bps"]),
            hold_sec=hold, entry_delay_sec=delay,
        )
        premium = PremiumRule(
            venues=venues,
            mean_window_sec=int(entry["premium_mean_window_sec"]),
            min_mean_samples=int(entry["premium_min_mean_samples"]),
            long_threshold_bps=float(entry["premium_long_threshold_bps"]),
            short_threshold_bps=float(entry["premium_short_threshold_bps"]),
            max_venue_age_sec=float(entry["max_venue_age_sec"]),
            max_bfx_age_sec=float(entry["max_bbo_age_sec"]),
            max_spread_bps=float(entry["max_spread_bps"]),
            max_fill_forward_sec=int(entry.get("max_fill_forward_sec", 5)),
            hold_sec=hold, entry_delay_sec=delay,
        )
        sessions = tuple(str(s).upper() for s in entry["allowed_sessions"])
        if not sessions or set(sessions) - set(SESSIONS):
            raise ValueError(f"allowed_sessions must be a non-empty subset of {SESSIONS}")
        return cls(lead=lead, premium=premium, allowed_sessions=sessions,
                   hold_sec=hold, entry_delay_sec=delay)

    def identity(self) -> dict:
        out = {"lead": self.lead.identity(), "premium": self.premium.identity(),
               "allowed_sessions": list(self.allowed_sessions),
               "hold_sec": self.hold_sec, "entry_delay_sec": self.entry_delay_sec}
        return out


def combine(lead_eval: Mapping[str, Any], premium_eval: Mapping[str, Any],
            rule: SessionFollowRule, *, now: float) -> dict:
    """One verdict from the two causal sub-verdicts for the same anchor (no state)."""
    lead_hit = lead_eval.get("status") == STATUS_TRIGGER and lead_eval.get("side")
    premium_hit = premium_eval.get("status") == STATUS_TRIGGER and premium_eval.get("side")
    base = lead_eval if lead_hit or not premium_hit else premium_eval
    stale = list(dict.fromkeys(list(lead_eval.get("stale_reasons") or ())
                               + list(premium_eval.get("stale_reasons") or ())))
    out = {
        "anchor_bucket_ts": int(lead_eval["anchor_bucket_ts"]),
        "status": base.get("status") or STATUS_BELOW,
        "stale_reasons": stale,
        "venue_bbo_age_s": dict(base.get("venue_bbo_age_s") or {}),
        "collector_age_s": base.get("collector_age_s"),
        "bfx_bbo_age_s": base.get("bfx_bbo_age_s"),
        "bid": base.get("bid"), "ask": base.get("ask"), "spread_bps": base.get("spread_bps"),
        "lead_bp": lead_eval.get("lead_bp"),
        "lead_status": lead_eval.get("status"),
        "premium_dev_bp": premium_eval.get("premium_dev_bp"),
        "premium_status": premium_eval.get("status"),
        "session": utc_session(now),
        "trigger_source": None,
        "side": None,
    }
    if lead_hit and premium_hit and lead_eval["side"] != premium_eval["side"]:
        out["status"] = STATUS_CONFLICT
        return out
    if not (lead_hit or premium_hit):
        return out
    out["side"] = lead_eval["side"] if lead_hit else premium_eval["side"]
    out["trigger_source"] = "LEAD+PREMIUM" if lead_hit and premium_hit else "LEAD" if lead_hit else "PREMIUM"
    out["status"] = STATUS_TRIGGER if out["session"] in rule.allowed_sessions else STATUS_SESSION_GATED
    return out


def is_qualifying(evaluation: Mapping[str, Any]) -> bool:
    return bool(evaluation.get("side")) and evaluation.get("status") in (STATUS_TRIGGER, STATUS_SESSION_GATED)


class SessionFollowEvaluator(LeadEvaluator):
    """XVS stateful 1 Hz wrapper; episode and outcome maturity logic from ``LeadEvaluator``."""

    ID_PREFIX = "xvs"
    TRIGGER_SCHEMA = TRIGGER_SCHEMA
    OUTCOME_SCHEMA = OUTCOME_SCHEMA
    SHADOW_FILE = SHADOW_FILE
    SIGNAL_KEY = "trigger_source"
    TRIGGER_FEATURE_KEY = "xvs_trigger"
    TRIGGER_FEATURE_FIELDS = (
        "trigger_id", "anchor_bucket_ts", "evaluated_ts", "side", "trigger_source", "session",
        "lead_bp", "premium_dev_bp", "lead_status", "premium_status",
        "venue_bbo_age_s", "collector_age_s", "bfx_bbo_age_s", "bfx_bid", "bfx_ask",
        "spread_bps", "episode_id", "episode_first", "rule",
    )

    def __init__(self, rule: SessionFollowRule, *, max_pending: int = MAX_PENDING, **kwargs) -> None:
        super().__init__(rule, max_pending=max_pending, **kwargs)
        self.tracker = PremiumTracker(rule.premium)

    def evaluate(self, *, now, live, bfx_quotes, bfx_bbo_ts) -> dict:
        lead_eval = lead_evaluate_second(self.rule.lead, now=now, live=live, bfx_quotes=bfx_quotes,
                                         bfx_bbo_ts=bfx_bbo_ts)
        premium_eval = premium_evaluate_second(self.rule.premium, self.tracker, now=now, live=live,
                                               bfx_quotes=bfx_quotes, bfx_bbo_ts=bfx_bbo_ts)
        return combine(lead_eval, premium_eval, self.rule, now=now)

    def qualifies(self, evaluation) -> bool:
        return is_qualifying(evaluation)

    def signal_fields(self, evaluation) -> dict:
        return {
            "trigger_source": evaluation["trigger_source"],
            "session": evaluation["session"],
            "lead_bp": evaluation["lead_bp"],
            "premium_dev_bp": evaluation["premium_dev_bp"],
            "lead_status": evaluation["lead_status"],
            "premium_status": evaluation["premium_status"],
        }

    def latest_features(self) -> dict:
        return self.tracker.facts()
