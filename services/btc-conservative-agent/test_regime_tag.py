"""Unit tests for the canonical regime tag (regime_tag.py)."""

import pytest

import regime_tag


def test_classify_regime_violent_wins_over_trending():
    assert regime_tag.classify_regime(81.0, 25.0) == regime_tag.REGIME_VIOLENT


def test_classify_regime_trending_when_adx_at_or_above_20():
    assert regime_tag.classify_regime(50.0, 20.0) == regime_tag.REGIME_TRENDING
    assert regime_tag.classify_regime(50.0, 30.0) == regime_tag.REGIME_TRENDING


def test_classify_regime_quiet_below_both_thresholds():
    assert regime_tag.classify_regime(50.0, 19.9) == regime_tag.REGIME_QUIET


def test_classify_regime_boundary_rv_is_not_violent():
    # "> 0.80" is strict: exactly 80.0 is not VIOLENT.
    assert regime_tag.classify_regime(80.0, 10.0) == regime_tag.REGIME_QUIET
    assert regime_tag.classify_regime(80.01, 10.0) == regime_tag.REGIME_VIOLENT


def test_classify_regime_missing_inputs_fail_closed_to_quiet():
    assert regime_tag.classify_regime(None, None) == regime_tag.REGIME_QUIET
    assert regime_tag.classify_regime(None, 30.0) == regime_tag.REGIME_TRENDING
    assert regime_tag.classify_regime(90.0, None) == regime_tag.REGIME_VIOLENT
    assert regime_tag.classify_regime("n/a", "n/a") == regime_tag.REGIME_QUIET


def _bar(rv_pct=None, adx=None, vol_exp_pct=None):
    bar = {"regime": {}}
    if rv_pct is not None:
        bar["regime"]["vol_expected_pct"] = rv_pct
    if adx is not None:
        bar["regime"]["adx"] = adx
    if vol_exp_pct is not None:
        bar["f"] = {regime_tag.VOL_EXPECTED_FEATURE_ID: [None, None, vol_exp_pct, "A"]}
    return bar


def test_regime_from_bar_reads_regime_block():
    assert regime_tag.regime_from_bar(_bar(rv_pct=85.0, adx=10.0)) == regime_tag.REGIME_VIOLENT
    assert regime_tag.regime_from_bar(_bar(rv_pct=50.0, adx=22.0)) == regime_tag.REGIME_TRENDING
    assert regime_tag.regime_from_bar(_bar(rv_pct=50.0, adx=10.0)) == regime_tag.REGIME_QUIET


def test_regime_from_bar_falls_back_to_feature_pct():
    assert regime_tag.regime_from_bar(_bar(vol_exp_pct=90.0)) == regime_tag.REGIME_VIOLENT


def test_regime_from_bar_missing_or_non_mapping_is_quiet():
    assert regime_tag.regime_from_bar(None) == regime_tag.REGIME_QUIET
    assert regime_tag.regime_from_bar({}) == regime_tag.REGIME_QUIET
    assert regime_tag.regime_from_bar("not a mapping") == regime_tag.REGIME_QUIET


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
