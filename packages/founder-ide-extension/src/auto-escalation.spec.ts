import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { founderAutoEscalationReason, isFailedVerificationResult } from './auto-escalation';

describe('Founder Auto escalation', () => {
  it('keeps routine questions and ordinary edits on Flash', () => {
    assert.equal(founderAutoEscalationReason([{ role: 'user', content: 'Explain this function.' }]), null);
    assert.equal(founderAutoEscalationReason([{ role: 'user', content: 'Fix the label in src/ui.ts.' }]), null);
  });

  it('requires multiple independent complexity signals', () => {
    const request = `${'Review this carefully. '.repeat(50)} Compare the authentication and security trade-offs end-to-end.`;
    assert.equal(founderAutoEscalationReason([{ role: 'user', content: request }]), 'high_complexity');
  });

  it('escalates after a real failed command or test result', () => {
    assert.equal(isFailedVerificationResult('Tests completed.\n[exit code 0]'), false);
    assert.equal(founderAutoEscalationReason([
      { role: 'user', content: 'Fix the settings label.' },
      { role: 'tool', content: '2 tests failed\n[exit code 1]' },
    ]), 'failed_verification');
  });
});
