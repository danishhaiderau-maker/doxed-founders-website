"""Explicit prospective freeze only; never called by dashboard/report reads."""
import argparse
import json
import math
import time
from pathlib import Path
from research.local_dynamic_fit_loader import load_verified_historical_fit
from research.local_dynamic_mapping import _hash
from research.local_dynamic_input import _safe_path
from research.mirror_generation_lease import MirrorGenerationLease
from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
from research_v3_sealed_holdout import create_seal, load_seal, _write_once


def seal_historical_fit(*, holdout_start_ts, clock=time.time, **load_options):
    boundary=float(holdout_start_ts)
    if not math.isfinite(boundary): raise ValueError('INVALID_HOLDOUT_BOUNDARY')
    verified=load_verified_historical_fit(**load_options)
    result,model=verified['result'],verified['frozen_policy']
    if (model is None or model.get('training_evidence_complete') is not True
            or (result.get('nested_protocol') or {}).get('passed') is not True):
        raise ValueError('COMPLETE_HISTORICAL_MODEL_REQUIRED')
    mapping,receipt=load_verified_local_dynamic_mapping(**load_options)
    if receipt!=result['input_receipt']: raise ValueError('SEAL_SOURCE_CHANGED')
    generation=mapping['expected_generation']
    candidates = list(model['candidates']) + [{'policy_id':model['policy_id'],
                                             'policy_signature':model['content_sha256']}]
    candidates = [dict(policy_id=key[0],policy_signature=key[1]) for key in sorted({
        (row['policy_id'],row['policy_signature']) for row in candidates})]
    binding={'fit_id':verified['fit_id'],'result_sha256':result['result_sha256'],
             'model_sha256':_hash(model),'input_receipt':receipt,
             'protocol':result['identity']['protocol'],'generation':generation,
             'holdout_start_ts':boundary,'sealed_policy_candidates':candidates}
    request_id=_hash(binding)
    root=_safe_path(Path(load_options['repo_root'])/'local-derived'/'dynamic-seals')
    lease=MirrorGenerationLease(root,owner='explicit-prospective-seal')
    lease.acquire(timeout_seconds=0)
    try:
        target=_safe_path(root/(request_id+'.json'))
        if target.exists():
            with target.open('rb') as stream: raw=stream.read(1024*1024+1)
            if len(raw)>1024*1024: raise ValueError('SEAL_RECEIPT_LIMIT')
            existing=json.loads(raw)
            if (existing.get('binding')!=binding or existing.get('receipt_sha256')!=
                    _hash({k:v for k,v in existing.items() if k!='receipt_sha256'})):
                raise ValueError('SEAL_BINDING_INVALID')
            if load_seal(root,existing['seal']['seal_id'])!=existing['seal']:
                raise ValueError('SEAL_RECEIPT_MISMATCH')
            return existing
        available=float(clock())
        if not math.isfinite(available) or boundary<=available:
            raise ValueError('HOLDOUT_BOUNDARY_NOT_FUTURE')
        if boundary<=float(model['training_cutoff_required_end_ts']):
            raise ValueError('HOLDOUT_OVERLAPS_TRAINING')
        intent_path=_safe_path(root/(request_id+'.intent.json'))
        if intent_path.exists():
            with intent_path.open('rb') as stream: raw=stream.read(1024*1024+1)
            if len(raw)>1024*1024: raise ValueError('SEAL_RECEIPT_LIMIT')
            intent=json.loads(raw)
            if (intent.get('binding')!=binding or intent.get('intent_sha256')!=
                    _hash({k:v for k,v in intent.items() if k!='intent_sha256'})):
                raise ValueError('SEAL_INTENT_INVALID')
            available=float(intent['verified_fit_available_at'])
            if not math.isfinite(available) or available>=boundary:
                raise ValueError('SEAL_INTENT_TIME_INVALID')
        else:
            intent={'binding':binding,'verified_fit_available_at':available}
            intent['intent_sha256']=_hash(intent)
            _write_once(intent_path,intent)
        seal=create_seal(root,dataset_epoch=generation['epoch_id'],
            source_revision=generation['source_revision'],deployed_revision=generation['deployed_revision'],
            tile_config_signature=generation['tile_config_signature'],
            cohort_signature=_hash(binding),training_snapshot_hash=result['result_sha256'],
            training_completed_at=available,sealed_at=available,holdout_start_ts=boundary,
            policy_candidates=candidates)
        output={'schema':'local_dynamic_prospective_seal_v1','binding':binding,'seal':seal,
            'verified_fit_available_at':available,
            'training_time_basis':'CONSERVATIVE_VERIFIED_AVAILABILITY_NOT_ORIGINAL_TRAINING_TIME',
            'qualification_allowed':False,'live_policy_change_allowed':False}
        output['receipt_sha256']=_hash(output)
        _write_once(target,output)
        return output
    finally: lease.release()


def main(argv=None):
    parser=argparse.ArgumentParser(description='Explicit future research holdout seal; no fitting or trading.')
    for name in ('repo-root','data-root','input-sha256','source-revision','analyzer-revision',
                 'transformation-signature','config-signature','holdout-start-ts'):
        parser.add_argument('--'+name,required=True)
    try:
        result=seal_historical_fit(**vars(parser.parse_args(argv)))
        print(json.dumps({'status':'SEALED_NOT_EVALUATED','seal_id':result['seal']['seal_id'],
                          'qualification_allowed':False}))
        return 0
    except (OSError,ValueError,RuntimeError):
        print(json.dumps({'status':'UNKNOWN','error':'PROSPECTIVE_SEAL_FAILED'}))
        return 1


if __name__=='__main__': raise SystemExit(main())
