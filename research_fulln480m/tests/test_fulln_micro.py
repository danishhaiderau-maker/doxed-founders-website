"""Stage A micro builder tests (card B-R63X r2.1 a312e9be2d95 s4.1, s5.1 A-1 / A-2 / A-3, s5.2 R-C1 /
R-C2 / R-C3, s6 S-1..S-5 / S-10 / S-11 / S-12, s7.1). Synthetic injected trades only; no data
files; no I/O except reading this package's own source files for the hash / isolation tests.

Fixtures: F-M-PARITY, F-M-OHLC (= F-G-OHLC), F-M-EMPTY, F-M-SORT, F-M-SAMEMS (SB-4), F-M-XDAY
(R-C1), F-M-STOP (S-1..S-4), F-M-S11 (a..e), F-M-S12, plus the OPEN-X-5 source-hash and isolation
tests for tests/ref_ext2_per_second.py.

Oracle comparison for OPEN / LOW / HIGH / count against tests/oracle_micro_spec.py is
in this file (s7.1 / OPEN-X-5 point 6 / P-8). The oracle was written by a different executor
(commit 4d4801dbe5c8, sha 09ebca04c93f) and is imported read-only; this file never edits it.
A separate tests/test_fulln_micro_oracle.py is NOT added: card s8 L1326 collects only
tests/test_fulln_micro.py and tests/test_fulln_exit.py.

Floats are compared by int64 bit view, NaN-equal (OPEN-X-5 point 7), never by ==.
The micro builder is direction-free and horizon-free; the window accessor is exercised at all 12
horizons. Nothing here is TESTED until HM's exact-head re-run receipt exists.
"""
import ast
import hashlib
import os
import subprocess
import sys

import numpy as np
import pytest

from research_fulln480m.fulln_closure import ClosureViolation
from research_fulln480m import fulln_micro as fm
from research_fulln480m.tests import ref_ext2_per_second as REF

DAY = 86_400
DAY_MS = 86_400_000
D0 = 19_723 * DAY  # 2024-01-01 00:00:00 UTC, epoch seconds (any UTC day start works)
H12 = (1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 14400)
LOOKAHEAD = 14_400
# Quoted-bytes sha256 of ext2 6b5956fdc19f L702-L710 (lines joined by LF, no trailing LF).
REF_QUOTE_SHA256 = "b181c7c0ed787d5055432b31c0338ef4ebbd7a49b43b26f2a6e97df9cdcb0867"
REF_DEF_LINE = "def per_second(sec, base, S, val, how):"
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(PKG_DIR)


# ------------------------------------------------------------------ helpers
def _sub(code):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=REPO_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


def stop(fn, *a, **kw):
    with pytest.raises(ClosureViolation) as ei:
        fn(*a, **kw)
    assert ei.value.exit_code == 78
    return ei.value


