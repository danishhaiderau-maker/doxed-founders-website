"""Immutable derived replay proof; never a canonical lifecycle or qualification."""
import hashlib
import json
import math
import re
import time
from pathlib import Path
from research.local_dynamic_input import _check,_source,_safe_path,_encoded
from research.local_dynamic_fit_loader import _read
from research.local_dynamic_mapping import _hash
from research.mirror_generation_lease import MirrorGenerationLease
from research_v3_sealed_holdout import load_seal,_write_once


def _positive(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
        raise ValueError('COUNTERFACTUAL_NUMBER_INVALID')
    return value


def _proof(root,ref):
    offset=ref.get('byte_offset'); length=ref.get('row_length')
    if type(offset) is not int or offset<0 or type(length) is not int or not 0<length<=1048576:
        raise ValueError('COUNTERFACTUAL_REFERENCE_INVALID')
    with _safe_path(Path(root)/'v3/ledgers/opportunity.jsonl').open('rb') as stream:
        stream.seek(offset); raw=stream.read(length)
    if len(raw)!=length or hashlib.sha256(raw).hexdigest()!=ref.get('row_sha256'):
        raise ValueError('COUNTERFACTUAL_OPPORTUNITY_HASH')
    row=json.loads(raw)
    if row.get('record_id')!=ref.get('record_id'): raise ValueError('COUNTERFACTUAL_OPPORTUNITY_ID')
    return row


def write_completion(*,repo_root,data_root,source_revision,opportunity_ref,entry,terminal,
                     path_rows,cost_contract,policy_id,source_segments,seal_request_id=None,clock=time.time,now=None,
                     baseline_reference=None,replay_inputs=None,exit_candidate=None,entry_source_segments=None,
                     _verify_only=False):
    from research.declared_shadow_model import validate_contract
    from research_v3_contract import canonical_json
    base=_safe_path(Path(repo_root)/'local-derived')
    if base.is_relative_to(_safe_path(data_root)) or _safe_path(data_root).is_relative_to(base):
        raise ValueError('COUNTERFACTUAL_RAW_OVERLAP')
    if len(_encoded([entry,terminal,path_rows,cost_contract,opportunity_ref]))>2097152:
        raise ValueError('COUNTERFACTUAL_PROOF_LIMIT')
    with MirrorGenerationLease(data_root,owner='counterfactual-proof').acquire(timeout_seconds=0):
        source=_source(_check(repo_root,data_root,source_revision,now=now))
        if _verify_only:
            from research.counterfactual_source_membership import verify_membership
            verify_membership(data_root,source,opportunity_ref,
                list(source_segments or [])+list(entry_source_segments or []))
        opportunity=_proof(data_root,opportunity_ref)
        if not isinstance(source_segments,list) or not 1<=len(source_segments)<=8:
            raise ValueError('COUNTERFACTUAL_SEGMENTS_MISSING')
        recovered=[]; segment_hashes=[]; remaining=2097152
        for ref in source_segments:
            digest=ref.get('sha256')
            if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
                raise ValueError('COUNTERFACTUAL_SEGMENT_ID')
            path=_safe_path(Path(data_root)/'v3/market_segments'/digest[:2]/(digest+'.json'))
            with path.open('rb') as stream: raw=stream.read(remaining+1)
            remaining-=len(raw)
            if remaining<0 or hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError('COUNTERFACTUAL_SEGMENT_HASH')
            recovered.extend(json.loads(raw)['rows']); segment_hashes.append(digest)
        if recovered!=path_rows or sorted(set(segment_hashes))!=terminal.get('source_segment_hashes'):
            raise ValueError('COUNTERFACTUAL_PATH_SOURCE_MISMATCH')
        if opportunity.get('epoch_id')!=source['epoch'] or opportunity.get('source_revision')!=source['revision']:
            raise ValueError('COUNTERFACTUAL_SOURCE_MISMATCH')
        direction=entry.get('direction')
        if direction not in ('LONG','SHORT') or terminal.get('direction',direction)!=direction:
            raise ValueError('COUNTERFACTUAL_DIRECTION_MISMATCH')
        signal=_positive(opportunity.get('signal_ts')); verified=_positive(clock())
        horizon=_positive(terminal.get('required_horizon_end_ts'))
        _positive(entry.get('filled_qty')); _positive(entry.get('fill_price'))
        if verified<horizon or horizon<signal: raise ValueError('COUNTERFACTUAL_TIME_ORDER')
        if (entry.get('supported') is not True or terminal.get('receipt_sha256')!=_hash({k:v for k,v in terminal.items() if k!='receipt_sha256'})
                or terminal.get('entry_receipt_sha256')!=hashlib.sha256(canonical_json(entry).encode()).hexdigest()
                or terminal.get('future_path_sha256')!=hashlib.sha256(canonical_json(path_rows).encode()).hexdigest()):
            raise ValueError('COUNTERFACTUAL_REPLAY_BINDING')
        generation=terminal.get('generation') or {}
        if any(generation.get(k)!=source.get(v) for k,v in (('epoch_id','epoch'),('source_revision','revision'),('deployed_revision','deployed_revision'))):
            raise ValueError('COUNTERFACTUAL_GENERATION_MISMATCH')
        if (opportunity.get('tile_config_signature')!=generation.get('tile_config_signature')
                or opportunity.get('deployed_revision')!=generation.get('deployed_revision')):
            raise ValueError('COUNTERFACTUAL_CONFIG_MISMATCH')
        from research.conservative_shadow_terminal import evaluate_shadow_terminal
        if (not isinstance(replay_inputs,dict) or replay_inputs.get('entry_receipt')!=entry
                or replay_inputs.get('future_path_rows')!=path_rows
                or replay_inputs.get('generation')!=generation
                or canonical_json(evaluate_shadow_terminal(**replay_inputs))!=canonical_json(terminal)):
            raise ValueError('COUNTERFACTUAL_SEMANTIC_REPLAY_MISMATCH')
        validate_contract(cost_contract,generation)
        if terminal.get('status')!='COMPLETE' or terminal.get('declared_contract_sha256')!=_hash(cost_contract):
            raise ValueError('COUNTERFACTUAL_COST_CONTRACT')
        scope='HISTORICAL_REPLAY_ONLY'; seal_hash=None
        if seal_request_id is not None:
            if not re.fullmatch('[0-9a-f]{64}',str(seal_request_id)): raise ValueError('COUNTERFACTUAL_SEAL_ID')
            wrapper=_read(base/'dynamic-seals'/(seal_request_id+'.json')); binding=wrapper.get('binding') or {}
            seal=wrapper.get('seal') or {}
            if (_hash(binding)!=seal_request_id or wrapper.get('receipt_sha256')!=_hash({k:v for k,v in wrapper.items() if k!='receipt_sha256'})
                    or load_seal(base/'dynamic-seals',seal.get('seal_id'))!=seal
                    or seal.get('cohort_signature')!=seal_request_id
                    or wrapper.get('schema')!='local_dynamic_prospective_seal_v2'
                    or seal.get('sealed_at')!=wrapper.get('verified_fit_available_at')):
                raise ValueError('COUNTERFACTUAL_SEAL_BINDING')
            if any((binding.get('generation') or {}).get(k)!=generation.get(k) for k in
                   ('epoch_id','source_revision','deployed_revision','tile_config_signature')):
                raise ValueError('COUNTERFACTUAL_SEAL_GENERATION_MISMATCH')
            from research_dynamic_entry_policy import verify_frozen_dynamic_policy
            fitid=binding.get('fit_id')
            if not re.fullmatch('[0-9a-f]{64}',str(fitid)): raise ValueError('COUNTERFACTUAL_FIT_ID')
            result=_read(base/'dynamic-fits'/fitid/'historical-result.json')
            model=_read(base/'dynamic-fits'/fitid/'frozen-policy.json')
            if (_hash(model)!=binding.get('model_sha256') or not verify_frozen_dynamic_policy(model)
                    or result.get('frozen_policy')!=model or result.get('result_sha256')!=binding.get('result_sha256')
                    or _hash({k:v for k,v in result.items() if k!='result_sha256'})!=result.get('result_sha256')):
                raise ValueError('COUNTERFACTUAL_FROZEN_MODEL_INVALID')
            if not _positive(wrapper.get('verified_fit_available_at'))<signal:
                raise ValueError('COUNTERFACTUAL_MODEL_NOT_AVAILABLE')
            capture=((opportunity.get('baseline_schedule_snapshot') or {}).get('directional_schedules') or {}).get(direction) or {}
            if not isinstance(baseline_reference,dict):
                raise ValueError('COUNTERFACTUAL_BASELINE_SOURCE_PROOF_MISSING')
            baseline_id=baseline_reference.get('baseline_id')
            schedule=(capture.get('schedules') or {}).get(baseline_id) or {}
            if (baseline_reference.get('capture_signature')!=capture.get('capture_signature')
                    or not capture.get('capture_signature') or baseline_reference.get('opportunity_id')!=opportunity['record_id']
                    or baseline_reference.get('source_episode_id')!=opportunity.get('episode_id')
                    or not schedule.get('schedule')):
                raise ValueError('COUNTERFACTUAL_BASELINE_SOURCE_PROOF_MISSING')
            from research.conservative_limit_fill import _normalise_schedule
            _,schedule_hash=_normalise_schedule(schedule['schedule'])
            if entry.get('schedule_sha256')!=schedule_hash:
                raise ValueError('COUNTERFACTUAL_NORMALIZED_SCHEDULE_SOURCE_MISMATCH')
            if not isinstance(entry_source_segments,list) or not 1<=len(entry_source_segments)<=8:
                raise ValueError('COUNTERFACTUAL_ENTRY_TAPE_MISSING')
            entry_rows=[]; remaining=2097152
            for ref in entry_source_segments:
                digest=ref.get('sha256')
                if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
                    raise ValueError('COUNTERFACTUAL_ENTRY_TAPE_INVALID')
                tape=_safe_path(Path(data_root)/'v3/market_segments'/digest[:2]/(digest+'.json'))
                with tape.open('rb') as stream: raw=stream.read(remaining+1)
                remaining-=len(raw)
                if remaining<0 or hashlib.sha256(raw).hexdigest()!=digest:
                    raise ValueError('COUNTERFACTUAL_ENTRY_TAPE_HASH')
                entry_rows.extend(json.loads(raw)['rows'])
            from research.entry_baseline_replay import materialize_same_opportunity_replay
            material={**opportunity,'market_microstructure_rows':entry_rows}
            replayed=materialize_same_opportunity_replay([material],generation=generation)
            matches=[result.get('conservative_receipt') for episode in replayed['episode_receipts']
                if episode.get('direction')==direction for result in episode.get('results',[])
                if result.get('baseline_id')==baseline_id and result.get('supported') is True]
            if len(matches)!=1 or matches[0]!=entry:
                raise ValueError('COUNTERFACTUAL_ENTRY_SEMANTIC_MISMATCH')
            from research_entry_baselines import ENTRY_BASELINE_REGISTRY
            from research.conservative_shadow_report import build_composite_policy_identity
            from research_v3_contract import canonical_hash
            specs=[s for s in ENTRY_BASELINE_REGISTRY['baselines'] if s['baseline_id']==baseline_id]
            if len(specs)!=1: raise ValueError('COUNTERFACTUAL_BASELINE_UNKNOWN')
            composite_spec,composite=build_composite_policy_identity({'baseline_id':baseline_id,'baseline_spec':specs[0],'conservative_receipt':entry,
                'policy_signature':specs[0]['policy_signature']}, exit_candidate or {})
            if composite.get('composite_policy_signature')!=terminal.get('policy_signature') or canonical_json(composite_spec)!=canonical_json(replay_inputs['policy_spec']):
                raise ValueError('COUNTERFACTUAL_COMPOSITE_POLICY_MISMATCH')
            if not _positive(binding.get('holdout_start_ts'))<=signal<_positive(binding.get('holdout_end_ts')):
                raise ValueError('COUNTERFACTUAL_OUTSIDE_WINDOW')
            if {'policy_id':policy_id,'policy_signature':terminal.get('policy_signature')} not in binding.get('sealed_policy_candidates',[]):
                raise ValueError('COUNTERFACTUAL_POLICY_NOT_SEALED')
            input_hash=(binding.get('input_receipt') or {}).get('input_sha256')
            if not re.fullmatch('[0-9a-f]{64}',str(input_hash)): raise ValueError('COUNTERFACTUAL_INPUT_ID')
            artifact=_read(base/'dynamic-inputs'/(input_hash+'.json'))
            if _hash(artifact)!=input_hash: raise ValueError('COUNTERFACTUAL_INPUT_HASH')
            dimensions=(artifact.get('mapping_payload') or {}).get('selected_group') or {}
            if ((dimensions.get('sizing') or {}).get('contract_sha256')!=_hash(cost_contract)
                    or dimensions.get('simulation_model')!=terminal.get('simulation_model')
                    or dimensions.get('cost_model_id')!=terminal.get('cost_model_id')
                    or dimensions.get('direction')!=direction):
                raise ValueError('COUNTERFACTUAL_SEALED_COST_MODEL_UNPROVEN')
            scope='SEALED_POLICY_REPLAY_PROOF_NOT_QUALIFIED'; seal_hash=wrapper['receipt_sha256']
        if _source(_check(repo_root,data_root,source_revision,now=now))!=source:
            raise ValueError('COUNTERFACTUAL_SOURCE_CHANGED')
        persisted_replay={k:v for k,v in replay_inputs.items() if k!='source_segment_payloads'}
        body={'schema':'local_counterfactual_completion_v2','source':source,'scope':scope,
            'opportunity_reference':opportunity_ref,'direction':direction,'policy_id':policy_id,
            'policy_signature':terminal.get('policy_signature'),'entry':entry,'terminal':terminal,
            'path_rows':path_rows,'cost_contract':cost_contract,'seal_request_id':seal_request_id,
            'source_segment_references':source_segments,
            'baseline_reference':baseline_reference,
            'exit_candidate':exit_candidate,
            'entry_source_segments':entry_source_segments,
            'replay_inputs':persisted_replay,
            'seal_receipt_sha256':seal_hash,'verified_at':verified,'qualification_allowed':False}
        body['entry_semantic_replay_verified']=seal_request_id is not None
        body['terminal_semantic_replay_verified']=True
        body['verification_blockers']=[] if seal_request_id is not None else ['ENTRY_SOURCE_REPLAY_NOT_RECOMPUTED']
        if _verify_only:
            return {'body':body,'opportunity':opportunity}
        if len(_encoded(body))>2097152: raise ValueError('COUNTERFACTUAL_PROOF_LIMIT')
        digest=_hash(body); _write_once(base/'counterfactual-completions'/(digest+'.json'),body)
        return {'artifact_sha256':digest,'scope':scope,'qualification_allowed':False}


def load_completion(repo_root,digest):
    if not re.fullmatch('[0-9a-f]{64}',str(digest)): raise ValueError('COUNTERFACTUAL_ARTIFACT_ID')
    value=_read(_safe_path(Path(repo_root)/'local-derived/counterfactual-completions'/(digest+'.json')))
    if _hash(value)!=digest or value.get('qualification_allowed') is not False:
        raise ValueError('COUNTERFACTUAL_ARTIFACT_HASH')
    return value
