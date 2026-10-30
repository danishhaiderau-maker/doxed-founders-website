"""HM-MISSING-20261012: integrity checks for the Health Monitor (pure; no I/O, no trading state).

Each check returns ``{"check", "status": GREEN|AMBER|RED|UNKNOWN, "alert": bool, "reasons": [...], ...}``.
``alert`` is True whenever status is RED (AMBER alerts only where noted) so the monitor can page on it.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Iterable, Mapping

SCHEMA = "monitor_check_v1"
STOP_TYPE_MARKERS = ("BREAKEVEN", "STOP", "CUT", "PATH_END", "TIME", "TRAIL", "GIVEBACK", "PROTECTION")
EXIT_TRUTH_RED_BP = 5.0
LATENCY_RED_SEC = 5.0
LATENCY_AMBER_SEC = 2.0
# Live exit paper_trigger->fill (live-copy mirror): audit §7 thresholds.
LIVE_EXIT_AMBER_SEC = 3.0
LIVE_EXIT_RED_SEC = 8.0
TAPE_RETENTION_REQUIRED_SEC = 7 * 86400
TAPE_GAP_AMBER_PCT = 1.0
TAPE_GAP_RED_PCT = 5.0


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _result(check: str, reasons: list, *, amber: bool = False, red: bool = False, unknown: bool = False,
            alert_on_amber: bool = False, **extra) -> dict:
    status = "RED" if red else "AMBER" if amber else "UNKNOWN" if unknown else "GREEN"
    return {"schema": SCHEMA, "check": check, "status": status,
            "alert": status == "RED" or (alert_on_amber and status == "AMBER"),
            "reasons": reasons[:50], **extra}


def is_stop_type(reason: Any) -> bool:
    r = str(reason or "").upper()
    return "TAKE_PROFIT" not in r and any(m in r for m in STOP_TYPE_MARKERS)


# ----------------------------------------------------------------------------- exit truth
def _book_top_compare_px(ex: Mapping[str, Any], exit_price: float | None) -> float | None:
    """Price compared to the final ``exit_book_top_*`` stamp (both-legs realism).

    Paper-book lanes store ``exit_price`` as ``VWAP_ALL_LEGS``. Comparing that blend to the FINAL
    leg's book top falsely REDs honest TP1 + remainder closes (BTC-477 gb2-899a1661f1ff: VWAP 78301
    vs final bid 78249 while each leg matched its own fill touch). Use the final leg's fill for the
    stamp check; per-leg honesty remains the fill_book statistic.
    """
    legs = ex.get("legs") or []
    multi = len(legs) > 1 or str(ex.get("exit_price_basis") or "") == "VWAP_ALL_LEGS"
    if multi and legs:
        final = _f((legs[-1] or {}).get("price"))
        if final is not None:
            return final
    return exit_price


def exit_truth(rows: Iterable[Mapping[str, Any]], tape_ts: list, tape_rows: list, *, since: float) -> dict:
    """Booked exit price vs the executable touch on the 1 s tape at close.

    LONG closes sell at the bid, SHORT closes buy at the ask. A stop-type exit booked better than the
    touch by more than 5 bp is RED (the stale-trigger booking bug of 8-10 Oct). Rows with their own
    exit_bid/exit_ask stamps use those instead of the tape.

    Both-legs realism: when ``exit_price`` is a multi-leg VWAP, the book-top statistic uses the final
    leg's fill (not the blend) against the final touch; each leg is still checked vs its fill snapshot.
    """
    checked, bad, no_tape = 0, [], 0
    fb_n, fb_missing, fb_all, fb_bad = 0, 0, [], []
    for r in rows:
        ex = r.get("exit") or {}
        ct, px = _f(ex.get("close_ts")), _f(ex.get("exit_price"))
        if ct is None or px is None or ct < since or ex.get("forced_close") or not is_stop_type(ex.get("exit_reason")):
            continue
        side = r.get("side")
        # Second statistic (HM 19 Oct), beside the trigger-time check and hiding nothing: each booked leg vs the touch of
        # the book the pricer actually walked at fill time (exit_depth_snapshot_legs[].fill). A leg booked better than that
        # touch is impossible for a book walk, so it is counted as a violation too.
        fb_better = _fill_book_better_bp(r, side)
        if fb_better is None:
            fb_missing += 1
        else:
            fb_n += 1
            fb_all.append(fb_better)
            if fb_better > EXIT_TRUTH_RED_BP:
                fb_bad.append({"trade_id": r.get("trade_id"), "lane": r.get("lane") or r.get("research_lane"),
                               "exit_reason": ex.get("exit_reason"), "booked_better_than_fill_touch_bp": round(fb_better, 2)})
        # R3: the book-top stamp (bfx_book_10l_1s) first; exit_bid/exit_ask only on legacy rows written before it.
        bid, ask = _f(ex.get("exit_book_top_bid")), _f(ex.get("exit_book_top_ask"))
        if bid is None or ask is None:
            bid, ask = _f(ex.get("exit_bid")), _f(ex.get("exit_ask"))
        if bid is None or ask is None:
            i = bisect.bisect_right(tape_ts, ct) - 1
            if i < 0 or ct - tape_ts[i] > 2.0:
                no_tape += 1
                continue
            bid, ask = _f(tape_rows[i].get("bid")), _f(tape_rows[i].get("ask"))
        touch = bid if side == "LONG" else ask if side == "SHORT" else None
        if not touch:
            no_tape += 1
            continue
        compare_px = _book_top_compare_px(ex, px)
        if compare_px is None:
            no_tape += 1
            continue
        checked += 1
        better_bp = (compare_px / touch - 1) * 1e4 * (1 if side == "LONG" else -1)
        if better_bp > EXIT_TRUTH_RED_BP:
            bad.append({"trade_id": r.get("trade_id"), "lane": r.get("lane") or r.get("research_lane"),
                        "exit_reason": ex.get("exit_reason"), "close_ts": ct, "booked": compare_px, "touch": touch,
                        "booked_better_than_touch_bp": round(better_bp, 2),
                        "exit_price_vwap": px if compare_px != px else None})
    reasons = [f"{len(bad)} stop-type exits booked >{EXIT_TRUTH_RED_BP:g} bp better than the touch"] if bad else []
    if fb_bad:
        reasons.append(f"{len(fb_bad)} stop-type exits booked >{EXIT_TRUTH_RED_BP:g} bp better than the fill-time book touch")
    fill_book = {"checked": fb_n, "without_snapshot": fb_missing, "violations": fb_bad[:50],
                 "max_better_bp": round(max(fb_all), 3) if fb_all else None,
                 "mean_better_bp": round(sum(fb_all) / len(fb_all), 3) if fb_all else None,
                 "basis": "booked leg price vs best level of exit_depth_snapshot_legs[].fill (taker side); positive = better than the touch"}
    return _result("exit-truth", reasons, red=bool(bad or fb_bad), unknown=checked == 0, checked=checked, without_touch=no_tape,
                   violations=bad[:50], fill_book=fill_book)


def _fill_book_better_bp(row: Mapping[str, Any], side: Any) -> float | None:
    """Worst (largest) booked-better-than-fill-touch bp over the row's exit legs, None when no leg has a fill snapshot."""
    legs = (row.get("pricing") or {}).get("exit_depth_snapshot_legs") or []
    key = "b" if side == "LONG" else "a" if side == "SHORT" else None
    worst = None
    for leg in legs if key else ():
        fill = (leg or {}).get("fill") or {}
        px = _f((leg or {}).get("price"))
        try:
            touch = _f((fill.get(key) or [[None]])[0][0])
        except (IndexError, TypeError):
            touch = None
        if px is None or not touch:
            continue
        bp = (px / touch - 1) * 1e4 * (1 if side == "LONG" else -1)
        worst = bp if worst is None else max(worst, bp)
    return worst


