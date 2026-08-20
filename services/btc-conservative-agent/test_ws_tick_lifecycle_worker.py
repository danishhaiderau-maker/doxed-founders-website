import ast
import threading
import time
import unittest
from pathlib import Path
from queue import Empty, Full, Queue
from types import SimpleNamespace


SOURCE_PATH = Path(__file__).with_name("bot.py")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def function_source(name):
    return ast.get_source_segment(SOURCE, FUNCTIONS[name])


def compile_function(name, namespace):
    module = ast.Module(body=[FUNCTIONS[name]], type_ignores=[])
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)


class WsTickLifecycleIsolationTest(unittest.TestCase):
    def test_websocket_trade_handler_never_runs_slow_lifecycle_inline(self):
        src = function_source("_process_ws_trade_tick")
        self.assertIn("_enqueue_ws_tick_lifecycle(price, tick_now)", src)
        self.assertNotIn("process_pending_orders()", src)
        self.assertNotIn("_tick_driven_position_exits(price)", src)

    def test_latest_tick_queue_is_bounded_and_coalesces(self):
        q = Queue(maxsize=1)
        ns = {
            "time": time,
            "Full": Full,
            "Empty": Empty,
            "ws_tick_lifecycle_queue": q,
        }
        compile_function("_enqueue_ws_tick_lifecycle", ns)
        self.assertTrue(ns["_enqueue_ws_tick_lifecycle"](100.0, 1.0))
        self.assertTrue(ns["_enqueue_ws_tick_lifecycle"](101.0, 2.0))
        self.assertEqual(q.qsize(), 1)
        self.assertEqual(q.get_nowait(), (2.0, 101.0))

    def test_tick_exit_uses_shared_position_evaluation_lock(self):
        calls = []
        lock = threading.Lock()
        pos = {"status": "OPEN", "dir": "LONG"}
        ns = {
            "time": time,
            "position_evaluation_lock": lock,
            "trade_lock": threading.RLock(),
            "open_positions": [pos],
            "get_mark_price": lambda direction, fallback: fallback,
            "_apply_position_exits": lambda p, mark, now: calls.append((p, mark)),
        }
        compile_function("_tick_driven_position_exits", ns)
        lock.acquire()
        try:
            self.assertFalse(ns["_tick_driven_position_exits"](123.0))
        finally:
            lock.release()
        self.assertEqual(calls, [])
        self.assertTrue(ns["_tick_driven_position_exits"](124.0))
        self.assertEqual(calls, [(pos, 124.0)])

    def test_worker_discards_stale_tick_and_processes_fresh_tick_once(self):
        q = Queue(maxsize=1)
        shutdown = threading.Event()
        calls = []
        ns = {
            "time": time,
            "Empty": Empty,
            "shutdown_event": shutdown,
            "ws_tick_lifecycle_queue": q,
            "WS_TICK_LIFECYCLE_MAX_AGE_SEC": 5.0,
            "_tick_driven_position_exits": lambda price: calls.append(price),
            "logger": SimpleNamespace(error=lambda *a, **k: None),
        }
        compile_function("ws_tick_lifecycle_worker", ns)
        q.put((time.time() - 10.0, 99.0))
        worker = threading.Thread(target=ns["ws_tick_lifecycle_worker"], daemon=True)
        worker.start()
        deadline = time.time() + 1.0
        while q.unfinished_tasks and time.time() < deadline:
            time.sleep(0.01)
        q.put((time.time(), 100.0))
        deadline = time.time() + 1.0
        while calls != [100.0] and time.time() < deadline:
            time.sleep(0.01)
        shutdown.set()
        worker.join(timeout=1.0)
        self.assertEqual(calls, [100.0])

    def test_main_starts_exactly_one_tick_lifecycle_worker(self):
        main = function_source("main")
        needle = "threading.Thread(target=safe_thread(ws_tick_lifecycle_worker)"
        self.assertEqual(main.count(needle), 1)


if __name__ == "__main__":
    unittest.main()
