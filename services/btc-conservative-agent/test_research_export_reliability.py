"""Resource bounds and failure-safety contracts for the research ZIP export."""

import io
import json
import os
import tempfile
import types
import zipfile

os.environ["FORCE_PAPER_MODE"] = "1"
os.environ["SKIP_EXCHANGE_MARKET_LOAD"] = "1"
os.environ["BOT_ADMIN_TOKEN"] = "deterministic-export-reliability-test-token"

import bot


def _authenticated_export(client):
    return client.get(
        "/api/export.csv",
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
        headers={
            "X-Forwarded-For": "203.0.113.10",
            "X-Bot-Admin-Token": os.environ["BOT_ADMIN_TOKEN"],
        },
    )


def _prepare_route(tmp_path, monkeypatch, export_files):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", os.environ["BOT_ADMIN_TOKEN"])
    monkeypatch.setattr(bot, "research_export_files", lambda: export_files)


def test_small_export_uses_disk_from_first_byte_and_cleans_up(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_bytes(os.urandom(4096))
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES])

    created = []
    real_temporary_file = tempfile.TemporaryFile

    def tracked_temporary_file(*args, **kwargs):
        handle = real_temporary_file(*args, **kwargs)
        created.append(handle)
        return handle

    monkeypatch.setattr(bot.tempfile, "TemporaryFile", tracked_temporary_file)

    response = _authenticated_export(bot.app.test_client())
    try:
        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert int(response.headers["Content-Length"]) == len(response.data)
        assert len(created) == 1
        with zipfile.ZipFile(io.BytesIO(response.data), "r") as archive:
            assert archive.testzip() is None
            assert archive.namelist() == [
                bot._RESEARCH_EXPORT_MANIFEST_NAME,
                bot.CSV_TRADES,
            ]
            assert archive.read(bot.CSV_TRADES) == member.read_bytes()
    finally:
        response.close()

    assert created[0].closed is True


def test_manifest_lists_included_and_optional_absent_members(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_text("safe", encoding="utf-8")
    _prepare_route(
        tmp_path,
        monkeypatch,
        [bot.CSV_TRADES, "historical/missing-optional.jsonl"],
    )

    response = _authenticated_export(bot.app.test_client())
    try:
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.data), "r") as archive:
            manifest = json.loads(archive.read(bot._RESEARCH_EXPORT_MANIFEST_NAME))
        assert manifest == {
            "schema": "bounded_research_export_manifest_v1",
            "scope": "diagnostic_convenience_existing_files_only",
            "canonical_mirror_ack_evidence": False,
            "complete_research_coverage": False,
            "generated_members": [bot._RESEARCH_EXPORT_MANIFEST_NAME],
            "included_members": [bot.CSV_TRADES],
            "omitted_optional_absent": ["historical/missing-optional.jsonl"],
        }
        assert str(tmp_path) not in response.data.decode("utf-8", errors="ignore")
    finally:
        response.close()


def test_no_available_data_returns_503_instead_of_empty_zip(tmp_path, monkeypatch):
    _prepare_route(tmp_path, monkeypatch, ["historical/missing-optional.jsonl"])

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 503
    assert response.mimetype == "application/json"
    assert response.headers["Retry-After"] == "60"


def test_path_escape_fails_without_partial_archive(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_text("safe", encoding="utf-8")
    outside = tmp_path.parent / "outside-export-secret.txt"
    outside.write_text("must-not-export", encoding="utf-8")
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES, str(outside)])

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 503
    assert response.mimetype == "application/json"
    assert response.headers["Retry-After"] == "60"
    assert "outside-export-secret" not in response.get_data(as_text=True)


def test_conflicting_archive_names_fail_without_partial_archive(tmp_path, monkeypatch):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "same.csv").write_text("one", encoding="utf-8")
    (tmp_path / "two" / "same.csv").write_text("two", encoding="utf-8")
    _prepare_route(tmp_path, monkeypatch, ["one/same.csv", "two/same.csv"])

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 503
    assert response.mimetype == "application/json"


def test_source_limit_returns_413_and_verified_sync_action(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_bytes(os.urandom(65))
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES])
    monkeypatch.setattr(bot, "_RESEARCH_EXPORT_MAX_SOURCE_BYTES", 64)

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 413
    assert response.get_json()["action"] == "use the verified research sync for large exports"
    assert response.headers["Cache-Control"] == "no-store"


def test_archive_output_limit_returns_413(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_bytes(os.urandom(1024))
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES])
    monkeypatch.setattr(bot, "_RESEARCH_EXPORT_MAX_SOURCE_BYTES", 2048)
    monkeypatch.setattr(bot, "_RESEARCH_EXPORT_MAX_ARCHIVE_BYTES", 64)
    monkeypatch.setattr(bot, "_RESEARCH_EXPORT_MIN_FREE_BYTES", 0)

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 413
    assert response.mimetype == "application/json"


def test_free_space_reserve_returns_503_before_build(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_text("small", encoding="utf-8")
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES])
    monkeypatch.setattr(bot.shutil, "disk_usage", lambda _: types.SimpleNamespace(free=1))

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"


def test_concurrent_build_returns_503_instead_of_racing_reserve(tmp_path, monkeypatch):
    _prepare_route(tmp_path, monkeypatch, [])
    assert bot._RESEARCH_EXPORT_BUILD_LOCK.acquire(blocking=False) is True
    try:
        response = _authenticated_export(bot.app.test_client())
    finally:
        bot._RESEARCH_EXPORT_BUILD_LOCK.release()

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"


def test_build_deadline_returns_503_and_closes_temp_file(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_text("small", encoding="utf-8")
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES])
    monkeypatch.setattr(bot, "_RESEARCH_EXPORT_BUILD_TIMEOUT_SEC", -1.0)

    created = []
    real_temporary_file = tempfile.TemporaryFile

    def tracked_temporary_file(*args, **kwargs):
        handle = real_temporary_file(*args, **kwargs)
        created.append(handle)
        return handle

    monkeypatch.setattr(bot.tempfile, "TemporaryFile", tracked_temporary_file)

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 503
    assert len(created) == 1
    assert created[0].closed is True


def test_unexpected_build_failure_is_generic_and_closes_temp_file(tmp_path, monkeypatch):
    member = tmp_path / bot.CSV_TRADES
    member.write_text("small", encoding="utf-8")
    _prepare_route(tmp_path, monkeypatch, [bot.CSV_TRADES])
    created = []
    real_temporary_file = tempfile.TemporaryFile

    def tracked_temporary_file(*args, **kwargs):
        handle = real_temporary_file(*args, **kwargs)
        created.append(handle)
        return handle

    class BrokenZipFile:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("sensitive-path-detail")

    monkeypatch.setattr(bot.tempfile, "TemporaryFile", tracked_temporary_file)
    monkeypatch.setattr(bot.zipfile, "ZipFile", BrokenZipFile)

    response = _authenticated_export(bot.app.test_client())

    assert response.status_code == 503
    assert response.get_json()["error"] == "research export temporarily unavailable"
    assert "sensitive-path-detail" not in response.get_data(as_text=True)
    assert len(created) == 1
    assert created[0].closed is True
