import unittest

import pandas as pd

import analyzer_research_engine_v62 as analyzer


class AnalysisEligibilityGateTests(unittest.TestCase):
    def setUp(self):
        self.original_loader = analyzer._load_jsonl_by_trade_id

    def tearDown(self):
        analyzer._load_jsonl_by_trade_id = self.original_loader

    def test_only_complete_clean_policy_rows_are_allowed(self):
        analyzer._load_jsonl_by_trade_id = lambda _path: {
            "eligible": {
                "analysis_eligibility_schema": "analysis_eligibility_v1",
                "analysis_eligible": True,
                "policy_snapshot_complete": True,
                "replay_complete": True,
                "analysis_exclusion_reasons": [],
            },
            "absence": {
                "analysis_eligibility_schema": "analysis_eligibility_v1",
                "analysis_eligible": False,
                "policy_snapshot_complete": True,
                "replay_complete": True,
                "analysis_exclusion_reasons": ["TERMINAL_PROVENANCE_EXCLUDED"],
            },
            "legacy": {},
        }
        frame = pd.DataFrame(
            [{"trade_id": "eligible"}, {"trade_id": "absence"}, {"trade_id": "legacy"}]
        )

        filtered = analyzer._filter_policy_analysis_df(frame, "test")
        self.assertEqual(filtered["trade_id"].tolist(), ["eligible"])

        replays = analyzer._filter_policy_analysis_replays(
            {"eligible": {"ticks": [1]}, "absence": {"ticks": [1]}, "legacy": {"ticks": [1]}},
            "test",
        )
        self.assertEqual(set(replays), {"eligible"})

    def test_missing_trade_identity_fails_closed(self):
        analyzer._load_jsonl_by_trade_id = lambda _path: {}
        frame = pd.DataFrame([{"net_pnl_usd": 1.0}])
        self.assertTrue(analyzer._filter_policy_analysis_df(frame, "test").empty)


if __name__ == "__main__":
    unittest.main()
