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
