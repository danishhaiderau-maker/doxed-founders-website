"""CPU item 2: TradesTail._refresh must match a full tail rebuild while reading only new bytes when inode and size are unchanged."""
from __future__ import annotations

import json
import math
import os
import threading

import pytest

import quote_trigger as qt

T = 1_790_000_000.0


class _FullRebuildTradesTail:
    """Reference: clear and re-read the last tail_bytes on every refresh (correct but expensive)."""

    def __init__(self, path: str, keep_s: int = 600, tail_bytes: int = 1024 * 1024) -> None:
        self.path, self.keep_s, self.tail_bytes = path, keep_s, tail_bytes
        self._lock = threading.Lock()
        self._by_s: dict = {}
        self._max_s = None

    def _refresh(self) -> None:
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                fh.seek(max(0, size - self.tail_bytes))
                chunk = fh.read()
        except OSError:
            return
        self._by_s = {}
        lines = chunk.splitlines()
        for line in (lines[1:] if size > self.tail_bytes else lines):
            try:
                r = json.loads(line)
                sec = int(r["s"])
            except (ValueError, KeyError, TypeError):
                continue
            prints = []
            for tr in r.get("tr") or []:
                try:
                    ms, amt, px = float(tr[1]), float(tr[2]), float(tr[3])
                except (TypeError, ValueError, IndexError):
                    continue
                if px > 0 and math.isfinite(px) and math.isfinite(ms):
                    prints.append({"ts": ms / 1000.0, "px": px, "amt": amt, "id": tr[0], "recv_s": sec})
            self._by_s[sec] = prints
        if self._by_s:
            hi = max(self._by_s)
            for sec in [s for s in self._by_s if s < hi - self.keep_s]:
                del self._by_s[sec]
            self._max_s = hi
        else:
            self._max_s = None

    def between(self, t0: float, t1: float) -> list:
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t1 - 0.5:
                self._refresh()
            out = [p for s in range(int(math.floor(t0)) - 1, int(math.floor(t1)) + 1)
                   for p in self._by_s.get(s, ()) if t0 < p["ts"] <= t1 and p["recv_s"] + 1 <= t1]
        out.sort(key=lambda p: p["ts"])
        return out

    def received_after(self, t0: float, t1: float, recv_after: float) -> list:
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t1 - 0.5:
                self._refresh()
            out = [p for s, ps in self._by_s.items() if s > recv_after for p in ps if t0 < p["ts"] <= t1]
        out.sort(key=lambda p: p["ts"])
        return out

    def coverage(self):
        with self._lock:
            if self._max_s is None:
                self._refresh()
            if not self._by_s:
                return None
            return {"lo_s": min(self._by_s), "hi_s": max(self._by_s), "n_s": len(self._by_s)}


def _row(sec: int, tr_id: int, ts: float, px: float, amt: float = 0.1) -> str:
    return json.dumps({"s": sec, "tr": [[tr_id, ts * 1000, amt, px]]}) + "\n"


def _sync_pair(path: str, inc: qt.TradesTail, ref: _FullRebuildTradesTail, t1: float) -> None:
    inc.between(T, t1)
    ref.between(T, t1)


def _assert_pair_equal(inc: qt.TradesTail, ref: _FullRebuildTradesTail, t0: float, t1: float, recv_after: float | None = None):
    assert inc.between(t0, t1) == ref.between(t0, t1)
    if recv_after is not None:
        assert inc.received_after(t0, t1, recv_after) == ref.received_after(t0, t1, recv_after)
    assert inc.coverage() == ref.coverage()


