"""RK PR997 conditions 1(a-c): on-disk stamps, reader boundary, closed series."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_capture_residuals as fixtures
import public_market_collector.vocabulary as vocab
from public_market_collector import framing, market
from public_market_collector.config import StartRefused
from public_market_collector.daily import execute_daily, run_daily
from public_market_collector.public_run import cite_prior_segments, open_session


def legacy_line(body):
    """PR988 checksum encoder, deliberately NOT the new writer."""
    digest = hashlib.sha256(framing.dumps(body).encode()).hexdigest()
    return (framing.dumps({**body, "record_checksum": digest}) + "\n").encode()


def example_mapping():
    """Synthetic mapping and synthetic receipt only. No real HM approval claimed."""
    def convert(record):
        record["record_vocab_v"] = vocab.CURRENT_VOCAB
        if record.get("payload", {}).get("cause") == "maintenance":
            record["payload"]["maintenance_event"] = "info_20060"
        return record
    return vocab.ReviewedVocabMapping("a" * 64, convert)


class VocabularyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pmc-vocabulary-")
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.legacy = {"channel": "gap", "payload": {"cause": "maintenance"}, "raw_id": "old:0:0"}
        self.current = {"channel": "gap", "payload": {"cause": "maintenance", "maintenance_event": "info_20061"},
                        "record_vocab_v": vocab.CURRENT_VOCAB, "raw_id": "new:0:0"}

    def test_homogeneous_legacy_is_readable_but_not_silently_upgraded(self):
        explicit = {**self.legacy, "record_vocab_v": vocab.LEGACY_VOCAB}
        result = vocab.pool_records([self.legacy, explicit])
        self.assertEqual(result, [self.legacy, explicit])
        self.assertNotIn("record_vocab_v", self.legacy)
        self.assertEqual(vocab.record_vocab_version(self.legacy), vocab.LEGACY_VOCAB)

    def test_current_series_and_empty_pool_are_readable(self):
        self.assertEqual(vocab.pool_records([self.current]), [self.current])
        self.assertEqual(vocab.pool_records([]), [])

    def test_mixed_implied_or_explicit_v1_with_v2_refuses_without_mapping(self):
        for old in (self.legacy, {**self.legacy, "record_vocab_v": vocab.LEGACY_VOCAB}):
            with self.assertRaises(vocab.VocabularyError):
                vocab.pool_records([old, self.current])

    def test_unknown_null_or_malformed_versions_never_imply_legacy(self):
        for invalid in (None, "", "pmc_vocab_v3", [], False, 1):
            with self.assertRaises(vocab.VocabularyError):
                vocab.pool_records([{**self.current, "record_vocab_v": invalid}], reviewed_mapping=example_mapping())
        with self.assertRaises(vocab.VocabularyError):
            vocab.pool_records([None])

    def test_allow_boolean_or_unreviewed_mapping_is_not_a_bypass(self):
        for unreviewed in (True, {}, lambda record: record):
            with self.assertRaises(vocab.VocabularyError):
                vocab.pool_records([self.legacy, self.current], reviewed_mapping=unreviewed)
        for receipt in (None, "", "HM said yes", "x" * 64, "A" * 64):
            with self.assertRaises(vocab.VocabularyError):
                vocab.ReviewedVocabMapping(receipt, lambda row: row)
        with self.assertRaises(vocab.VocabularyError):
            vocab.ReviewedVocabMapping("a" * 64, None)

    def test_explicit_reviewed_conversion_preserves_source_and_audit_reference(self):
        old = {**self.legacy, "record_checksum": "stale"}
        result = vocab.pool_records([old, self.current], reviewed_mapping=example_mapping())
        self.assertEqual([r["record_vocab_v"] for r in result], [vocab.CURRENT_VOCAB] * 2)
        self.assertEqual(result[0]["payload"]["maintenance_event"], "info_20060")
        self.assertNotIn("maintenance_event", old["payload"])
        self.assertNotIn("record_vocab_v", old)
        self.assertNotIn("record_checksum", result[0])
        self.assertEqual(result[0]["source_record_vocab_v"], vocab.LEGACY_VOCAB)
        self.assertEqual(result[0]["vocab_mapping_hm_review_sha256"], "a" * 64)
        self.assertEqual(result[1], self.current)

    def test_mapping_cannot_return_legacy_unknown_or_non_object(self):
        for converter in (lambda row: row, lambda row: None,
                          lambda row: {**row, "record_vocab_v": "pmc_vocab_v3"}):
            with self.assertRaises(vocab.VocabularyError):
                vocab.pool_records([self.legacy, self.current],
                                   reviewed_mapping=vocab.ReviewedVocabMapping("a" * 64, converter))

    def test_normalizer_and_canonical_reader_enforce_pool_guard(self):
        for reader in (market.normalize_raw_records, market.canonical_norms):
            with self.assertRaises(vocab.VocabularyError):
                reader([self.legacy, self.current], prec="P0", seq_scope=None, seq_layout=None)
        norms = market.normalize_raw_records([self.legacy, self.current], prec="P0", seq_scope=None,
                                             seq_layout=None, reviewed_mapping=example_mapping())
        self.assertTrue(all(row["record_vocab_v"] == vocab.CURRENT_VOCAB for row in norms))
        old_norms = market.normalize_raw_records([self.legacy], prec="P0", seq_scope=None, seq_layout=None)
        self.assertEqual(old_norms[0]["record_vocab_v"], vocab.LEGACY_VOCAB)

    def test_new_writer_primitives_stamp_and_checksum_every_record(self):
        journal = json.loads(framing.record_line({"kind": "health"}))
        self.assertEqual(journal["record_vocab_v"], vocab.CURRENT_VOCAB)
        self.assertTrue(framing.checksum_ok(journal))
        journal["record_vocab_v"] = vocab.LEGACY_VOCAB
        self.assertFalse(framing.checksum_ok(journal))
        rows = [json.loads(line) for line in framing.jsonl_bytes([{"channel": "gap"}, {"channel": "info"}]).splitlines()]
        self.assertEqual([r["record_vocab_v"] for r in rows], [vocab.CURRENT_VOCAB] * 2)
        self.assertIn("record_vocab_v", framing.RAW_KEYS)
        self.assertIn("record_vocab_v", framing.NORM_KEYS)

    def test_writers_refuse_explicit_legacy_relabel(self):
        record = {**self.legacy, "record_vocab_v": vocab.LEGACY_VOCAB}
        with self.assertRaises(vocab.VocabularyError):
            framing.record_line(record)
        with self.assertRaises(vocab.VocabularyError):
            framing.jsonl_bytes([record])

    def test_legacy_journal_cannot_be_resumed_or_appended(self):
        path = self.base / "index.journal"
        before = legacy_line({"kind": "commit"})
        path.write_bytes(before)
        (self.base / "health.journal").write_bytes(b"")
        with self.assertRaises(StartRefused):
            framing.AppendJournal(path, [], exclusive=False)
        with self.assertRaises(StartRefused):
            open_session(self.base, resume=True, repo_root=None, allow_root=self.base.parent,
                         disk_free_probe=lambda _: 10**12)
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_launch_or_runtime_journal_refuses_before_runner(self):
        for filename in ("launch.journal", "runtime.journal"):
            root = self.base / filename.split(".")[0]
            root.mkdir()
            before = legacy_line({"kind": "exit", "exit_code": 0, "utc_ns": 1})
            path = root / filename
            path.write_bytes(before)
            with redirect_stderr(io.StringIO()):
                result = execute_daily(str(root), runner=lambda *_: self.fail("legacy series resumed"),
                                       disk_free_probe=lambda _: 10**12, max_runs=1)
            self.assertEqual(result, 78)
            self.assertEqual(path.read_bytes(), before)

    def test_byte_readers_preserve_legacy_checksums_but_pooling_mixed_refuses(self):
        path = self.base / "mixed.journal"
        path.write_bytes(legacy_line({"kind": "health"}) + framing.record_line({"kind": "health"}))
        rows, torn = framing.read_journal(path)
        self.assertFalse(torn)
        self.assertTrue(all(framing.checksum_ok(r) for r in rows))
        with self.assertRaises(vocab.VocabularyError):
            vocab.pool_records(rows)
        with self.assertRaises(StartRefused):
            framing.AppendJournal(path, [], exclusive=False)

    def test_unknown_uncommitted_segment_with_no_journal_proof_is_closed(self):
        root = self.base / "uncertain"
        (root / "raw/book").mkdir(parents=True)
        (root / "index.journal").write_bytes(b"")
        (root / "health.journal").write_bytes(b"")
        (root / "raw/book/old-0001.gz").write_bytes(framing.gzip_member(b'{"channel":"info"}\n'))
        with self.assertRaises(StartRefused):
            open_session(root, resume=True, repo_root=None, allow_root=self.base,
                         disk_free_probe=lambda _: 10**12)

    def test_all_capture_journal_runtime_and_launch_families_on_disk_are_stamped(self):
        root = self.base / "capture"
        session = open_session(root, resume=False, repo_root=None, allow_root=self.base,
                               disk_free_probe=lambda _: 10**12, segment_max_bytes=1)
        try:
            for text in (fixtures._BOOK_SUB, fixtures._SNAP, fixtures._js([7, "hb"]),
                         fixtures._js({"event": "subscribed", "channel": "trades", "chanId": 8}),
                         fixtures._js([8, "te", [1, 1, 1, 6000]])):
                session.handle(text, recv_utc_ns=1, recv_mono_ns=1)
            session.commit()
            session.mark_maintenance(2, 2, event="info_20061")
            session.disconnect(recv_utc_ns=3, recv_mono_ns=3)
            session.commit()
            session.write_health(4)
            session.record_refusal("SYNTHETIC_ONLY")
        finally:
            session.release()
        cite_prior_segments(session)
        runroot = self.base / "daily"
        runroot.mkdir()
        clock = fixtures._Clock(step_ns=0)
        def run(path, event):
            class Transport(fixtures._ListTransport):
                def recv_text(inner):
                    if not inner.messages:
                        event.set()
                        return fixtures._js([7, "hb"])
                    return super().recv_text()
                def close(inner):
                    pass
            return run_daily(path, stop=event.is_set, clock_ns=clock, mono_ns=clock,
                             connect=lambda: Transport([fixtures._BOOK_SUB, fixtures._SNAP]),
                             disk_free_probe=lambda _: 10**12, deprioritize=lambda: "posix_nice_10")
        self.assertEqual(execute_daily(str(runroot), runner=run, disk_free_probe=lambda _: 10**12), 0)
        kinds = set()
        schemas = set()
        channels = set()
        count = 0
        for path in self.base.rglob("*"):
            if path.suffix == ".journal":
                rows, torn = framing.read_journal(path)
                self.assertFalse(torn)
            elif path.suffix == ".gz":
                rows, torn = framing.decode_prefix(path.read_bytes())
                self.assertIsNone(torn)
            else:
                continue
            for row in rows:
                count += 1
                self.assertEqual(row.get("record_vocab_v"), vocab.CURRENT_VOCAB, (path, row))
                kinds.add(row.get("kind"))
                schemas.add(row.get("schema_v"))
                channels.add(row.get("channel"))
        self.assertGreater(count, 20)
        self.assertTrue({"commit", "close", "startup_gap", "capture_stop", "health", "heartbeat",
                         "coverage", "day_start", "day_end", "start", "exit"} <= kinds)
        self.assertTrue({"pmc_raw_v1", "pmc_health_v1", "pmc_runtime_v1", "pmc_launch_v1"} <= schemas)
        self.assertTrue({"gap", "info", "book_snapshot", "trade", "heartbeat"} <= channels)


if __name__ == "__main__":
    unittest.main()
