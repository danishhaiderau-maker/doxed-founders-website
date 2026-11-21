"""BS tests (plan item C10): survivor alert + no-paper-tile-write proof (T-PT1..T-PT3).

Binding: plan r3 33f3ec1e0e07 s7 C10; Boss GO r2 3656382c8a5c; review-fix GO c65ece13d3b2;
residuals GO ad97fded1831; RK e1d778426447 / bb9b6d5ebedc; HM R2 b88d1b1e0653;
RULES c342ecf8ec9a; t2985u. Combined bypass close/disclose: Boss GO 18b5834de6a4 (proposal r2.1
ba7ce094c98b, final list 429cfff3b75e); RK 45d668eaa8fc / 1fdecbce8d45 / 1caad8ad3e31 / 0d9ffda558c4.
BS-R2 (arming-time open-fd check via /proc/self/fd) is NOT TESTED on Windows.
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
# Explicit denylist (RK bb9b6d5ebedc BS-R1; extended per RK 1caad8ad3e31 Q2/Q5 and 4f5676ff068c).
# These names include stdlib modules; the stdlib/third-party allowlist alone would not fail them.
_DENY_IMPORTS = frozenset({"ctypes", "_ctypes", "cffi", "sqlite3", "_sqlite3", "mmap",
                           "_posixsubprocess", "multiprocessing", "posix", "_winapi",
                           "dbm", "shelve", "nt"})
# Hard-coded ruled names (C6 / T01): the self-check must not shrink with _DENY_IMPORTS.
_RULED_DENY_IMPORTS = ("ctypes", "_ctypes", "cffi", "sqlite3", "_sqlite3", "mmap",
                       "_posixsubprocess", "multiprocessing", "posix", "_winapi",
                       "dbm", "shelve", "nt")
# String-form import calls (S01 / S02) and the ctypes-through-numpy route (S13).
_STRING_IMPORT_CALLS = ("import_module", "__import__")
_DENY_SUBMODULES = ("ctypeslib",)
_KNOWN_RESIDUALS_HEADING = "KNOWN RESIDUALS (RK 45d668eaa8fc)"
_KNOWN_RESIDUALS_BULLETS = "\n- ctypes (ctypes.CDLL / libc open, write, mkdir, system): stays open because a process-wide ctypes/dlopen block would also risk C extensions already loaded (numpy); the bundle does not import ctypes, and test_t_pt1's import allowlist would catch a new import. Coverage: static scan, plus this residual ruling. `from numpy import ctypeslib` is the same class (ctypes reachable through an allowlisted package); the static scan also rejects a ctypeslib import.\n\n- sqlite3.connect: stays open because SQLite's C library can create or write a file without going through the hooked os.open; the bundle does not import sqlite3, and the same static allowlist would catch a new import. Coverage: static scan, plus this residual ruling.\n\n- Pre-armed file descriptors: an fd opened before install_run_guard can still be written with os.write, io.FileIO(fd), sendfile, copy_file_range, or mmap. Stays open because revoking inherited fds would break stdin/stdout/stderr and any fd the caller already holds; the hook applies to new opens only. Coverage: exclusive-create of the alert and STOP files, and the intended run does not open outside paths before arming. BS-R2 checks fd numbers, not targets; stdio redirected to a file before arming is not detected.\n\n- Writes performed inside already-imported C extensions (numpy, etc.) that do not go through os.* or the audit hook: stays open for the same loaded-extension reason as ctypes. Coverage: the bundle's import allowlist, and no new non-stdlib I/O modules.\n\n- Path tricks that never pass a path to a hooked event (for example writing to an fd obtained via a pre-arm open of /proc/self/fd): same class as pre-armed fds. Coverage: same exclusive-create and pre-arm discipline.\n\n- `_posixsubprocess.fork_exec` called directly (plain import and importlib) - spawns /bin/sh with no audit event. Stays open because a reliable process wrap would have to import `_posixsubprocess` at arming, and that import is not added here (it would need a denylist exemption this proposal does not invent); the bundle does not import `_posixsubprocess`. Coverage: `_posixsubprocess` on `_DENY_IMPORTS`, the static scan, plus this residual ruling.\n\n- `multiprocessing` 'spawn' context Process: a fresh interpreter with NO guard runs the target. `multiprocessing.util.spawnv_passfds` (same fork_exec route). Stays open for the same reason as `_posixsubprocess.fork_exec`; the bundle does not import `multiprocessing`. fork and forkserver contexts are already blocked (exit 78). Coverage: `multiprocessing` on `_DENY_IMPORTS`, the static scan, plus this residual ruling.\n\n- an `os.open` reference bound before arming, or `fulln_alert._ORIG['open']`, called with dir_fd. Stays open because already-bound function objects cannot be revoked without walking every binding in the process; the hook applies to new wraps only (same infeasibility as pre-armed fds). A pre-arm dir fd is now refused at arming (BS-R2). Coverage: static T-PT1 no-touch check that no bundle module references `_GUARD` or `_ORIG` outside fulln_alert's own guard code, plus this residual ruling.\n\n- `fulln_alert._GUARD['armed'] = False`, then a normal open() outside (module-state disarm; \"no disarm\" claim is API-only). Stays open because any in-process caller can mutate the module dict; under the bb9b accidental-write threat model a deliberate in-process flip may stay a disclosed residual if a test proves the pinned code never touches it. Coverage: the same static no-touch test, plus this residual ruling.\n\n- Windows `_winapi.CreateProcess`: neither this nor the Windows default multiprocessing 'spawn' path is hooked by name. NOT TESTED on Linux. Stays open pending a Windows runtime test this box cannot run; the bundle does not import `_winapi`. Coverage: `_winapi` on `_DENY_IMPORTS`, the static scan, plus this residual ruling.\n\n- `os.startfile`: NOT TESTED (attribute absent on this Linux box; no ruled Windows run exists). A code CLOSE when the attribute is present is allowed (refuse while armed), but this is listed as a residual until a ruled Windows run exists (RK 1caad8ad3e31 Q8, 0d9ffda558c4 K2, HM check K2). Coverage: refuse when present and armed; proposed test `test_t_pt2_6d_os_startfile_refused` is skipped when the attribute is absent, plus this residual ruling.\n\n- `import dbm` / `import shelve` (stdlib file writers). Stdlib I/O passes the allowlist. RK's wording says \"non-stdlib\", so this is a disclosure, not a breach. Both names are added to `_DENY_IMPORTS`, each with a KILLED mutant (RK 1caad8ad3e31 Q2 and 0d9ffda558c4 K1: a tightening of the guard, fail-closed and inside the accidental-write model, not a search narrowing). Coverage: `dbm` and `shelve` on `_DENY_IMPORTS`, the static scan, plus this residual ruling.\n\n- `nt` (the Windows analogue of `posix`, class 3): direct `nt.*` calls (for example nt.open) skip the os.* wrappers; the runtime `nt` route is NOT TESTED (Windows). Stays open pending a ruled Windows run this box cannot run; this proposal does not wrap `nt`; the bundle does not import `nt`. Coverage: `nt` on `_DENY_IMPORTS`, a KILLED mutant, the static scan, plus this residual ruling.\n\n"


def _assert_import_allowed(path, m):
    """Fail on network, denied, or non-allowlisted imports (explicit denylist + stdlib/allowlist)."""
    top = m.split(".")[0]
    assert top not in _NETWORK, (path, m)
    assert not set(m.split(".")) & set(_DENY_SUBMODULES), (path, "denied import %s" % m)
    if top == PKG:
        assert not _FORBID.search(m[len(PKG):]), (path, m)
        return
    assert top not in _DENY_IMPORTS, (path, "denied import %s" % m)
    assert top in _STDLIB or top in _THIRD, (path, m)
    assert not _FORBID.search(m), (path, m)


def _bundle_files():
    out = []
    for name in sorted(os.listdir(PKG_DIR)):
        if name.endswith(".py"):
            out.append(os.path.join(PKG_DIR, name))
    return out


def _import_names(node):
    """Module names an Import / ImportFrom node brings in (same rule as the T-PT1 scan)."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""] + ["%s.%s" % (node.module, a.name) for a in node.names]
    return []


