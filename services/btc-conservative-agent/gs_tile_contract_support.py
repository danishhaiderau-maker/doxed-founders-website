"""Shared assertions for the FREEZE21B GS-20261004 / B paper tiles (imported by their dedicated tests)."""
from __future__ import annotations

import time

from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    RETIRED_POLICY_IDENTITIES,
    validate_tile_registry,
)
import regime_bars_3m as bars3m

GS_SCHEMA = "tile_pre_registration_gs20261004_v1"


def assert_paper_only_registry_tile(policy, *, index: int, prefix: str, hypothesis_id: str,
                                    bonferroni_k: int) -> dict:
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[index] == policy.LANE
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is True
    assert spec["id_prefix"] == prefix and spec["max_active_signals"] == 1
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    assert policy.POLICY_SIGNATURE == spec["policy_signature"]
    assert policy.ADAPTIVE_ENTRY is True and policy.MARKET_EXIT_CONTEXT is True
    assert policy.SKIP_FILL_REVALIDATION is True
    pre = spec["pre_registration"]
    assert pre["schema"] == GS_SCHEMA and pre["hypothesis_id"] == hypothesis_id
    assert pre["freeze_id"] == "FREEZE21B-20261004" and pre["role"] == "HYPOTHESIS"
    assert pre["target"]["min_fills"] == 30 and pre["target"]["min_n_eff"] == 30
    assert pre["kill"]["bonferroni_k"] == bonferroni_k and pre["decision"]["decision_day"] == 21
    assert pre["kill"]["harm_mean_bp_at_or_below"] == -2.0 and pre["kill"]["giveback_rate_above"] == 0.25
    return spec


def assert_dashboard(policy, *needles: str) -> dict:
    payload = policy.dashboard_policy()
    text = " ".join(payload["filter_chips"]) + " " + str(payload["entry"]) + " " + str(payload["exit"])
    assert "PAPER ONLY" in payload["filter_chips"]
    for needle in needles:
        assert needle in text, needle
    return payload


class FixedEngine(bars3m.RegimeBars3m):
    """An engine whose latest closed bar is fixed (regime classification tests)."""

    def __init__(self, bar: dict | None):
        super().__init__()
        self._bar = bar

    def latest(self, at_ts=None):
        return self._bar


def bar(*, atr_pct=50.0, spread=0.5, adx=15.0, atr_bp=5.0) -> dict:
    now = time.time()
    return {"seq": 1, "close_ts": now - 5, "available_ts": now - 4, "bar_ok": True, "atr_bp": atr_bp,
            "atr_abs": atr_bp * 6.0, "atr_pct_rank": atr_pct, "adx": adx, "spread_bp": spread, "c": 60000.0,
            "scores": {}, "prev_scores": {}, "events": {}}


def decide(policy, *, direction="LONG", engine_bar=None, ai_feature=None, bid=60000.0, ask=60000.5, ts=None):
    binding = policy._BINDING
    saved = binding.engine
    binding.engine = FixedEngine(engine_bar)
    try:
        now = float(ts if ts is not None else time.time())
        return policy.decide_entry(direction=direction, signal_ts=now, bid=bid, ask=ask, bbo_ts=now - 0.5,
                                   reference_price=(bid + ask) / 2.0, ai_feature=ai_feature)
    finally:
        binding.engine = saved


def asia_ts() -> float:
    """A timestamp at 03:00 UTC today (inside the ASIA session)."""
    now = time.time()
    return now - (now % 86400) + 3 * 3600
