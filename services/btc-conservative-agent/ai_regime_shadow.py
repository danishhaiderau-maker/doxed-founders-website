"""Shadow-only 60-minute regime prompt (logged alongside the live call; never gates orders).

The model answers a regime question, not a direction question: will the next
60 minutes trend, has the last hour's move exhausted, and how likely is the
last hour's direction to persist. It never names a side and never emits 0-100
scores. Each answer is scored against forward labels from the decision feature
snapshot stream and against two non-AI baselines with identical inputs:

* ``climatology`` - the expanding base rate of each label, i.e. the no-AI
  look-alike that knows nothing but history;
* ``stat_rule`` - a fixed, pre-registered rule on the same 12 facts.

Kill criterion (pre-registered in PREREGISTERED-HYPOTHESES-20261002.md, H8):
after 14 UTC days and at least ``KILL_MIN_SCORED`` scored non-abstain calls, the
prompt is removed unless its Brier score beats both baselines on persistence
AND on trending, each with the 2h-cluster bootstrap upper bound of
``brier_ai - brier_baseline`` below zero.
"""
from __future__ import annotations

import json
import math
import random
import threading
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional, Sequence

REGIME_PROMPT_SCHEMA = "ai_shadow_regime_prompt_v1"
REGIME_PROMPT_FILE = "ai_shadow_regime_prompt.jsonl"
REGIME_PROMPT_ID = "shadow_regime_60m_v1_20261002"
HYPOTHESIS_ID = "H8_AI_REGIME_60M_SHADOW_20261002"
HORIZON_SEC = 3600
MIN_INTERVAL_SEC = 900
DAILY_CAP = 96
MAX_TOKENS = 80

TRENDING_Z = 1.0
EXHAUSTION_RETRACE = 0.5
KILL_MIN_DAYS = 14
KILL_MIN_SCORED = 300
BOOTSTRAP_CLUSTER_SEC = 7200
BOOTSTRAP_RESAMPLES = 1000

FACT_KEYS = (
    "z15", "z60", "rv15_bp", "trend_score", "adx15m", "donchian_loc_3m",
    "flow_5m", "funding_bp_8h", "oi_change_1h_pct",
    "premium_dev_bp", "lead_60s_bp", "lead_300s_bp",
)
LABELS = ("trending", "exhausted", "persistence")

REGIME_SYSTEM_PROMPT = (
    "You are a calibrated regime forecaster for BTC-USD perpetual, 60-minute horizon.\n"
    "You receive precomputed, normalized facts. Do not recompute them and do not\n"
    "pick a trade direction. Most hours are not trending and most moves are close\n"
    "to a coin flip to persist; stay near base rates unless several independent\n"
    "facts agree. If data is stale or conflicted, abstain. Respond with JSON only."
)

REGIME_USER_TEMPLATE = (
    "QUESTION (next 60 minutes from as_of):\n"
    "trending: will |60m return| be at least 1 sigma (sigma = rv15 scaled to 60m)?\n"
    "exhausted: will the next 60m retrace at least half of the last 60m move?\n"
    "persistence: probability the next 60m return has the same sign as the last 60m return.\n\n"
    "FACTS (as_of {as_of_utc}; stale={stale})\n"
    "returns_z: 15m={z15} 60m={z60} (return / rv-scaled sigma) rv15={rv15_bp}bp per 10s\n"
    "trend: score={trend_score} (-3..+3 over 15m/1h/4h) adx15m={adx15m} donchian_3m={donchian_loc_3m}\n"
    "flow_5m={flow_5m} (-1 sell..+1 buy)\n"
    "derivs: funding={funding_bp_8h}bp/8h oi_1h={oi_change_1h_pct}%\n"
    "cross_venue: premium_dev={premium_dev_bp}bp (leaders minus Bitfinex vs 20m mean) "
    "lead_60s={lead_60s_bp}bp lead_300s={lead_300s_bp}bp (leader return minus Bitfinex)\n\n"
    "Return:\n"
    '{{"trending": true|false, "exhausted": true|false, '
    '"persistence": 0.00-1.00, "abstain": true|false}}'
)


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def build_regime_facts(compact_facts: Mapping[str, Any], premium: Optional[Mapping[str, Any]]) -> dict:
    """The 12 normalised facts, reused from the compact prompt plus cross-venue premium/lead."""
    premium = premium if isinstance(premium, Mapping) else {}
    facts = {
        "as_of_utc": compact_facts.get("as_of_utc"),
        "stale": bool(compact_facts.get("stale")),
    }
    for key in FACT_KEYS:
        source = premium if key in ("premium_dev_bp", "lead_60s_bp", "lead_300s_bp") else compact_facts
        facts[key] = source.get(key)
    facts["missing_facts"] = sorted(k for k in FACT_KEYS if facts.get(k) is None)
    return facts


