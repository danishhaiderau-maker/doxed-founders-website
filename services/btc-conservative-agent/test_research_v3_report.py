import tempfile
import time
import unittest
import json
import pytest
from pathlib import Path

from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY
from research.research_v3_report import (
    build_safe_policy_genome_v3_report,
    join_pre_entry_feature_receipts,
)
from research import research_dashboard as dashboard
from research_dynamic_entry_policy import DEFAULT_CAUSAL_FEATURES, _causal_feature_key
from research_v3_store import V3EvidenceStore


def _causal_join_input():
    return (
        {"episode_id": "e1", "opportunity_id": "o1", "signal_ts": 100},
        {"episode_id": "e1", "opportunity_id": "o1",
         "availability_boundary": "PRE_DECISION_ONLY", "captured_at_ts": 99,
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
                 "features": features},
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
        self.assertEqual(deployed["policy_epoch"], "v31-analyzer-hypothesis-paper-v1")
        self.assertEqual(deployed["policy_count"], 5)
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
            self.assertEqual(collection["pre_signal_context_segments"], 1)
            self.assertEqual(collection["terminal_path_market_segments"], 0)
            self.assertEqual(collection["market_segments"], 0)
            self.assertEqual(collection["market_segment_ledger_rows"], 1)
            self.assertEqual(collection["market_segment_objects_verified"], 1)

    def test_shared_ai_call_children_are_one_independent_cluster(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
                "grouping_basis": "SHARED_AI_CALL", "shared_ai_call_id": "scan-1",
            })
            for index, lane in enumerate(("PATIENT", "CONTINUOUS", "TILE_3", "TILE_4"), 1):
                store.append("decision", {
                    "record_id": f"d-{index}", "episode_id": "episode-1",
                    "decision_stage": "LANE_POLICY_VERDICT", "research_lane": lane,
                    "policy_id": lane, "policy_signature": f"sig-{index}",
                    "policy_epoch_id": "pe-1", "policy_decision": "REJECT",
                })

            report = build_safe_policy_genome_v3_report(data, reports)
            collection = report["collection"]
            self.assertEqual(collection["independent_opportunities"], 1)
            self.assertEqual(collection["decision_branches"], 4)
            self.assertEqual(collection["independent_cluster_count"], 1)
            self.assertEqual(collection["correlated_child_decision_count"], 4)
            self.assertEqual(
                collection["independence_grouping_basis"],
                "SHARED_AI_CALL_WITH_EPISODE_ID_FALLBACK",
            )
            self.assertEqual(collection["independence_clusters"], [{
                "cluster_id": "scan-1",
                "grouping_basis": "SHARED_AI_CALL",
                "episode_id": "episode-1",
                "shared_ai_call_id": "scan-1",
                "child_decision_count": 4,
                "child_lane_count": 4,
            }])

    def test_report_blocks_overdue_lane_order_orphan_and_accepts_resolutions(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {"record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1})
            for lane, signature in (("CONTINUOUS", "sig-c"), ("PATIENT", "sig-p")):
                store.append("decision", {
                    "record_id": f"d-{lane}", "episode_id": "episode-1",
                    "decision_stage": "LANE_POLICY_VERDICT", "research_lane": lane,
                    "policy_id": lane, "policy_signature": signature, "policy_epoch_id": "pe-1",
                    "order_intent_expected": True, "resolution_deadline_ts": 2,
                })
            first = build_safe_policy_genome_v3_report(data, reports)
            integrity = first["collection"]["entry_resolution_integrity"]
            self.assertEqual(integrity["overdue_orphan"], 2)
            self.assertIn("ORPHAN_EXPECTED_ORDER", first["blockers"])
            self.assertIsNone(first["number_one_strategy"])
            self.assertEqual(first["qualification"], "BLOCKED_ORDER_RESOLUTION_INTEGRITY")

            store.append("order_intent", {
                "record_id": "i-c", "episode_id": "episode-1", "research_lane": "CONTINUOUS",
                "policy_id": "CONTINUOUS", "policy_signature": "sig-c", "policy_epoch_id": "pe-1",
            })
            store.append("lifecycle", {
                "record_id": "l-p-no-order", "episode_id": "episode-1", "research_lane": "PATIENT",
                "policy_id": "PATIENT", "policy_signature": "sig-p", "policy_epoch_id": "pe-1",
                "resolution_scope": "LANE_ENTRY", "entry_resolution": "NO_ORDER",
                "entry_resolution_terminal": True, "terminal": True, "outcome_state": "NO_TRADE",
            })
            second = build_safe_policy_genome_v3_report(data, reports)
            integrity = second["collection"]["entry_resolution_integrity"]
            self.assertEqual(integrity["submitted"], 1)
            self.assertEqual(integrity["terminal_no_order"], 1)
            self.assertEqual(integrity["overdue_orphan"], 0)
            self.assertNotIn("ORPHAN_EXPECTED_ORDER", second["blockers"])

    def test_expired_order_receipt_repairs_intent_edge_but_keeps_execution_unknown(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1,
                "shared_ai_call_id": "scan-1",
            })
            store.append("decision", {
                "record_id": "d-1", "episode_id": "episode-1",
                "shared_ai_call_id": "scan-1", "decision_stage": "LANE_POLICY_VERDICT",
                "research_lane": "FAMILY_ATR_TRAIL", "policy_id": "trail",
                "policy_signature": "sig-trail", "policy_epoch_id": "pe-1",
                "order_intent_expected": True, "resolution_deadline_ts": 2,
            })
            Path(data, "expired_orders_3factor.csv").write_text(
                "time,trade_id,shared_ai_call_id,research_lane,reason,expired_ts,touched_limit\n"
                "2026-01-01T00:30:00Z,ftr-1,scan-1,FAMILY_ATR_TRAIL,"
                "SIGNAL_TTL_EXPIRED,1801,True\n",
                encoding="utf-8",
            )

            report = build_safe_policy_genome_v3_report(data, reports)
            integrity = report["collection"]["entry_resolution_integrity"]
            self.assertTrue(integrity["passed"])
            self.assertEqual(integrity["submitted"], 1)
            self.assertEqual(integrity["recovered_expired_order"], 1)
            self.assertEqual(integrity["overdue_orphan"], 0)
            recovered = integrity["recovered_expired_orders"]
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["trade_id"], "ftr-1")
            self.assertEqual(recovered[0]["resolution"], "ORDER_SUBMITTED_THEN_EXPIRED")
            self.assertEqual(recovered[0]["execution_classification"], "UNKNOWN")
            self.assertIn(
                "UNKNOWN_EXECUTION_GRADE_MARKET_EVIDENCE_MISSING",
                recovered[0]["unknown_reason_codes"],
            )
            self.assertNotIn("ORPHAN_EXPECTED_ORDER", report["blockers"])

    def test_expired_order_recovery_rejects_ambiguous_lane_receipts(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1,
                "shared_ai_call_id": "scan-1",
            })
            for suffix in ("a", "b"):
                store.append("decision", {
                    "record_id": f"d-{suffix}", "episode_id": "episode-1",
                    "shared_ai_call_id": "scan-1", "decision_stage": "LANE_POLICY_VERDICT",
                    "research_lane": "SAME_LANE", "policy_id": suffix,
                    "policy_signature": f"sig-{suffix}", "policy_epoch_id": "pe-1",
                    "order_intent_expected": True, "resolution_deadline_ts": 2,
                })
            Path(data, "expired_orders_3factor.csv").write_text(
                "time,trade_id,shared_ai_call_id,research_lane,reason\n"
                "2026-01-01T00:30:00Z,trade-1,scan-1,SAME_LANE,SIGNAL_TTL_EXPIRED\n",
                encoding="utf-8",
            )

            integrity = build_safe_policy_genome_v3_report(
                data, reports,
            )["collection"]["entry_resolution_integrity"]
            self.assertFalse(integrity["passed"])
            self.assertEqual(integrity["recovered_expired_order"], 0)
            self.assertEqual(integrity["overdue_orphan"], 2)

    def test_report_exposes_rejected_and_disabled_lane_decisions_separately(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            common = {
                "episode_id": "episode-1", "decision_stage": "LANE_POLICY_VERDICT",
                "policy_epoch_id": "pe-1",
            }
            store.append("decision", {
                **common, "record_id": "d-cont", "research_lane": "CONTINUOUS",
                "policy_id": "CONTINUOUS", "policy_signature": "sig-cont",
                "policy_decision": "ACCEPT", "outcome_state": "CENSORED",
                "execution_disposition": "ORDER_ELIGIBLE",
            })
            store.append("decision", {
                **common, "record_id": "d-patient", "research_lane": "OFFSET_029_ATR_TP_25",
                "policy_id": "PATIENT", "policy_signature": "sig-patient",
                "policy_decision": "ACCEPT", "outcome_state": "NO_TRADE",
                "execution_disposition": "LANE_DISABLED_NO_ORDER",
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["collection"]["decision_outcomes"], {
                "CENSORED": 1, "NO_TRADE": 1,
            })
            self.assertEqual(report["collection"]["decision_dispositions"], {
                "LANE_DISABLED_NO_ORDER": 1, "ORDER_ELIGIBLE": 1,
            })
            self.assertEqual(report["collection"]["lane_decision_outcomes"], {
                "CONTINUOUS": {"CENSORED": 1},
                "OFFSET_029_ATR_TP_25": {"NO_TRADE": 1},
            })
            self.assertFalse(report["epoch_scope"]["contamination_detected"])

    def test_report_separates_paper_observation_from_relay_capability(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            store.append("order_intent", {
                "record_id": "i-1", "episode_id": "episode-1", "event_id": "event-1",
                "research_lane": "CONTINUOUS", "policy_id": "CONTINUOUS",
                "policy_signature": "sig-cont", "policy_epoch_id": "pe-cont",
                "paper_policy_spec": {"relay_eligible": True},
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            identities = report["collection"]["effective_paper_execution_identities"]
            self.assertEqual(len(identities), 1)
            self.assertEqual(identities[0]["effective_execution_mode"], "PAPER_OBSERVED")
            self.assertTrue(identities[0]["live_relay_capable"])
            self.assertIn("does not authorize live relay", identities[0]["relay_capability_note"])
            self.assertFalse(report["real_bitfinex_trading_allowed"])

    def test_dashboard_api_and_page_are_fail_closed(self):
        original = dashboard._current_generation_report
        try:
            dashboard._current_generation_report = lambda name: {
                "schema": "current_generation_report_unavailable_v1",
                "status": "REPORT_NOT_IN_CURRENT_GENERATION",
                "qualification": "NO_SAFE_QUALIFIED_POLICY",
                "live_policy_change_allowed": False,
                "real_bitfinex_trading_allowed": False,
                "blockers": ["REPORT_NOT_IN_CURRENT_GENERATION", name],
                "report_unavailable": True,
                "collection": {},
                "candidate_screen": {},
                "safe_policy_ranking": {},
            }
            dashboard._API_RESPONSE_CACHE.clear()
            client = dashboard.app.test_client()
            payload = client.get("/api/safe-policy-genome-v3").get_json()
            self.assertEqual(payload["status"], "REPORT_NOT_IN_CURRENT_GENERATION")
            self.assertIn("REPORT_NOT_IN_CURRENT_GENERATION", payload["blockers"])
            self.assertFalse(payload["real_bitfinex_trading_allowed"])
            page = client.get("/safe-policy-genome-v3")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Safe Policy Genome", page.data)
        finally:
            dashboard._current_generation_report = original

    def test_report_excludes_foreign_epoch_and_pre_reset_rows(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            Path(data, "research_session.json").write_text(json.dumps({"fresh_collection_start_time": 1000}), encoding="utf-8")
            old = V3EvidenceStore(data, epoch_id="epoch-old")
            old.append("opportunity", {"record_id": "o-old", "episode_id": "episode-old", "signal_ts": 900})
            current = V3EvidenceStore(data, epoch_id="epoch-current")
            # A second store instance must finish the existing-ledger index
            # handshake before it can append; rejected writes are not fixtures.
            bootstrap = current.advance_emergency_idempotency_bootstrap("opportunity")
            self.assertTrue(bootstrap["complete"], bootstrap)
            current.append("opportunity", {"record_id": "o-rebuilt", "episode_id": "episode-rebuilt", "signal_ts": 950})
            current.append("opportunity", {"record_id": "o-fresh", "episode_id": "episode-fresh", "signal_ts": 1100})
            current.append("decision", {"record_id": "d-rebuilt", "episode_id": "episode-rebuilt", "primary_outcome": "REJECTED"})
            current.append("decision", {"record_id": "d-fresh", "episode_id": "episode-fresh", "primary_outcome": "REJECTED"})
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["epoch_id"], "epoch-current")
            self.assertEqual(report["collection"]["independent_opportunities"], 1)
            self.assertEqual(report["collection"]["decision_branches"], 1)
            self.assertEqual(report["status"], "V3_EPOCH_CONTAMINATION_BLOCKED")
            self.assertEqual(report["epoch_scope"]["excluded_stale_or_foreign_rows"], 2)
            self.assertIn("MIXED_OR_PRE_CUTOFF_V3_EVIDENCE_EXCLUDED", report["blockers"])

    def test_report_dedupes_fallback_alias_of_same_causal_signal(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            common = {"signal_ts": 1000.0, "symbol": "BTCUSD", "raw_direction": "LONG"}
            store.append("opportunity", {
                "record_id": "o-fallback", "episode_id": "episode-fallback",
                "grouping_basis": "TIME_DIRECTION_SYMBOL_FALLBACK", **common,
            })
            store.append("opportunity", {
                "record_id": "o-shared", "episode_id": "episode-shared",
                "grouping_basis": "SHARED_AI_CALL", "shared_ai_call_id": "scan-1", **common,
            })
            store.append("lifecycle", {
                "record_id": "l-shared", "episode_id": "episode-shared", "terminal": False,
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["collection"]["independent_opportunities"], 1)
            self.assertEqual(report["epoch_scope"]["excluded_identity_alias_rows"], 1)
            self.assertEqual(report["epoch_scope"]["identity_alias_episode_ids"], ["episode-fallback"])
            self.assertEqual(report["status"], "V3_EPOCH_CONTAMINATION_BLOCKED")
            self.assertIn("CAUSAL_IDENTITY_ALIAS_EXCLUDED", report["blockers"])

    def test_report_blocks_shared_call_split_by_symbol_alias(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            for suffix, symbol in (("venue", "TBTCF0:USTF0"), ("generic", "BTCUSD")):
                store.append("opportunity", {
                    "record_id": f"o-{suffix}", "episode_id": f"episode-{suffix}",
                    "signal_ts": 1000.0, "symbol": symbol, "raw_direction": "LONG",
                    "grouping_basis": "SHARED_AI_CALL", "shared_ai_call_id": "scan-same",
                })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["collection"]["independent_opportunities"], 1)
            self.assertEqual(report["epoch_scope"]["excluded_identity_alias_rows"], 1)
            self.assertIn("CAUSAL_IDENTITY_ALIAS_EXCLUDED", report["blockers"])

    def test_report_blocks_same_episode_policy_with_two_signatures(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            store.append("decision", {
                "record_id": "d-1", "episode_id": "episode-1", "decision_stage": "LANE_POLICY_VERDICT",
                "policy_id": "PATIENT", "policy_signature": "sig-decision", "policy_epoch_id": "pe-1",
            })
            store.append("order_intent", {
                "record_id": "i-1", "episode_id": "episode-1", "event_id": "event-1",
                "policy_id": "PATIENT", "policy_signature": "sig-order", "policy_epoch_id": "pe-2",
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertTrue(report["epoch_scope"]["policy_signature_divergence"])
            self.assertIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])

    def test_report_blocks_two_policy_ids_sharing_one_signature(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            for index, policy_id in enumerate(("CONTINUOUS", "PATIENT_CHASE"), 1):
                store.append("order_intent", {
                    "record_id": f"i-{index}", "episode_id": "episode-1",
                    "event_id": f"event-{index}", "policy_id": policy_id,
                    "policy_signature": "shared-wrong-signature",
                    "policy_epoch_id": "shared-wrong-epoch",
                })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["status"], "V3_EPOCH_CONTAMINATION_BLOCKED")
            self.assertEqual(report["epoch_scope"]["policy_signature_collisions"], {
                "shared-wrong-signature": ["CONTINUOUS", "PATIENT_CHASE"],
            })
            self.assertIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])

    def test_report_blocks_paper_scope_with_false_paper_identity(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            store.append("decision", {
                "record_id": "d-1", "episode_id": "episode-1",
                "decision_stage": "LANE_POLICY_VERDICT",
                "policy_id": "CONTINUOUS", "policy_signature": "sig-continuous",
                "policy_epoch_id": "pe-continuous",
                "policy_execution_scope": "PAPER_RESEARCH_ONLY",
                "paper_only": False,
                "paper_policy_spec": {"paper_only": False, "relay_eligible": True},
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["status"], "V3_EPOCH_CONTAMINATION_BLOCKED")
            self.assertEqual(report["epoch_scope"]["paper_world_contradiction_count"], 1)
            self.assertEqual(
                report["epoch_scope"]["paper_world_contradiction_rows"][0]["record_id"],
                "d-1",
            )
            self.assertIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])
            self.assertIsNone(report["number_one_strategy"])

    def test_report_blocks_execution_and_paper_lifecycle_without_lane_provenance(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
            })
            incomplete = {
                "episode_id": "episode-1", "event_id": "event-1",
                "policy_id": "PATIENT", "policy_signature": "sig-patient",
                "policy_epoch_id": "pe-patient",
            }
            store.append("execution", {"record_id": "e-1", **incomplete})
            store.append("lifecycle", {
                "record_id": "l-1", **incomplete,
                "observation_status": "PAPER_POSITION_CLOSED", "terminal": True,
            })
            blocked = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(blocked["epoch_scope"]["missing_policy_identity_rows"], 2)
            self.assertIn("POLICY_IDENTITY_CONTAMINATION", blocked["blockers"])
            self.assertIsNone(blocked["number_one_strategy"])

            complete = {
                **incomplete, "research_lane": "PATIENT",
                "shared_ai_call_id": "scan-1",
            }
            with tempfile.TemporaryDirectory() as clean_data, tempfile.TemporaryDirectory() as clean_reports:
                clean = V3EvidenceStore(clean_data, epoch_id="epoch-v3")
                clean.append("opportunity", {
                    "record_id": "o-1", "episode_id": "episode-1", "signal_ts": 1000,
                })
                clean.append("execution", {"record_id": "e-1", **complete})
                clean.append("lifecycle", {
                    "record_id": "l-1", **complete,
                    "observation_status": "PAPER_POSITION_CLOSED", "terminal": True,
                })
                accepted = build_safe_policy_genome_v3_report(clean_data, clean_reports)
                self.assertEqual(accepted["epoch_scope"]["missing_policy_identity_rows"], 0)
                self.assertNotIn("POLICY_IDENTITY_CONTAMINATION", accepted["blockers"])

    def test_report_treats_within_deadline_identity_join_as_pending(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            now = time.time()
            store.append("opportunity", {
                "record_id": "o-1", "episode_id": "episode-1", "signal_ts": now,
            })
            store.append("decision", {
                "record_id": "d-1", "episode_id": "episode-1",
                "decision_stage": "LANE_POLICY_VERDICT", "policy_id": "PATIENT",
                "policy_signature": "sig-patient", "policy_epoch_id": "pe-patient",
                "order_intent_expected": True, "resolution_deadline_ts": now + 300,
            })
            store.append("execution", {
                "record_id": "e-1", "episode_id": "episode-1", "event_id": "event-1",
                "policy_id": "PATIENT", "policy_signature": "sig-patient",
                "policy_epoch_id": "pe-patient",
            })
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["epoch_scope"]["missing_policy_identity_rows"], 0)
            self.assertEqual(report["epoch_scope"]["pending_policy_identity_rows"], 1)
            self.assertNotIn("POLICY_IDENTITY_CONTAMINATION", report["blockers"])
            self.assertIsNone(report["number_one_strategy"])


if __name__ == "__main__":
    unittest.main()
