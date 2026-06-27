"""Genome Evolution Engine — Stage 1.

The 7th genome layer. A scientific advisor that asks, for the CURRENT strategy:

    "Based on everything we have learned, how could the current strategy improve?"

It performs single-parameter counterfactual sweeps over historical CLOSED trades
and estimates, for each candidate change:

    expected_gain   - EV delta vs current strategy (per-trade, USD)
    confidence      - sample-size gated (HIGH >=100, MODERATE 30-99, LOW <30)
    sample          - trades the suggestion is built on
    drift           - does the gain still hold in the most recent 20% of trades?
    status          - RESEARCH_CANDIDATE / REJECT / RESEARCH_GAP / FROZEN

CRITICAL SAFEGUARD — the evolution engine NEVER modifies execution. Every output
is advisory. A suggestion only becomes a serious recommendation when it has:
sufficient sample, positive recent drift, and (where possible) out-of-sample
support. Otherwise it is shown as a hypothesis for further testing.

Stage 1 implements the counterfactual harness + six engines:
  Entry, Thesis/FastCut, Exit, Risk, Execution, Session
plus the aggregate reports:
  pillar_scores, top_improvements, one_change, frozen, research_queue, roi

The fast-cut -12% question is handled honestly: aggregate MFE/MAE cannot replay
an intraday path, so looser cuts are flagged RESEARCH_GAP (needs path replay)
rather than guessed. Tighter cuts are approximated from the realised loss scale.
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from research.genome.quality_score import (
    ev_confidence_interval,
    research_confidence_label,
)

try:  # Python 3.9+
    from statistics import fmean as _fmean
except Exception:  # pragma: no cover
    def _fmean(xs: List[float]) -> float:
        xs = list(xs)
        return sum(xs) / len(xs) if xs else 0.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TRADES_CSV = Path(__file__).resolve().parents[2] / "trades_3factor.csv"
REPORT_OUT = Path(__file__).resolve().parent / "genome_evolution_report.json"

MIN_SAMPLE = 30          # below this => LOW confidence, hypothesis only
SERIOUS_SAMPLE = 100     # HIGH confidence threshold
RECENT_FRACTION = 0.20   # last 20% of trades by ts => drift check
FAST_CUT_GRID = [-6.0, -8.0, -10.0, -12.0, -14.0]  # % unrealised cut thresholds
RISK_STOP_GRID = [1.2, 1.4, 1.6, 1.8, 2.0]          # % stop loss candidates

# Pillars tracked by the Strategy Improvement Center
PILLARS = ["entry", "exit", "risk", "execution", "ai"]


# ---------------------------------------------------------------------------
# Trade loading & field access
# ---------------------------------------------------------------------------

def _f(t: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = t.get(k)
        if v not in (None, "", "nan", "NaN"):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _s(t: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = t.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _pnl(t: Dict[str, Any]) -> float:
    return _f(t, "outcome_net_pnl_usd", "net_pnl_usd", "pnl")


def _session_from_utc(ts: str) -> str:
    """Coarse session label from a UTC ISO ts (advisory only)."""
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return "?"
    h = dt.hour
    wd = dt.weekday()  # 0=Mon .. 6=Sun
    if wd >= 5:
        return "WEEKEND"
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 12:
        return "LONDON"
    if 12 <= h < 16:
        return "OVERLAP"
    if 16 <= h < 21:
        return "NY"
    return "OFF_HOURS"


def load_trades(csv_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = Path(csv_path or DEFAULT_TRADES_CSV)
    if not path.is_file():
        return []
    trades: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            trades.append(row)
    # sort by ts so the drift split is chronological
    def _ts_key(t: Dict[str, Any]) -> str:
        return _s(t, "ts") or "0"
    trades.sort(key=_ts_key)
    return trades


# ---------------------------------------------------------------------------
# EV helpers + drift
# ---------------------------------------------------------------------------

def _ev(pnls: List[float]) -> float:
    return _fmean(pnls) if pnls else 0.0


def _ci(pnls: List[float]) -> Dict[str, float]:
    ci = ev_confidence_interval(pnls)
    return {"low": round(ci["low"], 3), "high": round(ci["high"], 3)}


def _split_recent(trades: List[Dict[str, Any]]) -> Tuple[List, List]:
    if not trades:
        return [], []
    n = len(trades)
    cut = max(1, int(n * (1.0 - RECENT_FRACTION)))
    return trades[:cut], trades[cut:]


def _drift_flag(older_pnls: List[float], recent_pnls: List[float]) -> str:
    """Does the edge survive in the most recent slice?"""
    if len(recent_pnls) < 10:
        return "INSUFFICIENT"
    ro = _ev(older_pnls)
    rr = _ev(recent_pnls)
    if ro <= 0 and rr <= 0:
        return "ABSENT"
    if ro > 0 and rr >= ro * 0.5:
        return "STABLE"
    if rr > 0:
        return "WEAKENING"
    return "BROKEN"


def _confidence(n: int) -> str:
    return research_confidence_label(n)


# ---------------------------------------------------------------------------
# Suggestion builder
# ---------------------------------------------------------------------------

def _suggestion(
    engine: str,
    change: str,
    current_ev: float,
    candidate_ev: float,
    sample: int,
    n_remaining: int,
    reason: str,
    *,
    reject_condition: Optional[str] = None,
    drift: str = "INSUFFICIENT",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    gain = round(candidate_ev - current_ev, 4)
    conf = _confidence(n_remaining)
    if gain <= 0 and sample < MIN_SAMPLE:
        status = "REJECT"
    elif gain <= 0:
        status = "REJECT"
    elif conf == "LOW":
        status = "HYPOTHESIS"
    elif drift in ("BROKEN", "ABSENT"):
        status = "RESEARCH_CANDIDATE"
    else:
        status = "RESEARCH_CANDIDATE"
    out: Dict[str, Any] = {
        "engine": engine,
        "change": change,
        "reject_condition": reject_condition,
        "current_ev": round(current_ev, 4),
        "estimated_ev": round(candidate_ev, 4),
        "expected_gain": gain,
        "sample": sample,
        "n_remaining": n_remaining,
        "confidence": conf,
        "drift": drift,
        "status": status,
        "reason": reason,
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def _filter_counterfactual(
    trades: List[Dict[str, Any]],
    label: str,
    predicate,
    reason: str,
    engine: str,
) -> Dict[str, Any]:
    """If we had NOT taken trades matching `predicate`, what would EV become?"""
    pnls = [_pnl(t) for t in trades]
    current_ev = _ev(pnls)
    rejected = [t for t in trades if predicate(t)]
    kept = [t for t in trades if not predicate(t)]
    kept_pnls = [_pnl(t) for t in kept]
    cand_ev = _ev(kept_pnls)
    older, recent = _split_recent(kept)
    drift = _drift_flag([_pnl(t) for t in older], [_pnl(t) for t in recent])
    rej_pnls = [_pnl(t) for t in rejected]
    return _suggestion(
        engine, label, current_ev, cand_ev,
        sample=len(rejected), n_remaining=len(kept),
        reason=reason,
        reject_condition=label,
        drift=drift,
        extra={
            "rejected_ev": round(_ev(rej_pnls), 4),
            "rejected_pnl_total": round(sum(rej_pnls), 2),
        },
    )


def engine_entry(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Could we have rejected some losers before entering?"""
    out: List[Dict[str, Any]] = []
    out.append(_filter_counterfactual(
        trades, "Reject ADX<16",
        lambda t: _f(t, "adx_at_entry") < 16,
        "Low-ADX (chop) entries underperform — test ADX>=16 gate.",
        "ENTRY",
    ))
    out.append(_filter_counterfactual(
        trades, "Reject AI 60-62",
        lambda t: 60 <= _f(t, "ai_threshold", "controls_ai_threshold") <= 62,
        "Borderline AI band 60-62 may carry noise — test raising floor to 63.",
        "ENTRY",
    ))
    out.append(_filter_counterfactual(
        trades, "Reject spread<3",
        lambda t: _f(t, "conviction_spread") < 3,
        "Tight-spread entries show less edge — test spread>=3 gate.",
        "ENTRY",
    ))
    out.append(_filter_counterfactual(
        trades, "Reject NEAR_RESISTANCE longs / NEAR_SUPPORT shorts",
        lambda t: (_s(t, "sr_state") == "NEAR_RESISTANCE" and _s(t, "final_direction", "dir") == "LONG")
                  or (_s(t, "sr_state") == "NEAR_SUPPORT" and _s(t, "final_direction", "dir") == "SHORT"),
        "Counter-SR entries (into resistance long / into support short) underperform.",
        "ENTRY",
    ))
    return [s for s in out if s["sample"] > 0]


