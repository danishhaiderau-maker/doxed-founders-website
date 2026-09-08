import pytest
from research_v3_sealed_holdout import create_seal, consume_seal
from test_research_v3_sealed_holdout import IDENTITY, CANDIDATES, episode
from research_v3_sealed_holdout import verify_evaluation_receipt, _sha256
from research_v3_contract import canonical_hash


def seal(root, **overrides):
    args = dict(training_completed_at=90, sealed_at=100, holdout_start_ts=200)
    args.update(overrides)
    return create_seal(root, **IDENTITY, training_snapshot_hash='train',
                       policy_candidates=CANDIDATES, **args)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
@pytest.mark.parametrize('field', ['training_completed_at', 'sealed_at', 'holdout_start_ts'])
def test_nonfinite_seal_rejected(tmp_path, field, bad):
    with pytest.raises(ValueError, match='NONFINITE_SEAL_TIME'):
        seal(tmp_path, **{field: bad})


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_evaluation_rejected(tmp_path, bad):
    frozen = seal(tmp_path)
    with pytest.raises(ValueError, match='NONFINITE_EVALUATION_TIME'):
        consume_seal(tmp_path, seal_id=frozen['seal_id'], policy_candidates=CANDIDATES,
                     holdout_episodes=[episode(0)], evaluation_started_at=bad)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
@pytest.mark.parametrize('field', ['signal_ts', 'evidence_collected_at'])
def test_nonfinite_episode_cannot_pass(tmp_path, field, bad):
    frozen = seal(tmp_path)
    row = {**episode(0), field: bad}
    receipt = consume_seal(tmp_path, seal_id=frozen['seal_id'], policy_candidates=CANDIDATES,
                           holdout_episodes=[row], evaluation_started_at=400)
    assert not receipt['passed']
    assert 'NONFINITE_CAUSAL_OR_COLLECTION_TIME:e-0' in receipt['blockers']


def test_rehashed_nonfinite_evaluation_receipt_rejected(tmp_path):
    frozen = seal(tmp_path)
    rows = [episode(0)]
    receipt = consume_seal(tmp_path, seal_id=frozen['seal_id'], policy_candidates=CANDIDATES,
                           holdout_episodes=rows, evaluation_started_at=400)
    receipt['evaluation_started_at'] = float('inf')
    body = {k: v for k, v in receipt.items() if k not in {'receipt_id', 'content_sha256'}}
    receipt['receipt_id'] = canonical_hash('holdout-evaluation', body, length=64)
    receipt['content_sha256'] = _sha256(body)
    assert not verify_evaluation_receipt(receipt, policy_id='p', holdout_episodes=rows)
