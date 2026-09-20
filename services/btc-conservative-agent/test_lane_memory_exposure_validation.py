"""Exposure validation is independent of presentation/execution tile rosters."""
import pytest

import pathway_lab_validation as validation


@pytest.mark.parametrize("pending,opened", [(1, 0), (0, 1), (1, 1)])
def test_configured_benchmark_exposure_does_not_make_it_an_execution_tile(pending, opened):
    benchmark = validation.COMPARISON_BENCHMARK_LANE
    before = tuple(validation.ACTIVE_TILE_ORDER)
    assert benchmark not in before
    receipt = validation.validate_lane_memory_runtime(
        {benchmark: pending}, {benchmark: opened}, ()
    )
    assert receipt["verdict"] == "PASS"
    assert tuple(validation.ACTIVE_TILE_ORDER) == before
    assert benchmark not in validation.COMBO_EXECUTION_LANES


def test_retired_benchmark_is_rejected():
    benchmark = validation.COMPARISON_BENCHMARK_LANE
    receipt = validation.validate_lane_memory_runtime({benchmark: 1}, {benchmark: 0}, (benchmark,))
    assert receipt["verdict"] == "CRITICAL"


def test_benchmark_exception_cannot_make_ai_scan_order_capable(monkeypatch):
    monkeypatch.setattr(validation, "COMPARISON_BENCHMARK_LANE", "AI_SCAN")
    receipt = validation.validate_lane_memory_runtime({"AI_SCAN": 1}, {"AI_SCAN": 0}, ())
    assert receipt["verdict"] == "CRITICAL"


def test_only_the_configured_benchmark_is_recognized(monkeypatch):
    monkeypatch.setattr(validation, "COMPARISON_BENCHMARK_LANE", "")
    receipt = validation.validate_lane_memory_runtime({"CONTINUOUS": 1}, {"CONTINUOUS": 0}, ())
    assert receipt["verdict"] == "CRITICAL"


@pytest.mark.parametrize("lane", ["AI_SCAN", "UNKNOWN", "OFFSET_029_ATR_TP_25"])
@pytest.mark.parametrize("pending,opened", [(1, 0), (0, 1), (1, 1)])
def test_unregistered_exposure_is_rejected_in_either_bucket(lane, pending, opened):
    receipt = validation.validate_lane_memory_runtime(
        {lane: pending}, {lane: opened}, ("OFFSET_029_ATR_TP_25",)
    )
    assert receipt["verdict"] == "CRITICAL"
    assert receipt["critical_issues"] == [f"UNREGISTERED_LANE_EXPOSURE:{lane}:1"]


def test_explicit_retirement_overrides_active_roster():
    lane = validation.ACTIVE_TILE_ORDER[0]
    receipt = validation.validate_lane_memory_runtime({lane: 1}, {lane: 0}, (lane,))
    assert receipt["verdict"] == "CRITICAL"


@pytest.mark.parametrize("pending,opened", [(1001, 0), (0, 1001), (1001, 1001)])
def test_active_lane_bucket_overflow_is_not_hidden(pending, opened):
    lane = validation.ACTIVE_TILE_ORDER[0]
    receipt = validation.validate_lane_memory_runtime({lane: pending}, {lane: opened}, ())
    assert receipt["verdict"] == "WARN"
    assert receipt["warn_issues"] == [f"LANE_BUCKET_OVERFLOW:{lane}:1001"]


def test_active_exposure_and_zero_unknown_buckets_remain_valid():
    lane = validation.ACTIVE_TILE_ORDER[0]
    receipt = validation.validate_lane_memory_runtime(
        {lane: 1, "UNKNOWN": 0}, {lane: 1, "UNKNOWN": 0}, ()
    )
    assert receipt["verdict"] == "PASS"
