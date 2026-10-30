"""PR-A: stream health fail-closed (UNKNOWN_THRESHOLD, RETIRED, thresholds, resurrection)."""
from __future__ import annotations

import json
from pathlib import Path

import monitor_streams as ms
import pandas as pd
from strategy_lab import stream_studies as studies
from strategy_lab import streams as sl_streams
from research import shadow_exit_report as ser


ROOT = Path(__file__).resolve().parent
EXPECTED = json.loads(
    (ROOT.parents[1] / "scripts" / "self_aware" / "monitor_stream_status_expected.json").read_text(encoding="utf-8")
)


def _write_stream(tmp_path, name: str, rows: list[dict], ts_key: str = "ts"):
    rel = ms.STREAMS[name]
    path = tmp_path / rel
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_expected_json_maps_unknown_threshold_to_red():
    assert EXPECTED["status_severity"]["UNKNOWN_THRESHOLD"] == "RED"
    assert ms.STATUS_SEVERITY["UNKNOWN_THRESHOLD"] == "RED"
    assert ms.STATUS_SEVERITY == EXPECTED["status_severity"]


def test_set_equality_every_stream_has_threshold_or_retired():
    ms.assert_stream_threshold_coverage()
    assert set(ms.STALE_AFTER_SEC) | set(ms.RETIRED_STREAMS) == set(ms.STREAMS)
    assert set(ms.STALE_AFTER_SEC) & set(ms.RETIRED_STREAMS) == set()


def test_liquidations_3600_others_live_86400():
    assert ms.STALE_AFTER_SEC["liquidations"] == 3600
    for name, lim in ms.STALE_AFTER_SEC.items():
        if name == "liquidations":
            continue
        assert lim == 86400, name


def test_no_threshold_and_7d_old_is_never_ok(tmp_path):
    """Fail-closed: missing threshold → UNKNOWN_THRESHOLD (RED), not OK even with a fresh-looking age."""
    now = 1_800_000_000.0
    # Inject a fake live stream with no threshold by temporarily removing one.
    name = "market_context"
    saved = ms.STALE_AFTER_SEC.pop(name)
    try:
        _write_stream(tmp_path, name, [{ "minute_ts": now - 7 * 86400, "spot": 1, "funding": 0, "oi": 1}],
                      ts_key="minute_ts")
        # Also prove classify itself: age present, limit missing → UNKNOWN_THRESHOLD
        status, lim = ms.classify_stream_status(name, age=7 * 86400, last_ts=now - 7 * 86400)
        assert status == "UNKNOWN_THRESHOLD" and lim is None
        assert ms.STATUS_SEVERITY[status] == "RED"
        h = ms.health(tmp_path, now)["streams"][name]
        assert h["status"] == "UNKNOWN_THRESHOLD"
        assert h["severity"] == "RED"
        assert h["status"] != "OK"
    finally:
        ms.STALE_AFTER_SEC[name] = saved


def test_retired_stays_retired_with_fresh_rows_before_boundary(tmp_path):
    name = "xvp_shadow_signals"
    retired_since = ms.RETIRED_STREAMS[name]
    now = retired_since + 10_000
    # Fresh relative to now, but last_ts still <= retired_since → RETIRED (not OK, not resurrected).
    _write_stream(tmp_path, name, [{"evaluated_ts": retired_since - 1, "side": "LONG", "premium_dev_bp": 2.0}])
    h = ms.health(tmp_path, now)["streams"][name]
    assert h["status"] == "RETIRED"
    assert h["severity"] == "GREEN"
    assert h["age_sec"] is not None and h["age_sec"] < 86400  # would have been OK under old fail-open


def test_resurrection_is_red_after_retirement_commit(tmp_path):
    name = "fill_markouts"
    retired_since = ms.RETIRED_STREAMS[name]
    now = retired_since + 100
    _write_stream(tmp_path, name, [{"ts": retired_since + 1, "fill_ts": retired_since + 1}])
    h = ms.health(tmp_path, now)["streams"][name]
    assert h["status"] == "RETIRED_RESURRECTED"
    assert h["severity"] == "RED"


