"""Strategy lab: simulator world rules, honest statistics, engine, export and client."""

import json
import math
import os

import numpy as np
import pandas as pd
import pytest

import combo_pathway_config as cpc
import cross_venue_tape as cvt
from strategy_lab import hypotheses as H
from strategy_lab import stats as S
from strategy_lab.client import StaleExportError, load_latest
from strategy_lab.engine import run_strategy_lab
from strategy_lab.export import LATEST_POINTER, stage_strategy_lab, write_export
from strategy_lab import api as lab_api
from strategy_lab.simulator import (CostModel, EntrySpec, ExitSpec, exit_spec_from_registry, live_fill_parity,
                                    simulate)
from strategy_lab.streams import stream_inventory
from strategy_lab.tape import build_tape, load_bitfinex_tape

T0 = 1_790_812_800


def _flat_tape(n=600, mid=60000.0, half=0.5, holes=()):
    ts = np.arange(T0, T0 + n)
    keep = np.ones(n, bool)
    for a, b in holes:
        keep[a:b] = False
    m = np.full(n, mid)
    return ts, m, half, keep


def _tape_from_mid(mid, half=0.5, holes=()):
    n = len(mid)
    ts = np.arange(T0, T0 + n)
    keep = np.ones(n, bool)
    for a, b in holes:
        keep[a:b] = False
    return build_tape(ts[keep], (mid - half)[keep], (mid + half)[keep])


# ------------------------------------------------------------------ simulator
def test_taker_pays_the_spread_and_time_exit_scores_at_the_executable_quote():
    mid = np.full(400, 60000.0)
    tape = _tape_from_mid(mid)
    sig = pd.DataFrame({"ts": [T0 + 10.0], "side": [1]})
    out = simulate(tape, sig, EntrySpec(kind="TAKER", latency_sec=1), ExitSpec(tcap_sec=60))
    row = out.iloc[0]
    assert row.filled and row.reason == "TIME" and row.fill_ts == T0 + 11
    assert row.fill_px == pytest.approx(60000.5)
    spread_bp = 1.0 / 60000.5 * 1e4                # bid exit vs ask fill, in bp of the fill price
    assert row.net_bp == pytest.approx(-spread_bp, rel=1e-6)
    assert row.mid_bp == pytest.approx(0.0) and row.spread_cost_bp == pytest.approx(spread_bp, rel=1e-6)


def test_hard_stop_and_capacity_one_skip():
    mid = np.full(900, 60000.0)
    mid[100:] = 60000.0 * (1 - 50 / 1e4)          # -50 bp gap at t=100
    tape = _tape_from_mid(mid)
    sig = pd.DataFrame({"ts": [T0 + 10.0, T0 + 50.0, T0 + 400.0], "side": [1, 1, -1]})
    out = simulate(tape, sig, EntrySpec(kind="TAKER"), ExitSpec(tcap_sec=300, hard_bp=40))
    assert list(out.reason) == ["HARD_STOP", "BUSY", "TIME"]
    assert out.iloc[0].quote_bp < -40
    dense = simulate(tape, sig, EntrySpec(kind="TAKER"), ExitSpec(tcap_sec=300, hard_bp=40), keep_skipped=False)
    assert list(dense.reason) == ["HARD_STOP", "TIME"]


def test_paths_crossing_a_long_tape_hole_are_censored_not_filled():
    mid = np.full(600, 60000.0)
    tape = _tape_from_mid(mid, holes=[(200, 300)])
    sig = pd.DataFrame({"ts": [T0 + 100.0], "side": [-1]})
    out = simulate(tape, sig, EntrySpec(kind="TAKER"), ExitSpec(tcap_sec=300))
    assert bool(out.iloc[0].censored) and out.iloc[0].reason == "CENSORED" and math.isnan(out.iloc[0].net_bp)


