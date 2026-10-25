"""R8/R0: incremental LiveFeatureFeed must be bit-identical to the former full rebuild.

Fixture: post-cutoff (s >= 1792263671) gf3 collector tails from Fly (2026-10-25). Deriv is backfilled to a
continuous >= 6 h span ending at the book window so BZ/minute_z paths are exercised on real replay data."""
import gzip
import json
import os
from pathlib import Path

import numpy as np
import pytest

import tile_fade3 as tf
from fade3_feed_test_helpers import full_rebuild_inputs

FX = Path(__file__).resolve().parent / "fixtures" / "gf3_feed_post_cutoff"
CUTOFF_TS = 1792263671
DERIV_MIN_SPAN_S = 6 * 3600


def _load():
    files = {}
    for f in tf.PRICE_SOURCES:
        with gzip.open(FX / (f + ".gz"), "rb") as fh:
            lines = [ln for ln in fh if ln.strip()]
        files[f] = (lines, [json.loads(ln)["s"] for ln in lines])
    return files


class _Replay:
    def __init__(self, root, files, t0):
        self.root, self.files, self.ptr = root, files, {f: 0 for f in files}
        self.grow(t0)

    def grow(self, t):
        for f, (lines, secs) in self.files.items():
            i = self.ptr[f]
            with open(os.path.join(self.root, f), "ab") as fh:
                while i < len(lines) and secs[i] <= t:
                    fh.write(lines[i])
                    i += 1
            self.ptr[f] = i

    def append(self, f, row):
        with open(os.path.join(self.root, f), "ab") as fh:
            fh.write((json.dumps(row) + "\n").encode())


def _reference_tuple(F):
    L, Sh = tf.entry_signals_e8(F)
    side = "LONG" if L[-1] else "SHORT" if Sh[-1] else None
    snap = {k: (float(F[k][-1]) if np.isfinite(F[k][-1]) else None)
            for k in ("mid", "spread", "imb5", "z3", "mom5", "ATR30", "BZ")}
    return float(F["S"][-1] + 1), side, snap


def _mismatches(feed, now):
    """0 iff incremental == full rebuild, feature-by-feature (tobytes) and returned tuple."""
    out = feed(now)
    inp = full_rebuild_inputs(feed, now)
    if inp is None:
        return int(out != (None, None, {}))
    F = tf.compute_features(*inp)
    bad = sum(F[k].tobytes() != feed.last_features[k].tobytes() for k in F)
    return bad + int(json.dumps(out) != json.dumps(_reference_tuple(F)))


def _run(tmp_path, feed=None, ticks=40, extra=None, t0=None, ticks_end=None):
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    if t0 is None:
        t0 = end - ticks - 2
    if ticks_end is None:
        ticks_end = ticks
    rp = _Replay(str(tmp_path), files, t0)
    feed = feed or tf.LiveFeatureFeed(str(tmp_path))
    bad = _mismatches(feed, t0 + 1.5)
    for k in range(ticks_end):
        t = t0 + 1 + k
        rp.grow(t)
        if extra:
            extra(rp, t, k)
        bad += _mismatches(feed, t + 1.5) + _mismatches(feed, t + 1.9)
    return feed, bad


def _full_replay(tmp_path, tail_s: int = 1200):
    """Second-by-second tail replay (post warm preload) with per-tick tobytes equivalence."""
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    t0 = end - tail_s
    rp = _Replay(str(tmp_path), files, t0)
    feed = tf.LiveFeatureFeed(str(tmp_path))
    bad, saw_finite_bz = _mismatches(feed, t0 + 1.5), False
    for t in range(t0 + 1, end + 1):
        rp.grow(t)
        bad += _mismatches(feed, t + 1.5)
        F = feed.last_features
        if F is not None and np.isfinite(F["BZ"]).any():
            saw_finite_bz = True
    return feed, bad, saw_finite_bz


def test_fixture_is_post_cutoff_and_deriv_covers_six_hours():
    files = _load()
    for f, (_, secs) in files.items():
        assert secs and min(secs) >= CUTOFF_TS, f
    book = files[tf.PRICE_SOURCES[0]][1]
    deriv = files[tf.PRICE_SOURCES[2]][1]
    assert max(book) - min(book) >= 5 * 3600
    assert max(deriv) - min(deriv) >= DERIV_MIN_SPAN_S


def test_incremental_features_bit_identical_to_full_rebuild(tmp_path):
    feed, bad = _run(tmp_path)
    assert bad == 0
    assert np.isfinite(feed.last_features["z3"]).sum() > 0
    assert np.isfinite(feed.last_features["BZ"]).sum() > 0
    assert feed.recomputes <= 41 + 1
    assert feed.full_rebuilds == 3


def test_full_replay_bit_identical_and_hits_finite_bz(tmp_path):
    feed, bad, saw_bz = _full_replay(tmp_path)
    assert bad == 0
    assert saw_bz
    assert np.isfinite(feed.last_features["BZ"]).sum() > 0


