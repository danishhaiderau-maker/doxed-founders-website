import unittest

from research_v3_contract import (
    EXECUTION_WORLDS,
    OUTCOME_STATES,
    SAFE_POLICY_GENOME_CONTRACT,
    build_policy_identity,
    normalize_lifecycle_outcome,
    validate_policy_spec,
)
from research_v3_ranking import REQUIRED_GATES, rank_safe_policies


def _protected_spec():
    return {
        "entry": {"offset_pct": 0.29, "ttl_min": 30, "chase": "w234_s25_i60"},
        "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_TAPE", "partial": "KEEP_PROTECTED"},
        "loss_protection": {"atr_stop_k": 1.5, "hard_stop_margin_pct": 20},
        "profit_protection": {"atr_tp_k": 2.5, "ladder": [[8, 5], [12, 10]]},
        "portfolio": {"concurrency_cap": 1, "daily_loss_kill_pct": 3},
    }


class ResearchV3ContractTests(unittest.TestCase):
    def test_contract_distinguishes_missing_from_real_zero(self):
        self.assertIn("REALIZED_ZERO_PNL", OUTCOME_STATES)
        self.assertIn("UNSUPPORTED", OUTCOME_STATES)
        self.assertEqual(SAFE_POLICY_GENOME_CONTRACT["fees"]["bitfinex_trading_fee_rate"], 0.0)
        self.assertIn("funding", SAFE_POLICY_GENOME_CONTRACT["fees"]["separate_non_fee_costs"])

    def test_lifecycle_workflow_labels_map_to_analytical_outcomes(self):
        self.assertEqual(normalize_lifecycle_outcome("PENDING_FILL"), "CENSORED")
        self.assertEqual(normalize_lifecycle_outcome("PAPER_REALIZED", net_pnl_usd=2), "REALIZED_PROFIT")
        self.assertEqual(normalize_lifecycle_outcome("PAPER_REALIZED", net_pnl_usd=-2), "REALIZED_LOSS")
        self.assertEqual(normalize_lifecycle_outcome("PAPER_REALIZED", net_pnl_usd=0), "REALIZED_ZERO_PNL")
        self.assertEqual(normalize_lifecycle_outcome("PAPER_REALIZED"), "CENSORED")

    def test_identity_changes_for_cost_or_data_snapshot(self):
        spec = _protected_spec()
        common = dict(simulator={"version": 3}, fill_model={"world": EXECUTION_WORLDS[1]})
        a = build_policy_identity(spec, **common, cost_model={"fees": 0, "funding": "actual"}, data_snapshot={"sha": "a"})
        b = build_policy_identity(spec, **common, cost_model={"fees": 0, "funding": "actual"}, data_snapshot={"sha": "b"})
        c = build_policy_identity(spec, **common, cost_model={"fees": 0, "funding": "zero"}, data_snapshot={"sha": "a"})
        self.assertNotEqual(a.policy_signature, b.policy_signature)
        self.assertNotEqual(a.policy_signature, c.policy_signature)

    def test_policy_validation_requires_protection_and_monotonic_ladder(self):
        spec = _protected_spec()
        self.assertEqual(validate_policy_spec(spec), [])
        spec["loss_protection"] = {}
        self.assertIn("UNPROTECTED_POLICY", validate_policy_spec(spec))
        spec = _protected_spec()
        spec["profit_protection"]["ladder"] = [[8, 5], [7, 6]]
        self.assertIn("NON_MONOTONIC_OR_INVALID_LADDER", validate_policy_spec(spec))

    def test_ranking_never_promotes_profit_with_failed_drawdown(self):
        passes = {gate: True for gate in REQUIRED_GATES}
        unsafe = {"policy_id": "HUGE_PNL_UNSAFE", "sealed_oos_net_usd": 1000, "max_drawdown_usd": -900, "cvar95_usd": -100, "expectancy_lcb_usd": 8, "gates": {**passes, "drawdown_budget_pass": False}}
        safe_a = {"policy_id": "SAFE_A", "sealed_oos_net_usd": 100, "max_drawdown_usd": -20, "cvar95_usd": -5, "expectancy_lcb_usd": 1, "gates": passes}
        safe_b = {"policy_id": "SAFE_B", "sealed_oos_net_usd": 100, "max_drawdown_usd": -10, "cvar95_usd": -7, "expectancy_lcb_usd": 1, "gates": passes}
        report = rank_safe_policies([unsafe, safe_a, safe_b])
        self.assertEqual(report["number_one"]["policy_id"], "SAFE_B")
        self.assertEqual(report["policies_qualified"], 2)
        self.assertIn("drawdown_budget_pass", report["blocked"][0]["ranking_blockers"])

    def test_ranking_requires_minimum_sample_and_sealed_holdout(self):
        passes = {gate: True for gate in REQUIRED_GATES}
        too_small = {"policy_id": "TOO_SMALL", "sealed_oos_net_usd": 99, "gates": {**passes, "minimum_episode_pass": False}}
        inspected_holdout = {"policy_id": "INSPECTED", "sealed_oos_net_usd": 99, "gates": {**passes, "sealed_holdout_pass": False}}
        report = rank_safe_policies([too_small, inspected_holdout])
        self.assertIsNone(report["number_one"])
        blockers = {row["policy_id"]: row["ranking_blockers"] for row in report["blocked"]}
        self.assertIn("minimum_episode_pass", blockers["TOO_SMALL"])
        self.assertIn("sealed_holdout_pass", blockers["INSPECTED"])


if __name__ == "__main__":
    unittest.main()
