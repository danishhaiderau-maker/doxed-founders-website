import hashlib
import pytest
from research_exact_deletion import delete_exact_research_files, ResearchDeletionRejected
from research_reset_failure_detail import reset_failure_fields


def test_mismatch_identifies_target_without_exposing_path(tmp_path):
    target = tmp_path / 'signal_replay.jsonl'
    target.write_bytes(b'changed')
    receipt = tmp_path / 'receipt.json'
    with pytest.raises(ResearchDeletionRejected) as caught:
        delete_exact_research_files(root=tmp_path, targets=[target], allowed_paths=[target],
            receipt_path=receipt, quiescent=True, recovery_states={'test': 'EMPTY'},
            expected_sha256_by_path={str(target): 'a' * 64})
    fields = reset_failure_fields(caught.value)
    assert fields['hash_mismatch']['target_path_sha256'] == hashlib.sha256(str(target).encode()).hexdigest()
    assert fields['hash_mismatch']['observed_sha256'] == hashlib.sha256(b'changed').hexdigest()
    assert fields['hash_mismatch']['mismatch_count'] == 1
    assert str(target) not in str(fields)
    assert target.read_bytes() == b'changed'
    assert not receipt.exists()


@pytest.mark.parametrize('field,value', [
    ('target_path_sha256', '/private/path'),
    ('expected_sha256', 'a' * 63),
    ('observed_sha256', 'g' * 64),
    ('mismatch_count', True),
    ('mismatch_count', 0),
    ('mismatch_count', 100001),
])
def test_malformed_mismatch_detail_is_not_published(field, value):
    error = ResearchDeletionRejected('EXPECTED_SHA256_MISMATCH')
    error.hash_mismatch = dict(target_path_sha256='a' * 64,
        expected_sha256='b' * 64, observed_sha256='c' * 64,
        mismatch_count=1, raw_path='/private/path')
    error.hash_mismatch[field] = value
    assert reset_failure_fields(error) == {
        'error': 'ResearchDeletionRejected',
        'rejection_code': 'EXPECTED_SHA256_MISMATCH'}


def test_valid_detail_drops_extra_fields():
    error = ResearchDeletionRejected('EXPECTED_SHA256_MISMATCH')
    error.hash_mismatch = dict(target_path_sha256='a' * 64,
        expected_sha256='b' * 64, observed_sha256='c' * 64,
        mismatch_count=1, raw_path='/private/path')
    assert 'raw_path' not in reset_failure_fields(error)['hash_mismatch']
