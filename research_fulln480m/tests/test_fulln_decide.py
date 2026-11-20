"""RED-first tests for the B-R63D decision layer (fulln_decide.py), card r2.2 24430bc38561.

Scope D1 (RK cfb28db913dd): labels BLOCKED / EMBARGO / UNFRESH / NO_FILL / FILLED, t_e and the
SCHEDULED t_x = t_e + h only. No exit price, X4, UNPRICED_WORST, LIQ, thesis, BR, H1-H10.
Synthetic injected arrays only (GC-3, e38a L77). Every row runs at the stated horizons, long AND
short; the layer is direction-agnostic, so both directions must give identical output.
Tie rows T1 / T3 / T4 run ONLY against the test-only, verdict-only tests/ref_clock_oracle.py
(RK 4de6 O1-O4); production refuses any non-X1 clock (STOP 78).
"""
import ast
import importlib
import inspect
import os
import subprocess
import sys
import types

import numpy as np
import pytest

from research_fulln480m.fulln_closure import ClosureViolation
from research_fulln480m.tests import decide_fixtures as F
from research_fulln480m.tests import ref_clock_oracle as O
from research_fulln480m import fulln_decide as fd

H12 = F.H12
DAY = F.DAY
HD = [(h, d) for h in H12 for d in F.DIRECTIONS]
HD_IDS = ["h%d-%s" % (h, "L" if d == 1 else "S") for h, d in HD]
CLOSURE_SHA_AT_BASE = "f5b6464023a20b69dd3725b47d3ec87f09368c4956f293ac46a7bf6c3d0c1eb4"
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(PKG_DIR)


def _pins():
    return importlib.import_module("research_fulln480m.fulln_decide_pins")


def core(has, last_tr, valid, Ts, h, d, cfg=None, **kw):
    """Pure core (any integer t_d), run for direction d AND -d; outputs must be identical."""
    cfg = F.config() if cfg is None else cfg
    r = fd.decide_core(has, last_tr, valid, list(Ts), h, cfg, direction=d, **kw)
    r2 = fd.decide_core(has, last_tr, valid, list(Ts), h, cfg, direction=-d, **kw)
    assert r["rows"] == r2["rows"]
    assert r["counts"] == r2["counts"]
    assert r["fold_counts"] == r2["fold_counts"]
    for row in r["rows"]:
        assert all(type(v) is int for v in (row[0], row[2], row[3]))  # exact integers only
    assert sum(r["counts"][k] for k in fd.LABELS) == len(list(Ts))   # every decision counted
    return r


def prod(has, last_tr, valid, Ts, h, d, **kw):
    r = fd.decide(has, last_tr, valid, list(Ts), h, direction=d, **kw)
    r2 = fd.decide(has, last_tr, valid, list(Ts), h, direction=-d, **kw)
    assert r["rows"] == r2["rows"] and r["counts"] == r2["counts"]
    return r


def labels(r):
    return [row[1] for row in r["rows"]]


def stop(fn, *a, **kw):
    with pytest.raises(ClosureViolation) as ei:
        fn(*a, **kw)
    assert ei.value.exit_code == 78
    return ei.value


def _sub(code):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=REPO_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


# ------------------------------------------------------------------ F-H12, pins, start-up
def test_F_H12_exactly_12_horizons():
    assert tuple(_pins().HORIZONS_12) == H12
    assert len(set(_pins().HORIZONS_12)) == 12
    assert fd.HORIZONS_12 == H12
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    for bad_h in (0, 2, 59, 61, 28800):
        stop(fd.decide_core, has, lt, valid, [3600], bad_h, F.config())


def test_F_R63D_PINS_ASSERT_literals_and_startup():
    p = _pins()
    assert p.EXIT_CLOCK == "X1" and p.STALE_S == 600 and type(p.STALE_S) is int
    assert tuple(p.ENTRY_WINDOW) == (1, 5)
    assert p.CLOSURE_SHA256 == CLOSURE_SHA_AT_BASE
    fd.check_pins(p)
    for bad in (dict(STALE_S=601, EXIT_CLOCK="X1"), dict(STALE_S=599, EXIT_CLOCK="X1"),
                dict(STALE_S=600, EXIT_CLOCK="EXT2"), dict(EXIT_CLOCK="X1"), dict(STALE_S=600),
                dict(STALE_S="600", EXIT_CLOCK="X1"), dict(STALE_S=True, EXIT_CLOCK="X1")):
        stop(fd.check_pins, types.SimpleNamespace(**bad))


def test_F_R63D_PINS_ASSERT_pins_file_self_check():
    """fulln_decide_pins.py asserts STALE_S == 600 and EXIT_CLOCK == 'X1' at import (exec of its
    own bytes with one literal swapped; the file itself is only read)."""
    assert "decide_core" in vars(fd)
    with open(_pins().__file__, "r", encoding="utf-8") as f:
        src = f.read()
    exec(compile(src, "pins_ok", "exec"), {})
    for old, new in (('STALE_S = 600', 'STALE_S = 601'), ('EXIT_CLOCK = "X1"', 'EXIT_CLOCK = "EXT2"')):
        assert src.count(old) == 1
        with pytest.raises(SystemExit) as ei:
            exec(compile(src.replace(old, new), "pins_bad", "exec"), {})
        assert ei.value.code == 78


