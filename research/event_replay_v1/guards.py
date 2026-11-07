"""Resource ceilings, output ledger, and practical containment.

The tree bound is two processes, each with a 256MiB virtual-address ceiling.
Resident size is at most virtual size after the ceiling is installed, so the
sum is a conservative upper bound on the original 512MiB RSS figure. It is
not a rename of that metric. There is no CPU-seconds cap. Wall deadlines are
120 seconds per command and 600 seconds per suite. Receipt files are 8MiB.
Aggregate command output is a separate 16MiB ledger.
"""

from __future__ import annotations

import os
import resource
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

VAS_BYTES = 268435456
MAX_WORKERS = 2
MAX_RSS_BOUND_BYTES = 512 * 1024 * 1024
MAX_COMMAND_SECONDS = 120
MAX_SUITE_SECONDS = 600
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_AGGREGATE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 2048
SECRET_KEY_MARKS = ("secret", "token", "password", "credential", "canary", "payload", "api_key")


class GuardError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CapStop(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"CAP_STOP:{code}")
        self.code = code


@dataclass
class ResourceLimits:
    max_workers: int = MAX_WORKERS
    max_rss_bytes: int = MAX_RSS_BOUND_BYTES
    max_command_seconds: int = MAX_COMMAND_SECONDS
    max_suite_seconds: int = MAX_SUITE_SECONDS
    max_receipt_bytes: int = MAX_RECEIPT_BYTES
    max_aggregate_bytes: int = MAX_AGGREGATE_BYTES
    max_events: int = MAX_EVENTS
    vas_bytes: int = VAS_BYTES


DEFAULT_LIMITS = ResourceLimits()


@dataclass
class ResourceObservation:
    workers: int = 1
    rss_bytes: int = 0
    command_seconds: float = 0
    suite_seconds: float = 0
    receipt_bytes: int = 0
    aggregate_bytes: int = 0
    events: int = 0
    vas_bytes: int = 0


def enforce_observation(obs: ResourceObservation, limits: ResourceLimits = DEFAULT_LIMITS) -> bool:
    if obs.workers > limits.max_workers:
        raise CapStop("workers")
    if obs.rss_bytes > limits.max_rss_bytes:
        raise CapStop("rss")
    if obs.command_seconds > limits.max_command_seconds:
        raise CapStop("command")
    if obs.suite_seconds > limits.max_suite_seconds:
        raise CapStop("suite")
    if obs.receipt_bytes > limits.max_receipt_bytes:
        raise CapStop("receipt")
    if obs.aggregate_bytes > limits.max_aggregate_bytes:
        raise CapStop("aggregate")
    if obs.events > limits.max_events:
        raise CapStop("events")
    if obs.vas_bytes > limits.vas_bytes:
        raise CapStop("vas")
    return True


def install_vas_ceiling(limit: int = VAS_BYTES) -> dict:
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    return {
        "metric": "RLIMIT_AS",
        "mechanism": "resource.setrlimit",
        "observed_soft": soft,
        "observed_hard": hard,
        "cap": limit,
    }


def observed_vas_ceiling() -> tuple[int, int]:
    return resource.getrlimit(resource.RLIMIT_AS)


def require_vas_ceiling(limit: int = VAS_BYTES) -> dict:
    soft, hard = observed_vas_ceiling()
    if soft < 0 or hard < 0 or soft > limit or hard > limit:
        raise CapStop("vas_ceiling_missing")
    return {
        "metric": "RLIMIT_AS",
        "mechanism": "inherited_or_installed_setrlimit",
        "observed_soft": soft,
        "observed_hard": hard,
        "cap": limit,
        "stopped": False,
    }


def sample_rss_bytes() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError as exc:
        raise CapStop("rss_enforcement_missing") from exc
    raise CapStop("rss_enforcement_missing")


