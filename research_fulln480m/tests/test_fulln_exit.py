"""Stage A exit fixtures for B-R63X (card r2.1 a312 s7.2 / s4 / s6). Live OFF. Gross only.

Collects as tests/test_fulln_exit.py (s8). Imports production fulln_exit inside helpers so a
missing module is a per-test ERROR (RED) rather than a collection failure. The oracle is never
imported or edited. Stage B / TIERET / F-H-* price fixtures are skipped with reason.
"""
import hashlib
import os
import subprocess
import textwrap
import sys
from pathlib import Path

import numpy as np
import pytest

from research_fulln480m.fulln_closure import ClosureViolation
from research_fulln480m.fulln_decide import (
    FILLED, NO_FILL, UNFRESH, EMBARGO, BLOCKED, NO_TE, decide_core, pins_config,
)
from research_fulln480m.fulln_exit_pins import (
    TP_BP, SL_BP, TR_BP, TH_KIND, N_EXITS, LIQ_FLOOR_BP, LIQ_FILL_BP,
    G3_ORDER, EXIT_CLOCK, N_FULL, HORIZONS_12, R63H_CARD_SHA256, R63I_CARD_SHA256,
    R63H_ADOPTED_SHA256, DECIDE_SHA256, DECIDE_PINS_SHA256, CLOSURE_SHA256,
)
from research_fulln480m.tests.exit_fixtures import (
    DAY_S, DIRS, HD, HORIZONS, copy_micro, empty_micro, ext_for_g,
    ext_fire_greatest_le_floor, ext_noliq_least_gt_floor, flat_window,
    k_max_i12, last_for_r, last_meeting_r, legal_Tprimes, legal_ks, poke_ext, poke_ext_value,
    poke_last, poke_last_value, production_r, production_g_adv, set_bar, two_day_axis,
    ulp_neighbourhood, repr_walk_micro, H5_TR_WITNESSES,
)
from research_fulln480m.tests.ref_ext2_exit_path import PARITY_LIQ_METHOD

P0 = np.float64(100.0)
HERE = Path(__file__).resolve().parent
PKG = HERE.parent
# Killers for MG-LIQ-NET435 (E-G2): -50.0 < g_adv <= -43.5, inclusive at -43.5.
NET435_KILLERS = (np.float64(-49.99), np.float64(-45.0), np.float64(-43.5))
# g64 short edge (MG-LIQ-F32CMP / F-G-LIQEDGE (b)): p0=80000, HIGH=80400.
G64_P0 = np.float64(80000.0)
G64_HIGH = np.float64(80400.0)
TIERET_SKIP = (
    "BLOCKED until D-LIQ-F32 F1+F2 pass the code-to-master gate and HM files its "
    "AC-1 re-run receipt (card s4.3 / s7.3; RK dc1d L20-L23; Boss CLEAR L22). NOT RUN."
)
STAGE_B_SKIP = (
    "Stage B (fulln_exit_stageb.py) is out of this Stage A subset: H.1-H.4 prices, "
    "UNPRICED_WORST_LIQ, G.5 n_b/n_c, F-G-HOLDWORST / F-G-HOLD30-STOP. NOT RUN here."
)
REF_SHA = "aca6b3a5db1ae1778c75ca02866653bde1b6aa30a6fbb73fccbcea4335b78c49"


def _ex():
    from research_fulln480m import fulln_exit as m
    return m


def _stop_or_raise(exc):
    assert isinstance(exc, ClosureViolation), type(exc)
    msg = str(exc)
    return msg


def _first_mismatch(name, got, expect, *, equal_nan=False):
    """Short first-mismatch text (RK b3bf (7)); no 768-wide list diff."""
    if got.shape != expect.shape:
        return "%s shape got=%r expect=%r" % (name, got.shape, expect.shape)
    if equal_nan:
        mism = ~((got == expect) | (np.isnan(got) & np.isnan(expect)))
    else:
        mism = np.asarray(got != expect)
    if not np.any(mism):
        return "%s differ (no index located)" % name
    i = int(np.flatnonzero(mism)[0])
    return "%s first mismatch at %d: got=%r expect=%r" % (name, i, got[i], expect[i])


def _assert_exact(name, got, expect, dtype, *, equal_nan=False):
    """Exact compare: dtype checked, np.array_equal, NaN explicit, no tolerance."""
    assert got.dtype == dtype, "%s dtype got=%r expect=%r" % (name, got.dtype, dtype)
    assert got.shape == expect.shape, "%s shape got=%r expect=%r" % (name, got.shape, expect.shape)
    if not np.array_equal(got, expect, equal_nan=equal_nan):
        msg = _first_mismatch(name, got, expect, equal_nan=equal_nan)
        print(msg)
        assert False, msg


def _all_liq(out, k_expect):
    assert out["label"].shape == (N_EXITS,)
    expect_lab = np.empty(N_EXITS, dtype=object)
    expect_lab[:] = "LIQ"
    _assert_exact("label", out["label"], expect_lab, object)
    expect_k = np.full(N_EXITS, np.int32(k_expect), dtype=np.int32)
    _assert_exact("k", out["k"], expect_k, np.int32)
    expect_g = np.full(N_EXITS, np.float64(LIQ_FILL_BP), dtype=np.float64)
    _assert_exact("g", out["g"], expect_g, np.float64)
    assert int(out["liq_count"]) == N_EXITS
    assert "exit_valid_mask" not in out


def _all_time(out, h):
    expect_lab = np.empty(N_EXITS, dtype=object)
    expect_lab[:] = "TIME"
    _assert_exact("label", out["label"], expect_lab, object)
    expect_k = np.full(N_EXITS, np.int32(h - 1), dtype=np.int32)
    _assert_exact("k", out["k"], expect_k, np.int32)
    assert out["g"].dtype == np.float64
    # NaN handled explicitly (every booked TIME g is NaN, never 0.0); no tolerance.
    if not np.all(np.isnan(out["g"])):
        expect_g = np.full(N_EXITS, np.nan, dtype=np.float64)
        msg = _first_mismatch("g", out["g"], expect_g, equal_nan=True)
        print(msg)
        assert False, msg
    assert not np.any(out["g"] == 0.0)
    assert int(out["liq_count"]) == 0


def _core(m, t_e, direction, h, **kw):
    return _ex().exit_core(m, int(t_e), int(direction), int(h), **kw)


# TESTFIX r1: realised-r reprs at p0=100 (production expression, float64). The ulp walk lands on
# r = +/-5.00000000000167 (smallest meeting LAST) and its one-ulp neighbour on +/-4.999999999999449.
R5_HIT = "5.00000000000167"
R5_MISS = "4.999999999999449"
R5_SL_HIT = "-5.00000000000167"
R5_SL_MISS = "-4.999999999999449"
R10_TR_HIT = "9.999999999998899"   # card L599 (realised TR hit r at h60 XDAY)
P0OPEN_RWRONG = {1: "1.9994001799461536", -1: "2.000600180055434"}  # r if p0 were LAST[t_e] (+3 bp)


def _repr(x):
    return repr(float(x))


def _walk_checked(direction, thr, hit_repr, miss_repr):
    """Walk the plant and assert (and print) the realised hit and neighbour reprs."""
    last_hit, r_hit, last_miss, r_miss = last_meeting_r(P0, direction, thr)
    print("walk d=%s thr=%r last_hit=%s r_hit=%s last_miss=%s r_miss=%s" % (
        direction, thr, _repr(last_hit), _repr(r_hit), _repr(last_miss), _repr(r_miss)))
    assert r_hit.dtype == np.float64 and r_miss.dtype == np.float64
    assert _repr(r_hit) == hit_repr, _repr(r_hit)
    assert _repr(r_miss) == miss_repr, _repr(r_miss)
    return last_hit, r_hit, last_miss, r_miss


def _assert_g_is(out, x, r_hit, hit_repr):
    """Booked g at column x is exactly the realised r (dtype checked, repr printed and asserted)."""
    assert out["g"].dtype == np.float64
    g = out["g"][x]
    print("col %d booked g repr=%s" % (x, _repr(g)))
    assert _repr(g) == hit_repr, _repr(g)
    assert g == r_hit


def _assert_neighbour_all_time(base, s, direction, last_miss, r_miss, miss_repr, t_e, h):
    """One-ulp miss-side LAST on a fresh copy: realised r repr asserted, nothing fires (all TIME)."""
    m2 = copy_micro(base)
    poke_last_value(m2, s, direction, last_miss, p0=P0)
    r_back = production_r(m2.last[s], direction, m2.open[t_e])
    assert _repr(r_back) == miss_repr, _repr(r_back)
    assert r_back == r_miss
    _all_time(_core(m2, t_e, direction, h), h)


# --------------------------------------------------------------------------- pins / isolation / S-8
def test_F_X_768_and_pins():
    assert N_EXITS == 768
    assert N_FULL == 1_922_088_960
    assert HORIZONS_12 == (1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 14400)
    assert G3_ORDER == ("SL", "TP", "TR", "THESIS", "TIME")
    assert EXIT_CLOCK == "X1"
    assert type(LIQ_FLOOR_BP) is float and LIQ_FLOOR_BP == -50.0
    assert type(LIQ_FILL_BP) is float and LIQ_FILL_BP == -100.0
    assert R63H_CARD_SHA256 is None and R63I_CARD_SHA256 is None
    assert TP_BP == (None, 5, 10, 15, 20, 30, 50, 100)
    assert SL_BP == TP_BP and TR_BP == (None, 10, 20, 50)
    assert TH_KIND == (None, "sign_reverses", "back_inside_p50")
    _ex().check_startup()
    for name, pinned in (
        ("fulln_decide.py", DECIDE_SHA256),
        ("fulln_decide_pins.py", DECIDE_PINS_SHA256),
        ("fulln_closure.py", CLOSURE_SHA256),
    ):
        got = hashlib.sha256((PKG / name).read_bytes()).hexdigest()
        assert got == pinned, (name, got, pinned)


