"""Read-only closure census of the three volume cleanup transaction owners."""
import hashlib
import json
import os
from pathlib import Path
import stat


def audit_auxiliary_cleanup(volume_root, *, max_entries=10000, max_bytes=16777216):
    root = Path(volume_root).absolute()
    if not Path(volume_root).is_absolute() or root == Path(root.anchor) or root == Path.home():
        raise ValueError("EXPLICIT_VOLUME_REQUIRED")
    if type(max_entries) is not int or not 0 < max_entries <= 100000 or type(max_bytes) is not int or not 0 < max_bytes <= 67108864:
        raise ValueError("INVALID_AUDIT_BOUNDS")
    def checked(path):
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("UNSAFE_AUDIT_LINK")
        return path.stat()
    checked(root)
    blockers, receipts = [], []
    count = consumed = 0
    complete = True
    specs = (
        ("v3/lifecycle_cleanup_transactions", (("PREPARED", "COMMITTED", "bundle_id"),)),
        ("v3/lifecycle_purge_transactions", (("PREPARED", "PURGED", "bundle_id"),)),
        ("raw_generation_cleanup_transactions", (("PREPARED", "QUARANTINED", "generation_id"),
                                                  ("PURGE_PREPARED", "PURGED", "generation_id"))),
    )
    def entries(path):
        nonlocal count
        if not path.exists(): return []
        checked(path)
        result = []
        with os.scandir(path) as stream:
            for entry in stream:
                count += 1
                if count > max_entries: raise ValueError("ENTRY_LIMIT")
                result.append(Path(entry.path))
        return result
    try:
        for relative, pairs in specs:
            folder = root / relative
            for tx in entries(folder):
                if not stat.S_ISDIR(checked(tx).st_mode): raise ValueError("INVALID_TRANSACTION_DIRECTORY")
                rows = {}
                allowed = {state + ".json" for pair in pairs for state in pair[:2]}
                for path in entries(tx):
                    info = checked(path)
                    if path.name not in allowed or not stat.S_ISREG(info.st_mode):
                        raise ValueError("UNKNOWN_TRANSACTION_MEMBER")
                    if consumed + info.st_size > max_bytes: raise ValueError("METADATA_LIMIT")
                    raw = path.read_bytes(); consumed += len(raw)
                    if consumed > max_bytes or path.stat().st_mtime_ns != info.st_mtime_ns:
                        raise ValueError("METADATA_CHANGED_OR_LIMIT")
                    row = json.loads(raw)
                    if not isinstance(row, dict): raise ValueError("INVALID_TRANSACTION_JSON")
                    rows[path.stem] = row
                    receipts.append({"path": path.relative_to(root).as_posix(),
                                     "sha256": hashlib.sha256(raw).hexdigest()})
                for start, end, identity in pairs:
                    if start not in rows and end not in rows: continue
                    a, b = rows.get(start), rows.get(end)
                    valid = (isinstance(a, dict) and isinstance(b, dict)
                             and a.get("state") == start and b.get("state") == end
                             and isinstance(a.get(identity), str) and bool(a[identity])
                             and a[identity] == b.get(identity)
                             and isinstance(a.get("schema"), str) and isinstance(b.get("schema"), str))
                    schemas = ({"PREPARED": "lifecycle_cleanup_transaction_v2", "COMMITTED": "lifecycle_cleanup_transaction_v2"}
                        if relative == "v3/lifecycle_cleanup_transactions" else
                        {"PREPARED": "lifecycle_cleanup_purge_v1", "PURGED": "lifecycle_cleanup_purge_v1"}
                        if relative == "v3/lifecycle_purge_transactions" else
                        {"PREPARED": "raw_generation_cleanup_transaction_v1", "QUARANTINED": "raw_generation_cleanup_transaction_v1",
                         "PURGE_PREPARED": "raw_generation_cleanup_transaction_v1", "PURGED": "raw_generation_purge_receipt_v1"})
                    valid = valid and a["schema"] == schemas[start] and b["schema"] == schemas[end]
                    if not valid:
                        blockers.append({"path": tx.relative_to(root).as_posix(),
                                         "code": "CLEANUP_PENDING_OR_INVALID", "phase": start})
    except (OSError, ValueError) as exc:
        complete = False
        blockers.append({"code": "CLEANUP_AUDIT_INCOMPLETE", "reason": type(exc).__name__})
    result = {"schema": "research_reset_auxiliary_audit_v1", "complete": complete,
              "safe": complete and not blockers, "blockers": blockers, "receipts": receipts,
              "scope": "TRANSACTION_PHASE_CLOSURE_NOT_PURGE_AUTHORIZATION",
              "pending_or_unknown_count": len(blockers) if complete else None,
              "scanned_entries": count, "metadata_bytes": consumed}
    result["sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
