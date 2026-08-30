"""Explicit, fail-closed recovery of future paths from a retired Fly tape.

This offline tool is never imported by the collector.  Dry-run is the default;
``--apply`` is required to append immutable superseding evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from research_v3_contract import canonical_json
from research_v3_future_paths import (
    DEFAULT_REQUIRED_HORIZONS_SEC,
    MAX_TAPE_TOTAL_READ_BYTES,
    _bounded_tape_tail,
    _coverage,
)
from research_v3_store import V3EvidenceStore


RECOVERY_SOURCE = "ARCHIVED_FLY_MIRROR_RECOVERY"
RECOVERY_SELECTION_VERSION = "archived_fly_mirror_recovery_v2"
RECOVERY_LEDGER_RELATIVE = Path("v3/recovery_ledgers/market_segment.jsonl")


class _RecoveryOverlayStore(V3EvidenceStore):
    """Write derived recovery rows outside the Fly-owned raw ledger.

    A canonical mirror refresh atomically replaces Fly-owned ledger files.  A
    separate append-only overlay keeps locally verified recovery evidence from
    being mistaken for Fly runtime data or silently removed by that refresh.
    """

    def __init__(self, root: Path, *, epoch_id: str):
        super().__init__(root, epoch_id=epoch_id)
        self.ledger_dir = self.root / "v3" / "recovery_ledgers"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)


def _contained(root: Path, candidate: Path) -> Path:
    authority = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(authority)
    except ValueError as exc:
        raise ValueError("ARCHIVE_RECOVERY_PATH_OUTSIDE_CANONICAL_ROOT") from exc
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise ValueError(f"ARCHIVE_RECOVERY_TRUNCATED_LEDGER:{line_no}")
            value = json.loads(line)
            if isinstance(value, Mapping):
                rows.append(dict(value))
    return rows


def _verify_archive_authority(
    root: Path, tape: Path, *, expected_size: int, expected_sha256: str,
) -> dict[str, Any]:
    archive_root = _contained(root, root / "archive" / "sync-retired")
    tape = _contained(root, tape)
    try:
        tape.relative_to(archive_root)
    except ValueError as exc:
        raise ValueError("ARCHIVE_RECOVERY_SOURCE_NOT_SYNC_RETIRED") from exc
    if tape.is_symlink() or not tape.is_file():
        raise ValueError("ARCHIVE_RECOVERY_SOURCE_INVALID")
    receipt_path = _contained(root, Path(str(tape) + ".receipt.json"))
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError("ARCHIVE_RECOVERY_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if not isinstance(receipt, Mapping):
        raise ValueError("ARCHIVE_RECOVERY_RECEIPT_INVALID")
    if (
        receipt.get("schema") != "canonical_research_cleanup_receipt_v1"
        or receipt.get("recoverable") is not True
        or receipt.get("reason") != "ABSENT_FROM_AUTHENTICATED_FLY_MANIFEST"
    ):
        raise ValueError("ARCHIVE_RECOVERY_RECEIPT_NOT_RECOVERABLE")
    archive_relative = tape.relative_to(root).as_posix()
    if str(receipt.get("archive_relative") or "") != archive_relative:
        raise ValueError("ARCHIVE_RECOVERY_RECEIPT_ARCHIVE_PATH_MISMATCH")
    original_relative = str(receipt.get("source_relative") or "")
    if not original_relative or Path(original_relative).is_absolute() or ".." in Path(original_relative).parts:
        raise ValueError("ARCHIVE_RECOVERY_RECEIPT_SOURCE_PATH_INVALID")
    if Path(original_relative).name != tape.name:
        raise ValueError("ARCHIVE_RECOVERY_RECEIPT_SOURCE_PATH_MISMATCH")
    size = tape.stat().st_size
    if size != int(expected_size):
        raise ValueError("ARCHIVE_RECOVERY_SIZE_MISMATCH")
    expected_digest = str(expected_sha256).lower()
    if len(expected_digest) != 64 or any(c not in "0123456789abcdef" for c in expected_digest):
        raise ValueError("ARCHIVE_RECOVERY_EXPECTED_SHA256_INVALID")
    actual_digest = _sha256(tape)
    if actual_digest != expected_digest:
        raise ValueError("ARCHIVE_RECOVERY_SHA256_MISMATCH")
    if size > MAX_TAPE_TOTAL_READ_BYTES:
        raise ValueError("ARCHIVE_RECOVERY_SOURCE_EXCEEDS_BOUNDED_READ_CAP")
    return {
        "receipt_path": receipt_path.relative_to(root).as_posix(),
        "receipt": dict(receipt),
        "archive_relative": archive_relative,
        "original_relative": original_relative,
        "size_bytes": size,
        "sha256": actual_digest,
    }


def _latest_unknown_owners(root: Path, epoch_id: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    paths = (
        root / "v3" / "ledgers" / "market_segment.jsonl",
        root / RECOVERY_LEDGER_RELATIVE,
    )
    for candidate in paths:
        path = _contained(root, candidate)
        for row in _read_jsonl(path):
            if str(row.get("epoch_id") or "") != str(epoch_id):
                continue
            if str(row.get("segment_role") or "").upper() != "SIGNAL_TO_120M_FUTURE_PATH":
                continue
            owner = str(row.get("future_path_owner_key") or "")
            if owner:
                latest[owner] = row
    return [
        row for row in latest.values()
        if str(row.get("future_path_status") or "").upper() == "UNKNOWN"
        and row.get("requested_start_ts") is not None
        and row.get("requested_end_ts") is not None
    ]


def _opportunity_symbols(root: Path, epoch_id: str) -> dict[str, str]:
    path = _contained(root, root / "v3" / "ledgers" / "opportunity.jsonl")
    result: dict[str, str] = {}
    for row in _read_jsonl(path):
        if str(row.get("epoch_id") or "") != str(epoch_id):
            continue
        opportunity_id = str(row.get("record_id") or row.get("opportunity_id") or "")
        symbol = str(row.get("symbol") or "")
        if opportunity_id and symbol:
            result[opportunity_id] = symbol
    return result


def _atomic_write_new(path: Path, payload: bytes, *, root: Path) -> bool:
    target = _contained(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise ValueError("ARCHIVE_RECOVERY_STATUS_RECEIPT_CONFLICT")
        return False
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def recover_archived_future_paths(
    *, canonical_root: str | Path, archive_tape: str | Path, epoch_id: str,
    expected_size: int, expected_sha256: str, apply: bool = False,
) -> dict[str, Any]:
    root = Path(canonical_root).resolve()
    if root.name != "canonical-research-data":
        raise ValueError("ARCHIVE_RECOVERY_ROOT_NOT_CANONICAL")
    tape = Path(archive_tape)
    if not tape.is_absolute():
        tape = root / tape
    authority = _verify_archive_authority(
        root, tape, expected_size=expected_size, expected_sha256=expected_sha256,
    )
    status_receipt_path = root / "v3" / "receipts" / f"archive-future-path-recovery-v2-{authority['sha256'][:24]}.json"
    if apply and status_receipt_path.is_file():
        persisted = json.loads(status_receipt_path.read_text(encoding="utf-8-sig"))
        if (
            not isinstance(persisted, Mapping)
            or persisted.get("schema") != "archived_future_path_recovery_status_v2"
            or persisted.get("epoch_id") != str(epoch_id)
            or (persisted.get("archive") or {}).get("sha256") != authority["sha256"]
        ):
            raise ValueError("ARCHIVE_RECOVERY_STATUS_RECEIPT_CONFLICT")
        return {
            **dict(persisted),
            "status_receipt_written": False,
            "reapplication_noop": True,
        }
    tape_rows, read_receipt = _bounded_tape_tail(tape, max_bytes=int(expected_size))
    if int(read_receipt.get("bytes_read") or 0) > MAX_TAPE_TOTAL_READ_BYTES:
        raise ValueError("ARCHIVE_RECOVERY_BOUNDED_READ_CAP_EXCEEDED")
    owners = _latest_unknown_owners(root, str(epoch_id))
    symbols = _opportunity_symbols(root, str(epoch_id))
    plans: list[dict[str, Any]] = []
    complete_count = incomplete_count = ineligible_count = 0
    horizons = list(DEFAULT_REQUIRED_HORIZONS_SEC)
    for previous in owners:
        start = float(previous["requested_start_ts"])
        end = float(previous["requested_end_ts"])
        rows = [row for row in tape_rows if start <= float(row["ts"]) <= end]
        coverage = _coverage(rows, start, end)
        coverage.update({
            "parse_errors": int(read_receipt.get("parse_errors") or 0),
            "required_horizons_sec": horizons,
            "horizon_mature": True,
            "recovery_source": RECOVERY_SOURCE,
        })
        status = "COMPLETE" if coverage["requested_bounds_complete"] else "UNKNOWN"
        if status == "COMPLETE":
            complete_count += 1
            if coverage["conservative_bbo_depth_eligible"] is not True:
                ineligible_count += 1
        else:
            incomplete_count += 1
        owner = str(previous["future_path_owner_key"])
        opportunity_id = str(previous.get("opportunity_id") or "")
        plans.append({
            "owner": owner,
            "previous": previous,
            "rows": rows,
            "coverage": coverage,
            "status": status,
            "symbol": symbols.get(opportunity_id) or str(previous.get("symbol") or "BTCUSD"),
            "record_id": f"future-path-recovery:{owner}:{authority['sha256'][:24]}",
        })
    written = duplicate = 0
    if apply:
        store = _RecoveryOverlayStore(root, epoch_id=str(epoch_id))
        for plan in plans:
            previous = plan["previous"]
            segment_ref = None
            if plan["status"] == "COMPLETE":
                segment_ref = store.put_market_segment(
                    source=RECOVERY_SOURCE,
                    symbol=plan["symbol"], timeframe="1s",
                    start_ts=float(previous["requested_start_ts"]),
                    end_ts=float(previous["requested_end_ts"]), rows=plan["rows"],
                )
            result = store.append("market_segment", {
                "record_id": plan["record_id"],
                "episode_id": previous.get("episode_id"),
                "event_id": previous.get("event_id"),
                "opportunity_id": previous.get("opportunity_id"),
                "decision_id": previous.get("decision_id"),
                "shared_ai_call_id": previous.get("shared_ai_call_id"),
                "future_path_owner_key": plan["owner"],
                "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
                "requested_horizons_sec": horizons,
                "requested_start_ts": previous["requested_start_ts"],
                "requested_end_ts": previous["requested_end_ts"],
                "future_path_status": plan["status"],
                "unknown_reason": None if plan["status"] == "COMPLETE" else "ARCHIVED_SOURCE_REQUESTED_HORIZON_INCOMPLETE",
                "segment_ref": segment_ref,
                "coverage": plan["coverage"],
                "evidence_provenance": RECOVERY_SOURCE,
                "archive_recovery": authority,
                "selection_version": RECOVERY_SELECTION_VERSION,
                "supersedes_record_ids": [str(previous.get("record_id") or "")],
                "evidence_only": True,
            })
            written += int(result["written"])
            duplicate += int(result["duplicate"])
    status = {
        "schema": "archived_future_path_recovery_status_v2",
        "mode": "APPLY" if apply else "DRY_RUN",
        "epoch_id": str(epoch_id),
        "source": RECOVERY_SOURCE,
        "selection_version": RECOVERY_SELECTION_VERSION,
        "recovery_ledger_relative": RECOVERY_LEDGER_RELATIVE.as_posix(),
        "archive": authority,
        "bounded_read": read_receipt,
        "unknown_owner_candidates": len(owners),
        "complete_recovered_count": complete_count,
        "incomplete_unknown_count": incomplete_count,
        "conservative_ineligible_complete_count": ineligible_count,
        "conservative_eligible_complete_count": complete_count - ineligible_count,
        "append_written_count": written,
        "append_duplicate_count": duplicate,
        "planned_record_ids": sorted(plan["record_id"] for plan in plans),
    }
    if apply:
        status["status_receipt_relative"] = status_receipt_path.relative_to(root).as_posix()
        payload = canonical_json(status).encode("utf-8")
        status["status_receipt_written"] = _atomic_write_new(
            status_receipt_path, payload, root=root,
        )
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", required=True)
    parser.add_argument("--archive-tape", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--expected-size", required=True, type=int)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--apply", action="store_true", help="append recovery evidence; default is dry-run")
    args = parser.parse_args()
    result = recover_archived_future_paths(
        canonical_root=args.canonical_root, archive_tape=args.archive_tape,
        epoch_id=args.epoch_id, expected_size=args.expected_size,
        expected_sha256=args.expected_sha256, apply=args.apply,
    )
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
