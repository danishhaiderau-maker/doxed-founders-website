import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  evaluateFounderCompletionEvidence,
  founderToolsForMode,
  founderWorkModeInstruction,
  isFounderVisualVerificationCommand,
  renderFounderCompletionReceipt,
} from './completion-evidence';

describe('Founder completion evidence', () => {
  it('accepts a completed read-only answer without inventing test evidence', () => {
    const receipt = evaluateFounderCompletionEvidence({
      mode: 'ask',
      goal: 'Explain how routing works.',
      finalAnswer: 'Founder Auto selects the eligible managed route.',
      requestCompleted: true,
      editedFiles: [],
      passedChecks: [],
    });

    assert.equal(receipt.verdict, 'passed');
    assert.equal(receipt.scope, 'read_only');
    assert.match(renderFounderCompletionReceipt(receipt), /Passed \| read-only response/);
  });

  it('rejects workspace edits in Ask and Plan modes', () => {
    for (const mode of ['ask', 'plan'] as const) {
      const receipt = evaluateFounderCompletionEvidence({
        mode,
        goal: 'Inspect the current implementation.',
        finalAnswer: 'I inspected it.',
        requestCompleted: true,
        editedFiles: ['src/app.ts'],
        passedChecks: ['npm test'],
      });

      assert.equal(receipt.verdict, 'incomplete');
      assert.match(receipt.missing.join(' '), new RegExp(`${mode} mode changed`, 'i'));
    }
  });

  it('rejects an implementation request that produced no edit', () => {
    const receipt = evaluateFounderCompletionEvidence({
      mode: 'build',
      goal: 'Fix the broken account button.',
      finalAnswer: 'Done.',
      requestCompleted: true,
      editedFiles: [],
      passedChecks: [],
    });

    assert.equal(receipt.verdict, 'incomplete');
    assert.match(receipt.missing.join(' '), /no workspace edit/);
  });

  it('requires a passing verification command for code edits', () => {
    const receipt = evaluateFounderCompletionEvidence({
      mode: 'debug',
      goal: 'Fix the gateway retry bug.',
      finalAnswer: 'The retry boundary is fixed.',
      requestCompleted: true,
      editedFiles: ['apps/api/src/gateway.ts'],
      passedChecks: [],
    });

    assert.equal(receipt.verdict, 'incomplete');
    assert.match(receipt.missing.join(' '), /no passing test/);
  });

  it('requires visual evidence for user-facing UI edits', () => {
    const incomplete = evaluateFounderCompletionEvidence({
      mode: 'build',
      goal: 'Update the Founder navigation.',
      finalAnswer: 'The Founder navigation is updated.',
      requestCompleted: true,
      editedFiles: ['apps/web/src/components/founder-nav.tsx'],
      passedChecks: ['npm run typecheck'],
    });
    const passed = evaluateFounderCompletionEvidence({
      mode: 'build',
      goal: 'Update the Founder navigation.',
      finalAnswer: 'The Founder navigation is updated.',
      requestCompleted: true,
      editedFiles: ['apps/web/src/components/founder-nav.tsx'],
      passedChecks: ['npm run typecheck', 'npm run test:playwright -- founder-nav'],
    });

    assert.equal(incomplete.verdict, 'incomplete');
    assert.match(incomplete.missing.join(' '), /no passing visual/);
    assert.equal(passed.verdict, 'passed');
    assert.equal(passed.visualCheckCount, 1);
  });

  it('rejects visual keywords and shell composition as completion evidence', () => {
    assert.equal(isFounderVisualVerificationCommand('echo screenshot'), false);
    assert.equal(
      isFounderVisualVerificationCommand('npm run typecheck -- screenshot'),
      false,
    );
    assert.equal(
      isFounderVisualVerificationCommand('npm run test:playwright && echo passed'),
      false,
    );
    assert.equal(
      isFounderVisualVerificationCommand('npm run test:playwright -- founder-nav'),
      true,
    );
    assert.equal(
      isFounderVisualVerificationCommand('npx playwright test founder-nav.spec.ts'),
      true,
    );
    assert.equal(
      isFounderVisualVerificationCommand(
        'node packages/founder-ide/scripts/installed-founder-navigation-qa.mjs',
      ),
      true,
    );
  });

  it('passes a verified non-UI implementation and exposes safe mode contracts', () => {
    const receipt = evaluateFounderCompletionEvidence({
      mode: 'build',
      goal: 'Implement quota reservation.',
      finalAnswer: 'Quota reservation is implemented and tested.',
      requestCompleted: true,
      editedFiles: ['apps/api/src/quota.ts'],
      passedChecks: ['npm test -- quota'],
    });

    assert.equal(receipt.verdict, 'passed');
    assert.match(founderWorkModeInstruction('plan'), /Do not edit files/);
    assert.deepEqual(
      founderToolsForMode('plan', ['founder-read-workspace', 'founder-edit-file', 'founder-run-command']),
      ['founder-read-workspace'],
    );
  });
});
