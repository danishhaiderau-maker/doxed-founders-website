from pathlib import Path
import pytest
from test_evidence_collected_receipts import _completion,_terminal_rows,_row,KEY,PROVENANCE,NOW
from lifecycle_completion_receipts import build_evidence_collected_receipt
from lifecycle_bundles import materialize_bundle
from research.holdout_collection_provenance import verify_collection_provenance


def bundle(tmp_path):
    completion=_completion()
    collected=build_evidence_collected_receipt(completion,identity=KEY.as_dict(),event_id='trade-1',
        provenance=PROVENANCE,collected_at=NOW)['receipt']
    rows=_terminal_rows()+[_row('lifecycle','complete',bundle_completion=completion),
                           _row('lifecycle','collected',evidence_collection_receipt=collected)]
    result=materialize_bundle(tmp_path,KEY,rows,now=NOW)
    assert result['written']
    return next((tmp_path/'v3/lifecycle_bundles').glob('*/*/manifest.json')).parent


def kwargs():
    return dict(epoch_id=KEY.collection_epoch_id,source_episode_id=KEY.episode_id,
                policy_signature=KEY.policy_signature,research_lane=KEY.research_lane,
                expected_provenance=PROVENANCE)


def test_real_bundle_retains_original_collection_hashes(tmp_path):
    path=bundle(tmp_path)
    proof=verify_collection_provenance(path,**kwargs())
    assert proof['evidence_collected_at']==NOW
    assert proof['identity']==KEY.as_dict()
    assert len(proof['evidence_collected_receipt_sha256'])==64
    assert len(proof['completion_receipt_sha256'])==64
    assert not proof['counterfactual_timestamp_inheritance_allowed']


@pytest.mark.parametrize('field,value',[('source_episode_id','transformed-id'),('research_lane','OTHER'),
                                     ('policy_signature','other-policy'),('epoch_id','other-epoch')])
def test_exact_identity_no_cross_lane_inheritance(tmp_path,field,value):
    path=bundle(tmp_path); args=kwargs(); args[field]=value
    with pytest.raises(ValueError,match='IDENTITY_MISMATCH'):
        verify_collection_provenance(path,**args)


def test_tamper_and_wrong_provenance_rejected(tmp_path):
    path=bundle(tmp_path); args=kwargs(); args['expected_provenance']={**PROVENANCE,'source_revision':'wrong'}
    with pytest.raises(ValueError,match='PROVENANCE_MISMATCH'):
        verify_collection_provenance(path,**args)
    events=path/'events.jsonl'; events.chmod(0o666); events.write_bytes(events.read_bytes()+b'{}\n')
    with pytest.raises(ValueError): verify_collection_provenance(path,**kwargs())
