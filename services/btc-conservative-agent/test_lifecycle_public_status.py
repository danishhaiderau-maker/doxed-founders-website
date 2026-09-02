import ast
import hashlib
import json
import os
import re
import time
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(BOT)
FUNCTIONS = {
    node.name: node for node in TREE.body
    if isinstance(node, ast.FunctionDef)
}


def _namespace(root, runtime):
    selected = [
        FUNCTIONS["_lifecycle_artifact_counts"],
        FUNCTIONS["_lifecycle_pipeline_public_status"],
    ]
    namespace = {
        "Path": Path, "os": os, "re": re, "time": time,
        "hashlib": hashlib, "json": json,
        "_data_sync_runtime_root": lambda: root / "runtime",
        "_data_sync_volume_root": lambda: root,
        "_lifecycle_pipeline_runtime_status": lambda: runtime,
        "_runtime_git_rev_exact": lambda: "a" * 40,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def test_public_status_distinguishes_pipeline_stages_and_is_bounded(tmp_path):
    wal_identity = {
        "epoch_id": "epoch-live", "source_revision": "a" * 40,
        "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64,
    }
    runtime = {
        "owner": True, "running": True, "active": False,
        "source_revision": "a" * 40, "last_outcome": "SUCCESS",
        "failure_count": 0, "backoff_sec": 0, "next_run_unix": 1010,
        "last_success_unix": 990, "pressure": False, "emergency": False,
        "last_worker_failure_unix": 980,
        "last_worker_failure": {
            "error_class": "ValueError", "error_code": "INVALID_JSONL_ROW",
            "ledger": "lifecycle", "byte_offset": 1406,
        },
        "overlap_code": "ACTIVE_OVERLAP_PATH:/secret/runtime/lease",
        "receipt_bootstrap": {
            "required": True, "status": "PENDING", "complete": False,
            "blocked": False, "ledger": "decision", "ledgers_checked": 3,
            "records_indexed": 16, "bytes_indexed": 2048, "cursor": 4096,
        },
        "last_result": {
            "rows_scanned": 7, "bytes_indexed": 80,
            "pending_dirty_lifecycles": 2, "promoted_qualification_retries": 1,
            "candidate_count": 3, "transfer_ready_count": 2,
            "transfer_bundle_count": 1, "completion_appended_count": 1,
            "bundle_count": 0, "caught_up": False,
            "stage_counts": {
                "TRANSFER_BUNDLE_MATERIALIZED_OR_VERIFIED": 1,
                "QUALIFICATION_DEFERRED_UNTIL_MATURITY": 2,
                **{f"STAGE_{index}": index for index in range(40)},
            },
            "blocker_counts": {
                "POST_OBSERVATION_INCOMPLETE": 2,
                "unsafe/path/value": 99,
            },
            "emergency_wal": {
                "observed_unix": 995,
                "identity": wal_identity,
                "identity_sha256": hashlib.sha256(json.dumps(
                    wal_identity, separators=(",", ":"), sort_keys=True,
                ).encode()).hexdigest(), "capacity_extents": 4,
                "free_extents": 3, "retained_count": 1, "retained_bytes": 100,
                "state_counts": {"PREPARED": 0, "DEFERRED": 0, "REPLAYED": 1},
                "oldest_generation": "12345678-1234-1234-1234-123456789abc",
                "oldest_state": "REPLAYED", "alarms": [],
                "last_action": {"replayed": True, "state": "REPLAYED"},
            },
        },
    }
    payload = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)
    assert payload["source_revision_match"] is True
    assert payload["progress"]["transfer_bundle_count"] == 1
    assert payload["progress"]["completion_appended_count"] == 1
    assert payload["progress"]["bundle_count"] == 0
    assert len(payload["stage_counts"]) <= 32
    assert "unsafe/path/value" not in payload["blocker_counts"]
    assert payload["last_success_age_sec"] == 10
    assert payload["last_failure_class"] == "ValueError"
    assert payload["last_failure_code"] == "INVALID_JSONL_ROW"
    assert payload["last_failure_ledger"] == "lifecycle"
    assert payload["last_failure_byte_offset"] == 1406
    assert payload["last_failure_age_sec"] == 20
    assert payload["next_run_in_sec"] == 10
    assert payload["overlap_code"] == "OVERLAP_ACTIVE_REDACTED"
    assert payload["receipt_bootstrap"] == {
        "required": True, "status": "PENDING", "complete": False,
        "blocked": False, "ledger": "decision", "ledgers_checked": 3,
        "records_indexed": 16, "bytes_indexed": 2048, "cursor": 4096,
    }
    assert payload["emergency_wal"]["status"] == "CURRENT"
    assert payload["emergency_wal"]["reserve_ready"] is True
    assert payload["emergency_wal"]["observed_age_sec"] == 5
    assert payload["emergency_wal"]["retained_count"] == 1
    assert payload["emergency_wal"]["state_counts"]["REPLAYED"] == 1
    assert payload["emergency_wal"]["last_action"]["replayed"] is True


def test_public_status_wal_identity_is_fail_closed_and_content_free(tmp_path):
    runtime = {"last_result": {"emergency_wal": {
        "observed_unix": 999, "identity": {
            "epoch_id": "../secret", "source_revision": "token=secret",
            "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64,
        },
        "capacity_extents": 4, "free_extents": 4, "retained_count": 0,
        "retained_bytes": 0, "state_counts": {},
        "alarms": ["SAFE_ALARM", "secret/path"],
        "last_action": {"reason": "SECRET/PATH"},
    }}}
    payload = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)
    wal = payload["emergency_wal"]
    assert wal["status"] == "INVALID"
    assert wal["identity"] is None
    assert wal["alarms"] == ["SAFE_ALARM"]
    assert "secret/path" not in repr(payload).lower()


