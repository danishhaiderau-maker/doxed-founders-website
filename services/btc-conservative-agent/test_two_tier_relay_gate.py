"""Two-tier Bitfinex live-control wiring (master + per-tile) is fail-closed.

Verifies the second tier: the per-tile "Bitfinex Live Orders" switch is
consulted by the relay delivery path *after* the master/owner/pre-arming guard,
so a paper intent can only become a real Bitfinex order when BOTH hold:
  1. master armed (relay guard ``filter_deliverable``), and
  2. the lane's switch is ON AND the intent was created at/after the lane arm.

In this revision every active tile is paper-only / relay-ineligible, so the
switch can only be turned ON by writing the switch state directly; that keeps
these tests pure and independent of the frozen registry.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from bitfinex_live_switch import (
    BitfinexLiveSwitch,
    DENY_SWITCH_NOT_REQUESTED,
    DENY_TILE_PRE_ARMING,
)

BOT = Path(__file__).with_name("bot.py")


class _Log:
    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def load_function(name: str, ns: dict) -> dict:
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(nodes) == 1, f"expected exactly one {name} in bot.py, got {len(nodes)}"
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def filter_ns(sw, denials):
    return load_function("_filter_relay_rows_by_live_switch", {
        "time": time,
        "_get_bfx_live_switch": lambda: sw,
        "_record_bitfinex_delivery_denial": lambda lane, reason, event_id=None: denials.append(
            (lane, reason, event_id)
        ),
        "_relay_push_state": {},
        "logger": _Log(),
    })


def arm_switch(sw, lane, allow_ts):
    row = sw._row(lane)
    row["bitfinex_live_orders"] = True
    row["last_allow_ts"] = allow_ts
    return sw


LANE = "FAMILY_COMMITTED_FADE_TAKER_90"


def _row(event_id, lane, created):
    return {"event_id": event_id, "created_at_unix": created,
            "payload": {"research_lane": lane}}


def test_master_disarmed_is_pass_through(tmp_path):
    """Tier 1 OFF -> the per-tile gate is a no-op (guard still holds PRE_ARMING)."""
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    denials = []
    ns = filter_ns(sw, denials)
    rows = [_row("e1", LANE, 100.0)]
    assert ns["_filter_relay_rows_by_live_switch"](rows, armed=False, now=200.0) == rows
    assert denials == []


def test_armed_but_tile_off_withholds(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    denials = []
    ns = filter_ns(sw, denials)
    rows = [_row("e1", LANE, 100.0)]
    out = ns["_filter_relay_rows_by_live_switch"](rows, armed=True, now=200.0)
    assert out == []
    assert ns["_relay_push_state"]["live_switch_withheld_total"] == 1
    assert denials and denials[0][1] == DENY_SWITCH_NOT_REQUESTED


def test_armed_tile_on_and_created_after_arm_delivers(tmp_path):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials)
    rows = [_row("e1", LANE, 150.0)]
    assert ns["_filter_relay_rows_by_live_switch"](rows, armed=True, now=200.0) == rows
    assert denials == []


def test_armed_tile_on_but_created_before_arm_withholds(tmp_path):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials)
    rows = [_row("e1", LANE, 50.0)]
    out = ns["_filter_relay_rows_by_live_switch"](rows, armed=True, now=200.0)
    assert out == []
    assert denials and denials[0][1] == DENY_TILE_PRE_ARMING


def test_armed_unknown_lane_fails_closed(tmp_path):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials)
    rows = [_row("e1", "NOPE", 150.0)]
    assert ns["_filter_relay_rows_by_live_switch"](rows, armed=True, now=200.0) == []
    assert denials and denials[0][1] == DENY_SWITCH_NOT_REQUESTED


def test_non_dict_rows_pass_through(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    denials = []
    ns = filter_ns(sw, denials)
    rows = ["not-a-dict"]
    assert ns["_filter_relay_rows_by_live_switch"](rows, armed=True, now=200.0) == rows


# -- source wiring contract (no import of the whole bot.py) -------------------


def test_drain_wires_per_tile_filter_after_master_guard():
    src = BOT.read_text(encoding="utf-8")
    assert "_filter_relay_rows_by_live_switch(rows, armed=armed)" in src
    guard_i = src.index("filter_deliverable(")
    filter_i = src.index("_filter_relay_rows_by_live_switch(rows, armed=armed)")
    assert guard_i < filter_i, "per-tile filter must run AFTER the master/owner guard"


def test_disarm_resets_every_per_tile_switch():
    src = BOT.read_text(encoding="utf-8")
    assert "_get_bfx_live_switch().reset_all_off(reason=\"MASTER_DISARMED\")" in src


def test_toggle_endpoint_is_admin_gated_and_never_arms():
    src = BOT.read_text(encoding="utf-8")
    assert "@app.route('/api/bitfinex/tiles/<lane>/live-orders', methods=['POST'])" in src
    assert "_admin_authed_strict()" in src
    # The endpoint must route arming authority to /api/live_arm, not call it itself.
    assert "This endpoint never arms the account" in src


def test_snapshot_publishes_master_and_switch():
    src = BOT.read_text(encoding="utf-8")
    assert 'snapshot["bitfinex_master"] = _bitfinex_master_state()' in src
    assert 'snapshot["bitfinex_live_switch"] = _bitfinex_live_switch_snapshot()' in src


def test_master_state_derives_and_logic():
    src = BOT.read_text(encoding="utf-8")
    assert '"master_on": live_armed and bfx_enabled' in src


def test_master_defaults_off_fail_closed():
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "state":
                    values = {}
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            values[k.value] = v
                    for flag in ("live_armed", "bitfinex_live_enabled"):
                        assert isinstance(values[flag], ast.Constant) and values[flag].value is False, flag
                    return
    raise AssertionError("initial `state` dict with default OFF flags not found")
