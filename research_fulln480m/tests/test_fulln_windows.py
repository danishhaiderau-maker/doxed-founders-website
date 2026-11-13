"""B3a tests (T3.*): windows, day blocks, DATA-THIN literal, fold floor, fold-id plumbing (HM BL8 / GS C-3),
int32 launch guard incl. the literal full-span case (GS C-2, HM H-6), allowlist gate + HM (f) deny paths,
no hard-coded staleness bound (RK 22ee55488e0c). Box stubs only; tmp files under pytest --basetemp."""
import ast
import hashlib
import inspect
import os

import numpy as np
import pytest

from research_fulln480m import fulln_windows as fw
from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

NS = 1_000_000_000


def _ns(text):
    return fw._utc_ns(text)


# ---------------------------------------------------------------- windows / day blocks
def test_t3_1_windows_literal_card_r3():
    exp = [("WU", "2024-10-30", "2024-11-01", 2), ("W01", "2024-11-01", "2025-01-01", 61),
           ("W02", "2025-01-01", "2025-03-01", 59), ("W03", "2025-03-01", "2025-05-01", 61),
           ("W04", "2025-05-01", "2025-07-01", 61), ("W05", "2025-07-01", "2025-09-01", 62),
           ("W06", "2025-09-01", "2025-11-01", 61), ("W07", "2025-11-01", "2026-01-01", 61),
           ("W08", "2026-01-01", "2026-03-01", 59), ("W09", "2026-03-01", "2026-05-01", 61),
           ("W10", "2026-05-01", "2026-07-01", 61), ("W11", "2026-07-01", "2026-09-01", 62),
           ("W12", "2026-09-01", "2026-10-30", 59)]
    for wid, a, b, d in exp:
        lo, hi = fw.window_bounds_ns(wid)
        assert lo == _ns(a + "T00:00:00Z") and hi == _ns(b + "T00:00:00Z")
        assert (hi - lo) == d * fw.DAY_NS
    assert sum(d for wid, _, _, d in exp if wid != "WU") == 728 == pins.K_DAY_BLOCKS
    assert fw.FULL_SPAN_BARS == 1_048_320


def test_t3_2_day_block_half_open_utc_edges():
    s = _ns("2024-11-01T00:00:00Z")
    e = _ns("2026-10-30T00:00:00Z")
    ts = np.array([s - 1, s, s + fw.DAY_NS - 1, s + fw.DAY_NS, e - 1, e], dtype=np.int64)
    assert fw.day_block_index(ts).tolist() == [-1, 0, 0, 1, 727, -1]
    # a UTC-midnight boundary in AEDT (11:00 local) is still a UTC day boundary
    t = _ns("2025-01-01T00:00:00Z")
    assert fw.day_block_index(np.array([t - 1, t], dtype=np.int64)).tolist() == [60, 61]
    assert fw.window_index(np.array([t - 1, t, e - 1, e, s - 1], dtype=np.int64)).tolist() == [0, 1, 11, -1, -1]


def test_t3_3_timestamps_must_be_int64():
    with pytest.raises(ClosureViolation):
        fw.day_block_index(np.array([1.0e18]))
    with pytest.raises(ClosureViolation):
        fw.window_bar_mask("W01", np.array([1, 2], dtype=np.int32))
    with pytest.raises(ClosureViolation):
        fw.window("W13")


def test_t3_4_window_bar_mask():
    lo, hi = fw.window_bounds_ns("W12")
    m = fw.window_bar_mask("W12", np.array([lo - 60 * NS, lo, hi - 60 * NS, hi], dtype=np.int64))
    assert m.tolist() == [False, True, True, False]


# ---------------------------------------------------------------- DATA-THIN literal
def test_t3_5_data_thin_90pct_20h_boundary():
    d = fw.window("W12").days  # 59 days: 90% = 53.1 -> need 54 covered days
    h = np.full(d, 24.0)
    h[:5] = 19.99  # 54 covered
    assert fw.data_thin("W12", "funding", h) is False
    h[5] = 0.0  # 53 covered < 53.1
    assert fw.data_thin("W12", "funding", h) is True
    h2 = np.full(61, 20.0)  # exactly 20 h counts as covered
    assert fw.data_thin("W01", "trades", h2) is False
    h3 = np.full(61, 24.0)
    h3[:7] = 0.0  # 54/61 = 88.5% -> thin
    assert fw.data_thin("W01", "trades", h3) is True
    h4 = np.full(61, 24.0)
    h4[:6] = 0.0  # 55/61 = 90.16% -> not thin
    assert fw.data_thin("W01", "trades", h4) is False
    w12 = np.zeros(59)
    w12[:30] = 24.0  # W12 x funding 30/59 = 50.8% (plan r3 s10) -> thin
    assert fw.data_thin("W12", "funding", w12) is True
    # integer rule: thin iff 10 x covered < 9 x days (no float rounding at the 90% edge)
    assert fw.DATA_THIN_FRACTION_NUM == 9 and fw.DATA_THIN_FRACTION_DEN == 10 and fw.DATA_THIN_MIN_HOURS == 20.0


