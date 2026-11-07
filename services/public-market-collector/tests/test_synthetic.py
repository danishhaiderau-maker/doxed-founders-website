"""Synthetic acceptance for the public market collector. No live socket."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ORIGINAL_SOCKET = socket.socket


class _BlockedSocket(_ORIGINAL_SOCKET):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network socket blocked")


socket.socket = _BlockedSocket
socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network blocked"))

import public_market_collector.__main__ as entrypoint
from public_market_collector.bitfinex_docs import CHECKSUM_EXAMPLE_SIGNED, CHECKSUM_EXAMPLE_STRING, UNKNOWNS
from public_market_collector.config import (
    OPERATIONAL_VALUES,
    CaptureStopped,
    ConfigUnset,
    CrashFault,
    StartRefused,
    backoff_delay,
    check_instrument,
)
from public_market_collector.framing import (
    checksum_ok,
    decode_prefix,
    durable_view,
    dumps,
    gzip_member,
    jsonl_bytes,
    read_journal,
    sha256_bytes,
    verify_segment,
)
from public_market_collector.guards import scan_package
from public_market_collector.market import Book, canonical_norms, example_checksum_matches_docs, signed_crc32
from public_market_collector.session import Session, append_startup_gap, inspect_torn_tail


def _cfg(root: Path, **overrides):
    class Config:
        test_only = True
        ops_approved = False
        symbol = "tBTCUSD"
        frozen_symbol = "tBTCUSD"
        prec = "P0"
        freq = "F0"
        length = "25"
        depth_n = 25
        output_root = str(root)
        queue_cap = 20
        disk_cap_bytes = 50_000_000
        segment_id_book = "bookseg"
        segment_id_trades = "tradeseg"
        segment_id_norm_book = "nbook"
        segment_id_norm_trades = "ntrades"
        epoch_ids = ("epoch-a", "epoch-b", "epoch-c")
        clock_warn_ms = None
        clock_invalid_ms = None
        backoff_initial_s = None
        backoff_cap_s = None
        backoff_jitter_s = None
        heartbeat_timeout_s = None
        health_interval_s = None
        free_disk_bytes = 123456
        trade_quiet_warn_s = None
        segment_max_bytes = None
        seq_layout = None
        seq_scope = None

    cfg = Config()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _js(value) -> str:
    return json.dumps(value, separators=(",", ":"))


def _sub(channel: str, chan: int) -> str:
    return _js(
        {
            "event": "subscribed",
            "channel": channel,
            "chanId": chan,
            "symbol": "tBTCUSD",
            "prec": "P0",
            "freq": "F0",
            "len": "25",
        }
    )


def _book(chan: int, body, seq=None) -> str:
    if seq is None:
        return _js([chan, body])
    return _js([chan, seq, body])


def _cs(chan: int, value: int, seq=None) -> str:
    if seq is None:
        return _js([chan, "cs", value])
    return _js([chan, seq, "cs", value])


def _hb(chan: int, seq=None) -> str:
    if seq is None:
        return _js([chan, "hb"])
    return _js([chan, seq, "hb"])


def _te(chan: int, trade: list, kind: str = "te", seq=None) -> str:
    if seq is None:
        return _js([chan, kind, trade])
    return _js([chan, seq, kind, trade])


def _crc(entries) -> int:
    book = Book(prec="P0")
    book.replace_agg(entries)
    _text, value = book.checksum()
    return value


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        os.environ["BITFINEX_API_KEY"] = "canary-secret-value"

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, **overrides) -> Session:
        return Session(_cfg(self.root, **overrides))

    def _raw(self, session: Session, stream: str = "raw-book") -> dict:
        segment_id = session.segment_id[stream]
        path = session.root / ("raw/book" if stream == "raw-book" else "raw/trades") / f"{segment_id}.gz"
        records, stopped = session.journal_records()
        view = durable_view(path, records, segment_id)
        view["journal_stopped"] = stopped
        view["path"] = path
        view["journal"] = records
        return view

    def _norms(self, session: Session) -> list[dict]:
        rows = []
        journal, _stopped = session.journal_records()
        for stream, folder in (("norm-book", "norm/book"), ("norm-trades", "norm/trades")):
            path = session.root / folder / f"{session.segment_id[stream]}.gz"
            if not path.exists():
                continue
            view = durable_view(path, journal, session.segment_id[stream])
            self.assertEqual(view["verify_errors"], [])
            rows.extend(view["records"])
        raw = {row["raw_id"]: row for row in session.durable_raw}
        rows.sort(key=lambda row: (raw[row["raw_id"]]["ingest_order"], row["norm_ordinal"]))
        return rows

    def test_t01_sequence_contiguous_jump_and_reconnect_reset(self):
        session = self._session(seq_layout="index_1_unverified", seq_scope="connection")
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]], seq=1), recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_book(7, [6000, 2, 2], seq=2), recv_utc_ns=3, recv_mono_ns=3)
        session.handle(_book(7, [6000, 3, 3], seq=4), recv_utc_ns=4, recv_mono_ns=4)
        session.disconnect(recv_utc_ns=5, recv_mono_ns=5)
        session.handle(_book(7, [6000, 4, 4], seq=1), recv_utc_ns=6, recv_mono_ns=6)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]], seq=2), recv_utc_ns=7, recv_mono_ns=7)
        session.commit()
        norms = self._norms(session)
        deltas = [row for row in norms if row["channel"] == "book_delta"]
        self.assertTrue(deltas[0]["applied"])
        self.assertFalse(deltas[1]["applied"])
        self.assertFalse(deltas[2]["applied"])
        gaps = [row for row in norms if row["channel"] == "gap"]
        self.assertTrue(any((session.durable_raw[i]["payload"] or {}).get("cause") == "seq_jump" for i, row in enumerate(session.durable_raw) if row["channel"] == "gap"))
        self.assertTrue(any(row["payload"].get("cause") == "disconnect" and row["payload"]["conn_epoch_before"] != row["payload"]["conn_epoch_after"] for row in session.durable_raw if row["channel"] == "gap"))
        snaps = [row for row in norms if row["channel"] == "book_snapshot"]
        self.assertTrue(snaps[-1]["applied"])
        self.assertTrue(snaps[-1]["book_valid"])
        self.assertGreaterEqual(len(gaps), 1)

    def test_t02_checksum_invalid_until_resnapshot(self):
        entries = [[6000, 1, 1], [5900, 1, 2], [6100, 1, -3], [6200, 1, -4]]
        self.assertEqual(CHECKSUM_EXAMPLE_STRING, "6000:1:6100:-3:5900:2:6200:-4")
        book = Book(prec="P0")
        book.replace_agg(entries)
        self.assertEqual(book.checksum_string(), CHECKSUM_EXAMPLE_STRING)
        self.assertEqual(book.checksum()[1], CHECKSUM_EXAMPLE_SIGNED)
        self.assertTrue(example_checksum_matches_docs())
        good = _crc(entries)
        session = self._session()
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, entries), recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_cs(7, good), recv_utc_ns=3, recv_mono_ns=3)
        session.handle(_book(7, [6000, 2, 5]), recv_utc_ns=4, recv_mono_ns=4)
        session.handle(_cs(7, good), recv_utc_ns=5, recv_mono_ns=5)
        session.handle(_book(7, [6000, 3, 6]), recv_utc_ns=6, recv_mono_ns=6)
        session.handle(_book(7, entries), recv_utc_ns=7, recv_mono_ns=7)
        session.commit()
        norms = self._norms(session)
        checks = [row for row in norms if row["channel"] == "checksum"]
        self.assertTrue(checks[0]["checksum_ok"])
        self.assertFalse(checks[1]["checksum_ok"])
        deltas = [row for row in norms if row["channel"] == "book_delta"]
        self.assertTrue(deltas[0]["applied"])
        self.assertFalse(deltas[1]["applied"])
        snaps = [row for row in norms if row["channel"] == "book_snapshot"]
        self.assertTrue(snaps[-1]["applied"])
        self.assertTrue(snaps[-1]["book_valid"])
        raw_book = Book(prec="R0")
        raw_book.replace_raw([[20, 10, 1], [21, 10, 2], [30, 11, -3]])
        self.assertIn("20:1:30:-3:21:2", raw_book.checksum_string())
        raw_book.apply_raw([20, 0, 1])
        self.assertNotIn("20:", raw_book.checksum_string())
        self.assertIsNone(norms[-1]["queue_position"])

    def test_t03_reconnect_backoff_gap_and_fresh_snapshot(self):
        session = self._session(backoff_initial_s=1, backoff_cap_s=60, backoff_jitter_s=0, heartbeat_timeout_s=15)
        with self.assertRaises(ConfigUnset):
            backoff_delay(1, initial_s=None, cap_s=60, jitter_s=0)
        self.assertEqual([session.plan_reconnect(i) for i in (1, 2, 3, 7, 8)], [1, 2, 4, 60, 60])
        session.handle(_sub("book", 7), recv_utc_ns=1_000, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=2_000, recv_mono_ns=2)
        session.handle(_hb(7), recv_utc_ns=3_000, recv_mono_ns=3)
        self.assertTrue(session.poll_liveness(3_000 + 16_000_000_000))
        session.handle(_book(7, [6000, 2, 2]), recv_utc_ns=4_000, recv_mono_ns=4)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=5_000, recv_mono_ns=5)
        session.commit()
        norms = self._norms(session)
        deltas = [row for row in norms if row["channel"] == "book_delta"]
        self.assertFalse(deltas[0]["applied"])
        snaps = [row for row in norms if row["channel"] == "book_snapshot"]
        self.assertEqual(snaps[-1]["conn_epoch"], "epoch-b")
        self.assertTrue(snaps[-1]["applied"])
        self.assertTrue(any(row["payload"]["cause"] == "disconnect" for row in session.durable_raw if row["channel"] == "gap"))

    def test_t04_duplicate_and_out_of_order_trades(self):
        session = self._session()
        session.handle(_sub("trades", 8), recv_utc_ns=10, recv_mono_ns=10)
        first = _te(8, [50, 1000, 1, 10])
        second = _te(8, [40, None, -2, 9], kind="te")
        # Keep the missing timestamp explicit in the array the venue shape uses.
        second = _js([8, "te", [40, None, -2, 9]])
        third = _te(8, [50, 1001, 1, 10], kind="tu")
        session.handle(first, recv_utc_ns=11, recv_mono_ns=11)
        session.handle(second, recv_utc_ns=12, recv_mono_ns=12)
        session.handle(third, recv_utc_ns=13, recv_mono_ns=13)
        session.commit()
        view = self._raw(session, "raw-trades")
        verbatim = [row["payload_verbatim"] for row in view["records"]]
        self.assertIn(first, verbatim)
        self.assertIn(second, verbatim)
        self.assertIn(third, verbatim)
        norms = [row for row in self._norms(session) if row["channel"] == "trade"]
        self.assertEqual([row["trade_id"] for row in norms], [50, 40, 50])
        self.assertFalse(norms[0]["duplicate"])
        self.assertTrue(norms[1]["out_of_order"])
        self.assertTrue(norms[1]["counted"])
        self.assertTrue(norms[2]["duplicate"])
        self.assertFalse(norms[2]["counted"])
        self.assertEqual(norms[1]["ts_exchange_ms"], None)
        self.assertEqual(norms[1]["side"], "sell")
        self.assertEqual(norms[0]["update_kind"], "te")
        self.assertEqual(norms[2]["update_kind"], "tu")

    def test_t05_partial_gzip_tail_is_memory_only(self):
        payload = jsonl_bytes([{"raw_id": "mem:0:0", "channel": "info", "n": 1}])
        blob = gzip_member(payload) + b"PARTIAL-TAIL"
        path = Path(self.tmp.name) / "torn.gz"
        path.write_bytes(blob)
        before = path.read_bytes()
        listing = sorted(p.name for p in path.parent.iterdir())
        report = inspect_torn_tail(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(report["sha256"], sha256_bytes(before))
        self.assertEqual(report["decodable_records"], 1)
        self.assertEqual(report["last_record"]["raw_id"], "mem:0:0")
        self.assertFalse(report["durable"])
        self.assertIsNotNone(report["torn_offset"])
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), listing)
        journal_root = Path(self.tmp.name) / "journal-root"
        append_startup_gap(journal_root, path, report)
        self.assertEqual(path.read_bytes(), before)
        copies = [p for p in journal_root.rglob("*") if p.is_file() and p.read_bytes() == before]
        self.assertEqual(copies, [])
        startup, stopped = read_journal(journal_root / "index.journal")
        self.assertFalse(stopped)
        self.assertEqual(startup[0]["cause"], "startup")
        self.assertEqual(startup[0]["last_raw_id"], "mem:0:0")
        self.assertFalse(startup[0]["durable"])

    def test_t06_clock_thresholds_are_explicit_inputs(self):
        unset = Session(_cfg(Path(self.tmp.name) / "clock-unset"))
        with self.assertRaises(ConfigUnset):
            unset.observe_clock_offset(300, recv_utc_ns=1, recv_mono_ns=1)
        session = Session(_cfg(Path(self.tmp.name) / "clock", clock_warn_ms=250, clock_invalid_ms=1000))
        self.assertEqual(session.observe_clock_offset(100, recv_utc_ns=1, recv_mono_ns=1), "ok")
        self.assertEqual(session.observe_clock_offset(300, recv_utc_ns=2, recv_mono_ns=2), "warn")
        self.assertTrue(session.latency_analytics_valid)
        self.assertEqual(session.observe_clock_offset(1500, recv_utc_ns=3, recv_mono_ns=3), "invalid")
        self.assertFalse(session.latency_analytics_valid)
        session.commit()
        classes = [row["payload"]["clock_class"] for row in session.durable_raw if row["channel"] == "gap"]
        self.assertEqual(classes, ["warn", "invalid"])
        self.assertTrue(all(row["payload"]["cause"] == "clock" for row in session.durable_raw if row["channel"] == "gap"))

    def test_t07_stop_on_queue_disk_and_write_failure(self):
        original = self.root
        original.mkdir()
        planted = original / "original.bin"
        planted.write_bytes(b"keep-me")
        planted_hash = sha256_bytes(planted.read_bytes())
        session = self._session(queue_cap=1)
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        with self.assertRaises(CaptureStopped) as caught:
            session.handle(_hb(7), recv_utc_ns=2, recv_mono_ns=2)
        self.assertIn("queue_full", caught.exception.stderr)
        self.assertNotIn("alert", caught.exception.stderr.lower())
        journal, _stopped = session.journal_records()
        stop = [row for row in journal if row["kind"] == "capture_stop"]
        self.assertEqual(stop[0]["detail"], "queue_full")
        self.assertEqual(stop[0]["rejected_sha256"], sha256_bytes(_hb(7).encode("utf-8")))
        raw_text = (session.root / "raw/book/bookseg.gz").read_bytes()
        self.assertNotIn(_hb(7).encode("utf-8"), gzip_payload_text(raw_text))
        self.assertEqual(planted.read_bytes(), b"keep-me")
        self.assertEqual(sha256_bytes(planted.read_bytes()), planted_hash)

        disk_root = Path(self.tmp.name) / "disk"
        disk = Session(_cfg(disk_root, disk_cap_bytes=0))
        with self.assertRaises(CaptureStopped) as disk_caught:
            disk.handle(_hb(7), recv_utc_ns=1, recv_mono_ns=1)
            disk.commit()
        self.assertIn("disk_full", disk_caught.exception.stderr)
        self.assertNotIn("alert", disk_caught.exception.stderr.lower())
        self.assertFalse((disk_root / "raw/book/bookseg.gz").exists())

        io_root = Path(self.tmp.name) / "io"
        io_session = Session(_cfg(io_root))
        io_session.handle(_hb(7), recv_utc_ns=1, recv_mono_ns=1)
        io_session.fault = "io_error"
        with self.assertRaises(CaptureStopped) as io_caught:
            io_session.commit()
        self.assertIn("io_error", io_caught.exception.stderr)
        self.assertNotIn("alert", io_caught.exception.stderr.lower())
        self.assertEqual(planted.read_bytes(), b"keep-me")
        for folder in (self.root, disk_root, io_root):
            for path in folder.rglob("*"):
                if path.is_file() and path.name == "original.bin":
                    self.assertEqual(path.read_bytes(), b"keep-me")

    def test_t08_instrument_mismatch_and_depth_header(self):
        self.assertTrue(all(value is None for value in OPERATIONAL_VALUES.values()))
        with self.assertRaises(StartRefused):
            check_instrument(None, "tBTCUSD")
        missing = Path(self.tmp.name) / "missing"
        with self.assertRaises(StartRefused):
            Session(_cfg(missing, symbol="tBTCF0:USTF0", frozen_symbol="tBTCUSD"))
        self.assertFalse(missing.exists())
        session = self._session(prec="P0", freq="F0", length="25", depth_n=25)
        session.handle(_hb(7), recv_utc_ns=1, recv_mono_ns=1)
        session.commit()
        header = self._raw(session)["records"][0]
        self.assertEqual(header["payload"]["prec"], "P0")
        self.assertEqual(header["payload"]["freq"], "F0")
        self.assertEqual(header["payload"]["len"], "25")
        self.assertEqual(header["payload"]["depth_n"], 25)
        self.assertTrue(header["payload"]["subscribed_depth_not_whole_exchange_book"])
        self.assertIsNone(header["payload"]["queue_position"])
        self.assertFalse(header["payload"]["board_freeze"])

    def test_t09_commit_order_crash_tail_and_prior_record(self):
        session = self._session()
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=2, recv_mono_ns=2)
        session.commit()
        first = [row["raw_id"] for row in self._raw(session)["records"]]
        self._assert_commit_order(session.trace, "raw-book")
        session.handle(_hb(7), recv_utc_ns=3, recv_mono_ns=3)
        session.fault = "after_fsync_before_commit"
        with self.assertRaises(CrashFault):
            session.commit()
        view = self._raw(session)
        self.assertEqual([row["raw_id"] for row in view["records"]], first)
        self.assertGreater(view["lost_uncommitted_tail_bytes"], 0)
        self.assertGreaterEqual(view["decodable_uncommitted_members"], 1)
        self.assertTrue(view["fsynced_bytes_are_not_records"])
        self.assertNotIn("hb", " ".join(row["channel"] for row in view["records"]))

    def test_t10_static_and_runtime_isolation(self):
        self.assertEqual(scan_package(), [])
        with self.assertRaises(RuntimeError):
            socket.socket()
        with self.assertRaises(RuntimeError):
            socket.create_connection(("api-pub.bitfinex.com", 443))
        self.assertEqual(entrypoint.main(["--start"]), 78)
        self.assertIn("OPS_NOT_APPROVED", entrypoint.REFUSAL)
        self.assertIn("BOARD_FREEZE_UNSET", entrypoint.REFUSAL)

    def test_t11_health_journal_heartbeat_is_not_freshness(self):
        session = self._session(health_interval_s=10, trade_quiet_warn_s=600)
        session.handle(_sub("book", 7), recv_utc_ns=0, recv_mono_ns=0)
        session.handle(_hb(7), recv_utc_ns=1_000, recv_mono_ns=1)
        self.assertIsNotNone(session.write_health(0))
        self.assertIsNone(session.write_health(5_000_000_000))
        second = session.write_health(10_000_000_000)
        self.assertIsNotNone(second)
        self.assertIsNone(second["event_lag_ms"])
        self.assertIsNone(second["last_exchange_event_time_ms"])
        self.assertEqual(second["liveness_receive_time_ns"], 1_000)
        path = session.health.path
        before = path.read_bytes()
        path.write_bytes(before + b'{"kind":"partial"')
        records, stopped = read_journal(path)
        self.assertTrue(stopped)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["kind"], "health")
        self.assertTrue(path.read_bytes().startswith(before))

    def test_t12_secrets_and_single_root(self):
        session = self._session(health_interval_s=10)
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_hb(7), recv_utc_ns=2, recv_mono_ns=2)
        session.commit()
        session.write_health(0)
        blob = b"".join(path.read_bytes() for path in session.root.rglob("*") if path.is_file())
        text = blob.decode("utf-8", errors="ignore")
        for banned in ("canary-secret-value", "api_key", "authorization", "Bearer", "alert sent", "alert_sent"):
            self.assertNotIn(banned, text)
        self.assertNotIn("alert", session.stderr.lower())
        parent = session.root.parent
        self.assertEqual([p.name for p in parent.iterdir() if p.is_dir()], [session.root.name])

    def test_t13_corrupt_last_member_is_not_repaired(self):
        session = self._session()
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=1, recv_mono_ns=1)
        session.commit()
        path = self._raw(session)["path"]
        original = path.read_bytes()
        path.write_bytes(original + b"\x1f\x8bNOT-A-MEMBER")
        view = self._raw(session)
        self.assertEqual(path.read_bytes(), original + b"\x1f\x8bNOT-A-MEMBER")
        self.assertGreater(len(view["records"]), 0)
        self.assertGreater(view["lost_uncommitted_tail_bytes"], 0)
        self.assertTrue(path.read_bytes().startswith(original))

    def test_t14_crash_between_fsync_and_commit(self):
        session = self._session()
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=1, recv_mono_ns=1)
        session.fault = "after_fsync_before_commit"
        with self.assertRaises(CrashFault) as caught:
            session.commit()
        self.assertEqual(caught.exception.mode, "after_fsync_before_commit")
        view = self._raw(session)
        self.assertEqual(view["records"], [])
        self.assertEqual(view["durable_count"], 0)
        self.assertGreaterEqual(view["decodable_uncommitted_members"], 1)
        self.assertTrue(view["fsynced_bytes_are_not_records"])
        self.assertGreater(view["lost_uncommitted_tail_bytes"], 0)

    def test_t15_torn_commit_line_keeps_prior_record(self):
        session = self._session()
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=1, recv_mono_ns=1)
        session.commit()
        prior, stopped = session.journal_records()
        self.assertFalse(stopped)
        self.assertTrue(prior)
        session.handle(_hb(7), recv_utc_ns=2, recv_mono_ns=2)
        session.fault = "torn_journal"
        with self.assertRaises(CrashFault):
            session.commit()
        records, stopped = read_journal(session.index.path)
        self.assertTrue(stopped)
        self.assertEqual(records[0]["kind"], "commit")
        self.assertTrue(checksum_ok(records[0]))
        self.assertEqual([row["kind"] for row in records], [row["kind"] for row in prior])
        session.index.path.write_bytes(session.index.path.read_bytes() + dumps({"kind": "later", "record_checksum": "nope"}).encode() + b"\n")
        again, stopped_again = read_journal(session.index.path)
        self.assertTrue(stopped_again)
        self.assertEqual(again, records)

    def test_t16_raw_id_ranges_and_mutation(self):
        session = self._session()
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=1, recv_mono_ns=1)
        session.commit()
        session.handle(_hb(7), recv_utc_ns=2, recv_mono_ns=2)
        session.commit()
        session.close_stream("raw-book")
        journal, _stopped = session.journal_records()
        close = next(row for row in journal if row.get("kind") == "close" and row.get("stream") == "raw-book")
        commits = [row for row in journal if row.get("kind") == "commit" and row.get("stream") == "raw-book"]
        self.assertGreaterEqual(len(commits), 2)
        self.assertNotIn("segment_sha256", commits[0])
        self.assertNotEqual(commits[0]["member_sha256"], close["segment_sha256"])
        path = session.root / "raw/book/bookseg.gz"
        blob = path.read_bytes()
        self.assertEqual(sha256_bytes(blob[commits[0]["member_start_offset"] : commits[0]["member_end_offset"]]), commits[0]["member_sha256"])
        self.assertEqual(sha256_bytes(blob[: commits[0]["member_end_offset"]]), commits[0]["prefix_sha256"])
        self.assertEqual(sha256_bytes(blob), close["segment_sha256"])
        checked = verify_segment(path, journal, "bookseg")
        self.assertTrue(checked["ok"], checked["errors"])
        norms = self._norms(session)
        raw_ids = {row["raw_id"] for row in self._raw(session)["records"]}
        self.assertTrue(raw_ids)
        self.assertTrue(all(row["raw_id"] in raw_ids for row in norms if row["stream"] == "raw-book"))
        mutated = bytearray(blob)
        mutated[commits[0]["member_start_offset"] + 10] ^= 0x01
        path.write_bytes(mutated)
        self.assertTrue(checksum_ok(close))
        rejected = verify_segment(path, journal, "bookseg")
        self.assertFalse(rejected["ok"])
        self.assertIn("prefix_range", rejected["errors"])
        close_looking = dict(close)
        close_looking.pop("record_checksum", None)
        close_looking["segment_sha256"] = sha256_bytes(bytes(mutated))
        close_looking["bytes"] = len(mutated)
        swapped = [close_looking if row is close or row.get("kind") == "close" else row for row in journal]
        still = verify_segment(path, swapped, "bookseg")
        self.assertFalse(still["ok"])
        self.assertIn("prefix_range", still["errors"])
        self.assertNotIn("segment_sha", still["errors"])
        self.assertNotEqual(commits[0]["member_sha256"], close_looking["segment_sha256"])

    def test_t17_book_validity_intervals_across_checksum_failure(self):
        entries = [[6000, 1, 1], [6100, 1, -1]]
        session = self._session()
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, entries), recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_cs(7, 1), recv_utc_ns=3, recv_mono_ns=3)
        session.handle(_book(7, entries), recv_utc_ns=4, recv_mono_ns=4)
        session.commit()
        norms = self._norms(session)
        opens = [row for row in norms if row["interval_event"] == "open"]
        closes = [row for row in norms if row["interval_event"] == "close"]
        self.assertEqual(len(opens), 2)
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["validity_interval_id"], opens[0]["validity_interval_id"])
        self.assertNotEqual(opens[0]["validity_interval_id"], opens[1]["validity_interval_id"])
        gap = next(row for row in session.durable_raw if row["channel"] == "gap")
        self.assertEqual(gap["payload"]["cause"], "checksum_fail")
        self.assertTrue(opens[1]["book_valid"])

    def test_t18_replay_is_identical_and_keeps_null_timestamps(self):
        session = self._session()
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_hb(7), recv_utc_ns=3, recv_mono_ns=3)
        session.handle(_sub("trades", 8), recv_utc_ns=4, recv_mono_ns=4)
        session.handle(_js([8, "te", [7, None, 1, 10]]), recv_utc_ns=5, recv_mono_ns=5)
        session.commit()
        stored = self._norms(session)
        first = canonical_norms(session.durable_raw, prec="P0", seq_scope=None, seq_layout=None)
        second = canonical_norms(session.durable_raw, prec="P0", seq_scope=None, seq_layout=None)
        self.assertEqual(first, second)
        self.assertEqual(first, "\n".join(dumps(row) for row in stored).encode("utf-8"))
        heartbeat = next(row for row in stored if row["channel"] == "heartbeat")
        book = next(row for row in stored if row["channel"] == "book_snapshot")
        trade = next(row for row in stored if row["channel"] == "trade")
        self.assertIsNone(heartbeat["ts_exchange_ms"])
        self.assertIsNone(book["ts_exchange_ms"])
        self.assertIsNone(trade["ts_exchange_ms"])
        self.assertNotEqual(heartbeat["ts_exchange_ms"], heartbeat["ts_recv_utc_ns"])
        self.assertTrue(UNKNOWNS)

    def _assert_commit_order(self, trace: list[str], stream: str) -> None:
        tokens = [
            f"{stream}:close_member",
            f"{stream}:fsync_segment",
            f"{stream}:checksummed_commit",
            f"{stream}:checksummed_commit:fsync_journal",
        ]
        indexes = []
        start = 0
        for token in tokens:
            idx = trace.index(token, start)
            indexes.append(idx)
            start = idx + 1
        self.assertEqual(indexes, sorted(indexes))


def gzip_payload_text(blob: bytes) -> bytes:
    rows, _torn = decode_prefix(blob)
    return dumps(rows).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
