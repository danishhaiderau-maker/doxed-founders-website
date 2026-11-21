"""B0 tests: C8 Route-2 closure loader (T8.1-T8.10). Box stubs only (build evidence, Route-2 cond. 5).

Scratch files: under <--basetemp>/b0-fixed/<test> when --basetemp is given (pytest's own tmp
machinery is never requested, so nothing is ever removed); otherwise pytest tmp_path.
Every file is (re)written deterministically at test start.
"""
import hashlib
import importlib
import importlib.util
import os
import sys
import types

import numpy as np
import pytest

from research_fulln480m import fulln_closure as fc
from research_fulln480m import pins

STUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs")
LOAD_ORDER = ("ci_lag", "ci_registry", "gpu_kernel", "ext2_search", "ci_build", "gpu_eval",
              "phase_b_lattice", "phase_b_run", "ci_discovery_scores")
NOT_LOADED = ("phase_b_search", "gpu_prod_path")
# Import edges of the closure (plan r2 s5) - deps must load earlier.
EDGES = {
    "ci_build": ("ci_registry", "ci_lag"),
    "gpu_eval": ("ext2_search", "gpu_kernel"),
    "phase_b_lattice": ("ci_registry", "gpu_kernel"),
    "phase_b_run": ("ci_build", "ci_lag", "ci_registry", "gpu_eval", "gpu_kernel", "phase_b_lattice"),
    "ci_discovery_scores": ("ext2_search",),
}
STUB_NAMES = LOAD_ORDER + NOT_LOADED + ("phase_b_ci_ready", "extra_unpinned", "outside_ok", "lateload")
DATA = {"ext2/code/configs/phase_b_search.json": b'{"stub": true}\n',
        "ext2/reports/phase-b-gpu-smoke-stub.json": b'{"smoke": "stub"}\n'}


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture
def scratch(request):
    base = request.config.option.basetemp
    if base:
        d = os.path.join(str(base), "b0-fixed", request.node.name)
        os.makedirs(d, exist_ok=True)
        return d
    return str(request.getfixturevalue("tmp_path"))


def _reset():
    for f in list(sys.meta_path):
        if isinstance(f, fc.RefusingFinder):
            sys.meta_path.remove(f)
    fc._STATE["latch"] = None
    fc._STATE["roots"].clear()
    for n in STUB_NAMES:
        sys.modules.pop(n, None)


@pytest.fixture(autouse=True)
def isolate():
    saved = list(sys.path)
    _reset()
    yield
    _reset()
    sys.path[:] = saved


def _build(scratch, order=LOAD_ORDER, complete=True, third_party=(), freeze=None,
           kernel=None):
    root = os.path.join(scratch, "external")
    mods, nl = [], []
    for n in order + NOT_LOADED:
        with open(os.path.join(STUBS, n + ".py"), "rb") as f:
            b = f.read()
        rel = "ext2/code/%s.py" % n
        _write(os.path.join(root, *rel.split("/")), b)
        pin = pins.ModulePin(n, rel, len(b), _sha(b), "stub")
        (mods if n in order else nl).append(pin)
    for rel, b in DATA.items():
        _write(os.path.join(root, *rel.split("/")), b)
    # an unpinned module sitting in the code folder
    _write(os.path.join(root, "ext2", "code", "extra_unpinned.py"), b"VALUE = 1\n")
    _write(os.path.join(root, "ext2", "code", "lateload.py"), b"VALUE = 2\n")
    ps = pins.PinSet(
        external_root=root,
        modules=tuple(mods),
        not_loaded=tuple(nl),
        forbidden_names=pins.FORBIDDEN_MODULE_NAMES,
        data_files=tuple(pins.FilePin(r, len(b), _sha(b), "stub") for r, b in DATA.items()),
        kernel_image_sha256=kernel or _sha(b"stub-ptx-image"),
        third_party=tuple(third_party),
        pip_freeze_sha256=freeze,
        closure_complete=complete,
        label="stub",
    )
    return root, ps


def _stop(excinfo, needle=None):
    assert excinfo.value.exit_code == 78
    if needle:
        assert needle in excinfo.value.reason, excinfo.value.reason


