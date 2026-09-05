from research.conservative_shadow_terminal import evaluate_shadow_terminal
from research.policy_evidence_schema import canonical_json, stable_hash
from research.quantity_execution import build_signed_quantity_constraints
from research.conservative_limit_fill import evaluate_limit_fill
from research_v3_contract import canonical_hash
import hashlib


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _signed(label, body):
    return {**body, "signature": stable_hash(label, body)}


def _rebind(values):
    entry_sha = _sha(values["entry_receipt"])
    path_sha = _sha(values["future_path_rows"])
    bindings = {"entry_receipt_sha256": entry_sha, "future_path_sha256": path_sha,
                "policy_signature": values["policy_signature"], "generation": values["generation"]}
    values["entry_receipt_sha256"] = entry_sha
    values["future_path_sha256"] = path_sha
    for key, label in (
        ("position_context", "conservative-shadow-position-context"),
        ("cost_model", "conservative-shadow-cost-model"),
        ("coverage_policy", "shadow-path-coverage-policy"),
    ):
        body = {name: item for name, item in values[key].items() if name != "signature"}
        body.update(bindings)
        values[key] = _signed(label, body)
    payload = canonical_json({"schema": "market_segment_v3", "rows": values["future_path_rows"]}).encode()
    digest = hashlib.sha256(payload).hexdigest()
    values["source_segment_payloads"] = [payload]
    segment_receipt = {
        "schema": "market_segment_v3", "sha256": digest,
        "verification_status": "CHECKSUM_VERIFIED", "verifier_version": "canonical-segment-v1",
        "generation": values["generation"],
    }
    values["source_segment_receipts"] = [{**segment_receipt,
                                           "receipt_sha256": _sha(segment_receipt)}]
    return values


def _inputs():
    generation = {name: name + "-1" for name in (
        "manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
        "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key",
    )}
    entry = {
        "schema": "conservative_limit_fill_receipt_v2", "supported": True,
        "final_classification": "PARTIAL_FILL", "trigger_bucket_ts": 10,
        "fill_price": 100, "filled_qty": 0.4, "direction": "LONG",
        "quantity_attempts": [{"accepted": True, "rounded_executable_quantity": .4,
                               "execution_price": 100, "trigger_bucket_ts": 10}],
        "symbol": "BTCUSD",
        "quantity_constraints": build_signed_quantity_constraints(
            symbol="BTCUSD", quantity_step="0.1", quantity_precision=1,
            min_lot="0.1", min_notional="1", captured_at="2026-01-01T00:00:00Z",
            source_revision="source_revision-1", source="fixture",
        ),
    }
    rows = [
        {"schema": "market_microstructure_1s_v1", "bucket_ts": ts,
         "fresh": True, "valid_bbo": True, "bid": price, "ask": price + 0.1,
         "bid_qty": 1, "ask_qty": 1, "trade_count": 1, "buy_qty": .1, "sell_qty": .1}
        for ts, price in ((11, 100), (12, 101), (13, 103), (14, 102))
    ]
    policy = {
        "entry": {"entry_policy_id": "entry"},
        "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_V1"},
        "loss_protection": {"atr_stop_k": 1.5, "hard_stop_margin_pct": 30,
                            "thesis_cut_margin_pct": -12, "thesis_window_sec": 300},
        "profit_protection": {"mode": "ATR_TARGET", "atr_tp_k": 2.5,
                              "ladder": [], "partial_take_profits": []},
        "portfolio": {"concurrency_cap": 1, "size_scale": 1},
    }
    entry_sha = _sha(entry)
    path_sha = _sha(rows)
    policy_sha = canonical_hash("v3-policy", policy)
    bindings = {"entry_receipt_sha256": entry_sha, "future_path_sha256": path_sha,
                "policy_signature": policy_sha, "generation": generation}
    context_body = {"schema": "conservative_shadow_position_context_v1",
                    "position_context_id": "position-1", "atr_pct_at_fill": 1,
                    "leverage": 10, "margin_usd": 4, **bindings}
    cost_body = {"schema": "conservative_shadow_cost_model_v1", "cost_model_id": "cost-1",
                 "trading_fees_usd": .1, "funding_usd": .02, "latency_cost_usd": .01,
                 "spread_slippage_basis": "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES",
                 **bindings}
    coverage_body = {"schema": "shadow_path_coverage_policy_v1", "sampling_interval_sec": 1,
                     "first_sample_offset_sec": 1,
                     "require_fresh_bbo": True, "require_trade_fields": True,
                     "path_start_basis": "FIRST_COMPLETE_SAMPLE_AFTER_ENTRY_FILL",
                     "path_end_basis": "DECLARED_REQUIRED_HORIZON",
                     "row_schema": "market_microstructure_1s_v1",
                     "source_segment_schema": "market_segment_v3", **bindings}
    segment_payload = canonical_json({"schema": "market_segment_v3", "rows": rows}).encode()
    segment_sha = hashlib.sha256(segment_payload).hexdigest()
    segment_receipt = {"schema": "market_segment_v3", "sha256": segment_sha,
                       "verification_status": "CHECKSUM_VERIFIED",
                       "verifier_version": "canonical-segment-v1", "generation": generation}
    return {
        "generation": generation, "entry_receipt": entry,
        "entry_receipt_sha256": entry_sha, "future_path_rows": rows,
        "future_path_sha256": path_sha, "required_horizon_end_ts": 14,
        "policy_spec": policy, "policy_signature": policy_sha,
        "position_context": _signed("conservative-shadow-position-context", context_body),
        "cost_model": _signed("conservative-shadow-cost-model", cost_body),
        "coverage_policy": _signed("shadow-path-coverage-policy", coverage_body),
        "source_segment_receipts": [{**segment_receipt,
                                     "receipt_sha256": _sha(segment_receipt)}],
        "source_segment_payloads": [segment_payload],
    }


