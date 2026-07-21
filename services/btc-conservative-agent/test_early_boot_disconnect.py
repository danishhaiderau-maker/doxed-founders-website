import importlib.util
import io
import unittest
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


if __name__ == "__main__":
    unittest.main()
