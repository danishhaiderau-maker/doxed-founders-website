"""Uptime: how long paper trading has run uninterrupted, from the watcher's per-tick verdicts.

An interruption is a watcher tick where Fly was unreachable (``fly.process``)
or paper was paused (``fly.paused``), or a Fly process restart seen in the
runtime snapshot. Gaps between ticks longer than 20 min are reported as
unobserved time, never silently counted as up.
"""
from __future__ import annotations

import time

import pandas as pd

from .facts import iso, parse_ts

GAP_SEC = 20 * 60
DOWN_CHECKS = ("fly.process", "fly.paused")


def compute(store, facts: dict, state: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rows = []
    try:
        rows = store.read('SELECT "at", verdict, failing FROM raw_verdicts WHERE "at" >= ? ORDER BY "at"',
                          [iso(now - 8 * 86400)])
    except Exception:  # noqa: BLE001
        rows = []
    rt = facts.get("runtime") or {}
    sp = rt.get("strategy_progress") or {}
    starts = state.setdefault("process_starts", [])
    observed = parse_ts(rt.get("observedAt"))
    age = sp.get("process_startup_age_sec")
    if observed and isinstance(age, (int, float)):
        start = round(observed - age)
        if not starts or abs(starts[-1] - start) > 120:
            starts.append(start)
            del starts[:-200]

    ticks = []
    for r in rows:
        t = parse_ts(r["at"])
        if t is None:
            continue
        failing = r.get("failing") or []
        down = [c for c in DOWN_CHECKS if c in failing]
        ticks.append((t, down))
    intervals = []  # (start, end, cause)
    unobserved = []
    for (t_prev, d_prev), (t, d) in zip(ticks, ticks[1:]):
        if t - t_prev > GAP_SEC:
            unobserved.append((t_prev, t))
        if d:
            intervals.append((t_prev if d_prev else t, t, ",".join(d)))
    for s in starts:
        if s >= now - 8 * 86400:
            intervals.append((s - 1, s, "fly_process_restart"))
    merged = []
    for s, e, c in sorted(intervals):
        if merged and s <= merged[-1][1] + 60:
            ms, me, mc = merged[-1]
            merged[-1] = (ms, max(me, e), mc if c in mc else f"{mc},{c}")
        else:
            merged.append((s, e, c))
    currently_down = bool(ticks and ticks[-1][1])
    last_end = merged[-1][1] if merged else (ticks[0][0] if ticks else None)
    run_sec = 0.0 if currently_down or last_end is None else now - last_end

    def runs(since: float) -> list[float]:
        since = max(since, ticks[0][0]) if ticks else now
        edges = [since] + [x for s, e, _ in merged if e > since for x in (s, e)] + [now]
        return [edges[k + 1] - edges[k] for k in range(0, len(edges) - 1, 2) if edges[k + 1] > edges[k]]

    proof = facts.get("proof_active") or {}
    t0 = parse_ts(proof.get("t0"))
    return {
        "schema": "self_aware_uptime_v1", "generated_at": iso(now),
        "running_uninterrupted_sec": round(run_sec), "currently_interrupted": currently_down,
        "last_interruption": ({"start": iso(merged[-1][0]), "end": iso(merged[-1][1]), "cause": merged[-1][2]}
                              if merged else None),
        "interruptions_24h": sum(1 for s, e, _ in merged if e >= now - 86400),
        "interruptions_7d": [{"start": iso(s), "end": iso(e), "cause": c} for s, e, c in merged
                             if e >= now - 7 * 86400][-50:],
        "longest_run_7d_sec": round(max(runs(now - 7 * 86400), default=0.0)),
        "unobserved_gaps_7d": [{"start": iso(a), "end": iso(b)} for a, b in unobserved if b >= now - 7 * 86400][-10:],
        "process_starts": [iso(s) for s in starts[-10:]],
        "proof": {"t0": proof.get("t0"), "ends_at": proof.get("ends_at"),
                  "hours_elapsed": round((now - t0) / 3600, 2) if t0 else None,
                  "result": (proof.get("status") or {}).get("result"),
                  "reasons": (proof.get("status") or {}).get("reasons")},
        "source": "watcher verdicts (laptop-chain/health/verdicts-*.jsonl) + Fly runtime snapshot process age",
    }


def as_frame(doc: dict) -> pd.DataFrame:
    return pd.DataFrame([{"generated_at": doc["generated_at"], "running_uninterrupted_sec": doc["running_uninterrupted_sec"],
                          "interruptions_24h": doc["interruptions_24h"], "longest_run_7d_sec": doc["longest_run_7d_sec"],
                          "currently_interrupted": doc["currently_interrupted"], "doc": __import__("json").dumps(doc)}])
