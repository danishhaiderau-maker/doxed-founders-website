"""Regression coverage for bounded independent executable-depth refresh."""

import ast
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))
FUNCTIONS = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}


def compile_function(name, namespace):
    module = ast.Module(body=[FUNCTIONS[name]], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)


class OrderBookRefreshTest(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.namespace = {
            "time": time,
            "uuid": uuid,
            "state": self.state,
            "state_lock": threading.RLock(),
            "_book_refresh_lock": threading.Lock(),
            "_book_refresh_claim_token": None,
            "_book_refresh_claim_started_ts": 0.0,
            "_last_book_refresh_ts": 0.0,
            "BOOK_REFRESH_SEC": 3.0,
            "BOOK_FORCE_MIN_SEC": 1.5,
            "BOOK_REFRESH_CLAIM_TTL_SEC": 8.0,
            "logger": SimpleNamespace(warning=lambda *_a, **_k: None),
        }
        compile_function("refresh_order_book_state", self.namespace)

    def test_concurrent_refresh_coalesces_without_holding_coordination_lock(self):
        entered = threading.Event()
        release = threading.Event()

        def blocked_fetch():
            entered.set()
            release.wait(2)
            return {"bids": [[100.0, 1, 1.0]], "asks": [[101.0, 1, 2.0]]}

        self.namespace["_fetch_bitfinex_book_rest_hot"] = blocked_fetch
        worker = threading.Thread(
            target=lambda: self.namespace["refresh_order_book_state"](force=True),
            daemon=True,
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.assertFalse(self.namespace["refresh_order_book_state"](force=True))
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertTrue(self.namespace["_book_refresh_lock"].acquire(blocking=False))
        self.namespace["_book_refresh_lock"].release()
        release.set()
        worker.join(1)

    def test_success_updates_depth_and_timestamp_atomically(self):
        self.namespace["_fetch_bitfinex_book_rest_hot"] = lambda: {
            "bids": [[200.0, 1, 0.25]],
            "asks": [[201.0, 1, 0.5]],
        }
        self.assertTrue(self.namespace["refresh_order_book_state"](force=True))
        self.assertEqual(self.state["bid_size_btc"], 0.25)
        self.assertEqual(self.state["ask_size_btc"], 0.5)
        self.assertEqual(self.state["book_ts"], self.state["book_refresh_last_success_ts"])
        self.assertEqual(self.state["book_refresh_consecutive_failures"], 0)
        self.assertFalse(self.state["book_refresh_inflight"])

    def test_failure_releases_claim_without_refreshing_stale_timestamp(self):
        self.state["book_ts"] = 1.0

        def fail():
            raise TimeoutError("bounded timeout")

        self.namespace["_fetch_bitfinex_book_rest_hot"] = fail
        self.assertFalse(self.namespace["refresh_order_book_state"](force=True))
        self.assertEqual(self.state["book_ts"], 1.0)
        self.assertIsNone(self.namespace["_book_refresh_claim_token"])
        self.assertEqual(self.state["book_refresh_consecutive_failures"], 1)


class OrderBookWorkerContractTest(unittest.TestCase):
    def test_dedicated_worker_is_started_and_fill_gate_remains_strict(self):
        worker = ast.get_source_segment(SOURCE, FUNCTIONS["order_book_refresh_loop"])
        venue_gate = ast.get_source_segment(SOURCE, FUNCTIONS["_venue_executable_showcase_fill"])
        self.assertIn("refresh_order_book_state()", worker)
        self.assertIn("BOOK_REFRESH_SEC - elapsed", worker)
        self.assertIn(
            "threading.Thread(target=safe_thread(order_book_refresh_loop)", SOURCE
        )
        self.assertIn("VENUE_EXECUTABLE_MAX_BOOK_AGE_SEC", venue_gate)
        self.assertIn('"BOOK_STALE"', venue_gate)


if __name__ == "__main__":
    unittest.main()
