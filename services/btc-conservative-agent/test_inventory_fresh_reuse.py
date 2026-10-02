"""Actual extracted inventory functions; no production runtime imports."""
import ast
import hmac
from pathlib import Path

import pytest
from test_data_sync_cadence_throttle_contract import _async_inventory_function

BOT = Path(__file__).with_name("bot.py")


def test_two_fresh_nonces_reuse_unexpired_completed_generation():
    generation = {"generation_id": "a"*64, "bundle_identity": {
        "source_git_rev": "rev", "collection_epoch_id": "epoch", "tile_registry_signature": "tile"}}
    state = {"status": "CURRENT", "rows": [], "generation": generation,
             "generation_id": "a"*64, "expires_at": 200, "served_since_refresh": False,
             "refreshing": False, "completed_refresh_nonce": "c"*32}
    request, starts = _async_inventory_function(state, 150)
    for nonce in ("d"*32, "e"*32):
        response = request(force_refresh=True, refresh_nonce=nonce)
        assert response["status"] == "CURRENT" and response["generation"] == generation
    assert starts == [] and state["refreshing"] is False


def test_expired_previously_served_generation_starts_refresh():
    state = {"status": "CURRENT", "rows": [], "generation": {"generation_id": "a"*64,
             "bundle_identity": {"source_git_rev": "rev", "collection_epoch_id": "epoch", "tile_registry_signature": "tile"}},
             "expires_at": 149, "served_since_refresh": True, "refreshing": False,
             "completed_refresh_nonce": "c"*32}
    request, starts = _async_inventory_function(state, 150)
    result = request(force_refresh=True, refresh_nonce="d"*32)
    assert result["status"] == "STALE_REVALIDATING" and result["refreshing"]
    assert len(starts) == 1


@pytest.mark.parametrize("field", ["source_git_rev", "collection_epoch_id", "tile_registry_signature", "missing"])
@pytest.mark.parametrize("served", [False, True])
def test_current_cache_identity_drift_cannot_use_any_reuse_exception(field, served):
    identity = {"source_git_rev": "rev", "collection_epoch_id": "epoch", "tile_registry_signature": "tile"}
    if field != "missing": identity[field] = "old"
    generation = {"generation_id": "a"*64, "bundle_identity": identity if field != "missing" else None}
    state = {"status": "CURRENT", "rows": [], "generation": generation,
             "expires_at": 200, "served_since_refresh": served, "refreshing": False,
             "completed_refresh_nonce": "c"*32}
    request, starts = _async_inventory_function(state, 150)
    response = request(force_refresh=True, refresh_nonce="c"*32)
    assert response["status"] == "STALE_REVALIDATING" and response["refreshing"]
    assert len(starts) == 1


@pytest.mark.parametrize("field", ["source_git_rev", "collection_epoch_id", "tile_registry_signature"])
def test_identity_drift_still_cannot_ack_cached_generation(field):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_data_sync_ack_v3_identity_matches")
    ns = {"hmac": hmac, "_runtime_git_rev": lambda: "rev",
          "_load_research_session_meta": lambda: {"collector_v22_epoch_id": "epoch"},
          "active_tile_registry_signature": lambda: "tile"}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT), "exec"), ns)
    row = {"source_git_rev": "rev", "collection_epoch_id": "epoch", "tile_registry_signature": "tile"}
    assert ns[node.name](row) == (True, None)
    row[field] = "old"
    assert ns[node.name](row) == (False, field)


def test_pinned_generation_branch_does_not_request_refresh():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "api_data_sync_manifest")
    branch = next(n for n in ast.walk(route) if isinstance(n, ast.If)
                  and isinstance(n.test, ast.Name) and n.test.id == "requested_generation_id")
    # Execute the unchanged retained branch body, including its authoritative
    # lookup and generation projection. It has no async-refresh invocation.
    function = ast.parse("def selected_branch():\n    pass\n").body[0]
    function.body = branch.body + [ast.Return(value=ast.Name(id="inventory_state", ctx=ast.Load()))]
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    generation = {"status": "CURRENT", "storage": "disk_pages_v2", "generation_id": "a"*64}
    ns = {"requested_generation_id": "a"*64, "_data_sync_async_inventory": {"refreshing": True},
          "_data_sync_inventory_generation": lambda _: generation}
    exec(compile(module, str(BOT), "exec"), ns)
    result = ns["selected_branch"]()
    assert result["status"] == "CURRENT" and result["generation"] == generation
    assert result["generation_available"] is True