def test_public_status_wal_never_false_greens_stale_future_or_inconsistent_state(tmp_path):
    identity = {
        "epoch_id": "epoch-live", "source_revision": "a" * 40,
        "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64,
    }
    digest = hashlib.sha256(json.dumps(
        identity, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    base = {
        "identity": identity, "identity_sha256": digest,
        "capacity_extents": 4, "free_extents": 4, "retained_count": 0,
        "retained_bytes": 0,
        "state_counts": {"PREPARED": 0, "DEFERRED": 0, "REPLAYED": 0},
        "oldest_generation": None, "oldest_state": None, "alarms": [],
    }
    for observed, expected in ((600, "STALE"), (1010, "INVALID")):
        runtime = {"last_result": {"emergency_wal": {**base, "observed_unix": observed}}}
        wal = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)["emergency_wal"]
        assert wal["status"] == expected
        assert wal["reserve_ready"] is False
    inconsistent = {**base, "observed_unix": 999, "free_extents": 4, "retained_count": 1}
    runtime = {"last_result": {"emergency_wal": inconsistent}}
    wal = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)["emergency_wal"]
    assert wal["status"] == "INVALID"
    assert wal["reserve_ready"] is False


def test_public_status_redacts_unbounded_worker_failure_material(tmp_path):
    runtime = {
        "last_worker_failure_unix": 900,
        "last_worker_failure": {
            "error_class": "RuntimeError /secret/path token=abc",
            "error_code": "BAD CODE user@example.test",
            "ledger": "../../secret", "byte_offset": "1406/private",
        },
    }
    payload = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)
    assert payload["last_failure_class"] == "FAILURE_CLASS_REDACTED"
    assert payload["last_failure_code"] == "FAILURE_CODE_REDACTED"
    assert payload["last_failure_age_sec"] == 100
    assert payload["last_failure_ledger"] is None
    assert payload["last_failure_byte_offset"] is None
    assert "secret" not in repr(payload)
    assert "example" not in repr(payload)


def test_public_status_keeps_recovered_incident_auditable_but_current(tmp_path):
    identity = {
        "epoch_id": "epoch-live", "source_revision": "a" * 40,
        "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64,
    }
    digest = hashlib.sha256(json.dumps(
        identity, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    runtime = {"last_result": {"emergency_wal": {
        "observed_unix": 999, "identity": identity, "identity_sha256": digest,
        "capacity_extents": 4, "free_extents": 4, "retained_count": 0,
        "retained_bytes": 0,
        "state_counts": {"PREPARED": 0, "DEFERRED": 0, "REPLAYED": 0},
        "oldest_generation": None, "oldest_state": None, "alarms": [],
        "incident_alarms": ["EMERGENCY_WAL_CONTROL_COPY_CORRUPT", "secret/path"],
    }}}
    wal = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)["emergency_wal"]
    assert wal["status"] == "CURRENT" and wal["reserve_ready"] is True
    assert wal["alarms"] == []
    assert wal["incident_alarms"] == ["EMERGENCY_WAL_CONTROL_COPY_CORRUPT"]


def test_artifact_counts_are_content_free_and_report_age(tmp_path):
    completion = tmp_path / "runtime" / "v3" / "lifecycle_bundles" / "aa" / ("lifecycle-" + "1" * 64)
    transfer = tmp_path / "runtime" / "v3" / "lifecycle_transfer_bundles" / "bb" / ("transfer-" + "2" * 64)
    ack = tmp_path / "v3" / "lifecycle_cleanup_acks" / ("lifecycle-" + "1" * 64 + ".json")
    completion.mkdir(parents=True)
    transfer.mkdir(parents=True)
    ack.parent.mkdir(parents=True)
    ack.write_text("secret receipt body", encoding="utf-8")
    for path in (completion, transfer, ack):
        os.utime(path, (900, 900))
    payload = _namespace(tmp_path, {})["_lifecycle_artifact_counts"](1000)
    assert payload["completion_bundles"] == {"count": 1, "newest_age_sec": 100, "scan_truncated": False}
    assert payload["transfer_bundles"]["count"] == 1
    assert payload["acks"]["count"] == 1
    assert "secret" not in repr(payload)


def test_public_status_contract_excludes_identity_and_secret_material():
    helper = ast.unparse(FUNCTIONS["_lifecycle_pipeline_public_status"])
    artifact = ast.unparse(FUNCTIONS["_lifecycle_artifact_counts"])
    assert '"lifecycle_pipeline": _lifecycle_pipeline_public_status(now)' in BOT
    for forbidden in (
        "nonce", "attestation", "key_id", "bundle_id", "receipt_path",
        "receipt_sha256", "receipt_body",
        "lifecycle_id", "user_id", "manifest_path",
    ):
        assert forbidden not in helper.lower()
        assert forbidden not in artifact.lower()
    assert "maximum_entries: int=4096" in artifact
    assert "index >= 256" in artifact


def test_public_bootstrap_status_fails_closed_on_unrecognized_values(tmp_path):
    runtime = {"receipt_bootstrap": {
        "required": True, "status": "secret/path", "complete": False,
        "blocked": True, "ledger": "../../secret", "ledgers_checked": 99,
        "records_indexed": 5, "bytes_indexed": 10, "cursor": -1,
    }}
    bootstrap = _namespace(tmp_path, runtime)["_lifecycle_pipeline_public_status"](1000)[
        "receipt_bootstrap"
    ]
    assert bootstrap == {
        "required": True, "status": "PENDING", "complete": False,
        "blocked": True, "ledger": None, "ledgers_checked": 8,
        "records_indexed": 5, "bytes_indexed": 10, "cursor": None,
    }
