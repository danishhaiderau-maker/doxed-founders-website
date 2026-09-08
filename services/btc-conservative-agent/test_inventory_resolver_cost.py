from pathlib import Path
import time
import pytest
from test_inventory_finalize_cadence import worker


@pytest.mark.parametrize('name,expected',[('tape.json','strict_generation_v1'),('events.log','append_prefix_v1'),('v3/ledgers/opportunity.jsonl','append_prefix_v1')])
def test_consistency_resolution_once(tmp_path,monkeypatch,name,expected):
    module=worker(); path=tmp_path/name
    path.parent.mkdir(parents=True,exist_ok=True); path.touch()
    original=Path.resolve; calls=[]
    def resolve(self,*args,**kwargs):
        calls.append(self)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',resolve)
    assert module._consistency_mode(path,{'_runtime':tmp_path})==expected
    assert calls==[path]


def test_containment_and_symlink_guards_remain(tmp_path):
    module=worker(); volume=tmp_path/'volume'; volume.mkdir()
    outside=tmp_path/'outside.json'; outside.touch()
    request={'_volume':volume,'_runtime':volume,'extensions':['.json']}
    assert module._allowed(outside,request) is False
    link=volume/'linked.json'
    try: link.symlink_to(outside)
    except OSError: pytest.skip('host symlink privilege unavailable')
    assert module._row(link,request) is None


def test_bounded_resolution_microbenchmark(tmp_path,monkeypatch):
    module=worker(); path=tmp_path/'tape.json'; path.touch()
    original=Path.resolve; count=0
    def resolve(self,*args,**kwargs):
        nonlocal count
        count+=1
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',resolve)
    start=time.perf_counter()
    for _ in range(200): module._consistency_mode(path,{'_runtime':tmp_path})
    elapsed=time.perf_counter()-start
    assert count==200
    print(f'classification_iterations=200 resolve_calls={count} elapsed_seconds={elapsed:.6f}; old_source_required_calls=400')
