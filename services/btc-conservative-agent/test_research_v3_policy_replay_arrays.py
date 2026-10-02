import random

import pytest

from research_v3_candidates import protection_screen
from research_v3_policy_replay import (
    PreparedReplayArrays,
    prepare_replay_price_path,
    replay_protected_policy,
    replay_protected_policy_arrays,
)


def _spec(protection):
    return {
        "entry": {"entry_policy_id": "TAKER", "offset_pct": 0.0, "chase_id": "no_chase"},
        "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_V1", "source_fill_model": "test"},
        "loss_protection": protection["loss_protection"],
        "profit_protection": protection["profit_protection"],
        "portfolio": {"concurrency_cap": 1, "size_scale": 1.0, "daily_loss_kill_pct": 3},
    }


def _path(seed, n, start=1000.0, gaps=False):
    rng = random.Random(seed)
    price, ts, rows = 50000.0, start, []
    for _ in range(n):
        ts += rng.choice((1.0, 1.0, 1.0, 3.0)) if gaps else 1.0
        price *= 1 + rng.gauss(0, 0.0004)
        rows.append({"ts": ts, "price": round(price, 1)})
    return rows


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_vectorised_replay_equals_canonical_on_every_field(seed, direction):
    rows = _path(seed, 2400 if seed % 3 else 7200, gaps=bool(seed % 2))
    fill_ts = rows[0]["ts"] + (seed % 5)
    entry = rows[seed % 5]["price"] * (1.0005 if direction == "LONG" else 0.9995)
    atr = 0.05 + 0.03 * (seed % 4)
    prepared = prepare_replay_price_path(rows, fill_ts=fill_ts)
    arrays = PreparedReplayArrays(prepared["ordered"], direction=direction, entry_price=entry,
                                  leverage=100.0, fill_ts=fill_ts)
    for protection in protection_screen():
        spec = _spec(protection)
        kwargs = dict(direction=direction, entry_price=entry, atr_pct_at_fill=atr, leverage=100.0,
                      margin_usd=0.25, policy_spec=spec, funding_usd=0.001, slippage_usd=0.002)
        canonical = replay_protected_policy(rows, fill_ts=fill_ts, collect_trace=False, **kwargs)
        fast = replay_protected_policy_arrays(arrays, **kwargs)
        assert fast == canonical, protection["protection_id"]


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_binary_search_exits_match_scan_on_plateaus_and_immediate_stops(seed, direction):
    rng = random.Random(1000 + seed)
    price, rows = 50000.0, []
    for index in range(1800):
        price *= 1 + rng.gauss(0, 0.004 if seed % 2 else 0.0008)
        rows.append({"ts": 1000.0 + index, "price": round(price, -1)})
    entry = rows[0]["price"]
    prepared = prepare_replay_price_path(rows, fill_ts=1000.0)
    arrays = PreparedReplayArrays(prepared["ordered"], direction=direction, entry_price=entry,
                                  leverage=100.0, fill_ts=1000.0)
    for protection in protection_screen():
        kwargs = dict(direction=direction, entry_price=entry, atr_pct_at_fill=0.04 + 0.02 * (seed % 3),
                      leverage=100.0, margin_usd=0.25, policy_spec=_spec(protection))
        assert replay_protected_policy_arrays(arrays, **kwargs) == replay_protected_policy(
            rows, fill_ts=1000.0, collect_trace=False, **kwargs), protection["protection_id"]


def test_vectorised_replay_matches_empty_and_invalid_paths():
    protection = protection_screen()[0]
    spec = _spec(protection)
    empty = PreparedReplayArrays((), direction="LONG", entry_price=100.0, leverage=100.0, fill_ts=0)
    kwargs = dict(direction="LONG", entry_price=100.0, atr_pct_at_fill=1.0, leverage=100.0,
                  margin_usd=0.25, policy_spec=spec)
    assert replay_protected_policy_arrays(empty, **kwargs) == replay_protected_policy(
        [], fill_ts=0, collect_trace=False, **kwargs)
    bad = dict(kwargs, policy_spec={**spec, "fill": {"execution_world": "NOPE"}})
    assert replay_protected_policy_arrays(empty, **bad) == replay_protected_policy(
        [{"ts": 1, "price": 100}], fill_ts=0, collect_trace=False, **bad)
