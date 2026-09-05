"""Pure, bounded bridge from same-publication evaluated rows to dynamic research.

The caller supplies discovery-normalized identity/economics fields, the exact
``generation``, ``signal_ts``, ``required_end_ts``, timestamped
``pre_entry_features``, ``bucket_definition_signature``, ``outcome_state`` and
strict ``terminal_complete``. These must come from verified source receipts;
this module never infers observation times, completes costs, or seals holdouts.
Unknown rows are counted and, when their causal identity is valid, retained as
episodes without a policy outcome. No missing outcome becomes a zero return.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from research_v3_validation import SUPPORTED_TERMINAL_STATES


GENERATION_FIELDS = (
    "manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
    "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key",
)
WORLDS = {"OBSERVED_PAPER", "CONSERVATIVE_BBO", "IDEAL_TOUCH"}
MISSING = {"", "UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "UNSUPPORTED"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() not in MISSING


def _number(value: Any, *, positive: bool = False) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (not positive or value > 0))


def adapt_dynamic_cohorts(
    rows: Iterable[Mapping[str, Any]], *, expected_generation: Mapping[str, Any],
    feature_names: Iterable[str], protocol: Mapping[str, Any],
    max_rows: int = 100_000, max_episodes: int = 20_000,
    max_groups: int = 1_024, max_candidates: int = 4_096,
) -> dict[str, Any]:
    """Return isolated cohorts consumable by research_dynamic_entry_policy.

    Declared-contract + declared USD margin groups tolerate naturally different
    requested quantities across opportunities. Without that explicit contract,
    exact quantity is the conservative fallback (reported, not normalized).
    Within one opportunity candidates must still agree on requested quantity.
    Limits raise, discarding the entire result; they never silently truncate.
    """
    for limit in (max_rows, max_episodes, max_groups, max_candidates):
        if type(limit) is not int or limit <= 0:
            raise ValueError("INVALID_ADAPTER_LIMIT")
    generation = dict(expected_generation)
    if any(not _text(generation.get(key)) for key in GENERATION_FIELDS):
        raise ValueError("EXPECTED_GENERATION_INCOMPLETE")
    names = tuple(feature_names)
    if not names or len(set(names)) != len(names) or any(not _text(n) for n in names):
        raise ValueError("INVALID_FEATURE_NAMES")
    protocol_hash = _hash(dict(protocol))
    groups: dict[str, dict[str, Any]] = {}
    rejections: Counter[str] = Counter()
    counts: Counter[str] = Counter({key: 0 for key in (
        "input_rows", "unknown_outcome_rows", "exact_quantity_fallback_rows",
        "duplicate_rows", "supported_outcomes", "output_episodes",
    )})
    total_episodes = 0
    candidate_keys: set[tuple[str, str]] = set()

    for source in rows:
        counts["input_rows"] += 1
        if counts["input_rows"] > max_rows:
            raise ValueError("DYNAMIC_ADAPTER_ROW_LIMIT")
        if not isinstance(source, Mapping):
            rejections["ROW_NOT_MAPPING"] += 1
            continue
        row = dict(source)
        state = str(row.get("outcome_state") or "UNKNOWN").upper()
        if state not in SUPPORTED_TERMINAL_STATES:
            counts["unknown_outcome_rows"] += 1
        reason = None
        if row.get("generation") != generation or any(
            key in row and row[key] != generation[key]
            for key in GENERATION_FIELDS
        ):
            reason = "GENERATION_MISMATCH"
        elif any(not _text(row.get(k)) for k in (
            "episode_id", "opportunity_id", "policy_id", "policy_signature",
            "bucket_definition_signature", "market", "symbol", "direction",
        )):
            reason = "CAUSAL_IDENTITY_INCOMPLETE"
        elif not _number(row.get("signal_ts")) or not _number(row.get("required_end_ts")):
            reason = "SIGNAL_OR_HORIZON_MISSING_NONFINITE"
        elif row["required_end_ts"] < row["signal_ts"]:
            reason = "HORIZON_BEFORE_SIGNAL"
        elif row.get("evidence_world") not in WORLDS:
            reason = "EVIDENCE_WORLD_UNSUPPORTED"
        elif any(not _text(row.get(k)) for k in (
            "cost_model_id", "simulation_model", "economics_evidence_basis",
        )):
            reason = "ECONOMICS_IDENTITY_INCOMPLETE"
        elif not _number(row.get("original_requested_qty"), positive=True):
            reason = "REQUESTED_QUANTITY_MISSING"
        features = row.get("pre_entry_features")
        if reason is None:
            for name in names:
                item = features.get(name) if isinstance(features, Mapping) else None
                if not isinstance(item, Mapping):
                    reason = "CAUSAL_FEATURE_MISSING"
                    break
                value, observed = item.get("value"), item.get("observed_ts")
                if not (_text(value) or _number(value)) or not _number(observed):
                    reason = "CAUSAL_FEATURE_VALUE_OR_TIME_INVALID"
                    break
                if observed > row["signal_ts"]:
                    reason = "POST_SIGNAL_FEATURE"
                    break
        contract = row.get("declared_contract_sha256")
        margin = row.get("declared_position_margin_usd")
        if reason is None and (contract is not None or margin is not None):
            if not (isinstance(contract, str) and re.fullmatch(r"[0-9a-fA-F]{64}", contract)
                    and _number(margin, positive=True)):
                reason = "DECLARED_SIZING_CONTRACT_INCOMPLETE"
        if reason:
            rejections[reason] += 1
            continue
        sizing = ({"basis": "DECLARED_CONTRACT_USD_MARGIN", "contract_sha256": contract.lower(),
                   "declared_position_margin_usd": margin} if contract is not None else
                  {"basis": "EXACT_QUANTITY_FALLBACK", "original_requested_qty": row["original_requested_qty"]})
        if contract is None:
            counts["exact_quantity_fallback_rows"] += 1
        dimensions = {k: row[k] for k in (
            "evidence_world", "cost_model_id", "simulation_model", "economics_evidence_basis",
            "market", "symbol", "bucket_definition_signature",
        )}
        dimensions["sizing"] = sizing
        gid = _hash(dimensions)
        if gid not in groups:
            if len(groups) >= max_groups:
                raise ValueError("DYNAMIC_ADAPTER_GROUP_LIMIT")
            groups[gid] = {"dimensions": dimensions, "episodes": {}, "signatures": {}, "bad_policies": set()}
        group = groups[gid]
        policy, signature = row["policy_id"], row["policy_signature"]
        candidate_keys.add((gid, policy))
        if len(candidate_keys) > max_candidates:
            raise ValueError("DYNAMIC_ADAPTER_CANDIDATE_LIMIT")
        prior_signature = group["signatures"].get(policy)
        if prior_signature is not None and prior_signature != signature:
            if policy not in group["bad_policies"]:
                rejections["AMBIGUOUS_POLICY_SIGNATURE"] += 1
            group["bad_policies"].add(policy)
        group["signatures"][policy] = signature
        eid = _hash([row["episode_id"], row["opportunity_id"]])
        causal = {"source_episode_id": row["episode_id"], "opportunity_id": row["opportunity_id"],
                  "signal_ts": row["signal_ts"], "direction": row["direction"],
                  "pre_entry_features": {name: dict(features[name]) for name in names},
                  "bucket_definition_signature": row["bucket_definition_signature"],
                  "original_requested_qty": row["original_requested_qty"]}
        if eid not in group["episodes"]:
            total_episodes += 1
            if total_episodes > max_episodes:
                raise ValueError("DYNAMIC_ADAPTER_EPISODE_LIMIT")
            group["episodes"][eid] = {"causal": causal, "required_end_ts": row["required_end_ts"],
                                      "outcomes": {}, "seen_outcomes": {}, "bad_outcomes": set(), "bad": False}
        episode = group["episodes"][eid]
        if episode["causal"] != causal:
            if not episode["bad"]:
                rejections["INCOMPARABLE_EPISODE_CANDIDATES"] += 1
            episode["bad"] = True
        episode["required_end_ts"] = max(episode["required_end_ts"], row["required_end_ts"])
        outcome = None
        if state in SUPPORTED_TERMINAL_STATES:
            pnl = row.get("net_pnl_usd")
            if row.get("terminal_complete") is not True or not _number(pnl):
                rejections["TERMINAL_ECONOMICS_INCOMPLETE"] += 1
            elif state in {"NO_FILL", "NO_TRADE", "REJECTED", "REALIZED_ZERO_PNL"} and pnl != 0:
                rejections["ZERO_OUTCOME_NONZERO_PNL"] += 1
            else:
                outcome = {"outcome_state": state, "net_pnl_usd": pnl}
        seen = episode["seen_outcomes"].setdefault(policy, set())
        fingerprint = _json(outcome)
        if fingerprint in seen:
            counts["duplicate_rows"] += 1
        elif seen:
            if policy not in episode["bad_outcomes"]:
                rejections["AMBIGUOUS_DUPLICATE_OUTCOME"] += 1
            episode["bad_outcomes"].add(policy)
        seen.add(fingerprint)
        episode["outcomes"][policy] = outcome

    result_groups = []
    for gid, group in sorted(groups.items()):
        candidates = [{"policy_id": p, "policy_signature": s}
                      for p, s in sorted(group["signatures"].items()) if p not in group["bad_policies"]]
        episodes = []
        for eid, episode in sorted(group["episodes"].items()):
            if episode["bad"]:
                continue
            outcomes = {p: o for p, o in sorted(episode["outcomes"].items())
                        if o is not None and p not in episode["bad_outcomes"] and p not in group["bad_policies"]}
            episodes.append({"episode_id": eid, **episode["causal"],
                             "required_end_ts": episode["required_end_ts"], "policy_outcomes": outcomes})
            counts["supported_outcomes"] += len(outcomes)
        body = {"group_id": gid, **group["dimensions"], "episodes": episodes, "candidates": candidates,
                "counts": {"episodes": len(episodes), "candidates": len(candidates),
                           "supported_outcomes": sum(len(e["policy_outcomes"]) for e in episodes),
                           "episodes_without_supported_outcomes": sum(not e["policy_outcomes"] for e in episodes),
                           "rejected_ambiguous_episodes": sum(e["bad"] for e in group["episodes"].values()),
                           "rejected_ambiguous_candidates": len(group["bad_policies"])}}
        body["cohort_sha256"] = _hash(body)
        result_groups.append(body)
    counts["output_episodes"] = sum(len(g["episodes"]) for g in result_groups)
    result = {"schema": "same_publication_dynamic_cohorts_v1", "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
              "expected_generation": generation, "feature_names": list(names), "protocol_sha256": protocol_hash,
              "groups": result_groups, "counts": dict(sorted(counts.items())),
              "rejections": dict(sorted(rejections.items())),
              "comparison_complete": False,
              "blockers": (["INPUT_ROWS_REJECTED_CANDIDATE_UNIVERSE_INCOMPLETE"] if rejections else [])
              + (["UNKNOWN_OUTCOMES_RETAINED"] if counts["unknown_outcome_rows"] else []),
              "sizing_fallback_warning": "EXACT_QUANTITY_GROUPS_CAN_REDUCE_CROSS_OPPORTUNITY_SUPPORT" if counts["exact_quantity_fallback_rows"] else None}
    result["adapter_sha256"] = _hash(result)
    return result
