"""Regression tests for the incremental segment shipper (Stage-2 root-cause fix).

Proves the delta-aware scan + incremental plan produce byte-identical segments,
manifests and final checkpoint state to the legacy full-scan/full-plan path, that
the delta path detects add/modify/delete (including same-size rewrites and
rotations) exactly, and that the checkpoint stays authoritative, recoverable and
idempotent across a simulated mid-cycle crash.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

import pytest

import research_segment_format as fmt
import research_segment_scan as scanlib
import research_segment_shipper as shipper_mod
from test_research_segments import Env, _rows


def _canon(ops):
    """Order-insensitive op signature (op order is already deterministic)."""
    return [(o["stream"], o["kind"], o["path"], o.get("base_offset"), o.get("end_offset"))
            for o in ops]


def _run(shipper, state, universe, changed=None):
    """plan -> select -> build, returning (ops, selected, built-or-None)."""
    shipper.throttled = []
    shipper.race_backoff.clear()
    shipper.next_cursor = ""
    ops = shipper.plan(state, universe, changed)
    selected, _deferred = shipper.select(ops, cursor=str(state.get("select_cursor") or ""))
    built = shipper.build(state, selected) if selected else None
    return ops, selected, built


def _changed_set(before, after):
    """Simulate exactly what the watcher reports: symdiff + in-place identity changes."""
    changed = set(before) ^ set(after)
    for rel in before:
        if rel in after:
            bstat, astat = before[rel][1], after[rel][1]
            if (int(bstat.st_size), int(bstat.st_mtime_ns), int(bstat.st_ino)) != (
                    int(astat.st_size), int(astat.st_mtime_ns), int(astat.st_ino)):
                changed.add(rel)
    return changed


# ------------------------------------------------------------------- (a) identical output
def test_incremental_matches_full_on_fresh_universe(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.write("b.jsonl", _rows(0, 3))
    env.write("s.json", b'{"k": 1}')
    env.write("state/nested.json", b'{"x": 2}')
    shipper = env.shipper()
    state = shipper.load_state()

    universe = shipper._full_scan()
    shipper._universe = dict(universe)
    changed = set(universe)  # everything is new -> everything is "changed"

    ops_f, _sel_f, built_f = _run(shipper, state, universe, changed=None)
    ops_i, _sel_i, built_i = _run(shipper, state, shipper._universe, changed)

    assert _canon(ops_f) == _canon(ops_i)
    assert built_f == built_i  # (segment bytes, manifest bytes, new_state) identical


def test_incremental_matches_full_on_second_cycle(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.write("b.jsonl", _rows(0, 3))
    env.write("s.json", b'{"k": 1}')
    shipper = env.shipper()
    state = shipper.load_state()

    # commit cycle 1 via the full path
    universe = shipper._full_scan()
    ops = shipper.plan(state, universe)
    selected, _ = shipper.select(ops)
    _seg, _man, state = shipper.build(state, selected)
    shipper._commit_state(state)

    before = dict(shipper._full_scan())
    # mutate: append to a.jsonl, add c.json, delete b.jsonl
    env.write("a.jsonl", _rows(5, 2), append=True)
    env.write("c.json", b'{"new": true}')
    (env.runtime / "b.jsonl").unlink()

    after = shipper._full_scan()
    changed = _changed_set(before, after)

    shipper._universe = dict(before)
    shipper._apply_scan_changes(changed)
    assert shipper._universe == after  # delta scan == full scan

    ops_f, _sel_f, built_f = _run(shipper, state, after, changed=None)
    ops_i, _sel_i, built_i = _run(shipper, state, shipper._universe, changed)
    assert _canon(ops_f) == _canon(ops_i)
    assert built_f == built_i


# ------------------------------------------------------------------- (b) delta detection
def test_incremental_detects_same_size_rewrite_and_rotation(tmp_path):
    env = Env(tmp_path)
    live = env.write("t.jsonl", _rows(0, 4))
    env.write("s.json", b'{"k": 1}')
    shipper = env.shipper()
    state = shipper.load_state()
    universe = shipper._full_scan()
    ops = shipper.plan(state, universe)
    selected, _ = shipper.select(ops)
    _seg, _man, state = shipper.build(state, selected)
    shipper._commit_state(state)

    before = dict(shipper._full_scan())
    # same-size rewrite of s.json (mtime bump) + rotation of t.jsonl
    (env.runtime / "s.json").write_bytes(b'{"k": 2}')
    os.utime(env.runtime / "s.json", ns=(before["s.json"][1].st_atime_ns,
                                          before["s.json"][1].st_mtime_ns + 5_000_000_000))
    os.rename(live, live.with_name("t.jsonl.1"))
    env.write("t.jsonl", _rows(100, 2))

    after = shipper._full_scan()
    changed = _changed_set(before, after)

    shipper._universe = dict(before)
    shipper._apply_scan_changes(changed)
    assert shipper._universe == after

    ops_f, _sel_f, built_f = _run(shipper, state, after, changed=None)
    ops_i, _sel_i, built_i = _run(shipper, state, shipper._universe, changed)
    assert _canon(ops_f) == _canon(ops_i)
    assert built_f == built_i
    kinds = {(o["kind"], o["path"]) for o in ops_i}
    assert ("SEAL", "t.jsonl.1") in kinds  # rotation was detected exactly


def test_unchanged_universe_plans_no_ops_incrementally(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.write("s.json", b'{"k": 1}')
    shipper = env.shipper()
    env.ship_all()  # fully ship so nothing is pending
    state = shipper.load_state()
    universe = shipper._full_scan()
    assert shipper.plan(state, universe) == []  # full path: no work
    assert shipper.plan(state, universe, changed=set()) == []  # incremental: no work


def test_incremental_redrains_pending_clamps_and_matches_full(tmp_path):
    # A large append that exceeds the segment budget is clamped; the unshipped
    # remainder must keep re-planning incrementally even when *no* file changed,
    # and each cycle's incremental plan must equal the full plan exactly.
    env = Env(tmp_path, max_segment_bytes=300)
    env.write("big.jsonl", _rows(0, 40))
    env.write("small.json", b"{}")
    inc = env.shipper()
    full = env.shipper()  # read-only reference for the full plan
    state = inc.load_state()
    universe = inc._full_scan()
    changed = set(universe)  # genesis: everything is new

    def run(shipper, st, uni, ch):
        shipper.throttled = []
        shipper.race_backoff.clear()
        ops = shipper.plan(st, uni, ch)
        selected, _ = shipper.select(ops, cursor=str(st.get("select_cursor") or ""))
        return ops, selected

    for _cycle in range(3):
        ops_i, selected_i = run(inc, state, universe, changed)
        ops_f, _ = run(full, state, universe, None)
        assert _canon(ops_i) == _canon(ops_f)
        assert any(o["stream"] == "big.jsonl" and o["kind"] == "APPEND" for o in ops_i)
        _seg, _man, state = inc.build(state, selected_i)
        inc._commit_state(state)
        changed = set()  # nothing changes; the pending clamp alone must keep draining
    assert "big.jsonl" not in inc._pending_clamps  # fully drained
    assert run(inc, state, universe, set())[0] == []  # incremental: no work remains
    assert run(full, state, universe, None)[0] == []  # full: agrees


# ------------------------------------------------------------------- (c) crash recovery
def test_cow_build_does_not_mutate_input_state(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.write("s.json", b'{"k": 1}')
    shipper = env.shipper()
    state = shipper.load_state()
    snapshot = json.loads(json.dumps(state))
    universe = shipper._full_scan()
    ops = shipper.plan(state, universe)
    selected, _ = shipper.select(ops)
    shipper.build(state, selected)
    assert state == snapshot  # copy-on-write never mutates the input checkpoint


def test_crash_mid_cycle_leaves_checkpoint_authoritative_and_idempotent(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 4))
    shipper = env.shipper()
    assert shipper.cycle()["shipped"]["seq"] == 1
    on_disk = json.loads(shipper.state_path.read_text())
    assert on_disk["seq"] == 1 and shipper.load_state() == on_disk  # cache == authoritative disk

    def boom(*_a, **_k):
        raise shipper_mod.PlanRace("simulated mid-build crash", "a.jsonl")

    monkeypatch.setattr(shipper, "build", boom)
    env.write("a.jsonl", _rows(4, 2), append=True)
    result = shipper.cycle()
    assert result["shipped"] is None and result.get("race") == "a.jsonl"
    monkeypatch.undo()
    assert shipper.load_state()["seq"] == 1  # nothing committed by the failed cycle

    # A fresh process reloads the authoritative checkpoint and ships seq 2 exactly once.
    fresh = env.shipper()
    assert fresh.load_state()["seq"] == 1
    assert fresh.cycle()["shipped"]["seq"] == 2
    assert fresh.cycle()["shipped"] is None  # idempotent
    env.puller().pull_once()
    env.assert_tree_matches_source()


# ------------------------------------------------------------------- watcher parser
def _event(wd, mask, name, cookie=0):
    name_bytes = name.encode() + b"\x00"
    length = (len(name_bytes) + 3) & ~3
    padded = name_bytes + b"\x00" * (length - len(name_bytes))
    return struct.pack("iIII", wd, mask, cookie, length) + padded


def test_parse_inotify_buffer_detects_changes_exactly():
    dir_of = {1: "", 2: "sub"}
    data = b"".join([
        _event(1, scanlib.IN_MODIFY, "a.jsonl"),
        _event(1, scanlib.IN_CREATE, "new.json"),
        _event(2, scanlib.IN_DELETE, "gone.json"),
        _event(1, scanlib.IN_CREATE | scanlib.IN_ISDIR, "newdir"),
    ])
    changed, appeared, vanished, overflow = scanlib.parse_inotify_buffer(data, dir_of)
    assert changed == {"a.jsonl", "new.json", "sub/gone.json", "newdir"}
    assert appeared == [("", "newdir")]
    assert vanished == []
    assert overflow is False


def test_parse_inotify_overflow_signals_full_rescan():
    data = struct.pack("iIII", -1, scanlib.IN_Q_OVERFLOW, 0, 0)
    changed, appeared, vanished, overflow = scanlib.parse_inotify_buffer(data, {})
    assert overflow is True and changed == set() and appeared == [] and vanished == []


def test_watcher_availability_matches_platform():
    assert scanlib.watcher_available() is scanlib.IS_LINUX
