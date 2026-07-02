#!/usr/bin/env python3
"""Bitfinex live execution bridge for the showcase bot.

This module is the single place where the showcase bot talks to Bitfinex's
PRIVATE (trading) API for live order placement, fill ingestion, manual-close
reconciliation, and post-restart state rebuild.

`bot.py` imports it lazily in these spots:
  - `_bitfinex_live_active()`         -> is_enabled(state)
  - `_maybe_bitfinex_limit_entry()`   -> submit_limit_entry(...)
  - `_maybe_bitfinex_market_entry()`  -> submit_market_entry(...)
  - `_maybe_bitfinex_close()`         -> submit_market_close(...)
  - `_maybe_bitfinex_cancel()`        -> cancel_exchange_order(...)
  - `/api/bitfinex_live` toggle       -> live_status(state, keys_ok)
  - main() startup                    -> configure(SYMBOL_CCXT)

Design rules (kept in sync with the workspace architecture lock):
  - NEVER crash the bot. Every public call is wrapped and logs a warning on failure.
  - The bot's in-memory `state` is the single source of truth; this module only
    mirrors orders/fills/positions to/from Bitfinex so the bot's snapshot stays
    accurate. No PnL / win-rate is calculated here.
  - All exchange calls go through the bot's `_exchange_call_with_retry` wrapper
    so rate limits and transient network errors are handled uniformly.
  - A small JSON sidecar (`bitfinex_live_state.json`) records the last seen
    exchange order/position/trade timestamps so we can rebuild after restart.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import urllib.request as _urllib_request
    import urllib.error as _urllib_error
except Exception:  # pragma: no cover
    _urllib_request = None
    _urllib_error = None

try:
    import ccxt  # type: ignore
except Exception:  # pragma: no cover - bot already hard-depends on ccxt
    ccxt = None

try:
    import logging
    _logger = logging.getLogger("bitfinex-live")
    if not _logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s"))
        _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)
except Exception:
    _logger = None


def _log(level: str, msg: str) -> None:
    if _logger is not None:
        getattr(_logger, level, _logger.info)(msg)


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_TRADE_SYMBOL: str = "BTC/USDT:USDT"
_SIDECAR = Path(__file__).resolve().parent / "bitfinex_live_state.json"
_STATE = {
    "configured": False,
    "last_submit_ts": 0.0,
    "last_submit_ok": None,
    "last_fill_ts": 0.0,
    "last_reconcile_ts": 0.0,
    "last_reconcile_drift": None,
    "last_rebuild_ts": 0.0,
    "open_order_ids": [],
}


# ---------------------------------------------------------------------------
# DDollar go-live gate
# ---------------------------------------------------------------------------
# Safety contract (per operator directive): arming live (`bitfinex_live_enabled`
# = true) is allowed, but NO market/limit ENTRY order may be placed unless the
# platform account holds >= DDOLLAR_GATE_MIN (default 2000) DDollar. This makes
# it safe to flip the live toggle on for an underfunded account — the bot stays
# in read-only / observe mode and simply logs that the gate blocked the order.
#
# The DDollar balance is READ-ONLY here (never mutated). It is fetched from the
# platform API via `DDOLLAR_GATE_URL` (bearer auth via `DDOLLAR_GATE_TOKEN`),
# expected to return JSON like {"ddollar": <number>} or {"balance": <number>}.
# The result is cached for DDOLLAR_GATE_TTL_SEC seconds.
#
# Fail-safe: if the gate cannot CONFIRM balance >= min (URL unset, fetch error,
# parse error, or balance below min), the entry is REFUSED. Close/cancel are
# never gated (they only reduce risk). This intentionally errs toward blocking.
_DDOLLAR_GATE_MIN_DEFAULT = 2000.0
_DDOLLAR_GATE_TTL_SEC = 60.0
_DDOLLAR_CACHE = {"ts": 0.0, "balance": None, "reason": ""}


def _ddollar_gate_min() -> float:
    try:
        return float(os.environ.get("DDOLLAR_GATE_MIN", _DDOLLAR_GATE_MIN_DEFAULT))
    except Exception:
        return _DDOLLAR_GATE_MIN_DEFAULT


def _ddollar_gate_enabled() -> bool:
    """The gate is active whenever live is armed. Unset URL => block by default."""
    return True


def _fetch_ddollar_balance() -> tuple[float | None, str]:
    """Return (balance, reason). balance is None when it could not be confirmed."""
    url = (os.environ.get("DDOLLAR_GATE_URL") or "").strip()
    if not url:
        return None, "DDOLLAR_GATE_URL unset — gate cannot confirm balance"
    if _urllib_request is None:
        return None, "urllib unavailable"
    try:
        req = _urllib_request.Request(url, headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {os.environ.get('DDOLLAR_GATE_TOKEN')}"}
               if os.environ.get("DDOLLAR_GATE_TOKEN") else {}),
        })
        with _urllib_request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        bal = None
        if isinstance(data, dict):
            for key in ("ddollar", "balance", "ddollarBalance", "points"):
                if isinstance(data.get(key), (int, float)):
                    bal = float(data[key])
                    break
        if bal is None:
            return None, "DDollar balance field missing in response"
        return bal, "ok"
    except Exception as exc:
        return None, f"DDollar fetch failed: {exc}"


def ddollar_gate_status() -> dict:
    """Read-only status for /api/bitfinex_live and logs. Never places orders."""
    now = time.time()
    if now - _DDOLLAR_CACHE["ts"] > _DDOLLAR_GATE_TTL_SEC:
        bal, reason = _fetch_ddollar_balance()
        _DDOLLAR_CACHE["ts"] = now
        _DDOLLAR_CACHE["balance"] = bal
        _DDOLLAR_CACHE["reason"] = reason
    bal = _DDOLLAR_CACHE["balance"]
    minimum = _ddollar_gate_min()
    passed = bal is not None and bal >= minimum
    return {
        "enabled": _ddollar_gate_enabled(),
        "minimum": minimum,
        "balance": bal,
        "passed": passed,
        "reason": _DDOLLAR_CACHE["reason"] if not passed else "ok",
        "url_configured": bool((os.environ.get("DDOLLAR_GATE_URL") or "").strip()),
    }


def _ddollar_gate_ok_for_entry() -> tuple[bool, str]:
    """Gate check for entry order paths. Returns (allowed, reason)."""
    status = ddollar_gate_status()
    if status["passed"]:
        return True, "ok"
    return False, status["reason"] or f"DDollar balance {status['balance']} < min {status['minimum']}"


def _persist() -> None:
    try:
        _SIDECAR.write_text(json.dumps(_STATE, default=str), encoding="utf-8")
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] sidecar persist failed: {exc}")


def _load_sidecar() -> None:
    global _STATE
    try:
        if _SIDECAR.is_file():
            saved = json.loads(_SIDECAR.read_text(encoding="utf-8") or "{}")
            if isinstance(saved, dict):
                for k, v in saved.items():
                    _STATE[k] = v
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] sidecar load failed: {exc}")


_load_sidecar()


# ---------------------------------------------------------------------------
# Configuration / enablement
# ---------------------------------------------------------------------------
def configure(symbol: str) -> None:
    """Set the trading symbol used for live orders (called from bot.main())."""
    global _TRADE_SYMBOL
    _TRADE_SYMBOL = str(symbol or _TRADE_SYMBOL)
    _STATE["configured"] = True
    _persist()
    _log("info", f"[BITFINEX LIVE] configured symbol={_TRADE_SYMBOL}")


def is_enabled(state: dict) -> bool:
    """True only when the operator has armed live execution on Bitfinex."""
    try:
        return bool((state or {}).get("bitfinex_live_enabled", False))
    except Exception:
        return False


def _dir_to_side(direction: str) -> str:
    d = (direction or "").strip().upper()
    if d in ("LONG", "BUY", "B"):
        return "buy"
    if d in ("SHORT", "SELL", "S"):
        return "sell"
    raise ValueError(f"unknown direction: {direction!r}")


def _amount(qty: float) -> float:
    q = float(qty or 0)
    if q <= 0:
        raise ValueError(f"non-positive qty: {qty}")
    return q


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------
def submit_market_entry(exchange, retry_fn, symbol: str, direction: str,
                        qty: float, leverage: int, trade_id: str) -> dict | None:
    """Open a position at market. Returns the exchange order dict or None."""
    if exchange is None or retry_fn is None:
        return None
    allowed, reason = _ddollar_gate_ok_for_entry()
    if not allowed:
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = False
        _persist()
        _log("warning", f"[BITFINEX LIVE] market entry BLOCKED by DDollar gate tid={trade_id}: {reason}")
        return None
    try:
        side = _dir_to_side(direction)
        amount = _amount(qty)
        params = {"leveraged": True, "type": "MARKET"}
        if trade_id:
            params["clientOrderId"] = str(trade_id)
        order = retry_fn(
            lambda: exchange.create_order(symbol or _TRADE_SYMBOL, "market", side, amount, None, params),
            label="BITFINEX_LIVE_MARKET_ENTRY",
        )
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = True
        _persist()
        _log("info", f"[BITFINEX LIVE] market entry {side} {amount} {symbol} tid={trade_id} -> id={_oid(order)}")
        return order
    except Exception as exc:
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = False
        _persist()
        _log("warning", f"[BITFINEX LIVE] market entry failed tid={trade_id}: {exc}")
        return None


def submit_limit_entry(exchange, retry_fn, symbol: str, direction: str,
                       qty: float, price: float, leverage: int, trade_id: str) -> str | None:
    """Place a leveraged limit order. Returns the exchange order id (string) or None."""
    if exchange is None or retry_fn is None:
        return None
    allowed, reason = _ddollar_gate_ok_for_entry()
    if not allowed:
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = False
        _persist()
        _log("warning", f"[BITFINEX LIVE] limit entry BLOCKED by DDollar gate tid={trade_id}: {reason}")
        return None
    try:
        side = _dir_to_side(direction)
        amount = _amount(qty)
        px = float(price or 0)
        if px <= 0:
            raise ValueError("limit price must be > 0")
        params = {"leveraged": True, "type": "LIMIT"}
        if trade_id:
            params["clientOrderId"] = str(trade_id)
        order = retry_fn(
            lambda: exchange.create_order(symbol or _TRADE_SYMBOL, "limit", side, amount, px, params),
            label="BITFINEX_LIVE_LIMIT_ENTRY",
        )
        oid = _oid(order)
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = True
        if oid:
            _STATE["open_order_ids"] = list(dict.fromkeys([str(oid)] + list(_STATE.get("open_order_ids", []))))[:50]
        _persist()
        _log("info", f"[BITFINEX LIVE] limit entry {side} {amount} @ {px} {symbol} tid={trade_id} -> id={oid}")
        return str(oid) if oid else None
    except Exception as exc:
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = False
        _persist()
        _log("warning", f"[BITFINEX LIVE] limit entry failed tid={trade_id}: {exc}")
        return None


def submit_market_close(exchange, retry_fn, symbol: str, direction: str,
                        qty: float, leverage: int, trade_id: str, exit_reason: str = "") -> dict | None:
    """Close a position with a market order opposite to the open direction."""
    if exchange is None or retry_fn is None:
        return None
    try:
        # Closing a LONG = sell; closing a SHORT = buy.
        close_dir = "SHORT" if _dir_to_side(direction) == "buy" else "LONG"
        side = _dir_to_side(close_dir)
        amount = _amount(qty)
        params = {"leveraged": True, "type": "MARKET", "reduceOnly": True}
        order = retry_fn(
            lambda: exchange.create_order(symbol or _TRADE_SYMBOL, "market", side, amount, None, params),
            label="BITFINEX_LIVE_MARKET_CLOSE",
        )
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = True
        _persist()
        _log("info", f"[BITFINEX LIVE] market close {side} {amount} {symbol} tid={trade_id} reason={exit_reason} -> id={_oid(order)}")
        return order
    except Exception as exc:
        _STATE["last_submit_ts"] = time.time()
        _STATE["last_submit_ok"] = False
        _persist()
        _log("warning", f"[BITFINEX LIVE] market close failed tid={trade_id}: {exc}")
        return None


def cancel_exchange_order(exchange, retry_fn, order_id: str, symbol: str, trade_id: str) -> bool:
    """Cancel a live order by id. Returns True on success."""
    if exchange is None or retry_fn is None or not order_id:
        return False
    try:
        retry_fn(
            lambda: exchange.cancel_order(str(order_id), symbol or _TRADE_SYMBOL),
            label="BITFINEX_LIVE_CANCEL",
        )
        ids = [x for x in _STATE.get("open_order_ids", []) if str(x) != str(order_id)]
        _STATE["open_order_ids"] = ids
        _persist()
        _log("info", f"[BITFINEX LIVE] cancelled id={order_id} tid={trade_id}")
        return True
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] cancel failed id={order_id} tid={trade_id}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Reads: fills, open orders, positions (for reconciliation + rebuild)
# ---------------------------------------------------------------------------
def fetch_open_orders(exchange, retry_fn, symbol: str | None = None) -> list:
    if exchange is None or retry_fn is None:
        return []
    try:
        return retry_fn(
            lambda: exchange.fetch_open_orders(symbol or _TRADE_SYMBOL),
            label="BITFINEX_LIVE_OPEN_ORDERS",
        ) or []
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] fetch_open_orders failed: {exc}")
        return []


def fetch_positions(exchange, retry_fn, symbol: str | None = None) -> list:
    if exchange is None or retry_fn is None:
        return []
    try:
        return retry_fn(
            lambda: exchange.fetch_positions([symbol or _TRADE_SYMBOL]),
            label="BITFINEX_LIVE_POSITIONS",
        ) or []
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] fetch_positions failed: {exc}")
        return []


def fetch_my_trades(exchange, retry_fn, symbol: str | None = None, since: int | None = None) -> list:
    if exchange is None or retry_fn is None:
        return []
    try:
        params = {}
        if since:
            params["since"] = int(since)
        return retry_fn(
            lambda: exchange.fetch_my_trades(symbol or _TRADE_SYMBOL, since=since, params=params),
            label="BITFINEX_LIVE_MY_TRADES",
        ) or []
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] fetch_my_trades failed: {exc}")
        return []


def fetch_balance(exchange, retry_fn) -> dict:
    if exchange is None or retry_fn is None:
        return {}
    try:
        return retry_fn(lambda: exchange.fetch_balance(), label="BITFINEX_LIVE_BALANCE") or {}
    except Exception as exc:
        _log("warning", f"[BITFINEX LIVE] fetch_balance failed: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Reconciliation + rebuild
# ---------------------------------------------------------------------------
def reconcile_exchange_state(exchange, retry_fn, symbol: str | None = None,
                             local_positions: list | None = None,
                             local_orders: list | None = None) -> dict:
    """Compare exchange truth against the bot's local view and report drift.

    Drift types we detect:
      - MANUAL_CLOSE: exchange has no position for a trade_id the bot still shows OPEN
      - UNEXPECTED_POSITION: exchange has a position the bot does not know about
      - ORDER_GONE: a live order id is no longer in exchange.fetch_open_orders
      - NEW_FILLS: trades on exchange newer than last_fill_ts the bot hasn't ingested
    """
    sym = symbol or _TRADE_SYMBOL
    ex_positions = fetch_positions(exchange, retry_fn, sym)
    ex_orders = fetch_open_orders(exchange, retry_fn, sym)
    since = int(_STATE.get("last_fill_ts", 0) * 1000) if _STATE.get("last_fill_ts") else None
    ex_trades = fetch_my_trades(exchange, retry_fn, sym, since=since)

    local_positions = local_positions or []
    local_orders = local_orders or []

    local_tids_open = {str(p.get("trade_id")) for p in local_positions if p.get("status") == "OPEN" or p.get("dir")}
    ex_tids = {str(getattr(p, "info", {}).get("symbol")) for p in ex_positions}

    manual_closes = [tid for tid in local_tids_open if tid and tid not in ex_tids]
    unexpected = [getattr(p, "info", {}).get("id") for p in ex_positions if str(getattr(p, "symbol", "")) == sym]

    known_oids = {str(o) for o in _STATE.get("open_order_ids", [])}
    live_oids = {str(getattr(o, "id", "")) for o in ex_orders}
    orders_gone = [oid for oid in known_oids if oid and oid not in live_oids]

    new_fills = []
    for t in ex_trades:
        ts = (getattr(t, "timestamp", None) or 0) / 1000.0
        if ts and ts > _STATE.get("last_fill_ts", 0):
            new_fills.append({"id": getattr(t, "id", ""), "ts": ts, "side": getattr(t, "side", ""),
                              "amount": getattr(t, "amount", None), "price": getattr(t, "price", None)})
            if ts > _STATE.get("last_fill_ts", 0):
                _STATE["last_fill_ts"] = ts

    drift = {
        "manual_closes": manual_closes,
        "unexpected_positions": unexpected,
        "orders_gone": orders_gone,
        "new_fills": new_fills,
        "exchange_position_count": len(ex_positions),
        "exchange_order_count": len(ex_orders),
        "local_open_count": len(local_tids_open),
    }
    _STATE["last_reconcile_ts"] = time.time()
    _STATE["last_reconcile_drift"] = drift
    _persist()
    _log("info", f"[BITFINEX LIVE] reconcile drift={drift}")
    return drift


def rebuild_state_from_exchange(exchange, retry_fn, symbol: str | None = None) -> dict:
    """After a restart, snapshot exchange truth so the bot can adopt it.

    Returns a dict with positions/orders/trades/balance the bot can fold into
    its in-memory state. The bot remains the source of truth for PnL/win-rate;
    this only seeds positions + open orders + recent fills + balance.
    """
    sym = symbol or _TRADE_SYMBOL
    payload = {
        "positions": [_pos_view(p) for p in fetch_positions(exchange, retry_fn, sym)],
        "open_orders": [_order_view(o) for o in fetch_open_orders(exchange, retry_fn, sym)],
        "recent_trades": [_trade_view(t) for t in fetch_my_trades(exchange, retry_fn, sym, since=int((time.time() - 86400) * 1000))],
        "balance": _balance_view(fetch_balance(exchange, retry_fn)),
        "rebuilt_at": time.time(),
    }
    _STATE["last_rebuild_ts"] = time.time()
    _persist()
    _log("info", f"[BITFINEX LIVE] rebuild positions={len(payload['positions'])} orders={len(payload['open_orders'])} trades={len(payload['recent_trades'])}")
    return payload


# ---------------------------------------------------------------------------
# Status (consumed by /api/bitfinex_live and State Integrity)
# ---------------------------------------------------------------------------
def live_status(state: dict, keys_ok: bool) -> dict:
    return {
        "wired": _STATE.get("configured", False),
        "enabled": is_enabled(state),
        "keys_ok": bool(keys_ok),
        "symbol": _TRADE_SYMBOL,
        "ddollar_gate": ddollar_gate_status(),
        "last_submit_sec_ago": (time.time() - _STATE["last_submit_ts"]) if _STATE.get("last_submit_ts") else None,
        "last_submit_ok": _STATE.get("last_submit_ok"),
        "last_fill_sec_ago": (time.time() - _STATE["last_fill_ts"]) if _STATE.get("last_fill_ts") else None,
        "last_reconcile_sec_ago": (time.time() - _STATE["last_reconcile_ts"]) if _STATE.get("last_reconcile_ts") else None,
        "last_rebuild_sec_ago": (time.time() - _STATE["last_rebuild_ts"]) if _STATE.get("last_rebuild_ts") else None,
        "open_order_ids": _STATE.get("open_order_ids", []),
        "sidecar": str(_SIDECAR.name),
    }


# ---------------------------------------------------------------------------
# View helpers
# ---------------------------------------------------------------------------
def _oid(order) -> str | None:
    if order is None:
        return None
    return getattr(order, "id", None) or (order.get("id") if isinstance(order, dict) else None)


def _pos_view(p):
    info = getattr(p, "info", {}) or {}
    return {
        "id": getattr(p, "id", None),
        "symbol": getattr(p, "symbol", None),
        "side": getattr(p, "side", None),
        "contracts": getattr(p, "contracts", None),
        "entry": getattr(p, "entryPrice", None),
        "mark": getattr(p, "markPrice", None),
        "unrealized_pnl": getattr(p, "unrealizedPnl", None),
        "leverage": getattr(p, "leverage", None),
        "liquidation": getattr(p, "liquidationPrice", None),
        "info": info,
    }


def _order_view(o):
    # created_at + cid (clientOrderId) are captured so Phase 3 reconcile-adopt can
    # (a) compute real TTL age from the exchange creation timestamp and
    # (b) use clientOrderId as a second match key when attributing orphan orders
    # back to a bot trade_id. reconcile_exchange_state itself is unchanged.
    cid = getattr(o, "clientOrderId", None)
    if cid is None and isinstance(o, dict):
        cid = o.get("clientOrderId") or (o.get("info") or {}).get("cid")
    return {
        "id": getattr(o, "id", None),
        "symbol": getattr(o, "symbol", None),
        "side": getattr(o, "side", None),
        "type": getattr(o, "type", None),
        "amount": getattr(o, "amount", None),
        "price": getattr(o, "price", None),
        "status": getattr(o, "status", None),
        "filled": getattr(o, "filled", None),
        "client_order_id": cid,
        "cid": cid,
        "created_at": getattr(o, "timestamp", None),
    }


def _trade_view(t):
    return {
        "id": getattr(t, "id", None),
        "symbol": getattr(t, "symbol", None),
        "side": getattr(t, "side", None),
        "amount": getattr(t, "amount", None),
        "price": getattr(t, "price", None),
        "timestamp": getattr(t, "timestamp", None),
        "fee": getattr(t, "fee", None),
    }


def _balance_view(b: dict) -> dict:
    if not isinstance(b, dict):
        return {}
    info = b.get("info") if isinstance(b.get("info"), dict) else {}
    total = b.get("total") if isinstance(b.get("total"), dict) else {}
    return {
        "total_usd": total.get("USDT") or total.get("USD"),
        "free_usd": (b.get("free") or {}).get("USDT") if isinstance(b.get("free"), dict) else None,
        "used_usd": (b.get("used") or {}).get("USDT") if isinstance(b.get("used"), dict) else None,
        "info_keys": list(info.keys())[:8] if isinstance(info, dict) else [],
    }
