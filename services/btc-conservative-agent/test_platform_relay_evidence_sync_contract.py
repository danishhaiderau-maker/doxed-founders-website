from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SCRIPT = (REPO / "scripts" / "sync-platform-relay-evidence.ps1").read_text(encoding="utf-8")
LOOP = (REPO / "scripts" / "sync-fly-bot-data-loop.ps1").read_text(encoding="utf-8")
CONTROLLER = (REPO / "apps" / "api" / "src" / "trading-agents" / "trading-agents.controller.ts").read_text(encoding="utf-8")
SERVICE = (REPO / "apps" / "api" / "src" / "trading-agents" / "trading-agents.service.ts").read_text(encoding="utf-8")


def test_export_is_authenticated_user_scoped_and_event_complete():
    assert "ops/relay-evidence" in CONTROLLER
    assert "X-Bot-Admin-Token" in CONTROLLER
    assert "await this.getOpsRelayStatus" in SERVICE
    assert "canonicalTradeId" in SERVICE
    assert "eventType: event.eventType" in SERVICE
    assert "generatingRevision" in SERVICE and "runIdentity" in SERVICE


def test_sync_is_atomic_and_fails_closed_on_missing_provenance():
    assert "Invoke-WebRequest" in SCRIPT
    assert "relay_lifecycle_evidence_v1" in SCRIPT
    assert "generatingRevision" in SCRIPT and "runIdentity" in SCRIPT
    assert "Move-Item -LiteralPath $temp" in SCRIPT
    assert "PROVENANCE_INCOMPLETE" in SCRIPT
    assert "$payload.agentSlug -cne [string]$agentSlug" in SCRIPT
    assert "$payload.userId -cne [string]$userId" in SCRIPT
    assert "DUPLICATE_EVENT" in SCRIPT and "TOO_LARGE" in SCRIPT and "STALE" in SCRIPT
    assert "PLATFORM_SOURCE_BOT_URL" in SCRIPT
    assert "/api/data-sync/platform-relay-evidence" in SCRIPT
    assert "FORWARD_ACK_INVALID" in SCRIPT


def test_sync_never_accepts_or_outputs_token_on_command_line():
    assert "[string]$AdminToken" not in SCRIPT
    assert "GetEnvironmentVariable('BOT_ADMIN_TOKEN', 'Process')" in SCRIPT
    assert "Write-Output $adminToken" not in SCRIPT
    assert "Write-Output $adminToken" not in SCRIPT
    assert "throw \"[RELAY_EVIDENCE_$Code]\"" in SCRIPT


def test_continuous_fly_mirror_also_schedules_platform_evidence_join():
    assert 'sync-platform-relay-evidence.ps1' in LOOP
    assert 'relay_lifecycle_evidence_v1.json' in LOOP
    assert '$env:PLATFORM_RELAY_EVIDENCE_FILE = $relayEvidenceDestination' in LOOP
    assert 'PLATFORM_API_BASE_URL' in LOOP
    assert 'PLATFORM_RELAY_AGENT_SLUG' in LOOP
    assert 'PLATFORM_RELAY_USER_ID' in LOOP
    assert LOOP.index('sync-platform-relay-evidence.ps1') < LOOP.index('if (-not ($forceByTime')
    assert 'relayEvidence = $relayEvidenceStatus' in LOOP
    assert 'lastSuccessAt = $relayEvidenceLastSuccessAt' in LOOP
    assert 'relay-evidence=$safeCode' in LOOP
    relay_log = LOOP.split('Add-Content -LiteralPath $logFile -Value (', 1)[1].split(')', 1)[0]
    assert '$_.Exception.Message' not in relay_log


def _run_sync(tmp_path: Path, payload: dict, token: str = "secret-never-print"):
    body = json.dumps(payload).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get("X-Bot-Admin-Token") == token
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            assert self.path == "/api/data-sync/platform-relay-evidence"
            assert self.headers.get("X-Bot-Admin-Token") == token
            forwarded = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            assert forwarded == body
            ack = json.dumps({
                "ok": True,
                "schema": "relay_lifecycle_evidence_v1",
                "sha256": __import__("hashlib").sha256(body).hexdigest(),
                "records": len(payload["records"]),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(ack)))
            self.end_headers()
            self.wfile.write(ack)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    destination = tmp_path / "relay_lifecycle_evidence_v1.json"
    env = os.environ.copy()
    env.update({
        "BOT_ADMIN_TOKEN": token,
        "PLATFORM_API_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "PLATFORM_RELAY_AGENT_SLUG": "conservative-btc",
        "PLATFORM_RELAY_USER_ID": "user-scope",
        "PLATFORM_RELAY_EVIDENCE_FILE": str(destination),
        "PLATFORM_SOURCE_BOT_URL": f"http://127.0.0.1:{server.server_port}",
    })
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / "scripts" / "sync-platform-relay-evidence.ps1")],
            env=env, capture_output=True, text=True, timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
    return result, destination


def _valid_payload():
    return {
        "schema": "relay_lifecycle_evidence_v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "generatingRevision": "272c88c02cfd22034794182e5d1fb21c98790841",
        "runIdentity": "deployment-test",
        "agentSlug": "conservative-btc",
        "userId": "user-scope",
        "records": [{
            "canonicalTradeId": "cont-test",
            "lifecycleId": "cycle-test",
            "participantId": "participant-test",
            "events": [{"id": "event-test", "eventType": "ORDER_ACK", "createdAt": datetime.now(timezone.utc).isoformat()}],
        }],
    }


def test_mocked_authenticated_sync_is_atomic_and_preserves_old_on_scope_failure(tmp_path):
    valid = _valid_payload()
    result, destination = _run_sync(tmp_path, valid)
    assert result.returncode == 0, result.stderr
    assert json.loads(destination.read_text(encoding="utf-8"))["records"][0]["canonicalTradeId"] == "cont-test"
    old = destination.read_bytes()

    invalid = _valid_payload()
    invalid["userId"] = "wrong-user"
    result, destination = _run_sync(tmp_path, invalid)
    assert result.returncode != 0
    assert destination.read_bytes() == old
    combined = result.stdout + result.stderr
    assert "RELAY_EVIDENCE_SCOPE_MISMATCH" in combined
    assert "secret-never-print" not in combined
    assert "user-scope" not in combined
