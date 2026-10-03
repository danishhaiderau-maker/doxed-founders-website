"""Indicator Edge laptop scorer, pre-registration, combination grid and weekly report."""

import json
import math

import numpy as np
import pytest

import indicator_edge_spec as spec
from research import indicator_edge_combos as combos
from research import indicator_edge_cycle as cycle
from research import indicator_edge_prereg as prereg
from research import indicator_edge_weekly as weekly
from research import indicator_forward_scorer as sc
from strategy_lab import tape as tape_mod

T0 = 1_791_072_000          # 2026-10-04T00:00:00Z
ORACLE = "RSI@F:TREND"
COIN = "CCI@F:TREND"
TWIN = "CCI@S:TREND"


def _tape(days=4, spread=0.0, hole=None, slope_bp_per_min=0.0):
    n = days * 86400 + 4 * 3600
    ts = T0 + np.arange(n)
    t = np.arange(n) / 60.0
    mid = 100_000.0 * (1 + 1e-4 * (slope_bp_per_min * t + 20 * np.sin(2 * np.pi * t / 47.0)))
    keep = np.ones(n, dtype=bool)
    if hole:
        keep[hole[0] - T0: hole[1] - T0] = False
    half = mid * spread / 2e4
    return tape_mod.build_tape(ts[keep], (mid - half)[keep], (mid + half)[keep])


def _row(bar_ts, f, *, sha=None, ok=True, late=False, emitted=None, session=None):
    return {"schema": spec.BAR_SCHEMA, "feature_set_sha": sha or spec.feature_set_sha(), "bar_ts": bar_ts,
            "bar_close_ts": bar_ts + spec.BAR_SEC, "ts": emitted or bar_ts + spec.BAR_SEC + 9, "late": late,
            "health": {"ok": ok, "reasons": []}, "f": f,
            "regime": {"session": session or sc.time.strftime("%H", sc.time.gmtime(bar_ts)) and
                       ("ASIA" if sc.time.gmtime(bar_ts).tm_hour < 8 else "EU" if sc.time.gmtime(bar_ts).tm_hour < 16 else "US"),
                       "vol_tercile": "MID", "trend_state": "RANGE"}}


def _rows(tape, days=3, freeze_at=T0):
    rows, rng = [], np.random.default_rng(7)
    for bar_ts in range(T0 + 600, T0 + days * 86400 - 600, spec.BAR_SEC):
        d = sc.decision_ts(_row(bar_ts, {}))
        i = int(math.ceil(d + 2)) - tape.t0
        fut = tape.mid[i + 15 * 60] - tape.mid[i]
        side = 1 if fut > 0 else -1
        coin = int(rng.choice([-1, 1]))
        f = {ORACLE: [None, side, 50 + 40 * side, "A"], COIN: [None, coin, 50 + 40 * coin, "A"],
             TWIN: [None, coin, 50 + 40 * coin, "A"], "BB@F:BREAKOUT": [None, 0, None, "W"]}
        rows.append(_row(bar_ts, f))
    return rows


@pytest.fixture()
def frozen(tmp_path):
    root = tmp_path / "ft"
    out = prereg.freeze_feature_set(root, now=T0, code_revision="test")
    assert out["status"] == "FROZEN"
    return root


# ------------------------------------------------------------- outcomes and statistics

def test_outcomes_are_exact_on_a_straight_line_and_respect_latency():
    tape = _tape(days=1, slope_bp_per_min=0.0)
    flat = tape_mod.build_tape(tape.t0 + np.arange(tape.n), np.full(tape.n, 100.0), np.full(tape.n, 100.0))
    oc = sc.outcomes(flat, np.array([T0 + 1000.0]))
    assert oc["mid"][2][0].tolist() == [0.0, 0.0, 0.0, 0.0]
    n = 86400
    mid = 100.0 * (1 + 1e-4 * np.arange(n) / 60.0)        # +1 bp per minute
    line = tape_mod.build_tape(T0 + np.arange(n), mid, mid)
    oc = sc.outcomes(line, np.array([T0 + 1000.0]))
    for j, w in enumerate(sc.WINDOWS):
        assert oc["mid"][2][0, j] == pytest.approx(w, rel=1e-2)
        assert oc["up"][0, j] == pytest.approx(w, rel=1e-2) and oc["dn"][0, j] == pytest.approx(0.0, abs=1e-6)
    assert oc["mid"][9][0, 0] == pytest.approx(3.0, rel=1e-2)


