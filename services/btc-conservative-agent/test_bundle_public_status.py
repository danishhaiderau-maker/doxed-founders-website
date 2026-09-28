"""The transport producer's admission outcome is observable without side effects."""
import ast
import re
import threading
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parent / "bot.py"
SOURCE = BOT.read_text(encoding="utf-8")


def _namespace(admit):
    ns = {
        "os": __import__("os"), "re": re,
        "_DATA_SYNC_BUNDLE_REGISTRY": None, "_DATA_SYNC_BUNDLE_REGISTRY_HYDRATING": False,
        "_DATA_SYNC_BUNDLE_LAST_STATUS": {"status": "NOT_STARTED"},
        "_DATA_SYNC_BUNDLE_ADMISSION_STATUS": {"outcome": "NOT_OBSERVED"},
        "_DATA_SYNC_BUNDLE_LAST_RECONCILE": {"outcome": "NOT_OBSERVED"},
        "_data_sync_inventory_cache_condition": threading.Condition(threading.RLock()),
        "_data_sync_async_inventory": {"status": "CURRENT", "generation_id": "a" * 64},
        "_admit_data_sync_bundle_generation": admit,
        "utc_iso": lambda: "2026-09-29T00:00:00Z",
    }
    tree = ast.parse(SOURCE)
    names = {"_data_sync_bundle_public_status", "_reconcile_data_sync_bundle_generation"}
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == 2
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT), "exec"), ns)
    return ns


def test_reconcile_outcome_is_published_read_only():
    ns = _namespace(lambda gid: {"outcome": "REGISTRY_HYDRATING", "started": False})
    ns["_reconcile_data_sync_bundle_generation"]()
    status = ns["_data_sync_bundle_public_status"]()
    assert status["last_reconcile"]["outcome"] == "REGISTRY_HYDRATING"
    assert status["last_reconcile"]["generation_id"] == "a" * 64
    assert status["registry_ready"] is False
    assert status["coordinator"]["status"] == "NOT_STARTED"


def test_reconcile_exception_is_recorded_and_propagated():
    def boom(gid):
        raise OSError("volume")
    ns = _namespace(boom)
    with pytest.raises(OSError):
        ns["_reconcile_data_sync_bundle_generation"]()
    assert ns["_data_sync_bundle_public_status"]()["last_reconcile"] == {
        "outcome": "EXCEPTION", "error": "OSError", "started": False,
        "generation_id": "a" * 64, "at": "2026-09-29T00:00:00Z"}


def test_status_route_exposes_producer_diagnostics():
    start = SOURCE.index("\ndef status():", SOURCE.index("@app.route('/api/status')"))
    body = SOURCE[start:SOURCE.index("\n@app.route(", start)]
    assert '"data_sync_transport_bundles": _data_sync_bundle_public_status(),' in body
