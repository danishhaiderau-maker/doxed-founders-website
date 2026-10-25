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
# A named structure must explain at least this share of the RSS growth (and >= 1 MiB) to be blamed for a leak;
# otherwise the growth is reported as UNATTRIBUTED_ANON_OR_NATIVE (malloc arenas, native buffers).
ATTRIBUTION_MIN_FRACTION = 0.10
ATTRIBUTION_MIN_BYTES = 1 << 20
UNATTRIBUTED = "UNATTRIBUTED_ANON_OR_NATIVE"


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


WARMUP_SEC = 2 * 3600.0
LEAK_CHECK_UPTIME_SEC = 3 * 3600.0
LEAK_MB_PER_H = 50.0
MIN_POST_WARMUP_SAMPLES = 6
STALE_SEC = 600.0           # runtime_telemetry samples every ~60 s; older than this the sample is not trusted
STALE_AMBER_SEC = 1800.0    # UNKNOWN that persists this long becomes AMBER
PENDING_RED_FRAC = 0.60
ANY_RED_FRAC = 0.80
DEFAULT_LIMIT_KB = 8192 * 1024
ABS_RED_KB = 3 * 1024 * 1024        # HM 19 Oct interim alert: RSS (bot + shipper) > 3 GB is RED at any uptime
ABS_RED_YOUNG_KB = 2 * 1024 * 1024  # ... and > 2 GB in a boot younger than 12 h
YOUNG_BOOT_SEC = 12 * 3600.0
SHIPPER_WINDOW_SEC = 30 * 60.0  # HM 19 Oct: rolling median over >= 30 min, shipper term only
STAIR_MB = 50.0                     # a rise this large between two consecutive samples is recorded as a stair
STAIRS_KEEP = 50
LOOPS_KEEP = 40