def test_complete_shadow_terminal_reuses_exit_policy_and_accounts_partial_quantity():
    receipt = evaluate_shadow_terminal(**_inputs())

    assert receipt["status"] == "COMPLETE"
    assert receipt["profitability_supported"] is True
    assert receipt["ranking_eligible"] is False
    assert receipt["execution_support_status"] == "SUPPORTED_CONSERVATIVE_SHADOW_ONLY"
    assert receipt["filled_qty"] == .4
    assert receipt["entry_vwap"] == 100
    assert receipt["entry_complete_ts"] == 10
    assert receipt["replay_start_ts"] == 11
    assert receipt["exit_reason"] == "ATR_TAKE_PROFIT"
    assert receipt["gross_pnl_usd"] == 1.2
    assert receipt["total_cost_usd"] == .13
    assert receipt["net_pnl_usd"] == 1.07
    assert receipt["spread_slippage_basis"] == "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES"
    assert receipt["simulation_model"] == "SAFE_POLICY_REPLAY_V3_EXECUTABLE_EXIT_BBO_DEPTH"
    assert len(receipt["receipt_sha256"]) == 64


def test_missing_second_in_required_horizon_is_unknown():
    values = _inputs()
    values["future_path_rows"] = [row for row in values["future_path_rows"] if row["bucket_ts"] != 11]
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "FUTURE_PATH_REQUIRED_HORIZON_INCOMPLETE" in receipt["blockers"]
    assert receipt["net_pnl_usd"] is None


def test_unsigned_cost_model_never_defaults_costs():
    values = _inputs()
    values["cost_model"] = {"schema": "conservative_shadow_cost_model_v1",
                            "cost_model_id": "cost-1"}

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "CONSERVATIVE_SHADOW_COST_MODEL_SIGNATURE_INVALID" in receipt["blockers"]
    assert "COST_MODEL_FIELD_INVALID:trading_fees_usd" in receipt["blockers"]


def test_exit_depth_shortfall_is_unknown_not_assumed_fill():
    values = _inputs()
    values["future_path_rows"][2]["bid_qty"] = .1
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert receipt["blockers"] == ["EXIT_VISIBLE_DEPTH_INSUFFICIENT"]


