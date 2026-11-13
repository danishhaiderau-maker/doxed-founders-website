"""AMEND5/6 bounded UI fixtures; no producer/ranking engine or laptop use."""
import concurrent.futures
import copy
import io
import json
import multiprocessing
import os
import signal
import socket
import threading
import time
from datetime import datetime,timezone
from pathlib import Path

import pytest
from research import twoyear_batch_view as V
from research import study_snapshot_loader as L
from research import research_dashboard as D

T=1800000000.0
STAMP=datetime.fromtimestamp(T,timezone.utc).isoformat()
HASH="a"*64


def accounting(filled=40,nofill=2,b=1,c=1,unresolved=0):
    captured=filled+nofill
    return {"filled_count":filled,"capture_count":captured,"count_a_no_fill":nofill,"count_b_exit_unpriceable":b,
            "count_c_unpriced_worst":c,"normal_count":filled-b-c,"missing_extreme_count":{"B":0,"C":unresolved},"mark_status":"UNKNOWN_MISSING_EXTREME" if unresolved else "OBSERVED","win_state":"UNKNOWN" if unresolved else "OBSERVED","no_fill_wins":0,
            "no_fill_share":nofill/captured if captured else None,"exit_unpriceable_share":b/filled if filled else None,
            "unpriced_worst_share":c/filled if filled else None,"board_review_5pct":(b+c)/filled>.05 if filled else None,
            "exit_mark":"LOW_LONG_HIGH_SHORT_1S","worst_mark":"ADVERSE_HOLDING_WINDOW"}


def fixture(count=1,strategies=3):
    batches=[]
    for i,(bid,*_) in enumerate(V.CALENDAR):
        complete=i<count and i in (0,2,3)
        status="complete" if complete else "sealed" if bid in ("B2","B6","B7") else "not_included" if bid in ("B8","B9","B10","B11") else "not_started"
        batches.append({"batch_id":bid,"status":status,"counted_for_ranking":complete,"count_reconciled":complete,"freeze_sha":HASH,"role":"research"})
    rows=[]
    for i in range(strategies if count else 0):
        rows.append({"strategy_id":str(i),"rank":i+1,"gross_positive_batches":1,"scope_batches":sum(bool(b.get("counted_for_ranking")) for b in batches),"gross_pnl_bp":10-i,
                     "gross_expectancy_bp":.2,"gross_drawdown_bp":2,"n_eff":30,"accounting":accounting(),
                     "per_batch":[{"batch_id":"B1","gross_pnl_bp":10-i,"hac":.03,"method":"per-batch frozen HAC","family_sha":HASH}],
                     "folds":[{"fold_id":"f1","accounting":accounting()}],"entry":"price return follows top percentile",
                     "trigger":None,"composite_exit":"first trigger: TP/time/HS/thesis","profit_taking":None,"risk":"loss can exceed margin",
                     "equity_points":[[0,1],[1,2]],"provenance":{"source_sha":HASH}})
    return {"schema":V.SCHEMA,"live":False,"basis":V.GROSS,"generated_at":STAMP,"source_as_of":STAMP,"generation_id":"fixture-generation",
            "series":"EXT2-EXTERNAL","freeze_sha":HASH,"chain_sha":HASH,"rules_sha":HASH,"selection_ledger_sha":HASH,"accounting_policy_sha":HASH,
            "accounting_policy":{"schema":"pin15-accounting-v1","mutually_exclusive":True,"unpriced_worst_in_filled":True,"precedence":"A_THEN_PROVEN_FILLED_C_THEN_B_THEN_NORMAL"},"board_sha":HASH,"code_sha":HASH,"writer":"fixture only",
            "selection_base":832,"variants_tried":8,"n_selection":840,"evaluated_count":840,"batches":batches,"ranking":rows,"ranked_batch_ids":[b["batch_id"] for b in batches if b["counted_for_ranking"]],"ranked_batch_count":sum(bool(b["counted_for_ranking"]) for b in batches),
            "findings":copy.deepcopy(rows),"distributions":({"B1":{"bins":[{"lo":0,"hi":10,"all_n":840,"top_k":3}]}} if count else {}),
            "score_def":"Frozen gross-positive share, median eligible gross, median -0.5*DD, smaller id; precomputed, not a gate",
            "provenance":[{"offline_verified_at":STAMP}],"forward":None}


