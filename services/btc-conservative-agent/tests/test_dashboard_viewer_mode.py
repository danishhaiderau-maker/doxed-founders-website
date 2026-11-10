"""Synthetic viewer-mode guards. No live market data, credentials, or network."""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import research_dashboard as dashboard

ROOT = Path(__file__).resolve().parents[3]
SHIM = ROOT / "services" / "btc-conservative-agent" / "research_dashboard.py"
LAUNCHER = ROOT / "scripts" / "start-dashboard-viewer.ps1"
CANARY = "CANARY_SECRET_do_not_leak"
CAP_KEYS = dashboard._VIEWER_CAP_KEYS
REJECTED_HOSTS = ["localhost", "0.0.0.0", "::", "::1", "example.com", "10.0.0.8", "127.0.0.1 ", "127.0.0.10", ""]
REJECTED_PORTS = ["0", "80", "1023", "65536", "-1", "80x", "", "1.5", "00080"]


@pytest.fixture(autouse=True)
def _reset():
    dashboard.reset_viewer_mode()
    yield
    dashboard.reset_viewer_mode()


def _caps(**overrides):
    base = {key: 10 ** 9 for key in CAP_KEYS}
    base.update(overrides)
    return base


def _write_caps(root: Path, caps: dict) -> None:
    folder = root / "viewer"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "caps.json").write_text(json.dumps(caps), encoding="utf-8")


def _enable(root: Path, allow=(), caps=None, host="127.0.0.1", port="9001"):
    if caps is not None:
        _write_caps(root, caps)
        allow = tuple(allow) + ("viewer/caps.json",)
    argv = ["viewer", "--viewer-mode", "--bind-host", host, "--port", str(port), "--data-root", str(root)]
    for rel in allow:
        argv.extend(["--allow", rel])
    refusal = dashboard.apply_viewer_mode_from_argv(argv)
    return refusal


def _client():
    return dashboard.app.test_client()


def test_rejected_bind_hosts_do_not_listen(tmp_path):
    for host in REJECTED_HOSTS:
        dashboard.reset_viewer_mode()
        refusal = _enable(tmp_path, host=host, caps=_caps())
        assert refusal and refusal.startswith("REFUSED_BIND_HOST"), (host, refusal)
        assert dashboard._VIEWER["listened"] is False
        assert dashboard._viewer_mode_enabled() is False


def test_rejected_ports_do_not_listen(tmp_path):
    for port in REJECTED_PORTS:
        dashboard.reset_viewer_mode()
        refusal = _enable(tmp_path, port=port, caps=_caps())
        assert refusal and refusal.startswith("REFUSED_PORT"), (port, refusal)
        assert dashboard._VIEWER["listened"] is False


def test_accepted_bind_does_not_listen_until_main(tmp_path):
    refusal = _enable(tmp_path, caps=_caps(), port="1024")
    assert refusal is None
    assert dashboard._VIEWER["listened"] is False
    assert dashboard._VIEWER["bind_host"] == "127.0.0.1"
    assert dashboard._VIEWER["port"] == 1024
    refusal = _enable(tmp_path, caps=_caps(), port="65535")
    assert refusal is None and dashboard._VIEWER["port"] == 65535


def test_missing_data_root_is_not_created(tmp_path):
    missing = tmp_path / "absent-root"
    refusal = dashboard.apply_viewer_mode_from_argv([
        "viewer", "--viewer-mode", "--bind-host", "127.0.0.1", "--port", "9001",
        "--data-root", str(missing),
    ])
    assert refusal and refusal.startswith("REFUSED_DATA_ROOT")
    assert not missing.exists()


@pytest.mark.parametrize("rel", ["../outside.txt", "/tmp/x", "C:/secret.txt", "\\\\server\\share", "viewer/../../x"])
def test_allow_paths_fail_closed(tmp_path, rel):
    refusal = _enable(tmp_path, allow=(rel,), caps=_caps())
    assert refusal and refusal.startswith("REFUSED_ALLOW_PATH"), refusal


def test_symlink_escape_fails_closed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    assert _enable(root, allow=("link/secret.txt",), caps=_caps()) is None
    with pytest.raises(dashboard._ViewerGuard) as caught:
        dashboard._viewer_open_allowlisted("link/secret.txt")
    assert caught.value.reason == "REFUSED_SYMLINK"


