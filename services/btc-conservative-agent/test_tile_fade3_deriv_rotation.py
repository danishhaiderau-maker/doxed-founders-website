"""RK 25 Oct (BZ R1): a deriv read spanning rotations equals the read of the same continuous data."""
import gzip
import json
import os
from pathlib import Path

import numpy as np
import pytest

import tile_fade3 as tf

FX = Path(__file__).resolve().parent / "fixtures" / "gf3_feed_post_cutoff"

T0 = tf.DERIV_FLOOR_TS + 10_000
N = 7 * 3600  # 7 h of 1 s deriv rows: more than the 6 h 15 m history


def _row(s):
    s = int(s)
    return json.dumps({"s": s, "deriv": float(80000 + 30 * np.sin(s / 977.0) + (s % 7)), "spot": 80000.0}) + "\n"


def _write(path, secs):
    with open(path, "w") as f:
        f.writelines(_row(s) for s in secs)


def _bz(hist, s_last):
    S = np.arange(s_last - tf.HISTORY_S, s_last + 1)
    D = np.asarray([[k, *v] for k, v in sorted(hist.rows.items()) if k <= s_last], dtype=float).reshape(-1, 3)
    j = np.searchsorted(D[:, 0], S, "right") - 1
    basis = np.where(j >= 0, (D[np.maximum(j, 0), 1] / D[np.maximum(j, 0), 2] - 1) * 1e4, np.nan)
    B = np.zeros((len(S), 10, 2)); B[:, :, 0] = 79999; B[:, :, 1] = 1
    A = np.zeros((len(S), 10, 2)); A[:, :, 0] = 80001; A[:, :, 1] = 1
    F = tf.compute_features(S, B, A, np.zeros((0, 4)), D)
    return F["BZ"]


def _rotated(tmp_path, secs, cuts):
    base = tmp_path / "rot" / tf.PRICE_SOURCES[2]
    base.parent.mkdir()
    parts = np.split(np.asarray(secs), cuts)
    for i, p in enumerate(parts[:-1], start=1):  # base.1 oldest ... base.k newest
        _write(f"{base}.{i}", p)
    _write(base, parts[-1])
    return str(base)


def _continuous(tmp_path, secs):
    p = tmp_path / "cont" / tf.PRICE_SOURCES[2]
    p.parent.mkdir()
    _write(p, secs)
    return str(p)


def _assert_equivalent(tmp_path, cls):
    secs = list(range(T0, T0 + N))
    cont = tf._JsonlHistory(_continuous(tmp_path, secs), tf._parse_deriv, 1 << 30)
    rot = cls(_rotated(tmp_path, secs, [N - 3 * 3600, N - 1800]), tf._parse_deriv, 16 << 20)  # live = 30 min
    cont.sync(); rot.sync()
    assert rot.rows == cont.rows
    a, b = _bz(rot, secs[-1]), _bz(cont, secs[-1])
    assert np.isfinite(b[-1]) and np.array_equal(a, b, equal_nan=True)
    assert rot.gap_end is None


def test_rotation_spanning_read_equals_continuous(tmp_path):
    _assert_equivalent(tmp_path, tf._RotatingDerivHistory)


def test_mutation_proof_no_backfill_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(tf._RotatingDerivHistory, "_backfill", lambda self: setattr(self, "gap_end", None))
    with pytest.raises(AssertionError):
        _assert_equivalent(tmp_path, tf._RotatingDerivHistory)


def test_rotation_after_start_refills(tmp_path):
    secs = list(range(T0, T0 + N))
    base = _rotated(tmp_path, secs, [N - 600])
    h = tf._RotatingDerivHistory(base, tf._parse_deriv, 16 << 20)
    h.sync()
    import os
    os.rename(base, base + ".2")  # live rotates; new empty-ish live file starts
    _write(base, range(T0 + N, T0 + N + 5))
    h.sync()
    assert min(h.rows) <= T0 + N + 4 - tf.HISTORY_S and h.gap_end is None


def test_boundary_gap_gives_nan_bz(tmp_path):
    secs = [s for s in range(T0, T0 + N) if not (T0 + N - 1800 - 120 <= s < T0 + N - 1800)]
    h = tf._RotatingDerivHistory(_rotated(tmp_path, secs, [len(secs) - 1800]), tf._parse_deriv, 16 << 20)
    h.sync()
    assert h.gap_end == T0 + N - 1800 and min(h.rows) == T0 + N - 1800  # never stitched across the hole