def test_t3_6_data_thin_refuses_bad_input():
    with pytest.raises(ClosureViolation):
        fw.data_thin("W01", "trades", np.full(60, 24.0))
    with pytest.raises(ClosureViolation):
        fw.data_thin("WU", "trades", np.full(2, 24.0))
    with pytest.raises(ClosureViolation):
        h = np.full(61, 24.0)
        h[3] = np.nan
        fw.data_thin("W01", "trades", h)
    with pytest.raises(ClosureViolation):
        fw.data_thin("W01", "trades", np.full(61, 25.0))
    with pytest.raises(ClosureViolation):
        fw.data_thin("W01", "trades", np.full(61, 24, dtype=np.int64))


def test_t3_7_data_thin_window_non_positive_kept_in_denominator():
    T = 3
    net = np.full((12, T), 5.0)
    thin = np.zeros((12, T), dtype=bool)
    thin[:3, 1] = True  # cell 1: 3 thin windows, positive net in all 12 -> only 9 positive
    thin[:4, 2] = True  # cell 2: 4 thin -> 8 positive -> floor fails (denominator stays 12)
    mask, n_pos = fw.fold_positive(net, thin)
    assert n_pos.tolist() == [12, 9, 8]
    assert fw.fold_floor_pass(n_pos).tolist() == [True, True, False]
    assert int(mask[0]) == 0x0FFF and int(mask[1]) == 0x0FF8 and int(mask[2]) == 0x0FF0
    assert mask.dtype == np.dtype("<u2") and int(mask.max()) < (1 << 12)


def test_t3_8_fold_positive_strict_and_refusals():
    net = np.zeros((12, 1))
    net[:9, 0] = 1e-12
    mask, n_pos = fw.fold_positive(net, np.zeros((12, 1), dtype=bool))
    assert n_pos.tolist() == [9]  # zero net is non-positive
    bad = net.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ClosureViolation):
        fw.fold_positive(bad, np.zeros((12, 1), dtype=bool))
    with pytest.raises(ClosureViolation):
        fw.fold_positive(np.zeros((11, 1)), np.zeros((11, 1), dtype=bool))


# ---------------------------------------------------------------- fold-id plumbing (HM BL8 / GS C-3)
def test_t3_9_fold_ids_zero_only():
    f = fw.fold_ids_zero(fw.FULL_SPAN_BARS)
    assert f.dtype == np.dtype("<i4") and f.shape == (1_048_320,) and int(f.max()) == 0 and int(f.min()) == 0
    fw.assert_fold_ids_zero(f)
    fw.assert_fold_ids_zero(fw.fold_ids_zero(0))


def test_t3_10_fold_ids_encoding_windows_or_days_refused():
    # fixture (HM H-6): a micro fold array whose ids encode the window of each bar -> 78
    lo, _ = fw.window_bounds_ns("W01")
    bar_ts = lo + np.arange(0, 4 * fw.DAY_NS, 60 * NS, dtype=np.int64)
    by_day = (fw.day_block_index(bar_ts) % fw.KERNEL_N_FOLDS).astype("<i4")
    with pytest.raises(ClosureViolation):
        fw.assert_fold_ids_zero(by_day)
    by_window = np.full(bar_ts.shape, 1, dtype="<i4")
    with pytest.raises(ClosureViolation):
        fw.assert_fold_ids_zero(by_window)
    with pytest.raises(ClosureViolation):
        fw.assert_fold_ids_zero(np.zeros(10, dtype=np.int64))
    with pytest.raises(ClosureViolation):
        fw.assert_fold_ids_zero(np.array([0, 3], dtype="<i4"))  # 3 is a valid kernel id, still refused


# ---------------------------------------------------------------- int32 guard (GS C-2, HM H-6)
def test_t3_11_int32_full_span_literal_fails_closed():
    with pytest.raises(ClosureViolation) as ei:
        fw.assert_int32_launch(1_048_320, 3)
    assert ei.value.exit_code == 78
    assert fw.launch_index_extents(1_048_320, 3)["flat_barsxnhx1536"] == 4_830_658_560


