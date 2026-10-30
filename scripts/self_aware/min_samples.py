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


# Explicit lane -> hypothesis table (fail closed: an unknown lane is INVALID). H1-H8 are OFFLINE replays on the fresh-epoch
# streams, no live tile by design (Strategist 19 Oct): each live tile row maps to its own tile label ("TILE:..."), which
# only keys the one-per-entry / non-overlap rule. If an H is ever promoted to a tile, Strategist registers the lane here first.
# R22 (2026-10-28): GS-06 retired — kept only in OPAQUE_RETIRED (19 Oct fixture rows stay parseable, never current-cohort
# countable). FADE3 / FADE3-LOOSE joined the live map after the GS-06 replace-one.
LIVE_HYPOTHESIS_BY_LANE: dict[str, str] = {
    "FAMILY_GS01_XV_PREMIUM_ATR_TP": "TILE:GS-01",
    "FAMILY_GSB1_CVD_DIV_REGIME": "TILE:B1",
    "FAMILY_GSB2_REGIME_SWITCHER": "TILE:B2",
    "FAMILY_RANDOM_CONTROL_TAKER_90": "TILE:CTRL",
    "FAMILY_GS_FADE3_TP30": "TILE:GS-FADE3",
    "FAMILY_GS_FADE3_LOOSE_TP25": "TILE:GS-FADE3-LOOSE",
}
# Opaque archive labels only (TILE_LIFECYCLE §retire step 6). Parsed for fixtures; invalid_reason rejects as RETIRED_LANE_OPAQUE.
OPAQUE_RETIRED_HYPOTHESIS_BY_LANE: dict[str, str] = {
    "FAMILY_GS06_COMMITTED_FADE_ATR_TP": "TILE:GS-06",
}
HYPOTHESIS_BY_LANE: dict[str, str] = {**LIVE_HYPOTHESIS_BY_LANE, **OPAQUE_RETIRED_HYPOTHESIS_BY_LANE}
# fill.fill_basis is NOT_INDEXED_SINCE_BOOT on most live rows (the fill-evidence index is empty after a boot; 38 of 41 closed rows on the
# 19 Oct newest page): the book pricing is then proven by fill_model + pricing.* facts + fill.evidence_status == COMPLETE.
BOOK_FILL_BASES = ("BOOK10L_TAKER_WALK", "NOT_INDEXED_SINCE_BOOT")
BOOK_EXIT_BASIS = "VWAP_ALL_LEGS"
# Every field the filter reads. A MISSING field makes the row INVALID (None where the route emits null is "present").
REQUIRED_TOP = ("status", "lane", "entry_ts", "exit_ts", "fresh_epoch_id", "identity_quarantined", "policy_signature", "trade_id")
REQUIRED_PRICING = ("fresh_eligible", "fill_valid", "legacy_pricing", "all_legs_book10l", "exit_status", "exit_price_basis",
                    "hold_seconds", "exit_pricing_basis", "exit_repriced_lag_s")  # last two: #731 fields, exposed by PR feat/tiles-repriced-fields
REQUIRED_EXIT = ("close_ts", "deploy_flatten", "forced_close")
REQUIRED_FILL = ("fill_basis", "fill_model", "evidence_status")


def hypothesis_of(row: dict) -> str | None:
    return HYPOTHESIS_BY_LANE.get(str(row.get("lane") or ""))


def net_bp_of(row: dict) -> float | None:
    p = row.get("pnl")
    return _num(p.get("bp")) if isinstance(p, dict) and p.get("bp_basis") == "net_pnl_bp" else None


def invalid_reason(row: dict) -> str | None:
    """Why a /api/monitor/tiles/trades row is NOT a valid countable trade (None = valid). 'valid' (HM 19 Oct): fresh epoch,
    book-priced (BOOK10L taker walk, VWAP exit; not FORCED_NO_BOOK / legacy BBO), not identity_quarantined, not deploy-flatten
    or forced close, not unstamped/straddle (entry_ts and exit_ts numeric). A missing field => INVALID. REPRICED_CURRENT_TIME
    exits are valid only within the 10 s cap (lag present) and are reported separately."""
    if not isinstance(row, dict):
        return "NOT_A_ROW"
    if str(row.get("status") or "").upper() != "CLOSED":
        return "NOT_CLOSED"
    pr, ex, fl = row.get("pricing"), row.get("exit"), row.get("fill")
    if not isinstance(pr, dict) or not isinstance(ex, dict) or not isinstance(fl, dict):
        return "MISSING_BLOCK"
    for blk, req, name in ((row, REQUIRED_TOP, "row"), (pr, REQUIRED_PRICING, "pricing"), (ex, REQUIRED_EXIT, "exit"),
                           (fl, REQUIRED_FILL, "fill")):
        miss = [k for k in req if k not in blk]
        if miss:
            return f"MISSING_FIELD:{name}.{miss[0]}"
    if hypothesis_of(row) is None:
        return "UNKNOWN_LANE"
    if str(row.get("lane") or "") in OPAQUE_RETIRED_HYPOTHESIS_BY_LANE:
        return "RETIRED_LANE_OPAQUE"  # R22: retired tile evidence is archive-only, never countable
    if row["identity_quarantined"] is not False:
        return "IDENTITY_QUARANTINED"
    if ex["deploy_flatten"] is not False or str(ex.get("close_origin") or "") == "DEPLOY_MAINTENANCE":
        return "DEPLOY_FLATTEN"
    if ex["forced_close"] is not False:
        return "FORCED_CLOSE"
    if _num(row["entry_ts"]) is None or _num(row["exit_ts"]) is None or _num(ex["close_ts"]) is None:
        return "UNSTAMPED_OR_STRADDLE_TS"
    if not str(row["fresh_epoch_id"] or "").strip():
        return "UNSTAMPED_FRESH_EPOCH_ID"
    if pr["fresh_eligible"] is not True:
        return "NOT_FRESH_ELIGIBLE"
    if pr["legacy_pricing"] is not False:
        return "LEGACY_PRICING"
    if pr["fill_valid"] is not True or pr["all_legs_book10l"] is not True:
        return "NOT_BOOK_PRICED"
    if fl["fill_basis"] not in BOOK_FILL_BASES or not str(fl["fill_model"] or "").startswith("BOOK10L"):
        return "LEGACY_BBO_PRICING"
    if fl["evidence_status"] != "COMPLETE":
        return "FILL_EVIDENCE_INCOMPLETE"
    if pr["exit_price_basis"] != BOOK_EXIT_BASIS or _num(pr["hold_seconds"]) is None:
        return "NOT_BOOK_PRICED_EXIT"
    st = str(pr["exit_status"] or "")
    if st == "PAPER_FILL":
        if pr["exit_pricing_basis"] == "REPRICED_CURRENT_TIME":
            lag = _num(pr["exit_repriced_lag_s"])
            if lag is None or lag > REPRICE_CAP_SEC:
                return "REPRICED_BEYOND_10S_CAP"
        return None
    return "FORCED_OR_UNPRICED_EXIT"   # PAPER_FILL_FORCED, FORCED_NO_BOOK, anything else


