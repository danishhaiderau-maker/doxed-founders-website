"""C10 survivor alert + no-paper-tile path (batch BS) for the BTC full-N 480,522,240 search.

Binding: plan r3 33f3ec1e0e07 s7 C10 / s12 BS; Boss GO r2 3656382c8a5c (BS = plan item C10, not the
G24 stoptest); review-fix GO c65ece13d3b2; RK 22ee55488e0c / e1d778426447; HM R2 b88d1b1e0653
(s3(b) flag table); RULES c342ecf8ec9a R66(g); Danish t2985u (nothing enters paper automatically;
Danish picks). The alert payload still carries pins.RULES_SHA256 from B0 until that revision.

- compute_alert(): pure. k = number of cells that pass the ONE frozen method AND all floors on
  W01-W12, counted over the FULL lattice (arrays of length N); confirmation alpha = 0.05 / k with
  k = that full count (never a pick list, never min(k, 50)). k = 0 gives a plain "no survivor" report.
- No alert from a STOPPED / latched / partial run, from a method that differs from the frozen
  method, or from a method-A run whose audit is not PASS (A-audit BREACH): ClosureViolation (78).
- A survivor with BREACH_NOT_EVALUATED (bit 7) or a breach bit (0/1) set is a STOP (78). A survivor
  with KC_INELIGIBLE / N_LT_2 / V_ZERO (bits 4/5/6, any method) or BLOCK_NOT_COMPUTED (bit 3,
  method A or B) is a STOP (78). write_survivor_alert then writes a disclosed failure record
  (what tripped, count) under RUN_DIR/results/SURVIVOR-STOP.json and .txt, exclusive create,
  so the outcome reported is a stop, not silence. DATA_THIN_ANY (bit 2) on a survivor is allowed.
- write_survivor_alert(): on a passing compute, writes ONLY <RUN_DIR>/results/SURVIVOR-ALERT.json
  and .txt, exclusive create, and only while install_run_guard(RUN_DIR) is armed in this process.
- install_run_guard(): process audit hook plus wrappers (no uninstall API). Refuses: write-open,
  create, rename, delete, copy or chmod outside RUN_DIR; any socket.* event; subprocess.Popen,
  os.system, os.exec*, os.posix_spawn*, os.fork / os.forkpty; any hooked path/open event that
  carries a dir_fd; os.mkfifo / os.mknod outside RUN_DIR. Residual unhooked routes (ctypes,
  sqlite3, pre-armed fds) are listed for RK, not claimed as closed.
- This module imports no repo service, tile, ledger, order, exchange or network module and holds
  no path literal for writing.
"""
import json
import os
import sys

import numpy as np

from research_fulln480m import fulln_closure
from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

ALERT_JSON = "SURVIVOR-ALERT.json"
ALERT_TXT = "SURVIVOR-ALERT.txt"
FAILURE_JSON = "SURVIVOR-STOP.json"
FAILURE_TXT = "SURVIVOR-STOP.txt"
RESULTS_DIR = "results"
STOP_LATCH = "STOP.LATCH"
# Flag bits used here (HM 8b8ec9208448 s3(b); same table as B4 fulln_accum.FLAG_TABLE).
FLAG_HARD_STOP_BREACH = 1 << 0
FLAG_THESIS_CUT_BREACH = 1 << 1
FLAG_DATA_THIN_ANY = 1 << 2
FLAG_BLOCK_NOT_COMPUTED = 1 << 3
FLAG_KC_INELIGIBLE = 1 << 4
FLAG_N_LT_2 = 1 << 5
FLAG_V_ZERO = 1 << 6
FLAG_BREACH_NOT_EVALUATED = 1 << 7
FLAGS_INELIGIBLE = FLAG_KC_INELIGIBLE | FLAG_N_LT_2 | FLAG_V_ZERO
FLAGS_SURVIVOR_STOP = FLAGS_INELIGIBLE | FLAG_BLOCK_NOT_COMPUTED
_INELIGIBLE_BITS = (
    (FLAG_KC_INELIGIBLE, "KC_INELIGIBLE"),
    (FLAG_N_LT_2, "N_LT_2"),
    (FLAG_V_ZERO, "V_ZERO"),
    (FLAG_BLOCK_NOT_COMPUTED, "BLOCK_NOT_COMPUTED"),
)
STATEMENT = ("Survivor alert only. No paper or live tile is created or changed by this bundle; "
             "Danish decides which strategies, if any, go to paper (t2985u, R66(g)).")

