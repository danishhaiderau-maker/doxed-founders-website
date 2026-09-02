import json
import hashlib
import os
import time
from pathlib import Path

import pytest

from lifecycle_bundles import (
    COMPLETION_SCHEMA, EVIDENCE_COLLECTED_SCHEMA, LifecycleKey, _exclusive_index_lock,
    canonical_json,
    materialize_ready_bundles,
)


def _row(
    key: LifecycleKey, record_id: str, now: float, *,
    evidence_collection: bool = True, **updates,
):
    row = {
        "record_id": record_id,
        "epoch_id": key.collection_epoch_id,
        "episode_id": key.episode_id,
        "policy_signature": key.policy_signature,
        "research_lane": key.research_lane,
        "observed_ts": now - 10_000,
        "source_revision": "a" * 40,
        "deployed_revision": "b" * 40,
        "tile_config_signature": "c" * 64,
        "config_signature": "d" * 64,
        "bundle_completion": {
            "schema": COMPLETION_SCHEMA,
            "terminal": True,
            "entry_outcome": "NO_FILL",
            "entry_schedule_terminal": True,
            "position_closed_or_never_opened": True,
            "post_observation_complete": True,
            "terminal_ts": now - 10_000,
            "horizon_complete_ts": now - 2_000,
        },
    }
    row.update(updates)
    if evidence_collection:
        completion = row["bundle_completion"]
        completion["completion_receipt_sha256"] = hashlib.sha256(
            canonical_json(completion).encode("utf-8")
        ).hexdigest()
        collected = {
            "schema": EVIDENCE_COLLECTED_SCHEMA,
            "identity": key.as_dict(),
            "event_id": record_id,
            "provenance": {
                field: row[field] for field in (
                    "source_revision", "deployed_revision",
                    "tile_config_signature", "config_signature",
                )
            },
            "completion_receipt_sha256": completion["completion_receipt_sha256"],
            "qualification_eligible_at": now - 2_000,
            "evidence_collected_at": now - 1_999,
        }
        collected["evidence_collected_receipt_sha256"] = hashlib.sha256(
            canonical_json(collected).encode("utf-8")
        ).hexdigest()
        row["evidence_collection_receipt"] = collected
    else:
        row.pop("bundle_completion", None)
    return row


def _append(root: Path, ledger: str, rows):
    path = root / "v3" / "ledgers" / f"{ledger}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    return path


def test_incremental_cursor_avoids_rescan_and_late_event_supersedes_bundle(tmp_path):
    now = time.time()
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "FIXED")
    source = _append(tmp_path, "lifecycle", [_row(key, "first", now)])

    first = materialize_ready_bundles(tmp_path, now=now)
    second = materialize_ready_bundles(tmp_path, now=now)
    _append(tmp_path, "lifecycle", [_row(
        key, "late-cost", now, evidence_collection=False, observed_ts=now - 1_500,
    )])
    third = materialize_ready_bundles(tmp_path, now=now)

    assert first["scan"]["bytes_indexed"] == source.stat().st_size - (
        json.dumps(_row(
            key, "late-cost", now, evidence_collection=False,
            observed_ts=now - 1_500,
        ), separators=(",", ":"), sort_keys=True).__len__() + 1
    )
    assert first["materialized_or_verified"] == 1
    assert second["scan"]["bytes_indexed"] == 0
    assert second["candidate_count"] == 0
    assert third["materialized_or_verified"] == 1
    assert first["bundles"][0]["bundle_id"] != third["bundles"][0]["bundle_id"]
    assert first["bundles"][0]["manifest"]["lifecycle_identity_id"] == third["bundles"][0]["manifest"]["lifecycle_identity_id"]
    assert first["source_cleanup_authorized"] is False


def test_scan_row_budget_is_global_and_restart_resumes_exact_offset(tmp_path):
    now = time.time()
    keys = [LifecycleKey("epoch", f"episode-{n}", "policy", "FIXED") for n in range(3)]
    _append(tmp_path, "lifecycle", [_row(key, f"row-{n}", now) for n, key in enumerate(keys)])

    reports = [materialize_ready_bundles(
        tmp_path, now=now, max_scan_rows=1, max_scan_bytes=1024 * 1024,
    ) for _ in range(3)]

    assert [report["scan"]["rows_scanned"] for report in reports] == [1, 1, 1]
    assert sum(report["materialized_or_verified"] for report in reports) == 3
    final = materialize_ready_bundles(tmp_path, now=now, max_scan_rows=1)
    assert final["scan"]["rows_scanned"] == 0
    assert final["candidate_count"] == 0


