"""Focused regression coverage for bounded BBO readiness recovery."""

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
FUNCTIONS = {
    node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)
}


def compile_function(name, namespace):
    module = ast.Module(body=[FUNCTIONS[name]], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)


class BboRefreshNonblockingTest(unittest.TestCase):
    def setUp(self):
        self.state = {"ws_last_tick": time.time()}
        self.namespace = {
            "time": time,
            "uuid": uuid,
            "state": self.state,
            "state_lock": threading.RLock(),
            "_bbo_refresh_lock": threading.Lock(),
            "_bbo_refresh_claim_token": None,
            "_bbo_refresh_claim_started_ts": 0.0,
            "_last_bbo_refresh_ts": 0.0,
            "BBO_REFRESH_SEC": 3.0,
            "BBO_REFRESH_CLAIM_TTL_SEC": 8.0,
            "WS_ENTRY_FRESH_SEC": 60.0,
            "logger": SimpleNamespace(warning=lambda *_args, **_kwargs: None),
        }
        compile_function("refresh_bbo_state", self.namespace)

    def test_concurrent_refresh_is_coalesced_without_waiting_on_network(self):
        entered = threading.Event()
        release = threading.Event()

        def hung_fetch():
            entered.set()
            release.wait(2)
            return {
                "bid": 100.0, "ask": 101.0, "last": 100.5,
                "spread_usd": 1.0, "spread_pct": 1.0,
            }

        self.namespace["_fetch_bitfinex_ticker_rest_hot"] = hung_fetch
        worker = threading.Thread(
            target=lambda: self.namespace["refresh_bbo_state"](force=True),
            daemon=True,
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.assertFalse(self.namespace["refresh_bbo_state"](force=True))
        self.assertLess(time.monotonic() - started, 0.1)
        # The refresh coordination lock is not held by the blocked network call.
        self.assertTrue(self.namespace["_bbo_refresh_lock"].acquire(blocking=False))
        self.namespace["_bbo_refresh_lock"].release()
        release.set()
        worker.join(1)

    def test_stale_claim_can_be_replaced_and_success_is_atomic(self):
        self.namespace["_bbo_refresh_claim_token"] = "abandoned"
        self.namespace["_bbo_refresh_claim_started_ts"] = time.time() - 30
        self.namespace["_fetch_bitfinex_ticker_rest_hot"] = lambda: {
            "bid": 200.0, "ask": 201.0, "last": 200.5,
            "spread_usd": 1.0, "spread_pct": 0.5,
        }
        self.assertTrue(self.namespace["refresh_bbo_state"](force=True))
        self.assertEqual(self.state["bid"], 200.0)
        self.assertEqual(self.state["ask"], 201.0)
        self.assertEqual(self.state["rest_price"], 200.5)
        self.assertEqual(self.state["rest_price_ts"], self.state["rest_last_tick"])
        self.assertEqual(self.state["rest_price_ts"], self.state["bbo_ts"])
        self.assertEqual(self.state["bbo_refresh_consecutive_failures"], 0)
        self.assertFalse(self.state["bbo_refresh_inflight"])
        self.assertEqual(self.state["bbo_refresh_stale_claims"], 1)

    def test_failure_releases_claim_and_preserves_strict_staleness(self):
        self.state["rest_price_ts"] = 1.0

        def fail():
            raise TimeoutError("bounded timeout")

        self.namespace["_fetch_bitfinex_ticker_rest_hot"] = fail
        self.assertFalse(self.namespace["refresh_bbo_state"](force=True))
        self.assertIsNone(self.namespace["_bbo_refresh_claim_token"])
        self.assertFalse(self.state["bbo_refresh_inflight"])
        self.assertEqual(self.state["rest_price_ts"], 1.0)
        self.assertEqual(self.state["bbo_refresh_consecutive_failures"], 1)
        self.assertIn("bounded timeout", self.state["bbo_refresh_last_error"])


class WorkerIsolationSourceContractTest(unittest.TestCase):
    def test_ohlcv_has_an_independent_worker(self):
        state_monitor = ast.get_source_segment(SOURCE, FUNCTIONS["state_monitor_loop"])
        ohlcv_worker = ast.get_source_segment(SOURCE, FUNCTIONS["ohlcv_refresh_loop"])
        self.assertNotIn("fetch_ohlcv()", state_monitor)
        self.assertIn("fetch_ohlcv()", ohlcv_worker)
        self.assertIn(
            "threading.Thread(target=safe_thread(ohlcv_refresh_loop)", SOURCE
        )

    def test_bbo_has_an_independent_bounded_cadence_worker(self):
        state_monitor = ast.get_source_segment(SOURCE, FUNCTIONS["state_monitor_loop"])
        bbo_worker = ast.get_source_segment(SOURCE, FUNCTIONS["bbo_refresh_loop"])
        self.assertNotIn("refresh_bbo_state()", state_monitor)
        self.assertIn("refresh_bbo_state()", bbo_worker)
        self.assertIn("BBO_REFRESH_SEC - elapsed", bbo_worker)
        self.assertIn(
            "threading.Thread(target=safe_thread(bbo_refresh_loop)", SOURCE
        )

    def test_bbo_worker_repeats_without_creating_a_request_stampede(self):
        calls = []

        class BoundedShutdown:
            def __init__(self):
                self.waits = []

            def is_set(self):
                return len(self.waits) >= 3

            def wait(self, seconds):
                self.waits.append(seconds)
                return self.is_set()

        shutdown = BoundedShutdown()
        namespace = {
            "time": time,
            "shutdown_event": shutdown,
            "BBO_REFRESH_SEC": 3.0,
            "refresh_bbo_state": lambda: calls.append(time.monotonic()),
            "logger": SimpleNamespace(error=lambda *_args, **_kwargs: None),
        }
        compile_function("bbo_refresh_loop", namespace)
        namespace["bbo_refresh_loop"]()
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(shutdown.waits), 3)
        self.assertTrue(all(0.05 <= delay <= 3.0 for delay in shutdown.waits))

    def test_entry_freshness_contract_is_not_weakened(self):
        readiness = ast.get_source_segment(
            SOURCE, FUNCTIONS["_fresh_rest_entry_quote_ready"]
        )
        pending = ast.get_source_segment(SOURCE, FUNCTIONS["process_pending_orders"])
        self.assertIn("quote_age <= REST_ENTRY_FRESH_SEC", readiness)
        self.assertIn("can_progress_new_entry()", pending)
        self.assertIn("allow_submit=False", pending)


if __name__ == "__main__":
    unittest.main()
