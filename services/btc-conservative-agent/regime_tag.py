"""Canonical per-trade regime tag (QUIET / TRENDING / VIOLENT).

One pure computation shared by the collector, analyzer, mirror and self-aware
schema registry so every trade row carries the same ``regime`` label. The rule
is fixed here and must not be duplicated anywhere else:

    RV percentile > 0.80   -> VIOLENT   (``indicator_bars_v1`` ``regime.vol_expected_pct``)
    else ADX(14) >= 20     -> TRENDING  (``indicator_bars_v1`` ``regime.adx``)
    else                   -> QUIET

``pct_rank`` in ``indicator_engine_core`` emits ``vol_expected_pct`` on a 0-100
scale, so "> 0.80" (the 80th percentile) is expressed here as ``> 80.0``.
Missing/unavailable inputs fail closed to QUIET (the most conservative label);
they are never guessed upward into VIOLENT/TRENDING.

Observation only: importing or calling this module cannot place, change or
cancel an order, read toggles, touch the relay, or create a tile.
"""
from __future__ import annotations

from typing import Any, Mapping

REGIME_QUIET = "QUIET"
REGIME_TRENDING = "TRENDING"
REGIME_VIOLENT = "VIOLENT"
REGIMES = (REGIME_QUIET, REGIME_TRENDING, REGIME_VIOLENT)

# Thresholds matching the frozen rule (RV percentile > 0.80; ADX(14) >= 20).
RV_PCT_VIOLENT = 80.0
ADX_TRENDING = 20.0

# Feature id whose ``pct`` cell mirrors ``regime.vol_expected_pct``.
VOL_EXPECTED_FEATURE_ID = "VOL_EXPECTED@F:STATE"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_regime(rv_percentile: Any, adx14: Any) -> str:
    """Return one of QUIET / TRENDING / VIOLENT for a single observation.

    ``rv_percentile`` is the realised-volatility percentile on the 0-100 scale
    produced by ``indicator_engine_core.pct_rank`` (``regime.vol_expected_pct``);
    ``adx14`` is the Wilder ADX(14) value (``regime.adx``).
    """
    rv = _num(rv_percentile)
    if rv is not None and rv > RV_PCT_VIOLENT:
        return REGIME_VIOLENT
    adx = _num(adx14)
    if adx is not None and adx >= ADX_TRENDING:
        return REGIME_TRENDING
    return REGIME_QUIET


def regime_from_bar(bar: Mapping[str, Any] | None) -> str:
    """Classify a closed ``indicator_bars_v1`` row.

    Reads ``regime.vol_expected_pct`` and ``regime.adx`` first, then falls back
    to the ``VOL_EXPECTED@F:STATE`` feature ``pct`` cell if the regime block is
    absent. Returns QUIET when the bar is missing or both inputs are None.
    """
    if not isinstance(bar, Mapping):
        return REGIME_QUIET
    regime = bar.get("regime")
    regime = regime if isinstance(regime, Mapping) else {}
    rv_pct = regime.get("vol_expected_pct")
    adx14 = regime.get("adx")
    if rv_pct is None:
        features = bar.get("f")
        features = features if isinstance(features, Mapping) else {}
        vol_exp = features.get(VOL_EXPECTED_FEATURE_ID)
        if isinstance(vol_exp, (list, tuple)) and len(vol_exp) >= 3:
            rv_pct = vol_exp[2]  # FEATURE_ROW_LAYOUT = (raw, score, pct, status)
    return classify_regime(rv_pct, adx14)