# ---------------------------------------------------------------- T8.1
def test_t8_1_one_byte_tamper_refused_before_exec(scratch):
    root, ps = _build(scratch)
    p = os.path.join(root, "ext2", "code", "gpu_kernel.py")
    with open(p, "rb") as f:
        b = f.read()
    _write(p, b.replace(b'"""BOX STUB', b'raise RuntimeError("TAMPERED-EXECUTED")\n"""BOX STUB', 1))
    with pytest.raises(fc.ClosureViolation) as e:
        fc.load_closure(ps)
    _stop(e, "gpu_kernel")
    _stop(e, "mismatch")  # refused by the hash/size check, never by executing the bytes
    for n in LOAD_ORDER:  # nothing executed, partial closure rolled back
        assert n not in sys.modules
    with pytest.raises(fc.ClosureViolation):
        fc.preflight(ps, mode="stub")
    # strict one-byte flip, same size (guard lifted for this fixture rewrite only)
    fc._STATE["roots"].clear()
    fc._STATE["latch"] = None
    _write(p, b[:-2] + bytes([b[-2] ^ 0x01]) + b[-1:])
    with pytest.raises(fc.ClosureViolation) as e:
        fc.load_closure(ps)
    _stop(e, "sha256 mismatch")


def test_t8_1b_read_pinned_size_and_sha(scratch):
    p = os.path.join(scratch, "f.bin")
    _write(p, b"abc")
    assert fc.read_pinned(p, 3, _sha(b"abc")) == b"abc"
    with pytest.raises(fc.ClosureViolation):
        fc.read_pinned(p, 3, _sha(b"abd"))
    with pytest.raises(fc.ClosureViolation):
        fc.read_pinned(p, 4, _sha(b"abc"))


# ---------------------------------------------------------------- T8.2
def test_t8_2_shadow_never_imported_and_sys_path_untouched(scratch):
    root, ps = _build(scratch)
    shadow = os.path.join(scratch, "shadow")
    _write(os.path.join(shadow, "ci_lag.py"), b'raise RuntimeError("SHADOW-EXECUTED")\n')
    sys.path.insert(0, shadow)
    before = list(sys.path)
    st = fc.load_closure(ps)
    assert sys.path == before
    m = st.module("ci_lag")
    assert sys.modules["ci_lag"] is m
    assert os.path.normcase(os.path.realpath(m.__file__)) == os.path.normcase(
        os.path.realpath(os.path.join(root, "ext2", "code", "ci_lag.py")))
    assert m.__spec__.origin == m.__file__
    # phase_b_run computes CODE from __file__: must be the real (pinned) folder
    assert os.path.realpath(st.module("phase_b_run").CODE) == os.path.realpath(
        os.path.join(root, "ext2", "code"))
    assert len(st.module("phase_b_run").plan_tiles()) == pins.N_TILES
    fc.assert_kernel_image(st)
    assert fc.audit(st)["ok"] is True


# ---------------------------------------------------------------- T8.3
def test_t8_3_load_opens_read_only_and_guard_active(scratch):
    root, ps = _build(scratch)
    st = fc.load_closure(ps)
    assert fc._norm(root) in fc._STATE["roots"]
    for rel in ("ext2/code/ci_lag.py", "ext2/code/configs/phase_b_search.json"):
        with pytest.raises(fc.ClosureViolation):
            open(os.path.join(root, *rel.split("/")), "r+b")
    fc._STATE["latch"] = None
    assert fc.audit(st)["ok"] is True


