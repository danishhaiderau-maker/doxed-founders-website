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
        # R3 rewrite (HM #873 a1): market_segment input is invalid; assert BLOCK, not legacy success.
        assert_r3_block(report, 'report')


import pytest as _pytest_r17
from research import r3_quarantine as _r3q_r17
from r3_test_assert import assert_r3_block


@_pytest_r17.fixture(autouse=True)
def _legacy_limit_fill_1s_unquarantined(monkeypatch):
    # These tests pin the legacy 1 s limit-fill math; production has LIMIT_FILL_1S retired (R17, RK #847 cond 4),
    # covered by test_r17_retired_sources.py.  Kept (not deleted) pending the book_limit_entry rebuild.
    monkeypatch.setattr(_r3q_r17, "ACTIVE", set(_r3q_r17.ACTIVE) - {"LIMIT_FILL_1S"})
