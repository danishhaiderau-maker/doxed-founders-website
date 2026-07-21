import importlib.util
import json
import tempfile
import unittest
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
                import os
                os.utime(folder, (stamp, stamp))

            result = retention.run_analyzer_retention(root, now=now, force=True)

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(live.read_text(encoding="utf-8"), '{"trade_id":"one","adx":31}\n')
            self.assertTrue(
                (root / "research_retention" / "daily" / "2026-07-21" / "daily_evidence_manifest.json").is_file()
            )
            self.assertLess(len(list(history.iterdir())), 5)
            manifest = json.loads(
                (root / "research_retention" / "daily" / "2026-07-21" / "daily_evidence_manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["safety"]["live_ledgers_deleted"])

    def test_interval_prevents_repeated_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
            retention.run_analyzer_retention(root, now=now, force=True)
            result = retention.run_analyzer_retention(root, now=now + timedelta(hours=1))
            self.assertEqual(result["status"], "SKIPPED_INTERVAL")


if __name__ == "__main__":
    unittest.main()
