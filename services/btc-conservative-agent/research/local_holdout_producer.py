"""Mirror-owned exact-lane holdout evidence; never seals or evaluates."""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
from research.local_dynamic_input import _check,_source,_safe_path,_encoded
from research.mirror_generation_lease import MirrorGenerationLease
from research.holdout_collection_provenance import verify_collection_provenance
from research.holdout_causal_provenance import require_matching_causal_projection
from research_v3_sealed_holdout import _write_once
from research.holdout_counterfactual_provenance import verify_counterfactual_provenance
from research.holdout_candidate_identity import candidate_identity_matches

_COUNTERFACTUAL_REASONS=frozenset({
    'COUNTERFACTUAL_PROSPECTIVE_REPLAY_PROOF_MISSING','COUNTERFACTUAL_ARTIFACT_HASH',
    'COUNTERFACTUAL_EXPECTED_IDENTITY_MISMATCH','COUNTERFACTUAL_CAUSAL_FEATURES_UNPROVEN',
    'COUNTERFACTUAL_SOURCE_NOT_IN_PINNED_DATASET','COUNTERFACTUAL_SOURCE_INVENTORY_UNVERIFIED',
    'COUNTERFACTUAL_OPPORTUNITY_OUTSIDE_VERIFIED_PREFIX','COUNTERFACTUAL_PINNED_SOURCE_HASH',
    'COUNTERFACTUAL_SOURCE_VERIFICATION_BUDGET','COUNTERFACTUAL_SOURCE_MISMATCH',
    'COUNTERFACTUAL_MODEL_NOT_AVAILABLE','COUNTERFACTUAL_SEAL_GENERATION_MISMATCH',
    'COUNTERFACTUAL_ENTRY_SEMANTIC_MISMATCH','COUNTERFACTUAL_SEMANTIC_REPLAY_MISMATCH',
    'COUNTERFACTUAL_COST_CONTRACT','COUNTERFACTUAL_SEALED_COST_MODEL_UNPROVEN',
    'COUNTERFACTUAL_REVERIFICATION_MISMATCH','COUNTERFACTUAL_OPPORTUNITY_HASH',
    'COUNTERFACTUAL_SEGMENT_HASH','COUNTERFACTUAL_OUTSIDE_WINDOW',
    'HOLDOUT_CAUSAL_PROJECTION_IDENTITY_MISMATCH','HOLDOUT_CAUSAL_PROJECTION_FEATURE_MISMATCH'})


