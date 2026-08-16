"""Atomic, content-addressed research session archives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "research_session_archive_v2"
MANIFEST = "archive_manifest.json"
EVIDENCE_NAMES = ("relay_lifecycle_evidence_v1.json", "counterfactual.jsonl")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str, *, reports: bool = False) -> Path:
    value = str(value or "").replace("\\", "/")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member: {value}")
    if reports:
        if value != path.name:
            raise ValueError(f"unsafe report member: {value}")
        path = Path("reports") / path.name
    elif value != path.name:
        raise ValueError(f"unsafe text artifact: {value}")
    return path


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _preserve_evidence(source: Path, staging: Path, object_root: Path) -> Path:
    suffix = "".join(source.suffixes)
    object_root.mkdir(parents=True, exist_ok=True)
    temp = object_root / f".evidence.{uuid.uuid4().hex}.tmp"
    _copy(source, temp)
    digest = _sha256(temp)
    immutable = object_root / f"{digest}{suffix}"
    if not immutable.is_file():
        try:
            try:
                os.replace(temp, immutable)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
    else:
        temp.unlink(missing_ok=True)
    if _sha256(immutable) != digest:
        raise RuntimeError(f"immutable evidence object failed verification: {source.name}")
    target = staging / "evidence" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(immutable, target)
    except OSError:
        _copy(immutable, target)
    return target


def create_archive(
    root: str | Path,
    payload: dict,
    archive_root: str | Path,
    *,
    evidence_root: str | Path | None = None,
) -> Path:
    root = Path(root).resolve()
    evidence_root = Path(evidence_root or root).resolve()
    archive_root = Path(archive_root).resolve()
    report_manifest_path = root / "report_manifest.json"
    report_manifest = json.loads(report_manifest_path.read_text(encoding="utf-8"))
    if report_manifest.get("schema") != "report_manifest_v1":
        raise ValueError("completed report_manifest_v1 is required")
    reports = report_manifest.get("reports")
    text_artifacts = report_manifest.get("text_artifacts")
    if not isinstance(reports, list) or len(reports) != int(report_manifest.get("report_count", -1)):
        raise ValueError("report manifest count is inconsistent")
    if not isinstance(text_artifacts, list):
        raise ValueError("report manifest text artifacts are missing")

    sources: dict[Path, Path] = {Path("report_manifest.json"): report_manifest_path}
    for value in text_artifacts:
        relative = _safe_relative(value)
        if relative in sources:
            raise ValueError(f"duplicate archive member: {relative.as_posix()}")
        sources[relative] = root / relative
    for row in reports:
        if not isinstance(row, dict):
            raise ValueError("invalid report manifest row")
        relative = _safe_relative(row.get("file"), reports=True)
        if relative in sources:
            raise ValueError(f"duplicate archive member: {relative.as_posix()}")
        source = root / relative
        if source.is_file() and source.stat().st_size != int(row.get("size_bytes", -1)):
            raise ValueError(f"report size does not match manifest: {relative.as_posix()}")
        sources[relative] = source
    missing = [relative.as_posix() for relative, source in sources.items() if not source.is_file()]
    if missing:
        raise FileNotFoundError(f"required archive artifacts missing: {', '.join(missing)}")

    provenance = report_manifest.get("analysis_provenance") or {}
    analyzer_revision = str(provenance.get("generation_revision") or "").strip()
    source_data_revision = str(provenance.get("source_data_revision") or "").strip()
    cohort_schema = str(provenance.get("cohort_schema") or "").strip()
    fresh_epoch = report_manifest.get("fresh_epoch") or {}
    if not re.fullmatch(r"[0-9a-fA-F]{40}", analyzer_revision):
        raise ValueError("archive requires a full analyzer Git revision")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", source_data_revision):
        raise ValueError("archive requires a complete source-data revision")
    if not cohort_schema:
        raise ValueError("archive requires a cohort schema")

    archive_root.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    final = archive_root / f"session_{stamp}_{nonce[:12]}"
    staging = archive_root / f".staging-{nonce}"
    object_root = archive_root / "_evidence_objects"
    try:
        staging.mkdir(parents=False, exist_ok=False)
        for relative, source in sources.items():
            _copy(source, staging / relative)
        session_meta = {
            "schema": SCHEMA,
            "session_id": final.name,
            "generated_at": payload.get("generated_at"),
            "analyzer_sync_id": payload.get("analyzer_sync_id"),
            "data_scope": payload.get("data_scope"),
            "performance": payload.get("performance") or {},
        }
        (staging / "session_meta.json").write_text(json.dumps(session_meta, indent=2), encoding="utf-8")
        evidence = []
        for name in EVIDENCE_NAMES:
            source = evidence_root / name
            if source.is_file():
                preserved = _preserve_evidence(source, staging, object_root)
                evidence.append({"name": name, "available": True, "sha256": _sha256(preserved), "size_bytes": preserved.stat().st_size})
            else:
                evidence.append({"name": name, "available": False})
        files = []
        for path in sorted(p for p in staging.rglob("*") if p.is_file()):
            files.append({"path": path.relative_to(staging).as_posix(), "size_bytes": path.stat().st_size, "sha256": _sha256(path)})
        manifest = {
            "schema": SCHEMA,
            "complete": True,
            "session_id": final.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "analyzer_run_id": report_manifest.get("analyzer_sync_id"),
            "analyzer_revision": analyzer_revision.lower(),
            "source_data_revision": source_data_revision.lower(),
            "cohort_schema": cohort_schema,
            "fresh_epoch": {
                "schema": fresh_epoch.get("schema"),
                "status": fresh_epoch.get("status") or "UNAVAILABLE",
                "epoch_id": fresh_epoch.get("epoch_id"),
                "cutoff_utc": fresh_epoch.get("cutoff_utc"),
                "kind": fresh_epoch.get("kind"),
            },
            "report_manifest_sha256": _sha256(staging / "report_manifest.json"),
            "files": files,
            "evidence": evidence,
        }
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(staging, final)

        # Preserve the dashboard's established index location while replacing
        # its contents atomically with completion-qualified v2 entries.
        index_path = root / "research_session_index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"schema": SCHEMA, "sessions": []}
        except (OSError, ValueError, TypeError):
            index = {"schema": SCHEMA, "sessions": []}
        sessions = [row for row in index.get("sessions", []) if isinstance(row, dict) and row.get("session_id") != final.name]
        performance = payload.get("performance") or {}
        sessions.insert(0, {
            "session_id": final.name,
            "created_at": manifest["created_at"],
            "generated_at": payload.get("generated_at"),
            "trades": performance.get("trades"),
            "net_pnl_usd": performance.get("net_pnl_usd"),
            "win_rate_pct": performance.get("win_rate_pct"),
            "path": str(final),
            "manifest_sha256": _sha256(final / MANIFEST),
        })
        index_temp = archive_root / f".index-{nonce}.tmp"
        index_temp.write_text(json.dumps({"schema": SCHEMA, "sessions": sessions[:50]}, indent=2), encoding="utf-8")
        try:
            os.replace(index_temp, index_path)
        finally:
            index_temp.unlink(missing_ok=True)
        return final
    finally:
        shutil.rmtree(staging, ignore_errors=True)