# ---------------------------------------------------------------- T8.4
def test_t8_4_real_pins_consistent():
    ps = pins.REAL_PINSET
    assert ps.load_names == LOAD_ORDER
    assert tuple(m.name for m in ps.not_loaded) == NOT_LOADED
    for i, n in enumerate(ps.load_names):
        for dep in EDGES.get(n, ()):
            assert ps.load_names.index(dep) < i, (n, dep)
    assert "phase_b_ci_ready" in ps.forbidden_names
    assert pins.N_TILES * pins.TILE_CELLS == pins.N_CELLS == 22 * 10 * 3 * 2 * 768 * 474
    assert pins.ACCUM_HEADER_BYTES + pins.N_CELLS * pins.ACCUM_RECORD_BYTES == pins.ACCUM_FILE_BYTES
    assert sum(np.dtype(d).itemsize for _, d in pins.ACCUM_COLUMNS) == pins.ACCUM_RECORD_BYTES == 51
    assert [c for c, _ in pins.ACCUM_COLUMNS][4:6] == ["v_block_bp2", "t_block"]
    assert sum(w.days for w in pins.WINDOWS if w.role == "discovery") == pins.K_DAY_BLOCKS == 728
    assert pins.DISCOVERY_WINDOW_IDS == tuple("W%02d" % i for i in range(1, 13))
    assert pins.FOLD_FLOOR == 9
    assert pins.MULTIPLIER_SHAPE == (728, 10_000)
    assert pins.MULTIPLIER_BYTES == 728 * 10_000 * 8
    assert pins.DAY_SUMS_BYTES_PER_TILE == pins.K_DAY_BLOCKS * pins.TILE_CELLS * 12
    assert pins.CHAIN_WALL_S == 48 * 3600 and pins.G_B5_CHAIN_BAR_S == 36 * 3600
    assert pins.RUN_DIR_MAX_BYTES == pins.OUTPUTS_MAX_BYTES_B + pins.ACCUM_FILE_BYTES + pins.CONTROL_MAX_BYTES
    assert pins.OUTPUTS_MAX_BYTES_B == 12 * pins.WINDOW_OUTPUT_MAX_BYTES_A
    assert abs(pins.ALPHA_BONF_ONE_SIDED - 0.05 / pins.N_CELLS) < 1e-14
    assert pins.CLOSURE_COMPLETE is False and pins.PIP_FREEZE_SHA256 is None
    assert {t.dist for t in pins.THIRD_PARTY} == {"numpy", "pandas", "scipy", "pyyaml"}


def test_t8_4b_launch_mode_refuses_incomplete_or_unpinned(scratch):
    with pytest.raises(fc.ClosureViolation) as e:
        fc.preflight(pins.REAL_PINSET, mode="launch")
    _stop(e, "closure")
    root, ps = _build(scratch, third_party=(pins.ThirdPartyPin("numpy", None),))
    with pytest.raises(fc.ClosureViolation) as e:
        fc.preflight(ps, mode="launch")
    _stop(e, "numpy")
    r = fc.preflight(ps, mode="stub")
    assert r["modules"]["ci_lag"]["sha256"] == ps.modules[0].sha256
    assert set(r["modules"]) == set(LOAD_ORDER + NOT_LOADED)
    assert len(r["data_files"]) == len(DATA)


def test_t8_4c_wrong_topological_order_refused(scratch):
    order = ("ci_lag", "ci_build") + tuple(n for n in LOAD_ORDER if n not in ("ci_lag", "ci_build"))
    root, ps = _build(scratch, order=order)
    with pytest.raises(fc.ClosureViolation) as e:
        fc.load_closure(ps)
    _stop(e, "ci_registry")


def test_t8_4d_kernel_image_mismatch(scratch):
    root, ps = _build(scratch, kernel="0" * 64)
    st = fc.load_closure(ps)
    with pytest.raises(fc.ClosureViolation) as e:
        fc.assert_kernel_image(st)
    _stop(e, "kernel")


def test_t8_4e_pinset_validation():
    good = pins.REAL_PINSET
    with pytest.raises(ValueError):
        pins.PinSet(good.external_root, good.modules, good.not_loaded, frozenset({"ci_lag"}),
                    good.data_files, None, (), None, False)
    bad = pins.ModulePin("x", "ext2/code/x.py", 1, "ABC")
    with pytest.raises(ValueError):
        pins.PinSet(good.external_root, (bad,), (), frozenset(), (), None, (), None, False)