def bits_equal(a, b):
    """int64 bit view compare, NaN-equal (OPEN-X-5 point 7)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return False
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return False
    return bool(np.array_equal(a[~na].view(np.int64), b[~nb].view(np.int64)))


def trade(day_s, second, ms, tid, price, amount=0.5):
    """One raw trade row [id, ts_ms, amount, price] (bfx_day form) in the day starting at day_s."""
    return [float(tid), float(day_s * 1000 + second * 1000 + ms), float(amount), float(price)]


def arr(rows):
    return np.array(rows, dtype=np.float64).reshape(-1, 4)


def gen_day(rng, day_s, n=3000, n_secs=700, id_base=0):
    """Random raw trades for one UTC day, in a SHUFFLED file order: many trades share a second,
    some share a millisecond, ms 0 and 999 occur, ids are unique and not in ts order."""
    secs_pool = rng.choice(DAY, size=n_secs, replace=False)
    secs = rng.choice(secs_pool, size=n)
    ms = rng.integers(0, 1000, size=n)
    ms[: n // 20] = 0
    ms[n // 20: n // 10] = 999
    # same-ms groups: copy (second, ms) of a few earlier rows
    k = n // 10
    src = rng.integers(0, n - k, size=k)
    secs[n - k:] = secs[src]
    ms[n - k:] = ms[src]
    ids = id_base + rng.choice(10 ** 9, size=n, replace=False)
    price = np.round(60_000.0 + rng.normal(0.0, 75.0, size=n), 1)
    amount = rng.choice([-1.0, 1.0], size=n) * rng.uniform(0.001, 2.0, size=n)
    ts = day_s * 1000 + secs.astype(np.int64) * 1000 + ms.astype(np.int64)
    t = np.column_stack([ids.astype(np.float64), ts.astype(np.float64), amount, price])
    return t[rng.permutation(n)]


def gen_span(seed, n_days=2):
    rng = np.random.default_rng(seed)
    return [(D0 + k * DAY, gen_day(rng, D0 + k * DAY, id_base=k * 10 ** 9)) for k in range(n_days)]


def ext2_px_has(days):
    """The test's OWN reference input (OPEN-X-5 point 5): its own lexsort per EXT2-SORT (ext2 L610),
    then the verbatim ext2 per_second "last" path as at ext2 L748-L756. Never the builder's output."""
    base = days[0][0]
    S = DAY * len(days)
    px = np.full(S, np.nan)
    for _d, t in days:
        t = np.array(t, dtype=np.float64, copy=True)
        ids, ts = t[:, 0].astype(np.int64), t[:, 1].astype(np.int64)
        o = np.lexsort((ids, ts))
        t = t[o]
        sec = (t[:, 1].astype(np.int64) // 1000)
        px_d = REF.per_second(sec, base, S, t[:, 3], "last")
        m = ~np.isnan(px_d)
        px[m] = px_d[m]
    has = ~np.isnan(px)
    return px, has


def ohlc_days():
    """Hand fixture (F-M-OHLC / F-M-EMPTY / SECROUND ms 0 and 999)."""
    rows = [
        trade(D0, 0, 0, 1, 98.0),          # ms 0 of second 0 -> row 0
        trade(D0, 10, 300, 7, 95.0),       # file order deliberately not ts order
        trade(D0, 10, 100, 5, 100.0),
        trade(D0, 10, 999, 8, 101.0),      # ms 999 stays in second 10 (floor, not round)
        trade(D0, 10, 200, 6, 105.0),
        trade(D0, 11, 0, 9, 102.5),
        trade(D0, 86_399, 999, 2, 99.0),   # last ms of the day -> row 86,399
    ]
    return [(D0, arr(rows))]


def _micro_ok():
    return fm.build_micro(ohlc_days())


# ------------------------------------------------------------------ OPEN-X-5: reference copy
def test_F_M_REFSRC_quote_sha():
    """OPEN-X-5 point 2: the copied function source hashes to the pinned quote sha (L702-L710)."""
    with open(REF.__file__, "r", encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    i = lines.index(REF_DEF_LINE)
    block = lines[i:i + 9]
    assert block[-1] == "    return out"
    assert hashlib.sha256("\n".join(block).encode("utf-8")).hexdigest() == REF_QUOTE_SHA256
    assert lines.count(REF_DEF_LINE) == 1
    assert "6b5956fdc19f" in text and "L752" in text and "L702-L710" in text
    assert callable(REF.per_second)


def _imports(path):
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
        hits += [n for n in names if isinstance(n, str) and (
            "ref_ext2" in n or "oracle" in n or n.endswith(".tests") or n == "tests" or "ext2_search" in n)]
    return hits


def test_F_M_REF_ISOLATION_static():
    """OPEN-X-5 point 4: production never imports the reference copy (or any test module / ext2)."""
    path = os.path.join(PKG_DIR, "fulln_micro.py")
    assert os.path.isfile(path)
    assert _imports(path) == []
    assert os.path.dirname(os.path.abspath(REF.__file__)) == os.path.join(PKG_DIR, "tests")


def test_F_M_REF_ISOLATION_runtime():
    code = ("import sys\n"
            "import research_fulln480m.fulln_micro as fm\n"
            "assert callable(fm.build_micro)\n"
            "bad = sorted(m for m in sys.modules if 'ref_ext2' in m or 'oracle' in m or 'ext2_search' in m"
            " or m.startswith('research_fulln480m.tests'))\n"
            "print('BAD=' + ','.join(bad))\n")
    r = _sub(code)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "BAD=", r.stdout


# ------------------------------------------------------------------ F-M-PARITY (AC-2 / CP-1)
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_F_M_PARITY(seed):
    days = gen_span(seed, n_days=2)
    px, has = ext2_px_has(days)
    m = fm.build_micro(days)
    assert m.base_s == D0 and len(m.count) == 2 * DAY
    assert bits_equal(m.last, px)                        # LAST bit-equal to ext2 "last"
    assert np.array_equal(m.count > 0, has)               # count > 0 == has
    for a in (m.open, m.low, m.high):                     # NaN pattern identical
        assert np.array_equal(np.isnan(a), np.isnan(px))
    assert fm.check_cp1(m, px=px, has=has) is None        # S-1..S-4 silent on valid data
    assert fm.cross_check_decide(m, has, px, np.ones(2 * DAY, dtype=bool)) is None


# ------------------------------------------------------------------ F-M-OHLC / F-M-EMPTY
def test_F_M_OHLC_hand_values():
    m = _micro_ok()
    assert len(m.count) == DAY and m.base_s == D0
    exp = {0: (1, 98.0, 98.0, 98.0, 98.0), 10: (4, 100.0, 101.0, 95.0, 105.0),
           11: (1, 102.5, 102.5, 102.5, 102.5), 86_399: (1, 99.0, 99.0, 99.0, 99.0)}
    for s, (c, o, la, lo, hi) in exp.items():
        assert int(m.count[s]) == c
        assert bits_equal(np.array([m.open[s], m.last[s], m.low[s], m.high[s]]), np.array([o, la, lo, hi]))
    assert int(m.count[1]) == 0 and int(m.count[9]) == 0 and int(m.count[12]) == 0


@pytest.mark.parametrize("seed", [4, 5])
def test_F_M_OHLC_invariant_random(seed):
    m = fm.build_micro(gen_span(seed, n_days=1))
    t = m.count > 0
    assert t.sum() > 0
    assert np.all(m.low[t] <= m.open[t]) and np.all(m.open[t] <= m.high[t])
    assert np.all(m.low[t] <= m.last[t]) and np.all(m.last[t] <= m.high[t])


def test_F_M_EMPTY():
    m = _micro_ok()
    e = m.count == 0
    assert int((~e).sum()) == 4
    assert m.count.dtype.kind == "u"
    for a in (m.open, m.last, m.low, m.high):
        assert a.dtype == np.float64
        assert np.all(np.isnan(a[e]))                      # NaN, never 0.0, on an empty row
        assert not np.any(np.isnan(a[~e]))
    assert np.all(m.count[e] == 0)


# ------------------------------------------------------------------ F-M-SORT
def test_F_M_SORT_explicit():
    rows = [trade(D0, 20, 900, 30, 200.0), trade(D0, 20, 100, 31, 210.0)]   # file order: later ts first
    m = fm.build_micro([(D0, arr(rows))])
    assert bits_equal(np.array([m.open[20], m.last[20]]), np.array([210.0, 200.0]))


@pytest.mark.parametrize("seed", [6, 7])
def test_F_M_SORT_shuffled_equals_sorted(seed):
    days = gen_span(seed, n_days=2)
    pre = []
    for d, t in days:
        o = np.lexsort((t[:, 0].astype(np.int64), t[:, 1].astype(np.int64)))
        pre.append((d, t[o].copy()))
    a, b = fm.build_micro(days), fm.build_micro(pre)
    assert np.array_equal(a.count, b.count)
    for x, y in ((a.open, b.open), (a.last, b.last), (a.low, b.low), (a.high, b.high)):
        assert bits_equal(x, y)


# ------------------------------------------------------------------ F-M-SAMEMS (SB-4 / BC-2)
def test_F_M_SAMEMS():
    rows = [
        # ascending ids, same ms
        trade(D0, 40, 500, 10, 1000.0), trade(D0, 40, 500, 11, 1001.0), trade(D0, 40, 500, 12, 1002.0),
        # descending ids in file order, same ms
        trade(D0, 41, 500, 22, 2002.0), trade(D0, 41, 500, 21, 2001.0), trade(D0, 41, 500, 20, 2000.0),
        # equal-prefix ids (numeric order 7 < 70 < 71 < 700), shuffled file order, same ms
        trade(D0, 42, 500, 71, 3071.0), trade(D0, 42, 500, 700, 3700.0), trade(D0, 42, 500, 7, 3007.0),
        trade(D0, 42, 500, 70, 3070.0),
    ]
    days = [(D0, arr(rows))]
    m = fm.build_micro(days)
    exp = {40: (3, 1000.0, 1002.0), 41: (3, 2000.0, 2002.0), 42: (4, 3007.0, 3700.0)}
    for s, (c, o, la) in exp.items():
        assert int(m.count[s]) == c
        assert bits_equal(np.array([m.open[s], m.last[s]]), np.array([o, la]))
    px, has = ext2_px_has(days)
    assert bits_equal(m.last, px) and np.array_equal(m.count > 0, has)


# ------------------------------------------------------------------ F-M-XDAY (R-C1 / OPEN-X-10)
def xday_days():
    d1 = D0 + DAY
    day0 = arr([trade(D0, 0, 0, 1, 100.0), trade(D0, 86_350, 0, 2, 120.0), trade(D0, 86_399, 500, 3, 110.0)])
    day1 = arr([trade(d1, 0, 0, 11, 90.0), trade(d1, 3_000, 0, 12, 85.0), trade(d1, 14_000, 0, 13, 80.0),
                trade(d1, 50_000, 0, 14, 70.0)])
    return [(D0, day0), (d1, day1)]


def test_F_M_XDAY_contiguous_axis_and_crossing():
    m = fm.build_micro(xday_days())
    assert len(m.count) == 2 * DAY and m.base_s == D0
    # DAYBASE: the first second of day 1 is row 86,400, not row 0 (no "mod 86,400" index).
    assert int(m.count[0]) == 1 and bits_equal(m.last[0:1], np.array([100.0]))
    assert int(m.count[DAY]) == 1 and bits_equal(m.last[DAY:DAY + 1], np.array([90.0]))
    T = 86_340
    t_e = T + 1
    # minimal crossing: h = 60, the last row is the first second of the next UTC day
    w = fm.window_rows(m, t_e, 60)
    assert len(w.count) == 60 and w.base_s == D0 + t_e
    assert (w.base_s + 59) // DAY == D0 // DAY + 1
    assert int(w.count[-1]) == 1 and bits_equal(w.last[-1:], np.array([90.0]))
    # h = 30 at T = 86,340: NO crossing
    w30 = fm.window_rows(m, t_e, 30)
    assert (w30.base_s + 29) // DAY == D0 // DAY
    assert int(w30.count.sum()) == 1                          # only the 86,350 print
    # extremes established before midnight, hit after (micro side; h in {60, 3600, 14400})
    for h, low_exp in ((60, 90.0), (3600, 85.0), (14400, 80.0)):
        w = fm.window_rows(m, t_e, h)
        assert len(w.count) == h
        assert float(np.nanmax(w.high)) == 120.0                 # peak before midnight
        assert float(np.nanmin(w.low)) == low_exp                # low after midnight
        day0_part = w.low[: DAY - t_e]
        assert float(np.nanmin(day0_part)) == 110.0             # clipping at midnight would change it
    # every one of the 12 horizons: the accessor does not STOP on a valid in-span window
    for h in H12:
        w = fm.window_rows(m, t_e, h)
        assert len(w.count) == h and w.base_s == D0 + t_e
    # TODO (fulln_exit stage): the F-M-XDAY sub-rows "TP, SL, LIQ and trail peak established before
    # midnight and hit after" assert EXIT labels / offsets and need fulln_exit.py; they are added with
    # tests/test_fulln_exit.py, not here (stage 1 is the micro builder only).


def test_F_M_XDAY_chunk_lookahead_equals_span():
    days = xday_days()
    m = fm.build_micro(days)
    c0 = fm.build_micro_chunk(days, 0)
    assert c0.base_s == D0 and len(c0.count) == DAY + LOOKAHEAD
    assert np.array_equal(c0.count, m.count[: DAY + LOOKAHEAD])
    for x, y in ((c0.open, m.open), (c0.last, m.last), (c0.low, m.low), (c0.high, m.high)):
        assert bits_equal(x, y[: DAY + LOOKAHEAD])
    w = fm.window_rows(c0, 86_341, 14_400)                 # needs rows up to 100,740 < 100,800
    assert float(np.nanmin(w.low)) == 80.0
    e = stop(fm.window_rows, c0, DAY + 1, 14_400)           # last row 100,800 is past the look-ahead
    assert "S-5:" in e.reason
    c1 = fm.build_micro_chunk(days, 1)                     # last day of the span: no look-ahead
    assert c1.base_s == D0 + DAY and len(c1.count) == DAY
    assert np.array_equal(c1.count, m.count[DAY:])
    e = stop(fm.window_rows, c1, 86_341, 60)                 # would need a second outside the span
    assert "S-5:" in e.reason


def test_F_M_XDAY_window_outside_span_stop():
    m = fm.build_micro(xday_days())
    for t_e, h in ((-1, 60), (2 * DAY - 59, 60), (2 * DAY, 1), (2 * DAY - 14_399, 14_400)):
        e = stop(fm.window_rows, m, t_e, h)
        assert "S-5:" in e.reason
    for bad_h in (0, -5, 2, 61, 28_800):
        e = stop(fm.window_rows, m, 100, bad_h)
        assert "S-10:" in e.reason


# ------------------------------------------------------------------ F-M-STOP (S-1..S-4)
def test_F_M_STOP_S1_traded_row_missing_extreme():
    m = _micro_ok()
    lo = m.low.copy(); lo[10] = np.nan
    assert "S-1:" in stop(fm.check_cp1, m._replace(low=lo)).reason
    hi = m.high.copy(); hi[11] = np.nan
    assert "S-1:" in stop(fm.check_cp1, m._replace(high=hi)).reason


def test_F_M_STOP_S2_empty_row_with_values():
    m = _micro_ok()
    for field in ("open", "last", "low", "high"):
        a = getattr(m, field).copy(); a[12] = 100.0
        assert "S-2:" in stop(fm.check_cp1, m._replace(**{field: a})).reason


def test_F_M_STOP_S3_parity():
    days = ohlc_days()
    m = fm.build_micro(days)
    px, has = ext2_px_has(days)
    assert fm.check_cp1(m, px=px, has=has) is None
    p2 = px.copy(); p2[10] = np.nextafter(p2[10], np.inf)          # one ULP
    assert "S-3:" in stop(fm.check_cp1, m, px=p2, has=has).reason
    p3 = px.copy(); p3[12] = 100.0                                   # NaN pattern differs
    assert "S-3:" in stop(fm.check_cp1, m, px=p3, has=has).reason
    h2 = has.copy(); h2[12] = True
    assert "S-3:" in stop(fm.check_cp1, m, px=px, has=h2).reason
    assert "S-3:" in stop(fm.check_cp1, m, px=px[:-1], has=has).reason


def test_F_M_STOP_S4_ohlc_order():
    m = _micro_ok()
    o = m.open.copy(); o[10] = 106.0                                 # OPEN above HIGH
    assert "S-4:" in stop(fm.check_cp1, m._replace(open=o)).reason
    o = m.open.copy(); o[10] = 94.0                                  # OPEN below LOW
    assert "S-4:" in stop(fm.check_cp1, m._replace(open=o)).reason
    la = m.last.copy(); la[10] = 94.5                                # LAST below LOW
    assert "S-4:" in stop(fm.check_cp1, m._replace(last=la)).reason
    la = m.last.copy(); la[10] = 105.5                               # LAST above HIGH
    assert "S-4:" in stop(fm.check_cp1, m._replace(last=la)).reason


def test_F_M_STOP_guarded_exit_code_78_subprocess():
    pre = ("import numpy as np\n"
           "from research_fulln480m import fulln_micro as fm\n"
           "D0 = %d\n"
           "t = np.array([[1.0, D0 * 1000.0 + 10500.0, 1.0, 100.0]])\n" % D0)
    bad = _sub(pre + "fm.build_micro_guarded([(D0, np.zeros((0, 4)))])\n")
    assert bad.returncode == 78, (bad.stdout, bad.stderr)
    bad2 = _sub(pre + "from research_fulln480m.fulln_closure import run_guarded\n"
                "m = fm.build_micro([(D0, t)])\n"
                "lo = m.low.copy(); lo[10] = np.nan\n"
                "run_guarded(lambda: fm.check_cp1(m._replace(low=lo)))\n")
    assert bad2.returncode == 78, (bad2.stdout, bad2.stderr)
    ok = _sub(pre + "m = fm.build_micro_guarded([(D0, t)])\n"
              "print(int(m.count[10]), repr(float(m.last[10])))\n")
    assert ok.returncode == 0 and ok.stdout.strip() == "1 100.0", (ok.stdout, ok.stderr)


# ------------------------------------------------------------------ F-M-S11 (a..e)
def test_F_M_S11_a_empty_pass_day():
    good = ohlc_days()[0][1]
    e = stop(fm.build_micro, [(D0, good), (D0 + DAY, np.zeros((0, 4)))])
    assert "S-11(a):" in e.reason


def test_F_M_S11_b_ts_outside_day():
    for ms_abs in (D0 * 1000 - 1, D0 * 1000 + DAY_MS, D0 * 1000 + 2 * DAY_MS, 0):
        t = arr([trade(D0, 5, 0, 1, 100.0), [2.0, float(ms_abs), 1.0, 100.0]])
        e = stop(fm.build_micro, [(D0, t)])
        assert "S-11(b):" in e.reason
    # the two edges inside the day are accepted: ms 0 of the first second, ms 999 of the last
    t = arr([[1.0, float(D0 * 1000), 1.0, 100.0], [2.0, float(D0 * 1000 + DAY_MS - 1), 1.0, 101.0]])
    m = fm.build_micro([(D0, t)])
    assert int(m.count[0]) == 1 and int(m.count[DAY - 1]) == 1 and int(m.count.sum()) == 2


def test_F_M_S11_c_bad_price():
    for p in (0.0, -1.0, np.nan, np.inf, -np.inf):
        t = arr([trade(D0, 5, 0, 1, 100.0), trade(D0, 6, 0, 2, 100.0)])
        t[1, 3] = p
        e = stop(fm.build_micro, [(D0, t)])
        assert "S-11(c):" in e.reason


def test_F_M_S11_d_duplicate_ids():
    t = arr([trade(D0, 5, 0, 1, 100.0), trade(D0, 6, 0, 1, 101.0)])
    e = stop(fm.build_micro, [(D0, t)])
    assert "S-11(d):" in e.reason


def test_F_M_S11_e_missing_day_in_span():
    a = arr([trade(D0, 5, 0, 1, 100.0)])
    c = arr([trade(D0 + 2 * DAY, 5, 0, 3, 100.0)])
    e = stop(fm.build_micro, [(D0, a), (D0 + 2 * DAY, c)])
    assert "S-11(e):" in e.reason


def test_F_M_S10_malformed_input():
    good = arr([trade(D0, 5, 0, 1, 100.0)])
    cases = [
        [],                                                          # no day at all
        [(D0, good[:, :3])],                                         # shape
        [(D0, good.astype(np.float32))],                             # dtype
        [(D0 + 1, good)],                                            # not a UTC day start
        [(D0 + DAY, good), (D0, good)],                              # day order
        [(D0, np.array([[1.5, D0 * 1000.0 + 5000.0, 1.0, 100.0]]))],   # non-integer id
        [(D0, np.array([[1.0, D0 * 1000.0 + 5000.5, 1.0, 100.0]]))],   # non-integer ts
        [(D0, np.array([[np.nan, D0 * 1000.0 + 5000.0, 1.0, 100.0]]))],  # non-finite id
        [(D0, np.array([[1.0, np.inf, 1.0, 100.0]]))],                 # non-finite ts
        [(float(D0), good)],                                         # day start not an int
    ]
    for days in cases:
        e = stop(fm.build_micro, days)
        assert "S-10:" in e.reason


# ------------------------------------------------------------------ F-M-S12 (micro vs decide)
def test_F_M_S12_cross_check():
    days = gen_span(8, n_days=2)
    m = fm.build_micro(days)
    px, has = ext2_px_has(days)
    S = 2 * DAY
    off = 50
    H = np.zeros(S + 100, dtype=bool); H[off:off + S] = has
    P = np.full(S + 100, np.nan); P[off:off + S] = px
    V = np.zeros(S + 100, dtype=bool); V[off:off + S] = True
    assert fm.cross_check_decide(m, H, P, V, offset=off) is None
    s_tr = off + int(np.flatnonzero(has)[3])
    s_em = off + int(np.flatnonzero(~has)[3])
    h2 = H.copy(); h2[s_tr] = False
    assert "S-12:" in stop(fm.cross_check_decide, m, h2, P, V, offset=off).reason
    h3 = H.copy(); h3[s_em] = True
    assert "S-12:" in stop(fm.cross_check_decide, m, h3, P, V, offset=off).reason
    p2 = P.copy(); p2[s_tr] = np.nextafter(p2[s_tr], -np.inf)
    assert "S-12:" in stop(fm.cross_check_decide, m, H, p2, V, offset=off).reason
    p3 = P.copy(); p3[s_tr] = np.nan
    assert "S-12:" in stop(fm.cross_check_decide, m, H, p3, V, offset=off).reason
    v2 = V.copy(); v2[off + DAY] = False
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P, v2, offset=off).reason
    # RK 1548ba71c8aa item 5: valid=False seconds on the pads are part of the comparison.
    assert int((~V).sum()) == 100 and int(V.sum()) == S
    assert np.array_equal(V, fm.micro_derived_valid(S + 100, off, S))
    # injected mismatch: a False pad flipped to True must STOP (the all-True-inside check missed this)
    v_pad = V.copy(); v_pad[off - 1] = True
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P, v_pad, offset=off).reason
    v_tail = V.copy(); v_tail[off + S] = True
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P, v_tail, offset=off).reason
    v_all = np.ones(S + 100, dtype=bool)
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P, v_all, offset=off).reason
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P, V, offset=off + 51).reason
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P, V, offset=-1).reason
    assert "S-12:" in stop(fm.cross_check_decide, m, H, P[:-1], V, offset=off).reason

