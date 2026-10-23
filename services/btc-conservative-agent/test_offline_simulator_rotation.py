import ast
import copy
import json
import os
from pathlib import Path
from research import platform_relay_evidence as pure_relay


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


class _Logger:
    def debug(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass


def _append_jsonl(path, row, **_kwargs):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return True


def _load_functions(*names):
    tree = ast.parse(SOURCE)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in set(names)
    ]
    namespace = {
        "Path": Path,
        "copy": copy,
        "json": json,
        "os": os,
        "hashlib": __import__("hashlib"),
        "logger": _Logger(),
        "_buf_float": lambda value, default=0: float(value if value is not None else default),
        "_OFFLINE_SIM_MAX_ROTATIONS": 128,
        "_OFFLINE_SIM_MAX_JSONL_ROW_BYTES": 8 * 1024 * 1024,
        "SIGNAL_SNAPSHOT_FILE": "signal_snapshot.jsonl",
        "SIGNAL_REPLAY_FILE": "signal_replay.jsonl",
        "COUNTERFACTUAL_FILE": "counterfactual.jsonl",
        "_pure_offline_sim_jsonl_paths": pure_relay._offline_sim_jsonl_paths,
        "_pure_load_offline_sim_jsonl_revisions": pure_relay._load_offline_sim_jsonl_revisions,
        "Optional": __import__("typing").Optional,
        "math": __import__("math"),
        "time": __import__("time"),
        "datetime": __import__("datetime").datetime,
        "timezone": __import__("datetime").timezone,
        "utc_iso": lambda: "2026-01-01T00:00:00+00:00",
        "_snapshot_shared_ai_call_id": lambda *_args: None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_rotation_loader_is_bounded_malformed_safe_and_newest_revision_wins(tmp_path):
    funcs = _load_functions("_offline_sim_jsonl_paths", "_load_offline_sim_jsonl_revisions")
    active = tmp_path / "signal_snapshot.jsonl"
    (tmp_path / "signal_snapshot.jsonl.1").write_text(
        '{"trade_id":"cont-old","revision":1}\n'
        '{"trade_id":"cont-shared","revision":1}\n', encoding="utf-8"
    )
    (tmp_path / "signal_snapshot.jsonl.2").write_text(
        'not-json\n[]\n{"trade_id":"cont-shared","revision":2}\n', encoding="utf-8"
    )
    (tmp_path / "signal_snapshot.jsonl.tmp").write_text(
        '{"trade_id":"cont-temp","revision":99}\n', encoding="utf-8"
    )
    active.write_text(
        '{"trade_id":"cont-shared","revision":3}\n'
        '{"trade_id":"cont-active","revision":1}\n', encoding="utf-8"
    )

    paths = [Path(path).name for path in funcs["_offline_sim_jsonl_paths"](active, 1)]
    assert paths == ["signal_snapshot.jsonl.2", "signal_snapshot.jsonl"]
    loaded = funcs["_load_offline_sim_jsonl_revisions"](
        active, target_trade_ids={"cont-old", "cont-shared"}
    )
    assert set(loaded) == {"cont-old", "cont-shared", "cont-active"}
    assert loaded["cont-shared"]["revision"] == 3
    assert "cont-temp" not in loaded
    active_only = funcs["_load_offline_sim_jsonl_revisions"](active)
    assert set(active_only) == {"cont-shared", "cont-active"}


