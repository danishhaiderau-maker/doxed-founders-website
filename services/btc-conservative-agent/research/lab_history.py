"""Bounded diagnostic pages of legacy LAB rows, not trade qualification/totals."""
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import stat

MAX_BYTES = 262144
MAX_ROWS = 100


class HistoryUnavailable(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _identity(path):
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode):
        raise HistoryUnavailable('SOURCE_NOT_REGULAR')
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def _encode(value, secret):
    payload = base64.urlsafe_b64encode(_json(value)).decode()
    return payload + '.' + hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _decode(cursor, secret):
    try:
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise ValueError()
        payload, signature = cursor.split('.')
        if not hmac.compare_digest(signature, hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(payload))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HistoryUnavailable('CURSOR_INVALID') from None


def _project(row):
    result = {}
    for key in ('ts', 'study_id', 'trade_id', 'research_lane', 'epoch_id', 'policy_version',
                'policy_signature', 'direction', 'entry_outcome', 'exit_reason', 'shared_ai_call_id', 'prompt_id'):
        value = row.get(key)
        result[key] = value[:256] if isinstance(value, str) else None
    for key in ('net_pnl_usd', 'fill_price', 'entry', 'margin_usdt', 'fill_delay_sec'):
        value = row.get(key)
        result[key] = value if type(value) in (int, float) and math.isfinite(value) else None
    result['filled'] = row.get('filled') if type(row.get('filled')) is bool else None
    snapshot = row.get('ai_snapshot')
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    result['original_ai'] = {}
    for key in ('decision', 'direction', 'model_id'):
        value = snapshot.get(key)
        result['original_ai'][key] = value[:256] if isinstance(value, str) else None
    for key in ('long_score', 'short_score', 'win_prob'):
        value = snapshot.get(key)
        result['original_ai'][key] = value if type(value) in (int, float) and math.isfinite(value) else None
    result['fill_assumption'] = 'LEGACY_INSTANT_OR_PRICE_TOUCH_SIMULATION_NOT_EXCHANGE_VERIFIED'
    from research.lab_accounting import completed_legacy_lab
    result['completed_strategy_exit']=completed_legacy_lab(row)
    result['economics_basis']='LEGACY_GROSS_BEFORE_COSTS'
    result['gross_before_costs_usd']=result['net_pnl_usd']
    result['net_after_costs_usd']=None
    result['costs_status']='UNMODELED'
    result['qualification_allowed'] = False
    return result


def read_history(path, *, identity, secret, lane='', cursor=None):
    """identity() supplies current epoch/reset generation/policy allowlist.

    Strict file-generation cursor: even append invalidates it. This sacrifices
    continuation across writes rather than claiming an unverified stable prefix.
    No request reads more than MAX_BYTES; no cohort aggregation or dedup claims.
    """
    if not isinstance(lane, str) or len(lane) > 64:
        raise HistoryUnavailable('LANE_INVALID')
    current = identity()
    policies = current.get('policies') or {}
    if not current.get('epoch'):
        raise HistoryUnavailable('CURRENT_EPOCH_UNAVAILABLE')
    if lane and lane not in policies:
        raise HistoryUnavailable('LANE_INVALID')
    path = Path(path)
    try:
        source = _identity(path)
    except FileNotFoundError:
        raise HistoryUnavailable('SOURCE_MISSING') from None
    binding = {'source': source, 'current': current, 'lane': lane}
    offset = 0
    if cursor:
        saved = _decode(cursor, secret)
        if saved.get('binding') != binding:
            raise HistoryUnavailable('CURSOR_GENERATION_CHANGED')
        offset = saved.get('offset')
        if type(offset) is not int or not 0 <= offset <= source[2]:
            raise HistoryUnavailable('CURSOR_INVALID')
    with path.open('rb') as stream:
        stream.seek(offset)
        raw = stream.read(min(MAX_BYTES, source[2] - offset))
    if _identity(path) != source or identity() != current:
        raise HistoryUnavailable('SOURCE_GENERATION_CHANGED')
    last_newline = raw.rfind(b'\n')
    complete = raw[:last_newline+1] if last_newline >= 0 else b''
    if not complete and raw and offset+len(raw) < source[2]:
        raise HistoryUnavailable('ROW_EXCEEDS_PAGE_BYTE_BUDGET')
    records = complete.splitlines(keepends=True)[:MAX_ROWS]
    next_offset = offset + sum(map(len, records))
    incomplete_tail = bool(offset+len(raw) == source[2] and len(complete) < len(raw)
                           and len(records) == len(complete.splitlines()))
    if incomplete_tail:
        next_offset = source[2]
    rows = []
    excluded = {}
    def exclude(reason):
        excluded[reason] = excluded.get(reason, 0) + 1
    for record in records:
        try:
            row = json.loads(record)
        except (ValueError, UnicodeError):
            exclude('MALFORMED_ROW'); continue
        if not isinstance(row, dict) or row.get('schema') != 'shadow_lane_outcome_v1':
            exclude('SCHEMA_UNAVAILABLE'); continue
        if row.get('epoch_id') != current['epoch'] or row.get('collection_epoch_id', current['epoch']) != current['epoch']:
            exclude('EPOCH_MISSING_OR_DIFFERENT'); continue
        if row.get('collection_mode') != 'LAB':
            exclude('NOT_LAB'); continue
        actual_lane = row.get('research_lane')
        if not isinstance(actual_lane, str):
            exclude('LANE_INVALID'); continue
        if lane and actual_lane != lane:
            exclude('LANE_FILTER'); continue
        expected = policies.get(actual_lane)
        if not expected or row.get('policy_version') != expected:
            exclude('POLICY_MISSING_OR_DIFFERENT'); continue
        rows.append(_project(row))
    eof = next_offset == source[2]
    return {'schema': 'lab_history_page_v1', 'status': 'AVAILABLE', 'epoch_id': current['epoch'],
        'lane': lane or 'ALL', 'available_lanes': sorted(policies), 'rows': rows,
        'coverage': {'scope': 'PAGE_ONLY_NOT_RECONCILED_COHORT', 'offset': offset,
            'next_offset': next_offset, 'source_bytes': source[2], 'bytes_read': len(raw),
            'records_scanned': len(records), 'returned_rows': len(rows), 'excluded': excluded,
            'incomplete_tail': incomplete_tail, 'end_of_pinned_file': eof,
            'whole_file_in_this_page': offset == 0 and eof,
            'duplicate_reconciliation_performed': False},
        'next_cursor': None if eof else _encode({'binding': binding, 'offset': next_offset}, secret),
        'qualification_allowed': False, 'cohort_totals': None,
        'policy_filter_basis': 'CURRENT_POLICY_VERSION_ONLY_EXACT_SIGNATURE_NOT_RECONCILED',
        'fill_assumption': 'LEGACY_INSTANT_OR_PRICE_TOUCH_SIMULATION_NOT_EXCHANGE_VERIFIED'}
