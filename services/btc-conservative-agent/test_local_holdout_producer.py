import pytest
from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
from research.local_dynamic_mapping import build_local_dynamic_mapping,_hash
from research.local_dynamic_input import write_local_dynamic_input
from research.local_holdout_producer import produce_local_holdout
from test_local_dynamic_loader import prepared
from test_dynamic_cohort_adapter import row
from test_local_dynamic_mapping import PROTOCOL
import test_evidence_collected_receipts as evidence
from lifecycle_bundles import LifecycleKey,materialize_bundle
from lifecycle_completion_receipts import build_evidence_collected_receipt


def test_discovery_projection_preserves_only_explicit_lineage():
    from research.discovery_scorecard_publication import _dynamic_projection
    identity={'collection_epoch_id':'e','episode_id':'original','policy_signature':'p','research_lane':'lane'}
    projected=_dynamic_projection({'source_lifecycle_identity':identity},{})
    assert projected['source_lifecycle_identity']==identity
    projected['source_lifecycle_identity']['episode_id']='changed'
    assert identity['episode_id']=='original'
    assert _dynamic_projection({'episode_id':'e','policy_signature':'p','research_lane':'lane'}, {})['source_lifecycle_identity'] is None


def test_real_mirror_bundle_producer_preserves_exact_lane(tmp_path,monkeypatch):
    args,mapping=prepared(tmp_path); generation=mapping['expected_generation']
    key=LifecycleKey(generation['epoch_id'],'original-episode','a'*64,'CONTINUOUS')
    provenance={k:generation[k] for k in ('source_revision','deployed_revision','tile_config_signature')}
    provenance['config_signature']='source-config'
    monkeypatch.setattr(evidence,'KEY',key); monkeypatch.setattr(evidence,'PROVENANCE',provenance)
    completion=evidence._completion()
    receipt=build_evidence_collected_receipt(completion,identity=key.as_dict(),event_id='trade-1',
        provenance=provenance,collected_at=evidence.NOW)['receipt']
    rows=evidence._terminal_rows()+[evidence._row('lifecycle','complete',bundle_completion=completion),
        evidence._row('lifecycle','collection',evidence_collection_receipt=receipt)]
    assert materialize_bundle(args['data_root'],key,rows,now=evidence.NOW)['written']
    source=row(generation=generation,episode_id=key.episode_id,outcome_state='NO_FILL',net_pnl_usd=0,
        source_lifecycle_identity=key.as_dict(),config_signature='source-config')
    adapted=adapt_dynamic_cohorts([source],expected_generation=generation,feature_names=['regime'],protocol=PROTOCOL)
    mapping=build_local_dynamic_mapping(adapted,group_id=adapted['groups'][0]['group_id'],expected_generation=generation,protocol=PROTOCOL)
    args['config_signature']=_hash(PROTOCOL)
    written=write_local_dynamic_input(**args,rows=mapping['training_episodes'],mapping_payload=mapping)
    result=produce_local_holdout(**args,input_sha256=written['input_sha256'])
    assert len(result['rows'])==1
    assert result['rows'][0]['source_episode_id']==key.episode_id
    assert result['rows'][0]['evidence_collected_at']==evidence.NOW
    assert not result['qualification_allowed'] and not result['sealed']


def test_missing_lineage_excluded_and_bad_mirror_rejected(tmp_path):
    import json
    args,mapping=prepared(tmp_path)
    written=write_local_dynamic_input(**args,rows=mapping['training_episodes'],mapping_payload=mapping)
    result=produce_local_holdout(**args,input_sha256=written['input_sha256'])
    assert not result['rows'] and result['excluded_counts']['EXACT_SOURCE_LIFECYCLE_IDENTITY_MISSING']==1
    heartbeat=args['data_root']/'.fly-data-sync-loop.heartbeat.json'
    data=json.loads(heartbeat.read_text()); data['ok']=False; heartbeat.write_text(json.dumps(data))
    from research.mirror_coherence import MirrorCoherenceError
    with pytest.raises(MirrorCoherenceError): produce_local_holdout(**args,input_sha256=written['input_sha256'])


def test_bounded_resume_no_partial_export_and_cache_tamper(tmp_path,monkeypatch):
    import json
    from copy import deepcopy
    from research import local_holdout_producer as module
    from dynamic_policy_analyzer import load_verified_local_dynamic_mapping
    args,mapping=prepared(tmp_path)
    written=write_local_dynamic_input(**args,rows=mapping['training_episodes'],mapping_payload=mapping)
    opts={**args,'input_sha256':written['input_sha256']}
    mapping,receipt=load_verified_local_dynamic_mapping(**opts)
    template=mapping['training_episodes'][0]; generation=mapping['expected_generation']
    episodes=[]
    for i in range(40):
        episode=deepcopy(template); episode['episode_id']='derived-'+str(i); episode['source_episode_id']='source-'+str(i)
        policy=next(iter(episode['policy_outcomes']))
        identity=dict(collection_epoch_id=generation['epoch_id'],episode_id=episode['source_episode_id'],policy_signature='sig',research_lane='L')
        episode['policy_outcomes'][policy]={'outcome_state':'NO_FILL','net_pnl_usd':0,'source_lifecycle_identity':identity,'source_config_signature':'cfg'}
        path=args['data_root']/f'v3/lifecycle_bundles/e/b{i}/manifest.json'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'identity':identity,'manifest_sha256':str(i),'provenance':{'config_signature':'cfg'}}))
        episodes.append(episode)
    mapping['training_episodes']=episodes
    monkeypatch.setattr(module,'load_verified_local_dynamic_mapping',lambda **k:(mapping,receipt))
    calls=[]
    def proof(path,**k):
        calls.append(k['source_episode_id'])
        return {'completion':{'entry_outcome':'NO_FILL'},'evidence_collected_at':20000}
    monkeypatch.setattr(module,'verify_collection_provenance',proof)
    first=module.produce_local_holdout(**opts)
    assert first['status']=='IN_PROGRESS' and first['pending_proofs']==8 and len(calls)==32
    assert not (args['repo_root']/'local-derived/holdout-inputs').exists()
    writer=module._write_once
    def interrupted(path,body):
        if body.get('schema')=='local_verified_holdout_input_v1': raise OSError('injected final publication failure')
        return writer(path,body)
    monkeypatch.setattr(module,'_write_once',interrupted)
    with pytest.raises(OSError): module.produce_local_holdout(**opts)
    assert len(calls)==40
    assert not (args['repo_root']/'local-derived/holdout-inputs').exists()
    monkeypatch.setattr(module,'_write_once',writer)
    second=module.produce_local_holdout(**opts)
    assert len(second['rows'])==40 and len(calls)==40
    assert len(set(calls))==40
    cache=next((args['repo_root']/'local-derived/holdout-progress').glob('*/*.json'))
    cache.chmod(0o666); cache.write_text('{}')
    with pytest.raises(ValueError,match='CACHE_INVALID'): module.produce_local_holdout(**opts)
