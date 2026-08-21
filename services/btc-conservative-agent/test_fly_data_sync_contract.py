import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
from research.platform_relay_evidence import _validate_platform_relay_evidence_payload as pure_validate_relay


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
SYNC_SCRIPT = (ROOT.parents[1] / "scripts" / "sync-fly-bot-data.ps1").read_text(
    encoding="utf-8"
)
SYNC_LOOP = (ROOT.parents[1] / "scripts" / "sync-fly-bot-data-loop.ps1").read_text(
    encoding="utf-8"
)
RELAY_SYNC = (ROOT.parents[1] / "scripts" / "sync-platform-relay-evidence.ps1").read_text(
    encoding="utf-8"
)
ATOMIC_HELPER = ROOT.parents[1] / "scripts" / "fly-mirror-atomic.ps1"


def test_analyzer_manifest_timestamp_is_canonicalized_before_publication():
    assert "$analyzerGeneratedAt = [string]$reportManifest.generated_at" not in SYNC_SCRIPT
    assert "$analyzerGeneratedAtRaw = $reportManifest.generated_at" in SYNC_SCRIPT
    assert "$analyzerGeneratedAtRaw -is [DateTime]" in SYNC_SCRIPT
    assert "$analyzerGeneratedAtValue.ToUniversalTime().ToString(" in SYNC_SCRIPT
    assert "[Globalization.CultureInfo]::InvariantCulture" in SYNC_SCRIPT
    assert "$analyzerCommittedAtValue = [DateTimeOffset]$reportManifestItem.LastWriteTimeUtc" in SYNC_SCRIPT
    assert "$analyzerGeneratedAtValue.AddMinutes(30)" in SYNC_SCRIPT
    assert "$artifactModifiedAt -gt $analyzerCommittedAtValue.AddMinutes(1)" in SYNC_SCRIPT
    assert "$artifactModifiedAt -gt $analyzerGeneratedAtValue.AddMinutes(5)" not in SYNC_SCRIPT


def test_relay_evidence_timestamp_survives_powershell_json_date_conversion():
    assert "[DateTimeOffset]::TryParse([string]$payload.generatedAt" not in RELAY_SYNC
    assert "$generatedAtRaw = $payload.generatedAt" in RELAY_SYNC
    assert "$generatedAtRaw -is [DateTime]" in RELAY_SYNC
    assert "[Globalization.CultureInfo]::InvariantCulture" in RELAY_SYNC


def test_fresh_epoch_signal_receipt_has_a_literal_signal_key():
    assert "@{ signal_ts = $currentSignal" in SYNC_LOOP
    assert "@{$signal_ts" not in SYNC_LOOP


def test_sync_loop_has_sha256_fallback_for_minimal_windows_hosts():
    assert "Get-Command Get-FileHash -ErrorAction SilentlyContinue" in SYNC_LOOP
    assert "[System.Security.Cryptography.SHA256]::Create()" in SYNC_LOOP
    assert "[System.IO.File]::OpenRead($resolved)" in SYNC_LOOP


def _load_bot_functions(*names):
    tree = ast.parse(BOT)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "Path": Path,
        "os": os,
        "time": time,
        "_DATA_SYNC_EXTENSIONS": frozenset(
            {".csv", ".json", ".jsonl", ".log", ".db", ".sqlite", ".sqlite3", ".txt"}
        ),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset({
            "pipeline_events_3factor.csv", "trades_3factor.csv",
            "research_events_v22.jsonl",
        }),
        "_RESEARCH_RAW_JSONL_NEVER_PRUNE": frozenset({"research_events_v22.jsonl"}),
        "_pure_validate_platform_relay_evidence_payload": pure_validate_relay,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_data_sync_inventory_excludes_preserved_history_from_active_mirror():
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts",
        "_data_sync_path_allowed",
        "_data_sync_complete_record_size",
        "_data_sync_consistency_mode",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "signal_snapshot.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "research_events_v22.provisional.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (root / "collector_storage_state.json").write_text(
            "{}\n", encoding="utf-8"
        )
        excluded = [
            root / "research_epoch_quarantine" / "epoch_1" / "old.jsonl",
            root / "research_archive" / "session_1" / "old.json",
            root / "research_session_archives" / "old.json",
            root / "object_store" / "old.jsonl",
        ]
        for path in excluded:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        namespace = {
            "Path": Path,
            "os": os,
            "_DATA_SYNC_EXTENSIONS": frozenset({".json", ".jsonl"}),
            "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset({"pipeline_events_3factor.csv"}),
            "_DATA_SYNC_EXCLUDED_NAMES": frozenset({
                "manifest.json", "research_events_v22.provisional.json",
                "collector_storage_state.json",
            }),
            "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset({
                "research_epoch_quarantine", "research_archive",
                "research_session_archives", "archive-v2", "object-store", "object_store",
                "corrupt_evidence_quarantine",
            }),
            "_data_sync_volume_root": lambda: root,
            "_data_sync_runtime_root": lambda: root,
            "_data_sync_allowed_roots": lambda: [root],
            "_data_sync_relpath": lambda path: path.resolve().relative_to(root).as_posix(),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        rows = namespace["_data_sync_inventory"]()
        assert [row["path"] for row in rows] == ["signal_snapshot.jsonl"]
        for path in excluded:
            assert namespace["_data_sync_path_allowed"](path) is False


