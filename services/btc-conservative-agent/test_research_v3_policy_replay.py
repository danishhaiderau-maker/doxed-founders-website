import unittest

from research_v3_policy_replay import replay_protected_policy


def spec(**overrides):
    value = {
        "entry": {"offset_pct": 0.29},
        "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_TAPE"},
        "loss_protection": {"atr_stop_k": 1.5, "hard_stop_margin_pct": 30, "thesis_cut_margin_pct": -12, "thesis_window_sec": 300, "time_stop_min": 120},
        "profit_protection": {"atr_tp_k": 2.5, "ladder": [], "break_even_arm_mfe_pct": None, "break_even_floor_pct": 0},
        "portfolio": {"concurrency_cap": 1},
    }
    for key, update in overrides.items():
        value[key].update(update)
    return value


class ProtectedReplayTests(unittest.TestCase):
    def test_atr_stop_prevents_tp_only_path_end_drawdown(self):
        policy = spec(loss_protection={"atr_stop_k": 1.5, "hard_stop_margin_pct": 100, "thesis_cut_margin_pct": None, "thesis_window_sec": 0})
        result = replay_protected_policy(
            [{"ts": 360, "price": 99.8}, {"ts": 420, "price": 99.2}, {"ts": 480, "price": 98}],
            direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=0.5,
            leverage=100, margin_usd=20, policy_spec=policy,
        )
        self.assertEqual(result["exit_reason"], "ATR_STOP")
        self.assertGreater(result["net_pnl_usd"], -20)

    def test_break_even_and_ladder_never_loosen(self):
        policy = spec(profit_protection={"atr_tp_k": 4, "break_even_arm_mfe_pct": 3, "break_even_floor_pct": 1, "ladder": [[8, 5], [12, 10]]})
        result = replay_protected_policy(
            [{"ts": 60, "price": 100.04}, {"ts": 120, "price": 100.09}, {"ts": 180, "price": 100.06}, {"ts": 240, "price": 100.04}],
            direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=1,
            leverage=100, margin_usd=20, policy_spec=policy,
        )
        floors = [row["active_floor_pct"] for row in result["trace"] if row["active_floor_pct"] is not None]
        self.assertEqual(floors, sorted(floors))
        self.assertEqual(result["exit_reason"], "PROFIT_PROTECTION_FLOOR")

    def test_zero_trading_fee_but_funding_and_slippage_reduce_net(self):
        result = replay_protected_policy(
            [{"ts": 60, "price": 100.03}], direction="LONG", entry_price=100,
            fill_ts=0, atr_pct_at_fill=0.01, leverage=100, margin_usd=20,
            policy_spec=spec(), funding_usd=0.1, slippage_usd=0.2,
        )
        self.assertEqual(result["exit_reason"], "ATR_TAKE_PROFIT")
        self.assertEqual(result["trading_fees_usd"], 0.0)
        self.assertAlmostEqual(result["gross_pnl_usd"] - result["net_pnl_usd"], 0.3)

    def test_unprotected_policy_fails_closed(self):
        policy = spec(loss_protection={"atr_stop_k": None, "hard_stop_margin_pct": None, "thesis_cut_margin_pct": None})
        result = replay_protected_policy([{"ts": 60, "price": 100}], direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=1, leverage=100, margin_usd=20, policy_spec=policy)
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertFalse(result["ranking_eligible"])

    def test_atr_trail_never_loosens_and_reports_giveback(self):
        policy = spec(
            profit_protection={
                "mode": "ATR_TRAIL", "atr_tp_k": None, "atr_trail_k": 1.0,
                "trail_activation_atr_k": 1.0, "partial_take_profits": [],
            },
        )
        result = replay_protected_policy(
            [{"ts": 60, "price": 100.01}, {"ts": 120, "price": 100.03}, {"ts": 180, "price": 100.019}],
            direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=0.01,
            leverage=100, margin_usd=20, policy_spec=policy,
        )
        self.assertEqual(result["exit_reason"], "PROFIT_PROTECTION_FLOOR")
        self.assertGreater(result["profit_giveback_pct"], 0)
        floors = [row["active_floor_pct"] for row in result["trace"] if row["active_floor_pct"] is not None]
        self.assertEqual(floors, sorted(floors))

    def test_hybrid_runner_accounts_for_partial_realization(self):
        policy = spec(
            profit_protection={
                "mode": "HYBRID_RUNNER", "atr_tp_k": None, "atr_trail_k": 1.0,
                "trail_activation_atr_k": 1.0,
                "partial_take_profits": [[1.0, 0.25], [1.5, 0.25]],
            },
        )
        result = replay_protected_policy(
            [{"ts": 60, "price": 100.01}, {"ts": 120, "price": 100.02}, {"ts": 180, "price": 100.009}],
            direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=0.01,
            leverage=100, margin_usd=20, policy_spec=policy,
        )
        self.assertEqual(result["partial_exit_count"], 2)
        self.assertEqual(result["remaining_fraction_at_terminal"], 0.5)
        self.assertGreater(result["portfolio_margin_return_pct"], result["margin_return_pct"])

    def test_hybrid_atr_break_even_arms_from_frozen_fill_atr(self):
        policy = spec(
            profit_protection={
                "mode": "HYBRID_RUNNER", "atr_tp_k": 2.5, "atr_trail_k": 1.0,
                "trail_activation_atr_k": 1.25,
                "break_even_arm_atr_k": 1.25, "break_even_floor_pct": 0.5,
                "partial_take_profits": [[1.0, 0.25], [1.5, 0.25]],
            },
        )
        result = replay_protected_policy(
            [
                {"ts": 60, "price": 100.01},
                {"ts": 120, "price": 100.013},
                {"ts": 180, "price": 100.004},
            ],
            direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=0.01,
            leverage=100, margin_usd=20, policy_spec=policy,
        )
        self.assertEqual(result["exit_reason"], "PROFIT_PROTECTION_FLOOR")
        self.assertGreaterEqual(result["active_floor_pct"], 0.5)
        self.assertEqual(result["partial_exit_count"], 1)

    def test_hybrid_runner_honors_final_atr_target(self):
        policy = spec(
            profit_protection={
                "mode": "HYBRID_RUNNER", "atr_tp_k": 2.5, "atr_trail_k": 1.0,
                "trail_activation_atr_k": 1.25,
                "break_even_arm_atr_k": 1.25, "break_even_floor_pct": 0.5,
                "partial_take_profits": [[1.0, 0.25], [1.5, 0.25]],
            },
        )
        result = replay_protected_policy(
            [
                {"ts": 60, "price": 100.01},
                {"ts": 120, "price": 100.015},
                {"ts": 180, "price": 100.025},
            ],
            direction="LONG", entry_price=100, fill_ts=0, atr_pct_at_fill=0.01,
            leverage=100, margin_usd=20, policy_spec=policy,
        )
        self.assertEqual(result["exit_reason"], "ATR_TAKE_PROFIT")
        self.assertEqual(result["partial_exit_count"], 2)
        self.assertEqual(result["remaining_fraction_at_terminal"], 0.5)


if __name__ == "__main__":
    unittest.main()
