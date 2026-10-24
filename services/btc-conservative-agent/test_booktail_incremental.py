"""RK PASS 24 Oct (CPU fix, RCA 21:30 AEDT): the incremental BookTail must select EXACTLY the rows the pre-fix
BookTail selected (latest complete row with s + 1 <= t and receive_ts <= t, no ticker/mid/tape fallback), price
exits identically (px / book_s / stale) and stop-fill rows identically (first row received at/after the trigger),
while at() stays <= 1 ms p99. Also covers partial last line, rotation / shrink / inode change and same-mtime writes.
The fixture is a real tail of /app/data/runtime/bfx_book_10l_1s.jsonl recorded from Fly on 24 Oct 2026."""
from __future__ import annotations

import gzip
import json
import os
import threading
import time

import paper_fill as pf
import quote_trigger as qt

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "bfx_book_10l_1s_recorded_20261024.jsonl.gz")


class _OldBookTail:
    """VERBATIM copy of the pre-fix (master 63b462ed) BookTail selection logic: the reference implementation."""

    def __init__(self, path, keep=600, tail_bytes=512 * 1024):
        self.path, self.keep, self.tail_bytes = path, keep, tail_bytes
        self._lock = threading.Lock()
        self._rows = {}
        self._max_s = None

    def _refresh(self):
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                fh.seek(max(0, size - self.tail_bytes))
                chunk = fh.read()
        except OSError:
            return
        lines = chunk.splitlines()
        for line in (lines[1:] if size > self.tail_bytes else lines):
            try:
                r = json.loads(line)
                sec = int(r["s"])
            except (ValueError, KeyError, TypeError):
                continue
            rx = pf._f(r.get("receive_ts"))
            self._rows[sec] = {"s": sec, "b": r.get("b") or [], "a": r.get("a") or [],
                               **({"carried": True} if r.get("carried") else {}),
                               "receive_ts": rx if rx and rx > 0 else None, "receive_mono": pf._f(r.get("receive_mono")),
                               "receive_ts_source": (str(r.get("receive_ts_source") or "")
                                                     if rx and rx > 0 else pf.RECEIVE_TS_MISSING)}
        if self._rows:
            for sec in sorted(self._rows)[:-self.keep]:
                del self._rows[sec]
            self._max_s = max(self._rows)

    def at(self, t):
        with self._lock:
            if self._max_s is None or self._max_s + 1 < t - 0.5:
                self._refresh()
            ok = [sec for sec, r in self._rows.items()
                  if sec + 1 <= t and (r.get("receive_ts") is None or r["receive_ts"] <= t)]
            return dict(self._rows[max(ok)]) if ok else None

    def rows_between(self, lo_s, hi_s):
        with self._lock:
            if self._max_s is None or self._max_s < hi_s:
                self._refresh()
            return [dict(self._rows[k]) for k in sorted(self._rows) if lo_s <= k <= hi_s]


def _recorded(tmp_path):
    p = tmp_path / "book.jsonl"
    with gzip.open(FIXTURE, "rb") as fh:
        p.write_bytes(fh.read())
    return str(p)


def _book_at(tail, memo=False):
    def f(t):
        return tail.at(t)
    f.rows_between = tail.rows_between
    if memo:
        f.newest_key = tail.newest_key
        f.newest_eligible = tail.newest_eligible
    return f


def _probe_times(path):
    secs, rxs = [], []
    with open(path, "rb") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            secs.append(int(r["s"]))
            if r.get("receive_ts"):
                rxs.append(float(r["receive_ts"]))
    lo, hi = max(secs) - 700, max(secs) + 4
    ts = [s + f for s in range(lo, hi) for f in (0.0, 0.25, 0.5, 0.75, 0.999)]
    ts += [x + d for x in rxs[-700:] for d in (-1e-6, 0.0, 1e-6, 0.4)]
    return sorted(ts)


def test_fixture_is_a_real_recording():
    with gzip.open(FIXTURE, "rt") as fh:
        rows = [json.loads(x) for x in fh.read().splitlines()[1:] if x.strip()]
    assert len(rows) > 1500 and all("s" in r and "b" in r and "a" in r for r in rows)
    assert sum(1 for r in rows if r.get("receive_ts")) > 1000


def test_at_rows_px_book_s_stale_identical_on_recorded_file(tmp_path):
    path = _recorded(tmp_path)
    old, new = _OldBookTail(path), pf.BookTail(path)
    old_at, new_at, new_memo = _book_at(old), _book_at(new), _book_at(new, memo=True)
    ts = _probe_times(path)
    assert len(ts) > 5000
    for t in ts:
        assert old.at(t) == new.at(t), t
        for d in ("LONG", "SHORT"):
            a = qt.book_exit_price(d, t, old_at)
            b = qt.book_exit_price(d, t, new_at)
            c = qt.book_exit_price(d, t, new_memo)  # memo path: must be identical too
            for k in ("px", "book_s", "stale", "price_age_s", "flag", "last_book_px"):
                assert a[k] == b[k] == c[k], (t, d, k, a[k], b[k], c[k])


