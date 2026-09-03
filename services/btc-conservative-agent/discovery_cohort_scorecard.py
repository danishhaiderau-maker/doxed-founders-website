"""Episode-matched discovery scorecard across three evidence worlds.

Worlds stay labeled and are never summed. Fail closed when joins are incomplete.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

WORLDS = ("OBSERVED_PAPER", "IDEAL_TOUCH", "CONSERVATIVE_BBO")
SCORECARD_SCHEMA = "adx_offset_chase_exit_scorecard_v1"


def build_episode_matched_scorecard(
    rows: Iterable[Mapping[str, Any]],
    *,
    axes: tuple[str, ...] = ("adx_bucket", "offset_pct", "chase_policy", "exit_family"),
) -> dict[str, Any]:
    """Build ADX×offset×chase×exit scorecards per world; never merge PnL."""
    by_world: dict[str, list[dict[str, Any]]] = {w: [] for w in WORLDS}
    blockers: list[str] = []
    for row in rows:
        world = str(row.get("evidence_world") or row.get("world") or "").upper()
        if world not in by_world:
            blockers.append(f"UNKNOWN_EVIDENCE_WORLD:{world or 'MISSING'}")
            continue
        episode_id = str(row.get("episode_id") or "").strip()
        if not episode_id:
            blockers.append("MISSING_EPISODE_ID")
            continue
        cell = {axis: row.get(axis) for axis in axes}
        if any(cell[a] in (None, "") for a in axes):
            blockers.append(f"INCOMPLETE_CELL:{episode_id}")
            continue
        by_world[world].append({
            "episode_id": episode_id,
            "cell": cell,
            "net_pnl_usd": row.get("net_pnl_usd"),
            "outcome_state": row.get("outcome_state"),
        })
    world_reports = {}
    for world, items in by_world.items():
        cells: dict[tuple, dict[str, Any]] = {}
        for item in items:
            key = tuple(item["cell"].get(a) for a in axes)
            slot = cells.setdefault(key, {
                "cell": item["cell"], "n": 0, "net_pnl_usd_sum": 0.0,
                "missing_pnl": 0, "episodes": [],
            })
            slot["n"] += 1
            slot["episodes"].append(item["episode_id"])
            if item["net_pnl_usd"] is None:
                slot["missing_pnl"] += 1
            else:
                slot["net_pnl_usd_sum"] += float(item["net_pnl_usd"])
        world_reports[world] = {
            "status": "AVAILABLE" if items else "EMPTY",
            "episode_count": len(items),
            "cells": sorted(
                (
                    {
                        **slot,
                        "mean_net_pnl_usd": (
                            None if slot["n"] == slot["missing_pnl"]
                            else slot["net_pnl_usd_sum"] / max(1, slot["n"] - slot["missing_pnl"])
                        ),
                    }
                    for slot in cells.values()
                ),
                key=lambda s: (-(s["mean_net_pnl_usd"] or float("-inf")), -s["n"]),
            ),
            "claim_label": (
                "OBSERVED paper fills only" if world == "OBSERVED_PAPER"
                else "IDEAL_TOUCH diagnostic only — not qualification"
                if world == "IDEAL_TOUCH"
                else "CONSERVATIVE_BBO sim — primary discovery ranking when complete"
            ),
        }
    return {
        "schema": SCORECARD_SCHEMA,
        "axes": list(axes),
        "worlds": world_reports,
        "pnl_sum_across_worlds": False,
        "blockers": sorted(set(blockers)),
        "status": "UNKNOWN" if blockers and not any(
            world_reports[w]["episode_count"] for w in WORLDS
        ) else "BUILT",
        "relay_eligible": False,
        "live_policy_change_allowed": False,
    }
