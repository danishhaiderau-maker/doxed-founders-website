import { test } from 'node:test';
import assert from 'node:assert/strict';
import { liveCopyPauseState } from './live-copy-ops.service';

const has = (o: Record<string, unknown>, sub: Record<string, unknown>) => {
  for (const [k, v] of Object.entries(sub)) assert.equal(o[k], v, k);
};

test('reports an armed account paused by the showcase bot', () => {
  const s = liveCopyPauseState({ status: 'ACTIVE', lastError: 'Showcase bot paused on dashboard — x', lastPassExitStage: 'SHOWCASE_PAUSED' });
  has(s as unknown as Record<string, unknown>, { paused: true, pause_owner: 'FLY_BOT', pause_source: 'EXECUTOR_LAST_ERROR', executor_last_pass_exit_stage: 'SHOWCASE_PAUSED' });
});
test('uses the last transition for a PAUSED account', () => {
  const s = liveCopyPauseState({ status: 'PAUSED', transition: { actor: 'SAFETY', reason: 'POSITION_MISMATCH', at: '2026-10-11T08:00:20Z' } });
  has(s as unknown as Record<string, unknown>, { paused: true, pause_owner: 'SAFETY', pause_reason: 'POSITION_MISMATCH', paused_at: '2026-10-11T08:00:20Z' });
});
test('is not paused when active with no pause error', () => {
  assert.equal(liveCopyPauseState({ status: 'ACTIVE', lastError: null }).paused, false);
});