def test_stop_fill_rows_identical_first_received_at_or_after(tmp_path):
    path = _recorded(tmp_path)
    old, new = _OldBookTail(path), pf.BookTail(path)
    ts = _probe_times(path)
    for t in ts[::7]:
        a = pf.first_row_received_at_or_after(_book_at(old), t)
        b = pf.first_row_received_at_or_after(_book_at(new), t)
        assert a == b, t
        assert pf.first_row_at_or_after(_book_at(old), t) == pf.first_row_at_or_after(_book_at(new), t), t


def test_at_p99_under_1ms_on_recorded_file(tmp_path):
    path = _recorded(tmp_path)
    new = pf.BookTail(path)
    newest = new.newest_key()[0]
    new.at(newest + 2.0)  # first load is a one-off
    lat = []
    for i in range(5000):
        t = newest + 1.0 + (i % 50) / 25.0
        t0 = time.perf_counter()
        new.at(t)
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    assert lat[int(0.99 * len(lat)) - 1] <= 1.0, lat[int(0.99 * len(lat)) - 1]


def _row(s, rx=None, bid=100.0):
    r = {"s": s, "b": [[bid, 1.0]], "a": [[bid + 1, 1.0]]}
    if rx is not None:
        r["receive_ts"], r["receive_ts_source"] = rx, pf.RECEIVE_TS_MEASURED
    return json.dumps(r) + "\n"


def test_partial_last_line_held_until_complete(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text(_row(100, 101.1) + _row(101, 102.1))
    bt = pf.BookTail(str(p))
    assert bt.at(103.0)["s"] == 101
    line = _row(102, 103.1, bid=200.0)
    with open(p, "a") as fh:
        fh.write(line[:17])  # half a row: must not be parsed or lost
    assert bt.at(104.0)["s"] == 101
    with open(p, "a") as fh:
        fh.write(line[17:])
    r = bt.at(104.0)
    assert r["s"] == 102 and r["b"][0][0] == 200.0


def test_rotation_shrink_and_inode_change_full_reset(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text("".join(_row(s, s + 1.1) for s in range(100, 110)))
    bt = pf.BookTail(str(p))
    assert bt.at(200.0)["s"] == 109
    # rotation: new inode with OLDER seconds -> old rows must be gone (full reset)
    q = tmp_path / "b2.jsonl"
    q.write_text(_row(50, 51.1, bid=7.0))
    os.replace(q, p)
    r = bt.at(200.0)
    assert r["s"] == 50 and r["b"][0][0] == 7.0 and bt.at(50.5) is None
    # shrink in place (truncate, same inode, shorter) -> reset too
    with open(p, "w") as fh:
        fh.write(_row(4, 5.1, bid=9.0))
    assert bt.at(200.0)["s"] == 4 and bt.reset_count >= 2
    # same-size in-place rewrite with a new mtime -> reset (append-only never changes mtime without growing)
    st = os.stat(p)
    with open(p, "w") as fh:
        fh.write(_row(3, 4.1, bid=8.0))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert bt.at(200.0)["s"] == 3 and bt.reset_count >= 3


def test_same_mtime_write_not_missed(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text(_row(100, 101.1))
    st = os.stat(p)
    bt = pf.BookTail(str(p))
    assert bt.at(105.0)["s"] == 100
    with open(p, "a") as fh:
        fh.write(_row(101, 102.1, bid=300.0))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # identical mtime: size must still trigger the reload
    assert os.stat(p).st_mtime_ns == st.st_mtime_ns
    r = bt.at(105.0)
    assert r["s"] == 101 and r["b"][0][0] == 300.0


def test_no_reread_when_file_unchanged(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text("".join(_row(s, s + 1.1) for s in range(100, 110)))
    bt = pf.BookTail(str(p))
    bt.at(200.0)
    n = bt.refresh_count
    for _ in range(100):
        bt.at(200.0)
    assert bt.refresh_count == n


def test_receive_ts_rule_unchanged_no_lookahead(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text(_row(100, 101.2) + _row(101, 102.9) + _row(102))
    bt = pf.BookTail(str(p))
    assert bt.at(102.5)["s"] == 100          # row 101 complete at 102 but received 102.9 > t
    assert bt.at(103.0)["s"] == 102          # pre-#851 row (no rx) still returned, labelled MISSING
    assert bt.at(103.0)["receive_ts_source"] == pf.RECEIVE_TS_MISSING
    assert bt.at(100.9) is None


def test_memo_never_prices_a_stale_exit(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text(_row(100, 100.5))
    bt = pf.BookTail(str(p))
    f = _book_at(bt, memo=True)
    a = qt.book_exit_price("LONG", 101.5, f)
    assert a["px"] == 100.0 and not a["stale"]
    hits = qt._EXIT_MEMO_STATS["hit"]
    b = qt.book_exit_price("LONG", 104.0, f)  # same newest row, now 3 s old -> memo hit but STALE, px None
    assert qt._EXIT_MEMO_STATS["hit"] == hits + 1
    assert b["stale"] and b["px"] is None and b["flag"] == qt.STALE_BOOK