@pytest.mark.parametrize("bad", [599, 601, F.MISSING, "600", 600.0, True, None])
def test_F_STALE_PIN_stop78(bad):
    stop(fd.check_startup_config, F.config(STALE_S=bad))
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    e = stop(fd.decide_core, has, lt, valid, [3600], 60, F.config(STALE_S=bad))
    assert "STALE" in e.reason


def test_F_STALE_PIN_600_runs():
    fd.check_startup_config(F.config())
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    assert labels(core(has, lt, valid, [3600], 60, 1)) == ["FILLED"]


@pytest.mark.parametrize("bad", ["EXT2", "x1", "X6", F.MISSING, None, ""])
def test_F_F3_CLOCKSTOP(bad):
    e = stop(fd.check_startup_config, F.config(EXIT_CLOCK=bad))
    assert "CLOCK" in e.reason
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    stop(fd.decide_core, has, lt, valid, [3600], 60, F.config(EXIT_CLOCK=bad))
    # T1 / T3 / T4 use the reference ext2-clock oracle and give BLOCK (A9 r10 L238)
    assert O.verdict(O.CLOCK_EXT2, 3660, O.prior_cursor(O.CLOCK_EXT2, 3600, 60))[1] == O.BLOCK


def test_F_F3_CLOCKSTOP_x1_runs():
    fd.check_startup_config(F.config(EXIT_CLOCK="X1"))


@pytest.mark.parametrize("cfg", [dict(ENTRY_WINDOW=(1, 600)), dict(ENTRY_WINDOW=(0, 599)),
                                 dict(ENTRY_WINDOW=(1, 4)), dict(ENTRY_WINDOW=F.MISSING),
                                 dict(EXIT_CLOCK="EXT2")], ids=["ents600", "ent0_599", "w4", "missing", "ext2"])
def test_F_B_NEEDS_A_stop78(cfg):
    """EM8: R63-B in force with a non-X1 clock or a non-E1 window -> STOP 78 before any booking."""
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    stop(fd.decide_core, has, lt, valid, [3600], 60, F.config(**cfg))
    stop(fd.check_em8, F.config(**cfg))


def test_F_B_NEEDS_A_x1_e1_runs():
    fd.check_em8(F.config())
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    assert labels(core(has, lt, valid, [3600], 60, 1)) == ["FILLED"]


# ------------------------------------------------------------------ closure sha (RK 7a06 C.5)
def test_F_R63D_CLOSURE_SHA_match_at_base():
    assert os.path.basename(fd.CLOSURE_PATH) == "fulln_closure.py"
    assert os.path.dirname(fd.CLOSURE_PATH) == PKG_DIR
    assert fd.check_closure_sha() == CLOSURE_SHA_AT_BASE


def test_F_R63D_CLOSURE_SHA_mismatch_stop78(tmp_path):
    e = stop(fd.check_closure_sha, pinned="0" * 64)
    assert "CLOSURE" in e.reason
    other = tmp_path / "fulln_closure_other.py"
    other.write_bytes(b"# not the pinned closure\n")
    stop(fd.check_closure_sha, path=str(other))
    stop(fd.check_closure_sha, path=str(tmp_path / "absent.py"))


def test_F_R63D_CLOSURE_SHA_exit_code_78_subprocess():
    code = ("from research_fulln480m import fulln_decide as fd\n"
            "from research_fulln480m.fulln_closure import run_guarded\n"
            "run_guarded(lambda: fd.check_closure_sha(pinned='0' * 64))\n")
    r = _sub(code)
    assert r.returncode == 78, (r.stdout, r.stderr)
    ok = _sub("from research_fulln480m import fulln_decide as fd\n"
              "from research_fulln480m.fulln_closure import run_guarded\n"
              "print(run_guarded(fd.check_closure_sha))\n")
    assert ok.returncode == 0 and ok.stdout.strip() == CLOSURE_SHA_AT_BASE, (ok.stdout, ok.stderr)


# ------------------------------------------------------------------ oracle isolation (RK 4de6 O1)
def _oracle_imports(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    hits = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [a.name for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
                "import_module", "__import__"):
            names = [getattr(a, "value", "") for a in node.args if isinstance(a, ast.Constant)]
        hits += [n for n in names if isinstance(n, str) and ("ref_clock_oracle" in n or n.endswith(".tests")
                                                             or n == "tests")]
    return hits


def test_F_R63D_ORACLE_ISOLATION_static():
    assert "decide" in vars(fd) and "decide_core" in vars(fd)
    assert _oracle_imports(os.path.join(PKG_DIR, "fulln_decide.py")) == []
    assert _oracle_imports(os.path.join(PKG_DIR, "fulln_decide_pins.py")) == []
    assert os.path.dirname(os.path.abspath(O.__file__)) == os.path.join(PKG_DIR, "tests")


def test_F_R63D_ORACLE_ISOLATION_runtime_transitive():
    assert "decide" in vars(fd)
    code = ("import sys\n"
            "import research_fulln480m.fulln_decide_pins\n"
            "import research_fulln480m.fulln_decide\n"
            "bad = sorted(m for m in sys.modules if 'ref_clock_oracle' in m or m.startswith('research_fulln480m.tests'))\n"
            "print('BAD=' + ','.join(bad))\n")
    r = _sub(code)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "BAD=", r.stdout


def test_oracle_O2_verdict_only_no_pricing():
    """O2: the oracle exposes clock identity and verdict only; no px / last_tr / nxt symbol."""
    assert "decide" in vars(fd)
    with open(O.__file__, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"px", "last_tr", "nxt", "np", "numpy", "p0", "Rt", "xs"}
    public = sorted(k for k in vars(O) if not k.startswith("_") and callable(vars(O)[k]))
    assert public == ["clock_identity", "prior_cursor", "verdict"]
    ident, v = O.verdict(O.CLOCK_X1, 10, 10)
    assert set(ident) == {"clock", "exit_instant", "entry_window_s"} and v in (O.ENTER, O.BLOCK)


# ------------------------------------------------------------------ off-grid production (HM C6)
def test_F_R63D_OFFGRID_PROD_stop78():
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601, 3631))
    for Ts in ([3630], [3601], [3659], [1], [3600, 3630]):
        e = stop(fd.decide, has, lt, valid, Ts, 60)
        assert "GRID" in e.reason
    r = prod(has, lt, valid, [3600], 60, 1)
    assert labels(r) == ["FILLED"]


