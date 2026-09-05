import hashlib
import json

from research.conservative_shadow_report import (
    build_composite_policy_identity, build_conservative_shadow_report,
    load_current_policy_candidates,
)
from research.policy_evidence_schema import canonical_json, stable_hash
from research.quantity_execution import build_signed_quantity_constraints
from research_v3_contract import canonical_hash


GEN = {name: name + "-1" for name in (
    "manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
    "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key",
)}


def _fixture(tmp_path, *, model=True):
    rows = [{"schema": "market_microstructure_1s_v1", "bucket_ts": ts, "fresh": True,
             "valid_bbo": True, "bid": px, "ask": px + .1, "bid_qty": 1, "ask_qty": 1,
             "trade_count": 1, "buy_qty": .1, "sell_qty": .1}
            for ts, px in ((10, 100), (11, 101), (12, 103), (13, 102))]
    payload = canonical_json({"schema": "market_segment_v3", "rows": rows}).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"v3/market_segments/{digest[:2]}/{digest}.json"
    path = tmp_path / relative; path.parent.mkdir(parents=True); path.write_bytes(payload)
    entry = {"schema": "conservative_limit_fill_receipt_v2", "supported": True,
             "final_classification": "PARTIAL_FILL", "trigger_bucket_ts": 10,
             "fill_price": 100, "filled_qty": .4, "direction": "LONG", "symbol": "BTCUSD",
             "quantity_attempts": [{"accepted": True, "rounded_executable_quantity": .4,
                                    "execution_price": 100, "trigger_bucket_ts": 10}],
             "quantity_constraints": build_signed_quantity_constraints(
                 symbol="BTCUSD", quantity_step="0.1", quantity_precision=1, min_lot="0.1",
                 min_notional="1", captured_at="2026-01-01T00:00:00Z",
                 source_revision="source_revision-1", source="fixture")}
    baseline_spec = {"baseline_id": "b1", "entry_type": "LIMIT", "execution_class": "RESEARCH_ONLY",
                     "relay_eligible": False, "missing_evidence_outcome": "UNKNOWN"}
    baseline_signature = canonical_hash("entry-baseline", baseline_spec)
    baseline_spec["policy_signature"] = baseline_signature
    result = {"baseline_id": "b1", "policy_signature": baseline_signature,
              "baseline_spec": baseline_spec, "supported": True,
              "outcome_state": "PARTIAL_FILL", "conservative_receipt": entry}
    baseline = {"schema": "entry_baseline_same_opportunity_replay_v1", "generation": GEN,
                "episode_receipts": [{"episode_id": "e1", "opportunity_id": "o1",
                    "market_evidence_provenance": [{"status": "VERIFIED", "sha256": digest,
                        "relative_path": relative, "segment_record_id": "s1"}],
                    "results": [result]}]}
    policy = {"entry": {"entry_policy_id": "entry"},
              "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_V1"},
              "loss_protection": {"atr_stop_k": 1.5, "hard_stop_margin_pct": 30,
                                  "thesis_cut_margin_pct": -12, "thesis_window_sec": 300},
              "profit_protection": {"mode": "ATR_TARGET", "atr_tp_k": 2.5,
                                    "ladder": [], "partial_take_profits": []},
              "portfolio": {"concurrency_cap": 1, "size_scale": 1}}
    signature = canonical_hash("v3-policy", policy)
    candidates = [{"policy_id": "p1", "policy_signature": signature, "policy_spec": policy}]
    artifact_identity = {"epoch_id": GEN["epoch_id"],
        "source_revision": GEN["source_revision"],
        "analyzer_generation_revision": GEN["analyzer_revision"],
        "tile_config_signature": GEN["tile_config_signature"]}
    artifact = {"schema": "policy_candidate_artifact_receipt_v1",
                "evaluation_generation": GEN,
                "artifact_identity": artifact_identity,
                "artifact_verified_identity_fields": sorted(artifact_identity),
                "generation_binding_basis": "CURRENT_EVALUATION_CONTEXT_NOT_COLLECTED_ARTIFACT_IDENTITY",
                "candidate_count": 1, "candidates_sha256": hashlib.sha256(canonical_json(candidates).encode()).hexdigest(),
                "source_basis": "IN_MEMORY_CURRENT_GENERATION"}
    research = None
    if model:
        composite_signature = build_composite_policy_identity(result, candidates[0])[1][
            "composite_policy_signature"]
        body = {"schema": "conservative_shadow_research_model_v1", "generation": GEN,
                "model_id": "explicit-1", "provenance": "TEST_EXPLICIT_INPUT",
                "contexts": [{"episode_id": "e1", "opportunity_id": "o1", "baseline_id": "b1",
                    "composite_policy_signature": composite_signature,
                    "position_context_id": "position-1",
                    "atr_pct_at_fill": 1, "atr_basis": "EXPLICIT_AT_FILL_OBSERVATION",
                    "atr_provenance": "fixture", "leverage": 10, "margin_usd": 4,
                    "sizing_provenance": "fixture", "cost_model_id": "cost-1",
                    "trading_fees_usd": .1, "funding_usd": .02, "latency_cost_usd": .01,
                    "cost_provenance": "fixture",
                    "spread_slippage_basis": "EMBEDDED_IN_ENTRY_AND_EXECUTABLE_EXIT_PRICES",
                    "sampling_interval_sec": 1, "first_sample_offset_sec": 1,
                    "require_fresh_bbo": True,
                    "require_trade_fields": True, "path_start_basis": "FIRST_COMPLETE_SAMPLE_AFTER_ENTRY_FILL",
                    "path_end_basis": "DECLARED_REQUIRED_HORIZON",
                    "row_schema": "market_microstructure_1s_v1", "source_segment_schema": "market_segment_v3",
                    "coverage_provenance": "fixture", "required_horizon_end_ts": 13}]}
        research = {**body, "signature": stable_hash("conservative-shadow-research-model", body)}
    return baseline, candidates, artifact, research


