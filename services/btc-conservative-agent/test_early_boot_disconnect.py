import importlib.util
import io
import json
import socket
import unittest
import urllib.request
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("early_boot.py")
SPEC = importlib.util.spec_from_file_location("early_boot_under_test", MODULE_PATH)
early_boot = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(early_boot)


class _DisconnectedSocket(io.BytesIO):
    def write(self, _value):
        raise ConnectionAbortedError(10053, "client closed")


class EarlyBootDisconnectTests(unittest.TestCase):
    def test_boot_server_is_bounded_and_proxy_resilient(self):
        self.assertTrue(early_boot._ResponsiveThreadingHTTPServer.daemon_threads)
        self.assertFalse(early_boot._ResponsiveThreadingHTTPServer.block_on_close)
        self.assertGreaterEqual(
            early_boot._ResponsiveThreadingHTTPServer.request_queue_size,
            128,
        )
        self.assertLessEqual(
            early_boot._ResponsiveThreadingHTTPServer.client_io_timeout_sec,
            2.0,
        )

    def test_accepted_client_receives_io_deadline(self):
        server = early_boot._ResponsiveThreadingHTTPServer(
            ("127.0.0.1", 0),
            early_boot.BaseHTTPRequestHandler,
        )
        client = socket.create_connection(server.server_address, timeout=2)
        try:
            accepted, _ = server.get_request()
            try:
                self.assertEqual(
                    accepted.gettimeout(),
                    early_boot._ResponsiveThreadingHTTPServer.client_io_timeout_sec,
                )
            finally:
                accepted.close()
        finally:
            client.close()
            server.server_close()

    def test_abandoned_health_response_is_not_raised(self):
        class Handler:
            wfile = _DisconnectedSocket()

            def send_response(self, *_args, **_kwargs):
                return None

            def send_header(self, *_args, **_kwargs):
                return None

            def end_headers(self):
                return None

        early_boot._write_json_response(Handler(), 503, {"boot": "starting"})

    def test_boot_ping_reports_owner_and_revision(self):
        early_boot.start_early_ping_server(
            0,
            host="127.0.0.1",
            version="test-version",
            source_git_rev="1234567890abcdef",
        )
        try:
            port = early_boot._server.server_address[1]
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ping", timeout=2
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["boot"], "starting")
            self.assertTrue(payload["dashboard_owner"])
            self.assertEqual(payload["dashboard_pid"], payload["bot_pid"])
            self.assertEqual(payload["dashboard_port"], port)
            self.assertEqual(payload["source_git_rev"], "1234567890ab")
        finally:
            early_boot.stop_early_ping_server()


if __name__ == "__main__":
    unittest.main()
