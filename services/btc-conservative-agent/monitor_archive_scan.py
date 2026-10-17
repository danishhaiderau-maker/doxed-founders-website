"""Read-only archive scan (archive design v2, step 1): what is physically on the Fly data dir, whole files.

Per file: bytes, row count, min/max timestamp, and (paper-row files) fresh / legacy / forced counts plus
fill_model_version counts. Covers rotated parts (<file>.N) and the paper_ioc_no_fill shadow file, and reads each
file COMPLETELY in a streaming way (1 MiB chunks, never the whole file in memory), up to the byte length that was
snapshotted at the start of the scan (a growing file is a consistent prefix; trailing partial line is reported).

Safety: opens files read-only, never writes, never takes trade_lock/state_lock, never reads secrets (names that look
like credentials are skipped and counted, contents never opened), throttled to 2 MB/s, single flight, and aborts on
3 consecutive CPU samples > 60% of one core or a git_rev change. The scan only MEASURES; nothing is exported.
"""
from __future__ import annotations

import csv
import fnmatch
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import paper_fill

SCHEMA = "monitor_archive_scan_v1"
CHUNK = 1024 * 1024
THROTTLE_BPS = 2 * 1024 * 1024
CPU_LIMIT = 0.60
CPU_SAMPLES_TO_ABORT = 3
CPU_SAMPLE_EVERY_S = 2.0
SCOPE_START_TS = datetime(2026, 10, 13, tzinfo=timezone.utc).timestamp() - 11 * 3600  # 13 Oct 00:00 AEDT
JSON_PARSE_MAX = 16 * 1024 * 1024
FORCED_STATUSES = ("FORCED_NO_BOOK", "PAPER_FILL_FORCED")

# (id, category, label, kind, glob patterns relative to the data dir; rotated parts are matched as <pat>.N)
REGISTRY = (
    ("trades_3factor", "paper_rows", None, "csv", ("trades_3factor.csv",)),
    ("paper_ioc_no_fill_shadow", "paper_rows", None, "jsonl", ("paper_ioc_no_fill_shadow.jsonl",)),
    ("disloc_trades", "paper_rows", "legacy-pricing", "jsonl", ("disloc_trades.jsonl",)),
    ("disloc_foi_trades", "paper_rows", "legacy-pricing", "jsonl", ("disloc_foi_trades.jsonl",)),
    ("shadow_exit_paths", "shadow", None, "jsonl", ("shadow_exit_paths.jsonl",)),
    ("xvp_shadow_signals", "shadow", None, "jsonl", ("xvp_shadow_signals.jsonl",)),
    ("ai_shadow_challengers", "shadow", None, "jsonl", ("ai_shadow_challengers.jsonl",)),
    ("taker_counterfactuals", "shadow", None, "jsonl", ("taker_signal_counterfactuals.jsonl",)),
    ("open_positions", "state", "secrets stripped at export", "json", ("open_positions.json",)),
    ("closed_positions", "state", "secrets stripped at export", "json", ("closed_positions*.json", "trades_state*.json")),
    ("archive_receipt", "receipt", None, "json", ("archive_receipt.json",)),
    ("archive_receipts_log", "receipt", None, "jsonl", ("archive_receipts.jsonl",)),
    ("wipe_receipt", "receipt", None, "json", ("fresh_start_wipe_receipt.json",)),
    ("deploy_receipts", "receipt", None, "json", ("*deploy*receipt*.json", "*deploy*receipt*.jsonl")),
    ("fill_receipts", "receipt", None, "json", ("*fill*receipt*.json", "*fill*receipt*.jsonl")),
    ("reconcile_receipts", "receipt", None, "json", ("*reconcile*receipt*.json", "*reconcile*receipt*.jsonl",
                                                       "lane_pnl_ledger_epoch_receipts.jsonl")),
    ("bfx_book_10l_1s", "market", None, "jsonl", ("bfx_book_10l_1s.jsonl",)),
    ("bfx_trades", "market", None, "jsonl", ("bfx_trades.jsonl",)),
    ("cross_venue_tape_1m", "market", None, "jsonl", ("cross_venue_tape_1m.jsonl",)),
    ("bfx_deriv_status_1s", "market", None, "jsonl", ("bfx_deriv_status_1s.jsonl",)),
    ("book_tape_1s", "market", None, "jsonl", ("market_microstructure_1s.jsonl",)),
    ("market_context_1m", "market", None, "jsonl", ("market_context_1m.jsonl",)),
    ("venue_bbo_1m", "market", None, "jsonl", ("venue_bbo_1m.jsonl",)),
)
# Never opened, never exported (credentials / live control). Matched on the file name, case-insensitive.
EXCLUDE_PATTERNS = ("*.env", "*secret*", "*token*", "*credential*", "*apikey*", "*api_key*", "bitfinex_live*",
                    "bitfinex_*key*", "config*.json", "*.pem", "*.key", "*vault*")