def test_F_X_ISOLATION_stageb_not_imported():
    sys.modules.pop("research_fulln480m.fulln_exit_stageb", None)
    sys.modules.pop("research_fulln480m.fulln_exit", None)
    m, t_e, p0 = None, 0, P0
    micro, _ = flat_window(5, t_e=0, p0=P0)
    out = _ex().exit(micro, 0, 1, 5)
    _all_time(out, 5)
    assert "research_fulln480m.fulln_exit_stageb" not in sys.modules
    assert "fulln_exit_stageb" not in sys.modules


def test_F_E_PARITY_METHOD():
    assert PARITY_LIQ_METHOD == "NON_PARITY_LIQ"


def test_F_E_REFSRC():
    from research_fulln480m.tests import ref_ext2_exit_path as ref
    q = ref.EXT2_L1087_L1104
    assert q.count("\n") == 17  # 18 lines, no trailing LF
    assert hashlib.sha256(q.encode()).hexdigest() == ref.QUOTED_BYTES_SHA256
    assert ref.QUOTED_BYTES_SHA256 == REF_SHA
    # F2: 8-space strip / textwrap.dedent; pin the dedented bytes too.
    d = ref.dedented_source()
    assert hashlib.sha256(d.encode()).hexdigest() == ref.DEDENTED_BYTES_SHA256
    assert d == textwrap.dedent(q)


# --------------------------------------------------------------------------- G.2 LIQ / NET435 / edge
@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQ_SLNONE(h, direction):
    """Raw adverse extreme -60 bp, last print stays at 0 (above -50); EXPECT LIQ -100 at k of that row."""
    m, p0 = flat_window(h, t_e=0, p0=P0)
    poke_ext(m, 0, direction, -60.0, p0=p0)
    # last stays p0 (r=0)
    out = _core(m, 0, direction, h)
    _all_liq(out, 0)
    assert out["g"][0] == np.float64(-100.0)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQ_EDGE50(h, direction):
    """6a FIRE: greatest representable g_adv <= -50.0 on the production g path; LIQ fires.

    EXT is derived by nextafter/ulp search (6d). Expected reprs are the RK 5482 neighbours,
    used only as an assertion pin, not as a search seed. Ruled state (RK c9b1 / ADD-4): no
    production seam; MG-LIQ-STRICT is EQUIVALENT, not KILLED, detected only by source pin.
    The 6b' monotone ulp walks and source pins below bind that equivalence.
    """
    m, p0 = flat_window(h, t_e=0, p0=P0)
    ext, _g_search = ext_fire_greatest_le_floor(p0, direction)
    poke_ext_value(m, 0, direction, ext, p0=p0)
    tables = _ex().first_hit_tables(m, 0, direction, h)
    got = np.float64(tables["g_adv"][0])
    print("EDGE50 FIRE h=%s d=%s g_adv=%r k_liq=%s" % (h, direction, got, tables["k_liq"]))
    expect = "-50.00000000000004" if int(direction) == 1 else "-50.00000000000115"
    assert repr(got) == "np.float64(%s)" % expect, (repr(got), expect)
    assert got <= np.float64(-50.0)
    out = _core(m, 0, direction, h)
    _all_liq(out, 0)
    assert int(tables["k_liq"]) == 0


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQ_EDGE50_noliq(h, direction):
    """6a NO-LIQ: first representable g_adv > -50.0, both sides; LIQ must not fire.

    Derived by nextafter/ulp search (6d), not a hand-typed EXT. This is also the
    MG-LIQ-F32CMP / BC-4 killer value on the formula path.
    """
    m, p0 = flat_window(h, t_e=0, p0=P0)
    ext, _g_search = ext_noliq_least_gt_floor(p0, direction)
    poke_ext_value(m, 0, direction, ext, p0=p0)
    tables = _ex().first_hit_tables(m, 0, direction, h)
    got = np.float64(tables["g_adv"][0])
    print("EDGE50 NOLIQ h=%s d=%s g_adv=%r k_liq=%s" % (h, direction, got, tables["k_liq"]))
    assert repr(got) == "np.float64(-49.999999999998934)", repr(got)
    assert got > np.float64(-50.0)
    out = _core(m, 0, direction, h)
    _all_time(out, h)
    assert int(tables["k_liq"]) == int(h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQ_HM_A_and_B(h, direction):
    m, p0 = flat_window(h, t_e=0, p0=P0)
    poke_ext(m, 0, direction, -60.0, p0=p0)
    _all_liq(_core(m, 0, direction, h), 0)
    m2, p02 = flat_window(h, t_e=0, p0=P0)
    poke_ext(m2, 0, direction, -40.0, p0=p02)
    _all_time(_core(m2, 0, direction, h), h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQ_LASTPRINT(h, direction):
    """Extreme -60 bp, last print -40 bp; SL=None still books LIQ (not the last print)."""
    m, p0 = flat_window(h, t_e=0, p0=P0)
    poke_last(m, 0, direction, -40.0, p0=p0)
    poke_ext(m, 0, direction, -60.0, p0=p0)
    out = _core(m, 0, direction, h)
    _all_liq(out, 0)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQ_SLNONE_ALL768(h, direction):
    """Same -60 bp construction; every column including SL != None books LIQ -100 once."""
    m, p0 = flat_window(h, t_e=0, p0=P0)
    poke_ext(m, 0, direction, -60.0, p0=p0)
    out = _core(m, 0, direction, h)
    _all_liq(out, 0)
    # F-X-LIQ-EXCL (a)/(c): one -100, never a double book.
    assert np.all(out["g"] == np.float64(-100.0))
    assert not np.any(out["g"] == np.float64(-200.0))


@pytest.mark.parametrize("h,direction", HD)
@pytest.mark.parametrize("g_adv", NET435_KILLERS)
def test_F_G_NET435_killers(h, direction, g_adv):
    m, p0 = flat_window(h, t_e=0, p0=P0)
    poke_ext(m, 0, direction, float(g_adv), p0=p0)
    out = _core(m, 0, direction, h)
    _all_time(out, h)
    # g_adv itself is in the open-closed interval, and must not fire LIQ.
    ext = m.low[0] if direction == 1 else m.high[0]
    got = np.float64(direction) * (ext / p0 - 1.0) * np.float64(1e4)
    assert -50.0 < got <= -43.5 or np.isclose(got, g_adv, rtol=0, atol=1e-12)


@pytest.mark.parametrize("h", HORIZONS)
def test_F_G_LIQEDGE_g64_short(h):
    """F-G-LIQEDGE (b) / MG-LIQ-F32CMP: short p0=80000 HIGH=80400 must not fire LIQ."""
    m, _ = flat_window(h, t_e=0, p0=G64_P0)
    set_bar(m, 0, p0=G64_P0, last=G64_P0, low=G64_P0, high=G64_HIGH)
    # remaining window rows stay flat at p0
    g = np.float64(-1) * (G64_HIGH / G64_P0 - 1.0) * np.float64(1e4)
    # Documented value: -49.999999999998934
    assert g > -50.0
    out = _core(m, 0, -1, h)
    _all_time(out, h)


# --------------------------------------------------------------------------- G.1 window, p0, 768, NaN
@pytest.mark.parametrize("h,direction", HD)
def test_F_G_W0_entry_row_can_trigger(h, direction):
    """W0: the entry row itself can trigger. LAST is the ulp-walked smallest meeting plant, so the
    realised r is 5.00000000000167 (not the 5.0 literal). Each column asserts the label its
    realised r implies: tp_i == 1 (TP 5) books TP at k=0 with g == realised r; the others cannot
    fire and book TIME with g NaN. A one-ulp miss-side neighbour fires nothing."""
    m, p0 = flat_window(h, t_e=0, p0=P0)
    base = copy_micro(m)
    last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
    poke_last_value(m, 0, direction, last_hit, p0=p0)
    out = _core(m, 0, direction, h)
    n_tp = 0
    for x in range(N_EXITS):
        tp_i = x // 96
        fires = tp_i >= 1 and r_hit >= np.float64(TP_BP[tp_i])
        if fires:
            assert tp_i == 1
            assert out["label"][x] == "TP" and out["k"][x] == 0
            assert out["g"][x] == r_hit
            n_tp += 1
        else:
            assert out["label"][x] == "TIME" and out["k"][x] == h - 1
            assert np.isnan(out["g"][x])
    assert n_tp == 96
    _assert_g_is(out, 96, r_hit, R5_HIT)
    _assert_neighbour_all_time(base, 0, direction, last_miss, r_miss, R5_MISS, 0, h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_PAST_no_row_before_te(h, direction):
    """A TP planted at t_e-1 must not fire; window starts at t_e."""
    if h == 1:
        t_e = 1
    else:
        t_e = 1
    m, p0 = flat_window(h, t_e=t_e, extra=0, p0=P0)
    poke_last(m, t_e - 1, direction, 100.0, p0=p0)  # would be a sure TP if included
    out = _core(m, t_e, direction, h)
    _all_time(out, h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_WEND_no_row_at_or_after_tx(h, direction):
    """A TP planted at t_x = t_e+h must not fire."""
    m, p0 = flat_window(h, t_e=0, extra=1, p0=P0)
    poke_last(m, h, direction, 100.0, p0=p0)
    out = _core(m, 0, direction, h)
    _all_time(out, h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_X_P0OPEN(h, direction):
    """p0 is OPEN of row t_e, not LAST of t_e. h > 1: LAST[t_e] is a favourable +3 bp, and a
    walked 5 bp plant at k=1 meets TP vs OPEN. With p0=LAST[t_e] the same print is only ~2.0 bp,
    so MX-P0-LAST misses TP and is killed by value. At h=1 there is no later row: stays all TIME
    (MX-P0-LAST is not killable at h=1 by this row)."""
    if h == 1:
        m, _ = flat_window(h, t_e=0, p0=P0)
        poke_last(m, 0, direction, -1.0, p0=P0)  # LAST != OPEN, |r| = 1 < 5
        _all_time(_core(m, 0, direction, h), h)
        return
    m, _ = flat_window(h, t_e=0, p0=P0)
    poke_last(m, 0, direction, 3.0, p0=P0)  # favourable +3 bp at t_e (< 5: no TP at k=0)
    base = copy_micro(m)
    last0 = m.last[0]
    last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
    poke_last_value(m, 1, direction, last_hit, p0=P0)
    r_wrong = production_r(last_hit, direction, last0)
    print("P0OPEN d=%s h=%s r_wrong(p0=LAST[t_e]=%s)=%s" % (direction, h, _repr(last0), _repr(r_wrong)))
    assert _repr(r_wrong) == P0OPEN_RWRONG[direction], _repr(r_wrong)
    assert r_wrong < np.float64(5.0)
    out = _core(m, 0, direction, h)
    n_tp = 0
    for x in range(N_EXITS):
        tp_i = x // 96
        fires = tp_i >= 1 and r_hit >= np.float64(TP_BP[tp_i])
        if fires:
            assert tp_i == 1
            assert out["label"][x] == "TP" and out["k"][x] == 1
            assert out["g"][x] == r_hit
            n_tp += 1
        else:
            assert out["label"][x] == "TIME" and out["k"][x] == h - 1
            assert np.isnan(out["g"][x])
    assert n_tp == 96
    _assert_g_is(out, 96, r_hit, R5_HIT)
    _assert_neighbour_all_time(base, 1, direction, last_miss, r_miss, R5_MISS, 0, h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_X_NAN(h, direction):
    m, _ = flat_window(h, t_e=0, p0=P0)
    out = _core(m, 0, direction, h)
    _all_time(out, h)


def test_F_X_768_combiner_width():
    m, _ = flat_window(1, t_e=0, p0=P0)
    out = _core(m, 0, 1, 1)
    assert out["label"].shape == (768,) and out["k"].shape == (768,) and out["g"].shape == (768,)
    # R-C7: same (t_e,d,h) is bit-identical across two calls (no per-cell drift).
    out2 = _core(m, 0, 1, 1)
    assert np.array_equal(out["k"], out2["k"])
    assert np.array_equal(np.isnan(out["g"]), np.isnan(out2["g"]))
    assert np.array_equal(out["label"], out2["label"])


# --------------------------------------------------------------------------- I.12 injection (S-10)
def _inj_kwargs(T, t_e, value, how="Tprime"):
    if how == "Tprime":
        return {"T": T, "thesis_Tprime": value}
    return {"T": T, "thesis_k": value}


@pytest.mark.parametrize("h,direction", HD)
def test_F_X_INJ_none_and_legal_and_illegal(h, direction):
    T = 0
    t_e = T + 1
    m, _ = flat_window(h, t_e=t_e, p0=P0)
    ex = _ex()
    # NONE accepted
    out = ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=None)
    _all_time(out, h)
    # every legal T' accepted; thesis competes at k = T'+1-t_e
    for Tp in legal_Tprimes(T, t_e, h):
        out = ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=(Tp, None))
        k = Tp + 1 - t_e
        assert 1 <= k <= h - 1
        # kind index 1 => th = x % 3 == 1 columns book THESIS (if k < h-1 or beat TIME at k=h-1)
        for x in range(N_EXITS):
            th = x % 3
            if th == 1:
                assert out["label"][x] == "THESIS" and out["k"][x] == k
                assert np.isnan(out["g"][x]) and out["g"][x] != 0.0
    # illegal offerings each STOP 78
    illegal_k = [0, -1, 1.5, True, False, h, h + 1]
    for bad in illegal_k:
        with pytest.raises(ClosureViolation) as ei:
            ex.exit_core(m, t_e, direction, h, T=T, thesis_k=bad)
        assert "S-10" in str(ei.value)
    # non-grid T' and T' < T+60
    with pytest.raises(ClosureViolation) as ei:
        ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=T + 2)
    assert "S-10" in str(ei.value)
    with pytest.raises(ClosureViolation) as ei:
        ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=T + 59)
    assert "S-10" in str(ei.value)


@pytest.mark.parametrize("h,direction", HD)
def test_F_X_INJ_OFFGRID(h, direction):
    """k=1 with t_e=T+2 is off-grid (T'=T+2) and STOPs; nearest on-grid k=59 iff K_max>=1."""
    T = 0
    t_e = T + 2
    m, _ = flat_window(h, t_e=t_e, extra=max(0, 60 - h), p0=P0)
    # Need enough rows: t_e + h - 1 < n. flat_window n = t_e+h+extra.
    # For legal k=59, window must include k=59, i.e. h-1>=59, h>=60, already implied by K_max>=1
    # at t_e=T+2: K_max = floor(h/60). Also n must cover t_e+h-1.
    ex = _ex()
    with pytest.raises(ClosureViolation) as ei:
        ex.exit_core(m, t_e, direction, h, T=T, thesis_k=1)
    assert "S-10" in str(ei.value)
    km = k_max_i12(T, t_e, h)
    if km >= 1:
        # nearest on-grid: T'=T+60, k=59
        out = ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=T + 60)
        for x in range(N_EXITS):
            if x % 3 == 1:
                assert out["label"][x] == "THESIS" and out["k"][x] == 59
    else:
        with pytest.raises(ClosureViolation) as ei:
            ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=T + 60)
        assert "S-10" in str(ei.value)