def _string_form_imports(tree):
    """Calls to importlib.import_module / import_module / __import__ (S01 / S02)."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if name in _STRING_IMPORT_CALLS:
                hits.append((getattr(node, "lineno", 0), name))
    return hits


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
        assert not _string_form_imports(tree), (path, "string-form import call", _string_form_imports(tree))
        for node in ast.walk(tree):
            mods = _import_names(node)
            for m in mods:
                _assert_import_allowed(path, m)
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



# ---------------------------------------------------------------- BS-R1 / BS-R2 / BS-R3 (RK bb9b6d5ebedc)
_LINUX_FD = sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd")
_SKIP_WIN_FD = pytest.mark.skipif(
    not _LINUX_FD,
    reason="NOT TESTED on Windows (BS-R2: Linux /proc/self/fd only)",
)


@pytest.mark.parametrize("mod", list(_RULED_DENY_IMPORTS) + ["h5py"])
def test_t_pt1_denied_or_nonstdlib_io_import_is_caught(mod):
    """Self-check of the allowlist: each named deny-import and a new non-stdlib I/O module fail.
    The list is hard-coded (C6 / T01), and _DENY_IMPORTS must equal the ruled thirteen names."""
    assert _DENY_IMPORTS == frozenset(_RULED_DENY_IMPORTS)
    assert len(_RULED_DENY_IMPORTS) == 13
    tree = ast.parse("import %s\n" % mod)
    node = tree.body[0]
    assert isinstance(node, ast.Import)
    with pytest.raises(AssertionError):
        _assert_import_allowed("<planted>", node.names[0].name)


def test_bs_r3_known_residuals_verbatim_in_module_docstring():
    doc = fa.__doc__ or ""
    assert _KNOWN_RESIDUALS_HEADING in doc
    assert _KNOWN_RESIDUALS_BULLETS in doc
    idx = doc.index(_KNOWN_RESIDUALS_HEADING)
    after = doc[idx + len(_KNOWN_RESIDUALS_HEADING):]
    assert after.startswith("\n") and _KNOWN_RESIDUALS_BULLETS in after
    assert "NOT TESTED" in doc


@_SKIP_WIN_FD
def test_bs_r2_arming_holds_only_stdio_plus_disclosed(scratch):
    """At install_run_guard, Linux /proc/self/fd has only 0,1,2 plus DISCLOSED_ARMING_FDS."""
    rc, out = _child("""
        fa.install_run_guard(RUN)
        allowed = set((0, 1, 2)) | set(fa.DISCLOSED_ARMING_FDS)
        open_fds = fa._linux_open_fds()
        extra = [fd for fd in open_fds if fd not in allowed]
        print({"open": open_fds, "extra": extra, "disclosed": sorted(fa.DISCLOSED_ARMING_FDS)})
        if extra:
            raise SystemExit("unexpected fds after arming: %s" % extra)
    """, scratch)
    assert rc == 0, out


@_SKIP_WIN_FD
def test_bs_r2_prearmed_extra_fd_refused(scratch):
    """An fd opened before install_run_guard must be refused at arming (exit 78)."""
    rc, out = _child("""
        extra = os.open(os.path.join(S, "prearm.bin"), os.O_WRONLY | os.O_CREAT)
        try:
            fa.install_run_guard(RUN)
        finally:
            os.close(extra)
    """, scratch)
    assert rc == 78, out



# ---------------------------------------------------------------- combined bypass close/disclose (GO 18b5834de6a4)
@pytest.mark.parametrize("mod", list(_RULED_DENY_IMPORTS))
def test_t_pt1_string_form_import_is_caught(mod):
    """S01 / S02: importlib.import_module("x") and __import__("x") are caught; the live bundle has none."""
    for planted in ("import importlib\nimportlib.import_module(%r)\n" % mod,
                    "from importlib import import_module\nimport_module(%r)\n" % mod,
                    "__import__(%r)\n" % mod):
        assert _string_form_imports(ast.parse(planted)), planted
    for path in _bundle_files():
        with open(path, encoding="utf-8") as f:
            assert not _string_form_imports(ast.parse(f.read(), path)), path


@pytest.mark.parametrize("planted", ["from numpy import ctypeslib\n", "import numpy.ctypeslib\n",
                                     "from numpy.ctypeslib import load_library\n"])
def test_t_pt1_numpy_ctypeslib_is_caught(planted):
    """S13: ctypes reachable through an allowlisted package (numpy.ctypeslib) is refused."""
    node = ast.parse(planted).body[0]
    with pytest.raises(AssertionError):
        for m in _import_names(node):
            _assert_import_allowed("<planted>", m)


def _guard_touches(path, tree, is_alert):
    """S14 / S15: references to _GUARD / _ORIG outside fulln_alert's own guard code."""
    bad = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", 0)
        if not is_alert:
            if isinstance(node, ast.Name) and node.id in ("_GUARD", "_ORIG"):
                bad.append((path, ln, node.id))
            if isinstance(node, ast.Attribute) and node.attr in ("_GUARD", "_ORIG"):
                bad.append((path, ln, node.attr))
            continue
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Subscript) and isinstance(fn.value, ast.Name) and fn.value.id == "_ORIG":
                bad.append((path, ln, "call through _ORIG[...]"))
            if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id in ("_GUARD", "_ORIG") and fn.attr != "get"):
                bad.append((path, ln, "%s.%s()" % (fn.value.id, fn.attr)))
            if (isinstance(fn, ast.Attribute) and fn.attr == "get" and isinstance(fn.value, ast.Name)
                    and fn.value.id == "_ORIG"):
                bad.append((path, ln, "_ORIG.get()"))
        if isinstance(node, ast.Delete):
            for tg in node.targets:
                if isinstance(tg, ast.Subscript) and isinstance(tg.value, ast.Name) and tg.value.id in ("_GUARD", "_ORIG"):
                    bad.append((path, ln, "del %s[...]" % tg.value.id))
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for tg in targets:
                if isinstance(tg, ast.Name) and tg.id in ("_GUARD", "_ORIG"):
                    if not (isinstance(node, ast.Assign) and isinstance(value, ast.Dict)):
                        bad.append((path, ln, "rebind %s" % tg.id))
                if not (isinstance(tg, ast.Subscript) and isinstance(tg.value, ast.Name)):
                    continue
                key = tg.slice.value if isinstance(tg.slice, ast.Constant) else None
                plain = isinstance(node, ast.Assign)
                if tg.value.id == "_GUARD":
                    if key == "armed" and plain and isinstance(value, ast.Constant) and value.value is True:
                        continue
                    if key == "run_dir" and plain and isinstance(value, ast.Name) and value.id == "root":
                        continue
                    bad.append((path, ln, "_GUARD[%r] write" % (key,)))
                elif tg.value.id == "_ORIG":
                    if (plain and isinstance(tg.slice, ast.Name) and tg.slice.id == "name"
                            and isinstance(value, ast.Name) and value.id == "fn"):
                        continue
                    bad.append((path, ln, "_ORIG[...] write"))
    return bad