# JSON documents captured by the export at export time (not scanned here).
CAPTURED_AT_EXPORT = ("/api/monitor/reconcile", "/api/monitor/fill-provenance", "/api/monitor/lanes",
                      "/api/monitor/tiles/trades", "/api/monitor/integrity-gaps")

_LOCK = threading.Lock()
_STATE: dict = {"status": "idle", "scan": None, "progress": None, "abort_reason": None, "started_ts": None,
                "finished_ts": None, "git_rev_at_start": None}


def excluded(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, p) for p in EXCLUDE_PATTERNS)


def _match(name: str, pattern: str) -> int | None:
    """0 for the current file, N for a rotated <file>.N part, None when the name does not match."""
    if fnmatch.fnmatch(name, pattern):
        return 0
    base, _, suffix = name.rpartition(".")
    if suffix.isdigit() and base and fnmatch.fnmatch(base, pattern):
        return int(suffix)
    return None


def discover(root: Path) -> tuple[list[dict], int]:
    """Registry entries x matching files (current + rotated .N). Returns (files, excluded_count)."""
    out, skipped = [], 0
    names = sorted(p.name for p in root.iterdir() if p.is_file()) if root.is_dir() else []
    for fid, category, label, kind, patterns in REGISTRY:
        seen = set()
        for name in names:
            part = next((m for m in (_match(name, pat) for pat in patterns) if m is not None), None)
            if part is None or name in seen:
                continue
            seen.add(name)
            if excluded(name):
                skipped += 1
                continue
            out.append({"id": fid, "category": category, "label": label, "kind": kind, "file": name, "part": part,
                        "snapshot_bytes": (root / name).stat().st_size})
    return out, skipped


def _ts_of(row: Any, kind: str) -> float | None:
    import monitor_streams
    if isinstance(row, dict):
        ts = monitor_streams.row_ts(row)
        if ts is not None:
            return ts
        raw = row.get("ts") or row.get("close_ts") or row.get("exit_ts")
        if isinstance(raw, str) and len(raw) >= 10:
            try:
                d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
            except ValueError:
                return None
    return None


def _classify(row: dict, acc: dict) -> None:
    version = paper_fill.as_number(row.get("fill_model_version"))
    key = str(int(version)) if version is not None and version == int(version) else "none"
    acc["fill_model_version_counts"][key] = acc["fill_model_version_counts"].get(key, 0) + 1
    status = str(row.get("exit_status") or "")
    if status in FORCED_STATUSES or paper_fill.is_excluded(row.get("fresh_data_excluded")):
        acc["forced"] += 1
    elif paper_fill.row_fresh_strict(row):
        acc["fresh"] += 1
    elif version is None or version < 1 or paper_fill.as_bool(row.get("legacy_pricing"), False):
        acc["legacy"] += 1
    else:
        acc["other_not_fresh"] += 1


class Aborted(Exception):
    pass


class Guard:
    """Throttle (2 MB/s) + abort rules, with injectable clocks so tests are deterministic."""

    def __init__(self, git_rev: Callable[[], Any], *, now=time.monotonic, sleep=time.sleep,
                 cpu_time=time.process_time, rate_bps: int = THROTTLE_BPS) -> None:
        self.git_rev, self.now, self.sleep, self.cpu_time, self.rate = git_rev, now, sleep, cpu_time, rate_bps
        self.rev0 = git_rev()
        self.t0 = self.now()
        self.bytes = 0
        self.slept = 0.0
        self.cpu_hits = 0
        self._last = (self.now(), self.cpu_time())
        self.cpu_samples: list[float] = []

    def after_chunk(self, n: int) -> None:
        self.bytes += n
        due = self.t0 + self.bytes / float(self.rate) - self.now()
        if due > 0:
            self.sleep(due)
            self.slept += due
        t, c = self.now(), self.cpu_time()
        if t - self._last[0] >= CPU_SAMPLE_EVERY_S:
            frac = (c - self._last[1]) / max(1e-9, t - self._last[0])
            self.cpu_samples.append(round(frac, 3))
            self.cpu_hits = self.cpu_hits + 1 if frac > CPU_LIMIT else 0
            self._last = (t, c)
            if self.cpu_hits >= CPU_SAMPLES_TO_ABORT:
                raise Aborted(f"CPU > {int(CPU_LIMIT * 100)}% for {CPU_SAMPLES_TO_ABORT} samples")
        if self.git_rev() != self.rev0:
            raise Aborted("git_rev changed during the scan")


