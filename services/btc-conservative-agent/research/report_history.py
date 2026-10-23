"""Every-run history of the full analyzer report set, plus append-only research ledgers.

``snapshot_report_set`` copies *every* top-level ``*.json`` report of one
completed generation (report_manifest, analyzer_integrity, best_policy,
safe_policy_genome_v3, exit ladder, data_health, event_study, ...) into::

    <history_root>/<UTC stamp>_<generation>/   files (large ones zstd/gzip) + manifest.json
    <history_root>/index.jsonl                 append-only snapshot index
    <history_root>/event_study_ledger.jsonl    one line per (generation, hypothesis): lockbox counters
    <history_root>/data_health_ledger.jsonl    one line per generation: per-stream verdicts

``history_root`` defaults to ``C:\\DoxxedCrypto\\analysis-archive\\report-history``
(``DOXXED_REPORT_HISTORY_DIR``): outside any git checkout, so ``git clean`` in
the analyzer checkout cannot erase it, and outside ``reports/history`` (which
``research.retention`` thins to ~one folder per day).

A file identical to the previous snapshot's copy is hard-linked instead of
stored again. Retention deletes whole snapshots, oldest first, only while the
total exceeds ``keep_bytes_cap`` and never one of the newest
``keep_min_generations`` or the newest snapshot of each of the last
``daily_keep_days`` UTC days. The ledgers are never rewritten: lines are
appended, de-duplicated on ``generation_id``.

Epoch cap (RK C4c): every snapshot, index line and ledger row carries the
``data_epoch_id`` it was written under. After each snapshot, when the current
epoch is known, ``cap_to_epoch`` deletes snapshots of any other epoch (or none)
and drops other-epoch rows from the index and ledgers (atomic rewrite), so the
history never spans two epochs. ``clear_for_reset`` empties the history at a
data reset: it removes only entries this module owns (snapshot folders,
staging folders, the index and the two ledgers) and refuses roots under
OneDrive or a Windows user Temp folder. ``never_touch_reason`` is the shared
guard: operator ``.puller-backup-*`` folders and ``gs-export*`` files (the
GS export temp files in ``AppData\\Local\\Temp``) are never touched, by this
module or by the laptop wipe planner in ``clean_epoch_wipe``.
"""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = "analyzer_report_history_v1"
EVENT_LEDGER_SCHEMA = "event_study_ledger_v1"
HEALTH_LEDGER_SCHEMA = "data_health_ledger_v1"
DEFAULT_HISTORY_ROOT = r"C:\DoxxedCrypto\analysis-archive\report-history"
DEFAULT_KEEP_BYTES_CAP = 4 * 1024 ** 3
DEFAULT_KEEP_MIN_GENERATIONS = 24
DEFAULT_DAILY_KEEP_DAYS = 14
COMPRESS_MIN_BYTES = 256 * 1024
GZIP_LEVEL = 5
STAGING_PREFIX = ".staging-"
STAGING_MAX_AGE_SEC = 3600
MANIFEST = "manifest.json"
INDEX = "index.jsonl"
EVENT_LEDGER = "event_study_ledger.jsonl"
HEALTH_LEDGER = "data_health_ledger.jsonl"
REPORT_MANIFEST = "report_manifest.json"
EVENT_STUDY_REPORT = "event_study_report.json"
DATA_HEALTH_REPORT = "data_health_report.json"
EPOCH_FIELD = "data_epoch_id"
CLEAR_CONFIRM = "CLEAR-REPORT-HISTORY"
OWNED_FILES = (INDEX, EVENT_LEDGER, HEALTH_LEDGER)
SNAPSHOT_NAME_RE = re.compile(r"^\d{8}T\d{6}Z_[A-Za-z0-9_-]{1,32}$")
NEVER_TOUCH_PREFIXES = ((".puller-backup-", "PULLER_BACKUP"), ("gs-export", "GS_EXPORT_TEMP"))

try:  # optional: zstd is faster and smaller; gzip is always available
    import zstandard as _zstd
except Exception:  # pragma: no cover - depends on the laptop environment
    _zstd = None


def resolve_history_root(root: Optional[str] = None) -> Path:
    return Path(root or os.environ.get("DOXXED_REPORT_HISTORY_DIR") or DEFAULT_HISTORY_ROOT)


