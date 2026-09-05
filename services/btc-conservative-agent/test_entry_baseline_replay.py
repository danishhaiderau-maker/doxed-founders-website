from copy import deepcopy
import hashlib

from research.quantity_execution import build_signed_quantity_constraints
from research.entry_baseline_replay import materialize_same_opportunity_replay
from research_entry_baselines import ENTRY_BASELINE_REGISTRY, classify_baseline_evidence
from research_entry_baselines import materialize_signal_time_baseline_schedules
from research.entry_baseline_replay import materialize_v3_opportunity_replay
import json


CONSTRAINTS = build_signed_quantity_constraints(
    symbol="BTC", quantity_step="0.1", quantity_precision=1,
    min_lot="0.1", min_notional="1", captured_at="2026-09-01T00:00:00Z",
    source_revision="fixture", source="TEST_FIXTURE",
)


def _row(ts, *, bid=99, ask=101, bid_qty=2, ask_qty=2):
    return {
        "schema": "market_microstructure_1s_v1", "symbol": "BTC", "bucket_ts": ts,
        "fresh": True, "valid_bbo": True, "bid": bid, "ask": ask,
        "bid_qty": bid_qty, "ask_qty": ask_qty, "buy_qty": 0, "sell_qty": 0,
        "buy_vwap": None, "sell_vwap": None, "trade_count": 0,
    }


def _episode(*, ask_qty=2):
    baselines = ENTRY_BASELINE_REGISTRY["baselines"]
    schedules = {}
    row_timestamps = set()
    for index, baseline in enumerate(baselines):
        if baseline["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL":
            start = 100
        elif baseline["baseline_id"] == "FINAL_MARKET_AFTER_EXPIRY":
            start = 1900
        elif baseline.get("entry_type") == "LIMIT_CHASE_WINDOW":
            start = 100 + baseline["window_start_sec"]
        else:
            start = 102 + index * 2
        row_timestamps.add(start)
        schedules[baseline["baseline_id"]] = {
            "episode_id": "ep-1", "opportunity_id": "opp-1",
            "policy_signature": baseline["policy_signature"],
            "schedule": [{
                "bucket_id": f"{baseline['baseline_id']}:0", "start_ts": start,
                "end_ts": start + 1, "limit_price": 101, "generation": 0,
            }],
        }
    return {
        "episode_id": "ep-1", "opportunity_id": "opp-1", "direction": "LONG",
        "signal_ts": 100, "expiry_ts": 1900, "symbol": "BTC", "requested_qty": 1,
        "requested_remaining_qty": 1, "signed_quantity_constraints": CONSTRAINTS,
        "latency_sec": 0, "fees_usd": 0, "slippage_model": "DECLARED_LIMIT",
        "authoritative_parent_expiry": True, "dataset_epoch": "epoch-1",
        "source_revision": "rev-1", "tile_config_signature": "tiles-1",
        "baseline_schedules": schedules,
        "market_microstructure_rows": [
            _row(ts, ask=101, ask_qty=ask_qty) for ts in sorted(row_timestamps)
        ],
    }


def _write_v3_opportunity(tmp_path, episode=None):
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True, exist_ok=True)
    value = deepcopy(episode or _episode())
    value.pop("market_microstructure_rows", None)
    value.setdefault("record_id", "opp-record")
    value.setdefault("epoch_id", value.get("dataset_epoch"))
    (ledgers / "opportunity.jsonl").write_text(json.dumps(value) + "\n", encoding="utf-8")
    return ledgers, value


def _segment_object(tmp_path, rows):
    envelope = {"schema": "market_segment_v3", "rows": rows}
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    object_path = tmp_path / "v3" / "market_segments" / digest[:2] / f"{digest}.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(encoded)
    return digest, f"v3/market_segments/{digest[:2]}/{digest}.json"


def test_same_opportunities_feed_every_baseline_and_zero_costs_are_valid():
    report = materialize_same_opportunity_replay([_episode()])
    assert report["same_opportunity_count"] == 1
    assert len(report["baseline_ids"]) == 11
    receipt = report["episode_receipts"][0]
    assert {row["opportunity_id"] for row in receipt["results"]} == {"opp-1"}
    assert {row["episode_id"] for row in receipt["results"]} == {"ep-1"}
    assert {row["outcome_state"] for row in receipt["results"]} == {"FULL_FILL"}
    assert all(summary["full_fills"] == 1 for summary in report["summaries"].values())
    assert report["analysis_scope"] == "ENTRY_FILL_COUNTERFACTUAL_ONLY"
    assert report["terminal_exit_pnl_evaluated"] is False
    assert report["profitability_supported"] is False
    assert receipt["direction"] == "LONG"
    assert receipt["results"][0]["baseline_spec"]["execution_class"] == "RESEARCH_ONLY"
    assert receipt["results"][0]["conservative_receipt"]["simulation_model"]
    evidence = {
        name: (0 if name in {"fees", "latency"} else True)
        for name in ENTRY_BASELINE_REGISTRY["baselines"][0]["required_evidence"]
    }
    evidence["terminal_outcome"] = "FULL_FILL"
    assert classify_baseline_evidence("MARKET_ENTRY_AT_SIGNAL", evidence)["supported"] is True