# ----------------------------------------------------------------------------- pnl reconcile
def pnl_reconcile(totals: Mapping[str, Any], rows_by_lane: Mapping[str, list], lanes_net: Mapping[str, Any] | None,
                  balance_reconcile: Mapping[str, Any] | None, *, tol_usd: float = 0.01) -> dict:
    """Totals == sum of trade rows (unrounded), dashboard lanes == totals, wallet == start + P&L."""
    reasons, per = [], {}
    for lane, t in (totals.get("lanes") or {}).items():
        rows = [x for x in rows_by_lane.get(lane) or [] if x.get("status") == "CLOSED"
                and not (x.get("exit") or {}).get("forced_close")]
        s = sum(_f((x.get("pnl") or {}).get("net_usd")) or 0.0 for x in rows)
        tn = _f(t.get("net_usd")) or 0.0
        entry = {"totals_net_usd": tn, "sum_rows_net_usd": round(s, 6)}
        if abs(s - tn) > 1e-6:
            reasons.append(f"{lane}: totals {tn:.4f} != rows {s:.4f}")
        ln = _f((lanes_net or {}).get(lane))
        if ln is not None:
            entry["lanes_net_usd"] = ln
            if abs(ln - tn) > tol_usd:
                reasons.append(f"{lane}: lanes/card {ln:.4f} != totals {tn:.4f} (basis split)")
        per[lane] = entry
    resid = _f((balance_reconcile or {}).get("residual_usd"))
    if resid is not None and abs(resid) > 0.05:
        reasons.append(f"wallet residual {resid:+.4f} USD")
    return _result("pnl-reconcile", reasons, red=bool(reasons), per_lane=per, balance_residual_usd=resid)


