"""B1 tests (C1 parts only): fulln_gross.py. Box synthetic micro only; no pinned module imported.

Binding: plan r3 33f3ec1e0e07 s7 C1 / s12 B1; Boss GO r2 3656382c8a5c; RK 22ee55488e0c (B1 SPLIT:
C7 micro builders + I1 encoding wait for plan r4; no code that sets exit returns to 0.0).
No file is written by these tests.
"""
import ast
import os
import sys

import numpy as np
import pytest

from research_fulln480m import fulln_closure as fc
from research_fulln480m import fulln_gross as fg

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fulln_gross.py")
LIQ = fg.LiqPolicy(liq_floor=-1.0e6, liq_fill=-1.0e6, ruling_sha="0123456789ab")  # TEST FIXTURE ONLY


@pytest.fixture(autouse=True)
def _latch_reset():
    fc._STATE["latch"] = None
    yield
    fc._STATE["latch"] = None


def _base(n_bars=91, seed=7):
    """Synthetic micro with the phase_b_run L244-L274 key layout and NON-zero costs/funding."""
    rng = np.random.default_rng(seed)
    m = {
        "dims": {"n_ch": 1, "n_th": 1, "n_h": 1, "n_exits": 768, "n_rows": 1},
        "feat": rng.standard_normal((n_bars, 1)),
        "minute": np.arange(n_bars, dtype=np.int32),
        "code": (np.arange(n_bars, dtype=np.int32) % 4).astype(np.int32),
        "fold": np.zeros(n_bars, np.int32),
        "thresh": np.zeros((1, 1), np.float64),
        "horizons": np.asarray([1800], np.int32),
        "won": np.zeros((1, 4), np.int32),
        "wfl": np.zeros((1, 4), np.int32),
        "fund_off": rng.integers(0, 100, n_bars).astype(np.int32),
        "fund_rate": rng.standard_normal(n_bars),
        "cost_bp": 7.5,
        "liq_floor": -500.0,
        "liq_fill": -600.0,
        "costs": {"cost_bp": 7.5, "p_cost": 7.5, "liq_floor": -500.0, "liq_fill": -600.0,
                  "p_liq_floor": -500.0, "p_liq_fill": -600.0},
        "eng_micro": True,
        "feature_source": "synthetic_template",
        "production_market_data": False,
    }
    shapes = {"Ttp": 8, "Tsl": 8, "Ttr": 4, "Tth": 3}
    for k, s in shapes.items():
        m[k] = rng.integers(0, 50, (1, 2, n_bars, s)).astype(np.int32)
        m["R" + k[1:]] = rng.standard_normal((1, 2, n_bars, s)).astype(np.float32)
    m["Tt"] = rng.integers(0, 50, (1, 2, n_bars)).astype(np.int32)
    m["Rt"] = rng.standard_normal((1, 2, n_bars)).astype(np.float32)
    return m


def _is78(excinfo):
    return getattr(excinfo.value, "exit_code", None) == 78


# ---------------------------------------------------------------- T1.1 - T1.5 gross terms
def test_t1_1_costs_and_funding_are_gross():
    out = fg.build_gross_micro(_base(), LIQ)
    assert out["cost_bp"] == 0.0
    assert out["costs"]["cost_bp"] == 0.0 and out["costs"]["p_cost"] == 0.0
    assert out["fund_rate"].dtype == np.float64 and np.all(out["fund_rate"] == 0.0)
    assert out["fund_off"].dtype == np.int32 and np.all(out["fund_off"] == 10 ** 9)
    assert out["fund_rate"].shape == (91,) and out["fund_off"].shape == (91,)
    assert out["gross"] is True
    fg.assert_gross(out)


def test_t1_2_liq_values_come_only_from_the_policy():
    out = fg.build_gross_micro(_base(), LIQ)
    for k in ("liq_floor", "p_liq_floor"):
        assert out["costs"][k] == LIQ.liq_floor
    for k in ("liq_fill", "p_liq_fill"):
        assert out["costs"][k] == LIQ.liq_fill
    assert out["liq_floor"] == LIQ.liq_floor and out["liq_fill"] == LIQ.liq_fill
    assert out["liq_ruling_sha"] == LIQ.ruling_sha


def test_t1_3_no_liq_policy_refuses_q3_open():
    with pytest.raises(fc.ClosureViolation) as e:
        fg.build_gross_micro(_base())
    assert _is78(e) and "Q3" in e.value.reason
    with pytest.raises(fc.ClosureViolation):
        fg.build_gross_micro(_base(), {"liq_floor": 0.0, "liq_fill": 0.0})


@pytest.mark.parametrize("sha,floor", [("XYZ", -1.0), ("0123", -1.0), ("0123456789ab", float("nan")),
                                       ("0123456789ab", True)])
def test_t1_4_liq_policy_validation(sha, floor):
    with pytest.raises(fc.ClosureViolation):
        fg.LiqPolicy(liq_floor=floor, liq_fill=-1.0, ruling_sha=sha)