def test_outcomes_executable_pays_the_spread_and_holes_censor():
    tape = _tape(days=1, spread=1.0, hole=(T0 + 5000, T0 + 5100))
    oc = sc.outcomes(tape, np.array([T0 + 1000.0, T0 + 4900.0]))
    mid0 = oc["mid"][2][0, 0]
    assert oc["exec_long"][0, 0] == pytest.approx(mid0 - 1.0, abs=0.01)
    assert oc["exec_short"][0, 0] == pytest.approx(-mid0 - 1.0, abs=0.01)
    assert np.isnan(oc["mid"][2][1, 0])                    # 100 s hole > 60 s censor inside +3 min
    assert np.isnan(sc.outcomes(None, np.array([1.0]))["mid"][2][0, 0])


def test_decision_ts_never_precedes_the_emitted_row():
    assert sc.decision_ts(_row(T0, {}, emitted=T0 + 189)) == T0 + 180 + 15
    assert sc.decision_ts(_row(T0, {}, emitted=T0 + 240)) == T0 + 240


def test_bh_adjust_matches_hand_computation():
    assert sc.bh_adjust([0.01, 0.04, 0.03, 0.5]) == [0.04, 0.053333, 0.053333, 0.5]
    assert sc.bh_adjust([]) == []


def test_cluster_bootstrap_floor_determinism_and_sign():
    ts = T0 + np.arange(40) * 1800.0
    pos = sc.cluster_bootstrap(np.full(40, 3.0) + np.sin(np.arange(40)), ts)
    assert pos["p_value"] < 0.01 and pos["clusters"] == 20 and pos["ci95_bp"][0] > 0
    assert pos == sc.cluster_bootstrap(np.full(40, 3.0) + np.sin(np.arange(40)), ts)
    neg = sc.cluster_bootstrap(-np.full(40, 3.0) + np.sin(np.arange(40)), ts)
    assert neg["p_value"] > 0.99
    few = sc.cluster_bootstrap(np.full(9, 50.0), T0 + np.arange(9) * 3600.0)
    assert few["p_value"] == 1.0 and few["ci95_bp"] is None


def test_spearman_handles_ties_and_constants():
    assert sc.spearman(np.array([1, 2, 2, 3.0]), np.array([1, 2, 2, 3.0])) == pytest.approx(1.0)
    assert sc.spearman(np.array([1, 2, 3.0]), np.array([3, 2, 1.0])) == pytest.approx(-1.0)
    assert sc.spearman(np.ones(5), np.arange(5.0)) is None


def test_eligibility_excludes_mismatch_pre_freeze_unhealthy_and_late():
    freeze = {"feature_set_sha": spec.feature_set_sha(), "frozen_at": T0 + 1000}
    rows = [_row(T0, {}), _row(T0 + 1800, {}, sha="0" * 64), _row(T0 + 3600, {}, ok=False),
            _row(T0 + 5400, {}, late=True), _row(T0 + 7200, {})]
    elig, ex = sc.eligible_rows(rows, freeze)
    assert [r["bar_ts"] for r in elig] == [T0 + 7200]
    assert ex == {"sha_mismatch": 1, "before_freeze": 1, "unhealthy": 1, "late": 1}
    assert sc.eligible_rows(rows, None)[0] == []


def _trial(days_pos, net=1.0, net9=1.0, q=0.01, sessions=("ASIA", "EU")):
    return {"daily": [{"day": f"d{i}", "n": 5, "mean_gross_bp": 2.0 if p else -2.0} for i, p in enumerate(days_pos)],
            "mean_net_bp": net, "mean_net_bp_9s": net9, "q_value": q,
            "regimes": {"session": {s: {"n": 10, "mean_net_bp": 1.0, "hit_rate": 0.6} for s in sessions}}}


