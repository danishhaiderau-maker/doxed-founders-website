import copy
import inspect
import json
from pathlib import Path

import pytest

import bot
import lifecycle_pipeline
import research_v3_bridge as bridge
import research_v3_store
from research_v3_store import V3EvidenceStore


PROVENANCE = {
    "evidence_provenance_schema": "v3_collection_provenance_v1",
    "source_revision": "a" * 40,
    "deployed_revision": "a" * 40,
    "tile_config_signature": "b" * 64,
    "config_signature": "c" * 64,
}


@pytest.fixture(autouse=True)
def fixed_provenance(monkeypatch):
    monkeypatch.setattr(research_v3_store, "_provenance_cache", dict(PROVENANCE))


def sources():
    signal = {
        "trade_id": "paper-no-fill-1",
        "shared_ai_call_id": "scan-paper-no-fill-1",
        "created_ts_ts": 100.0,
        "raw_direction": "LONG",
        "final_direction": "LONG",
        "symbol": "tBTCF0:USTF0",
        "research_lane": "CONTINUOUS",
        "policy_id": "CONTINUOUS",
    }
    schedule = {
        "schema": "research_chase_schedule_v1",
        "authoritative": True,
        "source": "SHOWCASE_PAPER_PENDING_ORDER",
        "trade_id": signal["trade_id"],
        "requested_qty": 0.2,
        "intervals": [{
            "bucket_id": "b0", "start_ts": 100.0, "end_ts": 200.0,
            "limit_price": 99.0,
        }],
        "terminal_ts": 200,
        "terminal_ts_exact": 200.0,
        "terminal_reason": "TTL_EXPIRED",
    }
    order = {
        **signal,
        "status": "PENDING",
        "qty": 0.2,
        "requested_qty": 0.2,
        "cancel_confirmed": True,
        "cancel_confirmed_ts": 200.0,
        "cancel_confirmed_reason": "TTL_EXPIRED",
        "cancel_confirmed_filled_qty": 0.0,
        "research_chase_schedule": schedule,
        "chase_schedule_authoritative": True,
    }
    return signal, order


def write_no_fill(tmp_path, order=None, signal=None):
    base_signal, base_order = sources()
    return bridge.dual_write_terminal_paper_no_fill(
        order or base_order,
        signal or base_signal,
        epoch_id="epoch-c", data_dir=tmp_path,
        lifecycle_final=True, paper_cancel_confirmed=True,
    )


def v22_event(*, primary_outcome="ACCEPTED_UNFILLED", observation_status="FUNNEL_COMPLETE"):
    signal, order = sources()
    policy = bridge._paper_policy_identity("epoch-c", order, signal)
    return {
        "event_id": signal["trade_id"],
        "event_episode_id": "unused-when-shared-ai-call-is-present",
        "epoch_id": "epoch-c",
        "envelope": {
            "signal_ts": 100.0,
            "raw_direction": "LONG",
            "executed_direction": "LONG",
            "policy_signature": policy["policy_signature"],
            "policy_epoch_id": policy["policy_epoch_id"],
        },
        "event_episode": {"shared_ai_call_id": signal["shared_ai_call_id"]},
        "feature_snapshot_at_signal": {
            "symbol": signal["symbol"],
            "adx": 30,
        },
        "pre_signal_context": {"1m": {"bars": 1}},
        "decision_tree_snapshot": {"AI": {"result": "PASS"}},
        "primary_outcome": primary_outcome,
        "observation_status": observation_status,
        "exact_reason": "TTL_EXPIRED",
        "ranking_eligible": False,
        "research_lane": policy["paper_policy_spec"]["research_lane"],
        "policy_id": policy["paper_policy_spec"]["policy_id"],
        "policy_signature": policy["policy_signature"],
        "policy_epoch_id": policy["policy_epoch_id"],
        "research_execution_basis": {"qty": order["requested_qty"]},
        "research_chase_schedule": order["research_chase_schedule"],
        "entry_children": [],
        "replay_eligibility": {"eligible": True},
    }


