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
@pytest.mark.parametrize("delay", [0, 2])
def test_pinned_bilateral_conditional_replay_is_separate(tmp_path, monkeypatch, missing_quotes, delay):
    import research.baseline_execution_context as context_module
    from copy import deepcopy
    context_calls = []
    build_context = context_module.build_conditional_delayed_baseline_context
    def capture_context(**kwargs):
        context_calls.append(kwargs)
        return build_context(**kwargs)
    monkeypatch.setattr(context_module, 'build_conditional_delayed_baseline_context', capture_context)
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
        schedules = producer(opportunity)
        from research.latency_schedule_replay import TREATMENT
        opportunity['research_timing_declarations'] = [dict(
            schema='declared_submission_timing_v1', evidence_basis='DECLARED_SIMULATION',
            provenance='FIXTURE', declared_at_ts=100,
            source_capture_signature=capture['capture_signature'], delay_sec=delay,
            ordering_treatment=TREATMENT)
            for capture in schedules['directional_schedules'].values()]
        return schedules
    monkeypatch.setattr(fixture, "materialize_signal_time_baseline_schedules", conditional)
    generation, manifest = fixture.dataset(tmp_path, defect="quote_time_missing" if missing_quotes else None)
    report = materialize_v3_opportunity_replay(tmp_path, generation=generation, canonical_manifest=manifest)
    assert len(report["episode_receipts"]) == 2
    if not missing_quotes:
        assert any(build_context(**args)['status'] == 'SUPPORTED' for args in context_calls), [build_context(**args) for args in context_calls]
        supported = next(args for args in context_calls if build_context(**args)['status'] == 'SUPPORTED')
        tampered = deepcopy(supported)
        tampered['delayed_replay_receipt']['entry_receipt']['requested_qty'] = 999
        assert build_context(**tampered)['status'] == 'UNKNOWN'
    for episode in report["episode_receipts"]:
        assert len(episode['delayed_variants']) == 1
        variant = episode['delayed_variants'][0]
        assert variant['results'] == []
        delayed = next(row for row in variant['conditional_results']
                       if row['baseline_id'] == 'MARKET_ENTRY_AT_SIGNAL')
        assert delayed['venue_acceptance'] == 'UNKNOWN'
        assert delayed['qualification_eligible'] is False
        if not missing_quotes and delay == 0:
            assert delayed['model_context_status'] == 'SUPPORTED', delayed
        else:
            assert delayed['outcome_state'] == 'UNKNOWN'
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
        from test_discovery_scorecard_manifest import _load_analyzer
        import research.policy_evidence_schema as identity
        import research.conservative_shadow_report as shadow
        from research.shadow_result_stream import verify_result_stream
        analyzer = _load_analyzer('populated_conditional_publisher')
        (tmp_path / 'canonical_dataset_current.json').write_text('{}')
        output = tmp_path / 'published'
        output.mkdir()
        monkeypatch.chdir(output)
        monkeypatch.setattr(identity, 'generation_identity', lambda *a, **k: generation)
        monkeypatch.setattr(shadow, 'load_current_policy_candidates', lambda *a, **k: (candidates, artifact))
        monkeypatch.setattr(analyzer, '_atomic_mirror_analyzer_report', lambda name: output / name)
        published, _ = analyzer._write_conservative_shadow_report(tmp_path, output, report,
            policy_cycle_succeeded=True, research_model=contract(generation))
        conditional = published['conditional_report']
        assert len(published['conditional_delayed_variant_reports']) == 1
        delayed_report = published['conditional_delayed_variant_reports'][0]['report']
        assert delayed_report['qualification_eligible'] is False
        if delay == 0:
            assert delayed_report['complete_replay_count'] >= 2
        with verify_result_stream(output, delayed_report, generation) as index:
            assert index.verified_summary['verified'] is True
        assert conditional['complete_replay_count'] >= 2
        assert conditional['qualification_eligible'] is False
        with verify_result_stream(output, conditional, generation) as index:
            assert index.verified_summary['verified'] is True
            assert index.verified_summary['complete_replay_count'] >= 2
        with verify_result_stream(output, published, generation) as index:
            assert index.verified_summary['verified'] is True
