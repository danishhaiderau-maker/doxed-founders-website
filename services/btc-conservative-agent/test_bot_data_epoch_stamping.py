"""bot.py clean-epoch hooks: manifest at boot, data_epoch_id on every _safe_append_jsonl row, status field."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import data_epoch

BOT = Path(__file__).with_name("bot.py")
HOOKS = ("_open_data_epoch", "_data_epoch_row", "_data_epoch_public")


def _hooks(runtime_root: Path, epoch_id: str | None):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in HOOKS]
    assert {n.name for n in nodes} == set(HOOKS)
    ns = {
        "os": os, "_data_epoch": data_epoch, "DATA_EPOCH_ID": epoch_id, "_DATA_EPOCH_MANIFEST": None,
        "_data_sync_runtime_root": lambda: runtime_root, "_runtime_git_rev": lambda: "abc123",
        "EXECUTION_FIX_VERSION": "v31-test", "logger": type("L", (), {"info": print, "error": print})(),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), "exec"), ns)
    return ns


def test_boot_opens_manifest_and_rows_are_stamped(tmp_path):
    ns = _hooks(tmp_path, "ce-20261004-v31-clean")
    manifest = ns["_open_data_epoch"]()
    assert manifest["epoch_id"] == "ce-20261004-v31-clean"
    assert json.loads((tmp_path / "data_epoch.json").read_text())["source_git_rev"] == "abc123"
    stamped = ns["_data_epoch_row"](str(tmp_path / "trade_outcome.jsonl"), {"ts": 1})
    assert stamped["data_epoch_id"] == "ce-20261004-v31-clean"
    sealed = {"ts": 1, "row_sha256": "x"}
    assert ns["_data_epoch_row"](str(tmp_path / "v3" / "ledgers" / "x.jsonl"), sealed) is sealed
    tape = {"ts": 1}
    assert ns["_data_epoch_row"](str(tmp_path / "market_microstructure_1s.jsonl"), tape) is tape
    assert ns["_data_epoch_public"]()["declared"] is True
    again = _hooks(tmp_path, "ce-20261004-v31-clean")["_open_data_epoch"]()
    assert again["started_at_ts"] == manifest["started_at_ts"]


def test_no_epoch_configured_leaves_rows_untouched(tmp_path):
    ns = _hooks(tmp_path, None)
    assert ns["_open_data_epoch"]() is None
    row = {"ts": 1}
    assert ns["_data_epoch_row"](str(tmp_path / "a.jsonl"), row) is row
    assert ns["_data_epoch_public"]()["declared"] is False
    assert not (tmp_path / "data_epoch.json").exists()


def test_safe_append_stamps_through_hook_and_survives_isolated_compile():
    source = BOT.read_text(encoding="utf-8")
    body = source[source.index("def _safe_append_jsonl("):source.index("def _validate_research_ledgers_on_startup")]
    assert 'globals().get("_data_epoch_row")' in body
    assert "_open_data_epoch()\n    _wipe_research_on_startup_if_needed()" in source
    assert '"data_epoch": _data_epoch_public(),' in source


def test_fly_toml_declares_epoch_and_fresh_segment_prefix():
    toml = BOT.with_name("fly.toml").read_text(encoding="utf-8")
    epoch = next(line.split('"')[1] for line in toml.splitlines() if line.strip().startswith("DATA_EPOCH_ID"))
    assert data_epoch.valid_epoch_id(epoch)
    assert 'RESEARCH_SEGMENTS_PREFIX = "v3"' in toml and 'RESEARCH_SEGMENTS_BASELINE_GENESIS = "1"' in toml
    assert "v2=/app/data/segment-shipper-v2" in toml
