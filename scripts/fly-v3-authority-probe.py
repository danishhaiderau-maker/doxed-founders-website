# Read-only: list every V3 authority pointer that V3EvidenceStore.open_read_only
# inspects, in its exact order, with identity, hash and mtime, and mark the first
# pointer whose identity conflicts with the one adopted before it.
import hashlib
import json
import os
from pathlib import Path

ROOTS = ("/app/data/runtime", "/app/data")


def candidates(receipt_dir):
    rows = sorted((receipt_dir / "ledger_generations_v1").glob("*/ACTIVE.json"))
    rows += sorted((receipt_dir / "emergency_record_idempotency_v1").glob("*/complete.json"))
    marker = receipt_dir / "transactional_record_authority_v1" / "ACTIVE.json"
    if marker.exists() or marker.is_symlink():
        rows.append(marker)
    return rows


out = {"process": {k: os.environ.get(k) for k in ("SOURCE_GIT_REV", "DATA_EPOCH_ID")}, "roots": []}
for root in ROOTS:
    receipt_dir = Path(root) / "v3" / "receipts"
    if not receipt_dir.is_dir():
        continue
    adopted, groups, pointers, first_conflict = None, {}, [], None
    for index, pointer in enumerate(candidates(receipt_dir)):
        entry = {"index": index, "path": str(pointer.relative_to(root))}
        try:
            raw = pointer.read_bytes()
            stat = pointer.stat()
            row = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            entry["skipped"] = type(exc).__name__
            pointers.append(entry)
            continue
        identity = row.get("identity")
        entry.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), mtime=int(stat.st_mtime),
                     keys=sorted(row)[:12], identity=identity,
                     boot={k: row.get(k) for k in ("boot_id", "process_boot_id", "generation", "generation_id",
                                                    "created_at", "updated_at", "status") if k in row})
        if isinstance(identity, dict):
            key = json.dumps(identity, sort_keys=True)
            groups.setdefault(key, []).append(index)
            if adopted is None:
                adopted = identity
            elif identity != adopted and first_conflict is None:
                first_conflict = index
                entry["conflicts_with_adopted"] = True
        pointers.append(entry)
    out["roots"].append({
        "root": root, "pointer_count": len(pointers), "adopted_identity": adopted,
        "first_conflict_index": first_conflict,
        "identity_groups": [{"identity": json.loads(k), "count": len(v), "indexes": v[:20]} for k, v in groups.items()],
        "pointers": pointers[:200], "pointers_truncated": len(pointers) > 200,
    })
print(json.dumps(out, sort_keys=True, default=str))