def render_regime_messages(facts: Mapping[str, Any]) -> list:
    values = {k: ("null" if v is None else v) for k, v in facts.items()}
    return [
        {"role": "system", "content": REGIME_SYSTEM_PROMPT},
        {"role": "user", "content": REGIME_USER_TEMPLATE.format(**values)},
    ]


def parse_regime_response(text: str) -> dict:
    out = {"parse_status": "INVALID_JSON", "trending": None, "exhausted": None,
           "persistence": None, "abstain": None}
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        blob = json.loads(text[start:end])
    except (ValueError, TypeError, AttributeError):
        return out
    if not isinstance(blob, Mapping):
        return out
    for key in ("trending", "exhausted", "abstain"):
        out[key] = blob.get(key) if isinstance(blob.get(key), bool) else None
    persistence = _finite(blob.get("persistence"))
    out["persistence"] = persistence
    if (None in (out["trending"], out["exhausted"], out["abstain"])
            or persistence is None or not 0.0 <= persistence <= 1.0):
        out["parse_status"] = "OUT_OF_RANGE_OR_MISSING"
        return out
    out["parse_status"] = "OK"
    return out


def stat_rule(facts: Mapping[str, Any]) -> dict:
    """Pre-registered non-AI rule on the same facts (probabilities, never a side)."""
    z60 = _finite(facts.get("z60"))
    flow = _finite(facts.get("flow_5m"))
    trend = _finite(facts.get("trend_score"))
    if z60 is None:
        return {"trending": None, "exhausted": None, "persistence": None}
    move = 1.0 if z60 > 0 else -1.0 if z60 < 0 else 0.0
    aligned = trend is not None and move != 0 and trend * move >= 2
    opposing_flow = flow is not None and move != 0 and flow * move < -0.2
    return {
        "trending": 0.40 if abs(z60) >= TRENDING_Z else 0.25,
        "exhausted": 0.45 if abs(z60) >= 2.0 and opposing_flow else 0.25,
        "persistence": 0.55 if aligned and abs(z60) >= TRENDING_Z else 0.50,
    }


def forward_labels(facts: Mapping[str, Any], prior_ret_60m_bp: Optional[float],
                   fwd_ret_60m_bp: Optional[float]) -> dict:
    """Realised regime labels for one call; null when unobservable."""
    rv = _finite(facts.get("rv15_bp"))
    prior, fwd = _finite(prior_ret_60m_bp), _finite(fwd_ret_60m_bp)
    out = {"trending": None, "exhausted": None, "persistence": None}
    if fwd is None:
        return out
    if rv:
        out["trending"] = abs(fwd) >= TRENDING_Z * rv * math.sqrt(HORIZON_SEC / 10.0)
    if prior:
        out["persistence"] = (fwd > 0) == (prior > 0) and fwd != 0
        out["exhausted"] = (fwd * prior < 0) and abs(fwd) >= EXHAUSTION_RETRACE * abs(prior)
    return out


def _ai_probabilities(parsed: Mapping[str, Any]) -> dict:
    return {
        "trending": 1.0 if parsed.get("trending") else 0.0,
        "exhausted": 1.0 if parsed.get("exhausted") else 0.0,
        "persistence": _finite(parsed.get("persistence")),
    }


