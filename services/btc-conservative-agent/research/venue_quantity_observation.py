"""Partial venue metadata for conditional research, never venue qualification."""
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

SCHEMA = 'venue_quantity_observation_v1'
FIELDS = ('schema', 'symbol', 'source_revision', 'captured_at', 'exchange_id',
          'market_id', 'adapter_version', 'amount_precision_semantics',
          'quantity_precision', 'quantity_step', 'min_lot', 'min_notional',
          'evidence_basis', 'qualification_eligible')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _positive(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def validate_venue_quantity_observation(raw, *, symbol=None, source_revision=None):
    if not isinstance(raw, Mapping):
        return None, ['VENUE_QUANTITY_OBSERVATION_MISSING']
    payload = {key: raw.get(key) for key in FIELDS}
    reasons = []
    try:
        if raw.get('payload_sha256') != _hash(payload): reasons.append('OBSERVATION_HASH_INVALID')
    except (TypeError, ValueError): reasons.append('OBSERVATION_HASH_INVALID')
    if payload['schema'] != SCHEMA: reasons.append('OBSERVATION_SCHEMA_INVALID')
    for key in ('symbol', 'source_revision', 'market_id', 'adapter_version'):
        if not isinstance(payload[key], str) or not payload[key].strip(): reasons.append('OBSERVATION_' + key.upper() + '_MISSING')
    if symbol is not None and payload['symbol'] != symbol: reasons.append('OBSERVATION_SYMBOL_MISMATCH')
    if source_revision is not None and payload['source_revision'] != source_revision: reasons.append('OBSERVATION_REVISION_MISMATCH')
    try:
        stamp = datetime.fromisoformat(payload['captured_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None: raise ValueError()
    except (ValueError, TypeError, AttributeError): reasons.append('OBSERVATION_TIME_INVALID')
    if payload['exchange_id'] != 'bitfinex' or payload['amount_precision_semantics'] != 'BITFINEX_AMOUNT_DECIMAL_PLACES_OVERRIDE':
        reasons.append('OBSERVATION_ADAPTER_SEMANTICS_UNSUPPORTED')
    precision = payload['quantity_precision']
    if type(precision) is not int or not 0 <= precision <= 18:
        reasons.append('OBSERVATION_PRECISION_INVALID')
    elif _positive(payload['quantity_step']) != Decimal(1).scaleb(-precision):
        reasons.append('OBSERVATION_STEP_MISMATCH')
    if _positive(payload['min_lot']) is None: reasons.append('OBSERVATION_MIN_LOT_INVALID')
    notional = payload['min_notional']
    if not isinstance(notional, Mapping):
        reasons.append('OBSERVATION_NOTIONAL_STATUS_MISSING')
    elif notional.get('status') == 'UNAVAILABLE':
        if notional.get('value') is not None or notional.get('reason') != 'NOT_EXPOSED_BY_ADAPTER':
            reasons.append('OBSERVATION_NOTIONAL_INVALID')
    elif notional.get('status') != 'OBSERVED' or _positive(notional.get('value')) is None:
        reasons.append('OBSERVATION_NOTIONAL_INVALID')
    if payload['evidence_basis'] != 'VENUE_METADATA_PARTIAL_OBSERVATION' or payload['qualification_eligible'] is not False:
        reasons.append('OBSERVATION_NOT_QUALIFICATION_AUTHORITY')
    return (None, sorted(set(reasons))) if reasons else ({**payload, 'payload_sha256': raw['payload_sha256']}, [])


def capture_venue_quantity_observation(exchange, *, ccxt_symbol, evidence_symbol,
                                      captured_at, source_revision, adapter_version):
    try:
        market = exchange.market(ccxt_symbol)
    except Exception:
        return {'observation': None, 'diagnostic_metadata_supported': False, 'qualification_eligible': False,
                'reasons': ['VENUE_METADATA_UNAVAILABLE_OR_INVALID']}
    try:
        amount = market['precision']['amount']
        lot = market['limits']['amount']['min']
        cost = (market['limits'].get('cost') or {}).get('min')
        payload = dict(schema=SCHEMA, symbol=evidence_symbol, source_revision=source_revision,
                       captured_at=captured_at, exchange_id=exchange.id, market_id=market['id'],
                       adapter_version=adapter_version,
                       amount_precision_semantics='BITFINEX_AMOUNT_DECIMAL_PLACES_OVERRIDE',
                       quantity_precision=amount,
                       quantity_step=str(Decimal(1).scaleb(-amount)) if type(amount) is int and 0 <= amount <= 18 else None,
                       min_lot=str(lot), min_notional=(
                           {'status': 'UNAVAILABLE', 'value': None, 'reason': 'NOT_EXPOSED_BY_ADAPTER'}
                           if cost is None else {'status': 'OBSERVED', 'value': str(cost)}),
                       evidence_basis='VENUE_METADATA_PARTIAL_OBSERVATION', qualification_eligible=False)
        payload['payload_sha256'] = _hash(payload)
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        return {'observation': None, 'diagnostic_metadata_supported': False, 'qualification_eligible': False,
                'reasons': ['VENUE_METADATA_UNAVAILABLE_OR_INVALID']}
    normalized, reasons = validate_venue_quantity_observation(payload, symbol=evidence_symbol, source_revision=source_revision)
    return {'observation': normalized, 'diagnostic_metadata_supported': normalized is not None,
            'qualification_eligible': False, 'reasons': reasons}
