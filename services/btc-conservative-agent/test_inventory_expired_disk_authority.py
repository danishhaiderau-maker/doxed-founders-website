"""Async delivery must agree with actual retained-generation authority."""
import ast
import hmac
import re
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest


def harness(retained_at, *, served=False, expires=0):
    source = Path(__file__).with_name('bot.py')
    names = {'_data_sync_request_async_inventory','_data_sync_inventory_generation'}
    nodes = [n for n in ast.parse(source.read_text(encoding='utf-8')).body
             if isinstance(n,ast.FunctionDef) and n.name in names]
    generation = 'a'*64
    identity = dict(source_git_rev='1'*40, collection_epoch_id='epoch', tile_registry_signature='tile')
    state = dict(status='CURRENT', rows=None, generation={'generation_id':generation, 'bundle_identity':dict(identity)},
                 generation_id=generation, served_since_refresh=served, expires_at=expires,
                 completed_refresh_nonce='b'*32, refreshing=False)
    starts=[]
    class Thread:
        def __init__(self, **kwargs): self.kwargs=kwargs
        def start(self): starts.append(self.kwargs)
    ns=dict(time=SimpleNamespace(monotonic=lambda:10000), re=re, hmac=hmac, uuid=uuid,
        threading=SimpleNamespace(Thread=Thread), utc_iso=lambda:'now',
        _data_sync_inventory_cache_condition=threading.Condition(),
        _data_sync_memory_identity_payload=lambda:dict(identity),
        _data_sync_async_inventory=state, _DATA_SYNC_INVENTORY_GENERATION_TTL_SECONDS=7200,
        _data_sync_inventory_generations={generation:dict(storage='disk_pages_v2',retained_at=retained_at)} if retained_at is not None else {},
        _data_sync_bundle_retention_allowed_locked=lambda _:True,
        _start_data_sync_bundle_reservation_hydration=lambda:None,
        _DATA_SYNC_BUNDLE_REGISTRY=SimpleNamespace(ready=True),
        _data_sync_inventory_refresh_worker=lambda:None)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
    return ns, starts


@pytest.mark.parametrize('retained_at,served,expires,force',[
    (1,False,0,False), (None,False,0,False), (1,True,20000,False), (1,True,0,True)])
def test_expired_or_missing_retention_never_returns_current_and_joins_one_build(retained_at,served,expires,force):
    ns,starts=harness(retained_at,served=served,expires=expires)
    request=ns['_data_sync_request_async_inventory']
    first=request(force_refresh=force,refresh_nonce='b'*32)
    second=request(force_refresh=force,refresh_nonce='b'*32)
    assert first['status']==second['status']=='STALE_REVALIDATING'
    assert first['refreshing'] is second['refreshing'] is True
    assert len(starts)==1 and starts[0]['args']==('b'*32,)
    assert ns['_data_sync_inventory_generation']('a'*64) is None


def test_delayed_unserved_but_retained_generation_still_delivers_without_scan():
    ns,starts=harness(9000)
    result=ns['_data_sync_request_async_inventory']()
    assert result['status']=='CURRENT'
    assert ns['_data_sync_async_inventory']['served_since_refresh'] is True
    assert ns['_data_sync_inventory_generation']('a'*64) is not None
    assert starts==[]


@pytest.mark.parametrize('field', ['source_git_rev','collection_epoch_id','tile_registry_signature'])
def test_retained_generation_with_changed_current_identity_is_not_current(field):
    ns,starts=harness(9000,expires=20000)
    ns['_data_sync_async_inventory']['generation']['bundle_identity'][field]='different'
    result=ns['_data_sync_request_async_inventory']()
    assert result['status']=='STALE_REVALIDATING'
    assert len(starts)==1