def test_bounded_scan_rotates_across_backlogged_ledgers(tmp_path):
    now = time.time()
    keys = [LifecycleKey("epoch", f"episode-{n}", "policy", "FIXED") for n in range(4)]
    _append(tmp_path, "opportunity", [_row(keys[0], "o-0", now), _row(keys[1], "o-1", now)])
    _append(tmp_path, "decision", [_row(keys[2], "d-0", now), _row(keys[3], "d-1", now)])
    first = materialize_ready_bundles(tmp_path, now=now, max_scan_rows=1)
    second = materialize_ready_bundles(tmp_path, now=now, max_scan_rows=1)
    assert list(first["scan"]["ledgers"]) == ["opportunity"]
    assert list(second["scan"]["ledgers"]) == ["decision"]


@pytest.mark.parametrize("mutation", ["truncate", "prefix", "rotate"])
def test_source_truncation_prefix_rewrite_and_rotation_fail_closed(tmp_path, mutation):
    now = time.time()
    key = LifecycleKey("epoch", "episode", "policy", "FIXED")
    source = _append(tmp_path, "lifecycle", [_row(key, "row", now)])
    materialize_ready_bundles(tmp_path, now=now)
    original = source.read_bytes()
    if mutation == "truncate":
        source.write_bytes(original[:10])
        expected = "SOURCE_LEDGER_TRUNCATED"
    elif mutation == "prefix":
        source.write_bytes(b"X" + original[1:])
        expected = "SOURCE_LEDGER_PREFIX_CHANGED"
    else:
        replacement = source.with_suffix(".replacement")
        replacement.write_bytes(original)
        source.unlink()
        os.replace(replacement, source)
        expected = "SOURCE_LEDGER_ROTATED"
    with pytest.raises(ValueError, match=expected):
        materialize_ready_bundles(tmp_path, now=now)


def test_incomplete_append_and_corrupt_index_fail_closed(tmp_path):
    now = time.time()
    key = LifecycleKey("epoch", "episode", "policy", "FIXED")
    source = _append(tmp_path, "lifecycle", [_row(key, "row", now)])
    materialize_ready_bundles(tmp_path, now=now)
    with source.open("ab") as handle:
        handle.write(b'{"record_id":"partial"}')
    with pytest.raises(ValueError, match="TRUNCATED_JSONL_LINE"):
        materialize_ready_bundles(tmp_path, now=now)

    source.write_bytes(source.read_bytes() + b"\n")
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    database.write_bytes(b"not a sqlite database")
    with pytest.raises(Exception):
        materialize_ready_bundles(tmp_path, now=now)


def test_scan_byte_limit_refuses_to_split_one_record(tmp_path):
    now = time.time()
    key = LifecycleKey("epoch", "episode", "policy", "FIXED")
    _append(tmp_path, "lifecycle", [_row(key, "row", now, padding="x" * 500)])
    with pytest.raises(ValueError, match="SCAN_BYTE_LIMIT_SPLITS_RECORD"):
        materialize_ready_bundles(tmp_path, now=now, max_scan_bytes=64)


def test_index_stores_offsets_not_duplicate_event_payload(tmp_path):
    now = time.time()
    key = LifecycleKey("epoch", "episode", "policy", "FIXED")
    marker = "unique-payload-that-must-remain-only-in-source-" + "z" * 10_000
    _append(tmp_path, "lifecycle", [_row(key, "row", now, diagnostic=marker)])
    materialize_ready_bundles(tmp_path, now=now)
    database = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    assert marker.encode() not in database.read_bytes()


def test_second_index_owner_fails_closed_without_waiting(tmp_path):
    with _exclusive_index_lock(tmp_path):
        with pytest.raises(ValueError, match="LIFECYCLE_INDEX_ALREADY_OWNED"):
            materialize_ready_bundles(tmp_path)
