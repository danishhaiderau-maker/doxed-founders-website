import unittest

import pandas as pd

import analyzer_research_engine_v62 as analyzer
from research.analysis_eligibility import (
    BITFINEX_COPY_FIDELITY,
    COPY_ONLY_EXCHANGE_FILLS,
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
                "required_entry_horizons_complete": True,
                "bitfinex_evidence": {
                    "linkage_complete": True,
                    "quantity_evidence_complete": True,
                    "order_ack_history_complete": True,
                    "stop_evidence_complete": True,
                    "source_snapshot_evidence_complete": True,
                    "reconciliation_complete": True,
                    "cost_evidence_complete": True,
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
            "SHOWCASE_FLAT_FAILSAFE",
            "EXIT_ONLY_PENDING_CANCEL_PARTIAL_FILL",
        ):
            result = classify_row({**base, "terminal_provenance": provenance})
            self.assertFalse(result["eligible"][SHOWCASE_STRATEGY])
            self.assertFalse(result["eligible"][BITFINEX_COPY_FIDELITY])
            self.assertFalse(result["eligible"][REAL_COPY_PARAMETER_OPTIMISATION])

    def test_unsupported_flat_book_and_late_fill_cleanup_have_explicit_reasons(self):
        base = {
            "trade_id": "excluded-terminal",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
        }
        flat = classify_row({**base, "terminal_provenance": "SHOWCASE_FLAT_FAILSAFE"})
        late = classify_row({**base, "terminal_provenance": "LATE_FILL_CLEANUP"})
        self.assertIn(
            "UNSUPPORTED_FLAT_BOOK_EXIT",
            flat["exclusion_reasons"][SHOWCASE_STRATEGY],
        )
        self.assertIn(
            "LATE_FILL_CLEANUP",
            late["exclusion_reasons"][SHOWCASE_STRATEGY],
        )

    def test_missing_trade_identity_fails_closed(self):
        analyzer._load_jsonl_by_trade_id = lambda _path: {}
        frame = pd.DataFrame([{"net_pnl_usd": 1.0}])
        self.assertTrue(analyzer._filter_policy_analysis_df(frame, "test").empty)

    def test_mirror_diff_stale_no_exposure_is_excluded_negative_evidence(self):
        row = {
            "trade_id": "cont-negative-evidence",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
            "terminal_provenance": "STALE_NO_EXPOSURE",
            "lifecycle_events": [
                {"event_type": "MIRROR_DIFF"},
                {"event_type": "STALE_NO_EXPOSURE"},
            ],
        }
        result = classify_row(row)
        for cohort in (
            SHOWCASE_STRATEGY,
            BITFINEX_COPY_FIDELITY,
            REAL_COPY_PARAMETER_OPTIMISATION,
        ):
            self.assertFalse(result["eligible"][cohort])
            self.assertIn(
                "MIRROR_DIFF_STALE_NO_EXPOSURE",
                result["exclusion_reasons"][cohort],
            )

    def test_mirror_diff_stale_is_explicit_without_terminal_provenance(self):
        result = classify_row({
            "trade_id": "cont-negative-no-terminal",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
            "lifecycle_events": [
                {"event_type": "MIRROR_DIFF"},
                {"event_type": "STALE_NO_EXPOSURE"},
            ],
        })
        self.assertIn(
            "MIRROR_DIFF_STALE_NO_EXPOSURE",
            result["exclusion_reasons"][BITFINEX_COPY_FIDELITY],
        )

    def test_showcase_only_relay_paused_is_tagged_and_not_real_copy_opt(self):
        result = classify_row({
            "trade_id": "cont-e33a",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
            "terminal_provenance": "THESIS_FAST_CUT",
            "showcase_only_relay_paused": True,
        })
        self.assertIn(
            "SHOWCASE_ONLY_RELAY_PAUSED",
            result["exclusion_reasons"][BITFINEX_COPY_FIDELITY],
        )
        self.assertFalse(result["eligible"][REAL_COPY_PARAMETER_OPTIMISATION])

    def test_copy_only_source_unconfirmed_does_not_drop_authenticated_scenario_c_pnl(self):
        row = {
            "trade_id": "cont-57bb",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "policy_comparability_key": "v5:baseline",
            "replay_complete": True,
            "terminal_provenance": "EXCHANGE_OBSERVED_TERMINAL",
            "actual_bitfinex_realized_pnl_usd": 0.79,
            "required_post_exit_horizons_complete": True,
            "required_entry_horizons_complete": True,
            "copy_fill_observed": {
                "classification": "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED",
            },
            "bitfinex_evidence": {
                "linkage_complete": True,
                "quantity_evidence_complete": True,
                "order_ack_history_complete": True,
                "stop_evidence_complete": True,
                "source_snapshot_evidence_complete": True,
                "reconciliation_complete": True,
                "cost_evidence_complete": True,
                "negative_events": [{"event": "MIRROR_DIFF"}],
            },
        }
        result = classify_row(row)
        self.assertIn(
            "COPY_ONLY_SOURCE_UNCONFIRMED",
            result["exclusion_reasons"][SHOWCASE_STRATEGY],
        )
        self.assertNotIn(
            "COPY_ONLY_SOURCE_UNCONFIRMED",
            result["exclusion_reasons"][REAL_COPY_PARAMETER_OPTIMISATION],
        )
        self.assertNotIn(
            "MIRROR_DIFF",
            result["exclusion_reasons"][REAL_COPY_PARAMETER_OPTIMISATION],
        )
        self.assertFalse(result["eligible"][REAL_COPY_PARAMETER_OPTIMISATION])
        self.assertTrue(result["eligible"][COPY_ONLY_EXCHANGE_FILLS])
        self.assertTrue(result["eligible"][BITFINEX_COPY_FIDELITY])

    def test_stale_limit_evidence_is_tagged_on_fidelity_cohort(self):
        result = classify_row({
            "trade_id": "cont-stale-limit",
            "policy_snapshot_complete": True,
            "policy_version": 5,
            "replay_complete": True,
            "terminal_provenance": "SOURCE_UNCONFIRMED",
            "source_order_market_evidence": {
                "original_limit_price": 63486.52,
                "current_limit_price": 63504.89,
                "limit_generation": 1,
                "latest_observation": {"limit_price": 63486.52, "limit_generation": 0},
            },
            "bitfinex_evidence": {
                "linkage_complete": True,
                "quantity_evidence_complete": True,
                "order_ack_history_complete": True,
                "stop_evidence_complete": True,
                "source_snapshot_evidence_complete": True,
                "reconciliation_complete": True,
            },
        })
        self.assertIn(
            "STALE_LIMIT_EVIDENCE",
            result["exclusion_reasons"][BITFINEX_COPY_FIDELITY],
        )


if __name__ == "__main__":
    unittest.main()
