"""Forward-only admitted scan census, not a continuous market-coverage claim."""
import contextvars
import functools
import hashlib
import json
import math
import uuid
from pathlib import Path
from collector_v22_provisional import _atomic_write_unlocked
from research.mirror_generation_lease import MirrorGenerationLease

_BOOT=uuid.uuid4().hex
_CURRENT=contextvars.ContextVar('research_scan_census',default=None)


def canonical_scan_store(root,epoch_id):
    from research_v3_store import V3EvidenceStore
    return V3EvidenceStore(root,epoch_id=epoch_id)


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _read(path,limit=262144):
    with path.open('rb') as stream: raw=stream.read(limit+1)
    if len(raw)>limit: raise ValueError('SCAN_CENSUS_READ_LIMIT')
    return json.loads(raw)


class ScanCensus:
    def __init__(self,store,*,clock,boot_id=_BOOT):
        self.store=store; self.clock=clock; self.boot=boot_id
        self.root=Path(store.root)/'v3/receipts/scan-census'/hashlib.sha256(store.epoch_id.encode()).hexdigest()[:16]
        self.path=self.root/'state.json'

    def _save(self,state):
        state=dict(state); state['sha256']=_hash({k:v for k,v in state.items() if k!='sha256'})
        _atomic_write_unlocked(self.path,state)

    def _load(self):
        if not self.path.exists():
            return {'schema':'scan_census_state_v1','epoch_id':self.store.epoch_id,'sequence':0,
                'boot_id':None,'active':{},'pending':None}
        state=_read(self.path)
        if state.get('epoch_id')!=self.store.epoch_id or state.get('sha256')!=_hash({k:v for k,v in state.items() if k!='sha256'}):
            raise ValueError('SCAN_CENSUS_STATE_INVALID')
        return state

    def _flush(self,state):
        pending=state.get('pending')
        if pending is None: return
        # Do not re-author old pending material under a different deployment.
        if pending['identity']!=self.store._identity_binding():
            raise ValueError('SCAN_CENSUS_PENDING_REVISION_CHANGED')
        result=self.store.append('decision',pending['row'])
        if result.get('blocked') or result.get('deferred') or not (result.get('written') or result.get('duplicate')):
            raise ValueError('SCAN_CENSUS_WRITE_NOT_DURABLE')
        # The terminal transition and pending-clear are one journal replace.
        # A crash before it replays the frozen row; a crash after it cannot
        # regenerate a second timestamp for the same terminal record ID.
        if pending['row'].get('decision_stage')=='SCAN_DISPOSITION':
            state['active'].pop(pending['row']['scan_id'],None)
        state['pending']=None; self._save(state)

    def _write(self,state,row):
        state['pending']={'identity':self.store._identity_binding(),'row':row}
        self._save(state); self._flush(state)

    def _time(self):
        value=self.clock()
        if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
            raise ValueError('SCAN_CENSUS_TIME_INVALID')
        return value

    def admit(self):
        lease=MirrorGenerationLease(self.root,owner='scan-census-writer'); lease.acquire(timeout_seconds=0)
        try:
            state=self._load(); self._flush(state)
            if state['boot_id']!=self.boot:
                previous=state['boot_id']; state['boot_id']=self.boot
                self._write(state,{'record_id':'scan-census-boot:'+self.boot,'decision_stage':'SCAN_CENSUS_BOOT',
                    'observed_ts':self._time(),'previous_boot_id':previous,
                    'unresolved_scan_ids':sorted(state['active']),
                    'continuity':'FORWARD_START' if previous is None else 'RESTART_GAP_UNKNOWN',
                    'continuous_market_coverage':False})
            if len(state['active'])>=64: raise ValueError('SCAN_CENSUS_ACTIVE_LIMIT')
            state['sequence']+=1
            scan='scan-census-'+hashlib.sha256(self.store.epoch_id.encode()).hexdigest()[:16]+'-'+str(state['sequence'])
            state['active'][scan]={'sequence':state['sequence'],'boot_id':self.boot}
            self._write(state,{'record_id':'scan-admission:'+scan,'decision_stage':'SCAN_ADMISSION',
                'scan_id':scan,'scan_sequence':state['sequence'],'boot_id':self.boot,
                'observed_ts':self._time(),'expected_directions':['LONG','SHORT'],
                'coverage_scope':'FRESH_AI_SCAN_INVOCATIONS_ONLY','qualification_eligible':False})
            return scan
        finally: lease.release()

    def finish(self,scan,*,refs,raised=False,verdicts=()):
        lease=MirrorGenerationLease(self.root,owner='scan-census-writer'); lease.acquire(timeout_seconds=0)
        try:
            state=self._load(); self._flush(state)
            if scan not in state['active']:
                return self._verify_finished(scan,refs=refs,raised=raised,verdicts=verdicts)
            self._write(state,{'record_id':'scan-disposition:'+scan,'decision_stage':'SCAN_DISPOSITION',
                'scan_id':scan,'scan_sequence':state['active'][scan]['sequence'],'observed_ts':self._time(),
                'disposition':'EXCEPTION_UNKNOWN' if raised else 'OPPORTUNITY_RECORDED' if refs else 'NO_OPPORTUNITY_UNKNOWN',
                'opportunity_references':refs,'async_fanout_coverage':'UNPROVEN',
                'observed_lane_verdicts':sorted(set(verdicts)),
                'qualification_blockers':['ASYNC_FANOUT_COVERAGE_UNPROVEN',
                    'CONTINUOUS_COLLECTION_COVERAGE_UNPROVEN']+([] if refs else ['NO_CANONICAL_OPPORTUNITY_FOR_SCAN']),
                'qualification_eligible':False})
        finally: lease.release()

    def fanout(self,scan,*,lane,policy_signature,job_key,payload_sha256,admitted=None):
        import re
        if (not re.fullmatch(r'[A-Z0-9_]{1,64}',lane)
                or not isinstance(policy_signature,str) or not 0<len(policy_signature)<=128
                or job_key!=lane+':'+scan or not re.fullmatch('[0-9a-f]{64}',payload_sha256)):
            raise ValueError('SCAN_FANOUT_IDENTITY_INVALID')
        identity={'scan_id':scan,'research_lane':lane,'policy_signature':policy_signature,
                  'job_key':job_key,'payload_sha256':payload_sha256}
        key=_hash(identity)
        lease=MirrorGenerationLease(self.root,owner='scan-fanout-writer'); lease.acquire(timeout_seconds=0)
        try:
            state=self._load(); self._flush(state)
            active=state['active'].get(scan)
            if active is None: raise ValueError('SCAN_FANOUT_PARENT_NOT_ACTIVE')
            plans=active.setdefault('fanout',{})
            if key not in plans:
                if admitted is not None: raise ValueError('SCAN_FANOUT_PLAN_MISSING')
                if len(plans)>=16: raise ValueError('SCAN_FANOUT_PLAN_LIMIT')
                if any(p['identity']['job_key']==job_key for p in plans.values()):
                    raise ValueError('SCAN_FANOUT_JOB_CONFLICT')
                plans[key]={'identity':identity}
                self._write(state,{'record_id':'scan-fanout-plan:'+key,
                    'decision_stage':'SCAN_FANOUT_PLAN',**identity,'observed_ts':self._time(),
                    'dispatch_kind':'ASYNC_COMBO_LANE_EXECUTION',
                    'completion_status':'UNKNOWN','qualification_eligible':False})
            if admitted is not None and 'admission' not in plans[key]:
                status='ENQUEUED' if admitted is True else 'ADMISSION_UNKNOWN'
                plans[key]['admission']=status
                self._write(state,{'record_id':'scan-fanout-admission:'+key,
                    'decision_stage':'SCAN_FANOUT_ADMISSION',**identity,
                    'plan_record_id':'scan-fanout-plan:'+key,'observed_ts':self._time(),
                    'admission_status':status,'completion_status':'UNKNOWN','qualification_eligible':False})
            record_id='scan-fanout-plan:'+key
            receipt=_read(self.store._record_receipt_path('decision',record_id))
            if receipt.get('state')!='COMMITTED': raise ValueError('SCAN_FANOUT_PLAN_NOT_COMMITTED')
            return {'schema':'scan_fanout_plan_reference_v1','plan_record_id':record_id,
                'plan_identity':identity,'source_identity':self.store._identity_binding(),
                'row_sha256':receipt['row_sha256'],'byte_offset':receipt['offset'],
                'row_length':receipt['length']}
        finally: lease.release()

    def _verify_finished(self,scan,*,refs,raised,verdicts):
        record_id='scan-disposition:'+scan
        try:
            receipt=_read(self.store._record_receipt_path('decision',record_id))
            offset,length=receipt['offset'],receipt['length']
            if (receipt.get('state')!='COMMITTED' or type(offset) is not int or offset<0
                    or type(length) is not int or not 0<length<=1048576):
                raise ValueError('SCAN_CENSUS_FINISHED_RECEIPT_INVALID')
            with self.store.ledger_path('decision').open('rb') as stream:
                stream.seek(offset); raw=stream.read(length)
            row=json.loads(raw)
            disposition='EXCEPTION_UNKNOWN' if raised else 'OPPORTUNITY_RECORDED' if refs else 'NO_OPPORTUNITY_UNKNOWN'
            if (hashlib.sha256(raw).hexdigest()!=receipt['row_sha256'] or row.get('record_id')!=record_id
                    or row.get('scan_id')!=scan or row.get('epoch_id')!=self.store.epoch_id
                    or row.get('opportunity_references')!=refs or row.get('disposition')!=disposition
                    or row.get('observed_lane_verdicts')!=sorted(set(verdicts))):
                raise ValueError('SCAN_CENSUS_FINISHED_PAYLOAD_CONFLICT')
            return {'duplicate':True,'record_id':record_id}
        except (OSError,KeyError,TypeError):
            raise ValueError('SCAN_CENSUS_ADMISSION_OR_FINISHED_RECEIPT_MISSING') from None