@pytest.mark.parametrize("h", (1, 5, 15, 30, 60))
@pytest.mark.parametrize("direction", DIRS)
def test_F_X_INJ_KMAX0(h, direction):
    T = 0
    t_e = T + 1
    assert k_max_i12(T, t_e, h) == 0
    m, _ = flat_window(h, t_e=t_e, p0=P0)
    ex = _ex()
    _all_time(ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=None), h)
    for bad in (1, T + 60, True, 0, h):
        kw = {"thesis_k": bad} if bad in (1, 0, h, True) else {"thesis_Tprime": bad}
        with pytest.raises(ClosureViolation) as ei:
            ex.exit_core(m, t_e, direction, h, T=T, **kw)
        assert "S-10" in str(ei.value)


# --------------------------------------------------------------------------- S-9 locks, S-5, S-7, S-10 shape, S-14/15
@pytest.mark.parametrize("h,direction", [(5, 1), (60, -1)])
def test_S9_price_request_and_thesis_kind(h, direction):
    m, _ = flat_window(h, t_e=1, p0=P0)
    ex = _ex()
    # production exit, no price request, no thesis kind: allowed; TIME g is NaN not a price.
    out = ex.exit(m, 1, direction, h)
    _all_time(out, h)
    with pytest.raises(ClosureViolation) as ei:
        ex.exit(m, 1, direction, h, request_time_thesis_price=True)
    assert "S-9" in str(ei.value)
    # production exit with a legal thesis kind present while I-lock is None
    T = 0
    t_e = 1
    km = k_max_i12(T, t_e, h)
    if km >= 1:
        with pytest.raises(ClosureViolation) as ei:
            ex.exit(m, t_e, direction, h, T=T, thesis_Tprime=T + 60)
        assert "S-9" in str(ei.value)
    # core may inject (tests); production is the locked entry
    if km >= 1:
        outc = ex.exit_core(m, t_e, direction, h, T=T, thesis_Tprime=T + 60)
        assert "THESIS" in list(outc["label"])


def test_S5_window_outside_valid_span():
    m, _ = flat_window(5, t_e=0, p0=P0)
    valid = np.ones(len(m.count), dtype=bool)
    valid[3] = False
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit_core(m, 0, 1, 5, valid=valid)
    assert "S-5" in str(ei.value)


def test_S7_nan_p0_and_empty_entry_and_tx():
    m, _ = flat_window(5, t_e=0, p0=P0)
    m.count[0] = 0
    m.open[0] = np.nan
    m.last[0] = np.nan
    m.low[0] = np.nan
    m.high[0] = np.nan
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit_core(m, 0, 1, 5)
    assert "S-7" in str(ei.value)
    m2, _ = flat_window(5, t_e=0, p0=P0)
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit(m2, 0, 1, 5, t_x=0 + 5 + 1)
    assert "S-7" in str(ei.value)


def test_S10_bad_direction_and_horizon():
    m, _ = flat_window(5, t_e=0, p0=P0)
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit_core(m, 0, 0, 5)
    assert "S-10" in str(ei.value)
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit_core(m, 0, 1, 7)
    assert "S-10" in str(ei.value)
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit_core(m, 0, True, 5)  # bool is not +1
    assert "S-10" in str(ei.value)


def test_F_X_TXCHECK():
    m, _ = flat_window(15, t_e=0, p0=P0)
    # matching t_x is accepted
    out = _ex().exit(m, 0, 1, 15, t_x=15)
    _all_time(out, 15)
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit(m, 0, 1, 15, t_x=14)
    assert "S-7" in str(ei.value)