def test_t_pt1_no_touch_guard_or_orig_outside_guard_code():
    """Classes 4 / 5 (S14 / S15): no bundle code disarms _GUARD or calls through _ORIG.
    Allowed: the definitions, _GUARD["run_dir"] = root, _GUARD["armed"] = True, _ORIG[name] = fn."""
    bad = []
    for path in _bundle_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        bad += _guard_touches(path, tree, os.path.basename(path) == "fulln_alert.py")
    assert not bad, bad
    planted = ('_GUARD = {"armed": False, "run_dir": None}\n_ORIG = {}\n'
               'def f():\n    _GUARD["armed"] = False\n'
               'def g(p):\n    return _ORIG["open"](p, 0)\n')
    hits = _guard_touches("<planted>", ast.parse(planted), True)
    assert len(hits) == 2, hits
    assert _guard_touches("<planted>", ast.parse("import x\nx._GUARD['armed'] = 0\n"), False)


def test_bs_r2_disclosed_arming_fds_is_empty():
    """M02: nothing is disclosed at arming beyond stdio."""
    assert fa.DISCLOSED_ARMING_FDS == frozenset()


@_SKIP_WIN_FD
@pytest.mark.parametrize("target", [5, 10])
def test_bs_r2_prearmed_high_fd_refused(scratch, target):
    """M10: a pre-arm fd moved to a higher number (5, inside 4-9; and 10) is refused at arming."""
    rc, out = _child("""
        fd = os.open(os.path.join(S, "prearm-hi-%d.bin"), os.O_WRONLY | os.O_CREAT)
        os.dup2(fd, %d)
        os.close(fd)
        try:
            fa.install_run_guard(RUN)
        finally:
            os.close(%d)
    """ % (target, target, target), scratch)
    assert rc == 78, out


