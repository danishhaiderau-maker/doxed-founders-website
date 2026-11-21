"""C8 Route-2 closure loader for the BTC full-N 480,522,240 search (batch B0).

Binding: RK b0eed943d057 conditions 1-6, RK 729f8bc6bd1c (phase_b_ci_ready never
imported; Q-R1 third-party pins), GS card r3 0173cd11a090, stop spec r3 76dc076cd771.

- Every closure file is pinned by full sha256 (pins.py). Each module is read ONCE
  (read-only), hashed, and exactly those bytes are compiled and executed.
- A meta-path finder placed first refuses any import that would resolve under the
  external root and is not loaded by this loader, every forbidden name, and every
  pinned-but-not-loaded name.
- audit() re-runs the sys.modules audit, re-hashes every pin and checks that no
  pinned function/class was replaced (monkey-patch detection). Run it at every
  batch/tile boundary (BoundaryAuditor / iter_with_audit).
- A process audit hook refuses any write/create/rename/delete under the root.
- Any violation raises ClosureViolation (exit code 78) and latches; run_guarded()
  turns either into sys.exit(78). This module never assigns attributes of a pinned
  module (NO monkey-patching) and never edits, copies or moves an existing file.
"""
import datetime
import hashlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import inspect
import json
import os
import platform
import re
import sys
import threading

from research_fulln480m import pins

EXIT_STOP = pins.EXIT_STOP

# Module-level state. There is deliberately no public API that disables the guard.
_STATE = {"latch": None, "roots": set(), "hook": False}
_TLS = threading.local()


class ClosureViolation(Exception):
    """A Route-2 violation. Always exit 78; always latched."""

    exit_code = EXIT_STOP

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = str(reason)
        if _STATE["latch"] is None:
            _STATE["latch"] = self.reason


def violation_latched():
    return _STATE["latch"]


def assert_no_latch():
    if _STATE["latch"] is not None:
        raise ClosureViolation("latched violation: %s" % _STATE["latch"])


def run_guarded(fn):
    """Run fn; a ClosureViolation, or a latch left by a swallowed one, exits 78."""
    try:
        result = fn()
    except ClosureViolation:
        sys.exit(EXIT_STOP)
    if _STATE["latch"] is not None:
        sys.exit(EXIT_STOP)
    return result


# ---------------------------------------------------------------- paths / hashing
def _norm(path):
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _under(norm_path, norm_root):
    return norm_path == norm_root or norm_path.startswith(norm_root.rstrip(os.sep) + os.sep)


def _under_any_root(path):
    if not _STATE["roots"]:
        return False
    n = _norm(path)
    return any(_under(n, r) for r in _STATE["roots"])


