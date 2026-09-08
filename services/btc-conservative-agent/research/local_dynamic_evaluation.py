"""Explicit frozen comparison on verified prospective evidence; never fits."""
import math
import re
import time
from pathlib import Path
from research.local_dynamic_fit_loader import _read
from research.local_dynamic_mapping import _hash
from research.local_dynamic_input import _safe_path, _directory
from research.local_holdout_producer import produce_local_holdout
from research.mirror_generation_lease import MirrorGenerationLease
from research_dynamic_entry_policy import verify_frozen_dynamic_policy, evaluate_frozen_dynamic_policy, _opportunity_value
from research_v3_sealed_holdout import load_seal, consume_seal, _write_once


def _positive(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
        raise ValueError('EVALUATION_TIMESTAMP_INVALID')
    return value


def evaluate_local_frozen_holdout(*, seal_request_id, clock=time.time, **options):
    if not re.fullmatch('[0-9a-f]{64}',str(seal_request_id)):
        raise ValueError('EVALUATION_SEAL_ID_INVALID')
    base=_safe_path(Path(options['repo_root'])/'local-derived')
    sealroot=_safe_path(base/'dynamic-seals')
    wrapper=_read(sealroot/(seal_request_id+'.json'))
    binding=wrapper.get('binding') or {}; seal=wrapper.get('seal') or {}
    if (wrapper.get('receipt_sha256')!=_hash({k:v for k,v in wrapper.items() if k!='receipt_sha256'})
            or _hash(binding)!=seal_request_id or seal.get('cohort_signature')!=_hash(binding)
            or seal.get('policy_candidates')!=binding.get('sealed_policy_candidates')
            or load_seal(sealroot,seal.get('seal_id'))!=seal):
        raise ValueError('EVALUATION_SEAL_BINDING_INVALID')
    fitid=binding.get('fit_id')
    if not re.fullmatch('[0-9a-f]{64}',str(fitid)): raise ValueError('EVALUATION_FIT_ID_INVALID')
    directory=_safe_path(base/'dynamic-fits'/fitid)
    result=_read(directory/'historical-result.json'); model=_read(directory/'frozen-policy.json')
    if (result.get('result_sha256')!=binding.get('result_sha256')
            or result.get('result_sha256')!=_hash({k:v for k,v in result.items() if k!='result_sha256'})
            or _hash(result.get('identity'))!=fitid or result.get('input_receipt')!=binding.get('input_receipt')
            or result.get('frozen_policy')!=model or _hash(model)!=binding.get('model_sha256')
            or not verify_frozen_dynamic_policy(model)):
        raise ValueError('EVALUATION_FROZEN_FIT_MISMATCH')
    lease=MirrorGenerationLease(base/'dynamic-evaluations'/seal_request_id[:16],owner='frozen-evaluation')
    lease.acquire(timeout_seconds=0)
    try:
        v2=wrapper.get('schema')=='local_dynamic_prospective_seal_v2'
        end=_positive(binding.get('holdout_end_ts')) if v2 else None
        now=_positive(clock())
        if v2:
            delay=_positive(binding.get('holdout_maturity_delay_sec'))
            if (end<=_positive(seal['holdout_start_ts']) or binding.get('holdout_mature_at')!=end+delay
                    or now<end+delay):
                raise ValueError('EVALUATION_PREDECLARED_WINDOW_NOT_MATURE')
        holdout=produce_local_holdout(**options)
        if holdout.get('status')=='IN_PROGRESS': return holdout
        if holdout['input_receipt']['input_sha256']==binding['input_receipt']['input_sha256']:
            raise ValueError('EVALUATION_TRAINING_INPUT_REUSED')
        # Read immutable historical artifact by its seal-bound hash; do not
        # require the old training mirror still to be current and never refit.
        inputs=_directory(options['repo_root'],options['data_root'])
        mappings=[]
        for digest in (binding['input_receipt']['input_sha256'],holdout['input_receipt']['input_sha256']):
            artifact=_read(inputs/(digest+'.json'))
            if _hash(artifact)!=digest: raise ValueError('EVALUATION_INPUT_CHECKSUM_MISMATCH')
            mappings.append(artifact['mapping_payload'])
        before,after=mappings
        dimensions=lambda m:{k:v for k,v in m['selected_group'].items()
            if k not in {'group_id','episodes','candidates','counts','cohort_sha256'}}
        if (before['mapping_sha256']!=result['identity']['mapping_sha256']
                or dimensions(before)!=dimensions(after) or before['candidates']!=after['candidates']
                or before['feature_names']!=after['feature_names'] or before['protocol']!=after['protocol']):
            raise ValueError('EVALUATION_COHORT_CONTRACT_CHANGED')
        boundary=_positive(seal['holdout_start_ts'])
        if now<=boundary or boundary<=_positive(model['training_cutoff_required_end_ts']):
            raise ValueError('EVALUATION_BOUNDARY_INVALID')
        source=holdout['source_generation']
        for field,sourcefield in [('dataset_epoch','epoch'),('source_revision','revision'),
                                 ('deployed_revision','deployed_revision')]:
            if source.get(sourcefield)!=seal[field]: raise ValueError('EVALUATION_SOURCE_IDENTITY_MISMATCH')
        episodes=[]; historical=0; postend=0
        candidates={row['policy_id']:row['policy_signature'] for row in model['candidates']}
        for original in holdout['rows']:
            if any(original.get(field)!=seal[field] for field in
                   ('dataset_epoch','source_revision','deployed_revision','tile_config_signature')):
                raise ValueError('EVALUATION_ROW_SOURCE_IDENTITY_MISMATCH')
            signal=_positive(original.get('signal_ts'))
            if signal<boundary: historical+=1; continue
            if end is not None and signal>=end: postend+=1; continue
            if (_positive(original.get('required_end_ts'))>now
                    or _positive(original.get('evidence_collected_at'))>now):
                raise ValueError('EVALUATION_HOLDOUT_NOT_MATURE')
            outcomes=original['policy_outcomes']; proofs=original['collection_provenance_by_policy']
            for policy,signature in candidates.items():
                if (policy not in outcomes or policy not in proofs
                        or outcomes[policy].get('source_lifecycle_identity',{}).get('policy_signature')!=signature
                        or not proofs[policy].get('causal_provenance')
                        or _opportunity_value(original,policy) is None):
                    raise ValueError('EVALUATION_FROZEN_CANDIDATE_COVERAGE_INCOMPLETE')
            episodes.append({**original,**{field:seal[field] for field in
                ('dataset_epoch','source_revision','deployed_revision','tile_config_signature','cohort_signature')}})
        if not episodes: raise ValueError('EVALUATION_NO_PROSPECTIVE_EVIDENCE')
        window_coverage=None
        if v2:
            if after.get('input_universe_schema')!='dynamic_input_universe_v1' or not isinstance(after.get('input_universe'),list):
                raise ValueError('EVALUATION_INPUT_UNIVERSE_UNAVAILABLE')
            expected=set(); past=future=0
            for anchor in after['input_universe']:
                signal=_positive(anchor.get('signal_ts'))
                if signal<boundary: past+=1; continue
                if signal>=end: future+=1; continue
                if anchor.get('group_id') is None:
                    raise ValueError('EVALUATION_IN_WINDOW_UNCLASSIFIED_INPUT')
                if anchor['group_id']!=after['selected_group']['group_id']: continue
                if not all(anchor.get(k) for k in ('episode_id','opportunity_id','policy_id')):
                    raise ValueError('EVALUATION_IN_WINDOW_INPUT_IDENTITY_MISSING')
                expected.add((anchor['episode_id'],anchor['opportunity_id'],anchor['policy_id']))
            supplied={(r['source_episode_id'],r['opportunity_id'],p) for r in episodes for p in candidates}
            if not expected or expected!=supplied:
                raise ValueError('EVALUATION_PROSPECTIVE_UNIVERSE_INCOMPLETE')
            window_coverage={'status':'COMPLETE_DECLARED_INPUT_UNIVERSE',
                'expected_candidate_episodes':len(expected),'supported_candidate_episodes':len(supplied),
                'historical_input_rows':past,'post_end_input_rows':future,
                'source_collection_exhaustiveness_verified':False}
        directory=_safe_path(base/'dynamic-evaluations'/seal_request_id[:16])
        identity={'seal_request_id':seal_request_id,'holdout_sha256':holdout['artifact_sha256'],
                  'model_sha256':binding['model_sha256'],'episodes_sha256':_hash(episodes)}
        intent_path=directory/'intent.json'
        if intent_path.exists():
            intent=_read(intent_path)
            if (intent.get('identity')!=identity or intent.get('sha256')!=
                    _hash({k:v for k,v in intent.items() if k!='sha256'})):
                raise ValueError('EVALUATION_RETRY_COHORT_CHANGED')
            now=_positive(intent['evaluation_started_at'])
        else:
            intent={'identity':identity,'evaluation_started_at':now}
            intent['sha256']=_hash(intent); _write_once(intent_path,intent)
        consumed=consume_seal(sealroot,seal_id=seal['seal_id'],
            policy_candidates=binding['sealed_policy_candidates'],holdout_episodes=episodes,evaluation_started_at=now)
        if consumed.get('passed') is not True: raise ValueError('EVALUATION_SEAL_CONSUMPTION_REJECTED')
        comparison=evaluate_frozen_dynamic_policy(model,episodes,evaluation_mode='SEALED_HOLDOUT',
            sealed_holdout_evaluation=consumed)
        comparison['qualification_eligible']=False
        remaining=['FULL_READINESS_GATES_NOT_EVALUATED','FIFTEEN_DAY_FORWARD_PAPER_TRIAL_NOT_PROVEN',
                   'SOURCE_COLLECTION_EXHAUSTIVENESS_NOT_VERIFIED']
        if not v2: remaining.append('HOLDOUT_END_NOT_PREDECLARED_IN_SEAL')
        comparison['qualification_blockers']=list(comparison['qualification_blockers'])+remaining
        output={'schema':'local_frozen_holdout_comparison_v1','identity':identity,'consumption_receipt':consumed,
            'comparison':comparison,'excluded_historical_rows':historical,
            'excluded_post_end_rows':postend,
            'declared_input_window_coverage':window_coverage,
            'producer_excluded_counts':holdout['excluded_counts'],
            'scope':'CONDITIONAL_SUPPORTED_COHORT_NOT_PORTFOLIO_RETURNS',
            'independent_source_episode_n':len({r['source_episode_id'] for r in episodes}),
            'scored_scenarios_may_be_correlated':True,
            'qualification_allowed':False,'live_policy_change_allowed':False,
            'qualification_blockers':remaining}
        output['sha256']=_hash(output); _write_once(directory/'result.json',output)
        return output
    finally: lease.release()


def main(argv=None):
    import argparse
    import json
    parser=argparse.ArgumentParser(description='Explicit frozen prospective comparison; no fitting or trading.')
    for name in ('repo-root','data-root','input-sha256','source-revision','analyzer-revision',
                 'transformation-signature','config-signature','seal-request-id'):
        parser.add_argument('--'+name,required=True)
    try:
        output=evaluate_local_frozen_holdout(**vars(parser.parse_args(argv)))
        print(json.dumps({'status':output.get('status','EVALUATED_CONDITIONAL_ONLY'),
                          'sha256':output.get('sha256'),'qualification_allowed':False}))
        return 0
    except (OSError,ValueError,RuntimeError):
        print(json.dumps({'status':'UNKNOWN','error':'FROZEN_EVALUATION_FAILED'}))
        return 1


if __name__=='__main__': raise SystemExit(main())
