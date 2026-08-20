"""Crash-safe storage for collector v2.2 events awaiting future tape."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

PROVISIONAL_STORE_FILE = "research_events_v22.provisional.json"
PROVISIONAL_STORE_SCHEMA = "research_event_provisional_store_v1"
_LOCK = threading.RLock()


def _path(data_dir: Optional[str] = None) -> Path:
    return Path(data_dir or os.getcwd()) / PROVISIONAL_STORE_FILE


def _empty() -> dict:
    return {"schema": PROVISIONAL_STORE_SCHEMA, "events": {}}


def _read_unlocked(path: Path) -> dict:
    if not path.is_file():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt provisional event store: {path}") from exc
    if raw.get("schema") != PROVISIONAL_STORE_SCHEMA or not isinstance(raw.get("events"), dict):
        raise ValueError(f"invalid provisional event store schema: {path}")
    return raw


def _atomic_write_unlocked(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def upsert_provisional_event(event_id: str, source: Mapping[str, Any], *, epoch_id: str, data_dir: Optional[str] = None) -> None:
    """Durably insert/replace the recovery source for one provisional event."""
    key, epoch = str(event_id or "").strip(), str(epoch_id or "").strip()
    if not key or not epoch:
        raise ValueError("event_id and epoch_id are required")
    path = _path(data_dir)
    with _LOCK:
        store = _read_unlocked(path)
        store["events"][key] = {"event_id": key, "epoch_id": epoch, "source": dict(source)}
        _atomic_write_unlocked(path, store)


def remove_provisional_event(event_id: str, *, data_dir: Optional[str] = None) -> bool:
    """Remove recovery state only after the caller committed the final event."""
    key, path = str(event_id or "").strip(), _path(data_dir)
    with _LOCK:
        store = _read_unlocked(path)
        if key not in store["events"]:
            return False
        del store["events"][key]
        _atomic_write_unlocked(path, store)
        return True


def load_provisional_events(*, epoch_id: Optional[str] = None, data_dir: Optional[str] = None) -> dict[str, dict]:
    """Return recovery sources, optionally restricted to the active epoch."""
    with _LOCK:
        rows = dict(_read_unlocked(_path(data_dir))["events"])
    wanted, result = None if epoch_id is None else str(epoch_id), {}
    for event_id, row in rows.items():
        if not isinstance(row, dict) or not isinstance(row.get("source"), dict):
            continue
        if wanted is not None and str(row.get("epoch_id")) != wanted:
            continue
        result[str(event_id)] = dict(row["source"])
    return result
