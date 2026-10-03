"""Mix-and-match search, forward tracker and materialized research API (synthetic data, no tape needed)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from research import dashboard_sections as ds
from research import genome_forward_tracker as ft
from research import genome_mix_match as mm
from research import research_api_cache as cache

T0 = 1_790_000_000 - (1_790_000_000 % 86400)


def _meta(rule, pid, groups=("TIME_EXIT_HARD_STOP",)):
    return {"policy_id": f"{rule}|TAKER_AT_SIGNAL|{pid}", "direction_rule": rule, "policy_family": "REGISTRY_TIME_EXIT",
            "entry_id": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "protection_id": pid, "groups": sorted(groups),
            "tags": sorted({rule, "TAKER", *groups})}


def _grid(days=4, step=600, seed=1):
    rng = np.random.default_rng(seed)
    ts = np.arange(T0, T0 + days * 86400, step, dtype=np.float64)
    n = len(ts)
    ctx = [{"adx": float(rng.uniform(10, 40)), "vol_pct": float(rng.uniform(0, 100)), "score_gap": float(rng.uniform(0, 60)),
            "ai_confidence": float(rng.uniform(40, 80)), "spread_bp": float(rng.uniform(0.5, 3)), "session": mm.session_of(t),
            "xv_lead_bp": float(rng.normal(0, 2)), "xv_premium_dev_bp": float(rng.normal(0, 3)), "episode_class": "AI_COMMITTED", "ai_side": "LONG"} for t in ts]
    day = ((ts - T0) // 86400).astype(int)
    noise = rng.normal(0, 0.05, size=(n, 3))
    lucky = np.where(day == 0, 0.08, -0.04) + noise[:, 0]       # great on day 0 only (in-sample trap)
    steady = 0.002 + noise[:, 1]                                 # small persistent edge
    loser = -0.01 + noise[:, 2]
    meta = [_meta("FADE", "A"), _meta("FOLLOW", "B"), _meta("FADE", "C", ("CHANDELIER",))]
    return mm.Grid(np.stack([lucky, steady, loser], axis=1), ts, meta, ctx, "AI_DECISION")


def test_nested_walk_forward_only_scores_unseen_blocks():
    g = _grid()
    wf = mm.nested_walk_forward(g, g.pool(), min_fills=30, min_eff=10)
    scored = [f for f in wf["folds"] if f.get("status") == "SCORED"]
    assert scored and all(f["test_day_utc"] != "1970-01-01" for f in scored)
    # the day-0 winner is picked in-sample but nested OOS must not inherit its day-0 profit
    full = g.scan(g.all, g.pool(), min_fills=30, min_eff=10)
    assert wf["oos"]["fills"] > 0
    assert wf["oos"]["ev_bp"] < mm.bp(full["ev_usd"]) + 1e-9 or g.meta[full["k"]]["protection_id"] != "A"


def test_policy_totals_sorted_by_oos_net_and_consistent():
    rows = []
    for i, (tr, oos) in enumerate([(0.5, -0.2), (-0.1, 0.4), (0.0, 0.1)]):
        rows.append({"policy_id": f"P{i}", "fill_world": "REALISTIC_V1", "policy_family": "X", "direction_rule": "FADE",
                     "entry": {"entry_id": "E"}, "exit": {"protection_id": "X"}, "holdout_verdict": "FAILED_HOLDOUT" if oos < 0 else "CONFIRMED",
                     "all": {"fills": 40, "wins": 20, "losses": 19, "net_pnl_usd": tr + oos, "ev_per_fill_usd": (tr + oos) / 40,
                             "max_drawdown_usd": -0.1, "win_rate_pct": 50.0},
                     "train": {"fills": 30, "net_pnl_usd": tr, "ev_per_fill_usd": tr / 30}, "oos": {"fills": 10, "net_pnl_usd": oos, "ev_per_fill_usd": oos / 10}})
    rows.append(dict(rows[0], fill_world="OPTIMISTIC_TOUCH_SHADOW", policy_id="SHADOW"))
    tot = mm.policy_totals(rows, "REALISTIC_V1", 2.0)
    assert [t["policy_id"] for t in tot] == ["P1", "P2", "P0"]          # OOS net desc, shadow excluded
    assert [t["train_rank"] for t in tot] == [3, 2, 1]                  # train rank kept as a secondary column
    assert all(abs(t["net_in_sample_usd"] + t["net_oos_usd"] - t["net_pnl_usd"]) < 1e-9 for t in tot)
    assert tot[-1]["holdout_verdict"] == "FAILED_HOLDOUT" and tot[0]["trades_per_day"] == 20.0


def test_forward_tracker_is_append_only_and_scores_only_after_freeze(tmp_path):
    g = _grid()
    freeze_at = T0 + 2 * 86400
    cands = [{"label": "IN_SAMPLE_TOP", "kind": "POLICY_FILTER", "cohort": "AI_DECISION",
              "rule": {"policy_id": g.meta[1]["policy_id"], "filters": []}},
             {"label": "META_POLICY", "kind": "META", "cohort": "AI_DECISION", "procedure": "META_SESSION",
              "rule": {"regime": "session", "mapping": {"EU": g.meta[1]["policy_id"], "US": None}}},
             {"label": "IN_SAMPLE_TOP", "kind": "POLICY_FILTER", "cohort": "AI_DECISION",
              "rule": {"policy_id": "FADE|GONE|X", "filters": []}}]
    cands = [c | {"rule_sha": ft.rule_sha(c["rule"])} for c in cands]
    assert ft.freeze_batch(tmp_path, cands, now=freeze_at)["status"] == "FROZEN"
    assert ft.freeze_batch(tmp_path, cands, now=freeze_at + 60)["status"] == "ALREADY_FROZEN_TODAY"
    doc = ft.score(tmp_path, {"AI_DECISION": g}, now=freeze_at + 86400)
    by = {r["label"] + (r.get("procedure") or "") + r["rule"].get("policy_id", ""): r for r in doc["rows"]}
    plain = by["IN_SAMPLE_TOP" + g.meta[1]["policy_id"]]
    assert plain["forward"]["fills"] == int((g.ts > freeze_at).sum())
    assert by["IN_SAMPLE_TOPFADE|GONE|X"]["status"] == "UNSCORABLE_POLICY_NOT_IN_GRID"
    assert by["META_POLICYMETA_SESSION"]["forward"]["fills"] < plain["forward"]["fills"]
    assert doc["chain"]["chain_ok"] and doc["rules_sha"] == ft.RULES_SHA
    lines = (tmp_path / ft.FROZEN_FILE).read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("IN_SAMPLE_TOP", "IN_SAMPLE_TQP")
    (tmp_path / ft.FROZEN_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ft.load_frozen(tmp_path)[1]["chain_ok"] is False
    assert ft.freeze_batch(tmp_path, cands, now=freeze_at + 86400)["status"] == "REFUSED_CHAIN_BROKEN"


@pytest.mark.parametrize("sm,low,want", [
    ({"fills": 10, "n_eff": 10, "ev_bp": 9, "ci_bp": [1, 20]}, 0.5, "INSUFFICIENT"),
    ({"fills": 40, "n_eff": 35, "ev_bp": 9, "ci_bp": [1, 20]}, 0.5, "FORWARD_CONFIRMED"),
    ({"fills": 40, "n_eff": 35, "ev_bp": 9, "ci_bp": [1, 20]}, -0.5, "INSUFFICIENT"),
    ({"fills": 40, "n_eff": 35, "ev_bp": -1, "ci_bp": [-5, 3]}, -6, "FORWARD_FAILED"),
    ({"fills": 40, "n_eff": 12, "ev_bp": -9, "ci_bp": [-15, -2]}, -16, "FORWARD_FAILED"),
])
def test_forward_verdict_rules(sm, low, want):
    assert ft.verdict(sm, low) == want


def test_candidates_cover_every_selection_kind():
    g = _grid()
    mix = mm.mix_match(g, None, full=True)
    labels = {c["label"] for c in ft.candidates_from(mix)}
    assert {"IN_SAMPLE_TOP", "NESTED_PROCEDURE_PICK", "REGIME_WINNER"} <= labels
    assert {r["regime"] for r in mix["regime_map"]} == set(mm.REGIMES)
    assert mix["multiple_testing"]["configurations_searched_full_data"] > 0


def _materialize(tmp_path):
    rows = [{"policy_id": f"P{i}", "net_oos_usd": float(5 - i), "train_rank": i + 1, "cohort": "AI" if i % 2 else "XV",
             "nested_oos": {"ev_bp": float(i)}, "holdout_verdict": "FAILED_HOLDOUT" if i == 2 else "CONFIRMED"} for i in range(6)]
    return cache.materialize(tmp_path, {"policy_totals": rows, "mix_match_structures": rows},
                             generation="g1", generated_at="2026-10-03T12:00:00Z")


def test_research_cache_sort_filter_paginate(tmp_path):
    summary = _materialize(tmp_path)
    db = tmp_path / cache.DB_NAME
    assert set(summary["datasets"]) == set(cache.DATASETS)
    idx = cache.index(db)
    assert idx["status"] == "OK" and {d["name"] for d in idx["datasets"]} == set(cache.DATASETS)
    st, out = cache.query(db, "policy_totals", {})
    assert st == 200 and [r["policy_id"] for r in out["rows"]] == [f"P{i}" for i in range(6)]
    st, out = cache.query(db, "policy_totals", {"sort": "train_rank", "order": "desc", "limit": "2", "offset": "1"})
    assert [r["policy_id"] for r in out["rows"]] == ["P4", "P3"] and out["total"] == 6
    st, out = cache.query(db, "mix_match_structures", {"sort": "nested_oos.ev_bp", "order": "desc", "cohort": "AI"})
    assert [r["policy_id"] for r in out["rows"]] == ["P5", "P3", "P1"]
    st, out = cache.query(db, "policy_totals", {"q": "failed_holdout"})
    assert [r["policy_id"] for r in out["rows"]] == ["P2"]
    assert cache.query(db, "policy_totals", {"sort": "x);drop"})[0] == 400
    assert cache.query(db, "policy_totals", {"nope": "1"})[0] == 400
    assert cache.query(db, "policy_totals", {"limit": "abc"})[0] == 400
    assert cache.query(db, "unknown", {})[0] == 404
    assert cache.index(tmp_path / "missing.sqlite3")["status"] == "UNAVAILABLE"


def test_section_checks_flag_unsorted_unreconciled_and_broken_chain():
    good = {"status": "OK", "generated_at": "t", "generation": "g", "contract_inputs": {
        "research_layer_status": "OK", "families_expected": ["A"], "families_present_table": ["A"], "families_present_totals": ["A"],
        "regimes_expected": ["trend"], "regimes_present": ["trend"], "cohorts_present": ["AI_DECISION"],
        "reconciliation": {"status": "PASS"}, "forward_chain_ok": True,
        "sort_keys": {"top_100_policies": {"key": "net_oos_usd", "values": [3, 2, 2, 1]}},
        "top_100_totals": [{"policy_id": "P", "fills": 3, "wins": 2, "losses": 1, "net_pnl_usd": 1.0,
                            "net_in_sample_usd": 0.4, "net_oos_usd": 0.6}]}}
    sev = {c["id"]: c["severity"] for c in ds.research_api_checks(good, {"generated_at": "t"})}
    assert set(sev.values()) == {"GREEN"}
    bad = json.loads(json.dumps(good))
    ci = bad["contract_inputs"]
    ci["sort_keys"]["top_100_policies"]["values"] = [1, 3]
    ci["top_100_totals"][0]["net_oos_usd"] = 0.9
    ci["forward_chain_ok"] = False
    ci["regimes_present"] = []
    sev = {c["id"]: c["severity"] for c in ds.research_api_checks(bad, {"generated_at": "other"})}
    assert sev["research_sort_order"] == sev["research_totals_reconcile"] == sev["research_forward_chain"] == "RED"
    assert sev["research_families_regimes_complete"] == sev["research_api_generation"] == "AMBER"
    assert ds.research_api_checks({"status": "UNAVAILABLE"}, None)[0]["severity"] == "RED"