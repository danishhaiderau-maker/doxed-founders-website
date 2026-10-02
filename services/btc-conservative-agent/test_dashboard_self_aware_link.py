"""The :9001 data-health page links and summarises the :9021 self-aware data view, failing soft."""
import http.server
import json
import socket
import threading

import pytest

from research import research_dashboard as dashboard

SUMMARY = {
    "schema": "self_aware_data_v1", "generated_at": "2026-10-02T11:40:34Z", "mirror_head": "2026-10-02T11:34:52Z",
    "streams": 100, "catalogued": 27, "stale_critical": [], "dead_field_count": 399,
    "watch_alarms": {"ai_calls": ["context.delta_change=DEAD_ZERO (0.0)"], "<script>": ["x<y"]},
    "tape": {"fill_pct_24h": 99.302, "gaps_24h": 12},
}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(dashboard, "_SELF_AWARE_CACHE", {"at": 0.0, "value": None})
    monkeypatch.setattr(dashboard, "_API_RESPONSE_CACHE", {})
    monkeypatch.setattr(dashboard, "_data_health_payload", lambda: ({}, {}, {}))
    monkeypatch.setattr(dashboard, "_archive_payload", lambda: ({"status": "UNAVAILABLE", "data": None}, {}))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_self_aware():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(SUMMARY).encode() if self.path == "/api/selfaware/data" else b"{}"
            self.send_response(200 if self.path == "/api/selfaware/data" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", _free_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_data_health_page_summarises_and_links_self_aware(monkeypatch, fake_self_aware):
    monkeypatch.setattr(dashboard, "SELF_AWARE_URL", fake_self_aware)
    client = dashboard.app.test_client()
    page = client.get("/data-health").get_data(as_text=True)
    section = page.split("<h2>Self-aware data awareness</h2>", 1)[1]
    assert f"href='{fake_self_aware}/data'" in section
    assert "100 streams (27 catalogued)" in section and "dead fields: 399" in section
    assert "tape filled 24h: 99.302% with 12 gaps" in section
    assert "context.delta_change=DEAD_ZERO" in section
    assert "<script>" not in section and "&lt;script&gt;" in section
    api = client.get("/api/streams/self-aware-data").get_json()
    assert api["status"] == "OK" and api["data"]["streams"] == 100


def test_unreachable_self_aware_never_breaks_the_page(monkeypatch):
    monkeypatch.setattr(dashboard, "SELF_AWARE_URL", f"http://127.0.0.1:{_free_port()}")
    client = dashboard.app.test_client()
    resp = client.get("/data-health")
    assert resp.status_code == 200
    section = resp.get_data(as_text=True).split("<h2>Self-aware data awareness</h2>", 1)[1]
    assert "UNAVAILABLE" in section and "/data'" in section
    api = client.get("/api/streams/self-aware-data").get_json()
    assert api["status"] == "UNAVAILABLE" and api["data"] is None and api["reason"]
