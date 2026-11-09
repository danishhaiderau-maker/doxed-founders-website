"""End-to-end A19 path on synthetic data with a small calendar (CPU numpy backend).

Synthetic data only; never real market data. A real run refuses this calendar."""
import numpy as np
import pytest

from ext2_test_data import small_calendar, synth_trades
from research import ext2_lattice as LT
from research import ext2_loader as L
from research import ext2_pipeline as P


@pytest.fixture(scope="module")
def run():
    cal = small_calendar()
    tr = synth_trades(cal.days(), seed=7)
    grid = L.build_grid(tr, cal.days())
    ctx = P.prepare(grid, tr, cal)
    out = P.run_search(ctx, LT.NumpyBackend())
    return cal, tr, ctx, out


def test_cell_enumeration_is_unique_and_complete():  # old HM N9
    cells = P.enumerate_cells()
    assert len(cells) == P.N_CELLS == 181442
    assert len(set(cells)) == len(cells)
    assert P.N_KEYS == 3360 and P.N_GRID_CELLS == 3360 * 54
    for key in (0, 1, 777, 3359):
        assert P.key_id(*P.key_parts(key)) == key
    assert len(P.MEMBERS) == 21 and len(set(P.MEMBERS)) == 21


def test_gates_cover_all_cells_and_untested_cells_have_p_one(run):  # B4, B11
    cal, _, _, out = run
    g = out["gates"]
    assert g["p_hac"].shape == (P.N_CELLS,)
    # 8 test days < T_min = 20, so every cell is untested with p = 1 (never p = 0).
    assert not g["tested"].any() and (g["p_hac"] == 1.0).all()
    assert not g["bh"].any() and len(g["lead_ids"]) == 0
    assert set(g["gate_pass_counts"]) >= {f"g{i}" for i in range(1, 12)}
    table = P.cells_table(g, cal)
    assert table.shape == (P.N_CELLS,)


