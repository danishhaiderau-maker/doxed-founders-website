"""Adopted HM R2 probes for PR #1003 (source sha256 b0d3acbcb419).
Independent synthetic probes; no live services, repository writes or data."""
import concurrent.futures
import json
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest
from strategy_lab.bounded_refresh import BoundedRefresh
from research import research_dashboard as D

SOURCE = Path(__import__('strategy_lab.bounded_refresh', fromlist=['x']).__file__).read_text()

def cls(mutant=None):
    source = SOURCE
    if mutant == 'deadline':
        source = source.replace('flight["event"].wait(remaining())', 'flight["event"].wait()')
    elif mutant == 'singleflight':
        source = source.replace('if self._flight is None:', 'if True: # MUTANT replaces a busy worker')
    module = ModuleType('hm_bounded_' + str(mutant))
    exec(compile(source, '<hm-local-mutant>', 'exec'), module.__dict__)
    return module.BoundedRefresh


def deadline_invariant(C):
    cache = C(ttl=5)
    release = threading.Event()
    timer = threading.Timer(.18, release.set)
    timer.start()
    try:
        start = time.monotonic()
        result = cache.get('same', lambda: release.wait(.5) or 'done', timeout=.025)
        elapsed = time.monotonic()-start
        assert elapsed < .10, f'caller exceeded deadline: {elapsed:.4f}s'
        assert result['cache']['status'] == 'TIMEOUT'
    finally:
        release.set()
        timer.join()


def singleflight_invariant(C):
    cache = C(ttl=5)
    release = threading.Event()
    entered = threading.Event()
    calls = []
    def load():
        calls.append(1)
        entered.set()
        release.wait(.8)
        return 'ok'
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            jobs = [pool.submit(cache.get, 'same', load, timeout=.025) for _ in range(12)]
            assert entered.wait(.2)
            results = [j.result(.3) for j in jobs]
        assert len(calls) == 1, f'loader fanout: {len(calls)}'
        assert all(r['cache']['partial'] for r in results)
        for i in range(10):
            assert cache.get('different'+str(i), load, timeout=.001)['cache']['partial']
        assert len(calls) == 1
    finally:
        release.set()


def test_head_deadline():
    deadline_invariant(cls())


def test_head_singleflight():
    singleflight_invariant(cls())


def test_deadline_mutant_is_killed():
    with pytest.raises(AssertionError, match='caller exceeded deadline'):
        deadline_invariant(cls('deadline'))


def test_singleflight_mutant_is_killed():
    with pytest.raises(AssertionError, match='loader fanout'):
        singleflight_invariant(cls('singleflight'))


def test_production_route_deadline_and_recovery(monkeypatch):
    cache = BoundedRefresh(ttl=30)
    release = threading.Event()
    calls = []
    def payload():
        calls.append(1)
        release.wait(4)
        return {}, {}, {'status': 'UNKNOWN'}
    monkeypatch.setattr(D, '_DATA_HEALTH_REFRESH', cache)
    monkeypatch.setattr(D, '_data_health_payload', payload)
    monkeypatch.setattr(D, '_archive_payload', lambda: ({'status':'UNAVAILABLE','data':None}, {}))
    monkeypatch.setattr(D, '_self_aware_data', lambda: pytest.fail('outbound self-aware access'))
    try:
        start = time.monotonic()
        result = D.app.test_client().get('/data-health')
        elapsed = time.monotonic()-start
        print('PRODUCTION_ROUTE', json.dumps({'elapsed_sec':elapsed,'http':result.status_code,'calls':len(calls)}))
        assert 2.45 <= elapsed < 2.7
        assert result.status_code == 503
        assert b'health data UNKNOWN' in result.data
        assert b'Live OFF' in result.data
        for _ in range(3):
            cache.get('different', lambda: pytest.fail('replacement worker'), timeout=.001)
        assert len(calls) == 1
    finally:
        release.set()
    assert cache.get(D._data_health_cache_key(), D._data_health_page_uncached, timeout=.5)['value']
    assert D.app.test_client().get('/data-health').status_code == 200


class GateEvent:
    def __init__(self):
        self.raw = threading.Event()
        self.waiting = threading.Event()
        self.signalled = threading.Event()
        self.allow_return = threading.Event()
    def set(self):
        self.raw.set()
    def wait(self, timeout=None):
        self.waiting.set()
        done = self.raw.wait(timeout)
        if done:
            self.signalled.set()
            assert self.allow_return.wait(1)
        return done


def test_key_change_cannot_lose_completed_result():
    # Force the valid interleaving: A completes; its waiter is descheduled;
    # B completes and replaces _good; A's waiter resumes. No cache internals
    # are mutated except an instrumented Event that controls thread scheduling.
    cache = BoundedRefresh(ttl=30)
    release_a = threading.Event()
    cache.get('A', lambda: (release_a.wait(1), 'A-value')[1], timeout=.005)
    gate = GateEvent()
    cache._flight['event'] = gate
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        a = pool.submit(cache.get, 'A', lambda: 'never', timeout=.8)
        try:
            assert gate.waiting.wait(.3)
            release_a.set()
            assert gate.signalled.wait(.3)
            b = cache.get('B', lambda: 'B-value', timeout=.2)
            assert b['value'] == 'B-value'
            gate.allow_return.set()
            answer = a.result(.4)
            print('KEY_RACE', json.dumps(answer))
            assert answer['value'] == 'A-value', 'FRESH result lost when another key completes'
        finally:
            release_a.set()
            gate.allow_return.set()


def test_thread_start_failure_does_not_latch_a_nonexistent_worker(monkeypatch):
    import strategy_lab.bounded_refresh as BR
    cache = BoundedRefresh(ttl=30, retry=.01)
    class CannotStart:
        def __init__(self, *a, **kw):
            pass
        def start(self):
            raise RuntimeError('fixture cannot start worker')
    with monkeypatch.context() as mp:
        mp.setattr(BR.threading, 'Thread', CannotStart)
        with pytest.raises(RuntimeError, match='fixture cannot start worker'):
            cache.get('same', lambda: 'never', timeout=.01)
    result = cache.get('same', lambda: 'recovered', timeout=.015)
    print('START_FAILURE', json.dumps(result))
    assert result['value'] == 'recovered', 'failed thread start permanently poisons the flight slot'
