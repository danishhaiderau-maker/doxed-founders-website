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
  assert.equal(timeDeadlineExitEnabled({}), false, 'LIVE-FIX-1495: default OFF (staged rollout)');
  assert.equal(timeDeadlineExitEnabled({ RELAY_TIME_DEADLINE_EXIT: 'on' }), true);
  assert.equal(timeDeadlineExitEnabled({ RELAY_TIME_DEADLINE_EXIT: 'off' }), false);
});

test('1490: close submit is gated to the deadline and paper close stays fallback', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const target = src.slice(src.indexOf('private async closeParticipantPositionToLedgerTarget('));
  const gate = target.indexOf('terminalFence.submitNotBeforeMs');
  const submitting = target.indexOf("to: 'SUBMITTING'");
  assert.ok(gate > 0 && gate < submitting, 'deadline wait happens before the SUBMITTING fence');
  assert.ok(!src.includes('wake.signedOpen.sourceEventAtMs);'), 'LIVE-FIX-1495: no longer keyed on the PAPER fill');
  assert.ok(src.includes('void this.recoverTimeDeadlineExits(agent.id'));
  assert.ok(src.includes('submitNotBeforeMs: deadlineMs'));
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

import { SignalSubscriberExecutionService, liveFillAtMsFromFilledPayload } from './signal-subscriber-execution.service';

function fakeExecutor(opts: { fillAtIso: string; openAfterFirstClose?: boolean }) {
  const proto = (SignalSubscriberExecutionService as unknown as { prototype: object }).prototype;
  const self = Object.create(proto) as Record<string, unknown>;
  const runs: Array<{ tradeId: string; fill: number; deadline: number }> = [];
  self.timeDeadlineTimers = new Map();
  self.timeDeadlineStats = { scheduled: 0, fired: 0, closed: 0, noOpenLot: 0, failed: 0, recovered: 0, expired: 0 };
  self.logger = { log() {}, warn() {}, error() {} };
  self.prisma = {
    signalCycleParticipant: {
      findMany: async () => [{ id: 'p1', userId: 'u1', cycle: { tradeId: 'dsl-1-1' } }],
      findFirst: async () => null,
    },
    signalCycleEvent: {
      findFirst: async () => ({ payload: { exchange_fill_last_at: opts.fillAtIso }, createdAt: new Date(0) }),
    },
  };
  self.runTimeDeadlineExit = async (_a: string, _u: string, tradeId: string, fill: number, deadline: number) => {
    runs.push({ tradeId, fill, deadline });
  };
  return { self, runs };
}

test('1495: timer starts from the LIVE fill in the persisted FILLED event', () => {
  assert.equal(liveFillAtMsFromFilledPayload({ exchange_fill_last_at: '2026-10-12T10:00:00.500Z', fill_detected_at: '2026-10-12T10:00:02Z' }, 1), Date.parse('2026-10-12T10:00:00.500Z'));
  assert.equal(liveFillAtMsFromFilledPayload({}, 42), 42);
});

test('1495: restart re-arms from DB; a passed deadline fires immediately, once', async () => {
  process.env.RELAY_TIME_DEADLINE_EXIT = 'on';
  try {
    const fill = Date.now() - 70_000;
    const { self, runs } = fakeExecutor({ fillAtIso: new Date(fill).toISOString() });
    const recover = (proto: unknown) => (proto as { recoverTimeDeadlineExits: (a: string, u: string[]) => Promise<number> }).recoverTimeDeadlineExits;
    const fn = recover(self);
    assert.equal(await fn.call(self, 'agent', ['u1']), 1);
    assert.equal(await fn.call(self, 'agent', ['u1']), 0, 'second pass must not double-schedule');
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(runs.length, 1);
    assert.equal(runs[0].fill, fill);
    assert.equal(runs[0].deadline, fill + 60_000);
  } finally { delete process.env.RELAY_TIME_DEADLINE_EXIT; }
});

test('1495: deadline older than the 5 min authority window is not scheduled', async () => {
  process.env.RELAY_TIME_DEADLINE_EXIT = 'on';
  try {
    const { self, runs } = fakeExecutor({ fillAtIso: new Date(Date.now() - 7 * 60_000).toISOString() });
    const fn = (self as unknown as { recoverTimeDeadlineExits: (a: string, u: string[]) => Promise<number> }).recoverTimeDeadlineExits;
    assert.equal(await fn.call(self, 'agent', ['u1']), 0);
    assert.equal((self.timeDeadlineStats as { expired: number }).expired, 1);
    assert.equal(runs.length, 0);
  } finally { delete process.env.RELAY_TIME_DEADLINE_EXIT; }
});

test('1495: double close - timer firing after the lot already closed is a no-op', async () => {
  const proto = (SignalSubscriberExecutionService as unknown as { prototype: Record<string, unknown> }).prototype;
  const { self } = fakeExecutor({ fillAtIso: new Date().toISOString() });
  let closeCalls = 0;
  self.closeVirtualLot = async () => { closeCalls += 1; return true; };
  await (proto.runTimeDeadlineExit as (...a: unknown[]) => Promise<void>).call(self, 'agent', 'u1', 'dsl-1-1', 1, 60_001);
  assert.equal(closeCalls, 0);
  assert.equal((self.timeDeadlineStats as { noOpenLot: number }).noOpenLot, 1);
});

test('1495: recovery off by default', async () => {
  const { self, runs } = fakeExecutor({ fillAtIso: new Date(Date.now() - 70_000).toISOString() });
  const fn = (self as unknown as { recoverTimeDeadlineExits: (a: string, u: string[]) => Promise<number> }).recoverTimeDeadlineExits;
  assert.equal(await fn.call(self, 'agent', ['u1']), 0);
  assert.equal(runs.length, 0);
});
