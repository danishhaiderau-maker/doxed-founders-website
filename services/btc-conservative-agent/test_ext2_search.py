"""Grammar count, censor boundaries and the batched exit lattice (HM B12).

The batched first-passage lattice is checked against the per-print reference
scan on random paths; the torch backend is checked on CPU when torch is
installed (GPU is NOT tested here)."""
from pathlib import Path

import numpy as np
import pytest

from research import ext2_lattice as LT
from research import ext2_search as search
from research.ext2_costs import Ext2Stop


def test_closed_form_and_enumeration_are_181442():
    assert search.count_combos_closed_form() == 181442
    assert search.count_combos_enumerated() == 181442
    assert search.count_combos() == 181442


def test_censor_boundaries_are_the_fold_test_ends():
    from research.ext2_calendar import FROZEN
    assert search.default_censor_boundaries_ms() == tuple(f.test_censor_ms() for f in FROZEN.folds)
    b = search.default_censor_boundaries_ms()
    assert search.active_censor_mts(b[0] - 1, b) == b[0]
    assert search.active_censor_mts(b[0], b) == b[1]


def _random_case(seed, n=3000, events=150):
    rng = np.random.default_rng(seed)
    mts = np.cumsum(rng.integers(1, 9_000, n)).astype(np.int64)
    price = 60000.0 * np.exp(np.cumsum(rng.standard_t(3, n) * 4e-4))
    pick = rng.integers(0, n - 50, events)
    ev = {"mts": mts[pick] + 1, "price": price[pick] * (1 + rng.normal(0, 1e-4, events)),
          "side": rng.choice([-1.0, 1.0], events)}
    bnd = np.where(rng.random(events) < 0.5, mts[np.minimum(pick + rng.integers(5, 2000, events), n - 1)],
                   mts[-1] + 10**9)
    ev["boundary"] = bnd.astype(np.int64)
    thesis_idx = np.minimum(pick + rng.integers(1, 3000, events), n)
    return mts, price, ev, thesis_idx


def _check_parity(backend, seed):
    mts, price, ev, th = _random_case(seed)
    base = LT.compute_base(LT.PrintPath(mts, price, backend), ev["mts"], ev["price"], ev["side"], ev["boundary"],
                           chunk=64)
    n = mts.size
    bad = 0
    for e in range(ev["mts"].size):
        thesis = np.zeros(n, dtype=bool)
        thesis[th[e]:] = True
        ref = LT.reference_scan(mts, price, thesis, entry_price=float(ev["price"][e]), entry_mts=int(ev["mts"][e]),
                                side=float(ev["side"][e]), boundary=int(ev["boundary"][e]))
        x = np.arange(54)
        res = LT.resolve(base, np.full(54, e), x, np.full(54, th[e]), mts, price, np.full(54, ev["mts"][e]),
                         np.full(54, ev["price"][e]), np.full(54, ev["side"][e]))
        bad += int(np.sum(res["code"] != ref["code"]))
        ok = ref["code"] != 7
        bad += int(np.sum(res["exit_idx"][ok] != ref["exit_idx"][ok]))
        assert np.allclose(res["gross_bp"][ok], ref["gross_bp"][ok], atol=1e-9, rtol=0)
    assert bad == 0
    return base


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_numpy_lattice_matches_the_per_print_reference(seed):
    base = _check_parity(LT.NumpyBackend(), seed)
    assert base["dtype"] == "float64" and base["chunks"] >= 2


def test_torch_cpu_lattice_matches_numpy_bitwise_on_indices():
    pytest.importorskip("torch")
    backend = LT.make_backend("torch", "cpu")
    base = _check_parity(backend, 4)
    assert base["kernel"] == "torch" and base["dtype"] == "float64"