# ------------------------------------------------------------------ oracle comparison (s7.1 / OPEN-X-5 point 6)
# The oracle (tests/oracle_micro_spec.py, function oracle_micro) is TEST-ONLY and was written
# by a different executor (P-8). Compared fields are count, OPEN, LOW, HIGH (card A-2 / R-C2);
# LAST is recorded too, but ext2 remains the LAST reference (F-M-PARITY). Bit view, NaN-equal.
from research_fulln480m.tests.oracle_micro_spec import oracle_micro


def _assert_oracle(days):
    m = fm.build_micro(days)
    o = oracle_micro(days)
    assert m.base_s == o["base_s"]
    assert np.array_equal(m.count, o["count"])
    for name, got in (("open", m.open), ("low", m.low), ("high", m.high)):
        assert bits_equal(got, o[name]), name
    assert np.array_equal(np.isnan(m.open), np.isnan(o["open"]))
    assert np.array_equal(np.isnan(m.low), np.isnan(o["low"]))
    assert np.array_equal(np.isnan(m.high), np.isnan(o["high"]))
    return m, o


def test_F_M_ORACLE_hand_fixtures():
    _assert_oracle(ohlc_days())
    _assert_oracle(xday_days())
    _assert_oracle([(D0, arr([trade(D0, 20, 900, 30, 200.0), trade(D0, 20, 100, 31, 210.0)]))])
    _assert_oracle([(D0, arr([
        trade(D0, 40, 500, 10, 1000.0), trade(D0, 40, 500, 11, 1001.0), trade(D0, 40, 500, 12, 1002.0),
        trade(D0, 41, 500, 22, 2002.0), trade(D0, 41, 500, 21, 2001.0), trade(D0, 41, 500, 20, 2000.0),
        trade(D0, 42, 500, 71, 3071.0), trade(D0, 42, 500, 700, 3700.0), trade(D0, 42, 500, 7, 3007.0),
        trade(D0, 42, 500, 70, 3070.0),
    ]))])


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5, 6, 7])
def test_F_M_ORACLE_random_multiday(seed):
    """Fixed seeds; two-day spans; same-ms ties and ms 0/999 are in gen_day; spans midnight."""
    _assert_oracle(gen_span(seed, n_days=2))
