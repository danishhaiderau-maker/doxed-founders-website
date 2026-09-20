"""Bounded contracts for pre-AI unavailable evidence."""
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from research.scan_counterfactual_unavailable import (
    build_scan_counterfactual_unavailable,
    record_scan_counterfactual_unavailable,
)


def test_context_unavailable_is_bilateral_diagnostic_evidence_only():
    receipt = build_scan_counterfactual_unavailable(
        context={'trade_id': 'scan-ctx-1'},
        reason_code='CONTEXT_UNAVAILABLE',
        observed_at_ts=1_700_000_000.0,
        source_revision='a' * 40,
        epoch_id='epoch-fixture',
    )
    assert receipt['schema'] == 'scan_counterfactual_unavailable_v1'
    assert receipt['scan_id'] == 'scan-ctx-1'
    assert receipt['ai_evaluated'] is False
    assert receipt['simulated_trade_count'] == 0
    assert receipt['qualification_eligible'] is False
    assert set(receipt['directional_coverage']) == {'LONG', 'SHORT'}
    assert all(side == {
        'status': 'UNAVAILABLE', 'reason_code': 'CONTEXT_UNAVAILABLE',
    } for side in receipt['directional_coverage'].values())
    assert not any(key in receipt for key in (
        'ai_decision', 'win_prob', 'market_indicators', 'fill_status',
    ))


@pytest.mark.parametrize('reason,observed_at', [
    ('MADE_UP_REASON', 1_700_000_000.0),
    ('CONTEXT_UNAVAILABLE', 0),
    ('CONTEXT_UNAVAILABLE', float('nan')),
])
def test_unavailable_receipt_rejects_unbounded_or_invalid_authority(reason, observed_at):
    with pytest.raises(ValueError):
        build_scan_counterfactual_unavailable(
            context={}, reason_code=reason, observed_at_ts=observed_at,
        )


def test_sink_failure_stays_visible_without_changing_receipt_semantics():
    result = record_scan_counterfactual_unavailable(
        append_receipt=lambda _receipt: False,
        context={'shared_ai_call_id': 'shared-1'},
        reason_code='CONTEXT_UNAVAILABLE',
        observed_at_ts=1_700_000_000.0,
    )
    assert result['write_status'] == 'FAILED'
    assert result['receipt']['scan_id'] == 'shared-1'
    assert result['receipt']['ai_evaluated'] is False
    assert result['receipt']['simulated_trade_count'] == 0
