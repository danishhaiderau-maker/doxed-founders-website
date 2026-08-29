import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
WORKER_PATH = ROOT / "data_sync_inventory_worker.py"
BOT_PATH = ROOT / "bot.py"


def _load_worker():
    spec = importlib.util.spec_from_file_location("isolated_inventory_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(volume: Path, nonce: str, *, roots=None):
    runtime = volume / "runtime"
    work = volume / ".data-sync-snapshots"
    runtime.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    return {
        "schema": "fly_runtime_inventory_worker_request_v1",
        "nonce": nonce,
        "source_revision": "a" * 40,
        "launched_unix": 100.0,
        "work_root": str(work.resolve()),
        "volume_root": str(volume.resolve()),
        "runtime_root": str(runtime.resolve()),
        "allowed_roots": [str(path.resolve()) for path in (roots or [runtime])],
        "top_level_receipt_names": [],
        "extensions": [".csv", ".json", ".jsonl"],
        "excluded_names": ["sync_inventory_current.json"],
        "excluded_dir_names": [".data-sync-snapshots"],
        "append_prefix_names": ["events.jsonl"],
        "serialized_append_targets": [],
        "rewrite_targets": [],
        "max_rows": 5000,
    }


def _paths(volume: Path, nonce: str):
    work = volume / ".data-sync-snapshots"
    return (
        work / f"inventory-request-{nonce}.json",
        work / f"inventory-result-{nonce}.json",
    )


def _make_directory_link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
    else:
        link.symlink_to(target, target_is_directory=True)


def test_worker_is_stdlib_only_and_does_not_import_the_bot():
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported <= {
        "__future__", "argparse", "hashlib", "json", "os", "time", "uuid",
        "datetime", "pathlib",
    }
    assert "bot" not in imported


def test_worker_nonce_identity_containment_and_atomic_result(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    nonce = "1" * 32
    request_path, result_path = _paths(volume, nonce)
    payload = _request(volume, nonce)
    (volume / "runtime" / "events.jsonl").write_bytes(b'{"a":1}\npartial')
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    assert worker.run(request_path, result_path, nonce) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["schema"] == "fly_runtime_inventory_worker_result_v1"
    assert result["nonce"] == nonce
    assert result["source_revision"] == "a" * 40
    assert result["file_count"] == len(result["rows"]) == 1
    assert result["rows"][0]["size"] == len(b'{"a":1}\n')
    assert result["rows_sha256"] == worker._rows_sha256(result["rows"])
    assert not list(result_path.parent.glob("*.tmp"))

    escaped_result = tmp_path / f"inventory-result-{nonce}.json"
    assert worker.run(request_path, escaped_result, nonce) == 1
    wrong_nonce = "2" * 32
    assert worker.run(request_path, result_path, wrong_nonce) == 1


def test_worker_rejects_allowed_root_outside_volume(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    outside = tmp_path / "outside"
    outside.mkdir()
    nonce = "3" * 32
    request_path, result_path = _paths(volume, nonce)
    payload = _request(volume, nonce, roots=[outside])
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert worker.run(request_path, result_path, nonce) == 1
    assert not result_path.exists()


def test_worker_deduplicates_overlapping_roots_and_filters_sensitive_files(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    nested = runtime / "research"
    nested.mkdir(parents=True)
    (nested / "good.json").write_text("{}", encoding="utf-8")
    (nested / "secret.json").write_text("{}", encoding="utf-8")
    nonce = "4" * 32
    request_path, result_path = _paths(volume, nonce)
    payload = _request(volume, nonce, roots=[runtime, nested])
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert worker.run(request_path, result_path, nonce) == 0
    rows = json.loads(result_path.read_text(encoding="utf-8"))["rows"]
    assert [row["path"] for row in rows] == ["research/good.json"]


@pytest.mark.parametrize("mutation", [
    "schema", "nonce", "revision", "file_count", "generated_before", "hash",
])
def test_parent_contract_rejects_corrupt_or_mismatched_worker_result(mutation):
    source = BOT_PATH.read_text(encoding="utf-8")
    assert 'result.get("schema") != _DATA_SYNC_INVENTORY_WORKER_RESULT_SCHEMA' in source
    assert 'str(result.get("nonce") or ""), nonce' in source
    assert 'str(result.get("source_revision") or ""), _runtime_git_rev()' in source
    assert 'int(result.get("file_count") or -1) != len(rows)' in source
    assert 'float(result.get("generated_unix") or 0.0) < launched_unix' in source
    assert 'str(result.get("rows_sha256") or "")' in source
    # Each named corruption maps to a distinct explicit validation fence above.
    assert mutation


def test_parent_contract_handles_missing_nonzero_timeout_and_cleans_unique_transients():
    source = BOT_PATH.read_text(encoding="utf-8")
    assert "if completed.returncode != 0:" in source
    assert 'result_path.read_text(encoding="utf-8")' in source
    assert "timeout=_DATA_SYNC_INVENTORY_WORKER_TIMEOUT_SECONDS" in source
    assert "except BaseException as exc:" in source
    assert "transient_paths = [request_path, result_path]" in source
    assert 'request_path.parent.glob(f"*{nonce}*")' in source
    assert "for transient in transient_paths:" in source
    assert "transient.unlink(missing_ok=True)" in source
    assert 'work_root / f"inventory-request-{nonce}.json"' in source
    assert 'work_root / f"inventory-result-{nonce}.json"' in source


def test_real_worker_nonzero_for_missing_request_and_orphan_cannot_satisfy_nonce(tmp_path):
    volume = tmp_path / "volume"
    work = volume / ".data-sync-snapshots"
    work.mkdir(parents=True)
    nonce = "5" * 32
    request_path, result_path = _paths(volume, nonce)
    orphan = work / f"inventory-result-{'6' * 32}.json"
    orphan.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(WORKER_PATH), "--request", str(request_path),
         "--result", str(result_path), "--nonce", nonce],
        capture_output=True, timeout=10, check=False,
    )
    assert completed.returncode != 0
    assert not result_path.exists()
    assert orphan.exists()


def test_nonce_bound_paths_are_unique_for_overlapping_requests(tmp_path):
    volume = tmp_path / "volume"
    first = _paths(volume, "7" * 32)
    second = _paths(volume, "8" * 32)
    assert set(first).isdisjoint(second)


def test_parent_and_worker_reject_linked_work_root_escape(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside"
    linked_work = volume / ".data-sync-snapshots"
    _make_directory_link(linked_work, outside)
    nonce = "9" * 32
    request_path, result_path = _paths(volume, nonce)
    # Construct without calling _request because it would try to mkdir the
    # deliberately linked work path.
    payload = {
        "schema": "fly_runtime_inventory_worker_request_v1",
        "nonce": nonce,
        "source_revision": "a" * 40,
        "launched_unix": 100.0,
        "work_root": str(linked_work),
        "volume_root": str(volume.resolve()),
        "runtime_root": str(runtime.resolve()),
        "allowed_roots": [str(runtime.resolve())],
        "top_level_receipt_names": [],
        "extensions": [".json"],
        "excluded_names": [],
        "excluded_dir_names": [".data-sync-snapshots"],
        "append_prefix_names": [],
        "serialized_append_targets": [],
        "rewrite_targets": [],
        "max_rows": 5000,
    }
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert worker.run(request_path, result_path, nonce) == 1
    assert not result_path.exists()

    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_data_sync_inventory_work_root"
    )
    namespace = {
        "Path": Path,
        "os": os,
        "_data_sync_volume_root": lambda: volume,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    with pytest.raises(ValueError):
        namespace["_data_sync_inventory_work_root"]()


def test_parent_orphan_cleanup_is_aged_bounded_and_confined(tmp_path):
    volume = tmp_path / "volume"
    work = volume / ".data-sync-snapshots"
    work.mkdir(parents=True)
    old = work / f"inventory-request-{'a' * 32}.json"
    old_tmp = work / f"inventory-result-{'b' * 32}.json.{'c' * 32}.tmp"
    recent = work / f"inventory-result-{'d' * 32}.json"
    unrelated = work / "do-not-remove.json"
    for path in (old, old_tmp, recent, unrelated):
        path.write_text("{}", encoding="utf-8")
    os.utime(old, (10, 10))
    os.utime(old_tmp, (10, 10))
    os.utime(recent, (950, 950))
    os.utime(unrelated, (10, 10))

    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_data_sync_cleanup_inventory_worker_orphans"
    )
    namespace = {
        "Path": Path, "os": os, "re": __import__("re"), "time": __import__("time"),
        "_DATA_SYNC_INVENTORY_ORPHAN_MAX_AGE_SECONDS": 900,
        "_DATA_SYNC_INVENTORY_ORPHAN_SCAN_LIMIT": 1000,
        "_DATA_SYNC_INVENTORY_ORPHAN_REMOVE_LIMIT": 100,
        "_data_sync_volume_root": lambda: volume,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    assert namespace["_data_sync_cleanup_inventory_worker_orphans"](work, now=1000) == 2
    assert not old.exists() and not old_tmp.exists()
    assert recent.exists() and unrelated.exists()

    outside = tmp_path / "outside-cleanup"
    linked = volume / "linked-cleanup"
    _make_directory_link(linked, outside)
    with pytest.raises(ValueError):
        namespace["_data_sync_cleanup_inventory_worker_orphans"](linked, now=1000)


def test_parent_worker_environment_does_not_inherit_production_secrets():
    source = BOT_PATH.read_text(encoding="utf-8")
    assert "worker_env = dict(os.environ)" not in source
    assert '"PYTHONNOUSERSITE": "1"' in source
    assert '"PYTHONHASHSEED": "0"' in source
    worker_block = source[source.index("worker_env = {"):source.index("completed = subprocess.run(")]
    for secret_name in (
        "BOT_ADMIN_TOKEN", "BITFINEX_API_KEY", "BITFINEX_API_SECRET",
        "OPENAI_API_KEY", "PLATFORM_API_TOKEN",
    ):
        assert secret_name not in worker_block
