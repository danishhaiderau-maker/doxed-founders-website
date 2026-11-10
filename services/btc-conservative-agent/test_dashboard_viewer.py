"""No engine imports required: run on native Python 3.11 + Flask + pytest."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import dashboard_viewer as viewer

BASE = "http://127.0.0.1:7002"
LANE = "FAMILY_RANDOM_CONTROL_TAKER_90"


@pytest.fixture(autouse=True)
def safe_test_environment(monkeypatch):
    # Sanitize only this test process; production has no environment bypass.
    for name in list(os.environ):
        if viewer.CONTROL_NAME.search(name) or name.upper().startswith(("BITFINEX_", "BFX_")) or name.upper() in viewer.CREDENTIAL_NAMES:
            monkeypatch.delenv(name)


@pytest.fixture
def disarmed_root(tmp_path):
    (tmp_path / viewer.ARM_FILE).write_text(json.dumps({
        "schema": viewer.ARM_SCHEMA, "state": {"enabled": False}}))
    return tmp_path


@pytest.fixture
def data_root(disarmed_root):
    tmp_path = disarmed_root
    payload = {
        "git_rev": "fixture-saved-revision",
        "pathway_lane_specs": {"lanes": [{
            "lane": LANE, "session_stats": {"real_fills": 3, "net_pnl_real": 12.5,
            "wins": 2, "losses": 1, "approves": 4, "scope": "FIXTURE_SAVED_ONLY"},
        }]},
    }
    (tmp_path / "debug_state_snapshot.json").write_text(json.dumps(payload))
    return tmp_path


def app_for(root):
    return viewer.create_app(root)


def test_import_and_application_do_not_load_runtime_or_start_workers(disarmed_root):
    tmp_path = disarmed_root
    before = (tmp_path / viewer.ARM_FILE).read_bytes()
    # Fresh subprocess catches dependencies even if another suite imported bot.
    code = """
import os, sys, threading
from pathlib import Path
def audit(event, args):
    if event == 'open':
        assert not args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), args[0]
    assert event not in {'os.mkdir', 'os.remove', 'os.rename', 'subprocess.Popen', 'socket.connect'}, event
sys.addaudithook(audit)
before = {t.ident for t in threading.enumerate()}
import dashboard_viewer as v
v.create_app(sys.argv[1])
for name in sys.modules:
    assert not any(name == x or name.startswith(x + '.') for x in (
        'bot', 'btc_conservative_agent', 'showcase_ui', 'ccxt', 'bitfinex_live_executor',
        'local_bitfinex_relay', 'live_copy_monitor_api', 'research_event_writer',
        'public_market_collector', 'paper_engine', 'order_v3', 'websocket')) , name
for name, module in list(sys.modules.items()):
    file = getattr(module, '__file__', '') or ''
    if file and Path(file).resolve().parent == v.SERVICE:
        assert name in {'dashboard_viewer', 'combo_pathway_config', 'tile_card_sections'}, name
assert {t.ident for t in threading.enumerate()} == before
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert [p.name for p in tmp_path.iterdir()] == [viewer.ARM_FILE]
    assert (tmp_path / viewer.ARM_FILE).read_bytes() == before


@pytest.mark.parametrize("name", [
    "LIVE_COPY_ARMED", "RELAY_ARMED", "LIVE_ARMED", "BITFINEX_LIVE_ARMED",
    "BITFINEX_LIVE_ENABLED", "LIVE_TRADING", "ENABLE_LIVE_TRADING", "LIVE_COPY_SOURCE_ENABLED",
    "BITFINEX_API_KEY", "BITFINEX_API_SECRET", "BITFINEX_READ_API_KEY", "BFX_KEY",
    "BFX_API_SECRET", "DATABASE_URL", "BOT_CONTROL_SECRET", "GH_TOKEN",
])
@pytest.mark.parametrize("value", ["1", "0", "", "DO_NOT_PRINT_ME"])
def test_refuses_control_and_credential_names_without_leaking_values(tmp_path, name, value, monkeypatch):
    monkeypatch.setenv(name, value)
    with pytest.raises(viewer.ViewerRefused) as exc:
        viewer.create_app(tmp_path)
    assert "unset" in str(exc.value).lower()
    assert "DO_NOT_PRINT_ME" not in str(exc.value)


def test_main_refusal_is_exit_78_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BFX_API_SECRET", "DO_NOT_PRINT_ME")
    assert viewer.main(["--data-dir", str(tmp_path)]) == 78
    assert "DO_NOT_PRINT_ME" not in capsys.readouterr().err


