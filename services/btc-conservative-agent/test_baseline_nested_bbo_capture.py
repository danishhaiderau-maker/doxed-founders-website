"""Captured nested BBOs are not hidden by an absent explicit envelope."""
from copy import deepcopy

import pytest

from research_entry_baselines import materialize_signal_time_baseline_schedules


BBO = {"bid": 99, "ask": 101, "bid_qty": 2, "ask_qty": 3}


def opportunity(features=None):
    return {"episode_id": "episode-test", "opportunity_id": "opportunity-test",
            "signal_ts": 100, "direction": "LONG", "signal_price": 100,
            "feature_snapshot_at_signal": features or {}}


@pytest.mark.parametrize("container", [None, "source_features", "market_context"])
@pytest.mark.parametrize("key", ["bbo", "signal_time_bbo"])
def test_nested_captured_quote_produces_same_schedule_as_explicit(container, key):
    nested = {key: deepcopy(BBO)}
    if container:
        nested = {container: nested}
    row = opportunity(nested)
    before = deepcopy(row)
    expected_row = opportunity()
    expected_row["signal_time_bbo"] = deepcopy(BBO)
    actual = materialize_signal_time_baseline_schedules(row)
    expected = materialize_signal_time_baseline_schedules(expected_row)
    assert actual == expected
    assert row == before
    assert actual["schedules"]["MARKET_ENTRY_AT_SIGNAL"]["schedule"][0]["limit_price"] == 101
    assert actual["schedules"]["FINAL_MARKET_AFTER_EXPIRY"]["schedule"] == []


def test_explicit_valid_quote_has_precedence_over_nested_quote():
    row = opportunity({"bbo": dict(BBO, ask=110)})
    row["signal_time_bbo"] = deepcopy(BBO)
    snapshot = materialize_signal_time_baseline_schedules(row)
    assert snapshot["schedules"]["MARKET_ENTRY_AT_SIGNAL"]["schedule"][0]["limit_price"] == 101


@pytest.mark.parametrize("invalid", [{"bid": 99, "ask": 101}, dict(BBO, ask=98), dict(BBO, ask_qty=0)])
def test_nonempty_invalid_explicit_quote_is_not_replaced_by_valid_nested_quote(invalid):
    row = opportunity({"bbo": deepcopy(BBO)})
    row["signal_time_bbo"] = invalid
    snapshot = materialize_signal_time_baseline_schedules(row)
    assert all(not value["schedule"] and value["capture_status"] == "UNKNOWN_NOT_CAPTURED_AT_SIGNAL"
               for value in snapshot["schedules"].values())


def test_empty_explicit_mapping_does_not_mask_captured_nested_quote():
    row = opportunity({"bbo": deepcopy(BBO)})
    row["signal_time_bbo"] = {}
    snapshot = materialize_signal_time_baseline_schedules(row)
    assert snapshot["schedules"]["MARKET_ENTRY_AT_SIGNAL"]["schedule"]


def test_missing_capture_does_not_borrow_future_market_path():
    row = opportunity()
    row["market_microstructure_rows"] = [{**BBO, "bucket_ts": 101}]
    row["future_bbo"] = deepcopy(BBO)
    snapshot = materialize_signal_time_baseline_schedules(row)
    assert all(not value["schedule"] and value["capture_status"] == "UNKNOWN_NOT_CAPTURED_AT_SIGNAL"
               for value in snapshot["schedules"].values())
