import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { LiveCopyFlyReporterService, reporterRunLine, REPORTER_RUNS_MAX } from './live-copy-fly-reporter';

const make = (reports: any[], ok = true) => {
  const svc = new LiveCopyFlyReporterService({} as any, { get: (k: string) => (k === 'SHOWCASE_WEBHOOK_SECRET' ? 's'.repeat(32) : undefined) } as any);
  (svc as any).collect = async () => reports;
  (svc as any).sendCapability = async () => false;
  (globalThis as any).fetch = async () => ({ ok, status: ok ? 200 : 500 });
  const lines: string[] = [];
  (svc as any).logger = { log: (l: string) => lines.push(l), warn: () => undefined };
  return { svc, lines };
};

describe('LIVE-FIX-1502 reporter run log', () => {
  it('records and logs one row per run with sent count and delay', async () => {
    const { svc, lines } = make([{ report_id: 'r1', event_at_ts: Date.now() / 1000 - 2 }]);
    await svc.tick('kick');
    assert.equal(svc.runs.length, 1);
    const run = svc.runs[0];
    assert.equal(run.trigger, 'kick');
    assert.deepEqual([run.collected, run.unacked, run.attempted, run.sent, run.failed], [1, 1, 1, 1, 0]);
    assert.ok(run.delays_ms[0] >= 1900);
    assert.deepEqual(lines, [reporterRunLine(run)]);
  });

  it('logs failures, backoff skips and idle runs; ring is bounded', async () => {
    const { svc, lines } = make([{ report_id: 'r2', event_at_ts: 1 }], false);
    await svc.tick('interval');
    assert.deepEqual([svc.runs[0].sent, svc.runs[0].failed], [0, 1]);
    await svc.tick('interval');
    assert.deepEqual([svc.runs[1].attempted, svc.runs[1].skipped_backoff], [0, 1]);
    for (let i = 0; i < REPORTER_RUNS_MAX + 5; i++) await svc.tick('interval');
    assert.equal(svc.runs.length, REPORTER_RUNS_MAX);
    assert.equal(lines.length, REPORTER_RUNS_MAX + 7);
  });
});