_GUARD = {"armed": False, "run_dir": None}
_WRAP_DONE = False
_ORIG = {}
_DIRFD_KW = ("dir_fd", "src_dir_fd", "dst_dir_fd")
_SPAWN_EVENTS = {
    "os.system", "os.exec", "os.posix_spawn", "os.posix_spawnp",
    "os.fork", "os.forkpty", "subprocess.Popen", "os.popen",
}


class IneligibleSurvivor(ClosureViolation):
    """A survivor carried an INELIGIBLE bit (4/5/6) or bit 3 (any method). Exit 78."""

    def __init__(self, reason, trips, count):
        super().__init__(reason)
        self.trips = list(trips)
        self.count = int(count)


def _norm(p):
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    return os.path.normcase(os.path.realpath(os.fspath(p)))


def _inside(path, root):
    try:
        return os.path.commonpath([_norm(path), root]) == root
    except ValueError:
        return False


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_PATH_EVENTS = {"os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.rename": (0, 1),
                "os.link": (0, 1), "os.symlink": (0, 1), "os.truncate": (0,), "os.chmod": (0,),
                "os.chown": (0,), "os.utime": (0,), "shutil.copyfile": (1,), "shutil.copytree": (1,),
                "shutil.move": (0, 1), "shutil.rmtree": (0,), "shutil.copymode": (1,),
                "shutil.copystat": (1,), "shutil.chown": (0,), "os.removexattr": (0,), "os.setxattr": (0,),
                "os.mkfifo": (0,), "os.mknod": (0,)}


def _is_path(x):
    return isinstance(x, (str, bytes, os.PathLike))


def _hook(event, args):
    if not _GUARD["armed"]:
        return
    root = _GUARD["run_dir"]
    if event in _SPAWN_EVENTS or event.startswith(("os.exec", "os.posix_spawn", "os.fork", "os.spawn")):
        raise ClosureViolation("process-spawn audit: %s refused (no child process / exec in the bundle)" % event)
    if event.startswith("socket."):
        raise ClosureViolation("network audit: %s refused (no network in the bundle)" % event)
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if not _is_path(path):
            return
        write = False
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            write = True
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            write = True
        if write and not _inside(path, root):
            raise ClosureViolation("write-open outside RUN_DIR refused: %r" % (path,))
        return
    idx = _PATH_EVENTS.get(event)
    if idx:
        for i in idx:
            if i < len(args) and _is_path(args[i]) and not _inside(args[i], root):
                raise ClosureViolation("%s outside RUN_DIR refused: %r" % (event, args[i]))


def _install_wrappers():
    """Wrap os.* functions that take dir_fd, plus mkfifo/mknod (no audit event on this Python)."""
    global _WRAP_DONE
    if _WRAP_DONE:
        return
    names = ("open", "mkdir", "rmdir", "remove", "unlink", "rename", "replace",
             "link", "symlink", "truncate", "chmod", "chown", "utime",
             "mkfifo", "mknod", "removexattr", "setxattr")
    for name in names:
        fn = getattr(os, name, None)
        if fn is None:
            continue
        _ORIG[name] = fn

        def _make(n, orig):
            def wrapped(*args, **kwargs):
                if _GUARD["armed"] and any(kwargs.get(k) is not None for k in _DIRFD_KW):
                    raise ClosureViolation("%s with dir_fd refused" % n)
                if _GUARD["armed"] and n in ("mkfifo", "mknod"):
                    path = args[0] if args else kwargs.get("path")
                    if _is_path(path) and not _inside(path, _GUARD["run_dir"]):
                        raise ClosureViolation("%s outside RUN_DIR refused: %r" % (n, path))
                return orig(*args, **kwargs)
            wrapped.__name__ = n
            wrapped.__qualname__ = n
            return wrapped

        setattr(os, name, _make(name, fn))
    _WRAP_DONE = True


