"""Safety tests for public forward capture. No live socket and no API key."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import public_market_collector.__main__ as entrypoint
from public_market_collector.config import CaptureStopped, StartRefused, backoff_delay
from public_market_collector.market import Book, canon_number
from public_market_collector.framing import durable_view, gzip_member, jsonl_bytes, sha256_bytes
from public_market_collector.guards import scan_package
from public_market_collector.output_policy import output_refusal
from public_market_collector.public_run import (
    FREE_DISK_FLOOR_BYTES,
    YIELD_EVERY,
    YIELD_SLEEP_S,
    DiskWatch,
    Reconnect,
    cite_prior_segments,
    open_session,
    pump,
    run_forever,
)
from public_market_collector.session import Session, inspect_torn_tail
from public_market_collector.ws_public import resubscribe_book_messages
from public_market_collector.ws_public import (
    BOOK_LEN,
    FLAG_OB_CHECKSUM,
    SUBSCRIBE_KEYS,
    SYMBOL,
    WS_GUID,
    FrameTooLarge,
    accept_matches,
    assert_public_host,
    authenticated_host,
    deprioritize_process,
    encode_server_frame,
    handshake_request,
    open_public_socket,
    parse_server_frames,
    public_endpoint,
    subscribe_payloads,
)

# Imported for the checksum flag assertion. The name in ws_public is the docs constant.
from public_market_collector.bitfinex_docs import FLAG_OB_CHECKSUM as DOCS_CHECKSUM_FLAG


def _js(value) -> str:
    return json.dumps(value, separators=(",", ":"))


class _Transport:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.events = []

    def send_text(self, text: str) -> None:
        self.events.append("send")
        self.sent.append(text)

    def recv_text(self):
        self.events.append("recv")
        if not self.messages:
            return None
        return self.messages.pop(0)


class _Sock:
    def __init__(self):
        self.sent = b""
        self.incoming = b""
        self._armed = False

    def sendall(self, data: bytes) -> None:
        self.sent += data
        if not self._armed:
            key = ""
            for line in self.sent.decode("ascii").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest())
            head = (
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
            )
            body = encode_server_frame(0x1, b"ab", fin=False) + encode_server_frame(0x0, b"c", fin=True)
            self.incoming = head + body
            self._armed = True

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        chunk = self.incoming[:size]
        self.incoming = self.incoming[size:]
        return chunk

    def settimeout(self, _timeout) -> None:
        return None


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def killed(label: str, check) -> None:
    try:
        check()
    except AssertionError:
        return
    raise AssertionError(f"KILLED_MUTATION {label} was not killed")


class CaptureSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self._saved_api_key = os.environ.get("BITFINEX_API_KEY")
        os.environ["BITFINEX_API_KEY"] = "canary-secret-value"
        self.free = 10**12

    def tearDown(self):
        if self._saved_api_key is None:
            os.environ.pop("BITFINEX_API_KEY", None)
        else:
            os.environ["BITFINEX_API_KEY"] = self._saved_api_key
        self.tmp.cleanup()

    def _probe(self, _path: Path):
        return self.free

    def _root(self) -> Path:
        return self.base / "public-book-data"

    def _open(self, root: Path | None = None, *, resume: bool = False, **kwargs):
        return open_session(
            root or self._root(),
            resume=resume,
            repo_root=None,
            allow_root=self.base,
            disk_free_probe=self._probe,
            **kwargs,
        )

    def test_protected_paths_are_not_created_or_rewritten(self):
        for name in ("local-fresh-data", "ext2", "fly-era", "fly-data"):
            parent = self.base / name
            parent.mkdir()
            sentinel = parent / "keep.txt"
            sentinel.write_bytes(b"keep-me")
            target = parent / "new-output"
            with self.assertRaises(StartRefused) as caught:
                self._open(target, resume=False)
            self.assertIn(name, str(caught.exception))
            self.assertEqual(sentinel.read_bytes(), b"keep-me")
            self.assertFalse(target.exists())
            self.assertEqual(sorted(path.name for path in parent.iterdir()), ["keep.txt"])
        self.assertEqual(
            output_refusal(Path("/app/database/out"), repo_root=None, allow_root=self.base, resume=False),
            "FORBIDDEN_OUTPUT:app-tree",
        )
        for variant in ("ext2-run", "local-fresh-data-copy", "external-data"):
            self.assertIsNotNone(
                output_refusal(self.base / variant / "out", repo_root=None, allow_root=self.base, resume=False)
            )
        repo = self.base / "checkout"
        inside = repo / "public-book-data"
        self.assertEqual(
            output_refusal(inside, repo_root=repo, allow_root=self.base, resume=False),
            "FORBIDDEN_OUTPUT:inside-checkout",
        )
        outside = Path("/tmp") / "not-under-allow"
        self.assertEqual(
            output_refusal(outside, repo_root=None, allow_root=self.base, resume=False),
            "OUTPUT_OUTSIDE_ALLOW_ROOT",
        )
        refused = self.base / "local-fresh-data"
        refusal = output_refusal(refused, repo_root=None, allow_root=self.base, resume=False)
        self.assertIsNotNone(refusal)
        killed("accept-local-fresh-data", lambda: self.assertIsNone(refusal))
        existing = self.base / "already"
        existing.mkdir()
        self.assertEqual(
            output_refusal(existing, repo_root=None, allow_root=self.base, resume=False),
            "OUTPUT_EXISTS",
        )
        self.assertIsNone(output_refusal(existing, repo_root=None, allow_root=self.base, resume=True))

    def test_floor_stops_before_any_directory_is_created(self):
        root = self._root()
        self.free = 100
        with self.assertRaises(StartRefused) as caught:
            self._open(root, resume=False)
        self.assertIn("disk_floor", str(caught.exception))
        self.assertFalse(root.exists())
        self.free = None
        with self.assertRaises(StartRefused) as caught:
            self._open(root, resume=False)
        self.assertIn("disk_free_unmeasurable", str(caught.exception))
        self.assertFalse(root.exists())

    def test_floor_stop_leaves_committed_bytes_unchanged(self):
        root = self._root()
        self.free = 10**12
        session = self._open(root, resume=False)
        transport = _Transport(
            [
                _js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL, "len": "250"}),
                _js([7, [[6000, 1, 1], [6100, 1, -1]]]),
            ]
        )
        pump(
            session,
            transport,
            sleep=lambda _seconds: None,
            clock_ns=lambda: 2_000,
            mono_ns=lambda: 3_000,
            max_messages=2,
        )
        before = _snapshot(root)
        self.assertTrue(before)
        self.free = 0
        session._fits = lambda rows, stream: True
        session.handle(_js([7, "hb"]), recv_utc_ns=4_000, recv_mono_ns=5_000)
        with self.assertRaises(CaptureStopped) as caught:
            session.commit()
        self.assertIn("disk_floor", caught.exception.stderr)
        self.assertEqual(_snapshot(root), before)
        self.assertIn("STOP_RECORD_BELOW_FLOOR", caught.exception.stderr)
        session.release()
        fresh = self.base / "floor-mutation"
        self.free = 10**12
        mutated = self._open(fresh, resume=False)
        mutated.handle(_js([7, "hb"]), recv_utc_ns=1, recv_mono_ns=1)
        mutated.commit()
        prior = _snapshot(fresh)
        self.free = 0
        mutated._floor_block = lambda _estimate: None
        mutated._fits = lambda rows, stream: True
        mutated.handle(_js([7, "hb"]), recv_utc_ns=2, recv_mono_ns=2)
        mutated.commit()
        wrote = _snapshot(fresh) != prior
        mutated.release()
        self.assertTrue(wrote)
        killed("floor-check-ignored", lambda: self.assertFalse(wrote))

    def test_restart_appends_and_does_not_rewrite_segments(self):
        root = self._root()
        self.free = 10**12
        session = self._open(root, resume=False)
        pump(
            session,
            _Transport(
                [
                    _js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL}),
                    _js([7, [[6000, 1, 1], [6100, 1, -1]]]),
                    _js({"event": "subscribed", "channel": "trades", "chanId": 8, "symbol": SYMBOL}),
                    _js([8, "te", [1, 1574694478808, -1, 6000]]),
                ]
            ),
            sleep=lambda _seconds: None,
            clock_ns=lambda: 10,
            mono_ns=lambda: 11,
            max_messages=4,
        )
        session.release()
        segment = root / "raw" / "book" / "book-0001.gz"
        original = segment.read_bytes()
        original_hash = sha256_bytes(original)
        journal = (root / "index.journal").read_bytes()
        segment.write_bytes(original + b"torn")
        torn_hash = sha256_bytes(segment.read_bytes())
        resumed = self._open(root, resume=True)
        cite_prior_segments(resumed)
        self.assertEqual(sha256_bytes(segment.read_bytes()), torn_hash)
        self.assertTrue((root / "index.journal").read_bytes().startswith(journal))
        self.assertFalse((root / "raw" / "book" / "book-0002.gz").exists())
        pump(
            resumed,
            _Transport([_js([7, "hb"])]),
            sleep=lambda _seconds: None,
            clock_ns=lambda: 12,
            mono_ns=lambda: 13,
            max_messages=1,
        )
        resumed.release()
        self.assertEqual(sha256_bytes(segment.read_bytes()), torn_hash)
        self.assertNotEqual(torn_hash, original_hash)
        self.assertTrue((root / "raw" / "book" / "book-0002.gz").is_file())
        view = durable_view(segment, [], "book-0001")
        self.assertGreaterEqual(view["lost_uncommitted_tail_bytes"], 4)
        blob = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
        self.assertNotIn(b"canary-secret-value", blob)
        self.assertNotIn(b"BITFINEX_API", blob)

    def test_torn_journal_refuses_without_writing(self):
        root = self._root()
        self.free = 10**12
        session = self._open(root, resume=False)
        session.release()
        journal = root / "index.journal"
        journal.write_bytes(journal.read_bytes() + b"torn")
        before = _snapshot(root)
        with self.assertRaises(StartRefused) as caught:
            self._open(root, resume=True)
        self.assertIn("RESTART_TORN_JOURNAL", str(caught.exception))
        self.assertEqual(_snapshot(root), before)

    def test_subscribe_is_public_full_depth_and_yields(self):
        self.assertEqual(FREE_DISK_FLOOR_BYTES, 512 * 1024 * 1024)
        self.assertEqual(BOOK_LEN, "250")
        self.assertEqual(SYMBOL, "tBTCF0:USTF0")
        self.assertEqual(DOCS_CHECKSUM_FLAG, FLAG_OB_CHECKSUM)
        payloads = subscribe_payloads()
        self.assertTrue(all(set(item) <= SUBSCRIBE_KEYS for item in payloads))
        self.assertEqual(payloads[1]["len"], "250")
        self.assertEqual(payloads[1]["symbol"], SYMBOL)
        self.assertEqual(payloads[0]["flags"], 131072)
        self.assertNotEqual(payloads[0]["flags"], 65536)
        self.assertNotEqual(payloads[0]["flags"], 32768)
        root = self._root()
        order = []

        def deprioritize():
            order.append("deprioritize")
            return "posix_nice_10"

        transport = _Transport([_js([7, "hb"]) for _ in range(YIELD_EVERY)])

        def connect():
            order.append("connect")
            return transport

        def sleep(seconds):
            order.append(("sleep", seconds))

        run_forever(
            root,
            resume=False,
            repo_root=None,
            allow_root=self.base,
            connect=connect,
            sleep=sleep,
            clock_ns=lambda: 100,
            mono_ns=lambda: 101,
            deprioritize=deprioritize,
            disk_free_probe=self._probe,
            max_messages=YIELD_EVERY,
            max_connects=1,
        )
        self.assertEqual(order[0], "deprioritize")
        self.assertEqual(order[1], "connect")
        self.assertIn(("sleep", YIELD_SLEEP_S), order)
        self.assertNotEqual(YIELD_SLEEP_S, 0)
        sleep_values = [item[1] for item in order if isinstance(item, tuple) and item[0] == "sleep"]
        self.assertEqual(sleep_values, [YIELD_SLEEP_S])
        killed("yield-removed", lambda: self.assertEqual(sleep_values, [0]))
        sent = [json.loads(item) for item in transport.sent]
        self.assertEqual(sent[1]["len"], "250")
        self.assertEqual(sent[1]["channel"], "book")
        self.assertEqual(sent[2]["channel"], "trades")
        self.assertTrue(transport.events.index("send") < transport.events.index("recv"))
        self.assertEqual(deprioritize_process(platform_name="posix", nice=lambda value: order.append(("nice", value))), "posix_nice_10")
        self.assertIn(("nice", 10), order)
        wins = []
        self.assertEqual(
            deprioritize_process(platform_name="nt", win_call=wins.append),
            "windows_below_normal",
        )
        self.assertEqual(wins, [0x00004000])

    def test_reconnect_sleeps_instead_of_spinning(self):
        sleeps = []

        def connect():
            raise ConnectionError("down")

        run_forever(
            self._root(),
            resume=False,
            repo_root=None,
            allow_root=self.base,
            connect=connect,
            sleep=sleeps.append,
            clock_ns=lambda: 50,
            mono_ns=lambda: 51,
            deprioritize=lambda: "posix_nice_10",
            disk_free_probe=self._probe,
            max_connects=1,
        )
        self.assertTrue(sleeps)
        self.assertGreaterEqual(sleeps[0], 1)

    def test_handshake_has_no_credentials_and_auth_host_never_connects(self):
        host, path = public_endpoint()
        blob = handshake_request(host, path, "dGhlIHNhbXBsZSBub25jZQ==")
        lowered = blob.lower()
        self.assertNotIn(b"authorization", lowered)
        self.assertNotIn(b"api-key", lowered)
        self.assertNotIn(b"secret", lowered)
        self.assertIn(host.encode("ascii"), blob)
        calls = []

        def factory(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("socket opened")

        with self.assertRaises(StartRefused):
            assert_public_host(authenticated_host())
        self.assertEqual(calls, [])
        self.assertNotEqual(host, authenticated_host())
        sock = _Sock()

        def public_factory(address, timeout):
            del timeout
            self.assertEqual(address, (host, 443))
            calls.append(address)
            return sock

        connection = open_public_socket(socket_factory=public_factory, ssl_wrap=lambda raw, wrapped_host: raw)
        self.assertEqual(connection.recv_text(), "abc")
        self.assertNotIn(b"authorization", sock.sent.lower())
        sample = b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"
        self.assertTrue(accept_matches("dGhlIHNhbXBsZSBub25jZQ==", sample))
        huge = bytes([0x81, 127]) + (9_000_000).to_bytes(8, "big")
        with self.assertRaises(FrameTooLarge):
            parse_server_frames(huge, 8_000_000)

    def test_entrypoint_still_refuses_without_capture_and_refuses_protected_output(self):
        self.assertEqual(entrypoint.main([]), 78)
        self.assertEqual(entrypoint.main(["--start"]), 78)
        seen = []

        def runner(*args):
            seen.append(args)
            return 0

        forbidden = self.base / "local-fresh-data" / "out"
        (self.base / "local-fresh-data").mkdir()
        sentinel = self.base / "local-fresh-data" / "keep.txt"
        sentinel.write_bytes(b"keep-me")
        code = entrypoint.main(
            ["capture", "--output", str(forbidden), "--allow-root", str(self.base)],
            runner=runner,
        )
        self.assertEqual(code, 78)
        self.assertEqual(seen, [])
        self.assertEqual(sentinel.read_bytes(), b"keep-me")
        self.assertFalse(forbidden.exists())
        safe = self._root()
        code = entrypoint.main(
            ["capture", "--output", str(safe), "--allow-root", str(self.base)],
            runner=runner,
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen[0][0], safe)
        self.assertFalse(seen[0][1])
        self.assertEqual(seen[0][2], entrypoint._REPO)
        self.assertEqual(seen[0][3], self.base)
        self.assertEqual(scan_package(), [])
        plant = self.base / "plant-other.py"
        plant.write_text("import socket\n", encoding="utf-8")
        findings = scan_package(plant.parent)
        self.assertTrue(any(item.endswith("import socket") or ": import socket" in item for item in findings))

    def test_public_session_is_not_the_synthetic_session(self):
        root = self._root()
        session = self._open(root, resume=False)
        self.assertIsInstance(session, Session)
        self.assertNotIn("bot", scan_package())
        session.release()
        with self.assertRaises(StartRefused):
            self._open(root, resume=False)

    def test_fractional_checksum_matches_and_mismatch_resubscribes(self):
        book = Book()
        book.replace_agg([[6000, 1, 0.5], [6100, 1, -0.25]])
        text, value = book.checksum()
        self.assertEqual(text, "6000:0.5:6100:-0.25")
        self.assertEqual(value, 1379275826)
        self.assertEqual(canon_number(1.0), "1")
        self.assertEqual(canon_number(1e-7), "1e-7")
        self.assertEqual(canon_number(1e21), "1e+21")
        import public_market_collector.market as market

        original = market.canon_number

        def broken(number):
            if isinstance(number, float) and not float(number).is_integer():
                raise ValueError("non_integer_checksum_stringification_unknown")
            return original(number)

        matched = True
        market.canon_number = broken
        try:
            book.checksum()
        except ValueError:
            matched = False
        finally:
            market.canon_number = original
        self.assertFalse(matched)
        killed("broken-canon-accepted-fraction", lambda: self.assertTrue(matched))
        self.assertEqual(book.checksum()[1], 1379275826)
        root = self._root()
        session = self._open(root, resume=False)
        transport = _Transport(
            [
                _js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL, "len": "250"}),
                _js([7, [[6000, 1, 0.5], [6100, 1, -0.25]]]),
                _js([7, "cs", value]),
            ]
        )
        pump(session, transport, sleep=lambda _s: None, clock_ns=lambda: 1_000_000_000, mono_ns=lambda: 1, max_messages=3)
        self.assertTrue(session.shadow.valid)
        self.assertFalse(session.pending_resubscribe)
        session.handle(_js([7, "cs", 1]), recv_utc_ns=2_000_000_000, recv_mono_ns=2)
        self.assertTrue(session.pending_resubscribe)
        self.assertFalse(session.shadow.valid)
        messages = resubscribe_book_messages(session.subs)
        self.assertEqual(messages[0]["event"], "unsubscribe")
        self.assertEqual(messages[0]["chanId"], 7)
        self.assertEqual(messages[1]["len"], "250")
        self.assertNotIn("api", json.dumps(messages).lower())
        session.release()

    def test_network_errors_reconnect_and_backoff_is_clamped(self):
        self.assertEqual(backoff_delay(5000, initial_s=1, cap_s=30, jitter_s=0), 30.0)
        sleeps = []

        def connect():
            raise socket.timeout("timed out")

        run_forever(
            self.base / "timeouts",
            resume=False,
            repo_root=None,
            allow_root=self.base,
            connect=connect,
            sleep=sleeps.append,
            clock_ns=lambda: 10,
            mono_ns=lambda: 11,
            deprioritize=lambda: "posix_nice_10",
            disk_free_probe=self._probe,
            max_connects=1,
        )
        self.assertEqual(sleeps, [1.0])
        for exc in (ssl.SSLError("handshake"), socket.gaierror(1, "dns")):
            sleeps.clear()

            def fail(error=exc):
                raise error

            run_forever(
                self.base / f"net-{type(exc).__name__}",
                resume=False,
                repo_root=None,
                allow_root=self.base,
                connect=fail,
                sleep=sleeps.append,
                clock_ns=lambda: 10,
                mono_ns=lambda: 11,
                deprioritize=lambda: "posix_nice_10",
                disk_free_probe=self._probe,
                max_connects=1,
            )
            self.assertEqual(sleeps, [1.0])

    def test_healthy_connection_resets_backoff(self):
        sleeps = []
        phase = {"n": 0}

        def connect():
            phase["n"] += 1
            if phase["n"] == 1:
                return _Transport([_js([7, "hb"])])
            raise socket.timeout("timed out")

        run_forever(
            self.base / "reset-backoff",
            resume=False,
            repo_root=None,
            allow_root=self.base,
            connect=connect,
            sleep=sleeps.append,
            clock_ns=lambda: 20,
            mono_ns=lambda: 21,
            deprioritize=lambda: "posix_nice_10",
            disk_free_probe=self._probe,
            max_connects=2,
        )
        self.assertEqual(sleeps[0], 1.0)

    def test_liveness_info_codes_and_disk_watch(self):
        session = self._open(self.base / "liveness", resume=False)
        session.last_liveness = 0

        class _Idle:
            def send_text(self, _text: str) -> None:
                return None

            def recv_text(self):
                raise TimeoutError("idle")

        with self.assertRaises(Reconnect) as caught:
            pump(
                session,
                _Idle(),
                sleep=lambda _s: None,
                clock_ns=lambda: 31_000_000_000,
                mono_ns=lambda: 1,
                max_messages=1,
            )
        self.assertEqual(caught.exception.cause, "liveness")
        session.release()
        info = self._open(self.base / "info-codes", resume=False)
        sent = _Transport(
            [
                _js({"event": "info", "code": 20060}),
                _js({"event": "info", "code": 20061}),
            ]
        )
        pump(info, sent, sleep=lambda _s: None, clock_ns=lambda: 100, mono_ns=lambda: 101, max_messages=2)
        self.assertFalse(info.maintenance_pause)
        self.assertGreaterEqual(len(sent.sent), 6)
        info.release()
        pause = self._open(self.base / "info-20051", resume=False)
        with self.assertRaises(Reconnect) as caught:
            pump(
                pause,
                _Transport([_js({"event": "info", "code": 20051})]),
                sleep=lambda _s: None,
                clock_ns=lambda: 100,
                mono_ns=lambda: 101,
                max_messages=2,
            )
        self.assertEqual(caught.exception.cause, "info_20051")
        pause.release()
        watched = self._open(self.base / "watch", resume=False)
        watch = DiskWatch(watched, 0.02)
        watch.start()
        self.free = 0
        time.sleep(0.25)
        watch.stop()
        self.assertEqual(watched.watch_reason, "disk_floor")
        watched.release()

    def test_segment_rollover_and_tail_only_inspect(self):
        root = self.base / "roll-size"
        session = self._open(root, resume=False, segment_max_bytes=1, segment_max_age_s=10**9)
        session.handle(_js({"event": "subscribed", "channel": "book", "chanId": 7, "symbol": SYMBOL}), recv_utc_ns=1, recv_mono_ns=1)
        session.handle(_js([7, [[6000, 1, 1], [6100, 1, -1]]]), recv_utc_ns=2, recv_mono_ns=2)
        session.commit()
        self.assertTrue((root / "raw" / "book" / "book-0001.gz").is_file())
        session.handle(_js([7, "hb"]), recv_utc_ns=3, recv_mono_ns=3)
        session.commit()
        self.assertTrue((root / "raw" / "book" / "book-0002.gz").is_file())
        first = (root / "raw" / "book" / "book-0001.gz").read_bytes()
        session.release()
        self.assertEqual((root / "raw" / "book" / "book-0001.gz").read_bytes(), first)
        aged = self.base / "roll-age"
        age = self._open(aged, resume=False, segment_max_bytes=10**12, segment_max_age_s=0)
        age.handle(_js([7, "hb"]), recv_utc_ns=1, recv_mono_ns=1)
        age.commit()
        age.handle(_js([7, "hb"]), recv_utc_ns=5, recv_mono_ns=5)
        age.commit()
        self.assertTrue((aged / "raw" / "book" / "book-0002.gz").is_file())
        age.release()
        payload = jsonl_bytes([{"raw_id": "mem:0:0", "channel": "info"}])
        blob = gzip_member(payload) + (b"A" * 80) + b"TORN"
        path = self.base / "wide.gz"
        path.write_bytes(blob)
        report = inspect_torn_tail(path, tail_max=32)
        self.assertLessEqual(report["bytes_read"], 32)
        self.assertEqual(report["sha256_scope"], "tail")
        self.assertEqual(path.read_bytes(), blob)

    def test_priority_denial_stops_before_any_directory_is_created(self):
        root = self.base / "denied"
        with self.assertRaises(StartRefused) as caught:
            run_forever(
                root,
                resume=False,
                repo_root=None,
                allow_root=self.base,
                deprioritize=lambda: "nice_denied",
                disk_free_probe=self._probe,
                connect=lambda: (_ for _ in ()).throw(AssertionError("connected")),
            )
        self.assertIn("PRIORITY_DENIED", str(caught.exception))
        self.assertNotIn("below_normal", str(caught.exception))
        self.assertFalse(root.exists())

    def test_long_run_commit_cost_stays_flat(self):
        root = self.base / "long-run"
        session = self._open(root, resume=False, segment_max_bytes=10**12, segment_max_age_s=10**12)
        walks = {"n": 0}
        real_walk = os.walk

        def counting(*args, **kwargs):
            walks["n"] += 1
            return real_walk(*args, **kwargs)

        os.walk = counting
        tracemalloc.start()
        times = []
        try:
            for index in range(50_000):
                session.handle(_js([7, "hb"]), recv_utc_ns=index + 1, recv_mono_ns=index + 1)
                if session.uncommitted_received >= 20:
                    started = time.perf_counter()
                    session.commit()
                    times.append(time.perf_counter() - started)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            os.walk = real_walk
            session.release()
        self.assertEqual(walks["n"], 0)
        self.assertEqual(session.durable_raw, [])
        self.assertGreaterEqual(len(times), 100)
        early = sum(times[:20]) / 20
        late = sum(times[-20:]) / 20
        self.assertLess(late, max(early * 5, early + 0.05))
        self.assertLess(peak, 20 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
