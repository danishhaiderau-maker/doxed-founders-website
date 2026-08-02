import ast
import os
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
SYNC_SCRIPT = (ROOT.parents[1] / "scripts" / "sync-fly-bot-data.ps1").read_text(
    encoding="utf-8"
)


def _load_bot_functions(*names):
    tree = ast.parse(BOT)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "Path": Path,
        "time": time,
        "_DATA_SYNC_EXTENSIONS": frozenset(
            {".csv", ".json", ".jsonl", ".log", ".db", ".sqlite", ".sqlite3", ".txt"}
        ),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


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
    assert "/api/data-sync/manifest" not in BOT[BOT.index("_READ_ONLY_GET_PATHS"):BOT.index("def _client_ip")]
    assert '"X-Bot-Admin-Token" = $AdminToken' in SYNC_SCRIPT
    assert "Chunk checksum mismatch" in SYNC_SCRIPT
    assert "$chunkLimit = 4MB" in SYNC_SCRIPT
    assert '$appendOnly = $extension -in @(".jsonl", ".csv", ".log", ".txt")' in SYNC_SCRIPT
    assert "[int64]$previous.mtime_ns -eq [int64]$row.mtime_ns" in SYNC_SCRIPT
    assert "def _data_sync_rotation_parts" in BOT
    assert "_data_sync_rotation_parts(resolved.name) is not None" in BOT
    assert 'path.startswith("/api/data-sync/")' in BOT
    assert "and not is_authenticated_data_sync" in BOT


def test_retention_never_removes_active_or_unacknowledged_files():
    assert "acknowledged rotations older than 24h are pruned except the two newest generations" in BOT
    assert "newest_two = frozenset(sorted(generations)[-2:])" in BOT
    assert "if rotation_index in newest_two" in BOT
    assert "int(ack.get(\"size\") or -1) == int(stat.st_size)" in BOT
    assert "int(ack.get(\"mtime_ns\") or -1) == int(stat.st_mtime_ns)" in BOT


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
    assert "if not _admin_authed_strict()" in BOT
    assert "analysis_dashboard.html" in SYNC_SCRIPT


if __name__ == "__main__":
    test_fly_runtime_cwd_is_volume_backed()
    test_incremental_sync_is_authenticated_and_chunk_verified()
    test_retention_never_removes_active_or_unacknowledged_files()
    test_numbered_rotations_are_supported_and_highest_two_are_retained()
    test_remote_analyzer_mirror_is_read_only_and_admin_gated()
    print("Fly data sync contract checks passed")
