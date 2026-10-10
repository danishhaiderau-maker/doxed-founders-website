import math

import dyn_regime as dr


def _feed(eng, start, secs, mid_fn):
    rows = []
    for t in range(start, start + secs):
        m = mid_fn(t)
        out = eng.observe_row({"bucket_ts": t, "bid": m - 0.5, "ask": m + 0.5, "fresh": True, "valid_bbo": True})
        if out:
            rows.append(out)
    return rows


def test_flat_market_is_quiet_after_warmup_and_streak():
    eng = dr.DynRegimeEngine()
    rows = _feed(eng, 6000, 1300, lambda t: 60000.0)
    assert rows[0]["raw"] == "UNKNOWN"
    assert rows[-1]["raw"] == "QUIET" and rows[-1]["committed"] == "QUIET"
    assert rows[-1]["leg"] == "TREND_SCORE_FADE_HA_EXITS"


def test_steady_drift_is_trend_and_gap_is_unknown():
    eng = dr.DynRegimeEngine()
    rows = _feed(eng, 6000, 1300, lambda t: 60000.0 * math.exp(2e-4 * (t - 6000) / 60))
    assert rows[-1]["raw"] == "TREND" and rows[-1]["committed"] == "TREND"
    assert eng.observe_row({"bucket_ts": 7400, "fresh": False, "valid_bbo": False})["raw"] == "UNKNOWN"
    assert eng.committed == "UNKNOWN"


def test_classify_thresholds():
    assert dr.classify_raw(20.0, 0.0) == "VIOLENT"
    assert dr.classify_raw(5.0, -12.2) == "TREND"
    assert dr.classify_raw(5.0, 3.0) == "QUIET"
    assert dr.classify_raw(None, 1.0) == "UNKNOWN"