class OutputLedger:
    """One authoritative byte ledger. Reservations never raise the caps."""

    def __init__(
        self,
        aggregate_cap: int = MAX_AGGREGATE_BYTES,
        receipt_cap: int = MAX_RECEIPT_BYTES,
    ) -> None:
        self.aggregate_cap = aggregate_cap
        self.receipt_cap = receipt_cap
        self.aggregate_used = 0
        self.receipt_used = 0
        self.transfers: list[dict] = []

    def remaining_aggregate(self) -> int:
        return self.aggregate_cap - self.aggregate_used

    def reserve_pipe(self, nbytes: int, kind: str) -> None:
        if nbytes < 0 or self.aggregate_used + nbytes > self.aggregate_cap:
            raise CapStop("aggregate")
        self.aggregate_used += nbytes
        self.transfers.append({"kind": kind, "bytes": nbytes, "aggregate_used": self.aggregate_used})

    def reserve_receipt(self, nbytes: int) -> None:
        if nbytes < 0 or self.receipt_used + nbytes > self.receipt_cap:
            raise CapStop("receipt")
        if self.aggregate_used + nbytes > self.aggregate_cap:
            raise CapStop("aggregate")
        self.receipt_used += nbytes
        self.aggregate_used += nbytes
        self.transfers.append(
            {
                "kind": "receipt",
                "bytes": nbytes,
                "receipt_used": self.receipt_used,
                "aggregate_used": self.aggregate_used,
            }
        )

    def refund(self, nbytes: int) -> None:
        if nbytes < 0 or nbytes > self.aggregate_used:
            raise CapStop("aggregate")
        self.aggregate_used -= nbytes
        self.transfers.append({"kind": "refund", "bytes": nbytes, "aggregate_used": self.aggregate_used})


class ProcessGate:
    """At most one live child. The parent plus that child is the tree of two."""

    def __init__(self) -> None:
        self.live = 0

    def acquire(self) -> dict:
        if self.live >= 1:
            raise CapStop("workers")
        self.live += 1
        return {
            "metric": "process_count",
            "mechanism": "process_gate",
            "observed": self.live + 1,
            "cap": MAX_WORKERS,
            "stopped": False,
        }

    def release(self) -> None:
        if self.live > 0:
            self.live -= 1


class _EnvironTrap(dict):
    def _refuse(self, *_args, **_kwargs):
        raise GuardError("PROHIBITED_ENV")

    def __getitem__(self, key):
        self._refuse()

    def __iter__(self):
        self._refuse()

    def __contains__(self, key):
        self._refuse()

    def get(self, key, default=None):
        self._refuse()

    keys = _refuse
    items = _refuse
    values = _refuse


def install_env_trap() -> None:
    os.environ = _EnvironTrap()

    def getenv(*_args, **_kwargs):
        raise GuardError("PROHIBITED_ENV")

    os.getenv = getenv


class OuterInterception:
    """Always-on practical containment. Inner mutants do not remove it."""

    def __init__(self) -> None:
        self.installed = False
        self.refusals: list[str] = []
        self._saved: dict = {}

    def install(self) -> None:
        if self.installed:
            return
        self._saved = {
            "socket": socket.socket,
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
        }

        def refuse(code):
            def _inner(*_args, **_kwargs):
                self.refusals.append(code)
                raise GuardError(code)

            return _inner

        socket.socket = refuse("PROHIBITED_NETWORK")
        socket.create_connection = refuse("PROHIBITED_NETWORK")
        socket.getaddrinfo = refuse("PROHIBITED_NETWORK")
        self.installed = True


class InnerGuard:
    """Production checks. A test may deactivate this object only."""

    def __init__(self) -> None:
        self.active = True

    def check(self, code: str) -> None:
        if self.active:
            raise GuardError(code)


INNER_GUARD = InnerGuard()


def structural_redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(mark in lowered for mark in SECRET_KEY_MARKS):
                continue
            cleaned[key] = structural_redact(item)
        return cleaned
    if isinstance(value, list):
        return [structural_redact(item) for item in value]
    if isinstance(value, str) and value.startswith("-----BEGIN "):
        return "[REDACTED]"
    return value


def monotonic_seconds(started: float) -> float:
    return time.monotonic() - started


