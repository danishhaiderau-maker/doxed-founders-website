import json
from pathlib import Path
import pytest
from test_data_sync_inventory_worker_contract import _load_worker, _request, _paths


def test_phase_timings_add_and_preserve_exception(monkeypatch):
    module=_load_worker(); clock=iter([1.,3.,4.,7.,8.,9.])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    totals={'row_metadata':0.0}
    marker=object()
    assert module._timed_inventory_phase(totals,'row_metadata',lambda:marker) is marker
    assert module._timed_inventory_phase(totals,'row_metadata',lambda:marker) is marker
    def fail(): raise RuntimeError('same')
    with pytest.raises(RuntimeError,match='same'): module._timed_inventory_phase(totals,'row_metadata',fail)
    assert totals['row_metadata']==6


@pytest.mark.parametrize('end',[float('nan'),float('inf'),-1.])
def test_invalid_clock_duration_does_not_poison_receipt(monkeypatch,end):
    module=_load_worker(); clock=iter([0.,end])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    totals={'row_metadata':0.0}
    module._timed_inventory_phase(totals,'row_metadata',lambda:None)
    assert totals=={'row_metadata':0.0}


def test_resumed_inventory_identity_independent_of_phase_telemetry(tmp_path,monkeypatch):
    module=_load_worker(); volume=tmp_path/'volume'
    nonce='a'*32
    request=_request(volume,nonce); request['inventory_file_budget']=1
    (volume/'runtime'/'a.json').write_text('{}')
    (volume/'runtime'/'b.json').write_text('{}')
    request_path,result_path=_paths(volume,nonce)
    request_path.write_text(json.dumps(request))
    parsed=module._load_request(request_path,result_path,nonce)
    fingerprint=module._request_fingerprint(parsed)
    seen=[]
    for _ in range(10):
        generation,receipt=module._build_resumable(parsed,Path(request['work_root']))
        seen.append(receipt)
        assert receipt['request_fingerprint']==fingerprint
        assert set(receipt['invocation_phase_seconds'])=={'row_metadata','row_storage','directory_freeze'}
        assert all(v>=0 for v in receipt['invocation_phase_seconds'].values())
        if generation is not None: break
    assert generation is not None
    assert len(seen)>1
    assert receipt['rows_written']==2
    assert 'invocation_phase_seconds' not in module._stable_request(parsed)
