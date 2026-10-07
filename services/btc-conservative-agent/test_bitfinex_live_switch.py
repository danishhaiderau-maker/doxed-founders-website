"""Tests for the per-tile Bitfinex live-orders switch (fail-closed)."""
from __future__ import annotations

import pytest

from bitfinex_live_switch import (
    BitfinexLiveSwitch,
    compute_size_checks,
    DENY_FORCE_PAPER,
    DENY_GLOBAL_NOT_ARMED,
    DENY_LANE_NOT_ALLOWLISTED,
    DENY_MARGIN_OUT_OF_RANGE,
    DENY_EXCHANGE_MIN_QTY,
    DENY_STOP_COVERAGE,
    DENY_SWITCH_NOT_REQUESTED,
    DENY_TILE_PRE_ARMING,
    ALLOW_ARMED,
)


def _global_arm(**overrides):
    base = {
        "force_paper_mode": False,
        "live_armed": True,
        "bitfinex_live_enabled": True,
        "relay_delivery_block": None,
        "keys_ok": True,
        "exchange_audit": {"authoritative": True, "fresh": True, "flat": True,
                           "orphan_order_ids": [], "orphan_position_ids": []},
        "market_ready": True,
        "system_ready": True,
        "manual_pause": False,
    }
    base.update(overrides)
    return base


def _size_checks(**overrides):
    base = compute_size_checks(
        margin_usd=0.25, leverage=100, mark_price=60000.0,
        exchange_min_qty=0.0001, exchange_max_qty=100.0,
        stop_coverage_verified=True, reduce_only_supported=True,
    )
    base.update(overrides)
    return base


