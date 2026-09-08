"""Bounded original opportunity verification; no retrospective capture claims."""
import hashlib
import json
from research.local_dynamic_input import _safe_path
from research.research_v3_report import normalize_pre_entry_feature_receipt


def verify_opportunity_reference(rows, *, data_root, identity, provenance):
    refs=[]
    for event in rows:
        binding=event.get('shared_context_binding') or {}
        if binding.get('status')!='BOUND': continue
        refs.extend(ref for ref in binding.get('references',[]) if ref.get('ledger')=='opportunity')
    if len(refs)!=1:
        raise ValueError('HOLDOUT_CAUSAL_OPPORTUNITY_MISSING_OR_AMBIGUOUS')
    ref=refs[0]; source=ref.get('source_row') or {}
    offset,length=ref.get('byte_offset'),ref.get('row_length')
    if (type(offset) is not int or offset<0 or type(length) is not int
            or length<=0 or length>1024*1024):
        raise ValueError('HOLDOUT_CAUSAL_REFERENCE_BUDGET')
    path=_safe_path(data_root/'v3/ledgers/opportunity.jsonl')
    try:
        with path.open('rb') as stream:
            stream.seek(offset); raw=stream.read(length)
    except FileNotFoundError:
        raise ValueError('HOLDOUT_CAUSAL_SOURCE_MISSING') from None
    if (len(raw)!=length or hashlib.sha256(raw).hexdigest()!=ref.get('row_sha256')
            or json.loads(raw)!=source):
        raise ValueError('HOLDOUT_CAUSAL_REFERENCE_HASH_MISMATCH')
    epochs={source[k] for k in ('epoch_id','collection_epoch_id') if source.get(k)}
    if (epochs!={identity['collection_epoch_id']} or source.get('episode_id')!=identity['episode_id']
            or any(source.get(k)!=v for k,v in provenance.items())
            or not source.get('record_id') or not source.get('shared_ai_call_id')):
        raise ValueError('HOLDOUT_CAUSAL_REFERENCE_IDENTITY_MISMATCH')
    anchors=[r for r in rows if r.get('resolution_scope')=='LANE_ENTRY']
    if not anchors or any(r.get('opportunity_id')!=source['record_id']
                          or r.get('shared_ai_call_id')!=source['shared_ai_call_id']
                          or r.get('episode_id')!=identity['episode_id']
                          or r.get('policy_signature')!=identity['policy_signature']
                          or r.get('research_lane')!=identity['research_lane']
                          or any(r.get(k)!=v for k,v in provenance.items()) for r in anchors):
        raise ValueError('HOLDOUT_CAUSAL_LANE_ANCHOR_MISMATCH')
    features=source.get('feature_snapshot_at_signal') or {}
    if not isinstance(features,dict):
        raise ValueError('HOLDOUT_CAUSAL_FEATURE_AVAILABILITY_UNPROVEN')
    # Normalize the actual embedded snapshot. This is NOT a separate
    # pre_entry_features ledger receipt and is never labelled as such.
    normalized,blockers=normalize_pre_entry_feature_receipt({
        'availability_boundary':'PRE_DECISION_ONLY',
        'capture_schema':features.get('capture_schema'),
        'captured_at_ts':features.get('captured_at_ts'),'features':features,
    },signal_ts=source.get('signal_ts'))
    if not normalized:
        raise ValueError('HOLDOUT_CAUSAL_FEATURE_AVAILABILITY_UNPROVEN')
    return {'schema':'verified_bundle_opportunity_snapshot_v1',
        'opportunity_id':source['record_id'],'source_episode_id':source['episode_id'],
        'signal_ts':source['signal_ts'],'pre_entry_features':normalized,
        'feature_blockers':blockers,'row_sha256':ref['row_sha256'],
        'byte_offset':offset,'row_length':length,
        'separate_pre_entry_ledger_verified':False}


def require_matching_causal_projection(original, proof):
    if (original.get('source_episode_id')!=proof.get('source_episode_id')
            or original.get('opportunity_id')!=proof.get('opportunity_id')
            or original.get('signal_ts')!=proof.get('signal_ts')):
        raise ValueError('HOLDOUT_CAUSAL_PROJECTION_IDENTITY_MISMATCH')
    features=original.get('pre_entry_features') or {}
    if not features or any(value!=(proof.get('pre_entry_features') or {}).get(name)
                           for name,value in features.items()):
        raise ValueError('HOLDOUT_CAUSAL_PROJECTION_FEATURE_MISMATCH')