def test_maker_fills_only_when_the_opposite_quote_crosses():
    mid = np.full(300, 60000.0)
    tape = _tape_from_mid(mid)
    sig = pd.DataFrame({"ts": [T0 + 10.0], "side": [1]})
    out = simulate(tape, sig, EntrySpec(kind="MAKER", ttl_sec=30), ExitSpec(tcap_sec=60))
    assert not out.iloc[0].filled                  # flat book: the ask never reaches bid + 1 tick
    mid2 = mid.copy()
    mid2[20:] -= 1.0                               # ask drops through the resting bid
    out2 = simulate(_tape_from_mid(mid2), sig, EntrySpec(kind="MAKER", ttl_sec=30), ExitSpec(tcap_sec=60))
    assert out2.iloc[0].filled and out2.iloc[0].fee_bp == pytest.approx(
        (CostModel().maker_fee_rate + CostModel().taker_fee_rate) * 1e4)


def test_registry_exit_specs_map_every_active_tile():
    for lane, spec in cpc.ACTIVE_TILE_REGISTRY.items():
        ex, why = exit_spec_from_registry(spec)
        assert ex is not None, (lane, why)
    ftf = exit_spec_from_registry(cpc.ACTIVE_TILE_REGISTRY["FAMILY_TREND_FADE_60"])[0]
    assert ftf.tcap_sec == 3600 and ftf.hard_bp == pytest.approx(40.0) and not ftf.needs_atr
    ladder = exit_spec_from_registry(cpc.ACTIVE_TILE_REGISTRY["FAMILY_ADAPTIVE_REGIME_LADDER"])[0]
    assert ladder.ladder and ladder.needs_atr


def test_live_fill_parity_replays_a_time_exit_exactly():
    mid = np.full(4000, 60000.0)
    mid[2000:] = 59900.0
    tape = _tape_from_mid(mid)
    fill = T0 + 100
    trades = pd.DataFrame([{
        "research_lane": "FAMILY_TREND_FADE_60", "trade_id": "ftf-1", "dir": "SHORT",
        "entry": 59999.5, "exit": 59900.5, "dur_min": 60.0, "exit_reason": "PATH_END_60M",
        "close_ts": pd.Timestamp(fill + 3600, unit="s", tz="UTC").isoformat(),
    }, {
        "research_lane": "FAMILY_TREND_FADE_60", "trade_id": "ftf-2", "dir": "LONG", "entry": 1, "exit": 1,
        "dur_min": 1, "exit_reason": "ADMIN_MANUAL_CLOSE", "close_ts": pd.Timestamp(fill, unit="s", tz="UTC").isoformat(),
    }])
    p = live_fill_parity(tape, trades, cpc.ACTIVE_TILE_REGISTRY, ["FAMILY_TREND_FADE_60"])
    lane = p["lanes"]["FAMILY_TREND_FADE_60"]
    assert lane["compared"] == 1 and lane["mae_bp"] == pytest.approx(0.0, abs=1e-6) and lane["verdict"] == "PASS"


# ------------------------------------------------------------------ statistics
def test_holm_and_bh_match_reference_values():
    p = [0.01, 0.04, 0.03, None]
    assert S.holm(p)[:3] == pytest.approx([0.03, 0.06, 0.06]) and S.holm(p)[3] is None
    assert S.benjamini_hochberg(p)[:3] == pytest.approx([0.03, 0.04, 0.04])


def test_expanding_quantile_never_uses_future_rows():
    rng = np.random.default_rng(1)
    v = rng.normal(size=200)
    ts = np.arange(200.0)
    a = S.expanding_quantile(v, ts, 0.5)
    v2 = v.copy()
    v2[150:] += 100.0
    b = S.expanding_quantile(v2, ts, 0.5)
    np.testing.assert_allclose(a[:151], b[:151])
    assert np.isnan(a[:30]).all()


def test_deflated_sharpe_falls_as_trials_grow():
    rng = np.random.default_rng(3)
    v = rng.normal(0.3, 1.0, 120)
    one = S.deflated_sharpe(v, 1)["dsr"]
    many = S.deflated_sharpe(v, 100)["dsr"]
    assert one > many


