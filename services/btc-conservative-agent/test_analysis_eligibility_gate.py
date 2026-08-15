import unittest

import pandas as pd

import analyzer_research_engine_v62 as analyzer
from research.analysis_eligibility import (
    BITFINEX_COPY_FIDELITY,
    REAL_COPY_PARAMETER_OPTIMISATION,
    SHOWCASE_STRATEGY,
    classify_row,
)


class AnalysisEligibilityGateTests(unittest.TestCase):
    def setUp(self):
        self.original_loader = analyzer._load_jsonl_by_trade_id

    def tearDown(self):
        analyzer._load_jsonl_by_trade_id = self.original_loader

    def test_only_fully_qualified_rows_are_allowed_for_parameter_optimisation(self):
        analyzer._load_jsonl_by_trade_id = lambda _path: {
            "eligible": {
                "trade_id": "eligible",
                "policy_snapshot_complete": True,
                "policy_version": 5,
                "policy_comparability_key": "v5:baseline",
                "replay_complete": True,
                "terminal_provenance": "SOURCE_CONFIRMED",
                "participant_id": "participant-1",
                "actual_bitfinex_realized_pnl_usd": 1.25,
                "required_post_exit_horizons_complete": True,
                "bitfinex_evidence": {
                    "quantity_evidence_complete": True,
                    "order_ack_history_complete": True,
                    "stop_evidence_complete": True,
                    "source_snapshot_evidence_complete": True,
                    "reconciliation_complete": True,
                },
            },
            "absence": {
                "trade_id": "absence",
                "policy_snapshot_complete": True,
                "policy_version": 5,
                "replay_complete": True,
                "terminal_provenance": "SOURCE_ABSENCE_FALLBACK",
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

    def test_showcase_strategy_does_not_require_bitfinex_linkage(self):
        row = {
            "trade_id": "showcase-only",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
            "terminal_provenance": "SOURCE_CONFIRMED",
        }
        result = classify_row(row)
        self.assertTrue(result["eligible"][SHOWCASE_STRATEGY])
        self.assertFalse(result["eligible"][BITFINEX_COPY_FIDELITY])
        self.assertFalse(result["eligible"][REAL_COPY_PARAMETER_OPTIMISATION])
        self.assertIn(
            "BITFINEX_LINKAGE_MISSING",
            result["exclusion_reasons"][BITFINEX_COPY_FIDELITY],
        )

    def test_fallback_manual_and_emergency_rows_fail_closed(self):
        base = {
            "trade_id": "bad-terminal",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
        }
        for provenance in (
            "SOURCE_ABSENCE_FALLBACK",
            "MANUAL_CLOSE",
            "EMERGENCY_CLOSE",
        ):
            result = classify_row({**base, "terminal_provenance": provenance})
            self.assertFalse(result["eligible"][SHOWCASE_STRATEGY])
            self.assertFalse(result["eligible"][BITFINEX_COPY_FIDELITY])
            self.assertFalse(result["eligible"][REAL_COPY_PARAMETER_OPTIMISATION])

    def test_missing_trade_identity_fails_closed(self):
        analyzer._load_jsonl_by_trade_id = lambda _path: {}
        frame = pd.DataFrame([{"net_pnl_usd": 1.0}])
        self.assertTrue(analyzer._filter_policy_analysis_df(frame, "test").empty)


if __name__ == "__main__":
    unittest.main()
