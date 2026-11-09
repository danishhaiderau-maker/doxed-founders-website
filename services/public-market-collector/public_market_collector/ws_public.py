"""Minimal public Bitfinex websocket. Stdlib only. No API key and no auth host.

SEQ_ALL (65536) and TIMESTAMP (32768) stay off. Official docs do not say where
those fields sit in the array, so turning them on could shift the book. The
checksum flag is a separate `cs` message and does not rewrite book rows.
Book `len` 250 is the documented public maximum, not the whole exchange book.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import socket
import ssl

from .bitfinex_docs import DOCUMENTED_AUTHENTICATED_WS, DOCUMENTED_PUBLIC_WS, FLAG_OB_CHECKSUM
from .config import CaptureStopped, StartRefused

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_HANDSHAKE_BYTES = 8192
SYMBOL = "tBTCF0:USTF0"
BOOK_PREC = "P0"
BOOK_FREQ = "F0"
BOOK_LEN = "250"
SUBSCRIBE_KEYS = frozenset({"event", "flags", "channel", "symbol", "prec", "freq", "len"})


class FrameTooLarge(Exception):
    pass


class HandshakeRejected(ConnectionError):
    """The server answered the upgrade with a status other than 101 (for example 503
    or 429 during maintenance). Retryable: the caller backs off and reconnects.
    A 101 with a wrong Sec-WebSocket-Accept is still StartRefused."""


class BadFrame:
    """A complete text message that is not valid UTF-8. Recorded and skipped."""

    def __init__(self, kind: str, size: int):
        self.kind = kind
        self.size = size


def public_endpoint() -> tuple[str, str]:
    prefix = "wss://"
    if not DOCUMENTED_PUBLIC_WS.startswith(prefix):
        raise StartRefused("PUBLIC_URL")
    host, _, path = DOCUMENTED_PUBLIC_WS[len(prefix) :].partition("/")
    if not host or not path:
        raise StartRefused("PUBLIC_URL")
    return host, "/" + path


def authenticated_host() -> str:
    prefix = "wss://"
    rest = DOCUMENTED_AUTHENTICATED_WS[len(prefix) :]
    return rest.split("/", 1)[0]


def assert_public_host(host: str) -> None:
    public_host, _path = public_endpoint()
    if host == authenticated_host() or host != public_host:
        raise StartRefused("PUBLIC_HOST_ONLY")


def subscribe_payloads() -> list[dict]:
    """Keyless public book (documented max length) and raw trades."""
    payloads = [
        {"event": "conf", "flags": FLAG_OB_CHECKSUM},
        {
            "event": "subscribe",
            "channel": "book",
            "symbol": SYMBOL,
            "prec": BOOK_PREC,
            "freq": BOOK_FREQ,
            "len": BOOK_LEN,
        },
        {"event": "subscribe", "channel": "trades", "symbol": SYMBOL},
    ]
    for payload in payloads:
        if set(payload) - SUBSCRIBE_KEYS:
            raise StartRefused("SUBSCRIBE_KEY_NOT_ALLOWED")
        if payload.get("flags") not in (None, FLAG_OB_CHECKSUM):
            raise StartRefused("SUBSCRIBE_FLAG_NOT_ALLOWED")
    return payloads


def handshake_request(host: str, path: str, key: str) -> bytes:
    assert_public_host(host)
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        "",
        "",
    ]
    blob = "\r\n".join(lines).encode("ascii")
    lowered = blob.lower()
    if b"authorization" in lowered or b"api-key" in lowered or b"secret" in lowered:
        raise StartRefused("HANDSHAKE_CREDENTIAL")
    return blob


def accept_matches(key: str, response: bytes) -> bool:
    expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
    text = response.decode("iso-8859-1", errors="replace")
    first = text.split("\r\n", 1)[0]
    if " 101 " not in f" {first} ":
        return False
    for line in text.split("\r\n"):
        if line.lower().startswith("sec-websocket-accept:"):
            return line.split(":", 1)[1].strip() == expected
    return False


def encode_client_frame(opcode: int, payload: bytes, mask: bytes) -> bytes:
    if len(mask) != 4:
        raise ValueError("mask")
    length = len(payload)
    header = bytes([0x80 | (opcode & 0x0F)])
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 65536:
        header += bytes([0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header += bytes([0x80 | 127]) + length.to_bytes(8, "big")
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return header + mask + masked


def encode_server_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Unmasked server frame, used by tests."""
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        return bytes([first, length]) + payload
    if length < 65536:
        return bytes([first, 126]) + length.to_bytes(2, "big") + payload
    return bytes([first, 127]) + length.to_bytes(8, "big") + payload


