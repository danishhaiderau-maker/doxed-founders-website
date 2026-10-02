from pathlib import Path
import json
import shutil

root = Path("/app/data/runtime")
hits = list(root.rglob("crash_dump.json"))
bad = []
for p in hits:
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        bad.append(
            {
                "path": str(p),
                "error": type(exc).__name__,
                "bytes": p.stat().st_size,
            }
        )
print(
    json.dumps(
        {"crash_dump_count": len(hits), "invalid_count": len(bad), "invalid": bad[:50]},
        sort_keys=True,
    )
)
qroot = root / "research_epoch_quarantine" / "ops-corrupt-recovery-receipts-20260921"
moved = []
for row in bad:
    p = Path(row["path"])
    if "recovery_receipts" not in p.parts and "research_reset_receipts" not in p.parts:
        continue
    dest = qroot / p.relative_to(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest))
    moved.append({"from": str(p), "to": str(dest)})
print(json.dumps({"moved_count": len(moved), "moved": moved}, sort_keys=True))
