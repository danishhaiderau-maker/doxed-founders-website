import tempfile
import time
import unittest
import json
import pytest
from pathlib import Path

from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY
from research.research_v3_report import (
    build_safe_policy_genome_v3_report,
    execution_scoring_classification,
    join_pre_entry_feature_receipts,
)
from research import research_dashboard as dashboard
from research_dynamic_entry_policy import DEFAULT_CAUSAL_FEATURES, _causal_feature_key
from research_v3_store import V3EvidenceStore
from r3_test_assert import assert_r3_block


def _causal_join_input():
    return (
        {"episode_id": "e1", "opportunity_id": "o1", "signal_ts": 100},
        {"episode_id": "e1", "opportunity_id": "o1",
         "availability_boundary": "PRE_DECISION_ONLY", "captured_at_ts": 99,
         "capture_schema": "measured_feature_capture_v1",
         "features": {name: "OBSERVED_BUCKET" for name in DEFAULT_CAUSAL_FEATURES}},
    )


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), "-Infinity", None])
@pytest.mark.parametrize("field", ["signal_ts", "captured_at_ts", "observed_ts"])
def test_causal_join_rejects_invalid_timestamps(invalid, field):
    opportunity, receipt = _causal_join_input()
    if field == "signal_ts":
        opportunity[field] = invalid
    elif field == "captured_at_ts":
        receipt[field] = invalid
    else:
        receipt["features"]["atr_bucket"] = {"value": "HIGH", field: invalid}
    joined, coverage = join_pre_entry_feature_receipts([opportunity], [receipt])
    assert joined[0]["pre_entry_feature_status"] == "UNKNOWN"
    assert "atr_bucket" not in joined[0]["pre_entry_features"]
    assert coverage["dynamic_schema_complete_opportunities"] == 0
    if field == "observed_ts":
        assert "depth_bucket" in joined[0]["pre_entry_features"]
    else:
        assert joined[0]["pre_entry_features"] == {}


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), "NaN", " ", {}, []])
def test_causal_join_preserves_other_dimensions_when_one_value_invalid(invalid):
    opportunity, receipt = _causal_join_input()
    receipt["features"]["atr_bucket"] = invalid
    joined, coverage = join_pre_entry_feature_receipts([opportunity], [receipt])
    assert joined[0]["pre_entry_feature_status"] == "UNKNOWN"
    assert "atr_bucket" not in joined[0]["pre_entry_features"]
    assert joined[0]["pre_entry_features"]["depth_bucket"]["value"] == "OBSERVED_BUCKET"
    assert coverage["dynamic_schema_complete_opportunities"] == 0


def test_causal_join_missing_identities_cannot_match_empty_strings():
    opportunity, receipt = _causal_join_input()
    for row in (opportunity, receipt):
        row.pop("episode_id")
        row.pop("opportunity_id")
    joined, coverage = join_pre_entry_feature_receipts([opportunity], [receipt])
    assert joined[0]["pre_entry_features"] == {}
    assert joined[0]["pre_entry_feature_blockers"] == ["PRE_ENTRY_OPPORTUNITY_IDENTITY_MISSING"]
    assert coverage["dynamic_schema_complete_opportunities"] == 0


def test_scoring_classification_never_relabels_unknown_or_resolved_as_no_fill():
    unknown = {
        "outcome_state": "NO_FILL",
        "execution_classification": "UNKNOWN",
        "resolution": "ORDER_SUBMITTED_THEN_EXPIRED",
        "unknown_reason_codes": [
            "UNKNOWN_EXECUTION_LEDGER_MISSING",
            "UNKNOWN_EXECUTION_GRADE_MARKET_EVIDENCE_MISSING",
        ],
    }
    resolved = {
        "outcome_state": "NO_FILL",
        "entry_resolution": "NO_ORDER",
        "entry_resolution_terminal": True,
    }
    assert execution_scoring_classification(unknown) == "UNKNOWN"
    assert execution_scoring_classification(resolved) == "UNKNOWN"
    assert execution_scoring_classification({"outcome_state": "NO_FILL"}) == "NO_FILL"