# ----------------------------------------------------------------------------- rules conformance
def rules_conformance(*, active_tiles: Iterable[str], retired_tiles: Iterable[str],
                      live_copy_tiles: Mapping[str, Mapping[str, Any]] | None, open_lanes: Iterable[str],
                      fee_rates_bp: Mapping[str, Any], totals_lanes: Mapping[str, Any],
                      partial_exit_tiles: Iterable[str] = ()) -> dict:
    """Danish's standing rules: zero fees; every dashboard tile live-copyable; retired tiles never trade
    or copy; deploy flattens never counted in strategy stats."""
    reasons = []
    for k, v in (fee_rates_bp or {}).items():
        if (_f(v) or 0.0) != 0.0:
            reasons.append(f"fee {k}={v} bp (rule: no fees)")
    active, retired = set(active_tiles), set(retired_tiles)
    # Danish's rule: every dashboard tile must be live-copyable (switch row present + operator-eligible).
    if live_copy_tiles is not None:
        for t in sorted(active):
            info = live_copy_tiles.get(t)
            if info is None:
                reasons.append(f"{t} has no live-copy switch (not live-copyable)")
            elif not info.get("live_eligible", info.get("copyable", False)):
                reasons.append(f"{t} not live-copyable")
            # LIVE-FIX-1330: an eligible switch is not enough when the relay
            # refuses the tile (partial exits need exchange reductions).
            if t in set(partial_exit_tiles):
                reasons.append(f"{t} not live-copyable: partial exits need exchange reductions (relay refuses)")
    for t in sorted(retired):
        info = (live_copy_tiles or {}).get(t) or {}
        if info.get("live_on") or info.get("bitfinex_live_orders"):
            reasons.append(f"retired {t} has live copy ON")
    for lane in set(open_lanes) & retired:
        reasons.append(f"retired {lane} holds an open position")
    for lane, t in (totals_lanes or {}).items():
        if (t.get("forced_closes_counted") or 0) > 0:
            reasons.append(f"{lane}: forced closes counted in strategy stats")
    return _result("rules-conformance", reasons, red=bool(reasons))


