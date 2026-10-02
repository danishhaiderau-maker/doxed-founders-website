import json
import os
from datetime import datetime, timezone

import numpy as np
import pytest

import market_context_tape as mct
import market_session_calendar as msc
from research import data_health_report as dh
from research import event_study as es
from research import preregistered_hypotheses as ph


REG = datetime.fromisoformat(ph.REGISTERED_UTC.replace("Z", "+00:00")).timestamp()

# Changing any rule, metric, horizon, sample size, kill rule or lockbox breaks
# the pre-registration: register a new id instead of editing these.
PINNED_SPEC_HASHES = {
    "H1_XVL_LEAD_10S_8BP_60S": "cb265d7a161a2da9",
    "H2_LIQ_BURST_60S_1M_CONTINUATION": "cd156cfe18917807",
    "H3_FUNDING_WINDOW_DRIFT_30M": "0cf35229b3437116",
    "H4_US_CASH_OPEN_VOL_EXPANSION": "95cd8c325ac11d0a",
    "H5_COINBASE_PREMIUM_LEAD_300S": "5caed452cf6364b5",
}


def test_preregistered_specs_are_frozen():
    assert {h["id"]: ph.spec_hash(h) for h in ph.HYPOTHESES} == PINNED_SPEC_HASHES
    assert ph.REGISTERED_UTC == "2026-10-02T00:00:00Z"


def test_every_hypothesis_is_fully_specified():
    for h in ph.HYPOTHESES:
        for key in ("event_rule", "metric", "horizons_sec", "primary_horizon_sec", "min_lockbox_events",
                    "kill_rule", "lockbox_days", "mechanism"):
            assert h.get(key) not in (None, "", [], {}), (h["id"], key)
        assert h["primary_horizon_sec"] in h["horizons_sec"]
        assert h["kill_rule"]


def _us_open_clock(start, days, effect_bp):
    n = days * 86400
    clock = es.Clock(start, start + n)
    rng = np.random.default_rng(7)
    mid = 100000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.2e-4, n)))
    for day in range(start - start % 86400, start + n, 86400):
        probe = day + 15 * 3600
        if datetime.fromtimestamp(probe, timezone.utc).weekday() >= 5:
            continue
        t = day + (13 if msc.us_dst(probe) else 14) * 3600 + 1800 - start
        if 0 <= t < n:
            mid[t + 2:min(n, t + 2400)] *= 1.0 + effect_bp / 1e4
    clock.bid[:] = mid - 0.5
    clock.ask[:] = mid + 0.5
    return clock


@pytest.fixture(scope="module")
def us_open_clock():
    return _us_open_clock(int(REG), 31, effect_bp=40.0)


def _h4():
    return next(h for h in ph.HYPOTHESES if h["id"] == "H4_US_CASH_OPEN_VOL_EXPANSION")


def test_lockbox_is_sealed_while_open(us_open_clock):
    terc = es.minute_terciles(us_open_clock)
    res = es.run_hypothesis(_h4(), us_open_clock, [], terc, now=REG + 10 * 86400)
    assert res["status"] == "LOCKBOX_ACCRUING"
    assert res["lockbox"]["scored"] is False
    assert "primary" not in res["lockbox"]
    assert res["lockbox"]["events_counted"] >= 20
    assert res["discovery"]["n_events"] == 0


def test_lockbox_scores_after_close(us_open_clock):
    terc = es.minute_terciles(us_open_clock)
    res = es.run_hypothesis(_h4(), us_open_clock, [], terc, now=REG + 31 * 86400)
    assert res["lockbox"]["scored"] is True
    assert res["lockbox"]["n_events"] >= _h4()["min_lockbox_events"]
    assert res["lockbox"]["primary"]["abnormal"]["mean"] > 20
    assert res["status"] == "CONFIRMED"
    assert res["spec_hash"] == PINNED_SPEC_HASHES[_h4()["id"]]


def test_null_effect_is_killed():
    clock = _us_open_clock(int(REG), 31, effect_bp=0.0)
    res = es.run_hypothesis(_h4(), clock, [], es.minute_terciles(clock), now=REG + 31 * 86400)
    assert res["status"] == "KILLED"


def test_pre_registration_events_are_exploratory_only():
    clock = _us_open_clock(int(REG) - 6 * 86400, 7, effect_bp=40.0)
    res = es.run_hypothesis(_h4(), clock, [], es.minute_terciles(clock), now=REG + 86400)
    assert res["discovery"]["label"] == "EXPLORATORY_NOT_CONFIRMATORY"
    assert res["discovery"]["n_events"] >= 3
    assert res["lockbox"]["events_counted"] <= 1


