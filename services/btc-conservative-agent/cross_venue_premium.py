"""Generic per-second cross-venue premium-extreme rule and evaluator shared by runtime, shadow and analyzer.

Rule (INDICATOR-SEARCH-MODEL-A signal 1, ``xv_prem_dev60``): the premium is the mean over Binance and Bybit of
``(venue_mid / bitfinex_mid - 1) * 1e4`` for one epoch-second bucket. Its
deviation is the premium minus its own trailing 3600-bucket mean (the current
bucket included; at least 1200 premium samples required). A deviation
``>= long_threshold_bps`` (+1.75) is a LONG trigger and ``<= short_threshold_bps``
(-1.88) a SHORT trigger: Bitfinex is cheap/rich against the leaders versus its
usual gap and is expected to follow them. Thresholds are the 20/80 tails fixed
on the MODEL-A development window and must never be re-fitted on live data.

Clock and outcome are identical to ``cross_venue_lead`` (bucket ``s`` carries
the quote as of ``s + 1``; anchor = ``floor(now) - 1``; taker entry at
``anchor + entry_delay``, taker exit ``hold_sec`` later, after spread, no fee).
Leader mids are forward-filled for at most ``max_fill_forward_sec`` buckets,
as in the research dataset; triggers additionally require every feed fresh.

Nothing here places, changes or cancels an order, or reads toggles, relay
state or fee constants. The other venues are price data only.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional

import cross_venue_tape as cvt
from cross_venue_lead import (
    LONG,
    SHORT,
    STATUS_BELOW,
    STATUS_SPREAD,
    STATUS_STALE,
    STATUS_TRIGGER,
    LeadEvaluator,
    _finite,
    _mid,
)

SHADOW_FILE = "xvp_shadow_signals.jsonl"
TRIGGER_SCHEMA = "xvp_shadow_trigger_v1"
OUTCOME_SCHEMA = "xvp_shadow_outcome_v1"
STATUS_WARMING = "MEAN_WARMING_UP"
FEATURES_SCHEMA = "cross_venue_premium_features_v1"
LEAD_WINDOWS_SEC = (60, 300)


@dataclass(frozen=True)
class PremiumRule:
    venues: tuple = ("binance", "bybit")
    mean_window_sec: int = 3600
    min_mean_samples: int = 1200
    long_threshold_bps: float = 1.75
    short_threshold_bps: float = -1.88
    max_venue_age_sec: float = 2.0
    max_bfx_age_sec: float = 2.0
    max_spread_bps: float = 3.0
    max_fill_forward_sec: int = 5
    hold_sec: int = 60
    entry_delay_sec: int = 1

    @classmethod
    def from_policy(cls, entry: Mapping[str, Any], exit_policy: Mapping[str, Any]) -> "PremiumRule":
        return cls(
            venues=tuple(entry["leader_venues"]),
            mean_window_sec=int(entry["premium_mean_window_sec"]),
            min_mean_samples=int(entry["premium_min_mean_samples"]),
            long_threshold_bps=float(entry["premium_long_threshold_bps"]),
            short_threshold_bps=float(entry["premium_short_threshold_bps"]),
            max_venue_age_sec=float(entry["max_venue_age_sec"]),
            max_bfx_age_sec=float(entry["max_bbo_age_sec"]),
            max_spread_bps=float(entry["max_spread_bps"]),
            max_fill_forward_sec=int(entry.get("max_fill_forward_sec", 5)),
            hold_sec=int(exit_policy["max_duration_sec"]),
            entry_delay_sec=int(entry.get("shadow_entry_delay_sec", 1)),
        )

    def identity(self) -> dict:
        out = asdict(self)
        out["venues"] = list(self.venues)
        return out

    def side_for(self, deviation: Optional[float]) -> Optional[str]:
        if deviation is None:
            return None
        if deviation >= self.long_threshold_bps:
            return LONG
        if deviation <= self.short_threshold_bps:
            return SHORT
        return None


def premium_bp(venue_mids: Mapping[str, Optional[float]], bfx_mid: Optional[float],
               venues) -> tuple:
    """(mean premium bp over venues with a mid, {venue: premium bp})."""
    per_venue: dict = {}
    if not bfx_mid or bfx_mid <= 0:
        return None, {v: None for v in venues}
    for venue in venues:
        mid = venue_mids.get(venue)
        per_venue[venue] = None if not mid or mid <= 0 else round((mid / bfx_mid - 1.0) * 1e4, 4)
    values = [v for v in per_venue.values() if v is not None]
    return (sum(values) / len(values) if values else None), per_venue


class PremiumTracker:
    """Bounded per-bucket history: premium (for its trailing mean) and mids (for leads).

    O(1) per bucket; memory is three rings of ``mean_window_sec`` floats. A gap
    in buckets is recorded as missing samples, never interpolated.
    """

    def __init__(self, rule: PremiumRule) -> None:
        self.rule = rule
        n = int(rule.mean_window_sec)
        self._prem: deque = deque(maxlen=n)
        self._leader_mid: deque = deque(maxlen=n)
        self._bfx_mid: deque = deque(maxlen=n)
        self._sum = 0.0
        self._count = 0
        self._last_bucket: Optional[int] = None
        self._last_seen: dict = {}

    def _push(self, prem: Optional[float], leader_mid: Optional[float], bfx_mid: Optional[float]) -> None:
        if len(self._prem) == self._prem.maxlen:
            old = self._prem[0]
            if old is not None:
                self._sum -= old
                self._count -= 1
        self._prem.append(prem)
        self._leader_mid.append(leader_mid)
        self._bfx_mid.append(bfx_mid)
        if prem is not None:
            self._sum += prem
            self._count += 1

    def _filled(self, venue: str, bucket: int, value: Optional[float]) -> Optional[float]:
        if value:
            self._last_seen[venue] = (bucket, value)
            return value
        seen = self._last_seen.get(venue)
        if seen and 0 <= bucket - seen[0] <= self.rule.max_fill_forward_sec:
            return seen[1]
        return None

    def observe(self, bucket: int, venue_mids: Mapping[str, Optional[float]],
                bfx_mid: Optional[float]) -> dict:
        """Record one bucket (idempotent per bucket) and return its premium facts."""
        bucket = int(bucket)
        if self._last_bucket is not None and bucket <= self._last_bucket:
            return self.facts()
        if self._last_bucket is not None:
            for _ in range(min(bucket - self._last_bucket - 1, self.rule.mean_window_sec)):
                self._push(None, None, None)
        mids = {v: self._filled(v, bucket, _finite(venue_mids.get(v))) for v in self.rule.venues}
        bfx = self._filled("bitfinex", bucket, _finite(bfx_mid))
        prem, per_venue = premium_bp(mids, bfx, self.rule.venues)
        present = [m for m in mids.values() if m]
        leader = sum(present) / len(present) if present else None
        self._push(prem, leader, bfx)
        self._last_bucket = bucket
        self._last_per_venue = per_venue
        return self.facts()

    def mean(self) -> Optional[float]:
        if self._count < self.rule.min_mean_samples or self._count <= 0:
            return None
        return self._sum / self._count

    def lead_bp(self, window_sec: int) -> Optional[float]:
        """Leader mean-mid return minus Bitfinex return over ``window_sec`` buckets."""
        w = int(window_sec)
        if w <= 0 or len(self._bfx_mid) <= w:
            return None
        l_now, l_then = self._leader_mid[-1], self._leader_mid[-1 - w]
        b_now, b_then = self._bfx_mid[-1], self._bfx_mid[-1 - w]
        if not (l_now and l_then and b_now and b_then):
            return None
        return (l_now / l_then - 1.0) * 1e4 - (b_now / b_then - 1.0) * 1e4

    def facts(self) -> dict:
        prem = self._prem[-1] if self._prem else None
        mean = self.mean()
        return {
            "schema": FEATURES_SCHEMA,
            "bucket_ts": self._last_bucket,
            "premium_bp": None if prem is None else round(prem, 4),
            "venue_premium_bp": dict(getattr(self, "_last_per_venue", {}) or {}),
            "premium_mean_bp": None if mean is None else round(mean, 4),
            "premium_dev_bp": None if prem is None or mean is None else round(prem - mean, 4),
            "mean_samples": self._count,
            **{f"lead_{w}s_bp": (None if self.lead_bp(w) is None else round(self.lead_bp(w), 4))
               for w in LEAD_WINDOWS_SEC},
        }


def evaluate_second(rule: PremiumRule, tracker: PremiumTracker, *, now: float,
                    live: Optional[Mapping[str, Any]], bfx_quotes: Mapping[int, tuple],
                    bfx_bbo_ts: Optional[float]) -> dict:
    """Causal verdict for the anchor bucket known at ``now``; updates ``tracker`` once per bucket."""
    anchor = int(math.floor(float(now))) - 1
    out: dict = {
        "anchor_bucket_ts": anchor,
        "status": STATUS_STALE,
        "stale_reasons": [],
        "venue_bbo_age_s": {},
        "collector_age_s": None,
        "bfx_bbo_age_s": None,
        "premium_bp": None,
        "venue_premium_bp": {},
        "premium_mean_bp": None,
        "premium_dev_bp": None,
        "mean_samples": 0,
        "side": None,
        "bid": None,
        "ask": None,
        "spread_bps": None,
    }
    reasons = out["stale_reasons"]
    if not isinstance(live, Mapping) or live.get("schema") != cvt.LIVE_SCHEMA:
        reasons.append("NO_LIVE_STATE")
        live = None
    else:
        written = _finite(live.get("written_ts"))
        out["collector_age_s"] = None if written is None else round(now - written, 3)
        if written is None or now - written > rule.max_venue_age_sec:
            reasons.append("COLLECTOR_STALE")
    venue_mids = {}
    for venue in rule.venues:
        cell = ((live or {}).get("venues") or {}).get(venue) or {}
        last = _finite(cell.get("last_bbo_ts"))
        age = None if last is None else round(now - last, 3)
        out["venue_bbo_age_s"][venue] = age
        if age is None or age > rule.max_venue_age_sec:
            reasons.append(f"VENUE_STALE:{venue}")
        venue_mids[venue] = cvt.live_mid_at(live, venue, anchor) if live is not None else None
        if not venue_mids[venue]:
            reasons.append(f"VENUE_MID_MISSING:{venue}")
    q_now = bfx_quotes.get(anchor)
    bfx_mid = _mid(q_now)
    if bfx_mid is None:
        reasons.append("BFX_MID_MISSING")
    bbo_ts = _finite(bfx_bbo_ts)
    out["bfx_bbo_age_s"] = None if bbo_ts is None else round(now - bbo_ts, 3)
    if bbo_ts is None or now - bbo_ts > rule.max_bfx_age_sec:
        reasons.append("BFX_BBO_STALE")
    if q_now and bfx_mid:
        bid, ask = float(q_now[0]), float(q_now[1])
        out["bid"], out["ask"] = bid, ask
        out["spread_bps"] = round((ask - bid) / ((bid + ask) / 2.0) * 1e4, 4)
    facts = tracker.observe(anchor, venue_mids, bfx_mid)
    for key in ("premium_bp", "venue_premium_bp", "premium_mean_bp", "premium_dev_bp", "mean_samples"):
        out[key] = facts.get(key)
    out["side"] = rule.side_for(out["premium_dev_bp"])
    if reasons:
        return out
    if out["premium_mean_bp"] is None:
        out["status"] = STATUS_WARMING
    elif out["side"] is None:
        out["status"] = STATUS_BELOW
    elif out["spread_bps"] is None or out["spread_bps"] > rule.max_spread_bps:
        out["status"] = STATUS_SPREAD
    else:
        out["status"] = STATUS_TRIGGER
    return out


def is_qualifying_premium(evaluation: Mapping[str, Any], rule: PremiumRule) -> bool:
    return bool(evaluation.get("side")) and rule.side_for(evaluation.get("premium_dev_bp")) == evaluation.get("side")


class PremiumEvaluator(LeadEvaluator):
    """XVP stateful 1 Hz wrapper; episode, capacity-1 and maturity logic from ``LeadEvaluator``."""

    ID_PREFIX = "xvp"
    TRIGGER_SCHEMA = TRIGGER_SCHEMA
    OUTCOME_SCHEMA = OUTCOME_SCHEMA
    SHADOW_FILE = SHADOW_FILE
    SIGNAL_KEY = "premium_dev_bp"
    TRIGGER_FEATURE_KEY = "xvp_trigger"
    TRIGGER_FEATURE_FIELDS = (
        "trigger_id", "anchor_bucket_ts", "evaluated_ts", "side", "premium_dev_bp",
        "premium_bp", "premium_mean_bp", "venue_premium_bp", "mean_samples",
        "venue_bbo_age_s", "collector_age_s", "bfx_bbo_age_s", "bfx_bid", "bfx_ask",
        "spread_bps", "episode_id", "episode_first", "rule",
    )

    def __init__(self, rule: PremiumRule, **kwargs) -> None:
        super().__init__(rule, **kwargs)
        self.tracker = PremiumTracker(rule)

    def evaluate(self, *, now, live, bfx_quotes, bfx_bbo_ts) -> dict:
        return evaluate_second(self.rule, self.tracker, now=now, live=live,
                               bfx_quotes=bfx_quotes, bfx_bbo_ts=bfx_bbo_ts)

    def qualifies(self, evaluation) -> bool:
        return is_qualifying_premium(evaluation, self.rule)

    def signal_fields(self, evaluation) -> dict:
        return {
            "premium_dev_bp": evaluation["premium_dev_bp"],
            "premium_bp": evaluation["premium_bp"],
            "premium_mean_bp": evaluation["premium_mean_bp"],
            "venue_premium_bp": dict(evaluation["venue_premium_bp"] or {}),
            "mean_samples": evaluation["mean_samples"],
        }

    def latest_features(self) -> dict:
        """Causal premium/lead facts for AI shadow prompts and feature snapshots."""
        return self.tracker.facts()
