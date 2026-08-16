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
import zipfile
from datetime import datetime
from pathlib import Path
from research.platform_relay_evidence import _validate_platform_relay_evidence_payload as pure_validate_relay


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
        "_pure_validate_platform_relay_evidence_payload": pure_validate_relay,
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
    assert "@app.route('/api/data-sync/platform-relay-evidence', methods=['POST'])" in BOT
    assert "def _validate_platform_relay_evidence_payload" in BOT
    assert "os.replace(temp, destination)" in BOT


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
    assert "outside the current run window" in SYNC_SCRIPT
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
