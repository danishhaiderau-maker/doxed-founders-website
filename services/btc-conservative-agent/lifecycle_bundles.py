"""Immutable, fail-closed Research V3 lifecycle bundle materialization.

Raw V3 ledgers are shared append streams.  They are never removed by this
module.  Instead, complete lifecycle evidence is duplicated into a
content-addressed bundle that can be transferred and acknowledged safely.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research_v3_contract import LEDGER_NAMES, canonical_json


BUNDLE_SCHEMA = "research_lifecycle_bundle_v1"
COMPLETION_SCHEMA = "lifecycle_bundle_completion_v1"
ENTRY_OUTCOMES = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})


def _present(value: Any) -> bool:
    return value not in (None, "", "UNKNOWN")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_iso(timestamp: Any) -> str | None:
    try:
        value = float(timestamp)
        if value <= 0:
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, order=True)
class LifecycleKey:
    collection_epoch_id: str
    episode_id: str
    policy_signature: str
    research_lane: str

    def as_dict(self) -> dict[str, str]:
        return {
            "collection_epoch_id": self.collection_epoch_id,
            "episode_id": self.episode_id,
            "policy_signature": self.policy_signature,
            "research_lane": self.research_lane,
        }

    @property
    def identity_id(self) -> str:
        digest = hashlib.sha256(canonical_json(self.as_dict()).encode("utf-8")).hexdigest()
        return f"lifecycle-identity-{digest}"


def lifecycle_key(row: dict[str, Any]) -> LifecycleKey:
    """Return the composite lifecycle identity; never guess missing fields."""
    values = {
        "collection_epoch_id": row.get("collection_epoch_id") or row.get("epoch_id"),
        "episode_id": row.get("episode_id"),
        "policy_signature": row.get("policy_signature"),
        "research_lane": row.get("research_lane"),
    }
    missing = sorted(name for name, value in values.items() if not _present(value))
    if missing:
        raise ValueError("LIFECYCLE_IDENTITY_INCOMPLETE:" + ",".join(missing))
    return LifecycleKey(
        str(values["collection_epoch_id"]),
        str(values["episode_id"]),
        str(values["policy_signature"]),
        str(values["research_lane"]).upper(),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise ValueError(f"TRUNCATED_JSONL_LINE:{path.name}:{line_no}")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"NON_OBJECT_JSONL_ROW:{path.name}:{line_no}")
            rows.append(row)
    return rows


def collect_lifecycle_rows(root: str | Path) -> dict[LifecycleKey, list[dict[str, Any]]]:
    """Join only exact composite identities from the shared V3 ledgers.

    Sparse rows are intentionally not attached, even if episode_id happens to
    match.  Guessing across multiple policy/lane lifecycles would corrupt the
    very evidence boundary this bundle is intended to prove.
    """
    ledger_dir = Path(root).resolve() / "v3" / "ledgers"
    grouped: dict[LifecycleKey, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[LifecycleKey, str, str]] = set()
    for ledger in LEDGER_NAMES:
        for row in _read_jsonl(ledger_dir / f"{ledger}.jsonl"):
            try:
                key = lifecycle_key(row)
            except ValueError:
                continue
            material = dict(row)
            material.setdefault("ledger", ledger)
            identity = (key, str(material.get("ledger") or ledger), str(material.get("record_id") or ""))
            if identity in seen:
                raise ValueError(f"DUPLICATE_LIFECYCLE_RECORD:{identity[1]}:{identity[2]}")
            seen.add(identity)
            grouped[key].append(material)
    for rows in grouped.values():
        rows.sort(key=lambda row: (
            str(row.get("ledger") or ""),
            float(row.get("observed_ts") or row.get("ts") or row.get("signal_ts") or 0.0),
            str(row.get("record_id") or row.get("event_id") or ""),
        ))
    return dict(grouped)


def classify_completion(
    rows: Iterable[dict[str, Any]], *, now: float | None = None,
    lifecycle_horizon_sec: float = 7200.0, reconciliation_allowance_sec: float = 180.0,
) -> dict[str, Any]:
    """Require an explicit, cost-aware completion receipt.

    Historical terminal labels alone are insufficient: they do not prove that
    post-entry/exit paths, costs, or the observation horizon were captured.
    """
    now = time.time() if now is None else float(now)
    candidates = []
    for row in rows:
        receipt = row.get("bundle_completion")
        if isinstance(receipt, dict) and receipt.get("schema") == COMPLETION_SCHEMA:
            candidates.append((float(receipt.get("terminal_ts") or 0.0), receipt))
    if not candidates:
        return {"ready": False, "classification": "UNKNOWN", "blockers": ["COMPLETION_RECEIPT_MISSING"]}
    _, receipt = max(candidates, key=lambda item: item[0])
    outcome = str(receipt.get("entry_outcome") or "").upper()
    blockers: list[str] = []
    if outcome not in ENTRY_OUTCOMES:
        blockers.append("ENTRY_OUTCOME_INVALID")
    terminal_ts = float(receipt.get("terminal_ts") or 0.0)
    horizon_complete_ts = float(receipt.get("horizon_complete_ts") or 0.0)
    minimum_horizon = terminal_ts + max(0.0, float(lifecycle_horizon_sec))
    if receipt.get("terminal") is not True:
        blockers.append("LIFECYCLE_NOT_TERMINAL")
    if receipt.get("entry_schedule_terminal") is not True:
        blockers.append("ENTRY_SCHEDULE_NOT_TERMINAL")
    if receipt.get("position_closed_or_never_opened") is not True:
        blockers.append("POSITION_NOT_CLOSED")
    if receipt.get("post_observation_complete") is not True:
        blockers.append("POST_OBSERVATION_INCOMPLETE")
    if terminal_ts <= 0:
        blockers.append("TERMINAL_TIMESTAMP_MISSING")
    if horizon_complete_ts < minimum_horizon:
        blockers.append("LIFECYCLE_HORIZON_INCOMPLETE")
    if now < horizon_complete_ts + max(0.0, float(reconciliation_allowance_sec)):
        blockers.append("RECONCILIATION_ALLOWANCE_ACTIVE")
    if outcome in {"FULL_FILL", "PARTIAL_FILL"}:
        for field, blocker in (
            ("exit_evidence_complete", "EXIT_EVIDENCE_INCOMPLETE"),
            ("costs_complete", "COST_EVIDENCE_INCOMPLETE"),
            ("mfe_mae_complete", "MFE_MAE_INCOMPLETE"),
            ("net_pnl_reconciled", "NET_PNL_UNRECONCILED"),
        ):
            if receipt.get(field) is not True:
                blockers.append(blocker)
    if outcome == "UNKNOWN" and receipt.get("unknown_reason") in (None, ""):
        blockers.append("UNKNOWN_REASON_MISSING")
    return {
        "ready": not blockers,
        "classification": outcome if outcome in ENTRY_OUTCOMES else "UNKNOWN",
        "terminal_ts": terminal_ts or None,
        "horizon_complete_ts": horizon_complete_ts or None,
        "blockers": sorted(set(blockers)),
    }


def _referenced_market_segments(root: Path, rows: Iterable[dict[str, Any]]) -> list[Path]:
    paths: set[Path] = set()
    for row in rows:
        for field, value in row.items():
            if not (str(field).endswith("segment_refs") and isinstance(value, list)):
                continue
            for ref in value:
                if not isinstance(ref, dict):
                    continue
                relative = str(ref.get("relative_path") or "")
                if not relative:
                    continue
                candidate = (root / relative).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise ValueError("MARKET_SEGMENT_PATH_OUTSIDE_ROOT") from exc
                if not candidate.is_file():
                    raise ValueError(f"MARKET_SEGMENT_MISSING:{relative}")
                expected = str(ref.get("sha256") or "").lower()
                if len(expected) != 64 or _sha256_file(candidate) != expected:
                    raise ValueError(f"MARKET_SEGMENT_SHA256_MISMATCH:{relative}")
                paths.add(candidate)
    return sorted(paths)


def _file_receipt(
    path: Path, relative: str, *, role: str, row_count: int,
    first_timestamp: str, last_timestamp: str,
) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": relative,
        "role": role,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": _sha256_file(path),
        "row_count": int(row_count),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
    }


def _cleanup_manifest_sha256(files: Iterable[dict[str, Any]]) -> str:
    canonical = [{
        "path": str(row["path"]),
        "sha256": str(row["sha256"]).lower(),
        "size": int(row["size"]),
        "mtime_ns": int(row["mtime_ns"]),
        "row_count": int(row["row_count"]),
        "first_timestamp": str(row["first_timestamp"]),
        "last_timestamp": str(row["last_timestamp"]),
    } for row in sorted(files, key=lambda item: str(item["path"]))]
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def _consistent_provenance(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    fields = ("source_revision", "deployed_revision", "tile_config_signature")
    material = list(rows)
    result: dict[str, str] = {}
    for field in fields:
        values = {str(row.get(field) or "").strip() for row in material}
        if len(values) != 1 or not all(_present(row.get(field)) for row in material):
            raise ValueError(f"LIFECYCLE_PROVENANCE_NOT_UNIQUE:{field}")
        result[field] = values.pop()
    return result


def _bundle_content_id(
    key: LifecycleKey, rows: Iterable[dict[str, Any]], completion: dict[str, Any],
    segments: Iterable[Path],
) -> str:
    material = {
        "identity": key.as_dict(),
        "completion": completion,
        "events_sha256": hashlib.sha256(
            "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")
        ).hexdigest(),
        "market_segments": [
            {"name": path.name, "sha256": _sha256_file(path)} for path in segments
        ],
    }
    return "lifecycle-" + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def materialize_bundle(
    root: str | Path, key: LifecycleKey, rows: Iterable[dict[str, Any]], *, now: float | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    frozen = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("ledger") or ""),
            float(row.get("observed_ts") or row.get("ts") or row.get("signal_ts") or 0.0),
            str(row.get("record_id") or row.get("event_id") or ""),
        ),
    )
    completion = classify_completion(frozen, now=now)
    if not completion["ready"]:
        return {"written": False, "lifecycle_identity_id": key.identity_id, "completion": completion}
    segments = _referenced_market_segments(root, frozen)
    bundle_id = _bundle_content_id(key, frozen, completion, segments)
    bundle_root = root / "v3" / "lifecycle_bundles"
    target = bundle_root / bundle_id[-64:-62] / bundle_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verification = verify_bundle(target)
        if not verification["passed"]:
            raise ValueError("EXISTING_LIFECYCLE_BUNDLE_INVALID")
        return {"written": False, "duplicate": True, "bundle_id": bundle_id, "path": str(target), "manifest": verification["manifest"]}
    staging_root = bundle_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    # Keep transient paths short enough for Windows legacy MAX_PATH while the
    # final directory retains the complete content-bound lifecycle identity.
    temporary = staging_root / f"{os.getpid()}-{uuid.uuid4().hex[:16]}"
    try:
        temporary.mkdir()
        event_path = temporary / "events.jsonl"
        with event_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in frozen:
                handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        timestamps = [
            float(value) for row in frozen
            for value in (row.get("observed_ts") or row.get("ts") or row.get("signal_ts"),)
            if _utc_iso(value)
        ]
        if not timestamps:
            raise ValueError("LIFECYCLE_EVENT_TIMESTAMPS_MISSING")
        receipts = [_file_receipt(
            event_path, "events.jsonl", role="LIFECYCLE_EVENTS",
            row_count=len(frozen), first_timestamp=_utc_iso(min(timestamps)),
            last_timestamp=_utc_iso(max(timestamps)),
        )]
        for source in segments:
            relative = source.relative_to(root).as_posix()
            destination = temporary / "market_segments" / source.stem[:2] / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256_file(destination) != _sha256_file(source):
                raise ValueError(f"MARKET_SEGMENT_COPY_SHA256_MISMATCH:{source.name}")
            envelope = json.loads(destination.read_text(encoding="utf-8"))
            segment_rows = envelope.get("rows") if isinstance(envelope, dict) else None
            start_iso = _utc_iso(envelope.get("start_ts") if isinstance(envelope, dict) else None)
            end_iso = _utc_iso(envelope.get("end_ts") if isinstance(envelope, dict) else None)
            if not isinstance(segment_rows, list) or not start_iso or not end_iso:
                raise ValueError(f"MARKET_SEGMENT_METADATA_INCOMPLETE:{source.name}")
            receipts.append(_file_receipt(
                destination, f"market_segments/{source.stem[:2]}/{source.name}", role="MARKET_SEGMENT",
                row_count=len(segment_rows), first_timestamp=start_iso,
                last_timestamp=end_iso,
            ))
            receipts[-1]["source_relative_path"] = relative
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "bundle_id": bundle_id,
            "lifecycle_identity_id": key.identity_id,
            "lifecycle_id": "|".join((key.episode_id, key.policy_signature, key.research_lane)),
            "identity": key.as_dict(),
            "provenance": _consistent_provenance(frozen),
            "completion": completion,
            "files": sorted(receipts, key=lambda row: row["path"]),
            "source_cleanup_authorized": False,
        }
        manifest["cleanup_manifest_sha256"] = _cleanup_manifest_sha256(manifest["files"])
        manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
        manifest_path = temporary / "manifest.json"
        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(manifest) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(temporary)
        os.replace(temporary, target)
        _fsync_dir(target.parent)
        verification = verify_bundle(target)
        if not verification["passed"]:
            raise ValueError("PUBLISHED_LIFECYCLE_BUNDLE_INVALID")
        return {"written": True, "duplicate": False, "bundle_id": bundle_id, "path": str(target), "manifest": verification["manifest"]}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def verify_bundle(bundle_path: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_path).resolve()
    manifest_path = bundle / "manifest.json"
    defects: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "defects": [f"MANIFEST_INVALID:{type(exc).__name__}"], "manifest": None}
    supplied_sha = str(manifest.pop("manifest_sha256", ""))
    actual_sha = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    manifest["manifest_sha256"] = supplied_sha
    if supplied_sha != actual_sha:
        defects.append("MANIFEST_SHA256_MISMATCH")
    try:
        key = LifecycleKey(**manifest["identity"])
        events = _read_jsonl(bundle / "events.jsonl")
        segment_paths = sorted((bundle / "market_segments").glob("*/*.json"))
        expected_id = _bundle_content_id(key, events, manifest.get("completion") or {}, segment_paths)
        if (
            manifest.get("bundle_id") != expected_id or bundle.name != expected_id
            or manifest.get("lifecycle_identity_id") != key.identity_id
        ):
            defects.append("BUNDLE_IDENTITY_MISMATCH")
    except (KeyError, TypeError, ValueError):
        defects.append("BUNDLE_IDENTITY_INVALID")
    for receipt in manifest.get("files") or []:
        relative = str(receipt.get("path") or "")
        candidate = (bundle / relative).resolve()
        try:
            candidate.relative_to(bundle)
        except ValueError:
            defects.append(f"FILE_PATH_OUTSIDE_BUNDLE:{relative}")
            continue
        if not candidate.is_file():
            defects.append(f"FILE_MISSING:{relative}")
            continue
        if candidate.stat().st_size != int(receipt.get("size") or -1):
            defects.append(f"FILE_SIZE_MISMATCH:{relative}")
        if _sha256_file(candidate) != str(receipt.get("sha256") or ""):
            defects.append(f"FILE_SHA256_MISMATCH:{relative}")
        if receipt.get("row_count") is not None:
            if receipt.get("role") == "MARKET_SEGMENT":
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                    rows = len(payload.get("rows")) if isinstance(payload.get("rows"), list) else -1
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    rows = -1
            else:
                with candidate.open("rb") as handle:
                    rows = sum(1 for line in handle if line.endswith(b"\n"))
            if rows != int(receipt["row_count"]):
                defects.append(f"FILE_ROW_COUNT_MISMATCH:{relative}")
    return {"passed": not defects, "defects": sorted(set(defects)), "manifest": manifest}


def materialize_ready_bundles(root: str | Path, *, now: float | None = None, max_bundles: int = 25) -> dict[str, Any]:
    results = []
    grouped = collect_lifecycle_rows(root)
    for key in sorted(grouped):
        result = materialize_bundle(root, key, grouped[key], now=now)
        if result.get("written") or result.get("duplicate"):
            results.append(result)
        if len(results) >= max(1, min(int(max_bundles), 100)):
            break
    return {
        "schema": "lifecycle_bundle_materialization_result_v1",
        "candidate_count": len(grouped),
        "materialized_or_verified": len(results),
        "bundles": results,
        "source_cleanup_authorized": False,
    }
