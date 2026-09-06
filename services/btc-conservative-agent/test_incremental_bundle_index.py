import json
import pytest
from test_fly_sync_bundle_adapter import build, adapter, FakeTime


@pytest.mark.parametrize("change", ["removed", "mutated", "duplicate"])
def test_building_prefix_change_rejected_after_verified_progress(tmp_path,change):
    request, fetch, _, _ = build(tmp_path)
    timing, emitted, polls = FakeTime(), [], []
    def growing(url,**kwargs):
        status,headers,body=fetch(url,**kwargs)
        if "/bundles?" in url:
            polls.append(url)
            index=json.loads(body)
            if len(polls)==1: index["status"]="BUILDING"
            elif change=="removed": index["packages"]=[]
            elif change=="mutated": index["packages"][0]["payload_bytes"]+=1
            else: index["packages"]*=2
            return status,headers,json.dumps(index).encode()
        return status,headers,body
    with pytest.raises(ValueError,match="BUNDLE_INDEX_(PREFIX_CHANGED|DUPLICATE)"):
        adapter.run(request,emit=emitted.append,fetch=growing,clock=timing.clock,sleep=timing.sleep)
    assert emitted[0]["status"]=="PACKAGE_VERIFIED"
    assert not any(r["status"]=="COMPLETE" for r in emitted)


def test_append_only_growth_downloads_each_package_once_and_reuses_verified_local(tmp_path):
    request, fetch, calls, source = build(tmp_path,129)
    request["verified_local_root"] = str(source)
    timing, emitted, polls = FakeTime(), [], []
    def growing(url,**kwargs):
        status,headers,body=fetch(url,**kwargs)
        if "/bundles?" in url:
            polls.append(url); index=json.loads(body)
            if len(polls)==1:
                index["status"]="BUILDING"; index["packages"]=index["packages"][:1]
            return status,headers,json.dumps(index).encode()
        return status,headers,body
    adapter.run(request,emit=emitted.append,fetch=growing,clock=timing.clock,sleep=timing.sleep)
    assert [r["status"] for r in emitted]==["PACKAGE_VERIFIED","INDEX_WAITING","PACKAGE_VERIFIED","COMPLETE"]
    verified=[r for r in emitted if r["status"]=="PACKAGE_VERIFIED"]
    assert all(r["reused_local"] is True for r in verified)
    assert emitted[-1]["files"]==129 and emitted[-1]["ack_sent"] is False
    assert len([u for u in calls if "descriptor=1" in u])==2
    assert not any("offset=" in u for u in calls)


def test_cross_package_member_repeat_never_completes(tmp_path,monkeypatch):
    request,fetch,_,_=build(tmp_path,129)
    actual=adapter.fetch_verified_package
    first=[]
    def overlapping(*args,**kwargs):
        result=actual(*args,**kwargs)
        if first: result["members"].append(first[0])
        else: first.append(result["members"][0])
        return result
    monkeypatch.setattr(adapter,"fetch_verified_package",overlapping)
    emitted=[]
    with pytest.raises(ValueError,match="BUNDLE_MEMBER_REPEATED_ACROSS_PACKAGES"):
        adapter.run(request,emit=emitted.append,fetch=fetch,sleep=lambda n:None)
    assert len(emitted)==1 and emitted[0]["status"]=="PACKAGE_VERIFIED"
