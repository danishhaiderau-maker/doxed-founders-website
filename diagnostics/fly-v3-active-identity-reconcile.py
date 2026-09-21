#!/usr/bin/env python3
import json, time
from pathlib import Path
root = Path("/app/data/runtime")
rr = root / "research_reset_receipts"
active = rr / "ACTIVE_RESET.json"
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
if active.exists():
    out["active_reset"] = json.loads(active.read_text("utf-8"))
# newest operation.json
ops = sorted(rr.glob("*/operation.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if ops:
    newest = ops[0]
    out["newest_operation_path"] = str(newest.relative_to(root))
    out["newest_operation"] = json.loads(newest.read_text("utf-8"))
# disk
import os
st = os.statvfs("/app/data")
out["disk"] = {"used_mib": round((st.f_blocks - st.f_bfree) * st.f_frsize / 1024 / 1024, 1), "free_mib": round(st.f_bfree * st.f_frsize / 1024 / 1024, 1)}
# session
sp = root / "research_session.json"
if sp.exists():
    s = json.loads(sp.read_text("utf-8"))
    out["session_epoch"] = s.get("collector_v22_epoch_id")
print(json.dumps(out, indent=2, sort_keys=True)[:20000])
