"""Bound derived analyzer storage without touching live trading ledgers.

The analyzer runs every 30 minutes.  Keeping every complete report tree makes
storage grow quickly even though those trees describe the same UTC day.  This
module creates one compact daily evidence snapshot, then keeps one derived
archive per day.  Append-only bot/trade ledgers are inventoried but never
rewritten here because the live bot may be writing them concurrently.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


STATUS_FILE = "research_retention_status.json"
MARKER_FILE = ".research_retention_last_run.json"
DAILY_DIR = Path("research_retention") / "daily"

COMPACT_EVIDENCE_FILES = (
    "research_compact_summary.json",
    "report_manifest.json",
    "executive_summary.txt",
    "research_highlights.txt",
    "research_findings.txt",
    "research_coverage.txt",
    "paused_shadow_research_report.json",
    "type_b_adx_v3_shadow_report.json",
    "historical_trade_cohort_report.json",
    "research_session_index.json",
)

# Authoritative/live inputs are deliberately inventory-only.  They remain
# available for ADX, Type B, replay, and audit research until an explicit fresh
# collection wipe or a future writer-owned rotation protocol handles them.
LIVE_LEDGER_FILES = (
    "trades_3factor.csv",
    "decisions_3factor.csv",
    "pipeline_events_3factor.csv",
    "candles_3factor.csv",
    "signal_snapshot.jsonl",
    "signal_replay.jsonl",
    "trade_lifecycle.jsonl",
    "trade_outcome.jsonl",
    "shadow_outcome.jsonl",
    "shadow_lane_outcome.jsonl",
    "lane_opportunity_capture.jsonl",
    "execution_funnel.jsonl",
)


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, default)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    sample = 1024 * 1024
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if stat.st_size <= sample * 2:
            for chunk in iter(lambda: handle.read(sample), b""):
                digest.update(chunk)
            mode = "full_sha256"
        else:
            digest.update(handle.read(sample))
            handle.seek(max(0, stat.st_size - sample))
            digest.update(handle.read(sample))
            digest.update(str(stat.st_size).encode("ascii"))
            mode = "head_tail_size_sha256"
    return {
        "path": path.name,
        "bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "fingerprint": digest.hexdigest(),
        "fingerprint_mode": mode,
        "retention": "LIVE_LEDGER_NOT_DELETED",
    }


def _folder_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _prune_derived_folders(
    parent: Path,
    *,
    now: datetime,
    retain_days: int,
    minimum_keep: int = 3,
) -> dict:
    if not parent.is_dir():
        return {"path": str(parent), "kept": 0, "deleted": 0, "deleted_bytes": 0}
    folders = sorted(
        (p for p in parent.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    kept_days: set[str] = set()
    deleted = 0
    deleted_bytes = 0
    kept = 0
    for index, folder in enumerate(folders):
        observed = _folder_time(folder)
        day_key = observed.date().isoformat()
        age_days = max(0, (now.date() - observed.date()).days)
        must_keep = index < minimum_keep
        keep = must_keep or (age_days <= retain_days and day_key not in kept_days)
        if keep:
            kept += 1
            kept_days.add(day_key)
            continue
        try:
            size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
            shutil.rmtree(folder)
            deleted += 1
            deleted_bytes += size
        except OSError:
            # A file in use is evidence that this run is not a safe deletion
            # boundary. Leave it for the next daily pass.
            kept += 1
    return {
        "path": str(parent),
        "kept": kept,
        "deleted": deleted,
        "deleted_bytes": deleted_bytes,
    }


def _reconcile_session_index(root: Path) -> None:
    path = root / "research_session_index.json"
    archive_root = root / "research_session_archives"
    if not path.is_file() or not archive_root.is_dir():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        sessions = payload.get("sessions") or []
        payload["sessions"] = [
            row for row in sessions
            if isinstance(row, dict)
            and (archive_root / str(row.get("session_id") or "")).is_dir()
        ]
        _atomic_json(path, payload)
    except (OSError, ValueError, TypeError):
        return


def run_analyzer_retention(
    root: str | Path = ".",
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Create daily evidence and prune redundant derived archives once per day."""
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    interval_hours = _env_int("ANALYZER_RETENTION_INTERVAL_HOURS", 24, 1)
    retain_days = _env_int("ANALYZER_DERIVED_RETENTION_DAYS", 30, 7)
    daily_keep_days = _env_int("ANALYZER_DAILY_EVIDENCE_DAYS", 90, 30)
    marker = root / MARKER_FILE
    if marker.is_file() and not force:
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            previous_at = datetime.fromisoformat(str(previous["completed_at"]).replace("Z", "+00:00"))
            age_hours = (now - previous_at).total_seconds() / 3600
            if age_hours < interval_hours:
                return {
                    "schema": "analyzer_retention_v1",
                    "status": "SKIPPED_INTERVAL",
                    "last_completed_at": previous_at.isoformat(),
                    "next_due_in_hours": round(interval_hours - age_hours, 2),
                }
        except (OSError, ValueError, TypeError, KeyError):
            pass

    daily = root / DAILY_DIR / now.date().isoformat()
    daily.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in COMPACT_EVIDENCE_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, daily / name)
            copied.append(name)

    live_inventory = []
    for name in LIVE_LEDGER_FILES:
        path = root / name
        if path.is_file():
            try:
                live_inventory.append(_fingerprint(path))
            except OSError:
                continue

    evidence = {
        "schema": "daily_research_evidence_v1",
        "generated_at": now.isoformat(),
        "day_utc": now.date().isoformat(),
        "compact_files": copied,
        "live_ledger_inventory": live_inventory,
        "safety": {
            "live_ledgers_deleted": False,
            "reason": "Bot-owned append-only ledgers are never rewritten by the analyzer.",
        },
    }
    _atomic_json(daily / "daily_evidence_manifest.json", evidence)

    prune_results = [
        _prune_derived_folders(
            root / "reports" / "history", now=now, retain_days=retain_days
        ),
        _prune_derived_folders(
            root / "research_session_archives", now=now, retain_days=retain_days
        ),
        _prune_derived_folders(
            root / DAILY_DIR, now=now, retain_days=daily_keep_days
        ),
    ]
    _reconcile_session_index(root)
    status = {
        "schema": "analyzer_retention_v1",
        "status": "COMPLETED",
        "completed_at": now.isoformat(),
        "interval_hours": interval_hours,
        "derived_retention_days": retain_days,
        "daily_evidence_days": daily_keep_days,
        "daily_snapshot": str(daily),
        "compact_files": len(copied),
        "live_ledgers_inventoried": len(live_inventory),
        "live_ledgers_deleted": 0,
        "pruned": prune_results,
        "deleted_bytes": sum(int(row.get("deleted_bytes") or 0) for row in prune_results),
    }
    _atomic_json(root / STATUS_FILE, status)
    _atomic_json(marker, {"completed_at": now.isoformat(), "status_file": STATUS_FILE})
    return status
