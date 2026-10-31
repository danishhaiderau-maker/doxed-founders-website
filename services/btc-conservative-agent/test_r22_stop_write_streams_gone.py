"""R22 AST grep: five STOP-WRITE streams have no durable append sites (R0c eb14e8711d31).

Streams: chase_offset_touch_grid, order_multiverse_entry_grid, post_exit_replay,
signal_replay, xvp_shadow_signals (xvp unconditional per HM 46a97b4debee).

Exempt (must still exist as writers elsewhere — not asserted gone):
cross_venue_tape_1m, venue_bbo_1m, bfx_trades, deriv.
"""
from __future__ import annotations

import ast
from pathlib import Path

BOT = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(BOT)

STOP_WRITE_BASENAMES = (
    "chase_offset_touch_grid.jsonl",
    "order_multiverse_entry_grid.jsonl",
    "post_exit_replay.jsonl",
    "signal_replay.jsonl",
    "xvp_shadow_signals.jsonl",
)
STOP_WRITE_CONSTANTS = (
    "CHASE_OFFSET_TOUCH_GRID_FILE",
    "ORDER_MULTIVERSE_ENTRY_GRID_FILE",
    "POST_EXIT_REPLAY_FILE",
    "SIGNAL_REPLAY_FILE",
)
EXEMPT_BASENAMES = (
    "cross_venue_tape_1m.jsonl",
    "venue_bbo_1m.jsonl",
    "bfx_trades.jsonl",
    "bfx_deriv_status_1s.jsonl",
)


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_no_safe_append_targets_stop_write_streams():
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in {"_safe_append_jsonl", "append_jsonl", "_append_jsonl", "safe_append_jsonl"}:
            continue
        first = node.args[0]
        names = _names(first)
        for const in STOP_WRITE_CONSTANTS:
            assert const not in names, ast.unparse(node)
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            for base in STOP_WRITE_BASENAMES:
                assert base not in first.value, ast.unparse(node)


def test_xvp_evaluator_shadow_file_is_none():
    from cross_venue_premium import PremiumEvaluator
    from regime_adaptive_binding import GsPremiumEvaluator

    assert PremiumEvaluator.SHADOW_FILE is None
    assert GsPremiumEvaluator.SHADOW_FILE is None


def test_xvp_module_shadow_file_is_none():
    """HM #956: module SHADOW_FILE must stay None so lead_lag never opens xvp stream."""
    import cross_venue_premium as xvp

    assert xvp.SHADOW_FILE is None


def test_xvp_basename_kept_for_wipe_and_monitor_not_serialized_append():
    src = Path(__file__).with_name("cross_venue_premium.py").read_text(encoding="utf-8")
    assert "SHADOW_FILE = None" in src
    assert 'SHADOW_FILE = "xvp_shadow_signals.jsonl"' not in src
    # Historical basename stays on wipe / monitor / rotation tables (no bot.py append).
    assert '"xvp_shadow_signals.jsonl"' in Path(__file__).with_name(
        "full_reset_no_archive.py"
    ).read_text(encoding="utf-8")
    assert '"xvp_shadow_signals.jsonl"' in Path(__file__).with_name(
        "monitor_streams.py"
    ).read_text(encoding="utf-8")
    serialized = BOT[BOT.index("_JSONL_SERIALIZED_APPEND_LITERALS = ("):BOT.index(
        "def _declared_serialized_jsonl_targets"
    )]
    assert '"xvp_shadow_signals.jsonl"' not in serialized
    assert '"CHASE_OFFSET_TOUCH_GRID_FILE"' not in BOT[
        BOT.index("_JSONL_SERIALIZED_APPEND_CONSTANTS = ("):BOT.index(
            "_JSONL_SERIALIZED_APPEND_LITERALS = ("
        )
    ]


def test_exempt_streams_still_named_in_monitor_catalog():
    import monitor_streams as ms

    # Exempt writers stay; catalog still lists them (or strategist aliases).
    assert ms.STREAMS["cross_venue_tape"] == "cross_venue_tape_1m.jsonl"
    assert ms.STREAMS["venue_bbo_lighter_paradex"] == "venue_bbo_1m.jsonl"
    assert ms.STREAMS["bfx_trades"] == "bfx_trades.jsonl"
    assert ms.STREAMS["bfx_deriv_status_1s"] == "bfx_deriv_status_1s.jsonl"
    for base in EXEMPT_BASENAMES:
        assert base not in STOP_WRITE_BASENAMES


def test_order_multiverse_entry_grid_status_stays_disabled_r17():
    assert '"order_multiverse_entry_grid_file": "disabled: R17"' in BOT


def test_r22_touch_grid_poll_arm_recovery_symbols_gone():
    """RK 7524ab402ae2 + HM #956: touch-grid book/arm rows + recovery read gone; shadow kept."""
    assert "touch_grid_books_inflight" not in BOT
    assert "_chase_grid_recovery_candidates" not in BOT
    assert "_touch_grid_book" not in BOT
    assert "arm_touch_grid_rows" not in BOT
    assert "poll_grid_state" not in BOT
    assert "new_grid_state" not in BOT
    assert "recover_compressed_shadow_states" not in BOT
    # Shadow-chase half kept.
    assert "def _poll_chase_offset_touch_grid(" in BOT
    assert "poll_compressed_shadow_chase(" in BOT
    assert "def _arm_shared_compressed_shadow_chase(" in BOT
    poll = next(
        n for n in TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == "_poll_chase_offset_touch_grid"
    )
    poll_src = ast.get_source_segment(BOT, poll) or ""
    assert "poll_compressed_shadow_chase(" in poll_src