def test_controls_are_deterministic(us_open_clock):
    terc = es.minute_terciles(us_open_clock)
    valid = np.isfinite(us_open_clock.mid)
    kw = dict(max_h=1801, match=ph.CONTROL_MATCH, per_event=5, seed_key="x", valid=valid)
    a = es.control_indices(us_open_clock, [100000, 400000], terc, **kw)
    b = es.control_indices(us_open_clock, [100000, 400000], terc, **kw)
    assert a == b and all(len(c) == 5 for c in a)
    for c, i in zip(a, [100000, 400000]):
        assert all(abs(j - i) > 1801 for j in c)


def test_liquidation_burst_detector_is_causal():
    spec = next(h for h in ph.HYPOTHESES if h["id"] == "H2_LIQ_BURST_60S_1M_CONTINUATION")
    clock = es.Clock(int(REG), int(REG) + 7200)
    clock.bid[:] = 100000.0
    clock.ask[:] = 100001.0
    liqs = [{"ts": REG + 1000 + k, "venue": "bybit", "liq_side": "SHORT_LIQUIDATED", "notional_usd": 300000.0}
            for k in range(4)]
    other = [{"ts": REG + 3000 + k, "venue": "deribit", "liq_side": "LONG_LIQUIDATED", "notional_usd": 9e6}
             for k in range(2)]
    events = es.detect_events(spec, clock, liqs + other)
    assert len(events) == 1
    i, sign = events[0]
    assert clock.start + i >= REG + 1003 and sign == 1


# --------------------------------------------------------------------------
# Data health
# --------------------------------------------------------------------------
def _write(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_data_health_reports_coverage_and_staleness(tmp_path):
    t0 = 1_800_000_000
    rows = []
    for m in range(60):
        ups = {"coinbase": [True] * 60 if m % 2 == 0 else [False] * 60, "binance_spot": [True] * 60}
        mids = {"coinbase": [100000.0] * 60, "binance_spot": [99990.0] * 60}
        rows.append(mct.encode_minute(t0 + 60 * m, mids, ups, bfx_mids=[99995.0] * 60,
                                      binance_perp_mids=[None] * 60,
                                      derivatives={v: {"status": "OK", "fetched_ts": t0 + 60 * m + 20}
                                                   for v in mct.DERIV_VENUES},
                                      liquidations={v: mct.liquidation_minute_summary([], 60)
                                                    for v in mct.LIQ_VENUES},
                                      flags=msc.flags(t0 + 60 * m)))
    _write(tmp_path / mct.FILE_NAME, rows)
    _write(tmp_path / "signal_replay.jsonl", [
        {"schema": "signal_replay_v4", "trade_id": "a", "lane": "shadow",
         "replay_completion_reason": "CENSORED_PROCESS_SHUTDOWN", "replay_complete": False},
        {"schema": "signal_replay_v4", "trade_id": "b", "lane": "executed",
         "replay_completion_reason": "POST_EXIT_HORIZON_COMPLETE", "replay_complete": True},
    ])
    report = dh.build_data_health(str(tmp_path), now=t0 + 3600 + 30)
    assert report["schema"] == dh.SCHEMA
    streams = {s["stream"]: s for s in report["streams"]}
    assert streams["coinbase_1s"]["coverage_pct_24h"] == pytest.approx(50.0, abs=0.5)
    assert streams["coinbase_1s"]["status"] == "DEGRADED"
    assert streams["binance_spot_1s"]["status"] == "OK"
    assert streams["liquidations_bybit"]["status"] in ("OK", "MISSING")
    replay = report["signal_replay"]
    assert replay["distinct_trades"] == 2 and replay["distinct_complete"] == 1
    assert replay["distinct_censored_shutdown"] == 1
    later = dh.build_data_health(str(tmp_path), now=t0 + 3600 + 4 * 3600)
    assert {s["stream"]: s for s in later["streams"]}["binance_spot_1s"]["status"] == "STALE"


def test_data_health_empty_dir_is_missing_not_ok(tmp_path):
    report = dh.build_data_health(str(tmp_path), now=1_800_000_000)
    assert report["status"] == "ATTENTION"
    assert all(s["status"] == "MISSING" for s in report["streams"])


def test_event_study_empty_dir(tmp_path):
    out = es.build_from_data_dir(str(tmp_path), now=REG)
    assert out["status"] == "NO_DATA" and out["registry"]["registered_utc"] == ph.REGISTERED_UTC
