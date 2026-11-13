"""B4 tests: fulln_accum.py (C4 accumulator). Box tiny 'test' layouts only - no 480M allocation.

Binding: plan r3 33f3ec1e0e07 s7 C4 / s12 B4; HM 8b8ec9208448 s3(b) / R2 b88d1b1e0653;
Boss GO r2 3656382c8a5c; review-fix GO c65ece13d3b2; RK e1d778426447; RULES c342ecf8ec9a.
B0 interface revision (FLAG 32 / s3 bits / s4 signatures) is OUT OF SCOPE here.
Scratch: <--basetemp>/b4-fixed/<sha12 of nodeid+file>/<test>/RUN_DIR/accum (pytest's tmp machinery is not used when
--basetemp is given, so nothing is removed); every file is new per session basetemp.
"""
import ast
import hashlib
import json
import os

import numpy as np
import pytest

from research_fulln480m import fulln_accum as fa
from research_fulln480m import fulln_closure as fc
from research_fulln480m import pins

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fulln_accum.py")
H = "ab" * 32  # TEST FIXTURE sha values only
SRCS = {"n_unpriced": {"pass": "TEST-FIXTURE", "receipt_sha256": "cd" * 32},
        "breach_bits": {"pass": "TEST-FIXTURE", "receipt_sha256": "ef" * 32}}
T = 8
LAY = fa.Layout(n_cells=24, tile_cells=T, label="test")


@pytest.fixture(autouse=True)
def _latch_reset():
    fc._STATE["latch"] = None
    yield
    fc._STATE["latch"] = None


@pytest.fixture
def accdir(request):
    base = request.config.option.basetemp
    if base:
        uniq = hashlib.sha256(request.node.nodeid.encode("utf-8") + os.path.abspath(__file__).encode("utf-8")).hexdigest()[:12]
        d = os.path.join(str(base), "b4-fixed", uniq, request.node.name, "RUN_DIR", "accum")
    else:
        d = os.path.join(str(request.getfixturevalue("tmp_path")), "RUN_DIR", "accum")
    os.makedirs(d, exist_ok=True)
    return d


def _path(d):
    return os.path.join(d, "pooled-stats.bin")


def _create(d, method="B", layout=LAY):
    return fa.Accumulator.create(_path(d), layout, method, H, H, H)


def rec(seed=0, method="B", t=T, all_not_evaluated=True):
    rng = np.random.default_rng(seed)
    n = rng.integers(2, 40, t).astype("<u4")  # cells 3.. have n >= 2
    n[0], n[1], n[2] = 0, 1, 5
    s = rng.standard_normal(t) * 10.0
    s[n == 0] = 0.0
    q = np.abs(rng.standard_normal(t)) * 100.0 + s * s
    q[n == 0] = 0.0
    v = np.abs(rng.standard_normal(t)) * 50.0 + 1.0
    flags = np.zeros(t, "u1")
    flags[n < 2] |= fa.FLAG_N_LT_2
    flags[2] |= fa.FLAG_V_ZERO
    if method == "A":
        flags[3] |= fa.FLAG_BLOCK_NOT_COMPUTED
    if all_not_evaluated:
        flags |= np.uint8(fa.FLAG_BREACH_NOT_EVALUATED)
    nan = (flags & np.uint8(fa.FLAG_N_LT_2 | fa.FLAG_V_ZERO | fa.FLAG_BLOCK_NOT_COMPUTED)) != 0
    v[nan] = np.nan
    tb = s / np.sqrt(v)
    return fa.TileRecord(
        n_trades=n, n_unpriced=rng.integers(0, 3, t).astype("<u4"), sum_bp=s.astype("<f8"),
        sumsq_bp2=q.astype("<f8"), v_block_bp2=v.astype("<f8"), t_block=tb.astype("<f8"),
        max_window_net_bp=(rng.standard_normal(t) * 3).astype("<f8"),
        window_pos_mask=rng.integers(0, 1 << 12, t).astype("<u2"), flags=flags)


def _is78(e):
    return getattr(e.value, "exit_code", None) == 78


