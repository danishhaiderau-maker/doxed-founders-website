from test_conservative_limit_fill import evaluate, row, schedule


def test_future_quote_cannot_fill_even_with_forged_fresh_flags():
    evidence = row(100, ask=99)
    evidence.update(source_ts=1200, observed_at_ts=1200)
    result = evaluate([evidence], direction="LONG", requested_qty=1,
                      chase_schedule=schedule(end=101), aggressor_window_sec=1, symbol="BTC")
    assert not result["supported"]
    assert "QUOTE_OBSERVATION_TIME_UNPROVEN" in result["negative_reasons"]


def test_missing_quote_timestamp_is_unknown_not_no_fill():
    evidence = row(100)
    evidence.pop("source_ts")
    evidence.pop("observed_at_ts")
    result = evaluate([evidence], direction="LONG", requested_qty=1,
                      chase_schedule=schedule(end=101), aggressor_window_sec=1, symbol="BTC")
    assert result["outcome"] == "UNSUPPORTED"


def test_fill_latency_starts_at_actual_observation_not_bucket_start():
    evidence = row(100, ask=99)
    evidence.update(source_ts=100.1, observed_at_ts=100.75)
    result = evaluate([evidence], direction="LONG", requested_qty=1,
                      chase_schedule=schedule(end=101), aggressor_window_sec=1, symbol="BTC")
    assert result["supported"]
    assert result["outcome"] == "FILL"
    assert result["quote_observed_at_ts"] == 100.75
    assert result["fill_latency_sec"] == 0.75


def test_partial_fill_and_aggregate_have_separate_availability_times():
    evidence = row(100, ask=99, ask_qty=.25, sell_qty=1, sell_vwap=99)
    evidence.update(source_ts=100.1, observed_at_ts=100.75, trade_collected_at_ts=101.2)
    result = evaluate([evidence], direction="LONG", requested_qty=1,
                      chase_schedule=schedule(end=101), aggressor_window_sec=1, symbol="BTC")
    assert result["outcome"] == "PARTIAL_FILL"
    assert result["fill_latency_sec"] == .75
    assert result["aggressor_available_at_ts"] == 101.2
    assert result["aggressor_time_semantics"] == "POST_BUCKET_CORROBORATION_NOT_FILL_TRIGGER"


def test_incomplete_trade_bucket_cannot_corroborate_quote_fill():
    evidence = row(100, ask=99, sell_qty=1, sell_vwap=99)
    evidence["trade_bucket_complete"] = False
    result = evaluate([evidence], direction="LONG", requested_qty=1,
                      chase_schedule=schedule(end=101), aggressor_window_sec=1, symbol="BTC")
    assert result["outcome"] == "FILL"
    assert not result["aggressor_corroborated"]
    assert result["aggressor_available_at_ts"] is None
