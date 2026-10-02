"""Corrected main rankings, stream studies, export groups and the agent insights feed."""

import json
import os
import time

import numpy as np
import pandas as pd
import pytest

from strategy_lab import export as E
from strategy_lab import insights as I
from strategy_lab import rankings as R
from strategy_lab import stream_studies as SS

T0 = 1_790_812_800
LANES = ("FAMILY_A", "FAMILY_B")
REGISTRY = {"FAMILY_A": {"id_prefix": "faa", "label": "A"}, "FAMILY_B": {"id_prefix": "fbb", "label": "B"}}


def _trades(n=60, seed=0, lane_means=(0.0, 0.0)):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        lane = LANES[i % 2]
        rows.append({"trade_id": f"{REGISTRY[lane]['id_prefix']}-{i:04d}", "research_lane": lane,
                     "net_pnl_usd": lane_means[i % 2] + rng.normal(0, 0.01), "close_ts": T0 + 3600 * i,
                     "exit_reason": "TRAIL", "features_velocity": rng.normal(), "shared_ai_call_id": f"c{i}",
                     "direction": "LONG"})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ rankings
def test_expanding_quintiles_never_look_ahead():
    rng = np.random.default_rng(1)
    v = rng.normal(size=120)
    ts = np.arange(120, dtype=float)
    base = R.expanding_bucket_labels(v, ts)
    changed = v.copy()
    changed[100:] = 1e6                                 # rewrite the future
    after = R.expanding_bucket_labels(changed, ts)
    assert list(base[:100]) == list(after[:100])
    assert set(base[:R.EXPANDING_MIN_HISTORY]) == {"WARMUP"}
    assert set(base[R.EXPANDING_MIN_HISTORY:]) <= {"Q1", "Q2", "Q3", "Q4", "Q5"}


def test_annotate_applies_holm_and_bh_and_skips_small_rows():
    rng = np.random.default_rng(2)
    ts = T0 + 3600.0 * np.arange(40)
    rows = [{"key": "strong"}, {"key": "null"}, {"key": "tiny"}]
    samples = [(0.05 + rng.normal(0, 0.01, 40), ts), (rng.normal(0, 0.01, 40), ts), (np.ones(5), ts[:5])]
    summary = R.annotate(rows, samples, family="t")
    strong, null, tiny = rows
    assert strong["corrected_verdict"] == "POSITIVE_FWER" and strong["p_holm"] >= strong["p_value"]
    assert null["corrected_verdict"] in ("NOT_SIGNIFICANT", "POSITIVE_FDR", "NEGATIVE_FDR")
    assert tiny["corrected_verdict"] == "INSUFFICIENT_N" and tiny["p_value"] is None
    assert summary["tested"] == 2 and summary["holm_significant"] >= 1


def test_family_summary_counts_a_zero_p_value():
    rows = [{"p_value": 0.0, "p_holm": 0.0, "q_bh": 0.0, "corrected_verdict": "NEGATIVE_FWER"}]
    s = R.family_summary(rows, "f")
    assert s["raw_p_below_alpha"] == 1 and s["holm_significant"] == 1 and s["bh_discoveries"] == 1


def test_aliased_correlation_labels_are_one_test():
    rows = [{"feature": "momentum", "column": "features_velocity", "correlation_with_pnl": 0.3, "n": 100},
            {"feature": "velocity", "column": "features_velocity", "correlation_with_pnl": 0.3, "n": 100},
            {"feature": "adx", "column": "adx", "correlation_with_pnl": 0.01, "n": 100}]
    R.annotate_correlations(rows)
    assert rows[0]["p_holm"] == rows[1]["p_holm"]
    assert rows[0]["p_holm"] == pytest.approx(min(1.0, 2 * rows[0]["p_value"]), abs=2e-6)   # m = 2, not 3