# --------------------------------------------------------------------------- compose / n_a / only-filled
def test_F_X_ONLYFILLED_COMPOSE_and_n_a():
    """Decide then exit only on FILLED; n_a = NO_FILL + UNFRESH (EMBARGO/BLOCKED excluded).

    TESTFIX r1 F-T3 / H1 / C3: decisions [0, 60, 120] give [UNFRESH, FILLED, NO_FILL]. T=0 is
    UNFRESH by adopted R63-F.6 (deci = T-1 < 0); T=60 fills at t_e=61; has/bars are cleared on
    121..125 only, so T=120 is NO_FILL."""
    n = 200
    m = empty_micro(n, base_s=0)
    valid = np.ones(n, dtype=bool)
    has = np.zeros(n, dtype=bool)
    last_tr = np.full(n, -1, dtype=np.int64)
    for s in range(n):
        set_bar(m, s, p0=P0)
        has[s] = True
        last_tr[s] = s
    for s in range(121, 126):
        m.count[s] = 0
        m.open[s] = m.last[s] = m.low[s] = m.high[s] = np.nan
        has[s] = False
    cfg = pins_config()
    assert cfg["STALE_S"] == 600
    rows_out = decide_core(has, last_tr, valid, [0, 60, 120], 5, cfg, direction=1)
    labels = [r[1] for r in rows_out["rows"]]
    assert labels == [UNFRESH, FILLED, NO_FILL], labels
    assert rows_out["rows"][0][0] == 0 and rows_out["rows"][1][0] == 60 and rows_out["rows"][2][0] == 120
    ex = _ex()
    n_a = ex.count_n_a(rows_out["rows"])
    expected = sum(1 for lab in labels if lab in (NO_FILL, UNFRESH))
    assert n_a == expected == 2
    n_filled = 0
    for T, lab, t_e, t_x in rows_out["rows"]:
        if lab != FILLED:
            continue
        n_filled += 1
        assert t_e == 61 and t_x == t_e + 5
        out = ex.exit(m, t_e, 1, 5, t_x=t_x)
        _all_time(out, 5)
    assert n_filled == 1


