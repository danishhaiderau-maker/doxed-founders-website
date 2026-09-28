import hashlib
import json
import pytest
from research_timing_capture import materialize_timing_declarations, load_runtime_timing_config, TREATMENT


def inputs():
    config = dict(schema='research_timing_config_v1', epoch_id='epoch-test',
        source_revision='a'*40, tile_config_signature='b'*64, activated_at_ts=99,
        delay_seconds=[0, 2], ordering_treatment=TREATMENT, evidence_basis='DECLARED_SIMULATION')
    row = dict(config, signal_ts=100)
    row['research_timing_config'] = config
    row['research_timing_config_sha256'] = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    snapshot = {'directional_schedules': {s: {'direction': s, 'capture_signature': s} for s in ('LONG', 'SHORT')}}
    return row, snapshot


def test_both_sides_without_order_mutation():
    row, snapshot = inputs()
    before = json.dumps(row, sort_keys=True)
    result = materialize_timing_declarations(row, snapshot)
    assert result['status'] == 'DECLARED'
    assert len(result['declarations']) == 4
    assert {r['source_capture_signature'] for r in result['declarations']} == {'LONG', 'SHORT'}
    assert json.dumps(row, sort_keys=True) == before


@pytest.mark.parametrize('field,value', [('activated_at_ts', 101), ('activated_at_ts', float('nan')),
    ('delay_seconds', [True]), ('delay_seconds', [2, 2]), ('delay_seconds', [301]),
    ('epoch_id', 'other'), ('ordering_treatment', 'UNKNOWN')])
def test_invalid_config_is_not_assumed(field, value):
    row, snapshot = inputs()
    row['research_timing_config'][field] = value
    assert materialize_timing_declarations(row, snapshot)['status'] == 'UNAVAILABLE'


def test_missing_and_bad_hash():
    assert materialize_timing_declarations({}, {})['reason'] == 'TIMING_CONFIG_MISSING'
    row, snapshot = inputs()
    row['research_timing_config_sha256'] = '0'*64
    assert materialize_timing_declarations(row, snapshot)['reason'] == 'TIMING_CONFIG_HASH_MISMATCH'


@pytest.mark.parametrize('snapshot', [None, [], {'directional_schedules': None},
    {'directional_schedules': {'LONG': None, 'SHORT': {}}}])
def test_malformed_capture_does_not_interrupt_writer(snapshot):
    row, _ = inputs()
    assert materialize_timing_declarations(row, snapshot)['status'] == 'UNAVAILABLE'


def test_runtime_loader_pins_config(tmp_path):
    row, _ = inputs()
    path = tmp_path / 'timing.json'
    path.write_text(json.dumps(row['research_timing_config']))
    env = {'BTC_RESEARCH_TIMING_CONFIG_FILE': str(path),
           'BTC_RESEARCH_TIMING_CONFIG_SHA256': row['research_timing_config_sha256']}
    assert load_runtime_timing_config(env)['research_timing_config'] == row['research_timing_config']
    path.write_text('{}')
    assert load_runtime_timing_config(env)['research_timing_config_status'] == 'UNAVAILABLE'
    assert load_runtime_timing_config({}) == {}


def test_duplicate_json_is_not_silently_selected(tmp_path):
    path = tmp_path / 'timing.json'
    path.write_text('{"x":1,"x":2}')
    digest = hashlib.sha256(b'{"x":2}').hexdigest()
    result = load_runtime_timing_config({'BTC_RESEARCH_TIMING_CONFIG_FILE': str(path),
        'BTC_RESEARCH_TIMING_CONFIG_SHA256': digest})
    assert result['research_timing_config_status'] == 'UNAVAILABLE'


def test_bridge_preserves_config_and_rejects_conflicting_sources():
    from research_v3_bridge import _signal_time_baseline_inputs
    row, snapshot = inputs()
    projected = _signal_time_baseline_inputs(row)
    opportunity = {**row, **projected}
    assert materialize_timing_declarations(opportunity, snapshot)['status'] == 'DECLARED'
    conflict = _signal_time_baseline_inputs(row, {'research_timing_config': {}}, row)
    assert conflict['research_timing_config'] is None
    assert materialize_timing_declarations({**row, **conflict}, snapshot)['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('verdict', ['APPROVE', 'REJECT', 'NO_TRADE'])
def test_actual_store_persists_both_sides_for_every_verdict(tmp_path, monkeypatch, verdict):
    import research_v3_store as store_module
    row, _ = inputs()
    row.update(record_id='opportunity-test', opportunity_id='opportunity-test',
        episode_id='episode-test', direction='LONG', raw_direction='LONG',
        ai_decision=verdict, symbol='BTC', signal_price=100,
        signal_time_bbo={'bid': 99, 'ask': 101, 'bid_qty': 1, 'ask_qty': 1})
    monkeypatch.setattr(store_module, '_collection_provenance', lambda: {
        'source_revision': row['source_revision'], 'deployed_revision': row['source_revision'],
        'tile_config_signature': row['tile_config_signature']})
    store = store_module.V3EvidenceStore(tmp_path, epoch_id=row['epoch_id'])
    store.append('opportunity', row)
    actual = json.loads(store.ledger_path('opportunity').read_text().strip())
    assert actual['ai_decision'] == verdict
    assert actual['research_timing_capture_status']['status'] == 'DECLARED'
    captures = actual['baseline_schedule_snapshot']['directional_schedules']
    assert {d['source_capture_signature'] for d in actual['research_timing_declarations']} == {
        captures['LONG']['capture_signature'], captures['SHORT']['capture_signature']}
    assert len(actual['research_timing_declarations']) == 4
