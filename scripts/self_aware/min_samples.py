"""Pre-registered countability minimums (Strategist, 19 Oct 2026) as code. Reference the frozen files by sha, not by copied prose.

* frozen H1-H8 file ........ PREREG-H1-H8-20261016.md
* Addendum 1 (min samples) . PREREG-ADDENDUM-MIN-SAMPLES-20261019.md
* Addendum 2 (combos pass) . PREREG-ADDENDUM2-COMBOS-HOLDOUT-PASS-20261019.md

Below a minimum the state is PENDING_SAMPLES: it never feeds a verdict, an edge claim or a ranking (``edge_claim`` and
``ranking_allowed`` are always False unless the state is COUNTABLE / PASS). H7 is UNTESTABLE (side rule not declared
pre-epoch) and H5 is FEASIBILITY_ONLY; neither is RED nor PENDING. Early kill/retire rules are FLAGS only: nothing in this
module changes a tile.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Iterable

FROZEN_H1_H8_SHA256 = "016de4e546c83dfeee70431cda6a37a43674da3821853039e07870085326e5f0"
ADDENDUM1_SHA256 = "a61ce07dba9f30e5b434b2c7454ff210e6609353208b459cd0b81ccdab7c96f5"
ADDENDUM2_SHA256 = "2a047c5e4083e81713986c45f3444d442995acd6f5292b1cd26a172e5d0bc0ce"
PREREG = {"frozen_h1_h8_sha256": FROZEN_H1_H8_SHA256, "addendum1_sha256": ADDENDUM1_SHA256,
          "addendum2_sha256": ADDENDUM2_SHA256}

MIN_TRADES = 300
REPRICE_CAP_SEC = 10.0
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261019
COMBOS_POLICIES_SEARCHED = 5832
COMBOS_MIN_MEAN_BP = 0.5          # Addendum 2: frozen retire floor
KILL_AT, KILL_MEAN_BP = 60, -1.0  # early rules: flags only
RETIRE_AT, RETIRE_MEAN_BP = 150, 0.5

# study -> (min valid trades, min distinct days); None = not a sample-count study
STUDIES: dict[str, tuple[int, int] | None] = {
    "H1": (MIN_TRADES, 3), "H2": (MIN_TRADES, 3), "H4": (MIN_TRADES, 3), "H6": (MIN_TRADES, 3),
    "H3": (MIN_TRADES, 5), "H8": (MIN_TRADES, 5), "analyzer.combos": (MIN_TRADES, 5),
    "H7": None, "H5": None,
}
STATIC_STATES = {"H7": "UNTESTABLE", "H5": "FEASIBILITY_ONLY"}
STATIC_WHY = {"H7": "side rule was not declared pre-epoch: untestable this epoch (Addendum 1)",
              "H5": "feasibility only: lead >= 1.52 s measurable, no sample-count verdict (Addendum 1)"}


def _g(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def invalid_reason(row: dict) -> str | None:
    """Why a closed tile row is NOT a valid countable trade (None = valid). Definition of 'valid' (HM 19 Oct):
    fresh epoch, book-priced (not FORCED_*/NO_BOOK or legacy BBO), not identity_quarantined, not deploy-flatten,
    not unstamped/straddle. REPRICED_CURRENT_TIME exits are valid only within the 10 s cap (counted separately)."""
    if not isinstance(row, dict) or str(row.get("status") or "").upper() != "CLOSED":
        return "NOT_CLOSED"
    pr = row.get("pricing") or {}
    ex = row.get("exit") or {}
    if row.get("identity_quarantined"):
        return "IDENTITY_QUARANTINED"
    if ex.get("deploy_flatten") or str(ex.get("close_origin") or "") == "DEPLOY_MAINTENANCE":
        return "DEPLOY_FLATTEN"
    if pr.get("fresh_eligible") is not True:
        return "NOT_FRESH_ELIGIBLE"
    if not str(pr.get("fresh_epoch_id") or "").strip():
        return "UNSTAMPED_FRESH_EPOCH_ID"
    if _num(row.get("entry_ts")) is None or _num(ex.get("close_ts")) is None:
        return "UNSTAMPED_OR_STRADDLE_TS"
    st = str(pr.get("exit_status") or "")
    if st == "REPRICED_CURRENT_TIME":
        age = _num(pr.get("exit_reprice_age_sec"))
        if age is None or age > REPRICE_CAP_SEC:
            return "REPRICED_BEYOND_10S_CAP"
        return None
    if st not in ("PAPER_FILL", "BOOK10L_FILL"):
        return "FORCED_OR_UNPRICED_EXIT"
    if str(pr.get("fill_model") or "").upper() in ("BBO", "LEGACY_BBO", "IDEAL_TOUCH"):
        return "LEGACY_BBO_PRICING"
    if pr.get("fill_valid") is False or pr.get("all_legs_book10l") is False:
        return "NOT_BOOK_PRICED"
    return None


def valid_trades(rows: Iterable[dict]) -> dict:
    """Valid trades, one per entry, non-overlapping holds per hypothesis; REPRICED_CURRENT_TIME reported separately."""
    seen_entry: set = set()
    cand, rejected, reprice = [], {}, 0
    for r in rows:
        why = invalid_reason(r)
        if why:
            rejected[why] = rejected.get(why, 0) + 1
            continue
        key = (str(r.get("hypothesis") or r.get("tile") or ""), str(r.get("entry_id") or r.get("trade_id")))
        if key in seen_entry:
            rejected["DUPLICATE_ENTRY"] = rejected.get("DUPLICATE_ENTRY", 0) + 1
            continue
        seen_entry.add(key)
        cand.append(r)
    out, last_exit = [], {}
    for r in sorted(cand, key=lambda x: float(x["entry_ts"])):
        h = str(r.get("hypothesis") or r.get("tile") or "")
        if float(r["entry_ts"]) < last_exit.get(h, float("-inf")):
            rejected["OVERLAPPING_HOLD"] = rejected.get("OVERLAPPING_HOLD", 0) + 1
            continue
        last_exit[h] = float(r["exit"]["close_ts"])
        out.append(r)
        if (r.get("pricing") or {}).get("exit_status") == "REPRICED_CURRENT_TIME":
            reprice += 1
    return {"trades": out, "n": len(out), "repriced_current_time_n": reprice, "rejected_by_reason": rejected}


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _mean(xs: list) -> float | None:
    return sum(xs) / len(xs) if xs else None


def early_flags(net_bp: list) -> list:
    """Flags only; never an automatic tile change."""
    n, m, out = len(net_bp), _mean(net_bp), []
    if n >= KILL_AT and m is not None and m <= KILL_MEAN_BP:
        out.append({"flag": "KILL_RULE_MET", "at_n": KILL_AT, "mean_bp": round(m, 3), "auto_action": False})
    if n >= RETIRE_AT and m is not None and m < RETIRE_MEAN_BP:
        out.append({"flag": "RETIRE_RULE_MET", "at_n": RETIRE_AT, "mean_bp": round(m, 3), "auto_action": False})
    return out


def study_state(study: str, rows: Iterable[dict] | None = None, net_bp_of=lambda r: _num(r.get("net_bp"))) -> dict:
    base = {"study": study, "prereg": PREREG, "edge_claim": False, "ranking_allowed": False, "auto_tile_change": False}
    if study in STATIC_STATES:
        return {**base, "state": STATIC_STATES[study], "why": STATIC_WHY[study]}
    mn, md = STUDIES[study]
    vt = valid_trades(rows or [])
    days = len({_day(t["exit"]["close_ts"]) for t in vt["trades"]})
    nets = [x for x in (net_bp_of(t) for t in vt["trades"]) if x is not None]
    ok = vt["n"] >= mn and days >= md
    return {**base, "state": "COUNTABLE" if ok else "PENDING_SAMPLES", "n_valid": vt["n"], "min_n": mn, "distinct_days": days,
            "min_days": md, "repriced_current_time_n": vt["repriced_current_time_n"],
            "rejected_by_reason": vt["rejected_by_reason"], "early_flags": early_flags(nets),
            "edge_claim": False, "ranking_allowed": ok}


def _cluster_bootstrap_lower(values: list, clusters: list, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> float:
    """95% CI lower bound (2.5th percentile) of the mean, resampling 1-hour clusters; fixed seed."""
    by: dict = {}
    for v, c in zip(values, clusters):
        by.setdefault(c, []).append(v)
    sums = [(sum(x), len(x)) for x in by.values()]
    k = len(sums)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        s = n = 0
        for _i in range(k):
            a, b = sums[rng.randrange(k)]
            s += a
            n += b
        means.append(s / n)
    means.sort()
    return means[int(0.025 * draws)]


def combos_holdout(h: dict | None) -> dict:
    """analyzer.combos sealed holdout (Addendum 1 + 2): ONE frozen policy, ONE look, frozen BEFORE any holdout row exists.

    h = {policy_id, frozen_at, sealed_at, looks, trades:[{ts, net_bp, control_net_bp, entry_ts, exit{close_ts}, ...}],
         training:{bh_fdr_q010_passed, policies_tested}}. Training-cut results are never evidence: they only gate PASS (gate 5).
    """
    base = {"study": "analyzer.combos", "prereg": PREREG, "edge_claim": False, "ranking_allowed": False,
            "training_cut_is_evidence": False, "auto_tile_change": False}
    if not isinstance(h, dict) or not h.get("trades"):
        return {**base, "state": "PENDING_SAMPLES", "n_valid": 0, "min_n": MIN_TRADES, "min_days": 5,
                "why": "no sealed holdout trades yet"}
    frozen, sealed = _num(h.get("frozen_at")), _num(h.get("sealed_at"))
    if frozen is None or sealed is None or sealed < frozen:
        return {**base, "state": "REFUSED_NOT_SEALED", "why": "holdout needs frozen_at and sealed_at (sealed_at >= frozen_at)"}
    if (_num(h.get("looks")) or 1) > 1:
        return {**base, "state": "REFUSED_REOPENED", "why": "the holdout is one look; it is never reopened or re-used"}
    pre = [t for t in h["trades"] if (_num(t.get("entry_ts")) or 0.0) < frozen]
    if pre:
        return {**base, "state": "REFUSED_NOT_SEALED", "rows_before_freeze": len(pre),
                "why": "a holdout row predates the policy freeze: not countable"}
    vt = valid_trades([{**t, "status": t.get("status", "CLOSED")} for t in h["trades"]])
    trades = vt["trades"]
    days = len({_day(t["exit"]["close_ts"]) for t in trades})
    info = {"policy_id": h.get("policy_id"), "n_valid": vt["n"], "min_n": MIN_TRADES, "distinct_days": days, "min_days": 5,
            "repriced_current_time_n": vt["repriced_current_time_n"], "rejected_by_reason": vt["rejected_by_reason"]}
    if vt["n"] < MIN_TRADES or days < 5:
        return {**base, **info, "state": "PENDING_SAMPLES"}
    nets = [float(t["net_bp"]) for t in trades]
    hours = [int(float(t["exit"]["close_ts"]) // 3600) for t in trades]
    mean = _mean(nets)
    gates = {"g1_n_days": True, "g2_mean_ge_0p5bp": mean >= COMBOS_MIN_MEAN_BP,
             "g3_ci_lower_gt0": _cluster_bootstrap_lower(nets, hours) > 0}
    ctl = [(_num(t.get("control_net_bp"))) for t in trades]
    if any(c is None for c in ctl):
        gates["g4_control_diff_ci_lower_gt0"] = False
    else:
        diffs = [a - b for a, b in zip(nets, ctl)]
        gates["g4_control_diff_ci_lower_gt0"] = _cluster_bootstrap_lower(diffs, hours) > 0
    tr = h.get("training") or {}
    gates["g5_training_bh_fdr_q010_all_5832"] = bool(tr.get("bh_fdr_q010_passed")) and tr.get("policies_tested") == COMBOS_POLICIES_SEARCHED
    passed = all(gates.values())
    return {**base, **info, "state": "PASS" if passed else "FAIL", "gates": gates, "mean_net_bp": round(mean, 3),
            "edge_claim": passed, "ranking_allowed": passed,
            "policy_retired": not passed, "holdout_reopen_allowed": False}


def combos_pending_text(report_obj: Any) -> str | None:
    """Used by the section-contract engine: text while analyzer.combos is below the registered minimum / not sealed."""
    h = _g(report_obj, "policy_grid", "sealed_holdout")
    res = combos_holdout(h if isinstance(h, dict) else None)
    if res["state"] in ("PENDING_SAMPLES", "REFUSED_NOT_SEALED", "REFUSED_REOPENED"):
        return (f"{res['state']}: analyzer.combos needs {MIN_TRADES} valid sealed-holdout trades over >= 5 days for ONE frozen "
                f"policy (Addendum1 sha {ADDENDUM1_SHA256}, Addendum2 sha {ADDENDUM2_SHA256}); never feeds a verdict, "
                f"edge claim or ranking" + (f" [{res.get('why')}]" if res.get("why") else ""))
    return None