def test_binds_only_loopback(data_root):
    app = app_for(data_root)
    app.config["VIEWER_PORT"] = 0  # Kernel assigns a test port; CLI disallows zero.
    server = viewer.make_viewer_server(app)
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.socket.getsockname()[0] == "127.0.0.1"
        assert not server.multithread
    finally:
        server.server_close()


def test_reuses_exact_frontend_and_tile_fixture(data_root):
    app = app_for(data_root)
    with app.test_client() as client:
        page = client.get("/", base_url=BASE)
        assert page.status_code == 200
        assert b'id="pathwayLaneTiles"' in page.data
        assert b'/static/dashboard.js' in page.data
        assert b'/static/dashboard-viewer.js' in page.data
        state = client.get("/api/state", base_url=BASE).get_json()
        tile = next(t for t in state["pathway_lane_specs"]["lanes"] if t["lane"] == LANE)
        assert tile["session_stats"]["net_pnl_real"] == 12.5
        assert state["viewer"]["snapshot_revision"] == "fixture-saved-revision"
        assert state["viewer"]["mode"] == "READ_ONLY"
        js = client.get("/static/dashboard.js", base_url=BASE)
        assert b'function renderPathwayLab(d)' in js.data
        assert b'window.initDashboardViewer(refresh)' in js.data
        assert "connect-src 'self'" in page.headers["Content-Security-Policy"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("path", ["/api/fresh_start", "/api/live_arm", "/api/positions/close", "/api/state"])
def test_mutations_are_rejected(data_root, method, path):
    with app_for(data_root).test_client() as client:
        response = client.open(path, method=method, base_url=BASE)
        assert response.status_code == 405
        assert "READ_ONLY" in response.get_json()["error"]


def test_unknown_get_controls_and_dns_rebinding_are_rejected(data_root):
    with app_for(data_root).test_client() as client:
        assert client.get("/api/fresh_epoch_reset", base_url=BASE).status_code == 404
        assert client.get("/api/state", base_url="http://attacker.invalid:7002").status_code == 403


def test_all_requests_leave_data_unchanged(data_root):
    def fingerprint():
        return {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in data_root.iterdir()}
    before = fingerprint()
    with app_for(data_root).test_client() as client:
        for path in ("/", "/static/dashboard.js", "/static/dashboard-viewer.js", "/api/state", "/health"):
            assert client.get(path, base_url=BASE).status_code == 200
        client.post("/api/live_arm", base_url=BASE)
    assert fingerprint() == before


@pytest.mark.parametrize("key", ["live_armed", "effective_live", "relay_armed", "bitfinex_live_enabled"])
def test_persisted_arming_refuses_start_and_subsequent_refresh(disarmed_root, key):
    tmp_path = disarmed_root
    app = app_for(tmp_path)
    (tmp_path / "debug_state_snapshot.json").write_text(json.dumps({key: True}))
    with pytest.raises(viewer.ViewerRefused):
        app_for(tmp_path)
    with app.test_client() as client:
        assert client.get("/api/state", base_url=BASE).status_code == 503
        assert client.get("/health", base_url=BASE).status_code == 503


def test_missing_evidence_stays_unknown_and_does_not_resurrect_retired_tiles(disarmed_root):
    tmp_path = disarmed_root
    snap = viewer.read_snapshot(tmp_path)
    assert all(not v["available"] for k, v in snap["viewer"]["sources"].items() if k != viewer.ARM_FILE)
    assert all("session_stats" not in t for t in snap["pathway_lane_specs"]["lanes"])
    retired = {"lane": "FAMILY_GSB1_CVD_DIV_REGIME"}
    (tmp_path / "pathway_lane_specs.json").write_text(json.dumps({"lanes": [retired]}))
    assert all(t["lane"] != retired["lane"] for t in viewer.read_snapshot(tmp_path)["pathway_lane_specs"]["lanes"])


def test_invalid_oversized_symlinked_and_nonpaper_evidence_fail_closed(disarmed_root, monkeypatch):
    tmp_path = disarmed_root
    target = tmp_path / "debug_state_snapshot.json"
    target.write_text("{")
    with pytest.raises(viewer.ViewerRefused):
        app_for(tmp_path)
    target.write_text('{"long":"xxxxxxxxxxxx"}')
    monkeypatch.setattr(viewer, "MAX_JSON_BYTES", 8)
    with pytest.raises(viewer.ViewerRefused):
        app_for(tmp_path)
    monkeypatch.setattr(viewer, "MAX_JSON_BYTES", 1024)
    target.unlink()  # Only this test's own temporary fixture, not evidence.
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text("{}")
    target.symlink_to(outside)
    with pytest.raises(viewer.ViewerRefused):
        app_for(tmp_path)
    target.unlink()
    (tmp_path / "paper_lifecycle_v1.json").write_text('{"paper_only":false}')
    with pytest.raises(viewer.ViewerRefused):
        app_for(tmp_path)


def test_evidence_is_text_not_executable_markup_and_secrets_are_omitted(data_root):
    path = data_root / "debug_state_snapshot.json"
    data = json.loads(path.read_text())
    data["pathway_lane_specs"]["lanes"][0].update({
        "subtitle": '<img src=x onerror="alert(1)">', "api_key": "DO_NOT_PRINT_ME"})
    path.write_text(json.dumps(data))
    text = json.dumps(viewer.read_snapshot(data_root))
    assert "<img" not in text
    assert "DO_NOT_PRINT_ME" not in text


def test_python_311_syntax():
    ast.parse((viewer.SERVICE / "dashboard_viewer.py").read_text(), feature_version=(3, 11))


def test_tile_page_renders_in_browser_from_saved_fixture(data_root):
    sync_playwright = pytest.importorskip("playwright.sync_api",
        reason="Optional Chromium dependency; the box verification installs it").sync_playwright
    app = app_for(data_root)
    app.config["VIEWER_PORT"] = 0
    server = viewer.make_viewer_server(app)
    port = server.server_port
    # Host validation uses the actual test port, like the configured CLI port.
    # Recreate app with that port; the existing bound server still owns its socket.
    server.app = viewer.create_app(data_root, port=port)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda req: requests.append((req.method, req.url)))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("document.querySelector('#pathwayLaneTiles').textContent.includes('$12.50')")
            cards = page.locator('#pathwayLaneTiles > div')
            assert cards.count() == len(viewer.ACTIVE_TILE_ORDER)
            assert "READ ONLY" in page.locator('#viewerBanner').inner_text()
            assert page.locator('#pathwayLaneTiles .tile-live-btn').count() == 0
            assert page.locator('#pathwayLaneTiles button').count() == 0
            assert page.locator('.viewer-missing-stats').count() == len(viewer.ACTIVE_TILE_ORDER) - 1
            page.get_by_role('button', name='Refresh now').click()
            assert page.evaluate("fetch('/api/live_arm', {method:'POST'}).then(() => false).catch(() => true)")
            assert page.evaluate("fetch('http://127.0.0.1:7810/health').then(() => false).catch(() => true)")
            assert all(method == 'GET' and url.startswith(f'http://127.0.0.1:{port}/') for method, url in requests)
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_new_live_environment_is_refused_on_each_request(data_root, monkeypatch):
    app = app_for(data_root)
    monkeypatch.setenv("LIVE_ARMED", "1")
    with app.test_client() as client:
        assert client.get("/api/state", base_url=BASE).status_code == 503


