"""Two-tier Bitfinex live-copy wiring (Fly output + per-tile) is fail-closed.

Option 1 (2026-10-09): the tier-1 "master" is Fly's Live copy output switch
and the per-tile gate applies at ALL times, requiring a signed approval.

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


SECRET = "gate-test-secret"


class _Output:
    def __init__(self, enabled=False, at=None):
        self.enabled, self.enabled_at_ts = enabled, at

    def snapshot(self):
        return {"enabled": self.enabled, "enabled_at_ts": self.enabled_at_ts}


def filter_ns(sw, denials, output=None, monkeypatch=None):
    import os
    import live_copy_control as lc
    if monkeypatch is not None:
        monkeypatch.setenv("SHOWCASE_WEBHOOK_SECRET", SECRET)
    out = output or _Output()
    return load_function("_filter_relay_rows_by_live_switch", {
        "time": time, "os": os, "_live_copy": lc,
        "_get_bfx_live_switch": lambda: sw,
        "_get_live_copy_output": lambda: out,
        "_live_copy_withheld_audited": set(),
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


def _approval(event="ORDER_PLACED", trade="t1", entry_allowed=True, secret=SECRET):
    import live_copy_control as lc
    return lc.sign_approval({"schema": lc.APPROVAL_SCHEMA, "trade_id": trade, "correlation_id": trade,
                             "event": event, "entry_allowed": entry_allowed}, secret)


def _row(event_id, lane, created, *, event="ORDER_PLACED", approval=True, trade="t1", secret=SECRET):
    payload = {"research_lane": lane, "event": event, "trade_id": trade}
    if approval:
        payload["live_copy_approval"] = _approval(event, trade, secret=secret)
    return {"event_id": event_id, "created_at_unix": created, "payload": payload}


ON = _Output(True, 90.0)


def test_unapproved_rows_withheld_even_when_output_off(tmp_path, monkeypatch):
    """The gate applies at ALL times: no signed approval -> never delivered."""
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    denials = []
    ns = filter_ns(sw, denials, monkeypatch=monkeypatch)
    rows = [_row("e1", LANE, 100.0, approval=False)]
    assert ns["_filter_relay_rows_by_live_switch"](rows, armed=False, now=200.0) == []
    assert denials[0][1] == "APPROVAL_MISSING_OR_INVALID"
    # Audited once per event id.
    ns["_filter_relay_rows_by_live_switch"](rows, armed=False, now=201.0)
    assert len(denials) == 1


def test_wrong_key_approval_withheld(tmp_path, monkeypatch):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials, ON, monkeypatch)
    rows = [_row("e1", LANE, 150.0, secret="attacker")]
    assert ns["_filter_relay_rows_by_live_switch"](rows, now=200.0) == []


def test_output_on_tile_off_withholds_entry(tmp_path, monkeypatch):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    denials = []
    ns = filter_ns(sw, denials, ON, monkeypatch)
    out = ns["_filter_relay_rows_by_live_switch"]([_row("e1", LANE, 100.0)], armed=True, now=200.0)
    assert out == []
    assert ns["_relay_push_state"]["live_switch_withheld_total"] == 1


def test_output_off_withholds_entry_even_with_tile_on(tmp_path, monkeypatch):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials, _Output(False), monkeypatch)
    assert ns["_filter_relay_rows_by_live_switch"]([_row("e1", LANE, 150.0)], now=200.0) == []


def test_all_on_and_created_after_arm_delivers(tmp_path, monkeypatch):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials, ON, monkeypatch)
    rows = [_row("e1", LANE, 150.0)]
    assert ns["_filter_relay_rows_by_live_switch"](rows, armed=True, now=200.0) == rows
    assert denials == []


def test_tile_on_but_created_before_arm_withholds(tmp_path, monkeypatch):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials, ON, monkeypatch)
    out = ns["_filter_relay_rows_by_live_switch"]([_row("e1", LANE, 50.0)], armed=True, now=200.0)
    assert out == []
    assert denials and denials[0][1] == DENY_TILE_PRE_ARMING


def test_unknown_lane_fails_closed(tmp_path, monkeypatch):
    sw = arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"), LANE, 100.0)
    denials = []
    ns = filter_ns(sw, denials, ON, monkeypatch)
    assert ns["_filter_relay_rows_by_live_switch"]([_row("e1", "NOPE", 150.0)], now=200.0) == []
    assert denials and denials[0][1] in (DENY_SWITCH_NOT_REQUESTED, "LIVE_COPY_OUTPUT_OFF",
                                         "TILE_LIVE_SWITCH_OFF")


def test_approved_exit_flows_with_everything_off(tmp_path, monkeypatch):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    ns = filter_ns(sw, [], _Output(False), monkeypatch)
    rows = [_row("x1", LANE, 150.0, event="POSITION_CLOSED")]
    rows[0]["payload"]["live_copy_approval"] = _approval("POSITION_CLOSED", entry_allowed=False)
    assert ns["_filter_relay_rows_by_live_switch"](rows, now=200.0) == rows


def test_non_dict_rows_fail_closed(tmp_path, monkeypatch):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    ns = filter_ns(sw, [], ON, monkeypatch)
    assert ns["_filter_relay_rows_by_live_switch"](["not-a-dict"], armed=True, now=200.0) == []


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
    assert "This endpoint never arms the account" in src


def test_master_arm_routes_are_retired():
    src = BOT.read_text(encoding="utf-8")
    assert src.count("return _retired_direct_arm_response()") == 2
    assert "FLY_DIRECT_ARM_RETIRED" in src


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