# --------------------------------------------------------------------------- F-M-XDAY exit labels
@pytest.mark.parametrize("kind", ("TP", "SL", "LIQ", "TR"))
def test_F_M_XDAY_exit_labels_h60(kind):
    """T=86340, t_e=T+1, h=60: last window row is 86400, first second of the next day.

    TESTFIX r1: TP / SL plants are ulp-walked (booked g == realised r, repr asserted) with a
    one-ulp no-fire neighbour; the TR hit books the realised r 9.999999999998899 (card L599)."""
    T = 86_340
    t_e = T + 1  # 86341
    h = 60
    last_row = t_e + h - 1
    assert last_row == 86_400
    m = two_day_axis(base_s=0)
    # Flat traded bars on the whole window, p0 at t_e.
    for s in range(t_e, last_row + 1):
        set_bar(m, s, p0=P0)
    base = copy_micro(m)
    direction = 1
    # Plant the event on the first next-day second; establish trail peak before midnight for TR.
    if kind == "TP":
        last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
        poke_last_value(m, last_row, direction, last_hit, p0=P0)
        out = _core(m, t_e, direction, h)
        for x in range(N_EXITS):
            if x // 96 == 1:
                assert out["label"][x] == "TP" and out["k"][x] == last_row - t_e
                assert out["g"][x] == r_hit
        _assert_g_is(out, 96, r_hit, R5_HIT)
        _assert_neighbour_all_time(base, last_row, direction, last_miss, r_miss, R5_MISS, t_e, h)
    elif kind == "SL":
        last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, -5.0, R5_SL_HIT, R5_SL_MISS)
        poke_last_value(m, last_row, direction, last_hit, p0=P0)
        out = _core(m, t_e, direction, h)
        for x in range(N_EXITS):
            sl_i = (x // 12) % 8
            tp_i = x // 96
            if sl_i == 1 and tp_i == 0:
                assert out["label"][x] == "SL" and out["k"][x] == last_row - t_e
                assert out["g"][x] == r_hit
        _assert_g_is(out, 12, r_hit, R5_SL_HIT)
        _assert_neighbour_all_time(base, last_row, direction, last_miss, r_miss, R5_SL_MISS, t_e, h)
    elif kind == "LIQ":
        poke_ext(m, last_row, direction, -60.0, p0=P0)
        out = _core(m, t_e, direction, h)
        _all_liq(out, last_row - t_e)
    else:
        # Trail: peak established before midnight (r=+20 at last pre-midnight row 86399),
        # then pullback after midnight by 10 bp (r=10) -> TR=10 hits on next-day row.
        pre = 86_399
        assert pre == last_row - 1 and pre < 86_400
        poke_last(m, pre, direction, 20.0, p0=P0)
        poke_last(m, last_row, direction, 10.0, p0=P0)
        out = _core(m, t_e, direction, h)
        # tr_i = (x // 3) % 4; tr_i==1 is TR_BP 10. tp=0, sl=0 so they don't steal.
        # The booked g is the realised r at the hit row (card L599), not the literal 10.0.
        r_tr = production_r(m.last[last_row], direction, P0)
        assert r_tr.dtype == np.float64
        assert _repr(r_tr) == R10_TR_HIT, _repr(r_tr)
        hit = False
        for x in range(N_EXITS):
            tp_i, sl_i, tr_i = x // 96, (x // 12) % 8, (x // 3) % 4
            if tp_i == 0 and sl_i == 0 and tr_i == 1:
                assert out["label"][x] == "TR" and out["k"][x] == last_row - t_e
                assert out["g"].dtype == np.float64
                print("col %d TR booked g repr=%s" % (x, _repr(out["g"][x])))
                assert out["g"][x] == np.float64(9.999999999998899)
                assert _repr(out["g"][x]) == R10_TR_HIT
                assert out["g"][x] == r_tr
                hit = True
        assert hit


@pytest.mark.parametrize("h,kind", [(3600, "TP"), (3600, "LIQ"), (14400, "TP"), (14400, "LIQ")])
def test_F_M_XDAY_longer_horizons(h, kind):
    T = 86_340
    t_e = T + 1
    last_row = t_e + h - 1
    assert last_row > 86_400
    m = empty_micro(t_e + h + 1, base_s=0)
    for s in range(t_e, last_row + 1):
        set_bar(m, s, p0=P0)
    base = copy_micro(m)
    direction = 1
    if kind == "TP":
        last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
        poke_last_value(m, 86_400, direction, last_hit, p0=P0)
        out = _core(m, t_e, direction, h)
        for x in range(N_EXITS):
            if x // 96 == 1:
                assert out["label"][x] == "TP" and out["k"][x] == 86_400 - t_e
                assert out["g"][x] == r_hit
        _assert_g_is(out, 96, r_hit, R5_HIT)
        _assert_neighbour_all_time(base, 86_400, direction, last_miss, r_miss, R5_MISS, t_e, h)
    else:
        poke_ext(m, 86_400, direction, -60.0, p0=P0)
        out = _core(m, t_e, direction, h)
        _all_liq(out, 86_400 - t_e)


def test_F_M_XDAY_h30_no_crossing():
    T = 86_340
    t_e = T + 1
    h = 30
    last_row = t_e + h - 1
    assert last_row == 86_370 and last_row < 86_400
    m = empty_micro(DAY_S, base_s=0)
    for s in range(t_e, last_row + 1):
        set_bar(m, s, p0=P0)
    base = copy_micro(m)
    last_hit, r_hit, last_miss, r_miss = _walk_checked(1, 5.0, R5_HIT, R5_MISS)
    poke_last_value(m, last_row, 1, last_hit, p0=P0)
    out = _core(m, t_e, 1, h)
    for x in range(N_EXITS):
        if x // 96 == 1:
            assert out["label"][x] == "TP" and out["k"][x] == last_row - t_e
            assert out["g"][x] == r_hit
    _assert_g_is(out, 96, r_hit, R5_HIT)
    _assert_neighbour_all_time(base, last_row, 1, last_miss, r_miss, R5_MISS, t_e, h)


# --------------------------------------------------------------------------- m-9 combiner ties (injected k)
def test_m9_combiner_unreachable_and_ties():
    """combine_from_k accepts injected k including unreachable SL/TP and same-k TP/TR / LIQ/SL."""
    ex = _ex()
    h = 30
    sent = h
    k_tp = np.full(7, sent, dtype=np.int32)
    g_tp = np.zeros(7, dtype=np.float64)
    k_sl = np.full(7, sent, dtype=np.int32)
    g_sl = np.zeros(7, dtype=np.float64)
    k_tr = np.full(3, sent, dtype=np.int32)
    g_tr = np.zeros(3, dtype=np.float64)
    k_th = np.full(2, sent, dtype=np.int32)
    # Same-k LIQ vs SL: LIQ wins (SL slot, beats SL).
    k_sl[0] = 3
    g_sl[0] = np.float64(-5.0)
    out = ex.combine_from_k(k_tp, g_tp, k_sl, g_sl, k_tr, g_tr, k_th, k_liq=3, k_time=h - 1, h=h)
    _all_liq(out, 3)
    # Same-k TP vs TR, no LIQ/SL: TP wins (G3_ORDER).
    k_sl[0] = sent
    k_tp[0] = 4
    g_tp[0] = np.float64(5.0)
    k_tr[0] = 4
    g_tr[0] = np.float64(1.0)
    out = ex.combine_from_k(k_tp, g_tp, k_sl, g_sl, k_tr, g_tr, k_th, k_liq=sent, k_time=h - 1, h=h)
    for x in range(N_EXITS):
        tp_i, sl_i, tr_i = x // 96, (x // 12) % 8, (x // 3) % 4
        if tp_i == 1:
            assert out["label"][x] == "TP" and out["k"][x] == 4
        elif tr_i == 1:
            assert out["label"][x] == "TR" and out["k"][x] == 4
        else:
            assert out["label"][x] == "TIME"
    # Unreachable k (k >= h) for SL/TP must not compete.
    k_tp[0] = h
    k_tr[0] = sent
    out = ex.combine_from_k(k_tp, g_tp, k_sl, g_sl, k_tr, g_tr, k_th, k_liq=sent, k_time=h - 1, h=h)
    _all_time(out, h)


# --------------------------------------------------------------------------- F-E-EXT2PARITY (limited, allowed diffs enumerated)
def _ext2_first_hit(px, e0, H, d, S, p0_inj):
    """Run the verbatim L1087-L1104 block on one entry (no forced-close cap; p0 injected).

    F3: exec is inside ref.run_verbatim, in an explicit namespace that supplies only
    np, H, e0, end, px, S, d, p0, T, R, a, m, TP_BP, SL_BP, TR_BP. T and R are read
    only after exec returns.
    """
    import numpy as np
    from research_fulln480m.tests import ref_ext2_exit_path as ref
    T = np.full((1, 17), int(H) + 1, np.int32)
    R = np.zeros((1, 17), np.float32)
    ns = {
        "np": np,
        "H": int(H),
        "e0": np.asarray([int(e0)], dtype=np.int64),
        "end": np.asarray([min(int(e0) + int(H), int(S) - 1)], dtype=np.int64),
        "px": np.asarray(px, dtype=np.float64),
        "S": int(S),
        "d": np.float64(d),
        "p0": np.asarray([np.float64(p0_inj)]),
        "T": T,
        "R": R,
        "a": 0,
        "m": 1,
        "TP_BP": ref.TP_BP,
        "SL_BP": ref.SL_BP,
        "TR_BP": ref.TR_BP,
    }
    # HM F2: first ndarray.any(axis) in a process lazily imports via
    # the frame's __builtins__['__import__']. The restricted exec has none.
    # Warm once outside the exec (HM probe: np.zeros((1,2),bool).any(1)).
    np.zeros((1, 2), bool).any(1)
    ref.run_verbatim(ns)
    return ns["T"][0], ns["R"][0]


@pytest.mark.parametrize("direction", DIRS)
def test_F_E_EXT2PARITY_planted_tp_sl_tr(direction):
    """Planted hits at k>=1 compared to ext2 with e0=t_e-1 (off 1..H maps to k 0..h-1).

    Allowed differences enumerated: no LIQ in ext2 (s5.2.2 NON_PARITY); p0 is injected as
    OPEN[t_e] not px[e0]; no forced-close cap (L1082-L1086); X1 TIME at k=h-1 replaces ext2
    L1105-L1111; G.3 combiner is ours (this test compares the 17-level first-hit T/R only);
    ext2 R is float32 stored and is never used for a LIQ decision (BC-4).
    """
    h = 30
    t_e = 1
    m, p0 = flat_window(h, t_e=t_e, p0=P0)
    poke_last(m, t_e + 5, direction, 10.0, p0=p0)   # TP 5 and 10
    poke_last(m, t_e + 8, direction, -15.0, p0=p0)  # after TP? different rows; SL at k=8
    # For TR, need a peak then pullback on a later row; keep this to TP/SL first-hit here,
    # plus a second micro for TR only.
    # Build px over the axis as LAST (ext2 uses last-print px).
    px = m.last.copy()
    T_ext, R_ext = _ext2_first_hit(px, e0=t_e - 1, H=h, d=direction, S=len(px), p0_inj=p0)
    tables = _ex().first_hit_tables(m, t_e, direction, h)
    # TP cols 0..6, SL 7..13 in ext2; our k_tp[7], k_sl[7] are 0-based k, sentinel = h
    # ext2 stores offset 1..H or big=H+1. Mapping: our k -> ext2 T = k+1; sentinel h -> H+1.
    for i, tp in enumerate(TP_BP[1:]):
        k = int(tables["k_tp"][i])
        ext = int(T_ext[i])
        if k >= h:
            assert ext == h + 1
        else:
            assert ext == k + 1
            # stored ext2 R is float32 of r at hit
            assert R_ext[i] == np.float32(tables["g_tp"][i])
    for i, sl in enumerate(SL_BP[1:]):
        k = int(tables["k_sl"][i])
        ext = int(T_ext[7 + i])
        if k >= h:
            assert ext == h + 1
        else:
            assert ext == k + 1
            assert R_ext[7 + i] == np.float32(tables["g_sl"][i])
    # TR-only micro: peak at k=2 (r=20), pullback at k=6 (r=10) -> TR 10
    m2, p02 = flat_window(h, t_e=t_e, p0=P0)
    poke_last(m2, t_e + 2, direction, 20.0, p0=p02)
    poke_last(m2, t_e + 6, direction, 10.0, p0=p02)
    T2, R2 = _ext2_first_hit(m2.last, e0=t_e - 1, H=h, d=direction, S=len(m2.last), p0_inj=p02)
    t2 = _ex().first_hit_tables(m2, t_e, direction, h)
    for i, tr in enumerate(TR_BP[1:]):
        k = int(t2["k_tr"][i])
        ext = int(T2[14 + i])
        if k >= h:
            assert ext == h + 1
        else:
            assert ext == k + 1
            assert R2[14 + i] == np.float32(t2["g_tr"][i])



# --------------------------------------------------------------------------- ADD-6: 6b' + H5 (s8 only)
# IEEE binary64 RN per operation, no FMA or extended precision: ASSUMED on this box.
# The all-x proof covers ANY finite p0 the code accepts; production checks finite, not p0 > 0.
# NaN/inf compare the same under strict/inclusive. This equivalence concerns the f64 path only.
# MG-* names canonical, MX-TP/SL/TR-STRICT are historical aliases.
_H5_LINE_SHAS = {218: '136cf92cf7b15d69d6cac6548f3e7e844ca4156d4b9a92d88b95fced939ce9c7', 219: '87fdec9cb6cfe4f29ffa3feed8c5f8a77867ae9f15195f31ae88dae9160d9809', 225: '50bb1fa6aed19c3070379795019648f80ff3c4b5d7128c4fb4a2881c7d5c5ea1', 228: '3ecd4c4dfb475d9a95bbfbdc60ca1dcf5c9b19fcf0809648f1ffa66bf6d0747f', 229: 'b14e54f52664e49c30fe5039c3e56bd4f09379246cb9db92e75cf723eec326ee', 235: '48d9d4ccaed25e7ad2e84836055d3f4d7b67158fbfebebbfcb36c0f03133d0a3', 236: '8a6a300e91140cff67ad474e62ff616a8e1276b660f1fca52b99197c82dc8b95', 241: 'd7e8f613d957d6d45d7d0c1b78e0daefbc9b5edaa86f24801a3c606a1146a777', 242: '63bdd9c0f4359b6eea3c551f59c7770457c6b340a44370e2b68f7f134967e815', 246: '46722dd8afee7f4afa5b8320d75e364475b89b6c1905c7a8360e895715fff1fc', 247: '6425ceb5e39bd4c835321cd506f4641f665ed7368ce83d308da9ebf5d8a2e868', 248: '56be09bd558c0167c8c5c7c9ad94860db8bb3182ec8886f4b9b8e3a1b62ea6ac'}
_TPSL_STRADDLE = {('TP', 5, 1.0, 1): ('4.999999999999449', '5.00000000000167'), ('TP', 5, 1.0, -1): ('4.999999999999449', '5.0000000000005596'), ('TP', 10, 1.0, 1): ('9.999999999998899', '10.000000000001119'), ('TP', 10, 1.0, -1): ('9.999999999998899', '10.000000000000009'), ('TP', 15, 1.0, 1): ('14.999999999998348', '15.000000000000568'), ('TP', 15, 1.0, -1): ('14.999999999999458', '15.000000000000568'), ('TP', 20, 1.0, 1): ('19.999999999997797', '20.000000000000018'), ('TP', 20, 1.0, -1): ('19.999999999998906', '20.000000000000018'), ('TP', 30, 1.0, 1): ('29.999999999998916', '30.000000000001137'), ('TP', 30, 1.0, -1): ('29.999999999998916', '30.00000000000003'), ('TP', 50, 1.0, 1): ('49.999999999998934', '50.00000000000115'), ('TP', 50, 1.0, -1): ('49.999999999998934', '50.00000000000004'), ('TP', 100, 1.0, 1): ('99.99999999999787', '100.00000000000009'), ('TP', 100, 1.0, -1): ('99.99999999999898', '100.00000000000009'), ('SL', 5, 1.0, 1): ('-5.0000000000005596', '-4.999999999999449'), ('SL', 5, 1.0, -1): ('-5.00000000000167', '-4.999999999999449'), ('SL', 10, 1.0, 1): ('-10.000000000000009', '-9.999999999998899'), ('SL', 10, 1.0, -1): ('-10.000000000001119', '-9.999999999998899'), ('SL', 15, 1.0, 1): ('-15.000000000000568', '-14.999999999999458'), ('SL', 15, 1.0, -1): ('-15.000000000000568', '-14.999999999998348'), ('SL', 20, 1.0, 1): ('-20.000000000000018', '-19.999999999998906'), ('SL', 20, 1.0, -1): ('-20.000000000000018', '-19.999999999997797'), ('SL', 30, 1.0, 1): ('-30.00000000000003', '-29.999999999998916'), ('SL', 30, 1.0, -1): ('-30.000000000001137', '-29.999999999998916'), ('SL', 50, 1.0, 1): ('-50.00000000000004', '-49.999999999998934'), ('SL', 50, 1.0, -1): ('-50.00000000000115', '-49.999999999998934'), ('SL', 100, 1.0, 1): ('-100.00000000000009', '-99.99999999999898'), ('SL', 100, 1.0, -1): ('-100.00000000000009', '-99.99999999999787'), ('TP', 5, 100.0, 1): ('4.999999999999449', '5.00000000000167'), ('TP', 5, 100.0, -1): ('4.999999999999449', '5.00000000000167'), ('TP', 10, 100.0, 1): ('9.999999999998899', '10.000000000001119'), ('TP', 10, 100.0, -1): ('9.999999999998899', '10.000000000001119'), ('TP', 15, 100.0, 1): ('14.999999999998348', '15.000000000000568'), ('TP', 15, 100.0, -1): ('14.999999999999458', '15.000000000000568'), ('TP', 20, 100.0, 1): ('19.999999999997797', '20.000000000000018'), ('TP', 20, 100.0, -1): ('19.999999999998906', '20.000000000000018'), ('TP', 30, 100.0, 1): ('29.999999999998916', '30.000000000001137'), ('TP', 30, 100.0, -1): ('29.999999999997804', '30.00000000000003'), ('TP', 50, 100.0, 1): ('49.999999999998934', '50.00000000000115'), ('TP', 50, 100.0, -1): ('49.999999999998934', '50.00000000000004'), ('TP', 100, 100.0, 1): ('99.99999999999787', '100.00000000000009'), ('TP', 100, 100.0, -1): ('99.99999999999898', '100.00000000000009'), ('SL', 5, 100.0, 1): ('-5.00000000000167', '-4.999999999999449'), ('SL', 5, 100.0, -1): ('-5.00000000000167', '-4.999999999999449'), ('SL', 10, 100.0, 1): ('-10.000000000001119', '-9.999999999998899'), ('SL', 10, 100.0, -1): ('-10.000000000001119', '-9.999999999998899'), ('SL', 15, 100.0, 1): ('-15.000000000000568', '-14.999999999999458'), ('SL', 15, 100.0, -1): ('-15.000000000000568', '-14.999999999998348'), ('SL', 20, 100.0, 1): ('-20.000000000000018', '-19.999999999998906'), ('SL', 20, 100.0, -1): ('-20.000000000000018', '-19.999999999997797'), ('SL', 30, 100.0, 1): ('-30.00000000000003', '-29.999999999997804'), ('SL', 30, 100.0, -1): ('-30.000000000001137', '-29.999999999998916'), ('SL', 50, 100.0, 1): ('-50.00000000000004', '-49.999999999998934'), ('SL', 50, 100.0, -1): ('-50.00000000000115', '-49.999999999998934'), ('SL', 100, 100.0, 1): ('-100.00000000000009', '-99.99999999999898'), ('SL', 100, 100.0, -1): ('-100.00000000000009', '-99.99999999999787')}


def _source_pin(lines):
    """Locate pinned lines by content (sha256 of the exact source line). Line numbers are secondary."""
    source = Path(_ex().__file__).read_text().splitlines()
    by_sha = {}
    for i, line in enumerate(source):
        by_sha.setdefault(hashlib.sha256(line.encode()).hexdigest(), []).append((i + 1, line))
    for n in lines:
        expect = _H5_LINE_SHAS[n]
        found = by_sha.get(expect)
        here = source[n - 1] if 0 <= n - 1 < len(source) else "<oob>"
        n_found = 0 if found is None else len(found)
        assert n_found == 1, "pinned content for historical L%s expected exactly one match (sha=%s, n_matches=%s); L%s now %r" % (
            n, expect, n_found, n, here)


def test_P_6b_LIQ_source_pin():
    """6b'.3: MG-LIQ-STRICT is EQUIVALENT, not KILLED. Detected ONLY by this pin."""
    _source_pin((219, 225))
    assert type(LIQ_FLOOR_BP) is float and LIQ_FLOOR_BP == -50.0
    assert _ex().LIQ_FLOOR_BP == -50.0


def test_P_H5_TPSL():
    """MG-TP-STRICT / MG-SL-STRICT: EQUIVALENT, not KILLED; pin binds the all-f64 proof."""
    _source_pin((218, 228, 229, 235, 236))
    assert _ex().TP_BP == _ex().SL_BP == TP_BP == SL_BP == (None, 5, 10, 15, 20, 30, 50, 100)


def test_P_H5_TR():
    """P-H5-TR (2176 V3): TR text plus TR_BP. Changed text voids the witness card."""
    _source_pin((241, 242, 246, 247, 248))
    assert _ex().TR_BP == TR_BP == (None, 10, 20, 50)


@pytest.mark.parametrize("direction", DIRS)
@pytest.mark.parametrize("p0", (100.0, 80000.0))
def test_F_6b_LIQ_EQUIV(direction, p0):
    """c9b1 6b'.2 / cc0c D1-D2: >=1000 ulps each side, no g_adv == -50.0.
    Exact L219 expression plus runtime PRODUCTION g_adv; adjacent straddle repr is p0-specific."""
    seed = ext_for_g(p0, direction, -50.0)
    xs = ulp_neighbourhood(seed, np.inf if direction == 1 else -np.inf)
    m = repr_walk_micro(p0, xs, direction, kind="ext")
    g = _ex().first_hit_tables(m, 0, direction, 3600)["g_adv"][1:2002]
    literal = production_g_adv(xs, direction, p0)
    assert g.dtype == np.float64 and np.array_equal(g, literal)
    assert len(xs) == 2001 and np.all(np.diff(g) >= 0)
    assert not np.any(g == np.float64(-50.0))
    i = int(np.flatnonzero(g > -50.0)[0])
    assert 0 < i < len(xs)
    assert np.nextafter(xs[i-1], np.inf if direction == 1 else -np.inf) == xs[i]
    fire, miss = _repr(g[i-1]), _repr(g[i])
    want_fire = "-50.00000000000004" if direction == 1 else "-50.00000000000115"
    want_miss = "-49.999999999997826" if p0 == 80000.0 and direction == 1 else "-49.999999999998934"
    print("LIQ straddle p0=%r d=%r fire=%s noLIQ=%s" % (p0, direction, fire, miss))
    assert (fire, miss) == (want_fire, want_miss)
    assert g[i-1] < -50.0 < g[i]


@pytest.mark.parametrize("kind,v,p0,direction", list(_TPSL_STRADDLE))
def test_F_H5_TPSL_EQUIV(kind, v, p0, direction):
    """Runtime model check via PRODUCTION first_hit_tables, not a test copy of r.
    Monotone 1000-ulp walk on both sides, no equality. Proof: 5043 s3 + 2176 (not a grid proof)."""
    V = np.float64(v if kind == "TP" else -v)
    xs = ulp_neighbourhood(last_for_r(p0, direction, V), np.inf if direction == 1 else -np.inf)
    m = repr_walk_micro(p0, xs, direction)
    r = _ex().first_hit_tables(m, 0, direction, 3600)["r"][1:2002]
    assert r.dtype == np.float64 and len(r) == 2001
    assert np.all(np.diff(r) >= 0) and not np.any(r == V)
    i = int(np.flatnonzero(r > V)[0])
    assert 0 < i < len(xs)
    assert np.nextafter(xs[i-1], np.inf if direction == 1 else -np.inf) == xs[i]
    pair = (_repr(r[i-1]), _repr(r[i]))
    print("TPSL straddle %s%r p0=%r d=%r below=%s above=%s" % (kind, v, p0, direction, *pair))
    assert pair == _TPSL_STRADDLE[(kind, v, p0, direction)]
    assert r[i-1] < V < r[i]
    assert np.array_equal(r >= V, r > V) and np.array_equal(r <= V, r < V)


def _h5_tr_micro(witness, last_b=None):
    tr, d, lp, lb, rp, rb, steps, lnb, rnb, ddnb = witness
    m, p0 = flat_window(5, p0=P0)
    set_bar(m, 1, p0=p0, last=np.float64(lp))
    set_bar(m, 2, p0=p0, last=np.float64(lb) if last_b is None else last_b)
    return m


@pytest.mark.parametrize("witness", H5_TR_WITNESSES, ids=["TR%d-%s" % (w[0], "long" if w[1]==1 else "short") for w in H5_TR_WITNESSES])
def test_F_H5_TR_STRICT_KILL(witness):
    """All six repr-literal rows KILL MG-TR-STRICT by value, not merely a source pin."""
    tr, d, lp, lb, rp, rb, steps, lnb, rnb, ddnb = witness
    m = _h5_tr_micro(witness)
    tables = _ex().first_hit_tables(m, 0, d, 5)
    r = tables["r"]
    dd = max(r[1], np.float64(0.0)) - r[2]
    assert (_repr(r[1]), _repr(r[2]), _repr(dd)) == (rp, rb, _repr(tr))
    assert dd == np.float64(tr) and tables["k_liq"] == 5
    tri = TR_BP.index(tr)
    x = 3 * tri
    assert _ex().exit_decode(x) == (0, 0, tri, 0)
    out = _core(m, 0, d, 5)
    result = (str(out["label"][x]), int(out["k"][x]), _repr(out["g"][x]))
    print("TR witness tr=%s d=%s LAST=%s peak=%s r=%s dd=%s result=%r" % (tr, d, lb, rp, rb, _repr(dd), result))
    assert out["g"].dtype == np.float64
    assert result == ("TR", 2, rb)


@pytest.mark.parametrize("witness", H5_TR_WITNESSES, ids=["TR%d-%s" % (w[0], "long" if w[1]==1 else "short") for w in H5_TR_WITNESSES])
def test_F_H5_TR_NB(witness):
    """Walk toward the peak, max 64 steps; FAIL, never skip, if no no-fire-at-k2 neighbour.
    RUN through production exit_core (V4), including the TR20-long 2-ulp row."""
    tr, d, lp, lb, rp, rb, steps, lnb, rnb, ddnb = witness
    last_b = np.float64(lb)
    for n in range(1, 65):
        last_b = np.nextafter(last_b, np.float64(lp))
        m = _h5_tr_micro(witness, last_b)
        tables = _ex().first_hit_tables(m, 0, d, 5)
        r = tables["r"]
        dd = max(r[1], np.float64(0.0)) - r[2]
        if dd < np.float64(tr):
            break
    else:
        pytest.fail("F-H5-TR-NB: no neighbour within 64 ulps")
    assert n == steps
    assert (_repr(last_b), _repr(r[2]), _repr(dd)) == (lnb, rnb, ddnb)
    x = 3 * TR_BP.index(tr)
    out = _core(m, 0, d, 5)
    result = (str(out["label"][x]), int(out["k"][x]), float(out["g"][x]))
    print("TR NB tr=%s d=%s steps=%s LAST=%s drawdown=%s result=%r" % (tr, d, n, _repr(last_b), _repr(dd), result))
    assert result[:2] == ("TR", 3)
    assert _repr(out["g"][x]) == ("0.0" if d == 1 else "-0.0")
    assert out["g"].dtype == np.float64
    if tr == 20 and d == 1:
        assert _repr(last_b) == "101.08000000000013"
        assert _repr(dd) == "19.999999999997783"
        assert result == ("TR", 3, 0.0)


@pytest.mark.parametrize("direction", DIRS)
def test_F_G_TR_FLOOR(direction):
    """1959: first-row r ~ -10.5, SL=None TR=10, fires ONLY because peak is floored at 0.
    No-fire neighbour is the 10 bp miss-side adjacent LAST (drawdown < 10), run through production."""
    m, p0 = flat_window(5, p0=P0)
    poke_last(m, 0, direction, -10.5, p0=p0)
    tables = _ex().first_hit_tables(m, 0, direction, 5)
    r0 = tables["r"][0]
    dd = np.float64(0.0) - r0
    assert _repr(r0) == "-10.499999999999954" and _repr(dd) == "10.499999999999954"
    assert tables["k_liq"] == 5
    out = _core(m, 0, direction, 5)
    assert _ex().exit_decode(3) == (0, 0, 1, 0)
    result = (str(out["label"][3]), int(out["k"][3]), _repr(out["g"][3]))
    print("TR FLOOR d=%s dd=%s result=%r" % (direction, _repr(dd), result))
    assert result == ("TR", 0, "-10.499999999999954")
    # The smallest-magnitude meeting SL -10 plant and its one-ulp miss neighbour.
    _last_hit, _r_hit, last_nb, r_nb = last_meeting_r(p0, direction, -10.0)
    nb, _ = flat_window(5, p0=p0)
    poke_last_value(nb, 0, direction, last_nb, p0=p0)
    tnb = _ex().first_hit_tables(nb, 0, direction, 5)
    nb_dd = np.float64(0.0) - tnb["r"][0]
    print("TR FLOOR NB d=%s LAST=%s r=%s dd=%s" % (direction, _repr(last_nb), _repr(tnb["r"][0]), _repr(nb_dd)))
    assert _repr(nb_dd) == "9.999999999998899" and nb_dd < np.float64(10.0)
    assert _repr(tnb["r"][0]) == "-9.999999999998899"
    nbo = _core(nb, 0, direction, 5)
    n_sl_none = 0
    for x in range(N_EXITS):
        if _ex().exit_decode(x)[1] == 0:  # SL=None: only TR could fire; drawdown < 10 -> TIME
            n_sl_none += 1
            assert str(nbo["label"][x]) == "TIME" and int(nbo["k"][x]) == 4, (x, nbo["label"][x])
            assert np.isnan(nbo["g"][x])
    assert n_sl_none == 96


def test_F_m9_injected_SL_TP_priority():
    """1959: unreachable end-to-end SL/TP tie MUST be killed at combine_from_k, card m-9."""
    ex = _ex()
    h = 30
    ktp = np.full(7, h, dtype=np.int32); ksl = ktp.copy()
    gtp = np.zeros(7, dtype=np.float64); gsl = gtp.copy()
    ktr = np.full(3, h, dtype=np.int32); gtr = np.zeros(3, dtype=np.float64)
    kth = np.full(2, h, dtype=np.int32)
    ktp[0] = ksl[0] = 4
    gtp[0] = np.float64(5.0); gsl[0] = np.float64(-5.0)
    out = ex.combine_from_k(ktp, gtp, ksl, gsl, ktr, gtr, kth, h, h-1, h)
    x = 96 + 12  # tp_i=sl_i=1, no TR/thesis
    assert ex.exit_decode(x) == (1, 1, 0, 0)
    result = (str(out["label"][x]), int(out["k"][x]), float(out["g"][x]))
    print("combiner injected k_sl=k_tp=4 x=108 result=%r" % (result,))
    assert result == ("SL", 4, -5.0)
    assert out["g"].dtype == np.float64


# --------------------------------------------------------------------------- skips (Stage B / TIERET / blocked)
@pytest.mark.parametrize("name", ["F-G-TIERET-%d" % i for i in range(1, 6)] + ["F-H-TIERET-LINK"])
def test_TIERET_blocked(name):
    pytest.skip("%s: %s" % (name, TIERET_SKIP))


@pytest.mark.parametrize("name", [
    "F-G-HOLDWORST", "F-G-HOLD30-STOP", "F-G5-n_b-n_c", "F-X-LIQ-EXCL-b-UNPRICED_WORST_LIQ",
    "F-H-II-LIQ-F32", "F-H-T31", "F-H4B-EDGE", "F-H-III-HLE30",
])
def test_StageB_deferred(name):
    pytest.skip("%s: %s" % (name, STAGE_B_SKIP))


_GUARDED_HAPPY_SCRIPT = textwrap.dedent("""\
    import sys
    import numpy as np
    from research_fulln480m.tests.exit_fixtures import flat_window
    from research_fulln480m import fulln_exit
    m, _ = flat_window(1, t_e=0, p0=np.float64(100.0))
    out = fulln_exit.exit_guarded(m, 0, 1, 1)
    labs = sorted(set(out["label"].tolist()))
    ks = sorted(set(int(k) for k in out["k"]))
    n_nan = int(np.isnan(out["g"]).sum())
    print("LABELS=%r KS=%r NNAN=%d GDTYPE=%s" % (labs, ks, n_nan, out["g"].dtype))
    sys.exit(0)
""")


def test_exit_guarded_is_run_guarded(tmp_path):
    """exit_guarded happy path in a FRESH process (TESTFIX r1 F-T4 / RK C4).

    An earlier pytest.raises(ClosureViolation) latches the in-process violation latch by design
    (fulln_closure; no unlatch API), so an in-process exit_guarded would sys.exit(78). The happy
    path therefore runs in a fresh subprocess: sys.executable -B, PYTHONDONTWRITEBYTECODE=1, cwd
    and files only inside tmp_path, no network. Happy path ONLY; latching is unchanged."""
    script = tmp_path / "exit_guarded_happy.py"
    script.write_text(_GUARDED_HAPPY_SCRIPT)
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(PKG.parent)}
    for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    argv = [sys.executable, "-B", str(script)]
    (tmp_path / "exit_guarded_happy.argv.txt").write_text(repr(argv) + "\n")
    print("exit_guarded subprocess argv=%r cwd=%s" % (argv, tmp_path))
    proc = subprocess.run(argv, cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120)
    print("rc=%d stdout=%r stderr=%r" % (proc.returncode, proc.stdout, proc.stderr))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "LABELS=['TIME'] KS=[0] NNAN=%d GDTYPE=float64" % N_EXITS