def test_F_R63D_OFFGRID_PROD_no_bypass_keyword():
    params = list(inspect.signature(fd.decide).parameters)
    assert params == ["has", "last_tr", "valid", "decisions", "h", "window_ends", "direction", "folds"]


def test_F_R63D_OFFGRID_PROD_exit_code_78_subprocess():
    assert "decide" in vars(fd)
    code = ("import numpy as np\n"
            "from research_fulln480m import fulln_decide as fd\n"
            "S = 86400; has = np.zeros(S, bool); has[[3570, 3601]] = True\n"
            "lt = np.maximum.accumulate(np.where(has, np.arange(S), -1))\n"
            "valid = np.ones(S, bool)\n"
            "fd.decide_guarded(has, lt, valid, [3630], 60)\n")
    r = _sub(code)
    assert r.returncode == 78, (r.stdout, r.stderr)


# ------------------------------------------------------------------ F-A9-E1 / BND-WIN (all 12 h)
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_A9_E1_and_BND_WIN(h, d):
    T = 3600
    def one(trades):
        has, lt, valid = F.arrays(DAY, trades=trades)
        return core(has, lt, valid, [T], h, d)
    r = one((T - 30, T + 1))
    assert r["rows"] == [(T, "FILLED", T + 1, T + 1 + h)]
    r = one((T - 30, T + 3))                      # rows T+1, T+2 empty: delayed fill
    assert r["rows"] == [(T, "FILLED", T + 3, T + 3 + h)]
    r = one((T - 30, T + 5))                      # only trade in T..T+6 at T+5 (kills [T+1,T+4])
    assert r["rows"] == [(T, "FILLED", T + 5, T + 5 + h)]
    for trades in ((T - 30, T), (T - 30, T + 6), (T - 30, T, T + 6)):   # trades only at T / T+6
        r = one(trades)
        assert r["rows"] == [(T, "NO_FILL", fd.NO_TE, T + 5 + h)]
        assert r["counts"]["NO_FILL"] == 1 and r["counts"]["DENOMINATOR"] == 1
        assert r["counts"]["UNFRESH"] == 0 and r["counts"]["FILLED"] == 0


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_MA9_FRESH_ENTCLAUSE_row(h, d):
    """Fresh, no trade in T..T+700 -> NO_FILL, UNFRESH +0 (plan L719 (3); eda4 A1)."""
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 701))
    r = core(has, lt, valid, [T], h, d)
    assert labels(r) == ["NO_FILL"] and r["counts"]["UNFRESH"] == 0 and r["counts"]["NO_FILL"] == 1


# ------------------------------------------------------------------ cursor rows
@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_CUR60_CUR120(d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, T + 61, T + 121))
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)
    assert r["rows"] == [(T, "FILLED", T + 1, T + 61), (T + 60, "BLOCKED", fd.NO_TE, T + 61),
                         (T + 120, "FILLED", T + 121, T + 181)]
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, T + 121, T + 181))
    r = core(has, lt, valid, [T, T + 120, T + 180], 120, d)
    assert labels(r) == ["FILLED", "BLOCKED", "FILLED"] and r["rows"][1][3] == T + 121
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 30, T + 61, T + 121))     # NO_FILL at T
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)
    assert r["rows"][0] == (T, "NO_FILL", fd.NO_TE, T + 65)
    assert labels(r) == ["NO_FILL", "BLOCKED", "FILLED"]
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 5, T + 61))                # h = 30, fill T+5
    r = core(has, lt, valid, [T, T + 60], 30, d)
    assert labels(r) == ["FILLED", "FILLED"] and r["rows"][0][3] == T + 35


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_DKEY(d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, T + 62, T + 121))
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)
    assert labels(r) == ["FILLED", "BLOCKED", "FILLED"]
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 2, T + 290, T + 304))
    r = core(has, lt, valid, [T, T + 300], 300, d)
    assert labels(r) == ["FILLED", "BLOCKED"] and r["rows"][0][3] == T + 302
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 5, T + 61))
    r = core(has, lt, valid, [T, T + 60], 30, d)
    assert labels(r) == ["FILLED", "FILLED"]