# B1: the canonical sidecar's CURRENT state.enabled is mandatory boolean false.
@pytest.mark.parametrize("value", [True, None, {}, {"off": False}, [], [False],
    "false", "False", "0", "", 0, 0.0, 1, "MISSING"],
    ids=["true", "null", "object", "nonempty-object", "list", "nonempty-list",
         "false-string", "False-string", "zero-string", "empty-string", "zero-int", "zero-float", "one-int", "missing"])
def test_b1_sidecar_uncertain_arm_values_fail_at_startup_and_refresh(disarmed_root, value):
    app = app_for(disarmed_root)
    state = {} if value == "MISSING" else {"enabled": value}
    (disarmed_root / viewer.ARM_FILE).write_text(json.dumps({"schema": viewer.ARM_SCHEMA, "state": state}))
    with pytest.raises(viewer.ViewerRefused):
        app_for(disarmed_root)
    with app.test_client() as client:
        for path in ("/api/state", "/health"):
            assert client.get(path, base_url=BASE).status_code == 503


@pytest.mark.parametrize("raw", ["MISSING_FILE", "{", "{}", '{"schema":"unknown","state":{"enabled":false}}',
    '{"schema":"fly_live_copy_output_v1"}', '{"schema":"fly_live_copy_output_v1","state":null}',
    '{"schema":"fly_live_copy_output_v1","state":[]}', '{"schema":"fly_live_copy_output_v1","state":"false"}'])
