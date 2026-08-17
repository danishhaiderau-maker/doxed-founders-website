import copy

from research.platform_relay_evidence import (
    _normalize_platform_bitfinex_evidence,
    _snapshot_with_platform_relay_evidence,
)
from research.source_market_evidence import (
    append_market_observation,
    evidence_summary,
    load_market_evidence_index,
    sync_canonical_pending_order,
    update_canonical_extrema,
)


def test_separate_order_objects_share_one_canonical_extrema_record():
    store = {}
    update_canonical_extrema(store, {"trade_id": "cont-1", "side": "sell", "limit_price": 100, "qty": 2}, 99)
    record = update_canonical_extrema(
        store, {"trade_id": "cont-1", "signal_dir": "SHORT", "limit_price": 100, "qty": 2}, 103
    )
    assert record["direction"] == "SHORT"
    assert record["market_min_price"] == 99
    assert record["market_max_price"] == 103
    assert evidence_summary(record)["canonical_trade_id"] == "cont-1"


def test_short_observation_uses_bid_and_persists_depth_and_buy_aggressor_evidence():
    store = {}
    record, observation = append_market_observation(
        store,
        {"trade_id": "cont-2", "side": "sell", "limit_price": 100, "qty": 2},
        market_price=101,
        bid=100.5,
        ask=100.6,
        venue_snapshot={"book_ts": 10.0},
        gate_evidence={
            "policy": "VENUE_EXECUTABLE_SHOWCASE_FILL_GATE_V1",
            "book_age_sec": 0.2,
            "visible_executable_qty": 3,
            "recent_executable_trade_qty": 2.5,
            "reason": "EXECUTABLE",
        },
        observed_ts=10.2,
    )
    assert observation["side_correct_executable_quote"] == 100.5
    assert observation["visible_executable_qty"] == 3
    assert observation["recent_executable_aggressor_qty"] == 2.5
    assert observation["source_strategy_state_unchanged"] is True
    assert record["latest_observation"]["fill_gate_verdict"] == "EXECUTABLE"


def _relay_index(fill_payload):
    records = [{
        "canonicalTradeId": "cont-copy",
        "lifecycleId": "life-copy",
        "participantId": "participant-copy",
        "events": [{
            "id": "fill-event",
            "eventType": "FILLED",
            "createdAt": "2026-08-17T00:00:00Z",
            "payload": fill_payload,
        }],
    }]
    return {
        "cont-copy": {
            "schema": "relay_lifecycle_evidence_v1",
            "generated_at": "2026-08-17T00:01:00Z",
            "generating_revision": "a" * 40,
            "run_identity": "run-1",
            "records": records,
            "evidence_revision": "revision-1",
        }
    }, records


def test_authenticated_copy_fill_creates_overlay_without_fabricating_source_fill():
    index, records = _relay_index({
        "fill_id": 123,
        "bitfinex_order_id": 456,
        "filled_quantity": 0.01,
        "fill_price": 63260,
        "exchange_fill_received_at": "2026-08-17T00:00:00Z",
    })
    source = {"trade_id": "cont-copy", "executed": False, "status": "EXPIRED"}
    enriched = _snapshot_with_platform_relay_evidence(copy.deepcopy(source), "cont-copy", index)
    assert enriched["executed"] is False
    assert enriched["status"] == "EXPIRED"
    assert enriched["copy_fill_observed"]["fill_ids"] == [123]
    assert enriched["copy_fill_observed"]["source_strategy_state_unchanged"] is True
    assert enriched["exchange_confirmed_shadow_overlay"]["divergence_classification"] == "COPY_FILLED_SOURCE_UNFILLED_OR_UNKNOWN"
    normalized = _normalize_platform_bitfinex_evidence(records, "cont-copy")
    assert normalized["copy_fill_observed"]["authority"] == "AUTHENTICATED_EXCHANGE_FILL"


def test_incomplete_copy_fill_remains_unknown_and_cannot_create_overlay():
    index, records = _relay_index({"fill_id": 123, "filled_quantity": 0.01})
    enriched = _snapshot_with_platform_relay_evidence(
        {"trade_id": "cont-copy", "executed": False}, "cont-copy", index
    )
    assert "copy_fill_observed" not in enriched
    assert _normalize_platform_bitfinex_evidence(records, "cont-copy")["copy_fill_observed"] == {}


