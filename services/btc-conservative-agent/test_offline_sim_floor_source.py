"""Fly's research_session.json has fresh_collection_start_time null; the floor must come from fresh_start_epoch.json."""
import json, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _fn():
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    m = re.search(r"def _offline_sim_epoch_floor_detail\(\):.*?\n\n\n(?=# HM C9 \(R17\): _load_offline_sim_jsonl_revisions)", src, re.S)
    ns = {"os": os, "logger": type("L", (), {"warning": staticmethod(lambda *a, **k: None)}),
          "_fresh_epoch_identity_from_session": lambda: (None, None, None)}
    ns["__builtins__"] = __builtins__
    exec(m.group(0), ns)
    return ns


def test_floor_read_from_fresh_start_epoch_when_session_has_none(tmp_path, monkeypatch):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": 1792263671.2608788}))
    monkeypatch.chdir(tmp_path / "runtime")
    assert _fn()["_offline_sim_epoch_floor"]() == 1792263671.2608788


def test_session_is_only_a_fallback_and_missing_everything_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ns = _fn()
    assert ns["_offline_sim_epoch_floor"]() is None
    ns["_fresh_epoch_identity_from_session"] = lambda: ("e", "1792263671.5", "k")
    assert ns["_offline_sim_epoch_floor"]() == 1792263671.5
