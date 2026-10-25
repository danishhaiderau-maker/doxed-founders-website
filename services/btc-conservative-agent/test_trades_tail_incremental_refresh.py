"""TradesTail._refresh: inode/size gate + offset append (CPU); equivalence vs full rebuild; mutation proofs."""
from __future__ import annotations

import json
import os
from pathlib import Path

import quote_trigger as qt


def _row(sec: int, prints) -> str:
    # prints: list of (id, ms, amt, px)
    tr = [[i, ms, amt, px] for i, ms, amt, px in prints]
    return json.dumps({"s": sec, "tr": tr}, separators=(",", ":"))


def _write(path: Path, rows: list[str], *, append: bool = False) -> None:
    mode = "ab" if append else "wb"
    with path.open(mode) as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else r.decode()).encode("utf-8") + b"\n")


def _full_rebuild(path: Path, keep_s: int = 600, tail_bytes: int = 1024 * 1024) -> dict:
    """Reference: same window as the old always-reparse tail (last tail_bytes, drop mid-seek first line)."""
    size = path.stat().st_size
    start = max(0, size - tail_bytes)
    with path.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read()
    lines = chunk.splitlines()
    body = lines[1:] if start > 0 else lines
    by_s: dict = {}
    for line in body:
        try:
            r = json.loads(line)
            sec = int(r["s"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        prints = []
        for tr in r.get("tr") or []:
            try:
                ms, amt, px = float(tr[1]), float(tr[2]), float(tr[3])
            except (TypeError, ValueError, IndexError):
                continue
            if px > 0:
                prints.append({"ts": ms / 1000.0, "px": px, "amt": amt, "id": tr[0], "recv_s": sec})
        by_s[sec] = prints
    if by_s:
        hi = max(by_s)
        by_s = {s: p for s, p in by_s.items() if s >= hi - keep_s}
    return by_s


def _snap(tail: qt.TradesTail) -> dict:
    return {s: list(ps) for s, ps in sorted(tail._by_s.items())}


def test_unchanged_size_skips_disk_reread(tmp_path, monkeypatch):
    p = tmp_path / "bfx_trades.jsonl"
    _write(p, [_row(100, [(1, 100_200, -0.1, 99.0)]), _row(101, [(2, 101_100, 0.1, 100.0)])])
    tail = qt.TradesTail(str(p), keep_s=600)
    assert tail.between(99.0, 102.0)  # load
    opens = {"n": 0}
    real_open = open

    def counting_open(*a, **k):
        opens["n"] += 1
        return real_open(*a, **k)

    monkeypatch.setattr("builtins.open", counting_open)
    before = opens["n"]
    for _ in range(20):
        tail.between(99.0, 102.0)
        tail.coverage()
    assert opens["n"] == before  # no open() when inode+size unchanged


def test_incremental_two_partial_writes_match_full_rebuild(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_bytes(b"")
    tail = qt.TradesTail(str(p), keep_s=600)
    # partial write (no trailing newline) then complete
    with p.open("ab") as fh:
        fh.write(_row(50, [(1, 50_500, -0.1, 90.0)]).encode()[:20])  # partial
    tail._refresh()
    assert _snap(tail) == {}  # incomplete line held
    with p.open("ab") as fh:
        fh.write(_row(50, [(1, 50_500, -0.1, 90.0)]).encode()[20:] + b"\n")
    tail._refresh()
    assert _snap(tail) == _full_rebuild(p)
    # second complete row
    _write(p, [_row(51, [(2, 51_200, 0.2, 91.0)])], append=True)
    tail._refresh()
    assert _snap(tail) == _full_rebuild(p)
    assert [x["id"] for x in tail.between(49.0, 52.0)] == [1, 2]


def test_truncation_and_new_inode_full_reload(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    _write(p, [_row(10, [(1, 10_100, -0.1, 80.0)]), _row(11, [(2, 11_100, -0.1, 81.0)])])
    tail = qt.TradesTail(str(p))
    tail._refresh()
    assert set(_snap(tail)) == {10, 11}
    # truncate (shrink) -> full reload
    _write(p, [_row(20, [(9, 20_100, 0.1, 82.0)])])
    tail._refresh()
    assert _snap(tail) == _full_rebuild(p) and set(_snap(tail)) == {20}
    # rotation: replace path with a new file (new inode on POSIX; size/dev change still reloads)
    p.unlink()
    _write(p, [_row(30, [(3, 30_100, 0.1, 83.0)]), _row(31, [(4, 31_100, 0.1, 84.0)])])
    tail._refresh()
    assert _snap(tail) == _full_rebuild(p) and set(_snap(tail)) == {30, 31}


def test_equivalence_existing_coverage_contract(tmp_path):
    """Prior C3 contract still holds under incremental refresh."""
    p = tmp_path / "bfx_trades.jsonl"
    _write(p, [_row(100, [(1, 100_200, -0.1, 99.0)]), _row(101, []),
               _row(105, [(2, 101_500, -0.2, 98.0), (3, 105_100, 0.1, 99.5)])])
    tail = qt.TradesTail(str(p))
    assert [x["id"] for x in tail.between(100.0, 102.0)] == [1]
    late = tail.received_after(100.0, 106.0, 101)
    assert [x["id"] for x in late] == [2, 3] and late[0]["recv_s"] == 105
    assert tail.coverage() == {"lo_s": 100, "hi_s": 105, "n_s": 3}


def test_mutation_proof_ignoring_size_change_misses_new_prints(tmp_path, monkeypatch):
    p = tmp_path / "bfx_trades.jsonl"
    _write(p, [_row(1, [(1, 1_100, -0.1, 90.0)])])
    tail = qt.TradesTail(str(p))
    tail._refresh()
    _write(p, [_row(2, [(2, 2_100, -0.1, 91.0)])], append=True)

    def mutant_refresh(self):
        # pretend size never changed -> skip read (breaks equivalence)
        return

    monkeypatch.setattr(qt.TradesTail, "_refresh", mutant_refresh)
    assert [x["id"] for x in tail.between(0.0, 3.0)] == [1]  # missed id 2
    # restore and prove the real path sees both
    monkeypatch.undo()
    tail2 = qt.TradesTail(str(p))
    assert [x["id"] for x in tail2.between(0.0, 3.0)] == [1, 2]


def test_mutation_proof_dropping_partial_corrupts_next_line(tmp_path, monkeypatch):
    p = tmp_path / "bfx_trades.jsonl"
    p.write_bytes(b"")
    tail = qt.TradesTail(str(p))
    part = _row(7, [(7, 7_100, 0.1, 70.0)]).encode()
    with p.open("ab") as fh:
        fh.write(part[:15])
    tail._refresh()
    # mutant: discard partial instead of keeping it
    monkeypatch.setattr(tail, "_partial", b"")
    with p.open("ab") as fh:
        fh.write(part[15:] + b"\n")
    tail._refresh()
    # without the kept prefix the line is invalid JSON -> empty; real path would have sec 7
    assert 7 not in tail._by_s
    # control: fresh tail with full file parses
    assert 7 in _full_rebuild(p)