def encoded(data):
    return json.dumps(data,separators=(",", ":")).encode()


@pytest.fixture
def store(monkeypatch):
    mono=[0.0]
    wall=[T]
    cache=V.SnapshotStore(clock=lambda:mono[0],wall=lambda:wall[0],bindings={k:HASH for k in ("chain_sha","freeze_sha","rules_sha","board_sha","code_sha","accounting_policy_sha","selection_ledger_sha")})
    monkeypatch.setattr(V,"STORE",cache)
    monkeypatch.setitem(D.app.config,"RESEARCH_OFFLINE_VIEWER",True)
    return cache,mono,wall


@pytest.mark.parametrize("count",[0,1,3])
def test_html_apis_states_and_no_source_io(count,store,monkeypatch):
    cache,_,_=store
    cache.publish(encoded(fixture(count)))
    client=D.app.test_client()
    def deny(*a,**kw):
        raise AssertionError("HTTP attempted source/process/network operation")
    monkeypatch.setattr(Path,"open",deny)
    monkeypatch.setattr(json,"loads",deny)
    monkeypatch.setattr(socket,"socket",deny)
    monkeypatch.setattr(multiprocessing.Process,"start",deny)
    for path in ["/details","/twoyear-batch","/strategy-findings","/api/research/twoyear/summary","/api/research/twoyear/leaderboard","/api/research/twoyear/cell/0","/api/research/twoyear/distribution?batch=B1","/api/research/twoyear/findings"]:
        start=time.monotonic()
        response=client.get(path)
        assert time.monotonic()-start<.275
        assert response.status_code in (200,404)
        assert b"Live OFF" in response.data
        assert len(response.data)<=V.HTML_CAP if response.content_type.startswith("text/html") else len(response.data)<=V.BYTE_CAP
    page=client.get('/twoyear-batch').data.decode()
    if count:
        assert V.GROSS in page and "of 11 batches ranked" in page and "full two-year" not in page
        assert "per-batch frozen HAC" in page
    for key in ("no edge","Cost sensitivity","net_stress","never cheaper","LOSS-at-W"):
        assert key not in page
    for path in ("/twoyear-batch","/api/research/twoyear/findings"):
        response=client.post(path)
        assert response.status_code==405 and b"Live OFF" in response.data
    assert client.get('/download/everything').status_code==403
    assert b'KNOWN GAP' in client.get('/data-health').data


def test_ttl_source_freshness_stale_cutoff_future_and_no_phantom_zero(store):
    cache,mono,wall=store
    body=cache.response("summary")[0]
    assert b'"completion_count":null' in body and b'writer UNKNOWN' in body
    cache.publish(encoded(fixture()))
    assert json.loads(cache.response("summary")[0])["state"]=="OBSERVED"
    mono[0]=5
    response=json.loads(cache.response("summary")[0])
    assert response["state"]=="STALE-RED" and response["source_as_of"]==STAMP
    mono[0]=0
    wall[0]=T+61
    assert json.loads(cache.response("summary")[0])["state"]=="STALE-RED"
    wall[0]=T+3601
    assert json.loads(cache.response("summary")[0])["rows"]==[]
    wall[0]=T-6
    assert json.loads(cache.response("summary")[0])["state"]=="UNKNOWN"


def test_k_slices_precomputed_without_changing_n_or_denominator(store):
    cache,_,_=store
    cache.publish(encoded(fixture(strategies=50)))
    for k in (30,40,50):
        data=json.loads(cache.response("board:"+str(k))[0])
        assert len(data["rows"])==k
        assert all(r["scope_batches"]==1 for r in data["rows"])
    assert b"Strategy 49" in cache.response("detail:49",html=True)[0]
    summary=json.loads(cache.response("summary")[0])
    assert (summary["n_selection"],summary["variants_tried"],summary["n_grammar"])==(840,8,181442)
    assert D.app.test_client().get('/api/research/twoyear/leaderboard?k=31').status_code==400