@_SKIP_WIN_FD
def test_bs_r2_refused_arming_leaves_guard_unarmed(scratch):
    """M13: a refused arming (extra fd) leaves _GUARD["armed"] False (no half-armed state)."""
    rc, out = _child("""
        extra = os.open(os.path.join(S, "prearm-m13.bin"), os.O_WRONLY | os.O_CREAT)
        try:
            fa.install_run_guard(RUN)
        except fc.ClosureViolation:
            print("ARMED_AFTER_REFUSAL=%r" % (fa._GUARD["armed"],))
            raise
        finally:
            os.close(extra)
    """, scratch)
    assert rc == 78, out
    assert "ARMED_AFTER_REFUSAL=False" in out, out


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="NOT TESTED on Windows (BS-R2 C5: Linux only)")
def test_bs_r2_missing_proc_self_fd_fails_closed(scratch):
    """C5: on Linux, a missing procfs fd directory refuses arming (78); it is not skipped."""
    rc, out = _child("""
        fa._PROC_SELF_FD = os.path.join(S, "missing-proc-self-fd")
        try:
            fa.install_run_guard(RUN)
        except fc.ClosureViolation as e:
            print("ARMED_AFTER_REFUSAL=%r" % (fa._GUARD["armed"],))
            print("REASON=%s" % e.reason)
            raise
    """, scratch)
    assert rc == 78, out
    assert "ARMED_AFTER_REFUSAL=False" in out and "fail closed" in out, out
    assert not os.path.exists(os.path.join(scratch, "missing-proc-self-fd"))