def test_rebuild_paths_overwrite_out_of_order_and_rotation_stay_identical(tmp_path):
    files = _load()
    book_lines = files[tf.PRICE_SOURCES[0]][0]

    def extra(rp, t, k):
        if k == 5:
            row = json.loads(book_lines[-200])
            row["b"][0][0] -= 1.0
            rp.append(tf.PRICE_SOURCES[0], row)
        if k == 10:
            rp.append(tf.PRICE_SOURCES[1], {"s": t - 3000, "tr": [[1, (t - 3000) * 1000, 0.5, 80000.0]]})
        if k == 15:
            rp.append(tf.PRICE_SOURCES[1], {"s": t, "tr": [[2 ** 40, t * 1000 + 999, -0.25, 80001.0]]})
        if k == 20:
            lines = files[tf.PRICE_SOURCES[2]][0]
            with open(os.path.join(rp.root, tf.PRICE_SOURCES[2]), "wb") as fh:
                fh.writelines(lines[-50:])
    feed, bad = _run(tmp_path, ticks=30, extra=extra)
    assert bad == 0
    assert feed.full_rebuilds >= 3 + 3


def test_mutation_proof_equivalence_check_catches_a_skipped_resort(tmp_path, monkeypatch):
    orig = tf._GrowBuf.set_all
    feed = tf.LiveFeatureFeed(str(tmp_path))
    calls = {"n": 0}

    def no_resort(self, data):
        calls["n"] += 1
        if self is feed._tbuf and calls["n"] > 3:
            return tf._GrowBuf.append(self, {"key": [0], "T": data["T"][:1]})
        return orig(self, data)
    monkeypatch.setattr(tf._GrowBuf, "set_all", no_resort)

    def extra(rp, t, k):
        if k == 3:
            rp.append(tf.PRICE_SOURCES[1], {"s": t - 3000, "tr": [[1, (t - 3000) * 1000, 0.5, 80000.0]]})
    _, bad = _run(tmp_path, feed=feed, ticks=8, extra=extra)
    assert bad > 0


def test_mutation_proof_equivalence_check_catches_a_stale_cache(tmp_path):
    class StaleFeed(tf.LiveFeatureFeed):
        def __call__(self, now):
            out = super().__call__(now)
            if self._cache_key is not None:
                self._frozen = getattr(self, "_frozen", (self._cache_val, self.last_features))
                self._cache_val, self.last_features = self._frozen
                return self._frozen[0]
            return out
    _, bad = _run(tmp_path, feed=StaleFeed(str(tmp_path)), ticks=4)
    assert bad > 0


class _SkipBookEviction(tf.LiveFeatureFeed):
    def _sync_book(self) -> None:
        t, buf = self.book, self._bbuf
        if self._needs_rebuild("book", t, buf):
            self.full_rebuilds += 1
            buf.set_all(self._book_cols(sorted(t.rows)))
            self._seen["book"] = t.resets
        elif t.new_secs:
            buf.append(self._book_cols(sorted(set(t.new_secs))))


def _synthetic_hist_root(tmp_path, n: int, s0: int = 1_761_000_000):
    from test_tile_fade3 import _feed_files
    return _feed_files(tmp_path, n, s0=s0)


def test_mutation_proof_eviction_above_floor(tmp_path):
    n = int(tf.HISTORY_S) + 80
    s0 = _synthetic_hist_root(tmp_path, n)
    end = s0 + n - 1
    feed = _SkipBookEviction(str(tmp_path))
    now = end + 1.5
    assert _mismatches(feed, now) == 0
    row = {"s": end + 1, "b": [[100000, 1.0]] * 5, "a": [[100002, 1.0]] * 5, "receive_ts": float(end + 1) + 0.5}
    with open(os.path.join(tmp_path, tf.PRICE_SOURCES[0]), "a") as fh:
        fh.write(json.dumps(row) + "\n")
    feed.book.sync()
    assert _mismatches(feed, end + 2.5) > 0


class _FreezeTradesGenInKey(tf.LiveFeatureFeed):
    _frozen = None

    def __call__(self, now):
        if self._frozen is None:
            out = super().__call__(now)
            self._frozen = self.trades.gen
            return out
        real = self.trades.gen
        self.trades.gen = self._frozen
        try:
            return super().__call__(now)
        finally:
            self.trades.gen = real


class _FreezeDerivGenInKey(tf.LiveFeatureFeed):
    _frozen = None

    def __call__(self, now):
        if self._frozen is None:
            out = super().__call__(now)
            self._frozen = self.deriv.gen
            return out
        real = self.deriv.gen
        self.deriv.gen = self._frozen
        try:
            return super().__call__(now)
        finally:
            self.deriv.gen = real


def test_mutation_proof_cache_key_ignores_trades_gen(tmp_path):
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    rp = _Replay(str(tmp_path), files, end)
    feed = _FreezeTradesGenInKey(str(tmp_path))
    now = end + 1.5
    assert _mismatches(feed, now) == 0
    rp.append(tf.PRICE_SOURCES[1], {"s": end, "tr": [[99, end * 1000 + 99, 0.5, 80000.0]]})
    feed.trades.sync()
    assert _mismatches(feed, now) > 0


