import sqlite3
import pytest
from test_data_sync_inventory_worker_contract import _load_worker


def test_15000_entries_resume_restart_without_repeated_range_count(tmp_path,monkeypatch):
    worker=_load_worker()
    monkeypatch.setattr(worker,'_bounded_directory_entries',lambda _: [(f'{n:05}.json',None,False,True) for n in range(15000)])
    database=tmp_path/'state.sqlite3'
    connection=worker._open_database(database,'a'*64,32*1024*1024)
    assert worker._freeze_directory_entries(connection,0,'large',tmp_path,50000,250000)==(1,15000)
    connection.close()
    # A new process/module connection must use the same durable guard.
    worker=_load_worker()
    connection=worker._open_database(database,'a'*64,32*1024*1024)
    queries=[]
    connection.set_trace_callback(queries.append)
    for _ in range(10):
        assert worker._freeze_directory_entries(connection,0,'large',tmp_path,50000,250000)==(1,15000)
    assert not any('COUNT(' in query.upper() for query in queries)
    worker._validate_spool_snapshot_integrity(connection)
    connection.close()


@pytest.mark.parametrize('statement',[
    "DELETE FROM directory_entries WHERE relative='frozen'",
    "UPDATE directory_entries SET name='changed' WHERE relative='frozen'",
    "UPDATE directory_entries SET relative='frozen' WHERE relative='unfrozen'",
    "DELETE FROM directory_snapshots WHERE relative='frozen'",
    "UPDATE directory_snapshots SET entry_count=0 WHERE relative='frozen'",
])
def test_sqlite_prevents_mutation_and_move_into_frozen_directory(tmp_path,monkeypatch,statement):
    worker=_load_worker()
    monkeypatch.setattr(worker,'_bounded_directory_entries',lambda _: [('one',None,False,True)])
    connection=worker._open_database(tmp_path/'state.sqlite3','b'*64,1024*1024)
    worker._freeze_directory_entries(connection,0,'frozen',tmp_path,50000,250000)
    connection.execute("INSERT INTO directory_entries VALUES(0,'unfrozen','two','FILE')")
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError,match='FROZEN_DIRECTORY_IMMUTABLE'):
        connection.execute(statement)
    connection.rollback()
    assert connection.execute("SELECT entry_count FROM directory_snapshots").fetchone()==(1,)
    connection.close()


def test_empty_snapshot_and_missing_guard_fail_closed_on_restart(tmp_path,monkeypatch):
    worker=_load_worker()
    monkeypatch.setattr(worker,'_bounded_directory_entries',lambda _: [])
    database=tmp_path/'state.sqlite3'
    connection=worker._open_database(database,'c'*64,1024*1024)
    assert worker._freeze_directory_entries(connection,0,'empty',tmp_path,50000,250000)==(1,0)
    connection.execute('DROP TRIGGER inventory_frozen_entry_delete')
    connection.commit();connection.close()
    with pytest.raises(worker.InventoryWorkerError,match='immutable directory guards changed'):
        worker._open_database(database,'c'*64,1024*1024)


def test_partial_freeze_transaction_rolls_back_without_false_snapshot(tmp_path):
    worker=_load_worker()
    database=tmp_path/'state.sqlite3'
    connection=worker._open_database(database,'d'*64,1024*1024)
    connection.execute('BEGIN IMMEDIATE')
    connection.execute("INSERT INTO directory_entries VALUES(0,'partial','one','FILE')")
    connection.execute("INSERT INTO directory_snapshots VALUES(0,'partial',1)")
    connection.rollback();connection.close()
    connection=worker._open_database(database,'d'*64,1024*1024)
    assert connection.execute('SELECT COUNT(*) FROM directory_entries').fetchone()==(0,)
    assert connection.execute('SELECT COUNT(*) FROM directory_snapshots').fetchone()==(0,)
    connection.close()


@pytest.mark.parametrize('corrupt',[False,True])
def test_legacy_spool_migration_checks_existing_counts_once(tmp_path,monkeypatch,corrupt):
    worker=_load_worker()
    monkeypatch.setattr(worker,'_bounded_directory_entries',lambda _: [('one',None,False,True)])
    database=tmp_path/'legacy.sqlite3'
    connection=worker._open_database(database,'e'*64,1024*1024)
    worker._freeze_directory_entries(connection,0,'frozen',tmp_path,50000,250000)
    for name in worker._immutable_directory_triggers():
        connection.execute(f'DROP TRIGGER {name}')
    connection.execute("DELETE FROM meta WHERE key='immutable_directory_v1'")
    if corrupt:
        connection.execute('DELETE FROM directory_entries')
    connection.commit();connection.close()
    if corrupt:
        with pytest.raises(worker.InventoryWorkerError,match='frozen directory count mismatch'):
            worker._open_database(database,'e'*64,1024*1024)
    else:
        connection=worker._open_database(database,'e'*64,1024*1024)
        queries=[];connection.set_trace_callback(queries.append)
        worker._freeze_directory_entries(connection,0,'frozen',tmp_path,50000,250000)
        assert not any('COUNT(' in query.upper() for query in queries)
        connection.close()
