"""LLM direction versus shadow challengers, from the per-call challenger log.

Every shared AI call logs one side per challenger (score-led LLM, abstain-respecting
LLM, 4-feature rule vote, inverted AI, 1m order-flow sign, 5m contrarian, seeded
random, compact v5 shadow prompt). The runtime matures each call from the 1s
tape at +10s/+1m/+5m/+15m/+60m (mid-to-mid, with the quoted spread at both ends)
and through a registry-derived tile geometry proxy.

Scoring: a side earns ``sign * mid_ret_bp``; a NONE side is flat and earns 0, so
abstention is neither rewarded nor excused. ``net`` subtracts half the quoted
spread at entry and exit when a side is taken (no exchange fee is assumed here;
the geometry section is the tile-relevant view).

Inference: observations are clustered by UTC decision hour because consecutive
three-minute calls share one market path. Each test reports a cluster-robust
(CR1) t statistic with G-1 degrees of freedom, a cluster bootstrap 95% CI, and
Benjamini-Hochberg q-values across the whole family of tests in the report.
Nothing here can place, change or cancel an order.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Optional

import ai_shadow_challengers as shadow

SCHEMA = "ai_challenger_report_v1"
BASELINE = "llm_score_led"
PRIMARY_HORIZON_SEC = 900
MIN_CALLS = 30
MIN_CLUSTERS = 10
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261001
FDR_Q = 0.05


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


# ---------------------------------------------------------------------------
# Statistics (stdlib only)
# ---------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(ln_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(ln_front) * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    if df <= 0 or not math.isfinite(t):
        return float("nan")
    return _betainc(df / 2.0, 0.5, df / (df + t * t))


def benjamini_hochberg(p_values: list) -> list:
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None and math.isfinite(p))
    q = [None] * len(p_values)
    m = len(indexed)
    running = 1.0
    for rank in range(m, 0, -1):
        p, i = indexed[rank - 1]
        running = min(running, p * m / rank)
        q[i] = min(1.0, running)
    return q


def cluster_test(values: list, clusters: list, *, seed: int = BOOTSTRAP_SEED) -> dict:
    """Mean with CR1 cluster-robust t-test and cluster bootstrap 95% CI."""
    pairs = [(v, g) for v, g in zip(values, clusters) if v is not None]
    n = len(pairs)
    groups = defaultdict(list)
    for v, g in pairs:
        groups[g].append(v)
    g_count = len(groups)
    out = {"n": n, "clusters": g_count, "mean": None, "se": None, "t": None, "df": None,
           "p": None, "ci95": [None, None]}
    if n == 0:
        return out
    mean = sum(v for v, _ in pairs) / n
    out["mean"] = _round(mean)
    if g_count < 2:
        return out
    score = sum(sum(v - mean for v in vs) ** 2 for vs in groups.values())
    se = math.sqrt(g_count / (g_count - 1) * score / (n * n))
    out["se"] = _round(se)
    out["df"] = g_count - 1
    if se > 0:
        t = mean / se
        out["t"] = _round(t, 3)
        out["p"] = _round(t_two_sided_p(t, g_count - 1), 6)
    rng = random.Random(seed)
    sums = [(sum(vs), len(vs)) for vs in groups.values()]
    boots = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        total = count = 0.0
        for _ in range(g_count):
            s, c = sums[rng.randrange(g_count)]
            total += s
            count += c
        boots.append(total / count)
    boots.sort()
    out["ci95"] = [_round(boots[int(0.025 * len(boots))]),
                   _round(boots[int(0.975 * len(boots)) - 1])]
    return out


# ---------------------------------------------------------------------------
# Journal assembly
# ---------------------------------------------------------------------------
def assemble_calls(rows: Iterable[Mapping[str, Any]], epoch_id: Optional[str] = None) -> dict:
    calls, markouts, geometry = {}, defaultdict(dict), {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if epoch_id and row.get("epoch_id") and str(row.get("epoch_id")) != str(epoch_id):
            continue
        call_id = str(row.get("shared_ai_call_id") or "")
        if not call_id:
            continue
        kind = row.get("row_kind")
        if kind == "CALL":
            calls[call_id] = dict(row)
        elif kind == "MARKOUT":
            markouts[call_id][int(_finite(row.get("horizon_sec")) or 0)] = row
        elif kind == "GEOMETRY":
            geometry[call_id] = row
    for call_id, call in calls.items():
        call["_markouts"] = markouts.get(call_id, {})
        call["_geometry"] = geometry.get(call_id)
    return calls


def _hour(call: Mapping[str, Any]) -> int:
    return int((_finite(call.get("decision_ts")) or 0.0) // 3600)


def _side(call: Mapping[str, Any], name: str) -> str:
    return (call.get("sides") or {}).get(name) or shadow.NONE


def _side_pnl(side: str, markout: Mapping[str, Any]) -> tuple:
    sign = shadow.side_sign(side)
    ret = _finite(markout.get("mid_ret_bp"))
    if ret is None:
        return None, None
    cost = ((_finite(markout.get("spread_in_bp")) or 0.0)
            + (_finite(markout.get("spread_out_bp")) or 0.0)) / 2.0
    gross = sign * ret
    return gross, gross - (cost if sign else 0.0)


def markout_section(calls: list, tests: list) -> dict:
    by_h = {}
    for h in shadow.MARKOUT_HORIZONS_SEC:
        matured = [c for c in calls if (c["_markouts"].get(h) or {}).get("tape_ok")]
        clusters = [_hour(c) for c in matured]
        base_net = [_side_pnl(_side(c, BASELINE), c["_markouts"][h])[1] for c in matured]
        entries = {}
        for name in shadow.CHALLENGERS:
            gross, net, hits = [], [], []
            for c in matured:
                side = _side(c, name)
                g, nt = _side_pnl(side, c["_markouts"][h])
                gross.append(g)
                net.append(nt)
                if shadow.side_sign(side):
                    hits.append(g)
            entry = {
                "calls": len(matured),
                "sides_taken": len(hits),
                "take_rate": _round(len(hits) / len(matured), 4) if matured else None,
                "hit_rate": _round(sum(1 for g in hits if g > 0) / len(hits), 4) if hits else None,
                "mean_gross_bp": cluster_test(gross, clusters)["mean"],
                "net_vs_zero": cluster_test(net, clusters),
            }
            tests.append({"family": "net_vs_zero", "horizon_sec": h, "challenger": name,
                          "ref": entry["net_vs_zero"]})
            if name != BASELINE:
                diff = [b - x for b, x in zip(base_net, net)]
                entry["llm_minus_challenger_net"] = cluster_test(diff, clusters)
                tests.append({"family": "llm_minus_challenger", "horizon_sec": h, "challenger": name,
                              "ref": entry["llm_minus_challenger_net"]})
            entries[name] = entry
        by_h[str(h)] = entries
    return by_h


def agreement_section(calls: list) -> dict:
    out = {}
    for name in shadow.CHALLENGERS:
        if name == BASELINE:
            continue
        both = agree = 0
        for c in calls:
            a, b = _side(c, BASELINE), _side(c, name)
            if shadow.side_sign(a) and shadow.side_sign(b):
                both += 1
                agree += int(a == b)
        out[name] = {"both_sided_calls": both,
                     "agreement_rate": _round(agree / both, 4) if both else None}
    return out


def geometry_section(calls: list) -> dict:
    lanes = defaultdict(lambda: defaultdict(list))
    for c in calls:
        results = (c.get("_geometry") or {}).get("results") or []
        for name in shadow.CHALLENGERS:
            side = _side(c, name)
            if not shadow.side_sign(side):
                continue
            for r in results:
                if r.get("lane") == shadow.COMPACT_QUESTION_LANE or r.get("side") != side:
                    continue
                lanes[str(r.get("lane"))][name].append((c, r))
    out = {}
    for lane, per in lanes.items():
        rows = {}
        for name, pairs in per.items():
            outcomes = Counter(str(r.get("result")) for _, r in pairs)
            filled = [(c, r) for c, r in pairs if r.get("result") in ("TARGET", "STOP", "TIME")]
            rows[name] = {
                "sides_taken": len(pairs),
                "outcomes": dict(outcomes),
                "fill_rate": _round(len(filled) / len(pairs), 4) if pairs else None,
                "target_rate_of_filled": (_round(outcomes.get("TARGET", 0) / len(filled), 4)
                                          if filled else None),
                "result_bp_filled": cluster_test([_finite(r.get("result_bp")) for _, r in filled],
                                                 [_hour(c) for c, _ in filled]),
            }
        out[lane] = rows
    return {"model": shadow.GEOMETRY_MODEL, "note": shadow.GEOMETRY_NOTE, "lanes": out}


def compact_section(calls: list, compact_by_id: Mapping[str, Mapping[str, Any]]) -> dict:
    rows = [compact_by_id[str(c.get("shared_ai_call_id"))] for c in calls
            if str(c.get("shared_ai_call_id")) in compact_by_id]
    states = Counter(str(r.get("call_state")) for r in rows)
    parse = Counter(str((r.get("parsed") or {}).get("parse_status"))
                    for r in rows if r.get("call_state") == "CALLED")
    pairs = []
    for c in calls:
        parsed = (compact_by_id.get(str(c.get("shared_ai_call_id"))) or {}).get("parsed") or {}
        if parsed.get("parse_status") != "OK":
            continue
        for r in (c.get("_geometry") or {}).get("results") or []:
            if (r.get("lane") != shadow.COMPACT_QUESTION_LANE
                    or r.get("result") not in ("TARGET", "STOP", "TIME")):
                continue
            p = _finite(parsed.get("p_long_success") if r.get("side") == shadow.LONG
                        else parsed.get("p_short_success"))
            if p is not None:
                pairs.append((p, 1.0 if r.get("result") == "TARGET" else 0.0))
    brier = climo = half = base_rate = None
    if pairs:
        base_rate = sum(y for _, y in pairs) / len(pairs)
        brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
        climo = sum((base_rate - y) ** 2 for _, y in pairs) / len(pairs)
        half = sum((0.5 - y) ** 2 for _, y in pairs) / len(pairs)
    bins = defaultdict(lambda: [0, 0.0, 0.0])
    for p, y in pairs:
        cell = bins[min(9, int(p * 10))]
        cell[0] += 1
        cell[1] += p
        cell[2] += y
    return {
        "prompt_id": shadow.COMPACT_PROMPT_ID,
        "call_states": dict(states),
        "parse_status": dict(parse),
        "scored_filled_questions": len(pairs),
        "base_rate_target_first": _round(base_rate),
        "brier": _round(brier),
        "brier_climatology_in_sample": _round(climo),
        "brier_constant_half": _round(half),
        "brier_skill_vs_half": None if brier is None or not half else _round(1.0 - brier / half),
        "calibration_bins": [
            {"bin": f"{b / 10:.1f}-{(b + 1) / 10:.1f}", "n": v[0],
             "mean_p": _round(v[1] / v[0]), "observed": _round(v[2] / v[0])}
            for b, v in sorted(bins.items())
        ],
        "note": ("Brier over filled compact-question geometry (+1 ATR before -1.5 ATR within "
                 "30 min after a 30 bp limit fill); climatology is in-sample, so optimistic."),
    }


def llm_behaviour_section(calls: list) -> dict:
    llm = [c.get("llm") or {} for c in calls]
    return {
        "calls": len(calls),
        "raw_no_trade_calls": sum(1 for x in llm if x.get("raw_direction") == "NO_TRADE"),
        "raw_no_trade_but_tiles_admitted_side": sum(
            1 for c, x in zip(calls, llm)
            if x.get("raw_direction") == "NO_TRADE" and shadow.side_sign(_side(c, BASELINE))
        ),
        "abstain_respecting_flat_calls": sum(1 for x in llm if x.get("abstained")),
        "ai_error_calls": sum(1 for x in llm if x.get("ai_error")),
        "win_prob_status": dict(Counter(str(c.get("win_prob_status")) for c in calls)),
        "side_counts": {name: dict(Counter(_side(c, name) for c in calls))
                        for name in shadow.CHALLENGERS},
    }


def _verdict(stat: Mapping[str, Any]) -> str:
    if (stat.get("n") or 0) < MIN_CALLS or (stat.get("clusters") or 0) < MIN_CLUSTERS:
        return "NOT_ENOUGH_DATA"
    q = stat.get("q_bh")
    if q is None or q > FDR_Q:
        return "NO_DETECTABLE_DIFFERENCE"
    return "LLM_BETTER" if (stat.get("mean") or 0) > 0 else "LLM_WORSE"


def cohort_key(call: Mapping[str, Any]) -> str:
    """Prompt cohort; calls after an input revision are a new cohort even under the same prompt id."""
    prompt_id = str(call.get("prompt_id") or "UNKNOWN")
    revision = call.get("prompt_input_revision")
    return f"{prompt_id}@{revision}" if revision else prompt_id


def build_ai_challenger_report(challenger_rows: Iterable[Mapping[str, Any]],
                               compact_rows: Iterable[Mapping[str, Any]] = (),
                               *, epoch_id: Optional[str] = None,
                               dead_input_threshold: int = 20) -> dict:
    all_calls = assemble_calls(challenger_rows, epoch_id=epoch_id)
    compact_by_id = {str(r.get("shared_ai_call_id")): r for r in (compact_rows or ())
                     if isinstance(r, Mapping) and r.get("shared_ai_call_id")}
    ordered = sorted(all_calls.values(), key=lambda c: _finite(c.get("decision_ts")) or 0.0)
    cohorts = defaultdict(list)
    for c in ordered:
        cohorts[cohort_key(c)].append(c)
    tests, cohort_payload = [], {}
    for prompt_id, calls in cohorts.items():
        local = []
        cohort_payload[prompt_id] = {
            "calls": len(calls),
            "first_decision_utc": calls[0].get("decision_utc"),
            "last_decision_utc": calls[-1].get("decision_utc"),
            "llm_behaviour": llm_behaviour_section(calls),
            "agreement_with_llm": agreement_section(calls),
            "markouts": markout_section(calls, local),
            "geometry_proxy": geometry_section(calls),
            "compact_v5": compact_section(calls, compact_by_id),
            "dead_inputs": shadow.dead_input_report(
                (c.get("prompt_payload") for c in calls if isinstance(c.get("prompt_payload"), Mapping)),
                dead_input_threshold,
            ),
        }
        for t in local:
            t["prompt_id"] = prompt_id
        tests.extend(local)
    for t, q in zip(tests, benjamini_hochberg([t["ref"].get("p") for t in tests])):
        t["ref"]["q_bh"] = _round(q, 6)
    comparisons, placebo = [], []
    for t in tests:
        ref = t["ref"]
        if t["family"] == "llm_minus_challenger" and t["horizon_sec"] == PRIMARY_HORIZON_SEC:
            comparisons.append({
                "prompt_id": t["prompt_id"], "challenger": t["challenger"],
                "horizon_sec": t["horizon_sec"], "n": ref["n"], "clusters": ref["clusters"],
                "mean_diff_net_bp": ref["mean"], "ci95": ref["ci95"], "p": ref["p"],
                "q_bh": ref.get("q_bh"), "verdict": _verdict(ref),
            })
        if t["family"] == "net_vs_zero" and t["challenger"] == "random":
            significant = ref.get("q_bh") is not None and ref["q_bh"] <= FDR_Q
            placebo.append({"prompt_id": t["prompt_id"], "horizon_sec": t["horizon_sec"],
                            "mean": ref["mean"], "q_bh": ref.get("q_bh"),
                            "flag": "PLACEBO_SIGNIFICANT" if significant else "OK"})
    if not ordered:
        status = "NO_DATA"
    elif all(c["verdict"] == "NOT_ENOUGH_DATA" for c in comparisons):
        status = "NOT_ENOUGH_DATA"
    else:
        status = "OK"
    return {
        "schema": SCHEMA,
        "status": status,
        "mode": "SHADOW_ONLY_NO_ORDERS",
        "epoch_id": epoch_id,
        "baseline": BASELINE,
        "challengers": list(shadow.CHALLENGERS),
        "horizons_sec": list(shadow.MARKOUT_HORIZONS_SEC),
        "primary_horizon_sec": PRIMARY_HORIZON_SEC,
        "current_prompt_id": ordered[-1].get("prompt_id") if ordered else None,
        "current_cohort": cohort_key(ordered[-1]) if ordered else None,
        "method": {
            "scoring": ("side * mid-to-mid return from decision+1s; NONE is flat (0); "
                        "net subtracts half the quoted spread in and out"),
            "clustering": "UTC decision hour; CR1 cluster-robust t with G-1 df",
            "ci": f"cluster bootstrap percentile 95%, {BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}",
            "multiple_testing": f"Benjamini-Hochberg across all {len(tests)} tests in this report, q<={FDR_Q}",
            "gates": f"verdicts need n>={MIN_CALLS} matured calls and >={MIN_CLUSTERS} hour clusters",
        },
        "primary_comparisons": comparisons,
        "random_placebo": placebo,
        "cohorts": cohort_payload,
        "tests_total": len(tests),
    }
