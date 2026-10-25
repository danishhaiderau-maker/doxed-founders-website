"""Session-age label counts from the analyzer floor (never pre-cutoff genesis)."""

import json
import time

import analyzer_research_engine_v62 as analyzer
import research_segment_promotion as promotion

FLOOR = analyzer.ANALYZER_MIN_FLOOR_TS


def _payload(tmp_path, monkeypatch, files):
    for name, doc in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    session = {"bot_start_time": time.time() - 213 * 3600}
    return analyzer.build_executive_summary_payload(session=session)


def test_hard_floor_drives_label_when_genesis_is_absent(tmp_path, monkeypatch):
    payload = _payload(tmp_path, monkeypatch, {})
    hours = (time.time() - FLOOR) / 3600.0
    assert payload["data_start_hours"] is not None
    assert abs(payload["data_start_hours"] - hours) < 0.05
    label = analyzer._session_age_label(payload)
    assert "since fresh start" in label
    assert "18 Oct 06:01 AEDT" in label
    assert "bot session" not in analyzer.format_executive_summary_short(payload)


def test_fresh_start_above_floor_is_preferred_over_older_shipper(tmp_path, monkeypatch):
    fresh = time.time() - 3600
    payload = _payload(tmp_path, monkeypatch, {
        "fresh_start_epoch.json": {"epoch_ts": fresh},
        "segment-shipper-v2/state.json": {"seq": 3, "baseline": {"created_at": time.time() - 9 * 3600}},
        ".fly-data-sync-loop.heartbeat.json": {"segmentGenesisAt": time.time() - 9 * 3600},
    })
    assert 0.9 < payload["data_start_hours"] < 1.1
    assert "since fresh start" in analyzer._session_age_label(payload)


def test_pre_floor_genesis_cannot_pull_label_below_hard_minimum(tmp_path, monkeypatch):
    # Heartbeat / shipper timestamps below the credibility cutoff must not win.
    payload = _payload(tmp_path, monkeypatch, {
        ".fly-data-sync-loop.heartbeat.json": {
            "segmentGenesisAt": FLOOR - 4.5 * 3600,
            "collectionEpochId": "epoch-keep",
        },
        "segment-shipper-v2/state.json": {
            "seq": 3,
            "baseline": {"created_at": FLOOR - 3600},
        },
    })
    hours = (time.time() - FLOOR) / 3600.0
    assert abs(payload["data_start_hours"] - hours) < 0.05
    assert "18 Oct 06:01 AEDT" in analyzer._session_age_label(payload)


def test_promotion_genesis_window_end_only_accepts_seq_one():
    assert promotion.genesis_window_end(json.dumps({"seq": 1, "window_end": 17.5}).encode()) == 17.5
    assert promotion.genesis_window_end(json.dumps({"seq": 2, "window_end": 17.5}).encode()) is None
    assert promotion.genesis_window_end(None) is None
    assert promotion.genesis_window_end(b"not-json") is None
