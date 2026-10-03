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
WINDOW_2H_SEC = 2 * 3600
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
    # Cross-venue triggers stamp shared_ai_call_ts with the evaluator's
    # evaluated_ts; signal_age_sec/entry_delay start later, at signal creation.
    call_ts = _ts(row.get("shared_ai_call_ts"))
    held = _finite(row.get("outcome_duration_sec"))
    fill_ts = _ts(row.get("fill_ts")) or (close_ts - held if held is not None else None)
    signal_to_fill = fill_ts - call_ts if call_ts is not None and fill_ts is not None else None
    return {
        "lane": lane, "call": call, "close_ts": close_ts, "pnl_usd": pnl,
        "bp": pnl / notional * 1e4 if notional > 0 else None,
        "reason": reason, "overshoot_bp": overshoot, "side": sign, "entry": entry,
        "signal_to_fill_sec": signal_to_fill if signal_to_fill is not None and 0 <= signal_to_fill <= 3600 else None,
    }


def _median_signal_to_fill_sec(fills: list[dict[str, Any]]) -> float | None:
    values = sorted(r["signal_to_fill_sec"] for r in fills if r.get("signal_to_fill_sec") is not None)
    if not values:
        return None
    mid = len(values) // 2
    return round(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0, 3)


def _cluster_ci(rows: Sequence[tuple[float, float]],
                cluster_sec: int = CLUSTER_SEC) -> tuple[float | None, float | None]:
    """95% CI of the mean of values, resampling time clusters (``(ts, value)``)."""
    clusters: dict[int, list[float]] = {}
    for ts, value in rows:
        clusters.setdefault(int(ts // cluster_sec), []).append(value)
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


def _is_hard_stop(reason: str) -> bool:
    return reason == "HARD_STOP" or reason.startswith("PHYSICAL_HARD_STOP")


def _max_window_profit_share(fills: list[dict[str, Any]]) -> float | None:
    """Largest share of total net profit closed inside any 2-hour window."""
    total = sum(r["pnl_usd"] for r in fills)
    if total <= 0:
        return None
    best = max(
        sum(r["pnl_usd"] for r in fills if start["close_ts"] <= r["close_ts"] < start["close_ts"] + WINDOW_2H_SEC)
        for start in fills
    )
    return round(best / total, 4)


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
    hard = [1 if _is_hard_stop(r["reason"]) else 0 for r in fills]
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
        "worst_fill_bp": round(min(bps), 4) if bps else None,
        "max_2h_window_profit_share": _max_window_profit_share(fills),
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


def _trade_count_verdict(stats: Mapping[str, Any], paired: Mapping[str, Any], pre: Mapping[str, Any],
                         dsr: float | None, now_ts: float) -> dict[str, Any]:
    promote, kill = pre["promotion"], pre["kill"]
    lo, _ = stats.get("per_fill_ev_ci95_bp") or [None, None]
    fills = int(stats.get("fills") or 0)
    net = float(stats.get("net_pnl_usd") or 0.0)
    age_days = (now_ts - (_ts(pre.get("registered_utc")) or now_ts)) / 86400.0
    kills = []
    if fills >= kill["k1_after_fills"] and net <= kill["k1_net_usd_at_or_below"]:
        kills.append("K1_NET_LOSS_AFTER_40")
    if fills >= kill["k2_after_fills"] and net <= kill["k2_net_usd_at_or_below"]:
        kills.append("K2_NOT_POSITIVE_AFTER_80")
    worst = stats.get("worst_fill_bp")
    if worst is not None and worst < kill["k3_worst_trade_bp_below"]:
        kills.append("K3_STOP_FAILURE")
    if (stats.get("max_drawdown_usd") or 0.0) > kill["k4_max_drawdown_usd"]:
        kills.append("K4_DRAWDOWN")
    share = stats.get("max_2h_window_profit_share")
    checks = {
        "min_fills": fills >= promote["min_fills"],
        "per_fill_ev_lower_ci95_gt_0": lo is not None and lo > promote["per_fill_ev_lower_ci95_gt_bp"],
        "both_halves_positive": (stats.get("first_half_ev_bp") or 0) > 0 and (stats.get("second_half_ev_bp") or 0) > 0,
        "no_2h_window_dominates": share is not None and share <= promote["max_2h_window_profit_share"],
    }
    return _finish(kills, checks, kill, age_days, deflated_sharpe=dsr)


def _xvl_extra_stats(fills: list[dict[str, Any]], pre: Mapping[str, Any]) -> dict[str, Any]:
    """1 h-cluster CI, UTC-day, Asia-session and single-hour concentration facts."""
    fills = sorted(fills, key=lambda r: r["close_ts"])
    bps = [(r["close_ts"], r["bp"]) for r in fills if r["bp"] is not None]
    lo, hi = _cluster_ci(bps, cluster_sec=3600)
    days: dict[str, float] = {}
    asia: dict[str, int] = {}
    hours: dict[int, float] = {}
    start_h, end_h = pre["promotion"].get("asia_session_utc_hours", (0, 8))
    for r in fills:
        moment = datetime.fromtimestamp(r["close_ts"], timezone.utc)
        day = moment.date().isoformat()
        days[day] = days.get(day, 0.0) + r["pnl_usd"]
        if start_h <= moment.hour < end_h:
            asia[day] = asia.get(day, 0) + 1
        hour = int(r["close_ts"] // 3600)
        hours[hour] = hours.get(hour, 0.0) + r["pnl_usd"]
    total = sum(r["pnl_usd"] for r in fills)
    first5 = [days[d] for d in sorted(days)[:5]]
    return {
        "mean_bp": round(sum(v for _, v in bps) / len(bps), 4) if bps else None,
        "per_fill_ev_ci95_bp_1h": [lo, hi],
        "utc_days": len(days),
        "asia_sessions_qualified": sum(
            1 for n in asia.values() if n >= pre["promotion"]["min_asia_session_fills"]
        ),
        "first_5_days_observed": len(first5),
        "positive_days_of_first_5": sum(1 for v in first5 if v > 0),
        "max_single_hour_profit_share": round(max(hours.values()) / total, 4) if fills and total > 0 else None,
        "median_signal_to_fill_sec": _median_signal_to_fill_sec(fills),
    }


def _xvl_verdict(stats: Mapping[str, Any], paired: Mapping[str, Any], pre: Mapping[str, Any],
                 dsr: float | None, now_ts: float) -> dict[str, Any]:
    promote, kill = pre["promotion"], pre["kill"]
    lo, hi = stats.get("per_fill_ev_ci95_bp_1h") or [None, None]
    fills = int(stats.get("fills") or 0)
    mean = stats.get("mean_bp")
    age_days = (now_ts - (_ts(pre.get("registered_utc")) or now_ts)) / 86400.0
    kills = []
    if fills >= kill["k1_after_fills"] and mean is not None and mean <= kill["k1_mean_bp_at_or_below"]:
        kills.append("K1_MEAN_NOT_POSITIVE_AFTER_150")
    if fills >= kill["k2_after_fills"] and hi is not None and hi < kill["k2_upper_ci95_lt_bp"]:
        kills.append("K2_UPPER_CI_BELOW_HALF_BP_AFTER_400")
    worst = stats.get("worst_fill_bp")
    stale_share = stats.get("stale_feed_fill_share")
    if (worst is not None and worst < kill["k3_worst_trade_bp_below"]) or (
        stale_share is not None and stale_share > kill["k3_max_stale_feed_fill_share"]
    ):
        kills.append("K3_STOP_OR_STALE_FEED_FAILURE")
    if (stats.get("max_drawdown_usd") or 0.0) > kill["k4_max_drawdown_usd"]:
        kills.append("K4_DRAWDOWN")
    share = stats.get("max_single_hour_profit_share")
    parity = stats.get("replay_parity_gap_bp")
    latency = stats.get("median_signal_to_fill_sec")
    overshoot = stats.get("max_lock_or_stop_overshoot_bp")
    checks = {
        "min_fills": fills >= promote["min_fills"],
        "min_utc_days": (stats.get("utc_days") or 0) >= promote["min_utc_days"],
        "asia_sessions": (stats.get("asia_sessions_qualified") or 0) >= promote["min_asia_sessions"],
        "per_fill_ev_lower_ci95_1h_gt_0": lo is not None and lo > promote["per_fill_ev_lower_ci95_gt_bp"],
        "positive_days_of_first_5": (stats.get("first_5_days_observed") or 0) >= 5
        and (stats.get("positive_days_of_first_5") or 0) >= promote["min_positive_days_of_first_5"],
        "both_halves_positive": (stats.get("first_half_ev_bp") or 0) > 0 and (stats.get("second_half_ev_bp") or 0) > 0,
        "no_hour_dominates": share is not None and share <= promote["max_single_hour_profit_share"],
        "replay_parity": parity is not None and abs(parity) <= promote["max_replay_parity_gap_bp"],
        "signal_to_fill_latency": latency is not None and latency <= promote["max_median_signal_to_fill_sec"],
        "stops_within_limit": overshoot is None or overshoot <= promote["max_stop_overshoot_bp"],
    }
    return _finish(kills, checks, kill, age_days, deflated_sharpe=dsr)


def _finish(kills: list[str], checks: Mapping[str, bool], kill: Mapping[str, Any], age_days: float,
            *, deflated_sharpe: float | None) -> dict[str, Any]:
    promoted = all(checks.values())
    if not promoted and age_days > kill["k5_max_days_without_promotion"]:
        kills.append("K5_TIME_BOX_INCONCLUSIVE")
    status = "KILL" if kills else ("PROMOTION_ELIGIBLE_FOR_OWNER_REVIEW" if promoted else "COLLECTING")
    return {
        "status": status, "kill_reasons": kills, "promotion_checks": dict(checks),
        "deflated_sharpe": deflated_sharpe, "days_since_registration": round(age_days, 3),
        "action_on_kill": "Toggle OFF and retire per TILE_LIFECYCLE.md (owner decision; never automatic)",
    }


def _committed_fade_extra_stats(fills: list[dict[str, Any]], pre: Mapping[str, Any]) -> dict[str, Any]:
    """2 h-cluster CI, distinct hours, hit rate and regime-day facts for the committed fade.

    The regime of a UTC day uses the tile's own entry prices that day
    (``ENTRY_PRICE_PROXY``): a first-to-last move of at least 1.5% is a trend
    day, otherwise range. Days with one fill are range days.
    """
    fills = sorted(fills, key=lambda r: r["close_ts"])
    bps = [(r["close_ts"], r["bp"]) for r in fills if r["bp"] is not None]
    lo, hi = _cluster_ci(bps, cluster_sec=WINDOW_2H_SEC)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in fills:
        by_day.setdefault(datetime.fromtimestamp(r["close_ts"], timezone.utc).date().isoformat(), []).append(r)
    regimes: dict[str, str] = {}
    trend_day_means: dict[str, float] = {}
    for day, rows in by_day.items():
        prices = [r["entry"] for r in rows if r.get("entry")]
        move = (prices[-1] / prices[0] - 1.0) if len(prices) >= 2 else 0.0
        regimes[day] = "UP" if move >= 0.015 else ("DOWN" if move <= -0.015 else "RANGE")
        day_bps = [r["bp"] for r in rows if r["bp"] is not None]
        if regimes[day] != "RANGE" and day_bps:
            trend_day_means[day] = round(sum(day_bps) / len(day_bps), 4)
    return {
        "per_fill_ev_ci95_bp_2h": [lo, hi],
        "distinct_hours": len({int(r["close_ts"] // 3600) for r in fills}),
        "hit_rate": round(sum(1 for _, v in bps if v > 0) / len(bps), 4) if bps else None,
        "regime_days": regimes,
        "regime_kinds_observed": sorted(set(regimes.values())),
        "regime_method": "ENTRY_PRICE_PROXY",
        "worst_trend_day_mean_bp": min(trend_day_means.values()) if trend_day_means else None,
    }


def _committed_fade_verdict(stats: Mapping[str, Any], paired: Mapping[str, Any], pre: Mapping[str, Any],
                            dsr: float | None, now_ts: float) -> dict[str, Any]:
    promote, kill = pre["promotion"], pre["kill"]
    lo, _ = stats.get("per_fill_ev_ci95_bp_2h") or [None, None]
    fills = int(stats.get("fills") or 0)
    age_days = (now_ts - (_ts(pre.get("registered_utc")) or now_ts)) / 86400.0
    kills = []
    hit = stats.get("hit_rate")
    if fills >= kill["k1_after_fills"] and hit is not None and hit < kill["k1_hit_rate_below"]:
        kills.append("K1_HIT_RATE_BELOW_52_AFTER_150")
    worst_day = stats.get("worst_trend_day_mean_bp")
    if worst_day is not None and worst_day < kill["k2_trend_day_mean_bp_below"]:
        kills.append("K2_TREND_DAY_MEAN_BELOW_MINUS_15")
    worst = stats.get("worst_fill_bp")
    if worst is not None and worst < kill["k3_worst_trade_bp_below"]:
        kills.append("K3_STOP_FAILURE")
    if (stats.get("max_drawdown_usd") or 0.0) > kill["k4_max_drawdown_usd"]:
        kills.append("K4_DRAWDOWN")
    diff = paired.get("mean_difference_bp")
    checks = {
        "min_fills": fills >= promote["min_fills"],
        "min_distinct_hours": (stats.get("distinct_hours") or 0) >= promote["min_distinct_hours"],
        "min_regime_days": len(stats.get("regime_kinds_observed") or ()) >= promote["min_regime_days"],
        "per_fill_ev_lower_ci95_2h_gt_0": lo is not None and lo > promote["per_fill_ev_lower_ci95_gt_bp"],
        "beats_control": diff is not None and diff >= promote["beats_control_by_bp"],
        "both_halves_positive": (stats.get("first_half_ev_bp") or 0) > 0 and (stats.get("second_half_ev_bp") or 0) > 0,
    }
    return _finish(kills, checks, kill, age_days, deflated_sharpe=dsr)


XVP_SESSIONS_UTC = {"ASIA": (0, 8), "EU": (8, 13), "US": (13, 21)}


def _xvp_extra_stats(fills: list[dict[str, Any]], pre: Mapping[str, Any]) -> dict[str, Any]:
    """1 h-cluster CI, UTC days, per-session days, day concentration and per-side means."""
    fills = sorted(fills, key=lambda r: r["close_ts"])
    bps = [(r["close_ts"], r["bp"]) for r in fills if r["bp"] is not None]
    lo, hi = _cluster_ci(bps, cluster_sec=3600)
    days: dict[str, float] = {}
    sessions: dict[str, set] = {name: set() for name in XVP_SESSIONS_UTC}
    for r in fills:
        moment = datetime.fromtimestamp(r["close_ts"], timezone.utc)
        day = moment.date().isoformat()
        days[day] = days.get(day, 0.0) + r["pnl_usd"]
        for name, (start_h, end_h) in XVP_SESSIONS_UTC.items():
            if start_h <= moment.hour < end_h:
                sessions[name].add(day)
    total = sum(r["pnl_usd"] for r in fills)
    side_bp = {
        label: [r["bp"] for r in fills if r.get("side") == sign and r["bp"] is not None]
        for label, sign in (("LONG", 1), ("SHORT", -1))
    }
    return {
        "mean_bp": round(sum(v for _, v in bps) / len(bps), 4) if bps else None,
        "per_fill_ev_ci95_bp_1h": [lo, hi],
        "utc_days": len(days),
        "session_days": {name: len(v) for name, v in sessions.items()},
        "session_hours_utc": {k: list(v) for k, v in XVP_SESSIONS_UTC.items()},
        "max_single_day_profit_share": round(max(days.values()) / total, 4) if fills and total > 0 else None,
        "side_mean_bp": {k: (round(sum(v) / len(v), 4) if v else None) for k, v in side_bp.items()},
        "median_signal_to_fill_sec": _median_signal_to_fill_sec(fills),
    }


def _xvp_verdict(stats: Mapping[str, Any], paired: Mapping[str, Any], pre: Mapping[str, Any],
                 dsr: float | None, now_ts: float) -> dict[str, Any]:
    """Shadow 5 s-delay, replay parity and signal-to-fill facts come from the analyzer's
    cross-venue report when present; until then those checks stay False (no promotion)."""
    promote, kill = pre["promotion"], pre["kill"]
    lo, _ = stats.get("per_fill_ev_ci95_bp_1h") or [None, None]
    fills = int(stats.get("fills") or 0)
    mean = stats.get("mean_bp")
    age_days = (now_ts - (_ts(pre.get("registered_utc")) or now_ts)) / 86400.0
    delay5 = stats.get("shadow_5s_delay_mean_bp")
    kills = []
    if fills >= kill["k1_after_fills"] and mean is not None and mean <= kill["k1_mean_bp_at_or_below"]:
        kills.append("K1_MEAN_NOT_POSITIVE_AFTER_300")
    if fills >= kill["k2_after_fills"] and delay5 is not None and delay5 < kill["k2_shadow_5s_delay_mean_below_bp"]:
        kills.append("K2_5S_DELAY_SHADOW_NEGATIVE_AFTER_300")
    worst = stats.get("worst_fill_bp")
    stale_share = stats.get("stale_feed_fill_share")
    if (worst is not None and worst < kill["k3_worst_trade_bp_below"]) or (
        stale_share is not None and stale_share > kill["k3_max_stale_feed_fill_share"]
    ):
        kills.append("K3_STOP_OR_STALE_FEED_FAILURE")
    if (stats.get("max_drawdown_usd") or 0.0) > kill["k4_max_drawdown_usd"]:
        kills.append("K4_DRAWDOWN")
    sessions = stats.get("session_days") or {}
    sides = stats.get("side_mean_bp") or {}
    share = stats.get("max_single_day_profit_share")
    parity = stats.get("replay_parity_gap_bp")
    latency = stats.get("median_signal_to_fill_sec")
    checks = {
        "min_fills": fills >= promote["min_fills"],
        "min_utc_days": (stats.get("utc_days") or 0) >= promote["min_utc_days"],
        "sessions": all((sessions.get(s) or 0) >= promote["min_sessions_each"] for s in promote["sessions"]),
        "per_fill_ev_lower_ci95_1h_gt_0": lo is not None and lo > promote["per_fill_ev_lower_ci95_gt_bp"],
        "shadow_5s_delay_positive": delay5 is not None and delay5 > promote["shadow_5s_delay_mean_gt_bp"],
        "no_day_dominates": share is not None and share <= promote["max_single_day_profit_share"],
        "both_sides_non_negative": all(
            sides.get(s) is not None and sides[s] >= promote["both_sides_mean_ge_bp"] for s in ("LONG", "SHORT")
        ),
        "replay_parity": parity is not None and abs(parity) <= promote["max_replay_parity_gap_bp"],
        "signal_to_fill_latency": latency is not None and latency <= promote["max_median_signal_to_fill_sec"],
    }
    return _finish(kills, checks, kill, age_days, deflated_sharpe=dsr)


VERDICT_RULES = {
    "tile_pre_registration_trade_count_v1": _trade_count_verdict,
    "tile_pre_registration_xvl_v1": _xvl_verdict,
    "tile_pre_registration_committed_fade_v1": _committed_fade_verdict,
    "tile_pre_registration_xvp_v1": _xvp_verdict,
}
EXTRA_STATS = {
    "tile_pre_registration_xvl_v1": _xvl_extra_stats,
    "tile_pre_registration_committed_fade_v1": _committed_fade_extra_stats,
    "tile_pre_registration_xvp_v1": _xvp_extra_stats,
}


UNJOINED_INPUT_REVISION = "UNJOINED"
PRE_REVISION_INPUTS = "shared_direction_inputs_r1"


def call_input_revisions(challenger_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """shared_ai_call_id -> prompt input revision; calls logged before the field existed are r1."""
    out: dict[str, str] = {}
    for row in challenger_rows:
        if row.get("row_kind") != "CALL" or not row.get("shared_ai_call_id"):
            continue
        out[str(row["shared_ai_call_id"])] = str(row.get("prompt_input_revision") or PRE_REVISION_INPUTS)
    return out


def _input_revision_cohorts(by_lane, lanes, revisions: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lane in lanes:
        cells: dict[str, list[float]] = {}
        for r in by_lane[lane]:
            if r["bp"] is None:
                continue
            cells.setdefault(revisions.get(r["call"], UNJOINED_INPUT_REVISION), []).append(r["bp"])
        out[lane] = {rev: {"n": len(v), "mean_bp": round(sum(v) / len(v), 4)} for rev, v in sorted(cells.items())}
    return out


def baseline_lane(registry: Mapping[str, Mapping[str, Any]], lanes: Sequence[str]) -> str | None:
    """First tile in display order whose registry spec declares a ``baseline_role``."""
    return next((lane for lane in lanes if (registry.get(lane) or {}).get("baseline_role")), None)


def build_report(*, trades: Iterable[Mapping[str, Any]], registry: Mapping[str, Mapping[str, Any]],
                 tile_order: Sequence[str], now_ts: float | None = None,
                 call_revisions: Mapping[str, str] | None = None) -> dict[str, Any]:
    now_ts = float(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    lanes = [str(lane).upper() for lane in tile_order]
    by_lane: dict[str, list[dict[str, Any]]] = {lane: [] for lane in lanes}
    for row in trades:
        fill = _fill_row(row)
        if fill and fill["lane"] in by_lane:
            by_lane[fill["lane"]].append(fill)
    stats = {lane: _tile_stats(by_lane[lane]) for lane in lanes}
    baseline = baseline_lane(registry, lanes)
    # The baseline is a yardstick, not a tested hypothesis.
    hypothesis_lanes = [lane for lane in lanes if lane != baseline]
    trials = len(hypothesis_lanes)
    srs = []
    for lane in hypothesis_lanes:
        values = [r["bp"] for r in by_lane[lane] if r["bp"] is not None]
        if len(values) >= 3:
            mean, sd, _, _ = _moments(values)
            if sd > 0:
                srs.append(mean / sd)
    sr_variance = None
    if len(srs) >= 2:
        mu = sum(srs) / len(srs)
        sr_variance = sum((s - mu) ** 2 for s in srs) / (len(srs) - 1)
    # Only tiles keyed to the shared AI call (reading it or making their own
    # call on it) can share a signal; cross-venue clock tiles are never paired.
    paired_lanes = [
        lane for lane in lanes
        if (registry.get(lane) or {}).get("uses_shared_ai_direction", True) is not False
        or (registry.get(lane) or {}).get("own_ai_call")
    ]
    pairs = [_paired(by_lane, a, b) for i, a in enumerate(paired_lanes) for b in paired_lanes[i + 1:]]
    vs_baseline = (
        [_paired(by_lane, baseline, lane) for lane in paired_lanes if lane != baseline]
        if baseline in paired_lanes else []
    )
    common = set.intersection(*[
        {r["call"] for r in by_lane[lane] if r["call"] and r["bp"] is not None} for lane in paired_lanes
    ]) if paired_lanes else set()
    all_paired = {
        "paired_tiles": paired_lanes,
        "signals_filled_by_every_tile": len(common),
        "per_tile_win": {
            lane: _win_fields([r["pnl_usd"] for r in by_lane[lane] if r["call"] in common])
            for lane in paired_lanes
        },
        "per_tile_ev_bp": {
            lane: round(sum(r["bp"] for r in by_lane[lane] if r["call"] in common) / len(common), 4)
            if common else None
            for lane in paired_lanes
        },
    }
    pre_registered = {}
    for lane in lanes:
        pre = (registry.get(lane) or {}).get("pre_registration")
        if not pre:
            continue
        values = [r["bp"] for r in sorted(by_lane[lane], key=lambda r: r["close_ts"]) if r["bp"] is not None]
        dsr = deflated_sharpe(values, trials=trials, sr_variance=sr_variance)
        extra = EXTRA_STATS.get(pre.get("schema"))
        if extra and by_lane[lane]:
            stats[lane].update(extra(by_lane[lane], pre))
        vs_control = next((p for p in pairs if p["control"] == pre.get("control_lane") and p["challenger"] == lane), {})
        rule = VERDICT_RULES.get(pre.get("schema"))
        pre_registered[lane] = {
            "hypothesis_id": pre["hypothesis_id"],
            "control_lane": pre.get("control_lane"),
            "control_meaning": pre.get("control_meaning"),
            "honest_label": pre.get("honest_label"),
            "rules": pre,
            "verdict": (
                rule(stats[lane], vs_control, pre, dsr, now_ts) if rule
                else {"status": "UNKNOWN_PRE_REGISTRATION_SCHEMA", "kill_reasons": [], "promotion_checks": {}}
            ),
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
        "baseline_lane": baseline,
        "vs_baseline": vs_baseline,
        "all_tiles_paired": all_paired,
        "pre_registered": pre_registered,
        "input_revision_cohorts": {
            "meaning": ("AI-fed tiles split by the shared call's prompt input revision; a revision "
                        "changes what the AI saw, so evidence must not pool across it"),
            "lanes": _input_revision_cohorts(by_lane, paired_lanes, call_revisions or {}),
        },
        "qualification_eligible": False,
    }
