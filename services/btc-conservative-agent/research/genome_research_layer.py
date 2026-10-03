"""Genome research layer: mix-and-match, regime map, meta-policy, totals, forward tracker and the materialized API.

Called by ``genome_grid_study.run`` after the outcome matrix exists. A failure here never blocks the genome report:
it is recorded as ``research_layer.status = FAILED`` and the dashboard/self-aware checks turn RED.
"""
from __future__ import annotations

import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from research import genome_forward_tracker as ft
from research import genome_mix_match as mm
from research import research_api_cache as cache

FORWARD_DIR = "forward-tracker"


def compute(gg: Any, mirror: Path, tape: Any, episodes: list[Mapping[str, Any]], matrix: Mapping[str, Any],
            keys: list[tuple], entries: list[dict], protections: Mapping[str, Any], rows: list[Mapping[str, Any]],
            out_dir: Path, *, generation: str, code_revision: str) -> dict[str, Any]:
    started = time.time()
    out: dict[str, Any] = {"status": "OK"}
    try:
        eps = [episodes[i] for i in matrix["episode_idx"]]
        xv = mm.load_cross_mids(mirror)
        ctx = mm.episode_context(mirror, eps, tape, xv)
        g = mm.Grid.from_matrix(matrix, keys, entries, protections, gg.CHASES, gg.HEADLINE_WORLD, ctx, gg.GENOME_COHORT)
        mixes = {g.cohort: mm.mix_match(g, rows, gg.HEADLINE_WORLD)}
        grids = {g.cohort: g}
        totals = mm.policy_totals(rows, gg.HEADLINE_WORLD, g.span_days(g.all))
        out["totals"] = totals
        out["family_totals"] = mm.family_totals(totals, g)
        out["reconciliation"] = mm.matrix_reconciliation(g, totals)
        try:
            gx = mm.xvenue_grid(gg, mirror, tape, xv)
            if gx is not None:
                mixes[gx.cohort] = mm.mix_match(gx, None, gg.HEADLINE_WORLD)
                grids[gx.cohort] = gx
        except Exception as exc:  # noqa: BLE001
            out["xvenue_error"] = f"{type(exc).__name__}: {exc}"[:400]
        out["mixes"] = mixes
        root = Path(out_dir) / FORWARD_DIR
        cands = [c for mix in mixes.values() for c in ft.candidates_from(mix)]
        out["freeze"] = ft.freeze_batch(root, cands, generation=generation, code_revision=code_revision)
        out["forward"] = ft.score(root, grids)
    except Exception as exc:  # noqa: BLE001
        out = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:500],
               "trace": traceback.format_exc()[-1500:]}
    out["runtime_sec"] = round(time.time() - started, 1)
    return out


def report_block(layer: Mapping[str, Any]) -> dict[str, Any]:
    """Compact report additions (full tables live in the materialized cache)."""
    if layer.get("status") != "OK":
        return {"research_layer": {k: layer.get(k) for k in ("status", "error", "runtime_sec")}}
    fwd = layer.get("forward") or {}
    return {
        "research_layer": {"status": "OK", "runtime_sec": layer.get("runtime_sec"), "xvenue_error": layer.get("xvenue_error"),
                           "cohorts": sorted(layer["mixes"]), "api_index": "/api/research"},
        "top_100_by_oos_net": layer["totals"][:100],
        "family_totals": layer["family_totals"],
        "policy_totals_reconciliation": layer["reconciliation"],
        "mix_match": layer["mixes"],
        "forward_tracker": {k: fwd.get(k) for k in ("scored_at", "rules", "rules_sha", "chain", "candidates", "verdicts",
                                                    "batches", "proposals", "proposal_policy")} | {"freeze": layer.get("freeze")},
    }


def _with_cohort(rows: list[Mapping[str, Any]] | None, cohort: str, **extra: Any) -> list[dict[str, Any]]:
    return [dict(r) | {"cohort": cohort} | extra for r in rows or []]


