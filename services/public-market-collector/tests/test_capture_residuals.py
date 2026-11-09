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


_GREETING = json.dumps({"event": "info", "version": 2, "serverId": "x", "platform": {"status": 1}}, separators=(",", ":"))
_BOOK_SUB = json.dumps({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": "tBTCF0:USTF0", "len": "250"}, separators=(",", ":"))
_SNAP = json.dumps([7, [[6000, 1, 1], [6100, 1, -1]]], separators=(",", ":"))


class _ScriptSock:
    """Server side of one websocket. Ends with EOF (recv returns b"") or a close frame."""

    def __init__(self, texts, *, end="eof", status=b"101 Switching Protocols", bad_accept=False, raw_frames=(), after_close=b""):
        self.texts = list(texts)
        self.end = end
        self.status = status
        self.bad_accept = bad_accept
        self.raw_frames = list(raw_frames)
        self.after_close = after_close
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
            body += encode_server_frame(0x8, b"") + self.after_close
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

        def sleep(seconds):
            # Fake time: a sleep advances the clock, as real sleep does.
            sleeps.append(seconds)
            clock.now += int(seconds * 1_000_000_000)

        return run_forever(
            self.base / name,
            resume=kwargs.pop("resume", False),
            repo_root=None,
            allow_root=self.base,
            connect=connect,
            sleep=sleep,
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
        # Data on a confirmed channel, connection up >= 30 s (clock steps 5 s per read), then
        # EOF or a close frame raising ConnectionError: the backoff resets every time.
        socks = []
        for index in range(8):
            socks.append(
                _ScriptSock(
                    [_GREETING, _BOOK_SUB, _SNAP, _js([7, "hb"]), _js([7, [6000, 1, 2]]), _js([7, "hb"])],
                    end="eof" if index % 2 == 0 else "close",
                )
            )
        sleeps = []
        self._run("reset-real", _connector(socks), sleeps, max_connects=8, clock=_Clock(step_ns=5_000_000_000))
        self.assertEqual(sleeps, [1.0] * 8)
        self.assertTrue(all(sock.closed for sock in socks), "each socket is closed on reconnect (item 17)")
        silent = [_ScriptSock([], end="eof") for _ in range(6)]
        quiet = []
        self._run("no-reset-silent", _connector(silent), quiet, max_connects=6)
        self.assertEqual([round(x) for x in quiet], [3, 3, 4, 8, 16, 30])

    # ---- NB1: the greeting is not a healthy connection; connect pacing ----
    def test_nb1_greeting_then_eof_grows_backoff(self):
        socks = [_ScriptSock([_GREETING], end="eof" if i % 2 == 0 else "close") for i in range(8)]
        sleeps = []
        connects = []
        clock = _Clock()
        inner = _connector(socks)

        def connect():
            connects.append(clock.now)
            return inner()

        self._run("greeting-eof", connect, sleeps, max_connects=8, clock=clock)
        self.assertEqual([round(x) for x in sleeps], [3, 3, 4, 8, 16, 30, 30, 30])
        gaps = [b - a for a, b in zip(connects, connects[1:])]
        self.assertTrue(all(gap >= 3_000_000_000 for gap in gaps), gaps)

    def test_nb1_info_20051_cycle_and_short_data_connection_do_not_reset(self):
        cycle = [_ScriptSock([_GREETING, _js({"event": "info", "code": 20051})]) for _ in range(6)]
        sleeps = []
        self._run("cycle-20051", _connector(cycle), sleeps, max_connects=6)
        self.assertEqual([round(x) for x in sleeps], [3, 3, 4, 8, 16, 30])
        short = [_ScriptSock([_GREETING, _BOOK_SUB, _SNAP, _js([7, [6000, 1, 2]])]) for _ in range(6)]
        brief = []
        self._run("short-data", _connector(short), brief, max_connects=6, clock=_Clock(step_ns=1_000_000_000))
        self.assertEqual([round(x) for x in brief], [1, 2, 4, 8, 16, 30], "data but < 30 s lifetime: no reset")

    def test_nb1_connect_pacer_floor_and_rolling_cap(self):
        self.assertEqual(public_run.CONNECT_MIN_INTERVAL_S, 3)
        self.assertLessEqual(public_run.CONNECTS_PER_WINDOW_MAX, 20)
        pacer = public_run.ConnectPacer()
        second = 1_000_000_000
        self.assertEqual(pacer.wait_s(0), 0)
        for index in range(10):
            now = index * 3 * second
            self.assertLessEqual(pacer.wait_s(now), 0)
            pacer.record(now)
        # 10 connects since t=0: the next waits until t0 + 60 s.
        self.assertEqual(pacer.wait_s(28 * second), 32.0)
        self.assertEqual(pacer.wait_s(60 * second), 0)
        tight = public_run.ConnectPacer(min_interval_s=0, max_in_window=20)
        t = 0
        times = []
        while t < 180 * second:
            if tight.wait_s(t) == 0:
                tight.record(t)
                times.append(t)
            t += second // 10
        for start in times:
            self.assertLessEqual(len([x for x in times if start <= x < start + 60 * second]), 20)
        self.assertEqual(len(times), 60)

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
        self.assertEqual([round(x) for x in sleeps], [3])
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
        self.assertEqual([round(x) for x in retry], [3, 3])
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


class _ListTransport:
    """Scripted transport for pump. An exhausted script raises ConnectionError (real shape)."""

    def __init__(self, messages, *, then="eof"):
        self.messages = list(messages)
        self.sent = []
        self.then = then

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv_text(self):
        if self.messages:
            return self.messages.pop(0)
        if self.then == "timeout":
            raise TimeoutError("idle")
        raise ConnectionError("closed")


def _norm_rows(root):
    from public_market_collector.framing import durable_view

    journal, _stopped = read_journal(root / "index.journal")
    rows = []
    for folder in ("norm/book", "norm/trades"):
        for path in sorted((root / folder).glob("*.gz")):
            rows.extend(durable_view(path, journal, path.name[:-3])["records"])
    return rows


def _raw_rows(root):
    from public_market_collector.framing import durable_view

    journal, _stopped = read_journal(root / "index.journal")
    rows = []
    for folder in ("raw/book", "raw/trades"):
        for path in sorted((root / folder).glob("*.gz")):
            rows.extend(durable_view(path, journal, path.name[:-3])["records"])
    return rows


class SecondRoundTests(unittest.TestCase):
    """HM R2 at a4a72e5b: N1, N2, N3 (HM01-HM14 survivors) and N5."""

    setUp = ResidualTests.setUp
    tearDown = ResidualTests.tearDown
    _probe = ResidualTests._probe
    _open = ResidualTests._open
    _run = ResidualTests._run

    def test_hm01_every_reconnect_invalidates_the_book(self):
        for name, first in (
            ("eof", _ScriptSock([_BOOK_SUB, _SNAP], end="eof")),
            ("close", _ScriptSock([_BOOK_SUB, _SNAP], end="close")),
            ("20051", _ScriptSock([_BOOK_SUB, _SNAP, _js({"event": "info", "code": 20051})], end="eof")),
        ):
            second = _ScriptSock(
                [_js({"event": "subscribed", "channel": "book", "chanId": 9, "symbol": SYMBOL}), _js([9, [6000, 2, 2]])],
                end="eof",
            )
            self._run(f"hm01-{name}", _connector([first, second]), [], max_connects=2)
            root = self.base / f"hm01-{name}"
            gaps = [row["payload"] for row in _raw_rows(root) if row["channel"] == "gap"]
            self.assertTrue(gaps)
            self.assertTrue(all(gap["cause"] == "disconnect" for gap in gaps), (name, gaps))
            if name == "20051":
                self.assertIn("info_20051", {gap.get("reconnect_reason") for gap in gaps})
            deltas = [row for row in _norm_rows(root) if row["channel"] == "book_delta"]
            self.assertEqual(len(deltas), 1)
            self.assertFalse(deltas[0]["applied"], f"{name}: a delta from the new connection hit the stale book")
            self.assertFalse(deltas[0]["book_valid"])

    def test_hm02_subs_cleared_for_each_connection(self):
        session = self._open("hm02")
        first = _ListTransport([_BOOK_SUB, _js({"event": "subscribed", "channel": "trades", "chanId": 8, "symbol": SYMBOL})])
        with self.assertRaises(Reconnect):
            pump(session, first, sleep=lambda _s: None, clock_ns=_Clock(), mono_ns=_Clock())
        second = _ListTransport([_js({"event": "info", "code": 20061})])
        with self.assertRaises(Reconnect):
            pump(session, second, sleep=lambda _s: None, clock_ns=_Clock(), mono_ns=_Clock())
        self.assertEqual([item for item in second.sent if item.get("event") == "unsubscribe"], [])
        session.release()

    def test_hm03_frames_after_a_close_frame_are_not_processed(self):
        after = encode_server_frame(0x1, _js({"event": "info", "marker": "after-close"}).encode())
        # The post-close frame arrives in the same read as the close frame.
        sock = _ScriptSock([_js({"event": "info", "marker": "before-close"})], end="close", after_close=after)

        def connect():
            return open_public_socket(socket_factory=lambda _a, _t: sock, ssl_wrap=lambda raw, _h: raw)

        self._run("hm03", connect, [], max_connects=1)
        verbatim = [row.get("payload_verbatim") or "" for row in _raw_rows(self.base / "hm03")]
        self.assertTrue(any("before-close" in text for text in verbatim))
        self.assertFalse(any("after-close" in text for text in verbatim))

    def test_hm07_n5_protocol_errors_exit_78_with_stop_record_and_flush(self):
        masked = bytes([0x81, 0x80 | 2]) + b"\x01\x02\x03\x04" + b"hi"
        bad_opcode = bytes([0x83, 0x00])
        bad_fragment = encode_server_frame(0x0, b"x")
        for name, frame, reason in (
            ("masked", masked, "WS_SERVER_MASKED"),
            ("opcode", bad_opcode, "WS_OPCODE"),
            ("fragment", bad_fragment, "WS_FRAGMENT"),
        ):
            sock = _ScriptSock([_BOOK_SUB, _SNAP], raw_frames=[frame], end="eof")
            sleeps = []
            with self.assertRaises(StartRefused) as caught:
                self._run(f"hm07-{name}", _connector([sock, _ScriptSock([])]), sleeps, max_connects=2)
            self.assertEqual(str(caught.exception), reason)
            self.assertEqual(sleeps, [], f"{name}: a protocol error must not back off and reconnect")
            root = self.base / f"hm07-{name}"
            records, _stopped = read_journal(root / "index.journal")
            stops = [row for row in records if row.get("kind") == "capture_stop"]
            self.assertEqual([(row["cause"], row["detail"]) for row in stops], [("start_refused", reason)])
            verbatim = [row.get("payload_verbatim") for row in _raw_rows(root)]
            self.assertIn(_SNAP, verbatim, "messages buffered before the refusal are committed")
        code = public_run.execute_capture(
            str(self.base / "never"), resume=False, repo_root=self.base / "no-repo", allow_root=str(self.base),
            runner=lambda *_a: 78,
        )
        self.assertEqual(code, 78)

    def test_hm08_scan_requires_a_decodable_member(self):
        from public_market_collector.framing import gzip_member, jsonl_bytes
        from public_market_collector.session import _MEMBER_HEADER, _scan_boundary

        member = gzip_member(jsonl_bytes([{"raw_id": "s:0:0", "channel": "info"}]))
        fake = _MEMBER_HEADER + b"\x02\xffnot-deflate-data"
        tail = b"junk" + fake + member
        self.assertEqual(_scan_boundary(tail), 4 + len(fake))
        path = self.base / "fake-header.gz"
        path.write_bytes(b"X" * 4096 + tail)
        report = inspect_torn_tail(path, tail_max=len(tail))
        self.assertEqual(report["boundary_source"], "scan")
        self.assertIsNone(report["torn_offset"])
        self.assertEqual(report["decodable_records"], 1)

    def test_hm09_resubscribe_deadline_fires_on_idle_timeouts(self):
        session = self._open("hm09")
        book = Book()
        book.replace_agg([[6000, 1, 1], [6100, 1, -1]])
        script = [_BOOK_SUB, _SNAP, _js([7, "cs", book.checksum()[1] + 1])]
        clock = _Clock(step_ns=4_000_000_000)
        with self.assertRaises(Reconnect) as caught:
            pump(session, _ListTransport(script, then="timeout"), sleep=lambda _s: None, clock_ns=clock, mono_ns=clock)
        self.assertEqual(caught.exception.cause, "resubscribe_timeout")
        session.release()

    def test_hm13_committed_member_mismatch_is_reported(self):
        session = self._open("hm13")
        session.handle(_js({"event": "info", "n": 1}), recv_utc_ns=1, recv_mono_ns=1)
        session.commit()
        session.release()
        segment = self.base / "hm13" / "raw" / "book" / "book-0001.gz"
        records, _stopped = read_journal(self.base / "hm13" / "index.journal")
        commit = [row for row in records if row.get("kind") == "commit" and row["segment_rel"] == "raw/book/book-0001.gz"][-1]
        good = inspect_torn_tail(segment, committed=commit)
        self.assertTrue(good["commit_verified"])
        self.assertGreater(good["decodable_records"], 0)
        forged = dict(commit, member_sha256="0" * 64)
        bad = inspect_torn_tail(segment, committed=forged)
        self.assertFalse(bad["commit_verified"])
        self.assertEqual(bad["decodable_records"], 0)
        self.assertEqual(bad["tail_state"], "committed_member_mismatch")
        self.assertIsNone(bad["last_raw_id"])

    def test_hm14_disconnect_clears_pending_resubscribe(self):
        session = self._open("hm14")
        book = Book()
        book.replace_agg([[6000, 1, 1], [6100, 1, -1]])
        session.handle(_BOOK_SUB, recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_SNAP, recv_utc_ns=2, recv_mono_ns=2)
        session.handle(_js([7, "cs", book.checksum()[1] + 1]), recv_utc_ns=3, recv_mono_ns=3)
        self.assertTrue(session.pending_resubscribe)
        session.disconnect(recv_utc_ns=4, recv_mono_ns=4, cause="disconnect")
        self.assertFalse(session.pending_resubscribe)
        self.assertIsNone(session.resubscribe_deadline_ns)
        session.release()

    def test_n1_maintenance_pause_cleared_on_reconnect(self):
        session = self._open("n1")
        with self.assertRaises(Reconnect):
            pump(session, _ListTransport([_js({"event": "info", "code": 20060})]), sleep=lambda _s: None, clock_ns=_Clock(), mono_ns=_Clock())
        self.assertTrue(session.maintenance_pause)
        with self.assertRaises(Reconnect):
            pump(session, _ListTransport([_GREETING]), sleep=lambda _s: None, clock_ns=_Clock(), mono_ns=_Clock())
        self.assertFalse(session.maintenance_pause)
        session.release()

    def test_n2_deadline_cleared_by_snapshot_after_20061_on_valid_book(self):
        session = self._open("n2")
        book = Book()
        book.replace_agg([[6000, 1, 1], [6100, 1, -1]])
        good = book.checksum()[1]
        clock = _Clock(step_ns=1_000_000_000)
        script = [
            _BOOK_SUB,
            _SNAP,
            _js([7, "cs", good]),
            _js({"event": "info", "code": 20061}),
            _js({"event": "subscribed", "channel": "book", "chanId": 9, "symbol": SYMBOL}),
            _js([9, [[6000, 1, 1], [6100, 1, -1]]]),
        ]
        seen = {}

        class _Watch(_ListTransport):
            def recv_text(self):
                if len(self.messages) == 41:
                    seen["valid"] = session.shadow.valid
                    seen["deadline"] = session.resubscribe_deadline_ns
                return super().recv_text()

        # ~80 s of heartbeats on the new channel, then one checksum mismatch.
        script += [_js([9, "hb"])] * 40 + [_js([9, "cs", good + 1]), _js([9, "hb"])]
        transport = _Watch(script)
        pump(session, transport, sleep=lambda _s: None, clock_ns=clock, mono_ns=clock, max_messages=len(script))
        self.assertTrue(seen["valid"])
        self.assertIsNone(seen["deadline"], "the snapshot after 20061 ends the in-flight resubscribe")
        unsubs = [item for item in transport.sent if item.get("event") == "unsubscribe" and item.get("chanId") == 9]
        self.assertEqual(len(unsubs), 1, "a later mismatch resubscribes instead of a spurious reconnect")
        session.release()


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
