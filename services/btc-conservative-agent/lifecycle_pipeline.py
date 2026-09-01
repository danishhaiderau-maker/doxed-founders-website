"""Bounded incremental qualification, completion, and bundle processing.

This module is deliberately independent of the trading loop.  It consumes the
durable offset/hash index maintained by :mod:`lifecycle_bundles`, evaluates
only dirty composite lifecycle identities, and never infers missing evidence.

Completion is intentionally two-pass.  The first pass appends an idempotent
completion receipt and leaves the identity dirty.  A later pass indexes that
append, verifies it from the source ledger, and only then materializes the
content-addressed bundle.  A crash at any boundary therefore cannot publish a
bundle that omits its completion receipt.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from lifecycle_bundles import (
    DEFAULT_MAX_SCAN_BYTES,
    DEFAULT_MAX_SCAN_ROWS,
    LEDGER_NAMES,
    LifecycleKey,
    _clear_dirty,
    _dirty_lifecycle_rows,
    _exclusive_index_lock,
    _index_ledger_chunk,
    _open_incremental_index,
    materialize_bundle,
    materialize_transfer_bundle,
)
from lifecycle_completion_reconciler import (
    evaluate_lifecycle_completion,
    evaluate_lifecycle_transfer_ready,
)
from qualification_horizon_index import (
    DEFAULT_MAX_INDEX_BYTES as DEFAULT_MAX_TAPE_INDEX_BYTES,
    DEFAULT_MAX_INDEX_ROWS as DEFAULT_MAX_TAPE_INDEX_ROWS,
    DEFAULT_MAX_QUERY_BYTES as DEFAULT_MAX_TAPE_QUERY_BYTES,
    DEFAULT_MAX_QUERY_ROWS as DEFAULT_MAX_TAPE_QUERY_ROWS,
    TapeOffsetIndex,
    produce_post_exit_path,
)
from research_v3_store import V3EvidenceStore, _collection_provenance


PIPELINE_SCHEMA = "lifecycle_pipeline_result_v1"
DEFAULT_MAX_LIFECYCLES = 5
DEFAULT_MAX_LIFECYCLE_ROWS = 10_000
DEFAULT_MAX_LIFECYCLE_BYTES = 8 * 1024 * 1024
MAX_LIFECYCLES = 25
MAX_LIFECYCLE_ROWS = 100_000
MAX_LIFECYCLE_BYTES = 64 * 1024 * 1024
_PROVENANCE_FIELDS = ("source_revision", "deployed_revision", "tile_config_signature")
QUALIFICATION_RETRY_SEC = 60.0
QUALIFICATION_HORIZON_SEC = 7200.0


def _ensure_retry_queue(connection) -> None:
    with connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS qualification_retry (
                collection_epoch_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                policy_signature TEXT NOT NULL,
                research_lane TEXT NOT NULL,
                retry_at REAL NOT NULL,
                reason TEXT NOT NULL,
                updated_unix REAL NOT NULL,
                PRIMARY KEY (
                    collection_epoch_id, episode_id,
                    policy_signature, research_lane
                )
            )
        """)


def _enqueue_retry(connection, key: LifecycleKey, *, retry_at: float,
                   reason: str, now: float) -> None:
    with connection:
        connection.execute("""
            INSERT INTO qualification_retry VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_epoch_id, episode_id, policy_signature, research_lane)
            DO UPDATE SET retry_at=excluded.retry_at, reason=excluded.reason,
                          updated_unix=excluded.updated_unix
        """, (*key.as_dict().values(), float(retry_at), str(reason), float(now)))


def _remove_retry(connection, key: LifecycleKey) -> None:
    with connection:
        connection.execute("""
            DELETE FROM qualification_retry
            WHERE collection_epoch_id=? AND episode_id=?
              AND policy_signature=? AND research_lane=?
        """, tuple(key.as_dict().values()))


def _promote_due_retries(connection, *, now: float, maximum: int) -> int:
    due = connection.execute("""
        SELECT collection_epoch_id, episode_id, policy_signature, research_lane
        FROM qualification_retry WHERE retry_at <= ?
        ORDER BY retry_at LIMIT ?
    """, (float(now), max(1, int(maximum)))).fetchall()
    with connection:
        for row in due:
            connection.execute(
                "INSERT OR IGNORE INTO dirty_lifecycle VALUES (?, ?, ?, ?)", tuple(row)
            )
    return len(due)


