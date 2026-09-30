"""The fresh-start wipe deletes only closed old rotations and a fully-ACKed v1 epoch."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import research_fresh_start_wipe as wipe

NOW = 2_000_000_000.0
OLD = NOW - 10 * 24 * 3600


def _file(path: Path, raw: bytes = b"x\n", mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture()
def volume(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_SEGMENTS_PREFIX", "v2")
    runtime = tmp_path / "runtime"
    for n in range(1, 6):
        _file(runtime / f"signal_replay.jsonl.{n}")
    _file(runtime / "post_exit_replay.jsonl.1")
    _file(runtime / "post_exit_replay.jsonl.2")
    _file(runtime / "post_exit_replay.jsonl.3", mtime=NOW - 3600)
    for name in ("signal_replay.jsonl", "research_session.json", "research.db", "paper_ledger.json",
                 "config-7002.json", "trades_3factor.csv", "v3/ledgers/tile.jsonl",
                 "v3/receipts/idem/0001.json", ".locks/trade.lock", "state.json.1"):
        _file(runtime / name)
    return tmp_path


def _v1_epoch(root: Path, published: int = 7, acked: bool = True, v2_seq: int = 3) -> None:
    _file(root / "segment-store" / "v1" / "seg" / "000000000001.tar.gz")
    _file(root / "segment-store" / "v1" / "man" / "000000000001.json")
    if acked:
        _file(root / "segment-store" / "v1" / "acks" / "laptop" / f"{published:012d}.json")
    _file(root / "segment-shipper" / "state.json")
    (root / "segment-shipper" / "status.json").write_text(json.dumps({"shipped_seq": published}))
    _file(root / "segment-shipper-v2" / "state.json",
          json.dumps({"seq": v2_seq, "baseline": {"seq": 1} if v2_seq else None}).encode())


def _paths(plan: dict, root: Path) -> set[str]:
    return {Path(row["path"]).relative_to(root).as_posix() for row in plan["candidates"]}


def test_only_old_rotations_beyond_the_newest_two_are_planned(volume):
    plan = wipe.build_plan(volume, now=NOW, v1_acked_through=None)
    assert _paths(plan, volume) == {"runtime/signal_replay.jsonl.1", "runtime/signal_replay.jsonl.2",
                                    "runtime/signal_replay.jsonl.3", "runtime/post_exit_replay.jsonl.1"}
    assert plan["v1_status"] == "V1_ALREADY_ABSENT"


def test_execute_requires_the_same_plan_and_keeps_every_live_file(volume, capsys):
    assert wipe.main(["--data-root", str(volume), "--execute", "--expect-plan-sha256", "0" * 64]) == 3
    assert (volume / "runtime" / "signal_replay.jsonl.1").exists()
    plan = wipe.build_plan(volume, now=NOW, v1_acked_through=None)
    result = wipe.execute(plan, volume)
    assert result["deleted_files"] == 4
    kept = {p.relative_to(volume).as_posix() for p in volume.rglob("*") if p.is_file()}
    assert {"runtime/signal_replay.jsonl", "runtime/research_session.json", "runtime/research.db",
            "runtime/paper_ledger.json", "runtime/config-7002.json", "runtime/.locks/trade.lock",
            "runtime/v3/ledgers/tile.jsonl", "runtime/v3/receipts/idem/0001.json",
            "runtime/signal_replay.jsonl.4", "runtime/signal_replay.jsonl.5",
            "runtime/post_exit_replay.jsonl.2", "runtime/post_exit_replay.jsonl.3",
            "runtime/state.json.1"} <= kept


def test_file_changed_after_planning_aborts_execution(volume):
    plan = wipe.build_plan(volume, now=NOW, v1_acked_through=None)
    _file(volume / "runtime" / "signal_replay.jsonl.2", b"changed\n")
    with pytest.raises(RuntimeError, match="changed after planning"):
        wipe.execute(plan, volume)


@pytest.mark.parametrize("kwargs, acked_through, status", [
    ({"acked": False}, 7, "V1_NOT_FULLY_ACKED(published=7)"),
    ({"acked": False}, "auto", "V1_NOT_FULLY_ACKED(published=7)"),
    ({}, 6, "V1_NOT_FULLY_ACKED(published=7)"),
    ({"v2_seq": 0}, 7, "V2_GENESIS_NOT_PUBLISHED"),
])
def test_v1_epoch_is_kept_until_v2_is_live_and_laptop_acked_everything(volume, kwargs, acked_through, status):
    _v1_epoch(volume, **kwargs)
    plan = wipe.build_plan(volume, now=NOW, v1_acked_through=acked_through)
    assert plan["v1_status"] == status and plan["v1_files"] == 0


def test_v1_epoch_is_kept_while_v1_is_the_active_prefix(volume, monkeypatch):
    _v1_epoch(volume)
    monkeypatch.setenv("RESEARCH_SEGMENTS_PREFIX", "v1")
    assert wipe.build_plan(volume, now=NOW, v1_acked_through=7)["v1_status"] == "V1_STILL_ACTIVE"


@pytest.mark.parametrize("acked_through", [7, "auto"])
def test_fully_acked_v1_epoch_is_removed_but_v2_survives(volume, acked_through):
    _v1_epoch(volume)
    plan = wipe.build_plan(volume, now=NOW, v1_acked_through=acked_through)
    assert plan["v1_status"] == "V1_FULLY_ACKED" and plan["v1_files"] == 5
    wipe.execute(plan, volume)
    assert not (volume / "segment-store" / "v1").exists()
    assert not (volume / "segment-shipper").exists()
    assert (volume / "segment-shipper-v2" / "state.json").is_file()