def scan_file(root: Path, entry: dict, guard: Guard, progress: Callable[[int], None] | None = None) -> dict:
    path, kind = root / entry["file"], entry["kind"]
    limit = int(entry["snapshot_bytes"])
    out = {k: entry[k] for k in ("id", "category", "label", "kind", "file", "part")}
    out.update({"bytes": limit, "rows": 0, "min_ts": None, "max_ts": None, "parse_errors": 0,
                "trailing_partial_bytes": 0})
    paper = entry["category"] == "paper_rows"
    acc = {"fresh": 0, "legacy": 0, "forced": 0, "other_not_fresh": 0, "fill_model_version_counts": {}}
    header: list[str] | None = None
    if kind == "json":
        out["rows"] = None
        if limit <= JSON_PARSE_MAX:
            try:
                doc = json.loads(path.read_bytes()[:limit])
                out["rows"] = len(doc) if isinstance(doc, (list, dict)) else 1
            except (OSError, ValueError):
                out["parse_errors"] = 1
        guard.after_chunk(limit)
        return out
    carry, read = b"", 0
    with path.open("rb") as fh:
        while read < limit:
            chunk = fh.read(min(CHUNK, limit - read))
            if not chunk:
                break
            read += len(chunk)
            guard.after_chunk(len(chunk))
            if progress:
                progress(len(chunk))
            lines = (carry + chunk).split(b"\n")
            carry = lines.pop()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    if kind == "csv":
                        cells = next(csv.reader([line.decode("utf-8", "replace")]))
                        if header is None:
                            header = cells
                            continue
                        row = dict(zip(header, cells))
                    else:
                        row = json.loads(line)
                except (ValueError, StopIteration, csv.Error):
                    out["parse_errors"] += 1
                    continue
                out["rows"] += 1
                ts = _ts_of(row, kind)
                if ts is not None:
                    out["min_ts"] = ts if out["min_ts"] is None else min(out["min_ts"], ts)
                    out["max_ts"] = ts if out["max_ts"] is None else max(out["max_ts"], ts)
                if paper and isinstance(row, dict):
                    _classify(row, acc)
    out["trailing_partial_bytes"] = len(carry.strip())
    if paper:
        out.update(acc)
    return out


def _label_partial(f: dict) -> None:
    if f["id"] == "bfx_book_10l_1s" and f["part"] == 0 and f["min_ts"] is not None:
        iso = datetime.fromtimestamp(f["min_ts"], timezone.utc).isoformat()
        f["completeness"] = f"PARTIAL: book stream starts at {iso}"
    f["starts_after_scope_start"] = (f["min_ts"] is not None and f["min_ts"] > SCOPE_START_TS)


def run_scan(root, guard: Guard, set_progress: Callable[[dict], None] | None = None) -> dict:
    root = Path(root)
    files, skipped = discover(root)
    total = sum(f["snapshot_bytes"] for f in files)
    done = [0]
    results = []
    for entry in files:
        if set_progress:
            set_progress({"file": entry["file"], "bytes_done": done[0], "bytes_total": total})
        r = scan_file(root, entry, guard, lambda n: done.__setitem__(0, done[0] + n))
        _label_partial(r)
        results.append(r)
    return {"files": results, "excluded_name_matches": skipped, "bytes_total": total,
            "scope_start_ts": SCOPE_START_TS, "excluded_patterns": list(EXCLUDE_PATTERNS),
            "captured_at_export": list(CAPTURED_AT_EXPORT),
            "guard": {"throttle_bps": guard.rate, "slept_s": round(guard.slept, 2), "cpu_samples": guard.cpu_samples[-20:]}}


def _worker(root, git_rev) -> None:
    try:
        guard = Guard(git_rev)
        scan = run_scan(root, guard, lambda p: _STATE.update(progress=p))
        with _LOCK:
            _STATE.update(status="done", scan=scan, finished_ts=time.time(), progress=None)
    except Aborted as exc:
        with _LOCK:
            _STATE.update(status="aborted", abort_reason=str(exc), finished_ts=time.time())
    except Exception as exc:  # noqa: BLE001 - a scan failure is reported, never raised into the bot
        with _LOCK:
            _STATE.update(status="error", abort_reason=f"{type(exc).__name__}: {str(exc)[:120]}", finished_ts=time.time())


def start(root, git_rev: Callable[[], Any], runner: Callable[[Callable], Any] | None = None) -> bool:
    """Single flight. Returns True when a new scan was started."""
    with _LOCK:
        if _STATE["status"] == "running":
            return False
        _STATE.update(status="running", scan=None, progress=None, abort_reason=None, started_ts=time.time(),
                      finished_ts=None, git_rev_at_start=git_rev())
    job = lambda: _worker(root, git_rev)  # noqa: E731
    if runner is not None:
        runner(job)
    else:
        threading.Thread(target=job, name="archive-scan", daemon=True).start()
    return True


def state() -> dict:
    with _LOCK:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _STATE.items()}


def reset_for_tests() -> None:
    with _LOCK:
        _STATE.update(status="idle", scan=None, progress=None, abort_reason=None, started_ts=None, finished_ts=None,
                      git_rev_at_start=None)