def test_confirmed_paper_cancel_writes_one_exact_terminal_no_fill(tmp_path):
    receipt = write_no_fill(tmp_path)
    assert receipt["write"]["written"] is True
    rows = [
        json.loads(line)
        for line in V3EvidenceStore(tmp_path, epoch_id="epoch-c")
        .ledger_path("lifecycle").read_text("utf-8").splitlines()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"] == "lifecycle:paper-no-fill-1:terminal"
    assert row["terminal"] is True
    assert row["terminal_no_fill"] is True
    assert row["outcome_state"] == "NO_FILL"
    assert row["position_state"] == "NEVER_OPENED"
    assert row["open_quantity"] == 0.0
    assert len(row["schedule_sha256"]) == 64

    duplicate = write_no_fill(tmp_path)
    assert duplicate["write"]["duplicate"] is True
    assert len(V3EvidenceStore._load_ids(
        V3EvidenceStore(tmp_path, epoch_id="epoch-c").ledger_path("lifecycle")
    )) == 1


@pytest.mark.parametrize("mutation", [
    lambda order: order.update(cancel_confirmed=False),
    lambda order: order.update(cancel_confirmed_ts=None),
    lambda order: order.update(cancel_confirmed_reason="OTHER"),
    lambda order: order.update(bitfinex_live_entry=True),
    lambda order: order.update(partial_fill=True),
    lambda order: order.update(fill_price=99.0),
    lambda order: order.update(cancel_confirmed_filled_qty=-0.1),
    lambda order: order.update(cancel_confirmed_filled_qty=float("nan")),
    lambda order: order.update(cancel_confirmed_filled_qty="bad"),
    lambda order: order.update(cancel_confirmed_filled_qty=0.1),
])
def test_no_fill_writer_rejects_unproven_or_conflicting_state(tmp_path, mutation):
    signal, order = sources()
    mutation(order)
    assert write_no_fill(tmp_path, order, signal) is None


def test_same_terminal_id_with_changed_schedule_fails_closed(tmp_path):
    write_no_fill(tmp_path)
    signal, order = sources()
    order["research_chase_schedule"] = copy.deepcopy(order["research_chase_schedule"])
    order["research_chase_schedule"]["intervals"][0]["limit_price"] = 98.0
    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        write_no_fill(tmp_path, order, signal)


def test_terminal_no_fill_is_transfer_ready_before_two_hour_horizon(tmp_path):
    signal, order = sources()
    bridge.dual_write_terminal_paper_schedule(
        order, signal, epoch_id="epoch-c", data_dir=tmp_path,
        lifecycle_final=True,
    )
    write_no_fill(tmp_path, order, signal)
    for _attempt in range(2 * len(lifecycle_pipeline.LEDGER_NAMES)):
        result = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
            tmp_path, now=201.0,
        )
        if result["transfer_bundle_count"] == 1:
            break
    assert result["transfer_bundle_count"] == 1
    assert result["completion_appended_count"] == 0
    row = result["results"][0]
    assert row["classification"] == "NO_FILL"
    assert row["transfer_ready"] is True
    assert row["qualification_ready"] is False
    assert row["transfer_bundle"]["manifest"]["source_cleanup_authorized"] is False


def test_v22_dedupe_requires_same_no_fill_semantics(tmp_path):
    write_no_fill(tmp_path)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-c")
    actual = bridge._read_exact_terminal_record(
        store, "lifecycle:paper-no-fill-1:terminal",
    )
    bridge._assert_v22_terminal_dedupe(
        store, actual, primary_outcome="ACCEPTED_UNFILLED",
    )
    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        bridge._assert_v22_terminal_dedupe(
            store, actual, primary_outcome="ACCEPTED_FILLED",
        )
    source = inspect.getsource(bridge.dual_write_v22_record)
    assert "_assert_v22_terminal_dedupe" in source


