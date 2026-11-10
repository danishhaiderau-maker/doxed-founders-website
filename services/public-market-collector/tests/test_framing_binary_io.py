"""Regression: SegmentFile and AppendJournal must store raw bytes on Windows.

Run 42734717686 stored 257 bytes for a 256-byte write because the collector
descriptors were opened without O_BINARY, so the CRT translated LF. This
test writes every byte value, including LF and a CRLF pair, through the
classes' real open and write paths and checks physical length and contents.
append_startup_gap's journal os.open is covered the same way.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_market_collector.framing import AppendJournal, SegmentFile, _write_all
from public_market_collector import session as session_mod


def all_byte_values_with_crlf() -> bytes:
    """Exactly 256 bytes: every value 0x00-0xFF once, with CR immediately followed by LF."""
    return bytes([0x0D, 0x0A]) + bytes(i for i in range(256) if i not in (0x0D, 0x0A))


class FramingBinaryIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pmc-framing-obinary-")
        self.base = Path(self.tmp.name)
        self.payload = all_byte_values_with_crlf()
        self.assertEqual(len(self.payload), 256)
        self.assertIn(0x0A, self.payload)
        self.assertIn(b"\r\n", self.payload)
        self.assertEqual(set(self.payload), set(range(256)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_segment_file_stores_exactly_256_bytes_and_round_trips(self):
        path = self.base / "segment.bin"
        segment = SegmentFile(path, [])
        try:
            _write_all(segment.fd, self.payload)
            os.fsync(segment.fd)
        finally:
            segment.mark_closed()
        self.assertEqual(os.path.getsize(path), 256)
        stored = path.read_bytes()
        self.assertEqual(len(stored), 256)
        self.assertEqual(stored, self.payload)
        self.assertEqual(segment.read_bytes(), self.payload)

    def test_append_journal_stores_exactly_256_bytes_and_round_trips(self):
        path = self.base / "index.journal"
        journal = AppendJournal(path, [], exclusive=True)
        try:
            _write_all(journal.fd, self.payload)
            os.fsync(journal.fd)
        finally:
            journal.close()
        self.assertEqual(os.path.getsize(path), 256)
        stored = path.read_bytes()
        self.assertEqual(len(stored), 256)
        self.assertEqual(stored, self.payload)

    def test_append_startup_gap_journal_stores_exactly_256_bytes_and_round_trips(self):
        """Reach session.append_startup_gap's journal os.open; write a controlled 256-byte payload."""
        segment = self.base / "torn.gz"
        segment.write_bytes(b"synthetic-segment")
        root = self.base / "journal-root"
        report = {
            "decodable_records": 0,
            "torn_offset": None,
            "sha256": "00" * 32,
            "tail_limit": 1024,
            "tail_state": "unknown",
            "boundary_source": "none",
            "committed_end_offset": None,
            "last_raw_id": None,
            "last_record": None,
        }
        with patch.object(session_mod, "record_line", return_value=self.payload):
            journal_path = session_mod.append_startup_gap(root, segment, report)
        self.assertEqual(journal_path, root / "index.journal")
        self.assertEqual(os.path.getsize(journal_path), 256)
        stored = journal_path.read_bytes()
        self.assertEqual(len(stored), 256)
        self.assertEqual(stored, self.payload)
        self.assertEqual(segment.read_bytes(), b"synthetic-segment")



if __name__ == "__main__":
    unittest.main()
