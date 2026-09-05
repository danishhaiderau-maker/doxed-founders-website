"""Fresh source capture -> pinned disk replay -> both-side declared terminals."""
from copy import deepcopy
import hashlib
import json

import pytest

from research.policy_evidence_schema import canonical_json, generation_identity
from research.quantity_execution import build_signed_quantity_constraints
from research.entry_baseline_replay import materialize_v3_opportunity_replay
from research.declared_shadow_model import _baseline_context
from research.conservative_shadow_report import build_conservative_shadow_report
from research_entry_baselines import materialize_signal_time_baseline_schedules
from test_declared_shadow_model import contract
from test_conservative_shadow_report import _fixture
from test_entry_baseline_replay import _row


def sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def dataset(root, *, defect=None, sizing="FIXED_MARGIN"):
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
                "shared_ai_call_id": "ai-1", "event_id": "event-1"}
    source = {"epoch_id": "epoch-1", "source_revision": "rev-1", "deployed_revision": "rev-1", "tile_config_signature": "tiles-1"}
    constraints = build_signed_quantity_constraints(symbol="BTC", quantity_step="0.1", quantity_precision=1,
        min_lot="0.1", min_notional="1", captured_at="1970-01-01T00:01:41Z" if defect == "late_constraints" else "1970-01-01T00:01:39Z",
        source_revision="rev-1", source="FIXTURE")
    declaration = {"schema": "research_baseline_context_declaration_v1", "evidence_basis": "DECLARED_SIMULATION",
        "provenance": "PREDECLARED_TEST_ONLY", "declared_at_ts": 100, "sizing_mode": sizing,
        "margin_usd": 10, "requested_qty": 1, "leverage": 10, "signed_quantity_constraints": constraints,
        "input_latency_sec": 0, "input_fee_assumption_usd": 0, "slippage_model": "EXECUTABLE_BBO_PRICES",
        "atr": {"basis": "DECLARED_SIGNAL_ATR_HOLD_CONSTANT", "atr_pct": 1,
                "observed_ts": 99, "available_at_ts": 100, "provenance": "SIGNAL_ATR_NOT_FILL_OBSERVATION"},
        "coverage_policy": {"sampling_interval_sec": 1, "first_sample_offset_sec": 1, "required_horizon_sec": 4}}
    if defect == "post_signal": declaration["declared_at_ts"] = 101
    if defect == "atr_future": declaration["atr"]["available_at_ts"] = 101
    if defect == "atr_missing": declaration.pop("atr")
    if defect == "latency": declaration["input_latency_sec"] = 1
    if defect == "fees_missing": declaration.pop("input_fee_assumption_usd")
    if defect == "wrong_constraints": declaration["signed_quantity_constraints"] = {}
    if defect == "boolean_interval": declaration["coverage_policy"]["sampling_interval_sec"] = True
    if defect == "atr_time_reversed": declaration["atr"]["available_at_ts"] = 98
    opportunity = {**identity, **source, "record_id": "opp-1", "symbol": "BTC", "market": "BITFINEX",
        "signal_ts": 100, "signal_price": 100, "direction": "LONG", "raw_direction": "LONG",
        "signal_time_bbo": {"bid": 99, "ask": 101, "bid_qty": .4, "ask_qty": .4},
        "research_baseline_context_declaration": declaration}
    opportunity["baseline_schedule_snapshot"] = materialize_signal_time_baseline_schedules(opportunity)
    rows = [_row(100, bid_qty=.4, ask_qty=.4), *[_row(ts, bid=105, ask=105.1) for ts in range(101, 105)]]
    if defect == "gap": rows.pop(2)
    segment = {"schema": "market_segment_v3", "symbol": "BTC", "timeframe": "1s",
               "start_ts": 100, "end_ts": 104, "rows": rows}
    relative = f"v3/market_segments/{sha(segment)[:2]}/{sha(segment)}.json"
    binding = {**identity, "record_id": "segment-1", "context_role": "ENTRY_PATH",
        "coverage": {"conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": sha(segment), "relative_path": relative, "row_count": len(rows)}}
    files = {"v3/ledgers/opportunity.jsonl": (canonical_json(opportunity) + "\n").encode(),
             "v3/ledgers/market_segment.jsonl": (canonical_json(binding) + "\n").encode(),
             relative: canonical_json(segment).encode()}
    state = {}
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        state[name] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    (root / ".fly-sync-state.json").write_text(json.dumps(state), encoding="utf-8")
    manifest = {"dataset_epoch": "epoch-1", **source,
                "dataset_checksum": sha({"revision": "rev-1", "epoch": "epoch-1", "files": state})}
    manifest["entry_hash"] = sha(manifest)
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    generation = generation_identity(manifest, analyzer_revision="analyzer-1")
    if defect == "tampered_source":
        (root / "v3/ledgers/opportunity.jsonl").write_bytes(files["v3/ledgers/opportunity.jsonl"] + b"\n")
    return generation, manifest


