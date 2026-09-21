#!/usr/bin/env python3
"""One-shot Fly volume repair: rebind collector_v22_epoch_id without wipe.

Writes /app/data/runtime/research_session.json fields required for manifests to
report collection_epoch_status=BOUND. Does not arm live, wipe research, or
CreateGoal. Safe to run while paper trading remains ON.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SESSION = Path(os.environ.get("SESSION_PATH", "/app/data/runtime/research_session.json"))
EPOCH_ID = os.environ["EXPECTED_EPOCH_ID"]
CUTOFF_UTC = os.environ["EXPECTED_CUTOFF_UTC"]


def main() -> int:
    material = f"fresh_research_epoch_v1|SHOWCASE_FRESH_COLLECTION|{CUTOFF_UTC}"
    expected = "epoch-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    if EPOCH_ID != expected:
        print(json.dumps({"ok": False, "error": "epoch mismatch", "expected": expected}))
        return 2
    cutoff_dt = datetime.fromisoformat(CUTOFF_UTC.replace("Z", "+00:00"))
    if cutoff_dt.tzinfo is None:
        cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)
    cutoff_ts = float(cutoff_dt.timestamp())
    prior = {}
    if SESSION.is_file():
        prior = json.loads(SESSION.read_text(encoding="utf-8"))
    meta = dict(prior)
    meta["collector_v22_epoch_id"] = EPOCH_ID
    meta["collector_v22_epoch_ts"] = cutoff_ts
    meta["fresh_collection_start_time"] = cutoff_ts
    meta["fresh_collection_start_iso_utc"] = datetime.fromtimestamp(
        cutoff_ts, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    meta.setdefault("fresh_collection_mode", False)
    meta.setdefault("collector_version", "v3.1")
    meta.setdefault("legacy_collector_version", "v2.2")
    tmp = SESSION.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SESSION)
    written = json.loads(SESSION.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(SESSION),
                "prior_epoch": prior.get("collector_v22_epoch_id"),
                "collection_epoch_id": written.get("collector_v22_epoch_id"),
                "fresh_collection_start_time": written.get("fresh_collection_start_time"),
                "wiped": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