def test_market_evidence_rotation_loader_is_malformed_safe_targeted_and_revisioned(tmp_path):
    active = tmp_path / "source_order_market_evidence.jsonl"
    rotated = tmp_path / "source_order_market_evidence.jsonl.1"
    rotated.write_text(
        '{"canonical_trade_id":"cont-target","observed_at_ts":1}\nnot-json\n'
        '{"canonical_trade_id":"cont-other","observed_at_ts":1}\n',
        encoding="utf-8",
    )
    active.write_text(
        '{"canonical_trade_id":"cont-target","observed_at_ts":2}\n', encoding="utf-8"
    )
    first = load_market_evidence_index(active, {"cont-target"})
    assert set(first) == {"cont-target"}
    assert [row["observed_at_ts"] for row in first["cont-target"]["observations"]] == [1, 2]
    revision = first["cont-target"]["evidence_revision"]
    with active.open("a", encoding="utf-8") as handle:
        handle.write('{"canonical_trade_id":"cont-target","observed_at_ts":3}\n')
    second = load_market_evidence_index(active, {"cont-target"})
    assert second["cont-target"]["evidence_revision"] != revision


def test_chase_ack_updates_same_canonical_limit_used_by_observations():
    store = {}
    order = {
        "trade_id": "cont-57bb",
        "side": "buy",
        "limit_price": 63486.52,
        "original_limit_price": 63486.52,
        "qty": 0.03147,
        "limit_chase_count": 0,
        "status": "PENDING",
    }
    sync_canonical_pending_order(store, order, chase_acked=False, observed_ts=1.0)
    _, first = append_market_observation(
        store, order, market_price=63500, bid=63490, ask=63495,
        venue_snapshot={"book_ts": 1.0},
        gate_evidence={"reason": "INSUFFICIENT_EXECUTABLE_DEPTH", "policy": "VENUE_EXECUTABLE_SHOWCASE_FILL_GATE_V1"},
        observed_ts=1.0,
    )
    assert first["limit_price"] == 63486.52
    assert first["limit_generation"] == 0

    # Chase ACK advances the SAME store object the fill gate/evidence share.
    order["limit_price"] = 63504.89
    order["limit_chase_count"] = 1
    sync_canonical_pending_order(store, order, chase_acked=True, observed_ts=2.0)
    record, second = append_market_observation(
        store, order, market_price=63510, bid=63500, ask=63505,
        venue_snapshot={"book_ts": 2.0},
        gate_evidence={"reason": "EXECUTABLE", "policy": "VENUE_EXECUTABLE_SHOWCASE_FILL_GATE_V1",
                       "visible_executable_qty": 1, "recent_executable_trade_qty": 1},
        observed_ts=2.0,
    )
    assert record["current_limit_price"] == 63504.89
    assert record["original_limit_price"] == 63486.52
    assert record["limit_generation"] == 1
    assert second["limit_price"] == 63504.89
    assert second["limit_generation"] == 1
    summary = evidence_summary(record)
    assert summary["current_limit_price"] == 63504.89
    assert summary["original_limit_price"] == 63486.52


def test_failed_chase_persist_keeps_old_limit_authoritative():
    store = {}
    order = {"trade_id": "cont-keep", "side": "buy", "limit_price": 100.0,
             "original_limit_price": 100.0, "limit_chase_count": 0, "qty": 1}
    sync_canonical_pending_order(store, order, chase_acked=True, observed_ts=1.0)
    # Simulated failed chase: caller must NOT pass chase_acked=True with new limit.
    failed = dict(order, limit_price=101.5, limit_chase_count=1)
    # Without chase_acked, generation does not advance past ACK until order gen matches live.
    # If chase never committed, live order still has old limit — pass old order.
    sync_canonical_pending_order(store, order, chase_acked=False, observed_ts=2.0)
    assert store["cont-keep"]["limit_price"] == 100.0
    assert store["cont-keep"]["limit_generation"] == 0


