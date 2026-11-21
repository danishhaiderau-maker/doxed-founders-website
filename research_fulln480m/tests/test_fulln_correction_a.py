"""B2 tests (T2.*): fallback A with the ELIGIBLE-ONLY audit frame (RK 6bf51f6bf4ac (b), RK 2e6b9127bf3e (1)).
t >= 3.0 is a disclosure column only (RK c07fa806dfad (4)); no z decision (HM B13). Box synthetic data only."""
import ast
import inspect

import numpy as np
import pytest
from scipy import stats

from research_fulln480m import fulln_correction_a as fa
from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

K = pins.K_DAY_BLOCKS


def _u4(x):
    return np.asarray(x, dtype=np.uint32)


def _f8(x):
    return np.asarray(x, dtype=np.float64)


# ---------------------------------------------------------------- statistics
def test_t2_1_plain_t_sample_variance():
    r = np.array([1.0, 2.0, 4.0, -1.0, 3.5])
    n, s, q = _u4([r.size]), _f8([r.sum()]), _f8([(r * r).sum()])
    exp = r.mean() / (r.std(ddof=1) / np.sqrt(r.size))
    assert abs(fa.plain_t(n, s, q)[0] - exp) < 1e-12
    assert abs(fa.plain_var(n, s, q)[0] - r.var(ddof=1)) < 1e-12
    t = fa.plain_t(_u4([0, 1, 3]), _f8([0.0, 2.0, 3.0]), _f8([0.0, 4.0, 3.0]))  # n<2 and var==0 undefined
    assert np.isnan(t).all()


def test_t2_2_block_t_reference():
    rng = np.random.default_rng(7)
    T = 6
    n_k = rng.integers(0, 3, size=(K, T)).astype(np.uint32)
    n_k[:, 3] = 0
    n_k[5, 3] = 1  # one trade only
    n_k[:, 4] = 0
    n_k[:40, 4] = 2  # constant returns -> V = 0
    s_k = rng.normal(size=(K, T)) * n_k
    s_k[:, 4] = 2.0 * n_k[:, 4]
    s_k[5, 3] = 1.5
    t, V, kc = fa.block_t(s_k, n_k)
    for c in range(T):
        S, n = s_k[:, c].sum(), n_k[:, c].sum()
        Vr = sum((s_k[k, c] - n_k[k, c] * S / n) ** 2 for k in range(K))
        assert abs(V[c] - Vr) <= 1e-9 * max(1.0, Vr)
        assert kc[c] == int((n_k[:, c] > 0).sum())
        if n >= 2 and Vr > 0:
            assert abs(t[c] - S / np.sqrt(Vr)) < 1e-9
    assert np.isnan(t[3]) and np.isnan(t[4]) and V[4] == 0.0
    assert t.dtype == np.float64 and V.dtype == np.float64 and kc.dtype == np.dtype("<u2")


def test_t2_3_block_t_refuses_float32_and_wrong_k():
    with pytest.raises(ClosureViolation):
        fa.block_t(np.zeros((K, 2), dtype=np.float32), np.zeros((K, 2), dtype=np.uint32))
    with pytest.raises(ClosureViolation):
        fa.block_t(np.zeros((669, 2)), np.zeros((669, 2), dtype=np.uint32))
    with pytest.raises(ClosureViolation):
        fa.plain_t(np.array([3], dtype=np.int64), _f8([1.0]), _f8([1.0]))


def test_t2_4_day_sums_match():
    n_k = np.ones((K, 2), dtype=np.uint32)
    s_k = np.full((K, 2), 0.5)
    fa.assert_day_sums_match(s_k, n_k, _u4([K, K]), _f8([K * 0.5, K * 0.5]))
    with pytest.raises(ClosureViolation):
        fa.assert_day_sums_match(s_k, n_k, _u4([K, K - 1]), _f8([K * 0.5, K * 0.5]))
    with pytest.raises(ClosureViolation):
        fa.assert_day_sums_match(s_k, n_k, _u4([K, K]), _f8([K * 0.5, K * 0.5 + 1e-3]))