def install_run_guard(run_dir):
    """Arm the process-wide write/network/spawn/dir_fd/mkfifo guard for RUN_DIR. There is no disarm."""
    root = _norm(run_dir)
    if not os.path.isdir(root):
        raise ClosureViolation("RUN_DIR does not exist")
    if _GUARD["armed"]:
        if _GUARD["run_dir"] != root:
            raise ClosureViolation("run guard already armed for another RUN_DIR")
        return
    _GUARD["run_dir"] = root
    _GUARD["armed"] = True
    _install_wrappers()
    sys.addaudithook(_hook)


def compute_alert(status, rejected, floors_pass, flags, n_cells=None):
    """Pure: the alert content. status keys: state, latched, partial, method_frozen, method_run,
    a_audit, freeze_sha256. Arrays: bool[N], bool[N], u1[N] over the FULL lattice."""
    expect_n = pins.N_CELLS if n_cells is None else int(n_cells)
    if not isinstance(status, dict):
        raise ClosureViolation("status must be a dict")
    if fulln_closure.violation_latched() is not None or status.get("latched") is not False:
        raise ClosureViolation("no alert from a latched run")
    if status.get("state") != "COMPLETE":
        raise ClosureViolation("no alert from a STOPPED or unfinished run (state %r)" % (status.get("state"),))
    if status.get("partial") is not False:
        raise ClosureViolation("no alert from a partial run")
    method = status.get("method_frozen")
    if method not in ("A", "B") or status.get("method_run") != method:
        raise ClosureViolation("run method must equal the ONE frozen method")
    audit = status.get("a_audit")
    if method == "A" and audit != "PASS":
        raise ClosureViolation("no alert from a method-A run whose audit is %r (BREACH = no alert)" % (audit,))
    if method == "B" and audit != "NOT_APPLICABLE":
        raise ClosureViolation("method B has no A-audit; a_audit must be NOT_APPLICABLE")
    fz = status.get("freeze_sha256")
    if not isinstance(fz, str) or len(fz) != 64 or any(c not in "0123456789abcdef" for c in fz):
        raise ClosureViolation("freeze_sha256 must be 64 lowercase hex")
    rej = np.asarray(rejected)
    flp = np.asarray(floors_pass)
    fl = np.asarray(flags)
    if rej.dtype != np.bool_ or flp.dtype != np.bool_ or fl.dtype != np.uint8:
        raise ClosureViolation("rejected/floors_pass must be bool, flags uint8")
    if not (rej.shape == flp.shape == fl.shape == (expect_n,)):
        raise ClosureViolation("arrays must cover the full lattice (%d cells)" % expect_n)
    surv = rej & flp
    ids = np.flatnonzero(surv)
    k = int(ids.size)
    sf = fl[ids]
    trips = []
    for mask, name in _INELIGIBLE_BITS:
        c = int(np.count_nonzero((sf & np.uint8(mask)) != 0))
        if c:
            trips.append({"bit": int(mask).bit_length() - 1, "name": name, "count": c})
    if trips:
        n_bad = int(np.count_nonzero((sf & np.uint8(FLAGS_SURVIVOR_STOP)) != 0))
        raise IneligibleSurvivor(
            "INELIGIBLE or BLOCK_NOT_COMPUTED on a survivor (STOP 78): trips=%s count=%d"
            % (trips, n_bad), trips, n_bad)
    if np.any(sf & np.uint8(FLAG_BREACH_NOT_EVALUATED)):
        raise ClosureViolation("BREACH_NOT_EVALUATED on a survivor (STOP 78)")
    if np.any(sf & np.uint8(FLAG_HARD_STOP_BREACH | FLAG_THESIS_CUT_BREACH)):
        raise ClosureViolation("breach bit set on a cell reported as passing all floors (STOP 78)")
    alpha = (pins.ALPHA_FWER / k) if k > 0 else None
    return {
        "k": k,
        "survivor_cells": [int(i) for i in ids],
        "n_cells": expect_n,
        "method": method,
        "a_audit": audit,
        "freeze_sha256": fz,
        "confirmation_alpha": alpha,
        "confirmation_alpha_rule": "0.05 / k, k = full survivor count over all cells (never a pick list)",
        "windows": list(pins.DISCOVERY_WINDOW_IDS),
        "rules_sha256": pins.RULES_SHA256,
        "result": "SURVIVORS" if k > 0 else "NO SURVIVOR",
        "statement": STATEMENT,
    }


