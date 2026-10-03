"""Genome grid study: replay parity with the canonical evaluator, chase/fill semantics, registry-derived exits."""
import json
import math

import numpy as np
import pytest

import combo_pathway_config as registry
from research import genome_grid_study as g
from research_v3_policy_replay import replay_protected_policy


def _spec(prot):
    return {"entry": {"entry_policy_id": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_id": "no_chase"},
            "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_V1", "source_fill_model": "test"},
            **g._spec(prot), "portfolio": {"concurrency_cap": 1, "size_scale": 1.0, "daily_loss_kill_pct": 3}}


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("seed", [3, 11])
def test_fast_replay_matches_canonical_evaluator_for_every_protection(direction, seed):
    rng = np.random.default_rng(seed)
    mark = 100_000 * np.exp(np.cumsum(rng.normal(0, 0.00004, g.PATH_END_SEC)))
    mark[rng.choice(len(mark), 200, replace=False)] = np.nan  # gaps are skipped, not filled
    path = g.prepare_path(direction, float(mark[0]), 0, {"bid": mark, "ask": mark})
    prices = [{"ts": 1000.0 + a, "price": float(p)} for a, p in zip(path["age"], path["price"])]
    protections = g.protection_specs()
    assert len(protections) >= 70
    for pid, prot in protections.items():
        spec = _spec(prot)
        canon = replay_protected_policy(prices, direction=direction, entry_price=float(mark[0]), fill_ts=1000.0,
                                        atr_pct_at_fill=0.08, leverage=g.LEVERAGE, margin_usd=g.MARGIN_USD,
                                        policy_spec=spec, collect_trace=False)
        assert canon["status"] == "COMPLETE", (pid, canon.get("reasons"))
        fast = g.fast_replay(path, spec, 0.08)
        assert fast["exit_reason"] == canon["exit_reason"], pid
        assert math.isclose(fast["net_pnl_usd"], float(canon["net_pnl_usd"]), abs_tol=1e-8), pid  # canonical rounds to 8 dp


def test_registry_exits_come_from_the_canonical_registry():
    prots = g.registry_protections()
    expected = {}
    for lane in registry.ACTIVE_TILE_ORDER:
        spec = registry.ACTIVE_TILE_REGISTRY[lane]
        if registry.is_cross_venue_clock_lane(lane):
            continue
        if spec["exit_policy"].get("family") == "TIME_EXIT_WITH_CATASTROPHIC_STOP":
            expected.setdefault(f"REGISTRY_{spec['exit_profile_id']}", []).append(lane)
    assert {pid: p["registry_lanes"] for pid, p in prots.items()} == expected
    for pid, prot in prots.items():
        lane = prot["registry_lanes"][0]
        ex = registry.ACTIVE_TILE_REGISTRY[lane]["exit_policy"]
        assert prot["loss_protection"]["time_stop_min"] * 60 == ex["max_duration_sec"]
        assert prot["loss_protection"]["hard_stop_margin_pct"] == ex["hard_stop_margin_pct"]
        assert set(g.sweep_protections(g.protection_specs())) >= {pid}


def test_limit_schedule_chases_toward_touch_only_inside_windows():
    entry = {"offset_pct": 0.30, "chase_id": "w234_s50_i180", "ttl_sec": 1800}
    bid = np.full(1800, 100_100.0)
    limit = g.limit_schedule(entry, "LONG", 100_000.0, bid, bid + 1)
    start = 100_000.0 * (1 - 0.003)
    assert np.all(limit[:600] == start)  # waits the first two 5-minute buckets
    assert limit[600] == pytest.approx(start + 0.5 * (100_100.0 - start))  # 50 % of the remaining gap
    assert np.all(np.diff(limit) >= 0) and limit[-1] < 100_100.0  # never crosses the touch
    assert limit[1499] == limit[-1]  # no reprice after the last window (1500 s)
    away = g.limit_schedule(entry, "LONG", 100_000.0, np.full(1800, 99_000.0), np.full(1800, 99_001.0))
    assert np.all(away == start)  # never chases away from the touch


def test_simulate_fill_worlds_realistic_headline_vs_optimistic_shadow():
    assert g.WORLDS == ("REALISTIC_V1", "OPTIMISTIC_TOUCH_SHADOW") and g.HEADLINE_WORLD == "REALISTIC_V1"
    entry = {"offset_pct": 0.10, "chase_id": "no_chase", "ttl_sec": 900}
    n = 900
    bid, ask = np.full(n, 100_000.0), np.full(n, 100_001.0)
    low = np.full(n, 100_000.0)
    low[100] = 99_900.0  # a print exactly at the limit: a touch, not a fill (queue ahead unknown -> top size)
    w = {"bid": bid, "ask": ask, "last": bid, "low": low, "high": ask, "fresh": np.ones(n),
         "bid_qty": np.full(n, 2.0), "ask_qty": np.full(n, 2.0), "tlow": np.full(n, np.nan), "thigh": np.full(n, np.nan),
         "sell_qty": np.zeros(n), "buy_qty": np.zeros(n), "sell_vwap": np.full(n, np.nan), "buy_vwap": np.full(n, np.nan)}
    w["tlow"][100], w["sell_qty"][100], w["sell_vwap"][100] = 99_900.0, 0.001, 99_900.0
    fills = g.simulate_fill(entry, "LONG", 100_000.0, w)
    assert fills["REALISTIC_V1"] is None  # touched, never traded through, queue not consumed
    assert fills["OPTIMISTIC_TOUCH_SHADOW"][:2] == (100, pytest.approx(99_900.0))
    w2 = {k: v.copy() for k, v in w.items()}
    w2["tlow"][300], w2["sell_qty"][300], w2["sell_vwap"][300] = 99_899.0, 0.001, 99_899.0  # trade-through
    assert g.simulate_fill(entry, "LONG", 100_000.0, w2)["REALISTIC_V1"] == (300, pytest.approx(99_900.0), 1.0, 1)
    w3 = {k: v.copy() for k, v in w.items()}
    w3["ask"][300] = 99_890.0  # BBO cross without any print is not a fill
    assert g.simulate_fill(entry, "LONG", 100_000.0, w3)["REALISTIC_V1"] is None