@pytest.mark.parametrize("op", ["os.fork()", "os.forkpty()"])
def test_t_pt2_6b_fork_and_forkpty_refused(scratch, op):
    """HBSa: os.fork / os.forkpty are refused after arming."""
    rc, txt = _child("""
        fa.install_run_guard(RUN)
        r = %s
        pid = r if isinstance(r, int) else r[0]
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        print("FORKED")
    """ % op, scratch)
    assert rc == 78, txt
    assert "FORKED" not in txt


def test_t_pt2_6c_exec_refused(scratch):
    """HBSb: os.exec* is refused after arming; no outside file."""
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "pwned-exec.txt")
    rc, txt = _child("""
        OUT = %r
        fa.install_run_guard(RUN)
        os.execv('/bin/sh', ['/bin/sh', '-c', 'echo pwned > ' + OUT])
    """ % target, scratch)
    assert rc == 78, txt
    assert not os.path.exists(target)


@pytest.mark.skipif(getattr(os, "startfile", None) is None, reason="NOT TESTED on Linux (os.startfile absent)")
def test_t_pt2_6d_os_startfile_refused(scratch):
    """Class 6b: os.startfile is refused while armed. NOT TESTED on Linux (attribute absent)."""
    rc, txt = _child("""
        fa.install_run_guard(RUN)
        os.startfile(os.path.join(S, "x.txt"))
    """, scratch)
    assert rc == 78, txt


@pytest.mark.parametrize("op,made", [
    ("os.rename('exists.txt', 'renamed.txt', src_dir_fd=fd, dst_dir_fd=fd)", "renamed.txt"),
    ("os.symlink('exists.txt', 'link.txt', dir_fd=fd)", "link.txt"),
    ("os.unlink('exists.txt', dir_fd=fd)", None),
    ("os.mkfifo('x.fifo', dir_fd=fd)", "x.fifo"),
])
def test_t_pt2_7b_dir_fd_on_rename_symlink_unlink_mkfifo_refused(scratch, op, made):
    """HBSc: dir_fd is refused on rename / symlink / unlink / mkfifo, not only on os.open."""
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    exist = os.path.join(out_dir, "exists.txt")
    if not os.path.exists(exist):
        with open(exist, "x") as f:
            f.write("x")
    rc, txt = _child("""
        OUTDIR = %r
        fa.install_run_guard(RUN)
        os.chdir(RUN)
        fd = os.open(OUTDIR, os.O_RDONLY)
        %s
    """ % (out_dir, op), scratch)
    assert rc == 78, txt
    assert os.path.exists(exist)
    if made:
        assert not os.path.lexists(os.path.join(out_dir, made))


