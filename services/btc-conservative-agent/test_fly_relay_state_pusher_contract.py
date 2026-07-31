import hashlib
import hmac
import json
import unittest

from fly_relay_state_pusher import build_signed_snapshot_payload


class FlyRelayStatePusherContractTests(unittest.TestCase):
    def test_payload_signature_covers_exact_json_and_sequence(self):
        snapshot = {
            "server_ts": "2026-07-31T00:00:00Z",
            "dashboard_owner": True,
            "dashboard_port": 7002,
            "nested": {"unicode": "Melbourne ✓", "value": 1.0},
        }
        payload = build_signed_snapshot_payload(1234, snapshot, "test-secret")

        self.assertEqual(json.loads(payload["snapshot_json"]), snapshot)
        expected = hmac.new(
            b"test-secret",
            f"1234.{payload['snapshot_json']}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(payload["snapshot_hmac"], expected)
        self.assertEqual(len(payload["snapshot_hmac"]), 64)

    def test_missing_secret_fails_closed(self):
        with self.assertRaises(ValueError):
            build_signed_snapshot_payload(1, {}, "")


if __name__ == "__main__":
    unittest.main()
