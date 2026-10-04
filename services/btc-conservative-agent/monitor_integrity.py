"""Read-only integrity telemetry for the monitor (``GET /api/monitor/integrity``).

Closes the 2026-10-04 API census gaps that were otherwise only visible in logs
or by ssh (see docs/grokbot/API-GAPS-20261004.md):

* ``tape_1s``: continuity of the 1 s microstructure tape (missing / failed /
  stale / late buckets per window, the longest gaps, the restart gap). Until
  now the API only exposed the last bucket age and a since-boot row counter,
  so a dropped minute or a restart hole was invisible.
* ``ai_calls``: a rolling window of AI provider outcomes (success rate,
  latency p50/p95/max, error-class counts). ``ai_provider_health`` only carries
  the *last* latency and the *last* error class.
* ``fill_quality``: paper-fill markouts aggregated per lane and liquidity
  (mean mid / exit-touch markout in bp at each horizon). Fly already writes
  ``fill_markouts.jsonl`` but the API only exposed row counters, so drift
  between the paper fill model and what the market did next was invisible.
* ``process``: the Fly machine/release identity (image ref, machine version)
  next to the git rev, so a deploy that did not roll the machine is visible.

Pure module: no Flask, no orders, no network. The only I/O is the bounded tail
read in :func:`read_last_bucket_ts`, called once at tape-loop start. Every
observer swallows its own errors so it can never break the caller (the 1 s
capture loop or the AI call path). Paper/live behaviour is unchanged.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

SCHEMA = "monitor_integrity_v1"
MAX_BYTES = 16 * 1024
WINDOWS_SEC = (("1h", 3600), ("24h", 86400))
TAPE_MINUTES_KEPT = 24 * 60 + 5
TAPE_LATE_AFTER_SEC = 2.0
TAPE_GAPS_KEPT = 200
TAPE_GAPS_SHOWN = 10
AI_OUTCOMES_KEPT = 4000
FILL_ROWS_KEPT = 2000
FILL_GROUPS_SHOWN = 24
TAIL_READ_BYTES = 64 * 1024
FLY_IDENTITY_ENV = ("FLY_APP_NAME", "FLY_REGION", "FLY_MACHINE_ID", "FLY_MACHINE_VERSION",
                    "FLY_IMAGE_REF", "FLY_PROCESS_GROUP", "FLY_VM_MEMORY_MB")
_BUCKET_RE = re.compile(rb'"bucket_ts"\s*:\s*(\d{9,11})')


def utc_iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def read_last_bucket_ts(path: str, max_bytes: int = TAIL_READ_BYTES) -> Optional[int]:
    """Last ``bucket_ts`` already on disk (bounded tail read; None if unknown)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - int(max_bytes)))
            tail = fh.read(int(max_bytes))
    except (OSError, ValueError):
        return None
    found = _BUCKET_RE.findall(tail)
    return int(found[-1]) if found else None


