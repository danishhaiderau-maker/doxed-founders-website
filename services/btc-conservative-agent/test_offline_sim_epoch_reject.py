"""Leak fix (HM 19 Oct): rotated shards older than the fresh floor are never opened/parsed; line prefilter; one funnel read."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))
import platform_relay_evidence as pre
import execution_funnel as ef

FLOOR = 1792263671.26


def _shard(path, rows, mtime):
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    os.utime(path, (mtime, mtime))


def test_pre_epoch_rotated_shard_is_skipped_unopened_and_fresh_shard_is_read(tmp_path, monkeypatch):
    active = tmp_path / "signal_replay.jsonl"
    _shard(active, [{"trade_id": "a1", "v": "active"}], time.time())
    old = tmp_path / "signal_replay.jsonl.1"
    _shard(old, [{"trade_id": "t1", "v": "old"}], FLOOR - 3600)
    fresh = tmp_path / "signal_replay.jsonl.2"
    _shard(fresh, [{"trade_id": "t2", "v": "fresh"}, {"trade_id": "zz", "v": "not a target"}], FLOOR + 60)
    opened = []
    real_open = open

    def spy(p, *a, **k):
        opened.append(os.path.basename(str(p)))
        return real_open(p, *a, **k)
    monkeypatch.setattr("builtins.open", spy)
    st = {}
    rows = pre._load_offline_sim_jsonl_revisions(str(active), target_trade_ids={"t1", "t2"}, min_ts=FLOOR, stats=st)
    assert set(rows) == {"a1", "t2"}                       # t1 lives only in the pre-epoch shard
    assert "signal_replay.jsonl.1" not in opened and "signal_replay.jsonl.2" in opened
    assert st["shards_seen"] == 2 and st["shards_skipped_pre_epoch"] == 1
    assert st["lines_skipped_prefilter"] == 1              # 'zz' never json.loads-ed


def test_without_a_floor_behaviour_is_unchanged_and_prefilter_matches_json_semantics(tmp_path):
    active = tmp_path / "x.jsonl"
    _shard(active, [], time.time())
    _shard(tmp_path / "x.jsonl.1", [{"trade_id": "t1", "n": 1}, "not json", {"trade_id": "t1", "n": 2}, {"id": "none"}], 5.0)
    rows = pre._load_offline_sim_jsonl_revisions(str(active), target_trade_ids={"t1"})
    assert rows == {"t1": {"trade_id": "t1", "n": 2}}      # newest revision wins, junk lines skipped


def test_rotation_cap_keeps_only_the_newest_shards(tmp_path):
    active = tmp_path / "y.jsonl"
    _shard(active, [], time.time())
    for i in range(1, 13):
        _shard(tmp_path / f"y.jsonl.{i}", [{"trade_id": f"t{i}"}], time.time())
    got = pre._load_offline_sim_jsonl_revisions(str(active), max_rotations=8, target_trade_ids={f"t{i}" for i in range(1, 13)})
    assert set(got) == {f"t{i}" for i in range(5, 13)}


def test_three_execution_reports_share_one_funnel_read_with_identical_output(tmp_path, monkeypatch):
    rows = [{"trade_id": "a", "stage": "APPROVE"}, {"trade_id": "a", "stage": "ORDER_SUBMITTED"},
            {"trade_id": "a", "stage": "FILLED"}, {"trade_id": "b", "stage": "ORDER_EXPIRED", "price_touched": True},
            {"trade_id": "a", "stage": "CLOSED"}]
    (tmp_path / ef.FUNNEL_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    calls = []
    real = ef._load_funnel_rows
    monkeypatch.setattr(ef, "_load_funnel_rows", lambda cwd: calls.append(cwd) or real(cwd))
    one = ef.refresh_all_execution_reports(str(tmp_path))
    assert len(calls) == 1
    strip = lambda d: {k: v for k, v in d.items() if k not in ("generated_at",)}
    calls.clear()
    separate = {"funnel_summary": ef.build_funnel_summary(str(tmp_path)), "fill_quality": ef.build_fill_quality_report(str(tmp_path)),
                "approval_ev": ef.build_approval_ev_report(str(tmp_path))}
    assert len(calls) == 3
    for k in one:
        assert strip(one[k]) == strip(separate[k]), k


def test_straddling_shard_rows_are_rejected_per_row_by_their_own_ts(tmp_path):
    active = tmp_path / "s.jsonl"
    _shard(active, [], time.time())
    _shard(tmp_path / "s.jsonl.1",
           [{"trade_id": "t1", "signal_ts": FLOOR - 30, "v": "pre"}, {"trade_id": "t2", "signal_ts": FLOOR + 30, "v": "fresh"},
            {"trade_id": "t3", "v": "unstamped"}], FLOOR + 600)        # newest row is post-floor, so the shard is opened
    st = {}
    rows = pre._load_offline_sim_jsonl_revisions(str(active), target_trade_ids={"t1", "t2", "t3"}, min_ts=FLOOR, stats=st)
    assert set(rows) == {"t2", "t3"} and st["lines_rejected_pre_epoch"] == 1 and st["lines_parsed"] == 2


def test_unreadable_floor_skips_every_rotated_shard_and_parses_none(tmp_path, monkeypatch):
    active = tmp_path / "u.jsonl"
    _shard(active, [{"trade_id": "a1"}], time.time())
    _shard(tmp_path / "u.jsonl.1", [{"trade_id": "t1", "signal_ts": FLOOR + 5}], FLOOR + 60)
    st = {}
    rows = pre._load_offline_sim_jsonl_revisions(str(active), target_trade_ids={"t1"}, min_ts=None, stats=st, floor_unreadable=True)
    assert set(rows) == {"a1"} and st["shards_skipped_floor_unreadable"] == 1 and st["lines_parsed"] == 1   # only the active line


def test_system_health_check_is_red_when_the_floor_is_unreadable_and_says_source_retired():
    """HM C9 (R17): the loader is retired, so no stuck AMBER "no loader call yet"; the floor itself still gates RED."""
    import bot
    real = bot._offline_sim_epoch_floor_detail
    try:
        bot._offline_sim_epoch_floor_detail = lambda: (1792263671.26, "fresh_start_epoch.json:cwd", None)
        check = bot._offline_sim_epoch_check()
        assert check["status"] == "GREEN" and "source retired (R17)" in check["observed"]
        assert "no loader call yet" not in check["observed"]
        bot._offline_sim_epoch_floor_detail = lambda: (None, None, "no floor")
        assert bot._offline_sim_epoch_check()["status"] == "RED"
        assert not hasattr(bot, "_load_offline_sim_jsonl_revisions")
    finally:
        bot._offline_sim_epoch_floor_detail = real