def test_t2_5_t_crit_student_df_kc_minus_1_no_z():
    assert round(fa.t_crit(727), 4) == 6.4468 == pins.T_CRIT_DF727
    assert round(fa.t_crit(199), 3) == 6.701
    assert round(fa.t_crit(499), 3) == 6.489
    assert abs(fa.t_crit(727) - stats.t.isf(1.0405e-10, 727)) < 1e-12
    v = fa.t_crit(np.array([29, 727, 29], dtype=np.int64))
    assert v[0] == v[2] and v[0] > v[1]
    assert round(fa.z_info_column(), 4) == 6.3552
    assert fa.t_crit(727) > fa.z_info_column()
    with pytest.raises(ClosureViolation):
        fa.t_crit(727.0)
    with pytest.raises(ClosureViolation):
        fa.t_crit(0)


def test_t2_6_no_z_in_any_decision_path():
    src = inspect.getsource(fa)
    tree = ast.parse(src)
    users = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == "norm":
                users.add(fn.name)
    assert users == {"z_info_column"}
    for name in ("t_crit", "stage2_reject", "audit_check", "audit_sample", "prescreen", "eligibility"):
        assert "z_info_column" not in inspect.getsource(getattr(fa, name)) or name == "audit_check"
    # audit_check only records z as an info column; the decision uses t_crit
    rec = _clean_audit()
    assert rec["z_info_column"] == fa.z_info_column() and rec["breach"] is False


# ---------------------------------------------------------------- eligibility
def test_t2_7_eligibility_classes_and_report():
    n = _u4([1, 2, 5, 5, 5, 0, 100])
    v = _f8([np.nan, 0.0, 3.0, 3.0, 3.0, np.nan, 2.0])
    kc = np.array([1, 30, 29, 30, 1, 0, 728], dtype=np.uint16)
    el, reasons = fa.eligibility(n, v, kc)
    assert el.tolist() == [False, False, False, True, False, False, True]
    assert reasons["N_LT_2"].tolist() == [True, False, False, False, False, True, False]
    assert reasons["V_ZERO"].tolist() == [False, True, False, False, False, False, False]
    assert reasons["KC_LT_30"].tolist() == [True, False, True, False, True, True, False]
    el2, rep = fa.eligibility_report(n, v, kc)
    assert rep["n_cells_in_N"] == 7 and rep["n_eligible"] == 2 and rep["n_ineligible"] == 5
    assert rep["by_class"] == {"KC_LT_30": 4, "N_LT_2": 2, "V_ZERO": 1}
    assert rep["by_class_disclosure_KC_LT_2"] == 3
    assert tuple(fa.INELIGIBLE_CLASSES) == ("KC_LT_30", "N_LT_2", "V_ZERO")


def test_t2_8_eligibility_nan_on_defined_stat_stops():
    with pytest.raises(ClosureViolation) as ei:
        fa.eligibility(_u4([5]), _f8([np.nan]), np.array([40], dtype=np.uint16))
    assert ei.value.exit_code == 78
    with pytest.raises(ClosureViolation):
        fa.eligibility(_u4([5]), _f8([np.inf]), np.array([40], dtype=np.uint16))
    with pytest.raises(ClosureViolation):
        fa.eligibility(_u4([5]), _f8([1.0]), np.array([40], dtype=np.int64))


# ---------------------------------------------------------------- stage 1 / 2
def test_t2_9_prescreen_needs_computed_floors():
    r = np.array([3.0, 3.0, 3.0, 3.0])
    n, s, q = _u4([4]), _f8([r.sum()]), _f8([(r * r).sum()])  # var 0 -> undefined -> fails
    with pytest.raises(ClosureViolation):
        fa.prescreen(n, s, q, None)
    with pytest.raises(ClosureViolation):
        fa.prescreen(n, s, q, np.array([1]))
    assert fa.prescreen(n, s, q, np.array([True])).tolist() == [False]
    # plain t exactly 3.0 passes (>=)
    x = np.array([1.0, -1.0] * 8)
    m = 3.0 * x.std(ddof=1) / np.sqrt(x.size)
    y = x + m
    n2, s2, q2 = _u4([y.size]), _f8([y.sum()]), _f8([(y * y).sum()])
    t = fa.plain_t(n2, s2, q2)[0]
    assert abs(t - 3.0) < 1e-9
    nb = _u4([y.size] * 2)
    assert fa.prescreen(nb, _f8([s2[0]] * 2), _f8([q2[0]] * 2), np.array([True, False])).tolist() == [t >= 3.0, False]