def test_labels_follow_the_pre_registered_gates():
    assert sc.label_trial(_trial([1, 1, 1]))[0] == "HINT"
    assert sc.label_trial(_trial([1, 1]))[0] == "NOISE"
    assert sc.label_trial(_trial([1, 0, 1]))[0] == "NOISE"
    assert sc.label_trial(_trial([1, 1, 1], net=-0.1))[0] == "NOISE"
    assert sc.label_trial(_trial([1, 1, 0, 1, 1, 1, 1]))[0] == "PROMISING"
    assert sc.label_trial(_trial([1, 1, 0, 1, 1, 1, 1], q=0.2))[0] == "NOISE"       # not FDR-significant
    assert sc.label_trial(_trial([1, 1, 0, 0, 0, 1, 1]))[0] == "NOISE"               # 4/7 sign days
    assert sc.label_trial(_trial([1] * 7, sessions=("EU",)))[0] == "HINT"             # one session only
    assert sc.label_trial(_trial([1] * 7, net9=-0.5))[0] == "HINT"                    # dies at 9 s latency


def test_correlation_clusters_keep_the_best_member():
    rng = np.random.default_rng(1)
    a = rng.normal(size=200)
    value = np.column_stack([a, a * 2 + 0.01 * rng.normal(size=200), rng.normal(size=200)])
    fids = ["X@F:TREND", "Y@F:TREND", "Z@F:TREND"]
    out = sc.correlation_clusters(value, fids, {"X@F:TREND": (0,), "Y@F:TREND": (1,), "Z@F:TREND": (0,)})
    assert out["member_of"] == {"Y@F:TREND": "Y@F:TREND", "X@F:TREND": "Y@F:TREND", "Z@F:TREND": "Z@F:TREND"}
    assert out["independent_count"] == 2


# ------------------------------------------------------------- end to end

def test_not_preregistered_scores_nothing(tmp_path):
    rep = sc.score(str(tmp_path), str(tmp_path / "o"), prereg_root=tmp_path / "none", now=T0, tape=None, rows=[])
    assert rep["status"] == "NOT_PREREGISTERED" and rep["features"] == []
    assert "not been pre-registered" in sc.daily_paragraph(rep, {})


def test_oracle_feature_earns_hint_and_coin_flip_stays_noise(tmp_path, frozen):
    tape = _tape(days=4)
    rows = _rows(tape, days=3)
    ctx = {}
    rep = sc.score(str(tmp_path), str(tmp_path / "o"), prereg_root=frozen, now=T0 + 4 * 86400, tape=tape,
                   rows=rows, context=ctx)
    assert rep["status"] == "OK" and rep["scored_days"] == 3
    feats = {f["feature"]: f for f in rep["features"]}
    o = feats[ORACLE]["windows"]["15"]
    assert o["hit_rate"] == 1.0 and o["mean_net_bp"] > 0 and o["rank_ic"] > 0.5 and o["top_bottom_spread_bp"] > 0
    assert feats[ORACLE]["windows"]["15"]["label"] == "HINT"
    assert feats[COIN]["label"] == "NOISE"
    assert feats[TWIN]["cluster_rep"] in (COIN, TWIN) and feats[COIN]["cluster_rep"] == feats[TWIN]["cluster_rep"]
    assert rep["top5"][0]["feature"] == ORACLE
    assert feats["BB@F:BREAKOUT"]["best"]["signals"] == 0                 # WARMING_UP never counts
    assert len(rep["features"]) == len(spec.scored_feature_ids())
    assert rep["trial_count"] == len(spec.scored_feature_ids()) * 4
    assert ctx["rows"] and ctx["freeze"]["prereg_id"] == rep["prereg"]["prereg_id"]
    assert rep["status_inventory"]["counts"]["UNAVAILABLE"] > 0           # synthetic rows omit most features


def test_daily_summary_appends_once_per_day_and_reports_changes(tmp_path, frozen):
    tape = _tape(days=4)
    rows = _rows(tape, days=3)
    out = str(tmp_path / "o")
    rep = sc.score(str(tmp_path), out, prereg_root=frozen, now=T0 + 4 * 86400, tape=tape, rows=rows)
    assert sc.write_outputs(rep, out)["daily_appended"] is True
    rep2 = sc.score(str(tmp_path), out, prereg_root=frozen, now=T0 + 4 * 86400 + 60, tape=tape, rows=rows)
    assert sc.write_outputs(rep2, out)["daily_appended"] is False
    lines = (tmp_path / "o" / sc.DAILY_FILE).read_text().splitlines()
    first = json.loads(lines[0])
    assert len(lines) == 1 and ORACLE in first["changes"]["HINT"] and "Top 5" in first["paragraph"]
    saved = json.loads((tmp_path / "o" / sc.REPORT_FILE).read_text())
    assert saved["daily_summary"]["date"] == first["date"] and "intraday_paragraph" in saved["daily_summary"]


