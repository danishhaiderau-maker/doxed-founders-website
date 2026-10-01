"""Paired tile comparison on identical signals plus pre-registered verdicts.

Registry tiles that share one AI call are paired by ``shared_ai_call_id``: a
signal is paired for two tiles only when both closed a paper fill from it, so
every paired difference compares exits on the same signal. Per-fill EV is
expressed in bp of notional (net PnL / (margin x leverage)), with 95%
confidence intervals from a 6-hour cluster bootstrap. A tile whose registry
spec carries ``pre_registration`` is scored against those frozen promotion and
kill rules; the verdict is advisory evidence for the owner and never arms or
promotes anything by itself.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "tile_paired_comparison_v1"
REPORT_FILE = "tile_paired_comparison_report.json"
CLUSTER_SEC = 6 * 3600
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261001
HARD_STOP_REASONS = frozenset({"PHYSICAL_HARD_STOP_30PCT", "HARD_STOP"})
UNFILLED_REASONS = frozenset({"NO_FILL", "UNFILLED", "CANCELLED", "EXPIRED"})
STOP_LIKE_REASONS = frozenset({
    "INITIAL_ATR_STOP", "PROFIT_PROTECTION_STOP", "PROFIT_LOCK_LADDER", "BREAKEVEN_LOCK",
})
_NORMAL = NormalDist()


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ts(value) -> float | None:
    number = _finite(value)
    if number is not None:
        return number / 1000.0 if number > 1e12 else number
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _fill_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    lane = str(row.get("research_lane") or "").strip().upper()
    call = str(row.get("shared_ai_call_id") or "").strip()
    pnl = _finite(row.get("net_pnl_usd"))
    close_ts = next((t for t in (_ts(row.get(k)) for k in ("close_ts", "exit_ts", "ts")) if t is not None), None)
    if not lane or pnl is None or close_ts is None:
        return None
    if str(row.get("exit_reason") or "").upper() in UNFILLED_REASONS or row.get("filled") is False \
            or str(row.get("status") or "").upper() in UNFILLED_REASONS:
        return None
    margin = _finite(row.get("margin_usdt")) or _finite(row.get("margin_usd")) or 0.25
    leverage = _finite(row.get("leverage")) or 100.0
    notional = margin * leverage
    reason = str(row.get("exit_reason") or "").upper()
    overshoot = None
    stop = _finite(row.get("policy_stop_price"))
    exit_price = _finite(row.get("exit_price")) or _finite(row.get("exit"))
    entry = _finite(row.get("entry")) or _finite(row.get("fill_price"))
    sign = {"LONG": 1, "SHORT": -1}.get(str(row.get("dir") or row.get("direction") or "").upper())
    if reason in STOP_LIKE_REASONS and stop and exit_price and entry and sign:
        overshoot = sign * (stop - exit_price) / entry * 1e4
    return {
        "lane": lane, "call": call, "close_ts": close_ts, "pnl_usd": pnl,
        "bp": pnl / notional * 1e4 if notional > 0 else None,
        "reason": reason, "overshoot_bp": overshoot,
    }


def _cluster_ci(rows: Sequence[tuple[float, float]]) -> tuple[float | None, float | None]:
    """95% CI of the mean of values, resampling 6-hour clusters (``(ts, value)``)."""
    clusters: dict[int, list[float]] = {}
    for ts, value in rows:
        clusters.setdefault(int(ts // CLUSTER_SEC), []).append(value)
    groups = list(clusters.values())
    if len(groups) < 2:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    means = []
    for _ in range(BOOTSTRAP_DRAWS):
        sample = [v for _ in groups for v in rng.choice(groups)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return round(means[int(0.025 * len(means))], 4), round(means[int(0.975 * len(means)) - 1], 4)


def _moments(values: Sequence[float]) -> tuple[float, float, float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    if sd == 0:
        return mean, 0.0, 0.0, 3.0
    skew = sum(((v - mean) / sd) ** 3 for v in values) / n
    kurt = sum(((v - mean) / sd) ** 4 for v in values) / n
    return mean, sd, skew, kurt


def deflated_sharpe(values: Sequence[float], *, trials: int, sr_variance: float | None) -> float | None:
    """Bailey & Lopez de Prado deflated Sharpe ratio of per-fill returns."""
    n = len(values)
    if n < 3 or trials < 1:
        return None
    mean, sd, skew, kurt = _moments(values)
    if sd == 0:
        return None
    sr = mean / sd
    variance = sr_variance if sr_variance and sr_variance > 0 else 1.0 / (n - 1)
    gamma = 0.5772156649
    if trials > 1:
        sr0 = math.sqrt(variance) * (
            (1 - gamma) * _NORMAL.inv_cdf(1 - 1.0 / trials)
            + gamma * _NORMAL.inv_cdf(1 - 1.0 / (trials * math.e))
        )
    else:
        sr0 = 0.0
    denom = 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr
    if denom <= 0:
        return None
    return round(_NORMAL.cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)), 4)


def _win_fields(pnls: list[float]) -> dict[str, Any]:
    """Win = net PnL > 0 after costs, over closed filled trades."""
    wins = sum(1 for v in pnls if v > 0)
    losses = sum(1 for v in pnls if v < 0)
    return {"wins": wins, "losses": losses,
            "win_rate_pct": round(100.0 * wins / len(pnls), 1) if pnls else None}


def _tile_stats(fills: list[dict[str, Any]]) -> dict[str, Any]:
    fills = sorted(fills, key=lambda r: r["close_ts"])
    bps = [r["bp"] for r in fills if r["bp"] is not None]
    if not fills:
        return {"fills": 0}
    lo, hi = _cluster_ci([(r["close_ts"], r["bp"]) for r in fills if r["bp"] is not None])
    half = len(bps) // 2
    cumulative = peak = drawdown = 0.0
    for r in fills:
        cumulative += r["pnl_usd"]
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    hard = [1 if r["reason"] in HARD_STOP_REASONS else 0 for r in fills]
    window = max((sum(hard[i:i + 50]) for i in range(max(1, len(hard) - 49))), default=0)
    overshoots = [r["overshoot_bp"] for r in fills if r["overshoot_bp"] is not None]
    return {
        "fills": len(fills),
        "first_close_ts": fills[0]["close_ts"],
        "last_close_ts": fills[-1]["close_ts"],
        "days_observed": round((fills[-1]["close_ts"] - fills[0]["close_ts"]) / 86400.0, 3),
        "net_pnl_usd": round(sum(r["pnl_usd"] for r in fills), 6),
        "per_fill_ev_bp": round(sum(bps) / len(bps), 4) if bps else None,
        "per_fill_ev_ci95_bp": [lo, hi],
        "first_half_ev_bp": round(sum(bps[:half]) / half, 4) if half else None,
        "second_half_ev_bp": round(sum(bps[half:]) / (len(bps) - half), 4) if len(bps) - half else None,
        **_win_fields([r["pnl_usd"] for r in fills]),
        "max_drawdown_usd": round(drawdown, 6),
        "max_hard_stops_in_rolling_50": int(window),
        "max_lock_or_stop_overshoot_bp": round(max(overshoots), 4) if overshoots else None,
        "overshoot_evidence_rows": len(overshoots),
        "by_exit_reason": {
            reason: sum(1 for r in fills if r["reason"] == reason)
            for reason in sorted({r["reason"] for r in fills})
        },
    }


def _paired(by_lane: Mapping[str, list[dict[str, Any]]], a: str, b: str) -> dict[str, Any]:
    left = {r["call"]: r for r in by_lane.get(a, []) if r["call"] and r["bp"] is not None}
    right = {r["call"]: r for r in by_lane.get(b, []) if r["call"] and r["bp"] is not None}
    calls = sorted(set(left) & set(right), key=lambda c: right[c]["close_ts"])
    diffs = [(right[c]["close_ts"], right[c]["bp"] - left[c]["bp"]) for c in calls]
    lo, hi = _cluster_ci(diffs)
    return {
        "control": a, "challenger": b,
        "paired_signals": len(diffs),
        "unpaired_control_fills": len(set(left) - set(right)),
        "unpaired_challenger_fills": len(set(right) - set(left)),
        "mean_difference_bp": round(sum(d for _, d in diffs) / len(diffs), 4) if diffs else None,
        "difference_ci95_bp": [lo, hi],
        "challenger_better_signals": sum(1 for _, d in diffs if d > 0),
        **{f"control_{k}": v for k, v in _win_fields([left[c]["pnl_usd"] for c in calls]).items()},
        **{f"challenger_{k}": v for k, v in _win_fields([right[c]["pnl_usd"] for c in calls]).items()},
        "identical_outcome_signals": sum(1 for _, d in diffs if abs(d) < 1e-9),
        "reading": "challenger minus control, bp of notional per paired signal (same shared AI call, both filled)",
    }


def _verdict(stats: Mapping[str, Any], paired: Mapping[str, Any], pre: Mapping[str, Any],
             dsr: float | None, now_ts: float) -> dict[str, Any]:
    promote, kill = pre["promotion"], pre["kill"]
    lo, hi = stats.get("per_fill_ev_ci95_bp") or [None, None]
    plo, phi = paired.get("difference_ci95_bp") or [None, None]
    fills = int(stats.get("fills") or 0)
    registered = _ts(pre.get("registered_utc")) or now_ts
    age_days = (now_ts - registered) / 86400.0
    kills = []
    if fills >= kill["k1_min_fills"] and hi is not None and hi < kill["k1_per_fill_ev_upper_ci95_lt_bp"]:
        kills.append("K1_PER_FILL_EV_UPPER_CI_BELOW_ZERO")
    if (paired.get("paired_signals") or 0) >= kill["k2_min_paired_signals"] and phi is not None \
            and phi < kill["k2_paired_vs_control_upper_ci95_lt_bp"]:
        kills.append("K2_LOSES_TO_CONTROL_ON_PAIRED_SIGNALS")
    overshoot = stats.get("max_lock_or_stop_overshoot_bp")
    if (stats.get("max_hard_stops_in_rolling_50") or 0) >= kill["k3_hard_stops_per_rolling_50_kill_at"] or (
        overshoot is not None and overshoot > kill["k3_max_lock_or_stop_overshoot_bp"]
    ):
        kills.append("K3_STOP_FAILURE")
    if (stats.get("max_drawdown_usd") or 0.0) > kill["k4_max_drawdown_usd"]:
        kills.append("K4_DRAWDOWN")
    checks = {
        "min_fills": fills >= promote["min_fills"],
        "min_days": (stats.get("days_observed") or 0.0) >= promote["min_days"],
        "per_fill_ev_lower_ci95_gt_0": lo is not None and lo > promote["per_fill_ev_lower_ci95_gt_bp"],
        "both_halves_positive": (stats.get("first_half_ev_bp") or 0) > 0 and (stats.get("second_half_ev_bp") or 0) > 0,
        "deflated_sharpe": dsr is not None and dsr >= promote["deflated_sharpe_min"],
        "beats_control_paired": plo is not None and plo > promote["paired_vs_control_lower_ci95_gt_bp"],
    }
    promoted = all(checks.values())
    if not promoted and age_days > kill["k5_max_days_without_promotion"]:
        kills.append("K5_TIME_BOX_INCONCLUSIVE")
    status = "KILL" if kills else ("PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW" if promoted else "COLLECTING")
    return {
        "status": status, "kill_reasons": kills, "promotion_checks": checks,
        "deflated_sharpe": dsr, "days_since_registration": round(age_days, 3),
        "action_on_kill": "Toggle OFF and retire per TILE_LIFECYCLE.md (owner decision; never automatic)",
    }


def build_report(*, trades: Iterable[Mapping[str, Any]], registry: Mapping[str, Mapping[str, Any]],
                 tile_order: Sequence[str], now_ts: float | None = None) -> dict[str, Any]:
    now_ts = float(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    lanes = [str(lane).upper() for lane in tile_order]
    by_lane: dict[str, list[dict[str, Any]]] = {lane: [] for lane in lanes}
    for row in trades:
        fill = _fill_row(row)
        if fill and fill["lane"] in by_lane:
            by_lane[fill["lane"]].append(fill)
    stats = {lane: _tile_stats(by_lane[lane]) for lane in lanes}
    trials = len(lanes)
    srs = []
    for lane in lanes:
        values = [r["bp"] for r in by_lane[lane] if r["bp"] is not None]
        if len(values) >= 3:
            mean, sd, _, _ = _moments(values)
            if sd > 0:
                srs.append(mean / sd)
    sr_variance = None
    if len(srs) >= 2:
        mu = sum(srs) / len(srs)
        sr_variance = sum((s - mu) ** 2 for s in srs) / (len(srs) - 1)
    pairs = [_paired(by_lane, a, b) for i, a in enumerate(lanes) for b in lanes[i + 1:]]
    common = set.intersection(*[
        {r["call"] for r in by_lane[lane] if r["call"] and r["bp"] is not None} for lane in lanes
    ]) if lanes else set()
    all_paired = {
        "signals_filled_by_every_tile": len(common),
        "per_tile_win": {
            lane: _win_fields([r["pnl_usd"] for r in by_lane[lane] if r["call"] in common])
            for lane in lanes
        },
        "per_tile_ev_bp": {
            lane: round(sum(r["bp"] for r in by_lane[lane] if r["call"] in common) / len(common), 4)
            if common else None
            for lane in lanes
        },
    }
    pre_registered = {}
    for lane in lanes:
        pre = (registry.get(lane) or {}).get("pre_registration")
        if not pre:
            continue
        values = [r["bp"] for r in sorted(by_lane[lane], key=lambda r: r["close_ts"]) if r["bp"] is not None]
        dsr = deflated_sharpe(values, trials=trials, sr_variance=sr_variance)
        vs_control = next((p for p in pairs if p["control"] == pre["control_lane"] and p["challenger"] == lane), {})
        pre_registered[lane] = {
            "hypothesis_id": pre["hypothesis_id"],
            "control_lane": pre["control_lane"],
            "rules": pre,
            "verdict": _verdict(stats[lane], vs_control, pre, dsr, now_ts),
        }
    return {
        "schema": SCHEMA,
        "generated_at": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(),
        "tile_order": lanes,
        "labels": {lane: (registry.get(lane) or {}).get("label") for lane in lanes},
        "evidence_world": "PAPER_LEDGER_LOCKS_FILL_AT_CROSSING_QUOTE",
        "ev_unit": "bp of notional per closed fill (net PnL / margin x leverage)",
        "ci_method": "6H_CLUSTER_BOOTSTRAP_95",
        "deflated_sharpe_trials": trials,
        "tiles": stats,
        "paired": pairs,
        "all_tiles_paired": all_paired,
        "pre_registered": pre_registered,
        "qualification_eligible": False,
    }
