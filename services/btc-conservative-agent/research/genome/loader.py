"""Load genome layers from research.db."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List


def default_db_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    agent_root = os.path.dirname(os.path.dirname(here))
    return os.path.join(agent_root, "research.db")


def load_table(db_path: str, table: str, limit: int = 0) -> List[Dict[str, Any]]:
    if not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = f"SELECT payload_json FROM {table} ORDER BY ts DESC"
    if limit > 0:
        sql += f" LIMIT {limit}"
    rows = []
    for row in conn.execute(sql):
        try:
            rows.append(json.loads(row["payload_json"]))
        except (TypeError, json.JSONDecodeError):
            continue
    conn.close()
    return rows


def load_all_layers(db_path: str | None = None) -> Dict[str, List[Dict[str, Any]]]:
    path = db_path or default_db_path()
    return {
        "environment": load_table(path, "environment_genome"),
        "market": load_table(path, "market_genome"),
        "decision": load_table(path, "decision_genome"),
        "execution": load_table(path, "execution_genome"),
        "lifecycle": load_table(path, "lifecycle_genome"),
        "trade": load_table(path, "trade_genome"),
    }