def market_entries(report):
    return [(episode, next(row for row in episode["results"] if row["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL"))
            for episode in report["episode_receipts"]]


@pytest.mark.parametrize("sizing", ["FIXED_MARGIN", "FIXED_QUANTITY"])
def test_actual_captured_declaration_reaches_two_independent_fill_contexts(tmp_path, sizing):
    generation, manifest = dataset(tmp_path, sizing=sizing)
    result = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    entries = market_entries(result)
    assert len(entries) == 2 and result["same_opportunity_count"] == 1
    assert {episode["direction"] for episode, _ in entries} == {"LONG", "SHORT"}
    signatures = set()
    for episode, entry in entries:
        assert entry["outcome_state"] == "PARTIAL_FILL"
        assert entry["model_context_status"] == "SUPPORTED", entry.get("model_context_blockers")
        context = _baseline_context(entry, generation)
        assert context["atr_basis"] == "DECLARED_SIGNAL_ATR_HOLD_CONSTANT"
        assert context["measured_fill_atr"] is None and context["qualification_eligible"] is False
        assert context["source_episode_id"] == "ep-1" and context["direction"] == episode["direction"]
        assert float(context["margin_usd"]) == pytest.approx(.4 * (101 if episode["direction"] == "LONG" else 99) / 10)
        assert entry["conservative_receipt"]["measured_input_latency_sec"] is None
        signatures.add(context["signature"])
    assert len(signatures) == 2


def test_normal_producer_to_declared_terminal_for_both_directions(tmp_path):
    generation, manifest = dataset(tmp_path)
    baseline = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    _, candidates, artifact, _ = _fixture(tmp_path / "policy-fixture", model=False)
    artifact.update(evaluation_generation=generation,
        artifact_identity={"epoch_id": generation["epoch_id"], "source_revision": generation["source_revision"],
                           "analyzer_generation_revision": generation["analyzer_revision"],
                           "tile_config_signature": generation["tile_config_signature"]})
    report = build_conservative_shadow_report(tmp_path, expected_generation=generation, baseline_report=baseline,
        policy_candidates=candidates, policy_artifact_receipt=artifact, research_model=contract(generation))
    selected = [row for row in report["results"] if row.get("baseline_id") == "MARKET_ENTRY_AT_SIGNAL"]
    assert len(selected) == 2
    assert all(row["status"] == "COMPLETE" for row in selected), [(row["status"], row.get("reason_codes"), row.get("terminal")) for row in selected]
    assert len({row["episode_id"] for row in selected}) == 2
    assert all(row["terminal"]["economics_evidence_basis"] == "DECLARED_SIMULATION" for row in selected)
    assert report["live_qualification"] is False
    from research.conservative_shadow_terminal import SIMULATION_MODEL
    assert all(row["terminal"]["simulation_model"] != SIMULATION_MODEL for row in selected)
    assert all(row["terminal"]["atr_treatment"] == "DECLARED_SIGNAL_ATR_HOLD_CONSTANT" for row in selected)
    # The actual terminal producer's model identity must stay separated by the
    # existing dynamic grouping contract even when cost/sizing are identical.
    from test_dynamic_cohort_adapter import adapt, row
    held = selected[0]["terminal"]["simulation_model"]
    grouped = adapt([row(simulation_model=held), row(simulation_model=SIMULATION_MODEL)])
    assert len(grouped["groups"]) == 2
    from discovery_cohort_scorecard import build_episode_matched_scorecard
    from test_discovery_cohort_scorecard import row as static_row
    static = build_episode_matched_scorecard([
        static_row("CONSERVATIVE_BBO", simulation_model=held),
        static_row("CONSERVATIVE_BBO", episode="other-episode", simulation_model=SIMULATION_MODEL),
    ])
    assert any(code.startswith("MODEL_MISMATCH:") for code in static["blockers"])


@pytest.mark.parametrize("defect", ["post_signal", "atr_future", "atr_missing", "atr_time_reversed", "latency", "fees_missing",
                                    "wrong_constraints", "late_constraints", "boolean_interval", "gap", "tampered_source"])
def test_missing_late_wrong_or_corrupt_evidence_cannot_create_context(tmp_path, defect):
    generation, manifest = dataset(tmp_path, defect=defect)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    for _, entry in market_entries(report):
        assert "execution_model_context" not in entry
        assert entry["outcome_state"] == "UNKNOWN" or entry.get("model_context_status") == "UNKNOWN"
        if defect == "latency":
            assert "DECLARED_BASELINE_LATENCY_TREATMENT_UNSUPPORTED" in entry["rejection_codes"]
        if defect == "late_constraints":
            assert "DECLARED_BASELINE_QUANTITY_METADATA_NOT_CAUSAL" in entry["rejection_codes"]


def test_declared_atr_label_cannot_be_reclassified_as_measured_without_new_signature(tmp_path):
    generation, manifest = dataset(tmp_path)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    entry = deepcopy(market_entries(report)[0][1])
    entry["execution_model_context"]["atr_basis"] = "EXPLICIT_AT_FILL_OBSERVATION"
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        _baseline_context(entry, generation)
