import copy
import pytest
from research.declared_shadow_model import (conditional_baseline_context, _baseline_context,
    build_conditional_declared_research_model, build_declared_research_model, sha)
from research.policy_evidence_schema import stable_hash
from test_declared_shadow_model import contract, GEN
from test_venue_quantity_observation import observation


def fixture(direction='LONG'):
    gen={**GEN, 'source_revision':'a'*40}
    receipt=dict(schema='conditional_limit_fill_receipt_v1', evidence_basis='DECLARED_SIMULATION_CONDITIONAL',
        qualification_eligible=False, venue_acceptance='UNKNOWN', symbol='BTC', direction=direction,
        quantity_constraints=observation()['observation'])
    body=dict(schema='baseline_execution_model_context_v1', generation=gen,
        entry_receipt_sha256=sha(receipt), atr_pct_at_fill=1, leverage=2, margin_usd=100,
        atr_basis='DECLARED_SIGNAL_ATR_HOLD_CONSTANT', context_evidence_basis='DECLARED_SIMULATION_CONDITIONAL',
        measured_fill_atr=None, research_context_declaration_sha256='a'*64,
        directional_capture_signature='directional-entry-capture-test',
        timing_basis='BASELINE_EXECUTION_TIMESTAMPS_UNCHANGED', latency_provenance='TEST',
        source_evidence_sha256=['b'*64], qualification_eligible=False, venue_acceptance='UNKNOWN',
        min_notional_treatment='UNMODELED_VENUE_ACCEPTANCE_CONDITIONAL')
    entry=dict(baseline_id='baseline', supported=True, outcome_state='FULL_FILL', conservative_receipt=receipt,
        execution_model_context={**body,'signature':stable_hash('baseline-execution-model-context',body)})
    return gen,entry


@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_explicit_conditional_only(direction):
    gen,entry=fixture(direction)
    assert conditional_baseline_context(entry,gen)['qualification_eligible'] is False
    with pytest.raises(ValueError,match='NOT_STRICT'): _baseline_context(entry,gen)
    report=dict(generation=gen,episode_receipts=[dict(episode_id='e',opportunity_id='o',results=[],conditional_results=[entry])])
    args=dict(baseline_report=report,policy_candidates=[],expected_generation=gen)
    assert build_declared_research_model(contract(gen),**args)['contexts']==[]
    model=build_conditional_declared_research_model(contract(gen),**args)
    assert len(model['contexts'])==1 and 'input_blockers' not in model['contexts'][0]
    assert model['model_id'].startswith('conditional:') and model['qualification_eligible'] is False
    assert model['provenance']=='DECLARED_SIMULATION_CONDITIONAL'


@pytest.mark.parametrize('defect',['receipt','signature','qualification','venue','atr','timing','quantity'])
def test_invalid_conditional_context_rejected(defect):
    gen,entry=fixture()
    ctx=entry['execution_model_context']
    if defect=='receipt': entry['conservative_receipt']['direction']='SHORT'
    elif defect=='signature': ctx['signature']='bad'
    else:
        if defect=='qualification': ctx['qualification_eligible']=True
        elif defect=='venue': ctx['venue_acceptance']='ACCEPTED'
        elif defect=='atr': ctx['measured_fill_atr']=1
        elif defect=='timing': ctx['timing_basis']='INVENTED'
        elif defect=='quantity':
            entry['conservative_receipt']['quantity_constraints']['min_lot']='99'
            ctx['entry_receipt_sha256']=sha(entry['conservative_receipt'])
        ctx['signature']=stable_hash('baseline-execution-model-context',{k:v for k,v in ctx.items() if k!='signature'})
    with pytest.raises(ValueError): conditional_baseline_context(entry,gen)