def test_t2_10_stage2_strict_and_eligible_only():
    c = fa.t_crit(727)
    tb = _f8([c, c + 1e-9, 99.0, 99.0])
    kc = np.array([728, 728, 29, 728], dtype=np.uint16)
    el = np.array([True, True, False, True])
    assert fa.stage2_reject(tb, kc, el).tolist() == [False, True, False, True]
    with pytest.raises(ClosureViolation):
        fa.stage2_reject(_f8([np.nan]), np.array([728], dtype=np.uint16), np.array([True]))
    assert fa.stage2_reject(_f8([np.nan]), np.array([728], dtype=np.uint16), np.array([False])).tolist() == [False]


def test_t2_11_window_major_order():
    order = fa.window_major_order()
    assert len(order) == 12 * 660
    assert order[0] == ("W01", 0) and order[659] == ("W01", 659) and order[660] == ("W02", 0)
    assert order[-1] == ("W12", 659)
    with pytest.raises(ClosureViolation):
        fa.window_major_order(("W01", "W02"))


# ---------------------------------------------------------------- audit frame / sample
def _frame(m=150_000, n_stratum=20_000, seed=3):
    idx = np.arange(0, 3 * m, 3, dtype=np.int64)[:m]
    pt = np.random.default_rng(seed).uniform(-5.0, 1.9, size=m)
    pos = np.random.default_rng(seed + 1).choice(m, size=n_stratum, replace=False)
    pt[pos] = np.random.default_rng(seed + 2).uniform(2.0, 3.0, size=n_stratum)
    pt[pos[0]] = 2.0
    return idx, pt


def test_t2_12_audit_frame_eligible_failed_only():
    failed = np.array([True, True, False, True, True])
    eligible = np.array([True, False, True, True, False])
    assert fa.audit_frame(failed, eligible).tolist() == [0, 3]
    assert fa.audit_frame(failed, eligible).dtype == np.dtype("<i8")


def test_t2_13_audit_sample_literal_procedure():
    idx, pt = _frame()
    smp = fa.audit_sample(idx, pt)
    assert smp.shape == (100_000,) and smp.dtype == np.dtype("<i8")
    assert np.unique(smp).size == 100_000
    assert np.isin(smp, idx).all()
    in_stratum = set(idx[(pt >= 2.0) & (pt < 3.0)].tolist())
    assert all(int(x) in in_stratum for x in smp[:10_000])
    # independent re-implementation of the pinned steps
    rng = np.random.Generator(np.random.PCG64(20261115))
    s1 = rng.choice(idx[(pt >= 2.0) & (pt < 3.0)], size=10_000, replace=False)
    s2 = rng.choice(np.setdiff1d(idx, s1), size=90_000, replace=False)
    assert np.array_equal(smp, np.concatenate([s1, s2]))
    assert fa.sample_sha256(smp) == fa.sample_sha256(fa.audit_sample(idx, pt))


def test_t2_14_audit_sample_never_tops_up():
    idx, pt = _frame(n_stratum=9_999)
    with pytest.raises(fa.AuditBreach):
        fa.audit_sample(idx, pt)
    idx2, pt2 = _frame(m=99_999, n_stratum=20_000)
    with pytest.raises(fa.AuditBreach):
        fa.audit_sample(idx2, pt2)
    idx3, pt3 = _frame()
    with pytest.raises(fa.AuditBreach):
        fa.audit_sample(idx3, pt3, seed=20261114)
    pt4 = pt3.copy()
    pt4[7] = np.nan
    with pytest.raises(fa.AuditBreach):
        fa.audit_sample(idx3, pt4)
    with pytest.raises(ClosureViolation):
        fa.audit_sample(idx3[::-1].copy(), pt3)


# ---------------------------------------------------------------- audit check (S1.3 rules 1-4)
def _stats(m=100_000):
    smp = np.arange(m, dtype=np.int64) * 7
    n = np.full(m, 100, dtype=np.uint32)
    pv = np.ones(m)
    vb = np.full(m, 100.0)  # R_c = (V/n)/var = 1
    tb = np.full(m, 0.5)
    kc = np.full(m, 200, dtype=np.uint16)
    el = np.ones(m, dtype=bool)
    return smp, n, pv, vb, tb, kc, el