@pytest.mark.parametrize("change",[lambda d:d.update(live=True),lambda d:d.update(basis="net-after-costs"),lambda d:d.update(n_selection=832),
    lambda d:d.update(schema="old"),lambda d:d.update(freeze_sha=None),lambda d:d.update(source_as_of="no clock"),
    lambda d:d.update(ranking=d["ranking"]*20),lambda d:d.update(provenance=[{}]*129),lambda d:d.update(distributions={"B1":{"bins":[{}]*33}})])
def test_invalid_snapshot_keeps_last_good(change,store):
    cache,_,_=store
    cache.publish(encoded(fixture()))
    old=cache.entry
    data=fixture()
    change(data)
    with pytest.raises((ValueError,TypeError)):
        cache.publish(encoded(data))
    assert cache.entry is old


def test_pin15_strict_boundary_signed_forward_and_unresolved(store):
    cache,_,_=store
    data=fixture()
    assert data["ranking"][0]["accounting"]["board_review_5pct"] is False  # exactly 5%
    data["ranking"][0]["accounting"]=accounting(b=2,c=1)
    cache.publish(encoded(data))
    for fill,gross,diff in ((7,10,3),(12,10,-2),(10,10,0),(-2,10,12)):
        data["forward"]={"fill_model_bp":fill,"gross_mark_bp":gross,"slippage_signed_bp":diff,
                         "same_signals_timestamps":True,"backtest_expectancy_bp":1,"ratio":.5,"deciding_record":"(i) fill model","gross_role":"ratio only"}
        cache.publish(encoded(data))
        assert V.SIGN in cache.response("findings_html",html=True)[0].decode()
    data["forward"]["slippage_signed_bp"]=-12
    with pytest.raises(ValueError,match="signed"):
        cache.publish(encoded(data))
    data["forward"]=None
    data["ranking"][0]["accounting"]=accounting(unresolved=1)
    with pytest.raises(ValueError,match="UNKNOWN P&L"):
        cache.publish(encoded(data))
    data["ranking"][0]["gross_pnl_bp"]=None
    with pytest.raises(ValueError,match="UNKNOWN P&L"):
        cache.publish(encoded(data))
    V.validate_accounting(data["ranking"][0]["accounting"],data["accounting_policy"])
    data["ranking"][0]["accounting"]["no_fill_wins"]=1
    with pytest.raises(ValueError,match="reconciliation"):
        cache.publish(encoded(data))
    zero=accounting(filled=0,nofill=0,b=0,c=0)
    assert zero["board_review_5pct"] is None
    V.validate_accounting(zero,data["accounting_policy"])


def test_caps_before_parse_huge_and_growing(tmp_path,monkeypatch):
    path=tmp_path/V.SNAPSHOT_NAME
    with path.open("wb") as handle:
        handle.truncate(64*1024**2)
    monkeypatch.setattr(V,"validate",lambda raw:pytest.fail("oversized file parsed"))
    with pytest.raises(ValueError,match="byte cap"):
        L.read_capped(tmp_path)
    path.write_bytes(b"{}")
    class Growing:
        def __enter__(self):
            self.h=path.open("r+b")
            return self
        def fileno(self): return self.h.fileno()
        def read(self,n):
            assert 0<n<=65536
            self.h.seek(0,2)
            self.h.write(b"x"*(V.BYTE_CAP+1))
            self.h.flush()
            self.h.seek(0)
            return self.h.read(n)
        def __exit__(self,*a): self.h.close()
    with pytest.raises(ValueError,match="byte cap"):
        L.read_capped(tmp_path,opener=lambda p:Growing())


def _blocked_child(connection):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)  # HM c3b9f820cfe9, RK dfdd73862d40, HM C3
    connection.recv_bytes()
    threading.Event().wait(10)


