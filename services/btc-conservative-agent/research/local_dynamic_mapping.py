"""Pure historical adapter-group mapping; no fitting, sealing, or Fly writes."""
import hashlib
import json
import math

from research.dynamic_cohort_adapter import GENERATION_FIELDS
from research.local_dynamic_input import MAX_BYTES, MAX_ROWS


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def validate_stored_mapping(mapping, stored):
    """Validate full mapping content against the independently verified store."""
    if (not isinstance(mapping, dict) or mapping.get('schema') != 'dynamic_policy_analysis_input_v1'
            or mapping.get('mapping_sha256') != _hash({k:v for k,v in mapping.items() if k != 'mapping_sha256'})):
        raise ValueError('LOCAL_DYNAMIC_MAPPING_CHECKSUM')
    source = stored['source_generation']
    expected = mapping.get('expected_generation') or {}
    for key, value in {'epoch_id':source['epoch'], 'source_revision':source['revision'],
                       'deployed_revision':source['deployed_revision'],
                       'manifest_entry_hash':source['manifest_entry_hash'],
                       'analyzer_revision':stored['analyzer_revision']}.items():
        if expected.get(key) != value:
            raise ValueError('LOCAL_DYNAMIC_MAPPING_SOURCE_MISMATCH')
    if (mapping.get('training_episodes') != stored['rows']
            or mapping.get('generation_revision') != stored['analyzer_revision']
            or mapping.get('dataset_epoch') != source['epoch']
            or mapping.get('source_revision') != source['revision']
            or _hash(mapping.get('protocol')) != stored['config_signature']):
        raise ValueError('LOCAL_DYNAMIC_MAPPING_CONTENT_MISMATCH')
    if (mapping.get('qualification_allowed') is not False
            or mapping.get('sealed_evaluation_allowed') is not False
            or mapping.get('sealed_holdout_episodes') != []
            or mapping.get('sealed_holdout_evaluation') is not None):
        raise ValueError('LOCAL_DYNAMIC_MAPPING_NOT_HISTORICAL')


