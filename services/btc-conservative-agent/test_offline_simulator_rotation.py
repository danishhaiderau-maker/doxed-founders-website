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


def test_offline_simulator_joins_historical_rotations_and_appends_changed_platform_revision(tmp_path):
    funcs = _load_functions(
        "_offline_sim_jsonl_paths",
        "_load_offline_sim_jsonl_revisions",
        "offline_simulator",
        "_compact_source_market_evidence",
        "_compact_market_path_ref",
        "_path_gap_census",
    )
    snapshot = tmp_path / "signal_snapshot.jsonl"
    replay = tmp_path / "signal_replay.jsonl"
    output = tmp_path / "counterfactual.jsonl"
    snapshot.write_text("", encoding="utf-8")
    replay.write_text("", encoding="utf-8")
    (tmp_path / "signal_snapshot.jsonl.4").write_text(json.dumps({
        "trade_id": "cont-historical", "ai": {"approved": True, "win_prob": 0.7},
        "direction": "SHORT", "executed": True, "config": {},
    }) + "\n", encoding="utf-8")
    (tmp_path / "signal_replay.jsonl.7").write_text(json.dumps({
        "trade_id": "cont-historical", "direction": "SHORT", "ticks": [],
        "start_price": 63000, "virtual_entry": 63000, "virtual_fill_t": 1,
    }) + "\n", encoding="utf-8")
    output.write_text(json.dumps({
        "schema": "counterfactual_v2", "trade_id": "cont-historical",
        "platform_evidence_revision": "revision-old",
    }) + "\n", encoding="utf-8")

    funcs.update({
        "write_counter": 0,
        "_sim_processed_trade_ids": {"cont-historical"},
        "_platform_relay_evidence_index": lambda: {
            "cont-historical": {"evidence_revision": "revision-new"}
        },
        "_snapshot_with_platform_relay_evidence": lambda row, _tid, index=None: {
            **row, "platform_evidence_revision": index["cont-historical"]["evidence_revision"]
        },
        "_state_leverage": lambda: 10,
        "FIXED_MARGIN_USDT": 20,
        "state": {"pullback_threshold": 0.001},
        "get_exit_config_snapshot": lambda: {},
        "simulate_replay_outcome": lambda _buf: {"filled": True, "net_pnl_usd": 1.0},
        "build_counterfactual_observability_fields": lambda _buf, snap, _replay, _outcome: {
            "platform_evidence_revision": snap.get("platform_evidence_revision")
        },
        "_safe_append_jsonl": _append_jsonl,
        "rotate_log": lambda _path: None,
    })
    funcs["offline_simulator"](str(snapshot), str(replay), str(output))
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[-1]["trade_id"] == "cont-historical"
    assert rows[-1]["platform_evidence_revision"] == "revision-new"
