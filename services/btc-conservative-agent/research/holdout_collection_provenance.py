"""Original qualification-bundle collection timestamps, never lane inheritance."""
import hashlib
import json
import math
import os
from pathlib import Path
from lifecycle_bundles import LifecycleKey, verify_bundle, classify_evidence_collection
from research.local_dynamic_input import _safe_path
from research.lifecycle_evidence_join import verify_manifest_collection_receipt


def verify_collection_provenance(bundle_path, *, epoch_id, source_episode_id,
                                 policy_signature, research_lane, expected_provenance):
    bundle=_safe_path(bundle_path)
    key=LifecycleKey(epoch_id,source_episode_id,policy_signature,research_lane)
    # Bound the existing verifier before it reads any bundle member. No links,
    # arbitrary side trees, or single unbounded events file are admitted.
    count=total=directories=0
    for directory,dirs,files in os.walk(bundle,followlinks=False):
        directories+=1
        if directories>128: raise ValueError('HOLDOUT_BUNDLE_READ_BUDGET')
        for name in dirs+files:
            path=Path(directory)/name
            if path.is_symlink() or path.resolve()!=path.absolute():
                raise ValueError('HOLDOUT_BUNDLE_LINK_REJECTED')
        for name in files:
            count+=1; total+=(Path(directory)/name).stat().st_size
            if count>256 or total>32*1024*1024:
                raise ValueError('HOLDOUT_BUNDLE_READ_BUDGET')
    before=(bundle/'manifest.json').read_bytes()
    verified=verify_bundle(bundle)
    manifest=verified.get('manifest') or {}
    if (verified.get('passed') is not True or manifest.get('maturity')!='QUALIFICATION_READY'
            or manifest.get('identity')!=key.as_dict()):
        raise ValueError('HOLDOUT_QUALIFICATION_BUNDLE_INVALID_OR_IDENTITY_MISMATCH')
    if manifest.get('provenance')!=expected_provenance:
        raise ValueError('HOLDOUT_BUNDLE_PROVENANCE_MISMATCH')
    raw=(bundle/'events.jsonl').read_bytes()
    rows=[json.loads(line) for line in raw.splitlines()]
    for row in rows:
        receipt=row.get('evidence_collection_receipt')
        if isinstance(receipt,dict):
            for field in ('evidence_collected_at','qualification_eligible_at'):
                value=receipt.get(field)
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                    raise ValueError('HOLDOUT_COLLECTION_TIMESTAMP_INVALID')
    collection=classify_evidence_collection(rows,key)
    joined=verify_manifest_collection_receipt(manifest,rows)
    if collection.get('ready') is not True or joined.get('valid') is not True:
        raise ValueError('HOLDOUT_COLLECTION_RECEIPT_INVALID')
    receipt=collection['receipt']
    if receipt!=((manifest.get('evidence_collection') or {}).get('receipt')):
        raise ValueError('HOLDOUT_COLLECTION_MANIFEST_MISMATCH')
    if (bundle/'manifest.json').read_bytes()!=before or not verify_bundle(bundle).get('passed'):
        raise ValueError('HOLDOUT_BUNDLE_CHANGED_DURING_READ')
    return {'schema':'qualification_bundle_collection_provenance_v1','identity':key.as_dict(),
        'provenance':dict(expected_provenance),'evidence_collected_at':receipt['evidence_collected_at'],
        'qualification_eligible_at':receipt['qualification_eligible_at'],
        'manifest_sha256':manifest['manifest_sha256'],'manifest_bytes_sha256':hashlib.sha256(before).hexdigest(),
        'events_sha256':hashlib.sha256(raw).hexdigest(),
        'evidence_collected_receipt_sha256':receipt['evidence_collected_receipt_sha256'],
        'completion_receipt_sha256':receipt['completion_receipt_sha256'],
        'counterfactual_timestamp_inheritance_allowed':False,'qualification_allowed':False}