ACTIVE_OUTER: OuterInterception | None = None
ACTIVE_FILE_GATE = None
SCRATCH_LIMIT = 1024


class ByteSource:
    """Receipt string whose source is original bytes, not code points."""

    def __init__(self, data: bytes) -> None:
        self.data = bytes(data)


def refund_receipt(ledger: OutputLedger, nbytes: int) -> None:
    """Return unused receipt reservation. Pipe refunds do not use this."""

    if nbytes < 0 or nbytes > ledger.receipt_used or nbytes > ledger.aggregate_used:
        raise CapStop("receipt")
    ledger.receipt_used -= nbytes
    ledger.aggregate_used -= nbytes
    ledger.transfers.append(
        {
            "kind": "receipt_refund",
            "bytes": nbytes,
            "receipt_used": ledger.receipt_used,
            "aggregate_used": ledger.aggregate_used,
        }
    )


def _room(ledger: OutputLedger) -> int:
    return min(
        ledger.receipt_cap - ledger.receipt_used,
        ledger.aggregate_cap - ledger.aggregate_used,
    )


class _StreamRead:
    def __init__(self, stream) -> None:
        self.stream = stream
        self.consumed = 0

    def take(self, nbytes: int) -> bytes:
        chunk = self.stream.read(nbytes)
        if chunk is None:
            chunk = b""
        self.consumed += len(chunk)
        return chunk


def read_chunked(stream, ledger: OutputLedger, chunk: int = 4096) -> bytes:
    """Read a pipe in pre-reserved chunks. Never calls an unbounded read."""

    reader = _StreamRead(stream)
    parts: list[bytes] = []
    while True:
        remaining = ledger.remaining_aggregate()
        step = chunk if chunk < remaining else remaining
        if step <= 0:
            ledger.reserve_pipe(1, "chunk")
        ledger.reserve_pipe(step, "chunk")
        data = reader.take(step)
        if len(data) < step:
            ledger.refund(step - len(data))
        if data:
            parts.append(data)
        if len(data) < step:
            break
    return b"".join(parts)


def read_framed(stream, ledger: OutputLedger) -> list[bytes]:
    """Length-prefixed frames. The body is reserved only after the 4-byte size is known."""

    reader = _StreamRead(stream)
    frames: list[bytes] = []
    while True:
        ledger.reserve_pipe(4, "frame_header")
        header = reader.take(4)
        if len(header) < 4:
            ledger.refund(4 - len(header))
            if header and len(header) != 0:
                raise CapStop("aggregate")
            break
        size = int.from_bytes(header, "big")
        if size > ledger.remaining_aggregate():
            raise CapStop("aggregate")
        ledger.reserve_pipe(size, "frame_body")
        body = reader.take(size)
        if len(body) < size:
            ledger.refund(size - len(body))
            raise CapStop("aggregate")
        frames.append(body)
    return frames


class ChunkPump:
    """Non-blocking chunk reads so a live child can leave a full pipe."""

    def __init__(self, fd: int, ledger: OutputLedger) -> None:
        os.set_blocking(fd, False)
        self.fd = fd
        self.ledger = ledger
        self.parts: list[bytes] = []
        self.eof = False
        self.progress = False

    def pull(self) -> None:
        self.progress = False
        if self.eof:
            return
        remaining = self.ledger.remaining_aggregate()
        step = 4096 if 4096 < remaining else remaining
        if step <= 0:
            self.ledger.reserve_pipe(1, "chunk")
        self.ledger.reserve_pipe(step, "chunk")
        try:
            data = os.read(self.fd, step)
        except BlockingIOError:
            self.ledger.refund(step)
            return
        if not data:
            self.ledger.refund(step)
            self.eof = True
            return
        if len(data) < step:
            self.ledger.refund(step - len(data))
        self.parts.append(data)
        self.progress = True

    def value(self) -> bytes:
        return b"".join(self.parts)


