#!/usr/bin/env python3
"""Fail-closed one-shot: quarantine incomplete lifecycle.jsonl tail, ftruncate in place.

Preserves inode (dev/ino) so ledger_cursor identity stays valid — only size/mtime
change. Cursor offset already equals published_size for this incident.

Env (all required unless noted):
  EXPECTED_PHYSICAL_SIZE
  EXPECTED_PUBLISHED_SIZE
  EXPECTED_SOURCE_SHA256
  EXPECTED_PREFIX_SHA256
  EXPECTED_TAIL_SIZE
  EXPECTED_TAIL_SHA256
  EXPECTED_INODE
  EXPECTED_MTIME_NS
  DATA_ROOT (default /app/data)
  APPLY=true to mutate; otherwise dry-run inspect only
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

SCHEMA = "lifecycle_incomplete_tail_repair_once_v1"
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
APPLY = os.environ.get("APPLY", "false").strip().lower() in {"1", "true", "yes"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _env_int(name: str) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise SystemExit(f"missing/invalid positive int env {name}")
    return int(raw)


def _env_sha(name: str) -> str:
    raw = str(os.environ.get(name) or "").strip().lower()
    if len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
        raise SystemExit(f"missing/invalid sha256 env {name}")
    return raw


def _validate_jsonl(raw: bytes) -> int:
    if raw and not raw.endswith(b"\n"):
        raise ValueError("PREFIX_NOT_LF_TERMINATED")
    count = 0
    for count, line in enumerate(raw.splitlines(keepends=True), 1):
        if len(line) > 2 * 1024 * 1024:
            raise ValueError(f"PREFIX_RECORD_TOO_LARGE:{count}")
        value = json.loads(line.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"PREFIX_NON_OBJECT:{count}")
    return count


def main() -> int:
    expected_physical = _env_int("EXPECTED_PHYSICAL_SIZE")
    expected_published = _env_int("EXPECTED_PUBLISHED_SIZE")
    expected_source_sha = _env_sha("EXPECTED_SOURCE_SHA256")
    expected_prefix_sha = _env_sha("EXPECTED_PREFIX_SHA256")
    expected_tail_size = _env_int("EXPECTED_TAIL_SIZE")
    expected_tail_sha = _env_sha("EXPECTED_TAIL_SHA256")
    expected_inode = _env_int("EXPECTED_INODE")
    expected_mtime_ns = _env_int("EXPECTED_MTIME_NS")

    if expected_physical != expected_published + expected_tail_size:
        raise SystemExit("PHYSICAL != PUBLISHED + TAIL")

    target = (DATA_ROOT / "runtime" / "v3" / "ledgers" / "lifecycle.jsonl").resolve()
    if not target.is_file() or target.is_symlink():
        raise SystemExit("TARGET_MISSING_OR_SYMLINK")
    expected_parent = (DATA_ROOT / "runtime" / "v3" / "ledgers").resolve()
    if target.parent != expected_parent:
        raise SystemExit("TARGET_OUTSIDE_LEDGER_ROOT")

    st = target.stat()
    data = target.read_bytes()
    newline = b"\n"
    last_nl = data.rfind(newline)
    published = last_nl + 1 if last_nl >= 0 else 0
    prefix = data[:published]
    tail = data[published:]

    probe = {
        "schema": SCHEMA,
        "apply": APPLY,
        "path": str(target),
        "physical_size": len(data),
        "published_size": published,
        "tail_size": len(tail),
        "source_sha256": _sha(data),
        "prefix_sha256": _sha(prefix),
        "tail_sha256": _sha(tail),
        "ends_with_newline": data.endswith(newline),
        "inode": int(st.st_ino),
        "mtime_ns": int(st.st_mtime_ns),
        "dev": int(st.st_dev),
    }
    print(json.dumps({"probe": probe}, sort_keys=True))

    # mtime may drift if readers touch atime/mtime accounting; cursor identity
    # is (dev, ino, offset, anchor) — require exact size/sha/inode/tail instead.
    if (
        len(data) != expected_physical
        or published != expected_published
        or len(tail) != expected_tail_size
        or _sha(data) != expected_source_sha
        or _sha(prefix) != expected_prefix_sha
        or _sha(tail) != expected_tail_sha
        or int(st.st_ino) != expected_inode
        or data.endswith(newline)
        or published <= 0
    ):
        raise SystemExit("EXPECTATION_MISMATCH:" + json.dumps(probe, sort_keys=True))
    if int(st.st_mtime_ns) != expected_mtime_ns:
        print(json.dumps({
            "mtime_drift": True,
            "expected_mtime_ns": expected_mtime_ns,
            "active_mtime_ns": int(st.st_mtime_ns),
        }, sort_keys=True))

    line_count = _validate_jsonl(prefix)

    if not APPLY:
        print(json.dumps({
            "ok": True, "dry_run": True, "would_repair": True,
            "valid_jsonl_lines": line_count,
            "exclude_tail_bytes": expected_tail_size,
        }, sort_keys=True))
        return 0

    repair_id = f"lifecycle-tail-once-{expected_source_sha[:16]}"
    quarantine_root = target.parent / "corrupt_evidence_quarantine"
    quarantine = quarantine_root / repair_id
    if quarantine.exists():
        raise SystemExit("QUARANTINE_ALREADY_EXISTS")

    quarantine_root.mkdir(parents=True, exist_ok=True)
    staging = quarantine_root / f".{repair_id}.{uuid.uuid4().hex[:8]}.tmp"
    staging.mkdir()
    try:
        (staging / "lifecycle.jsonl.original").write_bytes(data)
        (staging / "lifecycle.jsonl.incomplete-tail").write_bytes(tail)
        exclusion = {
            "schema": SCHEMA,
            "classification": "UNKNOWN",
            "ranking_eligible": False,
            "profitability_supported": False,
            "reason": "INCOMPLETE_JSONL_TAIL_EXCLUDED",
            "tail_size": expected_tail_size,
            "tail_sha256": expected_tail_sha,
            "source_sha256": expected_source_sha,
        }
        (staging / "excluded_unknown.json").write_text(
            json.dumps(exclusion, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest = {
            "schema": SCHEMA,
            "repair_id": repair_id,
            "target": "v3/ledgers/lifecycle.jsonl",
            "method": "ftruncate_preserve_inode",
            "source": {"size": expected_physical, "sha256": expected_source_sha},
            "source_stat": {"inode": expected_inode, "mtime_ns": expected_mtime_ns, "dev": int(st.st_dev)},
            "complete_prefix": {"size": expected_published, "sha256": expected_prefix_sha},
            "excluded_tail": {"size": expected_tail_size, "sha256": expected_tail_sha},
            "valid_jsonl_lines": line_count,
            "stamp_unix": time.time(),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, quarantine)
    finally:
        if staging.exists():
            import shutil
            shutil.rmtree(staging, ignore_errors=True)

    # In-place truncate preserves inode so cursor (dev,ino,offset,anchor) stays valid.
    with target.open("r+b") as handle:
        handle.truncate(expected_published)
        handle.flush()
        os.fsync(handle.fileno())

    after = target.read_bytes()
    after_st = target.stat()
    if (
        len(after) != expected_published
        or _sha(after) != expected_prefix_sha
        or not after.endswith(b"\n")
        or int(after_st.st_ino) != expected_inode
        or int(after_st.st_dev) != int(st.st_dev)
    ):
        raise SystemExit("POST_TRUNCATE_VALIDATION_FAILED")
    _validate_jsonl(after)

    receipt = {
        "schema": SCHEMA,
        "status": "REPAIRED",
        "repair_id": repair_id,
        "ok": True,
        "inode_preserved": True,
        "source_sha256": expected_source_sha,
        "prefix_sha256": expected_prefix_sha,
        "tail_sha256": expected_tail_sha,
        "excluded_classification": "UNKNOWN",
        "ranking_eligible": False,
        "source_cleanup_authorized": False,
        "valid_jsonl_lines": line_count,
        "active_size": len(after),
        "active_inode": int(after_st.st_ino),
        "active_mtime_ns": int(after_st.st_mtime_ns),
    }
    (quarantine / "repair_receipt.json").write_text(
        json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
