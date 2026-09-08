"""Read verified historical fits; never compute, seal, or invent timestamps."""
import json
from pathlib import Path
from research.local_dynamic_fit import _code_hash, MAX_RESULT_BYTES
from research.local_dynamic_input import _safe_path
from research.local_dynamic_mapping import _hash
from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
from research_dynamic_entry_policy import verify_frozen_dynamic_policy


def _read(path):
    with _safe_path(path).open('rb') as stream:
        raw=stream.read(MAX_RESULT_BYTES+1)
    if len(raw)>MAX_RESULT_BYTES:
        raise ValueError('LOCAL_FIT_READ_BUDGET')
    value=json.loads(raw)
    if not isinstance(value,dict):
        raise ValueError('LOCAL_FIT_RESULT_INVALID')
    return value


def load_verified_historical_fit(**load_options):
    mapping, receipt=load_verified_local_dynamic_mapping(**load_options)
    identity={'input_sha256':receipt['input_sha256'],'mapping_sha256':mapping['mapping_sha256'],
              'protocol':mapping['protocol'],'code_sha256':_code_hash()}
    fit_id=_hash(identity)
    directory=_safe_path(Path(load_options['repo_root'])/'local-derived'/'dynamic-fits'/fit_id)
    mirror=_safe_path(load_options['data_root'])
    if directory.is_relative_to(mirror) or mirror.is_relative_to(directory):
        raise ValueError('LOCAL_FIT_RAW_OVERLAP')
    result=_read(directory/'historical-result.json')
    if (result.get('schema')!='local_dynamic_historical_fit_v1'
            or result.get('identity')!=identity or result.get('input_receipt')!=receipt
            or result.get('result_sha256')!=_hash({k:v for k,v in result.items() if k!='result_sha256'})):
        raise ValueError('LOCAL_FIT_RESULT_CHECKSUM_OR_SOURCE_MISMATCH')
    if any(result.get(key) is not False for key in
           ('qualification_allowed','sealed_holdout_evaluated','live_policy_change_allowed')):
        raise ValueError('LOCAL_FIT_SCOPE_INVALID')
    model=result.get('frozen_policy')
    if model is not None:
        if not verify_frozen_dynamic_policy(model) or _read(directory/'frozen-policy.json')!=model:
            raise ValueError('LOCAL_FIT_MODEL_CHECKSUM_OR_MISMATCH')
    current,current_receipt=load_verified_local_dynamic_mapping(**load_options)
    if current!=mapping or current_receipt!=receipt or _code_hash()!=identity['code_sha256']:
        raise ValueError('LOCAL_FIT_SOURCE_CHANGED_DURING_READ')
    return {'schema':'verified_local_historical_fit_v1','fit_id':fit_id,
            'scope':'HISTORICAL_ONLY_NOT_SEALED_NOT_QUALIFIED',
            'result':result,'frozen_policy':model,
            'training_completed_at':None,'qualification_allowed':False,
            'live_policy_change_allowed':False}