class V3ReportTests(unittest.TestCase):
    def test_pre_entry_receipts_join_approve_reject_and_missing_stays_unknown(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            features = {
                "atr_bucket": "HIGH",
                "realized_volatility_bucket": "HIGH",
                "directional_spread_bucket": "WIDE",
                "depth_bucket": "DEEP",
                "liquidity_bucket": "LIQUID",
                "regime": "BULL",
                "direction": "LONG",
                "trend_strength_bucket": "STRONG",
            }
            for index, decision in enumerate(("ACCEPT", "REJECT", "REJECT"), 1):
                episode_id = f"episode-{index}"
                store.append("opportunity", {
                    "record_id": f"opportunity:{episode_id}",
                    "opportunity_id": f"opportunity:{episode_id}",
                    "episode_id": episode_id, "signal_ts": 1000 + index,
                })
                store.append("decision", {
                    "record_id": f"decision:{episode_id}",
                    "episode_id": episode_id, "policy_decision": decision,
                })
                if index < 3:
                    store.append("pre_entry_features", {
                        "record_id": f"pre-entry-features:{episode_id}",
                        "receipt_schema": "pre_entry_features_v1",
                        "availability_boundary": "PRE_DECISION_ONLY",
                        "capture_schema": "measured_feature_capture_v1",
                        "captured_at_ts": 999 + index,
                        "episode_id": episode_id,
                        "opportunity_id": f"opportunity:{episode_id}",
                        "features": features,
                    })

            report = build_safe_policy_genome_v3_report(data, reports, candidates=[])
            coverage = report["collection"]["pre_entry_feature_evidence"]
            self.assertEqual(coverage["opportunities"], 3)
            self.assertEqual(coverage["receipt_joined_opportunities"], 2)
            self.assertEqual(coverage["dynamic_schema_complete_opportunities"], 2)
            self.assertEqual(coverage["unknown_opportunities"], 1)
            self.assertEqual(
                coverage["blocker_counts"],
                {"PRE_ENTRY_FEATURE_RECEIPT_MISSING": 1},
            )
            self.assertIn("PRE_ENTRY_FEATURE_EVIDENCE_INCOMPLETE", report["blockers"])

            opportunities = [
                {"record_id": "opportunity:episode-1", "opportunity_id": "opportunity:episode-1",
                 "episode_id": "episode-1", "signal_ts": 1001},
                {"record_id": "opportunity:episode-3", "opportunity_id": "opportunity:episode-3",
                 "episode_id": "episode-3", "signal_ts": 1003},
            ]
            receipts = [
                {"episode_id": "episode-1", "opportunity_id": "opportunity:episode-1",
                 "availability_boundary": "PRE_DECISION_ONLY", "captured_at_ts": 1000,
                 "capture_schema": "measured_feature_capture_v1", "features": features},
            ]
            joined, _ = join_pre_entry_feature_receipts(opportunities, receipts)
            key, defects = _causal_feature_key(joined[0], tuple(DEFAULT_CAUSAL_FEATURES))
            self.assertEqual(defects, [])
            self.assertEqual(len(key), len(DEFAULT_CAUSAL_FEATURES))
            self.assertEqual(joined[1]["pre_entry_feature_status"], "UNKNOWN")
            self.assertEqual(joined[1]["pre_entry_features"], {})

    def test_empty_epoch_reports_deployed_policies_as_collecting_not_qualified(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            report = build_safe_policy_genome_v3_report(data, reports, candidates=[])

        deployed = report["deployed_policy_collection"]
        registry_epochs = list(dict.fromkeys(
            ACTIVE_TILE_REGISTRY[lane]["policy_epoch"] for lane in ACTIVE_TILE_ORDER
        ))
        self.assertEqual(deployed["policy_epochs"], registry_epochs)
        self.assertEqual(
            deployed["policy_epoch"],
            registry_epochs[0] if len(registry_epochs) == 1 else None,
        )
        self.assertEqual(deployed["policy_count"], len(ACTIVE_TILE_ORDER))
        self.assertFalse(deployed["qualification_allowed"])
        self.assertEqual(
            [row["policy_id"] for row in deployed["policies"]],
            [ACTIVE_TILE_REGISTRY[lane]["raw_policy_id"] for lane in ACTIVE_TILE_ORDER],
        )
        self.assertEqual(
            [row["policy_signature"] for row in deployed["policies"]],
            [ACTIVE_TILE_REGISTRY[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER],
        )
        self.assertTrue(all(row["qualification_status"] == "NOT_QUALIFIED" for row in deployed["policies"]))

    def test_persisted_ranking_is_bounded_and_summarizes_blocked_candidates(self):
        candidates = [
            {
                "policy_id": f"blocked-{index}",
                "gates": {},
            }
            for index in range(150)
        ]
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            report = build_safe_policy_genome_v3_report(
                data, reports, candidates=candidates,
            )
            ranking = report["safe_policy_ranking"]
            self.assertNotIn("blocked", ranking)
            self.assertEqual(ranking["blocked_policy_count"], 150)
            self.assertEqual(ranking["ranked"], [])
            self.assertEqual(
                ranking["blocked_gate_counts"]["integrity_pass"], 150,
            )
            persisted = json.loads(
                (Path(reports) / "safe_policy_genome_v3_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("blocked", persisted["safe_policy_ranking"])

    def test_empty_report_is_truthful_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["status"], "V3_READY_FOR_FRESH_EPOCH")
            self.assertIsNone(report["number_one_strategy"])
            self.assertFalse(report["real_bitfinex_trading_allowed"])
            self.assertEqual(report["data_scope"], "SESSION")

    def test_report_counts_independent_episodes_not_decision_branches(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {"record_id": "o-1", "episode_id": "episode-1"})
            store.append("decision", {"record_id": "d-1", "episode_id": "episode-1", "primary_outcome": "REJECTED"})
            store.append("decision", {"record_id": "d-2", "episode_id": "episode-1", "primary_outcome": "ACCEPTED_UNFILLED"})
            store.append("lifecycle", {
                "record_id": "l-1", "episode_id": "episode-1", "terminal": True,
                "observation_status": "LEGACY_PAPER_CLOSE",
                "outcome_state": "PAPER_REALIZED", "net_pnl_usd": 2.0,
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["collection"]["independent_opportunities"], 1)
            self.assertEqual(report["collection"]["decision_branches"], 2)
            self.assertEqual(report["status"], "V3_COLLECTING")
            self.assertEqual(report["data_scope"], "FRESH-COLLECTION")
            self.assertEqual(report["collection"]["outcome_states"], {"REALIZED_PROFIT": 1})

    def test_pre_signal_context_does_not_mature_terminal_market_path_gate(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            context_ref = store.put_market_segment(
                source="LIVE_MICROSTRUCTURE_1S_PRE_SIGNAL", symbol="BTCUSD",
                timeframe="1s", start_ts=998, end_ts=1000,
                rows=[{"ts": 998, "price": 100}, {"ts": 1000, "price": 101}],
            )
            store.append("market_segment", {
                "record_id": "context-1", "episode_id": "episode-1",
                "context_role": "PRE_SIGNAL_ONLY", "segment_ref": context_ref,
                "coverage": {"future_exit_path_included": False},
            })

            report = build_safe_policy_genome_v3_report(data, reports)
            collection = report["collection"]
            # R3 rewrite (HM #873 a1): market_segment input is invalid; assert BLOCK, not legacy success.
            assert_r3_block(report, 'report')


if __name__ == "__main__":
    unittest.main()