def test_walk_forward_scores_the_train_pick_out_of_sample():
    ts = np.arange(400.0) * 3600
    good = pd.DataFrame({"ts": ts, "net_bp": np.where(np.arange(400) % 2 == 0, 3.0, -1.0)})
    bad = pd.DataFrame({"ts": ts, "net_bp": np.where(np.arange(400) % 2 == 0, 1.0, -3.0)})
    wf = S.walk_forward({"good": good, "bad": bad}, embargo_sec=3600)
    assert wf["status"] == "OK" and all(f.get("pick") == "good" for f in wf["folds"])
    assert wf["oos"]["mean_bp"] == pytest.approx(1.0, abs=0.1)


# ------------------------------------------------------------------ registry
def test_hypothesis_registry_is_valid_and_bounded():
    assert H.validate() == []
    assert all(len(f["configs"]) <= H.MAX_EXPLORATORY_CONFIGS for f in H.EXPLORATORY_FAMILIES)
    assert all(pd.Timestamp(h["registered_at"]).tzinfo is not None for h in H.HYPOTHESES)
    assert sum(1 for h in H.HYPOTHESES if h.get("primary")) >= 1
    assert len(H.registry_signature()) == 16


# ------------------------------------------------------------------ engine on a synthetic mirror
def _write_mirror(d, lag=2, hours=6, seed=11):
    rng = np.random.default_rng(seed)
    n = hours * 3600 + 120
    leader = 60000.0 * np.exp(np.cumsum(rng.normal(0, 2.5e-4, n)))
    bfx = np.empty(n)
    bfx[lag:] = leader[:-lag]
    bfx[:lag] = leader[0]
    with open(os.path.join(d, "market_microstructure_1s.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"bucket_ts": T0 + i, "valid_bbo": True, "bid": bfx[i] - 0.5,
                                 "ask": bfx[i] + 0.5, "buy_qty": 0.1, "sell_qty": 0.1}) + "\n")
    with open(os.path.join(d, cvt.FILE_NAME), "w", encoding="utf-8") as fh:
        for m in range(n // 60):
            t0 = T0 + 60 * m
            secs = range(t0, t0 + 60)
            samples = [{"sec": s, "mid": float(leader[s - T0]), "last": None, "buy": 0.1, "sell": 0.0} for s in secs]
            row = cvt.encode_minute(t0, {"binance": samples, "bybit": samples},
                                    [float(bfx[s - T0]) for s in secs],
                                    meta={"collector_version": cvt.COLLECTOR_VERSION, "cpu_pct": 1.0})
            fh.write(json.dumps(row) + "\n")
    calls = []
    for k in range(60):
        ts = pd.Timestamp(T0 + 600 + k * 300, unit="s", tz="UTC").isoformat()
        long_s = 70 if k % 2 else 30
        calls.append({"ts": ts, "event": "AI_DECISION", "long_score": long_s, "short_score": 100 - long_s,
                      "shared_ai_call_id": f"c{k}", "trade_id": f"c{k}", "ai_direction_raw": "LONG"})
    pd.DataFrame(calls).to_csv(os.path.join(d, "ai_tranche_log.csv"), index=False)


def test_engine_runs_end_to_end_and_finds_a_planted_cross_venue_lead(tmp_path):
    _write_mirror(str(tmp_path))
    session = {"collector_v22_epoch_ts": T0, "collector_v22_epoch_id": "epoch-test"}
    payload, tables = run_strategy_lab(str(tmp_path), session=session, registry=cpc.ACTIVE_TILE_REGISTRY,
                                       tile_lanes=list(cpc.ACTIVE_TILE_REGISTRY), cache_dir=str(tmp_path / "cache"))
    assert payload["status"] == "OK" and payload["epoch_id"] == "epoch-test"
    by_id = {h["id"]: h for h in payload["hypotheses"]}
    assert set(by_id) == {h["id"] for h in H.HYPOTHESES}
    xvl = by_id["H_XVL_10S_8BP_60S"]
    assert xvl["full"]["n"] > 20 and xvl["full"]["mean_bp"] > 0
    assert xvl["verdict"] == "INSUFFICIENT"        # synthetic data predates registration: nothing unseen
    assert by_id["C_XVL_OWN_MOMENTUM"]["full"].get("mean_bp", 0) < xvl["full"]["mean_bp"]
    fam = {f["id"]: f for f in payload["families"]}
    assert fam["X_XVL_NEIGHBOURHOOD"]["family_p"] is not None and fam["X_XVL_NEIGHBOURHOOD"]["family_p"] < 0.05
    assert set(tables) >= {"hypotheses", "hypothesis_trades", "family_tests", "walk_forward", "correlation",
                           "sim_parity"}
    assert len(tables["family_tests"]) == sum(len(f["configs"]) for f in H.EXPLORATORY_FAMILIES)
    assert payload["timing"]["total_sec"] < 120
    # rotation cache: a second load re-reads only the active file
    assert load_bitfinex_tape(str(tmp_path), cache_dir=str(tmp_path / "cache")).n == payload["tape"]["seconds"]


def test_engine_reports_no_tape_without_crashing(tmp_path):
    payload, tables = run_strategy_lab(str(tmp_path), session={})
    assert payload["status"] == "NO_TAPE" and tables == {}


# ------------------------------------------------------------------ export + client + api
def _export(tmp_path, manifest_gen="gen-1"):
    report_dir = tmp_path / "analyzer"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report_manifest.json").write_text(json.dumps({
        "generation_id": manifest_gen, "analyzer_revision": "a" * 40, "generated_at": "2026-10-02T00:00:00+00:00",
        "dataset_epoch": "epoch-test", "dataset_checksum": "c" * 64}), encoding="utf-8")
    (report_dir / "trade_cohort_quarantine.json").write_text(json.dumps({"rows_detail": [
        {"trade_id": "x", "research_lane": "CONTINUOUS", "reason": "NON_REGISTRY_LANE"}]}), encoding="utf-8")
    stage_strategy_lab({"status": "OK", "hypotheses": [{"id": "H", "verdict": "INSUFFICIENT"}]},
                       {"hypotheses": pd.DataFrame([{"id": "H", "verdict": "INSUFFICIENT", "full_n": 3}])})
    trades = pd.DataFrame([{"trade_id": "t1", "research_lane": "FAMILY_TREND_FADE_60", "net_pnl_usd": 0.05,
                            "close_ts": "2026-10-01T10:00:00+00:00", "exit_reason": "PATH_END_60M"}])
    root = tmp_path / "exports"
    summary = write_export(report_dir=str(report_dir), data_dir=str(tmp_path), trades=trades,
                           registry=cpc.ACTIVE_TILE_REGISTRY, lanes=list(cpc.ACTIVE_TILE_REGISTRY), root=str(root))
    return root, report_dir, summary


