from research.policy_evidence_evaluator import (
    PHASE7_SUPPORT_GATE_V1,
    build_phase7_support_qualification,
)
from research.policy_evidence_schema import stable_hash


def _row(decision, regime="RANGE", direction="LONG", *, lane="CONTROL", cluster=None):
    values = {
        "realized_volatility": 0.1, "volatility_of_volatility": 0.01,
        "market_spread_bps": 1.0, "bid_depth_qty": 2.0, "ask_depth_qty": 2.0,
        "liquidity": "NORMAL", "regime": regime, "adx": 25.0,
        "trend_strength": 0.5, "market_structure": "BALANCED",
        "session": "ASIA", "signal_timestamp": 1,
    }
    return {
        "epoch_id": "epoch-1", "opportunity_id": f"opp-{decision}",
        "episode_id": f"episode-{decision}", "lane": lane, "side": direction,
        "dependence_cluster_id": cluster, "source_revision": "rev-1",
        "config_signature": "config-1", "tile_signature": "tiles-1",
        "regime_features_at_signal": {
            key: {"status": "OBSERVED", "value": value, "source": f"fixture.{key}"}
            for key, value in values.items()
        },
    }


def _config(cells, minimum=1):
    registry = {
        "schema": "eligible_regime_direction_cells_v1",
        "runtime_taxonomy_signature": PHASE7_SUPPORT_GATE_V1["runtime_taxonomy"]["signature"],
        "source_revision": "rev-1", "epoch_id": "epoch-1",
        "config_signature": "config-1", "tile_signature": "tiles-1",
        "eligible_cells": [
            {"regime": regime, "direction": direction} for regime, direction in cells
        ],
    }
    registry["signature"] = stable_hash("eligible-regime-direction-cells", registry)
    return {
        "minimum_independent_cohorts": minimum,
        "minimum_effective_cohorts": minimum,
        "minimum_cohorts_per_regime_direction": 1,
        "eligible_cell_registry": registry,
    }


def test_sibling_lanes_count_as_one_canonical_independent_decision():
    rows = [_row("one", lane=f"LANE_{number}") for number in range(6)]
    receipt = build_phase7_support_qualification(rows, _config([("RANGE", "LONG")]))
    assert receipt["row_count"] == 6
    assert receipt["raw_independent_decision_n"] == 1
    assert receipt["cluster_adjusted_effective_n"] == 1
    assert receipt["sibling_lane_rows_deduplicated"] == 5
    assert receipt["qualification_allowed"] is True


def test_effective_n_is_cluster_adjusted_and_cannot_be_inflated_by_decisions():
    rows = [_row("one", cluster="price-cluster-1"), _row("two", cluster="price-cluster-1")]
    receipt = build_phase7_support_qualification(rows, _config([("RANGE", "LONG")], minimum=2))
    assert receipt["raw_independent_decision_n"] == 2
    assert receipt["cluster_adjusted_effective_n"] == 1
    assert receipt["qualification_allowed"] is False
    assert "INSUFFICIENT_CLUSTER_ADJUSTED_EFFECTIVE_COHORTS" in receipt["reason_codes"]


def test_every_frozen_runtime_eligible_cell_must_have_support():
    receipt = build_phase7_support_qualification(
        [_row("one")], _config([("RANGE", "LONG"), ("RANGE", "SHORT")])
    )
    assert receipt["regime_direction_raw_independent_n"]["RANGE|SHORT"] == 0
    assert receipt["qualification_allowed"] is False
    assert "INSUFFICIENT_REGIME_DIRECTION_COHORT_SUPPORT" in receipt["reason_codes"]


def test_eligible_cells_require_exact_signed_registry():
    config = _config([("RANGE", "LONG")])
    del config["eligible_cell_registry"]
    receipt = build_phase7_support_qualification([_row("one")], config)
    assert receipt["qualification_allowed"] is False
    assert "SIGNED_ELIGIBLE_CELL_REGISTRY_MISSING" in receipt["reason_codes"]


def test_eligible_cell_registry_signature_and_provenance_fail_closed():
    config = _config([("RANGE", "LONG")])
    config["eligible_cell_registry"]["tile_signature"] = "wrong-after-signing"
    receipt = build_phase7_support_qualification([_row("one")], config)
    assert receipt["qualification_allowed"] is False
    assert "ELIGIBLE_CELL_REGISTRY_SIGNATURE_MISMATCH" in receipt["reason_codes"]
    assert "ELIGIBLE_CELL_REGISTRY_PROVENANCE_MISMATCH" in receipt["reason_codes"]


def test_observed_regime_not_in_frozen_taxonomy_fails_closed():
    receipt = build_phase7_support_qualification(
        [_row("one", regime="MYSTERY")], _config([("RANGE", "LONG")])
    )
    assert receipt["taxonomy_mismatch_decisions"] == 1
    assert receipt["gates"]["observed_regimes_match_frozen_taxonomy"] is False
    assert "OBSERVED_RUNTIME_TAXONOMY_MISMATCH" in receipt["reason_codes"]


def test_bull_bear_range_projection_rejects_training_evaluation_leakage():
    row = _row("one", regime="BULL")
    decision_id = stable_hash("independent-decision", {
        "epoch_id": row["epoch_id"], "opportunity_id": row["opportunity_id"],
        "episode_id": row["episode_id"],
    })
    projection = {
        "schema": "fold_fitted_regime_projection_v1", "fold_id": "fold-1",
        "fit_scope": "TRAINING_FOLD_ONLY",
        "source_taxonomy_signature": PHASE7_SUPPORT_GATE_V1["runtime_taxonomy"]["signature"],
        "target_regimes": ["BULL", "BEAR", "RANGE"],
        "fit_decision_ids": [decision_id], "evaluation_decision_ids": [decision_id],
    }
    projection["signature"] = stable_hash("fold-fitted-regime-projection", projection)
    config = _config([("BULL", "LONG")])
    config["regime_projection"] = projection
    receipt = build_phase7_support_qualification([row], config)
    assert receipt["regime_projection"]["status"] == "INVALID"
    assert receipt["qualification_allowed"] is False
    assert "REGIME_PROJECTION_FIT_EVALUATION_LEAKAGE" in receipt["reason_codes"]