def test_t3_12_int32_62_day_window_passes():
    ext = fw.assert_int32_launch(89_280, 3)
    assert ext == {"jobs_2xbarsx768": 137_134_080, "flat_barsxnhx1536": 411_402_240}
    for wid in fw.DISCOVERY_IDS:
        fw.assert_int32_launch(fw.window(wid).days * fw.BARS_PER_DAY, pins.N_HORIZONS)


def test_t3_13_int32_boundary():
    # flat = bars x 3 x 1536 = 4608 x bars; the largest bars with flat <= 2^31-1 is 466,033
    fw.assert_int32_launch(466_033, 3)
    with pytest.raises(ClosureViolation):
        fw.assert_int32_launch(466_034, 3)
    with pytest.raises(ClosureViolation):
        fw.assert_int32_launch(0, 3)


# ---------------------------------------------------------------- staleness not pinned
def test_t3_14_no_hard_coded_staleness_bound():
    assert fw.STALENESS_BOUND_S is None
    with pytest.raises(ClosureViolation):
        fw.staleness_bound_s("funding")
    src = inspect.getsource(fw.staleness_bound_s)
    tree = ast.parse(src)
    nums = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]
    assert nums == []


# ---------------------------------------------------------------- allowlist gate
def _mk(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p), len(data), hashlib.sha256(data).hexdigest()


def test_t3_15_allowlist_opens_exact_bytes(tmp_path):
    p, n, h = _mk(tmp_path, "a.bin", b"abc\n")
    g = fw.AllowlistGate({p: (n, h)})
    assert g.open(p).read() == b"abc\n"
    assert g.open(p.upper() if os.name == "nt" else p).read() == b"abc\n"


def test_t3_16_allowlist_refuses_unlisted_size_sha(tmp_path):
    p, n, h = _mk(tmp_path, "a.bin", b"abc\n")
    q, _, _ = _mk(tmp_path, "b.bin", b"zzz\n")
    g = fw.AllowlistGate({p: (n, h)})
    with pytest.raises(ClosureViolation):
        g.open(q)
    g2 = fw.AllowlistGate({p: (n + 1, h)})
    with pytest.raises(ClosureViolation):
        g2.open(p)
    g3 = fw.AllowlistGate({p: (n, "0" * 64)})
    with pytest.raises(ClosureViolation):
        g3.open(p)
    with pytest.raises(ClosureViolation):
        fw.AllowlistGate({p: (n, h.upper())})
    with pytest.raises(ClosureViolation):
        fw.AllowlistGate({p: (n, h), p.swapcase(): (n, h)})  # duplicate under case-insensitive compare


def test_t3_17_hm_f_deny_literals_refused():
    r2c = "C:\\DoxxedCrypto\\data\\binance-spot-1s-20260913-20261112-r1"
    tool = "C:\\DoxxedCrypto\\tools\\binance_spot_1s_dl_r2b.py"
    assert fw.DENY_PREFIXES == (r2c, tool)
    for bad in (r2c, r2c + "\\BTCUSDT-1s-2026-09-13.zip", r2c.upper() + "\\x", "c:/doxxedcrypto/data/binance-spot-1s-20260913-20261112-r1/x",
                "C:\\DoxxedCrypto\\data\\.\\binance-spot-1s-20260913-20261112-r1\\x",
                "C:\\DoxxedCrypto\\tools\\..\\data\\binance-spot-1s-20260913-20261112-r1\\x", tool, tool.lower(),
                "\\\\?\\" + tool):
        with pytest.raises(ClosureViolation):
            fw.AllowlistGate({bad: (1, "0" * 64)})
        g = fw.AllowlistGate({})
        with pytest.raises(ClosureViolation):
            g.open(bad)


def test_t3_18_deny_is_realpath_safe(tmp_path):
    deny_dir = tmp_path / "r2c-tree"
    deny_dir.mkdir()
    secret = deny_dir / "f.bin"
    secret.write_bytes(b"x")
    link = tmp_path / "innocent"
    os.symlink(str(deny_dir), str(link))
    via = str(link / "f.bin")
    h = hashlib.sha256(b"x").hexdigest()
    g = fw.AllowlistGate({via: (1, h)}, deny_prefixes=fw.DENY_PREFIXES + (str(deny_dir),))
    with pytest.raises(ClosureViolation):
        g.open(via)


def test_t3_19_deny_list_cannot_be_weakened():
    with pytest.raises(ClosureViolation):
        fw.AllowlistGate({}, deny_prefixes=("C:\\other",))