# ---------------------------------------------------------------- T8.5
def test_t8_5_finder_refuses_unpinned_external_module(scratch):
    root, ps = _build(scratch)
    fc.load_closure(ps)
    outside = os.path.join(scratch, "outside")
    _write(os.path.join(outside, "outside_ok.py"), b"OK = 1\n")
    sys.path.insert(0, os.path.join(root, "ext2", "code"))
    sys.path.insert(0, outside)
    with pytest.raises(fc.ClosureViolation) as e:
        importlib.import_module("extra_unpinned")
    _stop(e, "extra_unpinned")
    assert "extra_unpinned" not in sys.modules
    for n in NOT_LOADED:
        with pytest.raises(fc.ClosureViolation):
            importlib.import_module(n)
    assert importlib.import_module("outside_ok").OK == 1
    assert fc.violation_latched()


# ---------------------------------------------------------------- T8.6
def test_t8_6_audit_catches_late_module_at_next_boundary(scratch):
    root, ps = _build(scratch)
    st = fc.load_closure(ps)
    aud = fc.BoundaryAuditor(st)
    seen = []
    with pytest.raises(fc.ClosureViolation) as e:
        for i in fc.iter_with_audit(range(3), aud):
            seen.append(i)
            if i == 0:  # bypass the finder: direct spec load of an unpinned external file
                p = os.path.join(root, "ext2", "code", "lateload.py")
                spec = importlib.util.spec_from_file_location("lateload", p)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                sys.modules["lateload"] = m
    _stop(e, "lateload")
    assert seen == [0]


def test_t8_6b_clean_loop_audits_every_boundary(scratch):
    root, ps = _build(scratch)
    st = fc.load_closure(ps)
    aud = fc.BoundaryAuditor(st)
    assert list(fc.iter_with_audit(range(4), aud)) == [0, 1, 2, 3]
    assert aud.count == 5


def test_t8_6c_audit_catches_rehash_replace_and_monkeypatch(scratch):
    root, ps = _build(scratch)
    st = fc.load_closure(ps)
    # monkey-patch of a pinned function
    gk = st.module("gpu_kernel")
    orig = gk.combo_count
    gk.combo_count = lambda dims: 0
    with pytest.raises(fc.ClosureViolation) as e:
        fc.audit(st)
    _stop(e, "gpu_kernel.combo_count")
    gk.combo_count = orig
    fc._STATE["latch"] = None
    # module object replaced
    real = sys.modules["ci_lag"]
    sys.modules["ci_lag"] = types.ModuleType("ci_lag")
    with pytest.raises(fc.ClosureViolation):
        fc.audit(st)
    sys.modules["ci_lag"] = real
    fc._STATE["latch"] = None
    # file changed on disk after load (guard lifted for this fixture only)
    fc._STATE["roots"].clear()
    p = os.path.join(root, "ext2", "code", "ci_registry.py")
    with open(p, "ab") as f:
        f.write(b"#\n")
    with pytest.raises(fc.ClosureViolation) as e:
        fc.audit(st)
    _stop(e, "ci_registry")


# ---------------------------------------------------------------- T8.7
@pytest.mark.parametrize("how", ["wb", "ab", "r+b", "xb", "os_open", "rename_into", "remove"])
def test_t8_7_write_under_external_is_78(scratch, how):
    root, ps = _build(scratch)
    fc.load_closure(ps)
    tgt = os.path.join(root, "ext2", "code", "ci_lag.py")
    with pytest.raises(fc.ClosureViolation):
        if how == "os_open":
            os.close(os.open(tgt, os.O_WRONLY))
        elif how == "rename_into":  # src does not exist: a missed refusal can never move a file
            os.rename(os.path.join(scratch, "no_such_src.txt"), os.path.join(root, "ext2", "code", "new.py"))
        elif how == "remove":  # target does not exist: a missed refusal can never delete a file
            os.remove(os.path.join(root, "ext2", "code", "no_such_file.py"))
        elif how == "xb":
            open(os.path.join(root, "ext2", "code", "new2.py"), "xb")
        elif how == "wb":
            open(os.path.join(root, "ext2", "code", "new3.py"), "wb")
        else:
            open(tgt, how)
    assert fc.violation_latched()
    with pytest.raises(SystemExit) as e:
        fc.run_guarded(lambda: None)
    assert e.value.code == 78
    with open(tgt, "rb") as f:  # reads stay allowed
        assert f.read(3) == b'"""'


