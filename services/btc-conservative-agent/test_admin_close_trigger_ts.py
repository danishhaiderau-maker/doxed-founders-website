"""P3 (HM 19 Oct): flatten closes carry exit_trigger_ts (close-request time); latency stats never treat missing as 0."""
import inspect
import bot
import exit_latency_slo as els


def test_admin_close_stamps_trigger_ts_and_source_without_overwriting():
    pos = {"trade_id": "t"}
    assert bot._stamp_admin_close_trigger(pos, "ADMIN_MANUAL_CLOSE", 1792352364.5) is True
    assert pos["exit_trigger_ts"] == 1792352364.5 and pos["exit_trigger_source"] == "ADMIN_MANUAL_CLOSE"
    assert bot._stamp_admin_close_trigger(pos, "DEPLOY_FORCE_FLAT", 1792352999.0) is False
    assert pos["exit_trigger_ts"] == 1792352364.5 and pos["exit_trigger_source"] == "ADMIN_MANUAL_CLOSE"
    assert bot._stamp_admin_close_trigger({"exit_trigger_ts": 0}, "DEPLOY_FORCE_FLAT", 5.0) is True


def test_close_endpoint_stamps_before_closing():
    src = inspect.getsource(bot.api_close_showcase_position) if hasattr(bot, "api_close_showcase_position") else open(bot.__file__).read()
    assert src.index("_stamp_admin_close_trigger(matches[0]") < src.index("close_position(matches[0], close_reason)")


def test_stamped_trigger_flows_into_event_timestamps():
    pos = {"exit_trigger_ts": 100.0}
    ev = bot._trade_event_timestamps(pos, {}, exit_fill_ts=101.2)
    assert ev["exit_trigger_ts"] == 100.0 and ev["exit_fill_ts"] == 101.2


def test_latency_stats_skip_rows_missing_trigger_and_never_use_zero():
    rows = [{"exit_reason": "X", "event_timestamps": {"exit_trigger_ts": None, "exit_fill_ts": 1000.0}},
            {"exit_reason": "X", "event_timestamps": {"exit_trigger_ts": 0, "exit_fill_ts": 1000.0}},
            {"exit_reason": "X", "event_timestamps": {"exit_trigger_ts": 998.0, "exit_fill_ts": 1000.0}}]
    out = els.evaluate(rows)
    assert out["n"] == 1 and out["missing_trigger_ts"] == 2 and out["max_sec"] == 2.0
