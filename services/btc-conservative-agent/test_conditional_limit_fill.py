import pytest
from test_venue_quantity_observation import observation
from research.conservative_limit_fill import evaluate_conditional_limit_fill, evaluate_limit_fill
from research.conditional_quantity_execution import apply_conditional_quantity_constraints

@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('visible,outcome',[(2,'FULL_FILL'),(.4,'PARTIAL_FILL'),(0,'NO_FILL')])
def test_bilateral_conditional_fill(direction,visible,outcome):
    row=dict(schema='market_microstructure_1s_v1',symbol='BTC',bucket_ts=100,source_ts=100,observed_at_ts=100,fresh=True,valid_bbo=True,
             bid=99,ask=101,bid_qty=visible,ask_qty=visible,buy_qty=0,sell_qty=0)
    args=dict(direction=direction,requested_qty=1,aggressor_window_sec=1,symbol='BTC',
              chase_schedule=[dict(bucket_id='one',start_ts=100,end_ts=101,limit_price=101 if direction=='LONG' else 99)])
    result=evaluate_conditional_limit_fill([row],venue_quantity_observation=observation()['observation'],**args)
    assert result['final_classification']==outcome
    assert result['evidence_basis']=='DECLARED_SIMULATION_CONDITIONAL'
    assert result['venue_acceptance']=='UNKNOWN' and result['qualification_eligible'] is False
    strict=evaluate_limit_fill([row],quantity_constraints=observation()['observation'],**args)
    assert strict['supported'] is False

def test_unknown_also_tagged_and_no_fake_notional():
    result=evaluate_conditional_limit_fill([],direction='INVALID',requested_qty=1,chase_schedule=[],
        venue_quantity_observation=observation()['observation'])
    assert result['supported'] is False
    assert result['minimum_notional_decision']=='UNAVAILABLE'
    assert result['qualification_eligible'] is False

def test_amount_minimum_and_rounding_retained():
    result=apply_conditional_quantity_constraints(requested_qty=1,raw_partial_qty=.000001,
        execution_price=100,constraints=observation()['observation'],symbol='BTC')
    assert not result['accepted'] and result['minimum_lot_decision']=='FAIL'
    assert result['minimum_notional_decision']=='UNAVAILABLE'

@pytest.mark.parametrize('value',[True, -1, float('nan'), float('inf')])
def test_invalid_amounts_fail_closed_with_conditional_labels(value):
    result=apply_conditional_quantity_constraints(requested_qty=1,raw_partial_qty=value,
        execution_price=100,constraints=observation()['observation'],symbol='BTC')
    assert not result['accepted'] and result['final_classification']=='UNSUPPORTED'
    assert result['qualification_eligible'] is False and result['venue_acceptance']=='UNKNOWN'

def test_rounding_and_remaining_quantity_are_conserved():
    result=apply_conditional_quantity_constraints(requested_qty=1,raw_partial_qty=.500000009,
        accumulated_qty=.5,execution_price=100,constraints=observation()['observation'],symbol='BTC')
    assert result['rounded_executable_quantity']==.5
    assert result['accumulated_quantity_after']==1
    assert result['minimum_notional_decision']=='UNAVAILABLE'
