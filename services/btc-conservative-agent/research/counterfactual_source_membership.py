"""Pinned canonical inventory membership for replay source byte ranges."""
import hashlib
import json
from pathlib import Path
from research.local_dynamic_input import _safe_path
from research.entry_baseline_replay import _context_source_pins
from research.policy_evidence_schema import generation_identity


def verify_membership(root, source, opportunity_ref, segment_refs):
    root=_safe_path(root)
    path=_safe_path(root/'canonical_dataset_current.json')
    with path.open('rb') as stream: raw=stream.read(1048577)
    if len(raw)>1048576: raise ValueError('COUNTERFACTUAL_MANIFEST_BUDGET')
    manifest=json.loads(raw)
    if manifest.get('entry_hash')!=source.get('manifest_entry_hash'):
        raise ValueError('COUNTERFACTUAL_MANIFEST_IDENTITY')
    _safe_path(root/'.fly-sync-state.json')
    generation=generation_identity(manifest,analyzer_revision='membership',evaluator_version='membership')
    pins,records,errors,fence=_context_source_pins(root,generation,manifest)
    if errors: raise ValueError('COUNTERFACTUAL_SOURCE_INVENTORY_UNVERIFIED')
    names=['v3/ledgers/opportunity.jsonl']
    names.extend('v3/market_segments/'+ref['sha256'][:2]+'/'+ref['sha256']+'.json' for ref in segment_refs)
    budget=64*1024*1024
    for name in dict.fromkeys(names):
        record=records.get(name) or {}; size=record.get('size')
        if name not in pins or type(size) is not int or size<0:
            raise ValueError('COUNTERFACTUAL_SOURCE_NOT_IN_PINNED_DATASET')
        if name==names[0] and opportunity_ref['byte_offset']+opportunity_ref['row_length']>size:
            raise ValueError('COUNTERFACTUAL_OPPORTUNITY_OUTSIDE_VERIFIED_PREFIX')
        if size>budget: raise ValueError('COUNTERFACTUAL_SOURCE_VERIFICATION_BUDGET')
        budget-=size; digest=hashlib.sha256(); remaining=size
        with _safe_path(root/name).open('rb') as stream:
            while remaining:
                chunk=stream.read(min(1048576,remaining))
                if not chunk: raise ValueError('COUNTERFACTUAL_SOURCE_PREFIX_SHORT')
                digest.update(chunk); remaining-=len(chunk)
        if digest.hexdigest()!=pins[name]: raise ValueError('COUNTERFACTUAL_PINNED_SOURCE_HASH')
        if name!=names[0] and Path(name).stem!=pins[name]:
            raise ValueError('COUNTERFACTUAL_PINNED_SEGMENT_HASH')
    after=[]
    for file,limit in ((path,1048576),(root/'.fly-sync-state.json',32*1024*1024)):
        with file.open('rb') as stream: after.append(stream.read(limit+1))
    if tuple(after)!=fence:
        raise ValueError('COUNTERFACTUAL_SOURCE_INVENTORY_CHANGED')
