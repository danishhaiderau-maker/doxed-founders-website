"""Explicit bounded historical fit; never called by report refresh or sealing."""
import hashlib
import json
from pathlib import Path

from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
from research.local_dynamic_input import _safe_path
from research.local_dynamic_mapping import _hash
from research.mirror_generation_lease import MirrorGenerationLease
from research_dynamic_entry_policy import (nested_purged_walk_forward_dynamic,
    train_frozen_dynamic_policy, verify_frozen_dynamic_policy, write_immutable_policy_receipt)
from research_v3_sealed_holdout import _write_once

MAX_WORK = 1_000_000
MAX_RESULT_BYTES = 8 * 1024 * 1024


def _code_hash():
    base = Path(__file__).resolve().parent.parent
    files = [Path(__file__), base/'research_dynamic_entry_policy.py', base/'research_v3_validation.py']
    return _hash({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def fit_local_dynamic_input(**load_options):
    """Compute once for exact verified input/protocol/code; repeat only reads."""
    mapping, receipt = load_verified_local_dynamic_mapping(**load_options)
    identity = {'input_sha256':receipt['input_sha256'], 'mapping_sha256':mapping['mapping_sha256'],
                'protocol':mapping['protocol'], 'code_sha256':_code_hash()}
    fit_id = _hash(identity)
    directory = _safe_path(Path(load_options['repo_root'])/'local-derived'/'dynamic-fits'/fit_id)
    mirror = _safe_path(load_options['data_root'])
    if directory.is_relative_to(mirror) or mirror.is_relative_to(directory):
        raise ValueError('LOCAL_FIT_RAW_OVERLAP')
    lease = MirrorGenerationLease(directory, owner='explicit-dynamic-fit')
    lease.acquire(timeout_seconds=0)
    try:
        target = _safe_path(directory/'historical-result.json')
        if target.exists():
            with target.open('rb') as stream:
                raw = stream.read(MAX_RESULT_BYTES+1)
            if len(raw) > MAX_RESULT_BYTES:
                raise ValueError('LOCAL_FIT_RESULT_BUDGET')
            result = json.loads(raw)
            if (result.get('identity') != identity or result.get('result_sha256') != _hash(
                    {k:v for k,v in result.items() if k != 'result_sha256'})):
                raise ValueError('LOCAL_FIT_RESULT_CHECKSUM')
            if result.get('frozen_policy') is not None and not verify_frozen_dynamic_policy(result['frozen_policy']):
                raise ValueError('LOCAL_FIT_MODEL_CHECKSUM')
            return result
        rows, candidates, protocol = mapping['training_episodes'], mapping['candidates'], mapping['protocol']
        work = len(rows)*len(candidates)*protocol['outer_folds']*protocol['inner_folds']
        if work > MAX_WORK:
            raise ValueError('LOCAL_FIT_WORK_BUDGET')
        result = {'schema':'local_dynamic_historical_fit_v1', 'identity':identity,
            'input_receipt':receipt, 'status':'UNKNOWN', 'qualification_allowed':False,
            'sealed_holdout_evaluated':False, 'live_policy_change_allowed':False,
            'estimated_work_units':work, 'nested_protocol':None, 'frozen_policy':None,
            'blockers':['PROSPECTIVE_SEAL_REQUIRED']}
        try:
            result['nested_protocol'] = nested_purged_walk_forward_dynamic(rows,
                candidates=candidates, feature_names=mapping['feature_names'], **protocol,
                protocol_run_id=mapping['protocol_run_id'])
            result['frozen_policy'] = train_frozen_dynamic_policy(rows, candidates=candidates,
                feature_names=mapping['feature_names'],
                **{k:v for k,v in protocol.items() if k != 'outer_folds'},
                training_run_id=fit_id)
            if (result['nested_protocol']['passed'] is True
                    and result['frozen_policy']['training_evidence_complete'] is True):
                result['status'] = 'HISTORICAL_FIT_COMPLETE_NOT_QUALIFIED'
            else:
                result['blockers'].append('HISTORICAL_EVIDENCE_OR_FOLDS_INCOMPLETE')
        except (ValueError, TypeError, KeyError):
            result['blockers'].append('HISTORICAL_FIT_INPUT_REJECTED')
        # Revalidate concrete source authority before publishing any result.
        current, current_receipt = load_verified_local_dynamic_mapping(**load_options)
        if current != mapping or current_receipt != receipt:
            raise ValueError('LOCAL_FIT_SOURCE_CHANGED')
        result['result_sha256'] = _hash(result)
        if len(json.dumps(result).encode()) > MAX_RESULT_BYTES:
            raise ValueError('LOCAL_FIT_RESULT_BUDGET')
        if result['frozen_policy'] is not None:
            write_immutable_policy_receipt(directory, 'frozen-policy.json', result['frozen_policy'])
        _write_once(target, result)
        return result
    finally:
        lease.release()
