"""Verified, copy-only migration into the canonical desktop research store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
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
import storage_links  # noqa: E402


INCREMENTAL_INDEX = "migration/.incremental-index.json"
INCREMENTAL_SCHEMA = "canonical_migration_incremental_index_v1"
DEFAULT_VERIFY_INTERVAL_SEC = 24 * 3600
# Post-deploy receipt re-binding recopies ~42k small files; per-file
# create/replace latency, not bandwidth, bounds that.
DEFAULT_COPY_WORKERS = 8
_CHUNK = 4 * 1024 * 1024


def _json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
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


def _load_incremental_index(destination: Path) -> dict | None:
    try:
        index = json.loads((destination / INCREMENTAL_INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict) or index.get("schema") != INCREMENTAL_SCHEMA or not isinstance(index.get("files"), dict):
        return None
    return index


def _append_verified(src: Path, dst: Path, prefix_size: int, prefix_sha: str, expected_sha: str) -> bool:
    """Append src[prefix_size:] to dst when src extends the recorded prefix to exactly expected_sha.

    dst is only written after the whole source has been hashed and matched, and
    the written tail is read back and compared.
    """
    digest = hashlib.sha256()
    with src.open("rb") as handle:
        remaining = prefix_size
        while remaining:
            chunk = handle.read(min(_CHUNK, remaining))
            if not chunk:
                return False
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != prefix_sha:
            return False
        tail = hashlib.sha256()
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
            tail.update(chunk)
    if not expected_sha or digest.hexdigest() != expected_sha:
        return False
    storage_links.ensure_private(dst)
    with src.open("rb") as handle, dst.open("r+b") as out:
        handle.seek(prefix_size)
        out.seek(prefix_size)
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            out.write(chunk)
        out.truncate()
        out.flush()
        os.fsync(out.fileno())
    written = hashlib.sha256()
    with dst.open("rb") as handle:
        handle.seek(prefix_size)
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            written.update(chunk)
    if written.hexdigest() != tail.hexdigest():
        raise RuntimeError(f"Appended tail mismatch: {dst}")
    shutil.copystat(src, dst)
    return True


def _copy_verified(relative: str, src: Path, dst: Path, expected_size: int, expected_sha: str,
                   src_attested: bool, link: bool = False) -> None:
    if expected_sha and not src_attested and _sha256(src) != expected_sha:
        raise RuntimeError(f"Source checksum drift: {relative}")
    if link and expected_sha:
        outcome = storage_links.link_or_copy(src, dst, expected_sha256=expected_sha)
        if dst.stat().st_size != expected_size:
            raise RuntimeError(f"Linked size mismatch: {relative}")
        if outcome == "COPIED_FALLBACK" and _sha256(dst) != expected_sha:
            raise RuntimeError(f"Copied checksum mismatch: {relative}")
        return
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


def migrate(source: Path, destination: Path, heartbeat_path: Path, *, full: bool = False,
            verify_interval_sec: float = DEFAULT_VERIFY_INTERVAL_SEC, now: float | None = None,
            copy_workers: int = DEFAULT_COPY_WORKERS,
            link_settle_sec: float | None = storage_links.DEFAULT_SETTLE_SEC) -> dict:
    source = source.resolve()
    destination = initialize_store(destination, REPO_ROOT)
    heartbeat, revision = _validated_heartbeat(heartbeat_path)
    deployed_revision = _deployed_revision(heartbeat)
    now = time.time() if now is None else now

    state_path = source / ".fly-sync-state.json"
    state = _json(state_path)
    # Destination files are reused only when this tool recorded them with the
    # same checksum and they are byte-for-byte where it left them (size+mtime);
    # a periodic verify pass re-hashes every reused file.
    index = None if full else _load_incremental_index(destination)
    previous = index["files"] if index else {}
    last_verified = float(index.get("last_verified_at") or 0) if index else now
    verify = index is not None and now - last_verified >= verify_interval_sec
    copied = 0
    copied_bytes = 0
    counts = {"reused": 0, "appended": 0, "copied": 0, "linked": 0}
    written_bytes = 0
    files: dict[str, dict] = {}
    normalized_state: dict[str, dict] = {}
    entries: list[tuple[str, str, Path, int, str, dict]] = []
    pending: list[tuple[str, Path, Path, int, str, bool]] = []
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
        src_stat = src.stat()
        if src_stat.st_size != expected_size:
            raise RuntimeError(f"Source size drift: {relative}")
        expected_sha = str(record.get("sha256") or "").lower()
        # The promotion view records each file's mtime as it hashed it; an
        # unchanged mtime means that checksum still describes the source.
        src_attested = bool(expected_sha) and record.get("mtime_ns") is not None \
            and int(record["mtime_ns"]) == src_stat.st_mtime_ns
        dst = (destination / relative).resolve()
        try:
            dst.relative_to(destination)
        except ValueError as exc:
            raise RuntimeError(f"Destination path escaped store: {relative}") from exc
        key = str(relative).replace("\\", "/")
        prior = previous.get(key)
        dst_stat = dst.stat() if prior and dst.is_file() else None
        dst_intact = bool(dst_stat and dst_stat.st_size == prior["size"] and dst_stat.st_mtime_ns == prior["dst_mtime_ns"])
        done = False
        # Settled view files are hardlinked (one physical copy shared with the
        # view and the shadow tree); hot streams keep a private snapshot.
        link = (link_settle_sec is not None and link_settle_sec >= 0 and storage_links.links_enabled()
                and src_attested and storage_links.linkable(key)
                and storage_links.settled(src_stat, now, link_settle_sec))
        if link:
            if storage_links.same_file(src, dst) and (not verify or _sha256(dst) == expected_sha):
                counts["reused"] += 1
            else:
                pending.append((relative, src, dst, expected_size, expected_sha, src_attested, True))
                counts["linked"] += 1
            entries.append((relative, key, dst, expected_size, expected_sha, record))
            continue
        if dst_intact and src_attested and prior["sha256"] == expected_sha:
            done = not verify or _sha256(dst) == expected_sha
            if done:
                counts["reused"] += 1
        if not done and dst_intact and src_attested and expected_size > prior["size"]:
            done = _append_verified(src, dst, prior["size"], prior["sha256"], expected_sha)
            if done:
                counts["appended"] += 1
                written_bytes += expected_size - prior["size"]
        if not done:
            pending.append((relative, src, dst, expected_size, expected_sha, src_attested, False))
            counts["copied"] += 1
            written_bytes += expected_size
        entries.append((relative, key, dst, expected_size, expected_sha, record))
    with ThreadPoolExecutor(max_workers=max(1, int(copy_workers))) as pool:
        for future in [pool.submit(_copy_verified, *job) for job in pending]:
            future.result()
    for relative, key, dst, expected_size, expected_sha, record in entries:
        if dst.stat().st_size != expected_size:
            raise RuntimeError(f"Destination size mismatch: {relative}")
        if expected_sha:
            files[key] = {"size": expected_size, "sha256": expected_sha, "dst_mtime_ns": dst.stat().st_mtime_ns}
        copied += 1
        copied_bytes += expected_size
        normalized_state[key] = dict(record)
    (destination / INCREMENTAL_INDEX).parent.mkdir(parents=True, exist_ok=True)
    candidate_index = destination / (INCREMENTAL_INDEX + ".tmp")
    candidate_index.write_text(json.dumps({"schema": INCREMENTAL_SCHEMA, "files": files,
                                           "last_verified_at": now if verify else last_verified},
                                          sort_keys=True), encoding="utf-8")
    os.replace(candidate_index, destination / INCREMENTAL_INDEX)

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
        "mode": "FULL" if index is None else ("INCREMENTAL_VERIFIED" if verify else "INCREMENTAL"),
        "files_reused": counts["reused"],
        "files_appended": counts["appended"],
        "files_copied": counts["copied"],
        "files_linked": counts["linked"],
        "bytes_written": written_bytes,
        "promotion_level": str(heartbeat.get("promotionLevel") or "GREEN"),
        "promotion_warnings": [str(item) for item in heartbeat.get("promotionWarnings") or []],
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
    parser.add_argument("--full", action="store_true", help="ignore the incremental index and recopy every file")
    parser.add_argument("--verify-interval-sec", type=float, default=DEFAULT_VERIFY_INTERVAL_SEC)
    parser.add_argument("--copy-workers", type=int, default=DEFAULT_COPY_WORKERS)
    parser.add_argument("--link-settle-sec", type=float, default=storage_links.DEFAULT_SETTLE_SEC,
                        help="hardlink view files untouched this long (negative disables)")
    args = parser.parse_args()
    if args.record_existing:
        if args.source:
            parser.error("--source cannot be combined with --record-existing")
        receipt = record_existing_store(Path(args.destination), Path(args.heartbeat))
    else:
        if not args.source:
            parser.error("--source is required unless --record-existing is used")
        receipt = migrate(Path(args.source), Path(args.destination), Path(args.heartbeat),
                          full=args.full, verify_interval_sec=args.verify_interval_sec,
                          copy_workers=args.copy_workers, link_settle_sec=args.link_settle_sec)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