def test_tile_family_excludes_forced_exits_and_build_payload():
    tr = _trades(lane_means=(0.05, 0.0))
    tr.loc[0, "exit_reason"] = "ADMIN_FORCE_CLOSE"
    payload, tables = R.build_main_rankings(tr, LANES, labels={"FAMILY_A": "A"}, reports={})
    tiles = payload["tile_verdicts"]
    assert tiles["FAMILY_A"]["n_tested"] == 29 and tiles["FAMILY_A"]["corrected_verdict"] == "POSITIVE_FWER"
    assert set(payload["family_summaries"]) == {"tiles", "feature_impact", "top_combinations", "regime_lane_cells",
                                                "feature_importance"}
    fi = payload["families"]["feature_impact"]["rows"]
    assert all(r["bucket"] != "WARMUP" or r["p_value"] is None for r in fi)
    assert {"family", "corrected_verdict", "p_holm", "q_bh"} <= set(tables["main_rankings"].columns)


# ------------------------------------------------------------------ stream studies
def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _mk(h_values, field):
    return {"markouts": {h: {field: v, "on_time": True} for h, v in h_values.items()}}


def _stream_fixture(tmp_path, n=12):
    d = tmp_path / "data"
    d.mkdir()
    tr = _trades(n=n)
    post, taker, fills, events = [], [], [], []
    for i, row in tr.iterrows():
        tid, exit_ts = row.trade_id, T0 + 3600 * i
        exit_u = row.net_pnl_usd / 0.25 * 100
        post.append({"kind": "post_exit_header", "trade_id": tid, "ts": exit_ts, "post_exit_started_ts": exit_ts,
                     "margin_usdt": 0.25, "direction": "LONG", "exit_config": {"family": "TRAIL"}})
        for s in range(0, 3601, 15):
            post.append({"trade_id": tid, "phase": "post_exit", "ts": exit_ts + s, "unreal_pct": exit_u + 4.0})
        taker.append({"schema": "taker_signal_counterfactual_v1", "signal_ts": exit_ts, "shared_ai_call_id": row.shared_ai_call_id,
                      "direction": "LONG", "latency_sec": 1.0, "raw_ai_decision": "APPROVE", "bid": 100.0, "ask": 100.01,
                      **_mk({"1s": -2.0, "10s": -2.0, "60s": -1.0, "300s": 0.0}, "markout_exit_touch_bps")})
        fills.append({"schema": "fill_markout_v1", "fill_ts": exit_ts, "trade_id": tid, "research_lane": row.research_lane,
                      "liquidity": "MAKER", **_mk({"1s": -1.0, "10s": -1.0, "60s": 0.5, "300s": 1.0}, "markout_mid_bps")})
        events.append({"trade_id": tid, "epoch_id": "ep1", "envelope": {"signal_ts": exit_ts},
                       "primary_outcome": "ACCEPTED_FILLED", "observation_status": "COMPLETE",
                       "replay_eligibility": {"eligible": True}})
    _write_jsonl(d / "post_exit_replay.jsonl.1", post[: len(post) // 2])
    _write_jsonl(d / "post_exit_replay.jsonl", post[len(post) // 2:])
    _write_jsonl(d / "taker_signal_counterfactuals.jsonl", taker)
    _write_jsonl(d / "fill_markouts.jsonl", fills)
    _write_jsonl(d / "research_events_v22.jsonl", events)
    return str(d), tr


def test_stream_studies_end_to_end_and_cache(tmp_path):
    data, tr = _stream_fixture(tmp_path)
    cache = str(tmp_path / "cache")
    kw = dict(trades=tr, registry=REGISTRY, lanes=LANES, epoch_id="ep1", epoch_start=T0 - 1, cache_dir=cache,
              now=T0 + 3600 * 20)
    payload, tables = SS.run_stream_studies(data, **kw)
    assert payload["status"] == "OK" and not payload["errors"]
    regret = tables["exit_regret"]
    one_h = regret[regret.horizon_sec == 3600]
    assert set(one_h.research_lane) == set(LANES)
    assert (one_h.hold_longer_mean_usd.round(6) == 0.01).all()          # +4% of a $0.25 margin
    assert len(tables["exit_regret_trades"]) == len(tr)
    tk = tables["taker_counterfactual"]
    all_1s = tk[(tk.group == "ALL") & (tk.horizon == "1s")].iloc[0]
    assert all_1s.n == len(tr) and all_1s.ev_exit_touch_bps == pytest.approx(-2.0)
    assert set(tk[tk.group == "TILE"].value) == set(LANES)
    fm = tables["fill_markouts"]
    assert set(fm.liquidity) == {"ALL", "MAKER"}
    ev = tables["research_events"]
    assert ev.n.sum() == len(tr) and set(ev.lane) == set(LANES)
    health = tables["stream_study_health"].set_index("stream")
    assert (health.status == "ANALYSED").all()
    again, _ = SS.run_stream_studies(data, **kw)
    assert again["cache"]["hits"] >= 1 and again["exit_regret"]["trades"] == len(tr)


def test_research_events_index_is_incremental_and_skips_torn_tail(tmp_path):
    data, tr = _stream_fixture(tmp_path, n=4)
    path = os.path.join(data, "research_events_v22.jsonl")
    cache = SS.Cache(str(tmp_path / "c"))
    _, meta = SS.research_events([path], cache, "ep1", {"faa": "FAMILY_A", "fbb": "FAMILY_B"}, T0)
    assert meta["epoch_rows"] == 4
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"trade_id": "faa-9", "epoch_id": "ep1", "envelope": {"signal_ts": T0}}) + "\n")
        fh.write('{"trade_id": "faa-torn"')                             # writer mid-line
    _, meta = SS.research_events([path], cache, "ep1", {"faa": "FAMILY_A"}, T0)
    assert meta["epoch_rows"] == 5


def test_stream_studies_isolate_a_broken_stream(tmp_path, monkeypatch):
    data, tr = _stream_fixture(tmp_path)
    monkeypatch.setattr(SS, "fill_markouts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    payload, tables = SS.run_stream_studies(data, trades=tr, registry=REGISTRY, lanes=LANES, epoch_id="ep1",
                                            epoch_start=T0 - 1, cache_dir=None)
    assert payload["status"] == "PARTIAL" and "fill_markouts" in payload["errors"]
    h = tables["stream_study_health"].set_index("stream")
    assert h.loc["fill_markouts.jsonl", "status"] == "ERROR"
    assert h.loc["post_exit_replay.jsonl", "status"] == "ANALYSED"


# ------------------------------------------------------------------ export groups
def test_export_carries_rankings_and_stream_tables(tmp_path):
    tr = _trades(lane_means=(0.05, 0.0))
    rankings, rtables = R.build_main_rankings(tr, LANES, reports={})
    E.stage_strategy_lab({}, {})
    E.stage_group("main_rankings", rankings, rtables)
    E.stage_group("stream_studies", {"status": "OK", "timing": {"total_sec": 1.0}},
                  {"exit_regret": pd.DataFrame([{"research_lane": "FAMILY_A", "horizon_sec": 3600}])})
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "report_manifest.json").write_text(json.dumps({"generation_id": "g1", "analyzer_revision": "abc"}))
    summary = E.write_export(report_dir=str(report_dir), data_dir=str(tmp_path), trades=tr, registry=REGISTRY,
                             lanes=LANES, root=str(tmp_path / "exp"))
    assert set(E.TABLES) <= set(summary["tables"])
    assert summary["tables"]["main_rankings"]["rows"] > 0 and summary["tables"]["exit_regret"]["rows"] == 1
    assert summary["main_rankings"]["tile_verdicts"]["FAMILY_A"]["corrected_verdict"] == "POSITIVE_FWER"
    assert summary["stream_studies"]["status"] == "OK"
    tiles = pd.read_csv(tmp_path / "exp" / "latest" / "tile_stats.csv")
    assert "corrected_verdict" in tiles.columns
    assert (tmp_path / "exp" / "insights_client.py").is_file()
    again = E.write_export(report_dir=str(report_dir), data_dir=str(tmp_path), trades=tr, registry=REGISTRY,
                           lanes=LANES, root=str(tmp_path / "exp2"))
    assert again["main_rankings"] == {"status": "NOT_RUN"}             # staged groups are per generation


