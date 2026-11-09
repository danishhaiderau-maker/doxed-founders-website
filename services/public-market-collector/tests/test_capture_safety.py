"""Safety tests for public forward capture. No live socket and no API key."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import public_market_collector.__main__ as entrypoint
from public_market_collector.config import CaptureStopped, StartRefused
from public_market_collector.framing import durable_view, sha256_bytes
from public_market_collector.guards import scan_package
from public_market_collector.output_policy import output_refusal
from public_market_collector.public_run import (
    FREE_DISK_FLOOR_BYTES,
    YIELD_EVERY,
    YIELD_SLEEP_S,
    cite_prior_segments,
    open_session,
    pump,
    run_forever,
)
from public_market_collector.session import Session
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
        os.environ["BITFINEX_API_KEY"] = "canary-secret-value"
        self.free = 10**12

    def tearDown(self):
        self.tmp.cleanup()

    def _probe(self, _path: Path):
        return self.free

    def _root(self) -> Path:
        return self.base / "public-book-data"

    def test_protected_paths_are_not_created_or_rewritten(self):
        for name in ("local-fresh-data", "ext2", "fly-era", "fly-data"):
            parent = self.base / name
            parent.mkdir()
            sentinel = parent / "keep.txt"
            sentinel.write_bytes(b"keep-me")
            target = parent / "new-output"
            with self.assertRaises(StartRefused) as caught:
                open_session(target, resume=True, repo_root=None, disk_free_probe=self._probe)
            self.assertIn(name, str(caught.exception))
            self.assertEqual(sentinel.read_bytes(), b"keep-me")
            self.assertFalse(target.exists())
            self.assertEqual(sorted(path.name for path in parent.iterdir()), ["keep.txt"])
        self.assertEqual(
            output_refusal(Path("/app/data/public-book-data"), repo_root=None),
            "FORBIDDEN_OUTPUT:fly-app-data",
        )
        repo = self.base / "checkout"
        inside = repo / "public-book-data"
        self.assertEqual(output_refusal(inside, repo_root=repo), "FORBIDDEN_OUTPUT:inside-checkout")
        refused = self.base / "local-fresh-data"
        self.assertIsNotNone(output_refusal(refused, repo_root=None))
        killed("accept-local-fresh-data", lambda: self.assertIsNone(output_refusal(refused, repo_root=None)))

    def test_floor_stops_before_any_directory_is_created(self):
        root = self._root()
        self.free = 100
        with self.assertRaises(StartRefused) as caught:
            open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
        self.assertIn("disk_floor", str(caught.exception))
        self.assertFalse(root.exists())
        self.free = None
        with self.assertRaises(StartRefused) as caught:
            open_session(root, resume=False, repo_root=None, disk_free_probe=self._probe)
        self.assertIn("disk_free_unmeasurable", str(caught.exception))
        self.assertFalse(root.exists())

    def test_floor_stop_leaves_committed_bytes_unchanged(self):
        root = self._root()
        self.free = 10**12
        session = open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
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
        allowed = (0 - 10) >= FREE_DISK_FLOOR_BYTES
        self.assertFalse(allowed)
        killed("floor-comparator-inverted", lambda: self.assertTrue(allowed))
        session.release()

    def test_restart_appends_and_does_not_rewrite_segments(self):
        root = self._root()
        self.free = 10**12
        session = open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
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
        resumed = open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
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
        session = open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
        session.release()
        journal = root / "index.journal"
        journal.write_bytes(journal.read_bytes() + b"torn")
        before = _snapshot(root)
        with self.assertRaises(StartRefused) as caught:
            open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
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
            resume=True,
            repo_root=None,
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
        killed("yield-removed", lambda: self.assertEqual(YIELD_SLEEP_S, 0))
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
            resume=True,
            repo_root=None,
            connect=connect,
            sleep=sleeps.append,
            clock_ns=lambda: 50,
            mono_ns=lambda: 51,
            deprioritize=lambda: "test",
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
        code = entrypoint.main(["capture", "--output", str(forbidden)], runner=runner)
        self.assertEqual(code, 78)
        self.assertEqual(seen, [])
        self.assertEqual(sentinel.read_bytes(), b"keep-me")
        self.assertFalse(forbidden.exists())
        safe = self._root()
        code = entrypoint.main(["capture", "--output", str(safe), "--no-resume"], runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(seen[0][0], safe)
        self.assertFalse(seen[0][1])
        self.assertEqual(seen[0][2], entrypoint._REPO)
        self.assertEqual(scan_package(), [])
        plant = self.base / "plant-other.py"
        plant.write_text("import socket\n", encoding="utf-8")
        findings = scan_package(plant.parent)
        self.assertTrue(any(item.endswith("import socket") or ": import socket" in item for item in findings))

    def test_public_session_is_not_the_synthetic_session(self):
        root = self._root()
        session = open_session(root, resume=True, repo_root=None, disk_free_probe=self._probe)
        self.assertIsInstance(session, Session)
        self.assertNotIn("bot", scan_package())
        session.release()
        with self.assertRaises(StartRefused):
            open_session(root, resume=False, repo_root=None, disk_free_probe=self._probe)


if __name__ == "__main__":
    unittest.main()