def _g_run(h, d, delay):
    """Decision at T0 (fill delay d = 1..5, or NO_FILL), then one decision every minute."""
    T0 = 600
    G = F.G_TABLE[h]
    trades = {T0 - 30}
    if delay != "NF":
        trades.add(T0 + delay)
    for k in range(1, G + 2):
        trades.add(T0 + 60 * k + 1)
    has, lt, valid = F.arrays(DAY, trades=sorted(trades))
    Ts = [T0 + 60 * k for k in range(0, G + 2)]
    return core(has, lt, valid, Ts, h, d), G


@pytest.mark.parametrize("delay", [1, 2, 3, 4, 5, "NF"])
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_A9_G_and_BND_G(h, d, delay):
    r, G = _g_run(h, d, delay)
    labs = labels(r)
    assert labs[0] == ("NO_FILL" if delay == "NF" else "FILLED")
    first = next(k for k in range(1, len(labs)) if labs[k] != "BLOCKED")
    assert first == G                                  # m + G(h) admitted (machine table)
    assert all(lab == "BLOCKED" for lab in labs[1:G])  # m + G(h) - 1 (and earlier) BLOCKED
    for row in r["rows"][1:G]:
        assert row[3] == r["rows"][0][3]               # BLOCKED never moves the cursor


def test_F_A9_G_table_matches_C3():
    got = {}
    for h in H12:
        gs = set()
        for dl in (1, 2, 3, 4, 5, "NF"):
            labs = labels(_g_run(h, 1, dl)[0])
            gs.add(next(k for k in range(1, len(labs)) if labs[k] != "BLOCKED"))
        assert len(gs) == 1
        got[h] = gs.pop()
    assert got == F.G_TABLE


# ------------------------------------------------------------------ embargo
@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_EMBARGO_rows(d):
    T = 3600
    for h in (5, 60):
        for lv in (T + 5, T + 5 + h - 2):            # last valid second inside [T+5, T+5+h-1)
            has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1), valid_until=lv)
            r = core(has, lt, valid, [T], h, d)
            assert r["rows"] == [(T, "EMBARGO", fd.NO_TE, T + 5 + h)]
            assert r["counts"]["EMBARGO"] == 1 and r["counts"]["DENOMINATOR"] == 1
            assert r["counts"]["FILLED"] == 0
        has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1), valid_until=T + 5 + h - 1)
        assert labels(core(has, lt, valid, [T], h, d)) == ["FILLED"]      # boundary: not embargoed
    for h, lv, exp, cur in ((5, T + 5, "EMBARGO", T + 10), (5, T + 9, "FILLED", T + 6),
                            (60, T + 60, "EMBARGO", T + 65), (60, T + 64, "FILLED", T + 61)):
        has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1), valid_until=lv)   # (E-a)..(E-d)
        r = core(has, lt, valid, [T], h, d)
        assert labels(r) == [exp] and r["rows"][0][3] == cur


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_BND_EM2(h, d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1), valid_until=T + 5 + h - 1)
    assert labels(core(has, lt, valid, [T], h, d)) == ["FILLED"]
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1), valid_until=T + 5 + h - 2)
    r = core(has, lt, valid, [T], h, d)
    assert r["rows"] == [(T, "EMBARGO", fd.NO_TE, T + 5 + h)]


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_BND_EARLY_over_embargo_band(h, d):
    T = 3600
    for lv in range(T + h, T + h + 4):
        has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1), valid_until=lv)
        r = core(has, lt, valid, [T], h, d)
        assert labels(r) == ["EMBARGO"] and r["rows"][0][3] == T + 5 + h


# ------------------------------------------------------------------ freshness
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_F1_FRESHB_and_BND_FRESH(h, d):
    T = 3600
    for gap, exp in ((599, "FILLED"), (600, "FILLED"), (601, "UNFRESH")):
        has, lt, valid = F.arrays(DAY, trades=(T - gap, T + 1))
        r = core(has, lt, valid, [T], h, d)
        assert labels(r) == [exp]
        if exp == "UNFRESH":
            assert r["rows"][0] == (T, "UNFRESH", fd.NO_TE, T + 5 + h)
            assert r["counts"]["UNFRESH"] == 1 and r["counts"]["NO_FILL"] == 0
            assert r["counts"]["DENOMINATOR"] == 1
    has, lt, valid = F.arrays(DAY, trades=(301,))                  # no trade before T, m = 5
    assert labels(core(has, lt, valid, [300], h, d)) == ["UNFRESH"]
    has, lt, valid = F.arrays(DAY, trades=(0, 1))                  # m = 0 with a trade at second 0
    assert labels(core(has, lt, valid, [0], h, d)) == ["UNFRESH"]
    has, lt, valid = F.arrays(DAY, trades=(T + 1,), invalid=((T - 1, T),))   # GC-3 row
    lt = lt.copy()
    lt[T - 1] = T - 1                                              # injected: T - lt = 1
    assert valid[T] and not valid[T - 1]
    assert labels(core(has, lt, valid, [T], h, d)) == ["UNFRESH"]


@pytest.mark.parametrize("T", [600, 72000, 86400, 172860])
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_MA9_FRESH_VALIDT_kill_row(h, d, T):
    """PC5 code kill: valid[T - 1] false, T - lt = 1, valid[T] TRUE, arrays injected (GC-3)."""
    has, lt, valid = F.arrays(3 * DAY, trades=(T + 1,), invalid=((T - 1, T),))
    lt = lt.copy()
    lt[T - 1] = T - 1
    assert bool(valid[T]) and not bool(valid[T - 1]) and T - int(lt[T - 1]) == 1
    r = core(has, lt, valid, [T], h, d)
    assert r["rows"] == [(T, "UNFRESH", fd.NO_TE, T + 5 + h)]


