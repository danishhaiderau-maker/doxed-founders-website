"""Bounded local observed-child index; never an exhaustive fanout certificate."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from research.local_dynamic_input import _check, _source, _directory, _encoded
from research.mirror_generation_lease import MirrorGenerationLease
from collector_v22_provisional import _atomic_write_unlocked


def diagnostic_code(error):
    """Exact known codes only; never echo arbitrary exception text."""
    from research.mirror_generation_lease import MirrorGenerationLeaseTimeout
    if isinstance(error,MirrorGenerationLeaseTimeout): return 'CENSUS_MIRROR_LEASE_BUSY'
    if isinstance(error,sqlite3.Error):
        code=getattr(error,'sqlite_errorcode',None)
        if code==sqlite3.SQLITE_INTERRUPT: return 'CENSUS_INDEX_DEADLINE'
        if code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED): return 'CENSUS_INDEX_BUSY'
        return 'CENSUS_INDEX_FAILED'
    allowed={'CENSUS_REFERENCE_CURSOR_INVALID','CENSUS_BYTE_BUDGET_INVALID','CENSUS_CONFIG_REQUIRED',
        'CENSUS_HELD_LEASE_INVALID','CENSUS_EXPECTED_SOURCE_MISMATCH','CENSUS_ROW_UNSUPPORTED',
        'CENSUS_ROW_BINDING_CONFLICT','CENSUS_RECEIPT_LIMIT','CENSUS_RECEIPT_MISMATCH',
        'CENSUS_DUPLICATE_CONFLICT','CENSUS_SOURCE_CHANGED','CENSUS_GENERATION_CHANGED',
        'CENSUS_CACHED_REFERENCE_INVALID','CENSUS_ADMISSION_AMBIGUOUS','CENSUS_ADMISSION_CONFLICT',
        'MIRROR_SYNC_IN_PROGRESS','MIRROR_SYNC_RECEIPT_FAILED','MIRROR_REVISION_PARITY_NOT_MATCH',
        'MIRROR_REVISION_IDENTITY_MISMATCH','MIRROR_SYNC_RECEIPT_MISSING','MIRROR_SYNC_RECEIPT_INVALID',
        'MIRROR_SYNC_STATE_INVALID','MIRROR_EPOCH_IDENTITY_MISSING','MIRROR_SOURCE_IDENTITY_CHANGED',
        'DISPATCH_PARENT_ADMISSION_UNKNOWN','DISPATCH_PARENT_ADMISSION_CONFLICT',
        'DISPATCH_PLAN_IDENTITY_INVALID','DISPATCH_ADMISSION_CONFLICT',
        'DISPATCH_CHILD_REFERENCE_CONFLICT','DISPATCH_CHILD_SOURCE_CONFLICT',
        'DISPATCH_RESOLUTION_UNKNOWN','DISPATCH_RESOLUTION_CONFLICT'}
    message=str(error)
    return message if message in allowed else 'SCAN_RECONCILIATION_FAILED'


def _verify_page_ref(root, ref, source, config):
    if ref.get('ledger') not in ('decision','opportunity','lifecycle'):
        raise ValueError('CENSUS_CACHED_REFERENCE_INVALID')
    ledger=ref['ledger']; offset=ref.get('byte_offset'); length=ref.get('row_length')
    if type(offset) is not int or offset<0 or type(length) is not int or not 0<length<=1048576:
        raise ValueError('CENSUS_CACHED_REFERENCE_INVALID')
    with (Path(root)/'v3/ledgers'/f'{ledger}.jsonl').open('rb') as stream:
        stream.seek(offset); raw=stream.read(length)
    digest=hashlib.sha256(raw).hexdigest(); row=json.loads(raw)
    rid=row.get('record_id'); scan=row.get('scan_id') if ledger=='decision' else row.get('shared_ai_call_id')
    directions=(row.get('baseline_schedule_snapshot') or {}).get('directional_schedules') or {}
    if (digest!=ref.get('row_sha256') or rid!=ref.get('record_id') or scan!=ref.get('scan_id')
            or row.get('epoch_id')!=source['epoch'] or row.get('source_revision')!=source['revision']
            or row.get('tile_config_signature')!=config
            or ref.get('directions')!={side:(directions.get(side) or {}).get('capture_signature') for side in ('LONG','SHORT')}):
        raise ValueError('CENSUS_CACHED_REFERENCE_INVALID')
    path=Path(root)/'v3/receipts/emergency_record_idempotency_v1'/ledger/(hashlib.sha256(f'{ledger}\0{rid}'.encode()).hexdigest()+'.json')
    with path.open('rb') as handle: receipt_raw=handle.read(262145)
    if len(receipt_raw)>262144: raise ValueError('CENSUS_RECEIPT_LIMIT')
    receipt=json.loads(receipt_raw)
    if (receipt.get('state')!='COMMITTED' or receipt.get('offset')!=offset
            or receipt.get('length')!=length or receipt.get('row_sha256')!=digest):
        raise ValueError('CENSUS_RECEIPT_MISMATCH')
    return row


def reconcile_scans(*, repo_root, data_root, source_revision, config_signature, now=None,
                    max_bytes=1048576, reference_after='', held_lease=None, expected_source=None, dispatch_after=''):
    if not isinstance(dispatch_after,str) or len(dispatch_after)>256:
        raise ValueError('CENSUS_REFERENCE_CURSOR_INVALID')
    if not isinstance(reference_after,str) or len(reference_after)>256:
        raise ValueError('CENSUS_REFERENCE_CURSOR_INVALID')
    if type(max_bytes) is not int or not 1 <= max_bytes <= 2097152:
        raise ValueError('CENSUS_BYTE_BUDGET_INVALID')
    if not isinstance(config_signature,str) or not config_signature:
        raise ValueError('CENSUS_CONFIG_REQUIRED')
    directory=_directory(repo_root,data_root).parent/'scan-reconciliation'
    directory.mkdir(parents=True,exist_ok=True)
    lease=held_lease or MirrorGenerationLease(data_root,owner='local-scan-reconciliation')
    if held_lease is not None:
        if (not isinstance(held_lease,MirrorGenerationLease) or not held_lease.held
                or held_lease.path.resolve()!=MirrorGenerationLease(data_root).path.resolve()):
            raise ValueError('CENSUS_HELD_LEASE_INVALID')
    else: lease.acquire(timeout_seconds=0)
    try:
        source=_source(_check(repo_root,data_root,source_revision,now=now))
        if expected_source is not None and source!=expected_source:
            raise ValueError('CENSUS_EXPECTED_SOURCE_MISMATCH')
        binding={'source':source,'config_signature':config_signature,'index_contract':'scan_dispatch_refs_v2'}
        job=hashlib.sha256(_encoded(binding)).hexdigest()
        with sqlite3.connect(directory/'index.sqlite',timeout=1) as db:
            deadline=time.monotonic()+5
            db.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
            db.execute('CREATE TABLE IF NOT EXISTS cursors(job TEXT, ledger TEXT, fingerprint TEXT, offset INTEGER, PRIMARY KEY(job,ledger))')
            db.execute('CREATE TABLE IF NOT EXISTS refs(job TEXT,ledger TEXT,id TEXT,scan TEXT,digest TEXT,stage TEXT,reference TEXT, PRIMARY KEY(job,ledger,id))')
            db.execute('CREATE INDEX IF NOT EXISTS refs_scan_stage ON refs(job,scan,stage)')
            budget=max_bytes; complete=True
            for ledger in ('decision','opportunity','lifecycle'):
                path=Path(data_root)/'v3/ledgers'/f'{ledger}.jsonl'
                if not path.exists() and ledger=='lifecycle':
                    db.execute('DELETE FROM refs WHERE job=? AND ledger=?',(job,ledger))
                    db.execute('DELETE FROM cursors WHERE job=? AND ledger=?',(job,ledger))
                    continue
                stat=path.stat(); fingerprint=json.dumps([stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns])
                previous=db.execute('SELECT fingerprint,offset FROM cursors WHERE job=? AND ledger=?',(job,ledger)).fetchone()
                offset=previous[1] if previous and previous[0]==fingerprint else 0
                if previous and previous[0]!=fingerprint:
                    db.execute('DELETE FROM refs WHERE job=? AND ledger=?',(job,ledger))
                with path.open('rb') as stream:
                    stream.seek(offset)
                    while budget and stream.tell()<stat.st_size:
                        start=stream.tell(); raw=stream.readline(min(budget,1048576)+1)
                        if len(raw)>budget or not raw.endswith(b'\n'):
                            if budget==max_bytes or len(raw)>1048576: raise ValueError('CENSUS_ROW_UNSUPPORTED')
                            break
                        budget-=len(raw); offset=stream.tell()
                        if not raw.strip(): continue
                        row=json.loads(raw)
                        scan=row.get('scan_id') if ledger=='decision' else row.get('shared_ai_call_id')
                        if not isinstance(scan,str) or not scan.startswith('scan-census-'): continue
                        if (row.get('epoch_id')!=source['epoch'] or row.get('source_revision')!=source['revision']
                                or row.get('tile_config_signature')!=config_signature):
                            raise ValueError('CENSUS_ROW_BINDING_CONFLICT')
                        rid=row['record_id']; digest=hashlib.sha256(raw).hexdigest()
                        receipt_path=Path(data_root)/'v3/receipts/emergency_record_idempotency_v1'/ledger/(hashlib.sha256(f'{ledger}\0{rid}'.encode()).hexdigest()+'.json')
                        with receipt_path.open('rb') as handle: receipt_raw=handle.read(262145)
                        if len(receipt_raw)>262144: raise ValueError('CENSUS_RECEIPT_LIMIT')
                        receipt=json.loads(receipt_raw)
                        if (receipt.get('state')!='COMMITTED' or receipt.get('offset')!=start
                                or receipt.get('length')!=len(raw) or receipt.get('row_sha256')!=digest):
                            raise ValueError('CENSUS_RECEIPT_MISMATCH')
                        old=db.execute('SELECT digest FROM refs WHERE job=? AND ledger=? AND id=?',(job,ledger,rid)).fetchone()
                        if old and old[0]!=digest: raise ValueError('CENSUS_DUPLICATE_CONFLICT')
                        directions=(row.get('baseline_schedule_snapshot') or {}).get('directional_schedules') or {}
                        reference={'record_id':rid,'scan_id':scan,'ledger':ledger,
                            'row_sha256':digest,'byte_offset':start,'row_length':len(raw),
                            'directions':{side:(directions.get(side) or {}).get('capture_signature') for side in ('LONG','SHORT')}}
                        db.execute('INSERT OR IGNORE INTO refs VALUES(?,?,?,?,?,?,?)',(job,ledger,rid,scan,digest,row.get('decision_stage'),json.dumps(reference)))
                complete &= offset==stat.st_size
                after=path.stat()
                if json.dumps([after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns])!=fingerprint:
                    raise ValueError('CENSUS_SOURCE_CHANGED')
                db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?)',(job,ledger,fingerprint,offset))
            if _source(_check(repo_root,data_root,source_revision,now=now))!=source:
                raise ValueError('CENSUS_GENERATION_CHANGED')
            count=db.execute("SELECT COUNT(*) FROM refs o WHERE o.job=? AND o.ledger='opportunity' AND EXISTS (SELECT 1 FROM refs a WHERE a.job=o.job AND a.scan=o.scan AND a.stage='SCAN_ADMISSION')",(job,)).fetchone()[0]
            refs=[json.loads(row[0]) for row in db.execute("SELECT o.reference FROM refs o WHERE o.job=? AND o.ledger='opportunity' AND o.id>? AND EXISTS (SELECT 1 FROM refs a WHERE a.job=o.job AND a.scan=o.scan AND a.stage='SCAN_ADMISSION') ORDER BY o.id LIMIT 9",(job,reference_after))] if complete else []
            more=len(refs)>8; refs=refs[:8]
            for ref in refs:
                _verify_page_ref(data_root,ref,source,config_signature)
                admission=db.execute("SELECT reference FROM refs WHERE job=? AND scan=? AND stage='SCAN_ADMISSION' LIMIT 2",(job,ref['scan_id'])).fetchall()
                if len(admission)!=1: raise ValueError('CENSUS_ADMISSION_AMBIGUOUS')
                original=_verify_page_ref(data_root,json.loads(admission[0][0]),source,config_signature)
                if original.get('decision_stage')!='SCAN_ADMISSION' or original.get('scan_id')!=ref['scan_id']:
                    raise ValueError('CENSUS_ADMISSION_CONFLICT')
            dispatches=[]
            if complete:
                from research.scan_dispatch_verification import verify_dispatch
                plans=db.execute("SELECT reference,scan FROM refs WHERE job=? AND stage='SCAN_FANOUT_PLAN' AND id>? ORDER BY id LIMIT 9",(job,dispatch_after)).fetchall()
                for payload,scan in plans[:8]:
                    admissions=[json.loads(r[0]) for r in db.execute("SELECT reference FROM refs WHERE job=? AND scan=? AND stage='SCAN_FANOUT_ADMISSION' LIMIT 17",(job,scan))]
                    children=[json.loads(r[0]) for r in db.execute("SELECT reference FROM refs WHERE job=? AND scan=? AND ledger='lifecycle' LIMIT 33",(job,scan))]
                    parents=[json.loads(r[0]) for r in db.execute("SELECT reference FROM refs WHERE job=? AND scan=? AND stage='SCAN_ADMISSION' LIMIT 2",(job,scan))]
                    dispatches.append(verify_dispatch(data_root,json.loads(payload),admissions,children,source,config_signature,parents))
            result={'schema':'local_scan_reconciliation_v1','binding':binding,'index_caught_up':complete,
                'observed_joined_opportunity_rows':count if complete else None,
                'sample_original_references':refs,'reference_sample_truncated':more,
                'next_reference_cursor':refs[-1]['record_id'] if more else None,
                'observed_dispatch_page':dispatches,
                'dispatch_page_truncated':complete and len(plans)>8,
                'next_dispatch_cursor':json.loads(plans[7][0])['record_id'] if complete and len(plans)>8 else None,
                'exhaustive_fanout':False,'qualification_eligible':False,
                'blockers':['EXPECTED_CHILD_DENOMINATOR_UNPROVEN','CONTINUOUS_COLLECTION_UNPROVEN']}
            db.commit()
            _atomic_write_unlocked(directory/'current.json',result)
            return result
    finally:
        if held_lease is None: lease.release()


def main(argv=None):
    """Explicit local analyzer diagnostic; reconciler alone owns the mirror lease."""
    import argparse
    parser=argparse.ArgumentParser(description='Observed scan-child reconciliation only; not exhaustive coverage or qualification.')
    for name in ('repo-root','data-root','source-revision','config-signature'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--max-bytes',type=int,default=1048576)
    parser.add_argument('--reference-after',default='')
    parser.add_argument('--dispatch-after',default='')
    args=vars(parser.parse_args(argv))
    try:
        report=reconcile_scans(**args)
        print(json.dumps({'status':'OBSERVED_ONLY' if report['index_caught_up'] else 'INDEX_CATCHUP',
            'source_binding':report['binding'],'index_caught_up':report['index_caught_up'],
            'observed_joined_opportunity_rows':report['observed_joined_opportunity_rows'],
            'verified_reference_page':report['sample_original_references'],
            'next_reference_cursor':report['next_reference_cursor'],
            'more_references':report['reference_sample_truncated'],
            'observed_dispatch_page':report['observed_dispatch_page'],
            'next_dispatch_cursor':report['next_dispatch_cursor'],
            'more_dispatches':report['dispatch_page_truncated'],
            'exhaustive_collection_status':'UNKNOWN','qualification_eligible':False,
            'blockers':report['blockers']},allow_nan=False))
        return 0
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as error:
        print(json.dumps({'status':'UNKNOWN','error':diagnostic_code(error),
            'exhaustive_collection_status':'UNKNOWN','qualification_eligible':False}))
        return 1


if __name__=='__main__': raise SystemExit(main())
