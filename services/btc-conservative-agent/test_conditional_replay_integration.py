from types import SimpleNamespace
import pytest
import test_declared_directional_context_integration as fixture
from research.venue_quantity_observation import capture_venue_quantity_observation
from research.entry_baseline_replay import materialize_v3_opportunity_replay


@pytest.mark.parametrize("declaration", [["invalid"], "invalid", 12])
def test_malformed_declaration_is_unknown_not_analyzer_crash(declaration):
    from research.entry_baseline_replay import replay_episode
    report = replay_episode({"episode_id": "e", "opportunity_id": "o",
        "directional_capture": {"research_context_declaration": declaration}})
    assert report["results"]
    assert all(row["outcome_state"] == "UNKNOWN" for row in report["results"])


@pytest.mark.parametrize("missing_quotes", [False, True])
def test_pinned_bilateral_conditional_replay_is_separate(tmp_path, monkeypatch, missing_quotes):
    producer = fixture.materialize_signal_time_baseline_schedules
    def conditional(opportunity):
        declaration = opportunity["research_baseline_context_declaration"]
        declaration.pop("signed_quantity_constraints")
        exchange = SimpleNamespace(id="bitfinex", market=lambda _: {
            "id": "tBTCUSD", "precision": {"amount": 8},
            "limits": {"amount": {"min": .00001}, "cost": {"min": None}}})
        observation = capture_venue_quantity_observation(exchange, ccxt_symbol="BTC/USD",
            evidence_symbol="BTC", captured_at="1970-01-01T00:01:39Z",
            source_revision="rev-1", adapter_version="4.5.78")["observation"]
        declaration.update(schema="research_baseline_context_declaration_v2",
            evidence_basis="DECLARED_SIMULATION_CONDITIONAL", qualification_eligible=False,
            venue_acceptance="UNKNOWN", min_notional_treatment="UNMODELED_VENUE_ACCEPTANCE_CONDITIONAL",
            venue_quantity_observation=observation)
        return producer(opportunity)
    monkeypatch.setattr(fixture, "materialize_signal_time_baseline_schedules", conditional)
    generation, manifest = fixture.dataset(tmp_path, defect="quote_time_missing" if missing_quotes else None)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    assert len(report["episode_receipts"]) == 2
    for episode in report["episode_receipts"]:
        selected = [row for row in episode["conditional_results"] if row["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL"]
        assert len(selected) == 1
        if missing_quotes:
            assert selected[0]["outcome_state"] == "UNKNOWN"
            assert "QUOTE_OBSERVATION_TIME_UNPROVEN" in selected[0]["rejection_codes"]
        else:
            assert selected[0]["outcome_state"] in {"PARTIAL_FILL", "FULL_FILL"}
            assert selected[0]["model_context_status"] == "SUPPORTED", selected[0].get("model_context_blockers")
            context = selected[0]["execution_model_context"]
            assert context["context_evidence_basis"] == "DECLARED_SIMULATION_CONDITIONAL"
            assert context["qualification_eligible"] is False
            from research.declared_shadow_model import conditional_baseline_context, _baseline_context
            assert conditional_baseline_context(selected[0], generation) == context
            with pytest.raises(ValueError, match="CONDITIONAL_BASELINE_NOT_STRICT"):
                _baseline_context(selected[0], generation)
        assert selected[0]["venue_acceptance"] == "UNKNOWN"
        assert selected[0]["qualification_eligible"] is False
        assert not any(row["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL" for row in episode["results"])
    assert report["summaries"]["MARKET_ENTRY_AT_SIGNAL"]["full_fills"] == 0
    assert report["summaries"]["MARKET_ENTRY_AT_SIGNAL"]["partial_fills"] == 0
    assert report["summaries"]["MARKET_ENTRY_AT_SIGNAL"]["directional_evaluations"] == 0
    summary = report["conditional_summaries"]["MARKET_ENTRY_AT_SIGNAL"]
    assert summary["opportunities"] == 1
    assert summary["directional_evaluations"] == 2
    assert sum(summary[key] for key in ("full_fills", "partial_fills", "no_fills", "unknown")) == 2
    assert summary["unknown"] == (2 if missing_quotes else 0)
    assert summary["qualification_eligible"] is False
    if not missing_quotes:
        from research.conservative_shadow_report import build_conditional_shadow_report
        from test_conservative_shadow_report import _fixture
        from test_declared_shadow_model import contract
        _, candidates, artifact, _ = _fixture(tmp_path / "policy-fixture", model=False)
        artifact.update(evaluation_generation=generation, artifact_identity={
            "epoch_id": generation["epoch_id"], "source_revision": generation["source_revision"],
            "analyzer_generation_revision": generation["analyzer_revision"],
            "tile_config_signature": generation["tile_config_signature"]})
        terminal_report = build_conditional_shadow_report(tmp_path,
            expected_generation=generation, baseline_report=report,
            policy_candidates=candidates, policy_artifact_receipt=artifact,
            research_model=contract(generation))
        selected = [row for row in terminal_report["results"]
                    if row.get("baseline_id") == "MARKET_ENTRY_AT_SIGNAL"]
        assert len(selected) == 2
        assert all(row["status"] == "COMPLETE" for row in selected), selected
        assert all(row["terminal"]["conditional_profitability_supported"] for row in selected)
        assert all(row["terminal"]["venue_acceptance"] == "UNKNOWN" for row in selected)
        assert terminal_report["qualification_eligible"] is False
        assert terminal_report["profitability_supported"] is False
