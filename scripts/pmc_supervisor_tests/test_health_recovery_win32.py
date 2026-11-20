import hashlib
import http.client
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pmc_supervisor import policy as P
from pmc_supervisor.health import Cache, CollectorSample, DiskSample, ReceiptReader, HealthServer, probe_viewer, reserve_port, GIB
from pmc_supervisor.recovery import verify_signature, read_receipt
from pmc_supervisor.win32 import API, PROTOTYPES, LogDrain


def collector_row(path, row):
    raw=json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    path.write_text(json.dumps({**row,"record_checksum":hashlib.sha256(raw).hexdigest()},sort_keys=True,separators=(",",":"))+"\n")

@pytest.mark.parametrize("free,status,research,capture",[(None,"UNKNOWN",False,False),(100*GIB,"GREEN",True,True),
    (100*GIB-1,"RED",False,True),(20*GIB,"RED",False,True),(20*GIB-1,"RED",False,False)])
def test_disk_thresholds(free,status,research,capture):
    row=DiskSample().sample(100,free)
    assert (row["status"],row["new_research_allowed"],row["new_collector_allowed"])==(status,research,capture)
    assert row["hours_to_reserve"] is None


def test_disk_measured_projection_not_guess():
    disk=DiskSample();disk.sample(100,200*GIB)
    result=disk.sample(110,199*GIB)
    assert result["hours_to_reserve"]==pytest.approx(990/3600)
    assert result["projection_basis"]=="MEASURED_C_FREE_BYTE_DELTA_NOT_PMC_ONLY"
    assert disk.sample(120,200*GIB)["hours_to_reserve"] is None


def test_cache_stale_cannot_green():
    c=Cache();c.publish({"status":"GREEN","roles":{}},100)
    assert c.read(130)["status"]=="GREEN"
    assert c.read(130.001)["status"]=="UNKNOWN"
    assert c.read(99)["status"]=="UNKNOWN"
    result=c.read(110);result["status"]="forged"
    assert c.read(110)["status"]=="GREEN"


def test_incremental_receipts_malformed_partial_and_shrink(tmp_path):
    path=tmp_path/"runtime.journal"
    collector_row(path,{"kind":"heartbeat","utc_ns":123})
    r=ReceiptReader();assert r.read(path)["status"]=="VALID"
    with path.open("ab") as f:f.write(b'{"torn":')
    assert r.read(path)["last"] is None
    path.write_bytes(b"")
    assert r.read(path)["status"]=="UNKNOWN_IDENTITY"
    path.write_bytes(b'{}\n')
    assert ReceiptReader().read(path)["status"]=="UNKNOWN_OR_PARTIAL"


