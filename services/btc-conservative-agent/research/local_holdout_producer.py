"""Mirror-owned exact-lane holdout evidence; never seals or evaluates."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
from research.local_dynamic_input import _check,_source,_safe_path,_encoded
from research.mirror_generation_lease import MirrorGenerationLease
from research.holdout_collection_provenance import verify_collection_provenance
from research.holdout_causal_provenance import require_matching_causal_projection
from research_v3_sealed_holdout import _write_once


def _read_manifest(path):
    with _safe_path(path).open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('HOLDOUT_MANIFEST_READ_BUDGET')
    return json.loads(raw)


def produce_local_holdout(**options):
    mapping,input_receipt=load_verified_local_dynamic_mapping(**options)
    root=_safe_path(options['data_root'])
    lease=MirrorGenerationLease(root,owner='local-holdout-producer')
    lease.acquire(timeout_seconds=0)
    try:
        token=_check(options['repo_root'],root,options['source_revision'],now=options.get('now'))
        generation=mapping['expected_generation']
        source=_source(token)
        if source!=input_receipt['source_generation']:
            raise ValueError('HOLDOUT_INPUT_SOURCE_CHANGED')
        job=hashlib.sha256(_encoded({'contract':'causal-opportunity-v1','input':input_receipt,'source':source,
            'episodes':mapping['training_episodes']})).hexdigest()
        cache=_safe_path(Path(options['repo_root'])/'local-derived/holdout-progress'/job[:16])
        if cache.is_relative_to(root) or root.is_relative_to(cache): raise ValueError('HOLDOUT_RAW_OVERLAP')
        pending_cache=[]; deferred=0; cached_count=0
        paths=[]
        for path in (root/'v3/lifecycle_bundles').glob('*/*/manifest.json'):
            if len(paths)>=1000: raise ValueError('HOLDOUT_MANIFEST_SCAN_BUDGET')
            paths.append(_safe_path(path))
        by_identity={}
        for path in paths:
            manifest=_read_manifest(path)
            identity=manifest.get('identity') or {}
            key=tuple(identity.get(k) for k in ('collection_epoch_id','episode_id','policy_signature','research_lane'))
            by_identity.setdefault(key,[]).append(path.parent)
        excluded=Counter(); rows=[]; verified_count=0
        for original in mapping['training_episodes']:
            proofs={}; outcomes={}
            for candidate in mapping['candidates']:
                if candidate['policy_id'] not in original['policy_outcomes']:
                    excluded['MAPPED_CANDIDATE_OUTCOME_UNKNOWN']+=1
            for policy,outcome in original['policy_outcomes'].items():
                identity=outcome.get('source_lifecycle_identity') or {}
                if (not identity or identity.get('episode_id')!=original.get('source_episode_id')
                        or identity.get('collection_epoch_id')!=generation['epoch_id']):
                    excluded['EXACT_SOURCE_LIFECYCLE_IDENTITY_MISSING']+=1; continue
                key=tuple(identity.get(k) for k in ('collection_epoch_id','episode_id','policy_signature','research_lane'))
                matches=by_identity.get(key,[])
                if len(matches)!=1:
                    excluded['QUALIFICATION_BUNDLE_MISSING_OR_AMBIGUOUS']+=1; continue
                provenance={k:generation[k] for k in ('source_revision','deployed_revision','tile_config_signature')}
                provenance['config_signature']=outcome.get('source_config_signature')
                # Config is an exact source identity, never the fitting protocol.
                manifest=_read_manifest(matches[0]/'manifest.json')
                if provenance['config_signature'] != (manifest.get('provenance') or {}).get('config_signature'):
                    excluded['SOURCE_CONFIG_BINDING_MISSING_OR_MISMATCH']+=1; continue
                cache_key=hashlib.sha256(_encoded([key,policy,outcome,manifest.get('manifest_sha256')])).hexdigest()
                cache_path=_safe_path(cache/(cache_key[:32]+'.json'))
                if cache_path.exists():
                    with cache_path.open('rb') as stream: cache_raw=stream.read(1024*1024+1)
                    if len(cache_raw)>1024*1024: raise ValueError('HOLDOUT_CACHE_BUDGET')
                    stored=json.loads(cache_raw)
                    if (stored.get('job')!=job or stored.get('key')!=cache_key or stored.get('sha256')!=
                            hashlib.sha256(_encoded({k:v for k,v in stored.items() if k!='sha256'})).hexdigest()):
                        raise ValueError('HOLDOUT_CACHE_INVALID')
                    proof=stored['proof']; cached_count+=1
                else:
                    if verified_count>=32:
                        deferred+=1; continue
                    verified_count+=1
                    try:
                        proof=verify_collection_provenance(matches[0],epoch_id=key[0],source_episode_id=key[1],
                            policy_signature=key[2],research_lane=key[3],expected_provenance=provenance,
                            data_root=root)
                    except ValueError as error:
                        if not str(error).startswith('HOLDOUT_CAUSAL_'): raise
                        proof={'causal_error':str(error)}
                    stored={'job':job,'key':cache_key,'proof':proof}
                    stored['sha256']=hashlib.sha256(_encoded(stored)).hexdigest()
                    pending_cache.append((cache_path,stored))
                if proof.get('causal_error'):
                    excluded[proof['causal_error']]+=1; continue
                try: require_matching_causal_projection(original,proof.get('causal_provenance') or {})
                except ValueError as error:
                    excluded[str(error)]+=1; continue
                completion=proof['completion']; state=completion.get('entry_outcome')
                pnl=(completion.get('economics') or {}).get('net_pnl_usd') if state in {'FULL_FILL','PARTIAL_FILL'} else 0 if state=='NO_FILL' else None
                if state!=outcome.get('outcome_state') or pnl is None or pnl!=outcome.get('net_pnl_usd'):
                    excluded['OUTCOME_COMPLETION_MISMATCH']+=1; continue
                proofs[policy]=proof; outcomes[policy]=outcome
            if not outcomes: continue
            rows.append({**original,'policy_outcomes':outcomes,'collection_provenance_by_policy':proofs,
                'evidence_collected_at':max(p['evidence_collected_at'] for p in proofs.values())})
        _check(options['repo_root'],root,options['source_revision'],previous=token,held_lease=lease,now=options.get('now'))
        for path,stored in pending_cache: _write_once(path,stored)
        if deferred:
            return {'status':'IN_PROGRESS','job_id':job,'new_proofs':verified_count,
                'cached_proofs':cached_count,'pending_proofs':deferred,'qualification_allowed':False,
                'artifact_sha256':None}
        body={'schema':'local_verified_holdout_input_v1','source_generation':source,
              'input_receipt':input_receipt,'rows':rows,'excluded_counts':dict(excluded),
              'qualification_allowed':False,'sealed':False}
        raw=_encoded(body)
        if len(raw)>2*1024*1024: raise ValueError('HOLDOUT_ARTIFACT_BUDGET')
        digest=hashlib.sha256(raw).hexdigest()
        _check(options['repo_root'],root,options['source_revision'],previous=token,held_lease=lease,now=options.get('now'))
        directory=_safe_path(Path(options['repo_root'])/'local-derived/holdout-inputs')
        if directory.is_relative_to(root) or root.is_relative_to(directory): raise ValueError('HOLDOUT_RAW_OVERLAP')
        _write_once(directory/(digest+'.json'),body)
        return {'artifact_sha256':digest,**body}
    finally: lease.release()
