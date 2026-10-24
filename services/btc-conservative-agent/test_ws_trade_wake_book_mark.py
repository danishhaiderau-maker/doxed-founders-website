"""RK C (24 Oct): the WS trade print only wakes the exit pass. Source WS_TRADE_WAKE_BOOK_MARK, mark BOOK10L.
The trade-print price is never stored or used as a mark, trigger or fill; a book older than 2 s gives no price."""
import ast
import math
import subprocess
import time
from pathlib import Path

import pytest

import bot
import paper_fill

HERE = Path(__file__).resolve().parent
PRINT_PX = 12345.0  # a trade print far from the book: if it leaks anywhere the asserts below see it


def _book(monkeypatch, px, age_s):
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: {
        "s": float(math.floor(float(t)) - age_s), "b": [[float(px), 5.0]], "a": [[float(px) + 1.0, 5.0]]})


@pytest.fixture
def calls(monkeypatch):
    got = []
    monkeypatch.setattr(bot, "open_positions", [{"trade_id": "t", "dir": "LONG", "status": "OPEN", "entry": 60000.0}])
    monkeypatch.setattr(bot, "_evaluate_position_exit",
                        lambda pos, mark, now, **kw: got.append((mark, kw.get("exit_source"))) or False)
    return got


def test_trade_print_wakes_with_book_mark_and_new_source(monkeypatch, calls):
    _book(monkeypatch, 60000.0, 1)
    assert bot._tick_driven_position_exits(PRINT_PX) is True
    assert calls == [(60000.0, "WS_TRADE_WAKE_BOOK_MARK")]
    assert PRINT_PX not in [c[0] for c in calls]


def test_stale_book_over_2s_gives_no_price(monkeypatch, calls):
    _book(monkeypatch, 60000.0, 4)
    bot._tick_driven_position_exits(PRINT_PX)
    assert calls == [(0.0, "WS_TRADE_WAKE_BOOK_MARK")]  # 0.0 = no price-level exit; never the print
    assert bot.book_exit_mark("LONG")["px"] is None


def _fn_src(text, name):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(name)


def test_trigger_paths_stamp_book_mark_source_and_never_ws_tick():
    src = (HERE / "bot.py").read_text()
    assert 'exit_source="WS_TICK"' not in src
    fam = _fn_src(src, "_apply_family_tile_exit")
    assert 'pos["exit_mark_source"] = EXIT_MARK_SOURCE_BOOK' in fam
    assert bot.EXIT_MARK_SOURCE_BOOK == "BOOK10L"
    assert '"exit_mark_source": pos.get("exit_mark_source")' in src
    # The print arg of the wake pass is only a liveness gate: never stored on the position or passed on.
    wake = _fn_src(src, "_tick_driven_position_exits")
    body = wake.split('"""', 2)[-1]
    uses = [l for l in body.splitlines() if "price" in l and "get_mark_price" not in l and "#" not in l.split("price")[0]]
    assert uses == ["    if price is None or price <= 0:"], uses


def test_stop_fill_rule_unchanged():
    base = subprocess.run(["git", "show", "8d9de158:services/btc-conservative-agent/bot.py"], cwd=HERE,
                          capture_output=True, text=True)
    assert base.returncode == 0
    for name in ("_realism_level_stops", "book_exit_mark", "get_mark_price"):
        assert _fn_src(base.stdout, name) == _fn_src((HERE / "bot.py").read_text(), name), name
