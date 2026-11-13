"""C3 windows, input allowlist gate, DATA-THIN literal and fold-slot plumbing (batch B3a).

Binding: Coder plan r3 33f3ec1e0e07 s7 C3 / s12 B3a; GS card r3 0173cd11a090 s9 (windows,
half-open UTC); HM consult 8b8ec9208448 (f) deny paths + the two additive helpers, H-6 (BL8
fold-id fixture/mutant, literal full-span int32 case); GS review 03221eb7832d C-2/C-3;
RK 22ee55488e0c (no hard-coded staleness bound before the GS pin); RULES d8594afbb02e.

Every refusal raises fulln_closure.ClosureViolation (exit 78, latched). Nothing here writes a
file. Box build evidence only; not a PASS, CLEAR or freeze (R56). Live OFF. Gross only (R61).
"""
import calendar
import hashlib
import io
import ntpath
import os

import numpy as np

from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

# ---------------------------------------------------------------- windows (card r3 s9)
DAY_NS = 86_400 * 1_000_000_000
BARS_PER_DAY = 1_440


def _utc_ns(text):
    """'YYYY-MM-DDT00:00:00Z' -> int ns since epoch, UTC only (never local time)."""
    if not (isinstance(text, str) and len(text) == 20 and text.endswith("Z") and text[10] == "T"):
        raise ClosureViolation("window bound is not a literal UTC timestamp: %r" % (text,))
    y, mo, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
    hh, mm, ss = int(text[11:13]), int(text[14:16]), int(text[17:19])
    return calendar.timegm((y, mo, d, hh, mm, ss, 0, 0, 0)) * 1_000_000_000


_BY_ID = {w.id: w for w in pins.WINDOWS}
DISCOVERY_IDS = pins.DISCOVERY_WINDOW_IDS


def window(window_id):
    try:
        return _BY_ID[window_id]
    except KeyError:
        raise ClosureViolation("unknown window id: %r" % (window_id,))


def window_bounds_ns(window_id):
    """Half-open [start, end) in UTC ns."""
    w = window(window_id)
    lo, hi = _utc_ns(w.start_utc), _utc_ns(w.end_utc)
    if hi - lo != w.days * DAY_NS:
        raise ClosureViolation("window %s bounds disagree with its day count" % window_id)
    return lo, hi


def _check_windows():
    ids = [w.id for w in pins.WINDOWS]
    if ids != ["WU"] + ["W%02d" % i for i in range(1, 13)]:
        raise ClosureViolation("window ids are not WU, W01..W12")
    prev_hi = None
    for w in pins.WINDOWS:
        lo, hi = window_bounds_ns(w.id)
        if prev_hi is not None and lo != prev_hi:
            raise ClosureViolation("windows are not contiguous at %s" % w.id)
        prev_hi = hi
    if sum(window(i).days for i in DISCOVERY_IDS) != pins.K_DAY_BLOCKS:
        raise ClosureViolation("discovery days != K_DAY_BLOCKS")


_check_windows()
DISCOVERY_START_NS = window_bounds_ns(DISCOVERY_IDS[0])[0]
DISCOVERY_END_NS = window_bounds_ns(DISCOVERY_IDS[-1])[1]
FULL_SPAN_BARS = pins.K_DAY_BLOCKS * BARS_PER_DAY  # 1,048,320


def _as_ns(ts_ns):
    a = np.asarray(ts_ns)
    if a.dtype != np.dtype("<i8") and a.dtype != np.dtype("int64"):
        raise ClosureViolation("timestamps must be int64 ns UTC, got %s" % a.dtype)
    return a


