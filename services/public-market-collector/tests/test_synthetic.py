"""Synthetic acceptance for the public market collector. No live socket."""

from __future__ import annotations

import gzip
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
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
from public_market_collector import guards as guards_mod
from public_market_collector.guards import scan_package
from public_market_collector.market import Book, canonical_norms, example_checksum_matches_docs, signed_crc32
from public_market_collector.session import Session, append_startup_gap, inspect_torn_tail

# Integrity checks compare recorded bytes. They detect corruption. They are not a
# tamper-proof seal against someone who rewrites a commit together with its checksum.
# Sustained backpressure is not proven by the queue fixture. SEQ_ALL, TIMESTAMP,
# reconnect limits, checksum float spelling, and NTP stay unverified.
KILLED_LINES = []
_AUDIT_HOOKED = False


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
        """B: fresh on-disk raw, through journal verify, renormalized, compared to on-disk norm."""
        session = self._session()
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_hb(7), recv_utc_ns=3, recv_mono_ns=3)
        session.handle(_sub("trades", 8), recv_utc_ns=4, recv_mono_ns=4)
        session.handle(_js([8, "te", [7, None, 1, 10]]), recv_utc_ns=5, recv_mono_ns=5)
        session.handle(_js([8, "te", [9, 1574694478808, -2, 11]]), recv_utc_ns=6, recv_mono_ns=6)
        session.commit()
        root = Path(session.root)
        _journal, loaded = _disk_committed(root)
        raw_rows = list(loaded["raw-book"]) + list(loaded["raw-trades"])
        raw_rows.sort(key=lambda row: int(row["ingest_order"]))
        self.assertTrue(all(row["schema_v"] == "pmc_raw_v1" for row in raw_rows))
        raw_ids = {row["raw_id"] for row in raw_rows}
        self.assertTrue(raw_ids)
        self.assertTrue(all(str(raw_id).count(":") == 2 for raw_id in raw_ids))
        renormalized = canonical_norms(raw_rows, prec="P0", seq_scope=None, seq_layout=None)
        again = canonical_norms(raw_rows, prec="P0", seq_scope=None, seq_layout=None)
        self.assertEqual(renormalized, again)
        stored_rows = list(loaded["norm-book"]) + list(loaded["norm-trades"])
        order = {row["raw_id"]: int(row["ingest_order"]) for row in raw_rows}
        stored_rows.sort(key=lambda row: (order[row["raw_id"]], int(row["norm_ordinal"])))
        stored = "\n".join(dumps(row) for row in stored_rows).encode("utf-8")
        self.assertEqual(stored, renormalized)
        self.assertTrue(all(row["norm_schema_v"] == "pmc_norm_v1" for row in stored_rows))
        self.assertTrue(all(row["raw_id"] in raw_ids for row in stored_rows))
        for commit in _journal:
            if commit.get("kind") != "commit" or not str(commit.get("stream", "")).startswith("norm"):
                continue
            blob = (root / commit["segment_rel"]).read_bytes()
            chunk = blob[int(commit["member_start_offset"]) : int(commit["member_end_offset"])]
            self.assertEqual(sha256_bytes(chunk), commit["member_sha256"])
            payload = gzip.decompress(chunk)
            self.assertTrue(payload.endswith(b"\n"))
            self.assertIn(payload.splitlines()[0], stored)
        heartbeat = next(row for row in stored_rows if row["channel"] == "heartbeat")
        book = next(row for row in stored_rows if row["channel"] == "book_snapshot")
        null_trade = next(row for row in stored_rows if row["trade_id"] == 7)
        timed_trade = next(row for row in stored_rows if row["trade_id"] == 9)
        self.assertIsNone(heartbeat["ts_exchange_ms"])
        self.assertIsNone(book["ts_exchange_ms"])
        self.assertEqual(book["applied"], True)
        self.assertIsNone(null_trade["ts_exchange_ms"])
        self.assertEqual(null_trade["side"], "buy")
        self.assertEqual(null_trade["price"], 10)
        self.assertEqual(null_trade["amount"], 1)
        self.assertEqual(timed_trade["ts_exchange_ms"], 1574694478808)
        self.assertEqual(timed_trade["side"], "sell")
        self.assertNotEqual(heartbeat["ts_exchange_ms"], heartbeat["ts_recv_utc_ns"])
        mutated = [dict(row) for row in raw_rows]
        mutated[0] = dict(mutated[0], ts_exchange_ms=123456)
        bad = canonical_norms(mutated, prec="P0", seq_scope=None, seq_layout=None)
        self.assertNotEqual(bad, stored)
        killed("B-accept-mutated-raw", lambda: self.assertEqual(bad, stored))
        session.durable_raw[0]["symbol"] = "MEMORY-MUTATION"
        _journal2, loaded2 = _disk_committed(root)
        raw2 = list(loaded2["raw-book"]) + list(loaded2["raw-trades"])
        raw2.sort(key=lambda row: int(row["ingest_order"]))
        self.assertEqual(canonical_norms(raw2, prec="P0", seq_scope=None, seq_layout=None), stored)
        self.assertNotIn("MEMORY-MUTATION", stored.decode("utf-8"))
        self.assertTrue(UNKNOWNS)
        self.assertTrue(_journal2)

    def test_c_midstream_nonzero_disk_cap(self):
        """C: nonzero cap, committed data already present, then a loud stop."""
        root = Path(self.tmp.name) / "midstream-cap"
        session = Session(_cfg(root, disk_cap_bytes=50_000_000))
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=2, recv_mono_ns=2)
        session.commit()
        prior = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        segment = root / "raw/book/bookseg.gz"
        segment_hash = sha256_bytes(segment.read_bytes())
        journal_before = (root / "index.journal").read_bytes()
        committed_ids = [row["raw_id"] for row in durable_view(segment, read_journal(root / "index.journal")[0], "bookseg")["records"]]
        self.assertGreaterEqual(len(committed_ids), 1)
        wide = [[6000 + i, 1, 1 if i % 2 == 0 else -1] for i in range(40)]
        session.handle(_book(7, wide), recv_utc_ns=3, recv_mono_ns=3)
        predicted = {
            "kind": "capture_stop",
            "cause": "write_fail",
            "detail": "disk_full",
            "received": session.received,
            "durable": session.durable,
            "attempted": session.attempted,
            "rejected_counted": False,
            "rejected_sha256": None,
        }
        slack = len(dumps(predicted)) + 80
        estimate = 80 + sum(len(dumps(row)) for row in session.buffer["raw-book"])
        self.assertGreater(estimate, slack)
        session.cfg.disk_cap_bytes = _directory_size(root) + slack
        self.assertGreater(session.cfg.disk_cap_bytes, 0)
        with self.assertRaises(CaptureStopped) as caught:
            session.commit()
        stderr = caught.exception.stderr
        self.assertIn("CAPTURE_STOP", stderr)
        self.assertIn("disk_full", stderr)
        self.assertNotIn("alert", stderr.lower())
        self.assertEqual(segment.read_bytes(), prior["raw/book/bookseg.gz"])
        self.assertEqual(sha256_bytes(segment.read_bytes()), segment_hash)
        self.assertGreaterEqual(segment.stat().st_size, len(prior["raw/book/bookseg.gz"]))
        journal_after = (root / "index.journal").read_bytes()
        self.assertTrue(journal_after.startswith(journal_before))
        self.assertGreater(len(journal_after), len(journal_before))
        records, stopped = read_journal(root / "index.journal")
        self.assertFalse(stopped)
        self.assertTrue(any(row.get("kind") == "capture_stop" and row.get("detail") == "disk_full" for row in records))
        self.assertNotIn(b"alert", journal_after.lower())
        view = durable_view(segment, records, "bookseg")
        self.assertEqual(view.get("verify_errors"), [])
        self.assertEqual([row["raw_id"] for row in view["records"]], committed_ids)
        self.assertEqual(view["lost_uncommitted_tail_bytes"], 0)
        self.assertEqual(view["decodable_uncommitted_members"], 0)
        for rel, blob in prior.items():
            path = root / rel
            self.assertTrue(path.is_file())
            if rel != "index.journal":
                self.assertEqual(path.read_bytes(), blob)
            self.assertGreaterEqual(path.stat().st_size, len(blob))
        def _require_segment(blob: bytes) -> None:
            if sha256_bytes(blob) != segment_hash or len(blob) < len(prior["raw/book/bookseg.gz"]):
                raise AssertionError("segment changed")

        _require_segment(segment.read_bytes())
        killed("C-truncated-segment", lambda: _require_segment(segment.read_bytes()[:-1]))
        killed(
            "C-stop-removed",
            lambda: self.assertTrue(any(row.get("kind") == "capture_stop" for row in records if row.get("kind") != "capture_stop")),
        )

    def test_d_static_denylist_and_runtime_failclosed(self):
        """D: denylist plants, plus the real Session and entrypoint under a tripwire."""
        plant = Path(self.tmp.name) / "plants"
        plant.mkdir()
        (plant / "use_asyncio.py").write_text("import asyncio\n")
        (plant / "use_socket.py").write_text("import _socket\n")
        (plant / "use_importlib.py").write_text("import importlib\n")
        (plant / "use_dunder.py").write_text("x = __import__('json')\n")
        findings = scan_package(plant)
        self.assertTrue(any("import asyncio" in item for item in findings))
        self.assertTrue(any("import _socket" in item for item in findings))
        self.assertTrue(any("import importlib" in item for item in findings))
        self.assertTrue(any("call __import__" in item for item in findings))
        self.assertEqual(scan_package(), [])
        guards_mod.FORBIDDEN_IMPORT_ROOTS.remove("asyncio")
        try:
            weakened = scan_package(plant)
            killed("D-asyncio-denylist-removed", lambda: self.assertTrue(any("import asyncio" in item for item in weakened)))
        finally:
            guards_mod.FORBIDDEN_IMPORT_ROOTS.add("asyncio")
        self.assertTrue(any("import asyncio" in item for item in scan_package(plant)))
        guards_mod.FORBIDDEN_CALLS.remove("__import__")
        try:
            weakened_call = scan_package(plant)
            killed("D-dunder-import-removed", lambda: self.assertTrue(any("call __import__" in item for item in weakened_call)))
        finally:
            guards_mod.FORBIDDEN_CALLS.add("__import__")
        canary = "canary-secret-value"
        os.environ["BITFINEX_API_KEY"] = canary
        runtime = Path(self.tmp.name) / "runtime-guard"
        session = Session(_cfg(runtime))
        events = []

        def _audit(event, args):
            del args
            if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
                events.append(event)
                raise RuntimeError(f"fail-closed {event}")

        global _AUDIT_HOOKED
        if not _AUDIT_HOOKED:
            sys.addaudithook(_audit)
            _AUDIT_HOOKED = True
        stderr = io.StringIO()
        real_env = os.environ

        class _EnvTrap:
            def __getitem__(self, key):
                raise RuntimeError(f"env read {key}")

            def get(self, key, default=None):
                raise RuntimeError(f"env read {key}")

            def __contains__(self, key):
                raise RuntimeError(f"env read {key}")

        os.environ = _EnvTrap()
        try:
            with redirect_stderr(stderr):
                code = entrypoint.main([])
                session.handle(_hb(7), recv_utc_ns=1, recv_mono_ns=1)
                session.commit()
        finally:
            os.environ = real_env
        self.assertEqual(code, 78)
        self.assertIn("BOARD_FREEZE_UNSET", stderr.getvalue())
        self.assertEqual(events, [])
        self.assertNotIn(canary, stderr.getvalue())
        written = b"".join(path.read_bytes() for path in runtime.rglob("*") if path.is_file())
        self.assertNotIn(canary.encode("utf-8"), written)
        self.assertIn(canary, real_env.get("BITFINEX_API_KEY", ""))

        def _require_socket_block():
            try:
                socket.socket()
            except RuntimeError:
                return
            raise AssertionError("socket was not blocked")

        _require_socket_block()

        class _Silent:
            def __init__(self, *args, **kwargs):
                del args, kwargs

        saved_socket = socket.socket
        socket.socket = _Silent
        try:
            killed("D-socket-tripwire-removed", _require_socket_block)
        finally:
            socket.socket = saved_socket
        _require_socket_block()

    def test_nongate_e_malformed_checksum_and_noninteger_price(self):
        """Not an acceptance gate. Existing fail-closed checksum behaviour only."""
        session = self._session()
        session.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_book(7, [[6000, 1, 1], [6100, 1, -1]]), recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_js([7, "cs"]), recv_utc_ns=3, recv_mono_ns=3)
        session.commit()
        self.assertTrue(any(row["channel"] == "gap" and row["payload"].get("cause") == "checksum_fail" for row in session.durable_raw))
        other = Session(_cfg(Path(self.tmp.name) / "noninteger"))
        other.handle(_sub("book", 7), recv_utc_ns=1, recv_mono_ns=1)
        other.handle(_book(7, [[6000.5, 1, 1.5], [6100, 1, -1]]), recv_utc_ns=2, recv_mono_ns=2)
        other.handle(_cs(7, 1), recv_utc_ns=3, recv_mono_ns=3)
        other.commit()
        self.assertTrue(any(row["channel"] == "gap" and row["payload"].get("cause") == "checksum_fail" for row in other.durable_raw))

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


