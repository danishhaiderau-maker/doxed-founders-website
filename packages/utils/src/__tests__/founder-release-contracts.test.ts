import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FOUNDER_BENCHMARK_PROTOCOL,
  FOUNDER_FREE_WEEKLY_WEIGHTED_UNITS,
  founderPrivateBetaReady,
  founderPublicReleaseReady,
  measureFounderTokenUsage,
  validateFounderProofReceipt,
  type FounderProofReceipt,
  type FounderReleaseEvidence,
} from '../index.js';

const sha = 'a'.repeat(64);

function receipt(): FounderProofReceipt {
  return {
    version: 1,
    receiptId: 'receipt-1',
    taskId: 'task-1',
    workspaceId: 'workspace-1',
    agentId: 'agent-1',
    startedAt: '2026-07-22T00:00:00.000Z',
    finishedAt: '2026-07-22T00:01:00.000Z',
    outcome: 'completed',
    summary: 'Implemented and verified the requested change.',
    changedFiles: [
      {
        path: 'src/example.ts',
        beforeSha256: sha,
        afterSha256: sha,
        additions: 2,
        deletions: 1,
      },
    ],
    tests: [
      {
        command: 'npm test',
        status: 'passed',
        exitCode: 0,
        durationMs: 10,
        provenance: 'pre_existing',
        summary: '1 test passed',
      },
    ],
    commands: [],
    cost: {
      weightsVersion: 'founder-wtu-v1',
      weightedUnits: 100,
      rawInputTokens: 25,
      rawCachedInputTokens: 25,
      rawOutputTokens: 10,
      rawReasoningTokens: 10,
      providerCostUsd: 0.001,
      billingSource: 'platform_managed',
    },
    failures: [],
    previousReceiptSha256: null,
    receiptSha256: sha,
  };
}

function evidence(): FounderReleaseEvidence {
  return {
    unitTestsPassed: true,
    integrationTestsPassed: true,
    installedQaPassed: true,
    rollbackPassed: true,
    cleanVmPassed: true,
    soakHours: 24,
    signingGate: 'verified',
    betaFounders: 20,
    betaCompletedTasks: 200,
    criticalFailureRate: 0.02,
    weekTwoRetention: 0.35,
    unreconciledCostReservations: 0,
  };
}

describe('Founder weighted token units', () => {
  it('weights uncached, cached, visible output, and reasoning separately', () => {
    const result = measureFounderTokenUsage({
      inputTokens: 1_000,
      cachedInputTokens: 1_000,
      outputTokens: 100,
      reasoningTokens: 100,
      billingSource: 'platform_managed',
    });
    assert.equal(result.rawTokens, 2_200);
    assert.equal(result.weightedUnits, 1_850);
    assert.equal(result.weightsVersion, 'founder-wtu-v1');
    assert.equal(FOUNDER_FREE_WEEKLY_WEIGHTED_UNITS, 200_000);
  });

  it('records raw BYOK usage without consuming managed quota', () => {
    const result = measureFounderTokenUsage({
      inputTokens: 500,
      outputTokens: 100,
      billingSource: 'personal_byok',
    });
    assert.equal(result.rawTokens, 600);
    assert.equal(result.weightedUnits, 0);
    assert.equal(result.managed, false);
  });

  it('rejects negative or fractional token counts', () => {
    assert.throws(() =>
      measureFounderTokenUsage({
        inputTokens: -1,
        outputTokens: 0,
        billingSource: 'platform_managed',
      }),
    );
    assert.throws(() =>
      measureFounderTokenUsage({
        inputTokens: 1.5,
        outputTokens: 0,
        billingSource: 'platform_managed',
      }),
    );
  });
});

describe('Founder proof receipts', () => {
  it('accepts a complete hash-linked receipt', () => {
    assert.deepEqual(validateFounderProofReceipt(receipt()), []);
  });

  it('rejects completed receipts with failed tests or failures', () => {
    const invalid = receipt();
    invalid.tests[0]!.status = 'failed';
    invalid.failures.push({
      stage: 'test',
      code: 'TEST_FAILED',
      message: 'The test failed.',
      retryable: true,
    });
    assert.deepEqual(validateFounderProofReceipt(invalid), [
      'completed receipts cannot contain failures',
      'completed receipts cannot contain failed tests',
    ]);
  });
});

describe('Founder release gates', () => {
  it('locks the comparable benchmark sample size and repetitions', () => {
    assert.equal(FOUNDER_BENCHMARK_PROTOCOL.minimumTasks, 50);
    assert.equal(FOUNDER_BENCHMARK_PROTOCOL.runsPerTask, 5);
  });

  it('permits an unsigned private beta but not a public release', () => {
    const candidate = evidence();
    candidate.signingGate = 'identity_pending';
    assert.equal(founderPrivateBetaReady(candidate), true);
    assert.equal(founderPublicReleaseReady(candidate), false);
  });

  it('requires beta evidence and 24-hour soak for public release', () => {
    const candidate = evidence();
    assert.equal(founderPublicReleaseReady(candidate), true);
    candidate.soakHours = 23;
    assert.equal(founderPublicReleaseReady(candidate), false);
  });
});
