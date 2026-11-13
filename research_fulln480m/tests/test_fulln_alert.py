"""BS tests (plan item C10): survivor alert + no-paper-tile-write proof (T-PT1..T-PT3).

Binding: plan r3 33f3ec1e0e07 s7 C10; Boss GO r2 3656382c8a5c; review-fix GO c65ece13d3b2;
RK e1d778426447; HM R2 b88d1b1e0653; RULES c342ecf8ec9a; t2985u.
The run guard is a process-wide audit hook with no disarm, so every guarded case runs in a CHILD
python process (exit code 78 = refused). Scratch: <--basetemp>/bs-fixed/<sha12>/<test>/...
(new per session basetemp; nothing is removed). No network is used: the guard refuses socket.*
events before any connection is attempted.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap

import numpy as np
import pytest

from research_fulln480m import fulln_alert as fa
from research_fulln480m import fulln_closure as fc

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.basename(PKG_DIR)
ROOT = os.path.dirname(PKG_DIR)
FZ = "ab" * 32  # TEST FIXTURE freeze sha
N = 64          # tiny test lattice


@pytest.fixture(autouse=True)
def _latch_reset():
    fc._STATE["latch"] = None
    yield
    fc._STATE["latch"] = None


@pytest.fixture
def scratch(request):
    base = request.config.option.basetemp
    uniq = hashlib.sha256((request.node.nodeid + os.path.abspath(__file__)).encode("utf-8")).hexdigest()[:12]
    if base:
        d = os.path.join(str(base), "bs-fixed", uniq, request.node.name)
    else:
        d = os.path.join(str(request.getfixturevalue("tmp_path")), uniq)
    os.makedirs(os.path.join(d, "RUN_DIR"), exist_ok=True)
    return d


def status(**kw):
    s = {"state": "COMPLETE", "latched": False, "partial": False, "method_frozen": "B",
         "method_run": "B", "a_audit": "NOT_APPLICABLE", "freeze_sha256": FZ}
    s.update(kw)
    return s


def arrays(surv_ids=(), extra_rejected=(), extra_floor=()):
    rej = np.zeros(N, bool)
    flp = np.zeros(N, bool)
    fl = np.full(N, fa.FLAG_BREACH_NOT_EVALUATED, np.uint8)  # non-rejected cells: floors not evaluated
    for i in surv_ids:
        rej[i] = True; flp[i] = True; fl[i] = 0
    for i in extra_rejected:  # rejected by the method but failing a floor
        rej[i] = True; fl[i] = fa.FLAG_HARD_STOP_BREACH
    for i in extra_floor:  # passes floors but not the method
        flp[i] = True
    return rej, flp, fl


def _child(code, scratch):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    pre = textwrap.dedent("""
        import os, sys, json, shutil, socket
        import numpy as np
        from %s import fulln_alert as fa
        from %s import fulln_closure as fc
        S = %r
        RUN = os.path.join(S, "RUN_DIR")
        def main():
    """ % (PKG, PKG, scratch))
    body = textwrap.indent(textwrap.dedent(code), "    ")
    post = "\nfc.run_guarded(main)\nsys.exit(0)\n"
    p = subprocess.run([sys.executable, "-B", "-c", pre + body + post], env=env, cwd=scratch,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------- compute_alert (pure)
def test_c10_1_no_survivor_plain_report():
    a = fa.compute_alert(status(), *arrays(extra_rejected=(3,), extra_floor=(4,)), n_cells=N)
    assert a["k"] == 0 and a["result"] == "NO SURVIVOR" and a["survivor_cells"] == []
    assert a["confirmation_alpha"] is None
    assert "Danish decides" in a["statement"]


def test_c10_2_k_is_full_count_and_alpha_uses_it():
    ids = (1, 5, 9, 17, 20, 31, 33, 40, 47, 50, 58, 63)
    a = fa.compute_alert(status(), *arrays(ids, extra_rejected=(2, 3), extra_floor=(4, 6)), n_cells=N)
    assert a["k"] == 12 and a["survivor_cells"] == list(ids)
    assert a["confirmation_alpha"] == pytest.approx(0.05 / 12, rel=0, abs=0)


@pytest.mark.parametrize("kw", [dict(state="STOPPED"), dict(state="RUNNING"), dict(latched=True),
                                dict(latched=None), dict(partial=True), dict(method_run="A"),
                                dict(method_frozen="A", method_run="A", a_audit="BREACH"),
                                dict(method_frozen="A", method_run="A", a_audit="NOT_APPLICABLE"),
                                dict(a_audit="PASS"), dict(method_frozen="C", method_run="C"),
                                dict(freeze_sha256="AB" * 32)])
def test_c10_3_no_alert_from_bad_runs(kw):
    with pytest.raises(fc.ClosureViolation) as e:
        fa.compute_alert(status(**kw), *arrays((1,)), n_cells=N)
    assert e.value.exit_code == 78


def test_c10_4_method_a_pass_alerts():
    a = fa.compute_alert(status(method_frozen="A", method_run="A", a_audit="PASS"), *arrays((7,)), n_cells=N)
    assert a["k"] == 1 and a["method"] == "A"


def test_c10_5_latched_process_never_alerts():
    fc._STATE["latch"] = "earlier violation"
    with pytest.raises(fc.ClosureViolation):
        fa.compute_alert(status(), *arrays((1,)), n_cells=N)


@pytest.mark.parametrize("bit", [fa.FLAG_BREACH_NOT_EVALUATED, fa.FLAG_HARD_STOP_BREACH,
                                 fa.FLAG_THESIS_CUT_BREACH, fa.FLAG_BLOCK_NOT_COMPUTED])
def test_c10_6_survivor_flag_stops(bit):
    rej, flp, fl = arrays((1, 2))
    fl[2] |= np.uint8(bit)
    with pytest.raises(fc.ClosureViolation):
        fa.compute_alert(status(), rej, flp, fl, n_cells=N)


def test_c10_7_full_lattice_arrays_required():
    rej, flp, fl = arrays((1,))
    with pytest.raises(fc.ClosureViolation):
        fa.compute_alert(status(), rej[:32], flp[:32], fl[:32], n_cells=N)
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        fa.compute_alert(status(), rej, flp, fl)  # default N = 480,522,240
    fc._STATE["latch"] = None
    with pytest.raises(fc.ClosureViolation):
        fa.compute_alert(status(), rej.astype(np.uint8), flp, fl, n_cells=N)


def test_c10_8_write_refused_without_guard(scratch):
    with pytest.raises(fc.ClosureViolation):
        fa.write_survivor_alert(os.path.join(scratch, "RUN_DIR"), status(), *arrays((1,)), n_cells=N)
    assert not os.path.exists(os.path.join(scratch, "RUN_DIR", "results"))


# ---------------------------------------------------------------- guarded child processes
WRITE_ALERT = """
fa.install_run_guard(RUN)
rej = np.zeros(64, bool); flp = np.zeros(64, bool); fl = np.full(64, 128, np.uint8)
for i in (3, 9): rej[i] = True; flp[i] = True; fl[i] = 0
st = {"state": "COMPLETE", "latched": False, "partial": False, "method_frozen": "B",
      "method_run": "B", "a_audit": "NOT_APPLICABLE", "freeze_sha256": "ab" * 32}
