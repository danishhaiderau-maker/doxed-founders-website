"""REALISM_V2 order-type model (pure functions, no per-tile code, never prices a fill).

Binding model (tracker "REALISM FIXES F1-F7", HM #807 a-h, Rules Keeper, steering 23 Oct):
- EXCHANGE_RESIDENT: every tile carries an exchange-resident stop at
  min(hard + 25 bp, liq - 15 bp) = live_copy_control.exchange_stop_plan(spec) (LIVE_CODE, relay master 51d926f93),
  anchored at the paper ENTRY FILL and never moved (APPROVED live copies never move it whatever
  EXCHANGE_DYNAMIC_STOPS_ENABLED says). It is sized to the open qty (resized on partials), races the paper hard stop
  and every in-flight bot exit, and fills as a delay-0 full book walk at its trigger instant.
- BOT_SENT: every other exit (TP, TP1, thesis, time, trail/lock, BE, paper hard stop, forced/admin) is a reduce-only
  MARKET book walk at trigger + delay. No maker exits, no booking at the target.
- Entries: WALK_THEN_REST (GS-01 / GS-06 / CTRL: walk to a 5 bp protection limit, remainder rests under maker rules
  until the 3 s TTL then cancels), MAKER_REST (B1 / B2 regime cells: rest 600-1200 s, repriced at the
  LIMIT_UPDATED cadence, queue reset on every reprice), IOC_MARKET (DISLOC; tiles 6/7 stay OFF).
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, Optional

EXCHANGE_RESIDENT = "EXCHANGE_RESIDENT"
BOT_SENT = "BOT_SENT"
PARAM_SOURCE_LIVE_CODE = "LIVE_CODE"
LIVE_CODE_REF = "relay master 51d926f93; live_copy_control.exchange_stop_plan"
EXCHANGE_STOP_REASON_PREFIX = "EXCHANGE_STOP_"
_EXCHANGE_REASON = re.compile(r"EXCHANGE_STOP_\d+(\.\d+)?BP")

ENTRY_WALK_THEN_REST = "WALK_THEN_REST"
ENTRY_MAKER_REST = "MAKER_REST"
ENTRY_IOC_MARKET = "IOC_MARKET"
NO_FILL = "NO_FILL"
DEFAULT_PROTECTION_BP = 5.0
DEFAULT_TAKER_TTL_S = 3.0
PARAM_SOURCE_CONFIG = "CONFIG"  # combo_pathway_config / regime_exec cell (same values the live executor reads)


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _dir(d: Any) -> Optional[str]:
    s = str(d or "").upper()
    return "LONG" if s in ("LONG", "BUY") else ("SHORT" if s in ("SHORT", "SELL") else None)


# ------------------------------------------------------------------ exits
def exchange_stop_reason(bp: float) -> str:
    return f"{EXCHANGE_STOP_REASON_PREFIX}{float(bp):g}BP"


def is_exchange_stop_reason(reason: Any) -> bool:
    return bool(_EXCHANGE_REASON.fullmatch(str(reason or "").upper()))


def exit_order_class(reason: Any) -> str:
    """EXCHANGE_RESIDENT only for the exchange stop; every other exit (incl. paper hard stop, forced, admin) is BOT_SENT."""
    return EXCHANGE_RESIDENT if is_exchange_stop_reason(reason) else BOT_SENT


def exchange_stop_level(direction: Any, entry_fill_px: Any, spec: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """The exchange-resident stop of one position: anchored at the paper entry fill, never moved."""
    import live_copy_control as _lcc
    d, px = _dir(direction), _f(entry_fill_px)
    if d is None or px is None or px <= 0:
        return None
    plan = _lcc.exchange_stop_plan(spec)
    if not plan:
        return None
    bp = float(plan["exchange_stop_bp"])
    level = px * (1 - bp / 1e4) if d == "LONG" else px * (1 + bp / 1e4)
    return {"exchange_stop_px": round(level, 8), "exchange_stop_bp": bp, "exchange_stop_anchor_px": px,
            "exchange_stop_hard_bp": plan.get("hard_stop_bp"), "exchange_stop_cap_applied": plan.get("cap_applied"),
            "exchange_stop_param_source": PARAM_SOURCE_LIVE_CODE, "exchange_stop_param_ref": LIVE_CODE_REF,
            "exchange_stop_never_moved": True}


def race(exchange_trigger_ts: Optional[float], bot_trigger_ts: Optional[float], bot_delay_s: float) -> Optional[str]:
    """Which order closes the position: the resident stop wins if it triggers at or before the bot order's fill
    time (bot trigger + delay); exact ties go to the resident stop (it is already on the exchange)."""
    e, b = _f(exchange_trigger_ts), _f(bot_trigger_ts)
    if e is None and b is None:
        return None
    if e is None:
        return BOT_SENT
    if b is None:
        return EXCHANGE_RESIDENT
    return EXCHANGE_RESIDENT if e <= b + max(0.0, float(bot_delay_s or 0)) else BOT_SENT


# ------------------------------------------------------------------ entries
def entry_model(spec: Optional[Mapping[str, Any]], decision: Optional[Mapping[str, Any]] = None,
                exec_cell: Optional[Mapping[str, Any]] = None) -> dict:
    """Data-driven entry order type from the tile spec + the regime exec cell (no per-tile code)."""
    e = (spec or {}).get("entry_policy") or (spec or {}).get("entry") or {}
    mode = str(e.get("mode") or "").upper()
    prot = _f(e.get("taker_protection_bps"))
    ttl = _f(e.get("taker_ttl_sec"))
    if "SELF_SIMULATED" in mode or "MARKET_AT_REAL_TOUCH" in mode:
        return {"entry_order_model": ENTRY_IOC_MARKET, "entry_param_source": PARAM_SOURCE_CONFIG}
    kind = str((exec_cell or {}).get("kind") or (decision or {}).get("exec_kind") or "").upper()
    if exec_cell and kind and kind != "TAKER":
        return {"entry_order_model": ENTRY_MAKER_REST, "entry_rest_ttl_s": _f(exec_cell.get("ttl_sec")),
                "entry_reprice_s": _f(exec_cell.get("reprice_sec")), "entry_queue_reset_on_reprice": True,
                "entry_exec_kind": kind, "entry_param_source": PARAM_SOURCE_CONFIG}
    return {"entry_order_model": ENTRY_WALK_THEN_REST,
            "entry_protection_bp": prot if prot is not None else DEFAULT_PROTECTION_BP,
            "entry_rest_ttl_s": ttl if ttl is not None else DEFAULT_TAKER_TTL_S,
            "entry_param_source": PARAM_SOURCE_CONFIG if (prot is not None and ttl is not None) else "DEFAULT"}


def queue_ahead_at(direction: Any, limit_px: Any, book: Optional[Mapping[str, Any]]) -> float:
    """Resting size already at our limit price on our (passive) side when we join / reprice."""
    d, lp = _dir(direction), _f(limit_px)
    if d is None or lp is None or not isinstance(book, Mapping):
        return 0.0
    side = book.get("b" if d == "LONG" else "a") or []
    tot = 0.0
    for lv in side:
        try:
            p, s = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(p - lp) <= 1e-9:
            tot += s
    return tot


def maker_rest_step(direction: Any, limit_px: Any, remaining: float, queue_ahead: float,
                    trades: Iterable[Mapping[str, Any]]) -> dict:
    """Advance a resting entry limit over trade prints (oldest first). A print THROUGH the limit (sell below a buy
    limit / buy above a sell limit) fills us (trade-through); a print AT the limit on the aggressor side first
    depletes the queue ahead, then fills us. Partials allowed. Returns fills [(ts, px, qty)], remaining, queue_ahead."""
    d, lp = _dir(direction), _f(limit_px)
    rem = max(0.0, float(remaining or 0))
    q = max(0.0, float(queue_ahead or 0))
    fills = []
    if d is None or lp is None or rem <= 0:
        return {"fills": fills, "remaining": rem, "queue_ahead": q}
    for tr in trades or ():
        px, ts, amt = _f(tr.get("px")), _f(tr.get("ts")), _f(tr.get("amt"))
        if px is None or ts is None or rem <= 0:
            continue
        size = abs(amt) if amt else 0.0
        # aggressor: Bitfinex signed amount, negative = sell aggressor (hits bids), positive = buy aggressor
        hits_us = (amt is None or amt < 0) if d == "LONG" else (amt is None or amt > 0)
        through = px < lp - 1e-9 if d == "LONG" else px > lp + 1e-9
        at = abs(px - lp) <= 1e-9
        if through:
            take = rem if not size else min(rem, size)
            fills.append((ts, lp, take))
            rem -= take
        elif at and hits_us and size > 0:
            if q >= size:
                q -= size
                continue
            avail = size - q
            q = 0.0
            take = min(rem, avail)
            fills.append((ts, lp, take))
            rem -= take
    return {"fills": fills, "remaining": round(rem, 12), "queue_ahead": q}


def vwap(fills: Iterable) -> tuple:
    q = sum(f[2] for f in fills)
    return (sum(f[1] * f[2] for f in fills) / q if q > 0 else None), q


def combine_entry(walk: Optional[Mapping[str, Any]], rest_fills: Iterable, requested_qty: float, *,
                  model: Mapping[str, Any], rest_end_ts: Optional[float], rest_end_reason: str) -> dict:
    """Final entry result = the book walk (paper_fill, may be empty) + maker rest fills at the limit (may be empty).
    FILLED / PARTIAL (unfilled qty recorded, the rest is cancelled) / NO_FILL. Walk metadata is kept."""
    walk = dict(walk or {})
    wq = _f(walk.get("filled_qty")) or 0.0
    wpx = _f(walk.get("price"))
    fills = [(float(t), float(p), float(q)) for t, p, q in (rest_fills or ()) if q and q > 0]
    if wq > 0 and wpx:
        fills_all = [(walk.get("fill_ts"), wpx, wq)] + fills
    else:
        wq, fills_all = 0.0, list(fills)
    px, q = vwap(fills_all)
    req = max(0.0, float(requested_qty or 0))
    unfilled = max(0.0, req - q)
    status = NO_FILL if q <= 0 else ("FILLED" if unfilled <= 1e-12 else "PARTIAL")
    out = {k: v for k, v in walk.items() if k not in ("price", "filled_qty", "status")}
    rest_q = sum(f[2] for f in fills)
    last_ts = max(f[0] for f in fills) if fills else None
    out.update({
        "status": status, "price": px, "filled_qty": q, "requested_qty": req,
        "entry_unfilled_qty": round(unfilled, 12), "entry_walk_status": walk.get("status"),
        "entry_walk_filled_qty": wq, "entry_walk_px": wpx if wq > 0 else None,
        "entry_rest_filled_qty": rest_q, "entry_rest_fills": [[t, p, qq] for t, p, qq in fills],
        "entry_rest_end_ts": rest_end_ts, "entry_rest_end_reason": rest_end_reason,
        **{k: v for k, v in model.items()},
    })
    if fills:
        out["walk_fill_ts"] = walk.get("fill_ts")
        out["fill_ts"] = last_ts
        out["entry_model_fill_ts"] = last_ts
        out["entry_rest_fill_px"] = vwap(fills)[0]
    else:
        out["entry_model_fill_ts"] = walk.get("fill_ts") if wq > 0 else None
    if status == NO_FILL:
        out["no_fill_reason"] = rest_end_reason
    return out