def valid_trades(rows: Iterable[dict]) -> dict:
    """Valid trades, one per entry (trade_id), non-overlapping holds per hypothesis; repriced exits reported separately."""
    seen: set = set()
    cand, rejected = [], {}
    for r in rows:
        why = invalid_reason(r)
        if why:
            key = why.split(":")[0] if why.startswith("MISSING_FIELD") else why
            rejected[key] = rejected.get(key, 0) + 1
            if why.startswith("MISSING_FIELD"):
                rejected.setdefault("_first_missing", why)
            continue
        if r["trade_id"] in seen:
            rejected["DUPLICATE_ENTRY"] = rejected.get("DUPLICATE_ENTRY", 0) + 1
            continue
        seen.add(r["trade_id"])
        cand.append(r)
    out, last_exit, reprice = [], {}, 0
    for r in sorted(cand, key=lambda x: float(x["entry_ts"])):
        h = hypothesis_of(r)
        if float(r["entry_ts"]) < last_exit.get(h, float("-inf")):
            rejected["OVERLAPPING_HOLD"] = rejected.get("OVERLAPPING_HOLD", 0) + 1
            continue
        last_exit[h] = float(r["exit_ts"])
        out.append(r)
        if r["pricing"]["exit_pricing_basis"] == "REPRICED_CURRENT_TIME":
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


def study_state(study: str, rows: Iterable[dict] | None = None, hypothesis_ids: tuple = ()) -> dict:
    base = {"study": study, "prereg": PREREG, "edge_claim": False, "ranking_allowed": False, "auto_tile_change": False}
    if study in STATIC_STATES:
        return {**base, "state": STATIC_STATES[study], "why": STATIC_WHY[study]}
    mn, md = STUDIES[study]
    if not hypothesis_ids:
        return {**base, "state": "PENDING_SAMPLES", "n_valid": 0, "min_n": mn, "min_days": md,
                "why": "offline replay, no live tile by design: H1-H8 are replays on the fresh-epoch streams and no live tile maps to an H-id"}
    vt = valid_trades([r for r in (rows or []) if isinstance(r, dict) and hypothesis_of(r) in hypothesis_ids])
    days = len({_day(t["exit_ts"]) for t in vt["trades"]})
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

    h = {policy_id (= the frozen policy_signature), frozen_at, sealed_at, looks, trades: [real /tiles/trades rows, each with
         control_net_bp joined by the analyzer], training:{bh_fdr_q010_passed, policies_tested}}. Training-cut results are never evidence: they only gate PASS (gate 5).
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
    other = [t for t in h["trades"] if isinstance(t, dict) and t.get("policy_signature") != h.get("policy_id")]
    if other:
        return {**base, "state": "REFUSED_NOT_SEALED", "rows_other_policy": len(other),
                "why": "the holdout is for ONE frozen policy; rows of another policy_signature are present"}
    vt = valid_trades(h["trades"])
    trades = [t for t in vt["trades"] if net_bp_of(t) is not None]
    days = len({_day(t["exit_ts"]) for t in trades})
    info = {"policy_id": h.get("policy_id"), "n_valid": len(trades), "min_n": MIN_TRADES, "distinct_days": days, "min_days": 5,
            "repriced_current_time_n": vt["repriced_current_time_n"], "rejected_by_reason": vt["rejected_by_reason"]}
    if len(trades) < MIN_TRADES or days < 5:
        return {**base, **info, "state": "PENDING_SAMPLES"}
    nets = [net_bp_of(t) for t in trades]
    hours = [int(float(t["exit_ts"]) // 3600) for t in trades]
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
