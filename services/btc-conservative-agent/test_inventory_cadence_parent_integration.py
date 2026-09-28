"""Actual worker result serialization consumed by actual parent scheduling."""
import ast
from collections import defaultdict
import hmac
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest
from test_inventory_finalize_cadence import worker


@pytest.mark.parametrize('phase,elapsed,expected', [('FINALIZE',0.005,1),('SCAN',0.005,5),('FINALIZE',0.2,5)])
def test_worker_result_drives_parent_sleep(tmp_path,monkeypatch,phase,elapsed,expected):
    module=worker()
    receipt=defaultdict(lambda:0, phase=phase, request_fingerprint='f', checkpoint_path='c',
                        database_path='d', invocation_pages_written=1,
                        invocation_elapsed_seconds=elapsed,cpu_seconds=0.004)
    monkeypatch.setattr(module,'_build_resumable',lambda *a:(None,receipt))
    # Request creation, output serialization, identity validation, BUILDING
    # handling and scheduling are production functions, not copied formulas.
    monkeypatch.setattr(module,'_load_request',lambda p,*a:json.loads(p.read_text()))
    monkeypatch.setattr(module,'_request_fingerprint',lambda *a:'f')
    monkeypatch.setattr(module,'_acquire_generation_lease',lambda *a:None)
    monkeypatch.setattr(module,'_release_generation_lease',lambda *a:None)
    monkeypatch.delattr(module.os,'nice',raising=False)
    sleeps=[]; codes=[]; errors=[]
    def run(command,**kwargs):
        path=lambda flag:Path(command[command.index(flag)+1])
        code=module.run(path('--request'),path('--result'),command[-1])
        codes.append(code)
        return SimpleNamespace(returncode=code)
    def sleep(seconds):
        sleeps.append(seconds)
        raise RuntimeError('TEST_STOP_AFTER_OBSERVED_SLEEP')
    bot=Path(os.environ.get('CADENCE_TEST_BOT_PATH') or Path(__file__).with_name('bot.py'))
    tree=ast.parse(bot.read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_data_sync_inventory_refresh_worker')
    ns=dict(Path=Path,os=os,sys=sys,uuid=uuid,json=json,hmac=hmac,__file__=str(bot),threading=threading,
            time=SimpleNamespace(time=time.time,sleep=sleep),
            subprocess=SimpleNamespace(run=run,DEVNULL=-3),logger=SimpleNamespace(error=lambda *a:errors.append(a)),
            utc_iso=lambda:'test',active_tile_registry_signature=lambda:'config',
            _collector_v22_epoch_id=lambda:'epoch-test',_runtime_git_rev=lambda:'a'*40,
            _data_sync_inventory_work_root=lambda:tmp_path,
            _data_sync_volume_root=lambda:tmp_path,_data_sync_runtime_root=lambda:tmp_path,
            _data_sync_allowed_roots=lambda:[tmp_path],_data_sync_inventory_file_budget=lambda:250,
            _data_sync_inventory_slice_seconds=lambda:0.1,
            _data_sync_cleanup_inventory_worker_orphans=lambda *a:None,
            _data_sync_inventory_cache_condition=threading.Condition(),_data_sync_async_inventory={})
    for name in ('TOP_LEVEL_RECEIPT_NAMES','EXTENSIONS','EXCLUDED_NAMES','EXCLUDED_DIR_NAMES','APPEND_PREFIX_NAMES'):
        ns['_DATA_SYNC_'+name]=set()
    ns.update(_DATA_SYNC_INVENTORY_WORKER_REQUEST_SCHEMA='request',
              _DATA_SYNC_INVENTORY_WORKER_RESULT_SCHEMA=module.RESULT_SCHEMA,
              _DATA_SYNC_INVENTORY_WORKER_NAME='worker.py',_DATA_SYNC_MANIFEST_PAGE_DEFAULT=250,
              _DATA_SYNC_INVENTORY_WORKER_SLICE_SECONDS=0.1,
              _DATA_SYNC_INVENTORY_WORKER_TIMEOUT_SECONDS=10,_DATA_SYNC_INVENTORY_WORKER_FAILURE_CODES=set())
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-parent','exec'),ns)
    ns[fn.name]()
    assert codes==[75], errors
    assert sleeps==[expected]
    assert ns['_data_sync_async_inventory']['retry_after_seconds']==expected