# ---------------------------------------------------------------- layout / flags
def test_t4_1_real_layout_arithmetic_without_allocation():
    L = fa.real_layout()
    N = pins.N_CELLS
    assert L.file_bytes == 24_506_638_336 == pins.ACCUM_FILE_BYTES
    assert L.n_tiles == 660
    exp = [4096, 4096 + 4 * N, 4096 + 8 * N, 4096 + 16 * N, 4096 + 24 * N, 4096 + 32 * N,
           4096 + 40 * N, 4096 + 48 * N, 4096 + 50 * N]
    assert [L.column_offset(n) for n in fa.COLUMN_NAMES] == exp
    assert sum(np.dtype(d).itemsize for _, d in fa.COLUMNS) == 51
    assert fa.COLUMN_NAMES == ("n_trades", "n_unpriced", "sum_bp", "sumsq_bp2", "v_block_bp2", "t_block",
                               "max_window_net_bp", "window_pos_mask", "flags")


def test_t4_2_layout_refusals():
    for args in [(25, 8, "test"), (0, 8, "test"), (pins.N_CELLS, pins.TILE_CELLS, "test"),
                 (24, 8, "real"), (24, 8, "other")]:
        with pytest.raises(fc.ClosureViolation):
            fa.Layout(*args)
        fc._STATE["latch"] = None


