import json
import math
from pathlib import Path

import pytest

import relay_event_outbox as outbox_module
from relay_event_outbox import RelayEventOutbox


def _lifecycle_payload(row_count: int, note_size: int = 870) -> dict:
    pending = []
    for index in range(row_count):
        trade_id = f"paper-family-{index:06d}"
        pending.append({
            "event_id": f"{trade_id}:LIMIT_UPDATED:{index}:2026-09-21T00:00:00Z",
            "trade_id": trade_id,
            "event_type": "LIMIT_UPDATED",
            "event_seq": index,
            "payload_sha256": f"{index:064x}"[-64:],
            "payload": {
                "event": "LIMIT_UPDATED",
                "trade_id": trade_id,
                "limit_price": 115432.125 + index / 100,
                "note": ("position lifecycle Δ 雪 " + str(index) + " ") * (note_size // 24),
            },
            "attempts": index % 10086,
            "next_attempt_at_unix": 0.0,
        })
    return {
        "schema": "paper_lifecycle_v1",
        "generation": 146401,
        "paper_only": True,
        "live_armed": False,
        "positions": [],
        "pending_orders": [],
        "awaiting_signals": [],
        "transition_wal": None,
        "relay_events": {
            "schema": RelayEventOutbox.SCHEMA,
            "saved_at_unix": 1789950000.0,
            "pending": pending,
            "acks": [],
            "sequence_highwater": {row["trade_id"]: row["event_seq"] for row in pending},
        },
    }


@pytest.mark.parametrize("value", [
    {
        "ascii": "plain",
        "unicode": "Δ snow 雪 emoji 🚦",
        "negative_zero": -0.0,
        "positive_infinity": math.inf,
        "negative_infinity": -math.inf,
        "not_a_number": math.nan,
    },
    _lifecycle_payload(316),
])
def test_atomic_write_bytes_match_existing_json_dump_semantics(tmp_path, value):
    path = tmp_path / "paper_lifecycle_v1.json"
    expected = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if value.get("schema") == "paper_lifecycle_v1":
        assert 500_000 <= len(expected) <= 600_000

    RelayEventOutbox(path)._atomic_write(value)

    assert path.read_bytes() == expected
    assert not list(tmp_path.glob(".paper_lifecycle_v1.json.*.tmp"))


def test_atomic_write_large_generation_preserves_every_pending_and_highwater_row(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    value = _lifecycle_payload(1520, note_size=1400)
    expected = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert 3_500_000 <= len(expected) <= 5_000_000

    RelayEventOutbox(path)._atomic_write(value)

    actual = json.loads(path.read_text(encoding="utf-8"))
    assert path.read_bytes() == expected
    assert len(actual["relay_events"]["pending"]) == 1520
    assert len(actual["relay_events"]["sequence_highwater"]) == 1520
    assert actual["relay_events"]["pending"][-1]["attempts"] == 1519


def test_replace_failure_cleans_temporary_and_preserves_prior_valid_generation(tmp_path, monkeypatch):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    prior = _lifecycle_payload(3)
    box._atomic_write(prior)
    prior_bytes = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("replace interrupted")

    monkeypatch.setattr(outbox_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace interrupted"):
        box._atomic_write(_lifecycle_payload(4))

    assert path.read_bytes() == prior_bytes
    assert json.loads(path.read_text(encoding="utf-8")) == prior
    assert not list(tmp_path.glob(".paper_lifecycle_v1.json.*.tmp"))


def test_serialization_failure_creates_no_temporary_and_preserves_prior_file(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    prior = _lifecycle_payload(2)
    box._atomic_write(prior)
    prior_bytes = path.read_bytes()

    with pytest.raises(TypeError):
        box._atomic_write({"unsupported": Path("not-json")})

    assert path.read_bytes() == prior_bytes
    assert not list(tmp_path.glob(".paper_lifecycle_v1.json.*.tmp"))


def test_prepared_wal_still_recovers_exactly_once_after_restart(tmp_path):
    path = tmp_path / "paper_lifecycle_v1.json"
    box = RelayEventOutbox(path)
    base = _lifecycle_payload(0)
    box._persist(state_payload=base)
    target = dict(base)
    target["pending_orders"] = [{"trade_id": "wal-trade", "status": "PENDING"}]
    record = box.prepare_transition(
        target,
        {"event": "ORDER_PLACED", "trade_id": "wal-trade", "ts": "now"},
    )

    restarted = RelayEventOutbox(path)
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert restarted.healthy is True
    assert [row["event_id"] for row in restarted.due()] == [record["event_id"]]
    assert committed["transition_wal"] is None
    assert committed["pending_orders"] == [{"trade_id": "wal-trade", "status": "PENDING"}]
