import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { FounderProjectActivityStore, workspaceActivityId } from './project-activity';
import { evaluateFounderCompletionEvidence } from './completion-evidence';

describe('Founder project activity', () => {
  it('links a goal and verified result to one workspace brief', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-activity-'));
    const store = new FounderProjectActivityStore(path.join(root, 'activity.json'));
    const workspaceId = workspaceActivityId(['C:/repo'])!;
    const id = store.begin(workspaceId, 'Fix the gateway parser', 'founder-os-auto', '2026-07-22T00:00:00.000Z');
    assert.ok(id);
    assert.equal(store.complete(id, {
      status: 'completed',
      summary: 'Parser fixed and stream verified.',
      provider: 'deepseek',
      providerModel: 'deepseek-v4-flash',
      editedFiles: ['src/parser.ts'],
      checks: ['npm test'],
      verification: evaluateFounderCompletionEvidence({
        mode: 'debug',
        goal: 'Fix the gateway parser',
        finalAnswer: 'Parser fixed and stream verified.',
        requestCompleted: true,
        editedFiles: ['src/parser.ts'],
        passedChecks: ['npm test'],
      }),
      estimatedTokensAvoided: 420,
      completedAt: '2026-07-22T00:10:00.000Z',
    }), true);

    const brief = store.dailyBrief(workspaceId, 'Gateway', new Date('2026-07-22T12:00:00.000Z'));
    assert.match(brief, /Fix the gateway parser/);
    assert.match(brief, /deepseek\/deepseek-v4-flash/);
    assert.match(brief, /src\/parser\.ts/);
    assert.match(brief, /Completion receipts: 1 passed; 0 incomplete/);
    assert.match(brief, /Estimated tokens avoided: 420/);
  });

  it('keeps workspaces separate and refuses secret-bearing goals', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-activity-'));
    const store = new FounderProjectActivityStore(path.join(root, 'activity.json'));
    const first = workspaceActivityId(['C:/first'])!;
    const second = workspaceActivityId(['C:/second'])!;
    assert.equal(store.begin(first, 'api_key=do-not-store', 'founder-os-fast'), null);
    assert.ok(store.begin(second, 'Review settings UI', 'founder-os-fast'));
    assert.equal(store.recordsFor(first).length, 0);
    assert.equal(store.recordsFor(second).length, 1);
  });
});