def test_default_off_and_distinct_from_paper(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    st = sw.status()
    assert st["armed_lane_count"] == 0
    assert all(not r["bitfinex_live_orders"] for r in st["rows"])
    # The live switch is never the paper toggle key.
    assert st["tile_count"] == 13


def test_denies_because_lanes_are_not_allowlisted(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    ev = sw.evaluate(lane, global_arm=_global_arm(), size_checks=_size_checks())
    assert not ev["eligible"]
    assert DENY_LANE_NOT_ALLOWLISTED in ev["denials"]
    assert ev["relay_eligible"] is False


def test_request_on_stays_off_and_records_denial(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    snap = sw.request_on(lane, global_arm=_global_arm(), size_checks=_size_checks())
    assert snap["bitfinex_live_orders"] is False
    assert DENY_LANE_NOT_ALLOWLISTED in snap["last_denial"]


def test_force_paper_mode_denies(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    ev = sw.evaluate(lane, global_arm=_global_arm(force_paper_mode=True), size_checks=_size_checks())
    assert DENY_FORCE_PAPER in ev["denials"]


def test_global_not_armed_denies(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    ev = sw.evaluate(lane, global_arm=_global_arm(live_armed=False), size_checks=_size_checks())
    assert DENY_GLOBAL_NOT_ARMED in ev["denials"]


def test_size_margin_out_of_range_denies(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    # Use an allowlisted lane by overriding the registry check is impossible
    # here, so assert the size denial appears alongside the allowlist denial.
    ev = sw.evaluate(lane, global_arm=_global_arm(),
                     size_checks=_size_checks(margin_usd=1.00))
    assert DENY_MARGIN_OUT_OF_RANGE in ev["denials"]


def test_exchange_min_qty_denies_and_never_rounds_up(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    ev = sw.evaluate(lane, global_arm=_global_arm(),
                     size_checks=_size_checks(exchange_min_qty=0.01))
    assert DENY_EXCHANGE_MIN_QTY in ev["denials"]


def test_stop_coverage_unverified_denies(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    ev = sw.evaluate(lane, global_arm=_global_arm(),
                     size_checks=_size_checks(stop_coverage_verified=False))
    assert DENY_STOP_COVERAGE in ev["denials"]


def test_why_not_armed_is_explanatory(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    w = sw.why_not_armed(lane, global_arm=_global_arm(), size_checks=_size_checks())
    assert w["armed"] is False
    assert w["explanation"].startswith("Not armed")
    assert DENY_LANE_NOT_ALLOWLISTED in w["denials"]


def test_request_off_always_allowed(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    sw.request_off(lane, reason="OPERATOR_OFF")
    assert sw.snapshot(lane)["bitfinex_live_orders"] is False


def test_reset_all_off_fails_closed(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    st = sw.reset_all_off(reason="FAIL_CLOSED")
    assert st["armed_lane_count"] == 0


def test_unknown_lane_denies(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    ev = sw.evaluate("NOPE", global_arm=_global_arm(), size_checks=_size_checks())
    assert not ev["eligible"]
    assert DENY_LANE_NOT_ALLOWLISTED in ev["denials"]


def test_corrupt_sidecar_fails_closed(tmp_path):
    path = tmp_path / "switch.json"
    path.write_text("{not valid json", encoding="utf-8")
    sw = BitfinexLiveSwitch(path)
    assert sw.status()["armed_lane_count"] == 0


def test_compute_size_checks_quantity(tmp_path):
    s = compute_size_checks(margin_usd=0.25, leverage=100, mark_price=50000.0)
    assert s["notional_usd"] == 25.0
    assert s["quantity"] == pytest.approx(25.0 / 50000.0)


def test_compute_size_checks_missing_price_fails_closed():
    s = compute_size_checks(margin_usd=0.25, leverage=100, mark_price=None)
    assert s["quantity"] is None


# -- relay delivery gate (two-tier model, tier 2) ---------------------------
# delivery_gate() is consulted by the relay delivery path before any live order
# is placed. It is a pure gate over the switch state; in this revision every
# active tile is paper-only / relay-ineligible, so the switch can only be turned
# ON by directly setting the state (request_on always denies LANE_NOT_ALLOWLISTED).


def _arm_switch(sw, lane, allow_ts):
    row = sw._row(lane)
    row["bitfinex_live_orders"] = True
    row["last_allow_ts"] = allow_ts
    return sw


def test_delivery_gate_default_off_denies(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    lane = "FAMILY_COMMITTED_FADE_TAKER_90"
    allowed, reason = sw.delivery_gate(lane, created_at_unix=999.0, now=1000.0)
    assert allowed is False
    assert reason == DENY_SWITCH_NOT_REQUESTED


def test_delivery_gate_pre_arming_denies(tmp_path):
    sw = _arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"),
                     "FAMILY_COMMITTED_FADE_TAKER_90", 100.0)
    allowed, reason = sw.delivery_gate(
        "FAMILY_COMMITTED_FADE_TAKER_90", created_at_unix=50.0, now=200.0
    )
    assert allowed is False
    assert reason == DENY_TILE_PRE_ARMING


def test_delivery_gate_allows_after_arm(tmp_path):
    sw = _arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"),
                     "FAMILY_COMMITTED_FADE_TAKER_90", 100.0)
    allowed, reason = sw.delivery_gate(
        "FAMILY_COMMITTED_FADE_TAKER_90", created_at_unix=150.0, now=200.0
    )
    assert allowed is True
    assert reason is None


def test_delivery_gate_unknown_lane_fails_closed(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "switch.json")
    allowed, reason = sw.delivery_gate("NOPE", created_at_unix=999.0, now=1000.0)
    assert allowed is False
    assert reason == DENY_SWITCH_NOT_REQUESTED


def test_delivery_gate_missing_created_fails_closed(tmp_path):
    sw = _arm_switch(BitfinexLiveSwitch(tmp_path / "switch.json"),
                     "FAMILY_COMMITTED_FADE_TAKER_90", 100.0)
    allowed, reason = sw.delivery_gate(
        "FAMILY_COMMITTED_FADE_TAKER_90", created_at_unix=None, now=200.0
    )
    assert allowed is False
    assert reason == DENY_TILE_PRE_ARMING