def test_policy_or_entry_hash_mismatch_fails_before_profitability():
    values = _inputs()
    values["entry_receipt_sha256"] = "0" * 64
    values["policy_signature"] = "policy-wrong"

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "ENTRY_RECEIPT_SHA256_MISMATCH" in receipt["blockers"]
    assert "POLICY_SIGNATURE_INVALID" in receipt["blockers"]
    assert receipt["profitability_supported"] is False


def test_signed_two_second_sampling_policy_is_used_without_one_second_default():
    values = _inputs()
    values["future_path_rows"] = values["future_path_rows"][::2]
    values["required_horizon_end_ts"] = 13
    _rebind(values)
    coverage = dict(values["coverage_policy"])
    coverage.pop("signature")
    coverage["sampling_interval_sec"] = 2
    values["coverage_policy"] = _signed("shadow-path-coverage-policy", coverage)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["required_horizon_end_ts"] == 13


def test_position_notional_must_exactly_match_partial_filled_quantity():
    values = _inputs()
    context = dict(values["position_context"])
    context.pop("signature")
    context["margin_usd"] = 10
    values["position_context"] = _signed("conservative-shadow-position-context", context)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "POSITION_CONTEXT_QUANTITY_MISMATCH" in receipt["blockers"]


def test_fractional_sampling_interval_is_unknown_not_exception():
    values = _inputs()
    coverage = dict(values["coverage_policy"])
    coverage.pop("signature")
    coverage["sampling_interval_sec"] = .5
    values["coverage_policy"] = _signed("shadow-path-coverage-policy", coverage)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "COVERAGE_POLICY_FIELDS_INVALID" in receipt["blockers"]


def test_crossed_bbo_and_negative_trade_input_are_unknown():
    values = _inputs()
    values["future_path_rows"][1]["ask"] = 99
    values["future_path_rows"][1]["buy_qty"] = -1
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "FUTURE_PATH_ROW_NOT_EXECUTABLE" in receipt["blockers"]


def test_verified_segment_payload_must_be_the_actual_future_rows():
    values = _inputs()
    values["future_path_rows"][1]["bid"] = 999
    values["future_path_sha256"] = _sha(values["future_path_rows"])

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "FUTURE_PATH_NOT_DERIVED_FROM_VERIFIED_SEGMENTS" in receipt["blockers"]


def test_negative_funding_credit_is_permitted_when_signed():
    values = _inputs()
    costs = dict(values["cost_model"])
    costs.pop("signature")
    costs["funding_usd"] = -.02
    values["cost_model"] = _signed("conservative-shadow-cost-model", costs)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["funding_usd"] == -.02


def test_required_horizon_must_be_divisible_by_signed_sampling_interval():
    values = _inputs()
    values["future_path_rows"] = values["future_path_rows"][::2]
    coverage = dict(values["coverage_policy"])
    coverage.pop("signature")
    coverage["sampling_interval_sec"] = 2
    values["coverage_policy"] = _signed("shadow-path-coverage-policy", coverage)
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "FUTURE_PATH_REQUIRED_HORIZON_INCOMPLETE" in receipt["blockers"]


def test_malformed_generation_values_return_unknown_deterministically():
    values = _inputs()
    values["generation"] = {**values["generation"], "epoch_id": True,
                            "analyzer_revision": float("inf")}

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "GENERATION_INVALID:epoch_id" in receipt["blockers"]
    assert "GENERATION_INVALID:analyzer_revision" in receipt["blockers"]


def test_signed_cost_receipt_from_another_path_is_rejected():
    values = _inputs()
    costs = dict(values["cost_model"])
    costs.pop("signature")
    costs["future_path_sha256"] = "f" * 64
    values["cost_model"] = _signed("conservative-shadow-cost-model", costs)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "UNKNOWN"
    assert "COST_MODEL_FUTURE_PATH_SHA256_MISMATCH" in receipt["blockers"]


