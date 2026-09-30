"""Session-age label counts from the v2 segment genesis, not the old bot start."""

import json
import time

import analyzer_research_engine_v62 as analyzer
import research_segment_promotion as promotion


def _payload(tmp_path, monkeypatch, files):
    for name, doc in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    session = {"bot_start_time": time.time() - 213 * 3600}
    return analyzer.build_executive_summary_payload(session=session, data_scope="session")


def test_promoted_heartbeat_genesis_drives_label_without_touching_epoch(tmp_path, monkeypatch):
    genesis = time.time() - 4.5 * 3600
    payload = _payload(tmp_path, monkeypatch, {
        ".fly-data-sync-loop.heartbeat.json": {"segmentGenesisAt": genesis,
                                               "collectionEpochId": "epoch-keep"},
    })
    assert 4.4 < payload["data_start_hours"] < 4.6
    assert payload["session_hours"] > 200
    label = analyzer._session_age_label(payload)
    assert label.startswith("~4.") and "since v2 data start" in label
    assert "bot session" not in analyzer.format_executive_summary_short(payload)


def test_fly_shipper_baseline_is_preferred(tmp_path, monkeypatch):
    payload = _payload(tmp_path, monkeypatch, {
        "segment-shipper-v2/state.json": {"seq": 3, "baseline": {"created_at": time.time() - 3600}},
        ".fly-data-sync-loop.heartbeat.json": {"segmentGenesisAt": time.time() - 9 * 3600},
    })
    assert 0.9 < payload["data_start_hours"] < 1.1


def test_missing_genesis_falls_back_to_bot_session_label(tmp_path, monkeypatch):
    payload = _payload(tmp_path, monkeypatch, {})
    assert payload["data_start_hours"] is None
    assert analyzer._session_age_label(payload).endswith("h bot session")


def test_promotion_genesis_window_end_only_accepts_seq_one():
    assert promotion.genesis_window_end(json.dumps({"seq": 1, "window_end": 17.5}).encode()) == 17.5
    assert promotion.genesis_window_end(json.dumps({"seq": 2, "window_end": 17.5}).encode()) is None
    assert promotion.genesis_window_end(None) is None
    assert promotion.genesis_window_end(b"not-json") is None