def _cluster_bootstrap_ub(diffs: Sequence[tuple], resamples: int, seed: str) -> Optional[float]:
    """95% upper bound of the mean paired Brier difference, resampling 2h clusters."""
    clusters = defaultdict(list)
    for ts, diff in diffs:
        clusters[int(ts // BOOTSTRAP_CLUSTER_SEC)].append(diff)
    keys = sorted(clusters)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        sample = [d for k in (rng.choice(keys) for _ in keys) for d in clusters[k]]
        means.append(sum(sample) / len(sample))
    means.sort()
    return round(means[int(0.95 * (len(means) - 1))], 6)


def score_calls(rows: Iterable[Mapping[str, Any]], *, resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """Brier for AI vs climatology vs stat_rule on matured, non-abstain, parsed calls.

    Each row needs ``decision_ts``, ``facts``, ``parsed`` and ``labels``.
    Climatology is the expanding base rate of each label over earlier rows only.
    """
    ordered = sorted((r for r in rows if isinstance(r, Mapping)), key=lambda r: _finite(r.get("decision_ts")) or 0)
    seen = defaultdict(lambda: [0, 0])
    per_label = {label: {"n": 0, "ai": 0.0, "climatology": 0.0, "stat_rule": 0.0,
                         "diff_climatology": [], "diff_stat_rule": []} for label in LABELS}
    scored, abstained, first_ts, last_ts = 0, 0, None, None
    for row in ordered:
        parsed = row.get("parsed") or {}
        labels = row.get("labels") or {}
        ts = _finite(row.get("decision_ts"))
        if ts is None or parsed.get("parse_status") != "OK":
            continue
        if parsed.get("abstain"):
            abstained += 1
            continue
        ai = _ai_probabilities(parsed)
        rule = stat_rule(row.get("facts") or {})
        counted = False
        for label in LABELS:
            outcome = labels.get(label)
            if outcome is None or ai[label] is None or rule[label] is None:
                continue
            y = 1.0 if outcome else 0.0
            hits, total = seen[label]
            clim = (hits + 1.0) / (total + 2.0)
            cell = per_label[label]
            b_ai, b_clim, b_rule = (ai[label] - y) ** 2, (clim - y) ** 2, (rule[label] - y) ** 2
            cell["n"] += 1
            cell["ai"] += b_ai
            cell["climatology"] += b_clim
            cell["stat_rule"] += b_rule
            cell["diff_climatology"].append((ts, b_ai - b_clim))
            cell["diff_stat_rule"].append((ts, b_ai - b_rule))
            seen[label][0] += int(y)
            seen[label][1] += 1
            counted = True
        if counted:
            scored += 1
            first_ts = ts if first_ts is None else first_ts
            last_ts = ts
    report = {"schema": "ai_regime_shadow_score_v1", "prompt_id": REGIME_PROMPT_ID,
              "hypothesis_id": HYPOTHESIS_ID, "scored_calls": scored, "abstained_calls": abstained,
              "span_days": None if first_ts is None else round((last_ts - first_ts) / 86400.0, 3),
              "labels": {}}
    for label, cell in per_label.items():
        n = cell["n"]
        report["labels"][label] = {
            "n": n,
            "brier_ai": None if not n else round(cell["ai"] / n, 6),
            "brier_climatology": None if not n else round(cell["climatology"] / n, 6),
            "brier_stat_rule": None if not n else round(cell["stat_rule"] / n, 6),
            "ub95_ai_minus_climatology": _cluster_bootstrap_ub(cell["diff_climatology"], resamples, f"{label}-clim"),
            "ub95_ai_minus_stat_rule": _cluster_bootstrap_ub(cell["diff_stat_rule"], resamples, f"{label}-rule"),
        }
    report["verdict"] = kill_verdict(report)
    return report


def kill_verdict(report: Mapping[str, Any]) -> dict:
    span = report.get("span_days") or 0.0
    scored = int(report.get("scored_calls") or 0)
    if span < KILL_MIN_DAYS or scored < KILL_MIN_SCORED:
        return {"status": "COLLECTING", "span_days": span, "scored_calls": scored,
                "needs": {"days": KILL_MIN_DAYS, "scored_calls": KILL_MIN_SCORED}}
    beats = {}
    for label in ("persistence", "trending"):
        cell = (report.get("labels") or {}).get(label) or {}
        beats[label] = all(
            cell.get(key) is not None and cell[key] < 0
            for key in ("ub95_ai_minus_climatology", "ub95_ai_minus_stat_rule")
        )
    status = "KEEP" if all(beats.values()) else "KILL_REMOVE_PROMPT"
    return {"status": status, "beats_both_baselines": beats, "span_days": span, "scored_calls": scored}


class RegimeBudget:
    """15-minute spacing plus a UTC-day cap; independent of the compact prompt budget."""

    def __init__(self, min_interval_sec: float = MIN_INTERVAL_SEC, daily_cap: int = DAILY_CAP) -> None:
        self._lock = threading.Lock()
        self.min_interval_sec = float(min_interval_sec)
        self.daily_cap = int(daily_cap)
        self._last_ts = 0.0
        self._day = None
        self._count = 0

    def acquire(self, now: float) -> tuple:
        day = int(now // 86400)
        with self._lock:
            if day != self._day:
                self._day, self._count = day, 0
            if now - self._last_ts < self.min_interval_sec:
                return False, "MIN_INTERVAL"
            if self._count >= self.daily_cap:
                return False, "DAILY_CAP"
            self._last_ts = now
            self._count += 1
            return True, None
