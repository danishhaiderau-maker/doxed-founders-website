"""Actual runtime wrappers and real report writers, confined to temporary data."""
import ast
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('condition',['held_gate','failed_marker','missing_marker','unknown_os_error'])
def test_actual_report_writers_respect_reset_barriers(tmp_path,condition):
    names=['shadow_vs_live_entry_report.json','approval_ev_report.json',
           'fill_quality_report.json','execution_funnel_summary.json']
    for name in names:
        (tmp_path/name).write_text('sentinel')
    if condition=='failed_marker':
        folder=tmp_path/'research_reset_receipts'; folder.mkdir()
        (folder/'ACTIVE_RESET.json').write_text('{"stage":"FAILED"}')
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    selected={'_run_reset_guarded_report','_refresh_execution_reports_guarded',
              'refresh_shadow_vs_live_entry_report','_refresh_shadow_vs_live_entry_report_unlocked'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in selected]
    def lstat(path):
        if condition=='unknown_os_error':
            raise PermissionError('unavailable')
        return os.lstat(path)
    gate=threading.RLock()
    env=dict(os=SimpleNamespace(lstat=lstat,path=os.path,getcwd=lambda:str(tmp_path)),
        json=json,_research_write_gate=gate,_fresh_collection_lock=threading.Lock(),
        _data_sync_runtime_root=lambda:tmp_path,utc_iso=lambda:'now',
        EXECUTION_FIX_VERSION='fixture',ANALYZER_SYNC_ID='fixture',
        SHADOW_VS_LIVE_ENTRY_FILE='shadow_vs_live_entry.jsonl',
        SHADOW_VS_LIVE_ENTRY_REPORT=names[0],logger=SimpleNamespace(debug=lambda *a:None))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bot.py','exec'),env)
    results=[]
    def invoke():
        results.extend([env['_refresh_execution_reports_guarded'](str(tmp_path)),
                        env['refresh_shadow_vs_live_entry_report'](str(tmp_path))])
    if condition=='held_gate':
        with gate:
            worker=threading.Thread(target=invoke)
            worker.start(); worker.join(timeout=3)
            assert not worker.is_alive()
    else:
        invoke()
    assert len(results)==2
    if condition=='missing_marker':
        assert 'funnel_summary' in results[0] and results[1]['sample_count']==0
        for name in names:
            assert isinstance(json.loads((tmp_path/name).read_text()),dict)
    else:
        assert all(result['refresh_status']=='SKIPPED' for result in results)
        assert all((tmp_path/name).read_text()=='sentinel' for name in names)