def test_realistic_taker_uses_latency_and_opposite_bbo():
    n = 20
    bid = np.arange(n, dtype=float) + 100_000.0
    w = {"bid": bid, "ask": bid + 2, "last": bid + 1, "low": bid, "high": bid + 2, "fresh": np.ones(n),
         "ask_qty": np.full(n, 5.0), "bid_qty": np.full(n, 5.0)}
    entry = {"offset_pct": 0.0, "chase_id": "no_chase", "ttl_sec": 0}
    fills = g.simulate_fill(entry, "LONG", 100_000.0, w, latency_steps=6)
    assert fills["REALISTIC_V1"] == (6, 100_008.0, 1.0, 0)  # ask 6 s later
    assert fills["OPTIMISTIC_TOUCH_SHADOW"][:2] == (0, 100_001.0)  # last price, no latency
    assert g.latency_steps(1000.2, 6.5) == 6 and g.latency_steps(1000.9, 0.05) == 0


def test_episode_outcomes_align_with_row_keys_and_aggregate_holdout():
    entries = [e for e in g.entry_specs() if e["offset_pct"] in (0.0, 0.27)][:3]
    protections = g.protection_specs()
    n = max(int(e["ttl_sec"]) for e in entries) + g.PATH_END_SEC + 20
    rng = np.random.default_rng(5)
    mid = 100_000 * np.exp(np.cumsum(rng.normal(0, 0.00005, n)))
    tape = g.Tape(10_000, mid - 0.5, mid + 0.5, mid, mid - 1, mid + 1, np.ones(n), [])
    g._init_worker(tape, entries, protections)
    episodes = [{"episode_id": f"e{i}", "signal_ts": 9_999.0 + i, "signal_price": float(mid[i]), "direction": "LONG",
                 "atr14_pct": 0.08} for i in range(2)]
    results = [g.evaluate_episode((i, ep)) for i, ep in enumerate(episodes)]
    keys = g.row_keys(entries, protections)
    assert all(r["status"] == "EVALUATED" and len(r["code"]) == len(keys) for r in results)
    rows = g.aggregate(results, episodes, entries, protections, cut_ts=episodes[1]["signal_ts"])
    assert len(rows) == len(keys)
    for world in g.WORLDS:
        taker = next(r for r in rows if r["entry"]["entry_id"] == "TAKER_AT_SIGNAL" and r["fill_world"] == world
                     and r["direction_rule"] == "FOLLOW")
        assert taker["all"]["signals"] == 2 and taker["train"]["signals"] == 1 and taker["oos"]["signals"] == 1
        assert taker["evidence_label"] == "SIMULATED_COUNTERFACTUAL" and taker["fill_model"] == world
        assert taker["fill_quality"]["avg_fill_fraction"] == 1.0
    assert {r["fill_model_role"] for r in rows if r["fill_world"] == "REALISTIC_V1"} == {"HEADLINE"}
    assert {r["fill_model_role"] for r in rows if r["fill_world"] != "REALISTIC_V1"} == {"COMPARISON_SHADOW_NOT_HEADLINE"}
    summary = g.headline_vs_shadow(rows)
    assert summary["REALISTIC_V1"]["role"] == "HEADLINE"


def test_cache_round_trip_and_cycle_gate(tmp_path):
    ep = {"episode_id": "x", "signal_ts": 1.0, "signal_price": 2.0, "direction": "LONG", "atr14_pct": 0.1}
    res = {"idx": 0, "status": "EVALUATED", "values": np.arange(8.0).reshape(2, 4), "code": np.array([0, -1], np.int8)}
    g._cache_store(tmp_path, ep, res)
    back = g._cache_load(tmp_path, ep, 7)
    assert back["idx"] == 7 and np.array_equal(back["values"], res["values"]) and list(back["code"]) == [0, -1]
    g._cache_store(tmp_path, dict(ep, episode_id="y"), dict(res, status="CENSORED_TAPE_WINDOW"))
    assert g._cache_load(tmp_path, dict(ep, episode_id="y"), 0) is None
    status = tmp_path / "status.json"
    for phase, finished, busy in (("ANALYZER", None, True), ("PROMOTION", None, False), ("DONE", "t", False)):
        status.write_text(json.dumps({"startedAt": "s", "phase": phase, "finishedAt": finished}), encoding="utf-8")
        assert g.analyzer_cycle_busy(status) is busy


def test_run_refuses_onedrive(tmp_path):
    with pytest.raises(SystemExit, match="REFUSED_ONEDRIVE"):
        g.run(tmp_path, tmp_path, tmp_path / "OneDrive" / "out")