def _onedrive(path: Path) -> bool:
    return "onedrive" in {part.casefold() for part in Path(path).resolve().parts}


def _path_parts(path) -> list:
    """Path components split on both separators, so Windows paths are judged the same on any OS."""
    return [part.casefold() for part in re.split(r"[\\/]+", str(path)) if part]


def never_touch_reason(path) -> Optional[str]:
    """Why ``path`` must never be modified or deleted by any reset/cap code, else None.

    Shared by ``clear_for_reset``/``cap_to_epoch`` and ``clean_epoch_wipe.plan_laptop``: operator
    ``.puller-backup-*`` folders, ``gs-export*`` temp files, anything under a Windows user Temp
    folder (``AppData\\Local\\Temp``) and anything under OneDrive.
    """
    parts = _path_parts(path)
    for part in parts:
        for prefix, reason in NEVER_TOUCH_PREFIXES:
            if part.startswith(prefix):
                return reason
    for i in range(len(parts) - 2):
        if parts[i:i + 3] == ["appdata", "local", "temp"]:
            return "WINDOWS_USER_TEMP"
    if "onedrive" in parts:
        return "ONEDRIVE"
    return None


def current_epoch_id(epoch_id: Optional[str] = None) -> Optional[str]:
    """The epoch the history is capped to: ``epoch_id`` if given, else the active DATA_EPOCH_ID manifest's."""
    if epoch_id:
        return str(epoch_id)
    try:
        import data_epoch
        return data_epoch.active_epoch_id()
    except Exception:
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _compress(src: Path, dst_base: Path) -> tuple:
    if _zstd is not None:
        dst = dst_base.with_name(dst_base.name + ".zst")
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            _zstd.ZstdCompressor(level=6).copy_stream(fin, fout)
        return dst, "zstd"
    dst = dst_base.with_name(dst_base.name + ".gz")
    with open(src, "rb") as fin, gzip.open(dst, "wb", compresslevel=GZIP_LEVEL) as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    return dst, "gzip"


def _make_read_only(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IREAD)
    except OSError:
        pass


def _rmtree(path: Path) -> None:
    def onerror(func, target, _exc):
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)
    shutil.rmtree(path, onerror=onerror)


def _append_line(path: Path, doc: dict) -> None:
    """Append one JSON line; a torn last line is terminated, never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_newline = False
    if path.is_file() and path.stat().st_size:
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_newline = handle.read(1) != b"\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        if needs_newline:
            handle.write("\n")
        handle.write(json.dumps(doc, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _ledger_generations(path: Path) -> set:
    return {str(row["generation_id"]) for row in read_ledger(str(path)) if row.get("generation_id")}


def read_ledger(path: str) -> list:
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


# ------------------------------------------------------------------ snapshots
def list_snapshots(root: Path) -> list:
    """Complete snapshots (manifest present) oldest-first; the name starts with the UTC stamp."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / MANIFEST).is_file())


def _previous_files(root: Path) -> tuple:
    snaps = list_snapshots(root)
    if not snaps:
        return None, {}
    return snaps[-1], (_read_json(snaps[-1] / MANIFEST).get("files") or {})


def _generation_exists(root: Path, generation_id: str) -> Optional[str]:
    for row in read_ledger(str(root / INDEX)):
        if row.get("generation_id") == generation_id and (root / str(row.get("path") or "") / MANIFEST).is_file():
            return str(row.get("path"))
    return None


def _report_files(report_dir: Path, patterns: Iterable[str]) -> list:
    out = []
    for name in sorted(os.listdir(report_dir)):
        path = report_dir / name
        if path.is_file() and any(fnmatch.fnmatch(name, p) for p in patterns) and not name.endswith(".tmp"):
            out.append(path)
    return out


