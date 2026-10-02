"""Data retention: classification, analysis archive, custody-gated laptop pruning."""
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import analysis_archive as aa
import bot_data_retention as bdr
import data_retention_policy as policy

DAY = 86400.0


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trade(trade_id, lane, pnl, close, epoch="epoch-a", family=None, regime="trend"):
    return {"trade_id": trade_id, "research_lane": lane, "net_pnl_usd": pnl, "close_ts": close,
            "epoch_id": epoch, "cfg_family": family or lane, "regime": regime, "exit_reason": "TP"}


class PolicyTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(policy.classify("order_multiverse.jsonl.4"), policy.TIER_B)
        self.assertEqual(policy.classify("market_microstructure_1s.jsonl.2"), policy.TIER_A)
        self.assertEqual(policy.classify("v3/ledgers/order_intent.jsonl"), policy.TIER_A)
        self.assertEqual(policy.classify("bitfinex_relay_intents.jsonl"), policy.PROTECTED)
        self.assertEqual(policy.classify("research.db"), policy.PROTECTED)
        self.assertEqual(policy.classify("research_events_v22.jsonl.3"), policy.PROTECTED)

    def test_only_sealed_unprotected_rotations_are_deletable(self):
        self.assertTrue(policy.deletable_rotation("signal_replay.jsonl.12"))
        self.assertFalse(policy.deletable_rotation("signal_replay.jsonl"))
        self.assertFalse(policy.deletable_rotation("bitfinex_relay_audit.jsonl.2"))
        self.assertFalse(policy.deletable_rotation("trade_lifecycle.jsonl.2"))
        self.assertFalse(policy.deletable_rotation("v3/ledgers/order_intent.jsonl.1"))
        self.assertFalse(policy.deletable_rotation("quarantine/x/signal_replay.jsonl.1"))
        self.assertFalse(policy.deletable_rotation("chase_offset_touch_grid.jsonl.1"))
        self.assertFalse(policy.deletable_rotation("chase_offset_touch_grid.jsonl.1", fly=True))
        self.assertEqual(policy.classify("order_multiverse_entry_grid.jsonl.3"), policy.OTHER)
        self.assertFalse(policy.deletable_rotation("order_multiverse_entry_grid.jsonl.3", fly=True))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = str(base / "archive")
        self.reports = base / "reports"
        self.reports.mkdir()
        self.data = base / "data"
        self.data.mkdir()
        (self.reports / "report_manifest.json").write_text(json.dumps(
            {"generation_id": "gen1", "dataset_epoch": "epoch-a", "analyzer_revision": "abcdef1234567"}))
        (self.reports / "main_rankings_report.json").write_text(json.dumps({"rankings": [1, 2]}))

    def tearDown(self):
        for directory, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                os.chmod(os.path.join(directory, name), stat.S_IWRITE | stat.S_IREAD)
        self.tmp.cleanup()

    def test_snapshot_is_immutable_verified_and_carries_consumed_seq(self):
        now = _ts("2026-10-02T12:00:00")
        trades = [_trade("t1", "CHANDELIER", 1.0, "2026-10-01T10:00:00Z")]
        heartbeat = {"segmentAppliedSeq": 2206, "syncedAt": "2026-10-02T11:50:00Z", "segmentPrefix": "v2"}
        entry = aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=trades,
                                             now=now, root=self.root, source_heartbeat=heartbeat)
        self.assertTrue(entry["verified"])
        self.assertEqual(entry["segment_seq_through"], 2206)
        snap_dir = Path(self.root) / entry["path"]
        self.assertTrue((snap_dir / "reports" / "main_rankings_report.json.gz").is_file())
        with self.assertRaises(PermissionError):
            (snap_dir / "snapshot.json").write_text("tampered")
        os.chmod(snap_dir / "snapshot.json", stat.S_IWRITE | stat.S_IREAD)
        (snap_dir / "snapshot.json").write_text("tampered")
        self.assertFalse(aa.verify_snapshot(entry, self.root))

    def test_snapshot_keeps_every_declared_report_within_the_caps(self):
        declared = [f"extra_{i}_report.json" for i in range(20)] + ["huge_report.json", "../escape.json"]
        (self.reports / "report_manifest.json").write_text(json.dumps(
            {"generation_id": "gen1", "dataset_epoch": "epoch-a", "analyzer_revision": "abcdef1234567",
             "reports": [{"file": name} for name in declared]}))
        for name in declared[:20]:
            (self.reports / name).write_text(json.dumps({"rows": [name]}))
        (self.reports / "huge_report.json").write_text("x")
        with mock.patch.object(aa, "REPORT_COPY_MAX_BYTES", 0):
            entry = aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=[],
                                                 now=_ts("2026-10-02T12:00:00"), root=self.root)
        receipt = json.loads((Path(self.root) / entry["path"] / "receipt.json").read_text())
        self.assertEqual(entry["reports_copied"], 0)
        self.assertTrue(receipt["skipped_reports"]["huge_report.json"].startswith("TOO_LARGE"))
        self.assertNotIn("../escape.json", receipt["skipped_reports"])

        entry = aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=[],
                                             now=_ts("2026-10-02T13:00:00"), root=self.root)
        snap = Path(self.root) / entry["path"] / "reports"
        self.assertEqual(entry["reports_copied"], 22)  # 20 declared + huge + main_rankings
        self.assertTrue(all((snap / f"{name}.gz").is_file() for name in declared[:20]))
        self.assertFalse((Path(self.root).parent / "escape.json.gz").exists())
        self.assertTrue(entry["verified"])

        with mock.patch.object(aa, "SNAPSHOT_REPORTS_MAX_GZ_BYTES", 1):
            entry = aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=[],
                                                 now=_ts("2026-10-02T14:00:00"), root=self.root)
        receipt = json.loads((Path(self.root) / entry["path"] / "receipt.json").read_text())
        self.assertEqual((entry["reports_copied"], entry["reports_skipped"]), (0, 22))
        self.assertTrue(all(v.startswith("SNAPSHOT_BUDGET") for v in receipt["skipped_reports"].values()))

    def test_snapshot_without_heartbeat_has_unknown_consumed_seq(self):
        entry = aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=[],
                                             now=_ts("2026-10-02T12:00:00"), root=self.root)
        self.assertIsNone(entry["segment_seq_through"])

    def test_rollups_final_vs_open_and_epoch_carry_over(self):
        trades = [_trade("t1", "CHANDELIER", 1.0, "2026-10-01T10:00:00Z", epoch="epoch-a")]
        aa.update_rollups(trades, now=_ts("2026-10-01T20:00:00"), root=self.root)
        self.assertTrue((Path(self.root) / "rollups" / "open" / "2026-10-01.json").is_file())
        # Epoch reset: epoch-a trades are gone from the analyzer input.
        later = [_trade("t2", "CHANDELIER", -0.5, "2026-10-01T22:00:00Z", epoch="epoch-b")]
        written = aa.update_rollups(later, now=_ts("2026-10-02T07:00:00"), root=self.root)
        self.assertEqual(written["final"], ["2026-10-01"])
        final = json.loads((Path(self.root) / "rollups" / "daily" / "2026-10-01.json").read_text())
        self.assertEqual(sorted(final["epochs"]), ["epoch-a", "epoch-b"])
        self.assertFalse((Path(self.root) / "rollups" / "open" / "2026-10-01.json").exists())

    def test_long_horizon_combines_archive_and_raw_without_double_count(self):
        old = [_trade(f"o{i}", "CHANDELIER", 1.0, "2026-09-20T10:00:00Z") for i in range(3)]
        aa.update_rollups(old, now=_ts("2026-09-22T00:00:00"), root=self.root)
        today = [_trade("n1", "CHANDELIER", -1.0, "2026-10-02T09:00:00Z"),
                 _trade("n1", "CHANDELIER", -1.0, "2026-10-02T09:00:00Z")]
        report = aa.long_horizon_report(today, now=_ts("2026-10-02T12:00:00"), root=self.root,
                                        lanes=["CHANDELIER"])
        tile = report["tiles"]["CHANDELIER"]
        self.assertEqual(tile["n"], 4)
        self.assertAlmostEqual(tile["net_pnl_usd"], 2.0)
        self.assertEqual(report["coverage"]["days_from_archive"], 1)
        self.assertEqual(report["coverage"]["days_from_raw"], 1)
        # Raw data for an already archived (day, epoch) replaces it instead of adding.
        again = aa.long_horizon_report(old + today, now=_ts("2026-10-02T12:00:00"), root=self.root,
                                       lanes=["CHANDELIER"])
        self.assertEqual(again["tiles"]["CHANDELIER"]["n"], 4)

    def test_incompatible_rollup_is_excluded_and_moved_to_legacy(self):
        daily = Path(self.root) / "rollups" / "daily"
        daily.mkdir(parents=True)
        (daily / "2026-09-01.json").write_text(json.dumps(
            {"schema": aa.ROLLUP_SCHEMA, "schema_version": 99, "epochs": {}}))
        good = [_trade("g", "CHANDELIER", 1.0, "2026-09-02T10:00:00Z")]
        aa.update_rollups(good, now=_ts("2026-09-04T00:00:00"), root=self.root)
        archive = aa.load_archive(root=self.root)
        self.assertNotIn("2026-09-01", archive["daily"])
        self.assertIn("2026-09-02", archive["daily"])
        self.assertEqual(aa.compat_summary(archive)["rollup_daily"][aa.INCOMPATIBLE], 1)
        self.assertFalse((daily / "2026-09-01.json").exists())
        self.assertTrue(any((Path(self.root) / "legacy").rglob("2026-09-01.json")))

    def test_converter_keeps_old_data_contributing(self):
        daily = Path(self.root) / "rollups" / "daily"
        daily.mkdir(parents=True)
        (daily / "2026-09-01.json").write_text(json.dumps(
            {"schema": aa.ROLLUP_SCHEMA, "schema_version": 0, "cells": {"epoch-a": {}}}))
        aa.CONVERTERS[(aa.ROLLUP_SCHEMA, 0)] = lambda d: {**d, "schema_version": 1, "epochs": d.pop("cells")}
        try:
            archive = aa.load_archive(root=self.root)
        finally:
            aa.CONVERTERS.pop((aa.ROLLUP_SCHEMA, 0))
        self.assertIn("2026-09-01", archive["daily"])
        self.assertEqual(aa.compat_summary(archive)["rollup_daily"][aa.CONVERTED], 1)


    def test_standalone_client_reads_archive_and_flags_tamper_and_incompatible(self):
        from strategy_lab import client
        # The export-root client cannot import analysis_archive: keep both schema tables in lockstep.
        self.assertEqual(client.ARCHIVE_SUPPORTED, aa.SUPPORTED_VERSIONS)
        self.assertFalse(aa.CONVERTERS, "add the converter to strategy_lab/client.py too")
        trades = [_trade("t1", "CHANDELIER", 1.0, "2026-10-01T10:00:00Z"),
                  _trade("t2", "CHANDELIER", -0.5, "2026-10-01T11:00:00Z")]
        entry = aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=trades,
                                             now=_ts("2026-10-02T12:00:00"), root=self.root,
                                             source_heartbeat={"segmentAppliedSeq": 7})
        (Path(self.root) / "rollups" / "daily" / "2026-09-01.json").write_text(json.dumps(
            {"schema": aa.ROLLUP_SCHEMA, "schema_version": 99, "epochs": {}}))
        archive = client.load_archive(since="2026-09-01", root=self.root)
        self.assertEqual(len(archive["snapshots"]), 1)
        tile = archive["daily"][(archive["daily"]["dimension"] == "tile") & (archive["daily"]["day"] == "2026-10-01")]
        self.assertEqual(int(tile["n"].sum()), 2)
        self.assertAlmostEqual(float(tile["net_pnl_usd"].sum()), 0.5)
        statuses = dict(zip(archive["compat"]["id"], archive["compat"]["status"]))
        self.assertEqual(statuses["2026-09-01"], "INCOMPATIBLE")
        self.assertEqual(statuses[entry["snapshot_id"]], "COMPATIBLE")
        snap = Path(self.root) / entry["path"] / "snapshot.json"
        os.chmod(snap, stat.S_IWRITE | stat.S_IREAD)
        snap.write_text("tampered")
        again = client.load_archive(root=self.root)
        self.assertEqual(len(again["snapshots"]), 0)
        self.assertIn("TAMPERED", set(again["compat"]["status"]))

    def test_history_page_and_insights_component(self):
        from research import archive_history_view as view
        from strategy_lab import insights
        trades = [_trade("t1", "CHANDELIER", 1.0, "2026-10-01T10:00:00Z")]
        aa.write_generation_snapshot(report_dir=str(self.reports), data_dir=str(self.data), trades=trades,
                                     now=_ts("2026-10-02T12:00:00"), root=self.root,
                                     source_heartbeat={"segmentAppliedSeq": 9})
        from strategy_lab import client
        with mock.patch.object(insights, "RETENTION_DIR", str(self.data)), \
                mock.patch.object(client, "DEFAULT_ARCHIVE", self.root):
            comp = insights.archive_component(_ts("2026-10-02T13:00:00"))
            stale = insights.archive_component(_ts("2026-10-03T13:00:00"))
        self.assertEqual(comp["status"], "OK")
        self.assertEqual(comp["data"]["latest_snapshot"]["segment_seq_through"], 9)
        self.assertEqual(comp["data"]["retention"]["configured_mode"], "dry_run")
        self.assertEqual(stale["status"], "STALE")
        self.assertIsNone(stale["data"])
        report = aa.long_horizon_report(trades, now=_ts("2026-10-02T13:00:00"), root=self.root, lanes=["CHANDELIER"])
        page = view.render_archive_html(comp["data"], report, status="OK", reason=None, nav_links=[("x", "/x")])
        self.assertIn("CHANDELIER", page)
        self.assertIn("seq through 9", page)
        self.assertIn("Laptop retention", page)
        down = view.render_archive_html(None, None, status="UNAVAILABLE", reason="no <snap>", nav_links=[])
        self.assertIn("no &lt;snap&gt;", down)


class LaptopRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.cfg = dict(bdr.DEFAULTS)
        self.cfg.update({
            "shadow_root": str(base / "shadow"), "view_root": str(base / "view"),
            "segment_archive_root": str(base / "segments"), "compact_root": str(base / "compact"),
            "state_dir": str(base / "state"), "archive_root": str(base / "archive"),
            "historical_roots": (), "cap_bytes": 10 ** 12,
        })
        self.tree = base / "shadow" / "tree"
        self.view = base / "view"
        self.canonical = base / "canonical"
        for directory in (self.tree, self.view, self.canonical, base / "shadow" / ".puller"):
            directory.mkdir(parents=True)
        self.now = time.time()
        self.old = self.now - 3 * DAY
        self.fly = {}
        self.posts = []

    def tearDown(self):
        for directory, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                os.chmod(os.path.join(directory, name), stat.S_IWRITE | stat.S_IREAD)
        self.tmp.cleanup()

    def _mirror(self, relpath, content, *, mtime=None, fly_sha=None, cls="snapshot", copies=True):
        for root in (self.tree,) + ((self.view, self.canonical) if copies else ()):
            path = root / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            os.utime(path, (mtime or self.old, mtime or self.old))
        self.fly[relpath] = {"class": cls, "sha256": fly_sha or hashlib.sha256(content).hexdigest(),
                             "size": len(content)}
        return self.tree / relpath

    def _gates(self, *, verdict="GREEN", parity_seq=100, acked=100, consumed=100, snapshot=True):
        (self.base / "shadow" / ".puller" / "state.json").write_text(json.dumps(
            {"applied_seq": acked + 1, "acked_seq": acked}))
        (self.base / "shadow" / "parity-latest.json").write_text(json.dumps(
            {"verdict": verdict, "seq": parity_seq, "prefix": "v2",
             "generated_at": aa._iso(self.now - 600), "manifest_sha256": "x"}))
        man = self.base / "segments" / "v2" / "man"
        man.mkdir(parents=True, exist_ok=True)
        (man / f"{min(parity_seq, acked, consumed):012d}.json").write_text('{"m":1}')
        if snapshot:
            reports = self.base / "reports"
            reports.mkdir(exist_ok=True)
            (reports / "report_manifest.json").write_text(json.dumps({"generation_id": "g"}))
            aa.write_generation_snapshot(
                report_dir=str(reports), data_dir=str(self.canonical), trades=[], now=self.now - 300,
                root=self.cfg["archive_root"],
                source_heartbeat={"segmentAppliedSeq": consumed, "syncedAt": aa._iso(self.now - 1200)})

    def _run(self, mode):
        def post(body):
            self.posts.append(json.loads(body))
            return 404, {"error": "NOT_FOUND"}
        retention = bdr.Retention(self.cfg, self.canonical, now=self.now,
                                  fetch_files=lambda: {"seq": 100, "files": self.fly}, post_custody=post)
        return retention.run(mode)

    def test_dry_run_reports_and_deletes_nothing(self):
        tree_file = self._mirror("order_multiverse.jsonl.3", b'{"a":1}\n' * 100)
        self._gates()
        status = self._run(bdr.MODE_DRY_RUN)
        self.assertEqual(status["deny_reasons"], [])
        self.assertEqual(status["would_reclaim_bytes"], 3 * tree_file.stat().st_size)
        self.assertTrue(tree_file.exists())
        self.assertTrue((self.canonical / "order_multiverse.jsonl.3").exists())
        self.assertFalse((Path(self.cfg["state_dir"]) / "prune-ledger.jsonl").exists())
        plan = json.loads((Path(self.cfg["state_dir"]) / "dry-run-latest.json").read_text())
        self.assertEqual(plan["plan"][0]["relpath"], "order_multiverse.jsonl.3")

    def test_enforce_deletes_every_copy_and_ledgers_it(self):
        content = b'{"a":1}\n' * 100
        self._mirror("signal_replay.jsonl.7", content)
        keep_active = self._mirror("signal_replay.jsonl", content)
        protected = self._mirror("bitfinex_relay_intents.jsonl.2", content)
        ledger = self._mirror("v3/ledgers/order_intent.jsonl", content)
        self._gates()
        status = self._run(bdr.MODE_ENFORCE)
        for root in (self.tree, self.view, self.canonical):
            self.assertFalse((root / "signal_replay.jsonl.7").exists())
        self.assertTrue(keep_active.exists() and protected.exists() and ledger.exists())
        rows = bdr.Ledger(Path(self.cfg["state_dir"])).rows()
        self.assertEqual(sorted(r["root"] for r in rows), ["canonical", "tree", "view"])
        self.assertEqual(rows[0]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(bdr.pruned_index(self.cfg["state_dir"]),
                         {"signal_replay.jsonl.7": hashlib.sha256(content).hexdigest()})
        self.assertEqual(status["reclaimed_bytes"], 3 * len(content))

    def test_custody_receipt_is_bounded_by_ack_parity_and_analyzer(self):
        self._mirror("signal_replay.jsonl.7", b"x\n")
        self._gates(parity_seq=90, acked=100, consumed=80)
        status = self._run(bdr.MODE_DRY_RUN)
        self.assertEqual(self.posts[0]["through_seq"], 80)
        self.assertEqual(self.posts[0]["schema"], bdr.CUSTODY_SCHEMA)
        self.assertFalse(status["custody_post"]["posted"])
        self.assertEqual(status["custody_post"]["http_status"], 404)

    def test_hash_mismatch_keeps_every_copy(self):
        self._mirror("signal_replay.jsonl.7", b"local\n", fly_sha="0" * 64)
        self._gates()
        status = self._run(bdr.MODE_ENFORCE)
        self.assertTrue((self.tree / "signal_replay.jsonl.7").exists())
        self.assertEqual(status["kept"][0]["custody"], "LOCAL_SHA256_DIFFERS_FROM_FLY_CHECKPOINT")

    def test_divergent_view_copy_is_kept(self):
        self._mirror("signal_replay.jsonl.7", b"tree\n")
        (self.view / "signal_replay.jsonl.7").write_bytes(b"other\n")
        self._gates()
        self._run(bdr.MODE_ENFORCE)
        self.assertFalse((self.tree / "signal_replay.jsonl.7").exists())
        self.assertTrue((self.view / "signal_replay.jsonl.7").exists())

    def test_gates_fail_closed(self):
        cases = {
            "PARITY_NOT_GREEN": dict(verdict="RED"),
            "PARITY_SEQ_NOT_ACKED": dict(parity_seq=101, acked=100),
            "ANALYSIS_SNAPSHOT_UNVERIFIED": dict(snapshot=False),
        }
        for reason, kwargs in cases.items():
            with self.subTest(reason=reason):
                self.setUp()
                self._mirror("signal_replay.jsonl.7", b"x\n")
                self._gates(**kwargs)
                status = self._run(bdr.MODE_ENFORCE)
                self.assertIn(reason, status["deny_reasons"])
                self.assertTrue((self.tree / "signal_replay.jsonl.7").exists())
                self.tearDown()

    def test_young_unconsumed_or_baseline_files_are_kept(self):
        self._mirror("signal_replay.jsonl.7", b"young\n", mtime=self.now - 3600)
        self._mirror("signal_replay.jsonl.8", b"unconsumed\n", mtime=self.now - 900)
        self._mirror("order_multiverse.jsonl.18", b"baseline\n")
        self.fly["order_multiverse.jsonl.18"]["baseline"] = True
        self._gates()
        self._run(bdr.MODE_ENFORCE)
        for name in ("signal_replay.jsonl.7", "signal_replay.jsonl.8", "order_multiverse.jsonl.18"):
            self.assertTrue((self.tree / name).exists(), name)

    def test_segment_archive_pruned_only_within_custody_and_age(self):
        seg = self.base / "segments" / "v2" / "seg"
        seg.mkdir(parents=True)
        for seq, age in ((50, 10 * DAY), (99, 10 * DAY), (101, 10 * DAY), (60, 1 * DAY)):
            path = seg / f"{seq:012d}.tar.gz"
            path.write_bytes(b"seg")
            os.utime(path, (self.now - age, self.now - age))
        self._gates(parity_seq=100, acked=100, consumed=100)
        self._run(bdr.MODE_ENFORCE)
        remaining = sorted(p.name[:12] for p in seg.glob("*.tar.gz"))
        self.assertEqual(remaining, ["000000000060", "000000000101"])
        self.assertTrue((self.base / "segments" / "v2" / "man" / f"{100:012d}.json").exists())

    def test_cap_pressure_deletes_young_tier_b_but_never_protected(self):
        self.cfg["cap_bytes"] = 1
        self._mirror("signal_replay.jsonl.7", b"young\n", mtime=self.now - 3600)
        protected = self._mirror("research.db", b"db")
        self._gates()
        status = self._run(bdr.MODE_ENFORCE)
        self.assertFalse((self.tree / "signal_replay.jsonl.7").exists())
        self.assertTrue(protected.exists())
        self.assertEqual(status["cap"]["status"], "CAP_EXCEEDED_NOTHING_ELIGIBLE")
        self.assertEqual(status["level"], "RED")

    def test_tier_a_compaction_writes_zstd_parquet_with_schema_version(self):
        import pyarrow.parquet as pq
        rows = [json.dumps({"ts": _ts("2026-09-30T10:00:00") + i, "bid": 1}) for i in range(5)]
        rows += [json.dumps({"ts": _ts("2026-10-01T10:00:00"), "bid": 2})]
        self._mirror("market_microstructure_1s.jsonl.3", ("\n".join(rows) + "\n").encode(), copies=False)
        csv_text = "timestamp,action\n2026-09-30T11:00:00Z,BUY\n"
        self._mirror("ai_tranche_log.csv", csv_text.encode(), copies=False)
        self._gates()
        self.now = _ts("2026-10-02T12:00:00")
        status = self._run(bdr.MODE_ENFORCE)
        parts = sorted((Path(self.cfg["compact_root"]) / "tierA").rglob("*.parquet"))
        names = [p.relative_to(Path(self.cfg["compact_root"]) / "tierA").as_posix() for p in parts]
        self.assertIn("bitfinex_l1_tape_1s/v1/date=2026-09-30/part-0000.parquet", names)
        self.assertIn("ai_decisions/v1/date=2026-09-30/part-0000.parquet", names)
        table = pq.read_table(parts[names.index("bitfinex_l1_tape_1s/v1/date=2026-09-30/part-0000.parquet")])
        self.assertEqual(table.num_rows, 5)
        self.assertEqual(table.schema.metadata[b"schema_version"], b"1")
        meta = pq.ParquetFile(parts[0]).metadata.row_group(0).column(0)
        self.assertEqual(meta.compression, "ZSTD")
        self.assertTrue(all(row["status"] == aa.COMPATIBLE for row in status["tier_a_schema"]))
        # Re-running does not duplicate rows (offset/identity state).
        self._run(bdr.MODE_ENFORCE)
        self.assertEqual(len(list((Path(self.cfg["compact_root"]) / "tierA").rglob("*.parquet"))), len(parts))

    def test_incompatible_tier_a_partitions_move_to_legacy(self):
        legacy_part = Path(self.cfg["compact_root"]) / "tierA" / "bitfinex_l1_tape_1s" / "v0" / "date=2026-01-01"
        legacy_part.mkdir(parents=True)
        (legacy_part / "part-0000.parquet").write_bytes(b"old")
        self._gates()
        status = self._run(bdr.MODE_ENFORCE)
        self.assertEqual(status["compaction"]["legacy_moves"][0]["status"], aa.INCOMPATIBLE)
        self.assertTrue((Path(self.cfg["compact_root"]) / "legacy" / "bitfinex_l1_tape_1s" / "v0").is_dir())

    def test_parity_tolerates_only_hash_verified_laptop_prunes(self):
        import importlib.util
        script = Path(__file__).resolve().parents[2] / "scripts" / "research_segment_fly_parity.py"
        spec = importlib.util.spec_from_file_location("fly_parity_under_test", script)
        parity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parity)
        files = {"signal_replay.jsonl.7": {"class": "snapshot", "sha256": "a" * 64},
                 "signal_replay.jsonl.8": {"class": "snapshot", "sha256": "b" * 64}}
        report = parity.classify(files, [], self.tree, {}, {"signal_replay.jsonl.7": "a" * 64,
                                                           "signal_replay.jsonl.8": "c" * 64})
        self.assertEqual(report["counts"]["pruned_verified"], 1)
        self.assertEqual(report["counts"]["missing"], 1)
        self.assertEqual(report["verdict"], "RED")

    def test_lock_factory_waits_out_a_pull_then_gives_up(self):
        from research_segment_puller import PullerError
        attempts, now = [], [0.0]

        def busy_twice(path):
            attempts.append(path)
            if len(attempts) <= 2:
                raise PullerError("busy")
            return "LOCK"

        def sleep(seconds):
            now[0] += seconds

        factory = bdr.retrying_lock_factory(180, sleep=sleep, clock=lambda: now[0], lock_class=busy_twice)
        self.assertEqual(factory("run.lock"), "LOCK")
        self.assertEqual(len(attempts), 3)

        def always_busy(path):
            raise PullerError("busy")

        now[0] = 0.0
        factory = bdr.retrying_lock_factory(5, sleep=sleep, clock=lambda: now[0], lock_class=always_busy)
        with self.assertRaisesRegex(RuntimeError, "busy for 5s"):
            factory("run.lock")

    def test_refuses_onedrive_paths(self):
        with self.assertRaises(RuntimeError):
            bdr.refuse_onedrive(r"C:\Users\x\OneDrive\Desktop\data")


if __name__ == "__main__":
    unittest.main()
