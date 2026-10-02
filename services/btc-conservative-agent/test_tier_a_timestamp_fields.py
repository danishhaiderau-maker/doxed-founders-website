"""Tier A compactor: per-stream timestamps, undated re-bucketing, verified promotion, backfill."""
import csv
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import bot_data_retention as bdr
import data_retention_policy as policy


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


DAY1 = _ts("2026-09-29T10:00:00")
DAY2 = _ts("2026-09-30T10:00:00")
NOW = _ts("2026-10-02T12:00:00")


def _spec(base):
    return policy.TIER_A_DATASETS[base]


def _csv_text(header, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


class TimestampMappingTests(unittest.TestCase):
    def _field(self, base, row):
        return policy.row_timestamp(row, _spec(base)[2])

    def test_tape_uses_bucket_ts(self):
        ts, field = self._field("market_microstructure_1s.jsonl",
                                {"bucket_ts": 1790892316, "source_ts": 1790892315.77, "bid": 1})
        self.assertEqual((ts, field), (1790892316.0, "bucket_ts"))

    def test_market_context_and_cross_venue_use_minute_ts(self):
        self.assertEqual(self._field("market_context_1m.jsonl", {"minute_ts": 1790919960})[1], "minute_ts")
        self.assertEqual(self._field("cross_venue_tape_1m.jsonl", {"minute_ts": 1790844240})[1], "minute_ts")

    def test_liquidations_ts_then_recv_ts(self):
        self.assertEqual(self._field("liquidations.jsonl", {"ts": 1790919974.2, "exch_ts": 1})[1], "ts")
        self.assertEqual(self._field("liquidations.jsonl", {"recv_ts": 1790919974.2})[1], "recv_ts")

    def test_v3_ledgers(self):
        intent_submit = {"submitted_ts": 1790736508.17, "chase_schedule": {}}
        intent_shadow = {"entry_children": [{"hypothetical_order_start_ts": 1790691877.28}]}
        self.assertEqual(self._field("v3/ledgers/order_intent.jsonl", intent_submit)[1], "submitted_ts")
        self.assertEqual(self._field("v3/ledgers/order_intent.jsonl", intent_shadow),
                         (1790691877.28, "entry_children.0.hypothetical_order_start_ts"))
        self.assertEqual(self._field("v3/ledgers/execution.jsonl", {"fill_ts": 1790693440.5})[1], "fill_ts")
        self.assertEqual(self._field("v3/ledgers/execution.jsonl", {"close_ts": 1790693440.5})[1], "close_ts")
        self.assertEqual(self._field("v3/ledgers/lifecycle.jsonl", {"signal_ts": 1790691877.2})[1], "signal_ts")
        self.assertEqual(self._field("v3/ledgers/lifecycle.jsonl", {"observed_ts": 1790691877.2})[1],
                         "observed_ts")
        self.assertEqual(self._field("v3/ledgers/decision.jsonl", {"decision_ts": 1790736495.8})[1],
                         "decision_ts")
        self.assertEqual(self._field("v3/ledgers/opportunity.jsonl",
                                     {"causal_identity": {"signal_ts": 1790736495.8}})[1],
                         "causal_identity.signal_ts")

    def test_csv_and_ai_streams(self):
        self.assertEqual(self._field("trades_3factor.csv", {"close_ts": "1790738530.9", "ts": "x"})[1], "close_ts")
        # close_ts=0 is not a timestamp; fall through to ts instead of dating the row 1970.
        self.assertEqual(self._field("trades_3factor.csv", {"close_ts": "0", "ts": "2026-09-30T03:24:42+00:00"})[1],
                         "ts")
        self.assertEqual(self._field("ai_tranche_log.csv", {"ts": "2026-09-28T17:42:09.551795+00:00"})[1], "ts")
        self.assertEqual(self._field("expired_orders_3factor.csv", {"time": "2026-09-30T03:18:18+00:00"})[1],
                         "time")
        self.assertEqual(self._field("ai_shadow_challengers.jsonl", {"decision_ts": 1790812473.2})[1],
                         "decision_ts")
        self.assertEqual(self._field("ai_shadow_compact_prompt.jsonl",
                                     {"observed_at_utc": "2026-09-30T23:54:33+00:00"})[1], "observed_at_utc")

    def test_epoch_ms_and_iso_and_implausible(self):
        self.assertEqual(policy.parse_timestamp(1790892316000), 1790892316.0)
        self.assertEqual(policy.parse_timestamp("2026-09-30T00:00:00Z"), _ts("2026-09-30T00:00:00"))
        for bad in (0, -5, "0", 86400, "1970-01-02T00:00:00Z", True, "nan", float("inf"), "garbage"):
            self.assertIsNone(policy.parse_timestamp(bad), bad)
        self.assertFalse(policy.valid_day("1970-01-01"))
        self.assertTrue(policy.valid_day("2026-09-30"))


class CompactorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.tree = self.base / "shadow" / "tree"
        self.tree.mkdir(parents=True)
        self.root = self.base / "compact"

    def tearDown(self):
        for directory, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                os.chmod(os.path.join(directory, name), stat.S_IWRITE | stat.S_IREAD)
        self.tmp.cleanup()

    def _write(self, relpath, text):
        path = self.tree / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def _compactor(self, now=NOW):
        return bdr.Compactor(self.root, settle_hours=6.0, now=now)

    def _parts(self, dataset):
        return sorted((self.root / "tierA" / dataset).rglob("part-*.parquet"))

    def _rows(self, dataset, day):
        import pyarrow.parquet as pq
        out = []
        for part in sorted((self.root / "tierA" / dataset / "v1" / f"date={day}").glob("part-*.parquet")):
            out.extend(pq.read_table(part).to_pylist())
        return out

    def test_tape_rows_are_dated_by_bucket_ts_and_promoted(self):
        rows = [json.dumps({"bucket_ts": int(DAY1) + i, "bid": 1}) for i in range(3)]
        rows += [json.dumps({"bucket_ts": int(DAY2), "bid": 2})]
        self._write("market_microstructure_1s.jsonl.2", "\n".join(rows) + "\n")
        c = self._compactor()
        c.ingest(self.tree, dry_run=False)
        self.assertFalse(list(self.root.rglob("undated*")))
        done = c.finalize(now=NOW, dry_run=False)
        self.assertEqual(done["errors"], [])
        self.assertEqual(len(self._rows("bitfinex_l1_tape_1s", "2026-09-29")), 3)
        self.assertEqual(len(self._rows("bitfinex_l1_tape_1s", "2026-09-30")), 1)
        manifest = json.loads(next((self.root / "tierA" / "bitfinex_l1_tape_1s").rglob("*.manifest.json"))
                              .read_text())
        self.assertEqual(len(manifest["content_sha256"]), 64)
        self.assertEqual(c.staging_files(), [])

    def test_csv_multiline_quoted_records_stay_whole(self):
        header = ["ts", "trade_id", "comment"]
        text = _csv_text(header, [
            {"ts": "2026-09-29T10:00:00+00:00", "trade_id": "a", "comment": '{\n  "direction": "LONG"\n}'},
            {"ts": "2026-09-30T10:00:00+00:00", "trade_id": "b", "comment": "plain"},
        ])
        self._write("ai_tranche_log.csv", text)
        c = self._compactor()
        result = c.ingest(self.tree, dry_run=False)
        self.assertEqual((result["rows"], result["undated_rows"]), (2, 0))
        c.finalize(now=NOW, dry_run=False)
        row = json.loads(self._rows("ai_decisions", "2026-09-29")[0]["row"])
        self.assertEqual(row["comment"], '{\n  "direction": "LONG"\n}')
        self.assertEqual(row["trade_id"], "a")

    def test_csv_unterminated_trailing_record_waits(self):
        path = self._write("ai_tranche_log.csv", 'ts,comment\n2026-09-29T10:00:00+00:00,"open\n')
        c = self._compactor()
        c.ingest(self.tree, dry_run=False)
        entry = next(iter(c.state["sources"].values()))
        self.assertLess(entry["offset"], path.stat().st_size)
        with path.open("a", encoding="utf-8") as handle:
            handle.write('still open"\n')
        result = c.ingest(self.tree, dry_run=False)
        self.assertEqual(result["rows"], 1)

    def test_zero_timestamp_never_creates_1970_partition(self):
        text = _csv_text(["close_ts", "ts", "pnl"], [{"close_ts": "0", "ts": "0", "pnl": "1"}])
        self._write("trades_3factor.csv", text)
        c = self._compactor()
        c.ingest(self.tree, dry_run=False)
        c.finalize(now=NOW, dry_run=False, close_unresolved=True)
        self.assertFalse([p for p in self.root.rglob("date=1970*")])
        self.assertEqual(len(list((self.root / "tierA" / "closed_trades" / "v1" / "date=undated")
                                  .glob("*.parquet"))), 1)

    def test_legacy_undated_staging_is_rebucketed_by_content(self):
        staging = self.root / "staging" / "bitfinex_l1_tape_1s" / "v1"
        staging.mkdir(parents=True)
        lines = [json.dumps({"ts": None, "row": json.dumps({"bucket_ts": int(DAY1) + i})}) for i in range(4)]
        lines.append(json.dumps({"ts": None, "row": json.dumps({"no_time": 1})}))
        (staging / "undated.jsonl").write_text("\n".join(lines) + "\n")
        os.utime(staging / "undated.jsonl", (NOW, NOW))  # fresh mtime must not block promotion
        c = self._compactor()
        done = c.finalize(now=NOW, dry_run=False)
        self.assertEqual(done["errors"], [])
        self.assertEqual(len(self._rows("bitfinex_l1_tape_1s", "2026-09-29")), 4)
        self.assertFalse((staging / "undated.jsonl").exists())
        # The timestampless legacy row stays pending (counted) until its grace passes.
        pending = [p for p in c.staging_files() if bdr._staging_token(p) == bdr.UNDATED]
        self.assertEqual(sum(bdr._count_lines(p) for p in pending), 1)
        health = c.health(now=NOW, backfilled=False)
        tape = next(d for d in health["datasets"] if d["dataset"] == "bitfinex_l1_tape_1s")
        self.assertEqual(tape["undated_rows"], 1)
        self.assertEqual(health["level"], "AMBER")
        later = NOW + 7 * 3600
        c.finalize(now=later, dry_run=False)
        self.assertEqual(len(self._rows("bitfinex_l1_tape_1s", "undated")), 1)
        self.assertEqual(c.staging_files(), [])

    def test_decision_without_timestamp_joins_opportunity_by_event_id(self):
        self._write("v3/ledgers/opportunity.jsonl", json.dumps({"event_id": "e1", "signal_ts": DAY1}) + "\n")
        self._write("v3/ledgers/decision.jsonl", json.dumps({"event_id": "e1", "ledger": "decision"}) + "\n")
        c = self._compactor()
        c.ingest(self.tree, dry_run=False)
        c.finalize(now=NOW, dry_run=False)
        rows = self._rows("v3_decision", "2026-09-29")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], DAY1)

    def test_verification_failure_keeps_staging(self):
        self._write("market_microstructure_1s.jsonl.2", json.dumps({"bucket_ts": int(DAY1)}) + "\n")
        c = self._compactor()
        c.ingest(self.tree, dry_run=False)
        with mock.patch.object(bdr, "_readback_digest", return_value=(1, "0" * 64)):
            done = c.finalize(now=NOW, dry_run=False)
        self.assertTrue(done["errors"])
        self.assertEqual(self._parts("bitfinex_l1_tape_1s"), [])
        self.assertEqual(len(c.staging_files("bitfinex_l1_tape_1s")), 1)
        done = c.finalize(now=NOW, dry_run=False)
        self.assertEqual(done["errors"], [])
        self.assertEqual(len(self._parts("bitfinex_l1_tape_1s")), 1)

    def test_covered_requires_verified_parquet_not_staging(self):
        path = self._write("market_microstructure_1s.jsonl.2", json.dumps({"bucket_ts": int(DAY2)}) + "\n")
        early = _ts("2026-09-30T20:00:00")
        c = self._compactor(now=early)
        c.ingest(self.tree, dry_run=False)
        c.finalize(now=early, dry_run=False)
        self.assertEqual(len(c.staging_files()), 1)
        self.assertFalse(c.covered(path, "bitfinex_l1_tape_1s"))
        c.finalize(now=NOW, dry_run=False)
        self.assertTrue(c.covered(path, "bitfinex_l1_tape_1s"))
        part = self._parts("bitfinex_l1_tape_1s")[0]
        part.write_bytes(part.read_bytes() + b"x")
        self.assertFalse(bdr.Compactor(self.root, settle_hours=6.0).covered(path, "bitfinex_l1_tape_1s"))

    def test_legacy_entry_without_days_is_not_covered_until_attributed(self):
        path = self._write("market_microstructure_1s.jsonl.2", json.dumps({"bucket_ts": int(DAY1)}) + "\n")
        c = self._compactor()
        c.ingest(self.tree, dry_run=False)
        c.finalize(now=NOW, dry_run=False)
        for entry in c.state["sources"].values():
            entry.pop("days")
        self.assertFalse(c.covered(path, "bitfinex_l1_tape_1s"))
        self.assertEqual(c.attribute_days(self.tree), ["market_microstructure_1s.jsonl.2"])
        self.assertTrue(c.covered(path, "bitfinex_l1_tape_1s"))

    def test_health_flags_1970_partition_red(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        bad = self.root / "tierA" / "closed_trades" / "v1" / "date=1970-01-01"
        bad.mkdir(parents=True)
        pq.write_table(pa.table({"ts": [0.0], "row": ["{}"]}), bad / "part-0000.parquet")
        health = self._compactor().health(now=NOW, backfilled=False)
        closed = next(d for d in health["datasets"] if d["dataset"] == "closed_trades")
        self.assertEqual(closed["rows_1970"], 1)
        self.assertEqual(health["level"], "RED")
        self.assertEqual(closed["timestamp_field"], "close_ts")

    def test_health_red_when_closed_day_overdue(self):
        staging = self.root / "staging" / "liquidations" / "v1"
        staging.mkdir(parents=True)
        (staging / "2026-09-29.jsonl").write_text(json.dumps({"ts": DAY1, "row": "{}"}) + "\n")
        health = self._compactor().health(now=NOW, backfilled=False)
        liq = next(d for d in health["datasets"] if d["dataset"] == "liquidations")
        self.assertIn("CLOSED_DAY_UNPROMOTED_PAST_SETTLE_PLUS_24H", liq["reasons"])
        self.assertEqual(health["level"], "RED")
        clean = bdr.Compactor(self.base / "empty", settle_hours=6.0).health(now=NOW, backfilled=True)
        self.assertEqual(clean["level"], "GREEN")


class BackfillTests(unittest.TestCase):
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
        self.tree.mkdir(parents=True)
        self.root = base / "compact"
        self.data = base / "canonical"
        self.data.mkdir()
        staging = self.root / "staging" / "bitfinex_l1_tape_1s" / "v1"
        staging.mkdir(parents=True)
        lines = [json.dumps({"ts": None, "row": json.dumps({"bucket_ts": int(DAY2) + i})}) for i in range(5)]
        (staging / "undated.jsonl").write_text("\n".join(lines) + "\n")
        archive = base / "archive-manual" / "tree"
        archive.mkdir(parents=True)
        arch = [json.dumps({"bucket_ts": int(DAY1) + i}) for i in range(3)]
        arch += [json.dumps({"bucket_ts": int(DAY2) + i, "src": "archive"}) for i in range(2)]  # overlap
        (archive / "market_microstructure_1s.jsonl.1").write_text("\n".join(arch) + "\n")
        self.archive_root = base / "archive-manual"
        header = ["close_ts", "ts", "note"]
        (self.tree / "trades_3factor.csv").write_bytes(_csv_text(header, [
            {"close_ts": str(DAY1), "ts": "x", "note": "line1\nline2"}]).encode("utf-8"))
        legacy = self.root / "tierA" / "closed_trades" / "v1" / "date=1970-01-01"
        legacy.mkdir(parents=True)
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.table({"ts": [0.0], "row": ['{"close_ts":"0"}']}), legacy / "part-0000.parquet")

    def tearDown(self):
        for directory, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                os.chmod(os.path.join(directory, name), stat.S_IWRITE | stat.S_IREAD)
        self.tmp.cleanup()

    def _run(self, *, dry_run, lock_factory=None):
        return bdr.tier_a_backfill(self.cfg, dry_run=dry_run, data_root=self.data,
                                   archive_roots=[self.archive_root], now=NOW, lock_factory=lock_factory)

    def _snapshot(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())

    def test_dry_run_verifies_on_scratch_and_changes_nothing(self):
        before = self._snapshot()
        receipt = self._run(dry_run=True)
        self.assertEqual(receipt["status"], "OK", receipt.get("error"))
        self.assertEqual(self._snapshot(), before)
        self.assertGreater(receipt["partitions_created"], 0)
        self.assertEqual(receipt["hashes_verified"], receipt["partitions_created"])
        self.assertTrue((self.base / "state" / "tier-a-backfill" / "dry-run-latest.json").is_file())
        self.assertFalse((self.base / "state" / "tier-a-backfill" / "latest.json").exists())

    def test_real_run_promotes_dedupes_archive_and_is_idempotent(self):
        import pyarrow.parquet as pq
        archive_bytes = (self.archive_root / "tree" / "market_microstructure_1s.jsonl.1").read_bytes()
        receipt = self._run(dry_run=False)
        self.assertEqual(receipt["status"], "OK", receipt.get("error"))
        tape = self.root / "tierA" / "bitfinex_l1_tape_1s" / "v1"
        day2 = sum(pq.ParquetFile(p).metadata.num_rows for p in (tape / "date=2026-09-30").glob("*.parquet"))
        day1 = sum(pq.ParquetFile(p).metadata.num_rows for p in (tape / "date=2026-09-29").glob("*.parquet"))
        self.assertEqual((day1, day2), (3, 5))  # 2 overlapping archive seconds deduped by bucket_ts
        self.assertEqual(receipt["duplicates_dropped"], 2)
        self.assertFalse(list(self.root.glob("staging/bitfinex_l1_tape_1s/v1/*")))
        self.assertFalse(list((self.root / "tierA").rglob("date=1970*")))
        closed = list((self.root / "tierA" / "closed_trades" / "v1" / "date=2026-09-29").glob("*.parquet"))
        row = json.loads(pq.read_table(closed[0]).column("row").to_pylist()[0])
        self.assertEqual(row["note"], "line1\nline2")
        self.assertTrue(list((self.root / "superseded").rglob("date=1970-01-01")))
        self.assertEqual((self.archive_root / "tree" / "market_microstructure_1s.jsonl.1").read_bytes(),
                         archive_bytes)
        self.assertNotEqual(receipt["tier_a"]["level"], "RED")
        parts = sorted((self.root / "tierA").rglob("*.parquet"))
        again = self._run(dry_run=False)
        self.assertEqual(again["status"], "OK", again.get("error"))
        self.assertEqual(again["partitions_created"], 0)
        self.assertEqual(sorted((self.root / "tierA").rglob("*.parquet")), parts)
        self.assertTrue(bdr._backfill_receipt_present(self.base / "state"))

    def test_lock_busy_fails_closed(self):
        before = self._snapshot()

        def busy(path):
            raise RuntimeError("shadow-root lock busy for 180s; retry next cycle")

        receipt = self._run(dry_run=False, lock_factory=busy)
        self.assertEqual(receipt["status"], "FAILED")
        self.assertIn("lock busy", receipt["error"])
        self.assertEqual(self._snapshot(), before)

    def test_cycle_mutex_busy_fails_closed(self):
        before = self._snapshot()

        def busy():
            raise RuntimeError("analyzer cycle mutex busy")

        receipt = bdr.tier_a_backfill(self.cfg, dry_run=False, data_root=self.data, now=NOW,
                                      mutex_factory=busy)
        self.assertEqual(receipt["status"], "FAILED")
        self.assertEqual(self._snapshot(), before)

    def test_disk_cap_refuses(self):
        self.cfg["cap_bytes"] = 10
        receipt = self._run(dry_run=False)
        self.assertEqual(receipt["status"], "REFUSED_DISK")
        self.assertTrue((self.root / "staging" / "bitfinex_l1_tape_1s" / "v1" / "undated.jsonl").exists())

    @unittest.skipUnless(os.name == "nt", "named mutex is Windows-only")
    def test_cycle_mutex_factory_acquires_and_releases(self):
        with mock.patch.dict(os.environ, {"DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX": f"TierATest{os.getpid()}"}):
            handle = bdr.cycle_mutex_factory(0)()
            handle.release()
            bdr.cycle_mutex_factory(0)().release()