def test_t1_5_assert_gross_catches_every_cost_term():
    good = fg.build_gross_micro(_base(), LIQ)
    cases = []
    m = dict(good); m["cost_bp"] = 0.5; cases.append(m)
    m = dict(good); m["cost_bp"] = True; cases.append(m)
    m = dict(good); m["costs"] = dict(good["costs"], p_cost=1e-9); cases.append(m)
    m = dict(good); m["costs"] = dict(good["costs"], cost_bp=-0.1); cases.append(m)
    m = dict(good); m["costs"] = {k: v for k, v in good["costs"].items() if k != "p_cost"}; cases.append(m)
    m = dict(good); m["costs"] = dict(good["costs"], fee_bp=0.0); cases.append(m)
    fr = good["fund_rate"].copy(); fr[3] = 1e-6
    m = dict(good); m["fund_rate"] = fr; cases.append(m)
    fo = good["fund_off"].copy(); fo[0] = 5
    m = dict(good); m["fund_off"] = fo; cases.append(m)
    m = dict(good); m["fund_rate"] = good["fund_rate"].astype(np.float32); cases.append(m)
    m = dict(good); m["slippage_bp"] = 0.0; cases.append(m)
    m = dict(good); m["gross"] = False; cases.append(m)
    for i, bad in enumerate(cases):
        with pytest.raises(fc.ClosureViolation):
            fg.assert_gross(bad)
        fc._STATE["latch"] = None


# ---------------------------------------------------------------- T1.6 - T1.8 pass-through
def test_t1_6_return_arrays_pass_through_unchanged():
    base = _base()
    before = {k: base[k].tobytes() for k in fg.RETURN_KEYS}
    out = fg.build_gross_micro(base, LIQ)
    for k in fg.RETURN_KEYS:
        assert out[k] is base[k], k
        assert out[k].tobytes() == before[k], k
    for k in ("feat", "minute", "code", "fold", "thresh", "horizons", "won", "wfl", "dims"):
        assert out[k] is base[k], k


def test_t1_7_base_micro_not_mutated():
    base = _base()
    fr, fo = base["fund_rate"].copy(), base["fund_off"].copy()
    costs = dict(base["costs"])
    fg.build_gross_micro(base, LIQ)
    assert np.array_equal(base["fund_rate"], fr) and np.array_equal(base["fund_off"], fo)
    assert base["costs"] == costs and base["cost_bp"] == 7.5


def test_t1_8_unknown_or_missing_keys_refuse():
    b = _base(); b["mystery"] = 1
    with pytest.raises(fc.ClosureViolation):
        fg.build_gross_micro(b, LIQ)
    fc._STATE["latch"] = None
    b = _base(); b["costs"] = dict(b["costs"], fee_bp=3.0)
    with pytest.raises(fc.ClosureViolation):
        fg.build_gross_micro(b, LIQ)
    fc._STATE["latch"] = None
    b = _base(); del b["Rsl"]
    with pytest.raises(fc.ClosureViolation):
        fg.build_gross_micro(b, LIQ)
    fc._STATE["latch"] = None
    b = _base(); del b["minute"]
    with pytest.raises(fc.ClosureViolation):
        fg.build_gross_micro(b, LIQ)


# ---------------------------------------------------------------- T7.R61c refusals (C7 NOT built)
@pytest.mark.parametrize("fn", ["unpriced_counts", "unpriceable_exit_returns", "unpriceable_entry_nofill"])
def test_t1_9_r61c_entry_points_refuse_78(fn):
    with pytest.raises(fg.R61cNotImplemented) as e:
        getattr(fg, fn)(np.zeros(3, bool))
    assert _is78(e)
    assert isinstance(e.value, NotImplementedError) and isinstance(e.value, fc.ClosureViolation)
    assert "2ecf4832af48" in e.value.reason and "awaiting plan r4" in e.value.reason


def test_t1_10_built_micro_is_not_launchable_until_r4():
    out = fg.build_gross_micro(_base(), LIQ)
    assert out[fg.R61C_STATUS_KEY] == "NOT_IMPLEMENTED_AWAITING_PLAN_R4"
    with pytest.raises(fg.R61cNotImplemented) as e:
        fg.assert_r61c_ready(out)
    assert _is78(e)
    fc._STATE["latch"] = None
    forged = dict(out); forged[fg.R61C_STATUS_KEY] = "IMPLEMENTED"
    with pytest.raises(fg.R61cNotImplemented):
        fg.assert_r61c_ready(forged)


@pytest.mark.parametrize("key", ["exit_unpriced_mask", "entry_nofill", "worst_price_R", "hole_bars"])
def test_t1_11_unpriceable_inputs_refuse_awaiting_r4(key):
    b = _base(); b[key] = np.zeros(3, bool)
    with pytest.raises(fg.R61cNotImplemented) as e:
        fg.build_gross_micro(b, LIQ)
    assert "awaiting plan r4" in e.value.reason


def test_t1_12_run_guarded_exits_78():
    with pytest.raises(SystemExit) as e:
        fc.run_guarded(lambda: fg.unpriced_counts())
    assert e.value.code == 78


# ---------------------------------------------------------------- static checks on the source
def _tree():
    with open(SRC, "r", encoding="utf-8") as f:
        return ast.parse(f.read())


def test_t1_13_never_calls_refusing_or_cost_paths():
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
    for bad in ("build_micro", "realistic_costs", "build_tiny_micro", "phase_b_run", "gpu_eval",
                "gpu_kernel", "ext2_search", "phase_b_search", "load_closure"):
        assert bad not in names, bad


def test_t1_14_imports_are_allowlisted():
    allowed = {"math", "re", "dataclasses", "numpy", "research_fulln480m", "research_fulln480m.fulln_closure"}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name in allowed, a.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed, node.module


def test_t1_15_no_store_into_return_arrays():
    """No subscript store with a literal return key, no in-place fill / zeros_like / nan_to_num."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            sl = node.slice
            if isinstance(sl, ast.Constant):
                assert sl.value not in fg.RETURN_KEYS, sl.value
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("fill", "zeros_like", "nan_to_num", "full_like", "putmask", "copyto"), node.attr
