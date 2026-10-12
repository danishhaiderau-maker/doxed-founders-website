"""LIVE-FIX-1491: pure builders for the HM monitor routes (read-only, monitor token).

/api/monitor/live/timeline        per-trade per-hop ms, paper vs live side by side + slippage
/api/monitor/live/pause-history   relay pauses with cause + owner; RED on any self-pause in 1 h
/api/monitor/live/protection      per open position: stop/TP/OCO counts + mirror age
/api/monitor/live/clock           Fly offset, Fly->Railway and Railway<->Bitfinex one-way bounds
/api/monitor/live/paths           delivery path used (push vs outbox) and dedupe hits
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Mapping

SCHEMA = "hm_live_routes_v1"
GREEN, AMBER, RED = "GREEN", "AMBER", "RED"
LIVE_STAGES = ("fly_signal_at_ts", "fly_intent_emitted_at_ts", "railway_received_at_ts", "order_sent_at_ts",
               "exchange_ack_at_ts", "fill_at_ts", "stop_entry_ack_at_ts", "stop_fill_detected_at_ts",
               "stop_claim_started_at_ts", "stop_claimed_at_ts", "stop_phase_submitting_at_ts",
               "stop_submit_started_at_ts", "stop_placed_at_ts", "stop_protected_at_ts", "fill_enriched_at_ts", "stop_confirmed_at_ts",
               "exit_preflight_at_ts", "exit_sent_at_ts", "exit_ack_at_ts", "exit_confirmed_at_ts",
               "exit_recorded_at_ts")


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ts(v) -> float | None:
    x = _f(v)
    if x is not None:
        return x
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _pct(vals: list, q: float):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def _ms(a, b):
    return None if a is None or b is None else round((b - a) * 1000.0)


def _bp(live, paper, side, *, exit_leg=False):
    live, paper = _f(live), _f(paper)
    if not live or not paper:
        return None
    sign = 1 if str(side).upper() == "LONG" else -1
    if exit_leg:
        sign = -sign  # selling lower than paper on a LONG exit is adverse
    return round(sign * (live - paper) / paper * 1e4, 2)  # + = adverse vs paper


def timeline(reports: Iterable[Mapping], approvals: Iterable[Mapping], twins: Mapping[str, Mapping],
             *, trade_id: str | None = None, limit: int = 50, since: float = 0.0) -> dict:
    by: dict[str, dict] = {}
    for a in approvals or []:
        tid = str(a.get("correlation_id") or a.get("trade_id") or "")
        if not tid or (trade_id and tid != trade_id):
            continue
        row = by.setdefault(tid, {"trade_id": tid, "lane": a.get("research_lane"), "live": {}, "events": []})
        row["live"].setdefault("fly_signal_at_ts", _f(a.get("signal_at_ts")))
        if a.get("event") in ("ORDER_PLACED", "APPROVE_PENDING"):
            row["live"].setdefault("fly_intent_emitted_at_ts", _f(a.get("created_at_ts")))
        if a.get("event") == "POSITION_CLOSED":
            row["live"]["fly_exit_intent_at_ts"] = _f(a.get("created_at_ts"))
    for r in reports or []:
        tid = str(r.get("correlation_id") or "")
        if not tid or (trade_id and tid != trade_id):
            continue
        row = by.setdefault(tid, {"trade_id": tid, "lane": r.get("lane"), "live": {}, "events": []})
        row["lane"] = row.get("lane") or r.get("lane")
        row["account"] = r.get("account")
        row["events"].append(r.get("type"))
        tl = r.get("timeline") if isinstance(r.get("timeline"), Mapping) else {}
        for k in LIVE_STAGES:
            v = _f(tl.get(k))
            if v is not None and row["live"].get(k) is None:
                row["live"][k] = v
        if r.get("type") == "ORDER_FILLED" and isinstance(r.get("entry_obs"), Mapping):
            row["entry_obs"] = dict(r["entry_obs"])
        if r.get("type") == "ORDER_FILLED":
            row["live_fill_price"] = _f((r.get("fill") or {}).get("price"))
            row["side"] = row.get("side") or (r.get("order") or {}).get("side")
        if r.get("type") == "POSITION_CLOSED":
            row["live_exit_price"] = _f((r.get("close") or {}).get("price"))
            row["live"].setdefault("exit_recorded_at_ts", _f(r.get("event_at_ts")))
        if r.get("type") in ("ORDER_PLACED", "ORDER_SENT") and not row.get("side"):
            row["side"] = (r.get("order") or {}).get("side")
    rows = []
    for tid, row in by.items():
        tw = (twins or {}).get(tid) or {}
        side = str(tw.get("side") or row.get("side") or "").upper()
        L = row["live"]
        paper = {"signal_ts": _ts(tw.get("signal_ts")), "fill_ts": _ts(tw.get("entry_ts")),
                 "exit_ts": _ts(tw.get("exit_ts")), "entry_price": _f(tw.get("entry_price")),
                 "exit_price": _f(tw.get("exit_price")), "exit_reason": tw.get("exit_reason"),
                 "signal_touch_price": _f(tw.get("signal_price"))}
        t0 = L.get("fly_signal_at_ts") or paper["signal_ts"]
        if t0 is None or t0 < since:
            continue
        hops = {k.replace("_at_ts", ""): _ms(t0, L.get(k)) for k in LIVE_STAGES + ("fly_exit_intent_at_ts",)}
        exit_end = L.get("exit_ack_at_ts") or L.get("exit_recorded_at_ts")
        rows.append({
            "trade_id": tid, "lane": row.get("lane"), "account": row.get("account"), "side": side or None,
            "t0_signal_ts": t0, "live_ms_from_signal": hops,
            "paper_ms_from_signal": {"fill": _ms(t0, paper["fill_ts"]), "exit": _ms(t0, paper["exit_ts"])},
            "entry": {"signal_to_send_ms": _ms(t0, L.get("order_sent_at_ts")),
                      "send_to_ack_ms": _ms(L.get("order_sent_at_ts"), L.get("exchange_ack_at_ts")),
                      "live_fill_vs_paper_fill_ms": _ms(paper["fill_ts"], L.get("fill_at_ts")),
                      "fill_to_stop_placed_ms": _ms(L.get("fill_at_ts"), L.get("stop_placed_at_ts")),
                      "fill_to_stop_confirmed_ms": _ms(L.get("fill_at_ts"), L.get("stop_confirmed_at_ts")),
                      # LIVE-FIX-1494: stop path split (executor clock for all four marks)
                      "stop_ack_to_confirmed_ms": _ms(L.get("stop_placed_at_ts"), L.get("stop_confirmed_at_ts")),
                      "entry_ack_to_fill_detected_ms": _ms(L.get("stop_entry_ack_at_ts"), L.get("stop_fill_detected_at_ts")),
                      "fill_detected_to_claimed_ms": _ms(L.get("stop_fill_detected_at_ts"), L.get("stop_claimed_at_ts")),
                      "claimed_to_stop_submit_ms": _ms(L.get("stop_claimed_at_ts"), L.get("stop_submit_started_at_ts")),
                      # LIVE-FIX-1499 (HM two-second audit): observability only
                      "claim_ms": _ms(L.get("stop_claim_started_at_ts"), L.get("stop_claimed_at_ts")),
                      "phase_submitting_write_ms": _ms(L.get("stop_claimed_at_ts"), L.get("stop_phase_submitting_at_ts")),
                      "stop_ack_to_protected_ms": _ms(L.get("stop_placed_at_ts"), L.get("stop_protected_at_ts")),
                      "protected_to_fill_enriched_ms": _ms(L.get("stop_protected_at_ts"), L.get("fill_enriched_at_ts")),
                      "fill_detect_source": (row.get("entry_obs") or {}).get("fill_detect_source"),
                      "fill_probe_index": (row.get("entry_obs") or {}).get("fill_probe_index"),
                      "fill_probe_marks": (row.get("entry_obs") or {}).get("fill_probe_marks"),
                      "take_submit_fill_status": (row.get("entry_obs") or {}).get("take_submit_fill_status"),
                      "fast_path": (row.get("entry_obs") or {}).get("fast_path"),
                      "marks": (row.get("entry_obs") or {}).get("marks"),
                      "stop_submit_to_ack_ms": _ms(L.get("stop_submit_started_at_ts"), L.get("stop_placed_at_ts")),
                      "entry_ack_to_stop_ack_ms": _ms(L.get("stop_entry_ack_at_ts"), L.get("stop_placed_at_ts")),
                      "slippage_bp_vs_paper": _bp(row.get("live_fill_price"), paper["entry_price"], side),
                      # LIVE-FIX-1498 (Strategist): live fill vs signal touch (ask LONG / bid SHORT), + = adverse
                      "slippage_bp_vs_signal_touch": _bp(row.get("live_fill_price"), paper["signal_touch_price"], side),
                      "paper_slippage_bp_vs_signal_touch": _bp(paper["entry_price"], paper["signal_touch_price"], side)},
            "exit": {"paper_exit_to_fly_intent_ms": _ms(paper["exit_ts"], L.get("fly_exit_intent_at_ts")),
                     "paper_exit_to_send_ms": _ms(paper["exit_ts"], L.get("exit_sent_at_ts")),
                     "send_to_ack_ms": _ms(L.get("exit_sent_at_ts"), L.get("exit_ack_at_ts")),
                     "paper_exit_to_live_exit_ms": _ms(paper["exit_ts"], exit_end),
                     "slippage_bp_vs_paper": _bp(row.get("live_exit_price"), paper["exit_price"], side, exit_leg=True),
                     "paper_exit_reason": paper["exit_reason"]},
            "paper": paper, "live_stamps": L, "events": row["events"][-20:],
        })
    rows.sort(key=lambda r: r["t0_signal_ts"])
    rows = rows[-max(1, int(limit)):]

    def roll(path):
        vals = [r[path[0]][path[1]] for r in rows if r[path[0]].get(path[1]) is not None]
        return {"n": len(vals), "p50": _pct(vals, 0.5), "p95": _pct(vals, 0.95), "max": max(vals) if vals else None}

    return {"schema": SCHEMA, "route": "timeline", "n": len(rows), "rows": rows, "units": "ms, bp (+ = adverse)",
            "rolling": {k: roll(p) for k, p in {
                "entry_signal_to_send_ms": ("entry", "signal_to_send_ms"),
                "fill_to_stop_placed_ms": ("entry", "fill_to_stop_placed_ms"),
                "fill_to_stop_confirmed_ms": ("entry", "fill_to_stop_confirmed_ms"),
                **{k: ("entry", k) for k in ("stop_ack_to_confirmed_ms", "entry_ack_to_fill_detected_ms",
                                             "fill_detected_to_claimed_ms", "claimed_to_stop_submit_ms",
                                             "stop_submit_to_ack_ms", "entry_ack_to_stop_ack_ms")},
                "entry_slippage_bp": ("entry", "slippage_bp_vs_paper"),
                "entry_slippage_bp_vs_signal_touch": ("entry", "slippage_bp_vs_signal_touch"),
                "paper_entry_slippage_bp_vs_signal_touch": ("entry", "paper_slippage_bp_vs_signal_touch"),
                "exit_paper_to_live_ms": ("exit", "paper_exit_to_live_exit_ms"),
                "exit_slippage_bp": ("exit", "slippage_bp_vs_paper")}.items()}}


def pause_history(accounts: Iterable[Mapping], *, now: float) -> dict:
    rows = []
    for a in accounts or []:
        for p in a.get("pause_history") or []:
            at = _ts(p.get("at"))
            rows.append({**{k: p.get(k) for k in ("at", "actor", "owner", "action", "code", "message")},
                         "account": a.get("account"), "age_s": round(now - at, 1) if at else None})
    rows.sort(key=lambda r: str(r.get("at") or ""))
    self_1h = [r for r in rows if r.get("actor") == "SYSTEM" and r.get("age_s") is not None and r["age_s"] <= 3600]
    status = RED if self_1h else GREEN
    causes = (["SELF_PAUSE_LAST_HOUR"] + (["SELF_PAUSE_RECURRING"] if len(self_1h) > 1 else [])) if self_1h else []
    current = [{"account": a.get("account"), "status": a.get("status"), "paused": a.get("paused"),
                "last_transition": a.get("last_transition"), "last_error": a.get("last_error")}
               for a in accounts or []]
    return {"schema": SCHEMA, "route": "pause-history", "status": status, "alert": bool(self_1h),
            "causes": causes, "self_pauses_1h": len(self_1h), "rows": rows[-100:], "accounts": current}


PROTECTION_GAP_SEC = 1.0


def protection_state(accounts: Iterable[Mapping], *, mirror_max_age_s: float = 30.0) -> dict:
    out, status, causes = [], GREEN, []
    for a in accounts or []:
        m = a.get("exchange_mirror") or {}
        if not m:
            continue
        orders = [o for o in m.get("open_orders") or [] if isinstance(o, Mapping)]
        ro = [o for o in orders if o.get("reduce_only")]
        stops = [o for o in ro if "STOP" in str(o.get("type") or "").upper() and not o.get("oco")]
        tps = [o for o in ro if "LIMIT" in str(o.get("type") or "").upper() and not o.get("oco")]
        oco = [o for o in ro if o.get("oco")]
        age = _f(m.get("read_age_s"))
        for p in m.get("positions") or []:
            protective = len(stops) + (1 if oco else 0)
            row = {"account": a.get("account"), "side": p.get("side"), "amount": p.get("amount"),
                   "stops": len(stops), "take_profits": len(tps), "oco_orders": len(oco),
                   "protective_count": protective, "mirror_age_s": age,
                   "order_ids": [o.get("id") for o in ro]}
            if protective == 0:
                row["gap"] = "UNPROTECTED"
            elif len(stops) > 1 or len(oco) > 2:
                row["gap"] = "DUPLICATE_PROTECTION"
            if row.get("gap"):
                status = RED
                causes.append(f"{row['gap']}:{a.get('account')}")
            out.append(row)
        if m.get("orphans"):
            status = RED if status == RED else AMBER
            causes.append(f"ORPHAN_ORDERS_WHEN_FLAT:{a.get('account')}")
        if age is not None and age > mirror_max_age_s and m.get("positions"):
            status = RED if status == RED else AMBER
            causes.append(f"MIRROR_STALE:{a.get('account')}")
    return {"schema": SCHEMA, "route": "protection", "status": status, "alert": status != GREEN,
            "causes": causes, "positions": out,
            "note": "from executor exchange mirror (read-only REST snapshot; sweeper SLA 1 s lands with A1)"}


def clock(fly_sync: Mapping | None, reports: Iterable[Mapping], *, since: float) -> dict:
    """One-way delay bounds: min(dst - src) = latency + skew; a negative min proves skew."""
    def bound(pairs):
        vals = [round((b - a) * 1000.0) for a, b in pairs if a is not None and b is not None]
        return {"n": len(vals), "min_ms": min(vals) if vals else None, "p50_ms": _pct(vals, 0.5)}
    fly_to_rw, rw_to_bfx, bfx_to_rw, rw_to_fly = [], [], [], []
    for r in reports or []:
        if (_f(r.get("fly_received_at_ts")) or 0) < since:
            continue
        tl = r.get("timeline") if isinstance(r.get("timeline"), Mapping) else {}
        fly_to_rw.append((_f(tl.get("fly_intent_emitted_at_ts")), _f(tl.get("railway_received_at_ts"))))
        rw_to_bfx.append((_f(tl.get("order_sent_at_ts")), _f(tl.get("fill_at_ts"))))
        rw_to_fly.append((_f(r.get("event_at_ts")), _f(r.get("fly_received_at_ts"))))
    fs = fly_sync or {}
    off = _f(fs.get("offset_ms"))
    st = GREEN if off is not None and abs(off) <= 50 else AMBER if off is not None and abs(off) <= 250 else RED
    if off is None:
        st = AMBER
    return {"schema": SCHEMA, "route": "clock", "status": st, "alert": st == RED,
            "fly": {"offset_ms_vs_external": off, "rtt_ms": _f(fs.get("rtt_ms")), "status": fs.get("status"),
                    "slew": fs.get("slew"), "age_sec": fs.get("age_sec")},
            "one_way_bounds": {"fly_intent_to_railway_received": bound(fly_to_rw),
                               "railway_order_sent_to_bitfinex_fill_mts": bound(rw_to_bfx),
                               "railway_event_to_fly_received": bound(rw_to_fly)},
            "note": "min_ms < 0 on any hop proves clock skew between those hosts; Railway and Bitfinex offsets are bounded, not measured"}


def paths(relay: Mapping | None, deliveries: Any) -> dict:
    r = relay or {}
    push = r.get("push_state") or {}
    outbox = r.get("live_copy_outbox") or {}
    recent = r.get("recent_deliveries") or []
    by_path: dict[str, int] = {}
    for d in recent if isinstance(recent, list) else []:
        k = str((d or {}).get("path") or (d or {}).get("transport") or "unknown")
        by_path[k] = by_path.get(k, 0) + 1
    return {"schema": SCHEMA, "route": "paths", "push_state": push, "outbox": outbox,
            "recent_by_path": by_path, "dedupe_hits": outbox.get("dedupe_hits") if isinstance(outbox, Mapping) else None,
            "deliveries": deliveries if isinstance(deliveries, (dict, list)) else None,
            "note": "B1 direct push not built yet: every intent rides the outbox"}
