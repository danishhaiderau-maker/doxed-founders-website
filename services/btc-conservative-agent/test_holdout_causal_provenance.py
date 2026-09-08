import copy
import hashlib
import json
import pytest
from lifecycle_bundles import LifecycleKey
from research.holdout_causal_provenance import verify_opportunity_reference, require_matching_causal_projection
from test_local_holdout_producer import causal_rows


def fixture(tmp_path):
    key=LifecycleKey('epoch','episode','policy','lane')
    provenance={'source_revision':'rev','deployed_revision':'rev','config_signature':'cfg','tile_config_signature':'tile'}
    rows=causal_rows(tmp_path,[{}],key,provenance)
    return rows,dict(data_root=tmp_path,identity=key.as_dict(),provenance=provenance)


@pytest.mark.parametrize('defect',['embedded','duplicate','schema','signal','features','opportunity'])
def test_exact_causal_ref_rejects_changed_material(tmp_path,defect):
    rows,args=fixture(tmp_path)
    ref=rows[0]['shared_context_binding']['references'][0]
    if defect=='embedded':
        ref['source_row']['signal_ts']=2
        with pytest.raises(ValueError,match='HASH_MISMATCH'): verify_opportunity_reference(rows,**args)
        return
    if defect=='duplicate':
        rows[0]['shared_context_binding']['references'].append(copy.deepcopy(ref))
        with pytest.raises(ValueError,match='AMBIGUOUS'): verify_opportunity_reference(rows,**args)
        return
    if defect=='schema':
        ref['source_row']['feature_snapshot_at_signal'].pop('capture_schema')
        raw=(json.dumps(ref['source_row'])+'\n').encode()
        (tmp_path/'v3/ledgers/opportunity.jsonl').write_bytes(raw)
        ref.update(row_length=len(raw),row_sha256=hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError,match='AVAILABILITY_UNPROVEN'): verify_opportunity_reference(rows,**args)
        return
    proof=verify_opportunity_reference(rows,**args)
    original={k:copy.deepcopy(proof[k]) for k in ('source_episode_id','opportunity_id','signal_ts','pre_entry_features')}
    require_matching_causal_projection(original,proof)
    if defect=='signal': original['signal_ts']=1001
    elif defect=='opportunity': original['opportunity_id']='other'
    else: original['pre_entry_features']['regime']['value']='BEAR'
    with pytest.raises(ValueError,match='PROJECTION_'): require_matching_causal_projection(original,proof)