class RetentionStatusTests(unittest.TestCase):
    def test_status_carries_tier_a_block(self):
        tmp = tempfile.mkdtemp()
        try:
            base = Path(tmp)
            cfg = dict(bdr.DEFAULTS)
            cfg.update({"shadow_root": str(base / "shadow"), "view_root": str(base / "view"),
                        "segment_archive_root": str(base / "segments"), "compact_root": str(base / "compact"),
                        "state_dir": str(base / "state"), "archive_root": str(base / "archive"),
                        "historical_roots": (), "cap_bytes": 10 ** 12})
            (base / "shadow" / "tree").mkdir(parents=True)
            (base / "shadow" / "tree" / "liquidations.jsonl").write_text(
                json.dumps({"ts": DAY1, "side": "buy"}) + "\n")
            (base / "canonical").mkdir()
            status = bdr.Retention(cfg, base / "canonical", now=NOW, fetch_files=lambda: {"seq": 1, "files": {}},
                                   post_custody=lambda body: (404, {})).run(bdr.MODE_ENFORCE)
            block = status["tier_a"]
            liq = next(d for d in block["datasets"] if d["dataset"] == "liquidations")
            self.assertEqual(liq["promoted_days"], 1)
            self.assertEqual(liq["last_promoted_day"], "2026-09-29")
            self.assertEqual(liq["timestamp_field"], "ts")
            for key in ("dataset", "promoted_days", "last_promoted_day", "undated_rows", "undated_bytes",
                        "oldest_staging_day", "rows_1970", "timestamp_field"):
                self.assertIn(key, liq)
            self.assertEqual(block["level"], "GREEN")
            written = json.loads((base / "state" / "status.json").read_text())
            self.assertEqual(written["tier_a"]["level"], "GREEN")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