# ------------------------------------------------------------- pre-registration

def test_prereg_is_idempotent_chained_and_tamper_evident(tmp_path):
    root = tmp_path / "ft"
    root.mkdir()
    (root / prereg.FORWARD_TRACKER_FILE).write_text(json.dumps({"prev_sha": "GENESIS"}) + "\n")
    first = prereg.freeze_feature_set(root, now=T0)
    assert first["status"] == "FROZEN" and first["prereg_id"] == f"IE-FS-{spec.feature_set_sha()[:12]}"
    again = prereg.freeze_feature_set(root, now=T0 + 99)
    assert again["status"] == "ALREADY_FROZEN" and again["line_sha"] == first["line_sha"]
    rows, chain = prereg.load_chain(root / prereg.FROZEN_FILE)
    assert chain["chain_ok"] and len(rows) == 1 and rows[0]["anchor_forward_tracker"]["lines"] == 1
    assert rows[0]["trial_count"] == spec.trial_count() and rows[0]["spec_document"]["feature_set_version"]
    path = root / prereg.FROZEN_FILE
    original = path.read_text()
    assert '"round_trip_cost_bp":2.0' in original
    path.write_text(original.replace('"round_trip_cost_bp":2.0', '"round_trip_cost_bp":0.0'))
    assert prereg.freeze_feature_set(root, now=T0)["status"] == "REFUSED_FREEZE_TAMPERED"
    rep = sc.score(str(tmp_path), str(tmp_path / "o"), prereg_root=root, now=T0, tape=None, rows=[])
    assert rep["status"] == "PREREG_TAMPERED"
    path.write_text(original)
    combos_ok = prereg.freeze_combinations(root, [{"combo_id": "c1", "family": "B_MOMENTUM", "variant": {}}], now=T0)
    assert combos_ok["status"] == "FROZEN"
    path.write_text(path.read_text().replace('"trial_count":', '"trial_count_x":', 1))
    assert prereg.freeze_feature_set(root, now=T0)["status"] == "REFUSED_CHAIN_BROKEN"


# ------------------------------------------------------------- combinations

def _report(days, labels):
    feats = []
    for fid, lab in labels.items():
        ind = spec.indicator_of(fid)
        feats.append({"feature": fid, "label": lab, "family": ind["family"], "indicator": ind["id"],
                      "independent": True, "best_window_min": 15})
    return {"status": "OK", "scored_days": days, "features": feats, "feature_set_sha": spec.feature_set_sha()}


def test_combination_grid_is_gated_until_week_two_and_a_promising_trigger():
    assert combos.gate(_report(6, {ORACLE: "PROMISING"}))[0] is False
    assert combos.gate(_report(8, {ORACLE: "HINT"}))[0] is False
    assert combos.gate(_report(8, {ORACLE: "PROMISING"}))[0] is True


def test_candidate_variants_respect_the_shape_and_caps():
    rep = _report(8, {ORACLE: "PROMISING", "EMA_50@F:STATE": "PROMISING", "CVD@BFX:TREND": "PROMISING"})
    vs = combos.candidate_variants(rep)
    per_combo = {}
    for v in vs:
        if v["family"] == "TILE_FILTER":
            continue
        key = (v["variant"]["trigger"], v["variant"]["filter"])
        per_combo[key] = per_combo.get(key, 0) + 1
        assert v["variant"]["trigger"] != v["variant"]["filter"]
    assert per_combo and max(per_combo.values()) <= spec.COMBINATION_GRID["max_variants_per_combination"]
    assert len({v["combo_id"] for v in vs}) == len(vs)
    assert {v["variant"]["tile_filter"] for v in vs if v["family"] == "TILE_FILTER"} == set(combos.AI_TILE_FILTERS)
    fams = {}
    for v in vs:
        fams[v["family"]] = fams.get(v["family"], 0) + 1
    assert max(fams.values()) <= spec.COMBINATION_GRID["max_variants_per_family"]


