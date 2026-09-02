import json
import os
import threading
from pathlib import Path

import collector_storage
import research_v3_store
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
    assert terminal["deferred"] is True
    assert terminal["reason"] == "MANDATORY_ROW_DURABLY_DEFERRED_PENDING_IDEMPOTENCY_BOOTSTRAP"


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


def test_terminal_lifecycle_segment_can_close_under_emergency(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.925)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    result = store.put_market_segment(
        source="terminal-path", symbol="BTC", timeframe="1s",
        start_ts=1, end_ts=2, rows=({"ts": 1, "bid": 10},),
        lifecycle_existing=True,
    )
    assert result.get("blocked") is not True
    assert len(result["sha256"]) == 64


def test_emergency_membership_never_opens_large_lifecycle_ledger(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    _fraction(monkeypatch, 0.50)
    assert store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })["written"] is True

    # Make the ledger large and stale without allocating/writing 1 GiB. The
    # emergency check must use stat + bounded receipts and fail closed.
    lifecycle = store.ledger_path("lifecycle")
    with lifecycle.open("ab") as handle:
        handle.truncate(1024 * 1024 * 1024)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.resolve() == lifecycle.resolve():
            raise AssertionError("emergency admission opened lifecycle ledger")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    _fraction(monkeypatch, 0.925)
    result = store.append("decision", {
        "record_id": "decision:active:large", "episode_id": "active",
    })
    assert result["blocked"] is True


def test_membership_receipt_survives_store_restart(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    first = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    first.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })

    _fraction(monkeypatch, 0.925)
    restarted = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    assert restarted.append("decision", {
        "record_id": "decision:active:restart", "episode_id": "active",
    })["written"] is True
    assert restarted.append("decision", {
        "record_id": "decision:new:restart", "episode_id": "new",
    })["blocked"] is True


def test_missing_corrupt_and_stale_membership_fail_optional_closed(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    receipt = store._lifecycle_membership_dir / "current.json"
    original = receipt.read_bytes()
    _fraction(monkeypatch, 0.925)

    receipt.unlink()
    assert store.append("decision", {
        "record_id": "decision:active:missing", "episode_id": "active",
    })["blocked"] is True
    receipt.write_bytes(b"{broken")
    assert store.append("decision", {
        "record_id": "decision:active:corrupt", "episode_id": "active",
    })["blocked"] is True
    receipt.write_bytes(original)
    with store.ledger_path("lifecycle").open("ab") as handle:
        handle.write(b" ")
        handle.flush()
    assert store.append("decision", {
        "record_id": "decision:active:stale", "episode_id": "active",
    })["blocked"] is True
    assert store.append("execution", {
        "record_id": "execution:active:reconciliation", "episode_id": "active",
    })["written"] is True


def test_receipt_publish_failure_is_restart_safe_and_fails_closed(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:crashed:opened", "episode_id": "crashed",
        "outcome_state": "CENSORED",
    })
    _fraction(monkeypatch, 0.925)
    original_publish = store._atomic_json_receipt
    failed = {"value": False}

    def fail_binding(path, payload):
        if path.name == "current.json" and not failed["value"]:
            failed["value"] = True
            raise OSError("simulated crash before generation publication")
        return original_publish(path, payload)

    monkeypatch.setattr(store, "_atomic_json_receipt", fail_binding)
    try:
        store.append("lifecycle", {
            "record_id": "lifecycle:crashed:update", "episode_id": "crashed",
            "outcome_state": "CENSORED",
        })
    except OSError:
        pass
    else:
        raise AssertionError("simulated receipt failure did not propagate")

    restarted = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    replay = restarted.append("lifecycle", {
        "record_id": "lifecycle:crashed:update", "episode_id": "crashed",
        "outcome_state": "CENSORED",
    })
    assert replay["duplicate"] is True
    assert replay["idempotency_receipt_repaired"] is True
    assert restarted.append("decision", {
        "record_id": "decision:crashed:after-restart", "episode_id": "crashed",
    })["written"] is True


