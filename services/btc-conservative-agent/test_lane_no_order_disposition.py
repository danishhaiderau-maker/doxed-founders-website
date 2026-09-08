import pytest
from test_lifecycle_completion_reconciler import KEY, PROV, no_fill_rows
from lifecycle_completion_reconciler import evaluate_lifecycle_completion, evaluate_lifecycle_transfer_ready


def audit():
    return {**PROV, "ledger": "lifecycle", "epoch_id": KEY.collection_epoch_id,
            "episode_id": KEY.episode_id, "policy_signature": KEY.policy_signature,
            "research_lane": KEY.research_lane, "shared_ai_call_id": "scan-1",
            "event_id": "lane-entry:lane:scan-1", "resolution_scope": "LANE_ENTRY",
            "entry_resolution": "NO_ORDER", "entry_resolution_terminal": True,
            "terminal": True, "resolution_deadline_ts": 999999}


def test_no_order_is_audit_not_filled_or_transfer_authority():
    for evaluate in (evaluate_lifecycle_completion, evaluate_lifecycle_transfer_ready):
        result = evaluate(KEY, [audit()], now=100)
        assert result['classification'] == 'ENTRY_RESOLVED_NO_ORDER'
        assert result['ready'] is False and result['receipt'] is None
        assert result['blockers'] == ['AUDIT_ONLY_NO_ORDER_NOT_EXECUTION_LIFECYCLE']


@pytest.mark.parametrize('change', [{'terminal': False}, {'entry_resolution': 'AWAITING'},
                                   {'episode_id': 'other'}, {'shared_ai_call_id': ''}])
def test_nonterminal_or_wrong_identity_remains_strict(change):
    assert evaluate_lifecycle_completion(KEY, [{**audit(), **change}], now=100)['classification'] != 'ENTRY_RESOLVED_NO_ORDER'


def test_order_evidence_or_conflicting_identity_cannot_hide_under_audit():
    for rows in ([audit(), *no_fill_rows()], [audit(), {**audit(), 'episode_id': 'other'}]):
        result = evaluate_lifecycle_completion(KEY, rows, now=100)
        assert result['classification'] != 'ENTRY_RESOLVED_NO_ORDER'
        assert result['ready'] is False
