"""Verified, copy-only migration into the canonical desktop research store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "services" / "btc-conservative-agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from research.canonical_data_store import (  # noqa: E402
    append_manifest,
    default_store_root,
    initialize_store,
    publish_parity_status,
)


def _json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
    return count


def _validated_heartbeat(path: Path) -> tuple[dict, str]:
    heartbeat = _json(path)
    if heartbeat.get("ok") is not True or heartbeat.get("inProgress") is True:
        raise RuntimeError("Canonical store refused: mirror sync is incomplete")
    if str(heartbeat.get("revisionParity") or "").upper() != "MATCH":
        raise RuntimeError("Canonical store refused: mirror revision parity is not MATCH")
    revision = str(heartbeat.get("sourceRevision") or "").strip().lower()
    mirrored = str(heartbeat.get("mirroredSourceRevision") or "").strip().lower()
    if not revision or revision != mirrored:
        raise RuntimeError("Canonical store refused: source/mirror revision mismatch")
    return heartbeat, revision


def _deployed_revision(heartbeat: dict) -> str:
    """Return only explicitly observed deployment identity.

    Older heartbeat receipts did not carry this field.  They remain usable for
    historical indexing, but UNKNOWN is retained rather than copying the
    source revision and manufacturing deployment provenance.
    """
    revision = str(heartbeat.get("deployedRevision") or "").strip().lower()
    return revision or "UNKNOWN"


def record_existing_store(destination: Path, heartbeat_path: Path) -> dict:
    """Append the identity of one already-synchronized canonical generation."""
    destination = initialize_store(destination, REPO_ROOT)
    heartbeat, revision = _validated_heartbeat(heartbeat_path)
    deployed_revision = _deployed_revision(heartbeat)
    state_path = destination / ".fly-sync-state.json"
    state = _json(state_path)
    normalized_state: dict[str, dict] = {}
    byte_count = 0
    for relative, record in sorted(state.items()):
        if not isinstance(record, dict):
            raise RuntimeError(f"Invalid sync-state row: {relative}")
        source = (destination / relative).resolve()
        try:
            source.relative_to(destination)
        except ValueError as exc:
            raise RuntimeError(f"Canonical path escaped store: {relative}") from exc
        if not source.is_file():
            raise RuntimeError(f"Canonical file missing: {relative}")
        expected_size = int(record.get("size", -1))
        if source.stat().st_size != expected_size:
            raise RuntimeError(f"Canonical size drift: {relative}")
        expected_sha = str(record.get("sha256") or "").lower()
        if expected_sha and _sha256(source) != expected_sha:
            raise RuntimeError(f"Canonical checksum drift: {relative}")
        byte_count += expected_size
        normalized_state[str(relative).replace("\\", "/")] = dict(record)

    session = _json(destination / "research_session.json")
    epoch = str(
        session.get("collector_v22_epoch_id")
        or session.get("epoch_id")
        or session.get("collection_epoch")
        or ""
    ).strip()
    if not epoch:
        raise RuntimeError("Canonical store refused: epoch identity missing")
    checksum_payload = json.dumps(
        {"revision": revision, "epoch": epoch, "files": normalized_state},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    opportunity_count = _count_lines(destination / "v3" / "ledgers" / "opportunity.jsonl")
    row_count = sum(
        _count_lines(destination / "v3" / "ledgers" / f"{name}.jsonl")
        for name in ("opportunity", "decision", "order_intent", "execution", "lifecycle")
    )
    manifest = append_manifest(
        destination,
        {
            "dataset_epoch": epoch,
            "source_revision": revision,
            "deployed_revision": deployed_revision,
            "tile_config_signature": str(heartbeat.get("tileRegistrySignature") or ""),
            "collection_started_at": session.get("fresh_collection_started_at")
            or session.get("started_at")
            or session.get("session_start")
            or heartbeat.get("syncedAt"),
            "collection_observed_at": heartbeat.get("syncedAt"),
            "row_count": row_count,
            "opportunity_count": opportunity_count,
            "dataset_checksum": hashlib.sha256(checksum_payload).hexdigest(),
            "analyzer_status": "PENDING_CANONICAL_ANALYZER_RUN",
            "analyzer_completed_at": None,
            "analyzer_schema_version": "v62",
            "sync_direction": "FLY_TO_LOCAL_ONLY",
            "file_count": len(normalized_state),
            "byte_count": byte_count,
        },
    )
    parity = publish_parity_status(
        destination,
        {
            "dataset_epoch": epoch,
            "source_revision": revision,
            "deployed_revision": deployed_revision,
            "tile_config_signature": str(heartbeat.get("tileRegistrySignature") or ""),
        },
    )
    if not parity["ok"]:
        raise RuntimeError("Canonical store refused: committed manifest parity mismatch")
    return {
        "schema": "canonical_research_existing_store_receipt_v1",
        "source_authority": "FLY_PERSISTENT_VOLUME:/app/data",
        "destination": str(destination),
        "files_verified": len(normalized_state),
        "bytes_verified": byte_count,
        "manifest_entry_hash": manifest["entry_hash"],
        "source_deleted": False,
    }


def migrate(source: Path, destination: Path, heartbeat_path: Path) -> dict:
    source = source.resolve()
    destination = initialize_store(destination, REPO_ROOT)
    heartbeat, revision = _validated_heartbeat(heartbeat_path)
    deployed_revision = _deployed_revision(heartbeat)

    state_path = source / ".fly-sync-state.json"
    state = _json(state_path)
    copied = 0
    copied_bytes = 0
    normalized_state: dict[str, dict] = {}
    for relative, record in sorted(state.items()):
        if not isinstance(record, dict):
            raise RuntimeError(f"Invalid sync-state row: {relative}")
        src = (source / relative).resolve()
        try:
            src.relative_to(source)
        except ValueError as exc:
            raise RuntimeError(f"Source path escaped mirror: {relative}") from exc
        if not src.is_file():
            raise RuntimeError(f"Source file missing: {relative}")
        expected_size = int(record.get("size", -1))
        if src.stat().st_size != expected_size:
            raise RuntimeError(f"Source size drift: {relative}")
        expected_sha = str(record.get("sha256") or "").lower()
        if expected_sha and _sha256(src) != expected_sha:
            raise RuntimeError(f"Source checksum drift: {relative}")
        dst = (destination / relative).resolve()
        try:
            dst.relative_to(destination)
        except ValueError as exc:
            raise RuntimeError(f"Destination path escaped store: {relative}") from exc
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, candidate_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".migration", dir=dst.parent)
        os.close(fd)
        candidate = Path(candidate_name)
        try:
            shutil.copy2(src, candidate)
            if candidate.stat().st_size != expected_size:
                raise RuntimeError(f"Copied size mismatch: {relative}")
            actual_sha = _sha256(candidate)
            if expected_sha and actual_sha != expected_sha:
                raise RuntimeError(f"Copied checksum mismatch: {relative}")
            os.replace(candidate, dst)
        finally:
            candidate.unlink(missing_ok=True)
        copied += 1
        copied_bytes += expected_size
        normalized_state[str(relative).replace("\\", "/")] = dict(record)

    for name in (".fly-sync-state.json", ".fly-sync-growth-state.json"):
        src = source / name
        if src.is_file():
            shutil.copy2(src, destination / name)
    shutil.copy2(heartbeat_path, destination / ".fly-data-sync-loop.heartbeat.json")

    session = _json(destination / "research_session.json")
    epoch = str(
        session.get("collector_v22_epoch_id")
        or session.get("epoch_id")
        or session.get("collection_epoch")
        or ""
    ).strip()
    if not epoch:
        raise RuntimeError("Canonical migration refused: epoch identity missing")
    checksum_payload = json.dumps(
        {"revision": revision, "epoch": epoch, "files": normalized_state},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    opportunity_count = _count_lines(destination / "v3" / "ledgers" / "opportunity.jsonl")
    row_count = sum(
        _count_lines(destination / "v3" / "ledgers" / f"{name}.jsonl")
        for name in ("opportunity", "decision", "order_intent", "execution", "lifecycle")
    )
    started = (
        session.get("fresh_collection_started_at")
        or session.get("started_at")
        or session.get("session_start")
        or heartbeat.get("syncedAt")
    )
    manifest = append_manifest(
        destination,
        {
            "dataset_epoch": epoch,
            "source_revision": revision,
            "deployed_revision": deployed_revision,
            "tile_config_signature": str(heartbeat.get("tileRegistrySignature") or ""),
            "collection_started_at": started,
            "collection_observed_at": heartbeat.get("syncedAt"),
            "row_count": row_count,
            "opportunity_count": opportunity_count,
            "dataset_checksum": hashlib.sha256(checksum_payload).hexdigest(),
            "analyzer_status": "PENDING_CANONICAL_ANALYZER_RUN",
            "analyzer_completed_at": None,
            "analyzer_schema_version": "v62",
            "sync_direction": "FLY_TO_LOCAL_ONLY",
            "file_count": copied,
            "byte_count": copied_bytes,
        },
    )
    parity = publish_parity_status(
        destination,
        {
            "dataset_epoch": epoch,
            "source_revision": revision,
            "deployed_revision": deployed_revision,
            "tile_config_signature": str(heartbeat.get("tileRegistrySignature") or ""),
        },
    )
    if not parity["ok"]:
        raise RuntimeError("Canonical migration refused: committed manifest parity mismatch")
    receipt = {
        "schema": "canonical_research_migration_v1",
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(source),
        "destination": str(destination),
        "source_deleted": False,
        "files_verified": copied,
        "bytes_verified": copied_bytes,
        "manifest_entry_hash": manifest["entry_hash"],
        "fly_parity_claim": "MATCH_AT_HEARTBEAT_TIMESTAMP",
    }
    (destination / "migration" / "migration_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--record-existing", action="store_true")
    parser.add_argument("--heartbeat", required=True)
    parser.add_argument("--destination", default=str(default_store_root(REPO_ROOT)))
    args = parser.parse_args()
    if args.record_existing:
        if args.source:
            parser.error("--source cannot be combined with --record-existing")
        receipt = record_existing_store(Path(args.destination), Path(args.heartbeat))
    else:
        if not args.source:
            parser.error("--source is required unless --record-existing is used")
        receipt = migrate(Path(args.source), Path(args.destination), Path(args.heartbeat))
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
