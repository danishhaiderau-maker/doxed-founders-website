"""Tile live control: one always-clickable Live button, real state, fresh overlay."""
from pathlib import Path

SRC = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_single_live_button_replaces_make_eligible():
    assert "Make eligible" not in SRC
    assert "Turn live ON" not in SRC
    assert "Live: ' + (tileLiveRequested ? 'ON' : 'OFF')" in SRC
    assert "'Paper: ON' : 'Paper: OFF'" in SRC


def test_live_button_is_never_disabled():
    block = SRC[SRC.index("const tileLiveRequested"):SRC.index("const liveSwitchBlock")]
    assert "disabled" not in block


def test_held_reasons_are_plain_words():
    assert "STOP_COVERAGE_UNVERIFIED: 'waiting for first confirmed exchange stop'" in SRC
    assert "REDUCE_ONLY_UNSUPPORTED: 'waiting for first confirmed exchange stop'" in SRC
    assert "'account not armed'" in SRC and "'live copy output off'" in SRC


def test_active_overlay_refreshes_live_controls():
    loop = SRC[SRC.index("def _api_state_cache_refresher_loop"):SRC.index("def _relay_state_cache_refresher_loop")]
    assert 'snap["bitfinex_live_switch"] = _bitfinex_live_switch_snapshot()' in loop
    assert 'snap["bitfinex_master"] = _bitfinex_master_state()' in loop
    assert "_live_copy_promote_requested_tiles()" in loop


def test_live_copy_entry_uses_executable_policy():
    assert 'LIVE_COPY_ENTRY_POLICY = "fly_tile_exact_limit_v1"' in SRC
    assert 'payload["tile_entry_policy"]' in SRC


def test_monitor_force_paper_reflects_live_copy_lock():
    ctx = SRC[SRC.index("def _live_copy_monitor_context"):]
    ctx = ctx[:ctx.index("\ndef ")]
    assert '"force_paper_mode": _live_copy_paper_lock_active()' in ctx
