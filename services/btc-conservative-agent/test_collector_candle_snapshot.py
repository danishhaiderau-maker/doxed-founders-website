import ast
import copy
import threading
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")


def _load_helper(cache, latest):
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_collector_cached_candles_1m"
    )
    namespace = {"_mtf_cache": cache, "latest_candles": latest}
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"),
        namespace,
    )
    return namespace["_collector_cached_candles_1m"]


def test_collector_uses_cached_candles_without_network_dependency():
    cached = [[1], [2], [3]]
    helper = _load_helper({"1m": {"candles": cached}}, [[9]])

    assert helper(2) == [[2], [3]]
    assert helper(2) is not cached


def test_collector_falls_back_to_independent_ohlcv_worker_snapshot():
    helper = _load_helper({"1m": {"candles": []}}, [[7], [8], [9]])

    assert helper(2) == [[8], [9]]


def test_collector_sync_paths_do_not_call_network_candle_fetch():
    tree = ast.parse(BOT_SOURCE)
    functions = {
        item.name: item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
    }
    for name in ("_sync_order_multiverse", "persist_rejected_opportunity"):
        calls = {
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "fetch_mtf_candles" not in calls
        assert "_collector_cached_candles_1m" in calls


def _load_cycle_recorder(cached_rows, *, now=1000.0, compute_hook=None):
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_record_cycle_3m_universe"
    )

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    writes = []
    cache_calls = []

    class Clock:
        @staticmethod
        def time():
            return float(now)

    namespace = {
        "cycle_bucket_3m": lambda: 123,
        "_cycle_3m_written_buckets": {},
        "_cycle_3m_inflight_buckets": set(),
        "_cycle_3m_bucket_lock": threading.Lock(),
        "_cycle_3m_flow_and_sr": lambda: {},
        "_collector_cached_candles_1m": (
            lambda limit=200: cache_calls.append(limit) or list(cached_rows)
        ),
        "compute_3m_universe_snapshot": compute_hook or (
            lambda rows, **_kwargs: {
                "bar_count_1m": len(rows),
                "line": "fixture",
                "would_block_short": False,
                "would_block_reason": None,
            }
        ),
        "candle_ts_sec": lambda row: (
            float(row[0]) / 1000.0 if float(row[0]) > 1e12 else float(row[0])
        ) if row else None,
        "CANDLE_STALE_SEC": 180.0,
        "_safe_append_jsonl": lambda _path, row, **_kwargs: writes.append(copy.deepcopy(row)),
        "CYCLE_3M_UNIVERSE_FILE": "fixture.jsonl",
        "state_lock": threading.RLock(),
        "state": {},
        "copy": copy,
        "time": Clock,
        "logger": Logger(),
        "CYCLE_3M_UNIVERSE_TAG": "cycle-test",
        "PATH_REPLAY_POLICY_TAG": "path-test",
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"),
        namespace,
    )
    return namespace["_record_cycle_3m_universe"], writes, cache_calls


def test_cycle_recorder_hot_path_uses_cache_and_never_references_network():
    tree = ast.parse(BOT_SOURCE)
    recorder = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_record_cycle_3m_universe"
    )
    calls = {
        node.func.id
        for node in ast.walk(recorder)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "fetch_mtf_candles" not in calls
    assert "fetch_bitfinex_ohlcv" not in calls
    assert "_collector_cached_candles_1m" in calls

    fn, writes, cache_calls = _load_cycle_recorder([[940], [1000]])
    result = fn(outcome="TAKEN", trade_id="trade-1")

    assert cache_calls == [200]
    assert writes == [result]
    assert result["evidence_status"] == "AVAILABLE"
    assert result["calculation_status"] == "COMPUTED"
    assert result["source_row_count"] == 2
    assert result["source_last_candle_ts"] == 1000.0
    assert result["source_age_sec"] == 0.0
    assert result["source_unavailable_reason"] is None


def test_cycle_recorder_missing_cache_is_truthful_unknown_not_no_fill():
    fn, writes, cache_calls = _load_cycle_recorder([])
    result = fn(outcome="TAKEN", trade_id="trade-missing")

    assert cache_calls == [200]
    assert writes == [result]
    assert result["evidence_status"] == "SOURCE_UNAVAILABLE"
    assert result["calculation_status"] == "UNKNOWN"
    assert result["source_row_count"] == 0
    assert result["source_unavailable_reason"] == "CACHED_1M_SOURCE_UNAVAILABLE"
    assert result["would_block_short"] is None
    assert result["would_block_reason"] == "SOURCE_UNAVAILABLE"
    assert "NO_FILL" not in result.values()


def test_cycle_recorder_millisecond_timestamp_freshness_is_safe():
    fn, _, _ = _load_cycle_recorder([[999_000_000_000_000]], now=999_000_000_060.0)
    result = fn(outcome="TAKEN", trade_id="trade-ms")

    assert result["source_last_candle_ts"] == 999_000_000_000.0
    assert result["source_age_sec"] == 60.0
    assert result["evidence_status"] == "AVAILABLE"
    assert result["calculation_status"] == "COMPUTED"


def test_cycle_recorder_nonempty_stale_cache_is_unknown():
    fn, _, _ = _load_cycle_recorder([[700]], now=1000.0)
    result = fn(outcome="TAKEN", trade_id="trade-stale")

    assert result["source_age_sec"] == 300.0
    assert result["evidence_status"] == "SOURCE_STALE"
    assert result["calculation_status"] == "UNKNOWN"
    assert result["source_unavailable_reason"] == "CACHED_1M_SOURCE_STALE"
    assert result["would_block_short"] is None
    assert result["would_block_reason"] == "SOURCE_STALE"


def test_cycle_bucket_duplicate_guard_is_thread_safe_and_narrow():
    entered = threading.Event()
    release = threading.Event()

    def slow_compute(rows, **_kwargs):
        entered.set()
        assert release.wait(timeout=2.0)
        return {
            "bar_count_1m": len(rows),
            "line": "fixture",
            "would_block_short": False,
            "would_block_reason": None,
        }

    fn, writes, _ = _load_cycle_recorder([[1000]], compute_hook=slow_compute)
    results = []
    first = threading.Thread(target=lambda: results.append(fn(outcome="TAKEN")))
    second = threading.Thread(target=lambda: results.append(fn(outcome="TAKEN")))

    first.start()
    assert entered.wait(timeout=1.0)
    second.start()
    second.join(timeout=0.5)
    assert not second.is_alive(), "duplicate caller waited behind build work"
    release.set()
    first.join(timeout=1.0)

    assert not first.is_alive()
    assert len(writes) == 1
    assert sum(item is None for item in results) == 1
