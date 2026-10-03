import ast
import hashlib
import io
import json
import os
import re
import shutil
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
RELAY_SYNC = (ROOT.parents[1] / "scripts" / "sync-platform-relay-evidence.ps1").read_text(
    encoding="utf-8"
)


def test_relay_evidence_timestamp_survives_powershell_json_date_conversion():
    assert "[DateTimeOffset]::TryParse([string]$payload.generatedAt" not in RELAY_SYNC
    assert "$generatedAtRaw = $payload.generatedAt" in RELAY_SYNC
    assert "$generatedAtRaw -is [DateTime]" in RELAY_SYNC
    assert "[Globalization.CultureInfo]::InvariantCulture" in RELAY_SYNC


def test_relay_evidence_sync_deduplicates_timestamp_only_envelope_refreshes():
    assert "function Get-RelayEvidenceSemanticDigest" in RELAY_SYNC
    assert "if ([string]$name -ceq 'generatedAt') { continue }" in RELAY_SYNC
    assert "$incomingSemanticDigest = Get-RelayEvidenceSemanticDigest $payload" in RELAY_SYNC
    assert "Get-RelayEvidenceSemanticDigest $existingPayload" in RELAY_SYNC
    dedupe = RELAY_SYNC.index("$incomingSemanticDigest =")
    forward = RELAY_SYNC.index("$forward = Invoke-RestMethod")
    assert dedupe < forward
    assert "Write-Output $destination\n    return" in RELAY_SYNC


def test_identity_epoch_cache_is_primed_at_boot_and_updated_on_fresh_reset():
    main = BOT[BOT.index("def main():"):]
    assert main.index("_ensure_collector_v22_epoch()") < main.index(
        "_prime_data_sync_identity_epoch_cache()"
    )
    fresh = BOT[BOT.index("def _perform_fresh_collection_reset_locked("):BOT.index(
        "replay_buffers:", BOT.index("def _perform_fresh_collection_reset_locked(")
    )]
    signal_read = 'signal_ts = float(state.get("fresh_collection_signal_ts") or 0.0)'
    cache_update = "_update_data_sync_identity_epoch_cache("
    assert fresh.index(signal_read) < fresh.index(cache_update, fresh.index(signal_read))
    # The discard reset publishes the exact new epoch from its boundary receipt,
    # not a second read of potentially stale session metadata.
    assert 'collection_epoch_id=boundary["new_epoch"]' in fresh
    assert 'new_epoch_id=boundary["new_epoch"]' in fresh


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
        "_data_sync_runtime_root": lambda: Path.cwd(),
        "_pure_validate_platform_relay_evidence_payload": pure_validate_relay,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


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
        # Parameter-routed writers: their concrete targets are pinned below.
        if isinstance(first, ast.Name) and first.id not in {"output_file", "path", "sidecar"}:
            observed_constants.add(first.id)
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            observed_literals.add(first.value)

    assert observed_constants <= declared_constants
    assert observed_literals <= declared_literals
    assert {"XVL_SHADOW_FILE", "ADAPTIVE_ENTRY_DECISIONS_FILE", "RETIRED_TILE_BOUNDARY_FILE"} <= declared_constants
    assert {"fill_markouts.jsonl", "taker_signal_counterfactuals.jsonl",
            "xvp_shadow_signals.jsonl", "xvs_shadow_signals.jsonl"} <= declared_literals
    assert "FILL_QUALITY_FILE" in declared_constants
    assert "TYPE_B_RESEARCH_V2_EVENT_FILE" not in declared_constants
    assert "execution_funnel.jsonl" in declared_literals
    assert "retired_lane_violations.jsonl" not in declared_literals


