import unittest

from research_v3_risk import drawdown_budget_gate, portfolio_risk_metrics
from research_v3_search import build_search_plan, search_progress


class V3RiskAndSearchTests(unittest.TestCase):
    def test_portfolio_metrics_capture_drawdown_tail_and_recovery(self):
        metrics = portfolio_risk_metrics([10, -5, -10, 8, 12], starting_equity_usd=100)
        self.assertEqual(metrics["max_drawdown_usd"], -15)
        self.assertEqual(metrics["longest_loss_streak"], 2)
        self.assertLess(metrics["cvar95_usd"], 0)
        self.assertGreater(metrics["recovery_factor"], 0)

    def test_drawdown_gate_fails_even_when_net_is_positive(self):
        metrics = portfolio_risk_metrics([100, -90, 20], starting_equity_usd=100)
        gate = drawdown_budget_gate(metrics, max_drawdown_usd=25, max_drawdown_pct=20, min_cvar95_usd=-30)
        self.assertFalse(gate["passed"])
        self.assertIn("MAX_DRAWDOWN_USD_EXCEEDED", gate["reasons"])

    def test_search_plan_reports_billions_without_materializing(self):
        plan = build_search_plan({"offset": [0.01, 0.1, 0.29], "ttl": [5, 30], "chase": ["none", "patient"]})
        self.assertGreater(plan["counts"]["nominal_full_cartesian"], 1_000_000)
        self.assertEqual(plan["counts"]["materialized_policy_rows"], 0)
        self.assertIn("never persisted", plan["warning"])
        progress = search_progress(plan, [{"unique_policies_evaluated": 100, "independent_episodes": 20}, {"unique_policies_evaluated": 25, "independent_episodes": 30}])
        self.assertEqual(progress["unique_policies_evaluated"], 125)
        self.assertEqual(progress["independent_episodes"], 30)
        self.assertFalse(progress["exhaustive_materialization"])


if __name__ == "__main__":
    unittest.main()
