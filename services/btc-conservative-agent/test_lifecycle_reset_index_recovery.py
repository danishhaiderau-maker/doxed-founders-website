import hashlib
import json
import sqlite3

import pytest
import lifecycle_index_recovery as recovery
import lifecycle_pipeline
from test_lifecycle_index_recovery import _source


def setup_reset(root):
    source = _source(root, rows=4)
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(root)
    _source(root, rows=1)
    proof = dict(schema='research_reset_boundary_proof_v1', runtime_root=str(root),
                 new_epoch_id='new', retired_epoch_id='old', source_revision='revision',
                 recovery_receipt_sha256='a'*64, writers_quiesced=True, paper_only=True,
                 live_disarmed=True, epoch_retired=True, pending_paper_orders=0,
                 open_paper_positions=0, pending_wal_records=0, pending_recovery_records=0)
    operation = dict(schema='bot_destructive_research_reset_v1', stage='COMPLETE',
                     accounting_preserved=True, new_epoch_id='new', proof=proof,
                     deleted=[str(source)], deletion={'deletion_receipt': {
                         'root':str(root), 'status':'COMPLETE', 'deleted':[str(source)],
                         'context':{'proof_sha256':hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',', ':')).encode()).hexdigest()}}})
    path = root/'research_reset_receipts'/'reset'/'operation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(operation))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), source


