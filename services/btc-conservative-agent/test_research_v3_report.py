import tempfile
import time
import unittest
import json
from pathlib import Path

from research.research_v3_report import build_safe_policy_genome_v3_report
from research import research_dashboard as dashboard
from research_v3_store import V3EvidenceStore


class V3ReportTests(unittest.TestCase):
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
        original = dashboard._read_json
        try:
            dashboard._read_json = lambda name, default=None: {} if name == dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE else original(name, default)
            dashboard._API_RESPONSE_CACHE.clear()
            client = dashboard.app.test_client()
            payload = client.get("/api/safe-policy-genome-v3").get_json()
            self.assertEqual(payload["status"], "V3_REPORT_NOT_GENERATED")
            self.assertFalse(payload["real_bitfinex_trading_allowed"])
            page = client.get("/safe-policy-genome-v3")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Safe Policy Genome", page.data)
        finally:
            dashboard._read_json = original

    def test_report_excludes_foreign_epoch_and_pre_reset_rows(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            Path(data, "research_session.json").write_text(json.dumps({"fresh_collection_start_time": 1000}), encoding="utf-8")
            old = V3EvidenceStore(data, epoch_id="epoch-old")
            old.append("opportunity", {"record_id": "o-old", "episode_id": "episode-old", "signal_ts": 900})
            current = V3EvidenceStore(data, epoch_id="epoch-current")
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