def snapshot_report_set(report_dir: str, history_root: Optional[str] = None, *,
                        generation_id: Optional[str] = None,
                        keep_bytes_cap: int = DEFAULT_KEEP_BYTES_CAP,
                        keep_min_generations: int = DEFAULT_KEEP_MIN_GENERATIONS,
                        daily_keep_days: int = DEFAULT_DAILY_KEEP_DAYS,
                        patterns: Iterable[str] = ("*.json",),
                        compress_min_bytes: int = COMPRESS_MIN_BYTES,
                        now: Optional[float] = None, epoch_id: Optional[str] = None) -> dict:
    """Snapshot the full report set of one generation, append the ledgers, cap to the epoch, enforce retention.

    Idempotent per ``generation_id`` (default: ``report_manifest.json``'s). Never raises.
    """
    started = time.perf_counter()
    now = float(now if now is not None else time.time())
    src = Path(report_dir)
    root = resolve_history_root(history_root)
    result: dict = {"schema": SCHEMA, "report_dir": str(src), "history_root": str(root)}
    try:
        if _onedrive(src) or _onedrive(root):
            return {**result, "status": "REFUSED", "reason": "OneDrive paths are never used for history"}
        guard = never_touch_reason(root)
        if guard:
            return {**result, "status": "REFUSED", "reason": f"history root is never-touch: {guard}"}
        if not src.is_dir():
            return {**result, "status": "NO_REPORT_DIR"}
        manifest = _read_json(src / REPORT_MANIFEST)
        gen = str(generation_id or manifest.get("generation_id") or "").strip() or f"nogen-{int(now)}"
        result["generation_id"] = gen
        epoch = current_epoch_id(epoch_id)
        result[EPOCH_FIELD] = epoch
        root.mkdir(parents=True, exist_ok=True)
        _sweep_staging(root, now)
        existing = _generation_exists(root, gen)
        if existing:
            result.update(status="EXISTS", path=str(root / existing))
        else:
            result.update(_write_snapshot(src, root, gen, manifest, tuple(patterns), compress_min_bytes, now,
                                          epoch))
        result["event_study_ledger"] = append_event_study_ledger(str(src), str(root), generation_id=gen, now=now,
                                                                 epoch_id=epoch)
        result["data_health_ledger"] = append_data_health_ledger(str(src), str(root), generation_id=gen, now=now,
                                                                 epoch_id=epoch)
        result["epoch_cap"] = cap_to_epoch(str(root), epoch)
        result["retention"] = enforce_retention(str(root), keep_bytes_cap=keep_bytes_cap,
                                                keep_min_generations=keep_min_generations,
                                                daily_keep_days=daily_keep_days, now=now)
    except Exception as exc:  # history must never stop the analyzer
        result.update(status="ERROR", error=f"{type(exc).__name__}: {exc}")
    result["elapsed_sec"] = round(time.perf_counter() - started, 2)
    return result


def _write_snapshot(src: Path, root: Path, gen: str, manifest: dict, patterns, compress_min_bytes: int,
                    now: float, epoch: Optional[str] = None) -> dict:
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_gen = "".join(c for c in gen if c.isalnum() or c in "-_")[:32] or "nogen"
    name = f"{stamp}_{safe_gen}"
    staging = root / f"{STAGING_PREFIX}{name}"
    if staging.exists():
        _rmtree(staging)
    staging.mkdir(parents=True)
    prev_dir, prev_files = _previous_files(root)
    files, linked, stored_bytes, source_bytes = {}, 0, 0, 0
    for path in _report_files(src, patterns):
        try:
            size = path.stat().st_size
            digest = _sha256(path)
        except OSError:
            continue        # vanished between listing and copy: absent from this snapshot
        entry = {"sha256": digest, "bytes": size}
        prior = prev_files.get(path.name) or {}
        prior_path = prev_dir / prior["stored"] if prev_dir is not None and prior.get("stored") else None
        target = None
        if prior.get("sha256") == digest and prior_path is not None and prior_path.is_file():
            target = staging / prior["stored"]
            try:
                os.link(prior_path, target)
                entry.update(stored=prior["stored"], codec=prior.get("codec"), linked=True)
                linked += 1
            except OSError:
                target = None
        if target is None:
            try:
                if size >= compress_min_bytes:
                    target, codec = _compress(path, staging / path.name)
                else:
                    target, codec = staging / path.name, "none"
                    shutil.copyfile(path, target)
            except OSError:
                continue
            entry.update(stored=target.name, codec=codec, linked=False)
            _make_read_only(target)
        entry["stored_bytes"] = target.stat().st_size
        stored_bytes += 0 if entry["linked"] else entry["stored_bytes"]
        source_bytes += size
        files[path.name] = entry
    doc = {"schema": SCHEMA, "generation_id": gen, EPOCH_FIELD: epoch, "created_at": _iso(now), "created_ts": now,
           "report_dir": str(src), "files": files,
           "report_manifest": {k: manifest.get(k) for k in ("generation_id", "generated_at", "generation_started_at",
                                                            "analyzer_revision", "dataset_epoch",
                                                            "dataset_checksum")},
           "totals": {"files": len(files), "source_bytes": source_bytes, "new_stored_bytes": stored_bytes,
                      "linked_files": linked}}
    with open(staging / MANIFEST, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=1, sort_keys=True)
    _make_read_only(staging / MANIFEST)
    final = root / name
    os.replace(staging, final)
    _append_line(root / INDEX, {"schema": SCHEMA, "generation_id": gen, EPOCH_FIELD: epoch, "path": name,
                                "created_at": _iso(now),
                                "files": len(files), "manifest_sha256": _sha256(final / MANIFEST)})
    return {"status": "WRITTEN", "path": str(final), "files": len(files), "linked_files": linked,
            "source_bytes": source_bytes, "new_stored_bytes": stored_bytes}


