"""Daily-5 (4): /parts must finish under budget / support meta=1 (R0c eb14e8711d31)."""
from __future__ import annotations

import time

import monitor_streams as ms


def test_empty_parts_skip_edge_ts(tmp_path):
    rel = ms.STREAMS["book_tape_1s"]
    (tmp_path / rel).write_bytes(b"")
    (tmp_path / f"{rel}.1").write_bytes(b"")
    out = ms.parts_with_bounds(tmp_path, "book_tape_1s", include_edge_ts=True)
    assert out["truncated"] is False
    assert all(p.get("edge_ts_skipped") == "empty" for p in out["parts"])
    assert all(p["first_ts"] is None and p["last_ts"] is None for p in out["parts"])


def test_meta_path_skips_edge_without_opening_bodies(tmp_path, monkeypatch):
    rel = ms.STREAMS["disloc_trades"]
    row = b'{"ts": 1700000000}\n'
    (tmp_path / rel).write_bytes(row * 10)
    for i in range(1, 6):
        (tmp_path / f"{rel}.{i}").write_bytes(row * 10)

    calls = {"n": 0}
    real = ms._edge_ts

    def wrapped(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(ms, "_edge_ts", wrapped)
    out = ms.parts_with_bounds(tmp_path, "disloc_trades", include_edge_ts=False)
    assert calls["n"] == 0
    assert out["edge_ts"] is False
    assert all(p.get("edge_ts_skipped") == "meta" for p in out["parts"])


def test_budget_truncates_edge_work_on_many_parts(tmp_path, monkeypatch):
    rel = ms.STREAMS["dyn_regime_minutes"]
    row = b'{"ts": 1700000000}\n'
    (tmp_path / rel).write_bytes(row * 5)
    for i in range(1, 40):
        (tmp_path / f"{rel}.{i}").write_bytes(row * 5)

    def slow_edge(*_a, **_k):
        time.sleep(0.05)
        return 1700000000.0

    monkeypatch.setattr(ms, "_edge_ts", slow_edge)
    started = time.monotonic()
    out = ms.parts_with_bounds(
        tmp_path, "dyn_regime_minutes", include_edge_ts=True, budget_sec=0.12,
    )
    elapsed = time.monotonic() - started
    assert out["truncated"] is True
    assert elapsed < 1.0  # must not scan all 40 parts at 50ms each
    assert any(p.get("edge_ts_skipped") == "meta" for p in out["parts"])


def test_mutation_unlimited_budget_never_sets_truncated(tmp_path, monkeypatch):
    """Mutation: with huge budget and fast edges, truncated must stay False."""
    rel = ms.STREAMS["disloc_foi_trades"]
    row = b'{"ts": 1700000001}\n'
    (tmp_path / rel).write_bytes(row)
    for i in range(1, 8):
        (tmp_path / f"{rel}.{i}").write_bytes(row)
    monkeypatch.setattr(ms, "_edge_ts", lambda *_a, **_k: 1700000001.0)
    out = ms.parts_with_bounds(
        tmp_path, "disloc_foi_trades", include_edge_ts=True, budget_sec=60.0,
    )
    assert out["truncated"] is False
    assert all(p.get("first_ts") == 1700000001.0 for p in out["parts"])
