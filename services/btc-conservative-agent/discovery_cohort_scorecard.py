"""Strict episode-matched discovery scorecards across separate evidence worlds."""
from __future__ import annotations

import math
import re
from collections import Counter
from itertools import combinations
from typing import Any, Iterable, Mapping

WORLDS = ("OBSERVED_PAPER", "IDEAL_TOUCH", "CONSERVATIVE_BBO")
SCORECARD_SCHEMA = "adx_offset_chase_exit_scorecard_v2"
_WORLD_ALIASES = {
    "OBSERVED_PAPER": "OBSERVED_PAPER", "PAPER": "OBSERVED_PAPER",
    "PAPER_EXECUTED": "OBSERVED_PAPER", "ACTUAL_PAPER": "OBSERVED_PAPER",
    "IDEAL_TOUCH": "IDEAL_TOUCH", "IDEAL_TOUCH_DIAGNOSTIC": "IDEAL_TOUCH",
    "IDEAL_TOUCH_DIAGNOSTIC_ONLY": "IDEAL_TOUCH",
    "CONSERVATIVE_BBO": "CONSERVATIVE_BBO",
    "CONSERVATIVE_BBO_DEPTH": "CONSERVATIVE_BBO",
    "CONSERVATIVE_BBO_DEPTH_V1": "CONSERVATIVE_BBO",
    "CONSERVATIVE_BBO_DEPTH_TAPE": "CONSERVATIVE_BBO",
}
_REQUIRED_SCALAR_IDENTITIES = {
    "epoch": ("epoch_id", "dataset_epoch"),
    "opportunity_id": ("opportunity_id",),
    "source_revision": ("source_revision",),
    "deployed_revision": ("deployed_revision",),
    "direction": ("direction", "side"),
    "policy_id": ("policy_id", "baseline_id"),
    "policy_signature": ("policy_signature",),
    "schedule_sha256": ("schedule_sha256",),
    "quantity": ("original_requested_qty", "requested_qty"),
    "tile_config": ("tile_config_signature",), "config": ("config_signature",),
    "cost_model": ("cost_model_id", "cost_model"),
    "simulation_model": ("simulation_model", "fill_model", "execution_model", "execution_world"),
}
_OPTIONAL_SCALAR_IDENTITIES = {"schedule_id": ("schedule_id",),
    "economics_evidence_basis": ("economics_evidence_basis",),
    "declared_contract_sha256": ("declared_contract_sha256",)}
_SEQUENCE_IDENTITIES = {
    "tape_hashes": ("tape_sha256", "tape_hashes", "market_segment_sha256", "market_segment_hashes"),
    "tape_ids": ("tape_id", "tape_ids", "market_segment_id", "market_segment_ids"),
}
_IDENTITY_KEYS = tuple(_REQUIRED_SCALAR_IDENTITIES) + tuple(_OPTIONAL_SCALAR_IDENTITIES) + tuple(_SEQUENCE_IDENTITIES)
_SENTINELS = {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "NOT_AVAILABLE", "MISSING", "NAN", "INF", "INFINITY"}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: result = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return result if math.isfinite(result) else None


