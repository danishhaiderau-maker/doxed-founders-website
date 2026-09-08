from types import SimpleNamespace
from public_quantity_metadata import PublicQuantityMetadata


def test_cache_no_network_in_read_and_unknown_cost_preserved():
    calls=[]
    def factory():
        calls.append(1)
        return SimpleNamespace(load_markets=lambda:None,market=lambda symbol:{'id':'BTC',
            'precision':{'amount':8},'limits':{'amount':{'min':.00004},'cost':{'min':None}},'secret':'excluded'})
    cache=PublicQuantityMetadata(factory,'BTC/USDT')
    assert cache.refresh()
    first=cache.market('BTC/USDT'); first['precision']['amount']=0
    assert cache.market('BTC/USDT')['precision']['amount']==8
    assert cache.market('BTC/USDT')['limits']['cost']['min'] is None
    assert 'secret' not in first and calls==[1]


def test_failure_backoff_shutdown_sanitized():
    def fail(): raise RuntimeError('SECRET')
    cache=PublicQuantityMetadata(fail,'BTC')
    class Stop:
        waits=[]
        def is_set(self): return False
        def wait(self,n): self.waits.append(n); return len(self.waits)==3
    stop=Stop(); cache.run(stop)
    assert stop.waits==[5,10,20]
    assert cache.status()['failure_code']=='PUBLIC_METADATA_REFRESH_FAILED'


def test_actual_bot_capture_uses_cache_without_network():
    import ast
    from pathlib import Path
    tree=ast.parse(Path('bot.py').read_text(encoding='utf-8-sig'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_capture_runtime_quantity_constraints')
    cache=object(); calls=[]
    def capture(adapter,**kwargs): calls.append(adapter); return {'supported':False,'receipt':None}
    ns=dict(_public_quantity_metadata=cache,capture_quantity_constraints=capture,
        SYMBOL_CCXT='BTC/USDT',BITFINEX_WS_SYMBOL='BTC',utc_iso=lambda:'now',_runtime_git_rev=lambda:'rev')
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
    assert ns[fn.name]() == {'supported':False,'receipt':None}
    assert calls==[cache]


def test_subprocess_hard_deadline_and_secret_free_environment(monkeypatch):
    import public_quantity_metadata as module
    import subprocess
    import sys
    monkeypatch.setenv('BITFINEX_API_KEY','not-forwarded')
    original=subprocess.run
    seen=[]
    def run(command,**kwargs):
        seen.append(kwargs)
        # Exercise the real runner timeout/kill/wait, without any network.
        return original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
    monkeypatch.setattr(module.subprocess,'run',run)
    import pytest
    with pytest.raises(subprocess.TimeoutExpired): module.anonymous_metadata_adapter('BTC',timeout=.05)
    assert 'BITFINEX_API_KEY' not in seen[0]['env']
    assert seen[0]['timeout']==.05


def test_successful_cache_expires_explicitly():
    clock=[100.]
    factory=lambda:SimpleNamespace(load_markets=lambda:None,market=lambda symbol:{'id':'BTC',
        'precision':{'amount':8},'limits':{'amount':{'min':.00004},'cost':{'min':None}}})
    cache=PublicQuantityMetadata(factory,'BTC',clock=lambda:clock[0]); assert cache.refresh()
    clock[0]+=3601
    assert cache.status()['status']=='STALE'
    import pytest
    with pytest.raises(ValueError): cache.market('BTC')
