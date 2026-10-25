"""RK #882 12:48: analyzer floor = max(epoch manifest started_at_ts, fresh_start_epoch.json epoch_ts)."""
import json

import pandas as pd

import analyzer_research_engine_v62 as eng

FRESH = 1792263671  # 18 Oct 06:01 AEDT
BOT_START = 1792261592


class _NoGuard:
    declared = False
    manifest = {}


def _setup(tmp_path, monkeypatch, fresh=FRESH):
    (tmp_path / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": fresh}))
    monkeypatch.setattr(eng, "_agent_data_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(eng, "_epoch_guard", lambda: _NoGuard())


def test_floor_is_fresh_start_and_label_reads_it(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    start = eng._v2_data_start_ts()
    assert start.timestamp() == FRESH
    assert start.tz_convert("Australia/Sydney").strftime("%d %b %H:%M") == "18 Oct 06:01"


def test_floor_is_max_of_manifest_and_fresh_start(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    class G:
        declared = True
        manifest = {"started_at_ts": FRESH + 100, "epoch_id": "e"}
    monkeypatch.setattr(eng, "_epoch_guard", lambda: G())
    assert eng._analyzer_floor_epoch_ts() == FRESH + 100
    G.manifest = {"started_at_ts": FRESH - 100, "epoch_id": "e"}
    assert eng._analyzer_floor_epoch_ts() == FRESH


def test_cohort_cutoff_raised_from_bot_start_to_fresh_start(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert eng._session_start_ts({"bot_start_time": BOT_START}).timestamp() == FRESH
    assert eng._session_start_ts({"bot_start_time": FRESH + 5}).timestamp() == FRESH + 5


def test_selector_rejects_pre_cutoff_row_and_uses_epoch_admit(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(eng, "_epoch_admit", lambda rel, row: seen.append(rel) or row.get("id") != "blocked")
    rows = [{"id": "pre", "close_ts": str(FRESH - 1)}, {"id": "post", "close_ts": str(FRESH + 1)},
            {"id": "blocked", "close_ts": str(FRESH + 2)}, {"id": "notime"}]
    out = eng._selector_trades(lambda _path: rows, "trades.csv")
    assert [r["id"] for r in out] == ["post"]
    assert seen and set(seen) == {"trades.csv"}


def test_since_label_reads_fresh_start_in_aedt(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    label = eng._floor_label(eng._v2_data_start_ts())
    assert label == "18 Oct 06:01 AEDT"
    text = eng._session_age_label({"data_start_hours": 1.0, "data_start_label": label, "data_start_utc": "x"})
    assert text == "~1.0h since fresh start 18 Oct 06:01 AEDT"
