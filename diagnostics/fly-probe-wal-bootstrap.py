#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, "/app")
epoch = os.environ["EXPECTED_EPOCH"].strip()
root = Path("/app/data/runtime")
out = {"epoch": epoch, "steps": []}
t0 = time.time()
try:
    from research_v3_store import V3EvidenceStore
    store = V3EvidenceStore(root, epoch_id=epoch)
    out["identity"] = store._identity_binding()
    t1 = time.time()
    wal_status = store.emergency_wal_runtime_status()
    out["steps"].append({"name": "wal_status", "elapsed_s": round(time.time()-t1, 2), "status": wal_status})
    t2 = time.time()
    wal_action = store.replay_one_emergency_wal_record()
    out["steps"].append({"name": "wal_replay_one", "elapsed_s": round(time.time()-t2, 2), "action": wal_action})
    t3 = time.time()
    rr = store.advance_one_emergency_bootstrap_round_robin()
    out["steps"].append({"name": "bootstrap_rr", "elapsed_s": round(time.time()-t3, 2), "rr": rr})
    out["ok"] = True
except Exception as exc:
    out["ok"] = False
    out["error"] = f"{type(exc).__name__}:{exc}"
out["elapsed_s"] = round(time.time() - t0, 2)
print(json.dumps(out, sort_keys=True, default=str))
