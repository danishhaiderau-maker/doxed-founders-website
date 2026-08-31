import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import research_v3_store
from research_v3_store import V3EvidenceStore, _segment_hash_cache


def _append_process(root, record_id, start, result_queue):
    start.wait()
    result_queue.put(V3EvidenceStore(root, epoch_id="epoch-multiprocess").append(
        "decision", {"record_id": record_id, "episode_id": record_id},
    ))


def _segment_process(root, start, result_queue):
    start.wait()
    result_queue.put(V3EvidenceStore(root, epoch_id="epoch-multiprocess").put_market_segment(
        source="BITFINEX", symbol="BTC", timeframe="2s", start_ts=1, end_ts=2,
        rows=[{"ts": 1, "bid": 100, "ask": 101}],
    ))


class ResearchV3StoreTests(unittest.TestCase):
    @staticmethod
    def _run_processes(target, args, count):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        result_queue = context.Queue()
        processes = [context.Process(target=target, args=(*args, start, result_queue)) for _ in range(count)]
        for process in processes:
            process.start()
        start.set()
        results = [result_queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            if process.is_alive():
                process.terminate()
                process.join()
            if process.exitcode != 0:
                raise AssertionError(f"child process failed with exit code {process.exitcode}")
        return results

    def test_multiprocess_same_record_id_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = self._run_processes(_append_process, (tmp, "same-id"), 8)
            rows = V3EvidenceStore(tmp, epoch_id="epoch-multiprocess").ledger_path("decision").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(sum(bool(result["written"]) for result in results), 1)
            self.assertEqual(sum(bool(result["duplicate"]) for result in results), 7)

    def test_multiprocess_distinct_rows_are_complete_and_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            result_queue = context.Queue()
            processes = [context.Process(
                target=_append_process, args=(tmp, f"distinct-{index}", start, result_queue),
            ) for index in range(12)]
            for process in processes:
                process.start()
            start.set()
            results = [result_queue.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(timeout=30)
                self.assertEqual(process.exitcode, 0)
            store = V3EvidenceStore(tmp, epoch_id="epoch-multiprocess")
            rows = [json.loads(line) for line in store.ledger_path("decision").read_text().splitlines()]
            self.assertEqual(len(rows), 12)
            self.assertEqual(len({row["record_id"] for row in rows}), 12)
            self.assertTrue(all(result["written"] for result in results))
            self.assertTrue(store.verify()["passed"])

    def test_multiprocess_same_market_segment_is_one_valid_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = self._run_processes(_segment_process, (tmp,), 8)
            self.assertEqual(len({result["sha256"] for result in results}), 1)
            store = V3EvidenceStore(tmp, epoch_id="epoch-multiprocess")
            verification = store.verify()
            self.assertTrue(verification["passed"])
            self.assertEqual(verification["market_segment_count"], 1)

    def test_lock_target_outside_store_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            store = V3EvidenceStore(tmp, epoch_id="epoch-containment")
            with self.assertRaisesRegex(ValueError, "V3_STORE_PATH_OUTSIDE_ROOT"):
                with store._exclusive(Path(outside) / "not-store-data.jsonl"):
                    self.fail("outside path must never be locked or written")

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

    def test_every_new_ledger_row_is_centrally_revision_and_config_bound(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SOURCE_GIT_REV": "abcdef1234567890"}, clear=False,
        ):
            previous = research_v3_store._provenance_cache
            research_v3_store._provenance_cache = None
            try:
                store = V3EvidenceStore(tmp, epoch_id="epoch-provenance")
                store.append("decision", {
                    "record_id": "decision-provenance",
                    # Callers cannot spoof the central running identity.
                    "source_revision": "spoofed",
                    "deployed_revision": "spoofed",
                    "tile_config_signature": "spoofed",
                })
                row = json.loads(store.ledger_path("decision").read_text())
            finally:
                research_v3_store._provenance_cache = previous
        self.assertEqual(row["evidence_provenance_schema"], "v3_collection_provenance_v1")
        self.assertEqual(row["source_revision"], "abcdef1234567890")
        self.assertEqual(row["deployed_revision"], "abcdef1234567890")
        self.assertEqual(
            row["tile_config_signature"],
            research_v3_store.active_tile_registry_signature(),
        )

    def test_future_opportunity_gets_one_central_truthful_causal_identity(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SOURCE_GIT_REV": "abcdef1234567890"}, clear=False,
        ):
            previous = research_v3_store._provenance_cache
            research_v3_store._provenance_cache = None
            try:
                store = V3EvidenceStore(tmp, epoch_id="epoch-causal")
                store.append("opportunity", {
                    "record_id": "opportunity:episode-1",
                    "episode_id": "episode-1",
                    "shared_ai_call_id": "scan-1",
                    "signal_ts": 1_700_000_000,
                    "signal_timezone": "UTC",
                    "market": "BITFINEX",
                    "symbol": "tBTCF0:USTF0",
                    "raw_direction": "LONG",
                    "feature_snapshot_at_signal": {
                        "market_context": {
                            "regime_label": "BULL",
                            "realized_volatility": 0.012,
                        },
                        "cycle_3m_universe": {"atr14_pct_3m": 0.42, "adx14": 28.0},
                    },
                })
                row = json.loads(store.ledger_path("opportunity").read_text())
            finally:
                research_v3_store._provenance_cache = previous
        identity = row["causal_identity"]
        self.assertEqual(identity["source_revision"], "abcdef1234567890")
        self.assertEqual(identity["deployed_revision"], "abcdef1234567890")
        self.assertEqual(identity["analyzer_revision"], "UNKNOWN")
        self.assertEqual(identity["dataset_epoch"], "epoch-causal")
        self.assertEqual(identity["shared_ai_call_id"], "scan-1")
        self.assertEqual(identity["signal_timestamp_utc"], "2023-11-14T22:13:20Z")
        self.assertEqual(identity["market"], "BITFINEX")
        self.assertEqual(identity["direction"], "LONG")
        self.assertEqual(identity["regime_volatility"]["market_regime"], "BULL")
        self.assertEqual(identity["regime_volatility"]["atr14_pct_3m"], 0.42)
        self.assertTrue(identity["collection_identity_complete"])

    def test_historical_sparse_opportunity_projects_missing_fields_as_unknown(self):
        identity = research_v3_store.project_opportunity_causal_identity({
            "record_id": "opportunity:old", "episode_id": "episode-old",
        })
        self.assertEqual(identity["source_revision"], "UNKNOWN")
        self.assertEqual(identity["signal_timestamp_utc"], "UNKNOWN")
        self.assertEqual(identity["market"], "UNKNOWN")
        self.assertFalse(identity["collection_identity_complete"])
        self.assertIn("realized_volatility", identity["missing_fields"])

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

    def test_verify_reuses_signature_validated_market_segment_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            ref = store.put_market_segment(
                source="BITFINEX", symbol="BTC", timeframe="1s",
                start_ts=1, end_ts=2, rows=[{"ts": 1, "price": 100}],
            )
            path = store.root / ref["relative_path"]
            _segment_hash_cache.pop(str(path.resolve()), None)
            with mock.patch.object(
                V3EvidenceStore,
                "_hash_segment",
                wraps=V3EvidenceStore._hash_segment,
            ) as hash_segment:
                first = store.verify()
                second = store.verify()
            self.assertTrue(first["passed"])
            self.assertTrue(second["passed"])
            self.assertEqual(hash_segment.call_count, 1)

    def test_external_segment_write_invalidates_hash_cache_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            ref = store.put_market_segment(
                source="BITFINEX", symbol="BTC", timeframe="1s",
                start_ts=1, end_ts=2, rows=[{"ts": 1, "price": 100}],
            )
            self.assertTrue(store.verify()["passed"])
            path = store.root / ref["relative_path"]
            original = path.read_bytes()
            path.write_bytes(b"X" + original[1:])
            report = store.verify()
            self.assertFalse(report["passed"])
            self.assertEqual(report["market_segment_count"], 0)
            self.assertEqual(report["defects"][0]["reason"], "SHA256_MISMATCH")

    def test_external_segment_replace_invalidates_cache_even_with_same_size_and_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            ref = store.put_market_segment(
                source="BITFINEX", symbol="BTC", timeframe="1s",
                start_ts=1, end_ts=2, rows=[{"ts": 1, "price": 100}],
            )
            self.assertTrue(store.verify()["passed"])
            path = store.root / ref["relative_path"]
            before = path.stat()
            original = path.read_bytes()
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(b"Y" + original[1:])
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, path)
            report = store.verify()
            self.assertFalse(report["passed"])
            self.assertEqual(report["defects"][0]["reason"], "SHA256_MISMATCH")

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
