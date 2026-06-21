#!/usr/bin/env python3
"""v103 execution funnel — live APPROVE→ORDER→TOUCH→FILL→CLOSE tracking + insight reports."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean

FUNNEL_FILE = "execution_funnel.jsonl"
FILL_QUALITY_FILE = "fill_quality_report.json"
APPROVAL_EV_FILE = "approval_ev_report.json"
FUNNEL_SUMMARY_FILE = "execution_funnel_summary.json"

_lock = threading.Lock()
_states: dict[str, dict] = {}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: str, row: dict) -> None:
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


def _distance_pct(market: float, target: float) -> float:
    if not market or not target or market <= 0:
        return 0.0
    return round(abs(market - target) / market * 100.0, 4)


def _side_touch_distance(order: dict, price: float) -> tuple[bool, float, float]:
    """Return touched, closest_approach_pct, missed_by_pct for limit order."""
    limit = float(order.get("limit_price") or order.get("planned_limit_price") or 0)
    if limit <= 0 or price <= 0:
        return False, 0.0, 0.0
    side = str(order.get("side") or "").lower()
    signal_dir = str(order.get("signal_dir") or "").upper()
    if side == "buy" or signal_dir == "LONG":
        min_p = float(order.get("min_price_since_order") or price)
        touched = min_p <= limit or price <= limit
        closest = _distance_pct(limit, min_p) if min_p > limit else 0.0
        missed = _distance_pct(limit, min_p) if min_p > limit else 0.0
        return touched, closest, missed
    max_p = float(order.get("max_price_since_order") or price)
    touched = max_p >= limit or price >= limit
    closest = _distance_pct(limit, max_p) if max_p < limit else 0.0
    missed = _distance_pct(limit, max_p) if max_p < limit else 0.0
    return touched, closest, missed


def funnel_on_approve(signal: dict, ai: dict = None) -> None:
    tid = str(signal.get("trade_id") or "")
    if not tid:
        return
    price = float(signal.get("signal_price") or signal.get("price") or 0)
    planner = signal.get("trade_planner") or (ai or {}).get("trade_planner") or {}
    limit = float(signal.get("limit_price") or 0)
    row = {
        "schema": "execution_funnel_v1",
        "ts": _utc_iso(),
        "trade_id": tid,
        "stage": "APPROVE",
        "approved": True,
        "research_lane": signal.get("research_lane"),
        "direction": signal.get("final_direction"),
        "entry_zone_low": planner.get("entry_zone_low") or signal.get("ai_entry_zone_low"),
        "entry_zone_high": planner.get("entry_zone_high") or signal.get("ai_entry_zone_high"),
        "planner_limit": limit or None,
        "entry_mode": signal.get("entry_mode"),
        "market_price": price,
        "distance_from_market_pct": _distance_pct(price, limit) if limit else None,
        "ai_win_prob": (ai or {}).get("win_prob"),
        "edge_score": signal.get("edge_score_at_entry") or signal.get("edge_score"),
    }
    with _lock:
        _states[tid] = {**row, "order_submitted": False, "filled": False, "closed": False}
    _append_jsonl(FUNNEL_FILE, row)


def funnel_on_order(signal: dict, order: dict) -> None:
    tid = str(order.get("trade_id") or signal.get("trade_id") or "")
    if not tid:
        return
    price = float(state_price(signal))
    limit = float(order.get("limit_price") or order.get("planned_limit_price") or 0)
    row = {
        "schema": "execution_funnel_v1",
        "ts": _utc_iso(),
        "trade_id": tid,
        "stage": "ORDER_SUBMITTED",
        "order_submitted": True,
        "order_accepted": True,
        "limit_price": limit,
        "planned_limit_price": order.get("planned_limit_price"),
        "entry_mode": order.get("entry_mode") or signal.get("entry_mode"),
        "signal_price": order.get("signal_price") or signal.get("signal_price"),
        "distance_from_market_pct": _distance_pct(price, limit) if limit and price else None,
        "ttl_sec": int(os.getenv("LIMIT_ORDER_MAX_AGE_SEC", str(120 * 60))),
    }
    with _lock:
        st = _states.setdefault(tid, {"trade_id": tid})
        st.update(row)
    _append_jsonl(FUNNEL_FILE, row)


def funnel_on_capacity_reject(signal: dict, reason: str = "MAX_ACTIVE_SIGNALS") -> None:
    tid = str(signal.get("trade_id") or "")
    if not tid:
        return
    row = {
        "schema": "execution_funnel_v1",
        "ts": _utc_iso(),
        "trade_id": tid,
        "stage": "CAPACITY_REJECTED",
        "order_submitted": False,
        "fill_reason": reason,
    }
    with _lock:
        _states.setdefault(tid, {})["terminal_reason"] = reason
    _append_jsonl(FUNNEL_FILE, row)


def funnel_on_expire(order: dict, reason: str = "TTL_EXPIRED") -> None:
    tid = str(order.get("trade_id") or "")
    if not tid:
        return
    now = time.time()
    created = float(order.get("created_ts") or now)
    price = float(order.get("last_market_price") or 0)
    touched, closest, missed = _side_touch_distance(order, price) if price else (False, 0.0, 0.0)
    row = {
        "schema": "execution_funnel_v1",
        "ts": _utc_iso(),
        "trade_id": tid,
        "stage": "ORDER_EXPIRED",
        "filled": False,
        "fill_reason": reason,
        "age_at_expire_sec": round(now - created, 1),
        "price_touched": touched,
        "closest_approach_pct": closest,
        "missed_by_pct": missed,
        "limit_price": order.get("limit_price"),
    }
    with _lock:
        st = _states.setdefault(tid, {"trade_id": tid})
        st["terminal_reason"] = reason
        st["filled"] = False
    _append_jsonl(FUNNEL_FILE, row)


def funnel_update_touch(order: dict, price: float) -> None:
    tid = str(order.get("trade_id") or "")
    if not tid or order.get("status") != "PENDING":
        return
    touched, closest, missed = _side_touch_distance(order, price)
    order["last_market_price"] = price
    with _lock:
        st = _states.get(tid)
        if not st:
            return
        prev_closest = float(st.get("closest_approach_pct") or 999)
        if closest < prev_closest:
            st["closest_approach_pct"] = closest
            st["missed_by_pct"] = missed
        if touched:
            st["price_touched"] = True


def funnel_on_fill(order: dict, fill_price: float = None) -> None:
    tid = str(order.get("trade_id") or "")
    if not tid:
        return
    row = {
        "schema": "execution_funnel_v1",
        "ts": _utc_iso(),
        "trade_id": tid,
        "stage": "FILLED",
        "filled": True,
        "fill_price": fill_price or order.get("fill_price") or order.get("limit_price"),
        "fill_delay_sec": round(time.time() - float(order.get("created_ts") or time.time()), 2),
        "entry_mode": order.get("entry_mode"),
    }
    with _lock:
        st = _states.setdefault(tid, {"trade_id": tid})
        st.update({"filled": True, "fill_reason": "FILLED"})
    _append_jsonl(FUNNEL_FILE, row)


def funnel_on_close(trade_id: str, exit_reason: str, net_pnl_usd: float = None, hold_sec: float = None) -> None:
    tid = str(trade_id or "")
    if not tid:
        return
    row = {
        "schema": "execution_funnel_v1",
        "ts": _utc_iso(),
        "trade_id": tid,
        "stage": "CLOSED",
        "closed": True,
        "exit_reason": exit_reason,
        "net_pnl_usd": net_pnl_usd,
        "hold_sec": hold_sec,
    }
    with _lock:
        st = _states.setdefault(tid, {"trade_id": tid})
        st["closed"] = True
        st["exit_reason"] = exit_reason
    _append_jsonl(FUNNEL_FILE, row)


def state_price(signal: dict) -> float:
    return float(signal.get("signal_price") or signal.get("price") or 0)


def _load_funnel_rows(cwd: str) -> list:
    path = os.path.join(cwd, FUNNEL_FILE)
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _load_jsonl(path: str) -> list:
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def build_funnel_summary(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    rows = _load_funnel_rows(cwd)
    by_tid: dict[str, dict] = {}
    for r in rows:
        tid = str(r.get("trade_id") or "")
        if not tid:
            continue
        st = by_tid.setdefault(tid, {"trade_id": tid})
        st.update({k: v for k, v in r.items() if v is not None})
    approves = sum(1 for s in by_tid.values() if s.get("approved") or s.get("stage") == "APPROVE")
    orders = sum(1 for s in by_tid.values() if s.get("order_submitted"))
    touched = sum(1 for s in by_tid.values() if s.get("price_touched"))
    filled = sum(1 for s in by_tid.values() if s.get("filled"))
    closed = sum(1 for s in by_tid.values() if s.get("closed"))
    terminals = Counter(s.get("terminal_reason") or s.get("fill_reason") for s in by_tid.values() if s.get("terminal_reason") or s.get("fill_reason"))
    summary = {
        "cwd": cwd,
        "generated_at": _utc_iso(),
        "unique_trade_ids": len(by_tid),
        "approve_count": approves,
        "order_submitted_count": orders,
        "price_touch_count": touched,
        "filled_count": filled,
        "closed_count": closed,
        "approval_to_order_rate_pct": round(100 * orders / approves, 1) if approves else 0,
        "order_to_fill_rate_pct": round(100 * filled / orders, 1) if orders else 0,
        "fill_to_close_rate_pct": round(100 * closed / filled, 1) if filled else 0,
        "approve_to_fill_rate_pct": round(100 * filled / approves, 1) if approves else 0,
        "terminal_reasons": dict(terminals),
        "unaccounted_approves": max(0, approves - filled - sum(1 for s in by_tid.values() if s.get("terminal_reason"))),
    }
    out = os.path.join(cwd, FUNNEL_SUMMARY_FILE)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary


def build_fill_quality_report(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    rows = _load_funnel_rows(cwd)
    buckets = Counter()
    distances = []
    missed = []
    for r in rows:
        reason = r.get("fill_reason") or r.get("terminal_reason") or ""
        if r.get("stage") == "ORDER_EXPIRED":
            touched = r.get("price_touched")
            if not touched and (r.get("closest_approach_pct") or 0) > 0.5:
                buckets["TOO_DEEP"] += 1
            elif touched:
                buckets["TOUCHED_NOT_FILLED"] += 1
            elif reason == "TTL_EXPIRED":
                buckets["TTL_EXPIRED"] += 1
            else:
                buckets[reason or "TTL_EXPIRED"] += 1
            if r.get("missed_by_pct") is not None:
                missed.append(float(r["missed_by_pct"]))
        elif r.get("stage") == "CAPACITY_REJECTED":
            buckets["CAPACITY_REJECTED"] += 1
        elif reason == "CAPACITY_REPLACED":
            buckets["QUEUE_REPLACED"] += 1
        elif r.get("stage") == "FILLED":
            buckets["FILLED"] += 1
        if r.get("distance_from_market_pct") is not None:
            distances.append(float(r["distance_from_market_pct"]))
    report = {
        "cwd": cwd,
        "generated_at": _utc_iso(),
        "buckets": dict(buckets),
        "avg_distance_from_market_pct": round(mean(distances), 4) if distances else 0,
        "avg_missed_by_pct": round(mean(missed), 4) if missed else 0,
        "ttl_expired_count": buckets.get("TTL_EXPIRED", 0),
        "capacity_rejected_count": buckets.get("CAPACITY_REJECTED", 0),
        "queue_replaced_count": buckets.get("QUEUE_REPLACED", 0),
        "filled_count": buckets.get("FILLED", 0),
    }
    out = os.path.join(cwd, FILL_QUALITY_FILE)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def build_approval_ev_report(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    shadows = {str(s.get("trade_id")): s for s in _load_jsonl(os.path.join(cwd, "shadow_outcome.jsonl"))}
    trades = {str(t.get("trade_id")): t for t in _load_csv_trades(os.path.join(cwd, "trades_3factor.csv"))}
    funnel = _load_funnel_rows(cwd)
    approve_ids = {str(r.get("trade_id")) for r in funnel if r.get("stage") == "APPROVE"}
    order_ids = {str(r.get("trade_id")) for r in funnel if r.get("stage") == "ORDER_SUBMITTED"}
    fill_ids = {str(r.get("trade_id")) for r in funnel if r.get("stage") == "FILLED"}
    close_ids = {str(r.get("trade_id")) for r in funnel if r.get("stage") == "CLOSED"}

    def _ev(ids):
        pnls = []
        for tid in ids:
            if tid in trades:
                pnls.append(float(trades[tid].get("net_pnl_usd") or 0))
            elif tid in shadows:
                pnls.append(float(shadows[tid].get("net_pnl_usd") or 0))
        n = len(pnls)
        return {
            "n": len(ids),
            "with_pnl_n": n,
            "sum_pnl_usd": round(sum(pnls), 2),
            "avg_pnl_usd": round(mean(pnls), 3) if pnls else 0,
            "win_rate_pct": round(100 * sum(1 for p in pnls if p > 0) / n, 1) if n else 0,
        }

    report = {
        "cwd": cwd,
        "generated_at": _utc_iso(),
        "AI_APPROVE_EV": _ev(approve_ids),
        "ORDER_PLACED_EV": _ev(order_ids),
        "FILLED_EV": _ev(fill_ids),
        "TRADED_EV": _ev(close_ids),
    }
    out = os.path.join(cwd, APPROVAL_EV_FILE)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def _load_csv_trades(path: str) -> list:
    if not os.path.isfile(path):
        return []
    import csv
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def refresh_all_execution_reports(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    return {
        "funnel_summary": build_funnel_summary(cwd),
        "fill_quality": build_fill_quality_report(cwd),
        "approval_ev": build_approval_ev_report(cwd),
    }


if __name__ == "__main__":
    import sys
    data = refresh_all_execution_reports(sys.argv[1] if len(sys.argv) > 1 else None)
    s = data["funnel_summary"]
    print(
        f"approves={s.get('approve_count')} orders={s.get('order_submitted_count')} "
        f"fills={s.get('filled_count')} approve_to_fill={s.get('approve_to_fill_rate_pct')}%"
    )