def test_equivalence_initial_load_and_two_appends(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text(_row(int(T), 1, T + 0.2, 100.0))
    inc, ref = qt.TradesTail(str(p), tail_bytes=4096), _FullRebuildTradesTail(str(p), tail_bytes=4096)
    _sync_pair(str(p), inc, ref, T + 5)
    _assert_pair_equal(inc, ref, T, T + 2)
    with open(p, "a") as fh:
        fh.write(_row(int(T) + 1, 2, T + 1.1, 100.5))
    _sync_pair(str(p), inc, ref, T + 5)
    _assert_pair_equal(inc, ref, T, T + 3, recv_after=T)
    with open(p, "a") as fh:
        fh.write(_row(int(T) + 2, 3, T + 2.1, 101.0))
    _sync_pair(str(p), inc, ref, T + 5)
    _assert_pair_equal(inc, ref, T, T + 4, recv_after=T + 0.5)


def test_equivalence_two_partial_writes(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text(_row(int(T), 1, T + 0.2, 100.0))
    inc, ref = qt.TradesTail(str(p), tail_bytes=4096), _FullRebuildTradesTail(str(p), tail_bytes=4096)
    inc.between(T, T + 5)
    ref.between(T, T + 5)
    line = _row(int(T) + 1, 2, T + 1.1, 200.0)
    with open(p, "a") as fh:
        fh.write(line[:20])
    inc.between(T, T + 5)
    ref.between(T, T + 5)
    _assert_pair_equal(inc, ref, T, T + 3)
    with open(p, "a") as fh:
        fh.write(line[20:])
    inc.between(T, T + 5)
    ref.between(T, T + 5)
    _assert_pair_equal(inc, ref, T, T + 3)
    assert [g["px"] for g in inc.between(T, T + 3)] == [100.0, 200.0]


def test_equivalence_truncation(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text("".join(_row(int(T) + i, i, T + i + 0.1, 100.0 + i) for i in range(6)))
    inc, ref = qt.TradesTail(str(p), tail_bytes=4096), _FullRebuildTradesTail(str(p), tail_bytes=4096)
    inc.between(T, T + 20)
    ref.between(T, T + 20)
    with open(p, "w") as fh:
        fh.write(_row(int(T) + 4, 9, T + 4.1, 9.0))
    inc.between(T, T + 20)
    ref.between(T, T + 20)
    _assert_pair_equal(inc, ref, T, T + 10)
    assert [g["px"] for g in inc.between(T, T + 10)] == [9.0]


def test_equivalence_rotation(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text("".join(_row(int(T) + i, i, T + i + 0.1, 100.0 + i) for i in range(8)))
    inc, ref = qt.TradesTail(str(p), tail_bytes=4096), _FullRebuildTradesTail(str(p), tail_bytes=4096)
    inc.between(T, T + 20)
    ref.between(T, T + 20)
    q = tmp_path / "rot.jsonl"
    q.write_text(_row(int(T) + 50, 1, T + 50.1, 7.0))
    os.replace(q, p)
    inc.between(T, T + 60)
    ref.between(T, T + 60)
    _assert_pair_equal(inc, ref, T, T + 60)
    assert [g["px"] for g in inc.between(T, T + 60)] == [7.0]


def test_no_reread_when_inode_and_size_unchanged(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text("".join(_row(int(T) + i, i, T + i + 0.1, 100.0) for i in range(10)))
    tail = qt.TradesTail(str(p), tail_bytes=4096)
    tail.between(T, T + 20)
    n = tail.refresh_count
    for _ in range(50):
        tail.between(T, T + 20)
    assert tail.refresh_count == n


# --- mutation proofs: each sabotage must break the paired equivalence or behavioral assert ---


class _FrozenAfterFirstSync(qt.TradesTail):
    """Ignores inode/size changes after the first successful sync."""

    def _refresh(self) -> None:
        if self._synced_ident is not None:
            return
        super()._refresh()


class _NoPartialHold(qt.TradesTail):
    def _refresh(self) -> None:
        self._partial = b""
        super()._refresh()


class _NoShrinkReset(qt.TradesTail):
    def _refresh(self) -> None:
        st = os.stat(self.path)
        if self._offset > st.st_size:
            self._offset = st.st_size
        super()._refresh()


class _NoInodeReset(qt.TradesTail):
    """Simulates missing inode reset by merging pre-refresh seconds back in."""

    def _refresh(self) -> None:
        stale = dict(self._by_s)
        super()._refresh()
        if stale:
            for sec, prints in stale.items():
                self._by_s.setdefault(sec, prints)


def _expect_assert_fails(fn):
    with pytest.raises(AssertionError):
        fn()


def test_mutation_proof_two_appends(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text(_row(int(T), 1, T + 0.2, 100.0))
    inc, ref = _FrozenAfterFirstSync(str(p), tail_bytes=4096), _FullRebuildTradesTail(str(p), tail_bytes=4096)
    inc.between(T, T + 5)
    ref.between(T, T + 5)
    with open(p, "a") as fh:
        fh.write(_row(int(T) + 1, 2, T + 1.1, 100.5))
    _expect_assert_fails(lambda: _assert_pair_equal(inc, ref, T, T + 3))


def test_mutation_proof_two_partial_writes(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text(_row(int(T), 1, T + 0.2, 100.0))
    inc = _NoPartialHold(str(p), tail_bytes=4096)
    ref = _FullRebuildTradesTail(str(p), tail_bytes=4096)
    line = _row(int(T) + 1, 2, T + 1.1, 200.0)
    with open(p, "a") as fh:
        fh.write(line[:20])
    inc.between(T, T + 5)
    with open(p, "a") as fh:
        fh.write(line[20:])
    inc.between(T, T + 5)
    ref.between(T, T + 5)
    _expect_assert_fails(lambda: _assert_pair_equal(inc, ref, T, T + 3))


def test_mutation_proof_truncation(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text("".join(_row(int(T) + i, i, T + i + 0.1, 100.0 + i) for i in range(6)))
    inc = _NoShrinkReset(str(p), tail_bytes=4096)
    ref = _FullRebuildTradesTail(str(p), tail_bytes=4096)
    inc.between(T, T + 20)
    ref.between(T, T + 20)
    with open(p, "w") as fh:
        fh.write(_row(int(T) + 4, 9, T + 4.1, 9.0))
    inc.between(T, T + 20)
    ref.between(T, T + 20)
    _expect_assert_fails(lambda: _assert_pair_equal(inc, ref, T, T + 10))


def test_mutation_proof_rotation(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text("".join(_row(int(T) + i, i, T + i + 0.1, 100.0 + i) for i in range(8)))
    inc = _NoInodeReset(str(p), tail_bytes=4096)
    ref = _FullRebuildTradesTail(str(p), tail_bytes=4096)
    inc.between(T, T + 20)
    q = tmp_path / "rot.jsonl"
    q.write_text(_row(int(T) + 50, 1, T + 50.1, 7.0))
    os.replace(q, p)
    inc.between(T, T + 60)
    ref.between(T, T + 60)
    _expect_assert_fails(lambda: _assert_pair_equal(inc, ref, T, T + 60))


class _AlwaysRescan(qt.TradesTail):
    def _refresh(self) -> None:
        self._synced_ident = None
        super()._refresh()


def test_mutation_proof_no_reread(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_text(_row(int(T), 1, T + 0.2, 100.0))
    tail = _AlwaysRescan(str(p), tail_bytes=4096)
    tail.between(T, T + 5)
    n = tail.refresh_count
    tail.between(T, T + 5)

    def _check():
        assert tail.refresh_count == n

    _expect_assert_fails(_check)
