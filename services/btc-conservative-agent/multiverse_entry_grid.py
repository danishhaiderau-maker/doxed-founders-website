"""One hypothetical entry grid per shared AI call for ``order_multiverse.jsonl``.

Every tile order of one shared AI call used to carry its own 300-child
offset x chase grid, anchored at that tile's own signal second, price and
remaining TTL.  The grid is a call-level discovery universe, so it is now
anchored once per call (``entry_grid_anchor``) and written once to
``order_multiverse_entry_grid.jsonl``; tile rows carry ``entry_children_ref``.

Readers call :func:`hydrate_entry_children` (or :func:`load_order_multiverse`)
to get rows with ``entry_children`` restored, so pre-dedupe inline rows and
post-dedupe referenced rows read identically.  The canonical v2.2 event store
keeps inline children; only the multiverse stream is deduplicated.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterable, Iterator, Mapping, Optional

GRID_FILE = "order_multiverse_entry_grid.jsonl"
GRID_SCHEMA = "order_multiverse_entry_grid_v1"
REF_SCHEMA = "order_multiverse_entry_grid_ref_v1"
ANCHOR_SCHEMA = "entry_grid_anchor_v1"
ANCHOR_SHARED_CALL = "SHARED_AI_CALL_FIRST_TILE"
ANCHOR_TILE = "TILE_SIGNAL"

STATUS_INLINE = "INLINE"
STATUS_HYDRATED = "HYDRATED"
STATUS_GRID_MISSING = "GRID_MISSING"
STATUS_ABSENT = "ABSENT"

_ROTATION_RE = re.compile(r"^(?P<base>.+)\.(?P<index>\d+)$")


def make_anchor(*, shared_ai_call_id: Optional[str], signal_ts: float, signal_price: float,
                direction: str, ttl_sec: float) -> dict:
    call = str(shared_ai_call_id or "").strip()
    return {
        "schema": ANCHOR_SCHEMA,
        "basis": ANCHOR_SHARED_CALL if call else ANCHOR_TILE,
        "shared_ai_call_id": call or None,
        "signal_ts": float(signal_ts),
        "signal_price": float(signal_price),
        "direction": str(direction or "SHORT").upper(),
        "ttl_sec": float(ttl_sec),
    }


def anchor_is_valid(anchor: Any) -> bool:
    if not isinstance(anchor, Mapping) or anchor.get("schema") != ANCHOR_SCHEMA:
        return False
    try:
        return (float(anchor["signal_ts"]) > 0 and float(anchor["signal_price"]) > 0
                and float(anchor["ttl_sec"]) > 0
                and str(anchor.get("direction") or "") in ("LONG", "SHORT"))
    except (KeyError, TypeError, ValueError):
        return False


def grid_digest(children: Iterable[Mapping[str, Any]]) -> str:
    encoded = json.dumps(list(children or []), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def split_entry_grid(record: Mapping[str, Any]) -> tuple[dict, Optional[dict]]:
    """Return (tile row with a reference, grid row) for one v2.2 record."""
    children = record.get("entry_children")
    row = dict(record)
    if not isinstance(children, list) or not children:
        return row, None
    digest = grid_digest(children)
    anchor = record.get("entry_grid_anchor") if isinstance(record.get("entry_grid_anchor"), Mapping) else {}
    episode = record.get("event_episode") if isinstance(record.get("event_episode"), Mapping) else {}
    call = anchor.get("shared_ai_call_id") or episode.get("shared_ai_call_id")
    row.pop("entry_children", None)
    row["entry_children_ref"] = {
        "schema": REF_SCHEMA,
        "grid_sha256": digest,
        "shared_ai_call_id": call,
        "entry_children_count": len(children),
        "file": GRID_FILE,
    }
    grid = {
        "schema": GRID_SCHEMA,
        "grid_sha256": digest,
        "shared_ai_call_id": call,
        "event_episode_id": record.get("event_episode_id"),
        "epoch_id": record.get("epoch_id"),
        "first_event_id": record.get("event_id"),
        "entry_grid_anchor": dict(anchor) if anchor else None,
        "entry_children_count": len(children),
        "entry_children": children,
    }
    return row, grid


def hydrate_entry_children(row: Mapping[str, Any], grids: Mapping[str, list]) -> dict:
    """Restore ``entry_children`` from the grid index; never invents children."""
    out = dict(row)
    if isinstance(out.get("entry_children"), list):
        out["entry_children_status"] = STATUS_INLINE
        return out
    ref = out.get("entry_children_ref")
    if not isinstance(ref, Mapping):
        out["entry_children_status"] = STATUS_ABSENT
        return out
    children = grids.get(str(ref.get("grid_sha256") or ""))
    if children is None:
        out["entry_children"] = []
        out["entry_children_status"] = STATUS_GRID_MISSING
        return out
    out["entry_children"] = [dict(child) for child in children]
    out["entry_children_status"] = STATUS_HYDRATED
    return out


def rotation_family(directory: str, file_name: str) -> list[str]:
    """Numbered rotations (lower index = older) followed by the live file."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    rotations = []
    for name in names:
        match = _ROTATION_RE.match(name)
        if match and match.group("base") == file_name:
            rotations.append((int(match.group("index")), os.path.join(directory, name)))
    family = [path for _, path in sorted(rotations)]
    live = os.path.join(directory, file_name)
    if os.path.isfile(live):
        family.append(live)
    return family


def _iter_jsonl(paths: Iterable[str]) -> Iterator[dict]:
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        yield row
        except OSError:
            continue


def load_grid_index(directory: str, file_name: str = GRID_FILE) -> dict[str, list]:
    grids: dict[str, list] = {}
    for row in _iter_jsonl(rotation_family(directory, file_name)):
        if row.get("schema") != GRID_SCHEMA:
            continue
        digest = str(row.get("grid_sha256") or "")
        children = row.get("entry_children")
        if digest and isinstance(children, list) and digest not in grids:
            if grid_digest(children) == digest:
                grids[digest] = children
    return grids


def load_order_multiverse(directory: str, file_name: str = "order_multiverse.jsonl") -> Iterator[dict]:
    """Compatibility reader: every multiverse row with ``entry_children`` present."""
    grids = load_grid_index(directory)
    for row in _iter_jsonl(rotation_family(directory, file_name)):
        yield hydrate_entry_children(row, grids)