class FramePump:
    """Length-prefixed frames. The body is not read until its size fits the ledger."""

    def __init__(self, fd: int, ledger: OutputLedger) -> None:
        os.set_blocking(fd, False)
        self.fd = fd
        self.ledger = ledger
        self.pending = bytearray()
        self.frames: list[bytes] = []
        self.size: int | None = None
        self.eof = False
        self.progress = False

    def pull(self) -> None:
        self.progress = False
        if self.eof:
            return
        if self.size is None:
            want = 4 - len(self.pending)
        else:
            if self.size > self.ledger.remaining_aggregate():
                raise CapStop("aggregate")
            want = self.size - len(self.pending)
            if want > 4096:
                want = 4096
        if want <= 0:
            self._consume()
            return
        if want > self.ledger.remaining_aggregate():
            self.ledger.reserve_pipe(want, "frame")
        self.ledger.reserve_pipe(want, "frame")
        try:
            data = os.read(self.fd, want)
        except BlockingIOError:
            self.ledger.refund(want)
            return
        if not data:
            self.ledger.refund(want)
            self.eof = True
            if self.pending or self.size is not None:
                raise CapStop("aggregate")
            return
        if len(data) < want:
            self.ledger.refund(want - len(data))
        self.pending.extend(data)
        self.progress = True
        self._consume()

    def _consume(self) -> None:
        while True:
            if self.size is None:
                if len(self.pending) < 4:
                    return
                self.size = int.from_bytes(self.pending[:4], "big")
                del self.pending[:4]
                if self.size > self.ledger.remaining_aggregate():
                    raise CapStop("aggregate")
                continue
            if len(self.pending) < self.size:
                return
            self.frames.append(bytes(self.pending[: self.size]))
            del self.pending[: self.size]
            self.size = None


def _escape_len(code_point: int) -> int:
    if code_point in (0x22, 0x5C, 0x08, 0x09, 0x0A, 0x0C, 0x0D):
        return 2
    if code_point < 0x20 or code_point > 0x7F:
        if code_point > 0xFFFF:
            return 12
        return 6
    return 1


def _escape_bytes(code_point: int) -> bytes:
    if code_point == 0x22:
        return b'\\"'
    if code_point == 0x5C:
        return b"\\\\"
    short = {0x08: b"\\b", 0x09: b"\\t", 0x0A: b"\\n", 0x0C: b"\\f", 0x0D: b"\\r"}
    if code_point in short:
        return short[code_point]
    if code_point < 0x20 or 0x7F < code_point <= 0xFFFF:
        return ("\\u%04x" % code_point).encode("ascii")
    if code_point <= 0x7F:
        return bytes((code_point,))
    value = code_point - 0x10000
    high = 0xD800 + (value >> 10)
    low = 0xDC00 + (value & 0x3FF)
    return ("\\u%04x\\u%04x" % (high, low)).encode("ascii")


def _utf8_one(buf: bytes, index: int):
    """Return (code_point, consumed) or None when the sequence is incomplete."""

    if index >= len(buf):
        return None
    lead = buf[index]
    if lead < 0x80:
        return lead, 1
    if lead < 0xC2 or lead > 0xF4:
        return 0xFFFD, 1
    if lead <= 0xDF:
        need = 2
        minimum = 0x80
    elif lead <= 0xEF:
        need = 3
        minimum = 0x800
    else:
        need = 4
        minimum = 0x10000
    if index + need > len(buf):
        return None
    encoded = 0
    for offset in range(need):
        byte = buf[index + offset]
        if offset and (byte & 0xC0) != 0x80:
            return 0xFFFD, 1
        encoded = (encoded << 6) | (byte & 0x3F if offset else byte & (0xFF >> (need + 1)))
    if encoded < minimum or 0xD800 <= encoded <= 0xDFFF or encoded > 0x10FFFF:
        return 0xFFFD, 1
    if lead == 0xE0 and buf[index + 1] < 0xA0:
        return 0xFFFD, 1
    if lead == 0xED and buf[index + 1] >= 0xA0:
        return 0xFFFD, 1
    if lead == 0xF0 and buf[index + 1] < 0x90:
        return 0xFFFD, 1
    if lead == 0xF4 and buf[index + 1] > 0x8F:
        return 0xFFFD, 1
    return encoded, need


