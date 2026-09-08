"""Anonymous background metadata cache; consumers never initiate network I/O."""
import copy
import threading
import time
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace


def anonymous_metadata_adapter(symbol, *, timeout=20):
    """Hard whole-child deadline; subprocess.run kills and waits on timeout."""
    env={key:value for key,value in os.environ.items() if key.upper() in
         {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP'}}
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--fetch',symbol],
        env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout,check=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if len(result.stdout)>16384:
        raise ValueError('PUBLIC_METADATA_OUTPUT_LIMIT')
    market=json.loads(result.stdout)
    if not isinstance(market,dict): raise ValueError('PUBLIC_METADATA_INVALID')
    return SimpleNamespace(load_markets=lambda:None,market=lambda requested:copy.deepcopy(market))


class PublicQuantityMetadata:
    def __init__(self, factory, symbol, *, clock=time.time):
        self.factory, self.symbol, self.clock = factory, symbol, clock
        self._lock = threading.Lock()
        self._value = None
        self._status = {'status':'UNAVAILABLE','last_success_ts':None,'failure_code':None}

    def refresh(self):
        try:
            # Factory must create a separate anonymous adapter, not the shared
            # execution adapter. Only allowlisted metadata enters the cache.
            exchange = self.factory()
            exchange.load_markets()
            raw = exchange.market(self.symbol)
            market = {'id':raw.get('id'), 'precision':{'amount':(raw.get('precision') or {}).get('amount')},
                'limits':{key:{'min':((raw.get('limits') or {}).get(key) or {}).get('min')} for key in ('amount','cost')}}
            from research.venue_quantity_observation import capture_venue_quantity_observation
            cached = SimpleNamespace(id='bitfinex',market=lambda symbol:copy.deepcopy(market))
            check = capture_venue_quantity_observation(cached, ccxt_symbol=self.symbol,
                evidence_symbol='BTC', captured_at=datetime.now(timezone.utc).isoformat(),
                source_revision='0'*40, adapter_version='validation')
            if check.get('observation') is None:
                raise ValueError('metadata invalid')
            with self._lock:
                self._value = market
                self._status = {'status':'CURRENT','last_success_ts':self.clock(),'failure_code':None}
            return True
        except Exception:
            with self._lock:
                self._value = None
                self._status = {**self._status,'status':'UNAVAILABLE','failure_code':'PUBLIC_METADATA_REFRESH_FAILED'}
            return False

    def market(self, symbol):
        with self._lock:
            if (symbol != self.symbol or self._value is None
                    or self.clock()-self._status['last_success_ts']>3600):
                raise ValueError('PUBLIC_METADATA_UNAVAILABLE')
            return copy.deepcopy(self._value)

    id = 'bitfinex'

    def status(self):
        with self._lock:
            value=dict(self._status)
        value['age_sec'] = None if value['last_success_ts'] is None else max(0,self.clock()-value['last_success_ts'])
        if value['status']=='CURRENT' and value['age_sec']>3600: value['status']='STALE'
        return value

    def run(self, shutdown):
        delay=5
        while not shutdown.is_set():
            success=self.refresh()
            wait=3600 if success else delay
            delay=5 if success else min(300,delay*2)
            if shutdown.wait(wait): return


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--fetch': raise SystemExit(2)
    import ccxt
    exchange=ccxt.bitfinex({'enableRateLimit':True,'timeout':10000,
                          'options':{'maxRetriesOnFailure':0}})
    exchange.load_markets()
    raw=exchange.market(sys.argv[2])
    value={'id':raw.get('id'),'precision':{'amount':(raw.get('precision') or {}).get('amount')},
        'limits':{key:{'min':((raw.get('limits') or {}).get(key) or {}).get('min')} for key in ('amount','cost')}}
    print(json.dumps(value,allow_nan=False))
