import pytest
from research.latency_schedule_replay import replay_conditional_delayed_entry, replay_delayed_entry, TREATMENT, _strict_sha256
from test_venue_quantity_observation import observation


def args(direction='LONG', visible=1):
    return dict(schedule=[dict(bucket_id='a',start_ts=100,end_ts=102,limit_price=101 if direction=='LONG' else 99)],
        delay_sec=1,ordering_treatment=TREATMENT,direction=direction,requested_qty=1,symbol='BTC',
        venue_quantity_observation=observation()['observation'],
        tape=[dict(schema='market_microstructure_1s_v1',symbol='BTC',bucket_ts=101,source_ts=101,
            observed_at_ts=101,fresh=True,valid_bbo=True,bid=99,ask=101,bid_qty=visible,
            ask_qty=visible,buy_qty=0,sell_qty=0)])


@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('visible,outcome',[(1,'FULL_FILL'),(.4,'PARTIAL_FILL'),(0,'NO_FILL')])
def test_bilateral_delayed_conditional(direction,visible,outcome):
    values=args(direction,visible)
    result=replay_conditional_delayed_entry(**values)
    assert result['status']=='ENTRY_REPLAY_SUPPORTED'
    assert result['entry_receipt']['final_classification']==outcome
    assert result['venue_acceptance']=='UNKNOWN' and result['qualification_eligible'] is False
    assert result['replay_receipt_sha256']==_strict_sha256({k:v for k,v in result.items() if k!='replay_receipt_sha256'})
    values['quantity_constraints']=values.pop('venue_quantity_observation')
    assert replay_delayed_entry(**values)['status']=='UNKNOWN'


@pytest.mark.parametrize('defect',['expiry','ordering','quote_time','tamper'])
def test_non_success_still_conditional_and_hashed(defect):
    values=args()
    if defect=='expiry': values['delay_sec']=2
    elif defect=='ordering': values['ordering_treatment']='UNKNOWN'
    elif defect=='quote_time': values['tape'][0].pop('observed_at_ts'); values['tape'][0].pop('source_ts')
    else: values['venue_quantity_observation']['min_lot']='99'
    result=replay_conditional_delayed_entry(**values)
    assert result['status']==('NO_ACTIVE_INTERVALS' if defect=='expiry' else 'UNKNOWN')
    assert result['evidence_basis']=='DECLARED_SIMULATION_CONDITIONAL'
    assert result['qualification_eligible'] is False and result['live_arming_authorized'] is False
    assert result['replay_receipt_sha256']==_strict_sha256({k:v for k,v in result.items() if k!='replay_receipt_sha256'})


def test_input_identity_binds_quantity_and_tape():
    values=args(); first=replay_conditional_delayed_entry(**values)
    values['requested_qty']=.5
    second=replay_conditional_delayed_entry(**values)
    assert second['replay_input_sha256']!=first['replay_input_sha256']
    values['tape'][0]['bid_qty']=.25
    assert replay_conditional_delayed_entry(**values)['replay_input_sha256']!=second['replay_input_sha256']
