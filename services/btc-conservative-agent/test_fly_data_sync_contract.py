from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
SYNC_SCRIPT = (ROOT.parents[1] / "scripts" / "sync-fly-bot-data.ps1").read_text(
    encoding="utf-8"
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
    assert "/api/data-sync/manifest" not in BOT[BOT.index("_READ_ONLY_GET_PATHS"):BOT.index("def _client_ip")]
    assert '"X-Bot-Admin-Token" = $AdminToken' in SYNC_SCRIPT
    assert "Chunk checksum mismatch" in SYNC_SCRIPT
    assert '$appendOnly = $extension -in @(".jsonl", ".csv", ".log", ".txt")' in SYNC_SCRIPT
    assert "[int64]$previous.mtime_ns -eq [int64]$row.mtime_ns" in SYNC_SCRIPT


def test_retention_never_removes_active_or_unacknowledged_files():
    assert "only acknowledged .3+ rotations older than 24h" in BOT
    assert "int(match.group(1)) < 3" in BOT
    assert "int(ack.get(\"size\") or -1) == int(stat.st_size)" in BOT
    assert "int(ack.get(\"mtime_ns\") or -1) == int(stat.st_mtime_ns)" in BOT


def test_remote_analyzer_mirror_is_read_only_and_admin_gated():
    assert "@app.route('/api/data-sync/analyzer-report', methods=['POST'])" in BOT
    assert "@app.route('/analysis')" in BOT
    assert "if not _admin_authed_strict()" in BOT
    assert "analysis_dashboard.html" in SYNC_SCRIPT


if __name__ == "__main__":
    test_fly_runtime_cwd_is_volume_backed()
    test_incremental_sync_is_authenticated_and_chunk_verified()
    test_retention_never_removes_active_or_unacknowledged_files()
    test_remote_analyzer_mirror_is_read_only_and_admin_gated()
    print("Fly data sync contract checks passed")