def test_every_trade_respects_one_position_and_the_cooldown(run):  # A9, N2
    cal, _, ctx, out = run
    seg = out["segments"][0]
    cells = [0, 54 * 5 + 7, 54 * 1234 + 53, 54 * 3000 + 20]
    rep = P.run_chains(ctx, seg, out["base"], [(c // 54, c % 54) for c in cells], record=True)
    tr = rep["trades"]
    origin = ctx["origin_mts"]
    checked = 0
    for c in cells:
        sel = tr["cell"] == c
        bars = seg["bars"][tr["u"][sel].astype(int)]
        exit_bar = (tr["exit_mts"][sel] - origin) // 60_000
        assert np.all(bars[1:] >= exit_bar[:-1] + 1)
        assert np.all(tr["entry_mts"][sel][1:] > tr["exit_mts"][sel][:-1])
        checked += int(sel.sum())
    assert checked > 0


def test_accumulator_matches_a_direct_replay(run):
    cal, _, ctx, out = run
    acc = out["acc"]
    for cell in (54 * 5 + 7, 54 * 2000 + 1):
        rep = P.replay_cells(ctx, out["segments"], out["base"], [cell])[cell]
        n = 0 if rep is None else rep["net"].size
        assert acc.n[cell] == n
        if n:
            assert acc.net[cell] == pytest.approx(rep["net"].sum(), rel=1e-9, abs=1e-9)


def test_entries_are_causal_and_lookahead_fixtures_pass(run):  # B8, C3b, C3c
    cal, tr, ctx, out = run
    c3c = P.fixture_c3c(out["segments"])
    assert c3c["pass"] and c3c["entries_checked"] > 0
    tau = cal.folds[0].test_censor_ms() - 3 * 3_600_000
    c3b = P.fixture_c3b(lambda t: L.build_grid(t, cal.days()), tr, ctx, tau, cal)
    assert c3b["pass"], c3b["mismatches"]


def test_c3b_detects_a_planted_lookahead(run, monkeypatch):
    cal, tr, ctx, _ = run
    from research import ext2_features as F
    real = F.trailing_percentiles

    def leaky(values, window, probs=F.ALL_Q):  # M21-style: window includes bar t
        shifted = np.concatenate([np.asarray(values, dtype=float)[1:], [np.nan]])
        return real(shifted, window, probs)
    monkeypatch.setattr(P, "trailing_percentiles", leaky)
    tau = cal.folds[0].test_censor_ms() - 3 * 3_600_000
    c3b = P.fixture_c3b(lambda t: L.build_grid(t, cal.days()), tr, ctx, tau, cal)
    assert not c3b["pass"]


def test_path_end_is_counted_per_cell_and_matches_the_total(run):  # GS v4 CL17/CL29 (report-only)
    cal, tr, ctx, out = run
    per_cell = out["gates"]["path_end_count"]
    assert per_cell.shape == (P.N_CELLS,)
    assert int(per_cell[:P.N_GRID_CELLS].sum()) == out["acc"].counts["path_end_count"]
    table = P.cells_table(out["gates"], cal)
    assert (table["path_end_count"] == per_cell).all()
    basis = P.day_basis_receipt(out["gates"], cal)
    assert basis["g6_hac_t"]["basis"].startswith("trade_days") and basis["g6_hac_t"]["hac_lag_basis"] == "trade_days"
    assert basis["g7_white_rc"]["T"] == len(cal.test_days())


def test_canary_docstring_matches_the_flip():  # GS v4 G5 (2)
    doc = P.canary.__doc__
    assert "INDEPENDENTLY" in doc and "SANITY CHECK, not a calibration" in doc
    assert "serial dependence" in doc and "skew" in doc and "dependence structure across cells is kept" not in doc


def test_canary_and_counts_are_reported(run):
    _, _, _, out = run
    can = P.canary(out["gates"])
    assert can["pass"] and can["canary_leads"] == 0 and can["gates_applied"] == ["g2", "g6", "g7", "g8"]
    counts = out["acc"].counts
    assert counts["trades_oof"] > 0 and counts["liquidation_count"] >= 0
    assert out["base"]["dtype"] == "float64"


def test_dry_check_reports_per_feature_and_fold_coverage(run):  # N4, Boss ruling
    cal, _, ctx, _ = run
    rep = P.dry_check(ctx, cal)
    assert rep["reads_pnl"] is False
    assert len(rep["per_member"]) == 21
    assert set(rep["per_member"]["ret_L"]["240"]["by_fold"]) == {"F1", "F2", "F3", "F4"}
    assert rep["undefined_stat_bars"] >= 0 and rep["first_sigma_utc"] is not None
    assert rep["structurally_untestable_cells"] % 2160 == 0


def test_thesis_index_matches_the_closed_bar_reference(run):  # B8 (kills M34)
    cal, tr, ctx, _ = run
    from research import ext2_features as F
    mts = tr["mts"]
    close_mts = ctx["features"]["close_mts"]
    rng = np.random.default_rng(0)
    checked = 0
    for fi in range(6):
        for li in (0, 2):
            vals = ctx["features"]["features"][F.FEATURES[fi]][cal.lookbacks[li]]
            p50 = ctx["pct"][(fi, li)][:, 10]
            for di, d in enumerate((1, -1)):
                for t in rng.integers(cal.percentile_bars + 10, ctx["n"] - 600, 4):
                    t = int(t)
                    lo = int(np.searchsorted(mts, close_mts[t]))
                    hi = int(np.searchsorted(mts, close_mts[t] + 300 * 60_000))
                    flags = F.thesis_cross_flags(vals, p50, d, mts[lo:hi], close_mts, ctx["origin_mts"], t)
                    got = int(ctx["thesis_idx"][fi, li, di, t])
                    if flags.any():
                        assert got == lo + int(np.argmax(flags))
                        checked += 1
                    else:
                        assert got >= hi
    assert checked > 10


def test_stale_entries_past_the_60s_cap_are_no_trade(run):  # N3 in the pipeline (kills M35)
    _, _, _, out = run
    stale = 0
    for seg in out["segments"]:
        ent = seg["entries"]
        assert np.all(ent["latency_ms"][ent["valid"]] <= 60_000)
        assert np.all(ent["latency_ms"][ent["valid"]] >= 0)
        assert not np.any(ent["valid"] & ent["stale"])
        stale += int(ent["stale"].sum())
    assert stale > 0 and out["stale_entry_bars"] == stale


# ---- Push 4: tests that kill HM's surviving mutants H06b-H16, H25 (RK D2 hook) ----
from research import ext2_rules as RU  # noqa: E402
from research.ext2_calendar import FROZEN  # noqa: E402


def _gate_world(monkeypatch):
    """Seven hand-built grid cells on the FROZEN calendar (43 test days). Cell 'good'
    passes every gate; each other cell fails exactly ONE gate. Heavy statistics that
    are not under test (White RC panel, trade replay) are stubbed per cell."""
    days = len(FROZEN.test_days())
    acc = P.Accumulator(days, len(FROZEN.folds))
    rng = np.random.default_rng(5)
    names = ("good", "g1", "g6", "g7", "g8", "g9", "g10")
    cells = {name: 1000 + 54 * k for k, name in enumerate(names)}
    marker = {}
    for k, (name, c) in enumerate(cells.items()):
        series = 10.0 + 0.5 * rng.standard_normal(days)
        if name == "g6":
            series = 0.02 + 1.0 * rng.standard_normal(days)
            series[0] = abs(series[0]) + 0.5
        series[0] = round(float(series[0]), 3) + 1e-6 * (k + 1)
        marker[round(float(series[0]), 9)] = name
        n = 100
        rt = 2.0
        gross_mean = 1.5 * rt if name == "g1" else 5.0 * rt
        acc.n[c] = n
        acc.gross[c] = n * gross_mean
        acc.rt[c] = n * rt
        acc.net[c] = acc.stress[c] = acc.tick[c] = float(series.sum())
        acc.fold_net[c, :] = 1.0
        acc.daily[c, :] = series
        acc.daily_n[c, :] = 2

    def who(series):
        return marker.get(round(float(np.asarray(series)[0]), 9))
    real_dsr = RU.deflated_sharpe
    real_conc = RU.concentration
    monkeypatch.setattr(RU, "expected_max_sharpe", lambda var, trials=RU.COMBOS_SEARCHED: 0.0)  # SR0 not under test
    monkeypatch.setattr(RU, "deflated_sharpe", lambda s, sr0: 0.10 if who(s) == "g8" else real_dsr(s, sr0))
    monkeypatch.setattr(RU, "concentration", lambda d, w: {"max_day_share": 0.9, "max_week_share": 0.9}
                        if who(d) == "g10" else real_conc(d, w))

    def fake_rc(panel, tested, **kw):
        p = np.ones(panel.shape[0])
        p[np.asarray(tested, dtype=bool)] = 0.001
        p[cells["g7"]] = 0.5
        return {"p": p, "rc_p_best": 0.001, "best_cell": cells["good"]}
    monkeypatch.setattr(RU, "white_rc_pvalues", fake_rc)
    monkeypatch.setattr(P, "replay_cells", lambda *a, **k: {})
    monkeypatch.setattr(P, "trade_level_metrics", lambda tr, cell, cal: {
        "n_eff": 5 if cell == cells["g9"] else 100, "ci_low": 1.0, "ci_high": 2.0, "median_hold_s": 900.0})
    packages = {s: {"trades": None, "legs_min_oof_ok": False, "legs": {}} for s in ("S1", "S2")}
    gates = P.evaluate_gates({"calendar": FROZEN}, [], None, acc, packages)
    return cells, gates


def test_gate_wiring_each_single_gate_failure_blocks_a_lead(monkeypatch):  # kills H06b, H07-H10
    cells, gates = _gate_world(monkeypatch)
    assert gates["lead_ids"] == [cells["good"]]
    t = gates["tested"]
    assert all(t[c] for c in cells.values())
    g = {name: c for name, c in cells.items()}
    assert gates["bh"][g["good"]] and not gates["bh"][g["g6"]]                 # g6 is the only BH miss
    assert gates["white_rc_p"][g["g7"]] > RU.WHITE_RC_P_MAX                     # g7 (H06b)
    assert gates["dsr"][g["g8"]] < RU.DSR_MIN <= gates["dsr"][g["good"]]       # g8 (H07)
    assert gates["n_eff"][g["g9"]] < RU.NEFF_LEAD_FLOOR <= gates["n_eff"][g["good"]]  # g9 (H08)
    assert gates["max_day_share"][g["g10"]] > RU.MAX_DAY_SHARE                  # g10 (H09)
    assert 1.0 * 2.0 <= gates["gross_mean"][g["g1"]] < 2.0 * 2.0                # g1 (H10): 1x <= gross < 2x RT
    assert not gates["is_lead"][[g[n] for n in ("g1", "g6", "g7", "g8", "g9", "g10")]].any()


def _switch_acc():
    acc = P.Accumulator(len(FROZEN.test_days()), len(FROZEN.folds))
    bull = [c for c in range(0, 54 * 40) if (c // 54) % P.N_G == 1][:3]
    for c, v in zip(bull, (-5.0, -1.0, -3.0)):
        acc.val_n[c, 0] = 25
        acc.val_net[c, 0] = v
        acc.val_n[c, 1] = 25
        acc.val_net[c, 1] = -v
    return acc, bull


def test_s2_stands_aside_when_the_best_inner_val_net_is_not_positive(monkeypatch):  # kills H11
    monkeypatch.setattr(P, "replay_cells", lambda *a, **k: {})
    acc, bull = _switch_acc()
    pk = P.switching({"calendar": FROZEN}, [], None, acc)
    f1, f2 = FROZEN.folds[0].name, FROZEN.folds[1].name
    assert pk["S1"]["legs"][f"{f1}:BULL"] == bull[1]           # best of negatives (S1 always picks)
    assert pk["S2"]["legs"][f"{f1}:BULL"] is None              # S2 stands aside (net <= 0)
    assert pk["S1"]["legs"][f"{f2}:BULL"] == pk["S2"]["legs"][f"{f2}:BULL"] == bull[0]
    assert f"{f1}:BULL" in pk["S2"]["not_tested_regimes"]


def _canary_gates(cells=10, days=43):
    rng = np.random.default_rng(2)
    return {"daily": 5.0 + rng.standard_normal((cells, days)), "daily_n": np.ones((cells, days), dtype=np.int32),
            "tested": np.ones(cells, dtype=bool)}


def test_canary_fails_when_a_canary_lead_survives_and_passes_otherwise(monkeypatch):  # kills H12
    monkeypatch.setattr(RU, "white_rc_gate", lambda p: np.ones(len(p), dtype=bool))
    monkeypatch.setattr(RU, "deflated_sharpe", lambda s, sr0: 1.0)
    monkeypatch.setattr(RU, "bh_reject", lambda p: np.ones(len(p), dtype=bool))
    out = P.canary(_canary_gates())
    assert out["canary_leads"] >= 1 and out["pass"] is False
    assert out["gate6_disclosure"] == RU.GATE6_DISCLOSURE
    monkeypatch.setattr(RU, "bh_reject", lambda p: np.zeros(len(p), dtype=bool))
    out = P.canary(_canary_gates())
    assert out["canary_leads"] == 0 and out["pass"] is True


def _tiny_ctx(n=8):
    trig = {}
    for f in range(P.N_SINGLE):
        for li in range(P.N_L):
            trig[(f, li)] = np.zeros((n, P.N_P), dtype=np.int8)
    return {"regimes": {"code": np.asarray([1, 2, 3, 1, 0, 1, 2, 3][:n], dtype=np.int8)}, "trig": trig}


def test_regime_cells_only_take_signals_in_their_regime():  # kills H13
    ctx = _tiny_ctx()
    ctx["trig"][(0, 0)][:, 0] = 1
    seg = {"bars": np.arange(8), "entries": {"valid": np.ones(8, dtype=bool)}}
    for gi, expect in ((0, list(range(8))), (1, [0, 3, 5]), (2, [1, 6]), (3, [2, 7])):
        cand = P._segment_candidates(ctx, seg, [P.key_id(0, 0, 0, 0, gi)])
        assert cand["u"].tolist() == expect, gi


def test_pair_fires_only_when_both_features_agree_on_the_side():  # kills H14
    ctx = _tiny_ctx(4)
    ctx["trig"][(0, 1)][:, 2] = [1, 1, -1, 0]
    ctx["trig"][(1, 1)][:, 2] = [1, -1, -1, 1]
    pair = P.MEMBERS.index((0, 1))
    assert P.member_directions(ctx, pair, 1, 2, np.arange(4)).tolist() == [1, 0, -1, 0]


def test_entry_at_or_after_the_censor_boundary_is_not_a_trade():  # kills H15
    close = np.asarray([10_000, 70_000, 130_000], dtype=np.int64)
    mts = close + 1_000
    ctx = {"trades": {"mts": mts, "price": np.full(3, 100.0)}, "features": {"close_mts": close}}
    seg = {"bars": np.arange(3), "boundary_mts": int(mts[1])}
    ent = P.segment_entries(ctx, seg)
    assert ent["valid"].tolist() == [True, False, False]   # strictly before; equal is censored


def test_exit_never_uses_a_print_in_the_entry_millisecond():  # H16 (same-ms prints)
    # Entry print at 1000 ms; a second print in the SAME ms crosses every TP level. The
    # exit scan starts strictly after the entry ms (right=True), so it must not fire there.
    mts = np.asarray([1_000, 1_000, 2_000, 3_000, 4_000_000], dtype=np.int64)
    price = np.asarray([100.0, 101.0, 100.0, 100.0, 100.0])
    base = LT.compute_base(LT.PrintPath(mts, price, LT.NumpyBackend()), np.asarray([1_000]),
                           np.asarray([100.0]), np.asarray([1.0]), np.asarray([10**15]))
    assert int(base["start"][0]) == 2
    res = LT.resolve(base, np.zeros(54, dtype=int), np.arange(54), np.full(54, 5), mts, price,
                     np.full(54, 1_000), np.full(54, 100.0), np.full(54, 1.0))
    assert (res["exit_mts"] > 1_000).all() and (res["hold_ms"] > 0).all()
    assert 3 not in res["code"].tolist()    # no take-profit from the same-ms print


def _thesis_world():
    nb = 2
    th = np.full((P.N_SINGLE, P.N_L, 2, nb), 99, dtype=np.int64)
    th[0, 0, 0] = [9, 3]   # feature 0 (lowest id)
    th[1, 0, 0] = [5, 7]   # feature 1
    pair = P.MEMBERS.index((0, 1))
    member = np.asarray([pair, pair])
    z = np.zeros(nb, dtype=np.int64)
    return th, member, z, np.arange(nb)


@pytest.mark.parametrize("rule,expect", [("lowest_id", [9, 3]), ("either", [5, 3]), ("both", [9, 7])])
def test_pair_thesis_rule_hook_each_choice(rule, expect):  # RK D2; kills H25 for each choice
    th, member, z, bars = _thesis_world()
    assert P.thesis_print_index(th, member, z, z, bars, rule).tolist() == expect
    single = np.asarray([1, 1])
    assert P.thesis_print_index(th, single, z, z, bars, rule).tolist() == [5, 7]  # singles: own feature


def test_pair_thesis_rule_default_and_unknown():
    assert P.PAIR_THESIS_RULE == "lowest_id" and P.PAIR_THESIS_RULES == ("lowest_id", "either", "both")
    th, member, z, bars = _thesis_world()
    assert P.thesis_print_index(th, member, z, z, bars).tolist() == [9, 3]
    with pytest.raises(P.Ext2Stop, match="STOP_PAIR_THESIS_RULE"):
        P.thesis_print_index(th, member, z, z, bars, "last")


def test_run_chains_uses_the_pair_rule_from_the_context(run):  # hook is wired into the chains
    cal, tr, ctx, out = run
    seg = out["segments"][0]
    pair_keys = [P.key_id(P.MEMBERS.index((0, 1)), li, pi, 0, 0) for li in range(P.N_L) for pi in range(P.N_P)]
    chains = [(k, x) for k in pair_keys for x in range(54) if LT.EXIT_TABLE["tc"][x] > 0]
    got = {}
    for rule in P.PAIR_THESIS_RULES:
        c2 = dict(ctx)
        c2["pair_thesis_rule"] = rule
        res = P.run_chains(c2, seg, out["base"], chains, record=True)["trades"]
        got[rule] = (res["code"] == 2).sum(), res["exit_mts"].sum()
    assert len(set(got.values())) >= 2, got


def test_c3b_lattice_fixture_flags_a_leaky_lattice_and_passes_the_real_one(run):
    cal, tr, ctx, out = run
    tau = int(cal.folds[0].test_censor_ms()) - 3 * 3_600_000
    real = P.fixture_c3b_lattice_on_run(ctx, out["segments"], LT.NumpyBackend(), tau)
    assert real["pass"] is True and real["events_checked"] > 0
    ent = out["segments"][0]["entries"]
    pick = np.flatnonzero(ent["valid"] & (ent["entry_mts"] < tau))[:64]
    ev = {"mts": ent["entry_mts"][pick], "price": ent["entry_price"][pick], "side": np.ones(pick.size),
          "boundary": np.full(pick.size, int(out["segments"][0]["boundary_mts"]), dtype=np.int64)}

    last_unperturbed = float(tr["price"][-1])

    def leaky(m, pr, e):  # peeks at the final (post-tau) print price when choosing every take-profit index
        b = LT.compute_base(LT.PrintPath(m, pr, LT.NumpyBackend()), e["mts"], e["price"], e["side"], e["boundary"])
        b["tp"] = b["tp"] + int(pr[-1] > last_unperturbed * 1.005)
        return b
    assert P.fixture_c3b_lattice(tr["mts"], tr["price"], leaky, ev, tau)["pass"] is False


def test_path_end_counter_is_not_dead(run):
    cal, tr, ctx, out = run
    assert "path_end_count" in out["acc"].counts