def test_swap_race_fails_closed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "viewer"
    target.mkdir()
    file_path = target / "fixture.json"
    file_path.write_text('{"fixture":"race"}', encoding="utf-8")
    assert _enable(root, allow=("viewer/fixture.json",), caps=_caps(file_count=5, file_read_bytes=1000)) is None

    def swap(path):
        path.unlink()
        path.write_text('{"fixture":"swapped"}', encoding="utf-8")

    dashboard._VIEWER["before_open"] = swap
    with pytest.raises(dashboard._ViewerGuard) as caught:
        dashboard._viewer_open_allowlisted("viewer/fixture.json")
    assert caught.value.reason == "REFUSED_RACE"


def test_unset_cap_refuses_every_route_class(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert _enable(root, allow=(), caps=None) is None
    client = _client()
    for path in ("/api/system-health", "/favicon.ico", "/download/reports"):
        response = client.get(path)
        assert response.status_code == 403, path
        assert response.get_json()["reason"] == "CAP_UNSET"


def _prepare_usage_root(tmp_path, caps):
    root = tmp_path / "root"
    root.mkdir(parents=True)
    (root / "viewer").mkdir()
    (root / "viewer" / "fixture.json").write_text('{"fixture":"synthetic-viewer"}', encoding="utf-8")
    (root / "note.txt").write_bytes(b"note-bytes")
    assert _enable(root, allow=("viewer/fixture.json", "note.txt"), caps=caps) is None
    dashboard._VIEWER["clock"] = _step_clock()
    return root


def _step_clock():
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return 0.0 if ticks["n"] % 2 else 0.02

    return clock


def test_cap_at_and_over_for_each_key(tmp_path):
    root = _prepare_usage_root(tmp_path, _caps())
    client = _client()
    baseline = client.get("/api/system-health")
    assert baseline.status_code == 200, baseline.get_data(as_text=True)
    usage = json.loads(baseline.headers["X-Viewer-Usage"])
    for key in CAP_KEYS:
        used = int(usage[key])
        at_root = _prepare_usage_root(tmp_path / key, _caps(**{key: used}))
        assert _client().get("/api/system-health").status_code == 200, key
        over_root = _prepare_usage_root(tmp_path / (key + "-over"), _caps(**{key: max(0, used - 1)}))
        if used == 0:
            continue
        over = _client().get("/api/system-health")
        assert over.status_code == 403, key
        assert over.get_json()["reason"] == "CAP_" + key.upper()
        assert over.get_json()["partial"] is False
        assert at_root.is_dir() and over_root.is_dir()


def test_download_and_page_caps(tmp_path):
    root = _prepare_usage_root(tmp_path, _caps())
    client = _client()
    page = client.get("/favicon.ico")
    assert page.status_code == 204
    page_usage = json.loads(page.headers["X-Viewer-Usage"])
    assert page_usage["archive_entry_count"] == 1
    report = client.get("/download/reports")
    assert report.status_code == 200, report.get_data(as_text=True)
    assert report.mimetype == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(report.get_data()))
    assert archive.namelist() == ["note.txt"]
    report_usage = json.loads(report.headers["X-Viewer-Usage"])
    assert report_usage["archive_entry_count"] == 1
    assert report_usage["uncompressed_bytes"] == len(b"note-bytes")
    tight = _prepare_usage_root(tmp_path / "tight", _caps(uncompressed_bytes=report_usage["uncompressed_bytes"] - 1))
    refused = _client().get("/download/reports")
    assert refused.status_code == 403
    assert refused.get_json()["reason"] == "CAP_UNCOMPRESSED_BYTES"
    assert not list(tight.rglob("*.zip"))


def test_disk_staging_downloads_are_refused(tmp_path):
    root = _prepare_usage_root(tmp_path, _caps())
    client = _client()
    for path in ("/download/everything", "/download/complete", "/download/genome"):
        response = client.get(path)
        body = response.get_json()
        assert response.status_code == 403
        assert body["error"] == "disabled in viewer mode: disk staging"
        assert body["reason"] == "VIEWER_DISK_STAGING_DISABLED"


