"""One read-only snapshot of everything self-diagnosis reasons about.

Fly is never called from here: Fly state comes from the snapshots the laptop
supervisor already fetches (``fly_*_snapshot_v1.json``), so this layer adds
zero load to Fly's 60/min public rate limit. Mirror maxima come from DuckDB
over the raw views; local services are probed on 127.0.0.1 only.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Paths


def parse_ts(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else (v if v > 0 else None)
    text = str(value).strip()
    try:
        return parse_ts(float(text))
    except ValueError:
        pass
    text = text.replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = "".join(ch for ch in rest if ch.isdigit())
        tail = rest[len(digits):]
        text = f"{head}.{digits[:6]}{tail}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def tail_jsonl(path: Path, limit: int = 50, max_bytes: int = 2 * 1024 * 1024) -> list[dict[str, Any]]:
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - max_bytes))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if size > max_bytes:
        lines = lines[1:]
    rows = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def http_json(url: str, timeout: float = 5.0) -> tuple[Any, str | None, float]:
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), None, time.time() - started
    except Exception as exc:  # noqa: BLE001 - every failure is evidence, never fatal
        return None, f"{type(exc).__name__}: {str(exc)[:160]}", time.time() - started


def git_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


_ANCESTRY: dict[tuple[str, str], bool | None] = {}


def git_contains(repo: Path, ancestor: str | None, descendant: str | None) -> bool | None:
    """True when ``descendant`` contains ``ancestor`` (equal or a fast-forward of it); None when unknown."""
    if not ancestor or not descendant:
        return None
    a, d = ancestor.lower(), descendant.lower()
    if a.startswith(d[:12]) or d.startswith(a[:12]):
        return True
    key = (a, d)
    if key not in _ANCESTRY:
        try:
            out = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, d], capture_output=True,
                                 text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _ANCESTRY[key] = {0: True, 1: False}.get(out.returncode)
        except (OSError, subprocess.SubprocessError):
            _ANCESTRY[key] = None
        if len(_ANCESTRY) > 500:
            _ANCESTRY.clear()
    return _ANCESTRY.get(key)


def laptop_load(sample_sec: float = 2.0) -> tuple[float | None, float | None]:
    """System CPU busy % over ``sample_sec`` and memory used % (Windows kernel counters, no extra deps)."""
    if os.name != "nt":
        return None, None
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        k = ctypes.windll.kernel32

        def times() -> tuple[int, int, int]:
            idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            val = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
            return val(idle), val(kern), val(user)

        i0, k0, u0 = times()
        time.sleep(sample_sec)
        i1, k1, u1 = times()
        total = (k1 - k0) + (u1 - u0)
        cpu = round(100.0 * (1 - (i1 - i0) / total), 1) if total else None

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                       [(n, ctypes.c_ulonglong) for n in ("t", "a", "tp", "ap", "tv", "av", "ae")]

        ms = MemStatus()
        ms.dwLength = ctypes.sizeof(MemStatus)
        k.GlobalMemoryStatusEx(ctypes.byref(ms))
        return cpu, float(ms.dwMemoryLoad)
    except Exception:  # noqa: BLE001
        return None, None


def _source_has_files(store, view: str) -> bool | None:
    """True/False when the raw view's mirror glob currently matches files; None if unknown."""
    try:
        import glob as _glob

        from .store import RAW_SOURCES
        src = next((s for s in RAW_SOURCES if s.name == view), None)
        if src is None:
            return None
        root = getattr(store.paths, src.root)
        return any(Path(f).is_file() for f in _glob.glob(str(Path(root) / src.pattern), recursive=True))
    except Exception:  # noqa: BLE001
        return None


def _mirror_max(store, sql: str) -> tuple[Any, str | None]:
    """One mirror max/count probe.

    After an epoch reset the laptop mirror legitimately holds no files for a
    stream until the first segment lands; the raw view is then dropped (or
    still points at rotated files) and DuckDB raises Catalog/IO errors. That
    is an empty mirror during warmup, not an engine failure.
    """
    try:
        rows = store.read(sql)
        return (rows[0] if rows else {}), None
    except Exception as exc:  # noqa: BLE001
        text = f"{type(exc).__name__}: {str(exc)[:160]}"
        view = next((tok for tok in sql.replace("(", " ").replace(")", " ").split() if tok.startswith("raw_")), None)
        if view and _source_has_files(store, view) is False:
            return {"status": "EMPTY_WARMUP", "rows": 0, "detail": text}, None
        return {}, text


