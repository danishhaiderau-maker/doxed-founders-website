#!/usr/bin/env python3
import json, time
from pathlib import Path
root = Path("/app/data/runtime")
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
# research session
for name in ["research_session.json", "persistent_config.json", "paper_lifecycle_v1.json"]:
    p = root / name
    if p.is_file():
        try:
            j = json.loads(p.read_text("utf-8"))
            if name == "research_session.json":
                out["session_epoch"] = j.get("collector_v22_epoch_id")
                out["session_keys"] = sorted(j.keys())[:30]
            elif name == "paper_lifecycle_v1.json":
                out["paper"] = {"positions": len(j.get("positions") or []), "orders": len(j.get("pending_orders") or []), "live_armed": j.get("live_armed"), "paper_only": j.get("paper_only")}
        except Exception as exc:
            out[name] = f"ERR:{type(exc).__name__}"
# find active reset receipts
hits = []
for p in root.rglob("*"):
    if not p.is_file():
        continue
    n = p.name.lower()
    if "reset" in n or "fresh" in n or n == "operation.json" or "wipe" in n:
        if p.stat().st_size > 2_000_000:
            continue
        rel = str(p.relative_to(root))
        if any(x in rel for x in ["research_archive", "quarantine", "analyzer_generations"]):
            if "operation.json" not in n and "active_reset" not in rel.lower():
                continue
        hits.append({"path": rel, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime})
hits = sorted(hits, key=lambda r: -r["mtime"])[:40]
out["resetish_files"] = hits
# look specifically for research reset operation dirs
for cand in [
    root / "v3/receipts/research_reset_operations_v1",
    root / "v3/receipts/fresh_research_reset_v1",
    root / "research_reset",
    root / "v3/receipts",
]:
    if cand.exists():
        out.setdefault("dirs", {})[str(cand)] = sorted(p.name for p in cand.iterdir())[:50]
# disk
import os
st = os.statvfs("/app/data")
out["disk"] = {"used_mib": round((st.f_blocks - st.f_bfree) * st.f_frsize / 1024 / 1024, 1), "free_mib": round(st.f_bfree * st.f_frsize / 1024 / 1024, 1), "total_mib": round(st.f_blocks * st.f_frsize / 1024 / 1024, 1)}
print(json.dumps(out, indent=2, sort_keys=True))