def _directory_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def killed(label, check) -> None:
    """Run a check that must fail. The mutation exists only for this call."""
    try:
        check()
    except AssertionError as exc:
        line = f"KILLED_MUTATION {label} result=FAIL detail={exc.__class__.__name__}"
        KILLED_LINES.append(line)
        print(line, flush=True)
        return
    raise AssertionError(f"KILLED_MUTATION {label} was not killed")


def _disk_committed(root: Path) -> tuple[list[dict], dict[str, list[dict]]]:
    """Fresh reader: journal file and segment bytes only. No session memory."""
    journal, stopped = read_journal(root / "index.journal")
    if stopped:
        raise AssertionError("journal tail is not a complete prefix")
    loaded: dict[str, list[dict]] = {}
    seen = set()
    for row in journal:
        if row.get("kind") != "commit" or row.get("segment_id") in seen:
            continue
        seen.add(row["segment_id"])
        path = root / row["segment_rel"]
        verified = verify_segment(path, journal, row["segment_id"])
        if verified["errors"]:
            raise AssertionError(verified["errors"])
        view = durable_view(path, journal, row["segment_id"])
        if view.get("verify_errors"):
            raise AssertionError(view["verify_errors"])
        loaded[row["stream"]] = view["records"]
    return journal, loaded


def gzip_payload_text(blob: bytes) -> bytes:
    rows, _torn = decode_prefix(blob)
    return dumps(rows).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
