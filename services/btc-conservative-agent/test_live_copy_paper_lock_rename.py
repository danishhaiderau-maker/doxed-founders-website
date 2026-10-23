"""HM E6: force_paper_mode -> live_copy_paper_lock rename (fail-closed matrix + contract)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bitfinex_live_switch import (
    BitfinexLiveSwitch, DENY_FORCE_PAPER, DENY_PAPER_LOCK_CONFLICT, compute_size_checks,
    resolve_live_copy_paper_lock,
)

HERE = Path(__file__).resolve().parent
LANE = "FAMILY_COMMITTED_FADE_TAKER_90"


def _ga(**lock):
    base = {"live_armed": True, "bitfinex_live_enabled": True, "relay_delivery_block": None,
            "keys_ok": True, "market_ready": True, "system_ready": True, "manual_pause": False,
            "exchange_audit": {"authoritative": True, "fresh": True, "flat": True,
                               "orphan_order_ids": [], "orphan_position_ids": []}}
    base.update(lock)
    return base


def _size():
    return compute_size_checks(margin_usd=0.25, leverage=100, mark_price=60000.0,
                               exchange_min_qty=0.0001, exchange_max_qty=100.0,
                               stop_coverage_verified=True, reduce_only_supported=True)


@pytest.mark.parametrize("lock,expect_locked,expect_conflict", [
    ({"force_paper_mode": True}, True, False),                                  # old only
    ({"force_paper_mode": False}, False, False),
    ({"live_copy_paper_lock": True}, True, False),                              # new only
    ({"live_copy_paper_lock": False}, False, False),
    ({"live_copy_paper_lock": True, "force_paper_mode": True}, True, False),    # both equal
    ({"live_copy_paper_lock": False, "force_paper_mode": False}, False, False),
    ({"live_copy_paper_lock": False, "force_paper_mode": True}, True, True),    # conflict
    ({"live_copy_paper_lock": True, "force_paper_mode": False}, True, True),
    ({}, True, False),                                                          # both absent
])
def test_resolve_matrix(lock, expect_locked, expect_conflict):
    assert resolve_live_copy_paper_lock(_ga(**lock)) == (expect_locked, expect_conflict)


@pytest.mark.parametrize("lock,denied", [
    ({"force_paper_mode": True}, True),
    ({"live_copy_paper_lock": True}, True),
    ({"live_copy_paper_lock": False, "force_paper_mode": True}, True),
    ({"live_copy_paper_lock": True, "force_paper_mode": False}, True),
    ({}, True),
    ({"live_copy_paper_lock": False}, False),
    ({"live_copy_paper_lock": False, "force_paper_mode": False}, False),
])
def test_switch_denies_fail_closed(tmp_path, lock, denied):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    ev = sw.evaluate(LANE, global_arm=_ga(**lock), size_checks=_size())
    assert (DENY_FORCE_PAPER in ev["denials"]) is denied
    conflict = "live_copy_paper_lock" in lock and "force_paper_mode" in lock and \
        lock["live_copy_paper_lock"] != lock["force_paper_mode"]
    assert (DENY_PAPER_LOCK_CONFLICT in ev["denials"]) is conflict


def test_readiness_api_fields_and_fail_closed_default():
    import bitfinex_readiness_api as api
    out = api._paper_lock_fields({"live_copy_paper_lock": False, "force_paper_mode": False,
                                  "fly_direct_force_paper_mode": True})
    assert out["live_copy_paper_lock"] is False and out["force_paper_mode"] is False
    assert out["deprecated_alias_of"] == {"force_paper_mode": "live_copy_paper_lock"}
    assert out["fly_direct_force_paper_mode"] is True
    assert api._paper_lock_fields({})["live_copy_paper_lock"] is True
    conflict = api._paper_lock_fields({"live_copy_paper_lock": False, "force_paper_mode": True})
    assert conflict["live_copy_paper_lock"] is True and conflict["live_copy_paper_lock_conflict"] is True


def test_monitor_ctx_lock_prefers_new_name_and_fails_closed():
    import live_copy_lifecycle as lcl
    import live_copy_monitor_api as api
    for fn in (api._ctx_paper_lock, lcl._resolve_lock):
        assert fn({"live_copy_paper_lock": False}) is False
        assert fn({"force_paper_mode": False}) is False
        assert fn({}) is True
        assert fn({"live_copy_paper_lock": False, "force_paper_mode": True}) is True


# Every emitted ``force_paper_mode`` either means the Fly direct-path flag
# (``_force_paper_mode_active()``) or sits in a dict that declares it a
# deprecated alias of ``live_copy_paper_lock``.
_CONTRACT_FILES = ("bot.py", "bitfinex_readiness_api.py", "live_copy_monitor_api.py",
                   "live_copy_monitor.py", "live_copy_lifecycle.py")
# bot.py health(): local ``force_paper_mode = _force_paper_mode_active()``.
_NAME_BOUND_TO_FLY_FLAG = {("bot.py", "force_paper_mode")}


def _violations():
    bad = []
    for name in _CONTRACT_FILES:
        tree = ast.parse((HERE / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            if "force_paper_mode" not in keys:
                continue
            value = node.values[keys.index("force_paper_mode")]
            if isinstance(value, ast.Constant) and value.value == "live_copy_paper_lock":
                continue  # the deprecated_alias_of map itself
            if "deprecated_alias_of" in keys:
                continue
            if isinstance(value, ast.Call) and ast.unparse(value.func) == "_force_paper_mode_active":
                continue
            if isinstance(value, ast.Name) and (name, value.id) in _NAME_BOUND_TO_FLY_FLAG:
                continue
            bad.append(f"{name}:{node.lineno}: {ast.unparse(value)}")
    return bad


def test_force_paper_mode_field_contract():
    assert _violations() == []
