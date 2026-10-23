"""R17 (23 Oct): post_exit_replay.jsonl is retired (ticker/L1 marks, no depth).

The boot loader that restored post-exit replay buffers from the sidecar is
gone with the writer, so boot can no longer materialise expired ticks from
disk. The old boot-memory tests targeted ``_load_post_exit_replays``; this
file keeps the CI slot and pins the retirement instead. bot.py is checked by
AST (it cannot be imported in unit tests).
"""

from __future__ import annotations

import ast
from pathlib import Path

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SRC = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SRC)


def _function(name):
    return [n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name]


def test_boot_loader_is_retired():
    assert _function("_load_post_exit_replays") == []


def test_no_writer_appends_post_exit_replay():
    for node in ast.walk(BOT_TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
                node.func.id in ("_safe_append_jsonl", "rotate_log") and node.args:
            assert ast.unparse(node.args[0]) != "POST_EXIT_REPLAY_FILE"


def test_legacy_file_is_still_wiped_by_reset():
    # Legacy rotations on disk are removed by the reset wipe lists.
    assert BOT_SRC.count("POST_EXIT_REPLAY_FILE,") >= 2
