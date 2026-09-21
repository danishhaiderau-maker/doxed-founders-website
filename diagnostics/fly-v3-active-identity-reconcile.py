#!/usr/bin/env python3
import json, os, sys, time, traceback
from pathlib import Path
from collections import Counter

RUNTIME = Path("/app/data/runtime")
print("===PATHS===")
print(json.dumps({
  "runtime_exists": RUNTIME.exists(),
  "cwd": os.getcwd(),
  "app_listing": sorted(p.name for p in Path("/app").iterdir())[:40] if Path("/app").exists() else [],
  "data_listing": sorted(p.name for p in Path("/app/data").iterdir())[:40] if Path("/app/data").exists() else [],
}, indent=2))

receipt = RUNTIME / "v3" / "receipts"
print("===ALL_ACTIVE_JSON===")
actives = []
if receipt.exists():
  for p in sorted(receipt.rglob("ACTIVE.json")):
    try:
      j = json.loads(p.read_text("utf-8"))
      ident = j.get("identity") if isinstance(j, dict) else None
    except Exception as exc:
      actives.append({"path": str(p), "error": f"{type(exc).__name__}:{exc}"})
      continue
    actives.append({
      "path": str(p.relative_to(RUNTIME)),
      "bytes": p.stat().st_size,
      "epoch": (ident or {}).get("epoch_id") if isinstance(ident, dict) else None,
      "source_revision": (ident or {}).get("source_revision") if isinstance(ident, dict) else None,
      "deployed_revision": (ident or {}).get("deployed_revision") if isinstance(ident, dict) else None,
      "tile": (str((ident or {}).get("tile_config_signature") or "")[:16] if isinstance(ident, dict) else None),
      "identity": ident if isinstance(ident, dict) else None,
      "top_keys": sorted(j.keys())[:20] if isinstance(j, dict) else type(j).__name__,
    })
print(json.dumps(actives, indent=2, sort_keys=True))

print("===OPEN_READ_ONLY_SOURCE===")
sys.path.insert(0, "/app")
import research_v3_store, inspect
src = inspect.getsource(research_v3_store.V3EvidenceStore.open_read_only)
print(src)

print("===OPEN_READ_ONLY_CALL===")
try:
  store = research_v3_store.V3EvidenceStore.open_read_only(str(RUNTIME))
  print(json.dumps({"ok": True, "epoch": store.epoch_id, "ident": getattr(store, "_read_identity_override", None)}, indent=2))
except Exception:
  traceback.print_exc()
