"""Read-only probe of the transport-bundle maintenance inputs on the Fly volume.

Runs inside the machine via ``flyctl machine exec``. It never creates, writes,
renames, or deletes anything; it only lists bounded directories, reads the
small admission receipt, and calls the side-effect-free admission check.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

H64 = re.compile(r"[0-9a-f]{64}")
H32 = re.compile(r"[0-9a-f]{32}")
LIMIT = 20000


def _lease_root(root, *, sessions):
    if not root.is_dir():
        return {"exists": False}
    cutoff = time.time() - 7200
    entries = []
    with os.scandir(root) as it:
        for count, entry in enumerate(it):
            if count >= LIMIT:
                break
            entries.append(entry)
    report = {
        "exists": True,
        "entries": len(entries),
        "entries_truncated": len(entries) >= LIMIT,
        "bad_names": sorted(e.name for e in entries if not H64.fullmatch(e.name))[:10],
        "non_directories": sum(1 for e in entries if not e.is_dir(follow_symlinks=False)),
        "recent_2h": sum(1 for e in entries if e.stat(follow_symlinks=False).st_mtime >= cutoff),
    }
    if sessions:
        total, bad = 0, []
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            with os.scandir(entry.path) as it:
                for child in it:
                    total += 1
                    if not H32.fullmatch(child.name) or not child.is_dir(follow_symlinks=False):
                        bad.append(child.name[:40])
        report["sessions"] = total
        report["bad_sessions"] = bad[:10]
    return report


def main():
    volume = Path(os.getenv("BOT_DATA_DIR") or "/app/data").resolve()
    work = volume / ".data-sync-snapshots"
    out = {"schema": "fly_bundle_maintenance_probe_v1", "work_exists": work.is_dir(),
           "source_git_rev": (os.getenv("SOURCE_GIT_REV") or "")[:12],
           "bundles_enabled": os.getenv("DATA_SYNC_TRANSPORT_BUNDLES_ENABLED")}
    out["inventory_served"] = _lease_root(work / "inventory-served", sessions=False)
    out["inventory_acks"] = _lease_root(work / "inventory-acks", sessions=True)
    for name in ("transport-bundles", "transport-download-pins",
                 "transport-maintenance-receipts", "inventory-generations"):
        path = work / name
        out[name] = sorted(os.listdir(path))[:12] if path.is_dir() else None
    active = work / "transport-maintenance-receipts" / "active-maintenance.json"
    out["active_maintenance_present"] = active.exists()
    target = None
    admission = work / "bundle-admission-state.json"
    if admission.is_file() and admission.stat().st_size <= 65536:
        state = json.loads(admission.read_text(encoding="utf-8"))
        out["admission_state"] = {k: state.get(k) for k in (
            "outcome", "attempt_count", "next_retry_unix", "identity")}
        target = ((state.get("identity") or {}).get("generation_id"))
    if isinstance(target, str) and H64.fullmatch(target):
        sys.path.insert(0, "/app")
        try:
            from data_sync_bundle_storage import check_derivative_admission
            from data_sync_bundle_transport import MAX_PACKAGE_BYTES
            out["derivative_admission"] = check_derivative_admission(
                work / "transport-bundles", target, MAX_PACKAGE_BYTES)
        except Exception as exc:
            out["derivative_admission_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    print(json.dumps(out, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
