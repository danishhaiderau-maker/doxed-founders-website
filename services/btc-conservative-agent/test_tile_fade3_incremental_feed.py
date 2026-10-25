"""R8/R0: the incremental LiveFeatureFeed must be bit-identical to the former full rebuild.

Fixture: a recorded post-cutoff (s >= 1792263671) window of the three gf3 collector tails, pulled read-only from the
Fly runtime (doxed-btc-bot, 2026-10-25): last ~5 h 6 m of bfx_book_10l_1s, the trades and deriv rows in that span.
The replay grows the files second by second exactly as the collectors append, then compares every feature array
(tobytes) against compute_features(full_rebuild_inputs(...)) and the returned tuple against the pre-R8 algorithm."""
import gzip
import json
import os
from pathlib import Path

import numpy as np
import pytest

import tile_fade3 as tf

FX = Path(__file__).resolve().parent / "fixtures" / "gf3_feed_post_cutoff"
CUTOFF_TS = 1792263671


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
    """0 iff the incremental result equals a full rebuild, feature array for feature array, bit for bit."""
    out = feed(now)
    inp = tf.full_rebuild_inputs(feed, now)
    if inp is None:
        return int(out != (None, None, {}))
    F = tf.compute_features(*inp)
    bad = sum(F[k].tobytes() != feed.last_features[k].tobytes() for k in F)
    return bad + int(json.dumps(out) != json.dumps(_reference_tuple(F)))


def _run(tmp_path, feed=None, ticks=40, extra=None):
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    t0 = end - ticks - 2
    rp = _Replay(str(tmp_path), files, t0)
    feed = feed or tf.LiveFeatureFeed(str(tmp_path))
    bad = _mismatches(feed, t0 + 1.5)
    for k in range(ticks):
        t = t0 + 1 + k
        rp.grow(t)
        if extra:
            extra(rp, t, k)
        bad += _mismatches(feed, t + 1.5) + _mismatches(feed, t + 1.9)  # 2nd call same second: cache path
    return feed, bad


def test_fixture_is_post_cutoff_and_has_a_warm_window():
    files = _load()
    for f, (_, secs) in files.items():
        assert secs and min(secs) >= CUTOFF_TS, f
    book = files[tf.PRICE_SOURCES[0]][1]
    assert max(book) - min(book) >= 5 * 3600  # z3 needs >= 0.8 * 6 h of minutes; finite at the window end


def test_incremental_features_bit_identical_to_full_rebuild(tmp_path):
    feed, bad = _run(tmp_path)
    assert bad == 0
    assert np.isfinite(feed.last_features["z3"]).sum() > 0  # warm window: the z path is actually exercised
    assert feed.recomputes <= 41 + 1  # once per new second, never on the same-second cache path
    assert feed.full_rebuilds == 3  # initial ingest only (book, trades, deriv)


def test_rebuild_paths_overwrite_out_of_order_and_rotation_stay_identical(tmp_path):
    files = _load()
    book_lines = files[tf.PRICE_SOURCES[0]][0]

    def extra(rp, t, k):
        if k == 5:  # an overwritten (re-sent) earlier book second
            row = json.loads(book_lines[-200])
            row["b"][0][0] -= 1.0
            rp.append(tf.PRICE_SOURCES[0], row)
        if k == 10:  # a late trade print older than every buffered print
            rp.append(tf.PRICE_SOURCES[1], {"s": t - 3000, "tr": [[1, (t - 3000) * 1000, 0.5, 80000.0]]})
        if k == 15:  # an in-order new print
            rp.append(tf.PRICE_SOURCES[1], {"s": t, "tr": [[2 ** 40, t * 1000 + 999, -0.25, 80001.0]]})
        if k == 20:  # rotation of the deriv tail (truncate + rewrite last rows)
            lines = files[tf.PRICE_SOURCES[2]][0]
            with open(os.path.join(rp.root, tf.PRICE_SOURCES[2]), "wb") as fh:
                fh.writelines(lines[-50:])
    feed, bad = _run(tmp_path, ticks=30, extra=extra)
    assert bad == 0
    assert feed.full_rebuilds >= 3 + 3


def test_mutation_proof_equivalence_check_catches_a_skipped_resort(tmp_path, monkeypatch):
    """Mutant 1: append out-of-order prints without re-sorting -> the equivalence check must fail."""
    orig = tf._GrowBuf.set_all
    feed = tf.LiveFeatureFeed(str(tmp_path))
    calls = {"n": 0}

    def no_resort(self, data):
        calls["n"] += 1
        if self is feed._tbuf and calls["n"] > 3:  # after initial ingest: append instead of the re-sort
            return tf._GrowBuf.append(self, {"key": [0], "T": data["T"][:1]})
        return orig(self, data)
    monkeypatch.setattr(tf._GrowBuf, "set_all", no_resort)

    def extra(rp, t, k):
        if k == 3:
            rp.append(tf.PRICE_SOURCES[1], {"s": t - 3000, "tr": [[1, (t - 3000) * 1000, 0.5, 80000.0]]})
    _, bad = _run(tmp_path, feed=feed, ticks=8, extra=extra)
    assert bad > 0


def test_mutation_proof_equivalence_check_catches_a_stale_cache(tmp_path):
    """Mutant 2: a cache key that ignores new trade prints -> the equivalence check must fail."""
    class StaleFeed(tf.LiveFeatureFeed):
        def __call__(self, now):
            out = super().__call__(now)
            if self._cache_key is not None:  # freeze: pretend nothing changed after the first compute
                self._frozen = getattr(self, "_frozen", (self._cache_val, self.last_features))
                self._cache_val, self.last_features = self._frozen
                return self._frozen[0]
            return out
    _, bad = _run(tmp_path, feed=StaleFeed(str(tmp_path)), ticks=4)
    assert bad > 0


def test_step_cpu_budget_lowered_and_incremental_tick_well_under_it(tmp_path):
    import time
    assert tf.STEP_CPU_BUDGET_S == pytest.approx(0.1)
    files = _load()
    end = max(files[tf.PRICE_SOURCES[0]][1])
    rp = _Replay(str(tmp_path), files, end - 30)
    feed = tf.LiveFeatureFeed(str(tmp_path))
    feed(end - 30 + 1.5)  # one-off ingest
    worst = 0.0
    for k in range(20):
        rp.grow(end - 29 + k)
        c = time.process_time()
        feed(end - 29 + k + 1.5)
        worst = max(worst, time.process_time() - c)
    assert worst < tf.STEP_CPU_BUDGET_S, worst