def test_data_sync_inventory_never_advertises_a_partial_jsonl_record():
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts",
        "_data_sync_path_allowed",
        "_data_sync_complete_record_size",
        "_data_sync_consistency_mode",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        complete = b'{"event":1}\n'
        target = root / "shadow_lane_outcome.jsonl"
        target.write_bytes(complete + b'{"event":2')
        namespace = {
            "Path": Path,
            "os": os,
            "_DATA_SYNC_EXTENSIONS": frozenset({".jsonl"}),
            "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset({"pipeline_events_3factor.csv"}),
            "_DATA_SYNC_EXCLUDED_NAMES": frozenset(),
            "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset(),
            "_data_sync_volume_root": lambda: root,
            "_data_sync_runtime_root": lambda: root,
            "_data_sync_allowed_roots": lambda: [root],
            "_data_sync_relpath": lambda path: path.resolve().relative_to(root).as_posix(),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        rows = namespace["_data_sync_inventory"]()
        assert rows[0]["size"] == len(complete)

        with target.open("ab") as handle:
            handle.write(b'}\n')
        rows = namespace["_data_sync_inventory"]()
        assert rows[0]["size"] == target.stat().st_size


def test_data_sync_generation_fence_rejects_every_generation_change():
    namespace = _load_bot_functions("_data_sync_generation_matches")
    matches = namespace["_data_sync_generation_matches"]
    original = SimpleNamespace(st_size=100, st_mtime_ns=200, st_ino=300)
    assert matches(original, size=100, mtime_ns=200, inode=300)
    assert not matches(original, size=101, mtime_ns=200, inode=300)
    assert not matches(original, size=100, mtime_ns=201, inode=300)
    assert not matches(original, size=100, mtime_ns=200, inode=301)


def test_only_declared_append_ledgers_use_append_prefix_mode():
    mode = _load_bot_functions("_data_sync_consistency_mode")["_data_sync_consistency_mode"]
    assert mode(Path("pipeline_events_3factor.csv")) == "append_prefix_v1"
    assert mode(Path("signal_snapshot.csv")) == "strict_generation_v1"
    assert mode(Path("research_events_v22.jsonl")) == "append_prefix_v1"


def test_v22_prefix_growth_is_allowed_but_inode_replacement_is_fenced():
    namespace = _load_bot_functions("_data_sync_append_prefix_matches")
    matches = namespace["_data_sync_append_prefix_matches"]
    current = SimpleNamespace(st_size=12_000_000, st_ino=41)
    assert matches(current, minimum_size=10_000_000, inode=41)
    assert not matches(current, minimum_size=10_000_000, inode=42)


def test_serialized_jsonl_writer_targets_are_append_prefix_eligible(tmp_path):
    namespace = _load_bot_functions("_data_sync_consistency_mode")
    target = (tmp_path / "reversal_study.jsonl").resolve()
    namespace["_jsonl_serialized_append_targets"] = {str(target)}
    assert namespace["_data_sync_consistency_mode"](target) == "append_prefix_v1"
    assert namespace["_data_sync_consistency_mode"](
        tmp_path / "unregistered.jsonl"
    ) == "strict_generation_v1"


def test_signal_snapshot_rewrite_stays_strict_even_when_append_registered(tmp_path):
    namespace = _load_bot_functions("_data_sync_consistency_mode")
    target = (tmp_path / "signal_snapshot.jsonl").resolve()
    namespace["SIGNAL_SNAPSHOT_FILE"] = str(target)
    namespace["_jsonl_serialized_append_targets"] = {str(target)}
    assert namespace["_data_sync_consistency_mode"](target) == "strict_generation_v1"


def test_signal_snapshot_patch_and_append_share_the_canonical_path_lock():
    patch_body = BOT[
        BOT.index("def patch_signal_snapshot_outcome("):
        BOT.index("def log_signal_snapshot(")
    ]
    assert "with _jsonl_path_lock(SIGNAL_SNAPSHOT_FILE), signal_snapshot_lock:" in patch_body


def test_signal_snapshot_concurrent_append_survives_outcome_patch(tmp_path):
    tree = ast.parse(BOT)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "patch_signal_snapshot_outcome"
    )
    target = tmp_path / "signal_snapshot.jsonl"
    target.write_text('{"trade_id":"first","executed":false}\n', encoding="utf-8")
    path_lock = threading.RLock()
    replacement_started = threading.Event()

    def atomic_replace(path, write_fn, _file_lock, _label):
        temp = str(path) + ".test.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            write_fn(handle)
        replacement_started.set()
        time.sleep(0.05)
        os.replace(temp, path)
        return True

    namespace = {
        "os": os,
        "json": json,
        "time": time,
        "SIGNAL_SNAPSHOT_FILE": str(target),
        "signal_snapshot_lock": threading.RLock(),
        "_jsonl_path_lock": lambda _path: path_lock,
        "_atomic_file_replace": atomic_replace,
        "logger": SimpleNamespace(error=lambda *_args: None, warning=lambda *_args: None),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bot.py", "exec"), namespace)

    patch_thread = threading.Thread(
        target=namespace["patch_signal_snapshot_outcome"],
        args=("first",),
        kwargs={"executed": True},
    )

    def append_second():
        assert replacement_started.wait(1)
        with path_lock, target.open("a", encoding="utf-8") as handle:
            handle.write('{"trade_id":"second","executed":false}\n')

    append_thread = threading.Thread(target=append_second)
    patch_thread.start()
    append_thread.start()
    patch_thread.join(2)
    append_thread.join(2)
    assert not patch_thread.is_alive()
    assert not append_thread.is_alive()
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"trade_id": "first", "executed": True, "outcome": rows[0]["outcome"]},
        {"trade_id": "second", "executed": False},
    ]