@pytest.mark.parametrize("T", [600, 72000, 86400, 172860])
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_MA9_FRESH_VALIDT_negative_control(h, d, T):
    """valid[T] ALSO false: EMBARGO under the rule AND under the mutant (expected to SURVIVE)."""
    has, lt, valid = F.arrays(3 * DAY, trades=(T + 1,), invalid=((T - 1, T + 1),))
    lt = lt.copy()
    lt[T - 1] = T - 1
    r = core(has, lt, valid, [T], h, d)
    assert labels(r) == ["EMBARGO"]
    assert r["counts"]["EMBARGO_AND_STALE"] == 1 and r["counts"]["FAILDAY_IN_EMBARGO"] == 1


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_UNFRESH(d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 601, T + 1, T + 70, T + 121))
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)
    assert r["rows"] == [(T, "UNFRESH", fd.NO_TE, T + 65), (T + 60, "BLOCKED", fd.NO_TE, T + 65),
                         (T + 120, "FILLED", T + 121, T + 181)]
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, T + 70, T + 121), invalid=((T - 1, T),))
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)            # valid[T - 1] false
    assert labels(r) == ["UNFRESH", "BLOCKED", "FILLED"]


# ------------------------------------------------------------------ F.1 order
@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_F1_ORDER_Oa_stale_blocked(d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, T + 121))
    lt = lt.copy()
    lt[T + 59] = T + 60 - 700                       # GC-3: T+60 stale (700 s), injected
    lt[T + 119] = T + 100                           # T+120 fresh
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)
    assert r["rows"][1] == (T + 60, "BLOCKED", fd.NO_TE, T + 61)
    assert r["counts"]["BLOCKED"] == 1 and r["counts"]["UNFRESH"] == 0
    assert r["rows"][2] == (T + 120, "FILLED", T + 121, T + 181)


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_F1_ORDER_Ob_embargo_blocked(d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, T + 61, T + 121), valid_until=T + 123)
    r = core(has, lt, valid, [T, T + 60, T + 120], 60, d)
    assert r["rows"][0] == (T, "FILLED", T + 1, T + 61)
    assert r["rows"][1] == (T + 60, "BLOCKED", fd.NO_TE, T + 61)
    assert r["counts"]["EMBARGO"] == 1
    assert r["rows"][2] == (T + 120, "EMBARGO", fd.NO_TE, T + 185)


def _after_kind(kind, T, h):
    """Arrays where the decision at T is NO_FILL / UNFRESH / EMBARGO (t_x = T + 5 + h)."""
    if kind == "NO_FILL":                       # T+1..T+5 empty for every h (first later trade T+h+7)
        trades, vu = (T - 30, T + h + 7, T + h + 8), None
    elif kind == "UNFRESH":
        trades, vu = (T - 700, T + 1, T + h + 7, T + h + 8), None
    else:
        trades, vu = (T - 30, T + 1, T + h + 7, T + h + 8), T + h + 3
    return F.arrays(DAY, trades=trades, valid_until=vu)


@pytest.mark.parametrize("kind", ["NO_FILL", "UNFRESH", "EMBARGO"])
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_BND_CUR_N_offgrid(h, d, kind):
    """SYNTHETIC OFF-GRID instants through the pure core only (GS MAJOR-1; O-c generalised)."""
    T = 3600
    has, lt, valid = _after_kind(kind, T, h)
    tx = T + 5 + h
    for off, blocked in ((3, True), (4, True), (5, False), (6, False)):
        r = core(has, lt, valid, [T, T + h + off], h, d)
        assert r["rows"][0] == (T, kind, fd.NO_TE, tx)
        if blocked:
            assert r["rows"][1] == (T + h + off, "BLOCKED", fd.NO_TE, tx)
        else:
            assert r["rows"][1][1] != "BLOCKED"
    assert O.verdict(O.CLOCK_X1, tx, tx)[1] == O.ENTER           # T + h + 5: TIE, X1 ENTERS
    assert O.verdict(O.CLOCK_EXT2, tx, tx)[1] == O.BLOCK         # reference non-X1 verdict


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_F1_ORDER_Oc_offgrid(d):
    T, h = 3600, 60
    has, lt, valid = _after_kind("NO_FILL", T, h)
    got = [labels(core(has, lt, valid, [T, T + h + off], h, d))[1] for off in (3, 4, 5, 6)]
    assert got[0] == "BLOCKED" and got[1] == "BLOCKED"
    assert got[2] != "BLOCKED" and got[3] != "BLOCKED"