# ----------------------------------------------------------------------------- data completeness / tape
def data_completeness(tape_bounds: tuple, *, now: float, window_sec: float = 3600.0,
                      streams: Mapping[str, Any] | None = None, stale_sec: float = 120.0) -> dict:
    first, last, n = tape_bounds
    reasons, amber, red = [], False, False
    if not n or last is None:
        return _result("data-completeness", ["1 s tape empty"], red=True)
    lag = now - float(last)
    covered = min(window_sec, float(last) - float(first) + 1)
    expected = max(1.0, min(window_sec, now - float(first)))
    have = min(n, covered)
    gap_pct = max(0.0, 100.0 * (1 - have / expected))
    if lag > 10:
        reasons.append(f"tape last row {lag:.0f}s old")
        red = lag > 60
        amber = True
    if gap_pct > TAPE_GAP_RED_PCT:
        red = True
        reasons.append(f"tape missing {gap_pct:.1f}% of seconds")
    elif gap_pct > TAPE_GAP_AMBER_PCT:
        amber = True
        reasons.append(f"tape missing {gap_pct:.1f}% of seconds")
    stream_rows = {}
    for name, age in (streams or {}).items():
        a = _f(age)
        stream_rows[name] = a
        if a is None or a > stale_sec:
            amber = True
            reasons.append(f"stream {name} stale ({a}s)")
    return _result("data-completeness", reasons, red=red, amber=amber, alert_on_amber=False,
                   tape_lag_sec=round(lag, 1), tape_gap_pct=round(gap_pct, 3), streams_age_sec=stream_rows)


def tape_retention(memory_bounds: tuple, archive_oldest_ts: float | None, *, now: float) -> dict:
    """7-day 1 s tape retention: in-memory ring plus on-disk archive must reach back 7 days."""
    first_mem = memory_bounds[0]
    oldest = min(x for x in (first_mem, archive_oldest_ts) if x is not None) if (first_mem or archive_oldest_ts) else None
    reach = (now - float(oldest)) if oldest else 0.0
    ok = reach >= TAPE_RETENTION_REQUIRED_SEC - 3600
    reasons = [] if ok else [f"1 s tape reaches back {reach / 3600:.1f} h; rule is 7 days"]
    return _result("tape-retention", reasons, amber=not ok and reach > 0, red=not ok and reach <= 0,
                   alert_on_amber=True, reach_hours=round(reach / 3600, 2), required_hours=168,
                   memory_oldest_ts=first_mem, archive_oldest_ts=archive_oldest_ts)


# ----------------------------------------------------------------------------- latency guard
def latency_guard(slo: Mapping[str, Any], per_tile: Mapping[str, Any],
                  live_on_lanes: Iterable[str] | None = None) -> dict:
    """Alert flag over the exit-latency SLO.

    Paper/local exit: RED if any tile's max trigger->fill > 5 s, AMBER > 2 s.
    Live exit (paper_trigger->live fill on ``live_exit``): AMBER > 3 s, RED > 8 s.
    A lane that is live-ON but missing from ``per_tile`` is RED (no SLO coverage).
    """
    reasons, red, amber, worst = [], False, False, {}
    tile_keys = {str(k).upper() for k in (per_tile or {})}
    for lane in sorted({str(x).upper() for x in (live_on_lanes or ()) if x}):
        if lane not in tile_keys:
            red = True
            reasons.append(f"{lane}: live-ON but missing from exit-latency")
    for lane, t in (per_tile or {}).items():
        t = t or {}
        ex = t.get("exit") or {}
        mx = _f(ex.get("max_sec") or ex.get("max_trigger_to_fill_sec") or ex.get("p100_sec"))
        p95 = _f(ex.get("p95_sec"))
        v = mx if mx is not None else p95
        worst[lane] = v
        if v is not None:
            if v > LATENCY_RED_SEC:
                red = True
                reasons.append(f"{lane}: exit {v:.1f}s > {LATENCY_RED_SEC:g}s")
            elif v > LATENCY_AMBER_SEC:
                amber = True
                reasons.append(f"{lane}: exit {v:.1f}s > {LATENCY_AMBER_SEC:g}s")
        live_ex = t.get("live_exit") if isinstance(t.get("live_exit"), Mapping) else None
        if live_ex:
            lv = _f(live_ex.get("max_sec") or live_ex.get("p95_sec"))
            worst[f"{lane}.live_exit"] = lv
            if lv is None:
                continue
            top = None
            for bucket in (live_ex.get("breaches_over_8s") or live_ex.get("breaches_over_3s") or []):
                if isinstance(bucket, Mapping) and bucket.get("top_hop"):
                    top = bucket.get("top_hop")
                    break
            hop = f" (top hop {top})" if top else ""
            if lv > LIVE_EXIT_RED_SEC:
                red = True
                reasons.append(f"{lane}: live exit {lv:.1f}s > {LIVE_EXIT_RED_SEC:g}s{hop}")
            elif lv > LIVE_EXIT_AMBER_SEC:
                amber = True
                reasons.append(f"{lane}: live exit {lv:.1f}s > {LIVE_EXIT_AMBER_SEC:g}s{hop}")
    if str((slo or {}).get("status") or "").upper() == "RED":
        red = True
        reasons.append("exit-latency SLO RED")
    return _result("exits-latency-guard", reasons, red=red, amber=amber, alert_on_amber=True, worst_sec=worst)