def day_block_index(ts_ns):
    """UTC-day block k in 0..727 over W01-W12 (a trade belongs to the block of its entry bar); -1 outside."""
    a = _as_ns(ts_ns)
    inside = (a >= DISCOVERY_START_NS) & (a < DISCOVERY_END_NS)
    k = np.where(inside, (a - DISCOVERY_START_NS) // DAY_NS, -1)
    return k.astype("<i4")


def window_index(ts_ns):
    """0..11 for W01..W12 (half-open UTC), -1 outside the discovery span (WU included)."""
    a = _as_ns(ts_ns)
    out = np.full(a.shape, -1, dtype="<i1")
    for i, wid in enumerate(DISCOVERY_IDS):
        lo, hi = window_bounds_ns(wid)
        out[(a >= lo) & (a < hi)] = i
    return out


def window_bar_mask(window_id, bar_ts_ns):
    """HM (f) additive helper 2: True where a bar's UTC timestamp lies in [start, end) of the window."""
    lo, hi = window_bounds_ns(window_id)
    a = _as_ns(bar_ts_ns)
    return (a >= lo) & (a < hi)


# ---------------------------------------------------------------- fold slots (HM BL8 / GS C-3)
# gpu_kernel N_FOLDS = 4 (laptop read 4ca862a34092 / 023de562cb39; _check_micro L1144-L1145 refuses
# ids outside 0..3). The bundle NEVER uses the kernel fold slots for windows or days: folds = windows are
# computed on the host (plan r3 s7 C3); every bar carries fold id 0.
KERNEL_N_FOLDS = 4
FOLD_DTYPE = "<i4"


def fold_ids_zero(n_bars):
    """HM (f) additive helper 1: the only fold-id array the bundle may hand to the kernel (all 0)."""
    if not isinstance(n_bars, (int, np.integer)) or isinstance(n_bars, bool) or n_bars < 0:
        raise ClosureViolation("n_bars must be a non-negative int")
    return np.zeros(int(n_bars), dtype=FOLD_DTYPE)


def assert_fold_ids_zero(fold):
    """Refuse (78) any micro fold array that is not int32 all-zero (fold ids encoding windows/days)."""
    a = np.asarray(fold)
    if a.dtype != np.dtype(FOLD_DTYPE):
        raise ClosureViolation("fold ids must be int32, got %s" % a.dtype)
    if a.ndim != 1:
        raise ClosureViolation("fold ids must be 1-D per bar")
    if a.size and (int(a.min()) != 0 or int(a.max()) != 0):
        raise ClosureViolation("fold ids encode windows/days (non-zero id) - refused (HM BL8)")


# ---------------------------------------------------------------- int32 launch guard (GS C-2, HM H-6)
INT32_MAX = 2 ** 31 - 1
LATTICE_EXITS_PER_DIRECTION = pins.N_EXITS  # 768
LATTICE_DIRECTIONS = pins.N_DIRECTIONS  # 2


def launch_index_extents(n_bars, n_horizons):
    """Both index forms named in GS C-2: Coder's job count 2 x bars x 768 and HM's flat bars x n_h x 1,536."""
    for v in (n_bars, n_horizons):
        if not isinstance(v, (int, np.integer)) or isinstance(v, bool) or v <= 0:
            raise ClosureViolation("launch extents must be positive ints")
    jobs = LATTICE_DIRECTIONS * int(n_bars) * LATTICE_EXITS_PER_DIRECTION
    flat = int(n_bars) * int(n_horizons) * (LATTICE_DIRECTIONS * LATTICE_EXITS_PER_DIRECTION)
    return {"jobs_2xbarsx768": jobs, "flat_barsxnhx1536": flat}


def assert_int32_launch(n_bars, n_horizons):
    """Fail closed (78) if EITHER index form exceeds int32 (which one the kernel guards is OPEN, GS C-2)."""
    ext = launch_index_extents(n_bars, n_horizons)
    over = {k: v for k, v in ext.items() if v > INT32_MAX}
    if over:
        raise ClosureViolation("int32 launch guard: %s exceeds %d" % (over, INT32_MAX))
    return ext


# ---------------------------------------------------------------- DATA-THIN literal (plan r3 s7 C3)
DATA_THIN_MIN_HOURS = 20.0  # a UTC day counts as covered with >= 20 h
DATA_THIN_FRACTION_NUM, DATA_THIN_FRACTION_DEN = 9, 10  # thin if < 90% of the window's UTC days are covered


def data_thin(window_id, channel, day_hours):
    """True iff fewer than 90% of the window's UTC days have >= 20 h coverage (per channel x window).

    DATA-THIN = non-positive fold, kept in the denominator; the cell stays in N (never dropped).
    day_hours: float64 hours covered per UTC day of the window, computed from rows by the loader.
    """
    w = window(window_id)
    if w.role != "discovery":
        raise ClosureViolation("DATA-THIN is defined for discovery windows only: %s" % window_id)
    if not isinstance(channel, str) or not channel:
        raise ClosureViolation("channel name required")
    h = np.asarray(day_hours)
    if h.dtype != np.dtype("<f8") and h.dtype != np.dtype("float64"):
        raise ClosureViolation("day_hours must be float64")
    if h.shape != (w.days,):
        raise ClosureViolation("day_hours length %s != %d days of %s" % (h.shape, w.days, window_id))
    if not np.all(np.isfinite(h)) or np.any(h < 0.0) or np.any(h > 24.0):
        raise ClosureViolation("day_hours outside [0, 24] or not finite")
    covered = int(np.count_nonzero(h >= DATA_THIN_MIN_HOURS))
    return DATA_THIN_FRACTION_DEN * covered < DATA_THIN_FRACTION_NUM * w.days


def fold_positive(window_net_bp, thin):
    """window_pos_mask (u16 bits 0-11 = W01..W12 positive) and the positive-fold count.

    window_net_bp: f8[12, T] gross net per discovery window; thin: bool[12, T] DATA-THIN for any input
    the cell reads. A DATA-THIN window is NON-POSITIVE and stays in the 12-window denominator.
    """
    net = np.asarray(window_net_bp)
    th = np.asarray(thin)
    if net.dtype != np.dtype("float64") or net.ndim != 2 or net.shape[0] != len(DISCOVERY_IDS):
        raise ClosureViolation("window_net_bp must be float64 [12, T]")
    if th.dtype != np.bool_ or th.shape != net.shape:
        raise ClosureViolation("thin must be bool [12, T]")
    if not np.all(np.isfinite(net)):
        raise ClosureViolation("window net NaN/Inf")
    pos = (net > 0.0) & ~th
    mask = np.zeros(net.shape[1], dtype="<u2")
    for i in range(net.shape[0]):
        mask |= (pos[i].astype("<u2") << np.uint16(i))
    n_pos = pos.sum(axis=0).astype("<u1")
    return mask, n_pos


def fold_floor_pass(n_pos):
    """R59 fold floor: >= 9 of 12 discovery windows positive (denominator always 12)."""
    return np.asarray(n_pos).astype(np.int64) >= pins.FOLD_FLOOR


# ---------------------------------------------------------------- NB-4 staleness (NOT PINNED)
# RK 2e6b9127bf3e (2) requires a staleness mask; GS pins the freshness bound and mask in r5/A1.
# RK 22ee55488e0c: no hard-coded bound before that pin. Until a new sha of this file carries the
# GS-pinned value, any request for the bound refuses (78).
STALENESS_BOUND_S = None


def staleness_bound_s(channel):
    raise ClosureViolation("NB-4 staleness bound for %r is NOT PINNED (GS r5/A1 pending) - refused" % (channel,))


# ---------------------------------------------------------------- input allowlist gate
# HM (f): deny the r2c tree and the r2c tool (card r2c 8ed21ea1256e); card r3 s7 item 7.
DENY_PREFIXES = (
    "C:\\DoxxedCrypto\\data\\binance-spot-1s-20260913-20261112-r1",
    "C:\\DoxxedCrypto\\tools\\binance_spot_1s_dl_r2b.py",
)


def _is_windows_style(p):
    return len(p) >= 2 and p[1] == ":" or "\\" in p


def norm_path(p):
    """Normalised, case-insensitive comparison form (Windows rules for Windows-style strings)."""
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    p = os.fspath(p)
    if not isinstance(p, str) or not p:
        raise ClosureViolation("path must be a non-empty string")
    if "\x00" in p:
        raise ClosureViolation("NUL in path")
    if p.startswith("\\\\?\\"):
        p = p[4:]
    if _is_windows_style(p):
        return ntpath.normpath(p).casefold()
    return os.path.normpath(p).casefold()


def _deny_sep(norm):
    """Separator consistent with how norm_path encoded this form.

    Windows-style forms (drive letter or backslash, via ntpath) use ntpath.sep
    even when this module is imported on a POSIX host. POSIX forms use os.sep.
    """
    if _is_windows_style(norm):
        return ntpath.sep
    return os.sep


def _strip_trailing_sep(norm):
    """Strip trailing separators, leaving a drive root ('c:\\') or '/' intact."""
    sep = _deny_sep(norm)
    alt = ntpath.altsep if _is_windows_style(norm) else (os.altsep or "")
    while len(norm) > 1:
        is_drive_root = (
            _is_windows_style(norm)
            and len(norm) == 3
            and norm[1] == ":"
            and (norm.endswith(sep) or (alt and norm.endswith(alt)))
        )
        if is_drive_root:
            break
        if norm.endswith(sep) or (alt and norm.endswith(alt)):
            norm = norm[:-1]
            continue
        break
    return norm


def _denied(norm, deny_norms):
    # Deny only on exact match or a child path (startswith prefix + separator).
    # Trailing separators are stripped first. Look-alike siblings such as
    # '...-r1-copy' or '...\\r10' are not denied (HM 1e293c804a56).
    n = _strip_trailing_sep(norm)
    for d0 in deny_norms:
        d = _strip_trailing_sep(d0)
        sep = _deny_sep(d)
        if n == d or n.startswith(d + sep):
            return True
    return False


class AllowlistGate(object):
    """Read-only, manifest-gated opens. path + size + sha256 must match the manifest; else 78.

    The returned stream holds exactly the bytes that were hashed (hash-then-use, no re-open).
    Deny check runs on the given path AND on its realpath (junction/symlink safe).
    """

    def __init__(self, manifest, deny_prefixes=DENY_PREFIXES):
        self._deny = tuple(norm_path(d) for d in deny_prefixes)
        for need in DENY_PREFIXES:
            if norm_path(need) not in self._deny:
                raise ClosureViolation("deny list must contain %s" % need)
        self._m = {}
        for path, entry in dict(manifest).items():
            n = norm_path(path)
            if _denied(n, self._deny):
                raise ClosureViolation("manifest names a denied path: %s" % path)
            try:
                size, sha = entry
            except (TypeError, ValueError):
                raise ClosureViolation("manifest entry must be (size, sha256)")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ClosureViolation("manifest size must be a non-negative int: %s" % path)
            if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
                raise ClosureViolation("manifest sha256 must be 64 lowercase hex: %s" % path)
            if n in self._m:
                raise ClosureViolation("duplicate manifest path (case-insensitive): %s" % path)
            self._m[n] = (size, sha)
        self.opened = {}

    def _check_denied(self, path):
        n = norm_path(path)
        if _denied(n, self._deny):
            raise ClosureViolation("denied input path: %s" % path)
        real = norm_path(os.path.realpath(os.fspath(path)))
        if _denied(real, self._deny):
            raise ClosureViolation("denied input path via realpath: %s" % path)
        return n, real

    def open(self, path):
        n, real = self._check_denied(path)
        entry = self._m.get(n)
        if entry is None:
            raise ClosureViolation("path not in the input allowlist: %s" % path)
        size, sha = entry
        with open(os.fspath(path), "rb") as f:  # read-only
            data = f.read()
        if len(data) != size:
            raise ClosureViolation("allowlist size mismatch %s: %d != %d" % (path, len(data), size))
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            raise ClosureViolation("allowlist sha256 mismatch %s: %s != %s" % (path, got[:12], sha[:12]))
        self.opened[n] = got
        return io.BytesIO(data)
