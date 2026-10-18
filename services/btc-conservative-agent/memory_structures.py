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


def memory_limit_kb() -> int | None:
    """Actual VM memory limit: FLY_VM_MEMORY_MB (set by Fly), else a finite cgroup limit, else MemTotal. Never an alert line."""
    try:
        return int(os.environ["FLY_VM_MEMORY_MB"]) * 1024
    except (KeyError, ValueError):
        pass
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(f).read_text().strip()
            if v.isdigit() and int(v) < (1 << 50):
                return int(v) // 1024
        except OSError:
            pass
    try:
        return int(os.environ["FLY_VM_MEMORY_MB"]) * 1024
    except (KeyError, ValueError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def process_detail(top: int = 8, smaps_path: str = "/proc/self/smaps") -> dict:
    """Native-memory view: anon vs file RSS, biggest mappings, threads, open fds, DuckDB usage (if loaded). Read-only."""
    out: dict = {"anon_kb": None, "file_kb": None, "shmem_kb": None, "top_mappings": [], "threads": None, "open_fds": None,
                 "duckdb_memory_bytes": None}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("RssAnon:", "RssFile:", "RssShmem:")):
                k = {"RssAnon:": "anon_kb", "RssFile:": "file_kb", "RssShmem:": "shmem_kb"}[line.split()[0]]
                out[k] = int(line.split()[1])
            elif line.startswith("Threads:"):
                out["threads"] = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        out["open_fds"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    try:
        maps, cur = [], None
        for line in Path(smaps_path).read_text().splitlines():
            head = line.split()
            if head and "-" in head[0] and not head[0].endswith(":") and len(head) >= 5:
                cur = {"range": head[0], "perm": head[1], "name": head[5] if len(head) > 5 else "[anon]", "rss_kb": 0, "anon_kb": 0}
                maps.append(cur)
            elif cur is not None and line.startswith("Rss:"):
                cur["rss_kb"] = int(head[1])
            elif cur is not None and line.startswith("Anonymous:"):
                cur["anon_kb"] = int(head[1])
        maps.sort(key=lambda m: m["rss_kb"], reverse=True)
        out["top_mappings"] = maps[:top]
        out["mapping_count"] = len(maps)
    except (OSError, ValueError, IndexError):
        pass
    if "duckdb" in sys.modules:
        try:
            rows = sys.modules["duckdb"].sql("select coalesce(sum(memory_usage_bytes),0) from duckdb_memory()").fetchall()
            out["duckdb_memory_bytes"] = int(rows[0][0])
        except Exception:  # noqa: BLE001
            pass
    return out


def oom_eta(history: list, limit_kb: int | None, min_window_sec: float = 1800.0) -> dict:
    """Linear RSS growth over the retained window -> hours until the container limit. status RED <6 h, AMBER <24 h."""
    pts = [(h["ts"], h["rss_kb"]) for h in history if h.get("rss_kb")]
    if not limit_kb or len(pts) < 2 or pts[-1][0] - pts[0][0] < min_window_sec:
        return {"status": "PENDING", "hours_to_oom": None, "rss_growth_mb_per_h": None,
                "rss_mb": (pts[-1][1] // 1024) if pts else None, "limit_mb": (limit_kb or 0) // 1024 or None}
    slope = (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 3600.0)  # kB per hour
    rss = pts[-1][1]
    hours = None if slope <= 0 else round(max(0, limit_kb - rss) / slope, 2)
    st = "GREEN" if hours is None else ("RED" if hours < 6 else "AMBER" if hours < 24 else "GREEN")
    return {"status": st, "hours_to_oom": hours, "rss_growth_mb_per_h": round(slope / 1024, 1), "rss_mb": rss // 1024,
            "limit_mb": limit_kb // 1024}


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
        snap = {"ts": now, "pid": os.getpid(), "rss_kb": rss_kb(), "containers": module_containers(), "proc": process_detail(),
                "types": types_, "types_refreshed": full}
        self.history = (self.history + [snap])[-KEEP:]
        first = self.history[0]
        base = {r["name"]: r["len"] for r in first["containers"]}
        tbase = {r["type"]: r["shallow_bytes"] for r in first["types"]}
        doc = {"schema": "memory_structures_v1", "latest": snap,
               "oom_eta": oom_eta(self.history, memory_limit_kb()),
               "rss_series": [[round(h["ts"]), h["rss_kb"]] for h in self.history], "samples": len(self.history),
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
