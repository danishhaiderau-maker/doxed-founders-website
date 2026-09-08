from copy import deepcopy
import pytest
from research import policy_evidence_evaluator as evaluator
from research.discovery_scorecard_publication import _dynamic_projection
from test_policy_evidence_evaluator import _fixture, _row


def test_actual_evaluator_identity_reaches_discovery(tmp_path):
    result=evaluator.build_v3_conservative_results(_fixture(tmp_path,entry_rows=[_row(10,ask=100),_row(11,ask=100)]))
    row=result['results'][0]
    receipt=row['lifecycle_evidence']['receipt']
    assert row['source_lifecycle_identity']==receipt['identity']
    assert row['source_evidence_collected_receipt_sha256']==receipt['evidence_collected_receipt_sha256']
    assert row['source_completion_receipt_sha256']==receipt['completion_receipt_sha256']
    assert _dynamic_projection(row,{})['source_lifecycle_identity']==receipt['identity']


@pytest.mark.parametrize('kind',['missing','duplicate','other_lane'])
def test_no_inferred_lineage_for_missing_duplicate_or_crosslane(tmp_path,monkeypatch,kind):
    root=_fixture(tmp_path,entry_rows=[_row(10,ask=100),_row(11,ask=100)])
    original=evaluator.join_lifecycle_evidence
    def joined(*args):
        if kind!='other_lane':
            return {'status':'UNKNOWN','reason_codes':['UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_'+kind.upper()]}
        value=deepcopy(original(*args)); value['receipt']['identity']['research_lane']='OTHER'
        return value
    monkeypatch.setattr(evaluator,'join_lifecycle_evidence',joined)
    result=evaluator.build_v3_conservative_results(root)
    assert all('source_lifecycle_identity' not in row for row in result['results'])
