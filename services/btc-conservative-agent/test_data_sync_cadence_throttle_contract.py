import ast
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "bot.py"
WORKER_PATH = ROOT / "data_sync_inventory_worker.py"
LOOP_PATH = ROOT.parents[1] / "scripts" / "sync-fly-bot-data-loop.ps1"
WORKFLOW_PATH = ROOT.parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml"


def _load_worker():
    spec = importlib.util.spec_from_file_location("cadence_inventory_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker_request(volume: Path, nonce: str) -> tuple[Path, Path]:
    runtime = volume / "runtime"
    work = volume / ".data-sync-snapshots"
    runtime.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    request_path = work / f"inventory-request-{nonce}.json"
    result_path = work / f"inventory-result-{nonce}.json"
    request_path.write_text(json.dumps({
        "schema": "fly_runtime_inventory_worker_request_v1",
        "nonce": nonce,
        "source_revision": "a" * 40,
        "launched_unix": 1.0,
        "work_root": str(work.resolve()),
        "volume_root": str(volume.resolve()),
        "runtime_root": str(runtime.resolve()),
        "allowed_roots": [str(runtime.resolve())],
        "top_level_receipt_names": [],
        "extensions": [".json", ".jsonl"],
        "excluded_names": ["sync_inventory_current.json"],
        "excluded_dir_names": [".data-sync-snapshots"],
        "append_prefix_names": ["events.jsonl"],
        "serialized_append_targets": [],
        "rewrite_targets": [],
        "max_rows": 5000,
    }), encoding="utf-8")
    return request_path, result_path


def test_next_inventory_generation_detects_append_growth_and_new_file(tmp_path):
    """Exercise two real isolated scans, as consecutive cadence generations."""
    worker = _load_worker()
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "events.jsonl"
    ledger.write_bytes(b'{"generation":1}\n')

    request_1, result_1 = _worker_request(volume, "1" * 32)
    assert worker.run(request_1, result_1, "1" * 32) == 0
    first = json.loads(result_1.read_text(encoding="utf-8"))

    ledger.write_bytes(ledger.read_bytes() + b'{"generation":2}\n')
    (runtime / "new-evidence.json").write_text("{}", encoding="utf-8")
    request_2, result_2 = _worker_request(volume, "2" * 32)
    assert worker.run(request_2, result_2, "2" * 32) == 0
    second = json.loads(result_2.read_text(encoding="utf-8"))

    first_rows = {row["path"]: row for row in first["rows"]}
    second_rows = {row["path"]: row for row in second["rows"]}
    assert second_rows["events.jsonl"]["size"] > first_rows["events.jsonl"]["size"]
    assert "new-evidence.json" in second_rows
    assert second["rows_sha256"] != first["rows_sha256"]


def _async_inventory_function(state, monotonic_value):
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_data_sync_request_async_inventory"
    )
    starts = []

    class Thread:
        def __init__(self, **kwargs):
            starts.append(kwargs)

        def start(self):
            starts[-1]["started"] = True

    namespace = {
        "time": SimpleNamespace(monotonic=lambda: monotonic_value),
        "threading": SimpleNamespace(Thread=Thread),
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_async_inventory": state,
        "_data_sync_load_persisted_inventory_snapshot": lambda: None,
        "_data_sync_inventory_refresh_worker": lambda: None,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["_data_sync_request_async_inventory"], starts


def test_expired_current_inventory_is_withheld_and_revalidated_fail_closed():
    state = {
        "status": "CURRENT", "rows": [{"path": "old.json", "size": 1}],
        "generated_at": "old", "expires_at": 149.0, "refreshing": False,
        "error": None,
    }
    request_inventory, starts = _async_inventory_function(state, 150.0)
    result = request_inventory(force_refresh=False)
    assert result == {
        "status": "STALE_REVALIDATING", "rows": [],
        "generated_at": "old", "error": None,
    }
    assert state["status"] == "BUILDING"
    assert state["rows"] == [{"path": "old.json", "size": 1}]
    assert starts and starts[0]["started"] is True
    # A concurrent preflight also receives no rows that could authorize SKIP/MATCH.
    joined = request_inventory(force_refresh=False)
    assert joined["status"] == "STALE_REVALIDATING"
    assert joined["rows"] == []


def test_empty_or_building_inventory_is_withheld_until_current():
    state = {
        "status": "EMPTY", "rows": None, "generated_at": None,
        "expires_at": 0.0, "refreshing": False, "error": None,
    }
    request_inventory, starts = _async_inventory_function(state, 1.0)
    cold = request_inventory()
    assert cold["status"] == "BUILDING" and cold["rows"] == []
    assert starts and starts[0]["started"] is True
    building = request_inventory()
    assert building["status"] == "BUILDING" and building["rows"] == []


def test_low_requested_interval_is_clamped_for_poll_and_post_sync_sleep():
    """Execute the actual cadence and final sleep with a too-low caller value."""
    source = LOOP_PATH.read_text(encoding="utf-8")
    cadence = "\n".join(
        line for line in source.splitlines()
        if line.startswith("$minimumInventoryPollSec =")
        or line.startswith("$pollSec =")
    )
    start = source.index("    if ($didSync) {", source.index("Write-SizeReport"))
    end = source.index("\n    }\n  }\n} finally", start) + len("\n    }")
    branch = source[start:end]
    harness = (
        "function Start-Sleep { param([int]$Seconds) $script:observed=$Seconds }; "
        "$IntervalSec=30; " + cadence + "; $didSync=$true; " + branch +
        "; Write-Output \"$pollSec,$script:observed\""
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", harness],
        capture_output=True, text=True, timeout=15, check=True,
    )
    poll, post_sync = map(int, completed.stdout.strip().splitlines()[-1].split(","))
    assert poll == 180
    assert post_sync == 180


def test_default_cadence_is_bounded_to_180_seconds_and_cache_expires_first():
    source = LOOP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    ttl = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS" for target in node.targets)
    )
    assert "[int]$IntervalSec = 180" in source
    assert ttl == 150.0
    assert ttl < 180


