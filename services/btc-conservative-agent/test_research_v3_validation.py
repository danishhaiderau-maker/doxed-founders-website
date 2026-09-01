import unittest

from research_v3_validation import (
    chronological_folds,
    episode_block_bootstrap,
    validate_policy,
    validate_purged_walk_forward,
)
from research_v3_liquidation_buffer import build_liquidation_buffer_receipt


def episode(index, pnl, *, state="FULL_FILL", regime=None, measured_costs=True):
    outcome = {"outcome_state": state, "net_pnl_usd": pnl}
    if measured_costs and state in {"FULL_FILL", "PARTIAL_FILL", "REALIZED_ZERO_PNL"}:
        outcome["cost_evidence"] = {
            "schema": "measured_execution_cost_receipt_v1",
            "status": "MEASURED",
            "entry_fee_usd": 0.0, "exit_fee_usd": 0.0,
            "trading_fees_usd": 0.0,
            "funding_usd": 0.0,
            "entry_slippage_usd": 0.0, "exit_slippage_usd": 0.0,
            "slippage_usd": 0.0, "latency_cost_usd": 0.0,
            "gross_pnl_usd": pnl, "net_pnl_usd": pnl,
            "gross_pnl_basis": "ACTUAL_EXECUTION_PRICES_INCLUDES_PRICE_IMPACT",
            "net_pnl_reconciliation_basis": "GROSS_MINUS_TRADING_FEES_MINUS_FUNDING_FEES",
            "blockers": [],
            "source_receipt_ids": [f"cost-{index}"],
        }
    return {
        "episode_id": f"e-{index}",
        "signal_ts": index * 10_000,
        "required_end_ts": index * 10_000 + 7200,
        "regime": regime or ("BULL" if index % 3 == 0 else "BEAR" if index % 3 == 1 else "SIDEWAYS"),
        "policy_outcomes": {"p": outcome},
    }


