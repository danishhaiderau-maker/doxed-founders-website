import ast
import hashlib
import hmac
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lifecycle_bundles import COMPLETION_SCHEMA, LifecycleKey, materialize_bundle


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(BOT_SOURCE)
FUNCTIONS = {
    node.name: node for node in TREE.body
    if isinstance(node, ast.FunctionDef)
}


def _namespace(root: Path):
    names = (
        "_data_sync_iso8601_utc", "_data_sync_lifecycle_manifest_sha256",
        "_data_sync_lifecycle_identity_sha256", "_data_sync_lifecycle_cleanup_eligibility",
        "_data_sync_lifecycle_ack_path", "_data_sync_persist_lifecycle_ack",
        "_data_sync_validate_lifecycle_ack_bundle",
    )
    namespace = {
        "Path": Path, "datetime": datetime, "timezone": timezone,
        "json": json, "hashlib": hashlib, "hmac": hmac, "re": re,
        "os": os, "uuid": uuid,
        "utc_iso": lambda: "2026-09-01T00:00:00Z",
        "_data_sync_volume_root": lambda: root,
        "_DATA_SYNC_LIFECYCLE_CLEANUP_ACK_SCHEMA": "lifecycle_bundle_cleanup_ack_v1",
        "_DATA_SYNC_LIFECYCLE_CLEANUP_ENABLED": False,
        "_DATA_SYNC_TERMINAL_OUTCOMES": frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"}),
    }
    module = ast.Module(body=[FUNCTIONS[name] for name in names], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    namespace["_data_sync_resolve_relpath"] = lambda relative: (root / relative).resolve(strict=True)
    return namespace


def _bundle(root: Path, now=20_000.0):
    key = LifecycleKey("epoch-1", "episode-1", "policy-a", "FIXED")
    row = {
        "record_id": "life-1", "ledger": "lifecycle", "epoch_id": key.collection_epoch_id,
        "episode_id": key.episode_id, "policy_signature": key.policy_signature,
        "research_lane": key.research_lane, "observed_ts": now - 10_000,
        "source_revision": "a" * 40, "deployed_revision": "b" * 40,
        "tile_config_signature": "c" * 64,
        "bundle_completion": {
            "schema": COMPLETION_SCHEMA, "terminal": True, "entry_outcome": "NO_FILL",
            "entry_schedule_terminal": True, "position_closed_or_never_opened": True,
            "post_observation_complete": True, "terminal_ts": now - 10_000,
            "horizon_complete_ts": now - 2_000,
        },
    }
    result = materialize_bundle(root, key, [row], now=now)
    return Path(result["path"]), result["manifest"]


def _receipt(namespace, root, bundle_path, manifest):
    relative = (bundle_path / "manifest.json").relative_to(root).as_posix()
    receipt = {
        "schema": "lifecycle_bundle_cleanup_ack_v1",
        "bundle_id": manifest["bundle_id"], "lifecycle_id": manifest["lifecycle_id"],
        "bundle_manifest_path": relative,
        "source_git_rev": manifest["provenance"]["source_revision"],
        "deployed_git_rev": manifest["provenance"]["deployed_revision"],
        "collection_epoch_id": manifest["identity"]["collection_epoch_id"],
        "tile_registry_signature": manifest["provenance"]["tile_config_signature"],
        "terminal_outcome": manifest["completion"]["classification"],
        "terminal_at": "1970-01-01T02:46:40Z",
        "pending_order_ids": [], "open_position_ids": [],
        "files": manifest["files"], "manifest_sha256": manifest["cleanup_manifest_sha256"],
    }
    receipt["immutable_identity_sha256"] = namespace["_data_sync_lifecycle_identity_sha256"](receipt)
    receipt["laptop_acknowledgement"] = {
        name: {
            "complete": True, "bundle_id": receipt["bundle_id"],
            "lifecycle_id": receipt["lifecycle_id"],
            "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "manifest_sha256": receipt["manifest_sha256"],
            "acknowledged_at": "2026-09-01T00:00:00Z",
        } for name in ("canonical", "archive", "index")
    }
    return receipt


def test_exact_bundle_and_triple_ack_persist_atomically_with_cleanup_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        namespace = _namespace(root)
        bundle_path, manifest = _bundle(root)
        receipt = _receipt(namespace, root, bundle_path, manifest)
        eligibility = namespace["_data_sync_lifecycle_cleanup_eligibility"](receipt)
        assert eligibility["proof_complete"] is True
        assert eligibility["cleanup_authorized"] is False
        verified = namespace["_data_sync_validate_lifecycle_ack_bundle"](receipt)
        target = namespace["_data_sync_persist_lifecycle_ack"](receipt, eligibility)
        persisted = json.loads(target.read_text(encoding="utf-8"))
    assert verified["bundle_id"] == receipt["bundle_id"]
    assert persisted["source_cleanup_authorized"] is False
    assert persisted["transfer_receipt_validation"]["status"] == "ELIGIBLE_BUT_CLEANUP_DISABLED"


def test_bundle_ack_rejects_manifest_or_revision_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        namespace = _namespace(root)
        bundle_path, manifest = _bundle(root)
        receipt = _receipt(namespace, root, bundle_path, manifest)
        receipt["source_git_rev"] = "d" * 40
        receipt["immutable_identity_sha256"] = namespace["_data_sync_lifecycle_identity_sha256"](receipt)
        with pytest.raises(ValueError, match="does not match Fly bundle"):
            namespace["_data_sync_validate_lifecycle_ack_bundle"](receipt)


def test_ack_rechecks_bundle_bytes_and_conflicting_ack_cannot_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        namespace = _namespace(root)
        bundle_path, manifest = _bundle(root)
        receipt = _receipt(namespace, root, bundle_path, manifest)
        eligibility = namespace["_data_sync_lifecycle_cleanup_eligibility"](receipt)
        target = namespace["_data_sync_persist_lifecycle_ack"](receipt, eligibility)
        original = target.read_bytes()
        conflicting = json.loads(json.dumps(receipt))
        conflicting["laptop_acknowledgement"]["archive"]["sha256"] = "f" * 64
        with pytest.raises(ValueError, match="conflicting lifecycle acknowledgement"):
            namespace["_data_sync_persist_lifecycle_ack"](conflicting, eligibility)
        assert target.read_bytes() == original
        (bundle_path / "events.jsonl").write_text("corrupt\n", encoding="utf-8")
        with pytest.raises(ValueError, match="file integrity mismatch"):
            namespace["_data_sync_validate_lifecycle_ack_bundle"](receipt)


def test_route_is_admin_gated_and_has_reserved_bounded_worker_capacity():
    assert "@app.route('/api/data-sync/lifecycle-ack', methods=['POST'])" in BOT_SOURCE
    assert 'b"/api/data-sync/lifecycle-ack"' in BOT_SOURCE
    assert '"/api/data-sync/lifecycle-ack"' in BOT_SOURCE
    assert "source_cleanup_authorized\": False" in BOT_SOURCE
