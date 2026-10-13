"""Tile data-integrity monitor (Danish 10:28, HM-approved with amendments 10:33).

Pure functions; the bot runs ``evaluate`` every 5 min and serves the result read-only at
GET /api/monitor/tile-integrity (also folded into /api/monitor/checks).

HM amendments:
1. The fill recompute is a SEPARATE minimal implementation. It must never import the shared
   paper fill function (CI test enforces). A >0.1 bp difference from the same stored book is a
   code bug (reported as ``code_bug``).
2. Fill recompute: AMBER any fill >0.5 bp; RED any fill >2 bp or rolling-10 mean |diff| >0.5 bp.
   Live vs shadow: AMBER >1 bp per fill; RED >3 bp per fill or rolling-10 mean >1 bp.
3. Frozen feed: no book update messages for 5 s (message counter not advancing) AND book age >1 s.
   Never "same BBO".
4. Heartbeat: last run older than 10 min -> RED in /api/monitor/checks.
5. Detects an injected wrong fill, a missing snapshot and a P&L mismatch within one 5 min run.
6. fill_detect_lag_ms: relay fill detection vs exchange fill time, per live fill.
"""
from __future__ import annotations

import statistics
from typing import Any, Iterable, Mapping, Optional

SCHEMA = "tile_integrity_v1"
RUN_EVERY_S = 300
HEARTBEAT_RED_S = 600
CODE_BUG_BP = 0.1
FILL_AMBER_BP, FILL_RED_BP, FILL_ROLL_RED_BP = 0.5, 2.0, 0.5
LIVE_AMBER_BP, LIVE_RED_BP, LIVE_ROLL_RED_BP = 1.0, 3.0, 1.0
FROZEN_NO_MSG_S, FROZEN_AGE_S = 5.0, 1.0
REQUIRED_V1_FIELDS = ("fill_model_version", "ask_age_ms", "delay_s", "book_age_ms",
                      "depth_snapshot_signal", "depth_snapshot_fill")
_ORDER = {"RED": 3, "AMBER": 2, "UNKNOWN": 1, "GREEN": 0}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def walk_vwap(levels, qty) -> Optional[float]:
    """Minimal independent depth walk: levels [[px, size], ...] best first. None if not enough depth."""
    q = _f(qty)
    if not q or q <= 0 or not levels:
        return None
    left, cost = q, 0.0
    for lv in levels:
        px, sz = _f(lv[0]), _f(lv[1])
        if not px or not sz or sz <= 0:
            continue
        take = min(left, sz)
        cost += take * px
        left -= take
        if left <= 1e-12:
            return cost / q
    return None


def recompute_fill(row: Mapping[str, Any], book_by_sec: Mapping[int, Mapping[str, Any]]) -> dict:
    """Re-price one v1 paper fill from the stored 10-level book second it claims to have used."""
    sec = row.get("book_s")
    book = book_by_sec.get(int(sec)) if sec is not None else None
    if not book:
        return {"trade_id": row.get("trade_id"), "status": "MISSING_SNAPSHOT"}
    side = str(row.get("side") or "").upper()
    levels = book.get("a") if side == "LONG" else book.get("b")
    px = walk_vwap(levels, row.get("qty"))
    stored = _f(row.get("fill_price"))
    if px is None or not stored:
        return {"trade_id": row.get("trade_id"), "status": "NO_DEPTH" if px is None else "NO_PRICE"}
    diff = abs(stored / px - 1.0) * 1e4
    return {"trade_id": row.get("trade_id"), "status": "OK", "recomputed": px, "stored": stored,
            "diff_bp": round(diff, 4), "code_bug": diff > CODE_BUG_BP}


def _verdict(reasons_red, reasons_amber, **extra) -> dict:
    status = "RED" if reasons_red else "AMBER" if reasons_amber else "GREEN"
    return {"status": status, "reasons": (reasons_red + reasons_amber)[:50], **extra}


def fill_recompute_check(rows: Iterable[Mapping[str, Any]], book_by_sec) -> dict:
    red, amber, results = [], [], []
    for r in rows:
        if (r.get("fill_model_version") or 0) < 1:
            continue
        res = recompute_fill(r, book_by_sec)
        results.append(res)
        if res["status"] == "MISSING_SNAPSHOT":
            red.append(f"MISSING_SNAPSHOT {res['trade_id']}")
        elif res["status"] == "OK":
            if res["diff_bp"] > FILL_RED_BP:
                red.append(f"FILL_OFF {res['trade_id']} {res['diff_bp']}bp")
            elif res["diff_bp"] > FILL_AMBER_BP:
                amber.append(f"FILL_OFF {res['trade_id']} {res['diff_bp']}bp")
    diffs = [x["diff_bp"] for x in results if x["status"] == "OK"]
    roll = round(statistics.fmean(diffs[-10:]), 4) if diffs else None
    if roll is not None and len(diffs) >= 10 and roll > FILL_ROLL_RED_BP:
        red.append(f"FILL_ROLLING10 {roll}bp")
    return _verdict(red, amber, checked=len(results), rolling10_bp=roll,
                    code_bugs=sum(1 for x in results if x.get("code_bug")), worst=sorted(
                        results, key=lambda x: -(x.get("diff_bp") or 0))[:5])


