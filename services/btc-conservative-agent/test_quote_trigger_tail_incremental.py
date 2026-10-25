"""R1 CPU v728 / R8: TradesTail reads only appended bytes (inode+size gate, in-memory offset, partial rows kept,
full reload on shrink or a new inode). Every state must equal a fresh full rebuild of the same file."""
import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import quote_trigger as qt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CUTOFF = 1792263671
FIX = os.path.join(HERE, "fixtures", "bfx_trades_recorded_20261025.jsonl.gz")


def _lines():
    with gzip.open(FIX, "rb") as fh:
        out = [ln + b"\n" for ln in fh.read().split(b"\n") if ln]
    assert len(out) == 400
    return out


def _views(t):
    with t._lock:
        t._refresh()
        by_s = {k: list(v) for k, v in t._by_s.items()}
    if not by_s:
        return by_s, None, [], [], None
    hi, lo = max(by_s), min(by_s)
    return (by_s, t._max_s, t.between(lo - 1, hi + 2), t.received_after(lo - 1, hi + 2, lo + (hi - lo) / 2),
            t.coverage())


def _assert_same(inc, path, **kw):
    full = qt.TradesTail(path, **kw)
    assert _views(inc) == _views(full)


def test_fixture_is_post_cutoff_real_rows():
    import json
    assert min(json.loads(ln)["s"] for ln in _lines()) >= CUTOFF


def test_append_replay_equals_full_rebuild_after_every_append(tmp_path):
    p = str(tmp_path / "bfx_trades.jsonl")
    open(p, "wb").close()
    inc = qt.TradesTail(p, tail_bytes=40000)
    for i, ln in enumerate(_lines()):
        with open(p, "ab") as fh:
            if i % 7 == 0:  # a print row arriving in two partial writes
                fh.write(ln[: len(ln) // 2])
                fh.flush()
                _assert_same(inc, p, tail_bytes=40000)
                fh.write(ln[len(ln) // 2:])
            else:
                fh.write(ln)
        _assert_same(inc, p, tail_bytes=40000)
    assert inc._max_s == max(inc._by_s)


def test_unchanged_file_is_not_reread(tmp_path, monkeypatch):
    p = str(tmp_path / "bfx_trades.jsonl")
    with open(p, "wb") as fh:
        fh.writelines(_lines()[:50])
    t = qt.TradesTail(p)
    t._refresh()
    calls = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: calls.append(a) or real_open(*a, **k))
    for _ in range(20):
        t._refresh()
    assert calls == []


def test_truncation_triggers_full_reload(tmp_path):
    p = str(tmp_path / "bfx_trades.jsonl")
    ls = _lines()
    with open(p, "wb") as fh:
        fh.writelines(ls[:200])
    inc = qt.TradesTail(p)
    _assert_same(inc, p)
    with open(p, "wb") as fh:  # truncated in place (same inode), then a few new rows
        fh.writelines(ls[300:310])
    assert os.path.getsize(p) < inc._off
    _assert_same(inc, p)
    with open(p, "ab") as fh:
        fh.writelines(ls[310:320])
    _assert_same(inc, p)


def test_rotation_new_inode_same_size_triggers_full_reload(tmp_path):
    p = str(tmp_path / "bfx_trades.jsonl")
    ls = _lines()
    with open(p, "wb") as fh:
        fh.writelines(ls[:100])
    inc = qt.TradesTail(p)
    _assert_same(inc, p)
    size = os.path.getsize(p)
    # new file, same byte size, different rows (later rows padded/cut to the exact size) -> only the inode differs
    body = b"".join(ls[200:])[:size]
    body = body[: body.rfind(b"\n") + 1]
    body += b" " * (size - len(body) - 1) + b"\n"
    q = str(tmp_path / "new.jsonl")
    with open(q, "wb") as fh:
        fh.write(body)
    keep = str(tmp_path / "old.jsonl")
    os.rename(p, keep)  # old inode stays alive, so the new file cannot reuse it
    os.rename(q, p)
    assert os.path.getsize(p) == size
    _assert_same(inc, p)
