"""Pure contracts for the market-context tape (watch-only research collection).

Public, key-less data that adds dimensions the Bitfinex and perp lead-lag
tapes do not have:

* spot reference mids on the 1-second clock - Coinbase ``BTC-USD`` (US spot
  demand), Coinbase ``USDT-USD`` (stablecoin basis) and Binance spot
  ``BTCUSDT`` - and the per-second Coinbase premium versus Bitfinex
  ``tBTCF0:USTF0``, Binance spot and the Binance perp;
* forced-liquidation events from Binance, Bybit and OKX;
* a once-a-minute derivatives snapshot (funding, predicted funding, mark,
  index, basis, open interest and their deltas) for Binance, Bybit, OKX and
  Bitfinex from public REST;
* session and macro-calendar flags (``market_session_calendar``) and a causal
  trailing volatility regime (``trailing_regime``).

Nothing here can create, modify, cancel or gate an order, and nothing reads
or writes fee constants, tile toggles, relay state or policy identity.

Every feed carries an explicit per-second ``up`` mask; a disconnected or
stale feed is written as ``null``/``0`` and is never forward-filled.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

SCHEMA = "market_context_1m_v1"
FILE_NAME = "market_context_1m.jsonl"
LIQ_SCHEMA = "liquidation_event_v1"
LIQ_FILE_NAME = "liquidations.jsonl"
LIVE_SCHEMA = "market_context_live_v1"
# Rewritten every few seconds; operational state, never shipped as evidence.
LIVE_FILE = "market_context_live.json"
HEALTH_SCHEMA = "market_context_health_v1"
COLLECTOR_VERSION = "market_context_collector_v1_20261002"

SPOT_PRICE_UNIT = 0.005
PREMIUM_UNIT_BP = 0.01
ROTATE_BYTES = 20 * 1024 * 1024
FEED_STALE_SEC = 60.0
COLLECTOR_DOWN_SEC = 60.0
DERIV_STALE_SEC = 180.0
OKX_USDT_CT_VAL_BTC = 0.01
OKX_USD_CT_VAL_USD = 100.0

SPOT_FEEDS = ("coinbase", "binance_spot")
AUX_SPOT_FEEDS = ("coinbase_usdt",)
LIQ_VENUES = ("binance", "bybit", "okx")
DERIV_VENUES = ("binance", "bybit", "okx", "bitfinex")

FEEDS = {
    "coinbase": {
        "symbol": "BTC-USD",
        "connection": {
            "name": "coinbase_ticker",
            "url": "wss://ws-feed.exchange.coinbase.com",
            "subscribe": {"type": "subscribe", "product_ids": ["BTC-USD", "USDT-USD"],
                          "channels": ["ticker", "heartbeat"]},
            "app_ping": None,
        },
        "routes": ("coinbase", "coinbase_usdt"),
    },
    "binance_spot": {
        "symbol": "BTCUSDT",
        "connection": {
            "name": "binance_spot_depth",
            "url": "wss://stream.binance.com:9443/stream?streams=btcusdt@depth5@100ms",
            "subscribe": None,
            "app_ping": None,
        },
        "routes": ("binance_spot",),
    },
    "liq_binance": {
        "symbol": "BTCUSDT",
        "connection": {
            "name": "binance_force_order",
            "url": "wss://fstream.binance.com/market/stream?streams=btcusdt@forceOrder",
            "subscribe": None,
            # Liquidations are sparse; a request/response keeps the socket provably alive.
            "app_ping": '{"method":"LIST_SUBSCRIPTIONS","id":1}',
        },
        "routes": ("liq",),
    },
    "liq_bybit": {
        "symbol": "BTCUSDT",
        "connection": {
            "name": "bybit_all_liquidation",
            "url": "wss://stream.bybit.com/v5/public/linear",
            "subscribe": {"op": "subscribe", "args": ["allLiquidation.BTCUSDT"]},
            "app_ping": '{"op":"ping"}',
        },
        "routes": ("liq",),
    },
    "liq_okx": {
        "symbol": "BTC-USDT-SWAP,BTC-USD-SWAP",
        "connection": {
            "name": "okx_liquidation_orders",
            "url": "wss://ws.okx.com:8443/ws/v5/public",
            "subscribe": {"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]},
            "app_ping": "ping",
        },
        "routes": ("liq",),
    },
}

LIQ_FEED_VENUE = {"liq_binance": "binance", "liq_bybit": "bybit", "liq_okx": "okx"}

REST_ENDPOINTS = {
    "binance": (
        ("premium", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"),
        ("oi", "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"),
    ),
    "bybit": (
        ("ticker", "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"),
    ),
    "okx": (
        ("funding", "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"),
        ("oi", "https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP"),
        ("mark", "https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId=BTC-USDT-SWAP"),
        ("index", "https://www.okx.com/api/v5/market/index-tickers?instId=BTC-USDT"),
    ),
    "bitfinex": (
        ("status", "https://api-pub.bitfinex.com/v2/status/deriv?keys=tBTCF0:USTF0"),
    ),
}


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso_ms(value: Any) -> Optional[float]:
    if not isinstance(value, str) or len(value) < 20:
        return None
    text = value.rstrip("Z")
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(text + "+00:00").timestamp() * 1000.0
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Message parsers -> routed events  [(route, event_tuple), ...]
#   event: ("bbo", bid, ask, exch_ms) | ("hb",) | ("liq", {...})
# --------------------------------------------------------------------------
def parse_coinbase(message: Mapping[str, Any]) -> list:
    kind = message.get("type")
    product = message.get("product_id")
    route = {"BTC-USD": "coinbase", "USDT-USD": "coinbase_usdt"}.get(product)
    if route is None:
        return []
    if kind == "ticker":
        bid, ask = _finite(message.get("best_bid")), _finite(message.get("best_ask"))
        if bid and ask:
            return [(route, ("bbo", bid, ask, _iso_ms(message.get("time"))))]
        return []
    if kind == "heartbeat":
        return [(route, ("hb",))]
    return []


def parse_binance_spot(message: Mapping[str, Any]) -> list:
    data = message.get("data") if isinstance(message.get("data"), Mapping) else message
    bids, asks = data.get("bids") or [], data.get("asks") or []
    if not bids or not asks:
        return []
    bid, ask = _finite(bids[0][0]), _finite(asks[0][0])
    return [("binance_spot", ("bbo", bid, ask, None))] if bid and ask else []


def _liq(venue: str, symbol: str, exch_ms: Optional[float], liq_side: str, order_side: str,
         qty_btc: Optional[float], price: Optional[float], completeness: str, raw_qty=None,
         raw_unit=None) -> Optional[tuple]:
    if not qty_btc or not price or qty_btc <= 0 or price <= 0:
        return None
    return ("liq", ("liq", {
        "venue": venue,
        "symbol": symbol,
        "exch_ts": None if exch_ms is None else round(exch_ms / 1000.0, 3),
        "liq_side": liq_side,
        "order_side": order_side,
        "qty_btc": round(qty_btc, 6),
        "price": round(price, 2),
        "notional_usd": round(qty_btc * price, 2),
        "raw_qty": raw_qty,
        "raw_unit": raw_unit,
        "completeness": completeness,
    }))


def parse_binance_liq(message: Mapping[str, Any]) -> list:
    data = message.get("data") if isinstance(message.get("data"), Mapping) else message
    if data.get("e") != "forceOrder" or not isinstance(data.get("o"), Mapping):
        return []
    order = data["o"]
    side = str(order.get("S") or "").upper()
    if side not in ("BUY", "SELL"):
        return []
    qty = _finite(order.get("z")) or _finite(order.get("q"))
    price = _finite(order.get("ap")) or _finite(order.get("p"))
    # A SELL liquidation order closes a long. Binance pushes at most one
    # liquidation snapshot per symbol per second, so totals are a lower bound.
    ev = _liq("binance", str(order.get("s") or "BTCUSDT"), _finite(order.get("T")),
              "LONG_LIQUIDATED" if side == "SELL" else "SHORT_LIQUIDATED", side, qty, price,
              "SNAPSHOT_MAX_1_PER_SEC_LOWER_BOUND", order.get("z") or order.get("q"), "BTC")
    return [ev] if ev else []


def parse_bybit_liq(message: Mapping[str, Any]) -> list:
    if not str(message.get("topic") or "").startswith("allLiquidation."):
        return []
    out = []
    for item in message.get("data") or []:
        if not isinstance(item, Mapping):
            continue
        side = str(item.get("S") or "")
        if side not in ("Buy", "Sell"):
            continue
        # Bybit: S=Buy means a long position was liquidated.
        ev = _liq("bybit", str(item.get("s") or "BTCUSDT"), _finite(item.get("T")),
                  "LONG_LIQUIDATED" if side == "Buy" else "SHORT_LIQUIDATED",
                  "SELL" if side == "Buy" else "BUY", _finite(item.get("v")), _finite(item.get("p")),
                  "ALL_EVENTS", item.get("v"), "BTC")
        if ev:
            out.append(ev)
    return out


def parse_okx_liq(message: Mapping[str, Any]) -> list:
    arg = message.get("arg") if isinstance(message.get("arg"), Mapping) else {}
    if arg.get("channel") != "liquidation-orders" or message.get("event"):
        return []
    out = []
    for item in message.get("data") or []:
        if not isinstance(item, Mapping):
            continue
        inst = str(item.get("instId") or "")
        if inst not in ("BTC-USDT-SWAP", "BTC-USD-SWAP"):
            continue
        for d in item.get("details") or []:
            if not isinstance(d, Mapping):
                continue
            side = str(d.get("side") or "").lower()
            if side not in ("buy", "sell"):
                continue
            size, price = _finite(d.get("sz")), _finite(d.get("bkPx"))
            if not size or not price:
                continue
            if inst == "BTC-USDT-SWAP":
                qty, unit = size * OKX_USDT_CT_VAL_BTC, "CONTRACT_0.01_BTC"
            else:
                qty, unit = size * OKX_USD_CT_VAL_USD / price, "CONTRACT_100_USD"
            # A sell liquidation order closes a long (net or long position mode).
            ev = _liq("okx", inst, _finite(d.get("ts")),
                      "LONG_LIQUIDATED" if side == "sell" else "SHORT_LIQUIDATED", side.upper(),
                      qty, price, "EXCHANGE_SAMPLED", d.get("sz"), unit)
            if ev:
                out.append(ev)
    return out


PARSERS = {
    "coinbase": parse_coinbase,
    "binance_spot": parse_binance_spot,
    "liq_binance": parse_binance_liq,
    "liq_bybit": parse_bybit_liq,
    "liq_okx": parse_okx_liq,
}


# --------------------------------------------------------------------------
# REST snapshot parsers -> normalized derivatives fields
# --------------------------------------------------------------------------
def parse_rest(venue: str, payloads: Mapping[str, Any]) -> dict:
    """Normalize the raw REST payloads of one venue; missing values stay None."""
    out = {"funding_rate": None, "predicted_funding_rate": None, "next_funding_ms": None,
           "mark": None, "index": None, "oi_btc": None}
    try:
        if venue == "binance":
            p = payloads.get("premium") or {}
            o = payloads.get("oi") or {}
            # premiumIndex.lastFundingRate is the rate that settles at nextFundingTime.
            out.update(predicted_funding_rate=_finite(p.get("lastFundingRate")),
                       next_funding_ms=_finite(p.get("nextFundingTime")),
                       mark=_finite(p.get("markPrice")), index=_finite(p.get("indexPrice")),
                       oi_btc=_finite(o.get("openInterest")))
            out["funding_rate"] = out["predicted_funding_rate"]
        elif venue == "bybit":
            items = ((payloads.get("ticker") or {}).get("result") or {}).get("list") or []
            t = items[0] if items else {}
            out.update(predicted_funding_rate=_finite(t.get("fundingRate")),
                       next_funding_ms=_finite(t.get("nextFundingTime")),
                       mark=_finite(t.get("markPrice")), index=_finite(t.get("indexPrice")),
                       oi_btc=_finite(t.get("openInterest")))
            out["funding_rate"] = out["predicted_funding_rate"]
        elif venue == "okx":
            def first(key):
                data = (payloads.get(key) or {}).get("data") or []
                return data[0] if data and isinstance(data[0], Mapping) else {}
            f, o, m, i = first("funding"), first("oi"), first("mark"), first("index")
            out.update(funding_rate=_finite(f.get("fundingRate")),
                       predicted_funding_rate=_finite(f.get("nextFundingRate")) or _finite(f.get("fundingRate")),
                       next_funding_ms=_finite(f.get("fundingTime")),
                       mark=_finite(m.get("markPx")), index=_finite(i.get("idxPx")),
                       oi_btc=_finite(o.get("oiCcy")))
        elif venue == "bitfinex":
            rows = payloads.get("status") or []
            r = rows[0] if rows and isinstance(rows[0], list) else []

            def at(idx):
                return _finite(r[idx]) if len(r) > idx else None
            # status/deriv: [KEY, MTS, _, DERIV_PRICE, SPOT_PRICE, _, INSURANCE, _,
            #  NEXT_FUNDING_EVT_MTS, NEXT_FUNDING_ACCRUED, NEXT_FUNDING_STEP, _,
            #  CURRENT_FUNDING, _, _, MARK_PRICE, _, _, OPEN_INTEREST, ...]
            out.update(funding_rate=at(12), predicted_funding_rate=at(9),
                       next_funding_ms=at(8), mark=at(15), index=at(4), oi_btc=at(18))
            out["last_price"] = at(3)
    except (AttributeError, IndexError, TypeError):
        pass
    if out.get("mark") and out.get("index"):
        out["basis_bp"] = round((out["mark"] / out["index"] - 1.0) * 1e4, 3)
    else:
        out["basis_bp"] = None
    return out


def deriv_deltas(cur: Mapping[str, Any], prev: Optional[Mapping[str, Any]]) -> dict:
    """Minute deltas; null when the previous snapshot is missing, failed or too old."""
    keys = ("oi_delta_btc", "oi_delta_pct", "funding_delta", "predicted_funding_delta", "basis_delta_bp")
    out = {k: None for k in keys}
    if (not prev or prev.get("status") != "OK" or cur.get("status") != "OK"
            or not prev.get("fetched_ts") or not cur.get("fetched_ts")
            or cur["fetched_ts"] - prev["fetched_ts"] > DERIV_STALE_SEC):
        return out

    def diff(a, b, nd=10):
        return None if a is None or b is None else round(a - b, nd)
    out["oi_delta_btc"] = diff(cur.get("oi_btc"), prev.get("oi_btc"), 4)
    if out["oi_delta_btc"] is not None and prev.get("oi_btc"):
        out["oi_delta_pct"] = round(out["oi_delta_btc"] / prev["oi_btc"] * 100.0, 5)
    out["funding_delta"] = diff(cur.get("funding_rate"), prev.get("funding_rate"))
    out["predicted_funding_delta"] = diff(cur.get("predicted_funding_rate"), prev.get("predicted_funding_rate"))
    out["basis_delta_bp"] = diff(cur.get("basis_bp"), prev.get("basis_bp"), 3)
    return out


# --------------------------------------------------------------------------
# Routing sink and liquidation buffer (thread-safe)
# --------------------------------------------------------------------------
class FeedRouter:
    """Dispatches routed parser events to per-route sinks with ``on_events``."""

    def __init__(self, routes: Mapping[str, Any]) -> None:
        self.routes = dict(routes)
        self.msgs = 0
        self.last_msg_ts = None
        self.last_keepalive_ts = None

    def on_keepalive(self, recv_ts: float) -> None:
        """A plain-text keepalive reply (e.g. OKX "pong"): the socket is alive, no market data."""
        self.last_keepalive_ts = recv_ts

    def on_events(self, routed: Iterable[tuple], recv_ts: float) -> None:
        self.msgs += 1
        self.last_msg_ts = recv_ts
        grouped: dict = {}
        for route, event in routed:
            grouped.setdefault(route, []).append(event)
        for route, events in grouped.items():
            sink = self.routes.get(route)
            if sink is not None:
                sink.on_events(events, recv_ts)


class LiquidationBuffer:
    def __init__(self, venue: str) -> None:
        self.venue = venue
        self._lock = threading.Lock()
        self._pending: list = []
        self.msgs = 0
        self.events = 0
        self.last_msg_ts = None
        self.last_event_ts = None

    def on_events(self, events: Iterable[tuple], recv_ts: float) -> None:
        with self._lock:
            self.msgs += 1
            self.last_msg_ts = recv_ts
            for event in events:
                if event and event[0] == "liq":
                    self._pending.append({**event[1], "recv_ts": round(recv_ts, 3)})
                    self.events += 1
                    self.last_event_ts = recv_ts

    def drain(self) -> list:
        with self._lock:
            out, self._pending = self._pending, []
        return out


def liquidation_minute_summary(events: Iterable[Mapping[str, Any]], up_sec: int) -> dict:
    out = {"up_sec": int(up_sec), "long_n": 0, "short_n": 0, "long_btc": 0.0, "short_btc": 0.0,
           "long_usd": 0.0, "short_usd": 0.0, "max_event_usd": None}
    for e in events:
        side = "long" if e.get("liq_side") == "LONG_LIQUIDATED" else "short"
        out[f"{side}_n"] += 1
        out[f"{side}_btc"] += float(e.get("qty_btc") or 0.0)
        out[f"{side}_usd"] += float(e.get("notional_usd") or 0.0)
        usd = float(e.get("notional_usd") or 0.0)
        out["max_event_usd"] = usd if out["max_event_usd"] is None else max(out["max_event_usd"], usd)
    for k in ("long_btc", "short_btc"):
        out[k] = round(out[k], 6)
    for k in ("long_usd", "short_usd"):
        out[k] = round(out[k], 2)
    if up_sec <= 0:
        out["status"] = "FEED_DOWN"
    elif up_sec < 60:
        out["status"] = "PARTIAL"
    else:
        out["status"] = "OK"
    return out


# --------------------------------------------------------------------------
# Minute row encoding
# --------------------------------------------------------------------------
def _ref(values: list) -> Optional[float]:
    return next((v for v in values if v is not None), None)


def _offsets(values: list, ref: Optional[float], unit: float) -> list:
    return [None if v is None or ref is None else int(round((v - ref) / unit)) for v in values]


def _decode(ref: Optional[float], off: Optional[int], unit: float) -> Optional[float]:
    if ref is None or off is None:
        return None
    return float(ref) + int(off) * unit


def mask(flags: Iterable[Any]) -> str:
    return "".join("1" if f else "0" for f in flags)


def premium_series(num: list, den: list) -> list:
    out = []
    for a, b in zip(num, den):
        out.append(None if not a or not b else int(round((a / b - 1.0) * 1e4 / PREMIUM_UNIT_BP)))
    return out


def _mean(values: Iterable[Optional[float]], nd: int = 3) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return None if not vals else round(sum(vals) / len(vals), nd)


def encode_minute(minute_ts: int, spot_mids: Mapping[str, list], spot_up: Mapping[str, list], *,
                  bfx_mids: list, binance_perp_mids: list, usdt: Optional[Mapping[str, Any]] = None,
                  derivatives: Optional[Mapping[str, Any]] = None,
                  liquidations: Optional[Mapping[str, Any]] = None,
                  flags: Optional[Mapping[str, Any]] = None, regime: Optional[Mapping[str, Any]] = None,
                  meta: Optional[Mapping[str, Any]] = None) -> dict:
    minute_ts = int(minute_ts)
    row: dict = {
        "schema": SCHEMA,
        "minute_ts": minute_ts,
        "n": 60,
        "spot_price_unit": SPOT_PRICE_UNIT,
        "premium_unit_bp": PREMIUM_UNIT_BP,
        "spot": {},
    }
    for feed in SPOT_FEEDS:
        mids = list(spot_mids.get(feed) or [None] * 60)[:60]
        mids += [None] * (60 - len(mids))
        ups = list(spot_up.get(feed) or [False] * 60)[:60]
        ups += [False] * (60 - len(ups))
        mids = [m if u else None for m, u in zip(mids, ups)]
        ref = _ref(mids)
        row["spot"][feed] = {
            "symbol": FEEDS[feed]["symbol"],
            "m0": None if ref is None else round(ref, 3),
            "dm": _offsets(mids, ref, SPOT_PRICE_UNIT),
            "up": mask(ups),
            "up_sec": sum(1 for u in ups if u),
        }
    cb = [_decode(row["spot"]["coinbase"]["m0"], o, SPOT_PRICE_UNIT) for o in row["spot"]["coinbase"]["dm"]]
    bs = [_decode(row["spot"]["binance_spot"]["m0"], o, SPOT_PRICE_UNIT) for o in row["spot"]["binance_spot"]["dm"]]
    bfx = (list(bfx_mids) + [None] * 60)[:60]
    perp = (list(binance_perp_mids) + [None] * 60)[:60]
    prem = {
        "coinbase_vs_bfx": premium_series(cb, bfx),
        "coinbase_vs_binance_spot": premium_series(cb, bs),
        "coinbase_vs_binance_perp": premium_series(cb, perp),
        "binance_spot_vs_bfx": premium_series(bs, bfx),
    }
    row["premium"] = prem
    row["premium_bp_mean"] = {k: _mean([None if v is None else v * PREMIUM_UNIT_BP for v in vals])
                              for k, vals in prem.items()}
    row["ref_up_sec"] = {"bfx": sum(1 for v in bfx if v), "binance_perp": sum(1 for v in perp if v)}
    if usdt is not None:
        row["usdt_usd"] = dict(usdt)
    row["derivatives"] = {k: dict(v) for k, v in (derivatives or {}).items()}
    row["liquidations"] = {k: dict(v) for k, v in (liquidations or {}).items()}
    if flags is not None:
        row["flags"] = dict(flags)
    if regime is not None:
        row["regime"] = dict(regime)
    if meta is not None:
        row["meta"] = dict(meta)
    return row


def decode_minute(row: Mapping[str, Any]) -> dict:
    """{"spot": {feed: {sec: mid|None}}, "up": {feed: {sec: bool}}, "premium": {name: {sec: bp}}}."""
    if not isinstance(row, Mapping) or row.get("schema") != SCHEMA:
        return {}
    t0 = int(row.get("minute_ts") or 0)
    unit = _finite(row.get("spot_price_unit")) or SPOT_PRICE_UNIT
    punit = _finite(row.get("premium_unit_bp")) or PREMIUM_UNIT_BP
    out: dict = {"spot": {}, "up": {}, "premium": {}}
    for feed, cell in (row.get("spot") or {}).items():
        dms = cell.get("dm") or []
        up = str(cell.get("up") or "")
        out["spot"][feed] = {t0 + i: _decode(cell.get("m0"), dms[i] if i < len(dms) else None, unit)
                             for i in range(60)}
        out["up"][feed] = {t0 + i: (i < len(up) and up[i] == "1") for i in range(60)}
    for name, vals in (row.get("premium") or {}).items():
        out["premium"][name] = {t0 + i: (None if v is None else v * punit) for i, v in enumerate(vals or [])}
    return out


# --------------------------------------------------------------------------
# Health (collector live file -> bot status / monitors). Never gates orders.
# --------------------------------------------------------------------------
def health_from_live(live: Optional[Mapping[str, Any]], now: float, *, enabled: bool = True) -> dict:
    out = {"schema": HEALTH_SCHEMA, "enabled": bool(enabled), "status": "DISABLED", "stale_feeds": [],
           "age_sec": None, "feeds": {}, "derivatives_status": {}, "affects_orders": False}
    if not enabled:
        return out
    if not isinstance(live, Mapping) or live.get("schema") != LIVE_SCHEMA:
        out["status"] = "COLLECTOR_DOWN"
        return out
    age = now - float(live.get("written_ts") or 0.0)
    out["age_sec"] = round(age, 1)
    if isinstance(live.get("regime"), Mapping):
        out["regime"] = dict(live["regime"])
    if age > COLLECTOR_DOWN_SEC:
        out["status"] = "COLLECTOR_DOWN"
        return out
    stale = []
    for name, f in (live.get("feeds") or {}).items():
        last = f.get("last_msg_ts")
        fage = None if last is None else round(now - float(last), 1)
        # Liquidations are sparse: minutes without one is normal, so a keepalive
        # round-trip also proves the socket alive. Market-data feeds still need data.
        alive = last
        if name in LIQ_FEED_VENUE and f.get("last_keepalive_ts") is not None:
            alive = max(float(last or 0.0), float(f["last_keepalive_ts"]))
        alive_age = None if alive is None else round(now - float(alive), 1)
        ok = bool(f.get("connected")) and alive_age is not None and alive_age <= FEED_STALE_SEC
        out["feeds"][name] = {"connected": bool(f.get("connected")), "age_sec": fage,
                              "alive_age_sec": alive_age, "reconnects": f.get("reconnects"), "ok": ok}
        if not ok:
            stale.append(name)
    for venue, d in (live.get("derivatives") or {}).items():
        fetched = d.get("fetched_ts")
        status = d.get("status") or "MISSING"
        if status == "OK" and (fetched is None or now - float(fetched) > DERIV_STALE_SEC):
            status = "STALE"
        out["derivatives_status"][venue] = status
        if status != "OK":
            stale.append(f"deriv_{venue}")
    out["stale_feeds"] = sorted(stale)
    out["status"] = "OK" if not stale else "DEGRADED"
    return out
