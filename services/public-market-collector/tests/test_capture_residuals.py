"""HM R2 residuals at 9840bca6 (items 1-3 blocking, 5-17 non-blocking).

Each test uses the real failure shape: a PublicConnection over a scripted socket
that ends with EOF or a close frame, a mixed book and trade long run, and a
resume of an intact segment larger than the 256 KiB tail read. No live socket.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import public_market_collector.framing as framing
import public_market_collector.market as market
import public_market_collector.public_run as public_run
from public_market_collector.config import CaptureStopped, StartRefused
from public_market_collector.framing import read_journal, verify_segment
from public_market_collector.market import Book, js_number_string
from public_market_collector.output_policy import output_refusal
from public_market_collector.public_run import (
    YIELD_EVERY,
    DiskWatch,
    Reconnect,
    cite_prior_segments,
    open_session,
    pump,
    run_forever,
)
from public_market_collector.session import TAIL_READ_MAX, inspect_torn_tail
from public_market_collector.ws_public import (
    SYMBOL,
    WS_GUID,
    encode_server_frame,
    open_public_socket,
)


def _js(value) -> str:
    return json.dumps(value, separators=(",", ":"))


class _ScriptSock:
    """Server side of one websocket. Ends with EOF (recv returns b"") or a close frame."""

    def __init__(self, texts, *, end="eof", status=b"101 Switching Protocols", bad_accept=False, raw_frames=()):
        self.texts = list(texts)
        self.end = end
        self.status = status
        self.bad_accept = bad_accept
        self.raw_frames = list(raw_frames)
        self.sent = b""
        self.incoming = b""
        self._armed = False
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent += data
        if self._armed:
            return
        key = ""
        for line in self.sent.decode("ascii").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest())
        if self.bad_accept:
            accept = b"AAAA" + accept[4:]
        head = b"HTTP/1.1 " + self.status + b"\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        body = b"".join(encode_server_frame(0x1, text.encode("utf-8")) for text in self.texts)
        body += b"".join(self.raw_frames)
        if self.end == "close":
            body += encode_server_frame(0x8, b"")
        self.incoming = head + body
        self._armed = True

    def recv(self, size: int) -> bytes:
        chunk = self.incoming[:size]
        self.incoming = self.incoming[size:]
        return chunk

    def settimeout(self, _timeout) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _connector(socks):
    queue = list(socks)

    def connect():
        sock = queue.pop(0)
        return open_public_socket(socket_factory=lambda _address, _timeout: sock, ssl_wrap=lambda raw, _host: raw)

    return connect


class _Clock:
    def __init__(self, start_ns=1_700_000_000_000_000_000, step_ns=1_000_000):
        self.now = start_ns
        self.step = step_ns

    def __call__(self) -> int:
        self.now += self.step
        return self.now


class ResidualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.free = 10**12

    def tearDown(self):
        self.tmp.cleanup()

    def _probe(self, _path):
        return self.free

    def _open(self, name, **kwargs):
        return open_session(
            self.base / name,
            resume=kwargs.pop("resume", False),
            repo_root=None,
            allow_root=self.base,
            disk_free_probe=self._probe,
            **kwargs,
        )

    def _run(self, name, connect, sleeps, *, max_connects, clock=None, **kwargs):
        clock = clock or _Clock()
        return run_forever(
            self.base / name,
            resume=kwargs.pop("resume", False),
            repo_root=None,
            allow_root=self.base,
            connect=connect,
            sleep=sleeps.append,
            clock_ns=clock,
            mono_ns=clock,
            deprioritize=lambda: "posix_nice_10",
            disk_free_probe=self._probe,
            max_connects=max_connects,
            **kwargs,
        )

    # ---- item 1 (B1) and item 10: bounded trace, in-place state, count window ----
    def test_item01_item10_mixed_long_run_keeps_trace_and_state_bounded(self):
        session = self._open("long-mixed", segment_max_bytes=10**12, segment_max_age_s=10**12)
        rng = random.Random(988)
        mirror = Book()
        bids = [[60000 - i, 1, round(0.1 + i * 0.01, 8)] for i in range(250)]
        asks = [[60001 + i, 1, -round(0.1 + i * 0.01, 8)] for i in range(250)]
        snapshot = bids + asks
        mirror.replace_agg(snapshot)
        copies = {"n": 0}
        real_copy = market._copy_state

        def counting_copy(state):
            copies["n"] += 1
            return real_copy(state)

        saved_window = market.TRADE_ID_WINDOW
        market.TRADE_ID_WINDOW = 1000
        market._copy_state = counting_copy
        t = 1_700_000_000_000_000_000
        session.handle(_js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL, "len": "250"}), recv_utc_ns=t, recv_mono_ns=t)
        session.handle(_js({"event": "subscribed", "channel": "trades", "chanId": 8, "symbol": SYMBOL}), recv_utc_ns=t, recv_mono_ns=t)
        session.handle(_js([7, snapshot]), recv_utc_ns=t, recv_mono_ns=t)
        trade_id = 1_000_000
        total = 20_000
        mid_current = None
        tracemalloc.start()
        try:
            for index in range(total):
                t += 20_000_000
                roll = rng.random()
                if roll < 0.70:
                    side = rng.choice((1, -1))
                    price = (60000 - rng.randrange(250)) if side > 0 else (60001 + rng.randrange(250))
                    entry = [price, 1, round(side * rng.uniform(0.01, 2.0), 8)]
                    mirror.apply_agg(entry)
                    text = _js([7, entry])
                elif roll < 0.95:
                    trade_id += rng.randrange(1, 4)
                    mts = t // 1_000_000
                    text = _js([8, "te", [trade_id, mts, round(rng.uniform(-1, 1) or 0.5, 8), 60000.5]])
                else:
                    text = _js([7, "cs", mirror.checksum()[1]])
                session.handle(text, recv_utc_ns=t, recv_mono_ns=t)
                if session.uncommitted_received >= YIELD_EVERY:
                    session.commit()
                if index == 5_000:
                    mid_current, _peak = tracemalloc.get_traced_memory()
            session.commit()
            end_current, _peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            market._copy_state = real_copy
            market.TRADE_ID_WINDOW = saved_window
        session.release()
        self.assertTrue(session.shadow.valid, "mirror checksums should keep the book valid")
        self.assertEqual(session.gap_count, 0)
        self.assertLessEqual(len(session.trace), 1024, "trace must stay bounded over a long run")
        self.assertIsNotNone(getattr(session.trace, "maxlen", None))
        self.assertEqual(copies["n"], 0, "capture must not copy the book or trade-id set per record")
        self.assertLessEqual(len(session.shadow.seen_trade_ids), 1000)
        self.assertLessEqual(len(session._norm_state.seen_trade_ids), 1000)
        self.assertEqual(len(session.shadow.seen_trade_order), len(session.shadow.seen_trade_ids))
        self.assertLess(end_current - mid_current, 1024 * 1024)

    def test_item10_count_window_catches_duplicates_independent_of_id_spacing(self):
        saved = market.TRADE_ID_WINDOW
        market.TRADE_ID_WINDOW = 3
        try:
            state = market.FeedState(prec="P0", seq_scope=None)
            ids = [10, 10_000_000, 20_000_000]

            def trade(trade_id):
                return {"channel": "trade", "payload": [8, "te", [trade_id, 1, 1.0, 1.0]], "stream": "raw-trades"}

            for trade_id in ids:
                same = market.step(state, trade(trade_id), seq_layout=None, inplace=True)
                self.assertIs(same.state, state)
            dup = market.step(state, trade(10), seq_layout=None, inplace=True).norms[0]
            self.assertTrue(dup["duplicate"], "id 10 is within the last 3 ids by count")
            market.step(state, trade(30_000_000), seq_layout=None, inplace=True)
            kept = market.step(state, trade(10_000_000), seq_layout=None, inplace=True).norms[0]
            self.assertTrue(kept["duplicate"], "by count, 10M is still one of the last 3 ids; an id-space window drops it")
            gone = market.step(state, trade(10), seq_layout=None, inplace=True).norms[0]
            self.assertFalse(gone["duplicate"])
            self.assertTrue(gone["out_of_order"])
        finally:
            market.TRADE_ID_WINDOW = saved

    # ---- item 2 (B3): backoff resets after a real ConnectionError ----
    def test_item02_backoff_resets_after_real_connection_error(self):
        socks = []
        for index in range(8):
            socks.append(_ScriptSock([_js([7, "hb"]), _js([7, "hb"])], end="eof" if index % 2 == 0 else "close"))
        sleeps = []
        self._run("reset-real", _connector(socks), sleeps, max_connects=8)
        self.assertEqual(sleeps, [1.0] * 8)
        self.assertTrue(all(sock.closed for sock in socks), "each socket is closed on reconnect (item 17)")
        silent = [_ScriptSock([], end="eof") for _ in range(6)]
        quiet = []
        self._run("no-reset-silent", _connector(silent), quiet, max_connects=6)
        self.assertEqual(quiet, [1.0, 2.0, 4.0, 8.0, 16.0, 30.0])

    # ---- item 3 (B4): resume decodes only from member boundaries ----
    def _fill(self, session, records, size=1200):
        rng = random.Random(3)
        t = 1_000
        for _ in range(records):
            t += 1
            pad = "".join(rng.choice("0123456789abcdef") for _ in range(size))
            session.handle(_js({"event": "info", "pad": pad}), recv_utc_ns=t, recv_mono_ns=t)
            if session.uncommitted_received >= YIELD_EVERY:
                session.commit()
        session.commit()

    def test_item03_resume_of_intact_large_segment_is_not_torn(self):
        root = self.base / "resume-large"
        session = self._open("resume-large", segment_max_bytes=10**12, segment_max_age_s=10**12)
        self._fill(session, 600)
        session.release()
        segment = root / "raw" / "book" / "book-0001.gz"
        size = segment.stat().st_size
        self.assertGreater(size, TAIL_READ_MAX)
        before = segment.read_bytes()
        journal, _stopped = read_journal(root / "index.journal")
        last_commit = [row for row in journal if row.get("kind") == "commit" and row.get("segment_rel") == "raw/book/book-0001.gz"][-1]
        self.assertEqual(last_commit["member_end_offset"], size)
        resumed = self._open("resume-large", resume=True)
        cite_prior_segments(resumed)
        resumed.release()
        self.assertEqual(segment.read_bytes(), before)
        records, _stopped = read_journal(root / "index.journal")
        startup = [row for row in records if row.get("kind") == "startup_gap" and row["segment"].endswith("raw/book/book-0001.gz")]
        self.assertEqual(len(startup), 1)
        self.assertIsNone(startup[0]["torn_offset"])
        self.assertEqual(startup[0]["tail_state"], "ends_at_committed_boundary")
        self.assertEqual(startup[0]["boundary_source"], "journal")
        self.assertEqual(startup[0]["last_raw_id"], last_commit["last_raw_id"])
        self.assertGreater(startup[0]["decodable_records"], 0)

        # No journal: a scan finds a member boundary in the tail. Still intact.
        report = inspect_torn_tail(segment)
        self.assertLessEqual(report["bytes_read_total"], TAIL_READ_MAX)
        self.assertEqual(report["boundary_source"], "scan")
        self.assertIsNone(report["torn_offset"])
        self.assertGreater(report["decodable_records"], 0)

        # A torn tail after the last commit is cited at the committed boundary.
        torn_root = self.base / "resume-torn"
        torn = self._open("resume-torn", segment_max_bytes=10**12, segment_max_age_s=10**12)
        self._fill(torn, 600)
        torn.release()
        torn_segment = torn_root / "raw" / "book" / "book-0001.gz"
        end = torn_segment.stat().st_size
        with open(torn_segment, "ab") as handle:
            handle.write(b"\x1f\x8b\x08\x00partial")
        again = self._open("resume-torn", resume=True)
        cite_prior_segments(again)
        again.release()
        cited = [row for row in read_journal(torn_root / "index.journal")[0] if row.get("kind") == "startup_gap" and row["segment"].endswith("raw/book/book-0001.gz")]
        self.assertEqual(cited[0]["torn_offset"], end)
        self.assertEqual(cited[0]["tail_state"], "torn_after_commit")

    # ---- item 5: disk errors stop cleanly and are never a disconnect ----
    def test_item05_disk_error_in_pump_is_a_clean_stop(self):
        real = framing.SegmentFile.commit_member

        def enospc(self, rows, journal, *, label, fault, meta):
            if label.startswith("norm"):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(self, rows, journal, label=label, fault=fault, meta=meta)

        framing.SegmentFile.commit_member = enospc
        sleeps = []
        try:
            with self.assertRaises(CaptureStopped) as caught:
                self._run("enospc", _connector([_ScriptSock([_js([7, "hb"])] * (YIELD_EVERY + 2))]), sleeps, max_connects=3)
        finally:
            framing.SegmentFile.commit_member = real
        self.assertIn("cause=write_fail detail=io_error", caught.exception.stderr)
        self.assertNotIn(1.0, sleeps, "a disk error must not back off and reconnect")
        records, _stopped = read_journal(self.base / "enospc" / "index.journal")
        stops = [row for row in records if row.get("kind") == "capture_stop"]
        self.assertEqual([row["detail"] for row in stops], ["io_error"])

    # ---- item 6: bad UTF-8, malformed delta, non-101 handshake reconnect ----
    def test_item06_bad_frames_and_handshake_status_reconnect(self):
        bad = encode_server_frame(0x1, b"\xff\xfe\xfd")
        sock = _ScriptSock([], raw_frames=[bad, encode_server_frame(0x1, _js([7, "hb"]).encode())], end="eof")
        sleeps = []
        session = self._run("bad-utf8", _connector([sock]), sleeps, max_connects=1)
        self.assertEqual(session.bad_frame_count, 1)
        self.assertEqual(sleeps, [1.0])
        session_b = self._open("malformed")
        session_b.handle(_js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL}), recv_utc_ns=1, recv_mono_ns=1)
        session_b.handle(_js([7, [[6000, 1, 1], [6100, 1, -1]]]), recv_utc_ns=2, recv_mono_ns=2)
        session_b.handle(_js([7, [6000, 0, 5]]), recv_utc_ns=3, recv_mono_ns=3)
        session_b.handle(_js([7, "not-a-level", 1]), recv_utc_ns=4, recv_mono_ns=4)
        session_b.commit()
        self.assertFalse(session_b.shadow.valid)
        self.assertTrue(session_b.pending_resubscribe)
        session_b.release()
        statuses = [_ScriptSock([], status=b"503 Service Unavailable"), _ScriptSock([], status=b"429 Too Many Requests")]
        retry = []
        self._run("non-101", _connector(statuses), retry, max_connects=2)
        self.assertEqual(retry, [1.0, 2.0])
        with self.assertRaises(StartRefused):
            self._run("bad-accept", _connector([_ScriptSock([], bad_accept=True)]), [], max_connects=1)

    # ---- item 7: one resubscribe in flight, pruned channel ids, deadline ----
    def test_item07_single_resubscribe_pruned_subs_and_deadline(self):
        book = Book()
        book.replace_agg([[6000, 1, 1], [6100, 1, -1]])
        good = book.checksum()[1]
        texts = [
            _js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL}),
            _js([7, [[6000, 1, 1], [6100, 1, -1]]]),
            _js([7, "cs", good + 1]),
            _js([7, "cs", good + 2]),
            _js([7, "cs", good + 3]),
            _js({"event": "subscribed", "channel": "trades", "chanId": 8, "symbol": SYMBOL}),
            _js({"event": "unsubscribed", "chanId": 8, "status": "OK"}),
            _js({"event": "unsubscribed", "chanId": 7, "status": "OK"}),
            _js({"event": "subscribed", "channel": "book", "chanId": 9, "symbol": SYMBOL}),
            _js([9, [[6000, 1, 1], [6100, 1, -1]]]),
            _js([9, "cs", good]),
        ]
        session = self._open("resub")

        class _T:
            def __init__(self, messages):
                self.messages = list(messages)
                self.sent = []

            def send_text(self, text):
                self.sent.append(json.loads(text))

            def recv_text(self):
                return self.messages.pop(0)

        transport = _T(texts)
        pump(session, transport, sleep=lambda _s: None, clock_ns=_Clock(), mono_ns=_Clock(), max_messages=len(texts))
        unsubs = [item for item in transport.sent if item.get("event") == "unsubscribe"]
        self.assertEqual(unsubs, [{"event": "unsubscribe", "chanId": 7}])
        causes = [row["payload"].get("cause") for row in _all_raw(session) if row["channel"] == "gap"]
        self.assertEqual(causes, ["checksum_fail"], "three bad cs on one invalid book: one gap, one resubscribe")
        self.assertEqual(list(session.subs), [9])
        self.assertTrue(session.shadow.valid)
        self.assertIsNone(session.resubscribe_deadline_ns)
        session.release()
        # No new snapshot by the deadline -> reconnect.
        stuck = self._open("resub-stuck")
        clock = _Clock(step_ns=10_000_000_000)
        with self.assertRaises(Reconnect) as caught:
            pump(stuck, _T(texts[:3] + [_js([7, "hb"])] * 10), sleep=lambda _s: None, clock_ns=clock, mono_ns=clock, max_messages=13)
        self.assertEqual(caught.exception.cause, "resubscribe_timeout")
        stuck.release()

    # ---- item 8: 20061 unsubscribes first; maintenance_pause is read ----
    def test_item08_maintenance_resubscribe_and_pause(self):
        session = self._open("maint")
        sent = []

        class _T:
            def __init__(self, messages):
                self.messages = list(messages)

            def send_text(self, text):
                sent.append(json.loads(text))

            def recv_text(self):
                if self.messages:
                    return self.messages.pop(0)
                raise TimeoutError("idle")

        texts = [
            _js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL}),
            _js({"event": "subscribed", "channel": "trades", "chanId": 8, "symbol": SYMBOL}),
            _js({"event": "info", "code": 20061}),
        ]
        pump(session, _T(texts), sleep=lambda _s: None, clock_ns=_Clock(), mono_ns=_Clock(), max_messages=3)
        after = sent[3:]
        self.assertEqual(after[:2], [{"event": "unsubscribe", "chanId": 7}, {"event": "unsubscribe", "chanId": 8}])
        self.assertEqual([item.get("channel") for item in after[2:]], ["book", "trades"])
        session.release()
        paused = self._open("maint-pause")
        clock = _Clock(step_ns=0)
        transport = _T([_js({"event": "info", "code": 20060})])
        start = clock.now
        pump(paused, transport, sleep=lambda _s: None, clock_ns=clock, mono_ns=clock, max_messages=1)
        self.assertTrue(paused.maintenance_pause)
        clock.now = start + 100 * 1_000_000_000
        self.assertFalse(public_run._liveness_lapsed(paused, clock.now), "paused: 100 s of silence is expected")
        clock.now = start + 200 * 1_000_000_000
        self.assertTrue(public_run._liveness_lapsed(paused, clock.now))
        paused.release()

    # ---- item 9: clock records on class change only ----
    def test_item09_clock_gap_only_on_class_change(self):
        session = self._open("clock")
        session.handle(_js({"event": "subscribed", "channel": "trades", "chanId": 8, "symbol": SYMBOL}), recv_utc_ns=1, recv_mono_ns=1)
        for index in range(10):
            mts = 1_700_000_000_000 + index
            session.handle(_js([8, "te", [100 + index, mts, 1.0, 1.0]]), recv_utc_ns=(mts + 300) * 1_000_000, recv_mono_ns=index)
        mts = 1_700_000_001_000
        session.handle(_js([8, "te", [200, mts, 1.0, 1.0]]), recv_utc_ns=(mts + 10) * 1_000_000, recv_mono_ns=20)
        session.commit()
        clock_gaps = [row for row in _all_raw(session) if row["channel"] == "gap" and row["payload"].get("cause") == "clock"]
        self.assertEqual([row["payload"]["clock_class"] for row in clock_gaps], ["warn", "ok"])
        self.assertEqual(clock_gaps[0]["payload"]["offset_includes"], "network_latency_and_clock_skew")
        self.assertEqual(session.clock_nonok_count, 10)
        session.release()

    # ---- item 11: close record at roll; no age roll from opened_ns=0 ----
    def test_item11_rollover_close_record_and_no_zero_open_time(self):
        root = self.base / "roll"
        session = self._open("roll", segment_max_bytes=1, segment_max_age_s=10**9)
        session.handle(_js([7, "hb"]), recv_utc_ns=1, recv_mono_ns=1)
        session.commit()
        session.handle(_js([7, "hb"]), recv_utc_ns=2, recv_mono_ns=2)
        session.commit()
        records, _stopped = session.journal_records()
        closes = [row for row in records if row.get("kind") == "close" and row["segment_id"] == "book-0001"]
        self.assertEqual(len(closes), 1)
        verified = verify_segment(root / "raw" / "book" / "book-0001.gz", records, "book-0001")
        self.assertTrue(verified["ok"], verified["errors"])
        self.assertTrue(verified["close_checked"])
        session.release()
        aged_root = self.base / "aged"
        aged = self._open("aged", segment_max_bytes=10**12, segment_max_age_s=10)
        aged.commit()  # headers only, before any receive
        aged.handle(_js([7, "hb"]), recv_utc_ns=100 * 1_000_000_000, recv_mono_ns=1)
        aged.commit()
        self.assertFalse((aged_root / "raw" / "book" / "book-0002.gz").exists())
        aged.handle(_js([7, "hb"]), recv_utc_ns=111 * 1_000_000_000, recv_mono_ns=2)
        aged.commit()
        self.assertTrue((aged_root / "raw" / "book" / "book-0002.gz").exists())
        aged.release()

    # ---- item 12: --allow-root must be an existing, dedicated, non-root directory ----
    def test_item12_allow_root_is_validated(self):
        self.assertEqual(output_refusal(Path("/pmc-out"), repo_root=None, allow_root=Path("/"), resume=False), "ALLOW_ROOT_IS_FILESYSTEM_ROOT")
        missing = self.base / "missing"
        self.assertEqual(output_refusal(missing / "out", repo_root=None, allow_root=missing, resume=False), "ALLOW_ROOT_MISSING")
        home = Path.home()
        self.assertEqual(output_refusal(home / "pmc-out", repo_root=None, allow_root=home, resume=False), "ALLOW_ROOT_IS_HOME")
        self.assertEqual(output_refusal(Path("rel/out"), repo_root=None, allow_root=Path("rel"), resume=False), "ROOT_NOT_ABSOLUTE")
        self.assertIsNone(output_refusal(self.base / "ok", repo_root=None, allow_root=self.base, resume=False))

    # ---- item 13: a dead DiskWatch thread stops the writer ----
    def test_item13_disk_watch_failure_is_recorded_and_stops(self):
        session = self._open("watch-dies")

        def boom(_path):
            raise RuntimeError("probe exploded")

        session.cfg.disk_free_probe = boom
        watch = DiskWatch(session, 0.01)
        watch.start()
        time.sleep(0.2)
        watch.stop()
        self.assertFalse(watch.thread.is_alive())
        self.assertEqual(session.watch_reason, "disk_watch_failed:RuntimeError")
        session.cfg.disk_free_probe = self._probe
        with self.assertRaises(CaptureStopped) as caught:
            session.commit()
        self.assertIn("disk_watch_failed", caught.exception.stderr)
        session.release()

    # ---- item 15 and item 14 (node parity) ----
    def test_item15_negative_zero_is_zero(self):
        self.assertEqual(js_number_string(-0.0), "0")
        self.assertEqual(js_number_string(0.0), "0")

    @unittest.skipUnless(shutil.which("node"), "node not on PATH")
    def test_item14_js_number_string_matches_node(self):
        rng = random.Random(14)
        values = [-0.0, 1e-7, 1e-6, 1e21, 1e20, 0.1, 0.30000000000000004, 123456789.123, 5e-324]
        for _ in range(3000):
            exponent = rng.randrange(-12, 23)
            values.append(rng.choice((1, -1)) * rng.random() * (10**exponent))
            values.append(round(rng.uniform(0, 100000), rng.randrange(0, 9)))
        script = "let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{process.stdout.write(JSON.stringify(JSON.parse(d).map(v=>String(v))))})"
        out = subprocess.run(
            ["node", "-e", script], input=json.dumps(values), capture_output=True, text=True, timeout=60, check=True
        ).stdout
        expected = json.loads(out)
        got = [js_number_string(float(value)) for value in values]
        mismatches = [(v, e, g) for v, e, g in zip(values, expected, got) if e != g]
        self.assertEqual(mismatches[:5], [])


def _all_raw(session):
    """Raw rows from the committed raw-book segments (public capture keeps no history)."""
    from public_market_collector.framing import durable_view

    journal, _stopped = session.journal_records()
    rows = []
    for path in sorted((session.root / "raw" / "book").glob("*.gz")):
        rows.extend(durable_view(path, journal, path.name[:-3])["records"])
    return rows


if __name__ == "__main__":
    unittest.main()
