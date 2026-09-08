import json
import sqlite3
import pytest
from research import local_scan_reconciliation as module
from test_local_scan_reconciliation import fixture


def test_cli_actual_store_publishes_observed_only(tmp_path,monkeypatch,capsys):
    store,args=fixture(tmp_path,monkeypatch)
    cli=[value for key,value in args.items() for value in ('--'+key.replace('_','-'),str(value))]
    assert module.main(cli)==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='OBSERVED_ONLY'
    assert result['observed_joined_opportunity_rows']==1
    assert result['verified_reference_page'][0]['row_sha256']
    assert result['exhaustive_collection_status']=='UNKNOWN'
    assert not result['qualification_eligible']


@pytest.mark.parametrize('error',[ValueError('private payload'),sqlite3.OperationalError('private path')])
def test_cli_failure_sanitized_and_no_count(monkeypatch,capsys,error):
    def fail(**kwargs): raise error
    monkeypatch.setattr(module,'reconcile_scans',fail)
    assert module.main(['--repo-root','x','--data-root','y','--source-revision','z','--config-signature','c'])==1
    raw=capsys.readouterr().out; result=json.loads(raw)
    assert 'private' not in raw and 'observed_joined_opportunity_rows' not in result
    assert result['status']=='UNKNOWN' and not result['qualification_eligible']
