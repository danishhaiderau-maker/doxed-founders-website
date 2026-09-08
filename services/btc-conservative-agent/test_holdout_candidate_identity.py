import copy
import pytest
from research.holdout_candidate_identity import candidate_identity_matches


def fixture():
    row = dict(dataset_epoch='epoch', source_episode_id='episode', opportunity_id='opp', direction='LONG')
    identity = dict(epoch_id='epoch', source_episode_id='episode', opportunity_id='opp',
                    direction='LONG', policy_id='policy', policy_signature='signature', seal_request_id='seal')
    outcome = dict(counterfactual_identity=identity, replay_proof_sha256='a'*64)
    proof = dict(schema='verified_counterfactual_collection_provenance_v1',
                 counterfactual_identity=copy.deepcopy(identity), replay_proof_sha256='a'*64,
                 entry_semantic_replay_verified=True, terminal_semantic_replay_verified=True)
    return row, outcome, proof


def check(row, outcome, proof):
    return candidate_identity_matches(row, outcome, proof, policy='policy', signature='signature', seal_request_id='seal')


def test_exact_separate_identity():
    assert check(*fixture())


@pytest.mark.parametrize('field', ['epoch_id', 'source_episode_id', 'opportunity_id', 'direction', 'policy_id', 'policy_signature', 'seal_request_id'])
def test_wrong_identity_rejected(field):
    row, outcome, proof = fixture()
    proof['counterfactual_identity'][field] = 'other'
    assert not check(row, outcome, proof)


@pytest.mark.parametrize('field', ['entry_semantic_replay_verified', 'terminal_semantic_replay_verified'])
def test_missing_semantic_proof_rejected(field):
    row, outcome, proof = fixture()
    proof[field] = 1
    assert not check(row, outcome, proof)


def test_cannot_borrow_paper_identity_or_wrong_artifact():
    row, outcome, proof = fixture()
    outcome['source_lifecycle_identity'] = {'policy_signature': 'signature'}
    assert not check(row, outcome, proof)
    del outcome['source_lifecycle_identity']
    outcome['replay_proof_sha256'] = 'b'*64
    assert not check(row, outcome, proof)