def _sweep_staging(root: Path, now: float) -> None:
    for p in root.iterdir():
        if p.is_dir() and p.name.startswith(STAGING_PREFIX):
            try:
                if now - p.stat().st_mtime > STAGING_MAX_AGE_SEC:
                    _rmtree(p)
            except OSError:
                pass


def verify_snapshot(path: str) -> dict:
    """Recompute the sha256 of every stored file's original content against the manifest."""
    folder = Path(path)
    doc = _read_json(folder / MANIFEST)
    bad = []
    for name, entry in (doc.get("files") or {}).items():
        stored = folder / str(entry.get("stored") or "")
        try:
            if entry.get("codec") == "gzip":
                h = hashlib.sha256()
                with gzip.open(stored, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        h.update(chunk)
                raw_digest = h.hexdigest()
            elif entry.get("codec") == "zstd" and _zstd is not None:
                with open(stored, "rb") as handle:
                    raw_digest = hashlib.sha256(_zstd.ZstdDecompressor().stream_reader(handle).read()).hexdigest()
            else:
                raw_digest = _sha256(stored)
        except OSError:
            raw_digest = None
        if raw_digest != entry.get("sha256"):
            bad.append(name)
    return {"files": len(doc.get("files") or {}), "mismatched": bad, "ok": bool(doc) and not bad}


# ------------------------------------------------------------------ retention
def _snapshot_bytes(path: Path, seen_inodes: Optional[set] = None) -> tuple:
    """(bytes counted once per inode across snapshots, bytes freed if this snapshot alone is deleted)."""
    total = freed = 0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        st = f.stat()
        key = (st.st_dev, st.st_ino) if st.st_ino else (str(f),)
        if seen_inodes is None or key not in seen_inodes:
            total += st.st_size
            if seen_inodes is not None:
                seen_inodes.add(key)
        if st.st_nlink <= 1:
            freed += st.st_size
    return total, freed


def _snapshot_day(path: Path) -> str:
    stamp = path.name.split("_", 1)[0]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d")
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def enforce_retention(history_root: str, *, keep_bytes_cap: int = DEFAULT_KEEP_BYTES_CAP,
                      keep_min_generations: int = DEFAULT_KEEP_MIN_GENERATIONS,
                      daily_keep_days: int = DEFAULT_DAILY_KEEP_DAYS, now: Optional[float] = None) -> dict:
    """Delete whole snapshots oldest-first while over the cap; protected ones are never deleted."""
    now = float(now if now is not None else time.time())
    root = Path(history_root)
    snaps = list_snapshots(root)
    protected = set(snaps[-int(keep_min_generations):]) if keep_min_generations > 0 else set()
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    newest_per_day: dict = {}
    for snap in snaps:
        newest_per_day[_snapshot_day(snap)] = snap          # oldest-first: the last one wins
    for day, snap in newest_per_day.items():
        if (today - datetime.strptime(day, "%Y-%m-%d").date()).days < daily_keep_days:
            protected.add(snap)
    seen: set = set()
    sizes = {s: _snapshot_bytes(s, seen) for s in snaps}
    ledgers = sum(p.stat().st_size for p in root.glob("*.jsonl")) if root.is_dir() else 0
    total = sum(t for t, _ in sizes.values()) + ledgers
    deleted, freed_bytes = [], 0
    for snap in snaps:
        if total <= keep_bytes_cap:
            break
        if snap in protected:
            continue
        _, freed = _snapshot_bytes(snap)
        try:
            _rmtree(snap)
        except OSError:
            continue        # in use: try again next run
        deleted.append(snap.name)
        total -= freed
        freed_bytes += freed
    return {"snapshots_before": len(snaps), "deleted": deleted, "freed_bytes": freed_bytes,
            "total_bytes": total, "keep_bytes_cap": keep_bytes_cap, "protected": len(protected),
            "status": "OVER_CAP_PROTECTED" if total > keep_bytes_cap else "OK"}


# ------------------------------------------------------------------ ledgers
def append_event_study_ledger(report_dir: str, history_root: Optional[str] = None, *,
                              generation_id: str, now: Optional[float] = None,
                              epoch_id: Optional[str] = None) -> dict:
    """One line per pre-registered hypothesis (H1-H5 lockbox counters) for this generation."""
    now = float(now if now is not None else time.time())
    path = resolve_history_root(history_root) / EVENT_LEDGER
    report = _read_json(Path(report_dir) / EVENT_STUDY_REPORT)
    if not report:
        return {"status": "NO_REPORT", "appended": 0}
    if str(generation_id) in _ledger_generations(path):
        return {"status": "DUPLICATE_GENERATION", "appended": 0}
    appended = 0
    for h in report.get("hypotheses") or []:
        lock = h.get("lockbox") or {}
        disc = h.get("discovery") or {}
        _append_line(path, {
            "schema": EVENT_LEDGER_SCHEMA, "generation_id": str(generation_id), EPOCH_FIELD: epoch_id,
            "recorded_at": _iso(now),
            "report_generated_ts": report.get("generated_ts"), "dataset_epoch": report.get("dataset_epoch"),
            "registered_utc": (report.get("registry") or {}).get("registered_utc"),
            "hypothesis_id": h.get("id"), "spec_hash": h.get("spec_hash"), "status": h.get("status"),
            "metric": h.get("metric"), "min_lockbox_events": h.get("min_lockbox_events"),
            "lockbox": {k: lock.get(k) for k in ("start_utc", "end_utc", "open", "events_counted", "events_per_day",
                                                 "days_to_min_sample", "scored")},
            "discovery": {k: disc.get(k) for k in ("label", "n_events", "events_with_controls")},
            "span": report.get("span"),
        })
        appended += 1
    return {"status": "APPENDED", "appended": appended}


def append_data_health_ledger(report_dir: str, history_root: Optional[str] = None, *,
                              generation_id: str, now: Optional[float] = None,
                              epoch_id: Optional[str] = None) -> dict:
    """One line per generation with every stream's data-health verdict."""
    now = float(now if now is not None else time.time())
    path = resolve_history_root(history_root) / HEALTH_LEDGER
    report = _read_json(Path(report_dir) / DATA_HEALTH_REPORT)
    if not report:
        return {"status": "NO_REPORT", "appended": 0}
    if str(generation_id) in _ledger_generations(path):
        return {"status": "DUPLICATE_GENERATION", "appended": 0}
    keys = ("status", "rows", "rows_24h", "first_ts", "last_ts", "staleness_sec", "coverage_pct_24h",
            "lag_vs_mirror_head_sec")
    streams = {str(s.get("stream")): {k: s.get(k) for k in keys}
               for s in report.get("streams") or [] if isinstance(s, dict) and s.get("stream")}
    _append_line(path, {
        "schema": HEALTH_LEDGER_SCHEMA, "generation_id": str(generation_id), EPOCH_FIELD: epoch_id,
        "recorded_at": _iso(now),
        "report_generated_ts": report.get("generated_ts"), "dataset_epoch": report.get("dataset_epoch"),
        "status": report.get("status"), "status_counts": report.get("status_counts"),
        "window_sec": report.get("window_sec"), "mirror_head_ts": report.get("mirror_head_ts"),
        "streams": streams,
    })
    return {"status": "APPENDED", "appended": 1}


# ------------------------------------------------------------------ epoch cap and reset
def _owned_snapshot_dir(path: Path) -> bool:
    name = path.name
    if name.startswith(STAGING_PREFIX):
        name = name[len(STAGING_PREFIX):]
    return (path.is_dir() and not path.is_symlink() and bool(SNAPSHOT_NAME_RE.match(name))
            and never_touch_reason(path) is None)


def owned_entries(history_root: str) -> list:
    """Top-level entries of ``history_root`` this module wrote; nothing else is ever modified."""
    root = Path(history_root)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if never_touch_reason(p):
            continue
        if p.name in OWNED_FILES and p.is_file() and not p.is_symlink():
            out.append(p)
        elif _owned_snapshot_dir(p):
            out.append(p)
    return out


def _rewrite_ledger_for_epoch(path: Path, epoch: str) -> int:
    """Atomically drop rows not stamped with ``epoch``; returns the number dropped."""
    if not path.is_file() or path.is_symlink():
        return 0
    rows = read_ledger(str(path))
    keep = [row for row in rows if row.get(EPOCH_FIELD) == epoch]
    dropped = len(rows) - len(keep)
    if not dropped:
        return 0
    tmp = path.with_name(path.name + ".epoch-cap.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        for row in keep:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return dropped


def cap_to_epoch(history_root: str, epoch_id: Optional[str]) -> dict:
    """Keep only ``epoch_id``'s snapshots and rows; a no-op (never a delete) when the epoch is unknown."""
    root = Path(history_root)
    if not epoch_id:
        return {"status": "EPOCH_UNKNOWN", "deleted_snapshots": [], "dropped_rows": {}}
    if never_touch_reason(root) or _onedrive(root):
        return {"status": "REFUSED", "deleted_snapshots": [], "dropped_rows": {}}
    deleted = []
    for snap in list_snapshots(root):
        if not _owned_snapshot_dir(snap):
            continue
        if _read_json(snap / MANIFEST).get(EPOCH_FIELD) == epoch_id:
            continue
        try:
            _rmtree(snap)
        except OSError:
            continue        # in use: capped on the next run
        deleted.append(snap.name)
    dropped = {name: _rewrite_ledger_for_epoch(root / name, epoch_id) for name in OWNED_FILES}
    return {"status": "OK", EPOCH_FIELD: epoch_id, "deleted_snapshots": deleted,
            "dropped_rows": {k: v for k, v in dropped.items() if v}}


def clear_for_reset(history_root: Optional[str] = None, *, confirm: str) -> dict:
    """Empty the report history at a data reset; only ``owned_entries`` are removed, then read back."""
    root = resolve_history_root(history_root)
    result = {"history_root": str(root), "removed": [], "untouched": []}
    if confirm != CLEAR_CONFIRM:
        return {**result, "status": "REFUSED", "reason": f"confirm must be {CLEAR_CONFIRM!r}"}
    guard = never_touch_reason(root)
    if guard or _onedrive(root):
        return {**result, "status": "REFUSED", "reason": f"history root is never-touch: {guard or 'ONEDRIVE'}"}
    if not root.is_dir():
        return {**result, "status": "ABSENT"}
    owned = owned_entries(str(root))
    owned_names = {p.name for p in owned}
    for p in owned:
        try:
            if p.is_dir():
                _rmtree(p)
            else:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                p.unlink()
            result["removed"].append(p.name)
        except OSError as exc:
            result.setdefault("errors", []).append(f"{p.name}: {type(exc).__name__}")
    result["untouched"] = sorted(p.name for p in root.iterdir() if p.name not in owned_names)
    leftover = [p.name for p in owned_entries(str(root))]
    result["readback_leftover"] = leftover
    result["status"] = "CLEARED" if not leftover else "INCOMPLETE"
    return result


def main(argv=None) -> int:
    """Laptop operator CLI (run only with Danish's approval): ``clear-for-reset`` or ``cap-to-epoch``."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m research.report_history")
    sub = ap.add_subparsers(dest="cmd", required=True)
    clear = sub.add_parser("clear-for-reset")
    clear.add_argument("--root")
    clear.add_argument("--confirm", required=True)
    cap = sub.add_parser("cap-to-epoch")
    cap.add_argument("--root")
    cap.add_argument("--epoch-id")
    args = ap.parse_args(argv)
    if args.cmd == "clear-for-reset":
        out = clear_for_reset(args.root, confirm=args.confirm)
        ok = out["status"] in ("CLEARED", "ABSENT")
    else:
        root = resolve_history_root(args.root)
        out = cap_to_epoch(str(root), current_epoch_id(args.epoch_id))
        ok = out["status"] == "OK"
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