def make_tree(tmp_path, now, *, book=True, age=0, gaps=0, future=0):
    day=datetime.fromtimestamp(now,timezone.utc).strftime("%Y-%m-%d")
    for stream in ("raw/book","raw/trades","norm/book","norm/trades"):
        p=tmp_path/day/stream;p.mkdir(parents=True,exist_ok=True)
        (p/"test.gz").write_bytes(b"fixture")
        os.utime(p/"test.gz",(now-age+future,now-age+future))
    collector_row(tmp_path/"runtime.journal",{"kind":"heartbeat","schema_v":"pmc_runtime_v1","utc_ns":int(now*1e9),
        "book_valid":book,"received_count":10,"durable_count":10,"gaps":gaps,"checksum_faults":0,
        "observed_minutes":{"trades_feed":[int(now%86400//60)],"valid_book_feed":[int(now%86400//60)]}})
    collector_row(tmp_path/"launch.journal",{"kind":"start"})
    collector_row(tmp_path/day/"health.journal",{"kind":"health","schema_v":"pmc_health_v1","now_ns":int(now*1e9),
        "book_valid":book,"liveness_receive_time_ns":int(now*1e9),"trade_quiet":False})
    return day

@pytest.mark.parametrize("book,gaps,age,future,green",[(True,0,0,0,True),(False,0,0,0,False),(True,1,0,0,False),
                                                     (True,0,91,0,False),(True,0,0,6,False)])
def test_collector_freshness_validity_not_just_mtime(tmp_path,book,gaps,age,future,green):
    now=1800000000.;make_tree(tmp_path,now,book=book,gaps=gaps,age=age,future=future)
    result=CollectorSample(tmp_path).sample(now)
    assert (result["status"]=="GREEN")==green
    assert result["coverage_claim"]=="observed_not_continuous"


def test_quiet_trades_requires_confirmed_heartbeat(tmp_path):
    now=1800000000.;day=make_tree(tmp_path,now)
    for stream in ("raw/trades","norm/trades"):
        path=tmp_path/day/stream/"test.gz";os.utime(path,(now-1000,now-1000))
    s=CollectorSample(tmp_path)
    result=s.sample(now)
    assert result["streams"]["raw/trades"]["status"]=="QUIET/NO_NEW_TRADES"
    assert s.sample(now+100)["status"]!="GREEN"


def test_stagnant_durable_counter_and_missing_snapshot(tmp_path):
    now=1800000000.;make_tree(tmp_path,now);s=CollectorSample(tmp_path);assert s.sample(now)["status"]=="GREEN"
    # Append (never rewrite) a new checksummed heartbeat with advancing receive,
    # stagnant durable. The incremental reader must actually see it.
    p=tmp_path/"runtime.journal";row=json.loads(p.read_text());row.pop("record_checksum");row["received_count"]=11
    other=tmp_path/"new";collector_row(other,row)
    with p.open("ab") as f:f.write(other.read_bytes())
    result=s.sample(now+1)
    assert result["durable_progress"]=="STALLED" and result["status"]!="GREEN"
    assert CollectorSample(tmp_path/"missing").sample(now)["status"]=="UNKNOWN"


def test_loopback_probe_no_proxy_redirect_or_payload_leak():
    body={"pid":42,"alive":True,"ready":True,"data_root":P.DATA,"report_root":P.REPORTS,"generation_freshness":{"current":True}}
    class Conn:
        def __init__(self,host,port,timeout): assert (host,port,timeout)==("127.0.0.1",9001,1.)
        def request(self,method,path,headers): assert (method,path)==("GET","/api/health")
        def getresponse(self): return self
        status=200
        def read(self,size): assert size==65537;return json.dumps(body).encode()
        def close(self):pass
    with mock.patch("http.client.HTTPConnection",Conn):
        assert probe_viewer(42)["ready"]
        assert not probe_viewer(43)["ready"]
        body["generation_freshness"]["current"]=False
        assert not probe_viewer(42)["ready"]
        body["error"]="Fly timeout secret-canary"
        result=probe_viewer(42)
        assert result["fly_failure"] and "secret-canary" not in json.dumps(result)


def test_http_read_only_latched_200_not_green_and_occupied_port():
    cache=Cache();cache.publish({"status":"RED/HOLD","roles":{"pmc":{"phase":"LATCHED"}}},time.monotonic())
    server=HealthServer(cache,socket_factory=lambda _:reserve_port(0));port=server.server_address[1];thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with pytest.raises(P.Hold,match="PORT_OCCUPIED"): reserve_port(port)
        for method,path,expected in (("GET","/health.json",200),("HEAD","/health",200),("POST","/clear",405),
           ("PUT","/health.json",405),("DELETE","/",405),("GET","/health.json?reset=1",404),("GET","/reset",404)):
            c=http.client.HTTPConnection("127.0.0.1",port,timeout=2);c.request(method,path,headers={"Host":"127.0.0.1:9040"})
            r=c.getresponse();data=r.read();assert r.status==expected
            if path=="/health.json" and method=="GET":assert json.loads(data)["status"]!="GREEN"
            c.close()
        c=http.client.HTTPConnection("127.0.0.1",port,timeout=2)
        c.request("GET","/health.json",headers={"Host":"malicious.invalid"});assert c.getresponse().status==404;c.close()
    finally:
        server.shutdown();server.server_close();thread.join(2)


def test_api_declarations_all_mutations_fail_before_call():
    class Function:
        def __call__(self,*args): raise AssertionError("must not invoke mutated prototype")
    fake=type("FakeDLL",(),{name:Function() for name in PROTOTYPES})()
    api=API(fake)
    for name,(args,restype) in PROTOTYPES.items():
        fn=getattr(fake,name);fn.argtypes=None
        with pytest.raises(AssertionError,match="missing Win32 declaration"):api.call(name)
        fn.argtypes=args
        fn.restype=object()
        with pytest.raises(AssertionError,match="missing Win32 declaration"):api.call(name)
        fn.restype=restype
    api.assert_prototypes()


def test_bounded_log_safe_fault_inventory(tmp_path):
    path=tmp_path/"stderr.log";path.touch();flags=set()
    drain=LogDrain(io.BytesIO(b"ImportError: credential-canary\nFly timeout credential-canary\n"),path,flags)
    drain.thread.join(2)
    assert {"STARTUP_IMPORT_FAILURE","FLY_SIDE_PATH_FAILURE"}<=flags
    assert "credential-canary" not in json.dumps(list(flags))
    with mock.patch.object(LogDrain,"CAP",10):
        d=LogDrain(io.BytesIO(b"x"*100),path,flags);d.thread.join(2)
    assert "LOG_CAP" in flags


def test_no_unsigned_or_wrong_human_clear(tmp_path):
    assert not verify_signature({"who":"agent"},"",{"n":"0","e":65537})
    assert not verify_signature({},"abc",{})
    path=tmp_path/"human-clear.json";path.write_text('{}')
    key=tmp_path/"human-clear-public-key.json";key.write_text('{}')
    with pytest.raises(P.Hold):read_receipt(tmp_path,P.file_hash(path),"0"*64)


def test_missing_entire_api_declaration_asserts_before_use():
    class Function:
        def __call__(self,*args):raise AssertionError("must not invoke missing declaration")
    fake=type("FakeDLL",(),{name:Function() for name in PROTOTYPES})()
    api=API(fake)
    for name in tuple(PROTOTYPES):
        declaration=PROTOTYPES.pop(name)
        try:
            with pytest.raises(AssertionError,match="missing Win32 declaration"):api.call(name)
        finally:PROTOTYPES[name]=declaration