def test_export_is_versioned_and_loads_from_the_bundled_client(tmp_path):
    root, report_dir, summary = _export(tmp_path)
    assert (root / "latest" / "summary.json").is_file() and (root / "analyzer_client.py").is_file()
    assert (root / "README.md").is_file()
    assert (root / LATEST_POINTER).read_text().strip() == summary["export_id"]
    assert (root / "history" / summary["export_id"] / "hypotheses.csv").is_file()
    exp = load_latest(str(root), check_live=False, retries=0)
    assert exp["hypotheses"].iloc[0]["id"] == "H"
    assert exp["tile_stats"].set_index("research_lane").loc["FAMILY_TREND_FADE_60", "n"] == 1
    assert len(exp["quarantine"]) == 1 and exp.checks["manifest_parity"] == "MATCH"
    assert exp.summary["generation"]["generation_id"] == "gen-1"
    view = lab_api.export_latest(root=str(root), report_root=str(report_dir), table="tile_stats")
    assert view["status"] == "OK" and view["rows_total"] == len(cpc.ACTIVE_TILE_REGISTRY)
    assert lab_api.hypotheses(root=str(root), report_root=str(report_dir))["hypotheses"][0]["id"] == "H"
    assert lab_api.streams_health(root=str(root), report_root=str(report_dir))["status"] == "OK"