def materialize(layer: Mapping[str, Any], report: Mapping[str, Any], out_dir: Path) -> dict[str, Any]:
    generation = f"{report.get('code_revision', 'unknown')[:12]}@{report.get('generated_at')}"
    ds: dict[str, list[dict[str, Any]]] = {k: [] for k in cache.DATASETS}
    live = mm.lane_totals(report.get("live_paper_by_lane") or [])
    ds["live_paper_by_lane"] = live
    ds["walk_forward"] = [{"cohort": k, "episodes": w.get("episodes"), "pooled_oos": w.get("pooled_oos"),
                           "folds": w.get("folds")} for k, w in (report.get("walk_forward_by_utc_day") or {}).items()]
    extra: dict[str, Any] = {"research_layer_status": layer.get("status"), "research_layer_error": layer.get("error")}
    if layer.get("status") == "OK":
        totals = layer["totals"]
        ds["top_100_policies"] = totals[:100]
        ds["policy_totals"] = totals
        ds["family_totals"] = layer["family_totals"]
        for cohort, mix in layer["mixes"].items():
            ds["family_table"] += _with_cohort(mix.get("family_table"), cohort)
            ds["mix_match_structures"] += _with_cohort(mix.get("structures"), cohort)
            ds["most_probable"] += [dict(m) | {"cohort": cohort, "rank": i + 1} for i, m in enumerate(mix.get("most_probable") or [])]
            ds["ingredient_verdicts"] += _with_cohort(mix.get("ingredient_verdicts"), cohort)
            ds["marginal_effects"] += _with_cohort(mix.get("marginal_effects"), cohort)
            ds["regime_map"] += _with_cohort(mix.get("regime_map"), cohort)
            ds["meta_policies"] += _with_cohort(mix.get("meta_policies"), cohort)
            ds["in_sample_top"] += _with_cohort(mix.get("in_sample_top"), cohort)
            ds["mix_match_summary"].append({"cohort": cohort, **{k: mix.get(k) for k in (
                "episodes", "policies", "utc_days", "cluster_sec", "fold_blocks_sec", "context_coverage",
                "multiple_testing", "warning", "filter_defs", "most_probable_note", "runtime_sec")}})
        fwd = layer.get("forward") or {}
        ds["forward_tracker"] = list(fwd.get("rows") or [])
        batches: dict[str, dict[str, Any]] = {}
        for r in ds["forward_tracker"]:
            b = batches.setdefault(r["batch_id"], {"batch_id": r["batch_id"], "frozen_at_utc": r.get("frozen_at_utc"),
                                                   "candidates": 0, "verdicts": Counter()})
            b["candidates"] += 1
            b["verdicts"][r["verdict"]] += 1
        ds["forward_batches"] = [b | {"verdicts": dict(b["verdicts"])} for b in batches.values()]
        ai = layer["mixes"].get("AI_DECISION") or {}
        extra |= {
            "families_expected": list(mm.FAMILY_GROUPS),
            "families_present_table": sorted({r["group"] for r in ai.get("family_table") or [] if r.get("status") == "EVALUATED"}),
            "families_present_totals": sorted({r["group"] for r in layer["family_totals"] if r.get("policy_id")}),
            "regimes_expected": list(mm.REGIMES),
            "regimes_present": sorted({r["regime"] for r in ai.get("regime_map") or []}),
            "cohorts_present": sorted(layer["mixes"]),
            "reconciliation": layer["reconciliation"],
            "forward_chain_ok": ((fwd.get("chain") or {}).get("chain_ok")),
            "forward_verdicts": fwd.get("verdicts"),
            "top_100_totals": [{k: t[k] for k in ("policy_id", "fills", "wins", "losses", "net_pnl_usd",
                                                  "net_in_sample_usd", "net_oos_usd", "holdout_verdict")} for t in totals[:100]],
        }
    extra["sort_keys"] = {
        "top_100_policies": {"key": "net_oos_usd", "order": "desc", "values": [r.get("net_oos_usd") for r in ds["top_100_policies"]]},
        "live_paper_by_lane": {"key": "net_pnl_usd", "order": "desc", "values": [r.get("net_pnl_usd") for r in live]},
        "family_totals": {"key": "net_oos_usd", "order": "desc",
                          "values": [r.get("net_oos_usd") for r in ds["family_totals"] if r.get("net_oos_usd") is not None]},
    }
    return cache.materialize(out_dir, ds, generation=generation, generated_at=str(report.get("generated_at")),
                             summary_extra=extra)