def _check(smp, n, pv, vb, tb, kc, el, sha=None, seed=20261115, used=20261115):
    return fa.audit_check(smp, n, pv, vb, tb, kc, el, fa.sample_sha256(smp) if sha is None else sha, seed, used)


def _clean_audit():
    return _check(*_stats())


def test_t2_15_clean_audit_no_breach():
    rec = _clean_audit()
    assert rec["breach"] is False and rec["n_audited"] == 100_000
    assert fa.raise_on_breach(rec) is None


def test_t2_16_t_ge_3_is_disclosure_only():
    smp, n, pv, vb, tb, kc, el = _stats()
    tb[:500] = 3.0
    tb[500:900] = 5.9  # >= 3.0 but < t_{199} crit 6.70
    rec = _check(smp, n, pv, vb, tb, kc, el)
    assert rec["disclosure_block_t_ge_3.0"] == 900
    assert rec["breach"] is False
    fa.raise_on_breach(rec)


def test_t2_17_rule1_block_t_at_own_crit_breaches():
    smp, n, pv, vb, tb, kc, el = _stats()
    kc[11] = 30
    tb[11] = fa.t_crit(29)
    rec = _check(smp, n, pv, vb, tb, kc, el)
    assert rec["rule1_block_t_ge_crit"] == 1 and rec["breach"] is True
    with pytest.raises(fa.AuditBreach) as ei:
        fa.raise_on_breach(rec)
    assert ei.value.exit_code == 78
    # between t_{30}.isf and t_{29}.isf with K_c = 30: df must be K_c - 1 = 29 -> NOT a breach
    smp, n, pv, vb, tb, kc, el = _stats()
    kc[11] = 30
    tb[11] = 0.5 * (fa.t_crit(29) + fa.t_crit(30))
    assert _check(smp, n, pv, vb, tb, kc, el)["breach"] is False


def test_t2_18_rule2_rc_bound():
    smp, n, pv, vb, tb, kc, el = _stats()
    vb[3] = 0.2 * 100.0  # R_c 0.2 < 0.22282
    rec = _check(smp, n, pv, vb, tb, kc, el)
    assert rec["rule2_rc_lt_0.22282"] == 1 and rec["breach"]
    smp, n, pv, vb, tb, kc, el = _stats()
    vb[3] = 0.2229 * 100.0
    assert _check(smp, n, pv, vb, tb, kc, el)["breach"] is False
    assert fa.RC_BOUND == 0.22282


def test_t2_19_rule3_short_or_nonfinite():
    smp, n, pv, vb, tb, kc, el = _stats()
    tb[5] = np.nan
    assert _check(smp, n, pv, vb, tb, kc, el)["rule3_short_or_nonfinite"] is True
    smp, n, pv, vb, tb, kc, el = _stats()
    pv[9] = np.inf
    assert _check(smp, n, pv, vb, tb, kc, el)["breach"] is True
    a = _stats(99_999)
    rec = _check(*a)
    assert rec["rule3_short_or_nonfinite"] is True and rec["breach"] is True


def test_t2_20_rule4_sha_or_seed():
    a = _stats()
    assert _check(*a, sha="0" * 64)["rule4_sha_or_seed_mismatch"] is True
    assert _check(*a, used=20261114)["breach"] is True
    assert _check(*a, seed=1, used=1)["breach"] is True


def test_t2_21_ineligible_never_in_sample():
    smp, n, pv, vb, tb, kc, el = _stats()
    el[0] = False
    with pytest.raises(ClosureViolation):
        _check(smp, n, pv, vb, tb, kc, el)


def test_t2_22_frame_to_sample_end_to_end_eligible_only():
    m = 130_000
    failed = np.ones(m, dtype=bool)
    eligible = np.ones(m, dtype=bool)
    eligible[::13] = False  # 10,000 ineligible cells never enter the frame
    frame = fa.audit_frame(failed, eligible)
    assert frame.size == m - 10_000
    pt = np.where(np.arange(m) % 5 == 0, 2.5, 0.0)[frame]
    smp = fa.audit_sample(frame, pt)
    assert eligible[smp].all() and smp.size == 100_000
