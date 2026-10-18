"""memory_structures: read-only, bounded snapshot that names the growing in-process structure."""
import json
import sys
import types

import memory_structures as ms


def test_snapshot_names_the_growing_container_and_writes_atomically(tmp_path):
    mod = types.ModuleType("fake_growth_mod")
    mod.BIG = {i: i for i in range(3_000_000)}  # larger than every container in the test process
    mod.SMALL = [1, 2, 3]
    sys.modules["fake_growth_mod"] = mod
    try:
        t = ms.Tracker(tmp_path / "m.json", interval_sec=0)
        t.run(now=1000.0)
        mod.BIG.update({i: i for i in range(3_000_000, 3_000_300)})
        doc = t.run(now=1060.0)
    finally:
        del sys.modules["fake_growth_mod"]
    top = doc["container_growth"][0]
    assert top["name"] == "fake_growth_mod.BIG" and top["len_growth"] == 300 and top["len_now"] == 3_000_300
    assert all(r["name"] != "fake_growth_mod.SMALL" for r in doc["latest"]["containers"])  # < 50 items: not listed
    assert doc["samples"] == 2 and doc["window_sec"] == 60.0
    assert json.loads((tmp_path / "m.json").read_text())["schema"] == "memory_structures_v1"
    assert not list(tmp_path.glob(".*tmp"))


def test_due_respects_interval_and_history_is_bounded(tmp_path):
    t = ms.Tracker(tmp_path / "m.json", interval_sec=600)
    assert t.due(1000.0)
    t.run(now=1000.0)
    assert not t.due(1599.0) and t.due(1600.0)
    for i in range(ms.KEEP + 5):
        t.run(now=2000.0 + i)
    assert len(t.history) == ms.KEEP


def test_bot_runs_the_tracker_from_the_uptime_loop():
    from pathlib import Path
    src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "memory_structures.json" in src and "_mem_tracker.run(now)" in src
