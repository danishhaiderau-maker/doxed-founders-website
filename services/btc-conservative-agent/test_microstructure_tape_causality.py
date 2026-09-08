import pytest
from microstructure_tape import build_bucket, validate_window, window_reference


def bucket(source_ts):
    return build_bucket(bucket_ts=100, bid=99, ask=100, bid_qty=1,
                        ask_qty=1, last=99.5, source_ts=source_ts)


@pytest.mark.parametrize('source_ts', [101.001, 200, 10000])
def test_future_quote_is_not_historical_evidence(source_ts):
    row = bucket(source_ts)
    assert row['source_age_sec'] < 0
    assert row['fresh'] is False
    assert not validate_window([row], window_reference(100, 101))['eligible']


def test_old_writer_fresh_flag_cannot_override_causality():
    row = dict(bucket(200), fresh=True, source_age_sec=0)
    result = validate_window([row], window_reference(100, 101))
    assert not result['eligible']
    assert result['invalid_or_stale_buckets'] == [100]


@pytest.mark.parametrize('source_ts', [97.5, 100, 101])
def test_causal_fresh_boundary_remains_eligible(source_ts):
    assert validate_window([bucket(source_ts)], window_reference(100, 101))['eligible']


@pytest.mark.parametrize('extra', [
    {'observed_at_ts': 101.1}, {'observed_at_ts': None},
    {'observed_at_ts': 99}, {'trade_bucket_complete': False},
    {'trade_bucket_complete': None},
])
def test_explicit_incomplete_or_mistimed_capture_is_ineligible(extra):
    row = dict(bucket(100), **extra)
    assert not validate_window([row], window_reference(100, 101))['eligible']


def test_quote_cannot_come_after_actual_observation():
    row = dict(bucket(100.5), observed_at_ts=100.1, trade_bucket_complete=True)
    assert not validate_window([row], window_reference(100, 101))['eligible']


def test_complete_causal_observation_passes():
    row = dict(bucket(100), observed_at_ts=100.1, trade_bucket_complete=True)
    assert validate_window([row], window_reference(100, 101))['eligible']
