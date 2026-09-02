import ast
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
        "_data_sync_runtime_root": lambda: root / "runtime",
        "_data_sync_volume_root": lambda: root,
        "_lifecycle_pipeline_runtime_status": lambda: runtime,
        "_runtime_git_rev_exact": lambda: "a" * 40,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def test_public_status_distinguishes_pipeline_stages_and_is_bounded(tmp_path):
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
        "nonce", "receipt", "attestation", "key_id", "bundle_id",
        "lifecycle_id", "user_id", "manifest_path",
    ):
        assert forbidden not in helper.lower()
        assert forbidden not in artifact.lower()
    assert "maximum_entries: int=4096" in artifact
    assert "index >= 256" in artifact
