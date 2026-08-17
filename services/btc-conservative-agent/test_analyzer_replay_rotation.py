import tempfile
from pathlib import Path

import analyzer_research_engine_v62 as analyzer


def run():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in (
            "signal_replay.jsonl",
            "signal_replay.jsonl.1",
            "signal_replay.jsonl.6",
            "signal_replay.jsonl.29",
            "signal_replay.jsonl.tmp",
        ):
            (root / name).write_text("{}\n", encoding="utf-8")
        original = analyzer._agent_data_path
        try:
            analyzer._agent_data_path = lambda filename: str(root / filename)
            observed = [Path(path).name for path in analyzer._signal_replay_paths()]
            (root / "signal_replay.jsonl.1").write_text(
                '{"trade_id":"old-valid","post_exit_tick_count":1,"ticks":[{"seq":1,"t":12.0}]}\nnot-json\n', encoding="utf-8"
            )
            (root / "signal_replay.jsonl").write_text(
                '{"trade_id":"active-valid","ticks":[]}\n'
                '{"trade_id":"old-valid","replay_complete":false,"post_exit_tick_count":0,"ticks":[]}\n',
                encoding="utf-8",
            )
            analyzer._replay_cache = None
            loaded = analyzer._load_jsonl_replays(use_cache=False)
        finally:
            analyzer._agent_data_path = original
            analyzer._replay_cache = None
        expected = [
            "signal_replay.jsonl.1",
            "signal_replay.jsonl.6",
            "signal_replay.jsonl.29",
            "signal_replay.jsonl",
        ]
        if observed != expected:
            raise AssertionError(f"dynamic replay rotations mismatch: {observed!r}")
        if set(loaded) != {"old-valid", "active-valid"}:
            raise AssertionError(f"malformed row discarded valid replay evidence: {loaded!r}")
        if len(loaded["old-valid"]["ticks"]) != 1:
            raise AssertionError("compact later revision erased rotated tick evidence")

        rich = {
            "trade_id": "incident",
            "replay_complete": False,
            "post_exit_tick_count": 1,
            "post_exit_sec": 0,
            "ticks": [{"seq": 1, "t": 10.0}, {"seq": 2, "t": 20.0}],
        }
        compact = {
            "trade_id": "incident",
            "replay_complete": False,
            "post_exit_tick_count": 0,
            "post_exit_sec": 0,
            "replay_completion_reason": "INCOMPLETE_EXECUTED_POST_EXIT",
            "ticks": [],
        }
        merged = analyzer._merge_replay_revision(rich, compact)
        if merged["ticks"] != rich["ticks"] or merged["post_exit_tick_count"] != 1:
            raise AssertionError(f"compact terminal row erased richer replay evidence: {merged!r}")
        completed = analyzer._merge_replay_revision(merged, {
            "trade_id": "incident",
            "replay_complete": True,
            "post_exit_complete": True,
            "post_exit_tick_count": 2,
            "post_exit_sec": 7200,
            "ticks": [],
        })
        if completed["replay_complete"] is not True or completed["post_exit_complete"] is not True:
            raise AssertionError(f"explicit completion was not retained: {completed!r}")
        if completed["ticks"] != rich["ticks"] or completed["post_exit_sec"] != 7200:
            raise AssertionError(f"completion metadata erased path evidence: {completed!r}")
    print("PASS: analyzer reads all numeric replay rotations before the active file")


if __name__ == "__main__":
    run()