def test_identical_filled_v22_terminal_replay_is_idempotent(tmp_path):
    event = v22_event(primary_outcome="ACCEPTED_FILLED")
    first = bridge.dual_write_v22_record(event, data_dir=tmp_path)
    second = bridge.dual_write_v22_record(event, data_dir=tmp_path)
    assert first["writes"][-1]["written"] is True
    assert second["writes"][-1]["duplicate"] is True
    rows = V3EvidenceStore(
        tmp_path, epoch_id="epoch-c",
    ).ledger_path("lifecycle").read_text("utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["outcome_state"] == "FULL_FILL"


@pytest.mark.parametrize("observation_status", [
    "FUNNEL_COMPLETE",
    "PAPER_POSITION_CLOSED",
])
def test_deployed_filled_or_closed_v22_row_replays_under_candidate(
    tmp_path, monkeypatch, observation_status,
):
    event = v22_event(
        primary_outcome="ACCEPTED_FILLED",
        observation_status=observation_status,
    )
    deployed_append = V3EvidenceStore.append

    def append_without_candidate_terminal_fields(store, ledger, row):
        material = dict(row)
        if (
            ledger == "lifecycle"
            and str(material.get("record_id") or "").endswith(":terminal")
        ):
            for field in ("terminal_ts", "requested_qty", "schedule_sha256"):
                material.pop(field, None)
        return deployed_append(store, ledger, material)

    monkeypatch.setattr(V3EvidenceStore, "append", append_without_candidate_terminal_fields)
    first = bridge.dual_write_v22_record(event, data_dir=tmp_path)
    monkeypatch.setattr(V3EvidenceStore, "append", deployed_append)
    replay = bridge.dual_write_v22_record(event, data_dir=tmp_path)

    assert first["writes"][-1]["written"] is True
    assert replay["writes"][-1]["duplicate"] is True
    row = bridge._read_exact_terminal_record(
        V3EvidenceStore(tmp_path, epoch_id="epoch-c"),
        "lifecycle:paper-no-fill-1:terminal",
    )
    assert row["outcome_state"] == "FULL_FILL"
    assert row["observation_status"] == observation_status
    assert "terminal_ts" not in row
    assert "requested_qty" not in row
    assert "schedule_sha256" not in row


def test_deployed_no_fill_row_does_not_receive_legacy_field_forgiveness(
    tmp_path, monkeypatch,
):
    event = v22_event()
    deployed_append = V3EvidenceStore.append

    def append_without_candidate_terminal_fields(store, ledger, row):
        material = dict(row)
        if (
            ledger == "lifecycle"
            and str(material.get("record_id") or "").endswith(":terminal")
        ):
            for field in ("terminal_ts", "requested_qty", "schedule_sha256"):
                material.pop(field, None)
        return deployed_append(store, ledger, material)

    monkeypatch.setattr(V3EvidenceStore, "append", append_without_candidate_terminal_fields)
    bridge.dual_write_v22_record(event, data_dir=tmp_path)
    monkeypatch.setattr(V3EvidenceStore, "append", deployed_append)

    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        bridge.dual_write_v22_record(event, data_dir=tmp_path)


def test_explicit_no_fill_then_matching_v22_is_one_terminal(tmp_path):
    write_no_fill(tmp_path)
    receipt = bridge.dual_write_v22_record(v22_event(), data_dir=tmp_path)
    assert receipt["writes"][-1]["duplicate"] is True
    rows = V3EvidenceStore(
        tmp_path, epoch_id="epoch-c",
    ).ledger_path("lifecycle").read_text("utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["observation_status"] == "PAPER_ORDER_CLOSED_NO_FILL"


def test_matching_v22_then_explicit_no_fill_is_one_terminal(tmp_path):
    receipt = bridge.dual_write_v22_record(v22_event(), data_dir=tmp_path)
    assert receipt["writes"][-1]["written"] is True
    explicit = write_no_fill(tmp_path)
    assert explicit["write"]["duplicate"] is True
    rows = V3EvidenceStore(
        tmp_path, epoch_id="epoch-c",
    ).ledger_path("lifecycle").read_text("utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["observation_status"] == "FUNNEL_COMPLETE"


@pytest.mark.parametrize(("actual_id", "expected_id"), [
    (None, None),
    ("", "scan-paper-no-fill-1"),
    ("scan-paper-no-fill-1", ""),
    ("   ", "   "),
])
def test_cross_writer_no_fill_requires_explicit_shared_identity(
    tmp_path, actual_id, expected_id,
):
    write_no_fill(tmp_path)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-c")
    actual = bridge._read_exact_terminal_record(
        store, "lifecycle:paper-no-fill-1:terminal",
    )
    expected = copy.deepcopy(actual)
    actual["shared_ai_call_id"] = actual_id
    expected["shared_ai_call_id"] = expected_id

    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        bridge._assert_cross_writer_terminal_no_fill(actual, expected)


def test_explicit_no_fill_then_v22_schedule_conflict_fails_closed(tmp_path):
    write_no_fill(tmp_path)
    event = v22_event()
    event["research_chase_schedule"] = copy.deepcopy(
        event["research_chase_schedule"]
    )
    event["research_chase_schedule"]["terminal_ts_exact"] = 201.0
    event["research_chase_schedule"]["terminal_ts"] = 201
    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        bridge.dual_write_v22_record(event, data_dir=tmp_path)


def test_explicit_no_fill_then_v22_missing_schedule_fails_closed(tmp_path):
    write_no_fill(tmp_path)
    event = v22_event()
    event.pop("research_chase_schedule")
    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        bridge.dual_write_v22_record(event, data_dir=tmp_path)


def test_v22_then_explicit_no_fill_schedule_conflict_fails_closed(tmp_path):
    event = v22_event()
    event["research_chase_schedule"] = copy.deepcopy(
        event["research_chase_schedule"]
    )
    event["research_chase_schedule"]["requested_qty"] = 0.3
    event["research_execution_basis"] = {"qty": 0.3}
    bridge.dual_write_v22_record(event, data_dir=tmp_path)
    with pytest.raises(ValueError, match="V3_TERMINAL_OUTCOME_ID_CONFLICT"):
        write_no_fill(tmp_path)


def test_bot_hooks_preserve_original_venue_fact_and_confirm_revalidation():
    cancel = inspect.getsource(bot._cancel_pending_order_confirmed)
    assert "if not oid and evidence_lifecycle_final is True:" in cancel
    assert "paper_cancel_confirmed=True" in cancel
    assert cancel.index("dual_write_terminal_paper_schedule(") < cancel.index(
        "dual_write_terminal_paper_no_fill("
    )

    source = Path(bot.__file__).read_text("utf-8")
    pending = source[
        source.index("def process_pending_orders():"):
        source.index("\ndef fill_order(order):")
    ]
    cancelled = pending[pending.index("for order, fill_signal, reason, fill_claim in cancelled_at_fill:"):]
    assert "_cancel_pending_order_confirmed(" in cancelled
    assert "_record_expired_order(order, reason)" not in cancelled
    assert "close_research_order_schedule" not in cancelled

    temporary = inspect.getsource(bot._pull_order_to_virtual_chase)
    assert "evidence_lifecycle_final=False" in temporary
    chase_gate = inspect.getsource(bot._cancel_pending_for_chase_gate)
    assert "evidence_lifecycle_final=False" in chase_gate
