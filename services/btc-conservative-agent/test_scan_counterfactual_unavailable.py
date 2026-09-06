import pytest
from research.scan_counterfactual_unavailable import (
    build_scan_counterfactual_unavailable, record_scan_counterfactual_unavailable)


def fields():
    return dict(context={'trade_id':'scan-123', 'secret':'never copy', 'price':123},
                reason_code='FEATURE_VALIDATION_FAILED', observed_at_ts=123,
                source_revision='a'*40, epoch_id='epoch-test')


def test_gap_preserves_identity_without_fabricating_trade_or_market():
    receipt = build_scan_counterfactual_unavailable(**fields())
    assert receipt['scan_id'] == 'scan-123'
    assert set(receipt['directional_coverage']) == {'LONG', 'SHORT'}
    assert all(row['status'] == 'UNAVAILABLE' for row in receipt['directional_coverage'].values())
    assert receipt['simulated_trade_count'] == 0
    assert receipt['ai_evaluated'] is False
    assert 'never copy' not in str(receipt) and 'price' not in receipt


def test_missing_identity_not_invented():
    receipt = build_scan_counterfactual_unavailable(**{**fields(), 'context':{}})
    assert receipt['scan_id'] is None and receipt['identity_status'] == 'MISSING'


@pytest.mark.parametrize('sink', [lambda row:False, lambda row:None])
def test_write_failure_not_hidden(sink):
    assert record_scan_counterfactual_unavailable(append_receipt=sink, **fields())['write_status'] == 'FAILED'


def test_sink_exception_and_success():
    def failed(row):
        raise OSError('disk full')
    assert record_scan_counterfactual_unavailable(append_receipt=failed, **fields())['write_status'] == 'FAILED'
    rows=[]
    def accepted(row):
        rows.append(row)
        return True
    assert record_scan_counterfactual_unavailable(append_receipt=accepted, **fields())['write_status'] == 'ACCEPTED'
    assert len(rows) == 1
