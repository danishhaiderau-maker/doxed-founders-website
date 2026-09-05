"""Execute exact backend adapter functions without importing a trading owner."""
import ast
import hmac
from pathlib import Path
from types import SimpleNamespace
import threading

from flask import Flask, request
import pytest


BOT = Path(__file__).with_name("bot.py")


def load_functions(**extra):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    names = {"_data_sync_bundle_authenticated", "_data_sync_bundle_generation",
             "_start_data_sync_bundle_generation"}
    namespace = {"hmac": hmac, "request": request, "threading": threading, **extra}
    exec(compile(ast.Module(body=[node for node in tree.body if
        isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[]), str(BOT), "exec"), namespace)
    return namespace


def test_token_auth_never_inherits_loopback_trust():
    app = Flask(__name__)
    ns = load_functions(_BOT_ADMIN_TOKEN="test-token")
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert ns["_data_sync_bundle_authenticated"]() is False
    with app.test_request_context("/", headers={"X-Bot-Admin-Token": "test-token"}):
        assert ns["_data_sync_bundle_authenticated"]() is True
    ns["_BOT_ADMIN_TOKEN"] = ""
    with app.test_request_context("/"):
        assert ns["_data_sync_bundle_authenticated"]() is False


def test_retained_inventory_uses_frozen_identity_not_latest():
    generation = {"storage": "disk_pages_v2", "ack_eligible": True,
                  "bundle_identity": {"source_git_rev": "source", "collection_epoch_id": "old-epoch",
                                      "tile_registry_signature": "old-tile"}}
    ns = load_functions(_data_sync_inventory_generation=lambda _: generation,
        _data_sync_receipt_bootstrap_gate=lambda: {"complete": True}, _runtime_git_rev=lambda: "source")
    result = ns["_data_sync_bundle_generation"]("a" * 64)
    assert result["collection_epoch_id"] == "old-epoch" and result["tile_registry_signature"] == "old-tile"
    assert result["inventory_sha256"] == "a" * 64
    assert "source_git_rev" not in generation
    ns["_runtime_git_rev"] = lambda: "new-source"
    assert ns["_data_sync_bundle_generation"]("a" * 64) is None


@pytest.mark.parametrize("defect", ["missing-identity", "missing-epoch", "nonack", "bootstrap", "expired"])
def test_adapter_rejects_missing_authority(defect):
    generation = {"storage": "disk_pages_v2", "ack_eligible": True,
                  "bundle_identity": {"source_git_rev": "source", "collection_epoch_id": "epoch",
                                      "tile_registry_signature": "tile"}}
    if defect == "missing-identity": generation.pop("bundle_identity")
    if defect == "missing-epoch": generation["bundle_identity"].pop("collection_epoch_id")
    if defect == "nonack": generation["ack_eligible"] = False
    ns = load_functions(_data_sync_inventory_generation=lambda _: None if defect == "expired" else generation,
        _data_sync_receipt_bootstrap_gate=lambda: {"complete": defect != "bootstrap"},
        _runtime_git_rev=lambda: "source")
    assert ns["_data_sync_bundle_generation"]("a" * 64) is None


def test_disabled_coordinator_never_starts_work():
    import os
    ns = load_functions(os=SimpleNamespace(getenv=lambda *args: "0"))
    assert ns["_start_data_sync_bundle_generation"]("a" * 64) is False


def test_coordinator_singleton_rejects_duplicate_start():
    lock = threading.Lock(); lock.acquire()
    ns = load_functions(os=SimpleNamespace(getenv=lambda *args: "1"),
                        _DATA_SYNC_BUNDLE_COORDINATOR_LOCK=lock)
    ns["_data_sync_bundle_generation"] = lambda _: {"generation_id": "a" * 64}
    assert ns["_start_data_sync_bundle_generation"]("a" * 64) is False
    assert lock.locked()
    lock.release()


def test_real_registration_is_lazy_and_does_not_create_volume_artifacts(tmp_path):
    import data_sync_bundle_api as api
    app = Flask(__name__)
    missing = tmp_path / "absent"
    api.register_bundle_routes(app, authenticated=lambda: request.headers.get("X-Test") == "yes",
        generation_lookup=lambda _: {}, output_root=lambda: missing)
    client = app.test_client()
    assert client.get("/api/data-sync/bundles").status_code == 401
    assert client.get("/api/data-sync/bundles", headers={"X-Test": "yes"}).status_code == 404
    assert not missing.exists()


def test_inventory_hook_follows_publication_and_is_not_in_http_handler():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    calls = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_start_data_sync_bundle_generation":
                    calls.append((node.name, call.lineno))
    assert len(calls) == 1 and calls[0][0] == "_data_sync_inventory_refresh_worker"
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == calls[0][0])
    retains = [call.lineno for call in ast.walk(fn) if isinstance(call, ast.Call)
               and isinstance(call.func, ast.Name) and call.func.id == "_data_sync_retain_disk_inventory_generation"]
    assert retains and max(retains) < calls[0][1]
