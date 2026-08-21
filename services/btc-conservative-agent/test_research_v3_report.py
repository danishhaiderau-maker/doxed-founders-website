import tempfile
import unittest

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

    def test_report_counts_independent_episodes_not_decision_branches(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            store = V3EvidenceStore(data, epoch_id="epoch-v3")
            store.append("opportunity", {"record_id": "o-1", "episode_id": "episode-1"})
            store.append("decision", {"record_id": "d-1", "episode_id": "episode-1", "primary_outcome": "REJECTED"})
            store.append("decision", {"record_id": "d-2", "episode_id": "episode-1", "primary_outcome": "ACCEPTED_UNFILLED"})
            report = build_safe_policy_genome_v3_report(data, reports)
            self.assertEqual(report["collection"]["independent_opportunities"], 1)
            self.assertEqual(report["collection"]["decision_branches"], 2)
            self.assertEqual(report["status"], "V3_COLLECTING")

    def test_dashboard_api_and_page_are_fail_closed(self):
        original = dashboard._read_json
        try:
            dashboard._read_json = lambda name, default=None: {} if name == dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE else original(name, default)
            client = dashboard.app.test_client()
            payload = client.get("/api/safe-policy-genome-v3").get_json()
            self.assertEqual(payload["status"], "V3_REPORT_NOT_GENERATED")
            self.assertFalse(payload["real_bitfinex_trading_allowed"])
            page = client.get("/safe-policy-genome-v3")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Safe Policy Genome", page.data)
        finally:
            dashboard._read_json = original


if __name__ == "__main__":
    unittest.main()
