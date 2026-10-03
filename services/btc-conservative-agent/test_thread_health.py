"""Per-thread heartbeats, restart counters and queue drop counters."""
import ast
import queue
import re
import threading
from pathlib import Path

import thread_health as th

BOT = Path(__file__).with_name("bot.py")
SOURCE = BOT.read_text(encoding="utf-8")


def clocked():
    clock = {"now": 1000.0}
    return clock, th.ThreadHealthRegistry(clock=lambda: clock["now"])


def test_stale_after_three_intervals_or_floor():
    clock, reg = clocked()
    reg.register("fast", 2.0)
    reg.register("floored", 1.0, min_stale_sec=60.0)
    for name in ("fast", "floored"):
        reg.bind(name, thread=threading.current_thread())
        reg.beat(name)
    clock["now"] += 6.5
    snap = reg.snapshot()
    assert snap["fast"]["stale"] is True and snap["fast"]["stale_after_sec"] == 6.0
    assert snap["floored"]["stale"] is False and snap["floored"]["stale_after_sec"] == 60.0
    assert snap["fast"]["iterations"] == 1 and snap["fast"]["last_tick_age_sec"] == 6.5
    summary = reg.summary()
    assert summary["ok"] is False and summary["stale"] == ["fast"]


def test_not_started_loop_is_not_stale_and_dead_thread_is():
    clock, reg = clocked()
    reg.register("never", 1.0)
    reg.register("dead", 10.0)
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()
    reg.bind("dead", thread=finished)
    snap = reg.snapshot()
    assert snap["never"]["stale"] is False and snap["never"]["started"] is False
    assert snap["dead"]["alive"] is False and snap["dead"]["stale"] is True
    summary = reg.summary()
    assert summary["dead"] == ["dead"] and summary["not_started"] == ["never"]


def test_errors_and_restarts_are_counted():
    clock, reg = clocked()
    reg.register("loop", 1.0)
    reg.error("loop", ValueError("bad tick"))
    reg.restart("loop", RuntimeError("crash"))
    reg.restart("other", "RETURNED")
    clock["now"] += 5
    snap = reg.snapshot()
    assert snap["loop"]["error_count"] == 1
    assert snap["loop"]["last_error"] == "ValueError: bad tick"
    assert snap["loop"]["last_error_age_sec"] == 5.0
    assert snap["loop"]["restarts"] == 1
    assert snap["other"]["monitored"] is False
    assert reg.summary()["restarts_total"] == 2


def test_instrumentation_never_raises():
    reg = th.ThreadHealthRegistry(clock=lambda: "not-a-number")
    reg.beat("x")
    reg.error("x", object())
    reg.restart("x", None)
    th.FailureCounters(clock=lambda: None).failure("k", "e")
    th.RateLimitCounters(clock=lambda: None).hit("v")
    th.QueueCounters(clock=lambda: None).dropped("q")


def test_queue_counters_report_qsize_and_drops():
    clock = {"now": 50.0}
    counters = th.QueueCounters(clock=lambda: clock["now"])
    q = queue.Queue(maxsize=4)
    q.put(1)
    counters.dropped("event_queue", 3)
    clock["now"] += 2
    snap = counters.snapshot({"event_queue": q, "signal_queue": queue.Queue()})
    assert snap["event_queue"] == {"qsize": 1, "maxsize": 4, "dropped": 3, "last_drop_age_sec": 2.0}
    assert snap["signal_queue"]["dropped"] == 0 and snap["signal_queue"]["maxsize"] is None


def test_rate_limit_detection_and_counts():
    class Err(Exception):
        http_status = 429

    assert th.is_rate_limit_error(Err())
    assert th.is_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests"))
    assert not th.is_rate_limit_error(RuntimeError("HTTP 500"))
    counters = th.RateLimitCounters(clock=lambda: 10.0)
    counters.hit("bitfinex_rest", "ticker")
    counters.hit("bitfinex_rest", "ticker")
    snap = counters.snapshot()
    assert snap["bitfinex_rest"]["hits_429"] == 2 and snap["bitfinex_rest"]["clients"] == {"ticker": 2}


def monitored_names():
    tree = ast.parse(SOURCE)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "THREAD_HEALTH_MONITORED" for t in node.targets
        ):
            return [key.value for key in node.value.keys]
    raise AssertionError("THREAD_HEALTH_MONITORED missing")


def test_every_monitored_loop_beats_and_is_bound():
    names = monitored_names()
    assert len(names) == 8
    for name in names:
        assert f'_THREAD_HEALTH.beat("{name}")' in SOURCE, name
        bound = f'_THREAD_HEALTH.bind("{name}")' in SOURCE or re.search(
            rf"target=safe_thread\({name}\)", SOURCE
        )
        assert bound, name


def test_safe_thread_counts_crash_restarts():
    tree = ast.parse(SOURCE)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "safe_thread")
    registry = th.ThreadHealthRegistry()
    shutdown = threading.Event()
    paused = []
    ns = {
        "_THREAD_HEALTH": registry, "shutdown_event": shutdown,
        "logger": type("L", (), {"exception": staticmethod(lambda *a, **k: None)}),
        "dump_system_state": lambda: None, "set_execution_paused": paused.append,
        "time": type("T", (), {"sleep": staticmethod(lambda s: None)}),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), ns)
    calls = []

    def flaky_loop():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        shutdown.set()

    ns["safe_thread"](flaky_loop)()
    row = registry.snapshot()["flaky_loop"]
    assert row["restarts"] == 1 and "boom" in row["last_restart_reason"]
    assert paused == ["THREAD_CRASH"]


def test_status_and_ready_expose_thread_health():
    assert '"threads": lambda: _THREAD_HEALTH.snapshot(now)' in SOURCE
    assert '"thread_health": thread_summary' in SOURCE
    for name in ("signal_queue", "event_queue", "ws_tick_lifecycle_queue"):
        assert f'"{name}": {name}' in SOURCE