def test_concurrent_lifecycle_append_and_admission_remain_fail_closed(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    _fraction(monkeypatch, 0.925)
    barrier = threading.Barrier(2)
    results = []

    def lifecycle_writer():
        barrier.wait()
        results.append(store.append("lifecycle", {
            "record_id": "lifecycle:active:update", "episode_id": "active",
            "outcome_state": "CENSORED",
        }))

    thread = threading.Thread(target=lifecycle_writer)
    thread.start()
    barrier.wait()
    decision = store.append("decision", {
        "record_id": "decision:active:concurrent", "episode_id": "active",
    })
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert results[0].get("blocked") is not True
    assert decision.get("written") is True or decision.get("blocked") is True
    assert store.append("decision", {
        "record_id": "decision:active:after", "episode_id": "active",
    })["written"] is True


def test_emergency_cold_cache_never_scans_gigabyte_target_ledgers(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    decision_path = store.ledger_path("decision")
    execution_path = store.ledger_path("execution")
    for path in (decision_path, execution_path):
        with path.open("wb") as handle:
            handle.truncate(1024 * 1024 * 1024)
    research_v3_store._id_cache.clear()
    original_load = V3EvidenceStore._load_ids

    def reject_full_scan(path):
        if path in {decision_path, execution_path}:
            raise AssertionError("emergency path performed full target-ledger scan")
        return original_load(path)

    monkeypatch.setattr(V3EvidenceStore, "_load_ids", staticmethod(reject_full_scan))
    _fraction(monkeypatch, 0.925)
    optional = store.append("decision", {
        "record_id": "decision:active:cold", "episode_id": "active",
    })
    mandatory = store.append("execution", {
        "record_id": "execution:active:reconciliation", "episode_id": "active",
    })
    assert optional["blocked"] is True
    assert optional["reason"] == "EMERGENCY_IDEMPOTENCY_INDEX_INCOMPLETE"
    assert mandatory["deferred"] is True
    assert execution_path.stat().st_size == 1024 * 1024 * 1024


def test_truncate_regrow_cannot_revive_old_membership_generation(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:old:opened", "episode_id": "old",
        "outcome_state": "CENSORED",
    })
    lifecycle = store.ledger_path("lifecycle")
    prior_size = lifecycle.stat().st_size
    with lifecycle.open("wb") as handle:
        handle.write(b"x" * (prior_size + 1024))
        handle.flush()
    _fraction(monkeypatch, 0.925)
    # Mandatory evidence for another episode refreshes the generation. The old
    # marker must not become valid merely because the same inode grew larger.
    deferred = store.append("lifecycle", {
        "record_id": "lifecycle:new:terminal", "episode_id": "new",
        "terminal": True, "outcome_state": "NO_FILL",
    })
    assert deferred["deferred"] is True
    assert store.append("decision", {
        "record_id": "decision:old:after-regrow", "episode_id": "old",
    })["blocked"] is True


def test_epoch_and_config_rollover_invalidate_membership(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    first = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    first.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    _fraction(monkeypatch, 0.925)
    assert V3EvidenceStore(tmp_path, epoch_id="epoch-2").append("decision", {
        "record_id": "decision:active:new-epoch", "episode_id": "active",
    })["blocked"] is True

    changed = dict(research_v3_store._collection_provenance())
    changed["tile_config_signature"] = "changed-config-signature"
    monkeypatch.setattr(research_v3_store, "_provenance_cache", changed)
    assert V3EvidenceStore(tmp_path, epoch_id="epoch-1").append("decision", {
        "record_id": "decision:active:new-config", "episode_id": "active",
    })["blocked"] is True


def test_oversize_episode_id_cannot_grow_membership_receipts(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.925)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    oversized = "e" * 513
    store.append("lifecycle", {
        "record_id": "lifecycle:oversize:terminal", "episode_id": oversized,
        "terminal": True, "outcome_state": "NO_FILL",
    })
    assert store.append("decision", {
        "record_id": "decision:oversize", "episode_id": oversized,
    })["blocked"] is True
    markers = list(store._lifecycle_membership_dir.glob("*.json"))
    assert markers == []


def test_historical_upgrade_bootstrap_is_bounded_and_publishes_exact_completeness(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    path = store.ledger_path("decision")
    historical = [
        {"record_id": f"decision:historical:{index}", "episode_id": "active"}
        for index in range(3)
    ]
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in historical), "utf-8")

    first = store.advance_emergency_idempotency_bootstrap("decision", max_bytes=1)
    assert first["complete"] is False
    assert 0 < first["bytes_indexed"] < path.stat().st_size
    _fraction(monkeypatch, 0.925)
    assert store.append("decision", {
        "record_id": "decision:new:while-incomplete", "episode_id": "active",
    })["blocked"] is True
    # The already indexed historical ID is boundedly verified and cannot be
    # duplicated even while the overall generation is incomplete.
    duplicate = store.append("decision", {
        "record_id": "decision:historical:0", "episode_id": "active",
    })
    assert duplicate.get("reason") is None, duplicate
    assert duplicate["duplicate"] is True

    _fraction(monkeypatch, 0.50)
    while not store.advance_emergency_idempotency_bootstrap("decision", max_bytes=64)["complete"]:
        pass
    prior = path.stat().st_size
    appended = store.append("decision", {
        "record_id": "decision:after-bootstrap", "episode_id": "active",
    })
    assert appended["written"] is True
    assert path.stat().st_size > prior
    assert store._complete_generation("decision", research_v3_store._path_signature(path)) is True

    _fraction(monkeypatch, 0.925)
    assert store.append("decision", {
        "record_id": "decision:emergency-after-bootstrap", "episode_id": "active",
    })["written"] is True


def test_historical_completeness_rejects_replacement_and_truncation(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:active:opened", "episode_id": "active",
        "outcome_state": "CENSORED",
    })
    store.append("decision", {
        "record_id": "decision:seed", "episode_id": "active",
    })
    decision = store.ledger_path("decision")
    original = decision.read_bytes()
    replacement = decision.with_suffix(".replacement")
    replacement.write_bytes(original)
    os.replace(replacement, decision)
    _fraction(monkeypatch, 0.925)
    assert store.append("decision", {
        "record_id": "decision:after-replace", "episode_id": "active",
    })["blocked"] is True

    _fraction(monkeypatch, 0.50)
    while not store.advance_emergency_idempotency_bootstrap("decision")["complete"]:
        pass
    with decision.open("r+b") as handle:
        handle.truncate(max(1, len(original) // 2))
    _fraction(monkeypatch, 0.925)
    assert store.append("decision", {
        "record_id": "decision:after-truncate", "episode_id": "active",
    })["blocked"] is True


def test_incomplete_bootstrap_anchor_detects_truncate_regrow_past_cursor(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    path = store.ledger_path("decision")
    rows = [
        json.dumps({"record_id": f"decision:old:{index}", "episode_id": "active"}, separators=(",", ":")) + "\n"
        for index in range(4)
    ]
    path.write_text("".join(rows), "utf-8")
    first = store.advance_emergency_idempotency_bootstrap("decision", max_bytes=1)
    assert first["complete"] is False
    first_cursor = first["cursor"]

    replacement = rows[0].replace("decision:old:0", "decision:new:0") + "".join(rows[1:]) + rows[-1]
    path.write_text(replacement, "utf-8")
    assert path.stat().st_size > first_cursor
    resumed = store.advance_emergency_idempotency_bootstrap("decision", max_bytes=1)
    # Invalidated cursor anchor forces a restart at byte zero; it cannot trust
    # the old prefix merely because the same inode regrew beyond the cursor.
    assert resumed["cursor"] == first_cursor


def test_mandatory_row_defers_until_historical_index_complete_then_appends_once(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    execution = store.ledger_path("execution")
    execution.write_text(json.dumps({
        "record_id": "execution:historical", "episode_id": "active",
    }) + "\n", "utf-8")
    _fraction(monkeypatch, 0.925)
    row = {
        "record_id": "execution:active:reconciliation", "episode_id": "active",
    }
    deferred = store.append("execution", row)
    assert deferred["deferred"] is True
    before = execution.stat().st_size
    assert store.append("execution", row)["deferred"] is True
    assert execution.stat().st_size == before

    _fraction(monkeypatch, 0.50)
    while not store.advance_emergency_idempotency_bootstrap("execution")["complete"]:
        pass
    _fraction(monkeypatch, 0.925)
    resumed = store.append("execution", row)
    assert resumed["written"] is True and resumed["resumed_deferred"] is True
    after = execution.stat().st_size
    duplicate = store.append("execution", row)
    assert duplicate["duplicate"] is True
    assert execution.stat().st_size == after


def test_new_record_and_completeness_receipts_bind_active_generation_path(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    result = store.append("decision", {
        "record_id": "decision:generation-bound", "episode_id": "active",
    })
    assert result["written"] is True
    receipt = json.loads(store._record_receipt_path(
        "decision", "decision:generation-bound",
    ).read_text("utf-8"))
    expected = store._active_ledger_generation("decision")
    assert receipt["ledger_generation"] == expected
    while not store.advance_emergency_idempotency_bootstrap("decision")["complete"]:
        pass
    complete = json.loads(store._completeness_path("decision").read_text("utf-8"))
    assert complete["ledger_generation"] == expected


def test_legacy_generationless_receipt_remains_active_only_compatible(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("decision", {"record_id": "decision:legacy", "episode_id": "active"})
    while not store.advance_emergency_idempotency_bootstrap("decision")["complete"]:
        pass
    path = store._completeness_path("decision")
    receipt = json.loads(path.read_text("utf-8"))
    receipt.pop("ledger_generation")
    store._atomic_json_receipt(path, receipt)
    signature = research_v3_store._path_signature(store.ledger_path("decision"))
    assert store._complete_generation("decision", signature) is True
    receipt["ledger_generation"] = {
        **store._active_ledger_generation("decision"), "state": "SEALED", "generation": 1,
    }
    store._atomic_json_receipt(path, receipt)
    assert store._complete_generation("decision", signature) is False


def test_lifecycle_membership_binding_is_generation_aware(tmp_path, monkeypatch):
    _mounted(monkeypatch, tmp_path)
    _fraction(monkeypatch, 0.50)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-1")
    store.append("lifecycle", {
        "record_id": "lifecycle:generation:terminal", "episode_id": "episode-generation",
        "terminal": True, "outcome_state": "NO_FILL",
    })
    current = json.loads((store._lifecycle_membership_dir / "current.json").read_text("utf-8"))
    assert current["ledger_generation"] == store._active_ledger_generation("lifecycle")
    assert store._episode_has_lifecycle_receipt("episode-generation") is True
    current["ledger_generation"] = {**current["ledger_generation"], "state": "SEALED", "generation": 1}
    store._atomic_json_receipt(store._lifecycle_membership_dir / "current.json", current)
    assert store._episode_has_lifecycle_receipt("episode-generation") is False
