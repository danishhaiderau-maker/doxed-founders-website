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
