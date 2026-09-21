#!/usr/bin/env python3
"""Fast shallow post-wipe size probe. No full tree walk. No delete. No arm."""
from __future__ import annotations

import json
import os
from pathlib import Path

EXPECTED_EPOCH = str(os.environ.get("EXPECTED_EPOCH") or "").strip()
DATA = Path("/app/data")
RUNTIME = DATA / "runtime"

# Paths that inventory DOES scan (not in _DATA_SYNC_EXCLUDED_DIR_NAMES) and
# are typical post-wipe retainers / file-count bombs.
TARGETS = [
    "runtime",
    "runtime/v3",
    "runtime/v3/ledgers",
    "runtime/v3/lifecycle_bundle_index",
    "runtime/v3/lifecycle_bundle_index/recovery-quarantine",
    "runtime/v3/lifecycle_bundle_index/recovery-staging",
    "runtime/v3/receipts",
    "runtime/v3/receipts/emergency_record_idempotency_v1",
    "runtime/v3/generations",
    "runtime/research_reset_receipts",
    "runtime/emergency_evidence_wal_v2",
    "runtime/emergency_wal_release_acks",
    "runtime/epoch_quarantine",
    "runtime/research_epoch_quarantine",
    "runtime/.data-sync-snapshots",
    "runtime/.locks",
    "runtime/data_sync",
    ".data-sync-snapshots",
    "research_archive",
    "research_session_archives",
    "research_epoch_quarantine",
    "epoch_quarantine",
]


def shallow_bytes(path: Path, depth: int = 0, max_depth: int = 2) -> dict:
    out = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return out
    files = 0
    dirs = 0
    bytes_ = 0
    children = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        files += 1
                        bytes_ += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        dirs += 1
                        if depth < max_depth:
                            sub = shallow_bytes(Path(entry.path), depth + 1, max_depth)
                            children.append({
                                "name": entry.name,
                                "files": sub.get("files", 0),
                                "dirs": sub.get("dirs", 0),
                                "bytes": sub.get("bytes", 0),
                            })
                            files += int(sub.get("files") or 0)
                            dirs += int(sub.get("dirs") or 0)
                            bytes_ += int(sub.get("bytes") or 0)
                        else:
                            # one more level count only
                            sf = sd = sb = 0
                            try:
                                with os.scandir(entry.path) as it2:
                                    for e2 in it2:
                                        try:
                                            if e2.is_symlink():
                                                continue
                                            if e2.is_file(follow_symlinks=False):
                                                sf += 1
                                                sb += e2.stat(follow_symlinks=False).st_size
                                            elif e2.is_dir(follow_symlinks=False):
                                                sd += 1
                                        except OSError:
                                            pass
                            except OSError:
                                pass
                            children.append({"name": entry.name, "files": sf, "dirs": sd, "bytes": sb, "depth_capped": True})
                            files += sf
                            dirs += sd
                            bytes_ += sb
                except OSError:
                    continue
    except OSError as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
        return out
    children.sort(key=lambda r: int(r.get("bytes") or 0), reverse=True)
    out.update({"files": files, "dirs": dirs, "bytes": bytes_, "top": children[:25]})
    return out


def main() -> int:
    report = {
        "schema": "fly_postwipe_shallow_size_v1",
        "expected_epoch": EXPECTED_EPOCH or None,
        "ok": True,
        "disk": {},
        "targets": {},
    }
    try:
        usage = os.statvfs("/app/data")
        report["disk"] = {
            "total_bytes": usage.f_frsize * usage.f_blocks,
            "free_bytes": usage.f_frsize * usage.f_bavail,
            "used_bytes": usage.f_frsize * (usage.f_blocks - usage.f_bavail),
        }
    except Exception as exc:
        report["disk"] = {"error": str(exc)}

    for rel in TARGETS:
        report["targets"][rel] = shallow_bytes(DATA / rel, 0, 1)

    # List recovery-quarantine children names+sizes (file-count bomb suspect)
    rq = RUNTIME / "v3/lifecycle_bundle_index/recovery-quarantine"
    if rq.is_dir():
        kids = []
        with os.scandir(rq) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        st = shallow_bytes(Path(entry.path), 0, 0)
                        kids.append({"name": entry.name, "files": st.get("files"), "bytes": st.get("bytes")})
                    elif entry.is_file(follow_symlinks=False):
                        kids.append({"name": entry.name, "files": 1, "bytes": entry.stat().st_size})
                except OSError:
                    pass
        kids.sort(key=lambda r: int(r.get("bytes") or 0), reverse=True)
        report["recovery_quarantine_children"] = kids[:40]
        report["recovery_quarantine_child_count"] = len(kids)

    # emergency idempotency subdirs
    em = RUNTIME / "v3/receipts/emergency_record_idempotency_v1"
    if em.is_dir():
        report["emergency_idempotency"] = shallow_bytes(em, 0, 1)

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
