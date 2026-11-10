import concurrent.futures
import threading
import time

import pytest
from strategy_lab.bounded_refresh import BoundedRefresh
from strategy_lab import insights as I
from research import research_dashboard as D


def test_ttl_hit_expiry_and_error_backoff():
    now=[0.0]
    cache=BoundedRefresh(ttl=5,retry=1,clock=lambda:now[0])
    calls=[]
    def load():
        calls.append(1)
        return {"answer":len(calls)}
    assert cache.get("a",load)["value"]=={"answer":1}
    now[0]=4
    assert cache.get("a",load)["cache"]["status"]=="CACHED"
    assert len(calls)==1
    now[0]=5
    assert cache.get("a",load)["value"]=={"answer":2}
    now[0]=11
    def broken():
        raise ValueError("corrupt")
    out=cache.get("a",broken)
    assert out["cache"]["status"]=="ERROR" and out["cache"]["stale"]
    assert out["cache"]["cached_as_of"]
    assert cache.get("a",load)["cache"]["status"]=="ERROR"
    now[0]=12
    assert cache.get("a",load)["value"]=={"answer":3}


def test_coalesces_concurrency_and_retains_one_hung_slot():
    cache=BoundedRefresh(ttl=5)
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def hung():
        calls.append(1)
        entered.set()
        release.wait(2)
        return "recovered"
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            jobs=[pool.submit(cache.get,"same",hung,timeout=.025) for _ in range(16)]
            assert entered.wait(.2)
            results=[j.result(.5) for j in jobs]
        assert len(calls)==1
        assert all(r["cache"]["status"]=="TIMEOUT" for r in results)
        for _ in range(10):
            assert cache.get("same",hung,timeout=.001)["cache"]["partial"]
        assert len(calls)==1
        assert cache.get("other",lambda:"wrong",timeout=.001)["cache"]["status"]=="BUSY"
    finally:
        release.set()
    result=cache.get("same",hung,timeout=.2)
    assert result["value"]=="recovered"
    assert len(calls)==1


def test_lock_wait_bounded():
    cache=BoundedRefresh()
    cache._lock.acquire()
    try:
        start=time.monotonic()
        result=cache.get("a",lambda:None,timeout=.02)
        assert time.monotonic()-start<.10
        assert result["cache"]["status"]=="TIMEOUT"
    finally:
        cache._lock.release()


def test_archive_key_verify_stale_and_recovery(monkeypatch):
    cache=BoundedRefresh(ttl=60)
    monkeypatch.setattr(I,"_ARCHIVE_REFRESH",cache)
    calls=[]
    def raw(now,days,*,root):
        calls.append((days,root))
        return I._component(I.OK,data={"verified":True},as_of=now,max_age=I.ARCHIVE_MAX_AGE_SEC,now=now)
    monkeypatch.setattr(I,"_archive_component_uncached",raw)
    base=1800000000
    assert I.archive_component(base,root="one")["data"]=={"verified":True}
    assert I.archive_component(base,root="one")["refresh_status"]=="CACHED"
    assert len(calls)==1
    I.archive_component(base,days=7,root="one")
    I.archive_component(base,days=7,root="two")
    assert len(calls)==3


def test_whole_page_slow_source_no_network_and_recovery(monkeypatch):
    monkeypatch.setattr(D,"_DATA_HEALTH_REFRESH",BoundedRefresh(ttl=30))
    monkeypatch.setattr(D,"DATA_HEALTH_DEADLINE_SEC",.025)
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def payload():
        calls.append(1)
        entered.set()
        release.wait(2)
        return {},{},{"status":"UNKNOWN"}
    monkeypatch.setattr(D,"_data_health_payload",payload)
    monkeypatch.setattr(D,"_archive_payload",lambda:({"status":"UNAVAILABLE","data":None},{}))
    monkeypatch.setattr(D,"_self_aware_data",lambda:pytest.fail("network probe"))
    def request():
        with D.app.test_client() as client:
            start=time.monotonic()
            response=client.get('/data-health')
            return response,time.monotonic()-start
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            jobs=[pool.submit(request) for _ in range(10)]
            assert entered.wait(.3)
            results=[j.result(.5) for j in jobs]
        assert len(calls)==1
        assert all(r.status_code==503 and elapsed<.2 for r,elapsed in results)
        assert all(b'PARTIAL / TIMEOUT' in r.data and b'Live OFF' in r.data for r,_ in results)
        assert D.app.test_client().get('/favicon.ico').status_code==204
    finally:
        release.set()
    assert D._DATA_HEALTH_REFRESH.get(D._data_health_cache_key(),D._data_health_page_uncached,timeout=.3)["value"]
    response,elapsed=request()
    assert response.status_code==200 and response.headers['X-Data-Health-Refresh']=='CACHED'
    assert b'NOT CHECKED: offline Data Health view' in response.data
    assert len(calls)==1


def test_archive_hung_loader_has_bounded_result_and_no_workers(monkeypatch):
    monkeypatch.setattr(I,"_ARCHIVE_REFRESH",BoundedRefresh(ttl=60))
    release=threading.Event()
    calls=[]
    def hung(*a,**kw):
        calls.append(1)
        release.wait(2)
        return I._component(I.UNAVAILABLE,reason="fixture missing")
    monkeypatch.setattr(I,"_archive_component_uncached",hung)
    try:
        for _ in range(4):
            start=time.monotonic()
            result=I.archive_component(1800000000,timeout=.01)
            assert time.monotonic()-start<.1
            assert result["partial"] and result["data"] is None
            assert result["refresh_status"]=="TIMEOUT"
        assert len(calls)==1
    finally:
        release.set()
