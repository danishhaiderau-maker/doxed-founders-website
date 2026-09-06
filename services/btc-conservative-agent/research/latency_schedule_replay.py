"""Declared whole-second submission delay, not measured exchange latency."""
from copy import deepcopy
import hashlib
import json

from research.conservative_limit_fill import _normalise_schedule, evaluate_limit_fill


TREATMENT = 'FIXED_EXPIRY_CANCEL_BEFORE_REPLACE'


def delayed_submission_schedule(schedule, *, delay_sec, ordering_treatment):
    """Delay each submission, keeping explicit original interval ends fixed.

    This declared model assumes cancellation/expiry at the recorded interval
    end, followed by replacement arrival after the declared delay. It does NOT
    model delayed cancellation acknowledgements or resting old orders during
    replacement. Unknown ordering cannot be recovered from schedule alone.
    Output must be replayed against tape; it contains no reused fill evidence.
    """
    def unsupported(reason):
        return {'status':'UNSUPPORTED','reason_codes':[reason],'schedule':None}
    if type(delay_sec) is not int or delay_sec < 0:
        return unsupported('WHOLE_SECOND_NONNEGATIVE_DELAY_REQUIRED')
    if ordering_treatment != TREATMENT:
        return unsupported('CANCEL_REPLACE_ORDERING_UNSUPPORTED')
    try:
        normalized, source_hash = _normalise_schedule(schedule)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return unsupported('SOURCE_SCHEDULE_INVALID')
    delayed=[]
    expired=[]
    for interval in normalized:
        arrival=interval['start_ts']+delay_sec
        if arrival >= interval['end_ts']:
            expired.append(interval['bucket_id'])
            continue
        delayed.append({**deepcopy(interval),'start_ts':arrival})
    body={'schema':'declared_submission_delay_schedule_v1',
          'source_schedule_sha256':source_hash,'delay_sec':delay_sec,
          'ordering_treatment':ordering_treatment,'schedule':delayed,
          'expired_before_arrival_bucket_ids':expired,
          'evidence_basis':'DECLARED_SIMULATION','qualification_eligible':False,
          'requires_fresh_fill_replay':True}
    signature=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {**body,'scenario_sha256':signature,
            'status':'READY' if delayed else 'NO_ACTIVE_INTERVALS', 'reason_codes':[]}


def replay_delayed_entry(*, schedule, delay_sec, ordering_treatment, tape,
                         direction, requested_qty, quantity_constraints, symbol):
    """Entry-only research result; never authorizes lifecycle qualification."""
    adapted=delayed_submission_schedule(schedule,delay_sec=delay_sec,
                                        ordering_treatment=ordering_treatment)
    output={'schema':'declared_delayed_entry_replay_v1',
            'scope':'ENTRY_ONLY_NOT_FULL_LIFECYCLE','qualification_eligible':False,
            'evidence_basis':'DECLARED_SIMULATION','live_arming_authorized':False,
            'source_schedule_sha256':adapted.get('source_schedule_sha256'),
            'scenario_sha256':adapted.get('scenario_sha256'),
            'evaluated_schedule_sha256':None,'entry_receipt':None}
    if adapted['status']!='READY':
        return {**output,'status':adapted['status'] if adapted['status']=='NO_ACTIVE_INTERVALS' else 'UNKNOWN',
                'reason_codes':adapted['reason_codes'], 'timing_status':adapted['status']}
    _,evaluated_hash=_normalise_schedule(adapted['schedule'])
    output['evaluated_schedule_sha256']=evaluated_hash
    try:
        receipt=evaluate_limit_fill(tape,direction=direction,requested_qty=requested_qty,
            chase_schedule=adapted['schedule'],quantity_constraints=quantity_constraints,
            symbol=symbol,aggressor_window_sec=1)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        return {**output,'status':'UNKNOWN','reason_codes':['DELAYED_ENTRY_INPUT_UNSUPPORTED']}
    return {**output,'status':'ENTRY_REPLAY_SUPPORTED' if receipt.get('supported') is True else 'UNKNOWN',
            'reason_codes':list(receipt.get('negative_reasons') or []),'entry_receipt':receipt}
