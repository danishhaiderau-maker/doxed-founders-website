"""Venue parsing, book, checksum, and deterministic normalization.

Sequence layout `index_1_unverified` is a synthetic test input. Official docs
do not specify where SEQ_ALL places the integer.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

from . import bitfinex_docs as docs
from .framing import NORM_KEYS, dumps

CHANNELS = (
    "book_snapshot",
    "book_delta",
    "trade",
    "heartbeat",
    "checksum",
    "info",
    "gap",
    "epoch",
)


def signed_crc32(text: str) -> int:
    raw = zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF
    if raw >= 0x80000000:
        raw -= 0x100000000
    return raw


# Bitfinex checksum strings use JavaScript Number.toString (ES ToString), not Python str().
# Trade ids are not a contiguous sequence, so a missing id is not proof a trade was lost.
TRADE_ID_WINDOW = 100_000


def js_number_string(value: float) -> str:
    """JavaScript Number.toString for a finite float. Pinned against node in tests."""
    if value != value or math.isinf(value):
        raise ValueError("checksum number")
    if value == 0.0:
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    sign = "-" if value < 0 else ""
    mag = abs(float(value))
    text = None
    for prec in range(1, 18):
        candidate = format(mag, f".{prec}g")
        if float(candidate) == mag:
            text = candidate.lower()
            break
    if text is None:
        text = format(mag, ".17g").lower()
    if "e" not in text:
        return sign + text
    mant, exp_s = text.split("e")
    exp_i = int(exp_s)
    if 1e-6 <= mag < 1e21:
        if "." in mant:
            whole, frac = mant.split(".")
        else:
            whole, frac = mant, ""
        digits = whole + frac
        point = 1 + exp_i
        if point <= 0:
            fixed = "0." + ("0" * (-point)) + digits
        elif point >= len(digits):
            fixed = digits + ("0" * (point - len(digits)))
        else:
            fixed = digits[:point] + "." + digits[point:]
        return sign + fixed
    if "." in mant:
        mant = mant.rstrip("0").rstrip(".")
    exp_out = ("+" + str(exp_i)) if exp_i >= 0 else str(exp_i)
    return sign + f"{mant}e{exp_out}"


def canon_number(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("checksum number")
    if isinstance(value, float):
        return js_number_string(value)
    return str(value)


@dataclass
class Book:
    prec: str = "P0"
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    orders: dict = field(default_factory=dict)

    def replace_agg(self, entries: list) -> None:
        self.bids.clear()
        self.asks.clear()
        for entry in entries:
            self.apply_agg(entry)

    def apply_agg(self, entry: list) -> None:
        price, count, amount = entry[0], int(entry[1]), entry[2]
        if count == 0:
            if amount == 1:
                self.bids.pop(price, None)
            elif amount == -1:
                self.asks.pop(price, None)
            else:
                raise ValueError("delete_amount_unspecified")
            return
        if amount > 0:
            self.asks.pop(price, None)
            self.bids[price] = (count, amount)
        elif amount < 0:
            self.bids.pop(price, None)
            self.asks[price] = (count, amount)
        else:
            raise ValueError("zero_amount")

    def replace_raw(self, entries: list) -> None:
        self.orders.clear()
        for entry in entries:
            self.apply_raw(entry)

    def apply_raw(self, entry: list) -> None:
        order_id, price, amount = entry[0], entry[1], entry[2]
        if price == 0:
            self.orders.pop(order_id, None)
            return
        self.orders[order_id] = (price, amount)

    def checksum_string(self) -> str:
        if self.prec == "R0":
            bid_rows = sorted(
                ((price, order_id, amount) for order_id, (price, amount) in self.orders.items() if amount > 0),
                key=lambda row: (-row[0], row[1]),
            )
            ask_rows = sorted(
                ((price, order_id, amount) for order_id, (price, amount) in self.orders.items() if amount < 0),
                key=lambda row: (row[0], row[1]),
            )
            parts: list[str] = []
            for index in range(25):
                if index < len(bid_rows):
                    _price, order_id, amount = bid_rows[index]
                    parts.append(canon_number(order_id))
                    parts.append(canon_number(amount))
                if index < len(ask_rows):
                    _price, order_id, amount = ask_rows[index]
                    parts.append(canon_number(order_id))
                    parts.append(canon_number(amount))
            return ":".join(parts)
        bid_rows = sorted(self.bids.items(), key=lambda item: -item[0])[:25]
        ask_rows = sorted(self.asks.items(), key=lambda item: item[0])[:25]
        parts = []
        for index in range(25):
            if index < len(bid_rows):
                price, (_count, amount) = bid_rows[index]
                parts.append(canon_number(price))
                parts.append(canon_number(amount))
            if index < len(ask_rows):
                price, (_count, amount) = ask_rows[index]
                parts.append(canon_number(price))
                parts.append(canon_number(amount))
        return ":".join(parts)

    def checksum(self) -> tuple[str, int]:
        text = self.checksum_string()
        return text, signed_crc32(text)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def classify_message(message, sub: dict | None, seq_layout: str | None) -> dict:
    """Turn one parsed venue message into a channel skeleton. Does not invent time."""
    if isinstance(message, dict):
        return {
            "channel": "info",
            "venue_seq": None,
            "ts_exchange_ms": None,
            "payload": message,
            "seq_missing": False,
        }
    if not isinstance(message, list) or not message:
        return {
            "channel": "info",
            "venue_seq": None,
            "ts_exchange_ms": None,
            "payload": message,
            "seq_missing": False,
        }
    venue_seq = None
    body = message
    seq_missing = False
    if seq_layout == "index_1_unverified":
        if len(message) >= 2 and _is_int(message[1]):
            venue_seq = int(message[1])
            body = [message[0], *message[2:]]
        else:
            seq_missing = True
            body = message
    elif seq_layout is not None:
        raise ValueError("seq_layout")
    channel, ts_exchange = _channel_of(body, sub)
    if seq_layout == "index_1_unverified" and seq_missing and channel in {
        "book_snapshot",
        "book_delta",
        "trade",
        "heartbeat",
        "checksum",
    }:
        seq_missing = True
    else:
        seq_missing = False
    return {
        "channel": channel,
        "venue_seq": venue_seq,
        "ts_exchange_ms": ts_exchange,
        "payload": message,
        "seq_missing": seq_missing,
    }


def _channel_of(body: list, sub: dict | None) -> tuple[str, int | None]:
    if len(body) < 2:
        return "info", None
    marker = body[1]
    if marker == "hb":
        return "heartbeat", None
    if marker == "cs":
        return "checksum", None
    if marker in ("fte", "ftu"):
        return "info", None
    if marker in ("te", "tu"):
        trade = body[2] if len(body) > 2 and isinstance(body[2], list) else None
        ts = trade[1] if trade and len(trade) > 1 and _is_int(trade[1]) else None
        return "trade", ts
    if isinstance(marker, list):
        kind = (sub or {}).get("channel")
        if kind == "trades":
            return "trade", None
        if kind == "book":
            if marker and isinstance(marker[0], list):
                return "book_snapshot", None
            return "book_delta", None
        return "info", None
    return "info", None


def _rest(message, seq_layout: str | None):
    if seq_layout == "index_1_unverified" and len(message) >= 2 and _is_int(message[1]):
        return [message[0], *message[2:]]
    return message


@dataclass
class FeedState:
    prec: str
    seq_scope: str | None
    book: Book = field(default_factory=Book)
    valid: bool = False
    interval_id: str | None = None
    last_conn_seq: int | None = None
    last_chan_seq: dict = field(default_factory=dict)
    seen_trade_ids: set = field(default_factory=set)
    max_trade_id: int | None = None

    def __post_init__(self) -> None:
        self.book.prec = self.prec


@dataclass
class Step:
    state: FeedState
    norms: list
    followup: dict | None


def blank_norm(**overrides) -> dict:
    row = {key: None for key in NORM_KEYS}
    row["norm_schema_v"] = "pmc_norm_v1"
    row["queue_position"] = None
    row["duplicate"] = False
    row["out_of_order"] = False
    row["counted"] = False
    row["applied"] = False
    row["book_valid"] = None
    row["norm_ordinal"] = 0
    row.update(overrides)
    missing = [key for key in NORM_KEYS if key not in row]
    if missing:
        raise KeyError(",".join(missing))
    return {key: row[key] for key in NORM_KEYS}


def _copy_state(state: FeedState) -> FeedState:
    copied = FeedState(prec=state.prec, seq_scope=state.seq_scope)
    copied.book = Book(prec=state.prec)
    copied.book.bids = dict(state.book.bids)
    copied.book.asks = dict(state.book.asks)
    copied.book.orders = dict(state.book.orders)
    copied.valid = state.valid
    copied.interval_id = state.interval_id
    copied.last_conn_seq = state.last_conn_seq
    copied.last_chan_seq = dict(state.last_chan_seq)
    copied.seen_trade_ids = set(state.seen_trade_ids)
    copied.max_trade_id = state.max_trade_id
    return copied


def _base(rec: dict, **overrides) -> dict:
    fields = {
        "raw_id": rec.get("raw_id"),
        "segment_id": rec.get("segment_id"),
        "stream": rec.get("stream"),
        "venue": rec.get("venue"),
        "symbol": rec.get("symbol"),
        "channel": rec.get("channel"),
        "conn_epoch": rec.get("conn_epoch"),
        "seq_local": rec.get("seq_local"),
        "venue_seq": rec.get("venue_seq"),
        "ts_exchange_ms": rec.get("ts_exchange_ms"),
        "ts_recv_utc_ns": rec.get("ts_recv_utc_ns"),
        "ts_recv_mono_ns": rec.get("ts_recv_mono_ns"),
    }
    fields.update(overrides)
    return blank_norm(**fields)


def _seq_key(state: FeedState, rec: dict) -> str:
    if state.seq_scope == "channel":
        return str(rec.get("stream") or rec.get("channel"))
    return "connection"


def _last_seq(state: FeedState, rec: dict) -> int | None:
    if state.seq_scope == "channel":
        return state.last_chan_seq.get(_seq_key(state, rec))
    return state.last_conn_seq


def _set_seq(state: FeedState, rec: dict, value: int | None) -> None:
    if state.seq_scope == "channel":
        state.last_chan_seq[_seq_key(state, rec)] = value
    else:
        state.last_conn_seq = value


def _gap_from(rec: dict, cause: str, before, after) -> dict:
    return {
        "channel": "gap",
        "venue_seq": None,
        "ts_exchange_ms": None,
        "payload": {
            "gap_start_recv": rec.get("ts_recv_utc_ns"),
            "gap_end_recv": rec.get("ts_recv_utc_ns"),
            "cause": cause,
            "conn_epoch_before": rec.get("conn_epoch"),
            "conn_epoch_after": rec.get("conn_epoch"),
            "venue_seq_before": before,
            "venue_seq_after": after,
        },
        "payload_verbatim": None,
        "stream": rec.get("stream"),
    }


def _sequence_jump(state: FeedState, rec: dict) -> bool:
    if state.seq_scope is None:
        return False
    if rec.get("seq_missing"):
        return True
    venue_seq = rec.get("venue_seq")
    if venue_seq is None:
        return False
    previous = _last_seq(state, rec)
    if previous is None:
        return False
    return int(venue_seq) != int(previous) + 1


def _accept_seq(state: FeedState, rec: dict) -> None:
    if rec.get("venue_seq") is not None and state.seq_scope is not None:
        _set_seq(state, rec, int(rec["venue_seq"]))


def _book_entries(rec: dict, seq_layout: str | None) -> list:
    body = _rest(rec["payload"], seq_layout)
    marker = body[1]
    if isinstance(marker, list) and marker and isinstance(marker[0], list):
        return marker
    return [marker]


def _apply_book_entries(state: FeedState, entries: list, snapshot: bool) -> None:
    if state.prec == "R0":
        if snapshot:
            state.book.replace_raw(entries)
        else:
            for entry in entries:
                state.book.apply_raw(entry)
    else:
        if snapshot:
            state.book.replace_agg(entries)
        else:
            for entry in entries:
                state.book.apply_agg(entry)


def _trade_rows(rec: dict, seq_layout: str | None) -> list[tuple[str, list]]:
    body = _rest(rec["payload"], seq_layout)
    marker = body[1]
    if marker in ("te", "tu"):
        return [(marker, body[2])]
    if isinstance(marker, list):
        return [("snapshot", row) for row in marker]
    return []


def step(state: FeedState, rec: dict, *, seq_layout: str | None) -> Step:
    state = _copy_state(state)
    channel = rec.get("channel")
    followup = None
    norms: list[dict] = []
    jumped = channel in {"book_snapshot", "book_delta", "trade", "heartbeat", "checksum"} and _sequence_jump(
        state, rec
    )
    if jumped:
        previous = _last_seq(state, rec)
        followup = _gap_from(rec, "seq_jump", previous, rec.get("venue_seq"))
        state.valid = False
        _accept_seq(state, rec)
    if channel == "book_snapshot":
        if not jumped:
            entries = _book_entries(rec, seq_layout)
            _apply_book_entries(state, entries, True)
            state.valid = True
            interval = f"iv-{rec.get('raw_id')}"
            state.interval_id = interval
            _accept_seq(state, rec)
        norms.append(
            _base(
                rec,
                channel="book_snapshot",
                applied=not jumped,
                book_valid=bool(state.valid) and not jumped,
                validity_interval_id=state.interval_id if not jumped else None,
                interval_event="open" if not jumped else None,
                counted=not jumped,
            )
        )
    elif channel == "book_delta":
        applied = False
        if not jumped and state.valid:
            _apply_book_entries(state, _book_entries(rec, seq_layout), False)
            applied = True
            _accept_seq(state, rec)
        elif not jumped:
            _accept_seq(state, rec)
        norms.append(
            _base(
                rec,
                channel="book_delta",
                applied=applied,
                book_valid=bool(state.valid) and not jumped,
                validity_interval_id=state.interval_id if applied else None,
            )
        )
    elif channel == "checksum":
        checksum_ok = False
        if not jumped and state.valid:
            try:
                _text, local = state.book.checksum()
                body = _rest(rec["payload"], seq_layout)
                checksum_ok = int(body[2]) == int(local)
            except (ValueError, TypeError, IndexError):
                checksum_ok = False
        if not checksum_ok:
            state.valid = False
            if followup is None:
                followup = _gap_from(rec, "checksum_fail", _last_seq(state, rec), rec.get("venue_seq"))
        if not jumped:
            _accept_seq(state, rec)
        norms.append(
            _base(
                rec,
                channel="checksum",
                applied=False,
                book_valid=bool(checksum_ok and state.valid),
                checksum_ok=checksum_ok,
                validity_interval_id=state.interval_id if checksum_ok else None,
            )
        )
    elif channel == "trade":
        if not jumped:
            _accept_seq(state, rec)
        for ordinal, (kind, trade) in enumerate(_trade_rows(rec, seq_layout)):
            trade_id = trade[0] if trade else None
            mts = trade[1] if trade and len(trade) > 1 and _is_int(trade[1]) else None
            amount = trade[2] if trade and len(trade) > 2 else None
            price = trade[3] if trade and len(trade) > 3 else None
            duplicate = trade_id in state.seen_trade_ids if trade_id is not None else False
            out_of_order = (
                trade_id is not None
                and state.max_trade_id is not None
                and not duplicate
                and int(trade_id) < int(state.max_trade_id)
            )
            if trade_id is not None and not duplicate:
                state.seen_trade_ids.add(int(trade_id))
                state.max_trade_id = int(trade_id) if state.max_trade_id is None else max(int(state.max_trade_id), int(trade_id))
                if len(state.seen_trade_ids) > TRADE_ID_WINDOW:
                    floor_id = int(state.max_trade_id) - TRADE_ID_WINDOW
                    state.seen_trade_ids = {item for item in state.seen_trade_ids if int(item) >= floor_id}
            side = None
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                if amount > 0:
                    side = "buy"
                elif amount < 0:
                    side = "sell"
            norms.append(
                _base(
                    rec,
                    channel="trade",
                    ts_exchange_ms=mts,
                    duplicate=duplicate,
                    out_of_order=out_of_order,
                    counted=not duplicate and not jumped,
                    trade_id=trade_id,
                    price=price,
                    amount=amount,
                    side=side,
                    update_kind=kind,
                    applied=not duplicate and not jumped,
                    norm_ordinal=ordinal,
                )
            )
    elif channel == "heartbeat":
        if not jumped:
            _accept_seq(state, rec)
        norms.append(
            _base(
                rec,
                channel="heartbeat",
                ts_exchange_ms=None,
                applied=False,
                book_valid=state.valid,
            )
        )
    elif channel == "gap":
        payload = rec.get("payload") or {}
        cause = payload.get("cause")
        interval_event = None
        interval_id = None
        if cause in {"checksum_fail", "seq_jump", "disconnect", "maintenance"} and state.interval_id:
            interval_event = "close"
            interval_id = state.interval_id
            state.interval_id = None
        if cause in {"checksum_fail", "seq_jump", "disconnect", "write_fail", "startup", "maintenance"}:
            state.valid = False
        if cause == "disconnect":
            state.last_conn_seq = None
            state.last_chan_seq.clear()
        norms.append(
            _base(
                rec,
                channel="gap",
                book_valid=state.valid,
                validity_interval_id=interval_id,
                interval_event=interval_event,
                applied=False,
            )
        )
    else:
        norms.append(_base(rec, channel=channel if channel in CHANNELS else "info", applied=False))
    return Step(state, norms, followup)


def normalize_raw_records(records: list[dict], *, prec: str, seq_scope: str | None, seq_layout: str | None) -> list[dict]:
    state = FeedState(prec=prec, seq_scope=seq_scope)
    norms: list[dict] = []
    for rec in records:
        result = step(state, rec, seq_layout=seq_layout)
        state = result.state
        norms.extend(result.norms)
    return norms


def canonical_norms(records: list[dict], *, prec: str, seq_scope: str | None, seq_layout: str | None) -> bytes:
    rows = normalize_raw_records(records, prec=prec, seq_scope=seq_scope, seq_layout=seq_layout)
    return "\n".join(dumps(row) for row in rows).encode("utf-8")


def example_checksum_matches_docs() -> bool:
    text = docs.CHECKSUM_EXAMPLE_STRING
    return signed_crc32(text) == docs.CHECKSUM_EXAMPLE_SIGNED and signed_crc32("123456789") == -873187034
