import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import storage_links as sl


class StorageLinksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.alarms = self.root / "alarms.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, name, data=b"row\n" * 100):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_link_or_copy_links_and_verifies(self):
        src = self._file("a/x.jsonl")
        dst = self.root / "b" / "x.jsonl"
        self.assertEqual(sl.link_or_copy(src, dst, expected_sha256=sl.sha256_file(src)), "LINKED")
        self.assertTrue(sl.same_file(src, dst))
        self.assertEqual(sl.link_or_copy(src, dst), "ALREADY_LINKED")

    def test_link_failure_falls_back_to_verified_copy_and_alarms(self):
        src = self._file("a/x.jsonl")
        dst = self.root / "b" / "x.jsonl"
        with mock.patch.object(sl.os, "link", side_effect=OSError("cross-device")):
            outcome = sl.link_or_copy(src, dst, expected_sha256=sl.sha256_file(src), alarm_log=self.alarms)
        self.assertEqual(outcome, "COPIED_FALLBACK")
        self.assertFalse(sl.same_file(src, dst))
        self.assertEqual(dst.read_bytes(), src.read_bytes())
        self.assertIn("LINK_FALLBACK_COPY", self.alarms.read_text(encoding="utf-8"))

    def test_links_disabled_copies(self):
        src = self._file("a/x.jsonl")
        dst = self.root / "b" / "x.jsonl"
        with mock.patch.dict(os.environ, {sl.LINK_ENV: "0"}):
            self.assertEqual(sl.link_or_copy(src, dst), "COPIED")
        self.assertFalse(sl.same_file(src, dst))

    def test_ensure_private_splits_shared_file_without_touching_other_link(self):
        src = self._file("a/x.jsonl")
        dst = self.root / "b" / "x.jsonl"
        sl.link_or_copy(src, dst)
        self.assertTrue(sl.ensure_private(dst))
        with dst.open("ab") as handle:
            handle.write(b"tail\n")
        self.assertEqual(src.read_bytes(), b"row\n" * 100)
        self.assertTrue(dst.read_bytes().endswith(b"tail\n"))
        self.assertFalse(sl.ensure_private(dst))

    def test_replace_with_link_refuses_different_content(self):
        keeper = self._file("a/x.jsonl")
        dup = self._file("b/x.jsonl", b"row\n" * 99 + b"ROW\n")
        self.assertEqual(sl.replace_with_link(keeper, dup), "SKIP_CONTENT_DIFFERS")
        self.assertFalse(sl.same_file(keeper, dup))
        same = self._file("c/x.jsonl")
        self.assertEqual(sl.replace_with_link(keeper, same), "LINKED")
        self.assertTrue(sl.same_file(keeper, same))

    def test_deferred_when_target_in_use_but_identical(self):
        src = self._file("a/x.jsonl")
        dst = self._file("b/x.jsonl")
        with mock.patch.object(sl.os, "replace", side_effect=PermissionError("in use")):
            self.assertEqual(sl.link_or_copy(src, dst), "DEFERRED_IN_USE")
        self.assertEqual(dst.read_bytes(), src.read_bytes())

    def test_linkable_excludes_sqlite_and_dotfiles(self):
        self.assertTrue(sl.linkable("order_multiverse_entry_grid.jsonl.3"))
        for rel in ("state.db", "x.sqlite-wal", ".puller/run.lock", "a/.hidden", "x.tmp"):
            self.assertFalse(sl.linkable(rel), rel)

    def test_unique_bytes_counts_links_once(self):
        src = self._file("a/x.jsonl")
        sl.link_or_copy(src, self.root / "b" / "x.jsonl")
        out = sl.unique_bytes([self.root / "a", self.root / "b"])
        self.assertEqual(out["logical_bytes"], 2 * src.stat().st_size)
        self.assertEqual(out["physical_bytes"], src.stat().st_size)


class CompressSettledTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _settled(self, name, size=2 * 1024 * 1024, age=7 * 3600):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"grid_sha256":"abc","entry_children":[1,2,3]}\n' * (size // 48 + 1))
        old = time.time() - age
        os.utime(path, (old, old))
        return path

    def test_selects_only_settled_large_compressible_files_once_per_inode(self):
        grid = self._settled("tree/order_multiverse_entry_grid.jsonl.1")
        os.link(grid, self.root / "tree" / "view_copy.jsonl.1")
        self._settled("tree/hot.jsonl", age=60)
        self._settled("tree/small.jsonl", size=1000)
        self._settled("tree/state.db")
        self._settled("tree/seg.tar.gz")
        calls = []

        def runner(cmd, **_kw):
            calls.append(cmd[-1])
            return mock.Mock(returncode=0)

        with mock.patch.object(sl, "compression_enabled", return_value=True), \
                mock.patch.object(sl, "allocated_bytes", return_value=None):
            out = sl.compress_settled([self.root / "tree"], runner=runner)
        self.assertEqual(out["candidates"], 1)
        self.assertEqual(out["compressed"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["verify_failed"], 0)

    def test_dry_run_and_budget(self):
        self._settled("a/x.jsonl.1")
        self._settled("a/x.jsonl.2")
        with mock.patch.object(sl, "compression_enabled", return_value=True), \
                mock.patch.object(sl, "allocated_bytes", return_value=None):
            out = sl.compress_settled([self.root / "a"], dry_run=True, max_bytes=1)
        self.assertEqual(out["candidates"], 2)
        self.assertEqual(out["compressed"], 0)
        self.assertTrue(out["budget_exhausted"])

    def test_already_compressed_is_skipped(self):
        path = self._settled("a/x.jsonl.1")
        with mock.patch.object(sl, "compression_enabled", return_value=True), \
                mock.patch.object(sl, "allocated_bytes", return_value=path.stat().st_size // 10):
            out = sl.compress_settled([self.root / "a"], runner=lambda *a, **k: self.fail("compacted"))
        self.assertEqual(out["already_compressed"], 1)

    @unittest.skipUnless(os.name == "nt", "WOF is Windows-only")
    def test_real_wof_round_trip_keeps_bytes_and_appends(self):
        path = self._settled("a/order_multiverse_entry_grid.jsonl.1")
        before = sl.sha256_file(path)
        out = sl.compress_settled([self.root / "a"])
        if not out["compressed"]:
            self.skipTest("compact.exe /exe unavailable on this volume")
        self.assertEqual(sl.sha256_file(path), before)
        self.assertLess(sl.allocated_bytes(path), path.stat().st_size)
        original = path.read_bytes()
        with path.open("r+b") as handle:
            handle.seek(0, 2)
            handle.write(b"TAIL\n")
        self.assertEqual(path.read_bytes(), original + b"TAIL\n")


if __name__ == "__main__":
    unittest.main()
