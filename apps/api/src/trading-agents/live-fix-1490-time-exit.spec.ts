import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  validateTerminalCloseAuthority, isTimeDeadlineExitTradeId, timeDeadlineExitEnabled,
  TIME_DEADLINE_PREARM_MS,
} from './signal-subscriber-execution.service';

const auth = (fill: number, over: Record<string, unknown> = {}, tradeId = 'dsl-1791793701-5') => ({
  kind: 'SIGNED_TIME_DEADLINE' as const,
  canonicalTradeId: tradeId,
  lifecycleGeneration: 'rev:1',
  evidence: { schema: 'signed_time_deadline_v1', sourceFillAtMs: fill, holdSec: 60, deadlineMs: fill + 60_000, ...over },
});

test('1490: time-deadline authority valid only for dsl- at/after deadline - prearm', () => {
  const fill = 1_000_000;
  assert.equal(validateTerminalCloseAuthority(auth(fill), fill + 60_000), true);
  assert.equal(validateTerminalCloseAuthority(auth(fill), fill + 60_000 - TIME_DEADLINE_PREARM_MS), true);
  assert.equal(validateTerminalCloseAuthority(auth(fill), fill + 60_000 - TIME_DEADLINE_PREARM_MS - 1), false);
  assert.equal(validateTerminalCloseAuthority(auth(fill), fill + 30_000), false);
  assert.equal(validateTerminalCloseAuthority(auth(fill, {}, 'gs1-abc'), fill + 61_000), false);
  assert.equal(validateTerminalCloseAuthority(auth(fill, { holdSec: 30, deadlineMs: fill + 30_000 }), fill + 61_000), false);
  assert.equal(validateTerminalCloseAuthority(auth(fill, { deadlineMs: fill + 1_000 }), fill + 61_000), false);
});

test('1490: only DISLOC trade ids, kill switch off disables', () => {
  assert.equal(isTimeDeadlineExitTradeId('dsl-1-2'), true);
  assert.equal(isTimeDeadlineExitTradeId('dsf-1-2'), false);
  assert.equal(timeDeadlineExitEnabled({}), true);
  assert.equal(timeDeadlineExitEnabled({ RELAY_TIME_DEADLINE_EXIT: 'off' }), false);
});

test('1490: close submit is gated to the deadline and paper close stays fallback', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const target = src.slice(src.indexOf('private async closeParticipantPositionToLedgerTarget('));
  const gate = target.indexOf('terminalFence.submitNotBeforeMs');
  const submitting = target.indexOf("to: 'SUBMITTING'");
  assert.ok(gate > 0 && gate < submitting, 'deadline wait happens before the SUBMITTING fence');
  assert.ok(src.includes("this.scheduleTimeDeadlineExit(instance.agentId, instance.userId, wake.tradeId ?? null, wake.signedOpen.sourceEventAtMs)"));
  assert.ok(src.includes('submitNotBeforeMs: deadlineMs'));
});