def test_b1_sidecar_missing_malformed_or_unknown_schema_is_not_disarmed(tmp_path, raw):
    if raw != "MISSING_FILE":
        (tmp_path / viewer.ARM_FILE).write_text(raw)
    with pytest.raises(viewer.ViewerRefused):
        app_for(tmp_path)


@pytest.mark.parametrize("key", ["armed", "live_armed", "live_enabled", "bitfinex_live_enabled",
    "live_copy_armed", "relay_armed", "bitfinex_live_armed", "effective_live", "live_copy_output_enabled",
    "live_copy_source_enabled", "live_tiles_on", "bitfinex_live_on", "live_tiles_requested"])
@pytest.mark.parametrize("value", [None, {}, [], "false", "0", "", 0, 0.0, True])
def test_b1_all_recognized_arm_fields_require_boolean_false(disarmed_root, key, value):
    (disarmed_root / "debug_state_snapshot.json").write_text(json.dumps({key: value}))
    with pytest.raises(viewer.ViewerRefused):
        viewer.read_snapshot(disarmed_root)


def test_b1_false_sidecar_and_false_arm_fields_are_readonly_and_history_is_not_current(disarmed_root):
    (disarmed_root / viewer.ARM_FILE).write_text(json.dumps({"schema": viewer.ARM_SCHEMA,
        "state": {"enabled": False, "history": [{"enabled": True}]}}))
    (disarmed_root / "paper_lifecycle_v1.json").write_text(json.dumps({"paper_only": True, "live_armed": False}))
    (disarmed_root / "debug_state_snapshot.json").write_text(json.dumps({"live_armed": False}))
    before = {p.name: p.read_bytes() for p in disarmed_root.iterdir()}
    assert viewer.read_snapshot(disarmed_root)["viewer"]["live"] == "OFF"
    assert {p.name: p.read_bytes() for p in disarmed_root.iterdir()} == before


def test_b1_missing_lifecycle_arm_value_is_not_false(disarmed_root):
    (disarmed_root / "paper_lifecycle_v1.json").write_text('{"paper_only":true}')
    with pytest.raises(viewer.ViewerRefused):
        app_for(disarmed_root)


# B2: schema projection must exclude unknown keys at every supported depth/type.
@pytest.mark.parametrize("name", ["apiKey", "privateKey", "credentials", "unknown"])
def test_b2_unknown_and_credential_fields_never_reach_api_state(data_root, name):
    marker = "SYNTHETIC_DO_NOT_OUTPUT_" + name
    path = data_root / "debug_state_snapshot.json"
    data = json.loads(path.read_text())
    row = data["pathway_lane_specs"]["lanes"][0]
    value = {"key": marker, "unknown": {"privateKey": marker}} if name in {"credentials", "unknown"} else marker
    row[name] = value
    row["session_stats"][name] = value
    row["session_stats"]["pnl_breakdown"] = {"forced_closes": 2, name: value}
    row["card_sections"] = {"entry": ["known display text", {name: value}],
        "exit": {"order": "first trigger", "live": ["known exit", {name: value}], name: value}, name: value}
    row["entry"] = {"trigger": "known trigger", "filters": {"entry_mode": "known mode", name: value}, name: value}
    row["hypothesis_result"] = {"status": "SAVED_ONLY", name: value}
    row["chase_timing"] = {"contract": "known contract", name: value}
    data["git_rev"] = {name: value}  # Even a known scalar field cannot carry an object.
    data[name] = value
    path.write_text(json.dumps(data))
    with app_for(data_root).test_client() as client:
        response = client.get("/api/state", base_url=BASE)
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert marker not in text
        assert '"' + name + '"' not in text
        output = response.get_json()
        lane = next(row for row in output["pathway_lane_specs"]["lanes"] if row["lane"] == LANE)
        assert lane["card_sections"]["entry"] == ["known display text"]
        assert lane["entry"]["filters"] == {"entry_mode": "known mode"}
        assert lane["session_stats"]["pnl_breakdown"] == {"forced_closes": 2}
        assert lane["session_stats"]["net_pnl_real"] == 12.5
        assert output["viewer"]["snapshot_revision"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1, 1.5, True])
def test_display_allowlist_drops_nonfinite_or_invalid_trade_counts(data_root, value):
    path = data_root / "debug_state_snapshot.json"
    data = json.loads(path.read_text())
    data["pathway_lane_specs"]["lanes"][0]["session_stats"]["real_fills"] = value
    path.write_text(json.dumps(data))
    lane = next(row for row in viewer.read_snapshot(data_root)["pathway_lane_specs"]["lanes"] if row["lane"] == LANE)
    assert "session_stats" not in lane
