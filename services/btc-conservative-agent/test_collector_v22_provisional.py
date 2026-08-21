import json
import os
import tempfile
import unittest
from unittest import mock

from collector_v22_provisional import PROVISIONAL_STORE_FILE, load_provisional_events, remove_provisional_event, upsert_provisional_event


class ProvisionalStoreTests(unittest.TestCase):
    def test_restart_load_preserves_source_and_epoch(self):
        with tempfile.TemporaryDirectory() as root:
            source = {"trade_id": "evt-1", "created_ts_ts": 123.0, "status": "PENDING"}
            upsert_provisional_event("evt-1", source, epoch_id="epoch-a", data_dir=root)
            self.assertEqual(load_provisional_events(epoch_id="epoch-a", data_dir=root), {"evt-1": source})
            self.assertEqual(load_provisional_events(epoch_id="epoch-b", data_dir=root), {})

    def test_retry_is_idempotent_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            upsert_provisional_event("evt-1", {"trade_id": "evt-1", "status": "PENDING"}, epoch_id="epoch-a", data_dir=root)
            upsert_provisional_event("evt-1", {"trade_id": "evt-1", "status": "FILLED"}, epoch_id="epoch-a", data_dir=root)
            loaded = load_provisional_events(epoch_id="epoch-a", data_dir=root)
            self.assertEqual(list(loaded), ["evt-1"])
            self.assertEqual(loaded["evt-1"]["status"], "FILLED")

    def test_maturation_rebuild_cannot_erase_causal_identity(self):
        with tempfile.TemporaryDirectory() as root:
            upsert_provisional_event("cont-1", {
                "trade_id": "cont-1", "shared_ai_call_id": "scan-1",
                "created_ts_ts": 1000.0, "research_feature_snapshot": {"rsi": 42},
                "status": "PENDING",
            }, epoch_id="epoch-a", data_dir=root)
            receipt = upsert_provisional_event("cont-1", {
                "trade_id": "cont-1", "shared_ai_call_id": None,
                "created_ts_ts": None, "research_feature_snapshot": {},
                "status": "FILLED",
            }, epoch_id="epoch-a", data_dir=root)
            loaded = load_provisional_events(epoch_id="epoch-a", data_dir=root)["cont-1"]
            self.assertEqual(loaded["shared_ai_call_id"], "scan-1")
            self.assertEqual(loaded["created_ts_ts"], 1000.0)
            self.assertEqual(loaded["research_feature_snapshot"], {"rsi": 42})
            self.assertEqual(loaded["status"], "FILLED")
            self.assertEqual(receipt["episode_id"], "episode-" + __import__("hashlib").sha256(b"shared:BTCUSD:UNKNOWN:scan-1").hexdigest()[:20])

    def test_remove_is_explicit_and_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            upsert_provisional_event("evt-1", {"trade_id": "evt-1"}, epoch_id="epoch-a", data_dir=root)
            self.assertTrue(remove_provisional_event("evt-1", data_dir=root))
            self.assertFalse(remove_provisional_event("evt-1", data_dir=root))
            self.assertEqual(load_provisional_events(data_dir=root), {})

    def test_failed_atomic_replace_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            upsert_provisional_event("evt-1", {"trade_id": "evt-1"}, epoch_id="epoch-a", data_dir=root)
            path = os.path.join(root, PROVISIONAL_STORE_FILE)
            with open(path, "rb") as handle:
                before = handle.read()
            with mock.patch("collector_v22_provisional.os.replace", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    upsert_provisional_event("evt-2", {"trade_id": "evt-2"}, epoch_id="epoch-a", data_dir=root)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), before)
            self.assertEqual(set(json.loads(before)["events"]), {"evt-1"})

    def test_corrupt_store_fails_closed_instead_of_erasing_recovery_state(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, PROVISIONAL_STORE_FILE)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"events":')
            with self.assertRaisesRegex(ValueError, "corrupt provisional event store"):
                load_provisional_events(data_dir=root)
            with self.assertRaisesRegex(ValueError, "corrupt provisional event store"):
                upsert_provisional_event("evt-2", {"trade_id": "evt-2"}, epoch_id="epoch-a", data_dir=root)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), '{"events":')


if __name__ == "__main__":
    unittest.main()