def _txt(alert):
    lines = ["BTC full-N survivor alert", "result: %s" % alert["result"], "k: %d" % alert["k"],
             "method: %s (a_audit %s)" % (alert["method"], alert["a_audit"]),
             "cells searched: %d over %s-%s" % (alert["n_cells"], alert["windows"][0], alert["windows"][-1]),
             "freeze sha256: %s" % alert["freeze_sha256"]]
    if alert["k"] > 0:
        lines.append("confirmation alpha: %.6g (= 0.05 / %d)" % (alert["confirmation_alpha"], alert["k"]))
        lines.append("survivor cells: " + ", ".join(str(i) for i in alert["survivor_cells"]))
    else:
        lines.append("No cell passed the frozen method and all floors. No survivor.")
    lines.append(alert["statement"])
    return "\n".join(lines) + "\n"


def write_survivor_alert(run_dir, status, rejected, floors_pass, flags, n_cells=None):
    """Write the two alert files (exclusive create) under RUN_DIR/results. Guard must be armed."""
    root = _norm(run_dir)
    if not _GUARD["armed"] or _GUARD["run_dir"] != root:
        raise ClosureViolation("install_run_guard(RUN_DIR) must be armed before writing the alert")
    if os.path.lexists(os.path.join(root, STOP_LATCH)):
        raise ClosureViolation("STOP.LATCH present: no alert")
    try:
        alert = compute_alert(status, rejected, floors_pass, flags, n_cells)
    except IneligibleSurvivor as exc:
        _write_failure_record(root, exc, status)
        raise
    res = os.path.join(root, RESULTS_DIR)
    os.makedirs(res, exist_ok=True)
    pj, pt = os.path.join(res, ALERT_JSON), os.path.join(res, ALERT_TXT)
    if os.path.lexists(pj) or os.path.lexists(pt):
        raise ClosureViolation("alert files already exist (exclusive create; no overwrite)")
    body = json.dumps(alert, sort_keys=True, indent=1).encode("utf-8") + b"\n"
    with open(pj, "xb") as f:
        f.write(body)
    with open(pt, "x", encoding="utf-8", newline="\n") as f:
        f.write(_txt(alert))
    return {"json": pj, "txt": pt, "json_sha256": fulln_closure.sha256_bytes(body), "k": alert["k"]}


def _write_failure_record(root, exc, status):
    """Exclusive-create the disclosed STOP record (what tripped, count). Then the caller re-raises."""
    res = os.path.join(root, RESULTS_DIR)
    os.makedirs(res, exist_ok=True)
    rec = {
        "result": "STOP",
        "reason": "INELIGIBLE survivor(s)",
        "trips": exc.trips,
        "count": exc.count,
        "method": status.get("method_frozen"),
        "a_audit": status.get("a_audit"),
        "freeze_sha256": status.get("freeze_sha256"),
        "rules_sha256": pins.RULES_SHA256,
        "statement": ("Search STOPPED. Ineligible cells were presented as survivors. "
                      "Nothing enters paper. This stop is disclosed; it is not a survivor list."),
    }
    pj, pt = os.path.join(res, FAILURE_JSON), os.path.join(res, FAILURE_TXT)
    if os.path.lexists(pj) or os.path.lexists(pt):
        raise ClosureViolation("failure record already exists (exclusive create; no overwrite)")
    body = json.dumps(rec, sort_keys=True, indent=1).encode("utf-8") + b"\n"
    with open(pj, "xb") as f:
        f.write(body)
    lines = ["BTC full-N survivor STOP",
             "result: STOP",
             "count: %d" % rec["count"],
             "method: %s" % rec["method"],
             "trips: " + ", ".join("%s (bit %d, n=%d)" % (t["name"], t["bit"], t["count"]) for t in rec["trips"]),
             rec["statement"]]
    with open(pt, "x", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return {"json": pj, "txt": pt}
