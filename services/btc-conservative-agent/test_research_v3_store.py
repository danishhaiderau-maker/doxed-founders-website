import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from research_v3_store import V3EvidenceStore


class ResearchV3StoreTests(unittest.TestCase):
    def test_verify_reuses_signature_validated_ids_after_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-cache")
            store.append("opportunity", {"record_id": "opp-1"})

            with mock.patch.object(
                V3EvidenceStore,
                "_load_ids",
                wraps=V3EvidenceStore._load_ids,
            ) as load_ids:
                first = store.verify()
                first_calls = load_ids.call_count
                second = store.verify()

            self.assertTrue(first["passed"])
            self.assertEqual(second["ledger_counts"]["opportunity"], 1)
            self.assertEqual(load_ids.call_count, first_calls)

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

    def test_repeated_appends_reuse_the_durable_id_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            original = V3EvidenceStore._load_ids
            with mock.patch.object(V3EvidenceStore, "_load_ids", wraps=original) as load_ids:
                for index in range(10):
                    store.append("opportunity", {"record_id": f"opp-{index}", "episode_id": "episode-1"})
            self.assertEqual(load_ids.call_count, 1)

    def test_external_append_invalidates_cache_and_preserves_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            store.append("opportunity", {"record_id": "opp-1", "episode_id": "episode-1"})
            path = store.ledger_path("opportunity")
            external = {
                "schema": "research_evidence_v3",
                "ledger": "opportunity",
                "epoch_id": "epoch-v3-test",
                "record_id": "opp-external",
                "episode_id": "episode-2",
            }
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(external, sort_keys=True, separators=(",", ":")) + "\n")
            duplicate = store.append("opportunity", {"record_id": "opp-external", "episode_id": "episode-2"})
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_external_truncation_invalidates_cache_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            store.append("execution", {"record_id": "exec-1", "episode_id": "episode-1"})
            path = store.ledger_path("execution")
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write('{"record_id":"broken"}')
            with self.assertRaisesRegex(ValueError, "TRUNCATED_JSONL_LINE"):
                store.append("execution", {"record_id": "exec-2", "episode_id": "episode-2"})


if __name__ == "__main__":
    unittest.main()
