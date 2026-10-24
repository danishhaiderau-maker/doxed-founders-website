from copy import deepcopy
import pytest
import research.baseline_execution_context as module
from research.latency_schedule_replay import replay_delayed_entry, TREATMENT
from research.entry_baseline_replay import materialize_v3_opportunity_replay
from test_declared_directional_context_integration import dataset


@pytest.mark.parametrize('value',[None,[],False,{'entry_receipt':[]}])
def test_malformed_delayed_receipt_is_unknown(value):
    result=module.build_declared_delayed_baseline_context(timing_declaration={},tape=[],
        delayed_replay_receipt=value,entry_evidence={},entry_binding={},capture={})
    assert result['status']=='UNKNOWN'


@pytest.mark.parametrize('delay',[0,2])
def test_zero_delay_pinned_context_and_replay_tamper(tmp_path,monkeypatch,delay):
    import test_declared_directional_context_integration as fixture_module
    original_row=fixture_module._row
    def changing_row(ts,**kwargs):
        if ts==101:
            kwargs.update(bid=100.2,ask=100.3)
        if ts>=102:
            kwargs.update(bid=106,ask=106.1)
        if ts>=103:
            kwargs.update(bid=107,ask=107.1)
        row=original_row(ts,**kwargs)
        if ts==101:
            # REALISTIC_V1: the undelayed resting SHORT limit fills at 101 only
            # because a buy print trades through it; the delayed submission
            # arrives later and is marketable on arrival.
            row.update(buy_qty=2,buy_vwap=row['ask'],trade_count=1)
        return row
    monkeypatch.setattr(fixture_module,'_row',changing_row)
    producer=fixture_module.materialize_signal_time_baseline_schedules
    def capture_timing(opportunity):
        schedules=producer(opportunity)
        opportunity['research_timing_declarations']=[dict(
            schema='declared_submission_timing_v1',evidence_basis='DECLARED_SIMULATION',
            provenance='FIXTURE',declared_at_ts=100,source_capture_signature=capture['capture_signature'],
            delay_sec=delay,ordering_treatment=TREATMENT)
            for capture in schedules['directional_schedules'].values()]
        return schedules
    monkeypatch.setattr(fixture_module,'materialize_signal_time_baseline_schedules',capture_timing)
    generation,manifest=dataset(tmp_path)
    captured=[]
    original=module.build_declared_directional_baseline_context
    def intercept(**kwargs):
        captured.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(module,'build_declared_directional_baseline_context',intercept)
    baseline_report=materialize_v3_opportunity_replay(tmp_path,generation=generation,canonical_manifest=manifest)
    # R3 rewrite: delay contexts are built from market_segment rows, which are L1 copies.
    # No delay (zero or positive) may produce a SUPPORTED context or a replay receipt.
    # The R3 gate fires before any directional context is built: nothing to pin or tamper.
    assert not any(original(**value)['status']=='SUPPORTED' for value in captured)
    assert 'INVALID_R3_MARKET_SEGMENT_INPUT' in __import__('json').dumps(baseline_report,default=str)
    for episode in baseline_report['episode_receipts']:
        for variant in episode.get('delayed_variants',[]):
            assert all(r.get('model_context_status')!='SUPPORTED' for r in variant['results'])

import pytest as _pytest_r17
from research import r3_quarantine as _r3q_r17


@_pytest_r17.fixture(autouse=True)
def _legacy_limit_fill_1s_unquarantined(monkeypatch):
    # These tests pin the legacy 1 s limit-fill math; production has LIMIT_FILL_1S retired (R17, RK #847 cond 4),
    # covered by test_r17_retired_sources.py.  Kept (not deleted) pending the book_limit_entry rebuild.
    monkeypatch.setattr(_r3q_r17, "ACTIVE", set(_r3q_r17.ACTIVE) - {"LIMIT_FILL_1S"})