def emergency_admission(http_handlers: Mapping[str, Any] | None, *, now: float,
                        window_sec: float = 24 * 3600.0) -> dict:
    """HM D-B: ANY rejection of the reserved emergency class (pause/disarm/flatten POSTs) is RED.

    ``http_handlers`` is ``_dashboard_handler_snapshot()``. Probe-class (control) rejections are
    reported as context only; they are not safety-relevant. Counters reset on restart, so a RED
    clears 24 h after the last emergency rejection or at the next process start.
    """
    h = http_handlers or {}
    rej = h.get("rejected_by_cap") or {}
    em = rej.get("emergency") or {}
    total = int(_f(em.get("total")) or 0)
    last = _f(em.get("last_ts"))
    ctl = rej.get("control") or {}
    # HM #676 B2: dispatch AND the emergency reserve both full -> RED (any count > 0).
    dr = rej.get("dispatch_reserve") or {}
    dr_total = int(_f(dr.get("total")) or 0)
    dr_last = _f(dr.get("last_ts"))
    extra = {"dispatch_reserve_exhausted_total": dr_total, "dispatch_reserve_last_ts": dr_last,
             "emergency_rejected_total": total, "emergency_last_ts": last,
             "emergency_by_reason": dict(em.get("by_reason") or {}),
             "control_probe_rejected_total": int(_f(ctl.get("total")) or 0),
             "window_sec": window_sec}
    if dr_total > 0 and (dr_last is None or now - dr_last <= window_sec):
        return _result("emergency-admission",
                       [f"{dr_total} request(s) rejected with dispatch AND emergency reserve exhausted "
                        "(dispatch_cap_full_reserve_exhausted): a pause/disarm/flatten may have been refused"],
                       red=True, **extra)
    if total > 0 and (last is None or now - last <= window_sec):
        return _result("emergency-admission",
                       [f"{total} emergency-class request(s) rejected (pause/disarm/flatten may have been refused)"],
                       red=True, **extra)
    return _result("emergency-admission", [], **extra)


CLOCK_AMBER_MS = 50.0
CLOCK_RED_MS = 250.0


def clock_skew(sync: Mapping[str, Any] | None, *, live_on: bool) -> dict:
    """HM 20:33: alert on Fly wall-clock offset (992 ms went unalerted).

    AMBER > 50 ms (A2 budget), RED > 250 ms while live copy is on (AMBER otherwise).
    """
    s = sync or {}
    off = _f(s.get("offset_ms"))
    extra = {"offset_ms": off, "rtt_ms": _f(s.get("rtt_ms")), "age_sec": _f(s.get("age_sec")),
             "slew": s.get("slew"), "live_on": live_on}
    if off is None or s.get("synchronized") is None:
        return _result("clock-skew", [f"no trusted external sample ({s.get('reason') or 'unknown'})"],
                       unknown=True, **extra)
    a = abs(off)
    if a > CLOCK_RED_MS:
        return _result("clock-skew", [f"Fly clock offset {off:.0f} ms > {CLOCK_RED_MS:.0f} ms"],
                       red=live_on, amber=not live_on, alert_on_amber=True, **extra)
    if a > CLOCK_AMBER_MS:
        return _result("clock-skew", [f"Fly clock offset {off:.0f} ms > {CLOCK_AMBER_MS:.0f} ms"],
                       amber=True, alert_on_amber=live_on, **extra)
    return _result("clock-skew", [], **extra)