def presence_check(rows: Iterable[Mapping[str, Any]], v1_since_ts: Optional[float]) -> dict:
    if not v1_since_ts:
        return {"status": "GREEN", "reasons": [], "note": "V1_FILL_MODEL_NOT_YET_EMITTED", "checked": 0}
    red, n = [], 0
    for r in rows:
        ts = _f(r.get("fill_ts") or r.get("entry_ts") or r.get("ts"))
        if not ts or ts < v1_since_ts:
            continue
        n += 1
        miss = [k for k in REQUIRED_V1_FIELDS if r.get(k) in (None, "", [])]
        if miss:
            red.append(f"MISSING {r.get('trade_id')} {','.join(miss)}")
    return _verdict(red, [], checked=n)


def live_vs_shadow_check(chains: Iterable[Mapping[str, Any]]) -> dict:
    red, amber, diffs = [], [], []
    for ch in chains:
        d = _f(ch.get("slippage_vs_paper_bp"))
        if d is None:
            continue
        diffs.append(d)
        cid = ch.get("correlation_id")
        if abs(d) > LIVE_RED_BP:
            red.append(f"LIVE_VS_SHADOW {cid} {d}bp")
        elif abs(d) > LIVE_AMBER_BP:
            amber.append(f"LIVE_VS_SHADOW {cid} {d}bp")
    roll = round(statistics.fmean(diffs[-10:]), 4) if diffs else None
    if roll is not None and len(diffs) >= 10 and abs(roll) > LIVE_ROLL_RED_BP:
        red.append(f"LIVE_ROLLING10 {roll}bp")
    return _verdict(red, amber, checked=len(diffs), rolling10_bp=roll)


def fill_detect_lag(chains: Iterable[Mapping[str, Any]]) -> dict:
    """fill_detect_lag_ms = relay fill-detected ts - exchange fill ts (per live fill)."""
    lags = []
    for ch in chains:
        fill, det = _f(ch.get("fill_at_ts")), _f(ch.get("fill_detected_at_ts") or ch.get("fill_reported_to_fly_at_ts"))
        if fill and det:
            lags.append({"correlation_id": ch.get("correlation_id"), "fill_detect_lag_ms": round((det - fill) * 1000),
                         "basis": "fill_detected_at_ts" if ch.get("fill_detected_at_ts") else "fill_reported_to_fly_at_ts"})
    ms = sorted(x["fill_detect_lag_ms"] for x in lags)
    return {"n": len(ms), "p50_ms": ms[len(ms) // 2] if ms else None,
            "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else None, "recent": lags[-20:]}


class FeedWatch:
    """Frozen-feed detector over a message counter (HM: never 'same BBO')."""

    def __init__(self) -> None:
        self.last_msgs: Optional[int] = None
        self.last_advance_ts: Optional[float] = None

    def observe(self, msgs: Optional[int], book_ts: Optional[float], now: float) -> dict:
        if msgs is not None and msgs != self.last_msgs:
            self.last_msgs, self.last_advance_ts = msgs, now
        no_msg_s = None if self.last_advance_ts is None else now - self.last_advance_ts
        age_s = None if not book_ts else now - book_ts
        frozen = (no_msg_s is None or no_msg_s > FROZEN_NO_MSG_S) and (age_s is None or age_s > FROZEN_AGE_S)
        reasons = [f"FROZEN_FEED no_msg_s={no_msg_s} age_s={age_s}"] if frozen else []
        return {"status": "RED" if frozen else "GREEN", "reasons": reasons,
                "no_msg_s": None if no_msg_s is None else round(no_msg_s, 3),
                "book_age_s": None if age_s is None else round(age_s, 3)}


def pnl_check(pnl_reconcile: Optional[Mapping[str, Any]]) -> dict:
    """Reuses the existing pnl-reconcile check (tile/dashboard P&L == sum of trade rows)."""
    if not pnl_reconcile:
        return {"status": "UNKNOWN", "reasons": ["PNL_RECONCILE_UNAVAILABLE"]}
    st = pnl_reconcile.get("status") or "UNKNOWN"
    return {"status": "RED" if st in ("RED", "AMBER") else st, "reasons": list(pnl_reconcile.get("reasons") or [])[:10]}


def evaluate(*, now, rows, book_by_sec, chains, v1_since_ts, feed: dict, pnl_reconcile) -> dict:
    parts = {
        "fill-recompute": fill_recompute_check(rows, book_by_sec),
        "presence": presence_check(rows, v1_since_ts),
        "live-vs-shadow": live_vs_shadow_check(chains),
        "frozen-feed": feed,
        "pnl": pnl_check(pnl_reconcile),
    }
    worst = max((p["status"] for p in parts.values()), key=lambda s: _ORDER.get(s, 0), default="GREEN")
    return {"schema": SCHEMA, "run_ts": now, "status": worst, "alert": worst == "RED",
            "parts": parts, "fill_detect_lag": fill_detect_lag(chains)}


def heartbeat_check(last: Optional[Mapping[str, Any]], now: float) -> dict:
    """Entry for /api/monitor/checks: RED if not run in 10 min, else the last run's verdict."""
    if not last or last.get("run_ts") is None:
        return {"check": "tile-integrity", "status": "RED", "alert": True, "reasons": ["NEVER_RAN"]}
    age = now - float(last["run_ts"])
    if age > HEARTBEAT_RED_S:
        return {"check": "tile-integrity", "status": "RED", "alert": True,
                "reasons": [f"HEARTBEAT_STALE {round(age)}s"], "last_run_ts": last["run_ts"]}
    reasons = [f"{k}: {r}" for k, p in (last.get("parts") or {}).items() for r in (p.get("reasons") or [])[:3]]
    return {"check": "tile-integrity", "status": last.get("status", "UNKNOWN"),
            "alert": last.get("status") == "RED", "reasons": reasons[:50], "last_run_ts": last["run_ts"],
            "age_s": round(age, 1)}
