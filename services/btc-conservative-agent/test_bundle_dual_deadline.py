import json
import pytest
from test_fly_sync_bundle_adapter import adapter, build, FakeTime


@pytest.mark.parametrize('mode',['complete','idle','overall','changed'])
def test_verified_progress_dual_deadline(tmp_path, mode):
    request, fetch, _, _=build(tmp_path,129)
    timing=FakeTime(); emitted=[]; polls=0
    def paced(url,**kwargs):
        nonlocal polls
        status,headers,body=fetch(url,**kwargs)
        if '/bundles?' not in url:return status,headers,body
        polls+=1; data=json.loads(body)
        if polls==1:
            timing.now=500;data['packages']=data['packages'][:1];data['status']='BUILDING'
        elif polls==2:
            timing.now=1000;data['status']='COMPLETE' if mode=='complete' else 'BUILDING'
            if mode=='changed': data['packages'][0]['package_sha256']='f'*64
        elif mode=='overall': timing.now=1800
        if mode=='idle': data['status']='BUILDING'
        return status,headers,json.dumps(data).encode()
    if mode=='complete':
        adapter.run(request,emit=emitted.append,fetch=paced,clock=timing.clock,sleep=timing.sleep)
        assert emitted[-1]['status']=='COMPLETE' and emitted[-1]['files']==129
    else:
        code={'idle':'PREPARATION_DEADLINE','overall':'TRANSFER_DEADLINE','changed':'PREFIX_CHANGED'}[mode]
        with pytest.raises(ValueError,match=code):adapter.run(request,emit=emitted.append,fetch=paced,clock=timing.clock,sleep=timing.sleep)
        assert not any(x['status']=='COMPLETE' for x in emitted)
        assert any(x['status']=='PACKAGE_VERIFIED' for x in emitted)
        if mode=='idle': assert timing.now==1600.5
    assert all(x.get('ack_sent',False) is False for x in emitted)