def test_explicit_current_model_runs_complete_end_to_end(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["candidate_replay_count"] == report["complete_replay_count"] == 1
    assert report["unknown_replay_count"] == 0
    assert report["results"][0]["terminal"]["net_pnl_usd"] == 1.07
    assert report["results"][0]["policy_signature"] != candidates[0]["policy_signature"]
    assert report["results"][0]["source_candidate_policy_signature"] == candidates[0]["policy_signature"]
    assert report["results"][0]["entry_baseline_signature"].startswith("entry-baseline-")
    assert report["evaluation_scope"] == "ENTRY_PLUS_SINGLE_POSITION_EXIT"
    assert report["portfolio_competition_status"] == "NOT_SIMULATED"
    assert report["ranking_eligible"] is False


def test_missing_model_is_unknown_without_zero_cost_defaults(tmp_path):
    baseline, candidates, artifact, _ = _fixture(tmp_path, model=False)
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=None)
    assert report["complete_replay_count"] == 0
    assert report["unknown_replay_count"] == 1
    assert report["reason_counts"]["RESEARCH_MODEL_MISSING"] == 1
    assert report["results"][0]["net_pnl_usd"] is None
    assert report["status"] == "BUILT_INCOMPLETE"
    assert report["profitability_supported"] is False


def test_missing_explicit_context_provenance_is_unknown(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    model["contexts"][0]["cost_provenance"] = None
    body = {key: value for key, value in model.items() if key != "signature"}
    model["signature"] = stable_hash("conservative-shadow-research-model", body)
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["complete_replay_count"] == 0
    assert report["reason_counts"]["RESEARCH_MODEL_CONTEXT_FIELD_INVALID:cost_provenance"] == 1


def test_malformed_top_level_inputs_fail_closed(tmp_path):
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=None, policy_candidates=[], policy_artifact_receipt=None,
        research_model=None)
    assert report["status"] == "UNKNOWN"
    assert "BASELINE_REPORT_SCHEMA_INVALID" in report["blockers"]
    assert "POLICY_ARTIFACT_RECEIPT_SCHEMA_INVALID" in report["blockers"]


