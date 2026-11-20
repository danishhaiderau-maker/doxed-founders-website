"""B-R63D decision layer for the BTC full-N 480M search: labels, t_e and SCHEDULED t_x only.

Binding: card B-R63D r2.2 24430bc38561 (Boss GO e8963195b8f3; RK PASS aa84c3bb89e9); RK
cfb28db913dd D1-D3; adopted pins R63-A (X1, scheduled t_x = t_e + h only), R63-B .. R63-E and
R63-F.1 .. F.6 (A9 r10 7579003b2b41 L109-L201; plan r4.4.4 a59b s12G L709-L719 + ERRATUM-1 E1-2).
Rules c342ecf8ec9a. Live OFF. Paper only. Gross only (R61).

D1 scope: this module reads NO exit price, no X4 first-hit path, no UNPRICED_WORST, no LIQ, no
thesis cut, no BR panel, and never a cursor that depends on an actual path-exit second. A FILLED
decision sets the scheduled cursor t_x = t_e + h only (A9 r10 L158 "regardless of any earlier
TP / SL / trail / thesis / LIQ exit"). H1-H10 stay HELD.

Per cell (signal, h), applied before regime rows and identical across all 768 exit columns
(A9 r10 L158 C-1), every decision instant t_d is evaluated CURSOR -> EMBARGO -> FRESHNESS -> FILL
(R63-F.1, A9 r10 L190) and gets exactly one label in BLOCKED > EMBARGO > UNFRESH > NO_FILL > FILLED
(R63-F.5 (c)). Freshness is also evaluated on EMBARGO decisions for the EMBARGO_AND_STALE
sub-count ONLY (plan L715; A9 r10 L200).

Two entry points (HM C6; GS MINOR-5):
- decide_core(): the PURE CORE. It takes any integer decision instant t_d (synthetic off-grid
  rows O-c / BND-CUR-* / T2a exist only in tests that call it directly).
- decide(): the PRODUCTION entry. It reads the config from fulln_decide_pins only, asserts
  T % 60 == 0 for every decision (STOP 78 otherwise), checks EM6 UTC-day alignment of every
  evaluated-window end and valid-span end (STOP 78), then calls the core. There is no keyword,
  flag or argument on the production path that admits an off-grid instant.

Every refusal raises research_fulln480m.fulln_closure.ClosureViolation (exit code 78, latched);
decide_guarded() turns it into sys.exit(78) via run_guarded. fulln_closure.py is imported
read-only and its full sha256 is checked at start-up against fulln_decide_pins.CLOSURE_SHA256
(RK 7a06 C.5). The test-only reference oracle tests/ref_clock_oracle.py is never imported here
(RK 4de6 O1). pins.py (batch-1 constants) is not imported by this module (GS MINOR-4); it is
loaded only transitively by fulln_closure.
"""
import hashlib
import os

import numpy as np

from research_fulln480m import fulln_decide_pins as _pins
from research_fulln480m.fulln_closure import ClosureViolation, run_guarded  # imports:closure

EXIT_STOP = 78
BLOCKED = "BLOCKED"
EMBARGO = "EMBARGO"
UNFRESH = "UNFRESH"
NO_FILL = "NO_FILL"
FILLED = "FILLED"
LABELS = (BLOCKED, EMBARGO, UNFRESH, NO_FILL, FILLED)  # R63-F.5 (c) precedence order
EMBARGO_AND_STALE = "EMBARGO_AND_STALE"
FAILDAY_IN_EMBARGO = "FAILDAY_IN_EMBARGO"
FAILDAY_IN_EMBARGO_AND_STALE = "FAILDAY_IN_EMBARGO_AND_STALE"
DENOMINATOR = "DENOMINATOR"
COUNT_KEYS = LABELS + (EMBARGO_AND_STALE, FAILDAY_IN_EMBARGO, FAILDAY_IN_EMBARGO_AND_STALE, DENOMINATOR)
# Trade-capture denominator (GS MINOR-3; E2, EM3, plan L718): BLOCKED is NOT in it.
IN_DENOMINATOR = (EMBARGO, UNFRESH, NO_FILL, FILLED)  # denominator:set
NO_TE = -1  # t_e sentinel for every non-FILLED decision
HORIZONS_12 = tuple(_pins.HORIZONS_12)
E1_LAST = 5  # A9 r10 L139 EM2 / L158 C-1: "T + 5 s + h" (latest fill second of E1)
CLOSURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fulln_closure.py")


def _stop(reason):
    raise ClosureViolation("B-R63D STOP 78: %s" % reason)


