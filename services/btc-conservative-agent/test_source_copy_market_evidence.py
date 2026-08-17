import copy

from research.platform_relay_evidence import (
    _normalize_platform_bitfinex_evidence,
    _snapshot_with_platform_relay_evidence,
)
from research.source_market_evidence import (
    append_market_observation,
    evidence_summary,
    load_market_evidence_index,
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