def test_every_static_serialized_jsonl_target_is_declared_before_first_write():
    tree = ast.parse(BOT)
    declared_constants = set()
    declared_literals = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Tuple):
            continue
        values = {
            item.value for item in node.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if target.id == "_JSONL_SERIALIZED_APPEND_CONSTANTS":
            declared_constants = values
        elif target.id == "_JSONL_SERIALIZED_APPEND_LITERALS":
            declared_literals = values

    observed_constants = set()
    observed_literals = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_safe_append_jsonl" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id != "output_file":
            observed_constants.add(first.id)
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            observed_literals.add(first.value)

    assert observed_constants <= declared_constants
    assert observed_literals <= declared_literals
    assert "FILL_QUALITY_FILE" in declared_constants
    assert "TYPE_B_RESEARCH_V2_EVENT_FILE" in declared_constants
    assert {"execution_funnel.jsonl", "retired_lane_violations.jsonl"} <= declared_literals


def test_dynamic_csv_schema_expansion_is_an_atomic_inode_change():
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_atomic_write_csv_rows", "_dynamic_csv_writer_once"
        }
    ]
    namespace = {
        "os": os, "csv": __import__("csv"), "threading": threading,
        "safe_csv_row": lambda row: dict(row),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "pipeline_events_3factor.csv"
        namespace["_dynamic_csv_writer_once"](str(target), {"a": 1})
        first_inode = target.stat().st_ino
        namespace["_dynamic_csv_writer_once"](str(target), {"a": 2, "b": 3})
        assert target.stat().st_ino != first_inode
        rows = list(__import__("csv").DictReader(target.open(encoding="utf-8")))
        assert rows == [{"a": "1", "b": ""}, {"a": "2", "b": "3"}]


def test_trend_health_and_golden_stack_use_serialized_ledger_writers():
    assert "dynamic_csv_writer(TREND_HEALTH_CSV_FILE" in BOT
    assert '_safe_append_jsonl(GOLDEN_STACK_REJECTIONS_FILE, row, label="GS_REJECTION")' in BOT
    assert 'label="GS_REJECTION_OUTCOME"' in BOT
    assert '"trend_health.csv"' in BOT.split("_DATA_SYNC_APPEND_PREFIX_NAMES", 1)[1].split("})", 1)[0]


def test_append_prefix_mode_is_narrow_and_allows_only_same_inode_growth():
    namespace = _load_bot_functions(
        "_data_sync_consistency_mode", "_data_sync_append_prefix_matches"
    )
    mode = namespace["_data_sync_consistency_mode"]
    matches = namespace["_data_sync_append_prefix_matches"]
    assert mode(Path("bot_runtime.log")) == "append_prefix_v1"
    assert mode(Path("research_events_v22.jsonl")) == "append_prefix_v1"
    assert mode(Path("trades_3factor.csv")) == "append_prefix_v1"
    assert mode(Path("state.json")) == "strict_generation_v1"
    grown = SimpleNamespace(st_size=130, st_mtime_ns=999, st_ino=7)
    assert matches(grown, minimum_size=100, inode=7)
    assert not matches(grown, minimum_size=131, inode=7)
    assert not matches(grown, minimum_size=100, inode=8)


def _load_jsonl_writer(tmp_path):
    tree = ast.parse(BOT)
    wanted = {"_jsonl_path_lock", "_validate_or_quarantine_jsonl", "_safe_append_jsonl"}
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]

    class _Logger:
        def __init__(self):
            self.messages = []

        def error(self, message):
            self.messages.append(str(message))

    namespace = {
        "os": os,
        "json": json,
        "time": time,
        "uuid": uuid,
        "hashlib": hashlib,
        "threading": threading,
        "datetime": datetime,
        "timezone": timezone,
        "CSV_WRITE_RETRIES": 3,
        "CSV_WRITE_RETRY_BASE_SEC": 0,
        "_jsonl_append_locks_guard": threading.Lock(),
        "_jsonl_append_locks": {},
        "_jsonl_validated_targets": set(),
        "_jsonl_serialized_append_targets": set(),
        "rotate_log": lambda _path: None,
        "_transient_csv_lock_error": lambda _error: False,
        "_csv_write_fallback": lambda *_args: None,
        "utc_iso": lambda: datetime.now(timezone.utc).isoformat(),
        "logger": _Logger(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_jsonl_writer_serializes_concurrent_rows(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    append = namespace["_safe_append_jsonl"]
    target = tmp_path / "shadow_lane_outcome.jsonl"
    threads = [
        threading.Thread(target=append, args=(str(target), {"row": index}, "SHADOW"))
        for index in range(32)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 32
    assert {row["row"] for row in rows} == set(range(32))


def test_jsonl_writer_quarantines_corrupt_bytes_with_receipt(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "shadow_lane_outcome.jsonl"
    corrupt = b'{"row": 1}\n{"row":'
    target.write_bytes(corrupt)

    assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "SHADOW")
    assert [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()] == [
        {"row": 2}
    ]

    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    preserved = quarantine_dirs[0] / target.name
    receipt = json.loads(
        (quarantine_dirs[0] / "quarantine_manifest.json").read_text(encoding="utf-8")
    )
    assert preserved.read_bytes() == corrupt
    assert receipt["complete"] is True
    assert receipt["bad_line"] == 2
    assert receipt["size_bytes"] == len(corrupt)
    assert receipt["sha256"] == hashlib.sha256(corrupt).hexdigest()
    assert receipt["preserved_path"] == target.name


def test_jsonl_writer_is_serialized_and_corruption_is_preserved_not_deleted():
    assert "with _jsonl_path_lock(path):" in BOT
    assert "_validate_or_quarantine_jsonl(path, label)" in BOT
    assert '"schema": "corrupt_jsonl_quarantine_v1"' in BOT
    assert '"sha256": digest.hexdigest()' in BOT
    assert "os.replace(key, target)" in BOT
    assert '"corrupt_evidence_quarantine"' in BOT
    assert "os.fsync(f.fileno())" in BOT


def test_all_direct_append_opens_are_bounded_writer_internals_or_diagnostics():
    """Research ledgers must use the shared serialized JSONL/CSV writers."""
    tree = ast.parse(BOT)
    direct_append_functions = set()
    for function in (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name) or call.func.id != "open":
                continue
            mode = None
            if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                mode = call.args[1].value
            for keyword in call.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if isinstance(mode, str) and "a" in mode:
                direct_append_functions.add(function.name)
    assert direct_append_functions == {
        "_agent_dbg",                 # bounded ordinary diagnostic log
        "_dynamic_csv_writer_once",   # serialized by dynamic_csv_writer/csv_lock
        "_safe_append_jsonl",         # serialized by the per-path JSONL lock
        "dump_system_state",          # crash diagnostics, not research evidence
    }


def test_csv_fallback_uses_non_recursive_serialized_jsonl_writer():
    assert '_safe_append_jsonl(\n            CSV_FALLBACK_JSONL' in BOT
    assert 'label="CSV_FALLBACK", fallback_on_error=False' in BOT
    assert "if fallback_on_error:\n        _csv_write_fallback(path, row, last_err)" in BOT
    assert "_jsonl_serialized_append_targets.add(os.path.abspath(path))" in BOT


def test_sync_refetches_only_a_bounded_changed_generation_from_byte_zero():
    assert "$generationRefreshCount = 0" in SYNC_SCRIPT
    assert "$generationRefreshCount -lt 3" in SYNC_SCRIPT
    assert "-match 'generation changed'" in SYNC_SCRIPT
    assert 'Invoke-RestMethod -Uri "$base/api/data-sync/manifest"' in SYNC_SCRIPT
    assert 'Where-Object { [string]$_.path -eq $rel }' in SYNC_SCRIPT
    assert "$sameGeneration = $false" in SYNC_SCRIPT
    assert "$fullReplaceRetry = $true" in SYNC_SCRIPT
    assert "restarting the candidate from byte zero" in SYNC_SCRIPT
    # Other 409s (mode mismatch, unsafe path, malformed request) must continue
    # to fail closed rather than being hidden by a broad retry.
    assert "-match '^Fly sync HTTP 409 '" in SYNC_SCRIPT


def test_shadow_jsonl_is_validated_before_startup_collection_and_sync():
    assert "def _validate_research_ledgers_on_startup():" in BOT
    assert '(SHADOW_LANE_OUTCOME_FILE, "SHADOW_LANE_STARTUP")' in BOT
    assert '(SIGNAL_REPLAY_FILE, "SIGNAL_REPLAY_STARTUP")' in BOT
    assert '_safe_append_jsonl(SIGNAL_REPLAY_FILE, replay, label="SIGNAL_REPLAY")' in BOT
    main_start = BOT.index("def main():")
    assert BOT.index("_validate_research_ledgers_on_startup()", main_start) < BOT.index(
        "_restore_collector_v22_provisionals()", main_start
    )


def test_fly_runtime_cwd_is_volume_backed():
    assert 'RUNTIME_DIR="$DATA_DIR/runtime"' in ENTRYPOINT
    assert 'export BOT_SINGLETON_DIR="$DATA_DIR/locks"' in ENTRYPOINT
    assert 'cd "$RUNTIME_DIR"' in ENTRYPOINT
    assert "python /app/btc_conservative_agent.py" in ENTRYPOINT
    assert "python btc_conservative_agent.py" not in ENTRYPOINT


def test_incremental_sync_is_authenticated_and_chunk_verified():
    assert "@app.route('/api/data-sync/manifest')" in BOT
    assert "@app.route('/api/data-sync/file')" in BOT
    assert "@app.route('/api/data-sync/ack', methods=['POST'])" in BOT
    assert 'response.headers["X-Chunk-Sha256"]' in BOT
    assert '"physical_size": int(stat.st_size)' in BOT
    assert '"file generation changed after manifest"' in BOT
    assert '"file generation changed during download"' in BOT
    assert "/api/data-sync/manifest" not in BOT[BOT.index("_READ_ONLY_GET_PATHS"):BOT.index("def _client_ip")]
    assert '"X-Bot-Admin-Token" = $AdminToken' in SYNC_SCRIPT
    assert "Chunk checksum mismatch" in SYNC_SCRIPT
    assert "Fly sync chunk failed for $rel at offset $offset limit $limit" in SYNC_SCRIPT
    assert "after $attempt/3 attempt(s): $($_.Exception.Message)" in SYNC_SCRIPT
    assert "expected_physical_size=$expectedPhysicalSize" in SYNC_SCRIPT
    assert "expected_published_size=$expectedPublishedSize" in SYNC_SCRIPT
    assert "consistency_mode=$consistencyMode" in SYNC_SCRIPT
    assert "expected_mtime_ns=$expectedMtime" in SYNC_SCRIPT
    assert "expected_inode=$expectedInode" in SYNC_SCRIPT
    assert "$chunkLimit = 4MB" in SYNC_SCRIPT
    assert '$appendOnly = $extension -in @(".jsonl", ".csv", ".log", ".txt")' in SYNC_SCRIPT
    assert "[int64]$previous.mtime_ns -eq [int64]$row.mtime_ns" in SYNC_SCRIPT
    assert "def _data_sync_rotation_parts" in BOT
    assert '"consistency_mode": _data_sync_consistency_mode(resolved)' in BOT
    assert 'path.name in _DATA_SYNC_APPEND_PREFIX_NAMES' in BOT
    assert 'return "append_prefix_v1" if append_prefix' in BOT
    assert 'limit = min(limit, max(0, published_boundary - offset))' in BOT
    assert "_data_sync_rotation_parts(resolved.name) is not None" in BOT
    assert 'path.startswith("/api/data-sync/")' in BOT
    assert "and not is_authenticated_data_sync" in BOT
    assert "@app.route('/api/data-sync/platform-relay-evidence', methods=['POST'])" in BOT
    assert "def _validate_platform_relay_evidence_payload" in BOT
    assert "os.replace(temp, destination)" in BOT


def test_local_mirror_download_is_validated_then_atomically_published():
    assert '. (Join-Path $scriptDir "fly-mirror-atomic.ps1")' in SYNC_SCRIPT
    assert "Test-MirrorCandidate -Path $candidate" in SYNC_SCRIPT
    assert "Publish-MirrorCandidate -Candidate $candidate -Destination $local" in SYNC_SCRIPT
    assert "[System.IO.File]::Copy($local, $candidate, $true)" in SYNC_SCRIPT
    assert "if (-not ($sameGeneration -and $localSize -eq $remoteSize))" in SYNC_SCRIPT
    assert "retrying as a complete atomic replace without deleting prior valid records." in SYNC_SCRIPT
    assert "$fullReplaceRetry = $true" in SYNC_SCRIPT
    assert "[System.IO.File]::Replace($Candidate, $Destination" in ATOMIC_HELPER.read_text(encoding="utf-8")
    assert "Remove-Item -LiteralPath $local -Force" not in SYNC_SCRIPT


def test_invalid_jsonl_candidate_preserves_previous_mirror_and_valid_candidate_replaces_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        destination = root / "signal_snapshot.jsonl"
        invalid = root / "invalid.download"
        valid = root / "valid.download"
        destination.write_text('{"trade_id":"old"}\n', encoding="utf-8")
        invalid.write_text('{"trade_id":"partial"}', encoding="utf-8")
        valid.write_text('{"trade_id":"new"}\n', encoding="utf-8")
        command = (
            f". '{ATOMIC_HELPER}'; "
            f"$dest='{destination}'; $invalid='{invalid}'; $valid='{valid}'; "
            "$failed=$false; try { Test-MirrorCandidate -Path $invalid -RelativePath 'signal_snapshot.jsonl'; "
            "Publish-MirrorCandidate -Candidate $invalid -Destination $dest } catch { $failed=$true }; "
            "$before=[IO.File]::ReadAllText($dest); "
            "Test-MirrorCandidate -Path $valid -RelativePath 'signal_snapshot.jsonl'; "
            "Publish-MirrorCandidate -Candidate $valid -Destination $dest; "
            "$after=[IO.File]::ReadAllText($dest); "
            "@{failed=$failed;before=$before;after=$after}|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout.strip())
    result = {key: value.replace("\r\n", "\n") if isinstance(value, str) else value for key, value in result.items()}
    assert result == {
        "after": '{"trade_id":"new"}\n',
        "before": '{"trade_id":"old"}\n',
        "failed": True,
    }


def test_atomic_publish_retries_a_transient_windows_reader_lock_without_losing_either_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        destination = root / "locked.jsonl"
        candidate = root / "candidate.download"
        marker = root / "locked.marker"
        destination.write_text('{"version":"old"}\n', encoding="utf-8")
        candidate.write_text('{"version":"new"}\n', encoding="utf-8")
        command = (
            f". '{ATOMIC_HELPER}'; $dest='{destination}'; $candidate='{candidate}'; $marker='{marker}'; "
            "$job=Start-Job -ScriptBlock { param($p,$m) "
            "$s=[IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read); "
            "[IO.File]::WriteAllText($m,'locked'); Start-Sleep -Milliseconds 550; $s.Dispose() "
            "} -ArgumentList $dest,$marker; "
            "$until=(Get-Date).AddSeconds(5); while (!(Test-Path -LiteralPath $marker) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 20 }; "
            "Publish-MirrorCandidate -Candidate $candidate -Destination $dest -ReplaceAttempts 8; "
            "$job|Wait-Job|Remove-Job; [IO.File]::ReadAllText($dest)"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert completed.stdout.replace("\r\n", "\n").strip() == '{"version":"new"}'


def test_atomic_publish_lock_failure_names_the_destination_and_preserves_candidate():
    helper = ATOMIC_HELPER.read_text(encoding="utf-8")
    assert "Atomic mirror publish failed after $attempt attempt(s): destination=$Destination" in helper
    assert "retry only the atomic publish operation" in helper
    assert "-Attempts 12" in SYNC_SCRIPT


def test_platform_relay_evidence_validation_rejects_wrong_scope_and_duplicate_events():
    validate = _load_bot_functions("_validate_platform_relay_evidence_payload")[
        "_validate_platform_relay_evidence_payload"
    ]
    base = {
        "schema": "relay_lifecycle_evidence_v1",
        "generatedAt": "2026-08-16T00:00:00Z",
        "generatingRevision": "a" * 40,
        "runIdentity": "run-1",
        "agentSlug": "conservative-btc",
        "userId": "user-1",
        "records": [{
            "canonicalTradeId": "cont-1",
            "lifecycleId": "cycle-1",
            "participantId": "participant-1",
            "events": [{"id": "event-1", "eventType": "FILLED", "createdAt": "2026-08-16T00:00:01Z"}],
        }],
    }
    assert validate(base) == (True, "OK")
    wrong = json.loads(json.dumps(base))
    wrong["agentSlug"] = "other-agent"
    assert validate(wrong) == (False, "SCOPE_INVALID")
    duplicate = json.loads(json.dumps(base))
    duplicate["records"].append({
        "canonicalTradeId": "cont-2", "lifecycleId": "cycle-2", "participantId": "participant-2",
        "events": [{"id": "event-1", "eventType": "EXIT", "createdAt": "2026-08-16T00:00:02Z"}],
    })
    assert validate(duplicate) == (False, "DUPLICATE_EVENT")


def test_local_sync_has_fail_closed_30_gib_admission_guard():
    assert "[int]$MaxLocalMirrorGiB = 30" in SYNC_SCRIPT
    assert "$currentMirrorBytes + $incomingGrowth" in SYNC_SCRIPT
    assert "Local Fly mirror hard cap would be exceeded" in SYNC_SCRIPT
    assert "fingerprinted receipts" in SYNC_SCRIPT


def test_local_sync_removes_only_manifest_absent_top_level_raw_research_files():
    assert "stale local Fly research file" in SYNC_SCRIPT
    assert "\\.(jsonl|log|csv)(?:\\.\\d+)?$" in SYNC_SCRIPT
    assert "$manifestPaths.Contains($candidate.Name)" in SYNC_SCRIPT
    assert "Get-ChildItem -LiteralPath $targetRoot -File" in SYNC_SCRIPT
    assert "[System.IO.File]::Delete($resolvedCandidate)" in SYNC_SCRIPT
    assert "[void]$syncState.Remove($candidate.Name)" in SYNC_SCRIPT


def test_retention_never_removes_active_or_unacknowledged_files():
    assert "active/unacked files retained" in BOT
    assert "newest_kept = frozenset(sorted(generations)[-keep_newest:])" in BOT
    assert "if rotation_index in newest_kept" in BOT
    assert "int(ack.get(\"size\") or -1) == int(stat.st_size)" in BOT
    assert "int(ack.get(\"mtime_ns\") or -1) == int(stat.st_mtime_ns)" in BOT
    assert "volume_used_pct" in BOT


def test_numbered_rotations_are_supported_and_highest_two_are_retained():
    namespace = _load_bot_functions(
        "_data_sync_rotation_parts",
        "_prune_acknowledged_rotations",
    )
    rotation_parts = namespace["_data_sync_rotation_parts"]
    assert rotation_parts("signal_replay.jsonl.28") == ("signal_replay.jsonl", 28)
    assert rotation_parts("bot_runtime.log.3") == ("bot_runtime.log", 3)
    assert rotation_parts("credential.bin.4") is None
    assert rotation_parts("signal_replay.jsonl.tmp") is None

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        active = root / "signal_replay.jsonl"
        active.write_text("active\n", encoding="utf-8")
        old_stamp = time.time() - (48 * 3600)
        acks = {}
        for generation in (2, 7, 9, 11):
            path = root / f"signal_replay.jsonl.{generation}"
            path.write_text(f"rotation-{generation}\n", encoding="utf-8")
            os.utime(path, (old_stamp, old_stamp))
            if generation != 11:  # newest rotation deliberately remains unacknowledged
                stat = path.stat()
                acks[path.name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

        namespace["_data_sync_resolve_relpath"] = lambda rel: root / rel
        namespace["_data_sync_path_allowed"] = lambda path: path.is_file()
        removed = namespace["_prune_acknowledged_rotations"](acks)

        assert sorted(removed) == ["signal_replay.jsonl.2", "signal_replay.jsonl.7"]
        assert active.read_text(encoding="utf-8") == "active\n"
        assert (root / "signal_replay.jsonl.9").is_file()
        assert (root / "signal_replay.jsonl.11").is_file()


def test_remote_analyzer_mirror_is_read_only_and_admin_gated():
    assert "@app.route('/api/data-sync/analyzer-report', methods=['POST'])" in BOT
    assert "@app.route('/analysis')" in BOT
    assert "@app.route('/analysis/')" in BOT
    assert "@app.route('/analysis/<path:artifact_path>')" in BOT
    assert "if not _admin_authed_strict()" in BOT
    assert '$form.Add($content, "bundle", "analyzer_bundle.zip")' in SYNC_SCRIPT
    assert '"reports/$name"' in SYNC_SCRIPT
    assert '"report_manifest.json"' in SYNC_SCRIPT
    assert "$reportManifest.text_artifacts" in SYNC_SCRIPT
    assert "Required analyzer artifact is missing" in SYNC_SCRIPT
    assert "outside the committed run window" in SYNC_SCRIPT
    assert "metadata does not match the snapshotted file" in SYNC_SCRIPT
    assert "analysis_provenance.cohort_schema" in SYNC_SCRIPT
    assert 'schema = "analyzer_mirror_bundle_v2"' in SYNC_SCRIPT
    assert 'app.config["MAX_CONTENT_LENGTH"]' in BOT


def _load_analyzer_bundle_validators():
    namespace = {
        "Path": Path,
        "zipfile": zipfile,
        "json": json,
        "re": re,
        "datetime": datetime,
        "hashlib": hashlib,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        "_ANALYZER_BUNDLE_MAX_EXPANDED_BYTES": 150 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_MEMBERS": 256,
        "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_COMPRESSION_RATIO": 1000,
        "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json",
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
    }
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_safe_analyzer_bundle_members", "_validated_analyzer_bundle_manifest"}
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def _ensure_source_report_manifest(files):
    if files.get("report_manifest.json") not in (None, b"{}"):
        return
    text_artifacts = sorted(
        path for path in files
        if path != "report_manifest.json" and not path.startswith("reports/")
    )
    reports = [
        {"file": path.removeprefix("reports/")}
        for path in sorted(files)
        if path.startswith("reports/")
    ]
    files["report_manifest.json"] = json.dumps(
        {
            "schema": "report_manifest_v1",
            "analyzer_sync_id": "analyzer-v1",
            "analyzer_version": "analyzer-v1",
            "generated_at": "2026-08-16T00:00:00+00:00",
            "data_scope": "session",
            "session_scope": "SESSION",
            "analysis_provenance": {
                "cohort_schema": "analysis_cohorts_v1",
                "generation_revision": "b" * 40,
            },
            "report_count": len(reports),
            "reports": reports,
            "text_artifacts": text_artifacts,
        },
        sort_keys=True,
    ).encode()


def _bundle_manifest(files):
    _ensure_source_report_manifest(files)
    return {
        "schema": "analyzer_mirror_bundle_v2",
        "snapshot_id": "fixture-run-1",
        "analyzer_run_id": "analyzer-v1",
        "analyzer_version": "analyzer-v1",
        "analyzer_generated_at": "2026-08-16T00:00:00+00:00",
        "source_data_revision": "a" * 40,
        "analyzer_generation_revision": "b" * 40,
        "cohort_schema": "analysis_cohorts_v1",
        "data_scope": "session",
        "session_scope": "SESSION",
        "source_report_manifest_sha256": hashlib.sha256(files["report_manifest.json"]).hexdigest(),
        "files": [
            {
                "path": path,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in files.items()
        ],
    }


def _zip_bundle(files, manifest=None):
    _ensure_source_report_manifest(files)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
        archive.writestr(
            "bundle_manifest.json",
            json.dumps(manifest if manifest is not None else _bundle_manifest(files)),
        )
    return payload.getvalue()


def test_analyzer_bundle_validation_fails_closed_for_missing_dashboard_and_unsafe_paths():
    namespace = _load_analyzer_bundle_validators()
    validate = namespace["_safe_analyzer_bundle_members"]

    missing = io.BytesIO()
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("executive_summary.txt", "summary")
    with zipfile.ZipFile(io.BytesIO(missing.getvalue()), "r") as archive:
        try:
            validate(archive)
        except ValueError as exc:
            assert "missing bundle_manifest.json" in str(exc)
        else:
            raise AssertionError("bundle without dashboard must fail closed")

    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("analysis_dashboard.html", "dashboard")
        archive.writestr("bundle_manifest.json", "{}")
        archive.writestr("../secret.txt", "no")
    with zipfile.ZipFile(io.BytesIO(traversal.getvalue()), "r") as archive:
        try:
            validate(archive)
        except ValueError as exc:
            assert "unsafe path" in str(exc)
        else:
            raise AssertionError("path traversal must fail closed")


def test_analyzer_bundle_accepts_complete_read_only_report_tree():
    namespace = _load_analyzer_bundle_validators()
    files = {
        "analysis_dashboard.html": b'<a href="executive_summary.txt">summary</a>',
        "executive_summary.txt": b"summary",
        "report_manifest.json": b"{}",
        "reports/ai_calibration_report.json": b"{}",
    }
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = namespace["_safe_analyzer_bundle_members"](archive)
        manifest = namespace["_validated_analyzer_bundle_manifest"](archive, members)
    assert manifest["snapshot_id"] == "fixture-run-1"
    assert {str(rel).replace("\\", "/") for _, rel in members} == {
        "analysis_dashboard.html",
        "executive_summary.txt",
        "report_manifest.json",
        "reports/ai_calibration_report.json",
        "bundle_manifest.json",
    }


def test_analyzer_bundle_rejects_missing_extra_duplicate_and_bad_hash_members():
    namespace = _load_analyzer_bundle_validators()
    files = {"analysis_dashboard.html": b"dashboard", "executive_summary.txt": b"summary"}
    manifest = _bundle_manifest(files)
    manifest["files"][0]["sha256"] = "0" * 64
    payload = _zip_bundle(files, manifest)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = namespace["_safe_analyzer_bundle_members"](archive)
        # Structural validation succeeds; extraction performs the final content hash check.
        parsed = namespace["_validated_analyzer_bundle_manifest"](archive, members)
        assert parsed["files"][0]["sha256"] == "0" * 64

    missing_manifest = _bundle_manifest(files)
    missing_manifest["files"] = missing_manifest["files"][:-1]
    payload = _zip_bundle(files, missing_manifest)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = namespace["_safe_analyzer_bundle_members"](archive)
        try:
            namespace["_validated_analyzer_bundle_manifest"](archive, members)
        except ValueError as exc:
            assert "membership" in str(exc)
        else:
            raise AssertionError("undeclared archive member must fail closed")

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("analysis_dashboard.html", "one")
        archive.writestr("ANALYSIS_DASHBOARD.HTML", "two")
        archive.writestr("bundle_manifest.json", "{}")
    with zipfile.ZipFile(io.BytesIO(duplicate.getvalue()), "r") as archive:
        try:
            namespace["_safe_analyzer_bundle_members"](archive)
        except ValueError as exc:
            assert "duplicate path" in str(exc)
        else:
            raise AssertionError("case-colliding archive members must fail closed")


def test_analyzer_bundle_install_is_atomic_and_bad_hash_preserves_current_generation():
    tree = ast.parse(BOT)
    wanted = {
        "_valid_analyzer_generation",
        "_recover_latest_analyzer_generation",
        "_active_analyzer_mirror_dir",
        "_safe_analyzer_bundle_members",
        "_validated_analyzer_bundle_manifest",
        "_install_analyzer_bundle",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path,
        "zipfile": zipfile,
        "json": json,
        "re": re,
        "io": io,
        "os": os,
        "time": time,
        "shutil": shutil,
        "hashlib": hashlib,
        "datetime": datetime,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        "_ANALYZER_BUNDLE_MAX_EXPANDED_BYTES": 150 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_MEMBERS": 256,
        "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_COMPRESSION_RATIO": 1000,
        "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json",
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
        "_ANALYZER_INSTALL_LOCK": threading.RLock(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generations = root / "generations"
        pointer = root / "current.json"
        namespace["_analyzer_generations_dir"] = lambda: generations
        namespace["_analyzer_current_pointer_path"] = lambda: pointer
        namespace["_analyzer_mirror_dir"] = lambda: root / "legacy"
        namespace["_prune_analyzer_generations"] = lambda generation: None
        files = {
            "analysis_dashboard.html": b"dashboard-v1",
            "executive_summary.txt": b"summary-v1",
        }
        installed = namespace["_install_analyzer_bundle"](_zip_bundle(files), {"uploaded_at": "now"})
        assert installed["complete"] is True
        first_pointer = json.loads(pointer.read_text(encoding="utf-8"))
        first_generation = generations / first_pointer["generation"]
        assert (first_generation / "analysis_dashboard.html").read_bytes() == b"dashboard-v1"
        assert namespace["_active_analyzer_mirror_dir"]() == first_generation

        pointer.write_text("not-json", encoding="utf-8")
        assert namespace["_active_analyzer_mirror_dir"]() == first_generation
        pointer.write_text(json.dumps(first_pointer), encoding="utf-8")

        bad_manifest = _bundle_manifest(files)
        bad_manifest["files"][0]["sha256"] = "0" * 64
        try:
            namespace["_install_analyzer_bundle"](_zip_bundle(files, bad_manifest), {})
        except ValueError as exc:
            assert "integrity mismatch" in str(exc)
        else:
            raise AssertionError("bad artifact hash must fail installation")
        assert json.loads(pointer.read_text(encoding="utf-8")) == first_pointer
        assert (first_generation / "analysis_dashboard.html").read_bytes() == b"dashboard-v1"

        summary_path = first_generation / "executive_summary.txt"
        summary_path.write_bytes(b"tampered!")
        assert namespace["_active_analyzer_mirror_dir"]() is None
        summary_path.write_bytes(b"summary-v1")
        assert namespace["_active_analyzer_mirror_dir"]() == first_generation

        shutil.rmtree(first_generation)
        assert namespace["_active_analyzer_mirror_dir"]() is None
        (root / "legacy").mkdir()
        (root / "legacy" / "analysis_dashboard.html").write_text("legacy", encoding="utf-8")
        assert namespace["_active_analyzer_mirror_dir"]() is None


def test_flask_snapshot_routes_require_auth_serve_links_and_reject_traversal():
    from flask import Flask, jsonify, make_response, request, send_file

    tree = ast.parse(BOT)
    wanted = {
        "analyzer_mirror_dashboard",
        "analyzer_mirror_dashboard_index",
        "analyzer_mirror_artifact",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        mirror = Path(tmp)
        (mirror / "analysis_dashboard.html").write_text(
            '<a href="executive_summary.txt">summary</a>', encoding="utf-8"
        )
        (mirror / "executive_summary.txt").write_text("summary", encoding="utf-8")
        outside = mirror.parent / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        app = Flask("analyzer-route-fixture")
        auth = {"allowed": False}
        namespace = {
            "app": app,
            "request": request,
            "jsonify": jsonify,
            "make_response": make_response,
            "send_file": send_file,
            "_analyzer_view_authed": lambda: auth["allowed"],
            "_active_analyzer_mirror_dir": lambda: mirror,
            "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        client = app.test_client()
        unauthenticated = client.get("/analysis/")
        assert unauthenticated.status_code == 303
        assert unauthenticated.headers["Location"] == "/analysis/login"

        auth["allowed"] = True
        dashboard = client.get("/analysis/")
        assert dashboard.status_code == 200
        assert b"executive_summary.txt" in dashboard.data
        assert "default-src 'none'" in dashboard.headers["Content-Security-Policy"]
        summary = client.get("/analysis/executive_summary.txt")
        assert summary.status_code == 200
        assert summary.data == b"summary"
        traversal = client.get("/analysis/%2e%2e/secret.txt")
        assert traversal.status_code == 400
        unauthenticated.close()
        dashboard.close()
        summary.close()
        traversal.close()
        outside.unlink(missing_ok=True)


def test_flask_snapshot_routes_fail_closed_without_complete_generation():
    from flask import Flask, jsonify, make_response, request, send_file

    tree = ast.parse(BOT)
    wanted = {"analyzer_mirror_dashboard_index", "analyzer_mirror_artifact"}
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    app = Flask("analyzer-fail-closed-fixture")
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "make_response": make_response,
        "send_file": send_file,
        "_analyzer_view_authed": lambda: True,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    client = app.test_client()
    dashboard = client.get("/analysis/")
    artifact = client.get("/analysis/executive_summary.txt")
    assert dashboard.status_code == 503
    assert artifact.status_code == 503
    assert b"complete validated analyzer bundle" in dashboard.data


def test_legacy_html_publication_is_rejected_and_status_discloses_quarantine():
    from flask import Flask, jsonify, request

    tree = ast.parse(BOT)
    wanted = {"api_data_sync_analyzer_report", "api_analyzer_mirror_status"}
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        (legacy / "analysis_dashboard.html").write_text("forensic legacy", encoding="utf-8")
        app = Flask("analyzer-publication-fixture")
        namespace = {
            "app": app,
            "request": request,
            "jsonify": jsonify,
            "_admin_authed_strict": lambda: True,
            "_active_analyzer_mirror_dir": lambda: None,
            "_analyzer_mirror_dir": lambda: legacy,
            "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
            "_ANALYZER_BUNDLE_MAX_COMPRESSED_BYTES": 50 * 1024 * 1024,
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        client = app.test_client()
        response = client.post(
            "/api/data-sync/analyzer-report",
            data={"report": (io.BytesIO(b"new legacy"), "analysis_dashboard.html")},
        )
        assert response.status_code == 410
        assert response.json["required_schema"] == "analyzer_mirror_bundle_v2"
        assert (legacy / "analysis_dashboard.html").read_text(encoding="utf-8") == "forensic legacy"
        status = client.get("/api/analyzer-mirror/status")
        assert status.status_code == 404
        assert status.json["available"] is False
        assert status.json["legacy_data_preserved"] is True


if __name__ == "__main__":
    test_fly_runtime_cwd_is_volume_backed()
    test_incremental_sync_is_authenticated_and_chunk_verified()
    test_local_sync_has_fail_closed_30_gib_admission_guard()
    test_retention_never_removes_active_or_unacknowledged_files()
    test_numbered_rotations_are_supported_and_highest_two_are_retained()
    test_remote_analyzer_mirror_is_read_only_and_admin_gated()
    test_analyzer_bundle_validation_fails_closed_for_missing_dashboard_and_unsafe_paths()
    test_analyzer_bundle_accepts_complete_read_only_report_tree()
    print("Fly data sync contract checks passed")
