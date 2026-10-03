"""Pure contracts for the cross-venue BTC perp price tape (shadow research only).

Public, key-less market data from Binance USDT-M ``BTCUSDT``, Bybit linear
``BTCUSDT`` and OKX ``BTC-USDT-SWAP`` is bucketed on the same epoch-second
clock as the Bitfinex ``market_microstructure_1s`` tape so the analyzer can
measure whether those venues lead Bitfinex ``tBTCF0:USTF0``.

Nothing here can create, modify, cancel or gate an order, and nothing reads or
writes tile toggles, relay state, fee constants or policy identity. The
collector process, the bot's shadow challenger and the analyzer all share this
module so the stored encoding and the leader rule have exactly one definition.

Storage is one compact JSON row per UTC minute (``FILE_NAME``): per venue a
reference mid plus integer per-second offsets in ``PRICE_UNIT`` steps and
signed taker volume in ``QTY_UNIT`` steps, and the aligned Bitfinex mid.
"""
from __future__ import annotations

import json
import math
import threading
from collections import deque
from typing import Any, Iterable, Mapping, Optional

SCHEMA = "cross_venue_tape_1m_v1"
FILE_NAME = "cross_venue_tape_1m.jsonl"
LIVE_SCHEMA = "cross_venue_live_v1"
# Rewritten every second; operational state, never shipped as evidence.
LIVE_FILE = "cross_venue_live.json"
HEALTH_SCHEMA = "cross_venue_health_v1"
COLLECTOR_VERSION = "cross_venue_collector_v1_20261004"

PRICE_UNIT = 0.05
QTY_UNIT = 0.001
# Binance book imbalance (bid - ask) / (bid + ask) per second, stored as ints.
IMBALANCE_UNIT = 0.001
DEPTH5_STREAM = "btcusdt@depth5@100ms"
DEPTH20_STREAM = "btcusdt@depth20@500ms"
MAX_QUOTE_AGE_SEC = 3.5
ROTATE_BYTES = 20 * 1024 * 1024
LIVE_HISTORY_SEC = 150
VENUE_STALE_SEC = 30.0
COLLECTOR_DOWN_SEC = 30.0
OKX_CT_VAL_BTC = 0.01

LEADER_WINDOW_SEC = 10
LEADER_MIN_MOVE_BP = 2.0
LEADER_PRIORITY = ("binance", "bybit", "okx")
FEATURE_WINDOWS_SEC = (1, 3, 5, 10, 30, 60)

LONG, SHORT, NONE = "LONG", "SHORT", "NONE"

