import json

import collector_storage
from research_v3_store import V3EvidenceStore


def _fraction(monkeypatch, value):
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: value)


def _mounted(monkeypatch, root):
    monkeypatch.setenv("BOT_DATA_DIR", str(root))


def test_emergency_receipt_distinguishes_pressure_from_new_research_block(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.925)

    state = collector_storage.storage_state(str(tmp_path))
    blocked = collector_storage.emergency_admission(
        data_dir=str(tmp_path), purpose="test:new-opportunity",
    )

    assert state["pressure"] is True
    assert state["emergency"] is True
    assert state["new_nonessential_research_allowed"] is False
    assert state["emergency_threshold"] == 0.90
    assert blocked["allowed"] is False
    assert blocked["reason"] == "NEW_NONESSENTIAL_RESEARCH_BLOCKED_AT_STORAGE_EMERGENCY"


def test_v3_blocks_new_expansion_but_allows_open_lifecycle_completion(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    _fraction(monkeypatch, 0.50)
    opened = store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    assert opened["written"] is True

    _fraction(monkeypatch, 0.925)
    blocked = store.append("opportunity", {
        "record_id": "opportunity:new", "episode_id": "new",
    })
    continuing = store.append("decision", {
        "record_id": "decision:active:1", "episode_id": "active",
    })
    terminal = store.append("lifecycle", {
        "record_id": "lifecycle:active:terminal", "episode_id": "active",
        "terminal": True, "outcome_state": "NO_FILL",
    })
    execution = store.append("execution", {
        "record_id": "execution:active:terminal", "episode_id": "active",
        "status": "CLOSED",
    })

    assert blocked["blocked"] is True
    assert not store.ledger_path("opportunity").exists()
    assert continuing["written"] is True
    assert terminal["written"] is True
    assert execution["written"] is True
    lifecycle_rows = [json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()]
    assert [row["record_id"] for row in lifecycle_rows] == [
        "lifecycle:active:opened", "lifecycle:active:terminal",
    ]


def test_v3_does_not_start_new_order_or_open_lifecycle_at_emergency(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.925)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")

    intent = store.append("order_intent", {
        "record_id": "intent:new:submitted", "episode_id": "new",
        "status": "SUBMITTED",
    })
    opened = store.append("lifecycle", {
        "record_id": "lifecycle:new:opened", "episode_id": "new",
        "outcome_state": "CENSORED",
    })
    speculative_execution = store.append("execution", {
        "record_id": "execution:new:hypothetical", "episode_id": "new",
        "execution_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
    })

    assert intent["blocked"] is True
    assert opened["blocked"] is True
    assert speculative_execution["blocked"] is True


def test_corrupt_lifecycle_index_fails_optional_closed_but_terminal_stays_open(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.ledger_path("lifecycle").write_text('{"episode_id":"active"}', encoding="utf-8")
    _fraction(monkeypatch, 0.925)

    optional = store.append("decision", {
        "record_id": "decision:active:2", "episode_id": "active",
    })
    terminal = store.append("execution", {
        "record_id": "execution:active:paper-close", "episode_id": "active",
        "close_ts": 10,
    })

    assert optional["blocked"] is True
    assert terminal["written"] is True


def test_existing_content_addressed_segment_remains_idempotently_readable(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    kwargs = dict(source="tape", symbol="BTC", timeframe="1s", start_ts=1, end_ts=2,
                  rows=({"ts": 1, "bid": 10},))
    _fraction(monkeypatch, 0.50)
    first = store.put_market_segment(**kwargs)
    _fraction(monkeypatch, 0.925)
    duplicate = store.put_market_segment(**kwargs)
    blocked = store.put_market_segment(**{**kwargs, "end_ts": 3})

    assert duplicate["sha256"] == first["sha256"]
    assert blocked["blocked"] is True