def _int_digits(value: int) -> int:
    number = value if value >= 0 else -value
    if number == 0:
        return 1
    count = 0
    while number:
        number //= 10
        count += 1
    return count


def _digit_window(number: int, total: int, start: int, length: int) -> bytes:
    remaining = total - start
    window = (number // pow(10, remaining - length)) % pow(10, length)
    return f"{window:0{length}d}".encode("ascii")


def _binary_pair(value: float) -> tuple[int, int]:
    """abs(value) = n * 2**e with n < 2**53. No decimal digits are stored."""

    number = -value if value < 0 else value
    if number == 0.0:
        return 0, 0
    low = 4503599627370496.0  # 2**52
    high = 9007199254740992.0  # 2**53
    power = 0
    while number < low:
        number *= 2.0
        power -= 1
    while number >= high:
        number /= 2.0
        power += 1
    return int(number), power


def _split_binary(significand: int, power: int) -> tuple[int, int, int]:
    if significand == 0:
        return 0, 0, 0
    if power >= 0:
        return significand << power, 0, 0
    width = -power
    if width >= significand.bit_length():
        return 0, significand, width
    integer_value = significand >> width
    return integer_value, significand - (integer_value << width), width


def _reduce_fraction(numer: int, width: int) -> tuple[int, int]:
    if numer == 0 or width == 0:
        return 0, 0
    shift = 0
    while shift < width and numer % 2 == 0:
        numer //= 2
        shift += 1
    return numer, width - shift


def _fraction_digits(numer: int, places: int, start: int, take: int) -> bytes:
    """Digits [start, start+take) of the zero-padded places-digit field."""

    drop = places - (start + take)
    scaled = numer * pow(5, places)
    if drop:
        scaled //= pow(10, drop)
    block = scaled % pow(10, take)
    return f"{block:0{take}d}".encode("ascii")


class ReceiptWriter:
    """One receipt charge per emitted piece. Scratch is allocated only after reserve."""

    def __init__(self, ledger: OutputLedger, handle) -> None:
        self.ledger = ledger
        self.handle = handle
        self.scratch_allocations: list[int] = []

    def room(self) -> int:
        return _room(self.ledger)

    def write_reserved(self, reserved: int, data: bytes) -> None:
        if reserved < len(data):
            raise CapStop("receipt")
        self.handle.write(data)

    def write_exact(self, data: bytes) -> None:
        if len(data) > SCRATCH_LIMIT:
            offset = 0
            while offset < len(data):
                self.write_exact(data[offset:offset + SCRATCH_LIMIT])
                offset += SCRATCH_LIMIT
            return
        self.ledger.reserve_receipt(len(data))
        self.write_reserved(len(data), data)

    def _commit_new(self, data: bytes) -> None:
        self.scratch_allocations.append(len(data))
        self.write_reserved(len(data), data)

    def emit(self, value, sort_keys: bool = False) -> None:
        if isinstance(value, ByteSource):
            self.emit_byte_string(value.data)
            return
        if isinstance(value, str):
            self.emit_unicode_string(value)
            return
        if value is None:
            self.write_exact(b"null")
            return
        if value is True:
            self.write_exact(b"true")
            return
        if value is False:
            self.write_exact(b"false")
            return
        if isinstance(value, int):
            self.emit_int(value)
            return
        if isinstance(value, float):
            self.emit_float(value)
            return
        if isinstance(value, list):
            self.write_exact(b"[")
            for index, item in enumerate(value):
                if index:
                    self.write_exact(b",")
                self.emit(item, sort_keys)
            self.write_exact(b"]")
            return
        if isinstance(value, dict):
            self.write_exact(b"{")
            items = list(value.items())
            if sort_keys:
                items.sort(key=lambda pair: pair[0])
            for index, (key, item) in enumerate(items):
                if index:
                    self.write_exact(b",")
                self.emit_unicode_string(str(key))
                self.write_exact(b":")
                self.emit(item, sort_keys)
            self.write_exact(b"}")
            return
        raise CapStop("receipt")

    def emit_int(self, value: int) -> None:
        negative = value < 0
        number = -value if negative else value
        total = _int_digits(number)
        start = 0
        signed = negative
        while start < total:
            sign_len = 1 if signed else 0
            minimum = sign_len + 1 if start == 0 else 1
            space = self.room() if self.room() < SCRATCH_LIMIT else SCRATCH_LIMIT
            if space < minimum:
                self.ledger.reserve_receipt(minimum)
            take = space - sign_len
            if take > total - start:
                take = total - start
            if take < 1:
                self.ledger.reserve_receipt(minimum)
            piece_len = sign_len + take
            self.ledger.reserve_receipt(piece_len)
            buf = bytearray()
            if sign_len:
                buf.append(ord("-"))
            buf.extend(_digit_window(number, total, start, take))
            self._commit_new(bytes(buf))
            start += take
            signed = False

    def emit_float(self, value: float) -> None:
        if value != value or value == float("inf") or value == float("-inf"):
            raise CapStop("receipt")
        self.float_entry_receipt = self.ledger.receipt_used
        self.float_entry_scratch = len(self.scratch_allocations)
        negative = value < 0
        significand, power = _binary_pair(value)
        integer_value, frac_numer, width = _split_binary(significand, power)
        numer, places = _reduce_fraction(frac_numer, width)
        int_digits = 1 if integer_value == 0 else _int_digits(integer_value)
        frac_len = places if numer else 0
        pos_int = 0
        pos_frac = 0
        signed = negative
        while pos_int < int_digits or pos_frac < frac_len:
            room = self.room() if self.room() < SCRATCH_LIMIT else SCRATCH_LIMIT
            if pos_int < int_digits:
                sign_len = 1 if signed else 0
                minimum = sign_len + 1 if pos_int == 0 else 1
                if room < minimum:
                    self.ledger.reserve_receipt(minimum)
                take = int_digits - pos_int
                if take > room - sign_len:
                    take = room - sign_len
                piece_len = sign_len + take
                self.ledger.reserve_receipt(piece_len)
                buf = bytearray()
                if sign_len:
                    buf.append(ord("-"))
                if integer_value == 0:
                    buf.append(ord("0"))
                else:
                    buf.extend(_digit_window(integer_value, int_digits, pos_int, take))
                if len(buf) != piece_len:
                    raise CapStop("receipt")
                self._commit_new(bytes(buf))
                pos_int += take
                signed = False
                continue
            dot = 1 if pos_frac == 0 else 0
            minimum = dot + 1 if pos_frac == 0 else 1
            if room < minimum:
                self.ledger.reserve_receipt(minimum)
            take = frac_len - pos_frac
            if take > room - dot:
                take = room - dot
            piece_len = dot + take
            self.ledger.reserve_receipt(piece_len)
            buf = bytearray()
            if dot:
                buf.append(ord("."))
            buf.extend(_fraction_digits(numer, places, pos_frac, take))
            if len(buf) != piece_len:
                raise CapStop("receipt")
            self._commit_new(bytes(buf))
            pos_frac += take
        self.float_exit_receipt = self.ledger.receipt_used
        self.float_scratch = self.scratch_allocations[self.float_entry_scratch:]

    def emit_unicode_string(self, text: str) -> None:
        self.write_exact(b'"')
        index = 0
        while index < len(text):
            limit = self.room() if self.room() < SCRATCH_LIMIT else SCRATCH_LIMIT
            exact = 0
            count = 0
            while index + count < len(text) and count < SCRATCH_LIMIT:
                needed = _escape_len(ord(text[index + count]))
                if exact + needed > limit:
                    break
                exact += needed
                count += 1
            if count == 0:
                needed = _escape_len(ord(text[index]))
                before = len(self.scratch_allocations)
                self.ledger.reserve_receipt(needed)
                if len(self.scratch_allocations) != before:
                    raise CapStop("receipt")
                buf = _escape_bytes(ord(text[index]))
                self._commit_new(buf)
                index += 1
                continue
            self.ledger.reserve_receipt(exact)
            buf = bytearray()
            for offset in range(count):
                buf.extend(_escape_bytes(ord(text[index + offset])))
            if len(buf) != exact:
                raise CapStop("receipt")
            self._commit_new(bytes(buf))
            index += count
        self.write_exact(b'"')

    def emit_byte_string(self, data: bytes) -> None:
        self.write_exact(b'"')
        self.write_byte_content(data)
        self.write_exact(b'"')

    def write_byte_content(self, data: bytes) -> None:
        pending = data
        while pending:
            window, kind, reserved, consumed = _byte_window(pending, self.room())
            if kind == "incomplete":
                needed = 6
                self.ledger.reserve_receipt(needed)
                self._commit_new(b"\\ufffd")
                break
            if kind == "overflow":
                before = len(self.scratch_allocations)
                self.ledger.reserve_receipt(reserved)
                if len(self.scratch_allocations) != before:
                    raise CapStop("receipt")
                self._commit_new(_encode_bytes(window))
                pending = pending[consumed:]
                continue
            self.ledger.reserve_receipt(reserved)
            encoded = _encode_bytes(window)
            if len(encoded) > reserved:
                raise CapStop("receipt")
            if kind == "fast" and reserved != len(encoded):
                refund_receipt(self.ledger, reserved - len(encoded))
            self.write_reserved(len(encoded), encoded)
            self.scratch_allocations.append(len(encoded))
            pending = pending[consumed:]


def _encode_bytes(window: bytes) -> bytes:
    buf = bytearray()
    index = 0
    while index < len(window):
        found = _utf8_one(window, index)
        if found is None:
            buf.extend(b"\\ufffd")
            break
        code_point, consumed = found
        buf.extend(_escape_bytes(code_point))
        index += consumed
    return bytes(buf)


def _byte_window(pending: bytes, room: int):
    limit = room if room < SCRATCH_LIMIT else SCRATCH_LIMIT
    fast_end = 0
    fast_bytes = 0
    index = 0
    while index < len(pending):
        found = _utf8_one(pending, index)
        if found is None:
            break
        _code, consumed = found
        if 6 * (fast_bytes + consumed) > limit:
            break
        fast_bytes += consumed
        index += consumed
        fast_end = index
    if fast_bytes and 6 * fast_bytes <= limit:
        return pending[:fast_end], "fast", 6 * fast_bytes, fast_end
    exact = 0
    end = 0
    index = 0
    while index < len(pending):
        found = _utf8_one(pending, index)
        if found is None:
            break
        code_point, consumed = found
        needed = _escape_len(code_point)
        if exact + needed > limit:
            break
        exact += needed
        index += consumed
        end = index
    if end:
        return pending[:end], "exact", exact, end
    found = _utf8_one(pending, 0)
    if found is None:
        return pending, "incomplete", 6, len(pending)
    _code, consumed = found
    return pending[:consumed], "overflow", _escape_len(_code), consumed


def emit_receipt(ledger: OutputLedger, handle, value, sort_keys: bool = False) -> ReceiptWriter:
    writer = ReceiptWriter(ledger, handle)
    observe = getattr(ledger, "observe_writer", None)
    if observe is not None:
        observe(writer)
    writer.emit(value, sort_keys=sort_keys)
    return writer


def _path_text(path) -> str:
    if isinstance(path, int):
        return ""
    return os.fspath(path)


class FileGate:
    """Outer file wrapper. Inner deactivation does not remove it."""

    def __init__(self, refusals: list, write_paths: tuple[str, ...]) -> None:
        self.refusals = refusals
        package = Path(__file__).resolve().parent
        roots = [
            package,
            package.parent,
            Path(os.__file__).resolve().parent,
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path(sys.executable).resolve().parent,
        ]
        self.read_roots = []
        for root in roots:
            resolved = root.resolve()
            if resolved not in self.read_roots:
                self.read_roots.append(resolved)
        self.write_paths = [str(Path(item).resolve()) for item in write_paths]
        self.saved: dict = {}
        self.active = False
        self._busy = False

    def install(self) -> None:
        if self.active:
            return
        builtin_mod = sys.modules["builtins"]
        io_mod = sys.modules["io"]
        io_impl = sys.modules["_io"]
        self.saved = {
            "open": builtin_mod.open,
            "io_open": io_mod.open,
            "os_open": os.open,
            "scandir": os.scandir,
            "FileIO": io_impl.FileIO,
            "open_code": io_impl.open_code,
        }
        builtin_mod.open = self._open
        io_mod.open = self._open
        os.open = self._os_open
        os.scandir = self._scandir
        io_impl.FileIO = self._fileio
        io_impl.open_code = self._open_code
        self.active = True

    def _record(self, code: str):
        self.refusals.append(code)
        raise GuardError(code)

    def _resolved(self, path) -> str:
        return str(Path(_path_text(path)).resolve())

    def _read_ok(self, path) -> bool:
        raw = os.path.abspath(_path_text(path))
        if raw in ("/proc/self/status", f"/proc/{os.getpid()}/status"):
            return True
        text = self._resolved(path)
        if text in ("/proc/self/status", f"/proc/{os.getpid()}/status"):
            return True
        for root in self.read_roots:
            root_text = str(root)
            if text == root_text or text.startswith(root_text + os.sep):
                return True
        return False

    def _write_ok(self, path) -> bool:
        return self._resolved(path) in self.write_paths

    def _writing_mode(self, mode) -> bool:
        return any(mark in str(mode) for mark in ("w", "x", "a", "+"))

    def _open(self, file, mode="r", *args, **kwargs):
        if self._busy or isinstance(file, int):
            return self.saved["open"](file, mode, *args, **kwargs)
        self._busy = True
        try:
            if self._writing_mode(mode):
                if not self._write_ok(file):
                    self._record("PROHIBITED_WRITE")
            elif not self._read_ok(file):
                self._record("PROHIBITED_REAL_ROOT")
            return self.saved["open"](file, mode, *args, **kwargs)
        finally:
            self._busy = False

    def _os_open(self, path, flags, *args, **kwargs):
        if self._busy or isinstance(path, int):
            return self.saved["os_open"](path, flags, *args, **kwargs)
        self._busy = True
        try:
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT))
            if writing:
                if not self._write_ok(path):
                    self._record("PROHIBITED_WRITE")
            elif not self._read_ok(path):
                self._record("PROHIBITED_REAL_ROOT")
            return self.saved["os_open"](path, flags, *args, **kwargs)
        finally:
            self._busy = False

    def _fileio(self, file, mode="r", *args, **kwargs):
        if self._busy or isinstance(file, int):
            return self.saved["FileIO"](file, mode, *args, **kwargs)
        self._busy = True
        try:
            if self._writing_mode(mode):
                if not self._write_ok(file):
                    self._record("PROHIBITED_WRITE")
            elif not self._read_ok(file):
                self._record("PROHIBITED_REAL_ROOT")
            return self.saved["FileIO"](file, mode, *args, **kwargs)
        finally:
            self._busy = False

    def _open_code(self, path):
        if self._busy:
            return self.saved["open_code"](path)
        self._busy = True
        try:
            if not self._read_ok(path):
                self._record("PROHIBITED_REAL_ROOT")
            return self.saved["open_code"](path)
        finally:
            self._busy = False

    def _scandir(self, path="."):
        if self._busy:
            return self.saved["scandir"](path)
        self._busy = True
        try:
            if not self._read_ok(path):
                self._record("PROHIBITED_REAL_ROOT")
            return self.saved["scandir"](path)
        finally:
            self._busy = False


def install_entry_containment(write_paths: tuple[str, ...] = ()) -> OuterInterception:
    global ACTIVE_OUTER, ACTIVE_FILE_GATE
    outer = OuterInterception()
    outer.install()
    gate = FileGate(outer.refusals, tuple(write_paths))
    gate.install()
    ACTIVE_OUTER = outer
    ACTIVE_FILE_GATE = gate
    return outer