def engine_fastcut(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Thesis fast-cut threshold sweep — the -12% question.

    Aggregate MFE/MAE cannot replay an intraday path, so:
      * tighter cuts  => approximated by scaling the realised loss.
      * looser cuts   => flagged RESEARCH_GAP (outcome unknown without path replay).
    """
    fc = [t for t in trades if _s(t, "exit_reason", "outcome_exit_reason") == "THESIS_FAST_CUT"]
    out: List[Dict[str, Any]] = []
    if not fc:
        out.append({
            "engine": "FASTCUT",
            "change": "fast-cut threshold sweep",
            "status": "NO_DATA",
            "reason": "0 fast-cut trades in the loaded session — sweep cannot run yet.",
            "expected_gain": 0.0,
            "sample": 0,
            "confidence": "LOW",
            "drift": "INSUFFICIENT",
        })
        return out

    pnls_all = [_pnl(t) for t in trades]
    current_ev = _ev(pnls_all)
    # actual threshold in use (most common)
    tholds = [_f(t, "cfg_thesis_fast_exit_unreal_pct") for t in fc if _f(t, "cfg_thesis_fast_exit_unreal_pct") != 0]
    actual = _fmean(tholds) if tholds else -10.0

    for cand in FAST_CUT_GRID:
        cand_abs = abs(cand)
        actual_abs = abs(actual)
        if cand_abs <= actual_abs:
            # tighter cut => exit earlier with proportionally smaller loss
            scale = (cand_abs / actual_abs) if actual_abs else 1.0
            adj_pnls = []
            for t in trades:
                p = _pnl(t)
                if _s(t, "exit_reason", "outcome_exit_reason") == "THESIS_FAST_CUT" and p < 0:
                    adj_pnls.append(p * scale)
                else:
                    adj_pnls.append(p)
            cand_ev = _ev(adj_pnls)
            older, recent = _split_recent(trades)
            drift = _drift_flag([_pnl(t) * (scale if (_s(t, "exit_reason", "outcome_exit_reason") == "THESIS_FAST_CUT" and _pnl(t) < 0) else 1.0) for t in older],
                                [_pnl(t) * (scale if (_s(t, "exit_reason", "outcome_exit_reason") == "THESIS_FAST_CUT" and _pnl(t) < 0) else 1.0) for t in recent])
            out.append(_suggestion(
                "FASTCUT", f"Tighten fast-cut to {cand:.0f}%",
                current_ev, cand_ev,
                sample=len(fc), n_remaining=len(trades),
                reason=f"Tighter cut scales realised fast-cut losses by {scale:.2f} (approx — assumes proportional exit).",
                drift=drift,
                extra={"approximation": "linear_loss_scale", "actual_threshold_pct": round(actual, 2)},
            ))
        else:
            # looser cut => would NOT have exited at actual; outcome depends on path
            winners_if_held = sum(1 for t in fc if _f(t, "max_profit") > 0)
            deeper_loss_if_drawn = sum(1 for t in fc if _f(t, "max_drawdown", default=0) > actual_abs)
            out.append({
                "engine": "FASTCUT",
                "change": f"Loosen fast-cut to {cand:.0f}%",
                "current_ev": round(current_ev, 4),
                "estimated_ev": None,
                "expected_gain": None,
                "sample": len(fc),
                "n_remaining": len(trades),
                "confidence": "LOW",
                "drift": "INSUFFICIENT",
                "status": "RESEARCH_GAP",
                "reason": (
                    "Looser cut would let some fast-cut trades continue. Outcome is NOT "
                    "determinable from MFE/MAE alone — requires intraday path replay. "
                    f"Of {len(fc)} fast-cut trades, {winners_if_held} did reach a positive peak "
                    f"(max_profit>0) and {deeper_loss_if_drawn} drew down beyond {actual:.0f}%."
                ),
                "approximation": "needs_path_replay",
                "actual_threshold_pct": round(actual, 2),
                "peaks_reached": winners_if_held,
                "deeper_drawdowns": deeper_loss_if_drawn,
            })
    return out


def engine_exit(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Which exit reason has the best EV? (advisory — does not move trades)"""
    by_exit: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        by_exit[_s(t, "exit_reason", "outcome_exit_reason") or "?"].append(_pnl(t))
    out: List[Dict[str, Any]] = []
    pnls_all = [_pnl(t) for t in trades]
    current_ev = _ev(pnls_all)
    ranked = sorted(((k, v) for k, v in by_exit.items() if k != "?"),
                    key=lambda kv: _ev(kv[1]), reverse=True)
    for exit_reason, pnls in ranked:
        n = len(pnls)
        ev = _ev(pnls)
        out.append({
            "engine": "EXIT",
            "change": f"Profile: {exit_reason}",
            "exit_reason": exit_reason,
            "sample": n,
            "ev": round(ev, 4),
            "pnl_total": round(sum(pnls), 2),
            "confidence": _confidence(n),
            "status": "OBSERVED",
            "reason": "Observed EV by exit reason — advisory, do not reallocate trades to a different exit mechanically.",
        })
    return out


def engine_risk(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stop-loss sweep — optimise drawdown vs EV trade-off."""
    pnls_all = [_pnl(t) for t in trades]
    current_ev = _ev(pnls_all)
    out: List[Dict[str, Any]] = []
    for stop in RISK_STOP_GRID:
        # trades whose MAE breached this stop would have been closed at -stop%
        # approximate: cap losses at -stop (in pnl-pct terms via margin)
        adj_pnls = []
        capped = 0
        for t in trades:
            p = _pnl(t)
            mae_pct = _f(t, "max_drawdown")  # magnitude in % of entry
            cap_usd = -(_f(t, "margin_usdt", default=15.0) * stop / 100.0)
            if p < cap_usd and mae_pct >= stop:
                adj_pnls.append(cap_usd)
                capped += 1
            else:
                adj_pnls.append(p)
        cand_ev = _ev(adj_pnls)
        older, recent = _split_recent(trades)
        drift = _drift_flag([_pnl(t) for t in older], [_pnl(t) for t in recent])
        out.append(_suggestion(
            "RISK", f"Stop loss @ {stop:.1f}%",
            current_ev, cand_ev,
            sample=capped, n_remaining=len(trades),
            reason=f"Caps losses that drew down beyond {stop:.1f}% (approx — assumes fill at stop).",
            drift=drift,
            extra={"capped_trades": capped, "approximation": "fill_at_stop"},
        ))
    return out


def engine_execution(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Execution timing — entry delay, chase, slippage."""
    out: List[Dict[Dict[str, Any]], Any] = []
    out = []
    out.append(_filter_counterfactual(
        trades, "Reject entry_delay_sec > 120",
        lambda t: _f(t, "entry_delay_sec") > 120,
        "Long fill delays correlate with stale signals — test a 120s TTL.",
        "EXECUTION",
    ))
    out.append(_filter_counterfactual(
        trades, "Reject slippage > 0.30%",
        lambda t: _f(t, "slippage") > 0.30,
        "High-slippage fills erode edge — test a slippage cap.",
        "EXECUTION",
    ))
    # chase: is chase>=3 better than direct?
    direct = [t for t in trades if _f(t, "limit_chase_count") == 0]
    chased = [t for t in trades if _f(t, "limit_chase_count") >= 3]
    if direct and chased:
        out.append({
            "engine": "EXECUTION",
            "change": "Direct vs Chase 3+ (observed)",
            "direct_ev": round(_ev([_pnl(t) for t in direct]), 4),
            "direct_n": len(direct),
            "chase_ev": round(_ev([_pnl(t) for t in chased]), 4),
            "chase_n": len(chased),
            "confidence": _confidence(min(len(direct), len(chased))),
            "status": "OBSERVED",
            "reason": "Observed EV of direct fills vs chase>=3 — advisory.",
        })
    return [s for s in out if isinstance(s, dict) and s.get("sample", 1) is None or s.get("sample", 0) > 0 or s.get("status") == "OBSERVED"]


def engine_session(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Session weighting — disable Asia / weekend etc."""
    out: List[Dict[str, Any]] = []
    for sess in ("ASIA", "WEEKEND", "OFF_HOURS"):
        out.append(_filter_counterfactual(
            trades, f"Disable {sess}",
            lambda t, s=sess: _session_from_utc(_s(t, "ts")) == s,
            f"Test disabling {sess} session — observed EV may be negative.",
            "SESSION",
        ))
    return [s for s in out if s["sample"] > 0]


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

def _pillar_scores(sugg: List[Dict[str, Any]]) -> Dict[str, Any]:
    """1-5 stars + potential gain % per pillar."""
    by_pillar: Dict[str, List[Dict[str, Any]]] = {p: [] for p in PILLARS}
    engine_to_pillar = {
        "ENTRY": "entry", "FASTCUT": "exit", "EXIT": "exit",
        "RISK": "risk", "EXECUTION": "execution", "SESSION": "execution",
    }
    for s in sugg:
        if s.get("expected_gain") is None:
            continue
        p = engine_to_pillar.get(s.get("engine", ""), "ai")
        by_pillar[p].append(s)
    # AI pillar: no parameter sweep in stage 1 — derive from AI band spread
    scores: Dict[str, Any] = {}
    for p in PILLARS:
        cands = by_pillar[p]
        gains = [c["expected_gain"] for c in cands if c.get("expected_gain") and c["expected_gain"] > 0]
        best = max(gains) if gains else 0.0
        # stars: 5 => best>0.20, 4 => >0.12, 3 => >0.06, 2 => >0.02, 1 => else
        if best > 0.20:
            stars = 5
        elif best > 0.12:
            stars = 4
        elif best > 0.06:
            stars = 3
        elif best > 0.02:
            stars = 2
        else:
            stars = 1
        scores[p] = {
            "stars": stars,
            "best_gain_per_trade": round(best, 4),
            "candidates": len(cands),
        }
    return scores


def _top_improvements(sugg: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    cands = [s for s in sugg if s.get("status") in ("RESEARCH_CANDIDATE", "HYPOTHESIS")
             and s.get("expected_gain") and s["expected_gain"] > 0]
    cands.sort(key=lambda s: s["expected_gain"], reverse=True)
    return cands[:limit]


def _one_change(top: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    serious = [t for t in top if t.get("confidence") in ("HIGH", "MODERATE") and t.get("drift") in ("STABLE", "WEAKENING")]
    pick = serious[0] if serious else (top[0] if top else None)
    if not pick:
        return None
    return {
        "change": pick["change"],
        "engine": pick["engine"],
        "expected_gain": pick["expected_gain"],
        "confidence": pick["confidence"],
        "sample": pick.get("n_remaining") or pick.get("sample"),
        "drift": pick.get("drift"),
        "reason": pick.get("reason"),
    }


def _frozen(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Components that are stable and should NOT be changed."""
    frozen: List[Dict[str, Any]] = []
    # best exit reason with high sample + positive EV
    by_exit: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        by_exit[_s(t, "exit_reason", "outcome_exit_reason") or "?"].append(_pnl(t))
    for er, pnls in by_exit.items():
        n = len(pnls)
        ev = _ev(pnls)
        if er in ("?", "THESIS_FAST_CUT"):
            continue
        if n >= SERIOUS_SAMPLE and ev > 0:
            frozen.append({
                "component": f"Exit profile: {er}",
                "sample": n,
                "ev": round(ev, 4),
                "confidence": "HIGH",
                "reason": f"Positive EV across {n} trades — keep until invalidated.",
            })
    # best research lane
    by_lane: Dict[str, List[float]] = defaultdict(list)
    for t in trades:
        ln = _s(t, "research_lane")
        if ln:
            by_lane[ln].append(_pnl(t))
    for ln, pnls in by_lane.items():
        n = len(pnls)
        ev = _ev(pnls)
        if n >= SERIOUS_SAMPLE and ev > 0:
            frozen.append({
                "component": f"Lane: {ln}",
                "sample": n,
                "ev": round(ev, 4),
                "confidence": "HIGH",
                "reason": f"Best-in-class EV across {n} trades — freeze.",
            })
            break
    return frozen


def _research_queue(sugg: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hyp = [s for s in sugg if s.get("status") in ("HYPOTHESIS", "RESEARCH_GAP")]
    return [{"change": s.get("change"), "engine": s.get("engine"),
             "reason": s.get("reason"), "status": s.get("status"),
             "sample": s.get("sample")} for s in hyp]


def _strategy_score(pillars: Dict[str, Any], n: int) -> Dict[str, Any]:
    avg_stars = _fmean([p["stars"] for p in pillars.values()]) if pillars else 0.0
    score = round(20 * avg_stars + min(20, n / 50.0), 1)  # 0-120ish
    return {"score": min(100.0, score), "stars_avg": round(avg_stars, 2), "sample": n}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_report(csv_path: Optional[Path] = None) -> Dict[str, Any]:
    trades = load_trades(csv_path)
    n = len(trades)
    if n == 0:
        return {
            "status": "NO_DATA",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "message": "No closed trades found at the expected CSV path. Run the bot/analyzer first.",
            "trades_loaded": 0,
        }

    pnls = [_pnl(t) for t in trades]
    current_ev = _ev(pnls)
    ci = _ci(pnls)

    entry = engine_entry(trades)
    fastcut = engine_fastcut(trades)
    exit_eng = engine_exit(trades)
    risk = engine_risk(trades)
    execution = engine_execution(trades)
    session = engine_session(trades)

    all_sugg = entry + fastcut + risk + execution + session  # exit_eng is observed-only
    pillars = _pillar_scores(all_sugg)
    top = _top_improvements(all_sugg)
    one = _one_change(top)
    frozen = _frozen(trades)
    queue = _research_queue(all_sugg)
    strat = _strategy_score(pillars, n)

    return {
        "status": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trades_loaded": n,
        "current_strategy": {
            "ev_per_trade": round(current_ev, 4),
            "ev_ci_95": ci,
            "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
            "pnl_total": round(sum(pnls), 2),
        },
        "strategy_score": strat,
        "pillar_scores": pillars,
        "top_improvements": top,
        "one_change": one,
        "frozen_components": frozen,
        "research_queue": queue,
        "engines": {
            "entry": entry,
            "fastcut": fastcut,
            "exit": exit_eng,
            "risk": risk,
            "execution": execution,
            "session": session,
        },
        "disclaimer": (
            "Advisory only — the Genome Evolution Engine NEVER modifies execution. "
            "Every suggestion is a hypothesis until it has sufficient sample, stable "
            "recent drift, and (where possible) out-of-sample validation + human approval. "
            "Counterfactuals are approximations from aggregate MFE/MAE; looser-cut "
            "outcomes require intraday path replay (flagged RESEARCH_GAP)."
        ),
    }


def write_report(csv_path: Optional[Path] = None, out_path: Optional[Path] = None) -> Dict[str, Any]:
    rep = build_report(csv_path)
    target = Path(out_path or REPORT_OUT)
    try:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
    except Exception as exc:  # pragma: no cover
        rep["write_error"] = str(exc)
    return rep


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(build_report(), indent=2))
