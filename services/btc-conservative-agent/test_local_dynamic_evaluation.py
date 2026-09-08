import hashlib
import json
import pytest
from test_local_dynamic_fit import options
from test_dynamic_cohort_adapter import row
from test_local_holdout_producer import causal_rows
import test_evidence_collected_receipts as evidence
from research.local_dynamic_fit import fit_local_dynamic_input
from research.local_dynamic_seal import seal_historical_fit
from research.local_dynamic_evaluation import evaluate_local_frozen_holdout
from research.local_dynamic_input import write_local_dynamic_input
from research.local_dynamic_mapping import build_local_dynamic_mapping,_hash
from research.dynamic_cohort_adapter import adapt_dynamic_cohorts
from lifecycle_bundles import LifecycleKey,materialize_bundle
from lifecycle_completion_receipts import build_evidence_collected_receipt


def prepared(tmp_path,monkeypatch):
    args=options(tmp_path)
    fit=fit_local_dynamic_input(**args)
    seal=seal_historical_fit(**args,holdout_start_ts=2_000_100.,clock=lambda:2_000_000.)
    generation=seal['binding']['generation']; protocol=seal['binding']['protocol']
    key=LifecycleKey(generation['epoch_id'],'future-episode','a'*64,'CONTINUOUS')
    provenance={k:generation[k] for k in ('source_revision','deployed_revision','tile_config_signature')}
    provenance['config_signature']='source-config'
    old=evidence._terminal_rows
    monkeypatch.setattr(evidence,'KEY',key); monkeypatch.setattr(evidence,'PROVENANCE',provenance)
    def future_rows():
        rows=old()
        for r in rows: r['observed_ts']+=2_000_000
        rows[0]['chase_schedule']['terminal_ts']+=2_000_000
        rows[2]['coverage']['complete_through_ts']+=2_000_000
        return rows
    monkeypatch.setattr(evidence,'_terminal_rows',future_rows)
    monkeypatch.setattr(evidence,'NOW',2_020_000.)
    completion=evidence._completion()
    collection=build_evidence_collected_receipt(completion,identity=key.as_dict(),event_id='trade-1',
        provenance=provenance,collected_at=evidence.NOW)['receipt']
    rows=future_rows()+[evidence._row('lifecycle','complete',bundle_completion=completion),
        evidence._row('lifecycle','collection',evidence_collection_receipt=collection)]
    causal_rows(args['data_root'],rows,key,provenance)
    ref=rows[0]['shared_context_binding']['references'][0]; source=ref['source_row']
    source['signal_ts']+=2_000_000
    source['feature_snapshot_at_signal']['captured_at_ts']+=2_000_000
    source['feature_snapshot_at_signal']['regime']['observed_ts']+=2_000_000
    raw=(json.dumps(source)+'\n').encode()
    (args['data_root']/'v3/ledgers/opportunity.jsonl').write_bytes(raw)
    ref.update(row_length=len(raw),row_sha256=hashlib.sha256(raw).hexdigest())
    materialize_bundle(args['data_root'],key,rows,now=evidence.NOW)
    source=row(generation=generation,episode_id=key.episode_id,outcome_state='NO_FILL',net_pnl_usd=0,
        source_lifecycle_identity=key.as_dict(),config_signature='source-config',signal_ts=2_001_000.,
        required_end_ts=2_010_000.,pre_entry_features={'regime':{'value':'BULL','observed_ts':2_000_999.}})
    adapted=adapt_dynamic_cohorts([source],expected_generation=generation,feature_names=['regime'],protocol=protocol)
    mapping=build_local_dynamic_mapping(adapted,group_id=adapted['groups'][0]['group_id'],expected_generation=generation,protocol=protocol)
    writeargs={k:v for k,v in args.items() if k!='input_sha256'}
    receipt=write_local_dynamic_input(**writeargs,rows=mapping['training_episodes'],mapping_payload=mapping)
    return {**args,'input_sha256':receipt['input_sha256'],'seal_request_id':_hash(seal['binding'])},seal


def test_actual_prospective_producer_frozen_consumer_and_crash_retry(tmp_path,monkeypatch):
    args,seal=prepared(tmp_path,monkeypatch)
    from research import local_dynamic_fit as fit
    monkeypatch.setattr(fit,'train_frozen_dynamic_policy',lambda *a,**k:pytest.fail('retrained'))
    from research import local_dynamic_evaluation as module
    writer=module._write_once
    def crash(path,value):
        if value.get('schema')=='local_frozen_holdout_comparison_v1': raise OSError('injected after consumption')
        return writer(path,value)
    monkeypatch.setattr(module,'_write_once',crash)
    with pytest.raises(OSError,match='injected'):
        evaluate_local_frozen_holdout(**args,clock=lambda:2_020_001.)
    monkeypatch.setattr(module,'_write_once',writer)
    result=evaluate_local_frozen_holdout(**args,clock=lambda:2_020_100.)
    assert result['comparison']['episodes_scored']==1
    assert result['comparison']['sealed_holdout_evaluation_verified']
    assert result['comparison']['signed_static_baselines']
    assert not result['qualification_allowed']
    assert not result['comparison']['qualification_eligible']
    assert result['independent_source_episode_n']==1
    assert result['consumption_receipt']['evaluation_started_at']==2_020_001.
    assert evaluate_local_frozen_holdout(**args,clock=lambda:2_020_200.)==result


@pytest.mark.parametrize('fault',['early','nonfinite','model'])
def test_invalid_before_consumption(tmp_path,monkeypatch,fault):
    args,seal=prepared(tmp_path,monkeypatch)
    if fault=='model':
        path=args['repo_root']/'local-derived/dynamic-fits'/seal['binding']['fit_id']/'frozen-policy.json'
        path.chmod(0o666); path.write_text('{}')
    with pytest.raises(ValueError):
        evaluate_local_frozen_holdout(**args,clock=lambda:float('nan') if fault=='nonfinite' else 2_005_000.)
    assert not list((args['repo_root']/'local-derived/dynamic-seals/sealed_holdout/evaluations').glob('*.json'))


def test_missing_candidate_never_consumes_and_retry_rejects_changed_cohort(tmp_path,monkeypatch):
    args,seal=prepared(tmp_path,monkeypatch)
    from research import local_dynamic_evaluation as module
    producer=module.produce_local_holdout
    def missing(**opts):
        value=producer(**opts); value['rows'][0]['policy_outcomes']={}
        return value
    monkeypatch.setattr(module,'produce_local_holdout',missing)
    with pytest.raises(ValueError,match='COVERAGE_INCOMPLETE'):
        evaluate_local_frozen_holdout(**args,clock=lambda:2_020_001.)
    assert not list((args['repo_root']/'local-derived/dynamic-seals/sealed_holdout/evaluations').glob('*.json'))
    monkeypatch.setattr(module,'produce_local_holdout',producer)
    evaluate_local_frozen_holdout(**args,clock=lambda:2_020_001.)
    def changed(**opts):
        value=producer(**opts); value['artifact_sha256']='0'*64
        return value
    monkeypatch.setattr(module,'produce_local_holdout',changed)
    with pytest.raises(ValueError,match='RETRY_COHORT_CHANGED'):
        evaluate_local_frozen_holdout(**args,clock=lambda:2_020_100.)