print(json.dumps(fa.write_survivor_alert(RUN, st, rej, flp, fl, n_cells=64)))
"""


def test_t_pt2_1_alert_written_only_in_run_dir_results(scratch):
    rc, out = _child(WRITE_ALERT, scratch)
    assert rc == 0, out
    res = os.path.join(scratch, "RUN_DIR", "results")
    assert sorted(os.listdir(res)) == ["SURVIVOR-ALERT.json", "SURVIVOR-ALERT.txt"]
    alert = json.load(open(os.path.join(res, "SURVIVOR-ALERT.json")))
    assert alert["k"] == 2 and alert["survivor_cells"] == [3, 9] and alert["confirmation_alpha"] == 0.025
    assert sorted(os.listdir(os.path.join(scratch, "RUN_DIR"))) == ["results"]
    rc2, out2 = _child(WRITE_ALERT, scratch)  # exclusive create: second write refused
    assert rc2 == 78, out2


def test_t_pt2_2_planted_paper_tile_path_refused(scratch):
    tile = os.path.join(scratch, "outside", "services", "btc-conservative-agent", "paper-tiles", "tile-x.json")
    os.makedirs(os.path.dirname(tile), exist_ok=True)
    rc, out = _child("""
        fa.install_run_guard(RUN)
        with open(%r, "w") as f:
            f.write("{}")
    """ % tile, scratch)
    assert rc == 78, out
    assert not os.path.exists(tile)


@pytest.mark.parametrize("op", [
    "os.close(os.open(OUT, os.O_WRONLY | os.O_CREAT))",
    "open(OUT, 'a').close()",
    "open(OUT, 'r+b').close()",
    "os.mkdir(OUT)",
    "os.rename(INSIDE, OUT)",
    "shutil.copyfile(INSIDE, OUT)",
    "os.remove(EXIST)",
])
def test_t_pt2_3_listed_write_routes_outside_run_dir_refused(scratch, op):
    """Covered routes only; subprocess/os.system/posix_spawn/dir_fd/mkfifo have their own tests."""

    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    exist = os.path.join(out_dir, "exists.txt")
    if not os.path.exists(exist):
        with open(exist, "w") as f:
            f.write("x")
    rc, txt = _child("""
        OUT = %r
        EXIST = %r
        INSIDE = os.path.join(RUN, "inside.txt")
        with open(INSIDE, "w") as f:
            f.write("x")
        fa.install_run_guard(RUN)
        %s
    """ % (os.path.join(out_dir, "new.bin") if "r+b" not in op else exist, exist, op), scratch)
    assert rc == 78, txt
    assert os.path.exists(exist)
    assert not os.path.exists(os.path.join(out_dir, "new.bin"))


def test_t_pt2_4_inside_writes_and_outside_reads_allowed(scratch):
    rc, txt = _child("""
        fa.install_run_guard(RUN)
        with open(os.path.join(RUN, "ok.txt"), "w") as f:
            f.write("ok")
        open(fa.__file__, "rb").close()
    """, scratch)
    assert rc == 0, txt


def test_t_pt2_5_stop_latch_blocks_alert(scratch):
    with open(os.path.join(scratch, "RUN_DIR", "STOP.LATCH"), "w") as f:
        f.write("stop")
    rc, out = _child(WRITE_ALERT, scratch)
    assert rc == 78, out
    assert not os.path.exists(os.path.join(scratch, "RUN_DIR", "results"))


@pytest.mark.parametrize("op", ["socket.create_connection(('127.0.0.1', 9), timeout=1)",
                                "socket.getaddrinfo('localhost', 80)",
                                "socket.socket().connect(('127.0.0.1', 9))"])
def test_t_pt3_network_refused(scratch, op):
    rc, out = _child("""
        fa.install_run_guard(RUN)
        %s
    """ % op, scratch)
    assert rc == 78, out


# ---------------------------------------------------------------- T-PT1 static scan of the bundle
_STDLIB = set(getattr(sys, "stdlib_module_names", ()))
_NETWORK = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
            "xmlrpc", "socketserver", "asyncio", "requests", "websocket", "websockets", "aiohttp"}
_THIRD = {"numpy", "scipy", "pandas", "yaml"}
_FORBID = re.compile(r"(paper|live|tile|bitfinex|order|ledger|relay|exchange|ccxt|wallet|broker|bot)", re.I)
_PATHLIKE = re.compile(r"([\\/]|^[A-Za-z]:)")
_TILEPATH = re.compile(r"(paper|live|tile|order|ledger|services|bitfinex)", re.I)
# Read-only root pinned in B0 pins.py (never a write target; B0 write guard refuses writes under it).
_ALLOWED_PATH_LITERALS = {"C:\\DoxxedCrypto\\external"}


def _bundle_files():
    out = []
    for name in sorted(os.listdir(PKG_DIR)):
        if name.endswith(".py"):
            out.append(os.path.join(PKG_DIR, name))
    return out


def _docstring_nodes(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_t_pt1_bundle_static_scan():
    files = _bundle_files()
    assert any(f.endswith("fulln_alert.py") for f in files)
    for path in files:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + ["%s.%s" % (node.module, a.name) for a in node.names]
            for m in mods:
                top = m.split(".")[0]
                assert top not in _NETWORK, (path, m)
                if top == PKG:
                    assert not _FORBID.search(m[len(PKG):]), (path, m)
                    continue
                assert top in _STDLIB or top in _THIRD, (path, m)
                assert not _FORBID.search(m), (path, m)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                v = node.value
                if v in _ALLOWED_PATH_LITERALS:
                    continue
                if _PATHLIKE.search(v) and _TILEPATH.search(v):
                    raise AssertionError("tile/paper/live path literal in %s: %r" % (path, v))
                if re.match(r"^([A-Za-z]:[\\/]|/|\\\\)", v) and len(v) > 1:
                    raise AssertionError("absolute path literal in %s: %r" % (path, v))
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                if name in ("open", "makedirs", "mkdir", "copyfile", "rename", "replace") and node.args:
                    assert not isinstance(node.args[0], ast.Constant), (path, "literal path in %s()" % name)


def test_t_pt1_scan_catches_planted_writer(scratch):
    """Self-check of the scanner: a planted paper-tile writer must be flagged."""
    planted = 'def w():\n    with open("C:\\\\DoxxedCrypto\\\\services\\\\paper-tiles\\\\t.json", "w") as f:\n        f.write("1")\n'
    tree = ast.parse(planted)
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _PATHLIKE.search(n.value) and _TILEPATH.search(n.value)]
    assert hits


# ---------------------------------------------------------------- review-fix: BS-1 / BS-2 / k>50 (RK e1d7, HM b88d)
def test_c10_9_alpha_uses_full_k_above_50():
    ids = tuple(range(51))
    a = fa.compute_alert(status(), *arrays(ids), n_cells=N)
    assert a["k"] == 51
    assert a["confirmation_alpha"] == pytest.approx(0.05 / 51, rel=0, abs=0)


@pytest.mark.parametrize("bit,name", [
    (fa.FLAG_KC_INELIGIBLE, "KC_INELIGIBLE"),
    (fa.FLAG_N_LT_2, "N_LT_2"),
    (fa.FLAG_V_ZERO, "V_ZERO"),
    (fa.FLAG_BLOCK_NOT_COMPUTED, "BLOCK_NOT_COMPUTED"),
])
def test_c10_10_ineligible_or_bit3_survivor_stops(bit, name):
    rej, flp, fl = arrays((1, 2))
    fl[2] |= np.uint8(bit)
    with pytest.raises(fa.IneligibleSurvivor) as e:
        fa.compute_alert(status(), rej, flp, fl, n_cells=N)
    assert e.value.exit_code == 78
    assert e.value.count == 1
    assert any(t["name"] == name and t["count"] == 1 for t in e.value.trips)


def test_c10_11_method_a_bit3_survivor_stops():
    rej, flp, fl = arrays((7,))
    fl[7] |= np.uint8(fa.FLAG_BLOCK_NOT_COMPUTED)
    with pytest.raises(fa.IneligibleSurvivor) as e:
        fa.compute_alert(status(method_frozen="A", method_run="A", a_audit="PASS"),
                         rej, flp, fl, n_cells=N)
    assert e.value.exit_code == 78
    assert any(t["name"] == "BLOCK_NOT_COMPUTED" for t in e.value.trips)


def test_c10_12_data_thin_on_a_survivor_is_allowed():
    rej, flp, fl = arrays((1,))
    fl[1] |= np.uint8(fa.FLAG_DATA_THIN_ANY)
    a = fa.compute_alert(status(), rej, flp, fl, n_cells=N)
    assert a["k"] == 1 and a["survivor_cells"] == [1]


WRITE_INELIGIBLE = """
fa.install_run_guard(RUN)
rej = np.zeros(64, bool); flp = np.zeros(64, bool); fl = np.full(64, 128, np.uint8)
for i in (3, 9):
    rej[i] = True; flp[i] = True; fl[i] = 0