def test_variant_side_applies_filter_trigger_and_confirmation():
    fids = [ORACLE, "EMA_50@F:STATE", "CVD@BFX:TREND"]
    side = np.array([[1, 1, 1], [1, -1, 1], [-1, -1, 1], [1, 1, -1]], dtype=float)
    ctx = {"fids": fids, "arrays": {"side": side, "value": side * 40},
           "rows": [{"regime": {"trend_state": "TREND", "ai_class": "COMMITTED", "ai_side": "SHORT"}}] * 4}
    base = {"trigger": ORACLE, "trigger_setting": "signal", "filter_kind": "feature", "filter": "EMA_50@F:STATE",
            "filter_setting": 0, "confirmation": None, "window_min": 15}
    assert combos.variant_side(base, ctx).tolist() == [1, 0, -1, 1]
    assert combos.variant_side(base | {"confirmation": "CVD@BFX:TREND"}, ctx).tolist() == [1, 0, 0, 0]
    reg = base | {"filter_kind": "regime", "filter": "trend_state", "filter_setting": 1}
    assert combos.variant_side(reg, ctx).tolist() == [0, 0, 0, 0]                # RANGE only, rows are TREND
    fade = {"tile_filter": "AI_COMMITTED_FADE_TREND_AGREES", "trend_feature": "EMA_50@F:STATE", "window_min": 15}
    assert combos.variant_side(fade, ctx).tolist() == [1, 0, 0, 1]               # fade SHORT -> LONG when trend LONG


def test_evaluate_freezes_scores_forward_and_proposes_without_touching_the_registry(tmp_path, frozen):
    tape = _tape(days=4)
    rows = _rows(tape, days=3)
    ctx = {}
    rep = sc.score(str(tmp_path), str(tmp_path / "o"), prereg_root=frozen, now=T0 + 4 * 86400, tape=tape,
                   rows=rows, context=ctx)
    gated = combos.evaluate(rep, ctx, prereg_root=frozen, out_dir=str(tmp_path / "o"), now=T0 + 4 * 86400)
    assert gated["status"] == "GATED" and gated["frozen_variants"] == 0
    fake = _report(8, {ORACLE: "PROMISING", "EMA_50@F:STATE": "PROMISING"})
    res = combos.evaluate(fake, ctx, prereg_root=frozen, out_dir=str(tmp_path / "o"), now=T0)
    assert res["status"] == "ACTIVE" and res["newly_frozen"] > 0 and res["rows"]
    _, chain = prereg.load_chain(frozen / prereg.FROZEN_FILE)
    assert chain["chain_ok"] and chain["lines"] == 1 + res["newly_frozen"]
    again = combos.evaluate(fake, ctx, prereg_root=frozen, out_dir=str(tmp_path / "o"), now=T0 + 60)
    assert again["newly_frozen"] == 0
    proposal = combos.tile_package_proposal(res["rows"][0], fake)
    assert proposal["status"] == "PROPOSAL_ONLY_NOT_REGISTERED"
    assert "never edits combo_pathway_config.py" in proposal["tile_package_rules"]["output"]


# ------------------------------------------------------------- weekly and cycle

def test_weekly_render_and_cycle(tmp_path, frozen):
    tape = _tape(days=4)
    rows = _rows(tape, days=3)
    out, diag = str(tmp_path / "o"), tmp_path / "diag"
    res = cycle.run(str(tmp_path), out, frozen, diag, now=T0 + 8 * 86400, tape=tape, rows=rows)
    assert res["status"] == "OK" and res["combinations"] == "GATED"
    assert res["weekly"] and res["weekly"].endswith("INDICATOR-EDGE-WEEK-1.md")
    text = (diag / "INDICATOR-EDGE-WEEK-1.md").read_text()
    assert "{{" not in text and ORACLE in text and "Pre-registration id" in text and "GATED" in text
    rep = json.loads((tmp_path / "o" / sc.REPORT_FILE).read_text())
    assert weekly.write_week(rep, out, diag, frozen_at=T0, week=1) is None             # never overwrites
    assert weekly.week_number(T0, T0 + 7 * 86400 - 1) == 1 and weekly.week_number(T0, T0 + 7 * 86400) == 2
