from types import SimpleNamespace
import pytest
from research.venue_quantity_observation import capture_venue_quantity_observation
from research.runtime_baseline_declaration import build_runtime_baseline_declaration as build
import test_runtime_baseline_declaration as fixtures

def inputs():
    args=fixtures.RuntimeDeclarationTests().inputs()
    exchange=SimpleNamespace(id='bitfinex',market=lambda symbol:{'id':'tBTCUSD',
        'precision':{'amount':8},'limits':{'amount':{'min':'0.00001'},'cost':{'min':None}}})
    observation=capture_venue_quantity_observation(exchange,ccxt_symbol='BTC/USD',evidence_symbol='tBTCUSD',
        source_revision='rev',captured_at='1970-01-01T00:01:40Z',adapter_version='4.5.78')['observation']
    args['quantity_capture']={'receipt':None,'observation':observation,'reasons':['VENUE_MIN_NOTIONAL_UNAVAILABLE']}
    return args

def test_conditional_declaration_keeps_observed_metadata_and_denies_authority():
    args=inputs(); result=build(**args); declaration=result['declaration']
    assert result['status']=='DECLARED_CONDITIONAL_DIAGNOSTIC'
    assert declaration['schema']=='research_baseline_context_declaration_v2'
    assert declaration['evidence_basis']=='DECLARED_SIMULATION_CONDITIONAL'
    assert declaration['min_notional_treatment']=='UNMODELED_VENUE_ACCEPTANCE_CONDITIONAL'
    assert declaration['venue_acceptance']=='UNKNOWN' and declaration['qualification_eligible'] is False
    assert 'signed_quantity_constraints' not in declaration
    assert declaration['venue_quantity_observation']==args['quantity_capture']['observation']
    assert 'VENUE_MIN_NOTIONAL_UNAVAILABLE' in declaration['limitations']

@pytest.mark.parametrize('defect',['future','identity','hash','atr','fees','margin','invalid_strict'])
def test_other_gates_not_bypassed(defect):
    args=inputs()
    if defect=='future': args['captured_at_ts']=99
    if defect=='identity': args['source_revision']='other'
    if defect=='hash': args['quantity_capture']['observation']['min_lot']='1'
    if defect=='atr': args['context']['cycle_3m_universe']['captured_ts']=None
    if defect=='fees': args['maker_fee_rate']=None
    if defect=='margin': args['margin_usd']=None
    if defect=='invalid_strict': args['quantity_capture']['receipt']={'schema':'bad'}
    assert build(**args)['declaration'] is None

def test_valid_strict_receipt_stays_v1_even_with_observation():
    args=inputs();args['quantity_capture']['receipt']=fixtures.RuntimeDeclarationTests().inputs()['quantity_capture']['receipt']
    result=build(**args)
    assert result['declaration']['schema']=='research_baseline_context_declaration_v1'
    assert result['status']=='DECLARED_DIAGNOSTIC'

@pytest.mark.parametrize('reasons',[None, {'SECRET_VALUE':True}, ['SECRET_VALUE','VENUE_MIN_NOTIONAL_UNAVAILABLE']])
def test_only_allowlisted_reason_codes_are_retained(reasons):
    args=inputs();args['quantity_capture']['reasons']=reasons
    result=build(**args)
    assert result['declaration'] is not None
    assert 'SECRET_VALUE' not in str(result)