def _empty_bar(m, s):
    """Mark index s empty (count 0, OHLC NaN). S-4 applies only to traded rows."""
    s = int(s)
    m.count[s] = 0
    m.open[s] = m.last[s] = m.low[s] = m.high[s] = np.nan


# --------------------------------------------------------------------------- ADD-7: survivor-fix value rows (print/bind: run-86)
# Reprs bound from mut/r63x-run-86/scratch_print_bind.out (START inodes 415561).
# PLUS1 r[h-1] and ANCHOR g / p0 printed then bound to the existing R5_* / "100.0" literals.


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_WEND_tp_only_at_k_h_minus_1(h, direction):
    """PLUS1 kill: extra>=1 and a traded bar at t_e+h; len(r)==len(g_adv)==h (the kill).

    Card WEND k=h-1 booking is kept. The r[h-1] repr is anti-regression only (run-86: all 24
    cases printed 5.00000000000167, equal to R5_HIT). Existing
    test_F_G_WEND_no_row_at_or_after_tx is unchanged and is not claimed as a PLUS1 kill.
    """
    t_e = 0
    m, p0 = flat_window(h, t_e=t_e, extra=1, p0=P0)
    set_bar(m, t_e + h, p0=p0)
    assert int(m.count[t_e + h]) >= 1
    assert np.isfinite(m.open[t_e + h]) and np.isfinite(m.last[t_e + h])
    base = copy_micro(m)
    last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
    poke_last_value(m, t_e + h - 1, direction, last_hit, p0=p0)
    tables = _ex().first_hit_tables(m, t_e, direction, h)
    assert len(tables["r"]) == h
    assert len(tables["g_adv"]) == h
    print("PLUS1 r[h-1] h=%s d=%s repr=%s" % (h, direction, _repr(tables["r"][h - 1])))
    assert _repr(tables["r"][h - 1]) == R5_HIT
    assert tables["r"][h - 1] == r_hit
    out = _core(m, t_e, direction, h)
    n_tp = 0
    for x in range(N_EXITS):
        tp_i = x // 96
        fires = tp_i >= 1 and r_hit >= np.float64(TP_BP[tp_i])
        if fires:
            assert tp_i == 1
            assert out["label"][x] == "TP" and out["k"][x] == h - 1
            assert out["g"][x] == r_hit
            n_tp += 1
        else:
            assert out["label"][x] == "TIME" and out["k"][x] == h - 1
            assert np.isnan(out["g"][x])
    assert n_tp == 96
    _assert_g_is(out, 96, r_hit, R5_HIT)
    _assert_neighbour_all_time(base, t_e + h - 1, direction, last_miss, r_miss, R5_MISS, t_e, h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_ANCHOR_te_is_Tplus2(h, direction):
    """ANCHOR kill: t_e=T+2, plain calls (no try/except), label/k/g asserted on plants A and B.

    Production must not raise S-7 on either plant. p0 and booked g reprs were printed in run-86
    (p0=100.0, plant A g=R5_HIT, plant B g=R5_SL_HIT) and are bound here.
    """
    T = 0
    t_e = T + 2
    m, p0 = flat_window(h, t_e=t_e, extra=0, p0=P0)
    _empty_bar(m, T + 1)
    last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
    poke_last_value(m, t_e + h - 1, direction, last_hit, p0=p0)
    tables = _ex().first_hit_tables(m, t_e, direction, h)
    print("ANCHOR p0 h=%s d=%s repr=%s" % (h, direction, _repr(tables["p0"])))
    assert _repr(tables["p0"]) == "100.0"
    assert tables["p0"] == m.open[t_e]
    out = _core(m, t_e, direction, h, T=T)
    n_tp = 0
    for x in range(N_EXITS):
        tp_i = x // 96
        fires = tp_i >= 1 and r_hit >= np.float64(TP_BP[tp_i])
        if fires:
            assert tp_i == 1
            assert out["label"][x] == "TP" and out["k"][x] == h - 1
            assert out["g"][x] == r_hit
            n_tp += 1
        else:
            assert out["label"][x] == "TIME" and out["k"][x] == h - 1
            assert np.isnan(out["g"][x])
    assert n_tp == 96
    _assert_g_is(out, 96, r_hit, R5_HIT)

    mb, p0b = flat_window(h, t_e=t_e, extra=0, p0=P0)
    _empty_bar(mb, T + 1)
    sl_hit, r_sl, sl_miss, r_sl_miss = _walk_checked(direction, -5.0, R5_SL_HIT, R5_SL_MISS)
    poke_last_value(mb, t_e, direction, sl_hit, p0=p0b)
    outb = _core(mb, t_e, direction, h, T=T)
    n_sl = 0
    for x in range(N_EXITS):
        sl_i = (x // 12) % 8
        fires = sl_i >= 1 and r_sl <= np.float64(-SL_BP[sl_i])
        if fires:
            assert sl_i == 1
            assert outb["label"][x] == "SL" and outb["k"][x] == 0
            assert outb["g"][x] == r_sl
            n_sl += 1
        else:
            assert outb["label"][x] == "TIME" and outb["k"][x] == h - 1
            assert np.isnan(outb["g"][x])
    assert n_sl == 96
    _assert_g_is(outb, 12, r_sl, R5_SL_HIT)


_PROD_DERIVE_SCRIPT = textwrap.dedent("""\
    import sys
    import numpy as np
    from research_fulln480m.tests.exit_fixtures import flat_window
    from research_fulln480m import fulln_exit
    m, _ = flat_window(120, t_e=1, p0=np.float64(100.0))
    fulln_exit.exit_guarded(m, 1, 1, 120, T=0, thesis_Tprime=60)
    print("UNEXPECTED_RETURN")
    sys.exit(0)
""")


def test_MI_PROD_DERIVE_thesis_kind_stop78_subprocess(tmp_path):
    """Subprocess production exit_guarded with a thesis kind, legal T'=T+60, h=120.

    GREEN (run-86): rc==78, stdout empty, stderr empty. TIERET and I.11 stay locked.
    No assertion on stderr content beyond emptiness (ADDENDUM-1 Q2 / RK 5ea4).
    """
    script = tmp_path / "prod_derive_sub.py"
    script.write_text(_PROD_DERIVE_SCRIPT)
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(PKG.parent)}
    for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    argv = [sys.executable, "-B", str(script)]
    (tmp_path / "prod_derive_sub.argv.txt").write_text(repr(argv) + "\n")
    print("PROD-DERIVE argv=%r cwd=%s" % (argv, tmp_path))
    proc = subprocess.run(argv, cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120)
    print("rc=%d stdout=%r stderr=%r" % (proc.returncode, proc.stdout, proc.stderr))
    assert proc.returncode == 78, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == ""


def test_MI_PROD_DERIVE_inprocess_S9_sibling():
    """In-process sibling: km>=1, same legal T'=T+60, ClosureViolation message contains S-9."""
    T = 0
    t_e = 1
    h = 120
    direction = 1
    assert k_max_i12(T, t_e, h) >= 1
    m, _ = flat_window(h, t_e=t_e, p0=P0)
    with pytest.raises(ClosureViolation) as ei:
        _ex().exit(m, t_e, direction, h, T=T, thesis_Tprime=T + 60)
    assert "S-9" in str(ei.value)


@pytest.mark.parametrize("h,direction", [(h, d) for h in HORIZONS if h >= 5 for d in DIRS])
def test_F_G_PAST_EMPTYEND(h, direction):
    """NXT value row: empty last window row, TP-5 last only at t_e+h. Production all TIME."""
    t_e = 0
    m, p0 = flat_window(h, t_e=t_e, extra=1, p0=P0)
    _empty_bar(m, t_e + h - 1)
    last_hit, r_hit, last_miss, r_miss = _walk_checked(direction, 5.0, R5_HIT, R5_MISS)
    set_bar(m, t_e + h, p0=p0)
    poke_last_value(m, t_e + h, direction, last_hit, p0=p0)
    out = _core(m, t_e, direction, h)
    _all_time(out, h)


@pytest.mark.parametrize("h,direction", HD)
def test_F_G_LIQBAR60(h, direction):
    """BAR60 value rows: LIQ extreme only at t_e+h in the same UTC minute as t_e+h-1 (row 1);
    LIQ only at T sharing a UTC minute with t_e in T+1..T+5 (row 2). Production all TIME.
    """
    base_s = 1
    t_e = 0
    abs_last = int(base_s) + t_e + h - 1
    abs_extra = int(base_s) + t_e + h
    assert (abs_last // 60) == (abs_extra // 60), (abs_last, abs_extra)
    m, p0 = flat_window(h, t_e=t_e, extra=1, base_s=base_s, p0=P0)
    set_bar(m, t_e + h, p0=p0)
    ext_fire, g_fire = ext_fire_greatest_le_floor(p0, direction)
    poke_ext_value(m, t_e + h, direction, ext_fire, p0=p0)
    out = _core(m, t_e, direction, h)
    _all_time(out, h)

    T = 0
    t_e2 = 2
    base2 = 0
    assert ((base2 + T) // 60) == ((base2 + t_e2) // 60)
    assert 1 <= (t_e2 - T) <= 5
    m2, p02 = flat_window(h, t_e=t_e2, extra=0, base_s=base2, p0=P0)
    ext2, g2 = ext_fire_greatest_le_floor(p02, direction)
    poke_ext_value(m2, T, direction, ext2, p0=p02)
    out2 = _core(m2, t_e2, direction, h, T=T)
    _all_time(out2, h)


@pytest.mark.parametrize("h,direction", [(h, d) for h in HORIZONS if h >= 5 for d in DIRS])
def test_F_G_LIQEMPTY_END(h, direction):
    """NEXTFILL value row: empty last window row, LIQ extreme only at t_e+h. Production all TIME."""
    t_e = 0
    m, p0 = flat_window(h, t_e=t_e, extra=1, p0=P0)
    _empty_bar(m, t_e + h - 1)
    set_bar(m, t_e + h, p0=p0)
    ext_fire, g_fire = ext_fire_greatest_le_floor(p0, direction)
    poke_ext_value(m, t_e + h, direction, ext_fire, p0=p0)
    out = _core(m, t_e, direction, h)
    _all_time(out, h)


def test_F_X_ONLYFILLED_EMBARGO_BLOCKED_and_n_a():
    """F-X-ONLYFILLED extension: [FILLED, EMBARGO, BLOCKED] at 3600/7000/7060.

    n=8000, h=60, all traded, valid[6900:7300]=False. Cursor asserts per R4; exit() once;
    direct count_n_a(rows)==0. Production tuples printed in run-86 and bound here.
    Written from the binding spec; HM's file was not copied.
    """
    n = 8000
    h = 60
    m = empty_micro(n, base_s=0)
    valid = np.ones(n, dtype=bool)
    has = np.zeros(n, dtype=bool)
    last_tr = np.full(n, -1, dtype=np.int64)
    for s in range(n):
        set_bar(m, s, p0=P0)
        has[s] = True
        last_tr[s] = s
    valid[6900:7300] = False
    cfg = pins_config()
    rows_out = decide_core(has, last_tr, valid, [3600, 7000, 7060], h, cfg, direction=1)
    rows = rows_out["rows"]
    labels = [r[1] for r in rows]
    print("ONLYFILLED rows=%r" % (rows,))
    assert labels == [FILLED, EMBARGO, BLOCKED], labels
    T0, lab0, te0, tx0 = rows[0]
    T1, lab1, te1, tx1 = rows[1]
    T2, lab2, te2, tx2 = rows[2]
    assert T0 == 3600 and lab0 == FILLED
    assert te0 == 3601 and tx0 == te0 + h and tx0 == 3661
    assert T1 == 7000 and lab1 == EMBARGO
    assert te1 == NO_TE and tx1 == T1 + 5 + h and tx1 == 7065
    assert T2 == 7060 and lab2 == BLOCKED
    assert te2 == NO_TE and tx2 == tx1 and tx2 == 7065
    n_a = _ex().count_n_a(rows)
    print("count_n_a=%s" % n_a)
    assert n_a == 0
    n_exit = 0
    for T, lab, t_e, t_x in rows:
        if lab != FILLED:
            continue
        n_exit += 1
        out = _ex().exit(m, t_e, 1, h, t_x=t_x)
        _all_time(out, h)
    assert n_exit == 1


@pytest.mark.parametrize(
    "pair,want",
    [
        ([3600, 3660], [(3600, FILLED, 3601, 3661), (3660, BLOCKED, NO_TE, 3661)]),
        ([3600, 3661], [(3600, FILLED, 3601, 3661), (3661, FILLED, 3662, 3722)]),
        ([3600, 3662], [(3600, FILLED, 3601, 3661), (3662, FILLED, 3663, 3723)]),
    ],
    ids=["tx-1", "tx", "tx+1"],
)
def test_F_X_cursor_admits_boundary_pairs(pair, want):
    """HM C1 / RK a525 E1: three SEPARATE pairs vs the original FILLED t_x=3661, via decide_core.

    Values printed in run-86 and bound here. No four-row compose.
    """
    n = 4000
    h = 60
    valid = np.ones(n, dtype=bool)
    has = np.ones(n, dtype=bool)
    last_tr = np.arange(n, dtype=np.int64)
    cfg = pins_config()
    rows = decide_core(has, last_tr, valid, list(pair), h, cfg, direction=1)["rows"]
    print("BOUNDARY pair=%s rows=%r" % (list(pair), rows))
    assert rows == want, (pair, rows)
    assert rows[0][3] == 3661