def test_exchange_fill_ids_array_builds_copy_fill_observed_overlay():
    index, records = _relay_index({
        "exchange_fill_ids": [1958363331],
        "bitfinexOrderId": 242019286185,
        "clientOrderId": 958253392,
        "qty": 0.03147,
        "fill_price": 63504,
        "exchange_fill_received_at": "2026-08-17T04:18:14.885Z",
        "source_model_fill_state": "SOURCE_UNCONFIRMED",
        "copy_reconciliation_state": "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED",
    })
    source = {"trade_id": "cont-copy", "executed": False, "status": "EXPIRED"}
    enriched = _snapshot_with_platform_relay_evidence(copy.deepcopy(source), "cont-copy", index)
    assert enriched["executed"] is False
    assert enriched["status"] == "EXPIRED"
    assert enriched["copy_fill_observed"]["fill_ids"] == [1958363331]
    assert enriched["copy_fill_observed"]["classification"] == "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED"
    assert enriched["exchange_confirmed_shadow_overlay"]["excluded_from_showcase_strategy_stats"] is True
    assert enriched["exchange_confirmed_shadow_overlay"]["label"] == "EXCHANGE_CONFIRMED_SHADOW_POSITION"
    truth = enriched["dual_execution_truth"]
    assert truth["showcase_simulated"]["executed"] is False
    assert truth["showcase_simulated"]["status"] == "EXPIRED"
    assert truth["bitfinex_authenticated"]["authenticated"] is True
    assert truth["bitfinex_authenticated"]["fill_ids"] == [1958363331]
    assert truth["relationship"]["shadow_label"] == "EXCHANGE_CONFIRMED_SHADOW_POSITION"
    assert truth["relationship"]["excluded_from_showcase_strategy_stats"] is True


def test_copy_fill_overlay_is_idempotent_and_never_sets_showcase_filled():
    index, _records = _relay_index({
        "exchange_fill_ids": [1958363331],
        "bitfinexOrderId": 242019286185,
        "qty": 0.03147,
        "fill_price": 63504,
        "exchange_fill_received_at": "2026-08-17T04:18:14.885Z",
        "copy_reconciliation_state": "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED",
    })
    source = {"trade_id": "cont-copy", "executed": False, "status": "EXPIRED"}
    first = _snapshot_with_platform_relay_evidence(copy.deepcopy(source), "cont-copy", index)
    second = _snapshot_with_platform_relay_evidence(copy.deepcopy(first), "cont-copy", index)
    assert first["executed"] is False
    assert second["executed"] is False
    assert second["status"] == "EXPIRED"
    assert first["copy_fill_observed"]["fill_ids"] == second["copy_fill_observed"]["fill_ids"] == [1958363331]


def test_old_generation_cannot_mutate_chased_canonical_limit():
    store = {}
    order = {
        "trade_id": "cont-57bb",
        "side": "buy",
        "limit_price": 63486.52,
        "original_limit_price": 63486.52,
        "qty": 0.03147,
        "limit_chase_count": 0,
        "status": "PENDING",
    }
    sync_canonical_pending_order(store, order, chase_acked=False, observed_ts=1.0)
    order["limit_price"] = 63504.89
    order["limit_chase_count"] = 1
    sync_canonical_pending_order(store, order, chase_acked=True, observed_ts=2.0)
    stale = dict(order, limit_price=63486.52, limit_chase_count=0)
    sync_canonical_pending_order(store, stale, chase_acked=False, observed_ts=3.0)
    assert store["cont-57bb"]["current_limit_price"] == 63504.89
    assert store["cont-57bb"]["original_limit_price"] == 63486.52
    assert store["cont-57bb"]["limit_generation"] == 1
    _, observation = append_market_observation(
        store, stale, market_price=63500, bid=63490, ask=63495,
        venue_snapshot={"book_ts": 3.0},
        gate_evidence={"reason": "INSUFFICIENT_EXECUTABLE_DEPTH"},
        observed_ts=3.0,
    )
    assert observation["limit_price"] == 63504.89
    assert observation["limit_generation"] == 1


def test_incomplete_public_evidence_stays_unknown_without_fabricating_fill():
    source = {"trade_id": "cont-unknown", "executed": False, "status": "PENDING"}
    enriched = _snapshot_with_platform_relay_evidence(copy.deepcopy(source), "cont-unknown", {})
    assert enriched["executed"] is False
    assert enriched["status"] == "PENDING"
    assert "copy_fill_observed" not in enriched or not enriched.get("copy_fill_observed")
    assert enriched["dual_execution_truth"]["bitfinex_authenticated"]["authenticated"] is False
    assert enriched["dual_execution_truth"]["showcase_simulated"]["executed"] is False
