import ast
import hashlib
import hmac
import importlib.util
import json
import subprocess
import sys
import threading
import uuid
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


def _generation_rows(result: dict) -> dict[str, dict]:
    assert result["status"] == "COMPLETE" and "rows" not in result
    index_path = Path(result["page_index_path"])
    assert hashlib.sha256(index_path.read_bytes()).hexdigest() == result["page_index_sha256"]
    rows = {}
    descriptors = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    assert len(descriptors) == result["page_count"]
    for descriptor in descriptors:
        page_path = Path(result["generation_dir"]) / descriptor["file_name"]
        raw = page_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == descriptor["page_sha256"]
        page = json.loads(raw)
        assert len(page["rows"]) <= result["page_size"]
        rows.update({row["path"]: row for row in page["rows"]})
    assert len(rows) == result["file_count"]
    return rows


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

    first_rows = _generation_rows(first)
    second_rows = _generation_rows(second)
    assert second_rows["events.jsonl"]["size"] > first_rows["events.jsonl"]["size"]
    assert "new-evidence.json" in second_rows
    assert second["generation_id"] != first["generation_id"]


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
        "_data_sync_retain_inventory_generation": lambda *args, **kwargs: "f" * 64,
        "_data_sync_inventory_refresh_worker": lambda: None,
        "hmac": hmac,
        "uuid": uuid,
        "utc_iso": lambda: "2026-09-01T00:00:00Z",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["_data_sync_request_async_inventory"], starts


def test_expired_current_inventory_is_served_stale_while_revalidating_fail_closed():
    state = {
        "status": "CURRENT", "rows": [{"path": "old.json", "size": 1}],
        "generated_at": "old", "expires_at": 149.0,
        "served_since_refresh": True, "refreshing": False,
        "error": None,
    }
    request_inventory, starts = _async_inventory_function(state, 150.0)
    result = request_inventory(force_refresh=False)
    assert result["status"] == "STALE_REVALIDATING"
    assert result["rows"] == [{"path": "old.json", "size": 1}]
    assert result["refreshing"] is True
    assert state["status"] == "BUILDING"
    assert state["rows"] == [{"path": "old.json", "size": 1}]
    assert starts and starts[0]["started"] is True
    # A concurrent preflight can inspect the prior generation but its stale
    # status remains ineligible to authorize SKIP/MATCH or acknowledgement.
    joined = request_inventory(force_refresh=False)
    assert joined["status"] == "STALE_REVALIDATING"
    assert joined["rows"] == [{"path": "old.json", "size": 1}]


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
        "$IntervalSec=30; " + cadence + "; $sleepSec=$pollSec; $didSync=$true; " + branch +
        "; Write-Output \"$pollSec,$script:observed\""
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", harness],
        capture_output=True, text=True, timeout=45, check=True,
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


def test_ordinary_poll_uses_identity_only_and_full_inventory_is_due_gated():
    """Identity polling uses the reserved endpoint and gates full inventory work."""
    source = LOOP_PATH.read_text(encoding="utf-8")
    identity_call = source.index('/api/data-sync/identity')
    due_gate = source.index('$needsFullInventory = $forceByTime -or $forceFresh -or $forceByRevision -or $forceByGrowth')
    full_call = source.index('-ManifestUri ($SourceUrl.TrimEnd("/") + "/api/data-sync/manifest")', due_gate)
    assert identity_call < due_gate < full_call
    assert 'reason = "identity_match_before_full_interval"' in source
    assert '$currentVolumeUsedBytes = [int64]$manifest.volume.used' in source
    assert '$volumeGrowthBytes -ge $thresholdBytes' in source
    assert 'growthBytes = $volumeGrowthBytes' in source
    assert 'currentTotalBytes = $null' in source


def _preflight_function_source(source: str) -> str:
    """Extract the production preflight and every helper it calls."""
    start = source.index("function Get-BoundedDiagnosticText")
    end = source.index("\nfunction Invoke-OptionalRelayEvidenceSync", start)
    return source[start:end]


def test_building_inventory_retries_through_a_modeled_fifty_second_scan():
    source = LOOP_PATH.read_text(encoding="utf-8")
    function_source = _preflight_function_source(source)
    harness = (
        "$preflightManifestAttempts=13; $preflightManifestTimeoutSec=90; "
        "$preflightInventoryStallMaxSec=120; $preflightInventoryWaitMaxSec=120; $env:BOT_ADMIN_TOKEN='test'; "
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
        capture_output=True, text=True, timeout=45, check=True,
    )
    calls, modeled_wait = map(
        int, completed.stdout.strip().splitlines()[-1].split(",")
    )
    assert calls == 8
    assert modeled_wait == 50


