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


def _artifact(path):
    info = path.lstat()
    return {"name": path.name[:80], "bytes": info.st_size, "mtime": int(info.st_mtime),
            "dir": path.is_dir() and not path.is_symlink()}


def _derivative_detail(root):
    if not root.is_dir():
        return None
    report = {}
    for generation in sorted(root.iterdir())[:8]:
        if not generation.name.startswith("g-") or not generation.is_dir():
            continue
        children = sorted(generation.iterdir())[:16]
        entry = {"children": [_artifact(child) for child in children]}
        state_path = generation / "bundle-worker-state.json"
        indexed = set()
        if state_path.is_file() and state_path.stat().st_size <= 4 * 1024 * 1024:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                index = state.get("package_index") if isinstance(state, dict) else None
                entry["state_status"] = state.get("status") if isinstance(state, dict) else None
                entry["indexed_packages"] = len(index) if isinstance(index, list) else None
                for row in index if isinstance(index, list) else []:
                    if isinstance(row, dict) and isinstance(row.get("package_sha256"), str):
                        indexed.add(row["package_sha256"] + ".tar")
                        indexed.add("d-" + row["package_sha256"][:20] + ".json")
            except (OSError, ValueError) as exc:
                entry["state_error"] = type(exc).__name__
        for name in ("packages", "descriptors"):
            folder = generation / name
            if not folder.is_dir():
                continue
            names = []
            with os.scandir(folder) as it:
                for count, item in enumerate(it):
                    if count >= LIMIT:
                        break
                    names.append(item.name)
            unindexed = sorted(n for n in names if n not in indexed)
            entry[name] = {"count": len(names), "unindexed_count": len(unindexed),
                           "unindexed_sample": [_artifact(folder / n) for n in unindexed[:10]]}
        report[generation.name] = entry
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
    out["derivatives"] = _derivative_detail(work / "transport-bundles")
    active = work / "transport-maintenance-receipts" / "active-maintenance.json"
    out["active_maintenance_present"] = active.exists()
    if active.is_file() and active.stat().st_size <= 65536:
        intent = json.loads(active.read_text(encoding="utf-8"))
        candidate = str(intent.get("candidate") or "")
        token = str(intent.get("fence_token") or "")
        out["active_maintenance"] = {
            **{k: intent.get(k) for k in ("schema", "complete", "abandoned_unfenced",
                                          "candidate", "target_generation", "current_identity")},
            "fence_token_prefix": token[:16],
            "retirement_receipt_present": bool(token) and (
                work / "transport-maintenance-receipts" / f"r-{token}.json").exists(),
            "candidate_derivative_present": bool(candidate) and (
                work / "transport-bundles" / f"g-{candidate[:16]}").exists(),
        }
        pin = work / "transport-download-pins" / f"{candidate}.json"
        if H64.fullmatch(candidate) and pin.is_file() and pin.stat().st_size <= 65536:
            state = json.loads(pin.read_text(encoding="utf-8"))
            fence = state.get("fence")
            out["candidate_pin"] = {
                "fence_present": fence is not None,
                "fence_token_prefix": str((fence or {}).get("token") or "")[:16],
                "session_count": len(state.get("sessions") or {}),
                "session_expiries": sorted((state.get("sessions") or {}).values())[-3:],
                "now_unix": time.time(),
            }
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
