"""Boot restore of post-exit replay buffers must not materialise expired ticks.

A 299 MB rotated sidecar OOM-killed boot on a 2 GB machine because every tick
row was parsed and held before expired buffers were discarded. The loader is
extracted from bot.py's AST (bot.py cannot be imported in unit tests).
"""

from __future__ import annotations

import ast
import copy
import json
import threading
import time
from pathlib import Path
from typing import Dict

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_TREE = ast.parse(BOT_PATH.read_text(encoding="utf-8"))


class _CountingJson:
    def __init__(self):
        self.parsed = []

    def loads(self, line):
        row = json.loads(line)
        self.parsed.append(row)
        return row


def _loader(sidecar: Path, counting_json: _CountingJson, buffers: dict):
    nodes = [
        node for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "_load_post_exit_replays"
    ]
    assert len(nodes) == 1
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    def buf_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def buf_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    class _Logger:
        def info(self, *_a, **_k):
            pass

        error = warning = info

    namespace = {
        "Path": Path,
        "Dict": Dict,
        "json": counting_json,
        "time": time,
        "copy": copy,
        "logger": _Logger(),
        "replay_lock": threading.Lock(),
        "replay_buffers": buffers,
        "POST_EXIT_REPLAY_FILE": str(sidecar),
        "FIXED_MARGIN_USDT": 0.25,
        "_buf_float": buf_float,
        "_buf_int": buf_int,
        "_replay_leverage_default": lambda: 100,
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_load_post_exit_replays"]


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _header(tid, deadline, start):
    return {
        "kind": "post_exit_header", "trade_id": tid, "ts": start,
        "post_exit_deadline_ts": deadline, "post_exit_started_ts": start,
        "entry_price": 83451.0, "virtual_entry": 83451.0, "direction": "SHORT",
        "leverage": 100, "start_ts": start - 100,
    }


def _tick(tid, ts, price):
    return {"kind": "tick", "trade_id": tid, "ts": ts, "price": price, "phase": "post_exit"}


def test_expired_ticks_are_never_parsed_and_active_buffer_restores(tmp_path):
    now = time.time()
    sidecar = tmp_path / "post_exit_replay.jsonl"
    expired_rows = []
    for n in range(20):
        tid = f"fat-expired{n:04d}"
        expired_rows.append(_header(tid, now - 3600, now - 10800))
        expired_rows.extend(_tick(tid, now - 10000 + i, 80000.0 + i) for i in range(200))
    _write(tmp_path / "post_exit_replay.jsonl.2", expired_rows)
    _write(tmp_path / "post_exit_replay.jsonl.1", [
        _header("fc3-active000001", now + 3600, now - 600),
        _tick("fc3-active000001", now - 500, 84000.0),
    ])
    _write(sidecar, [
        _tick("fc3-active000001", now - 400, 84010.0),
        _tick("fat-expired0001", now - 390, 1.0),
    ])

    counting = _CountingJson()
    buffers: dict = {}
    _loader(sidecar, counting, buffers)()

    assert list(buffers) == ["fc3-active000001"]
    buf = buffers["fc3-active000001"]
    assert [tick["price"] for tick in buf["ticks"]] == [84000.0, 84010.0]
    assert buf["post_exit"] is True and buf["closed"] is False
    parsed_ticks = [row for row in counting.parsed if row.get("kind") == "tick"]
    assert all(row["trade_id"] == "fc3-active000001" for row in parsed_ticks)
    assert len(counting.parsed) <= 21 + 2


def test_all_expired_skips_tick_pass_entirely(tmp_path):
    now = time.time()
    sidecar = tmp_path / "post_exit_replay.jsonl"
    rows = [_header("ftr-old", now - 1, now - 7300)]
    rows.extend(_tick("ftr-old", now - 7000 + i, 1.0) for i in range(500))
    _write(sidecar, rows)

    counting = _CountingJson()
    buffers: dict = {}
    _loader(sidecar, counting, buffers)()

    assert buffers == {}
    assert [row.get("kind") for row in counting.parsed] == ["post_exit_header"]


def test_live_buffer_takes_precedence_over_sidecar(tmp_path):
    now = time.time()
    sidecar = tmp_path / "post_exit_replay.jsonl"
    _write(sidecar, [_header("fhy-live", now + 600, now - 60), _tick("fhy-live", now - 30, 2.0)])
    live = {"fhy-live": {"closed": False, "ticks": [{"price": 9.0}]}}
    _loader(sidecar, _CountingJson(), live)()
    assert live["fhy-live"]["ticks"] == [{"price": 9.0}]
