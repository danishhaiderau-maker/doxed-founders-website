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
FILL_ROLL_SIGNED_RED_BP = 0.5  # |signed rolling-10 mean| (n >= 10)
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


def walk_vwap(levels, qty):
    """Minimal independent depth walk: levels [[px, size], ...] best first.
    Returns (vwap over the filled part, filled_qty); (None, 0.0) when nothing fills."""
    q = _f(qty)
    if not q or q <= 0 or not levels:
        return None, 0.0
    left, cost, filled = q, 0.0, 0.0
    for lv in levels:
        px, sz = _f(lv[0]), _f(lv[1])
        if not px or not sz or sz <= 0:
            continue
        take = min(left, sz)
        cost += take * px
        filled += take
        left -= take
        if left <= 1e-12:
            break
    return (cost / filled if filled > 0 else None), filled


def _buys(side: str, leg: str) -> bool:
    """Entry LONG / exit SHORT buy (ask); entry SHORT / exit LONG sell (bid)."""
    return (str(side or "").upper() == "LONG") == (leg == "entry")


LEG_FIELDS = {"entry": ("book_s", "fill_price", "filled_qty"),
              "exit": ("exit_book_s", "exit_price", "exit_filled_qty")}


def recompute_fill(row: Mapping[str, Any], book_by_sec: Mapping[int, Mapping[str, Any]], leg: str = "entry") -> dict:
    """Re-price one v1 paper fill leg from the stored 10-level book second it claims to have used."""
    sec_key, px_key, qty_key = LEG_FIELDS[leg]
    tid = row.get("trade_id")
    sec = row.get(sec_key)
    book = book_by_sec.get(int(sec)) if sec is not None else None
    if not book:
        return {"trade_id": tid, "leg": leg, "status": "MISSING_SNAPSHOT"}
    levels = book.get("a") if _buys(row.get("side"), leg) else book.get("b")
    px, filled = walk_vwap(levels, row.get("qty"))
    stored = _f(row.get(px_key))
    if px is None:
        return {"trade_id": tid, "leg": leg, "status": "NO_DEPTH"}
    if not stored:
        return {"trade_id": tid, "leg": leg, "status": "NO_PRICE"}
    diff = (stored / px - 1.0) * 1e4
    sgn = 1.0 if _buys(row.get("side"), leg) else -1.0  # + = stored worse (paid more / received less)
    stored_qty = _f(row.get(qty_key))
    qty_mismatch = stored_qty is not None and abs(stored_qty - filled) > 1e-8
    return {"trade_id": tid, "leg": leg, "status": "OK", "recomputed": px, "stored": stored,
            "recomputed_qty": round(filled, 8), "stored_qty": stored_qty, "qty_mismatch": qty_mismatch,
            "diff_bp": round(abs(diff), 4), "signed_bp": round(sgn * diff, 4), "code_bug": abs(diff) > CODE_BUG_BP}


def _verdict(reasons_red, reasons_amber, **extra) -> dict:
    status = "RED" if reasons_red else "AMBER" if reasons_amber else "GREEN"
    return {"status": status, "reasons": (reasons_red + reasons_amber)[:50], **extra}


BACKSTOP_LEGS = {
    "entry": {"ts": "fill_ts", "start": "signal_ts", "delay": "delay_s", "book_s": "book_s",
              "price": "fill_price", "qty": "filled_qty", "book": "fill_book"},
    "exit": {"ts": "exit_ts", "start": "exit_trigger_ts", "delay": "exit_delay_s", "book_s": "exit_book_s",
             "price": "exit_price", "qty": "exit_filled_qty", "book": "exit_fill_book"},
}


def runtime_backstop(row: Mapping[str, Any]) -> list:
    """HM 13:27: per-row invariants that need no external book. Returns RED reasons.

    fill_ts >= start + delay_s (no early fill); book_s + 1 <= fill_ts (book second complete before
    the fill); the stored price equals a re-walk of the stored fill book. FORCED_NO_BOOK is RED.
    """
    out = []
    tid = row.get("trade_id")
    if row.get("exit_status") == "FORCED_NO_BOOK":
        out.append(f"FORCED_NO_BOOK {tid}")
    for leg, k in BACKSTOP_LEGS.items():
        ts, start, delay = _f(row.get(k["ts"])), _f(row.get(k["start"])), _f(row.get(k["delay"]))
        if row.get(k["price"]) is None:
            continue
        if ts is not None and start is not None and delay is not None and ts + 1e-6 < start + delay:
            out.append(f"EARLY_FILL {tid}/{leg} {ts} < {start}+{delay}")
        bs = _f(row.get(k["book_s"]))
        if ts is not None and bs is not None and bs + 1 > ts + 1e-6:
            out.append(f"LOOK_AHEAD_BOOK {tid}/{leg} book_s={int(bs)} fill_ts={ts}")
        book = row.get(k["book"])
        if leg == "exit" and (row.get("exit_pieces") or 1) > 1:
            continue  # VWAP over several books; each piece is checked on its own partial-exit row (legacy DISLOC_PARTIAL_EXIT history)
        if isinstance(book, Mapping):
            levels = book.get("a") if _buys(row.get("side"), leg) else book.get("b")
            px, filled = walk_vwap(levels, row.get(k["qty"]) or row.get("qty"))
            stored = _f(row.get(k["price"]))
            if px is None or stored is None or abs(stored - px) > 1e-9 * max(1.0, abs(px)):
                out.append(f"PRICE_NOT_FROM_BOOK {tid}/{leg} stored={stored} rewalk={px}")
    return out


