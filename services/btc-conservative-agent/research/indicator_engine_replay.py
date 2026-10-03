"""Replay the real indicator engine over mirrored Fly inputs (laptop benchmark).

Copies nothing from the mirror in place: input rows are streamed in the order
they become visible on Fly (tape second + 1 s, cross-venue minute + 63 s,
market-context minute + 64 s, AI snapshot at its decision time) and appended
to a scratch runtime directory while :class:`indicator_engine.Engine` runs on a
simulated clock through its normal warm start and file tails. Reports the
per-bar compute time, engine CPU per simulated day, peak memory, bytes per day
and the AVAILABLE / WARMING_UP / UNAVAILABLE census of the last row.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import re
import sys
import time
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import indicator_edge_spec as spec  # noqa: E402
import indicator_engine as ie  # noqa: E402

DEFAULT_MIRROR = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_WORK_ROOT = r"C:\DoxxedCrypto\tmp-indicator-edge"
SOURCES = (
    ("market_microstructure_1s.jsonl", re.compile(rb'"bucket_ts":\s*(\d+)'), 1.0),
    ("cross_venue_tape_1m.jsonl", re.compile(rb'"minute_ts":\s*(\d+)'), 63.0),
    ("market_context_1m.jsonl", re.compile(rb'"minute_ts":\s*(\d+)'), 64.0),
    ("decision_feature_snapshots.jsonl", re.compile(rb'"decision_ts":\s*([0-9.]+)'), 0.0),
)


def _stream(mirror: str, name: str, ts_re: re.Pattern, lag: float):
    for path in ie.rotation_paths(os.path.join(mirror, name)):
        with open(path, "rb") as handle:
            for line in handle:
                match = ts_re.search(line)
                if match:
                    yield float(match.group(1)) + lag, name, line if line.endswith(b"\n") else line + b"\n"


def _rss_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:
        return None


def replay(mirror: str, work: str, warm_hours: float, live_hours: float | None, step_sec: float = 2.0) -> dict:
    os.makedirs(work, exist_ok=True)
    for name, _, _ in SOURCES:
        if os.path.exists(os.path.join(work, name)):
            raise SystemExit(f"scratch dir not empty: {work}")
    merged = heapq.merge(*(_stream(mirror, n, r, lag) for n, r, lag in SOURCES), key=lambda item: item[0])
    handles = {n: open(os.path.join(work, n), "ab") for n, _, _ in SOURCES}
    first = None
    pending = None
    try:
        for item in merged:
            first = item[0] if first is None else first
            if item[0] >= first + warm_hours * 3600:
                pending = item
                break
            handles[item[1]].write(item[2])
        for h in handles.values():
            h.flush()
        if pending is None:
            raise SystemExit("not enough data for the warm window")
        clock = {"now": pending[0]}
        engine = ie.Engine(work, clock=lambda: clock["now"])
        rss0 = _rss_mb()
        cpu0 = time.process_time()
        engine.warm_start()
        warm_cpu = time.process_time() - cpu0
        peak = _rss_mb()
        start = now = clock["now"]
        end = None if live_hours is None else start + live_hours * 3600
        engine_cpu = 0.0
        compute = []
        rows_seen = 0
        idle_since = None
        while True:
            now += step_sec
            if end is not None and now > end:
                break
            while pending is not None and pending[0] <= now:
                handles[pending[1]].write(pending[2])
                pending = next(merged, None)
            for h in handles.values():
                h.flush()
            if pending is None:
                idle_since = idle_since or now
                if now - idle_since > 600:
                    break
            clock["now"] = now
            c0 = time.process_time()
            engine.tick(now)
            engine_cpu += time.process_time() - c0
            if engine.stats["rows_written"] > rows_seen:
                rows_seen = engine.stats["rows_written"]
                compute.append(engine.stats["compute_ms_last"])
                rss = _rss_mb()
                if rss is not None and (peak is None or rss > peak):
                    peak = rss
    finally:
        for h in handles.values():
            h.close()
    sim_days = max(1e-9, (now - start) / 86400.0)
    last = engine.last_row or {}
    out_bytes = os.path.getsize(engine.out_path) if os.path.exists(engine.out_path) else 0
    compute_sorted = sorted(c for c in compute if c is not None)

    def q(p):
        return None if not compute_sorted else compute_sorted[min(len(compute_sorted) - 1, int(p * len(compute_sorted)))]

    census = {"AVAILABLE": [], "WARMING_UP": [], "UNAVAILABLE": []}
    for fid, vals in (last.get("f") or {}).items():
        census[spec.STATUS_NAMES.get(vals[3], "WARMING_UP")].append(fid)
    return {
        "schema": "indicator_engine_replay_v1",
        "warm_hours": warm_hours,
        "simulated_live_hours": round((now - start) / 3600.0, 2),
        "warm": engine.stats["warm"],
        "warm_cpu_sec": round(warm_cpu, 2),
        "rows": engine.stats["rows_written"],
        "late_rows": engine.stats["late_rows_written"],
        "input_wait_timeouts": engine.stats["input_wait_timeouts"],
        "compute_ms": {"p50": q(0.5), "p95": q(0.95), "max": compute_sorted[-1] if compute_sorted else None},
        "engine_cpu_sec_per_day": round(engine_cpu / sim_days, 1),
        "engine_cpu_pct_of_one_core": round(engine_cpu / (sim_days * 86400.0) * 100.0, 3),
        "rss_mb": {"before_warm": rss0, "peak": peak},
        "bytes_per_row": round(out_bytes / max(1, engine.stats["rows_written"]), 1),
        "bytes_per_day": round(out_bytes / sim_days),
        "emitted_lag_sec_last": last.get("emitted_lag_sec"),
        "last_health": last.get("health"),
        "last_status_counts": last.get("status_counts"),
        "last_regime": last.get("regime"),
        "census": census,
        "feature_set_sha": spec.feature_set_sha(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mirror", default=DEFAULT_MIRROR)
    ap.add_argument("--work", default=None, help="empty scratch runtime dir (never the mirror)")
    ap.add_argument("--warm-hours", type=float, default=48.0)
    ap.add_argument("--live-hours", type=float, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    work = args.work or os.path.join(DEFAULT_WORK_ROOT, f"replay-{int(time.time())}")
    if os.path.abspath(work).startswith(os.path.abspath(args.mirror)):
        raise SystemExit("refusing to write inside the mirror")
    report = replay(args.mirror, work, args.warm_hours, args.live_hours)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    census = report.pop("census")
    print(json.dumps(report, indent=2))
    print(json.dumps({k: len(v) for k, v in census.items()}))
    for k in ("WARMING_UP", "UNAVAILABLE"):
        print(k, census[k])
    return 0


if __name__ == "__main__":
    sys.exit(main())
