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
                '{"trade_id":"old-valid","ticks":[]}\nnot-json\n', encoding="utf-8"
            )
            (root / "signal_replay.jsonl").write_text(
                '{"trade_id":"active-valid","ticks":[]}\n', encoding="utf-8"
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
    print("PASS: analyzer reads all numeric replay rotations before the active file")


if __name__ == "__main__":
    run()