def test_building_inventory_retries_through_a_modeled_fifty_second_scan():
    source = LOOP_PATH.read_text(encoding="utf-8")
    start = source.index("function Get-FlySyncPreflightManifest")
    end = source.index("\nfunction Invoke-OptionalRelayEvidenceSync", start)
    function_source = source[start:end]
    harness = (
        "$preflightManifestAttempts=13; $preflightManifestTimeoutSec=90; "
        "$preflightInventoryWaitMaxSec=120; $env:BOT_ADMIN_TOKEN='test'; "
        "$script:calls=0; $script:delays=@(); "
        "function Invoke-RestMethod { $script:calls += 1; "
        "if ($script:calls -lt 8) { throw 'inventory building' }; "
        "[pscustomobject]@{inventory_status='CURRENT'} }; "
        "function Start-Sleep { param([int]$Seconds) $script:delays += $Seconds }; "
        + function_source +
        "; $null=Get-FlySyncPreflightManifest -ManifestUri 'https://example.invalid'; "
        "Write-Output \"$script:calls,$(($script:delays | Measure-Object -Sum).Sum)\""
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", harness],
        capture_output=True, text=True, timeout=15, check=True,
    )
    calls, modeled_wait = map(
        int, completed.stdout.strip().splitlines()[-1].split(",")
    )
    assert calls == 8
    assert modeled_wait == 50


def _run_modeled_preflight(success_call: int | None) -> tuple[int, int, int]:
    source = LOOP_PATH.read_text(encoding="utf-8")
    start = source.index("function Get-FlySyncPreflightManifest")
    end = source.index("\nfunction Invoke-OptionalRelayEvidenceSync", start)
    function_source = source[start:end]
    success_condition = (
        f"$script:calls -lt {success_call}"
        if success_call is not None else "$true"
    )
    harness = (
        "$preflightManifestAttempts=13; $preflightManifestTimeoutSec=90; "
        "$preflightInventoryWaitMaxSec=120; $env:BOT_ADMIN_TOKEN='test'; "
        "$script:calls=0; $script:delays=@(); $script:failed=0; "
        "function Invoke-RestMethod { $script:calls += 1; "
        f"if ({success_condition}) {{ throw 'inventory building' }}; "
        "[pscustomobject]@{inventory_status='CURRENT'} }; "
        "function Start-Sleep { param([int]$Seconds) $script:delays += $Seconds }; "
        + function_source +
        "; try { $null=Get-FlySyncPreflightManifest -ManifestUri 'https://example.invalid' } "
        "catch { $script:failed=1 }; "
        "Write-Output \"$script:calls,$(($script:delays | Measure-Object -Sum).Sum),$script:failed\""
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", harness],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return tuple(map(int, completed.stdout.strip().splitlines()[-1].split(",")))


def test_building_inventory_can_complete_after_ninety_modeled_seconds():
    calls, modeled_wait, failed = _run_modeled_preflight(success_call=12)
    assert (calls, modeled_wait, failed) == (12, 90, 0)


def test_building_inventory_join_remains_bounded_when_worker_never_completes():
    calls, modeled_wait, failed = _run_modeled_preflight(success_call=None)
    assert calls == 13
    assert modeled_wait == 100
    assert modeled_wait <= 120
    assert failed == 1


def test_fly_deployment_workflow_executes_cadence_contract_suite():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '"services/btc-conservative-agent/test_data_sync_cadence_throttle_contract.py"' in workflow
    assert "python -m pytest test_data_sync_cadence_throttle_contract.py -q" in workflow
