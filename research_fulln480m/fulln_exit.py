"""B-R63X Stage A exit layer (card r2.1 a312 s4 / s6). Live OFF. Paper only. Gross only.

New file. Does not edit fulln_decide / fulln_decide_pins / fulln_closure / gpu_kernel / ext2
or the oracle. Imports decide / pins / closure read-only to pin content shas and to reuse
labels, run_guarded, and check_cp1's neighbour (S-1..S-4 live on the micro). TIME and THESIS
returns are NaN (never 0.0). Output width is always 768 (R63-J; S-14). R63H_CARD_SHA256 and
R63I_CARD_SHA256 stay None; production exit() STOPs 78 on a time/thesis price request or a
thesis kind present (S-9). fulln_exit_stageb is not imported while the H-lock is None.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from research_fulln480m.fulln_closure import ClosureViolation, run_guarded
from research_fulln480m.fulln_decide import NO_FILL, UNFRESH
from research_fulln480m.fulln_micro import check_cp1, window_rows
from research_fulln480m import fulln_exit_pins as pins
from research_fulln480m.fulln_exit_pins import (
    CLOSURE_SHA256,
    DECIDE_PINS_SHA256,
    DECIDE_SHA256,
    EXIT_STOP,
    G3_ORDER,
    HORIZONS_12,
    LIQ_FILL_BP,
    LIQ_FLOOR_BP,
    N_EXITS,
    SL_BP,
    TH_KIND,
    TP_BP,
    TR_BP,
)

_HERE = Path(__file__).resolve().parent
_LABEL_LIQ = "LIQ"
_LABEL_SL = "SL"
_LABEL_TP = "TP"
_LABEL_TR = "TR"
_LABEL_THESIS = "THESIS"
_LABEL_TIME = "TIME"
# Tie-break at the same k (lower wins). LIQ sits in the SL slot and beats a same-row SL.
_PRI_LIQ, _PRI_SL, _PRI_TP, _PRI_TR, _PRI_TH, _PRI_TIME = 0, 1, 2, 3, 4, 5
_ALLOWED_DIR = (1, -1)


def _stop(code, reason):
    raise ClosureViolation("B-R63X STOP 78: %s: %s" % (code, reason))


def _is_int_not_bool(value):
    if type(value) is bool or isinstance(value, np.bool_):
        return False
    if type(value) is int:
        return True
    return isinstance(value, np.integer)


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_startup():
    """S-8: pins literals and content shas of decide / decide_pins / closure."""
    reasons = list(pins.check_literals())
    if G3_ORDER != ("SL", "TP", "TR", "THESIS", "TIME"):
        reasons.append("G3_ORDER")
    for name, pinned in (
        ("fulln_decide.py", DECIDE_SHA256),
        ("fulln_decide_pins.py", DECIDE_PINS_SHA256),
        ("fulln_closure.py", CLOSURE_SHA256),
    ):
        got = _sha256_file(_HERE / name)
        if got != pinned:
            reasons.append("%s sha256 %s != pinned %s" % (name, got, pinned))
    if reasons:
        _stop("S-8", "; ".join(reasons))
    if pins.EXIT_STOP != 78:
        _stop("S-8", "EXIT_STOP")
    return None


def exit_decode(x):
    """EXT2-GRID: th = x % 3; tr = (x // 3) % 4; sl = (x // 12) % 8; tp = x // 96."""
    x = int(x)
    th = x % 3
    tr = (x // 3) % 4
    sl = (x // 12) % 8
    tp = x // 96
    return tp, sl, tr, th


def k_max_i12(T, t_e, h):
    """I.12 K_max = floor((t_e + h - 2 - T) / 60)."""
    return (int(t_e) + int(h) - 2 - int(T)) // 60


def legal_Tprimes(T, t_e, h):
    km = k_max_i12(T, t_e, h)
    if km < 1:
        return ()
    return tuple(int(T) + 60 * j for j in range(1, km + 1))


def _pair(value, name):
    if value is None:
        return (None, None)
    if isinstance(value, (list, tuple, np.ndarray)):
        if len(value) != 2:
            _stop("S-10", "%s must be NONE or a length-2 sequence" % name)
        return (value[0], value[1])
    return (value, value)


def _validate_injected_k(k, t_e, T, h):
    """A raw offered k (may be bool / non-int / out of range) -> legal T' or STOP S-10."""
    if k is None:
        return None
    if type(k) is bool or isinstance(k, np.bool_):
        _stop("S-10", "injected k is a boolean")
    if not _is_int_not_bool(k):
        _stop("S-10", "injected k is not an integer")
    k = int(k)
    if k == 0 or k < 0 or k > (int(h) - 1):
        _stop("S-10", "injected k=%d is not in the I.12 legal set" % k)
    return _validate_injected_Tprime(int(t_e) + k - 1, t_e, T, h)


def _validate_injected_Tprime(Tp, t_e, T, h):
    if Tp is None:
        return None
    if type(Tp) is bool or isinstance(Tp, np.bool_):
        _stop("S-10", "injected T' is a boolean")
    if not _is_int_not_bool(Tp):
        _stop("S-10", "injected T' is not an integer")
    if T is None or not _is_int_not_bool(T):
        _stop("S-10", "T must be an integer when a thesis cut is injected")
    Tp = int(Tp)
    t_e = int(t_e)
    T = int(T)
    h = int(h)
    legal = legal_Tprimes(T, t_e, h)
    if Tp not in legal:
        _stop("S-10", "injected T'=%d is not in the I.12 legal set (K_max=%d)"
              % (Tp, k_max_i12(T, t_e, h)))
    k = Tp + 1 - t_e
    if k < 1 or k > h - 1:
        _stop("S-10", "injected k=%d is not in 1..h-1" % k)
    return Tp


def _thesis_offsets(T, t_e, h, thesis_Tprime, thesis_k):
    """Return k_th length-2 (sentinel = h when NONE)."""
    if thesis_Tprime is not None and thesis_k is not None:
        _stop("S-10", "thesis_Tprime and thesis_k cannot both be given")
    sentinel = int(h)
    k_th = np.full(2, sentinel, dtype=np.int32)
    if thesis_Tprime is None and thesis_k is None:
        return k_th
    if thesis_k is not None:
        pair = _pair(thesis_k, "thesis_k")
        tps = [_validate_injected_k(pair[0], t_e, T, h),
               _validate_injected_k(pair[1], t_e, T, h)]
    else:
        pair = _pair(thesis_Tprime, "thesis_Tprime")
        tps = [_validate_injected_Tprime(pair[0], t_e, T, h),
               _validate_injected_Tprime(pair[1], t_e, T, h)]
    for i, Tp in enumerate(tps):
        if Tp is not None:
            k_th[i] = np.int32(int(Tp) + 1 - int(t_e))
    return k_th


def _thesis_kind_present(thesis_Tprime, thesis_k):
    if thesis_k is not None:
        pair = _pair(thesis_k, "thesis_k")
        return pair[0] is not None or pair[1] is not None
    if thesis_Tprime is None:
        return False
    pair = _pair(thesis_Tprime, "thesis_Tprime")
    return pair[0] is not None or pair[1] is not None


def _first_true(mask, sentinel):
    if not np.any(mask):
        return int(sentinel)
    return int(np.argmax(mask))


def first_hit_tables(micro, t_e, direction, h):
    """Per-level first-hit k and booked g for 7 TP + 7 SL + 3 TR, plus LIQ, on one (t_e, d, h).

    k is 0 at t_e (G.1). Sentinel is h (does not compete). Empty rows cannot trigger (NaN r / g_adv).
    LIQ fires at the first row with g_adv <= -50.0 and books -100.0. All float64 (S-15).
    """
    if not _is_int_not_bool(h) or int(h) not in HORIZONS_12:
        _stop("S-10", "horizon %r is not one of the 12" % (h,))
    if not _is_int_not_bool(direction) or int(direction) not in _ALLOWED_DIR:
        _stop("S-10", "direction %r is not +1 or -1" % (direction,))
    if not _is_int_not_bool(t_e):
        _stop("S-10", "t_e %r is not an integer" % (t_e,))
    h = int(h)
    direction = int(direction)
    t_e = int(t_e)
    w = window_rows(micro, t_e, h)
    # S-1..S-4 on the whole micro (window is a view).
    check_cp1(micro)
    p0 = np.float64(w.open[0])
    if int(w.count[0]) == 0 or not np.isfinite(p0):
        _stop("S-7", "FILLED-row checks: count[t_e]==0 or p0 is not finite")
    last = np.asarray(w.last, dtype=np.float64)
    ext = np.asarray(w.low if direction == 1 else w.high, dtype=np.float64)
    if last.dtype != np.float64 or ext.dtype != np.float64:
        _stop("S-15", "LAST or EXT dtype is not float64")
    with np.errstate(all="ignore"):
        r = np.float64(direction) * (last / p0 - np.float64(1.0)) * np.float64(1e4)
        g_adv = np.float64(direction) * (ext / p0 - np.float64(1.0)) * np.float64(1e4)
    if r.dtype != np.float64 or g_adv.dtype != np.float64:
        _stop("S-15", "r or g_adv dtype is not float64")
    # LIQ on g_adv only (ext2 px is not used). nan <= -50.0 is False.
    if g_adv.dtype != np.float64:
        _stop("S-15", "LIQ comparator input is not float64")
    k_liq = _first_true(g_adv <= np.float64(LIQ_FLOOR_BP), h)
    k_tp = np.full(len(TP_BP) - 1, h, dtype=np.int32)
    g_tp = np.zeros(len(TP_BP) - 1, dtype=np.float64)
    for i, tp in enumerate(TP_BP[1:]):
        k = _first_true(r >= np.float64(tp), h)
        k_tp[i] = np.int32(k)
        if k < h:
            g_tp[i] = np.float64(r[k])
    k_sl = np.full(len(SL_BP) - 1, h, dtype=np.int32)
    g_sl = np.zeros(len(SL_BP) - 1, dtype=np.float64)
    for i, sl in enumerate(SL_BP[1:]):
        k = _first_true(r <= np.float64(-sl), h)
        k_sl[i] = np.int32(k)
        if k < h:
            g_sl[i] = np.float64(r[k])
    # Trail peak = running max of r floored at 0; empty rows are nan and cannot trigger.
    peak = np.maximum.accumulate(np.where(np.isnan(r), -np.inf, r))
    peak = np.maximum(peak, 0.0)
    k_tr = np.full(len(TR_BP) - 1, h, dtype=np.int32)
    g_tr = np.zeros(len(TR_BP) - 1, dtype=np.float64)
    with np.errstate(all="ignore"):
        drawdown = peak - r
    for i, tr in enumerate(TR_BP[1:]):
        k = _first_true(drawdown >= np.float64(tr), h)
        k_tr[i] = np.int32(k)
        if k < h:
            g_tr[i] = np.float64(r[k])
    return {
        "p0": p0,
        "r": r,
        "g_adv": g_adv,
        "k_tp": k_tp, "g_tp": g_tp,
        "k_sl": k_sl, "g_sl": g_sl,
        "k_tr": k_tr, "g_tr": g_tr,
        "k_liq": int(k_liq),
        "g_liq": np.float64(LIQ_FILL_BP),
        "k_time": h - 1,
    }


def combine_from_k(k_tp, g_tp, k_sl, g_sl, k_tr, g_tr, k_th, k_liq, k_time, h, g_liq=None):
    """Pure combiner: 17 first-hit levels + LIQ + injected thesis + TIME -> 768 columns (G.3)."""
    if not _is_int_not_bool(h) or int(h) not in HORIZONS_12:
        _stop("S-10", "horizon %r is not one of the 12" % (h,))
    h = int(h)
    if g_liq is None:
        g_liq = np.float64(LIQ_FILL_BP)
    else:
        g_liq = np.float64(g_liq)
    k_tp = np.asarray(k_tp)
    g_tp = np.asarray(g_tp, dtype=np.float64)
    k_sl = np.asarray(k_sl)
    g_sl = np.asarray(g_sl, dtype=np.float64)
    k_tr = np.asarray(k_tr)
    g_tr = np.asarray(g_tr, dtype=np.float64)
    k_th = np.asarray(k_th)
    k_liq = int(k_liq)
    k_time = int(k_time)
    labels = np.empty(N_EXITS, dtype=object)
    ks = np.empty(N_EXITS, dtype=np.int32)
    gs = np.empty(N_EXITS, dtype=np.float64)
    for x in range(N_EXITS):
        tp_i, sl_i, tr_i, th_i = exit_decode(x)
        best_k = k_time
        best_pri = _PRI_TIME
        best_lab = _LABEL_TIME
        best_g = np.float64(np.nan)

        def _consider(k, pri, lab, g):
            nonlocal best_k, best_pri, best_lab, best_g
            if k is None:
                return
            k = int(k)
            if k < 0 or k >= h:
                return
            if (k, pri) < (best_k, best_pri):
                best_k, best_pri, best_lab, best_g = k, pri, lab, g

        _consider(k_liq, _PRI_LIQ, _LABEL_LIQ, g_liq)
        if sl_i > 0:
            _consider(k_sl[sl_i - 1], _PRI_SL, _LABEL_SL, np.float64(g_sl[sl_i - 1]))
        if tp_i > 0:
            _consider(k_tp[tp_i - 1], _PRI_TP, _LABEL_TP, np.float64(g_tp[tp_i - 1]))
        if tr_i > 0:
            _consider(k_tr[tr_i - 1], _PRI_TR, _LABEL_TR, np.float64(g_tr[tr_i - 1]))
        if th_i > 0:
            _consider(k_th[th_i - 1], _PRI_TH, _LABEL_THESIS, np.float64(np.nan))
        labels[x] = best_lab
        ks[x] = np.int32(best_k)
        gs[x] = np.float64(best_g)
    if labels.shape != (N_EXITS,) or ks.shape != (N_EXITS,) or gs.shape != (N_EXITS,):
        _stop("S-14", "exit output column count is not 768")
    if gs.dtype != np.float64:
        _stop("S-15", "booked g dtype is not float64")
    liq_count = int(np.sum(labels == _LABEL_LIQ))
    return {"label": labels, "k": ks, "g": gs, "liq_count": liq_count}


def exit_core(micro, t_e, direction, h, T=None, thesis_Tprime=None, thesis_k=None, valid=None):
    """Pure core: injected arrays, I.12-validated thesis offsets, 768-column result. Tests may inject."""
    if not _is_int_not_bool(h) or int(h) not in HORIZONS_12:
        _stop("S-10", "horizon %r is not one of the 12" % (h,))
    if not _is_int_not_bool(direction) or int(direction) not in _ALLOWED_DIR:
        _stop("S-10", "direction %r is not +1 or -1" % (direction,))
    if not _is_int_not_bool(t_e):
        _stop("S-10", "t_e %r is not an integer" % (t_e,))
    h = int(h)
    direction = int(direction)
    t_e = int(t_e)
    if valid is not None:
        valid = np.asarray(valid)
        if valid.shape[0] != len(micro.count):
            _stop("S-10", "valid length differs from the micro axis")
        end = t_e + h - 1
        if end >= len(valid) or t_e < 0:
            _stop("S-5", "window rows [%d, %d] outside the valid axis" % (t_e, end))
        if not np.all(valid[t_e:end + 1]):
            _stop("S-5", "a window row is outside the decision's valid span")
    k_th = _thesis_offsets(T, t_e, h, thesis_Tprime, thesis_k)
    tables = first_hit_tables(micro, t_e, direction, h)
    out = combine_from_k(
        tables["k_tp"], tables["g_tp"], tables["k_sl"], tables["g_sl"],
        tables["k_tr"], tables["g_tr"], k_th, tables["k_liq"], tables["k_time"], h,
        g_liq=tables["g_liq"],
    )
    if out["label"].shape != (768,):
        _stop("S-14", "exit output column count is not 768")
    if "exit_valid_mask" in out:
        _stop("S-14", "exit_valid_mask must not be applied to the output")
    return out


def exit(micro, t_e, direction, h, T=None, thesis_Tprime=None, thesis_k=None, valid=None,
         t_x=None, request_time_thesis_price=False):
    """Production entry: start-up pins, S-9 locks, optional t_x check, then exit_core."""
    check_startup()
    if request_time_thesis_price and pins.R63H_CARD_SHA256 is None:
        _stop("S-9", "time/thesis price request while R63H_CARD_SHA256 is None")
    if _thesis_kind_present(thesis_Tprime, thesis_k) and pins.R63I_CARD_SHA256 is None:
        _stop("S-9", "thesis kind present while R63I_CARD_SHA256 is None")
    if t_x is not None:
        if not _is_int_not_bool(t_e) or not _is_int_not_bool(h) or not _is_int_not_bool(t_x):
            _stop("S-10", "t_e / h / t_x must be integers")
        if int(t_x) != int(t_e) + int(h):
            _stop("S-7", "t_x %d != t_e + h = %d" % (int(t_x), int(t_e) + int(h)))
    return exit_core(micro, t_e, direction, h, T=T, thesis_Tprime=thesis_Tprime,
                     thesis_k=thesis_k, valid=valid)


def exit_guarded(*args, **kwargs):
    """exit() under fulln_closure.run_guarded: any ClosureViolation -> sys.exit(78)."""
    return run_guarded(lambda: exit(*args, **kwargs))


def count_n_a(rows):
    """Stage A n_a: NO_FILL + UNFRESH from fulln_decide labels (EMBARGO and BLOCKED excluded)."""
    n = 0
    for row in rows:
        lab = row[1]
        if lab == NO_FILL or lab == UNFRESH:
            n += 1
    return n