def test_gap_through_liquidation_books_the_worse_print():
    mts = np.array([1_000, 2_000, 3_000], dtype=np.int64)
    price = np.array([100.0, 99.0, 100.5])
    thesis = np.zeros(3, dtype=bool)
    ref = LT.reference_scan(mts, price, thesis, entry_price=100.0, entry_mts=500, side=1.0, boundary=None)
    assert (ref["code"] == 5).all()
    assert np.allclose(ref["exit_price"], 99.0)
    base = LT.compute_base(LT.PrintPath(mts, price, LT.NumpyBackend()), np.array([500]), np.array([100.0]),
                           np.array([1.0]), np.array([10**15]))
    res = LT.resolve(base, np.zeros(54, dtype=int), np.arange(54), np.full(54, 3), mts, price,
                     np.full(54, 500), np.full(54, 100.0), np.full(54, 1.0))
    assert (res["code"] == 5).all() and np.allclose(res["exit_price"], 99.0)
    mild = np.array([100.0, 99.8, 100.5])
    base = LT.compute_base(LT.PrintPath(mts, mild, LT.NumpyBackend()), np.array([500]), np.array([100.0]),
                           np.array([1.0]), np.array([10**15]))
    res = LT.resolve(base, np.zeros(54, dtype=int), np.arange(54), np.full(54, 3), mts, mild,
                     np.full(54, 500), np.full(54, 100.0), np.full(54, 1.0))
    assert set(res["code"].tolist()) <= {4, 3, 8}  # data ends before the boundary: path end (8)
    assert 5 not in res["code"]


def test_path_end_is_produced_when_the_data_ends_before_the_boundary():  # HM R2 deec587f path_end
    mts = np.array([1_000, 2_000, 3_000], dtype=np.int64)
    price = np.array([100.0, 100.01, 100.02])
    for boundary, code in ((2_500, 6), (10**15, 8)):
        base = LT.compute_base(LT.PrintPath(mts, price, LT.NumpyBackend()), np.array([500]), np.array([100.0]),
                               np.array([1.0]), np.array([boundary]))
        res = LT.resolve(base, np.zeros(54, dtype=int), np.arange(54), np.full(54, 3), mts, price,
                         np.full(54, 500), np.full(54, 100.0), np.full(54, 1.0))
        ref = LT.reference_scan(mts, price, np.zeros(3, dtype=bool), entry_price=100.0, entry_mts=500, side=1.0,
                                boundary=boundary)
        late = ref["code"] == code
        assert late.any() and (res["code"][late] == code).all()


def test_censor_closes_at_the_last_print_before_the_boundary_and_never_reads_past_it():
    mts = np.array([1_000, 2_000, 3_000, 4_000], dtype=np.int64)
    price = np.array([100.0, 100.01, 100.02, 50.0])
    base = LT.compute_base(LT.PrintPath(mts, price, LT.NumpyBackend()), np.array([500]), np.array([100.0]),
                           np.array([1.0]), np.array([3_500]))
    res = LT.resolve(base, np.zeros(54, dtype=int), np.arange(54), np.full(54, 4), mts, price,
                     np.full(54, 500), np.full(54, 100.0), np.full(54, 1.0))
    assert (res["code"] == 6).all() and (res["exit_idx"] == 2).all()
    unbounded = LT.resolve(base, np.zeros(54, dtype=int), np.arange(54), np.full(54, 4), mts, price,
                           np.full(54, 500), np.full(54, 100.0), np.full(54, 1.0),
                           has_boundary=np.zeros(54, dtype=bool))
    assert (unbounded["code"] == 8).all()
    dropped = LT.compute_base(LT.PrintPath(mts, price, LT.NumpyBackend()), np.array([3_200]),
                              np.array([100.0]), np.array([1.0]), np.array([3_500]))
    res = LT.resolve(dropped, np.zeros(54, dtype=int), np.arange(54), np.full(54, 4), mts, price,
                     np.full(54, 3_200), np.full(54, 100.0), np.full(54, 1.0))
    assert (res["code"] == 7).all()


def test_chunk_over_the_cap_stops():
    mts, price, ev, _ = _random_case(5)
    with pytest.raises(Ext2Stop, match="STOP_GPU_CHUNK_CAP"):
        LT.compute_base(LT.PrintPath(mts, price, LT.NumpyBackend()), ev["mts"], ev["price"], ev["side"],
                        ev["boundary"], chunk=64, chunk_cap_sec=0.0)


def test_numpy_backend_refuses_a_gpu_device_and_bad_kernels_stop():
    with pytest.raises(Ext2Stop, match="STOP_KERNEL"):
        LT.make_backend("numpy", "cuda")
    with pytest.raises(Ext2Stop, match="STOP_KERNEL"):
        LT.make_backend("jax", "cpu")


def test_bot_does_not_import_ext2():
    text = (Path(__file__).parent / "bot.py").read_text(encoding="utf-8", errors="ignore")
    assert "ext2" not in text.lower()