def _pin_path(pinset, rel_path):
    return os.path.join(pinset.external_root, *rel_path.split("/"))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_pinned(path, size, sha256):
    """One read-only open; returns exactly the bytes that were hashed."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise ClosureViolation("cannot read pinned file %s: %s" % (path, exc))
    if len(data) != size:
        raise ClosureViolation("size mismatch %s: %d != pinned %d" % (path, len(data), size))
    got = sha256_bytes(data)
    if got != sha256:
        raise ClosureViolation("sha256 mismatch %s: %s != pinned %s" % (path, got, sha256))
    return data


# ---------------------------------------------------------------- write guard
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_PATH_EVENTS_ARG0 = {"os.remove", "os.rmdir", "os.mkdir", "os.truncate", "os.chmod", "os.chown",
                     "os.utime", "shutil.rmtree", "os.chflags", "os.setxattr", "os.removexattr"}
_PATH_EVENTS_ARG01 = {"os.rename", "os.link", "os.symlink", "shutil.copyfile", "shutil.copymode",
                      "shutil.copystat", "shutil.move", "shutil.copytree"}


def _is_path(x):
    return isinstance(x, (str, bytes, os.PathLike))


def _audit_hook(event, args):
    if not _STATE["roots"]:
        return
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if not _is_path(path):
            return
        writing = False
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            writing = True
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            writing = True
        if writing and _under_any_root(path):
            raise ClosureViolation("write-mode open under external root: %s (%s)" % (path, mode or flags))
    elif event in _PATH_EVENTS_ARG0:
        if args and _is_path(args[0]) and _under_any_root(args[0]):
            raise ClosureViolation("%s under external root: %s" % (event, args[0]))
    elif event in _PATH_EVENTS_ARG01:
        for a in tuple(args)[:2]:
            if _is_path(a) and _under_any_root(a):
                raise ClosureViolation("%s touching external root: %s" % (event, a))


def install_write_guard(root):
    if not _STATE["hook"]:
        sys.addaudithook(_audit_hook)
        _STATE["hook"] = True
    _STATE["roots"].add(_norm(root))


# ---------------------------------------------------------------- finder / loader
class RefusingFinder(importlib.abc.MetaPathFinder):
    """First on sys.meta_path. Refuses; never loads anything itself."""

    def __init__(self, pinset):
        self.pinset = pinset
        self.root = _norm(pinset.external_root)
        self.load_names = frozenset(pinset.load_names)
        self.not_loaded = frozenset(m.name for m in pinset.not_loaded)

    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if top in self.pinset.forbidden_names or fullname in self.pinset.forbidden_names:
            raise ClosureViolation("forbidden module import refused: %s" % fullname)
        if fullname in self.not_loaded:
            raise ClosureViolation("pinned-but-not-loaded module import refused: %s" % fullname)
        if fullname in self.load_names:
            # loaded modules are served from sys.modules; reaching here means out of turn
            raise ClosureViolation("pinned module %s imported outside the loader or before its turn" % fullname)
        if getattr(_TLS, "busy", False):
            return None
        _TLS.busy = True
        try:
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            _TLS.busy = False
        if spec is not None:
            locs = []
            if spec.origin:
                locs.append(spec.origin)
            locs.extend(spec.submodule_search_locations or [])
            for loc in locs:
                if loc and loc not in ("built-in", "frozen") and _under(_norm(loc), self.root):
                    raise ClosureViolation("unpinned module under external root refused: %s (%s)" % (fullname, loc))
        return None


class _PinnedBytesLoader(importlib.abc.Loader):
    def __init__(self, data, path):
        self._data = data
        self._path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        code = compile(self._data, self._path, "exec", dont_inherit=True)
        exec(code, module.__dict__)

    def get_source(self, fullname):
        return self._data.decode("utf-8", errors="replace")


def _install_finder(pinset):
    for f in sys.meta_path:
        if isinstance(f, RefusingFinder) and f.pinset is pinset:
            return f
    f = RefusingFinder(pinset)
    sys.meta_path.insert(0, f)
    return f


def _snapshot(name, module):
    snap = {}
    for k, v in list(vars(module).items()):
        if (inspect.isfunction(v) or inspect.isclass(v)) and getattr(v, "__module__", None) == name:
            snap[k] = v
    return snap


class ClosureState(object):
    def __init__(self, pinset, modules, snapshots, finder):
        self.pinset = pinset
        self.modules = modules
        self.snapshots = snapshots
        self.finder = finder

    def module(self, name):
        return self.modules[name]


def load_closure(pinset):
    """Hash-then-exec every load-set module, in pins order."""
    assert_no_latch()
    install_write_guard(pinset.external_root)
    finder = _install_finder(pinset)
    for name in tuple(pinset.forbidden_names) + pinset.load_names + tuple(m.name for m in pinset.not_loaded):
        if name in sys.modules:
            raise ClosureViolation("module %s already in sys.modules before load_closure" % name)
    modules, snaps = {}, {}
    try:
        _load_all(pinset, modules, snaps)
    except BaseException:
        # a partial closure is never left registered (dict entries only; no file is touched)
        for name in modules:
            sys.modules.pop(name, None)
        raise
    return ClosureState(pinset, modules, snaps, finder)


def _load_all(pinset, modules, snaps):
    for pin in pinset.modules:
        path = _pin_path(pinset, pin.rel_path)
        data = read_pinned(path, pin.size, pin.sha256)
        loader = _PinnedBytesLoader(data, path)
        spec = importlib.machinery.ModuleSpec(pin.name, loader, origin=path)
        spec.has_location = True
        module = importlib.util.module_from_spec(spec)
        sys.modules[pin.name] = module
        try:
            loader.exec_module(module)
        except ClosureViolation:
            sys.modules.pop(pin.name, None)
            raise
        except BaseException as exc:  # noqa: BLE001 - any failure is a STOP
            sys.modules.pop(pin.name, None)
            raise ClosureViolation("exec of pinned module %s failed: %r" % (pin.name, exc))
        modules[pin.name] = module
        snaps[pin.name] = _snapshot(pin.name, module)


def assert_kernel_image(state):
    want = state.pinset.kernel_image_sha256
    if want is None:
        raise ClosureViolation("kernel image sha not pinned")
    got = state.module("gpu_kernel").kernel_sha256()
    if got != want:
        raise ClosureViolation("kernel image sha mismatch: %s != pinned %s" % (got, want))
    return got


def _module_locations(module):
    locs = []
    f = getattr(module, "__file__", None)
    if isinstance(f, str):
        locs.append(f)
    spec = getattr(module, "__spec__", None)
    if spec is not None and isinstance(getattr(spec, "origin", None), str):
        locs.append(spec.origin)
    p = getattr(module, "__path__", None)
    if p is not None:
        try:
            locs.extend(x for x in list(p) if isinstance(x, str))
        except TypeError:
            pass
    return [x for x in locs if x not in ("built-in", "frozen")]


def _hash_all(pinset):
    out = {"modules": {}, "data_files": {}}
    for pin in pinset.modules + pinset.not_loaded:
        read_pinned(_pin_path(pinset, pin.rel_path), pin.size, pin.sha256)
        out["modules"][pin.name] = {"rel_path": pin.rel_path, "size": pin.size, "sha256": pin.sha256}
    for pin in pinset.data_files:
        read_pinned(_pin_path(pinset, pin.rel_path), pin.size, pin.sha256)
        out["data_files"][pin.rel_path] = {"size": pin.size, "sha256": pin.sha256}
    return out


def audit(state):
    """sys.modules audit + full re-hash + monkey-patch check. Any finding = 78."""
    assert_no_latch()
    ps = state.pinset
    root = _norm(ps.external_root)
    for name in ps.forbidden_names:
        if name in sys.modules:
            raise ClosureViolation("forbidden module present in sys.modules: %s" % name)
    for m in ps.not_loaded:
        if m.name in sys.modules:
            raise ClosureViolation("pinned-but-not-loaded module present in sys.modules: %s" % m.name)
    for name, mod in state.modules.items():
        if sys.modules.get(name) is not mod:
            raise ClosureViolation("pinned module %s missing or replaced in sys.modules" % name)
    loaded_ids = {id(m) for m in state.modules.values()}
    n_checked = 0
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        for loc in _module_locations(mod):
            if _under(_norm(loc), root) and id(mod) not in loaded_ids:
                raise ClosureViolation("unpinned module under external root in sys.modules: %s (%s)" % (name, loc))
        n_checked += 1
    hashes = _hash_all(ps)
    for name, snap in state.snapshots.items():
        mod = state.modules[name]
        for attr, obj in snap.items():
            if mod.__dict__.get(attr) is not obj:
                raise ClosureViolation("monkey-patch detected: %s.%s replaced" % (name, attr))
    assert_no_latch()
    return {"ok": True, "sys_modules_checked": n_checked, "pins_rehashed": len(hashes["modules"]) + len(hashes["data_files"])}


class BoundaryAuditor(object):
    def __init__(self, state):
        self.state = state
        self.count = 0
        self.labels = []

    def boundary(self, label):
        result = audit(self.state)
        self.count += 1
        self.labels.append(str(label))
        return result


def iter_with_audit(items, auditor):
    """Audit before every item and once after the last (tile boundaries)."""
    for i, item in enumerate(items):
        auditor.boundary("before-%d" % i)
        yield item
    auditor.boundary("end")


# ---------------------------------------------------------------- third party (RK 729f8 Q-R1)
def _normalize_dist(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def third_party_report():
    out = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if name:
            out.setdefault(_normalize_dist(name), dist.version)
    return out


def freeze_text():
    """pip-freeze-equivalent text from importlib.metadata: sorted 'name==version' lines."""
    return "".join("%s==%s\n" % kv for kv in sorted(third_party_report().items()))


def freeze_sha256():
    return sha256_bytes(freeze_text().encode("utf-8"))


def check_third_party(pinset, require_pinned, require_freeze=False):
    have = third_party_report()
    versions = {}
    for pin in pinset.third_party:
        dist = _normalize_dist(pin.dist)
        got = have.get(dist)
        if pin.version is None:
            if require_pinned:
                raise ClosureViolation("third-party %s version not pinned" % dist)
        elif got is None:
            raise ClosureViolation("third-party %s not installed (pinned %s)" % (dist, pin.version))
        elif got != pin.version:
            raise ClosureViolation("third-party %s version mismatch: %s != pinned %s" % (dist, got, pin.version))
        versions[dist] = got
    actual = freeze_sha256()
    freeze_ok = False
    if pinset.pip_freeze_sha256 is None:
        if require_freeze:
            raise ClosureViolation("pip freeze sha not pinned")
    elif actual != pinset.pip_freeze_sha256:
        raise ClosureViolation("pip freeze sha mismatch: %s != pinned %s" % (actual, pinset.pip_freeze_sha256))
    else:
        freeze_ok = True
    return {"versions": versions, "freeze_sha256": actual, "freeze_ok": freeze_ok}


# ---------------------------------------------------------------- preflight / receipt
def preflight(pinset, mode):
    if mode not in ("launch", "stub"):
        raise ValueError("mode must be 'launch' or 'stub'")
    launch = mode == "launch"
    if launch and not pinset.closure_complete:
        raise ClosureViolation("closure enumeration incomplete (card r3 Appendix C); launch refused")
    tp = check_third_party(pinset, require_pinned=launch, require_freeze=launch)
    if launch and pinset.external_root != pins.EXTERNAL_ROOT:
        raise ClosureViolation("launch pinset root %s is not %s" % (pinset.external_root, pins.EXTERNAL_ROOT))
    hashes = _hash_all(pinset)
    return {
        "kind": "fulln480m-closure-preflight",
        "mode": mode,
        "label": pinset.label,
        "utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules_sha256": pins.RULES_SHA256,
        "card_sha256": pins.CARD_SHA256,
        "stopspec_sha256": pins.STOPSPEC_SHA256,
        "external_root": pinset.external_root,
        "closure_complete": pinset.closure_complete,
        "forbidden_names": sorted(pinset.forbidden_names),
        "modules": hashes["modules"],
        "data_files": hashes["data_files"],
        "kernel_image_sha256": pinset.kernel_image_sha256,
        "third_party": tp,
        "python": sys.version,
        "platform": platform.platform(),
    }


def write_receipt(path, receipt, pinset=None, max_bytes=5_000_000):
    """New file only (exclusive create), never under the real or the pinset's external root."""
    n = _norm(path)
    roots = [_norm(pins.EXTERNAL_ROOT)] + ([_norm(pinset.external_root)] if pinset is not None else [])
    if _under_any_root(path) or any(_under(n, r) for r in roots):
        raise ClosureViolation("receipt path under external root refused: %s" % path)
    data = (json.dumps(receipt, sort_keys=True, indent=1) + "\n").encode("utf-8")
    if len(data) > max_bytes:
        raise ClosureViolation("receipt larger than %d B" % max_bytes)
    try:
        with open(path, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        raise ClosureViolation("receipt exists; no overwrite: %s" % path)
    return sha256_bytes(data)
