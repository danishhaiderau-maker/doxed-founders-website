"""Explicit coverage gaps for pre-model failures; never creates simulated trades."""
from collections.abc import Mapping
import math


REASONS = frozenset({'CONTEXT_UNAVAILABLE', 'FEATURE_VALIDATION_FAILED', 'PRICE_UNAVAILABLE'})


def build_scan_counterfactual_unavailable(*, context, reason_code, observed_at_ts,
                                        source_revision=None, epoch_id=None):
    if reason_code not in REASONS:
        raise ValueError('SCAN_UNAVAILABLE_REASON_INVALID')
    if (type(observed_at_ts) not in (int, float)
            or not math.isfinite(observed_at_ts) or observed_at_ts <= 0):
        raise ValueError('SCAN_UNAVAILABLE_TIME_INVALID')
    context = context if isinstance(context, Mapping) else {}
    def identity(value):
        return value if isinstance(value, str) and 0 < len(value) <= 256 else None
    scan_id = identity(context.get('shared_ai_call_id')) or identity(context.get('trade_id'))
    return {'schema': 'scan_counterfactual_unavailable_v1',
            'event': 'COUNTERFACTUAL_COVERAGE_UNAVAILABLE',
            'observed_at_ts': observed_at_ts, 'scan_id': scan_id,
            'identity_status': 'PRESENT' if scan_id else 'MISSING',
            'source_revision': identity(source_revision), 'epoch_id': identity(epoch_id),
            'reason_code': reason_code, 'ai_evaluated': False,
            'directional_coverage': {side: {'status': 'UNAVAILABLE', 'reason_code': reason_code}
                                     for side in ('LONG', 'SHORT')},
            'simulated_trade_count': 0, 'qualification_eligible': False}


def record_scan_counterfactual_unavailable(*, append_receipt, **fields):
    """Sink acceptance is not an fsync/durability claim; failures remain visible."""
    receipt = build_scan_counterfactual_unavailable(**fields)
    try:
        accepted = append_receipt(receipt) is True
    except Exception:
        accepted = False
    return {'receipt': receipt, 'write_status': 'ACCEPTED' if accepted else 'FAILED'}