def parse_server_frames(buf: bytes, max_bytes: int) -> tuple[list[tuple[int, int, bytes]], bytes]:
    """Return complete frames as (fin, opcode, payload) and the unused tail.

    A declared length above `max_bytes` raises before the body is required.
    """
    frames: list[tuple[int, int, bytes]] = []
    offset = 0
    while offset + 2 <= len(buf):
        b0 = buf[offset]
        b1 = buf[offset + 1]
        if b1 & 0x80:
            if frames:
                # Return the good frames first; the next call raises on this one.
                break
            raise StartRefused("WS_SERVER_MASKED")
        length = b1 & 0x7F
        header = 2
        if length == 126:
            if offset + 4 > len(buf):
                break
            length = int.from_bytes(buf[offset + 2 : offset + 4], "big")
            header = 4
        elif length == 127:
            if offset + 10 > len(buf):
                break
            length = int.from_bytes(buf[offset + 2 : offset + 10], "big")
            header = 10
        if length > max_bytes or length < 0:
            raise FrameTooLarge(length)
        end = offset + header + length
        if end > len(buf):
            break
        fin = 1 if b0 & 0x80 else 0
        frames.append((fin, b0 & 0x0F, buf[offset + header : end]))
        offset = end
    return frames, buf[offset:]


def _recv_some(sock, size: int) -> bytes:
    chunk = sock.recv(size)
    if not chunk:
        raise ConnectionError("closed")
    return chunk