def _post_observation_present(rows: list[dict[str, Any]]) -> bool:
    return any(
        str(row.get("context_role") or row.get("segment_role") or "").upper()
        == "POST_EXIT_PATH"
        for row in rows
    )


def _terminal_candidate(key: LifecycleKey, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal_rows = [row for row in rows if row.get("terminal") is True]
    if not terminal_rows:
        return None
    event_ids = {str(row.get("event_id") or row.get("trade_id") or "") for row in rows}
    event_ids.discard("")
    if len(event_ids) != 1:
        return None
    terminal_ts = None
    for row in rows:
        schedule = row.get("chase_schedule") if isinstance(row.get("chase_schedule"), Mapping) else {}
        for value in (
            row.get("close_ts"), row.get("terminal_ts_exact"), row.get("terminal_ts"),
            schedule.get("terminal_ts_exact"), schedule.get("terminal_ts"),
        ):
            try:
                candidate = float(value)
            except (TypeError, ValueError, OverflowError):
                try:
                    text = str(value or "").strip()
                    parsed = datetime.fromisoformat(
                        text[:-1] + "+00:00" if text.endswith("Z") else text
                    )
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    candidate = parsed.timestamp()
                except (TypeError, ValueError, OverflowError):
                    continue
            if candidate > 0:
                terminal_ts = candidate if terminal_ts is None else max(terminal_ts, candidate)
    if terminal_ts is None:
        return None
    exemplar = terminal_rows[-1]
    return {
        "terminal": True, "terminal_ts": terminal_ts,
        "event_id": next(iter(event_ids)), "episode_id": key.episode_id,
        "policy_signature": key.policy_signature, "research_lane": key.research_lane,
        "policy_epoch_id": exemplar.get("policy_epoch_id"),
        "opportunity_id": exemplar.get("opportunity_id"),
        "shared_ai_call_id": exemplar.get("shared_ai_call_id"),
        "symbol": exemplar.get("symbol") or "UNKNOWN",
    }


def _completion_present(rows: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(row.get("bundle_completion"), Mapping)
        and row["bundle_completion"].get("schema") == "lifecycle_bundle_completion_v1"
        for row in rows
    )


def _completion_row(
    key: LifecycleKey, assessment: Mapping[str, Any]
) -> dict[str, Any]:
    receipt = assessment["receipt"]
    event_id = str(assessment.get("event_id") or "")
    if not event_id or not isinstance(receipt, Mapping):
        raise ValueError("READY_COMPLETION_IDENTITY_MISSING")
    return {
        "record_id": (
            f"lifecycle:{event_id}:bundle-completion:"
            f"{receipt['completion_receipt_sha256'][:16]}"
        ),
        "event_id": event_id,
        "episode_id": key.episode_id,
        "policy_signature": key.policy_signature,
        "research_lane": key.research_lane,
        "terminal": True,
        "observation_status": "LIFECYCLE_BUNDLE_COMPLETE",
        "outcome_state": receipt["entry_outcome"],
        "bundle_completion": dict(receipt),
        **dict(assessment["provenance"]),
    }


def _runtime_provenance_blockers(assessment: Mapping[str, Any]) -> list[str]:
    runtime = _collection_provenance()
    proven = assessment.get("provenance") or {}
    return [
        f"RUNTIME_{field.upper()}_MISMATCH"
        for field in _PROVENANCE_FIELDS
        if str(proven.get(field) or "") != str(runtime.get(field) or "")
    ]


def process_incremental_lifecycle_pipeline(
    root: str | Path,
    *,
    now: float | None = None,
    max_lifecycles: int = DEFAULT_MAX_LIFECYCLES,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
    max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
    max_lifecycle_rows: int = DEFAULT_MAX_LIFECYCLE_ROWS,
    max_lifecycle_bytes: int = DEFAULT_MAX_LIFECYCLE_BYTES,
    max_tape_index_bytes: int = DEFAULT_MAX_TAPE_INDEX_BYTES,
    max_tape_index_rows: int = DEFAULT_MAX_TAPE_INDEX_ROWS,
    max_tape_query_bytes: int = DEFAULT_MAX_TAPE_QUERY_BYTES,
    max_tape_query_rows: int = DEFAULT_MAX_TAPE_QUERY_ROWS,
    max_runtime_sec: float = 60.0,
    pressure_mode: bool = False,
) -> dict[str, Any]:
    """Process a bounded batch using the existing restart-safe index.

    ``pressure_mode`` is a caller-supplied fail-safe.  It clamps the batch to
    one lifecycle and one MiB of new ledger input. This function never
    authorizes source cleanup. It creates qualification evidence only from the
    independently indexed, exact, gap-free post-terminal tape window; any
    incomplete or corrupt interval remains UNKNOWN.
    """
    root = Path(root).resolve()
    current = time.time() if now is None else float(now)
    lifecycle_limit = max(1, min(int(max_lifecycles), MAX_LIFECYCLES))
    scan_byte_limit = max(1, min(int(max_scan_bytes), 64 * 1024 * 1024))
    scan_row_limit = max(1, min(int(max_scan_rows), 100_000))
    lifecycle_row_limit = max(1, min(int(max_lifecycle_rows), MAX_LIFECYCLE_ROWS))
    lifecycle_byte_limit = max(1, min(int(max_lifecycle_bytes), MAX_LIFECYCLE_BYTES))
    runtime_limit = max(1.0, min(float(max_runtime_sec), 300.0))
    if pressure_mode:
        lifecycle_limit = 1
        scan_byte_limit = min(scan_byte_limit, 1024 * 1024)
        scan_row_limit = min(scan_row_limit, 2_000)
        lifecycle_row_limit = min(lifecycle_row_limit, 2_000)
        lifecycle_byte_limit = min(lifecycle_byte_limit, 2 * 1024 * 1024)

    started = time.monotonic()
    tape_index_byte_limit = max(1, min(int(max_tape_index_bytes), 16 * 1024 * 1024))
    tape_index_row_limit = max(1, min(int(max_tape_index_rows), 100_000))
    tape_query_byte_limit = max(1, min(int(max_tape_query_bytes), 16 * 1024 * 1024))
    tape_query_row_limit = max(1, min(int(max_tape_query_rows), 10_000))
    if pressure_mode:
        tape_index_byte_limit = min(tape_index_byte_limit, 256 * 1024)
        tape_index_row_limit = min(tape_index_row_limit, 1_000)
        tape_query_byte_limit = min(tape_query_byte_limit, 2 * 1024 * 1024)
        tape_query_row_limit = min(tape_query_row_limit, 7_500)
    tape_index = TapeOffsetIndex(root)
    tape_refresh = tape_index.refresh(
        max_bytes=tape_index_byte_limit, max_rows=tape_index_row_limit,
        max_runtime_sec=min(5.0, max(0.1, runtime_limit / 4.0)),
    )
    remaining_bytes = scan_byte_limit
    remaining_rows = scan_row_limit
    scan_receipts: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    pending_dirty = 0
    with _exclusive_index_lock(root):
        connection = _open_incremental_index(root)
        try:
            _ensure_retry_queue(connection)
            promoted_retries = _promote_due_retries(
                connection, now=current, maximum=lifecycle_limit,
            )
            ledger_dir = root / "v3" / "ledgers"
            next_ledger = int(connection.execute(
                "SELECT next_ledger FROM index_meta WHERE singleton = 1"
            ).fetchone()[0]) % len(LEDGER_NAMES)
            order = tuple(LEDGER_NAMES[next_ledger:]) + tuple(LEDGER_NAMES[:next_ledger])
            for ledger in order:
                if time.monotonic() - started >= runtime_limit:
                    break
                path = ledger_dir / f"{ledger}.jsonl"
                if not path.exists() or remaining_bytes <= 0 or remaining_rows <= 0:
                    continue
                receipt = _index_ledger_chunk(
                    connection,
                    path,
                    ledger,
                    max_bytes=remaining_bytes,
                    max_rows=remaining_rows,
                )
                scan_receipts[ledger] = receipt
                remaining_bytes -= int(receipt["bytes_indexed"])
                remaining_rows -= int(receipt["rows_scanned"])
                with connection:
                    connection.execute(
                        "UPDATE index_meta SET next_ledger = ? WHERE singleton = 1",
                        ((LEDGER_NAMES.index(ledger) + 1) % len(LEDGER_NAMES),),
                    )

            dirty = _dirty_lifecycle_rows(
                connection,
                root,
                maximum=lifecycle_limit,
                max_events_per_lifecycle=lifecycle_row_limit,
                max_bytes_per_lifecycle=lifecycle_byte_limit,
            )
            for key, rows in dirty:
                if time.monotonic() - started >= runtime_limit:
                    break
                transfer = evaluate_lifecycle_transfer_ready(
                    key, rows, now=current,
                    lifecycle_horizon_sec=QUALIFICATION_HORIZON_SEC,
                )
                assessment = evaluate_lifecycle_completion(
                    key, rows, now=current,
                    lifecycle_horizon_sec=QUALIFICATION_HORIZON_SEC,
                )
                item: dict[str, Any] = {
                    "lifecycle_identity_id": key.identity_id,
                    "identity": key.as_dict(),
                    "qualification_ready": assessment["ready"],
                    "classification": assessment["classification"],
                    "blockers": list(assessment["blockers"]),
                    "completion_appended": False,
                    "completion_duplicate": False,
                    "bundle_written_or_verified": False,
                    "transfer_ready": transfer["ready"],
                    "transfer_blockers": list(transfer["blockers"]),
                    "transfer_bundle_written_or_verified": False,
                    "transfer_stage": "TRANSFER_INCOMPLETE",
                    "stage": "QUALIFICATION_INCOMPLETE",
                }
                if transfer["ready"]:
                    transfer_bundle = materialize_transfer_bundle(
                        root, key, rows, transfer,
                    )
                    item["transfer_bundle_written_or_verified"] = bool(
                        transfer_bundle.get("written")
                        or transfer_bundle.get("duplicate")
                    )
                    item["transfer_bundle"] = transfer_bundle
                    item["transfer_stage"] = (
                        "TRANSFER_BUNDLE_MATERIALIZED_OR_VERIFIED"
                        if item["transfer_bundle_written_or_verified"]
                        else "TRANSFER_NOT_MATERIALIZABLE"
                    )
                if not assessment["ready"]:
                    if not _post_observation_present(rows):
                        candidate = _terminal_candidate(key, rows)
                        if candidate is not None:
                            mature_at = float(candidate["terminal_ts"]) + QUALIFICATION_HORIZON_SEC
                            if current < mature_at:
                                _enqueue_retry(
                                    connection, key, retry_at=mature_at,
                                    reason="QUALIFICATION_HORIZON_NOT_MATURE", now=current,
                                )
                                item["stage"] = "QUALIFICATION_DEFERRED_UNTIL_MATURITY"
                                item["retry_at"] = mature_at
                                _clear_dirty(connection, key)
                                results.append(item)
                                continue
                            horizon = produce_post_exit_path(
                                candidate, data_dir=root,
                                epoch_id=key.collection_epoch_id, now_ts=current,
                                horizon_sec=QUALIFICATION_HORIZON_SEC,
                                index=tape_index,
                                max_query_bytes=tape_query_byte_limit,
                                max_query_rows=tape_query_row_limit,
                                max_runtime_sec=min(5.0, max(0.1, runtime_limit / 4.0)),
                            )
                            item["qualification_horizon"] = horizon
                            if horizon.get("write", {}).get("written") or horizon.get("write", {}).get("duplicate"):
                                _remove_retry(connection, key)
                                item["stage"] = "QUALIFICATION_HORIZON_PENDING_REINDEX"
                                # Keep dirty: a second pass must index and hash-
                                # verify the new POST_EXIT_PATH before completion.
                                results.append(item)
                                continue
                            retry_at = current + QUALIFICATION_RETRY_SEC
                            _enqueue_retry(
                                connection, key, retry_at=retry_at,
                                reason="POST_OBSERVATION_INCOMPLETE", now=current,
                            )
                            item["stage"] = "QUALIFICATION_EVIDENCE_RETRY_QUEUED"
                            item["retry_at"] = retry_at
                            item["blockers"] = list(
                                (horizon.get("post_observation") or {}).get("blockers") or item["blockers"]
                            )
                            _clear_dirty(connection, key)
                            results.append(item)
                            continue
                    # Evaluation was complete for this exact indexed event set.
                    # Any later horizon/terminal append marks it dirty again.
                    _clear_dirty(connection, key)
                    results.append(item)
                    continue

                provenance_blockers = _runtime_provenance_blockers(assessment)
                if provenance_blockers:
                    item["blockers"] = sorted(set(item["blockers"] + provenance_blockers))
                    item["stage"] = "RUNTIME_PROVENANCE_MISMATCH"
                    # Keep the identity dirty. Runtime provenance can change at
                    # deploy/epoch boundaries without another lifecycle append;
                    # clearing it here would strand otherwise complete evidence
                    # until an unrelated future row happened to re-dirty it.
                    results.append(item)
                    continue

                if not _completion_present(rows):
                    _remove_retry(connection, key)
                    store = V3EvidenceStore(root, epoch_id=key.collection_epoch_id)
                    write = store.append("lifecycle", _completion_row(key, assessment))
                    item["completion_appended"] = write.get("written") is True
                    item["completion_duplicate"] = write.get("duplicate") is True
                    item["completion_record_id"] = write.get("record_id")
                    item["stage"] = "COMPLETION_PENDING_REINDEX"
                    # Deliberately retain dirty.  The next invocation must index
                    # and hash-verify the append before bundle publication.
                    results.append(item)
                    continue

                bundle = materialize_bundle(
                    root,
                    key,
                    rows,
                    now=current,
                    lifecycle_horizon_sec=QUALIFICATION_HORIZON_SEC,
                )
                item["bundle_written_or_verified"] = bool(
                    bundle.get("written") or bundle.get("duplicate")
                )
                item["bundle"] = bundle
                item["stage"] = (
                    "BUNDLE_MATERIALIZED_OR_VERIFIED"
                    if item["bundle_written_or_verified"]
                    else "COMPLETION_NOT_MATERIALIZABLE"
                )
                item["blockers"] = list((bundle.get("completion") or {}).get("blockers") or [])
                _clear_dirty(connection, key)
                results.append(item)
        finally:
            pending_dirty = int(connection.execute(
                "SELECT COUNT(*) FROM dirty_lifecycle"
            ).fetchone()[0])
            connection.close()

    return {
        "schema": PIPELINE_SCHEMA,
        "generated_unix": time.time(),
        "pressure_mode": bool(pressure_mode),
        "candidate_count": len(results),
        "completion_appended_count": sum(item["completion_appended"] for item in results),
        "bundle_count": sum(item["bundle_written_or_verified"] for item in results),
        "transfer_ready_count": sum(item["transfer_ready"] for item in results),
        "transfer_bundle_count": sum(
            item["transfer_bundle_written_or_verified"] for item in results
        ),
        "results": results,
        "scan": {
            "ledgers": scan_receipts,
            "bytes_indexed": scan_byte_limit - remaining_bytes,
            "rows_scanned": scan_row_limit - remaining_rows,
            "byte_limit": scan_byte_limit,
            "row_limit": scan_row_limit,
            "lifecycle_limit": lifecycle_limit,
            "max_lifecycle_rows": lifecycle_row_limit,
            "max_lifecycle_bytes": lifecycle_byte_limit,
            "runtime_limit_sec": runtime_limit,
            "elapsed_sec": time.monotonic() - started,
            "pending_dirty_lifecycles": pending_dirty,
            "promoted_qualification_retries": promoted_retries,
            "tape_index": tape_refresh,
            "tape_index_byte_limit": tape_index_byte_limit,
            "tape_index_row_limit": tape_index_row_limit,
            "tape_query_byte_limit": tape_query_byte_limit,
            "tape_query_row_limit": tape_query_row_limit,
            "bounded": True,
        },
        "source_cleanup_authorized": False,
    }
