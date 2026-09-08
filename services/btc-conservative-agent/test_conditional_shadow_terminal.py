import pytest
from test_conservative_shadow_terminal import _inputs, _rebind, _sha
from test_declared_shadow_model import contract
from test_venue_quantity_observation import observation
from research.conservative_shadow_terminal import evaluate_shadow_terminal, evaluate_conditional_shadow_terminal
from research.conservative_limit_fill import evaluate_conditional_limit_fill


def inputs(direction='LONG'):
    values=_inputs()
    values['entry_receipt'].update(schema='conditional_limit_fill_receipt_v1',
        evidence_basis='DECLARED_SIMULATION_CONDITIONAL',qualification_eligible=False,
        venue_acceptance='UNKNOWN',symbol='BTC',direction=direction,
        quantity_constraints=observation()['observation'])
    values['cost_model'].update(calculation_mode='DECLARED_EXECUTION_RATE_MODEL_V1',
        declared_contract=contract(values['generation']),cost_provenance='DECLARED_SIMULATION_CONDITIONAL')
    if direction=='SHORT':
        for row in values['future_path_rows']:
            row['ask']=200-row['bid']; row['bid']=row['ask']-.1
    return _rebind(values)


@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_conditional_exact_terminal_pnl_and_strict_rejection(direction):
    values=inputs(direction)
    result=evaluate_conditional_shadow_terminal(**values)
    assert result['status']=='COMPLETE', result['blockers']
    assert result['gross_pnl_usd']==pytest.approx(1.2)
    exit_notional=41.2 if direction=='LONG' else 38.8
    assert result['net_pnl_usd']==pytest.approx(1.2-(40*.001+exit_notional*.002))
    assert result['conditional_profitability_supported'] is True
    assert result['profitability_supported'] is False and result['qualification_eligible'] is False
    assert result['live_qualification'] is False and result['venue_acceptance']=='UNKNOWN'
    assert result['economics_evidence_basis']=='DECLARED_SIMULATION_CONDITIONAL'
    assert result['receipt_sha256']==_sha({k:v for k,v in result.items() if k!='receipt_sha256'})
    assert evaluate_shadow_terminal(**values)['status']=='UNKNOWN'


@pytest.mark.parametrize('defect',['quantity','hash','coverage','authority','cost'])
def test_conditional_failures_remain_labelled_unknown(defect):
    values=inputs()
    if defect=='quantity':
        values['position_context']['margin_usd']=99; _rebind(values)
    elif defect=='hash': values['entry_receipt_sha256']='bad'
    elif defect=='coverage': values['required_horizon_end_ts']=15
    elif defect=='authority':
        values['entry_receipt']['qualification_eligible']=True; _rebind(values)
    elif defect=='cost':
        values['cost_model']['cost_provenance']='DECLARED_SIMULATION'; _rebind(values)
    result=evaluate_conditional_shadow_terminal(**values)
    assert result['status']=='UNKNOWN' and result['net_pnl_usd'] is None
    assert result['qualification_eligible'] is False and result['venue_acceptance']=='UNKNOWN'
    assert result['conditional_profitability_supported'] is False


def test_actual_conditional_fill_round_trip():
    values=inputs()
    entry=evaluate_conditional_limit_fill([
        dict(schema='market_microstructure_1s_v1',symbol='BTC',bucket_ts=10,
            source_ts=10,observed_at_ts=10,fresh=True,valid_bbo=True,bid=99.9,ask=100,
            bid_qty=1,ask_qty=.4,trade_count=0,buy_qty=0,sell_qty=0)],
        direction='LONG',requested_qty=.4,aggressor_window_sec=1,symbol='BTC',
        chase_schedule=[dict(bucket_id='entry',start_ts=10,end_ts=11,limit_price=100)],
        venue_quantity_observation=observation()['observation'])
    assert entry['supported'] is True
    values['entry_receipt']={**entry,'symbol':'BTC'}
    _rebind(values)
    result=evaluate_conditional_shadow_terminal(**values)
    assert result['status']=='COMPLETE',result['blockers']
    assert result['net_pnl_usd']==pytest.approx(1.0776)