def test_client_refuses_stale_torn_or_superseded_exports(tmp_path):
    root, report_dir, _ = _export(tmp_path)
    # superseded: the analyzer has a newer generation than the export
    manifest = json.loads((report_dir / "report_manifest.json").read_text())
    (report_dir / "report_manifest.json").write_text(json.dumps({**manifest, "generation_id": "gen-2"}))
    with pytest.raises(StaleExportError, match="generation"):
        load_latest(str(root), check_live=False, retries=0)
    assert lab_api.export_latest(root=str(root), report_root=str(report_dir))["status"] == "STALE"
    (report_dir / "report_manifest.json").write_text(json.dumps(manifest))
    # torn: a table changed after the summary was written
    with open(root / "latest" / "hypotheses.csv", "a", encoding="utf-8") as fh:
        fh.write("X,Y,1\n")
    with pytest.raises(StaleExportError, match="hash"):
        load_latest(str(root), check_live=False, retries=0)
    # old
    root2, _, _ = _export(tmp_path / "b")
    s = json.loads((root2 / "latest" / "summary.json").read_text())
    s["generated_at_ts"] -= 46 * 60
    (root2 / "latest" / "summary.json").write_text(json.dumps(s))
    with pytest.raises(StaleExportError, match="old"):
        load_latest(str(root2), check_live=False, retries=0)


def test_fill_time_guard_windowed_prior_price_matches_the_full_scan():
    from research.fill_time_guard_counterfactual import _prior_price

    rng = np.random.default_rng(5)
    secs = np.sort(rng.choice(np.arange(T0, T0 + 5000), 1500, replace=False))
    tape = [{"bucket_ts": int(s), "last": float(60000 + i)} for i, s in enumerate(secs)]
    tape_ts = [float(r["bucket_ts"]) for r in tape]
    for target in rng.uniform(T0 - 100, T0 + 6000, 200):
        assert _prior_price(tape, target, tape_ts) == _prior_price(tape, target)


def test_analyzer_reads_closed_rotations_and_windows_the_tape(tmp_path, monkeypatch):
    import analyzer_research_engine_v62 as az

    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    name = "market_microstructure_1s.jsonl"
    with open(tmp_path / f"{name}.1", "w", encoding="utf-8") as fh:
        for s in range(T0, T0 + 100):
            fh.write(json.dumps({"bucket_ts": s, "bid": 1, "ask": 2}) + "\n")
    with open(tmp_path / name, "w", encoding="utf-8") as fh:
        for s in range(T0 + 100, T0 + 200):
            fh.write(json.dumps({"bucket_ts": s, "bid": 1, "ask": 2}) + "\n")
    paths = az._rotation_paths(str(tmp_path / name))
    assert [os.path.basename(p) for p in paths] == [f"{name}.1", name]
    assert len(az._load_jsonl_rows_all_generations(name)) == 200
    near = az._tape_rows_near([T0 + 105], before_sec=10)
    assert sorted(r["bucket_ts"] for r in near) == list(range(T0 + 95, T0 + 107))
    assert az._laptop_artifacts_enabled() is bool(os.getenv("DOXXED_ANALYZER_EXPORT_DIR"))


def test_stream_inventory_flags_continuous_streams_but_not_idle_event_streams(tmp_path):
    for name in ("market_microstructure_1s.jsonl", "trades_3factor.csv"):
        (tmp_path / name).write_text("x\n")
    old = os.path.getmtime(tmp_path / "trades_3factor.csv") - 7200
    for name in ("market_microstructure_1s.jsonl", "trades_3factor.csv"):
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "market_microstructure_1s.jsonl.1").write_text("x\n")
    rows = {r["stream"]: r for r in stream_inventory(str(tmp_path))}
    assert rows["market_microstructure_1s.jsonl"]["status"] == "STALE"
    assert rows["market_microstructure_1s.jsonl"]["rotations"] == 1
    assert rows["trades_3factor.csv"]["status"] == "IDLE"
    assert rows["post_exit_replay.jsonl"]["status"] == "MISSING"