# ---------------------------------------------------------------- start-up checks
def check_closure_sha(path=CLOSURE_PATH, pinned=_pins.CLOSURE_SHA256):
    """RK 7a06 C.5: read-only hash of fulln_closure.py; a mismatch is STOP 78."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        _stop("CLOSURE-SHA: cannot read %s: %s" % (path, exc))
    got = hashlib.sha256(data).hexdigest()
    if got != pinned:  # closure:cmp
        _stop("CLOSURE-SHA: %s sha256 %s != pinned %s" % (path, got, pinned))
    return got


def check_pins(p=_pins):
    """HM C4: the pins module must hold STALE_S == 600 (int) and EXIT_CLOCK == 'X1'."""
    s = getattr(p, "STALE_S", None)
    if type(s) is not int or s != 600:
        _stop("PINS-ASSERT: STALE_S %r != 600" % (s,))
    if getattr(p, "EXIT_CLOCK", None) != "X1":
        _stop("PINS-ASSERT: EXIT_CLOCK %r != 'X1'" % (getattr(p, "EXIT_CLOCK", None),))


def pins_config(p=_pins):
    return {"EXIT_CLOCK": p.EXIT_CLOCK, "STALE_S": p.STALE_S, "ENTRY_WINDOW": tuple(p.ENTRY_WINDOW)}


def check_startup_config(config):
    """R63-F.3 (A9 r10 L195) and eda4 A2 (iii) (plan L719 (iii)): before any decision.

    Any exit clock other than X1, or a missing one -> STOP 78. Any stale value other than the int
    literal 600 (599, 601, missing, override) -> STOP 78.
    """
    if not isinstance(config, dict):
        _stop("CONFIG: not a dict")
    if config.get("EXIT_CLOCK", None) != "X1":  # startup:clock
        _stop("F3-CLOCK: exit clock %r is not X1" % (config.get("EXIT_CLOCK", None),))
    s = config.get("STALE_S", None)
    if type(s) is not int or s != 600:  # startup:stale
        _stop("STALE-PIN: stale_s %r is not the literal 600" % (s,))


def check_em8(config):
    """EM8 (A9 r10 L146; RK 4c1d K1): R63-B is valid only with the X1 clock AND the E1 window."""
    win = config.get("ENTRY_WINDOW", None)
    try:
        win = tuple(win)
    except TypeError:
        win = None
    if config.get("EXIT_CLOCK", None) != "X1" or win != (1, E1_LAST):  # em8:check
        _stop("EM8: R63-B in force with exit clock %r / entry window %r (needs X1 + E1)"
              % (config.get("EXIT_CLOCK", None), win))


# ---------------------------------------------------------------- spans, freshness, fill
def valid_spans(valid, window_ends=()):
    """EM1: maximal runs of PASS seconds, cut at every evaluated-window end (F.5 (b)).

    Returns (sid, lastv): sid[s] = span index of second s (-1 if not valid); lastv[s] =
    last_valid(s), the last second of the span containing s (-1 if not valid).
    """
    v = np.asarray(valid, dtype=bool)
    S = v.size
    cut = np.zeros(S, dtype=bool)
    for w in window_ends:
        cut[int(w)] = True
    cont = np.zeros(S, dtype=bool)              # cont[i]: second i continues the span of i - 1
    if S > 1:
        cont[1:] = v[1:] & v[:-1] & ~cut[:-1]
    start = v & ~cont
    sid = np.where(v, np.cumsum(start) - 1, -1).astype(np.int64)
    end = np.zeros(S, dtype=bool)
    if S > 0:
        end[:-1] = v[:-1] & ~cont[1:]
        end[-1] = v[-1]
    ends = np.flatnonzero(end).astype(np.int64)
    lastv = np.where(v, ends[np.clip(sid, 0, max(ends.size - 1, 0))] if ends.size else -1, -1)
    return sid, lastv.astype(np.int64)


def span_starts(valid, window_ends=()):
    """nstart[s] = number of valid-span first seconds in [0, s] (F.5 (b) reset points)."""
    sid, _ = valid_spans(valid, window_ends)
    return np.maximum.accumulate(np.where(sid >= 0, sid, -1)) + 1


def check_utc_alignment(valid, window_ends=()):
    """EM6 (A9 r10 L143): every evaluated window end and every valid-span end is a UTC-day
    boundary (last + 1 is a multiple of 86,400 s); otherwise STOP 78."""
    v = np.asarray(valid, dtype=bool)
    S = v.size
    day = _pins.DAY_S
    ends = sorted(set(int(w) for w in window_ends) | {S - 1})
    for w in ends:
        if (w + 1) % day != 0:  # em6:window
            _stop("EM6: evaluated window end %d is not on a UTC-day boundary" % w)
    _sid, lastv = valid_spans(v, window_ends)
    span_ends = np.unique(lastv[lastv >= 0])
    bad = span_ends[(span_ends + 1) % day != 0]
    if bad.size:  # em6:span
        _stop("EM6: valid-span end %d is not on a UTC-day boundary" % int(bad[0]))


def is_fresh(last_tr, valid, t_d, stale_s):
    """R63-F.6 / ext2 6b5956fdc19f L780-L782 + L873-L874 in decision-time terms (deci = t_d - 1).

    FRESH iff deci >= 0 and lt >= 0 and (deci - lt) < stale_s and valid[deci], lt = last_tr[deci]:
    T - lt in 1..600 is FRESH, 601 is UNFRESH; m = 0 (deci < 0) is STALE. The replaced entry-side
    clause ext2 L875-L876 is NOT part of fresh (E3; plan L719 (3)).
    """
    deci = t_d - 1  # fresh:deci
    if deci < 0:
        return False
    lt = int(last_tr[deci])
    return lt >= 0 and (deci - lt) < stale_s and bool(valid[deci])  # fresh:pred


def first_fill(has, T, lo, hi):
    """E1 (A9 r10 L111; R63-F.2): first second s in T+lo .. T+hi with has[s]; else NO_TE."""
    n = len(has)
    for s in range(T + lo, T + hi + 1):
        if 0 <= s < n and has[s]:
            return int(s)
    return NO_TE


def next_cursor(T, t_e, h):
    """R63-C / C-1 (A9 r10 L158): FILLED -> t_x = t_e + h (scheduled, X1); EMBARGO / UNFRESH /
    NO_FILL -> t_x = T + 5 + h."""
    if t_e != NO_TE:
        return t_e + h
    return T + E1_LAST + h


def cursor_admits(t_d, t_x):
    """R63-D (decision-keyed, A9 r10 L158 / L163) with R63-E: enters iff t_d >= t_x.

    t_x None is the minus-infinity cursor (no active cursor). At t_d == t_x the tie ENTERS under
    the X1 clock (R63-E (1)); X1 is the only clock production can run (F.3), so the BLOCK branch
    of R63-E exists only in the test-only reference oracle.
    """
    if t_x is None:  # cursor:none
        return True
    if t_d == t_x:
        return True  # cursor:tie
    return t_d > t_x  # cursor:cmp


def c2_ok(new_te, prior_tx):
    """C-2 (A9 r10 L159): new entry second STRICTLY later than the prior exit row t_x - 1."""
    return new_te > prior_tx - 1  # c2:cmp


def _zero_counts():
    return {k: 0 for k in COUNT_KEYS}


def _tally(c, label, stale, failday):
    c[label] += 1
    if label in IN_DENOMINATOR:
        c[DENOMINATOR] += 1
    if label == EMBARGO:
        if stale:
            c[EMBARGO_AND_STALE] += 1
        if failday:  # tally:failday
            c[FAILDAY_IN_EMBARGO] += 1
            if stale:
                c[FAILDAY_IN_EMBARGO_AND_STALE] += 1


def _check_inputs(has, last_tr, valid, decisions, h, direction, folds):
    if h not in HORIZONS_12 or type(h) is not int:
        _stop("H12: horizon %r is not one of the 12" % (h,))
    if direction not in (1, -1) or type(direction) is not int:
        _stop("DIRECTION: %r" % (direction,))
    S = len(valid)
    if len(has) != S or len(last_tr) != S:
        _stop("ARRAYS: has / last_tr / valid lengths differ")
    prev = None
    for T in decisions:
        if type(T) is not int and not isinstance(T, np.integer):
            _stop("DECISIONS: non-integer instant %r" % (T,))
        if T < 0 or T >= S:
            _stop("DECISIONS: instant %d outside [0, %d)" % (T, S))
        if prev is not None and T <= prev:
            _stop("DECISIONS: instants not strictly increasing at %d" % T)
        prev = T
    if folds is not None and len(folds) != len(decisions):
        _stop("FOLDS: length differs from decisions")


# ---------------------------------------------------------------- the pure core
def decide_core(has, last_tr, valid, decisions, h, config, window_ends=(), direction=1, folds=None):
    """Label every decision of one cell (signal, h). PURE CORE: any integer t_d.

    Inputs are injected directly (GC-3): has[s] (1 s row has trades > 0), last_tr[s] (last traded
    second <= s, else -1) and valid[s] (PASS second) are NOT cross-derived here.
    Returns {"cell", "rows": [(t_d, label, t_e, t_x)], "counts", "fold_counts"}; t_e is NO_TE
    unless FILLED; t_x is the scheduled cursor (unchanged cursor for BLOCKED).
    """
    check_startup_config(config)
    check_em8(config)
    decisions = [int(T) for T in decisions]
    _check_inputs(has, last_tr, valid, decisions, h, direction, folds)
    stale_s = config["STALE_S"]
    lo, hi = tuple(config["ENTRY_WINDOW"])
    sid, lastv = valid_spans(valid, window_ends)
    nstart = np.maximum.accumulate(np.where(sid >= 0, sid, -1)) + 1  # reset:starts
    counts = _zero_counts()
    fold_counts = {}
    rows = []
    tx = None          # active cursor; None = minus infinity
    tx_from = None     # decision instant that set the active cursor
    for i, T in enumerate(decisions):
        v_T = bool(valid[T])
        # F.5 (b): RESET at the first second of every valid span started since the cursor was set.
        if tx is not None and nstart[T] > nstart[tx_from]:  # reset:test
            tx = None
        fc = None
        if folds is not None:
            fc = fold_counts.setdefault(folds[i], _zero_counts())
        # (1) CURSOR (F.1): BLOCKED gets no other label, runs no further test, never moves t_x.
        if tx is not None and not cursor_admits(T, tx):  # step1:CURSOR
            rows.append((T, BLOCKED, NO_TE, tx))
            for c in (counts, fc):
                if c is not None:
                    _tally(c, BLOCKED, False, False)
            continue
        prior = tx
        fresh = is_fresh(last_tr, valid, T, stale_s)  # step3:fresh
        # (2) EMBARGO: F.5 (a) valid[T] false first (EM2 is not evaluated, EM1); then EM2.
        if (not v_T) or (T + E1_LAST + h - 1 > int(lastv[T])):  # step2:EMBARGO
            tx, tx_from = next_cursor(T, NO_TE, h), T  # cursor:EMBARGO
            rows.append((T, EMBARGO, NO_TE, tx))
            for c in (counts, fc):
                if c is not None:
                    _tally(c, EMBARGO, not fresh, not v_T)
            continue
        # (3) FRESHNESS (F.6): unfresh -> UNFRESH label, NO_FILL treatment (E3).
        if not fresh:  # step3:UNFRESH
            tx, tx_from = next_cursor(T, NO_TE, h), T  # cursor:UNFRESH
            rows.append((T, UNFRESH, NO_TE, tx))
            for c in (counts, fc):
                if c is not None:
                    _tally(c, UNFRESH, True, False)
            continue
        # (4) FILL SEARCH (E1): T+1 .. T+5.
        te = first_fill(has, T, lo, hi)  # step4:FILL
        if te == NO_TE:
            tx, tx_from = next_cursor(T, NO_TE, h), T  # cursor:NO_FILL
            rows.append((T, NO_FILL, NO_TE, tx))
            for c in (counts, fc):
                if c is not None:
                    _tally(c, NO_FILL, False, False)
            continue
        if prior is not None and not c2_ok(te, prior):
            _stop("C-2: entry second %d not later than prior exit row %d" % (te, prior - 1))
        tx, tx_from = next_cursor(T, te, h), T  # cursor:FILLED
        rows.append((T, FILLED, te, tx))
        for c in (counts, fc):
            if c is not None:
                _tally(c, FILLED, False, False)
    return {"cell": {"h": h, "direction": direction}, "rows": rows, "counts": counts,
            "fold_counts": fold_counts}


# ---------------------------------------------------------------- the production entry
def decide(has, last_tr, valid, decisions, h, window_ends=(), direction=1, folds=None):
    """PRODUCTION entry: pins-only config, T % 60 == 0 (STOP 78), EM6 alignment, then the core."""
    check_pins(_pins)
    config = pins_config(_pins)
    check_startup_config(config)
    grid = _pins.DECISION_GRID_S
    for T in decisions:
        if int(T) % grid != 0:  # prod:grid
            _stop("GRID: production decision instant %r is not a multiple of %d s" % (T, grid))
    check_utc_alignment(valid, window_ends)
    return decide_core(has, last_tr, valid, decisions, h, config, window_ends=window_ends,
                       direction=direction, folds=folds)


def decide_guarded(*args, **kwargs):
    """decide() under fulln_closure.run_guarded: any ClosureViolation -> sys.exit(78)."""
    return run_guarded(lambda: decide(*args, **kwargs))


# ---------------------------------------------------------------- start-up (module import)
check_pins(_pins)
check_closure_sha()
