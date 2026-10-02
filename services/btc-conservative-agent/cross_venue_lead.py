"""Per-second cross-venue lead (XVL) evaluator shared by runtime, shadow and analyzer.

Rule (pre-registered as H5_XVENUE_LEAD_60S_20261002): ``lead`` is the mean of
the Binance and Bybit mid returns over the last ``lookback_sec`` seconds minus
the Bitfinex mid return over the same seconds. ``|lead| >= threshold`` on fresh
feeds is a trigger in ``sign(lead)``.

Clock: the shared epoch-second buckets of ``cross_venue_tape`` and the Bitfinex
``market_microstructure_1s`` tape. Bucket ``s`` carries the quote as of
``s + 1``, so the newest bucket known at wall time ``t`` is ``floor(t) - 1``
(the anchor).

Shadow outcome (the research markout, identical to
``research.lead_lag_report.follow_section``): Bitfinex taker entry at bucket
``anchor + entry_delay`` (ask for LONG, bid for SHORT) and taker exit at bucket
``anchor + entry_delay + hold`` (bid / ask). After spread; no exchange fee is
applied here (Bitfinex fees are owned by ``bitfinex_cost_profile``).

Nothing here places, changes or cancels an order, or reads toggles, relay
state or fee constants. The other venues are price data only.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional

import cross_venue_tape as cvt

SHADOW_FILE = "xvl_shadow_signals.jsonl"
TRIGGER_SCHEMA = "xvl_shadow_trigger_v1"
OUTCOME_SCHEMA = "xvl_shadow_outcome_v1"
STATUS_SCHEMA = "xvl_evaluator_status_v1"
SIGNAL_CLOCK = "PER_SECOND_CROSS_VENUE_EVALUATOR"

STATUS_TRIGGER = "TRIGGER"
STATUS_BELOW = "BELOW_THRESHOLD"
STATUS_SPREAD = "SPREAD_ABOVE_MAX"
STATUS_STALE = "STALE_FEED"
STATUS_DUPLICATE = "DUPLICATE_ANCHOR"
LONG, SHORT = "LONG", "SHORT"
MATURITY_GRACE_SEC = 30
MAX_PENDING = 512


@dataclass(frozen=True)
class LeadRule:
    lookback_sec: int = 10
    lead_threshold_bps: float = 8.0
    venues: tuple = ("binance", "bybit")
    max_venue_age_sec: float = 2.0
    max_bfx_age_sec: float = 2.0
    max_spread_bps: float = 3.0
    hold_sec: int = 60
    entry_delay_sec: int = 1

    @classmethod
    def from_policy(cls, entry: Mapping[str, Any], exit_policy: Mapping[str, Any]) -> "LeadRule":
        return cls(
            lookback_sec=int(entry["lookback_sec"]),
            lead_threshold_bps=float(entry["lead_threshold_bps"]),
            venues=tuple(entry["leader_venues"]),
            max_venue_age_sec=float(entry["max_venue_age_sec"]),
            max_bfx_age_sec=float(entry["max_bbo_age_sec"]),
            max_spread_bps=float(entry["max_spread_bps"]),
            hold_sec=int(exit_policy["max_duration_sec"]),
            entry_delay_sec=int(entry.get("shadow_entry_delay_sec", 1)),
        )

    def identity(self) -> dict:
        out = asdict(self)
        out["venues"] = list(self.venues)
        return out


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ret_bp(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if not now or not then:
        return None
    return (now / then - 1.0) * 1e4


def _mid(quote) -> Optional[float]:
    if not quote:
        return None
    bid, ask = _finite(quote[0]), _finite(quote[1])
    if not bid or not ask or bid <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def lead_from_returns(venue_rets: Mapping[str, Optional[float]], bfx_ret: Optional[float],
                      venues) -> Optional[float]:
    """mean(venue returns) - Bitfinex return; None unless every input is known."""
    values = [venue_rets.get(v) for v in venues]
    if bfx_ret is None or not values or any(v is None for v in values):
        return None
    return sum(values) / len(values) - bfx_ret


def evaluate_second(rule: LeadRule, *, now: float, live: Optional[Mapping[str, Any]],
                    bfx_quotes: Mapping[int, tuple], bfx_bbo_ts: Optional[float]) -> dict:
    """Causal verdict for the anchor bucket known at ``now`` (no state)."""
    anchor = int(math.floor(float(now))) - 1
    w = int(rule.lookback_sec)
    out: dict = {
        "anchor_bucket_ts": anchor,
        "status": STATUS_STALE,
        "stale_reasons": [],
        "venue_ret_bp": {},
        "venue_bbo_age_s": {},
        "collector_age_s": None,
        "bfx_ret_bp": None,
        "bfx_bbo_age_s": None,
        "lead_bp": None,
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
    for venue in rule.venues:
        cell = ((live or {}).get("venues") or {}).get(venue) or {}
        last = _finite(cell.get("last_bbo_ts"))
        age = None if last is None else round(now - last, 3)
        out["venue_bbo_age_s"][venue] = age
        if age is None or age > rule.max_venue_age_sec:
            reasons.append(f"VENUE_STALE:{venue}")
        ret = None
        if live is not None:
            ret = _ret_bp(cvt.live_mid_at(live, venue, anchor), cvt.live_mid_at(live, venue, anchor - w))
        out["venue_ret_bp"][venue] = None if ret is None else round(ret, 4)
        if ret is None:
            reasons.append(f"VENUE_WINDOW_INCOMPLETE:{venue}")
    q_now, q_then = bfx_quotes.get(anchor), bfx_quotes.get(anchor - w)
    bfx_ret = _ret_bp(_mid(q_now), _mid(q_then))
    out["bfx_ret_bp"] = None if bfx_ret is None else round(bfx_ret, 4)
    if bfx_ret is None:
        reasons.append("BFX_WINDOW_INCOMPLETE")
    bbo_ts = _finite(bfx_bbo_ts)
    out["bfx_bbo_age_s"] = None if bbo_ts is None else round(now - bbo_ts, 3)
    if bbo_ts is None or now - bbo_ts > rule.max_bfx_age_sec:
        reasons.append("BFX_BBO_STALE")
    if q_now and _mid(q_now):
        bid, ask = float(q_now[0]), float(q_now[1])
        out["bid"], out["ask"] = bid, ask
        out["spread_bps"] = round((ask - bid) / ((bid + ask) / 2.0) * 1e4, 4)
    lead = lead_from_returns(out["venue_ret_bp"], bfx_ret, rule.venues)
    if lead is not None:
        out["lead_bp"] = round(lead, 4)
        out["side"] = LONG if lead > 0 else SHORT if lead < 0 else None
    if reasons:
        return out
    if lead is None or abs(lead) < rule.lead_threshold_bps:
        out["status"] = STATUS_BELOW
    elif out["spread_bps"] is None or out["spread_bps"] > rule.max_spread_bps:
        out["status"] = STATUS_SPREAD
    else:
        out["status"] = STATUS_TRIGGER
    return out


def is_qualifying_lead(evaluation: Mapping[str, Any], rule: LeadRule) -> bool:
    lead = evaluation.get("lead_bp")
    return lead is not None and abs(float(lead)) >= rule.lead_threshold_bps and bool(evaluation.get("side"))


def shadow_outcome(side: str, entry_quote, exit_quote) -> dict:
    """After-spread taker markout between two Bitfinex buckets (bp)."""
    m0, m1 = _mid(entry_quote), _mid(exit_quote)
    if m0 is None or m1 is None or side not in (LONG, SHORT):
        return {"status": "MISSING_QUOTE"}
    sign = 1.0 if side == LONG else -1.0
    bid0, ask0 = float(entry_quote[0]), float(entry_quote[1])
    bid1, ask1 = float(exit_quote[0]), float(exit_quote[1])
    entry, exit_ = (ask0, bid1) if sign > 0 else (bid0, ask1)
    net = sign * (exit_ - entry) / entry * 1e4
    return {
        "status": "OK",
        "entry_bid": bid0, "entry_ask": ask0, "exit_bid": bid1, "exit_ask": ask1,
        "entry_price": entry, "exit_price": exit_,
        "net_bp_after_spread": round(net, 4),
        "gross_mid_bp": round(sign * (m1 - m0) / m0 * 1e4, 4),
        "win": net > 0,
    }


class LeadEvaluator:
    """Stateful 1 Hz wrapper: episodes, capacity-1 replica flag and outcome maturity.

    Subclasses (other cross-venue triggers) override ``evaluate``, ``qualifies``
    and ``signal_fields`` plus the class-level identity constants; episode,
    capacity-1 and outcome maturity logic stays shared.
    """

    ID_PREFIX = "xvl"
    TRIGGER_SCHEMA = TRIGGER_SCHEMA
    OUTCOME_SCHEMA = OUTCOME_SCHEMA
    SHADOW_FILE = SHADOW_FILE
    SIGNAL_KEY = "lead_bp"
    # Trigger keys copied into the paper order's signal features.
    TRIGGER_FEATURE_KEY = "xvl_trigger"
    TRIGGER_FEATURE_FIELDS = (
        "trigger_id", "anchor_bucket_ts", "evaluated_ts", "side", "lead_bp",
        "venue_ret_bp", "bfx_ret_bp", "venue_bbo_age_s", "collector_age_s",
        "bfx_bbo_age_s", "bfx_bid", "bfx_ask", "spread_bps", "episode_id",
        "episode_first", "rule",
    )

    def __init__(self, rule: LeadRule, *, policy_id: str = "", policy_signature: str = "",
                 max_pending: int = MAX_PENDING) -> None:
        self.rule = rule
        self.policy_id = policy_id
        self.policy_signature = policy_signature
        self._pending: "OrderedDict[str, dict]" = OrderedDict()
        self._max_pending = int(max_pending)
        self._last_anchor: Optional[int] = None
        self._episode: Optional[dict] = None
        self._cap1_busy_until = -1
        self.stats = {
            "evaluations": 0, "missed_seconds": 0, "by_status": {}, "triggers_logged": 0,
            "qualifying": 0, "outcomes_ok": 0, "outcomes_missing": 0, "pending_evicted": 0,
            "last_anchor": None, "last_trigger_anchor": None,
        }

    def pending_count(self) -> int:
        return len(self._pending)

    def evaluate(self, *, now: float, live, bfx_quotes: Mapping[int, tuple],
                 bfx_bbo_ts: Optional[float]) -> dict:
        return evaluate_second(self.rule, now=now, live=live, bfx_quotes=bfx_quotes,
                               bfx_bbo_ts=bfx_bbo_ts)

    def qualifies(self, evaluation: Mapping[str, Any]) -> bool:
        return is_qualifying_lead(evaluation, self.rule)

    def signal_fields(self, evaluation: Mapping[str, Any]) -> dict:
        return {
            "lead_bp": evaluation["lead_bp"],
            "venue_ret_bp": dict(evaluation["venue_ret_bp"]),
            "bfx_ret_bp": evaluation["bfx_ret_bp"],
        }

    def step(self, *, now: float, live, bfx_quotes: Mapping[int, tuple],
             bfx_bbo_ts: Optional[float]) -> tuple:
        """(evaluation, trigger_row or None, [outcome rows]) for the current anchor."""
        evaluation = self.evaluate(now=now, live=live, bfx_quotes=bfx_quotes,
                                   bfx_bbo_ts=bfx_bbo_ts)
        anchor = evaluation["anchor_bucket_ts"]
        outcomes = self._mature(anchor, bfx_quotes)
        if self._last_anchor is not None and anchor <= self._last_anchor:
            evaluation["status"] = STATUS_DUPLICATE
            return evaluation, None, outcomes
        if self._last_anchor is not None and anchor > self._last_anchor + 1:
            self.stats["missed_seconds"] += anchor - self._last_anchor - 1
        self._last_anchor = anchor
        self.stats["evaluations"] += 1
        self.stats["last_anchor"] = anchor
        status = evaluation["status"]
        by_status = self.stats["by_status"]
        by_status[status] = by_status.get(status, 0) + 1
        if not self.qualifies(evaluation):
            self._episode = None
            evaluation["cap1_take"] = False
            return evaluation, None, outcomes
        episode = self._episode
        if episode is None or episode["side"] != evaluation["side"] or episode["last_anchor"] != anchor - 1:
            episode = {"id": f"{self.ID_PREFIX}-ep-{anchor}", "side": evaluation["side"], "start": anchor}
        episode["last_anchor"] = anchor
        first = episode["start"] == anchor
        self._episode = episode
        cap1_take = status == STATUS_TRIGGER and anchor > self._cap1_busy_until
        if cap1_take:
            self._cap1_busy_until = anchor + self.rule.entry_delay_sec + self.rule.hold_sec
        evaluation["cap1_take"] = cap1_take
        if status == STATUS_TRIGGER:
            self.stats["qualifying"] += 1
            self.stats["last_trigger_anchor"] = anchor
        trigger_id = f"{self.ID_PREFIX}-{anchor}"
        evaluation["trigger_id"] = trigger_id
        row = {
            "schema": self.TRIGGER_SCHEMA,
            "trigger_id": trigger_id,
            "anchor_bucket_ts": anchor,
            "evaluated_ts": round(float(now), 3),
            "side": evaluation["side"],
            "gate": status,
            "qualifies": status == STATUS_TRIGGER,
            **self.signal_fields(evaluation),
            "venue_bbo_age_s": dict(evaluation["venue_bbo_age_s"]),
            "collector_age_s": evaluation["collector_age_s"],
            "bfx_bbo_age_s": evaluation["bfx_bbo_age_s"],
            "stale_reasons": list(evaluation["stale_reasons"]),
            "bfx_bid": evaluation["bid"],
            "bfx_ask": evaluation["ask"],
            "spread_bps": evaluation["spread_bps"],
            "episode_id": episode["id"],
            "episode_first": first,
            "cap1_take": cap1_take,
            "entry_bucket_ts": anchor + self.rule.entry_delay_sec,
            "exit_bucket_ts": anchor + self.rule.entry_delay_sec + self.rule.hold_sec,
            "rule": self.rule.identity(),
            "policy_id": self.policy_id,
            "policy_signature": self.policy_signature,
            "shadow_only": True,
        }
        self.stats["triggers_logged"] += 1
        self._pending[trigger_id] = row
        while len(self._pending) > self._max_pending:
            self._pending.popitem(last=False)
            self.stats["pending_evicted"] += 1
        return evaluation, row, outcomes

    def _mature(self, anchor: int, bfx_quotes: Mapping[int, tuple]) -> list:
        out = []
        for trigger_id in list(self._pending):
            row = self._pending[trigger_id]
            exit_ts = int(row["exit_bucket_ts"])
            if anchor < exit_ts:
                break
            result = shadow_outcome(row["side"], bfx_quotes.get(int(row["entry_bucket_ts"])),
                                    bfx_quotes.get(exit_ts))
            if result["status"] != "OK" and anchor < exit_ts + MATURITY_GRACE_SEC:
                continue
            del self._pending[trigger_id]
            self.stats["outcomes_ok" if result["status"] == "OK" else "outcomes_missing"] += 1
            out.append({
                "schema": self.OUTCOME_SCHEMA,
                "trigger_id": trigger_id,
                "anchor_bucket_ts": row["anchor_bucket_ts"],
                "side": row["side"],
                "gate": row["gate"],
                "qualifies": row["qualifies"],
                "cap1_take": row["cap1_take"],
                "episode_id": row["episode_id"],
                self.SIGNAL_KEY: row[self.SIGNAL_KEY],
                "entry_bucket_ts": row["entry_bucket_ts"],
                "exit_bucket_ts": exit_ts,
                "hold_sec": self.rule.hold_sec,
                "fee_applied": False,
                "policy_signature": self.policy_signature,
                "shadow_only": True,
                **result,
            })
        return out

    def snapshot(self) -> dict:
        return {"schema": STATUS_SCHEMA, "rule": self.rule.identity(),
                "pending_outcomes": len(self._pending), **{k: (dict(v) if isinstance(v, dict) else v)
                                                          for k, v in self.stats.items()}}