VENUES = {
    "binance": {
        "symbol": "BTCUSDT",
        "connections": (
            {"name": "binance_public",
             "url": f"wss://fstream.binance.com/public/stream?streams={DEPTH5_STREAM}/{DEPTH20_STREAM}",
             "subscribe": None, "app_ping": None},
            {"name": "binance_market",
             "url": "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s",
             "subscribe": None, "app_ping": None},
        ),
    },
    "bybit": {
        "symbol": "BTCUSDT",
        "connections": (
            {"name": "bybit_linear",
             "url": "wss://stream.bybit.com/v5/public/linear",
             "subscribe": {"op": "subscribe", "args": ["tickers.BTCUSDT", "publicTrade.BTCUSDT"]},
             "app_ping": '{"op":"ping"}'},
        ),
    },
    "okx": {
        "symbol": "BTC-USDT-SWAP",
        "connections": (
            {"name": "okx_public",
             "url": "wss://ws.okx.com:8443/ws/v5/public",
             "subscribe": {"op": "subscribe", "args": [
                 {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                 {"channel": "trades", "instId": "BTC-USDT-SWAP"},
                 {"channel": "funding-rate", "instId": "BTC-USDT-SWAP"},
                 {"channel": "open-interest", "instId": "BTC-USDT-SWAP"},
             ]},
             "app_ping": "ping"},
        ),
    },
}


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------
# Message parsers -> normalized events
#   ("bbo", bid, ask, exch_ms) | ("trade", price, qty_btc, "BUY"|"SELL", exch_ms)
#   ("deriv", {field: value})
# --------------------------------------------------------------------------
def book_imbalance(bids: Iterable, asks: Iterable, levels: int) -> Optional[float]:
    """(bid qty - ask qty) / (bid qty + ask qty) over the first ``levels`` levels."""
    try:
        bq = sum(float(level[1]) for level in list(bids)[:levels])
        aq = sum(float(level[1]) for level in list(asks)[:levels])
    except (TypeError, ValueError, IndexError):
        return None
    total = bq + aq
    if not math.isfinite(total) or total <= 0:
        return None
    return (bq - aq) / total


def parse_binance(message: Mapping[str, Any]) -> list:
    data = message.get("data") if isinstance(message.get("data"), Mapping) else message
    kind = data.get("e")
    if kind == "depthUpdate":
        bids, asks = data.get("b") or [], data.get("a") or []
        if not bids or not asks:
            return []
        if message.get("stream") == DEPTH20_STREAM:
            # Book-imbalance evidence only; the BBO keeps coming from depth5@100ms.
            imb = book_imbalance(bids, asks, 20)
            return [] if imb is None else [("depth", "imb20", imb)]
        bid, ask = _finite(bids[0][0]), _finite(asks[0][0])
        out = [("bbo", bid, ask, _finite(data.get("T") or data.get("E")))] if bid and ask else []
        imb = book_imbalance(bids, asks, 5)
        if imb is not None:
            out.append(("depth", "imb5", imb))
        return out
    if kind == "aggTrade":
        price, qty = _finite(data.get("p")), _finite(data.get("q"))
        if not price or not qty:
            return []
        # m=True: the buyer is the maker, so the aggressor sold.
        side = "SELL" if data.get("m") is True else "BUY"
        return [("trade", price, qty, side, _finite(data.get("T")))]
    if kind == "markPriceUpdate":
        return [("deriv", {
            "funding_rate": _finite(data.get("r")),
            "mark": _finite(data.get("p")),
            "index": _finite(data.get("i")),
            "next_funding_ms": _finite(data.get("T")),
        })]
    return []


def parse_bybit(message: Mapping[str, Any]) -> list:
    topic = str(message.get("topic") or "")
    data = message.get("data")
    if topic.startswith("tickers.") and isinstance(data, Mapping):
        # Deltas omit unchanged fields, so every ticker push re-confirms the
        # current book even when it carries no new bid/ask.
        bid, ask = _finite(data.get("bid1Price")), _finite(data.get("ask1Price"))
        out = [("bbo_partial", bid, ask, _finite(message.get("ts")))]
        deriv = {
            "funding_rate": _finite(data.get("fundingRate")),
            "mark": _finite(data.get("markPrice")),
            "index": _finite(data.get("indexPrice")),
            "open_interest": _finite(data.get("openInterest")),
            "next_funding_ms": _finite(data.get("nextFundingTime")),
        }
        deriv = {k: v for k, v in deriv.items() if v is not None}
        if deriv:
            out.append(("deriv", deriv))
        return out
    if topic.startswith("publicTrade.") and isinstance(data, list):
        out = []
        for trade in data:
            if not isinstance(trade, Mapping):
                continue
            price, qty = _finite(trade.get("p")), _finite(trade.get("v"))
            side = str(trade.get("S") or "").upper()
            if price and qty and side in ("BUY", "SELL"):
                out.append(("trade", price, qty, side, _finite(trade.get("T"))))
        return out
    return []


def parse_okx(message: Mapping[str, Any]) -> list:
    arg = message.get("arg") if isinstance(message.get("arg"), Mapping) else {}
    channel = arg.get("channel")
    data = message.get("data")
    if message.get("event") or not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        if channel == "tickers":
            bid, ask = _finite(item.get("bidPx")), _finite(item.get("askPx"))
            if bid and ask:
                out.append(("bbo", bid, ask, _finite(item.get("ts"))))
        elif channel == "trades":
            price, size = _finite(item.get("px")), _finite(item.get("sz"))
            side = str(item.get("side") or "").upper()
            if price and size and side in ("BUY", "SELL"):
                out.append(("trade", price, size * OKX_CT_VAL_BTC, side, _finite(item.get("ts"))))
        elif channel == "funding-rate":
            out.append(("deriv", {k: v for k, v in {
                "funding_rate": _finite(item.get("fundingRate")),
                "next_funding_ms": _finite(item.get("fundingTime")),
            }.items() if v is not None}))
        elif channel == "open-interest":
            oi = _finite(item.get("oiCcy"))
            if oi is not None:
                out.append(("deriv", {"open_interest": oi}))
    return out


PARSERS = {"binance": parse_binance, "bybit": parse_bybit, "okx": parse_okx}


# --------------------------------------------------------------------------
# Per-venue second accumulator (thread-safe; WS threads write, ticker closes)
# --------------------------------------------------------------------------
class VenueAccumulator:
    """Keeps the last quote seen inside each receive-second and its taker flow.

    Seconds are keyed on local receive time, the same clock the Bitfinex tape
    uses, so the closer's own wake-up jitter can never move an update across a
    bucket boundary.
    """

    def __init__(self, venue: str, keep_seconds: int = 8) -> None:
        self.venue = venue
        self._lock = threading.Lock()
        self._bid = self._ask = None
        self._quote_ts = None
        self._last_trade = None
        self._quote_by_sec: dict = {}
        self._flow_by_sec: dict = {}
        self._lat_ms: deque = deque(maxlen=512)
        self._deriv: dict = {}
        self._depth_by_sec: dict = {}
        self._depth_last: dict = {}
        self._keep = int(keep_seconds)
        self.last_msg_ts = None
        self.msgs = 0

    def on_events(self, events: Iterable[tuple], recv_ts: float) -> None:
        sec = int(recv_ts)
        with self._lock:
            self.msgs += 1
            self.last_msg_ts = recv_ts
            for event in events:
                kind = event[0]
                if kind in ("bbo", "bbo_partial"):
                    bid = event[1] if event[1] else self._bid
                    ask = event[2] if event[2] else self._ask
                    if not bid or not ask or ask < bid:
                        continue
                    self._bid, self._ask, self._quote_ts = bid, ask, recv_ts
                    self._quote_by_sec[sec] = (bid, ask, recv_ts)
                    if event[3]:
                        self._lat_ms.append(recv_ts * 1000.0 - event[3])
                elif kind == "trade":
                    _, price, qty, side, exch_ms = event
                    cell = self._flow_by_sec.setdefault(sec, [0.0, 0.0, None])
                    cell[0 if side == "BUY" else 1] += qty
                    cell[2] = price
                    self._last_trade = price
                    if exch_ms:
                        self._lat_ms.append(recv_ts * 1000.0 - exch_ms)
                elif kind == "deriv":
                    self._deriv.update({k: v for k, v in event[1].items() if v is not None})
                    self._deriv["updated_ts"] = recv_ts
                elif kind == "depth":
                    _, key, value = event
                    self._depth_by_sec.setdefault(sec, {})[key] = value
                    self._depth_last[key] = (value, recv_ts)
            for store in (self._quote_by_sec, self._flow_by_sec, self._depth_by_sec):
                if len(store) > self._keep:
                    for old in sorted(store)[: len(store) - self._keep]:
                        store.pop(old, None)

    def close_second(self, sec: int, prev_quote: Optional[tuple]) -> dict:
        """Sample for bucket ``sec``: the quote as of ``sec + 1`` and its flow."""
        with self._lock:
            quote = self._quote_by_sec.pop(sec, None) or prev_quote
            flow = self._flow_by_sec.pop(sec, None)
            depth = self._depth_by_sec.pop(sec, None) or {}
            for store in (self._quote_by_sec, self._flow_by_sec, self._depth_by_sec):
                for old in [s for s in store if s < sec]:
                    store.pop(old, None)
            imbalance = {}
            for key in ("imb5", "imb20"):
                value = depth.get(key)
                if value is None:
                    last = self._depth_last.get(key)
                    # Same staleness bound as the quote: at most MAX_QUOTE_AGE_SEC old.
                    if last is not None and 0.0 <= sec + 1.0 - last[1] <= MAX_QUOTE_AGE_SEC:
                        value = last[0]
                imbalance[key] = value
        mid = None
        if quote is not None and 0.0 <= sec + 1.0 - quote[2] <= MAX_QUOTE_AGE_SEC:
            mid = (quote[0] + quote[1]) / 2.0
        return {
            "sec": sec,
            "mid": mid,
            "quote": quote,
            "last": None if flow is None else flow[2],
            "buy": 0.0 if flow is None else flow[0],
            "sell": 0.0 if flow is None else flow[1],
            "imb5": imbalance.get("imb5"),
            "imb20": imbalance.get("imb20"),
        }

    def derivatives(self) -> dict:
        with self._lock:
            return dict(self._deriv)

    def drain_latency_ms(self) -> Optional[float]:
        with self._lock:
            values = sorted(self._lat_ms)
            self._lat_ms.clear()
        return None if not values else round(values[len(values) // 2], 1)


# --------------------------------------------------------------------------
# Minute row encoding
# --------------------------------------------------------------------------
def _offsets(values: list, ref: Optional[float], unit: float) -> list:
    return [None if v is None or ref is None else int(round((v - ref) / unit)) for v in values]


def encode_minute(minute_ts: int, venue_samples: Mapping[str, list],
                  bfx_mids: list, *, derivatives: Optional[Mapping[str, Any]] = None,
                  latency_ms: Optional[Mapping[str, Any]] = None,
                  meta: Optional[Mapping[str, Any]] = None) -> dict:
    """One compact row for the 60 buckets starting at ``minute_ts``."""
    minute_ts = int(minute_ts)

    def ref_of(values):
        return next((v for v in values if v is not None), None)

    bfx_ref = ref_of(bfx_mids)
    row = {
        "schema": SCHEMA,
        "minute_ts": minute_ts,
        "n": 60,
        "price_unit": PRICE_UNIT,
        "qty_unit": QTY_UNIT,
        "bfx": {"symbol": "tBTCF0:USTF0",
                "m0": None if bfx_ref is None else round(bfx_ref, 3),
                "dm": _offsets(bfx_mids, bfx_ref, PRICE_UNIT)},
        "venues": {},
    }
    for venue, samples in venue_samples.items():
        by_offset = {int(s["sec"]) - minute_ts: s for s in samples}
        seq = [by_offset.get(i) or {} for i in range(60)]
        mids = [s.get("mid") for s in seq]
        ref = ref_of(mids)
        lasts = [s.get("last") for s in seq]
        row["venues"][venue] = {
            "symbol": VENUES.get(venue, {}).get("symbol"),
            "m0": None if ref is None else round(ref, 3),
            "dm": _offsets(mids, ref, PRICE_UNIT),
            # Last trade relative to the same-second mid (null without a trade).
            "dl": [None if l is None or m is None else int(round((l - m) / PRICE_UNIT))
                   for l, m in zip(lasts, mids)],
            "b": [int(round((s.get("buy") or 0.0) / QTY_UNIT)) for s in seq],
            "s": [int(round((s.get("sell") or 0.0) / QTY_UNIT)) for s in seq],
        }
        if any("up" in s for s in seq):
            # Per-second connection mask: b/s zeros at a '0' second are unknown flow.
            row["venues"][venue]["up"] = "".join("1" if s.get("up") else "0" for s in seq)
        for key in ("imb5", "imb20"):
            if any(s.get(key) is not None for s in seq):
                row["venues"][venue][key] = [None if s.get(key) is None else int(round(s[key] / IMBALANCE_UNIT))
                                             for s in seq]
                row["imbalance_unit"] = IMBALANCE_UNIT
    basis = {}
    for venue, v in row["venues"].items():
        vals = []
        for i in range(60):
            vm = _decode_value(v["m0"], v["dm"][i])
            bm = _decode_value(row["bfx"]["m0"], row["bfx"]["dm"][i])
            if vm and bm:
                vals.append((vm / bm - 1.0) * 1e4)
        basis[venue] = None if not vals else round(sum(vals) / len(vals), 3)
    row["basis_bp_mean"] = basis
    if derivatives:
        row["derivatives"] = {k: dict(v) for k, v in derivatives.items() if v}
    if latency_ms:
        row["latency_ms_median"] = dict(latency_ms)
    if meta:
        row["meta"] = dict(meta)
    return row


def _decode_value(ref: Optional[float], offset: Optional[int], unit: float = PRICE_UNIT) -> Optional[float]:
    if ref is None or offset is None:
        return None
    return float(ref) + int(offset) * unit


def decode_minute(row: Mapping[str, Any]) -> dict:
    """Return {"bfx": {sec: mid}, venue: {sec: {"mid","last","buy","sell"}}}."""
    if not isinstance(row, Mapping) or row.get("schema") != SCHEMA:
        return {}
    unit = _finite(row.get("price_unit")) or PRICE_UNIT
    qunit = _finite(row.get("qty_unit")) or QTY_UNIT
    t0 = int(row.get("minute_ts") or 0)
    out = {"bfx": {}}
    bfx = row.get("bfx") or {}
    for i, off in enumerate(bfx.get("dm") or []):
        value = _decode_value(bfx.get("m0"), off, unit)
        if value is not None:
            out["bfx"][t0 + i] = value
    for venue, v in (row.get("venues") or {}).items():
        cells = {}
        dms, dls = v.get("dm") or [], v.get("dl") or []
        buys, sells = v.get("b") or [], v.get("s") or []
        up = v.get("up")
        for i in range(min(60, len(dms))):
            mid = _decode_value(v.get("m0"), dms[i], unit)
            dl = dls[i] if i < len(dls) else None
            cells[t0 + i] = {
                "mid": mid,
                "last": None if mid is None or dl is None else mid + dl * unit,
                "buy": (buys[i] if i < len(buys) else 0) * qunit,
                "sell": (sells[i] if i < len(sells) else 0) * qunit,
                # None for rows written before the mask existed.
                "up": None if not isinstance(up, str) else (i < len(up) and up[i] == "1"),
            }
        out[venue] = cells
    return out


# --------------------------------------------------------------------------
# Live state (collector -> bot) and the leader challenger rule
# --------------------------------------------------------------------------
def live_mid_at(live: Mapping[str, Any], venue: str, sec: int) -> Optional[float]:
    history = (live.get("mids") or {}).get(venue) or []
    start = live.get("history_start_ts")
    if start is None:
        return None
    idx = int(sec) - int(start)
    if idx < 0 or idx >= len(history):
        return None
    return _finite(history[idx])


def _ret_bp(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if not now or not then:
        return None
    return round((now / then - 1.0) * 1e4, 4)


def leader_features(live: Optional[Mapping[str, Any]], decision_ts: float,
                    bfx_mid_at=None) -> dict:
    """Causal leader facts at ``decision_ts`` using buckets that closed by then.

    Bucket ``s`` carries the quote as of ``s + 1``, so the newest bucket known at
    ``decision_ts`` is ``floor(decision_ts) - 1``. ``bfx_mid_at(sec)`` supplies
    the Bitfinex mid of the same bucket for the lead gap and basis.
    """
    anchor = int(math.floor(float(decision_ts))) - 1
    out = {
        "schema": "cross_venue_leader_features_v1",
        "anchor_bucket_ts": anchor,
        "window_sec": LEADER_WINDOW_SEC,
        "min_move_bp": LEADER_MIN_MOVE_BP,
        "leader_venue": None,
        "leader_ret_bp": None,
        "side": NONE,
        "reason": "NO_LIVE_STATE",
        "venues": {},
        "bfx": {},
    }
    if not isinstance(live, Mapping) or live.get("schema") != LIVE_SCHEMA:
        return out
    bfx_now = bfx_mid_at(anchor) if bfx_mid_at else None
    if bfx_now:
        out["bfx"] = {f"ret_{w}s_bp": _ret_bp(bfx_now, bfx_mid_at(anchor - w))
                      for w in FEATURE_WINDOWS_SEC}
        out["bfx"]["mid"] = round(bfx_now, 3)
    for venue in VENUES:
        now_mid = live_mid_at(live, venue, anchor)
        cell = {"mid": None if now_mid is None else round(now_mid, 3)}
        for w in FEATURE_WINDOWS_SEC:
            cell[f"ret_{w}s_bp"] = _ret_bp(now_mid, live_mid_at(live, venue, anchor - w))
            bfx_ret = (out["bfx"] or {}).get(f"ret_{w}s_bp")
            if cell[f"ret_{w}s_bp"] is not None and bfx_ret is not None:
                cell[f"gap_{w}s_bp"] = round(cell[f"ret_{w}s_bp"] - bfx_ret, 4)
        cell["basis_bp"] = (None if now_mid is None or not bfx_now
                            else round((now_mid / bfx_now - 1.0) * 1e4, 3))
        out["venues"][venue] = cell
    for venue in LEADER_PRIORITY:
        ret = out["venues"].get(venue, {}).get(f"ret_{LEADER_WINDOW_SEC}s_bp")
        if ret is not None:
            out["leader_venue"], out["leader_ret_bp"] = venue, ret
            break
    if out["leader_venue"] is None:
        out["reason"] = "NO_FRESH_LEADER_WINDOW"
        return out
    if abs(out["leader_ret_bp"]) < LEADER_MIN_MOVE_BP:
        out["reason"] = "BELOW_THRESHOLD"
        return out
    out["side"] = LONG if out["leader_ret_bp"] > 0 else SHORT
    out["reason"] = "LEADER_MOVE"
    return out


def health_from_live(live: Optional[Mapping[str, Any]], now: float, *,
                     enabled: bool = True) -> dict:
    """Staleness verdict for monitoring; never affects readiness or orders."""
    out = {"schema": HEALTH_SCHEMA, "status": "DISABLED" if not enabled else "DOWN",
           "collector_age_s": None, "venues": {}, "stale_venues": [], "stats": {}}
    if not enabled:
        return out
    if not isinstance(live, Mapping) or live.get("schema") != LIVE_SCHEMA:
        out["reason"] = "NO_LIVE_STATE"
        return out
    written = _finite(live.get("written_ts"))
    out["collector_age_s"] = None if written is None else round(now - written, 1)
    out["stats"] = dict(live.get("stats") or {})
    out["collector_version"] = live.get("collector_version")
    for venue, cell in (live.get("venues") or {}).items():
        last = _finite((cell or {}).get("last_bbo_ts"))
        age = None if last is None else round(now - last, 1)
        out["venues"][venue] = {
            "bbo_age_s": age,
            "connected": bool((cell or {}).get("connected")),
            "reconnects": int((cell or {}).get("reconnects") or 0),
            "last_error": (cell or {}).get("last_error"),
            "msgs": int((cell or {}).get("msgs") or 0),
        }
        if age is None or age > VENUE_STALE_SEC:
            out["stale_venues"].append(venue)
    if written is None or now - written > COLLECTOR_DOWN_SEC:
        out["status"], out["reason"] = "DOWN", "COLLECTOR_HEARTBEAT_STALE"
    elif not out["venues"]:
        out["status"], out["reason"] = "STALE", "NO_VENUES"
    elif len(out["stale_venues"]) == len(out["venues"]):
        out["status"], out["reason"] = "STALE", "ALL_VENUES_STALE"
    elif out["stale_venues"]:
        out["status"], out["reason"] = "DEGRADED", "VENUE_STALE"
    else:
        out["status"], out["reason"] = "OK", None
    return out


def read_live(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