def record_current_fanout(lane,policy_signature,job_key,payload,*,admitted=None,payload_sha256=None):
    current=_CURRENT.get()
    if current is None: return None
    return current['census'].fanout(current['scan'],lane=lane,policy_signature=policy_signature,
        job_key=job_key,payload_sha256=payload_sha256 if payload_sha256 is not None else _hash(payload),admitted=admitted)


def observe_opportunity(store,write,policy_decision=None):
    current=_CURRENT.get()
    if current is None: return
    if policy_decision in {'ACCEPT','REJECT','ERROR','NO_TRADE','AI_NOT_CALLED'}:
        current['verdicts'].append(policy_decision)
    try:
        if len(current['refs'])>=8: raise ValueError('SCAN_CENSUS_REFERENCE_LIMIT')
        receipt=_read(store._record_receipt_path('opportunity',write['record_id']))
        if receipt.get('state')!='COMMITTED': raise ValueError('SCAN_CENSUS_OPPORTUNITY_NOT_COMMITTED')
        offset,length=receipt['offset'],receipt['length']
        if type(offset) is not int or offset<0 or type(length) is not int or not 0<length<=1048576:
            raise ValueError('SCAN_CENSUS_REFERENCE_LIMIT')
        with store.ledger_path('opportunity').open('rb') as stream:
            stream.seek(offset); raw=stream.read(length)
        if hashlib.sha256(raw).hexdigest()!=receipt['row_sha256']: raise ValueError('SCAN_CENSUS_REFERENCE_HASH')
        row=json.loads(raw)
        if row.get('shared_ai_call_id')!=current['scan'] or row.get('epoch_id')!=current['epoch_id']:
            raise ValueError('SCAN_CENSUS_REFERENCE_IDENTITY')
        sides=(row.get('baseline_schedule_snapshot') or {}).get('directional_schedules') or {}
        ref={'record_id':row['record_id'],'episode_id':row['episode_id'],'row_sha256':receipt['row_sha256'],
             'byte_offset':offset,'row_length':length,'directions':{side:(sides.get(side) or {}).get('capture_signature') for side in ('LONG','SHORT')}}
        if ref not in current['refs']: current['refs'].append(ref)
    except (OSError,ValueError,KeyError,TypeError):
        current['reference_failure']=True


def wrap_scan_census(function,*,eligible,store_factory,clock,on_failure):
    def notify(code):
        try: on_failure(code)
        except Exception: pass
    @functools.wraps(function)
    def wrapped(event):
        if not eligible(event): return function(event)
        try:
            census=ScanCensus(store_factory(),clock=clock); scan=census.admit()
        except Exception:
            notify('SCAN_CENSUS_ADMISSION_FAILED')
            return {'entry_resolution':'NO_ORDER','exact_reason':'SCAN_CENSUS_ADMISSION_FAILED'}
        current={'scan':scan,'census':census,'epoch_id':census.store.epoch_id,'refs':[],'reference_failure':False,'verdicts':[]}; token=_CURRENT.set(current)
        raised=False
        try:
            return function({**event,'research_scan_id':scan})
        except BaseException:
            raised=True; raise
        finally:
            _CURRENT.reset(token)
            try: census.finish(scan,refs=[] if current['reference_failure'] else current['refs'],raised=raised,verdicts=current['verdicts'])
            except Exception: notify('SCAN_CENSUS_DISPOSITION_FAILED')
    return wrapped