def build_local_dynamic_mapping(adapted, *, group_id, expected_generation, protocol):
    """Select exactly one verified adapter group, retaining its complete contract.

    The caller must bind this value to the canonical mirror via local input
    storage; hash validation here does not establish source authenticity.
    """
    if not isinstance(adapted, dict) or adapted.get('schema') != 'same_publication_dynamic_cohorts_v1':
        raise ValueError('DYNAMIC_MAPPING_ADAPTER_INVALID')
    if len(_json(adapted).encode()) > MAX_BYTES:
        raise ValueError('DYNAMIC_MAPPING_BYTE_BUDGET')
    if 'publication_sha256' in adapted or 'adapter_payload' in adapted:
        from research_v3_contract import canonical_json
        import hashlib
        if (adapted.get('adapter_envelope_schema')!='dynamic_adapter_publication_v1'
                or adapted.get('publication_sha256')!=hashlib.sha256(canonical_json(
                    {k:v for k,v in adapted.items() if k!='publication_sha256'}).encode()).hexdigest()
                or not isinstance(adapted.get('adapter_payload'),dict)):
            raise ValueError('DYNAMIC_MAPPING_PUBLICATION_CHECKSUM')
        outer=adapted
        adapted=outer['adapter_payload']
        if any(outer.get(key)!=value for key,value in adapted.items() if key!='blockers'):
            raise ValueError('DYNAMIC_MAPPING_PUBLICATION_CORE_CONFLICT')
    if (not isinstance(expected_generation, dict)
            or any(not isinstance(expected_generation.get(k), str) or not expected_generation[k].strip()
                   for k in GENERATION_FIELDS)
            or adapted.get('expected_generation') != expected_generation):
        raise ValueError('DYNAMIC_MAPPING_GENERATION_MISMATCH')
    if adapted.get('adapter_sha256') != _hash({k:v for k,v in adapted.items() if k != 'adapter_sha256'}):
        raise ValueError('DYNAMIC_MAPPING_ADAPTER_CHECKSUM')
    keys = {'outer_folds', 'inner_folds', 'purge_sec', 'embargo_sec', 'minimum_bucket_support'}
    if not isinstance(protocol, dict) or set(protocol) != keys:
        raise ValueError('DYNAMIC_MAPPING_PROTOCOL_INCOMPLETE')
    for key in ('outer_folds', 'inner_folds', 'minimum_bucket_support'):
        value = protocol[key]
        if type(value) is not int or not (1 if key == 'minimum_bucket_support' else 2) <= value <= 100:
            raise ValueError('DYNAMIC_MAPPING_PROTOCOL_INVALID')
    for key in ('purge_sec', 'embargo_sec'):
        value = protocol[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('DYNAMIC_MAPPING_PROTOCOL_INVALID')
    if adapted.get('protocol_sha256') != _hash(protocol):
        raise ValueError('DYNAMIC_MAPPING_PROTOCOL_HASH_MISMATCH')
    groups = adapted.get('groups')
    if not isinstance(groups, list):
        raise ValueError('DYNAMIC_MAPPING_GROUP_INVALID')
    matches = [g for g in groups if isinstance(g, dict) and g.get('group_id') == group_id]
    if len(matches) != 1:
        raise ValueError('DYNAMIC_MAPPING_GROUP_AMBIGUOUS_OR_MISSING')
    group = matches[0]
    if group.get('cohort_sha256') != _hash({k:v for k,v in group.items() if k != 'cohort_sha256'}):
        raise ValueError('DYNAMIC_MAPPING_GROUP_CHECKSUM')
    dimensions = {k:v for k,v in group.items() if k not in {'group_id','episodes','candidates','counts','cohort_sha256'}}
    if _hash(dimensions) != group_id:
        raise ValueError('DYNAMIC_MAPPING_GROUP_DIMENSIONS_MISMATCH')
    episodes = group.get('episodes')
    if not isinstance(episodes, list) or len(episodes) > MAX_ROWS:
        raise ValueError('DYNAMIC_MAPPING_ROW_BUDGET')
    training, seen = [], set()
    for episode in episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get('episode_id'), str) or not episode['episode_id']:
            raise ValueError('DYNAMIC_MAPPING_EPISODE_INVALID')
        if episode['episode_id'] in seen:
            raise ValueError('DYNAMIC_MAPPING_EPISODE_DUPLICATE')
        seen.add(episode['episode_id'])
        row = dict(episode)
        for target, origin in (('dataset_epoch','epoch_id'), ('source_revision','source_revision'),
                               ('deployed_revision','deployed_revision'), ('tile_config_signature','tile_config_signature')):
            if target in row and row[target] != expected_generation[origin]:
                raise ValueError('DYNAMIC_MAPPING_EPISODE_IDENTITY_MISMATCH')
            row[target] = expected_generation[origin]
        training.append(row)
    # Current adapter deliberately does not assert evidence collection timestamps.
    # Historical comparisons remain useful, but no adapter output is a seal.
    missing = sum('evidence_collected_at' not in row for row in training)
    body = {'schema':'dynamic_policy_analysis_input_v1',
        'generation_revision':expected_generation['analyzer_revision'],
        'dataset_epoch':expected_generation['epoch_id'], 'source_revision':expected_generation['source_revision'],
        'training_episodes':training, 'sealed_holdout_episodes':[], 'sealed_holdout_evaluation':None,
        'candidates':group['candidates'], 'feature_names':adapted['feature_names'], **protocol,
        'protocol':dict(protocol), 'protocol_run_id':_hash({'group':group_id,'protocol':protocol,
                                                          'cohort':group['cohort_sha256']}),
        'adapter_sha256':adapted['adapter_sha256'], 'selected_group':group,
        'input_universe_schema':adapted.get('input_universe_schema'),
        'input_universe':adapted.get('input_universe'),
        'expected_generation':dict(expected_generation), 'historical_diagnostics_allowed':True,
        'sealed_evaluation_allowed':False, 'missing_collection_timestamp_episodes':missing,
        'blockers':['PROSPECTIVE_SEAL_REQUIRED'] + (['EVIDENCE_COLLECTION_TIMESTAMP_MISSING'] if missing else []),
        'qualification_allowed':False}
    if len(_json(body).encode()) > MAX_BYTES:
        raise ValueError('DYNAMIC_MAPPING_BYTE_BUDGET')
    return json.loads(_json({**body, 'mapping_sha256':_hash(body)}))