def test_real_blocked_loader_terminated_reaped_and_global_slot(store,monkeypatch,tmp_path):
    cache,_,_=store
    context=multiprocessing.get_context("fork" if os.name!="nt" else "spawn")
    parent,child=context.Pipe()
    process=context.Process(target=_blocked_child,args=(child,))
    # C4: non-exiting parent handler so the fork inherits a leak regardless of CI order (HM 5757b40486b3 / 1066e0fcff07).
    prev = signal.signal(signal.SIGTERM, lambda s, f: None)
    try:
        process.start()
        child.close()
        monkeypatch.setattr(L,"_LAST_ATTEMPT",None)
        adapter=L.SupervisorCacheAdapter(process,parent,tmp_path,cache,platform_verified=True)
        start=time.monotonic()
        assert not adapter.tick()
        elapsed=time.monotonic()-start
        # Bounds tied to the loader: watchdog Timer(.100) (study_snapshot_loader L128) and
        # _stop join(timeout=.025) (L90), plus one explicit scheduling SLACK for shared CI
        # runners (CI measured 0.1278 s vs the old zero-slack .125; RK 504529584c5c/5f8da51ad076, HM).
        WATCHDOG_S,JOIN_S,SLACK_S=.100,.025,.100
        assert adapter.stop_elapsed is not None,"stop/reap not complete when tick returned"
        assert adapter.stop_elapsed<=JOIN_S+SLACK_S
        assert elapsed<WATCHDOG_S+JOIN_S+SLACK_S
        process.join(JOIN_S+SLACK_S)
        assert not process.is_alive()
        assert process.exitcode == -signal.SIGTERM
        assert adapter.latched
        assert not adapter.tick()
        assert adapter.stopped_and_reaped is True
    finally:
        if process.is_alive(): process.terminate()
        process.join(.1)
        parent.close()
        signal.signal(signal.SIGTERM, prev)


def test_requests_never_refresh_under_concurrent_expiry(store,monkeypatch):
    cache,mono,_=store
    cache.publish(encoded(fixture()))
    mono[0]=6
    monkeypatch.setattr(L,"read_capped",lambda *a,**k:pytest.fail("GET refreshed"))
    def request(i):
        with D.app.test_client() as c:
            start=time.monotonic()
            response=c.get('/twoyear-batch' if i%2 else '/api/research/twoyear/summary')
            return response,time.monotonic()-start
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        results=list(pool.map(request,range(96)))
    assert all(r.status_code==200 and elapsed<.275 for r,elapsed in results)
    assert all(b'STALE-RED' in r.data for r,_ in results)


def test_nav_and_standalone_baseline(store,tmp_path,monkeypatch):
    page=D.app.test_client().get('/details').data.decode()
    for sid,_,_ in D.REPORT_NAV:
        assert 'id="sec-'+sid+'"' in page
    assert [g[0] for g in D.REPORT_NAV_GROUPS]==['overview','twoyear','strategy-findings-group','data-health','archive']
    monkeypatch.setattr('sys.argv',["dashboard","--standalone","--data-root",str(tmp_path)])
    monkeypatch.setattr(D,"BIND_HOST","0.0.0.0")
    with pytest.raises(ValueError,match="127.0.0.1:9001"):
        D.main()