class V3ValidationTests(unittest.TestCase):
    def test_empty_scored_cohort_has_unavailable_risk_not_perfect_zeroes(self):
        report = validate_policy([], policy_id="p", starting_equity_usd=1000,
            max_drawdown_usd=100, max_drawdown_pct=10, min_cvar95_usd=-10,
            policies_tested=1, conservative_execution=False,
            neighborhood_stable=False, sealed_holdout=False)

        self.assertEqual(report["episodes_scored"], 0)
        self.assertEqual(report["evidence_status"], "INSUFFICIENT_EXECUTION_EVIDENCE")
        for metric in (
            "ending_equity_usd", "net_pnl_usd", "max_drawdown_usd",
            "max_drawdown_pct", "longest_loss_streak", "wins", "losses",
        ):
            self.assertIsNone(report["risk"][metric], metric)
        self.assertIn("DRAWDOWN_UNAVAILABLE", report["drawdown_budget"]["reasons"])
        self.assertFalse(report["gates"]["drawdown_budget_pass"])

    def test_seeded_bootstrap_is_deterministic_and_bounded(self):
        first = episode_block_bootstrap([1.0, -0.5, 2.0], samples=250, seed=19)
        second = episode_block_bootstrap([1.0, -0.5, 2.0], samples=250, seed=19)
        self.assertEqual(first, second)
        self.assertEqual(first["samples"], 250)
        self.assertLessEqual(first["mean_lcb95"], first["mean_ucb95"])
        self.assertGreaterEqual(first["probability_mean_positive"], 0.0)
        self.assertLessEqual(first["probability_mean_positive"], 1.0)

    def test_purge_removes_training_paths_overlapping_validation(self):
        rows = [episode(i, 1) for i in range(30)]
        folds = chronological_folds(rows, outer_folds=5, purge_sec=7200, embargo_sec=300)
        self.assertEqual(len(folds), 5)
        for fold in folds:
            boundary = fold["validation_start_ts"]
            self.assertTrue(all(row["required_end_ts"] < boundary - 7500 for row in fold["train"]))

    def test_purged_walk_forward_requires_complete_positive_later_folds(self):
        rows = [episode(i, 1) for i in range(90)]
        result = validate_purged_walk_forward(rows, policy_id="p")
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["complete_folds"], 3)
        self.assertEqual(result["positive_folds"], result["complete_folds"])
        self.assertEqual(
            result["policy_selection_semantics"],
            "FROZEN_BEFORE_VALIDATION_NOT_SELECTED_ON_FOLDS",
        )

        rows[70] = episode(70, None, state="UNSUPPORTED")
        incomplete = validate_purged_walk_forward(rows, policy_id="p")
        self.assertFalse(incomplete["passed"])
        self.assertTrue(any(
            "e-70" in fold["missing_or_unsupported_episode_ids"]
            for fold in incomplete["folds"]
        ))

    def test_validation_cannot_qualify_without_purged_walk_forward_receipt(self):
        rows = [episode(i, 2) for i in range(100)]
        missing = validate_policy(
            rows, policy_id="p", starting_equity_usd=1000,
            max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10,
            policies_tested=1, conservative_execution=True,
            neighborhood_stable=True, sealed_holdout=True,
            liquidation_buffer_verified=True,
        )
        self.assertFalse(missing["gates"]["purged_walk_forward_pass"])
        self.assertIn(
            "PURGED_WALK_FORWARD_NOT_SUPPLIED",
            missing["purged_walk_forward"]["blockers"],
        )

        receipt = validate_purged_walk_forward(rows, policy_id="p")
        supplied = validate_policy(
            rows, policy_id="p", starting_equity_usd=1000,
            max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10,
            policies_tested=1, conservative_execution=True,
            neighborhood_stable=True, sealed_holdout=True,
            liquidation_buffer_verified=True,
            purged_walk_forward=receipt,
        )
        self.assertTrue(supplied["gates"]["purged_walk_forward_pass"])

    def test_purged_walk_forward_rejects_duplicate_or_missing_causal_time(self):
        rows = [episode(i, 1) for i in range(90)]
        rows[20]["episode_id"] = rows[19]["episode_id"]
        rows[30].pop("required_end_ts")
        result = validate_purged_walk_forward(rows, policy_id="p")
        self.assertFalse(result["passed"])
        self.assertIn(
            "INVALID_WALK_FORWARD_CAUSAL_IDENTITIES_OR_TIMESTAMPS",
            result["blockers"],
        )
        self.assertTrue(any(
            defect.startswith("DUPLICATE_EPISODE_ID:")
            for defect in result["input_defects"]
        ))

    def test_unsupported_is_not_silently_zero_pnl(self):
        rows = [episode(i, 2) for i in range(100)]
        rows[5] = episode(5, None, state="UNSUPPORTED")
        report = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=100, max_drawdown_pct=10, min_cvar95_usd=-10, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=True)
        self.assertFalse(report["gates"]["integrity_pass"])
        self.assertEqual(report["episodes_scored"], 99)
        self.assertIn("e-5", report["missing_or_unsupported_episode_ids"])

    def test_positive_pnl_still_fails_unsafe_drawdown_or_unsealed_holdout(self):
        rows = [episode(i, 5) for i in range(100)]
        rows[80] = episode(80, -100)
        report = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=25, max_drawdown_pct=5, min_cvar95_usd=-30, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=False)
        self.assertGreater(report["risk"]["net_pnl_usd"], 0)
        self.assertFalse(report["gates"]["drawdown_budget_pass"])
        self.assertFalse(report["gates"]["sealed_holdout_pass"])
        self.assertFalse(report["qualified"])

    def test_no_fill_is_explicit_valid_opportunity_not_execution(self):
        rows = [episode(i, None, state="NO_FILL") for i in range(100)]
        report = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=25, max_drawdown_pct=5, min_cvar95_usd=-1, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=True)
        self.assertEqual(report["episodes_scored"], 100)
        self.assertEqual(report["risk"]["realized_zero_executions"], 0)
        self.assertEqual(report["risk"]["non_execution_zero_contributions"], 100)
        self.assertEqual(report["outcome_states"]["NO_FILL"], 100)
        self.assertFalse(report["gates"]["oos_lcb_positive_pass"])

    def test_liquidation_buffer_is_never_inferred_from_price_path(self):
        rows = [episode(i, 2) for i in range(100)]
        unverified = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=True)
        asserted = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=True, liquidation_buffer_verified=True)
        observations = [{
            "schema": "exchange_liquidation_buffer_observation_v1",
            "episode_id": f"e-{i}", "policy_id": "p", "direction": "LONG",
            "leverage": 10.0, "margin_usd": 100.0, "equity_usd": 1000.0,
            "entry_price": 100.0, "worst_adverse_mark_price": 95.0,
            "exchange_liquidation_price": 80.0,
            "maintenance_margin_rate_pct": 0.5,
            "max_adverse_excursion_pct": 5.0, "max_drawdown_usd": 5.0,
            "observed_buffer_pct": (15.0 / 95.0) * 100.0,
            "source_receipt_ids": [f"exchange-{i}"],
        } for i in range(100)]
        receipt = build_liquidation_buffer_receipt(
            policy_id="p", observations=observations,
            minimum_required_buffer_pct=10.0,
        )
        verified = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=True, liquidation_buffer_verified=receipt)
        self.assertFalse(unverified["gates"]["liquidation_buffer_pass"])
        self.assertFalse(asserted["gates"]["liquidation_buffer_pass"])
        self.assertTrue(verified["gates"]["liquidation_buffer_pass"])

    def test_executed_outcome_without_measured_cost_receipt_fails_closed(self):
        rows = [episode(i, 2) for i in range(100)]
        rows[17] = episode(17, 2, measured_costs=False)
        report = validate_policy(
            rows, policy_id="p", starting_equity_usd=1000,
            max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10,
            policies_tested=1, conservative_execution=True,
            neighborhood_stable=True, sealed_holdout=True,
            liquidation_buffer_verified=True,
            purged_walk_forward=validate_purged_walk_forward(rows, policy_id="p"),
        )
        self.assertFalse(report["gates"]["measured_costs_pass"])
        self.assertFalse(report["qualified"])
        self.assertEqual(
            report["measured_cost_evidence"]["defects"][0]["episode_id"],
            "e-17",
        )
        self.assertIn(
            "MEASURED_COST_RECEIPT_MISSING",
            report["measured_cost_evidence"]["defects"][0]["reasons"],
        )

    def test_explicit_measured_zero_costs_are_valid_evidence(self):
        rows = [episode(i, 2) for i in range(100)]
        report = validate_policy(
            rows, policy_id="p", starting_equity_usd=1000,
            max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10,
            policies_tested=1, conservative_execution=True,
            neighborhood_stable=True, sealed_holdout=True,
            liquidation_buffer_verified=True,
            purged_walk_forward=validate_purged_walk_forward(rows, policy_id="p"),
        )
        self.assertTrue(report["gates"]["measured_costs_pass"])
        self.assertEqual(report["measured_cost_evidence"]["defects"], [])

    def test_partial_fill_requires_all_cost_measurements_and_source(self):
        rows = [episode(i, None, state="NO_FILL") for i in range(100)]
        rows[5] = episode(5, 1, state="PARTIAL_FILL")
        receipt = rows[5]["policy_outcomes"]["p"]["cost_evidence"]
        receipt.pop("funding_usd")
        receipt["source_receipt_ids"] = []
        report = validate_policy(
            rows, policy_id="p", starting_equity_usd=1000,
            max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10,
            policies_tested=1, conservative_execution=True,
            neighborhood_stable=True, sealed_holdout=True,
        )
        reasons = report["measured_cost_evidence"]["defects"][0]["reasons"]
        self.assertFalse(report["gates"]["measured_costs_pass"])
        self.assertIn("FUNDING_USD_MEASUREMENT_MISSING", reasons)
        self.assertIn("MEASURED_COST_SOURCE_RECEIPT_REQUIRED", reasons)


if __name__ == "__main__":
    unittest.main()