@pytest.mark.parametrize("dl", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_BND_CUR_F_offgrid(h, d, dl):
    T = 600
    tx = T + dl + h
    has, lt, valid = F.arrays(DAY, trades=sorted({T - 30, T + dl, tx - 30, tx + 1}))
    r = core(has, lt, valid, [T, tx - 1], h, d)
    assert r["rows"] == [(T, "FILLED", T + dl, tx), (tx - 1, "BLOCKED", fd.NO_TE, tx)]
    r = core(has, lt, valid, [T, tx], h, d)                       # TIE at t_x ENTERS (X1)
    assert r["rows"][1] == (tx, "FILLED", tx + 1, tx + 1 + h)
    assert fd.c2_ok(tx + 1, tx)
    ident, v = O.verdict(O.CLOCK_EXT2, tx, tx)                   # reference non-X1: BLOCK (oracle only)
    assert v == O.BLOCK and ident["clock"] == "EXT2"


# ------------------------------------------------------------------ ties (R63-E)
@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_TIE_T2a_x1_core(d):
    T, h = 600, 60
    tx = T + 1 + h
    has, lt, valid = F.arrays(DAY, trades=(T - 30, T + 1, tx - 10, tx + 1))
    r = core(has, lt, valid, [T, tx], h, d)
    assert r["rows"][1] == (tx, "FILLED", tx + 1, tx + 1 + h)
    assert r["rows"][1][2] > tx - 1                                # C-2: no shared second
    assert O.verdict(O.CLOCK_X1, tx, O.prior_cursor(O.CLOCK_X1, T + 1, h))[1] == O.ENTER


def _no_production_ext2():
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    stop(fd.decide_core, has, lt, valid, [3600], 60, F.config(EXIT_CLOCK="EXT2"))


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_TIE_T1_ext2_oracle(d):
    _no_production_ext2()
    T = 3600 * (1 if d == 1 else 2)
    tx = O.prior_cursor(O.CLOCK_EXT2, T, 60)                       # T1a: e0 = T, end = T + 60
    assert tx == T + 60
    assert O.verdict(O.CLOCK_EXT2, T + 60, tx) == (O.clock_identity("EXT2"), O.BLOCK)
    tx = O.prior_cursor(O.CLOCK_EXT2, T + 299, 60)                 # T1b: e0 = T + 299
    assert tx == T + 359
    assert O.verdict(O.CLOCK_EXT2, T + 359, tx)[1] == O.BLOCK
    assert O.verdict(O.CLOCK_EXT2, T + 360, tx)[1] == O.ENTER
    assert O.verdict(O.CLOCK_EXT2, T + 358, tx)[1] == O.BLOCK


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_TIE_T3_pin_labels(d):
    _no_production_ext2()
    T = 3600
    tx = O.prior_cursor(O.CLOCK_EXT2, T, 60)
    flags = {"R63_A": False, "R63_D": True, "R63_E": True}
    assert O.verdict(O.CLOCK_EXT2, T + 60, tx, flags=flags)[1] == O.BLOCK


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_TIE_T4_r63a_flag_alone(d):
    _no_production_ext2()
    T = 3600
    tx = O.prior_cursor(O.CLOCK_EXT2, T, 60)
    flags = {"R63_A": True, "R63_D": True, "R63_E": True}
    assert O.verdict(O.CLOCK_EXT2, T + 60, tx, flags=flags)[1] == O.BLOCK


# ------------------------------------------------------------------ C-2
@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_C2_invariant(d):
    for tx in (61, 3661, 18000):
        assert fd.c2_ok(tx - 1, tx) is False                       # same second as prior exit row
        assert fd.c2_ok(tx - 2, tx) is False
        assert fd.c2_ok(tx, tx) is True
        assert fd.c2_ok(tx + 1, tx) is True
    T0 = 600
    trades = sorted({T0 - 30} | {T0 + 60 * k + 1 + (k % 5) for k in range(0, 400)})
    has, lt, valid = F.arrays(DAY, trades=trades)
    Ts = [T0 + 60 * k for k in range(0, 400)]
    for h in H12:
        r = core(has, lt, valid, Ts, h, d)
        prev = None
        for (T, lab, te, tx) in r["rows"]:
            if lab == "FILLED" and prev is not None:
                assert te > prev - 1
            if lab != "BLOCKED":
                prev = tx


# ------------------------------------------------------------------ span edges (F.5)
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_F5_SPANEDGE_a1_a2_a3(h, d):
    S, inv = F.three_days_fail_middle()
    T = DAY
    fill = tuple(range(T + 1, T + 6))
    has, lt, valid = F.arrays(S, trades=(T - 30,) + fill, invalid=inv)         # (a1) fresh
    r = core(has, lt, valid, [T], h, d)
    assert r["rows"] == [(T, "EMBARGO", fd.NO_TE, T + 5 + h)]
    c = r["counts"]
    assert (c["EMBARGO"], c["EMBARGO_AND_STALE"], c["FAILDAY_IN_EMBARGO"], c["FAILDAY_IN_EMBARGO_AND_STALE"]) == (1, 0, 1, 0)
    assert c["DENOMINATOR"] == 1
    has, lt, valid = F.arrays(S, trades=(T - 700,) + fill, invalid=inv)        # (a1) stale
    c = core(has, lt, valid, [T], h, d)["counts"]
    assert (c["EMBARGO"], c["EMBARGO_AND_STALE"], c["FAILDAY_IN_EMBARGO"], c["FAILDAY_IN_EMBARGO_AND_STALE"]) == (1, 1, 1, 1)
    T2 = DAY + 60
    has, lt, valid = F.arrays(S, trades=(T2 - 30,) + tuple(range(T2 + 1, T2 + 6)), invalid=inv)   # (a2)
    r = core(has, lt, valid, [T2], h, d)
    assert labels(r) == ["EMBARGO"]
    c = r["counts"]
    assert (c["EMBARGO"], c["EMBARGO_AND_STALE"], c["FAILDAY_IN_EMBARGO"], c["FAILDAY_IN_EMBARGO_AND_STALE"]) == (1, 1, 1, 1)
    T3 = 2 * DAY                                                                # (a3) mirror
    trades = (T3 - 30,) + tuple(range(T3 + 1, T3 + 6)) + (T3 + 70, T3 + 121)
    has, lt, valid = F.arrays(S, trades=trades, invalid=inv)
    Ts = [T3, T3 + 60, T3 + 120]
    r = core(has, lt, valid, Ts, h, d)
    assert r["rows"][0] == (T3, "UNFRESH", fd.NO_TE, T3 + 5 + h)
    assert r["counts"]["EMBARGO"] == 0 and r["counts"]["UNFRESH"] == 1
    for (Tk, lab, _te, _tx) in r["rows"][1:]:
        assert (lab == "BLOCKED") == (Tk < T3 + 5 + h)
    if h == 60:
        assert labels(r) == ["UNFRESH", "BLOCKED", "FILLED"]


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_F5_CARRY_window_start_resets(h, d):
    """EMBARGO (or any cursor) at the last decision of window k; first decision of k+1 not BLOCKED."""
    S = 2 * DAY
    Tl, Tf = DAY - 60, DAY
    has, lt, valid = F.arrays(S, trades=(Tl - 30, Tl + 1, Tf - 30, Tf + 1))
    for fn in (core, prod):
        r = fn(has, lt, valid, [Tl, Tf], h, d, window_ends=(DAY - 1,))
        assert r["rows"][1] == (Tf, "FILLED", Tf + 1, Tf + 1 + h)
        if h == 14400:
            assert r["rows"][0] == (Tl, "EMBARGO", fd.NO_TE, Tl + 5 + h)
    r = core(has, lt, valid, [Tl, Tf], h, d)                       # no window cut: same span
    assert (r["rows"][1][1] == "BLOCKED") == (r["rows"][0][3] > Tf)
    if h == 14400:
        assert r["rows"][1][1] == "BLOCKED"


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_BND_SPAN_first_decision_never_blocked(h, d):
    S, inv = F.three_days_fail_middle()
    Tf = 2 * DAY
    Tin = Tf - 60                                                  # last decision inside FAIL day 1
    has, lt, valid = F.arrays(S, trades=(Tin - 30, Tf + 1, Tf + 2), invalid=inv)
    r = core(has, lt, valid, [Tin, Tf], h, d)
    assert r["rows"][0] == (Tin, "EMBARGO", fd.NO_TE, Tin + 5 + h)
    assert r["rows"][1][1] != "BLOCKED"
    for t in (Tf + 1, Tf + 2):                                     # off-grid first decisions of the span
        if t <= Tin + 5 + h:
            r = core(has, lt, valid, [Tin, t], h, d)
            assert r["rows"][1][1] != "BLOCKED"
    S2 = 2 * DAY
    has, lt, valid = F.arrays(S2, trades=(DAY - 90, DAY - 59, DAY - 30, DAY + 1))
    r = core(has, lt, valid, [DAY - 60, DAY], h, d, window_ends=(DAY - 1,))
    assert r["rows"][1][1] != "BLOCKED"


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_F5_PRECEDENCE(h, d):
    T = 3600
    has, lt, valid = F.arrays(DAY, trades=(T - 700, T + 1), valid_until=T + 5 + h - 2)
    r = core(has, lt, valid, [T], h, d)
    assert labels(r) == ["EMBARGO"]
    assert r["counts"]["EMBARGO_AND_STALE"] == 1 and r["counts"]["UNFRESH"] == 0
    assert r["counts"]["FAILDAY_IN_EMBARGO"] == 0
    has, lt, valid = F.arrays(DAY, trades=(T - 700, T + 30))
    r = core(has, lt, valid, [T], h, d)
    assert labels(r) == ["UNFRESH"] and r["counts"]["NO_FILL"] == 0


# ------------------------------------------------------------------ EM6 alignment (production)
@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_UTCALIGN_stop78(d):
    has, lt, valid = F.arrays(2 * DAY, trades=(3570, 3601))
    e = stop(fd.decide, has, lt, valid, [3600], 60, window_ends=(DAY - 61,), direction=d)
    assert "EM6" in e.reason
    has2, lt2, valid2 = F.arrays(2 * DAY, trades=(3570, 3601), invalid=((DAY + 3600, 2 * DAY),))
    stop(fd.decide, has2, lt2, valid2, [3600], 60, direction=d)    # span end mid-day
    has3, lt3, valid3 = F.arrays(DAY + 600, trades=(3570, 3601))
    stop(fd.decide, has3, lt3, valid3, [3600], 60, direction=d)    # evaluated window end mid-day
    stop(fd.check_utc_alignment, valid2, ())
    fd.check_utc_alignment(valid, (DAY - 1,))


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_UTCALIGN_window_end_on_fail_second_stop78(d):
    """EM6: an evaluated WINDOW end mid-day is STOP 78 even where that second is not valid (inside a
    FAIL day), so no valid-span end is mid-day and only the window-end check can catch it."""
    S, inv = F.three_days_fail_middle()
    has, lt, valid = F.arrays(S, trades=(3570, 3601), invalid=inv)
    fd.check_utc_alignment(valid, (DAY - 1, 2 * DAY - 1))
    e = stop(fd.check_utc_alignment, valid, (DAY + 3600,))
    assert "window end" in e.reason
    stop(fd.decide, has, lt, valid, [3600], 60, window_ends=(DAY + 3600,), direction=d)
    assert labels(prod(has, lt, valid, [3600], 60, d, window_ends=(DAY - 1,))) == ["FILLED"]


@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_F_A9_UTCALIGN_day_aligned_zero_over_embargo(h, d):
    S = 2 * DAY
    trades = sorted(set(range(0, S, 7)))
    has, lt, valid = F.arrays(S, trades=trades)
    Ts = list(range(0, S, 60))
    r = prod(has, lt, valid, Ts, h, d, window_ends=(DAY - 1,))
    _sid, lastv = fd.valid_spans(valid, (DAY - 1,))
    band = 0
    for (T, lab, _te, _tx) in r["rows"]:
        if lab == "EMBARGO" and T + h <= int(lastv[T]) <= T + h + 3:
            band += 1
    assert band == 0
    assert sum(r["counts"][k] for k in fd.LABELS) == len(Ts)


@pytest.mark.parametrize("d", F.DIRECTIONS)
def test_F_A9_UTCALIGN_decide_guarded_exit_code_78_subprocess(d):
    """EM6 through decide_guarded: mid-day window end exits 78; day-aligned does not.

    run_guarded exits silently (sys.exit(78) with no print), so the reason text is
    not asserted (Boss addendum bf1c disclosure). The skip-check_utc_alignment
    mutant is what shows the 78 comes from EM6.
    """
    assert "decide_guarded" in vars(fd)
    setup = ("import numpy as np\n"
             "from research_fulln480m import fulln_decide as fd\n"
             "S = 2*86400; has = np.zeros(S, bool); has[[3570, 3601]] = True\n"
             "lt = np.maximum.accumulate(np.where(has, np.arange(S), -1))\n"
             "valid = np.ones(S, bool)\n")
    mid = setup + ("fd.decide_guarded(has, lt, valid, [3600], 60, "
                   "window_ends=(86400 + 3600,), direction=%d)\n" % d)
    aligned = setup + ("fd.decide_guarded(has, lt, valid, [3600], 60, "
                       "window_ends=(86400 - 1,), direction=%d)\n" % d)
    r_mid = _sub(mid)
    assert r_mid.returncode == 78, (r_mid.stdout, r_mid.stderr)
    r_ok = _sub(aligned)
    assert r_ok.returncode == 0, (r_ok.stdout, r_ok.stderr)


# ------------------------------------------------------------------ counts, folds, denominator
@pytest.mark.parametrize("h,d", HD, ids=HD_IDS)
def test_counts_denominator_and_folds(h, d):
    S, inv = F.three_days_fail_middle()
    trades = sorted(set(range(0, DAY, 11)) | set(range(2 * DAY, S, 13)) | {DAY + 5000})
    has, lt, valid = F.arrays(S, trades=trades, invalid=inv)
    Ts = list(range(0, S, 60))
    folds = [T // DAY for T in Ts]
    r = prod(has, lt, valid, Ts, h, d, window_ends=(DAY - 1, 2 * DAY - 1), folds=folds)
    c = r["counts"]
    assert set(c) == set(fd.COUNT_KEYS)
    assert c["DENOMINATOR"] == c["EMBARGO"] + c["UNFRESH"] + c["NO_FILL"] + c["FILLED"]
    assert c["DENOMINATOR"] + c["BLOCKED"] == len(Ts)
    assert c["EMBARGO_AND_STALE"] <= c["EMBARGO"] and c["FAILDAY_IN_EMBARGO"] <= c["EMBARGO"]
    assert c["FAILDAY_IN_EMBARGO_AND_STALE"] <= c["FAILDAY_IN_EMBARGO"]
    assert c["FAILDAY_IN_EMBARGO"] == sum(1 for (T, lab, _a, _b) in r["rows"] if lab == "EMBARGO" and not valid[T])
    assert sorted(r["fold_counts"]) == [0, 1, 2]
    for k in fd.COUNT_KEYS:
        assert sum(r["fold_counts"][f][k] for f in r["fold_counts"]) == c[k]
    assert r["fold_counts"][1]["FILLED"] == 0                     # FAIL day: no trade entered
    for (T, lab, te, tx) in r["rows"]:
        assert T % 60 == 0
        if lab == "FILLED":
            assert T + 1 <= te <= T + 5 and tx == te + h and valid[te] and te + h - 1 <= S - 1
        else:
            assert te == fd.NO_TE


def test_decide_core_rejects_malformed_inputs():
    has, lt, valid = F.arrays(DAY, trades=(3570, 3601))
    for Ts in ([3600, 3600], [3660, 3600], [-1], [DAY]):
        stop(fd.decide_core, has, lt, valid, Ts, 60, F.config())
    stop(fd.decide_core, has, lt[:-1], valid, [3600], 60, F.config())
    stop(fd.decide_core, has, lt, valid, [3600], 60, F.config(), direction=0)
    stop(fd.decide_core, has, lt, valid, [3600, 3660], 60, F.config(), folds=[0])
