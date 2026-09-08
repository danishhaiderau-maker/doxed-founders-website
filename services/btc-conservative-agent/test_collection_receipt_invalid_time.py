import hashlib
import pytest
from lifecycle_bundles import classify_evidence_collection
from research_v3_contract import canonical_json
from lifecycle_completion_receipts import build_evidence_collected_receipt
from test_evidence_collected_receipts import KEY, PROVENANCE, NOW, _completion, _row


@pytest.mark.parametrize('field', ['evidence_collected_at', 'qualification_eligible_at'])
@pytest.mark.parametrize('value', [None, 'bad', 0, -1, float('nan'), float('inf')])
def test_invalid_collection_time_fail_closed(field, value):
    completion = _completion()
    receipt = build_evidence_collected_receipt(completion, identity=KEY.as_dict(),
        event_id='trade-1', provenance=PROVENANCE, collected_at=NOW)['receipt']
    receipt[field] = value
    body = {k:v for k,v in receipt.items() if k != 'evidence_collected_receipt_sha256'}
    receipt['evidence_collected_receipt_sha256'] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    rows = [_row('lifecycle','completion',bundle_completion=completion),
            _row('lifecycle','collected',evidence_collection_receipt=receipt)]
    result = classify_evidence_collection(rows, KEY)
    assert not result['ready']
    assert 'EVIDENCE_COLLECTION_TIME_INVALID' in result['blockers']