def test_boundary_age_equal_limit_ok_limit_plus_one_stale(tmp_path):
    name = "liquidations"
    limit = ms.STALE_AFTER_SEC[name]
    now = 1_800_000_000.0
    _write_stream(tmp_path, name, [{"ts": now - limit, "venue": "bfx", "liq_side": "LONG"}])
    h = ms.health(tmp_path, now)["streams"][name]
    assert h["status"] == "OK" and h["age_sec"] == float(limit)
    _write_stream(tmp_path, name, [{"ts": now - (limit + 1), "venue": "bfx", "liq_side": "LONG"}])
    h2 = ms.health(tmp_path, now)["streams"][name]
    assert h2["status"] == "STALE" and h2["age_sec"] == float(limit + 1)


def test_taker_stage_empty_because_despite_current_epoch_rows(tmp_path):
    epoch = 1_792_263_671.0
    path = tmp_path / "taker_signal_counterfactuals.jsonl"
    path.write_text(json.dumps({
        "schema": "taker_signal_counterfactual_v1", "signal_ts": epoch + 100,
        "shared_ai_call_id": "c1", "direction": "LONG", "latency_sec": 0.25,
        "raw_ai_decision": "LONG", "bid": 1, "ask": 2,
        "markouts": {"1s": {"markout_exit_touch_bps": 1.0, "on_time": True,
                            "markout_mid_bps": 0.5}},
    }) + "\n", encoding="utf-8")
    df, meta = studies.taker_counterfactuals([str(path)], studies.Cache(None), pd.DataFrame(), epoch)
    assert meta["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert meta["rows"] == 0 and meta["rows_read"] == 0
    assert len(df) == 0


def test_shadow_exit_report_empty_because_despite_rows(tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "shadow_exit_paths.jsonl").write_text(
        json.dumps({"schema": "shadow_exit_path_v1", "record_id": "x", "source": "RUNTIME"}) + "\n",
        encoding="utf-8")
    out = ser.build_report(mirror, tmp_path / "bf")
    assert out["EMPTY_BECAUSE"] == "R17_RETIRED"
    assert out["sources"]["rows_read"] == 0 and out["sources"]["records"] == 0


def test_analyzer_streams_mark_retired():
    for name in ("fill_markouts.jsonl", "xvp_shadow_signals.jsonl", "ai_shadow_challengers.jsonl",
                 "taker_signal_counterfactuals.jsonl", "shadow_exit_paths.jsonl"):
        assert name in sl_streams.RETIRED_ANALYZER_STREAMS


def test_hm_ticker_streams_are_retired_not_thresholded():
    for name in ("fill_markouts", "xvp_shadow_signals", "ai_shadow_challengers"):
        assert name in ms.RETIRED_STREAMS
        assert name not in ms.STALE_AFTER_SEC


# --- mutation proofs (break the code → assert fails) -------------------------------------------

def test_mutation_fail_open_limit_none_would_be_ok():
    """If classify treated missing limit as OK, this mutation detector fires."""
    status, _ = ms.classify_stream_status("__no_such__", age=10.0, last_ts=1.0)
    assert status == "UNKNOWN_THRESHOLD"
    # Simulate the old fail-open predicate and prove it disagrees.
    limit_age = None
    age = 10.0
    old = "OK" if age is not None and (limit_age is None or age <= limit_age) else "STALE"
    assert old == "OK" and status != old


def test_mutation_retired_not_ok_when_fresh():
    status, _ = ms.classify_stream_status("xvp_shadow_signals", age=1.0,
                                          last_ts=ms.RETIRED_STREAMS["xvp_shadow_signals"] - 10)
    assert status == "RETIRED"
    assert status != "OK"


def test_mutation_boundary_off_by_one():
    assert ms.classify_stream_status("liquidations", age=3600, last_ts=1)[0] == "OK"
    assert ms.classify_stream_status("liquidations", age=3601, last_ts=1)[0] == "STALE"


def test_mutation_taker_empty_because_key_required():
    _, meta = studies.taker_counterfactuals([], studies.Cache(None), pd.DataFrame(), 0.0)
    assert meta.get("EMPTY_BECAUSE") == "R17_RETIRED"
    mutated = {**meta, "EMPTY_BECAUSE": "OK"}
    assert mutated["EMPTY_BECAUSE"] != "R17_RETIRED"