def test_feed_returns_nan_bz_while_gap_in_window(tmp_path, monkeypatch):
    feed = tf.LiveFeatureFeed(str(tmp_path))
    s_last = T0 + N
    monkeypatch.setattr(feed.deriv, "gap_end", s_last - 3600, raising=False)
    monkeypatch.setattr(feed.deriv, "sync", lambda: None)
    monkeypatch.setattr(feed.book, "sync", lambda: None)
    monkeypatch.setattr(feed.trades, "sync", lambda: None)
    for k in range(s_last - tf.HISTORY_S, s_last + 1):
        feed.book.rows[k] = ([[79999, 1]] * 10, [[80001, 1]] * 10, None)
        feed.deriv.rows[k] = (80000 + 30 * np.sin(k / 977.0) + (k % 7), 80000.0)
    assert feed(s_last + 1)[2]["BZ"] is None
    feed.deriv.gap_end = None
    assert feed(s_last + 1)[2]["BZ"] is not None


def test_floor_rows_never_used(tmp_path):
    lo = tf.DERIV_FLOOR_TS - 3600
    h = tf._RotatingDerivHistory(_rotated(tmp_path, list(range(lo, lo + 7200)), [3000]), tf._parse_deriv, 16 << 20)
    h.sync()
    assert min(h.rows) == tf.DERIV_FLOOR_TS


def _write_book_trades(root: Path, secs):
    book, trades = [], []
    for i, s in enumerate(secs):
        px = 100000.0 + (i % 7)
        book.append(json.dumps({"s": s, "b": [[px - 1, 1.0]] * 5, "a": [[px + 1, 1.0]] * 5}))
        trades.append(json.dumps({"s": s, "tr": [[i, s * 1000 + 1, 0.01, px]]}))
    (root / tf.PRICE_SOURCES[0]).write_text("\n".join(book) + "\n")
    (root / tf.PRICE_SOURCES[1]).write_text("\n".join(trades) + "\n")


def _layout_continuous_feed(tmp_path, secs):
    root = tmp_path / "feed"
    root.mkdir()
    _write(root / tf.PRICE_SOURCES[2], secs)
    _write_book_trades(root, secs)
    return root


def _layout_rotated_feed(tmp_path, secs, cuts):
    root = tmp_path / "rot_feed"
    root.mkdir()
    base = root / tf.PRICE_SOURCES[2]
    parts = np.split(np.asarray(secs), cuts)
    for i, p in enumerate(parts[:-1], start=1):
        _write(f"{base}.{i}", p)
    _write(base, parts[-1])
    _write_book_trades(root, secs)
    return root


def test_stored_trade_bz_pins_match_recompute_after_deriv_rotation(tmp_path):
    """Ledger ``features.BZ`` at entry was computed with full deriv history; after a 32 MiB-style rotation the feed
    must recompute the same BZ wherever the rotated files still cover the 360-min window (no gap)."""
    secs = list(range(T0, T0 + N))
    cont_root = _layout_continuous_feed(tmp_path, secs)
    cont_feed = tf.LiveFeatureFeed(str(cont_root))
    pins = []
    for t in range(T0 + N - 1800, T0 + N, 600):
        _, _, snap = cont_feed(t + 1.5)
        if snap.get("BZ") is not None:
            pins.append({"signal_ts": float(t + 1), "stored_bz": snap["BZ"]})
    assert len(pins) >= 3
    rot_root = _layout_rotated_feed(tmp_path, secs, [N - 3 * 3600, N - 1800])
    rot_feed = tf.LiveFeatureFeed(str(rot_root))
    for pin in pins:
        _, _, snap = rot_feed(pin["signal_ts"] + 0.5)
        assert snap.get("BZ") == pytest.approx(pin["stored_bz"], rel=0, abs=1e-9)


def test_fly_post_cutoff_fixture_bz_unchanged_by_deriv_rotation(tmp_path):
    """Recorded post-cutoff Fly tails: deriv rotation must not invent a finite BZ when the continuous read is NaN."""
    if not FX.is_dir():
        pytest.skip("gf3_feed_post_cutoff fixture not present")
    cont = tmp_path / "cont"
    rot = tmp_path / "rot"
    cont.mkdir(); rot.mkdir()
    deriv = tf.PRICE_SOURCES[2]
    for d in (cont, rot):
        for f in tf.PRICE_SOURCES:
            with gzip.open(FX / (f + ".gz"), "rb") as fh:
                (d / f).write_bytes(fh.read())
    lines = (cont / deriv).read_text().splitlines(True)
    cut = len(lines) // 2
    (rot / f"{deriv}.1").write_text("".join(lines[:cut]))
    (rot / deriv).write_text("".join(lines[cut:]))
    t_end = max(json.loads(ln)["s"] for ln in lines if ln.strip())
    bz_cont = tf.LiveFeatureFeed(str(cont))(t_end + 1.5)[2].get("BZ")
    bz_rot = tf.LiveFeatureFeed(str(rot))(t_end + 1.5)[2].get("BZ")
    assert bz_cont is None and bz_rot is None  # <6 h deriv span: both fail closed, never diverge