def test_t_pt2_8b_os_mknod_outside_refused(scratch):
    """HBSg: os.mknod outside RUN_DIR is refused (wrapper check; no audit event)."""
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "x.node")
    rc, txt = _child("""
        OUT = %r
        fa.install_run_guard(RUN)
        os.mknod(OUT)
    """ % target, scratch)
    assert rc == 78, txt
    assert not os.path.lexists(target)


_SKIP_NO_POSIX = pytest.mark.skipif(os.name != "posix", reason="posix module absent (Windows): NOT TESTED")


@_SKIP_NO_POSIX
def test_t_pt2_9_posix_open_dir_fd_refused(scratch):
    """Class 3 (PB-18b): posix.open with dir_fd is refused (wrappers also on the posix module)."""
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "via-posix-dirfd.bin")
    rc, txt = _child("""
        import posix
        OUTDIR = %r
        fa.install_run_guard(RUN)
        os.chdir(RUN)
        fd = os.open(OUTDIR, os.O_RDONLY)
        os.close(posix.open('via-posix-dirfd.bin', os.O_WRONLY | os.O_CREAT, dir_fd=fd))
    """ % out_dir, scratch)
    assert rc == 78, txt
    assert not os.path.lexists(target)


@_SKIP_NO_POSIX
def test_t_pt2_10_posix_mkfifo_outside_refused(scratch):
    """Class 3 (PB-21): posix.mkfifo outside RUN_DIR is refused."""
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "posix.fifo")
    rc, txt = _child("""
        import posix
        OUT = %r
        fa.install_run_guard(RUN)
        posix.mkfifo(OUT)
    """ % target, scratch)
    assert rc == 78, txt
    assert not os.path.lexists(target)


@_SKIP_NO_POSIX
def test_t_pt2_11_posix_mknod_outside_refused(scratch):
    """Class 3 (PB-23): posix.mknod outside RUN_DIR is refused."""
    out_dir = os.path.join(scratch, "outside")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "posix.node")
    rc, txt = _child("""
        import posix
        OUT = %r
        fa.install_run_guard(RUN)
        posix.mknod(OUT)
    """ % target, scratch)
    assert rc == 78, txt
    assert not os.path.lexists(target)


def test_c10_14_failure_count_is_cells_not_trip_kinds():
    """HBSd: the STOP count is the number of survivor cells, not the number of trip kinds."""
    rej, flp, fl = arrays((1, 2, 3))
    fl[2] |= np.uint8(fa.FLAG_KC_INELIGIBLE)
    fl[3] |= np.uint8(fa.FLAG_KC_INELIGIBLE)
    with pytest.raises(fa.IneligibleSurvivor) as e:
        fa.compute_alert(status(), rej, flp, fl, n_cells=N)
    assert e.value.exit_code == 78
    assert e.value.count == 2
    assert len(e.value.trips) == 1 and e.value.trips[0]["count"] == 2


@pytest.mark.parametrize("pre", ["SURVIVOR-STOP.json", "SURVIVOR-STOP.txt", "second-write"])
def test_c10_15_failure_record_exclusive_create(scratch, pre):
    """HBSe: a pre-existing STOP record (or a second ineligible write) exits 78 with no overwrite."""
    res = os.path.join(scratch, "RUN_DIR", "results")
    if pre == "second-write":
        rc0, out0 = _child(WRITE_INELIGIBLE, scratch)
        assert rc0 == 78, out0
        before = {n: hashlib.sha256(open(os.path.join(res, n), "rb").read()).hexdigest()
                  for n in sorted(os.listdir(res))}
        rc, out = _child(WRITE_INELIGIBLE, scratch)
        assert rc == 78, out
        after = {n: hashlib.sha256(open(os.path.join(res, n), "rb").read()).hexdigest()
                 for n in sorted(os.listdir(res))}
        assert after == before and sorted(after) == ["SURVIVOR-STOP.json", "SURVIVOR-STOP.txt"]
        return
    os.makedirs(res, exist_ok=True)
    pre_path = os.path.join(res, pre)
    with open(pre_path, "x") as f:
        f.write("PRE-EXISTING\n")
    rc, out = _child(WRITE_INELIGIBLE, scratch)
    assert rc == 78, out
    assert open(pre_path).read() == "PRE-EXISTING\n"
    assert sorted(os.listdir(res)) == [pre]