def test_t4_3_hm_flag_bit_table_and_derived_degenerate():
    assert fa.FLAG_TABLE == ((0, "HARD_STOP_BREACH"), (1, "THESIS_CUT_BREACH"), (2, "DATA_THIN_ANY"),
                             (3, "BLOCK_NOT_COMPUTED"), (4, "KC_INELIGIBLE"), (5, "N_LT_2"),
                             (6, "V_ZERO"), (7, "BREACH_NOT_EVALUATED"))
    for bit, name in fa.FLAG_TABLE:
        assert getattr(fa, "FLAG_" + name) == 1 << bit
    assert not hasattr(fa, "FLAG_DEGENERATE") and not hasattr(fa, "BIT_DEGENERATE")
    f = np.array([0, 1 << 5, 1 << 6, (1 << 5) | (1 << 6), 1 << 4, 0xFF ^ 0x60], "u1")
    assert fa.degenerate(f).tolist() == [False, True, True, True, False, False]
    with open(SRC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "FLAG_DEGENERATE" not in attrs  # B0 pins bit-5 DEGENERATE predates the HM pin


def test_t4_4_no_memory_map():
    with open(SRC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
            {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not ({"mmap", "memmap", "open_memmap"} & names)


# ---------------------------------------------------------------- create / open / header
def test_t4_5_create_exact_size_and_header(accdir):
    acc = _create(accdir)
    p = _path(accdir)
    assert os.path.getsize(p) == LAY.file_bytes == 4096 + 51 * 24
    with open(p, "rb") as f:
        hdr = fa.decode_header(f.read(4096))
    assert hdr["n_cells"] == 24 and hdr["method"] == "B" and hdr["units"] == "bp"
    assert hdr["rules_sha256"] == pins.RULES_SHA256
    assert hdr["flag_bits"][5] == [5, "N_LT_2"] and hdr["flag_bits"][7] == [7, "BREACH_NOT_EVALUATED"]
    assert [c[0] for c in hdr["columns"]] == list(fa.COLUMN_NAMES)
    lines = open(os.path.join(accdir, "commit.jsonl")).read().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event"] == "create"
    assert acc.committed_tiles() == []


def test_t4_6_create_refusals(accdir):
    _create(accdir)
    with pytest.raises(fc.ClosureViolation):
        _create(accdir)  # existing file
    fc._STATE["latch"] = None
    bad = os.path.join(os.path.dirname(accdir), "notaccum")
    os.makedirs(bad, exist_ok=True)
    with pytest.raises(fc.ClosureViolation):
        fa.Accumulator.create(os.path.join(bad, "pooled-stats.bin"), LAY, "B", H, H, H)
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        fa.Accumulator.create(os.path.join(accdir, "other.bin"), LAY, "B", H, H, H)


@pytest.mark.parametrize("method,lat,frz,card", [("C", H, H, H), ("B", "ab" * 6, H, H), ("B", H, None, H),
                                                 ("B", H, H, "AB" * 32)])
def test_t4_7_header_inputs_have_no_defaults(accdir, method, lat, frz, card):
    with pytest.raises(fc.ClosureViolation):
        fa.Accumulator.create(_path(accdir), LAY, method, lat, frz, card)
    assert not os.path.exists(_path(accdir))


def test_t4_8_real_layout_needs_clear_ref_and_creates_nothing(accdir):
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.create(_path(accdir), fa.real_layout(), "B", H, H, H)
    assert _is78(e) and "CLEAR" in e.value.reason
    assert not os.path.exists(_path(accdir))


def test_t4_9_open_refuses_other_size(accdir):
    _create(accdir)
    with open(_path(accdir), "ab") as f:
        f.write(b"\0")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "size" in e.value.reason


def test_t4_10_open_refuses_bad_header(accdir):
    _create(accdir)
    with open(_path(accdir), "r+b") as f:
        f.seek(4000)
        f.write(b"\x01")
    with pytest.raises(fc.ClosureViolation):
        fa.Accumulator.open(_path(accdir), LAY)
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        fa.decode_header(b"X" * 4096)


def test_t4_11_open_refuses_layout_mismatch(accdir):
    _create(accdir)
    with pytest.raises(fc.ClosureViolation):
        fa.Accumulator.open(_path(accdir), fa.Layout(n_cells=32, tile_cells=8, label="test"))


# ---------------------------------------------------------------- method B
def test_t4_12_b_write_once_roundtrip_and_commit(accdir):
    acc = _create(accdir)
    r = rec(1)
    shas = acc.write_tile(1, r, SRCS)
    back = acc.read_tile(1)
    for name, a in r.columns():
        b = getattr(back, name)
        assert b.dtype == a.dtype and b.tobytes() == a.tobytes(), name
        assert shas[name] == fa._sha(a.tobytes())
    last = json.loads(open(os.path.join(accdir, "commit.jsonl")).read().splitlines()[-1])
    assert last["event"] == "commit_tile" and last["tile"] == 1 and last["columns"] == shas
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile(1, rec(2), SRCS)
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.read_tile(0)  # never written
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile(3, rec(2), SRCS)  # out of range


def test_t4_13_b_resume_rehash_ok_then_tamper_void(accdir):
    acc = _create(accdir)
    acc.write_tile(0, rec(3), SRCS)
    acc.write_tile(2, rec(4), SRCS)
    acc2 = fa.Accumulator.open(_path(accdir), LAY)
    assert acc2.committed_tiles() == [0, 2]
    acc2.write_tile(1, rec(5), SRCS)
    assert fa.Accumulator.open(_path(accdir), LAY).committed_tiles() == [0, 1, 2]
    off = LAY.column_offset("sum_bp") + 2 * T * 8
    with open(_path(accdir), "r+b") as f:
        f.seek(off)
        f.write(b"\x7f")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "VOID" in e.value.reason


def test_t4_14_commit_log_tamper_void(accdir):
    acc = _create(accdir)
    acc.write_tile(0, rec(3), SRCS)
    cp = os.path.join(accdir, "commit.jsonl")
    lines = open(cp).read().splitlines()
    d = json.loads(lines[1]); d["tile"] = 0; d["extra"] = 1
    lines[1] = json.dumps(d, sort_keys=True, separators=(",", ":"))
    with open(cp, "w") as f:
        f.write("\n".join(lines) + "\n")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "VOID" in e.value.reason


def test_t4_15_b_crash_mid_tile_is_non_evidential(accdir):
    acc = _create(accdir)
    acc.write_tile(0, rec(3), SRCS)
    acc._append({"event": "begin_tile", "tile": 1, "method": "B"})  # crash after begin, before commit
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "NON-EVIDENTIAL" in e.value.reason


def test_t4_16_block_not_computed_under_b_is_stop(accdir):
    acc = _create(accdir)
    with pytest.raises(fc.ClosureViolation) as e:
        acc.write_tile(0, rec(6, method="A"), SRCS)
    assert "method B" in e.value.reason


def test_t4_17_stop_marks_non_evidential(accdir):
    acc = _create(accdir)
    acc.write_tile(0, rec(3), SRCS)
    with pytest.raises(fc.ClosureViolation):
        acc.stop("test stop")
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "NON-EVIDENTIAL" in e.value.reason


# ---------------------------------------------------------------- sources (no placeholders)
def test_t4_18_sources_required(accdir):
    acc = _create(accdir)
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile(0, rec(3), {})
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile(0, rec(3), {"n_unpriced": {"pass": "I1", "receipt_sha256": "00"}})
    fc._STATE["latch"] = None
    only_n = {"n_unpriced": SRCS["n_unpriced"]}
    acc.write_tile(0, rec(3), only_n)  # every cell BREACH_NOT_EVALUATED -> no I2 source needed
    with pytest.raises(fc.ClosureViolation) as e:
        acc.write_tile(1, rec(4, all_not_evaluated=False), only_n)
    assert "breach_bits" in e.value.reason
    acc.write_tile(1, rec(4, all_not_evaluated=False), SRCS)


# ---------------------------------------------------------------- record validation
def _mut(r, **kw):
    d = {n: a.copy() for n, a in r.columns()}
    for k, fn in kw.items():
        fn(d[k]) if callable(fn) else d.__setitem__(k, fn)
    return fa.TileRecord(**d)


def _setv(i, v):
    def f(a):
        a[i] = v
    return f


def _orv(i, v):
    def f(a):
        a[i] |= np.uint8(v)
    return f


BAD = {
    "bit7_with_bit0": dict(flags=_orv(5, fa.FLAG_HARD_STOP_BREACH)),
    "bit7_with_bit1": dict(flags=_orv(5, fa.FLAG_THESIS_CUT_BREACH)),
    "n_lt_2_missing": dict(flags=_setv(1, fa.FLAG_BREACH_NOT_EVALUATED)),
    "n_lt_2_spurious": dict(flags=_orv(5, fa.FLAG_N_LT_2)),
    "v_zero_with_n_lt_2": dict(flags=_orv(0, fa.FLAG_V_ZERO)),
    "nan_without_flag": dict(v_block_bp2=_setv(5, np.nan), t_block=_setv(5, np.nan)),
    "flag5_with_defined_fields": dict(flags=_orv(5, fa.FLAG_BLOCK_NOT_COMPUTED)),
    "v_nan_t_defined": dict(t_block=_setv(0, 1.0)),
    "inf_t": dict(t_block=_setv(5, np.inf)),
    "v_zero_defined": dict(v_block_bp2=_setv(5, 0.0), t_block=_setv(5, np.inf)),
    "v_negative": dict(v_block_bp2=_setv(5, -1.0)),
    "t_not_s_over_sqrt_v": dict(t_block=lambda a: a.__setitem__(5, a[5] * (1 + 1e-6))),
    "wrong_dtype": dict(sum_bp=lambda a: None, n_trades=np.zeros(T, "<u8")),
    "wrong_shape": dict(flags=np.zeros(T + 1, "u1")),
    "mask_high_bits": dict(window_pos_mask=_setv(4, 1 << 12)),
    "n0_nonzero_sum": dict(sum_bp=_setv(0, 1.0)),
    "sumsq_negative": dict(sumsq_bp2=_setv(4, -1.0)),
    "sum_nan": dict(sum_bp=_setv(4, np.nan)),
    "max_window_inf": dict(max_window_net_bp=_setv(4, np.inf)),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_t4_19_record_rules(case):
    good = rec(9)
    fa.validate_record(good, T, "B")
    with pytest.raises(fc.ClosureViolation):
        fa.validate_record(_mut(good, **BAD[case]), T, "B")


def test_t4_20_data_computed_degenerate_cells_carry_nan_and_flags():
    r = rec(11)
    assert np.isnan(r.v_block_bp2[[0, 1, 2]]).all() and np.isnan(r.t_block[[0, 1, 2]]).all()
    assert fa.degenerate(r.flags)[[0, 1, 2]].all() and not fa.degenerate(r.flags)[3:].any()


# ---------------------------------------------------------------- method A
def _window_pass(acc, wid, seed):
    acc.begin_window(wid)
    for i in range(LAY.n_tiles):
        acc.write_tile_inplace(i, rec(seed + i, method="A"), SRCS)
    acc.commit_window(wid)


def test_t4_21_a_window_major_commit_lines(accdir):
    acc = _create(accdir, method="A")
    _window_pass(acc, "W01", 100)
    _window_pass(acc, "W02", 200)
    lines = [json.loads(x) for x in open(os.path.join(accdir, "commit.jsonl")).read().splitlines()]
    cw = [d for d in lines if d["event"] == "commit_window"]
    assert [d["window"] for d in cw] == ["W01", "W02"]
    assert cw[-1]["columns"] == acc._full_hashes()
    acc2 = fa.Accumulator.open(_path(accdir), LAY)
    assert acc2.committed_windows() == ["W01", "W02"]
    back = acc2.read_tile(2)
    assert back.flags[3] & fa.FLAG_BLOCK_NOT_COMPUTED  # allowed under A
    _window_pass(acc2, "W03", 300)


def test_t4_22_a_order_completeness_and_twice(accdir):
    acc = _create(accdir, method="A")
    with pytest.raises(fc.ClosureViolation):
        acc.begin_window("W02")
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile_inplace(0, rec(1, method="A"), SRCS)  # no open window
    fc._STATE["latch"] = None
    acc.begin_window("W01")
    acc.write_tile_inplace(0, rec(1, method="A"), SRCS)
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile_inplace(0, rec(2, method="A"), SRCS)
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.commit_window("W01")  # incomplete
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.write_tile(1, rec(1), SRCS)  # B API under A
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        acc.begin_window("W02")  # W01 still open


def test_t4_23_a_crash_mid_window_non_evidential(accdir):
    acc = _create(accdir, method="A")
    _window_pass(acc, "W01", 1)
    acc.begin_window("W02")
    acc.write_tile_inplace(0, rec(7, method="A"), SRCS)
    del acc  # crash: no commit line for W02
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "NON-EVIDENTIAL" in e.value.reason


def test_t4_24_a_rehash_mismatch_void(accdir):
    acc = _create(accdir, method="A")
    _window_pass(acc, "W01", 1)
    with open(_path(accdir), "r+b") as f:
        f.seek(LAY.column_offset("n_trades"))
        f.write(b"\xff")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "VOID" in e.value.reason


def test_t4_25_a_uncommitted_data_void(accdir):
    _create(accdir, method="A")
    with open(_path(accdir), "r+b") as f:
        f.seek(LAY.column_offset("sum_bp"))
        f.write(b"\x01")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "VOID" in e.value.reason


def test_t4_26_b_api_refused_under_a_and_vice_versa(accdir):
    acc = _create(accdir, method="B")
    with pytest.raises(fc.ClosureViolation):
        acc.begin_window("W01")


def test_t4_27_n_lt_2_bit_is_computed_from_n_trades():
    """Isolated: NaN fields justified by another bit, but bit 5 disagrees with n_trades."""
    good = rec(12, method="A")
    fa.validate_record(good, T, "A")
    missing = _mut(good, flags=_setv(1, fa.FLAG_BREACH_NOT_EVALUATED | fa.FLAG_BLOCK_NOT_COMPUTED))
    with pytest.raises(fc.ClosureViolation):
        fa.validate_record(missing, T, "A")
    fc._STATE["latch"] = None
    spurious = _mut(good, flags=_orv(5, fa.FLAG_N_LT_2), v_block_bp2=_setv(5, np.nan), t_block=_setv(5, np.nan))
    with pytest.raises(fc.ClosureViolation):
        fa.validate_record(spurious, T, "A")


# ---------------------------------------------------------------- review-fix: H4g/h/k/l/m/n + >65535 / 2^32-1 (RK e1d7 B4-1)
def test_t4_28_u32_counts_roundtrip_no_saturation(accdir):
    """n_trades and n_unpriced above 65,535 and at 2^32-1 must round-trip unclipped (H4k)."""
    acc = _create(accdir)
    r = rec(21)
    r.n_trades[5] = 65536
    r.n_trades[6] = 0xFFFFFFFF
    r.n_unpriced[5] = 65536
    r.n_unpriced[6] = 0xFFFFFFFF
    # cells 5/6 already have n >= 2 in rec(); flags and defined V/t stay consistent
    assert (r.flags[5] & fa.FLAG_N_LT_2) == 0 and (r.flags[6] & fa.FLAG_N_LT_2) == 0
    fa.validate_record(r, T, "B")
    acc.write_tile(0, r, SRCS)
    back = acc.read_tile(0)
    assert int(back.n_trades[5]) == 65536 and int(back.n_trades[6]) == 0xFFFFFFFF
    assert int(back.n_unpriced[5]) == 65536 and int(back.n_unpriced[6]) == 0xFFFFFFFF
    assert int(back.n_trades[5]) != 65535 and int(back.n_unpriced[5]) != 65535
    assert back.n_trades.dtype == np.dtype("<u4") and back.n_unpriced.dtype == np.dtype("<u4")


def test_t4_29_write_refuses_when_readback_differs(accdir):
    """Kill H4g: the post-write tile hash must be checked against the bytes written."""
    acc = _create(accdir)
    real = acc._tile_hashes
    def bad(tile):
        return {name: "00" * 32 for name in fa.COLUMN_NAMES}
    acc._tile_hashes = bad
    with pytest.raises(fc.ClosureViolation) as e:
        acc.write_tile(0, rec(1), SRCS)
    assert "read-back" in e.value.reason


def test_t4_30_open_checks_header_rules_sha(accdir):
    """Kill H4h: open() refuses a header whose rules_sha256 != pins.RULES_SHA256,
    even when the create-line header sha is updated to match the tampered header."""
    acc = _create(accdir)
    p = _path(accdir)
    with open(p, "rb") as f:
        raw = f.read(4096)
    hdr = fa.decode_header(raw)
    hdr["rules_sha256"] = "ff" * 32
    new_raw = fa.encode_header(hdr)
    assert len(new_raw) == 4096
    with open(p, "r+b") as f:
        f.write(new_raw)
    cp = os.path.join(accdir, "commit.jsonl")
    entries = [json.loads(x) for x in open(cp).read().splitlines()]
    entries[0]["header_sha256"] = fa._sha(new_raw)
    prev, out = None, []
    for i, d in enumerate(entries):
        d = dict(d, seq=i, prev_sha256=prev)
        line = json.dumps(d, sort_keys=True, separators=(",", ":"))
        prev = fa._line_sha(line)
        out.append(line)
    with open(cp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(p, LAY)
    assert "rules" in e.value.reason.lower()


def test_t4_31_real_layout_refuses_wrong_n_or_tile(accdir):
    """Kill H4l: label 'real' with a size that is not N_CELLS x TILE_CELLS must STOP.
    Arithmetic only; no 24.5 GB allocation."""
    n = pins.TILE_CELLS * 2
    assert n != pins.N_CELLS and n % pins.TILE_CELLS == 0
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Layout(n_cells=n, tile_cells=pins.TILE_CELLS, label="real")
    assert "real layout" in e.value.reason
    fc._STATE["latch"] = None
    assert pins.N_CELLS % (pins.TILE_CELLS * 2) == 0
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Layout(n_cells=pins.N_CELLS, tile_cells=pins.TILE_CELLS * 2, label="real")
    assert "real layout" in e.value.reason


def test_t4_32_header_padding_must_be_zero(accdir):
    """Kill H4m: a single non-zero padding byte is refused by decode_header."""
    _create(accdir)
    with open(_path(accdir), "rb") as f:
        raw = f.read(4096)
    assert raw[-1] == 0
    assert fa.decode_header(raw)["n_cells"] == 24
    dirty = raw[:-1] + b"\x01"
    with pytest.raises(fc.ClosureViolation) as e:
        fa.decode_header(dirty)
    assert "padding" in e.value.reason


def test_t4_33_resume_refuses_method_mismatch_after_rechain(accdir):
    """Kill H4n: a commit-line method that disagrees with the header is VOID,
    even when the hash chain is rewritten so a chain break cannot hide it."""
    acc = _create(accdir)
    acc.write_tile(0, rec(3), SRCS)
    cp = os.path.join(accdir, "commit.jsonl")
    entries = [json.loads(x) for x in open(cp).read().splitlines()]
    changed = 0
    for d in entries:
        if d.get("event") in ("begin_tile", "commit_tile"):
            d["method"] = "A"
            changed += 1
    assert changed >= 1
    prev, out = None, []
    for i, d in enumerate(entries):
        d = dict(d, seq=i, prev_sha256=prev)
        line = json.dumps(d, sort_keys=True, separators=(",", ":"))
        prev = fa._line_sha(line)
        out.append(line)
    with open(cp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    with pytest.raises(fc.ClosureViolation) as e:
        fa.Accumulator.open(_path(accdir), LAY)
    assert "method" in e.value.reason.lower()