class PublicConnection:
    """One TLS websocket to the public host. `sock` is injected in tests."""

    def __init__(self, sock, *, host: str | None = None, path: str | None = None, max_bytes: int = MAX_FRAME_BYTES):
        default_host, default_path = public_endpoint()
        self.host = host or default_host
        self.path = path or default_path
        assert_public_host(self.host)
        self.sock = sock
        self.max_bytes = max_bytes
        self._buf = b""
        self._pending: list[str] = []
        self._fragments = b""
        self._fragment_opcode: int | None = None
        self._close_seen = False
        self._deferred: StartRefused | None = None
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        self.sock.sendall(handshake_request(self.host, self.path, key))
        while b"\r\n\r\n" not in self._buf:
            if len(self._buf) > MAX_HANDSHAKE_BYTES:
                raise StartRefused("WS_HANDSHAKE")
            self._buf += _recv_some(self.sock, 4096)
        marker = self._buf.index(b"\r\n\r\n") + 4
        head, self._buf = self._buf[:marker], self._buf[marker:]
        first = head.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
        if " 101 " not in f" {first} ":
            raise HandshakeRejected(f"WS_HANDSHAKE_STATUS:{first[:64]}")
        if not accept_matches(key, head):
            raise StartRefused("WS_HANDSHAKE")

    def send_text(self, text: str) -> None:
        mask = secrets.token_bytes(4)
        self.sock.sendall(encode_client_frame(0x1, text.encode("utf-8"), mask))

    def close(self) -> None:
        """Close the socket. Errors on close are ignored; nothing is written to disk."""
        sock, self.sock = self.sock, None
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass

    def recv_text(self) -> "str | BadFrame":
        while True:
            if self._pending:
                return self._pending.pop(0)
            if self._deferred is not None:
                # Messages decoded before the protocol error were returned first.
                raise self._deferred
            if self._close_seen:
                # Messages that arrived before the close frame were returned first.
                raise ConnectionError("close")
            frames, self._buf = parse_server_frames(self._buf, self.max_bytes)
            if not frames:
                self._buf += _recv_some(self.sock, 65536)
                if len(self._buf) > self.max_bytes + 16:
                    raise FrameTooLarge(len(self._buf))
                continue
            for fin, opcode, payload in frames:
                try:
                    text = self._consume(fin, opcode, payload)
                except StartRefused as exc:
                    if not self._pending:
                        raise
                    self._deferred = exc
                    break
                if text is not None:
                    self._pending.append(text)
                if self._close_seen:
                    break

    def _consume(self, fin: int, opcode: int, payload: bytes) -> str | None:
        if opcode == 0x9:
            mask = secrets.token_bytes(4)
            self.sock.sendall(encode_client_frame(0xA, payload, mask))
            return None
        if opcode == 0x8:
            self._close_seen = True
            return None
        if opcode == 0xA:
            return None
        if opcode in (0x1, 0x0):
            if opcode == 0x1:
                self._fragments = payload
                self._fragment_opcode = 0x1
            else:
                if self._fragment_opcode != 0x1:
                    raise StartRefused("WS_FRAGMENT")
                self._fragments += payload
            if len(self._fragments) > self.max_bytes:
                raise FrameTooLarge(len(self._fragments))
            if not fin:
                return None
            blob = self._fragments
            self._fragments = b""
            self._fragment_opcode = None
            try:
                return blob.decode("utf-8")
            except UnicodeDecodeError:
                return BadFrame("bad_utf8", len(blob))
        raise StartRefused("WS_OPCODE")


def resubscribe_book_messages(subs: dict) -> list[dict]:
    """Unsubscribe the book channel and subscribe it again. No credentials."""
    messages = []
    for chan_id, sub in subs.items():
        if isinstance(sub, dict) and sub.get("channel") == "book":
            messages.append({"event": "unsubscribe", "chanId": chan_id})
    messages.append(subscribe_payloads()[1])
    return messages


def resubscribe_all_messages(subs: dict) -> list[dict]:
    """After 20061: unsubscribe every current channel, then subscribe book and trades again."""
    messages = [{"event": "unsubscribe", "chanId": chan_id} for chan_id in subs]
    messages.extend(subscribe_payloads()[1:])
    return messages


def open_public_socket(socket_factory=None, ssl_wrap=None, timeout_s: float = 5):
    """Connect only to the documented public host. Factory is for tests."""
    host, _path = public_endpoint()
    assert_public_host(host)
    factory = socket_factory or socket.create_connection
    raw = factory((host, 443), timeout_s)
    if ssl_wrap is None:
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(raw, server_hostname=host)
    else:
        wrapped = ssl_wrap(raw, host)
    wrapped.settimeout(timeout_s)
    return PublicConnection(wrapped, host=host)


def deprioritize_process(*, platform_name: str | None = None, nice=None, win_call=None) -> str:
    """Drop below the paper engine. A denied nice does not raise."""
    name = platform_name if platform_name is not None else os.name
    if name == "nt":
        priority = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
        if win_call is not None:
            win_call(priority)
            return "windows_below_normal"
        import ctypes

        kernel = ctypes.windll.kernel32
        ok = kernel.SetPriorityClass(kernel.GetCurrentProcess(), priority)
        return "windows_below_normal" if ok else "priority_denied"
    fn = nice or os.nice
    try:
        fn(10)
    except OSError:
        return "nice_denied"
    return "posix_nice_10"


def stop_for_frame_cap() -> CaptureStopped:
    stderr = "CAPTURE_STOP cause=write_fail detail=ws_frame_cap\n"
    return CaptureStopped("ws_frame_cap", stderr)
