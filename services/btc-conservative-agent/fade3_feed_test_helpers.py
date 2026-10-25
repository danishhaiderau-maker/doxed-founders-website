"""Test-only helpers for gf3 LiveFeatureFeed equivalence (never imported on the tick path)."""
from __future__ import annotations

import numpy as np

import tile_fade3 as tf


def full_rebuild_inputs(feed: tf.LiveFeatureFeed, now: float):
    """Reference (pre-R8) full rebuild of compute_features inputs from synced tails."""
    secs = sorted(k for k, v in feed.book.rows.items() if k + 1 <= now and (v[2] is None or v[2] <= now))
    if not secs:
        return None
    s_last = secs[-1]
    S = np.asarray(secs, dtype=np.int64)
    B = np.asarray([feed.book.rows[k][0] for k in secs], dtype=float)
    A = np.asarray([feed.book.rows[k][1] for k in secs], dtype=float)
    T = sorted((t for k, prints in feed.trades.rows.items() if k + 1 <= s_last + 1 for t in prints),
               key=lambda t: (t[1], t[0]))
    D = [[k, *v] for k, v in sorted(feed.deriv.rows.items()) if k + 1 <= s_last + 1]
    return S, B, A, np.asarray(T, dtype=float).reshape(-1, 4), np.asarray(D, dtype=float).reshape(-1, 3)
