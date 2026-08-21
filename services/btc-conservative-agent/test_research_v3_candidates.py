import unittest

from research_v3_candidates import evaluate_protection_screen, protection_screen
from research_v3_ranking import rank_safe_policies


def source(event_id="event-1", episode_id="episode-1"):
    return {
        "event_id": event_id,
        "episode_id": episode_id,
        "signal_ts": 1000,
        "regime": "BULL",
        "direction": "LONG",
        "atr14_pct": 0.1,
        "leverage": 100,
        "margin_usd": 20,
        "entry_children": [{
            "entry_policy_id": "OFFSET_0.29_CHASE_patient",
            "offset_pct": 0.29,
            "chase_id": "patient",
            "fill_ts": 1000,
            "fill_price": 100,
            "fill_model": "IDEAL_TOUCH",
        }],
        "ordered_1s_prices": [
            {"ts": 1000, "price": 100},
            {"ts": 1001, "price": 100.3},
        ],
    }


class V3CandidateTests(unittest.TestCase):
    def test_screen_contains_requested_loss_and_profit_protection_families(self):
        names = {row["protection_id"] for row in protection_screen()}
        self.assertIn("ATR_TP_2.5_ATR_SL_1", names)
        self.assertIn("ATR_TP_2.5_THESIS_12_HARD_30", names)
        self.assertIn("ATR_TP_2.5_SCENARIO_C", names)
        self.assertIn("ATR_TP_2.5_TIME_120", names)
        self.assertIn("ATR_TP_2.5_BE_4_LOCK_1", names)
        self.assertIn("ATR_TP_2.5_GIVEBACK_40PCT", names)

    def test_descriptive_policies_are_visible_but_never_safe_qualified(self):
        report = evaluate_protection_screen([source()])
        self.assertEqual(report["unique_policies_evaluated"], len(protection_screen()))
        self.assertTrue(report["descriptive_top_100"])
        ranking = rank_safe_policies(report["candidates"])
        self.assertIsNone(ranking["number_one"])
        self.assertTrue(all("conservative_execution_pass" in row["ranking_blockers"] for row in ranking["blocked"]))

    def test_correlated_lane_rows_do_not_inflate_episode_count(self):
        report = evaluate_protection_screen([source("z-lane"), source("a-lane")])
        self.assertTrue(report["candidates"])
        self.assertTrue(all(row["episodes_total"] == 1 for row in report["candidates"]))

    def test_missing_ordered_path_is_unsupported_not_zero_pnl(self):
        row = source()
        row["ordered_1s_prices"] = []
        report = evaluate_protection_screen([row])
        first = report["candidates"][0]["validation"]
        self.assertEqual(first["episodes_scored"], 0)
        self.assertIn("episode-1", first["missing_or_unsupported_episode_ids"])


if __name__ == "__main__":
    unittest.main()
