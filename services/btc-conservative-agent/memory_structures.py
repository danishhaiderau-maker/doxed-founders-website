"""Bounded, read-only memory diagnostics: which in-process structure grows (Fly bot RSS 716 -> 1119 MB, 18 Oct).

snapshot() reports RSS, the largest module-level containers by length (dict/list/set/deque/OrderedDict) with their
shallow size, and a gc object-type histogram (count and shallow bytes). It never mutates anything. Tracker keeps the
last snapshots and reports per-name growth so the growing structure is named, not guessed.
"""
from __future__ import annotations

import collections
import gc
import json
import os
import sys
import time
from pathlib import Path

TOP_N = 25
HISTOGRAM_EVERY = 6  # with a 600 s interval: hourly
KEEP = 12  # snapshots kept for growth deltas
_CONTAINERS = (dict, list, set, frozenset, collections.deque, collections.OrderedDict, collections.defaultdict)


def rss_kb() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def module_containers(modules=None, top: int = TOP_N) -> list[dict]:
    rows = []
    for mname, mod in list((modules if modules is not None else sys.modules).items()):
        d = getattr(mod, "__dict__", None)
        if not isinstance(d, dict) or mname.startswith(("_pytest", "pytest", "typing", "importlib")):
            continue
        for name, val in list(d.items()):
            if isinstance(val, _CONTAINERS) and not name.startswith("__"):
                try:
                    n = len(val)
                    if n >= 50:
                        rows.append({"name": f"{mname}.{name}", "type": type(val).__name__, "len": n,
                                     "shallow_bytes": sys.getsizeof(val)})
                except Exception:  # noqa: BLE001 - a racing mutation must not break diagnostics
                    continue
    rows.sort(key=lambda r: r["len"], reverse=True)
    return rows[:top]


def type_histogram(top: int = TOP_N) -> list[dict]:
    counts: dict = {}
    sizes: dict = {}
    for o in gc.get_objects():
        t = type(o).__name__
        counts[t] = counts.get(t, 0) + 1
        try:
            sizes[t] = sizes.get(t, 0) + sys.getsizeof(o)
        except Exception:  # noqa: BLE001
            pass
    rows = [{"type": t, "count": c, "shallow_bytes": sizes.get(t, 0)} for t, c in counts.items()]
    rows.sort(key=lambda r: r["shallow_bytes"], reverse=True)
    return rows[:top]


class Tracker:
    def __init__(self, path, interval_sec: float = 600.0) -> None:
        self.path, self.interval, self._last, self.history = Path(path), float(interval_sec), 0.0, []
        self._runs = 0

    def due(self, now: float) -> bool:
        return now - self._last >= self.interval

    def run(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self._last = now
        # The gc histogram walks every object (a GIL pause of seconds on a big heap): only every HISTOGRAM_EVERY-th run.
        full = self._runs % HISTOGRAM_EVERY == 0
        self._runs += 1
        types_ = type_histogram() if full else (self.history[-1]["types"] if self.history else [])
        snap = {"ts": now, "pid": os.getpid(), "rss_kb": rss_kb(), "containers": module_containers(),
                "types": types_, "types_refreshed": full}
        self.history = (self.history + [snap])[-KEEP:]
        first = self.history[0]
        base = {r["name"]: r["len"] for r in first["containers"]}
        tbase = {r["type"]: r["shallow_bytes"] for r in first["types"]}
        doc = {"schema": "memory_structures_v1", "latest": snap, "samples": len(self.history),
               "window_sec": round(now - first["ts"], 1), "rss_growth_kb": (snap["rss_kb"] or 0) - (first["rss_kb"] or 0),
               "container_growth": sorted(
                   ({"name": r["name"], "len_now": r["len"], "len_growth": r["len"] - base.get(r["name"], 0),
                     "new_in_top": r["name"] not in base}
                    for r in snap["containers"]), key=lambda r: r["len_growth"], reverse=True)[:10],
               "type_growth_bytes": sorted(
                   ({"type": r["type"], "growth": r["shallow_bytes"] - tbase.get(r["type"], 0)}
                    for r in snap["types"]), key=lambda r: r["growth"], reverse=True)[:10]}
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)
        return doc
