import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from research.past_analysis import latest_past_analysis, list_past_analyses, seal_past_analysis
from research_genome.store import ResearchStore


class PastAnalysisTests(unittest.TestCase):
    def test_seal_past_analysis_keeps_reports_not_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compact = {
                "generated_at": "2026-07-22T10:08:50+00:00",
                "analyzer_sync_id": "v14-test",
                "data_scope": "session",
                "performance": {"trades": 179, "win_rate_pct": 66.5, "net_pnl_usd": 27.28},
                "coverage": {"confidence_status": "MODERATE", "confidence_note": "prefer >=200"},
                "key_findings": ["Type B led net profit."],
            }
            (root / "research_compact_summary.json").write_text(json.dumps(compact), encoding="utf-8")
            (root / "report_manifest.json").write_text(
                json.dumps({"generated_at": compact["generated_at"]}), encoding="utf-8"
            )
            (root / "research_findings.txt").write_text("findings", encoding="utf-8")
            (root / "lane_definition_report.json").write_text("{}", encoding="utf-8")
            (root / "trades_3factor.csv").write_text("trade_id\nabc\n", encoding="utf-8")
            (root / "research.db").write_bytes(b"not copied")

            manifest = seal_past_analysis(
                root,
                reason="fresh_collection",
                now=datetime(2026, 7, 22, 10, 15, tzinfo=timezone.utc),
            )
            archive = latest_past_analysis(root)

            self.assertIsNotNone(archive)
            assert archive is not None
            self.assertTrue((archive / "FINAL_ANALYSIS_SUMMARY.txt").is_file())
            self.assertTrue((archive / "research_findings.txt").is_file())
            self.assertTrue((archive / "lane_definition_report.json").is_file())
            self.assertFalse((archive / "trades_3factor.csv").exists())
            self.assertFalse((archive / "research.db").exists())
            self.assertFalse(manifest["raw_payloads_included"])
            self.assertEqual(
                {row["path"] for row in manifest["source_inventory"]},
                {"trades_3factor.csv", "research.db"},
            )
            self.assertEqual(len(list_past_analyses(root)), 1)

    def test_seal_requires_completed_analyzer_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "run analyzer"):
                seal_past_analysis(tmp)

    def test_latest_prefers_meaningful_analysis_over_newer_empty_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "past_analysis"
            meaningful = parent / "2026-07-22_100000Z_meaningful"
            empty = parent / "2026-07-22_110000Z_empty"
            for archive in (meaningful, empty):
                archive.mkdir(parents=True)
                (archive / "past_analysis_manifest.json").write_text("{}", encoding="utf-8")
            (parent / "index.json").write_text(
                json.dumps({
                    "schema": "past_analysis_v1",
                    "analyses": [
                        {
                            "archive_id": empty.name,
                            "performance": {"trades": 0, "net_pnl_usd": None},
                        },
                        {
                            "archive_id": meaningful.name,
                            "performance": {"trades": 179, "net_pnl_usd": 27.28},
                        },
                    ],
                }),
                encoding="utf-8",
            )

            self.assertEqual(latest_past_analysis(root), meaningful)

    def test_research_store_reset_starts_a_new_empty_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(tmp)
            store.append_event({"event_name": "AI_SCAN_COMPLETE", "ts": "2026-07-22T10:00:00+00:00"})
            self.assertEqual(store.stats()["events"], 1)

            result = store.reset()

            self.assertGreater(result["removed_bytes"], 0)
            self.assertEqual(store.stats()["events"], 0)
            with closing(sqlite3.connect(Path(tmp) / "research.db")) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM research_events").fetchone()[0], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