def test_rounded_position_uses_effective_filled_margin_for_gross_pnl():
    values = _inputs()
    context = dict(values["position_context"])
    context.pop("signature")
    context["margin_usd"] = 4.99  # raw quantity .499 rounds down to the filled .4
    values["position_context"] = _signed("conservative-shadow-position-context", context)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["declared_position_margin_usd"] == 4.99
    assert receipt["effective_filled_margin_usd"] == 4.0
    assert receipt["gross_pnl_usd"] == 1.2


def test_verified_segment_can_retain_pre_fill_and_post_horizon_context():
    values = _inputs()
    context_rows = [
        {**values["future_path_rows"][0], "bucket_ts": 10},
        *values["future_path_rows"],
        {**values["future_path_rows"][-1], "bucket_ts": 15},
    ]
    payload = canonical_json({"schema": "market_segment_v3", "rows": context_rows}).encode()
    digest = hashlib.sha256(payload).hexdigest()
    segment_receipt = {
        "schema": "market_segment_v3", "sha256": digest,
        "verification_status": "CHECKSUM_VERIFIED", "verifier_version": "canonical-segment-v1",
        "generation": values["generation"],
    }
    values["source_segment_payloads"] = [payload]
    values["source_segment_receipts"] = [{**segment_receipt,
                                           "receipt_sha256": _sha(segment_receipt)}]

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["future_path_sha256"] == _sha(values["future_path_rows"])
    assert receipt["source_segment_hashes"] == [digest]


def test_actual_conservative_limit_fill_receipt_round_trips_to_terminal_replay():
    values = _inputs()
    constraints = values["entry_receipt"]["quantity_constraints"]
    entry = evaluate_limit_fill(
        [{"schema": "market_microstructure_1s_v1", "symbol": "BTCUSD",
          "bucket_ts": 10, "fresh": True, "valid_bbo": True,
          "bid": 99.9, "ask": 100, "bid_qty": 1, "ask_qty": .4,
          "trade_count": 0, "buy_qty": 0, "sell_qty": 0}],
        direction="LONG", requested_qty=.4,
        chase_schedule=[{"bucket_id": "entry", "start_ts": 10,
                         "end_ts": 11, "limit_price": 100}],
        aggressor_window_sec=1, symbol="BTCUSD", quantity_constraints=constraints,
    )
    assert entry["supported"] is True
    assert entry["final_classification"] == "FULL_FILL"
    values["entry_receipt"] = entry
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["entry_fill_event_count"] == 1
    assert receipt["entry_complete_ts"] == 10
    assert receipt["entry_vwap"] == 100


def test_multiple_accepted_entry_fills_use_vwap_and_last_fill_clock():
    values = _inputs()
    values["entry_receipt"] = {
        **values["entry_receipt"], "fill_price": 101, "trigger_bucket_ts": 10,
        "quantity_attempts": [
            {"accepted": True, "rounded_executable_quantity": .2,
             "execution_price": 99, "trigger_bucket_ts": 9},
            {"accepted": True, "rounded_executable_quantity": .2,
             "execution_price": 101, "trigger_bucket_ts": 10},
        ],
    }
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["entry_fill_event_count"] == 2
    assert receipt["entry_vwap"] == 100
    assert receipt["entry_complete_ts"] == 10
    assert receipt["replay_start_ts"] == 11
    assert receipt["gross_pnl_usd"] == 1.2


def test_two_second_time_stop_ages_from_actual_last_fill_not_first_sample():
    values = _inputs()
    for row in values["future_path_rows"]:
        row["bid"] = 100
        row["ask"] = 100.1
    policy = values["policy_spec"]
    policy["loss_protection"].update({
        "atr_stop_k": 99,
        "hard_stop_margin_pct": 99,
        "thesis_cut_margin_pct": -99,
        "time_stop_min": 2 / 60,
    })
    policy["profit_protection"]["atr_tp_k"] = 99
    values["policy_signature"] = canonical_hash("v3-policy", policy)
    _rebind(values)

    receipt = evaluate_shadow_terminal(**values)

    assert receipt["status"] == "COMPLETE"
    assert receipt["entry_complete_ts"] == 10
    assert receipt["replay_start_ts"] == 11
    assert receipt["exit_reason"] == "TIME_STOP"
    assert receipt["exit_ts"] == 12
