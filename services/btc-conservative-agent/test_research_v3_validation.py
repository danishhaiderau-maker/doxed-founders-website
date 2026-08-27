import unittest

from research_v3_validation import chronological_folds, episode_block_bootstrap, validate_policy


def episode(index, pnl, *, state="FULL_FILL", regime=None):
    return {
        "episode_id": f"e-{index}",
        "signal_ts": index * 10_000,
        "required_end_ts": index * 10_000 + 7200,
        "regime": regime or ("BULL" if index % 3 == 0 else "BEAR" if index % 3 == 1 else "SIDEWAYS"),
        "policy_outcomes": {"p": {"outcome_state": state, "net_pnl_usd": pnl}},
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
        verified = validate_policy(rows, policy_id="p", starting_equity_usd=1000, max_drawdown_usd=100, max_drawdown_pct=20, min_cvar95_usd=-10, policies_tested=1, conservative_execution=True, neighborhood_stable=True, sealed_holdout=True, liquidation_buffer_verified=True)
        self.assertFalse(unverified["gates"]["liquidation_buffer_pass"])
        self.assertTrue(verified["gates"]["liquidation_buffer_pass"])


if __name__ == "__main__":
    unittest.main()
