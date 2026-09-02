import ast
import hashlib
import hmac
import importlib.util
import json
import os
import re
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


def _parent_validate_generation(result: dict, work_root: Path) -> dict:
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    wanted = {"_data_sync_file_sha256", "_data_sync_validate_disk_inventory_generation"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path, "hashlib": hashlib, "hmac": hmac, "json": json, "re": re,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["_data_sync_validate_disk_inventory_generation"](result, work_root)


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
        "__future__", "argparse", "hashlib", "hmac", "json", "os", "time", "uuid",
        "datetime", "pathlib", "resource", "shutil", "sqlite3",
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
    assert result["schema"] == "fly_runtime_inventory_worker_result_v2"
    assert result["status"] == "COMPLETE"
    assert result["nonce"] == nonce
    assert result["source_revision"] == "a" * 40
    rows = _generation_rows(result)
    assert result["file_count"] == len(rows) == 1
    assert rows[0]["size"] == len(b'{"a":1}\n')
    validated = _parent_validate_generation(result, result_path.parent)
    assert validated["storage"] == "disk_pages_v2"
    assert validated["generation_id"] == result["generation_id"]
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
    rows = _generation_rows(json.loads(result_path.read_text(encoding="utf-8")))
    assert [row["path"] for row in rows] == ["research/good.json"]


def test_worker_inventories_exact_safe_quarantine_artifacts_and_blocks_others(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    repair = runtime / "corrupt_evidence_quarantine" / "repair-1"
    repair.mkdir(parents=True)
    expected = {
        "execution_funnel.jsonl", "quarantine_manifest.json",
        "excluded_lines_unknown.json", "repair_receipt.json",
    }
    for name in expected:
        (repair / name).write_text("{}\n", encoding="utf-8")
    for name in ("admin_secret.json", "credential.json", ".env.json", "unsupported.bin"):
        (repair / name).write_text("blocked", encoding="utf-8")
    target = runtime / "research_archive" / "linked-target.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    linked = repair / "linked.json"
    linked_created = False
    try:
        linked.symlink_to(target)
        linked_created = True
    except OSError:
        pass

    nonce = "e" * 32
    request_path, result_path = _paths(volume, nonce)
    payload = _request(volume, nonce)
    payload["excluded_dir_names"].append("research_archive")
    assert "corrupt_evidence_quarantine" not in payload["excluded_dir_names"]
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert worker.run(request_path, result_path, nonce) == 0
    rows = _generation_rows(json.loads(result_path.read_text(encoding="utf-8")))
    prefix = "corrupt_evidence_quarantine/repair-1/"
    assert [row["path"] for row in rows] == [prefix + name for name in sorted(expected)]
    if linked_created:
        assert prefix + "linked.json" not in {row["path"] for row in rows}


def test_parent_contract_validates_v2_identity_pages_hashes_and_totals():
    source = BOT_PATH.read_text(encoding="utf-8")
    assert 'result.get("schema") != _DATA_SYNC_INVENTORY_WORKER_RESULT_SCHEMA' in source
    assert 'str(result.get("nonce") or ""), nonce' in source
    assert 'str(result.get("source_revision") or ""), _runtime_git_rev()' in source
    assert 'float(result.get("generated_unix") or 0.0) < launched_unix' in source
    assert 'completed.returncode == 75 and result.get("status") == "BUILDING"' in source
    assert '_data_sync_validate_disk_inventory_generation(' in source
    assert 're.fullmatch(r"[0-9a-f]{64}", generation_id)' in source
    assert '_data_sync_file_sha256(index_path)' in source
    assert 'descriptors_seen != page_count' in source
    assert 'indexed_files != file_count' in source
    assert 'indexed_bytes != total_bytes' in source


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


def _run_generation(worker, volume: Path, ordinal: int, payload: dict):
    nonce = f"{ordinal:032x}"
    request_path, result_path = _paths(volume, nonce)
    payload = dict(payload, nonce=nonce)
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    return worker.run(request_path, result_path, nonce), result_path


def _generation_rows(result: dict) -> list[dict]:
    index_path = Path(result["page_index_path"])
    assert hashlib.sha256(index_path.read_bytes()).hexdigest() == result["page_index_sha256"]
    rows = []
    descriptors = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    assert len(descriptors) == result["page_count"]
    for descriptor in descriptors:
        page_path = Path(result["generation_dir"]) / descriptor["file_name"]
        raw = page_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == descriptor["page_sha256"]
        page = json.loads(raw)
        assert page["file_count"] == len(page["rows"])
        assert len(page["rows"]) <= result["page_size"]
        rows.extend(page["rows"])
    return rows


def test_resumable_worker_never_publishes_a_partial_inventory(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    payload = _request(volume, "0" * 32)
    payload["max_rows"] = 7
    payload["inventory_directory_budget"] = 2
    for directory in range(4):
        target = runtime / f"d{directory}"
        target.mkdir(parents=True)
        for file_number in range(11):
            (target / f"evidence-{file_number:03d}.json").write_text("{}", encoding="utf-8")

    result = None
    for ordinal in range(1, 20):
        # Reloading models separate worker processes and proves the checkpoint,
        # not module memory, is the source of resumption.
        worker = _load_worker()
        returncode, result_path = _run_generation(worker, volume, ordinal, payload)
        if returncode == 0:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            break
        assert returncode == 75
        building = json.loads(result_path.read_text(encoding="utf-8"))
        assert building["status"] == "BUILDING"
        assert building["generation_id"] is None
        progress_path = next((volume / ".data-sync-snapshots").glob(
            "inventory-worker-v2-*.progress.json"
        ))
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        assert progress["complete"] is False
        assert progress["invocation_files_seen"] <= 7
        assert progress["invocation_dirs_seen"] <= 2

    assert result is not None
    rows = _generation_rows(result)
    assert result["file_count"] == len(rows) == 44
    assert len({row["path"] for row in rows}) == 44
    receipt = result["worker_receipt"]
    assert receipt["complete"] is True
    assert receipt["files_seen"] == 44
    assert receipt["dirs_seen"] == 5
    assert receipt["invocations"] > 1
    assert receipt["cpu_seconds"] >= 0
    assert receipt["peak_rss_bytes"] is None or receipt["peak_rss_bytes"] > 0
    work = volume / ".data-sync-snapshots"
    assert not list(work.glob("*.checkpoint.json"))
    assert not list(work.glob("*.sqlite3"))


def test_legacy_max_rows_is_a_slice_budget_not_a_silent_5000_cap(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    payload = _request(volume, "0" * 32)
    # Exceed the exact former production cap.  The first bounded slice cannot
    # publish; the resumed generation must contain every eligible file.
    for index in range(5003):
        (runtime / f"row-{index:05d}.json").write_text("{}", encoding="utf-8")
    result = None
    for ordinal in range(101, 110):
        code, result_path = _run_generation(_load_worker(), volume, ordinal, payload)
        current = json.loads(result_path.read_text(encoding="utf-8"))
        if code == 0:
            result = current
            break
        assert code == 75 and current["status"] == "BUILDING"
    assert result is not None
    rows = _generation_rows(result)
    assert result["file_count"] == len(rows) == 5003
    assert result["page_count"] == 21
    assert "rows" not in result and result_path.stat().st_size < 10_000
    assert rows[0]["path"] == "row-00000.json"
    assert rows[-1]["path"] == "row-05002.json"


def test_corrupt_checkpoint_fails_closed_then_recovers_from_quarantine(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    payload = _request(volume, "0" * 32)
    payload["max_rows"] = 2
    for index in range(5):
        (runtime / f"row-{index}.json").write_text("{}", encoding="utf-8")

    first_code, _ = _run_generation(worker, volume, 201, payload)
    assert first_code == 75
    work = volume / ".data-sync-snapshots"
    checkpoint = next(work.glob("*.checkpoint.json"))
    checkpoint.write_text('{"schema":"corrupt"}', encoding="utf-8")

    corrupt_code, corrupt_result = _run_generation(_load_worker(), volume, 202, payload)
    assert corrupt_code == 1
    assert json.loads(corrupt_result.read_text(encoding="utf-8"))["status"] == "FAILED"
    assert list(work.glob("*.checkpoint.json.corrupt-*"))
    assert list(work.glob("*.sqlite3.corrupt-*"))

    payload["max_rows"] = 100
    recovered_code, recovered_result = _run_generation(_load_worker(), volume, 203, payload)
    assert recovered_code == 0
    result = json.loads(recovered_result.read_text(encoding="utf-8"))
    assert result["file_count"] == 5


def test_corrupt_published_page_is_quarantined_and_rebuilt_atomically(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    payload = _request(volume, "0" * 32)
    (runtime / "evidence.json").write_text("{}", encoding="utf-8")

    first_code, first_path = _run_generation(worker, volume, 301, payload)
    assert first_code == 0
    first = json.loads(first_path.read_text(encoding="utf-8"))
    generation_dir = Path(first["generation_dir"])
    page = next(generation_dir.glob("p*.json"))
    page.write_text("corrupt", encoding="utf-8")

    second_code, second_path = _run_generation(_load_worker(), volume, 302, payload)
    assert second_code == 0
    second = json.loads(second_path.read_text(encoding="utf-8"))
    assert second["generation_id"] == first["generation_id"]
    assert _generation_rows(second)[0]["path"] == "evidence.json"
    quarantined = list(generation_dir.parent.glob(f"{generation_dir.name}.corrupt-*"))
    assert len(quarantined) == 1


def test_single_directory_has_a_hard_fail_closed_entry_bound(monkeypatch, tmp_path):
    worker = _load_worker()

    class Entry:
        def __init__(self, index):
            self.name = f"row-{index:05d}.json"
            self.path = str(tmp_path / self.name)

        def is_dir(self, **_):
            return False

        def is_file(self, **_):
            return True

    class Scan:
        def __enter__(self):
            return iter(Entry(index) for index in range(worker.MAX_DIRECTORY_ENTRIES + 1))

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(worker.os, "scandir", lambda _: Scan())
    with pytest.raises(worker.CheckpointError, match="directory entry hard limit exceeded"):
        worker._bounded_directory_entries(tmp_path)


def test_empty_inventory_publishes_one_valid_bounded_empty_page(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    nonce = "e" * 32
    request_path, result_path = _paths(volume, nonce)
    request_path.write_text(json.dumps(_request(volume, nonce)), encoding="utf-8")
    assert worker.run(request_path, result_path, nonce) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "COMPLETE"
    assert result["file_count"] == result["total_bytes"] == 0
    assert result["page_count"] == 1
    assert _generation_rows(result) == []


def test_effective_page_size_is_bound_into_resume_identity(tmp_path):
    worker = _load_worker()
    volume = tmp_path / "volume"
    nonce = "d" * 32
    request_path, result_path = _paths(volume, nonce)
    payload = _request(volume, nonce)
    payload["inventory_page_rows"] = 100
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    first = worker._load_request(request_path, result_path, nonce)
    first_fingerprint = worker._request_fingerprint(first)
    payload["inventory_page_rows"] = 250
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    second = worker._load_request(request_path, result_path, nonce)
    assert worker._request_fingerprint(second) != first_fingerprint


def test_sparse_approximately_1_2_gib_artifact_keeps_manifest_metadata_bounded(tmp_path, request):
    worker = _load_worker()
    volume = tmp_path / "volume"
    nonce = "f" * 32
    request_path, result_path = _paths(volume, nonce)
    payload = _request(volume, nonce)
    artifact = volume / "runtime" / "large-research-generation.json"
    logical_size = 1200 * 1024 * 1024
    with artifact.open("wb") as handle:
        handle.seek(logical_size - 1)
        handle.write(b"\0")
    request.addfinalizer(lambda: artifact.unlink(missing_ok=True))
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    assert worker.run(request_path, result_path, nonce) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    rows = _generation_rows(result)
    assert result["file_count"] == 1
    assert result["total_bytes"] == rows[0]["size"] == logical_size
    assert result_path.stat().st_size < 10_000
    page_path = next(Path(result["generation_dir"]).glob("p*.json"))
    assert page_path.stat().st_size < 64 * 1024