def test_mutation_proof_cache_key_ignores_deriv_gen(tmp_path):
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    rp = _Replay(str(tmp_path), files, end)
    feed = _FreezeDerivGenInKey(str(tmp_path))
    now = end + 1.5
    assert _mismatches(feed, now) == 0
    rp.append(tf.PRICE_SOURCES[2], {"s": end, "deriv": 99999.0, "spot": 80000.0, "mark": 80001.0})
    feed.deriv.sync()
    assert _mismatches(feed, now) > 0


class _DerivSearchsortedLeft(tf.LiveFeatureFeed):
    def __call__(self, now):
        for tail in (self.book, self.trades, self.deriv):
            tail.sync()
        self._sync_book()
        self._sync_trades()
        self._sync_deriv()
        S_all = self._bbuf.view("key")
        if not len(S_all):
            return None, None, {}
        rx = self._bbuf.view("rx")
        with np.errstate(invalid="ignore"):
            elig = (S_all + 1 <= now) & (np.isnan(rx) | (rx <= now))
        idx = np.flatnonzero(elig)
        if not idx.size:
            return None, None, {}
        contiguous = idx[-1] - idx[0] + 1 == idx.size
        s_last = int(S_all[idx[-1]])
        key = (int(S_all[idx[0]]), s_last, int(idx.size), self.book.gen, self.trades.gen, self.deriv.gen)
        if key == self._cache_key:
            return self._cache_val
        sl = slice(int(idx[0]), int(idx[-1]) + 1) if contiguous else idx
        S, B, A = S_all[sl], self._bbuf.view("B")[sl], self._bbuf.view("A")[sl]
        TK, T = self._tbuf.view("key"), self._tbuf.view("T")
        if len(TK) and TK.max() > s_last:
            T = T[TK <= s_last]
        DK = self._dbuf.view("key")
        nd = int(np.searchsorted(DK, s_last, "left"))
        D = np.column_stack([DK[:nd].astype(float), self._dbuf.view("D")[:nd]]) if nd else np.empty((0, 3))
        F = tf.compute_features(S, B, A, T, D)
        L, Sh = tf.entry_signals_e8(F)
        side = "LONG" if L[-1] else "SHORT" if Sh[-1] else None
        snap = {k: (float(F[k][-1]) if np.isfinite(F[k][-1]) else None)
                for k in ("mid", "spread", "imb5", "z3", "mom5", "ATR30", "BZ")}
        self.recomputes += 1
        self.last_features = F
        self._cache_key, self._cache_val = key, (float(s_last + 1), side, snap)
        return self._cache_val


def test_mutation_proof_searchsorted_left_breaks_deriv_slice(tmp_path):
    _, bad = _run(tmp_path, feed=_DerivSearchsortedLeft(str(tmp_path)), ticks=10)
    assert bad > 0


def test_mutation_proof_duplicate_trade_seconds_not_deduped(tmp_path, monkeypatch):
    def sync_no_dedupe(self):
        t, buf = self.trades, self._tbuf
        if self._needs_rebuild("trades", t, buf):
            self.full_rebuilds += 1
            buf.set_all(self._trade_rows(list(t.rows)))
            self._seen["trades"] = t.resets
            self._tmax = max(t.rows) if t.rows else -1
        elif t.new_secs:
            new = list(t.new_secs)
            add = self._trade_rows(new)
            if len(add["T"]) and len(buf):
                last, first = buf.view("T")[-1], add["T"][0]
                if (first[1], first[0]) <= (last[1], last[0]):
                    self.full_rebuilds += 1
                    buf.set_all(self._trade_rows(list(t.rows)))
                    add = None
            if add is not None:
                buf.append(add)
            self._tmax = max(self._tmax, max(new))
        if len(buf) and t.floor is not None:
            buf.keep(buf.view("key") >= t.floor)

    monkeypatch.setattr(tf.LiveFeatureFeed, "_sync_trades", sync_no_dedupe)
    feed = tf.LiveFeatureFeed(str(tmp_path))

    def extra(rp, t, k):
        if k == 4:
            rp.append(tf.PRICE_SOURCES[1], {"s": t, "tr": [[1, t * 1000, 0.1, 80000.0]]})
            rp.append(tf.PRICE_SOURCES[1], {"s": t, "tr": [[2, t * 1000 + 1, 0.2, 80001.0]]})
    _, bad = _run(tmp_path, feed=feed, ticks=8, extra=extra)
    assert bad > 0


@pytest.mark.cpu_budget
def test_step_cpu_budget_lowered_and_incremental_tick_well_under_ci_ceiling(tmp_path):
    import time
    assert tf.STEP_CPU_BUDGET_S == pytest.approx(0.1)
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    rp = _Replay(str(tmp_path), files, end - 30)
    feed = tf.LiveFeatureFeed(str(tmp_path))
    feed(end - 30 + 1.5)
    worst = 0.0
    for k in range(20):
        rp.grow(end - 29 + k)
        c = time.process_time()
        feed(end - 29 + k + 1.5)
        worst = max(worst, time.process_time() - c)
    assert worst < tf.STEP_CPU_CI_CEILING_S, worst
