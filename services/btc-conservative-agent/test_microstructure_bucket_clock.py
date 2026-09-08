import pytest
from microstructure_bucket_clock import observed_bucket


def test_slow_append_skips_missing_seconds_in_constant_work():
    first = observed_bucket(101, 101.02)
    assert first["bucket_ts"] == 101
    delayed = observed_bucket(first["next_bucket"], 1301.25)
    assert delayed["bucket_ts"] == 1301
    assert delayed["skipped_bucket_count"] == 1199
    assert delayed["skipped_start_ts"] == 102
    assert delayed["skipped_end_ts_exclusive"] == 1301
    assert delayed["observed_at_ts"] == 1301.25


def test_backward_clock_does_not_duplicate_bucket():
    assert observed_bucket(102, 100.2) == {"ready": False, "wait_seconds": 1.7999999999999972}
    assert observed_bucket(102, 102)["skipped_bucket_count"] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_invalid_time_rejected(value):
    with pytest.raises(ValueError):
        observed_bucket(1, value)


def test_actual_loop_freezes_quote_before_bucket_end_and_skips_slow_write():
    import ast
    import hashlib
    import json
    import threading
    from pathlib import Path
    from types import SimpleNamespace
    from microstructure_tape import build_bucket
    clock = [100.0]
    rows = []
    state = {"bid": 100, "ask": 101, "bid_qty": 1, "ask_qty": 1, "price": 100.5, "ws_last_tick": 101}
    class Stop:
        def is_set(self):
            return len(rows) == 2
        def wait(self, seconds):
            clock[0] += seconds
            # A quote arriving at the end must not replace the frozen quote.
            if clock[0] == 102:
                state["bid"] = 200
            return False
    def append(path, row, **kwargs):
        rows.append(row)
        clock[0] += 20
        state["ws_last_tick"] = clock[0]
        return True
    ns = dict(time=SimpleNamespace(time=lambda: clock[0]), shutdown_event=Stop(),
              state=state, state_lock=threading.RLock(), venue_fill_trade_tape_lock=threading.RLock(),
              venue_fill_trade_tape=[], build_microstructure_bucket=build_bucket,
              venue_fill_trade_retention={"started_ts": 99, "last_evicted_ts": 0},
              BITFINEX_WS_SYMBOL="BTC", MICROSTRUCTURE_TAPE_FILE="unused", _safe_append_jsonl=append,
              _microstructure_rows_written=0, _microstructure_write_failures=0,
              _microstructure_admission_suppressions=0, _microstructure_io_write_failures=0,
              _microstructure_capture_observation=dict(skipped_buckets_this_process=0,
                  last_gap=None,last_capture_lag_sec=None),
              hashlib=hashlib, json=json)
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "microstructure_capture_loop")
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_record_microstructure_capture_observation")
    exec(compile(ast.Module(body=[helper, fn], type_ignores=[]), "actual-microstructure-loop", "exec"), ns)
    ns["microstructure_capture_loop"]()
    assert rows[0]["bucket_ts"] == 101 and rows[0]["bid"] == 100
    assert rows[0]["observed_at_ts"] == 101
    assert rows[1]["bucket_ts"] == 122
    assert rows[1]["collection_gap"]["skipped_bucket_count"] == 20
    assert rows[1]["observed_at_ts"] >= 122
    assert ns["_microstructure_last_bucket"] == 122
    telemetry = ns["_microstructure_capture_observation"]
    assert telemetry["skipped_buckets_this_process"] == 20
    assert telemetry["last_gap"]["skipped_start_ts"] == 102
    assert telemetry["last_gap"]["skipped_end_ts_exclusive"] == 122
    assert telemetry["last_capture_lag_sec"] >= 20


def test_trade_interval_retention_overflow_and_reconnect_fail_closed():
    from microstructure_bucket_clock import trade_interval_proof
    stream = {"generation": 1, "connected_ts": 90, "last_disconnect": 80,
              "connected": True, "ready": True, "trades_subscribed": True}
    retention = {"started_ts": 90, "last_evicted_ts": 99.9}
    assert trade_interval_proof(100, 101, retention, stream, stream)["complete"]
    assert not trade_interval_proof(100, 101, {**retention, "last_evicted_ts": 100}, stream, stream)["complete"]
    assert not trade_interval_proof(100, 101, retention, stream, {**stream, "generation": 2})["complete"]
    assert not trade_interval_proof(100, 100.9, retention, stream, stream)["complete"]
    assert not trade_interval_proof(100, 101, retention, {}, {})["complete"]