def test_generation_or_candidate_hash_mismatch_fails_before_replay(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    artifact["evaluation_generation"] = {**GEN, "epoch_id": "stale"}
    artifact["candidates_sha256"] = "0" * 64
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["status"] == "UNKNOWN"
    assert "INPUT_GENERATION_MISMATCH" in report["blockers"]
    assert "POLICY_ARTIFACT_CANDIDATES_SHA256_MISMATCH" in report["blockers"]


def test_wrong_entry_baseline_identity_cannot_claim_candidate_pnl(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    baseline["episode_receipts"][0]["results"][0]["policy_signature"] = "entry-baseline-wrong"
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["complete_replay_count"] == 0
    assert report["reason_counts"]["ENTRY_BASELINE_SIGNATURE_INVALID"] == 1
    assert report["profitability_supported"] is False


def test_candidate_entry_and_portfolio_are_provenance_not_evaluated_identity(tmp_path):
    baseline, candidates, _artifact, _model = _fixture(tmp_path)
    entry_result = baseline["episode_receipts"][0]["results"][0]
    first_spec, first_identity = build_composite_policy_identity(entry_result, candidates[0])
    changed = json.loads(json.dumps(candidates[0]))
    changed["policy_spec"]["entry"] = {"entry_policy_id": "different-source-entry"}
    changed["policy_spec"]["portfolio"] = {"concurrency_cap": 5, "size_scale": .25}
    changed["policy_signature"] = canonical_hash("v3-policy", changed["policy_spec"])
    second_spec, second_identity = build_composite_policy_identity(entry_result, changed)
    assert first_identity["composite_policy_signature"] == second_identity["composite_policy_signature"]
    assert first_identity["source_candidate_policy_signature"] != second_identity["source_candidate_policy_signature"]
    assert first_identity["source_candidate_portfolio_signature"] != second_identity["source_candidate_portfolio_signature"]
    assert first_spec["portfolio"] == second_spec["portfolio"] == {
        "concurrency_cap": 1, "size_scale": 1,
        "evaluation_scope": "ONE_BASELINE_FILLED_POSITION"}


def test_duplicate_episode_not_counted_as_independent_or_replayed(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    baseline["episode_receipts"].append(dict(baseline["episode_receipts"][0]))
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["independent_episode_count"] == 0
    assert report["candidate_replay_count"] == 0
    assert report["duplicate_episode_ids"] == ["e1"]


def test_segment_path_and_checksum_are_source_fenced(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    baseline["episode_receipts"][0]["market_evidence_provenance"][0]["sha256"] = "0" * 64
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["complete_replay_count"] == 0
    assert report["reason_counts"]["SOURCE_SEGMENT_SHA256_MISMATCH"] == 1


def test_current_policy_loader_verifies_generation_gzip_rows_and_signatures(tmp_path):
    import gzip
    policy = _fixture(tmp_path)[1][0]
    row = {"schema": "safe_policy_exhaustive_row_v1", "epoch_id": GEN["epoch_id"],
           "source_revision": GEN["source_revision"],
           "analyzer_generation_revision": GEN["analyzer_revision"],
           "tile_config_signature": GEN["tile_config_signature"],
           "policy_identity_verified": True, **policy}
    artifact = tmp_path / "safe_policy_genome_v3_exhaustive.jsonl.gz"
    with gzip.open(artifact, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    manifest = {"schema": "safe_policy_exhaustive_manifest_v1",
                "epoch_id": GEN["epoch_id"], "source_revision": GEN["source_revision"],
                "analyzer_generation_revision": GEN["analyzer_revision"],
                "tile_config_signature": GEN["tile_config_signature"],
                "artifact": artifact.name, "compression": "gzip",
                "row_schema": "safe_policy_exhaustive_row_v1", "row_count": 1,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    (tmp_path / "safe_policy_genome_v3_exhaustive_manifest.json").write_text(json.dumps(manifest))
    candidates, receipt = load_current_policy_candidates(tmp_path, GEN, policy_cycle_succeeded=True)
    assert candidates == [policy]
    assert receipt["candidate_count"] == 1
    assert receipt["original_artifact_provenance"]["artifact_sha256"] == manifest["sha256"]


def test_different_candidate_horizons_are_order_independent(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    second = json.loads(json.dumps(candidates[0]))
    second["policy_id"] = "p2"
    second["policy_spec"]["profit_protection"]["atr_tp_k"] = 8
    second["policy_signature"] = canonical_hash("v3-policy", second["policy_spec"])
    candidates.append(second)
    artifact["candidate_count"] = 2
    artifact["candidates_sha256"] = hashlib.sha256(canonical_json(sorted(
        candidates, key=lambda item: (item["policy_signature"], item["policy_id"]))).encode()).hexdigest()
    second_context = dict(model["contexts"][0])
    entry_result = baseline["episode_receipts"][0]["results"][0]
    second_context["composite_policy_signature"] = build_composite_policy_identity(
        entry_result, second)[1]["composite_policy_signature"]
    second_context["required_horizon_end_ts"] = 12
    model["contexts"].append(second_context)
    body = {key: value for key, value in model.items() if key != "signature"}
    model["signature"] = stable_hash("conservative-shadow-research-model", body)
    first = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    second_report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=list(reversed(candidates)),
        policy_artifact_receipt=artifact, research_model=model)
    assert first["candidate_replay_count"] == first["complete_replay_count"] == 2
    assert [(row["policy_signature"], row["status"]) for row in first["results"]] == [
        (row["policy_signature"], row["status"]) for row in second_report["results"]]


def test_last_accepted_fill_event_defines_path_start(tmp_path):
    baseline, candidates, artifact, model = _fixture(tmp_path)
    receipt = baseline["episode_receipts"][0]["results"][0]["conservative_receipt"]
    receipt["quantity_attempts"] = [
        {"accepted": True, "rounded_executable_quantity": .2, "execution_price": 100,
         "trigger_bucket_ts": 10},
        {"accepted": True, "rounded_executable_quantity": .2, "execution_price": 101,
         "trigger_bucket_ts": 11},
    ]
    model["contexts"][0]["margin_usd"] = 4.02
    model["contexts"][0]["required_horizon_end_ts"] = 13
    body = {key: value for key, value in model.items() if key != "signature"}
    model["signature"] = stable_hash("conservative-shadow-research-model", body)
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=model)
    assert report["complete_replay_count"] == 1
    expected_rows = [
        {"schema": "market_microstructure_1s_v1", "bucket_ts": ts, "fresh": True,
         "valid_bbo": True, "bid": px, "ask": px + .1, "bid_qty": 1, "ask_qty": 1,
         "trade_count": 1, "buy_qty": .1, "sell_qty": .1}
        for ts, px in ((12, 103), (13, 102))
    ]
    assert report["results"][0]["terminal"]["future_path_sha256"] == hashlib.sha256(
        canonical_json(expected_rows).encode()).hexdigest()


def test_missing_model_large_grid_is_counted_without_segment_io(tmp_path, monkeypatch):
    baseline, candidates, artifact, _ = _fixture(tmp_path, model=False)
    candidates = [{**candidates[0], "policy_id": f"p{index}",
                   "policy_spec": {**candidates[0]["policy_spec"], "variant": index}}
                  for index in range(1000)]
    for candidate in candidates:
        candidate["policy_signature"] = canonical_hash("v3-policy", candidate["policy_spec"])
    candidates.sort(key=lambda item: (item["policy_signature"], item["policy_id"]))
    artifact["candidate_count"] = len(candidates)
    artifact["candidates_sha256"] = hashlib.sha256(canonical_json(candidates).encode()).hexdigest()
    monkeypatch.setattr("research.conservative_shadow_report._load_paths",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("segment IO")))
    report = build_conservative_shadow_report(tmp_path, expected_generation=GEN,
        baseline_report=baseline, policy_candidates=candidates,
        policy_artifact_receipt=artifact, research_model=None)
    assert report["candidate_replay_count"] == report["unknown_replay_count"] == 1000
    assert len(report["results"]) == 100
    assert report["results_truncated"] is True


def test_loader_roundtrips_actual_exhaustive_producer(tmp_path):
    from research.research_v3_report import _persist_exhaustive_policies
    policy = _fixture(tmp_path)[1][0]
    producer_candidate = {**policy, "policy_family": "FIXED_TARGET",
                          "supported_conservative_episodes": 0, "oos_episodes": 0}
    _persist_exhaustive_policies(tmp_path, [producer_candidate],
        epoch_id=GEN["epoch_id"], source_revision=GEN["source_revision"],
        analyzer_generation_revision=GEN["analyzer_revision"],
        tile_config_signature=GEN["tile_config_signature"])
    loaded, receipt = load_current_policy_candidates(tmp_path, GEN, policy_cycle_succeeded=True)
    assert loaded == [policy]
    assert receipt["candidate_count"] == 1
    assert receipt["artifact_verified_identity_fields"] == [
        "analyzer_generation_revision", "epoch_id", "source_revision", "tile_config_signature"]
    assert receipt["artifact_identity"] == {
        "epoch_id": GEN["epoch_id"], "source_revision": GEN["source_revision"],
        "analyzer_generation_revision": GEN["analyzer_revision"],
        "tile_config_signature": GEN["tile_config_signature"],
    }
    assert receipt["evaluation_generation"] == GEN
