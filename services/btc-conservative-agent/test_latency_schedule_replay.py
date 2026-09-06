from research.latency_schedule_replay import delayed_submission_schedule, TREATMENT
from test_conservative_limit_fill import evaluate, row, schedule
from test_conservative_limit_fill import SIGNED_CONSTRAINTS
from research.latency_schedule_replay import replay_delayed_entry
import pytest


def adapt(value,delay=1,ordering=TREATMENT):
    return delayed_submission_schedule(value,delay_sec=delay,ordering_treatment=ordering)


def test_zero_delay_actual_evaluator_equivalence():
    source=schedule(end=103)
    tape=[row(100),row(101,ask=100),row(102)]
    args=dict(direction='LONG',requested_qty=1,symbol='BTC',aggressor_window_sec=1)
    assert evaluate(tape,chase_schedule=source,**args)==evaluate(tape,chase_schedule=adapt(source,0)['schedule'],**args)


def test_prearrival_touch_cannot_fill():
    source=schedule(end=103)
    tape=[row(100,ask=100),row(101),row(102)]
    args=dict(direction='LONG',requested_qty=1,symbol='BTC',aggressor_window_sec=1)
    assert evaluate(tape,chase_schedule=source,**args)['outcome']=='FILL'
    delayed=evaluate(tape,chase_schedule=adapt(source)['schedule'],**args)
    assert delayed['supported'] is True and delayed['outcome']!='FILL'


def test_expiry_is_never_extended_or_converted_to_fill():
    result=adapt(schedule(end=101),2)
    assert result['status']=='NO_ACTIVE_INTERVALS' and result['schedule']==[]
    assert result['expired_before_arrival_bucket_ids']==['chase_3']


def test_reprice_gap_and_partial_depth_replayed():
    source=schedule(end=102)+schedule(start=102,end=105,limit=101,bucket='next')
    result=adapt(source)
    assert [(r['start_ts'],r['end_ts']) for r in result['schedule']]==[(101,102),(103,105)]
    tape=[row(101),row(102,ask=100),row(103,ask=101,ask_qty=.4),row(104,ask=102)]
    receipt=evaluate(tape,direction='LONG',requested_qty=1,symbol='BTC',chase_schedule=result['schedule'],aggressor_window_sec=1)
    assert receipt['final_classification']=='PARTIAL_FILL'


def test_unsupported_ordering_and_subseconds():
    assert adapt(schedule(),ordering='UNKNOWN')['status']=='UNSUPPORTED'
    assert adapt(schedule(),delay=.5)['status']=='UNSUPPORTED'
    assert adapt(schedule(start=100.5))['status']=='UNSUPPORTED'


def test_missing_postarrival_tape_stays_unsupported():
    receipt=evaluate([row(100,ask=100)],direction='LONG',requested_qty=1,
                     chase_schedule=adapt(schedule(end=103))['schedule'],symbol='BTC')
    assert receipt['supported'] is False


def replay(tape,**changes):
    args=dict(schedule=schedule(end=103),delay_sec=1,ordering_treatment=TREATMENT,
        tape=tape,direction='LONG',requested_qty=1,quantity_constraints=SIGNED_CONSTRAINTS,symbol='BTC')
    return replay_delayed_entry(**{**args,**changes})


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_entry_only_reexecutes_both_sides_and_binds_schedule(side):
    touch=row(100,ask=100) if side=='LONG' else row(100,bid=100)
    tape=[touch,row(101),row(102)]
    zero=replay(tape,delay_sec=0,direction=side)
    delayed=replay(tape,direction=side)
    assert zero['entry_receipt']['final_classification']=='FULL_FILL'
    assert delayed['status']=='ENTRY_REPLAY_SUPPORTED'
    assert delayed['entry_receipt']['final_classification']=='NO_FILL'
    assert delayed['scope']=='ENTRY_ONLY_NOT_FULL_LIFECYCLE'
    assert delayed['qualification_eligible'] is False
    assert zero['source_schedule_sha256']==delayed['source_schedule_sha256']
    assert zero['scenario_sha256']!=delayed['scenario_sha256']
    assert zero['evaluated_schedule_sha256']!=delayed['evaluated_schedule_sha256']


def test_entry_missing_evidence_and_empty_intervals_never_claim_fill():
    assert replay([])['status']=='UNKNOWN'
    empty=replay([],delay_sec=5)
    assert empty['status']=='NO_ACTIVE_INTERVALS'
    assert empty['entry_receipt'] is None and empty['evaluated_schedule_sha256'] is None
    assert replay([],ordering_treatment='UNKNOWN')['status']=='UNKNOWN'


@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_nonfinite_generation_is_unsupported(value):
    source=schedule()
    source[0]['generation']=value
    assert adapt(source)['status']=='UNSUPPORTED'


def test_unserializable_delay_fails_closed():
    assert adapt(schedule(),delay=10**5000)['status']=='UNSUPPORTED'


def test_replay_identity_binds_all_inputs_not_only_schedule():
    from copy import deepcopy
    tape=[row(100),row(101),row(102)]
    baseline=replay(tape)
    changed_tape=deepcopy(tape)
    changed_tape[1]['extra_observation']='different'
    constraints=deepcopy(SIGNED_CONSTRAINTS)
    constraints['extra_constraint']='different'
    for changed in [replay(changed_tape),replay(tape,direction='SHORT'),
                    replay(tape,requested_qty=2),replay(tape,quantity_constraints=constraints),
                    replay(tape,symbol='OTHER')]:
        assert changed['scenario_sha256']==baseline['scenario_sha256']
        assert changed['replay_input_sha256']!=baseline['replay_input_sha256']
    assert replay(tape)['replay_input_sha256']==baseline['replay_input_sha256']


def test_replay_receipt_hash_binds_result_and_input_identity():
    from research.latency_schedule_replay import _strict_sha256
    result=replay([row(100),row(101),row(102)])
    assert result['replay_receipt_sha256']==_strict_sha256({
        k:v for k,v in result.items() if k!='replay_receipt_sha256'})
    assert result['qualification_eligible'] is False


def test_nonfinite_tape_metadata_cannot_produce_replay_identity():
    tape=[row(100),row(101),row(102)]
    tape[1]['extra_observation']=float('nan')
    result=replay(tape)
    assert result['status']=='UNKNOWN'
    assert result['replay_input_sha256'] is None
    assert result['entry_receipt'] is None