def test_positive_accepted_partial_is_visible_for_each_baseline():
    report = materialize_same_opportunity_replay([_episode(ask_qty=.4)])
    assert all(summary["partial_fills"] == 1 for summary in report["summaries"].values())
    for result in report["episode_receipts"][0]["results"]:
        assert result["outcome_state"] == "PARTIAL_FILL"
        assert result["conservative_receipt"]["filled_qty"] == .4


def test_missing_tape_is_unknown_for_every_baseline_never_no_fill():
    episode = _episode()
    episode["market_microstructure_rows"] = []
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["unknown"] == 1 for summary in report["summaries"].values())
    assert all(summary["no_fills"] == 0 for summary in report["summaries"].values())


def test_complete_uncrossed_bbo_is_proven_no_fill_not_unknown():
    episode = _episode()
    episode["market_microstructure_rows"] = [
        _row(row["bucket_ts"], ask=101, ask_qty=0)
        for row in episode["market_microstructure_rows"]
    ]
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["no_fills"] == 1 for summary in report["summaries"].values())
    assert all(summary["unknown"] == 0 for summary in report["summaries"].values())


def test_missing_measured_fee_is_unknown_never_assumed_zero():
    episode = _episode()
    episode["fees_usd"] = None
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["unknown"] == 1 for summary in report["summaries"].values())
    for result in report["episode_receipts"][0]["results"]:
        assert "MISSING_FEES" in result["rejection_codes"]


def test_missing_requested_quantity_is_unknown_never_assumed():
    episode = _episode()
    episode["requested_qty"] = None
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["unknown"] == 1 for name, summary in report["summaries"].items()
               if name != "FINAL_MARKET_AFTER_EXPIRY")
    assert report["summaries"]["FINAL_MARKET_AFTER_EXPIRY"]["full_fills"] == 1
    assert all("MISSING_REQUESTED_QUANTITY" in result["rejection_codes"]
               for result in report["episode_receipts"][0]["results"]
               if result["baseline_id"] != "FINAL_MARKET_AFTER_EXPIRY")


def test_mismatched_schedule_identity_is_unknown_only_for_that_baseline():
    episode = _episode()
    episode["baseline_schedules"]["CHASE_13_MIN_COMPRESSED"]["opportunity_id"] = "other"
    report = materialize_same_opportunity_replay([episode])
    rows = {row["baseline_id"]: row for row in report["episode_receipts"][0]["results"]}
    assert rows["CHASE_13_MIN_COMPRESSED"]["outcome_state"] == "UNKNOWN"
    assert "BASELINE_SCHEDULE_OPPORTUNITY_ID_MISMATCH" in rows["CHASE_13_MIN_COMPRESSED"]["rejection_codes"]
    assert all(row["outcome_state"] == "FULL_FILL" for key, row in rows.items() if key != "CHASE_13_MIN_COMPRESSED")


def test_receipts_and_report_ids_are_deterministic_and_input_not_mutated():
    episode = _episode()
    before = deepcopy(episode)
    first = materialize_same_opportunity_replay([episode])
    second = materialize_same_opportunity_replay([episode])
    assert first == second
    assert episode == before


def test_window_schedule_outside_declared_bucket_is_unknown():
    episode = _episode()
    envelope = episode["baseline_schedules"]["CHASE_WINDOW_3"]
    envelope["schedule"][0]["start_ts"] = episode["signal_ts"]
    envelope["schedule"][0]["end_ts"] = episode["signal_ts"] + 1
    receipt = materialize_same_opportunity_replay([episode])["episode_receipts"][0]
    result = next(row for row in receipt["results"] if row["baseline_id"] == "CHASE_WINDOW_3")
    assert result["outcome_state"] == "UNKNOWN"
    assert result["rejection_codes"] == ["CHASE_WINDOW_SCHEDULE_OUTSIDE_DECLARED_BUCKET"]


def test_signal_time_snapshot_persists_unknown_for_every_uncaptured_schedule():
    snapshot = materialize_signal_time_baseline_schedules({
        "episode_id": "ep-1", "opportunity_id": "opp-1", "signal_ts": 100,
    })
    assert set(snapshot["schedules"]) == {
        row["baseline_id"] for row in ENTRY_BASELINE_REGISTRY["baselines"]
    }
    assert all(
        row["capture_status"] == "UNKNOWN_NOT_CAPTURED_AT_SIGNAL"
        for row in snapshot["schedules"].values()
    )


