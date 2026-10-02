"""Stage a complete segment shadow mirror for canonical promotion.

The analyzer reads only a promoted canonical store
(``canonical_dataset_current.json``), produced by
``scripts/migrate_canonical_research_store.py`` from a mirror that carries a
verified ``.fly-sync-state.json`` and a sync heartbeat. The segment puller's
shadow tree has neither, so this tool copies the tree into a separate view and
emits both, fail-closed: it refuses unless the shadow covers everything Fly has
published at the deployed revision.

    python research_segment_promotion.py --view C:\\DoxxedCrypto\\segment-promotion-view
    python ..\\..\\scripts\\migrate_canonical_research_store.py --source <view> \\
        --heartbeat <view>\\.segment-promotion.heartbeat.json --destination <checkout>\\...\\canonical-research-data

The live shadow tree is never written; the puller's run lock is held while
copying so no segment is applied mid-copy.

The view persists between cycles and is updated incrementally: unchanged files
(same source and view size/mtime as the index recorded) are reused, append-only
growth is verified against the recorded prefix hash and only the tail is
written, a same-size source whose bytes still hash to the recorded sha256
(mtime-only rewrite) is reused without touching the view, and anything else is
recopied. Full-file copies run on a bounded thread pool. A periodic verify pass
re-hashes every reused file; a missing or unreadable index forces a full
rebuild.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import research_segment_format as fmt
from research_segment_puller import STATE_SCHEMA, PullerError, _RunLock, refuse_unsafe_root
from research_segment_store import HttpSegmentSource, StoreError

HEARTBEAT_NAME = ".segment-promotion.heartbeat.json"
SYNC_STATE_NAME = ".fly-sync-state.json"
INDEX_NAME = ".segment-promotion.index.json"
INDEX_SCHEMA = "research_segment_promotion_index_v1"
VIEW_CONTROL_NAMES = frozenset({HEARTBEAT_NAME, SYNC_STATE_NAME, INDEX_NAME})
RECEIPT_SCHEMA = "research_segment_promotion_view_v1"
DEFAULT_VERIFY_INTERVAL_SEC = 24 * 3600
_CHUNK = 4 * 1024 * 1024
DEFAULT_MAX_UNSHIPPED_BYTES = 32 * 1024 * 1024
# Every Fly boot re-binds ~42k small idempotency receipts to the new revision;
# per-file create/replace latency, not bandwidth, bounds copying them.
DEFAULT_COPY_WORKERS = 8
# Per-append integrity caches keyed to the Fly inode/mtime of their source;
# meaningless off-host and never read by the analyzer.
FLY_LOCAL_ONLY_SUFFIXES = (".jsonl.validation.json",)


class PromotionRefused(RuntimeError):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_hashed(source: Path, target: Path) -> str:
    """Copy source to target in one pass, returning the sha256 of the bytes written."""
    digest = hashlib.sha256()
    with source.open("rb") as src, target.open("wb") as dst:
        for chunk in iter(lambda: src.read(_CHUNK), b""):
            digest.update(chunk)
            dst.write(chunk)
    shutil.copystat(source, target)
    return digest.hexdigest()


def _append_tail(source: Path, target: Path, prefix_size: int, prefix_sha: str) -> str | None:
    """Append source[prefix_size:] to target if source starts with the recorded prefix.

    Returns the full-file sha256, or None when the source prefix no longer
    matches (the caller then recopies the whole file).
    """
    digest = hashlib.sha256()
    with source.open("rb") as src:
        remaining = prefix_size
        while remaining:
            chunk = src.read(min(_CHUNK, remaining))
            if not chunk:
                return None
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != prefix_sha:
            return None
        with target.open("r+b") as dst:
            dst.seek(prefix_size)
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                digest.update(chunk)
                dst.write(chunk)
            dst.truncate()
    shutil.copystat(source, target)
    return digest.hexdigest()


def _refresh_file(source: Path, target: Path, recorded_sha: str | None) -> tuple[str, bool]:
    """Return (sha256, copied). A source that still hashes to recorded_sha is not copied."""
    if recorded_sha is not None:
        sha = _sha256_file(source)
        if sha == recorded_sha:
            return sha, False
    target.parent.mkdir(parents=True, exist_ok=True)
    return _copy_hashed(source, target), True


def _load_index(view_root: Path) -> dict | None:
    try:
        index = json.loads((view_root / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA or not isinstance(index.get("files"), dict):
        return None
    return index


def _write_json_atomic(path: Path, payload: dict, indent: int | None = None) -> None:
    candidate = path.with_name(path.name + ".tmp")
    candidate.write_text(json.dumps(payload, sort_keys=True, indent=indent), encoding="utf-8")
    os.replace(candidate, path)


def _clear_view(view_root: Path) -> None:
    for child in view_root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def deny_reasons(state: dict, head: dict, health: dict, tree: Path,
                 max_unshipped_bytes: int = DEFAULT_MAX_UNSHIPPED_BYTES) -> list[str]:
    reasons = []
    if state.get("schema") != STATE_SCHEMA:
        reasons.append("PULLER_STATE_MISSING")
        return reasons
    applied = int(state.get("applied_seq") or 0)
    published = int(head.get("published_seq") or 0)
    if applied < 1 or applied != published:
        reasons.append(f"SHADOW_BEHIND_PUBLISHED:{applied}<{published}")
    if head.get("last_manifest_sha256") != state.get("last_manifest_sha256"):
        reasons.append("HEAD_MANIFEST_MISMATCH")
    # A live epoch always has an in-flight tail; only a real backlog refuses.
    unshipped = int(head.get("unshipped_bytes") or 0)
    if unshipped > max_unshipped_bytes:
        reasons.append(f"FLY_UNSHIPPED_BYTES:{unshipped}>{max_unshipped_bytes}")
    if head.get("oversized_paths"):
        reasons.append(f"FLY_OVERSIZED_PATHS:{len(head['oversized_paths'])}")
    # A racing hot snapshot is only acceptable if an earlier version shipped.
    for row in head.get("racing_paths") or []:
        path = str(row.get("path") if isinstance(row, dict) else row)
        if path.endswith(FLY_LOCAL_ONLY_SUFFIXES):
            continue
        if not (tree / path).is_file():
            reasons.append(f"FLY_RACING_PATH_NEVER_SHIPPED:{path}")
    error = str(head.get("shipper_last_error") or "")
    if error and not error.startswith("PLAN_RACE"):
        reasons.append(f"FLY_SHIPPER_ERROR:{error}")
    shipped_rev = str(state.get("last_source_git_rev") or "").lower()
    deployed_rev = str(health.get("source_git_rev") or "").lower()
    if not shipped_rev or not deployed_rev or not shipped_rev.startswith(deployed_rev):
        reasons.append(f"REVISION_MISMATCH:{shipped_rev[:12] or '-'}!={deployed_rev or '-'}")
    if not str(health.get("tile_registry_signature") or "").strip():
        reasons.append("TILE_SIGNATURE_MISSING")
    if not (tree / "research_session.json").is_file():
        reasons.append("RESEARCH_SESSION_MISSING")
    return reasons


def genesis_window_end(manifest_raw: bytes | None) -> float | None:
    try:
        manifest = json.loads(manifest_raw or b"")
        value = float(manifest["window_end"])
    except (ValueError, KeyError, TypeError):
        return None
    return value if int(manifest.get("seq") or 0) == 1 and value > 0 else None


def stage_view(*, shadow_root: Path, view_root: Path, head: dict, health: dict,
               max_unshipped_bytes: int = DEFAULT_MAX_UNSHIPPED_BYTES,
               genesis_at: float | None = None, full: bool = False,
               verify_interval_sec: float = DEFAULT_VERIFY_INTERVAL_SEC,
               now: float | None = None, copy_workers: int = DEFAULT_COPY_WORKERS) -> dict:
    shadow_root = refuse_unsafe_root(shadow_root, "shadow root")
    view_root = refuse_unsafe_root(view_root, "promotion view")
    tree = shadow_root / "tree"
    if view_root.resolve() == tree.resolve() or tree.resolve() in view_root.resolve().parents:
        raise PromotionRefused(["VIEW_INSIDE_SHADOW_TREE"])
    # Only a view this tool staged (it carries the index) may be updated in place.
    index = _load_index(view_root) if view_root.is_dir() else None
    if index is None and view_root.exists() and any(view_root.iterdir()):
        raise PromotionRefused(["VIEW_NOT_EMPTY"])
    now = time.time() if now is None else now
    lock = _RunLock(shadow_root / ".puller" / "run.lock")
    try:
        state_path = shadow_root / ".puller" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        reasons = deny_reasons(state, head, health, tree, max_unshipped_bytes)
        if reasons:
            raise PromotionRefused(reasons)
        # The shadow equals the just-fetched Fly published head now; a quiet or
        # lock-blocked puller leaves last_applied_at old without any lag.
        head_verified_at = _utc_now()
        view_root.mkdir(parents=True, exist_ok=True)
        # The view is not consumable while it is being updated.
        for name in (SYNC_STATE_NAME, HEARTBEAT_NAME):
            (view_root / name).unlink(missing_ok=True)
        rebuild = full or index is None
        if rebuild:
            _clear_view(view_root)
            previous, last_verified = {}, now
        else:
            previous = index["files"]
            last_verified = float(index.get("last_verified_at") or 0)
        verify = not rebuild and now - last_verified >= verify_interval_sec
        _write_json_atomic(view_root / INDEX_NAME, {"schema": INDEX_SCHEMA, "complete": False, "files": previous,
                                                   "last_verified_at": last_verified})
        files, sync_state, byte_count = {}, {}, 0
        counts = {"reused": 0, "rehashed_unchanged": 0, "appended": 0, "copied": 0, "removed": 0}
        written_bytes = 0
        entries, refresh = [], {}
        for source in sorted(path for path in tree.rglob("*") if path.is_file()):
            relative = source.relative_to(tree).as_posix()
            target = view_root / relative
            src_stat = source.stat()
            prior = previous.get(relative)
            view_stat = target.stat() if prior and target.is_file() else None
            view_intact = bool(view_stat and view_stat.st_size == prior["size"]
                               and view_stat.st_mtime_ns == prior["mtime_ns"])
            sha = None
            verify_failed = False
            if (view_intact and src_stat.st_size == prior.get("src_size")
                    and src_stat.st_mtime_ns == prior.get("src_mtime_ns")):
                sha = prior["sha256"]
                if verify and (_sha256_file(target) != sha or _sha256_file(source) != sha):
                    sha, verify_failed = None, True
                else:
                    counts["reused"] += 1
            if sha is None and view_intact and src_stat.st_size > prior["size"]:
                sha = _append_tail(source, target, prior["size"], prior["sha256"])
                if sha is not None:
                    counts["appended"] += 1
                    written_bytes += src_stat.st_size - prior["size"]
            if sha is None:
                # The view still holds the recorded bytes (size+mtime intact), so
                # a same-size source with the recorded hash needs no copy.
                same_bytes_possible = (view_intact and not verify and not verify_failed
                                       and src_stat.st_size == prior["size"])
                refresh[relative] = (source, target, prior["sha256"] if same_bytes_possible else None)
            entries.append((relative, target, src_stat, sha))
        with ThreadPoolExecutor(max_workers=max(1, int(copy_workers))) as pool:
            futures = {rel: pool.submit(_refresh_file, *job) for rel, job in refresh.items()}
            results = {rel: future.result() for rel, future in futures.items()}
        for relative, target, src_stat, sha in entries:
            if sha is None:
                sha, copied = results[relative]
                if copied:
                    counts["copied"] += 1
                    written_bytes += src_stat.st_size
                else:
                    counts["rehashed_unchanged"] += 1
            stat = target.stat()
            if stat.st_size != src_stat.st_size:
                raise PromotionRefused([f"VIEW_SIZE_MISMATCH:{relative}"])
            record = {"size": stat.st_size, "sha256": sha, "inode": int(stat.st_ino), "mtime_ns": int(stat.st_mtime_ns)}
            sync_state[relative] = record
            files[relative] = {**record, "src_size": src_stat.st_size, "src_mtime_ns": src_stat.st_mtime_ns}
            byte_count += stat.st_size
        for stale in sorted(path for path in view_root.rglob("*") if path.is_file()):
            relative = stale.relative_to(view_root).as_posix()
            if relative not in files and relative not in VIEW_CONTROL_NAMES and not relative.endswith(".tmp"):
                stale.unlink()
                counts["removed"] += 1
        _write_json_atomic(view_root / INDEX_NAME, {"schema": INDEX_SCHEMA, "complete": True, "files": files,
                                                   "last_verified_at": now if verify else last_verified})
    finally:
        lock.release()
    revision = str(state["last_source_git_rev"]).lower()
    heartbeat = {
        "ok": True, "inProgress": False, "phase": "complete",
        "source": "research_segments_volume_sink", "syncedAt": head_verified_at,
        "segmentLastAppliedAt": state.get("last_applied_at"),
        "sourceRevision": revision, "observedSourceRevision": revision,
        "mirroredSourceRevision": revision,
        "deployedRevision": str(health["source_git_rev"]).lower(), "revisionParity": "MATCH",
        "tileRegistrySignature": health["tile_registry_signature"],
        "collectionEpochId": state.get("last_collection_epoch_id"),
        "segmentPrefix": head.get("prefix"),
        "segmentAppliedSeq": state["applied_seq"],
        "segmentGenesisAt": genesis_at,
        "segmentHeadManifestSha256": state["last_manifest_sha256"],
        "throttledSnapshots": head.get("throttled_snapshots") or [],
        "unshippedBytesAtPromotion": int(head.get("unshipped_bytes") or 0),
        "racingPaths": [str(row.get("path") if isinstance(row, dict) else row)
                        for row in head.get("racing_paths") or []],
        "fileCount": len(sync_state),
    }
    (view_root / SYNC_STATE_NAME).write_text(json.dumps(sync_state, sort_keys=True, indent=1), encoding="utf-8")
    (view_root / HEARTBEAT_NAME).write_text(json.dumps(heartbeat, sort_keys=True, indent=2), encoding="utf-8")
    return {"schema": RECEIPT_SCHEMA, "staged_at": _utc_now(), "view": str(view_root),
            "files": len(sync_state), "bytes": byte_count, "applied_seq": state["applied_seq"],
            "mode": "FULL_REBUILD" if rebuild else ("INCREMENTAL_VERIFIED" if verify else "INCREMENTAL"),
            "files_reused": counts["reused"], "files_rehashed_unchanged": counts["rehashed_unchanged"],
            "files_appended": counts["appended"],
            "files_copied": counts["copied"], "files_removed": counts["removed"], "bytes_written": written_bytes,
            "head_manifest_sha256": state["last_manifest_sha256"], "source_revision": revision}


def _health(base_url: str) -> dict:
    with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=30) as response:
        return json.loads(response.read())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-root", default=r"C:\DoxxedCrypto\fly-mirror-segments")
    parser.add_argument("--view", required=True)
    parser.add_argument("--base-url", default=os.getenv("RESEARCH_SEGMENTS_BASE_URL")
                        or "https://doxed-btc-bot.fly.dev")
    parser.add_argument("--prefix", default=os.getenv("RESEARCH_SEGMENTS_PREFIX") or "v2")
    parser.add_argument("--max-unshipped-bytes", type=int, default=DEFAULT_MAX_UNSHIPPED_BYTES)
    parser.add_argument("--full", action="store_true", help="rebuild the view from scratch")
    parser.add_argument("--verify-interval-sec", type=float, default=DEFAULT_VERIFY_INTERVAL_SEC)
    parser.add_argument("--copy-workers", type=int, default=DEFAULT_COPY_WORKERS)
    args = parser.parse_args(argv)
    try:
        source = HttpSegmentSource(base_url=args.base_url, prefix=args.prefix,
                                   admin_token=os.environ.get("BOT_ADMIN_TOKEN") or "")
        receipt = stage_view(shadow_root=Path(args.shadow_root), view_root=Path(args.view),
                             head=source.head(), health=_health(args.base_url),
                             max_unshipped_bytes=args.max_unshipped_bytes,
                             genesis_at=genesis_window_end(source.get(fmt.manifest_key(args.prefix, 1))),
                             full=args.full, verify_interval_sec=args.verify_interval_sec,
                             copy_workers=args.copy_workers)
    except PromotionRefused as exc:
        print(json.dumps({"ok": False, "deny_reasons": exc.reasons}, indent=2))
        return 3
    except (PullerError, StoreError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    print(json.dumps({"ok": True, **receipt}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