def test_structural_caps_duplicate_keys_and_exact_byte_boundaries(store,tmp_path,monkeypatch):
    cache,_,_=store
    raw=encoded(fixture())
    atcap=raw+b" "*(V.BYTE_CAP-len(raw))
    path=tmp_path/V.SNAPSHOT_NAME
    path.write_bytes(atcap)
    assert L.read_capped(tmp_path)==atcap
    with pytest.raises(ValueError,match="byte cap"):
        V.validate(atcap+b" ")
    with pytest.raises(ValueError,match="duplicate"):
        V.validate(b'{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError,match="record cap"):
        V.validate(b'{"rows":['+b'0,'*4096+b'0]}')
    with pytest.raises(ValueError,match="nesting"):
        V.validate(b'['*13+b'0'+b']'*13)
    data=fixture()
    data["too_long"]="é"*1025
    with pytest.raises(ValueError,match="string"):
        V.validate(encoded(data))
    assert V.SnapshotStore().entry is None
    with pytest.raises(ValueError,match="chain card PENDING"):
        V.SnapshotStore().publish(raw)


def test_global_five_second_schedule_and_failure_latch(store,monkeypatch,tmp_path):
    cache,_,_=store
    now=[0.0]
    class Process:
        def is_alive(self): return True
    class Pipe:
        def __init__(self): self.sent=0; self.good=True
        def send_bytes(self,b): self.sent+=1
        def poll(self,t): return True
        def recv_bytes(self,n): return b'O'+encoded(fixture()) if self.good else b'Ebad source'
    monkeypatch.setattr(L,"_LAST_ATTEMPT",None)
    pipe1,pipe2=Pipe(),Pipe()
    a=L.SupervisorCacheAdapter(Process(),pipe1,tmp_path,cache,platform_verified=True,clock=lambda:now[0])
    b=L.SupervisorCacheAdapter(Process(),pipe2,tmp_path,cache,platform_verified=True,clock=lambda:now[0])
    assert a.tick()
    assert not b.tick() and pipe2.sent==0
    now[0]=5
    pipe1.good=False
    assert not a.tick() and a.latched
    now[0]=10
    assert not a.tick() and pipe1.sent==2


def test_partial_scope_and_prebound_hash_mismatch_rejected(store):
    cache,_,_=store
    data=fixture()
    data["chain_sha"]='b'*64
    with pytest.raises(ValueError,match="identity mismatch"):
        cache.publish(encoded(data))
    data=fixture()
    data["batches"][5].update(status="complete",counted_for_ranking=True,count_reconciled=True,role_release_sha=HASH)
    with pytest.raises(ValueError,match="sealed batch|maximum partial scope"):
        cache.publish(encoded(data))


def test_forward_unknown_unmatched_and_nonpositive_reference(store):
    cache,_,_=store
    data=fixture()
    data["forward"]={"fill_model_bp":None,"gross_mark_bp":10,"slippage_signed_bp":0}
    with pytest.raises(ValueError,match="UNKNOWN difference"):
        cache.publish(encoded(data))
    data["forward"]={"fill_model_bp":7,"gross_mark_bp":10,"slippage_signed_bp":3,"same_signals_timestamps":False}
    with pytest.raises(ValueError,match="signal-time"):
        cache.publish(encoded(data))
    data["forward"]={"fill_model_bp":7,"gross_mark_bp":10,"slippage_signed_bp":3,"same_signals_timestamps":True,"deciding_record":"(i) fill model","gross_role":"ratio only","backtest_expectancy_bp":0,"ratio":.5}
    with pytest.raises(ValueError,match="positive comparable"):
        cache.publish(encoded(data))
    data["forward"]["ratio"]=None
    cache.publish(encoded(data))


def test_no_h1_release_or_live_constraint_can_be_inferred(store,tmp_path,monkeypatch):
    cache,_,_=store
    data=fixture()
    data["batches"][1].update(status="complete",counted_for_ranking=True,count_reconciled=True)
    with pytest.raises(ValueError,match="role release"):
        cache.publish(encoded(data))
    monkeypatch.setattr('sys.argv',["dashboard","--standalone","--data-root",str(tmp_path)])
    monkeypatch.setenv("FORCE_PAPER_MODE","0")
    with pytest.raises(ValueError,match="paper-only"):
        D.main()


def test_capped_reader_rejects_symlink(tmp_path):
    real=tmp_path/'real.json'
    real.write_bytes(encoded(fixture()))
    (tmp_path/V.SNAPSHOT_NAME).symlink_to(real)
    with pytest.raises(ValueError,match="non-symlink"):
        L.read_capped(tmp_path)


def test_loader_failure_reason_is_precompiled_and_preserves_source_time(store):
    cache,_,_=store
    cache.publish(encoded(fixture()))
    cache.reason="loader UNAVAILABLE / deadline latched"
    body=cache.response("html:40",html=True)[0]
    assert b"deadline latched" in body and STAMP.encode() in body
    assert b"writer UNKNOWN" in body
    assert json.loads(cache.response("summary")[0])["rows"]==[]


def test_release_and_all_bindings_are_required(store):
    cache,_,_=store
    with pytest.raises(ValueError,match="all immutable"):
        V.SnapshotStore(bindings={"chain_sha":HASH})
    data=fixture()
    data["batches"][1].update(status="complete",counted_for_ranking=True,count_reconciled=True,role_release_sha=HASH)
    data["ranked_batch_ids"]=["B1","B2"]
    data["ranked_batch_count"]=2
    for row in data["ranking"]: row["scope_batches"]=2
    with pytest.raises(ValueError,match="immutable approved"):
        cache.publish(encoded(data))
