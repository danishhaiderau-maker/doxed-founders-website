import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { TIME_DEADLINE_PREARM_MS } from './signal-subscriber-execution.service';

// LIVE-FIX-1490's DISLOC (dsl-) executor-owned TIME_60S exit was removed with Tiles 6/7
// (23 Oct 2026). What stays: the generic submitNotBeforeMs fence, bounded by TIME_DEADLINE_PREARM_MS.
test('1490 (retired): no SIGNED_TIME_DEADLINE authority or scheduling; generic fence bound stays', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  assert.equal(src.includes('SIGNED_TIME_DEADLINE'), false);
  assert.equal(src.includes('scheduleTimeDeadlineExit'), false);
  assert.equal(src.includes('/^dsl-/'), false);
  assert.equal(TIME_DEADLINE_PREARM_MS, 1_500);
  const target = src.slice(src.indexOf('private async closeParticipantPositionToLedgerTarget('));
  const gate = target.indexOf('terminalFence.submitNotBeforeMs');
  const submitting = target.indexOf("to: 'SUBMITTING'");
  assert.ok(gate > 0 && gate < submitting, 'submit-not-before wait stays before the SUBMITTING fence');
});

import { appendRelayPauseHistory as _aph, relayPauseCode as _rpc, RELAY_PAUSE_HISTORY_MAX as _max } from './signal-subscriber-execution.service';
test('LIVE-FIX-1491 pause history: code parse + bounded append', () => {
  assert.equal(_rpc('TERMINAL_CLOSE_CLAIM_REFUSED: x'), 'TERMINAL_CLOSE_CLAIM_REFUSED');
  assert.equal(_rpc('oops'), 'UNCLASSIFIED');
  let h: Record<string, unknown>[] = _aph(null, { code: 'A' });
  assert.equal(h.length, 1);
  for (let i = 0; i < 80; i++) h = _aph(h, { i });
  assert.equal(h.length, _max);
  assert.deepEqual(h[h.length - 1], { i: 79 });
});
