"""Pinned canonical inventory membership for replay source byte ranges."""
import hashlib
import json
from pathlib import Path
from research.local_dynamic_input import _safe_path
from research.entry_baseline_replay import _context_source_pins
from research.policy_evidence_schema import generation_identity


def verify_membership(root, source, opportunity_ref, segment_refs,held_lease=None):
    root=_safe_path(root)
    if held_lease is not None:
        from research.mirror_generation_lease import MirrorGenerationLease
        if (not isinstance(held_lease,MirrorGenerationLease) or not held_lease.held
                or held_lease.path!=root.resolve()/'.fly-mirror-generation.lease'):
            raise ValueError('COUNTERFACTUAL_HELD_LEASE_INVALID')
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
    offset,length=opportunity_ref.get('byte_offset'),opportunity_ref.get('row_length')
    if type(offset) is not int or offset<0 or type(length) is not int or not 0<length<=1048576:
        raise ValueError('COUNTERFACTUAL_REFERENCE_INVALID')
    # Only retain the verified opportunity prefix for this live caller lease.
    # Each requested byte range is still compared with the current file. This
    # avoids N full-prefix hashes without trusting mutable stat metadata.
    cache=getattr(held_lease,'_counterfactual_verified_prefix',None) if held_lease is not None else None
    for name in dict.fromkeys(names):
        record=records.get(name) or {}; size=record.get('size')
        if name not in pins or type(size) is not int or size<0:
            raise ValueError('COUNTERFACTUAL_SOURCE_NOT_IN_PINNED_DATASET')
        if name==names[0] and offset+length>size:
            raise ValueError('COUNTERFACTUAL_OPPORTUNITY_OUTSIDE_VERIFIED_PREFIX')
        key=(str(root),source.get('manifest_entry_hash'),pins[name],size)
        if name==names[0] and cache is not None and cache[0]==key:
            with _safe_path(root/name).open('rb') as stream:
                stream.seek(offset); current=stream.read(length)
            if current!=cache[1][offset:offset+length]:
                raise ValueError('COUNTERFACTUAL_PINNED_SOURCE_HASH')
            continue
        if size>budget: raise ValueError('COUNTERFACTUAL_SOURCE_VERIFICATION_BUDGET')
        budget-=size; digest=hashlib.sha256(); remaining=size; prefix=[]
        with _safe_path(root/name).open('rb') as stream:
            while remaining:
                chunk=stream.read(min(1048576,remaining))
                if not chunk: raise ValueError('COUNTERFACTUAL_SOURCE_PREFIX_SHORT')
                digest.update(chunk); remaining-=len(chunk)
                if name==names[0] and held_lease is not None: prefix.append(chunk)
        if digest.hexdigest()!=pins[name]: raise ValueError('COUNTERFACTUAL_PINNED_SOURCE_HASH')
        if name==names[0] and held_lease is not None:
            held_lease._counterfactual_verified_prefix=(key,b''.join(prefix))
        if name!=names[0] and Path(name).stem!=pins[name]:
            raise ValueError('COUNTERFACTUAL_PINNED_SEGMENT_HASH')
    after=[]
    for file,limit in ((path,1048576),(root/'.fly-sync-state.json',32*1024*1024)):
        with file.open('rb') as stream: after.append(stream.read(limit+1))
    if tuple(after)!=fence:
        raise ValueError('COUNTERFACTUAL_SOURCE_INVENTORY_CHANGED')
