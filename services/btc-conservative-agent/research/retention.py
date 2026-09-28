"""Bound analyzer storage without touching files owned by a live writer.

The analyzer runs every 30 minutes.  Keeping every complete report tree makes
storage grow quickly even though those trees describe the same UTC day.  This
module creates one compact daily evidence snapshot, then keeps one derived
archive per day.  Current append-only bot/trade ledgers are inventoried but
never rewritten here because the live bot may be writing them concurrently.
Closed numeric JSONL rotations are immutable and may be removed only after
their fingerprints are written to the daily evidence manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


STATUS_FILE = "research_retention_status.json"
MARKER_FILE = ".research_retention_last_run.json"
DAILY_DIR = Path("research_retention") / "daily"
RETENTION_SCHEMA = "analyzer_retention_v3"
DEFAULT_RAW_MIRROR_CAP_GIB = 25

COMPACT_EVIDENCE_FILES = (
    "analysis_summary.md",
    "research_compact_summary.json",
    "report_manifest.json",
    "executive_summary.txt",
    "research_highlights.txt",
    "research_findings.txt",
    "research_coverage.txt",
    "paused_shadow_research_report.json",
    "historical_trade_cohort_report.json",
    "research_session_index.json",
)

# Authoritative/live inputs are deliberately inventory-only.  They remain
# available for replay and audit research until an explicit fresh
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


def _write_readable_markdown_summary(root: Path, now: datetime) -> Path:
    """Persist a small human-readable receipt before raw inputs may be pruned."""
    compact_path = root / "research_compact_summary.json"
    compact: dict = {}
    if compact_path.is_file():
        try:
            compact = json.loads(compact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            compact = {}
    performance = compact.get("performance") or {}
    coverage = compact.get("coverage") or {}
    dataset = compact.get("dataset") or {}
    findings_path = root / "research_findings.txt"
    findings = "No generated findings were available for this pass."
    if findings_path.is_file():
        try:
            findings = findings_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    lines = [
        "# Analyzer evidence summary",
        "",
        f"- Evidence generated: {now.isoformat()}",
        f"- Analyzer output generated: {compact.get('generated_at') or 'unknown'}",
        f"- Analyzer version: {compact.get('analyzer_version') or 'unknown'}",
        f"- Scope: {compact.get('data_scope') or compact.get('session_scope') or 'unknown'}",
        f"- Session hours: {compact.get('session_hours', 'unknown')}",
        f"- Trades analyzed: {performance.get('trades', dataset.get('csv_trades', 0))}",
        f"- Win rate: {performance.get('win_rate_pct', 'unknown')}%",
        f"- Net PnL: ${performance.get('net_pnl_usd', 'unknown')}",
        f"- Expectancy per trade: ${performance.get('expectancy_usd', 'unknown')}",
        f"- MFE capture: {performance.get('mfe_capture_pct', 'unknown')}%",
        f"- Edge verdict: {compact.get('edge_verdict') or 'unknown'}",
        f"- Statistical confidence: {coverage.get('confidence_status') or 'unknown'}",
        "",
        "## Generated findings",
        "",
        "```text",
        findings,
        "```",
        "",
        "The accompanying JSON manifest contains fingerprints and inventories for auditability.",
        "",
    ]
    path = root / "analysis_summary.md"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(str(line) for line in lines), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256()
    chunk_size = 1024 * 1024
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return {
        "path": path.name,
        "bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "fingerprint": digest.hexdigest(),
        "fingerprint_mode": "full_sha256",
        "retention": "LIVE_LEDGER_NOT_DELETED",
    }


def _closed_jsonl_rotations(root: Path) -> list[Path]:
    """Return immutable append-ledger ``*.N`` siblings, never active files."""
    found = []
    safe_bases = (".jsonl", ".csv", ".log", ".txt")
    for path in root.iterdir() if root.is_dir() else ():
        if not path.is_file() or not path.suffix[1:].isdigit():
            continue
        base = Path(path.name.rsplit(".", 1)[0])
        if base.suffix.lower() in safe_bases:
            found.append(path)
    return sorted(found, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def _tree_bytes(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return total
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += int(path.stat().st_size)
        except OSError:
            continue
    return total


def _write_storage_receipt(path: Path, *, now: datetime, cap_bytes: int,
                           before_bytes: int, candidates: list[dict]) -> None:
    """Write human-readable evidence before any cap-driven deletion."""
    lines = [
        "# Raw mirror storage retention receipt", "",
        f"- Generated: {now.isoformat()}",
        f"- Mirror bytes before enforcement: {before_bytes}",
        f"- Hard admission cap bytes: {cap_bytes}",
        "- Safety: active ledgers and unacknowledged files are never deletion candidates.",
        "- Candidate rule: closed numeric append-only raw rotation, fingerprinted in the accompanying daily manifest.",
        "", "## Fingerprinted candidates", "",
    ]
    if not candidates:
        lines.append("No eligible closed rotations were present.")
    else:
        for row in candidates:
            lines.append(
                f"- `{row['path']}` - {row['bytes']} bytes - "
                f"{row['fingerprint_mode']} `{row['fingerprint']}`"
            )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_storage_outcome(path: Path, outcome: dict) -> None:
    """Atomically add the completed action list to the readable receipt."""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = "# Raw mirror storage retention receipt\n"
    lines = [
        "", "## Enforcement outcome", "",
        f"- Status: {outcome.get('status')}",
        f"- Mirror bytes after enforcement: {outcome.get('after_bytes')}",
        f"- Cap usage: {outcome.get('usage_pct')}%",
        f"- Fingerprint mismatches retained: {outcome.get('fingerprint_mismatches', 0)}",
        f"- Closed rotations deleted: {outcome.get('deleted', 0)}",
    ]
    for name in outcome.get("deleted_files") or []:
        lines.append(f"  - `{name}`")
    lines.extend(["", f"Safety result: {outcome.get('reason')}", ""])
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(existing.rstrip() + "\n" + "\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def _enforce_raw_mirror_cap(
    data_root: Path, *, cap_bytes: int, acknowledged_inventory: list[dict]
) -> dict:
    """Delete only receipt-acknowledged closed rotations; otherwise fail safe."""
    before = _tree_bytes(data_root)
    deleted: list[str] = []
    deleted_bytes = 0
    inventory = {str(row.get("path")): row for row in acknowledged_inventory}
    candidates: list[Path] = []
    fingerprint_mismatches = 0
    for path in _closed_jsonl_rotations(data_root):
        row = inventory.get(path.name)
        if not row or not row.get("fingerprint"):
            continue
        try:
            current_fingerprint = _fingerprint(path)
        except OSError:
            continue
        if (
            current_fingerprint.get("fingerprint") != row.get("fingerprint")
            or current_fingerprint.get("bytes") != row.get("bytes")
            or current_fingerprint.get("fingerprint_mode") != row.get("fingerprint_mode")
        ):
            fingerprint_mismatches += 1
            continue
        candidates.append(path)
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name))
    current = before
    for path in candidates:
        if current <= cap_bytes:
            break
        try:
            size = int(path.stat().st_size)
            path.unlink()
            deleted.append(path.name)
            deleted_bytes += size
            current -= size
        except OSError:
            continue
    after = _tree_bytes(data_root)
    return {
        "status": "WITHIN_CAP" if after <= cap_bytes else "FAIL_SAFE_CAP_EXCEEDED",
        "cap_bytes": cap_bytes,
        "before_bytes": before,
        "after_bytes": after,
        "usage_pct": round((after / cap_bytes) * 100, 3) if cap_bytes else 0,
        "acknowledged_candidates": len(candidates),
        "fingerprint_mismatches": fingerprint_mismatches,
        "deleted": len(deleted),
        "deleted_bytes": deleted_bytes,
        "deleted_files": deleted,
        "unsafe_files_deleted": 0,
        "reason": (
            "Only receipt-acknowledged closed rotations were eligible."
            if after <= cap_bytes else
            "Active or unacknowledged data exceeds the cap; sync admission must remain blocked."
        ),
    }


def _rotation_base(path: Path) -> str:
    return path.name.rsplit(".", 1)[0]


def _prune_closed_rotations(
    paths: list[Path],
    *,
    now: datetime,
    minimum_age_hours: int,
    keep_latest: int,
    acknowledged_inventory: list[dict] | None = None,
) -> dict:
    """Delete only fingerprinted, closed rotations outside the hot window."""
    by_base: dict[str, list[Path]] = {}
    for path in paths:
        by_base.setdefault(_rotation_base(path), []).append(path)
    deleted = []
    deleted_bytes = 0
    kept = []
    inventory = {
        str(row.get("path")): row for row in (acknowledged_inventory or [])
    }
    fingerprint_mismatches = 0
    for base, siblings in sorted(by_base.items()):
        siblings.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
        for index, path in enumerate(siblings):
            try:
                stat = path.stat()
            except OSError:
                continue
            age_hours = max(0.0, (now.timestamp() - stat.st_mtime) / 3600.0)
            if index < keep_latest or age_hours < minimum_age_hours:
                kept.append(path.name)
                continue
            acknowledged = inventory.get(path.name)
            try:
                current_fingerprint = _fingerprint(path)
            except OSError:
                kept.append(path.name)
                continue
            if (
                not acknowledged
                or current_fingerprint.get("fingerprint") != acknowledged.get("fingerprint")
                or current_fingerprint.get("bytes") != acknowledged.get("bytes")
                or current_fingerprint.get("fingerprint_mode") != acknowledged.get("fingerprint_mode")
            ):
                kept.append(path.name)
                fingerprint_mismatches += 1
                continue
            try:
                path.unlink()
                deleted.append(path.name)
                deleted_bytes += int(stat.st_size)
            except OSError:
                kept.append(path.name)
    return {
        "inventoried": len(paths),
        "kept": len(kept),
        "deleted": len(deleted),
        "deleted_bytes": deleted_bytes,
        "deleted_files": deleted,
        "minimum_age_hours": minimum_age_hours,
        "keep_latest_per_ledger": keep_latest,
        "fingerprint_mismatches": fingerprint_mismatches,
    }


def _research_db_inventory(path: Path) -> dict:
    """Capture compact counts before any raw-row retirement."""
    if not path.is_file():
        return {}
    tables = (
        "environment_genome", "market_genome", "decision_genome",
        "execution_genome", "lifecycle_genome", "trade_genome", "research_events",
    )
    out = _fingerprint(path)
    out["retention"] = "RAW_DB_HIGH_FREQUENCY_ROWS_MAY_BE_PRUNED"
    out["table_counts"] = {}
    try:
        with closing(sqlite3.connect(str(path), timeout=2.5)) as conn:
            conn.execute("PRAGMA busy_timeout=2500")
            for table in tables:
                try:
                    out["table_counts"][table] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
                except sqlite3.DatabaseError:
                    continue
    except sqlite3.DatabaseError as exc:
        out["inventory_error"] = str(exc)
    return out


def _prune_research_db_raw(path: Path, *, now: datetime, retain_hours: int) -> dict:
    """Retire only high-frequency raw rows; preserve decisions/trades and cohorts.

    No VACUUM is attempted while the bot may be writing. SQLite can reuse freed
    pages immediately; an exclusive physical compaction remains a controlled
    maintenance action at a stopped-bot boundary.
    """
    result = {
        "status": "NOT_FOUND",
        "retain_hours": retain_hours,
        "rows_deleted": 0,
        "by_table": {},
        "vacuum_performed": False,
    }
    if not path.is_file():
        return result
    cutoff = datetime.fromtimestamp(
        now.timestamp() - (retain_hours * 3600), tz=timezone.utc
    ).isoformat()
    try:
        with closing(sqlite3.connect(str(path), timeout=2.5)) as conn:
            conn.execute("PRAGMA busy_timeout=2500")
            conn.execute("BEGIN IMMEDIATE")
            for table in ("research_events", "lifecycle_genome"):
                before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                conn.execute(
                    f"DELETE FROM {table} WHERE ts IS NOT NULL AND julianday(ts) < julianday(?)",
                    (cutoff,),
                )
                after = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                result["by_table"][table] = {"before": before, "after": after, "deleted": before - after}
                result["rows_deleted"] += before - after
            conn.commit()
            result["freelist_pages"] = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            result["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
            result["reclaimable_bytes"] = result["freelist_pages"] * result["page_size"]
        result["status"] = "COMPLETED"
    except sqlite3.DatabaseError as exc:
        result["status"] = "SKIPPED_BUSY_OR_ERROR"
        result["error"] = str(exc)
    return result


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
    data_root: str | Path | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Create evidence and bound reports plus the analyzer's raw-data mirror.

    ``root`` owns the readable reports and retention receipts. ``data_root``
    owns the downloaded ledgers. They are normally the same directory, but the
    production desktop analyzer deliberately writes reports outside its
    ``fly-data-mirror``. Keeping the roots explicit prevents retention from
    silently inventorying zero raw files while that mirror grows forever.
    """
    root = Path(root).resolve()
    data_root = Path(data_root).resolve() if data_root is not None else root
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    interval_hours = _env_int("ANALYZER_RETENTION_INTERVAL_HOURS", 24, 1)
    retain_days = _env_int("ANALYZER_DERIVED_RETENTION_DAYS", 30, 7)
    daily_keep_days = _env_int("ANALYZER_DAILY_EVIDENCE_DAYS", 90, 30)
    rotation_age_hours = _env_int("ANALYZER_ROTATED_RAW_RETENTION_HOURS", 24, 6)
    rotation_keep_latest = _env_int("ANALYZER_ROTATED_RAW_KEEP_LATEST", 2, 1)
    raw_db_retain_hours = _env_int("ANALYZER_RAW_DB_RETENTION_HOURS", 72, 24)
    raw_mirror_cap_gib = _env_int(
        "ANALYZER_RAW_MIRROR_CAP_GIB", DEFAULT_RAW_MIRROR_CAP_GIB, 1
    )
    raw_mirror_cap_bytes = raw_mirror_cap_gib * 1024 * 1024 * 1024
    marker = root / MARKER_FILE
    if marker.is_file() and not force:
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous.get("schema") != RETENTION_SCHEMA:
                raise ValueError("retention schema upgrade due")
            previous_at = datetime.fromisoformat(str(previous["completed_at"]).replace("Z", "+00:00"))
            age_hours = (now - previous_at).total_seconds() / 3600
            if age_hours < interval_hours:
                # Derived archives are cheap to evaluate and grow on every
                # analyzer pass. Bound them every cycle even though raw-ledger
                # fingerprinting/database pruning remains a daily operation.
                derived_pruned = [
                    _prune_derived_folders(
                        root / "reports" / "history", now=now, retain_days=retain_days
                    ),
                    _prune_derived_folders(
                        root / "research_session_archives", now=now, retain_days=retain_days
                    ),
                ]
                _reconcile_session_index(root)
                skipped = {
                    "schema": RETENTION_SCHEMA,
                    "status": "SKIPPED_INTERVAL",
                    "last_completed_at": previous_at.isoformat(),
                    "next_due_in_hours": round(interval_hours - age_hours, 2),
                    "report_root": str(root),
                    "data_root": str(data_root),
                    "derived_pruned": derived_pruned,
                    "derived_deleted_bytes": sum(
                        int(row.get("deleted_bytes") or 0) for row in derived_pruned
                    ),
                }
                # The repository or mirror may have moved since the last daily
                # retention pass.  Leaving the prior status file untouched made
                # the read-only dashboard advertise obsolete (notably OneDrive)
                # paths for up to 24 hours even though the analyzer was already
                # running from the canonical location.  Refresh public metadata
                # on every analyzer cycle without repeating destructive cleanup.
                prior_status: dict = {}
                status_path = root / STATUS_FILE
                try:
                    prior_status = json.loads(status_path.read_text(encoding="utf-8"))
                    if not isinstance(prior_status, dict):
                        prior_status = {}
                except (OSError, ValueError, TypeError):
                    prior_status = {}
                prior_data_root = str(prior_status.get("data_root") or "")
                refreshed = {**prior_status, **skipped}
                refreshed["pruned"] = derived_pruned
                prior_snapshot = Path(str(prior_status.get("daily_snapshot") or ""))
                try:
                    prior_snapshot.relative_to(root)
                    refreshed["daily_snapshot"] = str(prior_snapshot)
                except (ValueError, OSError):
                    refreshed["daily_snapshot"] = None
                if prior_data_root and prior_data_root != str(data_root):
                    for key in (
                        "raw_mirror_bytes",
                        "raw_mirror_usage_pct",
                        "raw_mirror_cap_status",
                    ):
                        refreshed.pop(key, None)
                _atomic_json(status_path, refreshed)
                return refreshed
        except (OSError, ValueError, TypeError, KeyError):
            pass

    daily = root / DAILY_DIR / now.date().isoformat()
    daily.mkdir(parents=True, exist_ok=True)
    _write_readable_markdown_summary(root, now)
    copied: list[str] = []
    for name in COMPACT_EVIDENCE_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, daily / name)
            copied.append(name)

    live_inventory = []
    for name in LIVE_LEDGER_FILES:
        path = data_root / name
        if path.is_file():
            try:
                live_inventory.append(_fingerprint(path))
            except OSError:
                continue

    rotation_paths = _closed_jsonl_rotations(data_root)
    rotation_inventory = []
    for path in rotation_paths:
        try:
            row = _fingerprint(path)
            row["retention"] = "CLOSED_ROTATION_FINGERPRINTED_BEFORE_PRUNE"
            rotation_inventory.append(row)
        except OSError:
            continue
    db_path = data_root / "research.db"
    db_inventory = _research_db_inventory(db_path)

    evidence = {
        "schema": "daily_research_evidence_v2",
        "generated_at": now.isoformat(),
        "day_utc": now.date().isoformat(),
        "report_root": str(root),
        "data_root": str(data_root),
        "compact_files": copied,
        "live_ledger_inventory": live_inventory,
        "closed_rotation_inventory": rotation_inventory,
        "research_db_inventory": db_inventory,
        "safety": {
            "live_ledgers_deleted": False,
            "reason": (
                "Active bot-owned ledgers and decision/trade genome tables are preserved. "
                "Only fingerprinted closed rotations and expired high-frequency raw rows are pruned."
            ),
        },
    }
    # Persist the evidence receipt before removing any source data.
    _atomic_json(daily / "daily_evidence_manifest.json", evidence)
    _write_storage_receipt(
        daily / "storage_retention_receipt.md",
        now=now,
        cap_bytes=raw_mirror_cap_bytes,
        before_bytes=_tree_bytes(data_root),
        candidates=rotation_inventory,
    )

    rotation_prune = _prune_closed_rotations(
        rotation_paths,
        now=now,
        minimum_age_hours=rotation_age_hours,
        keep_latest=rotation_keep_latest,
        acknowledged_inventory=rotation_inventory,
    )
    db_prune = _prune_research_db_raw(
        db_path,
        now=now,
        retain_hours=raw_db_retain_hours,
    )
    cap_enforcement = _enforce_raw_mirror_cap(
        data_root,
        cap_bytes=raw_mirror_cap_bytes,
        acknowledged_inventory=rotation_inventory,
    )
    _append_storage_outcome(
        daily / "storage_retention_receipt.md",
        {
            **cap_enforcement,
            "deleted": int(rotation_prune.get("deleted") or 0)
            + int(cap_enforcement.get("deleted") or 0),
            "deleted_files": list(rotation_prune.get("deleted_files") or [])
            + list(cap_enforcement.get("deleted_files") or []),
            "fingerprint_mismatches": int(
                rotation_prune.get("fingerprint_mismatches") or 0
            ) + int(cap_enforcement.get("fingerprint_mismatches") or 0),
        },
    )
    evidence["closed_rotation_prune"] = rotation_prune
    evidence["research_db_prune"] = db_prune
    evidence["raw_mirror_cap"] = cap_enforcement
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
        "schema": RETENTION_SCHEMA,
        "status": "COMPLETED",
        "completed_at": now.isoformat(),
        "interval_hours": interval_hours,
        "derived_retention_days": retain_days,
        "daily_evidence_days": daily_keep_days,
        "rotated_raw_retention_hours": rotation_age_hours,
        "rotated_raw_keep_latest": rotation_keep_latest,
        "raw_db_retention_hours": raw_db_retain_hours,
        "daily_snapshot": str(daily),
        "data_root": str(data_root),
        "compact_files": len(copied),
        "live_ledgers_inventoried": len(live_inventory),
        "live_ledgers_deleted": 0,
        "rotated_raw_inventoried": rotation_prune["inventoried"],
        "rotated_raw_deleted": rotation_prune["deleted"],
        "rotated_raw_deleted_bytes": rotation_prune["deleted_bytes"],
        "raw_db_rows_deleted": db_prune["rows_deleted"],
        "raw_db_reclaimable_bytes": int(db_prune.get("reclaimable_bytes") or 0),
        "raw_db_status": db_prune["status"],
        "raw_mirror_cap_gib": raw_mirror_cap_gib,
        "raw_mirror_cap_bytes": raw_mirror_cap_bytes,
        "raw_mirror_bytes": cap_enforcement["after_bytes"],
        "raw_mirror_usage_pct": cap_enforcement["usage_pct"],
        "raw_mirror_cap_status": cap_enforcement["status"],
        "cap_deleted_rotations": cap_enforcement["deleted"],
        "cap_deleted_bytes": cap_enforcement["deleted_bytes"],
        "pruned": prune_results,
        "deleted_bytes": (
            sum(int(row.get("deleted_bytes") or 0) for row in prune_results)
            + int(rotation_prune["deleted_bytes"])
            + int(cap_enforcement["deleted_bytes"])
        ),
    }
    _atomic_json(root / STATUS_FILE, status)
    _atomic_json(marker, {
        "schema": RETENTION_SCHEMA,
        "completed_at": now.isoformat(),
        "status_file": STATUS_FILE,
    })
    return status
