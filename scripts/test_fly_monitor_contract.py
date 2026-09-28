"""Deterministic fixtures for the split Fly liveness/readiness contract."""

import copy

import pytest

from fly_monitor_contract import (
    MonitorContractError,
    require_health,
    require_ready,
    require_strategy_progress,
    require_tile_registry,
)


HEALTH = {
    "probe_contract": "PROCESS_LIVENESS_ONLY",
    "process_alive": True,
    "force_paper_mode": True,
    "live_armed": False,
    "bitfinex_live_enabled": False,
    "source_git_rev": "9b588c0b5f79",
}
READY = {
    "ok": True,
    "process_ready": True,
    "bot_version": "v-test",
    "tile_registry_signature": "sig-test",
    "active_tiles": [{"lane": "fixed"}, {"lane": "mfe"}],
    "strategy_progress": {"ok": True, "reasons": []},
    "strategy_progress_incident": {"active": False, "reasons": []},
}


def test_compact_health_and_full_ready_are_merged_by_contract():
    assert "strategy_progress" not in HEALTH
    assert "active_tiles" not in HEALTH
    require_health(HEALTH, status=200)
    ready = require_ready(READY, status=200)
    assert require_strategy_progress(ready)["ok"] is True
    require_tile_registry(
        ready,
        expected_version="v-test",
        expected_signature="sig-test",
        expected_lanes=["fixed", "mfe"],
    )


@pytest.mark.parametrize(
    ("payload", "status"),
    [({}, 200), ({"ok": False, "process_ready": False}, 503), (READY, 503)],
)
def test_missing_or_stale_ready_fails_closed(payload, status):
    with pytest.raises(MonitorContractError):
        require_ready(payload, status=status)


@pytest.mark.parametrize(
    "unsafe",
    [
        {"force_paper_mode": False},
        {"live_armed": True},
        {"bitfinex_live_enabled": True},
        {"process_alive": False},
    ],
)
def test_unsafe_or_dead_health_fails_closed(unsafe):
    payload = copy.deepcopy(HEALTH)
    payload.update(unsafe)
    with pytest.raises(MonitorContractError):
        require_health(payload, status=200)


def test_ready_requires_both_detailed_fields_and_rejects_progress_stall():
    for field in ("strategy_progress", "active_tiles"):
        payload = copy.deepcopy(READY)
        payload.pop(field)
        with pytest.raises(MonitorContractError):
            require_ready(payload, status=200)
    payload = copy.deepcopy(READY)
    payload["strategy_progress"] = {"ok": False, "reasons": ["AI_CADENCE_STALLED"]}
    with pytest.raises(MonitorContractError, match="AI_CADENCE_STALLED"):
        require_strategy_progress(require_ready(payload, status=200))


def test_tile_registry_mismatch_fails_closed():
    with pytest.raises(MonitorContractError, match="tile registry drift"):
        require_tile_registry(
            READY,
            expected_version="v-test",
            expected_signature="different",
            expected_lanes=["fixed", "mfe"],
        )