def _run_modeled_preflight(success_call: int | None) -> tuple[int, int, int]:
    source = LOOP_PATH.read_text(encoding="utf-8")
    function_source = _preflight_function_source(source)
    success_condition = (
        f"$script:calls -lt {success_call}"
        if success_call is not None else "$true"
    )
    harness = (
        "$preflightManifestAttempts=13; $preflightManifestTimeoutSec=90; "
        "$preflightInventoryStallMaxSec=120; $preflightInventoryWaitMaxSec=120; $env:BOT_ADMIN_TOKEN='test'; "
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
        capture_output=True, text=True, timeout=45, check=True,
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


def _run_progress_preflight(rows, *, stall=30, absolute=100):
    source = LOOP_PATH.read_text(encoding="utf-8")
    function_source = _preflight_function_source(source)
    encoded = json.dumps(rows, separators=(",", ":"))
    harness = (
        f"$preflightManifestAttempts=50; $preflightManifestTimeoutSec=90; "
        f"$preflightInventoryStallMaxSec={stall}; $preflightInventoryWaitMaxSec={absolute}; "
        "$env:BOT_ADMIN_TOKEN='test'; $script:calls=0; $script:elapsed=0; $script:uris=@(); "
        f"$script:rows=ConvertFrom-Json '{encoded}'; "
        "$preflightInventoryElapsedProvider={ $script:elapsed }; "
        "function Start-Sleep { param([int]$Seconds) $script:elapsed += $Seconds }; "
        "function Invoke-RestMethod { param([string]$Uri) $script:uris += $Uri; "
        "$row=$script:rows[[Math]::Min($script:calls,$script:rows.Count-1)]; "
        "$script:calls += 1; $row }; " + function_source +
        "; $script:failed=0; try { $null=Get-FlySyncPreflightManifest -ManifestUri "
        "'https://example.invalid/api/data-sync/manifest?fresh=1&nonce=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' } "
        "catch { $script:failed=1 }; $unique=($script:uris | Select-Object -Unique).Count; "
        "$nonceBound=[int](($script:uris | Where-Object { $_ -match "
        """'[?&]fresh=1&nonce=[0-9a-f]{32}$'""" + " }).Count -eq $script:uris.Count); "
        "Write-Output \"$script:calls,$script:elapsed,$script:failed,$unique,$nonceBound\""
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", harness],
        capture_output=True, text=True, timeout=45, check=True,
    )
    return tuple(map(int, completed.stdout.strip().splitlines()[-1].split(",")))


def _inventory(status="STALE_REVALIDATING", *, refreshing=True, phase="SCAN", files=0, error=None):
    return {
        "inventory_status": status,
        "inventory_build_status": "FAILED" if error else "BUILDING",
        "inventory_error": error,
        "inventory_worker": {
            "refreshing": refreshing, "phase": phase,
            "files_seen": files, "dirs_seen": files, "rows_discovered": files,
        },
    }


def test_progress_extends_same_nonce_join_until_current():
    rows = [_inventory(files=value) for value in (0, 10, 20, 30)] + [_inventory("CURRENT", refreshing=False)]
    calls, elapsed, failed, unique_uris, nonce_bound = _run_progress_preflight(rows, stall=15, absolute=100)
    assert calls == 5 and failed == 0
    assert elapsed > 15
    assert unique_uris == 1
    assert nonce_bound == 1


def test_unchanged_inventory_progress_fails_at_stall_deadline():
    calls, elapsed, failed, _, _ = _run_progress_preflight([_inventory(files=1)], stall=15, absolute=100)
    assert failed == 1 and elapsed >= 15
    assert calls < 50


def test_inventory_join_has_absolute_cap_despite_progress():
    rows = [_inventory(files=value) for value in range(50)]
    calls, elapsed, failed, _, _ = _run_progress_preflight(rows, stall=100, absolute=25)
    assert failed == 1 and elapsed <= 25
    assert calls < 50


def test_terminal_inventory_state_fails_immediately_and_never_accepts_stale():
    calls, elapsed, failed, _, _ = _run_progress_preflight([
        _inventory(error="RuntimeError", refreshing=False),
        _inventory("CURRENT", refreshing=False),
    ])
    assert (calls, elapsed, failed) == (1, 0, 1)


def test_fly_deployment_workflow_executes_cadence_contract_suite():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert '"services/btc-conservative-agent/test_data_sync_cadence_throttle_contract.py"' in workflow
    assert "python -m pytest test_data_sync_cadence_throttle_contract.py -q" in workflow
