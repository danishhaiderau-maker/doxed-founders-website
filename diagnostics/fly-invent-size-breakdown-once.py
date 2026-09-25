#!/usr/bin/env python3
"""One-shot invent size breakdown on Fly (read-only). Never arms Bitfinex."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
EXCLUDED_DIRS = {
    "research_archive", "research_session_archives", "research_epoch_quarantine",
    ".locks", ".data-sync-snapshots", "archive-v2", "object-store", "object_store",
    "__pycache__", ".git", "node_modules",
}
EXCLUDED_NAMES = {
    "manifest.json", "genome_cluster_library.json", "sync_inventory_current.json",
    "research_events_v22.provisional.json", "collector_storage_state.json",
    "open_positions.json",
}


def main() -> int:
    roots = [DATA_ROOT / "runtime", DATA_ROOT]
    by_prefix: dict[str, list[tuple[int, str]]] = defaultdict(list)
    total = 0
    count = 0
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            # skip if any excluded dir in path parts
            parts = set(Path(dirpath).parts)
            if parts & EXCLUDED_DIRS:
                dirnames[:] = []
                continue
            for name in filenames:
                if name in EXCLUDED_NAMES:
                    continue
                path = Path(dirpath) / name
                try:
                    key = str(path.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    size = path.stat().st_size
                except OSError:
                    continue
                rel = str(path.relative_to(DATA_ROOT)).replace("\\", "/")
                total += size
                count += 1
                if rel.startswith("runtime/"):
                    top = "/".join(rel.split("/")[:3]) if rel.count("/") >= 2 else rel
                else:
                    top = "/".join(rel.split("/")[:2]) if "/" in rel else rel
                # coarser buckets
                if "/v3/receipts/" in rel or rel.startswith("runtime/v3/receipts"):
                    bucket = "v3/receipts"
                elif "/v3/market_segments/" in rel or "market_segments" in rel:
                    bucket = "v3/market_segments"
                elif "/v3/ledgers/" in rel:
                    bucket = "v3/ledgers"
                elif rel.endswith(".sqlite3") or rel.endswith(".sqlite"):
                    bucket = "sqlite"
                elif "market_microstructure" in rel or "source_order_market" in rel:
                    bucket = "market_jsonl"
                elif "crash_dump" in rel or rel.endswith(".log"):
                    bucket = "logs_crash"
                elif "signal_snapshot" in rel or "research_reset" in rel:
                    bucket = "snapshots_reset"
                else:
                    bucket = top
                by_prefix[bucket].append((size, rel))

    ranked = []
    for bucket, rows in by_prefix.items():
        s = sum(sz for sz, _ in rows)
        ranked.append((s, len(rows), bucket))
    ranked.sort(reverse=True)
    top_files = sorted(((sz, rel) for rows in by_prefix.values() for sz, rel in rows), reverse=True)[:25]
    print(json.dumps({
        "ok": True,
        "total_mib": round(total / 1048576, 2),
        "file_count": count,
        "buckets_mib": [
            {"bucket": b, "mib": round(s / 1048576, 2), "files": n}
            for s, n, b in ranked[:20]
        ],
        "top_files_mib": [
            {"mib": round(s / 1048576, 2), "rel": rel}
            for s, rel in top_files
        ],
        "NO_SAFE": True,
        "never_arm": True,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
