"""delta_change fed to the AI prompt is the per-trade signed volume, never a constant 0."""
import ast
import logging
import time
from collections import deque
from pathlib import Path

BOT = Path(__file__).with_name("bot.py")
TREE = ast.parse(BOT.read_text(encoding="utf-8"))


def _ns():
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "update_orderflow"]
    assert len(nodes) == 1
    ns = {
        "time": time,
        "logger": logging.getLogger("test"),
        "orderflow": {"buy_volume": 0.0, "sell_volume": 0.0, "delta": 0.0, "imbalance": 0.0,
                      "last_update": 0.0, "prev_delta": 0.0},
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def _tick(ns, buffer, side, size):
    # Same arithmetic as _process_ws_trade_tick / _seed_ws_trade_buffers.
    ns["update_orderflow"]({"S": side, "v": size})
    buffer.append(ns["orderflow"]["delta"] - ns["orderflow"].get("prev_delta", 0))


def test_delta_change_is_the_signed_size_of_each_trade():
    ns, buf = _ns(), deque(maxlen=200)
    _tick(ns, buf, "Buy", 0.5)
    _tick(ns, buf, "Sell", 0.2)
    _tick(ns, buf, "Buy", 0.1)
    assert [round(x, 9) for x in buf] == [0.5, -0.2, 0.1]
    assert round(ns["orderflow"]["delta"], 9) == 0.4


def test_unknown_side_leaves_delta_change_zero_without_pinning_later_trades():
    ns, buf = _ns(), deque(maxlen=200)
    _tick(ns, buf, "", 1.0)
    _tick(ns, buf, "Sell", 0.3)
    assert [round(x, 9) for x in buf] == [0.0, -0.3]