def _identity(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    result, provenance, blockers = {}, {}, []
    for name, aliases in {**_REQUIRED_SCALAR_IDENTITIES, **_OPTIONAL_SCALAR_IDENTITIES}.items():
        supplied = [(alias, row.get(alias)) for alias in aliases if row.get(alias) not in (None, "")]
        if not supplied:
            if name in _REQUIRED_SCALAR_IDENTITIES: blockers.append(f"MISSING_IDENTITY:{name}")
            continue
        values = []
        for alias, raw in supplied:
            value = _finite(raw) if name == "quantity" else str(raw).strip()
            if (isinstance(raw, bool) or isinstance(raw, (Mapping, list, tuple, set))
                    or value is None or value == ""
                    or (isinstance(value, str) and value.upper() in _SENTINELS)
                    or (name == "quantity" and value <= 0)):
                blockers.append(f"INVALID_IDENTITY:{name}"); continue
            values.append(value); provenance[alias] = str(raw)
        if not values: continue
        if any(value != values[0] for value in values[1:]):
            blockers.append(f"CONFLICTING_IDENTITY_ALIASES:{name}"); continue
        result[name] = values[0]
    if "declared_contract_sha256" in result and not re.fullmatch(r"[0-9a-f]{64}", result["declared_contract_sha256"]):
        blockers.append("INVALID_IDENTITY:declared_contract_sha256")
    if result.get("economics_evidence_basis") == "DECLARED_SIMULATION" and "declared_contract_sha256" not in result:
        blockers.append("MISSING_IDENTITY:declared_contract_sha256")
    for name, aliases in _SEQUENCE_IDENTITIES.items():
        values = []
        for alias in aliases:
            raw = row.get(alias)
            if raw in (None, ""): continue
            raw_values = raw if isinstance(raw, (list, tuple, set)) else [raw]
            for item in raw_values:
                value = str(item).strip() if not isinstance(item, (Mapping, list, tuple, set, bool)) else ""
                if not value or value.upper() in _SENTINELS:
                    blockers.append(f"INVALID_IDENTITY:{name}"); continue
                values.append(value); provenance.setdefault(alias, []).append(value)
        if not values:
            blockers.append(f"MISSING_IDENTITY:{name}")
        else:
            result[name] = tuple(sorted(set(values)))
    result["original_fields"] = provenance
    return result, sorted(set(blockers))


def build_episode_matched_scorecard(rows: Iterable[Mapping[str, Any]], *, axes=("adx_bucket", "offset_pct", "chase_policy", "exit_family")) -> dict[str, Any]:
    grouped = {world: {} for world in WORLDS}; blockers = []
    for source in rows:
        row = dict(source); original = str(row.get("evidence_world") or row.get("world") or "").strip().upper()
        world = _WORLD_ALIASES.get(original)
        if not world: blockers.append(f"UNKNOWN_EVIDENCE_WORLD:{original or 'MISSING'}"); continue
        episode = str(row.get("episode_id") or "").strip()
        if not episode: blockers.append("MISSING_EPISODE_ID"); continue
        cell_key = tuple(row.get(axis) for axis in axes)
        malformed_axis = any(
            value in (None, "") or isinstance(value, (Mapping, list, tuple, set, bool))
            or (isinstance(value, float) and not math.isfinite(value))
            for value in cell_key
        )
        if malformed_axis: blockers.append(f"INCOMPLETE_OR_INVALID_CELL:{episode}"); continue
        identity, defects = _identity(row); pnl = _finite(row.get("net_pnl_usd"))
        group_key = (cell_key, identity.get("policy_id"), identity.get("policy_signature"))
        item = {"episode_id": episode, "cell": dict(zip(axes, cell_key)), "net_pnl_usd": pnl,
                "identity": identity, "identity_blockers": sorted(set(defects)),
                "pnl_status": "PROVIDED_IN_EVIDENCE_WORLD" if pnl is not None else "UNKNOWN_MISSING_OR_NONFINITE",
                "world_provenance": {"original": original, "normalized": world}}
        grouped[world].setdefault(group_key, {}).setdefault(episode, []).append(item)

    world_reports = {}
    for world, cells in grouped.items():
        reports, all_episodes, aliases = [], set(), Counter()
        for group_key, episodes in cells.items():
            cell_key, policy_id, policy_signature = group_key
            all_episodes.update(episodes); duplicates = sorted(e for e, variants in episodes.items() if len(variants) != 1)
            aliases.update(item["world_provenance"]["original"] for variants in episodes.values() for item in variants)
            blockers.extend(f"DUPLICATE_WORLD_EPISODE:{world}:{e}" for e in duplicates)
            unique = [variants[0] for variants in episodes.values() if len(variants) == 1]
            pnls = [item["net_pnl_usd"] for item in unique if item["net_pnl_usd"] is not None]
            identity_blocker_counts = Counter(
                defect for item in unique for defect in item["identity_blockers"]
            )
            all_items = [item for variants in episodes.values() for item in variants]
            model_keys = {tuple(item["identity"].get(field) for field in (
                "cost_model", "simulation_model", "economics_evidence_basis", "declared_contract_sha256"))
                for item in all_items}
            model_mismatch = len(model_keys) > 1
            bases = {item["identity"].get("economics_evidence_basis") for item in all_items}
            contracts = {item["identity"].get("declared_contract_sha256") for item in all_items}
            warnings = []
            if "DECLARED_SIMULATION" in bases:
                warnings.append("DECLARED_SIMULATION_NOT_OBSERVED_ECONOMICS")
            if None in bases:
                warnings.append("ECONOMICS_EVIDENCE_BASIS_UNSPECIFIED")
            if model_mismatch:
                warnings.append("MODEL_MISMATCH")
                identity_blocker_counts["MODEL_MISMATCH"] += len(all_items)
                blockers.append(f"MODEL_MISMATCH:{world}:{policy_id}:{policy_signature}")
            reports.append({"cell": dict(zip(axes, cell_key)), "policy_id": policy_id,
                            "policy_signature": policy_signature, "independent_episode_count": len(episodes),
                            "comparable_episode_count": 0 if model_mismatch else sum(not item["identity_blockers"] for item in unique),
                            "economics_evidence_basis": next(iter(bases)) or "UNSPECIFIED" if len(bases) == 1 else "MIXED",
                            "declared_contract_sha256": next(iter(contracts)) if len(contracts) == 1 else None,
                            "economics_model_comparable": not model_mismatch,
                            "warnings": warnings,
                            "pnl_observed_episode_count": len(pnls), "missing_or_nonfinite_pnl_count": len(unique)-len(pnls),
                            "duplicate_episode_ids": duplicates, "episodes": sorted(episodes),
                            "identity_blocker_counts": dict(sorted(identity_blocker_counts.items())),
                            "complete_pnl_evidence": bool(unique) and not duplicates
                            and not identity_blocker_counts and len(pnls) == len(unique),
                            "net_pnl_usd_sum": sum(pnls) if pnls and not model_mismatch else None,
                            "mean_net_pnl_usd": sum(pnls)/len(pnls) if pnls and not model_mismatch else None})
        reports.sort(key=lambda x: (x["mean_net_pnl_usd"] is None, -(x["mean_net_pnl_usd"] or 0), -x["independent_episode_count"]))
        eligible = [cell for cell in reports if cell["complete_pnl_evidence"]]
        descriptive_leader = None
        if eligible:
            leader = eligible[0]
            descriptive_leader = {
                key: leader[key] for key in (
                    "policy_id", "policy_signature", "cell", "independent_episode_count",
                    "net_pnl_usd_sum", "mean_net_pnl_usd",
                    "economics_evidence_basis", "declared_contract_sha256", "warnings",
                )
            }
            descriptive_leader.update({
                "tier": "DESCRIPTIVE_ONLY",
                "ranking_basis": "mean net PnL within this evidence world; policy cohorts may differ",
                "positive_mean": leader["mean_net_pnl_usd"] > 0,
                "out_of_sample_qualified": False,
            })
        world_reports[world] = {"status": "AVAILABLE" if all_episodes else "EMPTY",
                                "independent_episode_count": len(all_episodes), "cells": reports,
                                "complete_pnl_cell_count": len(eligible),
                                "profitability_evidence_available": bool(eligible),
                                "descriptive_leader": descriptive_leader,
                                "world_alias_provenance": dict(sorted(aliases.items())),
                                "claim_label": "separate descriptive evidence; no cross-world equivalence implied"}

    comparisons = []
    for group_key in sorted({key for cells in grouped.values() for key in cells}, key=str):
        cell_key, policy_id, policy_signature = group_key
        for left_world, right_world in combinations(WORLDS, 2):
            left, right = grouped[left_world].get(group_key, {}), grouped[right_world].get(group_key, {})
            left_ids, right_ids = set(left), set(right); intersection = sorted(left_ids & right_ids)
            matched, deltas, defects = 0, [], []
            mixed_models = any(len({tuple(item["identity"].get(field) for field in (
                "cost_model", "simulation_model", "economics_evidence_basis", "declared_contract_sha256"))
                for variants in side.values() for item in variants}) > 1 for side in (left, right))
            for episode in intersection:
                if len(left[episode]) != 1 or len(right[episode]) != 1:
                    defects.append(f"DUPLICATE_MATCH:{episode}"); continue
                a, b = left[episode][0], right[episode][0]
                if a["identity_blockers"] or b["identity_blockers"]:
                    defects.append(f"INCOMPLETE_IDENTITY:{episode}"); continue
                ai = {k:v for k,v in a["identity"].items() if k != "original_fields"}; bi = {k:v for k,v in b["identity"].items() if k != "original_fields"}
                mismatches = sorted(k for k in _IDENTITY_KEYS if ai.get(k) != bi.get(k))
                if mismatches:
                    prefix = "MODEL_DIFFERENCE_CALIBRATION_ONLY" if any(k in mismatches for k in (
                        "simulation_model", "cost_model", "economics_evidence_basis", "declared_contract_sha256")) else "IDENTITY_MISMATCH"
                    defects.append(f"{prefix}:{episode}:{','.join(mismatches)}"); continue
                matched += 1
                if a["net_pnl_usd"] is not None and b["net_pnl_usd"] is not None: deltas.append(b["net_pnl_usd"]-a["net_pnl_usd"])
            if mixed_models:
                matched, deltas = 0, []
                defects.append("MODEL_DIFFERENCE_CALIBRATION_ONLY:CELL_MODEL_MISMATCH")
            equal = bool(intersection) and matched == len(intersection) and left_ids == right_ids
            comparisons.append({"cell": dict(zip(axes, cell_key)), "policy_id": policy_id,
                                "policy_signature": policy_signature, "left_world": left_world, "right_world": right_world,
                                "left_independent_episodes": len(left_ids), "right_independent_episodes": len(right_ids),
                                "episode_intersection_count": len(intersection), "left_only_episode_ids": sorted(left_ids-right_ids),
                                "right_only_episode_ids": sorted(right_ids-left_ids), "exact_identity_match_count": matched,
                                "cohort_equality_proven": equal,
                                "complete_matched_pnl_evidence": equal and len(deltas) == matched,
                                "comparison_status": "EXACT_MATCHED_COHORT" if equal else "CALIBRATION_ONLY_MODEL_DIFFERENCE" if any(x.startswith("MODEL_DIFFERENCE_CALIBRATION_ONLY") for x in defects) else "NOT_COMPARABLE",
                                "matched_pnl_delta_count": len(deltas), "mean_right_minus_left_pnl_usd": sum(deltas)/len(deltas) if deltas else None,
                                "blockers": sorted(set(defects))})
    return {"schema": SCORECARD_SCHEMA, "axes": list(axes), "worlds": world_reports, "matched_comparisons": comparisons,
            "warnings": sorted({warning for world in world_reports.values() for cell in world["cells"] for warning in cell["warnings"]}),
            "pnl_sum_across_worlds": False, "discovery_shadow_equals_paper": False, "blockers": sorted(set(blockers)),
            "status": "BUILT" if any(world_reports[w]["independent_episode_count"] for w in WORLDS) else "UNKNOWN",
            "relay_eligible": False, "live_policy_change_allowed": False, "upstream_input_wiring_complete": False}


def build_disk_episode_matched_scorecard(rows, *, axes=("adx_bucket", "offset_pct", "chase_policy", "exit_family"),
                                       max_output_bytes=32 * 1024 * 1024,
                                       index_max_bytes=None):
    """Exact legacy semantics per policy/cell, bounded by explicit publication budgets.

No input rows are dropped. If even one cell or the complete output exceeds the
budget, publication fails closed instead of promoting a partial scorecard.
"""
    from research.shadow_result_stream import DiskRows, DEFAULT_INDEX_BYTES
    from research.policy_evidence_schema import canonical_json
    with DiskRows(max_bytes=DEFAULT_INDEX_BYTES if index_max_bytes is None else index_max_bytes) as index:
        index.db.execute("create table world_order(world text, key text, first_seq integer, primary key(world,key))")
        for row in rows:
            identity, _ = _identity(row)
            key = canonical_json([*[row.get(axis) for axis in axes], identity.get("policy_id"),
                                  identity.get("policy_signature")])
            index.append(row, key)
            original = str(row.get("evidence_world") or row.get("world") or "").strip().upper()
            world = _WORLD_ALIASES.get(original)
            if world:
                index.db.execute("insert or ignore into world_order values(?,?,?)", (world, key, index.count))
        result = build_episode_matched_scorecard([], axes=axes)
        index.db.execute("create table episodes(world text, episode text, primary key(world,episode))")
        blockers = set()
        used = 0
        for _, sources in index.groups(insertion_order=True):
            part = build_episode_matched_scorecard(sources, axes=axes)
            used += len(canonical_json(part).encode())
            if used > max_output_bytes:
                raise ValueError("DISCOVERY_FULL_OUTPUT_BUDGET_EXCEEDED")
            blockers.update(part["blockers"])
            result["matched_comparisons"].extend(part["matched_comparisons"])
            for world in WORLDS:
                target, source = result["worlds"][world], part["worlds"][world]
                target["cells"].extend(source["cells"])
                aliases = Counter(target["world_alias_provenance"])
                aliases.update(source["world_alias_provenance"])
                target["world_alias_provenance"] = dict(sorted(aliases.items()))
                for cell in source["cells"]:
                    index.db.executemany("insert or ignore into episodes values(?,?)",
                                         ((world, episode) for episode in cell["episodes"]))
        for world in WORLDS:
            target = result["worlds"][world]
            target["independent_episode_count"] = index.db.execute(
                "select count(*) from episodes where world=?", (world,)).fetchone()[0]
            target["status"] = "AVAILABLE" if target["independent_episode_count"] else "EMPTY"
            target["cells"].sort(key=lambda x: (x["mean_net_pnl_usd"] is None,
                -(x["mean_net_pnl_usd"] or 0), -x["independent_episode_count"],
                index.db.execute("select first_seq from world_order where world=? and key=?",
                    (world, canonical_json([*[x["cell"].get(axis) for axis in axes], x["policy_id"],
                                             x["policy_signature"]]))).fetchone()[0]))
            eligible = [cell for cell in target["cells"] if cell["complete_pnl_evidence"]]
            target["complete_pnl_cell_count"] = len(eligible)
            target["profitability_evidence_available"] = bool(eligible)
            if eligible:
                leader = eligible[0]
                target["descriptive_leader"] = {key: leader[key] for key in (
                    "policy_id", "policy_signature", "cell", "independent_episode_count",
                    "net_pnl_usd_sum", "mean_net_pnl_usd", "economics_evidence_basis",
                    "declared_contract_sha256", "warnings")}
                target["descriptive_leader"].update({"tier": "DESCRIPTIVE_ONLY",
                    "ranking_basis": "mean net PnL within this evidence world; policy cohorts may differ",
                    "positive_mean": leader["mean_net_pnl_usd"] > 0, "out_of_sample_qualified": False})
        result["blockers"] = sorted(blockers)
        result["warnings"] = sorted({warning for world in result["worlds"].values()
                                     for cell in world["cells"] for warning in cell["warnings"]})
        pair_order = {pair: i for i, pair in enumerate(combinations(WORLDS, 2))}
        result["matched_comparisons"].sort(key=lambda item: (
            str((tuple(item["cell"].get(axis) for axis in axes), item["policy_id"], item["policy_signature"])),
            pair_order[(item["left_world"], item["right_world"])]))
        result["status"] = "BUILT" if any(result["worlds"][w]["independent_episode_count"] for w in WORLDS) else "UNKNOWN"
        return result