def test_signal_time_snapshot_captures_pre_signal_market_limit_and_chase_schedules():
    snapshot = materialize_signal_time_baseline_schedules({
        "episode_id": "ep-1", "opportunity_id": "opp-1", "signal_ts": 100,
        "raw_direction": "LONG", "signal_price": 100,
        "signal_time_bbo": {"bid": 99, "ask": 101, "bid_qty": 2, "ask_qty": 3},
    })
    rows = snapshot["schedules"]
    assert rows["MARKET_ENTRY_AT_SIGNAL"]["schedule"][0]["limit_price"] == 101
    assert rows["NO_CHASE_LIMIT"]["schedule"][0]["limit_price"] == 99.9
    assert len(rows["CHASE_13_MIN_COMPRESSED"]["schedule"]) == 6
    assert len(rows["CHASE_30_MIN_LEGACY"]["schedule"]) > 1
    assert all(rows[f"CHASE_WINDOW_{index}"]["schedule"] for index in range(6))
    assert rows["FINAL_MARKET_AFTER_EXPIRY"]["capture_status"] == "UNKNOWN_FUTURE_BBO_REQUIRED"


def test_signal_time_snapshot_is_unknown_when_depth_is_absent():
    snapshot = materialize_signal_time_baseline_schedules({
        "episode_id": "ep-1", "opportunity_id": "opp-1", "signal_ts": 100,
        "raw_direction": "LONG", "signal_price": 100,
        "signal_time_bbo": {"bid": 99, "ask": 101},
    })
    assert all(
        row["capture_status"] == "UNKNOWN_NOT_CAPTURED_AT_SIGNAL"
        for row in snapshot["schedules"].values()
    )


def test_v3_materializer_uses_existing_engine_and_missing_schedule_is_unknown(tmp_path):
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    opportunity = {
        "record_id": "opp-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
        "epoch_id": "epoch-1", "source_revision": "rev-1",
        "tile_config_signature": "tiles-1", "raw_direction": "LONG",
        "symbol": "BTC", "signal_ts": 100,
    }
    opportunity["baseline_schedule_snapshot"] = materialize_signal_time_baseline_schedules(opportunity)
    (ledgers / "opportunity.jsonl").write_text(json.dumps(opportunity) + "\n", encoding="utf-8")
    report = materialize_v3_opportunity_replay(tmp_path)
    assert report["same_opportunity_count"] == 1
    assert report["directional_episode_count"] == 2
    assert all(row["unknown"] == 2 for row in report["summaries"].values())
    assert all(row["no_fills"] == 0 for row in report["summaries"].values())


def test_v3_materializer_joins_verified_content_addressed_market_segment(tmp_path):
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    episode = _episode()
    market_rows = episode.pop("market_microstructure_rows")
    opportunity = {**episode, "record_id": "opp-record", "epoch_id": "epoch-1",
                   "deployed_revision": "dep-1", "config_signature": "cfg-1"}
    (ledgers / "opportunity.jsonl").write_text(json.dumps(opportunity) + "\n", encoding="utf-8")
    envelope = {"schema": "market_segment_v3", "rows": market_rows}
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    object_path = tmp_path / "v3" / "market_segments" / digest[:2] / f"{digest}.json"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(encoded)
    segment = {
        "record_id": "segment-1", "epoch_id": "epoch-1", "opportunity_id": "opp-1",
        "episode_id": "ep-1", "context_role": "ENTRY_PATH",
        "coverage": {"conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": digest,
                        "relative_path": f"v3/market_segments/{digest[:2]}/{digest}.json",
                        "row_count": len(market_rows)},
    }
    (ledgers / "market_segment.jsonl").write_text(json.dumps(segment) + "\n", encoding="utf-8")
    report = materialize_v3_opportunity_replay(tmp_path)
    receipt = report["episode_receipts"][0]
    assert all(result["outcome_state"] == "FULL_FILL" for result in receipt["results"])
    assert receipt["deployed_revision"] == "dep-1"
    assert receipt["config_signature"] == "cfg-1"
    assert receipt["market_tape_hashes"] == [digest]
    assert receipt["market_tape_ids"] == ["segment-1"]
    assert receipt["market_evidence_provenance"][0]["relative_path"].endswith(f"{digest}.json")
    conservative = receipt["results"][0]["conservative_receipt"]
    assert conservative["tape_hashes"] == [digest]
    assert conservative["tape_ids"] == ["segment-1"]


