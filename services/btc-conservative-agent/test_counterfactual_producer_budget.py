import pytest
from research import local_holdout_producer as module


def test_counterfactual_limit_cannot_publish_partial_artifact(tmp_path,monkeypatch):
    root=tmp_path/'mirror'; root.mkdir()
    source={'epoch':'epoch'}
    mapping={'expected_generation':{'epoch_id':'epoch'},'candidates':[],
        'training_episodes':[{'policy_outcomes':{'p':{'counterfactual_identity':{'invalid':True}}}} for _ in range(33)]}
    monkeypatch.setattr(module,'load_verified_local_dynamic_mapping',lambda **kw:(mapping,{'source_generation':source}))
    monkeypatch.setattr(module,'_check',lambda *a,**kw:source)
    monkeypatch.setattr(module,'_source',lambda value:value)
    monkeypatch.setattr(module,'_write_once',lambda *a:pytest.fail('partial publication'))
    with pytest.raises(ValueError,match='RESUMABLE_VERIFICATION_REQUIRED'):
        module.produce_local_holdout(repo_root=tmp_path/'repo',data_root=root,source_revision='rev')
