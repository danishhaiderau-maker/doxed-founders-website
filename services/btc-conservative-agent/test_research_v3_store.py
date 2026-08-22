import json
import tempfile
import threading
import unittest
from pathlib import Path

from research_v3_store import V3EvidenceStore


class ResearchV3StoreTests(unittest.TestCase):
    def test_normalized_ledgers_are_idempotent_and_epoch_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            first = store.append("opportunity", {"record_id": "opp-1", "episode_id": "episode-1"})
            second = store.append("opportunity", {"record_id": "opp-1", "episode_id": "episode-1"})
            self.assertTrue(first["written"])
            self.assertTrue(second["duplicate"])
            row = json.loads(store.ledger_path("opportunity").read_text().strip())
            self.assertEqual(row["epoch_id"], "epoch-v3-test")
            self.assertEqual(row["ledger"], "opportunity")

    def test_concurrent_writers_do_not_duplicate_or_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            threads = [threading.Thread(target=store.append, args=("decision", {"record_id": f"d-{i}", "episode_id": "e"})) for i in range(32)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            lines = store.ledger_path("decision").read_text().splitlines()
            self.assertEqual(len(lines), 32)
            self.assertEqual(len({json.loads(line)["record_id"] for line in lines}), 32)
            self.assertTrue(store.verify()["passed"])

    def test_market_segments_are_content_addressed_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            args = dict(source="BITFINEX", symbol="tBTCF0:USTF0", timeframe="1s", start_ts=1, end_ts=2, rows=[{"ts": 1, "bid": 100, "ask": 101}])
            a = store.put_market_segment(**args)
            b = store.put_market_segment(**args)
            self.assertEqual(a["sha256"], b["sha256"])
            self.assertEqual(store.verify()["market_segment_count"], 1)

    def test_truncated_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            path = store.ledger_path("execution")
            path.write_text('{"record_id":"broken"}', encoding="utf-8")
            report = store.verify()
            self.assertFalse(report["passed"])
            self.assertIn("TRUNCATED_JSONL_LINE", report["defects"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
