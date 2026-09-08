from copy import deepcopy
from types import SimpleNamespace
import pytest
from research.venue_quantity_observation import capture_venue_quantity_observation as capture, validate_venue_quantity_observation as validate, _hash
from research.quantity_execution import validate_signed_quantity_constraints, apply_quantity_constraints


def observation(cost=None):
    exchange = SimpleNamespace(id='bitfinex', precisionMode=3, market=lambda symbol: {
        'id': 'tBTCF0:USTF0', 'precision': {'amount': 8},
        'limits': {'amount': {'min': '0.00004'}, 'cost': {'min': cost}}})
    return capture(exchange, ccxt_symbol='BTC/USDT:USDT', evidence_symbol='BTC',
                   captured_at='2026-09-09T00:00:00Z', source_revision='a'*40,
                   adapter_version='4.5.78')


def test_partial_metadata_preserved_without_fabricated_notional():
    result = observation()
    assert result['diagnostic_metadata_supported']
    row = result['observation']
    assert row['quantity_step'] == '1E-8'
    assert row['min_lot'] == '0.00004'
    assert row['adapter_version'] == '4.5.78'
    assert row['min_notional'] == {'status':'UNAVAILABLE','value':None,'reason':'NOT_EXPOSED_BY_ADAPTER'}
    assert row['qualification_eligible'] is False
    assert validate(row, symbol='BTC', source_revision='a'*40)[1] == []


def test_strict_v1_still_rejects_observation():
    row = observation()['observation']
    assert validate_signed_quantity_constraints(row, symbol='BTC')[0] is None
    assert apply_quantity_constraints(requested_qty=1, raw_partial_qty=1, execution_price=100,
                                      constraints=row, symbol='BTC')['accepted'] is False


def test_hash_and_identity_fail_closed():
    row = observation()['observation']; row['min_lot']='0.001'
    assert 'OBSERVATION_HASH_INVALID' in validate(row)[1]
    row=observation()['observation']
    assert 'OBSERVATION_SYMBOL_MISMATCH' in validate(row, symbol='ETH')[1]
    assert 'OBSERVATION_REVISION_MISMATCH' in validate(row, source_revision='b'*40)[1]

def test_qualification_authority_cannot_be_enabled_by_rehash():
    row=observation()['observation']; row['qualification_eligible']=True
    row['payload_sha256']=_hash({k:v for k,v in row.items() if k!='payload_sha256'})
    assert 'OBSERVATION_NOT_QUALIFICATION_AUTHORITY' in validate(row)[1]


@pytest.mark.parametrize('key', ['quantity_step','quantity_precision','min_lot','captured_at','adapter_version','source_revision','min_notional'])
def test_missing_fields_rejected_even_with_recomputed_hash(key):
    row=observation()['observation']; row[key]=None
    row['payload_sha256']=_hash({k:v for k,v in row.items() if k!='payload_sha256'})
    assert validate(row)[0] is None


@pytest.mark.parametrize('cost', [0,-1,'invalid'])
def test_invalid_published_notional_not_reclassified_unavailable(cost):
    assert observation(cost)['observation'] is None


def test_observed_notional_preserved_and_still_not_live_authority():
    row=observation('5')['observation']
    assert row['min_notional']=={'status':'OBSERVED','value':'5'}
    assert row['qualification_eligible'] is False


def test_actual_capture_exposes_partial_observation_but_keeps_strict_rejection():
    from research.venue_quantity_constraints import capture_quantity_constraints
    market={'id':'tBTCF0:USTF0','precision':{'amount':8},
            'limits':{'amount':{'min':0.00004},'cost':{'min':None}},
            'info':{'private':'MUST_NOT_COPY'}}
    calls=[]
    exchange=SimpleNamespace(id='bitfinex',market=lambda symbol:calls.append(symbol) or market)
    result=capture_quantity_constraints(exchange,ccxt_symbol='BTC/USDT:USDT',evidence_symbol='BTC',
        captured_at='2026-09-09T00:00:00Z',source_revision='a'*40)
    assert len(calls)==1
    assert result['supported'] is False and result['receipt'] is None
    assert result['reasons']==['VENUE_MIN_NOTIONAL_UNAVAILABLE']
    assert result['diagnostic_metadata_supported'] is True
    assert 'MUST_NOT_COPY' not in str(result)
