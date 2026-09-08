import hashlib
import pytest
from lifecycle_bundles import _open_incremental_index, _index_ledger_chunk, _dirty_lifecycle_rows, collect_lifecycle_rows
from test_lifecycle_pipeline import _append, _patch_provenance
from test_lane_no_order_disposition import audit, KEY, PROV


@pytest.mark.parametrize('mismatch', [None, 'shared_ai_call_id', 'collection_epoch_id', 'config_signature'])
def test_actual_shared_row_backfill_joins_without_source_identity_rewrite(tmp_path, monkeypatch, mismatch):
    _patch_provenance(monkeypatch)
    lane = {**audit(), 'record_id': 'lane-resolution', 'opportunity_id': 'opportunity:' + KEY.episode_id}
    shared = {**PROV, 'ledger': 'opportunity', 'record_id': lane['opportunity_id'],
              'epoch_id': KEY.collection_epoch_id, 'episode_id': KEY.episode_id,
              'shared_ai_call_id': lane['shared_ai_call_id'], 'event_id': 'scan-event',
              'market_context_segment_refs': [], 'baseline_directional_schedules': {'LONG': {}, 'SHORT': {}}}
    if mismatch:
        shared[mismatch] = 'conflict'
    _append(tmp_path, shared)
    _append(tmp_path, lane)
    root = tmp_path / 'v3' / 'ledgers'
    if mismatch == 'config_signature':
        import json
        stored = json.loads((root / 'opportunity.jsonl').read_text())
        stored['config_signature'] = 'conflict'
        (root / 'opportunity.jsonl').write_text(json.dumps(stored) + '\n')
    raw_before = (root / 'opportunity.jsonl').read_bytes()
    connection = _open_incremental_index(tmp_path)
    try:
        # Ordinary legacy scan advances but cannot index lane-less context.
        _index_ledger_chunk(connection, root / 'opportunity.jsonl', 'opportunity', max_bytes=100000, max_rows=1)
        # Emulate an already-current legacy index without the additive table/cursor.
        connection.execute('DELETE FROM shared_context_event')
        connection.execute("DELETE FROM ledger_cursor WHERE ledger='shared:opportunity'")
        connection.commit()
        assert connection.execute('SELECT COUNT(*) FROM shared_context_event').fetchone()[0] == 0
        _index_ledger_chunk(connection, root / 'lifecycle.jsonl', 'lifecycle', max_bytes=100000, max_rows=1)
        normal_cursor = connection.execute("SELECT byte_offset FROM ledger_cursor WHERE ledger='opportunity'").fetchone()[0]
        receipt = _index_ledger_chunk(connection, root / 'opportunity.jsonl', 'opportunity', max_bytes=100000, max_rows=1)
        assert receipt['rows_scanned'] == 1
        assert connection.execute("SELECT byte_offset FROM ledger_cursor WHERE ledger='opportunity'").fetchone()[0] == normal_cursor
        _, rows = _dirty_lifecycle_rows(connection, tmp_path, maximum=1)[0]
        binding = rows[0]['shared_context_binding']
        assert collect_lifecycle_rows(tmp_path)[KEY][0]['shared_context_binding'] == binding
        if mismatch:
            assert binding['status'] == 'UNBOUND' and binding['references'] == []
        else:
            assert binding['status'] == 'BOUND'
            ref = binding['references'][0]
            assert ref['row_sha256'] == hashlib.sha256(raw_before).hexdigest()
            assert ref['source_row'].get('research_lane') is None
            assert ref['source_row'].get('policy_signature') is None
            assert ref['source_row']['record_id'] == lane['opportunity_id']
        assert (root / 'opportunity.jsonl').read_bytes() == raw_before
        (root / 'opportunity.jsonl').write_bytes(raw_before.replace(b'scan-event', b'evil-event'))
        with pytest.raises(ValueError, match='SHARED_CONTEXT_SOURCE_CHANGED'):
            _dirty_lifecycle_rows(connection, tmp_path, maximum=1)
    finally:
        connection.close()


def test_ambiguous_refs_are_unbound_and_unchanged():
    from lifecycle_shared_context import attach_shared_context
    lane = {**audit(), 'opportunity_id': 'opportunity:' + KEY.episode_id}
    original = {**PROV, 'epoch_id': KEY.collection_epoch_id, 'episode_id': KEY.episode_id,
                'shared_ai_call_id': lane['shared_ai_call_id'], 'record_id': lane['opportunity_id']}
    ref = {'ledger': 'opportunity', 'source_row': original, 'row_sha256': 'a' * 64}
    bound = attach_shared_context(KEY, [lane], [ref, ref])[0]['shared_context_binding']
    assert bound['status'] == 'UNBOUND' and bound['references'] == []
    assert 'SHARED_CONTEXT_AMBIGUOUS' in bound['blockers']
    assert 'policy_signature' not in original and 'research_lane' not in original
