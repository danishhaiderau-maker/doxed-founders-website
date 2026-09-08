import ast
import json
import os
from pathlib import Path
import pytest
from research import local_scan_reconciliation as module
from research.mirror_generation_lease import MirrorGenerationLease
from test_local_scan_reconciliation import fixture


def test_real_held_lease_preserved_success_and_failure(tmp_path,monkeypatch):
    store,args=fixture(tmp_path,monkeypatch)
    with MirrorGenerationLease(args['data_root']).acquire(timeout_seconds=0) as lease:
        assert module.reconcile_scans(**args,held_lease=lease)['index_caught_up']
        assert lease.held
        with pytest.raises(ValueError,match='EXPECTED_SOURCE'):
            module.reconcile_scans(**args,held_lease=lease,expected_source={'wrong':True})
        assert lease.held
    with pytest.raises(ValueError,match='HELD_LEASE'):
        module.reconcile_scans(**args,held_lease=lease)
    with MirrorGenerationLease(tmp_path/'other').acquire(timeout_seconds=0) as wrong:
        with pytest.raises(ValueError,match='HELD_LEASE'):
            module.reconcile_scans(**args,held_lease=wrong)
        assert wrong.held


def test_actual_engine_writer_serializes_section_before_mirror(tmp_path,monkeypatch):
    from research import discovery_scorecard_publication as publication,local_dynamic_input
    path=Path(__file__).with_name('analyzer_research_engine_v62.py')
    fn=next(n for n in ast.parse(path.read_text(encoding='utf-8-sig')).body
        if isinstance(n,ast.FunctionDef) and n.name=='_write_discovery_scorecard_report')
    target=tmp_path/'report.json'; marker=object(); token=object()
    monkeypatch.setattr(publication,'build_discovery_scorecard_publication',lambda *a,**k:{'generation':{'source_revision':'r','tile_config_signature':'c'}})
    monkeypatch.setattr(local_dynamic_input,'_source',lambda t:{'token':t is token})
    def reconcile(**kwargs):
        assert kwargs['held_lease'] is marker and kwargs['expected_source']=={'token':True}
        return {'index_caught_up':True,'observed_joined_opportunity_rows':2,'qualification_eligible':False}
    monkeypatch.setattr(module,'reconcile_scans',reconcile)
    def mirror(name):
        assert json.loads(target.read_text())['scan_census_observed_coverage']['observed_joined_opportunity_rows']==2
        return True
    ns=dict(Path=Path,json=json,os=os,__file__=str(path),DISCOVERY_COHORT_SCORECARD_REPORT_FILE=str(target),
        _CURRENT_MIRROR_GENERATION_LEASE=marker,_CURRENT_MIRROR_COHERENCE_TOKEN=token,_atomic_mirror_analyzer_report=mirror)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'writer','exec'),ns)
    report,_=ns['_write_discovery_scorecard_report'](tmp_path,{},{} )
    assert not report['scan_census_observed_coverage']['qualification_eligible']