def test_bad_segment_hash_is_explicit_unknown(tmp_path):
    ledgers, _ = _write_v3_opportunity(tmp_path)
    segment = {
        "record_id": "bad-segment", "epoch_id": "epoch-1", "opportunity_id": "opp-1",
        "episode_id": "ep-1", "context_role": "ENTRY_PATH",
        "coverage": {"conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": "0" * 64,
                        "relative_path": "v3/market_segments/00/missing.json"},
    }
    (ledgers / "market_segment.jsonl").write_text(json.dumps(segment) + "\n", encoding="utf-8")
    receipt = materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert "UNKNOWN_TAPE_PATH_NOT_CANONICAL" in receipt["market_evidence_reason_codes"]
    assert all("UNKNOWN_TAPE_PATH_NOT_CANONICAL" in result["rejection_codes"]
               for result in receipt["results"])


def test_foreign_epoch_or_episode_segment_is_not_joined(tmp_path):
    ledgers, _ = _write_v3_opportunity(tmp_path)
    digest, relative = _segment_object(tmp_path, _episode()["market_microstructure_rows"])
    segment = {
        "record_id": "foreign", "epoch_id": "other", "opportunity_id": "opp-1",
        "episode_id": "other-episode", "context_role": "ENTRY_PATH",
        "coverage": {"conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": digest, "relative_path": relative},
    }
    (ledgers / "market_segment.jsonl").write_text(json.dumps(segment) + "\n", encoding="utf-8")
    receipt = materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert receipt["market_tape_hashes"] == []
    assert "NO_MATCHING_VERIFIED_MARKET_SEGMENT" in receipt["market_evidence_reason_codes"]


def test_invalid_newer_future_segment_falls_back_to_verified_owner_generation(tmp_path):
    ledgers, _ = _write_v3_opportunity(tmp_path)
    rows = _episode()["market_microstructure_rows"]
    digest, relative = _segment_object(tmp_path, rows)
    coverage = {"conservative_bbo_depth_eligible": True, "future_path_status": "COMPLETE",
                "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200]}
    base = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
            "context_role": "SIGNAL_TO_120M_FUTURE_PATH", "future_path_status": "COMPLETE",
            "future_path_owner_key": "owner-1", "coverage": coverage}
    good = {**base, "record_id": "good", "segment_ref": {"sha256": digest, "relative_path": relative}}
    bad = {**base, "record_id": "bad", "segment_ref": {"sha256": "0" * 64,
                                                           "relative_path": "v3/market_segments/00/bad.json"}}
    (ledgers / "market_segment.jsonl").write_text(
        json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8"
    )
    receipt = materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert receipt["market_tape_ids"] == ["good"]
    assert receipt["future_path_selection"]["selected_future_path_record_ids"] == ["good"]
    assert receipt["future_path_selection"]["invalid_newer_future_path_record_ids"] == ["bad"]


def test_duplicate_verified_segments_do_not_double_count_depth(tmp_path):
    ledgers, _ = _write_v3_opportunity(tmp_path)
    digest, relative = _segment_object(tmp_path, _episode()["market_microstructure_rows"])
    base = {
        "epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
        "context_role": "ENTRY_PATH", "coverage": {"conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": digest, "relative_path": relative},
    }
    (ledgers / "market_segment.jsonl").write_text(
        json.dumps({**base, "record_id": "copy-a"}) + "\n"
        + json.dumps({**base, "record_id": "copy-b"}) + "\n", encoding="utf-8"
    )
    receipt = materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert receipt["market_tape_hashes"] == [digest]
    assert all(result["outcome_state"] == "UNKNOWN" for result in receipt["results"])
    assert all("DUPLICATE_EVIDENCE_BUCKET" in result["rejection_codes"]
               for result in receipt["results"])


def test_top_level_and_causal_identity_conflict_is_explicit_unknown(tmp_path):
    episode = _episode()
    episode["causal_identity"] = {
        "dataset_epoch": "epoch-1", "source_revision": "different",
        "tile_config_signature": "tiles-1", "direction": "LONG", "symbol": "BTC",
    }
    _write_v3_opportunity(tmp_path, episode)
    receipt = materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert "CONFLICTING_CAUSAL_IDENTITY:source_revision" in receipt["market_evidence_reason_codes"] or \
        "CONFLICTING_CAUSAL_IDENTITY:source_revision" in receipt.get("materialization_reason_codes", [])
    assert all("CONFLICTING_CAUSAL_IDENTITY:source_revision" in result["rejection_codes"]
               for result in receipt["results"])


def test_config_signature_is_not_silently_used_as_tile_signature(tmp_path):
    episode = _episode()
    episode.pop("tile_config_signature")
    episode["config_signature"] = "config-only"
    _write_v3_opportunity(tmp_path, episode)
    receipt = materialize_v3_opportunity_replay(tmp_path)["episode_receipts"][0]
    assert receipt["tile_config_signature"] is None
    assert all("MISSING_TILE_CONFIG_SIGNATURE" in result["rejection_codes"]
               for result in receipt["results"])
