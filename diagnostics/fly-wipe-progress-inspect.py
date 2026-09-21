#!/usr/bin/env python3
import json, os
from pathlib import Path
root = Path("/app/data/runtime")
out = {"schema":"wipe_progress_inspect_v1"}
active = root / "research_reset_receipts" / "ACTIVE_RESET.json"
out["active_reset_exists"] = active.exists()
if active.exists():
    out["active_reset"] = json.loads(active.read_text("utf-8"))
    rid = out["active_reset"].get("reset_id")
    if rid:
        op = root / "research_reset_receipts" / rid / "operation.json"
        binding = root / "research_reset_receipts" / rid / "binding.json"
        out["operation_exists"] = op.exists()
        out["binding_exists"] = binding.exists()
        if op.exists():
            out["operation"] = json.loads(op.read_text("utf-8"))
        if binding.exists():
            b = json.loads(binding.read_text("utf-8"))
            out["binding_keys"] = sorted(b.keys())
            out["new_epoch"] = (b.get("proof") or {}).get("new_epoch") or b.get("new_epoch")
# preflight diagnostics
pf = list((root / "research_reset_receipts").glob("**/preflight*.json"))[:10]
out["preflight_glob_count"] = len(list((root/"research_reset_receipts").rglob("*.json")))
# latest diagnostic attempt
diag = root / "research_reset_diagnostics"
out["diagnostics_dir"] = diag.exists()
if diag.exists():
    files = sorted(diag.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    out["recent_diagnostics"] = []
    for p in files:
        try:
            row = json.loads(p.read_text("utf-8"))
            out["recent_diagnostics"].append({"path": str(p.relative_to(root)), "stage": row.get("stage"), "status": row.get("status"), "attempt": row.get("attempt_id")})
        except Exception as e:
            out["recent_diagnostics"].append({"path": str(p), "error": str(e)})
# df
import subprocess
out["df"] = subprocess.check_output(["df","-h","/app/data"], text=True)
print(json.dumps(out, indent=2, default=str))
