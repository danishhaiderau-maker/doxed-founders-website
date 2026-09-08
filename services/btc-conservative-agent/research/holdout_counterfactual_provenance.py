"""Revalidate own prospective counterfactual proof, never impersonate paper evidence."""
import hashlib
import re
import time
from pathlib import Path
from research.local_counterfactual_completion import load_completion, write_completion, _positive
from research.local_dynamic_input import _safe_path
from research.local_dynamic_mapping import _hash
from research.research_v3_report import normalize_pre_entry_feature_receipt


def verify_counterfactual_provenance(*, repo_root, data_root, source_revision,
                                    artifact_sha256, expected_identity, clock=time.time, now=None):
    artifact=load_completion(repo_root,artifact_sha256)
    if (artifact.get('schema')!='local_counterfactual_completion_v2'
            or artifact.get('scope')!='SEALED_POLICY_REPLAY_PROOF_NOT_QUALIFIED'
            or not artifact.get('seal_request_id') or not isinstance(artifact.get('replay_inputs'),dict)):
        raise ValueError('COUNTERFACTUAL_PROSPECTIVE_REPLAY_PROOF_MISSING')
    replay=dict(artifact['replay_inputs']); payloads=[]; remaining=2097152
    for ref in artifact['source_segment_references']:
        digest=ref.get('sha256')
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('COUNTERFACTUAL_SEGMENT_ID')
        path=_safe_path(Path(data_root)/'v3/market_segments'/digest[:2]/(digest+'.json'))
        with path.open('rb') as stream: raw=stream.read(remaining+1)
        remaining-=len(raw)
        if remaining<0 or hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('COUNTERFACTUAL_SEGMENT_HASH')
        payloads.append(raw)
    replay['source_segment_payloads']=payloads
    verified=_positive(clock()); original=_positive(artifact.get('verified_at'))
    if verified<original: raise ValueError('COUNTERFACTUAL_VERIFICATION_TIME_ORDER')
    result=write_completion(repo_root=repo_root,data_root=data_root,source_revision=source_revision,
        opportunity_ref=artifact['opportunity_reference'],entry=artifact['entry'],terminal=artifact['terminal'],
        path_rows=artifact['path_rows'],cost_contract=artifact['cost_contract'],policy_id=artifact['policy_id'],
        source_segments=artifact['source_segment_references'],seal_request_id=artifact['seal_request_id'],
        baseline_reference=artifact['baseline_reference'],replay_inputs=replay,exit_candidate=artifact['exit_candidate'],
        entry_source_segments=artifact['entry_source_segments'],clock=lambda:original,now=now,_verify_only=True)
    if _hash(result['body'])!=artifact_sha256:
        raise ValueError('COUNTERFACTUAL_REVERIFICATION_MISMATCH')
    source=result['opportunity']
    identity={'epoch_id':source.get('epoch_id'),'source_episode_id':source.get('episode_id'),
        'opportunity_id':source.get('record_id'),'policy_id':artifact['policy_id'],
        'policy_signature':artifact['policy_signature'],'direction':artifact['direction'],
        'seal_request_id':artifact['seal_request_id']}
    if identity!=expected_identity or any(not v for v in identity.values()):
        raise ValueError('COUNTERFACTUAL_EXPECTED_IDENTITY_MISMATCH')
    features=source.get('feature_snapshot_at_signal') or {}
    if not isinstance(features,dict): raise ValueError('COUNTERFACTUAL_CAUSAL_FEATURES_UNPROVEN')
    normalized,blockers=normalize_pre_entry_feature_receipt({'availability_boundary':'PRE_DECISION_ONLY',
        'capture_schema':features.get('capture_schema'),'captured_at_ts':features.get('captured_at_ts'),
        'features':features},signal_ts=source.get('signal_ts'))
    if not normalized: raise ValueError('COUNTERFACTUAL_CAUSAL_FEATURES_UNPROVEN')
    causal={'schema':'verified_counterfactual_opportunity_snapshot_v1',
        'source_episode_id':identity['source_episode_id'],'opportunity_id':identity['opportunity_id'],
        'signal_ts':source['signal_ts'],'pre_entry_features':normalized,'feature_blockers':blockers,
        **{k:artifact['opportunity_reference'][k] for k in ('row_sha256','byte_offset','row_length')},
        'separate_pre_entry_ledger_verified':False}
    return {'schema':'verified_counterfactual_collection_provenance_v1',
        'counterfactual_identity':identity,'replay_proof_sha256':artifact_sha256,
        'source':artifact['source'],'causal_provenance':causal,
        'entry_semantic_replay_verified':True,'terminal_semantic_replay_verified':True,
        'evidence_collected_at':original,'qualification_eligible_at':verified,
        'counterfactual_timestamp_inheritance_allowed':False,'qualification_allowed':False}
