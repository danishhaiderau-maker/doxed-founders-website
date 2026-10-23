"""R17 (RK 18:10, 23 Oct): signal_replay.jsonl writer is stopped.

Its ticks carry ticker last/best_bid/best_ask with no book walk and no
recorded book age, so the PRIME RULE cannot verify a row.
"""
import ast
import pathlib

BOT = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(BOT)


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_no_call_anywhere_appends_or_opens_signal_replay_for_write():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        first = node.args[0]
        if name in {"_safe_append_jsonl", "append_jsonl", "_append_jsonl", "safe_append_jsonl"}:
            assert "SIGNAL_REPLAY_FILE" not in _names(first), ast.unparse(node)
            assert not (isinstance(first, ast.Constant) and "signal_replay" in str(first.value)), ast.unparse(node)
        if name == "open" and len(node.args) > 1:
            mode = node.args[1]
            if isinstance(mode, ast.Constant) and any(c in str(mode.value) for c in "aw"):
                assert "SIGNAL_REPLAY_FILE" not in _names(first), ast.unparse(node)


def test_dump_replay_builds_no_replay_row_and_keeps_write_counter_decoupled():
    body = BOT[BOT.index("def dump_replay("):BOT.index("def load_policy(")]
    assert '"schema": "signal_replay_v4"' not in body
    assert "SIGNAL_REPLAY_FILE" not in body
    # write_counter counts dumps, not file appends (HM E1.1).
    assert "write_counter += 1" in body


def test_signal_replay_shard_glob_is_gone():
    assert "glob(SIGNAL_REPLAY_FILE" not in BOT
    assert 'SIGNAL_REPLAY_FILE + "*"' not in BOT


def test_signal_replay_not_serialized_or_startup_validated():
    serialized = BOT[BOT.index("_JSONL_SERIALIZED_APPEND_CONSTANTS = ("):BOT.index("_JSONL_SERIALIZED_APPEND_LITERALS = (")]
    assert '"SIGNAL_REPLAY_FILE"' not in serialized
    assert '"SIGNAL_REPLAY_STARTUP"' not in BOT


def test_reset_lists_still_name_signal_replay_so_old_copies_are_found():
    assert 'SIGNAL_REPLAY_FILE = "signal_replay.jsonl"' in BOT
    assert '"signal_replay.jsonl"' in BOT