class TapeContinuityTracker:
    """Per-minute counters for the 1 s tape plus a bounded list of gaps.

    ``observe`` is O(1) and is called once per bucket by the capture loop.
    A *gap* is a run of seconds with no row on disk between two written
    buckets (write failures, loop skips) or across a restart (``boot_gap``).
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._minutes: "OrderedDict[int, list]" = OrderedDict()  # minute -> [written, failed, stale, late]
        self._gaps: deque = deque(maxlen=TAPE_GAPS_KEPT)
        self._first_bucket: Optional[int] = None
        self._last_bucket: Optional[int] = None
        self._last_written: Optional[int] = None
        self._fail_since_written = 0
        self._prior_last_bucket: Optional[int] = None
        self._primed = False
        self.boot_gap: Optional[dict] = None
        self.observe_errors = 0

    def prime(self, prior_last_bucket: Optional[int]) -> None:
        """Last bucket written by the previous process (read once at loop start)."""
        with self._lock:
            self._prior_last_bucket = int(prior_last_bucket) if prior_last_bucket else None
            self._primed = True

    def observe(self, bucket_ts: int, *, written: bool, fresh: Any = None, valid_bbo: Any = None,
                observed_at: Optional[float] = None) -> None:
        try:
            ts = int(bucket_ts)
            now = float(self._clock() if observed_at is None else observed_at)
            late = (now - (ts + 1.0)) > TAPE_LATE_AFTER_SEC
            stale = bool(written) and (fresh is False or valid_bbo is False)
            with self._lock:
                if self._first_bucket is None:
                    self._first_bucket = ts
                    prior = self._prior_last_bucket
                    if prior and ts > prior + 1:
                        self.boot_gap = {"from_utc": utc_iso(prior + 1), "to_utc": utc_iso(ts - 1),
                                         "seconds": ts - prior - 1, "kind": "RESTART"}
                self._last_bucket = ts if self._last_bucket is None else max(self._last_bucket, ts)
                row = self._minutes.get(ts // 60)
                if row is None:
                    row = self._minutes[ts // 60] = [0, 0, 0, 0]
                    while len(self._minutes) > TAPE_MINUTES_KEPT:
                        self._minutes.popitem(last=False)
                if written:
                    row[0] += 1
                    if self._last_written is not None and ts > self._last_written + 1:
                        self._gaps.append((self._last_written + 1, ts - self._last_written - 1,
                                           "WRITE_FAIL" if self._fail_since_written else "SKIP"))
                    self._last_written = ts if self._last_written is None else max(self._last_written, ts)
                    self._fail_since_written = 0
                else:
                    row[1] += 1
                    self._fail_since_written += 1
                if stale:
                    row[2] += 1
                if late:
                    row[3] += 1
        except Exception:
            self.observe_errors += 1

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            minutes = list(self._minutes.items())
            gaps = list(self._gaps)
            first, last, last_written = self._first_bucket, self._last_bucket, self._last_written
            boot_gap = dict(self.boot_gap) if self.boot_gap else None
            primed, prior = self._primed, self._prior_last_bucket
        out = {
            "source": "market_microstructure_1s.jsonl (in-process writer counters)",
            "tracking_since_utc": utc_iso(first),
            "last_bucket_utc": utc_iso(last),
            "last_written_age_sec": round(now - (last_written + 1), 1) if last_written else None,
            "boot_gap": boot_gap,
            "prior_process_last_bucket_utc": utc_iso(prior) if primed else None,
            "observe_errors": self.observe_errors,
            "windows": {},
        }
        end = int(now) - 1
        for label, span in WINDOWS_SEC:
            # Whole minutes so the expected count and the per-minute counters cover the same seconds.
            start = (end - span + 1) // 60 * 60
            covered_start = max(start, first) if first is not None else None
            expected = (end - covered_start + 1) if covered_start is not None and end >= covered_start else 0
            written = failed = stale = late = 0
            for minute, (w, f, s, l) in minutes:
                if (minute + 1) * 60 <= start or minute * 60 > end:
                    continue
                written += w
                failed += f
                stale += s
                late += l
            in_window = [g for g in gaps if g[0] + g[1] - 1 >= start]
            missing = max(0, expected - written)
            out["windows"][label] = {
                "expected_buckets": expected,
                "written": written,
                "missing": missing,
                "write_failures": failed,
                "stale_or_invalid": stale,
                "late_written": late,
                "coverage_pct": round(100.0 * written / expected, 3) if expected else None,
                "gaps": len(in_window),
                "longest_gap_sec": max((g[1] for g in in_window), default=0),
                "window_complete": bool(first is not None and first <= start),
            }
        out["recent_gaps"] = [
            {"from_utc": utc_iso(s), "seconds": n, "kind": k}
            for s, n, k in sorted(gaps, key=lambda g: g[0], reverse=True)[:TAPE_GAPS_SHOWN]
        ]
        one_h = out["windows"]["1h"]
        if first is None:
            out["status"] = "NO_DATA"
        elif (out["last_written_age_sec"] or 0) > 10:
            out["status"] = "STALLED"
        elif one_h["missing"] > 60 or one_h["longest_gap_sec"] > 30:
            out["status"] = "GAPPY"
        elif one_h["missing"] or one_h["stale_or_invalid"] > 60:
            out["status"] = "DEGRADED"
        else:
            out["status"] = "OK"
        return out


def _percentile(values: list, pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    value = ordered[int(k)] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)
    return round(float(value), 1)


class AiCallWindow:
    """Bounded rolling window of AI provider outcomes (no prompts, no bodies)."""

    def __init__(self, clock: Callable[[], float] = time.time, maxlen: int = AI_OUTCOMES_KEPT):
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: deque = deque(maxlen=maxlen)
        self.observe_errors = 0

    def observe(self, *, ok: bool, latency_ms: Any = None, error_class: Optional[str] = None,
                now: Optional[float] = None) -> None:
        try:
            ts = float(self._clock() if now is None else now)
            try:
                lat = float(latency_ms) if latency_ms is not None else None
                if lat is not None and not math.isfinite(lat):
                    lat = None
            except (TypeError, ValueError):
                lat = None
            with self._lock:
                self._rows.append((ts, bool(ok), lat, (str(error_class)[:40] if error_class else None)))
        except Exception:
            self.observe_errors += 1

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            rows = list(self._rows)
        out = {"window_capacity": self._rows.maxlen, "observed_since_boot": len(rows),
               "oldest_utc": utc_iso(rows[0][0]) if rows else None, "windows": {}}
        for label, span in WINDOWS_SEC:
            sel = [r for r in rows if r[0] >= now - span]
            ok = [r for r in sel if r[1]]
            lat = [r[2] for r in ok if r[2] is not None]
            classes: dict = {}
            for r in sel:
                if not r[1]:
                    key = r[3] or "UNKNOWN"
                    classes[key] = classes.get(key, 0) + 1
            out["windows"][label] = {
                "calls": len(sel), "ok": len(ok), "failed": len(sel) - len(ok),
                "success_rate": round(len(ok) / len(sel), 4) if sel else None,
                "latency_ms_p50": _percentile(lat, 50), "latency_ms_p95": _percentile(lat, 95),
                "latency_ms_max": round(max(lat), 1) if lat else None,
                "error_classes": dict(sorted(classes.items(), key=lambda kv: -kv[1])[:12]),
                "window_complete": bool(rows and rows[0][0] <= now - span) or len(rows) == self._rows.maxlen,
            }
        last_fail = next((r for r in reversed(rows) if not r[1]), None)
        out["last_failure_utc"] = utc_iso(last_fail[0]) if last_fail else None
        out["last_failure_class"] = last_fail[3] if last_fail else None
        return out


class FillMarkoutAggregator:
    """Bounded window of completed paper-fill markout rows, summarised per lane x liquidity."""

    def __init__(self, maxlen: int = FILL_ROWS_KEPT):
        self._lock = threading.Lock()
        self._rows: deque = deque(maxlen=maxlen)
        self.observe_errors = 0

    def observe(self, row: Mapping[str, Any], now: Optional[float] = None) -> None:
        try:
            marks = {}
            for label, sample in (row.get("markouts") or {}).items():
                if isinstance(sample, Mapping):
                    marks[str(label)[:8]] = (sample.get("markout_mid_bps"), sample.get("markout_exit_touch_bps"),
                                             bool(sample.get("on_time")))
            item = (float(time.time() if now is None else now), str(row.get("research_lane") or "UNKNOWN")[:64],
                    str(row.get("liquidity") or "UNKNOWN")[:8], marks)
            with self._lock:
                self._rows.append(item)
        except Exception:
            self.observe_errors += 1

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = float(time.time() if now is None else now)
        with self._lock:
            rows = list(self._rows)
        groups: dict = {}
        for ts, lane, liq, marks in rows:
            grp = groups.setdefault((lane, liq), {"n": 0, "n_24h": 0, "h": {}})
            grp["n"] += 1
            grp["n_24h"] += ts >= now - 86400
            for label, (mid, touch, on_time) in marks.items():
                h = grp["h"].setdefault(label, {"mid": [], "touch": [], "late": 0})
                if isinstance(mid, (int, float)) and math.isfinite(mid):
                    h["mid"].append(float(mid))
                if isinstance(touch, (int, float)) and math.isfinite(touch):
                    h["touch"].append(float(touch))
                h["late"] += not on_time
        out_groups = []
        for (lane, liq), grp in sorted(groups.items(), key=lambda kv: -kv[1]["n"])[:FILL_GROUPS_SHOWN]:
            horizons = {}
            for label, h in sorted(grp["h"].items(), key=lambda kv: float(kv[0].rstrip("s") or 0)):
                horizons[label] = {
                    "mean_mid_bps": round(sum(h["mid"]) / len(h["mid"]), 2) if h["mid"] else None,
                    "mean_exit_touch_bps": round(sum(h["touch"]) / len(h["touch"]), 2) if h["touch"] else None,
                    "late_samples": h["late"],
                }
            out_groups.append({"lane": lane, "liquidity": liq, "fills": grp["n"], "fills_24h": grp["n_24h"],
                               "horizons": horizons})
        return {"source": "fill_markouts.jsonl rows (in-process, since boot)", "fills_observed": len(rows),
                "window_capacity": self._rows.maxlen, "groups": out_groups,
                "note": "mean markout of the paper fill price vs the market mid / exit touch after each horizon; "
                        "1s exit-touch ~ immediate round-trip cost vs the book; compare with the modelled slippage/fees"}


def process_identity(environ: Optional[Mapping[str, str]] = None, *, pid: Optional[int] = None,
                     git_rev: Optional[str] = None, boot_id: Optional[str] = None,
                     process_started_ts: Optional[float] = None) -> dict:
    env = os.environ if environ is None else environ
    fly = {key.lower(): str(env.get(key))[:160] for key in FLY_IDENTITY_ENV if env.get(key)}
    return {"git_rev": git_rev, "boot_id": boot_id, "pid": pid,
            "process_started_utc": utc_iso(process_started_ts), "fly": fly or None}


def is_authorized(*, admin_ok: bool, authorization: Optional[str], monitor_token: str,
                  bearer_matches: Callable[[Optional[str], str], bool]) -> bool:
    """Admin header/cookie, or the read-only monitor bearer token (never the reverse)."""
    if admin_ok:
        return True
    return bool(monitor_token) and bool(bearer_matches(authorization, monitor_token))


def build_payload(*, now: float, tape: TapeContinuityTracker, ai: AiCallWindow, identity: Mapping[str, Any],
                  fills: Optional[FillMarkoutAggregator] = None, extras: Optional[Mapping[str, Callable[[], Any]]] = None,
                  fit: Optional[Callable[[dict, int], dict]] = None) -> dict:
    payload: dict = {"schema": SCHEMA, "generated_at": utc_iso(now), "read_only": True,
                     "process": dict(identity)}
    sections = [("tape_1s", lambda: tape.snapshot(now)), ("ai_calls", lambda: ai.snapshot(now))]
    if fills is not None:
        sections.append(("fill_quality", lambda: fills.snapshot(now)))
    for name, build in sections:
        try:
            payload[name] = build()
        except Exception as exc:  # one section failing never hides the others
            payload[name] = {"status": "UNKNOWN", "error": type(exc).__name__}
    for name, build in (extras or {}).items():
        try:
            payload[name] = build()
        except Exception as exc:
            payload[name] = {"status": "UNKNOWN", "error": type(exc).__name__}
    statuses = [payload.get("tape_1s", {}).get("status")]
    ai_1h = (payload.get("ai_calls", {}).get("windows") or {}).get("1h") or {}
    payload["verdict"] = {
        "tape_1s": statuses[0],
        "ai_success_rate_1h": ai_1h.get("success_rate"),
        "ai_latency_ms_p95_1h": ai_1h.get("latency_ms_p95"),
    }
    return fit(payload, MAX_BYTES) if fit else payload
