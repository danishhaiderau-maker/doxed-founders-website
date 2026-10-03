"""Indicator Edge combination grid (week 2 onward) and tile-package proposals (laptop analyzer).

GATED until the feature set has >= 7 scored days and at least one PROMISING trigger. Then each combination is
[1 filter: PROMISING A/D feature or a G regime cell] + [1 PROMISING trigger from B/C/E/F] + [optional PROMISING
C confirmation], 2 settings per part (<= 8 variants per combination, <= 64 per trigger family). New variants are
frozen in the indicator-edge pre-registration chain and scored only on bars decided after their own freeze, with
BH FDR across every frozen variant. The two tile-filter variants score the AI calls gated by the best trend
feature. A combination still PROMISING after its own forward week yields a tile-package PROPOSAL file; nothing
here edits the tile registry, creates a tile, or touches orders or the relay.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

import indicator_edge_spec as spec
from research import indicator_edge_prereg as prereg
from research import indicator_forward_scorer as sc

GRID = spec.COMBINATION_GRID
PROPOSAL_SCHEMA = "indicator_edge_tile_proposal_v1"
PROPOSAL_DIR = "tile-proposals"
STRONG_VALUE = 30.0          # oriented pct >= 80 (or <= 20 for the opposite side)
REGIME_FILTERS = {
    "trend_state": (("TREND", "WEAK"), ("RANGE",)),
    "vol_tercile": (("HIGH",), ("LOW", "MID")),
}
FILTER_FAMILIES = {f for f in GRID["filter_families"] if f != "G_REGIME"}
TRIGGER_FAMILIES = set(GRID["trigger_families"])
CONFIRM_FAMILIES = set(GRID["confirmation_families"])
AI_TILE_FILTERS = ("AI_COMMITTED_FADE_TREND_AGREES", "AI_NO_TRADE_FOLLOW_TREND_AGREES")


def combo_id(definition: Mapping[str, Any]) -> str:
    return "IEC-" + hashlib.sha256(spec.canonical_json(definition).encode()).hexdigest()[:12]


def gate(report: Mapping[str, Any]) -> tuple[bool, str]:
    if report.get("status") != "OK":
        return False, f"scorer status {report.get('status')}"
    days = int(report.get("scored_days") or 0)
    if days < spec.SCORING_RULES["min_days_promising"]:
        return False, f"week 2 only: {days} scored day(s) < {spec.SCORING_RULES['min_days_promising']}"
    triggers = [f for f in report.get("features") or [] if f["label"] == "PROMISING" and f["family"] in TRIGGER_FAMILIES]
    if not triggers:
        return False, "no PROMISING trigger feature yet"
    return True, f"{len(triggers)} PROMISING trigger(s)"


def candidate_variants(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """All grid variants implied by the current PROMISING set (deterministic order, capped per family)."""
    feats = [f for f in report.get("features") or [] if f["label"] == "PROMISING" and f.get("independent", True)]
    filters = [("feature", f["feature"]) for f in feats if f["family"] in FILTER_FAMILIES]
    filters += [("regime", k) for k in REGIME_FILTERS]
    triggers = [f for f in feats if f["family"] in TRIGGER_FAMILIES]
    confirms = [f["feature"] for f in feats if f["family"] in CONFIRM_FAMILIES]
    out, per_family = [], {}
    for trig in triggers:
        for kind, filt in filters:
            if kind == "feature" and spec.indicator_of(filt)["id"] == trig["indicator"]:
                continue
            conf_opts = [None] + [c for c in confirms if spec.indicator_of(c)["id"] != trig["indicator"]][:1]
            for f_set in (0, 1):
                for t_set in ("signal", "strong"):
                    for conf in conf_opts:
                        fam = trig["family"]
                        if per_family.get(fam, 0) >= GRID["max_variants_per_family"]:
                            break
                        variant = {"trigger": trig["feature"], "trigger_setting": t_set, "filter_kind": kind,
                                   "filter": filt, "filter_setting": f_set, "confirmation": conf,
                                   "window_min": trig["best_window_min"]}
                        out.append({"combo_id": combo_id(variant), "family": fam, "variant": variant})
                        per_family[fam] = per_family.get(fam, 0) + 1
    best_trend = next((f for f in feats if f["family"] == spec.FAMILY_TREND), None)
    if best_trend:
        for name in AI_TILE_FILTERS:
            variant = {"tile_filter": name, "trend_feature": best_trend["feature"],
                       "window_min": best_trend["best_window_min"]}
            out.append({"combo_id": combo_id(variant), "family": "TILE_FILTER", "variant": variant})
    return out


def variant_side(variant: Mapping[str, Any], ctx: Mapping[str, Any]) -> np.ndarray:
    """Per-row side (+1/-1, 0 = no signal) of one frozen variant on the scorer context."""
    fids, arr, rows = ctx["fids"], ctx["arrays"], ctx["rows"]
    col = {f: i for i, f in enumerate(fids)}
    n = len(rows)
    if "tile_filter" in variant:
        tf = variant["trend_feature"]
        trend = arr["side"][:, col[tf]] if tf in col else np.full(n, np.nan)
        cls = np.array([(r.get("regime") or {}).get("ai_class") for r in rows], dtype=object)
        ai = np.array([{"LONG": 1.0, "SHORT": -1.0}.get((r.get("regime") or {}).get("ai_side"), 0.0) for r in rows])
        if variant["tile_filter"] == "AI_COMMITTED_FADE_TREND_AGREES":
            base = np.where(np.isin(cls, ["COMMITTED", "COMMITTED_SCORE_CONFLICT"]), -ai, 0.0)
        else:
            base = np.where(cls == "NO_TRADE_SCORE_LED", ai, 0.0)
        return np.where((base != 0) & (np.nan_to_num(trend) == base), base, 0.0)
    trig = variant["trigger"]
    if trig not in col:
        return np.zeros(n)
    side = np.nan_to_num(arr["side"][:, col[trig]])
    if variant["trigger_setting"] == "strong":
        val = np.nan_to_num(arr["value"][:, col[trig]])
        side = np.where(np.abs(val) >= STRONG_VALUE, side, 0.0)
    if variant["filter_kind"] == "feature":
        fc = col.get(variant["filter"])
        if fc is None:
            return np.zeros(n)
        if variant["filter_setting"] == 0:
            ok = np.nan_to_num(arr["side"][:, fc]) == side
        else:
            ok = np.nan_to_num(arr["value"][:, fc]) * side >= STRONG_VALUE
    else:
        allowed = REGIME_FILTERS[variant["filter"]][variant["filter_setting"]]
        labels = np.array([(r.get("regime") or {}).get(variant["filter"]) for r in rows], dtype=object)
        ok = np.isin(labels, list(allowed))
    if variant.get("confirmation"):
        cc = col.get(variant["confirmation"])
        if cc is None:
            return np.zeros(n)
        ok = ok & (np.nan_to_num(arr["side"][:, cc]) == side)
    return np.where(ok & (side != 0), side, 0.0)


def _frozen_combos(prereg_root: Path) -> list[dict[str, Any]]:
    rows, chain = prereg.load_chain(Path(prereg_root) / prereg.FROZEN_FILE)
    if not chain["chain_ok"]:
        return []
    return [r for r in rows if r.get("kind") == prereg.KIND_COMBINATION]


def score_frozen(frozen: Sequence[Mapping[str, Any]], ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    out, trials = [], []
    dts, days, regimes = ctx["dts"], ctx["days"], ctx["regimes"]
    for rec in frozen:
        v = rec["variant"]
        j = sc.WINDOWS.index(int(v["window_min"])) if int(v["window_min"]) in sc.WINDOWS else 1
        side = variant_side(v, ctx)
        side = np.where(dts > float(rec["frozen_at"]), side, 0.0)
        side = np.where(side != 0, side, np.nan)
        t = sc.trial_stats(side, np.full(len(side), np.nan), np.full(len(side), np.nan), 1.0, j,
                           ctx["outcomes"], dts, days, regimes)
        trials.append(t)
        out.append({"prereg_id": rec["prereg_id"], "combo_id": rec["combo_id"], "family": rec["family"],
                    "variant": v, "frozen_at_utc": rec["frozen_at_utc"],
                    "forward_days": len(t["daily"]), "stats": t})
    for row, q in zip(out, sc.bh_adjust([t["p_value"] for t in trials])):
        row["stats"]["q_value"] = q
        row["label"], row["label_reasons"] = sc.label_trial(row["stats"])
        row["tile_package_eligible"] = (row["label"] == "PROMISING"
                                        and row["forward_days"] >= GRID["forward_days"])
    out.sort(key=lambda r: (-sc.LABEL_RANK[r["label"]], r["stats"]["q_value"], r["combo_id"]))
    return out


def tile_package_proposal(row: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    rules = spec.TILE_PACKAGE_RULES
    return {"schema": PROPOSAL_SCHEMA, "status": "PROPOSAL_ONLY_NOT_REGISTERED", "combo_id": row["combo_id"],
            "prereg_id": row["prereg_id"], "variant": row["variant"], "forward_days": row["forward_days"],
            "evidence": {k: row["stats"].get(k) for k in ("signals", "hit_rate", "mean_net_bp", "mean_net_bp_9s",
                                                          "q_value", "rank_ic", "mfe_bp", "mae_bp")},
            "tile_package_rules": rules, "feature_set_sha": report.get("feature_set_sha"),
            "next_step": "Danish reviews; a separate post-freeze registry PR adds it paper-only, relay-ineligible, "
                         "default OFF with pre-registered promote/kill rules. This file never edits the registry."}


def evaluate(report: Mapping[str, Any], ctx: Mapping[str, Any], *, prereg_root: Path, out_dir: str,
             now: float | None = None, freeze: bool = True) -> dict[str, Any]:
    now = float(now if now is not None else time.time())
    ok, reason = gate(report)
    frozen = _frozen_combos(prereg_root)
    result: dict[str, Any] = {"grid_id": GRID["id"], "status": "ACTIVE" if ok else "GATED", "gate_reason": reason,
                              "shape": GRID["shape"], "frozen_variants": len(frozen), "newly_frozen": 0,
                              "rows": [], "tile_proposals": []}
    if ok and freeze:
        known = {r["combo_id"] for r in frozen}
        new = [c for c in candidate_variants(report) if c["combo_id"] not in known]
        if new:
            res = prereg.freeze_combinations(prereg_root, new, now=now)
            result["newly_frozen"] = res.get("frozen", 0)
            frozen = _frozen_combos(prereg_root)
            result["frozen_variants"] = len(frozen)
    if frozen and ctx.get("rows"):
        rows = score_frozen(frozen, ctx)
        result["rows"] = rows
        out = Path(out_dir) / PROPOSAL_DIR
        for row in rows:
            if row["tile_package_eligible"]:
                out.mkdir(parents=True, exist_ok=True)
                path = out / f"{row['combo_id']}.json"
                if not path.exists():
                    path.write_text(json.dumps(tile_package_proposal(row, report), indent=1, default=str),
                                    encoding="utf-8")
                result["tile_proposals"].append(str(path))
    return result