def collect(paths: Paths, store, now: float | None = None, *, probe_local: bool = True) -> dict[str, Any]:
    now = time.time() if now is None else now
    chain = paths.chain
    f: dict[str, Any] = {"now": now, "errors": {}}
    for key, name in (("runtime", "fly_runtime_snapshot_v1.json"), ("deploys", "fly_deploy_runs_snapshot_v1.json"),
                      ("segment_head", "fly_segment_head_snapshot_v1.json"), ("relay", "relay_status_snapshot_v1.json"),
                      ("analyzer_run", "analyzer-run.status.json"), ("cycle", "segment-analyzer-cycle.status.json"),
                      ("pull", "segment-pull.status.json"), ("incident", "laptop-chain-incident.state.json")):
        f[key] = read_json(chain / name)
    f["watcher"] = read_json(paths.health / "system-health-latest.json")
    f["proof_active"] = read_json(chain / "unattended-proof" / "active.json")
    f["autoff"] = tail_jsonl(chain / "v2c-auto-ff.receipts.jsonl", 20)
    f["manual"] = tail_jsonl(chain / "manual-interventions.jsonl", 20)
    rev = paths.chain / "analyzer-dashboard-revision.txt"
    try:
        f["analyzer_dashboard_rev"] = rev.read_text(encoding="utf-8").strip()
    except OSError:
        f["analyzer_dashboard_rev"] = None
    f["analyzer_head"] = git_head(paths.analyzer_repo)
    fly_rev = (f.get("runtime") or {}).get("git_rev")
    f["contains_fly"] = {k: git_contains(paths.analyzer_repo, fly_rev, r) for k, r in (
        ("head", f["analyzer_head"]), ("run", (f.get("analyzer_run") or {}).get("revision")),
        ("dash", f["analyzer_dashboard_rev"]))}

    mirror: dict[str, Any] = {}
    q = {
        "ai_tranche": "SELECT max(ts) AS max_ts, count(*) FILTER (WHERE event='AI_DECISION') AS decisions FROM raw_ai_tranche",
        "ai_input": "SELECT max(ts) AS max_ts, count(*) AS rows FROM raw_ai_input",
        "tape": "SELECT max(bucket_ts) AS max_ts, count(*) AS rows FROM raw_tape_1s",
        "cross_venue": "SELECT max(minute_ts) AS max_ts, count(*) AS rows FROM raw_cross_venue_1m",
        "decision": "SELECT max(decision_ts) AS max_ts, count(*) AS rows FROM raw_decision",
        "lifecycle": "SELECT max(coalesce(terminal_ts, submitted_ts, signal_ts)) AS max_ts, count(*) AS rows FROM raw_lifecycle",
        "execution": "SELECT max(coalesce(close_ts, fill_ts)) AS max_ts, count(*) AS rows FROM raw_execution",
    }
    for key, sql in q.items():
        row, err = _mirror_max(store, sql)
        if err:
            f["errors"][f"mirror.{key}"] = err
        mirror[key] = {"max_ts": parse_ts(row.get("max_ts")), **{k: v for k, v in row.items() if k != "max_ts"}}
    f["mirror"] = mirror

    if probe_local:
        f["laptop_cpu_pct"], f["laptop_mem_pct"] = laptop_load()
        f["svc_9001"] = dict(zip(("body", "error", "elapsed"), http_json("http://127.0.0.1:9001/api/health", 15)))
        f["svc_9001_health"] = dict(zip(("body", "error", "elapsed"),
                                        http_json("http://127.0.0.1:9001/api/system-health", 15)))
        f["svc_9011"] = dict(zip(("body", "error", "elapsed"),
                                 http_json("http://127.0.0.1:9011/api/system-health", 10)))
    return f


def snapshot_age(snap: Any, now: float) -> float | None:
    if not isinstance(snap, dict):
        return None
    at = parse_ts(snap.get("observedAt") or snap.get("generated_at") or snap.get("updatedAt"))
    return None if at is None else now - at


def watcher_check(facts: dict[str, Any], check_id: str) -> dict[str, Any] | None:
    for c in (facts.get("watcher") or {}).get("checks") or []:
        if isinstance(c, dict) and c.get("id") == check_id:
            return c
    return None
