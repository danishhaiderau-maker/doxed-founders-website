"""Incrementally rebuild research.db's genome tables from their JSONL mirrors.

Fly no longer snapshots the 2 GB hot research.db (online backup timed out at
600 s and blocked the segment shipper). Every genome write on Fly is mirrored
to research/genome/<table>.jsonl, which ships incrementally as append streams;
this replays only the bytes appended since the last run (INSERT OR REPLACE,
the same semantics as research_genome/store.py upsert_layer).

Usage: rebuild_research_db_from_genome_mirrors.py --data-root <canonical-research-data>
Writes <data-root>/research.db and <data-root>/.research_db_rebuild.state.json.
Exit 0 on success (including nothing to do), 1 on error. Never touches Fly.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"
sys.path.insert(0, str(AGENT))
from research_genome.store import DDL, LAYER_TABLES  # noqa: E402

# table -> (id column, parent column, parent payload key)
COLUMNS = {
    "environment_genome": ("environment_id", None, None),
    "market_genome": ("market_genome_id", "environment_id", "environment_id"),
    "decision_genome": ("decision_id", "market_genome_id", "market_genome_id"),
    "execution_genome": ("execution_id", "decision_id", "decision_id"),
    "lifecycle_genome": ("lifecycle_id", "execution_id", "execution_id"),
    "trade_genome": ("trade_id", "decision_id", "decision_id"),
}
SCHEMA = "research_db_rebuild_state_v1"


def _load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") == SCHEMA:
            return state
    except (OSError, ValueError):
        pass
    return {"schema": SCHEMA, "files": {}}


def rebuild(data_root: Path) -> dict:
    mirror_dir = data_root / "research" / "genome"
    db_path = data_root / "research.db"
    state_path = data_root / ".research_db_rebuild.state.json"
    state = _load_state(state_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DDL)
    summary = {"schema": "research_db_rebuild_v1", "started_at": time.time(), "tables": {}}
    for table in LAYER_TABLES.values():
        path = mirror_dir / f"{table}.jsonl"
        if not path.is_file():
            continue
        st = path.stat()
        tracked = state["files"].get(table) or {}
        offset = int(tracked.get("offset") or 0)
        # A rotated/replaced mirror (new inode or shrink) is replayed from 0;
        # INSERT OR REPLACE keeps that idempotent.
        if tracked.get("inode") != st.st_ino or st.st_size < offset:
            offset = 0
        id_col, parent_col, parent_key = COLUMNS[table]
        rows = bad = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break  # partial tail; picked up next run
                offset += len(raw)
                try:
                    payload = json.loads(raw)
                    row_id = str(payload[id_col])
                except (ValueError, KeyError, TypeError):
                    bad += 1
                    continue
                ts = str(payload.get("ts") or "")
                body = json.dumps(payload, default=str)
                if parent_col:
                    conn.execute(f"INSERT OR REPLACE INTO {table} ({id_col}, {parent_col}, ts, payload_json) "
                                 "VALUES (?, ?, ?, ?)", (row_id, payload.get(parent_key), ts, body))
                else:
                    conn.execute(f"INSERT OR REPLACE INTO {table} ({id_col}, ts, payload_json) VALUES (?, ?, ?)",
                                 (row_id, ts, body))
                rows += 1
        conn.commit()
        state["files"][table] = {"inode": st.st_ino, "offset": offset, "updated_at": time.time()}
        summary["tables"][table] = {"rows_applied": rows, "bad_rows": bad, "offset": offset}
    conn.close()
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, state_path)
    summary["finished_at"] = time.time()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", required=True)
    args = ap.parse_args()
    try:
        print(json.dumps(rebuild(Path(args.data_root)), sort_keys=True))
    except Exception as exc:  # report, never raise into the analyzer cycle
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
