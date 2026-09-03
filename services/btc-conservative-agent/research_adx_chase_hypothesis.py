"""Pre-registered hypothesis: high ADX → start chase further (e.g. 0.30%).

This module freezes the search protocol BEFORE looking at sealed holdout.
It does not arm Bitfinex, change live policy, or invent missing episodes.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

from research_v3_contract import canonical_json

HYPOTHESIS_SCHEMA = "adx_chase_offset_hypothesis_v1"
HYPOTHESIS_ID = "HIGH_ADX_FARTHER_CHASE_0_30"
RECEIPT_SCHEMA = "adx_chase_hypothesis_evaluation_receipt_v1"

# Frozen before holdout inspection. Do not mutate after first production use.
PRE_REGISTERED_HYPOTHESIS = {
    "schema": HYPOTHESIS_SCHEMA,
    "hypothesis_id": HYPOTHESIS_ID,
    "claim": (
        "When signal-time ADX is HIGH (adx_bucket=STRONG, ADX>=30), an initial "
        "limit offset of 0.30% beats closer starts (0.10% / 0.20%) on "
        "conservative E[PnL|opportunity] and/or fill-adjusted expectancy."
    ),
    "feature_definitions": {
        "adx_bucket": {"STRONG": "ADX >= 30", "source": "_adx_bucket"},
        "primary_feature": "trend_strength_bucket",
        "observed_ts_required": True,
        "leakage_rule": "observed_ts must be <= signal_ts",
    },
    "treatment_cells": {
        "offsets_pct": [0.10, 0.20, 0.27, 0.30],
        "chase_policies": ["no_chase", "w234_s50_i180", "compressed_0_1_2_4_7_10"],
        "primary_contrast": {"control_offset_pct": 0.10, "treatment_offset_pct": 0.30},
    },
    "evidence_worlds": {
        "primary": "CONSERVATIVE_BBO",
        "diagnostic": "IDEAL_TOUCH",
        "observed": "OBSERVED_PAPER",
        "pnl_sum_across_worlds": False,
    },
    "validation_protocol": {
        "screen_on": "inner_purged_walk_forward_training_blocks_only",
        "freeze_once": True,
        "sealed_holdout_evaluations": 1,
        "reject_n1_perfect_green": True,
        "min_support_per_cell": 20,
        "fail_closed_statuses": ["UNKNOWN", "INSUFFICIENT_EPISODES", "MISSING_PRE_ENTRY"],
    },
    "promotion_gates": {
        "auto_arm_bitfinex": False,
        "relay_eligible_from_this_receipt": False,
        "live_policy_change_allowed": False,
    },
}


def hypothesis_signature(material: Mapping[str, Any] | None = None) -> str:
    body = canonical_json(material or PRE_REGISTERED_HYPOTHESIS)
    return "hyp-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def emit_hypothesis_evaluation_receipt(
    *,
    status: str,
    blockers: list[str] | None = None,
    episode_count: int = 0,
    complete_episode_count: int = 0,
    data_root: str | Path | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit COMPLETE or UNKNOWN (fail-closed). Never invent holdout wins."""
    status_u = str(status or "UNKNOWN").upper()
    if status_u not in {"COMPLETE", "UNKNOWN", "INSUFFICIENT_EPISODES"}:
        status_u = "UNKNOWN"
    blockers = sorted({str(b) for b in (blockers or []) if b})
    if complete_episode_count < int(
        PRE_REGISTERED_HYPOTHESIS["validation_protocol"]["min_support_per_cell"]
    ):
        status_u = "UNKNOWN"
        blockers = sorted(set(blockers) | {"INSUFFICIENT_COMPLETE_EPISODES_FOR_PURGED_WF"})
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": status_u,
        "hypothesis_id": HYPOTHESIS_ID,
        "hypothesis_signature": hypothesis_signature(),
        "hypothesis": dict(PRE_REGISTERED_HYPOTHESIS),
        "evaluated_at_unix": time.time(),
        "episode_count": int(episode_count),
        "complete_episode_count": int(complete_episode_count),
        "purged_walk_forward": None if status_u != "COMPLETE" else (extra or {}).get("purged_walk_forward"),
        "sealed_holdout": None if status_u != "COMPLETE" else (extra or {}).get("sealed_holdout"),
        "blockers": blockers,
        "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
        "execution_class": "RESEARCH_ONLY",
        "relay_eligible": False,
        "live_policy_change_allowed": False,
        "bitfinex_arm_allowed": False,
    }
    if data_root is not None:
        out = Path(data_root) / "diagnostics" / "adx_chase_hypothesis_receipt.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt["receipt_path"] = str(out)
    return receipt


def evaluate_or_unknown(
    *,
    complete_episode_count: int,
    episode_count: int = 0,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run only when enough complete episodes exist; otherwise UNKNOWN."""
    min_n = int(PRE_REGISTERED_HYPOTHESIS["validation_protocol"]["min_support_per_cell"])
    if complete_episode_count < min_n:
        return emit_hypothesis_evaluation_receipt(
            status="UNKNOWN",
            blockers=["INSUFFICIENT_COMPLETE_EPISODES_FOR_PURGED_WF"],
            episode_count=episode_count,
            complete_episode_count=complete_episode_count,
            data_root=data_root,
        )
    # Holdout evaluation is intentionally not auto-run here without verified
    # mirror input. Callers must supply COMPLETE evidence through the dynamic
    # policy analyzer path after mirror verification.
    return emit_hypothesis_evaluation_receipt(
        status="UNKNOWN",
        blockers=["PURGED_WF_REQUIRES_VERIFIED_DYNAMIC_INPUT"],
        episode_count=episode_count,
        complete_episode_count=complete_episode_count,
        data_root=data_root,
    )
