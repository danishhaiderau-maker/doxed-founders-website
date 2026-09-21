#!/usr/bin/env python3
import json, os, hashlib
from pathlib import Path
RUNTIME = Path("/app/data/runtime")
out = {"schema": "lifecycle_bootstrap_inspect_v1"}
# recovery state
idx = RUNTIME / "v3" / "lifecycle_bundle_index"
out["index_dir_exists"] = idx.exists()
if idx.exists():
    out["index_listing"] = sorted(p.name for p in idx.iterdir())[:40]
    state = idx / "recovery-state.json"
    out["recovery_state_exists"] = state.exists()
    if state.exists():
        try:
            row = json.loads(state.read_text("utf-8"))
            out["recovery_state"] = {
                "schema": row.get("schema"),
                "phase": row.get("phase"),
                "trigger": row.get("trigger"),
                "recovery_id": row.get("recovery_id"),
                "reset_proof": row.get("reset_proof"),
                "completion_receipt_sha256": row.get("completion_receipt_sha256"),
                "keys": sorted(row.keys()),
            }
        except Exception as e:
            out["recovery_state_error"] = f"{type(e).__name__}:{e}"
# ACTIVE_RESET and recent operations
rr = RUNTIME / "research_reset_receipts"
out["reset_receipts_exists"] = rr.exists()
if rr.exists():
    actives = sorted(rr.glob("ACTIVE_RESET*"))
    out["active_reset_files"] = [p.name for p in actives]
    for p in actives[:5]:
        try:
            j = json.loads(p.read_text("utf-8"))
            out.setdefault("active_reset_rows", []).append({"name": p.name, "row": j})
        except Exception as e:
            out.setdefault("active_reset_rows", []).append({"name": p.name, "error": str(e)})
    # newest operation.json under reset dirs
    ops = sorted(rr.glob("*/operation.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    out["recent_operations"] = []
    for op in ops:
        try:
            j = json.loads(op.read_text("utf-8"))
            proof = j.get("proof") or {}
            out["recent_operations"].append({
                "path": str(op.relative_to(RUNTIME)).replace("\\","/"),
                "stage": j.get("stage"),
                "schema": j.get("schema"),
                "new_epoch_id": j.get("new_epoch_id") or proof.get("new_epoch_id"),
                "retired_epoch_id": proof.get("retired_epoch_id"),
                "source_revision": proof.get("source_revision"),
                "sha256": hashlib.sha256(op.read_bytes()).hexdigest(),
                "bytes": op.stat().st_size,
            })
        except Exception as e:
            out["recent_operations"].append({"path": str(op), "error": str(e)})
# session epoch
for cand in [RUNTIME/"research_session_meta.json", RUNTIME/"collector_session.json", RUNTIME/"v3/session.json"]:
    if cand.exists():
        try:
            out[f"meta:{cand.name}"] = json.loads(cand.read_text("utf-8"))
        except Exception as e:
            out[f"meta:{cand.name}"] = str(e)
# env
out["SOURCE_GIT_REV"] = os.getenv("SOURCE_GIT_REV")
print(json.dumps(out, indent=2, sort_keys=True, default=str))