# ------------------------------------------------------------------ insights
WALL = """\
2026-10-02T00:52:00Z | MULTIVERSE-STALL (worker multiverse-stall) DONE - Fly slot RELEASED (next: 6d3bb3e6 #258).
2026-10-02T00:52:29Z | FILL-GUARD-V2 (6d3bb3e6) CLAIMS Fly slot (multiverse-stall DONE): PR #258
2026-10-02T00:54:41Z | AI-MODEL-DEADLINE (worker ai-model-deadline) QUEUED LAST, NOT claiming slot; BATCH OFFER stands.
2026-10-02T01:03:11Z | ANALYZER-INSIGHTS (worker analyzer-engine, follow-up) STARTED, NOT claiming Fly slot
2026-10-02T01:12:26Z | FILL-GUARD-V2 (6d3bb3e6) MERGED #258 -> 1c0161948; ONE guarded push deploy run 1 IN PROGRESS.
"""


def test_parse_wall_slot_holder_and_queue():
    q = I.parse_wall(WALL.splitlines(), now=I._ts("2026-10-02T01:20:00Z"))
    assert q["slot_holder"] == "FILL-GUARD-V2" and q["slot_state"] == "DEPLOYING"
    assert [e["task"] for e in q["queue"]] == ["AI-MODEL-DEADLINE"]
    done = WALL + "2026-10-02T01:40:00Z | FILL-GUARD-V2 (6d3bb3e6) DONE - Fly slot RELEASED (next: x).\n"
    q2 = I.parse_wall(done.splitlines(), now=I._ts("2026-10-02T01:41:00Z"))
    assert q2["slot_holder"] is None and q2["slot_state"] == "FREE"


