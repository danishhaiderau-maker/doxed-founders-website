"""DISLOC / DISLOC-FOI -> signed live-copy lifecycle mapping (pure).

Danish 12 Oct 09:19: every dashboard tile is live-copyable through the same
signed copy path as GS-01. DISLOC self-simulates its paper orders, so bot.py
bridges each engine row through ``disloc_live_copy_events`` into
``_live_copy_publish`` (ORDER_PLACED -> POSITION_OPENED -> POSITION_CLOSED /
ORDER_CANCELLED). Entry is a marketable limit through the touch (taker,
IOC-like) and is only emitted while the signal is under 2 s old; the exit
mirrors the paper exit (60 s time exit or gap close).
"""
from __future__ import annotations

import math
import os
import time
from collections import deque

DISLOC_LIVE_MAX_SIGNAL_AGE_SEC = float(os.getenv("DISLOC_LIVE_MAX_SIGNAL_AGE_SEC", "2.0"))
# LIVE-FIX-1510 (Danish 09:06): the +5 bp cap is configurable. "off" = no price cap; the relay sends a
# true IOC at market (DISLOC_ENTRY_ORDER_MODE=IOC_MARKET) and the limit below is only a sizing/sanity
# reference (7 bp: inside the relay 10 bp limit-vs-mark sanity band).
_CUSHION_RAW = os.getenv("DISLOC_LIVE_TAKER_CUSHION_BP", "off").strip().lower()
DISLOC_LIVE_CAP_OFF = _CUSHION_RAW in ("off", "none", "")
DISLOC_LIVE_TAKER_CUSHION_BP = 7.0 if DISLOC_LIVE_CAP_OFF else float(_CUSHION_RAW)
DISLOC_LIVE_ENTRY_POLICY = "disloc_taker_marketable_limit_ioc_v1"
disloc_live_skips: deque = deque(maxlen=200)



def disloc_live_copy_events(row, now=None, touch=None, bot_version=None):
    """Map one DISLOC engine row to [(event, trade_id, extra)] live-copy lifecycle events. Pure."""
    if not isinstance(row, dict):
        return []
    ev = str(row.get("event") or "")
    tid = str(row.get("trade_id") or "")
    lane = str(row.get("research_lane") or "").upper()
    side = str(row.get("side") or "").upper()
    if not tid or side not in ("LONG", "SHORT") or not lane:
        return []
    if side == "SHORT" and str(row.get("side_tag") or "").startswith("SHORT_SHADOW"):
        return []
    now = time.time() if now is None else float(now)
    base = {"research_lane": lane, "direction": side, "signal_ts": row.get("signal_ts"),
            "margin_usdt": 0.25, "leverage": 100, "intent_source": "paper",
            "tile_engine": "disloc_engine", "bot_version": bot_version}
    if ev == "DISLOC_ORDER":
        if row.get("order_type") != "MARKET_TAKER_AT_REAL_TOUCH_AFTER_DELAY":
            return []
        try:
            age = now - float(row.get("signal_ts"))
        except (TypeError, ValueError):
            return []
        if age > DISLOC_LIVE_MAX_SIGNAL_AGE_SEC:
            disloc_live_skips.append({"trade_id": tid, "reason": "SIGNAL_TOO_OLD", "age_s": round(age, 3), "ts": now})
            return []
        touch_px = row.get("signal_ask") if side == "LONG" else row.get("signal_bid")
        try:
            touch_px = float(touch_px)
        except (TypeError, ValueError):
            return []
        if not touch_px > 0:
            return []
        cush = DISLOC_LIVE_TAKER_CUSHION_BP / 1e4
        limit = math.ceil(touch_px * (1 + cush)) if side == "LONG" else math.floor(touch_px * (1 - cush))
        return [("ORDER_PLACED", tid, {**base, "event_seq": 0, "signal_price": touch_px,
                 "limit_price": float(limit), "qty": 25.0 / float(limit),
                 "entry_limit_policy": DISLOC_LIVE_ENTRY_POLICY, "entry_order_semantics": "TAKER_IOC",
                 "entry_cap_mode": "OFF" if DISLOC_LIVE_CAP_OFF else "CAP_BP",
                 "entry_cap_bp": None if DISLOC_LIVE_CAP_OFF else DISLOC_LIVE_TAKER_CUSHION_BP,
                 "signal_touch_source": row.get("signal_touch_source"),
                 "signal_ask_age_ms": row.get("signal_ask_age_ms"),
                 "max_signal_age_sec": DISLOC_LIVE_MAX_SIGNAL_AGE_SEC, "signal_age_at_intent_s": round(age, 3),
                 "entry_reason": "DISLOC_SIGNAL", "executable": True})]
    if ev == "DISLOC_FILLED":
        px = row.get("fill_price")
        return [("POSITION_OPENED", tid, {**base, "fill_price": px, "qty": 25.0 / float(px)})] if px else []
    if ev == "DISLOC_CLOSED":
        return [("POSITION_CLOSED", tid, {**base, "exit_price": row.get("exit_price"),
                 "exit_reason": row.get("exit_reason"), "reason": row.get("exit_reason")})]
    if ev == "DISLOC_CANCELLED":
        return [("ORDER_CANCELLED", tid, {**base, "reason": str(row.get("cancel_reason") or "CANCELLED"),
                 "limit_price": row.get("limit_price") or row.get("signal_ask") or 0.0})]
    if ev == "DISLOC_ABANDONED":
        if row.get("fill_price"):
            bid, ask = (touch or (None, None))[:2]
            px = (bid if side == "LONG" else ask) or row.get("fill_price")
            return [("POSITION_CLOSED", tid, {**base, "exit_price": px, "exit_reason": "ABANDONED",
                     "reason": str(row.get("abandon_reason") or "ABANDONED")})]
        return [("ORDER_CANCELLED", tid, {**base, "reason": str(row.get("abandon_reason") or "ABANDONED"),
                 "limit_price": row.get("signal_ask") or 0.0})]
    return []