def test_dynamic_csv_schema_expansion_is_an_atomic_inode_change():
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_atomic_write_csv_rows", "_dynamic_csv_writer_once",
            "_quarantine_overflow_csv_rows",
        }
    ]
    namespace = {
        "os": os, "csv": __import__("csv"), "threading": threading,
        "safe_csv_row": lambda row: dict(row),
        "CSV_OVERFLOW_RESTKEY": "__csv_overflow_fields__",
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


def _load_jsonl_writer(tmp_path):
    tree = ast.parse(BOT)
    wanted = {
        "_jsonl_path_lock", "_jsonl_validation_signature",
        "_jsonl_validation_tail_sha256", "_jsonl_validation_receipt_path",
        "_fsync_jsonl_validation_parent",
        "_persist_jsonl_validation_receipt", "_jsonl_validation_receipt_matches",
        "_validate_or_quarantine_jsonl", "_safe_append_jsonl",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]

    class _Logger:
        def __init__(self):
            self.messages = []

        def error(self, message):
            self.messages.append(str(message))

        def warning(self, message, *args):
            self.messages.append(str(message) % args if args else str(message))

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
        "_jsonl_validated_targets": {},
        "_JSONL_VALIDATION_TAIL_BYTES": 64 * 1024,
        "_jsonl_serialized_append_targets": set(),
        "rotate_log": lambda _path: None,
        "_transient_csv_lock_error": lambda _error: False,
        "_csv_write_fallback": lambda *_args: None,
        "_data_sync_runtime_root": lambda: tmp_path,
        "emergency_admission": lambda **_kwargs: {
            "allowed": True,
            "reason": None,
            "threshold": 0.90,
        },
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


def test_jsonl_writer_distinguishes_admission_suppression_from_write_failure(tmp_path):
    suppressed = _load_jsonl_writer(tmp_path)
    suppressed["emergency_admission"] = lambda **_kwargs: {
        "allowed": False,
        "reason": "NEW_NONESSENTIAL_RESEARCH_BLOCKED_AT_STORAGE_EMERGENCY",
        "threshold": 0.90,
    }
    admission_outcome = {}
    assert not suppressed["_safe_append_jsonl"](
        str(tmp_path / "suppressed.jsonl"), {"row": 1},
        "MARKET_MICROSTRUCTURE_1S", outcome=admission_outcome,
    )
    assert admission_outcome == {"status": "ADMISSION_SUPPRESSED"}

    failed = _load_jsonl_writer(tmp_path)
    failed["rotate_log"] = lambda _path: (_ for _ in ()).throw(OSError("disk fault"))
    write_outcome = {}
    assert not failed["_safe_append_jsonl"](
        str(tmp_path / "failed.jsonl"), {"row": 1},
        "MARKET_MICROSTRUCTURE_1S", fallback_on_error=False,
        outcome=write_outcome,
    )
    assert write_outcome == {"status": "WRITE_FAILED"}


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


def test_jsonl_writer_restart_uses_durable_bounded_validation_receipt(tmp_path):
    target = tmp_path / "ai_input_log.jsonl"
    target.write_text(
        "".join(json.dumps({"row": index}) + "\n" for index in range(5000)),
        encoding="utf-8",
    )
    first = _load_jsonl_writer(tmp_path)
    assert first["_safe_append_jsonl"](str(target), {"row": 5000}, "AI_INPUT")
    assert Path(str(target) + ".validation.json").is_file()

    # A fresh namespace models a watchdog/process restart with an empty memory
    # cache. Loading the small receipt may decode once; historical rows must
    # not be decoded again.
    restarted = _load_jsonl_writer(tmp_path)
    original_loads = restarted["json"].loads
    decoded = []

    def _counting_loads(value, *args, **kwargs):
        decoded.append(len(value))
        return original_loads(value, *args, **kwargs)

    restarted["json"].loads = _counting_loads
    try:
        assert restarted["_safe_append_jsonl"](
            str(target), {"row": 5001}, "AI_INPUT"
        )
    finally:
        restarted["json"].loads = original_loads
    assert len(decoded) <= 2
    assert len(target.read_text(encoding="utf-8").splitlines()) == 5002


def test_jsonl_writer_external_mutation_invalidates_receipt_and_quarantines(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    assert namespace["_safe_append_jsonl"](str(target), {"row": 1}, "AI_INPUT")

    target.write_bytes(b'{"row": X}\n')
    assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "AI_INPUT")
    assert [json.loads(line) for line in target.read_text().splitlines()] == [{"row": 2}]
    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    receipt = json.loads(
        (quarantine_dirs[0] / "quarantine_manifest.json").read_text()
    )
    assert receipt["bad_line"] == 1


def test_jsonl_writer_external_truncation_invalidates_receipt(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    assert namespace["_safe_append_jsonl"](str(target), {"row": 1}, "AI_INPUT")
    assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "AI_INPUT")

    target.write_bytes(b'{"row":')
    assert namespace["_safe_append_jsonl"](str(target), {"row": 3}, "AI_INPUT")
    assert [json.loads(line) for line in target.read_text().splitlines()] == [{"row": 3}]
    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    assert (quarantine_dirs[0] / target.name).read_bytes() == b'{"row":'


def test_jsonl_writer_restored_mtime_prefix_mutation_invalidates_on_ctime(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    target.write_text(
        "".join(json.dumps({"row": index, "payload": "x" * 80}) + "\n"
                for index in range(2000)),
        encoding="utf-8",
    )
    assert namespace["_safe_append_jsonl"](
        str(target), {"row": 2000, "payload": "x" * 80}, "AI_INPUT"
    )
    before = target.stat()
    content = bytearray(target.read_bytes())
    mutation_at = content.find(b'"row": 100')
    assert 0 <= mutation_at < len(content) - 64 * 1024
    content[mutation_at + len(b'"row": ')] = ord("X")
    target.write_bytes(content)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    # Windows exposes creation time as st_ctime, while production Linux exposes
    # inode change time. Model the production stat transition explicitly when
    # the host cannot provide it so this regression remains cross-platform.
    real_signature = namespace["_jsonl_validation_signature"]
    current = real_signature(str(target))
    if current[4] == before.st_ctime_ns:
        namespace["_jsonl_validation_signature"] = (
            lambda path: real_signature(path)[:4] + (before.st_ctime_ns + 1,)
        )

    assert namespace["_safe_append_jsonl"](
        str(target), {"row": 2001, "payload": "x" * 80}, "AI_INPUT"
    )
    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    receipt = json.loads(
        (quarantine_dirs[0] / "quarantine_manifest.json").read_text()
    )
    assert receipt["bad_line"] == 101
    assert [json.loads(line) for line in target.read_text().splitlines()] == [
        {"row": 2001, "payload": "x" * 80}
    ]


def test_jsonl_receipt_failure_never_retries_a_durable_row(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    original_persist = namespace["_persist_jsonl_validation_receipt"]
    persist_calls = []

    def _fail_receipt(*_args):
        persist_calls.append(True)
        raise OSError("simulated receipt failure")

    namespace["_persist_jsonl_validation_receipt"] = _fail_receipt
    assert namespace["_safe_append_jsonl"](str(target), {"row": 1}, "AI_INPUT")
    assert persist_calls == [True]
    assert [json.loads(line) for line in target.read_text().splitlines()] == [{"row": 1}]

    # Receipt failure invalidates the memory cache. The next append must parse
    # the existing row before proceeding, then restore the durable receipt.
    namespace["_persist_jsonl_validation_receipt"] = original_persist
    original_loads = namespace["json"].loads
    decoded = []

    def _counting_loads(value, *args, **kwargs):
        decoded.append(value)
        return original_loads(value, *args, **kwargs)

    namespace["json"].loads = _counting_loads
    try:
        assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "AI_INPUT")
    finally:
        namespace["json"].loads = original_loads
    assert decoded
    assert [json.loads(line) for line in target.read_text().splitlines()] == [
        {"row": 1}, {"row": 2}
    ]


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
    assert direct_append_functions <= {
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
    test_analyzer_bundle_validation_fails_closed_for_missing_dashboard_and_unsafe_paths()
    test_analyzer_bundle_accepts_complete_read_only_report_tree()
    print("Fly data sync contract checks passed")


def test_guarded_workflow_has_exact_paper_only_flatten_recovery_mode():
    workflow = (ROOT.parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text(encoding="utf-8")
    assert "- flatten-paper-exposure" in workflow
    block = workflow.split("  flatten-paper-exposure:", 1)[1].split("\n  restart-only:", 1)[0]
    assert 'health.get("force_paper_mode") is True' in block
    assert 'health.get("live_armed") is False' in block
    assert 'health.get("bitfinex_live_enabled") is False' in block
    # #198: maintenance flatten closes paper through normal accounting, never phantom-cancel.
    assert '"/api/positions/close"' in block
    assert "phantom-cancel" not in block
    assert '"/api/orders/cancel"' in block
    assert "paper/live safety flags are not proven" in block
    assert "paper exposure did not flatten" in block
