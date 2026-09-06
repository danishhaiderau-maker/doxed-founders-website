"""Persist the actual reset stage before entering slow payload validation."""
import json

from test_bot_destructive_research_reset import runtime, run


def test_payload_stage_is_durable_before_execution(runtime, monkeypatch):
    import research_reset_execution
    original = research_reset_execution.execute_research_reset
    observed = []

    def inspect(**kwargs):
        if not kwargs.get('validate_only'):
            pointer = json.loads((runtime['root'] / 'research_reset_receipts' / 'ACTIVE_RESET.json').read_text())
            operation = runtime['root'] / 'research_reset_receipts' / pointer['reset_id'] / 'operation.json'
            observed.append(json.loads(operation.read_text())['stage'])
        return original(**kwargs)

    monkeypatch.setattr(research_reset_execution, 'execute_research_reset', inspect)
    result = run(runtime)
    assert result['ok'] is True
    assert observed == ['PAYLOAD_DELETION']


def test_payload_failure_keeps_exact_stage(runtime, monkeypatch):
    import research_reset_execution
    from research_exact_deletion import ResearchDeletionRejected
    original = research_reset_execution.execute_research_reset

    def fail_payload(**kwargs):
        if not kwargs.get('validate_only'):
            raise ResearchDeletionRejected('RESET_TARGET_CHANGED_AFTER_PLAN')
        return original(**kwargs)

    monkeypatch.setattr(research_reset_execution, 'execute_research_reset', fail_payload)
    result = run(runtime)
    assert result['ok'] is False
    assert runtime['payload'].exists()
    pointer = json.loads((runtime['root'] / 'research_reset_receipts' / 'ACTIVE_RESET.json').read_text())
    operation = runtime['root'] / 'research_reset_receipts' / pointer['reset_id'] / 'operation.json'
    receipt = json.loads(operation.read_text())
    assert 'PAYLOAD_DELETION' in json.dumps(receipt)