# ---------------------------------------------------------------- T8.8
def test_t8_8_phase_b_ci_ready_never_imported(scratch):
    root, ps = _build(scratch)
    st = fc.load_closure(ps)
    elsewhere = os.path.join(scratch, "elsewhere")
    with open(os.path.join(STUBS, "phase_b_ci_ready.py"), "rb") as f:
        _write(os.path.join(elsewhere, "phase_b_ci_ready.py"), f.read())
    sys.path.insert(0, elsewhere)
    with pytest.raises(fc.ClosureViolation) as e:
        importlib.import_module("phase_b_ci_ready")
    _stop(e, "phase_b_ci_ready")
    fc._STATE["latch"] = None
    sys.modules["phase_b_ci_ready"] = types.ModuleType("phase_b_ci_ready")
    with pytest.raises(fc.ClosureViolation) as e:
        fc.audit(st)
    _stop(e, "phase_b_ci_ready")


# ---------------------------------------------------------------- T8.9
def test_t8_9_third_party_version_and_freeze(scratch):
    have = fc.third_party_report()
    v = have["numpy"]
    ok = (pins.ThirdPartyPin("numpy", v),)
    r = fc.check_third_party(_build(scratch, third_party=ok)[1], require_pinned=True)
    assert r["versions"]["numpy"] == v
    wrong = (pins.ThirdPartyPin("numpy", v + ".wrong"),)
    with pytest.raises(fc.ClosureViolation) as e:
        fc.check_third_party(_build(scratch, third_party=wrong)[1], require_pinned=True)
    _stop(e, "numpy")
    missing = (pins.ThirdPartyPin("no-such-dist-fulln", "1.0"),)
    with pytest.raises(fc.ClosureViolation):
        fc.check_third_party(_build(scratch, third_party=missing)[1], require_pinned=True)
    fs = fc.freeze_sha256()
    assert fs == _sha(fc.freeze_text().encode("utf-8"))
    assert fc.check_third_party(_build(scratch, third_party=ok, freeze=fs)[1], require_pinned=True)["freeze_ok"]
    with pytest.raises(fc.ClosureViolation) as e:
        fc.check_third_party(_build(scratch, third_party=ok, freeze="f" * 64)[1], require_pinned=True)
    _stop(e, "freeze")
    with pytest.raises(fc.ClosureViolation):  # launch mode needs a freeze pin
        fc.check_third_party(_build(scratch, third_party=ok, freeze=None)[1], require_pinned=True,
                             require_freeze=True)


# ---------------------------------------------------------------- T8.10
def test_t8_10_run_guarded_and_swallowed_violation(scratch):
    def boom():
        raise fc.ClosureViolation("x")
    with pytest.raises(SystemExit) as e:
        fc.run_guarded(boom)
    assert e.value.code == 78
    _reset()
    root, ps = _build(scratch)
    st = fc.load_closure(ps)
    try:  # a caller that swallows the violation
        open(os.path.join(root, "ext2", "code", "ci_lag.py"), "ab")
    except Exception:
        pass
    with pytest.raises(fc.ClosureViolation):
        fc.BoundaryAuditor(st).boundary("tile-0001")
    with pytest.raises(SystemExit) as e:
        fc.run_guarded(lambda: 1)
    assert e.value.code == 78


def test_t8_10b_receipt_new_file_only(scratch):
    root, ps = _build(scratch)
    r = fc.preflight(ps, mode="stub")
    out = os.path.join(scratch, "receipts", "preflight-receipt-%s.json" % ps.modules[0].sha256[:8])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):  # deterministic scratch from an earlier run: exclusive create must refuse
        with pytest.raises(fc.ClosureViolation):
            fc.write_receipt(out, r)
    else:
        sha = fc.write_receipt(out, r)
        with open(out, "rb") as f:
            assert _sha(f.read()) == sha
        with pytest.raises(fc.ClosureViolation):
            fc.write_receipt(out, r)
    with pytest.raises(fc.ClosureViolation):
        fc.write_receipt(os.path.join(root, "r.json"), r, pinset=ps)
    with pytest.raises(fc.ClosureViolation):
        fc.write_receipt(pins.EXTERNAL_ROOT + "\\x\\r.json" if os.name == "nt" else
                         os.path.join(pins.EXTERNAL_ROOT, "r.json"), r)
