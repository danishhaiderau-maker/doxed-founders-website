"""Fail-closed identity helpers for current-collection analyzer inputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


REQUIRED_MANIFEST_FIELDS = (
    "dataset_epoch",
    "source_revision",
    "deployed_revision",
    "tile_config_signature",
)


def load_current_collection_contract(root: str | Path) -> tuple[dict[str, str] | None, list[str]]:
    from research.canonical_data_store import MANIFEST_SCHEMA, _canonical_bytes

    path = Path(root).resolve() / "canonical_dataset_current.json"
    try:
        if path.stat().st_size > 1024 * 1024:
            return None, ["CURRENT_COLLECTION_POINTER_TOO_LARGE"]
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None, ["CURRENT_COLLECTION_POINTER_TOO_LARGE"]
        payload = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError:
        return None, ["CURRENT_COLLECTION_POINTER_MISSING"]
    except (OSError, UnicodeError, ValueError, TypeError):
        return None, ["CURRENT_COLLECTION_POINTER_INVALID"]
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        return None, ["CURRENT_COLLECTION_POINTER_SCHEMA_INVALID"]
    claimed = str(payload.get("entry_hash") or "").strip().lower()
    material = {key: value for key, value in payload.items() if key != "entry_hash"}
    import hashlib
    computed = hashlib.sha256(_canonical_bytes(material)).hexdigest()
    if claimed != computed:
        return None, ["CURRENT_COLLECTION_POINTER_HASH_INVALID"]
    missing = [
        field for field in REQUIRED_MANIFEST_FIELDS
        if not str(payload.get(field) or "").strip()
        or str(payload.get(field)).strip().upper() == "UNKNOWN"
    ]
    if missing:
        return None, ["CURRENT_COLLECTION_POINTER_IDENTITY_MISSING:" + ",".join(missing)]
    return {
        "epoch_id": str(payload["dataset_epoch"]),
        "source_revision": str(payload["source_revision"]),
        "deployed_revision": str(payload["deployed_revision"]),
        "tile_config_signature": str(payload["tile_config_signature"]),
        "manifest_entry_hash": claimed,
    }, []


def row_contract_blockers(
    row: Mapping[str, Any], contract: Mapping[str, str], *, source: str,
) -> list[str]:
    blockers = []
    epoch_values = {
        str(row.get(alias)).strip()
        for alias in ("epoch_id", "dataset_epoch")
        if row.get(alias) not in (None, "")
    }
    if len(epoch_values) > 1:
        blockers.append(f"{source}:EPOCH_DECLARATION_CONFLICT")
    epoch = next(iter(epoch_values), "")
    if not epoch:
        blockers.append(f"{source}:EPOCH_MISSING")
    elif len(epoch_values) == 1 and epoch != contract["epoch_id"]:
        blockers.append(f"{source}:EPOCH_MISMATCH")
    declared = (
        ("source_revision", ("event_source_revision", "source_revision")),
        ("deployed_revision", ("event_deployed_revision", "deployed_revision")),
        ("tile_config_signature", ("event_config_signature", "tile_config_signature", "config_signature")),
    )
    for contract_field, aliases in declared:
        values = {
            str(row.get(alias)).strip()
            for alias in aliases if row.get(alias) not in (None, "")
        }
        if len(values) > 1:
            blockers.append(f"{source}:{contract_field.upper()}_DECLARATION_CONFLICT")
        elif values and next(iter(values)) != contract[contract_field]:
            blockers.append(f"{source}:{contract_field.upper()}_MISMATCH")
    return blockers


def select_current_rows(
    rows: Iterable[Mapping[str, Any]], contract: Mapping[str, str], *, source: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected, blockers = [], []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            blockers.append(f"{source}:ROW_NOT_OBJECT:{index}")
            continue
        defects = row_contract_blockers(row, contract, source=source)
        if defects:
            blockers.extend(defects)
            continue
        selected.append(dict(row))
    return selected, sorted(set(blockers))


def unique_exact_match(
    rows: Iterable[Mapping[str, Any]], expected: Mapping[str, Any], *, source: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    required = {key: str(value or "").strip() for key, value in expected.items()}
    if not required or any(not value for value in required.values()):
        return None, [f"{source}:JOIN_IDENTITY_INCOMPLETE"]
    matches = [
        dict(row) for row in rows
        if all(str(row.get(key) or "").strip() == value for key, value in required.items())
    ]
    if not matches:
        return None, [f"{source}:EXACT_JOIN_MISSING"]
    if len(matches) != 1:
        return None, [f"{source}:EXACT_JOIN_AMBIGUOUS"]
    return matches[0], []
