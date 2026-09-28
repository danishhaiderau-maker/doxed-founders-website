"""Deterministic fixtures for the split Fly liveness/readiness contract."""

import copy

import pytest

from fly_monitor_contract import (
    MonitorContractError,
    require_health,
    require_deployed_revision,
    require_ready,
    require_strategy_progress,
    require_tile_registry,
    resolve_deployed_revision,
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


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def _jobs(table):
    return lambda run_id: table.get(run_id, [])


def test_deployed_revision_skips_non_deploy_successes_and_failures():
    runs = [
        {"id": 3, "head_sha": SHA_C, "status": "completed", "conclusion": "success"},
        {"id": 2, "head_sha": SHA_B, "status": "completed", "conclusion": "failure"},
        {"id": 1, "head_sha": SHA_A, "status": "completed", "conclusion": "success"},
    ]
    jobs = _jobs({
        3: [{"name": "test-and-deploy", "conclusion": "skipped"}, {"name": "inspect-runtime", "conclusion": "success"}],
        2: [{"name": "test-and-deploy", "conclusion": "failure"}],
        1: [{"name": "test-and-deploy", "conclusion": "success"}],
    })
    assert resolve_deployed_revision(runs, jobs) == (SHA_A, "")


def test_in_flight_deploy_revision_is_tolerated():
    runs = [
        {"id": 2, "head_sha": SHA_B, "status": "in_progress", "conclusion": None},
        {"id": 1, "head_sha": SHA_A, "status": "completed", "conclusion": "success"},
    ]
    jobs = _jobs({
        2: [{"name": "test-and-deploy", "conclusion": None}],
        1: [{"name": "test-and-deploy", "conclusion": "success"}],
    })
    deployed, in_flight = resolve_deployed_revision(runs, jobs)
    assert (deployed, in_flight) == (SHA_A, SHA_B)
    require_deployed_revision(SHA_B, deployed=deployed, in_flight=in_flight)
    require_deployed_revision(SHA_A, deployed=deployed, in_flight=in_flight)
    with pytest.raises(MonitorContractError, match="revision drift"):
        require_deployed_revision(SHA_C, deployed=deployed, in_flight=in_flight)


def test_no_successful_deploy_fails_closed():
    with pytest.raises(MonitorContractError):
        resolve_deployed_revision([], _jobs({}))


def test_tile_registry_mismatch_fails_closed():
    with pytest.raises(MonitorContractError, match="tile registry drift"):
        require_tile_registry(
            READY,
            expected_version="v-test",
            expected_signature="different",
            expected_lanes=["fixed", "mfe"],
        )
