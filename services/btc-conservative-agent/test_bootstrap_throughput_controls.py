"""Bound throughput knobs without changing provenance or worker deadlines."""
import json

import pytest

import lifecycle_pipeline_runtime as runtime
import research_v3_store as store_module


@pytest.mark.parametrize("raw,records,byte_count", [
    (None, 64, 8 * 1024 * 1024), ("", 64, 8 * 1024 * 1024),
    ("garbage", 64, 8 * 1024 * 1024), ("nan", 64, 8 * 1024 * 1024),
    ("1.5", 64, 8 * 1024 * 1024), ("-10", 1, 1), ("0", 1, 1),
    ("128", 128, 128), ("999999999", 512, 32 * 1024 * 1024),
])
def test_knobs_default_and_hard_clamp(monkeypatch, raw, records, byte_count):
    for name in ("V3_BOOTSTRAP_RECORDS_PER_STEP", "V3_BOOTSTRAP_BYTES_PER_STEP"):
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
    assert store_module._bootstrap_records_per_step() == records
    assert store_module._bootstrap_bytes_per_step() == byte_count


def bootstrap_store(tmp_path, monkeypatch, *, rows):
    monkeypatch.setattr(store_module, "storage_blocks_new_nonessential_research", lambda *_: False)
    monkeypatch.setattr(store_module.V3EvidenceStore, "_emergency_wal_identity_available", lambda _: False)
    store = store_module.V3EvidenceStore(tmp_path, epoch_id="test-epoch")
    store.ledger_path("decision").write_text("".join(
        json.dumps({"record_id": f"record-{number}", "episode_id": "episode-1"}) + "\n"
        for number in range(rows)), encoding="utf-8")
    # Avoid timing the host disk: assert the number and bytes of actual
    # bootstrap publication calls, leaving durable checkpointing unchanged.
    published = []
    monkeypatch.setattr(store, "_publish_record_receipt", lambda *args, **kwargs: published.append(kwargs))
    return store, published


@pytest.mark.parametrize("configured,explicit,expected", [
    (None, None, 64), ("128", None, 128), ("999999", None, 512),
    ("128", 999999, 128), ("512", 16, 16),
])
def test_actual_bootstrap_respects_effective_record_budget(tmp_path, monkeypatch, configured, explicit, expected):
    monkeypatch.delenv("V3_BOOTSTRAP_BYTES_PER_STEP", raising=False)
    if configured is None:
        monkeypatch.delenv("V3_BOOTSTRAP_RECORDS_PER_STEP", raising=False)
    else:
        monkeypatch.setenv("V3_BOOTSTRAP_RECORDS_PER_STEP", configured)
    store, published = bootstrap_store(tmp_path, monkeypatch, rows=520)
    kwargs = {} if explicit is None else {"max_records": explicit}
    result = store.advance_emergency_idempotency_bootstrap("decision", **kwargs)
    assert result["complete"] is False
    assert result["records_indexed"] == len(published) == expected
    assert result["cursor"] == sum(len(item["payload"]) for item in published)
    checkpoint = json.loads(store._bootstrap_path("decision").read_text())
    assert checkpoint["cursor"] == result["cursor"]
    assert checkpoint["identity"] == store._identity_binding()


def test_small_byte_budget_only_allows_one_whole_row(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_BOOTSTRAP_RECORDS_PER_STEP", "512")
    monkeypatch.setenv("V3_BOOTSTRAP_BYTES_PER_STEP", "1")
    store, published = bootstrap_store(tmp_path, monkeypatch, rows=520)
    result = store.advance_emergency_idempotency_bootstrap("decision")
    assert result["records_indexed"] == len(published) == 1
    assert result["bytes_indexed"] == len(published[0]["payload"])
    assert result["bytes_indexed"] <= 1 + store_module._MAX_RECEIPT_ROW_BYTES


def test_worker_forwards_only_throughput_knobs_and_trusted_revision(monkeypatch):
    monkeypatch.setenv("V3_BOOTSTRAP_RECORDS_PER_STEP", "128")
    monkeypatch.setenv("V3_BOOTSTRAP_BYTES_PER_STEP", "8388608")
    for name in ("BOT_ADMIN_TOKEN", "DATABASE_URL", "BITFINEX_API_KEY", "SOURCE_GIT_REV",
                 "LIFECYCLE_WALL_TIMEOUT_SEC", "LIFECYCLE_CPU_LIMIT_SEC"):
        monkeypatch.setenv(name, "must-not-inherit")
    environment = runtime._minimal_worker_environment("a" * 40)
    assert environment["V3_BOOTSTRAP_RECORDS_PER_STEP"] == "128"
    assert environment["V3_BOOTSTRAP_BYTES_PER_STEP"] == "8388608"
    assert environment["SOURCE_GIT_REV"] == "a" * 40
    assert "must-not-inherit" not in environment.values()
    assert "BOT_ADMIN_TOKEN" not in environment
    assert "LIFECYCLE_WALL_TIMEOUT_SEC" not in environment