def test_status_model_and_non_evidential_07d(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    viewer = root / "viewer"
    viewer.mkdir()
    (viewer / "fixture.json").write_text('{"fixture":"synthetic-07"}', encoding="utf-8")
    (viewer / "runtime.json").write_text(json.dumps({
        "state": "OFFLINE", "observed_at": "2026-11-07T02:40:35Z", "source": "synthetic-receipt",
    }), encoding="utf-8")
    (viewer / "auth.json").write_text(json.dumps({
        "state": "AUTH_DOWN", "observed_at": "2020-01-01T00:00:00Z", "source": "synthetic-auth",
    }), encoding="utf-8")
    (viewer / "thresholds.json").write_text(json.dumps({
        "auth_max_age_sec": 60, "freshness_max_age_sec": 10**9,
    }), encoding="utf-8")
    (viewer / "historical.json").write_text(json.dumps({
        "state": "STALE", "observed_at": "2026-11-01T00:00:00Z", "counts": {"trades": 4},
    }), encoding="utf-8")
    (viewer / "forward.json").write_text(json.dumps({
        "state": "CURRENT", "observed_at": "2026-11-07T08:00:00Z", "counts": {"trades": 9},
    }), encoding="utf-8")
    (viewer / "phase_b_07d.json").write_text(json.dumps({
        "run": "07d", "accepted": True, "findings": ["edge"],
    }), encoding="utf-8")
    allow = (
        "viewer/fixture.json", "viewer/runtime.json", "viewer/auth.json", "viewer/thresholds.json",
        "viewer/historical.json", "viewer/forward.json", "viewer/phase_b_07d.json",
    )
    assert _enable(root, allow=allow, caps=_caps()) is None
    body = _client().get("/api/system-health").get_json()
    dashboard.require_projection_labels(body)
    assert body["runtime"]["state"] == "OFFLINE"
    assert body["runtime"]["source"] == "synthetic-receipt"
    assert body["auth"]["state"] == "UNKNOWN"
    assert body["auth"]["historical"]["state"] == "AUTH_DOWN"
    assert body["auth"]["current"] is False
    assert body["pipeline"]["state"] == "NOT_CONNECTED"
    assert body["historical"]["counts"] is None
    assert body["forward"]["counts"] is None
    assert body["historical"]["state"] == "STALE"
    assert body["forward"]["state"] == "CURRENT"
    assert body["phase_b_07d"]["evidence_class"] == "NON-EVIDENTIAL"
    assert body["phase_b_07d"]["accepted_strategy"] is False
    assert body["phase_b_07d"]["findings"] is None
    assert body["accepted_strategy"] is False
    assert body["verdict"] == "UNKNOWN"
    assert "GREEN" not in json.dumps(body)
    assert body["fixture"] == "synthetic-07"
    with pytest.raises(ValueError):
        dashboard.require_projection_labels({"source": "x"})


def test_contradictory_runtime_and_missing_source_are_not_offline(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    viewer = root / "viewer"
    viewer.mkdir()
    (viewer / "runtime.json").write_text('{"state":"OFFLINE","source":"synthetic"}', encoding="utf-8")
    (viewer / "data_freshness.json").write_text("not-json", encoding="utf-8")
    assert _enable(root, allow=("viewer/runtime.json", "viewer/data_freshness.json"), caps=_caps()) is None
    body = _client().get("/api/system-health").get_json()
    assert body["runtime"]["state"] == "UNKNOWN"
    assert body["data_freshness"]["state"] == "MISSING_SOURCE"
    assert body["runtime"]["state"] != "OFFLINE"


def test_unset_threshold_forces_unknown_auth(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    viewer = root / "viewer"
    viewer.mkdir()
    (viewer / "auth.json").write_text(json.dumps({
        "state": "AUTH_DOWN", "observed_at": "2026-11-07T00:00:00Z", "source": "synthetic-auth",
    }), encoding="utf-8")
    assert _enable(root, allow=("viewer/auth.json",), caps=_caps()) is None
    body = _client().get("/api/system-health").get_json()
    assert body["auth"]["state"] == "UNKNOWN"
    assert body["auth"]["historical"]["state"] == "AUTH_DOWN"


def test_no_network_and_no_fly_probe(tmp_path, monkeypatch):
    calls = {"fly": 0, "connect": 0}

    def boom(*_args, **_kwargs):
        calls["connect"] += 1
        raise AssertionError("outbound connect")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    def fly(*_args, **_kwargs):
        calls["fly"] += 1
        raise AssertionError("fetch_fly_uptime")

    monkeypatch.setattr(dashboard._runtime_uptime, "fetch_fly_uptime", fly)
    root = _prepare_usage_root(tmp_path, _caps())
    client = _client()
    health = client.get("/api/system-health")
    aware = client.get("/api/streams/self-aware-data")
    page = client.get("/favicon.ico")
    assert health.status_code == 200
    assert health.get_json()["fly_probe"] is False
    assert "fetch(" not in health.get_data(as_text=True)
    assert aware.get_json()["status"] == "UNAVAILABLE (viewer mode)"
    assert b"data-system-health-banner" not in page.get_data()
    assert calls == {"fly": 0, "connect": 0}
    assert root.is_dir()


def test_feature_matrix_covers_routes_and_main_ui(tmp_path):
    root = _prepare_usage_root(tmp_path, _caps())
    body = _client().get("/api/system-health").get_json()
    rows = {row["id"]: row["status"] for row in body["features"]}
    assert rows["main:/"] == "UNAVAILABLE_MAIN_UI"
    assert rows["fly-system-health-uptime"] == "GATED_VIEWER_DISABLED"
    assert rows["self-aware-9021"] == "GATED_VIEWER_DISABLED"
    assert rows["engine-generation-mkdir"] == "GATED_VIEWER_DISABLED"
    assert rows["research:/download/everything"] == "GATED_VIEWER_DISABLED"
    assert rows["research:/download/reports"] == "AVAILABLE"
    assert rows["research:/api/streams/self-aware-data"] == "GATED_VIEWER_DISABLED"
    for rule in dashboard.app.url_map.iter_rules():
        if rule.rule.startswith("/static"):
            continue
        assert "research:" + rule.rule in rows
    client = _client()
    for rule in dashboard.app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule.startswith("/static"):
            continue
        path = rule.rule
        for converter in ("<path:filename>", "<path:artifact_path>", "<session_id>", "<archive_id>", "<dataset>", "<section_id>"):
            path = path.replace(converter, "x")
        path = path.replace("<", "").replace(">", "")
        response = client.get(path)
        assert response.status_code != 500, path
        if path.startswith("/download/") and path != "/download/reports":
            assert response.status_code == 403
    assert root.is_dir()


def test_gets_do_not_write_or_leak_canary(tmp_path, monkeypatch, capsys):
    root = _prepare_usage_root(tmp_path, _caps())
    (root / "secret.txt").write_text(CANARY, encoding="utf-8")
    monkeypatch.setenv("VIEWER_CANARY", CANARY)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    client = _client()
    blobs = []
    for path in ("/api/system-health", "/api/streams/self-aware-data", "/download/reports", "/download/everything", "/favicon.ico"):
        response = client.get(path)
        blobs.append(response.get_data())
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    leaked = CANARY.encode()
    assert all(leaked not in blob for blob in blobs)
    captured = capsys.readouterr()
    assert CANARY not in captured.out and CANARY not in captured.err


def test_non_viewer_mode_still_calls_fly_helper_and_banner(monkeypatch):
    dashboard.reset_viewer_mode()
    calls = {"fly": 0}

    def fly(*_args, **_kwargs):
        calls["fly"] += 1
        return {"uptime": None, "error": "synthetic-blocked"}

    monkeypatch.setattr(dashboard._runtime_uptime, "fetch_fly_uptime", fly)
    response = _client().get("/api/system-health")
    assert calls["fly"] == 1
    assert response.status_code == 200
    with dashboard.app.test_request_context("/alerts"):
        page = dashboard.make_response("<html><body></body></html>", 200)
        page.mimetype = "text/html"
        stamped = dashboard._inject_system_health_banner(page)
    assert b"data-system-health-banner" in stamped.get_data()
    dashboard._VIEWER["enabled"] = True
    with dashboard.app.test_request_context("/alerts"):
        page = dashboard.make_response("<html><body></body></html>", 200)
        page.mimetype = "text/html"
        skipped = dashboard._inject_system_health_banner(page)
    assert b"data-system-health-banner" not in skipped.get_data()


def test_shim_passes_viewer_argv_without_listening():
    env = os.environ.copy()
    env.pop("BTC_AGENT_DATA_DIR", None)
    completed = subprocess.run(
        [sys.executable, str(SHIM), "--standalone", "--viewer-mode", "--bind-host", "0.0.0.0", "--port", "80",
         "--data-root", "/tmp"],
        capture_output=True, text=True, timeout=90, env=env, cwd=str(SHIM.parent),
    )
    assert completed.returncode == 2, completed.stderr
    assert "REFUSED_BIND_HOST" in completed.stderr


def _run_launcher(args, *, root_ok=True, interpreter_ok=True, shim_ok=True):
    text = LAUNCHER.read_text(encoding="utf-8")
    for needle in (
        'REFUSED_MISSING_ARG', 'REFUSED_BIND_HOST', '-cne "127.0.0.1"',
        "REFUSED_PORT", "-lt 1024", "-gt 65535", "REFUSED_DATA_ROOT",
        "REFUSED_INTERPRETER", "no PATH lookup", "--viewer-mode",
    ):
        assert needle in text, needle
    if any(not args.get(name) for name in ("Interpreter", "DataRoot", "BindHost", "Port")):
        return 2, "REFUSED_MISSING_ARG"
    if args["BindHost"] != "127.0.0.1":
        return 2, "REFUSED_BIND_HOST"
    if not str(args["Port"]).isdecimal():
        return 2, "REFUSED_PORT"
    number = int(args["Port"])
    if number < 1024 or number > 65535:
        return 2, "REFUSED_PORT"
    if not root_ok:
        return 2, "REFUSED_DATA_ROOT"
    if not interpreter_ok:
        return 2, "REFUSED_INTERPRETER"
    if not shim_ok:
        return 2, "REFUSED_ENTRYPOINT"
    return 0, "argv-only"


@pytest.mark.parametrize("host", [host for host in REJECTED_HOSTS if host])
def test_launcher_rejects_hosts(host):
    code, message = _run_launcher({"Interpreter": "py", "DataRoot": "root", "BindHost": host, "Port": "9001"})
    assert code == 2 and message == "REFUSED_BIND_HOST"


@pytest.mark.parametrize("port", [port for port in REJECTED_PORTS if port])
def test_launcher_rejects_ports(port):
    code, message = _run_launcher({"Interpreter": "py", "DataRoot": "root", "BindHost": "127.0.0.1", "Port": port})
    assert code == 2 and message == "REFUSED_PORT"


def test_launcher_rejects_missing_args_even_if_names_exist(monkeypatch):
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("DASHBOARD_BIND_HOST", "127.0.0.1")
    code, message = _run_launcher({"Interpreter": "py", "DataRoot": "root", "BindHost": "127.0.0.1", "Port": ""})
    assert code == 2 and message == "REFUSED_MISSING_ARG"


def test_launcher_rejects_missing_root_and_interpreter():
    assert _run_launcher({"Interpreter": "py", "DataRoot": "missing", "BindHost": "127.0.0.1", "Port": "9001"}, root_ok=False)[1] == "REFUSED_DATA_ROOT"
    assert _run_launcher({"Interpreter": "missing", "DataRoot": "root", "BindHost": "127.0.0.1", "Port": "9001"}, interpreter_ok=False)[1] == "REFUSED_INTERPRETER"


def test_launcher_with_pwsh_when_present(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is not installed; statement runner covered the launcher guards")
    interpreter = tmp_path / "interpreter"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    root = tmp_path / "data"
    root.mkdir()
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(LAUNCHER), "-Interpreter", str(interpreter),
         "-DataRoot", str(root), "-BindHost", "0.0.0.0", "-Port", "9001"],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 2
    assert "REFUSED_BIND_HOST" in completed.stderr + completed.stdout