def test_snapshot_refuses_stale_components(tmp_path, monkeypatch):
    now = time.time()
    state = tmp_path / "state"
    (state / "health").mkdir(parents=True)
    (state / "health" / "system-health-latest.json").write_text(json.dumps(
        {"generated_ts": now - 3600, "verdict": "GREEN", "failing": [], "checks": []}))
    (state / "segment-pull.status.json").write_text(json.dumps(
        {"finishedAt": I._iso(now - 30), "appliedSeq": 5, "remotePublishedSeq": 5, "ackedSeq": 5}))
    wall = tmp_path / "WALL.md"
    wall.write_text(WALL, encoding="utf-8")
    monkeypatch.setattr(I, "STATE_DIR", str(state))
    monkeypatch.setattr(I, "WALL_PATH", str(wall))
    monkeypatch.setattr(I, "HEALTH_ENDPOINT", "http://127.0.0.1:9/unreachable")
    monkeypatch.setattr(I, "FLY_STATUS_URL", "http://127.0.0.1:9/unreachable")
    monkeypatch.setenv("DOXXED_ANALYZER_EXPORT_DIR", str(tmp_path / "no-export"))

    class _Client:
        class StaleExportError(RuntimeError):
            pass

        def load_latest(self, **kw):
            raise self.StaleExportError("export is 99.0 min old (limit 45 min)")

    monkeypatch.setattr(I, "_load_client", lambda: _Client())
    snap = I.snapshot(timeout=1)
    comps = snap["components"]
    assert snap["status"] == "PARTIAL"
    assert comps["system_health"]["status"] == "STALE" and comps["system_health"]["data"] is None
    assert comps["analyzer_export"]["status"] == "STALE" and comps["analyzer_export"]["data"] is None
    assert comps["fly_bot"]["status"] == "UNAVAILABLE" and comps["fly_bot"]["data"] is None
    assert comps["transfer"]["status"] == "OK" and comps["transfer"]["data"]["applied_behind_published"] == 0
    assert comps["deploy_queue"]["status"] == "OK"
    assert {r["component"] for r in snap["refused"]} == {"system_health", "analyzer_export", "fly_bot"}
    assert snap["system_verdict"] == "UNKNOWN"


def test_fly_data_joins_win_rate_from_export():
    status = {"git_rev": "abc", "execution_paused": False, "last_ai_success_at": I._iso(time.time() - 30),
              "active_tiles": [{"lane": "FAMILY_A", "label": "A"}],
              "strategy_progress": {"open_positions": 1, "pending_orders": 0,
                                    "combo_lane_execution": {"FAMILY_A": {"accepting": True}}}}
    data = I._fly_data(status, [{"research_lane": "FAMILY_A", "win_rate": 0.5, "n": 10,
                                 "corrected_verdict": "NOT_SIGNIFICANT"}], time.time())
    tile = data["tiles"][0]
    assert tile["win_pct"] == 50.0 and tile["accepting"] is True and tile["corrected_verdict"] == "NOT_SIGNIFICANT"
    assert data["ai_success_stale"] is False and data["open_positions"] == 1
