import hashlib
import json
import pytest
from research.counterfactual_source_membership import verify_membership
from research.policy_evidence_schema import canonical_json


def pinned(tmp_path):
    root=tmp_path
    raw=b'{"record_id":"opp"}\n'; tape=b'{"rows":[]}'
    sha=lambda x:hashlib.sha256(x).hexdigest()
    digest=sha(tape)
    names={'v3/ledgers/opportunity.jsonl':raw,
        f'v3/market_segments/{digest[:2]}/{digest}.json':tape}
    state={}
    for name,value in names.items():
        path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value)
        state[name]={'size':len(value),'sha256':sha(value)}
    (root/'.fly-sync-state.json').write_text(json.dumps(state))
    manifest={'dataset_epoch':'epoch','source_revision':'rev','deployed_revision':'rev',
        'tile_config_signature':'config','dataset_checksum':sha(canonical_json({'revision':'rev','epoch':'epoch','files':state}).encode())}
    manifest['entry_hash']=sha(canonical_json(manifest).encode())
    (root/'canonical_dataset_current.json').write_text(json.dumps(manifest))
    return dict(root=root,source={'manifest_entry_hash':manifest['entry_hash']},
        opportunity_ref={'byte_offset':0,'row_length':len(raw)},segment_refs=[{'sha256':digest}])


def test_actual_pinned_membership(tmp_path):
    verify_membership(**pinned(tmp_path))


@pytest.mark.parametrize('kind',['appended','segment','changed_prefix'])
def test_unpromoted_sources_rejected(tmp_path,kind):
    args=pinned(tmp_path)
    path=tmp_path/'v3/ledgers/opportunity.jsonl'
    if kind=='appended':
        size=path.stat().st_size
        with path.open('ab') as stream: stream.write(b'{}\n')
        args['opportunity_ref']={'byte_offset':size,'row_length':3}
    elif kind=='segment':
        raw=b'{"rows":[1]}'; digest=hashlib.sha256(raw).hexdigest()
        path=tmp_path/f'v3/market_segments/{digest[:2]}/{digest}.json'
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
        args['segment_refs']=[{'sha256':digest}]
    else: path.write_bytes(b'x'*path.stat().st_size)
    with pytest.raises(ValueError): verify_membership(**args)
