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

# These fields define causal identity or point-in-time evidence.  A later
# maturation rebuild may add lifecycle state, but it must never erase an
# identity/snapshot already captured by the original signal.
_PRESERVE_NONEMPTY_SOURCE_FIELDS = (
    "shared_ai_call_id",
    "event_episode_id",
    "raw_direction",
    "final_direction",
    "symbol",
    "pair",
    "created_ts_ts",
    "signal_ts",
    "research_feature_snapshot",
)


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


def upsert_provisional_event(event_id: str, source: Mapping[str, Any], *, epoch_id: str, data_dir: Optional[str] = None) -> Optional[dict]:
    """Durably insert/replace the recovery source for one provisional event."""
    key, epoch = str(event_id or "").strip(), str(epoch_id or "").strip()
    if not key or not epoch:
        raise ValueError("event_id and epoch_id are required")
    path = _path(data_dir)
    with _LOCK:
        store = _read_unlocked(path)
        incoming = dict(source)
        previous = store["events"].get(key)
        if isinstance(previous, dict) and str(previous.get("epoch_id") or "") == epoch:
            prior_source = previous.get("source") if isinstance(previous.get("source"), dict) else {}
            merged = dict(prior_source)
            merged.update(incoming)
            for field in _PRESERVE_NONEMPTY_SOURCE_FIELDS:
                prior_value = prior_source.get(field)
                if prior_value not in (None, "", {}, []):
                    merged[field] = prior_value
            incoming = merged
        store["events"][key] = {"event_id": key, "epoch_id": epoch, "source": incoming}
        _atomic_write_unlocked(path, store)
    try:
        from research_v3_bridge import dual_write_provisional_source
        return dual_write_provisional_source(key, incoming, epoch_id=epoch, data_dir=str(path.parent))
    except Exception as exc:
        # The v2 provisional journal remains the recovery authority during the
        # migration.  A V3 dual-write error is returned to callers/supervision
        # rather than destroying the durable source or inventing completion.
        return {
            "schema": "v3_provisional_dual_write_receipt_v1",
            "event_id": key,
            "written": False,
            "error": f"{type(exc).__name__}:{exc}",
        }


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


def reset_provisional_events(*, epoch_id: str, data_dir: Optional[str] = None) -> None:
    """Atomically replace every pre-bound recovery source at an epoch boundary."""
    epoch = str(epoch_id or "").strip()
    if not epoch:
        raise ValueError("epoch_id is required")
    path = _path(data_dir)
    with _LOCK:
        payload = _empty()
        payload["bound_epoch_id"] = epoch
        _atomic_write_unlocked(path, payload)
