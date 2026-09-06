import io
import json
import pytest
from urllib.error import URLError
from test_fly_sync_bundle_adapter import adapter, build, FakeTime


@pytest.mark.parametrize('failure,category', [(503,None), (429,None), (TimeoutError('PRIVATE'), 'TIMEOUT'), (URLError(OSError('PRIVATE')), 'CONNECTION_ERROR')])
@pytest.mark.parametrize('header,wait', [('1',5), ('20',20), ('999',30), ('PRIVATE',5)])
def test_index_pressure_keeps_bounds_and_safe_context(tmp_path, failure, category, header, wait):
    request, _, _, _ = build(tmp_path)
    timing=FakeTime()
    calls=[]
    def fetch(*args, **kwargs):
        calls.append(1)
        if isinstance(failure, Exception): raise failure
        return failure, {'Retry-After':header}, b''
    with pytest.raises(adapter.IndexPressureError) as caught:
        adapter.run(request, emit=lambda _:None, fetch=fetch, clock=timing.clock, sleep=timing.sleep)
    row=caught.value.diagnostic
    assert row['phase']=='INDEX' and row['attempts']==2
    assert row['transport_error']==category
    assert row['http_status']==(failure if category is None else None)
    assert 'PRIVATE' not in repr(row) and 'package_sha256' not in row
    assert len(calls)==2 and timing.sleeps==[wait if category is None else 5]


@pytest.mark.parametrize('change', [{}, {'extra':'PRIVATE'}, {'attempts':True}, {'http_status':True}, {'phase':'CHUNK'}])
def test_child_serializes_only_exact_index_diagnostic(monkeypatch, capsys, change):
    row=dict(generation_id='a'*64,phase='INDEX',attempts=2,http_status=503,transport_error=None)
    error=adapter.IndexPressureError(row)
    error.diagnostic.update(change)
    monkeypatch.setattr(adapter.sys, 'stdin', io.TextIOWrapper(io.BytesIO(b'{}')))
    def fail(*args, **kwargs): raise error
    monkeypatch.setattr(adapter,'run',fail)
    assert adapter.main()==1
    output=json.loads(capsys.readouterr().out)
    assert ('index_diagnostic' in output) is (not change)
    assert 'PRIVATE' not in repr(output)
