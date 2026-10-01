"""Best single fixed tile versus a regime-conditional dynamic tile selector.

Both selectors are evaluated walk-forward on realized tile closes: at every
shared AI signal the selector may only learn from closes whose close time is
before that signal, and it chooses a tile from the market state recorded at
entry (``entry_context``: regime, ADX, ATR%), bucketed with the Chronological
OOS regime key. The chosen tile's own after-cost close for that signal is the
out-of-sample outcome.

* fixed: the tile with the best mean after-cost EV among tiles with at least
  ``MIN_TRAIN_CLOSES`` prior closes;
* dynamic: within the signal's regime, the best tile among tiles with at least
  ``MIN_REGIME_TRAIN_CLOSES`` prior closes in that regime; otherwise it falls
  back to the fixed pick (and is labelled so).

The verdict stays NOT_ENOUGH_DATA until each arm has ``MIN_OOS_CLOSES`` OOS
outcomes and the dynamic arm has made ``MIN_OOS_CLOSES`` regime-specific
picks. Deploy/operator forced exits are excluded exactly as in the EV ranking.
Nothing here can place, change or cancel an order.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from research.policy_candidate_oos import _regime_key
from research.tile_evidence_points import (
    CI_Z,
    FORCED_EXIT_REASONS,
    _epoch_seconds,
    _finite,
    _iso,
    _mapping,
    _summary,
    _upper,
    classify_trade_rows,
    exact_net_pnl,
)

REPORT_FILE = "fixed_vs_dynamic_selector_report.json"
REPORT_SCHEMA = "fixed_vs_dynamic_selector_v1"
MIN_TRAIN_CLOSES = 30
MIN_REGIME_TRAIN_CLOSES = 30
MIN_OOS_CLOSES = 30
MIN_REGIME_OOS_CLOSES = 30
MIN_HALF_CLOSES = 15
NOT_ENOUGH = "NOT_ENOUGH_DATA"


def trade_regime_key(row: Mapping[str, Any]) -> str | None:
    """Chronological-OOS regime bucket from the state recorded at entry (no session split)."""
    context = _mapping(row.get("entry_context"))
    regime = context.get("regime") or row.get("context_regime") or row.get("regime")
    adx = context.get("adx") if context.get("adx") is not None else row.get("adx_at_entry")
    atr = context.get("atr14_pct_3m")
    if not regime or _finite(adx) is None or _finite(atr) is None:
        return None
    key = _regime_key({"feature_snapshot_at_signal": {"cycle_3m_universe": {
        "regime": regime, "adx14": _finite(adx), "atr14_pct_3m": _finite(atr), "session_utc": "ALL"}}})
    return key.rsplit("|", 1)[0]


def _paired(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n < 2:
        return {"n": n, "mean_usd": round(values[0], 6) if n else None, "ci95_usd": None}
    mean, sd = statistics.fmean(values), statistics.stdev(values)
    half = CI_Z * sd / math.sqrt(n)
    return {"n": n, "mean_usd": round(mean, 6), "ci95_usd": [round(mean - half, 6), round(mean + half, 6)]}


def ci_label(summary: Mapping[str, Any]) -> str:
    ci = summary.get("ci95_usd")
    if not ci:
        return "NO_CI"
    return "CI_ABOVE_ZERO" if ci[0] > 0 else "CI_BELOW_ZERO" if ci[1] < 0 else "CI_SPANS_ZERO"


def _best(stats: Mapping[str, list[float]], minimum: int, order: list[str]) -> str | None:
    eligible = [(statistics.fmean(v), -order.index(lane), lane) for lane, v in stats.items()
                if len(v) >= minimum and lane in order]
    return max(eligible)[2] if eligible else None


def _strategy_closes(current: Iterable[Mapping[str, Any]]):
    closes, excluded = [], Counter()
    signatures: dict[str, Counter[str]] = defaultdict(Counter)
    for row in current:
        lane = _upper(row.get("research_lane"))
        signatures[lane][str(row.get("policy_signature") or "MISSING")] += 1
        if _upper(row.get("exit_reason") or row.get("outcome_exit_reason")) in FORCED_EXIT_REASONS:
            excluded["FORCED_EXIT"] += 1
            continue
        pnl, _basis = exact_net_pnl(row)
        decision = _epoch_seconds(row.get("shared_ai_call_ts"))
        closed = _epoch_seconds(row.get("close_ts")) or _epoch_seconds(row.get("ts"))
        regime = trade_regime_key(row)
        if pnl is None:
            excluded["NET_PNL_MISSING"] += 1
        elif decision is None or closed is None:
            excluded["DECISION_OR_CLOSE_TS_MISSING"] += 1
        elif regime is None:
            excluded["ENTRY_REGIME_MISSING"] += 1
        else:
            signal = str(row.get("shared_ai_call_id") or "") or f"ts:{decision:.3f}"
            closes.append({"lane": lane, "pnl": pnl, "decision": decision, "closed": closed,
                           "regime": regime, "signal": signal})
    return closes, excluded, signatures


def build_fixed_vs_dynamic_selector(
    *, registry: Mapping[str, Any], tile_order: Iterable[str], trades: Iterable[Mapping[str, Any]],
    epoch_id: str | None, v2_start_ts: float | None, generated_at: float | None = None,
    relay_interference_ids: Iterable[str] = (),
    lifecycle_contradiction_ids: Iterable[str] = (),
) -> dict[str, Any]:
    order = [str(lane).upper() for lane in tile_order]
    current, _quarantined = classify_trade_rows(
        trades, tiles=set(order), epoch_id=epoch_id, v2_start_ts=v2_start_ts,
        relay_interference_ids=set(relay_interference_ids or ()),
        lifecycle_contradiction_ids=set(lifecycle_contradiction_ids or ()))
    closes, excluded, signatures = _strategy_closes(current)

    signals: dict[str, dict[str, Any]] = {}
    regime_disagreements = 0
    for c in closes:
        s = signals.setdefault(c["signal"], {"decision": c["decision"], "regime": c["regime"], "by_lane": {}})
        s["decision"] = min(s["decision"], c["decision"])
        if c["regime"] != s["regime"]:
            regime_disagreements += 1
        s["by_lane"][c["lane"]] = c["pnl"]

    fixed_vals, dyn_vals, paired = [], [], []
    picks: Counter[str] = Counter()
    regime_oos: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"fixed": [], "dynamic": [], "dynamic_specific": []})
    dyn_choice_by_regime: dict[str, Counter[str]] = defaultdict(Counter)
    first_pick_at = None
    for _sid, s in sorted(signals.items(), key=lambda kv: kv[1]["decision"]):
        by_tile: dict[str, list[float]] = defaultdict(list)
        by_regime_tile: dict[str, list[float]] = defaultdict(list)
        for c in closes:
            if c["closed"] < s["decision"]:
                by_tile[c["lane"]].append(c["pnl"])
                if c["regime"] == s["regime"]:
                    by_regime_tile[c["lane"]].append(c["pnl"])
        fixed = _best(by_tile, MIN_TRAIN_CLOSES, order)
        regime_pick = _best(by_regime_tile, MIN_REGIME_TRAIN_CLOSES, order)
        dynamic = regime_pick or fixed
        if dynamic is None:
            picks["ABSTAIN_NO_TILE_WITH_ENOUGH_TRAINING"] += 1
            continue
        first_pick_at = first_pick_at or s["decision"]
        picks["DYNAMIC_REGIME_SPECIFIC" if regime_pick else "DYNAMIC_FALLBACK_TO_FIXED"] += 1
        dyn_choice_by_regime[s["regime"]][dynamic] += 1
        f_out = s["by_lane"].get(fixed) if fixed else None
        d_out = s["by_lane"].get(dynamic)
        cell = regime_oos[s["regime"]]
        if f_out is not None:
            fixed_vals.append(f_out)
            cell["fixed"].append(f_out)
        elif fixed:
            picks["FIXED_PICK_HAS_NO_STRATEGY_CLOSE"] += 1
        if d_out is not None:
            dyn_vals.append(d_out)
            cell["dynamic"].append(d_out)
            if regime_pick:
                cell["dynamic_specific"].append(d_out)
        else:
            picks["DYNAMIC_PICK_HAS_NO_STRATEGY_CLOSE"] += 1
        if f_out is not None and d_out is not None:
            paired.append(d_out - f_out)

    fixed_oos, dyn_oos, diff = _summary(fixed_vals), _summary(dyn_vals), _paired(paired)
    specific_n = sum(len(v["dynamic_specific"]) for v in regime_oos.values())
    gates = [
        {"gate": "fixed_oos_closes", "n": fixed_oos["n"], "min": MIN_OOS_CLOSES},
        {"gate": "dynamic_oos_closes", "n": dyn_oos["n"], "min": MIN_OOS_CLOSES},
        {"gate": "dynamic_regime_specific_oos_closes", "n": specific_n, "min": MIN_OOS_CLOSES},
    ]
    for g in gates:
        g["status"] = "PASS" if g["n"] >= g["min"] else NOT_ENOUGH
        g["needed"] = max(0, g["min"] - g["n"])
    if any(g["status"] != "PASS" for g in gates):
        verdict = NOT_ENOUGH
        verdict_text = "NOT ENOUGH DATA: " + "; ".join(
            f"{g['gate']} n={g['n']} (<{g['min']})" for g in gates if g["status"] != "PASS")
    else:
        low, high = diff["ci95_usd"] or (None, None)
        if low is not None and low > 0:
            verdict = "DYNAMIC_BETTER"
        elif high is not None and high < 0:
            verdict = "FIXED_BETTER"
        else:
            verdict = "NO_SIGNIFICANT_DIFFERENCE"
        verdict_text = (f"{verdict}: dynamic minus fixed {diff['mean_usd']} USD/close, 95% CI {diff['ci95_usd']} "
                        f"(fixed {ci_label(fixed_oos)}, dynamic {ci_label(dyn_oos)})")

    per_regime = []
    for regime in sorted({c["regime"] for c in closes}):
        cell = regime_oos.get(regime, {"fixed": [], "dynamic": [], "dynamic_specific": []})
        support = Counter(c["lane"] for c in closes if c["regime"] == regime)
        descriptive = {}
        for lane in order:
            vals = [c["pnl"] for c in closes if c["regime"] == regime and c["lane"] == lane]
            descriptive[lane] = {**_summary(vals),
                                 "gate": "OK" if len(vals) >= MIN_REGIME_TRAIN_CLOSES else NOT_ENOUGH}
        per_regime.append({
            "regime": regime,
            "oos_fixed": _summary(cell["fixed"]),
            "oos_dynamic": _summary(cell["dynamic"]),
            "oos_dynamic_regime_specific_n": len(cell["dynamic_specific"]),
            "dynamic_choices": dict(dyn_choice_by_regime.get(regime, {})),
            "gate": ("OK" if len(cell["dynamic"]) >= MIN_REGIME_OOS_CLOSES
                     else f"{NOT_ENOUGH} (n<{MIN_REGIME_OOS_CLOSES})"),
            "closes_by_tile": {lane: support.get(lane, 0) for lane in order},
            "full_sample_by_tile_descriptive": descriptive,
        })

    tiles = {}
    for lane in order:
        vals = [c["pnl"] for c in sorted((c for c in closes if c["lane"] == lane), key=lambda c: c["decision"])]
        half = len(vals) // 2
        first, second = _summary(vals[:half]), _summary(vals[half:])
        enough = half >= MIN_HALF_CLOSES
        consistent = bool(enough and first["mean_usd"] is not None and second["mean_usd"] is not None
                          and first["mean_usd"] > 0 and second["mean_usd"] > 0)
        sig_counts = signatures.get(lane, Counter())
        tiles[lane] = {
            "label": (registry.get(lane) or {}).get("label") or lane,
            "full_sample": _summary(vals),
            "chronological_halves": {
                "first": first, "second": second,
                "status": ("CONSISTENT_POSITIVE" if consistent
                           else f"{NOT_ENOUGH} (half n<{MIN_HALF_CLOSES})" if not enough
                           else "NOT_CONSISTENT_POSITIVE")},
            "oos_consistent_positive": consistent,
            "observed_policy_signatures": dict(sig_counts),
            "identity_single_signature": len(sig_counts) == 1,
        }
    ranked = [(t["full_sample"]["mean_usd"], lane) for lane, t in tiles.items()
              if t["full_sample"]["n"] >= MIN_TRAIN_CLOSES]

    generated = generated_at or datetime.now(timezone.utc).timestamp()
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": _iso(generated),
        "epoch_id": epoch_id,
        "v2_data_start_utc": _iso(v2_start_ts),
        "tile_order": order,
        "live_policy_change_allowed": False,
        "method": {
            "evaluation": "walk-forward per shared AI signal; a selector learns only from closes with close_ts "
                          "before the signal's shared_ai_call_ts (expanding window, re-fit at every signal)",
            "regime_key": "entry_context regime | ADX bucket | ATR% bucket (Chronological OOS buckets; "
                          "session not split); recorded at entry, so no lookahead",
            "outcome": "the selected tile's own exact after-cost net PnL for that signal; strategy exits only "
                       "(deploy/operator forced exits excluded)",
            "difference": "paired per signal (dynamic minus fixed) where both picks closed; "
                          "95% CI = mean +/- 1.96*sd/sqrt(n)",
            "min_train_closes": MIN_TRAIN_CLOSES, "min_regime_train_closes": MIN_REGIME_TRAIN_CLOSES,
            "min_oos_closes": MIN_OOS_CLOSES, "min_regime_oos_closes": MIN_REGIME_OOS_CLOSES,
            "costs": "reconciled terminal cost receipt: fees, funding and slippage embedded",
        },
        "verdict": verdict,
        "verdict_text": verdict_text,
        "gates": gates,
        "best_fixed_tile_full_sample": max(ranked)[1] if ranked else None,
        "oos": {"fixed": {**fixed_oos, "ci_label": ci_label(fixed_oos)},
                "dynamic": {**dyn_oos, "ci_label": ci_label(dyn_oos)},
                "dynamic_minus_fixed_paired": diff},
        "pick_counts": dict(sorted(picks.items())),
        "first_oos_pick_utc": _iso(first_pick_at),
        "per_regime": per_regime,
        "tiles": tiles,
        "inputs": {"strategy_closes": len(closes), "signals": len(signals),
                   "excluded": dict(sorted(excluded.items())),
                   "regime_disagreements_within_signal": regime_disagreements},
    }
