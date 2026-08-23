import ast
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
