"""Pure builders of injected synthetic arrays for test_fulln_decide.py (batch B-R63D).

Binding: card B-R63D r2.2 24430bc38561 s4.1 (CODER-PROPOSED name) and s6 (GC-3 form, e38a L77:
"must be built by injecting the valid / last_tr arrays directly"). No data files, no binary
blobs, no I/O. Every literal here is written independently of fulln_decide_pins.py so the tests
do not derive their expectations from the module under test.
"""
import numpy as np

DAY = 86_400
# A9 r10 L115 E4 / card s1.1: exactly these 12 horizons, in this order.
H12 = (1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 14400)
# A9 r10 L160 C-3: minimum decision-minute gap G(h).
G_TABLE = {1: 1, 5: 1, 15: 1, 30: 1, 60: 2, 120: 3, 300: 6, 600: 11, 900: 16,
           1800: 31, 3600: 61, 14400: 241}
DIRECTIONS = (1, -1)
MISSING = object()


def last_traded(has):
    """last_tr[s] = last traded second <= s, else -1 (derived; GC-3 rows inject instead)."""
    has = np.asarray(has, dtype=bool)
    idx = np.arange(has.size, dtype=np.int64)
    return np.maximum.accumulate(np.where(has, idx, -1)).astype(np.int64)


def arrays(S, trades=(), invalid=(), valid_until=None):
    """has / last_tr / valid of length S.

    trades: seconds with trades > 0. invalid: half-open (a, b) ranges of non-PASS seconds.
    valid_until: if given, every second > valid_until is invalid (a span ending mid-day).
    """
    has = np.zeros(S, dtype=bool)
    tr = [int(t) for t in trades]
    if tr:
        has[np.asarray(tr, dtype=np.int64)] = True
    valid = np.ones(S, dtype=bool)
    for a, b in invalid:
        valid[int(a):int(b)] = False
    if valid_until is not None:
        valid[int(valid_until) + 1:] = False
    return has, last_traded(has), valid


def config(**over):
    """Decision-layer config built from the card's literals (s4.1); MISSING deletes a key."""
    c = {"EXIT_CLOCK": "X1", "STALE_S": 600, "ENTRY_WINDOW": (1, 5)}
    for k, v in over.items():
        if v is MISSING:
            c.pop(k, None)
        else:
            c[k] = v
    return c


def span_len_for(h):
    """Seconds needed after T for the latest exit row of any decision at T (T + 5 + h - 1)."""
    return h + 5


def three_days_fail_middle():
    """Days 0 and 2 PASS, day 1 FAIL (R63-F.5 (a); F-F5-SPANEDGE)."""
    return 3 * DAY, ((DAY, 2 * DAY),)
