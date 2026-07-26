import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FounderProjectActivityRecord } from './project-activity';
import {
  activeCoordinationReason,
  normalizeDailyReviewProbeUrl,
  reviewOwnedFiles,
  selectDailyReviewTaskNames,
  shouldRunDailyQualityReview,
} from './daily-quality-review-state';

const NOW = Date.parse('2026-07-23T10:00:00.000Z');

function record(
  status: FounderProjectActivityRecord['status'],
  editedFiles: string[],
): FounderProjectActivityRecord {
  return {
    id: `${status}-${editedFiles.length}`,
    workspaceId: 'workspace',
    startedAt: '2026-07-23T09:00:00.000Z',
    completedAt: '2026-07-23T09:30:00.000Z',
    goal: 'Verify Founder work',
    model: 'founder-os-auto',
    status,
    summary: '',
    provider: 'deepseek',
    providerModel: 'deepseek-v4-flash',
    editedFiles,
    checks: [],
    estimatedTokensAvoided: 0,
    verification: null,
  };
}

describe('Founder daily quality review', () => {
  it('runs only when enabled and a completed review is at least one day old', () => {
    assert.equal(shouldRunDailyQualityReview(false, undefined, NOW), false);
    assert.equal(shouldRunDailyQualityReview(true, undefined, NOW), true);
    assert.equal(
      shouldRunDailyQualityReview(true, '2026-07-22T09:59:59.000Z', NOW),
      true,
    );
    assert.equal(
      shouldRunDailyQualityReview(true, '2026-07-23T09:30:00.000Z', NOW),
      false,
    );
  });

  it('deduplicates normalized Founder-owned files and ignores cancelled work', () => {
    assert.deepEqual(
      reviewOwnedFiles([
        record('completed', ['src\\app.ts', 'src/shared.ts']),
        record('reused', ['src/app.ts']),
        record('cancelled', ['src/ignored.ts']),
      ]),
      ['src/app.ts', 'src/shared.ts'],
    );
  });

  it('defers when another Founder task is active', () => {
    assert.equal(activeCoordinationReason({
      activeCount: 0,
      tasks: [],
    }), null);
    assert.match(activeCoordinationReason({
      activeCount: 1,
      tasks: [{
        title: 'Update settings',
      }],
    }) ?? '', /deferred/i);
  });

  it('runs only explicitly selected or default build and test tasks', () => {
    const tasks = [
      { name: 'Build', group: 'build', isDefault: true },
      { name: 'Tests', group: 'test', isDefault: true },
      { name: 'Deploy', group: 'build', isDefault: false },
      { name: 'Format', group: 'none', isDefault: true },
    ];
    assert.deepEqual(selectDailyReviewTaskNames(tasks, []), ['Build', 'Tests']);
    assert.deepEqual(selectDailyReviewTaskNames(tasks, ['Deploy']), ['Deploy']);
  });

  it('limits a configured review to four unique tasks', () => {
    const tasks = ['One', 'Two', 'Three', 'Four', 'Five', 'One']
      .map((name) => ({ name }));
    assert.deepEqual(
      selectDailyReviewTaskNames(tasks, ['One', 'Two', 'Three', 'Four', 'Five']),
      ['One', 'Two', 'Three', 'Four'],
    );
  });

  it('accepts safe HTTPS and localhost probes without credential-bearing URLs', () => {
    assert.equal(
      normalizeDailyReviewProbeUrl('https://example.com/api/health'),
      'https://example.com/api/health',
    );
    assert.equal(
      normalizeDailyReviewProbeUrl('http://localhost:7012/health'),
      'http://localhost:7012/health',
    );
    assert.equal(normalizeDailyReviewProbeUrl('http://example.com/health'), null);
    assert.equal(normalizeDailyReviewProbeUrl('https://user:secret@example.com/health'), null);
    assert.equal(normalizeDailyReviewProbeUrl('https://example.com/health?token=secret'), null);
  });
});