def _counterfactual_reason(error):
    code=str(error)
    return code if isinstance(error,ValueError) and code in _COUNTERFACTUAL_REASONS else 'COUNTERFACTUAL_PROVENANCE_UNVERIFIED'


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
        excluded=Counter(); rows=[]; verified_count=0; revalidations=[]
        for original in mapping['training_episodes']:
            proofs={}; outcomes={}
            for candidate in mapping['candidates']:
                if candidate['policy_id'] not in original['policy_outcomes']:
                    excluded['MAPPED_CANDIDATE_OUTCOME_UNKNOWN']+=1
            for policy,outcome in original['policy_outcomes'].items():
                if outcome.get('counterfactual_identity') or outcome.get('replay_proof_sha256'):
                    identity=outcome.get('counterfactual_identity') or {}
                    candidates=[c for c in mapping['candidates'] if c['policy_id']==policy]
                    expected={'epoch_id':generation['epoch_id'],'source_episode_id':original.get('source_episode_id'),
                        'opportunity_id':original.get('opportunity_id'),'policy_id':policy,
                        'policy_signature':candidates[0]['policy_signature'] if len(candidates)==1 else None,
                        'direction':original.get('direction'),'seal_request_id':identity.get('seal_request_id')}
                    if outcome.get('source_lifecycle_identity') or identity!=expected:
                        excluded['COUNTERFACTUAL_IDENTITY_MISMATCH']+=1; continue
                    cache_key=hashlib.sha256(_encoded(['counterfactual-producer-v1',expected,original,outcome])).hexdigest()
                    cache_path=_safe_path(cache/(cache_key[:32]+'.json'))
                    cached=None
                    if cache_path.exists():
                        cached=_read_manifest(cache_path)
                        if (cached.get('schema')!='trusted_local_counterfactual_producer_receipt_v1'
                                or cached.get('job')!=job or cached.get('key')!=cache_key
                                or cached.get('sha256')!=hashlib.sha256(_encoded({k:v for k,v in cached.items() if k!='sha256'})).hexdigest()):
                            raise ValueError('HOLDOUT_COUNTERFACTUAL_PRODUCER_RECEIPT_INVALID')
                        cached_count+=1
                    else:
                        if verified_count>=32:
                            deferred+=1; continue
                        verified_count+=1
                    try:
                        proof=verify_counterfactual_provenance(repo_root=options['repo_root'],data_root=root,
                            source_revision=options['source_revision'],artifact_sha256=outcome.get('replay_proof_sha256'),
                            expected_identity=expected,now=options.get('now'),held_lease=lease)
                        require_matching_causal_projection(original,proof['causal_provenance'])
                    except (ValueError,OSError) as error:
                        excluded[_counterfactual_reason(error)]+=1; continue
                    completion=proof['completion']; entry=completion['entry']; terminal=completion['terminal']
                    dimensions=mapping['selected_group']
                    if (not candidate_identity_matches(original,outcome,proof,policy=policy,
                            signature=expected['policy_signature'],seal_request_id=expected['seal_request_id'])
                            or entry.get('final_classification')!=outcome.get('outcome_state')
                            or terminal.get('net_pnl_usd')!=outcome.get('net_pnl_usd')
                            or terminal.get('status')!='COMPLETE'
                            or original.get('required_end_ts')!=terminal.get('required_horizon_end_ts')
                            or dimensions.get('cost_model_id')!=terminal.get('cost_model_id')
                            or dimensions.get('simulation_model')!=terminal.get('simulation_model')
                            or (dimensions.get('sizing') or {}).get('contract_sha256')!=terminal.get('declared_contract_sha256')):
                        excluded['COUNTERFACTUAL_OUTCOME_COMPLETION_MISMATCH']+=1; continue
                    # Fixed producer-owned state is trusted local append-only
                    # state, not arbitrary caller-supplied evidence. Its hash is
                    # corruption detection, not protection against an owner
                    # rewriting the trusted journal and artifacts together.
                    revalidations.append({'key':cache_key,'replay_proof_sha256':proof['replay_proof_sha256'],
                        'currently_verified_at':proof['qualification_eligible_at']})
                    if cached is not None:
                        prior=cached.get('proof') or {}
                        timekeys={'evidence_collected_at','qualification_eligible_at'}
                        if ({k:v for k,v in prior.items() if k not in timekeys}!=
                                {k:v for k,v in proof.items() if k not in timekeys}
                                or any(type(prior.get(k)) not in (int,float) or not math.isfinite(prior[k])
                                    or prior[k]<terminal['required_horizon_end_ts']
                                    or prior[k]>proof[k] for k in timekeys)):
                            raise ValueError('HOLDOUT_COUNTERFACTUAL_PRODUCER_RECEIPT_MISMATCH')
                        proof=prior
                    else:
                        stored={'schema':'trusted_local_counterfactual_producer_receipt_v1',
                            'job':job,'key':cache_key,'proof':proof}
                        stored['sha256']=hashlib.sha256(_encoded(stored)).hexdigest()
                        pending_cache.append((cache_path,stored))
                    proofs[policy]=proof; outcomes[policy]=outcome
                    continue
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
        if revalidations:
            validation={'schema':'counterfactual_producer_current_revalidation_v1','job':job,
                'source_generation':source,'verified_proofs':revalidations,
                'trust_boundary':'PRODUCER_OWNED_LOCAL_APPEND_ONLY_STATE_NOT_OWNER_TAMPER_PROOF'}
            validation_sha=hashlib.sha256(_encoded(validation)).hexdigest()
            _write_once(cache/('revalidation-'+validation_sha+'.json'),validation)
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