fl[9] |= 16  # KC_INELIGIBLE
st = {"state": "COMPLETE", "latched": False, "partial": False, "method_frozen": "B",
      "method_run": "B", "a_audit": "NOT_APPLICABLE", "freeze_sha256": "ab" * 32}
fa.write_survivor_alert(RUN, st, rej, flp, fl, n_cells=64)
"""


def test_c10_13_ineligible_write_discloses_failure_record_and_exits_78(scratch):
    rc, out = _child(WRITE_INELIGIBLE, scratch)
    assert rc == 78, out
    res = os.path.join(scratch, "RUN_DIR", "results")
    assert sorted(os.listdir(res)) == ["SURVIVOR-STOP.json", "SURVIVOR-STOP.txt"]
    rec = json.load(open(os.path.join(res, "SURVIVOR-STOP.json")))
    assert rec["result"] == "STOP" and rec["count"] == 1
    assert any(t["name"] == "KC_INELIGIBLE" and t["count"] == 1 for t in rec["trips"])
    assert "STOP" in open(os.path.join(res, "SURVIVOR-STOP.txt")).read()
    assert not os.path.exists(os.path.join(res, "SURVIVOR-ALERT.json"))


@pytest.mark.parametrize("op,label", [
    ("subprocess.run(['/bin/sh', '-c', 'echo pwned > ' + OUT], check=False)", "subprocess"),
    ("os.system('echo pwned > ' + OUT)", "os.system"),
    ("os.posix_spawn('/bin/true', ['/bin/true'], os.environ)", "posix_spawn"),
])
def test_t_pt2_6_process_spawn_refused(scratch, op, label):
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "pwned.txt")
    rc, txt = _child("""
        import subprocess
        OUT = %r
        fa.install_run_guard(RUN)
        %s
    """ % (target, op), scratch)
    assert rc == 78, (label, txt)
    assert not os.path.exists(target)


def test_t_pt2_7_os_open_dir_fd_write_refused(scratch):
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "via-dirfd.bin")
    rc, txt = _child("""
        OUTDIR = %r
        fa.install_run_guard(RUN)
        os.chdir(RUN)
        fd = os.open(OUTDIR, os.O_RDONLY)
        os.close(os.open('via-dirfd.bin', os.O_WRONLY | os.O_CREAT, dir_fd=fd))
    """ % out_dir, scratch)
    assert rc == 78, txt
    assert not os.path.exists(target)


def test_t_pt2_8_mkfifo_outside_refused(scratch):
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "x.fifo")
    rc, txt = _child("""
        OUT = %r
        fa.install_run_guard(RUN)
        os.mkfifo(OUT)
    """ % target, scratch)
    assert rc == 78, txt
    assert not os.path.exists(target)
