"""Seal analyzer conclusions before destructive Fresh Collection resets.

Past-analysis bundles deliberately contain derived evidence only: compact
summaries, reports, manifests, and source-file fingerprints.  Raw CSV, JSONL,
SQLite, and rotating log payloads are never copied into this folder, so the
operator can retain the conclusions without recreating the disk-growth problem
that Fresh Collection is intended to solve.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


PAST_ANALYSIS_DIR = "past_analysis"
PAST_ANALYSIS_SCHEMA = "past_analysis_v1"

CORE_FILES = (
    "executive_summary.txt",
    "research_highlights.txt",
    "research_findings.txt",
    "research_coverage.txt",
    "research_deep_dive_index.txt",
    "analysis_dashboard.html",
    "analyzer_run.log",
    "research_compact_summary.json",
    "report_manifest.json",
    "analyzer_integrity_report.json",
    "research_retention_status.json",
    "historical_trade_cohort_report.json",
    "paused_shadow_research_report.json",
    "type_b_adx_v3_shadow_report.json",
    "lane_definition_report.json",
    "lane_retirement_report.json",
)

RAW_SOURCE_NAMES = (
    "trades_3factor.csv",
    "decisions_3factor.csv",
    "blocked_signals_3factor.csv",
    "ai_tranche_log.csv",
    "pipeline_events_3factor.csv",
    "signal_snapshot.jsonl",
    "signal_replay.jsonl",
    "trade_outcome.jsonl",
    "shadow_outcome.jsonl",
    "shadow_lane_outcome.jsonl",
    "lane_opportunity_capture.jsonl",
    "research.db",
)

DERIVED_SUFFIXES = frozenset({".json", ".txt", ".html"})


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {} if default is None else default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256()
    sample = 1024 * 1024
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
        "bytes": int(stat.st_size),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "fingerprint": digest.hexdigest(),
        "fingerprint_mode": mode,
        "included_in_bundle": False,
    }


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return cleaned.strip("-._") or "analysis"


def _copy_file(root: Path, destination: Path, source: Path, copied: list[dict]) -> None:
    source = source.resolve()
    if not source.is_file() or root not in source.parents:
        return
    relative = source.relative_to(root)
    relative_name = relative.as_posix()
    if any(row.get("path") == relative_name for row in copied):
        return
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    copied.append({
        "path": relative_name,
        "bytes": int(target.stat().st_size),
        "sha256": _sha256(target),
    })


def _summary_text(compact: dict, *, archive_id: str, reason: str, created_at: str) -> str:
    performance = compact.get("performance") or {}
    coverage = compact.get("coverage") or {}
    findings = compact.get("key_findings") or []
    lines = [
        "DOXXED CRYPTO - PRESERVED PAST ANALYSIS",
        "=" * 46,
        f"Archive: {archive_id}",
        f"Sealed at: {created_at}",
        f"Reason: {reason}",
        f"Analyzer version: {compact.get('analyzer_sync_id') or compact.get('analyzer_version') or 'unknown'}",
        f"Analysis generated at: {compact.get('generated_at') or 'unknown'}",
        f"Data scope: {compact.get('data_scope') or 'unknown'}",
        "",
        "Headline result",
        f"- Trades: {performance.get('trades', 0)}",
        f"- Win rate: {performance.get('win_rate_pct', 0)}%",
        f"- Net P&L: ${performance.get('net_pnl_usd', 0)}",
        f"- EV/trade: ${performance.get('expectancy_usd', 0)}",
        f"- MFE capture: {performance.get('mfe_capture_pct', 0)}%",
        f"- Sample confidence: {coverage.get('confidence_status') or 'unknown'}",
        f"- Confidence note: {coverage.get('confidence_note') or 'n/a'}",
        "",
        "Findings",
    ]
    lines.extend(f"{index}. {finding}" for index, finding in enumerate(findings, 1))
    lines.extend([
        "",
        "Preservation policy",
        "- This folder contains derived reports and integrity evidence only.",
        "- Bulky CSV, JSONL, SQLite, and rotated runtime logs are fingerprinted, not copied.",
        "- Historical results remain downloadable without retaining the raw source payloads.",
        "",
    ])
    return "\n".join(lines)


def seal_past_analysis(
    root: str | Path = ".",
    *,
    reason: str = "manual",
    now: datetime | None = None,
    archive_id: str | None = None,
) -> dict:
    """Create an immutable compact analysis snapshot and integrity manifest."""
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    compact_path = root / "research_compact_summary.json"
    report_manifest_path = root / "report_manifest.json"
    if not compact_path.is_file() or not report_manifest_path.is_file():
        raise RuntimeError("final analyzer summary/manifest missing; run analyzer before Fresh Collection")
    compact = _json(compact_path)
    report_manifest = _json(report_manifest_path)
    if not compact or not report_manifest:
        raise RuntimeError("final analyzer summary/manifest is unreadable")

    generated = compact.get("generated_at") or report_manifest.get("generated_at") or now.isoformat()
    if archive_id is None:
        stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%d_%H%M%SZ")
        archive_id = f"{stamp}_{_safe_id(reason)}"
    archive_id = _safe_id(archive_id)
    parent = root / PAST_ANALYSIS_DIR
    destination = parent / archive_id
    if destination.exists():
        raise FileExistsError(f"past analysis already exists: {archive_id}")
    destination.mkdir(parents=True, exist_ok=False)

    copied: list[dict] = []
    try:
        for name in CORE_FILES:
            _copy_file(root, destination, root / name, copied)
        for pattern in ("*_report.json", "*_scorecard.json"):
            for source in sorted(root.glob(pattern)):
                _copy_file(root, destination, source, copied)
        for relative_dir in (Path("reports") / "all_data", Path("reports") / "genome"):
            directory = root / relative_dir
            if directory.is_dir():
                for source in sorted(directory.rglob("*")):
                    if source.is_file() and source.suffix.lower() in DERIVED_SUFFIXES:
                        _copy_file(root, destination, source, copied)

        raw_inventory = []
        for name in RAW_SOURCE_NAMES:
            source = root / name
            if source.is_file():
                raw_inventory.append(_fingerprint(source))

        created_at = now.astimezone(timezone.utc).isoformat()
        summary_path = destination / "FINAL_ANALYSIS_SUMMARY.txt"
        summary_path.write_text(
            _summary_text(compact, archive_id=archive_id, reason=reason, created_at=created_at),
            encoding="utf-8",
        )
        copied.append({
            "path": summary_path.relative_to(destination).as_posix(),
            "bytes": int(summary_path.stat().st_size),
            "sha256": _sha256(summary_path),
        })

        manifest = {
            "schema": PAST_ANALYSIS_SCHEMA,
            "archive_id": archive_id,
            "created_at": created_at,
            "reason": reason,
            "analysis_generated_at": generated,
            "analyzer_sync_id": compact.get("analyzer_sync_id"),
            "data_scope": compact.get("data_scope"),
            "performance": compact.get("performance") or {},
            "coverage": compact.get("coverage") or {},
            "key_findings": compact.get("key_findings") or [],
            "files": copied,
            "source_inventory": raw_inventory,
            "raw_payloads_included": False,
            "integrity": {
                "method": "sha256",
                "file_count": len(copied),
                "total_bytes": sum(int(row["bytes"]) for row in copied),
            },
        }
        manifest_path = destination / "past_analysis_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["manifest_sha256"] = _sha256(manifest_path)

        index_path = parent / "index.json"
        index = _json(index_path, {"schema": PAST_ANALYSIS_SCHEMA, "analyses": []})
        analyses = [
            row for row in (index.get("analyses") or [])
            if isinstance(row, dict) and row.get("archive_id") != archive_id
        ]
        analyses.insert(0, {
            "archive_id": archive_id,
            "created_at": created_at,
            "analysis_generated_at": generated,
            "reason": reason,
            "performance": compact.get("performance") or {},
            "path": str(destination),
        })
        index_path.write_text(
            json.dumps({"schema": PAST_ANALYSIS_SCHEMA, "analyses": analyses}, indent=2),
            encoding="utf-8",
        )
        return manifest
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def list_past_analyses(root: str | Path = ".") -> list[dict]:
    root = Path(root).resolve()
    parent = root / PAST_ANALYSIS_DIR
    index = _json(parent / "index.json", {"analyses": []})
    rows = []
    for row in index.get("analyses") or []:
        if not isinstance(row, dict):
            continue
        archive_id = _safe_id(row.get("archive_id") or "")
        if (parent / archive_id / "past_analysis_manifest.json").is_file():
            rows.append(row)
    return rows


def latest_past_analysis(root: str | Path = ".") -> Path | None:
    root = Path(root).resolve()
    rows = list_past_analyses(root)
    if not rows:
        return None
    return root / PAST_ANALYSIS_DIR / _safe_id(rows[0].get("archive_id") or "")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seal compact analyzer evidence before Fresh Collection")
    parser.add_argument("--root", default=".")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args()
    print(json.dumps(seal_past_analysis(args.root, reason=args.reason), indent=2))
