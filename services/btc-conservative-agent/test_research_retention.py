import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).parent / "research" / "retention.py"
SPEC = importlib.util.spec_from_file_location("research_retention_under_test", MODULE_PATH)
retention = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(retention)


class ResearchRetentionTests(unittest.TestCase):
    def test_daily_snapshot_preserves_live_ledgers_and_prunes_intraday_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
            (root / "research_compact_summary.json").write_text('{"trades": 4}', encoding="utf-8")
            live = root / "signal_snapshot.jsonl"
            live.write_text('{"trade_id":"one","adx":31}\n', encoding="utf-8")
            history = root / "reports" / "history"
            for index in range(5):
                folder = history / f"2026-07-21_0{index}-00"
                folder.mkdir(parents=True)
                (folder / "report.json").write_text("{}", encoding="utf-8")
                stamp = (now - timedelta(minutes=index)).timestamp()
                folder.touch()
                os.utime(folder, (stamp, stamp))

            result = retention.run_analyzer_retention(root, now=now, force=True)

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["schema"], "analyzer_retention_v2")
            self.assertEqual(live.read_text(encoding="utf-8"), '{"trade_id":"one","adx":31}\n')
            self.assertTrue(
                (root / "research_retention" / "daily" / "2026-07-21" / "daily_evidence_manifest.json").is_file()
            )
            self.assertTrue((root / "analysis_summary.md").is_file())
            self.assertTrue(
                (root / "research_retention" / "daily" / "2026-07-21" / "analysis_summary.md").is_file()
            )
            self.assertLess(len(list(history.iterdir())), 5)
            manifest = json.loads(
                (root / "research_retention" / "daily" / "2026-07-21" / "daily_evidence_manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["safety"]["live_ledgers_deleted"])

    def test_closed_rotations_are_snapshotted_then_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
            active = root / "signal_replay.jsonl"
            active.write_text('{"trade_id":"active"}\n', encoding="utf-8")
            for index in range(1, 5):
                path = root / f"signal_replay.jsonl.{index}"
                path.write_text(f'{{"trade_id":"rotated-{index}"}}\n', encoding="utf-8")
                stamp = (now - timedelta(hours=48 - index)).timestamp()
                os.utime(path, (stamp, stamp))

            result = retention.run_analyzer_retention(root, now=now, force=True)

            self.assertEqual(active.read_text(encoding="utf-8"), '{"trade_id":"active"}\n')
            self.assertEqual(result["rotated_raw_inventoried"], 4)
            self.assertEqual(result["rotated_raw_deleted"], 2)
            remaining = sorted(path.name for path in root.glob("signal_replay.jsonl.*"))
            self.assertEqual(len(remaining), 2)
            manifest = json.loads(
                (root / "research_retention" / "daily" / "2026-07-21" / "daily_evidence_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest["closed_rotation_inventory"]), 4)
            self.assertEqual(manifest["closed_rotation_prune"]["deleted"], 2)

    def test_raw_db_prunes_only_expired_high_frequency_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
            db = root / "research.db"
            with closing(sqlite3.connect(db)) as conn:
                conn.executescript("""
                    CREATE TABLE research_events (id INTEGER PRIMARY KEY, ts TEXT);
                    CREATE TABLE lifecycle_genome (lifecycle_id TEXT PRIMARY KEY, ts TEXT);
                    CREATE TABLE trade_genome (trade_id TEXT PRIMARY KEY, ts TEXT);
                """)
                old = (now - timedelta(hours=80)).isoformat()
                recent = (now - timedelta(hours=1)).isoformat()
                conn.executemany("INSERT INTO research_events (ts) VALUES (?)", [(old,), (recent,)])
                conn.executemany(
                    "INSERT INTO lifecycle_genome (lifecycle_id, ts) VALUES (?, ?)",
                    [("old", old), ("recent", recent)],
                )
                conn.execute("INSERT INTO trade_genome (trade_id, ts) VALUES (?, ?)", ("preserved", old))
                conn.commit()

            result = retention.run_analyzer_retention(root, now=now, force=True)

            self.assertEqual(result["raw_db_rows_deleted"], 2)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM research_events").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM lifecycle_genome").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trade_genome").fetchone()[0], 1)

    def test_separate_mirror_root_is_inventoried_and_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reports"
            mirror = Path(tmp) / "fly-data-mirror"
            root.mkdir()
            mirror.mkdir()
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
            (root / "research_compact_summary.json").write_text("{}", encoding="utf-8")
            active = mirror / "signal_replay.jsonl"
            active.write_text('{"trade_id":"active"}\n', encoding="utf-8")
            for index in range(1, 5):
                path = mirror / f"signal_replay.jsonl.{index}"
                path.write_text(f'{{"trade_id":"rotated-{index}"}}\n', encoding="utf-8")
                stamp = (now - timedelta(hours=48 - index)).timestamp()
                os.utime(path, (stamp, stamp))

            result = retention.run_analyzer_retention(
                root, data_root=mirror, now=now, force=True
            )

            self.assertEqual(result["data_root"], str(mirror.resolve()))
            self.assertEqual(result["rotated_raw_inventoried"], 4)
            self.assertEqual(result["rotated_raw_deleted"], 2)
            self.assertTrue(active.is_file())
            manifest = json.loads(
                (root / "research_retention" / "daily" / "2026-07-21" / "daily_evidence_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["data_root"], str(mirror.resolve()))

    def test_interval_prevents_repeated_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
            retention.run_analyzer_retention(root, now=now, force=True)
            history = root / "reports" / "history"
            for index in range(6):
                folder = history / f"intraday-{index}"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "report.json").write_text("{}", encoding="utf-8")
                stamp = (now + timedelta(minutes=index)).timestamp()
                os.utime(folder, (stamp, stamp))
            result = retention.run_analyzer_retention(root, now=now + timedelta(hours=1))
            self.assertEqual(result["status"], "SKIPPED_INTERVAL")
            self.assertEqual(len(list(history.iterdir())), 3)
            self.assertGreater(result["derived_deleted_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
