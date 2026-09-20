"""Real HttpListener acceptance for the production local-reset route handler."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROUTE_MODULE = REPO / "scripts" / "local-reset-http.ps1"
HOST_SCRIPT = HERE / "test_local_reset_http_listener_host.ps1"
ALLOWED_ORIGIN = "https://bot.doxxedcrypto.digital"


FIXTURE_CLI = r'''from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

agent = Path(os.environ["LOCAL_RESET_FIXTURE_AGENT_ROOT"])
sys.path.insert(0, str(agent))
from local_fresh_collection import capability_status, execute_operation, queue_operation, read_operation

canonical = Path(os.environ["LOCAL_RESET_FIXTURE_CANONICAL"])
archives = Path(os.environ["LOCAL_RESET_FIXTURE_ARCHIVES"])
state = Path(os.environ["LOCAL_RESET_FIXTURE_STATE"])

def audit(_canonical, _archives):
    return {"schema":"http_fixture_owner_audit_v1","safe":True,"owners":[],"running_tasks":[]}

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=("capability","queue","status","run"))
parser.add_argument("--operation-id")
args = parser.parse_args()
try:
    if args.command == "capability":
        result = capability_status(canonical_root=canonical, archive_root=archives)
    elif args.command == "queue":
        result, replay = queue_operation(canonical_root=canonical, archive_root=archives,
            state_root=state, request=json.load(sys.stdin), expected_canonical_root=canonical,
            expected_archive_root=archives)
        result = dict(result, replay=replay)
    elif args.command == "status":
        result = read_operation(state_root=state, operation_id=args.operation_id)
    else:
        result = execute_operation(state_root=state, operation_id=args.operation_id,
            owner_auditor=audit, expected_canonical_root=canonical,
            expected_archive_root=archives)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
except (OSError, ValueError, RuntimeError) as exc:
    print(json.dumps({"ok":False,"error":str(exc)}, sort_keys=True))
    raise SystemExit(2)
'''


class LocalResetHttpListenerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.canonical = base / "canonical-research-data"
        self.archives = base / "research_session_archives"
        self.state = base / "state"
        self.canonical.mkdir()
        self.archives.mkdir()
        (self.canonical / "canonical_dataset_current.json").write_text(
            json.dumps({"dataset_epoch": "fixture-generation"}), encoding="utf-8"
        )
        (self.canonical / "raw.jsonl").write_text("fixture\n", encoding="utf-8")
        (self.archives / "old.json").write_text("{}", encoding="utf-8")
        self.capability = secrets.token_urlsafe(32)
        self.capability_hash = base / "capability.sha256"
        self.capability_hash.write_text(
            hashlib.sha256(self.capability.encode()).hexdigest() + "\n", encoding="utf-8"
        )
        self.fixture_cli = base / "fixture_cli.py"
        self.fixture_cli.write_text(FIXTURE_CLI, encoding="utf-8")
        self.ready = base / "listener.ready"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            self.skipTest("PowerShell is required")
        environment = os.environ.copy()
        environment.update(
            LOCAL_RESET_FIXTURE_AGENT_ROOT=str(HERE),
            LOCAL_RESET_FIXTURE_CANONICAL=str(self.canonical),
            LOCAL_RESET_FIXTURE_ARCHIVES=str(self.archives),
            LOCAL_RESET_FIXTURE_STATE=str(self.state),
        )
        self.host = subprocess.Popen(
            [
                shell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(HOST_SCRIPT),
                "-Port",
                str(self.port),
                "-RouteModule",
                str(ROUTE_MODULE),
                "-FixtureCli",
                str(self.fixture_cli),
                "-CapabilityHashPath",
                str(self.capability_hash),
                "-ReadyPath",
                str(self.ready),
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline and not self.ready.exists() and self.host.poll() is None:
            time.sleep(0.05)
        if not self.ready.exists():
            stdout, stderr = self.host.communicate(timeout=5)
            self.fail(f"fixture HttpListener did not start: {stdout} {stderr}")

    def tearDown(self):
        if hasattr(self, "host") and self.host.poll() is None:
            self.host.terminate()
            try:
                self.host.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.host.kill()
                self.host.wait(timeout=5)
        self.temporary.cleanup()

    def request(self, method, path, *, capability=True, origin=ALLOWED_ORIGIN, body=None):
        headers = {"Origin": origin}
        if capability:
            headers["X-Local-Reset-Capability"] = self.capability
        encoded = None
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            payload = json.loads(response.read().decode())
            return response.status, payload
        finally:
            connection.close()

    def drop_post_response(self, path, body):
        encoded = json.dumps(body, separators=(",", ":")).encode()
        request = (
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Origin: {ALLOWED_ORIGIN}\r\n"
            f"X-Local-Reset-Capability: {self.capability}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n"
        ).encode() + encoded
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as client:
            client.sendall(request)
            # Deliberately close without reading the HTTP response.

    def test_authenticated_route_status_replay_conflict_and_lost_response(self):
        capability_path = "/api/local-research-reset/v1/capability"
        request_path = "/api/local-research-reset/v1/requests"

        status, payload = self.request(
            "GET", capability_path, origin="https://attacker.invalid"
        )
        self.assertEqual((status, payload["error"]), (403, "LOCAL_RESET_ORIGIN_REFUSED"))

        status, payload = self.request("GET", capability_path, capability=False)
        self.assertEqual((status, payload["error"]), (401, "LOCAL_RESET_UNAUTHORIZED"))

        status, payload = self.request("GET", request_path)
        self.assertEqual((status, payload["error"]), (405, "METHOD_NOT_ALLOWED"))

        status, capability = self.request("GET", capability_path)
        self.assertEqual(status, 200)
        self.assertEqual(capability["protocol"], "local_research_reset_protocol_v1")
        self.assertEqual(capability["scope_version"], "laptop_research_scope_v1")
        self.assertEqual(capability["current_local_generation"], "fixture-generation")

        operation_id = secrets.token_hex(16)
        body = {
            "request_id": operation_id,
            "confirmation": "DELETE LAPTOP RESEARCH ONLY",
            "expected_local_generation": "fixture-generation",
        }
        status, queued = self.request("POST", request_path, body=body)
        self.assertEqual(status, 202)
        self.assertEqual(queued["operation_id"], operation_id)
        self.assertEqual(queued["status"], "QUEUED")
        self.assertFalse(queued["replay"])
        self.assertNotEqual(queued["status"], "COMPLETE")

        self.drop_post_response(request_path, body)
        status, replay = self.request("POST", request_path, body=body)
        self.assertEqual(status, 202)
        self.assertEqual(replay["operation_id"], operation_id)
        self.assertTrue(replay["replay"])

        conflict = dict(body, expected_local_generation="conflicting-generation")
        status, payload = self.request("POST", request_path, body=conflict)
        self.assertEqual((status, payload["error"]), (409, "LOCAL_RESET_REPLAY_CONFLICT"))

        status_path = f"/api/local-research-reset/v1/operations/{operation_id}"
        status, payload = self.request("GET", status_path, capability=False)
        self.assertEqual((status, payload["error"]), (401, "LOCAL_RESET_UNAUTHORIZED"))

        deadline = time.time() + 15
        observed = []
        while time.time() < deadline:
            status, payload = self.request("GET", status_path)
            self.assertEqual(status, 200)
            observed.append(payload["status"])
            if payload["status"] == "COMPLETE":
                break
            time.sleep(0.1)
        self.assertEqual(payload["status"], "COMPLETE", observed)
        self.assertTrue(payload["deletion_reconciled"])
        self.assertTrue(payload["exact_hash_reconciliation"])
        self.assertEqual(payload["sync_state"], "BLOCKED_PENDING_VERIFIED_IMPORT")
        self.assertFalse(payload["fly_mutation_requested"])
        completion = Path(payload["completion_receipt_path"])
        self.assertEqual(
            hashlib.sha256(completion.read_bytes()).hexdigest(),
            payload["completion_receipt_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
