import ast
import re
import threading
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")


def _load_functions(*names, namespace=None):
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    assert {node.name for node in selected} == set(names)
    scope = dict(namespace or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), scope)
    return scope


class _Logger:
    def __init__(self):
        self.rows = []

    def info(self, *args):
        self.rows.append(("info", args))

    def warning(self, *args):
        self.rows.append(("warning", args))

    def error(self, *args):
        self.rows.append(("error", args))


class _Runtime:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def start(self, root, **kwargs):
        self.calls.append((root, kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return True

    def stop(self, timeout=5.0):
        self.calls.append(("stop", timeout))
        return True

    def status(self):
        return {
            "schema": "lifecycle_pipeline_runtime_status_v1",
            "running": not self.fail,
            "owner": not self.fail,
            "last_outcome": "RUNNING" if not self.fail else "FAILED",
            "source_cleanup_authorized": False,
        }


def _namespace(tmp_path, runtime=None, revision="a" * 40):
    condition = threading.Condition()
    snapshot_condition = threading.Condition()
    runtime = runtime or _Runtime()
    return {
        "Path": Path,
        "re": re,
        "logger": _Logger(),
        "importlib": type("_Imports", (), {"import_module": staticmethod(lambda _name: runtime)}),
        "_LIFECYCLE_PIPELINE_RUNTIME": None,
        "_LIFECYCLE_PIPELINE_LAST_STATUS": None,
        "copy": __import__("copy"),
        "_runtime_git_rev_exact": lambda: revision,
        "_data_sync_runtime_root": lambda: tmp_path / "runtime",
        "_data_sync_volume_root": lambda: tmp_path,
        "disk_usage_fraction": lambda _root: 0.81,
        "STORAGE_PRESSURE_THRESHOLD": 0.8,
        "_data_sync_inventory_cache_condition": condition,
        "_data_sync_inventory_cache": {"refreshing": False},
        "_data_sync_async_inventory": {"refreshing": False, "worker_active": False},
        "_data_sync_sqlite_snapshot_condition": snapshot_condition,
        "_data_sync_sqlite_snapshot_states": {},
        "mirror_generation_lease_held": lambda _root: False,
    }


def test_optional_owner_uses_exact_revision_pressure_and_overlap_probes(tmp_path):
    (tmp_path / "runtime").mkdir()
    runtime = _Runtime()
    scope = _load_functions(
        "_lifecycle_pipeline_pressure_probe",
        "_lifecycle_pipeline_overlap_probe",
        "_start_lifecycle_pipeline_runtime",
        "_stop_lifecycle_pipeline_runtime",
        "_lifecycle_pipeline_runtime_status",
        namespace=_namespace(tmp_path, runtime),
    )
    assert scope["_start_lifecycle_pipeline_runtime"]() is True
    root, kwargs = runtime.calls[0]
    assert root == tmp_path / "runtime"
    assert kwargs["source_revision"] == "a" * 40
    assert kwargs["pressure_probe"]()["pressure"] is True
    assert kwargs["pressure_probe"]()["emergency"] is False
    scope["_data_sync_async_inventory"]["refreshing"] = True
    assert kwargs["overlap_probe"]() == []
    scope["_data_sync_async_inventory"]["worker_active"] = True
    assert kwargs["overlap_probe"]() == ["SYNC_ASYNC_INVENTORY_REFRESH"]
    scope["_data_sync_sqlite_snapshot_states"]["research.db"] = {
        "status": "BUILDING",
    }
    assert kwargs["overlap_probe"]() == [
        "SYNC_ASYNC_INVENTORY_REFRESH",
        "SQLITE_SNAPSHOT_BUILDING",
    ]
    scope["_data_sync_async_inventory"]["refreshing"] = False
    scope["_data_sync_async_inventory"]["worker_active"] = False
    assert kwargs["overlap_probe"]() == ["SQLITE_SNAPSHOT_BUILDING"]
    scope["_data_sync_sqlite_snapshot_states"]["research.db"]["status"] = "CURRENT"
    assert kwargs["overlap_probe"]() == []
    assert scope["_stop_lifecycle_pipeline_runtime"](3.0) is True
    assert runtime.calls[-1] == ("stop", 3.0)


def test_optional_owner_skips_unknown_revision_and_contains_failure(tmp_path):
    (tmp_path / "runtime").mkdir()
    runtime = _Runtime(fail=True)
    scope = _load_functions(
        "_lifecycle_pipeline_pressure_probe",
        "_lifecycle_pipeline_overlap_probe",
        "_start_lifecycle_pipeline_runtime",
        "_stop_lifecycle_pipeline_runtime",
        "_lifecycle_pipeline_runtime_status",
        namespace=_namespace(tmp_path, runtime, revision="short"),
    )
    assert scope["_start_lifecycle_pipeline_runtime"]() is False
    assert runtime.calls == []
    scope["_runtime_git_rev_exact"] = lambda: "b" * 40
    assert scope["_start_lifecycle_pipeline_runtime"]() is False
    assert len(runtime.calls) == 1


def test_bot_wires_start_status_and_both_shutdown_paths():
    source = BOT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    def calls(function_name, called_name):
        return sum(
            1 for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == called_name
        )

    assert calls("main", "_start_lifecycle_pipeline_runtime") == 1
    assert calls("main", "_stop_lifecycle_pipeline_runtime") == 1
    assert calls("shutdown_handler", "_stop_lifecycle_pipeline_runtime") == 1
    assert '"lifecycle_pipeline_runtime": _lifecycle_pipeline_runtime_status()' in source


def test_optional_lifecycle_worker_starts_only_after_dashboard_bootstrap():
    source = BOT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    bootstrap_line = next(
        node.lineno for node in ast.walk(main)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_DASHBOARD_BOOTSTRAP_COMPLETE"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value is True
    )
    worker_line = next(
        node.lineno for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_start_lifecycle_pipeline_runtime"
    )
    assert worker_line > bootstrap_line