def setup_deleted_reset(root):
    path, digest, source = setup_reset(root)
    operation = json.loads(path.read_text())
    operation['proof']['retired_epoch_id'] = 'epoch'
    operation['deletion']['deletion_receipt']['context']['proof_sha256'] = hashlib.sha256(
        json.dumps(operation['proof'], sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    path.write_text(json.dumps(operation))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    index = root/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO dirty_lifecycle VALUES (?,?,?,?)",
            ('epoch', 'episode-0', 'policy', 'FIXED'),
        )
        connection.commit()
    finally:
        connection.close()
    source.unlink()
    return path, digest, source


def _add_deleted_ledger_proof(path, root, ledger):
    payload = json.loads(path.read_text())
    target = str(root/'v3'/'ledgers'/f'{ledger}.jsonl')
    payload['deleted'].append(target)
    payload['deletion']['deletion_receipt']['deleted'].append(target)
    path.write_text(json.dumps(payload))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_cursor(connection, ledger, offset=1):
    connection.execute(
        """INSERT OR IGNORE INTO ledger_cursor
           (ledger, source_dev, source_ino, byte_offset, source_anchor_sha256, source_mtime_ns)
           VALUES (?, 1, 1, ?, ?, 1)""",
        (ledger, offset, hashlib.sha256(b'').hexdigest()),
    )


def _insert_lifecycle_event(connection, ledger, epoch, suffix):
    connection.execute(
        """INSERT INTO lifecycle_event
           (ledger, byte_offset, row_sha256, collection_epoch_id, episode_id,
            policy_signature, research_lane, record_id, row_length)
           VALUES (?, ?, ?, ?, ?, 'policy', 'FIXED', ?, 1)""",
        (ledger, 1000 + suffix, 'a'*64, epoch, f'episode-{suffix}', f'record-{suffix}'),
    )


def test_reset_rebuild_requires_bound_proof_and_epoch(tmp_path):
    path, digest, source = setup_reset(tmp_path)
    original = source.read_bytes()
    with pytest.raises(ValueError, match='SOURCE_LEDGER_TRUNCATED'):
        lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path)
    for i in range(60):
        result = recovery.recover_reset_index(tmp_path, 'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path, operation_sha256=digest, current_epoch_id='new')
        if result['complete']: break
    assert result['complete']
    assert source.read_bytes() == original
    assert recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')['complete']
    with pytest.raises(ValueError, match='RESET_PROOF_INVALID'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='wrong')


def test_reset_rebuild_accepts_only_proven_deleted_ledger(tmp_path):
    path, digest, source = setup_deleted_reset(tmp_path)
    trigger = 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl'
    for _ in range(60):
        result = recovery.recover_reset_index(
            tmp_path, trigger, operation_path=path,
            operation_sha256=digest, current_epoch_id='new')
        if result['complete']:
            break
    assert result['complete']
    assert not source.exists()
    connection = sqlite3.connect(tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3')
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_event WHERE ledger='opportunity'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize('change', [
    'source_present', 'dirty_epoch', 'indexed_epoch', 'cursor', 'selected_evidence',
])
def test_deleted_ledger_requires_absence_and_old_epoch_index_proof(tmp_path, change):
    path, digest, source = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    if change == 'source_present':
        source.write_text('{}\n')
    else:
        connection = sqlite3.connect(index)
        try:
            if change == 'dirty_epoch':
                connection.execute("UPDATE dirty_lifecycle SET collection_epoch_id='new'")
            elif change == 'indexed_epoch':
                connection.execute("UPDATE lifecycle_event SET collection_epoch_id='new' WHERE ledger='opportunity'")
            elif change == 'cursor':
                connection.execute("UPDATE ledger_cursor SET byte_offset=0 WHERE ledger='opportunity'")
            else:
                connection.execute("DELETE FROM lifecycle_event WHERE ledger='opportunity'")
                connection.execute("DELETE FROM shared_context_event WHERE ledger='opportunity'")
            connection.commit()
        finally:
            connection.close()
    before = index.read_bytes()
    with pytest.raises(ValueError):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')
    assert index.read_bytes() == before


def test_deleted_ledger_trigger_requires_exact_complete_deletion_receipt(tmp_path):
    path, digest, source = setup_deleted_reset(tmp_path)
    payload = json.loads(path.read_text())
    payload['deletion']['deletion_receipt']['deleted'] = []
    path.write_text(json.dumps(payload))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='RESET_PROOF_INVALID'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')


def test_deleted_reset_rejects_current_epoch_evidence_on_other_ledger(tmp_path):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    digest = _add_deleted_ledger_proof(path, tmp_path, 'decision')
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        _insert_cursor(connection, 'decision')
        _insert_lifecycle_event(connection, 'decision', 'new', 90)
        connection.commit()
    finally:
        connection.close()
    before = index.read_bytes()
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')
    assert index.read_bytes() == before


def test_deleted_reset_rejects_mixed_shared_context_epoch(tmp_path):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        _insert_cursor(connection, 'shared:opportunity')
        connection.execute(
            """INSERT INTO shared_context_event
               (ledger, byte_offset, row_length, row_sha256, epoch_id, episode_id)
               VALUES ('opportunity', 9000, 1, ?, 'new', 'episode-shared')""",
            ('b'*64,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')


def test_deleted_reset_requires_every_canonical_source_absent(tmp_path):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    live = tmp_path/'v3'/'ledgers'/'decision.jsonl'
    live.write_text('{}\n')
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')


def test_deleted_reset_requires_exact_deletion_proof_for_every_referenced_ledger(tmp_path):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        _insert_cursor(connection, 'decision')
        _insert_lifecycle_event(connection, 'decision', 'epoch', 91)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')


def test_deleted_reset_rejects_unknown_cursor_name(tmp_path):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        _insert_cursor(connection, 'unknown-ledger')
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')


@pytest.mark.parametrize('epoch', [''])
def test_deleted_reset_rejects_blank_or_null_epoch(tmp_path, epoch):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        connection.execute(
            'UPDATE lifecycle_event SET collection_epoch_id=? WHERE ledger=?',
            (epoch, 'opportunity'),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new')


def test_index_schema_forbids_null_epoch_evidence(tmp_path):
    _path, _digest, _source_path = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    connection = sqlite3.connect(index)
    try:
        for table, column in (
            ('lifecycle_event', 'collection_epoch_id'),
            ('shared_context_event', 'epoch_id'),
            ('dirty_lifecycle', 'collection_epoch_id'),
        ):
            columns = {row[1]: row for row in connection.execute(f'PRAGMA table_info({table})')}
            assert columns[column][3] == 1
    finally:
        connection.close()


def test_truncated_reset_binding_preserves_legacy_shape(tmp_path):
    path, digest, _source_path = setup_reset(tmp_path)
    binding = recovery._reset_binding(
        tmp_path, 'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
        path, digest, 'new',
    )
    assert set(binding) == {
        'operation_path', 'operation_sha256', 'epoch_id', 'source_revision',
    }
    assert 'retired_epoch_id' not in binding
    assert 'deleted_ledgers' not in binding


def _drive_deleted_recovery_to_phase(root, path, digest, phase):
    state_path = root/'v3'/'lifecycle_bundle_index'/'recovery-state.json'
    for _ in range(20):
        recovery.recover_reset_index(
            root, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new',
        )
        state = json.loads(state_path.read_text())
        if state['phase'] == phase:
            return state
    raise AssertionError(f'recovery did not reach {phase}')


@pytest.mark.parametrize('phase', ['QUARANTINE', 'REBUILD', 'VERIFY', 'SWAP'])
def test_nonselected_source_appearing_at_recovery_boundary_blocks_swap(tmp_path, phase):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    _drive_deleted_recovery_to_phase(tmp_path, path, digest, phase)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    before = index.read_bytes()
    decision = tmp_path/'v3'/'ledgers'/'decision.jsonl'
    decision.write_text('{}\n')

    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new',
        )

    assert index.read_bytes() == before
    state = json.loads(
        (index.parent/'recovery-state.json').read_text()
    )
    assert state['phase'] == phase
    assert state['phase'] != 'COMPLETE'


def test_immediate_pre_swap_fence_closes_same_invocation_source_race(
    tmp_path, monkeypatch,
):
    path, digest, _source_path = setup_deleted_reset(tmp_path)
    _drive_deleted_recovery_to_phase(tmp_path, path, digest, 'SWAP')
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    before = index.read_bytes()
    decision = tmp_path/'v3'/'ledgers'/'decision.jsonl'
    original = recovery._assert_all_canonical_sources_absent
    calls = 0

    def race(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            decision.write_text('{}\n')
        return original(root)

    monkeypatch.setattr(recovery, '_assert_all_canonical_sources_absent', race)
    with pytest.raises(ValueError, match='DELETED_LEDGER_NOT_PROVEN'):
        recovery.recover_reset_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl',
            operation_path=path, operation_sha256=digest, current_epoch_id='new',
        )
    assert calls == 2
    assert index.read_bytes() == before
    state = json.loads((index.parent/'recovery-state.json').read_text())
    assert state['phase'] == 'SWAP'


def test_deleted_ledger_trigger_has_no_unproven_rotation_authority(tmp_path):
    path, digest, source = setup_deleted_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    before = index.read_bytes()
    with pytest.raises(ValueError, match='RECOVERY_TRIGGER_INVALID'):
        recovery.recover_rotated_index(
            tmp_path, 'SOURCE_LEDGER_DELETED_BY_RESET:opportunity.jsonl')
    assert index.read_bytes() == before


@pytest.mark.parametrize('change', ['hash', 'epoch', 'deletion', 'stage'])
def test_invalid_reset_receipt_does_not_touch_index(tmp_path, change):
    path, digest, source = setup_reset(tmp_path)
    index = tmp_path/'v3'/'lifecycle_bundle_index'/'lifecycle_index.sqlite3'
    before = index.read_bytes()
    if change in ('deletion','stage'):
        payload=json.loads(path.read_text())
        if change=='deletion': payload['deleted']=[]
        else: payload['stage']='PARTIAL'
        path.write_text(json.dumps(payload)); digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        recovery.recover_reset_index(tmp_path,'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path,operation_sha256='0'*64 if change=='hash' else digest,
                    current_epoch_id='wrong' if change=='epoch' else 'new')
    assert index.read_bytes()==before


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}', b'{"nested":{"x":1,"x":2}}'])
def test_reset_receipt_strict_json(raw):
    with pytest.raises(ValueError):
        recovery._strict_reset_json(raw)


def test_reset_receipt_size_bounded_before_read(tmp_path, monkeypatch):
    path, digest, source = setup_reset(tmp_path)
    monkeypatch.setattr(recovery, 'MAX_RESET_RECEIPT_BYTES', 10)
    with pytest.raises(ValueError, match='TOO_LARGE'):
        recovery.recover_reset_index(tmp_path,'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path,operation_sha256=digest,current_epoch_id='new')


def test_completed_resume_is_compact_after_live_index_advances(tmp_path, monkeypatch):
    path,digest,source=setup_reset(tmp_path)
    for _ in range(60):
        result=recovery.recover_reset_index(tmp_path,'SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                    operation_path=path,operation_sha256=digest,current_epoch_id='new')
        if result['complete']: break
    assert result['complete']
    with source.open('ab') as stream:
        stream.write(b'{"record_id":"new","epoch_id":"new","episode_id":"new"}\n')
    lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path,current_epoch_id='new')
    def prohibited(*a,**kw): raise AssertionError('bulk verification on COMPLETE')
    monkeypatch.setattr(recovery,'_reset_binding',prohibited)
    monkeypatch.setattr(recovery,'_verify_quarantine',prohibited)
    monkeypatch.setattr(recovery,'_sha',prohibited)
    for _ in range(2):
        assert recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')['complete']
    with pytest.raises(ValueError,match='RESET_PROOF_INVALID'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='wrong')
    statepath=tmp_path/'v3'/'lifecycle_bundle_index'/'recovery-state.json'
    state=json.loads(statepath.read_text())
    completion=statepath.parent/'recovery-quarantine'/recovery._directory_id(state)/'completion.json'
    completion.write_bytes(completion.read_bytes()+b' ')
    with pytest.raises(ValueError,match='COMPLETION_TAMPERED'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')
    statepath.write_text('{}')
    with pytest.raises(ValueError,match='STATE_TAMPERED'):
        recovery.resume_rotated_index_recovery(tmp_path,current_epoch_id='new')
