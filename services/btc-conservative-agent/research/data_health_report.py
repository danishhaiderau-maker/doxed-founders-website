"""Data-health panel: coverage %, staleness and row counts per research stream.

One report (``data_health_report.json``) the analyzer, the local research
dashboard and other agents (``/api/streams/health``) read to decide whether a
stream is fit for research before any study uses it. Each logical stream gets:

* ``rows`` / ``rows_24h`` and ``first_ts`` / ``last_ts``;
* ``staleness_sec`` against wall-clock now and ``lag_vs_mirror_head_sec``
  against the newest row of any stream (separates feed staleness from mirror
  lag on the laptop);
* ``coverage_pct_24h`` - observed units over expected units in the trailing
  24 h of the stream's own span (seconds with ``up=1`` for per-second feeds,
  minutes with ``status=OK`` for minute snapshots);
* a ``status`` verdict (OK / DEGRADED / STALE / MISSING) and stream-specific
  quality metrics for the streams repaired on 2026-10-02 (compact_v5 side
  coverage, win_prob population, counterfactual join keys, distinct-trade
  replay completeness, touch-grid fill certainty).

Read-only; nothing here can place, change or cancel an order.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from typing import Callable, Iterable, Optional

import cross_venue_tape as cvt
import market_context_tape as mct

SCHEMA = "data_health_v1"
REPORT_FILE = "data_health_report.json"
WINDOW_SEC = 86400
MAX_DAYS = 7
STALE_SEC = {"second": 180, "minute": 600, "event": 6 * 3600}
DEGRADED_COVERAGE_PCT = 95.0
REPLAY_MAX_BYTES = 400 * 1024 * 1024
HEAD_BYTES = 6000
_BUCKET_RE = re.compile(rb'"bucket_ts":\s*(\d+)')
_FRESH_RE = re.compile(rb'"fresh":\s*true')
_TRADE_ID_RE = re.compile(rb'"trade_id":\s*"([^"]+)"')
_REASON_RE = re.compile(rb'"replay_completion_reason":\s*"([^"]+)"')
_LANE_RE = re.compile(rb'"lane":\s*"([^"]*)"')
_COMPLETE_RE = re.compile(rb'"replay_complete":\s*(true|false)')


def _generations(path: str) -> list:
    rotated = []
    directory, base = os.path.split(path)
    try:
        names = os.listdir(directory or ".")
    except OSError:
        return []
    for name in names:
        if name.startswith(base + ".") and name.rsplit(".", 1)[-1].isdigit():
            rotated.append((int(name.rsplit(".", 1)[-1]), os.path.join(directory, name)))
    return [p for _, p in sorted(rotated)] + ([path] if os.path.isfile(path) else [])


def _iter_json(paths: Iterable[str]):
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        yield row
        except OSError:
            continue


def _verdict(last_ts: Optional[float], now: float, cadence: str, coverage: Optional[float]) -> str:
    if last_ts is None:
        return "MISSING"
    if now - last_ts > STALE_SEC[cadence]:
        return "STALE"
    if coverage is not None and coverage < DEGRADED_COVERAGE_PCT:
        return "DEGRADED"
    return "OK"


def _stream(name: str, *, cadence: str, rows: int, rows_24h: int, first_ts, last_ts, now: float,
            coverage: Optional[float], source: str, extra: Optional[dict] = None) -> dict:
    return {
        "stream": name,
        "source_file": source,
        "cadence": cadence,
        "rows": int(rows),
        "rows_24h": int(rows_24h),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "staleness_sec": None if last_ts is None else round(now - last_ts, 1),
        "coverage_pct_24h": None if coverage is None else round(min(coverage, 100.0), 2),
        "status": _verdict(last_ts, now, cadence, coverage),
        **(extra or {}),
    }


def _pct(num: float, den: float) -> Optional[float]:
    return None if not den else 100.0 * num / den


# ---------------------------------------------------------------------------
# Market-context collector streams
# ---------------------------------------------------------------------------
def load_market_context_rows(data_dir: str, max_days: int = MAX_DAYS) -> list:
    rows = {}
    for row in _iter_json(_generations(os.path.join(data_dir, mct.FILE_NAME))):
        if row.get("schema") == mct.SCHEMA:
            rows[int(row.get("minute_ts") or 0)] = row
    if not rows:
        return []
    floor = max(rows) - max_days * 86400
    return [rows[k] for k in sorted(rows) if k >= floor]


def load_liquidations(data_dir: str, start: float = 0.0) -> list:
    out = [r for r in _iter_json(_generations(os.path.join(data_dir, mct.LIQ_FILE_NAME)))
           if r.get("schema") == mct.LIQ_SCHEMA and float(r.get("ts") or 0) >= start]
    out.sort(key=lambda r: float(r.get("ts") or 0))
    return out


MC_STREAMS = (
    ("coinbase_1s", "second"), ("binance_spot_1s", "second"), ("coinbase_premium_vs_bfx_1s", "second"),
    ("coinbase_usdt_basis_1m", "minute"), ("session_macro_flags_1m", "minute"), ("trailing_regime_1m", "minute"),
)


def market_context_streams(rows: list, liqs: list, now: float) -> list:
    out = []
    src = mct.FILE_NAME
    if not rows:
        for name, cadence in MC_STREAMS:
            out.append(_stream(name, cadence=cadence, rows=0, rows_24h=0, first_ts=None, last_ts=None,
                               now=now, coverage=None, source=src))
        for v in mct.DERIV_VENUES:
            out.append(_stream(f"derivatives_{v}_1m", cadence="minute", rows=0, rows_24h=0, first_ts=None,
                               last_ts=None, now=now, coverage=None, source=src))
        for v in mct.LIQ_VENUES:
            out.append(_stream(f"liquidations_{v}", cadence="second", rows=0, rows_24h=0, first_ts=None,
                               last_ts=None, now=now, coverage=None, source=mct.LIQ_FILE_NAME))
        return out
    first, last = rows[0]["minute_ts"], rows[-1]["minute_ts"] + 60
    window_start = max(first, last - WINDOW_SEC)
    recent = [r for r in rows if r["minute_ts"] >= window_start]
    expected_sec = last - window_start
    expected_min = expected_sec / 60.0

    def spot(r, feed):
        return (r.get("spot") or {}).get(feed) or {}

    for feed in mct.SPOT_FEEDS:
        up = sum(int(spot(r, feed).get("up_sec") or 0) for r in recent)
        last_up = next((r["minute_ts"] + 60 for r in reversed(rows) if int(spot(r, feed).get("up_sec") or 0) > 0), None)
        out.append(_stream(f"{feed}_1s", cadence="second", rows=len(rows) * 60, rows_24h=up, first_ts=first,
                           last_ts=last_up, now=now, coverage=_pct(up, expected_sec), source=src,
                           extra={"unit": "seconds_with_up_mask"}))

    def prem(r):
        return (r.get("premium") or {}).get("coinbase_vs_bfx") or []

    prem_n = sum(sum(1 for v in prem(r) if v is not None) for r in recent)
    last_prem = next((r["minute_ts"] + 60 for r in reversed(rows) if any(v is not None for v in prem(r))), None)
    out.append(_stream("coinbase_premium_vs_bfx_1s", cadence="second", rows=len(rows) * 60, rows_24h=prem_n,
                       first_ts=first, last_ts=last_prem, now=now, coverage=_pct(prem_n, expected_sec),
                       source=src, extra={"unit": "seconds_with_both_mids"}))
    usdt_ok = sum(1 for r in recent if (r.get("usdt_usd") or {}).get("mean") is not None)
    out.append(_stream("coinbase_usdt_basis_1m", cadence="minute", rows=len(rows), rows_24h=usdt_ok,
                       first_ts=first, last_ts=last, now=now, coverage=_pct(usdt_ok, expected_min), source=src))

    def deriv(r, v):
        return (r.get("derivatives") or {}).get(v) or {}

    for v in mct.DERIV_VENUES:
        ok = sum(1 for r in recent if deriv(r, v).get("status") == "OK")
        statuses = Counter(deriv(r, v).get("status") or "MISSING" for r in recent)
        last_ok = next((r["minute_ts"] + 60 for r in reversed(rows) if deriv(r, v).get("status") == "OK"), None)
        out.append(_stream(f"derivatives_{v}_1m", cadence="minute", rows=len(rows), rows_24h=ok,
                           first_ts=first, last_ts=last_ok, now=now, coverage=_pct(ok, expected_min),
                           source=src, extra={"status_counts_24h": dict(statuses)}))
    flags_ok = sum(1 for r in recent if (r.get("flags") or {}).get("calendar_status") == "OK")
    out.append(_stream("session_macro_flags_1m", cadence="minute", rows=len(rows), rows_24h=flags_ok,
                       first_ts=first, last_ts=last, now=now, coverage=_pct(flags_ok, expected_min), source=src,
                       extra={"calendar_versions": sorted({(r.get("flags") or {}).get("calendar_version")
                                                           for r in recent} - {None})}))
    regime_ok = sum(1 for r in recent if (r.get("regime") or {}).get("label") not in (None, "WARMUP"))
    out.append(_stream("trailing_regime_1m", cadence="minute", rows=len(rows), rows_24h=regime_ok,
                       first_ts=first, last_ts=last, now=now, coverage=_pct(regime_ok, expected_min), source=src,
                       extra={"labels_24h": dict(Counter((r.get("regime") or {}).get("label") for r in recent)),
                              "note": "WARMUP for the first day after the stream starts (no full-sample thresholds)"}))

    def liq(r, v):
        return (r.get("liquidations") or {}).get(v) or {}

    for v in mct.LIQ_VENUES:
        up = sum(int(liq(r, v).get("up_sec") or 0) for r in recent)
        ev = [e for e in liqs if e.get("venue") == v]
        ev24 = [e for e in ev if float(e.get("ts") or 0) >= window_start]
        last_up = next((r["minute_ts"] + 60 for r in reversed(rows) if int(liq(r, v).get("up_sec") or 0) > 0), None)
        out.append(_stream(f"liquidations_{v}", cadence="second", rows=len(ev), rows_24h=len(ev24),
                           first_ts=float(ev[0]["ts"]) if ev else None, last_ts=last_up, now=now,
                           coverage=_pct(up, expected_sec), source=mct.LIQ_FILE_NAME,
                           extra={"unit": "feed_up_seconds", "last_event_ts": float(ev[-1]["ts"]) if ev else None,
                                  "notional_usd_24h": round(sum(float(e.get("notional_usd") or 0) for e in ev24), 2),
                                  "completeness": sorted({e.get("completeness") for e in ev} - {None})}))
    return out


# ---------------------------------------------------------------------------
# Existing streams (taker flow up-mask, Bitfinex tape) and repaired streams
# ---------------------------------------------------------------------------
def cross_venue_streams(data_dir: str, now: float) -> list:
    rows = {}
    for row in _iter_json(_generations(os.path.join(data_dir, cvt.FILE_NAME))):
        if row.get("schema") == cvt.SCHEMA:
            rows[int(row.get("minute_ts") or 0)] = row
    out = []
    if not rows:
        for v in cvt.VENUES:
            out.append(_stream(f"taker_flow_cvd_{v}_1s", cadence="second", rows=0, rows_24h=0, first_ts=None,
                               last_ts=None, now=now, coverage=None, source=cvt.FILE_NAME))
        return out
    keys = sorted(rows)
    first, last = keys[0], keys[-1] + 60
    window_start = max(first, last - WINDOW_SEC)
    recent = [rows[k] for k in keys if k >= window_start]
    expected = last - window_start

    def cell(r, v):
        return (r.get("venues") or {}).get(v) or {}

    for v in cvt.VENUES:
        masked = [r for r in recent if isinstance(cell(r, v).get("up"), str)]
        up = sum(cell(r, v)["up"].count("1") for r in masked)
        mids = sum(sum(1 for x in cell(r, v).get("dm") or [] if x is not None) for r in recent)
        unmasked_sec = (len(recent) - len(masked)) * 60
        out.append(_stream(f"taker_flow_cvd_{v}_1s", cadence="second", rows=len(rows) * 60, rows_24h=up,
                           first_ts=first, last_ts=last, now=now,
                           coverage=_pct(up, len(masked) * 60) if masked else None,
                           source=cvt.FILE_NAME,
                           extra={"unit": "seconds_with_connection_up",
                                  "seconds_without_mask_24h": unmasked_sec,
                                  "mask_note": "rows written before 2026-10-02 have no up mask: zero flow there is unknown",
                                  "mid_coverage_pct_24h": None if not expected else round(100.0 * mids / expected, 2)}))
    return out


def _bucket_range(path: str) -> tuple:
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 4096))
            tail = handle.read()
    except OSError:
        return None, None
    first = _BUCKET_RE.search(head)
    last = _BUCKET_RE.findall(tail)
    return (int(first.group(1)) if first else None, int(last[-1]) if last else None)


def bitfinex_tape_stream(data_dir: str, now: float) -> dict:
    gens = _generations(os.path.join(data_dir, "market_microstructure_1s.jsonl"))
    ranges = {g: _bucket_range(g) for g in gens}
    firsts = [r[0] for r in ranges.values() if r[0] is not None]
    lasts = [r[1] for r in ranges.values() if r[1] is not None]
    first, last = (min(firsts) if firsts else None), (max(lasts) if lasts else None)
    total = rows24 = fresh24 = 0
    coverage = None
    if last is not None:
        window_start = max(first, last - WINDOW_SEC)
        for g in gens:
            lo, hi = ranges[g]
            in_window = hi is None or hi >= window_start
            try:
                with open(g, "rb") as handle:
                    for line in handle:
                        total += 1
                        if not in_window:
                            continue
                        m = _BUCKET_RE.search(line)
                        if m and int(m.group(1)) >= window_start:
                            rows24 += 1
                            fresh24 += 1 if _FRESH_RE.search(line) else 0
            except OSError:
                continue
        coverage = _pct(fresh24, last - window_start)
    return _stream("bitfinex_bbo_1s", cadence="second", rows=total, rows_24h=rows24, first_ts=first,
                   last_ts=last, now=now, coverage=coverage, source="market_microstructure_1s.jsonl",
                   extra={"unit": "fresh_seconds"})


def signal_replay_health(data_dir: str, max_bytes: int = REPLAY_MAX_BYTES) -> dict:
    """Distinct-trade completeness from the newest replay generations (row heads only)."""
    gens = _generations(os.path.join(data_dir, "signal_replay.jsonl"))
    picked, size = [], 0
    for g in reversed(gens):
        try:
            size += os.path.getsize(g)
        except OSError:
            continue
        picked.append(g)
        if size >= max_bytes:
            break
    per_trade: dict = {}
    rows = 0
    reasons = Counter()
    for g in reversed(picked):
        try:
            with open(g, "rb") as handle:
                for line in handle:
                    head = line[:HEAD_BYTES]
                    tid = _TRADE_ID_RE.search(head)
                    if not tid:
                        continue
                    rows += 1
                    reason = _REASON_RE.search(head)
                    lane = _LANE_RE.search(head)
                    complete = _COMPLETE_RE.search(head)
                    r = reason.group(1).decode() if reason else "UNKNOWN"
                    reasons[r] += 1
                    c = per_trade.setdefault(tid.group(1).decode(), {"complete": False, "reasons": set(), "lane": None})
                    c["complete"] |= bool(complete and complete.group(1) == b"true")
                    c["reasons"].add(r)
                    c["lane"] = lane.group(1).decode() if lane else c["lane"]
        except OSError:
            continue
    distinct = len(per_trade)
    complete = sum(1 for c in per_trade.values() if c["complete"])
    censored = sum(1 for c in per_trade.values()
                   if not c["complete"] and "CENSORED_PROCESS_SHUTDOWN" in c["reasons"])
    by_lane: dict = {}
    for c in per_trade.values():
        cell = by_lane.setdefault(c["lane"] or "UNKNOWN", {"distinct": 0, "complete": 0})
        cell["distinct"] += 1
        cell["complete"] += int(c["complete"])
    return {
        "stream": "signal_replay",
        "source_file": "signal_replay.jsonl",
        "generations_scanned": len(picked),
        "rows_scanned": rows,
        "row_complete_pct": _round(_pct(sum(v for k, v in reasons.items() if k in (
            "POST_EXIT_HORIZON_COMPLETE", "FILL_ORIGIN_BUFFER_CLOSED")), rows)),
        "distinct_trades": distinct,
        "distinct_complete": complete,
        "distinct_complete_pct": _round(_pct(complete, distinct)),
        "distinct_censored_shutdown": censored,
        "row_reasons": dict(reasons),
        "by_lane": by_lane,
        "note": "Per distinct trade_id: a restart re-dumps a buffer, so row counts overstate gaps.",
    }


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def _not_none(side) -> bool:
    return side not in (None, "NONE")


def repaired_stream_quality(data_dir: str) -> dict:
    calls = [r for r in _iter_json(_generations(os.path.join(data_dir, "ai_shadow_challengers.jsonl")))
             if r.get("row_kind") == "CALL"]
    v2 = [r for r in calls if (r.get("compact") or {}).get("side_rule")]
    legacy = [r for r in calls if not (r.get("compact") or {}).get("side_rule")]
    out = {
        "compact_v5": {
            "calls": len(calls),
            "calls_with_side_rule": len(v2),
            "side_non_none_pct_v2": _round(_pct(sum(1 for r in v2 if _not_none((r.get("sides") or {}).get("compact_v5"))), len(v2))),
            "side_non_none_pct_legacy": _round(_pct(sum(1 for r in legacy if _not_none((r.get("sides") or {}).get("compact_v5"))), len(legacy))),
        },
        "win_prob": {
            "calls": len(calls),
            "populated_pct": _round(_pct(sum(1 for r in calls if r.get("win_prob") not in (None, 0)), len(calls))),
            "status_counts": dict(Counter(r.get("win_prob_status") for r in calls)),
        },
    }
    cf = list(_iter_json(_generations(os.path.join(data_dir, "counterfactual.jsonl"))))
    out["counterfactual"] = {"rows": len(cf)}
    for key in ("ts", "signal_ts", "shared_ai_call_id", "epoch_id", "opportunity_id"):
        out["counterfactual"][f"with_{key}_pct"] = _round(_pct(sum(1 for r in cf if r.get(key)), len(cf)))
    grid = [r for r in _iter_json(_generations(os.path.join(data_dir, "chase_offset_touch_grid.jsonl"))[-2:])
            if r.get("event") in ("TOUCHED", "CERTAIN_TOUCH")]
    touched = [r for r in grid if r.get("event") == "TOUCHED"]
    flagged = [r for r in touched if r.get("touch_flags_version")]
    at_limit = [r for r in flagged if r.get("fill_certainty") == "UNCERTAIN_AT_LIMIT_QUEUE"]
    out["touch_grid"] = {
        "touched_rows": len(touched),
        "touched_rows_with_flags": len(flagged),
        "fill_certainty_counts": dict(Counter(r.get("fill_certainty") for r in flagged)),
        "certain_pct_of_flagged": _round(_pct(sum(1 for r in flagged if str(r.get("fill_certainty") or "").startswith("CERTAIN")), len(flagged))),
        "later_certain_rows": sum(1 for r in grid if r.get("event") == "CERTAIN_TOUCH"),
        "queue_ahead_known_pct_of_at_limit": _round(_pct(sum(1 for r in at_limit if r.get("queue_ahead_upper_btc") is not None), len(at_limit))),
    }
    return out


def build_data_health(data_dir: str, now: Optional[float] = None,
                      clock: Callable[[], float] = time.time) -> dict:
    now = clock() if now is None else float(now)
    mc_rows = load_market_context_rows(data_dir)
    liqs = load_liquidations(data_dir, start=(mc_rows[0]["minute_ts"] if mc_rows else 0.0))
    streams = (market_context_streams(mc_rows, liqs, now) + cross_venue_streams(data_dir, now)
               + [bitfinex_tape_stream(data_dir, now)])
    head = max((s["last_ts"] for s in streams if s.get("last_ts")), default=None)
    for s in streams:
        s["lag_vs_mirror_head_sec"] = (None if head is None or s.get("last_ts") is None
                                       else round(head - s["last_ts"], 1))
    counts = Counter(s["status"] for s in streams)
    meta = (mc_rows[-1].get("meta") or {}) if mc_rows else {}
    return {
        "schema": SCHEMA,
        "generated_ts": round(now, 3),
        "window_sec": WINDOW_SEC,
        "mirror_head_ts": head,
        "status_counts": dict(counts),
        "status": "OK" if set(counts) <= {"OK"} else "ATTENTION",
        "streams": streams,
        "signal_replay": signal_replay_health(data_dir),
        "repaired_streams": repaired_stream_quality(data_dir),
        "market_context_collector": {
            "collector_versions": sorted({(r.get("meta") or {}).get("collector_version") for r in mc_rows} - {None}),
            "cpu_pct_last": meta.get("cpu_pct"),
            "rss_mb_last": meta.get("rss_mb"),
            "reconnects_last": meta.get("reconnects"),
        },
    }