def detect_stairs(series, min_mb: float = STAIR_MB) -> list:
    """[(ts, delta_mb)] for every consecutive-sample RSS rise >= min_mb (the ~110 MB/cycle stair signature)."""
    out, prev = [], None
    for t, v in series or []:
        if prev is not None and v and prev[1] and (v - prev[1]) / 1024.0 >= min_mb:
            out.append({"ts": round(float(t), 1), "delta_mb": round((v - prev[1]) / 1024.0, 1), "rss_mb": v // 1024})
        prev = (t, v)
    return out[-STAIRS_KEEP:]


SHIPPER_SCRIPT = "research_segment_shipper.py"


def cmdline_runs_script(cmdline: bytes | str, script: str = SHIPPER_SCRIPT) -> bool:
    """True only when an argv token IS the script (basename match): `python /app/research_segment_shipper.py`. A process that merely
    mentions 'shipper' in a path or an argument (CI/gate scripts, the monitor, `grep shipper`) does not match."""
    raw = cmdline.encode() if isinstance(cmdline, str) else cmdline
    toks = [t.decode("utf-8", "replace") for t in raw.split(b"\0") if t]
    return any(os.path.basename(t) == script for t in toks)


def process_rss_kb(script: str = SHIPPER_SCRIPT) -> int:
    """Summed RSS (kB) of OTHER processes that run ``script`` (the research segment shipper); 0 if none/unreadable."""
    total, me = 0, os.getpid()
    try:
        for ent in os.listdir("/proc"):
            if not ent.isdigit() or int(ent) == me:
                continue
            try:
                if cmdline_runs_script(Path(f"/proc/{ent}/cmdline").read_bytes(), script):
                    total += int(Path(f"/proc/{ent}/statm").read_text().split()[1]) * 4
            except (OSError, ValueError):
                continue
    except OSError:
        return 0
    return total

SERIES_KEEP = 400           # ~66 h at the 10-minute tracker interval


def _slope_kb_per_h(pts: list) -> float | None:
    """Least-squares RSS slope (kB/h) over (ts, rss_kb) points; None if the span is empty."""
    n = len(pts)
    if n < 2:
        return None
    t0 = pts[0][0]
    xs = [(t - t0) / 3600.0 for t, _ in pts]
    ys = [float(v) for _, v in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def oom_status(series: list, limit_kb: int | None, boot_ts: float | None, now: float,
               sample_ts: float | None, rss_kb_now: int | None, leak_structure: str | None = None,
               prev_boot: dict | None = None, extra_rss_kb: int = 0, extra_rss_raw_kb: int | None = None) -> dict:
    """fly.memory_oom_eta state. ``series`` = [(ts, rss_kb)] of runtime_telemetry samples (the same RSS /api/status shows).

    - missing/stale sample -> UNKNOWN, AMBER once it has persisted > 30 min;
    - first 2 h after boot -> PENDING (current RSS, NO OOM projection), RED at 60% of the VM limit;
    - RED at 80% of the limit at any time;
    - after warm-up: slope from post-warm-up samples only, >= 6 of them; > 50 MB/h at >= 3 h uptime is a leak (named).
    """
    limit_mb = (limit_kb // 1024) if limit_kb else None
    base = {"status": "UNKNOWN", "hours_to_oom": None, "rss_growth_mb_per_h": None, "rss_mb": None,
            "limit_mb": limit_mb, "uptime_sec": None, "leak": False, "leak_structure": None, "reason": None,
            "prev_boot": prev_boot}
    age = None if sample_ts is None else now - float(sample_ts)
    if rss_kb_now is not None and age is not None and age <= STALE_SEC:
        total_kb = rss_kb_now + max(0, int(extra_rss_kb or 0))     # bot + SMOOTHED shipper (rolling median)
        raw_kb = rss_kb_now + max(0, int(extra_rss_kb if extra_rss_raw_kb is None else extra_rss_raw_kb or 0))  # unsmoothed
        base["shipper_rss_mb"] = int(extra_rss_kb or 0) // 1024
        base["shipper_rss_raw_mb"] = int(extra_rss_kb if extra_rss_raw_kb is None else extra_rss_raw_kb or 0) // 1024
        if raw_kb >= ABS_RED_KB:   # the 3 GiB ceiling stays UNSMOOTHED
            base.update(status="RED", rss_mb=rss_kb_now // 1024, reason="RSS_OVER_3GB", sample_age_sec=round(age))
            return base
        if boot_ts is not None and now - float(boot_ts) < YOUNG_BOOT_SEC and total_kb >= ABS_RED_YOUNG_KB:
            base.update(status="RED", rss_mb=rss_kb_now // 1024, reason="RSS_OVER_2GB_UNDER_12H_BOOT", sample_age_sec=round(age),
                        uptime_sec=round(now - float(boot_ts)))
            return base
        # absolute ceilings apply even when the VM limit or boot time is unreadable (default: the 8 GB Fly VM)
        _lim = limit_kb or DEFAULT_LIMIT_KB
        if rss_kb_now >= ANY_RED_FRAC * _lim:
            base.update(status="RED", rss_mb=rss_kb_now // 1024, reason="RSS_OVER_80PCT_OF_LIMIT", sample_age_sec=round(age))
            return base
        if boot_ts is not None and now - float(boot_ts) < WARMUP_SEC and rss_kb_now >= PENDING_RED_FRAC * _lim:
            base.update(status="RED", rss_mb=rss_kb_now // 1024, reason="RSS_OVER_60PCT_DURING_WARMUP", sample_age_sec=round(age),
                        uptime_sec=round(now - float(boot_ts)))
            return base
    if sample_ts is None or rss_kb_now is None or age > STALE_SEC or not limit_kb or boot_ts is None:
        base["reason"] = "NO_SAMPLE" if sample_ts is None or rss_kb_now is None else (
            "STALE_SAMPLE" if age > STALE_SEC else "NO_LIMIT_OR_BOOT")
        base["sample_age_sec"] = None if age is None else round(age)
        if age is not None and age > STALE_AMBER_SEC:
            base["status"] = "AMBER"
        return base
    uptime = max(0.0, now - float(boot_ts))
    base.update(uptime_sec=round(uptime), rss_mb=rss_kb_now // 1024, sample_age_sec=round(age))
    frac = rss_kb_now / float(limit_kb)
    if frac >= ANY_RED_FRAC:
        base.update(status="RED", reason="RSS_OVER_80PCT_OF_LIMIT")
        return base
    if uptime < WARMUP_SEC:
        base.update(status="RED" if frac >= PENDING_RED_FRAC else "PENDING",
                    reason="RSS_OVER_60PCT_DURING_WARMUP" if frac >= PENDING_RED_FRAC else "WARMUP_NO_PROJECTION")
        return base
    post = [(t, v) for t, v in series if t >= float(boot_ts) + WARMUP_SEC and v]
    if len(post) < MIN_POST_WARMUP_SAMPLES:
        base.update(status="PENDING", reason=f"POST_WARMUP_SAMPLES_{len(post)}_OF_{MIN_POST_WARMUP_SAMPLES}")
        return base
    slope = _slope_kb_per_h(post)
    if slope is None:
        base.update(status="PENDING", reason="NO_SPAN")
        return base
    mbh = slope / 1024.0
    hours = None if slope <= 0 else round(max(0, limit_kb - rss_kb_now) / slope, 2)
    st = "GREEN" if hours is None else ("RED" if hours < 6 else "AMBER" if hours < 24 else "GREEN")
    leak = uptime >= LEAK_CHECK_UPTIME_SEC and mbh > LEAK_MB_PER_H
    if leak and st == "GREEN":
        st = "AMBER"
    base.update(status=st, hours_to_oom=hours, rss_growth_mb_per_h=round(mbh, 1), leak=leak,
                leak_structure=(leak_structure or "UNATTRIBUTED_ANON_OR_NATIVE") if leak else None,
                reason="LEAK_OVER_50MB_PER_H" if leak else "SLOPE")
    return base


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
                        row = {"name": f"{mname}.{name}", "type": type(val).__name__, "len": n,
                               "shallow_bytes": sys.getsizeof(val)}
                        maxlen = getattr(val, "maxlen", None) if isinstance(val, collections.deque) else None
                        if maxlen is not None:
                            row["maxlen"] = int(maxlen)
                            row["capped_filling"] = n < maxlen   # bounded: growth toward maxlen is not a leak
                        rows.append(row)
                except Exception:  # noqa: BLE001 - a racing mutation must not break diagnostics
                    continue
    rows.sort(key=lambda r: r["shallow_bytes"], reverse=True)   # rank by bytes, not element count
    return rows[:top]


def attribute_growth(containers: list, types_: list, base_bytes: dict, tbase: dict, rss_growth_kb: int | None) -> dict:
    """Name the structure behind RSS growth by BYTES. Capped deques still filling toward maxlen are skipped. If the
    best candidate explains < ATTRIBUTION_MIN_FRACTION of the RSS growth (or < 1 MiB), report UNATTRIBUTED."""
    rss_growth_bytes = max(0, int(rss_growth_kb or 0)) * 1024
    need = max(ATTRIBUTION_MIN_BYTES, ATTRIBUTION_MIN_FRACTION * rss_growth_bytes)
    cands = [("container:" + r["name"], r["shallow_bytes"] - base_bytes.get(r["name"], r["shallow_bytes"]))
             for r in containers if not r.get("capped_filling") and r["name"] in base_bytes]
    cands += [("type:" + r["type"], r["shallow_bytes"] - tbase.get(r["type"], r["shallow_bytes"]))
              for r in types_ if r["type"] in tbase]
    cands.sort(key=lambda c: c[1], reverse=True)
    best = cands[0] if cands else (None, 0)
    named = best[0] if best[0] is not None and best[1] >= need else None
    return {"structure": named or UNATTRIBUTED, "bytes_growth": int(best[1]) if named else None,
            "best_candidate": best[0], "best_candidate_bytes_growth": int(best[1]),
            "rss_growth_bytes": rss_growth_bytes,
            "fraction_of_rss_growth": round(best[1] / rss_growth_bytes, 4) if named and rss_growth_bytes else None}


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
        self.series: list = []
        self.boot_ts: float | None = None
        self.prev_boot: dict | None = None
        self.loops: list = []
        self.shipper_hist: list = []   # (ts, shipper_kb) for the rolling median (shipper term only; the bot's RSS is never smoothed)
        self._load_previous()

    def _load_previous(self) -> None:
        """Keep the previous boot's final RSS/slope when this process is a restart (new boot_ts resets the series)."""
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._prev_doc = doc if isinstance(doc, dict) else None

    def _roll_boot(self, boot_ts: float | None) -> None:
        if boot_ts is None:
            return
        doc = getattr(self, "_prev_doc", None)
        self._prev_doc = None
        if doc is not None:
            old_boot = doc.get("boot_ts")
            eta = doc.get("oom_eta") or {}
            if old_boot is not None and abs(float(old_boot) - float(boot_ts)) > 1.0:
                ser = [(float(a), int(b)) for a, b in (doc.get("rss_series_full") or []) if b]
                post = [(t, v) for t, v in ser if t >= float(old_boot) + WARMUP_SEC]
                sl = _slope_kb_per_h(post) if len(post) >= 2 else None
                self.prev_boot = {"boot_ts": old_boot, "ended_ts": ser[-1][0] if ser else None,
                                  "final_rss_mb": (ser[-1][1] // 1024) if ser else None,
                                  "slope_mb_per_h": round(sl / 1024.0, 1) if sl is not None else None,
                                  "samples": len(ser), "status": eta.get("status")}
            elif old_boot is not None:  # same boot (tracker re-created in-process): continue the series
                self.series = [(float(a), int(b)) for a, b in (doc.get("rss_series_full") or []) if b]
                self.prev_boot = doc.get("prev_boot")
        self.boot_ts = boot_ts

    def smooth_shipper(self, now: float, raw_kb: int) -> int:
        """Rolling MEDIAN of the shipper's RSS over the last SHIPPER_WINDOW_SEC (>= 30 min; fewer samples early in a boot).
        The shipper spikes to ~1.3 GB for about a minute every 6-7 min; the median ignores those spikes."""
        self.shipper_hist = [(t, v) for t, v in self.shipper_hist if now - t <= SHIPPER_WINDOW_SEC] + [(now, int(raw_kb or 0))]
        vals = sorted(v for _, v in self.shipper_hist)
        n = len(vals)
        return int(vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) // 2)

    def record_loop(self, name: str, start_ts: float, end_ts: float, steps: list | None = None) -> None:
        """Start/end of one loop body (analytics_loop) so a stair can be matched to the cycle that caused it; ``steps`` =
        per-step {step, sec, rss_delta_kb} so the step that grows RSS permanently is named."""
        rec = {"name": name, "start": round(float(start_ts), 1), "end": round(float(end_ts), 1),
               "body_sec": round(float(end_ts) - float(start_ts), 2)}
        if steps:
            rec["steps"] = [dict(x) for x in steps][:12]
        self.loops = (self.loops + [rec])[-LOOPS_KEEP:]

    def due(self, now: float) -> bool:
        return now - self._last >= self.interval

    def run(self, now: float | None = None, sample: dict | None = None) -> dict:
        """``sample`` = the runtime_telemetry row ({ts, boot_ts, memory:{rss_bytes}}): the same RSS /api/status reports."""
        now = time.time() if now is None else now
        sample_ts = rss_now = boot_ts = None
        if isinstance(sample, dict):
            try:
                sample_ts = float(sample.get("ts"))
                boot_ts = float(sample.get("boot_ts")) if sample.get("boot_ts") is not None else None
                b = (sample.get("memory") or {}).get("rss_bytes")
                rss_now = int(b) // 1024 if b else None
            except (TypeError, ValueError):
                sample_ts = rss_now = None
        if self.boot_ts is None or (boot_ts is not None and abs(boot_ts - self.boot_ts) > 1.0):
            self._roll_boot(boot_ts)
            if boot_ts is not None and self.boot_ts != boot_ts:
                self.boot_ts = boot_ts
        if rss_now is not None and sample_ts is not None and (not self.series or self.series[-1][0] < sample_ts):
            self.series = (self.series + [(sample_ts, rss_now)])[-SERIES_KEEP:]
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
        base_bytes = {r["name"]: r["shallow_bytes"] for r in first["containers"]}
        attribution = attribute_growth(snap["containers"], snap["types"], base_bytes, tbase,
                                       (snap["rss_kb"] or 0) - (first["rss_kb"] or 0))
        leak_name = attribution["structure"]
        shipper_raw_kb = process_rss_kb()
        shipper_kb = self.smooth_shipper(now, shipper_raw_kb)
        doc = {"schema": "memory_structures_v1", "latest": snap, "boot_ts": self.boot_ts,
               "oom_eta": oom_status(self.series, memory_limit_kb(), self.boot_ts, now, sample_ts, rss_now,
                                     leak_structure=leak_name, prev_boot=self.prev_boot, extra_rss_kb=shipper_kb, extra_rss_raw_kb=shipper_raw_kb),
               "shipper_rss_kb": shipper_kb, "shipper_rss_raw_kb": shipper_raw_kb,
               "shipper_window_sec": round(now - self.shipper_hist[0][0]) if self.shipper_hist else 0, "stairs": detect_stairs(self.series), "loops": list(self.loops),
               "prev_boot": self.prev_boot,
               "rss_series_full": [[round(t, 1), v] for t, v in self.series],
               "rss_series": [[round(h["ts"]), h["rss_kb"]] for h in self.history], "samples": len(self.history),
               "window_sec": round(now - first["ts"], 1), "rss_growth_kb": (snap["rss_kb"] or 0) - (first["rss_kb"] or 0),
               "leak_attribution": attribution,
               "container_growth": sorted(
                   ({"name": r["name"], "len_now": r["len"], "len_growth": r["len"] - base.get(r["name"], 0),
                     "bytes_now": r["shallow_bytes"],
                     "bytes_growth": r["shallow_bytes"] - base_bytes.get(r["name"], 0),
                     "capped_filling": bool(r.get("capped_filling")),
                     "new_in_top": r["name"] not in base}
                    for r in snap["containers"]), key=lambda r: r["bytes_growth"], reverse=True)[:10],
               "type_growth_bytes": sorted(
                   ({"type": r["type"], "growth": r["shallow_bytes"] - tbase.get(r["type"], 0)}
                    for r in snap["types"]), key=lambda r: r["growth"], reverse=True)[:10]}
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)
        return doc
