"""Mirror-owned exact-lane holdout evidence; never seals or evaluates."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
from research.local_dynamic_input import _check,_source,_safe_path,_encoded
from research.mirror_generation_lease import MirrorGenerationLease
from research.holdout_collection_provenance import verify_collection_provenance
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
            for policy,outcome in original['policy_outcomes'].items():
                identity=outcome.get('source_lifecycle_identity') or {}
                if (not identity or identity.get('episode_id')!=original.get('source_episode_id')
                        or identity.get('collection_epoch_id')!=generation['epoch_id']):
                    excluded['EXACT_SOURCE_LIFECYCLE_IDENTITY_MISSING']+=1; continue
                key=tuple(identity.get(k) for k in ('collection_epoch_id','episode_id','policy_signature','research_lane'))
                matches=by_identity.get(key,[])
                if len(matches)!=1:
                    excluded['QUALIFICATION_BUNDLE_MISSING_OR_AMBIGUOUS']+=1; continue
                verified_count+=1
                if verified_count>32: raise ValueError('HOLDOUT_BUNDLE_VERIFY_BUDGET')
                provenance={k:generation[k] for k in ('source_revision','deployed_revision','tile_config_signature')}
                provenance['config_signature']=outcome.get('source_config_signature')
                # Config is an exact source identity, never the fitting protocol.
                manifest=_read_manifest(matches[0]/'manifest.json')
                if provenance['config_signature'] != (manifest.get('provenance') or {}).get('config_signature'):
                    excluded['SOURCE_CONFIG_BINDING_MISSING_OR_MISMATCH']+=1; continue
                proof=verify_collection_provenance(matches[0],epoch_id=key[0],source_episode_id=key[1],
                    policy_signature=key[2],research_lane=key[3],expected_provenance=provenance)
                completion=proof['completion']; state=completion.get('entry_outcome')
                pnl=(completion.get('economics') or {}).get('net_pnl_usd') if state in {'FULL_FILL','PARTIAL_FILL'} else 0 if state=='NO_FILL' else None
                if state!=outcome.get('outcome_state') or pnl is None or pnl!=outcome.get('net_pnl_usd'):
                    excluded['OUTCOME_COMPLETION_MISMATCH']+=1; continue
                proofs[policy]=proof; outcomes[policy]=outcome
            if not outcomes: continue
            rows.append({**original,'policy_outcomes':outcomes,'collection_provenance_by_policy':proofs,
                'evidence_collected_at':max(p['evidence_collected_at'] for p in proofs.values())})
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