def fill_recompute_check(rows: Iterable[Mapping[str, Any]], book_by_sec) -> dict:
    red, amber, results = [], [], []
    if book_by_sec is None:  # the stored-book read itself failed: cannot verify
        return _verdict([], ["BOOK_READ_FAILED"], checked=0, rolling10_bp=None, rolling10_signed_bp=None,
                        code_bugs=0, worst=[])
    rows = list(rows)
    for r in rows:
        if (r.get("fill_model_version") or 0) < 1:
            continue
        red.extend(runtime_backstop(r))
        for leg in ("entry", "exit"):
            if leg == "exit" and r.get("exit_price") is None and r.get("exit_book_s") is None:
                continue
            res = recompute_fill(r, book_by_sec, leg)
            results.append(res)
            tag = f"{res['trade_id']}/{leg}"
            if res["status"] == "MISSING_SNAPSHOT":
                red.append(f"MISSING_SNAPSHOT {tag}")
            elif res["status"] in ("NO_DEPTH", "NO_PRICE"):
                amber.append(f"{res['status']} {tag}")
            else:
                if res["diff_bp"] > FILL_RED_BP:
                    red.append(f"FILL_OFF {tag} {res['diff_bp']}bp")
                elif res["diff_bp"] > FILL_AMBER_BP:
                    amber.append(f"FILL_OFF {tag} {res['diff_bp']}bp")
                if res["qty_mismatch"]:
                    amber.append(f"QTY_MISMATCH {tag} {res['stored_qty']} vs {res['recomputed_qty']}")
    ok = [x for x in results if x["status"] == "OK"]
    roll = round(statistics.fmean([x["diff_bp"] for x in ok[-10:]]), 4) if ok else None
    roll_signed = round(statistics.fmean([x["signed_bp"] for x in ok[-10:]]), 4) if ok else None
    if roll is not None and len(ok) >= 10 and roll > FILL_ROLL_RED_BP:
        red.append(f"FILL_ROLLING10 {roll}bp")
    # HM 13:20: a one-directional bias is RED even when each fill is individually small.
    if roll_signed is not None and len(ok) >= 10 and abs(roll_signed) > FILL_ROLL_SIGNED_RED_BP:
        red.append(f"FILL_BIAS_ROLLING10 {roll_signed}bp")
    return _verdict(red, amber, checked=len(results), rolling10_bp=roll, rolling10_signed_bp=roll_signed,
                    code_bugs=sum(1 for x in results if x.get("code_bug")),
                    worst=sorted(results, key=lambda x: -(x.get("diff_bp") or 0))[:5])


def delay_default_share(rows: Iterable[Mapping[str, Any]]) -> dict:
    """HM 12:20: share of v1 rows priced with the default delay (delay_source=default_until_n10)."""
    v1 = [r for r in rows if (r.get("fill_model_version") or 0) >= 1]
    d = sum(1 for r in v1 if str(r.get("delay_source") or "").startswith("default"))
    return {"n": len(v1), "defaulted": d, "share": round(d / len(v1), 3) if v1 else None}


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


def regime_coverage_check(tile_rows: Iterable[Mapping[str, Any]], since_ts: Optional[float]) -> dict:
    """Strategist 10:37: every tile row after the epoch must carry regime_at_signal. AMBER if any miss."""
    n, miss = 0, []
    for r in tile_rows:
        ts = _f(r.get("entry_ts") or r.get("opened_at_ts") or r.get("signal_ts") or r.get("fill_ts"))
        if since_ts and (not ts or ts < since_ts):
            continue
        n += 1
        # tile_trade_row nests the stamp under row["trigger"]; older/flat rows carry it at the top level.
        trig = r.get("trigger") if isinstance(r.get("trigger"), Mapping) else {}
        if (r.get("regime_at_signal") in (None, "")) and (trig.get("regime_at_signal") in (None, "")):
            miss.append(str(r.get("trade_id")))
    return _verdict([], [f"REGIME_MISSING {len(miss)}/{n} e.g. {miss[:5]}"] if miss else [],
                    checked=n, missing=len(miss))


def evaluate(*, now, rows, book_by_sec, chains, v1_since_ts, feed: dict, pnl_reconcile,
             tile_rows=(), regime_since_ts=None) -> dict:
    parts = {
        "regime-coverage": regime_coverage_check(tile_rows, regime_since_ts),
        "fill-recompute": fill_recompute_check(rows, book_by_sec),
        "presence": presence_check(rows, v1_since_ts),
        "live-vs-shadow": live_vs_shadow_check(chains),
        "frozen-feed": feed,
        "pnl": pnl_check(pnl_reconcile),
    }
    worst = max((p["status"] for p in parts.values()), key=lambda s: _ORDER.get(s, 0), default="GREEN")
    return {"schema": SCHEMA, "run_ts": now, "status": worst, "alert": worst == "RED",
            "parts": parts, "fill_detect_lag": fill_detect_lag(chains), "delay_default": delay_default_share(rows)}


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


def append_jsonl(path: str, res: Mapping[str, Any]) -> None:
    """Persist a compact run record (archived with the other runtime jsonl streams)."""
    import json
    try:
        rec = {"run_ts": res.get("run_ts"), "status": res.get("status"),
               "parts": {k: v.get("status") for k, v in (res.get("parts") or {}).items()},
               "reasons": [r for p in (res.get("parts") or {}).values() for r in (p.get("reasons") or [])][:20]}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
